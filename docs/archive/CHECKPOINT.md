# Session handoff — GATK-SV GenotypeBatch head-to-head (Track A local replay)

**Date line.** Written 2026-09-10 17:11 EDT (Thu). Clock cross-checked, not assumed:
`date` = `2026-09-10 17:11:44 EDT`; `api.github.com` `Date:` header = `10 Sep 2026 21:11:34 GMT`;
`api.firecloud.org` = `21:11:44 GMT`. Local clock agrees with both to <10 s. No ticket/PM date source in
this workspace.

**Revised same day, ~21:35 EDT** — §15/§15.5 added (queue item 1 closed: the RD gap is *input configuration*,
not Java math), §5 defects 1/2 re-labelled, new defects 8-10, §7 queue updated. If you only read one thing,
read §15.

**Purpose.** A fresh session starts from zero context. Read `CLAUDE.md` (orientation) → this file (state +
results) → `PLAN.md` (the plan). Every material claim is labelled in §13; corrections to earlier
documents are named in §12, not buried.

## 0. State at handoff

| thing | last observed | re-query with |
|---|---|---|
| **`runs/train_binsonly`** — the one-line-fix-only run (§16) | **IN FLIGHT**, started 21:39 EDT (all 1,462,095 condensed intervals + real `bin_exclude` + `--num-bins 100000` + whole-cohort `-V`); expect ≥46 min, **do not kill without asking** | `pgrep -fl TrainSVGenotyping`; `tail -f runs/train_binsonly/train.log` |
| **`runs/train_definitive`** — the config-corrected full trainer | **COMPLETE** 21:24:20 EDT, 5.50 min, `Runtime.totalMemory()=1216348160`; RD table reproduces baseline (§15.5) | `runs/train_definitive/` |
| `runs/train_full_stream` (production-config full trainer) | **COMPLETE**, `Runtime.totalMemory()=1681915904` in final log line | `runs/train_full_stream/train.log` |
| Java trainer processes on this Mac | **one** (`runs/train_binsonly`, pid 16996 at 21:40 EDT) | `pgrep -fl TrainSVGenotyping` |
| `<branch-under-test>` vs `origin/main` | branch is **25 commits behind** `origin/main` (`c73a08b8` "fix genotype SV trans") — irrelevant to §15 (the three gaps are in files untouched by those commits), relevant before queue item 6 | `git -C <gatk-sv-checkout> fetch --dry-run` |
| **Not mine** — another session's jobs on this host | `gsutil -m -q cp gs://<workspace-bucket>/.../rebatch_1.RD.txt.gz` → `scratch/localize/rebatch_1/inputs/`; `gsutil -m ls -r gs://<workspace-bucket>/` | `pgrep -fl gsutil` (PIDs 6955, 94073/94078 at 17:11). **Do not kill** — different workspace, different agent |
| Gradle daemon (mine, idle) | running, `-Xmx2g` | `pgrep -fl GradleDaemon`; harmless, `./gradlew --stop` to drop |
| Disk | 128 Gi free of 460 Gi; `staging` 20 G, `runs` 256 K | `df -h .` |

**Staging note:** the 3 evidence matrices in `staging/` are **hardlinks** into
`~/Work/ref_panel_1kg/`, so `rm -rf staging` does **not** free ~17 GB — unlink both to reclaim.

## 0.1 Git / provenance inventory (all repos under the workspace + the source clones)

