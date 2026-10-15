# Does the testkit cover the testing actually done on the trio calling branch?

**Question asked:** over the work on `trio_denovo_single_sample`, which testing tasks did the
gatk-sv-testkit perform or support, and which had to be done outside it?

**Method.** Read-only review. I took the task list from the branch's own two progress documents
(`gatk-sv/wt/trio-denovo/docs/progress/001-trio-denovo-single-sample.md` and
`002-trio-denovo-first-real-run.md`), which record each check with the exact command and the exact
error text it produced, then read the testkit source for a tool that performs that task. Verdicts
about testkit behaviour come from reading the code and CLI surface of the tool named, not from
running the heavy loops; where I ran something, it says so. 35 distinct tasks found: **7 covered by a
tool in this repo, 9 partial, 6 knowledge-only (documented, nothing to run), 11 absent, 1 broken
(returns the wrong answer), 1 not applicable by shape.** I also wrote throwaway versions of the two
missing static checks and ran them over the real WDL trees; §3 has the measured output, because it
settles whether the gap is difficulty or just absence.

**Answer: no, it is not sufficient.** The testkit covered the two loops it was shaped for — the
image build and the static WDL gate — plus read-only Terra inventory and metadata/cost plumbing that
the branch ended up hand-rolling anyway. It did not cover the single-sample/trio side of Terra at
all (config assembly, output-name validation, artifact fetch, QC/metrics/VCF reading for a
single-sample callset), and three of the five defects that cost real money
(`write_lines` at workflow scope, the `defined()` localization trap, the SIGPIPE rc=141) are
statically detectable and are not detected by anything in `checks/`. Two of them are detectable in
about twenty lines of miniwdl AST walking — §3 runs the prototypes against the actual refs and shows
what they find.

---

## 1. The finding with the most reach

`terra/terra.py:149` `submissions()` and `:140` `workspace_configs()` call
`fapi.list_submissions` / `fapi.list_workspace_configs`. The testkit's own
[docs/terra-head-to-head.md](docs/terra-head-to-head.md) §8 records that both return `[]` for a
workspace that demonstrably had the data, and that raw REST is the only truth:

> `GET …/submissions` via `firecloud.api.list_submissions` → `[]`. So does `list_workspace_configs`
> for a workspace holding 1 config. Raw REST with a `gcloud auth print-access-token` bearer returns
> the truth.

`terra/batch_status.py` discovers submissions through `terra.submissions()`, and
`terra/terra.py:160` `latest_workflow()` does too. So the whole `gsvtk terra status` / `--wait` /
`cost` loop — the read-only monitoring the skill advertises for "is my submission done, what did it
cost" — would have printed `no submission matching <prefix>` against the trio sandbox
(`gsv-trio-denovo-a0e10b99`, 5 submissions) and been wrong. The prose knows; the code path did not
change. This is the one gap I would fix first, because it is a wrong answer rather than a missing
one, and it sits under three separate tools.

Corroborating evidence that the branch worked around it: the call-level peek used on all five runs
is a 1.4 KB `gsvpeek.sh` living under `$GSVTK_WORK/staging/` (absolute path in the branch's own
doc 002 §5), which `cd`s into this repo, reads `GSVTK_TERRA_NAMESPACE` from `kit/gsvtk-config`, then
bypasses `firecloud` with raw `curl` and prints a bounded `executionStatus` tally. It is a testkit
tool that was written outside the testkit.

## 2. Task-by-task

`Covered` = a tool in the repo does the task. `Partial` = a tool does part of it, or is coupled to
the genotyping/joint-calling shape. `Docs-only` = the knowledge is in this repo, the capability is
not. `Absent` = nothing in the repo; it was done ad hoc or elsewhere.

### Build and static layer

