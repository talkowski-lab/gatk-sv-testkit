# The `gsvtk` command line

One program, at the repo root, that dispatches to the tools in this kit. It is the same door the
agent skill uses, so a person at a terminal and an agent in a session meet one set of mode names,
one exit-code table, and one read-only rule.

```bash
./gsvtk --help                          # every subcommand, and the exit-code table
./gsvtk tools                           # which of the four loops can run HERE right now
./gsvtk doctor --redact                 # what is configured, with the coordinates hidden
cd "$(./gsvtk repo)" && ./kit/gsvtk-config show      # the values themselves — terminal only
```

`--help` is honest with no configuration at all: the gate runs it with `GSVTK_CONFIG` pointing at an
empty file and no credentials, so a path that resolves configuration on the way to printing usage is
a bug this sweep catches. No-args prints the usage and exits 2; `--help` is the only path that prints
it and exits 0.

**This does not replace the scripts.** Every tool is still directly runnable, keeps its own `--help`,
its own flags and its own gates, and the README's tool reference lists them by file. The CLI is a
router plus one outer safety gate; `docs/*` still document the tools, because the tools are the
authority on their own flags.

## The read-only contract

Three rules, and the order between them is the point:

1. **`GSVTK_READ_ONLY=1`, or `--read-only` before the command, refuses every mode that POSTs, boots
   compute, or bulk-downloads.** The refusal happens *before* any dependency, credential or config
   probe, so a refusal can never be mistaken for a broken environment: exit 2, the mode named, and the
   exact command printed for a human to run in the checkout. `scripts/check_skill.py` grades that
   ordering, and `scripts/selftest.d/cli.sh` runs every one of those refusals against an interpreter
   that certainly cannot import `firecloud` — plus three positive controls proving a read-only
   dispatch still works under the same stripped environment. Without the controls, 17 refusals are
   also what a CLI that cannot parse its own arguments prints.
2. **A mutating mode needs `--confirm`, and says what it will do first**: what gets created, where it
   lands (the resolved workspace or registry path, or the key whose absence will make the tool exit 4),
   what it costs, and how to undo it. Nothing in this repo quotes a price — there is no rate lookup
   here, because your billing account, discounts and spot pricing are not visible from a git checkout
   — so cost is named as machine type, object size or VM-minutes, and you multiply by your own rate.
3. **The outer gate is additive.** `--confirm` is forwarded only to a tool that documents that flag
   itself; a tool that gates on `--write` still needs `--write` typed out, and
   `--allow-shared-target` is the tool's own guard, never this one's. Plan modes stay free everywhere:
   `--check`, `--dry-run` and a `fetch --verify` (which re-hashes what is already on disk) transfer
   nothing, so they dispatch under `GSVTK_READ_ONLY=1` without `--confirm`.

`make test` runs the offline assertions behind all of this: `bash
scripts/selftest.d/cli.sh .venv/bin/python` prints the suite's own tally (108 assertions) and every
mode below that is named there is checked reaching the file it claims to reach, against stand-in
scripts that echo the resolved command line.

| exit | means | do |
|---|---|---|
| 0 | clean | proceed |
| 1 | findings, or a real failure | read the lines: `checks/` and `compare/` print findings that are not verdicts |
| 2 | usage error, or a refused mode | fix the invocation, not the job |
| 3 | missing dependency, named with its fix (`gsvtk replay preflight` is the whole story for replay) | install it, or run the mode that does not need it |
| 4 | missing required config | the resolver's code: it names the key and the file to edit. Never invent a value |

## Command → tool

