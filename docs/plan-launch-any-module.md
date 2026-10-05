# Plan: launching modules outside the batch-chain steps 06-10

Status: **revision 2, after adversarial review.** Revision 1's two load-bearing technical claims were both
falsified by measurement, by me as well as by the reviewer; the corrected derivations are below, each with
the command that reproduces it. Nothing here is implemented. Stage D spends money and is not authorized by
this document.

## What review found in revision 1

Two criticals, both verified independently by the author against the tree at `main` = `e1909d2f`, not taken
on the reviewer's word:

- **The WDL contains no entity paths at all.** `git grep -l 'this\.' -- 'wdl/*.wdl'` returns **0 files**.
  `this.<x>` is Terra's entity-passing syntax and lives in method configs and input templates. Revision 1's
  Stage A proposed reading the root entity out of the miniwdl AST, which cannot answer it: a document that
  used `this` in an expression would fail to load, not report an access.
- **Even from the right artifact, the stated rule answered the wrong question.** On the corpus that does
  carry entity paths, `this.<collection>.<attr>` names the *member* entity, never the root. For
  `MergeBatchSites` the config contains 1 read of `this.sample_set_set_id` and 3 of `this.sample_sets`:
  the member rule derives `sample_sets`, and `profiles/genotyping.json` says `sample_set_set`. Our own
  `terra/steps.py` already refuses to guess this ("a wrong type resolves every `this.*` binding to nothing
  at runtime, after the VMs booted"). Worse, the rule's ambiguity bucket is empty, so the refusal it leaned
  on could never fire, and a submission with a wrong `entityType` still looks accepted.

Also corrected: a "measured fact" cited `ResolveCpxSvGenotyping`, a workflow that does not exist at `main`
(`git cat-file -e main:wdl/ResolveCpxSvGenotyping.wdl` fails; `ResolveComplexVariants` and `ResolveCpxSv`
do exist) — and this repo already files that name as an open documentation defect in
`docs/module-profiles.md` §12.3. Another claimed a PR #966 input rename that the layer absorbed; `git grep
-ingestion main` returns **0 files**, so the claim is unsupported and is deleted rather than reworded. A
third said attribute names have no upstream home; `workspace.tsv.tmpl` says otherwise.

## What survives, and the measurements that replaced the rest

Principle unchanged: **a profile may hold only facts with no other home**, and anything unresolved prints
as `UNRESOLVED` with a reason instead of being guessed. The stage sequence survives. The sources change.

| Claim | Status | Evidence |
|---|---|---|
| Root entity derivable from the WDL | **dead** | 0 WDL files contain `this.` |
| Root entity derivable from `this.<collection>.<attr>` | **dead** | member type, not root; 0 ambiguous cases so the refusal cannot fire |
| Root entity derivable from the **entity name key** `this.<etype>_id` | **holds** | 28 of 28 cohort launch configs give exactly one name key, 0 give many; 5 of 5 agree with the `rootEntityType` our profile carries today, including the trap: `MergeBatchSites.json.tmpl` → `${this.sample_set_set_id}` → `sample_set_set` |
| Upstream's renderer covers workflows outside the chain | **holds** | `inputs/templates/test/` holds 31 per-workflow template dirs; `build_default_inputs.sh` renders the single-sample one twice, plus 29 Terra configs |
| The renderer supplies launch *values* | **dead** | its Terra half is a placeholder map (`${workspace.*}` / `${this.*}`) that womtool refuses to evaluate, and its test half resolves to another lab's sample: `gs://fc-…` workspace-bucket paths, and 14 `*_docker` bindings including a personal dev image |
| Attribute names have no upstream home | **dead** | `inputs/templates/terra_workspaces/{cohort_mode,single_sample}/workspace.tsv.tmpl` are the rows Terra imports as workspace attributes |
| Most launch-time values are workspace attributes, not file values | **holds** | 91 `${this.*}`/`${workspace.*}` placeholders in the 148-binding single-sample config; the 131/147 figure is specifically about image bindings and is labeled as such here |
| Dockstore keys on workflow *name*, not path | **holds** | unchanged |

The name-key rule is the replacement, and it is testable offline against a tree from `git archive`: for
each launch config, the entity type is the single `<etype>` appearing as `${this.<etype>_id}`; refuse if
there is none, or more than one.

## Stage A0 - the census, with the right artifact

Corpus: `inputs/templates/terra_workspaces/**` at the ref being launched (renderer-independent read: the
one `{{ … }}`-neutralizing `json.loads` already in `terra/batch_configs.py`). Not `wdl/*.wdl`.

Deliverable: per workflow, the name key, the member collections it also reads, and whether the name key is
single. Revision 2 already contains that census for the cohort flavor (30 config templates under
`workflow_configurations/`: 28 with exactly one name key, 2 output write-back configs with none, 0 with
many). The single-sample config does not sit under `workflow_configurations/` at all, which is itself a
finding: the corpus layout differs per flavor, so the deriver must not assume a path.

Cross-check, and this is the part a checkout cannot answer: Terra's own `rootEntityType` for those configs.
The string appears in no file upstream, so the only authority is a live workspace. That check is a
credential-using read (decision 1) and the name-key rule is not "verified" until it has been run once.

## Stage A - schema from the WDL, entity from the name key

WDL at the ref: declared inputs, optionality, struct shapes, call graph - the half the AST does answer.
Entity type: the name key, refusing on zero or many. Also refuse when a workflow's config is absent from
the corpus at that ref, naming the path looked in.

One guard exists specifically because a plausible wrong entity is a silent wrong answer: the derived type
must be compared against the profile's `rootEntityType` where a profile exists, and a disagreement is a
plan-time error. `steps.py` refuses to fall back to `sample_set` for the same reason; the deriver inherits
that, it does not replace it.

Tests: offline fixtures per shape - one name key, no name key, two name keys, member reads from a different
collection than the name key (the `MergeBatchSites` shape, which is the trap), config absent. Named counted
skip when miniwdl is absent, never a silent zero.

## Stage B - resolve values by bundle, and never from a test sample

Every value is classified by the bundle it came from, because the renderer answers "what did *this values
profile* bind", not "what does this ref bind":

- **Terra-workspace bundle** - the launchable half. `${workspace.X}` is a reference to a workspace
  attribute; `${this.Y}` is a reference to a field of the entity row. These are bindings, not values, and
  they stay symbolic in the plan.
- **Test bundle** (`templates/test/**` resolved against `inputs/values/test_*.json`) - literals belonging
  to somebody else's sample: NA12878's CRAM in a `gs://fc-…` workspace bucket, ref-panel objects carrying
  submission UUIDs. Any value sourced here prints `UNRESOLVED: value belongs to bundle <name>` and is
  never used for a launch.
- **`*_docker` is excluded from resolution by rule.** It arrives only through `--image KEY=REF` or an
  explicit profile key. `batch_rerun_step._stated()` refuses a docker value that was merely *derived*,
  because "which image ran" is a statement about the code, and a rendered tree that happens to carry 14 of
  them must not launder a stale tag into a launch.

`workspace.tsv.tmpl` at the ref is the offline oracle: a `${workspace.X}` binding with no row there is
`UNRESOLVED` at plan time, with no credentials and no network. This is what makes a wrong attribute name a
plan error rather than a launched job with a missing input - revision 1 had listed that protection as
needing invention.

Acceptance, restated: for a workflow outside the chain (`GATKSVPipelineSingleSample`), every required input
is either resolved from the render or named as a workspace attribute that exists in `workspace.tsv.tmpl`.
The old criterion - "the unresolved list is empty" - was satisfied precisely by resolving everything to
someone else's sample, which is the failure this stage exists to prevent.

Also from review, and adopted: name the render destination explicitly. `--render-only` defaults to a shared
work-dir path and `rmtree`s it first, so two callers using the default stomp each other's tree.

## Stage C - what actually changes, no imaginary deletion

Revision 1 promised to "delete the step table". That table is already `profiles/<module>.json` data and the
launcher carries no step literals; the deletion target does not exist. What Stage C really is:

- the launcher's entity/row derivation moves from the profile's typed `rootEntityType` plus
  `GSVTK_BATCH`/`--entity` to the derived name key, with the profile as cross-check;
- a new profile field for **where the entity row comes from** (`entity_row` or equivalent). `STEP_FIELDS`
  has no such field today, and `_row_name` hardcodes `sample_set` behavior. Under §9 step 5 that is the
  wrong field to be missing, and it inherits the `bind` ceremony and its own probe;
- this is deliberately declared as **failing §9 step 5's two-changes test** (deriver, resolver, launcher
  generalization, plan mode, new field), so the schema is wrong for non-chain modules and the fix is the
  field, not a pile of conditionals;
