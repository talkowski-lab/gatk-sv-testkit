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

Two defects were fixed rather than left for `compare/vcf_paired_diff.py` to paper over:

  * **fractional fields came out as zero.** The histogram bins integers, so a Float FORMAT field
    (PCC, an AF, anything on 0-1) landed entirely in bin 0 and printed `mean 0.00, p05..p95 0,
    zero 100 %`, with only the `frac` column hinting that every value had been fractional. Each
    field is now classified from a bounded look-ahead over the first `--probe` records: integral
    fields still bin at 1 (unchanged numbers), fractional fields bin at `--float-scale` (default
    1000), and the binning resolution is printed as part of the result. A statistic whose precision
    is a property of the tool belongs in the output, not in the source.
  * **the cap was hardcoded at 99.** `--cap` now names it (default 99, which is GATK's truncation
    and what v1.1.1 saturates at), and it is applied in the field's own units, so a field on a
    0-999 scale or a 0-1 scale gets a meaningful "at cap" instead of a silent 99.
  * an uncompressed VCF raised `gzip.BadGzipFile: Not a gzipped file (b'##')`; the file type is read
    from the bytes now.
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
import sys

import numpy as np

import artifact

CAP = 200_000                       # histogram bins; with --float-scale 1000 this covers 0..200


def open_text(path: str):
    if not os.path.isfile(path):
        sys.exit(f"no such file: {path}")
    with open(path, "rb") as raw:
        magic = raw.read(2)
    if magic == b"\x1f\x8b":
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace", newline="")
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


