#!/usr/bin/env python3
"""Paired, site-restricted genotype-quality comparison of two pipelines' FORMAT fields.

`compare/gq_scale_compare.py` measures each side's marginals; this script answers the sharper
question, "on the SAME (site, sample) pairs, is the quality field the same number or just a
different scale?" - which matters because `GenotypeSVs.rescaleGq` (GenotypeSVs.java:724-728)
multiplies the internal quality by 99/maxQual with maxQual=999 (GenotypeSVs.java:260), i.e. it
divides by 10.09 and GATK then truncates at 99. v1.1.1 writes the internal 0-999 number directly.

    python compare/gq_paired_compare.py \
        work/outputs/baseline_step10/all_samples.genotyped_depth.vcf.gz \
        work/outputs/all_samples.genotype_batch.depth.vcf.gz \
        --field GQ --scale 99/999

Sites are joined by VID. Only (site, sample) pairs present on both sides are compared.

This is one of the two presets for the general tool `compare/vcf_paired_diff.py` (that tool takes
the field, the key and the scale as arguments; this file keeps the rule fixed, which is the point).
Three defects were fixed here rather than only in the new tool, because this file is still the one
people run:
  * samples used to be matched by COLUMN POSITION, so two headers listing the same samples in a
    different order produced `exit 2` — while the sibling `pair_level_concordance.py` handled the
    same pair. Samples are now matched by NAME and a re-ordered header is reported, not fatal.
  * duplicate VIDs used to be folded together silently (last writer wins, no count). They are now
    counted and printed, and the first record wins.
  * an uncompressed VCF raised `gzip.BadGzipFile`; the file type is read from the bytes now.
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
import sys

import numpy as np

import artifact


def open_text(path: str):
    """Plain or gz/bgzf, decided from the bytes: a traceback is not a comparison."""
    if not os.path.isfile(path):
        sys.exit(f"no such file: {path}")
    with open(path, "rb") as raw:
        magic = raw.read(2)
    if magic == b"\x1f\x8b":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace", newline="")
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


def load(path: str, field: str):
    """vid -> per-sample values (in THIS file's column order), the sample list, dup/missing counts."""
    samples, out = None, {}
    dupes = no_field = records = 0
    with open_text(path) as fh:
        for line in fh:
            if line.startswith("##"):
                continue
            if line.startswith("#CHROM"):
                samples = line.rstrip("\n").split("\t")[9:]
                continue
            cols = line.rstrip("\n").split("\t")
            keys = cols[8].split(":")
            if field not in keys:
                no_field += 1
                continue
            records += 1
            i = keys.index(field)
            vals = np.fromiter(((float(s.split(":")[i]) if i < len(s.split(":")) and
                                 s.split(":")[i] not in (".", "") else np.nan)
                                for s in cols[9:]), dtype=np.float32, count=len(cols) - 9)
            if cols[2] in out:
                dupes += 1                  # counted; the first record keeps the site
                continue
            out[cols[2]] = vals
    return samples, out, dupes, no_field, records


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("vcf_a")
    ap.add_argument("vcf_b")
    ap.add_argument("--field", default="GQ")
    ap.add_argument("--scale", default="99/999",
                    help="factor applied to A to put it on B's scale, e.g. 99/999")
    ap.add_argument("--cap", type=float, default=99.0,
                    help="truncation applied to scaled A (default 99 = GATK's; pass 1e9 to disable)")
    ap.add_argument("--label-a", default="baseline")
    ap.add_argument("--label-b", default="new")
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    a = ap.parse_args()
    num, den = (float(x) for x in a.scale.split("/"))
    k = num / den

    sA, A, dupA, nofieldA, recA = load(a.vcf_a, a.field)
    sB, B, dupB, nofieldB, recB = load(a.vcf_b, a.field)
    shared = sorted(set(A) & set(B))
    common_samples = [s for s in (sA or []) if s in (sB or [])]
    print(f"# {a.field}: {a.label_a} {len(A)} sites ({dupA} duplicate VIDs), {a.label_b} {len(B)} "
          f"sites ({dupB} duplicate VIDs), shared VIDs {len(shared)}; samples {len(sA or [])}/"
          f"{len(sB or [])}, {len(common_samples)} matched BY NAME")
    if dupA or dupB:
        print(f"#   !! duplicate VIDs on {a.label_a} {dupA}, on {a.label_b} {dupB}: the first record "
              f"per VID won")
    posA = {s: i for i, s in enumerate(sA or [])}
    posB = {s: i for i, s in enumerate(sB or [])}
    ia = [posA[s] for s in common_samples]
    ib = [posB[s] for s in common_samples]
    if [s for s in (sA or []) if s in posB] != [s for s in (sB or []) if s in posA]:
        print("#   (the two headers list the shared samples in a different ORDER — irrelevant here, "
              "cells are paired by name)")
    if not shared or not common_samples:
        print(f"# NOTHING COMPARED: shared VIDs {len(shared)}, shared sample names "
              f"{len(common_samples)}")
        artifact.write(a.json_out, artifact.envelope(
            "gq_paired_compare",
            {**artifact.input_file(a.label_a, a.vcf_a, records=recA),
             **artifact.input_file(a.label_b, a.vcf_b, records=recB)},
            {"join": "VID", "samples": "by name", "field": a.field, "scale": a.scale, "cap": a.cap},
            {"shared_vids": len(shared), "samples_matched": len(common_samples),
             "duplicate_vids": {a.label_a: dupA, a.label_b: dupB}},
            verdict="NOTHING_COMPARED", compared_something=False))
        return 2

    x = np.stack([A[v][ia] for v in shared])
    y = np.stack([B[v][ib] for v in shared])
    ok = ~(np.isnan(x) | np.isnan(y))
    xa, yb = x[ok], y[ok]
    print(f"# paired cells {ok.sum()} of {x.size} ({ok.mean():.4%}); "
          f"A missing {int(np.isnan(x).sum())}, B missing {int(np.isnan(y).sum())}")
    print(f"# {a.label_a}: mean {xa.mean():.2f} median {np.median(xa):.1f} "
          f"p05 {np.percentile(xa,5):.0f} p95 {np.percentile(xa,95):.0f} max {xa.max():.0f}")
    print(f"# {a.label_b}: mean {yb.mean():.2f} median {np.median(yb):.1f} "
          f"p05 {np.percentile(yb,5):.0f} p95 {np.percentile(yb,95):.0f} max {yb.max():.0f}")
    scaled = np.round(xa * k)
    clipped = np.minimum(scaled, a.cap)
    metrics = {}
    for name, s in (("raw scaled", scaled),
                    (f"scaled then capped at {a.cap:g}", clipped)):
        d = np.abs(yb - s)
        row = {"mean_abs_diff": float(d.mean()), "exact": float(np.mean(d < 0.5)),
               "within_1": float(np.mean(d <= 1.0)), "within_2": float(np.mean(d <= 2.0)),
               "corr": float(np.corrcoef(yb, s)[0, 1]),
               "b_higher": float(np.mean(yb > s + 0.5)), "b_lower": float(np.mean(yb < s - 0.5))}
        metrics[name] = row
        print(f"# A {name} (x{k:.5f}) vs B:  mean|diff| {row['mean_abs_diff']:.3f}  "
              f"exact {row['exact']:.4%}  within 1 {row['within_1']:.4%}  "
              f"within 2 {row['within_2']:.4%}  corr {row['corr']:.5f}  "
              f"B higher {row['b_higher']:.3%}  B lower {row['b_lower']:.3%}")
    over = float(np.mean(scaled > a.cap))
    if over:
        print(f"# cells where scaled {a.label_a} exceeds {a.cap:g} (the cap bites): {over:.4%}; "
              f"of those B == {a.cap:g}: {np.mean(yb[scaled > a.cap] == a.cap):.4%}")
    else:
        print(f"# cells where scaled {a.label_a} exceeds {a.cap:g}: 0.0000% (the cap never bites)")
    if nofieldA or nofieldB:
        print(f"#   !! records without a {a.field} FORMAT tag: {a.label_a} {nofieldA}, "
              f"{a.label_b} {nofieldB} (those records cannot contribute cells)")
    artifact.write(a.json_out, artifact.envelope(
        "gq_paired_compare",
        {**artifact.input_file(a.label_a, a.vcf_a, records=recA, sites=len(A)),
         **artifact.input_file(a.label_b, a.vcf_b, records=recB, sites=len(B))},
        {"join": "VID", "samples": "by name", "field": a.field, "scale": a.scale,
         "scale_factor": k, "cap": a.cap},
        {"paired_cells": int(ok.sum()), "cells_total": int(x.size),
         "missing_cells": {a.label_a: int(np.isnan(x).sum()), a.label_b: int(np.isnan(y).sum())},
         "duplicate_vids": {a.label_a: dupA, a.label_b: dupB},
         "shared_vids": len(shared), "samples_matched": len(common_samples),
         "cap_bites_fraction": over, "variants": metrics},
        verdict="MEASURED (no pass/fail judgement is made here)",
        compared_something=bool(ok.sum())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
