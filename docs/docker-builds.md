# gatk-sv docker builds, one command

Builds + pushes GATK-SV docker images for **any branch** without a local
Docker install (no Docker Desktop license, no Apple-Silicon images). It
automates exactly what the official docs prescribe
(`gatk-sv `website/docs/advanced/docker/manual.md``): spin up an **x86_64 Ubuntu VM**
in GCP, install Docker CE the way the manual does (current stable, full
plugin bundle), verify the engine can run `build_docker.py`'s exact build
flag combo (`--platform/--progress/--network/--squash` — a 2 s scratch
mini-build; on modern engines `--squash` is an accepted no-op, which is
what upstream CI effectively ships too), clone the branch, run
`build_docker.py`, push, shut down, clean up.

```
gatk-sv-build.sh  (local)  ──create VM──▶  Ubuntu x86_64 in YOUR-PROJECT
     │  streams serial log                        │ startup-script =
     │◀──────────────── marker ───────────────────┤ remote-build.sh (embedded)
                                                   ├ docker CE (manual-style) + combo probe
                                                   ├ git clone branch@SHA (forced clean)
                                                   ├ build_docker.py → docker push
                                                   └ marker + shutdown -h now (GCE-gated)
```

## What actually gets rebuilt (important!)

`build_docker.py` builds **the images you target plus everything derived
FROM them** (dependents). It does **not** rebuild their parents/base images:
a target's `FROM` line resolves to whatever that image's current tag is in
`inputs/values/dockers.json` (i.e. the registry's current parent). So:

- changes under `src/sv-pipeline`, `src/svtk`, `src/svqc`, `src/svtest`,
  `src/RdTest`, `src/WGD`, `dockerfiles/sv-pipeline*` →
  `docker/gatk-sv-build.sh <branch>` (target `sv-pipeline`) is exactly right;
- changes in a *base* image (`dockerfiles/sv-base*`, `src/sv_utils`,
  `dockerfiles/manta/*`, `dockerfiles/wham/*`, `src/str`, `src/stripy`, …) would be **silently
  ignored** by a `sv-pipeline` target — use the CI-equivalent auto-target
  mode instead: `--head-sha $(git rev-parse HEAD)`, which diffs vs
  merge-base with `main` and rebuilds precisely the affected chain;
- the true buildable leaves are **`stripy`, `str`, `manta`** — no
  `docker_dependencies`, nothing derives from them, so they build alone.
  That makes them ideal for a cheap end-to-end smoke test. Not leaves and
  not valid smokes: `wham` also drags `sv-shell` (+ `sv-pipeline`), `melt`
  builds alone but is in `non_public_images` (licensed inputs), and `vapor`
  is **not a target at all** (`vapor_docker` is only a dockers.json path
  key — `build_docker.py` rejects it).

The GATK **java** image (`gatk_docker`, the one that carries
`TrainSVGenotyping`/`GenotypeSVs`) is **not** built by `build_docker.py` —
it comes from the separate `broadinstitute/gatk` repo. This tool covers it
through a different path:

```bash
docker/gatk-sv-build.sh --gatk my-gatk-branch     # → YOUR-NAMESPACE/gatk:<branch>-<sha6>
```

which reproduces the GATK team's own `build_docker.sh -e <sha> -s -u`
semantics (whole-branch multi-stage root `Dockerfile`; the jar is compiled
INSIDE the container, host gradle untouched; unit tests skipped; the
large git-lfs runtime resources pre-pulled because the Dockerfile's `ADD .`
needs them real in the context) — minus the hardcoded upstream clone and
dockerhub/`broad-gatk` push targets: the only registry it writes is the
`--docker-repo` you name (default `YOUR-NAMESPACE/gatk`). To use one in a workspace,
set the `gatk_docker` input to
`us.gcr.io/YOUR-PROJECT/YOUR-NAMESPACE/gatk:<branch>-<sha6>`. "I built my
gatk-sv branch" ≠ "my gatk java changes are in an image" — build both.

## Usage

```bash
docker/gatk-sv-build.sh my-dev-branch                       # sv-pipeline (+ dependents)
docker/gatk-sv-build.sh --gatk my-gatk-branch                   # GATK java image → YOUR-NAMESPACE/gatk:<branch>-<sha6>
docker/gatk-sv-build.sh --head-sha <sha> my-dev-branch      # CI-style auto-target set
docker/gatk-sv-build.sh <branch> stripy                         # one leaf image, ~cheap
docker/gatk-sv-build.sh --check <branch>                        # read-only preflight, then exit
docker/gatk-sv-build.sh --dry-run <branch>                      # print the create command
docker/gatk-sv-build.sh --persistent my-dev-branch          # warm-cache builder VM (--skip-cleanup auto)
```

Default tag = **`<branch>-<sha6>`** (e.g. `my-dev-branch-9a34dc`) — a
branch-scoped TEST convention; rebuilding the same branch tip simply
overwrites its tag, and `--image-tag` overrides. Release-style tags
(`v1.1`, `v1.1.1`, date-prefixed upstream tags …) are **reserved for
production pushes** — this tool never mints them, and its first push
(anything under `…/YOUR-NAMESPACE/…`) proved the plumbing end to end before that
boundary ever matters. Default target `sv-pipeline`.
Default registry = `us.gcr.io/YOUR-PROJECT/YOUR-NAMESPACE/gatk-sv`, a personal
TEST user folder. **`us.gcr.io/YOUR-PROJECT/gatk-sv` (no personal namespace)
is production-only** — same GCR bucket either way, so `allUsers →
objectViewer` still lets Terra workspaces pull your test tags with zero
grants (point `dockers.json`/workspace configs at the full
`us.gcr.io/YOUR-PROJECT/YOUR-NAMESPACE/gatk-sv/image:tag` to use one).

**Ephemeral mode** (default): one fresh VM per build; deletes it on success;
on failure the VM self-shuts-down and is kept — full builder log at
`/var/log/gsv-build.log` on its disk, plus GCP's serial ring (~1 MB tail).
Ctrl-C detaches the watcher only; the build continues and self-shuts-down;
re-attach commands are also printed on failure/timeout paths. An in-VM
watchdog additionally bounds a *hung* builder: at `--timeout + 1 h` it prints
a FAILURE marker and powers the VM off, so no e2-standard-8 can ever idle
unbounded after the driver gave up.

**Persistent mode**: one named VM (label `created-by=gsv-build` — the tool
refuses to touch unlabeled VMs, and stops/starts it freely). The build runs
**detached on the VM** (a `sudo`-run launcher `nohup`s the builder and
verifies via its pidfile that it really started); the driver polls the log
over ssh, so laptop sleep / VPN blips cannot kill a 3-hour build. The driver
stops the VM after both success and failure (disk keeps billing until the VM
is deleted); `--keep-running` opts out. `--skip-cleanup` is **auto-added**
here — the warm layer cache is the mode's whole point (pass `--prune` if
disk pressure is the worry; note `build_docker.py`'s cleanup prunes only
once, at the end of a fully *successful* build, so failures keep the cache
anyway). Each run rotates the log to `/var/log/gsv-build.prev.log` so a
stale SUCCESS marker can never false-positive the watcher, and the builder
holds `flock /tmp/gsv-build.lock` — a concurrent second run fails fast
instead of interleaving on the shared clone. Liveness probes read the
builder's pidfile, and a transient ssh failure never stops a live build.
Persistent mode has **no in-VM watchdog**: on exit code 2 (gave up
watching) the VM stays RUNNING and you must check/stop it yourself.

