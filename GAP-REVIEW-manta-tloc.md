# Could this toolkit have run the manta tloc auto-resolve validation?

A capability review of **gatk-sv-testkit as it stands today** (`d958e8b`, offline gate:
`make test` → **32 ok, 0 skipped, 0 failed**), graded against the testing tasks actually performed
on a real gatk-sv branch: the manta single-tloc auto-resolve work (`svtk resolve`
`--resolve-single-tlocs`, `src/svtk/svtk/cxsv/complex_sv.py`, one caller in
`src/sv-pipeline/00_preprocessing/scripts/mantatloccheck.sh`), whose change sits in **step 04**
(`GatherBatchEvidence` → `TinyResolve` → `ResolveManta`).

**Verdict: roughly half.** The toolkit covers every *free, static, and build* task on that branch,
and covered them in practice. It could not have run **either** validation leg — the docker A/B or
the Terra cohort A/B — and both legs, which are the evidence behind the branch's PR, were
hand-built tooling living outside any repository.

That is not a surprise the docs hide. The README says: *"the run-the-pipeline parts are shaped
around the Genotyping module today — generalising them to any module is
[docs/module-profiles.md](docs/module-profiles.md), which is a proposal, not a feature."* The tloc
change happens to fall outside that shape. What this review adds is the measurement: which loops
broke, at which line, and what had to be written instead.

## Provenance and redaction

Nothing here is recollected from a session transcript. The task list came from three artifacts on
disk, re-read now: the branch's two handoff docs (`docs/handoff/manta_tloc_autoresolve.md` and
`docs/handoff/002_…_pr_and_terra_ab.md`), the harness directory's own `PLAN.md`, and the harness
scripts themselves. Claims labelled **code-read** were verified here against this checkout; claims
labelled **reported** come from those documents and were not re-run in this session.

Project, workspace, registry and machine-path values are **not written here**, and neither is the
branch's own name: per `CONTRIBUTING.md`, a personal coordinate is not the toolkit's to publish, and
`docs/terra-head-to-head.md` already redacts a person-named branch as `<branch-under-test>`. The
real values live in the branch, its PR, and the work dir's `PLAN.md`.

## 1. Task by task

| Task performed on the branch | Toolkit tool | Verdict |
|---|---|---|
| Resolve-semantics unit harness (19 checks: label from cytoband arms, strand-class cross-check, demotions, flag-off regression, merged manta+wham) | — | **absent** |
| WDL launchability A/B across refs, incl. `TinyResolve` | `checks/wdl_gate.sh --wf <WF> <base-ref> <branch-ref>` | **covered** (reported; the default list at `checks/wdl_gate.sh:61` omits `TinyResolve`, so `--wf` had to name it) |
| `sv_shell` JSON-contract and jq-null sweeps, base-vs-branch delta | `checks/svshell_contract_check.py`, `checks/svshell_jq_plumbing_scan.py --compare-to` | **covered** (reported) |
| Build the branch's `sv-pipeline` image with no local Docker, commit-pinned tag, personal namespace, `--check`/`--dry-run` first | `docker/gatk-sv-build.sh` (default target is `sv-pipeline`) | **covered** (reported; 25.4 VM-minutes, one ephemeral x86_64 VM) |
| Per-investigation isolation: own profile, own `$GSVTK_WORK`, own Terra workspace, no shared state with another investigation | `GSVTK_CONFIG=<per-investigation>.env` | **covered** (reported) |
| Trim a step-04 sub-workflow to a tloc-only harness, publish it, configure it, submit it over the cohort | — | **absent** |
| Freeze the step's real inputs server-side and prove the bytes | `terra/batch_freeze.py` (freezes `FROZEN_ATTRS`, the 17 `sample_set` attributes steps 06/07 read) | **absent for step 04** (317 objects / 8.65 GiB frozen by hand) |
| Two submissions differing in exactly one line (`sv_pipeline_docker`) | `terra/batch_rerun_step.py` — `CONFIG = "10-GenotypeBatch-rerun"`, `ETYPE = "sample_set"` (`:38-39`), `--step` choices `{06..10}` | **absent** |
| Per-sample label census over 156 × 3 arms with CONTROL / EFFECT / PURITY / CLOSURE verdicts | `compare/` — table differ, RD copy-state diff, GQ scale + paired GQ, pair-level concordance, profile summariser | **absent** |
| Recover output paths when the Terra entity attributes stayed empty and only Cromwell's `outputs` had them | `terra/batch_fetch_compare.sh fetch` reads `*<suffix>` **entity attributes** only | **absent** |
| Per-task cpu-VM-minutes for one task across two submissions; attempt/retry counts to tell preemption from an image-specific failure | `terra/batch_save_metadata.py --per-call --ns … --ws … --submission … --workflow …`, `terra/batch_cost.py` | **partial** (code-read: explicit targets and `--per-call` exist; `batch_cost.py` reports per-step and chain totals, not one task inside one workflow) |
| Run both images against real per-sample inputs on a VM and census what comes out | `checks/image-check/*.sh` is the same VM skeleton (boot → `docker create` + `docker cp` → serial-log marker → VM kept alive so the evidence survives), but scoped to `/opt/sv_shell` md5s and `/opt/gatk.jar` flags | **absent** |
| Prove the built image carries the bytes of the branch's changed file | `checks/image-check/svshell_image_check.sh:104-108` md5s `/opt/sv_shell/{single_sample_pipeline.sh,sample_inputs/…json}` against `git show <ref>:` | **absent for `sv-pipeline`/svtk** — the A/B leaned on a commit-SHA tag plus a registry digest, which proves the tag's provenance, not that the file inside came from those commits |
| Adversarial multi-lane review (logic / blast radius / runtime) | pi-subagents, not this repo | out of scope |

