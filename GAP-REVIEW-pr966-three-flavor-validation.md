# What the testkit needs to validate one gatk-sv PR across all three pipeline flavors

**Question.** gatk-sv PR #966 (`mw_fix_single_sample_blocking`, head `01107996`) changes code that runs
in all three flavors of the pipeline: cohort/joint WDL, single-sample WDL, and sv-shell. Can the
testkit validate that PR in all three? If not, what is missing?

**Answer.** No. The testkit can check the PR statically and can monitor and diff runs, but it can only
*launch* cohort steps 06–10. None of the code this PR changes runs in those steps. Every run needed to
validate the PR was, or would be, assembled by hand against the Terra API.

Written 2026-10-02 from a session that reviewed handoff `docs/handoff/004-single-sample-baf-fix-pr966.md`.
Each fact below was measured in that session; the section that measured it is cited.

---

## 1. What the PR changes and where it runs

| change | file(s) | flavor(s) it reaches | entry point that exercises it |
|---|---|---|---|
| `ConcatBaf`: GATK jar, `--sequence-dictionary`, merge panel BAF | `wdl/GATKSVPipelineSingleSample.wdl` | single-sample only | `GATKSVPipelineSingleSample` (sample entity) |
| `IntegrateGDVcf`: optional `sample_id`; hom-ref drop + `EVIDENCE=RD` only when set | `wdl/IntegrateGDVcf.wdl` | single-sample (set) **and** cohort (unset) | `GATKSVPipelineSingleSample`; standalone `IntegrateGDVcf` (sample_set_set, cohort config `inputs/templates/terra_workspaces/cohort_mode/workflow_configurations/IntegrateGDVcf.json.tmpl`) |
| SIGPIPE `\|\| true` on header probes | `CollectCoverage.wdl::CondenseReadCounts`, `WGD.wdl::BuildWGDMatrix`, `TasksMakeCohortVcf.wdl::ConcatBeds`, `Vapor.wdl::ConcatVapor`, `RefineComplexVariants.wdl::SplitCpxCtx`, `AnnotateExternalAFPerShard.wdl::SplitRefBed` | cohort + single-sample (shared tasks) | GatherBatchEvidence, TrainGCNV, CallGenomicDisorderCNVs, EvidenceQC/WGD, MainVcfQc, CollectQcVcfWide, Vapor, RefineComplexVariants, ShardedAnnotateVcf |
| `svtk` `pkg_resources` TODO (import restored) | `src/svtk/svtk/__init__.py` | every flavor that calls `svtk` inside `sv-pipeline` / `sv-shell` images | any; the image must be rebuilt for it to matter |
| workspace template keys removed | `inputs/templates/terra_workspaces/single_sample/workspace.tsv.tmpl` | single-sample Terra workspace | `build_inputs.py` render |
| Dockstore branch filters (temporary) | `.github/.dockstore.yml` | none at runtime | how the branch got onto Terra; `Verify` CI rejects it |

sv-shell (`src/sv_shell/`) has no GD stage, and its scripts already guard the same SIGPIPE pipelines
(`wgd.sh:49`, `annotate_vcf.sh:98`, `refine_complex_variants.sh:97`). So sv-shell is reached only
through the image (`svtk`).

The table above was built by hand with `grep`. The testkit has a tool for this question (`checks/wdl_reach.py`),
but `gsvtk check --reach` does not pass it `--dir`/`--target`, so it only printed its usage line (gap G1).

## 2. What was validated, and with what

