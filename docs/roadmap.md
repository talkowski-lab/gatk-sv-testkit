# Roadmap

What the kit has built but not yet checked on a real cloud run, and what is waiting on a maintainer
decision. Shipped capabilities are described in the other pages.

## Built, not yet verified on a real cloud run

Each of these passes its offline tests against fakes and synthetic data. None has run against real
Terra, GCS or a real VM yet, so the first real run has to confirm the items in the last column. Each
linked section says what was verified offline.

| Feature | Command | A first real run must confirm |
|---|---|---|
| [Unmerged branch on Terra](terra-head-to-head.md#running-a-branch-that-is-not-on-dockstore) | `./gsvtk terra method-plan <Workflow>`, then `./gsvtk terra method-upload <Workflow> --confirm` and a rerun with `--method agora://...` | Agora accepts the POST body and returns a `snapshotId`; Rawls accepts and echoes `methodRepoMethod` in Agora form on create, validate and read-back; a private snapshot launches from your workspace without extra ACLs; the flattened workflows pass Terra's WDL validation |
| [Replay one task](local-replay.md#replay-one-task-on-a-vm) | `./gsvtk replay task ...` | inputs stage on the VM host with the metadata token (requester-pays, token refresh on long staging); the container sees the bind mount; real Cromwell `script` files replay cleanly (`mkfifo` wrappers, the Batch backend layout, outputs outside the root) |
| [sv-shell end to end](local-replay.md#sv-shell-end-to-end) | `./gsvtk replay svshell ...` | the same staging and mount questions; the upload to `--out-uri` (files under 5 GB); runtime, disk and machine sizing; the fetch back with `gsutil cp` |
| [Frozen baseline per flavor](terra-head-to-head.md#frozen-baseline-for-other-flavors) | `./gsvtk terra plan --module single_sample --entity NAME`, then `freeze-copy`, `verify`, `freeze-attrs` | Terra accepts the `sample` TSV and creates the row; whether a `participant` column is needed; the CRAM index is named `<cram>.crai`; the submission id and `dockerImageUsed` appear in Terra's metadata where expected; the single-sample step launches on the frozen row, with its unfrozen reference-panel `literals` readable by the sandbox's pet service account. sv-shell: whatever ran it must upload its output and write the `--objects` file |
| [Resolve call-cached outputs](terra-head-to-head.md#outputs-served-from-the-call-cache) | `./gsvtk terra fetch ... --resolve-cached --dry-run` | needs a run that really hit the cache: the shape of `callCaching.result` and whether its call and shard match the hit workflow; whether a cached call keeps the output key with an empty value; whether a hit workflow in another submission or workspace can be read; the download of a `RESOLVED-VIA-HIT` path |
| [Profiles derived from upstream](module-profiles.md#deriving-a-profile-from-upstream) | `python scripts/derive_profile.py check --module genotyping` | nothing touches the cloud; it has been run read-only on a real gatk-sv checkout. Whether a derived binding launches is Terra's question, and its WDL scan is not miniwdl |

## Decisions for the maintainers

1. **The entity-type rule.** Decided: it matches Terra. On the public
   `GATK-Structural-Variants-Joint-Calling` workspace, all 24 configs agree with Terra's
   `rootEntityType`, and none were underivable. Rerun the check against any workspace with
   `./gsvtk terra dump-configs` and `./gsvtk entity --against-dump FILE`.
2. **Scope of reproducible baselines.** Decided: a reproducible baseline outside the cohort chain is in
   scope. It is built as the profile `freeze` block and `terra/batch_freeze.py --module` /
   `--objects` ([terra-head-to-head.md](terra-head-to-head.md#frozen-baseline-for-other-flavors)).
3. **The genotyping profile and upstream.** Decided: every difference is fixed or explained. The
   three missing per-caller index outputs on step 08 were added; dragen and melt are recorded as
   `unused_callers`; step 10's output changes as `branch_only_outputs` and `upstream_only_outputs`; and
   `single_sample`'s two explicit bindings as `template_overrides`. Both profiles now agree with gatk-sv
   `main`, which [compatibility.md](compatibility.md) records.
4. **`pesr_breakend_window` in sv-shell.** gatk-sv's `single_sample_pipeline.sh` passes
   `pesr_breakend_window` to `cluster_batch.sh`, which reads it with no default, and the shipped
   `src/sv_shell/sample_inputs/single_sample_pipeline*.json` files do not supply it.
   `checks/svshell_contract_check.py` does not catch this, because it checks pass-through keys only at
   the driver level. `replay/svshell_run.py` refuses those inputs unless the key is added or
   `--allow-missing pesr_breakend_window` is given. Decide whether upstream should add the key to its
   sample inputs or give it a default, and whether the contract check should follow keys into the
   modules.

## Known test-suite limit

A phase that skips because a prerequisite is missing (a gatk-sv checkout, the womtool jar) prints one SKIP for
a whole block of assertions. Each phase prints how many assertion sites it declares and how many ran, so the
gap is visible. Read those lines rather than the pass count alone.
