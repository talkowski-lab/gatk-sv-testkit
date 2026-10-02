# Head-to-head on Terra: a branch against a real baseline run

The question this answers: **did my change alter the pipeline's output, and if so where?**

Comparing two whole pipeline runs is usually meaningless: different inputs, different
reference panels, different tags, and the differences that matter hide inside the differences
that don't. So the shape here is: **freeze one baseline's inputs, rerun only the stage you
changed, compare that stage's output against the baseline's**, with every input pinned to a
coordinate that cannot move.

```
 recon            understand the baseline and your account          read-only
   ↓
 freeze           copy its inputs + outputs into YOUR bucket        write-once, server-side
   ↓
 configs          build method configs that point at your branch    POST, then never touched
   ↓
 gate             prove the inputs fit your WDL                     read-only, local
   ↓
 run  →  status   launch, wait, price                                costs money
   ↓
 fetch → compare  pull both sides' callsets and diff them           read-only
```

Set up once: [`setup.md`](setup.md), then `testkit.env` with `GSVTK_PROJECT`,
`GSVTK_TERRA_NAMESPACE`/`_WORKSPACE` (yours), `GSVTK_BRANCH`, `GSVTK_IMAGE_REPO`
([docker builds](docker-builds.md) to make the image it will use).

## Contents

