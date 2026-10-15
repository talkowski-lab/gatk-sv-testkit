# Does the testkit cover the testing actually done on the single-sample blocking branch?

**Question asked:** over the work that produced PR #966 (`main`'s single-sample blockers: `ConcatBaf`
invoking a binary its image does not contain, `svtk` importing `pkg_resources`, a `workspace.tsv.tmpl`
that would not render, and a `rc=141` SIGPIPE race), which testing tasks did gatk-sv-testkit perform
or support, and which had to be done outside it?

**Method.** The task list came from the session transcript itself (510 shell commands across the
fix-and-test session plus its handoff), not from a progress doc — see §7 for what that costs and buys.
Each task was then checked against this checkout at `f8d158a` by reading the named tool's code, and
four of the claims were **measured** in this session (§3): the gate run, a render-then-`bash -n` pass
over all 118 WDLs, a pipefail/early-exit scan at two refs, and `make audit`. 37 tasks:
**3 covered by a tool, 1 by repo convention, 3 covered by prose in `docs/`, 7 partial, 1 broken for
this shape, 1 not applicable by design, 21 absent.**

**Answer: no, it is not sufficient — and the interesting part is not the count.** Every one of the
three real defects sits in the one layer nothing in `checks/` looks at: the *rendered shell* of a
task and the *contents of the image it runs in*. Both WDL validators pass the broken tree, the gate
reports a zero delta against the fix, and the only oracles that found anything were a container, a
167 MB jar this repo tells you to go download yourself, and `bash -n` on a rendered block — the last
of which nothing here runs.

---

## 0. Provenance and redaction

Project, workspace, registry, scratch-bucket and machine-path values are not written here, and
neither is the branch name: per `CONTRIBUTING.md:123` a personal coordinate is not the toolkit's to
publish, and the two sibling reviews already disagree about this — `GAP-REVIEW-trio-calling.md`
names them, `GAP-REVIEW-manta-tloc.md` redacts them. The real values are in `docs/handoff/003-…`
(committed, and the reason `make audit` is red — §3.4) and in PR #966, so nothing is lost.
Commit SHAs are kept: they are public, verifiable, and not anyone's coordinate.

Verdicts labelled **measured** were run in this session; **code-read** means the named file and line
were opened; **transcript** means the task and its outcome come from the recorded commands.

## 1. The finding with the most reach

The branch's three defects, scored against every offline oracle available here:

| Defect | `checks/wdl_gate.sh` | `miniwdl check` | `womtool validate` | `bash -n` on the rendered block | inside the image |
|---|---|---|---|---|---|
| `ConcatBaf` ran `/gatk/gatk` inside `sv_pipeline_docker` | passes | passes | passes | passes | **fails** — the wrapper exists only in `gatk_docker` |
| `svtk` imported `pkg_resources` (gone in setuptools ≥ 82) | passes | n/a (not WDL) | n/a | n/a | **fails** — `svtk -h` at image build |
| `zcat \| awk … exit` under `set -o pipefail` → rc 141 | passes | passes | passes | passes — the race is semantic, not syntactic | **fails, and only on a large input** |

`checks/wdl_gate.sh origin/main <branch> --wf GATKSVPipelineSingleSample` — run in this session,
measured (§3.1) — prints the identical row for both refs: `EXIT 0  INCOMPLETECALL 2  STALE-BINDINGS 0`.
That is a correct reading of the gate's own contract: the branch changed no call binding, so there is
no binding delta. It is also a green light over a tree where a task invoked a binary that was not in
its container, and the gate's default workflow list (`checks/wdl_gate.sh:61`) *does* include
`GATKSVPipelineSingleSample` — so this is not an unfaced workflow, it is the wrong layer.

The layer the gate does not measure is exactly where the branch's money went. The rendered-script
half is cheap to reach and nobody has reached it: a 40-line render-and-`bash -n` pass found the
breakage that both validators certified as `Success!` (§3.2). The image half is not cheap, but its
shape is already in this repo twice (`docker/gatk-sv-build.sh`, `checks/image-check/*.sh`) and was
never used for it — the branch built six throwaway Cloud Build configs by hand instead (§3.3, T14).

