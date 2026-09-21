#!/bin/bash
# remote-build.sh — runs ON the Linux x86_64 builder VM, as root.
# Installs Docker CE the way the upstream manual prescribes, verifies the
# engine can run build_docker.py's exact build-flag combo, clones gatk-sv at
# a branch/SHA, runs scripts/docker/build_docker.py, pushes to the container
# registry, prints a machine-readable result marker, then (optionally) shuts
# the VM down.
#
# Parameters arrive either as GSV_SPEC_B64 (base64 JSON, used by the local
# driver in both modes; in ephemeral mode it is read from instance metadata
# key gsv-spec-b64 via `--from-metadata`), or as individual env vars
# (GSV_BRANCH, GSV_REPO_URL, GSV_DOCKER_REPO, GSV_IMAGE_TAG, GSV_TARGETS,
# GSV_BASE_SHA, GSV_HEAD_SHA, GSV_EXTRA_FLAGS, GSV_SHUTDOWN, GSV_PIN_SHA).
#
# Full build log goes to $GSV_LOG (/var/log/gsv-build.log) and stdout.

set -u
# GCE startup scripts run as root WITHOUT HOME set — every `git config
# --global`/`git lfs install` dies with "fatal: $HOME not set" unless this
# is fixed (learned live: first --gatk run failed at `git lfs install`).
export HOME="${HOME:-/root}"

LOG="${GSV_LOG:-/var/log/gsv-build.log}"
GSV_PIDFILE="${GSV_PIDFILE:-/tmp/gsv-build.pid}"
SHUTDOWN_PENDING=false
# Everything below is teed: stdout (serial port / ssh pty) AND the on-disk log
# the README promises for postmortems. bash-only (this script is bash). If tee
# cannot open $LOG (not root?), fall back to passthrough — never let a log
# problem SIGPIPE the builder.
# Save the real stdout BEFORE the tee redirect so finish() can always write
# its marker directly even if tee dies mid-run (marker loss would otherwise
# make the driver sit out the full timeout).
# Short-circuit BEFORE any logging/metadata work: this script is what a GCE startup script
# runs, so a stray `--help` on a real builder VM would otherwise read the push spec and start a
# build. It has no arguments of its own -- everything comes from instance metadata.
for _arg in "$@"; do
    case "$_arg" in
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0;;
    esac
done

exec 3>&1
exec > >(tee -a "$LOG" || cat) 2>&1

finish() {
    # finish SUCCESS|FAILURE <reason>
    MARKER="### GATK_SV_BUILD_RESULT=$1${2:+ ($2)} ###"
    echo "$MARKER"
    echo "$MARKER" >&3 2>/dev/null || true                 # bypass tee
    echo "$MARKER" >> "$LOG" 2>/dev/null || true           # bypass the pipe entirely
    rm -f "$GSV_PIDFILE" 2>/dev/null || true
    if [ "$SHUTDOWN_PENDING" = "true" ]; then
        if grep -isq google /sys/class/dmi/id/product_name 2>/dev/null; then
            echo "Shutting down in 180s; full log: $LOG (plus the GCP serial ring)."
            sleep 180
            shutdown -h now
        else
            echo "not a GCE instance — SKIPPING shutdown (refusing to power off the wrong machine)"
        fi
    fi
    exit $([ "$1" = "SUCCESS" ] && echo 0 || echo 1)
}

meta() {
    curl -fs -m 5 -H "Metadata-Flavor: Google" \
        "http://metadata.google.internal/computeMetadata/v1/instance/attributes/$1" 2>/dev/null \
        || true
}

# ---------------------------------------------------------------- parameters
SPEC_B64="${GSV_SPEC_B64:-}"
[ "${1:-}" = "--from-metadata" ] && SPEC_B64="$(meta gsv-spec-b64)"
if [ -n "$SPEC_B64" ]; then
    SPEC_EXPORTS="$(printf '%s' "$SPEC_B64" | base64 -d | python3 -c '
import json, sys, shlex
s = json.load(sys.stdin)
kv = {
    "GSV_KIND": s.get("kind", "gatk-sv"),
    "GSV_BRANCH": s.get("branch", ""),
    "GSV_REPO_URL": s.get("repo_url", ""),
    "GSV_DOCKER_REPO": s.get("docker_repo", ""),
    "GSV_IMAGE_TAG": s.get("tag", ""),
    "GSV_TARGETS": " ".join(s.get("targets") or []),
    "GSV_BASE_SHA": s.get("base_sha", ""),
    "GSV_HEAD_SHA": s.get("head_sha", ""),
    "GSV_EXTRA_FLAGS": " ".join(s.get("extra_flags") or []),
    "GSV_SHUTDOWN": "true" if s.get("shutdown") else "false",
    "GSV_PIN_SHA": s.get("pin_sha", ""),
}
print("\n".join("%s=%s" % (k, shlex.quote(v)) for k, v in kv.items()))
' 2>/dev/null)" || finish FAILURE "bad GSV spec JSON"
    eval "$SPEC_EXPORTS" || finish FAILURE "bad GSV spec JSON"
