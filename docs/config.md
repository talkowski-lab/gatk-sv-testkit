# Configuration

Every tool reads environment-specific values from one resolver, `kit/gsvtk-config`, so "my project",
"my workspace" and "my registry" mean the same thing everywhere. Python tools call it directly; shell
tools source `kit/config.sh`, which wraps it.

```bash
cp testkit.env.example testkit.env
./kit/gsvtk-config doctor          # what is set, what is missing, which files were read
./kit/gsvtk-config show            # every key, its value, and where the value came from
./kit/gsvtk-config get PROJECT     # one value (empty if unset)
./kit/gsvtk-config require PROJECT # one value, or exit 4 with instructions
./kit/gsvtk-config env             # eval-able `export GSVTK_...` lines
./kit/gsvtk-config work runs/x     # create and print a scratch directory under GSVTK_WORK
./kit/gsvtk-config miniwdl         # the miniwdl the WDL tools will use (empty if none)
```

## Precedence

Highest first:

1. **An exported environment variable**, e.g. `GSVTK_PROJECT=other-project docker/gatk-sv-build.sh my-branch`.
2. **A profile file.** When several exist, the later one in this list wins:
   - `./testkit.env` in the current directory
   - `<repo>/testkit.local.env`, found from the checkout rather than the current directory
   - `~/.config/gatk-sv-testkit/env`, per user, for all checkouts

   So the order is: environment > `~/.config/gatk-sv-testkit/env` > `<repo>/testkit.local.env` >
   `./testkit.env`. If `GSVTK_CONFIG` is set, that one file replaces the whole list. Pointing it at an
   empty file gives you an unconfigured run.
3. **A derived value**, e.g. the image registry from the project.
4. **A built-in default.**

Profiles are plain `KEY=value` lines, so bash can source them and Python can parse them without
dependencies. `export KEY=value`, quoted values and trailing `# comments` are accepted. `GSVTK_PROJECT=`
and bare `PROJECT=` are the same key; if two files set it, the higher-precedence file wins.

`show` names the exact file each value came from (`[profile:~/.config/gatk-sv-testkit/env]`), which is
how you notice two files setting `GSVTK_TERRA_WORKSPACE`. `doctor` lists every file it looked for,
including missing ones.

If `kit/config.sh` cannot run the resolver, shell tools still start (so `--help` works without
configuration) but print a warning and the resolver's error on stderr.

## Keys without a default

`GSVTK_PROJECT`, `GSVTK_TERRA_NAMESPACE` and `GSVTK_TERRA_WORKSPACE` have no default. The project
decides whose billing account runs VMs; the workspace decides whose method configs and entity
attributes get overwritten. A tool that needs one of them and finds it unset exits 4 and names the key
and the files where it can be set.

## Keys

All keys are read as `GSVTK_<KEY>` from the environment, or as `GSVTK_<KEY>` or `<KEY>` in a profile.

