#!/usr/bin/env bash
# wdl_gate.sh — is this WDL actually launchable, or does it only typecheck?
#
# `miniwdl check` passes a workflow whose call sites never bind a required input
# (IncompleteCall is a warning, not an error) and one that passes an input the callee
# never declared. Both are unlaunchable: the first dies when Cromwell asks for the
# missing value, the second when it rejects an unknown key. This gate makes them a
# number you can diff across refs.
#
# It also checks the input JSONs CI pairs with each WDL (checks/wdl_inputs_check.py renders the
# ref's default inputs in a temp tree; the checkout is never written to):
#   MISSING-INPUTS  required inputs no input JSON binds (womtool: "Required workflow input ... not specified")
#   EXTRA-KEYS      keys an input JSON names that the WDL cannot accept (womtool: "Unexpected input provided")
# Both are hard failures with or without --strict. Only key sets are compared, never values. With
# WOMTOOL_JAR exported, each pair also runs CI's `java -jar $WOMTOOL_JAR validate <wdl> -i <json>`;
# without it that half prints a counted SKIPPED line and --strict fails on it.
# See docs/static-checks.md for the shapes EXTRA-KEYS deliberately does not report.
#
# Each ref is materialized to its own directory by scripts/fetch_wdl.py, because imports
# resolve by filename within the directory.
#
#   checks/wdl_gate.sh                      # origin/main vs $GSVTK_BRANCH, default workflows
#   checks/wdl_gate.sh v1.1.1 HEAD          # any two refs
#   checks/wdl_gate.sh --wf SVShell --wf ResolveCpxSvGenotyping HEAD
#   checks/wdl_gate.sh --strict HEAD        # nonzero exit if anything is unlaunchable
#   checks/wdl_gate.sh --wf IntegrateGDVcf 7fbf1171   # the MISSING-INPUTS column is CI's question
#   checks/wdl_gate.sh --tree DIR --inputs-root RENDERED --wf SVShell  # a tree and JSONs you have
#   checks/wdl_gate.sh --no-terra HEAD      # skip CI's -t half (the Terra input JSONs)
#   (also: --miniwdl PATH)
#
# Each ref also gets a SEMANTICS row from checks/wdl_semantics.py: workflow-scope write_*, File
# inputs only ever tested with defined(), pipes whose reader exits early, and `bash -n` on the
# rendered command. These counts are not zero on gatk-sv `main`; with two refs a DELTA line is
# printed for anything that moved, and --strict fails on a rise.
#
# Needs miniwdl (pip install miniwdl, or make setup) and a local gatk-sv clone; the input layer
# also needs jinja2. miniwdl is resolved as $MINIWDL, then PATH, then the bin next to the
# interpreter, then ./.venv/bin. womtool is optional; its absence is a printed skip.
#
# Exit codes: 0 no hard errors; 1 gate failed; 2 usage; 3 miniwdl not found;
# 4 GSVTK_GATK_SV_CHECKOUT unset.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd -P)"
. "$ROOT/kit/config.sh"

# --help prints the header comment block.
_usage() { awk 'NR>1 { if (!/^#/) exit; sub(/^# ?/, ""); print }' "$0"; }

REFS=()
WFS=()
STRICT=0
TREE=""                                  # an already-materialized WDL tree: fetch nothing
INPUTS_ROOT=""                           # an already-rendered gatk-sv tree: render nothing
TERRA=1                                  # CI validates the Terra input JSONs too, with -t
# Resolved by gsvtk-config (shared with terra/wdl_flat.py); --miniwdl overrides.
MINIWDL="${MINIWDL:-$("$GSVTK_PYTHON" "$ROOT/kit/gsvtk-config" miniwdl 2>/dev/null)}"
MINIWDL="${MINIWDL:-miniwdl}"

while [ $# -gt 0 ]; do
    case "$1" in
        --wf|--workflow) WFS+=("$2"); shift 2;;
        --strict)        STRICT=1; shift;;
        --miniwdl)       MINIWDL="$2"; shift 2;;
        --tree)          [ -d "$2" ] || { echo "--tree: no such directory: $2" >&2; exit 2; }
                         TREE="$(cd "$2" && pwd)"; shift 2;;
        --inputs-root)   [ -d "$2" ] || { echo "--inputs-root: no such directory: $2" >&2; exit 2; }
                         INPUTS_ROOT="$(cd "$2" && pwd)"; shift 2;;
        --no-terra)      TERRA=0; shift;;
        -h|--help)       _usage; exit 0;;
        -*)              echo "unknown flag: $1 (see --help)" >&2; exit 2;;
        *)               REFS+=("$1"); shift;;
    esac
