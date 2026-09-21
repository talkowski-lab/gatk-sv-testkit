#!/usr/bin/env python3
"""Per-(site,sample) RD copy-state diff between two depth VCFs, via FORMAT/RD_CN.

    python compare/diff_rd_states.py [NEW.vcf.gz] [--baseline BASE.vcf.gz]

Needed because variant IDs differ between pipeline versions (SeparateDepthPesr renames
them), so the two callsets cannot be joined on ID. Sites are joined on the coordinate
key (CHROM, POS, END, SVLEN, SVTYPE) and samples by name; both sides write RD_CN as an
integer copy state, so the comparison is direct. Ambiguous keys -- several distinct
records sharing one coordinate key -- are counted and reported rather than hidden: the
join stays observation-level and last-writer-wins, and you can see how much that could
have affected you.

Use it to ask "which copy-state assignments actually moved", where a site-level
concordance number only tells you how many did. Requires `bcftools` on PATH.
"""
import os
import subprocess
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
import config  # noqa: E402


def _default_baseline() -> str:
    batch = config.get("BATCH", "all_samples")
    return str(config.work_dir("staging") / f"{batch}.genotyped_depth.vcf.gz")


BASE = _default_baseline()
NEW = str(config.work_dir("runs") / "train" / "train.genotyped.vcf.gz")
FMT = "[%CHROM\t%POS\t%INFO/END\t%INFO/SVLEN\t%INFO/SVTYPE\t%SAMPLE\t%RD_CN\n]"


def parse_args(argv):
    """Positional NEW, plus --baseline; keeps the original `diff_rd_states.py FILE` usage."""
    global BASE, NEW
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("-h", "--help"):
            print(__doc__); raise SystemExit(0)
        if a == "--baseline":
            i += 1
            BASE = argv[i]
        else:
            rest.append(a)
        i += 1
    if rest:
        NEW = rest[0]
    return rest


def dump(path):
    states = {}    # (key, sample) -> state string
    samples = set()
    keycount = Counter()   # key -> records seen (each record line repeats per sample)
    keysamples = {}
    p = subprocess.run(
        ["bcftools", "query", "-f", FMT, path],
        capture_output=True, text=True, check=True,
        errors="replace",
    )
    for line in p.stdout.splitlines():
        cols = line.rstrip("\n").split("\t")
        if len(cols) != 7:
            continue
        chrom, pos, end, svlen, svtype, sample, state = cols
        key = (chrom, pos, end, svlen, svtype)
        states[(key, sample)] = state
        samples.add(sample)
        keysamples.setdefault(key, set()).add(sample)
    keys = set(keysamples)
    # a key is ambiguous if >1 distinct record carried it: detect via line count per key/sample
    nlines_per_keysample = Counter()
    for (k, s) in states:
        nlines_per_keysample[k] += 1
    # we did not count duplicates explicitly; instead recount raw lines for a lightweight check
    p2 = subprocess.run(
        ["bcftools", "query", "-f", "%CHROM\t%POS\t%INFO/END\t%INFO/SVLEN\t%INFO/SVTYPE\n", path],
        capture_output=True, text=True, check=True,
    )
    dups = Counter()
    for line in p2.stdout.splitlines():
        c = line.rstrip("\n").split("\t")
        if len(c) == 5:
            dups[tuple(c)] += 1
    n_dup_keys = sum(1 for v in dups.values() if v > 1)
    return states, keys, samples, n_dup_keys


parse_args(sys.argv[1:])

for _label, _path in (("baseline", BASE), ("new", NEW)):
    if not os.path.exists(_path):
        # the common case is simply "you have not staged/fetched that side yet"
        raise SystemExit(f"{_label} VCF not found: {_path}\n"
                         f"  baseline side: python terra/stage_inputs.py (docs/local-replay.md)\n"
                         f"  new side:      terra/batch_fetch_compare.sh fetch (docs/terra-head-to-head.md)")

base_states, base_keys, base_samples, base_dup = dump(BASE)
new_states, new_keys, new_samples, new_dup = dump(NEW)

print(f"baseline: {len(base_keys)} site keys ({base_dup} keys carried >1 record), {len(base_samples)} samples")
print(f"java:     {len(new_keys)} site keys ({new_dup} keys carried >1 record), {len(new_samples)} samples")
print(f"samples equal: {base_samples == new_samples}"
      + (f" (only in base: {len(base_samples - new_samples)}, only in java: {len(new_samples - base_samples)})"
         if base_samples != new_samples else ""))

common_keys = base_keys & new_keys
print(f"matched site keys: {len(common_keys)} (of {len(base_keys)} baseline depth sites; "
      f"{len(base_keys - new_keys)} baseline keys not found on the java side)")

agree = 0
mism = 0
conf = Counter()
set_base13 = set()   # (key,sample) in state 1 or 3 per baseline
set_new13 = set()


def bucket(s):
    return s if -1 <= s <= 4 else 5   # >=5 folded into one bucket for display


for (key, sample), bs in base_states.items():
    if (key, sample) not in new_states:
        continue
    ns = new_states[(key, sample)]
    if bs == "." and ns == ".":
        continue
    b = int(bs) if bs != "." else -1
    n = int(ns) if ns != "." else -1
    conf[(bucket(b), bucket(n))] += 1
    if b in (1, 3):
        set_base13.add((key, sample))
    if n in (1, 3):
        set_new13.add((key, sample))
    if b == n:
        agree += 1
    else:
        mism += 1

total = agree + mism
print(f"\ncomparable obs: {total}; agree {agree} ({agree / total:.4f}); mismatch {mism}")

print("\nconfusion matrix (baseline row, java col; -1 = no-call, 5 = state>=5):")
vals = sorted({b for b, _ in conf} | {n for _, n in conf})
print("base\\java" + "".join(f"\t{v if v < 5 else '5+'}" for v in vals))
for b in vals:
    print((str(b) if b < 5 else "5+") + "".join(f"\t{conf.get((b, n), 0)}" for n in vals))

only_base = set_base13 - set_new13
only_new = set_new13 - set_base13
both = set_base13 & set_new13
print(f"\nstate-1/3 sets: baseline {len(set_base13)}, java {len(set_new13)}, shared {len(both)}")
print(f"symmetric difference: {len(only_base) + len(only_new)}"
      f" (baseline-only {len(only_base)}, java-only {len(only_new)}); "
      f"rel diff vs baseline: {(len(only_base) + len(only_new)) / max(1, len(set_base13)):.4f}")

by_state = Counter()
for s in only_base:
    by_state[("baseline-only", base_states[s])] += 1
for s in only_new:
    by_state[("java-only", new_states[s])] += 1
for k in sorted(by_state):
    print(f"  {k[0]:12s} state {k[1]}: {by_state[k]}")

one_off = sum(c for (b, n), c in conf.items() if -1 <= b <= 4 and -1 <= n <= 4 and abs(b - n) == 1)
print(f"\nobs with |state diff| == 1: {one_off} ({one_off / total:.4f} of comparable)")
