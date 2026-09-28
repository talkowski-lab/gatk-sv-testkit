# Static checks: catching it before a VM boots

Seven checkers, all local, all seconds, none needing data, docker, or a Terra account. They
exist because gatk-sv has classes of breakage that no existing CI sees:

| Breakage | Why normal CI misses it | Checker |
|---|---|---|
| A call site that never binds a required input | `miniwdl check` treats `IncompleteCall` as a warning and exits 0 | `wdl_gate.sh` |
| A call site passing an input the callee never declared | typechecking does not compare call sites across files | `wdl_gate.sh` |
| A renamed `sv_shell` JSON key with a reader left behind | `jq -r '.missing'` yields the string `null`, forwarded as `--flag null`, failing stages later | `svshell_contract_check.py`, `svshell_jq_plumbing_scan.py` |
| A workflow-scope `write_*`, a `File` input only ever tested with `defined()`, a `pipefail` pipe whose reader exits early, or a command block that is not valid bash | to a WDL validator a command block is a string, and "this File is only tested for" is a localization fact, not a type error | `wdl_semantics.py` |
| A shipped image whose jar predates the flags its WDL passes | the image is built from a pinned commit, not your branch | `image-check/` |
| "what breaks if I change this file?" answered from memory | blast radius is a graph question, and neither CI nor `miniwdl check` answers reachability across imports and calls | `wdl_reach.py` |
| A Terra method config binding an input the WDL at that ref does not declare | `wdl_gate.sh` compares call sites **inside** a WDL tree; nothing there knows about method configs, and `validate` asks Terra — which needs the ref published on Dockstore | `terra/batch_configs.py check --against <ref>` (below, and it is the pre-check of `create`/`validate`) |

The last checker lives in `terra/` rather than `checks/` because it checks this repo's own config
table against a WDL ref, not gatk-sv's source. It is still offline: `git archive` of `wdl/` from
`GSVTK_GATK_SV_CHECKOUT`, parsed by miniwdl, no Terra call and no published ref. [comparing a
head-to-head's configs to their ref](terra-head-to-head.md) has the findings it produces and the one
branch-only key it currently reports against `main`.

## `wdl_gate.sh` — is it launchable, not just valid

```bash
checks/wdl_gate.sh                       # origin/main vs $GSVTK_BRANCH, default workflows
checks/wdl_gate.sh v1.1.1 HEAD           # any two refs
checks/wdl_gate.sh --wf SVShell HEAD     # one workflow
checks/wdl_gate.sh --strict HEAD         # nonzero exit if anything is unlaunchable
```

Needs `miniwdl` (`python -m pip install -r requirements-dev.txt`, or into `./.venv`) and a local
gatk-sv clone (`GSVTK_GATK_SV_CHECKOUT`). Both are checked up front and the fix is printed rather
than a stack trace appearing.

`miniwdl` is resolved by `./kit/gsvtk-config miniwdl` — `MINIWDL`, then `PATH`, then the bin next
to the interpreter, then `./.venv/bin` — and `terra/wdl_flat.py --check` uses the same resolver.
That is not convenience: the console script `make setup` installs lives in a venv the invoking
shell has usually not activated, so `command -v miniwdl` said "not found" on the machine where
`miniwdl check` passes, and the WDL loop got written off as unavailable.

The suite's own interpreter is a third knob, `PYTHON=…` (`scripts/selftest.sh:29` — not
`$GSVTK_PYTHON`, which is what the *tools* use). It matters for reading the output: two checks
(`wdl_semantics --selftest`, `build_inputs --selftest`) are skipped with a named reason when that
interpreter cannot `import WDL`, so on a machine with miniwdl installed those SKIP lines never print and
the branch is never exercised. It was exercised by pointing `PYTHON=` at a bare venv: both skips printed
by name, and the count check then said "66 ran + 6 skipped = 72, but make help claims 83" — the twelve
failures in that run are the bare interpreter missing the kit's own packages, which are not optional.

Each ref is materialized to **its own directory** by `scripts/fetch_wdl.py` (git-archive of one
ref, with a `.provenance` file recording the exact SHA). That is not tidiness: WDL imports
resolve by filename within the directory, so a mixed tree resolves against the wrong version and
reports a result that is neither the old bug nor the new one.

The output is a count per ref, meant to be **diffed**:

```
WORKFLOW                       REF              EXIT   INCOMPLETECALL   STALE-BINDINGS
SVShell                        main@9a34dc12    0      2                0
SVShell                        main@77c1e0b2    0      3                1
```

(Pick refs where **both** sides actually contain the workflow you named: `--strict` now fails with an
explanation when `--wf SVShell` names something that does not exist at that ref, instead of reporting
`no hard errors` about a workflow it never looked at. `SVShell` is one of the newer WDLs, so old refs
are a real risk in a long-running comparison table.)