done

# --tree checks an existing WDL directory; the ref column shows its name.
if [ -n "$TREE" ]; then
    if [ ${#REFS[@]} -gt 1 ]; then
        echo "--tree checks ONE tree; name a second ref by fetching it (a baseline needs its own ref)" >&2
        exit 2
    fi
    [ ${#REFS[@]} -eq 0 ] && REFS=("$(basename "$TREE")")
fi

# Default: compare baseline against the change.
if [ ${#REFS[@]} -eq 0 ]; then
    REFS=(origin/main)
    [ -n "${GSVTK_BRANCH:-}" ] && REFS+=("$GSVTK_BRANCH")
fi
# The main entrypoints and frequently changed WDLs. IntegrateGDVcf is included because it once
# shipped with a required input no CI input JSON bound.
if [ ${#WFS[@]} -eq 0 ]; then
    WFS=(SVShell GATKSVPipelineSingleSample GenotypeBatch MakeCohortVcf IntegrateGDVcf)
fi

if [ ! -x "$MINIWDL" ] && ! command -v "$MINIWDL" >/dev/null 2>&1; then
    echo "miniwdl not found as '$MINIWDL'. Install it (make setup, or python -m pip" >&2
    echo "install --user miniwdl), or point MINIWDL at an existing install." >&2
    echo "  (checked: MINIWDL, PATH, the bin next to $GSVTK_PYTHON, $ROOT/.venv/bin)" >&2
    exit 3
fi
[ -n "${GSVTK_GATK_SV_CHECKOUT:-}" ] || {
    # Not needed when both --tree and --inputs-root are given.
    if [ -z "$TREE" ] || [ -z "$INPUTS_ROOT" ]; then
        echo "GSVTK_GATK_SV_CHECKOUT is unset: which gatk-sv clone are we gating?" >&2; exit 4
    fi
}

# Find an interpreter that can import the WDL module (it may differ from the miniwdl CLI's).
# If none, the SEMANTICS row prints SKIPPED and the gate fails.
SEM_PY=""
for _cand in "${GSVTK_PYTHON:-}" "$ROOT/.venv/bin/python" "$(dirname "$MINIWDL")/python3" python3; do
    [ -n "$_cand" ] && "$_cand" -c 'import WDL' >/dev/null 2>&1 && { SEM_PY="$_cand"; break; }
done

# Rendering needs jinja2; checking needs the WDL module. They are probed separately: JINJA_PY need
# not import WDL, because wdl_inputs_check.py --render-only does not use miniwdl.
JINJA_PY=""
for _cand in "${GSVTK_PYTHON:-}" "$ROOT/.venv/bin/python" "$(dirname "$MINIWDL")/python3" python3; do
    [ -n "$_cand" ] && "$_cand" -c 'import jinja2' >/dev/null 2>&1 && { JINJA_PY="$_cand"; break; }
done

OUT="$(gsvtk_work wdl-gate)"
status=0
delta_bad=0
SEM_LINES=()
# Skip counters, summed over refs. --strict fails on prerequisite skips.
skip_prereq=0                          # no jinja2 / no WDL module / no womtool jar / no java
skip_nopairs=0                         # CI itself has no input JSON for that workflow
# Extra-key counters, per ref.
ek_skip=0                              # the layer could not run at all (prerequisite, or no answer)
ek_pairs=0                             # workflows whose JSONs were read and found free of extras
ek_found=0                             # extra keys named
ek_bad=0                               # workflows carrying at least one
printf '%-30s %-26s %-6s %-16s %-15s %-15s %s\n' WORKFLOW REF EXIT INCOMPLETECALL STALE-BINDINGS MISSING-INPUTS EXTRA-KEYS
for ref in "${REFS[@]}"; do
    # The ref becomes a directory name under `rm -rf`; refuse `.`/`..` and only remove children of $OUT.
    case "$ref" in
      ""|"."|".."|*..*)
        printf '%-30s %-26s %s\n' "(ref)" "${ref:-(empty)}" \
               "REJECTED — name a real ref (a branch, tag or sha), not '.' or '..'"
        status=1; continue;;
    esac
    dir="$OUT/${ref//\//-}"
    if [ -n "$TREE" ]; then
        dir="$TREE"          # somebody else's tree: never fetched, and never wiped by the guard below
    else
        case "$dir" in "$OUT"/*) rm -rf "$dir" ;; esac
        "$GSVTK_PYTHON" "$ROOT/scripts/fetch_wdl.py" --ref "$ref" --dest "$dir" >/dev/null || {
            printf '%-30s %-26s %s\n' "(fetch)" "$ref" "FAILED — is $ref a real ref in ${GSVTK_GATK_SV_CHECKOUT}?"
            status=1; continue
        }
    fi
    sha=$(sed -n 's/^sha=//p' "$dir/.provenance" 2>/dev/null | cut -c1-8)
    refsha="${ref##*/}@${sha:-no-sha}"

    # ------------------------------------------------- the input-binding layer, once per ref
    # Render the ref's input JSONs once and reuse them for every --wf.
    inputs_root="$INPUTS_ROOT"
    inputs_note=""
    if [ -z "$SEM_PY" ]; then
        inputs_note="no interpreter here imports WDL (make setup, or pip install miniwdl)"
    elif [ -z "$inputs_root" ] && [ -z "$JINJA_PY" ]; then
        inputs_note="no interpreter here imports jinja2, which gatk-sv's build_inputs.py imports"
    elif [ -z "$inputs_root" ]; then
        rdest="$OUT/${ref//\//-}.inputs"
        rout="$("$JINJA_PY" "$HERE/wdl_inputs_check.py" --render-only \
            --repo "${GSVTK_GATK_SV_CHECKOUT:-}" --ref "$ref" --dest "$rdest" 2>&1)"; rrc=$?
        if [ "$rrc" -ne 0 ]; then
            inputs_note="could not render the ref's default inputs: $(printf '%s' "$rout" \
                | tr '\n\r' '  ' | cut -c1-200)"
        else
            inputs_root="$rdest"
        fi
    fi
    wt_run=0; wt_fail=0; wt_skip=0; wt_nopairs=0; wt_reason=""
    ref_nopairs=0
    ek_skip=0; ek_pairs=0; ek_found=0; ek_bad=0; ek_unanswered=0
    for wf in "${WFS[@]}"; do
        # An absent workflow (typo, or not present at this ref) is a failure.
        if [ ! -f "$dir/$wf.wdl" ]; then
            printf '%-30s %-26s %s\n' "$wf" "$ref" "ABSENT at $sha"
            status=1
            echo "        nothing was checked for $wf at $ref: wrong name, or it did not exist at"
            echo "        that ref. This is a failure, not a pass."
            continue
        fi
        log="$dir/$wf.check.txt"
        ( cd "$dir" && "$MINIWDL" check "$wf.wdl" >"$log" 2>&1 ); rc=$?
        inc=$(grep -c "IncompleteCall" "$log" 2>/dev/null || true)
        stale=$(grep -oE "No such input [A-Za-z0-9_]+" "$log" 2>/dev/null | sort -u | wc -l | tr -d ' ')

        # MISSING-INPUTS fails the gate with or without --strict.
        icell="-"
        ecell="-"
        idetail=""
        if [ -n "$inputs_note" ]; then
            icell="SKIPPED"
            ecell="SKIPPED"
            ek_skip=$((ek_skip + 1))
            skip_prereq=$((skip_prereq + 1))
        else
            iargs=(--wdl-dir "$dir" --wf "$wf" --inputs-root "$inputs_root")
            [ "$TERRA" -eq 0 ] && iargs+=(--no-terra)
            iout="$("$SEM_PY" "$HERE/wdl_inputs_check.py" "${iargs[@]}" 2>&1)"; irc=$?
            iline=$(printf '%s\n' "$iout" | grep -F "GSVTK-INPUTS wf=$wf " | head -1 | sed 's/^[^ ]* //')
            eline=$(printf '%s\n' "$iout" | grep -F "GSVTK-EXTRAS wf=$wf " | head -1 | sed 's/^[^ ]* //')
            wline=$(printf '%s\n' "$iout" | grep -F "GSVTK-WOMTOOL wf=$wf " | head -1 | sed 's/^[^ ]* //')
            if [ -z "$iline" ]; then
                # No verdict line: the checker itself failed.
                icell="CHECK-FAILED"
                idetail="exited $irc without a verdict; its output:"
                ecell="NO-ANSWER"; ek_skip=$((ek_skip + 1)); ek_unanswered=$((ek_unanswered + 1))
                status=1
            else
                istatus=$(printf '%s' "$iline" | sed -n 's/.* status=\([A-Za-z-]*\).*/\1/p')
                imissing=$(printf '%s' "$iline" | sed -n 's/.* missing=\([0-9]*\).*/\1/p')
                ecount=$(printf '%s' "$eline" | sed -n 's/.* extras=\([0-9]*\).*/\1/p')
                extras_flag=0
                if [ -z "$eline" ]; then
                    # The extras half produced no answer.
                    ecell="NO-ANSWER"; ek_skip=$((ek_skip + 1)); ek_unanswered=$((ek_unanswered + 1))
                    extras_flag=1; status=1
                else
                    estatus=$(printf '%s' "$eline" | sed -n 's/.* status=\([A-Za-z-]*\).*/\1/p')
                    case "$estatus" in
                        # Words, not a bare number, so it isn't confused with MISSING-INPUTS.
                        OK)        ecell="clean"; ek_pairs=$((ek_pairs + 1));;
                        FINDING)   ecell="${ecount:-?} extra"
                                   ek_found=$((ek_found + ${ecount:-1}))
                                   ek_bad=$((ek_bad + 1)); status=1;;
                        *)         ecell="NOT-CHECKED";;  # why is one column left, in MISSING-INPUTS
                    esac
                    # Print details unless the extras answer is clean or NOT-CHECKED.
                    case "$ecell" in clean|NOT-CHECKED) ;; *) extras_flag=1;; esac
                fi
                wstatus=$(printf '%s' "$wline" | sed -n 's/.* status=\([A-Za-z-]*\).*/\1/p')
                wfail=$(printf '%s' "$wline" | sed -n 's/.* failures=\([0-9]*\).*/\1/p')
                case "$wstatus" in
                    RUN)       wt_run=$((wt_run + 1)); [ "${wfail:-0}" -gt 0 ] && wt_fail=$((wt_fail + 1));;
                    NO-PAIRS)  wt_nopairs=$((wt_nopairs + 1));;
                    SKIPPED)   wt_skip=$((wt_skip + 1))
                               wt_reason=$(printf '%s' "$wline" \
                                   | sed -n 's/.* detail=\(.*\)/\1/p' | cut -c1-120);;
                esac
                case "$istatus" in
                    OK)        icell="${imissing:-0}";;
                    FINDING)   icell="${imissing:-?}"; status=1;;
                    NO-INPUTS) icell="NO-INPUT-JSON"; ref_nopairs=$((ref_nopairs + 1));;
                    *)         icell="${istatus:-UNKNOWN}"; status=1;;   # NO-WDL, LOAD-FAILURE, BAD-JSON
                esac
                if [ "$istatus" != OK ] || [ "$wstatus" = SKIPPED ] || [ "${wfail:-0}" -gt 0 ] \
                   || [ "$extras_flag" -eq 1 ] \
                   || printf '%s\n' "$iout" | grep -q '^  DISAGREES'; then
                    idetail="$(printf '%s\n' "$iout" | grep -v '^GSVTK-')"
                fi
            fi
        fi
        printf '%-30s %-26s %-6s %-16s %-15s %-15s %s\n' "$wf" "$refsha" "$rc" "$inc" "$stale" "$icell" "$ecell"
        grep -oE "No such input [A-Za-z0-9_]+" "$log" 2>/dev/null | sort -u | sed 's/^/        stale binding: /'
        [ -n "$idetail" ] && printf '%s\n' "$idetail" | sed 's/^/        /'
        if [ "$rc" -ne 0 ] || { [ "$STRICT" -eq 1 ] && { [ "${inc:-0}" -ne 0 ] || [ "${stale:-0}" -ne 0 ]; }; }; then
            status=1
            [ "$rc" -ne 0 ] && echo "        hard error — full report: $log"
        fi
    done

    # Per-ref INPUTS-JSON and WOMTOOL rows: ran, had nothing to run on, or could not run.
    if [ -n "$inputs_note" ]; then
        printf '%-30s %-26s %s\n' INPUTS-JSON "$refsha" "SKIPPED — $inputs_note"
        echo "        no required-input question was answered for ${#WFS[@]} workflow(s) at $ref."
        echo "        Nothing here agrees or disagrees with CI's 'Test with WOMtool' step."
    fi
    if [ "$wt_skip" -gt 0 ]; then
        printf '%-30s %-26s %s\n' WOMTOOL "$refsha" \
            "SKIPPED — $wt_skip of ${#WFS[@]} workflow(s): ${wt_reason:-no womtool jar}"
        echo "        CI's own command was not reproduced for those workflows. A counted skip, never"
        echo "        a pass: --strict fails on it (WOMTOOL_JAR=<path>, or pip install -r requirements-dev.txt for jinja2)."
    elif [ "$wt_fail" -gt 0 ]; then
        printf '%-30s %-26s %s\n' WOMTOOL "$refsha" "FAILED — womtool rejected ${wt_fail} pair(s) of $wt_run"
    elif [ "$wt_run" -gt 0 ]; then
        printf '%-30s %-26s %s\n' WOMTOOL "$refsha" "RUN=$wt_run FAIL=0 (womtool validate, CI's command)"
    elif [ "$wt_nopairs" -gt 0 ]; then
        printf '%-30s %-26s %s\n' WOMTOOL "$refsha" "NO-PAIRS — CI has no input JSON for these either"
    fi
    # Per-ref EXTRA-KEYS summary row.
    ek_noans=$(( ${#WFS[@]} - ek_pairs - ek_bad - ek_skip ))
    if [ "$ek_skip" -gt 0 ]; then
        printf '%-30s %-26s %s\n' EXTRA-KEYS "$refsha" \
            "SKIPPED — $ek_skip of ${#WFS[@]} workflow(s): no extra-key answer was produced"
        echo "        Whether those input JSONs bind an input the WDL never declared is UNANSWERED"
        echo "        for this ref, not answered clean. Same prerequisite as the INPUTS-JSON row above;"
        echo "        --strict fails on it."
    elif [ "$ek_found" -gt 0 ]; then
        printf '%-30s %-26s %s\n' EXTRA-KEYS "$refsha" \
            "FAILED — $ek_found un-declared key(s) in $ek_bad of ${#WFS[@]} workflow(s)"
    elif [ "$ek_pairs" -gt 0 ]; then
        ek_plural="s"; [ "$ek_pairs" -eq 1 ] && ek_plural=""
        printf '%-30s %-26s %s\n' EXTRA-KEYS "$refsha" \
            "CLEAN — $ek_pairs workflow(s)' input JSON${ek_plural} name no key the WDL does not declare"
    else
        printf '%-30s %-26s %s\n' EXTRA-KEYS "$refsha" \
            "NOT-CHECKED — $ek_noans workflow(s) had no comparable input JSON at this ref"
    fi
    skip_prereq=$((skip_prereq + wt_skip + ek_unanswered))
    # A prerequisite skip fails --strict; a workflow CI has no input JSON for does not.
    skip_nopairs=$((skip_nopairs + ref_nopairs + wt_nopairs))

    # One SEMANTICS row per ref: the scan reads every WDL in the tree.
    if [ -z "$SEM_PY" ]; then
        printf '%-30s %-26s %s\n' SEMANTICS "$refsha" \
               "SKIPPED — no interpreter here imports WDL (make setup, or pip install miniwdl)"
        echo "        nothing was checked by the semantics layer for $ref. This is a failure, not a pass."
        status=1
        continue
    fi
    sem="$("$SEM_PY" "$HERE/wdl_semantics.py" --dir "$dir" --summary-only \
        --jobs "${GSVTK_SEMANTICS_JOBS:-0}" 2>/dev/null)"; semrc=$?
    if [ -z "${sem//[^0-9= ]/}" ]; then
        printf '%-30s %-26s %s\n' SEMANTICS "$refsha" "FAILED (exit $semrc) — run: $SEM_PY $HERE/wdl_semantics.py --dir $dir"
        status=1; continue
    fi
    printf '%-30s %-26s %s\n' SEMANTICS "$refsha" "$sem"
    SEM_LINES+=("$sem")
    case " $sem " in *" LOAD-FAILURES=0 "*) ;; *)
        echo "        LOAD-FAILURES: miniwdl could not parse at least one file, so every count on this"
        echo "        row is a partial answer. Full list: $SEM_PY $HERE/wdl_semantics.py --dir $dir"
        status=1;;
    esac
done

# With two or more refs, print SEMANTICS counts that changed between the first and last; a rise is a finding.
if [ ${#SEM_LINES[@]} -ge 2 ]; then
    base_line="${SEM_LINES[0]}"; head_line="${SEM_LINES[$((${#SEM_LINES[@]} - 1))]}"
    for kv in $head_line; do
        k="${kv%%=*}"; want="${kv##*=}"
        base=""
        # Match the whole key field, not a prefix of the line.
        for bk in $base_line; do
            [ "${bk%%=*}" = "$k" ] && base="${bk##*=}"
        done
        case "$base" in ''|*[!0-9]*) continue;; esac     # absent or not a count: not a delta
        case "$want" in ''|*[!0-9]*) continue;; esac
        [ "$base" = "$want" ] && continue
        printf '        SEMANTICS DELTA %-14s %s -> %s (%+d vs baseline)\n' "$k" "$base" "$want" "$((want - base))"
        [ "$want" -gt "$base" ] && delta_bad=1
    done
fi

if [ "$status" -eq 0 ] && [ "$STRICT" -eq 1 ] && [ "$delta_bad" -eq 1 ]; then
    status=1
fi

# --strict fails on a prerequisite skip (no womtool jar, java, jinja2 or WDL module). A workflow CI
# has no input JSON for is only reported, matching CI.
if [ "$status" -eq 0 ] && [ "$STRICT" -eq 1 ] && [ "$skip_prereq" -gt 0 ]; then
    status=1
fi

if [ "$status" -eq 0 ] && [ "$STRICT" -eq 1 ]; then
    echo "no hard errors, and --strict is satisfied: no IncompleteCall, no stale binding, no missing"
    echo "required input in any CI-matched input JSON, no un-declared key in one either, no womtool"
    echo "failure, no rise in any SEMANTICS count, no layer skipped for a missing prerequisite, and"
    echo "every workflow named was present."
elif [ "$status" -eq 0 ]; then
    echo "no hard errors. Re-run with --strict to treat IncompleteCall / stale bindings / a rise in"
    echo "the SEMANTICS counts / any prerequisite skip as failure (today's gatk-sv carries a few of"
    echo "each on purpose, so --strict is a diff-against-baseline decision, not a default). Neither"
    echo "hard column is in that list: a MISSING-INPUTS count is a required input no CI input JSON"
    echo "binds, and an EXTRA-KEYS count is an input JSON naming a key the WDL never declared. Both"
    echo "are hard either way, because CI is red on both -- womtool's 'Unexpected input provided' for"
    echo "the second one."
else
    echo "gate FAILED: see the rows above; rc=2 is a hard error, IncompleteCall means a required"
    echo "             input is never bound (unlaunchable), stale binding means the callee has"
    echo "             dropped or renamed an input this caller still passes, MISSING-INPUTS means"
    echo "             a required input is absent from the input JSONs CI renders -- which is what"
    echo "             'Required workflow input ... not specified' in CI says, and miniwdl cannot"
    echo "             see -- and EXTRA-KEYS is its mirror: those JSONs name a key no declaration"
    echo "             can hand, which is womtool's 'Unexpected input provided' and CI's red build."
fi
if [ "$skip_nopairs" -gt 0 ]; then
    echo "note: $skip_nopairs workflow-run(s) had no CI input JSON to check, so the required-input"
    echo "      question was not asked for them (see the NO-INPUT-JSON rows above)."
fi
exit "$status"
