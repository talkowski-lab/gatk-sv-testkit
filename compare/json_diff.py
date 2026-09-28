#!/usr/bin/env python3
"""Nested JSON diff — starting with the one pair that proves the rest: inputs.json vs inputs.json.

Why this exists
---------------
Every claim in this repo about a fair comparison rests on the inputs having been frozen
(`terra/batch_freeze.py`, `frozen_baseline_attrs.tsv`, the recorded etags). All of that is
bookkeeping *about* the inputs; the thing nobody could do was open the two `inputs.json` files that
each workflow run emits — `inputs` and `outputs` are declared outputs of essentially every WDL — and
say "these two runs were given the same inputs, here is the difference or here is that there is
none". Same for `calling_config.json` / `denoising_config.json` (gCNV behaviour is a function of
them) and the vapor/IRS `*.json.gz` summaries.

A JSON diff for this purpose must NOT be a text diff: two runs whose inputs are identical except
that one writes to `gs://bucket-a/` and the other to `gs://bucket-b/` are the SAME inputs, and a
line diff screams about forty changes while hiding the one key that was actually added.

    python compare/json_diff.py run_a/inputs.json run_b/inputs.json --fold-paths
    python compare/json_diff.py a.json.gz b.json.gz --ignore '*.outputDir' --ignore '*.timestamp'

Rule (fixed, so a rerun means the same thing):
  * keys are reported as JSON pointers (`callers.GERMLINE.cnv_model_tar`); a key present on one side
    only is ADDED or REMOVED, and that is the class that actually catches a pipeline change.
  * a list whose elements are all strings is compared as a SET (added/removed items), not by index —
    re-ordering a file list is not a change, and index-wise comparison invents one.
  * `--fold-paths` compares path-like string values by basename, so a different bucket or scratch
    prefix is not reported as a difference. It is printed as a rule when used, because it is a
    deliberate widening, not a default.
  * numbers are compared exactly unless `--tol` says otherwise; a numeric change is printed with
    both values and the delta, never as "changed".
  * `--ignore` (repeatable, fnmatch on the pointer) excludes keys, and the exclusions are printed in
    the artifact, so a clean result says what was excluded to get there.
Exit: 0 identical under the stated rule, 1 differences, 2 unreadable/invalid JSON.
"""
from __future__ import annotations

import argparse
import fnmatch
import gzip
import io
import json
import os
import sys

import artifact

GZIP_MAGIC = b"\x1f\x8b"


def die(msg: str, code: int = 2) -> None:
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.exit(code)


def load_json(path: str, label: str):
    if not os.path.isfile(path):
        die(f"{label}: no such file: {path}")
    try:
        if open(path, "rb").read(2) == GZIP_MAGIC:
            with io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace") as fh:
                return json.load(fh)
        with open(path, encoding="utf-8", errors="replace") as fh:
            return json.load(fh)
    except ValueError as exc:
        die(f"{label}: {path} is not parseable JSON ({exc}) — diffing text would invent a result", 2)


def is_path_like(value):
    return isinstance(value, str) and ("/" in value or value.startswith(("gs:", "http", "file:")))


def flatten(value, path, fold_paths, out):
    """pointer -> comparable value. Lists of strings become a set keyed with a [] suffix."""
    if isinstance(value, dict):
        for key, child in value.items():
            flatten(child, f"{path}.{key}" if path else str(key), fold_paths, out)
        return
    if isinstance(value, list):
        if all(isinstance(x, str) for x in value):
            for item in value:
                out[("set", f"{path}[]")] = out.get(("set", f"{path}[]"), set()) | {
                    item.rsplit("/", 1)[-1] if fold_paths and is_path_like(item) else item}
            if not value:
                out[("empty-list", path)] = "[]"
            return
        for i, child in enumerate(value):
            flatten(child, f"{path}[{i}]", fold_paths, out)
        return
    if fold_paths and is_path_like(value):
        value = value.rsplit("/", 1)[-1]
    out[("val", path)] = value