## 2. Task by task

`Covered` = a tool here does the task. `Docs-only` = the knowledge is in this repo, the capability is
not. `Partial` = a tool does part of it or is coupled to the genotyping/joint shape. T-numbers are
referenced again in §6.

### Proving a defect before it costs a run

| # | Task performed | testkit today | verdict |
|---|---|---|---|
| T1 | Sweep the WDL tree for the bad idiom and compare against the sibling that already works (`java -jar ${GATK_JAR}`, which `sv-base` sets) | ad hoc `grep` + `git show`; `wdl_gate` measures bindings only | Absent |
| T2 | Cross-check the image a task is *bound to* against the absolute executable paths in its command. Measured inputs: 4 tasks reference `/gatk/gatk` at `main` (`BatchEvidenceMerging` `MergeEvidence`+`SDtoBAF`, `CollectSVEvidence` `RunCollectSVEvidence`, `ConcatBaf`); the two legitimate ones arrive with `gatk_docker = gatk_docker` (`BatchEvidenceMerging.wdl:74-75`), the broken one with `sv_pipeline_docker = sv_pipeline_docker` (`GATKSVPipelineSingleSample.wdl:692`) | nothing joins a call site's `*_docker` binding to the paths in the task body; the container reaches every task through a `RuntimeAttr` struct, so it is not statically visible inside the task at all — which is why the bug survived | Absent |
| T3 | Prove `pkg_resources` dies on setuptools ≥ 82: five throwaway venvs (80.9.0, 81, 82, 83, 84) importing `svtk` | no python-compat matrix harness; `requirements.txt` is deliberately unpinned and there is no production-pin export (manta review §2.5 hit the same wall from the other side, with pysam) | Absent |
| T4 | Prove `svtk` package data still resolves after the change (`resource_filename` → `RdTest.R`) — the gate that ended up in the Dockerfile | nothing | Absent |
| T5 | Reproduce rc=141 under bash 5.1.16 in `sv-base` — macOS ships bash 3.2.57, which has no `pipefail`, so the bug is **unreachable on the laptop** | `checks/image-check/svshell_image_check.sh` is the same VM skeleton (boot → pull → `docker cp` → serial marker → keep the VM alive) but hardcodes `/opt/sv_shell` md5s and `/opt/gatk.jar` flags; no "run this in a Linux container" entry point | Partial |
| T6 | Make the regression test grade **the WDL file**, not a copy of the block (`scripts/test/test_sigpipe.sh` extracts it from `wdl/CollectCoverage.wdl` at run time) | nothing extracts a command block; the renderer exists only as this review's prototype (§3.2) | Absent |
| T7 | Ask whether an offline validator can see a broken shell block at all | **measured**: miniwdl `rc=0`, womtool `Success!` on a tree with an unbalanced paren in a command block; `bash -n` catches it (§3.2). Nothing in `checks/` renders or `bash -n`s anything | Absent |
| T8 | `miniwdl check` the changed files and the driver | `checks/wdl_gate.sh` resolves miniwdl four ways and reports `EXIT`/IC/stale per ref; the branch ran bare `miniwdl check` instead, so the gate never ran on it | **Covered** (unused; would have said "no delta", §3.1) |
| T9 | `womtool validate` a hand-built 120-key single-sample input JSON | `WOMTOOL_JAR` exists only for `terra/batch_check_inputs.py` (`:40`), no fetch/pin/version check (`requirements-dev.txt` says so out loud); the jar was curl'd twice (167 MB) | Partial |
| T10 | flake8 the changed gatk-sv python with the repo's own `tox.ini` | `make flake` is `--select=F` on **this** repo only; `scripts/undef_module_refs.py` takes arbitrary paths and is stronger for the missing-import class | Partial |
| T11 | Enumerate the *remaining* unguarded `pipefail` + early-exit sites instead of remembering them | nothing; prototype in §3.3 | Absent |

### Images

