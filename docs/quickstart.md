# Quickstart: what you can do in the next ten minutes

Every command below was run on a laptop: **no Terra call, no VM created, no bucket written, nothing on
disk**. The two build-preflight commands do make *read-only* gcloud and GitHub calls — that is what
makes them worth running before the expensive one. The outputs are real captures from this checkout,
shortened where they ran long, with the project / workspace / registry values replaced by `YOUR_*` —
those are yours to name and the tools refuse to guess them ([config.md](config.md)).

By the end you will have seen all four loops, and you will know which ones this machine can actually
run.

Nothing on this page spends money. Two of the four loops spend money **when you follow them up** — a
branch build is roughly 1-3 h of `e2-standard-8`, a head-to-head step is a fleet of VMs over tens of
minutes to hours — and each section says so where it stands.

```
make setup && cp testkit.env.example testkit.env      # a couple of minutes; pip needs the network
./kit/gsvtk-config doctor                             # tells you what is still missing
checks/wdl_gate.sh origin/main                        # seconds, no data: is my WDL launchable?
checks/svshell_contract_check.py                      # seconds: did a JSON rename break SVShell?
python terra/batch_configs.py check --against main    # seconds: do my keys exist in that ref?
python compare/compare_batch_tables.py ...            # seconds: the table diff, verdicts and all
docker/gatk-sv-build.sh --check my-branch             # free: would a 1-3 h build even start?
make test                                             # the offline gate over this repo itself
```

---

## 0. Install, then ask what is missing

```bash
git clone https://github.com/talkowski-lab/gatk-sv-testkit.git && cd gatk-sv-testkit
make setup                          # ./.venv (first run needs the network for pip; nothing else here does)
cp testkit.env.example testkit.env  # then edit: project + Terra workspace are yours to name
```

Don't guess whether your setup works. Ask, and read *where each value came from*:

```
$ ./kit/gsvtk-config show
  GSVTK_BASELINE_WORKSPACE   GATK-Structural-Variants-Joint-Calling   [default]
  GSVTK_BATCH                all_samples                              [profile:~/repo/testkit.local.env]
- GSVTK_BRANCH               (unset)                                  [unset]
  GSVTK_IMAGE_REPO           us.gcr.io/YOUR_PROJECT/YOUR_NS/gatk-sv   [derived from PROJECT]
```

Two keys have no default at all — the GCP project and the Terra workspace — because they decide whose
billing runs and whose workspace gets written. **Exit 4 naming the missing key is correct behaviour**,
not a crash:

```
$ python terra/batch_configs.py validate
gsvtk-config: GSVTK_TERRA_NAMESPACE is not set (Terra workspace namespace of your sandbox).
  set it in the environment, or put `GSVTK_TERRA_NAMESPACE=...` in one of:
    /dev/null
  see docs/config.md
```

`./kit/gsvtk-config doctor` lists required-vs-optional keys and what is missing. Nothing here reads
`os.environ` directly: one resolver, one precedence chain (`env > profile > derived > default`), one
place to answer "where did this come from".

## 1. What can this machine actually run?

```
$ .pi/skills/gatk-sv-testkit/scripts/gsvtk tools
checkout  ~/repo/gatk-sv-testkit
gatk-sv   ~/repos/gatk-sv @ e1909d2f
terra     ~/repo/gatk-sv-testkit/.venv/bin/python
wdl_gate  ok (~/repo/gatk-sv-testkit/.venv/bin/miniwdl)
jq scan   ok
build     gcloud ok
inputs    java ok, WOMTOOL_JAR unset/missing — export WOMTOOL_JAR=/path/womtool.jar
```

That is the whole tool inventory with its dependencies resolved — including *where* miniwdl was found,
which matters because `make setup` installs it into a venv your shell has no reason to have activated.
The last line is the toolkit telling you the local-replay loop is partly blocked here, rather than you
discovering it 40 minutes later.

## 2. Loop: "is my WDL actually launchable?" — seconds, no data

`miniwdl check` passes a workflow whose call sites never bind a required input, and one that passes an
input the callee dropped. Both are unlaunchable, neither is a `check` error, and gatk-sv's CI does not
look for either. This gate makes them numbers you can diff between two refs:

```
$ checks/wdl_gate.sh origin/main
WORKFLOW                       REF              EXIT   INCOMPLETECALL   STALE-BINDINGS
SVShell                        main@e1909d2f    0      1                0
GATKSVPipelineSingleSample     main@e1909d2f    0      2                0
GenotypeBatch                  main@e1909d2f    0      0                0
MakeCohortVcf                  main@e1909d2f    0      0                0
no hard errors. Re-run with --strict to treat IncompleteCall / stale bindings as failure
```