| # | Task performed on the branch | testkit today | verdict |
|---|---|---|---|
| 1 | Build the branch's `sv-pipeline` (+ `sv-shell`) images on a throwaway x86 VM, no local Docker | `docker/gatk-sv-build.sh <branch> [image…]`, used as-is (doc 002 §3) | **Covered** |
| 2 | Diagnose the failed build (`pkg_resources`) from the GCE serial log, read-before-delete | `gatk-sv-build.sh` streams the log; the skill states the serial-output rule | **Covered** (log; root cause was human) |
| 3 | WDL launchability delta as a diffable number (`IC=2 stale=0` at `b0756590` and `6b666685`) | `checks/wdl_gate.sh:107-109` (IncompleteCall + stale bindings, one dir per ref), run via `gsvtk gate <ref> --repo …` | **Covered** |
| 4 | sv_shell JSON contract + `jq` null plumbing scan | `checks/svshell_contract_check.py`, `checks/svshell_jq_plumbing_scan.py` (ran inside `gate`) | **Covered**, and N/A: trio changed no `sv_shell` file |
| 5 | Typecheck **all** `wdl/*.wdl` (119 OK / 0 FAIL) | gatk-sv's own `scripts/test/miniwdl_validation.py`. `wdl_gate.sh:61` defaults to 4 workflows; `--wf` extends it, one flag at a time | Partial |
| 6 | womtool matrix over the repo's input JSONs (`validate.sh` → 48 tests) | Nothing fetches or pins a womtool jar; `WOMTOOL_JAR` exists only for `terra/batch_check_inputs.py` (`docs/setup.md:29`) | Absent |
| 7 | womtool-validate a **synthetic trio** input JSON | `replay/build_inputs.py` emits input JSONs but hardcodes the `SVShell.` prefix and the #961 rename (see `docs/module-profiles.md` §1); the trio JSON was built by hand in a heredoc | Partial |
| 8 | Run the branch's own unit tests (`pytest test_annotate_moi.py` → 4 passed) in a pinned venv | No runner for gatk-sv's tests. Done in `/tmp/moi-venv`, which macOS purged mid-project (doc 001 §5 gotcha 1) | Absent |
| 9 | flake8 the changed gatk-sv Python | `make flake` is `--select=F` on this repo only. `scripts/undef_module_refs.py` takes arbitrary `paths` and is stronger for the missing-import class, so it could run on a gatk-sv checkout | Partial |
| 10 | Render one WDL task's command from real JSON values and run it against 6 synthetic configs (proved `ValidateTrioInputs` before submitting) | Nothing. The equivalent discipline exists only for sv_shell's `jq` blocks (`svshell_jq_plumbing_scan.py`) | Absent |
| 11 | Prove an in-image Python fact (`pysam 0.15.4` vs `tabix_index(preset=…)`, doc 001 §1: "inferred, not executed") | `checks/image-check/` has exactly the right shape — throwaway VM, pull image, execute inside it — but only for sv_shell bytes and `GenotypeSVs` jar flags | Partial |

### Terra: submission, monitoring, money

