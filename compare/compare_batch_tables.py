#!/usr/bin/env python3
"""Table differ for the GATK-SV v1.1.1 vs new-pipeline genotyping head-to-head.

Baseline (v1.1.1) inputs, discovered by name *suffix* so any batch prefix works:
  <batch>.sr_metric_file.txt / <batch>.pe_metric_file.txt  key<TAB>value: sr_count|pe_count,
      median_hom, sd_het, (SR only) rare_min/rare_max/common_min/common_max and the frequency
      cutoffs rare_single/rare_both/common_single/common_both
  <batch>.depth.depth_sepcutoff.txt, <batch>.pesr.pesr_sepcutoff.txt  copy_state/mean/sd/cutoffs
  GenotypeBatch.<batch>.metrics.tsv  key<TAB>value genotype-step tallies
  <batch>.cutoffs  RF cutoffs (PE_log_pval / SR_sum_log_pval / RD_Median_Separation rows)
New-pipeline inputs: <batch>.sr_geno_params.tsv (15 columns, adds rare/common pass+fail tallies),
  <batch>.pe_geno_params.tsv, <batch>.rd_depth_geno_params.tsv, <batch>.rd_pesr_geno_params.tsv,
  <batch>.sr_cutoff_diagnostics.txt (## RUN_CONFIGURATION: sr/pe quality cutoff, min separations)
Verdicts: MATCH within tolerance, DELTA outside it, MISSING when either side lacks file/parameter,
STRATEGY for the SR frequency cutoffs and for SR_sum_log_pval->SRQ / PE_log_pval->PEQ: v1.1.1
fitted loose-single/strict-both frequency cutoffs on purpose, and the new *Q metrics are GATK QUAL
= -10*log10(p) against v1.1.1's -log10(p) (a 10x scale change on top of the rename), so those rows
are behaviour/scale changes and are never diffed as bare numbers -- the tool prints
"SR_sum_log_pval*10 == SRQ? <bool>" instead. Tolerances: exact integer counts/tallies, 1e-9
relative for RD mean/sd/cutoffs (~14-16 s.f. prints; last-digit float noise is known), 1e-4
absolute for sd_het / median_hom. Missing files yield MISSING rows; the tool never crashes.

Example: python3 compare/compare_batch_tables.py --baseline-dir work/staging \
    --new-dir work/runs/train_definitive --out-prefix /tmp/tbl
"""

import argparse
import fnmatch
from pathlib import Path

EXACT, REL, ABS, STRATEGY, SCALE = "exact", "rel", "abs", "strategy", "scale"
REL_TOL, ABS_TOL, QC_TOL = 1e-9, 1e-4, 1e-4  # QC_TOL = print precision of the diagnostics file
HEADER = ["surface", "param", "baseline", "new", "abs_diff", "rel_pct", "verdict"]
PATTERNS = {  # role -> (baseline globs, new globs); each list tried in order, first hit wins
    "sr_params": (["*.sr_metric_file.txt"], ["*.sr_geno_params.tsv"]),
    "pe_params": (["*.pe_metric_file.txt"], ["*.pe_geno_params.tsv"]),
    "rd_depth": (["*.depth.depth_sepcutoff.txt", "*.depth_sepcutoff.txt"], ["*.rd_depth_geno_params.tsv"]),
    "rd_pesr": (["*.pesr.pesr_sepcutoff.txt", "*.pesr_sepcutoff.txt"], ["*.rd_pesr_geno_params.tsv"]),
    "batch_metrics": (["GenotypeBatch.*.metrics.tsv", "*.metrics.tsv"], ["GenotypeBatch.*.metrics.tsv", "*.metrics.tsv"]),
    "rf_cutoffs": (["*.cutoffs"], ["*.cutoffs"]),
    "sr_diagnostics": (["*sr_cutoff_diagnostics.txt"], ["*sr_cutoff_diagnostics.txt"]),
}
READERS = {"sr_params": ("kv", "columnar"), "pe_params": ("kv", "columnar"), "rd_depth": ("states",) * 2,
           "rd_pesr": ("states",) * 2, "batch_metrics": ("kv",) * 2, "rf_cutoffs": ("cutoffs",) * 2,
           "sr_diagnostics": ("kv",) * 2}
