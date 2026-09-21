#!/usr/bin/env python
"""Recompute the SR training population two ways from real staged evidence, to measure why
`sd_het`/`median_hom` differ between v1.1.1 and the Java genotyper.

Both paths gate training samples on two-sided support; they differ in three measurable ways:

  1. gate value      v1.1: `awk '$NF > sr_count/2'`, sr_count = convert_poisson_p(SR_sum_log_pval)
                     Java: `Math.max(trainingCountCutoff / 2, 1)` with **integer** division, and
                     `trainingCountCutoff` comes from SRQ, which is GATK QUAL (-10log10 p)
  2. first-pass cutoff v1.1: `median + 1.645*mad(d)` (R `mad` carries 1.4826)
                     Java: `hetMedian + 1.645*hetMad` (no 1.4826) -> weaker hom filter
  3. hom class       v1.1: copy state 0 or 4; Java keeps copy state >= 4

`sd_het = 1.645 * 1.4826 * rawMAD` is an exact port in both, so only the population can explain the
gap. Tthe captured script rebuilds per-(site@sample) normalized two-sided counts from the staged batch
evidence and reports median / rawMAD / sd_het / median_hom under each recipe.

Copy states come from the v1.1.1 genotyped depth VCF (`RD_CN`), matched to SR sites **by coordinate
overlap** — v1.1.1 joined them by cluster VID, and SeparateDepthPesr renamed them, so the faithful
equivalent is the interval match; the match rate is reported so the number is auditable.

    python examples/recompute_het_population.py --region chr20:5000000-15000000
"""
import argparse
import collections
import json
import math
import os
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
import config  # noqa: E402

STAGE = str(config.work_dir("staging"))
REPORTS = str(config.work_dir("reports"))

SR_FILE = os.path.join(STAGE, "all_samples.sr.txt.gz")
MEDIAN_FILE = os.path.join(STAGE, "all_samples_medianCov.transposed.bed")
DEPTH_VCF = os.path.join(STAGE, "all_samples.genotyped_depth.vcf.gz")
PESR_VCF = os.path.join(STAGE, "all_samples.filtered_pesr_merged.vcf.gz")
CUTOFFS = os.path.join(STAGE, "all_samples.cutoffs")


def sh(cmd, allow_fail=False):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0 and not allow_fail:
        sys.stderr.write((r.stderr or "")[:600])
        raise RuntimeError(cmd)
    return r.stdout


def raw_mad(xs):
    m = statistics.median(xs)
    return statistics.median([abs(x - m) for x in xs])


def mad_14826(xs):
    """R's mad(): 1.4826 * median(|x - median(x)|)."""
    return 1.4826 * raw_mad(xs)


def poisson_mean_from_minuslog10p(p):
    """v1.1 `convert_poisson_p.py`: the SR/PE log-p metric is -log10(e^-k) = k/log(10), so the
    count cutoff is k = p * ln(10). Check against AoU: 3.4743 * ln(10) = 8.0 = v1.1's sr_count, and
    SRQ 25.94 QUAL (= 2.594 in v1.1 units) * ln(10) = 5.97 -> Java's integer sr_count 5."""
    return p * math.log(10.0)


def load_cutoffs(path):
    out = {}
    with open(path) as fh:
        hdr = fh.readline().rstrip("\n").split("\t")
        col = {k: i for i, k in enumerate(hdr)}
        for line in fh:
            f = line.rstrip("\n").split("\t")
            try:
                out.setdefault(f[col["metric"]], float(f[col["cutoff"]]))
            except (KeyError, IndexError, ValueError):
                continue
    return out


def load_median_cov(path):
    """medianCov.transposed.bed: chrom start end + one median per sample; header row names samples."""
    with open(path) as fh:
        hdr = fh.readline().rstrip("\n").split("\t")
        samples = hdr[3:]
        tot = collections.Counter()
        n = collections.Counter()
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) < 3 + len(samples):
                continue
            for i, s in enumerate(samples):
                try:
                    tot[s] += float(f[3 + i])
                    n[s] += 1
                except ValueError:
                    continue
    med = {s: (tot[s] / n[s] if n[s] else 0.0) for s in samples}
    return samples, med


