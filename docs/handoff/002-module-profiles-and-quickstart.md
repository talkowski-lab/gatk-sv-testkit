# 002 — Module-profiles design, three-way adversarial review, the step-0 fix, and a quickstart

**Session date:** 2026-09-24 (`date -u` on the working box: 18:15:33Z). Branch `main`, HEAD before this
session's work: `5f40a03`. Nothing was committed and nothing was pushed during the session itself.

## 1. What the user asked for

Three requests, in order:

1. **"i'm looking over the documentation. I think there is some tooling/documentation that is specific to
   certain modules, e.g. Genotyping. It is fine to use that as an example, but we should make the toolkit
   more general overall (supporting any module in gatk-sv) without it becoming too bloated/interdependent
   with the gatk-sv code base itself. How can we do this?"**
2. Mid-design, added constraint: **"it should be a friendly user experience as well. this is intended to
   be used by agents as well as humans"**.
3. Scope chosen from a 4-option question, answered free-text: **"write a design doc then send it through
   adversarial review with 3 subagents"**.
4. After the review landed: **"do (a). then show me a human readable quickstart guide that showcases
   core functionality"** — where (a) was *fix the `check_maps` crash (step 0) with a probe*.

**"Show me" was satisfied by a pasted tour in chat.** The user's only later reply was `commit`: the work
was accepted into the repo, and nothing was said about the tour's content — so nothing here treats it as
read or endorsed. The final instruction was the handoff itself. **No approval was given for anything**
beyond the commits, so nothing in §4 or §12 may be treated as sanctioned.

## 2. Files (all still uncommitted at freeze time — verify with `git log` after the push step)

```
 M README.md                      one paragraph: pointer to docs/quickstart.md
 M terra/batch_configs.py         the step-0 fix (check_maps 3-segment crash) + docstring
 M scripts/probe_fixes.py         + probe_nested_bindings (registered LAST on purpose)
 M scripts/selftest.sh            pinned probe count 12 -> 13, with the reason
?? docs/module-profiles.md        547 lines  — the design doc, revision 2
?? docs/quickstart.md             326 lines  — the guided tour
?? docs/handoff/002-…-quickstart.md  ~275 lines — this file, written last
```

`git status --short` at freeze time showed exactly those seven lines.

## 3. The live defect — found, fixed, pinned, falsified

**`terra/batch_configs.py` `check_maps()` crashed on any 3-segment binding key.**
Old lines 492-495: `bound, nested = {}, []` then `(nested if len(parts) > 2 else bound).update({…})` —
a list has no `.update`. Verified live before fixing: injecting one `Workflow.Call.input` key and
calling `check_maps` against a real `git archive` of `main` raised
`AttributeError: 'list' object has no attribute 'update'`.

Consequences, all CONFIRMED: the "N nested-call binding(s) not checked" report was unreachable;
`preflight()` (which `create` and `validate` run) refused by crashing rather than reporting; leaf-keying
via `dict.update` silently collapsed two call bindings sharing a leaf name. Reachability is not
hypothetical: **5** such bindings in gatk-sv `inputs/templates/terra_workspaces/single_sample/GATKSVPipelineSingleSample.json.tmpl`
(`GATKSVPipelineSingleSample.MakeCohortVcf.HERVK_reference`, `…RefineComplexVariants.n_per_split`,
`…AnnotateVcf.par_bed`) and **80** in `inputs/templates/test/GATKSVPipelineBatch/GATKSVPipelineBatch.json.tmpl`.

**Fixed:** `nested` is a dict keyed on the full key → `{full key: callee}`; each un-compared binding is
printed by name with the callee (a count is a footnote, a named key is findable); reported WITHOUT
failing the check, because a call binding is legitimate wiring; `check_maps` docstring corrected.

