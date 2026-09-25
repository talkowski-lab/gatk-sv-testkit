#!/usr/bin/env python3
"""Count what ONE artifact actually contains, independently of the summary shipped with it.

Why this exists
---------------
`compare/` was pairwise-only. Every tool here answers "did these two files agree", so a branch that
wanted to re-derive a pipeline's own summary from that pipeline's own VCF had to hand-roll it. Two
reviews show what that cost. One recounted **17,789 records by eye** to check a `MOI` summary — the
number is `749 + 2506 + 2542 + 6713 + 4365 + 914` over the six MOI values, and a recount by eye is a
measurement with no rule attached to it, so nobody can reproduce it. Another read a QC table of
**10 PASS / 17 FAIL** off 28 lines with no tool, on a table whose header row itself starts with
`#METRIC` — exactly the shape that makes a comment-skipping `awk` drop the header and report nothing.

So this answers the question a pairwise tool structurally cannot: **what does this ONE artifact
actually contain, counted independently?** It reads one file. It never opens a second one, so it can
never be mistaken for evidence of agreement — a tally that matches a pipeline's own summary table
proves that summary is self-consistent, not that the calls are right.

    # re-derive a MOI summary from the VCF the pipeline wrote it from
    python compare/artifact_tally.py SM-GN4BI.moi.vcf.gz --info MOI
    # a QC table's RESULT column, by name or by position
    python compare/artifact_tally.py sv_qc.SM-GN4BI.tsv --column RESULT --column 5
    # genotype calls, per sample cell, over the whole file
    python compare/artifact_tally.py calls.vcf.gz --format GT --top 8
    # prove this is the artifact whose summary is being re-derived, in the order expected
    python compare/artifact_tally.py trio.vcf.gz --info MOI \
        --header-assert '##INFO=<ID=MOI,' --header-assert '##FORMAT=<ID=GT,' \
        --samples-order case,mother,father

Rule (fixed, so a rerun means the same thing):
  * ONE input: `.vcf`, `.vcf.gz`, `.tsv`, `.tsv.gz`. Which reader to use is decided from the BYTES
    (a first line starting `##` or `#CHROM` is a VCF whatever it is named), then from the extension;
    the name alone is never trusted, because a `*.vcf` written by a step that died halfway is real.
  * `--info KEY` counts one count per RECORD carrying the key. A value containing `|` is counted BOTH
    ways — once as the whole value, once per part — and BOTH blocks are printed and labelled,
    because "6713 records whose MOI IS `INHERITED_FROM_BOTH`" and "6713 records whose MOI MENTIONS
    it" are different claims. Only `|` splits: a comma-joined `Number=.` value is counted whole,
    never silently shredded.
  * `--format KEY` counts one count per SAMPLE CELL and says so. A record whose FORMAT string lacks
    the subfield contributes `missing cells`, not fewer samples — a no-call rate is often the very
    thing being asked about. `.`/`./.` are buckets, never dropped. FORMAT cells are never split.
  * `--column NAME|N` counts one count per DATA ROW. `N` is 1-based over the header row (for a VCF,
    over `#CHROM POS ID REF ALT QUAL FILTER INFO FORMAT` and then the sample columns). A table whose
    header row starts with `#` has that one `#` stripped for NAME matching, and the strip is printed
    — that is `sv_qc`'s shape, and the alternative is a tool that reports `RESULT` as absent.
  * `--header-assert LITERAL` (repeatable) must appear in a header line, and the line that matched is
    printed, so a too-loose assert is visible: `##INFO=<ID=MOI` also matches `MOI_CONFIDENCE`, and
    the tool says it matched two lines instead of quietly picking one.
  * `--samples-order a,b,c` is compared against the `#CHROM` sample columns EXACTLY: same names, same
    order, same count. Fields whose meaning is positional (a parent's genotype, a trio-informative
    count) are read off the wrong column when the order differs, and nothing inside a VCF records
    which sample is the case.
  * `--qc-def PATH` attributes a verdict tally to the qc_def its thresholds came from: the path is
    printed beside the tally and recorded in the artifact. The file is NEVER opened — this tool counts
    ONE artifact, and a qc_def it could read would tempt it to re-derive a cut it did not make. A
    named qc_def that does not exist is printed as missing and changes nothing about the exit code:
    "the thresholds came from a file that is gone" is still the answer to where they came from, and
    inventing its contents would be a fabrication, while failing the tally would be a different
    finding attributed to the wrong thing.
  * `--invariant SPEC` (repeatable) is a DECLARED expectation about this artifact, and breaking one
    fails the run. Two forms, no more: `info=KEY=VALUE:N` (exactly N records must carry that WHOLE
    INFO value) and `col=NAME=VALUE:N` (exactly N rows must carry that column value). N may be 0 —
    "this value must not appear at all" is the most useful invariant of the two. Every invariant is
    measured over the whole file and printed, measured against expected, whatever the ones before it
    did: a comparator that asserted "the baseline arm must have CTX == 0" behind an `elif` chain
    skipped every remaining check on exactly the samples that violated it, and reported a tidy REVIEW
    over data that had already failed (the production baseline holds 7 such records). A key or column
    that is simply not there measures 0 and says which of the four absences it is — never "skipped".
    An invariant is a CHECK, not a tally: it never counts toward "something was tallied", so it cannot
    turn an empty artifact into a green run.
  * ragged rows and rows shorter than the requested column are COUNTED and reported, never skipped
    silently. `-v` lists every bucket; `--top N` (default 20) truncates with the omitted mass
    printed, so a truncated tail is never mistaken for the whole distribution.
  * plain stdlib text for everything. pysam is optional: `--pysam-check` re-counts the `--info` keys
    through pysam and reports per key whether the two readers agree — a second independent
    implementation of the parse is the only way to know the first one was right. It is never
    required, and with no pysam installed the plain-text path does not care.

Exit status carries whether a tally happened at all:
  0  counted something, and every assertion held
  1  counted something, but an assertion FAILED: a missing header line, an unexpected sample order, or
     a violated `--invariant`. All three share this code because all three mean the same thing — the
     bytes were counted and one thing declared about them is not true. Each is named on the page, and
     a failed assertion is a finding, not an absence of result.
  2  NOTHING was tallied: a 0-byte file, a header with no rows, a requested key absent from every
     record, an unreadable or unsupported input, `--samples-order` against a table, no tally
     requested, a `--invariant` spec that is not one of the two forms, or pysam missing when
     `--pysam-check` was asked for. A run that counted nothing never
     exits 0 next to one that counted something.
`--selftest` builds its own fixtures in a temp dir, asserts every number above by hand, and pairs
every failing case with the passing case that proves the check could have fired.
"""
from __future__ import annotations

import argparse
import gzip
import io
import os
import sys
from collections import Counter

# `import artifact` has to work from any cwd, so that `--help` and `--selftest` never depend on where
# the caller stood. compare/artifact.py is the envelope this file is required to emit, unmodified.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import artifact  # noqa: E402

GZIP_MAGIC = b"\x1f\x8b"
VCF_EXTS = (".vcf", ".vcf.gz")
TABLE_EXTS = (".tsv", ".tsv.gz")
FLAG = "(flag)"                    # an INFO key written bare, with no '='
VCF_FIXED = ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT"]


def die(msg: str, code: int = 2) -> None:
    """Every cannot-tally case leaves at a code that MEANS it.

    `sys.exit("text")` is exit 1, which here already means "an assertion failed". "Nothing was
    counted" must not be able to impersonate that, so the code is explicit.
    """
    sys.stdout.flush()          # so a piped run shows the tally BEFORE the reason it failed
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.exit(code)


def open_text(path: str):
    """Plain or gzip/BGZF, decided from the bytes, never from the name."""
    if not os.path.isfile(path):
        die(f"no such file: {path}")
    with open(path, "rb") as raw:
        magic = raw.read(2)
    if magic == GZIP_MAGIC:
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace",
                                newline="")
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


def kind_of(path: str, forced: str) -> tuple:
    """('vcf'|'table', how that was decided). Content first, extension second."""
    if forced != "auto":
        return forced, f"--kind {forced}"
    first = ""
    with open_text(path) as fh:
        first = next(fh, "")
    if first.startswith("#CHROM") or first.startswith("##"):
        return "vcf", "content (the first line is a VCF header)"
    base = path[:-3].lower() if path.lower().endswith(".gz") else path.lower()
    if base.endswith(VCF_EXTS):
        return "vcf", "extension"
    if base.endswith(TABLE_EXTS):
        return "table", "extension"
    die(f"{path}: not a VCF and not a tab-separated table — extension "
        f"{os.path.splitext(path)[1] or 'none'} is not one of "
        f"{', '.join(VCF_EXTS + TABLE_EXTS)}, and the first line is not a VCF header. Point me at the "
        f"artifact itself, not at a .md5, a .tbi or a .yaml")


class Tally:
    """One requested count: the buckets, the rows that carried a value, and the rows that did not."""

    def __init__(self, kind: str, spec: str):
        self.kind = kind                       # info | format | column
        self.spec = spec                       # exactly what was typed
        self.key = spec
        self.index = None                      # column: resolved 0-based position
        self.whole: Counter = Counter()
        self.parts: Counter = Counter()        # info only: the '|'-split view
        self.rows_with = 0
        self.rows_without = 0
        self.split_rows = 0                    # records whose value contained '|'
        self.cells_missing = 0                 # format: sample cells lacking the subfield
        self.declared = None                   # info/format: declared in the header?
        self.resolved = None                   # column: the header name it resolved to

    @property
    def unit(self) -> str:
        return {"info": "record", "format": "sample cell", "column": "row"}[self.kind]

    @property
    def values_tallied(self) -> int:
        return sum(self.whole.values())

    def as_dict(self) -> dict:
        d = {"kind": self.kind, "spec": self.spec, "key": self.key, "unit": self.unit,
             "rows_with_value": self.rows_with, "rows_without_value": self.rows_without,
             "whole": dict(self.whole), "split_on_pipe": self.kind == "info",
             "rows_whose_value_was_split": self.split_rows, "per_part": dict(self.parts)}
        if self.kind == "format":
            d["cells_missing"] = self.cells_missing
        if self.kind == "column":
            d["resolved_column"] = self.resolved
            d["index_1_based"] = self.index + 1 if self.index is not None else None
        if self.declared is not None:
            d["declared_in_header"] = self.declared
        return d