## 2. What is absent, ranked by what it would have saved

### 2.1 Nothing in the toolkit reaches step 04

`PLAN.md` states the blocker in the work dir's own words: the shipped configs cover steps 06-10 and
*none of them reaches `TinyResolve`*, so "a 06-10 rerun with the branch image would exercise the
image and never the change." The only caller of the new flag is reached from exactly one task:
`mantatloccheck.sh` ← `TinyResolve.wdl` task `ResolveManta` ← `GatherBatchEvidence.wdl`.

The genotyping literals are exactly the ones `docs/module-profiles.md` §1 inventories —
`batch_configs.CONFIGS` (64 input + 36 output bindings for 06→10), `batch_freeze.FROZEN_ATTRS`,
three separate copies of the step→workflow map (`batch_check_inputs.py`, `batch_save_metadata.py`,
`batch_status.py`), `batch_rerun_step.py`'s config/entity/prefix, and
`batch_fetch_compare.sh`'s seven attribute names. The diagnosis is written down; the cure is fenced
as `PROPOSED` (`docs/module-profiles.md:3`, *"Nothing here runs today"*).

### 2.2 No path for a trimmed or custom workflow

Needed and written by hand: a generator that extracts `struct RuntimeAttr` + `UntarFiles` +
`ResolveManta` **verbatim** from the repo and re-reads its own output to assert byte-identity, then a
method config over a **custom root entity type**.

Two toolkit walls were hit (**code-read**): `terra/wdl_flat.py` bundles a *single-workflow* closure
and refuses `TinyResolve` by name, because its import `GetShardInputs.wdl` declares a workflow of
its own — and there is no workspace-method POST helper anywhere in `terra/`. The workaround inlined
`GetShardInputs` as plain WDL and published through `.dockstore.yml` instead, which cost a
Dockstore *unpublished-versus-missing* investigation (§4). But the primitives are already generic —
`terra.submit(ns, ws, ns, cfg, entity, entity_type, expression, confirm=True)` takes any entity
type, and `upload_entities_tsv` creates one — so this is largely a **recipe and CLI gap**, not a
missing API.

### 2.3 No comparator for the measurement the PR rests on

The verdict shape was: **CONTROL** (baseline arm identical to the production artefact, per sample,
on records *and* every label), **EFFECT** (every sample changed by exactly the added `CTX` count),
**PURITY** (zero non-`CTX` label moved), **CLOSURE** (`SINGLE_ENDER` delta equals `CTX` added).
Measured: 156/156 CONTROL, 156/156 EFFECT, `+27,267` complex records `==` added `CTX`, zero other
label moved; docker leg on 3 samples gave `CTX` 172/160/155 with the closure identity holding
(reported).

Nothing in `compare/` does an INFO-label census. Worse, the trap that produced a false `REVIEW` was
the exact class this repo has a rule for: a comparator asserted an invariant that was not true
("the baseline arm must have `CTX == 0`"), and an `elif` chain silently skipped the remaining checks
for the samples that violated it. The data was fine — the production baseline itself contains 7
`CTX_*` records with the flag off, reproduced exactly by the baseline arm; the flag accounts for
27,267 of 27,274. `README.md`'s *"an empty table that reads like 'no differences' is the failure this
repo fears most"* is the right instinct; no comparator implements it for labels. **This is the
single most reusable artifact to upstream** — free, offline, and independent of which module you are
testing.

### 2.4 No runner that executes an image

