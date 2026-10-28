#!/usr/bin/env python3
"""Freeze a baseline run's *inputs* into your sandbox workspace's own GCS bucket.

A cloned workspace localizes gs:// with its own pet service account, which cannot read the
baseline workspace's bucket, so the inputs must be copied. The copy is server-side and
preserves crc32c, so `verify` can check the bytes.

What is frozen comes from the module profile's `freeze` block (`profiles/<module>.json`, chosen by
--module or GSVTK_MODULE): which attributes of which baseline entity row, and which sandbox row they
are written onto.

  * genotyping (cohort chain): what the 06/07 steps consume from upstream (ClusterBatch +
    GatherBatchEvidence outputs + median_cov), written as `<attr>{FROZEN_SUFFIX}` onto the batch
    sample_set. Steps 07-10 consume each other's new outputs, so those are produced in the sandbox.
  * single_sample: the sample row's read file (plus its derived `.crai`/`.bai`) and stripy file,
    written under their own names onto a `sample` row the TSV creates (--entity names the row).
  * --objects FILE: a list of gs:// objects with no entity row, e.g. one sv-shell run's output VCF
    and inputs. Copied, verified and recorded in a manifest; `attrs` refuses.

    python terra/batch_freeze.py plan      # what it would do (read-only)
    python terra/batch_freeze.py copy      # server-side copy (mutation, --write)
    python terra/batch_freeze.py verify    # crc32c and size both sides; writes the manifest
    python terra/batch_freeze.py attrs     # write the entity TSV into the sandbox (--write)

Anonymously readable public resource buckets (gatk-sv-resources-public,
gcp-public-data--broad-references) are referenced in place, never copied.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

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
# Paths only; importing must not create directories. Writers call config.work_dir().
ENTITIES = str(config.work_path("recon") / "sample_set_entities.json")
MANIFEST = str(config.work_path("manifests") / "baseline_frozen_inputs.json")
# recon.py's own dump: {etype: {"sample": [{"name", "attributes"}]}}; read when no per-type dump exists.
RECON_DUMP = str(config.work_path("recon") / "baseline_entities.json")
MODES = ("plan", "copy", "verify", "attrs")
# The index GatherSampleEvidence derives from a read file's path when none is bound.
SIDECARS = {".cram": ".crai", ".bam": ".bai"}

ARGS = argparse.Namespace(write=False, allow_shared_target=False, allow_unverified=False)


# ------------------------------------------------------------------ what is frozen

def entities_path(etype: str) -> str:
    return str(config.work_path("recon") / f"{etype}_entities.json")


def load_spec(args) -> dict:
    """The freeze spec: the module profile's `freeze` block, or an --objects file. Exits 2/4."""
    if args.objects:
        if args.module:
            raise SystemExit(2, "--objects and --module are exclusive: an --objects freeze has no "
                                "profile and no entity row")
        return load_objects_spec(args.objects, args.name)
    if args.name:
        raise SystemExit(2, "--name only applies to --objects")
    import module_profile
    r = module_profile.load(args.module)
    r.require("batch_freeze")
    if r.freeze is None:
        print(f"batch_freeze: module profile {r.path} has no `freeze` block, so nothing says which\n"
              f"  baseline attributes this module freezes or which entity row they go on.\n"
              f"  Add one (docs/module-profiles.md; profiles/genotyping.json is the cohort example):\n"
              f"{module_profile.FIELD_HELP.split('optional top', 1)[-1].split('anywhere', 1)[0]}",
              file=sys.stderr)
        raise SystemExit(4)
    spec = dict(r.freeze)
    spec["module"] = os.path.splitext(os.path.basename(str(r.path)))[0]
    spec["image_pins"] = {step: {k: p.ref for k, p in pins.items()}
                          for step, pins in r.image_pins.items()}
    spec["objects_file"] = ""
    return spec


