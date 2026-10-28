#!/usr/bin/env python3
"""Sample census of ONE long-format evidence table, streamed: which samples are actually in it.

Answers "how many distinct samples are in this column, and are they the ones you meant" for the
long-format evidence tables (merged BAF, SD/PE/SR) a single-sample run feeds to GD. A merged BAF
table that silently held only the case sample instead of the whole panel looks the same size on
disk. Large BGZF tables are streamed, not loaded.

    # require 157 distinct samples
    python compare/sample_census.py NA12878.baf.txt.gz --sample-column SAMPLE --expect 157
    # no header at all (baf_from_sd.py writes contig/pos/baf/sample): count column 4
    python compare/sample_census.py case.baf.txt.gz --sample-column 4
    # compare two tables' sample lists
    python compare/sample_census.py A.sd.txt.gz --write-samples A.samples.txt
    python compare/sample_census.py B.sd.txt.gz --write-samples B.samples.txt
    python compare/lineset_diff.py A.samples.txt B.samples.txt

Rule:
  * one file only; use `--write-samples` and `lineset_diff.py` to compare two censuses.
  * a first line starting `#` is the header, and the decision is printed. With no header, a column
    NAME cannot be resolved and the run refuses; give a 1-based number instead.
  * `--sample-column` takes a NAME (case-insensitive against the header) or a 1-based NUMBER; the
    resolved column, its position, and its header field are printed.
  * one pass, one counter per distinct value; peak RSS is printed.
  * values are counted as written. Normalization is opt-in and printed (`--normalize trim`,
    `--normalize case`).
  * blank lines and `#` lines inside the body are counted as ignored. A row with fewer fields than the
    census column is a SHORT row: counted, shown with its line number, and excluded.
  * `.`/empty values are counted like any other value and called out separately.
  * `--expect N` fails (exit 1) when the distinct count is not N.

Exit status:
  0  counted rows, and `--expect` (if given) held
  1  counted rows, but `--expect` did not hold
  2  nothing counted: 0 data rows, a column spec that does not resolve, a header-only file, a wide
     matrix (use `matrix_diff.py`), or an unreadable input.
`--selftest` writes its own fixtures (headered, headerless, wide, short-rowed, one-sample) and
asserts every rule above.
"""
from __future__ import annotations

import argparse
import os
import resource
import sys
from collections import Counter

# Make `import artifact` work from any cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import artifact  # noqa: E402
from artifact import die, open_text  # noqa: E402

JUNK = {".", "", "-", "NA", "N/A", "nan", "None"}



def resolve_column(spec, header, path, first_line, delim):
    """Return (index, display-name) for `--sample-column`, or exit with a hint."""
    spec = spec.strip()
    if spec.isdigit():
        idx = int(spec) - 1
        if idx < 0:
            die("--sample-column is 1-based; column 0 does not exist", 2)
        if header is not None and idx >= len(header):
            die(f"--sample-column {spec} is past the end of this header "
                f"({len(header)} column(s): {', '.join(header)})", 2)
        return idx, (header[idx] if header is not None and idx < len(header) else f"field {spec}")
    if header is None:
        die(f"{path}: --sample-column {spec!r} is a name and this file has no header line, so there "
            f"is nothing to match it against. Its first line, split on {'TAB' if delim == chr(9) else repr(delim)}, "
            f"has {len(first_line.split(delim))} field(s):\n    {first_line[:160]}\n"
            f"  Name the column by position instead: --sample-column <1-based number>", 2)
    lowered = {h.lower(): (i, h) for i, h in enumerate(header)}
    if spec.lower() in lowered:
        return lowered[spec.lower()]
    die(f"{path}: no column {spec!r} in this header: {', '.join(header)}\n"
        f"  If this is a WIDE matrix — one column PER sample, sites down the rows — then the samples "
        f"live in the column names, not in any value column, and a value-column census is the wrong "
        f"tool. Count the header: `head -1 {path} | cut -f{len(header) - 1}- | tr '\\t' '\\n'`, or "
        f"diff two of them with compare/matrix_diff.py.", 2)


