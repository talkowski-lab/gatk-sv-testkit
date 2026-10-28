#!/usr/bin/env python3
"""Record-body equality between two VCFs, with the volatile header lines put where a reader can see them.

Two runs of the same pipeline rarely produce byte-identical VCFs: tools such as `bcftools` rewrite
header lines with timestamps and temp paths. This compares the record bodies instead, with one
hash per side over the record lines only:

    # the two arms of a run, one header rewrite and one temp path apart
    python compare/vcf_body_diff.py armA/final.vcf.gz armB/final.vcf.gz
    # a body that differs only in an INFO key the pipeline legitimately recomputes
    python compare/vcf_body_diff.py base.vcf.gz new.vcf.gz --drop-info AC --drop-info AF
    # two call sets that are the same records in a different order
    python compare/vcf_body_diff.py base.vcf.gz new.vcf.gz --unordered --json body.json

`site_set_diff.py` reports which sites came back and `vcf_paired_diff.py` compares one FORMAT tag on
matched cells; this checks that every record is identical, e.g. for a no-op refactor.

Rule:
  * the BODY is every line that does not start with `#`. Header lines never enter the body digest.
  * VOLATILE header lines are matched by regex, listed in the output, and recorded in the artifact.
    Default: `##bcftools_*Command=`, `##GATKCommandLine.`, `##fileDate=` (timestamps, temp paths,
    hostnames). Any other header difference is reported as real. `--volatile REGEX` extends the list;
    dropped lines are still printed.
  * #CHROM sample columns are compared by name. If the sample sets or order differ, that is reported
    as a cohort difference and sample cells are not compared.
  * default order is file order (record N against record N). `--unordered` joins records on `--key`
    (default `CHROM,POS,ID,REF,ALT`) and reports key-set differences; it holds both bodies in memory,
    bounded by `--max-records`.
  * `--drop-column NAME` (a fixed column: ID, QUAL, FILTER, …) and `--drop-info KEY` remove a field
    before comparing. Both are printed and both change the digest.
  * a differing record pair is reported by record key and the name of each differing column (and
    each differing INFO key).

Exit status:
  0  bodies identical under the stated rule, no real header difference, same sample columns
  1  compared, and something differs: a record body, a non-volatile header line, or the sample columns
  2  NOTHING was compared: either side has 0 record lines (a header-only VCF proves nothing), no
     #CHROM line, a dropped column that does not exist, or `--unordered` over `--max-records`.
`--selftest` builds its own fixtures in a temp dir and asserts each case, including identical bodies
with different headers and compression.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import os
import re
import sys
from collections import Counter

# Make `import artifact` work from any cwd.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import artifact  # noqa: E402
from artifact import die, open_text  # noqa: E402

FIXED = ["#CHROM", "POS", "ID", "REF", "ALT", "QUAL", "FILTER", "INFO", "FORMAT"]
# Header lines that change on every re-run (timestamps, temp paths, hostnames).
VOLATILE_DEFAULT = [r"^##bcftools_[A-Za-z_]*Command=", r"^##GATKCommandLine\.", r"^##fileDate="]
FLAG_TOKEN = "\0flag"        # a bare INFO key (`PASS`), distinct from `KEY=` with an empty value



class Body:
    """One VCF, read once: the header (`meta`, `chrom_fields`), then the record lines as a stream."""

    def __init__(self, path, label):
        self.path = path
        self.label = label
        self.meta = []
        self.chrom_fields = None
        self.blank = 0
        self._it = iter(open_text(path, 'name one VCF from each side'))
        self._header_done = False

    def read_header(self):
        if self._header_done:
            return
        for line in self._it:
            text = line.rstrip("\n").rstrip("\r")
            if not text.strip():
                self.blank += 1
                continue
            if text.startswith("#"):
                if text.startswith("#CHROM"):
                    self.chrom_fields = text.split("\t")
                    self._header_done = True
                    return
                self.meta.append(text)
                continue
            die(f"{self.label}: {self.path}: data line before the #CHROM header line "
                f"(line {len(self.meta) + self.blank + 1}): {text[:80]!r}", 2)
        die(f"{self.label}: {self.path} has no #CHROM line, so it is not a VCF and has no record body "
            f"({len(self.meta)} header line(s) read)", 2)

    def records(self):
        """Yield every remaining (record) line in file order."""
        for line in self._it:
            text = line.rstrip("\n").rstrip("\r")
            if not text.strip():
                self.blank += 1
                continue
            yield text
        return


def column_plan(reader, drop_columns, label):
    """Return names and indices of the compared columns, or exit naming what is missing."""
    reader.read_header()
    fields = reader.chrom_fields
    names = list(FIXED) + fields[len(FIXED):]
    drop = []
    for name in drop_columns:
        if name not in names:
            die(f"{label}: --drop-column {name!r} is not a column of {reader.path}. "
                f"This side has: {', '.join(names[:12])}"
                + (" …" if len(names) > 12 else ""), 2)
        drop.append(name)
    keep = [i for i, n in enumerate(names) if n not in drop]
    return names, keep, drop


def drop_info_tokens(text, info_idx, drop_info):
    """Return INFO with the named keys removed."""
    if not drop_info:
        return text
    fields = text.split("\t")
    if info_idx >= len(fields):
        return text
    kept = [t for t in fields[info_idx].split(";")
            if t and t.split("=", 1)[0] not in drop_info]
    fields[info_idx] = ";".join(kept) if kept else "."
    return "\t".join(fields)


def normalise(text, keep, info_idx, drop_info):
    """The record line as the rule sees it: kept columns only, dropped INFO keys removed."""
    text = drop_info_tokens(text, info_idx, drop_info)
    fields = text.split("\t")
    return "\t".join(fields[i] if i < len(fields) else "" for i in keep)


def info_diff(a_text, b_text, info_idx, drop_info):
    """Return the names of INFO keys whose values differ."""
    def tokens(text):
        fields = text.split("\t")
        if info_idx >= len(fields) or fields[info_idx] == ".":
            return {}
        out = {}
        for tok in fields[info_idx].split(";"):
            if not tok:
                continue
            key, sep, val = tok.partition("=")
            out[key] = val if sep else FLAG_TOKEN
        return out
    ta, tb = tokens(drop_info_tokens(a_text, info_idx, drop_info)), \
        tokens(drop_info_tokens(b_text, info_idx, drop_info))
    moved = []
    for key in sorted(set(ta) | set(tb)):
        if key not in tb:
            moved.append(f"-{key}")
        elif key not in ta:
            moved.append(f"+{key}")
        elif ta[key] != tb[key]:
            moved.append(key)
    return moved


def sample_report(fa, fb):
    """Compare the #CHROM sample columns by name. Returns (kind, detail)."""
    sa, sb = fa[len(FIXED):], fb[len(FIXED):]
    if sa == sb:
        return ("same", f"{len(sa)} sample column(s), same names in the same order")
    if sorted(sa) == sorted(sb):
        return ("reordered", f"{len(sa)} sample column(s), same names, DIFFERENT ORDER: "
                             f"{sa[:6]}{' …' if len(sa) > 6 else ''} vs {sb[:6]}"
                             f"{' …' if len(sb) > 6 else ''}")
    only_a = [s for s in sa if s not in sb]
    only_b = [s for s in sb if s not in sa]
    return ("differ", f"A {len(sa)} sample column(s), B {len(sb)}: "
                      f"only in A {len(only_a)} {only_a[:6]}{' …' if len(only_a) > 6 else ''}, "
                      f"only in B {len(only_b)} {only_b[:6]}{' …' if len(only_b) > 6 else ''}")


