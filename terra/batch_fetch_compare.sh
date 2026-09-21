#!/usr/bin/env bash
# =============================================================================
# terra/batch_fetch_compare.sh
#
# Terra head-to-head: FETCH + COMPARE stage. Ready to run the moment the
# Terra chain (06-GenerateBatchMetrics -> ... -> 10-GenotypeBatch) writes its outputs.
#
#   fetch    read the chain outputs off the workspace entity, gsutil cp them into
#            $OUTPUTS, write $OUTPUTS/MANIFEST.json
#   profile  gatk-sv-profile in PAIRED mode: new-side VCFs vs the frozen baseline VCFs
#            -> $RESULTS/compare_pesr/ and $RESULTS/compare_depth/
#   table    compare/compare_batch_tables.py (written in parallel; tolerated if absent)
#   all      fetch -> profile -> table
#
# -----------------------------------------------------------------------------
# ATTRIBUTE CONVENTION (your sandbox workspace = GSVTK_TERRA_NAMESPACE/
# GSVTK_TERRA_WORKSPACE, entity sample_set/GSVTK_BATCH)
# -----------------------------------------------------------------------------
# That workspace is a clone of the baseline workspace, so ONE entity holds BOTH sides
# of the head-to-head. The suffix convention comes from
# terra/batch_configs.py:
#
#   *${FZ}  frozen baseline *upstream inputs* copied into the sandbox bucket
#          (terra/batch_freeze.py; see $STAGING/frozen_baseline_attrs.tsv)
#   *${NW}   OUTPUTS OF THE NEW SIDE (GSVTK_NEW_SUFFIX), from the 10-GenotypeBatch
#          output bindings, e.g.
#          "GenotypeBatch.genotyped_pesr_vcf": "this.genotyped_pesr_vcf${NW}"
#   (none) the inherited baseline outputs, e.g. this.genotyped_pesr_vcf
#
# The chain only ever writes *${NW}, so nothing is clobbered and both sides stay
# addressable. This script reads ONLY *${NW} attributes for the new side and never
# mutates Terra: it uses terra/terra.py read calls only, and every mutating helper in
# that module requires confirm=True, which is never passed here.
#
# Attributes fetched (each suffixed with $NW at read time):
#   genotyped_pesr_vcf, genotyped_depth_vcf,
#   genotyping_rd_depth_table, genotyping_rd_pesr_table, genotyping_pe_table,
#   genotyping_sr_table, genotyping_sr_cutoff_diagnostics
#   (+ optional genotyped_{pesr,depth}_vcf_index siblings when exported; otherwise the
#    profile stage indexes locally with `bcftools index -t`)
#
# -----------------------------------------------------------------------------
# WHY THE BASELINE SIDE IS READ FROM work/staging/ (not from Terra)
# -----------------------------------------------------------------------------
# The v1.1.1 baseline callsets are frozen locally at
#   work/staging/all_samples.genotyped_pesr.vcf.gz   (80,888 records)
#   work/staging/all_samples.genotyped_depth.vcf.gz  ( 5,386 records)
# (fetched read-only by terra/fetch_baseline.py; their gs:// URLs are recorded in
# work/manifests/baseline_run.json). Two reasons:
#   1. gatk-sv-profile preprocess writes intermediate VCFs and needs LOCAL, INDEXED
#      files — a gs:// path is not usable there.
#   2. the baseline bucket belongs to the frozen baseline workspace and is NOT writable
#      from the sandbox, so all comparison scratch has to be local anyway.
# Using the frozen local baseline (rather than re-fetching from Terra each time) also
# means every comparison you run shares one baseline: same file, same labels, same
# module set, so two runs are actually comparable.
#
# -----------------------------------------------------------------------------
# WHICH gatk-sv-profile MODULES TO RUN
# -----------------------------------------------------------------------------
# It was `gatk-sv-profile run` (preprocess + analyze end to end) in paired mode with the
# DEFAULT module list: the module dirs on disk are exactly
#   preprocess, site_overlap, genotype_concordance, genotype_exact_match, genotype_dist,
#   genotype_quality, counts_per_genome, allele_freq, size_signatures, upset, aggregate,
#   binned_counts, overall_counts
# i.e. ALL_MODULES with `family_analysis` auto-skipped (no --ped), so no --modules list is
# passed here either. Labels `--label-a baseline --label-b new` with A = v1.1.1
# (verified on the previous run: preprocess/annotated_a.sv.vcf.gz = 80,888 records =
# baseline, annotated_b = 96,915 = new) so the *_baseline / *_new_java suffixes land
# on the same sides. preprocess needs a `gatk` executable + --reference-dict +
# --contig-list; --java-options MUST be written `--java-options=-Xmx8g` (a bare `-Xmx8g`
# is parsed as its own flag), and the jar needs Java 17 (bare `java` here is 11).
#
# -----------------------------------------------------------------------------
# ENV OVERRIDES (all optional)
# -----------------------------------------------------------------------------
#   NS WS ETYPE ENTITY RESULTS                       workspace/entity/output root
#   TERRA_PY (default .venv-terra/bin/python) GSUTIL     fetch tooling
#   PROFILE_BIN  gatk-sv-profile on PATH (or set it to a venv-installed one;
#                see docs/comparators.md for the install)
#   GATK_JAR     a built GATK jar (missing => the preflight fails and names it)
#   GATK_BIN     launcher handed to gatk-sv-profile; default $RESULTS/bin/gatk,
#                generated from GATK_JAR (must strip --java-options like the real gatk)
#   JAVA         default `java` on PATH (GATK needs JDK 17+)
#   XMX NUM_WORKERS LABEL_A LABEL_B REF_DICT CONTIG_LIST
#   BASELINE_PESR_VCF BASELINE_DEPTH_VCF COMPARE_TABLES TABLE_PY
#
# SAFETY: Terra read-only; gsutil is only ever `stat` / `cp` (download) — no writes, no
# rm. Downloads are skipped when the local size already matches the object. An existing
# non-empty profile directory is REFUSED unless --force.
# =============================================================================
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/.." && pwd)
. "$ROOT/kit/config.sh"

