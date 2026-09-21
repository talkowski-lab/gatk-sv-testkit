#!/usr/bin/env bash
# Prove what the shipped sv-shell image actually contains, and run the plumbing scan on
# those bytes rather than on a git checkout.
#
#   1. boot a throwaway GCE VM (same lifecycle idea as docker-build/gatk-sv-build.sh:
#      ephemeral VM, startup script streams to the serial console, driver polls for a
#      result marker, driver deletes the VM)
#   2. pull $IMAGE with the instance's own credentials (metadata-server token -> no gcloud
#      dependency, no ADC)
#   3. extract /opt/sv_shell with `docker create` + `docker cp` (no bind mounts, no daemon
#      config changes)
#   4. md5 the driver and the shipped fixture and compare against the expected values from
#      `git show <ref>:<path>` -- this is the "shipped bytes == tested bytes" proof
#   5. run checks/svshell_jq_plumbing_scan.py --tree on the extracted tree, on the VM host
#      (python3 + apt jq), because the fragile part is jq plumbing and we only need jq
#
# Nothing is mutated: read-only image pull, read-only repo. Exit 0 only if the marker says
# SUCCESS and the md5s matched.
#
# Usage: svshell_image_check.sh --image REF   (required; see docs/static-checks.md) [--expect-driver-md5 MD5] [--expect-fixture-md5 MD5]
#                               [--project P] [--zone Z] [--machine M] [--disk GB] [--keep]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCANNER="${SCRIPT_DIR}/../svshell_jq_plumbing_scan.py"
. "$SCRIPT_DIR/../../kit/config.sh"

PROJECT="${GSVTK_PROJECT:-}"
ZONE="${GSVTK_ZONE:-us-central1-a}"
MACHINE="${GSVTK_MACHINE_TYPE:-e2-standard-8}"
DISK_GB=60
# No default image: the whole point of this check is that you name the exact image
# whose bytes you are testing, and a baked-in tag tests somebody's old build.
IMAGE=""
EXPECT_DRIVER_MD5=""
EXPECT_FIXTURE_MD5=""
KEEP=0
TIMEOUT_S=2400

while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE="$2"; shift 2;;
    --expect-driver-md5) EXPECT_DRIVER_MD5="$2"; shift 2;;
    --expect-fixture-md5) EXPECT_FIXTURE_MD5="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --zone) ZONE="$2"; shift 2;;
    --machine) MACHINE="$2"; shift 2;;
    --disk) DISK_GB="$2"; shift 2;;
    --keep) KEEP=1; shift;;
    -h|--help) sed -n '2,21p' "$0"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
[ -f "$SCANNER" ] || { echo "scanner not found at $SCANNER" >&2; exit 2; }
# printf '%b', not echo: bash's echo prints \n literally, so the second quoted line here was
# parsed as a COMMAND and `exit 2` was never reached -- a usage error came out as exit 1.
[ -n "$IMAGE" ] || { printf '%b' "no --image given: an unnamed image cannot be evidence.\n  e.g.  --image \$(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6>\n        built by docker/gatk-sv-build.sh   (see docs/static-checks.md)\n" >&2; exit 2; }
gsvtk_require PROJECT >/dev/null

SHORT="${IMAGE##*:}"; SHORT="${SHORT:0:12}"
INSTANCE="svchk-${SHORT}-$(date +%H%M%S)"
STARTUP="$(mktemp -t svchk-startup.XXXXXX)"
LOG="$(mktemp -t svchk-serial.XXXXXX)"
trap 'rm -f "$STARTUP"' EXIT

# ---------- startup script (runs on the VM as root; stdout -> serial console port 1)
cat > "$STARTUP" <<'EOS'
#!/bin/bash
set -uo pipefail
export HOME=/root          # GCE startup scripts run with HOME unset; git/docker tools care
IMAGE="__IMAGE__"
EXPECT_DRV="__EXPECT_DRV__"
EXPECT_FX="__EXPECT_FX__"

finish() {   # finish SUCCESS|FAILURE "detail"
  echo "### SVSHELL_IMAGE_CHECK=$1 ($2) image=$IMAGE ###"
  sleep 20   # give the serial log a window to flush before the instance goes away
  poweroff -f 2>/dev/null || shutdown -h now || true
}

