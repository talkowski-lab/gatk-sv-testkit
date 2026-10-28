# Head-to-head on Terra: a branch against a real baseline run

This page answers one question: did my change alter the pipeline's output, and if so where?

Comparing two whole pipeline runs rarely answers it. Inputs, reference panels and image tags all
differ, and the differences you care about hide among the ones you don't. The workflow here is to
freeze one baseline run's inputs, rerun only the stage you changed against those frozen inputs, and
compare that stage's output with the baseline's.

```
 recon            look at the baseline and your account          read-only
   ↓
 freeze           copy its inputs into YOUR bucket               writes (--write)
   ↓
 configs          method configs pointing at your branch         POSTs (--confirm)
   ↓
 gate             check the inputs fit your WDL                  read-only
   ↓
 run → status     launch, wait, price                            costs money (--confirm)
   ↓
 fetch → compare  pull both sides' outputs and diff them         read-only, large downloads
```

Before starting, follow [setup.md](setup.md) and set at least `GSVTK_PROJECT`,
`GSVTK_TERRA_NAMESPACE`, `GSVTK_TERRA_WORKSPACE` (your sandbox workspace) and `GSVTK_BRANCH` in
`testkit.env` ([config.md](config.md)). Build the image you will test with
[docker-builds.md](docker-builds.md). [test-a-branch.md](test-a-branch.md) is the short, ordered
checklist version of this page.

Every tool here also has a `./gsvtk terra <mode>` alias (`./gsvtk terra --help` lists them). Read-only
modes run as-is. Modes that write or spend need `--confirm` on the `gsvtk` command line as well as
whatever the tool itself requires, and all of them are refused under `GSVTK_READ_ONLY=1`.

## Contents

