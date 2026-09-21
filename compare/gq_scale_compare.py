#!/usr/bin/env python3
"""Compare FORMAT quality fields (GQ and the per-evidence *_GQ) between two VCFs.

Motivation: `work/compare_pesr/genotype_quality` reports one mean/median per side, and the
two pipelines do not use the same quality scale, so the summary tables are not directly comparable.
This script measures the *raw* distributions, per field, with the exact scale facts a reader needs:
where each field saturates, how much mass sits at the cap, and what the effective ceiling is.

    python compare/gq_scale_compare.py \
        work/outputs/baseline_step10/all_samples.genotyped_pesr.vcf.gz \
        work/outputs/all_samples.genotype_batch.pesr.vcf.gz \
        --fields GQ,RD_GQ,PE_GQ,SR_GQ --label-a baseline --label-b new

Parsing is plain text (one pass, per-record FORMAT index), not pysam per-sample access: 12.6M
cells per field. Values are bucketed into an integer histogram, so memory is O(1) and percentiles
come from the CDF. Missing ('.'/'./.') and negative values are counted separately, never averaged.
"""
from __future__ import annotations

import argparse
import gzip
import sys

import numpy as np

CAP = 200_000


def hist_file(path: str, fields: list) -> dict:
    out = {f: dict(hist=np.zeros(CAP, dtype=np.int64), missing=0, over_cap=0, n=0,
                   neg=0, frac=0.0, nonint=0) for f in fields}
    with gzip.open(path, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            keys = cols[8].split(":")
            idx = {k: i for i, k in enumerate(keys)}
            want = [(f, idx[f]) for f in fields if f in idx]
            for f, i in want:
                st = out[f]
                for s in cols[9:]:
                    parts = s.split(":")
                    if i >= len(parts):
                        st["missing"] += 1
                        continue
                    v = parts[i]
                    if v in (".", "", "./.", ".|.") or v.startswith(".:"):
                        st["missing"] += 1
                        continue
                    try:
                        x = float(v)
                    except ValueError:
                        st["nonint"] += 1
                        continue
                    st["n"] += 1
                    if x < 0:
                        st["neg"] += 1
                        continue
                    if float(v) != int(float(v)):
                        st["frac"] += 1
                    k = int(x)
                    if k >= CAP:
                        st["over_cap"] += 1
                        k = CAP - 1
                    st["hist"][k] += 1
            for f in fields:
                if f not in dict(want):
                    out[f]["missing"] += len(cols) - 9
    return out


def describe(st: dict) -> dict:
    h, n = st["hist"], st["n"]
    if not n:
        return {}
    cum = np.cumsum(h)
    q = lambda p: int(np.searchsorted(cum, p * n))  # noqa: E731
    mean = float((h * np.arange(CAP)).sum()) / n
    return dict(n=n, mean=mean, p05=q(0.05), p25=q(0.25), median=q(0.50), p75=q(0.75),
                p95=q(0.95), max=int(np.nonzero(h)[0][-1]),
                at_cap=float(h[99:].sum()) / n, exactly99=float(h[99].sum()) / n,
                zero=float(h[0].sum()) / n, missing=st["missing"],
                over_cap=st["over_cap"], fractional=int(st["frac"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("vcf_a")
    ap.add_argument("vcf_b")
    ap.add_argument("--fields", default="GQ,RD_GQ,PE_GQ,SR_GQ")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    a = ap.parse_args()
    fields = [f.strip() for f in a.fields.split(",") if f.strip()]
    res = {a.label_a: hist_file(a.vcf_a, fields), a.label_b: hist_file(a.vcf_b, fields)}
    for f in fields:
        print(f"\n## {f}")
        print(f"  {'side':12s} {'n':>10s} {'mean':>7s} {'p05':>5s} {'p25':>5s} {'med':>5s} "
              f"{'p75':>5s} {'p95':>5s} {'max':>6s} {'@99+':>7s} {'==99':>7s} {'zero':>7s} "
              f"{'missing':>9s} {'>cap':>5s} {'frac':>5s}")
        for side in (a.label_a, a.label_b):
            d = describe(res[side][f])
            if not d:
                print(f"  {side:12s} ABSENT from this VCF")
                continue
            print(f"  {side:12s} {d['n']:10d} {d['mean']:7.2f} {d['p05']:5d} {d['p25']:5d} "
                  f"{d['median']:5d} {d['p75']:5d} {d['p95']:5d} {d['max']:6d} "
                  f"{d['at_cap']:7.3%} {d['exactly99']:7.3%} {d['zero']:7.3%} "
                  f"{d['missing']:9d} {d['over_cap']:5d} {d['fractional']:5d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
