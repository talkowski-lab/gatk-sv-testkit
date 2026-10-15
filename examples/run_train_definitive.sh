#!/bin/bash
# Local definitive run: the new Java trainer on the frozen v1.1.1 cohort with the THREE
# input-configuration differences from v1.1.1 removed. Everything else is byte-identical to
# run_train_full.sh (same staging inputs, same SRQ/PEQ/separations, -XL chrX -XL chrY).
#
#   1. RD TRAINING POPULATION  --training-intervals = src/RdTest/train_hg38_reviewed_final.bed
#      (56 curated reviewed CNV loci) instead of the batch's 1,462,095 condensed intervals.
#      v1.1.1 fits the RD copy-state mean/sd on the curated loci (TrainRDGenotyping.wdl
#      MakeTrainingBed); fitting on condensed intervals means fitting a distribution over the
#      same fixed seed cutoffs (0.25/0.75/1.25/1.75) that assign the copy states -> circular.
#   2. RD BIN COUNT            --num-bins 100000. TrainSVGenotyping.numBins defaults to 10
#      (TrainSVGenotyping.java:238) while GenotypeSVs.numBins defaults to 100000
#      (GenotypeSVs.java:239) and wdl/GenotypeBatch.wdl passes neither, so the new pipeline
#      fits cutoffs on 10-bin compressed depth values and applies 100000-bin ones. v1.1.1 used
#      n_RD_genotype_bins=100000 at both train and apply.
#   3. DEPTH EXCLUSIONS        --depth-exclusion-intervals = bin_exclude.hg38.gatkcov.bed.gz
#      (1,512,310 x 100-bp poor bins) = ${workspace.bin_exclude}, the asset both the baseline
#      and production actually feed. Earlier replays used a hand-made 48-interval stub.
#
# Side effects worth knowing: RD training reads 56 intervals instead of 1.46 M, so the task
# finishes in ~5 min instead of ~46 and the RD-train OOM (defect 1) cannot trigger at all.
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
OUT="${OUT:-$(gsvtk_work runs/train_definitive)}"
XMX=${XMX:-8g}
INTERVALS=${INTERVALS:-$STAGE/train.curated56.intervals.bed}
DEPTH_EXCL=${DEPTH_EXCL:-$STAGE/bin_exclude.hg38.gatkcov.bed.gz}
NUM_BINS=${NUM_BINS:-100000}
TRAIN_VCF=${TRAIN_VCF:-$STAGE/all_samples.filtered_pesr_merged.vcf.gz}
EXTRA_ARGS=${EXTRA_ARGS:-}
mkdir -p "$OUT"
if [[ ! -s $STAGE/all_samples.filtered_pesr_merged.vcf.gz.tbi ]]; then bcftools index -f -t "$STAGE/all_samples.filtered_pesr_merged.vcf.gz"; fi
[[ -s "$INTERVALS" ]] || { echo "missing $INTERVALS"; exit 1; }
[[ -s "$DEPTH_EXCL" ]] || { echo "missing $DEPTH_EXCL (see header note 3)"; exit 1; }

CUT=$STAGE/all_samples.cutoffs
SRQ=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} $c["metric"]=="SR_sum_log_pval"{print $c["cutoff"]*10}' $CUT | head -1)
PEQ=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} $c["metric"]=="PE_log_pval"{print $c["cutoff"]*10}' $CUT | head -1)
PESR_SEP=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} toupper($c["algtype"])=="PESR" && ($c["min_svsize"]+0)==1000 && toupper($c["metric"])=="RD_MEDIAN_SEPARATION"{print $c["cutoff"]}' $CUT | sort -nr | head -1)
DEPTH_SEP=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} toupper($c["algtype"])=="DEPTH" && toupper($c["metric"])=="RD_MEDIAN_SEPARATION"{print $c["cutoff"]}' $CUT | sort -nr | head -1)
echo "SRQ=$SRQ PEQ=$PEQ PESR_SEP=$PESR_SEP DEPTH_SEP=$DEPTH_SEP num_bins=$NUM_BINS"
echo "intervals=$INTERVALS ($(wc -l < "$INTERVALS") rows)  depth_excl=$DEPTH_EXCL"

"$JAVA" -Xmx${XMX} -jar "$JAR" TrainSVGenotyping \
  -XL chrX -XL chrY \
  -V "$TRAIN_VCF" \
  --training-intervals "$INTERVALS" \
  -O "$OUT/train.genotyped.vcf.gz" \
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
  --num-bins "$NUM_BINS" \
  --output-dir "$OUT" \
  --output-name all_samples \
  ${EXTRA_ARGS} \
  > "$OUT/train.log" 2>&1

echo "=== tables ($OUT)"
for f in "$OUT"/*.tsv; do echo "--- $(basename "$f")"; column -t "$f"; done
grep -A12 "## SR_TRAINING_PASSES" "$OUT"/all_samples.sr_cutoff_diagnostics.txt || true
