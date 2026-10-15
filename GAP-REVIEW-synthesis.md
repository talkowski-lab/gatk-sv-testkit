# Three gap reviews, one build list

Consolidates [GAP-REVIEW-manta-tloc.md](GAP-REVIEW-manta-tloc.md),
[GAP-REVIEW-single-sample-blocking.md](GAP-REVIEW-single-sample-blocking.md) and
[GAP-REVIEW-trio-calling.md](GAP-REVIEW-trio-calling.md) — three independent reviews of this toolkit
graded against three real gatk-sv branches (manta tloc auto-resolve, the single-sample blocker PR,
trio calling). Graded against HEAD `9e6ae04`; `make test` green, `make audit` clean, 13 probes.

The filter applied throughout, because it is the project's whole economic argument: **a feature that
only serves one module is a feature that has to be re-synced every time gatk-sv moves.** Items are
kept, cut, or reshaped by whether they survive that question.

## Verdict

All three reviews answer the same question the same way — *the build loop and the static WDL gate
earned their keep; everything else fell outside* — and they were looking at three different modules.
That agreement is the finding. It is not 20 independent missing features; it is four layers, seen
three times.

| Layer | What is missing | Sightings |
|---|---|---|
| **Static** | The gate measures *bindings*. The defects lived in WDL *semantics* and in the *rendered shell* — where both WDL validators return `Success!`/`rc=0` on a tree that cannot run. | trio §"static layer" (3 defects, 2 prototyped), single-sample §1 + §3.2–3.3, manta §1 |
| **Terra reads** | The listing endpoint this repo's own docs say lies is the only path three tools use; the config/entity/attribute vocabulary is hand-transcribed for steps 06→10; artifact fetch reads `sample_set` attributes by name. | all three (the endpoint is a **3/3** sighting, and it returns a wrong answer, not a missing one) |
| **Images** | The only oracle for "does the image actually contain/run this" is two VM skeletons whose probe bodies are hardcoded. Three of five branch defects were invisible to every offline check and visible only inside a container. | manta §2.4, single-sample T5/T14/rec 3, trio task 11 |
| **Evidence** | Comparators are pairwise and genotyping-named; there is no single-artifact tally, no per-arm declared invariant, and no home for tooling a head-to-head needs that the repo does not have. | manta §2.3 + §5 (root cause), trio recs 8/9, single-sample T29/T32–34 |

Two of the three reviews noticed the toolkit's *rules* were not applied to their own branch: 13 probes,
none covering any of the six defects they described. That is `CONTRIBUTING.md`'s own bar, unpaid.

## The one rule behind most of the individual failures

Six separate incidents in three reviews are the same bug wearing different clothes:

> a check that measured nothing reported success.

- both arms died `rc=1`, `records=0` → printed as "the flag changed nothing" (manta §2.4)
- a comparator asserted an invariant that was false in the baseline, an `elif` chain skipped the
  remaining checks for exactly those samples → false `REVIEW` over good data (manta §2.3)
- `bash -n` passes on an empty file, `grep -c` returns 0 for a one-line file → both guards approved a
  **0-byte startup script**, ~20 idle VM-minutes (manta §2.4)
- `list_submissions` → `[]` → `recon` prints "0 recent" and **exits 0** (manta §3.1, reproduced here)
- a 2-byte `baseline_configs.json` (manta §3.1 neighbour); a config POST that persists with `valid: None`
- `fetch_baseline.py`'s unnumbered prefix never matches → a manifest with no entry, printed and continued
  (`module-profiles.md` §9.4)

`compare/artifact.py` already states the cure — `compared_something: false` means exit 2, "an empty
result is never silent" — for comparators only. **Generalise that from a comparator convention to a
repo-wide rule**, enforced per call site: no arm is scored unless it produced a nonzero, attributable
amount of something; no invariant is asserted unless its violation *fails the run* rather than skipping
the rest of its block; no list is summarized as a count without naming which endpoint answered it. This
is not a tool; it is the reason five of these reviews' anecdotes cost money instead of being caught.

---

## Add