| flavor | done | by testkit? | still open |
|---|---|---|---|
| single-sample | Terra run `58cd5f7b` at `7fbf1171`: BAF matrix 157 samples (was 1); GD 32,970 rows, 948 panel `cn_state` shifts, 0 for NA12878; final VCF 9,467 records with identical bodies. All re-measured this session from GCS | **no**: submitted by hand; compared by an ad-hoc python/`diff` script | run used image `sv-pipeline:mwfix-9f2ebe25`, which predates the branch's `svtk` revert (`d81a271e`); WDL changed again in `01107996` |
| cohort | womtool (CI's `scripts/test/validate.sh -t`, 29/29 Terra JSONs incl. cohort IntegrateGDVcf); miniwdl; `gsvtk check` diff `7fbf1171`→`01107996` = zero delta | static only | **zero executions** of any changed cohort task |
| sv-shell | `gsvtk check` contract and jq-plumbing scans show zero delta; CI built the `sv-shell` image | static only | **zero executions** |

### The miss that shows the gap

At `7fbf1171`, `gsvtk check --wf IntegrateGDVcf` reported the gate clean: exit 0, 0 IncompleteCall,
0 stale bindings. CI rejected the same commit with
`Required workflow input 'IntegrateGDVcf.sample_id' not specified`. The commit had made `sample_id`
required, and the cohort config never sets it. The testkit runs miniwdl but not womtool on the rendered
test/Terra input JSONs, so it could not see that. The previous agent's handoff then proposed "add
`sample_id` to the test template", which would have turned CI green and left cohort mode broken
(it would also filter a multi-sample VCF on one sample's GT). A launchability check that matches CI
would have caught it before push (gap G2).

## 3. Gaps, in the order they block validation

### Static

- **G1. `check --reach` is mis-wired.** The `check` command never forwards `--dir`/`--target` to
  `wdl_reach.py`. Needed: given `base..head`, list the changed tasks → calling workflows → flavor →
  launchable entry points, and also map `dockerfiles/**` and `src/**` changes → images → every task
  that uses that docker. Section 1 above is the output this should produce.
- **G2. `check` doesn't match CI's WDL gate.** Run `scripts/inputs/build_default_inputs.sh` +
  `scripts/test/validate.sh -t -d <tree> -j $WOMTOOL_JAR` at the target ref, and report the delta
  against the base ref. `tools` already detects `WOMTOOL_JAR`; `check` doesn't use it.
- **G3. `check --help` is refused** as an unknown flag. Minor.

### Images

- **G4. CI-built images are not published.** On PR runs `Publish` is skipped:
  `sv-pipeline:2026-10-02-v1.1.1-01107996` and `sv-shell:…-01107996` do not exist in
  `us.gcr.io/<registry>/gatk-sv` (`gcloud container images describe` → not found). Any
  executed test needs `gsvtk build <branch> --confirm` first. Needed: a build mode that builds
  only the images the reach answer (G1) names, prints the pushed digests, and writes them where the
  launch step (G5) reads image overrides, so a run cannot silently use a pre-change image.

### Launch

- **G5. No general single-workflow launcher.** `terra/batch_rerun_step.py` launches only steps 06–10
  (`steps.py`), and single-sample / participant-rooted modules are excluded by design
  (`batch_rerun_step.py:20`, `docs/module-profiles.md` §11). Needed: one tool that submits **any
  Dockstore-published workflow** with:
  - workflow name + version (branch), not a `.wdl` path (Dockstore keys on the workflow *name*; see
    `docs/terra-head-to-head.md` §8 for the `methodRepoMethod` shape);
  - any root entity type: `sample` (single-sample, SVShell), `sample_set` (GatherBatchEvidence, etc.),
    `sample_set_set` (IntegrateGDVcf cohort), or none;
  - `--image KEY=REF` overrides as quoted literals (the existing rerun tool already does this right);
  - explicit `useCallCache` (single-sample on Terra needs it **off** when inputs cross workspaces;
    `batch_configs.py:215-219`);
  - the existing `show` → `create` → `validate` → `submit --confirm` gating and the
    `--allow-shared-target` guard;
  - the 004 API fixes: entity fields at the **top level** of the submission POST; `allRepos=true` on
    config listings; the single-workspace GET nests under `workspace`.
- **G6. Getting an unmerged branch onto Terra without dirtying the PR.** Today the only route is
  adding the branch to `.github/.dockstore.yml` filters. That edits a read-only file, so `Verify`
  fails and the edit must be reverted before merge. Needed: a documented, scripted route that leaves
  the PR untouched. For example, a throwaway sidecar branch (`<pr-branch>-dockstore`) = PR head + the
  `.dockstore.yml` filter, rebuilt on each PR push. Or an Agora/method-repo upload of the flattened
  WDL (`terra/wdl_flat.py` already flattens). Say which, and what each costs in provenance.
- **G7. Task-level replay of a changed task on real inputs.** The SIGPIPE guards only fail on
  *large* streams. Running a whole cohort module to exercise one header probe wastes compute. Needed: render one
  task's `command` block from WDL at a ref, with inputs taken from a finished run's metadata, and run
  it inside a named image on a VM. `check image run_in_image` boots the VM, and
  `batch_peek.py --task` already reads a task's rendered script. Join them. Prefer this over
  workflow runs for all six SIGPIPE tasks.
- **G8. sv-shell execution.** Run `src/sv_shell/single_sample_pipeline.sh` with a sample inputs
  JSON in the branch's `sv-shell` image, either via `SVShell.wdl` on Terra (needs G5) or directly on a VM
  (extends G7). The contract check already covers the static side.

### Baselines

- **G9. A frozen baseline for every flavor.** The freeze loop pins inputs by crc32c+size for the
  cohort chain only, and it cannot write a participant entity (§11 q3). Needed, one per flavor:
  - single-sample: NA12878 on `main` with the current release images (submission id, workflow id,
    pinned inputs, output paths);
  - cohort: upstream outputs that exist for steps *outside* 06–10, i.e. GatherBatchEvidence inputs,
    and a sample_set_set holding `gd_output_tarball` + `ploidy_table` for IntegrateGDVcf;
  - sv-shell: one completed NA12878 run.

### Compare

- **G10. Output location from metadata, not listing.** `submission.outputFiles` is empty on
  finished submissions, and `gsutil ls -R` over a call-cached tree did not finish in 300 s (004).
  Needed: `fetch_outputs.py` resolving outputs by name from both arms' metadata, including
  `cacheCopy/` paths.
- **G11. Comparators this PR needed and the testkit doesn't ship:**
  - VCF record-body equality that ignores volatile header lines (`##bcftools_*Command` carry `Date=`
    and temp paths, which also change the gz size and md5);
  - GD calls table A/B keyed on `(sample, GD_ID)`: row/key sets, `cn_state` changes, `cn_probabilities`
    changes, case-sample changes reported on their own;
  - evidence-matrix sample census (distinct values in the sample column of a BAF/SD/PE/SR table,
    streamed, since these are ~1.8 GB).
  The script that produced the PR's numbers is in `docs/handoff/004-…` §"Resume here". Promote
  it rather than rewriting it.

## 4. Acceptance: the PR #966 test plan these features must be able to run

Each row should be one testkit invocation (plus `--confirm` where it spends), with a pass condition
the tool prints rather than one a person decides.

| # | flavor | what | arm A (baseline) | arm B | pass condition |
|---|---|---|---|---|---|
| 1 | single-sample | full `GATKSVPipelineSingleSample` on NA12878 | `58cd5f7b` (or a G9 `main` baseline) | `01107996` + images built from it (G4) | final VCF record bodies identical to `58cd5f7b`; BAF census 157; GD case `cn_state` changes 0 |
| 2 | cohort | standalone `IntegrateGDVcf`, `sample_id` unset | `main` | `01107996` | output VCF identical to `main` (the integrator's VCF, no `.filtered` file) |
| 3 | cohort | the six SIGPIPE tasks, task-level replay on the largest real inputs available (G7) | `main` @ the old line, to show `rc=141` happens | `01107996` | rc 0, identical outputs, and the replay must reproduce 141 on arm A or it proves nothing |
| 4 | sv-shell | `single_sample_pipeline.sh` NA12878 in the branch `sv-shell` image (G8) | a G9 sv-shell baseline | `01107996` image | completes; `svtk` steps succeed; final VCF matches baseline |
| 5 | all | static | `7fbf1171` / `main` | `01107996` | G1 reach output equals §1's table; G2 = CI's WOMtool result |

Row 3's pass condition matters: a guard is only shown to work if the unguarded line visibly fails
on the same input.

## 5. Not in scope here

- Showing that the BAF fix *improves* calls (needs a sample with a mosaic/partial CNV where BAF
  decides the call). That is a science question, not a testkit capability.
- The `Test Images Build` flake (`rm: cannot remove '/root/.gradle…': Directory not empty` in
  `dockerfiles/sv-shell/Dockerfile` `gatk_builder`; failed twice on `7fbf1171`, passed on
  `01107996` with the same Dockerfile). This is gatk-sv's problem, not the testkit's.
