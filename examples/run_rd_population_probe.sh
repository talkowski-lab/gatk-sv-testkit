#!/bin/bash
# Local probe: is the RD copy-state distribution defect a DEPTH-VALUE problem or a
# TRAINING-POPULATION problem?
#
# v1.1.1 fits the RD copy-state mean/sd with generate_cutoff.R on RdTest.R's `.median_geno`
# over the 56 curated reviewed CNV loci in src/RdTest/train_hg38_reviewed_final.bed
# (TrainRDGenotyping.wdl MakeTrainingBed), i.e. ~56 x 156 = 8,736 observations where the
# copy state is a KNOWN truth. The new Java trainer fits the same mean/sd over every condensed
# interval in the batch (1,462,095 here), where "copy state" is assigned by the same fixed seed
# cutoffs (0.25/0.75/1.25/1.75) that define the bins -> it fits a distribution over its own binning.
#
# Arms (identical args to run_train_full.sh / GenotypeBatch.wdl except the two swapped inputs):
#   A rd_curated56  --training-intervals = v1.1.1's 56 curated loci
#   B rd_rand8736   --training-intervals = 8,736 RANDOM condensed intervals (control for SIZE)
# Both use the chr20 mini VCF for -V so the PE/SR phases are cheap; trainCopyNumberSites never
# reads the VCF (TrainSVGenotyping.java trainCopyNumberSites uses trainingLocs + loader only), so
# the RD tables are VCF-independent.
#
# Pass condition (see docs/archive/CHECKPOINT.md, 'What good looks like'):
#   A reproduces baseline shape (state-2 mean ~1.0080 sd ~0.0615, state-0 sd ~0.0063,
#     hom-del cutoff ~0.1069)  -> depth value is equivalent, the defect is the population choice.
#   A keeps state-0 sd >= 5x baseline                                       -> Java's depth VALUE differs.
#   B looks like the full run (state-0 sd ~0.066)                           -> identity, not size.
set -euo pipefail
# Resolve this script's own directory BEFORE anything changes the cwd. Re-resolving a
# possibly-relative $0 after a `cd` looks up `examples/..` from the new cwd and fails, so a
# relative invocation (docs/local-replay.md shows exactly that) could never start.
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="$(cd "$SELF/.." && pwd -P)"
. "$ROOT/kit/config.sh"
# These are recipes, not CLIs: without this guard `--help` would fall through and start a run.
case "${1:-}" in
  -h|--help|help) awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "$0"; exit 0;;
esac
cd "$SELF"
# The jar under test: a locally built GATK (docs/local-replay.md). Override with
# JAR=/path/to/gatk.jar; discovered under GSVTK_GATK_CHECKOUT/build/libs by default.
_gatk_ck="${GSVTK_GATK_CHECKOUT:-.}"
JAR="${JAR:-$(ls -t "$_gatk_ck"/build/libs/gatk-package-*-local.jar 2>/dev/null | head -1)}"
JAVA="${JAVA:-java}"

STAGE="$(gsvtk_work staging)"
XMX=${XMX:-6g}
MINIVCF=$STAGE/train.chr20.vcf.gz
CUT=$STAGE/all_samples.cutoffs
# Depth exclusion list. Production/baseline feed ${workspace.bin_exclude} =
# bin_exclude.hg38.gatkcov.bed.gz (1,512,310 100-bp poor bins, 151 Mb); the earlier replays
# used a hand-made 48-interval stub (depth_blacklist.sorted.bed.gz), so replay RD numbers are
# under-excluded vs both baseline and production. Default here is the REAL asset.
DEPTH_EXCL=${DEPTH_EXCL:-$STAGE/bin_exclude.hg38.gatkcov.bed.gz}
# Extra GATK args, e.g. EXTRA_ARGS="--num-bins 100000" to test the train/apply bin-count mismatch
# (TrainSVGenotyping.numBins defaults to 10, GenotypeSVs.numBins defaults to 100000, WDL passes neither).
EXTRA_ARGS=${EXTRA_ARGS:-}
SUFFIX=${SUFFIX:-real}

SRQ=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} $c["metric"]=="SR_sum_log_pval"{print $c["cutoff"]*10}' $CUT | head -1)
PEQ=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} $c["metric"]=="PE_log_pval"{print $c["cutoff"]*10}' $CUT | head -1)
PESR_SEP=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} toupper($c["algtype"])=="PESR" && ($c["min_svsize"]+0)==1000 && toupper($c["metric"])=="RD_MEDIAN_SEPARATION"{print $c["cutoff"]}' $CUT | sort -nr | head -1)
DEPTH_SEP=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} toupper($c["algtype"])=="DEPTH" && toupper($c["metric"])=="RD_MEDIAN_SEPARATION"{print $c["cutoff"]}' $CUT | sort -nr | head -1)
echo "SRQ=$SRQ PEQ=$PEQ PESR_SEP=$PESR_SEP DEPTH_SEP=$DEPTH_SEP"

run_arm () {
  local name=$1 intervals=$2
  local out
  out="$(gsvtk_work "runs/${name}_${SUFFIX}")"
  mkdir -p "$out"
  echo "=== arm $name  ($(wc -l < "$intervals") intervals) -> $out"
  "$JAVA" -Xmx${XMX} -jar "$JAR" TrainSVGenotyping \
    -XL chrX -XL chrY \
    -V "$MINIVCF" \
    --training-intervals "$intervals" \
    -O "$out/probe.vcf.gz" \
    --median-coverage "$STAGE/all_samples_medianCov.transposed.bed" \
    --rd-file "$STAGE/all_samples.RD.txt.gz" \
    --split-reads-file "$STAGE/all_samples.sr.txt.gz" \
    --discordant-pairs-file "$STAGE/all_samples.pe.txt.gz" \
    --sequence-dictionary "$STAGE/Homo_sapiens_assembly38.dict" \
    --ploidy-table "$STAGE/all_batches.ploidy.tsv" \
    --depth-exclusion-intervals "$DEPTH_EXCL" \
    --pesr-exclusion-intervals "$STAGE/PESR.encode.peri_all.repeats.delly.hg38.blacklist.sorted.bed.gz" \
    --pe-quality "$PEQ" \
    --sr-quality "$SRQ" \
    --rd-depth-min-separation "$DEPTH_SEP" \
    --rd-pesr-min-separation "$PESR_SEP" \
    --output-dir "$out" \
    --output-name all_samples \
    ${EXTRA_ARGS} \
    > "$out/train.log" 2>&1
  echo "--- $name rd_depth_geno_params.tsv"
  column -t "$out"/all_samples.rd_depth_geno_params.tsv
}

run_arm rd_curated56 "$STAGE/train.curated56.intervals.bed"
run_arm rd_rand8736  "$STAGE/train.rand8736.intervals.bed"

echo "=== reference: v1.1.1 baseline (staging/all_samples.depth.depth_sepcutoff.txt)"
column -t "$STAGE/all_samples.depth.depth_sepcutoff.txt"
echo "=== reference: new pipeline, full condensed intervals (runs/train_full_stream)"
column -t runs/train_full_stream/all_samples.rd_depth_geno_params.tsv
