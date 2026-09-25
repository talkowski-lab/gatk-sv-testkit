#!/usr/bin/env python3
"""Site-weighted summary of a gatk-sv-profile paired run.

`gatk-sv-profile run --vcf-a ... --vcf-b ...` writes per-module bucketed tables under
<out>/<module>/tables/*.tsv.gz (+ .parquet twins). Nothing in the tool prints a single
headline number, so any figure quoted from it needs ONE written-down aggregation
rule rather than per-session improvisation. That rule is:

  * restrict to rows with n_sites > 0 (bucketed tables are sparse and mostly empty)
  * weight each bucket's value by its site count
  * **each side is filtered and weighted by its OWN counts** -- filtering A then weighting B with the
    same mask silently drops B-only buckets (a real bug: it made `pct_matched B->A` come out 0.9840
    instead of the table-consistent 0.9836, and inflated A->B coverage of the B side)
  * report both directions (A-side weights = n_sites_<label-a>, B-side = n_sites_<label-b>)
  * NaN means "metric undefined for that bucket" -> dropped, and the drop is counted

KNOWN LIMITATION (upstream, not a bug here): `gatk-sv-profile` explodes each site once per algorithm
(`dimensions.py explode_algorithm_buckets`), so summing a bucket table counts multi-caller sites more
than once -- on head-to-head PESR the B-side sums to 78,467 over 75,209 records (+3,258 `manta,wham`). The
effect on the headline is measurable and tiny (genotype exact match 0.990600 bucket-weighted vs
0.990512 pair-level, +0.009 pp), but for "what fraction of baseline sites came back" use the
pair/matcher count, not this table: the tool reports three mutually inconsistent numbers for that one
quantity (matcher pairs 73,884 / `status==TP` 75,639 / exact shared VID 74,241).

NB `gatk-sv-profile` **embeds the label values in its column names**
(`--label-a baseline` -> `n_sites_baseline`, `pct_matched_baseline`, ...), so the
column suffixes here are derived from the labels, not hardcoded. Labels containing characters that
pandas would mangle are not supported; pass the same labels used for the profile run.

Validated by a self-test: a callset compared against ITSELF must give 1.0000 everywhere. Run it
that way first on any new machine — anything less than 1.0 is this script or the input, not the
pipeline.

Usage:
    python compare/profile_summarize.py /tmp/profile_selftest
    ... profile_summarize.py DIR --label-a baseline --label-b new

Exit status: 0 on success, 2 if a required table is missing.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

# metric table -> (relative path, value columns, weight columns, rate columns of interest)
TABLES = {
    "genotype_concordance": "genotype_concordance/tables/concordance_metrics.tsv.gz",
    "genotype_exact_match": "genotype_exact_match/tables/genotype_match_rates.tsv.gz",
    "site_overlap": "site_overlap/tables/overlap_metrics.tsv.gz",
}


def wavg(values, weights):
    """Site-weighted mean over non-NaN pairs; returns (mean, n_used_rows, n_dropped_nan)."""
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    ok = ~np.isnan(values)
    if not ok.any():
        return None, 0, int((~ok).sum())
    return float(np.average(values[ok], weights=weights[ok])), int(ok.sum()), int((~ok).sum())


def col(df, base, label, where):
    """Resolve a label-suffixed column, failing loudly with the real column list."""
    name = f"{base}_{label}"
    if name not in df.columns:
        raise SystemExit(f"ERROR: column {name!r} absent from {where}.\n"
                         f"       actual columns: {list(df.columns)}\n"
                         f"       -> pass --label-a/--label-b matching the profile run's labels")
    return df[name]


def summarize(outdir, label_a, label_b):
    missing = [k for k, rel in TABLES.items() if not os.path.isfile(os.path.join(outdir, rel))]
    if missing:
        print(f"ERROR: missing table(s) for {missing} under {outdir}", file=sys.stderr)
        print("Expected a completed `gatk-sv-profile run` output dir.", file=sys.stderr)
        return 2

    print(f"# site-weighted summary of {outdir}")
    print(f"#   A = {label_a}   B = {label_b}")
    print("#   rule: rows with n_sites>0 only; weight = that row's site count\n")

    c = pd.read_csv(os.path.join(outdir, TABLES["genotype_concordance"]), sep="\t")
    print(f"## genotype_concordance ({len(c)} bucket rows read; each side weighted on its own n_sites)")
    for metric in sorted(c.metric.unique()):
        s = c[c.metric == metric]
        sa = s[col(s, "n_sites", label_a, metric) > 0]
        sb = s[col(s, "n_sites", label_b, metric) > 0]
        a, na, da = wavg(col(sa, "mean_value", label_a, metric), col(sa, "n_sites", label_a, metric))
        b, nb, db = wavg(col(sb, "mean_value", label_b, metric), col(sb, "n_sites", label_b, metric))
        fmt = lambda v: "  n/a " if v is None else f"{v:.4f}"
        print(f"  {metric:32s} A-side {fmt(a)} ({na:4d} rows, {da:4d} NaN dropped)"
              f"   B-side {fmt(b)} ({nb:4d} rows, {db:4d} NaN dropped)")
    # A-side weights answer "of the baseline's calls, how much agrees", and vice versa.
    print("  (A-side = fraction of baseline genotype calls agreeing; B-side = same from the new side)")

    e = pd.read_csv(os.path.join(outdir, TABLES["genotype_exact_match"]), sep="\t")
    e = e[e.n_sites > 0]
    print(f"\n## genotype_exact_match ({len(e)} non-empty rows; excludes no-call cells)")
    for rate_col, nice in [
        ("mean_exact_match_rate", "both-called exact match"),
        ("mean_het_to_homref_rate", "drift het -> homref"),
        ("mean_het_to_homalt_rate", "drift het -> homalt"),
        ("mean_homref_to_het_rate", "drift homref -> het"),
        ("mean_homalt_to_het_rate", "drift homalt -> het"),
    ]:
        # NaN here means the transition never occurred in that bucket, i.e. rate 0.
        d = int(e[rate_col].isna().sum())
        filled = e[rate_col].fillna(0.0)
        fv = float(np.average(filled, weights=e.n_sites))
        print(f"  {nice:24s} {fv:.6f}   (NaN -> 0 assumed; {d} of {len(e)} rows NaN)")

    o = pd.read_csv(os.path.join(outdir, TABLES["site_overlap"]), sep="\t")
    oa = o[col(o, "n_total", label_a, "site_overlap") > 0]
    ob = o[col(o, "n_total", label_b, "site_overlap") > 0]
    a, _, _ = wavg(col(oa, "pct_matched", label_a, "site_overlap"), col(oa, "n_total", label_a, "site_overlap"))
    b, _, _ = wavg(col(ob, "pct_matched", label_b, "site_overlap"), col(ob, "n_total", label_b, "site_overlap"))
    print(f"\n## site_overlap (A-side {len(oa)} / B-side {len(ob)} non-empty rows)")
    # sum-ratio cross-check: the bucket table double-counts multi-algorithm sites
    sa, sb_ = int(col(o, "n_total", label_a, "site_overlap").sum()), int(col(o, "n_total", label_b, "site_overlap").sum())
    ma, mb = int(col(o, "n_matched", label_a, "site_overlap").sum()), int(col(o, "n_matched", label_b, "site_overlap").sum())
    print(f"  pct_matched A->B {a:.4f}   B->A {b:.4f}")
    print(f"  sum-ratio cross-check  A->B {ma/sa:.4f} ({ma}/{sa})   B->A {mb/sb_:.4f} ({mb}/{sb_})"
          f"   [n_total sums exceed record counts when a site carries >1 algorithm]")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("outdir", help="gatk-sv-profile output directory")
    p.add_argument("--label-a", default="baseA")
    p.add_argument("--label-b", default="baseB")
    args = p.parse_args()
    return summarize(os.path.abspath(args.outdir), args.label_a, args.label_b)


if __name__ == "__main__":
    sys.exit(main())