**Probe:** `probe_nested_bindings` in `scripts/probe_fixes.py`. Registered **last** in `PROBES` because
it injects a key into `batch_configs.CONFIGS`. Pinned count raised 12 → 13 in `scripts/selftest.sh`.
**Falsified before being trusted:** reverting the two lines made the probe fail with
`FAIL  nested_bindings  AttributeError: 'list' object has no attribute 'update'`; the file was then
restored byte-identical (sha256 compared). The probe asserts in both directions: no call binding → no
report (so the count can't be a constant), and call binding present → `EXTRA`/`MISSING` still fire (so
the fix cannot suppress findings).

## 4. Two DEFECTS STILL OPEN, found in shipped code and deliberately NOT fixed

Both were reported to the user; neither was authorized, and neither has a probe.

1. **`scripts/selftest.sh:204` counts SKIPPED probes toward the pinned count** — **FIXED later in this
   session** at the user's request; the text below is the original finding, kept as it was written. The
   comparison was `if [ $((run_n + skip_n)) -lt "$want" ]`, while its own comment at `:186-188` claimed a probe "quietly skipped for a
   missing dependency lowers it" and that "'8 ok' versus '3 ok, 5 skipped' is the difference between a
   gate and a rumour". The code counts a skip as accounted-for. This is load-bearing for the design
   doc's §5/§7: CI checks out **only this repo** (`.github/workflows/ci.yml` installs both requirements
   files, so miniwdl IS present, but nothing sets `GSVTK_GATK_SV_CHECKOUT`), so any clone-backed
   per-module probe satisfies the count by skipping.
   *How it was closed:* `probecount` now compares `run_n` alone against `want` and prints which file to
   install. The CI-shape worry dissolved on contact with the workflow: `ci.yml` installs **both**
   requirement files, so CI already runs 13/13 and requiring all of them to *run* costs it nothing. What
   actually changed is the fresh-clone path, where the opposite half of the same bug lived: a probe that
   needed an optional tool failed outright instead of skipping, so `make setup` (runtime requirements only
   — miniwdl is a dev requirement) was enough to make `make test` red. That probe now SKIPs naming
   `requirements-dev.txt`, and the counter refuses to pass on the smaller number. Verified both directions
   in a throwaway clone of the pushed repo: without the dev file the gate fails with the remedy printed;
   with it, `make test` PASS, 0 failed. `docs/setup.md` gained a section, `make setup` and `make help`
   gained the line.
2. **`checks/wdl_gate.sh:18`'s documented example names a workflow that does not exist at `main`** —
   `--wf ResolveCpxSvGenotyping`; `git -C <checkout> cat-file -e main:wdl/ResolveCpxSvGenotyping.wdl`
   fails, and this script is written to report `ABSENT at <sha>` + exit 1 for exactly that. Violates
   CONTRIBUTING's "never document a flag you did not run" (applied to example workflow names).
   *Why not fixed:* one-line doc edit, but it changes an example the user may have copied; and I stopped
   editing code at the authorized scope (the crash + its probe).

## 5. The design doc: `docs/module-profiles.md`

Proposal only — **nothing in it is implemented**, and every command in its `PROPOSED` blocks names a
flag/file that does not exist. Its model: three layers, dependency one way (loops → profiles → the ref,
read with `git archive` + miniwdl, never vendored), and **a different oracle per question**: miniwdl for
existence/requiredness/type, gatk-sv's `<Workflow>.json.tmpl` for what production binds, and nothing
except the template for *which attribute or literal feeds an input*.

Rev 1 was reviewed by three independent adversarial subagents; **all of their material claims were
re-verified here before acceptance**, and four of my own load-bearing claims were overturned. §13 of the
doc publishes each one. The two that changed the design:

