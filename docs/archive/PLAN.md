# Head-to-head test framework: GATK-SV v1.1.1 vs `<branch-under-test>`

> **Live state and results now live in [`CHECKPOINT.md`](CHECKPOINT.md)** (in-flight/completed runs, run-dir
> map, baseline ground truth, defect list, environment gotchas). This file is the *plan*; the checkpoint is
> the *state*. Updated 2026-09-10.

Goal: settle, with numbers, whether the six outstanding v1.1→new differences are material, using a
reproducible harness that gates the PR. Baseline = the **v1.1.1** run in the Terra featured workspace
`<public-baseline-ns>/GATK-Structural-Variants-Joint-Calling` (156 1000G samples).

## 0. Facts established (2026-09-04)

| Fact | Value |
|---|---|
| Terra API path from this network | `https://api.firecloud.org/api/` — `api.terra.bio`/`console.terra.bio` are **NXDOMAIN** here; `data.terra.bio` is the Data Repo, not Rawls |
| fiss | PyPI package is `firecloud` (0.16.39), venv `.venv`, default root overridden to the above |
| Auth | ADC = `<you>@<institution>`; baseline workspace `accessLevel=OWNER`, `canCompute=true` |
| Data model | `sample` (156, per-caller VCFs + count files) → `sample_set` (2: `all_samples` fully run, `all_but_hg00096` stub) → `sample_set_set` (1: `all_batches`) |
| Orchestration | 24 workspace method configs, `01-…20-` per-step, each a **Dockstore** WDL pinned `v1.1.1` (`github.com/broadinstitute/gatk-sv/<Workflow>/v1.1.1`) |
| Baseline run | submissions 2026-07-17 → 2026-07-27, one per step, all `Done`; step 10-GenotypeBatch = `86003b19…` |
| Inputs/outputs | recorded on entity attributes as gs:// paths; frozen locally in `manifests/baseline_v111.json` (verified readable via `gsutil ls`) |
| Key staged sizes | `merged_PE` 9.45 GB, `merged_SR` 7.87 GB, `merged_bincov` 3.97 GB, `outlier_filtered_pesr_vcf` 13.1 MB, `genotyped_pesr_vcf` 35 MB, `genotyped_depth_vcf` 1.8 MB, `cutoffs` 1.1 KB |
| Baseline genotyper | `sv_pipeline_docker = …/sv-pipeline:2025-10-02-v1.1-483973d6` (shell+R+awk genotyper) |
| New side | gatk `18f211cec` + gatk-sv `ed9b4846` (`<branch-under-test>`) |

### Code layout (no interference with other agents)

`git worktree` from the `~/IdeaProjects` clones; those stay on their own branches.

| Path | Ref |
|---|---|
| `<gatk-checkout>/` | detached `18f211cec` (gatk `<gatk-branch>` tip) |
| `<gatk-sv-checkout>/` | `<branch-under-test>` @ `ed9b4846` |
| `<v111-checkout>/` | detached tag `v1.1.1` = `b5d5049c` |
| `<profiler-checkout>/` | `main` @ `6bc1dc7` |
| `<v11-checkout>` | not created — `gatk-sv-v1.1/` (tag `v1.1`) already in workspace, reference only |

## 1. Design principle: freeze upstream, swap one layer

Do **not** re-run the whole pipeline twice. The v1.1.1 run's intermediate artifacts are already on GCS and
addressable, so every comparison is "same bytes in, different code". Stages compared, in dependency order:

| Layer | Baseline (v1.1.1) | New (`<branch-under-test>`) | Primary question |
|---|---|---|---|
| **L3a** FilterBatchSites + FilterBatchSamples | step 07/08 outputs: `sites_filtered_*`, `outlier_filtered_*`, `cutoffs`, `RF_intermediate_files` | same WDL stage, new code | do site/sample filtering and the RF `cutoffs` table change? (this feeds `sr_count`/`pe_count`) |
| **L3b** GenerateBatchMetrics | step 06 → `metrics_file_batchmetrics` | same stage, new code | metric name/definition drift, stratification inputs |
| **L1** Genotyper parameters | `trained_PE_metrics`, `trained_SR_metrics`, `trained_genotype_*_sepcutoff` | `sr/pe/rd_geno_params.tsv`, `.sr_cutoff_diagnostics.txt` | sd_het, median_hom, sr_count, RD separation, state-0 SD |
| **L2** Genotypes | `genotyped_pesr_vcf`, `genotyped_depth_vcf` | same outputs from `GenotypeSVs` | GT/GQ concordance, per-class recall, background-fail flips |
| **L4** ClusterBatch (optional) | step 05 | unchanged code? | only if 07/08 differences trace back to clustering |