def describe(value):
    if isinstance(value, float):
        return "%.10g" % value
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    return text if len(text) <= 70 else text[:67] + "..."


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Diff two JSON/JSON.gz documents as structures, not as text.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("json_a")
    ap.add_argument("json_b")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--fold-paths", action="store_true",
                    help="compare path-like values by basename (different buckets = same input)")
    ap.add_argument("--tol", type=float, default=0.0, help="numeric tolerance (default exact)")
    ap.add_argument("--ignore", action="append", default=[], metavar="POINTER-GLOB")
    ap.add_argument("--max-print", type=int, default=25)
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    args = ap.parse_args()

    doc_a = load_json(args.json_a, args.label_a)
    doc_b = load_json(args.json_b, args.label_b)
    fa, fb = {}, {}
    flatten(doc_a, "", args.fold_paths, fa)
    flatten(doc_b, "", args.fold_paths, fb)

    def ignored(pointer: str) -> bool:
        return any(fnmatch.fnmatch(pointer, pattern) for pattern in args.ignore)

    added = sorted(p for kind, p in fb if kind != "empty-list" and ("val", p) not in fa
                   and ("set", p) not in fa and not ignored(p))
    removed = sorted(p for kind, p in fa if kind != "empty-list" and ("val", p) not in fb
                     and ("set", p) not in fb and not ignored(p))
    changed, numeric = [], []
    for key, value_a in fa.items():
        kind, pointer = key
        if kind == "empty-list" or ignored(pointer) or key not in fb:
            continue
        value_b = fb[key]
        if kind == "set":
            for missing in sorted(value_a - value_b):
                removed.append(f"{pointer} -= {missing}")
            for extra in sorted(value_b - value_a):
                added.append(f"{pointer} += {extra}")
            continue
        if isinstance(value_a, bool) != isinstance(value_b, bool) or type(value_a) is not type(value_b):
            if isinstance(value_a, (int, float)) and isinstance(value_b, (int, float)):
                pass
            else:
                changed.append((pointer, describe(value_a), describe(value_b), "type"))
                continue
        if isinstance(value_a, (int, float)) and isinstance(value_b, (int, float)):
            if abs(value_a - value_b) > args.tol:
                numeric.append((pointer, value_a, value_b, value_b - value_a))
            continue
        if value_a != value_b:
            changed.append((pointer, describe(value_a), describe(value_b), "value"))

    print(f"# {args.label_a}: {args.json_a}  [{os.path.getsize(args.json_a):,} bytes, "
          f"{len(fa)} leaf value(s)]")
    print(f"# {args.label_b}: {args.json_b}  [{os.path.getsize(args.json_b):,} bytes, "
          f"{len(fb)} leaf value(s)]")
    print("# rule: lists of strings compared as SETS; paths folded to basename: %s; numeric tol %g%s"
          % ("YES (stated deliberately)" if args.fold_paths else "no", args.tol,
             ("; ignored " + ", ".join(args.ignore)) if args.ignore else "; no ignores"))

    groups = [("key present only in %s" % args.label_b, added),
              ("key present only in %s" % args.label_a, removed)]
    for title, rows in groups:
        if rows:
            print(f"\n== {title} ({len(rows)})  <- the class that catches a pipeline change")
            for pointer in rows[:args.max_print]:
                print("  " + pointer)
            if len(rows) > args.max_print:
                print(f"  ... {len(rows) - args.max_print} more")
    if numeric:
        print(f"\n== numeric changes ({len(numeric)})")
        for pointer, va, vb, delta in numeric[:args.max_print]:
            print(f"  {pointer:<60} {va!r} -> {vb!r}   delta {delta:+.6g}")
    if changed:
        print(f"\n== value/type changes ({len(changed)})")
        for pointer, va, vb, kind in changed[:args.max_print]:
            print(f"  {pointer:<60} {va}  ->  {vb}   [{kind}]")

    total = len(added) + len(removed) + len(numeric) + len(changed)
    print(f"\n== SUMMARY  {len(added)} added, {len(removed)} removed, {len(numeric)} numeric, "
          f"{len(changed)} value/type -> {total} difference(s)")
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"tool": "json_diff", "argv": sys.argv[1:],
                       "inputs": {**artifact.input_file(args.label_a, args.json_a,
                                                        leaves=len(fa)),
                                  **artifact.input_file(args.label_b, args.json_b,
                                                        leaves=len(fb))},
                       "compared_something": bool(fa and fb),
                       "rule": {"fold_paths": args.fold_paths, "tol": args.tol,
                                "ignore": args.ignore, "string_lists": "compared as sets"},
                       "differences": {"only_b": added, "only_a": removed,
                                       "numeric": [{"pointer": p, args.label_a: va, args.label_b: vb,
                                                    "delta": d} for p, va, vb, d in numeric],
                                       "value": [{"pointer": p, "a": va, "b": vb, "kind": k}
                                                 for p, va, vb, k in changed]},
                       "counts": {"added": len(added), "removed": len(removed),
                                  "numeric": len(numeric), "value": len(changed)},
                       "identical": total == 0}, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote artifact -> {args.json_out}")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
