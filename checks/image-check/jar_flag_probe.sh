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
    -h|--help) sed -n '2,14p' "$0"; exit 0;;
    *) echo "unknown arg $1" >&2; exit 2;; esac; done
# printf '%b': bash echo prints the \n literally, and this guard is the only interface a
# first-time reader has.
[ -n "$IMAGE" ] || { printf '%b' "no --image given: name the sv-shell image whose /opt/gatk.jar you want to interrogate.\n  e.g.  --image \$(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6>   (docs/static-checks.md)\n" >&2; exit 2; }
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
  docker pull ${IMAGE} >/dev/null 2>&1 || echo "### PULL_FAILED"
  echo "### pulled ${IMAGE}"
  docker run --rm ${IMAGE} java -jar /opt/gatk.jar GenotypeSVs --help > /tmp/help.txt 2>&1
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
  --scopes=storage-rw --labels=purpose=jarflagprobe >/dev/null 2>&1 || { echo "  create failed" >&2; exit 1; }

ANSWER=""
for i in $(seq 1 45); do
  sleep 20
  S=$(timeout 120 gcloud compute instances get-serial-port-output "$NAME" --project=$PROJECT --port=1 --start=0 2>/dev/null || true)
  if echo "$S" | grep -q "JAR_FLAG_PROBE=DONE"; then ANSWER="$S"; break; fi
  [ $((i % 5)) -eq 0 ] && echo "  [${i}] waiting ($(echo "$S" | grep -c 'startup-script:') lines)"
done
timeout 300 gcloud compute instances delete "$NAME" --project=$PROJECT --zone="$ZONE" --quiet >/dev/null 2>&1 &

echo
if [ -z "$ANSWER" ]; then echo "  no answer captured (VM deleted anyway)"; exit 1; fi
echo "=== what the shipped jar accepts ==="
echo "$ANSWER" | sed -n 's/^startup-script: //p' | grep -E "^###" | cut -c1-118 | sed 's/^/  /'
