# Supporting any gatk-sv module: loops, profiles, and one question per oracle

> **Status: proposal, revision 2.** Nothing here runs today. Every command in a `PROPOSED` block
> names a flag or file that does not exist yet, so this doc is fenced off from the rest of `docs/`,
> where CONTRIBUTING's "never document a flag you did not run" holds.
>
> **Revision 1 was reviewed adversarially by three independent reviewers and it was wrong in four
> load-bearing places.** §13 lists each overturned claim, the measurement that overturned it, and
> what replaced it — published rather than reworded away, per
> [methodology.md](methodology.md). Three shipped defects surfaced during that review and are listed
> in §12 with their reproductions, because they are real whether or not this design is ever built.
> Two of the three are now **fixed and pinned** (§12 records both fixes); the third — a
> `wdl_gate.sh` example naming a workflow absent at `main` — still stands.

The question this answers: **how does this toolkit support a module other than genotyping without
becoming either a pile of per-module flags or a second copy of gatk-sv?**

Genotyping is the example because it is the module actually driven end to end. It is not the schema.

## 1. What is genotyping-specific today (corrected inventory)

The loops are module-agnostic: `docker/gatk-sv-build.sh <branch> [targets…]` takes any image, and
`kit/` carries no module name in any *value*. What is specific is upstream facts hand-copied into
code, in more places than revision 1 admitted.

| Where | What is transcribed |
|---|---|
| `terra/batch_configs.py` `CONFIGS` | 64 input + 36 output bindings for steps 06→10 (what `show` prints in 105 lines), plus `CALLERS`, `BRANCH_ONLY_INPUTS` and a 12-line rationale for one of those keys |
| `terra/batch_freeze.py` `FROZEN_ATTRS` | the 17 `sample_set` attributes 06/07 read upstream |
| `terra/batch_check_inputs.py:42` `WDLS` | step→workflow map — **copy 1 of 3** |
| `terra/batch_save_metadata.py:36` `STEPS` | step→workflow map — copy 2 of 3 |
| `terra/batch_status.py:27` `STEPS` | the 5-step chain — copy 3 of 3 (revising a claim made in rev 1: `batch_status.py` *does* hardcode the genotyping chain) |
| `terra/batch_fetch_compare.sh:36-38` | the exported-attribute list — **7** names, and the largest concentration of genotyping literals after `batch_configs.py` |
| `terra/batch_rerun_step.py` | `CONFIG = "10-GenotypeBatch-rerun"`, `ETYPE = "sample_set"`, the `GenotypeBatch.` key prefix, `body("10-GenotypeBatch")` |
| `terra/stage_inputs.py:10,346` | `genotyped_depth_vcf` inside a generic tool's *help and error text* |
| `checks/wdl_gate.sh:61` | `WFS=(SVShell GATKSVPipelineSingleSample GenotypeBatch MakeCohortVcf)` |
| `checks/image-check/*.sh` | `GenotypeSVs` **and the flags it probes** (`jar_flag_probe.sh:52`: `rd-depth-table rd-pesr-table rd-table`) |
| `replay/build_inputs.py:57,159-214` | the #961 rename, and a hardcoded `SVShell.` prefix — the replay loop is not parameterized by workflow at all |
| `compare/compare_batch_tables.py:38-55` | 7 table roles, 3 readers, tolerances, `RENAMES`, the 10× `SRQ`/`PEQ` scale note |
| `compare/diff_rd_states.py:26,31-32` | baseline/new VCF defaults, computed **at import** |
| `Makefile:242`, `kit/gsvtk-config:57` | an audit pattern; "used when running the java genotyper" |

Two vocabulary lists disagree across the repo today — `terra/recon.py:110` and
`terra/fetch_baseline.py:129` carry different entity-type lists (`recon` has `batch`,
`fetch_baseline` does not). Anything that adds a *fourth* place to say "sample_set" makes that
worse, which is why §3 does not introduce `root_entity` as a free-standing field.