| You type | It runs | Free? |
|---|---|---|
| `gsvtk locate` / `tools` / `doctor [--redact]` / `repo` / `version` | the config layer and `command -v`, nothing else | free |
| `gsvtk check [<ref>] [--wf NAME]... [--strict] [--repo DIR] [--compare-to REF] [--semantics] [--reach] [--images]` | `checks/wdl_gate.sh` — which now also runs `checks/wdl_inputs_check.py`, the required-input half CI asks, so an exported `WOMTOOL_JAR` is honoured by the gate and not only by terra tooling — `checks/svshell_contract_check.py`, `checks/svshell_jq_plumbing_scan.py`, and with the two opt-in flags `checks/wdl_semantics.py` and `checks/wdl_reach.py` | free, offline. `--reach` needs at least one `--wf NAME`, which IS the target: `wdl_reach` has no default target, so `--reach` alone is refused as a usage error before any checker runs. Every name goes to `checks/wdl_reach.py` as one repeated `--target` in ONE run, because the tree load (~35 s on a gatk-sv tree) is the whole cost and one process per name paid it N times: you get one `tree:` provenance line and one answer block per name, in the order you listed them. A name the tree does not know is reported by name with its own closest matches, and the run exits nonzero without swallowing the names that did answer. With no `--repo`, the tree both opt-in checkers scan is `GSVTK_GATK_SV_CHECKOUT` from the resolver -- the same source `svshell_contract_check.py` already used |
| `gsvtk check image <run_in_image\|svshell_image_check\|jar_flag_probe> ...` | `checks/image-check/*.sh` | **boots a GCE VM**: `--dry-run` is free, the probe needs `--confirm` |
| `gsvtk entity [--repo DIR --ref REV \| --tree DIR] [--corpus PATH]` | `checks/terra_entity_check.py` — which entity row each launch config runs against, derived from the config's own `${this.<etype>_id}` and cross-checked against the entity tables that ref ships. Every run also prints `GSVTK-ENTITY-UNCHECKED`: the member attributes, `${workspace.*}` bindings and live `rootEntityType` it deliberately did not evaluate | read-only — it reads a ref with `git archive`, or a directory you name. It never opens a workspace, so `--read-only` changes nothing about it |
| `gsvtk build <branch> [image ...]` | `docker/gatk-sv-build.sh --check` then `--dry-run` | free — this default *is* the preview |
| `gsvtk build <branch> [image ...] --confirm` | `docker/gatk-sv-build.sh <branch> [image ...]` | **VM + registry push** |
| `gsvtk terra recon\|show\|check\|plan\|verify\|status\|cost\|peek\|inputs\|save-metadata\|rerun-show` | `terra/recon.py`, `batch_configs.py show/check`, `batch_freeze.py plan/verify`, `batch_status.py`, `batch_cost.py`, `batch_peek.py`, `batch_check_inputs.py`, `batch_save_metadata.py`, `batch_rerun_step.py show` | read-only |
| `gsvtk terra configs-create\|configs-validate` | `terra/batch_configs.py create\|validate` | **POSTs** / live Terra call |
| `gsvtk terra freeze-copy --write` / `freeze-attrs --write` | `terra/batch_freeze.py copy\|attrs` | **tens of GiB**, entity attributes |
| `gsvtk terra rerun-create\|rerun-validate\|rerun-submit` | `terra/batch_rerun_step.py create\|validate\|submit` | **a fleet of VMs** |
| `gsvtk terra fetch` / `fetch-compare` | `terra/fetch_outputs.py`, `terra/batch_fetch_compare.sh` | **bulk download**, then local compare |
| `gsvtk compare <name> [args...]` | `compare/*.py` — the one called `<name>.py`, arguments forwarded verbatim (`compare --list` prints the names) | free, local |
| `gsvtk replay inputs` | `replay/build_inputs.py` | free, local |
| `gsvtk replay train-chr20\|train-full\|train-definitive\|rd-population-probe\|reference-run\|het-population` | the matching `examples/` driver, after the preflight below | free, local |
| `gsvtk replay preflight [mode]` | measures java major, the GATK jar, `bcftools`, and free disk | free |

Three name rules, all deliberate:

* **Flags are dispatched, not forwarded.** `--wf`/`--strict` belong to `wdl_gate.sh`, `--repo` to the
  python checkers, `--compare-to` to the jq plumbing scan; handing one tool another tool's flag is an
  argparse error that reads like a broken checker. `check` refuses an unknown flag instead.
* **A mode that would be ambiguous carries its tool.** `create` and `validate` exist in both
  `batch_configs.py` and `batch_rerun_step.py`, so `gsvtk terra create` is refused as ambiguous: a
  config POSTed by the other tool is read by the wrong submission. `copy`, `submit` and `attrs` are
  unambiguous, so they are accepted as short forms of `freeze-copy`, `rerun-submit` and
  `freeze-attrs`.
* **A command that could point at two trees points at one.** `entity --tree DIR --ref REV` is refused as a usage
  error rather than resolved by precedence. Its output is a census — counts claimed *about one tree* — and a
  silently winning `--ref` would print confident numbers about a commit nobody asked to grade. Nor does the CLI
  invent a tree when you pass neither: `check --semantics` asks the resolver because a scan with no tree answers
  nothing, while `entity` lets the tool ask that same resolver itself and print a named exit-3 prerequisite. One
  precedence chain (`kit/gsvtk-config`), one place that owns it.

## What the pre-flight print looks like

All three blocks below were run in a checkout with `GSVTK_CONFIG` pointing at an empty file, so the
target shows up as a missing key rather than a value. That is the point of the print: it says where
the write goes, or says it cannot know yet and names the key. The only edit to any of them is the
checkout path (`<checkout>` — see the legend after the blocks).

A mutating mode with no `--confirm` refuses, and does not dispatch anything:

