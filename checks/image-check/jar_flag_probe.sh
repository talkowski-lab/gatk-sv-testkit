#!/usr/bin/env bash
# jar_flag_probe.sh -- ask a SHIPPED sv-shell image which GenotypeSVs RD flags its jar accepts.
#
# Why this exists: the branch passes --rd-depth-table/--rd-pesr-table to GenotypeSVs, but the
# image's /opt/gatk.jar is built from a pinned GATK commit. If the pin predates the argument
# split, the shipped image cannot run the branch's sv_shell driver. That is only answerable by
# executing the jar inside the image. (No local docker here, so it runs on a throwaway VM.)
#
# Unlike svshell_image_check.sh this VM stays RUNNING after printing its markers: the driver
# polls the serial log while the instance is alive, then deletes it. GCE makes serial output
# unreadable once an instance is TERMINATED, and the self-deleting variants lost their evidence
# that way.
#
# Usage: jar_flag_probe.sh --image REF [--project P] [--zone Z] [--machine M]
#          (--image is required: name the image whose jar is being asked)
set -uo pipefail

. "$(cd "$(dirname "$0")/../.." && pwd -P)/kit/config.sh"
# The --image guard this directory shares (image_ref.sh). This is the launcher where the reference is
# interpolated into an UNQUOTED heredoc, so a reference carrying a newline arrived in the guest as a
# command on its own line -- root, on the VM, right after `docker pull`. See that file for the bytes.
IMAGE_REF_SH="$(cd "$(dirname "$0")" && pwd -P)/image_ref.sh"
[ -f "$IMAGE_REF_SH" ] \
    || { printf '%b' "jar_flag_probe.sh: the shared --image guard is missing at $IMAGE_REF_SH,\n  and this script writes its argument into a root startup script. Refusing.\n" >&2; exit 2; }
. "$IMAGE_REF_SH"
PROJECT="${GSVTK_PROJECT:-}"
ZONE="${GSVTK_ZONE:-us-central1-a}"
IMAGE=""
MACHINE="${GSVTK_MACHINE_TYPE:-e2-small}"
DISK_GB=50
while [ $# -gt 0 ]; do case $1 in
    --image) IMAGE="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --zone) ZONE="$2"; shift 2;;
    --machine) MACHINE="$2"; shift 2;;
    # Leading comment block, not a fixed range: a window prints N lines whatever the header grew to, so
    # editing this header can silently drop the very flags `--help` exists to list (svshell_image_check.sh
    # hit that with its exit-code legend). Self-bounding now.
    -h|--help) awk 'NR>1 && /^#/ {print; next} NR>1 && /^[[:space:]]*$/ {next} NR>1 {exit}' "$0"; exit 0;;
    *) echo "unknown arg $1" >&2; exit 2;; esac; done
# printf '%b': bash echo prints the \n literally, and this guard is the only interface a
# first-time reader has.
[ -n "$IMAGE" ] || { printf '%b' "no --image given: name the sv-shell image whose /opt/gatk.jar you want to interrogate.\n  e.g.  --image \$(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6>   (docs/static-checks.md)\n" >&2; exit 2; }
# Non-empty is not screened: `$name` below comes from this string and so does the guest's `docker
# pull` line, so the reference is refused before either is built.
image_ref_guard "$0" "$IMAGE"
gsvtk_require PROJECT >/dev/null

# md5 -q is the macOS spelling; on Linux it is md5sum. Name the VM from the image either way,
# so two probes of different images do not collide on one instance name.
_md5() { md5 -q 2>/dev/null || md5sum | cut -d' ' -f1; }
NAME="jarprobe-$(printf '%s' "$IMAGE" | _md5 | cut -c1-6)"
STARTUP=$(mktemp); trap 'rm -f "$STARTUP"' EXIT
cat > "$STARTUP" <<EOS
#!/bin/bash
{
  export HOME=/root DEBIAN_FRONTEND=noninteractive
  apt-get update -qq >/dev/null 2>&1
  apt-get install -y -qq docker.io >/dev/null 2>&1
  systemctl enable --now docker >/dev/null 2>&1; sleep 8
  # A failed pull used to print "### PULL_FAILED" and CARRY ON: docker run's exit status was
  # ignored, the empty help file was grepped, and JAR_FLAG_PROBE=DONE was printed anyway, so the
  # driver read "the shipped image cannot run this branch's driver" out of a registry that was
  # never reachable. A probe with no image has no verdict, so it says so and stops. Same answer the
  # siblings already give (run_in_image.sh::IMAGE_ABSENT, exit 5).
  if ! docker pull ${IMAGE} >/dev/null 2>&1; then echo "JAR_FLAG_PROBE=IMAGE_ABSENT ${IMAGE}"; exit 0; fi
  echo "### pulled ${IMAGE}"
  docker run --rm ${IMAGE} java -jar /opt/gatk.jar GenotypeSVs --help > /tmp/help.txt 2>&1
  # DONE means "there is help text to grade", not "this line was reached". An image that pulls but
  # cannot run the jar (entrypoint moved, /opt/gatk.jar gone, daemon wedged) would otherwise print
  # three confident `absent` rows over an empty file.
  if [ ! -s /tmp/help.txt ] || grep -qiE "Unable to find image|Cannot connect to the Docker daemon|No such file or directory|error while loading shared libraries" /tmp/help.txt; then
    echo "JAR_FLAG_PROBE=NO_HELP"; grep -iE "." /tmp/help.txt | head -3 | sed 's/^/###   /'; exit 0
  fi
  echo "### GenotypeSVs long names found in --help output:"
  for f in rd-depth-table rd-pesr-table rd-table; do
    if grep -qE -- "--\${f}([^a-z]|\$)" /tmp/help.txt; then echo "###   --\${f} = PRESENT"; else echo "###   --\${f} = absent"; fi
  done
  echo "### jar build info:"
  docker run --rm ${IMAGE} java -jar /opt/gatk.jar GenotypeSVs --help 2>&1 | grep -iE "^The Genome Analysis Toolkit|Build.*on|HLAType" | head -3 | sed 's/^/###   /'
  echo "JAR_FLAG_PROBE=DONE"
  sleep 1200
} 2>&1 | sed 's/^/### /'
EOS

