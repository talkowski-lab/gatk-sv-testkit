#!/usr/bin/env python3
"""Freeze the baseline run's *inputs* into your sandbox workspace's own GCS bucket.

Why copy at all: a cloned workspace gets its own Google project, and Cromwell localizes
gs:// with the *clone's* pet service account, which has no access to the baseline
workspace's bucket. Referencing the frozen inputs in place looks fine in the config and
then fails at localization time. Copying is server-side (GCS->GCS, same region) and
preserves crc32c, so `verify` can prove the bytes.

Frozen inputs = only what the 06/07 steps consume from upstream (ClusterBatch +
GatherBatchEvidence outputs + median_cov). Steps 07-10 consume each other's new outputs, so
their upstream files are produced inside the sandbox, not copied.

    python terra/batch_freeze.py plan      # what it would do (read-only)
    python terra/batch_freeze.py copy      # server-side copy (mutation)
    python terra/batch_freeze.py verify    # crc32c both sides
    python terra/batch_freeze.py attrs     # write <attr>{FROZEN_SUFFIX} into the sandbox

Anonymously readable public resource buckets (gatk-sv-resources-public,
gcp-public-data--broad-references) are referenced in place, never copied.
"""
from __future__ import annotations

import json
import subprocess
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
sys.path.insert(0, HERE)
import config  # noqa: E402
import terra  # noqa: E402

SANDBOX_NS = config.get("TERRA_NAMESPACE")
SANDBOX_WS = config.get("TERRA_WORKSPACE")


def require_sandbox():
    """Resolve the sandbox workspace, or exit naming the profile key that is missing."""
    global SANDBOX_NS, SANDBOX_WS
    SANDBOX_NS = SANDBOX_NS or config.require(
        "TERRA_NAMESPACE", "the workspace whose bucket receives the copies")
    SANDBOX_WS = SANDBOX_WS or config.require(
        "TERRA_WORKSPACE", "the workspace whose bucket receives the copies")
    return SANDBOX_NS, SANDBOX_WS
DEST_PREFIX = "frozen-baseline"
BATCH = config.get("BATCH", "all_samples")
FZ = "_" + config.get("FROZEN_SUFFIX", "frz")
# Paths, not directories: import is what `--help` runs, and it must not create anything. Writers
# call config.work_dir() themselves.
ENTITIES = str(config.work_path("recon") / "sample_set_entities.json")
MANIFEST = str(config.work_path("manifests") / "baseline_frozen_inputs.json")

# sample_set attributes the 06/07 steps read from upstream (frozen from the baseline)
FROZEN_ATTRS = [
    "clustered_depth_vcf", "clustered_depth_vcf_index",
    "clustered_manta_vcf", "clustered_manta_vcf_index",
    "clustered_scramble_vcf", "clustered_scramble_vcf_index",
    "clustered_wham_vcf", "clustered_wham_vcf_index",
    "merged_PE", "merged_PE_index",
    "merged_SR", "merged_SR_index",
    "merged_bincov", "merged_bincov_index",
    "merged_BAF", "merged_BAF_index",
    "median_cov",
]


def load_rows():
    if not os.path.exists(ENTITIES):
        raise SystemExit(
            f"no entity dump at {ENTITIES}.\n"
            f"  produce it first:  python terra/recon.py            "
            f"(read-only; dumps the baseline model)\n"
            f"  or point GSVTK_WORK at the checkout that already has it   (docs/config.md)")
    with open(ENTITIES) as fh:
        ent = json.load(fh)[BATCH]
    rows = []
    for attr in FROZEN_ATTRS:
        src = ent.get(attr)
        if not src or not src.startswith("gs://"):
            raise SystemExit(f"entity attribute {attr} is not a gs:// path: {src!r}")
        rows.append({"attr": attr, "src": src, "name": src.split("/")[-1]})

    # The frozen object name used to be the source basename, which silently collided: two baseline
    # attributes whose objects share a basename (this model nests same-named objects under different
    # batch/run directories, e.g. merged_PE and merged_SR both called merged_pe.out) mapped to ONE
    # object -- copy wrote it twice, last writer won, and `attrs --write` published the same path
    # under both names. One of the two inputs steps 06/07 read was then simply the wrong file, and
    # every head-to-head after it compared against partly wrong inputs.
    # Objects that are genuinely the same source object share one dest (no duplicate copy); objects
    # that merely share a name get attribute-qualified names.
    srcs, names = {}, {}
    for r in rows:
        srcs.setdefault(r["src"], set()).add(r["name"])
        names.setdefault(r["name"], set()).add(r["src"])
    bucket = dest_bucket()
    for r in rows:
        object_name = r["name"] if len(names[r["name"]]) == 1 else f"{r['attr']}__{r['name']}"
        r["dest"] = f"gs://{bucket}/{DEST_PREFIX}/{object_name}"
    by_dest = {}
    for r in rows:
        by_dest.setdefault(r["dest"], set()).add(r["src"])
    clashes = sorted(d for d, srcs_here in by_dest.items() if len(srcs_here) > 1)
    if clashes:
        raise SystemExit(f"two different source objects still map to one frozen path: {clashes}")
    collide = sorted(n for n, s in names.items() if len(s) > 1)
    if collide:
        print(f"   note: {len(collide)} basename(s) are shared by different source objects "
              f"({', '.join(collide[:4])}); frozen under attribute-qualified names instead of "
              f"sharing one object")
    return rows


