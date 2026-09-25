#!/usr/bin/env python3
"""Line-set diff: did the cohort, the interval set or the training set change?

Why this exists
---------------
Before any number in `compare/` means anything, the *inputs* have to be the same. Most of those
inputs are not tables at all — they are one-thing-per-line files: the analysis-sample list, the
intervals a run was limited to, `bothside_pass` / `background_fail` lists, `unresolved_svids`, the
`.ped`/`.fam` files de novo calling consumes, per-sample DEL/DUP BEDs. A cohort that quietly gained
or lost one sample explains a lot of "the metric moved", and none of the six comparators could open
one of these files.

    python compare/lineset_diff.py base/validated_samples.list new/validated_samples.list
    python compare/lineset_diff.py a.train.intervals.bed b.train.intervals.bed --columns 3
    python compare/lineset_diff.py a.ped b.ped --delim whitespace --columns 2

Rule (fixed, so a rerun means the same thing):
  * comparison is on the SET of normalized lines, plus per-side duplicate counts — a line appearing
    twice is reported, never silently deduplicated away, because a duplicated sample row in a PED is
    itself the finding.
  * `--columns N` compares only the first N fields (BED: chrom/start/end; a score column that
    moved is not a different interval). `--delim whitespace` splits on runs of blanks.
  * normalization is OPT-IN and printed: `--normalize case`, `--normalize chr` (strip a leading
    chr), `--normalize trim`. Nothing is normalized silently, so a "no difference" cannot be an
    artefact of the tool quietly rewriting one side — the same trap as an unannounced chr prefix.
  * comments (`#`) and blanks are dropped by default, `--keep-comments` keeps them.
Exit: 0 the two sets are equal, 1 they differ, 2 at least one side is empty (nothing to compare).
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys

GZIP_MAGIC = b"\x1f\x8b"


def die(msg: str, code: int = 2) -> None:
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.exit(code)


def open_text(path: str):
    if os.path.isdir(path):
        die(f"{path} is a directory; name a file (a tar bundle belongs in compare/tar_manifest.py)")
    if not os.path.isfile(path):
        die(f"no such file: {path}")
    with open(path, "rb") as raw:
        magic = raw.read(2)
    if magic == GZIP_MAGIC:
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace", newline="")
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


def digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as raw:
        for chunk in iter(lambda: raw.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def load(path, label, columns, delim, normalize, keep_comments):
    counts = {}
    raw_lines = 0
    dupes = 0
    comments = 0
    for line in open_text(path):
        text = line.rstrip("\n").rstrip("\r")
        if not text.strip():
            continue
        raw_lines += 1
        if text.lstrip().startswith("#") and not keep_comments:
            comments += 1
            continue
        fields = text.split() if delim == "whitespace" else text.split(delim)
        if columns is not None:
            fields = fields[:columns]
            if len(fields) < columns:
                die(f"{label}: {path} line {raw_lines} has {len(fields)} field(s), fewer than "
                    f"--columns {columns} — the two files are not the same kind of file", 2)
        key = "\t".join(fields)
        for rule in normalize:
            if rule == "case":
                key = key.lower()
            elif rule == "trim":
                key = "\t".join(f.strip() for f in key.split("\t"))
            elif rule == "chr":
                key = "\t".join(f[3:] if f[:3].lower() == "chr" else f for f in key.split("\t"))
            else:
                die(f"--normalize {rule!r} is not one of case, chr, trim")
        if key in counts:
            dupes += 1
        counts[key] = counts.get(key, 0) + 1
    if not counts:
        die(f"{label}: {path} yielded 0 comparable line(s) ({raw_lines} read, {comments} comment "
            f"line(s) dropped) — nothing to compare", 2)
    return counts, raw_lines, dupes, comments


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compare two line-oriented files as sets (sample lists, intervals, PED, VID lists).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file_a")
    ap.add_argument("file_b")
    ap.add_argument("--columns", type=int, default=None, help="compare only the first N fields")
    ap.add_argument("--delim", default="\t", help="'tab' (default), 'whitespace', or a character")
    ap.add_argument("--normalize", default="", help="comma list of case, chr, trim (default: none)")
    ap.add_argument("--keep-comments", action="store_true", help="lines starting with # are data")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--max-print", type=int, default=20)
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    args = ap.parse_args()

    delim = "\t" if args.delim in ("tab", "\\t") else args.delim
    normalize = [n.strip() for n in args.normalize.split(",") if n.strip()]
    ca, ra, da, cm_a = load(args.file_a, args.label_a, args.columns, delim, normalize,
                            args.keep_comments)
    cb, rb, db, cm_b = load(args.file_b, args.label_b, args.columns, delim, normalize,
                            args.keep_comments)
    print(f"# {args.label_a}: {args.file_a}  [{os.path.getsize(args.file_a):,} bytes, "
          f"sha256:{digest(args.file_a)}, {ra:,} line(s), {len(ca):,} distinct, {da} duplicate(s)"
          f"{f', {cm_a} comment(s) dropped' if cm_a else ''}]")
    print(f"# {args.label_b}: {args.file_b}  [{os.path.getsize(args.file_b):,} bytes, "
          f"sha256:{digest(args.file_b)}, {rb:,} line(s), {len(cb):,} distinct, {db} duplicate(s)"
          f"{f', {cm_b} comment(s) dropped' if cm_b else ''}]")
    print("# compared: set of %s%s; normalization: %s; columns: %s"
          % ("fields" if args.columns else "whole lines",
             "" if delim == "\t" else " (delim %r)" % delim,
             ", ".join(normalize) if normalize else "NONE (nothing was case- or chr-folded)",
             args.columns or "all"))

    common = sorted(set(ca) & set(cb))
    only_a = sorted(set(ca) - set(cb))
    only_b = sorted(set(cb) - set(ca))
    print(f"# shared {len(common)}, only in {args.label_a} {len(only_a)}, only in {args.label_b} "
          f"{len(only_b)}")
    for name, rows in ((f"only in {args.label_a}", only_a), (f"only in {args.label_b}", only_b)):
        if not rows:
            continue
        print(f"\n== {name} ({len(rows)})")
        for row in rows[:args.max_print]:
            print("  " + row.replace("\t", "  "))
        if len(rows) > args.max_print:
            print(f"  ... {len(rows) - args.max_print} more")
    if not only_a and not only_b and (da or db):
        print("\n== the sets are equal but duplicates differ: "
              f"{args.label_a} {da}, {args.label_b} {db} — check for a duplicated sample row")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"tool": "lineset_diff", "argv": sys.argv[1:],
                       "inputs": {args.label_a: {"path": args.file_a, "bytes": os.path.getsize(args.file_a),
                                                 "sha256_16": digest(args.file_a), "lines": ra,
                                                 "distinct": len(ca), "duplicates": da},
                                  args.label_b: {"path": args.file_b, "bytes": os.path.getsize(args.file_b),
                                                 "sha256_16": digest(args.file_b), "lines": rb,
                                                 "distinct": len(cb), "duplicates": db}},
                       "rule": {"columns": args.columns, "delim": delim, "normalize": normalize,
                                "keep_comments": args.keep_comments},
                       "compared_something": bool(ca and cb),
                       "sets": {"shared": len(common), "only_a": only_a[:2000], "only_b": only_b[:2000],
                                "counts": {"only_a": len(only_a), "only_b": len(only_b)}},
                       "equal": not only_a and not only_b}, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote artifact -> {args.json_out}")
    return 0 if not only_a and not only_b else 1


if __name__ == "__main__":
    sys.exit(main())