fi

# Every guard here must route through finish(): a bare `:?`/unbound death
# prints no marker, and the driver can then only sit out its full timeout.
[ -n "${GSV_BRANCH:-}" ] || finish FAILURE "GSV_BRANCH not set (spec JSON missing/malformed, or metadata server unreadable)"
[ -n "${GSV_DOCKER_REPO:-}" ] || finish FAILURE "GSV_DOCKER_REPO not set"
[ -n "${GSV_IMAGE_TAG:-}" ] || finish FAILURE "GSV_IMAGE_TAG not set"
[ -n "${GSV_REPO_URL:-}" ] || GSV_REPO_URL="https://github.com/broadinstitute/gatk-sv"
if [ -z "${GSV_TARGETS:-}" ] && [ -z "${GSV_HEAD_SHA:-}" ] && [ -z "${GSV_BASE_SHA:-}" ] \
   && [ "${GSV_KIND:-gatk-sv}" != "gatk" ]; then
    finish FAILURE "no build scope: targets/head_sha/base_sha all empty"
fi
# base_sha alone is valid: build_docker.py accepts --current-git-commit HEAD.

if [ "${GSV_KIND:-gatk-sv}" = "gatk" ]; then
    CLONE_DIR="${GSV_CLONE_DIR:-/opt/gsv/gatk}"
else
    CLONE_DIR="${GSV_CLONE_DIR:-/opt/gsv/gatk-sv}"
fi

echo "== gatk-sv remote build $(date -u +%FT%TZ) =="
echo "branch=$GSV_BRANCH repo=$GSV_REPO_URL docker_repo=$GSV_DOCKER_REPO tag=$GSV_IMAGE_TAG"
echo "targets='${GSV_TARGETS:-}' sha_mode='${GSV_BASE_SHA:-}..${GSV_HEAD_SHA:-}' extra='${GSV_EXTRA_FLAGS:-}' pin='${GSV_PIN_SHA:-}'"
ARCH=$(uname -m)
[ "$ARCH" = "x86_64" ] || finish FAILURE "arch is $ARCH, need x86_64 for linux/amd64 images"
# Arm self-shutdown only AFTER the arch guard (finish() additionally refuses
# on non-GCE hardware) — never shutdown a machine we just refused to build on.
if [ "${GSV_SHUTDOWN:-false}" = "true" ]; then SHUTDOWN_PENDING=true; fi

# One builder at a time: a shared persistent VM must never interleave two
# builds (checkout -f while another build reads the tree => wrong image
# pushed to the shared registry). flock exists on every GCE image; where it
# is absent (e.g. a macOS test box) only the lock is skipped.
if command -v flock >/dev/null 2>&1; then
    exec 9>/tmp/gsv-build.lock
    flock -n 9 || finish FAILURE "another gsv build holds /tmp/gsv-build.lock — refusing to share this VM"
fi
echo $$ > "$GSV_PIDFILE" 2>/dev/null || true

# ------------------------------------------------------------------ docker
export DEBIAN_FRONTEND=noninteractive
# DPkg::Lock::Timeout: Ubuntu 24.04 runs unattended-upgrades for minutes
# on first boot; startup scripts land squarely in that window.
APT="apt-get -o DPkg::Lock::Timeout=300"
echo "== ensuring Docker CE from docker.com =="
if command -v docker >/dev/null 2>&1; then
    # OBSERVED (first live runs): the ubuntu-2404-lts-amd64 GCE image ships
    # distro docker.io 27.5.1, whose builder cannot run build_docker.py's
    # flag combo. Never skip installation because "docker exists" — the
    # distro build is not what manual.md/CI validate. apt resolves the
    # docker-ce <-> docker.io conflict by removing the distro package.
    echo "pre-existing docker: $(docker --version 2>/dev/null || echo '?') — replacing with docker.com CE"
    command -v systemctl >/dev/null && systemctl stop docker.service 2>/dev/null || true