The 12-line comment at `batch_configs.py:80-92` — main declares 21 inputs, the branch 26, this is
the key a main-shaped run must drop, Rawls rejected the POST — **is the price of transcription.**
Everything below is organized around paying it once instead of per module, and around *not*
re-stranding that rationale (§3's `_why_*` rule) when the table moves.

## 2. The model: three layers, and a different oracle for each question

```
loops (code)      build · freeze · configs · gate · run · fetch · compare
                      |  reads by name, never names a module
                      v
profiles (data)   profiles/<module>.json + profiles/<module>.lock.json
                      |  graded against, by two oracles that answer different questions
                      v
gatk-sv (a ref)   wdl/*.wdl via git archive + miniwdl        <- what an input IS
                  inputs/templates/**/*.json.tmpl            <- what PRODUCTION binds
```

Dependency runs one way, and **neither gatk-sv source is vendored**: both are read out of
`GSVTK_GATK_SV_CHECKOUT` at a ref with `git archive` (which cannot touch the checkout's worktree,
index or HEAD — the existing rule from `scripts/fetch_wdl.py` and
`batch_configs._wdl_dir_from_ref()`).

The three questions, and the only thing that can answer each:

| Question | Answered by | Cannot be answered by |
|---|---|---|
| does input `K` exist? is it required? what is its type? | miniwdl on the WDL at a ref | the template (it can bind a key the WDL dropped) |
| which inputs does production actually bind, vs leave to a WDL default? | the `.json.tmpl` at a ref | miniwdl (an optional input is silent about who supplies it) |
| **which attribute or literal feeds input `K`?** | the `.json.tmpl` at a ref | nothing else. Measured: **196 of the 473 bindings across the 28 cohort templates (41%) bind to an attribute whose leaf name differs from the input name** — `median_coverage←this.median_cov`, `rd_file←this.merged_bincov`, `depth_exclusion_intervals←workspace.bin_exclude`, `batch←this.sample_set_id`. Plus 98 literal/`null`/array values. |

That last row is why revision 1's plan ("the profile declares the bindings it makes; miniwdl grades
it") fails: the input→attribute wiring exists in no machine-readable place *except* the templates,
so refusing them makes the profile hand-carry ~100 bindings and re-creates the transcription with a
schema and seven gates around it. §4 records the measurement and revokes rev 1's reason for
refusing them.

**A module is chain-shaped, not doc-shaped** — the unit of support is "the steps I can freeze,
rerun and diff in one head-to-head". gatk-sv publishes 25 module pages plus an index
(`website/docs/modules/`); mirroring that taxonomy would import an obligation no loop needs. Note
the consequence §9 step 5 must face: a chain sizes the **rerun** scope, while the **freeze** scope
is bigger than the chain (a 2-workflow CPX chain draws on ≥6 upstream producers), so the profile
must name producers that are not in the chain.

## 3. The profile, revised

Per-step, because that is the shape the data has. Generated for the example below from
`batch_configs.py show` rather than typed, then hand-minified.

```json
{
  "schema_version": 1,
  "name": "genotyping",
  "steps": [
    { "step": "10-GenotypeBatch",
      "wdl": "GenotypeBatch",
      "workflow": "GenotypeBatch",
      "inputs": {
        "GenotypeBatch.batch": "this.sample_set_id",
        "GenotypeBatch.vcf": "workspace.merge_batch_sites_vcf{new}",
        "GenotypeBatch.median_coverage": "this.median_cov{frz}",
        "GenotypeBatch.training_vcf": "this.outlier_filtered_pesr_vcf{new}" },
      "_why_GenotypeBatch.training_vcf": "branch-only: main trains PE/SR from `vcf`, the branch from a separate batch VCF. Both are coherent pipelines; they are different pipelines. Dropping it is a semantic change, not a fix.",
      "outputs": { "GenotypeBatch.genotyped_depth_vcf": "this.genotyped_depth_vcf{new}" },
      "export": ["genotyped_depth_vcf", "genotyped_pesr_vcf", "genotyping_rd_depth_table",
                  "genotyping_rd_pesr_table", "genotyping_pe_table", "genotyping_sr_table",
                  "genotyping_sr_cutoff_diagnostics"],
      "freeze": [
        { "attr": "clustered_@_vcf", "callers": true, "sidecar": "index" },
        { "attr": "merged_PE", "sidecar": "index" },
        { "attr": "median_cov" } ] }
  ],
  "compare": [
    { "role": "sr_params", "tool": "compare_batch_tables.py" },
    { "role": "depth_vcf_pair", "tool": "diff_rd_states.py",
      "inputs": { "baseline": "staging/<batch>.genotyped_depth.vcf.gz",
                   "new": "runs/train/train.genotyped.vcf.gz" } } ]
}
```

Nine rules, most of them review-derived:

1. **`wdl` and `workflow` are separate fields.** They differ for 12 of the 118 WDLs at `main`
   (`DepthClustering→ClusterDepth`, `PloidyEstimation→Ploidy`, `Genotype_2→Regenotype`,
   `ExpansionHunterDenovo→EHdnSTRAnalysis`, …). `dockstore()` needs the file basename,
   `_declared_inputs()` needs *both* `f"{workflow}.wdl"` **and** `wf.name == workflow`
   (`batch_configs.py:446-449` and `:455-456`), and `wdl_gate.sh` needs `$dir/$wf.wdl` to exist. One string cannot
   serve all three: for `DepthClustering.wdl`, the basename fails the name check and the workflow
   name fails the file lookup.
2. **`inputs` is the complete map, never a delta.** Terra has no "inherit from upstream": a config
   that omits an input silently takes the WDL default. The delta reading is not smaller, it is
   *a different pipeline that grades clean* — measured on `TrainGCNV`, whose template binds **53**
   keys while the WDL requires only **12**, so a "required + deltas" profile silently drops 41
   production-pinned knobs (`gcnv_model_*` epoch/iteration counts, `n_samples_subsample`,
   `do_explicit_gc_correction`) to defaults and reports success.
3. **A value is a path iff it begins `this.`/`workspace.`; otherwise it is a JSON literal.**
   Literals carry real types across the 28 templates: `gcnv_model_learning_rate: 0.03`,
   `max_shard_size: 500`, arrays via `| tojson`. A string-only grammar cannot express them, and
   `TrainGCNV` is 39 literals of 53 bindings.
4. **Index/sidecar closure is a code rule, not profile data.** "Freeze an attribute ⇒ freeze its
   declared index sibling" belongs in `batch_freeze.py`, with a probe asserting the resolved list
   equals today's 17 byte-for-byte. Rev 1's example listed 7 attrs and `@`-expanded to 9, silently
   unfreezing 8 `_index` sidecars — and `batch_freeze.py:84-95` records the real incident where two
   attributes collapsing to one frozen object made "every head-to-head after it compare against
   partly wrong inputs". A per-module author must not get to re-create that by omission.
5. **`export` is the target-attribute mapping (name → `this.<attr>` + suffix), not a restatement of
   the WDL's outputs.** The forbidden list in rev 1 banned "output names a WDL declares" while
   `export_attrs` was exactly that list, and `jar_probe.image` was a docker image name. Banned
   instead: the *set* of declared/required input names, WDL defaults, image **URIs/tags**, workflow
   existence — all of which the ref answers.
6. **`_why_*` siblings carry the rationale, and `check_profiles.py` enforces the pairing.** JSON has
   no comments, and this repo already solved that: `replay/images.example.json:2-7` is load-bearing
   *because* of its `_about` / `_why_explicit` / `_gatk_note` pseudo-keys. Rev 1 said "no comments"
   and banned per-module docs, which strands exactly the 12-line rationale §1 calls the price of
   transcription. `check_maps` and every reader ignore `_`-prefixed keys.
7. **`freeze` entries are objects, not strings, and name their entity path.** Production binds
   across a collection in 14 of the 28 templates (`TrainGCNV.count_files←${this.samples.coverage_counts}`,
   `ResolveComplexVariants.disc_files←${this.sample_sets.merged_PE}`,
   `AnnotateVcf.stripy_vcfs←${this.sample_sets.merged_stripy_vcf}`). Freezing those means writing
   `<attr>{frz}` onto every member row of *another* entity type, which `batch_freeze.py:253-254`
   cannot do (it emits one TSV headed `entity:sample_set_id`, one row). Measured:
   `TrainGCNV.count_files ← ${this.samples.coverage_counts}` (`TrainGCNV.json.tmpl:60`) and
   `ResolveComplexVariants.disc_files ← ${this.sample_sets.merged_PE}` (`:13`). Until that exists, **only
   root-entity-frozen chains are profile-eligible**, and §9 step 5 says so.
8. **`compare` entries carry `inputs` and, where the tool has them, tolerances/renames.**
   `compare_batch_tables.py` has **7** roles in three parallel tables (`PATTERNS`, `READERS`,
   `SR_SPECS`/`PE_SPECS`/`RENAMES`), so a `{role, tool}` pair with three invented role names needs a
   mapping table plus argument adapters — new Python against an acceptance test that forbids it.
9. **`schema_version` is mandatory.** With `{frz}` expansion, `@`-over-callers and a second module in
   flight, there must be a way to tell a stale reader from a stale file. Precedent: the skill's
   `version` and `scripts/gsvtk`'s `VERSION` are checked as a pair by `scripts/check_skill.py`.

**Format: still JSON, with one honest concession.** The floor is Python 3.9
(`undef_module_refs.py:32`, `kit/config.sh:24`), so `tomllib` (3.11+) is out; neither requirements file has a YAML
dep. Rev 1's other argument — "`compare/` is stdlib-only" — is **false**
(`compare/gq_scale_compare.py:24` imports `numpy`) and is retracted here. What rev 1 missed is that
`kit/gsvtk-config:96-98` already parses `KEY=value` with *one* parser for bash and Python, with a
docstring saying it chose that grammar so no parser is needed in shell. Profiles need `{frz}`/`{new}`
and `@`-expansion, and §5's consumers include **bash** (`checks/wdl_gate.sh`,
`checks/image-check/*.sh`). So JSON buys a second implementation of the same expansion semantics in
shell, which CONTRIBUTING names as the root cause of the worst bugs here. Either profiles move to
the existing `KEY=value` grammar, or `check_profiles.py` must prove the two expanders agree
byte-for-byte on a shared fixture. §11 q5 leaves that open deliberately rather than pretending the
nesting depth decides it.