A rise in `INCOMPLETECALL` means a call site stopped binding something; a rise in
`STALE-BINDINGS` means one is still passing an input the callee dropped. That delta is the whole
value: against gatk-sv as it stands there are a few `IncompleteCall` warnings that are deliberate,
so a raw nonzero is not a failure — *the change in the column* is. `--strict` makes nonzero the
exit status when you want a hard gate.

## `wdl_semantics.py` — does it RUN, or does it only typecheck?

`wdl_gate.sh` asks about call bindings. Three gatk-sv branches each fixed a defect that it — and
`miniwdl check`, and `womtool validate` — reported clean, because the defect was one layer down: in
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
worker — use it on a machine already deep in swap, which is the situation that got the knob written:
a full gate was started while a forgotten backgrounded one was still parsing, and 27 GB ended up in the
page compressor.

| Rule | The defect it names | Why no validator sees it |
|---|---|---|
| `WRITE-SCOPE` | a workflow-scope `write_lines`/`write_tsv`/`write_json`/`write_map` | it typechecks; Cromwell on PAPIv2 just cannot materialize a workflow-level File, so the submission dies in seconds |
| `DEFINED-ONLY` | a `File` input whose only appearance in the command is inside `defined(...)` | `defined()` is answered from the localization table, so the file is copied to the shard and never opened. Two whole-genome CRAMs measured $6.47 and two attempts |
| `PIPEFAIL` | a pipe whose reader stops early (`head`, `grep -m`, `sed q`, an awk `exit`) in a task that armed `pipefail` | the producer gets SIGPIPE, rc is 141, and it only happens on a large input. macOS ships bash 3.2, which has no `pipefail` at all, so the laptop cannot reproduce it and reading the tree is the cheap half |
| `SHELL-SYNTAX` | a command block `bash -n` will not parse | both WDL validators accept an unbalanced paren, because to them it is a string |

Measured at gatk-sv `main` — and these are **upstream counts to diff, not findings against you**:

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

Three things worth knowing before you trust or distrust a number:

- **The renderer is the AST's own literal/placeholder split, not a regex.** `task.command.parts` is
  the command as ordered literals and placeholders, so rendering is "join the literals, stub the
  placeholders". A regex has to guess where `~{true='(' false=')'}` ends and invents a syntax error
  nobody shipped.
- **Stubbing with a word leaves 2 artifact rows at `main`** (both `TasksMakeCohortVcf`, where a
  placeholder expands to a leading pipe). They stay, deliberately: the obvious fix — check a second
  rendering with an *empty* stub and require both to fail — was measured and discards **60**
  findings, every one of them an empty stub deleting the operand of `done <`, `if` or `>`. It removes
  the two artifacts by removing the check. `SHELL-SYNTAX=2` versus a baseline of `2` is no change;
  `3` is yours.
- **`LOAD-FAILURES` is a rule, not a footnote.** A file miniwdl cannot parse contributes nothing to
  any count, which is how both prototype scans in those reviews would have reported a smaller number
  than the truth. Nonzero here means every count beside it is a partial answer, and the tool exits 2
  rather than 0.

Loading is 33 s of the 40 s scan (`bash -n` across all 315 blocks is 2.5 s), so the parse fans out
per file: 9.3 s on 8 workers, falling back to serial if a process pool cannot start. A scan that
refuses to run because it tried to be fast is worse than a slow one.

`wdl_gate.sh` runs it once per ref and prints one `SEMANTICS` row beside the binding rows. At two
refs it also prints what moved:

```
        SEMANTICS DELTA SHELL-SYNTAX   2 -> 3 (+1 vs baseline)
```

`--strict` fails on a **rise** and never on the baseline's own findings, because on gatk-sv today
three of these four counts are legitimately nonzero.

`--block TASK` prints one task's rendered command. It is what a branch's own regression test wants
— grade the WDL, not a copy of the block — and it is the entry point for `image-check/` below, which
is how you prove a real block survives in the image it will run in.

## `wdl_reach.py` — the blast radius of a changed file

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
whose command block mentions a script name — which is the only way to connect a `.sh` edit to the WDLs
that would run it. On gatk-sv `main` that graph is 118 files, 657 nodes, 1554 edges
(`imports-outside=0 unresolved-calls=0` printed alongside, so a silently missing edge is visible):
`--reverse --target Structs.wdl` reaches 116 files at depths 1-3, `--target MakeCohortVcf.wdl` reaches
209 nodes including 39 scripts.

An unknown target exits nonzero and says what it looked for
(`no file, workflow, task or script of that name is in … (118 .wdl file(s) scanned …)`, plus the
closest names), and a target that nothing reaches prints `NOT REACHED` with the scan count — never an
empty table, which is the failure this repo names.