Exit codes: `0` ok, `1` failed, `2` stopped watching (build still running).

## One-time setup / first-run checklist

Set `GSVTK_PROJECT` (and optionally `GSVTK_IMAGE_NAMESPACE`, `GSVTK_ZONE`, `GSVTK_MACHINE_TYPE`)
in `testkit.env` rather than passing flags every time — flags still win. See [config.md](config.md)
and [setup.md](setup.md) for the IAM side.

1. **Push rights.** The VM pushes with its own credentials = the project
   compute SA (`<project-number>-compute@developer.gserviceaccount.com`, has the
   legacy project `roles/editor` → bucket `legacyBucketOwner` usually
   suffices; upstream CI pushes with its own grant). If a push is denied,
   the error names the missing permission; fallbacks:
   `--service-account <SA known to have push>`, or ask an admin:
   ```bash
   gcloud storage buckets add-iam-policy-binding \
     gs://us.artifacts.YOUR-PROJECT.appspot.com \
     --member=serviceAccount:<project-number>-compute@developer.gserviceaccount.com \
     --role=roles/storage.admin
   ```
   (`--impersonate` deliberately does NOT exist: `instances create
   --impersonate-service-account` changes who *calls the API*, not the VM's
   runtime identity — a misleading non-fix.)
2. **Your roles** in `YOUR-PROJECT`: `compute.instanceAdmin.v1`,
   `iam.serviceAccountUser`, `actAs` on the compute SA; `--check` probes the
   read paths and `instances.create` errors name anything missing.