Building the image is 25 minutes of the story; running it on real inputs was the other 90%.
Six scripts were hand-written (VM startup, per-sample input prep, metadata packing, per-sample
driver, two A/B revisions). The two scariest bugs were **harness** bugs, not pipeline bugs:

* the runner printed `MISSING LOCAL IMAGE` and carried on; both arms died `rc=1` with `records=0` —
  *"which reads exactly like the flag changed nothing"*;
* `bash -n` passes on an empty file and `grep -c` returns 0 for one, so both guards approved a
  **0-byte startup script**, costing ~20 idle VM-minutes.

`checks/image-check/jar_flag_probe.sh` already solves the hard parts of that pattern (VM kept
RUNNING deliberately, because *"GCE makes serial output unreadable once an instance is TERMINATED"*).
Given `CONTRIBUTING.md`'s bar — *"it removes an hour of waiting or catches a bug before it costs VM
money"* — a runner that **refuses to score a zero-record arm** is squarely in scope, and is the
inverse of the failure this repo says it fears.

### 2.5 No local python/svtk loop

`replay/` is Java trainers only (`replay/build_inputs.py` hardcodes the `SVShell.` prefix),
`requirements.txt` is deliberately unpinned, and pysam appears only as a `compare/` reader. The
branch needed a venv with an editable `svtk` and — the load-bearing fact — an understanding that
`dockerfiles/sv-pipeline-virtual-env` pins **`pysam==0.15.4`** while a local 0.24 cannot write a
header-`add_line`-added INFO tag through `resolve.py`'s `bcftools sort` stdin pipe:
`[E::vcf_format] Invalid BCF, the INFO tag id=16 is too large` (reported). That divergence is the
whole reason the flag-on path could not be tested locally and needed a docker rebuild. A documented
production pin, or a production-pinned local venv, turns a 25-minute image build into a
`pip install`.

## 3. Two defects this review reproduced

### 3.1 `terra/recon.py` can exit 0 on a silently empty submission inventory

`terra/terra.py:150` uses `firecloud.api.list_submissions` — the endpoint this repo's own
`docs/terra-head-to-head.md` §8 says lies: *"GET …/submissions via
`firecloud.api.list_submissions` → `[]` … Raw REST with a `gcloud auth print-access-token` bearer
returns the truth."* `recon` counts a section as failed only when the printed line contains
`" failed:"` (`terra/recon.py:35`), and the summary line (`:148`) is
`f"   {len(subs['submissions'])} recent -> recon/baseline_submissions.json"` — no such marker.

Offline reproduction (**code-read**, no credentials, run in this checkout):

```bash
cd terra && ../.venv/bin/python -c "
import recon
recon.p('   0 recent -> recon/baseline_submissions.json')
print('FAILS after the empty-inventory line:', recon.FAILS)
recon.p('   submissions failed: ValueError boom')
print('FAILS after a loud failure   :', len(recon.FAILS))
"
```

prints `[]` then `1`: the lying-empty case contributes nothing to `FAILS`, so recon reports an empty
inventory and exits 0. Note the configs section just above it (`:136-142`) *does* raw-REST — so the
file already knows the rule and applies it to one section and not the other.

The branch's session hit the neighbour of this (a `… 'str' object has no attribute 'get'` and a 2-byte
`baseline_configs.json`) and worked around it with a private read-only probe rather than reporting
it; `scripts/probe_fixes.py` pins neither. Both fixes want the same treatment: raw-REST
submissions like configs, name the endpoint when a list comes back empty, and add a probe whose
positive control is the lying-empty payload.

### 3.2 `useCallCache: True` is baked into the config body the A/B path POSTs

