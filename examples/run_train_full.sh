#!/bin/bash
# local replay replay, WHOLE GENOME: real new Java trainer on the frozen v1.1.1 cohort, so the
# numbers are directly comparable to the baseline's whole-batch metric files.
#
# Baseline (v1.1.1, whole batch, 156 samples):
#   SR: sr_count 10  median_hom 78   sd_het 26.8276  rare 0/2  common 2/156
#       rare_single .1 rare_both .6 common_single .9 common_both .9
#   PE: see all_samples.pe_metric_file.txt (fetch if not staged)
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
OUT="${OUT:-$(gsvtk_work runs/train_full)}"
XMX=${XMX:-14g}
INTERVALS=${INTERVALS:-$STAGE/train.full.intervals.bed}
mkdir -p "$OUT"
if [[ ! -s $STAGE/all_samples.filtered_pesr_merged.vcf.gz.tbi ]]; then bcftools index -f -t "$STAGE/all_samples.filtered_pesr_merged.vcf.gz"; fi

if [[ ! -s $STAGE/train.full.intervals.bed ]]; then
  awk -F'\t' '!/^@/ && $1!="CONTIG" {print $1"\t"$2"\t"$3"\t"$4}' \
      "$STAGE/condensed_intervals.annotated.tsv" > "$STAGE/train.full.intervals.bed"
fi

CUT=$STAGE/all_samples.cutoffs
SRQ=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} $c["metric"]=="SR_sum_log_pval"{print $c["cutoff"]*10}' $CUT | head -1)
PEQ=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} $c["metric"]=="PE_log_pval"{print $c["cutoff"]*10}' $CUT | head -1)
PESR_SEP=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} toupper($c["algtype"])=="PESR" && ($c["min_svsize"]+0)==1000 && toupper($c["metric"])=="RD_MEDIAN_SEPARATION"{print $c["cutoff"]}' $CUT | sort -nr | head -1)
DEPTH_SEP=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} toupper($c["algtype"])=="DEPTH" && toupper($c["metric"])=="RD_MEDIAN_SEPARATION"{print $c["cutoff"]}' $CUT | sort -nr | head -1)
echo "SRQ=$SRQ PEQ=$PEQ PESR_SEP=$PESR_SEP DEPTH_SEP=$DEPTH_SEP"

"$JAVA" -Xmx${XMX} -jar "$JAR" TrainSVGenotyping \
  -XL chrX -XL chrY \
  -V "$STAGE/all_samples.filtered_pesr_merged.vcf.gz" \
  --training-intervals "$INTERVALS" \
  -O "$OUT/train.full.genotyped.vcf.gz" \
  --median-coverage "$STAGE/all_samples_medianCov.transposed.bed" \
  --rd-file "$STAGE/all_samples.RD.txt.gz" \
  --split-reads-file "$STAGE/all_samples.sr.txt.gz" \
  --discordant-pairs-file "$STAGE/all_samples.pe.txt.gz" \
  --sequence-dictionary "$STAGE/Homo_sapiens_assembly38.dict" \
  --ploidy-table "$STAGE/all_batches.ploidy.tsv" \
  --depth-exclusion-intervals "$STAGE/depth_blacklist.sorted.bed.gz" \
  --pesr-exclusion-intervals "$STAGE/PESR.encode.peri_all.repeats.delly.hg38.blacklist.sorted.bed.gz" \
  --pe-quality "$PEQ" \
  --sr-quality "$SRQ" \
  --rd-depth-min-separation "$DEPTH_SEP" \
  --rd-pesr-min-separation "$PESR_SEP" \
  --output-dir "$OUT" \
  --output-name all_samples \
  > "$OUT/train.log" 2>&1

echo "=== tables"
for f in "$OUT"/*.tsv; do echo "--- $f"; column -t "$f"; done
grep -A12 "## SR_TRAINING_PASSES" "$OUT"/all_samples.sr_cutoff_diagnostics.txt
