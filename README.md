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

There's no single command-line program. Each tool is a script you run from the repo root, and they're
grouped into directories by task. Every script accepts `--help`, and the [Quickstart](docs/quickstart.md)
walks through each task with real output.

### Check a change before you run it

```bash
checks/wdl_gate.sh origin/main                               # will the pipeline start, or only look valid?
python checks/svshell_jq_plumbing_scan.py --compare-to main  # did my change add empty sv_shell inputs?
```

These run in seconds on a laptop, with no data and no cloud access. They catch problems `miniwdl check`
misses. Compare against `main` so you only see what your change added, not problems already upstream.

### Build images for your branch

```bash
docker/gatk-sv-build.sh --check my-branch     # free: does the branch exist?
docker/gatk-sv-build.sh --dry-run my-branch   # free: print the plan
docker/gatk-sv-build.sh my-branch             # billed: build on a temporary cloud VM, push, delete it
```

GATK-SV images target x86-64 Linux, so you can't practically build them on an Apple Silicon Mac. This
script builds them in your cloud project instead. The real build starts a VM right away, with no
confirmation prompt, so run `--dry-run` first. Push to a path no production pipeline reads from, or a
real run could pick up your test build.

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
python compare/compare_batch_tables.py --baseline-dir old/ --new-dir new/
```

The `compare/` tools diff tables, VCFs, matrices, JSON, and archives. Each can write a `--json` report
recording how it reached its answer. If there turns out to be nothing to compare, the tool exits with an
error. It won't report a match. See [comparators](docs/comparators.md).

### Use it from an AI agent

The repo ships an agent skill in `.pi/skills/gatk-sv-testkit/`. It works through a read-only wrapper,
`.pi/skills/gatk-sv-testkit/scripts/gsvtk`, which can run the checks, preview builds, and inspect Terra,
but can't submit jobs or spend money.

## Tool reference

All the scripts, one line each.

```text
checks/
  wdl_gate.sh                  will the pipeline actually start, or does it only look valid
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
