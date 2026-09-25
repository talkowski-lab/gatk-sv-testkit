#!/usr/bin/env bash
# run_in_image.sh -- run YOUR script inside a shipped image on a throwaway GCE VM, and report the
# rc, a bounded stdout and the VM name -- refusing to boot at all when what you asked it to run
# could not have taught you anything.
#
# Why this exists (GAP-REVIEW-manta-tloc.md §2.4; GAP-REVIEW-single-sample-blocking.md T5, T14,
# rec 3; GAP-REVIEW-synthesis.md A3): the hard parts of this pattern are already in this repo
# TWICE -- checks/image-check/svshell_image_check.sh and checks/image-check/jar_flag_probe.sh --
# but each with the probe BODY hardcoded (one md5s /opt/sv_shell, the other greps
# `GenotypeSVs --help`). So every other "is it in the image / does it run in the image" question
# got answered by hand instead: six throwaway Cloud Build configs written one at a time, and one
# VM deleted six minutes after it booted -- which is the evidence GCE makes unreadable ("serial
# output unreadable once an instance is TERMINATED", jar_flag_probe.sh header). This is that
# entry point, with the body taken as an argument.
#
# What is REUSED, and what is NEW -- said plainly, because the boot path was not exercised here.
#   Reused, copied from those two scripts: the image family, the startup-script-to-serial-console
#   protocol, the metadata-token registry login, the poll-for-marker loop, keep-the-VM-alive.
#   NEW: the guards. All of them are provable offline with --dry-run, which assembles and
#   validates the startup script without touching gcloud:
#     * no --image                          -> refuses, naming --image and the key that supplies one
#     * a 0-byte / whitespace-only / comment-only probe or overlay
#                                           -> refuses. `bash -n` passes on an empty file and
#                                              `grep -c` returns 0 for a one-line file, so BOTH
#                                              guards a previous session relied on approved a
#                                              0-byte startup script and burned ~20 idle
#                                              VM-minutes (manta §2.4).
#     * a reference that looks mis-assembled -> refuses before booting. This is the `rc=125 ...
#       (a space before the tag, two log columns   not found` class: the build log printed the
#        glued together, a KEY=value column        registry prefix and the image name as separate
#        copied whole, a scheme, a doubled slash)   columns, and the copy kept the space.
#     * the image was absent in the VM      -> NOT SCORED, exit 5. The incident: a runner printed
#                                              `MISSING LOCAL IMAGE` and carried on, so two arms
#                                              whose images were missing read as "the flag
#                                              changed nothing" (manta §2.4).
#     * an rc with no output behind it      -> also NOT SCORED: an rc from nothing is not
#                                              attributable to the thing under test.
#
# WHAT RAN IN THE SESSION THAT WROTE THIS: --help, --dry-run (probe mode and overlay mode) and
# every refusal above, offline, with no credentials and no configuration. WHAT DID NOT RUN: every
# gcloud, docker and VM path. --dry-run prints the assembled startup script and the create command
# line; the boot itself is the copied skeleton, unexercised here. It starts a VM in the project
# your profile names -- machine type, zone, disk and the poll ceiling are printed before the
# create call, and no price is quoted because this repo has no rate lookup to quote from.
#
# Usage (every mode also takes the VM options below, and --dry-run):
#   run_in_image.sh --image REF --probe my_probe.sh
#   run_in_image.sh --image REF --overlay test.Dockerfile [--probe my_probe.sh] [--copy f:REMOTE]
#   run_in_image.sh --image REF --run 'svtk -h' --run 'python -c "import pkg_resources"'
# The two proven sibling checks and the lifecycle rule are in docs/static-checks.md
# (`image-check/`); the `rc=125` reference class is in docs/troubleshooting.md.
#
# Exit codes:
#   0  the probe returned 0
#   1  the probe returned nonzero -- the rc IS the answer, and it is printed
#   2  usage, or a guard refused before anything was created
#   3  no result marker before --timeout (VM kept; the read and delete commands are printed)
#   4  a required configuration key is unset (kit/config.sh names it)
#   5  NOT SCORED -- nothing was measured: no image in the VM, the overlay did not build, the
#      marker is unreadable, or the arm produced an rc with no output. Never 0, because "the flag
#      changed nothing" is exactly what a dead arm looks like from here.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
. "$SCRIPT_DIR/../../kit/config.sh"

NL='
'
# GCE metadata values cap at 262144 bytes, and a startup script over the limit fails at create
# time with an error that never mentions size. The probe and any --copy payload ride inside this
# file, so bound the whole thing here and name what to do instead.
METADATA_LIMIT=262144
SAFE_BYTES=$(( METADATA_LIMIT - 60000 ))

