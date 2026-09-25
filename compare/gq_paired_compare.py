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
"""
from __future__ import annotations

import argparse
import gzip
import sys

import numpy as np


def load(path: str, field: str) -> tuple:
    samples, out = None, {}
    with gzip.open(path, "rt") as fh:
        for line in fh:
            if line.startswith("##"):
                continue
            if line.startswith("#CHROM"):
                samples = line.rstrip("\n").split("\t")[9:]
                continue
            cols = line.rstrip("\n").split("\t")
            keys = cols[8].split(":")
            if field not in keys:
                continue
            i = keys.index(field)
            vals = np.fromiter(((float(s.split(":")[i]) if i < len(s.split(":")) and
                                 s.split(":")[i] not in (".", "") else np.nan)
                                for s in cols[9:]), dtype=np.float32, count=len(cols) - 9)
            out[cols[2]] = vals
    return samples, out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("vcf_a")
    ap.add_argument("vcf_b")
    ap.add_argument("--field", default="GQ")
    ap.add_argument("--scale", default="99/999",
                    help="factor applied to A to put it on B's scale, e.g. 99/999")
    ap.add_argument("--label-a", default="baseline")
    ap.add_argument("--label-b", default="new")
    a = ap.parse_args()
    num, den = (float(x) for x in a.scale.split("/"))
    k = num / den

    sA, A = load(a.vcf_a, a.field)
    sB, B = load(a.vcf_b, a.field)
    shared = sorted(set(A) & set(B))
    print(f"# {a.field}: {a.label_a} {len(A)} sites, {a.label_b} {len(B)} sites, "
          f"shared VIDs {len(shared)}; samples {len(sA)}/{len(sB)} "
          f"{'same order' if sA == sB else 'DIFFERENT ORDER'}")
    if not shared or sA != sB:
        return 2

    x = np.stack([A[v] for v in shared])
    y = np.stack([B[v] for v in shared])
    ok = ~(np.isnan(x) | np.isnan(y))
    xa, yb = x[ok], y[ok]
    print(f"# paired cells {ok.sum()} of {x.size} ({ok.mean():.4%}); "
          f"A missing {int(np.isnan(x).sum())}, B missing {int(np.isnan(y).sum())}")
    print(f"# {a.label_a}: mean {xa.mean():.2f} median {np.median(xa):.1f} "
          f"p05 {np.percentile(xa,5):.0f} p95 {np.percentile(xa,95):.0f} max {xa.max():.0f}")
    print(f"# {a.label_b}: mean {yb.mean():.2f} median {np.median(yb):.1f} "
          f"p05 {np.percentile(yb,5):.0f} p95 {np.percentile(yb,95):.0f} max {yb.max():.0f}")
    scaled = np.round(xa * k)
    clipped = np.minimum(scaled, 99.0)
    for name, s in (("raw scaled", scaled), ("scaled then capped at 99", clipped)):
        d = np.abs(yb - s)
        print(f"# A {name} (x{k:.5f}) vs B:  mean|diff| {d.mean():.3f}  "
              f"exact {np.mean(d < 0.5):.4%}  within 1 {np.mean(d <= 1.0):.4%}  "
              f"within 2 {np.mean(d <= 2.0):.4%}  corr {np.corrcoef(yb, s)[0,1]:.5f}  "
              f"B higher {np.mean(yb > s + 0.5):.3%}  B lower {np.mean(yb < s - 0.5):.3%}")
    print(f"# cells where A/10.09 would exceed 99 (i.e. the cap bites): "
          f"{np.mean(scaled > 99.0):.4%}; of those B == 99: "
          f"{np.mean(yb[scaled > 99.0] == 99.0):.4%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