## 4. The oracle question: what was measured, and what rev 1 got wrong

Rev 1 measured the delta between `CONFIGS` and upstream's templates and then rejected the templates.
Both halves need correcting, so both are kept visible.

The measurement stands, reproduced independently by the falsification reviewer with a strict JSON
parse: across the five chain steps, upstream binds **69** keys, `CONFIGS` binds **64**, **63 shared**;
testkit-only is exactly `GenotypeBatch.training_vcf`; upstream-only is exactly `dragen_vcf`/`melt_vcf`
on steps 06/07/08 (the two callers `CALLERS` excludes for that cohort). So a transcribed map *is*
"upstream − excluded callers + branch-only extras": a rule, not data, with one key in 64 that was news.

**Overturned claim 1: "the templates are not parseable as JSON."** They are — one substitution and
all **28 parse cleanly** with `json.loads`:

```python
# PROPOSED-MECHANISM: ran here; 28/28 parse, 0 failures
NEUTRAL = re.compile(r"\{\{.*?\}\}", re.S)
obj = json.loads(NEUTRAL.sub("null", open(tmpl).read()))      # {{ x | tojson }} -> null
```

8 of the 28 templates contain a Jinja expression (in my 5-step chain: only
`GenerateBatchMetrics.json.tmpl`, whose `chr_x`/`chr_y` are `{{ reference_resources.chr_x | tojson }}`).
Rev 1 hit `JSONDecodeError: … line 11 column 34`, reproduced it, and generalized from one file to a
rejection of the source. The real reason not to *render* templates is different and stronger:
upstream's renderer is **bundle-dependent** — `gatk-sv/scripts/inputs/build_inputs.py` skips a whole
template when a referenced value is undefined and defaults `ref_panel → ref_panel_empty`,
`test_batch → test_batch_empty`. A faithful render answers "what did *this values profile* bind",
not "what does this ref bind" — and it needs `jinja2` plus gatk-sv's `inputs/values/` layout. So:
read the template structurally, never render it, never import their tool.