| # | Task performed | testkit today | verdict |
|---|---|---|---|
| 12 | Inventory the sandbox workspace (attributes, configs, submissions, entity) | `terra/recon.py --ns/--ws/--entities` is generic | **Covered** |
| 13 | Assemble the single-sample method config (116 inputs, 11 outputs) | `terra/batch_configs.py` `CONFIGS` holds steps 06→10 only (`GenerateBatchMetrics`, `FilterBatchSites`, `FilterBatchSamples`, `MergeBatchSites`, `GenotypeBatch`). Built by hand from `terra/wdl_flat.py` output + the published v1.1 map | Partial (`wdl_flat.py` is a solid building block) |
| 14 | Validate bound input keys against the WDL at a ref, offline | `batch_configs.py check --against <ref>` does precisely this via the miniwdl package (`_declared_inputs`, `check_maps`) — but only for the configs in `CONFIGS` | Partial |
| 15 | Validate **output** names against the workflow (defect E) | Nothing. `check` reads `wf.inputs`; outputs are the tool's own hardcoded maps. Rawls never validates them (§8), so the only pre-run check is missing | Docs-only |
| 16 | Payload-shape linting: workflow-qualified input keys, entity expression `this`, output *values* unqualified, `sourceRepo=dockstore` | §8 prose, with the verbatim 400s. No linter | Docs-only |
| 17 | Monitor five submissions to terminal without paying an agent turn per poll | `batch_status.py --wait <config-prefix>` matches any config prefix and prints vm-minutes, call count, failures and the message. The default table and `--costs` are the 5-step chain. The branch used the separate `terra-monitor` skill instead | Partial |
| 18 | Discover submissions at all | rides `fapi.list_submissions` → `[]` on the sandbox (§1 above) | **Broken**, and documented as such |
| 19 | Call-level progress ("33 calls Done, `MakeCohortVcf` Running") | No tool prints an `executionStatus` tally. Hand-rolled `gsvpeek.sh`, outside the repo | Absent |
| 20 | Fetch expanded Cromwell metadata for an arbitrary submission, bounded | `terra/batch_save_metadata.py --ns --ws --submission --workflow` (`expand_sub_workflows=True`, `:186-196`), `--per-call` for attempt counts | **Covered** (and unused — the branch used raw `curl` and a hand summary) |
| 21 | Trust the bucket, not metadata, for liveness (metadata served a byte-identical 641 532-byte snapshot twice, 10 min apart) | §8 prose. No `gsutil ls -d …/call-*/` helper | Docs-only |
| 22 | Rule out a double-submit after a submit call raised mid-flight | §8 says raw-REST the submission list; `terra.submissions()` is the lying path, so no helper | Docs-only |
| 23 | Cost per submission against a budget envelope in currency ($6.47, $1.16, $18.87, $26.50 total) | Deliberate design: "cost is VM-minutes and job counts, never currency". `batch_status` does print `cost=` off the workflow record when it can see the submission | Partial (by design) |
| 24 | Recompute any published cost figure offline | `batch_save_metadata.py` → `work/metadata/*.json` → `batch_cost.py` (refuses a chain total unless every tree is expanded) | **Covered** |
| 25 | Attribute analysis: which calls replayed from cache at $0.00 and which re-executed (the §7 item-5 correction that "cache covers evidence gathering, not the back half") | Nothing reads `callCaching`/`hit` or the `cacheCopy` log paths. That correction came from reading logs by eye | Absent |
| 26 | Prove which commit Dockstore serves for a branch version (descriptor sha256 == git blob sha256; ~200 s re-index latency) | §8 has the `curl` one-liner and notes TRS mounts at `/api/ga4gh/trs/v2`. No tool, no re-index poll | Docs-only |
| 27 | Prove **import** freshness (TRS has no bundle/files endpoint; ground truth is the rendered `script` in the scratch bucket) | §8 prose + a `gsutil cat … \| grep` recipe | Docs-only |
| 28 | Freeze a baseline run's inputs and diff one stage against it (the testkit's core loop) | Not applicable to this branch: it changed WDL only, kept the image, and had no baseline arm. Everything for it (`fetch_baseline`, `batch_freeze`, `stage_inputs`, `batch_fetch_compare`) is keyed to the 06→10 `sample_set` chain | N/A by shape |

### Artifacts and results reading

| # | Task performed | testkit today | verdict |
|---|---|---|---|
| 29 | Download the run's 17 outputs and keep durable checksummed copies (VCF 10 972 534 B, `moi_summary`, metrics, QC, `working_ped`) | `batch_fetch_compare.sh fetch` reads `*${NW}` attributes on a `sample_set` entity, genotyping names only. Done by hand with `gsutil` + `sha256`, into a scratch tree outside `$GSVTK_WORK` | Partial |
| 30 | **Independently re-derive an annotation from the VCF** — recount `MOI` (6713/4365/2542/2506/914/749) and `MOI_CONFIDENCE` from the 17 789 records rather than trusting the pipeline's own summary | `compare/*` are all pairwise (`vcf_a vcf_b`) genotype comparators, or batch-table role differers. No single-VCF INFO tally | Absent |
| 31 | Assert header facts: sample column order (case, mother, father), `##INFO=<ID=MOI,…>` present | No VCF header assertion tool | Absent |
| 32 | Read the QC verdict and attribute the thresholds (10 PASS / 17 FAIL, against `single_sample.qc_def` named in a workspace attribute) | Nothing. `grep` for `PASS`/`FAIL`/`qc_def` across `compare/` returns nothing | Absent |
| 33 | Check annotation coverage of metrics (0 of 413 metric rows match `moi\|trio\|inherit`) | Nothing | Absent |
| 34 | Diff this run's QCDF against a v1.1.1 single-sample baseline QCDF (open item §14 #5 — still open *because* it needs this) | The baseline machinery is joint-calling-shaped (see 28); a single-sample baseline head-to-head does not exist here | Absent |
| 35 | Validate the emitted pedigree (defect D: `AddTrioSamplesToPed` makes the parents non-founders, a 2-cycle in `working_ped`, still open) | Nothing. `grep -i pedigree` across the repo returns nothing | Absent |

