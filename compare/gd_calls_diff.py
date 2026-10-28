#!/usr/bin/env python3
"""A/B the GD calls table on its own key: (sample, GD_ID), with each KIND of change counted apart.

Unlike `compare/table_diff.py`, which counts cells, this classifies each change to a copy-number
call (dropped locus, cn_state change, posterior shift) and can report the case sample separately
from the reference panel.

*   `cn_probabilities` is a JSON map of copy number to probability (`{"2": 0.98, "1": 0.02}`); it is
    parsed and compared level by level, so key order and formatting do not matter.
*   `cn_state` is often empty (evaluated but not called). Empty -> value and value -> empty are
    counted as calls appearing/disappearing, not as state changes.

    # did the case sample move at all?
    python compare/gd_calls_diff.py armA/gd_cnv_calls.tsv.gz armB/gd_cnv_calls.tsv.gz \
        --case-sample NA12878
    # the panel moved, the case did not: the exit code says 1, the CASE line says 0
    python compare/gd_calls_diff.py base/gd.tsv.gz new/gd.tsv.gz --case-sample NA12878 \
        --json gd.json

Rule:
  * rows join on `(sample, GD_ID)` by name; repeated keys are counted and the first record wins.
  * four sections, each reported apart: **KEY SET** (rows present on one side only), **CN_STATE**
    (transitions, including the appeared/disappeared bucket), **CN_PROBABILITIES** (per copy-number
    level, `--prob-tol`), **OTHER COLUMNS** (every remaining shared column, numeric where both sides
    parse as numbers, exact text otherwise, missing on either side counted and not compared).
  * `--case-sample NAME` (repeatable, comma lists allowed) splits EVERY count in every section into
    the case sample and everything else, and prints the case line first.
  * `.`, `NA`, `nan`, `None` and empty are missing: counted, never compared.
  * `--ignore-column` (repeatable) drops a column from the OTHER COLUMNS section, and the count
    dropped is printed. A column present on one side only is `ONE_SIDED`, a coverage fact, not a delta.
  * side A is held in memory as a key -> row map and side B streams against it. Above `--max-keys`
    the tool refuses (narrow with `--only-column`) rather than truncating.

Exit status:
  0  same key set, no state transition, no posterior move, no other column moved
  1  compared, and at least one section found something (see the output for which, case or panel)
  2  nothing was compared: zero shared keys (usually a GD_ID namespace change between the runs), a
     table with no header, a key column missing from the header, 0 data rows, or `--max-keys` exceeded.
`--selftest` writes a four-row fixture pair and asserts every bucket above.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

# Make `import artifact` work from any cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import artifact  # noqa: E402
from artifact import die, open_text  # noqa: E402

MISSING = {"", ".", "-", "na", "n/a", "nan", "none", "null"}



def is_missing(text):
    return text is None or text.strip().lower() in MISSING


def as_number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def resolve(header, wanted, label, path, required=True):
    """Find a column by name, case-insensitively."""
    lowered = {h.lower(): h for h in header}
    if wanted.lower() in lowered:
        return lowered[wanted.lower()]
    if not required:
        return None
    die(f"{label}: no column {wanted!r} in {path}. This header has: {', '.join(header[:16])}"
        + (" …" if len(header) > 16 else "")
        + "\n  (the GD calls table is the wide `gd_cnv_calls.tsv.gz` the `call` stage writes; the "
          "legacy 6-column narrow manifest has no per-sample rows to join on)", 2)


def parse_probabilities(text):
    """`{"2": 0.98, "1": 0.02}` -> {2: 0.98, 1: 0.02}. Returns (map, error)."""
    if is_missing(text) or text.strip() == "{}":
        return {}, None
    try:
        raw = json.loads(text)
    except ValueError as exc:
        return None, f"not JSON ({exc})"
    if not isinstance(raw, dict):
        return None, "JSON is not an object of copy-number -> probability"
    out = {}
    for key, val in raw.items():
        try:
            cn = int(str(key))
            out[cn] = float(val)
        except (TypeError, ValueError):
            return None, f"entry {key!r}:{val!r} is not copy-number -> probability"
    return out, None


class Reader:
    """One GD table: the header is read at construction, then `iter_rows` streams the data rows."""

    def __init__(self, path, label):
        self.path = path
        self.label = label
        self.rows = 0
        self.blank = 0
        self.ragged = 0
        self._src = open_text(path, 'name the GD calls table from each side')
        first = None
        for line in self._src:
            text = line.rstrip("\n").rstrip("\r")
            if not text.strip():
                self.blank += 1
                continue
            if text.startswith("#"):
                die(f"{label}: {path}: a comment line before the header ({text[:60]!r}); the wide GD "
                    f"calls table starts with its header row", 2)
            first = text
            break
        if first is None:
            die(f"{label}: {path} is empty: no header row and no data rows, so nothing can be joined",
                2)
        self.header = first.split("\t")
        if len(self.header) < 2:
            die(f"{label}: {path}: header row has {len(self.header)} field(s). This is not the wide "
                f"gd_cnv_calls table (it needs a header naming sample, GD_ID and the call columns)", 2)
        if len(self.header) == 6 and not {"gd_id", "sample"} & {h.lower() for h in self.header}:
            die(f"{label}: {path} looks like the legacy 6-column NARROW GD manifest "
                f"({', '.join(self.header)}): one row per locus with the carrier samples joined into "
                f"one field. It has no per-sample rows, so (sample,GD_ID) cannot be its key. Diff it "
                f"with compare/table_diff.py keyed on the locus column, or re-run `call` to get "
                f"gd_cnv_calls.tsv.gz", 2)

    def iter_rows(self):
        """Yield (row_number, dict-of-columns) for every remaining line, padding short rows with ''."""
        for line in self._src:
            text = line.rstrip("\n").rstrip("\r")
            if not text.strip():
                self.blank += 1
                continue
            if text.startswith("#"):
                self.blank += 1
                continue
            self.rows += 1
            fields = text.split("\t")
            if len(fields) != len(self.header):
                self.ragged += 1
                fields += [""] * (len(self.header) - len(fields))
            yield self.rows, dict(zip(self.header, fields))


def fmt_counts(all_n, case_n, has_case):
    return f"{all_n:,}" + (f" (case {case_n:,})" if has_case else "")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="A/B two GD calls tables on (sample, GD_ID): key set, cn_state, cn_probabilities, "
                    "case sample and panel counted apart.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gd_a", nargs="?", help="the GD calls table of the first arm")
    ap.add_argument("gd_b", nargs="?", help="the GD calls table of the second arm")
    ap.add_argument("--sample-column", default="sample", help="default %(default)s")
    ap.add_argument("--gd-id-column", default="GD_ID", help="default %(default)s")
    ap.add_argument("--state-column", default="cn_state", help="default %(default)s")
    ap.add_argument("--prob-column", default="cn_probabilities", help="default %(default)s")
    ap.add_argument("--case-sample", action="append", default=[], metavar="NAME",
                    help="the sample(s) whose changes are the pass condition (repeatable, comma list)")
    ap.add_argument("--prob-tol", type=float, default=1e-6,
                    help="absolute tolerance on a copy-number probability (default %(default)s)")
    ap.add_argument("--tol", type=float, default=1e-9, help="relative tolerance on other numeric cells")
    ap.add_argument("--abs-tol", type=float, default=0.0, help="absolute floor for other numeric cells")
    ap.add_argument("--only-column", action="append", default=[], metavar="COL",
                    help="diff only these extra columns (repeatable; narrows memory too)")
    ap.add_argument("--ignore-column", action="append", default=[], metavar="COL", help="repeatable")
    ap.add_argument("--max-keys", type=int, default=1_000_000,
                    help="refuse above this many keys on side A (default %(default)s)")
    ap.add_argument("--max-print", type=int, default=20)
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--selftest", action="store_true",
                    help="write a fixture pair in a temp dir and assert every bucket above")
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.gd_a or not args.gd_b:
        ap.error("two GD calls tables are required: the two arms to compare")

    cases = set()
    for spec in args.case_sample:
        for name in spec.split(","):
            if name.strip():
                cases.add(name.strip())

    a = Reader(args.gd_a, args.label_a)
    b = Reader(args.gd_b, args.label_b)
    col_samp = resolve(a.header, args.sample_column, args.label_a, a.path)
    col_gd = resolve(a.header, args.gd_id_column, args.label_a, a.path)
    col_samp_b = resolve(b.header, args.sample_column, args.label_b, b.path)
    col_gd_b = resolve(b.header, args.gd_id_column, args.label_b, b.path)
    col_state = resolve(a.header, args.state_column, args.label_a, a.path, required=False)
    col_state_b = resolve(b.header, args.state_column, args.label_b, b.path, required=False)
    col_prob = resolve(a.header, args.prob_column, args.label_a, a.path, required=False)
    col_prob_b = resolve(b.header, args.prob_column, args.label_b, b.path, required=False)

    shared_cols = [c for c in a.header if c in set(b.header)]
    shared = set(shared_cols)
    lowered_shared = {c.lower(): c for c in shared}
    ignored = [c for c in shared_cols if c.lower() in {i.lower() for i in args.ignore_column}]
    special = {c for c in (col_state, col_prob) if c}
    key_cols = {col_samp, col_gd, col_samp_b, col_gd_b}
    if args.only_column:
        want, missing_want = [], []
        for name in args.only_column:
            real = lowered_shared.get(name.lower())
            (want if real else missing_want).append(real or name)
        if missing_want:
            die(f"--only-column names column(s) this pair does not share: {', '.join(missing_want)}", 2)
        extra = [c for c in a.header if c in set(want) and c not in special and c not in key_cols]
    else:
        extra = [c for c in shared_cols if c not in special
                 and c not in key_cols and c.lower() not in {i.lower() for i in args.ignore_column}]
    one_sided = [c for c in a.header if c not in set(b.header)] + \
                [c for c in b.header if c not in set(a.header)]
    keep_cols = extra + [c for c in special if c in a.header]

    print(f"# {args.label_a}: {a.path}  [{os.path.getsize(a.path):,} bytes, "
          f"sha256:{artifact.digest(a.path)}, {len(a.header)} column(s)]")
    print(f"# {args.label_b}: {b.path}  [{os.path.getsize(b.path):,} bytes, "
          f"sha256:{artifact.digest(b.path)}, {len(b.header)} column(s)]")
    print(f"# rule: join (sample,GD_ID) = ({col_samp},{col_gd}) / ({col_samp_b},{col_gd_b}) by NAME; "
          f"state column {col_state or 'ABSENT'} / {col_state_b or 'ABSENT'}; probability column "
          f"{col_prob or 'ABSENT'} / {col_prob_b or 'ABSENT'} with |Δ| > {args.prob_tol:g}; "
          f"{len(extra)} other shared column(s)"
          + (f"; ignored {len(ignored)}: {', '.join(ignored)}" if ignored else ""))
    print("# case: " + (", ".join(sorted(cases)) if cases
                        else "NOT SET — every count below is one undifferentiated bucket; "
                             "--case-sample NAME is what separates the sample you are validating "
                             "from the reference panel it was scored against"))

    # ---- side A into memory, side B streamed against it -----------------------------------------
    table = {}
    dup_a = 0
    for _, row in a.iter_rows():
        key = (row[col_samp], row[col_gd])
        if key in table:
            dup_a += 1
            continue
        if len(table) >= args.max_keys:
            die(f"{args.label_a}: more than --max-keys {args.max_keys:,} distinct (sample,GD_ID) keys. "
                f"This tool holds side A's rows to stream side B against them; narrow the retained "
                f"columns with --only-column, or raise the guard when the table is really that big.", 2)
        table[key] = {c: row.get(c, "") for c in keep_cols}
    dup_b = 0
    seen_b = set()
    counts = Counter()
    transitions = {}
    prob_detail = []
    only_b_keys = []
    cell_examples = {}
    max_prob_delta = (0.0, None)
    for _, row in b.iter_rows():
        key = (row[col_samp_b], row[col_gd_b])
        if key in seen_b:
            dup_b += 1
            continue
        seen_b.add(key)
        if key not in table:
            counts["key_only_b"] += 1
            if key[0] in cases:
                counts["key_only_b_case"] += 1
            if len(only_b_keys) < args.max_print:
                only_b_keys.append(f"{key[0]}/{key[1]}")
            continue
        counts["key_shared"] += 1
        rowa = table[key]
        is_case = key[0] in cases

        # -- cn_state -----------------------------------------------------------------------------
        if col_state and col_state_b:
            sa = rowa.get(col_state, "")
            sb = row.get(col_state, "")
            va, vb = (None if is_missing(sa) else sa.strip()), (None if is_missing(sb) else sb.strip())
            if va == vb:
                counts["state_same"] += 1
                if is_case:
                    counts["state_same_case"] += 1
            elif va is None or vb is None:
                counts["state_appeared" if va is None else "state_disappeared"] += 1
                if is_case:
                    counts[("state_appeared" if va is None else "state_disappeared") + "_case"] += 1
                transitions.setdefault(f"{va if va is not None else 'none'} -> "
                                       f"{vb if vb is not None else 'none'}", [0, 0])[0] += 1
                if is_case:
                    transitions[f"{va if va is not None else 'none'} -> "
                                f"{vb if vb is not None else 'none'}"][1] += 1
            elif as_number(va) is not None and as_number(vb) is not None and as_number(va) != as_number(vb):
                counts["state_changed"] += 1
                if is_case:
                    counts["state_changed_case"] += 1
                transitions.setdefault(f"{va} -> {vb}", [0, 0])[0] += 1
                if is_case:
                    transitions[f"{va} -> {vb}"][1] += 1
            else:
                counts["state_not_integer"] += 1
                if is_case:
                    counts["state_not_integer_case"] += 1
                transitions.setdefault(f"{va} -> {vb} (not an integer)", [0, 0])[0] += 1
                if is_case:
                    transitions[f"{va} -> {vb} (not an integer)"][1] += 1

        # -- cn_probabilities ---------------------------------------------------------------------
        if col_prob and col_prob_b:
            pa, ea = parse_probabilities(rowa.get(col_prob, ""))
            pb, eb = parse_probabilities(row.get(col_prob, ""))
            if ea or eb:
                counts["prob_unparseable"] += 1
                if is_case:
                    counts["prob_unparseable_case"] += 1
                if len(prob_detail) < args.max_print:
                    prob_detail.append(f"{key[0]}/{key[1]}: {ea or eb} in "
                                       f"{(row.get(col_prob) or '')[:60]!r}")
            elif not pa and not pb:
                counts["prob_absent_both"] += 1
            elif not pa or not pb:
                counts["prob_one_side_absent"] += 1
                if is_case:
                    counts["prob_one_side_absent_case"] += 1
            else:
                levels = sorted(set(pa) | set(pb))
                delta = max(abs(pa.get(cn, 0.0) - pb.get(cn, 0.0)) for cn in levels)
                levels_differ = set(pa) != set(pb)
                if delta > args.prob_tol:
                    counts["prob_changed"] += 1
                    if is_case:
                        counts["prob_changed_case"] += 1
                else:
                    counts["prob_same"] += 1
                    if is_case:
                        counts["prob_same_case"] += 1
                if levels_differ:
                    counts["prob_levels_differ"] += 1
                if delta > max_prob_delta[0]:
                    worst = max(levels, key=lambda cn: abs(pa.get(cn, 0.0) - pb.get(cn, 0.0)))
                    max_prob_delta = (delta, f"{key[0]}/{key[1]} CN {worst} "
                                             f"{pa.get(worst)} -> {pb.get(worst)}")

        # -- every other shared column ------------------------------------------------------------
        for col in extra:
            va, vb = rowa.get(col, ""), row.get(col, "")
            if is_missing(va) or is_missing(vb):
                counts[f"missing::{col}"] += 1
                continue
            na, nb = as_number(va), as_number(vb)
            if na is not None and nb is not None:
                same = (na == nb) or abs(na - nb) <= max(args.abs_tol,
                                                        args.tol * max(abs(na), abs(nb)))
            else:
                same = va == vb
            if not same:
                counts[f"moved::{col}"] += 1
                if is_case:
                    counts[f"moved::{col}::case"] += 1
                if col not in cell_examples:
                    cell_examples[col] = f"{key[0]}/{key[1]}: {va[:30]} -> {vb[:30]}"

    counts["rows_a"] = a.rows
    counts["rows_b"] = b.rows
    only_a_keys = [k for k in table if k not in seen_b]
    counts["key_only_a"] = len(only_a_keys)
    counts["key_only_a_case"] = sum(1 for k in only_a_keys if k[0] in cases)

    print(f"# {args.label_a}: {a.rows:,} data row(s), {len(table):,} distinct key(s)")
    print(f"# {args.label_b}: {b.rows:,} data row(s), {len(seen_b):,} distinct key(s)"
          + (f"   (blank/comment lines ignored {a.blank:,}/{b.blank:,}, ragged rows padded "
             f"{a.ragged:,}/{b.ragged:,})" if (a.blank or b.blank or a.ragged or b.ragged) else ""))
    if dup_a or dup_b:
        print(f"#   !! duplicate join keys: {args.label_a} {dup_a}, {args.label_b} {dup_b} — the first "
              f"record per key won, which is a choice, not a measurement")
    if a.ragged or b.ragged:
        print(f"#   !! rows whose field count did not match the header: A {a.ragged}, B {b.ragged} — "
              f"padded with empty cells, which makes them read as MISSING, not as different")

    if not a.rows or not b.rows:
        die(f"NOTHING COMPARED: {args.label_a} has {a.rows:,} data row(s), {args.label_b} has "
            f"{b.rows:,}. A header-only GD table agrees with nothing.", 2)

    print(f"\n== KEY SET ({col_samp}, {col_gd})")
    print(f"   shared {counts['key_shared']:,}, only in {args.label_a} "
          f"{fmt_counts(counts['key_only_a'], counts['key_only_a_case'], bool(cases))}, "
          f"only in {args.label_b} "
          f"{fmt_counts(counts['key_only_b'], counts['key_only_b_case'], bool(cases))}")
    for k in only_a_keys[:args.max_print]:
        print(f"   only in {args.label_a}: {k[0]}/{k[1]}")
    for k in only_b_keys[:args.max_print]:
        print(f"   only in {args.label_b}: {k}")
    if counts["key_shared"] == 0:
        die(f"NOTHING COMPARED: 0 shared ({col_samp},{col_gd}) keys between {a.rows:,} and "
            f"{b.rows:,} rows, so every row landed in 'only in one side'. Before believing that, "
            f"check the GD_ID namespace: a run that re-mints GD_IDs (a new reference panel, a "
            f"re-clustered locus set) produces two disjoint key sets that compare as a perfect "
            f"empty diff.", 2)

    print(f"\n== CN_STATE  (columns {col_state} / {col_state_b})")
    if col_state and col_state_b:
        print(f"   unchanged {fmt_counts(counts['state_same'], counts['state_same_case'], bool(cases))}, "
              f"changed {fmt_counts(counts['state_changed'], counts['state_changed_case'], bool(cases))}, "
              f"call appeared {fmt_counts(counts['state_appeared'], counts['state_appeared_case'], bool(cases))}, "
              f"call disappeared {fmt_counts(counts['state_disappeared'], counts['state_disappeared_case'], bool(cases))}, "
              f"not an integer {fmt_counts(counts['state_not_integer'], counts['state_not_integer_case'], bool(cases))}")
        for name, (n, ncase) in sorted(transitions.items()):
            print(f"   {name}: {fmt_counts(n, ncase, bool(cases))}")
        if not transitions:
            print("   no state moved on any shared key")
    else:
        side = args.label_a if col_state is None else args.label_b
        print(f"   NOT MEASURED: {args.state_column!r} is not a column of {side}. A section that could "
              f"not be measured is a finding about these two tables, not a pass.")

    print(f"\n== CN_PROBABILITIES  (column {col_prob}, JSON map copy-number -> probability, "
          f"|Δ| > {args.prob_tol:g} is a change)")
    if col_prob and col_prob_b:
        print(f"   changed {fmt_counts(counts['prob_changed'], counts['prob_changed_case'], bool(cases))}, "
              f"unchanged {fmt_counts(counts['prob_same'], counts['prob_same_case'], bool(cases))}, "
              f"one side has no posterior "
              f"{fmt_counts(counts['prob_one_side_absent'], counts['prob_one_side_absent_case'], bool(cases))}, "
              f"no posterior either side {counts['prob_absent_both']:,}, copy-number levels differ "
              f"{counts['prob_levels_differ']:,}, unparseable {counts['prob_unparseable']:,}")
        if max_prob_delta[1]:
            print(f"   largest |Δ| {max_prob_delta[0]:g} at {max_prob_delta[1]}")
        for line in prob_detail[:args.max_print]:
            print(f"   unparseable: {line}")
    else:
        side = args.label_a if col_prob is None else args.label_b
        print(f"   NOT MEASURED: {args.prob_column!r} is not a column of {side}, so no posterior was "
              f"compared at all.")

    print(f"\n== OTHER COLUMNS  ({len(extra)} shared column(s) beyond the key, state and posterior)")
    moved_any = False
    for col in extra:
        n = counts[f"moved::{col}"]
        miss = counts[f"missing::{col}"]
        if n or miss:
            moved_any = True
            print(f"   {col:<28} {n:,} cell(s) moved"
                  + (f" (case {counts[f'moved::{col}::case']:,})" if cases else "")
                  + (f", {miss:,} not comparable (missing on one side)" if miss else "")
                  + (f"   e.g. {cell_examples[col]}" if n and col in cell_examples else ""))
    if not moved_any:
        print("   no shared column moved and none carried a missing-vs-present pair")
    if one_sided:
        print("   ONE_SIDED columns (not diffed): " + ", ".join(one_sided))
    if ignored:
        print(f"   ignored by request, {len(ignored)} column(s): " + ", ".join(ignored))

    changed_sections = []
    if counts["key_only_a"] or counts["key_only_b"]:
        changed_sections.append("key set")
    if (counts["state_changed"] + counts["state_appeared"] + counts["state_disappeared"]
            + counts["state_not_integer"]):
        changed_sections.append("cn_state")
    if counts["prob_changed"] + counts["prob_one_side_absent"] + counts["prob_levels_differ"] \
            + counts["prob_unparseable"]:
        changed_sections.append("cn_probabilities")
    if any(counts[f"moved::{c}"] for c in extra):
        changed_sections.append("other columns")

    if cases:
        case_changes = (counts["state_changed_case"] + counts["state_appeared_case"]
                        + counts["state_disappeared_case"] + counts["state_not_integer_case"]
                        + counts["prob_changed_case"] + counts["prob_one_side_absent_case"]
                        + counts["key_only_a_case"] + counts["key_only_b_case"]
                        + sum(counts[f"moved::{c}::case"] for c in extra))
        print(f"\n# CASE {', '.join(sorted(cases))}: cn_state changes "
              f"{counts['state_changed_case'] + counts['state_appeared_case'] + counts['state_disappeared_case']},"
              f" cn_probabilities changes {counts['prob_changed_case']},"
              f" key-set changes {counts['key_only_a_case'] + counts['key_only_b_case']},"
              f" other columns {sum(counts[f'moved::{c}::case'] for c in extra)}"
              f"  -> {'CLEAN' if case_changes == 0 else 'MOVED'}")

    verdict = ("GD_IDENTICAL" if not changed_sections else "GD_DIFF[" + "+".join(changed_sections) + "]")
    if cases:
        verdict += "/CASE_" + ("CLEAN" if (counts["state_changed_case"] + counts["state_appeared_case"]
                                           + counts["state_disappeared_case"]
                                           + counts["prob_changed_case"]
                                           + counts["key_only_a_case"] + counts["key_only_b_case"]
                                           + sum(counts[f"moved::{c}::case"] for c in extra)) == 0
                              else "MOVED")
    else:
        verdict += "/CASE_NOT_SET"
    print(f"verdict: {verdict}")

    inputs = {}
    inputs.update(artifact.input_file(args.label_a, args.gd_a, rows=a.rows,
                                      distinct_keys=len(table), columns=len(a.header)))
    inputs.update(artifact.input_file(args.label_b, args.gd_b, rows=b.rows,
                                      distinct_keys=len(seen_b), columns=len(b.header)))
    doc = artifact.envelope(
        "gd_calls_diff", inputs,
        {"key": [col_samp, col_gd], "state_column": col_state, "prob_column": col_prob,
         "prob_tol": args.prob_tol, "tol": args.tol, "abs_tol": args.abs_tol,
         "case_samples": sorted(cases), "columns_diffed": extra,
         "ignore_column": args.ignore_column, "one_sided": one_sided,
         "duplicates": [dup_a, dup_b]},
        {"shared_keys": counts["key_shared"],
         "only_a": counts["key_only_a"], "only_b": counts["key_only_b"],
         "state": {"same": counts["state_same"], "changed": counts["state_changed"],
                   "appeared": counts["state_appeared"],
                   "disappeared": counts["state_disappeared"],
                   "not_integer": counts["state_not_integer"],
                   "transitions": {k: v[0] for k, v in transitions.items()},
                   "case": {k: v[1] for k, v in transitions.items()}},
         "probabilities": {"changed": counts["prob_changed"], "same": counts["prob_same"],
                           "one_side_absent": counts["prob_one_side_absent"],
                           "absent_both": counts["prob_absent_both"],
                           "levels_differ": counts["prob_levels_differ"],
                           "unparseable": counts["prob_unparseable"],
                           "max_abs_delta": max_prob_delta[0],
                           "max_abs_delta_at": max_prob_delta[1],
                           "changed_case": counts["prob_changed_case"]},
         "columns": {c: {"moved": counts[f"moved::{c}"], "moved_case": counts[f"moved::{c}::case"],
                         "missing_pairs": counts[f"missing::{c}"]} for c in extra},
         "case_samples": sorted(cases),
         "case_changes": (None if not cases else
                          counts["state_changed_case"] + counts["state_appeared_case"]
                          + counts["state_disappeared_case"] + counts["prob_changed_case"]
                          + counts["key_only_a_case"] + counts["key_only_b_case"]
                          + sum(counts[f"moved::{c}::case"] for c in extra)),
         "verdict": verdict},
        verdict=verdict, compared_something=True)
    artifact.write(args.json_out, doc)
    return 0 if not changed_sections else 1


# --------------------------------------------------------------------------------------------- selftest
# A four-row fixture pair with one planted difference per bucket.
_ST_HEADER = ("sample\tGD_ID\tchrom\tstart\tend\tsvtype\tcn_state\tcn_probabilities"
              "\tis_carrier\tqual_score\tsample_ploidy\tdosage_bins_at_median\n")
# A:  NA12878/GD_A state 2, posterior {2:0.98,1:0.02}; PANEL4/GD_ONLY_A (a panel row that vanishes);
#     PANEL1/GD_A state 2, which B calls 1 (a panel state change); PANEL2/GD_B state 1 with the same
#     posterior written in the OTHER key order and 2 dp (must NOT read as a change).
_ST_A = _ST_HEADER + (
    "NA12878\tGD_A\tchr1\t1000\t2000\tDEL\t2\t{\"2\": 0.98, \"1\": 0.02}\tTrue\t12.5\t2\t3\n"
    "PANEL4\tGD_ONLY_A\tchr1\t3000\t4000\tDUP\t1\t{\"1\": 0.9, \"2\": 0.1}\tTrue\t9\t2\t2\n"
    "PANEL1\tGD_A\tchr1\t1000\t2000\tDEL\t2\t{\"2\": 0.98, \"1\": 0.02}\tTrue\t11\t2\t3\n"
    "PANEL2\tGD_B\tchr2\t5000\t6000\tDEL\t1\t{\"1\": 0.60, \"2\": 0.30}\tTrue\t7.25\t2\t2\n")
# B:  NA12878/GD_A identical (the case did not move); PANEL1/GD_A state 2->1 with a posterior that
#     really moved; PANEL2/GD_B the SAME posterior rewritten; PANEL3/GD_C is new; PANEL4/GD_ONLY_A is
#     gone; and a duplicate key row for PANEL1/GD_A.
_ST_B = _ST_HEADER + (
    "NA12878\tGD_A\tchr1\t1000\t2000\tDEL\t2\t{\"1\": 0.02, \"2\": 0.98}\tTrue\t12.5\t2\t3\n"
    "PANEL1\tGD_A\tchr1\t1000\t2000\tDEL\t1\t{\"1\": 0.55, \"2\": 0.45}\tTrue\t11\t2\t3\n"
    "PANEL1\tGD_A\tchr1\t1000\t2000\tDEL\t1\t{\"1\": 0.55, \"2\": 0.45}\tTrue\t11\t2\t3\n"
    "PANEL2\tGD_B\tchr2\t5000\t6000\tDEL\t1\t{\"2\": 0.3, \"1\": 0.6}\tTrue\t7.25\t2\t.\n"
    "PANEL3\tGD_C\tchr3\t7000\t8000\tDUP\t3\t{\"3\": 0.99}\tTrue\t4\t2\t4\n")
# B with the case row deleted: nothing shared moved, and the case is still not clean.
_ST_C = "".join(line for line in _ST_B.splitlines(keepends=True) if not line.startswith("NA12878"))
# A table whose GD_IDs are all re-minted: must refuse with exit 2.
_ST_REKEY = _ST_HEADER + "".join(
    f"{s}\tGD_NEW_{i}\tchr1\t1000\t2000\tDEL\t2\t{{\"2\": 1.0}}\tTrue\t1\t2\t1\n"
    for i, s in enumerate(("NA12878", "PANEL1")))


def selftest() -> int:
    import gzip as _gzip
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

        a = w("gd_a.tsv", _ST_A)
        bgz = w("gd_b.tsv.gz", _ST_B, gz=True)
        c = w("gd_c.tsv", _ST_C)
        rekey = w("gd_rekey.tsv", _ST_REKEY)
        narrow = w("gd_narrow.tsv", "chr1\t1000\t2000\tGD_A\tDEL\tNA12878,PANEL1\n")
        nokey = w("gd_nokey.tsv", "sample\tLOCUS_ID\tcn_state\nNA12878\tGD_A\t2\n")

        r1 = run([a, bgz, "--case-sample", "NA12878"])
        check("the pair that started this file — the panel moved, the case did not — exits 1 AND "
              "prints the case count as 0",
              r1.returncode == 1 and "CASE NA12878" in r1.stdout and "-> CLEAN" in r1.stdout
              and "cn_state changes 0" in r1.stdout,
              f"exit {r1.returncode}\n{r1.stdout}{r1.stderr}")
        check("key set: 3 shared, 1 only in A, 1 only in B — both one-sided rows are panel rows here, "
              "and both are named",
              "shared 3, only in A 1 (case 0), only in B 1 (case 0)" in r1.stdout
              and "PANEL4/GD_ONLY_A" in r1.stdout and "PANEL3/GD_C" in r1.stdout, r1.stdout[:900])
        check("cn_state: exactly one transition, 2 -> 1, and it belongs to the panel, not the case",
              "2 -> 1: 1 (case 0)" in r1.stdout and "changed 1 (case 0)" in r1.stdout,
              "\n".join(ln for ln in r1.stdout.splitlines() if "state" in ln.lower() or "->" in ln))
        check("a posterior rewritten in another key order and precision is NOT a change (the text diff "
              "says it is, and the text diff is wrong)",
              "unchanged 2" in r1.stdout.replace("unchanged 2,", "unchanged 2")
              and "NA12878/GD_A" not in "".join(ln for ln in r1.stdout.splitlines()
                                                if "largest |" in ln),
              "\n".join(ln for ln in r1.stdout.splitlines() if "PROBABILIT" in ln or "Δ" in ln))
        check("a posterior that really moved IS a change, and the largest delta names the cell",
              "changed 1 (case 0)" in r1.stdout and "PANEL1/GD_A CN 1" in r1.stdout,
              "\n".join(ln for ln in r1.stdout.splitlines() if "Δ" in ln or "changed" in ln))
        check("qual_score 11 vs 11 is not a move, and dosage '.' is counted as missing rather than "
              "as a difference against 3",
              "dosage_bins_at_median                0" not in r1.stdout
              and "1 not comparable (missing on one side)" in r1.stdout,
              "\n".join(ln for ln in r1.stdout.splitlines() if "cell(s) moved" in ln))
        check("a duplicate (sample,GD_ID) on side B is disclosed, not folded",
              "duplicate join keys: A 0, B 1" in r1.stdout, r1.stdout[:600])
        check("the verdict names which sections differ and says the case is clean",
              "GD_DIFF[key set+cn_state+cn_probabilities]/CASE_CLEAN" in r1.stdout,
              "\n".join(ln for ln in r1.stdout.splitlines() if "verdict" in ln))

        r2 = run([a, bgz])
        check("without --case-sample the tool says so out loud instead of reporting one silent bucket",
              r2.returncode == 1 and "case: NOT SET" in r2.stdout and "CASE " not in r2.stdout,
              "\n".join(ln for ln in r2.stdout.splitlines() if "case" in ln)[:600])
        r2b = run([a, c, "--case-sample", "NA12878"])
        check("a row that simply VANISHED for the case sample is not a clean case, even though every "
              "shared cell still matches", r2b.returncode == 1
              and "key-set changes 1" in r2b.stdout and "-> MOVED" in r2b.stdout
              and "CASE_MOVED" in r2b.stdout,
              f"exit {r2b.returncode}\n{r2b.stdout[-900:]}")

        r3 = run([a, a, "--case-sample", "NA12878"])
        check("a table against itself is identical: exit 0, and every section says nothing moved",
              r3.returncode == 0 and "GD_IDENTICAL" in r3.stdout and "no state moved" in r3.stdout,
              f"exit {r3.returncode}\n{r3.stdout}{r3.stderr}")

        r4 = run([a, rekey, "--case-sample", "NA12878"])
        check("two GD_ID namespaces with NOTHING in common refuse at 2 and name the namespace trap, "
              "instead of printing an empty diff that reads like agreement",
              r4.returncode == 2 and "NOTHING COMPARED" in r4.stderr and "GD_ID namespace" in r4.stderr,
              f"exit {r4.returncode}\n{r4.stdout}{r4.stderr}")

        r5 = run([a, nokey])
        check("a table without GD_ID is refused with the columns it does have, not joined on column 1",
              r5.returncode == 2 and "no column" in r5.stderr and "LOCUS_ID" in r5.stderr,
              f"exit {r5.returncode}\n{r5.stdout}{r5.stderr}")
        r6 = run([a, narrow])
        check("the legacy narrow manifest is refused as the wrong SHAPE, naming the tool that can diff "
              "it, rather than joining on its coordinates",
              r6.returncode == 2 and "NARROW GD manifest" in r6.stderr and "table_diff" in r6.stderr,
              f"exit {r6.returncode}\n{r6.stdout}{r6.stderr}")

        r7 = run([a, bgz, "--max-keys", "2"])
        check("the memory guard is a refusal that names --only-column, never a truncated comparison",
              r7.returncode == 2 and "--only-column" in r7.stderr,
              f"exit {r7.returncode}\n{r7.stdout}{r7.stderr}")

        r8 = run([a, bgz, "--only-column", "qual_score", "--only-column", "nope"])
        check("--only-column naming a column the pair does not share is refused up front",
              r8.returncode == 2 and "does not share" in r8.stderr,
              f"exit {r8.returncode}\n{r8.stdout}{r8.stderr}")

        art = os.path.join(dd, "gd.json")
        run([a, bgz, "--case-sample", "NA12878", "--json", art])
        doc = json.load(open(art)) if os.path.isfile(art) else {}
        check("the artifact carries the four sections apart, with the case counts beside each",
              doc.get("tool") == "gd_calls_diff" and doc.get("shared_keys") == 3
              and doc.get("state", {}).get("changed") == 1
              and doc.get("state", {}).get("case", {}).get("2 -> 1") == 0
              and doc.get("probabilities", {}).get("changed_case") == 0
              and doc.get("case_changes") == 0 and doc.get("compared_something") is True,
              json.dumps({k: doc.get(k) for k in ("shared_keys", "state", "probabilities",
                                                  "case_changes")})[:600])
        check("the rule recorded in the artifact names the key columns and the tolerance used",
              doc.get("rule", {}).get("key") == ["sample", "GD_ID"]
              and doc.get("rule", {}).get("prob_tol") == 1e-06
              and doc.get("rule", {}).get("case_samples") == ["NA12878"],
              json.dumps(doc.get("rule", {}))[:400])
        check("gz is read from the bytes: the same table plain and gz gives the same verdict",
              run([a, w("gd_b_plain.tsv", _ST_B)]).stdout.count("GD_DIFF") == 1
              and run([a, bgz]).stdout.split("verdict:")[1] == run([a, w("gd_b_plain.tsv", _ST_B)]).stdout.split("verdict:")[1],
              "plain vs gz verdicts differ")

    print()
    if fails:
        print(f"  {len(fails)} selftest assertion(s) FAILED")
        return 1
    print("  all selftest assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
