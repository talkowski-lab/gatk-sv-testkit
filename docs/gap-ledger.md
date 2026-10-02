# Gap ledger: what the three GAP-REVIEW memos asked for, and where it landed

The three gap reviews (`GAP-REVIEW-manta-tloc.md`, `GAP-REVIEW-single-sample-blocking.md`,
`GAP-REVIEW-trio-calling.md`) and their synthesis were working notes, and the commit messages that
shipped this work cite them by section. This page is the tracked counterpart: one row per memo item,
what happened to it, and the command that proves that claim from a clean checkout. It exists because
"every tracked gap closed" is only useful if somebody else can check it, and because a claim with no
verifiable counterpart is the same failure this repo keeps writing rules about.

Scope came from one answer, not from this file: the approved work was **the 17-item list plus C2, A6
and A7**. Rows outside it say so, and say what would move them, rather than being quietly dropped or
counted as done.

Verification commands run offline. Nothing here needs data, docker, a network or credentials; where a
tool's other half is cloud-side, the row names that half and the guard logic proven in its place.

Every command here is written the way the rest of the docs write them: a path plus flags, and every
entry point is tracked executable, so `./checks/wdl_semantics.py --selftest` works from a clean
checkout. Two interpreter facts, both exported by `kit/config.sh`, which exports them and activates
nothing: `$GSVTK_GATK_SV_CHECKOUT` is the gatk-sv clone the rows below point `--dir`/`--repo` at, and
`$GSVTK_PYTHON` is whatever it resolved: the first of `python3` / `python` on your `PATH` that is 3.9+
(`kit/config.sh:14-21`). It does **not** resolve `./.venv`, so saying "source `kit/config.sh` and the
interpreter is taken care of" would be a lie: the venv is used only where a tool or the Makefile names
it. A bare `./terra/batch_peek.py` therefore runs under whichever `python3` your shell reaches first,
and for the tools whose packages live in `./.venv` that means the dependency guard prints instead of the
assertions running (`firecloud` for `terra/`, `miniwdl` for `checks/wdl_semantics.py` and
`replay/build_inputs.py`). Run those as `$GSVTK_PYTHON ./terra/batch_peek.py --selftest` after
`source .venv/bin/activate`, or name the interpreter. The gate takes a different knob again:
`PYTHON=… bash scripts/selftest.sh` (`scripts/selftest.sh:29`), and that is the only way its no-miniwdl
SKIP branch can be exercised at all, and that matters: on a machine with miniwdl installed,
those two SKIP lines never print.

This page is graded too: `scripts/check_doc_flags.py` runs in the gate and fails the build if any
command here names a tool that does not exist, uses `./x` on a file without the executable bit a clean
checkout would get, or types a flag that tool's own `--help` does not offer.

## Add