### The static-check layer, scored against the five defects

This is the layer the testkit exists to serve — "seconds, no data, no docker, no network" — and the
run it was pointed at produced five defects, three of which are statically visible.

| Defect | What it cost | Detectable offline? | A check in `checks/` today? |
|---|---|---|---|
| A — `write_lines()` at workflow scope cannot materialize on PAPIv2 | submission 1, ~10 s, $0.00, plus the round trip | Yes: a workflow-body `Decl` whose expression is `WDL.Expr.Apply` with `function_name` in {`write_lines`,`write_tsv`,`write_json`,`write_map`}. Measured: 1 finding at `91ea18cf`, 0 after the fix, 0 on `origin/main` (§3) | No |
| B — `File?` inputs present only for `defined()`, so Cromwell localized two whole-genome CRAMs | submission 2, **$6.47**, ~20 min × 2 attempts, zero stdout/stderr | Yes, exactly: a `File` input every placeholder mentioning it nests inside `defined(...)` and nowhere else. `has_mother_cram`/`has_father_cram` is the fix shape. Measured: 8 findings before the fix, 6 after, the two that disappear being the two CRAMs, and the surviving five are exactly open item §14 #4 (§3) | No |
| C — `set -o pipefail` + `awk … exit` against a 131 MiB `zcat` → rc=141, deterministic (`@RG` at header line 3368) | submission 3, **11 h 47 m**, $1.16, plus exhausted retries on shards 0 and 2 | Reproduction exists already, but in gatk-sv and in a container: `scripts/test/test_sigpipe.sh`, run on Cloud Build, extracting the block from `wdl/CollectCoverage.wdl` at run time (testkit `docs/handoff/003` §2). What is missing here is the offline half: a scan of `command{}` blocks for `pipefail` + an early-exiting consumer, across all WDLs, which is how the *remaining* sites get enumerated rather than guessed | No |
| D — `AddTrioSamplesToPed` emits an invalid pedigree (2-cycle) | still open, silent: shipped in the run's `working_ped` | Yes post-run (assert founders + acyclic on the downloaded PED), and pre-run by rendering the `printf` against synthetic ids | No |
| E — stale `final_bed` in the config outputs map | run 4 **$18.87 / 20 h** reported `Failed` after `Succeeded`, then a full resubmit | Yes: compare the config's output names against the workflow's declared outputs. `check_maps` already does the input half | No |

Four of five (A, B, C, E) are statically detectable; D needs rendering the task's `printf` against
synthetic ids, which is the pattern doc 001 §1 already used to validate `ValidateTrioInputs` before
anything was submitted. E is a six-line extension of `batch_configs._declared_inputs`, and E alone
accounts for the largest single spend of the workstream. `wdl_gate.sh` measures exit code,
IncompleteCall and stale bindings — the *binding* layer — and nothing measures the *runtime
semantics* layer where A, B and C live.

## 3. I wrote the two missing static checks, and they cost nothing to write

To keep this from being a review that only asks for things, I prototyped the A and B checks in throwaway
scripts and ran them over real trees, using this repo's own `./.venv/bin/python` (miniwdl 1.15.0) and
`git archive` of the WDL tree at three refs. Nothing was committed; the point is the measurement.

Check A, in full: load each `wdl/*.wdl` with miniwdl, walk `doc.workflow.body`, flag any `Decl` whose
expression is an `Apply` with `function_name` in `write_lines|write_tsv|write_json|write_map`.

```
$ python /tmp/scan_a.py wdl        # wdl/ materialized by `git archive <ref> wdl`

### trio branch BEFORE the fix (91ea18cf):
workflow-scope write_* : 1
    ('GATKSVPipelineSingleSample.wdl', 'GATKSVPipelineSingleSample', 'raw_trio_samples_list', 'write_lines')

### the fix commit itself (b0756590):
workflow-scope write_* : 0

### origin/main:
workflow-scope write_* : 0
```

All 119 files loaded, 0 failures. Zero findings on main, one on the broken ref, zero after the fix:
that is the zero-delta baseline shape `wdl_gate.sh` already ships, so it drops into the existing gate
as a third count column rather than a new oracle.