def header_report(meta_a, meta_b, patterns, max_print):
    """Every header difference, split into volatile and real, with the volatile ones listed by name."""
    ca, cb = Counter(meta_a), Counter(meta_b)
    differing = sorted((ca - cb) + (cb - ca))
    rx = [re.compile(p) for p in patterns]
    volatile, real = [], []
    for line in differing:
        (volatile if any(r.search(line) for r in rx) else real).append(line)
    shown = differing[:max_print]
    return {"differing": len(differing), "volatile": volatile, "real": real, "shown": shown}


def ordered_compare(a, b, keep, info_idx, drop_info, max_print, colnames):
    """Compare record N against record N, streaming both sides."""
    da = hashlib.sha256()
    db = hashlib.sha256()
    n_a = n_b = 0
    missing_a = missing_b = 0
    pairs_differing = 0
    examples = []
    for la, lb in itertools.zip_longest(a, b, fillvalue=None):
        if la is not None:
            n_a += 1
        if lb is not None:
            n_b += 1
        if la is None or lb is None:
            if la is None:
                missing_a += 1
            else:
                missing_b += 1
            shown = la if la is not None else lb
            if len(examples) < max_print:
                examples.append(("record with no partner on the other side", (shown or "")[:120]))
            continue
        na = normalise(la, keep, info_idx, drop_info)
        nb = normalise(lb, keep, info_idx, drop_info)
        da.update((na + "\n").encode("utf-8", "replace"))
        db.update((nb + "\n").encode("utf-8", "replace"))
        if na == nb:
            continue
        pairs_differing += 1
        if len(examples) < max_print:
            fa, fb = la.split("\t"), lb.split("\t")
            cols = [colnames[i] if i < len(colnames) else f"col{i + 1}" for i in keep
                    if (fa[i] if i < len(fa) else "") != (fb[i] if i < len(fb) else "")]
            moved = info_diff(la, lb, info_idx, drop_info)
            if moved:
                cols = [c for c in cols if c != "INFO"] + ["INFO{" + ",".join(moved) + "}"]
            key = "/".join((fa[i] if i < len(fa) else "?") for i in (0, 1, 2))
            examples.append((key, ", ".join(cols)))
    return {"mode": "file order", "records_a": n_a, "records_b": n_b,
            "unpaired_a": missing_b, "unpaired_b": missing_a, "pairs_differing": pairs_differing,
            "digest_a": da.hexdigest(), "digest_b": db.hexdigest(), "examples": examples}