| # | Task performed | testkit today | verdict |
|---|---|---|---|
| T12 | Preflight the build before booting anything | `docker/gatk-sv-build.sh --check` — and it worked: `error: branch '<under-test>' not found on github.com/broadinstitute/gatk-sv` / `gsvtk: preflight failed; fix that before anything else`. A refusal that names the cause is the whole point of a preflight | **Covered** |
| T13 | Get a runnable image of the branch | the branch was unpushed at that moment, so the testkit's path was correctly unavailable; the images came from gatk-sv's own `Docker Images` workflow on the PR | N/A by design |
| T14 | Build a **test layer** on a published image (`FROM <sv-pipeline:tag>` + `COPY` probes + `RUN svtk -h`) — used 6× for the `ConcatBaf` in-image test, the setuptools-84 gate, the `-test-` sv-pipeline tags and two sv-shell arms | `gatk-sv-build.sh` builds *a branch's own Dockerfiles from a clone*; it cannot express "assert against this already-published image", so every one of those was a hand-written `/tmp/…/cloudbuild.yaml` | Absent |
| T15 | Know that a sv-shell build only carries sv-pipeline changes if the base is rebuilt (`sv-shell` is `FROM $SV_PIPELINE_IMAGE`) | `docs/docker-builds.md:27`: *"**FROM them** (dependents). It does **not** rebuild their parents/base images"*. The fact was here; a build was spent rediscovering it | Docs-only |
| T16 | Prove the image a run used is the image the branch built (tag provenance + registry presence) | `checks/image-check/*.sh` proves "shipped bytes == tested bytes" by md5 — for `/opt/sv_shell` only; nothing does the same for `sv-pipeline`/svtk bytes (manta review §1 found the same hole) | Partial |

### Inputs, templates, and the cheap leg

| # | Task performed | testkit today | verdict |
|---|---|---|---|
| T17 | Render `inputs/templates/terra_workspaces/single_sample/workspace.tsv.tmpl` and assert rows × attributes (the fix was 2 rows × 96 attrs incl. `workspace:cloud_sdk_docker`) | gatk-sv's own `scripts/inputs/build_inputs.py` driven from a throwaway venv — the testkit venv cannot render (`./.venv/bin/python -c "import jinja2"` → `ModuleNotFoundError`, re-verified here; `jinja2` is in neither requirements file) | Partial |
| T18 | Cut real case/panel evidence down to a 1 Mb window (`tabix` SD + BAF + dbSNP loci) so a cheap leg existed at all | nothing; `tabix`/`pysam` are present in the venv only as `compare/` readers | Absent |
| T19 | Assemble and validate a single-sample input JSON (120 keys) | `replay/build_inputs.py` is SVShell-shaped — `--wdl` is documented as "SVShell.wdl of the arm being built", it parses `call-RunSVShell`'s `jq --arg` list, and it hardcodes the `SVShell.` prefix and the #961 RD-table rename (`:57-58`). `replay/single_sample_extra_inputs.json` is a 12-entry static list read only by `docs/archive/as-run/single_sample_arms.py:49` | Absent |
| T20 | Run the single-sample driver once on one scoped VM: Cromwell **Local** backend, hand-written `startup.sh` + `cromwell.conf` (gcs filesystem, `application-default` auth), serial-console polling to a `STATUS` marker, results tarred to GCS | nothing in `terra/` or `checks/` owns a Cromwell-on-a-VM loop; the VM lifecycle half exists inside two scripts that are each about something else. Transcript: 10 `instances create`, 12 serial-port polls, and two self-inflicted Cromwell errors (`scala.MatchError: …GcsPath`, `TimeoutException … waiting to copy`) that are *harness* bugs, the class manta review §2.4 warns about | Absent |
| T21 | Decide flake vs determinism: two attempts, same `rc=141`, and the reason one input size passes and the other does not (pipe buffer vs 135 MB) | nothing reads attempt layouts or `rc` files (T30) | Absent |

### Terra single-sample