**Overturned claim 2: rev 1's own key-regex is the bug class this repo documents.** It counted
left-hand quoted keys, and `FilterGenotypes.json.tmpl` binds a nested object
(`"…runtime_override_plot_qc_per_family": {"mem_gb": 15, "disk_gb": 100}`) — **12 reported where the
template binds 10**. `batch_configs._declared_inputs()` refuses regex for exactly this reason ("a
first attempt at this check reported 13 unknown bindings against main because its regex missed whole
`input {}` blocks"). Of the 28 templates, 27 agree with a strict parse and 1 does not. The headline
63/64 is unaffected (no divergence in the chain); the *method* was not portable. Strict parse only.

**Decision.** miniwdl stays authoritative for existence/requiredness/type. The template is a second,
equally offline source — structural read at a ref, never rendered — for the binding map and for the
finding class rev 1 could not see: **an optional input production pins and the profile leaves to a
WDL default is invisible today.** `check_maps` derives `required` as "not optional and no default"
(`batch_configs.py:460`) and prints `MISSING` only from that set (`:504`, printed `:531-533`), and
`validate()` prints `missingInputs` without failing (`:630-633`) — so nothing in the current code
distinguishes "optional, unbound by design" from "optional, forgotten". For genotyping the choice was
deliberate and documented (`batch_configs.py:235-236`: `n_RD_genotype_bins`,
`fail_on_degenerate_sr_cutoffs` come from WDL defaults); for a new-module author it is silent. It is
also not academic: `TrainGCNV.wdl:89` comments `String? sv_pipeline_docker # required if using
n_samples_subsample` and `:103` gates on `select_first([n_samples_subsample])` inside that `if` —
optional by type, required by graph, and the cohort template binds both. Hence §7's grader prints a
new non-fatal line per step: *optional inputs upstream binds that this profile leaves to a default*.

## 5. Grading a profile: the lock sidecar, and arms rather than one ref

Rev 1 said "reuse `check_maps` + `wdl_gate`, `make profiles`" and called it the row that protects the
head-to-head. It cannot run where this repo enforces things: `check_maps` needs
`GSVTK_GATK_SV_CHECKOUT`, CI checks out **only this repo** (`.github/workflows/ci.yml` installs both
requirements files — so miniwdl *is* there — but never sets a checkout), and by this repo's own
convention a missing dependency is a SKIP, not a FAIL (`preflight` prints "pre-check SKIPPED" and
returns). A profile could go stale while `make test` prints PASS. Worse, `scripts/selftest.sh:204`
satisfies its pinned probe count with `run_n + skip_n`, so a probe that always skips keeps the tally
green (its own comment at `:186-188` claimed the opposite).

That second half is now **fixed**: the counter compares `run_n` alone against `want` and prints the
dependency to install, so a skipped probe fails the gate instead of certifying it. It removes one of the
two arguments for a lock file, not both — CI still has no gatk-sv checkout, so the WDL-facing half of a
profile cannot be graded there at all, and that is what the sidecar is for.

So grading splits in two, following `fetch_wdl.py`'s `.provenance` precedent (a grader-written
**sibling** file, read by consumers at `wdl_gate.sh:92`):

- `profiles/<module>.lock.json` — written by the grader when a checkout *is* available: per step,
  declared/required counts, the optional-bound-upstream-vs-profile-omitted set, the Dockstore
  publication check, plus `ref`/`sha`/`graded_at`. Repo content, reviewed and audited like the
  profile it describes.
- CI grades **profile ↔ lock** with stdlib `json` alone: no clone, no miniwdl, no network.
  `make profiles` FAILS, not SKIPs, when the live grader is unavailable unless an explicit opt-in
  variable is set, and prints that it took it.

`verified_against` must be keyed by **arm**, not a single ref: every loop here runs two refs
(`wdl_gate.sh:52-56` defaults to `origin/main` *and* `$GSVTK_BRANCH`; the head-to-head is
baseline-ref vs branch-ref by definition; the rerun path pins `GSV_WDL_VERSION` independently). One
graded ref either blocks the loop it serves or quietly relaxes to "one of the refs in play" — and
"one ref" is already a shipped defect here: `preflight()` graded `--against` while `body()` built the
config of `BRANCH`, which is why `_posted_ref()` exists and refuses. Port that refusal to the loader:
refuse unless the ref the command will actually run is a recorded arm. That is the existing guard
moved up one layer, not new machinery.

## 6. Experience: same answers, existing doors

Requirement: usable by a person and by an agent, without a second interface to keep honest. Rev 1
proposed three new commands (`gsvtk-config modules`, `modules --json`, `gsvtk module explain`).
Review killed them, correctly: `gsvtk tools` already prints which loops can run here and *where each
dependency resolved from*, `gsvtk-config show` already prints key + value + provenance, `doctor`
already prints which profiles were read and what is missing, and `stamp` already prints `path @ sha`
on stderr (`scripts/gsvtk:372`; `cmd_tools` at `:228`) — which is rev 1's "every verdict carries its
ref" rule, already implemented. Three commands
beside four existing doors is the "two places disagreeing about one value" tax, and each advertised
command inherits a real cost: wrapper `case`, its USAGE heredoc, SKILL.md prose, and the
`check_skill.py` flag/claim checks that already refuse to police every flag in prose
(`check_skill.py:55-58` records that attempt and why it was dropped).

`explain` was the sharpest case: a commentless JSON leaf plus a ban on per-module docs means the one
generated view available is structurally incapable of carrying the *why* — and the why is the thing
§1 says transcription costs. `_why_*` (§3.6) fixes that at the data layer instead, and `cat` becomes
the human view.

What survives, and it is the part that actually decides the experience:

- **Default the module.** `MODULE` (default `genotyping`) and `MODULE_DIR` (derived: `<repo>/profiles`)
  go into `kit/gsvtk-config`'s `DEFAULTS` in the same commit that reads them, with a
  [config.md](config.md) row — CONTRIBUTING's "one resolver, one precedence chain". Rev 1's
  `$GSVTK_MODULE_PROFILES`-then-`<repo>/profiles` search is a second resolver and is deleted. So is
  the word "profile" for this concept: it already means the env file *and* the upstream QC tool
  (`PROFILE_BIN`, `compare/profile_summarize.py`). Say `module`.
