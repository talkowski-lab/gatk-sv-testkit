#!/usr/bin/env python3
"""Independent, VCF-level genotype concordance (no gatk-sv-profile, no bucketing).

gatk-sv-profile gives the *publishable* numbers, but its tables are bucketed (a site
picked up by N callers is counted N times in the row sums) and its site matcher is fuzzy.
So any exact headline figure quoted from it needs the aggregation rule written down, or
it cannot be reproduced. This script is that rule, implemented: no profiler, no
bucketing, one pass, deterministic, and it prints the inputs it used.

    python compare/pair_level_concordance.py \
        work/outputs/baseline_step10/all_samples.genotyped_pesr.vcf.gz \
        work/outputs/all_samples.genotype_batch.pesr.vcf.gz \
        --label-a baseline --label-b new

Rule (fixed, so a rerun means the same thing):
  * sites are keyed by the VCF ID field (VID). (CHROM,POS,REF,ALT) intersections are ~0 between
    the two pipelines because GenotypeSVs resolves REF and writes breakend BNDs, so a
    coordinate/REF-based intersection lands near zero and looks like total disagreement.
  * a (site, sample) pair is "both-called" iff BOTH sides have a non-no-call GT.
  * exact match compares GT allele *sets* (unphased), so 0|1 == 1|0.
  * report: shared VIDs, both-called pairs, exact match, and the drift matrix.
Expected for the 2026-09-15 head-to-head PESR pair (measured): 74,241 shared VIDs, exact 0.990512.
"""
from __future__ import annotations

import argparse
import collections
import sys

import pysam


def gts(path: str) -> tuple:
    """vid -> {sample: genotype-string-or-None}, plus the sample order from the header."""
    vcf = pysam.VariantFile(path)
    samples = list(vcf.header.samples)
    out = {}
    dupes = 0
    for rec in vcf:
        vid = rec.id
        if vid is None:
            continue
        row = {}
        for s in samples:
            try:
                gt = rec.samples[s]["GT"]
            except KeyError:
                row[s] = None
                continue
            if gt is None or gt == () or all(a is None for a in gt):
                row[s] = None
            else:
                row[s] = "|".join(str(a) for a in gt)
        if vid in out:
            dupes += 1
        out[vid] = row
    vcf.close()
    return out, samples, dupes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("vcf_a")
    ap.add_argument("vcf_b")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--code", action="store_true", help="also print the full GT drift matrix")
    a = ap.parse_args()

    A, sA, dupA = gts(a.vcf_a)
    B, sB, dupB = gts(a.vcf_b)
    print(f"# {a.label_a}: {len(A)} sites with VID ({dupA} duplicate VIDs) | {len(sA)} samples")
    print(f"# {a.label_b}: {len(B)} sites with VID ({dupB} duplicate VIDs) | {len(sB)} samples")
    if set(sA) != set(sB):
        print(f"  !! sample sets differ: only-A {sorted(set(sA) - set(sB))[:5]} "
              f"only-B {sorted(set(sB) - set(sA))[:5]}")
        return 2
    shared = sorted(set(A) & set(B))
    print(f"# shared VIDs {len(shared)}  "
          f"recovery {a.label_a}->{a.label_b} {len(shared) / len(A):.4f}  "
          f"{a.label_b}->{a.label_a} {len(shared) / len(B):.4f}")

    both = exact = 0
    drift = collections.Counter()
    for vid in shared:
        ra, rb = A[vid], B[vid]
        for s in sA:
            ga, gb = ra.get(s), rb.get(s)
            if ga is None or gb is None:
                continue
            both += 1
            if sorted(ga.split("|")) == sorted(gb.split("|")):
                exact += 1
            else:
                drift[(ga, gb)] += 1
    print(f"# both-called pairs {both} | exact match {exact / both:.6f}" if both else "# no both-called pairs")
    top = drift.most_common(8 if not a.code else 100)
    for (ga, gb), n in top:
        print(f"    drift {ga:>5s} -> {gb:<5s} {n:8d}  ({n / both:.6f})")
    print(f"# (total discordant pairs {sum(drift.values())}, "
          f"{sum(drift.values()) / both:.6f} of both-called)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