# ------------------------------- configuration ------------------------------
# Workspace and batch come from the profile (testkit.env). The attribute suffixes
# MUST match what terra/batch_configs.py wrote and batch_freeze.py froze, so they
# are read from the same two keys rather than being repeated here.
NS=${NS:-$(gsvtk_default TERRA_NAMESPACE "")}
WS=${WS:-$(gsvtk_default TERRA_WORKSPACE "")}
ETYPE=${ETYPE:-sample_set}
ENTITY=${ENTITY:-$(gsvtk_default BATCH all_samples)}
FZ="_$(gsvtk_default FROZEN_SUFFIX frz)"
NW="_$(gsvtk_default NEW_SUFFIX new)"

RESULTS=${RESULTS:-$(gsvtk_work)}
OUTPUTS=$RESULTS/outputs
ATTRS_TSV=$OUTPUTS/attrs_new.tsv
MANIFEST=$OUTPUTS/MANIFEST.json
MANIFEST_ROWS=$OUTPUTS/.manifest_rows.tsv
TABLES_DIR=$RESULTS/tables

STAGING=$RESULTS/staging
BASELINE_PESR_VCF=${BASELINE_PESR_VCF:-$STAGING/${ENTITY}.genotyped_pesr.vcf.gz}
BASELINE_DEPTH_VCF=${BASELINE_DEPTH_VCF:-$STAGING/${ENTITY}.genotyped_depth.vcf.gz}
REF_DICT=${REF_DICT:-$STAGING/Homo_sapiens_assembly38.dict}
CONTIG_LIST=${CONTIG_LIST:-$STAGING/primary_contigs.list}