Each ref is materialized into **its own** directory with `git archive` (never a checkout mutation):
imports resolve by filename within a directory, so a mixed tree resolves against the wrong version and
reports a result that is neither the old bug nor the new one.

Two things worth internalising about every checker here:

* **Findings are a diff, not a verdict.** Today's gatk-sv carries a few `IncompleteCall` warnings on
  purpose, so `--strict` is a baseline-comparison decision, not a default. `--strict` on a clean
  workflow says so explicitly:

  ```
  $ checks/wdl_gate.sh --wf GenotypeBatch --strict origin/main
  GenotypeBatch                  main@e1909d2f    0      0                0
  no hard errors, and --strict is satisfied: no IncompleteCall, no stale binding, and
  every workflow named was actually present at the ref given.
  ```

* **A workflow you did not check is a failure, not a blank cell.** A typo'd name — or one that only
  exists on newer refs — used to contribute nothing to the exit code, so the gate certified "no hard
  errors" having checked nothing. Now it prints `ABSENT at <sha>` and exits 1.

## 3. Loop: "did that rename break `sv_shell`?" — seconds, and the reason this repo exists

`src/sv_shell` has no CI at all. Its driver chains 14 module scripts, feeding each an `inputs.json`
that a `jq -n` block assembles — and `jq -r ".missing_key"` returns the **string** `"null"` instead of
failing, so a renamed key becomes `--some-flag null` several stages later, inside a running VM.

```
$ python checks/svshell_contract_check.py
sv_shell contract check: src/sv_shell/single_sample_pipeline.sh + 14 stage calls (14 compared, 0 not comparable), 119 top-level keys read
  FAIL: gather_batch_evidence.sh: reads 2 key(s) absent from gather_batch_evidence_inputs_json_filename built at src/sv_shell/single_sample_pipeline.sh: rename_samples, subset_primary_contigs
  FAIL: cluster_batch.sh: reads 1 key(s) absent from cluster_batch_inputs_json_filename built at src/sv_shell/single_sample_pipeline.sh: retain_female_chr_y
  FAIL: make_cohort_vcf.sh: reads 2 key(s) absent from MakeCohortVcf_inputs_json_filename built at src/sv_shell/single_sample_pipeline.sh: bincov_matrix, cohort_id
=> 6 unsupplied-read problem(s)
```

(Abridged: the full run also lists ~40 gCNV hyperparameters that arrive from elsewhere. Those are the
*baseline* findings — which is exactly why the next command matters.)

```
$ python checks/svshell_jq_plumbing_scan.py
=> FAIL: 7 block(s) put null/empty into a module argument (gate a change with --compare-to <ref>)

$ python checks/svshell_jq_plumbing_scan.py --compare-to main
vs baseline main: NEW null/empty in 0 key(s) across 0 block(s)
=> OK: full coverage on both refs and no new nulls introduced
```

Same tree, two verdicts, and **both are right**: the first reports the state of the world, the second
reports *what your change did*. Read the second one.

## 4. Loop: "would my Terra head-to-head actually run?" — seconds, offline, before any money

The method-config input maps are a snapshot of one branch's WDL signature, while `GSVTK_BRANCH` only
picks the Dockstore URL. Point them at different refs and Terra rejects the whole config as an extra
input **at submission** — after the config sat in your workspace looking created. This is that check,
offline, against your own checkout:

```
$ python terra/batch_configs.py check --against main
WDL read from ~/repos/gatk-sv @ main
  ok  06-GenerateBatchMetrics  20 bound vs 38 declared
  ok  07-FilterBatchSites       8 bound vs 20 declared
  ok  08-FilterBatchSamples     9 bound vs 25 declared
  ok  09-MergeBatchSites       10 bound vs 15 declared
  BAD 10-GenotypeBatch         17 bound vs 21 declared
      EXTRA  GenotypeBatch.training_vcf
        a KNOWN branch-only input: declared on the branch under test, absent from the ref you checked.
        ...
        Rawls rejects the whole config as an extra input at SUBMISSION, so it would sit in
        the workspace looking created until someone submitted it.
check: 1 problem(s).
```

It read that ref with miniwdl — not a regex, because a first attempt at this check used one and
reported 13 unknown bindings against `main`, where `main` has exactly one.

