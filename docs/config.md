# Configuration

Every tool asks one program for environment-specific values, so "my project", "my workspace"
and "my registry" mean the same thing everywhere and are decided in exactly one place:
`kit/gsvtk-config`.

```bash
cp testkit.env.example testkit.env
./kit/gsvtk-config doctor      # what is set, what is missing, which files were read
./kit/gsvtk-config show        # every key, its value and where that value came from
./kit/gsvtk-config work runs/x # print (and create) a scratch dir under $GSVTK_WORK
./kit/gsvtk-config miniwdl     # the miniwdl the WDL tools will use (empty if there is none)
```

`work` prints **the directory it created, subdirs included** — `OUT="$(gsvtk_work runs/train_full)"`
has to come back as `<work>/runs/train_full`. It used to create `runs/train_full` and print the work
root, so two runs' `OUT` variables pointed at the same directory and overwrote each other's outputs
while every log claimed its own. `kit/config.sh`'s `gsvtk_work` is a wrapper around this command, so
the shell helpers and the Python tools cannot drift apart.

## Precedence

Highest first:

1. **An exported environment variable** — `GSVTK_PROJECT=other-project docker/gatk-sv-build.sh …`
   for a one-off against a different project.
2. **A profile file.** An explicit `$GSVTK_CONFIG` **replaces** this list rather than joining it —
   it has to, or a repo-local `testkit.local.env` would keep winning over the very override you
   reach for to escape it, and "no configuration" could not be produced at all. Otherwise, later
   files win over earlier ones:
   - `./testkit.env` — this checkout (gitignored; the normal place)
   - `<repo>/testkit.local.env` — machine-local, found from the checkout rather than from your
     current directory (gitignored)
   - `~/.config/gatk-sv-testkit/env` — per-user, all checkouts
3. **A derived value** — e.g. the image registry from the project + namespace.
4. **A built-in default.**

The profile format is plain `KEY=value` so bash can source it and Python can parse it with no
dependency. `export KEY=value` and `KEY="value"` also work, and a bare `PROJECT=` is accepted
as well as `GSVTK_PROJECT=`.

**Provenance is per-value and names the file**, because the chain is last-wins: `show` prints
`[profile:/home/you/.config/gatk-sv-testkit/env]`, not `[profile]`. Two files both setting
`GSVTK_TERRA_WORKSPACE` is otherwise invisible, and that key decides which workspace a `copy --write`
or `attrs --write` will modify — you need to see *which file* answered. `doctor` lists the files it
read (including the ones that do not exist); `show` prints the winner and where it came from.

A shell tool sourcing `kit/config.sh` never hard-fails on a broken resolver — that is what keeps
`--help` working with zero configuration — but it **warns on stderr and prints the resolver's own
error** when it produced no exports. Silent fallback to inline defaults is how a tool ends up running
against the wrong project while looking healthy.

## Why two keys refuse to have defaults

`GSVTK_PROJECT` and the Terra workspace pair are the only keys with no built-in default, and
that is deliberate:

- the project decides **whose billing account** a VM runs under;
- the workspace decides **whose method configs and entity attributes** get overwritten.

A guessed default in either slot means either spending someone else's money or writing into
someone else's workspace. `require` exits with code 4 and prints which file to edit.

Everything else is either public (`github.com/broadinstitute/gatk-sv`, the Terra API alias, the
featured baseline workspace, the `all_samples` entity name used by gatk-sv's reference-panel
workspace) or safely derivable.

## Keys

| Key | Default | Used by | Notes |
|---|---|---|---|
| `GSVTK_PROJECT` | *(required)* | docker, image checks | runs the builder/probe VMs; also the GCR home by default. No `terra/` tool reads it — Terra charges the workspace's own project |
| `GSVTK_ZONE` | `us-central1-a` | docker, image checks | gatk-sv images are `linux/amd64`; keep this on native amd64 stock |
| `GSVTK_MACHINE_TYPE` | `e2-standard-8` | docker, image checks | gatk-sv's manual says `e2-standard-2` is the minimum; 8 is much faster for `sv-pipeline` |
| `GSVTK_DISK_GB` | `150` | docker | docs ask for ~100; layer cache for a full chain wants more |
| `GSVTK_IMAGE_NAMESPACE` | `test` | docker | the guard-rail segment: `us.gcr.io/<project>/<ns>/gatk-sv` |
| `GSVTK_IMAGE_REPO` | derived | docker, `batch_rerun_step.py` | override to name the push target exactly; set explicitly it also satisfies the rerun's pinned-image gate | override to name the push target exactly |
| `GSVTK_GATK_IMAGE_REPO` | derived | docker (`--gatk`), `batch_rerun_step.py` | | where *your* GATK java image goes |
| `GSVTK_GATK_SV_REPO_URL` | `https://github.com/broadinstitute/gatk-sv` | docker | what the VM clones |
| `GSVTK_GATK_REPO_URL` | `https://github.com/broadinstitute/gatk` | docker (`--gatk`) | |
| `GSVTK_GATK_SV_CHECKOUT` | *(unset)* | checks, fetch_wdl, batch input gate, `batch_configs.py check` | the clone whose bytes you are testing. Without it, `create`/`validate` print `pre-check SKIPPED` — the binding-vs-WDL comparison did not run, which is not a pass |
| `GSVTK_GATK_CHECKOUT` | *(unset)* | local replay | used to discover a locally built jar |
| `GSVTK_TERRA_API_ROOT` | `https://api.firecloud.org/api/` | terra | `api.terra.bio` does not resolve on every network; this alias does |
| `GSVTK_TERRA_NAMESPACE` / `_WORKSPACE` | *(required)* | terra | your sandbox: where configs are POSTed and outputs written |
| `GSVTK_BASELINE_NAMESPACE` / `_WORKSPACE` | the public featured GATK-SV workspace | terra recon/freeze/cost | the reference side of the comparison |
| `GSVTK_BRANCH` | *(unset)* | configs, WDL gate, labels | the gatk-sv branch under test |
| `GSVTK_FROZEN_SUFFIX` | `frz` | freeze, configs, fetch | attribute suffix for frozen baseline inputs |
| `GSVTK_NEW_SUFFIX` | `new` | configs, fetch | attribute suffix for this chain's outputs |
| `GSVTK_BATCH` | `all_samples` | freeze, configs, rerun, fetch+compare, `fetch_baseline.py --entity`, `stage_inputs.py --attrs`, `diff_rd_states.py` | the `sample_set` entity holding batch-level attributes. Every reader now takes it from here; when the configured row is absent from a frozen manifest, `stage_inputs.py` stops and names the rows that exist rather than selecting nothing at exit 0 |
| `GSVTK_WORK` | `<repo>/work` | everything that writes | scratch: staged inputs, runs, manifests, fetched outputs |