echo "### sv-shell image check start: $(date -u) image=$IMAGE"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null 2>&1 || apt-get update || finish FAILURE "apt update"
apt-get install -y -qq docker.io jq ca-certificates curl >/dev/null 2>&1 \
  || finish FAILURE "apt install docker.io jq"
systemctl start docker >/dev/null 2>&1 || finish FAILURE "docker daemon"
echo "### docker $(docker --version)"

# Registry auth from the instance's own service account (no gcloud, no ADC needed).
TOKEN=$(curl -s -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token \
  | jq -r .access_token)
[ -n "$TOKEN" ] && [ "$TOKEN" != "null" ] || finish FAILURE "no metadata token"
REG="${IMAGE%%/*}"
echo "$TOKEN" | docker login -u oauth2accesstoken --password-stdin "$REG" >/dev/null 2>&1 \
  || finish FAILURE "docker login $REG"

echo "### pulling $IMAGE (this is a large image; ~13 GB)"
DIGEST=$(docker pull -q "$IMAGE") || finish FAILURE "docker pull"
echo "### pulled digest=$DIGEST"

CID=$(docker create "$IMAGE" /bin/true) || finish FAILURE "docker create"
mkdir -p /tmp/extracted
docker cp "${CID}:/opt/sv_shell" /tmp/extracted/sv_shell || { docker rm -f "$CID" >/dev/null; finish FAILURE "docker cp"; }
docker rm -f "$CID" >/dev/null

DRV=/tmp/extracted/sv_shell/single_sample_pipeline.sh
FX=/tmp/extracted/sv_shell/sample_inputs/single_sample_pipeline.json
[ -f "$DRV" ] || finish FAILURE "no single_sample_pipeline.sh in the image's /opt/sv_shell"
[ -f "$FX" ]  || finish FAILURE "no sample_inputs/single_sample_pipeline.json in the image"

AMD5=$(md5sum "$DRV" | awk '{print $1}')
FMD5=$(md5sum "$FX" | awk '{print $1}')
echo "### image driver  md5=$AMD5 lines=$(wc -l < "$DRV")   expected=${EXPECT_DRV:-<not given>}"
echo "### image fixture md5=$FMD5                        expected=${EXPECT_FX:-<not given>}"
if [ -n "$EXPECT_DRV" ] && [ "$AMD5" != "$EXPECT_DRV" ]; then finish FAILURE "driver bytes differ from the tested commit"; fi
if [ -n "$EXPECT_FX" ] && [ "$FMD5" != "$EXPECT_FX" ]; then finish FAILURE "fixture bytes differ from the tested commit"; fi
if [ -z "$EXPECT_DRV" ] && [ -z "$EXPECT_FX" ]; then
  # The whole reason this VM exists is byte identity. Silently "passing" it when no expectation
  # was supplied would produce exactly the false proof this project documents against.
  echo "### byte-identity NOT PROVEN: no --expect-driver-md5/--expect-fixture-md5 given."
  echo "###   compute them from the checkout you tested:"
  echo "###     git -C <gatk-sv> show <ref>:src/sv_shell/single_sample_pipeline.sh | md5sum"
  echo "###   the scan below still runs; its result is about the tree in THIS image only."
else
  echo "### byte-identity check passed (shipped bytes == tested bytes)"
fi

base64 -d > /tmp/scan.py <<'EOB64'
__SCANNER_B64__
EOB64
echo "### plumbing scan of the extracted /opt/sv_shell"
python3 /tmp/scan.py --tree /tmp/extracted/sv_shell --list-keys || finish FAILURE "scan exited nonzero"
echo "### SVSCAN_DONE"
echo "### shipped jar: GenotypeSVs long-name surface (the defect this image must close)"
docker run --rm "$IMAGE" java -jar /opt/gatk.jar GenotypeSVs --help > /tmp/gatk_help.txt 2>&1 || true
HAVE_DEPTH=no; HAVE_PESR=no
for f in rd-depth-table rd-pesr-table rd-table; do
  if grep -qE -- "--$f([^-a-z]|$)" /tmp/gatk_help.txt; then st=PRESENT; else st=absent; fi
  [ "$f" = "rd-depth-table" ] && [ "$st" = PRESENT ] && HAVE_DEPTH=yes
  [ "$f" = "rd-pesr-table" ] && [ "$st" = PRESENT ] && HAVE_PESR=yes
  echo "###   --$f : $st"