**The mutating half of this loop refuses you first, on purpose.** `show` before `submit` is the whole
discipline, and the tooling enforces the order:

```
$ python terra/batch_configs.py create
create POSTs (and overwrites) method configs in your workspace: a config with a
  wrong binding is worse than no config, because the next submission will use it.
  review `show` first, then re-run with --confirm.

$ python terra/batch_freeze.py plan
no entity dump at $GSVTK_WORK/recon/sample_set_entities.json.
  produce it first:  python terra/recon.py            (read-only; dumps the baseline model)
  or point GSVTK_WORK at the checkout that already has it   (docs/config.md)
```

The second one is the house style for failure: name the missing file, name the command that makes it,
exit nonzero. An empty table that reads like "no differences" is the failure this repo fears most.

Recon → freeze → configs → gate → run → fetch → compare, with what each step costs and which are
write-once, is in [terra-head-to-head.md](terra-head-to-head.md).

## 5. Loop: "the numbers moved — do they match?"

Six comparators, one rule each, all of them written down so a number survives someone checking it.
They take explicit paths, touch no network, and write nothing except the one tool with `--out-prefix`.

| Tool | Question it answers | Needs |
|---|---|---|
| `profile_summarize.py` | one site-weighted headline from a `gatk-sv-profile` run | a profile output tree |
| `pair_level_concordance.py` | concordance with **no** profiler and **no** bucketing | two VCFs |
| `gq_scale_compare.py` | are these quality fields even on the same scale? | two VCFs |
| `gq_paired_compare.py` | on the *same* (site, sample) pairs: same number, or different scale? | two VCFs |
| `diff_rd_states.py` | where do RD copy-state calls disagree? | two depth VCFs |
| `compare_batch_tables.py` | the per-column table diff, strategy-aware | the two runs' table files |

Run the differ with nothing in it and it tells you it compared nothing, with exit 1, instead of
printing a tidy empty table:

```
$ mkdir -p empty/b empty/n
$ python compare/compare_batch_tables.py --baseline-dir empty/b --new-dir empty/n
== SUMMARY  MATCH=0  DELTA=0  STRATEGY=0  MISSING=6
  every column is MISSING: this diff compared nothing. Check the 'discovered' lines above
  and the NOTES (ambiguous roles are reported, not guessed).
```

With real inputs it reports `MATCH` / `DELTA` / `MISSING`, and `STRATEGY` for a row that changed *by
design* — where diffing the raw means would be a lie. A worked capture, including the four SR cutoffs
labelled `STRATEGY` and the `SR_sum_log_pval` → `SRQ` 10× scale trap, is in
[table_diff_example.md](../examples/table_diff_example.md). The three properties that have each burned
someone (site-weighted vs row-weighted, scale before magnitude, marginals vs pairs) are in
[comparators.md](comparators.md).

## 6. Loop: "build my branch's image" — and the free way to check first

An image built for `linux/amd64` cannot be built natively on Apple Silicon. This builds it on one
throwaway x86_64 GCE VM with no local Docker, streams the log, and deletes itself on success.

```
$ docker/gatk-sv-build.sh --check my-branch          # free: resolves the branch, no VM
error: branch 'my-branch' not found on https://github.com/broadinstitute/gatk-sv

$ docker/gatk-sv-build.sh --dry-run my-branch        # free: prints the plan, touches nothing
==> target
  project  YOUR_PROJECT   (us-central1-a, e2-standard-8, 150GB)
  push to  us.gcr.io/YOUR_PROJECT/YOUR_NS/gatk-sv
==> Preflight
OK   image family ubuntu-2404-lts-amd64 available
OK   zone us-central1-a visible (compute API ok)
INFO VM service account: <compute-sa>
INFO it must be able to push to us.gcr.io/YOUR_PROJECT/YOUR_NS/gatk-sv; for GCR that is (bucket-scoped): ...
+ gcloud compute instances create gsv-my-branch-00000000 --machine-type e2-standard-8 ...
```

`--check` and `--dry-run` are the two commands to run before the expensive one: the first proves the
branch resolves, the second prints the exact `gcloud` call, the machine type and the registry it will
push to. **Cost, stated plainly: `e2-standard-8`, roughly 1-3 hours, plus a 150 GB PD-SSD.** Nothing in
this repo quotes a price — your billing account, discounts and spot pricing are not ours to read; the
timeout ceiling is the only bound it can give you.