def dest_bucket():
    w = terra.workspace(SANDBOX_NS, SANDBOX_WS)
    return w["workspace"]["bucketName"]


def gsutil(args, timeout=7200):
    return subprocess.run(["gsutil", "-q"] + args, capture_output=True, text=True, timeout=timeout)


# `gsutil stat` labels are not the metadata key names: crc32c prints as "Hash (crc32c):".
# A naive `line.startswith("crc32c:")` parse returns None for objects that exist, which silently
# defeats the idempotence check in copy() (observed: it re-copied 21 GiB).
STAT_KEYS = {"crc32c": "Hash (crc32c)", "bytes": "Content-Length", "etag": "ETag"}


def stat_field(url, field="crc32c"):
    key = STAT_KEYS[field]
    r = subprocess.run(["gsutil", "stat", url], capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return None
    for line in r.stdout.splitlines():
        if line.strip().startswith(key + ":"):
            return line.split(":", 1)[1].strip()
    return None


def plan(rows):
    for r in rows:
        print(f"{r['attr']:26s} {r['src']}")
        print(f"{'':26s} -> {r['dest']}")
    print(f"\n{len(rows)} objects")


def copy(rows):
    """Server-side GCS->GCS copy of the frozen inputs into the sandbox bucket.

    Gated like every other mutator here, because it is the one that writes tens of GiB: the
    objects are new (so nothing is overwritten, and a re-run only pays for what is missing) but
    they cost storage from the moment they exist, and `attrs --write` later points the batch at
    them.
    """
    total = sum(int(stat_field(r["src"], "bytes") or 0) for r in rows)
    print(f"{len(rows)} object(s), {total / 2**30:.2f} GiB "
          f"-> gs://{dest_bucket()}/{DEST_PREFIX}")
    if "--write" not in sys.argv:
        print("dry run: nothing was copied. Re-run with --write to copy into the bucket above.")
        return
    terra.assert_writable_target(SANDBOX_NS, SANDBOX_WS, "copy the frozen baseline inputs",
                                allow="--allow-shared-target" in sys.argv)
    todo = []
    for r in rows:
        if stat_field(r["dest"]) is not None:
            print(f"present, skipping: {r['dest']}")
            continue
        todo.append(r)
    if not todo:
        print("nothing to copy")
        return
    # one gsutil invocation per object: -m over many cp's is the simple parallel form
    procs = []
    for r in todo:
        procs.append(subprocess.Popen(["gsutil", "-q", "cp", r["src"], r["dest"]],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True))
        print(f"copied {r['attr']}")
    fails = 0
    for r, p in zip(todo, procs):
        out, err = p.communicate()
        if p.returncode != 0:
            fails += 1
            print(f"FAIL {r['attr']}: {err[-300:]}")
    print(f"copied {len(todo) - fails}/{len(todo)}")
    if fails:
        raise SystemExit(1)


def verify(rows):
    bad = []
    total = 0
    out = []
    for r in rows:
        a = stat_field(r["src"])
        b = stat_field(r["dest"])
        size = stat_field(r["dest"], "bytes")
        total += int(size) if size else 0
        out.append({**r, "crc32c_src": a, "crc32c_dest": b, "bytes": int(size or 0)})
        if a is None or a != b:
            bad.append((r["attr"], a, b))
        print(f"{'OK  ' if a == b else 'DIFF'} {r['attr']:26s} crc32c={b} bytes={size}")
    # The manifest is what later steps (and later people) trust, so it must record the VERDICT, not
    # just the object list: a manifest written by a FAILED verify used to be indistinguishable from
    # a successful one.
    doc = {"dest_prefix": f"gs://{dest_bucket()}/{DEST_PREFIX}", "objects": out,
           "total_bytes": total, "verified": not bad,
           "mismatches": [{"attr": a, "crc32c_src": x, "crc32c_dest": y} for a, x, y in bad]}
    config.work_dir("manifests")          # the path is built without mkdir; this write makes the dir
    json.dump(doc, open(MANIFEST, "w"), indent=1)
    print(f"\nmanifest -> {MANIFEST}  total {total/2**30:.2f} GiB  verified={not bad}")
    if bad:
        raise SystemExit(f"crc32c mismatch on {len(bad)} objects: {bad}")


def publish_guard(manifest_path: str, allow_unverified: bool = False) -> None:
    """Refuse to publish frozen attributes unless a crc32c verify PASSED is on record.

    Three states must all be refused, not just the loud one. This used to test only
    `verified is False`, so BOTH other ways of having no proof sailed through: no manifest at all
    (verify never run), and a manifest written before the verdict key existed or by a hand-edited
    file. `attrs --write` then published gs:// paths into the entity as the frozen inputs of every
    later comparison, on the strength of nothing -- which is the same silent-wrongness shape as
    counting a step with no metadata as zero VM-minutes.
    """
    if allow_unverified:
        print("   !! --allow-unverified: publishing frozen paths with no recorded crc32c verify")
        return
    try:
        with open(manifest_path) as fh:
            man = json.load(fh)
    except FileNotFoundError:
        raise SystemExit(
            f"no frozen-input manifest at {manifest_path}, so nothing has verified these objects.\n"
            f"  Run `python terra/batch_freeze.py verify` (read-only; it re-crc32cs both sides and\n"
            f"  writes the verdict) first, or pass --allow-unverified if you proved the copies\n"
            f"  some other way and can say how.")
    except (OSError, ValueError) as e:
        raise SystemExit(f"cannot read {manifest_path} ({e}); refusing to publish frozen paths "
                         f"without a readable verify verdict. Re-run `verify`.")
    if man.get("verified") is not True:
        n = len(man.get("mismatches") or [])
        raise SystemExit(
            f"{manifest_path} records no PASSED crc32c verify"
            + (f" ({n} mismatching object(s))" if n else " (no 'verified' verdict in it)")
            + ".\n  Re-run `python terra/batch_freeze.py verify` and resolve it before publishing "
              "attributes --\n  publishing an unverified frozen path means the head-to-head runs on "
              "inputs nobody confirmed.\n  (--allow-unverified only if you have proof elsewhere.)")


def attrs(rows):
    """Write <attr>{FROZEN_SUFFIX} onto the batch sample_set (Terra merges attributes)."""
    lines = ["entity:sample_set_id\t" + "\t".join(f"{r['attr']}{FZ}" for r in rows)]
    lines.append(f"{BATCH}\t" + "\t".join(r["dest"] for r in rows))
    publish_guard(MANIFEST, allow_unverified="--allow-unverified" in sys.argv)
    tsv = "\n".join(lines) + "\n"
    path = str(config.work_dir("staging") / "frozen_baseline_attrs.tsv")
    open(path, "w").write(tsv)
    print(tsv.replace("gs://", "\n    gs://"))
    if "--write" not in sys.argv:
        print("dry run: re-run with --write to upload")
        return
    terra.assert_writable_target(SANDBOX_NS, SANDBOX_WS, "write frozen-input attributes",
                                allow="--allow-shared-target" in sys.argv)
    # fapi.upload_entities_tsv() opens the argument as a FILENAME (it is not content) - passing the
    # TSV body raises OSError "File name too long".
    r = terra.upload_entities_tsv(SANDBOX_NS, SANDBOX_WS, path, confirm=True)
    print("upload:", json.dumps(r)[:400])


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
    rows = load_rows()
    {"plan": plan, "copy": copy, "verify": verify, "attrs": attrs}[mode](rows)


def usage(code=0):
    print("""usage: batch_freeze.py [plan|copy|verify|attrs] [--write]

  plan     list what would be copied (read-only: Terra + gsutil stat only)
  copy     server-side GCS->GCS copy of the frozen inputs into the sandbox bucket
           (--write: it creates tens of GiB of billable objects that nothing overwrites)
  verify   compare crc32c on both sides; nonzero exit on any mismatch
  attrs    write <attr>{FROZEN_SUFFIX} onto the batch sample_set (--write to upload)
           Refuses unless `verify` has recorded a PASSED crc32c verdict in the manifest: no
           manifest, a corrupt one and a failed one are all refused. --allow-unverified overrides
           it, and prints that it did.

Both mutators refuse GSVTK_BASELINE_NAMESPACE/_WORKSPACE unless --allow-shared-target: that
workspace is the shared reference run, and Terra merges entity attributes rather than replacing.

Workspace, batch and the frozen suffix come from the profile; see docs/config.md.""",
          file=sys.stderr if code else sys.stdout)
    raise SystemExit(code)


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        usage()
    mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if mode not in ("plan", "copy", "verify", "attrs"):
        usage(2)
    require_sandbox()
    main()