| # | Task performed | testkit today | verdict |
|---|---|---|---|
| T22 | Inventory the sandbox workspace (attrs, entities, configs, submissions, `gs://` census) | `terra/recon.py` — run in-session as `gsvtk terra recon` on the sandbox; generic, worked as-is | **Covered** |
| T23 | Clone the single-sample workspace, re-point its attributes, upload the `sample` entity | `terra.py:236 create_workspace` (new attributes, not a clone) and `:244 upload_entities_tsv`; no clone, no template render, no verb — and `gsvtk` refuses `create`/`attrs --write` by design (`scripts/gsvtk:13`), so all of it was raw `curl` (5 `POST`s) | Absent |
| T24 | Create the single-sample method config (112 inputs, 11 outputs, Dockstore method by reference) | `batch_configs.py:112 CONFIGS` holds five joint steps (`GenerateBatchMetrics`→`GenotypeBatch`, `rootEntityType: sample_set`); no other config is describable | Absent |
| T25 | Validate a config's bound keys against the branch WDL, offline | `batch_configs.py check --against <ref>` does exactly this — for those five configs. **This branch fixed the tool here**: `--drop-branch-only-inputs` was not reaching the guard that gates create/validate/submit, so testkit PR #1 landed the seam plus a probe, now pinned at `scripts/probe_fixes.py:945` (`drop_flag_guard`, registered in `PROBES` at `:1233`) | Partial |
| T26 | Validate config **output** names against the workflow (a stale name is a run that reports `Failed` after `Succeeded`) | `check` reads `wf.inputs`; the `outputs` maps are hand-maintained literals (`:136,153,180,207,238`). Rawls does not validate them either, so no pre-run check exists anywhere | Absent (trio review rec 2, third sighting) |
| T27 | Submit with `useCallCache=false` — the durable fact from this branch is that cross-workspace cache copies time out (`TimeoutException … waiting to copy gs://fc-…/all_samples.RD.txt.gz`) and cost one Failed submission | `batch_configs.py:319` hardcodes `"useCallCache": True` with no flag, while `docs/terra-head-to-head.md:256` advises the opposite between arms. The shipped builder would have inflicted this branch's most expensive self-inflicted failure on you | Absent, and contradicted by a shipped default |
| T28 | Watch the submission to terminal and report status + cost without an agent turn per poll | `batch_status.wait(prefix)` matches **any** config prefix and `--costs` prints `cost=` off the workflow record — so the shape is right and single-sample is *not* excluded. It rides `terra.py:149 submissions()` → `fapi.list_submissions`, the endpoint this repo's own `docs/terra-head-to-head.md` §8 records as returning `[]` for a workspace that demonstrably had submissions. In-session the monitoring was the separate `terra-monitor` skill, whose `twatch.py --diagnose` died on `HTTP Error 405: Method Not Allowed` | Broken (for this workspace) |
| T29 | Cost, in currency, as a lower bound while running | Rawls `cost` via `curl` in-session; `batch_status --costs` prints it when the listing works; `batch_cost.py` recomputes VM-minutes from saved metadata and refuses an unexpanded chain | Partial |
| T30 | Liveness from the bucket, not the API: `gsutil cat $B/**/rc \| sort \| uniq -c` (433 files, all zero), plus the per-call `call-*` layout | nothing tallies `rc` files. The transcript did this 13×, and it was the only signal that survived the stale-metadata problem the docs describe | Absent |
| T31 | Read one failed task's `stderr`, its **rendered `script`** (to prove the `|| true` guard was in the script the VM actually ran), and its `attempt-N` layout | nothing fetches or greps a task's scratch objects (`stderr` appears 16× in the transcript) | Absent |
| T32 | Prove Dockstore serves the branch version a config references | `docs/terra-head-to-head.md:183` has the `.dockstore.yml` rule and §8 the TRS `curl` recipe; no tool, no re-index poll | Docs-only |
| T33 | Get the config payload shape right (workflow-qualified keys, entity expression `this`, unqualified output values) | `docs/terra-head-to-head.md` §8, added by `d63c771` out of this same week's runs, with the verbatim 400s — knowledge present, linter absent | Docs-only |

### CI, publication, and blast radius