def unordered_compare(a, b, key_idx, keep, info_idx, drop_info, max_print, max_records, colnames):
    """Join records on a key so order does not matter. Holds both sides in memory."""
    def keyed(reader, label):
        table = {}
        dupes = 0
        count = 0
        for line in reader:
            count += 1
            if count > max_records:
                die(f"--unordered holds every record of both sides in memory to sort them; "
                    f"{label} passed --max-records {max_records:,}. Raise the guard or drop "
                    f"--unordered (file order streams and holds nothing).", 2)
            fields = line.split("\t")
            # Take the key from the raw line, since --drop-column changes the normalised one.
            key = tuple(fields[i] if i < len(fields) else "" for i in key_idx)
            if key in table:
                dupes += 1                          # the first record per key wins
                continue
            table[key] = (normalise(line, keep, info_idx, drop_info), line)
        return table, count, dupes

    ta, na, dup_a = keyed(a, "A")
    tb, nb, dup_b = keyed(b, "B")
    shared = sorted(set(ta) & set(tb))
    only_a = sorted(set(ta) - set(tb))
    only_b = sorted(set(tb) - set(ta))
    moved = [k for k in shared if ta[k][0] != tb[k][0]]
    da = hashlib.sha256()
    db = hashlib.sha256()
    for k in sorted(set(ta)):
        da.update(("\t".join(k) + "\0" + ta[k][0] + "\n").encode("utf-8", "replace"))
    for k in sorted(set(tb)):
        db.update(("\t".join(k) + "\0" + tb[k][0] + "\n").encode("utf-8", "replace"))
    examples = []
    for k in moved[:max_print]:
        fa, fb = ta[k][1].split("\t"), tb[k][1].split("\t")
        cols = [colnames[i] if i < len(colnames) else f"col{i + 1}" for i in keep
                if (fa[i] if i < len(fa) else "") != (fb[i] if i < len(fb) else "")]
        moved_info = info_diff(ta[k][1], tb[k][1], info_idx, drop_info)
        if moved_info:
            cols = [c for c in cols if c != "INFO"] + ["INFO{" + ",".join(moved_info) + "}"]
        examples.append(("/".join(k), "; ".join(cols)))
    return {"mode": "joined on " + ",".join("col%d" % (i + 1) for i in key_idx),
            "records_a": na, "records_b": nb, "shared": len(shared),
            "only_a": len(only_a), "only_b": len(only_b), "duplicates_a": dup_a,
            "duplicates_b": dup_b, "pairs_differing": len(moved),
            "digest_a": da.hexdigest(), "digest_b": db.hexdigest(), "examples": examples,
            "only_a_examples": ["\t".join(k) for k in only_a[:max_print]],
            "only_b_examples": ["\t".join(k) for k in only_b[:max_print]]}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Are two VCFs' RECORD BODIES identical, whatever their headers say?",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("vcf_a", nargs="?", help="the first VCF (.vcf or .vcf.gz)")
    ap.add_argument("vcf_b", nargs="?", help="the second VCF (.vcf or .vcf.gz)")
    ap.add_argument("--selftest", action="store_true",
                    help="build a fixture pair in a temp dir and assert every rule above")
    ap.add_argument("--unordered", action="store_true",
                    help="join records on --key instead of comparing file order")
    ap.add_argument("--key", default="CHROM,POS,ID,REF,ALT",
                    help="comma list of fixed column names for --unordered (default %(default)s)")
    ap.add_argument("--drop-column", action="append", default=[], metavar="NAME",
                    help="compare as if this column did not exist (repeatable)")
    ap.add_argument("--drop-info", action="append", default=[], metavar="KEY",
                    help="compare as if this INFO key did not exist (repeatable)")
    ap.add_argument("--volatile", action="append", default=[], metavar="REGEX",
                    help="extra header-line pattern to treat as volatile (repeatable)")
    ap.add_argument("--max-records", type=int, default=2_000_000,
                    help="--unordered only: refuse above this many records per side (default %(default)s)")
    ap.add_argument("--max-print", type=int, default=20)
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.vcf_a or not args.vcf_b:
        ap.error("two VCFs are required: the file to compare and the file to compare it against")

    patterns = list(VOLATILE_DEFAULT) + list(args.volatile)
    for p in patterns:                       # validate regexes before reading records
        try:
            re.compile(p)
        except re.error as exc:
            die(f"--volatile {p!r} is not a valid regular expression: {exc}", 2)

    for label, path in ((args.label_a, args.vcf_a), (args.label_b, args.vcf_b)):
        if os.path.isdir(path):
            die(f"{label}: {path} is a directory; name one VCF")

    a = Body(args.vcf_a, args.label_a)
    b = Body(args.vcf_b, args.label_b)
    names_a, keep_a, dropped_a = column_plan(a, args.drop_column, args.label_a)
    names_b, keep_b, dropped_b = column_plan(b, args.drop_column, args.label_b)
    if keep_a[:len(FIXED)] != keep_b[:len(FIXED)]:
        die(f"the nine fixed columns do not line up between the two files "
            f"({args.label_a} header: {','.join(a.chrom_fields[:len(FIXED)])}; "
            f"{args.label_b} header: {','.join(b.chrom_fields[:len(FIXED)])}), so there is no shared "
            f"body layout to compare. A different NUMBER of sample columns is not this refusal: that "
            f"is reported as SAMPLES-DIFFER", 2)
    keep = keep_a
    info_idx = FIXED.index("INFO")
    key_idx = []
    if args.unordered:
        for name in [k.strip().upper().lstrip("#") for k in args.key.split(",") if k.strip()]:
            if name not in [n.upper().lstrip("#") for n in FIXED]:
                die(f"--key {name!r} is not one of the fixed VCF columns "
                    f"({', '.join(FIXED)}); sample columns cannot be a join key here", 2)
            key_idx.append([n.upper().lstrip("#") for n in FIXED].index(name))
        if not key_idx:
            die("--key is empty, so --unordered has nothing to join on", 2)

    for label, reader in ((args.label_a, a), (args.label_b, b)):
        print(f"# {label}: {reader.path}  [{os.path.getsize(reader.path):,} bytes, "
              f"sha256:{artifact.digest(reader.path)}, {len(reader.meta)} header line(s)]")
    hdr = header_report(a.meta, b.meta, patterns, args.max_print)
    kind, detail = sample_report(a.chrom_fields, b.chrom_fields)
    if kind != "same":
        # Sample names or order differ, so cells can't be compared positionally.
        keep = [i for i in keep if i < len(FIXED)]
    rule_words = (("joined on " + args.key) if args.unordered else "file order") + \
                 (f"; dropped columns {','.join(dropped_a)}" if dropped_a else "") + \
                 (f"; dropped INFO keys {','.join(args.drop_info)}" if args.drop_info else "") + \
                 ("" if kind == "same" else f"; sample columns not compared ({kind})")
    print(f"# rule: {len(keep)} of {len(names_a)} column(s) compared; body order: {rule_words}")

    if args.unordered:
        res = unordered_compare(a.records(), b.records(), key_idx, keep, info_idx,
                                args.drop_info, args.max_print, args.max_records, names_a)
    else:
        res = ordered_compare(a.records(), b.records(), keep, info_idx, args.drop_info,
                              args.max_print, names_a)

    print(f"# {args.label_a}: {res['records_a']:,} record line(s)   "
          f"{args.label_b}: {res['records_b']:,} record line(s)"
          + (f"   (+{a.blank:,}/{b.blank:,} blank line(s) ignored)"))
    if res["records_a"] == 0 or res["records_b"] == 0:
        die(f"NOTHING COMPARED: {args.label_a} has {res['records_a']:,} record line(s) and "
            f"{args.label_b} has {res['records_b']:,} record line(s). A header-only VCF cannot agree "
            f"with anything, so this is not a pass. (Blank lines ignored: {a.blank:,} / {b.blank:,}.)",
            2)

    print(f"# BODY-SHA256 ({args.label_a})  {res['digest_a']}")
    print(f"# BODY-SHA256 ({args.label_b})  {res['digest_b']}")
    same_digest = res["digest_a"] == res["digest_b"]

    body_clean = same_digest
    if args.unordered:
        body_clean = (res["pairs_differing"] == 0 and not res["only_a"] and not res["only_b"])
    else:
        body_clean = (res["pairs_differing"] == 0 and not res["unpaired_a"] and not res["unpaired_b"])

    if args.unordered and (res["duplicates_a"] or res["duplicates_b"]):
        print(f"# !! duplicate join keys: {args.label_a} {res['duplicates_a']}, "
              f"{args.label_b} {res['duplicates_b']} — the first record per key won, which is a "
              f"choice, not a measurement")
    if not body_clean:
        if args.unordered:
            print(f"# BODY-DIFFERS: shared {res['shared']:,}, only in {args.label_a} "
                  f"{res['only_a']:,}, only in {args.label_b} {res['only_b']:,}, "
                  f"{res['pairs_differing']:,} shared record(s) whose body differs")
            for side, keys in ((args.label_a, res["only_a_examples"]),
                               (args.label_b, res["only_b_examples"])):
                for k in keys[:args.max_print]:
                    print(f"   only in {side}: {k}")
        else:
            print(f"# BODY-DIFFERS: {res['pairs_differing']:,} aligned record pair(s) differ"
                  + (f"; {res['unpaired_a']:,} record(s) only in {args.label_a}"
                     f", {res['unpaired_b']:,} only in {args.label_b}"
                     if (res["unpaired_a"] or res["unpaired_b"]) else ""))
        for key, cols in res["examples"]:
            print(f"   {key}: {cols}")
        if len(res["examples"]) >= args.max_print:
            print(f"   ... first {args.max_print} shown")
    else:
        print(f"# BODY-IDENTICAL: {res['records_a']:,} record(s) on each side, one hash "
              f"({res['digest_a'][:16]}…), rule: {rule_words}")

    if hdr["differing"]:
        if hdr["real"]:
            print(f"# HEADERS-DIFFER: {hdr['differing']} header line(s) differ, "
                  f"{len(hdr['real'])} of them NOT volatile")
            for line in hdr["real"][:args.max_print]:
                print(f"   real: {line[:160]}")
        else:
            print(f"# HEADERS-VOLATILE-ONLY: {hdr['differing']} header line(s) differ, every one of "
                  f"them matched a volatile pattern")
        for line in hdr["volatile"][:args.max_print]:
            print(f"   volatile, ignored by the rule: {line[:160]}")
        if hdr["differing"] > args.max_print:
            print(f"   ... {hdr['differing'] - args.max_print} more")
    else:
        print("# HEADERS-IDENTICAL: no header line differs")
    print(f"# SAMPLES-{kind.upper()}: {detail}")
    if kind == "reordered":
        print("#   the sample CELLS were not compared positionally: a column permutation is not a "
              "call change, and compare/vcf_paired_diff.py joins those cells by name")
    if kind == "differ":
        print("#   a different sample set is a different call set; comparing genotype cells by "
              "position across it would manufacture differences that are only column shifts")

    verdict = ("BODY_IDENTICAL" if body_clean else "BODY_DIFFERS") + "/" + \
              ("HEADERS_IDENTICAL" if not hdr["differing"]
               else ("HEADERS_VOLATILE_ONLY" if not hdr["real"] else "HEADERS_DIFFER")) + \
              "/" + ("SAMPLES_SAME" if kind == "same" else f"SAMPLES_{kind.upper()}")
    print(f"verdict: {verdict}")

    inputs = {}
    inputs.update(artifact.input_file(args.label_a, args.vcf_a, records=res["records_a"],
                                      body_sha256=res["digest_a"], header_lines=len(a.meta)))
    inputs.update(artifact.input_file(args.label_b, args.vcf_b, records=res["records_b"],
                                      body_sha256=res["digest_b"], header_lines=len(b.meta)))
    doc = artifact.envelope(
        "vcf_body_diff", inputs,
        {"order": "unordered on " + args.key if args.unordered else "file order",
         "join_key": args.key if args.unordered else None,
         "columns_compared": [names_a[i] for i in keep],
         "drop_column": args.drop_column, "drop_info": args.drop_info,
         "volatile_patterns": patterns,
         "sample_cells_compared": kind == "same",
         "blank_lines_ignored": [a.blank, b.blank]},
        {"records": [res["records_a"], res["records_b"]],
         "body_identical": bool(body_clean),
         "body_differs": int(res["pairs_differing"]),
         "unpaired": [res.get("unpaired_a", res.get("only_b", 0)),
                      res.get("unpaired_b", res.get("only_a", 0))],
         "duplicates": [res.get("duplicates_a", 0), res.get("duplicates_b", 0)],
         "body_sha256": [res["digest_a"], res["digest_b"]],
         "header_lines_differing": hdr["differing"],
         "header_volatile_differing": len(hdr["volatile"]),
         "header_real_differing": len(hdr["real"]),
         "header_volatile_lines": [x[:200] for x in hdr["volatile"][:200]],
         "header_real_lines": [x[:200] for x in hdr["real"][:200]],
         "samples": {"kind": kind, "detail": detail,
                     "a": a.chrom_fields[len(FIXED):], "b": b.chrom_fields[len(FIXED):]},
         "differing_records": [{"key": k, "columns": c} for k, c in res["examples"]],
         "verdict": verdict},
        verdict=verdict, compared_something=True)
    artifact.write(args.json_out, doc)
    ok = body_clean and not hdr["real"] and kind == "same"
    return 0 if ok else 1


