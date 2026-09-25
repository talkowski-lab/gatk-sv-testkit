# Head-to-head on Terra: a branch against a real baseline run

The question this answers: **did my change alter the pipeline's output, and if so where?**

Comparing two whole pipeline runs is usually meaningless — different inputs, different
reference panels, different tags, and the differences that matter hide inside the differences
that don't. So the shape here is: **freeze one baseline's inputs, rerun only the stage you
changed, compare that stage's output against the baseline's** — with every input pinned to a
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

## 1. Recon — read-only, run this first

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
basename (this model does — e.g. two batches each holding `merged_pe.out`): those freeze as
`<attribute>__<basename>`. Without that, both attributes end up pointing at one object, the last
writer wins, and one of the inputs steps 06/07 read is quietly the wrong file.

**Object coordinates.** The baseline's input files get copied into your workspace bucket with
their **names, `crc32c` and byte size** recorded in
`$GSVTK_WORK/manifests/baseline_frozen_inputs.json`. Not referenced — copied: your sandbox has its
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
# and, for the rerun path, the same two things with the same guard:
python terra/batch_rerun_step.py show
python terra/batch_rerun_step.py create --confirm [--drop-branch-only-inputs]
```

The configs are generated from one table, so a binding cannot drift between steps. Each config
binds the frozen inputs plus **the image you built** plus the Dockstore tag for the rest of the
chain.

**The input maps are a snapshot of one branch's WDL signature; `GSVTK_BRANCH` only chooses the
Dockstore URL.** Point the URL at a ref the maps were not written against and they carry keys that
ref never declared, which Rawls rejects as an **extra input at submission** — after `create`
succeeded, so the workspace holds a config that looks fine until someone submits it. A ref that
Dockstore never published fails earlier with `Cannot get dockstore://... from method repo`: one
mismatch, two symptoms, and the 404 tends to arrive first and absorb the attention.

`check` is the offline form of `validate`: it reads `wdl/` out of your own checkout with `git
archive` and parses the workflow with miniwdl, so it needs neither the ref to be published nor a
Terra target — which is why `create` and `validate` run it before doing anything else and refuse on
findings. What it reports:

| Finding | Means |
|---|---|
| `EXTRA <WF>.<input>` | the map binds a key this ref does not declare. Known branch-only keys are labelled as such (the table beside `CONFIGS`), because "exists on the branch under test" and "never seen" need different fixes |
| `MISSING <WF>.<input>` | the ref **requires** it and nothing binds it: no default to fall back on, fails as a missing input |
| `CANNOT CHECK` | the workflow file is not in that tree or miniwdl could not load it. Counted as a finding — a skipped comparison is not a pass |
| pre-check `SKIPPED` from `create` | no ref or no checkout, so the comparison never ran. Printed, never silent |

`--allow-unknown-inputs` posts anyway and prints that it was taken. Measured against this repo's
maps (redacted per this repo's own audit rule, which treats a person-named branch as an internal
identifier — see [config.md](config.md)): `<branch-under-test>` 0 of 64 rejected, `origin/main` 1
(`10-GenotypeBatch.training_vcf`, a branch-only input), `v1.1.1` 56 findings — those maps were never
a `v1.1.1` shape. Run `check` yourself to reproduce all three; the numbers are three commands.

**The guard is on the rerun path too, and that mattered more than it looks.**
`terra/batch_rerun_step.py` builds its config with `from batch_configs import body` — the builder,
not the guard — so when the pre-check landed only in `batch_configs.create`/`validate`, the one path
that actually submits stayed open: create returned 200, submit returned 400, and Terra's own
typecheck was the thing that named the key. `create`, `validate` and `submit` each call it now. Since
rerun's Dockstore pin is `GSV_WDL_VERSION`, which can differ from `GSVTK_BRANCH`, what it grades is
the pin — the ref that config will really run — and it prints which ref it compared.