Set the two suffixes once and keep them consistent: `batch_configs.py` writes outputs to
`*{NEW_SUFFIX}` and `batch_fetch_compare.sh` reads the new side from the same suffix. If they
disagree you get an empty comparison, not an error — which is worse.

## Tool config that is not in the profile

Things that are per-invocation rather than per-user stay as flags or their own variables:

| Variable | Tool | Meaning |
|---|---|---|
| `FISS_API_URL` | `terra/terra.py` | last-word override of the API root, above `GSVTK_TERRA_API_ROOT` |
| `JAVA` | `batch_check_inputs.py`, `batch_fetch_compare.sh` | the `java` used for womtool and for the GATK jar |
| `GSVTK_PYTHON` | `kit/config.sh` | interpreter used to resolve config (>=3.9) |
| `WOMTOOL_JAR` | `terra/batch_check_inputs.py` | path to a womtool jar; required, and its absence is reported as a missing prerequisite, not a crash |
| `MINIWDL` | `checks/wdl_gate.sh`, `terra/wdl_flat.py` | the miniwdl executable, and it is optional: without it both resolve `$PATH` → the bin next to the interpreter → `./.venv/bin` (one resolver, `./kit/gsvtk-config miniwdl`, prints the path it chose). `make setup` installs miniwdl into a venv your shell has not activated, so a PATH-only lookup reported the checker missing on machines where it passes |
| `JAR`, `GATK_JAR` | examples, `batch_fetch_compare.sh` | a locally built GATK jar |
| `PROFILE_BIN` | `batch_fetch_compare.sh` | an installed `gatk-sv-profile` |
| `GSV_WDL_VERSION` | `terra/batch_rerun_step.py` | override the Dockstore version, e.g. to pin a SHA-pinned tag. It is also the ref the rerun pre-check compares bindings against — not `GSVTK_BRANCH`, which may name something else entirely, and grading the wrong ref is a pass about a document nothing runs |
| `CAPTURED`, `IMAGES` | `examples/replay_reference_run.sh` | local copy of that task's Cromwell `script`, and the per-arm image map |
| `RESULTS` | `batch_fetch_compare.sh` | scratch root; `OUTPUTS`/`STAGING` and its other knobs (`TERRA_PY`, `GSUTIL`, `GATK_BIN`, `XMX`, `NUM_WORKERS`, `LABEL_A`, `LABEL_B`, `REF_DICT`, `CONTIG_LIST`, `BASELINE_PESR_VCF`, `NS`, `WS`, `ENTITY`) derive from it or are named in that script's own header |

## Work directory

`GSVTK_WORK` holds everything regenerable: `staging/` (frozen inputs, tens of GB), `runs/`
(local replay output), `manifests/` (frozen gs:// coordinates), `recon/` (Terra dumps),
`outputs/` (fetched callsets), `metadata/` (Cromwell metadata), `wdl/` and `wdl-gate/`
(per-ref materialized WDL trees), `replay/`, `tables/`, `reports/`.

Point it at a roomy disk. It is gitignored at every depth, and `make clean-work` prints what
it would delete without deleting anything — hardlinked staging trees make `du` misleading about
what is actually yours.

Nothing creates these directories as a side effect of printing usage. Python tools build scratch
paths with `config.work_path()` (a path, no `mkdir`) and the write sites call `work_dir()` when
bytes are about to land; `gsvtk-config work` / `gsvtk_work` still create, because shell callers
assign their output directory from what it prints. The Python half is pinned by the
`help_writes_nothing` probe, because import-time `work_dir()` meant `--help` left
`manifests/` and `staging/` behind — and a typo in `GSVTK_WORK` was invisible until something was
written into the wrong tree.

## Changing a value that is baked into a Terra run

`GSVTK_BRANCH` and the two suffixes appear inside the method configs already created in your
workspace. Changing them does not rewrite existing configs — rerun `batch_configs.py create`
(and `batch_freeze.py attrs` if the frozen suffix changed), or you will be comparing a new
profile against old outputs. `batch_configs.py show` prints what the current profile *would*
create, which is the fastest way to see the difference before mutating anything.