fi
$APT update -qq || finish FAILURE "apt update"
$APT install -y -qq ca-certificates curl git python3 gnupg >/dev/null \
    || finish FAILURE "apt install prereqs"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
    || finish FAILURE "docker gpg fetch"
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
$APT update -qq
# Install exactly the bundle upstream's own manual prescribes
# (website/docs/advanced/docker/manual.md, incl. buildx/compose plugins)
# on the current stable release, and leave the DEFAULT environment
# alone: build_docker.py always passes `--platform --progress --network
# --squash`, a combo that only completes on the modern default builder
# (engine 29+ measured; --squash accepted as a compat no-op — what
# upstream CI effectively ships too). DOCKER_BUILDKIT=0 (legacy builder)
# rejects --progress. The combo-probe below is the real gate.
$APT install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null \
    || finish FAILURE "docker-ce install"
# Match upstream's publish job (sv_pipeline_docker.yml writes the same
# experimental:true via jq): harmless on modern engines, and keeps daemon
# identity with CI. Merge into, never clobber, a pre-existing config.
DAEMON_JSON=/etc/docker/daemon.json
NEED_RESTART=false
if [ ! -s "$DAEMON_JSON" ]; then
    echo '{"experimental": true}' > "$DAEMON_JSON" || finish FAILURE "write daemon.json"
    NEED_RESTART=true
elif grep -Eq '"experimental"[[:space:]]*:[[:space:]]*true' "$DAEMON_JSON"; then
    echo "daemon.json already experimental"
else
    python3 -c 'import json,sys;p=sys.argv[1];d=json.load(open(p));d["experimental"]=True;json.dump(d,open(p,"w"))' "$DAEMON_JSON" \
        || finish FAILURE "merge daemon.json (pre-existing file not parseable?)"
    NEED_RESTART=true
fi
if [ "$NEED_RESTART" = "true" ]; then
    systemctl restart docker.service || finish FAILURE "docker restart"
fi
systemctl is-active --quiet docker || systemctl start docker.service || finish FAILURE "docker daemon inactive"
docker --version || finish FAILURE "docker CLI broken"
if [ "${GSV_KIND:-gatk-sv}" = "gatk" ]; then
    echo "kind=gatk: skipping the build_docker.py combo probe (gatk image uses plain docker build)"
else
# Preflight the EXACT flag combo build_docker.py always passes
# (`--platform linux/amd64 --progress plain --network=host ... --squash`)
# with a 2-second scratch mini-build. --help greps proved unreliable across
# builder generations (first live run: 27.5.1 classic showed --squash in
# help yet rejected --progress; engine 29 default accepts --squash as a
# no-op). Fail in seconds, named, not 3 h in.
GSV_PROBE_DIR=$(mktemp -d /tmp/gsv-probe-XXXXXX) || finish FAILURE "mktemp probe dir"
printf 'FROM scratch\nCOPY p p\n' > "$GSV_PROBE_DIR/Dockerfile"
: > "$GSV_PROBE_DIR/p"
docker build --platform linux/amd64 --progress plain --network=host \
    -f "$GSV_PROBE_DIR/Dockerfile" --squash "$GSV_PROBE_DIR" >/dev/null 2>&1 \
    || finish FAILURE "engine $(docker version --format '{{.Server.Version}}' 2>/dev/null) cannot run build_docker.py's flag combo (--platform/--progress/--network/--squash)"
rm -rf "$GSV_PROBE_DIR"
echo "engine combo-probe OK"
fi

# Registry auth via the VM's own credentials (instance service account).
REG_HOST="${GSV_DOCKER_REPO%%/*}"
gcloud auth configure-docker "$REG_HOST" --quiet || finish FAILURE "configure-docker $REG_HOST"

# -------------------------------------------------------------------- code
if [ ! -d "$CLONE_DIR/.git" ]; then
    mkdir -p "$(dirname "$CLONE_DIR")"
    git clone "$GSV_REPO_URL" "$CLONE_DIR" || finish FAILURE "git clone $GSV_REPO_URL"
else
    git -C "$CLONE_DIR" fetch origin "+refs/heads/*:refs/remotes/origin/*" --prune \
        || finish FAILURE "git fetch"
fi
git config --global --add safe.directory "$CLONE_DIR" >/dev/null 2>&1 || true
# Discard TRACKED residue from a previous build (persistent clones!):
# build_docker.py rewrites inputs/values/dockers.json even on failure, and its
# default git-protect refuses a dirty tree — a bare `checkout -B` neither
# discards it reliably nor fails loudly (upstream CI sidesteps this with
# --disable-git-protect). Force-reset first, then drop untracked junk.
git -C "$CLONE_DIR" checkout -q -f -B gsv-build "origin/$GSV_BRANCH" \
    || git -C "$CLONE_DIR" checkout -q -f -B gsv-build "$GSV_BRANCH" \
    || finish FAILURE "branch '$GSV_BRANCH' not found on origin"