Order matters: L3 must run first and its produced `cutoffs` must be recorded, because `SRQ/PEQ →
sr_count/pe_count` is input-driven (already proven: `computeCountCutoff` is an exact port of
`convert_poisson_p.py`, the 5-vs-8 gap comes from `SRQ` 25.94 vs `SR_sum_log_pval` 3.4743 = 34.74 QUAL).

## 2. Comparison surfaces

1. **Table differ** (`compare_metrics.py`, to write): align v1.1.1 `*_metric_file.txt` /
   `*_sepcutoff` / `.cutoffs` against new `*_geno_params.tsv` on the renamed columns
   (`SR_sum_log_pval→SRQ`, `PE_log_pval→PEQ`, `RD_log_pval→RDQ`, …), emit per-parameter
   `baseline | new | abs | rel% | pass_rel% | verdict`.
2. **Population recompute** (`recompute_het_population.py`, to write): stream the staged
   `merged_SR`/`merged_PE` + baseline RD copy states and compute the het/hom count distributions under four
   population definitions — (i) v1.1.1 as-coded (all samples at two-sided-pass VIDs, zero-filled),
   (ii) both-side-gated (new code), (iii) gated + `>=sr_count` filter, (iv) as (i) but with the 1.4826-corrected
   `hetCutoff`. This *directly* tests the sd_het and median_hom hypotheses on real data instead of by reading.
3. **Genotype concordance** (`gatk-sv-profile`, paired mode): `validate → preprocess(SVConcordance via gatk)
   → analyze` gives site overlap, allele-frequency correlation and genotype concordance. Needs a `gatk`
   on PATH (`--gatk-path`), inputs need `SVTYPE`, `GT`, preferably `GQ`/`ECN`.
   Plus my own exact cross-tab (`gt_crosstab.py`): per-sample GT transitions 0/1/2×copy-state,
   stratified by SVTYPE × size band × AF class, with GQ distributions and a background-fail flip count.
   The profiler's plots answer "how different"; the cross-tab answers "which genotypes moved and why".
4. **Diagnostics gate check**: assert `SR_SELECTION_STATUS=OK` on both bins and
   `selected_is_all_zero_cell=false` on every new run; this is the regression guard the AoU batch taught us.

## 3. Execution tracks

**Track A — local replay of the genotyping layer (no orchestrator, no cloud spend).**
Only needs Java + Python: `TrainSVGenotyping`, `GenotypeSVs`, `ConcatVcfs`, `SeparateDepthPesr` are GATK
java; `GenerateBatchMetrics` is `svtest` python. Inputs staged with `gsutil`, hg38 dict/fasta from
`gs://gcp-public-data--broad-references`. 156 samples is the size the AoU investigation already showed the
new code handles ("~150-sample tests pass"). Constraints found: this mac has 24 GB RAM, no docker, no R
(so the v1.1.1 shell path cannot run locally — which is fine, we reuse its outputs), `~22 GB` of staged
count files per run.

**Track B — Terra, faithful WDL-level run (the acceptance run, costs money).**
Requirements, in order of friction:
1. Sandbox workspace — clone of the featured workspace (never submit into it: it is the public reference and
   the entity attributes would be overwritten).
2. Billing project: `<public-baseline-ns>` (role User) or `featured-workspace-testing` (Owner).
3. **WDL source for the new code.** The configs pin Dockstore `v1.1.1`. Branch WDLs import siblings
   (`TasksGenotypeBatch.wdl`, `Structs.wdl`), so Agora one-file methods won't work; we need Dockstore
   versions built from `<branch-under-test>` (fork registration is enough), or Track B runs Cromwell against a
   local checkout instead.
4. Inputs JSON generated per step from the manifest, pointing at the frozen baseline paths, with outputs
   redirected into the sandbox bucket.

Recommended: Track A for the iteration loop (L1/L2 + hypothesis tests), Track B once, for L3 + the final
number that goes in the PR.

## 4. Verdict table to be produced

| Item | Mechanism (established) | Measured delta on this cohort | Warranted? | Fix |
|---|---|---|---|---|
| `sd_het` ≈58% of v1.1.1 | training population gated on both-side support vs v1.1.1's zero-filled all-sample het population | TBD (L1 + recompute) | TBD | TBD |
| `median_hom` 84 vs 93 | first-pass `hetCutoff` drops 1.4826 (`SplitReadEvidenceGenotyper.java:261`, `DiscordantPairEvidenceGenotyper.java:173`) | TBD | likely yes (1-line) | add constant, regenerate expected TSVs |
| cutoff strategy inverted | both sides are real optima of different training sets | TBD (L2 flip count) | TBD | none if concordant |
| RD min-sep stratification collapsed | `GenotypeBatch.wdl` passes one depth/PESR separation | TBD (DEL/DUP + size bands) | TBD | plumb per-svtype/per-size |
| RD state-0 SD 2.17× | copy-state fit on state 0 | TBD (hom-del GT count) | TBD | none unless hom-del moves |
| `sr_count` 5 vs 8 | input RF cutoff scale/level, not code | done | no | document |

