# 003 — Single-sample blockers: ConcatBaf, svtk `pkg_resources`, and an rc=141 SIGPIPE race that only large inputs hit

**Session date:** 2026-09-25 (`date -u` at freeze: **17:06:19Z**). Branch of record: `mw_fix_single_sample_blocking`
in the worktree `/Users/markw/IdeaProjects/gatk-sv/wt/fix-ss-blocking`; HEAD `04fa5142`, remote head identical.

**Clock disagreement, recorded not resolved (§3.1):** `gh pr view 1 --repo talkowski-lab/gatk-sv-testkit` reports
`mergedAt = 2026-09-23T19:27:52Z`, which **predates this session's start**. Both timestamps are given; neither was
reconciled. Do not "fix" one to match the other — re-query.

Every claim below is labelled **verified** (command in the line), **inferred**, or **not exercised**.

---

## 1. What was asked, and what changed

The operator asked for the blocking issues on `main` to be fixed, tested, and PR'd; then to prove the changed
`svtk`/`sv_shell` paths on Terra; then (mid-session) "just roll a fix into this branch and test it" for the newly
found `rc=141` failures.

| workstream | state at freeze | proof |
|---|---|---|
| A. `ConcatBaf` used `/gatk/gatk` inside `sv_pipeline_docker` | **verified fixed, twice, on Terra** | `call-ConcatBafCase/**/rc` = `0` in submission `84d5820a…`; Cloud Build `7ab2727d…` in-image test |
| B. `svtk` imported `pkg_resources` (removed in setuptools ≥ 82) | **verified fixed in image** | Cloud Build `3501413f…`: `RUN svtk -h` passes with `setuptools-84.0.0`, gates `svtk imports with pkg_resources unavailable` + `svtk package data present` |
| C. single-sample `workspace.tsv.tmpl` would not render | **verified** | rendered `workspace.tsv` = 2 rows × 96 attrs, includes `workspace:cloud_sdk_docker` |
| D. `CondenseReadCounts` died `rc=141` on **both** attempts | **reproduced in container AND verified fixed in the live Terra run** | see §2 |
| E. sv-shell reachability of these fixes | analysed, **no code change made** | see §5 |
| F. joint `10-GenotypeBatch` rerun | **not run** | blocked then unblocked, see §4 correction 3 and OI-5 |

## 2. The headline finding: `rc = 141` is a race, not a flake — and the fix

Under `set -euxo pipefail`:

```
existing_sample_id=$(zcat counts.tsv.gz | awk -F "\t" '/^@RG/ { …; print $i; exit }')
+ existing_sample_id=NA12878        ← last line of the failed task's stderr; task rc = 141
```

awk `exit`s on the first `@RG` record, closes the pipe while `zcat` is still writing, `zcat` takes SIGPIPE
(128+13=141), `pipefail` propagates it, `set -e` aborts — **after** the value was extracted correctly.
Small inputs pass because the stream drains into the 64 KB pipe buffer before awk exits; a 135 MB
`counts.tsv.gz` fails every attempt. `svtk` appears **0 times** in that task's rendered script, so this was
never an `svtk` regression — it is pre-existing `main` code that had simply never been reached, because every
earlier attempt at this pipeline died upstream.

Fix = the guard the same task already uses on its two sibling probes (`counts_first_line`, `rd_header`): `|| true`.
Six sites fixed (only `CollectCoverage.wdl` is on the single-sample path; the rest are module 04–12 code):
`CollectCoverage`, `AnnotateExternalAFPerShard`, `RefineComplexVariants` ×2, `TasksMakeCohortVcf`, `Vapor`, `WGD`.

**Container proof** — `scripts/test/test_sigpipe.sh`, Cloud Build `abe1c6d0-11a2-40e9-9828-68e4559a1e67` SUCCESS,
bash 5.1.16 in `sv-base`. The "NEW" block is **extracted from `wdl/CollectCoverage.wdl` at run time**, so it grades
the file rather than a copy:

| case | rc |
|---|---|
| small input, old block | `0` ← why nobody ever hit this |
| 10 MB gz / 60 MB raw, old block | **`141`** ← the exact reported code |
| same input, new block from the WDL | `0`, `VALUE=NA12878` |