git -C "$CLONE_DIR" reset --hard -q HEAD || finish FAILURE "reset clone"
git -C "$CLONE_DIR" clean -fdq
if [ -n "${GSV_PIN_SHA:-}" ]; then
    git -C "$CLONE_DIR" cat-file -e "${GSV_PIN_SHA}^{commit}" 2>/dev/null \
        || git -C "$CLONE_DIR" fetch origin "$GSV_PIN_SHA" || finish FAILURE "pin SHA unfetchable"
    git -C "$CLONE_DIR" checkout -q --detach "$GSV_PIN_SHA" || finish FAILURE "checkout pin SHA"
fi
# Auto-target mode without --base-sha: diff against the merge with main,
# mirroring the manual docs (git merge-base main <branch>).
if [ -z "${GSV_BASE_SHA:-}" ] && [ -n "${GSV_HEAD_SHA:-}" ]; then
    GSV_BASE_SHA="$(git -C "$CLONE_DIR" merge-base origin/main "${GSV_HEAD_SHA}" 2>/dev/null || true)"
    [ -n "$GSV_BASE_SHA" ] || finish FAILURE "cannot compute merge-base(origin/main, ${GSV_HEAD_SHA})"
fi
echo "building at $(git -C "$CLONE_DIR" rev-parse HEAD)"

# ------------------------------------------------- build (kind=gatk image)
# Mirrors `build_docker.sh -e <sha> -s -u` run at the GATK repo root, minus
# its hardcoded upstream clone / dockerhub endpoints: the repo's root
# multi-stage Dockerfile compiles the jar INSIDE the container (gradle on
# the host never touched), unit tests are skipped (-u semantics), and the
# only network write is our own --docker-repo push. RELEASE=false (no
# conda-lite variants, no latest tags — those belong to real releases).
if [ "${GSV_KIND:-gatk-sv}" = "gatk" ]; then
    $APT install -y -qq git-lfs >/dev/null || finish FAILURE "git-lfs install"
    git -C "$CLONE_DIR" lfs install --force || finish FAILURE "git lfs install"
    # The Dockerfile's ADD . copies the build context and the in-container
    # build has no git-lfs, so large runtime resources must be real files
    # in the context before `docker build` starts (same reason upstream's
    # script does its staging `git lfs pull`).
    git -C "$CLONE_DIR" lfs pull --include src/main/resources/large/ \
        || finish FAILURE "git lfs pull (large resources)"
    IMAGE_REF="$GSV_DOCKER_REPO:$GSV_IMAGE_TAG"
    echo "== docker build $IMAGE_REF (root Dockerfile, RELEASE=false, no unit tests) =="
    df -h / /var/lib/docker 2>/dev/null || true
    ( cd "$CLONE_DIR" && docker build -t "$IMAGE_REF" --build-arg RELEASE=false . )
    GRC=$?
    df -h / /var/lib/docker 2>/dev/null || true
    [ "$GRC" -eq 0 ] || finish FAILURE "gatk docker build (rc $GRC)"
    docker push "$IMAGE_REF" || finish FAILURE "gatk docker push"
    docker image prune -f --filter label=stage=gatkIntermediateBuildImage >/dev/null || true
    finish SUCCESS "gatk image pushed to $GSV_DOCKER_REPO :$GSV_IMAGE_TAG"
fi

# ------------------------------------------------------------- build (gatk-sv)
cd "$CLONE_DIR" || finish FAILURE "cd $CLONE_DIR"
BUILD_ARGS=(--docker-repo "$GSV_DOCKER_REPO" --image-tag "$GSV_IMAGE_TAG")
if [ -n "${GSV_BASE_SHA:-}" ]; then
    BUILD_ARGS+=(--base-git-commit "$GSV_BASE_SHA" --current-git-commit "${GSV_HEAD_SHA:-HEAD}")
else
    # shellcheck disable=SC2201  # intentional word splitting
    BUILD_ARGS+=(--targets ${GSV_TARGETS})
fi
# shellcheck disable=SC2201  # intentional word splitting
echo "== python3 scripts/docker/build_docker.py ${BUILD_ARGS[*]} ${GSV_EXTRA_FLAGS:-} =="
df -h / /var/lib/docker 2>/dev/null || true

python3 scripts/docker/build_docker.py "${BUILD_ARGS[@]}" ${GSV_EXTRA_FLAGS:-}
RC=$?
df -h / /var/lib/docker 2>/dev/null || true
[ "$RC" -eq 0 ] || finish FAILURE "build_docker.py exit $RC"

echo "== updated dockers.json entries =="
git -C "$CLONE_DIR" diff -- inputs/values/dockers.json | grep '^+' || echo "(none)"

finish SUCCESS "images pushed to $GSV_DOCKER_REPO :$GSV_IMAGE_TAG"