TERRA_PY=${TERRA_PY:-${GSVTK_PYTHON:-python3}}
GSUTIL=${GSUTIL:-gsutil}
# gatk-sv-profile and a GATK jar are both optional extras: install the profiler on
# PATH and point GATK_JAR at a built jar (see docs/comparators.md). Missing ones are
# reported by profile_preflight, they do not fail the fetch stage.
PROFILE_BIN=${PROFILE_BIN:-gatk-sv-profile}
GATK_JAR=${GATK_JAR:-}
JAVA=${JAVA:-java}
GATK_BIN=${GATK_BIN:-$RESULTS/bin/gatk}
XMX=${XMX:-8g}
NUM_WORKERS=${NUM_WORKERS:-4}
LABEL_A=${LABEL_A:-baseline}
LABEL_B=${LABEL_B:-$(gsvtk_default BRANCH new)}
COMPARE_TABLES=${COMPARE_TABLES:-$ROOT/compare/compare_batch_tables.py}
TABLE_PY=${TABLE_PY:-${GSVTK_PYTHON:-python3}}

# chain outputs to fetch; the $NW suffix is appended at read time.
# L1/L2 (the genotyping layer) are REQUIRED; the L3 filter/metrics surface and the QC
# side-files are OPTIONAL so a partial chain still yields what exists.
REQUIRED_ATTRS=(genotyped_pesr_vcf genotyped_depth_vcf \
                genotyping_rd_depth_table genotyping_rd_pesr_table \
                genotyping_pe_table genotyping_sr_table \
                genotyping_sr_cutoff_diagnostics)
OPTIONAL_ATTRS=(genotyped_pesr_vcf_index genotyped_depth_vcf_index \
                cutoffs scores metrics metrics_file_batchmetrics \
                ploidy_table filtered_batch_samples_file outlier_samples_excluded_file \
                regeno_coverage_medians)

DRY_RUN=0
FORCE=0
SUB=""
EXTRA=()

usage() {
  cat <<'USAGE'
usage: batch_fetch_compare.sh <fetch|profile|table|all> [--dry-run] [--force] [-- <extra args for `table`>]

  fetch     read *${NW} attrs of the batch sample_set (read-only Terra) and gsutil cp
            the chain outputs into $OUTPUTS/ + write $MANIFEST
  profile   gatk-sv-profile paired run: baseline ($STAGING) vs new (*${NW}),
            into $RESULTS/compare_pesr/ and $RESULTS/compare_depth/
  table     run compare/compare_batch_tables.py (prints "not present yet" if missing)
  all       fetch, profile, table

  --dry-run print every command without executing it
  --force   allow writing into a non-empty profile output directory (default: refuse)
USAGE
}

# ------------------------------- small helpers ------------------------------
# %b so that \n inside a message is a real newline: bash's echo prints them literally, which
# turned two-line "here is how to fix it" guidance into one unreadable run-on line.
die() { printf 'ERROR: %b\n' "$*" >&2; exit 1; }

# print a command; execute it unless --dry-run
run() {
  echo "  + $*"
  if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
  "$@"
}

bytes_of() { wc -c < "$1" | tr -d ' '; }

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

# attr-name -> gs:// url from the fetched attribute table ("" when unknown/absent)
attr_url() {
  [ -s "$ATTRS_TSV" ] || { echo ""; return 0; }
  awk -F'\t' -v k="$1" '$1 == k { print $2; exit }' "$ATTRS_TSV"
}

# `gsutil stat` (read-only) -> Content-Length, empty in dry-run / on failure
remote_size() {
  local url=$1 out
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  + $GSUTIL stat $url" >&2
    return 0
  fi
  out=$("$GSUTIL" stat "$url" 2>/dev/null || true)
  printf '%s\n' "$out" | awk -F': ' '/^Content-Length/ { gsub(/ /, "", $2); print $2; exit }'
}

