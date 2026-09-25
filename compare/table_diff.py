#!/usr/bin/env python3
"""Keyed row-wise diff of ANY delimited table: the rule stays written down, the file names do not.

Why this exists
---------------
`compare/` already had a table differ, but it knew seven v1.1.1 filenames. Point it at two trees
that share a schema — branch vs branch, rerun vs rerun, or either side against the 16 `*.tsv.gz`
tables a `gatk-sv-profile` run leaves on disk — and every column came back `MISSING`. The reason
is structural: the *reader* was chosen by which side of the diff a file came from, not by the
file, so the documented `--baseline-file ROLE=PATH` override moved the filename and not the
format. A columnar file on the baseline side was parsed as key/value, and the output said
`MISSING` — which reads as "the baseline has no such parameter" when it means "I read this file
with the wrong reader". That is this repo's own named failure class, reached through the escape
hatch, which is why generativity got fixed instead of another filename being added.

So: this tool takes **a pair of files and a key column**. Nothing else is assumed to be true —
not the extension, not which side is "newer", not whether the schema is v1.1.1's or the current
pipeline's.

    # same-schema, the case the old tool could not express: two current-format run trees
    python compare/table_diff.py runs/a/all_samples.sr_geno_params.tsv \
                                   runs/b/all_samples.sr_geno_params.tsv --key sample_id

    # a gatk-sv-profile module table pair (it writes one file PER SIDE, same header)
    python compare/table_diff.py results/paired/genotype_quality/tables/gq_summary.base.tsv.gz \
                                 results/paired/genotype_quality/tables/gq_summary.new.tsv.gz \
                                 --key algorithm --out-prefix /tmp/gq --json /tmp/gq.json

    # the rule belongs in a file, not in a head: see docs/comparators.md for the spec format
    python compare/table_diff.py A.tsv B.tsv --spec my_rule.json

Rule (fixed, so a rerun means the same thing):
  * rows join on `--key` (default: first column); composite key is `--key colA,colB`.
  * columns compare **by name, never by position** — a reordered table is the same table.
  * each column's kind is inferred (all-integer -> exact; else numeric -> relative; else exact
    text) and printed with the value that produced it; `--spec` overrides it, and an override is
    printed AS an override so the arithmetic someone else must reproduce is on the page.
  * `.`/`-`/`NA`/`N/A`/`nan`/empty are missing values: counted, never compared and never averaged.
  * a column present on one side only is `ONE_SIDED`, not a delta — one side computed more
    columns is a coverage fact, not a difference (docs/comparators.md).
  * keys that appear more than once are COUNTED and reported. The first row wins; nothing is
    collapsed silently, because last-writer-wins is the accident you need to be able to see.
  * `strategy` columns (spec) print both values and are never diffed as bare numbers: a scale or
    definition change is not a delta, and `scale` in the spec lets the tool *say* whether the
    two definitions are consistent instead of quietly assuming they are not.

Exit status carries whether a comparison happened at all:
  0  compared, no DELTA cell
  1  compared, >= 1 DELTA cell. DELTA is not "you broke something": see docs/terra-head-to-head.md
     section 7 — it is a bug, an intended behaviour change, or an uncontrolled input difference,
     and only you can say which.
  2  NOTHING was compared (zero shared keys, zero comparable cells, or unreadable input). This is
     the important one: an empty diff that exits 0 next to a real run reads like a result.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import itertools
import json
import os
import re
import sys

MATCH, DELTA, ONE_SIDE, ONE_SIDED, STRATEGY, SKIP = ("MATCH", "DELTA", "ONE_SIDE", "ONE_SIDED",
                                                     "STRATEGY", "SKIP")
MISSING_VALUES = {".", "-", "NA", "N/A", "nan", "NaN", "None", ""}
# Columns that identify a row rather than measuring it. Used ONLY to pick a default --key, and the
# choice is printed, so a data column can never become an undocumented join rule.
IDENTIFIERS = ("sample_id", "key", "metric", "param", "parameter", "group", "algorithm",
               "combination", "category", "contig", "chrom", "svtype", "region", "interval",
               "bin", "name", "id", "sample", "variant_id", "vid", "strands", "copy_state")
INT_RE = re.compile(r"[-+]?\d+$")
GZIP_MAGIC = b"\x1f\x8b"
HEADER = ["column", "kind", "cells", MATCH, DELTA, ONE_SIDE, "verdict"]


def die(msg: str, code: int = 2) -> None:
    """Everything here that cannot compare says so loudly, at a status code that MEANS it.

    `sys.exit("text")` exits 1 — which is this tool's "there is a DELTA" code. A "nothing was
    compared" case must not be able to impersonate a real result, so the code is explicit.
    """
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.exit(code)


def open_text(path: str):
    """Plain or gzip/BGZF, decided from the bytes.

    The old tools called `gzip.open` unconditionally and died with `BadGzipFile: Not a gzipped
    file (b'##')` on an uncompressed VCF — a traceback instead of an answer. BGZF (what
    `gatk-sv-profile` and the pipeline write) is a legal multi-member gzip stream, so one path
    covers both.
    """
    if not os.path.isfile(path):
        die(f"no such file: {path}")
    with open(path, "rb") as raw:
        magic = raw.read(2)
    if magic == GZIP_MAGIC:
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace", newline="")
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


def sniff_delim(sample_lines: list, wanted: str) -> str:
    if wanted and wanted != "auto":
        return {"tab": "\t", "comma": ",", "semicolon": ";"}.get(wanted, wanted)
    counts = {d: sum(line.count(d) for line in sample_lines) for d in ("\t", ",", ";")}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else "\t"


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as raw:
        for chunk in iter(lambda: raw.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class Table:
    """One side: header, rows keyed for joining, and the facts about how badly it read."""

    def __init__(self, path: str, delim: str, has_header: bool, max_rows: int, label: str):
        self.path, self.label = path, label
        self.max_rows = max_rows
        self.notes, self.ragged, self.dupe_rows, self.rows = [], 0, 0, {}
        self.order = []
        with open_text(path) as fh:
            head = next(fh, None)
            if head is None:
                die(f"{label}: {path} is empty, so there is nothing to compare", 2)
            self.delim = sniff_delim([head], delim)
            # ONE reader over the whole stream. `csv.reader(lines, ",")` is also the second
            # positional slot being *dialect*, not delimiter — which raises `unknown dialect`
            # inside main(), i.e. after --help and py_compile have both passed.
            reader = csv.reader(itertools.chain([head], fh), delimiter=self.delim)
            first = next(reader)
            if has_header:
                self.columns = dedupe([c.strip() for c in first])
            else:
                self.columns = ["col%d" % (i + 1) for i in range(len(first))]
            for row in reader:
                if not row or not any(f.strip() for f in row):
                    continue
                if not has_header and not self.order:      # row 1 is data, not a header
                    self.remember(row)
                    continue
                if len(row) != len(self.columns):
                    self.ragged += 1
                    row = row[:len(self.columns)] + [""] * (len(self.columns) - len(row))
                self.remember(row)
        self.bytes = os.path.getsize(path)
        self.digest = sha256_of(path)
        if self.ragged:
            self.notes.append(f"{label}: {self.ragged} row(s) had a different field count than the "
                              f"header; padded/truncated to the header width and counted")

    def remember(self, row: list) -> None:
        """Index one row by its whole content, so a repeated line is a counted dupe, not a merge."""
        key = tuple(row)
        if key in self.rows:
            self.dupe_rows += 1                       # identical line: counted, never merged quietly
            return
        if len(self.rows) >= self.max_rows:
            die(f"{self.label}: {self.path} exceeded --max-rows {self.max_rows:,} rows. This tool "
                f"holds both tables in memory;\n"
                f"  for a wide per-sample matrix use compare/matrix_diff.py (streams, and\n"
                f"  answers which sample moved), or narrow with --only-col.", 2)
        self.rows[key] = row
        self.order.append(key)

    def index(self, name: str) -> int:
        if name in self.columns:
            return self.columns.index(name)
        die(f"{self.label}: --key column {name!r} is not a column of {self.path}\n"
            f"       actual columns: {self.columns}\n"
            f"       (a headerless file needs --no-header, and an index like --key 0 works too)")
        raise AssertionError  # unreachable: die() exits

    def cell(self, row_key: tuple, idx: int) -> str:
        """One field of a stored row. Takes the ROW key (the whole line), not a join key —
        passing a join key here is a KeyError by design, because the two are different things."""
        return (self.rows[row_key][idx] if idx is not None else "").strip()


def dedupe(names: list) -> list:
    """Duplicate header names get a suffix instead of a silent collision."""
    seen, out = {}, []
    for i, n in enumerate(names):
        if not n:
            n = "col%d" % (i + 1)
        seen[n] = seen.get(n, 0) + 1
        out.append(n if seen[n] == 1 else "%s#%d" % (n, seen[n]))
    return out


def number(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def infer_kind(values: list) -> str:
    """exact for all-integer, rel for all-numeric, text otherwise. Printed, so it can be argued with."""
    seen = [v for v in values if v not in MISSING_VALUES]
    if not seen:
        return "text"
    if all(INT_RE.match(v) for v in seen):
        return "exact"
    if all(number(v) is not None for v in seen):
        return "rel"
    return "text"


def resolve_key(table: Table, spec_text: str) -> list:
    keys = []
    for part in spec_text.split(","):
        part = part.strip()
        if part.isdigit():                       # positional key: only unambiguous without a header
            idx = int(part)
            if idx >= len(table.columns):
                die(f"{table.label}: --key {part} is past the end of {table.path} "
                    f"({len(table.columns)} columns)")
            keys.append(idx)
        else:
            keys.append(table.index(part))
    return keys


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Keyed row-wise diff of two delimited tables (tsv/csv, plain or gz), any schema.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("Rule (fixed")[0])
    ap.add_argument("table_a")
    ap.add_argument("table_b")
    ap.add_argument("--key", default=None, metavar="COL[,COL]",
                    help="join column(s): name or 0-based index; default: first column of A")
    ap.add_argument("--delim", default="auto", help="auto (default), tab, comma, semicolon, or a char")
    ap.add_argument("--no-header", action="store_true", help="first line is data on BOTH sides")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--spec", type=argparse.FileType("r"), default=None,
                    help="JSON rule file: per-column kind/tolerance/scale/note (docs/comparators.md)")
    ap.add_argument("--tol", type=float, default=1e-9, help="relative tolerance for numeric columns")
    ap.add_argument("--abs-tol", type=float, default=0.0, help="absolute tolerance (0 = off)")
    ap.add_argument("--only-col", action="append", default=[], metavar="COL")
    ap.add_argument("--ignore-col", action="append", default=[], metavar="COL")
    ap.add_argument("--max-rows", type=int, default=2_000_000,
                    help="rows loaded per side (default 2,000,000); exceeding it exits 2 rather than OOM")
    ap.add_argument("--max-print", type=int, default=20, help="differing cells shown on stdout")
    ap.add_argument("--out-prefix", help="write the complete <prefix>.tsv of differing cells")
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    args = ap.parse_args()

    spec = json.load(args.spec) if args.spec else {}
    spec_cols = spec.get("columns", {})
    tol = float(spec.get("tol", {}).get("rel", args.tol))
    abs_tol = float(spec.get("tol", {}).get("abs", args.abs_tol))
    ignore = list(args.ignore_col) + list(spec.get("ignore", []))

    a = Table(args.table_a, args.delim, not args.no_header, args.max_rows, args.label_a)
    b = Table(args.table_b, args.delim, not args.no_header, args.max_rows, args.label_b)

    key_spec = args.key or spec.get("key")
    key_source = "--key" if args.key else ("spec" if spec.get("key") else "")
    if not key_spec:
        # A data column silently chosen as the join key is an invented rule. Prefer a column that
        # is an identifier by convention, say which one was picked, and never pretend the default
        # was a decision someone made.
        lowered = [c.lower() for c in a.columns]
        for candidate in IDENTIFIERS:
            if candidate in lowered:
                key_spec = a.columns[lowered.index(candidate)]
                key_source = "default: identifier-shaped column"
                break
        else:
            key_spec = a.columns[0]
            key_source = ("default: FIRST column, and NO identifier-shaped column exists here — "
                          "this key is probably DATA")
    keys_a, keys_b = resolve_key(a, key_spec), resolve_key(b, key_spec)
    if [a.columns[i] for i in keys_a] != [b.columns[i] for i in keys_b]:
        die(f"key columns resolved to different names ({a.label}={[a.columns[i] for i in keys_a]}, "
            f"{b.label}={[b.columns[i] for i in keys_b]}) — the two files are not the same table, "
            f"so a name-keyed diff would compare unrelated columns.\n"
            f"       {a.label} columns: {a.columns}\n"
            f"       {b.label} columns: {b.columns}")
    key_names = [a.columns[i] for i in keys_a]
    print(f"# key: {'+'.join(key_names)} (source: {key_source})")

    def keyed(table: Table, idxs: list):
        """join-key -> row, keeping the FIRST row per key and COUNTING the ones that lost.

        Collapsing here is how a comparator silently invents a result: two rows sharing one join
        key is a fact about the input that has to reach the page, and `gq_paired_compare.py`
        collapsed duplicate VIDs without even counting them.
        """
        out, lost = {}, 0
        for k in table.order:
            kk = tuple(table.cell(k, i) for i in idxs)
            if kk in out:
                lost += 1
                continue
            out[kk] = k
        return out, lost

    ka, lost_a = keyed(a, keys_a)
    kb, lost_b = keyed(b, keys_b)
    shared = sorted(set(ka) & set(kb))
    print(f"# {args.label_a}: {a.path}  [{a.bytes} bytes, sha256:{a.digest}, {len(a.rows)} rows, "
          f"{len(a.columns)} cols, delim={a.delim!r}{' ,no-header' if args.no_header else ''}]")
    print(f"# {args.label_b}: {b.path}  [{b.bytes} bytes, sha256:{b.digest}, {len(b.rows)} rows, "
          f"{len(b.columns)} cols, delim={b.delim!r}]")
    print(f"# join on {'+'.join(key_names)}: shared {len(shared)}  "
          f"{args.label_a}-only {len(ka) - len(shared)}  {args.label_b}-only {len(kb) - len(shared)}"
          f"  (key values, not rows: {a.dupe_rows + b.dupe_rows} identical line(s) and "
          f"{lost_a + lost_b} extra row(s) sharing a key were not joined)")
    if lost_a or lost_b:
        print(f"#   !! duplicate join keys: {args.label_a} {lost_a}, {args.label_b} {lost_b} — the "
              f"first row per key won,\n"
              f"     which is a choice, not a measurement")

    if not shared:
        die(f"FATAL: 0 shared {'+'.join(key_names)} values, so nothing was compared.\n"
            f"       {args.label_a} has {len(ka)} key value(s), {args.label_b} has {len(kb)}.\n"
            f"       first {args.label_a} keys: {[list(k) for k in list(ka)[:3]]}\n"
            f"       first {args.label_b} keys: {[list(k) for k in list(kb)[:3]]}\n"
            f"       Usual causes: --key naming the wrong column, a contig prefix difference\n"
            f"       (chr20 vs 20), or these being two different tables entirely.", 2)

    only = set(args.only_col)
    common = [c for c in a.columns if c in b.columns and c not in key_names and c not in ignore
              and (not only or c in only)]
    one_sided = [c for c in a.columns + b.columns if (c in a.columns) != (c in b.columns)
                 and c not in key_names and c not in ignore]
    if one_sided:
        print("# ONE_SIDED columns (not diffed): "
              + ", ".join("%s[only in %s]" % (c, args.label_a if c in a.columns else args.label_b)
                          for c in dict.fromkeys(one_sided)))

    rows, deltas, cells = [], 0, 0
    for col in common:
        ia, ib = a.columns.index(col), b.columns.index(col)
        rule = spec_cols.get(col, {})
        kind = rule.get("kind") or infer_kind([a.cell(ka[k], ia) for k in shared]
                                             + [b.cell(kb[k], ib) for k in shared])
        origin = "spec" if "kind" in rule else "inferred"
        scale = rule.get("scale")
        factor = 1.0
        if scale:
            num, _, den = str(scale).partition("/")
            factor = float(num) / float(den or 1)
        counts = {MATCH: 0, DELTA: 0, ONE_SIDE: 0, STRATEGY: 0, SKIP: 0}
        for key in shared:
            va, vb = a.cell(ka[key], ia), b.cell(kb[key], ib)
            ma, mb = va in MISSING_VALUES, vb in MISSING_VALUES
            if ma and mb:
                counts[SKIP] += 1
                continue
            if ma != mb:
                counts[ONE_SIDE] += 1
                deltas += 1
                rows.append([a.cell(ka[key], keys_a[0]), col, va, vb, ONE_SIDE, ""])
                continue
            if kind == "strategy" or str(scale).lower() == "strategy":
                counts[STRATEGY] += 1
                rows.append([a.cell(ka[key], keys_a[0]), col, va, vb, STRATEGY,
                             rule.get("note", "")])
                continue
            if kind == "text":
                ok = va == vb
                note = ""
            else:
                na, nb = number(va), number(vb)
                na = na * factor if na is not None else None
                if na is None or nb is None:
                    counts[ONE_SIDE] += 1
                    continue
                d = abs(na - nb)
                ok = d <= max(tol * max(abs(na), abs(nb)), abs_tol, 1e-12) if kind == "rel" else na == nb
                note = "" if scale is None else "scale=%s" % scale
            counts[MATCH if ok else DELTA] += 1
            if not ok:
                deltas += 1
                rows.append([a.cell(ka[key], keys_a[0]), col, va, vb, DELTA, note])
        cells += sum(counts[x] for x in (MATCH, DELTA, ONE_SIDE, STRATEGY))
        verdict = DELTA if counts[DELTA] else (STRATEGY if counts[STRATEGY] else MATCH)
        rows_note = f" (scale {scale})" if scale and str(scale).lower() != "strategy" else ""
        print(f"  {col:28s} {kind:6s} {origin:8s} cells {counts[MATCH] + counts[DELTA] + counts[ONE_SIDE]:6d}"
              f"  {MATCH} {counts[MATCH]:6d}  {DELTA} {counts[DELTA]:6d}  {ONE_SIDE} {counts[ONE_SIDE]:4d}"
              f"  {'-> ' + verdict if verdict != MATCH else 'MATCH'}{rows_note}")

    if rows:
        print(f"\n== notable cells ({len(rows)}: DELTA, ONE_SIDE and STRATEGY rows are listed, MATCH "
              f"is not; first {min(args.max_print, len(rows))} shown, complete in the TSV when "
              f"--out-prefix is given)")
        width = max(len(str(r[0])) for r in rows[:args.max_print]) + 1
        print("  %-*s  %-24s  %-18s  %-18s  %s" % (width, key_names[0], "column",
                                                    args.label_a, args.label_b, "verdict"))
        for r in rows[:args.max_print]:
            print("  %-*s  %-24s  %-18s  %-18s  %s %s" % (width, r[0], r[1], r[2], r[3], r[4], r[5]))

    notes = list(spec.get("notes", []))
    if args.no_header:
        notes.append("no-header mode: columns are col1..colN, so a reordered file IS a different "
                     "table by design; confirm the two files were written the same way")
    if len(ka) == 1 or len(kb) == 1:
        notes.append(f"key column {'+'.join(key_names)} has "
                     f"{min(len(ka), len(kb))} distinct value on at least one side, so the row join "
                     f"pairs whatever "
                     f"is there; for a one-row parameter table that is the whole comparison, but "
                     f"pass --key explicitly before quoting it")
    for note in notes + a.notes + b.notes:
        print(f"  NOTE {note}")

    compared = cells > 0
    if not compared:
        print("\n== NOTHING COMPARABLE: 0 comparable cells across %d shared key(s) — every shared "
              "cell was\n   missing on both sides, or every non-key column was ONE_SIDED." % len(shared))
    counts_all = [sum(1 for r in rows if r[4] == v) for v in (DELTA, ONE_SIDE, STRATEGY)]
    matched = cells - counts_all[0] - counts_all[1] - counts_all[2]
    print("\n== SUMMARY  %s: %d shared key(s), %d column(s), %d cell(s) compared | %s %d, %s %d, "
          "%s %d, %s %d"
          % (key_names[0], len(shared), len(common), cells, MATCH, matched, DELTA, counts_all[0],
             ONE_SIDE, counts_all[1], STRATEGY, counts_all[2]))

    if args.out_prefix:
        out = args.out_prefix if args.out_prefix.endswith(".tsv") else args.out_prefix + ".tsv"
        if os.path.dirname(out):
            os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as fh:
            fh.write("\t".join(["key", "column", args.label_a, args.label_b, "verdict", "note"]) + "\n")
            for r in rows:
                fh.write("\t".join(str(x) for x in r) + "\n")
        print(f"wrote {len(rows)} notable cell(s) -> {out}")

    if args.json_out:
        artifact = {"tool": "table_diff", "argv": sys.argv[1:],
                    "inputs": {args.label_a: {"path": a.path, "bytes": a.bytes, "sha256_16": a.digest,
                                               "rows": len(a.rows), "columns": a.columns,
                                               "delimiter": a.delim, "duplicate_rows": a.dupe_rows,
                                               "extra_rows_per_key": lost_a,
                                               "ragged_rows": a.ragged},
                               args.label_b: {"path": b.path, "bytes": b.bytes, "sha256_16": b.digest,
                                              "rows": len(b.rows), "columns": b.columns,
                                              "delimiter": b.delim, "duplicate_rows": b.dupe_rows,
                                              "extra_rows_per_key": lost_b,
                                              "ragged_rows": b.ragged}},
                    "rule": {"key": key_names, "tol_rel": tol, "tol_abs": abs_tol,
                             "spec": args.spec.name if args.spec else None,
                             "columns": {c: (spec_cols.get(c, {}).get("kind") or "inferred")
                                         for c in common}},
                    "join": {"shared": len(shared), "a_only": len(ka) - len(shared),
                             "b_only": len(kb) - len(shared)},
                    "one_sided_columns": one_sided,
                    "counts": {"cells": cells, MATCH: matched, DELTA: counts_all[0],
                               ONE_SIDE: counts_all[1], STRATEGY: counts_all[2]},
                    "deltas": [{"key": r[0], "column": r[1], args.label_a: r[2],
                                args.label_b: r[3], "verdict": r[4], "note": r[5]}
                               for r in rows[:2000]],
                    "compared_something": compared}
        with open(args.json_out, "w") as fh:
            json.dump(artifact, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote artifact -> {args.json_out}")

    if not compared:
        return 2
    return 1 if deltas else 0


if __name__ == "__main__":
    sys.exit(main())