- the empty-that-reads-as-clean guard (§9 step 4's second guard) applies to "this module has no config"
  too.

No guard is weakened. The image-pinning refusal, `--confirm`, read-only mode, the shared-target guard and
explicit `useCallCache: false` are tested in `scripts/selftest.d/rerun.sh` and `scripts/selftest.d/cli.sh`;
Stage C must keep both phases green before any behavior moves, and the phases are named here so the claim
is checkable rather than aspirational.

## Stage D - prove it, cheapest first

1. A body-printing mode for the launcher that POSTs nothing. **Not named `--plan`**: `gsvtk terra plan`
   already means "print the freeze plan" (it dispatches `batch_freeze.py plan`). Any new mode also has to
   be registered in the read-only whitelist and get its own refusal probe - "no POST of any kind" is a
   claim that needs the raiser/recorder test `rerun.sh` already uses, not a sentence.
2. Diff that body against the hand-assembled PR #966 config, **committed first as a redacted golden JSON**
   so the diff runs in a clean checkout. A comparison only the author can perform is not an acceptance test.
3. Only then, with an explicit cost ceiling stated before submission: one real launch outside the chain,
   watched with the existing monitor, outputs fetched and compared. Acceptance is the run producing the
   expected entity output with inputs matching the plan, not the submission being accepted.

Row type, corrected: upstream's single-sample deployment ships `participant.tsv.tmpl` as a name key only,
while `sample.tsv.tmpl` carries `sample_id, bam_or_cram_file, bam_or_cram_index, participant,
case_stripy_file` - the exact attributes that workflow reads through `this.*`. So the row is `sample`-rooted
and `participant` is the link column; the freeze-loop blocker is a sample-row problem, not the
participant-row problem revision 1 described. Which row a live workspace actually submits against is still
only knowable from that workspace.

## Non-goals

Image build/publish; reproducible baselines outside the cohort chain (the `freeze` field, §11 q3); editing
the gatk-sv checkout; any local copy of upstream input files.

## Risks not resolved

- The name-key rule is unverified against Terra's own `rootEntityType` until the credential recon runs. It
  is right on 5/5 of the steps we already launch, which is encouraging and not the same as verified.
- Upstream ships entity write-back configs for only 2 of its 28 launch configs, so "outputs come from the
  ref" covers a fraction; the output bindings stay hand-carried.
- The single-sample Terra config binds one key twice with different values (`mei_bed`), so a strict
  `json.loads` and a first-wins reader disagree silently. Any structural read must pick a side and say so.
- A grammar that treats a value as a path iff it begins `this.`/`workspace.` mis-reads
  `"${[this.a, this.b, …]}"` (five real reads, begins `${[`), which is the grammar Stage B expands with.
- Only the Dockstore-published subset of ~118 WDLs can be launched; Stage A must refuse, not invent an
  address, for the rest.
- Stage C touches the code path that guards money. A mistake there is not a red test, it is a bill.

## Owner decisions

1. The credential-using read: dump the workspace method configs once, to check 28 name-key answers against
   `rootEntityType`. Read-only; the offline oracle above shrinks what this is needed for, not whether it is
   needed.
2. One real launch in Stage D, with a cost ceiling stated before submission.
3. Whether a reproducible baseline outside the cohort chain is in scope, or launching against an existing
   workspace is the requirement.

## Review record

Charters: money/blast radius, derivation claims, repo-rule and verifiability consistency. Outcomes:
derivation **DOES-NOT-SURVIVE** (both criticals above), repo-rules **SURVIVES-WITH-CHANGES** (Stage B's
value source, Stage C's nonexistent deletion, §9 step 5 not invoked, acceptance criteria not checkable in a
clean checkout, `--plan` name collision). The money charter returned no findings - the agent had no shell
and stalled - so it is re-run rather than counted as passing. Method caveat on the derivation review: its
per-file content claims were read from the local checkout and re-verified here; its file *enumeration* came
from a public GitHub API pinned to the same sha, which was outside the no-network instruction and is noted
rather than hidden.
