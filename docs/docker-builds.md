# gatk-sv docker builds

`docker/gatk-sv-build.sh` builds and pushes GATK-SV docker images for any branch without a local
Docker install. It follows gatk-sv's own manual build instructions
(`website/docs/advanced/docker/manual.md` in gatk-sv): it creates an x86_64 Ubuntu VM in your GCP
project, installs Docker CE, checks that the engine accepts `build_docker.py`'s build flags with a
small test build, clones the branch, runs `build_docker.py`, pushes, and shuts the VM down.

```
gatk-sv-build.sh  (local)  ──create VM──▶  Ubuntu x86_64 in YOUR-PROJECT
     │  streams the build log                     │ runs remote-build.sh
     │◀──────────────── result marker ────────────┤
                                                   ├ install Docker CE + flag probe
                                                   ├ git clone branch@SHA
                                                   ├ build_docker.py → docker push
                                                   └ result marker, then shutdown
```

`./gsvtk build <branch>` wraps it: with no flags it runs the free preview (`--check`, then
`--dry-run`), and `--confirm` does the real build.

## Usage

```bash
docker/gatk-sv-build.sh --check my-dev-branch               # read-only preflight, then exit
docker/gatk-sv-build.sh --dry-run my-dev-branch             # print the gcloud commands, change nothing
docker/gatk-sv-build.sh my-dev-branch                       # sv-pipeline and everything derived from it
docker/gatk-sv-build.sh my-dev-branch stripy                # one leaf image, cheap
docker/gatk-sv-build.sh --head-sha <sha> my-dev-branch      # CI-style: rebuild what the diff touches
docker/gatk-sv-build.sh --gatk my-gatk-branch               # the GATK Java image
docker/gatk-sv-build.sh --persistent my-dev-branch          # reuse one VM and its layer cache
```

`docker/gatk-sv-build.sh --help` lists every flag. Defaults come from `testkit.env`
([config.md](config.md)) and flags override them: `--project` (`GSVTK_PROJECT`, required),
`--zone`, `--machine-type` (default `e2-standard-8`), `--disk-size` (default 150 GB pd-ssd),
`--docker-repo`, `--repo-url`, `--timeout` (hours to watch, default 8).

Exit codes: `0` success, `1` build or setup failed, `2` usage error or gave up watching (the build may
still be running; re-attach commands are printed).

### Tags and registry

The default tag is `<branch>-<sha6>` (for example `my-dev-branch-9a34dc`); rebuilding the same tip
overwrites it, and `--image-tag` overrides it. The tool never creates release-style tags.

The default push target is `GSVTK_IMAGE_REPO`, which defaults to
`us.gcr.io/<project>/<GSVTK_IMAGE_NAMESPACE>/gatk-sv`. Keep test images under a personal namespace:
a test tag pushed to a path that production pipelines read becomes an image production runs. The
driver warns before booting if the push target lacks your namespace. To use a built image, point the
workspace config or `dockers.json` at the full ref, for example
`us.gcr.io/YOUR-PROJECT/YOUR-NAMESPACE/gatk-sv/sv-pipeline:my-dev-branch-9a34dc`. For Artifact
Registry, pass `--docker-repo <region>-docker.pkg.dev/YOUR-PROJECT/<repo>`.

## What gets rebuilt

`build_docker.py` builds the images you name plus every image derived from them. It does not rebuild
their parents: a target's `FROM` resolves to the parent's current tag in `inputs/values/dockers.json`.

* Changes under `src/sv-pipeline`, `src/svtk`, `src/svqc`, `src/svtest`, `src/RdTest`, `src/WGD` or
  `dockerfiles/sv-pipeline*`: the default `sv-pipeline` target is correct.
* Changes to a base image (`dockerfiles/sv-base*`, `src/sv_utils`, `dockerfiles/manta/*`,
  `dockerfiles/wham/*`, `src/str`, `src/stripy`, and so on) are silently ignored by an `sv-pipeline`
  build. Use `--head-sha $(git rev-parse HEAD)`, which diffs against the merge-base with `main` and
  rebuilds the affected chain. `--head-sha`/`--base-sha` cannot be combined with image names.
* `stripy`, `str` and `manta` are true leaves (no dependencies, nothing derives from them), which makes
  them good cheap smoke tests. `wham` also rebuilds `sv-shell` and `sv-pipeline`; `melt` is a licensed
  non-public image; `vapor` is not a build target.

### The GATK Java image

