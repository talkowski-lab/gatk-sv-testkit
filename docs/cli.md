# The `gsvtk` command line

`./gsvtk` at the repo root is the entry point for the kit. It dispatches to the scripts in `checks/`,
`docker/`, `terra/`, `compare/`, `replay/` and `examples/`, and adds one outer safety gate. The agent
skill runs the same program in read-only mode, so people and agents share one set of mode names and exit
codes.

```bash
./gsvtk --help                    # every subcommand and the exit codes
./gsvtk tools                     # which loops can run on this machine, and where each dependency was found
./gsvtk doctor --redact           # what is configured, with values hidden
./kit/gsvtk-config show           # the values themselves (keep them out of issues and transcripts)
```

`--help` works with no configuration at all. Running `./gsvtk` with no arguments prints the usage and
exits 2.

The CLI does not replace the scripts. Each tool stays directly runnable, keeps its own `--help`, flags
and gates, and its `--help` is the authority on its flags.

## Exit codes

| exit | meaning | what to do |
|---|---|---|
| 0 | clean | proceed |
| 1 | findings, or a real failure | read the output; `checks/` and `compare/` print findings, which are not verdicts on their own |
| 2 | usage error, or a refused mode | fix the invocation |
| 3 | missing dependency, named with its fix | install it, or use a mode that does not need it (`./gsvtk replay preflight` lists what replay needs) |
| 4 | missing required config | the message names the key and the file to set it in |

Comparators under `compare/` use 0 for no differences, 1 for differences, and 2 when there was nothing
to compare.

## The read-only contract

1. **`GSVTK_READ_ONLY=1`, or `--read-only` before the command, refuses every mode that POSTs to Terra,
   boots compute or bulk-downloads.** The refusal happens before any dependency, credential or config
   check, so it cannot be mistaken for a broken environment. It exits 2, names the mode, and prints the
   command a person would run in the checkout.
2. **A mutating mode needs `--confirm`**, and prints four things before it acts: what gets created,
   where (the resolved workspace or registry path, or the missing key that will make the tool exit 4),
   what it costs, and how to undo it. Cost is given as machine type, VM-minutes or object size, never as
   a price.
3. **The outer gate adds to the tool's own gates.** `--confirm` is forwarded only to tools that accept
   it. A tool that wants `--write` or `--allow-shared-target` still needs it typed. Plan modes
   (`--check`, `--dry-run`, `fetch --verify`, `fetch-compare table`) transfer nothing, so they run under
   read-only without `--confirm`.

A mutating mode without `--confirm`:

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

Under `GSVTK_READ_ONLY=1` the same command is refused earlier, with `REFUSED` and the command to run by
hand in the checkout. With `--confirm`, the CLI prints the what / where / cost / undo block and then runs
the tool, which resolves its own target:

```
$ ./gsvtk terra configs-create --confirm
gsvtk: MUTATING mode — what happens next, before it happens:
  what   the shipped method configs, POSTed into Terra (overwrite, not append)
  where  Terra (unset — GSVTK_TERRA_NAMESPACE / GSVTK_TERRA_WORKSPACE: the tool exits 4 naming them) — the tools refuse the SHARED baseline workspace unless --allow-shared-target
  cost   no VM cost; the cost is a config a later submission silently reads, and Terra keeps no history of the body it replaced
  undo   there is no undo in Terra: print the current bodies FIRST (gsvtk terra show / gsvtk terra rerun-show) and re-POST those, or recreate from the branch you meant
...
gsvtk-config: GSVTK_TERRA_NAMESPACE is not set (Terra workspace namespace of your sandbox).
```

On a configured machine the `where` line names your workspace, or the registry path for a build.

## Commands

