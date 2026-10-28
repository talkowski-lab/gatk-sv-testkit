#!/usr/bin/env python3
"""Independent, VCF-level genotype concordance (no gatk-sv-profile, no bucketing).

Unlike gatk-sv-profile (bucketed tables, fuzzy site matching), this counts each site once with
exact VID matching, in one deterministic pass, and prints the inputs it used.

    python compare/pair_level_concordance.py \
        work/outputs/baseline_step10/all_samples.genotyped_pesr.vcf.gz \
        work/outputs/all_samples.genotype_batch.pesr.vcf.gz \
        --label-a baseline --label-b new

Rule:
  * sites are keyed by the VCF ID field (VID); GenotypeSVs resolves REF and writes breakend BNDs,
    so a (CHROM,POS,REF,ALT) intersection between the two pipelines is near zero.
  * a (site, sample) pair is "both-called" iff BOTH sides have a non-no-call GT.
  * exact match compares GT allele *sets* (unphased), so 0|1 == 1|0.
  * report: shared VIDs, both-called pairs, exact match, and the drift matrix.
"""
from __future__ import annotations

import argparse
import collections
import sys

import pysam

import artifact


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
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    a = ap.parse_args()

    A, sA, dupA = gts(a.vcf_a)
    B, sB, dupB = gts(a.vcf_b)
    print(f"# {a.label_a}: {len(A)} sites with VID ({dupA} duplicate VIDs) | {len(sA)} samples")
    print(f"# {a.label_b}: {len(B)} sites with VID ({dupB} duplicate VIDs) | {len(sB)} samples")
    rule = {"join": "VCF ID (VID); (CHROM,POS,REF,ALT) intersects near zero across these "
                   "pipelines because GenotypeSVs resolves REF and writes BNDs",
            "both_called": "both sides have a non-no-call GT",
            "match": "GT allele SETS compared unphased, so 0|1 == 1|0"}
    inputs = {**artifact.input_file(a.label_a, a.vcf_a, sites=len(A), duplicate_vids=dupA),
              **artifact.input_file(a.label_b, a.vcf_b, sites=len(B), duplicate_vids=dupB)}
    if set(sA) != set(sB):
        print(f"  !! sample sets differ: only-A {sorted(set(sA) - set(sB))[:5]} "
              f"only-B {sorted(set(sB) - set(sA))[:5]}")
        artifact.write(a.json_out, artifact.envelope(
            "pair_level_concordance", inputs, rule,
            {"reason": "the two files share no usable sample set", "samples_a": len(sA),
             "samples_b": len(sB)}, verdict="NOTHING_COMPARED", compared_something=False))
        return 2
    shared = sorted(set(A) & set(B))
    rec_ab = f"{len(shared) / len(A):.4f}" if A else "n/a"
    rec_ba = f"{len(shared) / len(B):.4f}" if B else "n/a"
    print(f"# shared VIDs {len(shared)}  "
          f"recovery {a.label_a}->{a.label_b} {rec_ab}  {a.label_b}->{a.label_a} {rec_ba}")

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
    if both:
        print(f"# (total discordant pairs {sum(drift.values())}, "
              f"{sum(drift.values()) / both:.6f} of both-called)")
    artifact.write(a.json_out, artifact.envelope(
        "pair_level_concordance", inputs, rule,
        {"shared_vids": len(shared), "both_called_pairs": both, "exact_match": exact,
         "exact_match_rate": exact / both if both else None,
         "recovery": {f"{a.label_a}->{a.label_b}": len(shared) / len(A) if A else None,
                      f"{a.label_b}->{a.label_a}": len(shared) / len(B) if B else None},
         "samples": len(sA), "duplicate_vids": {a.label_a: dupA, a.label_b: dupB},
         "drift": {f"{ga} -> {gb}": n for (ga, gb), n in drift.most_common(200)}},
        verdict="MEASURED — pass/fail is the caller's call",
        compared_something=bool(both)))
    return 0 if both else 2


if __name__ == "__main__":
    sys.exit(main())