# ------------------------------ gatk launcher -------------------------------
# gatk-sv-profile preprocess shells out to a `gatk` executable and passes
# `--java-options <opts>`; the real GATK launcher pulls that pair into the JVM, so the
# generated wrapper must too (`java -jar` alone would leave an unknown argument).
write_gatk_wrapper() {
  local dest=$1
  if [ -x "$dest" ]; then
    echo "  [gatk] launcher already present: $dest"
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  + write $dest (gatk launcher: $JAVA -jar $GATK_JAR, strips --java-options)"
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  cat > "$dest" <<EOF
#!/usr/bin/env bash
# Generated by terra/batch_fetch_compare.sh — GATK launcher for gatk-sv-profile.
JAVA_BIN="\${GATK_WRAPPER_JAVA:-$JAVA}"
JAR="\${GATK_WRAPPER_JAR:-$GATK_JAR}"
[ -x "\$JAVA_BIN" ] || { echo "gatk wrapper: no java at \$JAVA_BIN" >&2; exit 1; }
[ -s "\$JAR" ] || { echo "gatk wrapper: missing jar \$JAR" >&2; exit 1; }
declare -a opts args
while [ \$# -gt 0 ]; do
  if [ "\$1" = "--java-options" ]; then
    shift
    if [ \$# -eq 0 ]; then break; fi
    for o in \$1; do opts[\${#opts[@]}]="\$o"; done
  else
    args[\${#args[@]}]="\$1"
  fi
  shift
done
exec "\$JAVA_BIN" \${opts[@]+"\${opts[@]}"} -jar "\$JAR" \${args[@]+"\${args[@]}"}
EOF
  chmod +x "$dest"
  echo "  [gatk] wrote launcher $dest -> $JAVA -jar $GATK_JAR"
}

# ================================ fetch =====================================
fetch_attrs_tsv() {
  # Read-only entity read through terra/terra.py (entity_sample -> Rawls
  # get_entities_query). No mutating call anywhere; confirm=True is never passed.
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  + $TERRA_PY <inline terra.py entity reader> $NS $WS $ETYPE $ENTITY > $ATTRS_TSV"
    return 0
  fi
  mkdir -p "$OUTPUTS"
  "$TERRA_PY" - "$NS" "$WS" "$ETYPE" "$ENTITY" "$HERE" "$NW" > "$ATTRS_TSV" <<'PY'
import sys
ns, ws, etype, entity, testkit, suf = sys.argv[1:7]
sys.path.insert(0, testkit)  # terra.py lives beside this script
import terra  # read-only calls only; every mutation needs confirm=True, never used here

page, row = 1, None
while row is None:
    d = terra.entity_sample(ns, ws, etype, page_size=50, page=page)
    for e in (d.get("results") or d.get("entities") or []):
        if e.get("name") == entity:
            row = e
            break
    meta = d.get("resultMetadata") or {}
    pages = max(1, int(meta.get("filteredPageCount") or 1))
    if row is not None or page >= pages:
        break
    page += 1
if row is None:
    raise SystemExit("entity %s:%s not found in %s/%s" % (etype, entity, ns, ws))

attrs = row.get("attributes") or {}
kept = 0
for key in sorted(attrs):
    if not key.endswith(suf):
        continue
    value = attrs[key]
    if isinstance(value, str):
        print("%s\t%s" % (key, value))
        kept += 1
    else:
        sys.stderr.write("[attrs] %s is not a string (%s); skipped\n"
                         % (key, type(value).__name__))
sys.stderr.write("[attrs] %d *%s attribute(s) on %s/%s:%s\n" % (kept, suf, ns, etype, entity))
PY
}

do_fetch() {
  echo "== fetch: $NS/$WS  $ETYPE:$ENTITY  (*${NW} attributes only)"
  run mkdir -p "$OUTPUTS"
  fetch_attrs_tsv
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  (dry-run: gs:// URLs come from the *${NW} attributes at run time; paths shown as \$OUTPUTS/<object name>)"
  else
    : > "$MANIFEST_ROWS"
    echo "  [attrs] $(wc -l < "$ATTRS_TSV" | tr -d ' ') *${NW} attribute(s) -> $ATTRS_TSV"
  fi

  local attr name url dest rsize lsize sha size missing=0
  for attr in "${REQUIRED_ATTRS[@]}"; do
    name="${attr}${NW}"
    url=""
    [ "$DRY_RUN" -eq 1 ] || url=$(attr_url "$name")
    if [ -z "$url" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [plan] $name: $GSUTIL cp <\$$name> $OUTPUTS/<object basename>  (+ sha256/size into MANIFEST.json)"
        continue
      fi
      echo "  [missing] $name is not set on $ETYPE:$ENTITY — that chain output has not been written yet"
      missing=$((missing + 1))
      continue
    fi

    dest=$OUTPUTS/$(basename "$url")
    echo "  [gs] $name -> $url"
    rsize=$(remote_size "$url")
    if [ -s "$dest" ]; then
      lsize=$(bytes_of "$dest")
      if [ -n "$rsize" ] && [ "$lsize" = "$rsize" ]; then
        echo "  [skip] $dest already present ($lsize bytes == remote)"
      else
        echo "  [stale] $dest is $lsize bytes vs remote ${rsize:-unknown} -> re-downloading"
        run "$GSUTIL" cp "$url" "$dest" || die "gsutil cp failed: $url -> $dest"
      fi
    else
      run "$GSUTIL" cp "$url" "$dest" || die "gsutil cp failed: $url -> $dest"
    fi

    sha=""; size=""
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "  [plan] sha256sum + bytes of $dest"
    else
      [ -s "$dest" ] || die "download failed: $url -> $dest"
      size=$(bytes_of "$dest")
      sha=$(sha256_of "$dest")
      echo "  [ok] $dest  $size bytes  sha256=$sha"
      printf '%s\t%s\t%s\t%s\t%s\n' "$name" "$url" "$dest" "$sha" "$size" >> "$MANIFEST_ROWS"
    fi
  done

  # optional CSI/TBI siblings: nice-to-have, never fatal
  for attr in "${OPTIONAL_ATTRS[@]}"; do
    name="${attr}${NW}"
    url=$(attr_url "$name")
    if [ -z "$url" ]; then
      if [ "$DRY_RUN" -eq 1 ]; then
        echo "  [plan] $name: fetched only if the config exports it"
      else
        echo "  [optional] $name not exported; the profile stage indexes locally"
      fi
      continue
    fi
    dest=$OUTPUTS/$(basename "$url")
    echo "  [gs] $name -> $url"
    if [ -s "$dest" ]; then
      echo "  [skip] $dest already present"
      continue
    fi
    run "$GSUTIL" cp "$url" "$dest" || echo "  [warn] optional index download failed: $url (will index locally)"
  done

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  + write $MANIFEST (attr -> {gs, local, sha256, bytes})"
    return 0
  fi

  "$TERRA_PY" - "$MANIFEST_ROWS" "$MANIFEST" "$NS" "$WS" "$ETYPE" "$ENTITY" "$NW" <<'PY'
import json, os, sys, time
rows_path, manifest_path = sys.argv[1:3]
ns, ws, etype, entity = sys.argv[3:7]
suf = sys.argv[7]
files = {}
if os.path.exists(rows_path):
    with open(rows_path) as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 5 or not parts[0]:
                continue
            attr, url, local, sha, size = parts
            files[attr] = {"gs": url, "local": local, "sha256": sha,
                           "bytes": int(size) if size.isdigit() else None}
doc = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
       "workspace": "%s/%s" % (ns, ws),
       "entity": "%s:%s" % (etype, entity),
       "convention": "new-side chain outputs = *%s attributes; inherited baseline = unsuffixed attributes" % suf,
       "files": files}
os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
with open(manifest_path, "w") as fh:
    json.dump(doc, fh, indent=1, sort_keys=True)
print("  [manifest] %s (%d file(s))" % (manifest_path, len(files)))
PY

  if [ "$missing" -ne 0 ]; then
    echo "fetch incomplete: $missing required *${NW} attribute(s) not set yet (expected until the chain finishes)"
    return 1
  fi
  echo "fetch complete: $MANIFEST"
}

# ================================ profile ===================================
new_vcf_local() { # attr-name -> local fetched path ("" if the attr is unknown)
  local url
  url=$(attr_url "${1}${NW}")
  [ -n "$url" ] || return 0
  printf '%s\n' "$OUTPUTS/$(basename "$url")"
}

profile_preflight() {
  if [ ! -x "$PROFILE_BIN" ]; then
    die "gatk-sv-profile not found/executable as $PROFILE_BIN.\n  install it:  python -m venv .venv-profile && .venv-profile/bin/pip install -e <gatk-sv-profile-checkout>\n  or point at it:  export PROFILE_BIN=/path/to/gatk-sv-profile   (docs/comparators.md)"
  fi
  if [ ! -e "$GATK_JAR" ]; then
    ls -l "$GATK_JAR" >&2 || true
    die "GATK jar missing at $GATK_JAR.\n  build one:  cd <gatk checkout> && JAVA_HOME=<jdk17> ./gradlew localJar\n  then set:   export GATK_JAR=<gatk checkout>/build/libs/gatk-package-<ver>-local.jar   (docs/local-replay.md)"
  fi
  echo "  [gatk] jar: $GATK_JAR"
  [ -x "$JAVA" ] || die "no usable java at '$JAVA' — GATK 4.6 needs JDK 17+ on a path GATK_BIN can reach"
  [ -s "$REF_DICT" ]    || die "reference dict missing: $REF_DICT"
  [ -s "$CONTIG_LIST" ] || die "contig list missing: $CONTIG_LIST"
  write_gatk_wrapper "$GATK_BIN"
}

ensure_index() {
  local vcf=$1
  case "$vcf" in
    *.vcf.gz) ;;
    *) return 0 ;;
  esac
  if [ -e "$vcf.tbi" ] || [ -e "${vcf%.gz}.csi" ]; then
    echo "  [index] ok: $(basename "$vcf")"
    return 0
  fi
  if [ -s "$OUTPUTS/$(basename "$vcf").tbi" ]; then
    run ln -sf "$OUTPUTS/$(basename "$vcf").tbi" "$vcf.tbi"
    return 0
  fi
  run bcftools index -f -t "$vcf"
}

profile_channel() { # $1=pesr|depth  $2=baseline vcf  $3=new vcf  $4=out dir
  local channel=$1 base_vcf=$2 new_vcf=$3 out=$4
  echo "-- $channel: $LABEL_A $base_vcf   vs   $LABEL_B $new_vcf"
  if [ "$DRY_RUN" -eq 0 ] && [ ! -s "$base_vcf" ]; then
    die "baseline $channel VCF missing: $base_vcf (frozen locally by terra/fetch_baseline.py — see the header note on why the baseline side comes from work/staging)"
  fi
  if [ "$DRY_RUN" -eq 0 ] && [ ! -s "$new_vcf" ]; then
    die "new $channel VCF missing: $new_vcf — run \`terra/batch_fetch_compare.sh fetch\` first (the *${NW} attribute may not be set yet)"
  fi
  if [ -d "$out" ] && [ -n "$(ls -A "$out" 2>/dev/null || true)" ] && [ "$FORCE" -eq 0 ]; then
    echo "  [refuse] $out already exists and is non-empty — pass --force to write into it anyway"
    return 1
  fi
  ensure_index "$base_vcf"
  ensure_index "$new_vcf"
  run mkdir -p "$out"
  local cmd=("$PROFILE_BIN" run \
    --vcf-a "$base_vcf" --vcf-b "$new_vcf" \
    --label-a "$LABEL_A" --label-b "$LABEL_B" \
    --reference-dict "$REF_DICT" --contig-list "$CONTIG_LIST" \
    --output-dir "$out" \
    --gatk-path "$GATK_BIN" "--java-options=-Xmx$XMX" \
    --num-workers "$NUM_WORKERS")
  echo "  + ${cmd[*]} 2>&1 | tee $out/$channel.profile.log"
  if [ "$DRY_RUN" -eq 0 ]; then
    "${cmd[@]}" 2>&1 | tee "$out/$channel.profile.log"
  fi
}

do_profile() {
  echo "== profile: paired gatk-sv-profile — $LABEL_A (baseline, frozen in $STAGING) vs $LABEL_B (new *${NW} chain outputs)"
  profile_preflight
  local pesr_new depth_new rc=0
  if [ "$DRY_RUN" -eq 1 ]; then
    pesr_new=${OUTPUTS}/all_samples.genotype_batch.pesr.vcf.gz
    depth_new=${OUTPUTS}/all_samples.genotype_batch.depth.vcf.gz
    local u
    u=$(attr_url "genotyped_pesr_vcf${NW}"); [ -n "$u" ] && pesr_new=$OUTPUTS/$(basename "$u")
    u=$(attr_url "genotyped_depth_vcf${NW}"); [ -n "$u" ] && depth_new=$OUTPUTS/$(basename "$u")
    echo "  (dry-run: new-side file names shown as placeholders — the real ones come from the *${NW} attributes)"
  else
    pesr_new=$(new_vcf_local genotyped_pesr_vcf)
    [ -n "$pesr_new" ] || die "genotyped_pesr_vcf${NW} is not set on $ETYPE:$ENTITY (or $ATTRS_TSV is missing) — nothing to compare yet; run fetch against a finished chain"
    depth_new=$(new_vcf_local genotyped_depth_vcf)
  fi
  profile_channel pesr "$BASELINE_PESR_VCF" "$pesr_new" "$RESULTS/compare_pesr" || rc=1
  if [ -n "${depth_new:-}" ]; then
    profile_channel depth "$BASELINE_DEPTH_VCF" "$depth_new" "$RESULTS/compare_depth" || rc=1
  else
    echo "  [skip] depth channel: genotyped_depth_vcf${NW} is not set"
  fi
  return $rc
}

# ================================= table ====================================
# compare_batch_tables.py is authored in parallel, so its interface is discovered rather
# than assumed: its --help (read-only) in a real run, its source in --dry-run.
tool_flags() {
  local out=""
  if [ "$DRY_RUN" -eq 0 ]; then
    echo "  + $TABLE_PY $COMPARE_TABLES --help" >&2
    out=$("$TABLE_PY" "$COMPARE_TABLES" --help 2>&1 || true)
  fi
  if [ -z "$out" ]; then
    echo "  (flags read from the $COMPARE_TABLES source, not executed)" >&2
    out=$(grep -o -- "--[a-z][a-z0-9-]*" "$COMPARE_TABLES" 2>/dev/null | sort -u || true)
  fi
  printf '%s\n' "$out"
}

do_table() {
  echo "== table: compare_batch_tables.py (4 genotyping tables + SR cutoff diagnostics vs v1.1.1)"
  if [ ! -f "$COMPARE_TABLES" ]; then
    echo "compare_batch_tables.py not present yet"
    return 0
  fi
  if [ ${#EXTRA[@]} -gt 0 ]; then
    run "$TABLE_PY" "$COMPARE_TABLES" ${EXTRA[@]+"${EXTRA[@]}"}
    return 0
  fi
  # New side = the fetched *${NW} chain outputs in $OUTPUTS; baseline side = the
  # frozen copy in $STAGING. compare/compare_batch_tables.py ships in this repo, so
  # name its flags directly instead of guessing them from --help.
  [ -f "$COMPARE_TABLES" ] || die "table differ not found at $COMPARE_TABLES"
  run mkdir -p "$TABLES_DIR"
  run "$TABLE_PY" "$COMPARE_TABLES" \
      --baseline-dir "$STAGING" \
      --new-dir "$OUTPUTS" \
      --out-prefix "$TABLES_DIR/batch_tables" \
      ${EXTRA[@]+"${EXTRA[@]}"}
}

# ================================== main ====================================
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --force)   FORCE=1 ;;
    -h|--help) usage; exit 0 ;;
    --) shift; EXTRA=("$@"); break ;;
    -*) echo "unknown flag: $1" >&2; usage; exit 2 ;;
    *) if [ -z "$SUB" ]; then SUB=$1; else EXTRA+=("$1"); fi ;;
  esac
  shift
done

[ -n "$SUB" ] || { usage; exit 2; }
case "$SUB" in
  fetch)   do_fetch ;;
  profile) do_profile ;;
  table)   do_table ;;
  all)     do_fetch; do_profile; do_table ;;
  *) echo "unknown subcommand: $SUB" >&2; usage; exit 2 ;;
esac
