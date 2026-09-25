# gatk-sv-testkit

**[GATK-SV](https://github.com/broadinstitute/gatk-sv) is an ambitious piece of engineering: one
pipeline that carries structural-variant calling from raw reads to a whole cohort. This toolkit sits
around it: offline checks, a build harness and comparison tools that shorten the feedback loop when
you change something.**

That breadth is what makes it useful, and it is also what makes feedback slow. The usual way to validate
a change is to launch the pipeline and wait: hours of VM time, a Docker build that Apple Silicon cannot
run natively, and — in the `src/sv_shell` layer, which gatk-sv's CI does not cover — a renamed JSON key
that only shows itself several stages into a live run. Each tool here removes one of those waits: a
question answered offline in seconds, or a build that runs where your laptop cannot. A full run is
still the last word, and nothing here claims otherwise.

Nothing in this repo is part of GATK-SV, is endorsed by the Broad Institute, or is required to run it.
It is a developer's toolbox *around* the pipeline.

## What you can do with it

* **Catch a wiring break in seconds, before you launch anything.** A workflow can pass `miniwdl check` and still
  be unlaunchable, because a call site never passes a required input or passes one the callee deleted;
  `checks/wdl_gate.sh` turns both into numbers you can diff between two refs. And if a key gets renamed
  in `src/sv_shell`, `jq` hands the *string* `"null"` to a module several stages later — after you have
  paid for the VMs. `checks/svshell_contract_check.py` finds it before you submit anything.
* **Know whether your change moved the numbers, with a verdict per column.** Not "here are two tables,
  good luck": `MATCH`, `DELTA`, `MISSING`, and `STRATEGY` for a value that changed *on purpose*, where
  diffing raw means would be a lie. Point it at empty directories and it reports that it compared
  nothing and exits nonzero, because an empty table that reads like "no differences" is the failure this
  repo fears most. It also documents the traps that already burned someone — two quality fields that are
  the same measurement on different scales, where "the new pipeline lost 90% of the signal" is really a
  10× unit change ([docs/comparators.md](docs/comparators.md)).
* **Build your branch's image without Docker on your laptop.** One throwaway x86_64 VM does the build,
  streams the log, pushes, and deletes itself on success. `--check` and `--dry-run` cost nothing and tell
  you whether the build would even start.
* **Fail before you spend, not after.** A method-config map can match one branch's WDL and not the branch
  you are actually testing, and the cloud rejects the whole config at submission — after it sat in your
  workspace looking fine. `terra/batch_configs.py check --against main` catches that offline, in seconds,
  against your own checkout.
* **Run the real trainer locally on a real run's exact inputs**, with a jar you built yourself. No cloud,
  no data movement, nothing to rent.
* **Never guess where a setting came from.** `show` and `doctor` print every value with its source
  (environment, profile file, derived, default). The two settings that decide *whose bill it is* and
  *whose workspace gets written* have no default at all: the tools stop and name the missing key rather
  than invent one.
* **Hand it to an agent without holding its hand.** The repo ships the skill that drives it, and the
  wrapper dispatches only read-only modes — the ones that POST or start compute are refused before
  anything else runs.

## A normal session

Change a WDL or a shell script → run the two static checks → build the image → run the same pipeline step
before and after → diff the tables and VCFs → publish a sentence with a number in it that somebody else
can reproduce.

## What it is not

It does not call variants; that is gatk-sv. It does not host your data, and it will not quote you a
price — costs are stated as machine type and wall-clock, because your billing account and discounts are
not ours to read. Its findings need triage rather than a green light: run against gatk-sv as it stands,
the checks report several unsupplied reads and a handful of `IncompleteCall` warnings *on purpose*, so
the value is the **diff** against the ref you are changing
([docs/static-checks.md](docs/static-checks.md)). And the run-the-pipeline parts are shaped around the
Genotyping module today — generalising them to any module is
[docs/module-profiles.md](docs/module-profiles.md), which is a proposal, not a feature.

## The four loops it shortens

| Loop | Without this | With this | Docs |
|---|---|---|---|
| **Build a docker image from a branch** | Install Docker Desktop, or hand-run the manual build on a VM; ~1-3 h; Apple Silicon cannot build `linux/amd64` locally at all | `docker/gatk-sv-build.sh <branch>` — one throwaway x86_64 GCE VM, no local Docker, streams the log, deletes itself | [docker builds](docs/docker-builds.md) |
| **Compare a branch against a real baseline run** | Re-run the whole pipeline twice and hope the inputs matched | Freeze one baseline's inputs, run only the changed stage, diff the tables and VCFs that came out | [Terra head-to-head](docs/terra-head-to-head.md) |
| **Test a genotyper change without any cloud spend** | Not possible without a Terra run | `terra/stage_inputs.py` pulls the exact frozen inputs; a locally built GATK jar runs the real trainer on them | [local replay](docs/local-replay.md) |
| **Catch a WDL / `sv_shell` break before submitting** | Discover it mid-submission, after VMs booted | `checks/` — static: seconds, no data, no docker, no network (`image-check/` is the deliberate exception: it asks a shipped image) | [static checks](docs/static-checks.md) |

## Quickstart

```bash
git clone https://github.com/talkowski-lab/gatk-sv-testkit.git
cd gatk-sv-testkit
make setup                          # .venv with the python dependencies

cp testkit.env.example testkit.env  # then edit it: project + workspace are yours to name
./kit/gsvtk-config doctor           # tells you exactly what is still missing

make test                           # the offline gate: syntax, undefined-module refs, a pyflakes
                                    # bug sweep, `--help` on every CLI, real end-to-end invocations,
                                    # self-tests + canary + probes for every confirmed defect
```

`make test` runs with no credentials, no data and no network, and it is the gate this repo holds itself
to. Its probes are worth understanding: each one reproduces a defect a review confirmed and then proves
the guarded path was reachable, so "the guard never fired" cannot be mistaken for "the guard worked".
The probe count is pinned — if a probe disappears, the gate fails. One known gap, recorded rather than
hidden: a probe *skipped* for a missing dependency still satisfies that count
([docs/handoff/002-module-profiles-and-quickstart.md](docs/handoff/002-module-profiles-and-quickstart.md),
§4), so `ok` counts matter more than the pass/fail line.

Then pick a loop. The guided tour of all four, with the real output each step prints, is
[docs/quickstart.md](docs/quickstart.md) — every command in it runs with no credentials and no data.
The two most common first runs:

```bash
# build a branch's sv-pipeline image on a throwaway x86 VM, no local Docker
docker/gatk-sv-build.sh --dry-run my-branch    # shows what it would do, touches nothing
docker/gatk-sv-build.sh my-branch              # ~1-3 h, streams the build log

# is my branch's WDL actually launchable? (refs are in your gatk-sv checkout; needs miniwdl,
# takes seconds)
checks/wdl_gate.sh origin/main my-branch
```

## What is here

```
docker/     gatk-sv-build.sh + remote-build.sh   build+push any branch's images, no local Docker
terra/      recon, baseline freezing, method configs, status, cost, fetch+compare  (Terra loop)
checks/     static checks: WDL launchability, sv_shell JSON contract, image byte-proof
compare/    table differ, VCF concordance, GQ scale, gatk-sv-profile summariser
replay/     rebuild a launchable input JSON from a captured successful run
scripts/    fetch_wdl.py (WDLs from your checkout, never vendored), audit.py (publish guard)
kit/        the config layer every tool reads (gsvtk-config + config.sh + config.py)
examples/   worked drivers from a real investigation, kept as recipes
docs/       how each loop works, and the gotchas with their verbatim error text
.pi/skills/ the agent skill that drives this repo: SKILL.md + a read-only `gsvtk` wrapper
```

Run `make help` for the tool index, or `./kit/gsvtk-config show` to see the resolved configuration.

## Configuration

One profile, read by every tool, resolved in one place (`kit/gsvtk-config`):

```
environment variable  >  testkit.env  >  derived  >  built-in default
```

Two values have **no default at all**: the GCP project and the Terra workspace. They decide
whose money is spent and whose workspace is written to, so the tools stop and name the missing
key instead of guessing. See [docs/config.md](docs/config.md).

Scratch output (staged inputs, replay runs, frozen manifests, fetched callsets — tens of GB)
goes under `GSVTK_WORK` and is gitignored. Nothing the tools produce is committed.

## Things worth knowing before you start

- **The repo ships the skill that drives it.** `.pi/skills/gatk-sv-testkit/` is a
  [pi](https://github.com/badlogic/pi-mono) skill, also usable by any harness that reads `.pi/skills/`:
  how to locate a checkout, which loop answers which question, what is safe to run unattended. Its
  wrapper dispatches **only** read-only modes — `submit`, `create`, `copy`, `attrs --write`, `fetch`,
  `profile` are refused before anything else runs, so an agent cannot reach a POST or a VM by accident.
  `make selftest` runs `scripts/check_skill.py`, which *executes* those refusals and compares them to the
  prose, so "submit is refused" stays a checked claim rather than a sentence.
- **Read-only by default.** Mutating helpers in `terra/` need `confirm=True`; the ones that start compute
  also refuse without `--confirm` on the command line; the ones that write to Terra refuse the shared
  baseline workspace unless you name `--allow-shared-target`. Recon, status, cost, fetch, all of `checks/`
  and all of `compare/` write nothing outside `$GSVTK_WORK` — which is still a write: recon dumps eight
  JSONs there, and `recon/*.json` is an inventory of your workspace.
- **Name a push target that nothing reads.** The image-registry default is derived from your
  project plus a namespace segment. Pointing a test build at a registry path a real pipeline
  reads turns your experiment into that pipeline's image. The tools warn; the guard rail is you.
- **The baseline workspace referenced in defaults is public.**
  `broad-firecloud-dsde-methods/GATK-Structural-Variants-Joint-Calling` is the featured GATK-SV
  workspace that gatk-sv's own documentation links to (see `website/docs/execution/joint.md` and
  `website/docs/advanced/build_ref_panel.md` in the gatk-sv repo). Override `GSVTK_BASELINE_*` to
  point at your own frozen run.
- **The publish guard uses *your* identifiers, not the author's.** `make audit` fails if the tracked tree
  holds a credential shape (a service-account address, a private key, a real home path) or any value that
  resolves to one of **your** coordinates — project, Terra workspace, registry path, checkout path — read
  back off your own configuration, minus anything that is a shipped default. No file is exempt, and
  nobody's names are shipped in the pattern list, so CI grades the shapes and your machine grades your
  own values. Run it before pushing, not only in CI
  ([CONTRIBUTING.md](CONTRIBUTING.md) has the rule about what may be added where).
- **`gs://` access is your own.** Staging and fetching use `gsutil` under your credentials.
  Some gatk-sv resource buckets are anonymously readable; baseline workspace buckets are not,
  which is why freezing copies them server-side rather than referencing them.
- **When something breaks, grep [docs/troubleshooting.md](docs/troubleshooting.md) for the verbatim
  error text** before searching the web. It is keyed on exact strings, which is what you actually have.

## Costs, stated plainly

- A `sv-pipeline` build: `e2-standard-8`, roughly 1-3 hours, plus a 150 GB PD-SSD. Ephemeral
  mode deletes the VM on success; on failure it self-shuts-down and is kept for you to read.
- A Terra head-to-head step is a fleet of VMs over tens of minutes to hours. `terra/batch_cost.py`
  recomputes VM-minutes from saved Cromwell metadata so a published number stays checkable.
- Local replay costs nothing but time and disk: the staged 1KG matrices are tens of GB.

## Attribution and licence

The design follows GATK-SV's documented flows (manual docker deployment, the numbered joint-calling
workspace, the `wdl/` and `src/sv_shell` layouts). This toolkit is separate from and not endorsed by
the Broad Institute. See [LICENSE](LICENSE).

Session records from the investigation that produced this toolkit are in
[docs/archive/](docs/archive/) — including the parts that turned out to be wrong, and the
[why behind that choice](docs/methodology.md).

## Contributing

`make test` before pushing; see [CONTRIBUTING.md](CONTRIBUTING.md). The bar for a new tool here
is "it removes an hour of waiting or catches a bug before it costs VM time" — if it does neither,
it probably belongs in gatk-sv itself.