| Key | Default | Used by | Notes |
|---|---|---|---|
| `GSVTK_PROJECT` | required | docker builds, image checks | runs the builder and probe VMs, and is the default registry home. Terra tools don't read it: Terra bills the workspace's project |
| `GSVTK_ZONE` | `us-central1-a` | docker builds, image checks | pick a zone with amd64 capacity |
| `GSVTK_MACHINE_TYPE` | `e2-standard-8` | docker builds, image checks | gatk-sv documents `e2-standard-2` as the minimum |
| `GSVTK_DISK_GB` | `150` | docker builds | boot disk size |
| `GSVTK_IMAGE_NAMESPACE` | `test` | docker builds | registry path segment that is yours, so a test push cannot overwrite a production image |
| `GSVTK_IMAGE_REPO` | derived: `us.gcr.io/<project>/<namespace>/gatk-sv` | docker builds, `batch_rerun_step.py` | push target for gatk-sv images. Set explicitly with a tag, it also counts as an image pin for reruns |
| `GSVTK_GATK_IMAGE_REPO` | derived: `us.gcr.io/<project>/<namespace>/gatk` | docker builds (`--gatk`), `batch_rerun_step.py` | push target for the GATK Java image |
| `GSVTK_GATK_SV_REPO_URL` | `https://github.com/broadinstitute/gatk-sv` | docker builds | what the build VM clones |
| `GSVTK_GATK_REPO_URL` | `https://github.com/broadinstitute/gatk` | docker builds (`--gatk`) | |
| `GSVTK_GATK_SV_CHECKOUT` | unset | static checks, `scripts/fetch_wdl.py`, `batch_configs.py check`, `batch_check_inputs.py` | your local gatk-sv clone. Without it, `create` and `validate` print `pre-check SKIPPED`, which is not a pass |
| `GSVTK_GATK_CHECKOUT` | unset | local replay | local gatk clone, used to find a built jar |
| `GSVTK_TERRA_API_ROOT` | `https://api.firecloud.org/api/` | `terra/` | `api.terra.bio` does not resolve on every network; this alias does |
| `GSVTK_TERRA_NAMESPACE` / `GSVTK_TERRA_WORKSPACE` | required | `terra/` | your sandbox workspace: where configs are POSTed and outputs written |
| `GSVTK_BASELINE_NAMESPACE` / `GSVTK_BASELINE_WORKSPACE` | `broad-firecloud-dsde-methods` / `GATK-Structural-Variants-Joint-Calling` | `terra/` recon, freeze, cost | the reference run, read only. The default is the public featured GATK-SV workspace |
| `GSVTK_BRANCH` | unset | method configs, WDL gate, labels | the gatk-sv branch under test |
| `GSVTK_FROZEN_SUFFIX` | `frz` | freeze, configs, fetch | entity-attribute suffix for frozen baseline inputs |
| `GSVTK_NEW_SUFFIX` | `new` | configs, fetch | entity-attribute suffix for this chain's outputs |
| `GSVTK_BATCH` | `all_samples` | freeze, configs, rerun, fetch and compare, `fetch_baseline.py`, `stage_inputs.py` | the `sample_set` entity holding batch-level attributes. `stage_inputs.py --attrs` exits 2 if this row is not in the frozen manifest |
| `GSVTK_MODULE` | `genotyping` | `terra/`, `kit/module_profile.py` | which module the tools drive; its profile is `<MODULE_DIR>/<MODULE>.json`. `./gsvtk --module NAME` sets it |
| `GSVTK_MODULE_DIR` | derived: `<repo>/profiles` | same | directory holding module profiles |
| `GSVTK_WORK` | `<repo>/work` | everything that writes | scratch tree; see below |

Keep the two suffixes consistent: `batch_configs.py` writes outputs to `<attr>_<NEW_SUFFIX>` and
`batch_fetch_compare.sh` reads the same suffix. If they disagree you get an empty comparison instead of
an error. Module profiles write the suffixes as `{frz}` and `{new}`, and `kit/module_profile.py` expands
them.

## Image entries in a module profile

The two image-repo keys name at most two refs, while a step can bind a dozen `*_docker` inputs. The
per-step `images` map in `profiles/<module>.json` carries the rest (see
[module-profiles.md](module-profiles.md)). One field there needs explaining:

`versioned_by: "path"` on an `images` entry (`{"ref": "…", "versioned_by": "path"}`) says the ref's
version is part of its path, so it has no tag and needs none. `marketplace.gcr.io/google/ubuntu1804`,
a GCE image family, is the example. `terra/batch_rerun_step.py` then accepts the key as pinned and labels
it `+PATH-VERSIONED (profile versioned_by)`. It exits 4 if the value is anything other than `path`, if
the ref's last path segment has no version token, if the ref already has a tag or digest, or if the entry
also uses `--image`. This is unrelated to `--allow-unpinned-docker`, which lets a workspace attribute
supply the image.

## Per-invocation variables

These are not profile keys. Set them in the environment for one run.