Check B: per task, collect the root names of every placeholder in `task.command.parts`, note whether
each is nested in `defined(...)`, then report any declared `File`/`Array[File]` input whose every
appearance is `defined`-only.

```
B  File input used only inside defined(): 8      # at b0756590 — one commit before the CRAM fix
    ('GATKSVPipelineSingleSample.wdl', 'ValidateTrioInputs', 'mother_cram')
    ('GATKSVPipelineSingleSample.wdl', 'ValidateTrioInputs', 'father_cram')
    ('GATKSVPipelineSingleSample.wdl', 'ValidateTrioInputs', 'dragen_vcf')
    ('GATKSVPipelineSingleSample.wdl', 'ValidateTrioInputs', 'case_manta_vcf')
    ('GATKSVPipelineSingleSample.wdl', 'ValidateTrioInputs', 'case_melt_vcf')
    ('GATKSVPipelineSingleSample.wdl', 'ValidateTrioInputs', 'case_scramble_vcf')
    ('GATKSVPipelineSingleSample.wdl', 'ValidateTrioInputs', 'case_wham_vcf')
    ('TasksMakeCohortVcf.wdl', 'RenameVariantIds', 'vcf_index')

B  File input used only inside defined(): 6      # at 6b666685 — the CRAM fix itself
    ('GATKSVPipelineSingleSample.wdl', 'ValidateTrioInputs', 'dragen_vcf')
    ('GATKSVPipelineSingleSample.wdl', 'ValidateTrioInputs', 'case_manta_vcf')
    ('GATKSVPipelineSingleSample.wdl', 'ValidateTrioInputs', 'case_melt_vcf')
    ('GATKSVPipelineSingleSample.wdl', 'ValidateTrioInputs', 'case_scramble_vcf')
    ('GATKSVPipelineSingleSample.wdl', 'ValidateTrioInputs', 'case_wham_vcf')
    ('TasksMakeCohortVcf.wdl', 'RenameVariantIds', 'vcf_index')
```

The two lines that vanish between the refs are defect B: the count falls exactly when
`mother_cram`/`father_cram` became call-site Booleans, which is the diffable-delta behaviour
`wdl_gate.sh` already sells. The five that survive are the sites doc 002 §6 B says were "deliberately
left alone" and §14 #4 carries as an open item — the check finds precisely that residue, no more and
no less. One pass at the start of the workstream would have listed all seven before the $6.47
submission, instead of two days and $18.87 later in a handoff doc. The eighth
(`RenameVariantIds.vcf_index`) is one finding to triage, and it is not obviously a false positive: a
`.idx` localized and never opened is the same trap in cheaper clothing.

The implementation notes a future author should not re-learn: `miniwdl` 1.15.0 exposes
`doc.workflow` singular (one workflow per file holds, in gatk-sv), `doc.tasks`, and
`task.command.parts` (not `task.command_parts`); placeholder root names come from `WDL.Expr.Ident`
leaves; `Apply.function_name` is a ply `Token`, so compare `str(tok)`. Those cost me one failed run
each.

## 4. Three of the testkit's own rules were not applied to this work

**Probes.** `CONTRIBUTING.md:22-26`: a reproduced defect gets a reproduction in
`scripts/probe_fixes.py`, one function, one `PROBES` entry, pinned count raised, plus a control
proving the guarded path was reachable. `scripts/probe_fixes.py` has 13 probes and zero occurrences
of `write_lines`, `sigpipe`, `141`, `final_bed`, `localiz`, `pedigree` or `moi`. The five defects
landed as §8 prose, which is exactly the "the guard never fired can be mistaken for the guard
worked" failure this repo says it fears most. A, B and E are pinnable from the §3 scans as they
stand. For C the reproduction is real but lives in gatk-sv and needs a container (`scripts/test/
test_sigpipe.sh` on Cloud Build, per `docs/handoff/003` §2) — pinning it here is the offline half of
the same finding.

**Module coupling.** `docs/module-profiles.md` §1 already inventories what is genotyping-specific,
and the trio run confirms the inventory from the other side: nothing in it can address a
single-sample run, because the config maps, attribute suffixes, entity type (`sample_set`), and table
roles are all joint-calling. A single-sample/trio head-to-head would need a profile of that doc
built. That is a proposal with a `PROPOSED` fence, not a feature.

