# Plan: launching modules outside the batch-chain steps 06-10

Status: **draft, for adversarial review.** Nothing here is implemented. Owner decisions requested at the
bottom; stage D spends money and is not authorized by this document.

## Why this exists

`terra/batch_rerun_step.py` launches steps 06-10 of the batch genotyping chain and nothing else. It works
because a module profile names the workflow, the root entity type and the step's config name, so the
launcher has no genotyping literals of its own. It stops at 10 because three facts about any *other*
module are still typed by hand rather than read from somewhere that changes when gatk-sv changes:

- which workflow a module runs,
- what entity that workflow runs against,
- where its input values come from.

The owner constraint that shapes the whole design: **no hard-coded templates that have to be kept
synchronized with gatk-sv.** A template that silently drifts is worse than no template, because it fails
as a successful launch with stale inputs.

## Measured facts this plan leans on

1. Most launch-time values are not in files. On a real gatk-sv tree, of 147 bindings for one shared
   container's images, **131 were Terra workspace attributes** rather than values in any file, and 16 were
   literals. A launcher that expects inputs to come from a rendered JSON is describing 11% of the problem.
2. Upstream's own input renderer is already runnable here: `checks/wdl_inputs_check.py --render-only`
   unpacks a ref with `git archive` and runs `build_default_inputs.sh` in a temp tree. The checkout is
   never touched, and upstream decides which input JSONs exist and what they contain. gatk-sv PR #966
   renamed `test_gatk_ingestion_params` -> `test_gatk_ingestion_resources_30x` and the layer adapted with
   zero local edits.
3. `this.*` reads decide what a workflow iterates over, and they can be read out of the WDL. The §9
   step-5 measurement used exactly this to *refuse* to launch a module: `TrainGCNV` reads `this.samples.*`
   at two sites while operating on `sample_set` rows, and `ResolveCpxSvGenotyping` is one of only 8 of 12
   batch-chain workflows that read over `sample_sets`.
4. CI pairs each workflow with `test/<W>.inputs.json` plus a Terra JSON at
   `terra/workflow_configurations/<W>.inputs.json`; the Terra half is the one that actually launches.

## Design principle

A profile may hold **only facts with no other home**. Everything derivable is read at run time from one of:

| Source | Supplies | Why it does not drift |
|---|---|---|
| the WDL at the ref (miniwdl AST) | declared inputs, optionality, struct shapes, call graph | read from the ref being launched |
| upstream's input renderer at the ref | default values CI itself uses | upstream owns the file list and the values |
| the Terra workspace | attribute values, existing entity rows | they *are* the state |
| the module profile | which attribute feeds which input; where the entity row comes from | short, and it fails loudly when wrong |

Anything the four cannot supply is printed as `UNRESOLVED` with a reason. It is never guessed, never
defaulted silently, and never filled from a local copy of last month's values.

## Stage A0 - measure before writing a line of launcher code

Deliverable: a measurement, committed as a table, answering: **can the root entity type be derived from the
WDL alone, across every workflow in the checkout?**

Method: for each workflow, read `this.<collection>.<attr>` member accesses and the collections it scatters
over; classify as unambiguous / ambiguous / none. Cross-check against something that is not our own
reasoning - Terra's own `rootEntityType` in the workspace method configs, from a recon dump the operator
runs once with credentials. Report agreement and disagreement per workflow, including the cases where a
workflow has no `this.*` read at all, reads two collections, or reads `this` inside a called task.

Exit criterion: a named list of workflows where derivation is safe, and a named list where it is not. If
derivation cannot beat "read Terra's own method config" for the majority, **stage A changes shape** to
config-first (the operator's existing config is the source, our job is to regenerate and diff it) and this
plan is rewritten. That is a real possible outcome, not a formality.

## Stage A - derive the launch schema

Given a workflow name and a ref: workflow source path, the Dockstore `name#version` (the launch address;
note Dockstore keys on workflow *name*, not path, so a path-derived guess can bind the wrong workflow),
declared inputs with optionality, and the root entity plus the entity paths it reads. Refuse to produce a
plan when the root entity is ambiguous, naming the competing reads.

Tests: offline fixtures; a synthetic WDL per shape (one collection, two collections, none, `this` inside a
task, struct-typed input). Every assertion falsified by breaking the mechanism, per the ledger's rule that
an assertion nobody has seen fail is a guess.

## Stage B - resolve values without a template

Render upstream's inputs at the ref (reuse `--render-only`; do not write a second renderer - §10's rule is
one expander per kind). Overlay the profile's attribute references. Anything left is `UNRESOLVED`, with the
reason: not in upstream's render, present but an attribute we were not told about, struct member, or
conflicting between the test JSON and the Terra JSON.

Acceptance: on `origin/main` and one older ref, the resolved set for a workflow outside 06-10 (candidate:
`GATKSVPipelineSingleSample`, since CI renders its inputs too) covers every required input CI itself
launches with, and the unresolved list is empty for that workflow or every entry has a stated reason.

## Stage C - make 06-10 a caller of the general launcher

Generalize the existing path rather than writing a second one: show -> create -> validate -> submit stays
the only route to a submission, with `--confirm` required, the shared-target guard intact, read-only mode
honored, and image pinning (`--image KEY=REF`, refusal to launch unpinned) carried over unchanged because
that is the money-safety half and generalization must not weaken it. `useCallCache: false` stays explicit.
Then delete the step table, keeping `rerun` as a thin caller.

Order matters: the general path must reproduce the existing 43 rerun assertions and the pinned-image guard
before any step-specific code is deleted, and the deletion is its own commit so a regression has a name.

## Stage D - prove it, cheapest first

1. A `--plan` mode that prints the exact config body, root entity type and entity row it *would* submit,
   with no POST of any kind.
2. Diff that against the hand-assembled config from the PR #966 investigation, which was built against the
   Terra API by hand and is therefore an independent oracle rather than our own output.
3. Only then, and only with the owner's explicit go: launch one real single-sample run, watch it with the
   existing monitor, fetch its outputs, compare. Acceptance is not "it submitted" - it is "the run produced
   the expected entity output and its inputs match the plan".

## Non-goals

- Image build/publish (the `--push` ledger gap). Separate, and it needs a registry decision.
- Participant/member-entity baselines (§11 q3). The freeze loop writes one cohort-chain row type today;
  modules rooted elsewhere need the `freeze` field, which is exactly why `TrainGCNV` failed the two-changes
  test. This plan launches against an existing workspace; it does not claim reproducible baselines for
  modules outside the cohort chain.
- Editing the gatk-sv checkout, or any local copy of upstream input files.

## Risks the plan has not resolved

- Root entity derivation may not be derivable from the WDL (stage A0 exists to find out early).
- Upstream's renderer may not cover a workflow outside the cohort chain, or may cover it with values that
  only make sense inside a full release tree.
- Attribute names have no upstream home. If the workspace's attributes are what makes a launch work, the
  profile's attribute list is a template of a different kind - smaller and self-checking, but ours to keep
  right. The mitigation is that a wrong attribute name must fail at plan time as `UNRESOLVED`, never as a
  launched job with a missing input.
- Stage C touches the code path that guards money. A mistake there is not a red test, it is a bill.

## Owner decisions requested

1. Authorization for the credential-using recon in stage A0 (read-only dump of workspace method configs).
2. Approval for exactly one real launch in stage D, with a cost ceiling stated before submission.
3. Whether "reproducible baseline outside the cohort chain" is in scope at all, or launching an existing
   workspace is the actual requirement.
