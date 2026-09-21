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
ENTITIES = str(config.work_dir("recon") / "sample_set_entities.json")
MANIFEST = str(config.work_dir("manifests") / "baseline_frozen_inputs.json")

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
        name = src.split("/")[-1]
        rows.append({"attr": attr, "src": src, "name": name,
                     "dest": f"gs://{dest_bucket()}/{DEST_PREFIX}/{name}"})
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
    json.dump({"dest_prefix": f"gs://{dest_bucket()}/{DEST_PREFIX}", "objects": out,
               "total_bytes": total}, open(MANIFEST, "w"), indent=1)
    print(f"\nmanifest -> {MANIFEST}  total {total/2**30:.2f} GiB")
    if bad:
        raise SystemExit(f"crc32c mismatch on {len(bad)} objects: {bad}")


def attrs(rows):
    """Write <attr>{FROZEN_SUFFIX} onto the batch sample_set (Terra merges attributes)."""
    lines = ["entity:sample_set_id\t" + "\t".join(f"{r['attr']}{FZ}" for r in rows)]
    lines.append(f"{BATCH}\t" + "\t".join(r["dest"] for r in rows))
    tsv = "\n".join(lines) + "\n"
    path = str(config.work_dir("staging") / "frozen_baseline_attrs.tsv")
    open(path, "w").write(tsv)
    print(tsv.replace("gs://", "\n    gs://"))
    if "--write" not in sys.argv:
        print("dry run: re-run with --write to upload")
        return
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
  verify   compare crc32c on both sides; nonzero exit on any mismatch
  attrs    write <attr>{FROZEN_SUFFIX} onto the batch sample_set (--write to upload)

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