| # | Feature | Generic because | Effort | Sightings |
|---|---|---|---|---|
| **A1** | **Four more count columns in `checks/wdl_gate.sh`**, all diffed against a base ref: (a) workflow-scope `write_lines`/`write_tsv`/`write_json`/`write_map`; (b) `File`/`Array[File]` input referenced *only* inside `defined(...)`; (c) `set -o pipefail` + an early-exiting pipe consumer (order-aware: a pipe on a line *before* `set -o pipefail` is not a finding); (d) **render each task's command block and `bash -n` it**. | Runs on the WDL tree. Knows nothing about any module, entity, step, or attribute — so it cannot drift out of sync, only out of coverage. | small (a/b ~20 lines each; d ~40) | trio A/B + recs 3/4/5, single-sample recs 1/7, manta (same wall) |
| **A2** | Second verb on the same renderer: **extract one named task's rendered command block to a file**. | It is the entry point for "run this real block in a container", and it is how a branch's own regression test grades the WDL instead of a copy of the block. | small (same code) | single-sample rec 2 / T6 |
| **A3** | **`checks/image-check/run_in_image.sh`**: `--image REF` + a caller-supplied probe script *or* a Dockerfile overlay (`FROM that image` + `COPY` probes + `RUN whatever`) → boot, pull, execute, report rc + bounded stdout + attempt counts, keep the VM, **refuse a 0-byte script and refuse to score a missing image or a zero-result arm**. | The hard parts (VM lifecycle, `docker cp`, serial-log marker, keep-VM-alive) already exist twice with a hardcoded body; the missing thing is the *entry point*, which serves every future "is it in the image / does it run in the image" question. | medium | manta §2.4, single-sample T5/T14/rec 3, trio task 11 |
| **A4** | **Terra read path that survives the lying endpoint**: raw-REST `GET …/submissions` and config listing in `terra/terra.py`, keep `fapi`, **fail loudly when the two disagree**. Then one bounded `peek`: `executionStatus` tally, attempt/retry counts, first failure message, and an `rc`-file tally over the scratch prefix (the liveness signal that survives stale metadata). | Fixes three shipped tools at once (`batch_status`, `latest_workflow`, `recon`) rather than adding one; the peek is per-call, not per-module. | small | **3/3** (trio rec 1, manta §3.1, single-sample rec 4/T28) |
| **A5** | **Fetch artifacts by workflow output *name*, for any workflow**, into a checksummed manifest under `$GSVTK_WORK`; plus one task-artifact read (its `stderr` tail, its rendered `script`, its `attempt-N` layout). | Names come from the run's own outputs instead of a 7-name literal list, and it covers the case where entity attributes stayed empty and only Cromwell's `outputs` had the paths. | medium | manta §1, single-sample T29/T31, trio recs 6/8 |
| **A6** | **Single-artifact readers + declared per-arm invariants**: tally a field over *one* file (VCF INFO/FORMAT census, batch-table column, metrics key coverage), assert header facts (sample column order, `##INFO=` present), read a QC verdict *naming the `qc_def` it came from*, and compare tallies across arms against **declared** invariants that fail loudly. | Every branch re-derived some count from its own VCF by hand, and the invariant discipline is what stops "the baseline must be zero" (false in the production baseline) from silently skipping checks. The CONTROL/EFFECT/PURITY/CLOSURE shape manta needed is one instance of this, not a feature. | medium | manta §2.3, trio rec 9 (MOI recount, 10 PASS/17 FAIL, 0-of-413 metrics), single-sample T32/T33 |
| **A7** | **Reachability**: given a changed file or script, list the workflows/tasks/imports that reach it — in both directions. | Answers blast radius and "does this even reach the module I'm changing" from the import/call graph; it replaces two guesses (which fixes carry to `sv-shell`; which step exercises my new flag). | medium | single-sample rec 10/T36, manta §2.1 (same join, other direction) |
| **A8** | **Derived facts, not transcribed ones**: production python pins read out of gatk-sv's own `dockerfiles/*virtual-env` at a ref (or generate a production-pinned local venv); "what is inside image X" answered by A3 probing the image; a womtool jar fetched + version-checked, or refuse. | The three most expensive "I had to know this" facts in the reviews (pysam 0.15.4 vs a local 0.24; setuptools ≥82 dropping `pkg_resources`; `/gatk/gatk` exists only in `gatk_docker`) are all upstream-owned facts. Copying them into this repo creates the sync tax; deriving them is free forever. | small–medium | manta §2.5, single-sample T2/T3, trio task 6/13 |
| **A9** | `jinja2` + `PyYAML` in `requirements-dev.txt`. | Two lines that remove a second interpreter from "does the workspace template render, and how many rows × attributes?" and make `.dockstore.yml` parseable rather than text-inspected. | trivial | single-sample T17/rec 11, trio rec 13 |
| **A10** | **Troubleshooting rows + CI triage row**, keyed on verbatim text, each with a probe and a positive control. | The doc's entire design is "paste your error into a search box". Verified absent today for 10 of 11 quoted strings: `unexpected json type`, `…is reserved and cannot be overwritten`, `409 … already exists`, `Invalid outputs … Error while parsing the expr`, `invalid choice: 'inputs'`, `[E::vcf_format] Invalid BCF, the INFO tag id=16 is too large`, `rc=125`, `##[error]Readonly file modified: …` (which paths are protected, and that dropping commits does not unpublish Dockstore versions), plus "is this red check shared infra?" = same workflow, same window, another branch. | small | manta §4, single-sample rec 9/T34/T35 |