* [1. Recon (read-only, run this first)](#1-recon-read-only-run-this-first)
* [2. Freeze the baseline](#2-freeze-the-baseline)
* [3. Method configs](#3-method-configs)
* [4. Gate before you submit](#4-gate-before-you-submit)
* [5. Run, wait, price](#5-run-wait-price)
* [6. Fetch and compare](#6-fetch-and-compare)
* [7. If the numbers moved](#7-if-the-numbers-moved)
* [Cost and blast radius](#cost-and-blast-radius)
* [8. API edges that bite, with the exact text each produces](#8-api-edges-that-bite-with-the-exact-text-each-produces)
* [9. Reading one task's artifacts, and fetching an output by its name](#9-reading-one-tasks-artifacts-and-fetching-an-output-by-its-name)

## 1. Recon (read-only, run this first)

```bash
python terra/recon.py
```

Identity, billing projects you can charge, workspaces you can see, a bounded sample of the
baseline workspace's data model, its method configs, and recent submissions with per-workflow
runnable names. Writes only under `GSVTK_WORK/recon/`.

To look at your own sandbox instead:

```bash
python terra/recon.py --ns "$GSVTK_TERRA_NAMESPACE" --ws "$GSVTK_TERRA_WORKSPACE"
```

## 2. Freeze the baseline

Two separate things get pinned, and conflating them is how a comparison silently becomes invalid:

```bash
python terra/batch_freeze.py plan                      # costs nothing, prints the whole plan
python terra/batch_freeze.py copy                      # dry run: prints N objects, GiB, destination
python terra/batch_freeze.py copy --write              # server-side gs://→gs:// into YOUR bucket
python terra/batch_freeze.py verify                    # re-crc32cs both sides; nonzero on mismatch
python terra/batch_freeze.py attrs --write             # publish coordinates as new attributes
```

Three gates on this path, because it is the one that both spends money and can silently corrupt a
comparison:

* **`copy` and `attrs` need `--write`.** Without it they print and return. `copy` is not cheap just
  because it is server-side: tens of GiB start accruing storage cost the moment they exist.
* **Both refuse the shared baseline workspace.** Writing your frozen copies or your attributes onto
  the workspace everyone reads from is a change to other people's baseline; the tools refuse unless
  you pass `--allow-shared-target` on purpose.
* **`attrs --write` refuses unless a PASSED verify is on record.** `verify` writes
  `"verified": true|false` (plus the mismatching objects) into the manifest, and `attrs` requires that
  verdict to exist and be true. Absent, unreadable and failed are all refused: the test used to be
  `verified is False`, so "verify never ran" (and "manifest written before that key existed")
  published frozen paths with nothing behind them. `--allow-unverified` is the explicit escape, and it
  prints that it took it. Publishing a frozen path nobody confirmed is how a head-to-head ends up
  running on inputs that are not the baseline.

Frozen object names are the source basename, **except** when two *different* source objects share a
basename (this model does, e.g. two batches each holding `merged_pe.out`): those freeze as
`<attribute>__<basename>`. Without that, both attributes end up pointing at one object, the last
writer wins, and one of the inputs steps 06/07 read is quietly the wrong file.

**Object coordinates.** The baseline's input files get copied into your workspace bucket with
their **names, `crc32c` and byte size** recorded in
`$GSVTK_WORK/manifests/baseline_frozen_inputs.json`. Not referenced, copied: your sandbox has its
own Google project, and Cromwell localizes `gs://` with that project's pet service account, which
cannot read the baseline's bucket. Copies also survive deletion of the source, and `gsutil cp`
preserves `crc32c`, so a `verify` match proves the bytes you compared are the bytes you will
compare next time.

**Workspace attributes.** Some inputs arrive as workspace-level attributes that cannot be
inferred from the workflow output, because the Cromwell `root` is an ephemeral `fc-*` bucket
that Terra deletes. Those get read explicitly and copied too.

`verify` is the honest one: it re-reads the live objects and fails if the published attributes
or the bundle contents ever drift.

## 3. Method configs

```bash
python terra/batch_configs.py show                    # print every resolved input binding, no POST
python terra/batch_configs.py check --against main    # those keys vs that ref's WDL, offline
python terra/batch_configs.py create                  # POST/overwrite the configs in YOUR workspace
python terra/batch_configs.py validate                # Terra-side typecheck of the Dockstore WDL
# and, for the rerun path, the same two things with the same guard, for any step of the chain:
python terra/batch_rerun_step.py --step 08 show
python terra/batch_rerun_step.py --step 08 create --confirm [--drop-branch-only-inputs]
```

**`--step` is the whole story of that command, so the tool says what it resolved to.** The step decides
the config name (`<NN>-<Workflow>-rerun`), the input-key prefix, the Dockstore method path and the root
entity, and every one of those comes from `terra/steps.py` — the same map `batch_check_inputs.py`,
`batch_save_metadata.py` and `batch_status.py` read. Until it did, the rerun tool held five
step-10-shaped literals of its own, and asking it for step 08 got you `10-GenotypeBatch` with no
complaint. Every mode now starts by naming what it picked. Real output, from a profile holding only
placeholders, offline (`show` mutates nothing):

```text
$ python terra/batch_rerun_step.py --step 09 show
[rerun] step 09  workflow MergeBatchSites  config 09-MergeBatchSites-rerun
        root entity sample_set_set  (row: UNRESOLVED: pass --entity <name>)
        Dockstore github.com/broadinstitute/gatk-sv/MergeBatchSites @ your-branch-under-test -- the ref the binding check grades too
        reads chain outputs of: 06-GenerateBatchMetrics, 08-FilterBatchSamples
        prove those attributes exist first: python terra/batch_check_inputs.py --step 09   (show does not read your workspace)
image inputs are not fully pinned -- a submit with these runs whatever the workspace
  attribute points at today, which is exactly what this tool exists to prevent:
    unpinned:  MergeBatchSites.gatk_docker
    unpinned:  MergeBatchSites.sv_base_mini_docker
    unpinned:  MergeBatchSites.sv_pipeline_docker
```

That is `show` refusing to print a body it would not submit, and it is the point of the third line: the
upstream attributes a mid-chain step reads are derived from `batch_configs`' own `outputs` maps, so a
rerun of 09 knows it consumes 06's and 08's output and names the command that can prove those
attributes are in your workspace. `show` cannot see your workspace and does not claim to.

The last five steps of the batch chain are 06 GenerateBatchMetrics, 07 FilterBatchSites, 08
FilterBatchSamples, 09 MergeBatchSites, 10 GenotypeBatch, and `--step` defaults to 10. Single-sample /
participant-rooted modules are **not** rerunnable here, by decision rather than by omission: the freeze
loop cannot write a participant entity at all (docs/module-profiles.md §11 q3), so there is no frozen
row for such a step to read — a `--step` that accepted them would be a claim nothing can check.

The configs are generated from one table, so a binding cannot drift between steps. Each config
binds the frozen inputs plus **the image you built** plus the Dockstore tag for the rest of the
chain.

**The input maps are a snapshot of one branch's WDL signature; `GSVTK_BRANCH` only chooses the
Dockstore URL.** Point the URL at a ref the maps were not written against and they carry keys that
ref never declared, which Rawls rejects as an **extra input at submission**, after `create`
succeeded, so the workspace holds a config that looks fine until someone submits it. A ref that
Dockstore never published fails earlier with `Cannot get dockstore://... from method repo`: one
mismatch, two symptoms, and the 404 tends to arrive first and absorb the attention.

`check` is the offline form of `validate`: it reads `wdl/` out of your own checkout with `git
archive` and parses the workflow with miniwdl, so it needs neither the ref to be published nor a
Terra target, which is why `create` and `validate` run it before doing anything else and refuse on
findings. What it reports:

| Finding | Means |
|---|---|
| `EXTRA <WF>.<input>` | the map binds a key this ref does not declare. Known branch-only keys are labelled as such (the table beside `CONFIGS`), because "exists on the branch under test" and "never seen" need different fixes |
| `MISSING <WF>.<input>` | the ref **requires** it and nothing binds it: no default to fall back on, fails as a missing input |
| `CANNOT CHECK` | the workflow file is not in that tree or miniwdl could not load it. Counted as a finding: a skipped comparison is not a pass |
| pre-check `SKIPPED` from `create` | no ref or no checkout, so the comparison never ran. Printed, never silent |

`--allow-unknown-inputs` posts anyway and prints that it was taken. Measured against this repo's
maps (redacted per this repo's own audit rule, which treats a person-named branch as an internal
identifier, see [config.md](config.md)): `<branch-under-test>` 0 of 64 rejected, `origin/main` 1
(`10-GenotypeBatch.training_vcf`, a branch-only input), `v1.1.1` 56 findings; those maps were never
a `v1.1.1` shape. Run `check` yourself to reproduce all three; the numbers are three commands.

**The guard is on the rerun path too, and that mattered more than it looks.**
`terra/batch_rerun_step.py` builds its config with `from batch_configs import body`: the builder,,
not the guard, so when the pre-check landed only in `batch_configs.create`/`validate`, the one path
that actually submits stayed open: create returned 200, submit returned 400, and Terra's own
typecheck was the thing that named the key. `create`, `validate` and `submit` each call it now. Since
rerun's Dockstore pin is `GSV_WDL_VERSION`, which can differ from `GSVTK_BRANCH`, what it grades is
the pin (the ref that config will really run) and it prints which ref it compared.

**Want the other ref's shape? Ask for it by name.** `--drop-branch-only-inputs` removes
known-branch-only bindings the target ref does not declare, so the map fits the ref you pointed at.
It is deliberately not automatic, because pruning a binding does not fix anything: it selects a
different pipeline: main's `GenotypeBatch` trains PE/SR from `vcf`, the branch from a separate training VCF.
So it drops nothing against a ref it cannot read (unverified is not evidence of absence). It prints
each dropped key to **stderr** so `show | jq` stays valid JSON, and it prints the semantic
consequence plus the ref it compared. If you meant to run your own branch, unset `GSVTK_BRANCH`
instead of reaching for this flag.

The guard grades the **pruned** map, not the raw table: `check_maps(..., drop=...)` applies the same
`BRANCH_ONLY_INPUTS` rule `_adapt_inputs()` uses. Before that, `show --drop-branch-only-inputs`
printed the 16-key body Rawls accepts while `create` on the same command line refused to POST it and
named the key it had just dropped. The flag is still not a blindfold, a key outside the table is
reported `EXTRA` with the flag set.

**…and it grades the one ref that config runs.** `--against` belongs to `check`, and a command that
POSTS refuses it: `create` and `validate` compare the ref their own Dockstore pin names
(`GSVTK_BRANCH`, or `GSV_WDL_VERSION` on a rerun), because that is the WDL Terra will actually read.
Letting you pick a different one was worse than a wrong pass, with `--drop-branch-only-inputs` the
guard pruned the key *there* and then posted the map built from the branch, printing `DROPPED … this
is what body() posts` about a body it had not built. To post another ref's shape, point
`GSVTK_BRANCH` at it (that is what pins Dockstore there); to see that ref's findings without posting
anything, `check --against <ref> --drop-branch-only-inputs`. `probe_fixes.py`'s `drop_flag_guard` pins
both halves, including the body that actually leaves the machine, because "the guard and the body
agree" is a claim about two components and only the POST can settle it.

Two non-obvious details that cost real debugging time when wrong:

- **The root entity differs by step, and it is not what you would guess.** `09-MergeBatchSites`
  is submitted against `sample_set_set`; `06`, `07`, `08` and `10-GenotypeBatch` against
  `sample_set` (`batch_configs.py`'s `rootEntityType` per config is the authority, and it agrees
  with gatk-sv's own `GenotypeBatch.json.tmpl`, which binds `GenotypeBatch.batch` to
  `${this.sample_set_id}`). Submit against the wrong one and the batch-level `this.*` bindings
  resolve to nothing, usually at runtime, after VMs booted.
  `terra/steps.py` carries the same value for the rerun path and `batch_rerun_step.py` refuses a step
  where the two tables disagree, because "which one wins" would decide what runs. Its visible
  consequence: `--step 09 submit` **refuses** unless you pass `--entity <cohort-name>`, because
  `GSVTK_BATCH` names a `sample_set` row and guessing a cohort row is exactly the accepted-submission
  that binds nothing. The refusal comes before the target resolution, so it also sends nothing.
- **The WDL file basename is not always the workflow name.** A Dockstore method path ends in the
  workflow *name*. Measured in a gatk-sv checkout (`git archive <ref> wdl`, then the `workflow`
  declaration inside each file) at a branch head and at `origin/main`: 12 of the 109 WDLs that declare
  a top-level workflow declare a name other than their file's basename — `wdl/DepthClustering.wdl`
  declares `ClusterDepth`, `wdl/PloidyEstimation.wdl` declares `Ploidy`, `wdl/Genotype_2.wdl` declares
  `Regenotype`. All five rerunnable steps were checked individually and each one agrees
  (`wdl/MergeBatchSites.wdl` declares `workflow MergeBatchSites`), which is why the step→workflow map
  can serve as the last path segment for 06→10 — and why a step added to that map whose file and
  workflow differ must carry the workflow **name**, or Rawls answers `Cannot get dockstore://... from
  method repo`.
- **`GSVTK_BATCH` must name a real `sample_set`.** It defaults to `all_samples`, the entity in
  gatk-sv's reference-panel workspace; rename it and every `this.<attr>` binding silently empties.
  It is read by every tool on this path, including `fetch_baseline.py --entity` (which writes the
  manifest) and `stage_inputs.py --attrs` (which reads it back), so a wrong value cannot split the
  freeze and the staging across two different rows. If the row is missing from the manifest,
  `stage_inputs.py` stops with exit 2, naming the rows that exist, instead of selecting nothing and
  exiting 0.
- **A workflow Dockstore does not publish cannot be a method by reference.** `.github/.dockstore.yml`
  carries ~30 named workflows, and anything only ever called as a sub-workflow is not among them,
  while a workspace method is a single descriptor file. `terra/wdl_flat.py` bundles a
  single-workflow closure into one document, and `--check` typechecks the result with miniwdl,
  refusing to report success without it (miniwdl is resolved through `./kit/gsvtk-config miniwdl`,
  so the copy `make setup` put in `./.venv/bin` is found without activating anything). It refuses
  multi-workflow closures on purpose, including `TinyResolve`, whose import `GetShardInputs.wdl`
  declares a workflow of its own, because a document with two workflows has no primary and fails
  at submission looking like a broken WDL. It likewise refuses a closure where two **different**
  files declare the same task/struct name (importing one file twice is fine; it is emitted once):
  one document cannot hold it twice, and picking a winner is a decision about what the pipeline
  runs, not a bundler detail. gatk-sv main has no such collision in any of its 118 closures.
- **A binding is not an expression.** A value Cromwell *evaluates* rather than *reads* can come
  back empty, and the WDL's own default then wins, which is invisible in the create/validate
  path, because both consider the config well-formed. `batch_check_inputs.py` flags bindings that
  contain `~{}` or start like an expression (`select_all`, `read_*`, `{`, `[`) as
  `FAIL expression-shaped bindings`, and exits 1 on any FAIL.

## 4. Gate before you submit

```bash
python terra/batch_check_inputs.py --step 08
WOMTOOL_JAR=/path/to/womtool.jar python terra/batch_check_inputs.py --step 08 --no-sidecars
```

The submission-time analogue of `checks/wdl_gate.sh`, but stronger: womtool typechecks your
actual WDL under a complete inputs object, so a missing or renamed input fails on your laptop in
seconds instead of on step 08 in an hour. The `--no-sidecars` run models the index-less case
that Terra's `localize()` does not supply.

`terra/batch_rerun_step.py` refuses every mode **except `show`** while no image is pinned:
either `--image KEY=REF` per call, or an explicit `GSVTK_IMAGE_REPO`/`GSVTK_GATK_IMAGE_REPO` in the
profile. A value merely *derived* from your project does not count, because "derived from the
project" is not a statement about which code ran. `show` is deliberately allowed unpinned so you
can inspect the body before deciding. Unpinned images are the one class of mistake that otherwise
looks like a successful submission followed by an `ImagePullBackupFailed` on every shard, or
worse, a green run of someone else's code.

**The guard reads the step you named, not a fixed list.** Each step declares its own `*_docker`
inputs, and they are not the same set: 07 declares one (`sv_pipeline_docker`), 08 declares three
including `linux_docker`, and 06/09/10 declare `gatk_docker`, `sv_pipeline_docker` and
`sv_base_mini_docker`. So `--step 07 show` refuses over one key and `--step 08 show` over three, and
the keys it prints are that workflow's (`FilterBatchSamples.linux_docker`, not `GenotypeBatch.*`):
pointing a body at inputs another workflow never declared is the extra-input rejection at submission.
A pinned-but-UNTAGGED path is refused in the same breath — `.../sv-pipeline` with no `:` floats to
whatever is current at pull time, which is not a statement about which code ran.

## 5. Run, wait, price

```bash
python terra/batch_rerun_step.py status
python terra/batch_rerun_step.py submit --confirm     # needs your branch, your image, --confirm
python terra/batch_status.py --wait 08                # poll the newest 08-* submission until terminal
python terra/batch_status.py --costs                  # quick per-submission cost delta
```

What `batch_rerun_step.py` guarantees is about *images*: every `*_docker` input is pinned literally
into the config it POSTs, so a rerun cannot inherit whatever the workspace attribute happened to
point at. Those guarantees are per step now, not per GenotypeBatch: the config name, the input
prefix, the Dockstore URI, the root entity and the submission row all come from `terra/steps.py`, the
submission line names the step, the row and `useCallCache` before the fleet boots, and the metadata it
dumps is named per step (`rerun_submission.09.json`), because five steps sharing one file means a 07
overwrites the 10 record you came to look at. It does **not** re-read live workflow outputs,
`batch_check_inputs.py --step 10` is the tool that checks bindings against the last submission's actual
`inputResolutions`.

### Two arms, one variable

A rerun that changes the WDL ref, *and* the image, *and* reads attributes written by some earlier
branch-era chain answers no question in particular: the changes are confounded and a diff cannot
separate them. If the chain you froze came from a branch that also rewrote a workflow feeding this
step, then the inputs themselves are branch-era artifacts, and running a main-shaped step over them
and comparing to a production baseline mixes three variables at once.

To ask "did my code change the output", leave one variable: same WDL ref on both arms, same frozen
inputs, `sv_pipeline_docker` = the shipped image on one arm and your commit-pinned build on the
other. That is two submissions of one step, and the config machinery above is what makes them
identical apart from one line. Price it after the fact with `terra/batch_cost.py` over the metadata
`batch_save_metadata.py` left behind (`--groups 10-baseline` adds a call-group roll-up, `--json` writes
the artifact) rather than guessing beforehand.

Two ways that design degenerates silently, both cheap to avoid:

- **Both arms write the same entity attributes.** Outputs land in `*<GSVTK_NEW_SUFFIX>` (default
  `_new`), so the second arm overwrites the first and the comparison ends up reading one run against
  itself. Give each arm its own suffix and pass the matching `GSVTK_FROZEN_SUFFIX` to whatever reads
  that arm back; the empty-diff failure mode in [troubleshooting.md](troubleshooting.md) is this
  disagreement between two tools.
- **`useCallCache: true`.** Cromwell reuses call outputs when command and inputs match, which is what
  you want across retries and not what you want between arms if a tag is mutable. Pin both images by
  digest or commit-SHA tag, which is what the pinning guard in this tool already refuses to let you
  skip for `*_docker`.

```bash
python terra/batch_save_metadata.py --outdir "$GSVTK_WORK/metadata"   # discovery, then full dumps
python terra/batch_cost.py --outdir "$GSVTK_WORK/metadata" \
       --groups 10-baseline --groups 10-new
```

Cost is **recomputed from saved Cromwell metadata** rather than quoted from a dashboard, and is
reported in **VM-minutes and job counts**: no rates, no currency, because your billing account is
not mine. The accounting is `sum(vmEndTime - vmStartTime)` over every call record at any nesting
depth (`batch_save_metadata.vm_minutes`), which is deliberately not the root `call start→end` span:
spans are printed as a separate column because they are what produced two bogus headline figures
here. Multiply the VM-minutes by your own per-type price and re-run the arithmetic against the JSON;
that is the point of storing it.

**The chain total and the ratio are refused when the data is incomplete**, because every one of these
forms used to print a tidy wrong number and exit 0:

| What is missing | What the tool now says |
|---|---|
| one step's metadata file on either side | `chain total is PARTIAL: no saved metadata for 06/baseline`, `ratio new/baseline: n/a`, **exit 1**. That step counted as *zero* in the sum, deleting one file once flipped the conclusion from "4.5× cheaper" to "20× more expensive" |
| a sub-workflow whose tree was never expanded (depth cap, older or hand-trimmed file) | `N sub-workflow call(s) have no expanded tree below them -> cost is a FLOOR`. `_missingSubWorkflows` alone was not enough: a truncated tree looks exactly like a complete one, and baseline step 10 read 918 VM-min instead of 1672.8 that way |
| a call with `vmStartTime` but no `vmEndTime` (running or aborted) | `excluded from BOTH minutes and job count`: the job count under-reports too, not just the minutes |
| no step files at all in `--outdir` | says so and exits 1, rather than printing a table of zeros |

So `every step present on both sides, every tree expanded -> these are measurements` is a claim the
tool has checked, and `these are FLOORS, not measurements (fetch_failed=…, unexpanded=…,
half-timestamped=…)` is what an incomplete tree gets called.

## 6. Fetch and compare

```bash
terra/batch_fetch_compare.sh fetch      # read *<new> attrs, gsutil cp both sides
terra/batch_fetch_compare.sh table      # the table differ
terra/batch_fetch_compare.sh profile    # paired gatk-sv-profile run
terra/batch_fetch_compare.sh all --dry-run
```

`fetch` is a no-clobber sync of tens of GB, so keep the scratch under `GSVTK_WORK` on a roomy
disk. `profile` refuses a non-empty output directory unless `--force`.

For **any** other callset pair, use the generic comparators directly, see
[comparators.md](comparators.md).

## 7. If the numbers moved

The discipline that matters: for each differing column, decide which of these it is, and write
the answer next to the number.

1. **a bug in my change**: the interesting case;
2. **an intended behaviour change**: then the baseline number is what must change, and a
   strategy-aware comparator should mark it `STRATEGY`, never diff it as a bare number;
3. **an input difference you did not control**: the comparison is invalid, not the code.

Case 3 is why freezing exists. Case 2 is why [comparators.md](comparators.md) exists: a column
whose inputs are *superset* on one side has no right to be compared as a mean.

## Cost and blast radius

- Free and read-only: `recon`, `freeze plan/verify`, `configs show`, `check_inputs`, `status`,
  `cost`, every `checks/` and `compare/` tool.
- Writes, no compute: `freeze copy`, `freeze attrs --write`, `configs create --confirm`,
  `batch_rerun_step.py create --confirm`. Both `create` paths require `--confirm` because a
  method config with a wrong binding is worse than no config: the next submission reads it, and
  both refuse the shared baseline workspace unless `--allow-shared-target`. Every mode of both
  tools resolves namespace + workspace **before** the first request, so an unset target is exit 4
  naming the profile key rather than a `POST /api/workspaces//methodconfigs` that the edge answers
  with a 405.
- **Spends money:** `submit`. Requires `--confirm`, your `GSVTK_BRANCH` *and* `GSVTK_IMAGE_REPO`,
  and re-reads live outputs first.
- Terra has no per-workspace budget cap. A whole-chain rerun is a fleet of VMs. Prefer
  `batch_rerun_step.py` (single step) over a full rerun, and `--wait` over watching the console.

## 8. API edges that bite, with the exact text each produces

All of these were hit driving real submissions on 2026-09-22…25 (gatk-sv `trio_denovo_single_sample`,
single-sample pipeline, ~$26 of real runs). Each was worth a round trip, none is documented elsewhere
in this repo, and two of them only surface after a run has already spent money.

**Payload shape: rejected at submit time, cheap to find, painful to guess.**

- Config `inputs` keys must be **workflow-qualified**: `GATKSVPipelineSingleSample.dragen_vcf`, not
  `dragen_vcf`. Strip the prefix and the validator reports every single input as extra (observed:
  117 `extraInputs`, 87 missing), a total-misery error message for a one-line cause.
- Submission entity expression is exactly `this`. `this.sample.SM-GN4BI` → HTTP 400.
- Config `outputs` **values** are unqualified: `this.moi_summary`, not
  `this.GATKSVPipelineSingleSample.moi_summary`:
  `400 … Invalid outputs: GATKSVPipelineSingleSample.moi_summary -> Entity references not permitted in
  the middle of output expressions`. The workflow prefix belongs to the key, never to the value.
- `methodRepoMethod.sourceRepo` must be `dockstore`; `github` → `400 Illegal method repo 'github'`.

**Outputs are not validated. Ever.** `create_workspace_config` / `overwrite_workspace_config` return
`extraInputs` / `invalidInputs` / `invalidOutputs`, and `invalidOutputs` is **0 even for an output name
the workflow does not declare**. A stale key then fails at the very end of a finished run:

```
output named GATKSVPipelineSingleSample.final_bed does not exist
```

Cromwell's own root status was `Succeeded`, all 17 outputs existed, and Terra still marked the
workflow `Failed`. Cost of that one key: an entire 20 h / $18.87 run reported as a failure. **Check
the config's output names against the descriptor before submitting**, because the only validator is the run:

```bash
ID='%23workflow%2Fgithub.com%2Fbroadinstitute%2Fgatk-sv%2FSingleSamplePipeline'
curl -s "https://dockstore.org/api/ga4gh/trs/v2/tools/$ID/versions/<branch>/WDL/descriptor" \
  | python3 -c "import json,sys,re; c=json.load(sys.stdin)['content']; i=c.rindex('output {'); print('\n'.join(re.findall(r'^\s*\w+\??\s+(\w+)\s*=', c[i:], re.M)))"
```

**Endpoints that lie by omission** (all observed on a workspace that demonstrably had the data):

- `GET …/submissions` via `firecloud.api.list_submissions` → `[]`. So does `list_workspace_configs`
  for a workspace holding 1 config. Raw REST with a `gcloud auth print-access-token` bearer returns
  the truth. **You no longer hand-curl this:** `terra.submissions()` and `terra.workspace_configs()`
  ask both clients on every call, print `the two endpoints DISAGREE — raw REST GET /api/… answered 2,
  fapi.list_submissions answered 0` when the counts differ (and use raw REST), and **raise naming the
  endpoint** when both answer empty, because an empty listing and an invisible workspace are otherwise
  the same answer. One extra request per listing is the accepted price of detecting the lie.
  `probe_listing_lie` pins it offline; `recon` exits 1 on this answer rather than 0
  (`probe_recon_empty_inventory`). The reason to check after a submit call that raised mid-flight is
  unchanged: prove you did not double-spend.
- Per-workflow metadata is a **cached snapshot**: two fetches 30+ min apart returned byte-identical
  JSON while the scratch bucket proved the run had advanced. It also omits sub-workflow internals:
  top-level `calls` sat at 9 entries while ~200 tasks ran inside `GatherBatchEvidence`. Use
  `?expandSubWorkflows=true` for the real call graph and know it returns **~45 MB**: `curl -o` it and
  print a summary, never into an agent's context.
- For live progress and spend, trust the scratch bucket (`gsutil ls -d …/call-*/`) and the submission's
  `cost` field. `twatch.py --diagnose` answers HTTP **405**; don't route around it by hand. For the
  call-level view without the 45 MB payload, `terra/batch_peek.py` reads non-expanded metadata and
  prints a bounded tally (240 records across 60 calls renders 18 lines) plus the first failure message
  and per-attempt rc values; it refuses a submission it cannot see instead of reporting nothing. Its
  live-Terra path needs credentials and was **not** exercised by this repo's offline gate, the
  guards, the tally, and the exit codes were proven against metadata fixtures.
- Finished submissions store their config under a per-submission snapshot name
  (`single-sample-trio-a0e10b99_B0EJFlC5SLk`). Those are not extra configs; don't clean them up.

**Two WDL-side facts that cost real runs, because the backend is not a filesystem.**

- A `write_lines()`/`write_tsv()`/`write_json()` at **workflow** scope cannot be materialized on
  PAPIv2: `Could not build the path "write_lines_….tmp". It may refer to a filesystem not supported
  by this instance of Cromwell. Supported filesystems are: DRS, Google Cloud Storage, HTTP.` It checks
  clean in miniwdl and runs fine on local Cromwell. Materialize inside a task command instead.
- Every `File` input is **downloaded** into the task's working directory before the command runs, even
  when the command only asks `defined(the_file)`. Two whole-genome parental CRAMs onto a task asking
  for `local-disk 10 HDD` = a job that runs ~20 min, writes **no stdout/stderr at all**, and dies with
  `The job was stopped before the command finished`, which reads like an image-pull failure until you
  `gsutil cat` the task's `gcs_localization.sh` and see the CRAMs in it. Pass a `Boolean` computed at
  the call site when you only need presence.

## 9. Reading one task's artifacts, and fetching an output by its name

Two `curl`-shaped jobs that every review hand-rolled, now tools. Both are provable offline because
both take an injected transport, and both say so when the cloud half is not what was exercised.

**`terra/batch_peek.py`** is the bounded call-level peek (the 1.4 KB scratch script five reviews
rewrote independently). The tally and the `rc`-object liveness signal are the running-monitor half;
`--task` is the artifact half, and it reads the artifact rather than printing where it lives:

```bash
./terra/batch_peek.py --metadata "$GSVTK_WORK"/metadata/run1.*.json \
    --task GatherBatchEvidence --tail 25 --no-scratch
```

`--metadata` takes a local Cromwell dump, so the whole path runs with no network and no credentials;
which is also how `--selftest` proves it (54 assertions). What it prints: the `rc` read from the file,
the **tail** of `stderr` with the drop counted (`TAIL 25 shown, first 375 not printed`), the **rendered
`script`** block the backend actually executed, and which `attempt-N` directories exist. The attempt
layout is what turns "unstable image" into "preempted": `rc=141` under `attempt-1` and `rc=0` under
`attempt-2` says so without opening a log. `--attempt 9` against a call dir that has no `attempt-9`
is REFUSED by name. It will not fall back to a neighbour's log and let you quote the wrong attempt.
A withheld `stdout` is not a failure; a thing the page tried to show and could not read is, and the
exit code says 1. `--shard` refuses a shard the call does not have for the same reason.

**`terra/fetch_outputs.py`** takes the **workflow output name** and resolves it in Cromwell metadata,
which is where the paths actually were (the Terra entity attributes stayed empty; see
`GAP-REVIEW-manta-tloc.md` §1). Never a bucket path typed by hand:

```bash
./terra/fetch_outputs.py --metadata "$GSVTK_WORK"/metadata/run1.json \
    --output GATKSVPipelineSingleSample.svVCF --dry-run
```

Three answers, deliberately not conflated: the name was **never declared** (exit 3, the declared list
printed), it was **declared and carries no value** (exit 4, saying out loud that this is a different
question), or it is **present** (exit 0, sha256 into an appending manifest keyed by output name).
`--verify` re-hashes what is on disk and exits 6 on drift. `--dry-run` prints the exact
`gsutil -m cp -n` line without running it, and `--link-dir` hard-links instead of copying.

`--all`, `--subdir` and `--manifest` exist in `--help` and are **not** exercised by this repo's
offline gate (the gate drives `--metadata`, `--output`, `--dry-run`, `--link-dir`, `--verify`); the
copy itself goes through one injected seam, so the command is proven as text and the bytes that move
in `--selftest` come from a local capture. The real-cloud copy is named in the file header as the
path no check in this repo has executed.