| # | Task performed | testkit today | verdict |
|---|---|---|---|
| T34 | Triage a red PR: which check is ours, and why. `##[error]Readonly file modified: .github/.dockstore.yml` (job `Verify`, `readonly_check.yaml`, exempting only `gatk-sv-bot`) is ours; `Test Images Build (3.8)` failing one minute after another developer's branch is not | nothing. `grep -rn "Readonly file modified\|readonly_check\|gh run view" docs/ CONTRIBUTING.md` → no rows, in a file whose whole convention is verbatim-error-text keying | Absent |
| T35 | Prove a shared-infra failure is shared (same workflow, same window, another branch) | nothing | Absent |
| T36 | Decide which fixes carry to `sv-shell`: is `SVShell.wdl`'s closure even reachable (`imports` only `Structs.wdl`; nothing imports `SVShell.wdl`), and does `sv-shell`'s own `annotate_vcf.sh` already guard its probes? | nothing answers "which workflows/tasks/scripts reach this file". The manta review needed the identical join from the other end (`mantatloccheck.sh` ← `TinyResolve` ← `GatherBatchEvidence`) | Absent |
| T37 | Freeze the evidence: a numbered handoff with every number reproducible | the `docs/handoff/NNN-*.md` convention, and `docs/handoff/003-…` is this branch's — written here, and the reason the publish gate is red (§3.4) | Covered by convention |

**Tally:** Covered — T8, T12, T22 (tools) plus T37 (convention). Docs-only — T15, T32, T33. Partial —
T5, T9, T10, T16, T17, T25, T29. Broken — T28. N/A by design — T13. Absent — T1, T2, T3, T4, T6, T7,
T11, T14, T18, T19, T20, T21, T23, T24, T26, T27, T30, T31, T34, T35, T36 (21).

## 3. What I measured in this session