| # | Item | Status | Proof |
|---|---|---|---|
| **A1** | Four count columns in the gate: (a) workflow-scope `write_*`, (b) `File` input referenced only inside `defined(...)`, (c) order-aware `set -o pipefail` + early-exiting consumer, (d) render each task's command and `bash -n` it | **done**: the four rules live in one checker (`WRITE-SCOPE`, `DEFINED-ONLY`, `PIPEFAIL`, `SHELL-SYNTAX`, plus `LOAD-FAILURES`), printed per ref as a `SEMANTICS` row with a delta, and `--strict` exits 1 on a rise | `./checks/wdl_semantics.py --dir "$GSVTK_GATK_SV_CHECKOUT" --summary-only`; `bash checks/wdl_gate.sh <sha>` (refs are positional; `origin/main` if omitted); `./checks/wdl_semantics.py --selftest` |
| **A2** | Second verb on the same renderer: extract one named task's rendered command block | **done**: `--block TASK` prints the render (`--raw` for the literal WDL text), which is the entry point for "run this real block in a container" (`checks/image-check/run_in_image.sh --probe`) | `./checks/wdl_semantics.py --dir "$GSVTK_GATK_SV_CHECKOUT" --block CondenseReadCounts` (a task that exists upstream; the first draft of this row named `Condense`, which does not) |
| **A3** | `checks/image-check/run_in_image.sh`: `--image REF` + caller probe, refuse a 0-byte script / missing image / zero-result arm | **done, and the refusals are the proven part**: no instance has been created by any check in this repo; the guards that were fooled in two reviews fire before a VM exists | `GSVTK_... --image ''` etc. are pinned in `scripts/selftest.sh`, with the passing `--dry-run` case as the control; `docs/static-checks.md` names the unexecuted half |
| **A4** | Terra read path that survives the lying endpoint, then one bounded peek | **done**: `terra/terra.py::_read_both` asks raw REST *and* `fapi` on every listing, prints the disagreement with both counts, raises naming the endpoint on a quiet empty; `terra/batch_peek.py` is the peek (call tally, attempt/retry counts, first failure, `rc`-object tally over the scratch prefix) | `./terra/batch_peek.py --selftest` (58 assertions, injected transport); probes `listing_lie`, `recon_empty_inventory`. `--attempt` selects the record, so the preempted attempt-1 of a call that came back on attempt-2 is reachable. That is the log A5 was asked for |
| **A5** | Fetch artifacts by workflow output *name*, plus one task-artifact read (`stderr` tail, rendered `script`, `attempt-N` layout) | **done**: `terra/fetch_outputs.py` resolves the name in Cromwell metadata and separates "never declared" (3) from "declared, no value" (4) from "present" (0), sha256 into an appending manifest, `--verify` exits 6 on drift; `batch_peek.py --task` reads the artifact rather than printing its path | `./terra/fetch_outputs.py --help` + its 13 gate assertions; `./terra/batch_peek.py --metadata <dump> --task <Call> --no-scratch`; probe `task_artifact_tail` |
| **A6** | Single-artifact readers + declared per-arm invariants | **done**: `compare/artifact_tally.py` tallies one file, asserts header facts, exits 2 on zero records; `--invariant` measures a declared expectation over every row and FAILS (exit 1) instead of skipping; `--qc-def` names the file a verdict's thresholds came from and never opens it | `./compare/artifact_tally.py --selftest`; the elif defect, on any tallied VCF: `./compare/artifact_tally.py run.vcf --info SVTYPE --invariant 'info=SVTYPE=DEL:9' --invariant 'info=SVTYPE=BND:1'` prints VIOLATED *and* the second measurement, rc 1 |
| **A7** | Reachability: given a changed file, list the workflows/tasks that would run it (and the reverse) | **done**: `checks/wdl_reach.py`, both directions, script edges included, `imports-outside`/`unresolved-calls` printed so a missing edge is visible. Not in `make test`: a full-tree parse costs ~34 s, which is the right price for a question and the wrong price for a gate | `./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --reverse --target Structs.wdl`; `--selftest` is in the gate |
| **A8** | Derived facts, not transcribed ones (production python pins) | **done**: `scripts/prod_pins.py` reads the pins out of gatk-sv's dockerfiles *at a ref* with `git show` (the clone's tree, index and HEAD are never touched). No version literal in the runtime path | `./scripts/prod_pins.py --repo "$GSVTK_GATK_SV_CHECKOUT" --ref origin/main`; `--selftest` asserts the full count across a backslash continuation. The *refuse* half is met by refusal and says so: no checkout means no answer rather than a remembered one. `terra/batch_check_inputs.py::required_inputs` is the same rule for womtool: it raises naming `WOMTOOL_JAR=` and the download instead of guessing a REQUIRED set, because a missing call-input binding does not fail typecheck, it fails at input-resolution time mid-submission |
| **A9** | `jinja2` + `PyYAML` in `requirements-dev.txt` | **done**: added on a measured `ModuleNotFoundError`, then actually installed (`jinja2 3.1.6`, `PyYAML 6.0.3`). No tool imports either yet; they are here so the next template renderer or `qc_def` parser cannot fail mid-run | `./.venv/bin/python -c 'import jinja2, yaml'` |
| **A10** | Troubleshooting rows keyed on verbatim error text, + the CI triage row | **done**: 13 rows, each string verified absent from the doc before it was added, meanings taken from the memos | `grep -c '^|' docs/troubleshooting.md`; `scripts/check_docs.py` (in the gate) keeps quoted code honest |

