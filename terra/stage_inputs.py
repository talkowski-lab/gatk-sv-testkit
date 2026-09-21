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
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import terra  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MAN = str(config.work_dir("manifests") / "baseline_run.json")
STAGE = str(config.work_dir("staging"))


def p(*a):
    print(*a, flush=True)


def sh(cmd, **kw):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd}\n{r.stderr[:600]}")
    return r.stdout.strip()


def remote_size(uri: str) -> int:
    out = sh(f"gsutil -q ls -l {json.dumps(uri)}")
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
    row = ents.get("all_samples", {})
    for a in attrs:
        v = row.get(a)
        if isinstance(v, str) and v.startswith("gs://"):
            want.setdefault(a, v)
        elif v is not None:
            p(f"   note: attribute {a} is not a single gs:// object: {str(v)[:60]}")
    return want


def adopt_from_local(uri, link_dirs, dest):
    """Adopt an already-localized copy (same basename and size) by hardlink/copy."""
    base = os.path.basename(uri)
    try:
        size = remote_size(uri)
    except Exception:
        return None
    for d in link_dirs:
        for cand in glob.glob(os.path.join(os.path.expanduser(d), "**", base), recursive=True):
            if os.path.getsize(cand) == size:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                if not os.path.exists(dest):
                    try:
                        os.link(cand, dest)
                    except OSError:
                        subprocess.run(["gsutil", "-m", "cp", "-n", cand, dest], check=True)
                return {"source": "local", "path": cand, "size": size}
    return None


def stage_one(name, uri, region, link_dirs):
    os.makedirs(STAGE, exist_ok=True)
    base = os.path.basename(uri)
    dest = os.path.join(STAGE, base if not region else f"{region}__{name}__{base}")
    size = remote_size(uri)
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        return {"status": "cached", "local": dest, "size": size}
    if link_dirs:
        got = adopt_from_local(uri, link_dirs, os.path.join(STAGE, base))
        if got:
            log = {"status": "adopted", "local": os.path.join(STAGE, base),
                   "adopted_from": got["path"], "size": size}
            if region:
                idx = os.path.join(STAGE, base) + ".tbi"
                if not os.path.exists(idx):
                    sh(f"gsutil -m cp -n {json.dumps(uri + '.tbi')} {json.dumps(idx)} || true")
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
            sh(f"gsutil -m cp -n {json.dumps(uri + '.tbi')} {json.dumps(idx)} || true")
        tabix_slice(full, region, dest)
        return {"status": "sliced", "local": dest, "region": region, "full": full}
    sh(f"gsutil -m cp -n {json.dumps(uri)} {json.dumps(dest)}")
    if os.path.getsize(dest) != size:
        raise RuntimeError(f"size mismatch for {name}: local {os.path.getsize(dest)} != remote {size}")
    if base.endswith(".gz"):
        for ext in (".tbi", ".idx"):
            try:
                sh(f"gsutil -m cp -n {json.dumps(uri + ext)} {json.dumps(dest + ext)}")
            except Exception:
                pass
    return {"status": "downloaded", "local": dest, "size": size}


def tabix_slice(full, region, dest):
    """Region-slice a bgzf object, keeping the header. bed-style coords for matrices,
    tabix auto-detects for VCFs."""
    sh(f"tabix -H {json.dumps(full)} {region} | bgzip -@4 > {json.dumps(dest)}")
    sh(f"tabix -f -p bed {json.dumps(dest)} 2>/dev/null || bcftools index -f {json.dumps(dest)} 2>/dev/null || true")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keys", nargs="*", default=[], help="WDL input names, e.g. rd_file pe_file")
    ap.add_argument("--attrs", nargs="*", default=[], help="sample_set attribute names")
    ap.add_argument("--region", default=None, help="e.g. chr20; tabix-slice bgzf objects")
    ap.add_argument("--link-dir", action="append", default=[],
                    help="dir to adopt already-localized files from (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

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
    out = os.path.join(STAGE, "staged.json")
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
