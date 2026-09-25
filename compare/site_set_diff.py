#!/usr/bin/env python3
"""Who is missing, and what kind of thing it is: a stratified set diff of two callsets.

Why this exists
---------------
Every concordance number in this repo is computed on the **intersection**:
`pair_level_concordance.py` reports exact match over shared VIDs, `gq_paired_compare.py` over
shared cells, `diff_rd_states.py` over matched site keys. That discipline is right for a rate, but
it makes the question you actually ask after "the number moved" unanswerable: *which sites are
gone, and do they have a shape?* A 1 % loss that is uniform noise and a 1 % loss that is every
manta insertion over 1 kb give the same headline and opposite conclusions.

`gatk-sv-profile` can stratify, but its site matcher is fuzzy and its tables are bucketed by caller
(a site found by N callers is counted N times), which is exactly what docs/comparators.md says
makes a figure unreproducible. This is the profiler-free version: exact key match, one count per
site, and the symmetric difference broken down by what it is.

    python compare/site_set_diff.py baseline.pesr.vcf.gz branch.pesr.vcf.gz --join vid
    python compare/site_set_diff.py a.depth.vcf.gz b.depth.vcf.gz --join coord \
           --strata svtype,size,algorithm --min-support 50

Rule (fixed, so a rerun means the same thing):
  * sites are keyed exactly — no fuzzy matching, no "same site within 100 bp": `--join vid` or
    `--join coord` (CHROM/POS/END/SVLEN/SVTYPE). A site is shared iff the key is identical.
  * every stratum comes from the record's own INFO, and a stratum value that differs between the
    two sides of a SHARED site is reported separately ("came back, but was reclassified") instead
    of being averaged away.
  * `size` is |INFO/SVLEN| bucketed at 50/200/1000/10000 by default (`--size-buckets`), with an
    explicit `unknown` bucket — a missing SVLEN is not silently size 0.
  * contig naming is checked before any rate is printed. `chr20` vs `20` empties an exact-key
    intersection, and an empty intersection printed next to a rate of "n/a" is the most dangerous
    false "they agree" recorded in this repo; here it is a FATAL with both contig sets side by side,
    and `--normalize chrom` exists to redo the join deliberately rather than by accident.
Exit: 0 nothing lost or gained, 1 the sets differ, 2 nothing was comparable.
"""
from __future__ import annotations

import argparse
import collections
import gzip
import io
import json
import os
import sys

import artifact

GZIP_MAGIC = b"\x1f\x8b"
STRATA = ("svtype", "size", "algorithm", "contig", "mei")


def die(msg: str, code: int = 2) -> None:
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.exit(code)


def open_text(path: str):
    if not os.path.isfile(path):
        die(f"no such file: {path}")
    with open(path, "rb") as raw:
        magic = raw.read(2)
    if magic == GZIP_MAGIC:
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace", newline="")
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


def strip_chrom(contig: str) -> str:
    return contig[3:] if contig.lower().startswith("chr") else contig


def size_bucket(edges):
    def classify(text):
        try:
            value = abs(int(float(text)))
        except (TypeError, ValueError):
            return "unknown"
        for edge in edges:
            if value < edge:
                return "<%d" % edge
        return ">=%d" % edges[-1]
    return classify


def stratum_getter(name, edges, normalize_chrom):
    """Return fn(info, chrom, cols) -> stratum value. Absent data is always its own bucket."""
    if name == "svtype":
        return lambda info, chrom, cols: info.get("SVTYPE") or "none"
    if name == "size":
        classify = size_bucket(edges)
        return lambda info, chrom, cols: classify(info.get("SVLEN"))
    if name == "algorithm":
        def algo(info, chrom, cols):
            raw = info.get("ALGORITHMS") or info.get("ALGORITHMS_ANNOT")
            if not raw:
                return "none"
            return "+".join(sorted({a.strip() for a in raw.split(",") if a.strip()}))
        return algo
    if name == "mei":
        def mei(info, chrom, cols):
            for key in ("MEI:SUBTYPE", "MEI_SUBTYPE", "SUBTYPE"):
                if key in info:
                    return info[key]
            return "not-MEI"
        return mei
    if name == "contig":
        if normalize_chrom:
            return lambda info, chrom, cols: strip_chrom(chrom)
        return lambda info, chrom, cols: chrom
    die(f"--strata {name!r} is not one of {', '.join(STRATA)}")
    raise AssertionError