## 5. Orchestration

* One script per action, idempotent, all under ``, all read-only against Terra unless `confirm=True`.
* Layout: `{terra.py,recon.py,fetch_baseline.py,…}`, artifacts in `{recon,manifests,staging,runs,reports}`.
* Nothing writes to `<worktrees>/*` trees; subagents write only their own file under `recon/` and get explicit
  hard timeouts (the two 30-minute scouts that stalled and timed out produced nothing — recon after this is inline).
* Terra discipline: `clone_workspace` into a sandbox; submissions one step at a time; cost read from Cromwell
  metadata (`executionEvent` `cost`) after each step; never `--lock` or delete the baseline workspace.
* Every run directory keeps a `RUN.json` (gatk sha, gatk-sv sha, docker tags, input manifest hash, wall time,
  cost) so any number in the verdict table is traceable to a byte-exact input set.

## 6. Open decisions (need the user)

1. Terra sandbox: create it, and in which namespace + billing project? OK to `clone_workspace` the featured one?
2. WDL publication for the branch: publish Dockstore versions from a fork, or run Track B with Cromwell on a
   checkout (no Dockstore)?
3. Compute for Track A: 24 GB mac only, or grant ssh to `<host>` / `<host>` (currently
   `Permission denied (publickey,password)` as `<you>`)?
4. Baseline policy: reuse the v1.1.1 outputs already in the bucket (fast, free) or re-run v1.1.1 in the
   sandbox alongside (identical Cromwell/reference/VM images, ~2× cost)?

## 4.1 Verdict table — FILLED from Track B (Terra, 156-sample 1000G cohort, 2026-09-15)

Chain `06→10` run end-to-end in `<your-ns>/<your-sandbox>` with
`<branch-under-test>` `ce8318e3` + gatk `<gatk-branch>` `d026672` images, against the frozen v1.1.1
baseline. Full evidence: `runs_<run-tag>/NOTES.md`; concordance: `runs_<run-tag>/compare_{pesr,depth}/`.

| Item (from §4) | Measured delta on this cohort | Warranted? | Fix |
|---|---|---|---|
| `sd_het` ≈58% of v1.1.1 | **raw MAD differs by 2 (SR) and 1 (PE) integer units** (9 vs 11/10). `sd_het` is quantised — 1 MAD unit = 1.645·1.4826 = 2.438877 — so "0.818×/0.900×" is false precision. Reproduces in Terra with production images ⇒ not a replay artifact. Cause unestablished, and **population parity is unknowable from disk** (v1.1.1 metric files carry 9/3 keys; the new tool emits no PE diagnostics). Measured downstream cost: PESR GT concordance **0.9905 pair-level** (0.9906 bucket-weighted), exact 0.9905; depth 0.9972/0.9997. | no code fix available | document as bounded residual; 0.99 GT concordance is the ship evidence |
| `median_hom` low (1.4826 dropped) | **FIXED and verified in production**: `first_pass_het_cutoff 63.9499 = 42.0 + 1.645·1.4826·9`. `median_hom` now **SR 86 / PE 83 vs baseline 78 / 76** (overshoots upward; direction is driven by row "pe_count" below, not by the constant). | yes → done (`cac2fa3b4`) | none further |
| cutoff strategy inverted | production cutoffs `rare 0.1/0.6, common 1.0/0.1` vs v1.1.1 `0.1/0.6, 0.9/0.9`; grid healthy (121 cells, 0 NaN, not tied, winner ≠ all-zero); `ValidateSRCutoffs` rc=0 and passes the table through unmodified (crc32c equal). GT impact ≤ the 0.94-0.99 non-ref concordance band; no flip explosion. | none | document |
| RD min-sep stratification collapsed | Confirmed in production: depth-side cutoffs are literally `1∓sep` (0.6189999878 / 1.3810000122 = 1∓0.381). **Downstream harm measured ≈ 0**: depth `genotype_concordance` 0.9972/0.9997, exact 0.999921, all 5,386 baseline depth sites matched. | owner preference only | per-svtype/per-size plumbing if the owner wants v1.1 parity |
| RD state-0 SD | **CLOSED**: state-0 mean/sd 0.045452815264742 / 0.006301943860032662 == v1.1.1 to 14 s.f. with `--num-bins 100000` + curated 56-site training bed, both live (`281d02ac`, `ed9b4846`). | yes → done | none |
| `sr_count` 5 vs 8 | In Terra `sr_count` **10 == baseline 10** (SRQ 47.68, +2.2% over the ×10-scaled baseline value). | no | document |