| You type | It runs | Cost |
|---|---|---|
| `gsvtk locate` / `tools` / `doctor [--redact]` / `repo` / `version` | the config layer and `command -v` | free |
| `gsvtk check [<ref>] [--wf NAME]... [--strict] [--repo DIR] [--compare-to REF]` | `checks/wdl_gate.sh` (which also runs `checks/wdl_inputs_check.py`), `checks/svshell_contract_check.py`, `checks/svshell_jq_plumbing_scan.py` | free, offline |
| `gsvtk check ... --semantics` | adds `checks/wdl_semantics.py` over the tree | free, slower |
| `gsvtk check --reach --wf NAME [--wf NAME]... [--images]` | `checks/wdl_reach.py`, one run answering every named workflow | free; `--reach` needs at least one `--wf` |
| `gsvtk check image run_in_image\|svshell_image_check\|jar_flag_probe ...` | `checks/image-check/*.sh` | boots a VM: `--dry-run` is free, the real probe needs `--confirm` |
| `gsvtk entity [--repo DIR --ref REV \| --tree DIR] [--corpus PATH]` | `checks/terra_entity_check.py`: which entity row each Terra launch config runs against, and whether the shipped entity tables carry what it reads | free, offline (reads a ref with `git archive`) |
| `gsvtk entity --against-dump FILE` | `checks/terra_entity_check.py --against-dump`: the derived entity type against Terra's `rootEntityType`, from a `terra dump-configs` file | free, offline |
| `gsvtk compat [--ref REF] [--role branch\|baseline] [--record]` | `scripts/check_compat.py check`: whether this kit is compatible with a gatk-sv ref ([compatibility.md](compatibility.md)); `compat table` prints the README table | free, offline (reads a ref with git); `--record` writes `compat.json` |
| `gsvtk publish ...` / `gsvtk publish-check <branch>` | `checks/version_resolves.py`: does the Dockstore version a rerun names resolve, and is it still the branch tip | free; `publish-check` makes read-only GETs to Dockstore and `git ls-remote` |
| `gsvtk build <branch> [image ...]` | `docker/gatk-sv-build.sh --check`, then `--dry-run` | free preview |
| `gsvtk build <branch> [image ...] --confirm` | `docker/gatk-sv-build.sh <branch> [image ...]` | VM and registry push |
| `gsvtk terra recon\|show\|check\|plan\|verify\|status\|cost\|peek\|inputs\|save-metadata\|rerun-show` | `terra/recon.py`, `batch_configs.py show/check`, `batch_freeze.py plan/verify`, `batch_status.py`, `batch_cost.py`, `batch_peek.py`, `batch_check_inputs.py`, `batch_save_metadata.py`, `batch_rerun_step.py show` | read-only |
| `gsvtk terra dump-configs [--ns NS --ws WS] [--out FILE]` | `terra/dump_configs.py`: every method config in one workspace, full payloads, to one file | read-only (GETs) |
| `gsvtk terra method-plan <Workflow> [--ref REF]` | `terra/method_upload.py <Workflow> plan`: the flattened WDL at a ref and the exact upload request | free, sends nothing |
| `gsvtk terra method-upload <Workflow> [--ref REF] --confirm` | `terra/method_upload.py <Workflow> upload`: a private method-repository snapshot a rerun can name with `--method agora://NS/NAME/N` | writes a method snapshot, no compute |
| `gsvtk terra configs-create\|configs-validate` | `terra/batch_configs.py create\|validate` | POSTs to Terra |
| `gsvtk terra freeze-copy\|freeze-attrs` | `terra/batch_freeze.py copy\|attrs` (each still needs `--write`) | tens of GiB copied; entity attributes written |
| `gsvtk terra rerun-create\|rerun-validate\|rerun-submit` | `terra/batch_rerun_step.py create\|validate\|submit` | a batch of VMs |
| `gsvtk terra abort --submission <full id>` | `terra/abort_submission.py` | stops a running submission |
| `gsvtk terra fetch` / `fetch-compare` | `terra/fetch_outputs.py` (including `--resolve-cached`), `terra/batch_fetch_compare.sh` | bulk download, then a local compare; `--dry-run` is free |
| `gsvtk compare <name> [args...]` | the matching script in `compare/`, arguments passed through (`compare --list` prints the names) | free, local |
| `gsvtk replay inputs` | `replay/build_inputs.py` | free, local |
| `gsvtk replay train-chr20\|train-full\|train-definitive\|rd-population-probe\|reference-run\|het-population` | the matching `examples/` driver, after a preflight | free, local |
| `gsvtk replay preflight [mode]` | checks the Java version, the GATK jar, `bcftools` and free disk | free |
| `gsvtk replay task ...` / `gsvtk replay svshell ...` | `replay/task_replay.py` (one finished task, rerun in an image) / `replay/svshell_run.py` (sv-shell end to end, then a VCF compare) | free plan; `--confirm` boots a VM (`svshell` also uploads to `--out-uri`) |

Rules worth knowing:

- **`check` routes each flag to the tool that owns it.** `--wf` and `--strict` go to `wdl_gate.sh`,
  `--repo` to the Python checkers, `--compare-to` to the jq plumbing scan. An unknown flag is refused.
  With no `--repo`, the tree the checkers read is `GSVTK_GATK_SV_CHECKOUT`.
- **`build` takes only the branch, image names and `--confirm`.** Other `gatk-sv-build.sh` flags
  (`--persistent`, `--gatk`, `--image-tag`, ...) belong to the script; run it directly for those.
- **Ambiguous mode names are refused.** `create` and `validate` exist in both `batch_configs.py` and
  `batch_rerun_step.py`, so `gsvtk terra create` is refused; use `configs-create` or `rerun-create`.
  `copy`, `attrs` and `submit` are accepted as short forms of `freeze-copy`, `freeze-attrs` and
  `rerun-submit`.
