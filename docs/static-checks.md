# Static checks: catching it before a VM boots

Seven checkers, all local, all seconds, none needing data, docker, or a Terra account. They
exist because gatk-sv has classes of breakage that no existing CI sees:

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

A pass means the derived type is a shipped table's type and the read names are its columns. It does not
mean the entity **row** exists (`terra.entity_sample()` before any submission), that the config's values
are right (Stage B of the plan), or that Terra's own `rootEntityType` agrees — that string exists only in
a live workspace, and is still an open decision (`docs/plan-launch-any-module.md`, "Risks not resolved").
Member attributes read through a collection (`${this.sample_sets.ploidy_table}`) are listed and **not**
checked: the member entity type is not derived, and plural-to-singular is the guess that binds nothing
when wrong. One machine line per config (`GSVTK-ENTITY …`), one `GSVTK-ENTITY-SUMMARY` census line, and one
`GSVTK-ENTITY-UNCHECKED` line that counts the three gaps above instead of leaving them to be inferred — because
"no refusal printed" and "nothing left unexamined" are different facts, and the first run of this tool was read
as covering the second.

    checks/terra_entity_check.py --repo "$GSVTK_GATK_SV_CHECKOUT" --ref main | grep UNCHECKED

At `e1909d2f` that prints `configs=31 member-attr-reads=64 configs-with-member-attrs=14
workspace-binding-reads=304 configs-with-workspace-reads=30 root-entity-type=never-read`: the 64 is the
member-attribute count the plan carries, and 30 of 31 configs bind at least one `${workspace.*}` value, which is
Stage B's job — counted here, deliberately not resolved, since resolving it from a test sample would report a
value for a production config that came from a test. `root-entity-type=never-read` is not a limitation to be
fixed here: that string lives only in a live workspace. The same line on the fixture corpus reads
`configs=20 member-attr-reads=7 configs-with-member-attrs=3 workspace-binding-reads=8
configs-with-workspace-reads=7`, and `scripts/selftest.d/entity.sh` asserts those fixture numbers exactly — the
corpus is committed, so a fixture that changes shape has to move the assertion with it. The line prints on exit 0
too, and carries no verdict word: it says what was not looked at, never whether it was fine.

Graded offline by `scripts/selftest.d/entity.sh` against a committed synthetic corpus — 20 configs, one per
refusal shape, including the trap (a config whose member collection is a different type from its name key)
and the bait table that makes the dead member rule look right: **54 assertions with a gatk-sv checkout
holding `e1909d2f`, 44 passed + 1 named counted skip without one** — the skip names how many assertions it
withheld, which is the reporting shape `docs/gap-ledger.md` C10 still asks for elsewhere. No checkout, no
network, no credentials, no jar.

## `image-check/`: proof about shipped bytes, not checkout bytes

A checkout can be checked statically; an image has to be asked. Both scripts boot one throwaway
GCE VM (needs `GSVTK_PROJECT`), pull the image with the instance's own credentials, and stream
results to the serial console:

```bash
# name the image whose bytes you want proven (--image is required: an unnamed image cannot be evidence)
checks/image-check/svshell_image_check.sh --image "$(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6>"
checks/image-check/jar_flag_probe.sh --image <ref>     # which GenotypeSVs flags the shipped jar accepts
```

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

> [!WARNING]
> This VM stays running after printing its markers, and
> the driver deletes it only after reading the serial log. GCE makes serial output unreadable once
> an instance is `TERMINATED`, and the self-deleting variants lost their evidence that way.
> If a check dies mid-run, `gcloud compute instances delete <name>`; the scripts only clean up
> instances they created.

### `image-check/run_in_image.sh`: execute a script inside an image you named

Two reviews that needed "does my branch's image actually run this?" hand-rolled a boot-a-VM driver,
and both hit **harness** bugs rather than pipeline bugs: one runner printed `MISSING LOCAL IMAGE` and
carried on, so both arms died `rc=1` with `records=0`, which reads exactly like the flag under test
changed nothing (`docs/troubleshooting.md` carries that row). This is the generic version: name an
image, name a script, get its stdout off the serial port.

```bash
checks/image-check/run_in_image.sh --dry-run \
    --image $(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6> \
    --probe scripts/test/test_sigpipe.sh
```

What it refuses **before** any compute exists, each verified by running it:

| input | result |
|---|---|
| no `--image` | rc 2, prints how to derive a ref from `IMAGE_REPO` rather than guessing one |
| `--image` containing whitespace | rc 2: "a reference is one token". The build log prints an image *prefix* and *name* on separate lines; joining them with a space is how a nonexistent ref is born (`rc=125 … not found`) |
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

Detectors are imported from `audit.py` rather than copied (a second copy of a credential pattern list is
a second list that stops being true), so the shapes are shared and the coordinate half is derived from the
machine running it (8 settings here, 5 of which matched a shipped default and are skipped). Findings are
masked: the rule, the length, and bounded context, but never the whole value.

Its selftest's control is asymmetric on purpose: a planted coordinate in a file that a later commit
deletes must still be found (as `HISTORY`), and the blessed placeholder spelling `gs://<your-dev-bucket>/`
must produce nothing. A checker that flagged both, or neither, is not reading the store.

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