def load_depth_records(region):
    """Depth genotypes as intervals: (chrom, start, end, svtype, {sample: RD_CN})."""
    samples = sh(f"bcftools query -l {DEPTH_VCF}").split()
    fmt = "%CHROM\t%POS\t%INFO/END\t%INFO/SVTYPE\t[%RD_CN\t]\n"
    cmd = f"bcftools query -f '{fmt}' {DEPTH_VCF} -r {region} 2>/dev/null"
    recs = collections.defaultdict(list)
    for line in sh(cmd, allow_fail=True).splitlines():
        f = line.split("\t")
        if len(f) < 4 + len(samples):
            continue
        try:
            start, end = int(f[1]), int(f[2])
        except ValueError:
            continue
        cn = {}
        for i, s in enumerate(samples):
            try:
                cn[s] = int(f[4 + i])
            except (ValueError, IndexError):
                continue
        recs[f[0]].append((start, end, f[3], cn))
    for c in recs:
        recs[c].sort()
    return samples, dict(recs)


def match_copy_states(recs, chrom, start, end, svtype):
    """Best-overlap depth record for an SR site: >=50% reciprocal overlap, same SVTYPE preferred."""
    cand = recs.get(chrom, [])
    best, best_ov = None, 0.0
    a_len = max(1, end - start)
    for (ds, de, dsv, cn) in cand:
        if de < start - 1000 or ds > end + 1000:
            continue
        ov = min(end, de) - max(start, ds)
        if ov <= 0:
            continue
        frac = ov / min(a_len, max(1, de - ds))
        prefer = 1.1 if dsv == svtype else 1.0
        if frac * prefer > best_ov and frac >= 0.5:
            best, best_ov = cn, frac * prefer
    return best


def sr_sites(region):
    """Sites with SR in INFO/EVIDENCE (v1.1's `pass.srtest.txt`)."""
    fmt = "%CHROM\t%POS\t%ID\t%INFO/SVTYPE\t%INFO/END\t%INFO/EVIDENCE\n"
    cmd = f"bcftools query -f '{fmt}' {PESR_VCF} -r {region} 2>/dev/null"
    out = []
    for line in sh(cmd, allow_fail=True).splitlines():
        f = line.split("\t")
        if len(f) < 6 or "SR" not in f[5]:
            continue
        try:
            pos = int(f[1])
            end = int(f[4]) if f[4].isdigit() else pos
        except ValueError:
            continue
        out.append((f[0], pos, max(pos, end), f[2] or f"{f[0]}_{f[1]}_{f[3]}", f[3]))
    return out


def side_counts(chrom, start, end, pad):
    """{sample: (start_norm_count, end_norm_count)} from the tidy SR file.

    Tidy SR rows are `chrom pos side count sample`; evidence is attributed to a variant side by
    proximity (window `pad` around the breakpoint), which is how `svtk count-sr` and Java's
    SplitReadEvidenceAggregator both decide start- vs end-side support."""
    res = collections.defaultdict(lambda: [0.0, 0.0])
    for idx, pos in enumerate((start, end)):
        q = f"tabix {SR_FILE} {chrom}:{max(1, pos - pad)}-{pos + pad} 2>/dev/null"
        for line in sh(q, allow_fail=True).splitlines():
            f = line.split("\t")
            if len(f) < 5:
                continue
            try:
                cnt = float(f[3])
            except ValueError:
                continue
            res[f[4]][idx] += cnt
    return res


