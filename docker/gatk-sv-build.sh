#!/bin/bash
# gatk-sv-build.sh — one-command GATK-SV docker builds on a Linux x86 VM in GCP.
#
# Automates gatk-sv's "Manual Deployment" docs: create an x86 Ubuntu VM, install
# Docker CE with experimental enabled (build_docker.py pushes with --squash),
# clone the branch, run scripts/docker/build_docker.py, shut down. No local
# Docker is used.
#
# Usage:  gatk-sv-build.sh [options] <branch> [image ...]
#   e.g.  gatk-sv-build.sh my-dev-branch sv-pipeline
#
# Requires: gcloud (authenticated), git, curl, python3. Docker NOT required.
# See docs/config.md for the profile (project + push target are yours to name);
# see docs/docker-builds.md for the one-time IAM grant that lets the VM push.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REMOTE_SCRIPT="$SCRIPT_DIR/remote-build.sh"
EMBED_TOKEN="GSV_EMBEDDED_REMOTE_SCRIPT_v1"

. "$SCRIPT_DIR/../kit/config.sh"

# ---------------------------- defaults / parse
# Precedence: CLI flag, then GSVTK_* environment, then testkit.env. PROJECT and
# DOCKER_REPO have no default (see the guard after argument parsing).
PROJECT="${GSVTK_PROJECT:-}"
ZONE="${GSVTK_ZONE:-us-central1-a}"
MACHINE_TYPE="${GSVTK_MACHINE_TYPE:-e2-standard-8}"
DISK_GB="${GSVTK_DISK_GB:-150}"
REPO_URL=""   # resolved per BUILD_KIND from GSVTK_{GATK,GATK_SV}_REPO_URL
DOCKER_REPO=""   # resolved per BUILD_KIND from GSVTK_IMAGE_REPO / GSVTK_GATK_IMAGE_REPO
BUILD_KIND="gatk-sv"
IMAGE_TAG=""
BASE_SHA=""
HEAD_SHA=""
EXTRA_FLAGS=()
INSTANCE=""
SERVICE_ACCOUNT=""
PERSISTENT=false
KEEP=false
KEEP_RUNNING=false
TIMEOUT_H=8
IMAGE_FAMILY="ubuntu-2404-lts-amd64"
DRY_RUN=false
CHECK_ONLY=false
POSITIONAL=()