New rows that only Track B could produce:

| Item | Measured | Warranted? | Fix |
|---|---|---|---|
| **`pe_count` 4 vs v1.1.1's 8** | branch step-07 RF emits `PEQ 21.71` (0.625× the ×10-scaled 34.74) → `computeCountCutoff` (`return Math.max(i-1,1)`; first `i` with `qual >= PEQ` = 5) → **4**. Margin **0.022% of PEQ** ⇒ knife-edge (a 0.02% PEQ move gives 3). Confirmed at the tip: **only PE got the `cac2fa3b4` formulation** (unrounded `-10·log10 p` + `>=`); SR still uses byte-rounded `QualityUtils.errorProbToQual` + strict `>` (`SplitReadEvidenceGenotyper.java:164-167` vs `DiscordantPairEvidenceGenotyper.java:124-127`). Explains `median_hom` above baseline and non-ref-inflating drift (pair-level `homref→het` 0.37%, `homalt→het` 0.45% vs `het→homref` 0.10%). | **owner decision** — accept the step-07 RF shift (root: `labelers.py` `RD_Median_Separation` 0.15→0.10 + metric population), and whether SR should adopt PE's cutoff formulation | if not: revisit `labelers.py`/metric population in 06-07, not the genotyper |
| **PESR per-caller record population −19% / loci −5.8% upstream** | step-08 output 78,467 records vs baseline 96,915, but unique `(chrom,pos)` = 68,054 vs 72,281 (**−5.8% loci**, not −19%): **wham 26,351→9,105 is 93.5% of the gap**, and 13,509 of the 19,405 "missing" IDs are the same locus under another caller. `GenotypeSVs` itself loses **zero** records (input 81,413 = 75,209 PESR + 6,204 depth). | yes, investigate — but at steps 06-08 filter/scores, not the genotyper | mechanism **unattributed**; suspect the deleted `RewriteScores` call + `select_first` reorder feeding `FilterAnnotateVcf.scores` (`FilterBatchSites.wdl:55-58`) |
| **VCF representation change by `GenotypeSVs`** | INS `REF` `N`→resolved base (100%→0.011%); INS `END==POS` in **100%** of new records vs 6.6% baseline (baseline END=POS+SVLEN); BND `ALT` symbolic `<BND>` → breakend (9,379/9,379). **No in-pipeline consumer reads `END` as an INS size or matches `<BND>`** (only `svfile.py:265` clustering arithmetic, identical in v1.1.1, plus an upstream single-sample path) ⇒ **interop/spec** item, not a pipeline break; the repo already warns against `END-POS` (`src/gatk-sv-compare/PLAN.md:1878`). | document + spec test; `gatk-sv-profile` cannot detect it | publish the representation spec (or align with v1.1.1), check external consumers |
| Observability: no step-10 metrics | branch `GenotypeBatch.wdl` has **no** `GenotypeBatchMetrics` task → no `metrics_file_genotypebatch`; table diff `MATCH 11 / DELTA 31 / STRATEGY 6 / MISSING 268` (most MISSING = `genotyped_depth_vcf_*` metric rows v1.1.1 emitted). | yes | re-add a metrics task or document the dropped metrics |
| Cost / structure | **auditable** from `runs_<run-tag>/metadata/*.json` (nested VM-span, trees complete): chain 06→10 = **1,210.6 VM-min / 169 jobs** vs baseline **14,609.6 / 1,929** = **12.1×**; step 10 alone 315.2 vs 1,672.8 (**5.3×**), wall 63.9 vs 234.0 min. The dropped `GenotypeBatchMetrics` sub-workflow is **28.3 VM-min = 1.7%** of baseline step 10, so subtracting it still leaves 5.2× — structure, not a removed task. **Step 09 is 3.5× MORE expensive on the branch** (28.7 vs 8.3 VM-min; 21.9 vs 5.3 min wall): `FormatVcfForGatk`×2 + `ConcatVcfs` around two `SVCluster` merges. Caveats: 7% fewer records applied; step 06's 17× partly reflects v1.1.1 per-sample metrics the branch no longer emits. Corrects this plan's earlier "8 jobs / 382 VM-min" and "885 VM-min", which were **root call-span sums** (382.1 / 885.5). | n/a (good) | report in PR with the accounting stated |
| Pre-flight `region_file_indexes` `.tbi` | `GenerateBatchMetrics.wdl:121-122` (from `main`, not this branch) requires `.tbi` siblings that the legacy `gcp-public-data--broad-references` bucket lacks → all 28 `SVRegionOverlap` shards died at localization on attempt 1. Worked around by repointing sandbox attrs to the curated bucket (byte-identical, indexed). | yes, pre-existing | document; default the resource attrs to the curated bucket |