def load_objects_spec(path: str, name: str) -> dict:
    """An --objects file: {"name", "source_run": {...}, "objects": {label: gs://...}}."""
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError) as e:
        raise SystemExit(2, f"--objects {path}: cannot read it as JSON ({e})")
    objs = doc.get("objects") if isinstance(doc, dict) else None
    if not isinstance(objs, dict) or not objs:
        raise SystemExit(2, f"--objects {path}: no non-empty \"objects\" map of label -> gs:// path; "
                            f"refusing to plan an empty freeze")
    name = name or str(doc.get("name") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise SystemExit(2, f"--objects {path}: name {name!r} must be letters, digits, `_` or `-` "
                            f"(it names the manifest and the bucket prefix); give \"name\" in the "
                            f"file or --name")
    src = doc.get("source_run") or {}
    if not isinstance(src, dict):
        raise SystemExit(2, f"--objects {path}: \"source_run\" is a {type(src).__name__}, not an object")
    return {"module": name, "tag": name, "entity_type": "", "row_source": "", "row_mode": "none",
            "attrs": list(objs), "optional_attrs": [], "suffix": "", "objects": dict(objs),
            "source_run": src, "image_pins": {}, "objects_file": path}


def row_name(spec, args) -> str:
    if spec["row_mode"] == "none":
        return ""
    if spec["row_source"]:
        if args.entity:
            raise SystemExit(2, f"--entity: this module's freeze row comes from "
                                f"GSVTK_{spec['row_source']}, not the command line")
        return config.get(spec["row_source"], "all_samples" if spec["row_source"] == "BATCH" else "")
    if not args.entity:
        raise SystemExit(2, f"module {spec['module']} names its {spec['entity_type']} row with "
                            f"--entity ROW (the baseline row to freeze); none was given")
    return args.entity


def baseline_row(spec, row: str) -> dict:
    """The baseline row's attributes: the per-type dump, else recon.py's sampled dump."""
    etype = spec["entity_type"]
    per_type = ENTITIES if etype == "sample_set" else entities_path(etype)
    if os.path.exists(per_type):
        with open(per_type) as fh:
            ent = json.load(fh)
        if row in ent:
            return ent[row]
    if os.path.exists(RECON_DUMP):
        with open(RECON_DUMP) as fh:
            dump = json.load(fh)
        for r in (dump.get(etype) or {}).get("sample") or []:
            if r.get("name") == row:
                return r.get("attributes") or {}
    raise SystemExit(
        f"no baseline {etype} row {row!r} in {per_type} or {RECON_DUMP}.\n"
        f"  produce it first:  python terra/recon.py --entities {etype}   "
        f"(read-only; dumps the baseline model)\n"
        f"  or point GSVTK_WORK at the checkout that already has it   (docs/config.md)")


def load_rows(spec, row: str):
    if spec["row_mode"] == "none":
        ent = spec["objects"]
    else:
        ent = baseline_row(spec, row)
    rows = []
    for attr in spec["attrs"]:
        src = ent.get(attr)
        if not isinstance(src, str) or not src.startswith("gs://"):
            raise SystemExit(f"entity attribute {attr} is not a gs:// path: {src!r}")
        rows.append({"attr": attr, "src": src, "name": src.split("/")[-1]})
    skipped = []
    for attr in spec["optional_attrs"]:
        src = ent.get(attr)
        if isinstance(src, str) and src.startswith("gs://"):
            rows.append({"attr": attr, "src": src, "name": src.split("/")[-1]})
        else:
            skipped.append(attr)
    if skipped:
        print(f"   note: optional attribute(s) not on the baseline row, not frozen: {', '.join(skipped)}")

    # Different source objects can share a basename, so those get attribute-qualified dest names.
    # The same source object referenced twice shares one dest.
    srcs, names = {}, {}
    for r in rows:
        srcs.setdefault(r["src"], set()).add(r["name"])
        names.setdefault(r["name"], set()).add(r["src"])
    prefix = dest_prefix(spec, row)
    for r in rows:
        object_name = r["name"] if len(names[r["name"]]) == 1 else f"{r['attr']}__{r['name']}"
        r["dest"] = f"{prefix}/{object_name}"
    # A read file's derived index travels with it, under the same derived name.
    for r in list(rows):
        for ext, idx in SIDECARS.items():
            if r["src"].endswith(ext):
                rows.append({"attr": r["attr"] + idx, "src": r["src"] + idx, "name": r["name"] + idx,
                             "dest": r["dest"] + idx, "sidecar_of": r["attr"]})
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


def dest_prefix(spec, row: str) -> str:
    """gs://<sandbox bucket>/frozen-baseline[/<tag>[/<row>]]; the empty tag keeps the cohort path."""
    base = f"gs://{dest_bucket()}/{DEST_PREFIX}"
    if not spec["tag"]:
        return base
    return f"{base}/{spec['tag']}" + (f"/{row}" if row else "")


def manifest_path(spec) -> str:
    if not spec["tag"]:
        return MANIFEST
    return str(config.work_path("manifests") / f"baseline_frozen_inputs.{spec['tag']}.json")


def staging_names(spec):
    """(row TSV, participant TSV) file names under staging/."""
    if not spec["tag"]:
        return "frozen_baseline_attrs.tsv", "frozen_baseline_participant.tsv"
    return (f"frozen_baseline_attrs.{spec['tag']}.tsv",
            f"frozen_baseline_participant.{spec['tag']}.tsv")


def gsutil(args, timeout=7200):
    return subprocess.run(["gsutil", "-q"] + args, capture_output=True, text=True, timeout=timeout)


# `gsutil stat` labels differ from the metadata key names, e.g. crc32c prints as "Hash (crc32c):".
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


# ------------------------------------------------------------------ the entity TSV

def participant_of(base: dict) -> str:
    """The participant the baseline row references, or "" when it references none."""
    p = base.get("participant")
    if isinstance(p, dict) and p.get("entityType") == "participant":
        return str(p.get("entityName") or "")
    return ""


def entity_tsvs(spec, row: str, rows, base: dict):
    """[(staging file name, TSV text)] in upload order: a participant row first when one is referenced.

    Terra refuses a `sample` row whose `participant` column names a participant that does not exist,
    so the participant is written first. A baseline row with no participant gets none.
    """
    main_rows = [r for r in rows if "sidecar_of" not in r]
    cols = [f"{r['attr']}{spec['suffix']}" for r in main_rows]
    vals = [r["dest"] for r in main_rows]
    out = []
    attrs_name, part_name = staging_names(spec)
    part = participant_of(base) if spec["entity_type"] == "sample" else ""
    if part:
        out.append((part_name, f"entity:participant_id\n{part}\n"))
        cols = ["participant"] + cols
        vals = [part] + vals
    lines = [f"entity:{spec['entity_type']}_id\t" + "\t".join(cols), f"{row}\t" + "\t".join(vals)]
    out.append((attrs_name, "\n".join(lines) + "\n"))
    return out


def show_tsvs(tsvs):
    for name, text in tsvs:
        print(text.replace("gs://", "\n    gs://"))


# ------------------------------------------------------------------ modes

def refuse_read_only(mode: str) -> None:
    """GSVTK_READ_ONLY refuses every write before any credential or bucket is consulted."""
    ro = os.environ.get("GSVTK_READ_ONLY", "")
    if ro and ro != "0":
        print(f"batch_freeze: REFUSED -- `{mode} --write` writes into your sandbox "
              f"({'GCS objects' if mode == 'copy' else 'Terra entities'}) and GSVTK_READ_ONLY is set.\n"
              f"  Nothing was copied or uploaded. Unset GSVTK_READ_ONLY to write; `plan`, `verify` and\n"
              f"  `{mode}` without --write stay available.", file=sys.stderr)
        raise SystemExit(2)


def announce(spec, what: str, where: str, undo: str) -> None:
    """What/where/undo before a write, for the flavors gsvtk's own pre-flight does not describe."""
    if spec["tag"]:
        print(f"   will: {what}\n   where: {where}\n   undo: {undo}")


def plan(spec, row, rows, base):
    for r in rows:
        print(f"{r['attr']:26s} {r['src']}")
        print(f"{'':26s} -> {r['dest']}")
    print(f"\n{len(rows)} objects")
    if spec["row_mode"] == "create":
        tsvs = entity_tsvs(spec, row, rows, base)
        print(f"\n`attrs --write` uploads this to {SANDBOX_NS}/{SANDBOX_WS} "
              f"(creates {spec['entity_type']} {row!r}, or merges onto it if it exists):")
        show_tsvs(tsvs)
    if spec["tag"]:
        print(f"manifest (written by verify) -> {manifest_path(spec)}")


def copy(spec, row, rows, base):
    """Server-side GCS->GCS copy of the frozen inputs into the sandbox bucket (needs --write).

    Objects already present are skipped.
    """
    total = sum(int(stat_field(r["src"], "bytes") or 0) for r in rows)
    prefix = dest_prefix(spec, row)
    print(f"{len(rows)} object(s), {total / 2**30:.2f} GiB "
          f"-> {prefix}")
    if not ARGS.write:
        print("dry run: nothing was copied. Re-run with --write to copy into the bucket above.")
        return
    refuse_read_only("copy")
    terra.assert_writable_target(SANDBOX_NS, SANDBOX_WS, "copy the frozen baseline inputs",
                                allow=ARGS.allow_shared_target)
    announce(spec, f"server-side copy of {len(rows)} object(s), {total / 2**30:.2f} GiB (billable "
                   f"storage nothing overwrites)", prefix + "/",
             f"gsutil -m rm -r {prefix}/")
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
        print(f"copying {r['attr']}")
    fails = 0
    for r, p in zip(todo, procs):
        out, err = p.communicate()
        if p.returncode != 0:
            fails += 1
            print(f"FAIL {r['attr']}: {err[-300:]}")
    print(f"copied {len(todo) - fails}/{len(todo)}")
    if fails:
        raise SystemExit(1)


def images_from_metadata(meta: dict) -> dict:
    """{call: image} from Cromwell metadata, preferring the digest Cromwell resolved (dockerImageUsed)."""
    out = {}

    def walk(m, path):
        for call, attempts in (m.get("calls") or {}).items():
            for a in attempts or []:
                img = a.get("dockerImageUsed") or (a.get("runtimeAttributes") or {}).get("docker")
                if img:
                    out[f"{path}{call}"] = img
                sub = a.get("subWorkflowMetadata")
                if isinstance(sub, dict):
                    walk(sub, f"{path}{call}/")
    walk(meta, "")
    return out


def source_run(spec, args) -> dict:
    """The baseline run the frozen objects came from: --objects file < --metadata < flags.

    Fields nobody stated are null and named in `unknown`, so the manifest says what it does not know.
    """
    run = {"submission_id": None, "workflow_id": None, "method": None, "method_version": None,
           "images": {}, "images_from": None}
    extra = {}
    for k, v in (spec.get("source_run") or {}).items():
        if k in run:
            run[k] = v
        else:
            extra[k] = v
    if spec.get("source_run", {}).get("images"):
        run["images_from"] = f"--objects {spec['objects_file']}"
    if args.metadata:
        try:
            with open(args.metadata) as fh:
                meta = json.load(fh)
        except (OSError, ValueError) as e:
            raise SystemExit(2, f"--metadata {args.metadata}: cannot read it as JSON ({e})")
        run["workflow_id"] = meta.get("id") or run["workflow_id"]
        run["method"] = meta.get("workflowName") or run["method"]
        labels = meta.get("labels") or {}
        m = re.search(r"/submissions/([0-9a-f-]{36})/", str(meta.get("workflowRoot") or ""))
        run["submission_id"] = (labels.get("submission-id") or (m.group(1) if m else None)
                                or run["submission_id"])
        imgs = images_from_metadata(meta)
        if imgs:
            run["images"], run["images_from"] = imgs, f"--metadata {args.metadata}"
    for flag, key in (("submission", "submission_id"), ("workflow", "workflow_id"),
                      ("method", "method"), ("method_version", "method_version")):
        if getattr(args, flag):
            run[key] = getattr(args, flag)
    if spec.get("image_pins"):
        run["profile_image_pins"] = spec["image_pins"]
    run.update(extra)
    run["unknown"] = [k for k in ("submission_id", "workflow_id", "method", "method_version",
                                  "images") if not run.get(k)]
    return run


def verify(spec, row, rows, base):
    bad = []
    total = 0
    out = []
    for r in rows:
        a = stat_field(r["src"])
        b = stat_field(r["dest"])
        size = stat_field(r["dest"], "bytes")
        size_src = stat_field(r["src"], "bytes")
        total += int(size) if size else 0
        out.append({**r, "crc32c_src": a, "crc32c_dest": b, "bytes": int(size or 0)})
        ok = a is not None and a == b and size_src == size
        if not ok:
            bad.append((r["attr"], a, b, size_src, size))
        print(f"{'OK  ' if ok else 'DIFF'} {r['attr']:26s} crc32c={b} bytes={size}")
    run = source_run(spec, ARGS)
    # Record the verdict in the manifest; publish_guard() checks it.
    doc = {"dest_prefix": dest_prefix(spec, row), "objects": out,
           "total_bytes": total, "verified": not bad,
           "mismatches": [{"attr": a, "crc32c_src": x, "crc32c_dest": y, "bytes_src": s, "bytes_dest": d}
                          for a, x, y, s, d in bad],
           "module": spec["module"],
           "entity": ({"type": spec["entity_type"], "row": row,
                       "attr_suffix": spec["suffix"], "row_mode": spec["row_mode"]}
                      if spec["row_mode"] != "none" else None),
           "source_run": run}
    path = manifest_path(spec)
    config.work_dir("manifests")          # the path is built without mkdir; this write makes the dir
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=1)
    if spec["tag"] or len(run["unknown"]) < 5:
        print(f"source run: submission={run['submission_id']} workflow={run['workflow_id']} "
              f"method={run['method']}@{run['method_version']} images={len(run['images'] or {})}"
              + (f"  (unknown: {', '.join(run['unknown'])})" if run["unknown"] else ""))
    print(f"\nmanifest -> {path}  total {total/2**30:.2f} GiB  verified={not bad}")
    if bad:
        raise SystemExit(f"crc32c/size mismatch on {len(bad)} objects: {bad}")