3. **Smoke test before the first real build (~minutes, ~cents).** Validates
   the platform assumption this tool cannot verify from a Mac: the GCE
   `startup-script` actually reaches serial port 1 on Ubuntu 24.04, running
   as root with a working metadata server. (The engine assumption — that
   `docker build` accepts `build_docker.py`'s `--squash` combo — is *not*
   checked here; the baked-in mini-build probe in `remote-build.sh` covers
   it on the first leaf build, dying in seconds with a named reason if an
   engine update ever breaks the combo.)
   Also reboot once and re-poll to learn whether startup scripts run per
   boot or once per instance:
   ```bash
   cat >/tmp/gsv-smoke.sh <<'EOF'
   #!/bin/bash
   echo GSV_SMOKE_MARKER_42
   id
   command -v gcloud || echo NO_GCLOUD   # the push path needs gcloud on the image
   curl -fs -H "Metadata-Flavor: Google" \
     "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/email"
   EOF
   gcloud compute instances create gsv-smoke --project YOUR-PROJECT \
     --zone us-central1-a --machine-type e2-small --boot-disk-size 20GB \
     --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
     --metadata-from-file startup-script=/tmp/gsv-smoke.sh
   # poll: gcloud compute instances get-serial-port-output gsv-smoke --project ... --zone ...
   # expect GSV_SMOKE_MARKER_42, uid=0, gcloud path, the SA email → then:
   gcloud compute instances delete gsv-smoke --project ... --zone ... --quiet
   ```
   Then do the first real run as a **leaf image** build
   (`docker/gatk-sv-build.sh <branch> stripy`) to exercise
   clone→docker-install→squash-probe→push→marker→delete end to end before
   committing to a multi-hour chain. (The squash probe is baked into
   `remote-build.sh` — if Docker's stable channel ever ships without the
   classic builder, the build dies in seconds with a named reason, not 3 h
   in.)

## Files

- `docker/gatk-sv-build.sh` — local driver (bash 3.2, macOS-safe; gcloud/git/curl/python3, **no docker**)
- `docker/remote-build.sh` — the builder; embedded into the VM `startup-script`
  (ephemeral) or scp'd + launched detached via `/tmp/gsv-launch.sh` run
  under `sudo` (persistent). All params travel as one base64-JSON metadata
  key `gsv-spec-b64` (commas/spaces are unsafe in GCE `--metadata` values;
  `--from-metadata` decodes via python). Every failure path routes through
  `finish()` so a marker is always the last word (the marker is written to
  raw stdout and to the log file directly, not only through `tee`);
  self-shutdown is armed only after the arch guard AND gated on GCE hardware
  identity (`/sys/class/dmi/id/product_name`), and the marker window before
  power-off is 180 s (serial is unreadable once TERMINATED — the driver
  detects a missed window instead of watching for hours). A `flock` +
  pidfile pair enforces one-builder-per-VM and gives the driver a
  trustworthy liveness signal.

## Postmortem: reading the log of a TERMINATED VM when SSH is unavailable