- **"Templates are not parseable as JSON" was wrong.** One substitution and 28/28 parse:
  `json.loads(re.sub(r"\{\{.*?\}\}", "null", text, flags=re.S))`. 8 of 28 carry Jinja. I had hit
  `JSONDecodeError: … line 11 column 34` in `GenerateBatchMetrics.json.tmpl` (`chr_x` is
  `{{ reference_resources.chr_x | tojson }}`) and generalized to rejecting the source. This mattered:
  **196 of 473 template bindings (41%)** point at an attribute whose leaf name differs from the input
  name (`median_coverage←this.median_cov`, `rd_file←this.merged_bincov`, `batch←this.sample_set_id`),
  so miniwdl cannot produce the binding map and refusing the templates would have made the profile
  hand-carry ~100 bindings — transcription with a schema and gates around it. The *correct* reason not to
  render is that gatk-sv's renderer is bundle-dependent (`scripts/inputs/build_inputs.py` skips a
  template with any undefined value; defaults `ref_panel → ref_panel_empty`) and needs `jinja2`.
- **My own measurement method was the bug class the repo documents.** My key-regex counted 12 keys where
  `FilterGenotypes.json.tmpl` binds 10 (`mem_gb`/`disk_gb` are a nested object). `batch_configs.py:436-440`
  refuses regex for exactly this reason. The 63/64 headline still reproduces exactly; the method
  was not portable. Strict parse only.

Two reviewer numbers did **not** reproduce and were corrected rather than cited: **196** differing-leaf
bindings (reviewer said 212), and "miniwdl isn't installed in CI" (it is — CI installs
`requirements-dev.txt`; what CI lacks is a gatk-sv checkout).

Other review-forced changes, all verified here before acceptance: `bind` must be a complete map
(`TrainGCNV` template binds **53**, its WDL requires **12** — the delta reading silently drops 41
production-pinned knobs and grades clean); `wdl` and `workflow` are separate fields (**12** of 118 WDLs
differ, e.g. `DepthClustering→ClusterDepth`, `PloidyEstimation→Ploidy`, `Genotype_2→Regenotype`);
**32** of 118 WDLs are Dockstore-published (`.github/.dockstore.yml`), so an unlisted workflow grades
green then 404s; index/`_index` closure belongs in code (my example `freeze_attrs` expanded to **9** not
17, silently unfreezing 8 sidecars); `_why_*` pseudo-keys carry rationale (the repo's existing convention
in `replay/images.example.json:2-7`, which my "no comments" line ignored); grading moves to a lock
sidecar because CI has no checkout and skips satisfy counts; `verified_against` per **arm**, not per ref
(every loop runs two refs; one ref is a defect already pinned by `_posted_ref()`); four of the seven
proposed gates were theatre and were replaced with a behavioural probe; the second module ships as
"profile + ≤2 named code changes", not "zero new Python" (already false for both candidates: `TrainGCNV`
needs member-entity freeze **and** a comparator that does not exist; the CPX pair draws on ≥6
out-of-chain producers).

**UX (§6), driven by the "friendly for agents and humans" instruction:** rev 1 proposed three new
commands; review killed them and it was right — `gsvtk tools`, `gsvtk-config show`, `doctor` and `stamp`
already answer those questions. The surviving design: `MODULE`/`MODULE_DIR` registered in
`kit/gsvtk-config`'s `DEFAULTS` so `show` answers "which module, and why that one"; `exit 4` naming the
complete field list (rev 1's error text named 5 of 9 fields); `_why_*` so `cat profiles/<m>.json` *is*
the human view; provenance on every verdict via the existing `stamp`.

**Open before any implementation:** §11 q5 — `profiles/*.json` (needs a second, independent `{frz}`/
`{new}`+`@`-expander in bash) vs the repo's existing `KEY=value` grammar (`kit/gsvtk-config:96-98` parses
it once for both languages by explicit design). This can change the shape of the whole design.

## 6. The quickstart: `docs/quickstart.md`

326 lines, linked from README. **Every command in it was actually run in this session** (CONTRIBUTING's
hard rule), and its outputs are real captures — abridged where long, with project / workspace / registry
values replaced by `YOUR_*`. Verified: `make audit`'s own pattern list returns clean against both new
docs, and no absolute home paths appear.