**It is not in `make test`.** A full-tree parse costs ~34 s (miniwdl, whole tree), which is the right
price for a question you ask deliberately and the wrong price for a gate that must run on every
change. Its `--selftest` (4 tiny fixtures, chain resolved both directions, orphan named as one) *is*
in the gate.

## `svshell_contract_check.py` — the rename that becomes `null`

```bash
checks/svshell_contract_check.py                  # $GSVTK_GATK_SV_CHECKOUT, or --repo DIR
checks/svshell_contract_check.py --repo <clone> -v
```

`single_sample_pipeline.sh` chains ~14 module scripts, each fed an `inputs.json` assembled by a
`jq -n` block from the top-level inputs plus the previous module's `outputs.json`. Because `jq`
returns the string `null` for a missing key instead of failing, a renamed key turns into
`--some-flag null` several stages later — long after the cause, inside a VM, after the expensive
part. gatk-sv has no CI coverage of `src/sv_shell` at all.

The checker cross-references statically:

- driver `$inputs[0].KEY` reads **vs** what the top-level supply actually contains — the shipped
  `sample_inputs/single_sample_pipeline*.json` fixtures plus the `--arg`/`--argjson` names in
  `wdl/SVShell.wdl` (its jq is `$ARGS.named`, so argument names *are* the keys);
- module `outputs.json` writes **vs** later reads of those keys.

**Read the findings as a set, not a verdict.** Run against upstream it reports several unsupplied
reads, mostly gcnv hyperparameters that arrive from elsewhere in real use. Their value is the
diff: run before and after your change, and any *new* unsupplied read is a rename you missed.

**Coverage is printed, and partial coverage is a failure, not a clean run.** The header reports
`N of M stage calls compared`; if `N < M` the verdict is `NOT PROVEN` and the exit status is 1,
because "no findings" from a checker that did not look is the most expensive possible output. (This
is not hypothetical: the first version of this checker compared a handful of the ~14 stage calls — it
missed bare object keys, dashed keys, single-quoted module reads and `// default` reads — and still
reported a clean run on the subset it saw. It now compares 14 of 14 against real gatk-sv.) Key
recognition was also widened, so a key written *any* of `KEY:`, `"KEY":`, `'KEY':`, `"KEY":` with
dashes, or read with a `// default`, is no longer invisible.

`--strict` turns the pre-existing upstream findings into a nonzero exit as well; `--selftest` runs
the parser against hostile inline fixtures so a regression in *detection* fails the gate rather than
quietly shrinking coverage (`make selftest` runs it, and pins a minimum number of compared stage
calls).

## `svshell_jq_plumbing_scan.py` — execute the plumbing instead of reading it

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