def parse_info(field: str) -> dict:
    """{key: raw value} for one INFO column; a bare flag records as FLAG, and nothing is split here."""
    out = {}
    for kv in field.split(";"):
        if not kv:
            continue
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k] = v
        else:
            out[kv] = FLAG
    return out


def header_ids(lines: list, tag: str) -> set:
    """Every ID declared as `##<tag>=<ID=NAME,...`. Not a claim to trust — but `--header-assert`
    asserts against these lines, so they are read once and kept."""
    ids = set()
    prefix = f"##{tag}=<ID="
    for ln in lines:
        if ln.startswith(prefix):
            ids.add(ln[len(prefix):].split(",")[0])
    return ids


def count_parts(counter: Counter, value: str) -> bool:
    """Count `value` into `counter` once per `|`-part; True when the value really was joined."""
    parts = value.split("|")
    for p in parts:
        counter[p if p else "(empty)"] += 1
    return len(parts) > 1


INVARIANT_GRAMMAR = "info=KEY=VALUE:N or col=NAME=VALUE:N"


def parse_invariant(spec: str) -> dict:
    """One declared expectation, parsed before the file is opened. Two forms, and only two.

        info=KEY=VALUE:N   exactly N records must carry INFO KEY with the WHOLE value VALUE
        col=NAME=VALUE:N   exactly N rows must carry VALUE in the column named NAME

    `info=` matches the WHOLE value, as the WHOLE VALUE block does: a '|'-joined value is ONE value,
    not its parts, so "6713 records whose MOI IS X" is the claim being declared, never "mentions X".
    VALUE may itself contain '=' and ':' — N is read off the LAST ':' and KEY off the FIRST '='.
    N may be 0, which is the shape that mattered: "the baseline arm must have CTX == 0 records".

    A spec that is not one of the two forms dies at 2 like every other unusable argument. A
    half-parsed invariant would be worse than none, because it prints like a check that ran.
    """
    fam, feq, rest = spec.partition("=")
    if feq != "=" or fam not in ("info", "col"):
        die(f"--invariant {spec!r}: the grammar is exactly {INVARIANT_GRAMMAR} — 'info' for one INFO "
            f"key of a VCF, 'col' for one column named in the header row. Nothing else is in scope by "
            f"design: a FORMAT cell has no per-record meaning, and a third family would be a rule "
            f"nobody declared")
    head, ceq, tail = rest.rpartition(":")
    if ceq != ":":
        die(f"--invariant {spec!r}: the expected count is missing — {INVARIANT_GRAMMAR}, where N is "
            f"the count that MUST hold (0 is a valid N: the value must not appear at all)")
    try:
        expected = int(tail)
    except ValueError:
        die(f"--invariant {spec!r}: N must be a whole number, found {tail!r}")
    if expected < 0:
        die(f"--invariant {spec!r}: N cannot be negative — a count that must hold is a count")
    key, keq, value = head.partition("=")
    if keq != "=" or not key:
        die(f"--invariant {spec!r}: {'KEY' if fam == 'info' else 'NAME'} is missing — the form is "
            f"{fam}=KEY=VALUE:N")
    return {"spec": spec, "family": fam, "key": key, "value": value, "expected": expected,
            "unit": "record" if fam == "info" else "row", "measured": 0, "key_in": 0,
            "index": None, "resolved": None, "declared": None, "no_info_column": False,
            "holds": None}


def tally_file(path: str, kind: str, infos: list, formats: list, columns: list,
               invariants: list = ()) -> dict:
    """ONE pass over the file. Memory is O(distinct values), never O(rows).

    The header block is read through the same handle as the records, so a 12M-record VCF is opened
    once and every requested key is counted in that single pass.

    `invariants` (parsed by `parse_invariant`) are counted in that SAME pass and never interrupt it:
    an expectation whose key, column or whole family is absent measures 0 and carries the reason, because
    aborting on the first unmeasurable one is the `elif` bug this exists to replace — the remaining
    expectations would go unmeasured on the exact data that broke the first.
    """
    tallies = ([Tally("info", k) for k in infos] + [Tally("format", k) for k in formats]
               + [Tally("column", c) for c in columns])
    res = {"header_lines": [], "assert_lines": [], "header_row": None, "samples": [], "names": [],
           "rows": 0, "ragged": 0, "comment_rows": 0, "empty_rows": 0, "notes": [],
           "undeclared": [], "invariants": list(invariants)}
    by_spec = {t.spec: t for t in tallies}

    with open_text(path) as fh:
        # ------------------------------------------------------------------ header
        if kind == "vcf":
            for line in fh:
                if line.startswith("##"):
                    res["header_lines"].append(line.rstrip("\n"))
                    continue
                if line.startswith("#CHROM"):
                    res["header_row"] = line.rstrip("\n")
                    res["samples"] = res["header_row"].split("\t")[9:]
                    break
                if line.strip():
                    die(f"{path}: expected a VCF header, found {line[:60]!r}")
            else:
                die(f"{path}: no #CHROM line, so this cannot be tallied as a VCF — a `bgzip` write "
                    f"killed mid-header is the usual cause")
            names = VCF_FIXED + res["samples"]
            res["assert_lines"] = res["header_lines"] + [res["header_row"]]
            info_ids, format_ids = header_ids(res["header_lines"], "INFO"), \
                header_ids(res["header_lines"], "FORMAT")
        else:
            raw = None
            for line in fh:
                if line.strip():
                    raw = line.rstrip("\n")
                    break
            if raw is None:
                die(f"{path}: no content at all, so there is nothing to tally")
            names_line = raw
            if names_line.startswith("#"):
                names_line = names_line[1:]
                res["notes"].append(
                    "the header row carries a leading '#'; exactly one '#' was stripped for NAME "
                    f"matching only ({raw.split(chr(9))[0]!r} -> {names_line.split(chr(9))[0]!r}). "
                    f"That row IS the header: it is not counted among the data rows")
            names = names_line.split("\t")
            if len(names) < 2:
                die(f"{path}: the header row holds {len(names)} tab-separated field(s), so this is "
                    f"not a .tsv — a comma- or space-separated table is not what its name says")
            res["header_row"] = raw
            res["header_lines"] = [raw]
            res["assert_lines"] = [raw]
            info_ids, format_ids = set(), set()

        res["names"] = names

        # ------------------------------------------------- resolve requested columns
        for spec in columns:
            t = by_spec[spec]
            if spec.isdigit():
                i = int(spec) - 1
                if not 0 <= i < len(names):
                    die(f"--column {spec}: there is no column {spec} — this file has {len(names)} "
                        f"column(s): {names}")
                t.index, t.resolved = i, names[i]
                t.key = f"{spec}:{names[i]}"
            else:
                hits = [i for i, n in enumerate(names) if n == spec]
                if not hits:
                    die(f"--column {spec!r}: no such column — this file's columns are {names}")
                if len(hits) > 1:
                    res["notes"].append(
                        f"--column {spec!r} matches {len(hits)} columns (1-based {hits}): the FIRST "
                        f"was used, so the value is counted from that one position only")
                t.index, t.resolved = hits[0], spec

        for t in tallies:
            if t.kind in ("info", "format"):
                t.declared = t.key in (info_ids if t.kind == "info" else format_ids)
                if not t.declared:
                    res["undeclared"].append(f"{t.kind.upper()}:{t.key}")

        # -------------------------------------------------- resolve invariant targets
        # Nothing here may `die`: an invariant that cannot resolve its target still has to be counted
        # (as 0), reported, and followed by every invariant after it.
        for iv in invariants:
            if iv["family"] == "col":
                hits = [i for i, n in enumerate(names) if n == iv["key"]]
                iv["resolved"] = bool(hits)
                if hits:
                    iv["index"] = hits[0]
                    if len(hits) > 1:
                        res["notes"].append(
                            f"--invariant {iv['spec']}: column {iv['key']!r} matches {len(hits)} "
                            f"columns (1-based {hits}): the FIRST is the one measured")
            elif kind == "table":
                iv["no_info_column"] = True
            else:
                iv["declared"] = iv["key"] in info_ids

        # ------------------------------------------------------------------ rows
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                res["empty_rows"] += 1
                continue
            if line.startswith("#"):
                res["comment_rows"] += 1
                continue
            cols = line.split("\t")
            res["rows"] += 1
            if len(cols) < (len(VCF_FIXED) if kind == "vcf" else len(names)):
                res["ragged"] += 1
            info = None
            for t in tallies:
                if t.kind == "info":
                    if info is None:
                        info = parse_info(cols[7]) if len(cols) > 7 else {}
                    if t.key in info:
                        t.rows_with += 1
                        v = info[t.key]
                        t.whole[v] += 1
                        if count_parts(t.parts, v):
                            t.split_rows += 1
                    else:
                        t.rows_without += 1
                elif t.kind == "format":
                    keys = cols[8].split(":") if len(cols) > 8 else []
                    idx = keys.index(t.key) if t.key in keys else None
                    for cell in cols[9:]:
                        sub = cell.split(":")
                        if idx is None or idx >= len(sub) or sub[idx] == "":
                            t.cells_missing += 1
                        else:
                            t.rows_with += 1
                            t.whole[sub[idx]] += 1
                else:
                    if t.index >= len(cols):
                        t.rows_without += 1
                    else:
                        t.rows_with += 1
                        t.whole[cols[t.index]] += 1
            # ------------------------------------------------------------------ invariants
            # A separate loop, so that the shape of the tally loop above (including its `elif` chain)
            # can never decide whether an expectation gets measured.
            for iv in invariants:
                if iv["family"] == "info":
                    if iv["no_info_column"]:
                        continue
                    if info is None:
                        info = parse_info(cols[7]) if len(cols) > 7 else {}
                    if iv["key"] in info:
                        iv["key_in"] += 1
                        if info[iv["key"]] == iv["value"]:
                            iv["measured"] += 1
                elif iv["index"] is not None and iv["index"] < len(cols):
                    iv["key_in"] += 1
                    if cols[iv["index"]] == iv["value"]:
                        iv["measured"] += 1

    for iv in invariants:
        iv["holds"] = iv["measured"] == iv["expected"]

    res["tallies"] = tallies
    return res


