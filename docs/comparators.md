# Comparators

The tools under `compare/` answer one question each about pipeline outputs: did this table, VCF,
matrix, list, JSON document or tarball change between two runs, and how. They are read-only, never
touch the network, and write only where you point `--out-prefix`, `--json` or `--write-samples`.
Each one states its comparison rule in its output and in its `--json` artifact, so a number can be
re-derived by someone else.

[`gatk-sv-profile`](https://github.com/broadinstitute/gatk-sv-profile) remains the tool for
publishable concordance with fuzzy site matching. These tools cover what it does not: exact joins,
a single headline number with its aggregation rule, and the many output shapes that are not VCFs.

Run any of them directly (for example `python compare/table_diff.py --help`) or through the launcher:
`./gsvtk compare <name> [args...]`, with `./gsvtk compare --list` for the names.

## Contents

* [The tools](#the-tools)
* [Exit codes](#exit-codes)
* [The `--json` artifact](#the---json-artifact)
* [Rules every tool follows](#rules-every-tool-follows)
* [Choosing a tool by output shape](#choosing-a-tool-by-output-shape)
* [Per-tool usage](#per-tool-usage)
* [Testing](#testing)

## The tools

| Tool | Question it answers | Inputs | Dependencies |
|---|---|---|---|
| `table_diff.py` | Do two keyed tables (TSV/CSV, plain or gz) differ, column by column? | two tables | stdlib |
| `compare_batch_tables.py` | How do the genotyping trainer's per-batch tables differ, with columns that changed by design marked `STRATEGY`? | two run directories | stdlib |
| `matrix_diff.py` | In a wide key × sample matrix, which samples and which rows moved? | two matrices | stdlib |
| `vcf_paired_diff.py` | On the same (site, sample) cells, does a `FORMAT` or `INFO` field agree? | two VCFs | numpy |
| `vcf_body_diff.py` | Are two VCFs' record bodies identical, ignoring volatile header lines? | two VCFs | stdlib |
| `site_set_diff.py` | Which sites were lost or gained, by SV type, size, algorithm, contig? | two VCFs | stdlib |
| `pair_level_concordance.py` | Genotype concordance on shared VIDs, with no profiler and no bucketing | two VCFs | pysam |
| `gq_scale_compare.py` | Are two VCFs' quality fields on the same scale (where they saturate, mass at the cap)? | two VCFs | numpy |
| `gd_calls_diff.py` | How did the GD calls table change, with the case sample reported apart from the panel? | two GD tables | stdlib |
| `lineset_diff.py` | Do two line-oriented files (sample lists, BEDs, VID lists, PED) differ as sets? | two files | stdlib |
| `json_diff.py` | Do two JSON documents (`inputs.json`, metrics) differ as structures? | two JSON / JSON.gz | stdlib |
| `tar_manifest.py` | Do two bundles differ by member manifest, optionally by bytes, including tar-in-tar? | two tarballs or directories | stdlib |
| `profile_summarize.py` | One site-weighted headline from a `gatk-sv-profile` paired run | one profile output dir | numpy, pandas |
| `artifact_tally.py` | What does one VCF or table actually contain? | one file | stdlib (pysam for `--pysam-check`) |
| `sample_census.py` | How many distinct samples are in one long-format evidence table? | one table | stdlib |

`compare/artifact.py` holds the shared envelope and I/O helpers. `compare/make_fixtures.py` writes
the synthetic test inputs (see [Testing](#testing)).

## Exit codes

All comparators share one contract:

| Code | Meaning |
|---|---|
| 0 | Compared, and nothing differs under the stated rule. |
| 1 | Compared, and found at least one difference. |
| 2 | Nothing was compared: disjoint keys, no shared samples, no comparable cell, an empty or unreadable input. This is never a pass. |

The single-file tools use the same codes with "counted" in place of "compared": `artifact_tally.py`
and `sample_census.py` exit 1 only when a declared expectation (`--header-assert`,
`--samples-order`, `--invariant`, `--expect`) fails, and 2 when nothing was counted.
`pair_level_concordance.py`, `gq_scale_compare.py` and `profile_summarize.py` are measurements with
no pass/fail rule, so they exit 0 when they measured something and 2 when they could not.

## The `--json` artifact

Every tool takes `--json PATH` and writes one envelope shape, built by `compare/artifact.py`:

```json
{
  "tool": "table_diff",
  "argv": ["A.tsv", "B.tsv", "--key", "sample_id"],
  "inputs": {"A": {"path": "A.tsv", "bytes": 102, "sha256_16": "b26c4868..."}},
  "rule": {"join": "sample_id", "tolerance": 1e-06},
  "...": "tool-specific metric keys",
  "verdict": "optional",
  "compared_something": true
}
```

| Key | Content |
|---|---|
| `tool`, `argv` | Which tool ran, with which arguments. |
| `inputs` | Per input: path, byte size, and the first 16 hex characters of its SHA-256, so the artifact can be checked against the files later. |
| `rule` | The join, tolerance, scale and normalization decisions that give the numbers their meaning. |
| metric keys | Tool-specific results. |
| `verdict` | Present when the tool states one. |
| `compared_something` | `false` exactly when the tool exits 2. |

The parent directory of `PATH` is created if needed, and the tool prints `wrote artifact -> PATH`.

## Rules every tool follows

* Join by name, never by position. Samples and columns are matched by name, so a reordered file is
  not a difference.
* Duplicate keys are counted and reported. The first record per key is used, and the output says so.
* Missing values (`.`, `-`, `NA`, empty) are counted and excluded, never compared as numbers.
* A column or sample present on one side only is reported separately from a value change.
* Contig naming is not normalized silently. When `chr20` meets `20`, the join is empty, the tool
  exits 2 and names the contigs it saw. Opt in with `--normalize chrom` (`site_set_diff.py`) or
  `--normalize chr` (`lineset_diff.py`), and the output records that it happened.
* Variant IDs are often rewritten between pipeline versions (variant resolution renames them). If a
  VID join comes back empty or tiny, check the ID namespaces and use `--join coord` where the tool
  offers it.

Before quoting a concordance number, keep in mind:

* `gatk-sv-profile` tables are bucketed per caller, so a site found by four callers contributes four
  rows. `profile_summarize.py` weights by site and prints the weights.
* Quality fields may be on different scales. `GenotypeSVs` rescales GQ by `99/999`, so compare
  scales with `gq_scale_compare.py` before comparing values, then pair cells with
  `vcf_paired_diff.py --scale 99/999 --cap 99`.
* Matching marginal distributions do not mean matching per-cell values. Use `vcf_paired_diff.py` for
  the paired question.
* If one side computed a statistic over more samples, callers or intervals than the other, comparing
  the means reports a coverage difference as if it were a change. Recompute the distribution on
  matched inputs instead.

Three shapes are deliberately not covered: parquet and pickle files (they need `pyarrow`/`pickle`
environments; convert to TSV there), figures (`.pdf`/`.png`), and fuzzy site matching (use
`gatk-sv-profile` and quote its rule).

## Choosing a tool by output shape

| Shape | Example | Tool |
|---|---|---|
| VCF cells (site × sample × tag) | `GQ`, `RD_CN`, `EV`, `PCC` | `vcf_paired_diff.py`, `pair_level_concordance.py` |
| VCF record bodies | a final VCF whose header carries dates and temp paths | `vcf_body_diff.py` |
| VCF site sets | which sites came back | `site_set_diff.py` |
| Keyed table with a header | `*.tsv.gz` with a stable first column | `table_diff.py` |
| Trainer per-batch tables | `*.sr_metric_file.txt`, `*_geno_params.tsv`, sepcutoff tables | `compare_batch_tables.py` |
| Per-(sample, locus) GD calls | `gd_cnv_calls.tsv.gz` | `gd_calls_diff.py` |
| Wide matrix | `*.RD.txt.gz`, bincov matrix, `all_batches.ploidy.tsv`, `*_medianCov.transposed.bed` | `matrix_diff.py` |
| Long unheadered records | `*.sr.txt.gz`, `*.pe.txt.gz` | `lineset_diff.py --columns N` |
| Sample column of an evidence table | merged `*.baf.txt.gz`, `.sd`/`.pe`/`.sr` | `sample_census.py`, then `lineset_diff.py` |
| Line sets | sample lists, interval BEDs, VID lists, PED | `lineset_diff.py` |
| JSON | `inputs.json`, metrics JSON, `.json.gz` | `json_diff.py` |
| Bundles | `*.tar.gz`, including tar-in-tar | `tar_manifest.py` |

## Per-tool usage

### `table_diff.py`

```bash
python compare/table_diff.py runs/a/all_samples.sr_geno_params.tsv runs/b/all_samples.sr_geno_params.tsv --key sample_id
python compare/table_diff.py base.tsv.gz new.tsv.gz --key algorithm --out-prefix /tmp/gq --json /tmp/gq.json
python compare/table_diff.py A.tsv B.tsv --spec my_rule.json
```

Rows join on `--key` (name or 0-based index; `colA,colB` for a composite key). Without `--key` it
uses the first identifier-shaped column (`sample_id`, `key`, `param`, `contig`, `id`, ...), else the
first column, and prints the choice. Each column's kind is inferred and printed: all-integer columns
compare exactly, numeric columns within `--tol` (relative) or `--abs-tol`, everything else as text.
`--delim` defaults to auto-detection; `--no-header` treats the first line as data on both sides.
`--max-rows` (default 2,000,000) caps rows per side and exits 2 rather than running out of memory.
`--out-prefix` writes every differing cell to `<prefix>.tsv`.

`--spec` takes a JSON rule file:

```json
{
  "key": "sample_id",
  "tol": {"rel": 1e-6, "abs": 0},
  "ignore": ["timestamp"],
  "columns": {
    "GQ": {"kind": "rel", "scale": "99/999"},
    "rare_single": {"kind": "strategy", "note": "fitted differently on purpose"}
  }
}
```

`kind` is `exact`, `rel`, `text` or `strategy`. A `scale` of `num/den` multiplies side A before
comparing. `strategy` columns print both values with the note and are never diffed as numbers.
Values set in the spec are printed as overrides.

### `compare_batch_tables.py`

```bash
python compare/compare_batch_tables.py --baseline-dir "$GSVTK_WORK/staging" \
    --new-dir "$GSVTK_WORK/runs/train_definitive" --out-prefix /tmp/tbl
```

Diffs the tables the genotyping trainers produce: SR and PE metric/parameter files, RD depth and
PESR cutoff tables, the batch metrics TSV, the RF cutoffs file and SR cutoff diagnostics. Files are
found per role by filename suffix, so any batch prefix works. If a role matches more than one file
in a directory, it is reported as `MISSING` with the candidates named; choose one with
`--baseline-file ROLE=PATH` or `--new-file ROLE=PATH` (roles: `sr_params`, `pe_params`,
`rd_depth`, `rd_pesr`, `batch_metrics`, `rf_cutoffs`, `sr_diagnostics`).

Verdicts per row are `MATCH`, `DELTA`, `MISSING` and `STRATEGY`. `STRATEGY` marks a row that
changed by design (for example the SR frequency cutoffs, or `SR_sum_log_pval` → `SRQ`, which is a
10× scale change to GATK QUAL), so it is printed but never diffed as a number. `--max-rows` limits
stdout per surface; the TSV is always complete. See
[examples/table_diff_example.md](../examples/table_diff_example.md) for annotated output.

### `matrix_diff.py`

```bash
python compare/matrix_diff.py base/all_samples.RD.txt.gz new/all_samples.RD.txt.gz --worst 10 --json /tmp/rd.json
python compare/matrix_diff.py a.tsv.gz b.tsv.gz --key-cols 0
```

Key columns default to the leading identifier-shaped ones (`#Chr`, `Start`, `End`); `--key-cols 0`
means no key. Sample columns join by name, so added or reordered samples are reported as cohort
changes, not value changes. `--align order` (default) walks both files in lockstep with constant
memory and exits 2 when keys diverge; `--align index` loads file A (up to `--max-rows`) so B can be
in any order. `--only-sample`/`--ignore-sample` narrow the comparison; `--worst N` prints the rows
that moved most.

### `vcf_paired_diff.py`

```bash
python compare/vcf_paired_diff.py base.pesr.vcf.gz new.pesr.vcf.gz --field FORMAT:GQ --join vid --scale 99/999 --cap 99
python compare/vcf_paired_diff.py base.depth.vcf.gz new.depth.vcf.gz --join coord --field FORMAT:RD_CN --by-sample
python compare/vcf_paired_diff.py a.vcf b.vcf --field FORMAT:EV --field INFO:SVLEN
```

Compares any `FORMAT` or `INFO` field on cells both files share. `--join vid` keys on the ID column;
`--join coord` keys on (CHROM, POS, END, SVLEN, SVTYPE), which survives ID rewrites. `--scale num/den`
multiplies side A, and `--cap N` clamps after scaling; both variants are printed. `--tol auto`
(default) uses 0.5 for integer fields and 1e-6 otherwise, and prints the value it chose.
`--by-sample` adds a per-sample table, `--samples-file` restricts samples, and `--max-payload-mb`
caps memory. Zero shared keys or zero shared samples exits 2 with `FATAL: ...`.

### `vcf_body_diff.py`

```bash
./compare/vcf_body_diff.py armA/final.vcf.gz armB/final.vcf.gz
./compare/vcf_body_diff.py base.vcf.gz new.vcf.gz --drop-info AC --drop-info AF
./compare/vcf_body_diff.py base.vcf.gz new.vcf.gz --unordered --json body.json
```

Two runs of one pipeline rarely produce byte-identical VCFs because tools such as `bcftools` write
command lines and dates into the header. This tool hashes the record lines only and prints one
digest per side plus the record count. Header lines matching `##bcftools_*Command=`,
`##GATKCommandLine.` or `##fileDate=` are treated as volatile and listed; `--volatile REGEX` adds
patterns. Any other header difference (including `##bcftools_viewVersion=`) is reported.
Records are compared in file order by default; `--unordered` joins on `--key` (default
`CHROM,POS,ID,REF,ALT`). `--drop-column`/`--drop-info` compare as if that column or INFO key were
absent. A different sample set is reported as `SAMPLES-DIFFER` and genotype cells are not compared
positionally; use `vcf_paired_diff.py` for those. A file with no records exits 2.

### `site_set_diff.py`

```bash
python compare/site_set_diff.py baseline.pesr.vcf.gz branch.pesr.vcf.gz --join vid
python compare/site_set_diff.py a.depth.vcf.gz b.depth.vcf.gz --join coord --strata svtype,size,algorithm --min-support 50
```

Reports lost and gained sites, stratified by `--strata` (`svtype`, `size`, `algorithm`, `contig`,
`mei`; default `svtype,size,algorithm`). Size buckets default to 50/200/1000/10000 bp
(`--size-buckets`). A shared site whose stratum changed is reported as a reclassification. Matching
is exact; `--normalize chrom` strips a leading `chr` on both sides.

### `pair_level_concordance.py`

```bash
python compare/pair_level_concordance.py "$A" "$B" --label-a baseline --label-b branch
```

Joins on VID, compares GT allele sets unphased for every sample on both sides, and prints recovery
in each direction, the exact-match rate over both-called pairs, and the top genotype transitions
(`--code` prints the full matrix). The two files must have the same sample set, or it exits 2.

### `gq_scale_compare.py`

```bash
python compare/gq_scale_compare.py base.pesr.vcf.gz new.pesr.vcf.gz --fields GQ,RD_GQ,PE_GQ,SR_GQ --label-a baseline --label-b new
```

Measures each side's distribution of the named fields: where each saturates, how much mass sits
at `--cap` (default 99), and the effective ceiling. Integer fields bin at 1, fractional fields at
1/`--float-scale`.

### `gd_calls_diff.py`

```bash
./compare/gd_calls_diff.py armA/gd_cnv_calls.tsv.gz armB/gd_cnv_calls.tsv.gz --case-sample NA12878
./compare/gd_calls_diff.py base/gd.tsv.gz new/gd.tsv.gz --only-column qual_score --json gd.json
```

Joins on (`sample`, `GD_ID`) and reports four sections, each split into case sample and panel:
key set changes, `cn_state` transitions (a call appearing or disappearing is its own bucket),
`cn_probabilities` compared as a copy-number → probability map within `--prob-tol` (key order and
formatting do not count), and every other shared column. Column names are configurable
(`--sample-column`, `--gd-id-column`, `--state-column`, `--prob-column`). Zero shared keys exits 2
and points at a `GD_ID` namespace change. The older narrow GD manifest (one row per locus) is
refused as the wrong shape.

### `lineset_diff.py`

```bash
python compare/lineset_diff.py base/validated_samples.list new/validated_samples.list
python compare/lineset_diff.py a.train.intervals.bed b.train.intervals.bed --columns 3
python compare/lineset_diff.py a.ped b.ped --delim whitespace --columns 2
```

Compares the sets of lines and reports per-side duplicates. `--columns N` uses only the first N
fields. Normalization is opt-in with `--normalize case,chr,trim`. Comment and blank lines are
dropped unless `--keep-comments` is given. An empty side exits 2.

### `json_diff.py`

```bash
python compare/json_diff.py run_a/inputs.json run_b/inputs.json --fold-paths
python compare/json_diff.py a.json.gz b.json.gz --ignore '*.outputDir' --ignore '*.timestamp'
```

Reports changes by JSON pointer as `ADDED`, `REMOVED` or changed. Lists of strings compare as sets.
`--fold-paths` compares path-like values by basename, so a different bucket is not a change.
Numbers compare exactly unless `--tol` is given. `--ignore` takes fnmatch patterns on the pointer.

### `tar_manifest.py`

```bash
python compare/tar_manifest.py base/RF_intermediate_files.tar.gz new/RF_intermediate_files.tar.gz --hash
python compare/tar_manifest.py unpacked_a/ unpacked_b/
```

Compares member path, size and type, ignoring order, mtimes and ownership (`--compare-mtime` to
include mtimes). Byte identity is checked only with `--hash`, and the output says when it was not.
Nested tarballs are recursed to `--depth` (default 2). macOS `._*` and `.DS_Store` members are
dropped unless `--include-junk`. Nothing is extracted to disk.

### `profile_summarize.py`

```bash
gatk-sv-profile run --vcf-a "$A" --vcf-b "$B" --label-a baseline --label-b branch --output-dir "$OUT"
python compare/profile_summarize.py "$OUT" --label-a baseline --label-b branch
```

Aggregates the profile's concordance, exact-match and site-overlap tables into site-weighted rates
in both directions, dropping buckets with zero sites and counting undefined (NaN) buckets. Pass the
same labels used for the profile run, because `gatk-sv-profile` embeds them in column names. A
callset profiled against itself must give 1.0000 for every rate, which is a quick check of a new
installation. For "what fraction of sites came back", prefer `site_set_diff.py` or
`pair_level_concordance.py`, which count each site once.

### `artifact_tally.py`

```bash
./compare/artifact_tally.py run.vcf --info MOI --header-assert '##INFO=<ID=MOI,'
./compare/artifact_tally.py run.table --column RESULT --qc-def inputs/single_sample.qc_def
./compare/artifact_tally.py run.vcf.gz --invariant 'info=SVTYPE=CTX:0'
```

Counts what one VCF or table contains, independent of any summary shipped with it. `--info`,
`--format` and `--column` tally values (repeatable; `--top` and `-v` control how many are shown).
Assertions fail the run with exit 1: `--header-assert LITERAL`, `--samples-order a,b,c` (exact
order), and `--invariant` (`info=KEY=VALUE:N` or `col=NAME=VALUE:N`; `N=0` means the value must not
appear). Every invariant is measured and printed even if an earlier one failed. `--qc-def PATH`
records where a table's thresholds came from without opening the file. `--pysam-check` re-counts the
`--info` keys with pysam and reports whether the readers agree.

### `sample_census.py`

```bash
./compare/sample_census.py NA12878.baf.txt.gz --sample-column SAMPLE --expect 157
./compare/sample_census.py case.baf.txt.gz --sample-column 4
./compare/sample_census.py A.sd.txt.gz --write-samples A.samples.txt
./compare/lineset_diff.py A.samples.txt B.samples.txt
```

Streams one long-format evidence table and counts distinct values in its sample column (by name,
default `SAMPLE`, or 1-based number). It prints the per-sample row distribution and flags a table
with exactly one sample, the shape of a merge that dropped the panel. `--expect N` turns the count
into a check (exit 1 if not exactly N). A first line starting with `#` is the header; otherwise the
first line is data and the column must be given by number. `--normalize trim,case` is opt-in and
printed. A wide sample-per-column matrix is refused; use `matrix_diff.py`. Memory is proportional to
the number of distinct samples, not the file size. `--write-samples` writes the sorted sample list
for `lineset_diff.py`.

## Testing

`compare/make_fixtures.py DIR` writes a synthetic input pair whose planted differences are listed in
its docstring (moved GQ, `RD_CN` and `PCC` cells, a dropped and an added site, a duplicate VID, a
shuffled sample order, a same-size byte change in a tarball, volatile VCF headers, a GD table where
only the panel moved, an evidence table missing a sample). `scripts/selftest.sh` runs every
comparator against it and asserts both the exit code and the reported numbers.
`scripts/check_artifacts.py` checks that each `--json` artifact carries `tool`, `argv`, `inputs`
(with hashes), `rule` and `compared_something`.

`vcf_body_diff.py`, `gd_calls_diff.py`, `sample_census.py` and `artifact_tally.py` also take
`--selftest`, which builds fixtures in a temporary directory and checks cases a fixed pair cannot
express (record order, renamed versus added samples, headerless and wide tables).

When adding a comparator, make sure a pair that differs only in column order compares as identical,
and that a fractional `FORMAT` field produces real statistics rather than integer-binned zeros.