- **`entity` reads one tree.** `--tree` and `--ref` together is a usage error.
- `terra profile` and a bare `terra attrs` (which only prints without `--write`) are refused by the
  CLI; run `python terra/batch_freeze.py attrs` in the checkout to see what would be written.

## Choosing the module: `--module NAME`

The module decides which step numbers exist. The step map comes from `profiles/<module>.json`; the
default module is `genotyping` (steps 06 to 10). `--module NAME`, placed before the command, exports
`GSVTK_MODULE` for the tool it runs, so `./gsvtk doctor --redact` reports that key with source `[env]`.
Setting `GSVTK_MODULE=single_sample` yourself does the same thing.

```
$ ./gsvtk terra rerun-show --step 01
unknown step '01': this map knows 06, 07, 08, 09, 10 (GenerateBatchMetrics, FilterBatchSites, FilterBatchSamples, MergeBatchSites, GenotypeBatch). ...

$ ./gsvtk --module single_sample terra rerun-show --step 01 --allow-unpinned-docker
[rerun] step 01  workflow GATKSVPipelineSingleSample  config 01-GATKSVPipelineSingleSample-rerun
        root entity sample  (row: UNRESOLVED: `entity_row` is --entity, so pass --entity <name>)
...
```

- An empty name (`--module ""`) is refused, so an unset shell variable cannot silently select the
  default module.
- An unknown name is refused with `no profile at <checkout>/profiles/<NAME>.json`.
- The same gates apply to every module. `rerun-show` refuses while `*_docker` inputs would come from
  the workspace unless you pass `--allow-unpinned-docker`. `rerun-submit --confirm` for single_sample
  also needs `--entity <sample row>`, because no config key holds that row.

`ls profiles/` lists the available modules. See [module-profiles.md](module-profiles.md).

## Image pins from a build: `--image-from`

`gsvtk terra rerun-create|rerun-validate|rerun-submit --step NN --image-from FILE` takes the step's
`*_docker` pins from a capture file written by `docker/image_capture.py` after a build. A successful
`docker/gatk-sv-build.sh` run, ephemeral or `--persistent`, writes one under
`$GSVTK_WORK/image-capture/`. It reads that one file and resolves nothing over the network.

Per key, the most specific source wins: `--image KEY=REF`, then `--image-from` (repeatable; the last
file to state a key wins), then the step's `images` map in the module profile, then the
`GSVTK_IMAGE_REPO` / `GSVTK_GATK_IMAGE_REPO` settings. `show` labels each pin with its source, and a
coverage line reports `image capture: N of M keys supplied by <file>`.

| exit | meaning |
|---|---|
| 2 | no file at that path |
| 3 | the file is not a usable capture: tracked by git, unreadable, wrong `kind` or `version`, empty `images`, or missing a key this step binds |

A key the capture lists as `unresolved` is reported by name and then judged by the normal pinned-image
guard. Capture refs must be digests; the capture cannot exempt itself from that guard. See
[docker-builds.md](docker-builds.md) for how captures are made.

## Interpreters

`terra/` needs `firecloud`; `compare/` needs `numpy`, `pandas` and `pysam`; most other tools need only
the standard library. The CLI picks an interpreter per purpose:

| purpose | order tried |
|---|---|
| `terra` modes | `GSVTK_TERRA_PY` (if it can import `firecloud`), `./.venv/bin/python`, `python3` |
| everything else | `GSVTK_PY`, `./.venv/bin/python`, `python3` |

If no interpreter can import `firecloud`, the `terra` modes exit with a message naming
`GSVTK_TERRA_PY`; the other modes are unaffected.

## Not covered by the CLI

Run these directly:

- `terra/fetch_baseline.py`, `terra/stage_inputs.py`, `terra/wdl_flat.py`
- `scripts/derive_profile.py` (profile fields derived from upstream; free, local)
- `checks/body_golden_diff.py`, `checks/failure_cause.py`, `checks/cost_estimate.py`
- `docker/image_capture.py`
- `scripts/*` (repo tooling) and `kit/*` (the config layer)

`compare/make_fixtures.py` and `compare/artifact.py` are not comparators and are refused by
`gsvtk compare`.

## From an agent session

The skill in `skills/gatk-sv-testkit/` tells an agent to run every command as `gsvtk --read-only ...`.
All mode rules live in `./gsvtk`. `scripts/check_skill.py` (run by `make test`) checks that every
command the skill gives carries `--read-only`, and that every mode it says is refused really is.
Installing the skill is in [setup.md](setup.md#the-agent-skill).

## See also

- [config.md](config.md): settings and per-invocation variables
- [setup.md](setup.md): dependencies and the agent skill
- [static-checks.md](static-checks.md), [terra-head-to-head.md](terra-head-to-head.md),
  [local-replay.md](local-replay.md), [comparators.md](comparators.md): each loop in depth