def pysam_crosscheck(path: str, infos: list, by_spec: dict) -> dict:
    """Re-count the INFO keys with a different implementation. Optional by construction.

    pysam TYPES values (ints, tuples, booleans), so its scalars are normalised back to the text form
    the INFO column holds before comparing. A key that still disagrees is printed as a parser
    question — not smoothed over, because the entire value of a second reader is that it may
    disagree. The version is returned and recorded: "the two readers agreed" is a claim about a
    specific pysam, and pysam's INFO handling has moved before.
    """
    try:
        import pysam
    except ImportError as exc:
        die(f"--pysam-check needs pysam and this interpreter has none ({exc}). The plain-text path "
            f"needs no pysam at all: rerun without --pysam-check, or `make setup`")
    out = {"version": getattr(pysam, "__version__", "unknown"), "keys": {}}
    keys = out["keys"]
    for k in infos:
        keys[k] = {"whole": Counter(), "rows_with": 0, "rows_without": 0}
    with pysam.VariantFile(path) as vf:
        for rec in vf:
            for k in infos:
                try:
                    v = rec.info[k]
                except KeyError:
                    keys[k]["rows_without"] += 1
                    continue
                if v is True:
                    v = FLAG
                elif isinstance(v, tuple):
                    v = ",".join(str(x) for x in v)
                keys[k]["rows_with"] += 1
                keys[k]["whole"][str(v)] += 1
    for k in infos:
        keys[k]["agree"] = (keys[k]["whole"] == by_spec[k].whole
                            and keys[k]["rows_with"] == by_spec[k].rows_with)
    return out


def print_counter(counter: Counter, total: int, top: int, verbose: bool, unit: str) -> None:
    rows = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    shown = rows if verbose else rows[:top]
    for value, n in shown:
        text = value if len(value) <= 90 else value[:87] + "..."
        print(f"     {text:<60} {n:>12,}  {n / total:7.2%}")
    if len(rows) > len(shown):
        rest = sum(n for _, n in rows[len(shown):])
        print(f"     ... {len(rows) - len(shown):,} more value(s), {rest:,} {unit}(s) "
              f"({rest / total:.2%} of this tally) — -v lists every one")


def print_tally(t: Tally, top: int, verbose: bool, qc_def: dict = None) -> None:
    total = t.values_tallied
    label = (f"{t.kind.upper()} {t.key}" if t.kind != "column"
             else f"COLUMN {t.spec}" + (f"  ->  {t.resolved}" if t.resolved != t.spec else ""))
    print(f"\n## {label}")
    if t.kind == "format":
        print(f"  {total:,} sample cell(s) carried a value, {t.cells_missing:,} did not "
              f"(a cell missing the subfield is counted, never dropped)")
    else:
        print(f"  {total:,} {t.unit}(s) carried a value, {t.rows_without:,} did not")
    if qc_def:
        # Beside the count, not in a footnote: a page read as "10 PASS / 17 FAIL" is exactly the page
        # that has to say which qc_def drew the line between them.
        if qc_def["exists"]:
            print(f"  qc-def     {qc_def['path']}  ({qc_def['bytes']:,} byte(s)) — the file these "
                  f"thresholds came from. It is named, never opened: the cut itself is not re-derived "
                  f"here, so this tally says where the verdict rule lives, not that the rule was right")
        else:
            print(f"  qc-def     {qc_def['path']}  DOES NOT EXIST — still the answer to \"where did "
                  f"these thresholds come from\", and the one thing that cannot be recovered from these "
                  f"bytes. Nothing is invented for it and the tally is not failed by it: the verdict "
                  f"stands, the cut behind it cannot be audited from here")
    if t.declared is False:
        print("  NOT declared in the header — counted anyway, but a summary that reads this key is "
              "reading a tag no header describes")
    if not total:
        print(f"  nothing to show: no {t.unit} carried this value")
        return
    print(f"  WHOLE VALUE   one count per {t.unit}; {len(t.whole)} distinct value(s), summing to "
          f"{total:,}")
    print_counter(t.whole, total, top, verbose, t.unit)
    if t.kind != "info":
        print(f"  PER PART      not applied — {t.unit}s are counted WHOLE; only `--info` splits on '|'")
    elif t.split_rows:
        print(f"  PER PART      the {t.split_rows:,} {t.unit}(s) whose value contains '|' are counted "
              f"once per part here (the {total - t.split_rows:,} single-valued ones count once too), "
              f"so this block sums to {sum(t.parts.values()):,} over {total:,} {t.unit}(s) — NOT a "
              f"recount of the block above")
        print_counter(t.parts, sum(t.parts.values()), top, verbose, t.unit)
    else:
        print("  PER PART      not repeated: no value contained '|', so the per-part tally would be "
              "identical to the whole-value block above")


def invariant_note(iv: dict, res: dict) -> str:
    """Why the number is what it is, printed whenever a value came out 0 — because "0" from an absent
    key, from an absent column, from an absent family and from a present-but-different value are four
    DIFFERENT findings, and all four must read as a measurement taken over the whole file rather than
    as a check that never ran.
    """
    rows, tgt = res["rows"], f"{iv['key']}={iv['value']!r}"
    if iv["measured"]:
        if iv["family"] == "info" and iv["declared"] is False:
            return (f"INFO key {iv['key']} is not declared in the header — counted anyway, so this "
                    f"expectation is being held against a tag no header describes")
        return ""
    if iv["family"] == "info":
        if iv["no_info_column"]:
            return (f"this file is read as a TABLE, which has no INFO column, so 0 of {rows:,} row(s) "
                    f"could carry {tgt} however many were declared")
        if not iv["key_in"]:
            return (f"INFO key {iv['key']} appears in 0 of {rows:,} record(s)"
                    + (" and is not declared in the header" if iv["declared"] is False else "")
                    + f" — {iv['measured']} is what this file holds, measured over all {rows:,} of them")
        return (f"INFO key {iv['key']} appears in {iv['key_in']:,} record(s), but no WHOLE value among "
                f"them is {iv['value']!r} — a '|'-joined value is ONE value here, never its parts")
    if iv["resolved"] is False:
        return (f"no column {iv['key']!r} in this header, whose columns are {res['names']} — 0 of "
                f"{rows:,} row(s) could hold {iv['value']!r}, which is what was measured")
    if not iv["key_in"]:
        return (f"column {iv['key']} is in the header and holds no value in any of the {rows:,} row(s) "
                f"read — {iv['measured']} measured over all of them")
    return (f"column {iv['key']} holds a value in {iv['key_in']:,} row(s) and none of those values is "
            f"{iv['value']!r}")


def invariant_as_dict(iv: dict, res: dict) -> dict:
    """One invariant's line, in the envelope: the number measured, the number declared, the reason."""
    return {"spec": iv["spec"], "family": iv["family"], "key": iv["key"], "value": iv["value"],
            "expected": iv["expected"], "measured": iv["measured"], "holds": iv["holds"],
            "unit": iv["unit"], "rows_read": res["rows"], "target_present_in": iv["key_in"],
            "note": invariant_note(iv, res) or None}