Covers: config resolution + the exit-4-names-the-key behaviour; `gsvtk tools` dependency resolution
(this machine: `WOMTOOL_JAR unset/missing`, i.e. replay is partly blocked); `checks/wdl_gate.sh
origin/main` real table + the "findings are a diff" and "a workflow you didn't check is a failure"
rules; `svshell_contract_check.py` (6 unsupplied-read findings) beside
`svshell_jq_plumbing_scan.py --compare-to main` which prints `=> OK: … no new nulls introduced` — same
tree, two verdicts, both correct; `batch_configs.py check --against main` naming
`GenotypeBatch.training_vcf` as a KNOWN branch-only input; the read-only refusals (`create` without
`--confirm`, `batch_freeze.py plan` naming the missing recon dump, unset target → exit 4); the six
comparators plus the differ's empty-input honesty (`MISSING=6`, "compared nothing", exit 1);
`docker/gatk-sv-build.sh --check` (`error: branch 'my-branch' not found on
https://github.com/broadinstitute/gatk-sv`) and `--dry-run` with the cost stated; the pinned probe
count; the skill wrapper's executed refusal of `terra create`.

I corrected two of my own overclaims while drafting it: `make setup` is not offline (pip needs the
network), and `--check`/`--dry-run` do make read-only gcloud + GitHub calls.

## 7. Verification state (re-run to confirm, do not trust these lines)

| Claim | Verify with | Status at freeze |
|---|---|---|
| The offline gate passes | `make test` → `PASS (offline gate)`, `selftest: 20 ok, 0 skipped, 0 failed` | ran, PASS |
| 13 probes, 0 skipped | `python scripts/probe_fixes.py` → `probes: 13 ok, 0 skipped, 0 failed` | ran, 13/0/0 |
| The new probe detects the regression | revert the two lines in `check_maps`, re-run, expect `FAIL nested_bindings AttributeError` | ran, FAILed as expected; file restored sha-identical |
| Docs structurally sound + links resolve | `python scripts/check_docs.py` → `21 file(s) … resolves` | ran, rc 0 |
| No internal identifier in publishable set | `make audit` → `clean`, over **83** tracked files, which now includes all three new docs | ran, clean |
| Undefined-module-reference sweep | part of `make test` (`undefmods: 27 files`) | ran |
| The handoff's four commits reached the remote | `git fetch` + `git log origin/main..HEAD` (empty) + `git show origin/main:docs/handoff/002-….md` | ran at handoff time; **a future session must re-run it** — see §10 |

`make test` runs with a real gatk-sv checkout present on this machine, which is why clone-backed selftests
report `ok` rather than SKIP. That is exactly the CI-vs-local gap in §4 defect 1.

## 8. What is DONE vs PENDING

**Done + locally verified:** the `check_maps` fix, its probe (with falsification), the pinned-count bump,
design doc rev 2 with every reviewer claim independently re-verified, the quickstart with every command
executed, README pointer.

**Pending, not started, not authorized:** fixing §4's two open defects; implementing any part of the
design doc (step 0 was explicitly scoped as the only code change authorized); deciding §11 q5 (JSON vs
`KEY=value`).

**Done as the handoff step, not during the session:** the six paths above were committed as four commits
(fix + probe; design doc; quickstart + README pointer; this file) and pushed to `origin/main`. Verify with
`git log --oneline -5` and `git log origin/main..HEAD` (the latter must be empty). `make audit` was clean
over 83 tracked files, which is the first time the two new docs were inside the publishable set.

**Needs the user's decision, not mine:** (i) whether the two new docs should be restructured after they
are read (they are committed but unread by the user), (ii) §11 q5, (iii) ~~whether to fix the
`selftest.sh` skip-counting~~ — decided and done later in the session, and it did not reshape CI (§4.1),
(iv) whether to fold the three review reports into the
repo (paths in §9; they are retention-managed session artifacts, so copy them in if they matter).

## 9. Artifact paths