**Want the other ref's shape? Ask for it by name.** `--drop-branch-only-inputs` removes
known-branch-only bindings the target ref does not declare, so the map fits the ref you pointed at.
It is deliberately not automatic, because pruning a binding is not a fix, it selects a different
pipeline: main's `GenotypeBatch` trains PE/SR from `vcf`, the branch from a separate training VCF.
So it drops nothing against a ref it cannot read (unverified is not evidence of absence), it prints
each dropped key to **stderr** so `show | jq` stays valid JSON, and it prints the semantic
consequence plus the ref it compared. If you meant to run your own branch, unset `GSVTK_BRANCH`
instead of reaching for this flag.

The guard grades the **pruned** map, not the raw table: `check_maps(..., drop=...)` applies the same
`BRANCH_ONLY_INPUTS` rule `_adapt_inputs()` uses. Before that, `show --drop-branch-only-inputs`
printed the 16-key body Rawls accepts while `create` on the same command line refused to POST it and
named the key it had just dropped. The flag is still not a blindfold — a key outside the table is
reported `EXTRA` with the flag set.

**…and it grades the one ref that config runs.** `--against` belongs to `check`, and a command that
POSTS refuses it: `create` and `validate` compare the ref their own Dockstore pin names
(`GSVTK_BRANCH`, or `GSV_WDL_VERSION` on a rerun), because that is the WDL Terra will actually read.
Letting you pick a different one was worse than a wrong pass — with `--drop-branch-only-inputs` the
guard pruned the key *there* and then posted the map built from the branch, printing `DROPPED … this
is what body() posts` about a body it had not built. To post another ref's shape, point
`GSVTK_BRANCH` at it (that is what pins Dockstore there); to see that ref's findings without posting
anything, `check --against <ref> --drop-branch-only-inputs`. `probe_fixes.py`'s `drop_flag_guard` pins
both halves — including the body that actually leaves the machine, because "the guard and the body
agree" is a claim about two components and only the POST can settle it.

Two non-obvious details that cost real debugging time when wrong:

- **The root entity differs by step, and it is not what you would guess.** `09-MergeBatchSites`
  is submitted against `sample_set_set`; `06`, `07`, `08` and `10-GenotypeBatch` against
  `sample_set` (`batch_configs.py`'s `rootEntityType` per config is the authority, and it agrees
  with gatk-sv's own `GenotypeBatch.json.tmpl`, which binds `GenotypeBatch.batch` to
  `${this.sample_set_id}`). Submit against the wrong one and the batch-level `this.*` bindings
  resolve to nothing — usually at runtime, after VMs booted.