def print_invariants(invariants: list, res: dict) -> int:
    """Print every declared invariant, and return how many were violated.

    There is no `break`, no early `return` and no `elif` in this function on purpose. The bug this
    replaces was a chain of expectations where the first failure routed around the rest, so the run
    that had the most to say said the least, and a page of good-looking REVIEW sat over data that had
    already broken its own declaration.
    """
    violated = sum(1 for iv in invariants if not iv["holds"])
    print(f"\n## --invariant   {len(invariants)} declared expectation(s), every one measured over all "
          f"{res['rows']:,} row(s) and printed whatever the ones before it decided")
    if any(iv["family"] == "info" for iv in invariants):
        print("  (an info= invariant matches the WHOLE value, as the WHOLE VALUE block does: a "
              "'|'-joined value is one value, not its parts)")
    for iv in invariants:
        note = invariant_note(iv, res)
        print(f"  {'VIOLATED' if not iv['holds'] else 'ok      '} {iv['spec']:<34} "
              f"measured {iv['measured']:,} {iv['unit']}(s) carry {iv['key']}={iv['value']}, "
              f"expected exactly {iv['expected']:,}"
              + (f"\n            note: {note}" if note else ""))
    if violated:
        rest = len(invariants) - violated
        print(f"  => {violated} of {len(invariants)} declared invariant(s) VIOLATED — a finding, not a "
              f"REVIEW: the run exits 1, the code that already means 'counted something, but an "
              f"assertion failed'"
              + (f"; the other {rest} invariant(s) were measured above all the same" if rest else
                 f"; all {len(invariants)} invariant(s) were measured above, none short-circuited "
                 f"by this one"))
    return violated


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="one file, one tally, no second file. Exit 2 means nothing was counted.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("artifact", nargs="?", help="the .vcf / .vcf.gz / .tsv / .tsv.gz to count")
    ap.add_argument("--info", action="append", default=[], metavar="KEY",
                    help="tally one INFO key (repeatable); a '|'-joined value is counted whole AND per "
                         "part")
    ap.add_argument("--format", action="append", default=[], metavar="KEY", dest="fmt",
                    help="tally one FORMAT subfield across every sample cell (repeatable)")
    ap.add_argument("--column", action="append", default=[], metavar="NAME|N",
                    help="tally one column by header name or 1-based position (repeatable)")
    ap.add_argument("--header-assert", action="append", default=[], metavar="LITERAL", dest="asserts",
                    help="this literal must appear in a header line or the run fails (repeatable)")
    ap.add_argument("--samples-order", metavar="a,b,c",
                    help="the #CHROM sample columns must be EXACTLY these names in this order")
    ap.add_argument("--qc-def", dest="qc_def", metavar="PATH",
                    help="name the qc_def these thresholds came from, beside the tally and in the "
                         "artifact. The file is NEVER opened: this tool counts one artifact, so the "
                         "path is an attribution, not a re-derivation of the cut. A path that does not "
                         "exist is printed as missing and changes no exit code")
    ap.add_argument("--invariant", action="append", default=[], metavar="SPEC", dest="invariants",
                    help=f"a DECLARED expectation that fails the run when the data breaks it "
                         f"(repeatable, every one is always measured and printed). Grammar: "
                         f"{INVARIANT_GRAMMAR} — N records (info=, WHOLE value) or N rows (col=) must "
                         f"carry VALUE; N=0 means the value must not appear at all. A check, not a "
                         f"tally: it never makes an empty artifact count as counted. Exit 1")
    ap.add_argument("--kind", choices=("auto", "vcf", "table"), default="auto",
                    help="reader override (default: content, then extension)")
    ap.add_argument("--top", type=int, default=20, help="values shown per tally (default 20)")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every value, not the top --top")
    ap.add_argument("--pysam-check", action="store_true",
                    help="re-count the --info keys with pysam and report whether the two readers agree")
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    ap.add_argument("--selftest", action="store_true",
                    help="assert the counters, the asserts and the empty cases on built-in fixtures")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.artifact:
        die("name the artifact to count (see --help)")
    path = a.artifact
    if not os.path.isfile(path):
        die(f"no such file: {path}")
    size = os.path.getsize(path)
    if size == 0:
        die(f"{path} is a 0-BYTE file: the step that was meant to write it produced nothing at all. "
            f"Nothing was tallied — and this is NOT the same failure as a header that then wrote no "
            f"records.")
    if not (a.info or a.fmt or a.column):
        die("nothing was asked for: give at least one --info KEY, --format KEY or --column NAME|N "
            "(--help lists them)")

    kind, kind_source = kind_of(path, a.kind)
    if a.samples_order is not None and kind == "table":
        die("--samples-order needs a VCF: a table has no sample columns, only header columns. Assert "
            "its header with --header-assert instead")
    # Parsed up front: a malformed spec must leave before a byte of the artifact is read, so that no
    # run can print half an invariant and call the page a result.
    invariants = [parse_invariant(s) for s in a.invariants]
    qc_def = None
    if a.qc_def is not None:
        exists = os.path.isfile(a.qc_def)
        qc_def = {"path": a.qc_def, "exists": exists,
                  "bytes": os.path.getsize(a.qc_def) if exists else None, "contents_read": False}
    res = tally_file(path, kind, a.info, a.fmt, a.column, invariants)
    tallies = res["tallies"]
    by_spec = {t.spec: t for t in tallies}

    print(f"# one-artifact tally of {path}")
    print(f"#   {kind} reader ({kind_source}); {size:,} bytes; "
          f"{len(res['header_lines']):,} header line(s); {len(res['samples']):,} sample column(s)")
    print("#   rule: --info counts one count per RECORD, whole AND per '|' part when the value is "
          "joined; --format counts one count per SAMPLE CELL, never split; --column counts one count "
          "per ROW, 1-based. Missing values are counted, not dropped. "
          + ("every value" if a.verbose else f"the top {a.top} values") + " are listed below.")
    for note in res["notes"]:
        print(f"#   note: {note}")
    if res["undeclared"]:
        print(f"#   note: {', '.join(res['undeclared'])} requested but not declared in the header")
    if res["comment_rows"]:
        print(f"#   note: {res['comment_rows']:,} '#' comment line(s) inside the data were skipped")
    if res["empty_rows"]:
        print(f"#   note: {res['empty_rows']:,} blank line(s) were skipped")
    if res["ragged"]:
        print(f"#   note: {res['ragged']:,} row(s) had FEWER fields than the header; for those rows "
              f"the requested column/key counted as missing rather than being skipped")

    for t in tallies:
        print_tally(t, a.top, a.verbose, qc_def)

    # --------------------------------------------------------- header asserts
    assert_results, asserts_ok = [], True
    if a.asserts:
        print("\n## --header-assert")
        if kind == "table":
            print("  (a table has one header row; each literal is matched against it)")
        for lit in a.asserts:
            hits = [ln for ln in res["assert_lines"] if lit in ln]
            asserts_ok = asserts_ok and bool(hits)
            print(f"  {'ok     ' if hits else 'MISSING'} {lit!r}"
                  + (f"   matched {len(hits)} header line(s): {hits[0][:110]}" if hits
                     else "   no header line contains it"))
            if len(hits) > 1:
                print("            this assert is LOOSE: it cannot tell that tag apart from its "
                      "neighbours, so tighten it (a trailing comma usually does)")
            assert_results.append({"assert": lit, "matched": len(hits),
                                   "line": hits[0] if hits else None})

    # ------------------------------------------------------------ sample order
    order_ok = None
    if a.samples_order is not None:
        want = a.samples_order.split(",")
        got = res["samples"]
        order_ok = want == got
        print(f"\n## --samples-order   ({'ok' if order_ok else 'MISMATCH'})")
        print(f"  expected {len(want)}: {', '.join(want)}")
        print(f"  found    {len(got)}: {', '.join(got) if got else '(no sample columns)'}")
        if not order_ok:
            print("  => the sample columns are NOT in this order, so every field whose meaning is "
                  "positional (whose genotype, which parent) is being read off the wrong column")

    values_tallied = sum(t.values_tallied for t in tallies)
    tallied = values_tallied > 0
    print(f"\n== TOTALS   {res['rows']:,} data row(s) read; {values_tallied:,} value(s) tallied over "
          f"{len(tallies)} tally(ies); tallied_something: {str(tallied).lower()}")

    # ------------------------------------------------- declared invariants
    # After TOTALS, so a reader who stops at "what was counted" still cannot miss what was declared
    # about it; and last among the assertions, so nothing here can be skipped past.
    invariants_ok, invariants_violated = None, None
    if invariants:
        invariants_violated = print_invariants(invariants, res)
        invariants_ok = invariants_violated == 0

    pysam_version = None
    if a.pysam_check:
        if not a.info:
            die("--pysam-check cross-checks the --info keys and none was given")
        xs = pysam_crosscheck(path, a.info, by_spec)
        pysam_version = xs["version"]
        print(f"\n## --pysam-check   pysam {pysam_version}, a second independent reader; pysam types "
              f"values, so its scalars are normalised back to the INFO text before comparing. "
              f"(htslib may print an index-file warning on stderr for an unindexed .vcf.gz: that is "
              f"noise, not a gap — this reader streams the whole file and needs no .tbi)")
        for k in a.info:
            r = xs["keys"][k]
            print(f"  {'AGREE ' if r['agree'] else 'DIFFER'} INFO {k}: pysam {r['rows_with']:,} "
                  f"record(s) / {len(r['whole'])} distinct value(s); plain text "
                  f"{by_spec[k].rows_with:,} record(s) / {len(by_spec[k].whole)} distinct value(s)"
                  + ("" if r["agree"] else
                     "   => do not pick the number you like: find out which reader is wrong before "
                     "quoting either"))

    verdict = ("COUNTED one artifact independently — no second file was read, so this is not evidence "
               "of agreement with anything, only of what these bytes contain")
    if not tallied:
        verdict = "NOTHING WAS TALLIED — see the reason on the page; this is not a result"
    elif asserts_ok is False or order_ok is False:
        verdict = "COUNTED, but an ASSERTION FAILED — this is not the artifact it was claimed to be"
    elif invariants_ok is False:
        verdict = (f"COUNTED, but {invariants_violated} DECLARED INVARIANT(S) ARE VIOLATED — the run "
                   f"declared these expectations about its own data and the data says otherwise. Exit "
                   f"1, not a REVIEW: see the VIOLATED line(s), which are every invariant, including "
                   f"the ones behind the first failure")

    artifact.write(a.json_out, artifact.envelope(
        "artifact_tally", artifact.input_file("input", path, kind=kind),
        {"what": [f"{t.kind.upper()}:{t.key}" for t in tallies],
         "unit": {"info": "one VCF record", "format": "one sample cell", "column": "one data row"},
         "pipe_split": "INFO only: a value containing '|' is counted whole AND once per part, both "
                       "printed and labelled; FORMAT cells and table columns are counted whole",
         "missing": "an absent key/cell/column is counted separately; '.'-valued cells are buckets",
         "columns": "1-based over the header row (for a VCF: #CHROM..FORMAT, then the samples)",
         "reader": "stdlib text (gzip decided from the bytes)"
                   + (f"; pysam {pysam_version} cross-checked" if pysam_version else
                      "; pysam not used"),
         "kind": kind, "kind_source": kind_source,
         "top": "every value (-v)" if a.verbose else a.top,
         "header_asserts": a.asserts, "samples_order": a.samples_order,
         "qc_def": a.qc_def,
         "invariants": a.invariants,
         "invariant_grammar": f"{INVARIANT_GRAMMAR} — N is the exact count that must hold; info= "
                              "matches the WHOLE INFO value; every invariant is measured and reported "
                              "whatever the others did; a violation exits 1"},
        {"kind": kind, "bytes": size, "header_lines": len(res["header_lines"]),
         "samples": res["samples"], "rows_read": res["rows"], "ragged_rows": res["ragged"],
         "skipped_comment_rows": res["comment_rows"], "skipped_empty_rows": res["empty_rows"],
         "notes": res["notes"], "undeclared_requested": res["undeclared"],
         "tallies": {f"{t.kind}:{t.key}": t.as_dict() for t in tallies},
         "values_tallied": values_tallied,
         "header_asserts_result": assert_results, "header_asserts_ok": asserts_ok,
         "samples_order_ok": order_ok,
         "qc_def_exists": qc_def["exists"] if qc_def else None,
         "qc_def_bytes": qc_def["bytes"] if qc_def else None,
         "qc_def_contents_read": qc_def["contents_read"] if qc_def else None,
         "invariants_result": [invariant_as_dict(iv, res) for iv in invariants],
         "invariants_ok": invariants_ok, "invariants_violated": invariants_violated,
         "pysam_version": pysam_version,
         "tallied_something": tallied},
        verdict=verdict,
        compared_something=tallied))

    # The empty cases, ordered by what they tell the reader: a 0-byte file (the step never ran), a
    # header with no rows (it ran and wrote nothing), and a key absent from every record (wrong file,
    # or wrong key) are three DIFFERENT failures and must not share one message.
    if not tallied:
        if res["rows"] == 0:
            die(f"{path} holds {size:,} bytes and {len(res['header_lines']):,} header line(s) but "
                f"ZERO data rows: the header was written and no record followed it. Nothing was "
                f"tallied — this is not a zero-count result.")
        missing = [f"{t.kind.upper()}:{t.key}" for t in tallies if not t.values_tallied]
        die(f"{path} has {res['rows']:,} data row(s), but none of "
            f"{', '.join(missing)} appeared in any of them, so nothing was tallied. Wrong artifact, "
            f"wrong key, or a step that wrote the header and dropped the field.")
    if asserts_ok is False or order_ok is False or invariants_ok is False:
        # An invariant violation joins the header-assert and sample-order failures at exit 1: the code
        # that already means "something WAS counted, and an assertion about it failed". Exit 2 stays
        # reserved for "nothing was counted", which outranks a violation by design — an empty artifact
        # cannot satisfy a declaration either, and the reason for its emptiness is the finding.
        return 1
    return 0