**Live Terra proof (verified):** `CondenseReadCounts` ran **one** attempt, `rc=0`, wrote
`condensed_counts.NA12878.tsv.gz`; the rendered script carries the guard:
`63:     }' || true)` on the probe reading the real `call-CollectCounts/NA12878.counts.tsv.gz`.

## 3. Coordinates — Terra run state at handoff (re-queried, not carried forward)

| item | value |
|---|---|
| workspace | `broad-firecloud-dsde-methods/GATK-Structural-Variants-Single-Sample-mw-fix966` |
| submission | `84d5820a-d8ab-4afd-a0d1-75cc09b5bbcd` |
| workflow | `5354e616-efcf-4b86-83c3-2d9fa9528805` — **`Running`** at freeze, `outputs: {}`, `messages: []` |
| started | 2026-09-24T16:12Z (~25 h before freeze) |
| rc tally | **433 files, all `0`, zero non-zero** — measured 15:40Z and again 17:05Z |
| in flight | `BatchEvidenceMerging.SDtoBAF` (localization files present, no `rc`; took 342 min in the prior run) |
| cost | `~$1.73` per Rawls — **partial-period, treat as a lower bound**, not the bill |
| watcher | alive, `/tmp/twatch_fix966_v2.log`, `--max-wait 21600` (dies with this session; no completion wake after) |

**Divergence to re-query:** the rc tally was **identical at 15:40Z and 17:05Z (433, all 0)**. SDtoBAF being the sole
in-flight task is plausible at 342 min, but a stalled run would look the same. Next session, decide it by whether
new objects appear under the submission prefix, not by assuming.

## 4. Corrections — the wrong claims are named here, not buried

1. **`.github/.dockstore.yml` branch publication is not allowed in a PR, and it made CI red.** Earlier in this
   session the working assumption was that adding branch filters to `.github/.dockstore.yml` is "normally how the
   branch is made visible". That is true for *sandbox* visibility (Dockstore did publish all 7 branch versions) and
   **false for a PR to `main`**: `.github/workflows/readonly_check.yaml` (job name **"Verify"**) protects
   `.github/.dockstore.yml` and `inputs/values/dockers.json` and exempts only `gatk-sv-bot`. Failure text:
   `##[error]Readonly file modified: .github/.dockstore.yml` → `Process completed with exit code 1`.
   **Consequence: commits `4419315c` and `e5bbc402` must come off PR #966.** That converts old open item
   "decide whether to keep or drop the dockstore commits" from a judgement call into a requirement. Note deleting
   them from the PR does **not** unpublish the Dockstore branch versions.
