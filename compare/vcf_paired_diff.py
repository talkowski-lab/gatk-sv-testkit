#!/usr/bin/env python3
"""Paired diff of any VCF field on the SAME (site, sample) cells: the general form of two presets.

Why this exists
---------------
`gq_paired_compare.py` and `diff_rd_states.py` are the same tool twice, and each copy hardened a
different corner while leaving the others:

  * `gq_paired_compare.py` refuses (exit 2) when the two headers list the samples in a different
    ORDER, because it indexes cells by column position — while its sibling
    `pair_level_concordance.py` joins by NAME and takes the same input happily. Sample order is not
    under your control between pipeline versions, so one of the two is unusable on real pairs, and
    the failure reads as "these files cannot be compared".
  * it also folded duplicate VIDs together without counting them (a wrong number with no trace),
    hardcoded the cap at 99, and could not read an uncompressed VCF (`BadGzipFile` traceback).
  * `diff_rd_states.py` counts ambiguous keys properly but hardcodes the join key, the field
    (`RD_CN`) and the "state 1 or 3" question, so it cannot ask anything else.

This is the join and the accumulator, with the questions moved to the command line:

    # the GQ scale question, on files that arrived with different sample orders
    python compare/vcf_paired_diff.py base.pesr.vcf.gz new.pesr.vcf.gz --field FORMAT:GQ \
           --join vid --scale 99/999 --cap 99

    # the copy-state question is the same tool with different arguments
    python compare/vcf_paired_diff.py base.depth.vcf.gz new.depth.vcf.gz --join coord \
           --field FORMAT:RD_CN --by-sample

    # and something nobody has asked yet
    python compare/vcf_paired_diff.py a.vcf b.vcf --field FORMAT:EV --field INFO:SVLEN

Rule (fixed, so a rerun means the same thing):
  * samples line up **by name, never by position**; a header reordering changes no number here and
    is printed as a fact rather than an error. One-sided samples are counted and excluded.
  * `--join vid` uses the ID column; `--join coord` uses (CHROM,POS,END,SVLEN,SVTYPE). Which one is
    correct depends on the pair: variant resolution REWRITES IDs between versions (so a vid-join
    across versions compares nothing), while the coordinate/REF intersection *within* a version is
    near zero because GenotypeSVs resolves REF and writes BNDs. The tool prints how many keys
    matched and exits 2 when that number is zero — it never reports a rate over an empty join.
  * duplicate join keys are counted per side; the first record wins and you are told how much of
    the result that could affect.
  * `--scale num/den` multiplies side A; `--cap N` clamps AFTER scaling. When both are given, both
    variants (scaled, scaled-then-capped) are printed, because "is the scale right" and "does the
    cap bite" are different findings.
  * cells missing on either side are excluded and counted; no statistic here divides by cells it
    did not compare.
Exit: 0 compared and every field inside tolerance; 1 at least one field outside; 2 nothing compared.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import hashlib
import io
import json
import os
import sys

try:
    import numpy as np
except ImportError:                                     # pragma: no cover
    sys.exit("vcf_paired_diff.py needs numpy:  pip install -r requirements.txt (see docs/setup.md)")

MISSING_VALUES = {".", "", "./.", ".|.", "-"}
GZIP_MAGIC = b"\x1f\x8b"
FLUSH = 200_000                                          # cells buffered before one numpy array


def die(msg: str, code: int = 2) -> None:
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.exit(code)


def open_text(path: str):
    """Plain VCF or bgzf/gzip, decided from the bytes — not from an assumption buried in main()."""
    if not os.path.isfile(path):
        die(f"no such file: {path}\n"
            f"  baseline side: python terra/stage_inputs.py (docs/local-replay.md)\n"
            f"  new side:      terra/batch_fetch_compare.sh fetch (docs/terra-head-to-head.md)")
    with open(path, "rb") as raw:
        magic = raw.read(2)
    if magic == GZIP_MAGIC:
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace", newline="")
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


def parse_field(text: str):
    kind, _, name = text.rpartition(":")
    kind = (kind or "FORMAT").upper()
    if kind not in ("FORMAT", "INFO") or not name:
        die(f"--field {text!r}: expected TAG, FORMAT:TAG or INFO:TAG")
    return kind, name


def number(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def digest(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as raw:
        for chunk in iter(lambda: raw.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


class Field:
    """Accumulator for one field. Numeric cells become float32 chunks; anything else is counted.

    Buffering matters: appending one numpy array per cell (the obvious way to write this) allocates
    one object per cell, and a PESR pair is ~12 million cells per field.
    """

    def __init__(self, kind, name):
        self.kind, self.name = kind, name
        self.buf_a, self.buf_b, self.chunks_a, self.chunks_b = [], [], [], []
        self.drift = collections.Counter()
        self.missing_a = self.missing_b = 0
        self.text = 0
        self.mean_abs = self.exact = self.within = None
        self.per_sample = collections.defaultdict(lambda: [0, 0.0, 0])   # n, sum|diff|, exact
        self.n = 0

    def add(self, na, nb, sample=None, abs_diff=None):
        self.n += 1
        self.buf_a.append(na)
        self.buf_b.append(nb)
        self.drift[(na, nb)] += 1
        if sample is not None:
            cell = self.per_sample[sample]
            cell[0] += 1
            if na == nb:
                cell[2] += 1
            if abs_diff is not None:
                cell[1] += abs_diff

    def flush(self):
        if self.buf_a:
            self.chunks_a.append(np.asarray(self.buf_a, dtype=np.float32))
            self.chunks_b.append(np.asarray(self.buf_b, dtype=np.float32))
            self.buf_a, self.buf_b = [], []

    def arrays(self):
        self.flush()
        if not self.chunks_a:
            return None, None
        if len(self.chunks_a) == 1:
            return self.chunks_a[0], self.chunks_b[0]
        return np.concatenate(self.chunks_a), np.concatenate(self.chunks_b)


def read_records(path):
    """Yield (columns, INFO dict) for every data record of a plain or bgzf VCF."""
    with open_text(path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 9:
                continue
            info = {}
            for cell in cols[7].split(";"):
                name, _, value = cell.partition("=")
                info[name] = value
            yield cols, info


def key_of(cols, info, join):
    if join == "coord":
        return (cols[0], cols[1], info.get("END", cols[1]), info.get("SVLEN", "."),
                info.get("SVTYPE", "."))
    return cols[2] if cols[2] not in (".", "") else None


def header_samples(path):
    with open_text(path) as fh:
        for line in fh:
            if line.startswith("#CHROM"):
                return line.rstrip("\n").split("\t")[9:]
            if not line.startswith("#"):
                break
    return []


def scan(path, fields, join, label):
    """One pass: samples, record count, join keys (with duplicates counted), field coverage."""
    samples = header_samples(path)
    keys, records, no_key, dup = [], 0, 0, 0
    seen = set()
    present = collections.Counter()
    for cols, info in read_records(path):
        records += 1
        fkeys = cols[8].split(":")
        for kind, name in fields:
            if (kind == "INFO" and name in info) or (kind == "FORMAT" and name in fkeys):
                present[(kind, name)] += 1
        key = key_of(cols, info, join)
        if key is None:
            no_key += 1
            continue
        dup += 1 if key in seen else 0
        seen.add(key)
        keys.append(key)
    absent = [f"{k}:{n}" for (k, n) in fields if not present[(k, n)]]
    if absent:
        die(f"{label}: {path} carries none of {absent} on any of its {records:,} record(s), so that "
            f"field cannot be compared.\n"
            f"       check what the callset actually writes: zcat {path} 2>/dev/null | head -60", 2)
    if not samples and any(k == "FORMAT" for k, _ in fields):
        die(f"{label}: {path} declares no sample columns but FORMAT fields were requested")
    return samples, records, keys, dup, no_key, present


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compare VCF FORMAT/INFO fields on the cells two callsets share.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("vcf_a")
    ap.add_argument("vcf_b")
    ap.add_argument("--field", action="append", default=[], metavar="[FORMAT:|INFO:]TAG",
                    help="repeatable; a bare TAG means FORMAT:TAG")
    ap.add_argument("--join", choices=("vid", "coord"), default="vid")
    ap.add_argument("--scale", default=None, metavar="num/den",
                    help="multiply side A by num/den first, e.g. 99/999")
    ap.add_argument("--cap", type=float, default=None, help="clamp scaled A (NOT hardcoded at 99)")
    ap.add_argument("--tol", default="auto",
                    help="absolute tolerance counted as agreement. 'auto' (default) is 0.5 when both "
                         "sides hold only\nintegers (i.e. the same integer agrees) and 1e-6 otherwise "
                         "— an absolute 0.5 on a\n0-1 field such as PCC would call every cell an "
                         "agreement. The resolved value is printed.")
    ap.add_argument("--samples-file", help="one sample name per line; intersected with both headers")
    ap.add_argument("--by-sample", action="store_true", help="per-sample table: which sample moved?")
    ap.add_argument("--label-a", default="baseline")
    ap.add_argument("--label-b", default="new")
    ap.add_argument("--drift", type=int, default=8, help="top-N transitions/rows to print")
    ap.add_argument("--max-payload-mb", type=int, default=1500,
                    help="cap on buffered side-A payload; exceeding it exits 2 instead of OOM")
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    args = ap.parse_args()

    if not args.field:
        die("no --field given. Name at least one: --field FORMAT:GQ, --field INFO:SVLEN.\n"
            "       Deliberately no default: guessing the field is how a comparator answers a "
            "question\n       nobody asked.")
    specs = []
    for text in args.field:
        spec = parse_field(text)
        if spec not in specs:
            specs.append(spec)

    sA, nA, keysA, dupA, nokeyA, presentA = scan(args.vcf_a, specs, args.join, args.label_a)
    sB, nB, keysB, dupB, nokeyB, presentB = scan(args.vcf_b, specs, args.join, args.label_b)
    posA = {s: i for i, s in enumerate(sA)}
    posB = {s: i for i, s in enumerate(sB)}
    shared_samples = [s for s in sA if s in posB]
    if args.samples_file:
        if not os.path.isfile(args.samples_file):
            die(f"--samples-file {args.samples_file}: no such file")
        with open(args.samples_file) as fh:
            wanted = {l.strip() for l in fh if l.strip() and not l.startswith("#")}
        shared_samples = [s for s in shared_samples if s in wanted]
    # (index in A's columns, index in B's columns) per shared sample, in shared_samples order.
    # This is the whole fix for the positional-join defect: the pairing is built from NAMES once,
    # before a single cell is read, so a re-ordered header cannot shift a value into another sample.
    pairs = [(posA[s], posB[s]) for s in shared_samples]
    order_differs = [s for s in sA if s in posB] != [s for s in sB if s in posA]

    setA, setB = set(keysA), set(keysB)
    shared_keys = setA & setB
    print(f"# {args.label_a}: {args.vcf_a}  [{os.path.getsize(args.vcf_a):,} bytes, "
          f"sha256:{digest(args.vcf_a)}, {nA:,} records, {len(sA)} samples]")
    print(f"# {args.label_b}: {args.vcf_b}  [{os.path.getsize(args.vcf_b):,} bytes, "
          f"sha256:{digest(args.vcf_b)}, {nB:,} records, {len(sB)} samples]")
    print(f"# join --join {args.join}: shared keys {len(shared_keys):,} of "
          f"{args.label_a} {len(setA):,} / {args.label_b} {len(setB):,}")
    print(f"# samples joined BY NAME: {len(shared_samples)} "
          f"(only in {args.label_a} {len([s for s in sA if s not in posB])}, only in "
          f"{args.label_b} {len([s for s in sB if s not in posA])})"
          + ("; header order differs, which is not a difference" if order_differs else ""))
    if dupA or dupB:
        print(f"#   !! duplicate join keys: {args.label_a} {dupA:,}, {args.label_b} {dupB:,} — the "
              f"first record per key won")
    if nokeyA or nokeyB:
        print(f"#   !! records with no join key: {args.label_a} {nokeyA:,}, {args.label_b} {nokeyB:,}")
    if not shared_keys:
        hint = (f"       first {args.label_a} IDs: {keysA[:3]}\n"
                f"       first {args.label_b} IDs: {keysB[:3]}\n"
                f"       IDs are REWRITTEN between pipeline versions (RenameVariants) — try\n"
                f"       --join coord. Within one version an empty ID intersection means the two\n"
                f"       files are not the same cohort."
                if args.join == "vid" else
                f"       Contig naming is the usual cause (chr20 vs 20):\n"
                f"       {args.label_a} contigs: {sorted({k[0] for k in keysA[:200]})[:8]}\n"
                f"       {args.label_b} contigs: {sorted({k[0] for k in keysB[:200]})[:8]}")
        die(f"FATAL: 0 shared {'IDs' if args.join == 'vid' else 'coordinate keys'}, so nothing was "
            f"compared.\n{hint}", 2)
    if not shared_samples and any(k == "FORMAT" for k, _ in specs):
        die(f"FATAL: the two files share no sample names, so no FORMAT cell can be paired.\n"
            f"       {args.label_a}: {sA[:5]}\n       {args.label_b}: {sB[:5]}", 2)

    # Buffer side A's requested payload (tab-joined text) for the shared keys only, then stream B
    # against it: memory is bounded by what was asked for, not by the file.
    payload = {}
    for cols, info in read_records(args.vcf_a):
        key = key_of(cols, info, args.join)
        if key is None or key not in shared_keys or key in payload:
            continue
        fkeys = cols[8].split(":")
        cells = []
        for kind, name in specs:
            if kind == "INFO":
                cells.append(info.get(name, "."))
            elif name in fkeys:
                idx = fkeys.index(name)
                parts = [s.split(":") for s in cols[9:]]
                cells.append("\t".join(p[idx] if idx < len(p) else "." for p in parts))
            else:
                cells.append("\t".join(["."] * (len(cols) - 9)))
        payload[key] = cells
    used = sum(len(c) + sum(len(x) for x in c) for c in payload.values())
    if used > args.max_payload_mb * 1024 * 1024:
        die(f"side-A payload for {len(shared_keys):,} shared key(s) is {used / 1e6:,.0f} MB, over "
            f"--max-payload-mb {args.max_payload_mb}.\n"
            f"       Narrow it: --samples-file with the samples you care about, or fewer --field.", 2)

    factor = 1.0
    if args.scale:
        num, _, den = args.scale.partition("/")
        factor = float(num) / float(den or 1)

    fields = {spec: Field(*spec) for spec in specs}
    visited = 0
    dup_skipped = 0
    consumed = set()                 # keys already paired with a B record
    for cols, info in read_records(args.vcf_b):
        key = key_of(cols, info, args.join)
        if key is None or key not in payload:
            continue
        if key in consumed:
            # A second B record with a key A also has: pairing it against A's FIRST record would
            # compare two different variants and report their gap as a difference (it did, on the
            # fixture: 3 bogus cells). Counted and skipped, never folded in.
            dup_skipped += 1
            continue
        consumed.add(key)
        visited += 1
        stored = payload[key]
        fkeys = cols[8].split(":")
        bparts = [s.split(":") for s in cols[9:]]
        for slot, spec in enumerate(specs):
            field = fields[spec]
            kind, name = spec
            if kind == "INFO":
                va, vb = stored[slot], info.get(name, ".")
                if va in MISSING_VALUES or vb in MISSING_VALUES:
                    field.missing_a += va in MISSING_VALUES
                    field.missing_b += vb in MISSING_VALUES
                    continue
                na, nb = number(va), number(vb)
                if na is None or nb is None:
                    field.text += 1
                    field.drift[(va, vb)] += 1
                else:
                    field.add(na, nb)
                continue
            if name in fkeys:
                idx = fkeys.index(name)
                bvals = [p[idx] if idx < len(p) else "." for p in bparts]
            else:
                bvals = ["."] * len(bparts)
            avals = stored[slot].split("\t")
            for pos, (ai, bi) in enumerate(pairs):
                va = avals[ai] if ai < len(avals) else "."
                vb = bvals[bi] if bi < len(bvals) else "."
                ma, mb = va in MISSING_VALUES, vb in MISSING_VALUES
                if ma or mb:
                    field.missing_a += ma
                    field.missing_b += mb
                    continue
                na, nb = number(va), number(vb)
                if na is None or nb is None:
                    field.text += 1
                    field.drift[(va, vb)] += 1
                    continue
                field.add(na, nb, shared_samples[pos], abs(nb - na * factor))
            if len(field.buf_a) >= FLUSH:
                field.flush()

    verdicts, json_fields, any_compared = {}, {}, False
    for spec in specs:
        field = fields[spec]
        label = f"{spec[0]}:{spec[1]}"
        print(f"\n## {label}  (present on {args.label_a} {presentA[spec]:,} record(s), "
              f"{args.label_b} {presentB[spec]:,})")
        if field.text:
            total = sum(field.drift.values())
            print(f"  categorical: {total:,} paired value(s), {len(field.drift):,} distinct "
                  f"transition(s)")
            for (va, vb), count in field.drift.most_common(args.drift):
                print(f"    {str(va)[:26]:>26s} -> {str(vb)[:26]:<26s} {count:8,d}  ({count / total:.6f})")
            verdicts[label] = "MATCH" if len(field.drift) <= 1 else "DELTA"
            json_fields[label] = {"kind": "categorical", "cells": total,
                                  "transitions": {f"{a} -> {b}": c for (a, b), c in
                                                  field.drift.most_common(200)}}
            any_compared = True
            if not field.n:
                continue
            print(f"  ({field.n:,} of those cells were numeric too; the numeric block below is "
                  f"still printed)")
        xa, xb = field.arrays()
        if xa is None:
            print(f"  NOTHING COMPARABLE: missing on {args.label_a} {field.missing_a:,}, on "
                  f"{args.label_b} {field.missing_b:,}")
            verdicts[label] = "NOTHING"
            continue
        any_compared = True
        n = xa.size
        if str(args.tol).lower() == "auto":
            integral = bool(np.all(xa == np.rint(xa)) and np.all(xb == np.rint(xb)))
            tol = 0.5 if integral else 1e-6
            print(f"  {'tolerance':>30s}: {tol:g} — "
                  + ("values are integers on both sides, so 'the same integer' is the test"
                     if integral else
                     "values are NOT integers here, so an absolute 0.5 would have called every "
                     "cell an agreement"))
        else:
            tol = float(args.tol)
            print(f"  {'tolerance':>30s}: {tol:g} (given)")
        scaled = xa * factor
        variants = [(f"scaled x{factor:.5f}", scaled)] if args.scale else [("raw", xa)]
        if args.cap is not None:
            variants.append((f"scaled then capped at {args.cap:g}", np.minimum(scaled, args.cap)))
        within_best = 0.0
        for suffix, sx in variants:
            d = np.abs(xb - sx)
            within = float(np.mean(d <= tol))
            corr = (float(np.corrcoef(xb, sx)[0, 1]) if sx.std() > 0 and xb.std() > 0
                    else float("nan"))
            print(f"  {suffix:>30s}: mean|diff| {d.mean():.4g}  exact {float(np.mean(d == 0.0)):.4%}"
                  f"  within {tol:g} {within:.4%}  corr {corr:.5f}  {args.label_b} higher "
                  f"{float(np.mean(xb > sx + tol)):.3%}  lower "
                  f"{float(np.mean(xb < sx - tol)):.3%}")
            within_best = max(within_best, within)
            if suffix == "raw" or suffix.startswith("scaled x") or suffix.startswith("scaled then"):
                field.mean_abs, field.exact, field.within = float(d.mean()), float(np.mean(d == 0.0)), within
        print(f"  {'marginals':>30s}: n {n:,}  {args.label_a} mean {xa.mean():.4g} median "
              f"{np.median(xa):.4g} p05 {np.percentile(xa, 5):.4g} p95 {np.percentile(xa, 95):.4g}"
              f"  {args.label_b} mean {xb.mean():.4g} median {np.median(xb):.4g} max {xb.max():.4g}")
        if args.cap is not None:
            over = scaled > args.cap
            share = float(np.mean(over))
            at_cap = float(np.mean(xb[over] == args.cap)) if over.any() else float("nan")
            print(f"  {'cap ' + format(args.cap, 'g'):>30s}: scaled {args.label_a} exceeds it on "
                  f"{share:.4%} of cells; of those, {args.label_b} equals the cap {at_cap:.4%}")
        if len(field.drift) <= 12:
            print(f"  value transitions (top {args.drift}):")
            for (va, vb), count in field.drift.most_common(args.drift):
                print(f"    {format(va, 'g'):>8s} -> {format(vb, 'g'):<8s} {count:8,d}  ({count / n:.6f})")
        rows = []
        if args.by_sample and spec[0] == "FORMAT":
            rows = sorted(((v[1] / v[0], v[0], v[2] / v[0], s) for s, v in field.per_sample.items()
                           if v[0]), reverse=True)
            if rows:
                print("  per-sample, worst first (which sample moved?):")
                for mean_abs, cnt, exact_rate, s in rows[:args.drift]:
                    print(f"    {s:<12s} cells {cnt:8,d}  mean|diff| {mean_abs:<10.4g} "
                          f"exact {exact_rate:.4%}")
                if len(rows) > args.drift:
                    print(f"    ... {len(rows) - args.drift} more sample(s), all in --json")
        verdicts[label] = "MATCH" if within_best == 1.0 else "DELTA"
        json_fields[label] = {"kind": "numeric", "cells": int(n), "missing_a": field.missing_a,
                              "missing_b": field.missing_b, "mean_abs_diff": field.mean_abs,
                              "exact": field.exact, "within_tol": field.within, "tol": tol,
                              "tol_given": args.tol,
                              "scale": args.scale, "cap": args.cap, "mean_a": float(xa.mean()),
                              "mean_b": float(xb.mean()),
                              "transitions": {f"{a:g} -> {b:g}": c for (a, b), c in
                                              field.drift.most_common(200)}
                              if len(field.drift) <= 500 else None,
                              "per_sample": [{"sample": s, "cells": c, "mean_abs_diff": m,
                                              "exact": e} for m, c, e, s in rows] or None}

    print("\n== SUMMARY  compared %s shared record(s); %d sample column(s) paired by name | %s"
          % (f"{visited:,}", len(shared_samples), "  ".join(f"{k}={v}" for k, v in verdicts.items())))
    if dup_skipped:
        print(f"   !! {dup_skipped:,} record(s) on {args.label_b} repeated a key already paired, so "
              f"they were NOT compared (pairing them against {args.label_a}'s first record would "
              f"have compared two different variants and called the gap a difference)")
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"tool": "vcf_paired_diff", "argv": sys.argv[1:],
                       "inputs": {args.label_a: {"path": args.vcf_a, "records": nA,
                                                 "sha256_16": digest(args.vcf_a)},
                                  args.label_b: {"path": args.vcf_b, "records": nB,
                                                 "sha256_16": digest(args.vcf_b)}},
                       "rule": {"join": args.join, "scale": args.scale, "cap": args.cap,
                                "tol": args.tol, "fields": [f"{k}:{n}" for k, n in specs]},
                       "join_stats": {"shared_keys": len(shared_keys), "records_visited": visited,
                                      "duplicate_records_skipped_on_b": dup_skipped,
                                      "samples_compared": len(shared_samples),
                                      "duplicate_keys_a": dupA, "duplicate_keys_b": dupB,
                                      "no_key_records_a": nokeyA, "no_key_records_b": nokeyB},
                       "fields": json_fields, "verdicts": verdicts,
                       "compared_something": any_compared}, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote artifact -> {args.json_out}")

    if not any_compared:
        return 2
    return 0 if all(v == "MATCH" for v in verdicts.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