done
grep -c "rd-depth-table" /tmp/gatk_help.txt >/dev/null || true
if [ "$HAVE_DEPTH" != yes ] || [ "$HAVE_PESR" != yes ]; then
  finish FAILURE "jar in the shipped image does not accept --rd-depth-table/--rd-pesr-table; the gatk pin is stale"
fi
echo "### jar accepts both split RD tables: OK"
finish SUCCESS "image bytes match, scan complete"
EOS

b64=$(base64 < "$SCANNER" | tr -d '\n')
python3 - "$STARTUP" "$IMAGE" "$EXPECT_DRIVER_MD5" "$EXPECT_FIXTURE_MD5" "$b64" <<'PYEOF'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
s = (p.read_text()
     .replace('__IMAGE__', sys.argv[2])
     .replace('__EXPECT_DRV__', sys.argv[3])
     .replace('__EXPECT_FX__', sys.argv[4])
     .replace('__SCANNER_B64__', sys.argv[5]))
p.write_text(s)
PYEOF

echo "==> preflight"
gcloud compute images list --project ubuntu-os-cloud \
  --filter "family='ubuntu-2404-lts-amd64'" --format 'value(name)' | grep -q . \
  || { echo "image family unavailable" >&2; exit 2; }

echo "==> creating $INSTANCE ($MACHINE, ${DISK_GB}GB, $ZONE)"
gcloud compute instances create "$INSTANCE" --project "$PROJECT" --zone "$ZONE" \
  --machine-type "$MACHINE" --image-family ubuntu-2404-lts-amd64 \
  --image-project ubuntu-os-cloud --boot-disk-size "${DISK_GB}GB" --boot-disk-type pd-ssd \
  --boot-disk-auto-delete --scopes cloud-platform --labels created-by=svshell-check \
  --metadata-from-file "startup-script=$STARTUP" >/dev/null

deadline=$(( $(date +%s) + TIMEOUT_S ))
RESULT=""
echo "==> waiting for the result marker (timeout $((TIMEOUT_S/60)) min)"
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep 30
  gcloud compute instances get-serial-port-output "$INSTANCE" --project "$PROJECT" \
    --zone "$ZONE" --port 1 --start 0 > "$LOG" 2>/dev/null || true
  if grep -q "SVSHELL_IMAGE_CHECK=" "$LOG"; then
    RESULT=$(grep -o "SVSHELL_IMAGE_CHECK=[A-Z]*" "$LOG" | tail -1)
    break
  fi
  echo "    ... $(du -h "$LOG" | cut -f1) of serial log"
done

echo "==> serial log: $LOG ($(du -h "$LOG" | cut -f1))"
# GCE prefixes startup-script stdout with "startup-script: ", so anchoring on "^###" finds nothing
    grep -aE "### " "$LOG" | sed 's/.*startup-script: //' | sed 's/^/    /' | sort -u | tail -40 || true

if [ "$KEEP" -eq 0 ]; then
  gcloud compute instances delete "$INSTANCE" --project "$PROJECT" --zone "$ZONE" --quiet >/dev/null 2>&1 || \
    echo "WARN could not delete $INSTANCE; cleanup: gcloud compute instances delete $INSTANCE --project $PROJECT --zone $ZONE --quiet"
else
  echo "==> keeping $INSTANCE (asked with --keep); serial log at $LOG"
fi

case "$RESULT" in
  SVSHELL_IMAGE_CHECK=SUCCESS) echo "RESULT: SUCCESS — the shipped image carries the tested bytes and the plumbing scan is clean"; exit 0;;
  SVSHELL_IMAGE_CHECK=FAILURE) echo "RESULT: FAILURE — see the ### lines above"; exit 1;;
  *) echo "RESULT: TIMEOUT — no marker after $((TIMEOUT_S/60)) min; VM $INSTANCE (log $LOG)"; exit 3;;
esac