SR_SPECS = [("sr_count", EXACT), ("median_hom", ABS), ("sd_het", ABS), ("rare_min", EXACT), ("rare_max", EXACT),
            ("common_min", EXACT), ("common_max", EXACT), ("rare_pass", EXACT), ("rare_fail", EXACT),
            ("common_pass", EXACT), ("common_fail", EXACT), ("rare_single", STRATEGY), ("rare_both", STRATEGY),
            ("common_single", STRATEGY), ("common_both", STRATEGY)]
PE_SPECS = [("pe_count", EXACT), ("median_hom", ABS), ("sd_het", ABS)]
SEPARATIONS = ("pesr_min_separation", "depth_min_separation")  # baseline key = "selected_" + these
RENAMES = {"SR_sum_log_pval": ("SRQ", "sr_quality_cutoff"), "PE_log_pval": ("PEQ", "pe_quality_cutoff")}
SCALE_NOTE = ("NOTE: SRQ/PEQ are GATK QUAL = -10*log10(p); v1.1.1 SR_sum_log_pval/PE_log_pval are -log10(p), "
              "i.e. a 10x scale change on top of the rename -- never diffed as raw numbers")


def num(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def gfmt(x):
    return "-" if x is None else "%.10g" % x


def parse_table(path, kind):
    """Read one file into {param: raw value}; kind = kv | columnar | states | cutoffs."""
    rows = [l.split("\t") for l in path.read_text(errors="replace").splitlines() if l.strip()]
    if kind == "kv":  # key<TAB>value, section markers skipped
        return {f[0].strip(): f[1].strip() for f in rows if len(f) >= 2 and f[0][:1] not in ("#", "[")}
    if kind == "columnar":  # header + a single data row
        return dict(zip(rows[0], rows[1])) if len(rows) > 1 else {}
    if kind == "states":  # copy_state/mean/sd/cutoffs -> '<state>.<column>'
        return {"%s.%s" % (f[0].strip(), name): f[i] for f in rows[1:] if f[0].strip().isdigit()
                for i, name in enumerate(rows[0][1:], 1) if i < len(f)}
    col = {name: i for i, name in enumerate(rows[0])} if rows else {}  # RF cutoffs table
    out, depth = {}, []
    if any(k not in col for k in ("metric", "cutoff", "algtype", "svtype", "min_svsize")):
        return out  # not a recognisable cutoffs table: report everything MISSING rather than crash
    for f in rows[1:]:
        if len(f) <= max(col.values()) or f[0].startswith("#"):
            continue
        metric, cutoff = f[col["metric"]], f[col["cutoff"]]
        out.setdefault(metric, cutoff)
        out["%s[%s/%s]" % (metric, f[col["algtype"]], f[col["svtype"]])] = cutoff
        if "median_separation" in metric.lower():  # the value v1.1.1's TrainRDGenotyping selects
            if f[col["algtype"]] == "PESR" and num(f[col["min_svsize"]]) == 1000:
                out.setdefault("selected_pesr_min_separation", cutoff)
            elif f[col["algtype"]] == "Depth":
                depth.append((num(cutoff), cutoff))
    if depth:
        out["selected_depth_min_separation"] = max(depth)[1]  # max over rows, like v1.1.1's sort -nr
    return out


def row(surface, param, kind, b_raw, n_raw):
    b, n, diff, rel = num(b_raw), num(n_raw), "-", "-"
    if b_raw is None or n_raw is None:
        verdict = "MISSING"
    elif b is None or n is None:  # non-numeric values: exact text comparison
        verdict = "MATCH" if b_raw == n_raw else "DELTA"
    elif kind == SCALE:  # definition/scale change: no numeric diff is meaningful
        verdict = "STRATEGY"
    else:
        d, scale = abs(b - n), max(abs(b), abs(n))
        ok = b == n if kind == EXACT else d <= (max(REL_TOL * scale, 1e-12) if kind == REL else ABS_TOL)
        verdict = "STRATEGY" if kind == STRATEGY else ("MATCH" if ok else "DELTA")
        diff, rel = gfmt(d), gfmt(100.0 * d / scale if scale else 0.0)
    return [surface, param, b_raw or "-", n_raw or "-", diff, rel, verdict]


def compare_params(surface, role, specs, side_b, side_n):
    if side_b.get(role) is None or side_n.get(role) is None:
        return [row(surface, "%s: no %s file" % (role, "baseline" if side_b.get(role) is None else "new"),
                    ABS, None, None)]
    return [row(surface, p, kind, side_b[role].get(p), side_n[role].get(p)) for p, kind in specs]


def rd_specs(role, side_b, side_n):
    keys = set(side_b.get(role) or {}) | set(side_n.get(role) or {})
    return [(k, REL) for k in sorted(keys, key=lambda k: (num(k.split(".")[0]) if k[:1].isdigit() else -1.0, k))]


def compare_metrics(side_b, side_n):
    if side_b.get("batch_metrics") is None or side_n.get("batch_metrics") is None:
        return compare_params("batch_metrics", "batch_metrics", [], side_b, side_n)
    b, n = side_b["batch_metrics"], side_n["batch_metrics"]
    rows = []
    for key in sorted(set(b) | set(n)):
        pair = [num(x) for x in (b.get(key), n.get(key))]
        rows.append(row("batch_metrics", key, EXACT if all(x is not None and x.is_integer() for x in pair) else REL,
                        b.get(key), n.get(key)))
    return rows


def compare_cutoffs(side_b, side_n):
    """RD min-separation rows (REL) + the SRQ/PEQ scale-change note: returns (rows, notes)."""
    notes, b, conf = [SCALE_NOTE], side_b.get("rf_cutoffs") or {}, side_n.get("sr_diagnostics") or {}
    if side_b.get("rf_cutoffs") is None and side_n.get("sr_diagnostics") is None:
        return [row("rd_separation", "rf_cutoffs/sr_diagnostics: no baseline file", REL, None, None)], notes
    rows = [row("rd_separation", "%s (v1.1.1 TrainRDGenotyping selection)" % key, REL,
                b.get("selected_" + key), conf.get(key))
            for key in SEPARATIONS if b.get("selected_" + key) or conf.get(key)]
    for old, (new_metric, conf_key) in sorted(RENAMES.items()):
        rows.append(row("qc_scale", "%s -> %s (GATK QUAL = -10*log10(p))" % (old, new_metric), SCALE,
                        b.get(old), conf.get(conf_key)))
        scaled, nv = num(b.get(old)), num(conf.get(conf_key))
        if scaled is None or nv is None:
            notes.append("%s*10 == %s? MISSING (baseline=%s, new=%s)" % (old, new_metric, b.get(old), conf.get(conf_key)))
        else:
            scaled *= 10.0
            ok = abs(scaled - nv) <= max(QC_TOL * max(abs(scaled), abs(nv)), 1e-9)
            notes.append("%s*10 == %s? %s (%.6f vs %.6f; tolerance %g relative = print precision)"
                         % (old, new_metric, ok, scaled, nv, QC_TOL))
    return rows, notes


def load_side(directory, overrides, index):
    """Discover (or take overridden) files per role; returns (paths, parsed-or-None)."""
    files = sorted(Path(directory).iterdir(), key=lambda p: (len(p.name), p.name)) if Path(directory).is_dir() else []
    paths, data = {}, {}
    for role, globs in PATTERNS.items():
        path = overrides.get(role) or next((p for glob in globs[index] for p in files
                                           if p.is_file() and fnmatch.fnmatch(p.name, glob)), None)
        paths[role], data[role] = path, (parse_table(path, READERS[role][index]) if path else None)
    return paths, data


def parse_overrides(pairs, flag):
    for pair in pairs:
        role, _, path = pair.partition("=")
        if role not in PATTERNS or not path:
            raise SystemExit("bad --%s %r (roles: %s)" % (flag, pair, ", ".join(sorted(PATTERNS))))
    return {pair.partition("=")[0]: Path(pair.partition("=")[2]) for pair in pairs}


def print_table(rows, max_rows):
    for surface in dict.fromkeys(r[0] for r in rows):
        sub, total = [r for r in rows if r[0] == surface], len([r for r in rows if r[0] == surface])
        fmt = "".join("{:<%d}  " % max(8, 2 + max(len(str(r[i])) for r in sub[:max_rows] + [HEADER])) for i in range(4)) \
            + "{:>12}  {:>12}  {}"
        print("\n== %s: %d row(s)%s" % (surface, total,
                                        "" if total <= max_rows else "; first %d shown, all rows in the TSV" % max_rows))
        print("\n".join(fmt.format(*r) for r in [HEADER] + sub[:max_rows]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline-dir", type=Path, required=True)
    ap.add_argument("--new-dir", type=Path, required=True)
    ap.add_argument("--out-prefix", type=Path, help="write <prefix>.tsv ('.tsv' appended if absent)")
    ap.add_argument("--baseline-file", action="append", default=[], metavar="ROLE=PATH")
    ap.add_argument("--new-file", action="append", default=[], metavar="ROLE=PATH")
    ap.add_argument("--max-rows", type=int, default=20, help="stdout rows per surface; TSV is complete")
    a = ap.parse_args()
    for label, d in (("baseline-dir", a.baseline_dir), ("new-dir", a.new_dir)):
        if not os.path.isdir(d):
            sys.exit(f"{label} is not a directory: {d}\n"
                     f"  a typo there would report every column MISSING and exit 0, which looks\n"
                     f"  like a result. See docs/comparators.md for what each side should hold.")
    paths_b, side_b = load_side(a.baseline_dir, parse_overrides(a.baseline_file, "baseline-file"), 0)
    paths_n, side_n = load_side(a.new_dir, parse_overrides(a.new_file, "new-file"), 1)
    for role in PATTERNS:
        print("discovered %-14s baseline=%-46s new=%s" % (role, paths_b[role] or "MISSING", paths_n[role] or "MISSING"))
    rows = compare_params("sr_params", "sr_params", SR_SPECS, side_b, side_n)
    rows += compare_params("pe_params", "pe_params", PE_SPECS, side_b, side_n)
    rows += compare_params("rd_depth", "rd_depth", rd_specs("rd_depth", side_b, side_n), side_b, side_n)
    rows += compare_params("rd_pesr", "rd_pesr", rd_specs("rd_pesr", side_b, side_n), side_b, side_n)
    rows += compare_metrics(side_b, side_n)
    cutoff_rows, notes = compare_cutoffs(side_b, side_n)
    rows += cutoff_rows
    print_table(rows or [HEADER[:4] + ["-", "-", "MISSING"]], a.max_rows)
    print("\n== NOTES\n" + "\n".join("  " + n for n in notes))
    print("\n== SUMMARY  " + "  ".join("%s=%d" % (v, sum(1 for r in rows if r[6] == v))
                                       for v in ("MATCH", "DELTA", "STRATEGY", "MISSING")))
    if a.out_prefix:
        out = a.out_prefix if a.out_prefix.suffix == ".tsv" else Path(str(a.out_prefix) + ".tsv")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\t".join(HEADER) + "\n" + "".join("\t".join(map(str, r)) + "\n" for r in rows))
        print("wrote %d rows -> %s" % (len(rows), out))


if __name__ == "__main__":
    main()