## Change

| # | Change | Why | Effort |
|---|---|---|---|
| **C1** | `batch_configs.py:319` ships `"useCallCache": True`. Flip the default and make caching explicit (`--call-cache` / `--no-call-cache`). | Two of three reviews found the shipped default actively harmful, for *different* measured reasons: cross-workspace cache copies time out mid-run and cost a Failed submission (single-sample T27, `docs/handoff/003` §"Terra single-sample needs false"), and `docs/terra-head-to-head.md` §5 tells an A/B the opposite for mutable tags (manta §3.2). Today the documented advice cannot be followed with the shipped tool. | trivial |
| **C2** | Validate config **output** names against the workflow's declared outputs, in `check_maps` beside the input check; and extend `check --against` to *any* workflow, not just the five chain configs. | Rawls never validates outputs, so this repo is the only place a stale name can be caught — one review's single largest spend ($18.87 + 20 h reported `Failed` after `Succeeded`, then a resubmit). Generalising `--against` is also the prerequisite for every non-genotyping config. | small |
| **C3** | Build the **narrow derived slice** of `docs/module-profiles.md` §9 steps 0–4, and nothing more: one source for step→workflow/entity/export/attribute-suffix, bindings derived by strict-parsing gatk-sv's own `.json.tmpl` at a ref, a lock sidecar for the diff. Explicitly **not** a hand-typed 100-key map per module. | The three reviews hit this wall from three modules (step 04 sub-workflow, single-sample config assembly, trio config assembly). But a literal profile *is* the sync tax the kit exists to avoid — the profile has to be generated from upstream and locked, or we have traded one drift problem for a bigger one. | large, already designed |
| **C4** | Make **refuse-to-score-empty** a repo rule with per-site probes (see the rule above): recon's lying-empty case, runners' zero-record arms, comparator `elif` chains, manifest entries that silently go missing. | Six measured incidents, one convention, and `compare/artifact.py` already proves the pattern works here. | small |
| **C5** | `wdl_gate.sh`: widen the `REF` column (a real branch name overflows `%-16s` and misaligns a table people screenshot into a PR); keep the default `--wf` set as the script's own list with any derived set *added* (module-profiles §9.4 warns a narrow profile would silently drop three of four). | Both memos that used the gate asked for something outside its default list; one hit the cosmetic bug on the way. | trivial |
| **C6** | Adopt `docs/handoff/003`'s **placeholder + legend table** convention as the house rule for handoff and review prose, so the three reviews stop disagreeing about what may be published. | The reviews already diverged: two redacted branch/project/registry values, one named them. `make audit` cannot arbitrate, because it grades *your machine's* coordinates plus credential shapes — not somebody else's workspace or branch name. | docs only |

## Remove

| # | Delete | Why |
|---|---|---|
| **R1** | The **three** copies of the step→workflow map (`batch_check_inputs.py`, `batch_save_metadata.py`, `batch_status.py`) → one reader. | Not tidiness: `next(c for c in CONFIGS if c.startswith(step + "-"))` raises a bare `StopIteration` on a step it does not know, and `fetch_baseline.py`'s prefix match can never match an unnumbered chain — it prints "no config for step" and writes a manifest with the entry missing. Both are the empty-that-reads-clean class. |
| **R2** | `replay/build_inputs.py`'s hardcoded `SVShell.` prefix (5 sites) and its special case for the gatk-sv #961 RD-table rename. | Derive the prefix from the root workflow of `--wdl`. The rename is *already* `svshell_contract_check.py`'s job; keeping a copy of a past upstream rename inside a generic builder is exactly the recurring sync liability being avoided. |
| **R3** | `replay/single_sample_extra_inputs.json` → `docs/archive/`. | 12 static entries read by exactly one archived as-run script, and it is the file `docs/archive/README.md` had to annotate for shipping a real `gs://` path. |
| **R4** | The `"useCallCache": True` literal (see C1). | |
| **R5** | The transcribed profile example in `module-profiles.md` §3 once derived profiles exist — and shrink the doc's unbuilt remainder regardless. | A `PROPOSED` fence did not stop three reviewers from each reading 44 KB of prose, then rediscovering the same wall and writing it up as new. If a section stays unbuilt it belongs in an issue, not in `docs/`. |
| **R6** | `"upstream says nothing here"`-style module literals in general: do not add a sixth `CONFIGS` entry for single-sample, do not add a per-module comparator ahead of C3. | Each such literal is a value that will silently disagree with upstream the next time gatk-sv moves — and the worst bugs in this repo's history were two places disagreeing about one value (`CONTRIBUTING.md`). |