**The publish gate is red at HEAD, for the same reason.** `make audit` at `f8d158a` reports
`FAIL — 9 hit(s) in 1 file(s)`, all in `docs/handoff/003-single-sample-blockers-pr966.md`: a real
project name, a derived registry path, an absolute home path, and an `fc-…` bucket URL. The README
says "No file is exempt", and handoff docs are exactly where those values accumulate, so the gate and
the habit are in tension and the gate is currently losing. This review was written against that
backdrop and passes it: with the file added to the tracked set, `make audit` still reports
`hits=9 files=1`, i.e. none of them mine. Worth deciding deliberately — either handoff docs get a
placeholder convention, or the audit learns a `waive` file per doc — rather than leaving a red gate
that the next session learns to skim past.

## 5. What is genuinely sufficient here

Worth saying plainly, because these are load-bearing and were used:

- `docker/gatk-sv-build.sh` — the branch built images with it, on hardware that cannot build
  `linux/amd64` natively. No substitute was needed.
- `checks/wdl_gate.sh` via `gsvtk gate` — the IC/stale zero-delta baseline is the static evidence in
  doc 002 §9, measured identical at two separate fix commits.
- `kit/gsvtk-config` — even the external `gsvpeek.sh` reads the namespace through it.
- `terra/recon.py`, `terra/wdl_flat.py` — generic, worked on the sandbox as-is.
- `terra/batch_save_metadata.py`'s explicit-target mode and `batch_cost.py` — a real, auditable
  cost path for an arbitrary submission, sitting unused because the ad hoc `curl` was closer to hand.
- `docs/terra-head-to-head.md` §8, `docs/troubleshooting.md`'s verbatim-error-text keying, and the
  skill's read-only whitelist. The branch's API lessons are in this repo, findable, with the error
  text a future session will actually have.

The shape of the gap is consistent: **the testkit is strong on loops, weak on one-offs, and its
Terra half is a joint-calling half.** Everything the trio run needed that was *not* a loop — a
config built for one workflow, an output fetched by name, a count re-derived from one VCF, one QC
table read, one pedigree checked — was hand-rolled, and the hand-rolled pieces are the ones whose
lessons turned into defects.

## 6. Ranked recommendations

Each names the trio cost it would have prevented or recovered.

| Priority | Change | Effort | Trio evidence |
|---|---|---|---|
| 1 | Make submission/config listing survive the lying endpoint: raw-REST (`session()` at `terra/terra.py:99`) a `GET …/submissions` and `…/workspace-definitions` path, keep `fapi`, and fail loudly if the two disagree. Fixes `batch_status`, `latest_workflow`, `gsvtk terra status` | small | doc 002 §10: `[]` with 5 submissions present |
| 2 | Validate config **output** names against the workflow's declared outputs, in `check_maps` beside the input check. Extend `--against` to a single-sample workflow | small | defect E: $18.87 + 20 h reported `Failed`, plus run 5 |
| 3 | New static check: workflow-scope `write_*` in any production WDL, as a third count column beside IC/stale, diffed against a base ref. **Prototype in §3: 0 findings on `origin/main`, 1 on the broken ref** | small, prototyped | defect A: a failed submission |
| 4 | New static check: `File` input referenced only inside `defined(...)`. **Prototype in §3: all 7 trio sites at the first pass** — the 2 that cost $6.47 and the 5 still open as §14 #4 — plus 1 to triage | small, prototyped | defect B: $6.47, and open item §14 #4 (`dragen_vcf`, `case_*_vcf` — same trap, still unpatched) |
| 5 | New static check: `pipefail` + an early-exiting pipe consumer in a `command{}` block, reporting **every** site so the remaining ones are counted rather than remembered. The container proof of the bug already exists in gatk-sv (`scripts/test/test_sigpipe.sh`, Cloud Build, `docs/handoff/003` §2) | medium | defect C: 11 h 47 m, and a divergent second fix on `mw_fix_single_sample_blocking` that one shared probe would have reconciled |
| 6 | Fold `gsvpeek.sh` into `terra/` as a bounded call-level peek (`executionStatus` tally, running shards, first failure message, never the raw dump). It already reads this repo's config | small | doc 002 §9 needed "33 Done + MakeCohortVcf Running"; the tool exists, in `~/Work` |
| 7 | Cache-hit accounting from saved expanded metadata: calls with `callCaching.hit`, split by vm-minutes. Prints the sentence, not a guess | small | doc 002 §7 item 5: "cache reuse covers evidence gathering, not the back half" — an estimate that was wrong when the owner chose it |
| 8 | Artifact fetch by **workflow output name** for any workflow + checksummed manifest under `$GSVTK_WORK` | medium | the 17 outputs, sha256s and the durable trio copies all live outside this repo |
| 9 | Single-VCF reading tools that the branch actually needed: an INFO/FORMAT value tally (re-derive a pipeline summary from the VCF), a header assertion, a `sv_qc.tsv` PASS/FAIL tally with the `qc_def` path it came from, and a metrics key-coverage probe | medium | doc 002 §1's independent MOI recount, 10 PASS / 17 FAIL, and 0-of-413 metrics keys |
| 10 | Pedigree validator (founders, no cycle, sex codes) runnable on a downloaded `working_ped` | small | defect D, still open |
| 11 | A single-sample module profile so freeze/fetch/compare can address a single-sample baseline, then run open item §14 #5 (trio QCDF vs v1.1.1 single-sample QCDF) | large, and it is `docs/module-profiles.md` | the reason §14 #5 and §14 #10 (RD cutoffs unquotable) are still open |
| 12 | Pin the four detectable defects in `scripts/probe_fixes.py` with controls, and raise the pinned count | small | `CONTRIBUTING.md:22-26` |
| 13 | Two cheap additions: PyYAML in `requirements-dev.txt` (so `.github/.dockstore.yml` is parseable, not text-inspected — verified absent: `./.venv/bin/python -c "import yaml"` fails today while `import WDL` succeeds), and a womtool jar pin (fetch + version, or refuse) for the `validate.sh` matrix | small | doc 002 §10: "No PyYAML in any local venv"; doc 001 §2: jar downloaded and deleted after every run |