def summarize(label, gated, rdcn, use_14826, hom_states, dump=None):
    het, hom = [], []
    for vid, counts in gated.items():
        cn = rdcn.get(vid)
        if not cn:
            continue
        for sample, total in counts.items():
            state = cn.get(sample)
            if state is None:
                continue
            if state in (1, 3):
                het.append(total)
            elif state in hom_states:
                hom.append(total)
    out = {"recipe": label, "variants_with_support": len(gated),
           "het_n": len(het), "hom_n_before_filter": len(hom)}
    if len(het) < 10:
        out["error"] = "het population too small"
        return out
    hm = statistics.median(het)
    rmad = raw_mad(het)
    het_cutoff = hm + 1.645 * (mad_14826(het) if use_14826 else rmad)
    out.update(het_median=round(hm, 4), het_raw_mad=round(rmad, 4),
               sd_het=round(1.645 * 1.4826 * rmad, 4),
               first_pass_het_cutoff=round(het_cutoff, 4))
    kept = [c for c in hom if c >= het_cutoff]
    out["hom_n_after_filter"] = len(kept)
    if kept:
        out["median_hom"] = round(statistics.median(kept), 4)
    if dump:
        with open(dump, "w") as fh:
            fh.write("\n".join(str(x) for x in sorted(het)) + "\n")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="chr20:5000000-15000000")
    ap.add_argument("--pad", type=int, default=20)
    ap.add_argument("--target-cov", type=float, default=60.0)
    ap.add_argument("--max-sites", type=int, default=500)
    ap.add_argument("--gates", default="", help="comma-separated extra gate thresholds to sweep")
    ap.add_argument("--dump-prefix", default=None, help="write het populations here for auditing")
    ap.add_argument("--out", default=os.path.join(REPORTS, "het_population.json"))
    args = ap.parse_args()

    os.makedirs(REPORTS, exist_ok=True)
    _samples, med = load_median_cov(MEDIAN_FILE)
    _dsamples, depth = load_depth_records(args.region)
    sites = sr_sites(args.region)[: args.max_sites]
    cuts = load_cutoffs(CUTOFFS)
    p = cuts.get("SR_sum_log_pval")
    if p is None:
        sys.exit("no SR_sum_log_pval in staged cutoffs")

    k = poisson_mean_from_minuslog10p(p)          # v1.1 sr_count (e.g. 8)
    k_qual = poisson_mean_from_minuslog10p(p)     # same p; Java's SRQ is 10*p, i.e. same -log10 p
    thr_v111 = k / 2.0                            # awk float division
    thr_java = max(int(k_qual // 2), 1)           # Java integer division (comment in the code flags this)

    print(f"region={args.region}  SR-evidence sites={len(sites)}  "
          f"depth records={sum(len(v) for v in depth.values())}  median-cov samples={len(med)}")
    print(f"SR_sum_log_pval={p} -> sr_count={k:.3f}")
    print(f"  v1.1 gate: > {thr_v111:.3f} (float sr_count/2)")
    print(f"  Java gate: > {thr_java} (integer division; sr_count {k_qual:.3f} -> {int(k_qual//2)})")

    gated = collections.defaultdict(dict)
    gate_names = [("v111", thr_v111), ("java", thr_java)]
    for g in [x for x in args.gates.split(",") if x]:
        gate_names.append((f"gate{g}", float(g)))
    rdcn = {}
    matched = 0
    for chrom, pos, end, vid, svtype in sites:
        cn = match_copy_states(depth, chrom, pos, end, svtype)
        if cn is None:
            continue
        matched += 1
        rdcn[vid] = cn
        raw = side_counts(chrom, pos, end, args.pad)
        norm = {}
        for s, (l, r) in raw.items():
            cov = med.get(s) or 0.0
            if cov > 0:
                norm[s] = (round(args.target_cov * l / cov), round(args.target_cov * r / cov))
        for name, thr in gate_names:
            keep = {s: (l + r) for s, (l, r) in norm.items() if l > thr and r > thr}
            if keep:
                gated[name][vid] = keep

    print(f"sites matched to a depth genotype: {matched}/{len(sites)}")
    rows = [
        summarize("v1.1 as-coded: gate > sr_count/2, hetCutoff w/ 1.4826, hom={0,4}",
                  gated["v111"], rdcn, True, {0, 4},
                  dump=(args.dump_prefix + ".v111_het.txt" if args.dump_prefix else None)),
        summarize("Java as-coded: gate > int(sr_count/2), hetCutoff w/o 1.4826, hom={0,4}",
                  gated["java"], rdcn, False, {0, 4},
                  dump=(args.dump_prefix + ".java_het.txt" if args.dump_prefix else None)),
    ]
    for name, _thr in gate_names[2:]:
        rows.append(summarize(f"gate sweep > {name[4:]}: 1.4826 in hetCutoff (v1.1 filter)",
                             gated[name], rdcn, True, {0, 4}))
        rows.append(summarize(f"gate sweep > {name[4:]}: no 1.4826 in hetCutoff (Java filter)",
                             gated[name], rdcn, False, {0, 4}))
    print()
    for r in rows:
        print(json.dumps(r, sort_keys=True))
    with open(args.out, "w") as fh:
        json.dump({"region": args.region, "pad": args.pad, "target_cov": args.target_cov,
                   "sr_sum_log_pval": p, "sr_count": k, "gate_v111": thr_v111,
                   "gate_java": thr_java, "sites": len(sites), "sites_matched_to_depth": matched,
                   "rows": rows}, fh, indent=1)
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