2. **`Test Images Build (3.8)` failing on `04fa5142` is not attributable to this branch.** `vj-vapor-optimize`
   (another developer's branch) failed at `2026-09-24T16:08`, one minute before this branch's 16:09 run, and this
   branch's image builds passed on 09-22 and 09-23 with the same Dockerfiles. `04fa5142` touches only WDLs and a
   test script. Treat as shared-infra; re-run rather than bisect. **`Verify` is genuinely ours** (correction 1).
3. **"Joint rerun blocked pending testkit PR #1" is stale.** PR #1 (`mw_drop_flag_guard_seam`) reports
   `state=MERGED`, merge `5f40a038`, and current `main` (`d63c771`) contains the `--drop-branch-only-inputs`
   handling. The blocker is presumed gone but **not exercised** — prove it with the §7 joint command before
   planning around it.
4. **A `grep pkg_resources` hit in `src/svtk/svtk/__init__.py` is my docstring**, not code. No `pkg_resources`
   import remains in `src/` (`grep -rln pkg_resources src/ dockerfiles/` → only that docstring, a stale local
   `.pyc`, and the Dockerfile gate's deliberate probe).
5. **The cross-workspace call-cache timeouts were self-inflicted**, not a pipeline defect: submission `3a824687…`
   went out with `useCallCache=true`. The re-run used `false` and has had zero failures.

## 5. sv-shell: what carries and what does not (analysis only, verified by grep)

| fix | on the sv-shell path? | evidence |
|---|---|---|
| svtk `pkg_resources` | **yes, carries by image lineage** | `dockerfiles/sv-shell/Dockerfile:42 FROM $SV_PIPELINE_IMAGE`; sv-shell calls `svtk cluster`/`standardize_vcf`/`rdtest2vcf` and its smoke test `sv-shell -h` imports svtk. Carries **only** from a fixed base — from the published `2026-09-17` base it inherits the `setuptools<82` mask instead |
| SIGPIPE guards (6 WDLs) | **not in `SVShell.wdl`'s closure** | `SVShell.wdl` imports only `Structs.wdl`; nothing imports `SVShell.wdl` (standalone driver). The `TasksMakeCohortVcf`/`Vapor`/`WGD` fixes still matter for module 04–12 downstream |
| SIGPIPE in sv-shell's own scripts | **nothing to port — already guarded** | `annotate_vcf.sh:98 zcat … \| head -1 > header \|\| true`; its awk filters run to EOF. They use `set -Exeuo pipefail` (errtrace + pipefail) |
| ConcatBaf | **no** | `ConcatBaf` appears only in `wdl/GATKSVPipelineSingleSample.wdl` |
| contig scope (`--all-contigs`) | **NOT carried — deliberate gap** | `src/sv_shell/clean_vcf.sh:304` still calls `add_retro_del_filters.py` with no contig scope, which that script requires → **sv-shell cannot complete from `main`**. Fix exists only on test-only `mw_test_svshell_runnable` (`838fce5c`, cherry-pick of `f021e85c`) because those hunks belong to PR #961 |

## 6. Mutation ledger — what was created or overwritten, with how to verify/undo

**Reversible**
- PR **https://github.com/broadinstitute/gatk-sv/pull/966** (draft, `OPEN`, head `04fa5142`). Undo: close PR; delete branch.
- Branch `mw_fix_single_sample_blocking` pushed to `broadinstitute/gatk-sv` → `04fa5142`. Undo: `git push origin :mw_fix_single_sample_blocking`.
- **Test-only branch `mw_test_svshell_runnable`** → `838fce5c` (PR branch + `git cherry-pick -x f021e85c`). Never to be merged. Undo: `git push origin :mw_test_svshell_runnable`.
- Dockstore: 7 workflow branch-versions published for `mw_fix_single_sample_blocking`. Persists after the PR edits; unpublish in the Dockstore UI if it matters.
- Terra workspace `…-mw-fix966` (cloned from `…-mw-ploidy`: 97 attrs, `sample/NA12878`, 0 method configs) + method config `01-SingleSample-mwfix` (112 inputs). Undo: delete workspace/config.
- Overwrote method config **`10-GenotypeBatch-rerun`** in `broad-firecloud-dsde-methods/GATK-Structural-Variants-Joint-Calling-Test` (pre-existing config, during a failed `create --confirm`). Restore: re-create with `GSVTK_BRANCH=mw_genotype_scale` and the prior images. **Not yet restored.**
- Images in `us.gcr.io/broad-dsde-methods/markw/gatk-sv/`: `sv-pipeline:mwfix-9f2ebe25`, `sv-pipeline:mwfix-test-9f2ebe25`, `sv-shell:mwfix-test-9f2ebe25`. Final states of the `-test-` builds were **not re-queried at freeze** (`84a08c03…` = FAILURE, `df4de0d6…` was `WORKING`). Verify: `gcloud builds describe <id> --project broad-dsde-methods`.
- `gs://broad-dsde-methods-markw/gatk-sv/na12878-scoped-9f2ebe25/results/results.tgz` — **still present** (~27 GB, matched 1 object). Delete when the scoped-VM evidence is no longer needed.

**Terminal / gone**
- Submissions `3a824687…` (`Failed`, `$1.17`, the `rc=141` run) and `84d5820a…` (running). GCE `gsv-na12878-scoped`: **gone** (`gcloud compute instances list … | grep gsv` → no rows). Not billing.

**Not mine, untouched** — parallel worktrees/sessions: `wt/manta_tloc_autoresolve` (its own HEAD says "Handoff 002: PR #968"), `wt/jrc_args` (2 unpushed commits), `wt/tloc-pr` (branch never pushed), `wt/trio-denovo`, and untracked `GAP-REVIEW-manta-tloc.md` in the testkit checkout. Nothing here was staged, committed, or deleted by this session.

## 7. Resume here — paste-able, verify each line

```bash
cd /Users/markw/IdeaProjects/gatk-sv/wt/fix-ss-blocking

 (A) drop the two Dockstore commits, then force-push and re-check CI
 git rebase --onto 9f2ebe25 e5bbc402 mw_fix_single_sample_blocking
 git --no-pager log --oneline -5
 git push --force-with-lease origin mw_fix_single_sample_blocking
 gh pr checks 966 --repo broadinstitute/gatk-sv

 (B) Terra run: status, cost, and where it stands
 TOK=$(gcloud auth print-access-token)
 curl -s -H "Authorization: Bearer $TOK" \
   "https://api.firecloud.org/api/workspaces/broad-firecloud-dsde-methods/GATK-Structural-Variants-Single-Sample-mw-fix966/submissions/84d5820a-d8ab-4afd-a0d1-75cc09b5bbcd" \
   | python3 -c 'import json,sys; d=json.load(sys.stdin); w=d["workflows"][0]; print(d["status"], w["status"], len(w.get("outputs") or {}))'

 (C) task tally — growth here is the real liveness signal, not the status string
 B=gs://fc-9a9e8ac1-132e-41de-817b-bc08b4931957/submissions/84d5820a-d8ab-4afd-a0d1-75cc09b5bbcd/GATKSVPipelineSingleSample/5354e616-efcf-4b86-83c3-2d9fa9528805
 gsutil -u broad-dsde-methods cat $B/**/rc | sort | uniq -c

 (D) re-run the SIGPIPE regression test (needs a Linux bash ≥ 4 — macOS ships 3.2, no pipefail)
 bash scripts/test/test_sigpipe.sh          # or the Cloud Build config in the commit message

 (E) joint step 10 rerun, now that the seam is on testkit main — re-verify before submitting
 cd /Users/markw/IdeaProjects/gatk-sv-testkit
 export GSVTK_BRANCH=mw_fix_single_sample_blocking WOMTOOL_JAR=/tmp/womtool-84.jar
 ./.venv/bin/python terra/batch_configs.py check --against mw_fix_single_sample_blocking --config 10-GenotypeBatch
```

## 8. Expected values — what good looks like, so a result can be judged

| check | expected | if instead |
|---|---|---|
| `gh pr checks 966` after OI-1 | `Verify` **pass**; `Test Images Build` pass | `Readonly file modified: …` → the dockstore commits are still in the branch |
| §7(C) rc tally | grows past **433**, then `SDtoBAF` `rc=0`, then workflow `Succeeded` | unchanged tally + no new objects ⇒ stalled, escalate to Cromwell log |
| any non-zero `rc` | none expected; preemption shows as an `attempt-N` dir that then ends `rc=0` | a second `141` anywhere ⇒ a probe still unguarded, `grep -n "zcat" wdl/*.wdl \| grep -v "|| true"` |
| `ConcatBafCase` | `rc=0` (already verified this run) | `127` → image/WDL drift; `2` + `No dictionary found` → `--sequence-dictionary` lost |
| §7(E) joint `check` | no `EXTRA … training_vcf`; `17 bound vs 21 declared` becomes consistent | that same `EXTRA` line ⇒ ref/branch mix-up |
| sv-shell from `main` | expected to **fail** at `add_retro_del_filters.py` (unrecognized `--all-contigs` / missing `--contig`) | passing ⇒ `main` gained the contig scope, re-read `clean_vcf.sh:304` |

## 9. Gotchas actually hit (each with the error text that produced it)

1. `rc=141` SIGPIPE-under-`pipefail` — see §2. `gsutil ls -d <dir>/*/` **listing files, not dirs**, is what hid the attempt layout; `-m -d` then died with `"ls" command does not support "file://" URLs`.
2. `##[error]Readonly file modified: .github/.dockstore.yml` — readonly guard, §4.1.
3. `scala.MatchError: gs://…1kg_ref_panel_v1.ped (of class cromwell.filesystems.gcs.GcsPath)` — Cromwell **Local backend** cannot localize a `GcsPath` input that way; scoped single-VM limitation, not a branch defect.
4. `TimeoutException … waiting to copy gs://fc-c2e11471-…/all_samples.RD.txt.gz` — cross-workspace call-cache copy from `useCallCache=true`.
5. `twatch.py status --diagnose` → `HTTP Error 405: Method Not Allowed`. Diagnosis had to come from the Cromwell `workflow.logs/workflow.<uuid>.log` object in GCS instead. **Still broken; worth a toolkit issue.**
6. `Validation errors: Extra inputs: GenotypeBatch.training_vcf` — the rerun config bound a key that exists only on `mw_genotype_scale`.
7. sv-shell image lineage: build succeeded, `clean_vcf.sh` had `--all-contigs`, verify still failed because `add_retro_del_filters.py` was inherited from a base image without it. Rebuild the base first.
8. macOS `/bin/bash` is **3.2.57** — no `set -o pipefail`, so the SIGPIPE bug cannot be reproduced locally at all; it needs a container (`sv-base`, bash 5.1.16).
9. A mechanical `sed`-style pass put `|| true` **outside** a `$( … )` and produced an unbalanced paren; caught by `womtool`, not by `miniwdl`. Run `womtool validate` on the driver, not only `miniwdl check` on the edited file.
10. `gh run view --log-failed` tails are runner credential-cleanup noise. Grep `##[error]`.

## 10. Durable facts for standing instructions

Deliberately **not** committed elsewhere: the `gatk-sv` checkout tracks no `CLAUDE.md`/`AGENTS.md` (adding one would
land in PR #966), and `/Users/markw/Work/genotypebatch_debug/CLAUDE.md` is a live pointer owned by a parallel
session. These facts are for the owner to place:

- Editing `.github/.dockstore.yml` or `inputs/values/dockers.json` in a PR fails CI (`Verify`, `readonly_check.yaml`); only `gatk-sv-bot` is exempt.
- Reproducing any `pipefail` bug needs Linux; macOS bash is 3.2.57.
- `sv-shell` is `FROM $SV_PIPELINE_IMAGE` — a sv-shell build only carries sv-pipeline changes if built from a rebuilt base.
- Terra single-sample runs need `useCallCache=false`; cross-workspace cache copies time out.
- `womtool validate <driver.wdl>` catches WDL command-block breakage that `miniwdl check <file>` misses.

## 11. Open items / next steps

- [ ] **OI-1 (blocking the merge)** drop `4419315c` + `e5bbc402` from PR #966, force-push, confirm `Verify` passes. §7(A).
- [ ] **OI-2** let `84d5820a…` finish; confirm `SDtoBAF rc=0` and workflow `Succeeded`; record final cost as a *measured* number (§7 B/C).
- [ ] **OI-3** update the PR #966 body with the Terra evidence in §2/§3 — **and** the correction that the dockstore commits are out.
- [ ] **OI-4** decide the sv-shell contig-scope gap: land PR #961's `add_retro_del_filters` change first, or a standalone PR. Until then sv-shell is not runnable from `main` (§5).
- [ ] **OI-5** joint `10-GenotypeBatch` rerun: re-verify the seam is usable on testkit `main` (§7 E), then submit; restore `10-GenotypeBatch-rerun` afterwards.
- [ ] **OI-6** deferred semantic bug: GD panel-BAF drops `--sample-names`, `349,606,438` input rows → `3,473,862` retained. Needs a maintainer decision + validated cohort run. **Not a PR #966 item.**
- [ ] **OI-7** re-query Cloud Build `df4de0d6…` and the sv-shell `-test-` build states; delete the images if the sv-shell thread is dropped.
- [ ] **OI-8** hygiene: delete the 27 GB `results.tgz`; file a toolkit issue for `twatch --diagnose` HTTP 405.
- [ ] **OI-9** resolve the PR #1 merge-timestamp anomaly only if it matters to the joint work (§ header).