# --------------------------------------------------------------------------------------------- selftest
# Expected results are derived by hand. The first case is the same bodies under different headers
# and compression; expected exit 0.
_ST_A = """##fileformat=VCFv4.2
##contig=<ID=20,length=64444167>
##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type">
##INFO=<ID=AC,Number=1,Type=Integer,Description="Allele count">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tcase\tmom
20\t1000\tSV_1\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;AC=3\tGT\t0/1\t0/0
20\t2000\tSV_2\tN\t<DUP>\t60\tPASS\tSVTYPE=DUP;AC=1\tGT\t0/0\t0/1
"""
_ST_B = """##fileformat=VCFv4.2
##contig=<ID=20,length=64444167>
##bcftools_viewCommand=view -Oz -o /tmp/tmp.Q0xR1/out.vcf; Date=20261002T143107
##GATKCommandLine.PrintSVEvidence: Version=4.5.0.0, Date=Wed Oct 02 14:31:07 2026, Options:--TMP_DIR=/tmp/x
##fileDate=20261002
##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type">
##INFO=<ID=AC,Number=1,Type=Integer,Description="Allele count">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tcase\tmom
20\t1000\tSV_1\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;AC=3\tGT\t0/1\t0/0
20\t2000\tSV_2\tN\t<DUP>\t60\tPASS\tSVTYPE=DUP;AC=1\tGT\t0/0\t0/1
"""
# Same two records with AC recomputed on the first one.
_ST_C = """##fileformat=VCFv4.2
##contig=<ID=20,length=64444167>
##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type">
##INFO=<ID=AC,Number=1,Type=Integer,Description="Allele count">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tcase\tmom
20\t1000\tSV_1\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;AC=4\tGT\t0/1\t0/0
20\t2000\tSV_2\tN\t<DUP>\t60\tPASS\tSVTYPE=DUP;AC=1\tGT\t0/0\t0/1
"""
# A different cohort with otherwise identical fixed columns.
_ST_D = _ST_A.replace("#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tcase\tmom",
                      "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tcase\tdad")