- **`GSVTK_BATCH` must name a real `sample_set`.** It defaults to `all_samples`, the entity in
  gatk-sv's reference-panel workspace; rename it and every `this.<attr>` binding silently empties.
  It is read by every tool on this path — including `fetch_baseline.py --entity` (which writes the
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
  multi-workflow closures on purpose -- including `TinyResolve`, whose import `GetShardInputs.wdl`
  declares a workflow of its own -- because a document with two workflows has no primary and fails
  at submission looking like a broken WDL. It likewise refuses a closure where two **different**
  files declare the same task/struct name (importing one file twice is fine; it is emitted once):
  one document cannot hold it twice, and picking a winner is a decision about what the pipeline
  runs, not a bundler detail. gatk-sv main has no such collision in any of its 118 closures.
- **A binding is not an expression.** A value Cromwell *evaluates* rather than *reads* can come
  back empty, and the WDL's own default then wins — which is invisible in the create/validate
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

`terra/batch_rerun_step.py` refuses every mode **except `show`** while no image is pinned —
either `--image KEY=REF` per call, or an explicit `GSVTK_IMAGE_REPO`/`GSVTK_GATK_IMAGE_REPO` in the
profile. A value merely *derived* from your project does not count, because "derived from the
project" is not a statement about which code ran. `show` is deliberately allowed unpinned so you
can inspect the body before deciding. Unpinned images are the one class of mistake that otherwise
looks like a successful submission followed by an `ImagePullBackupFailed` on every shard — or
worse, a green run of someone else's code.

## 5. Run, wait, price

```bash
python terra/batch_rerun_step.py status
python terra/batch_rerun_step.py submit --confirm     # needs your branch, your image, --confirm
python terra/batch_status.py --wait 08                # poll the newest 08-* submission until terminal
python terra/batch_status.py --costs                  # quick per-submission cost delta
```

What `batch_rerun_step.py` guarantees is about *images*: every `*_docker` input is pinned literally
into the config it POSTs, so a rerun cannot inherit whatever the workspace attribute happened to
point at. It does **not** re-read live workflow outputs — `batch_check_inputs.py --step 10` is the
tool that checks bindings against the last submission's actual `inputResolutions`.

### Two arms, one variable

A rerun that changes the WDL ref, *and* the image, *and* reads attributes written by some earlier
branch-era chain answers no question in particular: the changes are confounded and a diff cannot
separate them. If the chain you froze came from a branch that also rewrote a workflow feeding this
step, then the inputs themselves are branch-era artifacts, and running a main-shaped step over them
and comparing to a production baseline mixes three variables at once.

To ask "did my code change the output", leave one variable: same WDL ref on both arms, same frozen
inputs, `sv_pipeline_docker` = the shipped image on one arm and your commit-pinned build on the
other. That is two submissions of one step, and the config machinery above is what makes them
identical apart from one line. Price it after the fact with `batch_cost.py --costs` rather than
guessing beforehand.

Two ways that design degenerates silently, both cheap to avoid:

- **Both arms write the same entity attributes.** Outputs land in `*<GSVTK_NEW_SUFFIX>` (default
  `_new`), so the second arm overwrites the first and the comparison ends up reading one run against
  itself. Give each arm its own suffix and pass the matching `GSVTK_FROZEN_SUFFIX` to whatever reads
  that arm back — the empty-diff failure mode in [troubleshooting.md](troubleshooting.md) is this
  disagreement between two tools.
- **`useCallCache: true`.** Cromwell reuses call outputs when command and inputs match, which is what
  you want across retries and not what you want between arms if a tag is mutable. Pin both images by
  digest or commit-SHA tag — which is what the pinning guard in this tool already refuses to let you
  skip for `*_docker`.

```bash
python terra/batch_save_metadata.py --outdir "$GSVTK_WORK/metadata"   # discovery, then full dumps
python terra/batch_cost.py --outdir "$GSVTK_WORK/metadata" \
       --groups 10-baseline --groups 10-new
```

Cost is **recomputed from saved Cromwell metadata** rather than quoted from a dashboard, and is
reported in **VM-minutes and job counts** — no rates, no currency, because your billing account is
not mine. The accounting is `sum(vmEndTime - vmStartTime)` over every call record at any nesting
depth (`batch_save_metadata.vm_minutes`), which is deliberately not the root `call start→end` span:
spans are printed as a separate column because they are what produced two bogus headline figures
here. Multiply the VM-minutes by your own per-type price and re-run the arithmetic against the JSON —
that is the point of storing it.

**The chain total and the ratio are refused when the data is incomplete**, because every one of these
forms used to print a tidy wrong number and exit 0:

| What is missing | What the tool now says |
|---|---|
| one step's metadata file on either side | `chain total is PARTIAL: no saved metadata for 06/baseline`, `ratio new/baseline: n/a`, **exit 1**. That step counted as *zero* in the sum — deleting one file once flipped the conclusion from “4.5× cheaper” to “20× more expensive” |
| a sub-workflow whose tree was never expanded (depth cap, older or hand-trimmed file) | `N sub-workflow call(s) have no expanded tree below them -> cost is a FLOOR`. `_missingSubWorkflows` alone was not enough: a truncated tree looks exactly like a complete one, and baseline step 10 read 918 VM-min instead of 1672.8 that way |
| a call with `vmStartTime` but no `vmEndTime` (running or aborted) | `excluded from BOTH minutes and job count` — the job count under-reports too, not just the minutes |
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

For **any** other callset pair, use the generic comparators directly —
[comparators.md](comparators.md).

## 7. If the numbers moved

The discipline that matters: for each differing column, decide which of these it is, and write
the answer next to the number.

1. **a bug in my change** — the interesting case;
2. **an intended behaviour change** — then the baseline number is what must change, and a
   strategy-aware comparator should mark it `STRATEGY`, never diff it as a bare number;
3. **an input difference you did not control** — the comparison is invalid, not the code.

Case 3 is why freezing exists. Case 2 is why [comparators.md](comparators.md) exists — a column
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