def probe_scales(path: str, fields: list, probe: int, float_scale: int) -> dict:
    """Classify each field from the first `probe` records: integer -> bin 1, fractional -> float_scale.

    A bounded look-ahead rather than a mid-stream rescale: the binning must be fixed before the one
    pass begins, and which side of that line a field falls on is a property of the file, not a guess.
    """
    seen = {f: False for f in fields}
    fractional = set()
    with open_text(path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            idx = {k: i for i, k in enumerate(cols[8].split(":"))}
            for f in fields:
                if f not in idx:
                    continue
                seen[f] = True
                for s in cols[9:]:
                    parts = s.split(":")
                    if idx[f] < len(parts) and ("." in parts[idx[f]] or "e" in parts[idx[f]].lower()):
                        fractional.add(f)
            if all(seen.values()) and len(fractional) == len(seen):
                break
            probe -= 1
            if probe <= 0:
                break
    return {f: (float_scale if f in fractional else 1) for f in fields}


def hist_file(path: str, fields: list, scales: dict) -> dict:
    out = {f: dict(hist=np.zeros(CAP, dtype=np.int64), missing=0, over_cap=0, n=0,
                   neg=0, frac=0.0, nonint=0) for f in fields}
    with open_text(path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            keys = cols[8].split(":")
            idx = {k: i for i, k in enumerate(keys)}
            want = [(f, idx[f]) for f in fields if f in idx]
            for f, i in want:
                st = out[f]
                scale = scales[f]
                for s in cols[9:]:
                    parts = s.split(":")
                    if i >= len(parts):
                        st["missing"] += 1
                        continue
                    v = parts[i]
                    if v in (".", "", "./.", ".|.", ".:") or v.startswith(".:"):
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
                    k = int(round(x * scale))
                    if k >= CAP:
                        st["over_cap"] += 1
                        k = CAP - 1
                    st["hist"][k] += 1
            for f in fields:
                if f not in dict(want):
                    out[f]["missing"] += len(cols) - 9
    return out


def describe(st: dict, scale: int, cap: float) -> dict:
    h, n = st["hist"], st["n"]
    if not n:
        return {}
    cum = np.cumsum(h)

    def q(pct):                                   # returns the value in the FIELD's own units
        return int(np.searchsorted(cum, pct * n)) / scale

    mean = float((h * np.arange(CAP)).sum()) / n / scale
    cap_bin = int(round(cap * scale))
    at_cap = float(h[cap_bin:].sum()) / n if 0 < cap_bin < CAP else 0.0
    exactly_cap = float(h[cap_bin].sum()) / n if 0 < cap_bin < CAP else 0.0
    nonzero = np.nonzero(h)[0]
    return dict(n=n, mean=mean, p05=q(0.05), p25=q(0.25), median=q(0.50), p75=q(0.75),
                p95=q(0.95), max=int(nonzero[-1]) / scale if len(nonzero) else 0.0,
                at_cap=at_cap, exactly_cap=exactly_cap, zero=float(h[0].sum()) / n,
                missing=st["missing"], over_cap=st["over_cap"], fractional=int(st["frac"]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("vcf_a")
    ap.add_argument("vcf_b")
    ap.add_argument("--fields", default="GQ,RD_GQ,PE_GQ,SR_GQ")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--cap", type=float, default=99.0,
                    help="where the field is truncated, in the FIELD's units (default 99)")
    ap.add_argument("--float-scale", type=int, default=1000,
                    help="binning resolution for fractional fields (default 1000 = 0.001)")
    ap.add_argument("--probe", type=int, default=2000,
                    help="records inspected to classify a field as integer or fractional")
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    a = ap.parse_args()
    fields = [f.strip() for f in a.fields.split(",") if f.strip()]
    for path in (a.vcf_a, a.vcf_b):
        if not os.path.isfile(path):
            sys.exit(f"no such file: {path}")
    scales = probe_scales(a.vcf_a, fields, a.probe, a.float_scale)
    scales_b = probe_scales(a.vcf_b, fields, a.probe, a.float_scale)
    # One field, one binning: if EITHER side is fractional, both must be binned the same way or the
    # two distributions are not measured on the same ruler.
    scales = {f: max(scales[f], scales_b[f]) for f in fields}
    print("# binning: " + ", ".join(f"{f}=1/{scales[f]:g}" for f in fields)
          + "   (a fractional field is binned at 1/%g so its percentiles mean something; the cap is "
            "read in the field's own units, --cap %g)" % (a.float_scale, a.cap))
    res = {a.label_a: hist_file(a.vcf_a, fields, scales), a.label_b: hist_file(a.vcf_b, fields, scales)}
    metrics = {}
    for f in fields:
        print(f"\n## {f}   (bin 1/{scales[f]:g}, cap {a.cap:g})")
        print(f"  {'side':12s} {'n':>10s} {'mean':>8s} {'p05':>7s} {'p25':>7s} {'med':>7s} "
              f"{'p75':>7s} {'p95':>7s} {'max':>8s} {'@cap+':>7s} {'==cap':>7s} {'zero':>7s} "
              f"{'missing':>9s} {'>cap':>5s} {'frac':>5s}")
        metrics[f] = {"bin_scale": scales[f], "cap": a.cap, "sides": {}}
        for side in (a.label_a, a.label_b):
            d = describe(res[side][f], scales[f], a.cap)
            if not d:
                print(f"  {side:12s} ABSENT from this VCF")
                continue
            print(f"  {side:12s} {d['n']:10d} {d['mean']:8.4g} {d['p05']:7.4g} {d['p25']:7.4g} "
                  f"{d['median']:7.4g} {d['p75']:7.4g} {d['p95']:7.4g} {d['max']:8.4g} "
                  f"{d['at_cap']:7.3%} {d['exactly_cap']:7.3%} {d['zero']:7.3%} "
                  f"{d['missing']:9d} {d['over_cap']:5d} {d['fractional']:5d}")
            metrics[f]["sides"][side] = d
    compared = any(metrics[f]["sides"] for f in fields)
    artifact.write(a.json_out, artifact.envelope(
        "gq_scale_compare",
        {**artifact.input_file(a.label_a, a.vcf_a), **artifact.input_file(a.label_b, a.vcf_b)},
        {"fields": fields, "bin_scale": scales, "cap": a.cap, "probe_records": a.probe,
         "note": "histogram over the whole file; percentiles from the CDF, so they are exact at the "
                 "stated bin resolution"},
        {"fields": metrics},
        verdict="MEASURED marginals only — this tool does not join sites, so it cannot say whether "
                "the same site changed",
        compared_something=compared))
    return 0 if compared else 2


if __name__ == "__main__":
    sys.exit(main())