* [1. Recon (read-only, run this first)](#1-recon-read-only-run-this-first)
* [2. Freeze the baseline](#2-freeze-the-baseline)
* [3. Method configs](#3-method-configs)
* [4. Gate before you submit](#4-gate-before-you-submit)
* [5. Run, wait, price](#5-run-wait-price)
* [6. Fetch and compare](#6-fetch-and-compare)
* [7. If the numbers moved](#7-if-the-numbers-moved)
* [Cost and blast radius](#cost-and-blast-radius)
* [8. Terra API behavior to know about](#8-terra-api-behavior-to-know-about)
* [9. Reading one task's artifacts, and fetching an output by its name](#9-reading-one-tasks-artifacts-and-fetching-an-output-by-its-name)

## 1. Recon (read-only, run this first)

```bash
python terra/recon.py
python terra/recon.py --ns "$GSVTK_TERRA_NAMESPACE" --ws "$GSVTK_TERRA_WORKSPACE"   # your sandbox instead
```

Prints your identity (redacted unless `--show-identity`), the billing projects you can charge, the
workspaces you can see, a bounded sample of the target workspace's data model, its method configs,
and recent submissions with their full submission ids. It writes only under `$GSVTK_WORK/recon/`.
It exits 1 if any section failed (including a submission or config listing that came back empty,
which is more often a visibility problem than an empty workspace; see
[§8](#8-terra-api-behavior-to-know-about)), so a partial report is never mistaken for a clean one.

## 2. Freeze the baseline

```bash
python terra/batch_freeze.py plan            # read-only: what would be copied
python terra/batch_freeze.py copy            # dry run: object count, GiB, destination
python terra/batch_freeze.py copy --write    # server-side gs:// -> gs:// copy into your bucket
python terra/batch_freeze.py verify          # re-check crc32c on both sides; nonzero on mismatch
python terra/batch_freeze.py attrs --write   # publish the frozen paths as <attr>_frz attributes
```

The baseline's input files are copied into your workspace bucket, and their names, `crc32c` and
sizes are recorded in `$GSVTK_WORK/manifests/baseline_frozen_inputs.json`. They are copied rather
than referenced because Cromwell localizes `gs://` paths with your workspace's pet service account,
which usually cannot read the baseline's bucket. A copy also survives deletion of the source, and
since `crc32c` is preserved, a passing `verify` shows the bytes you compare against are the bytes the
baseline read.

Workspace-level attributes that cannot be recovered from the workflow outputs (Cromwell's execution
root is an ephemeral `fc-*` bucket) are read and copied too.

Safety rules:

* `copy` and `attrs` do nothing without `--write`. The copy is tens of GiB of billable storage.
* Both refuse to write into the shared baseline workspace (`GSVTK_BASELINE_NAMESPACE` /
  `GSVTK_BASELINE_WORKSPACE`) unless you pass `--allow-shared-target`.
* `attrs --write` refuses unless `verify` has recorded a passing verdict in the manifest. A missing,
  unreadable or failed verdict is refused. `--allow-unverified` overrides this and says so.

Frozen objects keep their source basename. When two different source objects share a basename (two
batches each holding `merged_pe.out`, for example), they are stored as `<attribute>__<basename>` so
neither overwrites the other.

For the `genotyping` module, the batch row everything reads and writes is the `sample_set` named by
`GSVTK_BATCH` (default `all_samples`). Every tool on this path uses that one setting, including
`fetch_baseline.py --entity` and `stage_inputs.py --attrs`. If the name is wrong, attribute bindings
resolve to nothing, so check it against the workspace.

`verify` compares size as well as `crc32c`, and the manifest also records the module, the entity row
and the source run.

### Frozen baseline for other flavors

What `terra/batch_freeze.py` freezes comes from the module profile's `freeze` block
([module-profiles.md](module-profiles.md#top-level)). The cohort chain (`genotyping`) is unchanged: same
attributes, same file and bucket names. For single-sample, name the baseline `sample` row:

```bash
python terra/recon.py --entities sample                                        # read-only dump
python terra/batch_freeze.py plan   --module single_sample --entity HG00096    # objects + the sample TSV
python terra/batch_freeze.py copy   --module single_sample --entity HG00096 --write
python terra/batch_save_metadata.py --outdir "$GSVTK_WORK/metadata"            # read-only
python terra/batch_freeze.py verify --module single_sample --entity HG00096 \
    --metadata "$GSVTK_WORK/metadata/<run>.<workflow-id>.json" --method-version <tag>
python terra/batch_freeze.py attrs  --module single_sample --entity HG00096 --write
```

The same modes through the CLI are `./gsvtk terra plan --module single_sample --entity HG00096`,
`./gsvtk terra verify ...`, and `./gsvtk terra freeze-copy ... --write --confirm` /
`./gsvtk terra freeze-attrs ... --write --confirm`.

* The CRAM's `.crai` (or the BAM's `.bai`) is copied beside it, because the WDL derives the index from
  the read file's path.
* The single-sample attributes are written under the names the step reads (no `_frz` suffix), onto a
  new `sample` row. If the baseline row references a `participant`, that participant is uploaded first
  and the sample row carries it; otherwise no participant is written.
* Objects go under `frozen-baseline/single_sample/<ROW>/` in your bucket. The manifest is
  `$GSVTK_WORK/manifests/baseline_frozen_inputs.single_sample.json`: every object's path, size and
  `crc32c`, plus the source run (submission, workflow, method, version, and the image digest each call
  used, from `--metadata`).
* `verify` refuses (exit 2) to record a baseline outside the cohort chain that names no source run:
  pass `--metadata`, or `--submission` / `--workflow`.
* `attrs` refuses unless the PASSED manifest is for the same module and row.
* Both writers refuse under `GSVTK_READ_ONLY=1` and keep the shared-workspace guard.

For a run with no Terra entity (sv-shell), list the run's `gs://` objects in a JSON file and pass
`--objects FILE` to `plan`, `copy --write` and `verify`:

```json
{"name": "sv_shell", "source_run": {"run_id": "...", "images": {}}, "objects": {"final_vcf": "gs://..."}}
```

`attrs` refuses there, because there is no row to write. The objects must already be at `gs://` paths:
uploading a local VCF is not built, so whatever ran sv-shell has to upload its output and write that
file.

Verified offline only, with a fake `gsutil` and a fake Terra module: the genotyping output is
byte-identical to before; the single-sample plan, TSV, participant ordering, index sidecar, write
gating, refusals and manifest; and the `--objects` flow. Unverified until a real run: that Terra
accepts the `sample` TSV and creates the row; whether a `participant` column is needed at all; that
the baseline CRAM's index is named `<cram>.crai` (copy fails loudly if not); the submission-id and
`dockerImageUsed` fields in Terra's Cromwell metadata; and that the single-sample step launches on the
frozen row. Its `literals` still point at the reference panel bucket, which is not frozen and must be
readable by the sandbox's pet service account.

## 3. Method configs

Two tools build method configs. `batch_configs.py` handles the whole batch chain;
`batch_rerun_step.py` builds one step's config with its images pinned, and is the one you normally
use.

```bash
python terra/batch_configs.py show                    # every resolved binding, no POST
python terra/batch_configs.py check --against main    # bound keys vs that ref's WDL, offline
python terra/batch_configs.py create --confirm        # POST the configs into YOUR workspace
python terra/batch_configs.py validate                # ask Terra to typecheck each one

python terra/batch_rerun_step.py --step 08 --tag rc1 show
python terra/batch_rerun_step.py --step 08 --tag rc1 create --confirm
python terra/batch_rerun_step.py --step 08 --tag rc1 validate
```

### Steps

`python terra/steps.py` prints the step map. For the shipped `genotyping` module it is:

| step | workflow | root entity | row comes from |
|---|---|---|---|
| 06 | GenerateBatchMetrics | `sample_set` | `GSVTK_BATCH` |
| 07 | FilterBatchSites | `sample_set` | `GSVTK_BATCH` |
| 08 | FilterBatchSamples | `sample_set` | `GSVTK_BATCH` |
| 09 | MergeBatchSites | `sample_set_set` | `--entity <name>` (required) |
| 10 | GenotypeBatch | `sample_set` | `GSVTK_BATCH` |

`--step` defaults to 10. It decides the config name, the input-key prefix, the Dockstore path, the
root entity and the row, and every mode prints what it resolved before doing anything. Other modules
(`./gsvtk --module single_sample ...`) have their own step maps; see [cli.md](cli.md) and
[module-profiles.md](module-profiles.md).

The root entity matters. Submitting 09 against a `sample_set` instead of `sample_set_set`, or any
other step against the wrong type, makes the `this.*` bindings resolve to nothing, usually after VMs
have booted. The type is derived from the step's own `this.<etype>_id` binding and cross-checked
against the profile; `batch_rerun_step.py` refuses a step where the two disagree. For 09 there is no
configuration key that names a cohort row, so `submit` refuses until you pass `--entity`.

### `--tag`: keeping two reruns apart

Without `--tag`, a rerun's config is named `<NN>-<Workflow>-rerun` and its outputs are written to
`this.<attr>_new` (the `GSVTK_NEW_SUFFIX`). A second rerun of the same step then overwrites the
first one's config and its output attributes, and a later comparison reads one run against itself.

`--tag TAG` changes exactly two names, both printed by `show`:

| | untagged | `--tag rc1` |
|---|---|---|
| config | `08-FilterBatchSamples-rerun` | `08-FilterBatchSamples-rc1` |
| outputs | `this.<attr>_new` | `this.<attr>_new_rc1` |

Inputs do not move: a tagged run still reads the chain's `_frz` and `_new` attributes, so two tagged
arms answer the same question from the same inputs. The tag may contain letters, digits and
underscores; `rerun` is refused. `--overwrite` on `create` must repeat the tagged name.

### Input maps and the ref they fit

The input maps describe one branch's WDL signature. `GSVTK_BRANCH` (or `GSV_WDL_VERSION` for a rerun)
only chooses which Dockstore version the config points at. If that ref declares different inputs,
Terra accepts the `create` and then rejects the submission for extra inputs. If Dockstore never
published the ref, the failure is earlier: `Cannot get dockstore://... from method repo`.

`batch_configs.py check` catches the first case offline. It reads `wdl/` from your gatk-sv checkout at
the ref with `git archive` and parses it with miniwdl, so the ref does not need to be published.
`create`, `validate` and the rerun tool's `submit` run the same check first and refuse on findings.

| Finding | Means |
|---|---|
| `EXTRA <WF>.<input>` | the map binds a key this ref does not declare. Known branch-only keys are labelled as such |
| `MISSING <WF>.<input>` | the ref requires an input with no default, and nothing binds it |
| `CANNOT CHECK` | the workflow file is absent at that ref, or miniwdl could not load it. Counted as a finding |
| out `UNDECLARED` | an output key the workflow does not declare. Terra accepts it at `create` and fails the finished run |
| `NO OUTPUTS BOUND` | the config writes nothing back; the run succeeds and leaves no attributes |
| pre-check `SKIPPED` (from `create`) | no ref or no checkout to compare against; printed, never silent |

`--against` belongs to `check` only. Commands that POST grade the ref their own Dockstore pin names,
because that is the WDL Terra will run. `--allow-unknown-inputs` posts anyway and prints that it did.

`--drop-branch-only-inputs` removes known branch-only bindings that the target ref does not declare.
This changes what the pipeline does (main's `GenotypeBatch` trains PE/SR from `vcf`, the branch from a
separate training VCF), so it is never automatic. It prints each dropped key to stderr, so
`show | jq` stays valid JSON, and it drops nothing against a ref it cannot read. If you meant to run
your own branch, fix the ref instead of using this flag. To see another ref's findings without
posting, run `check --against <ref> --drop-branch-only-inputs`.

`create` also refuses when upstream's `.json.tmpl` template binds a key twice and the map still reads
it from an attribute, since which attribute wins would depend on document order.
`--allow-duplicate-key <Workflow.key>` accepts one such key by name.

Two more WDL details:

* **The Dockstore path ends in the workflow name, which is not always the file's basename.**
  `wdl/DepthClustering.wdl` declares `workflow ClusterDepth`, for example. The five batch steps all
  match, but a new step whose file and workflow differ must use the workflow name.
* **A workflow Dockstore does not publish cannot be referenced as a method.** Sub-workflows are not in
  `.github/.dockstore.yml`. `python terra/wdl_flat.py <Workflow> --check` bundles a single-workflow
  import closure into one document and typechecks it with miniwdl, so it can be uploaded to the method
  repository ([below](#running-a-branch-that-is-not-on-dockstore)). It refuses closures containing
  more than one workflow, and closures where two different files declare the same task or struct name.

### Running a branch that is not on Dockstore

A rerun config normally names the step's Dockstore entry, which exists only for published branches. To
run any commit instead, upload the step's flattened WDL to the Terra method repository as a private
snapshot in your own namespace, then point the rerun at it:

```bash
./gsvtk terra method-plan GenotypeBatch --ref my-branch                  # free: sends nothing
./gsvtk terra method-upload GenotypeBatch --ref my-branch --confirm      # prints agora://<ns>/GenotypeBatch/<N>
python terra/batch_rerun_step.py --step 10 --method agora://<ns>/GenotypeBatch/<N> show
```

The tool behind both modes is `python terra/method_upload.py <Workflow> [plan|upload]`; run directly,
`upload` needs its own `--confirm`.

* **What is uploaded.** The WDL is read from the commit with `git archive`, never from your working
  tree, so uncommitted edits are not uploaded. `plan` prints the ref and full sha, the origin URL, the
  WDL's size and sha256, and the exact request body, and saves both under
  `$GSVTK_WORK/method-upload/`.
* **Provenance.** The origin URL and full commit sha are written into the snapshot's synopsis,
  documentation and comment, and into a comment at the top of the WDL.
* **The rerun.** `upload` keeps a record under `$GSVTK_WORK/method-upload/`. While that record is
  present, the rerun grades its bindings against the uploaded commit and refuses a snapshot of a
  different workflow. The Dockstore publish check reports `NOT APPLICABLE` for an Agora method.
  `create` and `submit` still need `--confirm`, and `GSVTK_BRANCH` must still be set (exit 4
  otherwise), although the Agora method replaces the Dockstore path it names.
* **Where it goes.** The namespace defaults to `GSVTK_TERRA_NAMESPACE` (`--namespace` to change; the
  name defaults to the workflow). Uploading into `GSVTK_BASELINE_NAMESPACE` needs
  `--allow-shared-target`. `upload` refuses under `GSVTK_READ_ONLY=1`.
* **Undo.** `upload` prints `DELETE <TERRA_API_ROOT>methods/<ns>/<name>/<N>`, which removes the
  snapshot.
* **Limits.** `wdl_flat.py`'s limits apply: a step whose import closure holds two workflows cannot be
  uploaded this way.

Verified offline only: flattening at a ref (uncommitted edits excluded), the payload and its
provenance, every refusal, that `upload` POSTs exactly the planned body, the record it writes, the
rerun body in Agora form, the unchanged Dockstore form without `--method`, and the workflow-mismatch
refusal. Unverified until a real cloud run: that Agora accepts this POST body and returns a
`snapshotId`; that Rawls accepts and echoes `methodRepoMethod` in Agora form on create, validate and
read-back; that a private snapshot launches from your workspace without extra method ACLs; and that
the flattened gatk-sv workflows pass Terra's WDL validation. Before uploading,
`python terra/wdl_flat.py <Workflow> --check` runs miniwdl on the same bytes.

## 4. Gate before you submit

```bash
python terra/batch_check_inputs.py --step 08
WOMTOOL_JAR=/path/to/womtool.jar python terra/batch_check_inputs.py --step 08 --no-sidecars
```

`batch_check_inputs.py` is read-only. It checks that the step's config binds files that exist, that
the attributes it binds actually sit on the row it will run against (`--entity` picks the row), and,
with `WOMTOOL_JAR` set, typechecks the WDL against a complete inputs object so a missing or renamed
input fails here. Without the jar, the womtool check and the required-set half of the input check
print a counted `SKIP` and the rest still runs. `--no-sidecars` models a run where index files are not
localized. It flags expression-shaped bindings (`~{}`, `select_all`, `read_*`, `{`, `[`) as
`FAIL expression-shaped bindings`, because Cromwell can evaluate those to empty and silently fall back
to the WDL default. It exits 1 on any FAIL.

It also checks the output suffix. If none of your step's outputs are under the suffix in force but
they are under another one, it reports a suffix mismatch rather than missing outputs, and names where
the suffix came from. `--strict` turns an underivable expected suffix into a failure.

### Images must be pinned

`batch_rerun_step.py` writes every `*_docker` input into the config body as a literal ref, so the run
cannot pick up whatever a workspace attribute points at. Refs come from, in order of precedence:

1. `--image KEY=REF` on the command line;
2. `--image-from <capture.json>`, an image-digest capture written by a build
   ([docker-builds.md](docker-builds.md#what-a-build-leaves-behind-the-image-capture));
3. the step's `images` map in `profiles/<module>.json`;
4. `GSVTK_GATK_IMAGE_REPO` (for `gatk_docker`) and `GSVTK_IMAGE_REPO` (for `sv_pipeline_docker`).

`show` prints an `image pins:` line naming the source of each ref (`--image`, `capture:<file>`,
`profile`, `profile/env`, or `UNPINNED`). If any `*_docker` input is unpinned, or a ref has no tag or
digest, every mode refuses, including `show`. `--allow-unpinned-docker` lets those inputs resolve from
workspace attributes; `submit` honours it and prints the exempted keys before the POST. Each step
declares its own set of `*_docker` inputs (07 has one, 08 has three including `linux_docker`), and
the refusal names that workflow's keys.

Example `show` output, offline, with nothing pinned:

```text
$ python terra/batch_rerun_step.py --step 09 show
[rerun] step 09  workflow MergeBatchSites  config 09-MergeBatchSites-rerun
        root entity sample_set_set  (row: UNRESOLVED: `entity_row` is --entity, so pass --entity <name>)
        Dockstore github.com/broadinstitute/gatk-sv/MergeBatchSites @ your-branch -- the ref the binding check grades too
        image pins: gatk_docker=UNPINNED   sv_base_mini_docker=UNPINNED   sv_pipeline_docker=UNPINNED
        writes 2 output attribute(s) (no --tag): workspace.merge_batch_sites_vcf_new, workspace.merge_batch_sites_vcf_index_new
        reads chain outputs of: 06-GenerateBatchMetrics, 08-FilterBatchSamples
        prove those attributes exist first: python terra/batch_check_inputs.py --step 09   (show does not read your workspace)
image inputs are not fully pinned -- a submit with these runs whatever the workspace
  attribute points at today, which is exactly what this tool exists to prevent:
  ...
```

### The Dockstore descriptor must be your branch tip

`create` and `submit` check whether the Dockstore version the config points at still resolves to
your branch tip, using the same checker as `./gsvtk publish` (`checks/version_resolves.py`, see
[static-checks.md](static-checks.md)). The check never fetches. Supply a Dockstore capture with
`GSVTK_PUBLISH_CAPTURE` and the tip (a sha, or `git ls-remote` output) with `GSVTK_PUBLISH_TIP`.

| verdict | what `create` / `submit` do |
|---|---|
| `CURRENT` | proceed |
| `STALE` | refuse, naming both shas and the `<branch>_terra` remedy. `--allow-stale-dockstore` proceeds and prints the exemption before the POST |
| `ABSENT` | refuse, same override. A capture that does not include your entry also answers `ABSENT` |
| `REFUSED` | proceed, labelled `UNVERIFIED`: the checker declined to answer, so there is nothing to override |
| (variables unset) | proceed, labelled `NOT RUN`, naming the variables to set |

A stale descriptor is costly because nothing fails: the name resolves, `validate` passes, the run
completes, and the output differs from the baseline for a reason unrelated to your change. Dockstore
only publishes branches listed in `filters.branches` in `.github/.dockstore.yml`; see
[test-a-branch.md](test-a-branch.md) for the remedy. A rerun given `--method agora://...` does not use
Dockstore, so this check reports `NOT APPLICABLE`
([§3](#running-a-branch-that-is-not-on-dockstore)).

## 5. Run, wait, price

```bash
python terra/batch_rerun_step.py --step 08 --tag rc1 submit --confirm
python terra/batch_status.py --wait 08        # poll the newest 08-* submission until terminal
python terra/batch_status.py --costs 08       # VM minutes per workflow, with a labelled total
python terra/batch_rerun_step.py --step 08 status   # recent submissions in the workspace
```

`submit` requires `--confirm`. Before the POST it rebuilds the body and reads the config of the same
name back out of the workspace, and refuses unless the two agree field by field. A submission runs the
config the workspace holds, which may have been posted by another branch or person; to change it, run
`create --confirm` again (with `--overwrite <name>` if it exists). An unreadable or missing config is
also a refusal. The submission line it prints names the step, the row and `useCallCache`, and those
are the values actually sent. The submission record is saved under `$GSVTK_WORK/metadata/` per step
and tag (`rerun_submission.08.rc1.json`).

`batch_status.py --costs PREFIX` prices every config whose name starts with PREFIX, including configs
outside the chain; a prefix that matches nothing exits 1.

### Call caching (`--call-cache`)

Call caching is off unless you ask for it. `batch_configs.py` and `batch_rerun_step.py` both accept
`--call-cache` / `--no-call-cache`; passing both is refused. The value is stored in the method config
and sent with the submission request, and `submit` prints the value it sends.

Leave it off for comparisons. With caching on, Cromwell reuses a call's earlier outputs when its
command and inputs match, so an arm can finish green without running your image at all. Cross-workspace
cache copies have also timed out mid-run and failed submissions. Turn it on only for retries of the
same run. `terra/fetch_outputs.py --cache-report` lists outputs that were served from the cache, and
`--refuse-cached` exits 7 if any were. `--resolve-cached` follows a cached output back to the bytes
behind it ([§9](#outputs-served-from-the-call-cache)).

### Two arms, one variable

A rerun that changes the WDL ref, the image and the upstream inputs all at once cannot tell you which
change moved the output. If the chain you froze came from a branch that also rewrote an upstream
workflow, the inputs themselves are branch artifacts.

To ask whether your code changed the output, change one thing: run the same step twice against the
same frozen inputs and the same WDL ref, once with the shipped image and once with your commit-pinned
build, each with its own `--tag`. Two ways this goes wrong without any error:

* **Both arms write the same attributes.** Give each arm its own tag (or `GSVTK_NEW_SUFFIX`), and make
  sure whatever reads an arm back uses the matching suffix. The empty-diff case in
  [troubleshooting.md](troubleshooting.md) is this mismatch.
* **Call caching is on, or a tag is mutable.** Pin images by digest or commit tag, and leave caching off.

### Aborting a submission

```bash
python terra/abort_submission.py --submission <full-submission-id> --confirm
```

Sends one DELETE for the submission, then polls (default 8 reads, 15 s apart; `--polls`,
`--interval`) until it reads terminal `Aborted`. `--no-wait` returns after the DELETE without
observing anything. It needs the full id, which `recon` and `batch_rerun_step.py status` print, and it
refuses the shared baseline workspace unless `--allow-shared-target`. Exit codes: 0 aborted (or
`--no-wait`), 1 refused, timed out, or ended in another state, 2 usage, 4 workspace not configured.

### Pricing a run from saved metadata

```bash
python terra/batch_save_metadata.py --outdir "$GSVTK_WORK/metadata"   # discovery, then full dumps
python terra/batch_cost.py --outdir "$GSVTK_WORK/metadata" --groups 10-baseline --groups 10-new
```

`batch_save_metadata.py` writes the full, sub-workflow-expanded Cromwell metadata for each submission
(`--target 10-new`, `--step`, `--side` narrow it). `batch_cost.py` never touches the network: it
recomputes cost from those files as VM-minutes and job counts, `sum(vmEndTime - vmStartTime)` over
every call at any depth, with no currency because rates depend on your billing account. `--json`
writes the table as JSON; `--groups` adds a per-call-group roll-up for one target.

Save metadata as soon as a submission reaches a terminal state. Terra prunes sub-workflow metadata
later, and a later fetch produces a much smaller tree that prices far lower without failing.

`batch_cost.py` reports incomplete data instead of a total:

| Missing | Output |
|---|---|
| a step's metadata on either side | `chain total is PARTIAL`, ratio `n/a`, exit 1 |
| a sub-workflow tree that was never expanded or failed to fetch | the cost is labelled a FLOOR |
| a call with `vmStartTime` but no `vmEndTime` | excluded from both minutes and job count, and counted |
| no metadata files at all | says so, exit 1 |

`checks/cost_estimate.py` prices a single submission's dump in vCPU-hours and dollars from the bundled
`prices/us-central1.json`, and withholds the dollar figure when the tree is truncated.

## 6. Fetch and compare

```bash
terra/batch_fetch_compare.sh all --dry-run   # print every command, run nothing
terra/batch_fetch_compare.sh fetch           # read *_new attributes, gsutil cp both sides
terra/batch_fetch_compare.sh table           # compare/compare_batch_tables.py
terra/batch_fetch_compare.sh profile         # paired gatk-sv-profile run
```

`fetch` is a no-clobber copy of tens of GB, so keep `GSVTK_WORK` on a roomy disk. `profile` refuses a
non-empty output directory unless `--force`. `table` exits 0 when the tables match, 1 on differences
and 2 when nothing was compared. For any other pair of callsets, use the comparators directly
([comparators.md](comparators.md)).

## 7. If the numbers moved

For each column that differs, decide which of these it is and write the answer next to the number:

1. **A bug in your change.**
2. **An intended behavior change.** The baseline number is what has to change. A strategy-aware
   comparator should mark the row `STRATEGY` instead of diffing it as a plain number.
3. **An input difference you did not control.** The comparison is invalid, not the code.

Freezing exists to rule out case 3. [comparators.md](comparators.md) covers case 2, for example a
column whose inputs are a superset on one side and so cannot be compared as a mean.

## Cost and blast radius

| Category | Commands |
|---|---|
| Free, read-only | `recon.py`, `batch_freeze.py plan` / `verify`, `batch_configs.py show` / `check`, `batch_rerun_step.py show`, `batch_check_inputs.py`, `batch_status.py`, `batch_cost.py`, `batch_peek.py`, `method_upload.py plan`, `dump_configs.py`, `fetch_outputs.py --dry-run` (with or without `--resolve-cached`), every `checks/` and `compare/` tool |
| Writes, no compute | `batch_freeze.py copy --write` (storage cost), `batch_freeze.py attrs --write`, `batch_configs.py create --confirm`, `batch_rerun_step.py create --confirm`, `method_upload.py upload --confirm` (a method snapshot) |
| Spends money | `batch_rerun_step.py submit --confirm` |
| Stops compute | `abort_submission.py --confirm` |

All writers refuse the shared baseline workspace unless `--allow-shared-target`. Every mode resolves
namespace and workspace before the first request, so an unset target exits 4 naming the profile key.
Terra has no per-workspace budget cap, and a whole-chain rerun is a fleet of VMs: rerun single steps
with `batch_rerun_step.py`, and watch with `batch_status.py --wait`.

## 8. Terra API behavior to know about

These came from driving real submissions. Each one is either rejected at submit time with an
unhelpful message, or passes validation and fails later.

**Config payload shape.**

* Config `inputs` keys must be workflow-qualified: `GATKSVPipelineSingleSample.dragen_vcf`, not
  `dragen_vcf`. Without the prefix, the validator reports every input as extra.
* The submission entity expression is exactly `this`. `this.sample.<id>` returns HTTP 400.
* Config `outputs` values are unqualified: `this.moi_summary`, not
  `this.GATKSVPipelineSingleSample.moi_summary`. The qualified form returns
  `400 … Entity references not permitted in the middle of output expressions`.
* `methodRepoMethod.sourceRepo` must be `dockstore`; `github` returns `400 Illegal method repo 'github'`.
* The config body needs `methodConfigVersion` and `deleted`; Rawls returns 400 without them. The
  tools set both.
* The submission POST takes `entityType` / `entityName` at the top level, and the submission record
  returns them nested under `submissionEntity`.

**Output names are never validated.** `invalidOutputs` is 0 even for an output the workflow does not
declare. The run then completes and Terra marks it `Failed` with
`output named <Workflow>.<name> does not exist`. `batch_configs.py check` reports these as
out `UNDECLARED`; run it before submitting.

**Listings can come back empty.**

* `GET …/submissions` and the method-config listing through the fiss client have returned `[]` for a
  workspace that held data, while raw REST returned it. `terra.submissions()` and
  `terra.workspace_configs()` ask both clients, print `the two endpoints DISAGREE` and use raw REST
  when the counts differ, and raise naming the endpoint when both are empty.
* `GET …/methodconfigs` can return `[]` from both clients until `?allRepos=true` is added.
* A finished submission's `outputFiles` is empty. Get output paths from saved Cromwell metadata
  (`terra/fetch_outputs.py`), not from the submission record. Listing the run tree with `gsutil ls -R`
  is slow because call caching leaves thousands of `cacheCopy` objects.

**Metadata is a cached snapshot.** Per-workflow metadata can lag the run by 30 minutes or more, and
top-level `calls` omits sub-workflow tasks. `?expandSubWorkflows=true` gives the full call graph but
can be tens of MB, so write it to a file. For live progress, the scratch bucket (`gsutil ls -d
…/call-*/`) and `terra/batch_peek.py` are more reliable.

**Per-submission snapshot configs.** When a submission runs, Terra stores a copy of its method config
under a snapshot name, `<config>_<random>` (for example `10-GenotypeBatch_Ab12Cd`). These are not
extra configs and should not be cleaned up. The tools look up the exact config name first, so a
snapshot that shares the `NN-` prefix is not mistaken for the live config; `batch_status.py` lists
snapshots under `(other config)`, and `batch_check_inputs.py` names and counts them as unscored.

**WDL behavior on the Google backend.**

* `write_lines()`, `write_tsv()` or `write_json()` at workflow scope fails on PAPIv2 with
  `Could not build the path "write_lines_….tmp"`, although it passes miniwdl and runs on local
  Cromwell. Call them inside a task command instead.
* Every `File` input is downloaded before the task's command runs, even if the command only checks
  `defined()`. Large unused inputs can fill a small disk and kill the job with
  `The job was stopped before the command finished` and no stdout or stderr. Pass a `Boolean` from the
  call site when you only need presence.

**A workflow without a Dockstore entry** (for example `BatchEvidenceMerging`) has no Dockstore method
path. Either upload it to the method repository
([§3](#running-a-branch-that-is-not-on-dockstore)) or reach it through a published parent workflow.

## 9. Reading one task's artifacts, and fetching an output by its name

**`terra/batch_peek.py`** is a bounded call-level view of a workflow: what is running, what retried,
the first failure message and per-attempt `rc` values. Against Terra it needs `--ns`, `--ws`,
`--submission` and `--workflow`. With `--metadata` it reads a local Cromwell dump and needs no network:

```bash
./terra/batch_peek.py --metadata "$GSVTK_WORK"/metadata/run1.json \
    --task GatherBatchEvidence --tail 25 --no-scratch
```

`--task` prints one call's `rc`, the tail of its stderr (with a count of lines not shown), the
rendered `script` that ran, and which `attempt-N` directories exist. `rc=141` under `attempt-1` and
`rc=0` under `attempt-2` usually means preemption. `--shard` and `--attempt` select one; asking for one
that does not exist is refused rather than substituted. `--show-stdout` adds stdout. Exit codes: 0
terminal with no failures, 2 terminal with failures or aborts, 3 still running, 1 could not read the
submission or an artifact it tried to show.

**`terra/fetch_outputs.py`** downloads outputs by workflow output name, resolving paths from saved
Cromwell metadata (entity attributes can be empty even when the run produced outputs):

```bash
./terra/fetch_outputs.py --metadata "$GSVTK_WORK"/metadata/run1.json \
    --output GATKSVPipelineSingleSample.svVCF --dry-run
```

Files go under `$GSVTK_WORK/outputs/` (`--subdir` to change) and each is recorded with its sha256 in
an appending manifest (`--manifest`). `--all` fetches every declared file output. `--dry-run` prints
the exact `gsutil -m cp -n` commands. `--link-dir` adopts an existing local copy instead of
downloading. `--verify` re-hashes what the manifest records.

| Exit | Meaning |
|---|---|
| 0 | fetched, or verified clean |
| 2 | usage: ambiguous name, `--all` with `--output`, nothing requested |
| 3 | the workflow does not declare that output (the declared names are printed) |
| 4 | the output is declared but has no value; under `--resolve-cached`, `NO-PRODUCING-CALL` or `NOT-A-CACHE-HIT` |
| 5 | no readable metadata with an `outputs` map, or an unreadable `--hit-metadata` file |
| 6 | a fetch did not land, or `--verify` found drift |
| 7 | `--refuse-cached` found a call-cache copy, or `--resolve-cached` could not follow one |

### Outputs served from the call cache

`--refuse-cached` stops at a cache-served output. `--resolve-cached` finds where its bytes are and
labels each output:

| Label | Meaning |
|---|---|
| `RESOLVED-DIRECT` | this run wrote it |
| `RESOLVED-CACHECOPY` | a copy under the call's `cacheCopy/`, byte-identical to the earlier call's output |
| `RESOLVED-VIA-HIT <workflowId>:<call>:<shard>` | the cached call names no path, so the path comes from the call it hit, in that workflow's metadata |

```bash
./gsvtk terra fetch --metadata "$GSVTK_WORK"/metadata/run1.json \
    --output GenotypeBatch.genotyped_pesr_vcf --resolve-cached --dry-run
```

The hit workflow is looked for in the run's own metadata first. If it is not there, save its metadata
and pass it, or look it up read-only in Terra (GETs only, so this works under `GSVTK_READ_ONLY=1`):

```bash
./terra/fetch_outputs.py --metadata "$GSVTK_WORK"/metadata/run1.json --output genotyped_pesr_vcf \
    --resolve-cached --hit-metadata hits/earlier.json --dry-run
./terra/fetch_outputs.py --metadata "$GSVTK_WORK"/metadata/run1.json --output genotyped_pesr_vcf \
    --resolve-cached --online --max-submissions 50 --dry-run   # or --hit-submission <id>
```

A hit on a sub-workflow is never listed at submission level, so `--online` cannot find it by search;
pass `--hit-submission` or `--hit-metadata`, as the refusal says. When no call output carries the
output's name, `--call NAME=FQN[:SHARD][@OUTPUT]` names the producing call. Scattered calls are
resolved per shard.

Anything it cannot follow is refused by name before anything is fetched: `HIT-RESULT-ABSENT`,
`HIT-RESULT-MALFORMED`, `HIT-WORKFLOW-NOT-FOUND`, `HIT-CALL-NOT-FOUND` and `HIT-OUTPUT-ABSENT` exit 7;
`NO-PRODUCING-CALL` (it lists cache-hit calls with empty outputs as `--call` candidates) and
`NOT-A-CACHE-HIT` exit 4; `AMBIGUOUS-CALL` exits 2. The manifest records each file's `route`. Through
`./gsvtk terra fetch`, a run without `--dry-run` downloads and so needs `--confirm`.

Verified offline only, on synthetic metadata: the route logic for every label, scatter, retry and
saved-tree lookups, every refusal, and the online control flow against a fake Terra module. Unverified
until a run that really hit the call cache: the exact shape of `callCaching.result` and whether its
call and shard match the hit workflow's metadata; whether a cached call's `outputs` keeps the output
key with an empty value (name matching relies on it; if it is dropped, the tool refuses with
`NO-PRODUCING-CALL`); whether Terra serves a hit workflow from another submission or workspace (only
the configured or `--ns`/`--ws` workspace is searched); and the download of a `RESOLVED-VIA-HIT` path,
which may have been deleted since. Every resolve run prints this as an `UNVERIFIED` note and stores it
in the manifest.