`gatk_docker` (which carries `TrainSVGenotyping`, `GenotypeSVs` and the other Java SV tools) is built
from `broadinstitute/gatk`, not by `build_docker.py`. Use `--gatk`:

```bash
docker/gatk-sv-build.sh --gatk my-gatk-branch     # pushes YOUR-NAMESPACE/gatk:<branch>-<sha6>
```

This matches the GATK team's `build_docker.sh -e <sha> -s -u`: a whole-branch build of the root
`Dockerfile` with the jar compiled inside the container and unit tests skipped. It pushes only to
`--docker-repo` (default `GSVTK_GATK_IMAGE_REPO`). If your change touches both repos, build both
images.

## Ephemeral and persistent modes

**Ephemeral (default).** One fresh VM per build, named `gsv-<branch>-<sha8>`. The builder runs as the
VM's startup script and the driver streams its serial output.

* On success the driver deletes the VM (and its disk), unless `--keep`.
* On failure the VM shuts itself down and is kept for a postmortem. The full log is
  `/var/log/gsv-build.log` on its disk.
* Ctrl-C stops watching only; the build continues and the VM shuts itself down when done.
* An in-VM watchdog powers the VM off at `--timeout` plus one hour, so a hung build cannot run forever.
* An existing VM with the same name makes `create` fail; delete it or pass `--instance`.

**Persistent (`--persistent`).** One named VM (default `gsv-docker-builder`, labelled
`created-by=gsv-build`) reused across builds so the Docker layer cache survives. The tool refuses to
touch a VM without that label.

* The build runs detached on the VM and the driver polls the log over SSH, so a laptop sleeping or a
  dropped connection does not kill it.
* `--skip-cleanup` is added automatically to keep the cache; pass `--prune` if disk space is a
  concern. `--no-force-rebuild` makes more use of the warm cache.
* The driver stops the VM after success and after failure; `--keep-running` keeps it up. A stopped VM's
  disk still bills until the VM is deleted.
* If launching fails before the poll loop starts (SSH never comes up, the copy fails), the driver stops
  the VM and prints the postmortem commands.
* Only one build runs per VM. A second invocation on a busy VM refuses with `GSV_BUSY pid=...` and
  prints the command to watch the running build.
* Each run rotates the previous log to `/var/log/gsv-build.prev.log`; `/var/log/gsv-build.boot.log`
  holds the launcher's output.
* There is no in-VM watchdog. If the driver exits 2 (gave up watching), the VM is still running and you
  must stop it yourself.

## What a build leaves behind: the image capture

`build_docker.py` reports pushed images by tag, and tags can be re-pushed. After a successful build,
in both modes, the driver writes an image-digest capture so a later launch can pin exact bytes:

* `$GSVTK_WORK/image-capture/<branch>-<sha6>.serial`, the build text the driver streamed;
* `$GSVTK_WORK/image-capture/<branch>-<sha6>.json`, the capture written by
  `docker/image_capture.py --resolve`.