def publish_guard(manifest_path: str, allow_unverified: bool = False, expect: dict | None = None) -> None:
    """Refuse to publish frozen attributes unless a crc32c verify PASSED is on record.

    A missing manifest, an unreadable one, and one without `verified: true` are all refused. With
    `expect`, the manifest's module and entity row must match too, so a verdict for one sample is
    not taken as a verdict for another.
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
    if expect:
        got = {"module": man.get("module"), "row": (man.get("entity") or {}).get("row")}
        if got != expect:
            raise SystemExit(
                f"{manifest_path} verified {got}, not {expect}. Re-run `verify` for this row before "
                f"publishing it.")


def attrs(spec, row, rows, base):
    """Write the frozen paths onto the sandbox entity row (Terra merges attributes)."""
    if spec["row_mode"] == "none":
        raise SystemExit(2, f"attrs: --objects {spec['objects_file']} freezes objects only; there is "
                            f"no entity row to write. The manifest from `verify` is the baseline record.")
    tsvs = entity_tsvs(spec, row, rows, base)
    publish_guard(manifest_path(spec), allow_unverified=ARGS.allow_unverified,
                  expect={"module": spec["module"], "row": row} if spec["tag"] else None)
    staging = config.work_dir("staging")
    paths = []
    for name, text in tsvs:
        path = str(staging / name)
        with open(path, "w") as fh:
            fh.write(text)
        paths.append(path)
    show_tsvs(tsvs)
    if not ARGS.write:
        print("dry run: re-run with --write to upload")
        return
    refuse_read_only("attrs")
    terra.assert_writable_target(SANDBOX_NS, SANDBOX_WS, "write frozen-input attributes",
                                allow=ARGS.allow_shared_target)
    announce(spec, f"upload {len(paths)} entity TSV(s): {spec['entity_type']} {row!r}"
                   + (" and its participant" if len(paths) > 1 else ""),
             f"Terra {SANDBOX_NS}/{SANDBOX_WS}",
             f"Terra merges attributes and keeps no history: delete the {spec['entity_type']} row in "
             f"the workspace Data tab, or re-upload the prior values")
    # upload_entities_tsv() takes a filename, not the TSV content.
    for path in paths:
        r = terra.upload_entities_tsv(SANDBOX_NS, SANDBOX_WS, path, confirm=True)
        print("upload:", json.dumps(r)[:400])


def parse(argv):
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("mode", nargs="?", default="plan")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--allow-shared-target", action="store_true")
    ap.add_argument("--allow-unverified", action="store_true")
    ap.add_argument("--module", default="")
    ap.add_argument("--entity", default="")
    ap.add_argument("--objects", default="")
    ap.add_argument("--name", default="")
    ap.add_argument("--metadata", default="")
    ap.add_argument("--submission", default="")
    ap.add_argument("--workflow", default="")
    ap.add_argument("--method", default="")
    ap.add_argument("--method-version", default="")
    args, unknown = ap.parse_known_args(argv)
    if unknown:
        print(f"batch_freeze: unknown argument(s): {' '.join(unknown)}", file=sys.stderr)
        usage(2)
    return args


def main():
    global ARGS
    ARGS = parse(sys.argv[1:])
    spec = load_spec(ARGS)
    row = row_name(spec, ARGS)
    rows = load_rows(spec, row)
    base = {} if spec["row_mode"] == "none" else baseline_row(spec, row)
    if ARGS.mode == "verify" and spec["tag"]:
        run = source_run(spec, ARGS)
        if not any(run.get(k) for k in ("submission_id", "workflow_id", "run_id")):
            raise SystemExit(2, f"verify: module {spec['module']} records a baseline, and nothing names "
                                f"the run it came from. Pass --metadata <Cromwell metadata JSON> "
                                f"(terra/batch_save_metadata.py writes one) or --submission/--workflow"
                                + (", or put \"source_run\" in the --objects file"
                                   if spec["objects_file"] else "") + ".")
    {"plan": plan, "copy": copy, "verify": verify, "attrs": attrs}[ARGS.mode](spec, row, rows, base)


def usage(code=0):
    print("""usage: batch_freeze.py [plan|copy|verify|attrs] [--write] [--module M] [--entity ROW]
                       [--objects FILE [--name NAME]] [--metadata FILE] [--submission ID]
                       [--workflow ID] [--method NAME] [--method-version V]

  plan     list what would be copied (read-only: Terra + gsutil stat only); for a module whose
           freeze creates its row (single_sample), also the entity TSV `attrs` would upload
  copy     server-side GCS->GCS copy of the frozen inputs into the sandbox bucket
           (--write: it creates tens of GiB of billable objects that nothing overwrites)
  verify   compare crc32c and size on both sides; nonzero exit on any mismatch. Writes the
           manifest: every object (path, size, crc32c) and the source run
  attrs    write the frozen paths onto the sandbox entity row (--write to upload)
           Refuses unless `verify` has recorded a PASSED crc32c verdict in the manifest: no
           manifest, a corrupt one and a failed one are all refused. --allow-unverified overrides
           it, and prints that it did.