- **`show` answers "which module, and why that one"**, because it already prints provenance for every
  other value. No new subcommand, one registered key.
- **A missing module names the file to write and the questions it answers**, `exit 4`, per
  CONTRIBUTING's "name the missing thing" — and the list must be complete, which rev 1's was not (it
  named 5 of the 9 fields a working profile needs, so following the error text produced a profile that
  could not drive a chain).
- **Provenance on every verdict**: reuse `stamp` rather than inventing it.
- **Agent-facing output is one flag on an existing command** (`show --json`), not a parallel command.
- **A profile loaded from outside the tracked set must print that it was taken**, with its path and
  sha256, and say *not audited* — `make audit` scans `git ls-files`, so an out-of-repo profile is
  invisible to it. Refuse an out-of-repo module on any mutating path without an explicit flag.

Deliberately still absent: menus, wizards, per-module READMEs, per-module skill prose. Those cost
prose, and prose is what `check_docs.py`/`check_skill.py` already charge for.

## 7. Gates: two kept, four replaced, one deleted

A gate that can pass while the thing it guards is broken is worse than no gate, because `make test`
then prints PASS about it. Four of rev 1's seven were that.

| Rev 1's row | Verdict | Replaced with |
|---|---|---|
| no gatk-sv path literal outside `kit/` | **unsatisfiable** — 36 sites in 8 files match `wdl/`, `src/sv_shell` or `inputs/templates` across `terra checks compare replay scripts docker` (`svshell_contract_check.py:32-35`, `batch_check_inputs.py:80`, `fetch_wdl.py:47,80`, `docker/*`, `wdl_flat.py:62`), and the only fix (put `wdl`/`src/sv_shell`/fixture-glob layout in the profile) is banned by §3's forbidden list | one locator for the **checkout root** (`kit/config.py:105` `checkout()` already is it), and every gatk-sv-relative path built from a named constant in that module |
| `import profiles.genotyping` fails | **theatre** — the file is `.json`, so what it forbids already fails, and the real failure (`if name == "genotyping":` in code) passes | see the behavioural probe below |
| profiles are leaves; keys/lines capped | **kept**, capped from the *measured* genotyping profile, not "~20 lines"; "~20" was contradicted by the 100 bindings the file must carry | — |
| workflows exist + bindings fit | real but **unrunnable in CI** (§5), and blind to values | lock sidecar (§5) + `make profiles` fails-without-opt-in |
| unverifiable ref ⇒ refuse | **kept** — the wording already ships ("an unverified ref is not evidence") | arms instead of one ref (§5) |
| no module-named identifier in `terra/ checks/ compare/ replay/` | **greps literals, not facts**: `chain = json.load(open("profiles/genotyping.json"))["chain"]` in `batch_status.py` passes it while staying hard-wired at runtime; meanwhile it would fail 117 sites in 24 files, most of them the *why* comments CONTRIBUTING mandates (`gq_paired_compare.py:6-7`), and it cannot see `Makefile:242` or `kit/gsvtk-config:57` at all | behavioural probe: **for every tool in those dirs, changing `MODULE` must change what it prints** — this repo's positive-control idiom, which also kills "the checker that checks nothing" |
| probe count rises per module | **theatre** — `PROBES` pins *reproduced defects*, and `selftest.sh:204` counts SKIPs toward the pinned total, so a clone-dependent per-module probe raises the number without ever running | count only probes that *ran*; assert the module's grader ran or the profile is marked `ungraded` |

Plus three that rev 1 lacked and review forced:

- **Dockstore publication.** 32 of the 118 `wdl/*.wdl` are published (`.github/.dockstore.yml`).
  miniwdl reads all 118 offline, so a profile naming an unpublished workflow grades green and then
  dies with the `dockstore://… from method repo` 404 that `batch_configs.py:22-27` already documents.
  Every `steps[].wdl` must appear in that file at the graded ref — same `git archive` discipline.
  (`checks/wdl_gate.sh`'s own `--help` example, `--wf ResolveCpxSvGenotyping`, names a workflow absent
  at `main` — §12.)
