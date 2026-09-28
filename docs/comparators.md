# Comparators: numbers that can be reproduced

[`gatk-sv-profile`](https://github.com/broadinstitute/gatk-sv-profile) is the right tool for
publishable concordance. It is not the end of the job: its tables are **bucketed** (a site picked
up by N callers is counted N times in a row sum), its site matcher is fuzzy, and nothing in it
prints one headline number. So a figure lifted from a profile table is unreproducible unless the
aggregation rule that produced it is written down.

Every tool here is a written-down rule, implemented. Use them when the number has to survive
someone else checking it.

| Tool | Question it answers | Needs |
|---|---|---|
| `profile_summarize.py` | one site-weighted headline from a profile paired run | a profile run's output tree |
| `pair_level_concordance.py` | concordance with **no** profiler and **no** bucketing | two VCFs |
| `gq_scale_compare.py` | are these quality fields even on the same scale? | two VCFs |
| `gq_paired_compare.py` | on the *same* (site, sample) pairs, same number or different scale? | two VCFs |
| `diff_rd_states.py` | where do RD copy-state calls disagree, per (site, sample)? | two depth VCFs |
| `compare_batch_tables.py` | the per-column table diff, strategy-aware | the two runs' table files |
| `table_diff.py` | any two keyed tables (TSV/CSV, gz or plain), by named key and named column | two tables |
| `matrix_diff.py` | a wide site×sample matrix: which sample moved, which row moved | two matrices |
| `vcf_paired_diff.py` | any `FORMAT`/`INFO` tag paired on the same (site, sample): the general form of the two GQ tools and `diff_rd_states.py` | two VCFs |
| `site_set_diff.py` | which sites came back, stratified by svtype / size / algorithm / contig | two VCFs |
| `lineset_diff.py` | sample lists, interval lists, VID lists, PED-like files, compared as sets | two line files |
| `json_diff.py` | did `inputs.json` change (paths foldable, string lists compared as sets) | two JSON / JSON.gz |
| `tar_manifest.py` | did a bundle change (members, sizes, optionally bytes, tar-in-tar included) | two tarballs or directories |

The last seven are generic engines; the first six are fixed rules that happen to be the ones a
v1.1.1 head-to-head needed. Both kinds take `--json`; see **One artifact envelope** below.

Seven of the thirteen need nothing but the standard library (`table_diff`, `matrix_diff`,
`site_set_diff`, `lineset_diff`, `json_diff`, `tar_manifest`, `compare_batch_tables`); the GQ pair
and `vcf_paired_diff` need `numpy`, `profile_summarize` needs `numpy`/`pandas`,
`pair_level_concordance` needs `pysam` and `diff_rd_states` shells out to `bcftools`. None of them
touch the network, and none of them write outside the path you name: `--out-prefix` and `--json`
are the only writes anywhere in this directory.

## Contents

* [Read this before quoting any concordance number](#read-this-before-quoting-any-concordance-number)
* [When variant IDs do not join the two callsets](#when-variant-ids-do-not-join-the-two-callsets)
* [Strategy-aware table diffing](#strategy-aware-table-diffing)
* [The workflow that produced those rules](#the-workflow-that-produced-those-rules)
* [Paired profiling and `compare_batch_tables.py`](#paired-profiling-and-compare_batch_tablespy)
* [Which artefact shape needs which tool](#which-artefact-shape-needs-which-tool)
* [One artifact envelope (`--json`)](#one-artifact-envelope---json)
* [One artifact at a time (`compare/artifact_tally.py`)](#one-artifact-at-a-time-compareartifact_tallypy)
* [How these are tested](#how-these-are-tested)

## Read this before quoting any concordance number

Three properties that have each burned someone:

1. **Site-weighted, not row-weighted.** gatk-sv-profile buckets by caller, so a site found by four
   callers contributes four rows. Summing rows silently weights multi-caller sites more heavily.
   `profile_summarize.py` weights by site and prints the weights, so the arithmetic is inspectable.
2. **Scale matters before magnitude does.** `GenotypeSVs.rescaleGq` multiplies the internal
   quality by `99 / maxQual` (maxQual = 999). Two pipelines whose quality fields both look like
   "0-100" can have completely different effective ceilings, and a mean-vs-mean comparison of them
   is meaningless. `gq_scale_compare.py` reports where each field **saturates** and how much mass
   sits at the cap, the facts you need before comparing any quality statistic.
3. **Marginals are not pairs.** Each side's distribution can match while the per-site values
   disagree. `gq_paired_compare.py` restricts to the intersection of (site, sample) pairs and asks
   the sharper question: on the same pair, is it the same number, or just a different scale?
4. **Reordering is not a difference, and a duplicate is not one row.** Two pipelines' outputs
   routinely list the same samples in a different column order, or the same columns in a different
   order, and a positional join turns that into a phantom disagreement. Or, in one case that
   reached a fixture, compared a variant against *a different variant* sharing its ID and reported
   the gap as a delta. Every tool here joins by **name**, and when a key repeats it prints the count
   (`duplicate join keys: A 1, B 1 — the first record per key won, which is a choice, not a
   measurement`) instead of folding the rows together.

`compare/profile_summarize.py` is validated by a self-comparison **property**, not a shipped test:
point it at a profile run of a callset against itself and every rate must be 1.0000
(`python compare/profile_summarize.py /tmp/profile_selftest`). `make test` does **not** cover it:
it needs `numpy`/`pandas` and a profile output tree. Run it once per new machine.

## When variant IDs do not join the two callsets

`diff_rd_states.py` exists because **variant IDs are not stable across pipeline versions**:
variant resolution rewrites them (`RenameVariants` → `04_variant_resolution/scripts/rename.py
--prefix`, see `wdl/ResolveComplexVariants.wdl`), and `SeparateDepthPesr` then splits the genotyped
VCF by `INFO/ALGORITHMS` without touching the ID column. Two callsets from different versions
therefore cannot be joined on ID. Sites are joined on
**coordinate** and compared through `FORMAT/RD_CN`, the copy state itself:

```bash
python compare/diff_rd_states.py "$NEW_depth" --baseline "$BASE_depth"
```

With no arguments it uses the staged baseline and the newest replay output under `GSVTK_WORK`.

If you find yourself getting a suspiciously empty diff between two pipeline versions, check the
ID namespaces before believing the emptiness. Two disjoint ID sets produce a perfect-looking
"no differences" table. The script now refuses to produce that table:

* **zero shared site keys → `FATAL: no site key matched …`, exit 2.** Everything downstream would
  have printed `0 / 0.0000 / n/a` and exited 0, which reads as "they agree". The usual cause is contig
  naming (`chr20` vs `20`).
* **zero comparable observations → `FATAL`, exit 2.** The agreement and off-by-one fractions divide
  by that count, so 0 is not 100 % agreement.
* **a field that is not an integer is counted and named.** `int()` on a float-looking or sentinel
  value used to abort a run that had already read both VCFs; those records are excluded and printed
  under `excluded, not integer RD states`, per side, with the offending value.
* **`rel diff vs baseline` prints `n/a` when the baseline state-1/3 set is empty**, instead of the
  old `max(1, len(...))` division that turned a missing denominator into a large-looking ratio.

What it still does not do is judge. The final line states what was compared, and pass or fail is your
call.

## Strategy-aware table diffing

`compare_batch_tables.py` (stdlib only, read-only, no network, no Terra) diffs the per-sample
metric tables that the genotyper consumes (`sr/pe_metric_file.txt`, depth and PESR sepcutoff
tables, the metrics TSV, the cutoffs file) and is the reason `STRATEGY` exists as a verdict.

File discovery is deliberate: for each role it takes **exactly one** file, and if a directory holds
more than one candidate (`all_samples.sr_metric_file.txt` *and* `b.sr_metric_file.txt`, say) it stops
and lists them rather than picking one: the first version quietly chose the shortest filename, so
dropping a second batch's tables into the same directory made it cross-compare batch B's baseline
against batch A's output and report huge `DELTA`s that were really two different experiments.
Nothing comparable at all is reported as such, with exit 1, rather than an empty table.

```bash
python compare/compare_batch_tables.py \
    --baseline-dir "$GSVTK_WORK/staging" --new-dir "$GSVTK_WORK/runs/train_definitive" \
    --out-prefix /tmp/tbl
```

Verdicts: `MATCH`, `DELTA`, `MISSING`, and `STRATEGY` for a row that changed **by design**,
where comparing the raw means would be a lie. Baseline files are found by name *suffix*, so any
batch prefix works. See [examples/table_diff_example.md](../examples/table_diff_example.md) for a
real capture and what each verdict looks like.

The rule that took the most unlearning, and the one to apply to any cohort statistic:

> [!WARNING]
> A column whose inputs are a superset on one side must not be diffed as a mean. When one
> side computed a statistic over more samples, callers, or intervals than the other, the correct
> comparison is the recomputed distribution, or nothing. Diffing the means reports a difference
> that is an artefact of coverage, and it will look like a bug in your change.

## The workflow that produced those rules

```bash
# 1. the publishable pass
gatk-sv-profile run --vcf-a "$A" --vcf-b "$B" --label-a baseline --label-b branch \
       --output-dir "$GSVTK_WORK/results/paired" \
       --reference-dict "$GSVTK_WORK/staging/Homo_sapiens_assembly38.dict" \
       --contig-list "$GSVTK_WORK/staging/primary_contigs.list"
python compare/profile_summarize.py "$GSVTK_WORK/results/paired" \
       --label-a baseline --label-b branch

# 2. the independent pass — same inputs, different aggregation, no profiler
python compare/pair_level_concordance.py "$A" "$B"

# 3. quality fields, after the scales are known
python compare/gq_scale_compare.py "$A" "$B"
python compare/gq_paired_compare.py "$A_pesr" "$B_pesr" --field GQ --scale 99/999
```

If (1) and (2) disagree, the disagreement is the finding: it is located in the aggregation rule,
and you now know which number to stop quoting until the rule is written down.

## Paired profiling and `compare_batch_tables.py`

`terra/batch_fetch_compare.sh profile` runs `gatk-sv-profile` in paired mode against the frozen
baseline and the new outputs, then `table` runs the differ over both sides' table files. It
refuses a non-empty output directory without `--force`, because a half-written profile run reads
plausibly.

## Which artefact shape needs which tool

The 118 production WDLs in `gatk-sv` declare a few thousand `File` outputs, and their filenames are
not a usable index: `*.tsv.gz` covers a per-sample metric table, a site×sample matrix and a
long-format observation list, which need three different operations. Sorting artefacts by **what a
comparison has to do to them** is what makes the set small enough to implement:

| Shape | What it looks like | What comparing it means | Tool |
|---|---|---|---|
| cell table | VCF: site × sample × `FORMAT` tag | pair the same (site, sample), then compare one tag | `vcf_paired_diff.py`, `gq_paired_compare.py`, `diff_rd_states.py`, `pair_level_concordance.py` |
| keyed rows | `*.tsv.gz` with a header and a stable first column | join on the named key, diff named columns, report one-sided columns | `table_diff.py`, `compare_batch_tables.py` |
| wide matrix | key columns + one column per sample (`all_samples.RD.txt.gz`, binned coverage, `all_batches.ploidy.tsv`) | join samples by name, report cohort change separately from value change | `matrix_diff.py` |
| long list | unheadered 5-7 column records (`all_samples.sr.txt.gz`, `all_samples.pe.txt.gz`) | compare sets of fields, never positions | `lineset_diff.py --columns N` |
| set | sample lists, contig lists, VID lists, PED files | set difference, with normalization opt-in | `lineset_diff.py` |
| key→value with a preamble | `inputs.json`, metrics JSON, `.json.gz` | compare by JSON pointer, fold paths, sets for string lists | `json_diff.py` |
| bundle | `*.tar.gz` (and tar inside tar, the `Array[Array[File]]` gCNV case) | manifest of members; bytes only when asked | `tar_manifest.py` |

Three shapes are deliberately **not** covered, and pretending otherwise would be the interesting
kind of wrong:

* **parquet and pickle.** `gatk-sv-profile`'s per-contig `sites_*.parquet` and the trainers'
  pickled models need `pyarrow`/`pickle`, which are not in `requirements.txt`. Convert them in the
  profile environment and diff the TSV, or read them there; `compare/` stays importable on a bare
  interpreter.
* **figures.** A `.pdf`/`.png` difference is a judgement, and a pixel diff would be a number that
  means nothing without a human. Read them yourself.
* **fuzzy site matching.** `site_set_diff.py` compares exact keys and will happily tell you a site
  you know is shared is missing, because "did the matcher pair these" is `gatk-sv-profile`'s
  question, not this directory's. If you need the fuzzy answer, run the profiler and quote its rule.

The contig trap (`chr20` in the evidence `.txt.gz` files, bare `20` in genotyped VCFs, sometimes
both across two versions of the same cohort) is handled the same way everywhere: the tools do **not**
quietly strip prefixes. An empty intersection exits 2 and names the contigs it saw; you then decide,
with `--normalize chrom` (`site_set_diff.py`) or `--normalize chr` (`lineset_diff.py`), and the
output states that normalization happened, because a comparison that silently folded namespaces is
a comparison that hid a bug.

## One artifact envelope (`--json`)

Every comparator takes `--json PATH` and writes the same shape (`compare/artifact.py`):

```json
{ "tool": "table_diff", "argv": [...],
  "inputs": {"baseline": {"path": "…", "bytes": 102, "sha256_16": "b26c4868…"}},
  "rule": {"join": "VID", "tolerance": 0.5, "…": "every decision that makes the number mean something"},
  "…metric keys…": {},
  "compared_something": true }
```

`inputs` carries a hash prefix because an artifact that does not name the bytes it measured cannot
be re-checked after a re-fetch. `rule` holds the written-down rule that produced the number,
stored next to it. `compared_something: false` means
exit 2: an empty result is never a silent pass.

Exit codes are part of the contract across all thirteen: **0** compared and nothing outside the
stated rule, **1** compared and found differences, **2** compared nothing (disjoint keys, no shared
sample, unreadable join), which is never a pass.

## One artifact at a time (`compare/artifact_tally.py`)

Every other tool in this directory answers "how do these two files differ?". That question is
unavailable in the moment you most need it: a branch produces one VCF, and the thing to know is what
is *in* it. One review recounted 17,789 records by eye to check a MOI summary; another read a QC table
of 10 PASS / 17 FAIL and had no tool that could name the `qc_def` behind the cut. Counting one file
independently is also the only way to tell "the comparator says no difference" apart from "the
comparator read nothing".

```bash
./compare/artifact_tally.py run.vcf --info MOI --header-assert '##INFO=<ID=MOI,'
./compare/artifact_tally.py run.table --column RESULT --qc-def inputs/single_sample.qc_def
```

Three behaviours matter more here than the tally does:

* **zero records counted exits 2 rather than 0.** An empty file and a header-only file print different
  sentences, because "there was nothing in it" and "I could not read it" are different findings.
* **`--invariant` is a check.** `--invariant 'info=SVTYPE=DEL:0'` measures the declared
  expectation over every row and FAILS (exit 1) when it is violated. It exists because a comparator
  asserted "the baseline arm must have `CTX == 0`" (false in the production baseline, which holds 7
  such records), and an `elif` chain silently skipped the remaining checks, turning good data into a
  REVIEW. Every invariant prints its measured number whether or not the one before it passed; a key
  that no record carries measures 0 and says so, which is a measurement allowed to disagree with the
  declaration.
* **`--qc-def` names where a verdict's thresholds came from and never opens the file.** A PASS/FAIL
  count that cannot be traced back to a rule cannot be audited. An absent path is disclosed instead of
  dropped:
  the verdict stands, the cut behind it cannot be audited from here.

`--samples-order` asserts the sample columns exactly (a prefix match passes a swapped pair), and
`--pysam-check` prints whether this interpreter's pysam agrees with production's pin; see
`scripts/prod_pins.py` and `docs/setup.md` for why the pinned version is worth checking.

## How these are tested

`make smoke` used to exercise one of the six comparators, on 40 MB of real VCF, with no assertion
about the numbers that came out. Now `compare/make_fixtures.py` writes a synthetic pair whose
planted differences are **listed in its docstring**: 2 moved GQ cells, 2 moved `RD_CN` cells,
1 moved `PCC` cell, 1 dropped site, 1 added site, a duplicate VID, a shuffled sample order, a
same-size byte change in a tarball. `scripts/selftest.sh` asserts the exit code **and** the
number for every tool (`expect DESC WANT_RC NEEDLE… -- cmd`). Two properties are worth keeping in
mind when you add a comparator:

* the fixture pair that differs only in **column order** must come back exact 100 %, and
* a Float `FORMAT` field must produce real statistics: the original integer-histogram tool printed
  `mean 0.00 / zero 100 %` for `PCC`, with only a `frac` column hinting that every value had been
  fractional.

`scripts/check_artifacts.py` runs the fixture pair through every comparator that can take it and
fails if any `--json` output is missing `tool`, `argv`, `inputs` (with a hash per file), `rule` or
`compared_something`. That check is what caught four of the new tools inventing four slightly
different envelopes.