# --------------------------------------------------------------------------------------------- selftest
# Assertions, not a demo. Every bad case is paired with the passing case that proves the check could
# have fired: an assertion nobody demonstrated failing is indistinguishable from one that cannot.
# Every expected number below is derived BY HAND from the fixtures, not from the tool.
_ST_VCF = """##fileformat=VCFv4.1
##INFO=<ID=MOI,Number=1,Type=String,Description="Mode of inheritance">
##INFO=<ID=MOI_CONFIDENCE,Number=1,Type=String,Description="Confidence in the MOI call">
##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type of structural variant">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
##FORMAT=<ID=GQ,Number=1,Type=Integer,Description="Genotype quality">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tcase\tmother\tfather
chr1\t100\t.\tN\t<DEL>\t.\tPASS\tMOI=DE_NOVO;SVTYPE=DEL\tGT:GQ\t0/0:99\t0/0:88\t0/1:77
chr1\t200\t.\tN\t<INS>\t.\tPASS\tMOI=INHERITED_FROM_MOTHER|INHERITED_FROM_FATHER;SVTYPE=INS\tGT:GQ\t0/1:60\t0/0:50\t0/1:40
chr1\t300\t.\tN\t<DUP>\t.\tLowGQ\tSVTYPE=DUP\tGT:GQ\t./.:0\t./.:0\t./.:0
chr2\t400\t.\tN\t<BND>\t.\tPASS\tMOI=UNASSESSABLE;SVTYPE=BND\tGT\t0/1\t0/0\t0/1
"""
# The same shape the pipeline's QC table has: a leading '#' ON the header row.
_ST_TABLE = """#METRIC\tMIN\tMAX\tVALUE\tRESULT
a\t0\t1\t0.5\tPASS
b\t0\t1\t0.5\tFAIL
c\t0\t1\t0.5\tFAIL
"""
# 3 records carry MOI (rows 1,2,4); 1 does not (row 3). FORMAT=GT on row 4 has no GQ subfield, so GQ
# is missing from 3 cells (one per sample), not from 3 fewer samples. GT over 4 records x 3 samples
# = 12 cells: 0/0 four times, 0/1 five times, ./. three times.


