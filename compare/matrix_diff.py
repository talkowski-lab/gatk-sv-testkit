#!/usr/bin/env python3
"""Wide-matrix diff: key column(s) x one column PER SAMPLE — and which sample actually moved.

Why this exists
---------------
Half the artefacts that decide a GATK-SV result are not VCFs and not key/value tables; they are
matrices with a sample on every column:

    <batch>.RD.txt.gz                  #Chr Start End + one depth per (bin, sample)   [159 columns]
    <batch>_medianCov.transposed.bed   sample names on one line, one value per sample below it
    <batch>.bincov-matrix.tsv          binned coverage, same shape
    all_batches.ploidy.tsv             SAMPLE x chromosome
    <batch>_WGD_scoring_matrix.bed.gz  whole-genome-duplication scoring

`compare/` had nothing that could open a single one of them, so every question of the form "did
sample X's evidence change?" was unanswerable — the existing comparators aggregate over all
samples before printing anything. This tool keeps the sample axis: its per-column table IS the
per-sample answer, in descending order of how far that sample moved.

    python compare/matrix_diff.py base/all_samples.RD.txt.gz new/all_samples.RD.txt.gz \
           --align order --worst 10 --json /tmp/rd.json
    python compare/matrix_diff.py a.tsv.gz b.tsv.gz --key-cols 0     # samples-are-columns, 1 row

Rule (fixed, so a rerun means the same thing):
  * key columns default to the leading identifier-shaped ones (`#Chr`, `Start`, `End`, ...) and the
    detection is printed; `--key-cols 0` means "no key, the whole row is data".
  * value columns are joined **by name**, never by position: a cohort that gained a sample, or a
    table whose samples were re-sorted, is not a table whose values changed. That distinction is
    the entire point of this file, and it is the bug the sibling comparator still has.
  * `--align order` (default) walks the two files in lockstep and checks the key at EVERY row.
    O(1) memory, one pass, and it stops with exit 2 the moment the two grids diverge — a partial
    comparison reported as a complete one is the failure mode this repo keeps naming.
  * `--align index` loads the first file's rows (bounded by `--max-rows`) so the second can be
    joined in any order. Slower, and it says so rather than pretending to be free.
  * cells that are missing (`.`/`-`/`NA`/empty) or unparseable are counted per column, never
    averaged; mean/mean|diff| are over comparable cells only.
Exit: 0 no cell outside tolerance, 1 at least one, 2 nothing (or only part) was compared.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys

MISSING_VALUES = {".", "-", "NA", "N/A", "nan", "NaN", "None", ""}
KEYCOLS = {"#chr", "chr", "chrom", "#chrom", "start", "end", "pos", "bin", "bin_idx", "interval",
           "name", "id", "copy_state", "algtype", "svtype", "group", "metric", "sample",
           "sample_id", "contig", "state", "bin_index"}
GZIP_MAGIC = b"\x1f\x8b"


def die(msg: str, code: int = 2) -> None:
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.exit(code)


def open_text(path: str):
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


def number(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


class Matrix:
    """Header + row stream of a wide table. Values are kept as text until a column is asked for."""

    def __init__(self, path: str, label: str):
        self.path, self.label = path, label
        self.fh = open_text(path)
        head = next(self.fh, None)
        if head is None:
            die(f"{label}: {path} is empty, so there is nothing to compare", 2)
        self.columns = [c.strip() for c in head.rstrip("\n").split("\t")]
        if len(self.columns) < 2:
            die(f"{label}: {path} has {len(self.columns)} tab-separated column(s) on its first "
                f"line, so it is not a wide matrix.\n"
                f"       a key/value or one-row-per-metric table belongs in compare/table_diff.py", 2)
        self.bytes = os.path.getsize(path)
        self.digest = digest(path)
        self.rows = 0

    def rows_iter(self):
        for line in self.fh:
            if not line.strip():
                continue
            self.rows += 1
            yield line.rstrip("\n").split("\t")

    def close(self):
        self.fh.close()


def pick_key_columns(columns: list, wanted: str, label: str, path: str) -> list:
    """Which leading columns identify a row. Printed by the caller: never a silent choice."""
    if wanted == "auto":
        out = []
        for name in columns:
            if name.lower() in KEYCOLS:
                out.append(name)
            else:
                break
        return out
    if wanted.isdigit():
        n = int(wanted)
        if n > len(columns):
            die(f"{label}: --key-cols {n} exceeds the {len(columns)} columns of {path}")
        return columns[:n]
    chosen = [c.strip() for c in wanted.split(",") if c.strip()]
    for c in chosen:
        if c not in columns:
            die(f"{label}: --key-cols names {c!r}, which is not a column of {path}\n"
                f"       actual columns: {columns[:12]}"
                f"{' ...' if len(columns) > 12 else ''} ({len(columns)} total)")
    return chosen


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Diff two wide key x sample matrices (RD evidence, bincov, ploidy, medianCov).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("matrix_a")
    ap.add_argument("matrix_b")
    ap.add_argument("--key-cols", default="auto", metavar="auto|N|COL,COL",
                    help="identifier columns (default auto: leading identifier-shaped); 0 = none")
    ap.add_argument("--align", choices=("order", "index"), default="order",
                    help="order = lockstep, verifies the key every row (default, O(1) memory); "
                         "index = load A, then join B in any order")
    ap.add_argument("--max-rows", type=int, default=2_000_000,
                    help="--align index only: rows of A held in memory (default 2,000,000)")
    ap.add_argument("--tol", type=float, default=1e-6,
                    help="relative tolerance; a cell inside it counts as MATCH (default 1e-6)")
    ap.add_argument("--abs-tol", type=float, default=0.0, help="absolute tolerance (0 = off)")
    ap.add_argument("--only-sample", action="append", default=[], metavar="NAME")
    ap.add_argument("--ignore-sample", action="append", default=[], metavar="NAME")
    ap.add_argument("--label-a", default="baseline")
    ap.add_argument("--label-b", default="new")
    ap.add_argument("--worst", type=int, default=5,
                    help="rows with the largest mean |diff| to print (default 5)")
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    args = ap.parse_args()

    a = Matrix(args.matrix_a, args.label_a)
    b = Matrix(args.matrix_b, args.label_b)
    keys_a = pick_key_columns(a.columns, args.key_cols, args.label_a, a.path)
    keys_b = pick_key_columns(b.columns, args.key_cols, args.label_b, b.path)
    if keys_a != keys_b:
        die(f"key columns differ between the two files ({args.label_a}={keys_a}, "
            f"{args.label_b}={keys_b}), so their row grids cannot be identified against each other")

    shared_keys = [a.columns.index(k) for k in keys_a]
    idx_b_key = [b.columns.index(k) for k in keys_a]
    only = set(args.only_sample)
    samples_a = [c for c in a.columns if c not in keys_a and c not in args.ignore_sample
                 and (not only or c in only)]
    samples_b = [c for c in b.columns if c not in keys_b and c not in args.ignore_sample
                 and (not only or c in only)]
    shared = [c for c in samples_a if c in samples_b]
    a_only = [c for c in samples_a if c not in samples_b]
    b_only = [c for c in samples_b if c not in samples_a]
    ia = [a.columns.index(c) for c in shared]
    ib = [b.columns.index(c) for c in shared]

    print(f"# {args.label_a}: {a.path}  [{a.bytes} bytes, sha256:{a.digest}, "
          f"{len(a.columns)} columns]")
    print(f"# {args.label_b}: {b.path}  [{b.bytes} bytes, sha256:{b.digest}, "
          f"{len(b.columns)} columns]")
    print(f"# key: {'+'.join(keys_a) or '(none — the whole row is data)'} "
          f"(--key-cols {args.key_cols})")
    print(f"# sample/value columns: shared {len(shared)}, {args.label_a}-only {len(a_only)}, "
          f"{args.label_b}-only {len(b_only)}")
    if a_only or b_only:
        print("#   !! the two tables do not cover the same samples — that is a cohort difference, "
              "not a value change")
        for name, lst in ((args.label_a, a_only), (args.label_b, b_only)):
            if lst:
                print(f"#       only in {name} ({len(lst)}): {', '.join(lst[:8])}"
                      f"{' ...' if len(lst) > 8 else ''}")
    if not shared:
        die("FATAL: 0 sample columns shared between the two files, so nothing was compared.\n"
            f"       {args.label_a} columns: {samples_a[:8]}\n"
            f"       {args.label_b} columns: {samples_b[:8]}\n"
            f"       Usual causes: sample-name namespace differs, one file is transposed and the\n"
            f"       other is not, or --key-cols swallowed the sample columns.", 2)

    # one accumulator per shared sample column: n, exact, within, |diff| sums, max, means, missing
    acc = {c: dict(n=0, exact=0, within=0, sum_abs=0.0, max_abs=0.0, sum_a=0.0, sum_b=0.0,
                   missing=0, unparse=0) for c in shared}
    worst = []                                    # (mean|diff|, key, worst column, worst |diff|)
    compared_rows = rows_a = rows_b = skipped_a = skipped_b = 0
    mismatch = None

    if args.align == "order":
        iter_a, iter_b = a.rows_iter(), b.rows_iter()
        ra, rb = next(iter_a, None), next(iter_b, None)
        while ra is not None and rb is not None:
            ka = [ra[i] if i < len(ra) else "" for i in shared_keys]
            kb = [rb[i] if i < len(rb) else "" for i in idx_b_key]
            if ka != kb:
                mismatch = (rows_a, ka, kb)
                break
            _compare_row(ka, ra, rb, ia, ib, shared, acc, args, worst)
            compared_rows += 1
            rows_a += 1
            rows_b += 1
            ra, rb = next(iter_a, None), next(iter_b, None)
        while ra is not None:
            skipped_a += 1
            rows_a += 1
            ra = next(iter_a, None)
        while rb is not None:
            skipped_b += 1
            rows_b += 1
            rb = next(iter_b, None)
    else:
        index = {}
        for ra in a.rows_iter():
            ka = tuple(ra[i] if i < len(ra) else "" for i in shared_keys)
            if ka in index:
                continue
            if len(index) >= args.max_rows:
                die(f"--align index held {args.max_rows:,} rows of {args.label_a} and stopped.\n"
                    f"       Use --align order (O(1) memory, verifies the grid instead of indexing "
                    f"it),\n       or narrow the two files to one region first.", 2)
            index[ka] = [ra[i] if i < len(ra) else "" for i in ia]
        rows_a = len(index)
        rows_b = 0
        for rb in b.rows_iter():
            kb = tuple(rb[i] if i < len(rb) else "" for i in idx_b_key)
            if kb not in index:
                skipped_b += 1
                continue
            _compare_row(list(kb), index[kb], rb, list(range(len(shared))), ib, shared, acc, args, worst)
            compared_rows += 1
            rows_b += 1
        skipped_a = rows_a - compared_rows

    a.close()
    b.close()

    if mismatch:
        die(f"FATAL: the two grids diverged before row {mismatch[0] + 1:,} — compared "
            f"{compared_rows:,} row(s) then stopped.\n"
            f"       {args.label_a} key: {mismatch[1]}\n"
            f"       {args.label_b} key: {mismatch[2]}\n"
            f"       A partial comparison must not be printed as a complete one. Either the files "
            f"are\n"
            f"       ordered differently (--align index), cover different regions, or use\n"
            f"       different contig naming (chr20 vs 20).", 2)
    print(f"# rows compared {compared_rows:,}  ({args.label_a}-only {skipped_a:,}, "
          f"{args.label_b}-only {skipped_b:,})"
          + ("" if args.align == "order" else "  [index join: order-independent]"))
    if args.align == "order" and (skipped_a or skipped_b):
        print("#   !! --align order stopped at the first divergence, so unskipped rows beyond it "
              "were never seen")

    cells = sum(acc[c]["n"] for c in shared)
    print(f"\n{'sample':<24} {'cells':>10} {'mean|d|':>10} {'max|d|':>10} {'within':>9} "
          f"{'exact':>9} {'mean A':>12} {'mean B':>12}")
    order = sorted(shared, key=lambda c: -(acc[c]["sum_abs"] / acc[c]["n"] if acc[c]["n"] else -1))
    per_sample = []
    for c in order:
        s = acc[c]
        n = s["n"]
        mabs = s["sum_abs"] / n if n else float("nan")
        within = s["within"] / n if n else float("nan")
        per_sample.append({"sample": c, "cells": n, "mean_abs_diff": mabs, "max_abs_diff": s["max_abs"],
                           "within_tol": within, "exact": s["exact"] / n if n else float("nan"),
                           "mean_a": s["sum_a"] / n if n else float("nan"),
                           "mean_b": s["sum_b"] / n if n else float("nan"),
                           "missing": s["missing"], "unparseable": s["unparse"]})
        if n:
            print(f"{c[:24]:<24} {n:10,d} {mabs:10.4g} {s['max_abs']:10.4g} {within:9.4%} "
                  f"{s['exact'] / n:9.4%} {s['sum_a'] / n:12.6g} {s['sum_b'] / n:12.6g}")
        else:
            print(f"{c[:24]:<24} {0:10,d} {'-- no comparable cell --':>42}  "
                  f"missing {s['missing']:,}, unparseable {s['unparse']:,}")
    if len(order) > 25:
        print(f"... {len(order) - 25} more column(s); all of them are in --json")

    if worst and compared_rows:
        worst.sort(key=lambda t: -t[0])
        width = max((len("+".join(map(str, w[1]))) for w in worst[:args.worst]), default=4) or 4
        print(f"\n== rows with the largest mean |diff| (first {args.worst} of {compared_rows:,})")
        for mean_abs, key, col, d in worst[:args.worst]:
            print(f"  {'+'.join(map(str, key)):<{width}}  mean|diff| {mean_abs:<10.6g} "
                  f"worst column {col} ({d:.6g})")

    inside = sum(acc[c]["within"] for c in shared)
    print(f"\n== SUMMARY  {compared_rows:,} row(s) x {len(shared)} sample column(s) = "
          f"{cells:,} comparable cell(s); inside tolerance {inside:,} "
          f"({inside / cells:.4%})" if cells else
          "\n== NOTHING COMPARABLE: every cell of every shared column was missing or unparseable")

    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"tool": "matrix_diff", "argv": sys.argv[1:],
                       "inputs": {args.label_a: {"path": a.path, "bytes": a.bytes, "sha256_16": a.digest,
                                                 "columns": len(a.columns)},
                                  args.label_b: {"path": b.path, "bytes": b.bytes,
                                                 "sha256_16": b.digest, "columns": len(b.columns)}},
                       "rule": {"key_columns": keys_a, "align": args.align, "tol_rel": args.tol,
                                "tol_abs": args.abs_tol},
                       "samples": {"shared": len(shared), "only_a": a_only, "only_b": b_only},
                       "rows_compared": compared_rows, "cells_compared": cells,
                       "cells_inside_tolerance": inside, "per_sample": per_sample,
                       "worst_rows": [{"key": w[1], "mean_abs_diff": w[0], "column": w[2],
                                       "max_cell_diff": w[3]} for w in sorted(worst, key=lambda t: -t[0])[:50]],
                       "compared_something": cells > 0}, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote artifact -> {args.json_out}")

    if not cells:
        return 2
    return 0 if inside == cells else 1


def _compare_row(key, row_a, row_b, ia, ib, shared, acc, args, worst) -> None:
    """Accumulate one joined row into the per-sample statistics."""
    per_cell = []
    for slot, col in enumerate(shared):
        va = row_a[ia[slot]] if ia[slot] < len(row_a) else ""
        vb = row_b[ib[slot]] if ib[slot] < len(row_b) else ""
        s = acc[col]
        if va in MISSING_VALUES and vb in MISSING_VALUES:
            continue
        if va in MISSING_VALUES or vb in MISSING_VALUES:
            s["missing"] += 1
            continue
        na, nb = number(va), number(vb)
        if na is None or nb is None:
            s["unparse"] += 1
            continue
        s["n"] += 1
        s["sum_a"] += na
        s["sum_b"] += nb
        d = abs(na - nb)
        s["sum_abs"] += d
        if d > s["max_abs"]:
            s["max_abs"] = d
        if d == 0.0:
            s["exact"] += 1
        if d <= max(args.tol * max(abs(na), abs(nb)), args.abs_tol, 1e-12):
            s["within"] += 1
        per_cell.append((d, col))
    if per_cell and worst is not None:
        worst.append((sum(d for d, _ in per_cell) / len(per_cell), list(key),
                      max(per_cell)[1], max(per_cell)[0]))
        if len(worst) > 400:                    # bounded: keep the largest, never the whole table
            worst.sort(key=lambda t: -t[0])
            del worst[400:]


if __name__ == "__main__":
    sys.exit(main())
