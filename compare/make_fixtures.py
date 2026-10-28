#!/usr/bin/env python3
"""Write the synthetic input pair every comparator in this directory is tested against.

Every planted difference is listed below; scripts/selftest.sh asserts the expected result for each.

    python compare/make_fixtures.py /tmp/gsvtk-fixtures

Planted differences (the test expectations, in one place):

  geno_a.vcf / geno_b.vcf     6 sites x 3 samples, FORMAT GT:GQ:RD_CN:PCC
                              - B's header lists SAMPLE2/SAMPLE3 before SAMPLE1   -> samples must be
                                joined by NAME, and this alone must not change any verdict
                              - VID SV_20_5000_DUP is a duplicate in BOTH files    -> 1 duplicate each
                              - SV_20_5000_DUP/SAMPLE3 GQ 99 -> 55                 -> 1 GQ cell moved
                              - SV_20_100000_DEL/SAMPLE1 GQ 88 -> 90               -> 1 GQ cell moved
                              - SV_20_999_INV is in A only, SV_20_777_BND in B only-> set difference
                              - SAMPLE1/SAMPLE2 RD_CN 1 -> 2 on SV_20_300000_DEL   -> 2 RD cells moved
                              - PCC is a Float field: it must produce real stats, not an all-zero row
  geno_same.vcf               geno_a.vcf with the sample columns permuted          -> self-comparison
  wide_a.tsv / wide_b.tsv     5 sites x 3 samples (key #Chr/Start/End/SVLEN/SVTYPE)
                              - B drops SAMPLE3 and adds SAMPLE4                   -> cohort difference
                              - SV_20_5000 SAMPLE2 25 -> 61                        -> 1 cell moved
                              - column order shuffled, values untouched            -> must be "equal"
  wide_c.tsv                  same grid, one row shifted                           -> must be refused
  tbl_a.tsv / tbl_b.tsv       keyed table; tbl_a carries a duplicate SV_1 row (pe_count 5), B changes
                              sr_min_support 12->15 on SV_2 and carries one column A does not
                              -> 1 DELTA, 1 ONE_SIDED column, 1 duplicate key on each side
  tbl_reordered.tsv           tbl_a with columns permuted and rows re-sorted       -> must be "equal"
  tbl_disjoint.tsv            same schema, no key in common                        -> exit 2
  list_*.txt                  sample lists; list_chr.txt is the same set with a chr prefix on every
                              interval line -> equal only when --normalize chrom is asked for
  inputs_a.json / inputs_b.json  differ in a bucket path, a numeric output_prefix, and one added key
  bundle_a.tar.gz / bundle_b.tar.gz  B adds one member and rewrites one member to the SAME byte count
                                     -> the byte change is only visible with --hash.
                                     (tar-in-tar recursion is not covered by these fixtures.)
  profile_a.tsv.gz / profile_b.tsv.gz  gzipped same-schema twins, as gatk-sv-profile writes them
  body_a.vcf / body_b.vcf.gz   the SAME 3 record lines: B adds two volatile header lines
                               (##bcftools_viewCommand with Date= and a temp path, ##fileDate) and is
                               gzipped while A is plain  -> identical record bodies, different bytes,
                               different md5, different size.
  body_c.vcf                   body_a's records with INFO/AC recomputed on the middle one (AC 3 -> 4)
                               -> 1 record body differs, named by INFO key
  body_empty.vcf               the same header, no records at all -> NOTHING COMPARED (exit 2)
  gd_a.tsv.gz / gd_b.tsv.gz    4 vs 5 rows keyed on (sample, GD_ID), the four GD buckets planted apart:
                               1 key only in A (PANEL4/GD_ONLY_A), 1 key only in B (PANEL3/GD_C), 1
                               PANEL cn_state 2 -> 1 with a posterior that really moved, 1 posterior
                               rewritten in another key order and precision (NOT a change), 1
                               duplicate key on B, 1 '.' dosage cell, and the case row (NA12878/GD_A)
                               untouched -> exit 1 with `CASE NA12878 … -> CLEAN`
  gd_rekey.tsv.gz              the same 2 loci under re-minted GD_IDs -> disjoint keys, refused at 2
  baf_a.txt.gz / baf_b.txt.gz  long-format evidence table (CHROM POS BAF SAMPLE): A carries 3 samples
                               over 6 rows, B lost every PANEL2 row -> census 3 vs 2
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import os
import tarfile

SAMPLES = ["SAMPLE1", "SAMPLE2", "SAMPLE3"]

VCF_SITES = [
    # vid, contig, pos, end, svlen, svtype, {sample: (GT, GQ, RD_CN, PCC)}
    ("SV_20_1000_DEL", "20", 1000, 1050, 50, "DEL",
     {"SAMPLE1": ("0|1", 65, 1, 0.83), "SAMPLE2": ("0|0", 99, 2, 0.12),
      "SAMPLE3": ("0|1", 71, 1, 0.86)}),
    ("SV_20_5000_DUP", "20", 5000, 5200, 200, "DUP",
     {"SAMPLE1": ("0|1", 55, 3, 0.91), "SAMPLE2": ("1|1", 90, 3, 0.95),
      "SAMPLE3": ("0|1", 99, 3, 0.44)}),
    ("SV_20_5000_DUP", "20", 5000, 5200, 200, "DUP",
     {"SAMPLE1": ("0|1", 12, 3, 0.11), "SAMPLE2": ("1|1", 12, 3, 0.11),
      "SAMPLE3": ("0|1", 12, 3, 0.11)}),
    ("SV_20_300000_DEL", "20", 300000, 300450, -450, "DEL",
     {"SAMPLE1": ("0|1", 35, 1, 0.55), "SAMPLE2": ("0|1", 40, 1, 0.60),
      "SAMPLE3": ("0|0", 88, 2, 0.20)}),
    ("SV_20_999_INV", "20", 999, 1000, 1, "INV",
     {"SAMPLE1": ("0|1", 20, -1, 0.33), "SAMPLE2": ("0|0", 99, 2, 0.05),
      "SAMPLE3": ("0|0", 99, 2, 0.06)}),
    ("SV_20_100000_DEL", "20", 100000, 100600, -600, "DEL",
     {"SAMPLE1": ("0|1", 88, 1, 0.70), "SAMPLE2": ("0|0", 95, 2, 0.10),
      "SAMPLE3": ("0|1", 60, 1, 0.68)}),
]

# site vid -> {sample: replacement tuple} for geno_b.vcf
B_FIXES = {
    "SV_20_5000_DUP": {"SAMPLE3": ("0|1", 55, 3, 0.99)},        # GQ 99 -> 55, PCC 0.44 -> 0.99
    "SV_20_100000_DEL": {"SAMPLE1": ("0|1", 90, 1, 0.70)},      # GQ 88 -> 90
    "SV_20_300000_DEL": {"SAMPLE1": ("1|1", 35, 2, 0.55),       # RD_CN 1 -> 2
                         "SAMPLE2": ("0|1", 40, 2, 0.60)},      # RD_CN 1 -> 2
}
B_DROP, B_ADD = "SV_20_999_INV", ("SV_20_777_BND", "20", 777, 778, 0, "BND",
                                  {"SAMPLE1": ("0|1", 45, -1, 0.41),
                                   "SAMPLE2": ("0|0", 92, 2, 0.07),
                                   "SAMPLE3": ("0|0", 93, 2, 0.08)})

WIDE_ROWS = [  # (chrom, start, end, svlen, svtype, {sample: value})
    ("chr20", 1000, 1050, 50, "DEL", {"SAMPLE1": 41.0, "SAMPLE2": 99.0, "SAMPLE3": 42.0}),
    ("chr20", 5000, 5200, 200, "DUP", {"SAMPLE1": 8.0, "SAMPLE2": 25.0, "SAMPLE3": 7.0}),
    ("chr20", 300000, 300450, -450, "DEL", {"SAMPLE1": 55.0, "SAMPLE2": 60.0, "SAMPLE3": 54.0}),
    ("chr20", 999, 1000, 1, "INV", {"SAMPLE1": 33.0, "SAMPLE2": 5.0, "SAMPLE3": 6.0}),
    ("chr20", 100000, 100600, -600, "DEL", {"SAMPLE1": 70.0, "SAMPLE2": 10.0, "SAMPLE3": 68.0}),
]

TABLE_ROWS = [  # (vid, svtype, svlen, sr_count, pe_count, sr_min_support)
    ("SV_1", "DEL", -120, 12, 4, 12),
    ("SV_2", "DUP", 340, 3, 9, 3),
    ("SV_3", "INV", 1, 0, 7, 0),
]
TABLE_DUP_ROW = ("SV_1", "DEL", -120, 12, 5, 12)   # second record with an existing key

HEAD = ("##fileformat=VCFv4.2\n"
        "##contig=<ID=20,length=64444167>\n"
        "##INFO=<ID=END,Number=1,Type=Integer,Description=\"End position\">\n"
        "##INFO=<ID=SVTYPE,Number=1,Type=String,Description=\"Type of structural variant\">\n"
        "##INFO=<ID=SVLEN,Number=1,Type=Integer,Description=\"Difference in length between REF and ALT\">\n"
        "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n"
        "##FORMAT=<ID=GQ,Number=1,Type=Integer,Description=\"Genotype Quality\">\n"
        "##FORMAT=<ID=RD_CN,Number=1,Type=Integer,Description=\"Read depth CN\">\n"
        "##FORMAT=<ID=PCC,Number=1,Type=Float,Description=\"Correlation of coverage to profiles\">\n")


def vcf_text(samples: list, sites: list) -> str:
    out = [HEAD + "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t"
           + "\t".join(samples) + "\n"]
    for vid, chrom, pos, end, svlen, svtype, gt in sites:
        fmt = "\t".join(":".join(map(str, gt[s])) for s in samples)
        out.append(f"{chrom}\t{pos}\t{vid}\tN\t<{svtype}>\t.\tPASS\t"
                   f"END={end};SVTYPE={svtype};SVLEN={svlen}\tGT:GQ:RD_CN:PCC\t{fmt}\n")
    return "".join(out)


def wide_text(samples: list, rows: list) -> str:
    out = ["#Chr\tStart\tEnd\tSVLEN\tSVTYPE\t" + "\t".join(samples) + "\n"]
    for chrom, start, end, svlen, svtype, vals in rows:
        out.append("\t".join([chrom, str(start), str(end), str(svlen), svtype]
                             + [f"{vals[s]:.4f}" for s in samples]) + "\n")
    return "".join(out)


def table_text(rows: list, extra_column: str = None) -> str:
    header = ["vid", "svtype", "svlen", "sr_count", "pe_count", "sr_min_support"]
    if extra_column:
        header.append(extra_column)
    out = ["\t".join(header) + "\n"]
    for r in rows:
        out.append("\t".join(str(x) for x in r) + ("\t8" if extra_column else "") + "\n")
    return "".join(out)


def tar_write(path: str, members: dict) -> None:
    """Write a tar.gz with every member's mtime pinned to 0, so output is reproducible."""
    with tarfile.open(path, "w:gz") as tf:
        for name, data in sorted(members.items()):
            raw = data.encode()
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mtime = 0
            tf.addfile(info, io.BytesIO(raw))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("directory", help="where to write the fixture tree (created if missing)")
    a = ap.parse_args()
    os.makedirs(a.directory, exist_ok=True)
    w = lambda name, text: open(os.path.join(a.directory, name), "w").write(text)  # noqa: E731

    b_sites = [site for site in VCF_SITES if site[0] != B_DROP] + [B_ADD]
    b_sites = [(vid, c, p, e, sl, st, {s: B_FIXES.get(vid, {}).get(s, v) for s, v in gt.items()})
               for vid, c, p, e, sl, st, gt in b_sites]
    w("geno_a.vcf", vcf_text(SAMPLES, VCF_SITES))
    w("geno_b.vcf", vcf_text([SAMPLES[1], SAMPLES[2], SAMPLES[0]], b_sites))
    w("geno_same.vcf", vcf_text([SAMPLES[2], SAMPLES[0], SAMPLES[1]], VCF_SITES))

    w("wide_a.tsv", wide_text(SAMPLES, WIDE_ROWS))
    w("wide_b.tsv", wide_text(["SAMPLE4", "SAMPLE2", "SAMPLE1"],
                              [(c, s, e, sl, st, dict(v, SAMPLE2=(61.0 if st == "DUP" else v["SAMPLE2"]),
                                                      SAMPLE4=9.0)) for c, s, e, sl, st, v in WIDE_ROWS]))
    shifted = list(WIDE_ROWS)
    shifted[1], shifted[2] = shifted[2], shifted[1]
    w("wide_c.tsv", wide_text(SAMPLES, shifted))

    w("tbl_a.tsv", table_text(TABLE_ROWS + [TABLE_DUP_ROW]))
    w("tbl_b.tsv", table_text([TABLE_ROWS[0], ("SV_2", "DUP", 340, 3, 9, 15), TABLE_ROWS[2],
                               TABLE_DUP_ROW], extra_column="pe_min_support"))
    # The same rows with columns permuted and rows re-sorted: must compare equal.
    w("tbl_reordered.tsv",
      "sr_min_support\tvid\tsvlen\tsvtype\tsr_count\tpe_count\n"
      "12\tSV_1\t-120\tDEL\t12\t4\n"
      "0\tSV_3\t1\tINV\t0\t7\n"
      "3\tSV_2\t340\tDUP\t3\t9\n"
      "12\tSV_1\t-120\tDEL\t12\t5\n")
    w("tbl_disjoint.tsv", table_text([("SV_9", "INS", 88, 1, 1, 1)]))

    w("list_a.txt", "SAMPLE1\nSAMPLE2\nSAMPLE3\n")
    w("list_b.txt", "SAMPLE1\nSAMPLE3\nSAMPLE4\n")
    w("list_chr.txt", "chr20\t1000\tDEL\nchr20\t5000\tDUP\nchr20\t300000\tDEL\n")
    w("list_nochr.txt", "20\t1000\tDEL\n20\t5000\tDUP\n20\t300000\tDEL\n")

    inputs_a = {"del_bed": "gs://bucket-A/cohort/dels.bed", "out_dir": "gs://bucket-A/run",
                "n_shards": 16, "discovery_strategy": "merge"}
    inputs_b = {"del_bed": "gs://bucket-B/cohort/dels.bed", "out_dir": "gs://bucket-B/run",
                "n_shards": 8, "discovery_strategy": "merge", "omit_alts": "False"}
    w("inputs_a.json", json.dumps(inputs_a, indent=1, sort_keys=True) + "\n")
    w("inputs_b.json", json.dumps(inputs_b, indent=1, sort_keys=True) + "\n")

    tar_write(os.path.join(a.directory, "bundle_a.tar.gz"), {
        "payload/one.tsv": "a\t1\nb\t2\nc\t3\n",
        "payload/two.txt": "unchanged bytes\n",
    })
    # payload/one.tsv is rewritten to the same byte count, so only --hash sees it.
    tar_write(os.path.join(a.directory, "bundle_b.tar.gz"), {
        "payload/one.tsv": "a\t1\nb\t9\nc\t3\n",
        "payload/two.txt": "unchanged bytes\n",
        "payload/added.txt": "only in B\n",
    })

    # ---- VCF record-body equality: the same bodies under a rewritten header ----------------------
    body_head = ("##fileformat=VCFv4.2\n"
                 "##contig=<ID=20,length=64444167>\n"
                 "##INFO=<ID=SVTYPE,Number=1,Type=String,Description=\"Type\">\n"
                 "##INFO=<ID=AC,Number=1,Type=Integer,Description=\"Allele count\">\n"
                 "##FORMAT=<ID=GT,Number=1,Type=String,Description=\"Genotype\">\n")
    body_chrom = "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tcase\tmom\n"
    body_rows = ("20\t1000\tSV_1\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;AC=3\tGT\t0/1\t0/0\n"
                 "20\t2000\tSV_2\tN\t<DUP>\t60\tPASS\tSVTYPE=DUP;AC=1\tGT\t0/0\t0/1\n"
                 "20\t3000\tSV_3\tN\t<INV>\t.\tLowGQ\tSVTYPE=INV;AC=2\tGT\t0/1\t0/1\n")
    volatile = ("##bcftools_viewCommand=view -Oz -o /tmp/tmp.FIXTURE1/out.vcf; Date=20261007T000000\n"
                "##fileDate=20261007\n")
    w("body_a.vcf", body_head + body_chrom + body_rows)
    # The same three records under a rewritten header, gzipped.
    with gzip.open(os.path.join(a.directory, "body_b.vcf.gz"), "wt") as fh:
        fh.write(body_head + volatile + body_chrom + body_rows)
    w("body_c.vcf", body_head + body_chrom
      + body_rows.replace("SVTYPE=DUP;AC=1", "SVTYPE=DUP;AC=4"))
    w("body_empty.vcf", body_head + body_chrom)

    # ---- GD calls table: the four buckets of "the calls changed", planted apart -------------------
    gd_head = ("sample\tGD_ID\tchrom\tstart\tend\tsvtype\tcn_state\tcn_probabilities\tis_carrier"
               "\tqual_score\tdosage_bins_at_median\n")
    gd_rows_a = (
        "NA12878\tGD_A\tchr1\t1000\t2000\tDEL\t2\t{\"2\": 0.98, \"1\": 0.02}\tTrue\t12.5\t3\n"
        "PANEL4\tGD_ONLY_A\tchr1\t3000\t4000\tDUP\t1\t{\"1\": 0.9, \"2\": 0.1}\tTrue\t9\t2\n"
        "PANEL1\tGD_A\tchr1\t1000\t2000\tDEL\t2\t{\"2\": 0.98, \"1\": 0.02}\tTrue\t11\t3\n"
        "PANEL2\tGD_B\tchr2\t5000\t6000\tDEL\t1\t{\"1\": 0.60, \"2\": 0.30}\tTrue\t7.25\t2\n")
    gd_rows_b = (
        "NA12878\tGD_A\tchr1\t1000\t2000\tDEL\t2\t{\"1\": 0.02, \"2\": 0.98}\tTrue\t12.5\t3\n"
        "PANEL1\tGD_A\tchr1\t1000\t2000\tDEL\t1\t{\"1\": 0.55, \"2\": 0.45}\tTrue\t11\t3\n"
        "PANEL1\tGD_A\tchr1\t1000\t2000\tDEL\t1\t{\"1\": 0.55, \"2\": 0.45}\tTrue\t11\t3\n"
        "PANEL2\tGD_B\tchr2\t5000\t6000\tDEL\t1\t{\"2\": 0.3, \"1\": 0.6}\tTrue\t7.25\t.\n"
        "PANEL3\tGD_C\tchr3\t7000\t8000\tDUP\t3\t{\"3\": 0.99}\tTrue\t4\t4\n")
    gd_rekey = ("NA12878\tGD_NEW_1\tchr1\t1000\t2000\tDEL\t2\t{\"2\": 1.0}\tTrue\t12.5\t3\n"
                "PANEL1\tGD_NEW_2\tchr1\t1000\t2000\tDEL\t2\t{\"2\": 1.0}\tTrue\t11\t3\n")
    for name, body in (("gd_a.tsv.gz", gd_rows_a), ("gd_b.tsv.gz", gd_rows_b),
                       ("gd_rekey.tsv.gz", gd_rekey)):
        with gzip.open(os.path.join(a.directory, name), "wt") as fh:
            fh.write(gd_head + body)

    # ---- evidence-matrix census: a merged BAF table that lost a sample ----------------------------
    baf_head = "#CHROM\tPOS\tBAF\tSAMPLE\n"
    baf_rows_a = "".join(f"chr1\t{i * 100}\t0.5\t{s}\n" for s in ("NA12878", "PANEL1", "PANEL2")
                         for i in (1, 2))
    baf_rows_b = "".join(ln for ln in baf_rows_a.splitlines(keepends=True) if "PANEL2" not in ln)
    for name, body in (("baf_a.txt.gz", baf_rows_a), ("baf_b.txt.gz", baf_rows_b)):
        with gzip.open(os.path.join(a.directory, name), "wt") as fh:
            fh.write(baf_head + body)

    prof_a = ("metric\tn_baseline_v111\tn_new_java\nmean_gq\t66.48\t7.28\nmedian_gq\t88\t9\n")
    prof_b = ("metric\tn_baseline_v111\tn_new_java\nmean_gq\t66.48\t7.30\nmedian_gq\t88\t9\n")
    for name, text in (("profile_a.tsv.gz", prof_a), ("profile_b.tsv.gz", prof_b)):
        with gzip.open(os.path.join(a.directory, name), "wt") as fh:
            fh.write(text)

    print(f"wrote fixtures -> {a.directory}")
    print("planted: 2 moved GQ cells, 2 moved RD_CN cells, 1 moved PCC cell, 1 dropped site, "
          "1 added site, 1 duplicate VID per VCF, 1 moved matrix cell, 1 added and 1 dropped sample "
          "in the matrix, 1 changed table cell, 1 one-sided table column, 1 duplicate key in each "
          "table, 1 added bundle member, 1 same-size byte change, 1 volatile-header-only VCF pair "
          "(plain vs gz) with 1 recomputed INFO/AC and 1 header-only VCF, 1 GD key dropped and 1 "
          "added with 1 panel cn_state shift, 1 posterior rewritten and 1 posterior moved, 1 "
          "duplicate GD key, 1 re-keyed GD table, 1 evidence-table sample lost")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
