# gatk-sv-testkit

Build, replay and validation tooling for [GATK-SV](https://github.com/broadinstitute/gatk-sv)
development — the parts of a gatk-sv change that are slow, expensive or impossible to check
with the pipeline itself.

It exists because of a specific problem: a gatk-sv change is normally validated by launching
the full pipeline on real data and waiting. That costs hours of VM time per experiment, needs
a Docker daemon gatk-sv's build does not run on Apple Silicon, and — for `src/sv_shell`, which
has no CI at all — cannot detect a renamed JSON key until the job dies several stages later.
Every tool here shortens one of those loops.

Nothing in this repo is part of GATK-SV, is endorsed by the Broad Institute, or is required to
run it. It is a developer's toolbox *around* the pipeline.

## The four loops it shortens

| Loop | Without this | With this | Docs |
|---|---|---|---|
| **Build a docker image from a branch** | Install Docker Desktop, or hand-run the manual build on a VM; ~1-3 h; Apple Silicon cannot build `linux/amd64` locally at all | `docker/gatk-sv-build.sh <branch>` — one throwaway x86_64 GCE VM, no local Docker, streams the log, deletes itself | [docker builds](docs/docker-builds.md) |
| **Compare a branch against a real baseline run** | Re-run the whole pipeline twice and hope the inputs matched | Freeze one baseline's inputs, run only the changed stage, diff the tables and VCFs that came out | [Terra head-to-head](docs/terra-head-to-head.md) |
| **Test a genotyper change without any cloud spend** | Not possible without a Terra run | `stage_inputs.py` pulls the exact frozen inputs; a locally built GATK jar runs the real trainer on them | [local replay](docs/local-replay.md) |
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

Then pick a loop. The two most common first runs:

```bash
# build a branch's sv-pipeline image on a throwaway x86 VM, no local Docker
docker/gatk-sv-build.sh --dry-run my-branch    # shows what it would do, touches nothing
docker/gatk-sv-build.sh my-branch              # ~1-3 h, streams the build log

# is my branch's WDL actually launchable? (needs miniwdl, takes seconds)
checks/wdl_gate.sh origin/main my-branch
```

## What is here

```
docker/     gatk-sv-build.sh + remote-build.sh   build+push any branch's images, no local Docker
terra/      recon, baseline freezing, method configs, status, cost, fetch+compare  (Terra loop)
checks/     static checks: WDL launchability, sv_shell JSON contract, image byte-proof
compare/    table differ, VCF concordance, GQ scale, gatk-sv-profile summariser
replay/     rebuild a launchable input JSON from a captured successful run
scripts/    fetch_wdl.py (WDLs from your checkout, never vendored)
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
  [pi](https://github.com/badlogic/pi-mono) skill (also usable by any harness that reads
  `.pi/skills/`): how to locate a checkout, which loop answers which question, and what is safe to
  run unattended. Its wrapper dispatches **only** read-only modes — `submit`, `create`, `copy`,
  `attrs --write`, `fetch`, `profile` are refused before anything else runs, so an agent working from
  the skill cannot reach a POST or a VM by accident. `make selftest` runs `scripts/check_skill.py`,
  which executes those refusals and compares them to the prose, so "submit is refused" stays a
  checked claim rather than a sentence.
- **Read-only by default.** Every mutating helper in `terra/` needs `confirm=True`, the ones that
  start compute additionally refuse without a `--confirm` flag on the command line, and the ones
  that write to Terra refuse the shared baseline workspace unless you name `--allow-shared-target`.
  Recon, status, cost, fetch, all of `checks/` and all of `compare/` never write **outside
  `$GSVTK_WORK` (recon dumps eight JSONs there — that is the point of running it, but they
  are writes, and `recon/*.json` contains your workspace inventory).
- **Name a push target that nothing reads.** The image-registry default is derived from your
  project plus a namespace segment. Pointing a test build at a registry path a real pipeline
  reads turns your experiment into that pipeline's image. The tools warn; the guard rail is you.
- **The baseline workspace referenced in defaults is public.**
  `broad-firecloud-dsde-methods/GATK-Structural-Variants-Joint-Calling` is the featured GATK-SV
  workspace that gatk-sv's own documentation links to (see `website/docs/execution/joint.md` and
  `website/docs/advanced/build_ref_panel.md` in the gatk-sv repo). Override `GSVTK_BASELINE_*` to
  point at your own frozen run.
- **`checks/` findings need triage, they are not a pass/fail oracle.** Run against gatk-sv as
  it stands, the `sv_shell` contract check reports several unsupplied reads (mostly gcnv
  hyperparameters arriving from elsewhere) and the WDL gate reports a handful of
  `IncompleteCall` warnings. The value is the *diff* against the ref you are changing. See
  [docs/static-checks.md](docs/static-checks.md).
- **`gs://` access is your own.** Staging and fetching use `gsutil` under your credentials.
  Some gatk-sv resource buckets are anonymously readable; baseline workspace buckets are not,
  which is why freezing copies them server-side rather than referencing them.

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