Repo-relative: `docs/module-profiles.md`, `docs/quickstart.md`, `terra/batch_configs.py`,
`scripts/probe_fixes.py`, `scripts/selftest.sh`, `README.md`.

Three review reports, under the agent home (`~/.pi/agent`), in the per-project sessions directory whose
name encodes this checkout's path — `sessions/--<project-slug>--/subagent-artifacts/outputs/`:

```
21750c6a-c02b-4c65-95c1-4e07bb0e3145/reviews/
  generalization.md   (113 lines)  "genotyping in a trench coat?" — schema holes
  coupling-bloat.md   (143 lines)  judged against CONTRIBUTING/methodology; named the theatre gates
  falsification.md    (131 lines)  re-derived my measurements; found the Jinja parseability error
```

These are **retention-managed session artifacts — copy them into the repo if they matter.**

Throwaway verification scripts that were NOT kept in the repo: `/tmp/verify_templates.py` (28/28 parse,
196/473 differing-leaf), `/tmp/derive_probe2.py` (the 69/64/63 key-set comparison),
`/tmp/verify_doc_claims.py`, `/tmp/bc-fixed.py` (the falsification backup).

No delegation happened in this turn, so there is nothing to drain or wait on at completion.

## 10. Divergences — re-queries pending

| Claim | Last verified | Why it cannot be trusted later |
|---|---|---|
| "the handoff's four commits are on `origin/main`" | push output at handoff time | **A push is a claim, not a fact, for the next session.** Run `git log origin/main..HEAD` (must be empty) and `git fetch` before touching anything, and never re-commit from this doc's word. |
| gatk-sv tree state (`main @ e1909d2f`, a **dirty** dev checkout with five `wt/*` worktrees) | reads during this session | All 28-template / 118-WDL / 196-binding numbers were measured against THAT tree at THAT sha. Re-run `git -C <checkout> rev-parse main` before quoting any of them; they are re-derivable with `/tmp/verify_templates.py` if it still exists, else rebuild it from the doc's §4 snippet. |
| "the design doc's §13 corrections are complete" | this session | They are complete *for the three reviews obtained*. A fourth reviewer with a different mandate could overturn something that survived. |
| quickstart outputs still match the tools | this session | Any later change to `wdl_gate`, the contract check or `batch_configs` invalidates the captured blocks. The doc names each command; re-run them. |
| `make test` green | this session | Cheap to re-verify; do it, don't read it here. |
| `docs/handoff/001-adversarial-hardening.md` | **not read in full** (298 lines; the prior session's handoff) | It is the sibling of this file, not its predecessor in topic. Read its §-ledger before assuming anything about what the *previous* round did or claimed. `docs/archive/CHECKPOINT.md` also exists and is older. |
| §6's "`make audit`'s own pattern list returns clean" | this session, before the audit was changed | **Superseded after this handoff was written.** `AUDIT_PATTERNS` no longer exists: the blocklist of one person's identifiers was replaced by `scripts/audit.py`, which ships machine-independent SHAPES and derives the personal half from the publishing machine's own resolved config, with no file exempt. Do not go looking for the pattern list, and re-run `make audit` rather than believing this row. It has since grown a second lesson: matching a whole value is not enough, because `docs/archive/` shipped the *middle segment* of a workspace name inside a directory and script names for months and both designs passed it by. |
| every git SHA quoted in this file (`5f40a03`, the four handoff commits) | this session, pre-rewrite | **No longer resolvable.** On 2026-09-24 `main` was squashed to a single commit so the initial commit's coordinates stop being reachable through `git log -p` — see [methodology.md](../methodology.md), which also states what a rewrite cannot undo. Re-run §7's right-hand column instead of hunting for a commit. |

## 11. Durable facts folded into standing instructions

One bullet was folded, under **Behavior**: web search and subagent tools are part of the normal workflow
here, while delegation itself still needs the operator's authorization.

It is at `~/.pi/agent/skills/instructions/AGENTS.md` — **created during this session**, because the path
this repo's skill text names (`<agent-home>/skills/instructions/AGENTS.md` resolved against a
`~/.codex/codex` home) **does not exist on this machine**: `~/.codex` is absent entirely, and the
editing tool reported `Successfully replaced 1 block(s)` for that nonexistent path twice. Verified with
`ls` + `grep` on the file above; **whether the harness actually reads that file is UNVERIFIED**, so treat
this bullet as durable only in the weak sense that it is written down somewhere findable. Two things the
next session should do: confirm which instructions file this agent really loads, and report the
phantom-success edit to the user (it is the kind of silent lie this repo's whole methodology is against).

Nothing else from this session belongs in standing instructions: every other fact is about a gatk-sv
tree that moves daily, which is what §10 is for.

## 12. Next steps, in order

1. `git log --oneline -5`, `git status --short`, `git log origin/main..HEAD` — establish reality before
   touching anything (per §10). The push is claimed in §8, not proven to a future session.
2. The two new docs are committed but **unread** by the user (~875 lines). Ask before restructuring
   them; the fix + probe landed as its own commit precisely so it could ship independently of them.
3. The one decision blocking all design work: **JSON vs `KEY=value`** (design doc §11 q5).
4. Of §4's two open defects, `selftest.sh`'s probe counter is **done**. One remains: the
   `wdl_gate.sh:18` example naming a workflow absent at `main` — one line, zero risk, never run.
5. Only then design step 1 (golden capture, before moving any data).

## 13. What good looks like — closing ledger

| Intended outcome | Evidence | State |
|---|---|---|
| A general (non-genotyping-only) design exists and is written down | `docs/module-profiles.md`, 547 lines, `check_docs` clean | **CONFIRMED** |
| …without bloat or gatk-sv interdependence | §2's one-way dependency + read-at-a-ref rule; §7's gates; §8's non-goals | **PENDING** — it is a proposal; nothing was built to be measured against it |
| …usable by humans and agents | §6 (rewritten after review deleted three new commands) | **PENDING** — design only; the quickstart is the only realized UX artifact |
| Rev 1 adversarially reviewed by 3 subagents | three reports in §9; all material claims re-verified here | **CONFIRMED** |
| …and the review changed the design | 4 overturned claims + 2 corrected numbers published in §13 | **CONFIRMED** |
| The `check_maps` crash is fixed | `make test` PASS; probe `nested_bindings` green | **CONFIRMED** (local gate only — no CI run, no remote) |
| …and the fix is pinned so it cannot regress | pinned count 12→13; probe falsified by reverting the fix | **CONFIRMED** |
| A human-readable quickstart showcasing core functionality exists | `docs/quickstart.md`, 326 lines, every command run here, audit-pattern clean | **CONFIRMED** (as an artifact) |
| The user is satisfied with the quickstart | they replied only `commit`, with no comment on the tour's content | **PENDING** — acceptance of the commits is not the same as the tour being read or found useful |
| Anything was committed / pushed | four commits + push, then the history squashed to one commit at the user's request (`0cc2d07` → `cdb6444`, force-push) | **CONFIRMED against the remote**, not from the local view: a fresh clone of the pushed URL has 1 commit, the old head is not in it, and every blob and message greps clean for every coordinate the old blocklist named. Limits in [methodology.md](../methodology.md): clones/forks taken beforehand keep the old objects, and a host may still serve a dereferenced SHA by other means |
| A skipped probe no longer satisfies the pinned count | `probecount` compares `run_n` alone; falsified in both directions in a throwaway clone — no dev requirements: gate FAILS with the remedy printed; with them: PASS, 0 failed | **CONFIRMED** |
| §4's two open defects fixed | one at the time of writing, the probe counter later | **PARTLY CONFIRMED** — `check_maps` fixed + pinned, `selftest.sh` counter fixed + verified. One stands: `wdl_gate.sh:18`'s example names a workflow absent at `main` |
