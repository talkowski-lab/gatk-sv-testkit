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
* **`attrs --write` refuses over a failed verify.** `verify` writes `"verified": true|false` (plus
  the mismatching objects) into the manifest, and `attrs` refuses to publish if the last verify for
  that prefix failed. Publishing a frozen path nobody confirmed is how a head-to-head ends up running
  on inputs that are not the baseline.

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
python terra/batch_configs.py show          # print every resolved input binding, no POST
python terra/batch_configs.py create        # POST/overwrite the configs in YOUR workspace
python terra/batch_configs.py validate
```

The configs are generated from one table, so a binding cannot drift between steps. Each config
binds the frozen inputs plus **the image you built** plus the Dockstore tag for the rest of the
chain.

Two non-obvious details that cost real debugging time when wrong:

- **The root entity differs by step, and it is not what you would guess.** `09-MergeBatchSites`
  is submitted against `sample_set_set`; `06`, `07`, `08` and `10-GenotypeBatch` against
  `sample_set` (`batch_configs.py`'s `rootEntityType` per config is the authority, and it agrees
  with gatk-sv's own `GenotypeBatch.json.tmpl`, which binds `GenotypeBatch.batch` to
  `${this.sample_set_id}`). Submit against the wrong one and the batch-level `this.*` bindings
  resolve to nothing — usually at runtime, after VMs booted.
- **`GSVTK_BATCH` must name a real `sample_set`.** It defaults to `all_samples`, the entity in
  gatk-sv's reference-panel workspace; rename it and every `this.<attr>` binding silently empties.
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
  method config with a wrong binding is worse than no config: the next submission reads it.
- **Spends money:** `submit`. Requires `--confirm`, your `GSVTK_BRANCH` *and* `GSVTK_IMAGE_REPO`,
  and re-reads live outputs first.
- Terra has no per-workspace budget cap. A whole-chain rerun is a fleet of VMs. Prefer
  `batch_rerun_step.py` (single step) over a full rerun, and `--wait` over watching the console.
