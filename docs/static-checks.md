# Static checks

The tools under `checks/` find gatk-sv breakage that ordinary CI and `miniwdl check` miss, before
anything is launched. Most of them are offline: they read a local gatk-sv clone (or a materialized
tree) and need no data, no Docker and no Terra account. The exceptions are the `image-check/` launchers,
which boot a GCE VM, and are marked as such below.

`./gsvtk check` bundles the commonly used checks into one command. Every tool here can also be run
directly, and each section below shows how.

## Overview

| Check | What it catches | Cost | Needs |
|---|---|---|---|
| [`wdl_gate.sh`](#wdl_gatesh) | call sites that never bind a required input or pass an undeclared one; input JSONs that miss required keys or carry unknown ones | seconds to a minute per ref | miniwdl, jinja2, gatk-sv clone; womtool jar optional |
| [`wdl_inputs_check.py`](#wdl_inputs_checkpy) | the input-JSON half of `wdl_gate.sh`, run alone | seconds | miniwdl, jinja2, gatk-sv clone |
| [`wdl_semantics.py`](#wdl_semanticspy) | workflow-scope `write_*`, `File` inputs only tested with `defined()`, early-exit pipes under `pipefail`, command blocks `bash -n` rejects | ~10 s with 8 workers | miniwdl |
| [`wdl_reach.py`](#wdl_reachpy) | which files, workflows, tasks and scripts a change reaches, and which docker images those workflows bind | ~35 s tree load | miniwdl |
| [`svshell_contract_check.py`](#svshell_contract_checkpy) | an `sv_shell` JSON key that is read but never supplied | seconds | gatk-sv clone |
| [`svshell_jq_plumbing_scan.py`](#svshell_jq_plumbing_scanpy) | `jq -n` blocks that put `null` into a module argument | seconds | `jq`, gatk-sv clone |
| [`terra_entity_check.py`](#terra_entity_checkpy) | Terra launch configs whose entity type, entity columns, workspace attributes or required inputs do not line up | seconds | gatk-sv clone; miniwdl for the required-input layer |
| [`version_resolves.py`](#version_resolvespy) | a Dockstore version that does not resolve, or resolves to a commit behind the branch tip | instant | a captured Dockstore listing; optionally `git ls-remote` output |
| [`body_golden_diff.py`](#body_golden_diffpy) | differences between the config body this repo would POST and a config captured from a workspace | instant | an untracked capture file |
| [`cost_estimate.py`](#cost_estimatepy) | the dollar cost of a finished launch | instant | expanded Cromwell metadata, a rates file |
| [`failure_cause.py`](#failure_causepy) | whether a failed workflow failed in a task or in the cloud | instant | expanded Cromwell metadata |
| [`sigpipe_pair.sh`](#sigpipe_pairsh) | whether a SIGPIPE guard actually changes a pipeline's exit status | instant | bash; perl if SIGPIPE is inherited as ignored |
| [`image-check/`](#image-check) | what a shipped image actually contains and runs | one GCE VM, up to 25–40 min | `GSVTK_PROJECT`, gcloud; `--confirm` through `gsvtk` |
| [`make audit`](#make-audit) | private values in the files that would be published | seconds | git |
| [`make audit-history`](#make-audit-history) | private values in any blob or commit message a push would send | seconds | git |

A related offline check lives in `terra/`: `terra/batch_configs.py check --against <ref>` compares every
key this repo's Terra method configs bind against the WDL at a ref. It is the pre-check that
`create` and `validate` run; see [terra-head-to-head.md](terra-head-to-head.md).

### Reading the output

Several of these tools report counts that are nonzero on unmodified gatk-sv. Those counts are a
baseline: run the tool on the base ref and on your branch and compare. The flags that turn a count
into a pass/fail gate are `--strict` (`wdl_gate.sh`, `wdl_semantics.py`, `svshell_contract_check.py`)
and `--compare-to <ref>` (`svshell_jq_plumbing_scan.py`). A gate is only as good as its baseline, so
compare against a current `origin/main`, not a stale local `main`.

Every tool that can only see part of its input says so in its output (a `NOT PROVEN`, `SKIPPED`,
`LOAD-FAILURES` or coverage line). Read those lines before reading a clean result as a pass.

## `gsvtk check`

```bash
./gsvtk check                                   # contract check + jq scan on GSVTK_GATK_SV_CHECKOUT
./gsvtk check HEAD                              # also wdl_gate.sh at HEAD; the jq scan compares to HEAD
./gsvtk check your-branch --wf GenotypeBatch --strict
./gsvtk check --compare-to origin/main          # jq scan fails only on nulls new since origin/main
./gsvtk check HEAD --semantics                  # add wdl_semantics.py (slow)
./gsvtk check --reach --wf GenotypeBatch        # add wdl_reach.py, one answer per --wf
./gsvtk check --reach --images --wf GenotypeBatch
```

What runs:

1. `wdl_gate.sh` at `<ref>`, only when a ref is given (`--wf` and `--strict` are passed through).
2. `svshell_contract_check.py` against `--repo DIR` (default `GSVTK_GATK_SV_CHECKOUT`).
3. `svshell_jq_plumbing_scan.py`, if `jq` is on `PATH`. With `--compare-to REF`, or with a positional
   ref, it runs in gate form against that ref; otherwise it runs an absolute scan, which is red on
   unmodified gatk-sv.
4. `wdl_semantics.py`, with `--semantics`.
5. `wdl_reach.py`, with `--reach`. Every `--wf NAME` becomes a `--target`, all answered by one tree load.
   `--reach` without `--wf` is a usage error. `--images` requires `--reach`; when a ref is given it is
   passed as `--inputs-ref`. `--reverse` and the render options are only available on the tool itself.

Exit codes: 0 clean, 1 at least one checker reported findings, 2 usage error. A finding here is a
list to compare against your base ref, not a verdict on its own.

`./gsvtk check image <tool>` runs one of the `image-check/` launchers; see [`image-check/`](#image-check).

## `wdl_gate.sh`

Is a workflow launchable, not just valid? `miniwdl check` reports a call site that never binds a
required input as an `IncompleteCall` warning and exits 0, and it does not compare call sites across
files. It also never opens an input JSON. This gate counts all of those per ref so you can diff them.

```bash
checks/wdl_gate.sh                            # origin/main vs $GSVTK_BRANCH, default workflows
checks/wdl_gate.sh v1.1.1 HEAD                # any two refs
checks/wdl_gate.sh --wf SVShell --wf ResolveCpxSvGenotyping HEAD
checks/wdl_gate.sh --strict HEAD              # nonzero exit if anything is unlaunchable
checks/wdl_gate.sh --tree DIR --inputs-root RENDERED --wf SVShell   # a tree and JSONs you already have
checks/wdl_gate.sh --no-terra HEAD            # skip the Terra input JSONs CI checks with -t
```

**Needs:** miniwdl and jinja2 (both in `requirements-dev.txt`), and a gatk-sv clone at
`GSVTK_GATK_SV_CHECKOUT`. miniwdl is resolved as `$MINIWDL`, then `PATH`, then the bin next to the
interpreter, then `./.venv/bin`, so an unactivated venv still works (`./kit/gsvtk-config miniwdl`
shows what it finds). A womtool jar is optional (`WOMTOOL_JAR`).

Each ref is extracted to its own directory by `scripts/fetch_wdl.py` (a `git archive` of `wdl/` plus
a `.provenance` file with the exact SHA). WDL imports resolve by filename within a directory, so two
refs must never share one.

### Output

One row per workflow per ref:

```
WORKFLOW          REF              EXIT   INCOMPLETECALL   STALE-BINDINGS   MISSING-INPUTS   EXTRA-KEYS
SVShell           main@9a34dc12    0      2                0                NO-INPUT-JSON    NOT-CHECKED
SVShell           main@77c1e0b2    0      3                1                NO-INPUT-JSON    NOT-CHECKED
IntegrateGDVcf    7fbf1171         0      0                0                1                clean
```

| Column | Meaning | How to read it |
|---|---|---|
| `INCOMPLETECALL` | call sites that do not bind a required callee input | delta column: gatk-sv has a few deliberate ones, so a rise is the finding |
| `STALE-BINDINGS` | call sites passing an input the callee does not declare | delta column |
| `MISSING-INPUTS` | required workflow inputs absent from every input JSON CI pairs with the workflow (womtool: `Required workflow input ... not specified`) | hard finding, with or without `--strict` |
| `EXTRA-KEYS` | keys an input JSON names that the WDL cannot accept (womtool: `Unexpected input provided`) | `clean` or `N extra`; hard finding, with or without `--strict` |

`NO-INPUT-JSON` / `NOT-CHECKED` mean CI has no input JSON for that workflow. They are printed and
counted but are not a `--strict` failure, because CI is green on such a workflow.

Each ref also gets a `SEMANTICS` row from `wdl_semantics.py --summary-only`. With two refs a delta line
is printed for anything that moved:

```
        SEMANTICS DELTA SHELL-SYNTAX   2 -> 3 (+1 vs baseline)
```

`--strict` fails on a rise in a semantics count, never on the baseline's own findings.

Use refs where both sides contain the workflow you name. With `--strict`, a `--wf` that does not exist
at a ref fails with an explanation.

### The input-JSON layer

`MISSING-INPUTS` and `EXTRA-KEYS` come from `wdl_inputs_check.py`. It renders the ref's own default input
JSONs (gatk-sv's build_default_inputs.sh, run in a `git archive` of `inputs/` and `scripts/inputs/`; your checkout is
never written to), pairs them with workflows the way gatk-sv's `validate.sh` does, including the Terra
configs CI validates with `-t`, and compares key sets. Values are never compared or opened.

With `WOMTOOL_JAR` set, each pair also runs CI's exact `java -jar $WOMTOOL_JAR validate <wdl> -i <json>`
and the two answers are compared. Without it, that layer prints a counted `SKIPPED` row, and
`--strict` fails on it. The same applies when the extra-key answer could not be produced:
`SKIPPED — N of M workflow(s): no extra-key answer was produced`.

The extra-key check uses miniwdl's `available_inputs` (workflow inputs plus every unbound call input,
such as `Workflow.call_name.input`) as the expected set, which is a superset of `womtool inputs`. So it
never reports a key womtool would accept, but it can stay quiet where womtool complains: a struct member
written flat as its own key (`Wf.struct_opt.label`), a namespace container (`{"Wf": {...}}`), and a call
path deeper than `womtool inputs` prints. When womtool is available, its row still catches these.

### Exit codes

0 no hard errors; 1 gate failed; 2 usage; 3 miniwdl not found; 4 `GSVTK_GATK_SV_CHECKOUT` unset.

## `wdl_inputs_check.py`

The input-JSON layer of `wdl_gate.sh`, runnable on its own. The file is not executable, so run it
through the interpreter.

```bash
python checks/wdl_inputs_check.py --wdl-dir "$GSVTK_WORK/wdl/HEAD" --ref HEAD --wf IntegrateGDVcf
python checks/wdl_inputs_check.py --wdl-dir DIR --inputs-root RENDERED     # JSONs already rendered
python checks/wdl_inputs_check.py --ref HEAD --render-only --dest DIR      # render and stop
```

| Flag | Default |
|---|---|
| `--wdl-dir` | a materialized WDL tree (`scripts/fetch_wdl.py --dest`) |
| `--wf` | workflow by WDL file name; repeatable |
| `--repo` / `--ref` | `GSVTK_GATK_SV_CHECKOUT` / `origin/main` |
| `--inputs-root` | a tree holding `inputs/build/**.json`; skips rendering |
| `--no-terra` | skip the Terra configs CI checks only with `-t` |
| `--womtool-jar` / `--java` | `$WOMTOOL_JAR` / `$JAVA`, then `java` on `PATH` |

**Render destination.** `--render-only` deletes its destination before rendering, so it only deletes a
tree it created. It writes a `.gsvtk-render-stamp` (tool and ref) before any content, and then:

| Destination | Result |
|---|---|
| absent or empty | render |
| stamped by this tool for the same ref | replace and re-stamp |
| stamped for another ref | refuse (`reason=other-ref`): use a separate `--dest` per ref |
| non-empty and not stamped | refuse (`reason=un-stamped`): it will not delete a directory it did not create |
| exists and is not a directory | refuse (`reason=not-a-directory`) |

Refusals print `GSVTK-RENDER status=REFUSED jsons=0 reason=<...>` and exit 1. `wdl_gate.sh` and
`wdl_reach.py --images` each render into their own `--dest`, and turn a refusal into a named skip
instead of deleting anything. Two concurrent renders of the same ref into the same directory are not
protected against. The verbatim refusal text is in [troubleshooting.md](troubleshooting.md).

Exit codes: 0 clean, 1 findings or a refused render, 2 usage.

## `wdl_semantics.py`

Both WDL validators treat a command block as a string, and neither checks what a workflow does at
run time. This scanner reports four rules as counts per tree:

| Rule | Finds | Why it matters |
|---|---|---|
| `WRITE-SCOPE` | a workflow-scope `write_lines` / `write_tsv` / `write_json` / `write_map` | it typechecks, but Cromwell on PAPIv2 cannot materialize a workflow-level File, so the submission dies within seconds |
| `DEFINED-ONLY` | a `File` input whose only use in the command is inside `defined(...)` | the file is localized to the VM and never read, which costs time and money for large inputs |
| `PIPEFAIL` | a pipe whose reader stops early (`head`, `grep -m`, `sed q`, an awk `exit`) in a task that sets `pipefail` | the producer gets SIGPIPE and the task exits 141, typically only on large inputs. Sites guarded with `\|\| true` and similar are listed as `GUARDED`, not counted |
| `SHELL-SYNTAX` | a rendered command block that `bash -n` rejects | an unbalanced paren passes both WDL validators |

```bash
checks/wdl_semantics.py --dir "$GSVTK_GATK_SV_CHECKOUT"                 # counts + findings
checks/wdl_semantics.py --dir <fetched-ref-dir> --summary-only          # one KEY=N line
checks/wdl_semantics.py --dir "$GSVTK_GATK_SV_CHECKOUT" --rule pipefail -v
checks/wdl_semantics.py --dir "$GSVTK_GATK_SV_CHECKOUT" --block CondenseReadCounts   # one rendered command
checks/wdl_semantics.py --selftest
```

Example output (gatk-sv `main`):

```
tree: .../gatk-sv   files=118 tasks=315 workflows=109   scan=9.3s (8 workers)
  WRITE-SCOPE=0 DEFINED-ONLY=2 PIPEFAIL=3 SHELL-SYNTAX=2 LOAD-FAILURES=0

PIPEFAIL — 6
  ResolveCpxSv.wdl::GetSeCutoff:6  UNGUARDED (head)  $( awk -F '\t' ... $FILE | head -n1 )
  CollectCoverage.wdl::CondenseReadCounts:5  GUARDED (head)  counts_first_line=$(zcat X | head -n 1 || true)
```

These are upstream baseline counts. Compare your branch against its base; a count that rises is yours.

Other options: `--json PATH` writes a machine-readable artifact, `--top N` limits findings shown per
rule (default 20), `-v` shows all, `--raw` with `--block` prints the literal WDL text instead of the
render, and `--strict` exits 1 on any finding.

Things to know:

- Commands are rendered from miniwdl's own split of literals and placeholders, with each placeholder
  replaced by a stub word. On gatk-sv `main` this leaves two `SHELL-SYNTAX` rows that are rendering
  artifacts (both in `TasksMakeCohortVcf`, where a placeholder expands to a leading pipe). They are part
  of the baseline.
- `LOAD-FAILURES` counts files miniwdl could not parse. Those files contribute nothing to any other
  count, so a nonzero value means the other counts are partial, and the tool exits 2.
- The parse runs in parallel (`--jobs`, default `min(8, cpus)`, each worker holding a few hundred MB).
  Set `GSVTK_SEMANTICS_JOBS=1` (or `--jobs 1`) on a machine short of memory; it also caps the row
  `wdl_gate.sh` runs.
- `--block TASK` is useful for a regression test that should grade the real WDL command rather than a
  copy, and for building an `image-check/` probe.

Exit codes: 0 no findings (or findings without `--strict`), 1 findings with `--strict`, or `--block`
named no task or an ambiguous one, 2 partial scan (`LOAD-FAILURES`).

## `wdl_reach.py`

What does a change reach, or what reaches the thing you changed? The graph has three kinds of edge:
file `import`, workflow `call`, and a task whose command block mentions a script by name. The last is
what connects a `.sh` edit to the WDLs that run it.

```bash
./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --reverse --target Structs.wdl
./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --target MakeCohortVcf.wdl
./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --reverse --target mantatloccheck.sh
./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --target GenotypeBatch --target SVShell
```

- Forward (the default) is what the target reaches; `--reverse` is what reaches the target.
- `--target` accepts a file (`Structs.wdl`), a callable (`MakeCohortVcf`, or `File.wdl::Name`), or a
  script (`mantatloccheck.sh`). It is repeatable: loading the tree costs about 35 s and the walks are
  free, so ask several questions in one run. Each target gets its own answer block, in order.
- A target nothing reaches prints `NOT REACHED` with the scan count. A target that matches nothing prints
  the closest known names, and the run exits 2 even if the other targets were answered.
- The header prints `imports-outside=` and `unresolved-calls=` so a missing edge is visible.
- `--json PATH` writes one artifact covering every target; `--top` and `-v` control how many hits print.

### `--images`

```bash
./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --reverse --target CountSamples --images
./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --reverse --target mantatloccheck.sh \
    --images --inputs-root "$GSVTK_GATK_SV_CHECKOUT"      # read already-rendered JSONs, render nothing
```

A workflow that reaches your change but binds an image built from another commit runs the old code
without any warning. `--images` prints, for each reaching workflow, every `*_docker` input it binds,
the value, and where the value came from: the WDL default, a rendered input JSON (`inputs/build/**`),
or a module profile (`profiles/<module>.json`). `inputs/values/dockers.json` is shown for context.
A workflow with no container input prints `binds NO docker input`; a `${workspace.x}` placeholder or a
computed default prints `UNRESOLVED` with the reason and is counted.

The images are then sorted into buckets:

| Bucket | Meaning | Decided by |
|---|---|---|
| `[0] NOT A BUILD OF THE COMMIT UNDER REVIEW` | a run binding it tests a pre-change image | a release-shaped tag (date-prefixed or `vN`) |
| `[1] BUILT FROM A DIFFERENT COMMIT` | a stale branch build | a `<branch>-<sha>` tag (as `docker/gatk-sv-build.sh` mints it) whose sha is not the commit under review |
| `[2] BUILT FROM THE COMMIT UNDER REVIEW` | contains your change | same tag shape, matching sha |
| `[3] CANNOT SAY` | untagged, or neither convention | never folded into `[2]` |

Bucket `[1]` trusts a sha-shaped tag suffix. Third-party images sometimes use a build timestamp or
hash there (`vapor:header-hash-2fc8f12`), so each line quotes the tag and the sha it parsed; read it
before trusting the bucket.

Options for `--images`: `--inputs-root DIR` reads already-rendered JSONs instead of rendering;
`--checkout DIR` is the clone to render from (default `--dir` if it holds `inputs/`, else
`GSVTK_GATK_SV_CHECKOUT`); `--inputs-ref REF` is the ref to render and the commit under review (default
`HEAD`); `--head-sha SHA` overrides the commit under review. Rendering adds about 2 s and goes
through `wdl_inputs_check.py --render-only` into its own destination. If no value source could be
read, the run prints `IMAGE ANSWER INCOMPLETE` and exits 2.

Exit codes: 0 answered (including `NOT REACHED`), 2 an unknown target, no tree, a partial answer, or an
incomplete image answer, 3 miniwdl missing. `--selftest` runs four fixture WDLs.

`wdl_reach.py` is not part of `make test` because of the tree-load cost; its `--selftest` is.

## `svshell_contract_check.py`

gatk-sv's single-sample driver (single_sample_pipeline.sh under `src/sv_shell/`) chains 14 module
scripts, each fed an `inputs.json` built by a `jq -n` block from the top-level inputs and earlier
modules' `outputs.json`. `jq -r '.missing'` yields
the string `null` instead of failing, so a renamed key becomes `--some-flag null` several stages later,
inside a VM. gatk-sv has no CI coverage of `src/sv_shell`.

```bash
checks/svshell_contract_check.py                     # GSVTK_GATK_SV_CHECKOUT, else cwd
checks/svshell_contract_check.py --repo <clone> -v
checks/svshell_contract_check.py --selftest          # no checkout needed
```

It compares, statically:

- driver `$inputs[0].KEY` reads against the top-level supply: the shipped
  `src/sv_shell/sample_inputs/single_sample_pipeline*.json` fixtures, and the `--arg` / `--argjson` names
  in `wdl/SVShell.wdl` (its jq uses `$ARGS.named`, so argument names are the keys);
- each module's `jq -r '.KEY'` reads against the keys the driver's producer block writes into that
  module's `inputs.json`.

Keys written as `KEY:`, `"KEY":`, `'KEY':`, with dashes, or read with `// default` are all recognized;
a read with a `// default` cannot yield null and is not reported.

The header reports `N stage calls (C compared, U not comparable)`. Upstream gatk-sv has some unsupplied
reads already (mostly gcnv hyperparameters that arrive from elsewhere in real use), so compare the list
before and after your change: a new unsupplied read is a rename you missed.

Exit codes: 0 every compared read has a supplier (prints `NOT PROVEN` if some stage calls could not be
compared); 1 unsupplied reads, or uncompared stage calls under `--strict`; 4 the path is not a gatk-sv
checkout.

## `svshell_jq_plumbing_scan.py`

Reading the driver cannot prove its jq blocks are right; running them can. The scan finds every
`jq -n ... > "${target}"` block, substitutes shell variables (the top-level input file becomes the
fixture, and each variable the driver produces becomes a stub with the keys its producer block wrote),
runs each block with real `jq`, and reports every key that comes out null, empty or the string `"null"`.
It runs two passes so a null produced upstream propagates to its consumers as it would at run time.

```bash
checks/svshell_jq_plumbing_scan.py --repo <worktree>                     # absolute scan
checks/svshell_jq_plumbing_scan.py --repo <worktree> --compare-to main   # gate form
checks/svshell_jq_plumbing_scan.py --tree <extracted /opt/sv_shell>      # scan shipped bytes
checks/svshell_jq_plumbing_scan.py --list-keys
checks/svshell_jq_plumbing_scan.py --selftest
```

**Needs:** `jq` on `PATH`. `--fixture` overrides the top-level input
(default `src/sv_shell/sample_inputs/single_sample_pipeline.json`).

Coverage is printed as `blocks: P in file, E extracted, X executed, Y errored, Z with null/empty`.
Blocks that cannot be resolved are counted and named.

- **Absolute scan** fails on any null. Unmodified gatk-sv has pre-existing nulls, so this is red upstream
  by design.
- **`--compare-to REF`** fails only on nulls the branch introduced (keyed by block, since line numbers
  move), and also fails if coverage is incomplete on either ref. Use this form to gate a change.
  It needs a git checkout, so it cannot be combined with `--tree`; scan both trees and compare the reports.

Exit codes: 0 clean; 1 nulls (new nulls, for `--compare-to`) or incomplete coverage.

## `terra_entity_check.py`

Upstream's Terra launch configs (`inputs/templates/terra_workspaces/**/*.json.tmpl`) bind inputs with
`${this.<attr>}` and `${workspace.<attr>}` but never say which entity type they run against. A wrong
entity type is not an error at submission: every `this.*` binding resolves to nothing after the VMs
boot. This tool answers, per config, offline:

| Layer | Question | Refuses when |
|---|---|---|
| entity | which entity type does the config run against, derived from its one `${this.<etype>_id}` read, and does a shipped table of that type (a TSV headed `entity:<etype>_id` or `membership:<etype>_id`) carry every column it reads? | `cannot-parse`, `no-name-key`, `many-name-keys`, `no-shipped-table`, `columns-missing` (in that precedence) |
| workspace oracle | what attribute names does each flavor's `workspace.tsv.tmpl` declare? Status `OK`, `ABSENT`, `MALFORMED` or `DUPE` | (feeds the value layer) |
| value | where does each bound value come from: `workspace-attr`, `workspace-missing`, `entity`, `literal`, `needs-bundle`, `test-bundle`, `docker`, `mixed`, `no-oracle` | `workspace-missing`, `no-oracle`, `test-bundle`, an unfinished template (`{{` or `${` with no close) |
| required | is every required input of the named workflow carried by a launchable value? | `unbound` |

```bash
checks/terra_entity_check.py --repo "$GSVTK_GATK_SV_CHECKOUT" --ref main   # read a ref via git archive
checks/terra_entity_check.py --tree scripts/selftest.d/fixtures/entity     # the offline fixture corpus
checks/terra_entity_check.py --tree T --corpus inputs/templates/terra_workspaces
./gsvtk entity                                                             # the same, via the CLI
```

Defaults: `--repo` is `GSVTK_GATK_SV_CHECKOUT`, `--ref` is `GSVTK_GATK_SV_REF`, else `HEAD`. The WDL
tree for the required layer is always `<tree>/wdl`. The entity and value layers need only the standard
library; the required layer imports miniwdl through `wdl_inputs_check.py`.

### Output

One machine line per config per layer, plus a summary per layer. The prefixes are stable:
`GSVTK-ENTITY`, `GSVTK-ENTITY-SUMMARY`, `GSVTK-ENTITY-UNCHECKED`, `GSVTK-ORACLE`, `GSVTK-ORACLE-SUMMARY`,
`GSVTK-VALUE`, `GSVTK-VALUE-SUMMARY`, `GSVTK-REQUIRED`, `GSVTK-REQUIRED-SUMMARY`. The full field list
is in `--help`. A `== census ==` block repeats the totals in words.

`GSVTK-ENTITY-UNCHECKED` lists what the run did not evaluate and carries no verdict. Its fields include
`member-attr-reads` (reads through a collection such as `${this.sample_sets.ploidy_table}`, listed but
not checked), `workspace-attribute-values-unread` (the tool checks that a workspace attribute is
declared, never its value), and `required-configs-uncaptured` (configs whose workflow was never opened,
for example every config when miniwdl is missing, with the exit code unaffected). Check that last field
before reading exit 0 as "every required input is bound".

`needs-bundle` means the value is a `{{ <root>.<key> }}` template; it is disclosed, not refused, and no
values profile is opened. `*_docker` bindings are excluded: images must be stated with `--image` or a
profile pin, and `DOCKER-MUST-BE-STATED` / `DOCKER-KEY-UNENFORCED` lines point at docker keys the
launcher would not pin.

A `workspace-missing` refusal names the attribute, the flavor's `workspace.tsv.tmpl`, and the fix: add a
row to that exact file, or stop binding the key. Under `MALFORMED`, `DUPE` or `ABSENT` it says which rows
to repair or collapse, or which file to create. Other TSVs the flavor ships are listed as what exists;
adding a row to one of them does not clear the refusal.

The required layer's `capture=` word says why the question could not be asked, in precedence order:
`NO-MINIWDL`, `NO-WDL-DIR`, `NO-DOCUMENT`, `LOAD-FAILURE`, `NO-WORKFLOW` (the file holds only tasks),
`NO-WDL`, `OK`. A required input bound to a whole object counts as `container`, not `unbound`.

A pass does not mean the entity row exists in a workspace, that the bound values are the correct ones,
or that Terra's `rootEntityType` agrees. `--against-dump` (below) asks that last question.

### Does Terra agree with the derived entity type?

The entity check derives each config's root entity type from its `${this.<etype>_id}` name key. To
check that against the `rootEntityType` Terra actually stores, save the workspace's method configs
(read-only, GETs only) and compare offline:

```bash
./gsvtk terra dump-configs --ns <namespace> --ws <workspace>   # prints the file it wrote, last
./gsvtk entity --against-dump <that file>
```

The tools behind them are `python terra/dump_configs.py` and
`checks/terra_entity_check.py --against-dump FILE`. A new dump tool is needed because the listing
`terra/recon.py` saves carries `rootEntityType` but not `inputs`, so the dump fetches each config in
full.

* **The dump.** With no `--ns`/`--ws` it reads your sandbox (`GSVTK_TERRA_NAMESPACE` /
  `GSVTK_TERRA_WORKSPACE`, exit 4 if unset); any workspace you can read, the baseline included, can be
  named. It writes `$GSVTK_WORK/configs/<ns>__<ws>.json` (`--out` to change). It runs under
  `GSVTK_READ_ONLY=1`. Exit 1 if the listing failed or came back empty, or if any one config could not
  be fetched (the file is still written, with an `errors` list).
* **The comparison** is offline and needs no credentials. Each config prints
  `GSVTK-ENTITY-TERRA config=<ns>/<name>` followed by `AGREE`, `DISAGREE derived=X terra=Y`, or
  `UNDERIVABLE <reason>` (`no-inputs`, `no-inputs-object`, `no-name-key`, `many-name-keys(a,b)`), then
  one `GSVTK-ENTITY-TERRA-SUMMARY` line with a verdict of `AGREE`, `DISAGREE` or `NOTHING-DERIVABLE`.
  Exit 0 if every derivable config agrees, 1 on any disagreement, 2 if nothing was derivable (an empty
  dump is refused, not passed), the dump is unreadable, or `--against-dump` was combined with
  `--tree`, `--repo`, `--ref` or `--corpus`.

Verified offline only, on synthetic dumps and a fake Terra module: every verdict and refusal, the
summary line, and that the dump tool only lists and gets. Unverified until it is run against a real
workspace: that Terra's config payloads carry `inputs` as bare expressions (`this.sample_set_id`), the
format this reads; and what real configs yield (a config that reads no `_id` comes back
`UNDERIVABLE`). The run's `GSVTK-ENTITY-UNCHECKED` line still says Terra's `rootEntityType` is not
asked, because the default mode does not ask it.

### Exit codes

0 every config answered and every value is classifiable and launchable (read `capture=` and
`required-configs-uncaptured=` first); 1 at least one config is `UNRESOLVED`; 2 usage error; 3 a
prerequisite is missing (no checkout, a ref that will not archive, no corpus or no config at that ref,
or no `terra/batch_configs.py` to read the template expander from).

A corpus file that is not valid UTF-8 is reported as a refusal (`cannot-parse`, `values-unreadable`, or
`UNREADABLE table`), not a crash.

The offline phase `scripts/selftest.d/entity.sh` grades this tool against two committed synthetic
corpora, `scripts/selftest.d/fixtures/entity/` (one config per refusal shape) and
`scripts/selftest.d/fixtures/entity-required/` (every `capture=` word). To reproduce the no-checkout
skip, set `GSVTK_GATK_SV_CHECKOUT` to a path that does not exist; an empty value falls through to your
configured profile.

## `version_resolves.py`

Does the Dockstore version a launch will name exist, and is the commit behind it the branch tip?

```bash
checks/version_resolves.py --step 01 --from-file /tmp/trs-single-sample.json    # depth 1
checks/version_resolves.py --from-file capture.json --all-steps \
    --tip-from-file /tmp/ls-remote.txt                                          # depth 2
checks/version_resolves.py --print-entries --all-steps                          # entries a fetch would read
checks/version_resolves.py --selftest
```

The same check is available as `./gsvtk publish` (offline, with `--from-file`) and
`./gsvtk publish-check <branch>` (fetches the listing and the tip itself, both read-only GETs). See
[test-a-branch.md](test-a-branch.md).

- **Depth 1**, `--from-file` alone: does the `(entry, version)` pair the launcher will put in
  `methodRepoMethod` resolve? Prints `RESOLVES`, `NO SUCH ENTRY`, `NO SUCH VERSION`, or a refusal.
  The pair is derived the way the launcher derives it, so a Dockstore entry name that differs from the
  WDL name (`GATKSVPipelineSingleSample.wdl` is published as `SingleSamplePipeline`) is caught.
- **Depth 2**, adding `--tip SHA` or `--tip-from-file FILE` (the output of
  `git ls-remote origin refs/heads/<branch>`): prints `CURRENT`, `STALE` (naming both shas), `ABSENT`, or
  `REFUSED`. `--branch` defaults to the version name, which for these entries is the branch name.
- `--step NN` checks one step of the module profile (default `01`); `--all-steps` checks every entry a
  rerun can name. `--entry` / `--version` override the derived pair.

The tool never fetches. Capture commands (unauthenticated, read-only) are in its module docstring:
the depth-2 listing is
`https://dockstore.org/api/workflows/path/workflow/<entry>/published?include=versions`, which carries
a `commitID` per version. Keep captures out of the repo, since a listing names other people's branches.
A capture that does not cover the requested entry, or has no `commitID` for it, is `REFUSED`, never
`CURRENT`.

**Why a version name can resolve to an old commit.** Dockstore publishes a gatk-sv branch only while it
is listed in that entry's `filters.branches` in `.github/.dockstore.yml`, and upstream's
`readonly_check.yaml` fails any PR that edits that file. If a rebase drops the commit that added the
filter, Dockstore stops syncing the branch but keeps serving the last version it saw under the same
name. Depth 1 says `RESOLVES` and Terra's `validate` passes. On `STALE` or `ABSENT` the tool prints the
remedy and runs none of it: create `<branch>_terra` at the tip, add one commit putting `<branch>_terra`
in that entry's `filters.branches`, wait for the sync, re-check that name, and point `GSV_WDL_VERSION`
at it.

Exit codes: 0 `CURRENT` (or the name resolves, at depth 1); 1 `STALE` / `ABSENT` / not found; 2 usage;
4 `REFUSED`. Through `gsvtk publish`, 3 means `--ls-remote` or `--fetch` could not read the remote.

## `body_golden_diff.py`

Compares the method-config body this repo would POST against a config captured from a workspace that
already ran successfully. The capture usually ran with call caching on and dev image tags, so a
field-by-field diff would fail on exactly the fields that matter. Instead it:

- diffs binding keys and symbolic bindings (`this.*`, `workspace.*`). Literal values are reported as
  `LITERAL-VALUES` but do not affect the verdict;
- asserts two things on the body alone: `useCallCache` is stated and false, and every `*_docker` input is
  a quoted literal naming a version (tag, digest, or a versioned last path segment);
- requires both sides to state the same `rootEntityType`.

```bash
./gsvtk terra rerun-show --step 01 --entity ROW --image KEY=REG/PATH:TAG \
    | checks/body_golden_diff.py --capture <untracked-capture.json>
checks/body_golden_diff.py --body body.json --capture <untracked-capture.json>
checks/body_golden_diff.py --selftest
```

- `--body` is the body to audit (`-`, stdin, by default). `terra rerun-show` prints the same dict
  `create` sends.
- `--capture` must be a path to a file this repo does not track. A tracked capture is refused (exit 3)
  because a live config's values are private coordinates.
- `--min-coverage F` (default 0.90): if the shared keys cover less than this fraction of the larger key
  set, the diff is refused as vacuous (exit 7) instead of reported as agreement.
- Values are printed as hashes. `--show-values` prints literals; do not paste that output anywhere
  public.
- `--max-list N` limits findings per bucket (default 20); the summary counts are never truncated.

Exit codes: 0 agreement and both assertions hold; 1 differences or a failed assertion; 2 usage, unreadable or not JSON; 3 capture is tracked; 4 an empty
or absent inputs/outputs map; 5 no usable `_gsvtk_capture` provenance block; 6 `rootEntityType` differs
or is missing on one side; 7 coverage too low.

`scripts/golden_configs.py` answers a different question: whether this repo's own config table moved,
against a checked-in capture it can regenerate.

## `cost_estimate.py`

Prices a finished launch from its expanded Cromwell metadata.

```bash
checks/cost_estimate.py --metadata md.json --rates prices/us-central1.json
checks/cost_estimate.py --selftest
```

- The rates file must carry `source` and `as_of`, and at least one of `vcpu_hour`, `vcpu_hour_spot`,
  `ram_gb_hour`. `prices/us-central1.json` is an example; it quotes an n1-standard machine rate per vCPU
  with RAM bundled (`bundled_ram_gb_per_vcpu`).
- Cost is `cpu` × wall time per call. Cromwell metadata carries no RAM or machine type, so unless the
  rate bundles memory the total is reported as a floor.
- A call with no `cpu` or no start/end is reported as `UNKNOWN`, never summed as zero.
- The tree's completeness comes from the markers `terra/batch_save_metadata.py` writes. A truncated tree
  prints vCPU-hours as a floor and no dollar figure. A dump with no marker prints `tree: NO MARKER` and
  is still priced, as a possible floor.

Exit codes: 0 priced; 2 arguments missing; 4 the rates file cannot name its source or has no price, or
the tree is truncated.

## `failure_cause.py`

Classifies why a Cromwell workflow failed: a task that exited nonzero (fix the inputs, image or paths)
or the cloud (preemption, a Batch job that vanished, a transient Batch failure; retry or change the
failure policy).

```bash
checks/failure_cause.py --metadata md.json
checks/failure_cause.py --selftest
```

The metadata must be expanded (`terra/batch_save_metadata.py`, or `expand_sub_workflows=True`);
otherwise sub-workflow tasks are missing and the tool reports `NOTHING GRADED`.

Exit codes: 0 infrastructure only (`INFRASTRUCTURE`); 3 at least one task exited nonzero
(`BODY IMPLICATED`, listing up to eight); 4 nothing to grade (unexpanded or a success document);
2 no `--metadata`.

## `sigpipe_pair.sh`

Demonstrates that a SIGPIPE guard works by running an unguarded and a guarded pipeline over the same
producer. Under `set -o pipefail`, `producer | head -n 1` exits 141 when the producer is killed by
SIGPIPE. The pair passes only if the unguarded arm exits 141 and the guarded arm exits 0. The file is
not executable; run it through bash:

```bash
bash checks/sigpipe_pair.sh
```

Pass `--selftest` to also run a mutant where both arms are guarded, which must be refused.

| Output | Exit | Meaning |
|---|---|---|
| `PAIRED` | 0 | the guard is evidenced |
| `GUARD DOES NOT WORK` | 1 | the unguarded arm failed and the guarded arm did too |
| `VACUOUS` | 4 | the unguarded arm did not exit 141, so nothing was tested |

`SIGPIPE_PAIR_LINES` sets the producer length (default 20000). A process that inherits SIGPIPE as
ignored (GitHub Actions does this) cannot die of a closed pipe, so the script re-executes itself once
under perl with SIGPIPE reset to the default. Without perl that case is `VACUOUS`.

macOS ships bash 3.2, which has no `pipefail`, so a `PIPEFAIL` finding from `wdl_semantics.py` may not
reproduce on a Mac shell. Use this script, or `image-check/run_in_image.sh` with the real task command,
to show a fix works.

## `image-check/`

A checkout can be checked statically; a shipped image has to be asked. Each launcher boots one
throwaway GCE VM in `GSVTK_PROJECT`, pulls the image with the instance's own credentials, runs
something, and streams results to the serial console. These cost money. Through `gsvtk` they require
`--confirm` and are refused when `GSVTK_READ_ONLY=1`:

```bash
./gsvtk check image run_in_image --dry-run --image REF --probe my_probe.sh     # free: plan only
./gsvtk check image svshell_image_check --image REF --confirm
./gsvtk check image jar_flag_probe --image REF --confirm
```

| Launcher | Answers | Default ceiling |
|---|---|---|
| `run_in_image.sh` | what a script prints and returns inside the image | 25 min (`--timeout MIN`) |
| `svshell_image_check.sh` | whether the image carries the `sv_shell` driver and fixture you tested, and whether the jq plumbing scan is clean on those bytes | 40 min (`--timeout MIN`) |
| `jar_flag_probe.sh` | which `GenotypeSVs` flags the image's `/opt/gatk.jar` accepts | fixed |

### `--image` screening

`--image` is required by all three. The reference is written into a startup script that runs as root on
the VM, so each launcher screens it with the shared `checks/image-check/image_ref.sh` before creating
anything, and exits 2 with a reason for: whitespace (two log columns joined with a space, the usual
cause of `rc=125 ... not found`), a URI scheme, `=`, a list separator, a leading `-`, a doubled or
dangling separator, an unexpanded glob, an uppercase repository name, or any character outside
`a-zA-Z0-9._/:@+-`. A branch build's reference is
`$(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6>`.

### VM lifecycle

The VM stays up after printing its result marker, and the driver deletes it only after reading the
serial log, because GCE serial output is unreadable once an instance is terminated. `--keep` leaves it
running and prints the delete command.

- `svshell_image_check.sh` and `jar_flag_probe.sh` delete the VM on Ctrl-C or `TERM` too (unless
  `--keep`). `run_in_image.sh` does not: on interrupt it prints the serial-log and delete commands.
- A run with no result marker before the ceiling keeps the VM, so the log can still be read, and prints
  the delete command.
- If a delete fails, the script prints `CLEANUP FAILED -- this VM may still be running` and the exact
  `gcloud compute instances delete` to run. The scripts only delete instances they created.

### `run_in_image.sh`

Runs your script inside an image you name and reports its exit code and a bounded stdout.

```bash
bash checks/image-check/run_in_image.sh --dry-run \
    --image $(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6> \
    --probe scripts/test/test_sigpipe.sh
```

- `--probe SCRIPT` is fed on stdin to `--entrypoint <shell>` (`--shell`, default bash), so no bind mount
  or build context is needed.
- `--overlay FILE` appends Dockerfile lines after a generated `FROM <REF>`; the layer is built in the VM
  and the probe runs in it. A `FROM` in the overlay is refused. `--run 'CMD'` is shorthand for one `RUN`
  line (repeatable). `--copy LOCAL[:REMOTE]` adds a file to the build context and requires an overlay.
- `--mount HOST:CTR` (repeatable) bind-mounts a VM host directory into the container.
  `--host-pre SCRIPT` runs a bash script on the VM host, not in the image, after the pull and before
  the probe (for example, to stage inputs into a mounted directory); if it fails, the result is
  `RUN_IN_IMAGE=STAGE_FAILED`, exit 5, and the probe never ran. `--host-post SCRIPT` runs on the host
  after the probe, sees `RUN_IN_IMAGE_RC`, and its output and `HOST_POST_RC=<n>` print before the
  verdict. `replay/task_replay.py` and `replay/svshell_run.py` use these
  ([local-replay.md](local-replay.md#replay-one-task-on-a-vm)). Without them, nothing changes.
- VM placement: `--project`, `--zone`, `--machine`, `--disk`, `--vm-name`. `--max-lines N` bounds the
  printed output (default 60).
- `--dry-run` prints the instance it would create and the assembled startup script, validates the script
  (`bash -n`, marker present), and changes nothing. It needs no `GSVTK_PROJECT`. Run it before spending a
  boot.

Refused before any VM exists: no `--image` (exit 2, with how to derive one from `IMAGE_REPO`), a bad
reference (above), and an empty, whitespace-only or comment-only probe or overlay. Under `--dry-run` a
valid probe prints its byte count and its non-comment byte count.

Exit codes: 0 the probe returned 0; 1 the probe returned nonzero (its rc is printed); 2 usage or a
refused input; 3 no result marker before `--timeout` (VM kept); 4 a required configuration key is unset;
5 not scored: the image could not be pulled in the VM (`IMAGE_ABSENT`), the overlay did not build, the
marker was unreadable, `--host-pre` failed (`STAGE_FAILED`), or a nonzero rc came with no output
(usually docker's own 125/126/127). A
not-scored arm is not a result; don't record it as "no difference". For a private registry the VM's
service account needs read access ([troubleshooting.md](troubleshooting.md)).

### `svshell_image_check.sh`

```bash
checks/image-check/svshell_image_check.sh --image "$(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6>" \
    --expect-driver-md5 <md5> --expect-fixture-md5 <md5>
```

Extracts `/opt/sv_shell` with `docker create` + `docker cp`, md5s the driver and the shipped fixture,
compares them with the expected values, and runs `svshell_jq_plumbing_scan.py --tree` on the extracted
tree. Compute the expected md5s from the ref you tested:

```bash
git -C <gatk-sv> show <ref>:src/sv_shell/single_sample_pipeline.sh | md5sum
```

| Result | Exit | Meaning |
|---|---|---|
| `PROVEN` | 0 | both md5s were supplied and matched, and the scan is clean |
| `SCAN_CLEAN` | 0 | the scan is clean, but byte identity is not proven because both md5s were not supplied |
| `FAILURE` | 1 | the scan or an md5 said no |
| `TIMEOUT` | 3 | no marker before the ceiling; the VM is kept |

Only `PROVEN` is evidence that the image carries the bytes you tested.

### `jar_flag_probe.sh`

```bash
checks/image-check/jar_flag_probe.sh --image <ref>
```

Runs the image's `/opt/gatk.jar` and reports whether `GenotypeSVs` accepts the RD flags the `sv_shell`
driver passes (`--rd-depth-table`, `--rd-pesr-table`). The image's jar is built from a pinned GATK
commit, which may predate a flag your branch relies on. `JAR_FLAG_PROBE=DONE` means there was help text
to grade. `JAR_FLAG_PROBE=IMAGE_ABSENT` (the image did not pull) and `JAR_FLAG_PROBE=NO_HELP` (it pulled
but produced nothing gradable) are not findings about the jar; the driver stops on either and exits 5.
Takes `--project`, `--zone`, `--machine`; no `--timeout`.

## `make audit`

Scans the files that would be published (`git ls-files`) for private values.

```bash
make audit                       # summary
make audit V=1                   # every literal with its key and provenance
./scripts/audit.py --selftest
```

What it looks for:

- **Shapes**: patterns for classes of private value, independent of the machine (service-account
  addresses, project numbers used as such, `fc-` handles, private keys, API keys and tokens, machine-local
  home paths). CI grades these.
- **Personal values**: this machine's coordinates, from `kit/gsvtk-config show` minus anything that
  resolves the same with no profile, plus `audit.local.txt`.
- **Values from history**: coordinate values found in earlier versions of the config-bearing files
  (`testkit.env`, `testkit.local.env`, `testkit.env.example`, `profiles/*.json`, and the captured bodies
  under `scripts/selftest.d/golden/`), read with `git show` so nothing is modified. This catches a
  workspace or bucket you used once and moved off. A value that equals a shipped default, or that the
  tracked config still carries at HEAD, is treated as public.

Each tier prints how many values it graded, and says so when it found nothing to derive. With
`--no-git` the history tier is skipped, and the run says so.

`audit.local.txt` (gitignored; copy `audit.local.example`) holds extra literals, `re:` regexes, and
`waive <value>` lines that declare a derived value public. Fix a hit by replacing the value with a
placeholder such as `<your-old-workspace>`, not by waiving it.

Two sources are out of reach: values supplied only through `GSVTK_CONFIG` or
`~/.config/gatk-sv-testkit/env` (their history is on the machine, not in the repo), and names that were
only ever typed into prose.

Exit codes: 0 clean, 1 hits, 2 could not run.

## `make audit-history`

`make audit` checks the files as they are now. A scrubbed file's old blob is still reachable from the
branch, still sent on push, and still visible on GitHub. This check grades every blob and every commit
message, and classifies each hit:

| Class | Meaning | Verdict |
|---|---|---|
| `HEAD` | current content of a tracked path | fails |
| `STAGED` | in the index, not yet committed | fails |
| `HISTORY` | reachable from a ref, not at HEAD | reported; fails with `PUBLISH=1` |
| `DANGLING` | in the object store, reachable from no ref | advisory (a push does not send it); `--dangling-fails` makes it fail |

```bash
make audit-history                 # HEAD and STAGED must be clean
make audit-history PUBLISH=1       # run before git push: HISTORY must be clean too
make audit-history V=1             # print up to 200 findings
./scripts/audit_history.py --selftest
```

Direct options: `--root`, `--show N` (default 25; all are counted), `--pattern-file PATH`,
`--shapes-only` (skip this machine's coordinates; needs no kit config), `--dangling-fails`, `--publish`.

The detectors are shared with `audit.py`. Findings are masked: the rule, the length and some context,
never the whole value. Blobs up to 8,000,000 bytes are read (non-UTF-8 bytes are replaced, not skipped);
larger blobs are counted and named on a `PARTIAL:` line, and any such blob fails the run
(`MAX_OVERSIZED_BLOBS = 0`).

A `HISTORY` hit can only be removed by rewriting history (for example `git filter-repo --replace-text`)
before the commits are pushed; a copy already pushed cannot be recalled.

Exit codes: 0 clean, 1 hits in a failing class.

## Using them as gates

Before submitting, cheapest first:

```bash
checks/wdl_gate.sh --strict HEAD                         # seconds to a minute
checks/svshell_contract_check.py --strict                # seconds
checks/svshell_jq_plumbing_scan.py --compare-to main     # needs jq
checks/terra_entity_check.py --ref HEAD                  # if you touched launch configs
make audit-history PUBLISH=1                             # before git push
```

`./gsvtk check HEAD --strict` runs the first three in one command (there `--strict` applies to
`wdl_gate.sh` only). `make test` runs the offline
selftests of these tools, not the full-tree scans. When comparing selftest tallies across machines,
compare declared counts: verdict counts depend on which prerequisites (a gatk-sv clone, miniwdl, jq) the
machine has, and a missing one turns legs into named skips.
