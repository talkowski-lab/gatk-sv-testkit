#!/usr/bin/env bash
# jar_flag_probe.sh -- ask a SHIPPED sv-shell image which GenotypeSVs RD flags its jar accepts.
#
# The sv_shell driver passes --rd-depth-table/--rd-pesr-table to GenotypeSVs; if the image's
# pinned GATK predates those flags, the image cannot run it. Runs the jar on a throwaway GCE VM.
# The VM stays running until the driver has read its serial log (serial output is unreadable once
# an instance is terminated), then the driver deletes it.
#
# Usage: jar_flag_probe.sh --image REF [--project P] [--zone Z] [--machine M]
#          (--image is required: name the image whose jar is being asked)
set -uo pipefail

. "$(cd "$(dirname "$0")/../.." && pwd -P)/kit/config.sh"
# Shared --image guard. The reference is interpolated into an unquoted heredoc run as root on the VM.
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
    # Print the leading comment block.
    -h|--help) awk 'NR>1 && /^#/ {print; next} NR>1 && /^[[:space:]]*$/ {next} NR>1 {exit}' "$0"; exit 0;;
    *) echo "unknown arg $1" >&2; exit 2;; esac; done
[ -n "$IMAGE" ] || { printf '%b' "no --image given: name the sv-shell image whose /opt/gatk.jar you want to interrogate.\n  e.g.  --image \$(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6>   (docs/static-checks.md)\n" >&2; exit 2; }
image_ref_guard "$0" "$IMAGE"
gsvtk_require PROJECT >/dev/null

# Name the VM from the image hash so probes of different images don't collide (md5 on macOS, md5sum on Linux).
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
timeout 400 gcloud compute instances create "$NAME" --project="$PROJECT" --zone="$ZONE" \
  --machine-type="$MACHINE" --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size="${DISK_GB}GB" --boot-disk-type=pd-ssd --metadata-from-file startup-script="$STARTUP" \
  --scopes=storage-ro --boot-disk-auto-delete --labels=purpose=jarflagprobe \
  || { echo "  create failed (the gcloud error above is the reason)" >&2; exit 1; }
# Delete the VM on exit or interrupt, registered as soon as it exists so it can't be left billing.
# If the delete fails, print the command to run by hand.
cleanup() {
  rm -f "$STARTUP"
  timeout 300 gcloud compute instances delete "$NAME" --project="$PROJECT" --zone="$ZONE" --quiet \
    || printf '  CLEANUP FAILED -- this VM may still be running. Delete it with:\n    gcloud compute instances delete %s --project=%s --zone=%s --quiet\n' "$NAME" "$PROJECT" "$ZONE" >&2
}
trap cleanup EXIT
trap 'trap - EXIT; cleanup; exit 130' INT TERM

ANSWER=""
for i in $(seq 1 45); do
  sleep 20
  # Instances are zonal; without --zone the poll would use the gcloud default zone.
  S=$(timeout 120 gcloud compute instances get-serial-port-output "$NAME" --project="$PROJECT" --zone="$ZONE" --port=1 --start=0 2>/dev/null || true)
  if echo "$S" | grep -q "JAR_FLAG_PROBE=DONE"; then ANSWER="$S"; break; fi
  # Stop early when the guest reports there will be no verdict.
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
# The VM is deleted by the EXIT trap.
echo
if [ -z "$ANSWER" ]; then echo "  no answer captured (VM deleted -- see the cleanup note if that failed)"; exit 1; fi
echo "=== what the shipped jar accepts ==="
echo "$ANSWER" | sed -n 's/^startup-script: //p' | grep -E "^###" | cut -c1-118 | sed 's/^/  /'