usage() {
cat <<'EOF'
Usage: gatk-sv-build.sh [options] <branch> [image ...]

Positional: the branch to build, then zero or more image targets
(defaults to: sv-pipeline). Images listed here are rebuilt together
with every image derived from them (build_docker.py dependency chain).

Defaults come from the gatk-sv-testkit profile (testkit.env / GSVTK_* env vars);
any flag here beats them. See docs/config.md.

Options:
  --project ID           GCP project (default GSVTK_PROJECT; required)
  --zone ZONE            compute zone (default GSVTK_ZONE)
  --machine-type M       default GSVTK_MACHINE_TYPE (e2-standard-8); docs'
                         minimum is e2-standard-2
  --disk-size GB         boot pd-ssd size, default GSVTK_DISK_GB (150; docs:
                         ~100 needed)
  --repo-url URL         git repo for the VM to clone (default
                         GSVTK_GATK_SV_REPO_URL, i.e. the public
                         github.com/broadinstitute/gatk-sv)
  --docker-repo REPO     push target (default GSVTK_IMAGE_REPO, derived as
                         us.gcr.io/<project>/<GSVTK_IMAGE_NAMESPACE>/gatk-sv;
                         GSVTK_GATK_IMAGE_REPO with --gatk). Name a path NO
                         pipeline reads: a test tag pushed to a production
                         registry path becomes the image production runs.
  --gatk                 build the GATK JAVA image instead (branch of
                         broadinstitute/gatk; whole-branch docker build via the
                         repo's root Dockerfile, unit tests skipped — the
                         build_docker.sh '-e <sha> -s -u' semantics, pointed at
                         your test folder instead of dockerhub)
  --image-tag TAG        default <branch>-<sha6> e.g. my-dev-branch-9a34dc (test
                         convention; release-style tags are production-only)
  --base-sha SHA         auto-target mode: diff base (mutually exclusive
  --head-sha SHA         with image positionals; base defaults to
                         merge-base with main, computed on the VM)
  --skip-dependent       build_docker.py --skip-dependent-images
  --prune                build_docker.py --prune-after-each-image
  --no-force-rebuild     build_docker.py --no-force-rebuild (useful with
                         --persistent warm caches)
  --skip-cleanup         build_docker.py --skip-cleanup: keep docker layers
                         + build cache after success (persistent warm-cache
                         runs want this; it also grows the disk)
  --instance NAME        VM name (ephemeral default gsv-<branch>-<sha8>;
                         persistent default gsv-docker-builder)
  --persistent           reuse one named VM across builds so docker layer
                         cache survives; builds run over `gcloud compute
                         ssh`; VM stopped when idle unless --keep-running
  --keep                 keep (don't delete) the ephemeral VM after SUCCESS
  --keep-running         persistent mode: don't stop the VM afterwards
  --service-account SA   VM service account (default: project's compute SA);
                         needs push rights — see docs/docker-builds.md, "One-time setup"
  --timeout HOURS        give up watching after this long (default 8);
                         the VM build continues regardless
  --dry-run              print the gcloud commands; change nothing
  --check                read-only preflight, then exit
  --help                 this help

Exit codes: 0 success, 1 build/setup failed, 2 usage error or gave up watching
(a build may still be running; re-attach instructions are printed).
EOF
}

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mwarning: %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31merror: %s\033[0m\n' "$*" >&2; exit 1; }
# Usage errors exit 2 so they are distinguishable from a failed build (1).
die_usage() { printf '\033[1;31merror: %s\033[0m\n' "$*" >&2; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --project)         PROJECT="$2"; shift 2;;
        --zone)            ZONE="$2"; shift 2;;
        --machine-type)    MACHINE_TYPE="$2"; shift 2;;
        --disk-size)       DISK_GB="$2"; shift 2;;
        --repo-url)        REPO_URL="$2"; shift 2;;
        --docker-repo)     DOCKER_REPO="$2"; shift 2;;
        --image-tag)       IMAGE_TAG="$2"; shift 2;;
        --base-sha)        BASE_SHA="$2"; shift 2;;
        --head-sha)        HEAD_SHA="$2"; shift 2;;
        --instance)        INSTANCE="$2"; shift 2;;
        --service-account) SERVICE_ACCOUNT="$2"; shift 2;;
        --skip-dependent)  EXTRA_FLAGS+=(--skip-dependent-images); shift;;
        --prune)           EXTRA_FLAGS+=(--prune-after-each-image); shift;;
        --no-force-rebuild) EXTRA_FLAGS+=(--no-force-rebuild); shift;;
        --skip-cleanup)    EXTRA_FLAGS+=(--skip-cleanup); shift;;
        --persistent)      PERSISTENT=true; shift;;
        --keep)            KEEP=true; shift;;
        --keep-running)    KEEP_RUNNING=true; shift;;
        --timeout)         TIMEOUT_H="$2"; shift 2;;
        --dry-run)         DRY_RUN=true; shift;;
        --gatk)            BUILD_KIND=gatk; shift;;
        --check)           CHECK_ONLY=true; shift;;
        --help|-h)         usage; exit 0;;
        -*)                die_usage "unknown flag: $1 (see --help)";;
        *)                 POSITIONAL+=("$1"); shift;;
    esac
done

