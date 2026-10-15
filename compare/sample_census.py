#!/usr/bin/env python3
"""Sample census of ONE long-format evidence table, streamed: which samples are actually in it.

Why this exists
---------------
The evidence matrices a single-sample run feeds to GD — the merged BAF table, and the SD/PE/SR tables
the same pipeline writes — are the inputs whose sample content decides whether the downstream call
means anything. `ConcatBaf` merges the case's BAF with the reference panel's; when it passed only the
case, the merged table held **1 sample instead of 157**, the GD stage still ran, and nothing in the run
reported it. `ls -l` says 1.8 GB either way: a big file and a wrong file have the same size.

That question — **how many distinct samples are in this column, and are they the ones you meant** — is
not a diff, so no comparator in this directory could answer it, and `artifact_tally.py` tallies one
column's VALUES per row without ever asking what the column's cardinality means. It also has to be
answerable over a 1.8 GB BGZF table without loading it, which rules out every "read it into a dict of
rows" one-liner that a review would otherwise write:

    # the pass condition a PR review needed, printed by the tool rather than decided by a person
    python compare/sample_census.py NA12878.baf.txt.gz --sample-column SAMPLE --expect 157
    # no header at all (baf_from_sd.py writes contig/pos/baf/sample): count column 4
    python compare/sample_census.py case.baf.txt.gz --sample-column 4
    # two arms' censuses, diffed by the tool that diffs lists
    python compare/sample_census.py A.sd.txt.gz --write-samples A.samples.txt
    python compare/sample_census.py B.sd.txt.gz --write-samples B.samples.txt
    python compare/lineset_diff.py A.samples.txt B.samples.txt

Rule (fixed, so a rerun means the same thing):
  * ONE file. This is a census, not a comparison, and it never opens a second file: a census that
    quietly joined two tables would be a diff wearing a census's name. `--against FILE` is deliberately
    absent — `--write-samples` hands the list to `lineset_diff.py`, which is the tool that diffs it.
  * a first line starting `#` IS the header, and the decision is printed. With no header, a NAME
    column cannot be resolved and the run refuses, naming the first line and telling you to give a
    1-based number instead. Nothing is guessed, because guessing that line 1 is data in a file that
    had a header drops one sample, and guessing the reverse invents one.
  * `--sample-column` takes a NAME (case-insensitive against the header) or a 1-based NUMBER; the
    resolved column, its position, and the header field it landed on are all printed.
  * one pass, one counter per distinct value. Nothing else is held: peak RSS is printed, because
    "streamed" is a claim about memory and a claim nobody can check is a claim that rots.
  * values are counted AS WRITTEN. Normalization is OPT-IN and printed (`--normalize trim`,
    `--normalize case`), for the same reason `lineset_diff.py` prints it: a sample named with a trailing
    space is a distinct sample until somebody says otherwise, and a tool that folds names quietly is a
    tool that hides the cohort change you came to find.
  * blank lines and `#` lines inside the body are counted as ignored, never as rows. A row with fewer
    fields than the census column is a SHORT row: counted, shown with its line number, and excluded —
    it has no value there, and reading a missing field as `""` would manufacture a sample named nothing.
  * `.`/empty values are counted like any other value and called out separately. They are data defects
    in an evidence table, and dropping them would make the census disagree with the file.
  * `--expect N` is a declared expectation and it FAILS (exit 1) when the distinct count is not N. It
    is the difference between a tool that prints 1 and a tool that says `EXPECTED 157, FOUND 1`.

Exit status:
  0  counted rows, and `--expect` (if given) held
  1  counted rows, but `--expect` did not hold
  2  NOTHING counted: 0 data rows, a column spec that does not resolve, a header-only file, a wide
     matrix (its samples live in the column NAMES, which is `matrix_diff.py`'s shape), or an unreadable
     input. A census of nothing is never a pass.
`--selftest` writes its own fixtures — headered, headerless, wide, short-rowed, one-sample — and
asserts every rule above, including the 1-sample case that started this file.
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
import resource
import sys
from collections import Counter

# `import artifact` has to work from any cwd, so that `--help` and `--selftest` never depend on where
# the caller stood. compare/artifact.py is the envelope this file is required to emit, unmodified.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import artifact  # noqa: E402

GZIP_MAGIC = b"\x1f\x8b"
JUNK = {".", "", "-", "NA", "N/A", "nan", "None"}


def die(msg: str, code: int = 2) -> None:
    """Exit 1 already means "the expectation did not hold", so every refusal names its own code."""
    sys.stdout.flush()
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.exit(code)


def open_text(path: str):
    """Plain or gzip/BGZF, decided from the BYTES: these tables are BGZF in production, plain in a fix."""
    if os.path.isdir(path):
        die(f"{path} is a directory; name one evidence table")
    if not os.path.isfile(path):
        die(f"no such file: {path}")
    with open(path, "rb") as raw:
        magic = raw.read(2)
    if magic == GZIP_MAGIC:
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace", newline="")
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


def resolve_column(spec, header, path, first_line, delim):
    """(index, display-name) for `--sample-column`, or a refusal that says what to type instead."""
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

    # One peek decides the header, so the column is resolved BEFORE the first row is counted: a file
    # whose only line is a header must refuse as an empty census, not as "no such column".
    src = iter(open_text(args.table))
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
        """One data line: count its value, or count it as short. Nothing else is remembered."""
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
# 3 samples, 9 rows: SAMP_A 4 rows, SAMP_B 3 rows, SAMP_C 2 rows. Every expected number below is
# countable off these nine lines, including the median (sorted [2,3,4] -> 3) and the shares.
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
# SAMP_A written once with a trailing space, and SAMP_B once in lower case: five distinct strings,
# three samples. Whether those are the same sample is a decision, so it is the caller's to make.
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

        # The streaming claim, TESTED rather than printed -- measured in a unit that means the same
        # thing on both platforms. Peak RSS is not such a unit. The previous form graded RSS across 20x
        # the rows against a constant (ratio <= 1.60), which was one laptop's measurement dressed as a
        # bound: 35.4 -> 35.9 MiB here (ratio 1.01), and 28.9 -> 121.3 MiB (ratio 4.20) on the ubuntu
        # runner, red on a tool that holds nothing, because glibc keeps the high-water mark of a million
        # churned line objects even though every one is freed. Loosening the constant only slows the lie
        # down -- and a paired comparison against "a reader that holds the rows" was tried and REJECTED
        # here: a mutant census holding the raw lines passed a 2x band, because raw strings are much
        # cheaper than the split-field holder the comparison was built from. A check that a known defect
        # walks through is not a check.
        # So the quantity changed, not the constant: tracemalloc reports the PEAK OF LIVE PYTHON OBJECTS,
        # which is the thing the claim is about, and ignores what an allocator chose to retain. Streaming
        # holds one counter per distinct value, so 20x the rows must not move it. Checked against a mutant
        # made for the occasion -- a copy of this file patched to append every row to a module list, run
        # outside the tree -- which this selftest turns red, and which is NOT committed here: if a
        # row-hoarding census ever lands for real, that mutant belongs in scripts/selftest.d/ as a
        # fixture. The RSS line the tool prints is kept as information, not as a verdict.
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

        # Run the tool under the tracker. Same interpreter, same argv it would have had, one extra line
        # printed at the end; the tool's own code path is untouched, so nothing is being tested harder or
        # softer depending on which arm it is in.
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