def load(path, join, chosen, getters, normalize_chrom, max_records, label):
    """key -> strata tuple, one entry per site (duplicate keys counted, first record wins)."""
    sites = {}
    dup = no_key = records = 0
    contigs = collections.Counter()
    with open_text(path) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            cols = line.rstrip("\n").split("\t")
            if len(cols) < 8:
                continue
            records += 1
            if records > max_records:
                die(f"{label}: {path} exceeded --max-records {max_records:,}. Narrow both files to "
                    f"one contig first (bcftools view -r); this holds one entry per site.", 2)
            info = {}
            for cell in cols[7].split(";"):
                name, _, value = cell.partition("=")
                info[name] = value
            chrom = strip_chrom(cols[0]) if normalize_chrom else cols[0]
            contigs[chrom] += 1
            if join == "coord":
                key = (chrom, cols[1], info.get("END", cols[1]), info.get("SVLEN", "."),
                       info.get("SVTYPE", "."))
            else:
                key = cols[2] if cols[2] not in (".", "") else None
            if key is None:
                no_key += 1
                continue
            if key in sites:
                dup += 1
                continue
            sites[key] = tuple(getters[name](info, chrom, cols) for name in chosen)
    if not sites:
        die(f"{label}: {path} yielded 0 usable {join} keys out of {records:,} record(s), so there "
            f"is no set to compare.", 2)
    return sites, records, contigs, dup, no_key


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stratified set diff of two SV callsets: what is lost, gained, reclassified.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("vcf_a")
    ap.add_argument("vcf_b")
    ap.add_argument("--join", choices=("vid", "coord"), default="vid")
    ap.add_argument("--strata", default="svtype,size,algorithm",
                    help="comma list of %s (default svtype,size,algorithm)" % ", ".join(STRATA))
    ap.add_argument("--size-buckets", default="50,200,1000,10000",
                    help="upper edges of the size stratum (default 50,200,1000,10000)")
    ap.add_argument("--normalize", choices=("none", "chrom"), default="none",
                    help="'chrom' strips a leading chr on BOTH sides before joining, and says so")
    ap.add_argument("--min-support", type=int, default=20,
                    help="a stratum needs this many sites on one side to be ranked (default 20)")
    ap.add_argument("--label-a", default="baseline")
    ap.add_argument("--label-b", default="new")
    ap.add_argument("--max-records", type=int, default=5_000_000)
    ap.add_argument("--max-rows", type=int, default=20, help="rows printed per stratum")
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    args = ap.parse_args()

    chosen = [s.strip() for s in args.strata.split(",") if s.strip()]
    for name in chosen:
        if name not in STRATA:
            die(f"--strata {name!r} is not one of {', '.join(STRATA)}")
    edges = [float(x) for x in args.size_buckets.split(",") if x.strip()]
    getters = {name: stratum_getter(name, edges, args.normalize == "chrom") for name in chosen}
    norm = args.normalize == "chrom"
    if norm:
        print("# NOTE --normalize chrom: a leading 'chr' is stripped from both sides before the "
              "join.\n#      A join made this way is a decision, so it is printed as one.")

    sites_a, nA, contigs_a, dupA, nokeyA = load(args.vcf_a, args.join, chosen, getters, norm,
                                                args.max_records, args.label_a)
    sites_b, nB, contigs_b, dupB, nokeyB = load(args.vcf_b, args.join, chosen, getters, norm,
                                                args.max_records, args.label_b)
    print(f"# {args.label_a}: {args.vcf_a}  [{os.path.getsize(args.vcf_a):,} bytes, {nA:,} records, "
          f"{len(sites_a):,} keys]")
    print(f"# {args.label_b}: {args.vcf_b}  [{os.path.getsize(args.vcf_b):,} bytes, {nB:,} records, "
          f"{len(sites_b):,} keys]")
    if dupA or dupB:
        print(f"#   !! duplicate keys: {args.label_a} {dupA:,}, {args.label_b} {dupB:,} — counted "
              f"once per key, first record's strata used")
    if nokeyA or nokeyB:
        print(f"#   !! records with no {args.join} key: {args.label_a} {nokeyA:,}, "
              f"{args.label_b} {nokeyB:,}")

    shared = set(sites_a) & set(sites_b)
    only_a = set(sites_a) - shared
    only_b = set(sites_b) - shared
    if not shared:
        extra = ""
        if not (set(contigs_a) & set(contigs_b)):
            extra = (f"       Contig sets do not intersect: {args.label_a} "
                     f"{sorted(contigs_a)[:6]}, {args.label_b} {sorted(contigs_b)[:6]}\n")
            if not norm and {strip_chrom(c) for c in contigs_a} & {strip_chrom(c) for c in contigs_b}:
                extra += ("       ... but they DO intersect once a leading chr is stripped. That is a\n"
                          "       naming difference, not a biological one: re-run with --normalize\n"
                          "       chrom to join deliberately. An empty intersection read as agreement\n"
                          "       is the false pass this check exists to stop.\n")
        die(f"FATAL: 0 shared {args.join} keys between {len(sites_a):,} and {len(sites_b):,} sites, "
            f"so nothing was compared.\n{extra}"
            + ("       Across pipeline versions IDs are rewritten (RenameVariants): --join coord.\n"
               if args.join == "vid" else "")
            + "       Note this is NOT a verdict of disagreement: it says the join found nothing.", 2)

    print(f"# shared keys {len(shared):,}  {args.label_a}-only {len(only_a):,}  {args.label_b}-only "
          f"{len(only_b):,}")
    print(f"# recovery: {args.label_a}->{args.label_b} {len(shared) / len(sites_a):.4f}"
          f"   {args.label_b}->{args.label_a} {len(shared) / len(sites_b):.4f}")

    per = {name: dict(a=collections.Counter(), b=collections.Counter(),
                      shared_from_a=collections.Counter(), shared_from_b=collections.Counter(),
                      changed=0) for name in chosen}
    joint_a, joint_b, joint_shared = (collections.Counter(), collections.Counter(),
                                      collections.Counter())
    for strata in sites_a.values():
        for i, name in enumerate(chosen):
            per[name]["a"][strata[i]] += 1
        joint_a[strata] += 1
    for strata in sites_b.values():
        for i, name in enumerate(chosen):
            per[name]["b"][strata[i]] += 1
        joint_b[strata] += 1
    for key in shared:
        sa, sb = sites_a[key], sites_b[key]
        for i, name in enumerate(chosen):
            per[name]["shared_from_a"][sa[i]] += 1
            per[name]["shared_from_b"][sb[i]] += 1
            if sa[i] != sb[i]:
                per[name]["changed"] += 1
        joint_shared[sa] += 1

    for name in chosen:
        rows = sorted(set(per[name]["a"]) | set(per[name]["b"]),
                      key=lambda v: -(per[name]["a"][v] + per[name]["b"][v]))
        print(f"\n## stratum: {name}  ({len(rows)} value(s))")
        print(f"  {'value':<28} {'A':>9} {'B':>9} {'sharedA':>9} {'A-only':>8} {'B-only':>8}"
              f" {'rec A->B':>9} {'rec B->A':>9}")
        for value in rows[:args.max_rows]:
            ca, cb = per[name]["a"][value], per[name]["b"][value]
            sa_, sb_ = per[name]["shared_from_a"][value], per[name]["shared_from_b"][value]
            print(f"  {str(value)[:28]:<28} {ca:9,d} {cb:9,d} {sa_:9,d} {ca - sa_:8,d} {cb - sb_:8,d}"
                  f" {sa_ / ca if ca else float('nan'):>9.4f} {sb_ / cb if cb else float('nan'):>9.4f}")
        if len(rows) > args.max_rows:
            print(f"  ... {len(rows) - args.max_rows} more value(s); all of them are in --json")
        if per[name]["changed"]:
            print(f"  !! {per[name]['changed']:,} shared site(s) carry a DIFFERENT {name} on the two "
                  f"sides — recovered, but reclassified")

    worst = []
    for strata, ca in joint_a.items():
        if max(ca, joint_b[strata]) >= args.min_support and ca:
            worst.append((1.0 - joint_shared[strata] / ca, "from-" + args.label_a, strata,
                          ca, joint_b[strata], joint_shared[strata]))
    for strata, cb in joint_b.items():
        if max(joint_a[strata], cb) >= args.min_support and cb:
            worst.append((1.0 - joint_shared[strata] / cb, "from-" + args.label_b, strata,
                          joint_a[strata], cb, joint_shared[strata]))
    worst.sort(key=lambda t: -t[0])
    lossy = [w for w in worst if w[0] > 0]
    if lossy:
        print(f"\n== strata with a loss (joint {len(chosen)}-way split, support >= "
              f"{args.min_support})")
        print(f"  {'loss':>7}  {'direction':<18} {'A sites':>9} {'B sites':>9} {'shared':>9}  "
              + "  ".join(f"{name:<10}" for name in chosen))
        for loss, direction, strata, ca, cb, cs in lossy[:args.max_rows]:
            print(f"  {loss:7.4f}  {direction:<18} {ca:9,d} {cb:9,d} {cs:9,d}  "
                  + "  ".join(f"{str(s)[:10]:<10}" for s in strata))
        if len(lossy) > args.max_rows:
            print(f"  ... {len(lossy) - args.max_rows} more; all in --json")
    if len(worst) > len(lossy):
        print(f"  ({len(worst) - len(lossy)} stratum/combination(s) at or above support came back "
              f"with no loss at all)")

    print(f"\n== SUMMARY  {args.label_a} {len(sites_a):,} site(s), {args.label_b} {len(sites_b):,}, "
          f"shared {len(shared):,}; lost from {args.label_a} {len(only_a):,}, gained {len(only_b):,}")
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"tool": "site_set_diff", "argv": sys.argv[1:],
                       "inputs": {**artifact.input_file(args.label_a, args.vcf_a, records=nA,
                                                        sites=len(sites_a)),
                                  **artifact.input_file(args.label_b, args.vcf_b, records=nB,
                                                        sites=len(sites_b))},
                       "compared_something": bool(shared),
                       "rule": {"join": args.join, "strata": chosen, "size_buckets": edges,
                                "normalize": args.normalize, "min_support": args.min_support},
                       "sets": {"shared": len(shared), "only_a": len(only_a), "only_b": len(only_b),
                                "recovery_a_to_b": len(shared) / len(sites_a),
                                "recovery_b_to_a": len(shared) / len(sites_b)},
                       "strata": {name: {"a": dict(per[name]["a"]), "b": dict(per[name]["b"]),
                                         "shared_from_a": dict(per[name]["shared_from_a"]),
                                         "shared_from_b": dict(per[name]["shared_from_b"]),
                                         "changed_on_shared_sites": per[name]["changed"]}
                                  for name in chosen},
                       "worst_strata": [{"loss": loss, "direction": d, "strata": list(s),
                                         "a": ca, "b": cb, "shared": cs}
                                        for loss, d, s, ca, cb, cs in worst[:500]]},
                      fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote artifact -> {args.json_out}")
    return 0 if not only_a and not only_b else 1


if __name__ == "__main__":
    sys.exit(main())