One trap that saves people time: a build VM acts as **its own** service account.
`--impersonate-service-account` changes the API caller, not the VM, so it cannot fix a denied push —
the fix is the bucket grant the `INFO` line prints.

## 7. Loop: "run the real trainer on the real inputs, locally"

The inputs of a captured successful run, staged; a locally built GATK jar, pointed at them. Costs
nothing but time and disk (the 1KG matrices are tens of GB). The worked drivers are in
`examples/` — deliberately recipes, not supported interfaces — and the one decision they each encode is
in the table beside them (`do not publish RD numbers from a chr20 slice`).

Start with [local-replay.md](local-replay.md). Note step 1's answer above: `WOMTOOL_JAR unset/missing`
means this loop is blocked on this machine until you point it at a jar.

## 8. Before you change anything here: the offline gate

`make test` must pass with **no credentials, no data, no network**, and its six parts each exist
because the previous one proved insufficient — the comparator that was dead on every real invocation
passed `py_compile` *and* the `--help` sweep, and shipped.

```
$ make test
  ok    compare_batch_tables runs, finds nothing to compare, exits 1
  ok    scripts/probe_fixes.py pins every confirmed defect with a control (13 ok, 0 skipped)
selftest: 32 ok, 0 skipped, 0 failed

make test: PASS (offline gate)
```

The `13` is a pinned count, not a coincidence: each of those probes reproduces a defect a review
confirmed, then asserts a **positive control** proving the guarded path was reachable — because "the
guard never fired" and "the guard could not have fired" print the same thing. That number going down is
a failed gate. `make audit` is the separate last line of defence: it fails if the publishable file set
holds a credential shape **or any value that is one of your own coordinates** — it derives those from
your configuration rather than shipping anyone's names, so CI grades the shapes and your machine grades
your names. Both run in CI.

Two things on a first run. Install the dev file alongside `make setup`
(`.venv/bin/python -m pip install -r requirements-dev.txt`) — miniwdl and flake8 are dev tools, not
imports, so `make setup` alone cannot install them. And read the `ok` count, not the pass/fail line: a
probe whose tool is missing prints `SKIP ... needs miniwdl (pip install -r requirements-dev.txt)` and the
pinned count then **fails** rather than passing on twelve. A skipped probe proves nothing. That is a
correction, not the original behaviour — the counter used to add skips to the tally, so "3 ok, 5 skipped"
passed as 8.

If a defect you just reproduced is fixed here, it belongs in `scripts/probe_fixes.py` — one function,
one `PROBES` entry, and the count raised in `scripts/selftest.sh`.

## 9. The agents' door

`.pi/skills/gatk-sv-testkit/` ships the skill that drives this repo, and its wrapper dispatches
**only** read-only modes — the refusals are executed by `make test`, so "submit is refused" stays a
checked claim rather than a sentence:

```
$ .pi/skills/gatk-sv-testkit/scripts/gsvtk terra create
gsvtk: refusing 'create'.
  This wrapper only dispatches read-only Terra modes:
    recon | show | check | plan | verify | status | cost | inputs | rerun-show
  The modes that POST or start compute (create, submit, copy, attrs --write) and
  `validate`, which is read-only in spirit but asks Terra to resolve a config, are yours to run
  in the repo, deliberately, after reading `show`/`plan` output.
```

When a human wants one of those modes, that is not an obstacle to route around: run it in the
checkout, `show` first, and say out loud where the write goes, what it costs, and how to undo it.

## Where to go next

| You want | Go |
|---|---|
| the head-to-head end to end (recon → freeze → configs → gate → run → compare) | [terra-head-to-head.md](terra-head-to-head.md) |
| why the static checks report what they report | [static-checks.md](static-checks.md) |
| a number you can publish | [comparators.md](comparators.md) |
| images, registries, the VM lifecycle, the gcr grant | [docker-builds.md](docker-builds.md) |
| the trainer, locally, on a real run's inputs | [local-replay.md](local-replay.md) |
| every config key and where it resolves from | [config.md](config.md), [setup.md](setup.md) |
| an error you are holding right now | [troubleshooting.md](troubleshooting.md) — keyed on the verbatim text |
| supporting a module other than genotyping | [module-profiles.md](module-profiles.md) (a proposal, not a feature) |
| the bar for adding a tool | [../CONTRIBUTING.md](../CONTRIBUTING.md) |

When something breaks, **grep `docs/troubleshooting.md` for the verbatim string** before searching the
web. It is keyed on exact error text, which is what you have.