PROJECT="${GSVTK_PROJECT:-}"
ZONE="${GSVTK_ZONE:-us-central1-a}"
# e2-small rather than the profile's e2-standard-8: a probe is one pull plus one command.
# svshell_image_check.sh needs 8 vCPUs because it runs a whole-tree jq scan on the VM host.
MACHINE="${GSVTK_MACHINE_TYPE:-e2-small}"
DISK_GB=60            # one image plus its layers; an sv-shell pull alone is ~13 GB
TIMEOUT_S=1500        # driver poll ceiling, printed before booting
MAX_OUT_LINES=60      # bounded stdout: the first N lines of the probe's merged output
SHELL_BIN="bash"
KEEP=0
DRY_RUN=0
IMAGE=""
PROBE=""
OVERLAY=""
VM_NAME=""
RUN_LINES=""          # accumulated Dockerfile RUN lines, newline-separated
COPY_SPECS=""         # accumulated --copy arguments, LOCAL[:REMOTE], newline-separated

usage() {
    cat <<'USAGE'
usage: run_in_image.sh --image REF (--probe SCRIPT | --overlay FILE | --run 'CMD' ...) [options]

  --image REF        the image to run in (required: an unnamed image cannot be evidence)
  --probe SCRIPT     a shell script to execute INSIDE the image. It rides on stdin to
                     `--entrypoint <shell>`, so no bind mount and no build context is needed.
  --overlay FILE     Dockerfile lines appended after a GENERATED `FROM <REF>`; the layer is built
                     in the VM and becomes what the probe runs in. A `FROM` here would start a
                     second stage and `docker run` uses the LAST one, so your --image would not be
                     the image that ran -- refused. A `COPY`/`ADD` line needs a matching --copy.
  --run 'CMD'        shorthand for one `RUN CMD` overlay line (repeatable, order preserved)
  --copy LOCAL[:REMOTE]  put LOCAL in the build context and COPY it to REMOTE (default
                     /probe/<basename>) in the built layer. Refused without --overlay/--run: a
                     COPY with no RUN and no layer to attach to builds nothing.
  --shell SHELL      what to exec inside the image instead of bash (e.g. sh). --entrypoint is
                     replaced by it deliberately: otherwise the image's own ENTRYPOINT swallows
                     the probe as its $1.
  --project P --zone Z --machine M --disk GB   VM placement; project comes from GSVTK_PROJECT
  --timeout MIN      poll ceiling for the result marker (default 25)
  --max-lines N      how many lines of the probe's stdout to print (default 60)
  --vm-name NAME     instance name (default runin-<6 chars from the image>-<HHMMSS>)
  --keep             leave the VM running after a captured verdict (prints the delete command)
  --dry-run          assemble + validate the startup script, print it and the create command, and
                     change NOTHING. Needs no GSVTK_PROJECT. This is the half that is testable
                     offline, so run it before spending a boot on a probe.
  -h|--help          this text

Read docs/troubleshooting.md for `rc=125 ... not found` (a reference assembled from two log
columns) and for the "the VM is gone, so is the serial log" lifecycle rule this script follows.
USAGE
}

die() { printf '%b' "run_in_image.sh: $1" >&2; exit 2; }

while [ $# -gt 0 ]; do
    case "$1" in
        --image) IMAGE="${2:-}"; shift 2 ;;
        --probe) PROBE="${2:-}"; shift 2 ;;
        --overlay) OVERLAY="${2:-}"; shift 2 ;;
        --run) RUN_LINES="${RUN_LINES}${2:-}${NL}"; shift 2 ;;
        --copy) COPY_SPECS="${COPY_SPECS}${2:-}${NL}"; shift 2 ;;
        --shell) SHELL_BIN="${2:-}"; shift 2 ;;
        --project) PROJECT="${2:-}"; shift 2 ;;
        --zone) ZONE="${2:-}"; shift 2 ;;
        --machine) MACHINE="${2:-}"; shift 2 ;;
        --disk) DISK_GB="${2:-}"; shift 2 ;;
        --timeout) case "${2:-}" in '' | *[!0-9]*) die "--timeout must be an integer number of minutes, not \"${2:-}\".\n" ;; esac
                     TIMEOUT_S=$(( $2 * 60 )); shift 2 ;;
        --max-lines) MAX_OUT_LINES="${2:-}"; shift 2 ;;
        --vm-name) VM_NAME="${2:-}"; shift 2 ;;
        --keep) KEEP=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown arg: $1" >&2; usage >&2; exit 2 ;;
    esac