What is frozen comes from the module profile's `freeze` block (--module, else GSVTK_MODULE):
  genotyping     17 batch sample_set attributes -> <attr>{FROZEN_SUFFIX} on GSVTK_BATCH
  single_sample  the sample's read file (+ .crai/.bai) and stripy file -> a `sample` row named by
                 --entity ROW, under the names the step reads; plus its participant if it has one
--objects FILE   {"name", "source_run": {...}, "objects": {label: gs://...}}: objects only, no
                 entity row (e.g. one sv-shell run's output VCF and inputs); attrs refuses

Source run (recorded by verify; required for every module but genotyping): --metadata reads a
Cromwell metadata JSON (workflow id, submission id, workflow name, the images each call used);
the flags override it.

Both mutators refuse GSVTK_BASELINE_NAMESPACE/_WORKSPACE unless --allow-shared-target: that
workspace is the shared reference run, and Terra merges entity attributes rather than replacing.
Both refuse under GSVTK_READ_ONLY=1.

Workspace, batch and the frozen suffix come from the profile; see docs/config.md.""",
          file=sys.stderr if code else sys.stdout)
    raise SystemExit(code)


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        usage()
    mode = sys.argv[1] if len(sys.argv) > 1 else "plan"
    if mode not in MODES:
        usage(2)
    require_sandbox()
    try:
        main()
    except SystemExit as e:
        # SystemExit(code, message): print the message, exit with the code.
        if len(e.args) == 2 and isinstance(e.args[0], int):
            print(f"batch_freeze: {e.args[1]}", file=sys.stderr)
            raise SystemExit(e.args[0])
        raise