| Variable | Read by | Meaning |
|---|---|---|
| `GSVTK_CONFIG` | `kit/gsvtk-config` | one profile file that replaces the profile chain |
| `GSVTK_READ_ONLY` | `gsvtk` | refuse every mode that POSTs, boots compute or bulk-downloads ([cli.md](cli.md)) |
| `GSVTK_TERRA_PY`, `GSVTK_PY` | `gsvtk` | interpreter for `terra` modes (must import `firecloud`) and for the other tools |
| `GSVTK_PYTHON` | `kit/config.sh` | interpreter used to run the resolver (3.9 or later) |
| `GSVTK_DOCKSTORE_API_ROOT` | `gsvtk publish-check` | Dockstore API root (default `https://dockstore.org/api/`) |
| `GSVTK_PUBLISH_CAPTURE`, `GSVTK_PUBLISH_TIP` | `terra/batch_rerun_step.py` | a Dockstore capture and branch tip for the publish check, read from files, never fetched |
| `GSVTK_GATK_SV_REF` | `checks/terra_entity_check.py` | default ref when `--ref` is not given (otherwise `HEAD`) |
| `GSVTK_SEMANTICS_JOBS` | `checks/wdl_gate.sh` | parallel jobs for the semantics pass (default 0, automatic) |
| `GSVTK_RENDER_PYTHON` | `checks/wdl_inputs_check.py` | interpreter used to render gatk-sv's input JSONs (needs `jinja2`) |
| `GSVTK_CAPTURE_STEP` | `docker/gatk-sv-build.sh` | which step's image keys a build's capture is written for (default `01`) |
| `GSVTK_WOMTOOL_INPUTS_JSON` | `terra/batch_check_inputs.py` | read the `womtool inputs` answer from this file instead of running womtool (offline testing) |
| `GSVTK_ENTITY_JSON` | `terra/batch_check_inputs.py` | answer the attribute-existence check from a captured Terra entity/workspace read (offline testing) |
| `GSVTK_METHODCONFIG_LIST_JSON` | `terra/batch_check_inputs.py` | a captured method-config listing in place of a live read (offline testing) |
| `GSVTK_IMAGE_DIGESTS_JSON` | `docker/image_capture.py` | a ref-to-digest map in place of registry lookups (offline testing) |
| `FISS_API_URL` | `terra/terra.py` | overrides `GSVTK_TERRA_API_ROOT` |
| `WOMTOOL_JAR` | `terra/batch_check_inputs.py`, `checks/wdl_gate.sh` | a womtool jar (gatk-sv CI uses womtool 84). Without it the gate still checks input keys with miniwdl and reports the womtool part as `SKIPPED`; `--strict` treats that skip as a failure. Keep the jar outside the repo, or `make audit` flags it |
| `JAVA` | `batch_check_inputs.py`, `batch_fetch_compare.sh`, `checks/wdl_gate.sh` | the `java` used to run womtool and the GATK jar |
| `MINIWDL` | `checks/wdl_gate.sh`, `terra/wdl_flat.py` | the miniwdl executable. Otherwise found on `PATH`, next to the interpreter, or in `./.venv/bin` (`./kit/gsvtk-config miniwdl` prints the choice) |
| `JAR`, `GATK_JAR` | `examples/`, `batch_fetch_compare.sh` | a locally built GATK jar |
| `PROFILE_BIN` | `batch_fetch_compare.sh` | an installed `gatk-sv-profile` |
| `GSV_WDL_VERSION` | `terra/batch_rerun_step.py` | the Dockstore version to launch, and the ref the rerun pre-check compares bindings against |
| `CAPTURED`, `IMAGES` | `examples/replay_reference_run.sh` | a task's Cromwell `script` file and the image map |
| `RESULTS` | `batch_fetch_compare.sh` | scratch root; that script's header lists its other variables |

The offline-testing variables print their source on every run (for example `captured Terra read
<file> — NOT a live read`), and a file of the wrong shape is refused naming the variable.

## Work directory

`GSVTK_WORK` holds everything regenerable: `staging/` (frozen inputs, tens of GB), `runs/` (local
replay output), `manifests/`, `recon/` (Terra dumps), `outputs/` (fetched callsets), `metadata/`
(Cromwell metadata), `image-capture/`, `wdl/` and `wdl-gate/` (per-ref WDL trees), and others. Put it on
a roomy disk. It is gitignored, and `make clean-work` shows what it holds and what would be deleted
without deleting anything.

`gsvtk-config work <subdir>` (and `gsvtk_work` in `kit/config.sh`) creates the directory and prints
its full path. Printing `--help` never creates anything under `GSVTK_WORK`.

## Changing values already used in Terra

`GSVTK_BRANCH` and the two suffixes are written into the method configs in your workspace. Changing them
does not update existing configs. Rerun `batch_configs.py create` (and `batch_freeze.py attrs` if the
frozen suffix changed), or you will compare a new profile against old outputs. `batch_configs.py show`
prints what the current profile would create, so run it first.

## Adding a key

Add it to `DEFAULTS` in `kit/gsvtk-config`, with a default or a reason it has none, and add a row to
this page in the same change. Tools must not read `GSVTK_*` settings from the environment directly. See
[CONTRIBUTING.md](../CONTRIBUTING.md).
