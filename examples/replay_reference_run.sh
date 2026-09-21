#!/usr/bin/env bash
# Rebuild a launchable SVShell input JSON from somebody else's SUCCESSFUL run, then
# compare your arm's outputs against theirs.
#
# Why this is the cheap way to test the single-sample path: `src/sv_shell` has no CI and
# the two WDLs that drive it are not exercised anywhere, so a change is normally validated
# by launching a full pipeline on real data and hoping. A completed run already contains
# everything needed: its Cromwell `script` dump holds the exact inputs.json it ran, its
# outputs hold what those inputs produced. Replay that, change one thing, compare.
#
# Everything here is read-only against the reference workspace. Creating configs or
# submitting in YOUR workspace is deliberately not scripted: do it from the two numbers
# this prints, once you have looked at them.
#
# Required environment (the run you are replaying -- there is no sensible default):
#   REF_NS, REF_WS        namespace/name of the workspace that ran it
#   REF_SUB, REF_WF       submission id / workflow id of the successful run
#   CAPTURED              the local copy of that workflow's Cromwell `script` file
#                         gsutil cp <bucket>/submissions/<sub>/SVShell/<wf>/call-*/script "$CAPTURED"
#   IMAGES                JSON map of docker input -> image for the arm you are building
#                         (see replay/images.example.json)
# Optional:
#   GSVTK_GATK_SV_CHECKOUT  the gatk-sv clone whose WDL defines the arm (required by inputs)
#
#   examples/replay_reference_run.sh inputs     # rebuild the input JSON (offline once captured)
#   examples/replay_reference_run.sh counts A.vcf.gz B.vcf.gz   # record + genotype tallies
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd -P)"
. "$ROOT/kit/config.sh"

OUT="$(gsvtk_work replay)"
WDL="$(gsvtk_work wdl)"
cmd="${1:-help}"; shift || true

need() { [ -n "${!1:-}" ] || { echo "$1 is not set -- it names the run you are replaying." >&2; exit 4; }; }

inputs() {
    need CAPTURED; need IMAGES
    [ -f "$CAPTURED" ] || { echo "no captured script at $CAPTURED. Pull it with the gsutil cp in the header." >&2; exit 4; }
    # The WDL of the ARM, not of the reference run: build_inputs diffing the two is how a
    # renamed input shows up before submission rather than as a null argument three stages later.
    local sha_ref="${GSVTK_BRANCH:-HEAD}"
    "$GSVTK_PYTHON" "$ROOT/scripts/fetch_wdl.py" --ref "$sha_ref" --dest "$WDL" >/dev/null
    "$GSVTK_PYTHON" "$ROOT/replay/build_inputs.py" \
        --script "$CAPTURED" \
        --wdl "$WDL/SVShell.wdl" \
        --images "$IMAGES" \
        --out-dir "$OUT" \
        --arm "${ARM:-branch}" \
        "$@"
    echo "wrote $OUT/*.inputs.json -- inspect before launching anything."
}

counts() {  # record counts + genotype tallies, side by side: no profiler, no bucketing
    local a="${1:?first VCF}" b="${2:?second VCF}"
    "$GSVTK_PYTHON" - "$a" "$b" <<'PY'
import sys, pysam
for p in sys.argv[1:]:
    v = pysam.VariantFile(p)
    n = 0
    gt = {}
    for rec in v:
        n += 1
        for s in v.header.samples:
            g = rec.samples[s].get("GT")
            key = "".join("." if x is None else str(x) for x in (g or ()))
            gt[key] = gt.get(key, 0) + 1
    print(f"  {p}\n    records={n}  genotype_counts={dict(sorted(gt.items()))}")
PY
}

case "$cmd" in
  inputs) inputs "$@";;
  counts) counts "$@";;
  -h|--help|help) sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//';;
  *) echo "unknown command: $cmd (try: inputs, counts)" >&2; exit 2;;
esac