Learned the hard way on the first live runs: **serial output is unreadable
once an instance is TERMINATED** (`Could not fetch serial port output: …
is not ready`), and this project's network gives no TCP-22 path from here
(`ssh: connect to host … port 22: Operation timed out`). The failed VM's
disk still holds `/var/log/gsv-build.log`. Read it by attaching that disk
read-only to a throwaway reader VM:

```bash
gcloud compute instances delete <failed-gsv-vm> --project P --zone Z --quiet
# wait for detach or create fails with "The disk resource … is already being
# used by …" (poll: gcloud compute disks describe <failed-gsv-vm> … --format 'value(users)')
cat >/tmp/gsv-logread.sh <<'EOF'
#!/bin/bash
echo GSV_LOGREAD_START
mkdir -p /mnt/old && mount -o ro /dev/sdb1 /mnt/old
grep -a "GATK_SV_BUILD_RESULT" /mnt/old/var/log/gsv-build.log
tail -c 5000 /mnt/old/var/log/gsv-build.log
echo GSV_LOGREAD_DONE
# NOTE: no shutdown here — this reader's own serial would hit the same
# TERMINATED trap. Leave it RUNNING, read the serial, then delete it.
EOF
gcloud compute instances create gsv-logread --project P --zone Z \
  --machine-type e2-micro --boot-disk-size 20GB --boot-disk-auto-delete \
  --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
  --labels created-by=gsv-build \
  --disk name=<failed-gsv-vm>,mode=ro,auto-delete=no \
  --metadata-from-file startup-script=/tmp/gsv-logread.sh
# poll serial for GSV_LOGREAD_DONE, then:
gcloud compute instances delete gsv-logread --project P --zone Z --quiet
```

## Why not Cloud Build

Cloud Build is Google's managed "build dockers" service and would remove the
VM entirely, but for this pipeline: (1) its pools run a **Google-managed
docker daemon with no control over `daemon.json`/feature flags** — today
`build_docker.py`'s `--squash` happens to be accepted as a no-op by modern
engines (`cloud-builders#486` was about the *classic* builder's real squash;
the practical blocker softened), but the uncontrolled managed daemon and
(2) GitHub-push triggers needing the Cloud Build GitHub App installed on
`broadinstitute/gatk-sv` (org-admin), plus (3) "custom workers" having
vanished from current GCP docs, keep the scripted VM simpler and fully
observable. Revisit if `--no-squash` is ever upstreamed. Cost aside:
default-pool machines run $0.006–0.0624/min vs ~$0.35/hr for the equivalent
GCE VM here.

## Costs / gotchas

- e2-standard-8 ≈ $0.32/h + 150 GB pd-ssd ≈ $0.03/h; full sv-pipeline chain
  from scratch ≈ 1.5–3 h ⇒ low single dollars. Deleted VM ⇒ disk gone: the
  create now passes `--boot-disk-auto-delete`, so an ephemeral VM provably
  takes its disk with it (the ephemeral watchdog removes the last "hung VM
  bills forever" case).
- `--dry-run` changes nothing (still hits GitHub/gcloud read APIs).
- `--persistent` failure path STOPS the VM (not delete): start + ssh to
  postmortem `/var/log/gsv-build.log` (previous run rotated to
  `gsv-build.prev.log`; `gsv-build.boot.log` holds the launcher's
  pre-tee copy of the builder's stdout).
- Ephemeral VM name = `gsv-<branch>-<sha8>`; a stale VM with that name makes
  `create` fail loudly — delete it or `--instance`.
- Orphan sweep: `gcloud compute instances list --project YOUR-PROJECT
  --filter 'name~^gsv-'`.
- The repo must be **publicly clonable** from the VM (default = public
  upstream URL). Private fork → `--repo-url` with an embedded-PAT https URL.
- GCR is deprecated upstream; if the project moves to Artifact Registry,
  pass `--docker-repo <region>-docker.pkg.dev/YOUR-PROJECT/<repo>` —
  docker auth is configured for whichever host you name.