The scan reports its own **coverage** on one line — `blocks: P in file, E extracted, X executed,
Y errored, Z with null/empty` — and refuses to summarise a partial scan: if `X < P` you get a
nonzero exit and a message saying the verdict means nothing. Extraction used to skip blocks that were
written a little differently from the canonical `jq -n \` shape (quoting, line breaks, a redirect on
the same line) while the summary still said *every jq block executed*; blocks that cannot be resolved
are now counted and named instead of dropped. Each producer block gets a stub carrying the keys its
consumers read, so a stale reader against another block's output is detectable rather than silently
null, and `--selftest` pins both behaviours for `make selftest`.

## `image-check/` — proof about shipped bytes, not checkout bytes

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
something — per file, so a clean scan of a file nobody pinned can never be quoted as byte identity.
The earlier wording printed one blanket "byte-identity check passed" for the whole run, which people
(naturally) quoted as proof about files it had never compared to anything.

`svshell_image_check.sh` extracts `/opt/sv_shell` with `docker create` + `docker cp` (no bind
mounts, no daemon config) and md5s the driver and shipped fixture. **The `--expect-*` values are
the proof**: compute them from the checkout you actually tested and pass them in —

```bash
git -C <gatk-sv> show <ref>:src/sv_shell/single_sample_pipeline.sh | md5sum
```

— because otherwise the byte comparison prints `byte-identity NOT PROVEN` and is skipped: the
image then only proves it contains *a* driver, not the one you audited. It then runs the plumbing
scan `--tree` on the extracted tree, on the VM host.

`jar_flag_probe.sh` answers "does the jar inside the image accept the flags my branch passes".
Only answerable by *executing* the jar: the image's `/opt/gatk.jar` is built from a pinned GATK
commit, which can predate your argument split, in which case the image cannot run your driver
however correct the driver is.

> **Lifecycle note, learned the hard way:** this VM stays running after printing its markers, and
> the driver deletes it only after reading the serial log. GCE makes serial output unreadable once
> an instance is `TERMINATED`, and the self-deleting variants lost their evidence that way.
> If a check dies mid-run, `gcloud compute instances delete <name>` — the scripts only clean up
> instances they created.

### `image-check/run_in_image.sh` — execute a script inside an image you named

Two reviews that needed "does my branch's image actually run this?" hand-rolled a boot-a-VM driver,
and both hit **harness** bugs rather than pipeline bugs: one runner printed `MISSING LOCAL IMAGE` and
carried on, so both arms died `rc=1` with `records=0` — which reads exactly like the flag under test
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
| `--image` containing whitespace | rc 2 — "a reference is one token". The build log prints an image *prefix* and *name* on separate lines; joining them with a space is how a nonexistent ref is born (`rc=125 … not found`) |
| `--probe` pointing at a 0-byte file | rc 2 — booting for an empty script measures nothing. `bash -n` passes on an empty file, which is the same trap one layer down |
| a non-empty probe | rc 0 under `--dry-run`: prints its byte count **and its non-comment byte count**, so a file that is all comments cannot pass as content |

`--dry-run` prints the instance it would create, the assembled startup script, and validates that
script (`bash -n`, marker present) — all of it with zero configuration and zero cloud calls. The
other half is not proven here: this repo's gate is offline, so **no instance was created by any of
this repo's checks**. What the live path adds is `gcloud compute instances create` with
`--entrypoint bash`, the probe fed on stdin, and a poll for the `RUN_IN_IMAGE=<marker>` serial marker
under a 25-minute ceiling. It does not power the instance off itself, for the reason in the lifecycle
note above.

## `audit_history.py` — what the object store would publish

`make audit` answers "is the publishable file set clean today?". That is not the question a push asks.
The version of `docs/handoff/003-single-sample-blockers-pr966.md` committed at `f8d158a` carried three
absolute home paths, a dev bucket object path, a registry namespace containing the operator's name, a
project id and a workspace bucket UUID — and the remote is public. A later commit scrubbed the file to the
placeholder convention, `make audit` has printed `hits=0` ever since, and none of that unpublished a
thing: the old blob is still reachable from `main`, still served to a clone, and GitHub still renders it
at the commit URL.

So this checker grades **blobs and commit messages**, not files, and classifies every hit by whether it
can still travel:

| class | meaning | verdict |
|---|---|---|
| `HEAD` | current content of a tracked path | fails (the half `audit` already covers) |
| `STAGED` | in the index, not yet committed | fails — one commit from shipping |
| `HISTORY` | reachable from a ref, not at HEAD | reported by default; `--publish` fails. **This is the class `audit` cannot see.** |
| `DANGLING` | in the object store, reachable from no ref | advisory — a normal push does not send it |

    make audit-history                 # what a commit would ship: HEAD + STAGED must be clean
    make audit-history PUBLISH=1       # run this before `git push`; HISTORY must be clean too
    ./scripts/audit_history.py --selftest

Detectors are imported from `audit.py` rather than copied — a second copy of a credential pattern list is
a second list that stops being true — so the shapes are shared and the coordinate half is derived from the
machine running it (8 settings here, 5 of which matched a shipped default and are skipped). Findings are
masked: the rule, the length, and bounded context, never the whole value.

Its selftest's control is asymmetric on purpose: a planted coordinate in a file that a later commit
deletes must still be found (as `HISTORY`), and the blessed placeholder spelling `gs://<your-dev-bucket>/`
must produce nothing. A checker that flagged both, or neither, is not reading the store.

**What `PUBLISH=1` reports on this repo today, and why each is accepted** — recorded here rather than
waived silently:

1. `checks/wdl_semantics.py:318`, a historical blob: an illustrative per-user temp path (the
   macOS temp-directory *shape*, written with an ellipsis and a fake name) inside a docstring explaining
   that such paths differ per user. A shape hit on prose, not a coordinate.
2. one commit message containing the operator's username as a bare word — in the message that
   *describes* this leak. The same username is already author metadata on every commit in the pushed
   history, so scrubbing prose changes what is public by nothing while rewriting author fields would
   destroy attribution. Left alone — and deliberately not spelled out here, because this file is graded by
   the same audit and a checker's own documentation is not exempt from its rules.

The rewrite that cleaned the rest was `git filter-repo --replace-text` with six pairs (home paths, dev
bucket, registry namespace, project id, two workspace-bucket handle forms), chosen from what the checker
reported rather than from memory. Verification after it: `HEAD^{tree}` byte-identical to the pre-rewrite
HEAD (`8172f9a2`) — content untouched, only history — commit count unchanged at 28, author fields
unchanged, and a re-scan with those six needles as patterns returning zero anywhere in the store. A bundle
of the pre-rewrite refs is kept out of the repo as the rollback path. What no rewrite can do is recall a
copy GitHub or a crawler already holds; with 0 forks and 0 watchers the reachable-copy surface is as small
as it gets, not zero.

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