```
$ ./gsvtk terra rerun-submit
gsvtk: refusing "terra rerun-submit" — it starts a submission: a batch of VMs, real money, and mutating modes need --confirm.
       Nothing was created, POSTed, booted or downloaded.
       Ask the free version of the same question first: show / plan / check / --dry-run
       print the target and the object count without writing anything.
       Run it when you mean it:
         gsvtk terra rerun-submit --confirm
       (the tool's own gate still applies: --write, --allow-shared-target, and whatever
        preflight it prints before it POSTs. This gate does not replace those.)
```

The same mode under `GSVTK_READ_ONLY=1` (which is what the agent skill always sets) refuses earlier
and says plainly that nothing was even looked up — no dependency, no credential, no config:

```
$ GSVTK_READ_ONLY=1 ./gsvtk terra rerun-submit
gsvtk: REFUSED — terra rerun-submit POSTs, boots compute, or bulk-downloads, and this run is read-only.
       starts a submission: a batch of VMs, real money
       Nothing was submitted, downloaded or created, and no dependency, credential or
       config value was consulted: this is a policy refusal, not a broken environment.
       Read-only is the default because one whole-chain rerun is a fleet of VMs and Terra
       has no per-workspace budget cap (CONTRIBUTING: "Mutating vs read-only").
       To do it, step out of read-only and run it yourself, in the checkout:
         cd <checkout> && python terra/batch_rerun_step.py submit
       (or drop GSVTK_READ_ONLY / the --read-only flag and add --confirm: the outer gate
        still wants the write spelled out before it acts.)
```

With `--confirm` it prints the four things and then execs the tool — which resolves the target itself
and exits 4 naming the key, because the outer gate does not resolve a target on a tool's behalf and
does not get to:

```
$ ./gsvtk terra configs-create --confirm
gsvtk: MUTATING mode — what happens next, before it happens:
  what   the shipped method configs, POSTed into Terra (overwrite, not append)
  where  Terra (unset — GSVTK_TERRA_NAMESPACE / GSVTK_TERRA_WORKSPACE: the tool exits 4 naming them) — the tools refuse the SHARED baseline workspace unless --allow-shared-target
  cost   no VM cost; the cost is a config a later submission silently reads, and Terra keeps no history of the body it replaced
  undo   there is no undo in Terra: print the current bodies FIRST (gsvtk terra show / gsvtk terra rerun-show) and re-POST those, or recreate from the branch you meant
repo  <checkout> @ 9d7f60c +dirty
gsvtk-config: GSVTK_TERRA_NAMESPACE is not set (Terra workspace namespace of your sandbox).
  set it in the environment, or put `GSVTK_TERRA_NAMESPACE=...` in one of:
    /tmp/empty.env
  see docs/config.md
```

| Placeholder | What it is | Where the real value lives |
|---|---|---|
| `<checkout>` | the checkout the command ran in | `./gsvtk repo`, or `scripts/gsvtk repo` from the skill |
| `9d7f60c` | the commit the stamp reported | `git -C "$(./gsvtk repo)" rev-parse --short HEAD` |

Every mutating mode's four lines are written from the resolved configuration, so on a configured
machine the `where` line names your workspace pair (or `IMAGE_REPO` for a build) rather than the keys
that are missing. Cost is always VM-minutes, object size or "no money", never a price.

## What the CLI does not cover

Deliberately, so the direct form stays the documented one:

* `terra/fetch_baseline.py`, `terra/stage_inputs.py`, `terra/wdl_flat.py` — per-invocation utilities
  with their own flag surfaces; they are in the README's tool reference and run directly.
* `compare/make_fixtures.py` (it builds the gate's fixtures, it is not a comparator) and
  `compare/artifact.py` (a reader module; its CLI is `artifact_tally.py`). Both refuse by name.
* `scripts/*` (the gate's own tooling), `kit/*` (the config layer), and the rest of `checks/image-check`
  beyond the three named probes.
* Every flag of every underlying tool. `gsvtk <subcommand> --help` is the CLI's surface; the tool's
  `--help` is the authority on its own.

## From an agent session

`.pi/skills/gatk-sv-testkit/` ships the skill, and its `scripts/gsvtk` is now a thin shim: it locates
the checkout, requires that the checkout is a git clone of an origin named in `GSVTK_TRUSTED_REPOS`,
stamps its own version, and execs this program with `GSVTK_READ_ONLY=1`. So the trust gate and the
read-only stamp live in the skill, and every mode rule lives here, once. `scripts/check_skill.py` is
what keeps the two honest: it parses the read-only whitelist out of `./gsvtk` and executes each mode
SKILL.md claims is refused, and it fails if the shim stops setting the flag or grows a dispatcher of
its own again.

## See also

* [config.md](config.md) — the keys, the two with no default, and the per-invocation variables
* [setup.md](setup.md) — dependencies and the skill
* [static-checks.md](static-checks.md), [terra-head-to-head.md](terra-head-to-head.md),
  [local-replay.md](local-replay.md), [comparators.md](comparators.md) — the loops, in depth, per tool