Items 1-4, 6, 7, 10 and 12 are what I would call cheap-and-real: each maps to a defect or a
correction that cost a submission, an hour of wall clock, or a wrong number given to the owner. Two of
them (3, 4) are no longer estimates: they were written once already, in a scratch directory, in
roughly 20 lines each, against the interpreter this repo already installs.

## 7. Limits of this review

- I did not run the image build, any Terra call, or any `compare/` tool. Tool behaviour is from the
  named file's code and `--help` surface; file:line references were opened, not inferred. What I *did*
  run: the two prototype scans in §3 against `git archive` trees of `91ea18cf`, `b0756590`,
  `6b666685` and `origin/main`, and the `import yaml` / `import WDL` check in §6 item 13.
- The task list is only as complete as docs 001 and 002. Both are unusually explicit (command,
  verbatim error, expected value), but ad hoc `grep`/`git show` reasoning about WDL semantics is not
  enumerated there and I have not counted it as a task.
- The §3 scans are prototypes in `/tmp`, not contributions: no fixture tree, no controls, and no
  handling for a WDL that fails to load (they `continue`, which hides findings rather than reporting
  them — the opposite of what `check_docs.py` and `svshell_jq_plumbing_scan.py` do about the same
  trap). Counts are for the four refs measured, not a claim about every ref.
- **This repo moved while I was reading it.** It was at `d63c771` when I started and is at `f8d158a`
  ("Handoff 003: single-sample blockers…", +208 lines of `docs/handoff/003-single-sample-blockers-pr966.md`,
  another session's work) at writing time. I re-ran every load-bearing grep against the new HEAD:
  `fapi.list_submissions` in `terra/terra.py`, the hardcoded `STEPS` in `batch_status.py:27`, the 13
  probes in `probe_fixes.py`, `wdl_gate.sh:61` — all unchanged, since that commit touches only
  `docs/`. Doc 003 covers the *other* branch's SIGPIPE work and is cited above where it changes a
  recommendation.
- Tasks 1-4 and 12 are recorded in the branch docs as *actually executed with the testkit*; the rest
  of the `Covered` rows mean "a tool in this repo performs this task", which is the question asked.
- Doc 002 §9 refers to the gate as `testkit/scripts/gsvtk gate`; the wrapper is at
  `.pi/skills/gatk-sv-testkit/scripts/gsvtk` (`VERSION="0.5.0"`, `gate` accepts `--repo DIR`). The
  flag is right, the path is stale.