## Declined — one-off, on the stated criterion

Kept on the record so they are not re-proposed by the next branch review:

- **A single-sample 120-key input JSON generator.** gatk-sv owns `scripts/inputs/build_inputs.py`; ours should render/validate, not re-implement their bundle-dependent resolver.
- **A pedigree validator as a trio feature.** Defect D (non-founder parents → a 2-cycle in `working_ped`) is real and still open, and PEDs appear in more than one gatk-sv module — so make it a small *structural* validator (founders, acyclicity, sex codes, samples present) only if a second module needs it. Not now.
- **A step-04 / TinyResolve rerun config, a trio config, a single-sample config.** Those are C3 outcomes, not features. Adding any of them by hand is R6.
- **Loosening the read-only front door.** Every Terra write in the reviews was a deliberate `curl`, never an accidental wrapper call — that design worked and should stay. The gap was never "writes are blocked", it was "the config builder can only describe five configs" (C2/C3).
- **A generic Cromwell-on-a-VM driver.** Both reviews that hand-rolled one hit *harness* bugs (a `MISSING LOCAL IMAGE` that kept running, `scala.MatchError`, a copy `TimeoutException`). Until there is a second real user, this is a documented recipe plus A3/A4's guards, not a tool.
- **More comparator engines.** Seven shape engines already sit beside six rules; the reviews asked for *declared invariants and single-artifact tallies* (A6), not another diff.

## Sequencing

**First PR (cheap, all verified, no module changes):** A1 (four gate columns) + A2 + A9 + C1 + C5 +
C4's recon fix + A10's rows + probes with controls. Two of the four A1 columns were measured twice
already (trio prototyped and ran them over four refs; single-sample ran the renderer over three), and
I reproduced the renderer finding myself below.

**Second:** A4 + A5 (Terra reads) — one wrong answer under three shipped tools, then the liveness and
artifact reads that every branch hand-rolled.

**Third:** A3 (in-image runner) + A8 (derived pins) — the layer where the defects actually lived.

**Then** C3 (derived profiles), with A6/A7 alongside, because A6's invariant discipline is what makes a
second module's head-to-head trustworthy and A7 tells a branch which module it is allowed to touch.

## Already fixed — do not do this twice

Two reviews flagged **`make audit` red at HEAD** with the handoff-doc/audit tension "still open".
Closed at `9e6ae04`: `docs/handoff/003` is scrubbed to placeholders with a recover-each-one legend, and
`audit` is now one of `make test`'s seven parts rather than a separate target. Verified here: clean at
HEAD, and clean with all three memos staged (99 files, `hits=0`), so they are publishable as written.

## What I checked myself vs. what I propagated

Reproduced in this session, offline, at `9e6ae04`:

- **`recon` exits 0 on a silently empty inventory.** `recon.p('   0 recent -> …')` adds nothing to
  `FAILS`; the same call with `" failed:"` in it does. The configs section a few lines above already
  uses raw REST — so the file knows the rule and applies it to one section only.
- **Both WDL validators are blind to a broken shell block; `bash -n` is not.** On a copy of a real
  gatk-sv tree (`wdl/`, 118 files) with one `)` dropped inside a `command <<<` block in
  `CondenseReadCounts`: `miniwdl check` → `rc=0` with output *byte-identical* to the clean tree;
  render-and-`bash -n` → clean tree 2 failing blocks (both `TasksMakeCohortVcf.wdl`, renderer noise),
  mutated tree 3, the new one being the mutated file. This is why A1(d) ships as a **baseline delta with
  known-noise rows**, never as an absolute oracle.
- `batch_configs.py:319` `"useCallCache": True`; `terra.py:140/149/160` single-path `fapi` listing;
  `CONFIGS` = five joint steps only with five hand-written `outputs` maps; `SVShell.` hardcoded ×5;
  `requirements*.txt` hold no `jinja2`/`PyYAML`; 13 probes, none of them about any defect these reviews
  describe; the image-check skeletons take `--image` but their probe bodies are literals;
  10 of 11 quoted error strings absent from `docs/troubleshooting.md`.
- The miniwdl API the A1(a) scan needs is present in the shipped venv (`doc.workflow`, `doc.tasks`,
  `task.command.parts`, `Apply.function_name` compared as `str`).

**Not** verified here: anything requiring credentials, a VM, or a Terra call. Every claim about a
live run (costs, timings, cache-copy timeouts, the step-04 freeze sizes, the 156×3 label census) is the
reviewers' reported evidence, and this list treats it as such. The two reviews disagree on purpose —
one built its task list from a session transcript (wider, includes harness bugs), one from handoff docs
only — and the manta review's redaction discipline is the better model for a file that gets published.
