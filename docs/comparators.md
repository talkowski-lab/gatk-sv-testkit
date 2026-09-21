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

Only `diff_rd_states.py` has defaults (baseline `staging/<batch>.genotyped_depth.vcf.gz`, new side
`runs/train/train.genotyped.vcf.gz`); the other five take explicit paths, and only
`compare_batch_tables.py` writes a file at all (at `--out-prefix`). Everything needs `numpy`,
`pandas`, `pysam` or `bcftools` as noted in `requirements.txt` — but none of them touch the
network, and none of them write outside the path you name.

## Read this before quoting any concordance number

Three properties that have each burned someone:

1. **Site-weighted, not row-weighted.** gatk-sv-profile buckets by caller, so a site found by four
   callers contributes four rows. Summing rows silently weights multi-caller sites more heavily.
   `profile_summarize.py` weights by site and prints the weights, so the arithmetic is inspectable.
2. **Scale matters before magnitude does.** `GenotypeSVs.rescaleGq` multiplies the internal
   quality by `99 / maxQual` (maxQual = 999). Two pipelines whose quality fields both look like
   "0-100" can have completely different effective ceilings, and a mean-vs-mean comparison of them
   is meaningless. `gq_scale_compare.py` reports where each field **saturates** and how much mass
   sits at the cap — the facts you need before comparing any quality statistic.
3. **Marginals are not pairs.** Each side's distribution can match while the per-site values
   disagree. `gq_paired_compare.py` restricts to the intersection of (site, sample) pairs and asks
   the sharper question: on the same pair, is it the same number, or just a different scale?

`compare/profile_summarize.py` is validated by a self-comparison **property**, not a shipped test:
point it at a profile run of a callset against itself and every rate must be 1.0000
(`python compare/profile_summarize.py /tmp/profile_selftest`). `make test` does **not** cover it —
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
"no differences" table.

## Strategy-aware table diffing

`compare_batch_tables.py` (stdlib only, read-only, no network, no Terra) diffs the per-sample
metric tables that the genotyper consumes — `sr/pe_metric_file.txt`, depth and PESR sepcutoff
tables, the metrics TSV, the cutoffs file — and is the reason `STRATEGY` exists as a verdict.

```bash
python compare/compare_batch_tables.py \
    --baseline-dir "$GSVTK_WORK/staging" --new-dir "$GSVTK_WORK/runs/train_definitive" \
    --out-prefix /tmp/tbl
```

Verdicts: `MATCH`, `DELTA`, `MISSING`, and `STRATEGY` for a row that changed **by design** —
where comparing the raw means would be a lie. Baseline files are found by name *suffix*, so any
batch prefix works. See [examples/table_diff_example.md](../examples/table_diff_example.md) for a
real capture and what each verdict looks like.

The rule that took the most unlearning, and the one to apply to any cohort statistic:

> **A column whose inputs are a superset on one side must never be diffed as a mean.** When one
> side computed a statistic over more samples, callers, or intervals than the other, the correct
> comparison is the recomputed distribution — or nothing. Diffing the means reports a difference
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