| repo | ref | tip | vs origin | worktree |
|---|---|---|---|---|
| workspace root | — | — | **not a git repo** (`git rev-parse` → `fatal: not a git repository`) | `CLAUDE.md`, `**` are therefore **untracked** — nothing was committed or pushed for this session's docs |
| `gatk/` | `<gatk-branch>` | `18f211cec` | ahead 0 / behind 0 | clean |
| `gatk-sv-main/` | `main` | `ba2531c3` | ahead 0 / behind 0 | clean |
| `gatk-sv-v1.1/` | detached `5af2592e` | `5af2592e` | n/a | dirty only `?? gatk/` — a **nested second clone** at `97f681a0e`, stale; never read RD/SR v1.1 code from there, use `<v111-checkout>` |
| `<gatk-checkout>` | detached | `18f211cec905b5f9b1bbe3096a11c9660d3362ae` | n/a (detached) | **DIRTY: 2 modified files + `build_logs/`** — the streaming patch, see §5 patch block |
| `<gatk-sv-checkout>` | `<branch-under-test>` | `ed9b4846` | tip == remote `ed9b4846af13fba93d8f92234061654aeefe2dbf` | clean |
| `<v111-checkout>` | detached (tag `v1.1.1`) | `b5d5049c` | n/a | clean |
| `<profiler-checkout>` | `main` | `6bc1dc7` | ahead 0 / behind 0 | clean |
| `~/IdeaProjects/gatk` | `<gatk-branch>` | `18f211cec` | remote head `18f211cec905...` ✓ | pre-existing `?? .tokensave/`, **not mine** |
| `~/IdeaProjects/gatk-sv` | `<branch-of-another-session>` (another agent's branch — **not** `<branch-under-test>`) | — | remote head of `<branch-under-test>` = `ed9b4846af13...` ✓ | `?? .claude/`, `?? .tokensave/`, **not mine**; that clone is checked out on a *different* branch — which is why we use worktrees |

Verified by `git ls-remote origin refs/heads/<branch>` at handoff: **both fix branches are unchanged on the
remote, i.e. this session pushed nothing** (and `git status -sb` shows no ahead commits locally anywhere).

## 0.2 Mutations ledger — everything this session changed, with undo

Reversible / local only. **No Terra mutations, no git pushes, no branch creation on any remote.**

| mutation | how to verify | undo |
|---|---|---|
| 2 source files patched in `<gatk-checkout>` (streaming RD train) | `git -C <gatk-checkout> diff --stat` → `2 files changed, 51 insertions(+), 27 deletions(-)` | `git -C <gatk-checkout> checkout -- src/main/java/org/broadinstitute/hellbender/tools/sv/DepthEvidenceGenotyper.java src/main/java/org/broadinstitute/hellbender/tools/walkers/sv/TrainSVGenotyping.java` (patch archived at `patches/0001-streaming-rd-train.patch` (8,060 bytes)) |
| 457 MB jar built from that dirty tree | `ls <gatk-checkout>/build/libs/*local.jar` | `rm -rf <gatk-checkout>/build` |
| `**` created (~30 scripts/docs, `staging/` 20 GB incl. hardlinks, `runs/` 256 KB) | `ls ` | delete dir |
| 3 hardlinks into `staging/` pointing at `~/Work/ref_panel_1kg/` files | `stat -f '%l %N' staging/all_samples.sr.txt.gz` (link count ≥2) | unlink staging **and** the originals to reclaim space |
| **2 new staged assets downloaded read-only from the public bucket** (21:05-21:09 EDT): `bin_exclude.hg38.gatkcov.bed.gz` (14.4 MB, 1,512,310 rows) + `train_hg38_reviewed_final.bed` (2,285 B, 56 rows, md5 `N4q6UoFADEI2z2VyHuUnVQ==` == both the repo copy and the bucket object) | `ls -l staging/{bin_exclude.hg38.gatkcov.bed.gz,train.curated56.intervals.bed}` | `rm` them |
| `runs/rd_*` (6 probe arms), `runs/train_rd_curated`, `runs/train_definitive` + `run_rd_population_probe.sh`, `run_train_definitive.sh`, `patches/proposal-rd-training-config.patch` | `ls runs` / `git -C <gatk-sv-checkout> status --short` → **clean** (the proposal patch is *not* applied) | `rm -rf` the dirs / the files |
| `<v111-checkout>` was checked out (was detached at tag) | `git -C <v111-checkout> rev-parse --abbrev-ref HEAD` → `b5d5049cb7075a60611c7ec79734fca2df47fee4` | leave as-is or `git -C <v111-checkout> checkout v1.1.1` |
| baseline files downloaded from the public Terra workspace bucket (read-only `gsutil cp`) | `staging/staged.json` | delete files |

Deliberately **not** done: no Terra sandbox created, no method/config registered, no entity attribute
written, no submission launched, no Cromwell/Docker run, no code committed anywhere.


---

## 1. `runs/train_full_stream` — COMPLETE (started 16:13, done ~16:59 EDT)

Real `TrainSVGenotyping` on the **whole** frozen v1.1.1 cohort: **all 1,462,095** condensed training
intervals (no subsampling) and the whole-genome training VCF (`variants_seen 93269`,
`variants_not_cnv 38963`). Patched (streaming) jar, `-Xmx8g`, peak `Runtime.totalMemory()=1.68 GB`
for the whole run. Results are in §4.2. To re-run:

```bash
cd <workspace>/testkit
OUT=$PWD/runs/train_full_stream XMX=8g \
INTERVALS=$PWD/staging/train.full.intervals.bed \
nohup bash run_train_full.sh > runs_train_full_stream.out 2>&1 &      # ~46 min
```

Logs/outputs: `runs/train_full_stream/train.log` (RD progress logged every 100k intervals),
`all_samples.{sr,pe,rd_depth,rd_pesr}_geno_params.tsv`, `all_samples.sr_cutoff_diagnostics.txt`.

---

## 2. What the experiment is

| | |
|---|---|
| Goal | Decide whether the outstanding new-vs-v1.1.1 genotyper/metric differences are warranted, **before pushing anything** |
| New side | GATK `<gatk-branch>` @ `18f211cec` (`<gatk-checkout>`, detached) + gatk-sv `<branch-under-test>` @ `ed9b4846` (`<gatk-sv-checkout>`) |
| Baseline side | gatk-sv tag `v1.1.1` @ `b5d5049c` (`<v111-checkout>`) outputs, **reused from the bucket, not rerun** |
| Cohort | Terra `<public-baseline-ns>/GATK-Structural-Variants-Joint-Calling`, `sample_set=all_samples`, 156 samples |
| Track | **Track A**: replay the real tools locally, replicating WDL command lines by reading the WDL. No Terra writes, no sandbox, no Cromwell/Docker |
| Method | Freeze v1.1.1 inputs → run new Java trainer → diff parameter tables (L1), then VCF/GT concordance (L2), plus FilterBatch/GenerateBatchMetrics (L3) |

### Baseline coordinates

- Workspace id `<baseline-workspace-id>`, bucket `fc-<baseline-workspace-id>`
- GenotypeBatch submission `<submission-id-1>` (v1.1.1, completed 2026-07)
- Manifest of resolved inputs/outputs: `manifests/baseline_v111.json` (~12 MB; steps 05–10)
  - entity attributes are at `manifest['entities']['sample_set']['all_samples']` — **flat**, no `attributes` sub-key
- Terra API from this network: **`https://api.firecloud.org/api/`** (`api.terra.bio` is NXDOMAIN here)
- FISS venv `.venv` (PyPI package name is `firecloud`, v0.16.39); `terra.py` helpers are
  read-only by default, mutating calls need `confirm=True`. **No Terra writes have been made.**

### Staged inputs (`staging/`)

Heavy evidence matrices were **hardlinked from `~/Work/ref_panel_1kg/`** after byte-exact size
match against baseline `merged_SR`/`merged_PE`/`merged_bincov` — no egress needed:
`all_samples.sr.txt.gz` (7.87 GB), `all_samples.pe.txt.gz` (9.45 GB), `all_samples.RD.txt.gz` (3.97 GB) + `.tbi`.
Plus: `all_samples_medianCov.transposed.bed`, `all_samples.filtered_pesr_merged.vcf.gz` (training VCF
= baseline `outlier_filtered_pesr_vcf`), `condensed_intervals.annotated.tsv`, `all_samples.cutoffs`,
`Homo_sapiens_assembly38.dict`, `primary_contigs.list`, `all_batches.ploidy.tsv`,
depth + PESR blacklist beds (+`.tbi`), `train.full.intervals.bed` (1,462,095 rows),
`train.sub8.intervals.bed` (246,876 rows, `($2 % 8)==3` hash).

Baseline ground-truth files fetched into `staging/`:
`all_samples.sr_metric_file.txt`, `all_samples.pe_metric_file.txt`,
`GenotypeBatch.all_samples.metrics.tsv`, `all_samples.regeno.coverage_medians_merged.bed.gz`,
`all_samples.{depth,pesr}.{depth,pesr}_sepcutoff.txt`.

### Derived trainer arguments (from `all_samples.cutoffs`, exactly as the WDL awk does)

`SRQ=46.6451  PEQ=34.743558552260145  PESR_SEP=0.203639398099545  DEPTH_SEP=0.384848484848485`
(`SRQ/PEQ = SR_sum_log_pval / PE_log_pval × 10`, i.e. v1.1 `-log10 p` → GATK QUAL/deciban.)

---

## 3. Runs in `runs/` — read this table before comparing anything

| dir | intervals | jar | meaning |
|---|---|---|---|
| `train_chr20` | 12,001 (chr20:1–25 Mb, contiguous) | original `18f211cec` | scale demo; SR population tiny (`first_pass_variants=6`) |
| `train_full` | **246,876 subsample** (whole-genome VCF) | original `18f211cec` | ⚠ **dir name is misleading**: it holds the *subsampled* RD run (my `OUT` env override landed after the script's own assignment). This is where the §4 SR/PE/RD numbers come from |
| `train_sub8_streamcheck` | 246,876 subsample | patched (streaming) | numerical-equivalence validation of the patch — SR/PE identical, RD agrees to ≤3.5e-11 |
| `train_full_stream` | **1,462,095 full** | patched (streaming) | **COMPLETE — the production-configuration reference run.** Results in §4.2. Its `-V` is the *batch* VCF and its depth-exclusion file is the wrong asset (§15.1 R3), so read §15 before quoting it as "production-equivalent" |
| `rd_curated56` (+ `_real`, `_bins100k`) | 56 curated loci | patched | RD-only probe arms (`-V` = 836-record chr20 VCF — legitimate: RD training never reads the VCF). State-0 mean/sd by arm: 0.0489/0.0146 → 0.0473/0.00997 → **0.0454528/0.0063019 = baseline** |
| `rd_rand8736` (+ `_real`, `_bins100k`) | 8,736 random condensed intervals | patched | **size control** for the arms above: matched observation count, wrong population identity — reproduces the full-run pathology, not the baseline |
| `train_rd_curated` | 56 curated loci | patched | whole-cohort `-V`, stub exclusions, bins 10 — first evidence the RD fix propagates to SR (4.92 min!); SR `sd_het` 19.511 → 21.950 |
| **`train_definitive`** | 56 curated loci | patched | **R1+R2+R3 all corrected** (§15.5): curated loci + `--num-bins 100000` + real `bin_exclude` + whole-cohort `-V` → RD = baseline; SR/PE `sd_het` 21.950 |
| `train_binsonly` | **1,462,095 full** | patched | §16 counterfactual: only R2 (+R3) corrected, population left as production's. In flight at handoff |

`run_train_full.sh` now honours `OUT`, `JAR`, `XMX`, `INTERVALS` env overrides.
`run_train_chr20.sh` is the chr20 slice variant.

---

## 4. Results so far

### 4.1 v1.1.1 baseline ground truth (whole batch, 156 samples)

```
SR: sr_count 10   median_hom 78   sd_het 26.8276            (implied raw MAD 11)
    rare_min 0  rare_max 2  common_min 2  common_max 156
    rare_single .1  rare_both .6  common_single .9  common_both .9
PE: pe_count 8    median_hom 76   sd_het 24.3888            (implied raw MAD 10)
RD (all_samples.depth_sepcutoff.txt; depth + pesr files share these means/sds):
 state 0  mean 0.0454528  sd 0.0063019  cutoff 0.106875
 state 1  mean 0.5027525  sd 0.0406171  cutoff 0.615152
 state 2  mean 1.0080415  sd 0.0614836  cutoff 1.384850
 state 3  mean 1.5407418  sd 0.0769897  cutoff 1.774751
 state 4  mean 2.0794014  sd 0.1002309  cutoff 2.25
RD_Median_Separation strata still present in all_samples.cutoffs (v1.1.1 used them per svtype/size band):
 PESR svsize>=1000 0.203639 | PESR svsize>=0 0.136634 | Depth>=5000 DEL 0.374603 | Depth>=5000 DUP 0.384848
```

### 4.2 FINAL head-to-head, full interval set (`runs/train_full_stream`, 1,462,095 intervals) — **quote these**

| parameter | v1.1.1 | new Java | ratio / verdict |
|---|---|---|---|
| `sr_count` | 10 | **10** | ✓ exact (RD-independent) |
| `pe_count` | 8 | **7** | 1-ulp tie, §5.3 — **fix** |
| rare bins / common bins | 0/2, 2/156 | 0/2, 2/156 | ✓ exact |
| SR `sd_het` (raw MAD) | 26.8276 (11) | 19.5110 (8) | 0.727× |
| PE `sd_het` (raw MAD) | 24.3888 (10) | 21.9499 (9) | 0.900× |
| SR `median_hom` | 78 | 68 | 0.872× |
| PE `median_hom` | 76 | 70 | 0.921× |
| SR freq cutoffs single/both | .1/.6 (rare), .9/.9 (common) | .2/.6, 1/.6 | grid healthy: rare 11131/36328, common 151701/1097543 |
| SR training size | — | `first_pass_variants 206`, het obs 1015, hom obs 716, het_median 33, cutoff 46.16, mad_raw 8 | |
| RD state 0 | 0.04545 / **0.00630** / **0.10688** | 0.08789 / **0.06626** / **0.25607** | sd **10.51×**, hom-del cutoff **2.40×** |
| RD state 1 | 0.50275 / 0.04062 / 0.61515 | 0.60253 / 0.13650 / 0.61515 ✓ | mean 1.20×, sd 3.36×, cutoff matches |
| RD state 2 | 1.00804 / 0.06148 / 1.38485 | 0.93610 / 0.10333 / 1.38485 ✓ | mean 0.93× ("normal" sits 6.4% low), sd 1.68× |
| RD state 3 | 1.54074 / 0.07699 / 1.77475 | 1.37435 / 0.12615 / 1.65031 | mean 0.89×, sd 1.64×, cutoff 0.93× |
| RD state 4 | 2.07940 / 0.10023 / 2.25 | 1.95776 / 0.14055 / 2.25 ✓ | mean 0.94×, sd 1.40× |
| RD state 1→2 (PESR table) | 0.61515 | 0.79238 = 1 + 0.2036 | §5.4 separation pinning |

**RD interval selection turned out not to move SR/PE at all**: `runs/train_full_stream` (full) and
`runs/train_full` (246,876 subsample) give **identical** `sd_het`, `median_hom`, `pe_count`, frequency
cutoffs and pass/fail tallies, despite `first_pass_variants` 205 → 206 — the het/hom statistics are
medians and MADs, so they are insensitive to a handful of sites. So the §4.3 SR/PE numbers were right
after all, and the user's caution only bit the **RD table itself** (state-0 cutoff 0.3274 subsample vs
0.2561 full, −22%). Still: never subsample RD intervals for reporting — the RD table is sensitive.

### 4.3 New-vs-baseline, subsampled RD (sensitivity check only — superseded by §4.2)

| parameter | v1.1.1 | Java new | note |
|---|---|---|---|
| `sr_count` | 10 | **10** | ✓ exact, RD-independent |
| `pe_count` | 8 | **7** | float tie, §5.3 |
| rare/common bins | 0/2, 2/156 | 0/2, 2/156 | ✓ exact |
| SR `median_hom` | 78 | 68 | ⚠ confounded |
| PE `median_hom` | 76 | 70 | ⚠ confounded |
| SR `sd_het` | 26.8276 (MAD 11) | 19.5110 (MAD 8) = 0.727× | ⚠ confounded |
| PE `sd_het` | 24.3888 (MAD 10) | 21.9499 (MAD 9) = 0.900× | ⚠ confounded |
| SR freq cutoffs s/b | .1/.6, .9/.9 | .2/.6, 1/.6 | grid healthy: rare 11131 pass / 36328 fail; common 151701 / 1097543 |
| SR training size | unknown | `first_pass_variants 205`, het obs 1003, hom obs 717, het_median 33, `first_pass_het_cutoff 46.16`, mad_raw 8 | |
| RD state 0 | 0.04545 / 0.00630 / 0.10688 | 0.10602 / **0.06682** / **0.32738** | sd **10.6×**, hom-del cutoff **3.06×** — interval count moves sd only 17 %, so not a subsample artifact |
| RD state 1 | 0.50275 / 0.04062 | 0.66681 / 0.10246 | mean 1.33×, sd 2.5× |
| RD state 2 | 1.00804 / 0.06148 | 0.93990 / 0.10413 | "normal" state 6.7 % below 1 vs baseline +0.8 % |
| RD state 3 | 1.54074 / 0.07699 | 1.35798 / 0.11408 | |
| RD state 4 | 2.07940 / 0.10023 | 1.95224 / 0.14053 | |
| state 1→2 cutoff | 0.615152 | 0.6152 depth-only ✓ / **0.7964 PESR** | `1 + separation` pinning, §5.4 |

### 4.4 The scale demo (`train_chr20`, 836 records)

`sd_het 12.194` (raw MAD 5), `median_hom 56.5`, and diagnostics
`first_pass_variants 6`, het obs 50, hom obs 76 — MAD is integer-quantized at that size, so
**small-slice SR numbers are meaningless**; do not quote them as evidence.

---

## 5. Defects found (with evidence)

1. **RD training memory is unbounded → OOM at the task's own 16 GiB default.**
   `TrainSVGenotyping.trainCopyNumberSites` accumulated `List<DepthGenotypeResult>` for every
   training interval; `DepthEvidenceGenotyper.train` then flattened it into `int[] copyStates` +
   `double[] sampleDepths` + a boxed `List<Integer>` per state. On 1.46 M intervals × 156 samples
   it died at 35.2 min with `OutOfMemoryError` at `DepthEvidenceGenotyper.java:202`
   (`Runtime.totalMemory()=15032385536`, heap cap 14 g). `GenotypeBatch.wdl:264-265` sets
   `cpu_cores: 1, mem_gb: 16` for `TrainSVGenotyping`, so **production hits this at 156 samples**;
   AoU (300+ samples) is worse. v1.1.1's RD training was streaming R/awk → O(1) memory.
2. **RD distribution shape vs v1.1.1 — CLOSED as a *configuration* defect, not a Java defect (§15).**
   Symptoms were state-0 sd 10.51×, state-0 cutoff 2.40× (hom-del boundary 0.1069 → 0.2561), state-2 mean
   7.1 % low. The earlier hypothesis (Java's per-interval depth **value** definition differs from v1.1.1's
   `RD_genotype`) is **disproved**: with v1.1.1's curated 56-locus training set + `--num-bins 100000` + the
   real `bin_exclude`, Java's state-0 mean/sd come out identical to the baseline to 14 significant figures.
   Three input differences do all the work (R1 population, R2 bin count, R3 exclusion asset) — see §15.1.
   Still true: it explains SR *and* PE `sd_het` shrinking together (both partition het/hom by RD copy state),
   and the fix only *partially* propagates (SR 0.727× → 0.818×, PE unchanged at 0.900×) — §15.5.
3. **`pe_count` 8 → 7 is a 1-ulp tie, not a formula error.**
   `DiscordantPairEvidenceGenotyper.computeCountCutoff` (:121-131) returns `i-1` for the smallest
   `i` with `10·log10(e)·i > qualityCutoff` (Poisson P(0;λ=i)=e^−i via `errorProbToQual`). Our
   `PEQ = 34.743558552260145` is exactly `8 × 10·log10(e)`, so the strict `>` is decided by
   rounding noise → 7. SR is safe (`46.6451 / 4.342944819 = 10.7404`, not a multiple) → 10 ✓.
   Consequence: PE's two-sided training gate drops from `pe_count/2 = 4` to `3`.
   Fix: compare with tolerance / derive analytically.
4. **RD min-separation stratification is collapsed** (`GenotypeBatch.wdl` ~283-299): one
   `PESR_SEP` + one `DEPTH_SEP` instead of v1.1.1's per-svtype/per-size-band rows (see §4.1).
   Numerically confirmed: the state 1→2 cutoff comes out exactly `1 + separation`
   (depth-only 0.6152 = 1+0.3848 ✓ matches baseline; PESR 0.7964 = 1+0.2036 is new).
   **Severity raised by §15.5 item 3**: on the depth side *both* boundaries are clamp-determined in v1.1.1 too
   (`1 ∓ DEPTH_SEP`, never the fit), so this defect decides those two numbers outright — and the match here is
   luck, since baseline's DEL row was 0.3746 vs the DUP row's 0.3848 that both pipelines pass.
5. **`TrainSVGenotyping` does not fail on a degenerate SR grid** (deliberate, for diagnostics) and
   `ValidateSRCutoffs` only checks grid *non-degeneracy*, not statistical support: it reported
   `rare/common_selection_status OK` on a **6-variant** fit in `train_chr20`. Gate tightening needs
   a minimum-observation threshold calibrated at baseline scale.
6. RD params are **not stable under interval subsetting**: state-0 cutoff 0.3818 (12k contiguous)
   vs 0.3274 (246k hashed) vs **0.2561 (full)** — so RD must always be reported from the full interval
   set. (SR/PE params, by contrast, were identical between the 246k and full runs — §4.2.)
7. Still open from earlier: Java first-pass `hetCutoff` omits the `1.4826` MAD consistency constant
   (`SplitReadEvidenceGenotyper.java:261`, `DiscordantPairEvidenceGenotyper.java:173`); real but
   small (54.5 → 60.1 in emulation). Also SR population gate semantics: Java pair-level
   (`int(sr_count/2)`) vs v1.1.1's row/VID-level `> sr_count/2` float — untested, see §6.
   **Re-measured after §15.5: `first_pass_het_cutoff 47.805 = 33.0 + 1.645·9` where v1.1.1's rule gives
   54.87**, so the omission is live and its direction (keeps weak hom observations → low `median_hom`
   69 vs 78) matches.
8. **NEW (defect, production): RD is *trained* with 10 bins and *applied* with 100 000 bins.**
   `TrainSVGenotyping.numBins` defaults to **10** (`TrainSVGenotyping.java:238`); `GenotypeSVs.numBins`
   defaults to **100 000** (`GenotypeSVs.java:239`); `wdl/GenotypeBatch.wdl` passes `--num-bins` to neither.
   v1.1.1 used `n_RD_genotype_bins = 100000` at train *and* apply. This is an internal train/apply
   inconsistency independent of v1.1 parity, and it is what drags the trained state-2 mean to 0.936 and
   thereby *makes* the `1 + separation` clamp fire (with 100 000 bins the same population gives 1.012).
   One-line fix; proposal in `patches/proposal-rd-training-config.patch` (not applied).
9. **NEW (defect, design): RD copy-state training population is the batch's own condensed intervals.**
   v1.1.1 fit the copy-state mean/sd inside a curated reviewed-CNV bed baked into the image
   (`<v111-checkout>/wdl/TrainRDGenotyping.wdl` MakeTrainingBed → `/opt/RdTest/train_hg38_reviewed_final.bed`,
   hg19 `1kg.train.loci.bed`). The new path fits it on intervals whose copy state was *assigned by the fixed
   seed cutoffs* (0.25/0.75/1.25/1.75) that the fit is meant to replace → circular. Size is not the issue:
   the 8,736-random-interval control reproduces the pathology (§15.3). The asset is still shipped on
   `<branch-under-test>` (`src/RdTest/train_hg38_reviewed_final.bed`, blob `d6aaab2d`, copied by
   `dockerfiles/sv-pipeline/Dockerfile:33 COPY src/RdTest /opt/RdTest`) and is in
   `gs://gatk-sv-resources-public/hg38/v0/sv-resources/resources/v1/` → feedable as a workspace attribute
   with **zero new assets and zero Java changes**.
10. **NEW (replay fidelity, mine): the replays' depth-exclusion asset was the wrong one.** Every run in §4
   used `staging/depth_blacklist.sorted.bed.gz` (48 rows — a genuine pipeline asset, md5
   `nfqEmtnuINq9DjSrtFvDnQ==` == the public object; *not* the hand-made stub §0.2 used to call it), while
   both v1.1.1 and production feed `${workspace.bin_exclude}` = `bin_exclude.hg38.gatkcov.bed.gz`
   (1,512,310 × 100-bp rows). Worth 32 % of the curated state-0 sd, so §4.2's state-0 numbers are slightly
   pessimistic vs production. `run_train_definitive.sh` uses the real one; `run_train_full.sh` still does not.

### Patch applied locally, NOT committed, NOT pushed

`patches/0001-streaming-rd-train.patch` (52 + 26 lines, 2 files) replaces the RD
accumulation with per-copy-state **Welford** counters and streams intervals through a lazy
`Iterable` (plus progress logging every 100k intervals). Verified numerically equivalent to the
original on the 246,876-interval config: Δmean ≤ 3.5e-11, Δsd ≤ 1.3e-13, Δcutoff ≤ 1e-12;
`sr_geno_params.tsv`, `pe_geno_params.tsv` and the `## SR_TRAINING_PASSES` block **identical**.

⚠ Provenance: the built jar keeps the name `gatk-package-4.6.2.0-126-g18f211c-SNAPSHOT-local.jar`
even though the tree is dirty. `<gatk-checkout>` HEAD is `18f211cec905b5f9b1bbe3096a11c9660d3362ae`
with the two files modified. If a byte-exact `18f211cec` run is required, `git stash` first and
rebuild (≈51 s with Java 17).

---

## 6. Next: the 2×2 that breaks the snowball

User's (correct) objection: PE/SR training is downstream of RD training, so any RD change
snowballs into `sd_het`/`median_hom`, and a serial diff attributes nothing. Plan instead:

Use `TrainSVGenotyping --output-training-vcf true` (Phase 2a), which re-genotypes **every** training
site and therefore yields per-(site,sample) RD copy states for all clustered sites — this also
fixes the old `69/790` matching problem (the staged `genotyped_depth.vcf.gz` is *after*
`SeparateDepthPesr`, so it only holds `EVIDENCE=RD` sites).

| | RD states = Java's | RD states = v1.1.1 cutoffs re-applied to Java's depth ratios |
|---|---|---|
| SR/PE gate = Java (pair-level) | pipeline as-is | isolates **cutoff** inheritance |
| SR/PE gate = v1.1 (VID-level row semantics) | isolates **gate** semantics | closest replication of v1.1 |

Interpretation: row 2 ≈ row 1 → gate irrelevant, whole gap inherited from RD, SR/PE code can ship.
Column 2 moving 19.5 → 26.8 → RD *cutoffs*; row 2 moving → population semantics; neither →
Java's depth **value** definition differs (state-2 mean already says it does) → fix RD upstream.
Supporting tool: `recompute_het_population.py` (emulates SR het/hom populations from the
raw matrices; already has the corrected `poisson_mean_from_minuslog10p(p) = p·ln10`).

## 7. Remaining queue

- [x] **1. Java's per-interval depth value vs v1.1.1's `RD_genotype`** — **DONE the same day; answer in §15.**
      The depth-value recipe is a faithful port (identical mean/sd to 14 s.f. given v1.1.1's inputs); the whole
      §4.2 RD gap is three input-configuration differences (R1 population / R2 `--num-bins` / R3 exclusion
      asset), of which R2+R3 are one-line and R1 needs an owner decision. The proposed WDL change is
      `patches/proposal-rd-training-config.patch` (verified `git apply --check` clean against
      `<gatk-sv-checkout>` @ `ed9b4846`, **not applied, not committed, not pushed**).
- [ ] **2. The §6 2×2 — rescope: the RD axis is already answered, so only the gate axis is left.**
      §15.5 shows RD states are now baseline-equivalent, yet SR `sd_het` is 21.950 vs 26.8276 (raw MAD **9 vs
      11**) and PE 21.9499 vs 24.3888 (MAD **9 vs 10**) — i.e. 2 and 1 MAD units, not the huge hole §4.2
      implied. Run the remaining cell on top of `train_definitive`'s configuration: `--output-training-vcf true`
      for per-(site,sample) RD states, then replay v1.1.1's selection in
      `recompute_het_population.py`. Cheap version first: does admitting whole VID rows (v1.1.1
      semantics: any qualifying sample admits the row, so zero-support samples join the het population) raise
      MAD 9 → 11? Direction is predicted (v1.1.1's MAD is *larger*), which is what makes it falsifiable.
- [ ] **3. Propose four fixes on their branches** — owner approval required before any push:
      streaming RD train (defect 1, patch ready), `computeCountCutoff` tolerance (defect 3),
      `1.4826` in first-pass `hetCutoff` (defect 7), support threshold in `ValidateSRCutoffs` (defect 5).
- [ ] **4. L1 remainder — PE has no diagnostics file at all** (SR has `sr_cutoff_diagnostics.txt`);
      that asymmetry is itself a gap worth reporting.
- [ ] **5. L3** `FilterBatchSites` / `FilterBatchSamples` / `GenerateBatchMetrics` vs baseline
      (`staging/GenotypeBatch.all_samples.metrics.tsv` already downloaded). **Not started.**
- [ ] **6. L2** `GenotypeSVs` + `ConcatVcfs` + `SeparateDepthPesr` on identical inputs → GT/GQ cross-tab
      vs baseline `genotyped_{pesr,depth}_vcf`. **Not started.** Optionally `gatk-sv-profile` paired mode
      (`<profiler-checkout>`; its `preprocess`/`run` need a `gatk` executable — never exercised here).
- [ ] **7. Judge the SR frequency-cutoff strategy change** (§4.2) — needs the L2 downstream comparison,
      not more table reading.
- [ ] **8. Decide where these docs live**: workspace root is not a git repo, so `CLAUDE.md` and
      `**` are untracked. Ask before creating a repo or moving them into a clone.

## 8. Environment

- Java 17 required (Java 11 fails `build.gradle:147`):
  `JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home`; build
  `./gradlew localJar` (~51 s) in `<gatk-checkout>`; jar in `<gatk-checkout>/build/libs/`.
- No Docker, no Cromwell, no R/Rscript, no python2 → v1.1.1 shell path cannot run locally (fine: baseline reused).
  `bedtools`, `tabix`, `bcftools`, `samtools`, `gsutil` (ADC as `<you>@<institution>`) available.
- 24 GB RAM Mac, 128 GB free disk. Do **not** run two full-interval trainers concurrently.
- Repos: worktrees under `<worktrees>/` (`gatk-aou` detached `18f211cec`, `gatk-sv-scale` `ed9b4846`,
  `gatk-sv-v111` `b5d5049c`, `gatk-sv-profile` `6bc1dc7`). `~/IdeaProjects/*` clones are **read-only**
  — never switch branches there.
- Two earlier 30-min scout subagents timed out with no output; keep delegated work short or inline.
- SSH to `<host>` / `<host>` fails (`Permission denied`) — no remote compute available.

## 9. What "good" looks like (expected values for the next check)

So the next session can *judge* a result, not just read it.

| next check | pass condition | how to tell it failed |
|---|---|---|
| ~~RD depth-value fix (§7.1) reproduces v1.1.1's distribution shape~~ | **CONFIRMED (§15.5).** state-2 mean 1.0078546 vs **1.0080** (−0.02 %); state-0 sd 0.006301943860032662 vs **0.00630194386003265**; hom-del cutoff 0.1077624 vs **0.10688** (1.008×) | the predicted failure branch ("still ≥5× → it's the bin *population*") is what actually happened, and the fix is R1+R2 in §15.1 |
| ~~same, judged the other way~~ | **PARTED.** state-2 mean is near 1.0 ✓ but `sd_het` ratios are **0.818 / 0.900** vs the ≥0.9 bar → the failure branch fires exactly as written: *"residual SR/PE-side population defect → the §6 gate axis now matters"* | — (this is now queue item 2) |
| ~~RD state-0 SD itself~~ | **RESOLVED — baseline 0.00630 is real, not implausibly tight.** Java reproduces it to 14 s.f. from the same inputs. The "10.5×" headline *was* Java being noisy, and the noise came from the 10-bin default + interval population, not from v1.1.1 being narrow | — |
| `--num-bins` / curated-loci fix applied to the branch | RD depth table within ~1 % of baseline on state-0 mean/sd/cutoff **and** state-2 mean ≈1.008; PESR-side state-1/2 ≈ 0.7038/1.2446 (i.e. the `1±sep` clamp stops firing) | state-2 mean < 0.97 → bins still 10 (check `--num-bins` actually reached the command line in the Cromwell log) |
| `computeCountCutoff` tolerance fix | `pe_count` **8**, PE two-sided gate `pe_count/2` = **4** | still 7 → tolerance applied on the wrong side of the comparison |
| first-pass `1.4826` fix | `first_pass_het_cutoff` 46.16 → **52.511** (= 33 + 1.645·1.4826·8, same inputs) | 46.16 unchanged → constant added in `sd_het` only, not in the filter |
| Phase 2a copy-state dump for the 2×2 | per-(site,sample) RD states for ~**10^5** clustered sites (v1.1.1 trained on ~130k), i.e. not the 69/790 match rate the post-`SeparateDepthPesr` VCF gave | match rate still ~10 % → wrong VCF (RD-only) handed to the emulator |
| patch equivalence | `train_sub8_streamcheck` vs `train_full` SR/PE tables identical (they were), RD Δ ≤ 3.5e-11 | any material Δ → heap-dependent state, patch is not a pure refactor |
| determinism | rerunning `train_full_stream` reproduces §4.2 exactly | any diff → the jar or inputs changed (see §5 provenance warning) |

## 10. Gotchas actually hit (verbatim error text)

| gotcha | the error you will see | what to do |
|---|---|---|
| Java 11 default on this Mac | `A Java 17 compatible (Java 17 or later) version is required to build GATK, but 11 was found.` (`build.gradle` line 147) | `JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home ./gradlew localJar` (~51 s) |
| RD train OOM at the task's own memory default | `java.lang.OutOfMemoryError: Java heap space` at `DepthEvidenceGenotyper.train(DepthEvidenceGenotyper.java:202)` ← `TrainSVGenotyping.trainCopyNumberSites(TrainSVGenotyping.java:734)`, after `Elapsed time: 35.21 minutes`, `Runtime.totalMemory()=15032385536` with `-Xmx14g` | use the patch in §5, or the 246k subsample *for SR/PE only* (§4.2: SR/PE are interval-insensitive, RD is not) |
| Terra API hostname | `curl: (6) Could not resolve host: api.terra.bio` (re-verified 17:1x) | base `https://api.firecloud.org/api/` — unauthenticated `GET /api/version` gives `401`, which is the *expected* good sign; add the ADC token |
| FISS package name | `ERROR: No matching distribution found for fiss` (re-verified) | `pip install firecloud` (v0.16.39 installed in `.venv`) |
| v1.1.1 shell genotyper cannot run locally | `Rscript: not found`, `python2: not found` (re-verified) | don't try; reuse baseline outputs (§2) |
| BGZF speed on Apple Silicon | `WARN IntelInflaterFactory - IntelInflater is not supported, using Java.util.zip.Inflater` | not an error; expect slower-than-Terra wall-clock (full run ≈46 min here) |
| manifest shape | entity attributes are at `manifest['entities']['sample_set']['all_samples']` — **flat**, no `attributes` key (my first resolver raised `AttributeError`/`KeyError` assuming otherwise) | index by entity *type*, then name |
| run-dir collision (fixed) | `OUT=` env override silently ignored because `run_train_full.sh` assigned `OUT` *after* the `${OUT:-}` default → the sub8 run overwrote `runs/train_full/train.log`, **destroying the OOM log** (see §13 "evidence lost") | script fixed (single `OUT=${OUT:-...}` at line 13); always `ls $OUT` after launching |
| `du` on hardlinks | `du -sh staging` says 20 G though the matrices are shared with `~/Work/ref_panel_1kg` | `stat -f '%l'` before concluding what deletion frees |

## 11. Deliverables this session (file → what it is)

| path | what | tracked? |
|---|---|---|
| `CHECKPOINT.md` | this document | **no** (root is not a git repo) |
| `CLAUDE.md` | standing instructions; corrected (see §12) | no |
| `PLAN.md` | the comparison plan + pointer here | no |
| `patches/0001-streaming-rd-train.patch` | Welford/streaming RD train, 8,060 bytes | no |
| `run_train_full.sh` | trainer driver; now honours `OUT`/`JAR`/`XMX`/`INTERVALS` | no |
| `run_train_chr20.sh` | chr20 slice variant (scale demo only) | no |
| `stage_inputs.py` | stage baseline inputs; `--link-dir` adopts local files by basename+exact size | no |
| `fetch_baseline.py`, `recon.py`, `terra.py` | fiss read helpers (`confirm=True` required to mutate) | no |
| `recompute_het_population.py` | SR het/hom population emulator (v1.1 vs Java recipes, gate sweep) | no |
| `manifests/baseline_v111.json` | 11.8 MB frozen baseline inputs/outputs, steps 05–10 | no |
| `runs/{train_chr20,train_full,train_sub8_streamcheck,train_full_stream}/` | outputs; semantics differ — §3 table | no |
| `run_rd_population_probe.sh` | RD population/bins/exclusions probe: 2 arms (56 curated loci vs 8,736 random condensed) × env `SUFFIX`/`DEPTH_EXCL`/`EXTRA_ARGS`; ~30 s/arm because RD ignores `-V` | no |
| `run_train_definitive.sh` | the §15.5 reference run (curated loci + `--num-bins 100000` + real `bin_exclude` + whole-cohort `-V`); honours `OUT`/`JAR`/`XMX`/`INTERVALS`/`DEPTH_EXCL`/`NUM_BINS`/`EXTRA_ARGS` | no |
| `patches/proposal-rd-training-config.patch` | **proposal, not applied**: adds `File? rd_training_intervals` + `Int n_RD_genotype_bins = 100000` to `GenotypeBatch.wdl` and wires `--training-intervals`/`--num-bins`; verified `git apply --check` clean @ `ed9b4846` | no |
| `runs/{rd_curated56,rd_curated56_real,rd_curated56_bins100k,rd_rand8736,rd_rand8736_real,rd_rand8736_bins100k,train_rd_curated,train_definitive}/` | §15 arms + runs | no |
| `runs/train_binsonly/` | §16 counterfactual (production population, bins fixed only); may still be running | no |
| `staging/{bin_exclude.hg38.gatkcov.bed.gz,train.curated56.intervals.bed}` | the two assets R1/R3 need | no |
| `reports/het_population.json` | chr20 population-recipe sweep result | no |
| `<worktrees>/{gatk-aou,gatk-sv-scale,gatk-sv-v111,gatk-sv-profile}` | isolated worktrees | separate repos (§0.1) |

## 12. Corrections — earlier claims this session proved WRONG

Each was load-bearing; the old text is fixed in place, and named here so it stays fixed.

1. **`sr_count` conversion.** Old claim (`CLAUDE.md` open item, mine from earlier this session): both
   implementations invert `-log10(1-e^-k)`. **Wrong**: the metric is `-log10(e^-k) = k/ln10`, so
   `k = p·ln10` in *both*. Verified: `SR_sum_log_pval 4.664513885836166 × ln10 = 10.740` → `sr_count 10`,
   which equals the baseline's 10 exactly.
2. **"v1.1 has no per-sample SR threshold; the zero-filled `SR_sum` matrix fattens the het tail."**
   **Wrong.** v1.1.1 gates per sample: `SR_genotype.opt_part1.sh:23-27` builds `two.sided.pass.txt` from
   VID@sample pairs with `$NF > sr_count/2` on *both* sides. What remains is *pair-level* (Java) vs
   *VID-row-level* (v1.1.1) gating — a smaller, still-unmeasured difference.
3. **RD hom-del cutoff inflation 3.06× (0.1069 → 0.3274).** **Superseded**: that number came from the
   interval-subsampled run. Full interval set → **2.40× (0.1069 → 0.2561)**, SD ratio **10.51×**.
4. **"`median_hom` 84 vs 93 follows from the missing `1.4826`."** Over-stated. In the chr20 emulation the
   `1.4826`-corrected filter changed `first_pass_het_cutoff` 54.515 → 60.072 but left `median_hom` at 89.5
   in both recipes. The omission is real (verified by code + numerics); its *median_hom* effect is not established.
5. **"RD state-0 SD 2.17× v1.1" and "`sd_het` ≈ 58 % of v1.1"** (old `CLAUDE.md` open items, from the AoU
   516-sample run) — replaced by this cohort's measured 10.51× and 0.727×/0.900×.
6. **"One mechanism being missed for PE and SR shrinking together."** Answered: both evidence types
   partition het/hom training observations by **RD copy state**, so one RD defect propagates to both.
7. **"Subsampling RD intervals only affects RD."** Half-wrong in the *safe* direction: it does not move
   SR/PE params at all (§4.2), but it moved the RD table by 22 % — so report RD from the full set only.
8. **"The baseline's raw MADs are SR 14 / PE 12."** (inherited from `CLAUDE.md`, which used the 516-sample
   AoU figures). For *this* 156-sample cohort: `26.8276/(1.4826·1.645) = 11.0` and `24.3888/2.4388770 = 10.0`.
   The residual after the RD fix is 2 MAD units (SR) and 1 (PE), not 6 and 5.
9. **"Java's RD depth *value* differs from v1.1.1's `RD_genotype`"** — the leading hypothesis of §4/§6 and of
   `CLAUDE.md` open item 1. **Disproved in §15.2**: same inputs → identical mean/sd to 14 s.f. What actually
   differs is the bin count (10 vs 100 000), the training population (condensed intervals vs curated loci),
   and — in our replays only — the exclusion asset.
10. **"staging's `depth_blacklist.sorted.bed.gz` is a hand-made 48-row stub"** (§0.2's own words). It is a real
    pipeline asset, md5-identical to the public object; the replay error was feeding *that* asset where
    production feeds `bin_exclude.hg38.gatkcov.bed.gz`. §0.2 is corrected; the wording "stub" survives only in
    old run-dir notes.
11. **"Defect 1 (RD OOM) is a Java defect needing the streaming patch."** It is downstream of defect 9: with
    the curated population the whole trainer runs in **4.92 min** at 1.19 GB. The patch is still worth shipping
    as insurance (and it is numerically verified), but it stops being a prerequisite the moment R1 is fixed.

## 13. Claims ledger

**Added by §15/§15.5 (21:00-21:40 EDT, all re-runnable):** the exact-reproduction row
(`runs/rd_curated56_bins100k` vs `staging/all_samples.depth.depth_sepcutoff.txt`), the 7-arm table in §15.2,
the `train_definitive` table in §15.5, the `--num-bins` defaults (read from `TrainSVGenotyping.java:238` /
`GenotypeSVs.java:239`, and `grep -n "num-bins" wdl/GenotypeBatch.wdl` → empty), the asset md5s
(`gsutil stat` vs local), and that `proposal-rd-training-config.patch` passes `git apply --check`.
**Still inferred there:** that R1 alone would suffice in production (only tested with R2+R3 in combination,
and the R1-only full-cohort run `train_rd_curated` kept the stub exclusions and 10 bins); that SR/PE's
residual 1-2 MAD units are gate semantics (mechanism argued, never measured).

**Verified by command this session (re-runnable):** all §4.2 numbers (tables in `runs/train_full_stream/`,
diagnostics in `all_samples.sr_cutoff_diagnostics.txt`); baseline values in §4.1 (`staging/all_samples.{sr,pe}_metric_file.txt`,
`all_samples.{depth,pesr}.depth_sepcutoff.txt`, `all_samples.cutoffs`); patch equivalence (§5, diff of the
two run dirs); branch tips and "nothing pushed" (§0.1, `git ls-remote`); the §10 error strings; clock (§ date line).

**Inferred, not measured:** RD as the *cause* of the SR/PE `sd_het` gap (mechanism is code-verified —
both populances key on copy state — but the 2×2 in §6 has never been run); production relevance of the OOM
at AoU scale (156-sample OOM is measured, AoU extrapolation is not); whether `GenotypeSVs` output VCFs differ
(L2 never run).

**Evidence lost:** the raw OOM log. `runs/train_full/train.log` was overwritten by the retry, so
`grep -rl OutOfMemoryError ` now matches only this document. Reproducer if needed: `git -C <gatk-checkout>
stash && ./gradlew localJar` (unpatched), then `XMX=14g INTERVALS=$PWD/staging/train.full.intervals.bed
OUT=$PWD/runs/oom_repro bash run_train_full.sh` → expect the trace at ~35 min. Code-level corroboration
stands: unbounded `List<DepthGenotypeResult>` + `GenotypeBatch.wdl:264-265` `mem_gb: 16`.

**Deliberately not measured:** anything requiring raw genomic reads or a Terra submission; AoU cohort
(300+ samples) behaviour; `gatk-sv-profile` paired mode; GQ concordance; `FilterBatch`/`GenerateBatchMetrics`;
wall-clock parity with Terra; whether v1.1.1's state-0 SD of 0.00630 is *plausible* (§9).

## 14. Gates that fail *after* the real work is done (don't throw away output)

- `TrainSVGenotyping` **never fails on a degenerate SR cutoff grid** — deliberate, because Cromwell only
  delocalises outputs on success and the diagnostics file is the point. So a run that "failed" downstream may
  still hold usable `*_geno_params.tsv` + diagnostics; read `all_samples.sr_cutoff_diagnostics.txt`
  (`## SR_SELECTION_STATUS`) before discarding a directory.
- `ValidateSRCutoffs` (branch `<branch-under-test>`) gates the genotyping scatter on those diagnostics and is
  the step most likely to fail *after* training succeeded. The trained tables survive it; only the scatter is blocked.
- The SR **and** PE numbers are reproducible from the frozen staging inputs in ~46 min, so a lost `runs/` dir
  costs a rerun, not a lost insight. The staging inputs (20 GB, mostly hardlinks) are the expensive part — keep them.

---

## 15. QUEUE ITEM 1 CLOSED (same day, ~21:30 EDT): the RD depth value is a *faithful port* — the whole RD table gap is input configuration

Four cheap local arms + one full run, all `TrainSVGenotyping` from `<gatk-checkout>` (patched `18f211cec`), all
arguments identical to `GenotypeBatch.wdl` except the inputs under test. Driver: `run_rd_population_probe.sh`
(env: `SUFFIX`, `DEPTH_EXCL`, `EXTRA_ARGS`, `XMX`); full run: `run_train_definitive.sh`.
The probe arms pass `-V train.chr20.vcf.gz` (836 records) instead of the whole-cohort VCF because
`trainCopyNumberSites()` reads only `trainingLocs` + the depth matrix (`TrainSVGenotyping.java:716-744`) —
**RD tables are VCF-independent**, so an RD experiment costs ~30 s, not 46 min.

### 15.1 Three configuration differences from v1.1.1 (all independently confirmed)

| # | difference | v1.1.1 | new pipeline (main / `<branch-under-test>`) | evidence |
|---|---|---|---|---|
| **R1** | **RD training population** | 56 curated reviewed CNV loci: `MakeTrainingBed` → `/opt/RdTest/train_hg38_reviewed_final.bed` (hg38) / `1kg.train.loci.bed` (hg19), i.e. ~56×156 ≈ 8.7 k observations whose copy state is *known truth* | **every condensed interval in the batch** (1,462,095 here) whose copy state is *assigned by the same fixed seed cutoffs* 0.25/0.75/1.25/1.75 that the fit then re-derives boundaries from → circular | `<v111-checkout>/wdl/TrainRDGenotyping.wdl` (MakeTrainingBed); `<gatk-sv-checkout>/wdl/GenotypeBatch.wdl:80-107` (`training_intervals` ← condensed intervals). Local copy of the bed is md5-identical to repo **and** to `gs://gatk-sv-resources-public/.../resources/v1/train_hg38_reviewed_final.bed` (`N4q6UoFADEI2z2VyHuUnVQ==`) → the probe used the exact asset v1.1.1 used |
| **R2** | **RD bin count (`--num-bins`)** | `n_RD_genotype_bins = 100000` at *train and* apply (`TasksGenotypeBatch.wdl:359 -i ~{n_bins}`) | `TrainSVGenotyping.numBins` **defaults to 10** (`TrainSVGenotyping.java:238`) while `GenotypeSVs.numBins` **defaults to 100000** (`GenotypeSVs.java:239`) and **the WDL passes neither** → cutoffs are fit on 10-bin *compressed* depth values and applied to 100 000-bin (uncompressed) ones. `n_RD_genotype_bins` survives on the new branch only in the R-side calls (`GenotypeDepthPart1.wdl:16,41`, `GenotypePESRPart1.wdl:18,61`) | `grep -n "num-bins" wdl/GenotypeBatch.wdl` → empty; baseline input `GenotypeBatch.n_RD_genotype_bins = "100000"` |
| **R3** | **depth exclusion asset** | `-y bin_exclude` = `${workspace.bin_exclude}` = `bin_exclude.hg38.gatkcov.bed.gz`, **1,512,310 × 100-bp poor bins** (151 Mb, 5 % of the genome; 1:1 with the 100-bp RD matrix bins) | production is the *same* (`inputs/templates/.../GenotypeBatch.json.tmpl:12 "GenotypeBatch.depth_exclusion_intervals": "${workspace.bin_exclude}"`) — **our replay was not**: staging held `depth_blacklist.sorted.bed.gz` (48 rows; md5 `nfqEmtnuINq9DjSrtFvDnQ==` == the public object, so a real asset, but the wrong one) | `manifests/baseline_v111.json` → `/steps/10-GenotypeBatch/inputs/GenotypeBatch.bin_exclude/value`; `gsutil stat` hashes |

Corrections this forces in §0.2: the depth-exclusion file in staging was described as a hand-made stub —
it is a genuine pipeline asset (`depth_blacklist.sorted.bed.gz`, 48 centromeric-scale blocks), just not the
one production feeds.

### 15.2 Java reproduces v1.1.1's RD table **exactly** once R1+R2+R3 are removed

State-0 row of the trained table (the free boundary; state-2 row is the one that feeds the `1±sep` clamp):

| run | population | bins | depth excl. | state0 mean | state0 sd | state0 cutoff | state2 mean | state2 sd |
|---|---|---|---|---|---|---|---|---|
| **baseline v1.1.1** (`staging/all_samples.depth.depth_sepcutoff.txt`) | 56 curated | 100 000 | real | 0.0454528152647421 | 0.00630194386003265 | 0.106875141793862 | 1.00804145738871 | 0.0614836083585576 |
| `runs/train_full_stream` (production-equivalent config) | 1.46 M condensed | 10 | stub-as-fed | 0.08789252 | 0.06626075 (**10.51×**) | 0.25607121 (**2.40×**) | 0.93610003 (−7.1 %) | 0.10332972 |
| `runs/rd_curated56` | 56 curated | 10 | stub | 0.04889208 | 0.01463064 (2.32×) | 0.16428337 | 1.01585390 | 0.06580215 |
| `runs/rd_curated56_real` | 56 curated | 10 | real | 0.04727844 | 0.00995725 (1.58×) | 0.13646164 | 1.01507277 | 0.06481734 |
| **`runs/rd_curated56_bins100k`** | 56 curated | **100 000** | real | **0.045452815264742** | **0.006301943860032662** | 0.10776243 (**1.008×**) | **1.00785456** (−0.02 %) | 0.06162524 |
| `runs/rd_rand8736_real` (size control) | 8 736 random condensed | 10 | real | 0.05888391 | 0.04635954 (7.36×) | 0.21607856 | 0.93770729 | 0.10281595 |
| `runs/rd_rand8736_bins100k` | 8 736 random condensed | 100 000 | real | 0.05684455 | 0.03747789 (5.95×) | 0.17843658 | **1.01211031** | 0.09919924 |

**The state-0 mean and sd in the last-but-one row are identical to the baseline to 14 significant figures**
(0.045452815264742 vs …7421; 0.006301943860032662 vs …630194386003265). States 1–4 still differ by ≤0.07 %
(0.502521 vs 0.502753; 1.007855 vs 1.008041; 1.540391 vs 1.540742; 2.077875 vs 2.079401) — a handful of
boundary observations between R's `(prev, cutoff]` and Java's `getCopyState` / the `MIN_INTERVALS_AFTER_EXCLUSION`
fallback ladder. Not worth chasing at 5e-4.

So **the RD depth-value recipe, the MAD-free mean/sd accumulation and the cutoff rule are the same math** —
`DepthEvidenceGenotyper.getCutoff` (`:230-235`) *is* `generate_cutoff.R::zcalc`; verified arithmetically on the
baseline numbers: zcalc(0.0454528, 0.00630194, 0.5027525, 0.0406171) = 0.1068751 = baseline state-0 cutoff, and
the same function reproduces the baseline's *PESR-side* 0.7037636 and 1.2445660 exactly. `DepthMatrixLoader`
carries RdTest.R's own comments ("Approximate rebinned per-sample medians") and its constants
(`largeVariantSize=2500000`, `largeVariantPoints=500`, `largeVariantWindow=2000` = RdTest.R's `-x/--verylargevariantpoints/--verylargevariantwindow` defaults).
The separation rule also agrees: at `sep=0.3848` the baseline clamps state-1→0.615152 and state-2→1.38485, at
`sep=0.2036` it leaves 0.7037636/1.2445660 alone — one `1∓sep` rule, applied to both sides, exactly Java's
`applySeparation` (`TrainSVGenotyping.java:756+`).

### 15.3 Attribution: which knob moves which number

| symptom | cause | measured |
|---|---|---|
| state-0 **sd** 10.51× | R1 dominant, R2 secondary, R3 (our replay only) tertiary | population alone: 10.51×→2.32×; + real exclusions: →1.58×; + 100 000 bins: →**1.000×** |
| state-0 **cutoff** 2.40× (hom-del threshold too permissive) | same three | 2.40×→1.54×→1.28×→**1.008×** |
| state-2 **mean** 0.9361 instead of ≈1.008 (the number that *forces* the `1+sep` clamp to fire) | **R2 alone** — the 10-bin compression | with 100 000 bins the mean is ≈1.01 even on random condensed intervals (1.01211) |
| PESR-side RD cutoffs 0.7924/1.2036 (clamp firing where v1.1.1 had trained values 0.7038/1.2446) | knock-on of the state-2 mean in the row above | `runs/train_rd_curated`: 0.7140/1.2531 → **+1.5 % / +0.7 % of baseline** |
| `sd_het` (SR) 19.511 vs 26.8276 | R1 confirmed to move it, not yet fully explained | `runs/train_rd_curated`: **21.950** (raw MAD 8 → 9; baseline 14) |

R1 is not merely "n is smaller": the size control `rd_rand8736*` (8 736 random condensed intervals, i.e. the
same observation count as the 56 loci) reproduces the *full-run* pathology, not the baseline. It is the
**identity** of the population — curated loci carry real CNVs, so copy states 0/1/3/4 are populated by real
biology; whole-genome condensed intervals populate them with coverage noise that was *defined* into those
states by the seed cutoffs.

### 15.4 What this re-labels in the defect list

* **Defect 2 (RD distribution) is a configuration defect, not a Java defect.** Two production-relevant changes:
  (R2) pass `--num-bins ${n_RD_genotype_bins}` to `TrainSVGenotyping` in `wdl/GenotypeBatch.wdl`, or align the
  Java default with `GenotypeSVs`' 100 000 — this one is a one-line fix and it also removes an *internal*
  train/apply inconsistency that exists regardless of v1.1 parity; (R1) feed a curated known-CNV training set
  for RD, which needs an owner decision because "train RD on the batch's own intervals" looks deliberate —
  note the asset is already in the public resources bucket (`.../resources/v1/train_hg38_reviewed_final.bed`),
  so it is feedable as a workspace attribute with no new data.
* **Defect 1 (RD train OOM at the task's own 16 GiB) is downstream of R1.** With the curated population the
  whole trainer ran in **4.92 min** (vs 46 min) at `Runtime.totalMemory()=1195376640` (1.19 GB). The streaming
  patch is then belt-and-braces rather than a prerequisite — but it is still required as long as R1 stays.
* **Replay fidelity defect (mine, and it was flattering the new pipeline in one direction and hurting it in
  another):** every replay in §4 ran with `depth_blacklist.sorted.bed.gz` instead of `bin_exclude.hg38.gatkcov.bed.gz`.
  Exclusion barely matters for 2-kb condensed intervals (`DepthMatrixLoader.MIN_INTERVALS_AFTER_EXCLUSION = 10`
  with `PADDING_FRACTION_EXCLUSION = 0.1` → the fallback ladder reinstalls unexcluded bins), but it is worth
  32 % of the curated-loci state-0 sd. Production numbers will therefore be *better* than §4.2's state-0 sd.

### 15.5 Full-cohort confirmation: `runs/train_definitive` (`run_train_definitive.sh`, 5.50 min)

R1+R2+R3 together, whole-cohort training VCF (`staging/all_samples.filtered_pesr_merged.vcf.gz`, 93,269
records), `-XL chrX -XL chrY`, `-Xmx8g`, `Runtime.totalMemory()=1216348160`.

| trained value | baseline v1.1.1 | `train_full_stream` (production config) | **`train_definitive`** | vs baseline |
|---|---|---|---|---|
| RD depth state-0 mean / sd | 0.0454528 / 0.0063019 | 0.0878925 / 0.0662608 | **0.045452815264742 / 0.006301943860032662** | identical to 14 s.f. |
| RD depth cutoffs (state0→4) | 0.106875 / 0.615152 / 1.38485 / 1.774751 / 2.25 | 0.256071 / 0.615152 / 1.38485 / 1.650310 / 2.25 | **0.1077624 / 0.6151515 / 1.3848485 / 1.7760893 / 2.25** | +0.8 % / 0 / 0 / +0.08 % / 0 |
| RD **PESR** cutoffs (state1, state2) | 0.7037636 / 1.2445660 | 0.7923839 / 1.2036394 (clamp firing) | **0.7011975 / 1.2435646** | −0.37 % / −0.08 % |
| RD depth state-2 mean (feeds the `1+sep` clamp) | 1.0080415 | 0.9361000 | **1.0078546** | −0.02 % |
| SR `sr_count` | 10 | 10 | **10** | ✓ |
| SR `sd_het` / raw MAD | 26.8276 / **11** | 19.5110 / 8 | **21.949893 / 9** | 0.818× (was 0.727×) |
| SR `median_hom` | 78 | 68 | **69** | 0.885× |
| PE `pe_count` / `sd_het` / raw MAD | 8 / 24.3888 / **10** | 7 / 21.9499 / 9 | 7 / **21.949893** / 9 | 0.900× (unchanged) |
| PE `median_hom` | 76 | 70 | **71** | 0.934× |
| SR rare/common bins, pass/fail tallies | 0/2, 2/156 | identical | identical (11131/36328, 151701/1097543) | RD-independent, as §4.2 found |
| SR cutoff grid winner | .1/.6, .9/.9 | .2/.6, 1/.6 | .2/.6, 1/.6 | defect 5 untouched by this fix |

**Correction to a number carried in `CLAUDE.md`:** the baseline's *raw* MADs for this cohort are SR **11**
and PE **10** (`26.8276 / (1.4826·1.645) = 11.0`, `24.3888 / 2.4388770 = 10.0`), not 14/12 (those were the
516-sample AoU figures). So the residual `sd_het` gap after the RD fix is **2 MAD units for SR, 1 for PE** —
a much smaller hole than §4.2 implied, and plausibly all observation-selection (the §6 gate axis), which is
now the *only* axis left: RD states are reproduced, so both evidence types' het/hom partitions key on
baseline-equivalent copy states.

RD side of the trainer: first-pass SR diagnostics moved with the fix — `first_pass_variants 206 → 266`,
`first_pass_het_observations 1015 → 1319`, `het_median 33.0` (unchanged in all runs), `mad_raw 8 → 9`,
`hom_observations 716 → 745`, `hom_median 68 → 69`. `first_pass_het_cutoff 47.805 = 33.0 + 1.645·9`
confirms **defect 6 (the dropped 1.4826) is still live** — v1.1.1's rule would give 54.87 here.

Two things to be suspicious of, both unchecked:
1. SR and PE `sd_het` are now the *same number* (21.949893, raw MAD 9) — the code paths are separate
   (`TrainSVGenotyping.java:431-510` uses distinct `splitReadGenotyper` / `discordantPairGenotyper` objects),
   so this is most likely two integer medians landing together, but there is **no PE diagnostics file**
   (`sr_cutoff_diagnostics.txt` is SR-only), so it cannot be confirmed from output. Writing PE's first-pass
   stats the same way would close this cheaply.
2. `train_rd_curated` (curated loci, **stub** exclusions, 10 bins) is already within 1.5 % / 5 % of baseline on
   the PESR-side state-1/state-2 cutoffs, so the PESR numbers were mostly fixed by R1 alone; R2's contribution
   shows up in the state-0 tail and the state-2 mean.
3. **How `applySeparation` actually works** (`TrainSVGenotyping.java:755-786`, now read in full, corrects any
   earlier vagueness): the fitted quantile cutoff is clamped *toward* the fixed separation —
   state 1 `if (c > 1 - sep) c = 1 - sep`, state 2 `if (c < 1 + sep) c = 1 + sep`. Therefore:
   * **Depth-side state-1/state-2 cutoffs are never fit-determined** — the fit wanted narrower spacing than
     `DEPTH_SEP = 0.384848`, so both clamps fire in baseline *and* new, and the published cutoffs are literally
     `1 ∓ 0.384848`. That makes **defect 4 the whole story for those two numbers**: whatever single
     `DEPTH_SEP` the WDL passes *is* the hom/het boundary (here it matches baseline only because both happen to
     use the DUP row's 0.3848; baseline's DEL row was 0.3746).
   * PESR-side (`sep = 0.2036`): baseline (0.70376/1.24457) and `train_definitive` (0.70120/1.24356) are both
     **pure zcalc**, no clamp. In `train_full_stream`, state-2 came out 1.20361 = `1 + 0.2036` → the clamp *was*
     binding there, i.e. the population pathology had pushed the fit below the floor. After R1+R2+R3 the fit is
     again the active constraint, which is *why* it now agrees with baseline to 0.08 %.

---

## 16. Mechanism behind R2 (`--num-bins`), and the run that tests the one-line fix alone

`--num-bins` is **not** a buffer size — it is the number of bins `DepthMatrixLoader` *compresses each interval's
depth vector down to* before it is used: `TrainSVGenotyping.java:702` passes `numBins` into the loader, and
`DepthMatrixLoader.java:244-257` computes `targetBins` / `compression = span / numBins` and resamples. Two
consequences, both now mechanical rather than inferred:

* **Train/apply really are different data.** Training sees 10 resampled values per interval; `GenotypeSVs`
  (`numBins = 100000`, i.e. ≥ any interval's raw bin count) sees every 100-bp bin. The mean/sd fitted at
  training time therefore describe a *different random variable* than the one the cutoffs are applied to —
  independent of any v1.1 comparison, this is self-inconsistent.
* **Edge trimming scales with it.** `DepthMatrixLoader.java:144-152` drops the first and last internal bin
  whenever `numBins >= MIN_BINS_FOR_TRIMMING (4)`: at 10 bins that discards 20 % of the interval's values, at
  100 000 it discards 2 bins out of ~20 (a 2-kb interval). So R2 changes both the resolution *and* the
  effective fraction of each interval that is used.

`runs/rd_curated56_bins100k` reproduces v1.1.1's state-0 mean and sd **to the last stored digit**
(0.045452815264742 / 0.006301943860032662 vs 0.0454528152647421 / 0.00630194386003265), which is what told us
the binned-value recipe is a faithful port: identical inputs, identical output, different JVM and language.

**In flight when this was written**: `runs/train_binsonly` (`nohup`, started 21:39 EDT) = production population
(all 1,462,095 condensed intervals) + the **real** `bin_exclude` + `--num-bins 100000` + whole-cohort `-V`. It
isolates the one-line fix from R1, which is the actual owner decision ("is `--num-bins 100000` alone enough, or
do we need the curated loci?"). Expect ≥46 min (the 10-bin full run) and more memory per interval; check with
`pgrep -fl TrainSVGenotyping` / `tail -f runs/train_binsonly/train.log` (`Training on 1462095 CNV sites`
confirmed at 21:39:05) and read §15.2's table format. If it is still running in a later session, it is *not* a
stale process — do not kill it without asking.

**Measured rate (21:41 EDT)**: `RD training progress: 100000 / 1462095 intervals` at 21:41:23, i.e. ~2.3 min
per 100 k intervals at `--num-bins 100000` → RD ≈ 34 min, whole run ETA **≈ 22:25 EDT**, RSS 2.0 GB with
`-Xmx8g`. Progress logs every 100 k intervals (`grep -c "RD training progress"` → 14 in a complete full run, so
14 is the "RD finished" signal). Note this arm is *slower per interval* than the 10-bin run precisely because
R2 removes the compression — that is the cost side of the one-line fix, worth quoting to the owner.

---

## 17. ⚠ AUDIT RESULTS (22:20-22:45 EDT) — **R1 IS REFUTED AS A PRODUCTION DEFECT. READ THIS BEFORE §15.**

Four fresh-context falsification auditors were sent out (`reports/audit_*.md`; 2 completed, 2 timed out
and were resumed for narrowed questions). Two of my three findings did not survive. I re-verified the refutation
myself from the AoU tables on disk — it is correct.

### 17.1 What is actually true

**`GenotypeBatch.training_intervals` is ALREADY the curated reviewed-CNV bed in every checked-in deployment.**
Verified by me, not just the auditor:

```
inputs/templates/terra_workspaces/cohort_mode/workflow_configurations/GenotypeBatch.json.tmpl:20
    "GenotypeBatch.training_intervals": "${workspace.depth_training_bed}"
inputs/values/resources_hg38.json:26
    "depth_training_bed": "gs://gatk-sv-resources-public/hg38/v0/sv-resources/resources/v1/train_hg38_reviewed_final.bed"
```
(identical on `main`). So the "R1: production trains RD on the batch's condensed intervals" story is **a
replay artifact of my own driver**: `run_train_full.sh:21-23` awk's `train.full.intervals.bed` out of
`condensed_intervals.annotated.tsv` — an input I invented. §15.1-R1, §15.4's "R1 needs an owner decision", and
the `rd_training_intervals` half of the proposal patch are all **void**.

**The AoU production RD table has the curated-population signature, not the condensed-interval one** (my own
reading of `data_old/` v1.1.1 vs `data_<gatk-branch>-aou-18f211c/` new, same 516-sample batch):

| | v1.1.1 | new (prod) | ratio | vs my local condensed replay |
|---|---|---|---|---|
| state-0 mean | 0.0330363 | 0.0348932 | 1.056× | 0.0879 = 2.66× |
| state-0 **sd** | 0.0036288 | 0.0078591 | **2.17×** | **0.0663 = 18.3×** |
| state-0 cutoff | 0.0870245 | 0.1337107 | **1.54×** | 0.2561 = 2.94× |
| state-2 mean | 1.010539 | 1.016588 | 1.006× ✓ | 0.9361 = 0.926× ✗ |

So production RD was never "10.5×, diploid mean 7 % low". That signature only ever existed in my replays.

**R2 (`--num-bins`) survives and is now the *sole* production RD defect** — and the cross-cohort match is
striking. Locally, curated loci with the Java default `numBins=10` vs the baseline's 100 000 gave
state-0 **sd 2.32×** and **cutoff 1.5372×**; AoU production (same curated bed, `--num-bins` never passed) gives
**sd 2.17× and cutoff 1.5365×** — the same fingerprint 0.05 % apart on a different cohort (516 vs 156 samples),
while state-2 mean stays healthy in both. That is the mechanism, identified across two independent cohorts.

**R3 is downgraded to replay fidelity only** (pending the narrowed lane's answer on the production chain); the
32 % effect it had was measured on 56 large curated loci, where exclusion can bite, not on 2-kb intervals.

### 17.2 The audit's other corrections (all re-verified by me unless noted)

| my claim | verdict | what to say instead |
|---|---|---|
| §15.2 "reproduces the RD table **exactly**"; "states 1-4 differ ≤0.07 %, not worth chasing" | **FALSE as written** | only **2 of 15** cells match to 14 s.f. Means match ≤0.073 %, but **sd** is off: state-1 **−1.702 %**, state-4 −0.866 %, state-3 +0.798 %, state-2 +0.230 %. I recomputed all 15 myself. |
| "all five cutoffs agree within 0.8 %" | over-claimed | measured **+0.830 %**, and 3 of the 5 are not fit outputs at all (state-1/2 depth-side are literally `1−0.384848`/`1+0.384848`; state-4 is the hard 2.25 cap). The agreement exercises **2 free parameters**, not 5. |
| (auditor) the whole residual hom-del cutoff shift is carried by the **state-1 row** | plausible, **my zcalc reproduction of it failed** (I mis-keyed the formula and got −401) — do not cite as verified. Their Java-side `getCutoff` reproduction is separate and believed. |
| "defect 1 (OOM) is downstream of R1 → patch is belt-and-braces" | **REFUTED and unsound** | R1 doesn't exist, so the 1.46 M-interval population is what production *actually* trains on... except that it trains on the curated bed, so **the OOM is not live in production either** — but `--num-bins 100000` does strictly increase per-interval data, and the WDL still gives the task `mem_gb: 16, max_retries: 1`. Ship the streaming patch on its own merits (it is numerically verified), never gate it on a config decision. New datum: `train_binsonly` (condensed + bins 100000) = **39.87 min, 1.99 GB**. |
| "SR == PE `sd_het` is probably coincidence, but unprovable without a PE diagnostics file" | **under-claimed — it is provable, and I should have looked** | SR ≠ PE in **9 of 13** run dirs, including inside one process (`rd_curated56`: SR 12.194 vs PE 25.608; `rd_rand8736`: 12.194 vs 26.828). No leaked state. Both statistics quantise to multiples of K/2 = 1.2194, so ties are cheap. (I reran this scan myself.) |
| "the 8,736-interval control is observation-count-matched to 56 loci" | **WRONG by 156×** | 56 loci × 156 samples = 8,736 *cells*, but 8,736 intervals × 156 samples = **1,362,816** cells. Also uncontrolled: interval size (curated mean 31,906 bp vs 1,999 bp), which under `numBins=10` means the two arms fit differently-compressed values. Conclusion (population identity dominates) still survives — the random arms are 6-7× off *despite* 156× more data, and within the random family sd grows sub-linearly (8,736→0.0462, 12,001→0.0574, 1,462,095→0.0663) — but the control as labelled does not establish it. A truly count-matched arm (56 *random* 2-kb intervals) was never run. |
| "RD population is set **only** by `--training-intervals`" | overstated | also gated by `--median-coverage` (normalises every value), `--num-bins`, `--depth-exclusion-intervals`, `--n-training-states` (default 5; seed state ≥5 is **dropped**, `DepthEvidenceGenotyper.java:200`), `--large-variant-*`, and the RD file's sample set. The true and important half stands: **`-V` provably cannot affect RD** — three independent on-disk proofs, the best being `data_new`, `data_new_diagnostic` and `data_<gatk-branch>-aou-18f211c` RD tables all md5 `f20f3aaf0362d34e6d3ef4c7e72e50f8` while their SR tables differ (I verified this md5 myself). |
| "the circularity of seed-assigned copy states is a design defect of the new path" | **overstated** | v1.1.1 is **structurally identical** (`generate_cutoff.R:20-35` buckets on `seq(0.25, …, by=.5)` seeds), so circularity cannot explain any divergence. §15.1-R1's circularity argument is dead weight — delete it. |
| "identical to 14 significant figures" | precision nit | 14 is Java's TSV formatter floor (R prints 16); say "equal as printed to the shorter representation". |
| "`-XL chrX -XL chrY` protects the run" | incomplete | `-XL`/`-L` filter *driving variants*, **not** `--training-intervals`. Our curated bed happens to be autosome-only (so it was a no-op), but a bed with chrX/Y loci would train on them anyway. |

### 17.3 New candidate causes of the residual RD/SR/PE gap, found by the R1 auditor (unverified, high value)

1. **Boundary comparison is inverted between Java and R.** Java `DepthEvidenceGenotyper.java:58` uses
   `< upperBound` (a value exactly on a boundary goes to the **upper** state); R's `generate_cutoff.R` uses
   `<=`/`>` the other way. A boundary-tied observation flips state → this is exactly the class of difference
   that produces the **state-1 sd −1.70 %** residual, and it is *not* rounding noise.
2. **R drops ratio ≤ 0 from every bucket** (`> prev_cutoff` starting at 0); **Java admits [0, 0.25)** into state 0.
3. **Empty-state mean fallback differs**: Java `0.5*i` (state 0 → 0.0) vs R `(i+1)*0.5` (state 0 → 0.5).
4. **Latent NaN hazard**: an interval with zero usable bins yields `copyStates=2` with NaN medians
   (`DepthEvidenceGenotyper.java:131-140`) and the Welford update (:196-205) would NaN-poison state 2's
   mean/sd and hence the 1↔2 cutoff. No on-disk table shows NaN, so it has not fired *here* — but it would be
   silent. Worth a guard regardless.
5. Production corroboration of the separation-collapse defect (my defect 4): AoU's new RD row-1/row-2 cutoffs
   are **0.60100001 / 1.39899999 = exactly 1 ∓ 0.399** (AoU passes its own single `DEPTH_SEP`, v1.1.1's baseline
   used 0.62098/1.37902 = *its own* 1∓0.2209... no — v1.1.1's are also `1∓sep`, with sep 0.37902). The point
   stands: **those two numbers are the WDL's separation, not a fit**, in both versions.

### 17.4 Decisive next measurements (cheapest first)

1. **Read the resolved inputs of the AoU submission** that produced `data_<gatk-branch>-aou-18f211c` (one Cromwell
   `/metadata` inputs read, or the Terra method-config + `workspace.depth_training_bed`). This is the only
   remaining way to exclude a per-workspace override of `training_intervals`. If it says the curated bed (the
   templates say it will), R1 leaves the defect list permanently.
2. **`--num-bins 100000` on the *production* population is no longer the interesting experiment** (it was
   `train_binsonly`: RD still broken — state-0 sd 0.0629, state-3 mean 1.344 — only state-2's mean was fixed).
   The interesting one is **curated loci + everything else production-like + `--num-bins 100000`**, which is
   `runs/train_definitive`, already done: RD = baseline. So the production fix is one line and it is already
   validated locally.
3. **Per-observation RD check (the only thing that can close "faithful port")**: one
   `--output-training-vcf true` run on the frozen 156-sample cohort with the curated config, then compare
   per-(site,sample) copy state against `staging/all_samples.genotyped_depth.vcf.gz` FORMAT `RD_CN`
   (v1.1.1's own call, 5,386 records, curated loci confirmed present). Mean/sd agreement is permutation-invariant
   and cannot exclude a value→(sample, locus) mis-assignment; this can.
4. Boundary-semantics probes (17.3 #1-3) are answerable by reading 10 lines of each implementation plus, if
   needed, a tiny synthetic input — no cohort needed.

### 17.5 R2 and R3 verdicts came back late (both lanes were resumed with narrowed questions)

**R2 `--num-bins`: CONFIRMED, with my magnitude claims corrected.**
* v1.1.1 fitted **and** applied RD at `-i 100000` = effectively all bins: `wdl/GenotypeBatch.wdl:25 n_RD_genotype_bins`
  → `GenotypeDepthPart1.wdl:61` → `TrainRDGenotyping.wdl:53 n_bins = n_bins` → `TasksGenotypeBatch.wdl:359 -i ~{n_bins}`
  → `RdTest.R:1120/1126 loadData(..., bins, ...)` → `median_geno` → `generate_cutoff.R:14-38 mean()/sd()`.
  Baseline manifest carries `n_RD_genotype_bins = "100000"`. Inside `loadData` (`RdTest.R:388-401`)
  `bins` is clamped to `numInternalBins`, so 100 000 ⇒ **compression = 1, no rebinning**.
* **Why the Java default is 10, most likely**: `RdTest.R:81 make_option(c("-i","--bins"), default=10)` — the *R default*,
  which the WDL always overrides and which therefore never took effect (the `bins=10` at `RdTest.R:1220` is the
  plot-only matrix, `##Compress x-axis to 10 bins so it is easier to view###`). `TrainSVGenotyping.java:238`
  reproduces the dead default instead of the deployed value. Treat as inference, not proof.
* **My R2 numbers corrected**: holding the exclusion asset fixed, bins 10 → 100 000 moves state-0 sd **1.58×**, not
  the 2.32× I quoted (2.32× conflated R2 with R3, because `runs/rd_curated56` read the stub exclusion and
  `runs/rd_curated56_bins100k` read the real one). And the "state-2 mean 0.9533 → 1.0079" figure **is not in any run
  table and is retracted**: curated + bins=10 gives 1.015854, so the depressed state-2 mean (0.936) is the
  **interval-population** symptom, not the bin symptom.
* **Scope correction, and it matters**: `--num-bins` is *not* RD-only. The train loader (`:702`) feeds
  `computeDepthGenotype` (`:836-847`), whose copy states gate PE `trainableRecord:466 / addFirstPass:483 /
  addSecondPass:494` and SR `addFirstPass:519 / addSecondPass:543`. So fixing bins can move `sd_het`/`median_hom`
  too, and my "the RD fix moved SR 19.511 → 21.950" is an **unattributed blend of population + bins**. Only the
  Phase-2b SR grid accumulation (`:599-622`) is depth-free.

**R3 exclusion: PARTLY — chain confirmed, scope refuted.**
* Both chains confirmed end-to-end: `${workspace.bin_exclude}` → `GenotypeBatch.wdl:37` → `:251` → `:332
  --depth-exclusion-intervals`, resolving to `bin_exclude.hg38.gatkcov.bed.gz` (`inputs/values/resources_hg38.json:8`);
  v1.1.1 parity via `TrainRDGenotyping.wdl:9 File bin_exclude` → `TasksGenotypeBatch.wdl:361 -y` →
  `RdTest.R:73 --poorbincov` → `removeExcludedBinCovBins:291-319`. The in-repo `src/RdTest/bin_exclude.bed.gz` is the
  **hg19** asset (4.8 M rows) and is not what either version uses. `depth_blacklist.sorted.bed.gz` is legitimately
  `depth_exclude_list` for **ClusterBatch** (`ClusterBatch.wdl:41 → :282`), never RD genotyping — my "wrong asset,
  replay-only" framing stands.
* **Refuted: "exclusion affects RD *training* only."** `GenotypeSVs` loads it at apply
  (`GenotypeSVs.java:144 → :348-350 → :740`), so train/apply parity is a real requirement and my §15.4 sentence was
  wrong. **New defect**: `GenotypeSVs.java:151 pesrExclusionIntervalsPath` is declared and **never read** — the PESR
  exclusion is silently a no-op at apply time.
* My "fallback ladder makes exclusion weak for 2-kb intervals" was a **misuse of the constants** (`0.1` is the 10 %
  end-shave; `MIN_INTERVALS_AFTER_EXCLUSION` gates bins kept *within one interval*). Measured instead: 7,809/8,736
  (89.4 %) condensed intervals contain zero blacklisted bins, 0/56 curated loci hit the ladder.
* The −32 % state-0 sd I attributed to R3 is real but was measured **at `numBins=10`** (may be an R2 interaction),
  and with the real exclusion state-4 sd gets *worse* vs baseline (+14.5 %), so "production will be better than
  §4.2" is not established either way.

**Correction to my own §17.1 fingerprint claim (self-audit).** I compared AoU production's state-0 cutoff ratio
(1.5365×) against my **stub-exclusion** local arm (1.5372×) and called it a match to 0.05 %. Wrong comparator: the
right local arm is curated + **real** exclusion + bins 10 = `runs/rd_curated56_real` → cutoff ratio **1.2768×**,
sd ratio **1.5797×**, vs production 1.5365× / 2.166×. So production's elevation is *larger* than my local bins-only
prediction. Keep the conclusion (num-bins is the live production RD defect, population is not), drop the numeric
"0.05 % apart" claim — it was comparing across both a cohort change (516 vs 156 samples) and an exclusion change.

**New defects found by the audit, verified by me:**
1. `<gatk-sv-checkout>/wdl/GATKSVPipelineBatch.wdl:239-255` — the `call GenotypeBatch` binds only 14 inputs and leaves
   four **required** ones (`training_intervals`:30, `ploidy_table`:36, `depth_exclusion_intervals`:37,
   `pesr_exclusion_intervals`:38 — plus their `_index` siblings) **unbound**, so the pipeline-level caller cannot run
   GenotypeBatch; it is only runnable standalone in cohort mode (which is how AoU runs it). Auditor says `main` has
   the same omission (`:314-329`). *Unconfirmed by tooling here*: `miniwdl`/`womtool` are **not** installed in
   `.venv` (`ModuleNotFoundError: No module named 'WDL'`), so run `python scripts/test/miniwdl_validation.py`
   in a proper env before reporting it.
2. `GenotypeSVs.java:151` dead PESR-exclusion input (above).
3. `TrainSVGenotyping.java:238 numBins = 10` — reproduces a constant that was dead in v1.1.1 (above).

---

## 18. Deliverables after the audit (replaces parts of §11 and the §15.4 proposal)

| file | status | note |
|---|---|---|
| `patches/proposal-rd-numbins-only.patch` | **the current proposal** — 43 lines, `git apply --check` clean at `ed9b4846`, **not applied, not committed, not pushed** | adds `Int n_RD_genotype_bins = 100000` (workflow + task) and passes `--num-bins`. Nothing else: the curated RD bed is already the deployment default (§17.1), so an `rd_training_intervals` input would fix a problem that does not exist |
| `patches/proposal-rd-training-config.patch` | **SUPERSEDED, wrong premise** — kept only as an audit trail | its `rd_training_intervals` half is void; do not show it to the owner |
| `patches/0001-streaming-rd-train.patch` | unchanged, numerically verified | ship on its own merits; §17.2 corrects why (not because of R1) |
| `reports/audit_r1_population.md`, `audit_r2_numbins.md`, `audit_r3_exclusion.md`, `audit_inference_chain.md` | auditor output | 2 lanes timed out at 30 min and were **resumed** with narrowed prompts (resumes are `48d3d47c`, `e0c90e89`) — that is the pattern to reuse: a timed-out lane keeps its whole context, a fresh lane loses it |
| `runs/train_binsonly/` | COMPLETE, 39.87 min, `Runtime.totalMemory()=1992294400` | negative result: condensed population + bins 100000 still pathological (state-0 sd 0.0629, state-3 mean 1.344); only state-2's mean was repaired (1.0089). Population, not bins, drives that signature |

Process lesson worth keeping: **the auditors earned their keep.** 2 of my 3 findings were downgraded and one was
refuted outright, by reading files that were already on disk — including a claim (`training_intervals` ←
condensed intervals) that I had attributed to `GenotypeBatch.wdl:80-107`, which never said it. My strongest local
result (`train_definitive` reproducing baseline RD to 14 s.f.) survived, but its *interpretation* changed: it now
proves the Java port is faithful at full bin resolution, and the production defect is one unsupplied argument.

---

## 19. AUDIT ROUND 2 (4 lanes, `reports/audit2_*.md`) — verdicts on the four surviving findings

| finding | verdict | what changes |
|---|---|---|
| **`--num-bins` is the live production RD defect** | **CONFIRMED (A1-A3, all numbers recomputed by the auditor)** | "Fully explains" downgraded to "consistent with and plausibly dominant". Local bins-only effect = 1.58× state-0 sd (156 cohort) vs production 2.17× (516 cohort) — residual may be cohort scale or boundary semantics. Separately found: AoU's `DEPTH_SEP` derives from the *new* phase-1 cutoffs asset (0.399) vs v1.1.1's (0.37902) — explains the state-1/2 cutoff shift (0.601 vs 0.621), not state-0. Decisive test: ONE Terra submission — re-run the 516-sample AoU inputs with only `--num-bins 100000` added (raw AoU data not local). |
| **Two RD cutoffs are never fitted / "stratification collapse"** | **PARTIALLY CONFIRMED, framing REFUTED.** Depth-side s1→2/s2→3 = exactly `1∓sep` in all 6 datasets on disk (fit values always violate the bound); PESR-side s1→2 is FITTED in all 6. **v1.1.1 is architecturally identical**: `UpdateCutoff` (`TrainRDGenotyping.wdl:211-258`) selects the same single row (PESR `min_svsize==1000`, depth max) with the same one-sided clamp, and v1.1.1's apply consumes the pre-clamped table with no per-variant separation. | Open item 6 ("stratification collapsed") is **closed as NOT a regression**. Honest statement: in BOTH pipelines the depth-side hom/het boundary = the WDL's separation; only the rf_cutoffs-derived value differs (0.399 vs 0.3848). |
| **SR/PE gap: (i) dropped 1.4826, (ii) pair- vs row-level gate, (iii) pe_count 1-ulp tie** | (i) **CONFIRMED but mis-attributed by me**: the first-pass filter only drops state 0/4 rows, and `sd_het` is computed on state 1/3 → the missing 1.4826 **cannot affect `sd_het` at all**; it affects `median_hom` only (47.805 vs v1.1.1's 54.95 on the same inputs). (ii) **REFUTED**: v1.1.1 gates per (VID, sample), both sides *strictly >* `sr_count/2`, then inner-joins — same granularity as Java (`hasBothSideSupport` :195-210, whose comment even says "This matches the legacy SR training path"). The only value-level difference is v1.1.1's int rounding, which biases **Java** to admit slightly more at the boundary (5.4>5 passes in Java, rounds to 5, fails in v1.1.1) — the opposite of my claim. (iii) **CONFIRMED**: `3.4743558552260145×10 − 8·10·log10(e) == 0.0` exactly in IEEE-754; identical formula on both scales; tie decided by last ulp (v1.1.1/numpy → 8, Java → 7). | The SR/PE `sd_het` gap (raw MAD 9 vs 11) now has **no established cause** among the three candidates. Surviving candidates: per-observation RD state assignment (Java `<` vs R `<=` boundary at `DepthEvidenceGenotyper.java:58`, empty-state mean fallback `0.5*i` vs `(i+1)*0.5`) and int-vs-fractional counts → a different state-1/3 observation *set*, not a gate difference. Decisive test (cheap, local): Phase 2a `--output-training-vcf true` on the frozen cohort + v1.1.1 RD states on the same depth ratios → symmetric difference of the state-1/3 sets; the set-diff size must match the MAD gap. |
| **Wiring: (D1) unbound required inputs, (D2) dead PESR exclusion** | D1 **CONFIRMED, counts corrected**: 17 required inputs, call binds 13, 4 unbound with no defaults (`training_intervals`, `ploidy_table`, 2 exclusions); no `_index` siblings (derived in-workflow); `main` lacks 3 (has `ploidy_table`). **Live-at-replacement, not latent**: v1.1.1's frozen baseline ran GenotypeBatch *from* the pipeline caller (`GATKSVPipelineBatch.wdl:234`), so the new WDLs break that path on day one. Needs `miniwdl check` for the validator error (not installed in `.venv`). D2 **CONFIRMED as an internal self-parity bug, REFUTED as a v1.1.1 regression**: v1.1.1 also never used the PESR exclusion at apply (RD-only there too); `GenotypeSVs` *receives* the arg (WDL passes it) but never reads the field. | D1 joins the ship list alongside num-bins; D2 stays a one-line fix but is no longer a parity argument. |

Process note: all four lanes completed **before** the session reload killed the workflow wrapper; the reload lost only the return envelope, not the reports. If a wrapper dies again, check `subagent-artifacts/outputs/<workflow>/reports/` first.

## §20 Per-fix tests (2026-09-11): each fix tested individually

Setup: two fresh-context subagent lanes (reports `reports/fixtest_fix2_wdl.md`,
`fixtest_fix34_trainer.md`) + one parent run. **No Terra.** Trainers strictly serial (pgrep-guarded).
P0 used the reference `<gatk-checkout>` jar; fixes 3/4/4b used scratch jars built in `<scratch-dir>`
(cloned from `~/IdeaProjects/gatk` read-only, at `18f211cec`, Java 17 `localJar`). `runs/train_definitive`
never touched. New run dirs: `runs/p0_control`, `runs/fix3_14826`, `runs/fix4_pecount`, `runs/fix4b_pequal`.

### Verdicts

| Fix | Verdict | Key evidence |
|---|---|---|
| **1. `--num-bins 100000`** | **WORKS; effect now isolated.** | P0 (bins 10, curated, real excl, whole-cohort VCF) vs definitive (bins 100000, else identical): RD state-0 sd **1.580×** (0.0099572 vs 0.0063019), mean 1.040×, del-cutoff 0.13646 vs 0.10688 (**1.277×**); state-3 cutoff +0.82%; states 1/2/4 within 5.4%. **On SR/PE: `sd_het` ZERO effect** (both 21.949893), SR `median_hom` +1 (68→69), PE zero. ⇒ the 19.511→21.950 `sd_het` move attributed earlier to "the RD fix" was the **population/exclusion** knobs, NOT bins. |
| **2. WDL input bindings** | **WORKS; no-op for AoU.** Correction: the failure surfaces at **input-resolution time, not parse** — miniwdl 1.12.1 `WDL.load` PASSES unpatched (typecheck does not check call-input binding). Proven by set-diff: 17 required / 13 bound / 4 unbound (scale: `training_intervals`, `ploidy_table`, 2 exclusions); `main` 16/13/3. 2-hunk patch (`patches/proposal-pipeline-bindings.patch`) → `WDL.load` OK, unbound ∅, `git apply --check` clean, **composes** with the num-bins patch (zero file overlap, 3way clean). AoU runs cohort-mode standalone (its JSON supplies all four) ⇒ patch only un-breaks the full-pipeline entrypoint. |
| **3. 1.4826 in first-pass filter** | **WORKS, as predicted.** | `first_pass_het_cutoff` 47.805 → **54.9499** (prediction 33+1.645·1.4826·9 = 54.9499, exact); `median_hom` SR 69→**71**, PE 71→**74** (baseline 78/76 ⇒ PE now 0.974×); `sd_het` **unchanged** 21.949893 (the audit's mechanism — filter only drops state 0/4 — confirmed empirically); `sr_count` 10; RD table identical to ≥13 s.f. (14th-digit FP noise across builds); `first_pass_variants` 280→266. |
| **4. pe_count `>=` (as prescribed)** | **NO-OP — refuted as a fix.** | `runs/fix4_pecount`: pe_count still 7. Root cause found by the lane (verified against the built jar): `computeCountCutoff` compares `QualityUtils.errorProbToQual(p)`, and that overload **rounds to a byte** (`Math.round`, `QualityUtils.java:208-210`) → qual(8) = round(34.743558…) = **35.0**, so `35.0 > PEQ` already at i=8 → returns 7 under both `>` and `>=`. The "exactly-tied IEEE double" framing (§19 C4) applies to the *unrounded* formula only. Second latent issue: the WDL/driver awk extracts PEQ at 6 s.f. (`PEQ=34.7436`), vs the file's full-precision `3.4743558552260145`. |
| **4b. pe_count with unrounded quality (PE only)** | **WORKS.** | `qual = -10·log10(p)` (1 line, `DiscordantPairEvidenceGenotyper.java:129`): pe_count = **8** (gate 4, v1.1.1 parity), `sr_count` 10, `median_hom` 71/69 and `sd_het` 21.949893 **unmoved**, RD identical. Caveat: with the 6-s.f. PEQ (34.7436) the unrounded qual(8)=34.743558… < PEQ ⇒ 8; at FULL-precision PEQ the tie at the last ulp returns ⇒ a robust owner fix is **unrounded quality AND `>=`** (or full-precision PEQ). SR twin has the same rounding but its count is not tie-sensitive (10.74). |
| **5. GenotypeSVs reads PESR exclusion** | **DEFERRED to owner.** | Making the dead field live means inventing NEW apply-time semantics (what exclusion means for PE/SR counts at apply) — a design decision, not a test. v1.1.1 was also a no-op at apply (§19 D2), so it is not a parity regression. GenotypeSVs apply inputs are not all staged locally; no local test attempted. |

### Consequences for the ship list

1. **`--num-bins 100000`** — keep; RD-table effect isolated above; does not by itself touch SR/PE `sd_het`.
2. **WDL bindings patch** — ship with the num-bins patch (composes); un-blocks the pipeline entrypoint only.
3. **1.4826** — ship if `median_hom` parity matters (closes most of the PE gap: 74 vs 76; SR 71 vs 78). No `sd_het` effect.
4. **pe_count** — the one-char `>=` is worthless; the working fix is the quality computation (4b). If the owner wants v1.1.1 parity (8), ship 4b + `>=` together; otherwise document the 7-vs-8 as a rounding-policy choice.
5. **GenotypeSVs PESR exclusion** — needs a design decision; park.

### Environment notes (new)

- pip's current miniwdl (1.15.0) has **no** `WDL.load`/`from_file`; pin `miniwdl==1.12.1`, and on Python 3.14
  call `asyncio.set_event_loop(asyncio.new_event_loop())` before `WDL.load` (else `RuntimeError: There is no
  current event loop`). miniwdl typecheck does NOT catch unbound call inputs — use `call.callee.required_inputs`
  set-diff.
- `<scratch-dir>` currently carries the fix-4b edit (uncommitted scratch); `<scratch-venv>` has miniwdl 1.12.1.
- RD md5s: definitive `171d5a89…`, the three /tmp builds `e33ad1fc…` (numerically identical to ≥13 s.f.;
  md5s will never match across builds — last-digit FP non-determinism).
- `run_train_definitive.sh` is mode 644 — run it as `bash run_train_definitive.sh` (honors OUT/JAR/NUM_BINS/EXTRA_ARGS/XMX).

## §21 Combined fixes + first end-to-end genotype comparison vs v1.1.1 (2026-09-11)

All local. No Terra. Combined jar: `/tmp/gatk_combined` @ `18f211cec` + exactly the 3 proven fixes
(`SplitReadEvidenceGenotyper.java:261` + `DiscordantPairEvidenceGenotyper.java:173` 1.4826; PE
`computeCountCutoff` unrounded `qual = -10·log10(p)` + `>=`). New run dirs:
`runs/combined_train`, `runs/combined_genotype/`, `runs/combined_genotype_depth/`,
`runs/combined_compare_{pesr,depth}/`. Lane reports: `reports/combined_apply.md`,
`comparison_prep.md`.

### Train (`runs/combined_train`, definitive config)
All expectations met exactly: RD state-0 0.04545281526474247 / 0.006301943860032666 (baseline to 11 s.f.);
SR `10 / 71 / 21.949893` (71 = fix-3 effect), PE `8 / 74 / 21.949893` (8 = fix-4b effect, 74 = fix-3);
`first_pass_het_cutoff` 54.949893, `first_pass_variants` 266.

### Apply
- **PESR**: GenotypeSVs on the 96,915-record filtered PE/SR VCF, whole genome, 8.69 min, peak ~2.4 GiB, 0 errors.
- **Depth**: the staged input is PESR-only, so the in-pipeline depth split is empty by construction; the depth
  channel was compared separately by feeding v1.1.1's own depth candidate VCF
  (`all_samples.depth.outliers_removed.vcf.gz`, 5,386 records, downloaded read-only from the manifest) through
  the SAME combined jar: 27 s, 5,386 records. (Caveat: production's new-pipeline depth candidate population may
  differ; this is an apples-to-apples genotyper comparison on v1.1.1's sites.)
- New FORMAT: `GT:ECN:EV:GQ:PE_GQ:PE_GT:SR_GQ:SR_GT` (17 declared). GQ rescaled 0–99 (baseline 0–999) ⇒ GQ
  modules not directly comparable; GT modules fine.

### PESR comparison (gatk-sv-profile, 80,878 matched site pairs of 80,888 baseline / 96,915 new, 156 samples)
Site-weighted, `*_baseline_v111` = baseline GTs vs new-truth, `*_new_java` = new GTs vs baseline-truth:

| metric | baseline | new | read |
|---|---|---|---|
| genotype_concordance | 0.9842 | 0.9860 | ~98.5% symmetric agreement |
| non_ref_genotype_concordance | 0.9171 | 0.9346 | |
| het_sensitivity | 0.9990 | **0.9762** | new drops ~2.4% of baseline hets |
| het_ppv | 0.9550 | 0.9992 | new's hets are near-confirmed |
| homvar_sensitivity | 0.8028 | **0.9540** | new calls far more hom-alt |
| homvar_ppv | 0.9365 | 0.7998 | …at lower precision |
| var_sensitivity / var_ppv | 0.9724 / 0.9527 | 0.9741 / 0.9703 | site-level calls stable |

Per svtype genotype_concordance (both directions ≈): DEL 0.9921/0.9915, INS 0.9517/0.9551, DUP 0.9924/0.9923,
BND 0.9919/0.9920, INV 0.9938/0.9938, INS:MEI 0.9836/0.9841. INS is the weakest (short-INS het calls).

Exact-match transitions (80,878 pairs, 4-d.p. precision): exact 0.9842; het→homref 0.0066, het→homalt 0.0070,
homref→het 0.0001, homalt→het 0.0018, homalt→homref 0.0004 ⇒ net het→hom drift (~1.4% of het observations),
tiny reverse flow. Sample-level GT distribution (proportions; new has 20% more sites): baseline
homref 84.17% / het 12.39% / homalt 3.44% / no-call 0% vs new 84.72% / **10.96%** / **4.32%** / 0%.
Direction = exactly the predicted consequence of the stricter het filter + new cutoff strategy (open items 3/5),
not a random regression.

### Depth comparison (5,386/5,386 sites matched; 840,216 obs = 5,386×156)
- concordance metrics ≈ 1.0 in all directions (genotype_concordance 0.9975/1.0000; exact 1.0000 at 4 d.p.).
- Direct per-cell GT diff: **27 sites / 2,205 cells (0.26% of all obs)** differ, dominated by
  **baseline-called → new-no-call** (1,330 homalt + 775 homref + 79 het) vs 13 baseline-no-call → new-called
  and 7 het→homalt. Both-called disagreement ≈ 19 cells (~0.002%). The new depth genotyper is essentially
  a faithful port at GT level, slightly more conservative at the no-call boundary.

### Environment notes (new)
- Bare `java` on this box is **11** — the depth apply failed with `UnsupportedClassVersionError … 61.0 … up to
  55.0` until run with `/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home/bin/java` explicitly
  (the trainer driver sets JAVA itself, which is why only direct java invocations were affected).
- gatk-sv-profile venv: `.venv-profile` (non-editable install of <profiler-checkout> + sed patch
  `--sequence-dict`→`--sequence-dictionary` in site-packages/preprocess.py); gatk wrapper `/tmp/gatk_combined/gatk`;
  argparse: `--java-options=-Xmx8g` (bare `-Xmx8g` is parsed as a flag). Both /tmp paths are ephemeral —
  re-create per `reports/comparison_prep.md` if wiped.
- `gatk-sv-profile genotype_exact_match` excludes no-call cells (depth exact 1.0000 despite the 2,205-cell
  no-call diff) — read it as both-called agreement.
- New-side record counts: 96,915 (input) = 96,915 (output); trainer `variants_seen` 93,269 (it skips 3,646
  BND/multi-locus rows).

## §22 Session handoff — 2026-09-11 (EDT; system clock matches run logs)

No live jobs at handoff (`pgrep -fl "TrainSVGenotyping|GenotypeSVs|gatk-sv-profile|SVConcordance"` → none).
No Terra jobs were submitted this session (owner constraint). No commits, no pushes.

### Repo state (re-queried at handoff, `repo_state.sh` + `git ls-remote`)
| repo | ref | state |
|---|---|---|
| `gatk/` (workspace clone) | `18f211cec` | clean; remote head = local head = `18f211cec…` (re-queried) |
| `<gatk-sv-checkout>` | `ed9b4846` | **clean**; remote head = local head = `ed9b4846…` (re-queried) |
| `<gatk-checkout>` | `18f211cec` | DIRTY = the known uncommitted **streaming-RD patch** (exactly `DepthEvidenceGenotyper.java` + `TrainSVGenotyping.java`, matches `patches/0001-streaming-rd-train.patch` files) + untracked `build_logs/`. Pre-existing; nothing added this session |
| `<profiler-checkout>` | `6bc1dc7` | DIRTY = untracked `build/` (pip wheel build debris from the non-editable install) — new this session, harmless, `rm -rf <profiler-checkout>/build` to clean |
| `gatk-sv-v1.1/` | `5af2592e` | untracked nested `gatk/` clone (pre-existing, documented) |
| `<v111-checkout>`, `<profiler-checkout>` refs, `gatk-sv-main` | as before | untouched |

### Mutations ledger (this session)
- **GCS: read-only** (`gsutil cp -n`): `all_samples.genotyped_pesr.vcf.gz` (35,019,811 B, md5
  `c4dbb4215391ab0d17000f43a38ea49e`) + `.tbi` (md5 `fa9e3e6cdfed9d3aa8d270db417ffcc7`), and
  `all_samples.depth.outliers_removed.vcf.gz` (238.1 KiB) — all now in `staging/`. **No GCS writes,
  no Terra submissions.**
- **Branches: zero commits, zero pushes** (remote heads re-read above). None of the proposal patches applied
  to any worktree (<gatk-sv-checkout> clean; verified by repo state).
- **Local artifacts** (all listed under Deliverables) + `/tmp` scratch (ephemeral): `gatk_fixtest` (fix3/4b
  edits), `gatk_combined` (combined jar + `gatk` wrapper), `venv-miniwdl` (miniwdl 1.12.1), `venv-profile`
  (gatk-sv-profile + sed patch), `wdltest` (patched WDL copy).
- `<profiler-checkout>/build/` — see repo state (reversible: delete).

### Corrections shouted (earlier doc claims proved wrong this session)
1. ~~"WOM/validator will error on the unbound call inputs"~~ — miniwdl 1.12.1 `WDL.load` **passes** unpatched;
   the failure surfaces at **input-resolution time**, not parse/typecheck (verified by set-diff instead).
2. ~~"the num-bins fix can move `sd_het`/`median_hom`"~~ — isolated by P0 vs definitive: **zero** `sd_het`
   effect, +1 SR `median_hom` only. The 19.511→21.950 `sd_het` move was the population/exclusion knobs.
3. ~~"pe_count's 1-ulp IEEE tie is the live blocker; `>=` fixes it"~~ — the live blocker is
   `QualityUtils.errorProbToQual` **byte rounding** (qual(8)=35.0 > PEQ at i=8); `>=` tested as a **no-op**;
   the working fix is unrounded quality (4b), robust form = unrounded + `>=` (in the combined jar).
4. Record count: input VCF = **96,915** records; trainer `variants_seen` 93,269 (skips 3,646 BND/multi-locus rows).

### How to resume (paste-able)

> **SUPERSEDED for item 1 (see §23):** the Phase 2a output VCF carries only the 93,269 *training* (PESR)
> sites — none of the 5,386 baseline depth sites — so item 1 as written cannot produce the intended diff.
> The decisive test was instead run on the §21 GenotypeSVs depth output (which does carry `RD_CN`);
> verdict in §23.2. Items 2-4 below remain valid.
```bash
cd <workspace>/testkit
 # 1) The sd_het MAD-9-vs-11 residual — Phase 2a per-observation RD states (decisive test, ~6 min):
OUT=$PWD/runs/phase2a NUM_BINS=100000 \
  JAR=/tmp/gatk_combined/build/libs/gatk-package-4.6.2.0-126-g18f211c-SNAPSHOT-local.jar \
  EXTRA_ARGS="--output-training-vcf true" bash run_train_definitive.sh
 #    then diff FORMAT/RD_CN of $OUT/all_samples.training_vcf.gz per (site,sample) against
 #    staging/all_samples.genotyped_depth.vcf.gz (v1.1.1's own call); symmetric difference of the
 #    state-1/3 observation sets must be large enough to explain MAD 9 vs 11 (CHECKPOINT §6 2x2).
 # 2) Re-create /tmp artifacts if wiped (venv-profile + sed + gatk wrapper):
 #    see reports/comparison_prep.md sections "Venv + install" and "Tool" (one sed line included).
 # 3) Re-run either comparison: see reports/comparison_prep.md "Ready-to-paste compare commands"
 #    (use --java-options=-Xmx8g; VCFs already exist under runs/combined_*).
 # 4) Java for direct invocations: /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home/bin/java
 #    (bare `java` is 11).
```

### What "good" looks like (expected values for the next check)
- Phase 2a: output VCF carries `FORMAT/RD_CN`; state-1/3 set-diff **between** Java and v1.1.1 states is the
  signal — if the sets are near-identical, the residual lives in the het/hom *filter* population, not the RD
  states (and the surviving candidates from §19 row 3 get dropped).
- Any re-run of the combined train must reproduce: RD state-0 `0.045452815264742/0.0063019438600326`, SR
  `10 71 21.949893`, PE `8 74 21.949893`, `first_pass_het_cutoff 54.949893` (last-digit FP noise across builds
  is normal; md5s will not match across builds).
- miniwdl set-diff expectations: scale branch 17 required / 13 bound / 4 unbound; `main` 16/13/3.

### Gotchas actually hit this session (with the error text)
- `bash: run_train_definitive.sh: Permission denied` (mode 644) → always `bash run_train_definitive.sh`.
- pip miniwdl 1.15: `AttributeError: type object 'Workflow' has no attribute 'from_file'` → pin 1.12.1; on
  Python 3.14 also `RuntimeError: There is no current event loop in thread 'MainThread'` → call
  `asyncio.set_event_loop(asyncio.new_event_loop())` before `WDL.load`.
- gatk-sv-profile: `argument --java-options: expected one argument` → `--java-options=-Xmx8g`; SVConcordance
  flag mismatch `--sequence-dict` vs `--sequence-dictionary` → sed the venv's `preprocess.py`.
- bare `java`: `UnsupportedClassVersionError … class file version 61.0 … only recognizes … 55.0` → use the
  explicit Java 17 binary (drivers set JAVA themselves; only direct `java -jar` was affected).
- `bcftools query -f '%GT…'`: `FORMAT fields must be enclosed in square brackets` → `[ %GT\n]`.
- gatk-sv-profile `genotype_exact_match` **excludes no-call cells** (depth exact 1.0000 despite the 0.26%
  no-call diff) — read it as both-called agreement.
- profile tables are `.tsv.gz` and wide (13-col metric format; the per-pair table is `genotype_match_rates`).

### Deliverables
| file | what |
|---|---|
| `CHECKPOINT.md` | +§20 (per-fix verdicts), +§21 (combined run + GT comparison), +§22 (this) — 978 lines |
| `CLAUDE.md` | status bullets (wiring tested, combined test), open items 3/4/6/8 updated/closed |
| `patches/proposal-pipeline-bindings.patch` | NEW — 2 hunks, typechecks clean, composes with num-bins patch, no-op for AoU cohort mode; unapplied |
| `patches/proposal-rd-numbins-only.patch` | unchanged, still the current `GenotypeBatch.wdl` proposal |
| `runs/{p0_control,fix3_14826,fix4_pecount,fix4b_pequal,combined_train,combined_genotype,combined_genotype_depth,combined_compare_pesr,combined_compare_depth}` | new run dirs (all inputs staged; RUN metadata in each dir's train.log/logs) |
| `reports/{fixtest_fix2_wdl,fixtest_fix34_trainer,combined_apply,comparison_prep}.md` | lane reports |
| `staging/{all_samples.genotyped_pesr.vcf.gz(+tbi),all_samples.depth.outliers_removed.vcf.gz(+tbi)}` | new staged baseline files (md5s in §21/§22) |
| `/tmp/gatk_combined` (jar) | combined-fixes jar @ `18f211cec` + 3 edits; provenance in `reports/combined_apply.md` |

### Open items (carried forward — unfinished ≠ deleted)
- [ ] **AoU 516-sample rerun with `--num-bins 100000`** (+ optional bindings patch) — owner's call; needs Terra
      (this session submitted none). Settles whether num-bins alone closes the production RD gap (local: 1.58×
      vs production 2.17× → "plausibly dominant", not "fully explains").
- [x] **`sd_het` MAD 9 vs 11 residual** — Phase 2a per-observation RD-state diff — **DONE 2026-09-11 §23: refuted** (states agree 99.96%; state-1/3 set symmetric difference 59/82,414). Residual re-scoped to three small effects (§23.3); emulator-scale population reconstruction if the owner wants the last word.
- [ ] **Owner decision on the measured het→hom drift** (PESR het 12.39%→10.96%, homalt 3.44%→4.32%; §21):
      acceptable downstream? Item 5's "unvalidated downstream" is now measured, not hypothetical.
- [ ] **Fix 5 (GenotypeSVs PESR-exclusion read)** — deferred; needs an apply-semantics design decision.
- [ ] **`ValidateSRCutoffs`** min-observation threshold (CLAUDE.md item 7) — unchanged.
- [ ] **SR twin byte-rounding** in `SplitReadEvidenceGenotyper` `computeCountCutoff` — same latent issue; SR
      count not tie-sensitive (10.74). Document-or-fix if PE/SR parity matters.
- [ ] **WDL PEQ precision** — production WDL awk extracts PEQ at 6 s.f. (`34.7436`); at full precision the
      pe_count tie returns even with unrounded quality. Fold into the owner's fix-4 call.
- [ ] Cleanup (optional): `<profiler-checkout>/build/` debris; `/tmp` scratch.
- [ ] Not measured (deliberately): GQ-scale comparison (baseline 0–999 vs new rescaled 0–99 — needs a
      rescale spec); production's new-pipeline depth-candidate population (depth comparison used v1.1.1's sites).

## §23 Pickup session 2026-09-11 ~15:20-16:00 EDT — the decisive per-observation RD-state test is DONE: states agree 99.96%; the MAD 9-vs-11 residual is NOT the RD states

Pickup re-verified the §22 ledger (all CONFIRMED: branch heads/remote, worktree dirt, `/tmp` artifacts incl. the combined jar, combined_train values SR `10 71 21.949893` / PE `8 74 21.949893` / cutoff 54.949893 / variants 266, staged md5s). One divergence: `gatk-sv-main` origin/main moved +10 commits since handoff (docker/CRAN/SVLEN/CleanVcf: `783243f7`..`c73a08b8`) — none touch GenotypeBatch; relevant only before rebase/branch work.

### 23.1 The resume block's premise was wrong (correction, named per §7a)

§22 resume item 1 said Phase 2a's `--output-training-vcf true` output would carry the depth sites' `RD_CN` for diffing against `all_samples.genotyped_depth.vcf.gz`. **It does not**: the training VCF is the *PESR merged* VCF (93,269 records), so the phase-2a VCF (`runs/phase2a/train.genotyped.vcf.gz`, 93,269 sites) contains **zero** of the 5,386 baseline depth sites (matched site keys: 0). Two consequences, both measured:

* **The decisive test needed no new run.** `GenotypeSVs`' depth-channel output (`runs/combined_genotype_depth/all_samples.depth_genotype_batch.vcf.gz`, §21) already writes `FORMAT/RD_CN` = raw `copyStates[i]` (0–4; `GenotypeSVs.java:665`, header "Depth genotype copy state") on exactly the 5,386 v1.1.1 depth sites.
* **New latent defect (robustness, not live in production):** `TrainSVGenotyping -V <depth-only VCF> --output-training-vcf true` crashes in 0.40 min with `java.lang.IllegalStateException: No discordant pair counts after first pass` at `DiscordantPairEvidenceGenotyper.finalizeFirstPass` ← `TrainSVGenotyping.traverse(:485)` — any `-V` with zero PE-observable records kills the whole training run (evidence: `runs/phase2a_depth/`, RD tables were written before the crash). `run_train_definitive.sh` gained a `TRAIN_VCF` env override this session (testkit only).

### 23.2 Result (new script `diff_rd_states.py`; joined on CHROM/POS/END/SVLEN/SVTYPE, 5,372/5,386 sites)

| metric | value |
|---|---|
| comparable obs | 838,032 |
| exact RD_CN agreement | **99.96%** (837,711) |
| total mismatches | 321 (0.038%); all ≤1-state flips (59) + 166 within the >4 copy tail |
| **state-1/3 (het) observation sets** | baseline 82,414, java 82,447, shared 82,401 → **symmetric difference = 59 (0.07%)** |

**Verdict on open item 2 (`sd_het` MAD 9 vs 11):** the §19/§17.3 candidate cause (per-observation RD state assignment — `<` vs `<=` boundary at `DepthEvidenceGenotyper.java:58`, empty-state fallback) is **refuted as the explanation**: a 59-observation set difference cannot move a median-based MAD by 2 units. (Label: measured on the 5,386 depth sites; generalised to the 93k PESR sites by the shared code path — the depth sites are the informative minority where states ≠2.)

### 23.3 Structural verification chain for the SR het population (all verified this session)

1. **Site file**: baseline `GenotypeBatch.batch_pesr_vcf` (manifest `10-GenotypeBatch/inputs`) == staged `all_samples.filtered_pesr_merged.vcf.gz` — same gs:// object (FilterBatchSamples `23ffd0c6` / call-MergePesrVcfs).
2. **Coverage medians**: baseline `GenotypeBatch.medianfile` == staged `all_samples_medianCov.transposed.bed` — same gs:// object (MedianCov `b7a6aa87`).
3. **Count recipe is a faithful port**: v1.1.1 = per-side `svtk count-sr` normalised `round(count·60/median_cov)` (pandas `.round()`, half-to-even; `pesr_test.py:73-83`) then `sum_SR.sh` **sums the two rounded per-side values**; Java = `Math.round(60·count/cov)` per side (`EvidenceStatUtils.java:71-73`, half-up) then `FirstPassResult` stores `startCount + endCount` (`SplitReadEvidenceGenotyper.java:1220-1235`). §19's "int vs fractional counts" candidate was already wrong about Java (Java rounds); the only value-level difference is the **.5 tie-break mode** (half-up vs banker's), i.e. at most ±1 on a handful of values.
4. **Gate identity**: v1.1.1 `two.sided.pass.txt` = both per-side rounded counts `> sr_count/2` (float division); Java `hasBothSideSupport` = same strict `>` per side; at `sr_count = 10` both thresholds are exactly 5 (int/float division coincide).
5. **Site-set difference (new)**: Java `variants_seen` 93,269 vs 96,915 records — 3,646 unparseable (multi-locus/BND) records. 9,802 BND-ALT records exist, **6,570 with `EVIDENCE=…SR…`**; Java does process the rest of the BND set. Estimated impact on the het population: ~1% scale (≈ tens of observations vs 1,319) — **too small to explain 2 MAD units**, but not exactly zero.

**Remaining candidate causes for SR raw MAD 9 vs 11 (PE 9 vs 10), in order of plausibility after this session:** (a) the 3,646-record site-set gap incl. BND sites v1.1.1's svtk handled (small, measured upper bound above); (b) the rounding tie-break; (c) boundary flips on the 93k PESR sites (cutoff 0.1078 vs 0.1069 — unmeasured; the 59/82k flip rate is the depth-site measurement only). All three are now *small* effects; the residual is a genuine but minor v1.1.1-incompatibility, and the combined-fixes GT comparison (§21: 98.5% PESR concordance, ≈99.998% depth) is the better evidence base for the owner's ship decision. Decisive further measurement if wanted: reconstruct v1.1.1's exact het population (gate + counts + v1.1.1 states) from the staged raw matrices and diff the observation lists against Java's 1,319 — an emulator-scale job, not a quick probe.

### 23.4 Deliverables this session (additions to §11/§22)

| file | what |
|---|---|
| `diff_rd_states.py` | per-(site,sample) RD_CN diff, coord-key join, confusion matrix + state-1/3 set symmetric difference; ran on the §21 GenotypeSVs depth output (result §23.2) |
| `runs/phase2a/` | Phase 2a full re-genotype run (8.98 min, 3.4 GB peak): `train.genotyped.vcf.gz` with `RD_CN` for all 93,269 training sites; params identical to `combined_train` (SR 10/71/21.949893, PE 8/74, cutoff 54.949893, variants 266) |
| `runs/phase2a_depth/` | CRASHED arm (depth-only `-V`): evidence for the `finalizeFirstPass` robustness gap (§23.1); RD tables partial, no SR/PE outputs |
| `run_train_definitive.sh` | + `TRAIN_VCF` env override (default unchanged) |
| `runs_phase2a.out`, `runs_phase2a_depth.out` | nohup logs |

Open items unchanged except: item "sd_het MAD 9 vs 11 residual — Phase 2a per-observation RD-state diff" is **DONE** (verdict §23.2); the AoU rerun, owner decisions (het→hom drift, fix-5 PESR exclusion, PEQ precision), `ValidateSRCutoffs` threshold, SR-twin rounding, and doc-location question remain. New small item: the §23.1 `finalizeFirstPass` crash (guard the empty first pass or document the requirement).

## §24 Code committed & pushed + docker rebuild map (2026-09-11, post-§23)

Owner decision: prior work "not worth it" — proceed to a controlled Terra run of the new code
(GenerateBatchMetrics, FilterBatch, MergeBatchSites, GenotypeBatch). Prerequisite: commit + push the
audited fixes to their branches. All done this session; **no images built** (owner builds).

### 24.1 Pushed commits

| repo / branch | tip (post-push) | new commits |
|---|---|---|
| `broadinstitute/gatk` `<gatk-branch>` | `d02667245` | `fcc52a7da` streaming Welford RD training (= `0001-streaming-rd-train.patch`, expected RD digits move at 15th s.f. only); `cac2fa3b4` 1.4826 ×2 (`SplitReadEvidenceGenotyper.java:261`, `DiscordantPairEvidenceGenotyper.java:173`) + PE `computeCountCutoff` unrounded `-10·log10(p)` + `>=` (`:126-130`), with regenerated expected test outputs; `d026672` removes the unused `QualityUtils` import `cac2fa3b4` left behind (audit fix; tests re-run green) |
| `broadinstitute/gatk-sv` `<branch-under-test>` | `ce8318e3` | `281d02ac` `GenotypeBatch.wdl` `n_RD_genotype_bins = 100000` + `--num-bins` (= `proposal-rd-numbins-only.patch`); `8ad9621f` `GATKSVPipelineBatch.wdl` 4-input bindings (= `proposal-pipeline-bindings.patch`); `ce8318e3` test-input templates supply the 4 now-required inputs + drop dead v1.1-era dotted keys (audit follow-up; see §24.5) |

Both remotes verified post-push via `git ls-remote`, twice (initial push + audit push): gatk `18f211cec→d02667245`; gatk-sv `ed9b4846→ce8318e3`.
PESR-exclusion-at-apply (fix 5) intentionally NOT included (deferred).

### 24.2 Test validation for the Java commit (all green, 137 tests / 0 failures)

Ran with Java 17: `TrainSVGenotypingTest` (25 testcases = 3 own incl. the update-toggle guard + 22 inherited from `GatkToolIntegrationTest`),
`SplitReadEvidenceGenotyperTest` (35), `DiscordantPairEvidenceGenotyperTest` (34), `DepthEvidenceGenotyperTest` (39),
`DepthEvidenceUnitTest` (2), `DiscordantPairEvidenceUnitTest` (2). Audit verified the result XMLs were fresh
(single run, identical mtimes) and the guard test passed → the toggle was `false` during the assertions.
Expected outputs regenerated via the
`UPDATE_EXACT_MATCH_EXPECTED_OUTPUTS` toggle (flipped, ran, flipped back — guard test enforces):
- `expected.rd_geno_params.tsv`: Welford digits only (15th s.f.).
- `expected.pe_geno_params.tsv`: `7 89.5 21.949893` → `6 114 21.949893` (test uses default PEQ 30.0; exact
  `qual(7)=30.4006 ≥ 30.0` → 6, which is v1.1.1-consistent at full precision; old byte-rounded `qual(8)=35>30`
  gave 7).
- `expected.vcf.gz`: **CORRECTED by audit** — the file has 101 records (3,314 header lines; an earlier
  "52/3417 records" said lines-as-records). 52/101 records changed: `PE_GQ` in all 52 (e.g. 16→11, 292→17,
  447→999) and `PE_GT` in 18 of them (e.g. 0→1, 3→2); the aggregate `GT` and all RD/SR fields unchanged.
  The original "every diff is a single PE_GQ field" came from a vacuous field attribution — the parser used
  column 7 (INFO) as FORMAT, so per-field labels were never actually computed (see §24.5).
- `expected.sr_geno_params.tsv` **unchanged**. The committed diagnostics file gives the real fixture scale:
  `first_pass_variants 5`, `first_pass_het_observations 8` (the original "7 het obs" conflated `sr_count=7` —
  corrected). `1.4826` moves `first_pass_het_cutoff` 41.1125→43.0972 (=37+6.0972 ✓) but every printed table
  value (median_hom 75, sd_het 6.0971925, grid tallies, winner cell) is identical.
- The tracked raw `train_sv_test.sr_geno_params.tsv` was **value-stale** — **CORRECTED**: it always had all
  15 columns (the original "10 columns, pre-cutoffs" was wrong); what was stale were the grid tallies
  (`13/81/928` vs `12/88/1228`) and winner cell (`common_single` 1.0 vs 0.7). Root cause pinned: raw was last
  regenerated by `3212b4554`, then `04eed9ce5` regenerated `expected.sr_geno_params.tsv` (1/1) *without*
  touching the raw sibling — drift since then. My regeneration re-synced it.
- New untracked `train_sv_test.sr_cutoff_diagnostics.txt` committed (standard tool output for the same run;
  `phase2_mode 2a_full_genotype`, cutoffs 30.0/30.0).

WDL side: miniwdl 1.12.1 `WDL.load` passes both patched files; post-patch set-diff = required 17 / bound 17 /
missing NONE (was 17/13/4). Audit additionally ran **womtool-84** (the exact CI binary) `validate` on both
patched WDLs: `Success!` ×2. Post-import-cleanup re-run of the three key suites: 25+35+34, 0 failures.

### 24.3 Docker rebuild map for the controlled Terra run

Image tags are **workspace attributes** (`${workspace.*}_docker`; see `inputs/templates/terra_workspaces/
cohort_mode/workflow_configurations/*.json.tmpl`), with in-repo example values in `inputs/values/dockers.json`.
WDL files run under Cromwell — **not** baked into any image.

| image (deployed tag) | used by (of the 4) | rebuild? | why |
|---|---|---|---|
| `us.gcr.io/<project>/<ns>/gatk:<gatk-branch>-aou-d096612` | GenotypeBatch (TrainSVGenotyping/ValidateSRCutoffs/GenotypeSVs/ConcatVcfs), MergeBatchSites, GenerateBatchMetrics (`gatk_docker` tasks) | **YES** — new tag `<gatk-branch>-aou-cac2fa3b4` from the branch tip | the deployed tag predates even `fee36f367`/`18f211cec`; the GATK jar is built inside the image (`Dockerfile`: `ADD . /gatk` + gradle bundle stage) |
| `us.gcr.io/<project>/<you>/sv-pipeline:<branch>--98466e98` (also `sv_pipeline_qc_docker`) | FilterBatch (Sites/Samples), MergeBatchSites, GenerateBatchMetrics (`sv_pipeline_docker` tasks) | **YES** — new tag `<branch>--8ad9621f` | of the 6 baked-in dirs, exactly one baked file moved since the pinned image: `src/svtk/svtk/adjudicate/labelers.py` `TrainingLabeler` RD_MEDIAN_SEPARATION Fail threshold `0.15→0.10` (FilterBatch training-classification path). The WDL changes themselves need no rebuild |
| `us.gcr.io/<project>/gatk-sv/sv-base-mini:2024-10-25-v0.29-beta-5ea22a52` | all four | NO | shared release image, untouched |
| `marketplace.gcr.io/google/ubuntu1804` | FilterBatchSamples | NO | public image |

sv-pipeline build inputs unchanged: `SVBASE_IMAGE` = shared `sv-base:2024-10-25`, `VIRTUAL_ENV_IMAGE` =
`sv-pipeline-virtual-env:2025-05-01-v1.0.3-85054e07` (both in `dockers.json`). After pushing the images the
AoU workspace attributes / method configs must point at the two new tags. `gq_recalibrator_docker`
(`gatk:<gatk-branch>-1eac9db`) is a different tool line — untouched by these commits.

### 24.4 Notes / gotchas this session

- `<gatk-checkout>` `gatk` launcher file: do NOT confuse with the unrelated modified `gatk` file in the
  `/tmp/gatk_combined` scratch clone (that one is build noise, never committed).
- `<gatk-checkout>/build_logs/` remains untracked (pre-existing session junk).
- macOS `sed` choked on the `s/…;…/` toggle flip — used the edit tool instead; and `head -c -1` is unsupported
  (a bogus "IDENTICAL" cmp on empty streams nearly slipped through — always verify the byte comparison actually ran).
- `gatk-sv-main` clone is 10 commits behind `origin/main` (docker/CRAN/SVLEN/CleanVcf, `783243f7`..`c73a08b8`)
  — irrelevant to GenotypeBatch, but rebase-awareness before any main work.

### 24.4 addendum (audit) — tooling self-faults, recorded so they don't recur

- **VCF column indexing:** FORMAT is column index 8, not 7 (7 is INFO). Both the session's diff script and the
  first audit re-check got this wrong; the failure mode is *silent* — a `FORMAT: []` empty parse and
  vacuous per-field counts that still "run". Any per-field VCF claim must show the per-field dictionary as
  non-empty before being believed.
- **`head -c -1` on macOS** (already noted above) + `cmp` of two empty streams returns success — verify the
  streams are non-empty.
- Mid-audit overclaim, retracted below (§24.5, item 4).

## §25 Session audit (2026-09-14, auditing §24 which ran 2026-09-11)

> **Date correction (named):** this heading previously read "same day, post-§24 pushes". Commit dates prove
> §24 = 2026-09-11 (`fcc52a7da` 16:47, `cac2fa3b4` 16:48, `281d02ac`/`8ad9621f` 16:25) and the audit's own
> commits = **2026-09-14 12:24** (`d02667245`, `ce8318e3`). The session spanned days; §24's "(2026-09-11)"
> label is right, §25's was not.

Owner asked for an audit of the §24 work. Method: re-derive every load-bearing claim from artifacts,
adversarially, rather than restating the session narrative.

**Verified sound (re-checked from artifacts, not memory):**
1. Remote tips: gatk `d02667245`, gatk-sv `ce8318e3`; worktrees clean vs HEAD.
2. Pushed blobs contain exactly the intended edits: `1.4826 * 1.645` at `SplitReadEvidenceGenotyper.java:261`
   and `DiscordantPairEvidenceGenotyper.java:173`; `-10.0 * Math.log10(p)` + `>=` at `:129-130`;
   `UPDATE_EXACT_MATCH_EXPECTED_OUTPUTS = false` in the pushed test file; the pushed
   `expected.vcf.gz` gunzips to md5 `0527c84e…` == the artifact the passing tests asserted against.
3. Commits are surgical: `fcc52a7da` 5 files; `cac2fa3b4` 8 files (binary vcf/tbi numstat `-`, diagnostics
   file = the 466 lines of "473 insertions"); gatk-sv commits wdl-only (13+/17+).
4. `d096612` = "Optimize SR cutoff tabulation" and is an **ancestor of `fee36f367`** — the §24.3 "deployed gatk
   image predates the diagnostics/stride commits" claim moves from inferred to proven.
5. Test-run provenance: all six result XMLs share one fresh mtime; 25-test TrainSVGenotypingTest = 3 own +
   22 inherited base-class cases; guard test present and green.

**Defects found in §24 (corrected inline, named):** the "52/3417, single PE_GQ field" VCF claim
(actually 52/101 records, PE_GQ 52 + PE_GT 18, GT untouched); the "7 het obs" explanation
(`first_pass_het_observations = 8`, `first_pass_variants = 5`); the "raw sr table was 10 columns,
pre-cutoffs" claim (15 columns all along; value-stale since `04eed9ce5` updated expected without the raw
sibling). Root causes: INFO-vs-FORMAT column bug producing vacuous field attribution; conflating
`sr_count` with het-N; eyeballing 8 records and generalizing.

**New findings (not in §24):**
1. **`QualityUtils` import was left unused** by `cac2fa3b4` — fixed + pushed (`d026672`, three suites re-run
   green). (No local checkstyle config found; GATK prb may or may not gate on it — the cleanup is justified
   regardless.)
2. **The GATKSVPipelineBatch *test input* templates were dead long before this session**:
   `build_inputs.py` **silently skips** (INFO-level) any template with undefined value references. Both
   templates lack `qc_definitions`/`outlier_cutoff_table` values (ref_panel_1kg) and a dozen more (hgdp), so
   these JSONs are never generated locally or in CI ⇒ the pipeline WDL pair has **no womtool coverage at all**.
3. Mid-audit I claimed "the bindings patch will fail CI at PR-to-main (missing required inputs in test
   templates)" — **RETRACTED**: CI can only validate inputs that `build_default_inputs.sh` actually renders,
   and these never render. What the audit did land instead: commit `ce8318e3` supplies the 4 now-required
   inputs at the pipeline level and drops five **dead dotted `GenotypeBatch.*` keys**
   (`n_per_split`/`pesr_exclude_list`/`seed_cutoffs`/`reference_build`/`bin_exclude` — v1.1-era names, no
   such inputs post-rewrite). The templates remain skipped-but-correct; filling the missing batch-level
   values (so the pair renders + validates) is an optional owner follow-up.
   Related mechanism note: `main` supplies GenotypeBatch's exclusion-interval inputs via **dotted
   sub-workflow JSON keys** (`GATKSVPipelineBatch.GenotypeBatch.training_intervals`, template lines ~109-111);
   the `8ad9621f` patch makes them explicit parent-level bindings instead — strictly more portable, and the
   cohort-mode config path is unaffected either way.
4. **womtool-84** (exact CI artifact) `validate` on both patched WDLs: `Success!` ×2 — parity with CI's
   syntax stage, previously miniwdl-only.
5. Terra API probe from here: `https://api.firecloud.org/api/version` → 401 unauthenticated ⇒ the AoU
   workspace's actual `*_docker` attributes remain ***inferred*** (dockers.json is the in-repo default).
   Owner must confirm/point `workspace.gatk_docker` and `workspace.sv_pipeline_docker` (+`sv_pipeline_qc_docker`,
   same image) at the rebuilt tags before the controlled run.
6. Branch-local stale refs (informational): `~/IdeaProjects/*` clones' local refs don't move on push; use
   `git show origin/<gatk-branch>:<path>` after fetching. `gatk-sv-main` clone remains 10 behind origin/main.

**Bottom line:** the shipped commits themselves were clean and are now byte-verified against the passing test
run; the audit's net changes are the import cleanup (`d026672`), the template correctness commit (`ce8318e3`,
inert for CI by design), three named factual corrections in §24.2, and one retracted mid-audit claim.

## §26 Handoff freeze — 2026-09-14 12:3x EDT (system clock; §24 artifacts dated 09-11, audit commits 09-14 — both stated, disagreement explained)

**Session-owned live state at handoff: NONE.** No runs, jobs, or nohup output from this session are in
flight. Foreign processes that must NOT be killed: gsutil pid **6955** (another agent's Terra-exec localize,
still running since 09-11), http.server pid **96425** (port 8731, belongs to `~/Work/clarum`). Our gradle
daemon pid 88598 (Java 17, 2 GiB) is harmless and self-restarting.

### Resume here

Nothing to resume *in the replay lane* — Track A code work is fully shipped. The ball is with the owner:

```bash
 # verify shipped state (expect d02667245 / ce8318e3):
git -C <gatk-checkout> ls-remote origin refs/heads/<gatk-branch>
git -C <gatk-sv-checkout> ls-remote origin refs/heads/<branch-under-test>
 # reproduce the green suites (Java 17 mandatory):
cd <gatk-checkout> && JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home \
  ./gradlew test --tests "*TrainSVGenotypingTest" --tests "*SplitReadEvidenceGenotyperTest" \
  --tests "*DiscordantPairEvidenceGenotyperTest"
```

### Mutations ledger (this session; all reversible via normal revert commits)

| mutation | verify / undo |
|---|---|
| 3 commits pushed to `broadinstitute/gatk` `<gatk-branch>` → `d02667245` | `git ls-remote origin refs/heads/<gatk-branch>` |
| 3 commits pushed to `broadinstitute/gatk-sv` `<branch-under-test>` → `ce8318e3` | `git ls-remote origin refs/heads/<branch-under-test>` |
| Terra: **no mutations** (only a read probe → 401) | n/a |
| Docker images: **none built or pushed** (owner's step) | n/a |

### What "good" looks like for the owner's controlled run (pre-flight gates)

1. New `gatk` image (tag `...<you>/gatk:<gatk-branch>-aou-d026672` from tip `d02667245`) — inside the image:
   `gatk TrainSVGenotyping --help` and `gatk ValidateSRCutoffs --help` exist (both are branch-only tools).
2. New `sv-pipeline` image (tag `...<you>/sv-pipeline:<branch>--ce8318e`) — inside the image:
   `grep -n "0 <= row.RD_MEDIAN_SEPARATION < 0.10" /opt/svtk/svtk/adjudicate/labelers.py` matches (proves the
   one baked svtk change shipped); same image serves `sv_pipeline_qc_docker`.
3. AoU workspace attributes `gatk_docker` + `sv_pipeline_docker` + `sv_pipeline_qc_docker` updated to the two
   new tags — **still *inferred* from in-repo `dockers.json`; never verified from here** (API 401).
4. Sanity of GenotypeBatch on the frozen 156-cohort replay: SR `10 71 21.949893`, PE `8 74 21.949893`,
   cutoff `54.949893` (§21/§24 values — the combined-fix signature).

### Open items / next steps (carried, unchanged from §22/§23 unless marked)

- [ ] OWNER: build the two images above, update workspace attrs, run the controlled Terra steps
      (GenerateBatchMetrics, FilterBatch*, MergeBatchSites, GenotypeBatch) — the reason for §24.
- [ ] OWNER decisions: het→hom drift acceptability (§21 measured), fix-5 PESR-exclusion-at-apply, PEQ awk
      precision (6 s.f. truncation in the WDL extraction).
- [ ] `ValidateSRCutoffs` min-observation threshold (currently gates degeneracy only).
- [ ] SR-twin byte-rounding (same latent 1-ulp as fixed PE twin; harmless at current cutoffs).
- [ ] Guard `finalizeFirstPass` empty-PE crash (§23.1; robustness gap, not production defect).
- [ ] NEW: optional inputs hygiene — fill `qc_definitions`/`outlier_cutoff_table` (ref_panel_1kg) + hgdp's
      ~12 missing values so `GATKSVPipelineBatch` test JSONs finally render and get womtool coverage (§25.2).
- [ ] Before any `main` work: `gatk-sv-main` clone is 10 behind `origin/main`.

### Stale-by-design locations (do not trust their fetch state)

- `gatk/` (workspace throwaway): at `18f211cec`, **3 commits behind** tip `d02667245`.
- `gatk-sv-v1.1/gatk/` (nested old clone @ `97f681a0e`): its `origin/<gatk-branch>` tracking ref is stale —
  "behind=0 ahead=0" there is a **lie**; never use this clone.
- `~/IdeaProjects/*`: read-only by policy; their local branches don't move on my pushes — read tips via
  `git show origin/<branch>:<path>` after fetching.

### /tmp artifacts (macOS purges ~3d idle — all expendable)

| path | status |
|---|---|
| `/tmp/womtool-84.jar` (167 MB) | keep or re-download: `curl -L -o /tmp/womtool-84.jar https://github.com/broadinstitute/cromwell/releases/download/84/womtool-84.jar` |
| `/tmp/gatk_combined` | **redundant** — its edits are upstream in `cac2fa3b4`; the modified `gatk` launcher file there was build noise, never commit it |
| `/tmp/fixes34.diff`, `/tmp/expected_vcf_*.vcf`, `/tmp/vcf.diff` | spent evidence; docs §24/§25 hold the conclusions |
| `<scratch-venv>` | miniwdl 1.12.1 + (new) jinja2; reusable |

## §27 Session handoff — 2026-09-15 (UTC 20:49 / local EDT 16:49, both agree on the date) — Track B (Terra 06→10) COMPLETE

Track B ran end-to-end on the frozen 156-sample cohort with **branch code + branch images**, and every
acceptance number in `PLAN.md` §4.1 is now measured, with the load-bearing ones re-verified by
an adversarial review (`runs_trackb/adversarial_review.md`) and by recomputing cost from saved
Cromwell metadata. **Nothing is in flight**: 0 GCE instances (`gcloud compute instances list
--project <gcp-project> --filter "name~^gsv-"` → empty), no local watcher/profile processes, and
all 6 sandbox submissions `Done`.

### What changed, per workstream (evidence in parentheses)

1. **Sandbox + inputs frozen.** Workspace `<your-ns>/<your-sandbox>`
   created; 17 baseline inputs server-side-copied (21.55 GiB) and crc32c-verified; 5 method configs
   (06-10) created and Terra-validated (`trackb_freeze.py`, `trackb_configs.py`).
2. **Chain 06→10 ran on branch images** `gatk-sv/sv-pipeline:<branch>--ce8318` +
   `gatk:<gatk-branch>-aou-d02667` (GATK `4.6.2.0-129-gd026672-SNAPSHOT`). All five steps Succeeded
   (step 06 needed one retry after a resource-localization failure — see gotchas).
   Cost recomputed from saved metadata: chain **1,210.6 VM-min / 169 jobs** vs baseline
   **14,609.6 / 1,929**; step 10 alone **315.2 vs 1,672.8** (`trackb_save_metadata.py` →
   `runs_trackb/metadata/*.json`, every tree printed `(tree complete)`).
3. **Acceptance numbers** (paired `gatk-sv-profile`, frozen rule `profile_summarize.py`):
   PESR genotype concordance **0.9895 / 0.9905** (bucket-weighted), both-called exact match
   **0.990600** bucket-weighted / **0.990512** re-derived pair-level from the VCFs; depth
   **0.9972 / 0.9997**, exact **0.999921**, all **5,386/5,386** baseline depth sites matched by VID.
   Drift direction is non-ref-inflating (`homref→het` 0.37%, `homalt→het` 0.45% pair-level) — the
   **opposite** of Track A §21, which drifted het→hom.
4. **Trained-parameter payload** (`runs_trackb/outputs/`, sha256 in `MANIFEST.json`):
   SR `10 / 86 / 21.949893`, cutoffs `0.1 / 0.6 / 1.0 / 0.1`; PE `4 / 83 / 21.949893`; RD tables
   byte-identical to Track A's means/sds and matching v1.1.1 state-0 to 14 s.f.
   `first_pass_het_cutoff 63.949892999999996` = `42.0 + 1.645·1.4826·9` → the `cac2fa3b4` fix is live
   and the value **discriminates** (v1.1.1's formula would print 56.805).
   SR grid healthy (`selection_made` both classes, 0 NaN, winner ≠ all-zero); `ValidateSRCutoffs`
   passed the table through unmodified (crc32c equal).
   **Streaming Welford RD training validated at production scale**: `-Xmx13599M`, shutdown heap
   3.83 GiB, 12.36 min, no OOM (pre-`fcc52a7da` code OOMed at 16 GiB on this cohort).
5. **Sample-filter layer is a no-op on this cohort**: `filtered_batch_samples_file_new` 156 lines/1248 B
   identical to baseline, 0 outliers both sides (`diff` empty).
6. **Adversarial review + reconciliation** (`runs_trackb/adversarial_review.md`, and the retraction
   table at the end of `runs_trackb/NOTES.md`). Four of my claims did not survive; two of my own
   tooling bugs were found and fixed. Details in "Corrections shouted" below.

### Coordinates (all re-queryable; no secrets here)

| thing | value | how to re-verify |
|---|---|---|
| sandbox workspace | `<your-ns>/<your-sandbox>` (id `<sandbox-workspace-id>`) | `.venv/bin/python -W ignore trackb_status.py` |
| sandbox bucket / Google project | `gs://<workspace-bucket>` / `<google-project>` | `gsutil ls gs://<workspace-bucket>…/submissions/` |
| baseline (read-only, never submit) | `<public-baseline-ns>/GATK-Structural-Variants-Joint-Calling`, bucket `gs://<workspace-bucket>` | `manifests/baseline_v111.json` |
| submissions (all `Done`) | 06 `f053e41f` (retry of failed `4bd2361a`), 07 `c11db664`, 08 `be1072dd`, 09 `79f173a7`, 10 `e4f63471` | `trackb_status.py --costs` |
| baseline step ids that are REAL | step 10 = `86003b19`/wf `e2921cf4`; step 09 = `292f8ecb`/`64ad47ce`; step 08 = `23ffd0c6`/`33b2a2ce`; step 07 = `4b24825e`/`3e574322`; step 06 = `df147230`/`746c675b` | `runs_trackb/metadata/*.json` |
| branch images | `us.gcr.io/<project>/<ns>/gatk-sv/sv-pipeline:<branch>--ce8318`, `…/<you>/gatk:<gatk-branch>-aou-d02667` | pulled successfully by steps 06-10 |
| branch heads (remote, verified) | `<branch-under-test>` → `ae5afc37950e211b77c99c0311eb794490cbbd71`; `<gatk-branch>` → `d02667245120786db8449c13e56af20fe9f3aafa` | `git -C <gatk-sv-checkout> ls-remote origin refs/heads/<branch-under-test>` |
| evidence dirs | `runs_trackb/{NOTES.md,outputs,compare_pesr,compare_depth,tables,metadata,submissions,watch_*.log}` — **no `adversarial_review.md` file exists; the review is `NOTES.md:482+` "ADVERSARIAL REVIEW RECONCILIATION"** (corrected by §28.2-1) | `ls runs_trackb/` |
| dockstore | `github.com/broadinstitute/gatk-sv/<W>@<branch-under-test>` synced via `ae5afc37` (5 workflows) | TRS `…/tools/%23workflow%2Fgithub.com%2Fbroadinstitute%2Fgatk-sv%2F<W>/versions/<branch-under-test>/WDL/files` |

### Resume here

```bash
cd <workspace>
 git -C <gatk-sv-checkout> log --oneline -1 && git -C <gatk-sv-checkout> ls-remote origin refs/heads/<branch-under-test>
 .venv/bin/python -W ignore trackb_status.py                 # verify: all 6 submissions Done
 gcloud compute instances list --project <gcp-project> --filter "name~^gsv-" --format "value(name,status)"
 .venv-profile/bin/python profile_summarize.py runs_trackb/compare_pesr \
     --label-a baseline_v111 --label-b new_java                             # Expected: 0.9895 / 0.9905
 .venv-profile/bin/python profile_summarize.py /tmp/profile_selftest   # verify instrument: 1.0000 everywhere
 .venv/bin/python -W ignore trackb_save_metadata.py --per-call --target 10-new --target 10-baseline
 sed -n '/^## §23/,$p' CHECKPOINT.md; sed -n '/ADVERSARIAL REVIEW RECONCILIATION/,$p' runs_trackb/NOTES.md
```

### What "good" looks like — Expected values for the next check

| check | Expected (measured 2026-09-15) | judge |
|---|---|---|
| `profile_summarize.py runs_trackb/compare_pesr` | concordance 0.9895/0.9905, exact 0.990600, `pct_matched` A→B 0.9351 / B→A 0.9836 | drifts ⇒ tables or rule changed |
| self-test (`/tmp/profile_selftest`, baseline vs itself) | **1.0000 for every metric** | anything <1 ⇒ profile venv/gatk jar changed |
| independent pair-level exact match | 0.990512 (0.990600 bucket-weighted, +0.009 pp from multi-algorithm buckets) | pair-level is the publishable value |
| site recovery A→B | **0.913 (73,884 matcher pairs) / 0.918 (74,241 shared VIDs)** — NOT 0.9351 | 75,639 is `status==TP`, a third inconsistent count |
| step 10 params | SR `10/86/21.949893` + `0.1/0.6/1.0/0.1`; PE `4/83/21.949893`; RD state0 `0.045452815264742/0.006301943860032662` | `sd_het` MAD 9 vs baseline 11 is the known residual |
| chain cost | 1,210.6 VM-min/169 jobs new vs 14,609.6/1,929 baseline; step 09 is the only step where the branch costs **more** | any tree not `(tree complete)` ⇒ cost is a floor |
| step-10 input gates | 17/17 inputs resolve; 7 `+ ".tbi"` sidecars exist | `trackb_fetch_compare.sh` re-resolves |

> **Nothing to push from this workspace.** `<workspace>` is **not a git
> repo** (`git rev-parse` → `fatal: not a git repository`), so `CLAUDE.md`, `**` and
> `runs_trackb/**` are untracked-by-design — do not go looking for a commit that contains them. The
> only git artifact this session is `ae5afc37` on `<branch-under-test>` (pushed; remote head re-read as
> `ae5afc37950e211b77c99c0311eb794490cbbd71`). `repo_state.sh` reports the workspace clones as
> dirty/ahead only because of untracked scratch (`<gatk-checkout>/build_logs/`, `<profiler-checkout>/build/`,
> the nested `gatk-sv-v1.1/gatk/` clone) and `gatk-sv-main` sitting 10 behind `origin/main` — none of
> it is this session's work.

### Mutation ledger (what this session changed outside its own scratch)

| mutation | reversible? | undo / verify |
|---|---|---|
| Created Terra workspace `<your-sandbox>` + bucket contents (21.55 GiB copied inputs) | reversible | `DELETE /api/workspaces/…` (fiss `delete_workspace`, `confirm=True`) — **not done**: keep until the owner has read the numbers |
| 5 method configs `06…10-*` in the sandbox workspace | reversible | `fapi.delete_method_config` per config |
| 6 submissions (one failed workflow: 06 attempt 1) | **not undoable**, only aborable | already terminal; costs $0.18 (step 10) etc. |
| Sandbox workspace **attributes** PATCHed: `rmsk`/`segdups` repointed from `gcp-public-data--broad-references` to `gatk-sv-resources-public` (byte-identical, `.tbi` present), + 14 `*_frz`/docker/provenance attrs | reversible | attribute PATCH again; verify with `GET /api/workspaces/<ns>/<ws>` |
| git commit `ae5afc37` on `<branch-under-test>` (Dockstore branch filter for 5 workflows), **pushed**; remote head re-read = `ae5afc379…` | reversible (force-push revert) | `git -C <gatk-sv-checkout> show --stat ae5afc37` |
| Dockstore: 5 workflows auto-synced `@<branch-under-test>` | reversible via Dockstore UI | TRS versions endpoint above |
| GCR: no new images this session (built in a previous one) | n/a | — |
| Nothing written to `~/IdeaProjects/*`; `<worktrees>/*` trees untouched except the `ae5afc37` commit made there earlier | — | `git -C <gatk-sv-checkout> status -sb` → clean |

### Gotchas found (only ones hit, with the error that proved it)

* `GenerateBatchMetrics.wdl:121-122` requires `region_file_indexes` (`.tbi` of `segdups`/`rmsk`); the
  legacy bucket has none → all 28 `SVRegionOverlap` shards died in localization, rc=None, no stderr;
  Cloud Logging: `ERROR: (gcloud.storage.cp) The following URLs matched no objects or files:
  gs://gcp-public-data--broad-references/…/hg38.randomFo…`. Pre-existing on `main`
  (`2d5d8a44 "New GenerateBatchMetrics workflow (#894)"`), **0** occurrences in v1.1.1; branch file is
  byte-identical to `gatk-sv-main`'s.
* Terra's Cromwell returns **no** `subWorkflowMetadata` / `subWorkflowList` even with
  `expand_sub_workflows=True` — nested jobs are only reachable via each call's `subWorkflowId`. A tree
  with a failed child fetch **silently** under-counts: it read 3 jobs/9.8 VM-min, then 177/918.1, for a
  true 264/1672.8. `trackb_save_metadata.py` now retries once and prints `(tree complete)` or
  `** N sub-workflow(s) FAILED … cost is a floor`.
* `gatk-sv-profile` embeds **label values in column names** (`--label-a baseline_v111` →
  `n_sites_baseline_v111`); a summariser that hardcodes `_baseA` dies with
  `AttributeError: 'DataFrame' object has no attribute 'n_sites_baseA'`.
* `bcftools index -n` / record counts are **not** comparable across pipelines via `(CHROM,POS,REF,ALT)`:
  the branch resolves `REF` and writes breakend BNDs, so exact-key intersection ≈ 0 while the tool's
  own matcher finds 73,884 pairs. Key on the `ID`/VID instead.
* `gsutil cp -r 0-400000 <url>` (range form) wrote nothing here; `gsutil cat "$U" | head -c 400000` worked.
* Rawls entity link members are not in `entity_sample` output (`link members: 0`), so an array input
  like `this.sample_sets.outlier_filtered_pesr_vcf_new` must be proven at submit time via
  `inputResolutions`, not by reading the entity.
* Recurring `GCP Batch task exited with VMPreemption(50001)` (06, 09 ×1, 10 ×4 attempts); all
  recovered; for preempted attempts GCS holds only `script` (no stderr/rc), so Cromwell's
  `failures[].message` is the only record.
* No stale `17925b24…` record exists in NOTES/CHECKPOINT/PLAN (grep), but the id was quoted in an
  earlier session's chat; `get_submission` → HTTP 404 `Submission with id 17925b24-… not found`.

### Corrections shouted (earlier claims proved wrong — the old text is fixed, not restated)

1. **"step-08 output −19% PESR sites"** → wrong reading: −19% is *records*; unique loci are **−5.8%**
   (72,281→68,054) and **wham 26,351→9,105 is 93.5% of the gap**; 13,509 "missing" IDs are the same
   locus under another caller. PLAN §4.1 row rewritten.
2. **"step 09 drops 3,258 sites"** → wrong: step 09 `SVCluster` **merges** wham onto manta
   (`algorithms` `manta,wham` = 3,258). `GenotypeSVs` + `SeparateDepthPesr` lose **zero** records.
3. **"`site_overlap` A→B 0.9351"** → not a site-recovery number (that is `status==TP`); publish
   **0.913 (matcher) / 0.918 (VID)**. The tool reports 73,884 / 75,639 / 74,241 for one quantity.
4. **"any consumer computing `END-POS` mis-reads the new VCFs"** → severity overstated: no in-pipeline
   consumer does (`svfile.py:265` is clustering-only and identical in v1.1.1; the `<BND>` matcher is an
   upstream single-sample path). Re-scoped to interop/spec.
5. **`sd_het` "0.818× / 0.900×"** → false precision: MAD is integer-quantised (1 unit = 2.438877);
   say "raw MAD differs by 2 (SR) / 1 (PE)". Also `first_pass_het_observations ==
   second_pass_het_observations` is **by construction** (`SplitReadEvidenceGenotyper.java:555`).
6. **Cost figures in my own notes** — "885 VM-min" and "382 VM-min" were **root call-span sums**
   (885.5 / 382.1), and the chain comparison "434.8 vs 885.6" was wrong; auditable values are in the
   Expected table. Baseline step 06 really is **12,805 VM-min / 1,633 jobs**.
7. **CLAUDE.md "audited: SR≠PE in 9 of 13 run dirs"** → my own parse of `runs/*` gives **18
   dirs: identical in 8, differing in 10** (every full-cohort run has SR == PE == 21.949893).
8. **CLAUDE.md branch-tip table** → `<branch-under-test>` tip is now **`ae5afc37`** (Dockstore publish
   commit, descendant of `ce8318e3`); `<gatk-branch>` unchanged at `d02667245` (no code commits this
   session).

### Deliverables written this session

| file | what it is |
|---|---|
| `trackb_{freeze,configs,status,fetch_compare,save_metadata}.py`/`.sh` | freeze inputs → 5 configs → status/cost → fetch+profile+table → auditable metadata |
| `profile_summarize.py` | frozen aggregation rule (label-derived columns, per-side weights, self-test at 1.0000) |
| `runs_trackb/NOTES.md` | evidence log incl. the full retraction table |
| ~~`runs_trackb/adversarial_review.md`~~ **never landed** (§28.2-1) | the unsoftened adversarial review lives in `runs_trackb/NOTES.md:482+` |
| `runs_trackb/metadata/*.json` | raw Cromwell metadata (recursive, completeness-flagged) — the cost audit |
| `runs_trackb/outputs/` (+`MANIFEST.json`) | genotyped VCFs, 4 param tables, SR diagnostics, ploidy, metrics |
| `runs_trackb/compare_{pesr,depth}/` | paired `gatk-sv-profile` outputs (11 modules each) |
| `runs_trackb/tables/batch_tables.tsv` | 316-row table diff: MATCH 11 / DELTA 31 / STRATEGY 6 / MISSING 268 |
| `PLAN.md` §4.1 | verdict table filled and corrected |
| `<gatk-sv-checkout>/.github/.dockstore.yml` → `ae5afc37` | Dockstore branch filter, pushed |

### Open items / next steps (nothing running; all of these are owner decisions or new measurements)

- [ ] **Owner decision A — `pe_count` 4 vs v1.1.1's 8.** Cause is step 07's own `PEQ 21.71`
  (0.625× baseline). Margin is **0.022% of PEQ** (`computeCountCutoff` returns `Math.max(i-1,1)`,
  first `i` with `qual>=PEQ` = 5), so 4 is knife-edge: ±0.02% PEQ ⇒ 3. Decide whether the step-07 RF
  shift (`labelers.py` `RD_Median_Separation` 0.15→0.10 + metric-population change, `metrics_common_*`
  population dropped) is acceptable, or revisit 06/07 first.
- [ ] **Owner decision B — formulation asymmetry.** Only PE got `cac2fa3b4`'s unrounded
  `-10·log10 p` + `>=`; SR still uses byte-rounded `QualityUtils.errorProbToQual` + strict `>`
  (`SplitReadEvidenceGenotyper.java:164-167` vs `DiscordantPairEvidenceGenotyper.java:124-127`).
  Make them one function or justify the difference.
- [ ] **Measure the per-caller/wham record loss** (steps 06-08): fetch both sides' step-07
  per-caller VCFs and split "filter loss" from "deredundation". Suspect: deleted `RewriteScores` +
  `select_first` reorder feeding `FilterAnnotateVcf.scores` (`<gatk-sv-checkout>/wdl/FilterBatchSites.wdl:55-58`).
- [ ] **Observability regressions to file against the branch**: (a) no `GenotypeBatchMetrics` task in
  `GenotypeBatch.wdl` → no step-10 metrics file at all (the WDL still ships `GenotypeBatchMetrics.wdl`,
  just unwired); (b) no `TrainSVGenotyping` log output and **no PE diagnostics section**, so PE's het
  n/MAD/median are unobtainable; (c) `metrics_common_*` population (27 rows) dropped in step 06.
- [ ] **`sd_het` residual (MAD 9 vs 11/10)**: reproduces in production; the §23 (2026-09-11) candidates were refuted; needs an
  emulator-scale reconstruction of v1.1.1's het population, or accept the 0.99 GT concordance as the
  ship evidence.
- [ ] **`ValidateSRCutoffs` min-observation threshold** (CLAUDE.md item 7) still unimplemented — this
  run's grid was genuinely healthy so "OK" was correct, but the gate still cannot catch the AoU case.
- [ ] **VCF representation spec** (INS `END==POS` 100%, resolved `REF`, breakend BNDs): publish the spec
  or align with v1.1.1; check *external* consumers (nothing in-pipeline reads it).
- [ ] **Delete or keep the sandbox**: bucket holds 21.55 GiB of copied inputs + all outputs; the config
  `10-GenotypeBatch` chain is re-runnable in one submit per step.
- [ ] Not exercised/not measured, deliberately: local docker content gates (`gatk TrainSVGenotyping
  --help`, `labelers.py` 0.10 grep inside the image) — skipped by owner choice, still unchecked.

## §28 Pickup session 2026-09-15 ~16:55-17:20 EDT (local clock; §27 froze at 16:49 EDT the same day, so nothing had hours to decay in) — §27 contract closed: 3 divergences, 1 new code-level defect candidate

Nothing was in flight at pickup and nothing was submitted, deployed, or re-run by this session
(duplicate-work guard honoured: the chain already exists → read results, never relaunch).
Foreign processes still alive and untouched: gsutil/python pid **6955**, `clarum` http.server pid **96425**.

### 28.1 §27 expectations ledger, re-measured

| §27 row | Expected | Actual at pickup | stamp + proof |
|---|---|---|---|
| 6 submissions `Done` | all Done | 5 configs → `Done`, `wf=Succeeded` (the 6th is the failed 06 attempt-1, not a "latest per config" row) | CONFIRMED — `trackb_status.py` |
| 0 GCE instances | empty | empty | CONFIRMED — `gcloud compute instances list --project <gcp-project> --filter "name~^gsv-"` |
| remote heads | `ae5afc37…` / `d02667245…` | byte-equal | CONFIRMED — `git ls-remote origin refs/heads/…` in `<gatk-sv-checkout>`, `<gatk-checkout>` |
| profile instrument self-test | 1.0000 everywhere | 1.0000 everywhere (627 rows/module) | CONFIRMED — `profile_summarize.py /tmp/profile_selftest` |
| PESR concordance | 0.9895/0.9905, exact 0.990600, pct_matched .9351/.9836 | identical to the digit | CONFIRMED — `profile_summarize.py runs_trackb/compare_pesr` |
| step-10 param payload | SR `10/86/21.949893`+`.1/.6/1/…0.1`, PE `4/83/21.949893`, RD state0 `0.045452815264742/0.006301943860032662` | verbatim in `runs_trackb/outputs/*_geno_params.tsv` | CONFIRMED (also all 7 MANIFEST sha256 re-hash OK) |
| depth channel | exact 0.999921, 5,386/5,386 by VID | exact **0.999921**, shared VIDs **5,386/5,386** | CONFIRMED — new `pair_level_concordance.py` (812,448 both-called pairs, 64 discordant) |
| chain cost | new 1,210.6/169; base 14,609.6/1,929 | new **1,210.6 (169)** exact; base **14,609.5 (1,935)** | CONFIRMED for cost, **DIVERGED for the baseline job count** → 28.2 |
| site recovery | 0.913 matcher (73,884) / 0.918 VID (74,241) | matcher **73,884** (`compare_pesr/pesr.profile.log:116`), VID **74,241** re-derived | CONFIRMED |
| pair-level exact match | 0.990512 | **0.989969** under a frozen rule | **DIVERGED** → 28.2 |
| step-10 input gates | 17/17 resolve, 7 `.tbi` | WOM required **17**, config binds **17**, last submission resolved **17**, sidecars **7/13** gs:// inputs | CONFIRMED — new `trackb_check_inputs.py` |
| RD training population | curated 56-locus bed | `training_intervals = gs://gatk-sv-resources-public/hg38/v0/sv-resources/resources/v1/train_hg38_reviewed_final.bed` (resolved value, not an inference) | CONFIRMED — same script (this is §17.1's R1 refutation re-proved from the *actual run*) |
| svtk `labelers.py` 0.10 baked change | gate deferred | **source-level CONFIRMED** on `<branch-under-test>` (`src/svtk/svtk/adjudicate/labelers.py:25` `0 <= row.RD_MEDIAN_SEPARATION < 0.10` vs v1.1.1 `:25` `< 0.15`, plus `PE_log_pval → PEQ < -10*np.log10(0.05)`) | the **in-image** grep is still not exercised (label kept) |

### 28.2 Divergences (these are now the work; none of them changes the ship decision)

1. **`runs_trackb/adversarial_review.md` does not exist.** `find` over the whole workspace returns
   nothing; §27's deliverables table and CLAUDE.md both cite it. The review content is real and is
   in `runs_trackb/NOTES.md:482+` ("2026-09-15 — ADVERSARIAL REVIEW RECONCILIATION", retractions
   table + the two self-faults). Pointers corrected in place; the number "10 findings" is therefore
   *not* independently verifiable as a separate artifact.
2. **Baseline step-08 job count is 21 attempt-records, not 15** (chain baseline **1,935**, not
   1,929). VM-min is unaffected (59.6 reproduces to the digit, and 14,609.6 vs 14,609.5 is print
   rounding). Reproduce: `trackb_recompute_cost.py` (offline, over `runs_trackb/metadata/*.json`,
   every tree `(tree complete)`); the counts are attempt records carrying both `vmStartTime`/`vmEndTime`
   — 08-baseline has 8 unique call paths, 4 of them retried.
3. **"independent pair-level exact match 0.990512" cannot be reproduced by any single recorded rule.**
   Frozen rule (VID-key join, both sides non-no-call, unphased GT equality) over the two PESR VCFs on
   disk gives **0.989969** on 11,581,596 both-called pairs (`pair_level_concordance.py`,
   command in its docstring). §27's own drift rates imply inconsistent denominators
   (homref→het 0.37% ⇒ ~12.05 M pairs; homalt→het 0.45% ⇒ ~11.48 M pairs), so that figure was an
   ad-hoc subset, not a rule. Publishable options, both defensible: **0.990600** (profile,
   bucket-weighted) or **0.989969** (frozen pair-level, rule now committed). The depth side of the same
   ad-hoc derivation *did* reproduce exactly (0.999921), so only the PESR row is affected.

### 28.3 NEW, code-level, for OWNER DECISION A (step-07 PEQ/`pe_count`): the renamed `*Q` family is NOT one scale

Read at `<gatk-branch>` @ `d02667245`, all four writers of the metric columns that the step-07 RF
consumes (`<gatk-sv-checkout>/src/svtk/svtk/adjudicate/adjudicate_sv.py:105-106` features
`RD_MEDIAN_SEPARATION, RDQ, RD_P2` / `SRQ`, `PEQ`, `PESRQ`, …):

| metric | code | value of the metric | cap |
|---|---|---|---|
| `SRQ`, `SR1Q`, `SR2Q` | `aggregation/SplitReadEvidenceTester.java:207-210` → `aggregation/EvidenceStatUtils.java:23` | **`-10·log10 p`** (GATK QUAL) | `MAX_QUAL = 99` (`:54`) |
| `PEQ` | `aggregation/DiscordantPairEvidenceTester.java:59-64` → same `probToQual` | **`-10·log10 p`** | `99` (`:23`) |
| `PESRQ` | `aggregation/PESREvidenceTester.java:74-75` → same | **`-10·log10 p`** | `99` (`:22`) |
| `RDQ`, `RD_P2` | `aggregation/DepthEvidenceTest.java:73-74` | **`-log10 p`** — the v1.1.1 scale, written into a `*Q`-named field (its own header says so: `walkers/sv/AggregateDepthEvidence.java:258` "Depth evidence quality (-log10 p-value)") | caller-supplied |

Consequences, in order of confidence:
* **Verified by reading:** cross-version cutoff comparisons must be per-metric. §27's PEQ conversion
  (21.71 QUAL vs v1.1.1's 3.4743558 `-log10` = 34.74 QUAL, 0.625×) is **correct**; the same arithmetic
  applied to the RD rows would be wrong by 10×. That is exactly why v1.1.1's RD cutoffs look
  *unchanged* on the branch (Depth-DEL `RD_log_pval` 3.7399344443949145 vs branch `RDQ`
  3.740000009536743, i.e. the same quantity + float32 print noise) while SR/PE moved.
* **Verified from the shipped `cutoffs` file:** the branch's `PESRQ` cutoff is **exactly 99.0** — the
  `MAX_QUAL` ceiling — where v1.1.1's `PESR_log_pval` 13.89742342090406 ≡ **138.97** in QUAL, i.e.
  *above* the branch's cap. So the PESR quality metric is **saturated at the cap** and the RF-selected
  cut sits on the ceiling; the model cannot rank anything above it.
* **NOT measured:** whether saturation changes the pass/fail set. It cannot be measured from any
  retained artifact — the Q columns are consumed inside step 07 and are gone downstream: the step-09
  `ConcatVcfs` VCF (step-10 input, 81,413 records, streamed from GCS) declares only
  `ALGORITHMS BOTHSIDES_SUPPORT CHR2 END END2 EVIDENCE HIGH_SR_BACKGROUND MEMBERS SR1POS SR2POS STRANDS SVTYPE`;
  the genotyped PESR VCF carries only `varGQ`. Closing this needs a step-07 rerun that dumps the
  metric table (owner-scale experiment), or reading `adjudicate_sv.py`'s comparison direction to see
  whether a cut at the cap is `>=` (passes only saturated rows) or `>` (passes none).
* Not claimed: that this causes the −5.8 % unique-locus loss or `pe_count` 4. It is a candidate
  mechanism, same population as Owner decision A, and it is cheap to settle next.

### 28.4 Deliverables this pickup (all new, none committed anywhere; workspace root is not a git repo)

| file | what it is | reproduces |
|---|---|---|
| `trackb_recompute_cost.py` | chain-cost re-check **offline** from `runs_trackb/metadata/*.json`, completeness-flagged, `--groups` roll-up | NOTES "Auditable cost" table + the 28.2-2 correction |
| `pair_level_concordance.py` | frozen rule for VCF-level genotype concordance, no profile, no buckets | depth 0.999921 / 5,386 sites; PESR 0.989969 + drift matrix |
| `trackb_check_inputs.py` | WDL-required (womtool) vs config-bound vs submission-resolved, then `.tbi` sidecar probe; `--step 06..10` | §27 step-10 input gates (17/17, 7/13) |

Promote to standing instructions: **pickup gates for this workspace** =
`git ls-remote` both branches; `trackb_status.py`; `gcloud … --filter "name~^gsv-"`;
`profile_summarize.py /tmp/profile_selftest` (1.0000 or the profile venv/jar moved);
`trackb_recompute_cost.py`; `trackb_check_inputs.py`. All read-only, all < 3 min total.

### 28.5 Narrowing owner decision A while the metric-scale thread was open (code read, `<gatk-sv-checkout>` @ `ae5afc37`)

Which `cutoffs` rows actually reach a decision anywhere downstream:

| consumer | what it reads | file:line |
|---|---|---|
| step 07 `FilterAnnotateVcf` (the pass/fail gate for every caller VCF) | **only the RF probability** — `awk '($3!="NA" && $3>=0.5)'` over `<batch>.scores`; no metric column is compared to a cutoff here | `wdl/FilterBatchSites.wdl:122` (`svtk adjudicate` → scores+cutoffs), gate at `:138` |
| step 10 `GenotypeBatch` → `TrainSVGenotyping` | `PEQ` → `--pe-quality`, `SRQ` → `--sr-quality`, plus `PESR_SEP`/`DEPTH_SEP` = the two `RD_MEDIAN_SEPARATION` rows | `wdl/GenotypeBatch.wdl:288-315, 346-347` |
| anywhere else | nothing reads `RDQ`, `RD_P2`, `PECS`, `SRCS`, `PESRCS` or **`PESRQ`** — those cutoff rows are computed and never consumed | (grep of `wdl/` + `src/`) |

Therefore, as a verdict on the 28.3 thread:
* `pe_count` 4 is **not** a units artifact and **not** cap saturation: `PEQ = 21.71` is far below its
  cap of 99, and it is the only cutoff row that can move it. The shift is a genuine difference in the
  RF fit — consistent with the two step-07 changes already known (`labelers.py` 0.15→0.10 and the
  metric-population/`metrics_common_*` drop). It stays an owner decision about whether step 07's
  filter is *right*, not a units bug to fix.
* The `PESRQ = 99.0`-at-cap observation is real but lands on a **dead row**: nothing consumes it. Its
  only possible effect is as an RF *training feature* (a saturated feature cannot split), which would
  belong to the step-07 pass-set change, not to the genotype tables. Unmeasured, and now cheap to
  deprioritise.
* The `RDQ`/`RD_P2` `-log10` scale mismatch is a **spec/interop** item (a `*Q` field that is not QUAL,
  documented as "-log10 p-value" at `AggregateDepthEvidence.java:258`) with no in-pipeline consumer of
  its cutoff; it explains why v1.1.1's RD cutoffs look unchanged, and no more than that.

## §29 Handoff freeze — 2026-09-18 11:19 EDT = 15:19 UTC

Date is externally corroborated, not taken from the box alone: `date` said `2026-09-18 11:19 EDT` and
`curl -I https://www.googleapis.com/storage/v1/b` returned `Date: Fri, 18 Sep 2026 15:19:56 GMT`.
This session **crossed UTC midnight**: the `sv-shell` rebuild / PR work is 2026-09-17 (build log
`2026-09-17T20:44Z`), while the A/B run, its failure, issue #965 and all `SVShell` work are 2026-09-18
(failing task stderr `2026-09-18 04:47 UTC`). Earlier sections stamped 09-17 stay correct.

### 29.0 Live state at freeze — re-queried in the last minutes, not carried forward

| thing | state at handoff | how to re-query |
|---|---|---|
| `gatk-sv` branch `<branch-under-test>` | local `1fe2d87e2` == `origin/1fe2d87e2` **IN-SYNC**, worktree clean | `git -C <gatk-sv-checkout> rev-parse HEAD` + `git ls-remote origin refs/heads/<branch-under-test>` |
| `gatk` prod branch `<gatk-branch>` | `36c8568c3` in sync, worktree clean except untracked `build_logs/` | same, repo `<gatk-checkout>` |
| PR #961 | `OPEN`, **1 commit**, head `1fe2d87e28`, `mergeable=MERGEABLE`; `Verify` **fail** (dockers.json readonly, expected), CodeQL/Linting/WDL pass | `gh pr view 961 --repo broadinstitute/gatk-sv --json headRefOid,commits,mergeable` |
| Terra arm A (WDL pipeline) | **`Failed`**, cost **$4.31** (final) | `twatch.py … status ad6c8a60-…` |
| Terra arm B (WDL pipeline) | **abort accepted (HTTP 204, with `{"userComment": …}`) but Terra STILL reports `Running` ~1 h later, cost $1.46 and still creeping (+$0.02 between two polls 20 min apart)** — the in-flight task drains; **not** `Aborted` at freeze. If it is still `Running` at pickup, the abort was accepted and ignored for the child workflow: check the child workflow's own id and abort that | `twatch.py -w <public-baseline-ns>/<your-single-sample-workspace> status 51e31b4d-…` |
| Dockstore `SingleSamplePipeline@<branch-under-test>` | published (confirmed ~40 min after the filter landed) | TRSE `…/tools/%23workflow%2Fgithub.com%2Fbroadinstitute%2Fgatk-sv%2FSingleSamplePipeline/versions` |
| Dockstore `SVShell@<branch-under-test>` | **published** (11 versions listed) — arm B's WDL is available | same endpoint, `…%2FSVShell/versions` |
| `sv-shell` prod image | `gatk-sv/sv-shell:<branch>--8748f0` = `sha256:cfe0a28f50f4d4d02af857bfb891488a5803945b6dd3934868a4181ccc70ad45` | `gcloud container images describe …` |
| `svshell_arms.py` | **built but not run**: `prep` works, `create`/`submit` never executed, no SVShell configs exist in Terra yet | `… svshell_arms.py prep` |

### 29.1 What changed, per workstream — with the evidence behind each claim

**A. Branch `<branch-under-test>` re-based twice (main moved twice under us) and stayed one commit.**
`0750a0cc` → `baf359fd` → `eb32d514` → **`1fe2d87e`** (tip). Two upstream merges landed mid-session:
#962 (`fc014646`, the `.dockstore.yml` YAML fix) and #964 (`67b318e7`, ploidy table) + `e1909d2f`
("Update docker images list"), which made the PR `CONFLICTING`. Only one file conflicted
(`inputs/values/dockers.json`) and it was the intended semantic conflict — our 3 pins vs main's new
`2026-09-17-v1.1.1-67b318e7` release pins; resolution kept ours. Verified after rebase: `dockers.json`
diff vs `origin/main` is exactly 4 keys; #964's `ploidy_table` additions survived **alongside** our
`rd_depth_table` binding (`wdl/GATKSVPipelineSingleSample.wdl:1212`). Shape at handoff: **32 files,
+414 −89, 1 commit**. Local gates after each rebase: miniwdl over all `wdl/*.wdl` pass,
`validate.sh -t` **29/29**, `build_default_inputs.sh` OK.

**B. The `sv-shell` jar-pin defect is closed, and the image's base was wrong too.**
`dockerfiles/sv-shell/Dockerfile:26` moved `672d8557d…` → `36c8568c37b382fa58e3e2a057849978b7ff7fae`.
Why it was a real defect: branch `src/sv_shell/genotype_svs.sh:82-83` passes
`--rd-depth-table`/`--rd-pesr-table`, but `GenotypeSVs` at `672d8557d` declares only `"rd-table"` — the
shipped jar could not parse its own driver's flags. Build provenance from the log:
`#8 50.05 HEAD is now at 36c8568c3 TrainSVGenotyping batch train…`. Separately, the image previously
pinned (`e46faf`) was `FROM …/sv-pipeline:2026-09-09-v1.1.1-31d3df3c` — **v1.1.1 python under new shell
scripts**; `build_docker.py` takes sv-shell's `FROM` from `dockers.json` *at the built commit*, so the
rebuild is `FROM …/sv-pipeline:<branch>--9b9809@sha256:eb5492bb882a…` (68 references in
`docker-build/build_svshell_<branch-under-test>_8748f0bb.log`, `GATK_SV_BUILD_RESULT=SUCCESS`).
Image content still represents the tip: `git diff --name-only 8748f0bb HEAD -- src dockerfiles` is empty
(only `dockers.json` + `.dockstore.yml` changed after the build, and neither is copied into any image).

**C. PR #961 body now states the pin, the provenance and the corrections** (verified by re-fetching the
live body and grepping for distinctive substrings `cfe0a28f`, `companion change forced by the Java
argument split`, `HEAD is now at 36c8568c3` — each found exactly once).

**D. Dockstore**: added `<branch-under-test>` to the branch filters of **`SingleSamplePipeline`** and
**`SVShell`** (so now 7 of 32 entries carry it: the 5 Track B steps + those 2). Both confirmed published
via the TRSE versions endpoint. `.github/.dockstore.yml` delta vs `main` is exactly **6 → 7** single-line
insertions, verified by parsing the file and printing the parsed filter lists.

**E. The `GATKSVPipelineSingleSample` A/B ran, and found an upstream defect (not ours).** See 29.1-F.
Arms: `A_baseline_main` (Dockstore `@main`, main's images, single `genotyping_rd_table`) vs
`B_branch_<branch-under-test>` (`@<branch-under-test>`, our 3 pins, split RD tables staged in my own
workspace bucket). The A/B delta was **only** 3 images + the RD-table rename — verified by printing the
set difference of the two configs' 110/111 inputs.

**F. Upstream defect reported: https://github.com/broadinstitute/gatk-sv/issues/965.**
`task ConcatBaf` (added by `31d3df3c` = **#945**, 2026-09-09) invokes `/gatk/gatk … PrintSVEvidence`
while declaring `docker: sv_pipeline_docker`; the `sv-base`→`sv-pipeline` chain ships GATK as
`ARG GATK_JAR="/opt/gatk.jar"` and **never** creates `/gatk`. Proven wrong-from-birth, not rotted:
the task, the command and the runtime all land as `+` lines in that one commit, and I checked
`sv-base`/`sv-pipeline` Dockerfiles at `v1.1`, `v1.1.1`, `main`, `HEAD` for any `/gatk` path (0 hits).
Single-sample only; batch/cohort mode merges BAF through `BatchEvidenceMerging.wdl` under
`docker=gatk_docker`, which is correct. Suggested fix (1 line, this file's own convention):
`java -Xmx2g -jar ${GATK_JAR} PrintSVEvidence`. **Deliberately kept out of #961** per owner decision.

**G. What `sv-shell` coverage actually is** — needed so the next session does not over-claim:
`wdl/GATKSVPipelineSingleSample.wdl` contains **zero** references to `SVShell`/`sv_shell_docker`, so the
A/B above **did not exercise the rebuilt image at all**. What does cover it: the plumbing scan (all 15
`jq -n` blocks executed, 0 new null/empty keys vs `origin/main`), the shipped-image byte check (driver
md5 `259b0b4ce2c92b4a632dc84ce024f7e5`, fixture `e18605c19156cbdf02be7f7981f5a8f3` == branch bytes), and
the jar-pin/`GenotypeSVs`-argument comparison. What is **still not exercised**: an SVShell end-to-end run.

### 29.2 Corrections this session made to earlier load-bearing claims (verified against git)

| wrong claim | where it lived | the truth, with proof |
|---|---|---|
| "`origin/main` cannot launch the single-sample pipeline (`miniwdl: No such input rd_table`); PR #961 repairs it" | NOTES §"svshell replay harness" §2, and I nearly shipped it in the PR body | **Retracted.** My own methodology bug: main's WDL was loaded from a scratch dir that already held this branch's imports. Each ref materialised separately ⇒ **both load clean**. Main's `rd_table` at `:1211` is an argument of `call genotypebatch.GenotypeSVs`, whose **task** declares `rd_table` on main and the split pair on the branch. So my edit is a **companion change forced by the Java argument split**, not a repair. Fixed in `runs_trackb/NOTES.md` (verbatim text kept under a RETRACTED heading), in the PR body, and in `CLAUDE.md`. |
| "`wdl/SVShell.wdl` is unlaunchable on both refs" | NOTES, and my message to the owner | **Too strong.** It is launchable, just not from a bare input JSON: `RunSVShell` requires **102** inputs (main) / **103** (branch) and the WDL `call` binds **28**, so **74** must arrive as call-scope config keys `SVShell.RunSVShell.<x>` — which is how real single-sample users' configs work. miniwdl's `IncompleteCall` is stricter than Cromwell/WOM here. Measured with `svshell-replay/_svshell_probe.py` on clean per-ref trees. |
| "my `.dockstore.yml` edit is fine" (a check that said so) | my own verification one-liner | Two defects: the edit **deleted `- main`** from one entry and duplicated a `tags:` key (and a later "repair" of mine deleted **3 whole workflow entries**, 32 → 29). The check was worthless because its format string interpolated the expected value `main` into its own output. Caught by entry-count + printing parsed filters. I had already force-pushed the broken yml to the PR tip; fixed in `608737a6`, now in `1fe2d87e`. |

### 29.3 Coordinates and verify commands — external objects created or touched this session

| object | identifier | create/undo |
|---|---|---|
| PR | https://github.com/broadinstitute/gatk-sv/pull/961 — head `1fe2d87e28`, 1 commit, MERGEABLE | body edited twice (`gh pr edit --body-file`) |
| Issue (new) | https://github.com/broadinstitute/gatk-sv/issues/965 | `gh issue close 965` to undo |
| Branch | `broadinstitute/gatk-sv` `<branch-under-test>` → `1fe2d87e` | force-pushed 3× with `--force-with-lease`; prior tips: `0750a0cc` (tag `archive/<branch-under-test>-pre-gatk-pin`), `3cbf20a7` (`archive/…-pre-review-fixes`), `f19319fd` (`archive/…-pre-resquash`) |
| Image (new) | `us.gcr.io/<project>/gatk-sv/sv-shell:<branch>--8748f0` = `sha256:cfe0a28f50f4…` | `add-tag` from `<you>/gatk-sv/sv-shell:<branch>--8748f0` (same digest); `gcloud container images delete <prod-tag>` to undo |
| Dockstore entries | `SingleSamplePipeline@<branch-under-test>`, `SVShell@<branch-under-test>` | driven by `.github/.dockstore.yml`; remove the filter lines to undo |
| Terra configs (mine) | `<billing-project>/A_baseline_main`, `<billing-project>/B_branch_<branch-under-test>` in `<public-baseline-ns>/<your-single-sample-workspace>` | `fapi.delete_workspace_config(NS, WS, '<billing-project>', <name>)` |
| Terra submissions (mine) | A `<submission-id-2>` (`Failed`, $4.31); B `<submission-id-3>` (abort accepted, last seen `Running`, $1.44) | A terminal; B: `PATCH …/submissions/<id>?workflowStatus=ABORTED` **with** `{"userComment": …}` body |
| GCS objects (mine) | `gs://<workspace-bucket>/genotyping_tables/all_samples.rd_{depth,pesr}_geno_params.tsv` (copies of my step-10 rerun tables) | `gsutil rm` them; sources untouched in `gs://<workspace-bucket>…` |
| a colleague | **read-only all session**; no writes to `<colleague-ns>/*`, no writes to `gs://<gcp-project>-vj` or `gs://<workspace-bucket>…` | — |

### 29.4 Resume here

```bash
 cd <workspace>
 git -C <gatk-sv-checkout> rev-parse --short HEAD          # expect 1fe2d87e
 git -C <gatk-sv-checkout> status --porcelain              # expect empty
 git -C <gatk-sv-checkout> diff --stat origin/main HEAD | tail -1   # expect 32 files, +414 -89
 timeout 200 python3 ~/.pi/agent/skills/terra-monitor/scripts/twatch.py \
   -w <public-baseline-ns>/<your-single-sample-workspace> \
   status <submission-id-2> <submission-id-3>
   # 29.0 saw B still Running after the abort was accepted; it should be Aborted by now. Cost must not exceed ~$2.
 GSV_CFG_VERSION=1 .venv/bin/python svshell-replay/svshell_arms.py prep
   # the real next step: it prints UNRESOLVED required inputs per arm. 74 were unresolved at freeze.
 .venv/bin/python svshell-replay/single_sample_arms.py prep
   # the WDL-pipeline pair (already run once; A failed upstream on #965)
 sed -n '1,40p' svshell-replay/README.md          # harness design + its known limits
```

### 29.5 What "good" looks like — expected values, so a result can be judged not just read

| check | expected | what a deviation means |
|---|---|---|
| `svshell_arms.py prep` | `UNRESOLVED=0` for **both** arms | any unresolved name is a real input gap; do **not** submit with gaps — Cromwell fails late and expensively |
| arm A vs arm B differing keys | exactly: `sv_shell_docker`, `sv_pipeline_docker`, `gatk_docker`, `genotyping_rd_table`(A) vs `genotyping_rd_depth_table`+`genotyping_rd_pesr_table`(B) | any other difference = leakage; the A/B would prove nothing |
| submission-time validate | `CLEAN` for both | `validate_config` says `VALID` even for a **nonexistent** config — trust only `POST …/submissions/validate` |
| both arms finish | final VCF record counts within a few % of each other; no `rc=127` | an `rc=127` "No such file" is again an image/path mismatch, not a genotyping result |
| ConcatBaf issue #965 | still open; `main`'s single-sample still fails at `call-ConcatBafCase` | if closed as fixed, re-run the WDL-pipeline pair (it becomes usable as an A/B) |
| PR #961 | `Verify` red on `dockers.json` only; everything else green; admin merge required | any *other* red = a real regression to fix before merge |
| merged state | `dockers.json` on `main` carries the 4 branch pins → they must be replaced by CI-built images at the next release bump | forgetting this leaves `main` pointing at personal builds |

### 29.6 Gotchas hit this session — each with the error text that produced it (re-verify before trusting any of it)

- **`sample_id` is a reserved entity attribute** for `entityType=sample` — it *is* the entity name. Writing it: `Attribute name sample_id is reserved and cannot be overwritten`. So `this.sample_id` needs nothing.
- **Creating a method config needs `methodConfigVersion` as an Int and `deleted: false`**: `Expected Int as JsNumber, but got "d1329193-…"` and `Object is missing required member 'deleted'`.
- **Entity upsert wants a single object**, not a list: `Object expected in field 'name'`.
- **Aborting a submission needs a JSON body with a comment**: `Object is missing required member 'userComment'`; correct call is `PATCH …/submissions/<id>?workflowStatus=ABORTED` + `{"userComment": "…"}` (→ 204).
- **`validate_config` returns `invalid: []` for a config that does not exist** — my first `validate` printed `VALID` twice for two configs whose `create` had 400'd. Assert existence with `get_workspace_config`, and treat `POST …/submissions/validate` as the only completeness gate (it is what found 11 absent required inputs).
- **From this network the Rawls list endpoints answer `[]`** (`…/methodconfigs`, `…/entity-types`) **even for objects that exist**; direct GET by name works. Discover config names from `submissions[].methodConfiguration{Namespace,Name}`. Legacy entity GET is `…/entities/sample/<name>`; `…/entities/default/sample/<name>` → `405`.
- **`${workspace.x}` is a template reference, not a value.** The repo's *built* Terra inputs keep them un-substituted; quoting one into a config makes a literal path that fails only at runtime. `single_sample_arms.py` now refuses on any `${` in a value.
- **The repo's SVShell fixture is mini-test data**: 25 of the 27 gap values are `/inputs/…` container paths (`/inputs/HERVK.sorted.bed.gz`, `/inputs/RGP_1153_3.cram`, `["/inputs/hg38.SimpRep…"]`). `svshell_arms.py` refuses them and reports them as unresolved instead of silently shipping wrong inputs.
- **`.dockstore.yml` edits**: YAML accepts a one-element `branches:` list, so deleting `- main` parses fine. Verify by printing **parsed** filter lists and entry counts (32), never by grepping for the value you expect — and never interpolate the expected value into the check's own output string.
- **Build provenance needs two checks, not one**: the `git checkout` line (`HEAD is now at …`) proves the jar, and the `SV_PIPELINE_IMAGE=` build-arg proves the base. The base comes from `dockers.json` *at the built commit*, so an early rebuild can silently inherit v1.1.1 python.
- **A self-deleting GCE VM loses its evidence**: `gcloud compute instances get-serial-port-output` on a `TERMINATED` instance returns nothing (`…is not ready`); a probe VM must stay `RUNNING` until the driver has read the serial log. My `jar_flag_probe.sh` does that, but `gcloud` also returned non-zero *while succeeding* at create, which aborted the driver — so verify by querying the instance rather than trusting the exit status.
- **Dockstore HM versions endpoint** (the only cheap way to ask "is this branch published?"): `GET https://dockstore.org/api/api/ga4gh/v2/tools/%23workflow%2F<repo-path>/versions`.

### 29.7 Deliverables — file → what changed

| file | change |
|---|---|
| `<gatk-sv-checkout>/dockerfiles/sv-shell/Dockerfile` | GATK pin → `36c8568c37…` + comment tying the pin to the flags `src/sv_shell` passes (on the PR) |
| `<gatk-sv-checkout>/inputs/values/dockers.json` | `sv-shell` → `…:<branch>--8748f0` (4 keys differ from `main`) |
| `<gatk-sv-checkout>/.github/.dockstore.yml` | `<branch-under-test>` filters now on 7 entries incl. `SingleSamplePipeline`, `SVShell`; `- main` preserved on all 32 |
| `svshell-replay/svshell_arms.py` | **new** — SVShell A/B builder: call-scope keys, refusal on `${` and on `/inputs/` mini-paths, refuses to submit without `--confirm` |
| `svshell-replay/_svshell_probe.py` | **new** — prints required workflow/task input names for one ref (the measurement that corrected the "unlaunchable" claim) |
| `svshell-replay/single_sample_arms.py` | **new** — WDL-pipeline A/B builder (used to run the pair); per-arm images literalised, refuses unknown `${` |
| `svshell-replay/single_sample_extra_inputs.json` | **new** — the 11 GD/annotator inputs the March config lacked, resolved from `inputs/values/{resources_hg38,ref_panel_1kg}.json` |
| `svshell-image-check/jar_flag_probe.sh` | **new** — asks a shipped image's jar which `GenotypeSVs` RD flags it accepts; VM stays up until captured |
| `svshell-image-check/svshell_image_check_v2.sh` | **new** — v1 + a jar-flag gate that **fails** the check if the pin is stale |
| `runs_trackb/NOTES.md` | 3 new sections: jar-pin closure, A/B failure + #965 mechanism table, the RETRACTED §2 correction |
| `CHECKPOINT.md` | this §29 |
| `CLAUDE.md` | new Status section; corrected launchability bullet; Terra method-config facts; sv-shell two-rebuilds/base-image fact; branch/image coordinates |

### 29.8 Mutation ledger (this session)

| mutation | where | reversible? | verify / undo |
|---|---|---|---|
| force-push `<branch-under-test>` (3×: pin bump, yml fix, dockstore+rebase) | `broadinstitute/gatk-sv` | yes (prior tips tagged) | `git ls-remote origin refs/heads/<branch-under-test>` |
| `add-tag` prod `sv-shell:<branch>--8748f0` | `us.gcr.io/<project>/gatk-sv` | yes | `gcloud container images describe`; delete = undo |
| PR #961 body rewritten | GitHub | yes (body files in `/tmp/pr_body3.md`) | `gh pr view 961 --json body` |
| issue #965 created | GitHub | yes | `gh issue close 965` |
| 2 method configs created | my workspace `…-<branch>` | yes | `fapi.delete_workspace_config` |
| 2 submissions started, 1 aborted | my workspace `…-<branch>` | no (spend is spend: $4.31 final + ~$1.46 and creeping) | `twatch.py status` |
| 2 objects copied | `gs://<workspace-bucket>…/genotyping_tables/` | yes | `gsutil rm` |
| attempted entity-attribute merge on `sample/NA12878` | my workspace | **n/a — rejected by API** (`sample_id` reserved), so nothing changed | GET the entity, confirm attributes unchanged |
| a colleague's workspaces / buckets | — | **none**: read-only | — |
| build VMs (`gsv-<branch>--8748f0bb`, `svchk-*`, `jarprobe-*`) | `<gcp-project>` | n/a | all deleted/self-deleted; `jarprobe-343778` deleted explicitly |

### 29.9 Open items / next steps

- [ ] **Make the SVShell A/B launchable** (`prep` → `UNRESOLVED=0`). The 74 unresolved names need real values from canonical public assets: `gs://gatk-sv-resources-public/hg38/v0/sv-resources/…`, `gs://gatk-sv-ref-panel-1kg-v1-1/…`, plus a real `bam_or_cram_file`. Do **not** reuse a colleague's captured `fc-*` bucket paths (`gs://<workspace-bucket>…` is his workspace bucket — my pet SA has no claim on it at run time); the repo's `dragen` fixture is mini data. Best cross-check: the March-era config in `…-<branch>` (its 96 `workspace.*` attributes resolve there, and those attrs hold real paths) — read them and map them onto `SVShell.RunSVShell.<name>`.
- [ ] **Arm B's `genotyping_rd_pesr_table` is currently a PLACEHOLDER** in `svshell_arms.py` (pointed at the same ref-panel file as the depth table). That is wrong and must be replaced with a real PESR-side RD table (a `TrainSVGenotyping` output, e.g. `gs://<workspace-bucket>…/call-TrainSVGenotyping/attempt-2/all_samples.rd_pesr_geno_params.tsv`) before any run, or the branch arm measures nothing.
- [ ] Confirm arm B reached `Aborted` (29.0 saw it still `Running` after the 204).
- [ ] **SVShell end-to-end run is still the missing evidence** for "did we break `sv-shell`". Nothing in Track A/B/#961 exercises the image.
- [ ] Admin-merge #961 (needs bypass of `readonly_check.yaml` `Verify`, which fails on `dockers.json` by design).
- [ ] After merge: replace the personal image pins on `main` with CI-built release images at the next docker bump.
- [ ] AoU 516-sample canary with the 3 updated pins — still the only real test of the production cohort path.
- [ ] Owner decisions unchanged: `pe_count` 4 vs 8 (knife-edge 0.022% margin), het→hom drift acceptability, observability bundle (`GenotypeBatchMetrics` unwired, no `TrainSVGenotyping` logs/PE diagnostics), `GenotypeSVs` VCF representation (INS `END==POS`, resolved `REF`, BNDs), the 10 excluded SVCluster low-mem commits, `GenotypeSVs.java:151 pesrExclusionIntervalsPath` parsed-and-ignored.
- [ ] `session-handoff` itself: §29 written; §28's ledger stays as it was (no changes needed to it).
