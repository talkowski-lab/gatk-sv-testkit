#!/bin/bash
# Local replay: run the REAL new Java genotyper trainer on a frozen v1.1.1 cohort slice.
#
# Command line mirrors wdl/GenotypeBatch.wdl TrainSVGenotyping (lines ~321-340),
# with inputs taken from the frozen baseline (work/manifests/baseline_run.json) and the
# cutoff-derived params computed with the same awk the WDL uses.
#
# Target to beat (v1.1.1 whole-batch SR metrics, gs://...call-TrainSRGenotyping/.../all_samples.sr_metric_file.txt):
#   sr_count 10   median_hom 78   sd_het 26.8276   rare 0/2 common 2/156
#   rare_single .1 rare_both .6 common_single .9 common_both .9
# (our region run is chr20-restricted, so compare qualitatively + against the python emulation)
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
OUT="$(gsvtk_work runs/train_chr20)"
REGION=${REGION:-chr20:1000000-25000000}
mkdir -p "$OUT"

# ---- training VCF = batch-level PESR VCF (the branch's `training_vcf`), region-restricted ----
if [[ ! -s $STAGE/train.chr20.vcf.gz ]]; then
  bcftools view -r "$REGION" -Oz -o "$STAGE/train.chr20.vcf.gz" "$STAGE/all_samples.filtered_pesr_merged.vcf.gz"
  bcftools index -f -t "$STAGE/train.chr20.vcf.gz"
fi
# ---- training intervals, region-restricted ----
if [[ ! -s $STAGE/train.chr20.intervals.bed ]]; then
  # file is CONTIG/START/END/GC_CONTENT with @SQ header lines and a column-name row; strip both
  awk -F'\t' -v s=1000000 -v e=25000000 '!/^@/ && $1=="chr20" && $2 < e && $3 > s {print $1"\t"$2"\t"$3"\t"$4}' \
      "$STAGE/condensed_intervals.annotated.tsv" > "$STAGE/train.chr20.intervals.bed"
fi

# ---- params from the frozen RF cutoffs, same awk as the WDL ----
CUT=$STAGE/all_samples.cutoffs
# v1.1 cutoffs carry SR_sum_log_pval / PE_log_pval in -log10 p units; the new metric is GATK QUAL
# (-10 log10 p), so x10 reproduces the new-side SRQ/PEQ for the same discrimination.
SRQ=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} $c["metric"]=="SR_sum_log_pval"{print $c["cutoff"]*10}' $CUT | head -1)
PEQ=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} $c["metric"]=="PE_log_pval"{print $c["cutoff"]*10}' $CUT | head -1)
PESR_SEP=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} toupper($c["algtype"])=="PESR" && ($c["min_svsize"]+0)==1000 && toupper($c["metric"])=="RD_MEDIAN_SEPARATION"{print $c["cutoff"]}' $CUT | sort -nr | head -1)
DEPTH_SEP=$(awk -F'\t' 'NR==1{for(i=1;i<=NF;i++) c[$i]=i;next} toupper($c["algtype"])=="DEPTH" && toupper($c["metric"])=="RD_MEDIAN_SEPARATION"{print $c["cutoff"]}' $CUT | sort -nr | head -1)
echo "SRQ=$SRQ PEQ=$PEQ PESR_SEP=$PESR_SEP DEPTH_SEP=$DEPTH_SEP"

set -x
"$JAVA" -Xmx14g -jar "$JAR" TrainSVGenotyping \
  -XL chrX -XL chrY \
  -V "$STAGE/train.chr20.vcf.gz" \
  --training-intervals "$STAGE/train.chr20.intervals.bed" \
  -O "$OUT/train.chr20.genotyped.vcf.gz" \
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
  --output-name all_samples.chr20 \
  > "$OUT/train.log" 2>&1
set +x
echo "=== tables"
for f in "$OUT"/*.tsv "$OUT"/*.txt; do echo "--- $f"; cat "$f"; done