[ -f "$REMOTE_SCRIPT" ] || die "remote-build.sh missing next to $0"
BRANCH=""
TARGETS=()
if [ ${#POSITIONAL[@]} -gt 0 ]; then BRANCH="${POSITIONAL[0]}"; fi
if [ ${#POSITIONAL[@]} -gt 1 ]; then TARGETS=("${POSITIONAL[@]:1}"); fi
[ -n "$BRANCH" ] || { usage; die_usage "missing <branch>"; }
# Resolve kind-specific remotes/targets before anything else uses them.
case "$BUILD_KIND" in
  gatk)
      [ ${#TARGETS[@]} -eq 0 ] || die "--gatk builds the whole branch; image targets do not apply"
      [ -z "$HEAD_SHA$BASE_SHA" ] || die "--gatk ignores --base-sha/--head-sha (whole-branch image build)"
      [ -n "$REPO_URL" ] || REPO_URL="${GSVTK_GATK_REPO_URL:-https://github.com/broadinstitute/gatk}"
      [ -n "$DOCKER_REPO" ] || DOCKER_REPO="${GSVTK_GATK_IMAGE_REPO:-}"
      ;;
  gatk-sv)
      [ -n "$REPO_URL" ] || REPO_URL="${GSVTK_GATK_SV_REPO_URL:-https://github.com/broadinstitute/gatk-sv}"
      [ -n "$DOCKER_REPO" ] || DOCKER_REPO="${GSVTK_IMAGE_REPO:-}"
      if [ ${#TARGETS[@]} -eq 0 ] && [ -z "$HEAD_SHA$BASE_SHA" ]; then TARGETS=(sv-pipeline); fi
      ;;
  *) die "internal: unknown BUILD_KIND '$BUILD_KIND'";;
esac
if [ -n "$HEAD_SHA$BASE_SHA" ] && [ ${#TARGETS[@]} -gt 0 ]; then
    die "image targets and --base-sha/--head-sha are mutually exclusive (build_docker.py limitation)"
fi

# Past here a VM may be created, so project and push target must be explicit.
[ -n "$PROJECT" ] || gsvtk_require PROJECT
[ -n "$DOCKER_REPO" ] || die "no push target: set GSVTK_IMAGE_REPO (or GSVTK_PROJECT + GSVTK_IMAGE_NAMESPACE) in testkit.env, or pass --docker-repo"
say "target"
echo "  project  $PROJECT   ($ZONE, $MACHINE_TYPE, ${DISK_GB}GB)"
echo "  clone    $REPO_URL"
# The registry may hold production images; warn before booting if the push target
# is outside the configured namespace.
_GSVTK_NS="${GSVTK_IMAGE_NAMESPACE:-}"
if [ -n "$_GSVTK_NS" ]; then
  case "$DOCKER_REPO" in
    */"$_GSVTK_NS"|*/"$_GSVTK_NS"/*) : ;;
    *) echo "  WARNING: push target has no '$_GSVTK_NS' namespace segment:"
       echo "             $DOCKER_REPO"
       echo "           A tag here can be a production image path. Set GSVTK_IMAGE_NAMESPACE or pass"
       echo "           --docker-repo pointing inside your own namespace to silence this." ;;
  esac
fi
echo "  push to  $DOCKER_REPO"

case "$DISK_GB" in ''|*[!0-9]*) die "--disk-size must be an integer (GB)";; esac
case "$TIMEOUT_H" in ''|*[!0-9]*) die "--timeout must be an integer (hours)";; esac

# A persistent builder exists to keep its docker cache, so skip build_docker.py's cleanup.
if $PERSISTENT; then
    case " ${EXTRA_FLAGS[*]:-} " in
        *" --skip-cleanup "*) ;;
        *) EXTRA_FLAGS+=(--skip-cleanup)
           echo "persistent mode: auto-adding --skip-cleanup (keeps the docker cache warm; pass --prune if disk is the worry)";;
    esac
fi

sanitize() { printf '%s' "$1" | tr 'A-Z' 'a-z' | sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$//'; }

# ---------------------------- resolve branch tip + default tag
SHA="$(git ls-remote "$REPO_URL" "refs/heads/$BRANCH" 2>/dev/null | head -1 | cut -f1 || true)"
if [ -z "$SHA" ]; then
    $DRY_RUN || die "branch '$BRANCH' not found on $REPO_URL"
    SHA="0000000000000000000000000000000000000000"
fi
SHA8="${SHA:0:8}"
# Computed unconditionally: save_capture names its file from it even when --image-tag was given.
BRANCH_TAG="$(sanitize "$BRANCH" | cut -c1-110 | sed 's/-*$//')"
[ -n "$BRANCH_TAG" ] || BRANCH_TAG="branch"
if [ -z "$IMAGE_TAG" ]; then
    # Test tags only (<branch>-<sha6>); never anything resembling a release tag.
    IMAGE_TAG="${BRANCH_TAG}-${SHA:0:6}"
fi
if [ -z "$INSTANCE" ]; then
    if $PERSISTENT; then INSTANCE="gsv-docker-builder"
    else INSTANCE="gsv-$(sanitize "$BRANCH" | cut -c1-40 | sed 's/-*$//')-${SHA8}"
    fi
fi
# GCE names: [a-z][a-z0-9-]*, no trailing '-', <=63 chars
INSTANCE="$(printf '%s' "$INSTANCE" | sed 's/^[0-9]/b-&/')"
case "$INSTANCE" in ''|-[a-z0-9-]*|*-|*[!a-z0-9-]*) die "invalid instance name '$INSTANCE' (must start [a-z], end [a-z0-9], charset [a-z0-9-])";; esac
[ ${#INSTANCE} -le 63 ] || die "instance name '$INSTANCE' exceeds 63 chars"

# ---------------------------- build spec (base64 JSON => one metadata key)
EXTRA_STR="${EXTRA_FLAGS[*]:-}"
SPEC_B64="$(python3 -c '
import base64, json, sys
(a, t, b, h, tip, drepo, tag, extra, persist, repo, kind) = sys.argv[1:12]
spec = {
    "kind": kind,
    "branch": a,
    "repo_url": repo,
    "docker_repo": drepo,
    "tag": tag,
    "base_sha": b,
    "head_sha": h,
    "pin_sha": h or ("" if b else tip),
    "targets": t.split() if t else [],
    "extra_flags": extra.split(),
    "shutdown": persist != "true",
}
print(base64.b64encode(json.dumps(spec).encode()).decode())
' "$BRANCH" "${TARGETS[*]:-}" "$BASE_SHA" "$HEAD_SHA" "$SHA" "$DOCKER_REPO" "$IMAGE_TAG" "$EXTRA_STR" "$PERSISTENT" "$REPO_URL" "$BUILD_KIND")"

# ---------------------------- preflight (read-only)
vm_sa() {
    [ -n "$SERVICE_ACCOUNT" ] && { printf '%s' "$SERVICE_ACCOUNT"; return; }
    local sa
    sa="$(gcloud iam service-accounts list --project "$PROJECT" \
            --format 'value(email)' 2>/dev/null | grep -m1 -- 'compute@developer.iam.gserviceaccount.com\|compute@developer.gserviceaccount.com' || true)"
    if [ -z "$sa" ]; then
        local num
        num="$(gcloud projects describe "$PROJECT" --format 'value(projectNumber)' 2>/dev/null || true)"
        sa="${num:+${num}-compute@developer.gserviceaccount.com}"
    fi
    printf '%s' "$sa"
}
preflight() {
    say "Preflight"
    echo "project=$PROJECT zone=$ZONE branch=$BRANCH@${SHA:0:8}"
    echo "targets='${TARGETS[*]:-}' auto_sha='${BASE_SHA:-}|${HEAD_SHA:-}' tag=$IMAGE_TAG"
    echo "push_to=$DOCKER_REPO  mode=$([ "$PERSISTENT" = true ] && echo persistent || echo ephemeral) vm=$INSTANCE"
    gcloud compute images list --project ubuntu-os-cloud --limit 1 \
        --filter "family='$IMAGE_FAMILY'" --format 'value(name)' \
        | grep -q . && echo "OK   image family $IMAGE_FAMILY available" \
        || warn "image family $IMAGE_FAMILY not found"
    gcloud compute zones describe "$ZONE" --project "$PROJECT" --format 'value(name)' >/dev/null 2>&1 \
        && echo "OK   zone $ZONE visible (compute API ok)" \
        || warn "cannot describe zone $ZONE — compute.googleapis.com enabled / do you have permissions in $PROJECT?"
    local sa; sa="$(vm_sa)"
    echo "INFO VM service account: ${sa:-<project compute default>}"
    echo "INFO it must be able to push to $DOCKER_REPO; for GCR that is (bucket-scoped):"
    echo "       gcloud storage buckets add-iam-policy-binding gs://us.artifacts.${PROJECT}.appspot.com \\"
    echo "         --member=serviceAccount:${sa:-<compute SA email>} --role=roles/storage.admin"
    echo "INFO (needs permissions you may not have -> project admin; or --service-account an SA that already can)"
    if gcloud storage ls -b "gs://us.artifacts.${PROJECT}.appspot.com" >/dev/null 2>&1; then
        echo "OK   registry bucket us.artifacts.${PROJECT}.appspot.com reachable"
    else
        warn "registry bucket not listable by you (only the VM SA matters for push; harmless if --docker-repo is elsewhere)"
    fi
}
preflight
$CHECK_ONLY && exit 0

# ---------------------------- commands
create_base() {
    CREATE=(gcloud compute instances create "$INSTANCE"
        --project "$PROJECT" --zone "$ZONE" --machine-type "$MACHINE_TYPE"
        --image-family "$IMAGE_FAMILY" --image-project ubuntu-os-cloud
        --boot-disk-size "${DISK_GB}GB" --boot-disk-type pd-ssd --boot-disk-auto-delete
        --scopes cloud-platform --labels created-by=gsv-build)
    [ -n "$SERVICE_ACCOUNT" ] && CREATE+=(--service-account "$SERVICE_ACCOUNT")
    return 0
}
del_vm() {
    if ! gcloud compute instances delete "$INSTANCE" --project "$PROJECT" --zone "$ZONE" --quiet; then
        warn "could not delete $INSTANCE — orphan bills (VM + ${DISK_GB}GB disk)! Run:"
        warn "  gcloud compute instances delete $INSTANCE --project $PROJECT --zone $ZONE --quiet"
    fi
}
stop_vm() { gcloud compute instances stop "$INSTANCE" --project "$PROJECT" --zone "$ZONE" --quiet || warn "could not stop $INSTANCE"; }
vm_status() { gcloud compute instances describe "$INSTANCE" --project "$PROJECT" --zone "$ZONE" --format 'value(status)' 2>/dev/null || echo GONE; }

# Record the image digests this build pushed, from the serial text already held locally (the VM is
# deleted by then). Every path returns 0 and only warns, so a capture failure can never skip VM
# teardown. Must not print the build result marker, which the watch loop matches on.
save_capture() {
    $DRY_RUN && return 0
    if [ -z "${full:-}" ]; then warn "capture skipped: no serial text in hand to read the build's own output from"; return 0; fi
    local capdir serial rc
    capdir="$(gsvtk_work image-capture 2>/dev/null)" || { warn "capture skipped: no resolvable scratch dir (kit/gsvtk-config work image-capture)"; return 0; }
    serial="$capdir/${BRANCH_TAG}-${SHA:0:6}.serial"
    printf '%s\n' "$full" > "$serial" 2>/dev/null || { warn "capture skipped: cannot write $serial"; return 0; }
    "${GSVTK_PYTHON:-python3}" "$SCRIPT_DIR/image_capture.py" --from-serial "$serial" --resolve \
        --module "$(gsvtk_default MODULE single_sample)" --step "${GSVTK_CAPTURE_STEP:-01}" \
        ${SHA:+--at-sha "$SHA"} || rc=$?
    if [ "${rc:-0}" != 0 ]; then
        warn "capture step failed (rc $rc) — the images ARE pushed and teardown proceeds regardless"
        warn "  re-run it against the saved text: $SCRIPT_DIR/image_capture.py --from-serial $serial --resolve"
    fi
    return 0
}

run() { echo "+ $*"; $DRY_RUN && return 0; "$@"; }

if ! $PERSISTENT; then
    # ---------- ephemeral: startup-script = wrapper embedding remote-build.sh
    STARTUP_FILE="$(mktemp "${TMPDIR:-/tmp}/gsv-startup-XXXXXX")"
    trap 'rm -f "$STARTUP_FILE"' EXIT
    {
        echo '#!/bin/sh'
        echo "# generated by gatk-sv-build.sh — embedded builder script"
        echo "mkdir -p /opt/gsv"
        echo "cat > /opt/gsv/remote-build.sh <<'$EMBED_TOKEN'"
        cat "$REMOTE_SCRIPT"
        echo ""   # the heredoc terminator must start its own line
        echo "$EMBED_TOKEN"
        # Not exec: if the builder dies without its own marker, print a FAILURE
        # marker so the driver notices quickly. The watchdog shuts down a hung
        # build, since the builder's self-shutdown only runs if it terminates.
        echo "WATCH_S=$(( TIMEOUT_H * 3600 + 3600 ))"
        echo "( sleep \$WATCH_S; echo \"### GATK_SV_BUILD_RESULT=FAILURE (watchdog: builder hung past \${WATCH_S}s) ###\"; grep -isq google /sys/class/dmi/id/product_name && shutdown -h now ) & GSV_W=\$!"
        echo "bash /opt/gsv/remote-build.sh --from-metadata; GSV_RC=\$?"
        echo "kill \"\$GSV_W\" 2>/dev/null"
        echo "if [ \"\$GSV_RC\" -ne 0 ]; then echo \"### GATK_SV_BUILD_RESULT=FAILURE (builder died rc=\$GSV_RC) ###\"; grep -isq google /sys/class/dmi/id/product_name && shutdown -h now; fi"
    } > "$STARTUP_FILE"

    create_base
    run "${CREATE[@]}" \
        --metadata-from-file "startup-script=$STARTUP_FILE" \
        --metadata "gsv-spec-b64=$SPEC_B64"
    $DRY_RUN && exit 0

    trap 'printf "\n"; warn "detached from watching; the build CONTINUES on the VM and it self-shuts-down. Re-attach: gcloud compute instances get-serial-port-output $INSTANCE --project $PROJECT --zone $ZONE"; exit 130' INT
    say "Building on '$INSTANCE' — streaming serial log (Ctrl-C stops watching, NOT the build)"
    last="" ; rc=2 ; gone=0 ; misspolls=0 ; deadline=$(( $(date +%s) + TIMEOUT_H * 3600 ))
    while :; do
        full="$(gcloud compute instances get-serial-port-output "$INSTANCE" --project "$PROJECT" --zone "$ZONE" 2>/dev/null || true)"
        if [ -n "$full" ] && [ "$full" != "$last" ]; then
            case "$full" in
                "$last"*) printf '%s\n' "${full#"$last"}";;
                *)        printf '%s\n' "$full";;
            esac
            last="$full"
        fi
        case "$full" in
            *"GATK_SV_BUILD_RESULT=SUCCESS"*) rc=0; break;;
            *"GATK_SV_BUILD_RESULT=FAILURE"*) rc=1; break;;
        esac
        # GONE can be a transient API error; require three in a row.
        st="$(vm_status)"
        if [ "$st" = "GONE" ]; then
            gone=$(( gone + 1 ))
            [ "$gone" -ge 3 ] && { sleep 5; rc=1; break; }
        else
            gone=0
        fi
        # Serial output is unreadable once the VM is TERMINATED. If we never saw
        # any and it is already TERMINATED, the result is unrecoverable; stop now.
        if [ "$st" = "TERMINATED" ] && [ -z "$full" ]; then
            misspolls=$(( misspolls + 1 ))
            if [ "$misspolls" -ge 4 ]; then
                warn "VM is TERMINATED and its serial was never readable — the result window was missed (check the on-disk log, not the serial)."
                rc=1; break
            fi
        else
            misspolls=0
            [ -z "$full" ] && echo "(watch: serial not readable yet — status '$st')"
        fi
        [ "$(date +%s)" -ge "$deadline" ] && { rc=2; break; }
        sleep 20
    done
    trap - INT

    if [ "$rc" -eq 0 ]; then
        say "SUCCESS — pushed to $DOCKER_REPO :$IMAGE_TAG"
        # Delete first so the capture can never skip teardown.
        $KEEP || { say "deleting VM"; del_vm; }
        save_capture || warn "capture failed in a way this branch was not written to expect"
        exit 0
    elif [ "$rc" = "1" ]; then
        say "FAILED (see log above)"
        warn "VM kept for postmortem (it self-shuts-down; full log: /var/log/gsv-build.log on its disk)"
        warn "  serial:  gcloud compute instances get-serial-port-output $INSTANCE --project $PROJECT --zone $ZONE"
        warn "  if serial is 'not ready' (VM already TERMINATED): start it (startup script re-runs and RE-BUILDS once),"
        warn "  or attach its disk to a throwaway VM read-only and tail the log (recipe in docs/docker-builds.md)."
        warn "  cleanup: gcloud compute instances delete $INSTANCE --project $PROJECT --zone $ZONE --quiet"
        exit 1
    else
        say "GAVE UP watching after ${TIMEOUT_H}h — the build keeps running on the VM"
        warn "  re-attach: gcloud compute instances get-serial-port-output $INSTANCE --project $PROJECT --zone $ZONE"
        warn "  cleanup:   gcloud compute instances delete $INSTANCE --project $PROJECT --zone $ZONE --quiet"
        exit 2
    fi
fi

# ---------- persistent: (create|start) + DETACHED build over SSH + polling
create_base
st="$(vm_status)"
if [ "$st" != "GONE" ]; then
    owner="$(gcloud compute instances describe "$INSTANCE" --project "$PROJECT" --zone "$ZONE" --format 'value(labels.created-by)' 2>/dev/null || echo UNKNOWN)"
    [ "$owner" = "gsv-build" ] || die "VM '$INSTANCE' exists but is not ours (created-by='$owner'); refusing to scp/sudo/stop someone else's machine — choose another --instance"
fi
if [ "$st" = "GONE" ]; then
    say "Creating persistent builder VM '$INSTANCE' (no startup script; builds run via SSH)"
    run "${CREATE[@]}"
elif [ "$st" = "TERMINATED" ]; then
    say "Starting persistent builder VM '$INSTANCE'"
    run gcloud compute instances start "$INSTANCE" --project "$PROJECT" --zone "$ZONE"
fi
$DRY_RUN && exit 0

say "Waiting for SSH on '$INSTANCE'"
# Until the poll loop takes over, stop the VM on any exit so a failed launch cannot leave it
# billing (unless --keep-running).
GUARD_ARMED=1
prebuild_guard() {
    [ "${GUARD_ARMED:-0}" = 1 ] || return 0
    if [ "$KEEP_RUNNING" = true ]; then
        warn "build did not start, but $INSTANCE stays up because --keep-running asked for that"
        return 0
    fi
    warn "build never reached its poll loop — stopping $INSTANCE so a failed launch cannot bill"
    warn "postmortem: gcloud compute instances start $INSTANCE --project $PROJECT --zone $ZONE"
    warn "           gcloud compute ssh $INSTANCE --project $PROJECT --zone $ZONE --command 'tail -200 /var/log/gsv-build.log'"
    stop_vm
}
trap prebuild_guard EXIT
trap 'prebuild_guard; exit 130' INT TERM

ssh_ready=false
for i in $(seq 1 30); do
    st="$(vm_status)"
    if [ "$st" = "RUNNING" ] \
       && gcloud compute ssh "$INSTANCE" --project "$PROJECT" --zone "$ZONE" --command true >/dev/null 2>&1; then
        ssh_ready=true; break
    fi
    sleep 10
done
$ssh_ready || die "SSH never came up on $INSTANCE (first-boot key setup / OS Login / firewall?). Inspect manually: gcloud compute ssh $INSTANCE --project $PROJECT --zone $ZONE"

ssh_cmd() { gcloud compute ssh "$INSTANCE" --project "$PROJECT" --zone "$ZONE" --command "$1" 2>/dev/null; }

say "Copying builder + launcher; launching the build DETACHED (laptop sleep / VPN blips cannot kill it)"
LAUNCH_FILE="$(mktemp "${TMPDIR:-/tmp}/gsv-launch-XXXXXX")"
trap 'rm -f "$LAUNCH_FILE"; prebuild_guard' EXIT
{
    echo '#!/bin/bash'
    echo 'set -u'
    # Rotate the log so a previous run's SUCCESS marker cannot be matched, and
    # drop any stale pidfile.
    echo 'p=$(cat /tmp/gsv-build.pid 2>/dev/null || true)'
    echo 'if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then echo "GSV_BUSY pid=$p"; tail -n 3 /var/log/gsv-build.log 2>/dev/null || true; exit 1; fi'
    echo 'mv -f /var/log/gsv-build.log /var/log/gsv-build.prev.log 2>/dev/null || true'
    echo 'rm -f /tmp/gsv-build.pid'
    # Never rm the .lock file: flock is per inode, so a new file would let two builders run.
    echo "GSV_SPEC_B64=$SPEC_B64 nohup bash /tmp/gsv-remote-build.sh >/var/log/gsv-build.boot.log 2>&1 </dev/null &"
    echo 'sleep 3'
    echo 'p=$(cat /tmp/gsv-build.pid 2>/dev/null || true)'
    echo 'if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then echo GSV_LAUNCHED; else echo GSV_LAUNCH_FAILED; tail -n 20 /var/log/gsv-build.boot.log /var/log/gsv-build.log 2>/dev/null || true; exit 1; fi'
} > "$LAUNCH_FILE"
gcloud compute scp "$REMOTE_SCRIPT" "$INSTANCE:/tmp/gsv-remote-build.sh" --project "$PROJECT" --zone "$ZONE"
gcloud compute scp "$LAUNCH_FILE" "$INSTANCE:/tmp/gsv-launch.sh" --project "$PROJECT" --zone "$ZONE"
launch_out="$(ssh_cmd "sudo bash /tmp/gsv-launch.sh" || true)"
case "$launch_out" in
    *GSV_LAUNCHED*) ;;
    # Never start a second build on the same checkout.
    *GSV_BUSY*) printf '%s\n' "$launch_out"; die "a build is already running on $INSTANCE (its pid is alive). Watch it instead of starting a second: gcloud compute ssh $INSTANCE --project $PROJECT --zone $ZONE --command 'tail -f /var/log/gsv-build.log'" ;;
    *) printf '%s\n' "$launch_out"; die "builder failed to start on $INSTANCE (tail output above)" ;;
esac

# The poll loop owns cleanup from here on.
GUARD_ARMED=0
rc=2; seen=0; misses=0; deadline=$(( $(date +%s) + TIMEOUT_H * 3600 ))
# Liveness = the builder's own pidfile (written after it takes the flock),
# not `pgrep -f`, which would also match the probe command line itself.
ALIVE_TEST='p=$(cat /tmp/gsv-build.pid 2>/dev/null || true); [ -n "$p" ] && kill -0 "$p" 2>/dev/null'
full=""   # the build log as read so far; save_capture parses it on success
trap 'printf "\n"; warn "detached from watching; the DETACHED build continues on $INSTANCE. Re-attach: gcloud compute ssh $INSTANCE --project $PROJECT --zone $ZONE --command '\''tail -f /var/log/gsv-build.log'\''"; exit 130' INT
while :; do
    # sentinel distinguishes "no new log lines" from "ssh itself failed";
    # GSV_ALIVE distinguishes "quiet but healthy" from "nothing is running"
    chunk="$(ssh_cmd "{ tail -n +$(( seen + 1 )) /var/log/gsv-build.log 2>/dev/null; if $ALIVE_TEST; then echo GSV_ALIVE; fi; echo __GSV_SENTINEL__; }" || true)"
    case "$chunk" in
        *__GSV_SENTINEL__*) misses=0;;
        *) misses=$(( misses + 1 ))
           if [ "$misses" -ge 5 ]; then
               # One last liveness check before deciding the VM should be stopped.
               last_alive="$(gcloud compute ssh "$INSTANCE" --project "$PROJECT" --zone "$ZONE" \
                   --command "if $ALIVE_TEST; then echo YES; else echo NO; fi" 2>&1 | tail -1 || true)"
               case "$last_alive" in
                   *YES*) warn "ssh probes failed but the builder is alive on $INSTANCE — leaving it RUNNING; re-attach and watch manually"; rc=2;;
                   *) warn "5 consecutive ssh probes failed, builder not confirmed alive; last VM status: $(vm_status)"; rc=1;;
               esac
               break
           fi
           sleep 30; continue;;
    esac
    body="${chunk%__GSV_SENTINEL__*}"
    case "$body" in
        *"GATK_SV_BUILD_RESULT=SUCCESS"*) printf '%s\n' "$body"; full="$full$body"; rc=0; break;;
        *"GATK_SV_BUILD_RESULT=FAILURE"*) printf '%s\n' "$body"; rc=1; break;;
    esac
    shown="$(printf '%s\n' "$body" | grep -v '^GSV_ALIVE$' || true)"
    [ -n "$shown" ] && { printf '%s' "$shown"; full="$full$shown"$'\n'; }
    # Advance the cursor from the file's line count, not from bytes received.
    wcnt="$(ssh_cmd "wc -l < /var/log/gsv-build.log 2>/dev/null" || true)"
    wcnt="${wcnt//[^0-9]/}"
    [ -n "$wcnt" ] && seen="$wcnt"
    [ "$(date +%s)" -ge "$deadline" ] && { rc=2; break; }
    sleep 45
done
trap - INT

if [ "$rc" -eq 0 ]; then
    say "SUCCESS — pushed to $DOCKER_REPO :$IMAGE_TAG"
    if ! $KEEP_RUNNING; then
        say "Stopping VM (its pd-ssd disk keeps billing until the VM is deleted)"
        stop_vm
    fi
    full="$(printf '%s\n' "$full" | grep -v '^GSV_ALIVE$' || true)"
    save_capture || warn "capture failed in a way this branch was not written to expect"
    exit 0
elif [ "$rc" -eq 1 ]; then
    say "FAILED — stopping VM to avoid a forgotten running box"
    warn "postmortem: gcloud compute instances start $INSTANCE --project $PROJECT --zone $ZONE"
    warn "           gcloud compute ssh $INSTANCE --project $PROJECT --zone $ZONE --command 'tail -200 /var/log/gsv-build.log'"
    stop_vm
    exit 1
else
    say "GAVE UP watching after ${TIMEOUT_H}h — build continues on the VM"
    warn "  re-attach: gcloud compute ssh $INSTANCE --project $PROJECT --zone $ZONE --command 'tail -f /var/log/gsv-build.log'"
    warn "  persistent mode has NO in-VM watchdog (ephemeral does): if the builder is hung, nothing will ever power this machine off"
    warn "  verify with the pidfile, then stop it: gcloud compute instances stop $INSTANCE --project $PROJECT --zone $ZONE"
    exit 2
fi
