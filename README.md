# gatk-sv-testkit

[![ci](https://github.com/talkowski-lab/gatk-sv-testkit/actions/workflows/ci.yml/badge.svg)](https://github.com/talkowski-lab/gatk-sv-testkit/actions/workflows/ci.yml)

[GATK-SV](https://github.com/broadinstitute/gatk-sv) carries structural-variant calling from raw reads
to a whole cohort, in one pipeline. This repo is a set of tools that sit around it: static checks, a
docker build harness, and comparison tools for what comes out.

It exists because checking a change in that pipeline usually means launching it and waiting. The waits
are familiar: hours of VM time; a docker build that Apple Silicon cannot run natively; and, in the
`src/sv_shell` layer, which gatk-sv's CI does not cover, a renamed JSON key that first shows itself
several stages into a live run. Each tool here answers one of those questions somewhere cheaper. A full
run is still the real test, and none of this replaces it.

The static checks are the fast end: no credentials, no data, no docker. Captured 2026-09-28, not a
mockup:

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

35 seconds on a laptop against gatk-sv `main`, working WDLs fetched from a checkout. Give it your branch
as the second argument and the same command prints the change in those counts against the base ref.

> [!NOTE]
> Nothing here is part of GATK-SV, endorsed by the Broad Institute, or required to run the pipeline. It
> is a developer's toolbox *around* it.

## Contents

* [What you can do with it](#what-you-can-do-with-it)
* [A normal session](#a-normal-session)
* [What it is not](#what-it-is-not)
* [The four loops it shortens](#the-four-loops-it-shortens)
* [Getting Started](#getting-started)
  * [Prerequisites](#prerequisites)
  * [Installing](#installing)
  * [Configuration](#configuration)
  * [Things worth knowing before you start](#things-worth-knowing-before-you-start)
  * [What is here](#what-is-here)
* [Running the tests](#running-the-tests)
  * [End to end tests](#end-to-end-tests)
  * [Coding style tests](#coding-style-tests)
* [Costs](#costs)
* [Built With](#built-with)
* [Contributing](#contributing)
* [License](#license)
* [Acknowledgments](#acknowledgments)

## What you can do with it

* A workflow can pass `miniwdl check` and still be unlaunchable, because a call site never passes a
  required input, or passes one the callee deleted. `checks/wdl_gate.sh` turns both into counts you can
  diff between two refs. The `src/sv_shell` layer has the same delay built in: rename a key there and
  `jq` hands the *string* `"null"` to a module several stages later, after you have paid for the VMs.
  `checks/svshell_contract_check.py` finds it before you submit anything.
* The comparators give a verdict per column: `MATCH`, `DELTA`, `MISSING`, and `STRATEGY` for a value
  that changed *on purpose*, where comparing raw means would tell you nothing. Point one at empty
  directories and it reports that it compared nothing and exits nonzero, because an empty table that
  reads like "no differences" is the failure this repo has been caught by more than once. The comparator
  docs also list the traps that already cost someone an analysis, like two quality fields that measure
  the same thing on different scales, where "the new pipeline lost 90% of the signal" was really a 10×
  unit change ([docs/comparators.md](docs/comparators.md)).
* `--check` and `--dry-run` on the build script cost nothing and tell you whether the build would even
  start. The build itself runs on a throwaway x86_64 VM: it streams the log, pushes, and deletes the VM
  on success. Your laptop needs no Docker.
* A method-config map can match one branch's WDL and not the branch you are actually testing, and the
  cloud rejects the whole config at submission, after it sat in your workspace looking fine.
  `terra/batch_configs.py check --against main` does that comparison offline, in seconds, against your
  own checkout.
* You can run the real trainer locally on a real run's exact inputs, with a jar you built yourself. No
  cloud, no data movement, nothing to rent.
* `show` and `doctor` print every setting with where it came from: environment, profile file, derived,
  or default. The two settings that decide whose bill it is and whose workspace gets written have no
  default at all. The tools stop and name the missing key.
* The repo ships the skill that drives it, and the wrapper dispatches read-only modes only, so an agent
  cannot reach a POST or a VM by accident.

## A normal session

Change a WDL or a shell script → run the two static checks → build the image → run the same pipeline step
before and after → diff the tables and VCFs → write up the result with the command that produced it, so
somebody else can run it too.

## What it is not

It does not call variants; that is gatk-sv. It does not host your data. It will not quote you a price
either: costs are given as machine type and wall-clock, because your billing account and discounts are
not something this repo can see. Its findings need triage before they mean anything. Run against gatk-sv
as it stands, the static checks report several unsupplied reads and a handful of `IncompleteCall`
warnings *on purpose*, so what you want is the **diff** against the ref you are changing
([docs/static-checks.md](docs/static-checks.md)). The run-the-pipeline tools are also written around the
Genotyping module. Making them work for any module is
[docs/module-profiles.md](docs/module-profiles.md), which is a proposal and has not been built.

## The four loops it shortens

| Loop | Without this | With this | Docs |
|---|---|---|---|
| **Build a docker image from a branch** | Install Docker Desktop, or hand-run the manual build on a VM; ~1-3 h; Apple Silicon cannot build `linux/amd64` locally at all | `docker/gatk-sv-build.sh <branch>`: one throwaway x86_64 GCE VM, no local Docker, streams the log, deletes itself | [docker builds](docs/docker-builds.md) |
| **Compare a branch against a real baseline run** | Re-run the whole pipeline twice and hope the inputs matched | Freeze one baseline's inputs, run only the changed stage, diff the tables and VCFs that came out | [Terra head-to-head](docs/terra-head-to-head.md) |
| **Test a genotyper change without any cloud spend** | Not possible without a Terra run | `terra/stage_inputs.py` pulls the exact frozen inputs; a locally built GATK jar runs the real trainer on them | [local replay](docs/local-replay.md) |
| **Catch a WDL / `sv_shell` break before submitting** | Discover it mid-submission, after VMs booted | `checks/` is static: seconds, no data, no docker, no network. `wdl_gate.sh` asks whether the call bindings are right; `wdl_semantics.py` asks whether the workflow would RUN at all (both diff against a base ref). `image-check/` is the deliberate exception: it asks a shipped image | [static checks](docs/static-checks.md) |

## Getting Started

### Prerequisites

Python 3.9 or newer, and `make setup` gives you a venv with what the tools import. Most of what follows
is optional and each tool says what it is missing; the full page is
[docs/setup.md](docs/setup.md).

| You want to | Also needs |
|---|---|
| run anything in `checks/` or `kit/` | nothing beyond the standard library |
| run the WDL checks or rebuild an input JSON | `miniwdl`, from `requirements-dev.txt` |
| use the `terra/` tools | `firecloud` (that **is** fiss; the PyPI name is not `fiss`) and `google-auth`, plus `gcloud auth application-default login` |
| fetch or stage `gs://` objects | `gsutil`, under the same credentials |
| build or probe docker images | `gcloud`, and IAM that lets a VM push to your registry |
| run the plumbing scan | `jq` on `PATH` |
| compare profile tables | `numpy`, `pandas`; `bcftools` or `pysam` for some |
| replay a trainer locally | JDK 17+ and a GATK jar you built |

### Installing

```bash
git clone https://github.com/talkowski-lab/gatk-sv-testkit.git
cd gatk-sv-testkit
make setup                          # .venv with the python dependencies
.venv/bin/python -m pip install -r requirements-dev.txt   # miniwdl + flake8: the FULL gate

cp testkit.env.example testkit.env  # then edit it: project + workspace are yours to name
./kit/gsvtk-config doctor           # tells you exactly what is still missing
```

Install the dev file alongside `make setup`. miniwdl and flake8 are dev tools rather than imports, so
`make setup` alone cannot install them, and without them the gate quietly covers less than it claims.

Then pick a loop. [docs/quickstart.md](docs/quickstart.md) walks through all four and prints the real
output of each step; nothing in it spends money. The two most common first runs:

```bash
# build a branch's sv-pipeline image on a throwaway x86 VM, no local Docker
docker/gatk-sv-build.sh --dry-run my-branch    # shows what it would do, touches nothing
docker/gatk-sv-build.sh my-branch              # ~1-3 h, streams the build log

# is my branch's WDL actually launchable? (refs are in your gatk-sv checkout; needs miniwdl,
# takes seconds)
checks/wdl_gate.sh origin/main my-branch
```

### Configuration

One profile, read by every tool, resolved in one place (`kit/gsvtk-config`):

```
environment variable  >  testkit.env  >  derived  >  built-in default
```

Two values have **no default at all**: the GCP project and the Terra workspace. They decide whose money
is spent and whose workspace gets written to, so the tools stop and name the missing key rather than
guess. See [docs/config.md](docs/config.md).

Scratch output (staged inputs, replay runs, frozen manifests, fetched callsets, tens of GB) goes under
`GSVTK_WORK` and is gitignored. Nothing the tools produce is committed.

### Things worth knowing before you start

- **The skill ships with the repo.** `.pi/skills/gatk-sv-testkit/` is a
  [pi](https://github.com/badlogic/pi-mono) skill, and any other harness that reads `.pi/skills/` can use
  it too. It covers how to locate a checkout, which loop answers which question, and what is safe to run
  unattended. Its wrapper dispatches **only** read-only modes: `submit`, `create`, `copy`,
  `attrs --write`, `fetch`, `profile` are refused before anything else runs. `make selftest` runs
  `scripts/check_skill.py`, which *executes* those refusals and compares the result to what the prose
  claims, so "submit is refused" stays something that gets checked.
- **Read-only by default.** Mutating helpers in `terra/` need `confirm=True`. The ones that start compute
  also refuse without `--confirm` on the command line, and the ones that write to Terra refuse the shared
  baseline workspace unless you name `--allow-shared-target`. Recon, status, cost, fetch, all of
  `checks/` and all of `compare/` write nothing outside `$GSVTK_WORK`. That is still a write, though:
  recon dumps eight JSONs there, and `recon/*.json` is an inventory of your workspace.
- **Pick a push target that nothing reads.** The image-registry default is derived from your project plus
  a namespace segment. If a real pipeline reads the path you push a test build to, then that pipeline is
  now using your image. The tools warn about it, and beyond the warning it is your call.
- **The baseline workspace referenced in defaults is public.**
  `broad-firecloud-dsde-methods/GATK-Structural-Variants-Joint-Calling` is the featured GATK-SV
  workspace that gatk-sv's own documentation links to (see `website/docs/execution/joint.md` and
  `website/docs/advanced/build_ref_panel.md` in the gatk-sv repo). Override `GSVTK_BASELINE_*` to
  point at your own frozen run.
- **The publish guard uses *your* identifiers, not the author's.** `make audit` fails if the tracked tree
  holds a credential shape (a service-account address, a private key, a real home path) or any value that
  resolves to one of **your** coordinates (project, Terra workspace, registry path, checkout path) read
  back off your own configuration, minus anything that is a shipped default. No file is exempt, and
  nobody's names are shipped in the pattern list, so CI grades the shapes and your machine grades your
  own values. Run it before pushing, not only in CI.
  `make audit` grades the tracked tree, so a value that shipped once keeps passing forever: a later scrub
  makes the file clean while the published blob stays reachable. `make audit-history` grades the object
  store and every commit message instead, classified by exposure (`HEAD`/`STAGED` fail, `HISTORY`
  reported, `DANGLING` advisory), and `make audit-history PUBLISH=1` is the pre-push form. Why it exists:
  [`docs/static-checks.md`](docs/static-checks.md)
  ([CONTRIBUTING.md](CONTRIBUTING.md) has the rule about what may be added where).
- **`gs://` access is your own.** Staging and fetching use `gsutil` under your credentials.
  Some gatk-sv resource buckets are anonymously readable; baseline workspace buckets are not,
  which is why freezing copies them server-side rather than referencing them.
- **When something breaks, grep [docs/troubleshooting.md](docs/troubleshooting.md) for the error text
  you have** before searching the web. Every row is keyed on the verbatim message, which is the one
  thing you definitely have.
- **[docs/gap-ledger.md](docs/gap-ledger.md)** lists every item the three gap reviews asked for, what
  happened to each one, and the command that proves that claim, including the two items left out of
  scope and why. Commit messages cite the reviews; this is the tracked counterpart.

### What is here

```
docker/     gatk-sv-build.sh + remote-build.sh   build+push any branch's images, no local Docker
terra/      recon, baseline freezing, method configs, status, cost, call-level peek, fetch
            artifacts by workflow output name, compare  (Terra loop)
checks/     static checks: WDL launchability, WDL semantics + blast radius, sv_shell JSON
            contract, image byte-proof (and running a script inside an image)
compare/    13 comparators + a single-artifact tally (artifact_tally.py): keyed tables,
            site×sample matrices, VCF fields, sets, bundles
replay/     rebuild a launchable input JSON from a captured successful run
scripts/    fetch_wdl.py (WDLs from your checkout, never vendored), audit.py (publish guard)
kit/        the config layer every tool reads (gsvtk-config + config.sh + config.py)
examples/   worked drivers from a real investigation, kept as recipes
docs/       how each loop works, and the gotchas with their verbatim error text
.pi/skills/ the agent skill that drives this repo: SKILL.md + a read-only `gsvtk` wrapper
```

Run `make help` for the tool index, or `./kit/gsvtk-config show` to see the resolved configuration.

## Running the tests

```bash
make test
```

This is the gate on the toolkit itself, and the static checks above are tools that grade gatk-sv. Don't
confuse the two. `make test` needs no credentials, no data and no network, and it is the standard this
repo holds itself to. Seven phases, each added because the one before it let something through:

```bash
make syntax      # every .sh parses, every .py compiles
make undefmods   # attribute access on a module the file never imports
make flake       # pyflakes sweep: undefined names, unused values, imports that are not there
make helpsweep   # --help on every tool, with zero configuration
make smoke       # the tools actually run, end to end, on hostile fixtures
make audit       # the publish guard (see Things worth knowing)
make selftest    # config layer, both checkers, the shipped skill, the docs' structure, the probes
```

The phases that go beyond compiling are the ones that earned their place. `--help` never reaches
`main()`, and a comparator that was dead on every real invocation passed `py_compile` and the help sweep
and shipped. [CONTRIBUTING.md](CONTRIBUTING.md) has the table of what each part proves and what it caught.

### End to end tests

`make smoke` runs the tools against fixtures built to be hostile: empty files, and a table whose schema
matches but shares no key with its partner, which must exit 2 rather than report agreement.
`make selftest` adds a **canary**, which asserts that a known-failing command is reported as failing, and
a set of **probes** in `scripts/probe_fixes.py`, one per defect a review confirmed. Each
probe also runs a positive control proving the guarded path was reachable, because a guard that never
fired and a guard that could never fire produce the same output.

The probe count is pinned, and `WANT` counts the probes that must **run**: one that vanishes fails the
gate, and so does one that never ran because a dependency is missing. The SKIP line names the file to
install, and the gate will not pass on a smaller number. That last part is a correction. An earlier
version of the counter added the skip tally to the running tally, so "3 ok, 5 skipped" passed as 8, and
handoff §4 recorded the gap without closing it
([docs/handoff/002-module-profiles-and-quickstart.md](docs/handoff/002-module-profiles-and-quickstart.md)).
Read the `ok` counts, not just the pass/fail line.

### Coding style tests

There is no formatter here and no style argument to have; `make lint` is an alias of `make syntax`, and
the Makefile says why: no whitespace opinions. `make flake` is the rest of it, and it runs
`flake8 --select=F` so it reports real defects rather than taste: undefined names, imports that are not
there, values computed and then dropped. `make undefmods` covers the other half of `NameError`, the
attribute-on-a-missing-import case that `py_compile` accepts. Style conventions that matter are in
[CONTRIBUTING.md](CONTRIBUTING.md), which is also where the bar for a new tool lives.

## Costs

- A `sv-pipeline` build: `e2-standard-8`, roughly 1-3 hours, plus a 150 GB PD-SSD. Ephemeral mode
  deletes the VM on success; on failure it self-shuts-down and is kept so you can read the log.
- A Terra head-to-head step is a fleet of VMs over tens of minutes to hours. `terra/batch_cost.py`
  recomputes VM-minutes from saved Cromwell metadata, so a published number can be checked afterwards.
- Local replay costs time and disk. The staged 1KG matrices are tens of GB.

## Built With

What the tools are made of, and what you end up depending on:

* **Bash 3.2** for `kit/` and the shell drivers, because macOS still ships it and a bash-4 feature once
  broke the config shim silently.
* **Python 3.9+**, standard library first. Most of `compare/` is stdlib only; `numpy`, `pandas` and
  `pysam` are used where a table or a VCF makes them the short path.
* **[miniwdl](https://github.com/chanzuckerberg/miniwdl)** for WDL parsing, as a dev dependency, and
  `flake8` used only as a pyflakes runner.
* **[jq](https://stedolan.github.io/jq/)**, `bcftools`, `gsutil` and `gcloud` as external binaries. Each
  tool says what is missing and how to install it.
* **[Terra](https://terra.bio/) / FireCloud** through `firecloud` (fiss), pinned to the
  `api.firecloud.org` alias because `api.terra.bio` does not resolve on every network.
* **Google Compute Engine** for the build and probe VMs. No local docker anywhere.
* **[GATK](https://github.com/broadinstitute/gatk)** jars for local replay, built by you from your own
  checkout.
* **[pi](https://github.com/badlogic/pi-mono)** for the agent skill in `.pi/skills/`.

## Contributing

`make test` before pushing; see [CONTRIBUTING.md](CONTRIBUTING.md). A new tool has to either remove an
hour of waiting or catch a bug before it costs VM time. If it does neither, it probably belongs in
gatk-sv itself.

## License

BSD 3-Clause, Talkowski Lab. See [LICENSE](LICENSE). This toolkit is separate from and not endorsed by
the Broad Institute.

## Acknowledgments

The design follows GATK-SV's documented flows: manual docker deployment, the numbered joint-calling
workspace, the `wdl/` and `src/sv_shell` layouts. Concordance work is expected to be published with
[`gatk-sv-profile`](https://github.com/broadinstitute/gatk-sv-profile); the comparators here exist
because its tables are bucketed and a figure lifted from one needs its aggregation rule written down.

Session records from the investigation that produced this toolkit are in
[docs/archive/](docs/archive/), including the parts that turned out to be wrong.
[docs/methodology.md](docs/methodology.md) explains why they are kept.