def quantile(sorted_vals, q):
    if not sorted_vals:
        return 0
    pos = max(0, min(len(sorted_vals) - 1, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[pos]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stream one long-format evidence table and census the values of its sample column.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("table", nargs="?", help="BAF / SD / PE / SR table, plain or BGZF")
    ap.add_argument("--sample-column", default="SAMPLE", metavar="NAME|N",
                    help="column NAME, or 1-based NUMBER (default: SAMPLE, by name)")
    ap.add_argument("--delim", default="\t", help="'tab' (default) or a single character")
    ap.add_argument("--expect", type=int, default=None, metavar="N",
                    help="declared expectation: FAIL (exit 1) unless exactly N distinct values are found")
    ap.add_argument("--normalize", default="", metavar="LIST",
                    help="comma list of trim, case — printed, never done quietly")
    ap.add_argument("--top", type=int, default=20, help="show N busiest and N quietest (default 20)")
    ap.add_argument("--write-samples", metavar="PATH",
                    help="write the distinct values, sorted, one per line (for lineset_diff.py)")
    ap.add_argument("--selftest", action="store_true",
                    help="write fixture tables in a temp dir and assert every rule above")
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.table:
        ap.error("one evidence table is required (this is a census of one file, not a diff)")

    normalize = [n.strip().lower() for n in args.normalize.split(",") if n.strip()]
    for n in normalize:
        if n not in ("trim", "case"):
            die(f"--normalize {n!r} is not one of trim, case", 2)
    delim = "\t" if args.delim in ("tab", "\\t") else args.delim
    if len(delim) != 1:
        die(f"--delim must be one character or the word 'tab', got {args.delim!r}", 2)

    print(f"# input: {args.table}  [{os.path.getsize(args.table):,} bytes on disk, "
          f"sha256:{artifact.digest(args.table)}]")

    counts = Counter()
    rows = 0
    blank = 0
    short = 0
    short_first = None
    line_no = 0

    # Peek at the first line to decide the header and resolve the column before counting.
    src = iter(open_text(args.table, 'name one evidence table'))
    first_line = None
    for raw in src:
        line_no += 1
        text = raw.rstrip("\n").rstrip("\r")
        if not text.strip():
            blank += 1
            continue
        first_line = text
        break
    if first_line is None:
        die(f"NOTHING CENSUSED: {args.table} has no non-blank line at all.", 2)
    header = first_line.lstrip("#").split(delim) if first_line.startswith("#") else None
    idx, colname = resolve_column(args.sample_column, header, args.table, first_line, delim)

    def census(text, at_line):
        """Count one data line's value, or count it as short."""
        nonlocal rows, blank, short, short_first
        if not text.strip():
            blank += 1
            return
        if text.startswith("#"):
            blank += 1
            return
        rows += 1
        fields = text.split(delim)
        if len(fields) <= idx:
            short += 1
            if short_first is None:
                short_first = at_line
            return
        value = fields[idx]
        if "trim" in normalize:
            value = value.strip()
        if "case" in normalize:
            value = value.lower()
        counts[value] += 1

    if header is None:
        census(first_line, line_no)             # no header: that line was data
    for raw in src:
        line_no += 1
        census(raw.rstrip("\n").rstrip("\r"), line_no)

    print("# header: " + (f"yes — {' | '.join(header)[:120]}" if header is not None
                           else "NO header line: the first line is data"))
    print(f"# column: {colname} (1-based position {idx + 1}); delimiter: "
          f"{'TAB' if delim == chr(9) else repr(delim)}; normalization: "
          f"{', '.join(normalize) if normalize else 'NONE (values counted exactly as written)'}")
    print(f"# rows: {rows:,} data line(s) read, {sum(counts.values()):,} carried a value in that "
          f"column, {short:,} short line(s) with no value there"
          + (f" (first at line {short_first})" if short_first else "")
          + f", {blank:,} blank/comment line(s) ignored")

    if not counts:
        die(f"NOTHING CENSUSED: {args.table} yielded 0 rows carrying a value in column "
            f"{idx + 1} ({rows:,} data line(s), {short:,} of them too short, {blank:,} blank). "
            f"An empty census is not a sample count.", 2)

    per_sample = sorted(counts.values())
    total = sum(counts.values())
    distinct = len(counts)
    busiest = counts.most_common()
    junk = {v: c for v, c in counts.items() if v in JUNK}

    print(f"# distinct values: {distinct:,}   rows per value: min {per_sample[0]:,}, "
          f"p25 {quantile(per_sample, 0.25):,}, median {quantile(per_sample, 0.5):,}, "
          f"p75 {quantile(per_sample, 0.75):,}, max {per_sample[-1]:,}   "
          f"(mean {total / distinct:,.1f})")
    print(f"# concentration: the busiest value carries {100.0 * busiest[0][1] / total:.2f}% of "
          f"{total:,} rows; the quietest carries {100.0 * per_sample[0] / total:.2f}%")
    if distinct == 1:
        print("#   note: exactly ONE distinct value. On a merged BAF/SD/PE/SR table that is the shape "
              "of a merge that kept only one side — the case without its panel, or the panel without "
              "the case. It is not by itself a verdict; --expect is.")
    if junk:
        print("#   note: " + ", ".join(f"{v!r} x {c:,}" for v, c in sorted(junk.items()))
              + " counted as a value. They are data defects, not samples, and they were NOT dropped.")

    print(f"\n== busiest {min(args.top, distinct)} value(s)")
    for value, n in busiest[:args.top]:
        print(f"   {value[:60]:<60} {n:>12,}  {100.0 * n / total:6.2f}%")
    if distinct > 2 * args.top:
        print(f"\n== quietest {args.top} value(s)")
        for value, n in sorted(busiest, key=lambda kv: (kv[1], kv[0]))[:args.top]:
            print(f"   {value[:60]:<60} {n:>12,}  {100.0 * n / total:6.2f}%")
    if distinct > args.top:
        print(f"#   … {distinct - min(args.top, distinct):,} more distinct value(s) counted and not "
              f"printed; --top N raises the ceiling, --write-samples writes them all")

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    mib = rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024
    print(f"# streamed: one pass, one counter per distinct value, no rows held. Peak RSS of this "
          f"process: {mib:.1f} MiB (ru_maxrss; bytes on macOS, KiB on Linux) against a "
          f"{os.path.getsize(args.table):,} byte input")

    if args.write_samples:
        with open(args.write_samples, "w", encoding="utf-8") as fh:
            for value in sorted(counts):
                fh.write(value + "\n")
        print(f"wrote {distinct:,} distinct value(s) -> {args.write_samples}")

    verdict = f"CENSUS_{distinct}"
    ok = True
    if args.expect is not None:
        ok = distinct == args.expect
        print(f"# EXPECTED {args.expect:,} distinct sample(s), FOUND {distinct:,} -> "
              f"{'HELD' if ok else 'VIOLATED'}")
        verdict += f"_EXPECTED_{args.expect}_" + ("HELD" if ok else "VIOLATED")
    else:
        print("# no --expect given: this is a measurement, not a pass. --expect N turns it into a "
              "check a tool decides.")
        verdict += "_UNDECLARED"
    print(f"verdict: {verdict}")

    if args.write_samples:
        out_entry = artifact.input_file("written_samples", args.write_samples,
                                        purpose="the distinct values, for compare/lineset_diff.py")
    else:
        out_entry = {}
    inputs = dict(artifact.input_file("table", args.table, rows=rows, distinct=distinct,
                                      column=colname, column_index=idx + 1))
    inputs.update(out_entry)
    doc = artifact.envelope(
        "sample_census", inputs,
        {"sample_column": args.sample_column, "resolved_column": colname, "resolved_index": idx + 1,
         "delimiter": delim, "normalize": normalize, "header_line": header is not None,
         "expect": args.expect, "one_pass_streamed": True},
        {"rows": rows, "rows_census": total, "short_rows": short, "ignored_lines": blank,
         "distinct": distinct,
         "rows_per_value": {"min": per_sample[0], "p25": quantile(per_sample, 0.25),
                            "median": quantile(per_sample, 0.5), "p75": quantile(per_sample, 0.75),
                            "max": per_sample[-1], "mean": total / distinct},
         "busiest": {"value": busiest[0][0], "rows": busiest[0][1],
                     "share": busiest[0][1] / total},
         "junk_values": {k: v for k, v in junk.items()},
         "top": [{"value": v, "rows": n} for v, n in busiest[:args.top]],
         "peak_rss_mib": round(mib, 1)},
        verdict=verdict, compared_something=True)
    artifact.write(args.json_out, doc)
    return 0 if ok else 1


# --------------------------------------------------------------------------------------------- selftest
# 3 samples, 9 rows: SAMP_A 4 rows, SAMP_B 3 rows, SAMP_C 2 rows (median 3).
_ST_BAF = """#CHROM\tPOS\tBAF\tSAMPLE
chr1\t100\t0.51\tSAMP_A
chr1\t200\t0.49\tSAMP_A
chr1\t300\t0.50\tSAMP_A
chr1\t400\t0.52\tSAMP_A
chr1\t100\t0.48\tSAMP_B
chr1\t200\t0.55\tSAMP_B
chr1\t300\t0.47\tSAMP_B
chr1\t100\t0.53\tSAMP_C
chr1\t200\t0.46\tSAMP_C
"""
_ST_NOHDR = ("chr1\t100\t0.51\tSAMP_A\nchr1\t200\t0.49\tSAMP_B\nchr1\t300\t0.50\tSAMP_A\n")
_ST_ONE = """#CHROM\tPOS\tBAF\tSAMPLE
""" + "".join(f"chr1\t{i * 100}\t0.5\tONLYONE\n" for i in range(1, 6))
_ST_SHORT = _ST_BAF + "chr1\t500\n"
_ST_JUNK = _ST_BAF + "chr1\t600\t0.5\t.\n"
# SAMP_A once with a trailing space, SAMP_B once in lower case: five distinct strings, three samples.
_ST_MIXED = (_ST_BAF.replace("chr1\t200\t0.49\tSAMP_A", "chr1\t200\t0.49\tSAMP_A ")
             .replace("chr1\t100\t0.48\tSAMP_B", "chr1\t100\t0.48\tsamp_b"))
_ST_WIDE = "#CHROM\tSTART\tEND\tSAMP_A\tSAMP_B\tSAMP_C\nchr1\t100\t200\t0.51\t0.49\t0.50\n"


def selftest() -> int:
    import gzip as _gzip
    import json
    import subprocess
    import tempfile

    fails = []

    def check(desc, cond, detail=""):
        print(("  ok    " if cond else "  FAIL  ") + desc
              + (f"\n          {detail}" if (detail and not cond) else ""))
        if not cond:
            fails.append(desc)

    tool = os.path.abspath(__file__)

    def run(argv):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GSVTK_")}
        return subprocess.run([sys.executable, tool] + argv, capture_output=True, text=True, env=env)

    with tempfile.TemporaryDirectory() as dd:
        def w(name, text, gz=False):
            p = os.path.join(dd, name)
            if gz:
                with _gzip.open(p, "wt") as fh:
                    fh.write(text)
            else:
                with open(p, "w") as fh:
                    fh.write(text)
            return p

        baf = w("a.baf.txt", _ST_BAF)
        bafgz = w("a.baf.txt.gz", _ST_BAF, gz=True)
        one = w("one.baf.txt", _ST_ONE)
        short = w("short.baf.txt", _ST_SHORT)
        junk = w("junk.baf.txt", _ST_JUNK)
        wide = w("wide.txt", _ST_WIDE)
        nohdr = w("nohdr.txt", _ST_NOHDR)
        empty = w("empty.txt", "#CHROM\tPOS\tBAF\tSAMPLE\n")

        r1 = run([baf])
        check("the census counts distinct values, not rows: 3 samples over 9 rows, with the per-value "
              "distribution beside it", r1.returncode == 0
              and "distinct values: 3" in r1.stdout and "9 data line(s) read" in r1.stdout
              and "min 2," in r1.stdout and "median 3," in r1.stdout and "max 4" in r1.stdout,
              f"exit {r1.returncode}\n{r1.stdout}{r1.stderr}")
        check("the header decision is PRINTED, with the columns it read",
              "# header: yes" in r1.stdout and "CHROM" in r1.stdout and "SAMPLE" in r1.stdout,
              r1.stdout[:400])
        check("the resolved column names the header field AND its 1-based position",
              "column: SAMPLE (1-based position 4)" in r1.stdout, r1.stdout[:500])
        check("the shares are printed, so a matrix dominated by one sample is visible without a flag",
              "busiest value carries 44.44%" in r1.stdout,
              "\n".join(ln for ln in r1.stdout.splitlines() if "concentration" in ln))
        check("the streamed claim is stated with the memory that proves it", "Peak RSS" in r1.stdout
              and "one counter per distinct value" in r1.stdout, r1.stdout[-400:])
        check("no --expect means a measurement, not a pass, and the tool says which it printed",
              "no --expect given" in r1.stdout and "CENSUS_3_UNDECLARED" in r1.stdout,
              r1.stdout[-300:])

        r2 = run([bafgz, "--expect", "3"])
        check("BGZF reads from the bytes and --expect turns the count into a check a tool decides",
              r2.returncode == 0 and "EXPECTED 3 distinct sample(s), FOUND 3 -> HELD" in r2.stdout,
              f"exit {r2.returncode}\n{r2.stdout}{r2.stderr}")
        r3 = run([one, "--expect", "157"])
        check("THE BUG: one sample where 157 were expected fails the run and names both numbers",
              r3.returncode == 1 and "EXPECTED 157" in r3.stdout and "FOUND 1" in r3.stdout
              and "VIOLATED" in r3.stdout and "exactly ONE distinct value" in r3.stdout,
              f"exit {r3.returncode}\n{r3.stdout}{r3.stderr}")
        r4 = run([baf, "--expect", "157"])
        check("and 3 is not 157 either — the refusal does not depend on being exactly one sample",
              r4.returncode == 1 and "VIOLATED" in r4.stdout, f"exit {r4.returncode}")

        r5 = run([nohdr, "--sample-column", "4"])
        check("a headerless table is censusable by position, and says it found no header",
              r5.returncode == 0 and "# header: NO header line" in r5.stdout
              and "distinct values: 2" in r5.stdout, f"exit {r5.returncode}\n{r5.stdout}{r5.stderr}")
        r6 = run([nohdr])
        check("naming a column in a file with no header is refused, and the refusal shows the first "
              "line and says to name a position instead of guessing one", r6.returncode == 2
              and "has no header line" in r6.stderr
              and "Name the column by position" in r6.stderr,
              f"exit {r6.returncode}\n{r6.stdout}{r6.stderr}")

        r7 = run([wide])
        check("a WIDE matrix is refused with the reason and the tool that fits, not censused into a "
              "single wrong number", r7.returncode == 2 and "WIDE matrix" in r7.stderr
              and "matrix_diff" in r7.stderr, f"exit {r7.returncode}\n{r7.stdout}{r7.stderr}")

        r8 = run([short])
        check("a row too short to have a value there is counted and line-numbered, never counted as a "
              "sample named empty", r8.returncode == 0 and "1 short line(s) with no value there"
              in r8.stdout and "first at line 11" in r8.stdout and "distinct values: 3" in r8.stdout,
              f"exit {r8.returncode}\n{r8.stdout}{r8.stderr}")
        r9 = run([junk])
        check("a '.' in the sample column is counted AND called out, never quietly dropped",
              r9.returncode == 0 and "distinct values: 4" in r9.stdout
              and "data defects, not samples" in r9.stdout,
              f"exit {r9.returncode}\n{r9.stdout}{r9.stderr}")

        r10 = run([empty])
        check("a header-only file yields NOTHING CENSUSED at exit 2, never a sample count of 0 at 0",
              r10.returncode == 2 and "NOTHING CENSUSED" in r10.stderr,
              f"exit {r10.returncode}\n{r10.stdout}{r10.stderr}")

        out = os.path.join(dd, "samples.txt")
        r11 = run([baf, "--write-samples", out])
        written = open(out).read().splitlines() if os.path.isfile(out) else []
        check("--write-samples writes the distinct values sorted, which is what lineset_diff.py eats",
              written == ["SAMP_A", "SAMP_B", "SAMP_C"] and "wrote 3 distinct value(s)" in r11.stdout,
              str(written))

        r12 = run([w("mixed.txt", _ST_MIXED)])
        check("a trailing-space name and a case difference are DISTINCT until you ask otherwise, and "
              "the page says nothing was normalized", r12.returncode == 0
              and "distinct values: 5" in r12.stdout
              and "normalization: NONE" in r12.stdout, f"exit {r12.returncode}\n{r12.stdout}")
        r13 = run([w("mixed.txt", _ST_MIXED), "--normalize", "trim,case"])
        check("--normalize trim,case folds them to 3 and says it did", r13.returncode == 0
              and "distinct values: 3" in r13.stdout and "normalization: trim, case" in r13.stdout,
              f"exit {r13.returncode}\n{r13.stdout}")
        r14 = run([baf, "--normalize", "chr"])
        check("a normalization this tool does not offer is refused rather than silently ignored",
              r14.returncode == 2 and "not one of trim, case" in r14.stderr,
              f"exit {r14.returncode}\n{r14.stdout}{r14.stderr}")

        art = os.path.join(dd, "census.json")
        run([baf, "--expect", "3", "--json", art])
        doc = json.load(open(art)) if os.path.isfile(art) else {}
        check("the artifact carries the rule (column, delimiter, normalization, expectation) and the "
              "count it produced", doc.get("tool") == "sample_census"
              and doc.get("distinct") == 3 and doc.get("rows") == 9
              and doc.get("rule", {}).get("resolved_column") == "SAMPLE"
              and doc.get("rule", {}).get("expect") == 3
              and doc.get("compared_something") is True,
              json.dumps({k: doc.get(k) for k in ("tool", "distinct", "rows", "rule")})[:500])
        check("inputs names the file AND its bytes, so a re-fetch cannot silently change the answer",
              {"path", "bytes", "sha256_16"} <= set(doc.get("inputs", {}).get("table", {})),
              json.dumps(doc.get("inputs", {}))[:300])

        # Streaming check: tracemalloc's peak of live Python objects must not grow with 20x the rows.
        # (Peak RSS is not used because glibc retains freed memory, so it varies by platform.)
        small_table = w("scale_small.txt", "#CHROM\tPOS\tBAF\tSAMPLE\n"
                                   + "".join(f"chr1\t{i}\t0.5\ts{i}\n" for i in range(50_000)))
        big_table = w("scale_big.txt", "#CHROM\tPOS\tBAF\tSAMPLE\n"
                                  + "".join(f"chr1\t{i}\t0.5\ts{i % 50000}\n"
                                            for i in range(1_000_000)))
        r_scale, r_big = run([small_table]), run([big_table])

        def peak_rss(text):
            for line in text.splitlines():
                if "Peak RSS of this process:" in line:
                    return float(line.split("Peak RSS of this process:")[1].split("MiB")[0])
            return None

        # Run the tool unchanged under tracemalloc and print the peak at the end.
        tracer = w("traced_peak.py",
                   "import runpy, sys, tracemalloc\n"
                   "tool, argv = sys.argv[1], sys.argv[2:]\n"
                   "sys.argv = [tool] + argv\n"
                   "tracemalloc.start()\n"
                   "try:\n"
                   "    runpy.run_path(tool, run_name=\"__main__\")\n"
                   "except SystemExit:\n"
                   "    pass   # the tool's own exit code is not what this arm measures; its verdict is\n"
                   "           # already printed by the time it exits, and the peak is read below\n"
                   "peak = tracemalloc.get_traced_memory()[1]\n"
                   "print(f\"PEAK-TRACKED {peak / 1e6:.2f} MiB\")\n")

        def tracked(table):
            out = subprocess.run([sys.executable, tracer, tool, table,
                                  "--sample-column", "SAMPLE", "--top", "1"],
                                 capture_output=True, text=True).stdout
            for line in out.splitlines():
                if line.startswith("PEAK-TRACKED"):
                    return float(line.split()[1])
            return None

        tr_small, tr_big = tracked(small_table), tracked(big_table)
        rss_small, rss_big = peak_rss(r_scale.stdout), peak_rss(r_big.stdout)
        check("50,000 values over 50,000 rows and the SAME 50,000 values over 1,000,000 rows cost the "
              "same memory: peak LIVE OBJECTS does not move when rows go up 20x with the same distinct "
              "values, which is what one counter per distinct value has to mean (and a census that kept "
              "rows cannot pass)",
              bool(tr_small) and bool(tr_big) and tr_big <= tr_small * 1.6
              and r_scale.stdout.count("distinct values: 50,000") == 1
              and r_big.stdout.count("distinct values: 50,000") == 1,
              f"tracked {tr_small} MiB -> {tr_big} MiB over 20x the rows (ratio "
              f"{(tr_big / tr_small if tr_small else 0):.2f}, ceiling 1.60 on live objects); RSS for "
              f"reference {rss_small} -> {rss_big} MiB, which is machine-dependent and not graded")

    print()
    if fails:
        print(f"  {len(fails)} selftest assertion(s) FAILED")
        return 1
    print("  all selftest assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