`terra/batch_configs.py:319` sets `"useCallCache": True`, and `terra/batch_rerun_step.py` builds its
config with `from batch_configs import body` — the builder, not a copy. Meanwhile
`docs/terra-head-to-head.md` §5 tells a two-arm A/B to want the opposite (*"`useCallCache: true` …
not what you want between arms if a tag is mutable"*), and both arms of the tloc A/B ran with it
**False**. There is no flag to express that today, so the documented guidance cannot be followed
with the shipped tooling. One `--no-call-cache` on the config path, or flipping the default and
letting a single-step rerun opt in, closes it.

## 4. Verbatim failure text that deserves a `docs/troubleshooting.md` row

That file is keyed on exact strings "so the next person can paste their error into a search box"
(`CONTRIBUTING.md`). These are absent from it today, each having cost a round trip on the branch:

| Verbatim | Cause it does not name |
|---|---|
| `400 "The request content was malformed:\nunexpected json type"` | **two** unrelated causes: `methodVersion` must be an **Int** on an agora ref, and real JSON list/int values in `inputs` (must be strings) |
| `400 Entity type workspace is reserved and cannot be overwritten` | the `rootEntityType: workspace` dead end, whose two predecessor errors are `400 … you haven't passed one to the submission` and `500 AttributeEntityReference(workspace,…) not found` |
| `409 <config> already exists` on a retry | Rawls **persists a config whose method resolution failed**, after which `valid` reads `None` forever and `POST …/methodconfigs/validate` answers `405 supported methods: OPTIONS` |
| `Validation errors: Invalid outputs: … -> Error while parsing the expr` | an inverted `outputs` map is **accepted at creation** and only fails at submission — the sibling of the documented "`invalidOutputs` is 0 even for a name the workflow does not declare" |
| `invalid choice: 'inputs'` (miniwdl) | the subcommand is `input_template`; and its required-only list is **not** "all declared inputs" (a defaulted input looks unknown to it) |
| `HTTP 404 Cannot get dockstore://… from method repo` | an entry that exists but is **unpublished** 404s identically to one that was never registered — and unpublished entries are invisible to the anonymous API *and* to Rawls. The existing row at `docs/troubleshooting.md:26` gets half of this right ("never published to Dockstore") but not that a *created, unpublished* entry looks like a missing one |
| `[E::vcf_format] Invalid BCF, the INFO tag id=16 is too large` | a pysam-version-stack divergence, not a corrupt VCF: production ships 0.15.4, a local 0.24 fails on header-added INFO tags written through a `bcftools sort` stdin pipe |
| `Job for docker0 …` / `rc=125` … `not found` | an image reference assembled from the build log's *prefix* and *name*, printed separately with a space before the tag |

Also worth one line wherever `docker/gatk-sv-build.sh` is documented: a piped `rc=0` printed after
`| head` is `head`'s status, which is how a miniwdl usage error stayed hidden.

## 5. Where the evidence tooling ended up

Everything that produced the PR evidence — the census comparator, the freeze + verifier, the Terra
read and write probes, the WDL generator, the freeze manifests, the 156 × 3 downloaded VCF cache —
lives in a per-investigation work directory that **is not a repository** (`git rev-parse` →
`fatal: not a git repository`), and that directory also held the SSH private key used for the A/B
VM. Path withheld above for the reason in §"Provenance and redaction".

This is structural, not carelessness. `work/` is gitignored scratch **by design**
(`README.md`: *"Nothing the tools produce is committed"*), so there was no slot for
*tooling* — as opposed to output — that a head-to-head needed and the repo did not have. It is the
actual root cause of §2.1-§2.5: the work went bespoke not because the capability was unknowable, but
because a non-genotyping step has nowhere to land.

## 6. Build order, if the next branch of this shape should be cheap

1. **The label-census comparator** with CONTROL / EFFECT / PURITY / CLOSURE and a recorded
   invariant-per-arm (so an invented invariant fails loudly instead of skipping checks). Free,
   offline, module-agnostic, immediately reusable — and it is the artifact a PR description cites.
2. **Parameterize the rerun path** by workflow / entity type / exported-attribute set, i.e. build
   `docs/module-profiles.md` §3 in the order its §9 lays out. The tloc case is the second data point
   that the genotyping transcription is the real cost.
3. **The in-image A/B runner**, on the `checks/image-check` VM skeleton, refusing a zero-record arm
   and refusing a 0-byte startup script, with `--per-call`-style attempt accounting so preemption is
   not read as an image bug.
4. **Ship the production python pins** (pysam and friends) as a fact, or generate a
   production-pinned local venv, so local-vs-production-stack divergence is something you read
   rather than rediscover.
5. The two §3 defects and the §4 rows, which are small, self-contained, and each want a probe with a
   positive control per `CONTRIBUTING.md`.

## 7. Limits of this claim

* The covered rows in §1 are **reported** from the work dir's `PLAN.md`, not re-run here: this
  session had no credentials and ran nothing that touches Terra, GCS or a VM. What was verified here
  is (a) the offline gate passes, (b) every code-read claim above, and (c) §3.1's reproduction.
* The absent rows are judged against the toolkit **as of `d958e8b`**, whose self-described boundary
  excludes them (README "What it is not", `docs/module-profiles.md` status banner). Finding the same
  wall twice is evidence the wall is real, not that it is unmovable.
* A full step-04 head-to-head (31,206 VM-minutes per arm, reported) was deliberately not run: the
  trimmed harness covers the tloc path, and its soundness argument (the dropped tasks are siblings,
  not ancestors) is in the branch's own handoff. That scope limit belongs to the branch's evidence,
  and no amount of tooling changes it.