- **`bind` values get the ceremony.** `check_maps` builds `bound` from *keys* (`:492-503`) and never
  examines a value, so §7's grader is structurally blind to the one profile field that can change which pipeline
  runs: rebinding `GenotypeBatch.vcf` from `workspace.merge_batch_sites_vcf{new}` to
  `this.outlier_filtered_pesr_vcf{new}` means "genotype this batch's own sites, not the cohort-merged
  sites". That is the same class as `--drop-branch-only-inputs`, which earned a flag, a stderr
  ceremony and three probes. When a checkout is readable, the loader diffs each binding against that
  ref's template value and prints `SEMANTIC divergence from upstream wiring: key, upstream=X,
  profile=Y` on stderr; `show` marks divergent bindings; a probe pins the print. CONTRIBUTING: a new
  mutator needs a guard **and a probe** — a profile field that redirects a run is one.
- **`--help` stays zero-config.** `CONFIGS` becoming a file read at import would make `--help`
  depend on repo data, which breaks the helpsweep contract and `probe_help_writes_nothing`
  (which runs `batch_configs.py` and `diff_rd_states.py` with a near-empty profile and requires exit
  0 *and* zero directories created — `diff_rd_states.py:26,31-32` computes its defaults at import for
  exactly this reason). So: profiles load lazily, in the command body, from a loader that never
  raises and never creates; helpsweep gains a fixture module and must pass with `MODULE_DIR` pointed
  at an empty directory.

## 8. Non-goals (unchanged, and the reason the doc is worth keeping)

- **`checks/svshell_contract_check.py` does not become generic.** Its parsing model *is* the
  `sv_shell` idiom — the driver's `jq -n` producer blocks, `jq -r ".K"` module reads, `$inputs[0].K`
  supply (`svshell_contract_check.py:32-58`). Parameterizing driver path and fixture glob is fine;
  a generic shell-JSON-contract engine would be a new interpreter with no second user.
- **No "supports all 118 WDLs".** Support is declared by a profile, so the ~115 workflows nobody has
  driven cost nothing. Name the unit, though: 118 `.wdl` files, 109 declaring a top-level `workflow`,
  32 Dockstore-published.
- **No generic WDL runner, and no per-workflow-closure profile field.** `wdl_flat.py`'s contract is
  *one workflow per closure* — it refuses any closure containing more than one (`GetShardInputs.wdl`
  is the named counterexample). That is the opposite of a chain fact, so §11 q4 of rev 1 is answered
  **no**: `wdl_flat` stays a separate mechanism with its own `bundle: true` flag in the profile.
- **No per-module docs, no vendored gatk-sv, no new required dependency, no per-module skill prose.**

## 9. Migration, resequenced

**Step 0 — done, landed without this design.** `batch_configs.py:492-495` read
`bound, nested = {}, []` then `(nested if len(parts) > 2 else bound).update({…})` — a list has no
`.update`. Verified live: injecting one 3-segment binding key and calling `check_maps` against a real
`git archive` of `main` raised `AttributeError: 'list' object has no attribute 'update'`. The
advertised "N nested-call binding(s) not checked" branch was therefore unreachable, and
`preflight()` crashed rather than reporting on any config binding a call input — which upstream
configs do: 5 in `single_sample/GATKSVPipelineSingleSample.json.tmpl`
(`GATKSVPipelineSingleSample.MakeCohortVcf.HERVK_reference`, `…RefineComplexVariants.n_per_split`,
`…AnnotateVcf.par_bed`) and 80 in `test/GATKSVPipelineBatch/GATKSVPipelineBatch.json.tmpl`; a
`RuntimeAttr?` struct input is dotted sub-fields too, and `TrainGCNV` declares 9. Leaf-keying also
collapsed two distinct call bindings sharing a leaf.

**Fixed:** `nested` is a dict keyed on the full key, each un-compared binding is printed by name with
the callee it binds on (a count is a footnote; a named key is findable), and it is reported without
failing the check, because a call binding is legitimate wiring. Pinned by `probe_fixes.py`'s
`nested_bindings` probe — which was falsified before it was trusted: reverting the two lines makes it
fail with the original `AttributeError`, and it asserts in both directions (no call binding → no
report; call binding present → `EXTRA`/`MISSING` still fire, so the fix cannot suppress findings).

Still open here, and only meaningful once profiles exist: *check* `Workflow.Call.input` against the
sub-workflow's declared inputs instead of waiving it — see §3 rule 1's `wdl`/`workflow` split.

1. **Golden first.** Capture `GSVTK_BRANCH=<ref> batch_configs.py show` and `body()` per config as a
   golden file *before* anything moves, then move, then require byte-for-byte equality, then retire
   the golden. Rev 1 claimed the existing probes already guard the map's contents; they do not — the
   fixture WDLs are **generated from the map under test** (`probe_fixes.py:503`, `:532` iterate
   `bc.CONFIGS` to synthesize the "declared" side), so a binding lost in the move disappears from both
   sides and the probe stays green. The probes validate the comparison machinery, which is worth a
   lot; they have never validated the table's contents.
2. **Move data, constraint-first.** `CONFIGS`/`BRANCH_ONLY_INPUTS` stay module-level dicts of
   identical shape, populated by a lazy loader that never raises or creates at import. Five probes
   reach into these as module attributes and must survive **unrewritten**: `probe_adapt_drops`,
   `probe_drop_flag_guard`, `probe_map_vs_wdl`, `probe_rerun_map_guard`, and `_fixture_tree` /
   `_fixture_wdl`. Rev 1's "change no behaviour" was honest in intent and wrong in cost: moving the
   dict out from under them forces rewrites, and `selftest.sh:278` pins the count at 12.
3. **Derive where it is safe, grade the gap.** `check` strict-parses each step's template at the ref
   (printing `DERIVED` / `JINJA-NEUTRALISED` / `NO TEMPLATE` per step, never rendering), and adds the
   two new findings: required-but-unbound (already exists) and **optional-bound-upstream-but-omitted**
   (new, non-fatal, exit 1 once profiles are the source). Expect exactly the `dragen`/`melt` and
   branch-only deltas of §4; anything else is a real finding.
4. **Collapse, don't duplicate.** Delete the **three** step→workflow copies
   (`batch_check_inputs.py:42`, `batch_save_metadata.py:36`, `batch_status.py:27`) into one reader fed
   by the module, and fold in `batch_fetch_compare.sh`'s export list (7 names),
   `batch_rerun_step.py`'s prefix/`ETYPE`/Dockstore path, `build_inputs.py`'s `SVShell.` prefix,
   `wdl_gate`'s default set and the jar-probe target+flags. Two guards on this step, both from review:
   the default `--wf` set stays the script's own list with profiles *added* (a profile naming only
   `GenotypeBatch` would silently drop `SVShell`, `GATKSVPipelineSingleSample` and `MakeCohortVcf` —
   three of four — in a tool whose comment says an unchecked workflow must fail because "the gate
   certified no hard errors having checked nothing"); and `batch_check_inputs.py:79`'s
   `next(c for c in tc.CONFIGS if c.startswith(a.step + "-"))` raises a bare `StopIteration` when no
   config matches a step, while `fetch_baseline.py:140`'s `startswith(prefix + "-")` can never match an
   un-numbered chain name — it prints `!! no config for step` and continues, writing a manifest with no
   entry for it (`:137-143`). Both become reachable the moment chains are data, and both are this
   repo's named failure class: an empty that reads like nothing was wrong.
5. **Ship a second module, honestly.** Rev 1's "zero new Python" is already false for both its own
   candidates: `TrainGCNV` needs member-entity freeze (§3.7) **and** a comparator nobody has (its
   comparable artifacts are tarballs and per-interval VCF sets — no existing tool reads those), and the
   CPX pair draws on ≥6 out-of-chain producers. So the acceptance test is: **a profile, plus at most
   two named code changes, each listed here before the work starts.** If it needs more, the schema is
   wrong; say which field.

## 10. What this makes newly possible to get wrong

- **A binding that silently changes which pipeline runs** — the single highest-value omission in
  rev 1, now §7's ceremony rule.
- **Two refs in one run, one graded ref** — §5's arms.
- **A profile that is valid JSON and still wrong** (freezes the wrong attribute). Grading catches the
  binding-shaped subset; "these are the inputs your arm should pin" is judgement. Mitigation ships
  today: `batch_freeze.py plan` prints per-attribute byte sizes before a copy.
- **`@`-over-`callers` shrinking a cohort.** Excluded callers must print as an explicit "not bound: …"
  line in `show`. Note `select_all` makes this substantive, not cosmetic: `GenerateBatchMetrics.wdl:66,69`
  feed `dragen_vcf`/`melt_vcf` into `select_all([...])`, so omitting them changes the merged VCF count.
- **Two expanders for `{frz}`/`{new}`.** Today three Python/bash consumers already share the grammar
  and `docs/config.md:94` already names the failure ("if they disagree you get an empty comparison,
  not an error"). Profiles add a data-level consumer in both languages; §3's format note is the
  mitigation and §11 q5 is the open decision.
- **Out-of-repo modules are unaudited** — §6's print-the-sha rule; `make audit` only sees `git ls-files`.
- **Parallel profile edits** are not a problem (a nested-JSON binding diff is as reviewable as a
  dict-literal diff); the real cost was the stranded rationale, which `_why_*` fixes.
- **Ownership when upstream renames a key**: there is no CODEOWNERS/MAINTAINERS here, so the lock's
  `graded_at` is the only drift signal, and `show` should print "graded 2026-09-24 / UNGRADED" the way
  `gsvtk tools` prints what cannot run.
- **Still no new dependency**, and none of this boots compute or reaches the network.

## 11. Open questions

1. Does `branch_only_inputs` generalize at all, or is it genotyping-shaped? It reads as a band-aid for
   "the map I copied is a snapshot of one ref". The honest model: the module is *written against* a
   ref, the lock records it, and ref mismatch is a refusal — which deletes both the field and the
   12-line comment.
2. One module per chain, or per chain *and* per arm? The two-arm head-to-head
   ([terra-head-to-head.md](terra-head-to-head.md) §5) wants the one differing variable to be module
   data, not env overrides.
3. Where does a *single-sample* module live? **Not compatible yet, and rev 1 understated it**:
   `stage_inputs.py:109` reads `manifest["entities"]["sample_set"]`; `fetch_baseline.py:129` fetches
   `sample_set`/`sample_set_set`/`sample` and never `participant`, so `resolve()` falls through to
   "bare attribute on the root entity" and records `null`; `batch_freeze.py:253-254` writes one
   `entity:sample_set_id` TSV; `batch_rerun_step.py:39` hardcodes `sample_set`. Upstream's
   single-sample deployment ships `participant.tsv.tmpl` (`entity:participant_id`). A module declaring
   `root_entity: participant` would be **read and ignored** by the freeze/submit loops — the worst
   kind of "supported". Either that loop is parameterized first, or it stays out of scope and says so.
4. *(answered **no** by review)* `wdl_flat.py` as a profile field — see §8.
5. **JSON with two expanders, or `KEY=value` with one?** §3's format concession. This is the decision
   most likely to change the shape of everything above, so it should be made before step 1, not during.

## 12. Defects this review confirmed in shipped code

Found while reviewing a proposal, real regardless of whether the proposal is built. Each has a
reproduction; 1 is fixed, 2 and 3 are not.

1. **`check_maps` crashed on any 3-segment binding key** — was `terra/batch_configs.py:492-495`.
   `bound, nested = {}, []` then `nested.update(…)`; verified live against a real `git archive` of
   `main` (`AttributeError: 'list' object has no attribute 'update'`). Dead only because no `CONFIGS`
   key had three segments; upstream's own single-sample config has 5 and its batch test fixture has
   80. Consequences were: the "nested-call bindings not checked" report unreachable, and
   `create`/`validate` refusing by crashing rather than reporting. **Fixed**, with a
   `nested_bindings` probe that was itself falsified (revert the fix → the probe fails with that
   `AttributeError`). See §9 step 0.
2. **A probe that always skips satisfies the pinned count** — **fixed**, published rather than deleted.
   The claim was true as written: `scripts/selftest.sh` compared `run_n + skip_n` against `want` while its
   own comment said a probe "quietly skipped for a missing dependency lowers it", and that "'8 ok' versus
   '3 ok, 5 skipped' is the difference between a gate and a rumour". The code now counts runs only, and the
   probe that exposed this in a fresh clone (`miniwdl_resolver` — miniwdl is a *dev* requirement, so
   `make setup` alone cannot have installed it) SKIPs naming `requirements-dev.txt` instead of failing over
   an absent tool. Consequence for this design: a per-module probe no longer needs a lock to be *counted*,
   but still needs one to be *graded*, because CI has no checkout to grade against.
3. **`checks/wdl_gate.sh`'s documented example names a workflow that does not exist at `main`** —
   `--wf ResolveCpxSvGenotyping` (`:18`); `git cat-file -e main:wdl/ResolveCpxSvGenotyping.wdl` fails,
   and this script is written to report `ABSENT` and exit 1 for exactly that. CONTRIBUTING's "never
   document a flag you did not run" applies to the example workflow name too.

## 13. Review round 1: what was falsified, and what replaced it

Three independent adversarial reviewers (generality / coupling-and-bloat / falsification), their
claims re-verified here rather than accepted; two numbers did not reproduce and are corrected below.

| Rev 1 claim | What was actually true | Now |
|---|---|---|
| "the templates are not parseable as JSON" → templates are for humans, miniwdl is the only oracle | 28/28 parse with one `re.sub` neutralizing `{{…}}`; 8 of 28 carry Jinja (1 of my 5). The real reason not to render is `build_inputs.py`'s bundle-dependence + a `jinja2` dep | §4; templates become the second offline source, read structurally, never rendered |
| the input→attribute map is a transcription burden the profile can drop | **196 of 473** bindings (41%) point at an attribute whose leaf name differs from the input name; the reviewer measured 212, the difference being how literal/`null` values are counted — either way, ~4 in 10 | §2's three-question table; profile carries the diff, not the map |
| a profile declares the bindings it makes (~20 lines) | a partial map is a *different pipeline that grades clean*: `TrainGCNV` binds 53, requires 12 | §3.2 complete map, §3.3 typed literals |
| `freeze_attrs` example, `export_attrs`, "no comments", "stdlib `json`", "stdlib-only `compare/`" | example expands to 9 not 17 (8 `_index` sidecars unfrozen); exports 6 not 7; `images.example.json` already carries `_about`/`_why_explicit`; `gq_scale_compare.py:24` imports `numpy` | §3.4/3.6, §3's format note (numpy claim retracted) |
| "no comments" + "no per-module docs" | strands the 12-line rationale that §1 calls the price of transcription | `_why_*` pairing, enforced |
| "`batch_status.py` … never mention genotyping; `kit/` does not either" | false: `batch_status.py:27-28` is the 5-step chain; `stage_inputs.py:346`; `kit/gsvtk-config:57`; step map in **3** files; `batch_fetch_compare.sh` owns the export list | §1 rewritten |
| "reuse `check_maps` + `wdl_gate`" as the load-bearing gate | needs a clone CI does not have; skips satisfy counts; blind to *values*, which is the field that changes runs | §5 lock sidecar; §7's `bind` ceremony |
| "the existing probes already pin the guards around these maps" | the fixture WDLs are generated **from** the map under test | §9 step 1 golden-first |
| "each step ships alone with `make test` green" | step 1 as written forces rewrites of 5 probes and breaks zero-config `--help` | §9 steps 0-2, constraint-first |
| three new discovery commands for good UX | four doors already answer it (`gsvtk tools`, `show`, `doctor`, `stamp`); each new command is a priced tax on `check_skill.py` | §6 rebuilt on existing surfaces (rev 1's own restraint, applied consistently) |
| `$GSVTK_MODULE_PROFILES` then `<repo>/profiles` | a second resolver; "profile" already means two other things | §6: `MODULE`/`MODULE_DIR` in `DEFAULTS`, one chain |
| gate rows 1/2/6/7 | two unsatisfiable, two theatre (they grep literals, not facts) | §7 table: behavioural probe "changing `MODULE` changes what each tool prints" |
| second module ships as "a profile only, zero new Python" | false for both named candidates | §9 step 5: profile + ≤2 named code changes |
| `verified_against`: one ref | every loop runs two arms; one ref is a defect already pinned by `_posted_ref()` | §5 arms |
| "26 module pages", "118 WDLs" | 25 pages + index; 118 files / 109 workflows / 32 published | §8 names the unit |

**Survived, and was checked to survive:** §2's dependency direction and read-at-a-ref rule (it is
`fetch_wdl.py`'s existing contract restated); §4's *measurement*, reproduced independently;
§8's non-goals, which all three reviewers called the strongest part — including the
`svshell_contract_check` refusal, which the code confirms is machinery not config; and §10's posture
of listing what gets newly breakable (rev 1 just omitted the biggest one).

### Round 2: what closed while the format decision waited

Two of the defects the review round confirmed are now closed, and neither changes what this design is
for. `check_maps` no longer crashes on a call-site binding (13th probe `nested_bindings`, positive
controls for both arms). The probe counter no longer lets a *skip* satisfy its pinned count, which leaves
§5's lock sidecar justified by **content** grading rather than by counting — the CI-has-no-checkout half
of that argument is untouched. Everything from §9 down is still unbuilt, and §11's q5 (profile format) is
still the decision that gates it.

## 14. Reviewing this doc

```bash
GSVTK_BRANCH=main python terra/batch_configs.py show        # the maps a module would replace
GSVTK_BRANCH=main python terra/batch_configs.py show | wc -l  # measured: 105 lines
checks/wdl_gate.sh --help                                   # the --wf list §3 moves (+ §12 defect 3)
make help                                                  # the phases §7 adds to
```

Interpreter: one with `firecloud` installed (`make setup`), and `GSVTK_BRANCH` set — `show` names the
branch in every Dockstore URI and refuses to guess it.

Related: [config.md](config.md) (one resolver, one precedence chain),
[static-checks.md](static-checks.md) (findings are a diff, not an oracle),
[terra-head-to-head.md](terra-head-to-head.md) (the loop two-thirds of this doc is about),
[methodology.md](methodology.md) (published corrections, and why),
[../CONTRIBUTING.md](../CONTRIBUTING.md) (the bar a tool clears, which a profile field inherits).