done

# ---------------------------------------------------------------- guard: --image
# Named by name, with the config key that builds one -- every guard in this directory does that,
# because "an unnamed image cannot be evidence" (svshell_image_check.sh).
[ -n "$IMAGE" ] || die "no --image given: name the image you want executed.\n  e.g.  --image \$(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6>\n        built by docker/gatk-sv-build.sh   (docs/static-checks.md has the other two checks)\n"

# ---------------------------------------------- guard: a mis-assembled image reference
# The `rc=125 ... not found` class (GAP-REVIEW-manta-tloc.md §4). `docker pull` reports a name it
# cannot find and exits 125; a runner that scores that rc scores nothing. Every check below is a
# shape that cannot occur in a reference copied out of a tag listing, so a real reference never
# trips it -- and each names the specific shape it saw, rather than "invalid".
image_ref_problem() {
    # Reports the FIRST problem only: two reasons read as one run-on sentence (measured), and the
    # first one is what the person should fix.
    local ref="$1"
    case "$ref" in
        *[[:space:]]*) printf 'contains whitespace -- a reference is one token'; return ;;
        *://*) printf 'carries a URI scheme; the form is host/repo:tag'; return ;;
        *=*) printf 'contains "=", so this looks like a KEY=value column copied whole'; return ;;
        *,* | *\;*) printf 'contains a list separator; --image takes exactly one reference'; return ;;
        -*) printf 'starts with "-", which docker reads as a flag'; return ;;
        *'//'*) printf 'contains a doubled slash: two names joined, not one'; return ;;
        */ | ':' | *: | /*) printf 'has a dangling separator (a leading/trailing "/" or a trailing ":")'; return ;;
        *'*'* | *'?'*) printf 'contains a glob character the shell left unexpanded (nothing matched it)'; return ;;
    esac
    # Docker repository names are lowercase. An uppercase letter in the name half means this came
    # from prose or a log heading, not from a tag listing. [[:upper:]], not [A-Z]: in a UTF-8 locale
    # the range also matches lowercase, which made every honest reference look mis-assembled.
    case "${ref%:*}" in
        *[[:upper:]]*) printf 'has an uppercase letter in the repository name; docker names are lowercase' ;;
    esac
}
REF_PROBLEM="$(image_ref_problem "$IMAGE")"
if [ -n "$REF_PROBLEM" ]; then
    printf '%b' "run_in_image.sh: refusing \"--image $IMAGE\": it $REF_PROBLEM.\n\
  That is the class that reaches a VM as \`rc=125 ... not found\`: the build log printed the\n\
  registry PREFIX and the image NAME as separate columns and the copy kept the space (or lost a\n\
  slash). Join them into one string with nothing between them, or re-read the reference from\n\
  \`gcloud container images list-tags <repo>\`. Nothing was created.\n" >&2
    exit 2
fi

# ------------------------------------------------------ guard: what is to be executed
[ -n "$PROBE$OVERLAY$RUN_LINES$COPY_SPECS" ] \
    || { usage >&2; die "nothing to execute: pass --probe SCRIPT, --overlay FILE, or at least one --run 'CMD'.\n"; }

# The 0-byte class, checked on the CALLER'S input rather than on the assembled file, because that
# is where it is attributable. A script that is empty, whitespace, or comments has no command in
# it, so whatever rc comes back belongs to nothing.
content_bytes() {
    # non-whitespace, non-comment bytes; 0 for a file with no command in it
    sed -e 's/#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' "$1" \
        | grep -v -e '^$' | tr -d '\n' | wc -c | tr -d ' '
}
guard_input_file() {
    local what="$1" path="$2" bytes nb
    [ -n "$path" ] || return 0
    [ -f "$path" ] || die "$what \"$path\" is not a file. Nothing was created.\n"
    bytes=$(wc -c < "$path" | tr -d ' ')
    nb=$(content_bytes "$path")
    if [ "$bytes" -eq 0 ]; then
        die "$what \"$path\" is 0 BYTES -- refusing to boot for it.\n\
  \`bash -n\` passes on an empty file and \`grep -c\` returns 0 for a one-line file, so the two\n\
  guards the previous session trusted both approved a 0-byte startup script, and ~20 idle\n\
  VM-minutes later there was no answer either way (GAP-REVIEW-manta-tloc.md §2.4).\n"
    fi
    [ "$nb" -gt 0 ] || die "$what \"$path\" holds $bytes bytes and ZERO non-comment bytes -- comments only.\n  A probe with no command cannot produce an rc that means anything. Nothing was created.\n"
    printf '  [%s] %s: %s bytes, %s non-comment bytes\n' "$what" "$path" "$bytes" "$nb" >&2
}
guard_input_file probe "$PROBE"
if [ -n "$PROBE" ]; then
    # `bash -n` is NOT sufficient -- see the message above -- but it is free, and it is the guard
    # that catches a probe with bytes in it that do not parse.
    bash -n "$PROBE" || die "probe \"$PROBE\" does not parse (bash -n output above). Nothing was created.\n"
fi
if [ -n "$OVERLAY" ]; then
    guard_input_file overlay "$OVERLAY"
    # A second FROM starts a new stage and `docker run <tag>` runs the LAST stage: the image you
    # named would silently not be the image that ran.
    grep -qiE '^[[:space:]]*FROM[[:space:]]' "$OVERLAY" \
        && die "overlay \"$OVERLAY\" has its own FROM line -- refusing.\n  This script generates \`FROM <your --image>\` as line 1. A second FROM starts a SECOND\n  STAGE, and docker run uses the last one, so the probe would not have been running in the\n  image you named. Delete the FROM line and pass its image as --image.\n"
    grep -qiE '^[[:space:]]*(COPY|ADD)[[:space:]]' "$OVERLAY" && [ -z "$COPY_SPECS" ] \
        && die "overlay \"$OVERLAY\" has a COPY/ADD line but no --copy was given.\n  The build context is assembled inside the VM and holds only what you pass with\n  --copy LOCAL[:REMOTE]; a COPY of anything else fails mid-build, after the pull, in a VM that\n  is already billing. Pass --copy, or drop the line.\n"
fi
[ -n "$COPY_SPECS" ] && [ -z "$OVERLAY$RUN_LINES" ] \
    && die "--copy needs --overlay or --run: a COPY with no RUN and no layer to attach to builds nothing.\n"
case "$SHELL_BIN" in
    '' | *[[:space:]]*) die "--shell must be one word (a shell name or an absolute path), not \"$SHELL_BIN\".\n" ;;
esac
case "$MAX_OUT_LINES" in '' | *[!0-9]*) die "--max-lines must be an integer, not \"$MAX_OUT_LINES\".\n" ;; esac
case "$DISK_GB" in '' | *[!0-9]*) die "--disk must be an integer number of GB, not \"$DISK_GB\".\n" ;; esac
case "$TIMEOUT_S" in '' | *[!0-9]*) die "--timeout must be an integer number of minutes.\n" ;; esac

# ---------------------------------------------------- build the overlay + copy payloads
DOCKERFILE_BODY=""
[ -n "$OVERLAY" ] && DOCKERFILE_BODY="$(cat "$OVERLAY")${NL}"
if [ -n "$RUN_LINES" ]; then
    while IFS= read -r line; do
        [ -n "$line" ] || continue
        DOCKERFILE_BODY="${DOCKERFILE_BODY}RUN ${line}${NL}"
    done <<EORL
$RUN_LINES
EORL
fi
# --copy payloads: the file's base64 is embedded in the startup script as a heredoc, so no bucket,
# no scp and no extra credentials move a probe INTO the image. Same trick svshell_image_check.sh
# uses for the scanner blob. The build context name is the basename, so two sources sharing one
# basename would collide and the second COPY would read the first file -- the two-places-one-value
# bug CONTRIBUTING.md calls this repo's worst history, so it is refused rather than resolved.
COPY_PAYLOAD=""
CTX_SEEN=""
DOCKERFILE_BODY_B64=""
HAS_OVERLAY=""
if [ -n "$COPY_SPECS" ]; then
    COPY_PAYLOAD="  mkdir -p /tmp/buildctx"   # the payloads land inside the context: make it first
    idx=0
    while IFS= read -r spec; do
        [ -n "$spec" ] || continue
        src="${spec%%:*}"
        case "$spec" in *:*) remote="${spec#*:}" ;; *) remote="/probe/$(basename "$src")" ;; esac
        [ -f "$src" ] || die "--copy source \"$src\" is not a file.\n"
        [ -s "$src" ] || die "--copy source \"$src\" is 0 bytes: an empty COPY proves nothing about an image.\n"
        ctx="$(basename "$src")"
        case "$CTX_SEEN" in
            *"${NL}${ctx}${NL}"*) die "--copy: two sources share the basename \"$ctx\". The build context holds ONE file\n  under that name and both COPY lines would read it, silently. Rename one of them.\n" ;;
        esac
        CTX_SEEN="${CTX_SEEN}${NL}${ctx}${NL}"
        idx=$(( idx + 1 ))
        DOCKERFILE_BODY="${DOCKERFILE_BODY}COPY ${ctx} ${remote}${NL}"
        COPY_PAYLOAD="${COPY_PAYLOAD}${NL}  # ctx=${ctx}  src=$(basename "$src")
  base64 -d > '/tmp/buildctx/${ctx}' <<'EOCTX${idx}'
$(base64 < "$src")
EOCTX${idx}
  echo \"### build context: ${ctx} = \$(wc -c < '/tmp/buildctx/${ctx}') bytes\""
    done <<EOCS
$COPY_SPECS
EOCS
    HAS_OVERLAY=yes
fi
if [ -n "$DOCKERFILE_BODY" ]; then
    HAS_OVERLAY=yes
    DOCKERFILE_BODY_B64="$(printf '%s' "$DOCKERFILE_BODY" | base64 | tr -d '\n')"
fi

# --------------------------------------------------------- assemble the startup script
STARTUP="$(mktemp -t runin-startup.XXXXXX)"
trap 'rm -f "$STARTUP"' EXIT
cat > "$STARTUP" <<'EOS'
#!/bin/bash
# Assembled by checks/image-check/run_in_image.sh -- edit the tool, not this copy.
# Everything inside the braces reaches serial console port 1 with a "### " prefix, which is what
# the driver greps for. It does NOT power the machine off: GCE makes serial output unreadable once
# an instance is TERMINATED, so the evidence leaves with the instance (the lesson recorded at the
# top of checks/image-check/jar_flag_probe.sh). Deleting is the driver's job, and it only deletes
# once it has read a verdict.
{
  export HOME=/root DEBIAN_FRONTEND=noninteractive
  IMAGE="__IMAGE__"
  SHELL_BIN="__SHELL__"
  MAX_LINES="__MAX_OUT_LINES__"
  echo "### run_in_image start: $(date -u) image=$IMAGE shell=$SHELL_BIN"
  apt-get update -qq >/dev/null 2>&1
  apt-get install -y -qq docker.io jq ca-certificates curl >/dev/null 2>&1 \
    || echo "### apt-get failed (docker and jq are what everything below needs): expect a failure marker"
  systemctl enable --now docker >/dev/null 2>&1
  sleep 8

  # Registry auth from the instance's own identity: metadata token, no gcloud, no ADC. Copied from
  # svshell_image_check.sh. A public image needs none of it, so a failure here is printed and the
  # pull below is what actually decides.
  TOKEN=$(curl -s -H "Metadata-Flavor: Google" \
    http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token \
    | jq -r .access_token 2>/dev/null)
  REG="${IMAGE%%/*}"
  if [ -n "$TOKEN" ] && [ "$TOKEN" != "null" ]; then
    printf '%s' "$TOKEN" | docker login -u oauth2accesstoken --password-stdin "$REG" >/dev/null 2>&1 \
      || echo "### docker login $REG failed (fine for a public image, fatal for a private one)"
  else
    echo "### no metadata token; pulling unauthenticated"
  fi

  echo "### pulling $IMAGE (an sv-shell-sized image is ~13 GB)"
  if ! docker pull "$IMAGE" >/dev/null 2>&1; then
    # The guard manta §2.4 asks for, enforced HERE so no rc downstream can be quoted as a result:
    # a probe run against an image that is not present measures nothing. The runner that printed
    # "MISSING LOCAL IMAGE" and carried on made two dead arms read as "the flag changed nothing".
    echo "### IMAGE ABSENT: docker pull $IMAGE failed; the probe was NOT executed"
    echo "RUN_IN_IMAGE=IMAGE_ABSENT"
    sleep 1200
    exit 0
  fi
  echo "### pulled $IMAGE id=$(docker image inspect -f '{{.Id}}' "$IMAGE" 2>/dev/null)"

  RUN_TARGET="$IMAGE"
__COPY_PAYLOAD__
  if [ -n "__HAS_OVERLAY__" ]; then
    mkdir -p /tmp/buildctx
    { echo "FROM $IMAGE"; printf '%s' "__OVERLAY_B64__" | base64 -d; } > /tmp/buildctx/Dockerfile
    echo "### overlay Dockerfile (generated line 1 is the FROM; the rest is yours):"
    sed 's/^/### | /' /tmp/buildctx/Dockerfile
    if docker build -q -t runin-layer:latest /tmp/buildctx >/tmp/build.log 2>&1; then
      RUN_TARGET="runin-layer:latest"
      echo "### overlay built: $RUN_TARGET (last 20 build-log lines)"
      tail -n 20 /tmp/build.log | sed 's/^/### | /'
    else
      echo "### OVERLAY BUILD FAILED rc=$? (the probe was NOT executed against a layer that does not exist)"
      tail -n 40 /tmp/build.log | sed 's/^/### | /'
      echo "RUN_IN_IMAGE=BUILD_FAILED"
      sleep 1200
      exit 0
    fi
  fi

  RC=0
  if [ -n "__PROBE_B64__" ]; then
    printf '%s' "__PROBE_B64__" | base64 -d > /tmp/probe.sh
    echo "### probe: $(wc -l < /tmp/probe.sh) lines, $(wc -c < /tmp/probe.sh) bytes, target=$RUN_TARGET"
    # --entrypoint is REPLACED so the image's own ENTRYPOINT cannot swallow the probe as its $1;
    # the script rides on stdin, so no bind mount and no daemon config change.
    docker run --rm -i --entrypoint "$SHELL_BIN" "$RUN_TARGET" -s < /tmp/probe.sh >/tmp/out.txt 2>&1 || RC=$?
    echo "### probe rc=$RC"
  else
    # Overlay-only mode: the RUN lines ARE the assertions, so the build's rc is the verdict and
    # the build log is the output. Nothing else ran, and this line says so.
    echo "### no probe given: the verdict is the overlay build's rc (0 = every RUN line succeeded)"
    [ -f /tmp/build.log ] && cp /tmp/build.log /tmp/out.txt
  fi
  TOTAL_LINES=$(wc -l < /tmp/out.txt 2>/dev/null | tr -d ' ')
  echo "### output: PROBE_OUT_LINES=${TOTAL_LINES:-0} captured, first $MAX_LINES shown"
  echo "### output begin"
  sed -n "1,${MAX_LINES}p" /tmp/out.txt 2>/dev/null | cut -c1-200 | sed 's/^/### | /'
  echo "### output end"
  echo "RUN_IN_IMAGE=RC$RC"
  # Stay attached so the serial buffer keeps its tail. The instance does NOT stop itself -- that
  # is the point (jar_flag_probe.sh): the driver reads the log first, then deletes.
  sleep 1200
} 2>&1 | sed 's/^/### /'
EOS

# Substitute the payloads. python, not sed: the blobs are base64 (no sed metacharacters survive
# that) and the values are arbitrary user text. svshell_image_check.sh uses the same shape.
PROBE_B64=""
[ -n "$PROBE" ] && PROBE_B64="$(base64 < "$PROBE" | tr -d '\n')"
python3 - "$STARTUP" "$IMAGE" "$SHELL_BIN" "$MAX_OUT_LINES" "$PROBE_B64" \
    "$DOCKERFILE_BODY_B64" "$COPY_PAYLOAD" "$HAS_OVERLAY" <<'PYEOF'
import pathlib
import sys
path, image, shell, max_lines, probe_b64, body_b64, copy_payload, has_overlay = sys.argv[1:9]
p = pathlib.Path(path)
s = (p.read_text()
     .replace("__IMAGE__", image)
     .replace("__SHELL__", shell)
     .replace("__MAX_OUT_LINES__", max_lines)
     .replace("__PROBE_B64__", probe_b64)
     .replace("__OVERLAY_B64__", body_b64)
     .replace("__COPY_PAYLOAD__", copy_payload)
     # in-band emptiness test for the generated Dockerfile step
     .replace("__HAS_OVERLAY__", "overlay" if has_overlay else ""))
p.write_text(s)
PYEOF

# ------------------------------------------- validate the assembled script (all offline)
# The four things that make an assembled file worth a VM: it parses, it holds a command, it
# carries the marker the driver polls for, and it fits inside one metadata value.
validate_startup() {
    bash -n "$STARTUP" || die "the ASSEMBLED startup script does not parse (output above). That is a bug\n  in the assembler, not in your probe -- report it with the --dry-run output. Nothing was created.\n"
    local bytes nb
    bytes=$(wc -c < "$STARTUP" | tr -d ' ')
    nb=$(content_bytes "$STARTUP")
    [ "$nb" -gt 0 ] || die "the ASSEMBLED startup script holds zero non-comment bytes.\n"
    grep -q "RUN_IN_IMAGE=" "$STARTUP" \
        || die "the assembled script never prints the RUN_IN_IMAGE= marker.\n  The driver would poll a live VM for $(( TIMEOUT_S / 60 )) minutes and then report TIMEOUT: a\n  script that cannot answer is not a probe. Assembler bug; nothing was created.\n"
    [ "$bytes" -lt "$SAFE_BYTES" ] \
        || die "the assembled startup script is $bytes bytes; a GCE metadata value caps at\n  $METADATA_LIMIT and the create call fails with an error that never mentions size.\n  Shrink the probe / --copy payloads, or have the probe read its own inputs out of a bucket\n  instead of embedding them.\n"
    printf '  [validate] startup script: %s bytes, %s non-comment bytes, marker present, bash -n clean\n' "$bytes" "$nb"
}

_md5() { md5 -q 2>/dev/null || md5sum | cut -d' ' -f1; }
[ -n "$VM_NAME" ] || VM_NAME="runin-$(printf '%s' "$IMAGE" | _md5 | cut -c1-6)-$(date +%H%M%S)"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "== --dry-run: nothing was created, and this half needs no configuration at all"
    echo "   instance : $VM_NAME  ($MACHINE, ${DISK_GB}GB pd-ssd, $ZONE)"
    echo "   image    : $IMAGE"
    if [ -n "$PROBE" ]; then echo "   probe    : $PROBE"; fi
    if [ -n "$HAS_OVERLAY" ]; then echo "   overlay  : FROM $IMAGE + the lines above (built inside the VM)"; fi
    echo "   exec     : --entrypoint $SHELL_BIN, probe on stdin (the image's ENTRYPOINT is replaced)"
    echo "   poll for : RUN_IN_IMAGE=<marker>, ceiling $(( TIMEOUT_S / 60 )) min"
    validate_startup
    echo "===== assembled startup script (would be passed with --metadata-from-file) ====="
    cat "$STARTUP"
    echo "===== end of startup script; the create call would be: ====="
    printf 'gcloud compute instances create %s --project <GSVTK_PROJECT> --zone %s \\\n' "$VM_NAME" "$ZONE"
    printf '  --machine-type %s --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud \\\n' "$MACHINE"
    printf '  --boot-disk-size %sGB --boot-disk-type pd-ssd --boot-disk-auto-delete \\\n' "$DISK_GB"
    printf '  --scopes cloud-platform --labels purpose=runinimage --metadata-from-file startup-script=<assembled>\n'
    echo "then: poll get-serial-port-output for RUN_IN_IMAGE=, print rc + bounded stdout + this name,"
    echo "and delete only after a verdict was captured (otherwise print the delete command and keep the VM)."
    exit 0
fi

# ------------------------------------------------------ boot (the copied skeleton)
gsvtk_require PROJECT >/dev/null
LOG="$(mktemp -t runin-serial.XXXXXX)"

echo "==> what this boots: instance $VM_NAME, $MACHINE, ${DISK_GB}GB pd-ssd, $ZONE, project $PROJECT"
echo "==> image $IMAGE; poll ceiling $(( TIMEOUT_S / 60 )) min; the boot disk is auto-deleted WITH the instance"
echo "==> no price is quoted here: this repo has no rate lookup, so multiply the machine type by your own"
echo "==> nothing is deleted on a failure path -- the read and delete commands are printed instead, so the"
echo "    serial log survives long enough to be read (docs/troubleshooting.md, \"Build failed, and the VM is gone\")"
validate_startup

gcloud compute instances create "$VM_NAME" --project "$PROJECT" --zone "$ZONE" \
    --machine-type "$MACHINE" --image-family ubuntu-2404-lts-amd64 \
    --image-project ubuntu-os-cloud --boot-disk-size "${DISK_GB}GB" --boot-disk-type pd-ssd \
    --boot-disk-auto-delete --scopes cloud-platform --labels purpose=runinimage \
    --metadata-from-file "startup-script=$STARTUP" >/dev/null \
    || { echo "  create failed (the gcloud error above is the reason); nothing was created to clean up" >&2; exit 1; }
echo "==> created $VM_NAME"
# An interrupt from here on leaves a live VM, and the one thing jar_flag_probe's history says never
# to do is leave one silently: print the exact commands and get out. The text is inline rather than
# a call to manual_delete, because a trap registered here must not depend on a function that the
# script has not defined yet.
trap 'printf "%s\n" "==> interrupted; the VM is still up. Read the log, then delete it:" >&2; printf "    gcloud compute instances get-serial-port-output %s --project=%s --zone=%s --port=1 --start=0\n    gcloud compute instances delete %s --project=%s --zone=%s --quiet\n" "$VM_NAME" "$PROJECT" "$ZONE" "$VM_NAME" "$PROJECT" "$ZONE" >&2; exit 130' INT TERM

delete_vm() {
    gcloud compute instances delete "$VM_NAME" --project "$PROJECT" --zone "$ZONE" --quiet >/dev/null 2>&1 \
        || printf '  CLEANUP FAILED -- this VM may still be running. Delete it with:\n    gcloud compute instances delete %s --project=%s --zone=%s --quiet\n' "$VM_NAME" "$PROJECT" "$ZONE" >&2
}
manual_delete() {
    printf '  VM %s IS STILL RUNNING and billing. Read the log, then delete it:\n    gcloud compute instances get-serial-port-output %s --project=%s --zone=%s --port=1 --start=0\n    gcloud compute instances delete %s --project=%s --zone=%s --quiet\n' \
        "$VM_NAME" "$VM_NAME" "$PROJECT" "$ZONE" "$VM_NAME" "$PROJECT" "$ZONE" >&2
}

deadline=$(( $(date +%s) + TIMEOUT_S ))
MARKER=""
while [ "$(date +%s)" -lt "$deadline" ]; do
    sleep 30
    gcloud compute instances get-serial-port-output "$VM_NAME" --project "$PROJECT" \
        --zone "$ZONE" --port 1 --start=0 > "$LOG" 2>/dev/null || true
    MARKER="$(grep -a -o 'RUN_IN_IMAGE=[A-Za-z0-9_]*' "$LOG" | tail -1)"
    [ -n "$MARKER" ] && break
    echo "    ... $(du -h "$LOG" | cut -f1) of serial log, no marker yet"
done

show_output() {
    # GCE prefixes startup-script stdout with "startup-script: ", so anchoring on "^###" matches
    # nothing (a comment in svshell_image_check.sh, bought with a lost run). Bounded so a chatty
    # probe cannot bury the verdict.
    grep -a '### ' "$LOG" | sed 's/.*startup-script: //' | cut -c1-200 | sed -n '1,400p' | sed 's/^/  /'
}

if [ -z "$MARKER" ]; then
    echo "==> NOT SCORED: no result marker within $(( TIMEOUT_S / 60 )) min. VM $VM_NAME was KEPT."
    show_output
    manual_delete
    exit 3
fi
show_output
case "$MARKER" in
    RUN_IN_IMAGE=IMAGE_ABSENT)
        echo "==> NOT SCORED: the image was ABSENT in the VM -- \`docker pull $IMAGE\` failed and the"
        echo "    probe never ran. Do NOT record this arm as \"no difference\": that is how two dead"
        echo "    arms read as \"the flag changed nothing\" (GAP-REVIEW-manta-tloc.md §2.4)."
        echo "    For a private registry the VM's own service account needs the read grant --"
        echo "    --impersonate-service-account changes the caller, never the VM (docs/troubleshooting.md)."
        manual_delete
        exit 5 ;;
    RUN_IN_IMAGE=BUILD_FAILED)
        echo "==> NOT SCORED: the overlay layer did not build, so the probe never ran against it."
        manual_delete
        exit 5 ;;
esac
RC="${MARKER#RUN_IN_IMAGE=RC}"
case "$RC" in '' | *[!0-9]*) echo "==> NOT SCORED: unparseable marker \"$MARKER\""; manual_delete; exit 5 ;; esac
OUT_LINES="$(grep -a -o 'PROBE_OUT_LINES=[0-9]*' "$LOG" | tail -1 | cut -d= -f2)"
if [ "$RC" != "0" ] && [ "${OUT_LINES:-0}" -eq 0 ]; then
    # An rc with nothing behind it is not attributable to the thing under test: docker's own
    # 125/126/127 (image not there, not executable, command not in the image) read exactly like a
    # probe that failed hard. manta §2.4's zero-record arm, one layer up.
    echo "==> NOT SCORED: rc=$RC with ZERO lines of probe output. The probe probably never started"
    echo "    (125/126/127 = docker could not run $SHELL_BIN in this image). Fix --shell or the"
    echo "    reference and re-run; recording this as a finding would be guessing."
    manual_delete
    exit 5
fi
echo "==> verdict: probe rc=$RC, scored against ${OUT_LINES:-0} line(s) of captured output"
if [ "$KEEP" -eq 1 ]; then
    echo "==> --keep: VM $VM_NAME left running; the probe's FULL output is /tmp/out.txt on it"
    manual_delete
else
    delete_vm
    echo "==> deleted $VM_NAME (serial log kept locally at $LOG)"
fi
exit "$RC"