echo "=== creating $NAME ==="
# $ZONE and $MACHINE, not literals: a hardcoded zone ignored GSVTK_ZONE, so a quota problem in
# one region could not be worked around without editing this script.
timeout 400 gcloud compute instances create "$NAME" --project=$PROJECT --zone="$ZONE" \
  --machine-type="$MACHINE" --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size="${DISK_GB}GB" --boot-disk-type=pd-ssd --metadata-from-file startup-script="$STARTUP" \
  --scopes=storage-ro --boot-disk-auto-delete --labels=purpose=jarflagprobe \
  || { echo "  create failed (the gcloud error above is the reason)" >&2; exit 1; }
# The delete used to be a background job with its output thrown away, reached only on the normal
# path. A Ctrl-C during the ~15-minute wait, or one transient gcloud failure, left an 8-vCPU VM
# billing to your project and told nobody. Now: registered the moment the VM exists, reached on
# interrupt too, synchronous, and it prints the exact command to run by hand if cleanup itself fails.
cleanup() {
  timeout 300 gcloud compute instances delete "$NAME" --project=$PROJECT --zone="$ZONE" --quiet \
    || printf '  CLEANUP FAILED -- this VM may still be running. Delete it with:\n    gcloud compute instances delete %s --project=%s --zone=%s --quiet\n' "$NAME" "$PROJECT" "$ZONE" >&2
}
trap cleanup EXIT
trap 'trap - EXIT; cleanup; exit 130' INT TERM

ANSWER=""
for i in $(seq 1 45); do
  sleep 20
  # --zone here, not inherited: instances are ZONAL resources, so a get with no zone falls back to the
  # operator's gcloud default. When that default is unset every poll errors, and when it names another
  # zone every poll 404s -- either way the probe waits out its whole ceiling printing `no answer
  # captured` for an image whose answer was on the wire the entire time. create/delete below already
  # pass $ZONE; the poll was the one call that forgot the reason $ZONE exists.
  S=$(timeout 120 gcloud compute instances get-serial-port-output "$NAME" --project=$PROJECT --zone="$ZONE" --port=1 --start=0 2>/dev/null || true)
  if echo "$S" | grep -q "JAR_FLAG_PROBE=DONE"; then ANSWER="$S"; break; fi
  # Stop on the first poll that says there will be no verdict. Waiting 15 minutes to report
  # "no answer captured" is the difference between a 40-second answer and a coffee.
  for m in IMAGE_ABSENT NO_HELP; do
    if echo "$S" | grep -q "JAR_FLAG_PROBE=$m"; then
      printf '  JAR_FLAG_PROBE=%s -- the probe VM could not produce a jar --help, so there is NO verdict here.\n' "$m" >&2
      printf '  This is NOT a finding about which flags the shipped jar accepts: an absent or unrunnable\n' >&2
      printf '  image answers nothing. Check the ref and the registry grant, then probe again.\n' >&2
      exit 5
    fi
  done
  [ $((i % 5)) -eq 0 ] && echo "  [${i}] waiting ($(echo "$S" | grep -c 'startup-script:') lines)"
done
# Deletion happens in the EXIT trap (registered at create time), on this path and on the interrupt
# path alike.
echo
if [ -z "$ANSWER" ]; then echo "  no answer captured (VM deleted -- see the cleanup note if that failed)"; exit 1; fi
echo "=== what the shipped jar accepts ==="
echo "$ANSWER" | sed -n 's/^startup-script: //p' | grep -E "^###" | cut -c1-118 | sed 's/^/  /'