def selftest() -> int:
    import importlib.util
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

    def run(args, cwd=None, env=None):
        # The caller's env has to actually REACH the child. This read `e = {} if env is not None`, which
        # discarded the PYTHONPATH a simulated-absence check passed in -- so the "no pysam" assertion
        # was measuring an interpreter that had pysam loaded all along: it printed AGREE from a real
        # pysam and exited 0, while its own control passed. A check that cannot fail is worse than no
        # check, because the suite reports it as coverage.
        e = dict(env) if env is not None else dict(os.environ)
        for k in list(e):                        # zero configuration: no profile, no work dir
            if k.startswith("GSVTK_"):
                del e[k]
        return subprocess.run([sys.executable, tool] + args, capture_output=True, text=True,
                              cwd=cwd or tempfile.gettempdir(), env=e)

    with tempfile.TemporaryDirectory() as dd:
        def w(name, text, gz=False):
            p = os.path.join(dd, name)
            fh = gzip.open(p, "wt") if gz else open(p, "w")
            with fh:
                fh.write(text)
            return p

        vcf = w("tally_fixture.vcf", _ST_VCF)
        gz = w("tally_fixture.vcf.gz", _ST_VCF, gz=True)
        tbl = w("tally_fixture.tsv", _ST_TABLE)
        header_only = w("header_only.vcf", "".join(_ST_VCF.splitlines(True)[:7]))
        empty = os.path.join(dd, "empty.vcf")
        open(empty, "wb").close()

        print("# exact counts on the fixture VCF (every number derived by hand from _ST_VCF)")
        art = os.path.join(dd, "artifact.json")
        p = run([vcf, "--info", "MOI", "--info", "SVTYPE", "--format", "GT", "--format", "GQ",
                 "--column", "FILTER", "--column", "2", "--json", art])
        doc = json.load(open(art)) if p.returncode == 0 and os.path.isfile(art) else {}
        t = doc.get("tallies", {})
        check("INFO + FORMAT + column in one pass exit 0 and write the envelope",
              p.returncode == 0 and bool(doc), f"exit {p.returncode}\n{p.stdout}{p.stderr}")
        moi = t.get("info:MOI", {})
        check("MOI: exactly 3 records carry it and 1 does not",
              [moi.get("rows_with_value"), moi.get("rows_without_value")] == [3, 1], str(moi))
        check("MOI whole values are exactly DE_NOVO 1, the joined value 1, UNASSESSABLE 1",
              moi.get("whole") == {"DE_NOVO": 1,
                                   "INHERITED_FROM_MOTHER|INHERITED_FROM_FATHER": 1,
                                   "UNASSESSABLE": 1}, str(moi.get("whole")))
        check("MOI per part counts the joined record TWICE: 4 parts over 3 records",
              moi.get("per_part") == {"DE_NOVO": 1, "INHERITED_FROM_MOTHER": 1,
                                      "INHERITED_FROM_FATHER": 1, "UNASSESSABLE": 1}
              and moi.get("rows_whose_value_was_split") == 1, str(moi))
        check("whole sums to 3 while per part sums to 4, so the double count is disclosed in the data",
              sum(moi.get("whole", {}).values()) == 3
              and sum(moi.get("per_part", {}).values()) == 4, str(moi))
        check("and the page SAYS which block is which",
              "WHOLE VALUE" in p.stdout and "PER PART" in p.stdout
              and "NOT a recount of the block above" in p.stdout, p.stdout[-700:])
        sv = t.get("info:SVTYPE", {})
        check("SVTYPE: 4 of 4 records, one each of DEL/INS/DUP/BND",
              [sv.get("rows_with_value"), sv.get("whole")]
              == [4, {"DEL": 1, "INS": 1, "DUP": 1, "BND": 1}], str(sv))
        check("a header-declared key is reported as declared", moi.get("declared_in_header") is True,
              str(moi))
        gt = t.get("format:GT", {})
        check("FORMAT GT: all 12 sample cells, exactly 0/0 4, 0/1 5, ./. 3",
              [gt.get("rows_with_value"), gt.get("whole")]
              == [12, {"0/0": 4, "0/1": 5, "./.": 3}], str(gt))
        gq = t.get("format:GQ", {})
        check("FORMAT GQ: the FORMAT=GT-only record gives 3 MISSING cells, not 3 fewer samples",
              [gq.get("rows_with_value"), gq.get("cells_missing"), gq.get("whole")]
              == [9, 3, {"99": 1, "88": 1, "77": 1, "60": 1, "50": 1, "40": 1, "0": 3}], str(gq))
        check("no-call './.' is a bucket, never a dropped cell", gt.get("whole", {}).get("./.") == 3,
              str(gt))
        check("COLUMN FILTER by name: PASS 3, LowGQ 1",
              t.get("column:FILTER", {}).get("whole") == {"PASS": 3, "LowGQ": 1},
              str(t.get("column:FILTER")))
        pos = t.get("column:2:POS", {})
        check("COLUMN 2 by 1-based position resolves to POS and counts 4 singletons",
              pos.get("resolved_column") == "POS" and pos.get("rows_with_value") == 4
              and set(pos.get("whole", {}).values()) == {1}, str(pos))
        check("the envelope names the tool, the rule, the hash and tallied_something",
              doc.get("tool") == "artifact_tally" and doc.get("compared_something") is True
              and doc.get("tallied_something") is True
              and len(doc.get("inputs", {}).get("input", {}).get("sha256_16", "")) == 16
              and "|" in doc["rule"]["pipe_split"]
              and doc["rule"]["unit"]["info"] == "one VCF record",
              json.dumps({k: doc.get(k) for k in ("tool", "rule", "tallied_something")})[:300])
        check("the envelope records the sample order it found",
              doc.get("samples") == ["case", "mother", "father"], str(doc.get("samples")))

        print("\n# the gzipped and the plain copy of the same bytes must agree (two readers, one truth)")
        art_gz = os.path.join(dd, "artifact_gz.json")
        pg = run([gz, "--info", "MOI", "--format", "GT", "--json", art_gz])
        dgz = json.load(open(art_gz)) if pg.returncode == 0 and os.path.isfile(art_gz) else {}
        check("the .vcf.gz tally equals the .vcf tally, read as gzip",
              pg.returncode == 0 and dgz.get("tallies")
              == {"info:MOI": t.get("info:MOI"), "format:GT": t.get("format:GT")},
              f"exit {pg.returncode}\n{pg.stdout[-300:]}{pg.stderr[-300:]}")
        check("gzip content beats the extension in both directions (name not trusted)",
              dgz.get("rule", {}).get("kind_source") == "content (the first line is a VCF header)",
              str(dgz.get("rule", {}).get("kind_source")))

        print("\n# --header-assert: a miss must fail, and the control proves the check could fire")
        c1 = run([vcf, "--info", "MOI", "--header-assert", "##INFO=<ID=MOI,"])
        check("CONTROL: a present header line exits 0", c1.returncode == 0,
              f"exit {c1.returncode}\n{c1.stdout[-300:]}{c1.stderr[-300:]}")
        check("CONTROL: the line that matched is printed, so the assert is auditable",
              "##INFO=<ID=MOI," in c1.stdout and "matched 1 header line" in c1.stdout,
              c1.stdout[-400:])
        c2 = run([vcf, "--info", "MOI", "--header-assert", "##INFO=<ID=PEDSIZE,"])
        check("a header line that is not there fails the run", c2.returncode != 0,
              f"exit {c2.returncode}")
        check("and it names the missing literal", "MISSING" in c2.stdout
              and "##INFO=<ID=PEDSIZE," in c2.stdout, c2.stdout[-400:])
        art_bad = os.path.join(dd, "artifact_badassert.json")
        run([vcf, "--info", "MOI", "--header-assert", "##INFO=<ID=PEDSIZE,", "--json", art_bad])
        dbad = json.load(open(art_bad)) if os.path.isfile(art_bad) else {}
        check("the envelope records the failed assert, and a failed assert does NOT make the tally "
              "claim it counted nothing",
              dbad.get("header_asserts_ok") is False
              and dbad.get("tallied_something") is True
              and dbad.get("header_asserts_result") == [{"assert": "##INFO=<ID=PEDSIZE,",
                                                         "matched": 0, "line": None}],
              json.dumps({k: dbad.get(k) for k in ("header_asserts_ok", "tallied_something",
                                                   "header_asserts_result")})[:400])
        c3 = run([vcf, "--info", "MOI", "--header-assert", "##INFO=<ID=MOI"])
        check("a LOOSE assert (no trailing comma) reports 2 matches, so MOI cannot hide MOI_CONFIDENCE",
              "matched 2 header line" in c3.stdout and "LOOSE" in c3.stdout, c3.stdout[-400:])

        print("\n# --samples-order: exact match, and its control")
        s1 = run([vcf, "--info", "MOI", "--samples-order", "case,mother,father"])
        check("CONTROL: the true order exits 0 and says ok",
              s1.returncode == 0 and "(ok)" in s1.stdout.split("## --samples-order")[1][:30],
              f"exit {s1.returncode}\n{s1.stdout[-300:]}")
        s2 = run([vcf, "--info", "MOI", "--samples-order", "case,father,mother"])
        check("a permuted order fails and is called a MISMATCH",
              s2.returncode == 1 and "MISMATCH" in s2.stdout, f"exit {s2.returncode}")
        check("and it prints BOTH the expected and the found order",
              "case, father, mother" in s2.stdout and "case, mother, father" in s2.stdout,
              s2.stdout[-500:])
        check("a MISMATCH still counts the records: exit 1, not exit 2", s2.returncode == 1,
              f"exit {s2.returncode}")
        s3 = run([vcf, "--info", "MOI", "--samples-order", "case,mother"])
        check("a truncation is a mismatch too, not a prefix match", s3.returncode == 1,
              f"exit {s3.returncode}")
        s4 = run([tbl, "--column", "RESULT", "--samples-order", "a,b"])
        check("--samples-order against a table refuses rather than inventing an order",
              s4.returncode == 2, f"exit {s4.returncode}")

        print("\n# nothing counted is never exit 0, and the empty cases say different things")
        e1 = run([header_only, "--info", "MOI"])
        check("a header with no records exits 2", e1.returncode == 2, f"exit {e1.returncode}")
        check("and the message names ZERO data rows plus the byte count",
              "ZERO data rows" in e1.stderr and "bytes" in e1.stderr, e1.stderr)
        e2 = run([empty, "--info", "MOI"])
        check("a 0-byte file exits 2", e2.returncode == 2, f"exit {e2.returncode}")
        check("and it says 0-BYTE, which is NOT what the header-only case says",
              "0-BYTE" in e2.stderr and "ZERO data rows" not in e2.stderr
              and "ZERO data rows" in e1.stderr, f"{e2.stderr}\n---\n{e1.stderr}")
        e3 = run([vcf, "--info", "MOI_NOT_IN_THIS_FILE"])
        check("a key absent from every record exits 2 even though 4 rows were read",
              e3.returncode == 2, f"exit {e3.returncode}")
        check("and it names the key and the rows it did read",
              "MOI_NOT_IN_THIS_FILE" in e3.stderr and "4 data row" in e3.stderr, e3.stderr)
        e4 = run([vcf, "--info", "MOI"])
        check("CONTROL: the same file with a present key exits 0", e4.returncode == 0,
              f"exit {e4.returncode}\n{e4.stdout[-300:]}{e4.stderr[-300:]}")
        art_empty = os.path.join(dd, "artifact_empty.json")
        run([header_only, "--info", "MOI", "--json", art_empty])
        de = json.load(open(art_empty)) if os.path.isfile(art_empty) else {}
        check("the envelope for an empty run says tallied_something: false and rows_read: 0",
              de.get("tallied_something") is False and de.get("compared_something") is False
              and de.get("rows_read") == 0, json.dumps(de)[:300])

        print("\n# the table path: sv_qc's shape, a '#' ON the header row")
        art_t = os.path.join(dd, "artifact_table.json")
        tb = run([tbl, "--column", "RESULT", "--column", "5", "--json", art_t])
        dtxt = json.load(open(art_t)) if tb.returncode == 0 and os.path.isfile(art_t) else {}
        check("CONTROL: a table column exits 0 and counts FAIL 2 / PASS 1 (the fixture's own rows)",
              tb.returncode == 0 and dtxt.get("tallies", {}).get("column:RESULT", {}).get("whole")
              == {"PASS": 1, "FAIL": 2},
              f"exit {tb.returncode}\n{tb.stdout[-300:]}{tb.stderr[-300:]}")
        check("the leading '#' on the header row is disclosed, not silently eaten",
              "leading '#'" in tb.stdout, tb.stdout[:600])
        check("--column 5 and --column RESULT name the same column, and 5 resolves to RESULT",
              dtxt.get("tallies", {}).get("column:5:RESULT", {}).get("resolved_column") == "RESULT",
              json.dumps(dtxt.get("tallies", {}))[:300])
        tbad = run([tbl, "--column", "VERDICT"])
        check("a column that is not there exits 2", tbad.returncode == 2, f"exit {tbad.returncode}")
        check("and it lists the columns the file actually has",
              "VERDICT" in tbad.stderr and "RESULT" in tbad.stderr, tbad.stderr)
        tno = run([tbl, "--column", "METRIC"])
        check("CONTROL: '#METRIC' is reachable as METRIC once the '#' is accounted for",
              tno.returncode == 0 and "3 row" in tno.stdout,
              f"exit {tno.returncode}\n{tno.stdout[-300:]}")

        print("\n# no profile, no work dir, no second file")
        h = run(["--help"], cwd=dd, env={})
        check("--help exits 0 with an EMPTY environment and a cwd outside the repo",
              h.returncode == 0, f"exit {h.returncode}\n{h.stderr[-400:]}")
        check("--help lists every knob the contract names",
              all(k in h.stdout for k in ("--info", "--format", "--column", "--header-assert",
                                          "--samples-order", "--qc-def", "--invariant", "--top",
                                          "-v", "--json")),
              h.stdout[:400])
        n0 = run([vcf], cwd=dd, env={})
        check("a file with no tally requested exits 2 rather than printing nothing and passing",
              n0.returncode == 2, f"exit {n0.returncode}")
        n1 = run([w("tally.vcf.md5", "d41d8cd98f00b204e9800998ecf8427e  tally.vcf\n"),
                  "--column", "1"], cwd=dd)
        check("an unsupported extension is refused by name, not misread as a table",
              n1.returncode == 2 and ".vcf" in n1.stderr and ".tsv" in n1.stderr,
              f"exit {n1.returncode}\n{n1.stderr}")
        n2 = run([os.path.join(dd, "nope.vcf"), "--info", "MOI"], cwd=dd)
        check("a missing file exits 2 and says so", n2.returncode == 2 and "no such file" in n2.stderr,
              f"exit {n2.returncode}\n{n2.stderr}")
        if importlib.util.find_spec("pysam") is None:
            print("  SKIP  pysam is not importable here, so the AGREE cross-check could not be "
                  "attempted (the two assertions below still prove the plain-text path needs it no "
                  "less)")
        else:
            pc = run([vcf, "--info", "MOI", "--info", "SVTYPE", "--pysam-check"])
            check("pysam agrees with the plain-text INFO counts (an independent parse of the same bytes)",
                  pc.returncode == 0 and pc.stdout.count("AGREE ") == 2
                  and "DIFFER" not in pc.stdout,
                  f"exit {pc.returncode}\n{pc.stdout[-600:]}")
        # The contract is that pysam is NEVER required, so the no-pysam case must be proven even in an
        # interpreter that HAS it: shadow the module with one that refuses to import.
        noshim = os.path.join(dd, "no_pysam")
        os.mkdir(noshim)
        with open(os.path.join(noshim, "pysam.py"), "w") as fh:
            fh.write("raise ImportError('simulated absence: pysam is not installed')\n")
        # Its own tiny plain VCF, so this pair of checks measures the shim and nothing else: the
        # fixture in scope above is whichever of them the loop above is standing in.
        with open(os.path.join(dd, "noshim_probe.vcf"), "w") as fh:
            fh.write("##fileformat=VCFv4.2\n"
                     '##INFO=<ID=MOI,Number=1,Type=String,Description="mode of inheritance">\n'
                     "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tSAMPLE_01\n"
                     "1\t1000\t.\tA\tT\t.\tPASS\tMOI=1\tGT\t0/1\n")
        n5 = run([vcf, "--info", "MOI", "--format", "GT"], env={"PYTHONPATH": noshim})
        check("CONTROL: with pysam UNIMPORTABLE the plain-text path still exits 0 (it is never "
              "required)", n5.returncode == 0,
              f"exit {n5.returncode}\n{n5.stdout[-200:]}{n5.stderr[-400:]}")
        n6 = run([os.path.join(dd, "noshim_probe.vcf"), "--info", "MOI", "--pysam-check"],
                 env={"PYTHONPATH": noshim})
        shim_blocks = subprocess.run([sys.executable, "-c", "import pysam"],
                                     env={"PYTHONPATH": noshim}, capture_output=True).returncode != 0
        # A selftest whose answer depends on what happens to be installed is a check that cannot say
        # what it saw. So the absence is SIMULATED (a shim that refuses to import) and the shim itself
        # is verified here, which holds on a machine with pysam and on one without it.
        check("the absence is simulated, not accidental: this shim really does block `import pysam`",
              shim_blocks, "PYTHONPATH shim did not block the import -- the two checks below would "
                          "be measuring nothing")
        check("and --pysam-check with no pysam names the fix at exit 2, rather than raising a "
              "traceback", n6.returncode == 2 and "needs pysam" in n6.stderr
              and "Traceback" not in n6.stderr,
              f"exit {n6.returncode}\n--stdout--\n{n6.stdout[-300:]}\n--stderr--\n{n6.stderr[-300:]}")

        # ----------------------------------------------------------------- --qc-def
        # The review that read "10 PASS / 17 FAIL" off an sv_qc.tsv could not say which qc_def drew
        # the line. These assertions hold the tool to the one thing that fixes it: name the file,
        # beside the count, and never pretend to have read it.
        print("\n# --qc-def: a verdict tally must name the qc_def its thresholds came from")
        # Content the tool must NEVER echo: naming the def file is an attribution, reading it would be
        # a second artifact, which this tool is defined not to open.
        qcdef = w("qc_def.tsv", "METRIC\tMIN\tMAX\nRATIO\t0.9\t1.1\nTOKEN_MUST_NOT_APPEAR\t1\n")
        gone = os.path.join(dd, "qc_def.deleted.tsv")

        def tally_block(out, marker="## COLUMN RESULT"):
            return (out.split(marker, 1)[1].split("\n##", 1)[0] if marker in out
                    else f"(no {marker!r} block on the page)")

        art_qc = os.path.join(dd, "artifact_qcdef.json")
        q1 = run([tbl, "--column", "RESULT", "--qc-def", qcdef, "--json", art_qc])
        dqc = json.load(open(art_qc)) if q1.returncode == 0 and os.path.isfile(art_qc) else {}
        qb = tally_block(q1.stdout)
        check("CONTROL: --qc-def naming a file that EXISTS exits 0 — it attributes, it does not check",
              q1.returncode == 0, f"exit {q1.returncode}\n{q1.stdout[-300:]}{q1.stderr[-300:]}")
        check("the qc_def path is printed inside the RESULT tally block, right under the PASS/FAIL "
              "count it attributes (not buried in a header note)",
              "qc-def" in qb and qcdef in qb and qb.index("qc-def") < qb.index("WHOLE VALUE"),
              qb[:600])
        check("the qc_def's CONTENTS are never read, echoed or invented (a 17-FAIL page that also "
              "printed invented thresholds would be worse than one that named nothing)",
              "TOKEN_MUST_NOT_APPEAR" not in q1.stdout and "RATIO" not in q1.stdout
              and "0.9" not in q1.stdout, qb[:600])
        check("the envelope records the path, that it exists, its size, and that nothing was read from "
              "it", dqc.get("rule", {}).get("qc_def") == qcdef
              and dqc.get("qc_def_exists") is True
              and dqc.get("qc_def_bytes") == os.path.getsize(qcdef)
              and dqc.get("qc_def_contents_read") is False,
              json.dumps({k: dqc.get(k) for k in ("qc_def_exists", "qc_def_bytes",
                                                  "qc_def_contents_read")})
              + " rule.qc_def=" + str(dqc.get("rule", {}).get("qc_def")))
        art_qc2 = os.path.join(dd, "artifact_qcdef_gone.json")
        q2 = run([tbl, "--column", "RESULT", "--qc-def", gone, "--json", art_qc2])
        dq2 = json.load(open(art_qc2)) if os.path.isfile(art_qc2) else {}
        qb2 = tally_block(q2.stdout)
        check("an ABSENT qc_def is disclosed plainly, not silently dropped: it is still the answer to "
              "'where did these thresholds come from'",
              "DOES NOT EXIST" in q2.stdout and gone in qb2 and "qc-def" in qb2,
              f"exit {q2.returncode}\n{qb2[:600]}")
        check("and it does NOT fail the tally — exit 0, tallied_something still true, the gap recorded "
              "as exists:false / bytes:null so it travels with the artifact",
              q2.returncode == 0 and dq2.get("tallied_something") is True
              and dq2.get("qc_def_exists") is False and dq2.get("qc_def_bytes") is None
              and dq2.get("rule", {}).get("qc_def") == gone,
              f"exit {q2.returncode}\n{json.dumps({k: dq2.get(k) for k in ('qc_def_exists', 'qc_def_bytes', 'tallied_something')})}")
        q3 = run([tbl, "--column", "RESULT"])
        check("CONTROL: with no --qc-def the page says nothing about any qc_def (the absent-file "
              "disclosure is not the tool's default voice)",
              q3.returncode == 0 and "qc-def" not in q3.stdout, q3.stdout[-400:])

        # ------------------------------------------------------------- --invariant
        # The defect this replaces: a comparator asserted "the baseline arm must have CTX == 0", an
        # `elif` chain skipped the remaining checks for exactly the samples that violated it (7 such
        # records in the production baseline), and the run reported a clean-looking REVIEW. So every
        # case below is read by LINE, not by exit code alone — a violation that hides the check behind
        # it is the bug, whether or not the exit code happens to be right.
        print("\n# --invariant: a DECLARED expectation, measured and printed even when the one before "
              "it just failed")

        def iv_block(out):
            return (out.split("## --invariant", 1)[1].split("\n##", 1)[0]
                    if "## --invariant" in out else "(no --invariant block on the page)")

        def iv_line(out, spec):
            for ln in iv_block(out).splitlines():
                if f"{spec} " in ln:
                    return ln.strip()
            return "(NO LINE PRINTED for this invariant — it was never measured)"

        i0 = run([tbl, "--invariant", "col=RESULT=PASS:1"])
        check("--invariant is a CHECK, not a tally: it cannot stand in for --info/--column, so an "
              "artifact that counted nothing still cannot be reported as having been counted",
              i0.returncode == 2 and "--info" in i0.stderr, f"exit {i0.returncode}\n{i0.stderr}")
        p1 = run([tbl, "--column", "RESULT", "--invariant", "col=RESULT=PASS:1"])
        check("CONTROL col=NAME=VALUE:N: the count that HOLDS (1 PASS row, hand-counted off _ST_TABLE) "
              "prints ok and exits 0",
              p1.returncode == 0 and iv_line(p1.stdout, "col=RESULT=PASS:1").startswith("ok")
              and "measured 1 row(s) carry RESULT=PASS, expected exactly 1"
              in iv_line(p1.stdout, "col=RESULT=PASS:1"),
              f"exit {p1.returncode}\n{iv_block(p1.stdout)}")
        p2 = run([tbl, "--column", "RESULT", "--invariant", "col=RESULT=PASS:2"])
        check("col= with the wrong N is VIOLATED and fails the run at exit 1 — the code this file "
              "already gives to 'counted something, but an assertion failed'. Not exit 2: the tally DID "
              "count", p2.returncode == 1, f"exit {p2.returncode}\n{iv_block(p2.stdout)}")
        check("and the line carries BOTH numbers: what it measured and what was expected",
              "measured 1 row(s) carry RESULT=PASS, expected exactly 2"
              in iv_line(p2.stdout, "col=RESULT=PASS:2"), iv_block(p2.stdout))
        p3 = run([tbl, "--column", "RESULT", "--invariant", "col=RESULT=PASS:9",
                  "--invariant", "col=RESULT=FAIL:9"])
        check("when EVERY invariant is violated the page still states that all were measured: a wall of "
              "VIOLATED lines is the correct output, not an early exit",
              p3.returncode == 1
              and iv_line(p3.stdout, "col=RESULT=PASS:9").startswith("VIOLATED")
              and iv_line(p3.stdout, "col=RESULT=FAIL:9").startswith("VIOLATED")
              and "2 of 2 declared invariant(s) VIOLATED" in p3.stdout
              and "all 2 invariant(s) were measured above" in p3.stdout,
              f"exit {p3.returncode}\n{iv_block(p3.stdout)}")
        d1 = run([vcf, "--info", "SVTYPE", "--invariant", "info=SVTYPE=DEL:1"])
        check("CONTROL info=KEY=VALUE:N: 1 record carries SVTYPE=DEL (by hand off _ST_VCF), so it "
              "holds and exits 0", d1.returncode == 0
              and iv_line(d1.stdout, "info=SVTYPE=DEL:1").startswith("ok")
              and "measured 1 record(s) carry SVTYPE=DEL, expected exactly 1"
              in iv_line(d1.stdout, "info=SVTYPE=DEL:1"),
              f"exit {d1.returncode}\n{iv_block(d1.stdout)}")
        d2 = run([vcf, "--info", "SVTYPE", "--invariant", "info=SVTYPE=DEL:0"])
        check("info=KEY=VALUE:0 on a value that IS present is VIOLATED and exits 1 — the CTX == 0 "
              "shape, where the value must not appear at all and 1 record says otherwise",
              d2.returncode == 1 and iv_line(d2.stdout, "info=SVTYPE=DEL:0").startswith("VIOLATED")
              and "measured 1 record(s) carry SVTYPE=DEL, expected exactly 0"
              in iv_line(d2.stdout, "info=SVTYPE=DEL:0"),
              f"exit {d2.returncode}\n{iv_block(d2.stdout)}")
        v1 = run([vcf, "--info", "SVTYPE", "--invariant", "info=SVTYPE=DEL:9",
                  "--invariant", "info=SVTYPE=BND:1"])
        check("THE elif BUG'S OWN SHAPE: two invariants, the FIRST violated — the SECOND is still "
              "measured, and its number is on the page",
              v1.returncode == 1
              and iv_line(v1.stdout, "info=SVTYPE=DEL:9").startswith("VIOLATED")
              and iv_line(v1.stdout, "info=SVTYPE=BND:1").startswith("ok")
              and "measured 1 record(s) carry SVTYPE=BND, expected exactly 1"
              in iv_line(v1.stdout, "info=SVTYPE=BND:1"),
              f"exit {v1.returncode}\n{iv_block(v1.stdout)}")
        v2 = run([vcf, "--info", "SVTYPE", "--invariant", "info=SVTYPE=DEL:9",
                  "--invariant", "info=SVTYPE=INS:9", "--invariant", "info=SVTYPE=DUP:1"])
        check("it does not stop after two either: three invariants give three lines, each with its own "
              "measured number, and the summary counts them",
              v2.returncode == 1
              and all(not iv_line(v2.stdout, s).startswith("(NO LINE") for s in
                      ("info=SVTYPE=DEL:9", "info=SVTYPE=INS:9", "info=SVTYPE=DUP:1"))
              and [iv_line(v2.stdout, s)[:8].strip() for s in
                   ("info=SVTYPE=DEL:9", "info=SVTYPE=INS:9")] == ["VIOLATED", "VIOLATED"]
              and "measured 1 record(s) carry SVTYPE=INS, expected exactly 9"
              in iv_line(v2.stdout, "info=SVTYPE=INS:9")
              and iv_line(v2.stdout, "info=SVTYPE=DUP:1").startswith("ok")
              and "2 of 3 declared invariant(s) VIOLATED" in v2.stdout,
              f"exit {v2.returncode}\n{iv_block(v2.stdout)}")
        v3 = run([vcf, "--info", "SVTYPE", "--invariant", "info=SVTYPE=DEL:1",
                  "--invariant", "info=SVTYPE=INS:9"])
        check("and with the violation LAST, the ok line before it does not soften it: exit 1 all the "
              "same", v3.returncode == 1
              and iv_line(v3.stdout, "info=SVTYPE=DEL:1").startswith("ok")
              and iv_line(v3.stdout, "info=SVTYPE=INS:9").startswith("VIOLATED"),
              f"exit {v3.returncode}\n{iv_block(v3.stdout)}")
        a1 = run([vcf, "--info", "MOI", "--invariant", "info=CTX=0:0"])
        check("an invariant on a key this file has never heard of MEASURES 0 — never reported as a "
              "check that did not run", a1.returncode == 0
              and iv_line(a1.stdout, "info=CTX=0:0").startswith("ok")
              and "measured 0 record(s) carry CTX=0, expected exactly 0"
              in iv_line(a1.stdout, "info=CTX=0:0"),
              f"exit {a1.returncode}\n{iv_block(a1.stdout)}")
        check("and it SAYS the key is in 0 of 4 records and undeclared, so a reader cannot mistake "
              "'nothing carries it' for 'nothing was looked at'",
              "CTX appears in 0 of 4 record(s)" in iv_block(a1.stdout)
              and "not declared in the header" in iv_block(a1.stdout), iv_block(a1.stdout))
        check("no invariant anywhere on that page is described as skipped, skipped past or omitted",
              "skip" not in iv_block(a1.stdout).lower()
              and "omit" not in iv_block(a1.stdout).lower(), iv_block(a1.stdout))
        a2 = run([vcf, "--info", "MOI", "--invariant", "info=CTX=0:7"])
        check("because 0 is a measurement it is allowed to disagree with the declaration: the absent "
              "key VIOLATES :7 and fails the run", a2.returncode == 1
              and iv_line(a2.stdout, "info=CTX=0:7").startswith("VIOLATED")
              and "measured 0 record(s) carry CTX=0, expected exactly 7"
              in iv_line(a2.stdout, "info=CTX=0:7"), f"exit {a2.returncode}\n{iv_block(a2.stdout)}")
        a3 = run([tbl, "--column", "RESULT", "--invariant", "col=VERDICT=PASS:0"])
        check("an invariant on a column the header does not have measures 0 too, and names the columns "
              "it looked among instead of dying and taking the others with it", a3.returncode == 0
              and "no column 'VERDICT' in this header" in iv_block(a3.stdout)
              and "RESULT" in iv_block(a3.stdout),
              f"exit {a3.returncode}\n{iv_block(a3.stdout)}")
        a4 = run([tbl, "--column", "RESULT", "--invariant", "info=CTX=0:0"])
        check("info= against a table measures 0 and says the FAMILY is absent, rather than reading "
              "column 8 and presenting it as INFO", a4.returncode == 0
              and "no INFO column" in iv_block(a4.stdout),
              f"exit {a4.returncode}\n{iv_block(a4.stdout)}")
        w1 = run([vcf, "--info", "MOI", "--invariant", "info=MOI=INHERITED_FROM_MOTHER:0"])
        check("info= measures the WHOLE value, never the '|'-parts: the joined record does NOT carry "
              "MOI=INHERITED_FROM_MOTHER, so 0 is right — and the page says that is the rule it used",
              w1.returncode == 0
              and "measured 0 record(s) carry MOI=INHERITED_FROM_MOTHER, expected exactly 0"
              in iv_line(w1.stdout, "info=MOI=INHERITED_FROM_MOTHER:0")
              and "WHOLE value" in iv_block(w1.stdout),
              f"exit {w1.returncode}\n{iv_block(w1.stdout)}")
        g1 = run([tbl, "--column", "RESULT", "--invariant", "format=GT:0/1:1"])
        check("a third family is refused up front at exit 2, naming the two forms that exist",
              g1.returncode == 2 and "info=KEY=VALUE:N" in g1.stderr
              and "col=NAME=VALUE:N" in g1.stderr, f"exit {g1.returncode}\n{g1.stderr}")
        g2 = run([tbl, "--column", "RESULT", "--invariant", "col=RESULT=PASS"])
        check("an invariant with no :N is refused, not read as 'expected 0' — a typo that defaults to a "
              "passing check is the worst kind of REVIEW", g2.returncode == 2
              and "--invariant" in g2.stderr, f"exit {g2.returncode}\n{g2.stderr}")
        art_inv = os.path.join(dd, "artifact_invariant.json")
        run([tbl, "--column", "RESULT", "--invariant", "col=RESULT=FAIL:9",
             "--invariant", "col=RESULT=PASS:1", "--json", art_inv])
        dinv = json.load(open(art_inv)) if os.path.isfile(art_inv) else {}
        rec = {d.get("spec"): d for d in dinv.get("invariants_result", [])}
        check("the envelope records every invariant with its measured and expected numbers, so the "
              "violation travels with the artifact",
              [[rec.get(s, {}).get(k) for k in ("measured", "holds", "expected")] for s in
               ("col=RESULT=FAIL:9", "col=RESULT=PASS:1")] == [[2, False, 9], [1, True, 1]]
              and dinv.get("invariants_ok") is False and dinv.get("invariants_violated") == 1,
              json.dumps(dinv.get("invariants_result"))[:500])
        check("a violated invariant does NOT make the tally claim it counted nothing, and the verdict "
              "names the violation rather than resting",
              dinv.get("tallied_something") is True and dinv.get("compared_something") is True
              and "DECLARED INVARIANT" in str(dinv.get("verdict")),
              json.dumps({k: dinv.get(k) for k in ("tallied_something", "verdict")})[:400])
        check("the declared specs live in `rule`, next to the rule that produced the number",
              dinv.get("rule", {}).get("invariants") == ["col=RESULT=FAIL:9", "col=RESULT=PASS:1"]
              and "info=KEY=VALUE:N" in str(dinv.get("rule", {}).get("invariant_grammar")),
              json.dumps(dinv.get("rule", {}))[:400])

    print()
    if fails:
        print(f"  {len(fails)} selftest assertion(s) FAILED")
        return 1
    print("  all selftest assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