## Change

| # | Item | Status | Proof |
|---|---|---|---|
| **C1** | Flip `"useCallCache": True`; make it a command-line choice | **done**: `--call-cache` / `--no-call-cache`, default off; both on one line is a refusal, because argv order must not decide which arm of an A/B reuses calls | probe `call_cache` (control: the flag reaches the body `batch_rerun_step.py` POSTs) |
| **C2** | Validate config **output** names against the workflow's declarations, offline | **done**: `batch_configs.py check --against <ref>` compares workflow-level outputs and keeps three states apart: `UNDECLARED` (nonzero), `NO OUTPUTS BOUND` (nonzero, legal to Rawls, writes nothing back), and a workflow declaring none at all (disclosed, never enumerated per key). `--config` takes a shipped name *or* a path to any exported config JSON, resolving the workflow from its own `methodRepoMethod` | `./terra/batch_configs.py --selftest`; a config with one invented key returns `out UNDECLARED <Wf>.not_a_real_output`, rc 1. Run against this repo's own defaults and it reports four real problems: see *Known drift* below; the check is right and the map is a snapshot |
| **C3** | Build the narrow derived slice of `docs/module-profiles.md` §9 steps 0-4 (one source for step→workflow/entity/export/attribute-suffix, bindings parsed from gatk-sv's own `.json.tmpl` at a ref, lock sidecar for the diff) | **out of the approved scope**: the answer was "the 17-item list plus C2, A6, A7". The step→workflow half does have a single source now (`terra/steps.py`, three copies removed = R1), which is the part A6/R6 needed to be trustworthy. What is *not* built is the entity/export/attribute-suffix slice derived from upstream templates, and the profile therefore still has hand-written prose behind it | If it is ever approved, the shape is fixed by the memo: derive from the templates at a ref with a lock file, and print "not derived from upstream" rather than transcribing (R6). `scripts/fetch_wdl.py` and `prod_pins.py` are the two existing models for reading upstream at a ref |
| **C4** | Make refuse-to-score-empty a repo rule, with per-site probes | **done**: `CONTRIBUTING.md` now carries it as "A check that cannot fail is not a check", with the positive-control requirement beside it; the per-site probes are `recon_empty_inventory`, `listing_lie`, `task_artifact_tail` and the comparators' zero-record exits | `grep -A3 'Refuse to score' CONTRIBUTING.md`; `./scripts/probe_fixes.py` (20 probes, each with its control) |
| **C5** | Widen the gate's `REF` column (a real branch name overflowed `%-16s` and misaligned a table people screenshot into PRs) | **done**: `%-26s`, and the ref is printed as `<branch>@<sha>` without relying on the width | `head -1` of `bash checks/wdl_gate.sh <sha>` (refs are positional; `origin/main` if omitted) output stays aligned with a 26-column ref |
| **C6** | Adopt the placeholder + legend convention as the house rule for handoff/review prose | **done**: written into `CONTRIBUTING.md` ("Coordinates: placeholder plus legend, not redaction-by-vibe"), including where the line falls (workspace names kept, object UUIDs replaced) and why `make audit` cannot arbitrate the rest | `grep -A4 'placeholder plus legend' CONTRIBUTING.md` |

## Remove

| # | Item | Status |
|---|---|---|
| **R1** | Three copies of the step→workflow map | **done**: `terra/steps.py` is the single map; `batch_check_inputs.WDLS` deleted; a lookup that misses names the step and the patterns it tried instead of dying as a bare `StopIteration` (probe `step_lookup_names_itself`) |
| **R2** | `replay/build_inputs.py`'s hardcoded `SVShell.` prefix (5 sites) and the private `#961` rename | **done**: the prefix is the loaded root workflow's own name plus a dot (measured: `['SVShell']` → `['GATKSVPipelineSingleSample']`, same 20 leaf names and values); the rename and `--translate-rd-keys` are gone, leftovers surface as `task_args_not_in_wdl` |
| **R3** | `replay/single_sample_extra_inputs.json` → `docs/archive/` | **done**: a captured run's inputs are an artifact, not a template; refs fixed, and the one remaining mention is the as-run checkpoint table, which is history (that tree is ignored session material now, not a tracked path) |
| **R4** | The `"useCallCache": True` literal | **done**: see C1 |
| **R5** | Delete the transcribed profile example in `module-profiles.md` §3 once derived profiles exist; shrink the unbuilt remainder regardless | **out of the approved scope, and correctly sequenced behind C3**: the memo's own condition is "once derived profiles exist", and they do not (see C3). Deleting the prose now would leave the facts with no home. The review cost it exists to prevent is real and is recorded in `CONTRIBUTING.md`'s derived-facts rule instead |
| **R6** | `"upstream says nothing here"`-style module literals; no sixth `CONFIGS` entry for single-sample, no per-module comparator ahead of C3 | **done**: none added. Where upstream does not carry a fact, the tools say the fact is absent rather than inventing one (`batch_peek.py` refuses to compose a call dir, `fetch_outputs.py` refuses to guess a bucket, `prod_pins.py` reads the pin or fails) |

## Known drift in the shipped defaults, found by C2, deliberately not "fixed" here

`./terra/batch_configs.py check --against origin/main` exits 1 on **this repo's own** shipped map. The
same four problems appear at the frozen baseline ref `e1909d2fa646`, so it is not one branch moving:

| kind | what `CONFIGS['10-GenotypeBatch']` binds | what upstream declares now |
|---|---|---|
| input | `GenotypeBatch.training_vcf` | no such input |
| output | `GenotypeBatch.genotyping_rd_depth_table` | merged back: `genotyping_rd_table` |
| output | `GenotypeBatch.genotyping_rd_pesr_table` | merged back: `genotyping_rd_table` |
| output | `GenotypeBatch.genotyping_sr_cutoff_diagnostics` | no such output |

The names were never invented. In gatk-sv, `git log -S genotyping_rd_depth_table` shows them added by
`f5201550` ("Update pesr/depth separate tables") and removed by `e46faff8` ("Train batch genotyping on
the batch's own calls and gate on cutoff quality"); today's `GenotypeBatch` declares
`genotyping_rd_table`, `genotyping_pe_table`, `genotyping_sr_table` and
`genotyping_reclassification_table`. This repo has carried the pre-rename names since its initial commit
`cdb6444`, which is what `CONFIGS` is documented to be: its own docstring says the input maps are "a
SNAPSHOT of one branch's WDL signature, while GSVTK_BRANCH [moves]".

Why the map is not edited here: which binding is correct depends on the workflow version a config's
`methodRepoMethod` resolves to, and `batch_configs.dockstore()` pins that to `$GSVTK_BRANCH`, *your*
branch, which this repo cannot know. Deleting a key your branch still declares loses a write-back just
as quietly as keeping one it no longer declares, and neither failure is visible at submission time.
So the tool says it per ref, every time, and `show` prints the JSON that would be POSTed. Rawls accepts
a config whose outputs name nothing real (`invalidOutputs: 0`); the run then finishes and saves nothing,
which is the $18.87 / 20 h incident this check exists for.

    ./terra/batch_configs.py check --against <the ref your branch is built from>

Read it before `show` / `create` for step 10.

## Reading this page as a reviewer

Two things are deliberately *not* claimed anywhere in this repo:

1. **The cloud halves.** No check in this repo has created a VM, submitted a workflow, or copied a byte
   out of a bucket. `batch_peek.py`'s live listing, `fetch_outputs.py`'s real `gsutil` copy and
   `run_in_image.sh`'s boot are proven as *guard logic and rendered commands* against local fixtures,
   and each file's header names the unexecuted path.
2. **Absolute counts.** The semantic counts, reachability counts and pin counts are baselines to diff
   against a base ref. Upstream moves; a pinned absolute count is a check that expires.