The capture maps each `*_docker` input of one module step (default: `single_sample` step `01`; set
`GSVTK_CAPTURE_STEP` to change the step) to an `@sha256` digest ref. Keys this build refreshed are
taken from the `dockers.json` diff the build prints; other keys are resolved from the branch's
`dockers.json` or recorded as `unresolved` with a reason (an untagged Docker Hub name, for example).
Pass it to a rerun with
`python terra/batch_rerun_step.py --image-from <capture.json> ...`
([terra-head-to-head.md](terra-head-to-head.md#images-must-be-pinned)). Unresolved keys still need
`--image` or a profile entry.

The capture runs after the VM is deleted or stopped and always returns success to the driver, so a
capture problem never blocks teardown; it prints a warning and the command to re-run it:

```bash
./docker/image_capture.py --from-serial "$GSVTK_WORK"/image-capture/<branch>-<sha6>.serial --resolve
./docker/image_capture.py --from-serial <file> --dry-run      # print the capture, write nothing
```

`--resolve` calls `gcloud container images describe` per ref; without it every key is `unresolved`.
`--out` must not be inside the git-tracked tree, since a capture holds real registry coordinates.
Exit codes: 0 wrote a capture, 2 usage or a missing input file, 3 unusable input (no refresh block,
wrong shape, a tracked output path), 4 the capture would contain no usable entries.
`./docker/image_capture.py --selftest` runs offline.

## One-time setup

Set `GSVTK_PROJECT` (and optionally `GSVTK_IMAGE_NAMESPACE`, `GSVTK_ZONE`, `GSVTK_MACHINE_TYPE`) in
`testkit.env`. See [config.md](config.md) and [setup.md](setup.md) for IAM.

1. **Push rights.** The VM pushes as its service account, by default the project's compute service
   account (`<project-number>-compute@developer.gserviceaccount.com`), which usually has enough access
   through the legacy `roles/editor`. If a push is denied, pass
   `--service-account <SA with push rights>` or ask an admin for:
   ```bash
   gcloud storage buckets add-iam-policy-binding \
     gs://us.artifacts.YOUR-PROJECT.appspot.com \
     --member=serviceAccount:<project-number>-compute@developer.gserviceaccount.com \
     --role=roles/storage.admin
   ```
   Impersonation flags would change who calls the API, not the VM's identity, so they would not help.
2. **Your roles** in the project: `compute.instanceAdmin.v1` and `iam.serviceAccountUser` (actAs on the
   compute service account). `--check` probes the read paths, and `instances create` errors name any
   missing permission.
3. **First build.** Build a leaf image first (`docker/gatk-sv-build.sh <branch> stripy`). It exercises
   the clone, Docker install, flag probe, push and teardown in minutes before you commit to a
   multi-hour chain. If the Docker engine ever stops accepting the build flags, the flag probe fails
   in seconds with a named reason.

The repo must be publicly clonable from the VM. For a private fork, pass `--repo-url` with an https URL
that embeds a token.

## Costs / gotchas

* An `e2-standard-8` with a 150 GB pd-ssd costs roughly $0.35/h. A full `sv-pipeline` chain from
  scratch takes about 1.5 to 3 hours.
* `--dry-run` and `--check` change nothing, but still call GitHub and gcloud read APIs.
* Find leftover builder VMs with
  `gcloud compute instances list --project YOUR-PROJECT --filter 'name~^gsv-'`.
* Serial output cannot be read once a VM is `TERMINATED`. The builder waits 180 s after printing its
  result marker before powering off, and the driver reports a missed window instead of waiting.
* Starting a failed ephemeral VM re-runs its startup script, which rebuilds once.

## Reading the log of a stopped VM

If a failed ephemeral VM is already `TERMINATED` and you have no SSH path to it, read its log by
attaching its disk read-only to a small reader VM:

```bash
# the builder VM is created with --boot-disk-auto-delete, so keep the disk before deleting the VM
gcloud compute instances set-disk-auto-delete <failed-vm> --project P --zone Z --disk <failed-vm> --no-auto-delete
gcloud compute instances delete <failed-vm> --project P --zone Z --quiet
# wait until `gcloud compute disks describe <failed-vm> ... --format 'value(users)'` is empty
cat >/tmp/gsv-logread.sh <<'EOF'
#!/bin/bash
echo GSV_LOGREAD_START
mkdir -p /mnt/old && mount -o ro /dev/sdb1 /mnt/old
grep -a "GATK_SV_BUILD_RESULT" /mnt/old/var/log/gsv-build.log
tail -c 5000 /mnt/old/var/log/gsv-build.log
echo GSV_LOGREAD_DONE
EOF
gcloud compute instances create gsv-logread --project P --zone Z \
  --machine-type e2-micro --boot-disk-size 20GB --boot-disk-auto-delete \
  --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \
  --labels created-by=gsv-build \
  --disk name=<failed-vm>,mode=ro,auto-delete=no \
  --metadata-from-file startup-script=/tmp/gsv-logread.sh
# read the serial output until GSV_LOGREAD_DONE, then delete both:
gcloud compute instances delete gsv-logread --project P --zone Z --quiet
gcloud compute disks delete <failed-vm> --project P --zone Z --quiet
```

The reader script does not shut down, so its serial output stays readable.

For a persistent VM, start it and use SSH:
`gcloud compute ssh gsv-docker-builder --command 'tail -200 /var/log/gsv-build.log'`.

## Files

* `docker/gatk-sv-build.sh`: the local driver. Needs bash 3.2+, gcloud, git, curl and python3; no
  Docker.
* `docker/remote-build.sh`: the builder that runs on the VM. Parameters travel as one base64-JSON
  metadata key, `gsv-spec-b64`. Every failure path prints a result marker last. Self-shutdown only
  happens on GCE hardware. A `flock` plus a pidfile enforce one builder per VM and give the driver a
  liveness signal.
* `docker/image_capture.py`: turns a finished build's text into the digest capture described above.

Cloud Build is not used because its managed Docker daemon cannot be configured and GitHub triggers
would need an app installed on the gatk-sv org; a scripted VM is simpler to observe.
