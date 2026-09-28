# gatk-sv-testkit

[![ci](https://github.com/talkowski-lab/gatk-sv-testkit/actions/workflows/ci.yml/badge.svg)](https://github.com/talkowski-lab/gatk-sv-testkit/actions/workflows/ci.yml)

[GATK-SV](https://github.com/broadinstitute/gatk-sv) is a pipeline that calls structural variants from
raw reads, across a whole cohort. This repo is a toolbox that sits next to it. It does not call variants,
and it does not host your data.

The reason it exists is waiting. Checking one change in that pipeline usually means launching it and
waiting: hours of VM time, a docker image your laptop cannot build, or a renamed JSON key that shows
itself five stages into a live run, after you have already paid for the machines. Every tool here answers
one of those questions somewhere cheaper. A real run is still the real test, and nothing here replaces it.

> [!NOTE]
> Nothing in this repo is part of GATK-SV, endorsed by the Broad Institute, or needed to run the pipeline.

## The four jobs

| Job | Without these tools | With them |
|---|---|---|
| Build a docker image from a branch | Install Docker Desktop, or run the manual build on a VM by hand. One to three hours. Apple Silicon cannot build `linux/amd64` locally at all. | `docker/gatk-sv-build.sh <branch>` boots one temporary x86_64 VM, builds, pushes, and deletes itself. No Docker on your laptop. [Docker builds](docs/docker-builds.md) |
| Compare a branch against a real run | Run the whole pipeline twice and hope the inputs matched. | Freeze one run's inputs, re-run only the stage you changed, and diff the tables and VCFs that come out. [Terra head-to-head](docs/terra-head-to-head.md) |
| Test a genotyper change with no cloud spend | Not possible without a Terra run. | Pull that run's exact frozen inputs and run the real trainer locally, against a jar you built. [Local replay](docs/local-replay.md) |
| Find a WDL or `sv_shell` break before submitting | Discover it mid-submission, once the VMs have booted. | Static checks, in seconds: no data, no Docker, no network. [Static checks](docs/static-checks.md) |

A session usually runs in that order: change a WDL, run the two static checks, build the image, re-run
the one stage you changed, diff the results, and write down the command behind every number.
[docs/quickstart.md](docs/quickstart.md) walks through all four jobs with the real output of each step,
and nothing in it spends money.

## Getting started

### What you need

Python 3.9 or newer. The rest depends on which job you want, and every tool names what it is missing.

- Static checks and the config tools: nothing else.
- The WDL checks: `miniwdl`, from `requirements-dev.txt`.
- The Terra tools: `firecloud` (that is fiss; the PyPI name is not `fiss`) and `google-auth`, plus
  `gcloud auth application-default login`.
- Docker builds and probes: `gcloud`, and permission for a VM to push to your registry.
- Local replay: JDK 17 or newer, and a GATK jar you built.
- Comparing profile tables: `numpy` and `pandas`, plus `bcftools` or `pysam` for some.

Credentials and the full table are in [docs/setup.md](docs/setup.md).

### Install

```bash
git clone https://github.com/talkowski-lab/gatk-sv-testkit.git
cd gatk-sv-testkit
make setup                                             # a virtualenv with what the tools import
.venv/bin/python -m pip install -r requirements-dev.txt   # and the dev tools: miniwdl and flake8
cp testkit.env.example testkit.env                     # then edit it: your project, your workspace
./kit/gsvtk-config doctor                              # prints what is still missing
```

Do not skip the second install line. `make setup` installs only what the tools import, so without the dev
file the test gate further down quietly covers less than it claims to.

### Try it

The fastest end of the toolbox. It needs `miniwdl`, which the dev install above provides, and a checkout
of gatk-sv for the WDLs, pointed at by `GSVTK_GATK_SV_CHECKOUT`. Nothing in it touches the cloud:

```bash
checks/wdl_gate.sh origin/main
```

Real output, captured 2026-09-28, not a mockup:

```text
$ checks/wdl_gate.sh origin/main
WORKFLOW                       REF                        EXIT   INCOMPLETECALL   STALE-BINDINGS
SVShell                        main@e1909d2f              0      1                0
GATKSVPipelineSingleSample     main@e1909d2f              0      2                0
GenotypeBatch                  main@e1909d2f              0      0                0
MakeCohortVcf                  main@e1909d2f              0      0                0
SEMANTICS                      main@e1909d2f              WRITE-SCOPE=0 DEFINED-ONLY=2 PIPEFAIL=3 SHELL-SYNTAX=2 LOAD-FAILURES=0
no hard errors. Re-run with --strict to treat IncompleteCall / stale bindings / a rise in
the SEMANTICS counts as failure (today's gatk-sv carries a few of each on purpose, so
 --strict is a diff-against-baseline decision, not a default).
```

35 seconds on a laptop, reading the WDLs from your own gatk-sv checkout. The checker counts two things a
passing `miniwdl check` cannot see: an input that some call site never supplies, and an input the callee
no longer accepts. Today's gatk-sv has a few of both, so the useful number is the difference between two
refs rather than the absolute count:

```bash
checks/wdl_gate.sh origin/main my-branch
```

## Configuration

One file, `testkit.env`, read by every tool through `kit/gsvtk-config`. Precedence:

```
environment variable  >  testkit.env  >  derived  >  built-in default
```

Two keys have no default at all: your GCP project and your Terra workspace. They decide whose bill this is
and whose workspace gets written to, so the tools stop and name the missing key instead of guessing. Run
`./kit/gsvtk-config show` to print every value with where it came from. Details in
[docs/config.md](docs/config.md).

Scratch output, which for some jobs runs to tens of gigabytes, goes under `GSVTK_WORK` and is gitignored.
Nothing the tools produce gets committed.

## Three things worth knowing early

1. **Most of it writes nothing.** Everything in `checks/` and `compare/` touches only `GSVTK_WORK`.
   Helpers that change something need `confirm=True`. The ones that start compute also need `--confirm` on
   the command line. The ones that write to Terra refuse the shared baseline workspace unless you add
   `--allow-shared-target`.
2. **Choose a push target that nothing reads.** The default registry path is derived from your project. If
   a real pipeline reads that path, then it is now running your test image. The tools warn you, and past
   the warning it is your judgement.
3. **Run `make audit` before you push.** It fails if a tracked file holds a credential shape, or any value
   that resolves to one of *your* coordinates: project, workspace, registry path, checkout path. It reads
   those off your own configuration, which is why CI alone cannot catch it.
   [docs/static-checks.md](docs/static-checks.md) explains both it and `make audit-history`, which grades
   what you already published instead of what you are about to publish.

The repo also ships the agent skill that drives it, under `.pi/skills/gatk-sv-testkit/`. Its wrapper
dispatches read-only modes only, so an agent cannot reach a submission or a VM by accident.
[docs/setup.md](docs/setup.md) has the details.

## What is in the repo

```
docker/     build and push any branch's images, with no local Docker
terra/      the Terra loop: recon, baseline freezing, method configs, status, cost, fetch, compare
checks/     static checks: WDL launchability, WDL semantics, sv_shell JSON contract, image byte-proof
compare/    comparators for keyed tables, matrices, VCF fields, sets and bundles, plus an
            artifact tally that refuses to report agreement on nothing
kit/        the config layer that every tool reads
replay/     rebuild a launchable input JSON from a captured successful run
scripts/    the publish guard, the WDL fetcher, and the gate's own tooling
examples/   worked drivers from a real investigation, kept as recipes
docs/       one page per job, with the gotchas keyed on their verbatim error text
.pi/skills/ the agent skill and its read-only wrapper
```

Run `make help` for the tool index.

## Running the tests

```bash
make test
```

That grades the toolkit itself. Everything under `checks/` grades gatk-sv, which is a different question
and easy to confuse with this one. This gate needs no credentials, no data and no network. It has seven phases, and each one
was added because the one before it let something through:

```bash
make syntax      # every .sh parses, every .py compiles
make undefmods   # attribute access on a module the file never imports
make flake       # pyflakes: undefined names, unused values, imports that are not there
make helpsweep   # --help on every tool, with zero configuration
make smoke       # the tools actually run, end to end, on hostile fixtures
make audit       # the publish guard
make selftest    # config layer, both checkers, the shipped skill, the docs, the pinned probe set
```

Two of those phases are there because compiling was not enough. A tool can print its usage and still be
dead when called for real, so `smoke` runs the tools against empty files and against tables that share no
keys, where the honest answer is "I compared nothing" followed by a nonzero exit. `selftest` adds a canary
that asserts a known-failing command is reported as failing, plus one probe per defect a review confirmed,
each with a positive control so a guard that could never fire cannot pass as a guard that never had to.
The probe count is pinned: a probe that vanishes fails the gate, and so does one that never ran because a
dependency was missing. Read the `ok` count, not the pass line.

There is no style checker here. `make lint` is an alias of `make syntax`, and the Makefile says why: no
whitespace opinions. [CONTRIBUTING.md](CONTRIBUTING.md) has the table of what each phase proves, and the
bug each one caught.

## Costs

No prices, because your billing account and your discounts are not visible from here. Machines and
wall-clock instead.

- A `sv-pipeline` build: one `e2-standard-8`, one to three hours, plus a 150 GB persistent disk. It
  deletes itself on success; on failure it shuts down and stays, so you can read the log.
- A Terra head-to-head step: a fleet of VMs, tens of minutes to hours. `terra/batch_cost.py` recomputes
  the VM-minutes from saved metadata, so a published number can be checked afterwards.
- Local replay: time and disk. The staged reference matrices run to tens of gigabytes.

## Where to look next

- [docs/quickstart.md](docs/quickstart.md) all four jobs, step by step, with real output
- [docs/setup.md](docs/setup.md) dependencies, credentials, and the agent skill
- [docs/config.md](docs/config.md) every key, including the two with no default
- [docs/troubleshooting.md](docs/troubleshooting.md) keyed on the verbatim error text, which is the one
  thing you definitely have
- [docs/comparators.md](docs/comparators.md) what each comparator decides, and the unit traps that already
  cost someone an analysis
- [docs/gap-ledger.md](docs/gap-ledger.md) every item the gap reviews asked for, what happened to it, and
  the command that proves it

## Contributing

Run `make test` first. [CONTRIBUTING.md](CONTRIBUTING.md) has the bar: a new tool either removes an hour
of waiting or catches a bug before it costs VM time. If it does neither, it probably belongs in gatk-sv.

## Built with

Bash 3.2 for the config layer and the shell drivers, because macOS still ships it. Python 3.9 and mostly
the standard library. [miniwdl](https://github.com/chanzuckerberg/miniwdl) for WDL parsing, and `flake8`
used only as a pyflakes runner. External binaries: [jq](https://stedolan.github.io/jq/), `bcftools`,
`gsutil`, `gcloud`. Terra through `firecloud`. Google Compute Engine for the build and probe VMs.
[GATK](https://github.com/broadinstitute/gatk) jars you build yourself.
[pi](https://github.com/badlogic/pi-mono) for the agent skill.

## License

BSD 3-Clause, Talkowski Lab. See [LICENSE](LICENSE). This toolkit is separate from GATK-SV and is not
endorsed by the Broad Institute.

## Acknowledgments

The design follows GATK-SV's documented flows: manual docker deployment, the numbered joint-calling
workspace, and the `wdl/` and `src/sv_shell` layouts. Concordance is expected to be published with
[gatk-sv-profile](https://github.com/broadinstitute/gatk-sv-profile). The comparators here exist because
its tables are bucketed, and a figure lifted from one needs its aggregation rule written down.