Read-only, no cloud, no credentials. Trees were materialised by the gate's own
`scripts/fetch_wdl.py` (118 WDLs per ref, one directory per ref, into a `GSVTK_WORK` override so no
other session's scratch was touched).

### 3.1 The gate is green across the whole fix

```
$ checks/wdl_gate.sh origin/main <branch-under-test> --wf GATKSVPipelineSingleSample
WORKFLOW                       REF                            EXIT  INCOMPLETECALL  STALE-BINDINGS
GATKSVPipelineSingleSample     main@e1909d2f                    0   2               0
GATKSVPipelineSingleSample     <branch>@04fa5142                0   2               0
```

Identical. Correct per its contract (no binding changed) and silent on all three defects. Cosmetic
note for whoever touches it next: the `REF` column is `%-16s`, so a real branch name overflows it and
the columns misalign — worth knowing before this table is screenshotted into a PR description.

### 3.2 Neither validator sees the shell; `bash -n` does

Mutated `origin/main` exactly the way the branch reported breaking it (a mechanical pass that put
`|| true` *outside* the `$( … )`, leaving an unbalanced paren in `CondenseReadCounts`):

| oracle | result on the mutated tree | result on clean `main` |
|---|---|---|
| `miniwdl check GATKSVPipelineSingleSample.wdl` | **rc=0** (same IC=2, same warnings) | rc=0 |
| `java -jar womtool-84.jar validate` (same driver) | **`Success!`** | `Success!` |
| render each task's command (`~{…}`/`${…}` → a literal) + `bash -n` | **11** of 314 blocks fail | **10** of 314 |

The one block that appears only in the mutated tree is exactly
`CollectCoverage.wdl task CondenseReadCounts`. So a render-then-`bash -n` pass, diffed against a base
ref the way `wdl_gate.sh` already diffs IC and stale bindings, catches this class — and the ten
pre-existing failures on `main` are my renderer's artifacts, not bugs: gatk-sv writes
`~{true='(' false=')'}`-style placeholders that a naive substitution mangles. That is why this has to
ship as a **baseline delta** and not as an absolute oracle: 10 known-noisy rows, 1 new finding.

This also qualifies a line in `docs/handoff/003-…` §9.9, which recorded the breakage as "caught by
`womtool`, not by `miniwdl`". For the shape reproduced here — an unbalanced paren left in a
`command <<<` block — womtool validates it happily too, and so does miniwdl. Nothing in the offline
loop caught that shape; the live run did. If the historical breakage instead sat in a `command { }`
block, its `${…}` placeholders would have made it a WDL parse error, which is the one shape womtool
*does* see — worth re-checking against the real diff before rewriting that handoff line.

### 3.3 The pipefail scan is 40 lines and already finds residual sites

Scan = a task whose command sets `pipefail` containing a pipe whose consumer exits early (`head`,
`awk … exit`, `grep -m`, `sed … q`), classified by whether a `|| true` guards it.

```
### origin/main                                   21 sites — UNGUARDED 15 | GUARDED 6
### <branch-under-test> @ 04fa5142                21 sites — UNGUARDED 10 | GUARDED 11
```

Five of the branch's seven fixed hunks flip from UNGUARDED to GUARDED across that diff. Two do not:
the `RefineComplexVariants`/`SplitCpxCtx` pair already reads as guarded at `main`, because a
12-line lookahead is a crude stand-in for "same `$( … )`". The prototype's false-positive classes are
worth recording so nobody re-derives them — of the 10 residual UNGUARDED rows at the branch, **three
are real candidates** and seven are artifacts:

* **real:** `CollectPerSampleBenchmarking.wdl` `RenameBenchmarkTarfileSamples`
  (`tar --list -f … | head -n1`), `ResolveCpxSv.wdl` `GetSeCutoff` (`awk … | head -n1`),
  `TrainRDGenotyping.wdl` `UpdateCutoff` (`sort -nr | head -n 1`) — all in `set -euo pipefail` tasks.
* **artifact — order matters:** `MakeBincovMatrix.wdl` `SetBins`/`MakeBincovMatrixColumns` run
  `gunzip -c … | head -c 1` on the line *before* `set -o pipefail`. A check that asks "does this task
  contain pipefail" is wrong; it has to be order-aware.
* **artifact — a consumer that drains is not an early exit:** the three `SingleSampleFiltering.wdl`
  hits and `TasksClusterBatch.wdl` `SVCluster` match `exit` inside `END{ … }` or an unrelated `else`.
  The SIGPIPE needs a consumer that stops *reading*.

Those three residual sites are on module 04–12 paths this branch never ran, which is precisely the
argument for the check: the branch fixed the six it happened to walk into, and the count of what is
left is a number rather than a memory.

### 3.4 The publish gate is red at HEAD, and this file does not change that

`make audit` at `f8d158a`: `audit: FAIL — 9 hit(s) in 1 file(s)`, all in
`docs/handoff/003-single-sample-blockers-pr966.md` (project, derived registry path, absolute home
path, a scratch-bucket URL). `audit.py` grades `git ls-files`, so this review — like
`GAP-REVIEW-trio-calling.md` and `GAP-REVIEW-manta-tloc.md`, all three untracked — adds `hits=0` and
leaves the tally at 9/1. The tension the trio review recorded is still open and this branch's handoff
is now the thing that trips it: either handoff docs get a placeholder convention or the audit learns
a per-doc waiver. A gate that is red at HEAD is a gate the next session skimps.

## 4. Where the testkit did earn its keep

* `docker/gatk-sv-build.sh --check` refused correctly and cheaply (T12), before any VM.
* `gsvtk locate` / `tools` / `kit/gsvtk-config` — the config layer resolved every value with its
  source, and the two no-default coordinates (project, workspace) are exactly the two that decided
  which bill and which workspace this work touched.
* The image-pinning guard did its job on the joint leg: `batch_rerun_step.py show` refused to print a
  body with `unpinned: GenotypeBatch.gatk_docker` and `unpinned: GenotypeBatch.sv_base_mini_docker`,
  which is the failure it exists to prevent.
* The read-only front door held: `gsvtk` dispatches only `locate/doctor/tools/gate/build/terra`, and
  `submit`, `create`, `copy`, `attrs --write`, `fetch` are refused at `scripts/gsvtk:13`. Every
  Terra write in this workstream was a deliberate `curl`, never an accidental wrapper call.
* **This branch improved the testkit.** `--drop-branch-only-inputs` not reaching the create/validate/
  guard was found *because* the branch's rerun config bound a key that existed only on another branch
  (`Validation errors: Extra inputs: GenotypeBatch.training_vcf`), fixed in testkit PR #1, and pinned
  by `probe_drop_flag_guard` — which counts as `RUN`, not as `SKIP`, per the corrected counter.
* `docs/terra-head-to-head.md` §8 now carries this week's payload-shape 400s with verbatim text
  (T33) and `docs/docker-builds.md:27` already carried the base-image lineage rule (T15).

## 5. Where it would have actively hurt

Three places, all small:

1. `batch_configs.py:319` ships `"useCallCache": True`. This branch's measured fact is that
   single-sample Terra runs want `false`, because a cross-workspace cache copy times out mid-run
   (`TimeoutException … all_samples.RD.txt.gz`) and the submission failed. One `--no-call-cache`
   closes the gap between the shipped default and the documented advice (manta review §3.2 asked for
   the same flag for the opposite reason).
2. The monitoring path is one endpoint wide: `terra.py:149` → `fapi.list_submissions`, which §8 of
   this repo's own docs says returns `[]` where raw REST returns the truth. `batch_status.wait`,
   `report` and `latest_workflow` all sit on it, and this branch needed all three. This is the second
   review to find it, and the first to note the surrounding functions are otherwise shape-agnostic —
   the fix is a raw-REST call, not a redesign. Third review to find it (§6 rec 4).
3. `requirements.txt`/`requirements-dev.txt` cover miniwdl and flake8 but not `jinja2`, so the most
   basic question about the single-sample workspace template ("does it render, and how many rows ×
   attributes?") required building a second interpreter. Two lines.

## 6. Ranked recommendations

Ranked by what this branch paid for the gap. Items already in the sibling reviews are marked, because
three reviews converging on the same line is the argument for doing it.

| # | Change | What it costs | Evidence from this branch |
|---|---|---|---|
| 1 | **`checks/wdl_render.sh`**: render every task's command block (placeholders stubbed, `%{}`/section forms honoured) and `bash -n` it, reporting **counts per ref** beside IC/stale. A baseline diff, not an oracle | small, **prototyped §3.2** | an unbalanced paren in a `command <<<` block passes miniwdl *and* womtool; only `bash -n` sees it, and nothing in `checks/` runs `bash -n` (§3.2) |
| 2 | Second verb on the same renderer: **extract a named task's command block** to a file. That is what `scripts/test/test_sigpipe.sh` in gatk-sv does by hand so its test grades the WDL rather than a copy — and it is the entry point for "run this real block in a container" | small, same code | T6 |
| 3 | **A Linux exec harness.** One entry point that runs a shell script inside `sv-base` (Cloud Build `RUN`, or the `checks/image-check` VM skeleton parameterised) and reports rc. The transcript has six hand-written Cloud Build configs and one deleted-in-six-minutes VM because this did not exist | medium | T5, T14: bash 3.2.57 cannot reproduce `pipefail` at all, so *every* shell-regression proof here was a detour |
| 4 | **Raw-REST submissions/config listing** in `terra.py`, keeping `fapi` and failing loudly when the two disagree | small, **third sighting** (trio rec 1, manta §3.1) | T28: the only monitoring tool in the repo cannot see the workspace |
| 5 | **Per-task artifact reads**: `rc` tally over a submission prefix, a task's `stderr` tail, its rendered `script`, its `attempt-N` layout — bounded output, `$GSVTK_WORK` only | small, sibling of trio rec 6 (`gsvpeek.sh`, which is a testkit tool living outside the testkit) | T21, T30, T31: 13 + 16 + 12 transcript calls, and the only liveness signal that held |
| 6 | **Container-path × binding check** (T2): join a call site's `*_docker` to the literal executable paths in the task body; flag a path its image does not provide. Needs a 5-row table of what each image contains (`gatk_docker` → `/gatk/gatk`; `sv_pipeline_docker`/`sv-base` → `${GATK_JAR}=/opt/gatk.jar`) and a comment-stripping step, since the fix left the old path in a comment | medium | the defect that opened the branch: 4 candidate sites at `main`, 1 wrong, 0 after the fix |
| 7 | **`pipefail` + early-exit scan** as a third count column, order-aware, with the §3.3 false-positive classes as fixtures | medium | T11: 15 → 10 by hand-fixing six sites; 3 residual candidates still standing on module 04–12 paths |
| 8 | **Validate config outputs**, and a `--no-call-cache` (pairs with §5.1). Then a single-sample *module profile*, i.e. actually build `docs/module-profiles.md` | config-outputs small, profile large — **third sighting** (trio rec 2, module-profiles proposal) | T24, T26, T27: the entire single-sample Terra leg was hand-assembled in `curl` |
| 9 | **Two troubleshooting rows + one helper for gatk-sv CI**: `##[error]Readonly file modified: …` (which paths are protected, `gatk-sv-bot` exempt, and that deleting the commits from the PR does not unpublish Dockstore versions), and "is this failure shared infra?" = same workflow, same window, another branch | small | T34, T35: one red check attributed for a day, and two commits that had to come off the PR |
| 10 | **Reachability**: given a changed file or script, list the workflows/tasks that reach it through imports and call sites | medium | T36 here, manta §2.1 from the other end — the same question asked twice in one week |
| 11 | **Pin/serve womtool and add `jinja2`** to `requirements-dev.txt`; a `tools` line that prints the womtool version or refuses | small, **trio rec 13** | T9, T17: 167 MB downloaded twice, one extra venv built |
| 12 | Pin the pinnable defects in `scripts/probe_fixes.py` with controls (§1 and §3.2's delta, §3.3's counts), raise the pinned count, and **decide the handoff/audit tension** so the gate is green at HEAD | small, `CONTRIBUTING.md:22-27` | §3.4; 13 probes today, none about `141`, shell rendering, or image-path bindings |

Items 1, 2, 4, 5, 9 and 11 are the cheap-and-real set: one prototype already exists, each maps to a
specific stall or misattribution in the transcript, and none of them needs the genotyping module to
change shape.

## 7. Limits of this review

* **The task list is transcript-derived.** That makes it wider than a doc-derived list (it includes
  the 20-odd `grep`/`git show` joins that reasoning consists of, and the harness bugs) but it also
  means the tasks are those *a session recorded*, not the tasks *someone should have run*: I counted
  no missing control as a task. The manta review deliberately did the opposite (documents only, no
  transcript). Where the two reviews disagree, the manta one is the better model for what belongs in
  a published file.
* **Cloud claims are transcript-only.** Nothing here touched Terra, GCS, Cloud Build or a VM; T12–T33
  describe what the recorded commands did and did not have. The `make audit` run and §3.1–3.3 were
  executed here.
* **§3.2 and §3.3 are prototypes in `/tmp`, not contributions**: no fixture tree, no controls,
  naive on `~{true=… false=…}` placeholders and on the `$( )` guard boundary, and a command-block
  extractor that takes the first `command` block per task. Counts are for `origin/main`,
  `04fa5142` and one synthetic mutation, not a claim about every ref.
* **This branch was handed two of its three defects.** The `/gatk/gatk` path bug and the broken
  `workspace.tsv.tmpl` arrived as a report from a parallel session investigating a different branch
  (the operator's opening message is that report), and `pkg_resources` was found while diagnosing the
  first; only the `rc=141` race was discovered here, and it was discovered by a live Terra submission.
  So the §1 table grades the testkit against a mixed set on purpose: "what would it take to catch
  these before a run", not "what did this branch invent".
* **I did not run the build, the image check, or any `compare/` tool.** Verdicts about them are
  code-read against the lines cited. `compare/` gets no row here because this branch produced no
  comparable second arm — that is the same boundary the manta review measured as a comparator gap.
