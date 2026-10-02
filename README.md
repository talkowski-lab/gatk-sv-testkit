# gatk-sv-testkit

[![ci](https://github.com/talkowski-lab/gatk-sv-testkit/actions/workflows/ci.yml/badge.svg)](https://github.com/talkowski-lab/gatk-sv-testkit/actions/workflows/ci.yml)

A toolkit for developers working on [GATK-SV](https://github.com/broadinstitute/gatk-sv), a structural variant
discovery pipeline for short-read sequencing data.

A full GATK-SV run takes hours of cloud compute. Most of the mistakes that sink a run are cheap to catch if
you look before you launch. This kit helps you look first. Its tools check whether your change will start,
build images for your branch, rerun a single stage instead of the whole pipeline, and compare the outputs
of two runs.

## What it does

- Tells you in seconds, on your laptop, whether a change will run.
- Builds pipeline images on a cloud VM, so you don't need Docker locally.
- Reruns one stage instead of the whole pipeline.
- Runs the heavy stages locally, at no cloud cost.
- Compares two runs.

gatk-sv-testkit is an independent project. It follows GATK-SV's documented conventions for building images
and for the shared Terra workspace.

## Installation

You need Python 3.9 or later. Other requirements depend on which tools you use, and each tool tells you
what's missing.

```bash
git clone https://github.com/talkowski-lab/gatk-sv-testkit.git
cd gatk-sv-testkit
make setup                                              # creates ./.venv
.venv/bin/python -m pip install -r requirements-dev.txt  # extra tools the test suite needs
```

Then create your config and check it:

```bash
cp testkit.env.example testkit.env   # set your cloud project and Terra workspace
./kit/gsvtk-config doctor            # shows what's set, what's missing, and where each value came from
```

Two settings have no default: your cloud project and your Terra workspace. They decide who gets billed and
where things get written, so the tools won't guess them. `make help` lists the make targets, and every tool
accepts `--help`. The [setup guide](docs/setup.md) covers the full dependency list, cloud login, and the
agent skill.

## Usage

`./gsvtk` is the one command-line program for the kit:

```bash
./gsvtk --help                  # every subcommand, and the exit codes
./gsvtk tools                   # which loops can run on this machine, and where each dependency was found
./gsvtk check origin/main       # the static checks, in cost order
./gsvtk build my-branch         # the free preview: --check then --dry-run
./gsvtk terra show              # what the method configs would bind (read-only, offline)
./gsvtk compare table-diff ...  # the comparators, dispatched by name
./gsvtk replay preflight        # what blocks a local replay, measured not assumed
```

Nothing else changes: every tool is still a script you can run from the repo root, each keeps its own
`--help`, its own flags and its own gates, and the [tool
reference](#tool-reference) below is still the list of scripts. The CLI routes to them and adds one
outer gate: modes that POST, boot compute or bulk-download are refused under `GSVTK_READ_ONLY=1`
(or `--read-only`), and they need `--confirm` even when you are not read-only. The command-to-tool map
and that contract are in [docs/cli.md](docs/cli.md); the [Quickstart](docs/quickstart.md) walks through
each task with real output.

### Check a change before you run it

```bash
./gsvtk check origin/main                                    # all three checks, in cost order
python checks/svshell_jq_plumbing_scan.py --compare-to main  # just one, with its own flags
```

The CLI dispatches each flag to the tool that implements it (`--wf`/`--strict` to the WDL gate,
`--compare-to` to the plumbing scan) rather than handing every tool the whole command line.

These run in seconds on a laptop, with no data and no cloud access. They catch problems `miniwdl check`
misses. Compare against `main` so you only see what your change added, not problems already upstream.

### Build images for your branch

```bash
./gsvtk build my-branch                  # free: --check, then --dry-run, then how to do it for real
./gsvtk build my-branch --confirm        # billed: build on a temporary cloud VM, push, delete it
docker/gatk-sv-build.sh --check my-branch     # or drive the script directly, with its own flags
```

GATK-SV images target x86-64 Linux, so you can't practically build them on an Apple Silicon Mac. This
script builds them in your cloud project instead. The real build starts a VM right away, with no
confirmation prompt, so `./gsvtk build <branch>` runs `--check` and `--dry-run` and stops there; `--confirm` is the only
flag the CLI accepts here, and it prints project, zone, machine type, the resolved push target and the
undo before it boots anything. Push to a path no production pipeline reads from, or a real run could
pick up your test build.

### Rerun one stage on Terra and compare

```bash
python terra/recon.py                         # read-only look at the workspace and baseline run
python terra/batch_freeze.py plan             # what would be copied from the baseline
python terra/batch_freeze.py copy --write     # copy the baseline's inputs into your bucket
python terra/batch_rerun_step.py --step 08 submit --confirm   # rerun that stage with your branch's images
python terra/batch_status.py --costs          # check progress and cost
terra/batch_fetch_compare.sh all              # download the outputs and compare them to the baseline
```

Save one good run as a baseline, rerun the stage you changed on the same inputs, and compare. It costs far
less than running the whole pipeline twice, and you know the inputs matched. Commands that change anything
outside your machine refuse to run until you pass `--confirm` or `--write`. `--step` picks the stage, and
`batch_rerun_step.py` reruns any of the five batch stages 06, 07, 08, 09, 10 — printing the workflow, config
name and root entity it resolved to before it does anything, so you see which stage you asked for. The
single-sample stages are not on that list: the freeze loop cannot write a participant entity at all, so
there is nothing frozen to rerun against (see [module profiles](docs/module-profiles.md)). The [Terra
head-to-head](docs/terra-head-to-head.md) guide covers the full sequence.

### Run a stage locally

Some GATK-based stages are expensive. `replay/build_inputs.py` rebuilds a past run's inputs from its own
records, so you can run the stage on your machine with exactly what the real run used. If the answer
changes, your code changed it. You'll need a GATK `.jar` you built yourself and tens of gigabytes of free
disk. See [local replay](docs/local-replay.md).

### Compare outputs

```bash
./gsvtk compare batch-tables --baseline-dir old/ --new-dir new/
python compare/table_diff.py a.tsv b.tsv --key sample_id    # or any comparator directly
```

The `compare/` tools diff tables, VCFs, matrices, JSON, and archives. Each can write a `--json` report
recording how it reached its answer. If there turns out to be nothing to compare, the tool exits with an
error. It won't report a match. See [comparators](docs/comparators.md).

### Use it from an AI agent

The repo ships an agent skill in `.pi/skills/gatk-sv-testkit/`. Its `scripts/gsvtk` is a thin shim: it
finds the checkout, requires that the checkout is a clone of a remote you trust, then runs this same
`./gsvtk` with `GSVTK_READ_ONLY=1`. So an agent can run the checks, preview builds and inspect Terra,
but can't submit jobs, boot a VM or bulk-download — and the rules it is held to are the ones above, not
a second copy.

## Tool reference

All the scripts, one line each. `./gsvtk <command>` reaches most of them by subcommand
([docs/cli.md](docs/cli.md) is the map); the rest run directly, as written here.

```text
gsvtk                          the command-line entry point: check | build | terra | compare | replay
checks/
  wdl_gate.sh                  will the pipeline start: miniwdl, plus the input JSONs CI renders
  wdl_semantics.py             bugs that only show up while a stage is running
  wdl_reach.py                 which workflows a file change affects
  svshell_contract_check.py    stages that ask for inputs nobody provides
  svshell_jq_plumbing_scan.py  runs the input-building code and flags empty values

checks/image-check/
  run_in_image.sh              run your own script inside a published image, on a cloud VM
  svshell_image_check.sh       check the sv_shell scripts inside the published image match your branch
  jar_flag_probe.sh            list the options the image's Java tools accept

docker/
  gatk-sv-build.sh             build a branch's images on a temporary cloud VM

terra/
  recon.py                     inspect a workspace and a past run (read-only)
  fetch_baseline.py            save a finished run's inputs and outputs to a local file
  stage_inputs.py              download those inputs, or just one chromosome's worth
  wdl_flat.py                  inline a WDL's imports into one file so Terra can store it
  batch_freeze.py              copy the inputs into your own bucket: plan | copy | verify | attrs
  batch_configs.py             manage the method configs that wire files to stages
  batch_check_inputs.py        check a stage's config against its WDL, and that its input files exist
  batch_rerun_step.py          rerun one stage of 06-10 with your branch's code and images
  batch_status.py              check whether jobs have finished and what they cost
  batch_peek.py                see what's running, what retried, and what failed first
  batch_save_metadata.py       save a run's metadata locally
  batch_cost.py                work out a run's cost from its saved metadata
  fetch_outputs.py             download specific outputs from a run
  batch_fetch_compare.sh       download a stage's outputs and compare them

replay/
  build_inputs.py              rebuild a past run's input file from that run's own records

compare/
  compare_batch_tables.py      summary tables from two runs, column by column
  table_diff.py                any two tables that share a key column
  matrix_diff.py               site-by-sample matrices: which cells changed
  vcf_paired_diff.py           per-(site, sample) values across two VCFs
  site_set_diff.py             variants found in one run but not the other
  lineset_diff.py              two lists: what was added, what was removed
  json_diff.py                 nested JSON, with paths you choose to ignore
  tar_manifest.py              two archives, by file list and checksum
  profile_summarize.py         one headline number from GATK-SV's own comparison tool
  pair_level_concordance.py    concordance between two call sets, computed independently
  gq_scale_compare.py          whether two quality columns use the same scale
  gq_paired_compare.py         on matched pairs: the same values, or a different scale
  diff_rd_states.py            where two runs disagree on copy number
  artifact_tally.py            count what an output file actually contains, ignoring its own summary

kit/
  gsvtk-config                 shared settings for every tool: get | require | show | work | miniwdl | doctor
```

## Documentation

- [Quickstart](docs/quickstart.md): a walkthrough of the main tasks, with real output at every step. None
  of it costs anything.
- [Setup](docs/setup.md): dependencies, cloud login, and the agent skill.
- [Configuration](docs/config.md): every setting, including the two without defaults.
- [The `gsvtk` command line](docs/cli.md): the command-to-tool map and the read-only contract.
- Guides: [static checks](docs/static-checks.md), [Docker builds](docs/docker-builds.md),
  [Terra head-to-head](docs/terra-head-to-head.md), [local replay](docs/local-replay.md), and
  [comparators](docs/comparators.md).
- [Troubleshooting](docs/troubleshooting.md): search for your exact error message.
- [Methodology](docs/methodology.md) and the [gap ledger](docs/gap-ledger.md): how the claims in these
  docs were tested, and how each review finding was resolved.

## Costs

The checks and the build previews (`--check`, `--dry-run`) are free. An image build uses one mid-sized VM
for about 1.5 to 3 hours ([details](docs/docker-builds.md#costs--gotchas)). Rerunning a stage on Terra
uses a batch of VMs for anywhere from tens of minutes to a few hours. A local run costs only time and
disk. The docs don't quote prices, because rates vary by account.

## Contributing

Run `make test` before you open a pull request. It's the offline test suite, and it must pass with no
cloud access, no data, and no network. [CONTRIBUTING.md](CONTRIBUTING.md) covers what it checks, where new
files go, and the bar for adding a tool.
