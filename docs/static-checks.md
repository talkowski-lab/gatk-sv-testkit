# Static checks: catching it before a VM boots

Every checker here is local, runs in seconds, and needs no data, no docker and no Terra account.
No count is stated for them, on purpose: this list has outgrown a fixed number twice, and a stale
tally in the first line is a reason not to trust the rest of the paragraph. They exist because
gatk-sv has classes of breakage that no existing CI sees:

| Breakage | Why normal CI misses it | Checker |
|---|---|---|
| A call site that never binds a required input | `miniwdl check` treats `IncompleteCall` as a warning and exits 0 | `wdl_gate.sh` |
| A call site passing an input the callee never declared | typechecking does not compare call sites across files | `wdl_gate.sh` |
| A required workflow input that the repo's own input JSON never binds | no WDL validator opens an input file, and `IncompleteCall` stays 0 because the call site *does* bind it — the caller who never will is Cromwell, at submission time. CI's `Test with WOMtool` step catches it; nothing here used to | `wdl_gate.sh` (`MISSING-INPUTS`, via `wdl_inputs_check.py`) |
| The mirror of that: an input JSON that binds a key the WDL does not declare — a stale or misspelled binding left behind by a rename | `miniwdl check` never opens an input file, and the required-half check only asks whether required keys are present, so a key that should not be there is invisible. gatk-sv CI fails on it twice (`womtool … validate`, and `terra_validation.py`'s own loop); nothing here used to | `wdl_gate.sh` (`EXTRA-KEYS`, same file, other direction) |
| A renamed `sv_shell` JSON key with a reader left behind | `jq -r '.missing'` yields the string `null`, forwarded as `--flag null`, failing stages later | `svshell_contract_check.py`, `svshell_jq_plumbing_scan.py` |
| A workflow-scope `write_*`, a `File` input only ever tested with `defined()`, a `pipefail` pipe whose reader exits early, or a command block that is not valid bash | to a WDL validator a command block is a string, and "this File is only tested for" is a localization fact, not a type error | `wdl_semantics.py` |
| A shipped image whose jar predates the flags its WDL passes | the image is built from a pinned commit, not your branch | `image-check/` |
| "what breaks if I change this file?" answered from memory | blast radius is a graph question, and neither CI nor `miniwdl check` answers reachability across imports and calls | `wdl_reach.py` |
| A Terra method config binding an input the WDL at that ref does not declare | `wdl_gate.sh` compares call sites **inside** a WDL tree; nothing there knows about method configs, and `validate` asks Terra, which needs the ref published on Dockstore | `terra/batch_configs.py check --against <ref>` (below, and it is the pre-check of `create`/`validate`) |
| A launch config whose entity type is a guess, or whose `${this.<attr>}` reads are not columns of any entity table the ref ships | a method config is JSON, not WDL: no validator opens it, and a wrong `entityType` is not an error — it resolves every `this.*` binding to nothing at run time, after the VMs booted | `terra_entity_check.py` |
| The config that names a workflow binds **nothing** for an input that workflow requires — not an entity read, not a workspace attribute, not a literal | nothing compares a method config to the required-input set of the workflow its own `methodRepoMethod` points at: `wdl_gate.sh`'s `MISSING-INPUTS` asks about gatk-sv's **test** input JSONs, and `batch_configs.py check` asks about keys the WDL does not *declare* (the mirror direction). Cromwell asks at input-resolution time, mid-submission | `terra_entity_check.py` (`UNRESOLVED-REQUIRED`, via `GSVTK-REQUIRED`) |
| A Dockstore version whose **name resolves** while the commit behind it sits behind the branch tip, so the launch runs a week-old WDL under a name that sounds current | nothing in CI compares a published Dockstore version against a git ref — and the only thing that keeps a gatk-sv version moving is that entry's `filters.branches` in `.github/.dockstore.yml`, a file upstream's `readonly_check.yaml` forbids any PR to touch, so once a rebase drops the commit that added the filter the sync stops while the name keeps resolving. Every name-level check says `RESOLVES` | `version_resolves.py` (depth 2, `--tip-from-file`; `STALE` names both shas) |

The last checker lives in `terra/` rather than `checks/` because it checks this repo's own config
table against a WDL ref, not gatk-sv's source. It is still offline: `git archive` of `wdl/` from
`GSVTK_GATK_SV_CHECKOUT`, parsed by miniwdl, no Terra call and no published ref. [comparing a
head-to-head's configs to their ref](terra-head-to-head.md) has the findings it produces and the one
branch-only key it currently reports against `main`.

## Contents

* [`wdl_gate.sh`: is it launchable, not just valid](#wdl_gatesh-is-it-launchable-not-just-valid)
* [`wdl_semantics.py`: does it RUN, or does it only typecheck?](#wdl_semanticspy-does-it-run-or-does-it-only-typecheck)
* [`wdl_reach.py`: the blast radius of a changed file](#wdl_reachpy-the-blast-radius-of-a-changed-file)
* [`svshell_contract_check.py`: the rename that becomes `null`](#svshell_contract_checkpy-the-rename-that-becomes-null)
* [`svshell_jq_plumbing_scan.py`: execute the plumbing instead of reading it](#svshell_jq_plumbing_scanpy-execute-the-plumbing-instead-of-reading-it)
* [`terra_entity_check.py`: which entity row a launch config runs against](#terra_entity_checkpy-which-entity-row-a-launch-config-runs-against)
* [`version_resolves.py`: does the version a launch names still point at the tip](#version_resolvespy-does-the-version-a-launch-names-still-point-at-the-tip)
* [`image-check/`: proof about shipped bytes, not checkout bytes](#image-check-proof-about-shipped-bytes-not-checkout-bytes)
* [`audit_history.py`: what the object store would publish](#audit_historypy-what-the-object-store-would-publish)
* [Using them as gates](#using-them-as-gates)

## `wdl_gate.sh`: is it launchable, not just valid

```bash
checks/wdl_gate.sh                       # origin/main vs $GSVTK_BRANCH, default workflows
checks/wdl_gate.sh v1.1.1 HEAD           # any two refs
checks/wdl_gate.sh --wf SVShell HEAD     # one workflow
checks/wdl_gate.sh --strict HEAD         # nonzero exit if anything is unlaunchable
```

Needs `miniwdl` (`python -m pip install -r requirements-dev.txt`, or into `./.venv`) and a local
gatk-sv clone (`GSVTK_GATK_SV_CHECKOUT`). Both are checked up front and the fix is printed rather
than a stack trace appearing.

`miniwdl` is resolved by `./kit/gsvtk-config miniwdl`: `MINIWDL`, then `PATH`, then the bin next
to the interpreter, then `./.venv/bin`, and `terra/wdl_flat.py --check` uses the same resolver.
That is not convenience: the console script `make setup` installs lives in a venv the invoking
shell has usually not activated, so `command -v miniwdl` said "not found" on the machine where
`miniwdl check` passes, and the WDL loop got written off as unavailable.

The suite's own interpreter is a third knob, `PYTHON=…` (`scripts/selftest.sh:29`, not
`$GSVTK_PYTHON`, which is what the *tools* use). It matters for reading the output: two checks
(`wdl_semantics --selftest`, `build_inputs --selftest`) are skipped with a named reason when that
interpreter cannot `import WDL`, so on a machine with miniwdl installed those SKIP lines never print and
the branch is never exercised. It was exercised by pointing `PYTHON=` at a bare venv: both skips printed
by name, and the count check then said "66 ran + 6 skipped = 72, but make help claims 83"; the twelve
failures in that run are the bare interpreter missing the kit's own packages, which are not optional.

Each ref is materialized to **its own directory** by `scripts/fetch_wdl.py` (git-archive of one
ref, with a `.provenance` file recording the exact SHA). That is not tidiness: WDL imports
resolve by filename within the directory, so a mixed tree resolves against the wrong version and
reports a result that is neither the old bug nor the new one.

The output is a count per ref, meant to be **diffed**:

```
WORKFLOW                       REF              EXIT   INCOMPLETECALL   STALE-BINDINGS   MISSING-INPUTS   EXTRA-KEYS
SVShell                        main@9a34dc12    0      2                0                NO-INPUT-JSON    NOT-CHECKED
SVShell                        main@77c1e0b2    0      3                1                NO-INPUT-JSON    NOT-CHECKED
IntegrateGDVcf                 7fbf1171         0      0                0                1              clean
IntegrateGDVcf                 01107996         0      0                0                0              clean
```

(Pick refs where **both** sides actually contain the workflow you named: `--strict` now fails with an
explanation when `--wf SVShell` names something that does not exist at that ref, instead of reporting
`no hard errors` about a workflow it never looked at. `SVShell` is one of the newer WDLs, so old refs
are a real risk in a long-running comparison table.)

A rise in `INCOMPLETECALL` means a call site stopped binding something; a rise in
`STALE-BINDINGS` means one is still passing an input the callee dropped. That delta is the whole
value: against gatk-sv as it stands there are a few `IncompleteCall` warnings that are deliberate,
so a raw nonzero is not a failure; *the change in the column* is. `--strict` makes nonzero the
exit status when you want a hard gate.

`MISSING-INPUTS` is not a delta column, and the two `IntegrateGDVcf` rows are why. At `7fbf1171` the
WDL declares `String sample_id` (required, `wdl/IntegrateGDVcf.wdl:31`) while
`inputs/templates/test/IntegrateGDVcf/IntegrateGDVcf.json.tmpl` contains **zero** occurrences of
`sample_id`; `01107996` made it `String?`. miniwdl reported `0 IncompleteCall` on both — correctly,
because the call site *does* bind `sample_id`, and the party that never will is the one supplying the
input file — and gatk-sv's CI rejected `7fbf1171` with `Required workflow input 'IntegrateGDVcf.sample_id'
not specified`. The gate ran half of that CI step and reported the result as a pass.

So the gate now renders the ref's own default input JSONs (`checks/wdl_inputs_check.py` git-archives
`inputs/` + `scripts/inputs/` into `$WORK` and runs gatk-sv's `build_default_inputs.sh`; the checkout is
never written to) and asks whether every input the workflow *requires* appears in the key set of the
JSONs gatk-sv's own `validate.sh` would pair with it — including the Terra configs CI validates with
`-t`, which `--no-terra` drops. **Key sets, never values**: womtool grades presence, so a bound value
is this check's blind spot on purpose (that question is `terra/batch_check_inputs.py`'s), and no
rendered path is ever opened. A nonzero `MISSING-INPUTS` is a hard finding with or without `--strict`,
because CI is red on it. `NO-INPUT-JSON` means CI itself has no input JSON for that workflow: printed,
counted in the closing note, and deliberately *not* a `--strict` failure, since parity with CI at a ref
is the promise and CI is green on such a ref. With `WOMTOOL_JAR` exported every pair also gets CI's
exact `java -jar $WOMTOOL_JAR validate` and the two answers are compared; without it the layer prints a
named, counted `SKIPPED` row that `--strict` fails on. See [troubleshooting](troubleshooting.md) for the
CI log line this column exists to predict. `EXTRA-KEYS` is its mirror, and the last paragraph of this
section is about it.

### `EXTRA-KEYS`: a key the input JSON names that the WDL does not declare

`MISSING-INPUTS` asks whether every required key is in the JSON. `EXTRA-KEYS` asks whether every key in
the JSON is something the WDL declares — the same seven JSONs read backwards. Its CI signal is womtool's
second failure mode, which the required-half check cannot see:

```
WARNING: Unexpected input provided: IntegrateGDVcf.this_key_does_not_exist_in_the_wdl
(expected inputs: [IntegrateGDVcf.ConcatVcfs.allow_overlaps, …])                    rc=1
```

and gatk-sv CI fails on it twice over: `validate.sh` womtool-validates the test JSONs, and
`terra_validation.py` (upstream's, not this repo's) has its own loop for the `-t` half —
`for inp in terra_inputs: if inp not in womtool_inputs: … "Unexpected input"; valid = False`. So a PR can
rename an input, leave the old binding in a template, get a red build, and this repo's `gsvtk check` says
clean. That is the same blind spot `MISSING-INPUTS` closed, one direction over.

**Why it is allowed to block a run.** The extras=0 measurement is the necessary half — every pair this
layer pairs, at three refs (`origin/main` c0314afa, `7fbf1171`, `01107996`), every one of them clean:

```
origin/main   workflows_with_pairs=38  pairs=77  extras=0     (and womtool's own `validate` on those 77
7fbf1171      workflows_with_pairs=38  pairs=77  extras=0      pairs: 0 answered "Unexpected input")
01107996      workflows_with_pairs=38  pairs=77  extras=0
```

but a zero on three trees does not license a hard gate on the fourth. The licence is the **direction of
the difference** between the two expected-key sets. For all 109 workflows with a primary callable, at all
three refs (327 workflow-ref comparisons), this layer's expected set is a **superset** of `womtool
inputs` — zero keys that womtool expects and this layer does not. So a key this layer cannot attribute to
a declaration is a key womtool cannot attribute either: the offline answer can be *quieter* than CI, never
louder, and only a check that cannot over-report is allowed to fail something. That is also why the
column is `clean`/`N extra` rather than a bare number — the column beside it answers a different question
with bare numbers, and a `1` in both would read as the same finding twice.

The expected set is not merely the workflow's declarations. `womtool inputs` also lists every input of
every call the call site does not bind — `Workflow.call_name.input`, nested workflow calls included —
and miniwdl's `Workflow.available_inputs` is that set. Real gatk-sv input JSONs use it: of the 77 pairs,
4 bind a call-qualified key and 8 bind a struct as an object, so a naive "is this a declared workflow
input?" test would cry wolf on 12 of 77 files at every clean ref there is.

**What it cannot see.** Four under-reports, each measured at womtool-84, each one blindness rather than
noise, and none of them a gate pass — where the offline layer is quiet and CI is not, the `WOMTOOL` row
still runs CI's own command and still fails the run:

| The shape | This layer | womtool-84, measured |
|---|---|---|
| A struct member path written flat as its own key (`ShapeProbe.struct_opt.label`) | not an extra: `required_inputs` already accepts that spelling as binding the member, and the tool would contradict itself otherwise | `WARNING: Unexpected input provided: ShapeProbe.struct_opt.label` — pinned as pair B of the cross-check (`fixtures/jar/womtool84-shapematrix-flatmember.*`), asserted in both directions |
| A key *below* a declared key (a member of a struct or map handed over as one object) | not an extra, and only the outermost unexplained key is ever reported | `Success!` on `{"struct_req": {"label": "x", "bogus_member": 3}}` — womtool does not police members of a value either |
| A namespace container (`{"ShapeProbe": {"plain_req": …}}`) | not an extra (and the required half reads it as bound) | never names the container; reports every required input `not specified` instead |
| A call path deeper than `womtool inputs` prints (`Wf.Call.SubCall.input`) | legal, because miniwdl's recursion goes deeper than the jar's printout | absent from `womtool inputs`, so womtool would refuse it — the superset relation is one-way, and this is the direction it leans |

The gate treats a skipped extras answer the way it treats a skipped womtool answer: `SKIPPED — N of M
workflow(s): no extra-key answer was produced`, printed as a per-ref row, counted into the prerequisite
totals, and fatal under `--strict` — never a `clean` cell in a column that never ran. `NOT-CHECKED` (a
workflow with no CI input JSON at all) is likewise not a pass; the reason is named one column left.

**Hard, with and without `--strict`** — like `MISSING-INPUTS`, because CI is red on it either way. That
was checked against the pre-change gate, not assumed: the same `--tree`/`--inputs-root` runs at all three
refs, before the column existed and after, give the same exit status every time (`origin/main` 0 → 0,
`7fbf1171` 1 → 1 on `IntegrateGDVcf.sample_id`, `01107996` 0 → 0), with every `EXTRA-KEYS` cell reading
`clean` on all of them. `scripts/selftest.d/womtool.sh` §10 asserts the fixture twin of that claim — a
tree whose required half is clean and whose only finding is an extra key must exit 1 — and §11 pins this
layer's answer to womtool's own words on the same input file. The cross-check is deliberately **two
pairs** (one where the two agree, one where they deliberately differ, plus a capture taken off a real
rendered gatk-sv JSON), because a pair is a JVM start and the 77-pair sweep above was already run once
out of band; what generalises the two pairs is the superset relation, which is what the paragraph above
measured.

## `wdl_semantics.py`: does it RUN, or does it only typecheck?

`wdl_gate.sh` asks about call bindings. Three gatk-sv branches each fixed a defect that it (and
`miniwdl check`, and `womtool validate`) reported clean, because the defect was one layer down: in
what the workflow *means*, and in what the rendered shell *is*. Measured on a real 118-file tree with
one `)` deleted from a `command <<<` block:

```
$ miniwdl check CollectCoverage.wdl        # the mutated tree
rc=0     # output byte-identical to the clean tree
```

Both validators treat a command block as a string. This checker reads the four things that are not
strings, as counts per ref so that a branch shows up as a delta:

```bash
checks/wdl_semantics.py --dir "$GSVTK_GATK_SV_CHECKOUT"     # counts + findings
checks/wdl_semantics.py --dir <fetched-ref-dir> --summary-only   # one line, for the gate
checks/wdl_semantics.py --dir "$GSVTK_GATK_SV_CHECKOUT" --block CondenseReadCounts
checks/wdl_semantics.py --selftest                         # the fixtures and their controls
```

The scan parses the whole tree in parallel (`--jobs`, default `min(8, cpus)` workers, each holding a
few hundred MB of AST). `GSVTK_SEMANTICS_JOBS=1` caps both this and the gate's real-tree row to one
worker: use it on a machine already deep in swap, which is the situation that got the knob written:
a full gate was started while a forgotten backgrounded one was still parsing, and 27 GB ended up in the
page compressor.

| Rule | The defect it names | Why no validator sees it |
|---|---|---|
| `WRITE-SCOPE` | a workflow-scope `write_lines`/`write_tsv`/`write_json`/`write_map` | it typechecks; Cromwell on PAPIv2 just cannot materialize a workflow-level File, so the submission dies in seconds |
| `DEFINED-ONLY` | a `File` input whose only appearance in the command is inside `defined(...)` | `defined()` is answered from the localization table, so the file is copied to the shard and never opened. Two whole-genome CRAMs measured $6.47 and two attempts |
| `PIPEFAIL` | a pipe whose reader stops early (`head`, `grep -m`, `sed q`, an awk `exit`) in a task that armed `pipefail` | the producer gets SIGPIPE, rc is 141, and it only happens on a large input. macOS ships bash 3.2, which has no `pipefail` at all, so the laptop cannot reproduce it and reading the tree is the cheap half |
| `SHELL-SYNTAX` | a command block `bash -n` will not parse | both WDL validators accept an unbalanced paren, because to them it is a string |

Measured at gatk-sv `main`, and these are **upstream counts to diff, not findings against you**:

```
tree: .../gatk-sv   files=118 tasks=315 workflows=109   scan=9.3s (8 workers)
  WRITE-SCOPE=0 DEFINED-ONLY=2 PIPEFAIL=3 SHELL-SYNTAX=2 LOAD-FAILURES=0

PIPEFAIL — 6
  ResolveCpxSv.wdl::GetSeCutoff:6  UNGUARDED (head)  $( awk -F '\t' ... $FILE | head -n1 )
  TrainRDGenotyping.wdl::UpdateCutoff:24  UNGUARDED (head)  | sort -nr | head -n 1)
  CollectCoverage.wdl::CondenseReadCounts:5  GUARDED (head)  counts_first_line=$(zcat X | head -n 1 || true)
```

That output cross-checks two independent reviews of this repo. `PIPEFAIL` names exactly the three
residual sites one of them found by hand after triaging its own ten rows, and it credits the
`|| true` ones as GUARDED instead of counting them. `DEFINED-ONLY` lands on the two `.idx` siblings,
including the one that review flagged as "the same trap in cheaper clothing". `WRITE-SCOPE` is 0,
which is the baseline the broken ref in the other review would have moved.

Three things to know before trusting a number from this scanner:

- **The renderer uses the AST's own literal/placeholder split.** `task.command.parts` is
  the command as ordered literals and placeholders, so rendering is "join the literals, stub the
  placeholders". A regex has to guess where `~{true='(' false=')'}` ends and invents a syntax error
  nobody shipped.
- **Stubbing with a word leaves 2 artifact rows at `main`** (both `TasksMakeCohortVcf`, where a
  placeholder expands to a leading pipe). They stay, deliberately: the obvious fix (check a second
  rendering with an *empty* stub and require both to fail) was measured and discards **60**
  findings, every one of them an empty stub deleting the operand of `done <`, `if` or `>`. It removes
  the two artifacts by removing the check. `SHELL-SYNTAX=2` versus a baseline of `2` is no change;
  `3` is yours.
- **`LOAD-FAILURES` is a rule.** A file miniwdl cannot parse contributes nothing to
  any count, which is how both prototype scans in those reviews would have reported a smaller number
  than the truth. Nonzero here means every count beside it is a partial answer, and the tool exits 2
  rather than 0.

Loading is 33 s of the 40 s scan (`bash -n` across all 315 blocks is 2.5 s), so the parse fans out
per file: 9.3 s on 8 workers, falling back to serial if a process pool cannot start. A scan that
refuses to run because it tried to be fast is not an improvement.

`wdl_gate.sh` runs it once per ref and prints one `SEMANTICS` row beside the binding rows. At two
refs it also prints what moved:

```
        SEMANTICS DELTA SHELL-SYNTAX   2 -> 3 (+1 vs baseline)
```

`--strict` fails on a **rise** and never on the baseline's own findings, because on gatk-sv today
three of these four counts are legitimately nonzero.

`--block TASK` prints one task's rendered command. It is what a branch's own regression test wants
(it grades the WDL rather than a copy of the block) and it is the entry point for `image-check/` below, which
is how you prove a real block survives in the image it will run in.

## `wdl_reach.py`: the blast radius of a changed file

Two reviews asked the same question from opposite ends and each hand-rolled the join: the manta memo
needed `mantatloccheck.sh` ← `TinyResolve` ← `GatherBatchEvidence` (which task invokes this script, and
what reaches that task), and the single-sample memo needed the reverse for `SVShell.wdl`. One tool
answers both directions.

```bash
./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --reverse --target Structs.wdl
./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT"          --target MakeCohortVcf.wdl
./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --reverse --target mantatloccheck.sh
```

Edges come from the AST plus the literal command text: file→file `import`, workflow→`call`, and a task
whose command block mentions a script name, which is the only way to connect a `.sh` edit to the WDLs
that would run it. On gatk-sv `main` that graph is 118 files, 657 nodes, 1554 edges
(`imports-outside=0 unresolved-calls=0` printed alongside, so a silently missing edge is visible):
`--reverse --target Structs.wdl` reaches 116 files at depths 1-3, `--target MakeCohortVcf.wdl` reaches
209 nodes including 39 scripts.

An unknown target exits nonzero and says what it looked for
(`no file, workflow, task or script of that name is in … (118 .wdl file(s) scanned …)`, plus the
closest names), and a target that nothing reaches prints `NOT REACHED` with the scan count instead of an
empty table.

`--target` is repeatable, and the tree loads **once** for all of them: N names are N answer blocks in the
order they were asked, not N ~34 s parses (measured on this tree: two names in one call, one `tree:` line,
38 s; the same two names as two invocations cost ~72 s). Each block answers for its own name, so an
unanswered one prints its own `NOT REACHED` and an unresolvable one its own closest-names list — and the
run exits 2 if *any* name was unknown even when the others answered, because a blast radius that quietly
covered only the names it recognised is the exact failure this tool exists to prevent. `gsvtk check
--reach --wf A --wf B` is built on that: one call, one load, one answer per name.

### `--images`: which dockers a change forces, and which workflows are wired to someone else's image

```bash
checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --reverse --target CountSamples --images
checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --reverse --target mantatloccheck.sh \
    --images --inputs-root "$GSVTK_GATK_SV_CHECKOUT"      # read already-rendered JSONs, render nothing
```

Reach tells you which workflows a change reaches; the review question is what has to be *built*, and the
scary half of it: a reaching workflow bound to an image your commit did not produce runs the pre-change
code and prints nothing suspicious. `--images` prints, per reaching workflow, the `*_docker` inputs it
binds, the value, and which of the four places that hold it answered — the workflow's own declaration
(a `WDL default`), a **rendered input JSON** (`inputs/build/**`), a **module profile**
(`profiles/<module>.json`, i.e. a Terra chain) — plus `inputs/values/dockers.json` as context on whether
the value is the one committed into the tree. Nothing is invented: a workflow that binds no container
input prints `binds NO docker input`, a binding whose value is a `${workspace.x}` placeholder or a
computed WDL default prints `UNRESOLVED` with the reason and is **counted**, and every count that could
hide an omission (`43 workflow(s) in this answer, 43 listed`, `from 45 answer node(s) …`, `131 binding(s)
whose value is not a literal image`) is printed next to the answer.

Then the distinction, in counted buckets, over the whole answer:

| bucket | what it means | how it is decided |
| --- | --- | --- |
| `[0] NOT A BUILD OF THE COMMIT UNDER REVIEW` | a run binding it tests a **pre-change** image | the tag is release-shaped (date-prefixed, or `vN`) — `docs/docker-builds.md` reserves those for production pushes |
| `[1] BUILT FROM A DIFFERENT COMMIT` | a stale branch build | the tag is `<branch>-<sha>` as `docker/gatk-sv-build.sh` mints it, and the sha it carries is not the commit under review |
| `[2] BUILT FROM THE COMMIT UNDER REVIEW` | this one does contain your change | same shape, sha matches (the checkout answers it, or `--head-sha` does) |
| `[3] CANNOT SAY` | untagged, or neither convention | printed, never folded into `[2]` |

Measured on the real tree (`Utils.wdl::CountSamples`, a shared-library task, one load):
`43 workflow(s) in this answer, 43 listed`, **15 distinct images across 147 bindings** — 10 published
(`[0]`), 3 branch-minted but from a different commit (`[1]`, including `gatk:mw-gatk-sv-53d5c2d`, which
11 workflows bind), 2 untagged (`[3]`), **0 built from the commit under review**, and 131 bindings that
are Terra workspace attributes rather than images. Wall clock 41 s against 38 s for the same command
without `--images` (both `load=33-34 s`; the rest is gatk-sv's own renderer, run once, ~1.6 s).

Two couplings worth naming. The JSON layer is `checks/wdl_inputs_check.py --render-only` invoked as a
subprocess — that file's flags, its `GSVTK-RENDER status=… jsons=… dest=…` line and the layout it leaves
behind are the contract, and if any of it changes the answer degrades to a named skip with `UNRESOLVED`
values, never to a fabricated image. And bucket `[1]` trusts the tag's sha-shaped suffix: for third-party
images (`genomes-in-the-cloud:2.3.2-1510681135`, `vapor:header-hash-2fc8f12`) that suffix is a build
timestamp rather than a gatk-sv commit, so the printed reason always quotes the tag and the sha it was
taken from — read it, don't trust the bucket label.

Part of that subprocess contract is the **render destination**, and the rule is that a render may only
delete a tree it created. `--render-only` clears its destination before it writes, and its default is
one directory per ref under the shared work dir, so what used to decide whether an `rmtree` happened was
`os.path.isdir(dest)` and nothing else — another tool's tree, a render an older release left behind, the
directory a typo'd `--dest` pointed at, or the tree a second caller was reading all went. Now a render
stamps the tree it is about to own: `.gsvtk-render-stamp` at the root of the destination, one JSON naming
`checks/wdl_inputs_check.py` and the ref it rendered, written *before* any content so that a render which
dies midway leaves a tree a later run can recognise rather than an un-stamped tree nobody may replace.
A destination is replaced when it is absent, when it is empty, or when the stamp names the ref being
rendered — that is what a re-render means — and refused otherwise:

```
GSVTK-RENDER status=REFUSED jsons=0 reason=un-stamped
  RENDER REFUSED (un-stamped): <dir> is not empty and carries no gsvtk render stamp (<dir>/.gsvtk-render-stamp): this tool deletes its destination before it renders, so it will not delete a directory it did not create. Render into a directory of its own (--dest), or remove it yourself if nothing is reading it.

GSVTK-RENDER status=REFUSED jsons=0 reason=other-ref
  RENDER REFUSED (other-ref): <dir> was rendered by this tool at ref v1.1.1, not ref HEAD: two refs cannot share one destination. Give one of them a directory of its own (--dest) to have both trees.
```

`<dir>` is the destination that was asked for, printed by the tool itself. Both refusals exit 1, so a
caller can assert them, and the gate turns one into its named `INPUTS-JSON SKIPPED` row instead of
deleting the tree it found; a destination that is not a directory at all is refused the same way
(`reason=not-a-directory`, which used to reach `os.makedirs` and raise). Both real callers pass their own
`--dest` — `checks/wdl_gate.sh` renders `wdl-gate/<ref>.inputs` beside the WDL tree it fetched, and
`checks/wdl_reach.py` renders under `reach-images/render-<ref>` — so neither eats the shared default, and
the default path is now resolved without creating it, so a run that renders nowhere leaves no directory
behind. `scripts/selftest.d/womtool.sh` §12 grades all of this against a fixture renderer (a stand-in
`build_default_inputs.sh`, because a selftest cannot require a gatk-sv clone), including the leg that
plants a non-empty un-stamped directory at the destination: a refusing branch nobody can reach is
decoration, so the reachability is asserted. What the stamp does NOT claim is safety for two concurrent
renders of the *same* ref into the *same* directory — still a replacement, because re-rendering a ref is
meant to replace that ref's tree. The refusal text is in [troubleshooting](troubleshooting.md) verbatim.

A run that asked for images and could not read any value source prints `IMAGE ANSWER INCOMPLETE` and
exits 2: "no images involved" and "I could not tell you" must not print the same way. A caller who did
not pass `--images` gets exactly the old bytes — the same artifact document apart from `argv` and
`load_seconds`, asserted by `scripts/selftest.d/reach.sh`.

**It is not in `make test`.** A full-tree parse costs ~34 s (miniwdl, whole tree), which is the right
price for a question you ask deliberately and the wrong price for a gate that must run on every
change. Its `--selftest` (4 tiny fixtures, chain resolved both directions, orphan named as one, several
targets answered by one load in the order asked) *is* in the gate, and `scripts/selftest.d/reach.sh` is
the 30-assertion phase for `--images` — seven synthetic WDLs, four synthetic input JSONs, a synthetic
`dockers.json` and a synthetic module profile, no checkout, no network, no jar; it runs standalone today
with `bash scripts/selftest.d/reach.sh .venv/bin/python`, and its one line in the `selftest:` recipe is
still pending.

## `svshell_contract_check.py`: the rename that becomes `null`

```bash
checks/svshell_contract_check.py                  # $GSVTK_GATK_SV_CHECKOUT, or --repo DIR
checks/svshell_contract_check.py --repo <clone> -v
```

`single_sample_pipeline.sh` chains ~14 module scripts, each fed an `inputs.json` assembled by a
`jq -n` block from the top-level inputs plus the previous module's `outputs.json`. Because `jq`
returns the string `null` for a missing key instead of failing, a renamed key turns into
`--some-flag null` several stages later, long after the cause, inside a VM, after the expensive
part. gatk-sv has no CI coverage of `src/sv_shell` at all.

The checker cross-references statically:

- driver `$inputs[0].KEY` reads **vs** what the top-level supply actually contains: the shipped
  `sample_inputs/single_sample_pipeline*.json` fixtures plus the `--arg`/`--argjson` names in
  `wdl/SVShell.wdl` (its jq is `$ARGS.named`, so argument names *are* the keys);
- module `outputs.json` writes **vs** later reads of those keys.

**Read the findings as a set.** Run against upstream it reports several unsupplied
reads, mostly gcnv hyperparameters that arrive from elsewhere in real use. Their value is the
diff: run before and after your change, and any *new* unsupplied read is a rename you missed.

**Coverage is printed, and partial coverage counts as a failure.** The header reports
`N of M stage calls compared`; if `N < M` the verdict is `NOT PROVEN` and the exit status is 1,
because "no findings" from a checker that did not look is the most expensive possible output. (This
is not hypothetical: the first version of this checker compared a handful of the ~14 stage calls; it
missed bare object keys, dashed keys, single-quoted module reads and `// default` reads, and still
reported a clean run on the subset it saw. It now compares 14 of 14 against real gatk-sv.) Key
recognition was also widened, so a key written *any* of `KEY:`, `"KEY":`, `'KEY':`, `"KEY":` with
dashes, or read with a `// default`, is no longer invisible.

`--strict` turns the pre-existing upstream findings into a nonzero exit as well; `--selftest` runs
the parser against hostile inline fixtures so a regression in *detection* fails the gate rather than
quietly shrinking coverage (`make selftest` runs it, and pins a minimum number of compared stage
calls).

## `svshell_jq_plumbing_scan.py`: execute the plumbing instead of reading it

```bash
checks/svshell_jq_plumbing_scan.py --repo <worktree>                  # absolute scan (red upstream by design)
checks/svshell_jq_plumbing_scan.py --repo <worktree> --compare-to main # the gate form: only NEW nulls fail
checks/svshell_jq_plumbing_scan.py --tree <extracted /opt/sv_shell>      # scan shipped bytes
checks/svshell_jq_plumbing_scan.py --list-keys                           # the key inventory
```

Reading the driver cannot prove the jq blocks are right; running them can. The scan extracts
every `jq -n \` … `> "${target}"` block, substitutes shell variables (top-level input file →
fixture, any `*outputs_json*` variable → a synthesized stub carrying every key the driver reads
from any slurped variable), and **executes** each block with real `jq`, then exits nonzero if a
block fails to execute or puts a null/empty into an argument. Note this means a plain scan is red
against unmodified gatk-sv, which has pre-existing nulls: use `--compare-to <ref>` to gate a
change, which fails only on nulls the branch *introduced*.

Needs `jq` on PATH. `--compare-to` exists so a PR gate reports only findings that are new to the
branch, which is the difference between a useful gate and one that gets ignored.

The scan reports its own **coverage** on one line: `blocks: P in file, E extracted, X executed,
Y errored, Z with null/empty`, and refuses to summarise a partial scan: if `X < P` you get a
nonzero exit and a message saying the verdict means nothing. Extraction used to skip blocks that were
written a little differently from the canonical `jq -n \` shape (quoting, line breaks, a redirect on
the same line) while the summary still said *every jq block executed*; blocks that cannot be resolved
are now counted and named instead of dropped. Each producer block gets a stub carrying the keys its
consumers read, so a stale reader against another block's output is detectable rather than silently
null, and `--selftest` pins both behaviours for `make selftest`.

## `terra_entity_check.py`: which entity row a launch config runs against

```bash
checks/terra_entity_check.py --repo "$GSVTK_GATK_SV_CHECKOUT" --ref main   # read a ref (git archive, read-only)
checks/terra_entity_check.py --tree scripts/selftest.d/fixtures/entity     # the offline fixture corpus
checks/terra_entity_check.py --tree T --corpus inputs/templates/terra_workspaces   # the corpus is a path, not a guess
```

Every launch config upstream ships binds its inputs with `${this.<attr>}`, and none of them says which
entity **type** the row is. A wrong type is not an error: as `terra/steps.py` puts it, it "resolves every
`this.*` binding to nothing at runtime, after the VMs booted". Two derivation rules were measured and one
is dead. The member rule (`this.<collection>.<attr>` names the type) derives `sample_sets` for
`MergeBatchSites.json.tmpl`, whose row is a `sample_set_set`. The name-key rule — the single-component
`${this.<etype>_id}` — answers once per config, and this tool derives it, cross-checks it, and refuses
rather than guessing. The WDL is not a source for this at all: `git grep -l 'this\.' -- 'wdl/*.wdl'` at
gatk-sv `e1909d2f` returns 0 files.

Measured by the first command at gatk-sv `e1909d2f`, and printed by the tool itself as `== census ==`:
**31 configs — 29 with exactly one name key, 2 with none, 0 with many** (the 2 are the
`output_configurations/*` write-back files, which bind no entity attribute at all). Derived types:
`sample_set_set` 16, `sample_set` 10, `sample` 3. Against the **shipped entity tables** — every TSV in the
corpus whose first header column is `entity:<etype>_id` or `membership:<etype>_id` — **3 pass**
(`GatherSampleEvidence`, `StripyWorkflow`, `GATKSVPipelineSingleSample`, all `sample`-rooted), **16 have no
shipped table of the derived type** (upstream ships no `sample_set_set` table), and **10 have a table that
lacks a column they read** (the only `sample_set` table is `sample_set_membership_1kgp.tsv.tmpl`, one
column: `sample`). That last 10 are a finding about upstream's corpus, not a limitation of the check: the
plan's Stage A expected the shipped tables to answer for all 29 name-keyed configs, and at this ref they
answer for 3. The same corpus carries one `${[this.a, this.b, …]}` binding holding **five** reads
(`PlotSVCountsPerSample.vcfs`), one key bound twice with different values
(`GATKSVPipelineSingleSample.mei_bed` — `json.loads` is last-wins), 30 `{{ … }}` expressions across 9
configs, and 0 reads inside nested objects; the tool counts each shape per config instead of assuming the
common one, and `--tree scripts/selftest.d/fixtures/entity` prints a synthetic corpus holding all of them.

Refusals are exit 1 and name what was looked for and where: `no-name-key` (with the reads it *did* find,
or the count of `${workspace.*}` bindings if it found none), `many-name-keys` (both types),
`no-shipped-table` (the type, the header pattern, and every table it did find), `columns-missing` (the
column, and what each candidate table carries). Exit 0 means every config answered — reachable, and graded:
the phase builds a one-config corpus that does answer cleanly. Exit 2 is a usage error, exit 3 a missing
prerequisite (no checkout, a ref that will not archive, no corpus at it, or no `terra/batch_configs.py` to
borrow the one `{{ … }}` expander from — this file refuses to define a second).

A corpus file that cannot be **decoded** is a refusal too, not a crash. Three of the readers opened a file in
text mode and caught `OSError`; `UnicodeDecodeError` is a `ValueError`, so one byte that is not UTF-8 escaped
as a traceback — exit 1, the code that means *findings*, with zero `GSVTK-*` lines, for a 42-config corpus
that otherwise answers every config. All three readers take the pair now, and the fact lands in a refusal the
file already had: `cannot-parse` (the config), `values-unreadable` (the same file on the value side, because
both layers open it) and `UNREADABLE table` (a workspace/entity TSV). The census still prints around them
(`8fbc516`; fixtures `alpha/workflow_configurations/Undecodable.json.tmpl` and `zeta/undecodable.tsv.tmpl`,
one raw `0x92` byte each, well-formed apart from the byte, so their only defect is undecodability).

A pass means the derived type is a shipped table's type and the read names are its columns, that every value
the config binds is classed by provenance, and (since the required-input layer) that every required input of
every workflow the config names is carried by one of those classes. It does not
mean the entity **row** exists (`terra.entity_sample()` before any submission), that a config's values are
the *right* ones, or that Terra's own `rootEntityType` agrees — that string exists only in a live workspace,
and is still an open decision (`docs/plan-launch-any-module.md`, "Risks not resolved"). What "are the values
right" has since become is named below: the value layer grades **where each bound value comes from**, never
whether the path is the correct path. Member attributes read through a collection
(`${this.sample_sets.ploidy_table}`) are listed and **not** checked: the member entity type is not derived,
and plural-to-singular is the guess that binds nothing when wrong. One machine line per config
(`GSVTK-ENTITY …`), one `GSVTK-ENTITY-SUMMARY` census line, and one `GSVTK-ENTITY-UNCHECKED` line that counts
what the run did not evaluate instead of leaving it to be inferred — because "no refusal printed" and
"nothing left unexamined" are different facts, and the first run of this tool was read as covering the
second.

    checks/terra_entity_check.py --repo "$GSVTK_GATK_SV_CHECKOUT" --ref e1909d2f | grep UNCHECKED

At `e1909d2f` that line is (re-printed at `e633128`, 2026-10-09; the two `required-` fields are the third pair this line has absorbed):

    GSVTK-ENTITY-UNCHECKED configs=31 member-attr-reads=64 configs-with-member-attrs=14 workspace-binding-reads=0 configs-with-workspace-reads=0 workspace-attribute-values-unread=174 configs-with-unclassified-values=0 required-configs-uncaptured=0 required-uncaptured-because=- root-entity-type=never-read

**Two of those fields replaced numbers this page carried.** It printed
`workspace-binding-reads=304 configs-with-workspace-reads=30`, with prose saying the 30-of-31 workspace
bindings were "counted here, deliberately not resolved, since resolving it from a test sample would report a
value for a production config that came from a test", and it labelled `${workspace.*}` resolution "Stage B's
job". That was written before Stage B shipped, and it is superseded: the value layer now classifies every
read of every document it can read, so the reads left unexamined are **0** — a measured 0, not a cleared
claim, because the 304 is still on the run, one field over in
`GSVTK-VALUE-SUMMARY … workspace-reads=304 workspace-resolved-reads=288 workspace-missing-reads=16`. 30 of
the 31 configs still carry at least one workspace read (31 `GSVTK-VALUE config=` lines, 1 of them
`workspace-reads=0`), so the scope of what got enforced is the same 30 the old line counted.

What did **not** close is printed beside it. `workspace-attribute-values-unread=174` is the second column of
the two flavors' `workspace.tsv.tmpl` tables (76 + 98 rows) — counted, never opened, because reading a value
answers "what did that values profile bind", not "what does this ref declare".
`configs-with-unclassified-values=0` is the same earned-zero for documents the layer could not read: a config
that will not parse has its unreadability reported, never its reads counted. The member-attribute half of the
gap is unchanged — `member-attr-reads=64 configs-with-member-attrs=14`, still the count the plan carries —
and `root-entity-type=never-read` is not a limitation to be fixed here: that string lives only in a live
workspace. This line moves no exit code and carries no verdict word (`scripts/selftest.d/entity.sh` asserts
that it holds none of `PASS`/`FAIL`/`ok=`/`UNRESOLVED`); the run above exits 1 because the *entity* deriver
has findings, the value layer refuses 15 configs and the required layer refuses 13 of them, not because of
anything on this line. `required-configs-uncaptured=0` is that line's own new zero, and it is the one a reader
most needs to stop reading as good news: it counts configs whose workflow was **never opened**, so the
required census covers none of them and their `GSVTK-REQUIRED` lines say `capture=<word>` instead of `OK`. On
the 42-config fixture corpus the same field reads `required-configs-uncaptured=39
required-uncaptured-because=mixed`, because that corpus ships one WDL behind 3 of its configs on purpose; a
run with `miniwdl` absent gets `=42`-ish and `because=NO-MINIWDL` (measured: `6` on the 6-config acceptance
corpus), which is the shape a CI box prints, and `Qblind` of the phase executes exactly that arm rather than
asserting it.

The same line on the fixture corpus — `checks/terra_entity_check.py --tree
scripts/selftest.d/fixtures/entity`, exit 1 — reads `configs=42 member-attr-reads=7
configs-with-member-attrs=3 workspace-binding-reads=0 configs-with-workspace-reads=0
workspace-attribute-values-unread=12 configs-with-unclassified-values=4 required-configs-uncaptured=39
required-uncaptured-because=mixed` (`workspace-attribute-values-unread=12` and the 4 unchanged; the two
`required-` fields arrived with the acceptance layer). This page had it as `configs=20 …
workspace-binding-reads=8 configs-with-workspace-reads=7`, and until the audit lane (`8fbc516`, `7e2f8b4`)
`configs=32 …
configs-with-unclassified-values=1`. The corpus grows one config per hole a fix closes: that lane added ten
(`Undecodable` + `zeta/undecodable.tsv.tmpl` for the undecodable byte, `UnterminatedBundle` and
`UnbracedBinding` for the unfinished template, `MultiLineBundle` for the legal multi-line `{{ … }}`,
`DroppedList` for the duplicate whose *dropped* value is a list carrying a bundle marker,
`KeyLeafDocker`/`KeyLeafMissing` for the `_docker` leaf exclusion that used to outrank the refusals, plus
`RootFlavor`, `TwoWorkflows` and `MembershipTable`), and `configs-with-unclassified-values` moved 1 → 4
because three of those ten are documents whose reads can be reported as unreadable and never counted
(`Undecodable`, `UnbracedBinding`, `UnterminatedBundle`) beside the `CannotParse` that was already there.
The value layer also reclassified the reads that used to be merely counted.
`scripts/selftest.d/entity.sh` asserts those fixture numbers **exactly**
— its `E1` leg pins this one line as three literal needles — so a fixture that changes shape has to move the
assertion with it, which is why the numbers here are the tool's and not the plan's.

Two more blocks ride on the same run, and they are the reason "Stage B's job" above is history rather than a
to-do — and a third block, the required-input layer, rides on top of both of them, which is where the sentence
those two set up actually gets graded (see below, "The third layer"). The **oracle** block prints one
`GSVTK-ORACLE flavor=…` line per flavor plus one summary, which at
`e1909d2f` is `GSVTK-ORACLE-SUMMARY flavors=2 ok=2 absent=0 malformed=0 dupes=0 names=174 values=not-read
enforced=yes`. The **value** block prints one `GSVTK-VALUE config=…` line per config plus one summary, which
classifies every scalar binding by the bundle it came from — `workspace-attr` (a name that flavor's own table
declares), `workspace-missing` (a name it does not), `entity`, `literal`, `needs-bundle`, `test-bundle`,
`docker`, `mixed`, `no-oracle`:

    GSVTK-VALUE-SUMMARY configs=31 bindings=622 workspace-attr=187 workspace-missing=16 entity=174 literal=113 needs-bundle=30 test-bundle=0 docker=101 mixed=1 no-oracle=0 nested-reads=20 workspace-reads=304 workspace-resolved-reads=288 workspace-missing-reads=16 no-oracle-reads=0 bundles=reference_resources dropped-values=1 dropped-workspace-reads=1 unresolved=16 configs-unresolved=15 configs-no-oracle=0 configs-needs-bundle=9 configs-no-values=0 configs-unclassified=0

and the `== census ==` block says the same in words: `622 value(s) classified, 16 refused by class, 30
disclosed as needing a bundle, 15 config(s) refusing, 0 with no value at all, 0 the layer could not read`.

Two refusal classes in that layer are new since the whole-repo audit, and both are a document read as
something it is not. An **unfinished template** — `{{ reference_resources.foo` with no closing `}}`, or a `${`
that never closes — used to classify as `literal`, which is a statement that a launch may carry raw template
state=OK; it is refused now, on both readers, by `unfinished_template`, while the *legal* multi-line
`{{ … }}` is accepted because the launcher's own pattern accepts it and refusing it would be a false refusal
in the expensive direction (`7e2f8b4`; fixtures `UnterminatedBundle`, `UnbracedBinding`, `MultiLineBundle`).
And a `_docker` key bound from a single `${workspace.*_docker}` read is a **docker-shaped value, not a stated
pin**: it prints `DOCKER-MUST-BE-STATED`, while `DOCKER-KEY-UNENFORCED` names the docker-shaped keys whose
*leaf* does not end in `_docker` and which `terra/batch_rerun_step.py::_stated()` therefore never asks a pin
for — so the advice cannot name a key the launcher ignores. Measured at `e1909d2f`: 0 instances of the
unenforced shape on the real tree (4 configs carry it in the fixture corpus), and none of this moved a
real-tree count — `configs-unresolved=15` and `configs-unclassified=0` above are the same numbers before and
after that commit, which is what makes them evidence rather than fallout.
`enforced=yes` is the load-bearing word on the oracle line: a `workspace-missing` name moves the exit code, which
is the difference between a disclosure and a guard. `needs-bundle` names a **bundle root, not a file** — this
tool renders nothing and never opens a values profile — and at this ref the root is `reference_resources`
exclusively (`git -C "$GSVTK_GATK_SV_CHECKOUT" grep -h -o '{{ *[a-z_]*\.' e1909d2f --
'inputs/templates/terra_workspaces/**/*.json.tmpl' | sort | uniq -c` prints `30 reference_resources`), while
`git -C "$GSVTK_GATK_SV_CHECKOUT" ls-tree -r --name-only e1909d2f -- inputs/values` ships six profiles
(`dockers.json`, `hgdp.json`, `ref_panel_1kg.json`, `resources_hg38.json` and the two
`test_single_sample_NA12878`/`NA19240` ones), none of them named `reference_resources.json`. So a
`NEEDS-BUNDLE` line is a disclosure that an operator must state the bundle for; it is not a claim that a
profile answering that root exists, and `ref_panel` is not a root any config binds at this ref.

A `workspace-missing` refusal is exit 1 and it names its own evidence **and its own cure**, verbatim (the
whole line as the real-ref run prints it at `e1909d2f`):

    UNRESOLVED-VALUE AnnotateVcf.ped_file [workspace-missing] AnnotateVcf.ped_file reads `cohort_ped_file` and `cohort_mode/workspace.tsv.tmpl` does not declare it: that file declares 76 attribute name(s), status=OK, and none of them is this one. Looked for that first field (an optional `workspace:` prefix stripped) in that file and no other; what it does declare: `cloud_sdk_docker`, `clustering_config_part1`, `clustering_config_part2`, `cnmops_docker`, `depth_training_bed`, `gatk_docker`, `gcnv_gatk_docker`, `genomes_in_the_cloud_docker` (+68 more, not listed). A name with no row cannot be launched — the alternative is a submission that discovers the missing input after the VMs booted. To clear it: add a row whose FIRST field is each name quoted above to `cohort_mode/workspace.tsv.tmpl` — that exact file, and no other TSV that flavor ships, because only this one is read — or stop binding `AnnotateVcf.ped_file`

That `To clear it:` tail is ruling R21 (`e27f90b`), and it is why this page used to say the opposite ("it does
**not** print the two ways to clear it"). What makes it safe to follow is that the cure is **conditioned on the
state the same sentence printed**: a row to ADD where `status=OK` says a table exists, rows to REPAIR under
`MALFORMED`, rows to COLLAPSE under `DUPE`, and under `ABSENT` a file to CREATE — named with its directory,
because there is nothing to edit there and "add a row to the workspace table" is advice that cannot be applied.
Each arm names the file the diagnosis already printed, never a path rebuilt from a pattern, and each ends with
the alternative that needs no file (`stop binding <key>`). The `ABSENT` arm is where the near-miss trap lives:
the TSVs that flavor ships are printed on that same line as **what EXISTS, not what to edit** — on the
committed fixture corpus `alpha`'s ABSENT refusal lists `alpha/workspace_other.tsv.tmpl` (a workspace table
under a near-miss name) and `alpha/workflow_configurations/workspace.tsv.tmpl` (the oracle's exact name one
directory down) beside four entity tables, and a row added to either changes nothing while reading like a cure.
`scripts/selftest.d/entity.sh` runs the arms instead of quoting them (legs `C0`-`C8`: needle the filename,
directory and attribute out of the printed sentence, apply the edit where it says, require the refusal to
clear, then apply the same row to each decoy and require the refusal to survive). The row to add, and the
binding to stop, were both run here, on one config and one table:

```bash
tool="$PWD/checks/terra_entity_check.py"        # run from the testkit checkout
d=$(mktemp -d)/tree; mkdir -p "$d/inputs/templates/terra_workspaces/enforce"
printf 'entity:cohort_id\tsites_vcf\n' > "$d/inputs/templates/terra_workspaces/enforce/cohort.tsv.tmpl"
printf '{\n  "EnforceJob.cohort": "${this.cohort_id}",\n  "EnforceJob.ref_fasta": "${workspace.reference_fasta}"\n}\n' \
    > "$d/inputs/templates/terra_workspaces/enforce/EnforceJob.json.tmpl"
: > "$d/inputs/templates/terra_workspaces/enforce/workspace.tsv.tmpl"
"$tool" --tree "$d" | tail -1; echo "rc=${PIPESTATUS[0]}"   # 1: workspace-missing=1 unresolved=1
printf 'workspace:reference_fasta\t{{ reference_resources.reference_fasta }}\n' \
    > "$d/inputs/templates/terra_workspaces/enforce/workspace.tsv.tmpl"
"$tool" --tree "$d" | tail -1; echo "rc=${PIPESTATUS[0]}"   # 0: workspace-attr=1 workspace-missing=0
```

The first run exits 1 with `GSVTK-VALUE-SUMMARY … workspace-attr=0 workspace-missing=1 … unresolved=1
configs-unresolved=1`, the second exits 0 with `workspace-attr=1 … workspace-missing=0 … unresolved=0`, and
emptying the oracle file again while deleting the `${workspace.*}` binding from the config also exits 0
(`bindings=1 workspace-reads=0`) — the refusal follows the value, not the corpus. `scripts/selftest.d/entity.sh`
grades that flip in both directions as legs `V1`/`V2`.

The third layer asks the question those two could not: **does the config that names a workflow bind what that
workflow requires?** That is Stage B's acceptance sentence, and until `8d5fbaa` nothing in this repo printed
it — 0 unbound and 0 `workspace-missing` was green *by default*, which is why the fixture corpus where a
required oracle row is absent, and the refusal it produces, is the deliverable rather than a footnote. The
required-input predicate and the WDL loader are **imported** from `checks/wdl_inputs_check.py`, not copied out
of it, for the reason row C8 of [gap-ledger.md](gap-ledger.md) records (a copied `"optional" in value` is how a
struct-typed required input silently vanishes from the required set), and a tree whose sibling checker carries
no such function is exit 3 naming the file — a half-layer is never silent. The required input's **value class**
is what gets graded, taken from the value layer's own verdict on the same bytes, plus the two shapes no value
class describes: `container` (the config hands the key a whole object — a presence test would call that
unbound, which is a false refusal) and `unbound` (nothing carries it, the one refusal this layer exists to
print). Its two lines:

    GSVTK-REQUIRED config=<relpath> flavor=<f> wf=<a,b> workflows=<n> capture=<word> required=<n> <class fields> container=<n> unbound=<n> files=<a.wdl|-> state=OK|UNRESOLVED|NOT-CAPTURED reason=<code|->
    GSVTK-REQUIRED-SUMMARY configs=31 required=427 workspace-attr=152 workspace-missing=11 entity=109 literal=35 needs-bundle=6 test-bundle=0 docker=91 mixed=1 no-oracle=0 container=0 unbound=22 workflows=29 workflows-captured=29 configs-ok=18 configs-unsatisfied=13 configs-not-captured=0 capture-no-miniwdl=0 capture-no-wdl-dir=0 capture-no-document=0 capture-load-failure=0 capture-no-workflow=0 capture-no-wdl=0 capture-ok=31

`required=` is the sum of the class fields and `configs=` is the sum of the three state counters AND of the
seven `capture-` counters — identities the phase reads off the printed line, with the field names taken from
the tool's own tuples, so a class or a capture word added without its counter skews a number instead of
quietly coexisting with it. `capture=` is the vocabulary for *the question could not be asked*, in precedence
order because the earlier word makes the later ones unaskable: `NO-MINIWDL` → `NO-WDL-DIR` → `NO-DOCUMENT` →
`LOAD-FAILURE` → `NO-WORKFLOW` → `NO-WDL` → `OK`. Two of those words are deliberate departures from the other
WDL checker: `wdl_inputs_check.py` folds "the file declares tasks only" into `NO-WDL` and its own pins keep it
that way, while this layer keeps `NO-WORKFLOW` apart because "no such file" and "the file you named binds
nothing" are two different operator actions (9 of gatk-sv's 118 WDLs at `e1909d2f` are task-only libraries).
The three corpus-wide causes (`NO-MINIWDL`, `NO-WDL-DIR`, `NO-WDL`) are stated once in a run-level banner and
never 31 times, and precedence between the two kinds of answer is fixed: `UNRESOLVED` beats `NOT-CAPTURED`, so
a finding is never hidden by a disclosure.

What that buys, at `e1909d2f`, re-printed at `e633128`: 427 required inputs over 29 workflows, **all 29
workflows captured**, 18 configs `OK` and 13 `UNRESOLVED`. Read the 13 rather than counting them: 11 are
required inputs whose carrying value the value layer had *already* refused
(`reason=required-workspace-missing`: 9 `ped_file`, plus `GenotypeBatch.vcf` and
`RegenotypeCNVs.merge_batch_sites_vcf` — every one a key bound to an attribute its flavor never declares), and
the 22 `unbound` all belong to the two `output_configurations/*` write-back files,
which already refuse at the entity layer as `no-name-key`. A leg asserts exactly that — **zero configs whose
required refusal is their only finding** — because a new layer that manufactures brokenness at a ref is worse
than a layer that missed it. The sentence the plan was written for is printed too:
`GATKSVPipelineSingleSample` asks 83 required inputs and returns `unbound=0 state=OK`, with 10 container images
and 2 bundle renders disclosed beside it, not resolved — `"required input(s) are container images: excluded by
rule"` and `"required input(s) carry a {{ … }} render: NAMED here and resolved by nobody"`, each on its own line
next to the pass.

Two costs this layer took on, recorded rather than glossed. It unpacks a **second** subtree per
`--repo`/`--ref` run — `wdl/`, 1.4 MB / 118 files at `e1909d2f`, one quiet `git archive` of one pathspec — and
it imports **miniwdl**, lazily, through the sibling checker: `--help`, the entity layer and the value layer
still need nothing but the standard library, so the import probe runs per run rather than at import time, and
an interpreter without `WDL` gets `capture=NO-MINIWDL` on every config with the exit code unmoved (the entity
and value answers do not depend on it, so a missing `wdl/` is `NO-WDL-DIR` and never exit 3). And
`--wdl-dir` was rejected on purpose, not forgotten: the WDL tree is `<tree>/wdl`, which is the pairing rule
upstream CI already applies by basename, and a flag would let one run point the required layer at a different
tree than the configs it grades. `Q0` of the phase proves the isolation by deleting `wdl/` from a copy of the
corpus and requiring the `GSVTK-ENTITY-SUMMARY` and `GSVTK-VALUE-SUMMARY` lines to come back byte-identical.

Graded offline by `scripts/selftest.d/entity.sh` against a committed synthetic corpus — 42 configs, one per
refusal shape, including the trap (a config whose member collection is a different type from its name key)
and the bait table that makes the dead member rule look right, plus a SECOND corpus, `fixtures/entity-required/`,
which is the acceptance corpus: one flavor, six configs, five shipped WDLs, exercising every capture word
(`OK`, `LOAD-FAILURE`, `NO-WORKFLOW`, `NO-WDL`, `NO-WDL-DIR`, and `NO-MINIWDL` via a stubbed interpreter), the
refusal, the `container` control and the precedence rule. Four numbers, because this phase has two independent
guards — a checkout, and an interpreter that can import `WDL` — and every combination is real here (printed at
`e633128`, 2026-10-09; re-run them before quoting, this census has been wrong twice):

| arm | command | tally |
|---|---|---|
| miniwdl + checkout | `bash scripts/selftest.d/entity.sh .venv/bin/python` | `279 passed, 0 failed, 0 skipped`, `declares 276 … reached 276 … nothing skipped` |
| miniwdl, no checkout | `env GSVTK_GATK_SV_CHECKOUT=/nonexistent bash scripts/selftest.d/entity.sh .venv/bin/python` | `250 passed, 0 failed, 1 skipped`, `reached 247, so 29 did not run behind 1 named skip`, extent `entity-no-checkout: 29 site(s) withheld, lines 1547-1679` |
| no miniwdl, checkout | `bash scripts/selftest.d/entity.sh /opt/homebrew/bin/python3` (any interpreter named by ABSOLUTE path that cannot import `WDL`; a bare `python3` will not do it, because the phase upgrades a bare name to `./.venv/bin/python`) | `237 passed, 0 failed, 2 skipped`, `reached 237, so 39 did not run behind 2 named skip(s)`, extents `entity-no-miniwdl: 29 … 1419-1526` and `entity-real-miniwdl: 10 … 1637-1673` |
| no miniwdl, no checkout | `env GSVTK_GATK_SV_CHECKOUT=/nonexistent bash scripts/selftest.d/entity.sh /opt/homebrew/bin/python3` | `218 passed, 0 failed, 2 skipped`, `reached 218, so 58 did not run behind 2 named skip(s)` (29 + 29) |

The fourth column is why two regions exist: the two guards are independent, and `DECLARED - reached` would
otherwise attribute both withheld totals to whichever SKIP line printed last. Note which interpreter takes which
arm — `make test` defaults `PYTHON` to the bare NAME `python3` (`Makefile:13`) and this phase upgrades a bare
name to `./.venv/bin/python` when one exists, so on a box whose venv has miniwdl **`make test` runs the
miniwdl arm** and prints no skip; driving the skipped arm deliberately takes an interpreter named by absolute
path. The arm nobody can guarantee their box will reach is still executed on every run: `Qblind` re-runs the
acceptance corpus under an interpreter whose `WDL` is a stub that raises, which is how `NO-MINIWDL` is observed
where miniwdl is installed. `bash scripts/selftest.sh` also runs every phase, so `make test` prints whichever
of those four lines this machine's interpreter earns.

This block has been wrong twice for exactly that reason: it said 144 declared / 125 reached / extent 864-947
until `e27f90b`'s 27 cure legs, and 171 / 152 / 1010-1093 until the audit lane (`8fbc516`, `7e2f8b4`) added 43
more legs outside the guard — 214 / 195 / 1267-1350 was that lane's shape, and the required-input layer took
the declared count to 276 while moving the region's extent again — and `make test` stayed green across both
gaps (see [methodology.md](methodology.md), "A doc's present tense is the part that rots"). The extents above are
the phase's own report, not a line number someone grepped for.

Two things that page used to say are wrong and are named here because they are easy to re-trip: it said
**"54 assertions with a gatk-sv checkout holding `e1909d2f`, 44 passed + 1 named counted skip without
one"**, which was the pre-Stage-B shape and is now 276 declared / 276 reached with a checkout, 247 reached with
29 withheld without one; and the no-checkout command it implied was `GSVTK_GATK_SV_CHECKOUT=` **empty**, which is not a
switch. `kit/config.py:45-47` is `return _core.get(key) or fallback`, so an empty value is falsy and falls
through the profile chain to the default — which on a configured box is that same real checkout. Measured on
this machine at `e633128`: `env GSVTK_GATK_SV_CHECKOUT= bash scripts/selftest.d/entity.sh .venv/bin/python` →
`279 passed, 0 failed, 0 skipped … nothing skipped`, while `env GSVTK_GATK_SV_CHECKOUT=/nonexistent
bash scripts/selftest.d/entity.sh .venv/bin/python` → the 250-passed / 29-withheld pair above. Reproducing the CI skip takes a
path that is not there, never `=` empty; CI gets there because the variable is *unset* **and** nothing
resolves a checkout. The mechanism is written out at `scripts/selftest.d/entity.sh:183-193`. No checkout, no
network, no credentials, no jar — and no `miniwdl` for the legs outside the two `*miniwdl*` regions, which is
what the `entity-no-miniwdl` skip is for: the phase that owes a required-input answer says it withheld it, and
`Qblind` runs the missing-miniwdl arm anyway.

## `version_resolves.py`: does the version a launch names still point at the tip

```bash
checks/version_resolves.py --step 01 --from-file /tmp/trs-single-sample.json   # depth 1: does the NAME resolve
checks/version_resolves.py --from-file capture.json --all-steps \
    --tip-from-file /tmp/ls-remote.txt          # depth 2: is the commit behind it the TIP
gsvtk publish --from-file capture.json --all-steps --tip-from-file /tmp/ls-remote.txt
```

One tool at two depths, because both questions read the same capture document and two tools answering one question
with two vocabularies is a reader reconciling verdicts. **Depth 1** answers whether the `(entry, version)` the launcher
will put in `methodRepoMethod` exists at all: `RESOLVES`, `NO SUCH ENTRY`, `NO SUCH VERSION`, or a refusal. It grades
the pair that `batch_rerun_step.dstore()` itself builds, so a body builder that disagrees with the profile cannot pass.
**Depth 2** adds a tip source — `--tip`, or `--tip-from-file` holding `git ls-remote origin refs/heads/<branch>` output
— and answers one word: `CURRENT`, `STALE` (naming **both** shas), `ABSENT`, or `REFUSED`. `--all-steps` answers for
every entry the launcher can name rather than for one step's, and prints a counting line. `gsvtk publish` dispatches
the same file; it is read-only, takes no `--confirm`, and runs under `GSVTK_READ_ONLY=1`. Exit codes are the CLI's:
0 `CURRENT`, 1 `STALE`/`ABSENT`, 2 usage, 4 `REFUSED`.

It never fetches, and that is the contract rather than a limitation: the operator captures with the `curl` in
`--help`, and a check that silently reaches the network is a check that fails differently on someone else's machine.
`--tip-from-file` exists so the comparison itself is gradeable offline — it is what the gate drives. `gsvtk publish
--ls-remote` is the one arm allowed to reach out (one read-only `git ls-remote`, refs only, no Terra or gcloud
credential), it says which remote it is asking, and no leg runs it.

**Why a name is not a commit — the structural cause, not a one-off.** Dockstore publishes a gatk-sv branch only while
that branch is listed in **that entry's** `filters.branches` in `.github/.dockstore.yml`, and a PR branch cannot carry
that edit: upstream's `.github/workflows/readonly_check.yaml` fails any PR that touches the file. Both read at
`origin/main` on 2026-10-14 — the `GenotypeBatch` entry's filter is `branches: [main]`, and the check's list is
`readonly_files=(".github/.dockstore.yml" "inputs/values/dockers.json")`. So when a rebase drops the commit that added
the filter, Dockstore stops syncing the branch, while the version name it already published keeps resolving. Depth 1
says `RESOLVES`, Terra's `validate` passes, and the launch runs the descriptor Dockstore last saw. There is no CI that
can see this coming, because the fix cannot be committed on the branch that needs it. The remedy the tool prints is
therefore a sidecar: branch `<branch>_terra` at the tip, one commit on it adding `<branch>_terra` to that entry's
`filters.branches`, wait for the sync (under a minute in practice), re-check naming `<branch>_terra`, and move
`GSV_WDL_VERSION` to it. The tool prints those commands and runs none of them.

**Measured, 2026-10-14 (dated capture note; one read-only capture, no credentials, no POST).** For the field case
`GenotypeBatch @ mw_genotype_scale`: the unauthenticated TRS listing still **lists the version** — 3 entries, 341
version rows — while `git ls-remote origin refs/heads/mw_genotype_scale` answers
`af68ae488725dddf7d43b66547c9bfa0324db1e1`. The commit Dockstore was serving, `62d7f80d0b32eca55ca5eaeae770cf56eccbfeca`,
is 12 commits back, and its committer timestamp equals that version row's `meta_version` (`2026-10-01 16:07:38`) to the
second, which is what a per-version last-sync stamp means. The same listing also answers a design question, and answers
it no: **341 version rows, 0 carrying a commit field of any spelling.** Dockstore's own `openapi.json` puts `commitID`
on `WorkflowVersion`/`Tag`/`Version` and not on `ToolVersion`. An earlier revision of this page concluded from two
closed routes that a `commitID` capture therefore required a token; re-measured 2026-10-15 against
`.../path/workflow/github.com%2Fbroadinstitute%2Fgatk-sv%2FGenotypeBatch`, and both old numbers were right while the
conclusion was not: `?include=versions` alone → **401**, `/versions` → **404**, and — the one never tried —
`/published?include=versions` → **200 unauthenticated, 28 version rows, 28 carrying `commitID`**. So depth 2 needs no
token, `gsvtk publish --fetch` runs that GET, and a capture that omits the entry is still a **`REFUSED`** for that
entry and never a `CURRENT` — the leg that grades the real committed capture exists to keep that true.

What it does **not** prove, stated rather than implied:

* the `commitID` fixture under `scripts/selftest.d/fixtures/trs/` is a **shape-holder**, labelled as one — its field
  names are Dockstore's, its two shas are real commits measured that day, and no live endpoint that would answer with
  exactly that envelope was reachable anonymously (see that directory's `README.md`);
* the committed real capture is an **excerpt**: 1 entry and 2 version rows out of the 3 entries and 341 rows measured.
  The rest stays out of the repo, because a full listing is a list of other people's branch names;
* `--ls-remote`, the sync delay of "under a minute", and the remedy's *effectiveness* are operations reports, not
  measurements any leg makes. Nothing here launches, POSTs, pushes or spends: every leg grades a file.

## `image-check/`: proof about shipped bytes, not checkout bytes

A checkout can be checked statically; an image has to be asked. Both scripts boot one throwaway
GCE VM (needs `GSVTK_PROJECT`), pull the image with the instance's own credentials, and stream
results to the serial console:

```bash
# name the image whose bytes you want proven (--image is required: an unnamed image cannot be evidence)
checks/image-check/svshell_image_check.sh --image "$(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6>"
checks/image-check/jar_flag_probe.sh --image <ref>     # which GenotypeSVs flags the shipped jar accepts
```

All three of this directory's launchers refuse an `--image` they cannot screen, from **one** file:
`checks/image-check/image_ref.sh`, sourced by each of them, and each refuses to run at all when that
file is missing. Two of them screened nothing but emptiness before it was shared, and both write the
reference into a startup script that runs as **root** on the VM — so a `"` in the argument escaped
`svshell_image_check.sh`'s generated `IMAGE="…"` line, and a newline put a bare command on its own line
inside `jar_flag_probe.sh`'s guest, right after `docker pull`. Both were read back out of the bytes
those launchers actually handed to `--metadata-from-file`, not inferred. The refusal happens before an
instance name is derived from the reference, so "Nothing was created" is literally true on every path;
the shapes it names are the table in the run_in_image section below, and the per-launcher refusals are
graded in `scripts/selftest.sh` (section C5).

`--timeout MIN` shortens the driver's poll ceiling — `run_in_image.sh`, default 1500 s = 25 minutes. The
three image launchers do not share that number, and the sentence used to name none of them: `svshell_image_check.sh`
defaults to 2400 s = 40 minutes and takes the same flag, `jar_flag_probe.sh` implements no such flag (its
ceiling is fixed), and a whole-branch review read this line as documenting svshell and filed it as a
contradiction. It exists because that ceiling
was once hardcoded at 2400: a scan that never prints a marker then costs the caller the whole default,
and anything driving that path inherits it -- measured, the offline gate ran 30 minutes without reaching
the next phase. Ask for `--timeout 1` when you want a short answer; a shorter ceiling changes the
verdict you are willing to wait for, not the work the VM does.

`--expect-driver-md5` / `--expect-fixture-md5` are what make it a proof rather than a demo, and the
verdict says which of the two you got: **`PROVEN`** only when both expected md5s were supplied and
matched the shipped bytes, **`SCAN_CLEAN`** when the scan ran clean but no expected md5 was given for
something, per file, so a clean scan of a file nobody pinned can never be quoted as byte identity.
The earlier wording printed one blanket "byte-identity check passed" for the whole run, which people
(naturally) quoted as proof about files it had never compared to anything.

`svshell_image_check.sh` extracts `/opt/sv_shell` with `docker create` + `docker cp` (no bind
mounts, no daemon config) and md5s the driver and shipped fixture. **The `--expect-*` values are
the proof**: compute them from the checkout you actually tested and pass them in.

```bash
git -C <gatk-sv> show <ref>:src/sv_shell/single_sample_pipeline.sh | md5sum
```

Otherwise the byte comparison prints `byte-identity NOT PROVEN` and is skipped: the
image then only proves it contains *a* driver, not the one you audited. It then runs the plumbing
scan `--tree` on the extracted tree, on the VM host.

`jar_flag_probe.sh` answers "does the jar inside the image accept the flags my branch passes".
Only answerable by *executing* the jar: the image's `/opt/gatk.jar` is built from a pinned GATK
commit, which can predate your argument split, in which case the image cannot run your driver
however correct the driver is.

`JAR_FLAG_PROBE=DONE` means **there is help text to grade**, not "this line was reached". A failed
`docker pull` used to print `### PULL_FAILED` and carry on: `docker run`'s status was ignored, the empty
help file was grepped, and `DONE` printed anyway — so a registry that was never reachable answered "the
shipped jar does not accept this flag". The two no-verdict markers are `JAR_FLAG_PROBE=IMAGE_ABSENT <ref>`
(image would not pull) and `JAR_FLAG_PROBE=NO_HELP` (it pulled but produced nothing gradable: entrypoint
moved, `/opt/gatk.jar` gone, daemon unreachable, missing shared library). The driver stops on the **first
sight** of either and exits 5, naming them as "NOT a finding about which flags the shipped jar accepts",
instead of waiting out the 15-minute ceiling to report that nothing was captured (`ebfbe18`).

> [!WARNING]
> This VM stays running after printing its markers, and
> the driver deletes it only after reading the serial log. GCE makes serial output unreadable once
> an instance is `TERMINATED`, and the self-deleting variants lost their evidence that way.
> An **interrupt** is not a leak any more: both scripts register their delete the moment the VM exists and
> reach it on `INT`/`TERM` too, so Ctrl-C during a ≤40-minute pull deletes the instance instead of billing
> quietly (`--keep` is honoured, because that is an explicit request and this is an accident;
> `svshell_image_check.sh` copies `jar_flag_probe.sh:78-79` rather than inventing a third answer).
> What still needs a person is a cleanup that *fails*: the trap prints
> `CLEANUP FAILED -- this VM may still be running` with the exact `gcloud compute instances delete` to run,
> and the scripts only ever clean up instances they created. A run that captured **no marker at all** is not
> deleted either: it prints `no result marker captured, so this run is NOT SCORED. … is being KEPT so the serial
> output stays re-fetchable` and hands over the delete command, because `--start 0` on a deleted VM returns
> nothing — that is `d85f757`, which moved the delete to *after* the verdict was read, so a scan that had
> `PROVEN` itself could no longer print `RESULT: TIMEOUT` for an instance it had just destroyed.

### `image-check/run_in_image.sh`: execute a script inside an image you named

Two reviews that needed "does my branch's image actually run this?" hand-rolled a boot-a-VM driver,
and both hit **harness** bugs rather than pipeline bugs: one runner printed `MISSING LOCAL IMAGE` and
carried on, so both arms died `rc=1` with `records=0`, which reads exactly like the flag under test
changed nothing (`docs/troubleshooting.md` carries that row). This is the generic version: name an
image, name a script, get its stdout off the serial port.

```bash
# Run through the interpreter, and that is the documented form on purpose: this file was tracked 100644 until
# `09b1398`, so `./checks/image-check/run_in_image.sh` answered rc 126 / Permission denied on a clean checkout
# (measured at `d939f31`) — the defect `check_doc_flags.py` grades for a documented `./x`. That commit fixed the
# mode, not the habit: `bash <script>` is how this page reaches the shebang-carrying files that stay 644 by
# decision (`scripts/selftest.sh`, `scripts/selftest.d/declared.sh`, `scripts/selftest.d/womtool.sh`,
# `checks/wdl_inputs_check.py`, `kit/module_profile.py`), and one spelling for both kinds beats two.
bash checks/image-check/run_in_image.sh --dry-run \
    --image $(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6> \
    --probe scripts/test/test_sigpipe.sh
```

What it refuses **before** any compute exists, each verified by running it:

| input | result |
|---|---|
| no `--image` | rc 2, prints how to derive a ref from `IMAGE_REPO` rather than guessing one |
| `--image` containing whitespace | rc 2: "a reference is one token". The build log prints an image *prefix* and *name* on separate lines; joining them with a space is how a nonexistent ref is born (`rc=125 … not found`) |
| `--image` carrying a character no docker reference can contain | rc 2, naming the characters: `it carries "$()", which no docker reference can contain — and which would reach a root shell on the probe VM if this reference were interpolated`. The arms above it were a **denylist of honest mistakes** and were never a boundary — `$(`, a backtick, `(`, `)`, `|`, `>`, `<`, `\`, `&`, `!` passed every one of them, and the reference is interpolated into a startup script that runs as **root** on the VM. The last word is a character allowlist — one copy, in `checks/image-check/image_ref.sh`, sourced by all three launchers here — and it is a shell expansion rather than a `tr`, on purpose: `bad="${ref//[a-zA-Z0-9._\/:@+-]/}"`. It used to be `tr -d 'A-Za-z0-9._-/:@+-'`, and `_-/` in that set is a **reversed range**: GNU tr prints `range-endpoints of '_-/' are in reverse collating sequence order`, deletes nothing, exits 1, and the captured empty string read as "clean" — so on `runs-on: ubuntu-latest` the arm screened **nothing**, while macOS's tr, which reads that `-` literally, kept it alive on the dev box where the gate was green. No external binary means no platform to be dead on. `--image '$(id)'` is refused, while a real `us.gcr.io/...:tag@sha256:…` and a `reg:5000/a/b:1` still pass `--dry-run`; `scripts/selftest.sh` pins both halves, because this arm shipped with no assertion at all (`c3196fe`) |
| `--probe` pointing at a 0-byte file | rc 2: booting for an empty script measures nothing. `bash -n` passes on an empty file, which is the same trap one layer down |
| a non-empty probe | rc 0 under `--dry-run`: prints its byte count **and its non-comment byte count**, so a file that is all comments cannot pass as content |

`--dry-run` prints the instance it would create, the assembled startup script, and validates that
script (`bash -n`, marker present), all of it with zero configuration and zero cloud calls. The
other half is not proven here: this repo's gate is offline, so **no instance was created by any of
this repo's checks**. What the live path adds is `gcloud compute instances create` with
`--entrypoint bash`, the probe fed on stdin, and a poll for the `RUN_IN_IMAGE=<marker>` serial marker
under a 25-minute ceiling. It does not power the instance off itself, for the reason in the lifecycle
note above.

## `audit_history.py`: what the object store would publish

`make audit` answers "is the publishable file set clean today?". That is not the question a push asks.
The version of `docs/handoff/003-single-sample-blockers-pr966.md` committed at `f8d158a` carried three
absolute home paths, a dev bucket object path, a registry namespace containing the operator's name, a
project id and a workspace bucket UUID, and the remote is public. A later commit scrubbed the file to the
placeholder convention, `make audit` has printed `hits=0` ever since, and none of that unpublished a
thing: the old blob is still reachable from `main`, still served to a clone, and GitHub still renders it
at the commit URL.

So this checker grades **blobs and commit messages** rather than files, and classifies every hit by
whether it can still travel:

| class | meaning | verdict |
|---|---|---|
| `HEAD` | current content of a tracked path | fails (the half `audit` already covers) |
| `STAGED` | in the index, not yet committed | fails: one commit from shipping |
| `HISTORY` | reachable from a ref, not at HEAD | reported by default; `--publish` fails. **This is the class `audit` cannot see.** |
| `DANGLING` | in the object store, reachable from no ref | advisory: a normal push does not send it |

    make audit-history                 # what a commit would ship: HEAD + STAGED must be clean
    make audit-history PUBLISH=1       # run this before `git push`; HISTORY must be clean too
    ./scripts/audit_history.py --selftest

Both arms fired on real values on the same day, 2026-10-09. HEAD caught a personal registry path
(`us.gcr.io/<project>/<user>/gatk-sv/sv-pipeline:<tag>`, the username quoted from the object rather than
reproduced here, because a doc that names it is the same publication the check refuses) by way of a
value copied out of a live capture — the capture describes one run, and copying from it silently installed a default
(`ba2e5c3` removed all 13 image keys from that layer and the loader now refuses one). HISTORY named a live method config
that had been captured earlier the same day and already moved to an ignored path: the file is gone from the tree, the
blob is still reachable, which is precisely what `a scrubbed file does not unpublish its blob` means. So `make test`
green and `make audit-history PUBLISH=1` red is not a contradiction — it is the gate saying *this is fine to keep
locally and not fine to push*.

Detectors are imported from `audit.py` rather than copied (a second copy of a credential pattern list is
a second list that stops being true), so the shapes are shared and the coordinate half is derived from the
machine running it (8 settings here, 5 of which matched a shipped default and are skipped). Findings are
masked: the rule, the length, and bounded context, but never the whole value.

**What it actually reads.** Every blob at or under the 8 MiB budget is decoded `errors="replace"` and graded,
*including* the ones that are not valid UTF-8 — a coordinate is ASCII, so replacement costs a byte of context
and nothing else. The `except UnicodeDecodeError: continue` that used to sit there bought exactly one thing:
a committed `.vcf.gz` or image layer carrying a workspace name was invisible to the only check that exists for
a published blob, while `audit.py` decoded the *working* set leniently and this file's own canary
(`scripts/selftest.sh`) says a binary is REPORTED BY FILENAME, not skipped. Blobs **over** the budget are
still not read — and are therefore counted and NAMED every run: `partial_report()` prints
`PARTIAL: N blob(s) exceed the 8,000,000-byte read budget` and the count is gated against
`MAX_OVERSIZED_BLOBS`, so `HISTORY 0` can never be presented as a clean answer when it was a partial one.
The ceiling is **0**, and that is a measurement rather than a hope: on 2026-10-08 at `d939f31`,
`git cat-file --batch-all-objects --batch-check` reported **591 blobs, largest 177,693 bytes, 0 over the
budget**, and re-decoding every one of them found **2** that fail strict UTF-8 — the two fixtures `8fbc516`
planted, which are graded now instead of skipped. Those three counts belong to the object store on that date and
move with every commit, so they are dated here rather than asserted; `MAX_OVERSIZED_BLOBS = 0` is the constant
that does not move, and raising it is a written admission that a named blob
may ship unaudited, which is verbatim what the failure text says (`d27e48f`).

Its selftest's control is asymmetric on purpose: a planted coordinate in a file that a later commit
deletes must still be found (as `HISTORY`), and the blessed placeholder spelling `gs://<your-dev-bucket>/`
must produce nothing. A checker that flagged both, or neither, is not reading the store. The over-budget leg
asserts the same split the other way: an oversized blob is announced by count and name and is RED at ceiling 0
naming `MAX_OVERSIZED_BLOBS`, GREEN at ceiling 1, and still prints the `PARTIAL` line at both — the ceiling
gates the verdict and never the announcement — and deleting the file does not un-count the blob, because the
object is still reachable.

**What `PUBLISH=1` reports on this repo today, and why each is accepted**, recorded here rather than
waived silently:

1. `checks/wdl_semantics.py:318`, a historical blob: an illustrative per-user temp path (the
   macOS temp-directory *shape*, written with an ellipsis and a fake name) inside a docstring explaining
   that such paths differ per user. A shape hit on prose, not a coordinate.
2. one commit message containing the operator's username as a bare word, in the message that
   *describes* this leak. The same username is already author metadata on every commit in the pushed
   history, so scrubbing prose changes what is public by nothing while rewriting author fields would
   destroy attribution. Left alone, and deliberately not spelled out here, because this file is graded by
   the same audit and a checker's own documentation is not exempt from its rules.
3. `scripts/audit_history.py:177`, a historical blob: a fabricated key header, written as a
   selftest fixture and followed by the words "not really a key". The shape is what the rule matches, so
   the rule fired on the file that shipped it, and that is the guard behaving correctly rather than
   pedantically: a shape rule that quietly learned which files it may ignore is not a shape rule. The fix
   went into the fixture, not the rule (assemble the header at runtime, plant it in a temp repo that is
   never tracked), which is why this is a `HISTORY` line now instead of a `HEAD` one. No key material ever
   existed in that blob, so there is nothing to rotate, and rewriting history to bury a fixture would
   trade real commit dates and attribution for a cosmetic gain.

The rewrite that cleaned the rest was `git filter-repo --replace-text` with six pairs (home paths, dev
bucket, registry namespace, project id, two workspace-bucket handle forms), chosen from what the checker
reported rather than from memory. Verification after it: `HEAD^{tree}` byte-identical to the pre-rewrite
HEAD (`8172f9a2`), content untouched with only history rewritten, commit count unchanged at 28, author fields
unchanged, and a re-scan with those six needles as patterns returning zero anywhere in the store. A bundle
of the pre-rewrite refs is kept out of the repo as the rollback path. What no rewrite can do is recall a
copy GitHub or a crawler already holds; with 0 forks and 0 watchers the reachable-copy surface is as small
as it gets, though not zero.

## Using them as gates


Pre-submit, in this order (cheapest first, and each one can invalidate the ones after it):

```bash
scripts/fetch_wdl.py --ref HEAD --dest "$GSVTK_WORK/wdl/HEAD"
checks/wdl_gate.sh --strict HEAD                                  # seconds
checks/svshell_contract_check.py                                  # seconds
checks/svshell_jq_plumbing_scan.py --compare-to main              # needs jq
python terra/batch_check_inputs.py --step 10                      # needs womtool + config
```

`--strict` and `--compare-to` are the two flags that turn "here is a list" into "this branch
regressed", and they are the ones worth wiring into automation. Anything that compares against a
ref is only as trustworthy as that ref: `origin/main` today, not `main` from last week.