# A's two records in the other order.
_ST_E = """##fileformat=VCFv4.2
##contig=<ID=20,length=64444167>
##INFO=<ID=SVTYPE,Number=1,Type=String,Description="Type">
##INFO=<ID=AC,Number=1,Type=Integer,Description="Allele count">
##FORMAT=<ID=GT,Number=1,Type=String,Description="Genotype">
#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tcase\tmom
20\t2000\tSV_2\tN\t<DUP>\t60\tPASS\tSVTYPE=DUP;AC=1\tGT\t0/0\t0/1
20\t1000\tSV_1\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;AC=3\tGT\t0/1\t0/0
"""
# A's two records plus a duplicate of the first (a duplicate key the tool must report).
_ST_F = _ST_A + "20\t1000\tSV_1\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL;AC=3\tGT\t0/1\t0/0\n"
# A with a third sample column: a cohort difference.
_ST_G = (_ST_A.replace("INFO\tFORMAT\tcase\tmom", "INFO\tFORMAT\tcase\tmom\tdad")
         .replace("GT\t0/1\t0/0", "GT\t0/1\t0/0\t0/1").replace("GT\t0/0\t0/1", "GT\t0/0\t0/1\t0/0"))


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

    def run(args):
        e = {k: v for k, v in os.environ.items() if not k.startswith("GSVTK_")}
        return subprocess.run([sys.executable, tool] + args, capture_output=True, text=True, env=e)

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

        a = w("body_a.vcf", _ST_A)
        bgz = w("body_b.vcf.gz", _ST_B, gz=True)
        c = w("body_c.vcf", _ST_C)
        d = w("body_d.vcf", _ST_D)
        e = w("body_e.vcf", _ST_E)
        f = w("body_f.vcf", _ST_F)
        empty = w("body_empty.vcf", "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\t"
                                    "FILTER\tINFO\tFORMAT\tcase\tmom\n")
        nohdr = w("body_nohdr.txt", "20\t1000\tSV_1\tN\t<DEL>\t.\tPASS\t.\tGT\t0/1\n")

        r1 = run([a, bgz])
        check("the header was rewritten, the container changed and the bytes differ — the bodies are "
              "still identical, which is the claim md5 could not make",
              r1.returncode == 0 and "BODY-IDENTICAL" in r1.stdout
              and "BODY-SHA256 (A)" in r1.stdout and "HEADERS-VOLATILE-ONLY" in r1.stdout,
              f"exit {r1.returncode}\n{r1.stdout}{r1.stderr}")
        check("the volatile lines it dropped are LISTED, not silently dropped",
              "##bcftools_viewCommand=" in r1.stdout and "##fileDate=20261002" in r1.stdout
              and "volatile, ignored by the rule" in r1.stdout, r1.stdout[:600])
        check("the two BODY-SHA256 lines carry the same hash, so the claim is one number per arm",
              len({ln.split()[-1] for ln in r1.stdout.splitlines()
                   if ln.startswith("# BODY-SHA256")}) == 1,
              "\n".join(ln for ln in r1.stdout.splitlines() if "SHA256" in ln))
        check("the sample columns are reported as the same, by name and order",
              "SAMPLES-SAME" in r1.stdout and "2 sample column(s)" in r1.stdout, r1.stdout[-400:])

        r2 = run([a, c])
        check("a recomputed INFO/AC is a body difference, named by INFO key and by record rather "
              "than printed as two long strings",
              r2.returncode == 1 and "BODY-DIFFERS" in r2.stdout and "INFO{AC}" in r2.stdout
              and "20/1000/SV_1" in r2.stdout and "1 aligned record pair(s) differ" in r2.stdout,
              f"exit {r2.returncode}\n{r2.stdout}{r2.stderr}")
        r3 = run([a, c, "--drop-info", "AC"])
        check("--drop-info AC removes EXACTLY that difference: the bodies are now identical, and the "
              "page says which keys it dropped",
              r3.returncode == 0 and "BODY-IDENTICAL" in r3.stdout
              and "dropped INFO keys AC" in r3.stdout,
              f"exit {r3.returncode}\n{r3.stdout}\n{r3.stderr}")
        r3b = run([a, e])
        check("two sorted VCFs holding the same records in a different order ARE different under the "
              "default rule — order is a rule here, not an accident",
              r3b.returncode == 1 and "BODY-DIFFERS" in r3b.stdout
              and "body order: file order" in r3b.stdout,
              f"exit {r3b.returncode}\n{r3b.stdout}\n{r3b.stderr}")
        r4 = run([a, e, "--unordered", "--key", "CHROM,POS,ID"])
        check("--unordered says those same two files ARE the same records, and prints the key it "
              "joined on", r4.returncode == 0 and "BODY-IDENTICAL" in r4.stdout
              and "joined on CHROM,POS,ID" in r4.stdout,
              f"exit {r4.returncode}\n{r4.stdout}\n{r4.stderr}")
        r4b = run([a, f, "--unordered"])
        check("a record repeated under the same key is DISCLOSED, not folded into the shared set",
              "duplicate join keys" in r4b.stdout and "B 1" in r4b.stdout,
              f"exit {r4b.returncode}\n{r4b.stdout}")

        r6 = run([a, d])
        check("mom -> dad is a COHORT change: the genotype cells are not diffed positionally, and the "
              "run does not exit 0", r6.returncode == 1 and "SAMPLES-DIFFER" in r6.stdout
              and "not compared" in r6.stdout, f"exit {r6.returncode}\n{r6.stdout}{r6.stderr}")
        r6b = run([a, w("body_g.vcf", _ST_G)])
        check("a cohort that GREW by one sample is SAMPLES-DIFFER (exit 1), not a layout refusal "
              "(exit 2): the record body the two files share is still measured", r6b.returncode == 1
              and "SAMPLES-DIFFER" in r6b.stdout and "A 2 sample column(s), B 3" in r6b.stdout,
              f"exit {r6b.returncode}\n{r6b.stdout}{r6b.stderr}")

        r7 = run([a, empty])
        check("a header-only VCF cannot agree with anything: zero records exits 2, not 0 and not 1",
              r7.returncode == 2 and "NOTHING COMPARED" in r7.stderr
              and "0 record line(s)" in r7.stderr,
              f"exit {r7.returncode}\n{r7.stdout}{r7.stderr}")
        r7b = run([a, w("body_version.vcf", _ST_B.replace(
            "##bcftools_viewCommand=", "##bcftools_viewVersion=1.20+pl2\n##bcftools_viewCommand="))])
        check("a bcftools VERSION line is NOT treated as volatile: a different tool chain in the "
              "header is a real difference, so the bodies are identical and the run still exits 1",
              r7b.returncode == 1 and "BODY-IDENTICAL" in r7b.stdout
              and "HEADERS-DIFFER" in r7b.stdout and "bcftools_viewVersion" in r7b.stdout,
              f"exit {r7b.returncode}\n{r7b.stdout}{r7b.stderr}")
        r8 = run([a, nohdr])
        check("a file with no #CHROM line is refused by name, not read as a body of zero records",
              r8.returncode == 2 and "#CHROM" in r8.stderr,
              f"exit {r8.returncode}\n{r8.stdout}{r8.stderr}")
        r9 = run([a, bgz, "--drop-column", "NOT_A_COLUMN"])
        check("a --drop-column nobody has is refused with the columns this side does have",
              r9.returncode == 2 and "is not a column" in r9.stderr,
              f"exit {r9.returncode}\n{r9.stdout}{r9.stderr}")
        r10 = run([a, bgz, "--volatile", "([bad"])
        check("a bad --volatile regex fails at exit 2 before a single record is read",
              r10.returncode == 2 and "regular expression" in r10.stderr,
              f"exit {r10.returncode}\n{r10.stdout}{r10.stderr}")
        r11 = run([a, bgz, "--max-records", "1", "--unordered"])
        check("--unordered over its guard refuses and names the flag, rather than OOMing on a real "
              "call set", r11.returncode == 2 and "--max-records" in r11.stderr,
              f"exit {r11.returncode}\n{r11.stdout}{r11.stderr}")

        gj = os.path.join(dd, "body.json")
        run([a, c, "--json", gj])
        doc = json.load(open(gj)) if os.path.isfile(gj) else {}
        check("the artifact carries the rule, both body hashes and the per-record findings",
              doc.get("tool") == "vcf_body_diff" and len(doc.get("body_sha256", [])) == 2
              and doc.get("body_identical") is False
              and doc.get("rule", {}).get("order") == "file order"
              and "AC" in str(doc.get("differing_records"))
              and doc.get("compared_something") is True,
              json.dumps({k: doc.get(k) for k in ("tool", "body_identical", "body_sha256")})[:400])
        check("a header line that is NOT volatile makes the run exit 1 even when the bodies match",
              run([a, w("body_g.vcf", _ST_A.replace("##fileformat=VCFv4.2",
                    "##fileformat=VCFv4.2\n##FILTER=<ID=LOWGQ,Description=\"Low quality\">"))]).returncode
              == 1, "expected 1 from a real FILTER header addition")
        check("a dropped INFO key is recorded in the rule that produced the number",
              run([a, c, "--drop-info", "AC", "--json", gj]).returncode == 0
              and json.load(open(gj)).get("rule", {}).get("drop_info") == ["AC"],
              json.dumps(json.load(open(gj)).get("rule", {}))[:400])
        check("gzip is decided from the BYTES, not the name: a file called .txt that holds gzip still "
              "reads, and its body still matches the plain-text side",
              run([a, w("body_named_wrong.txt", _ST_A, gz=True)]).returncode == 0
              and run([a, w("body_named_wrong.txt", _ST_A, gz=True)]).stdout.count(
                  "BODY-IDENTICAL") == 1,
              f"exit {run([a, os.path.join(dd, 'body_named_wrong.txt')]).returncode}\n"
              f"{run([a, os.path.join(dd, 'body_named_wrong.txt')]).stdout}")

    print()
    if fails:
        print(f"  {len(fails)} selftest assertion(s) FAILED")
        return 1
    print("  all selftest assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
