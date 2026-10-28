# gatk-sv-testkit

[![ci](https://github.com/talkowski-lab/gatk-sv-testkit/actions/workflows/ci.yml/badge.svg)](https://github.com/talkowski-lab/gatk-sv-testkit/actions/workflows/ci.yml)

A toolkit for developers working on [GATK-SV](https://github.com/broadinstitute/gatk-sv), a structural
variant discovery pipeline for short-read sequencing data.

A full GATK-SV run takes hours of cloud compute, and many of the mistakes that sink a run can be caught
before launch. This kit gives you:

- static checks that tell you, in seconds and offline, whether a WDL or `sv_shell` change will run;
- image builds for any gatk-sv branch on a temporary cloud VM, so you don't need local Docker;
- a way to rerun one pipeline stage on Terra against a frozen baseline run, instead of the whole pipeline;
- local replay of GATK-based stages on a real run's exact inputs, at no cloud cost;
- comparators that diff tables, VCFs, matrices, JSON and archives between two runs.

gatk-sv-testkit is an independent project. It follows GATK-SV's documented conventions for building
images and for the shared Terra workspace.

## Installation

You need Python 3.9 or later. Other requirements depend on which tools you use; each tool names what
it is missing.

```bash
git clone https://github.com/talkowski-lab/gatk-sv-testkit.git
cd gatk-sv-testkit
make setup                                               # creates ./.venv from requirements.txt
.venv/bin/python -m pip install -r requirements-dev.txt  # miniwdl, jinja2, flake8, pytest: for the full checks and tests
cp testkit.env.example testkit.env                       # set your cloud project and Terra workspace
./kit/gsvtk-config doctor                                # what is set, what is missing, and where each value came from
```

Your GCP project and your Terra workspace have no default: they decide who is billed and where things
are written, so the tools refuse to guess them. [Setup](docs/setup.md) covers the full dependency list
and cloud login; [Configuration](docs/config.md) lists every setting.

## Quick usage

`./gsvtk` is the command-line entry point. It routes to the scripts in this repo and adds one safety
gate.

```bash
./gsvtk --help                  # every subcommand and the exit codes
./gsvtk tools                   # which loops can run on this machine, and where each dependency was found
./gsvtk check origin/main       # the static checks against a gatk-sv ref
./gsvtk build my-branch         # free preview of an image build: --check, then --dry-run
./gsvtk terra show              # what the Terra method configs would bind (offline)
./gsvtk compare --list          # the comparators, by name
./gsvtk replay preflight        # what is missing for a local replay on this machine
```

Every tool is also a script you can run directly from the repo root, with its own `--help`.

### Safety

Everything is read-only by default. Any mode that POSTs to Terra, boots a VM, pushes an image or
bulk-downloads data refuses to run until you add `--confirm` (and, for some tools, their own `--write`
or `--allow-shared-target`). Before acting, it prints what it will create, where, what it costs and how
to undo it. With `GSVTK_READ_ONLY=1` or `./gsvtk --read-only`, those modes are refused outright. No
`make` target spends money. The full contract is in [docs/cli.md](docs/cli.md).

Exit codes are shared across the kit: 0 clean, 1 findings or failure, 2 usage error or refused mode,
3 missing dependency, 4 missing required config. Comparators use 0 for same, 1 for differences, and 2
when there was nothing to compare.

## Tool map

| Task | Start with | Guide |
|---|---|---|
| First run, end to end, with nothing billed | `./gsvtk tools` | [Quickstart](docs/quickstart.md) |
| Dependencies, cloud login, the agent skill | `make setup` | [Setup](docs/setup.md) |
| Settings and where they come from | `./kit/gsvtk-config show` | [Configuration](docs/config.md) |
| The CLI, its modes and the read-only contract | `./gsvtk --help` | [CLI reference](docs/cli.md) |
| Will my WDL or `sv_shell` change launch? | `./gsvtk check <ref>` | [Static checks](docs/static-checks.md) |
| Build a branch's images | `docker/gatk-sv-build.sh --check <branch>` | [Docker builds](docs/docker-builds.md) |
| Rerun one stage on Terra and compare | `python terra/recon.py` | [Terra head-to-head](docs/terra-head-to-head.md), [Test a branch on Terra](docs/test-a-branch.md) |
| Run a branch that is not published on Dockstore | `./gsvtk terra method-plan <Workflow> --ref <branch>` | [Terra head-to-head §3](docs/terra-head-to-head.md#running-a-branch-that-is-not-on-dockstore) |
| Drive a module other than genotyping | `./gsvtk --module single_sample terra rerun-show --step 01` | [Module profiles](docs/module-profiles.md) |
| Compare a module profile with upstream gatk-sv | `python scripts/derive_profile.py check --module genotyping` | [Module profiles](docs/module-profiles.md#deriving-a-profile-from-upstream) |
| Run a GATK-based stage locally on a real run's inputs | `./gsvtk replay preflight` | [Local replay](docs/local-replay.md) |
| Replay one task, or sv-shell end to end, on a cloud VM | `./gsvtk replay task ...` / `./gsvtk replay svshell ...` (free plan) | [Local replay](docs/local-replay.md#replay-one-task-on-a-vm) |
| Compare two runs' outputs | `./gsvtk compare <name> ...` | [Comparators](docs/comparators.md) |
| An error message you are looking at | search for the verbatim text | [Troubleshooting](docs/troubleshooting.md) |
| How results in these docs were checked | | [Methodology](docs/methodology.md) |
| What is built but not yet verified on a cloud run, and open decisions | | [Roadmap](docs/roadmap.md) |

### Scripts

```text
gsvtk                          command-line entry point
checks/
  wdl_gate.sh                  will the pipeline start: miniwdl, plus the input JSONs CI renders
  wdl_inputs_check.py          do the rendered input JSONs bind every required WDL input
  wdl_semantics.py             bugs that only show up while a stage is running
  wdl_reach.py                 which workflows a file change affects
  svshell_contract_check.py    sv_shell stages that read inputs nobody provides
  svshell_jq_plumbing_scan.py  runs the sv_shell input-building jq and flags empty values
  terra_entity_check.py        which entity row each launch config runs against, and does Terra agree
  version_resolves.py          is the Dockstore version a rerun names still the branch tip
  body_golden_diff.py          diff a config body against one captured from a workspace
  failure_cause.py             did a workflow fail in the task or in the cloud
  cost_estimate.py             price a finished run from its metadata and a rates file you supply
checks/image-check/
  run_in_image.sh              run a script inside a published image, on a cloud VM
  svshell_image_check.sh       do the sv_shell scripts in a published image match your branch
  jar_flag_probe.sh            list the options the image's Java tools accept
docker/
  gatk-sv-build.sh             build a branch's images on a temporary cloud VM
  image_capture.py             turn a finished build into digest-pinned image refs
terra/
  recon.py                     inspect a workspace and a past run (read-only)
  fetch_baseline.py            save a finished run's inputs and outputs to a local file
  stage_inputs.py              download those inputs, or one chromosome's worth
  wdl_flat.py                  inline a WDL's imports into one file
  method_upload.py             upload a flattened WDL at a commit to Terra's method repository
  dump_configs.py              save every method config in a workspace, in full (read-only)
  batch_freeze.py              copy baseline inputs into your bucket: plan | copy | verify | attrs
  batch_configs.py             show, check, create and validate the method configs
  batch_check_inputs.py        check a stage's config against its WDL and that its inputs exist
  batch_rerun_step.py          rerun one stage with your branch's code and images
  abort_submission.py          abort one running submission
  batch_status.py              job state and VM-minutes
  batch_peek.py                what is running, what retried, what failed first
  batch_save_metadata.py       save a run's Cromwell metadata locally
  batch_cost.py                VM-minutes from saved metadata
  fetch_outputs.py             download named workflow outputs, resolving call-cache hits
  batch_fetch_compare.sh       download a stage's outputs and compare them
replay/
  build_inputs.py              rebuild a past run's inputs JSON from its own metadata
  task_replay.py               rerun one finished task in an image on a cloud VM, on its real inputs
  svshell_run.py               run sv-shell end to end on a cloud VM and compare the final VCF
compare/
  compare_batch_tables.py      summary tables from two runs, column by column
  table_diff.py                any two tables that share a key column
  matrix_diff.py               site-by-sample matrices: which cells changed
  vcf_paired_diff.py           per-(site, sample) values across two VCFs
  site_set_diff.py             variants found in one run but not the other
  lineset_diff.py              two lists: what was added, what was removed
  json_diff.py                 nested JSON, with paths you choose to ignore
  tar_manifest.py              two archives, by file list and checksum
  profile_summarize.py         one headline number from gatk-sv-profile output
  pair_level_concordance.py    concordance between two call sets
  gq_scale_compare.py          whether two quality fields use the same scale
  vcf_body_diff.py             two VCF record bodies, header lines put aside
  gd_calls_diff.py             two GD calls tables on (sample, GD_ID)
  artifact_tally.py            count what one output file contains
  sample_census.py             distinct samples in one evidence table, streamed
kit/
  gsvtk-config                 shared settings: get | require | env | show | work | miniwdl | doctor
scripts/
  derive_profile.py            derive a module profile's fields from upstream gatk-sv, or check one
```

## Compatibility

The kit is checked against two kinds of gatk-sv ref. A **branch** ref is the gatk-sv commit your branch
under test is based on; the module profiles must agree with it. A **baseline** ref is the release the
frozen baseline run used. The checked refs:

<!-- compat-table:begin (scripts/check_compat.py table) -->
| gatk-sv ref | commit | role | verdict | checked |
|---|---|---|---|---|
| `main` | `8fea3adc` | branch | compatible | 2026-10-28 |
| `v1.1.1` | `b5d5049c` | baseline | compatible | 2026-10-28 |
<!-- compat-table:end -->

Check your own gatk-sv clone with `./gsvtk compat --ref HEAD`. See [Compatibility](docs/compatibility.md).

## Costs

The checks and the build previews are free. An image build uses one `e2-standard-8` VM for roughly 1 to 3
hours. Rerunning a stage on Terra uses a batch of VMs for tens of minutes to a few hours. A local replay
costs time and disk. The tools report cost as machine type, VM-minutes or object size and never quote a
price, because rates depend on your account.

## Contributing

Run `make test` before opening a pull request. It is the offline test suite and must pass with no
credentials, data or network. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

BSD 3-Clause. See [LICENSE](LICENSE).
