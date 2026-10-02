#!/usr/bin/env bash
# wdl_gate.sh — is this WDL actually launchable, or does it only typecheck?
#
# `miniwdl check` passes a workflow whose call sites never bind a required input
# (IncompleteCall is a warning, not an error) and one that passes an input the callee
# never declared. Both are unlaunchable: the first dies when Cromwell asks for the
# missing value, the second when it rejects an unknown key. Neither is visible from the
# WDL alone, and neither is caught by gatk-sv's CI. This gate makes them a number you
# can diff across refs.
#
# A third shape of unlaunchable lives OUTSIDE the WDL: an input the workflow requires that the
# rendered input JSON never binds. `miniwdl check` cannot see it, because it never opens an input
# file, and IncompleteCall stays 0, because the call site DOES bind it -- the caller who never will
# is Cromwell, at submission time. gatk-sv `7fbf1171` merged exactly that (`String sample_id` at
# wdl/IntegrateGDVcf.wdl:31, zero occurrences of `sample_id` in
# inputs/templates/test/IntegrateGDVcf/IntegrateGDVcf.json.tmpl); CI's "Test with WOMtool" step
# rejected it and this gate called it clean, exit 0. So the gate runs the other half of CI as well:
# checks/wdl_inputs_check.py renders the ref's own default inputs (upstream's
# build_default_inputs.sh, in a temp tree built by `git archive`; the gatk-sv checkout is never
# written to) and asks, per --wf name, whether every input the workflow REQUIRES is present in the
# key set of the input JSONs CI pairs with that WDL. Key SETS, never values -- womtool grades
# presence, and gatk-sv's own Terra configs are nothing but `${this placeholders}`. With
# WOMTOOL_JAR exported, each pair also gets CI's exact command
# (`java -jar $WOMTOOL_JAR validate <wdl> -i <json>`) and the two answers are compared; without it
# that half prints a named, counted SKIPPED line and --strict fails on it, because a check that
# could not run must never be reported as a check that passed.
#
# Each ref is materialized to its OWN directory by scripts/fetch_wdl.py. That is not
# tidiness: imports resolve by filename within the directory, so a mixed tree resolves
# against the wrong version and reports a result that is neither the old bug nor the new
# one. One directory per ref, one ref per directory.
#
#   checks/wdl_gate.sh                      # origin/main vs $GSVTK_BRANCH, default workflows
#   checks/wdl_gate.sh v1.1.1 HEAD          # any two refs
#   checks/wdl_gate.sh --wf SVShell --wf ResolveCpxSvGenotyping HEAD
#   checks/wdl_gate.sh --strict HEAD        # nonzero exit if anything is unlaunchable
#   checks/wdl_gate.sh --wf IntegrateGDVcf 7fbf1171   # the MISSING-INPUTS column is CI's question
#   checks/wdl_gate.sh --tree DIR --inputs-root RENDERED --wf SVShell  # a tree and JSONs you have
#   checks/wdl_gate.sh --no-terra HEAD      # skip CI's -t half (the Terra input JSONs)
#
# Beside the bindings, each ref also gets a SEMANTICS row from checks/wdl_semantics.py: the four
# things that make a task RUN rather than typecheck (workflow-scope write_*, File inputs only ever
# tested with defined(), pipes whose reader exits early, and `bash -n` on the rendered command).
# Those counts are NOT zero on gatk-sv `main` and are not meant to be: two refs print a DELTA line
# for anything that moved, and --strict fails on a rise, never on the baseline's own findings.
#
# Needs miniwdl (pip install miniwdl, or make setup) and a local gatk-sv clone. Both are
# checked up front, with the fix printed rather than a stack trace. miniwdl is resolved as
# $MINIWDL, then PATH, then the bin next to the interpreter, then ./.venv/bin -- because
# `make setup` installs it into a venv that this script's shell has no reason to have
# activated, and a PATH-only lookup used to call the whole loop unavailable. The input-binding
# layer wants one more thing, `jinja2` (what gatk-sv's own renderer imports), and it looks for it the
# same way; womtool is optional by design and its absence is a printed skip, never a silence.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd -P)"
. "$ROOT/kit/config.sh"

# --help prints this header. It used to be `sed -n "2,31p"`, a line range that quietly stopped
# covering the header the first time the header outgrew it -- a usage text with the newest flag
# missing is worse than none, because the flag then looks unimplemented. Print the comment block
# itself, up to the first line that is not a comment.
_usage() { awk 'NR>1 { if (!/^#/) exit; sub(/^# ?/, ""); print }' "$0"; }

REFS=()
WFS=()
STRICT=0
TREE=""                                  # an already-materialized WDL tree: fetch nothing
INPUTS_ROOT=""                           # an already-rendered gatk-sv tree: render nothing
TERRA=1                                  # CI validates the Terra input JSONs too, with -t
# Resolution lives in gsvtk-config so this script and terra/wdl_flat.py cannot disagree. An
# explicit --miniwdl below still wins; an explicit MINIWDL that does not resolve is kept as-is
# so the error names what the user actually asked for.
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

# `--tree` is the escape hatch for "I already have the tree": a checkout's own wdl/, a fixture, or a
# ref someone fetched earlier. It exists because the alternative is a test that needs a gatk-sv clone
# and a womtool jar, which is a test that does not run. It also means the ref column can name a
# directory rather than a ref, so say which one this is instead of inventing a SHA.
if [ -n "$TREE" ]; then
    if [ ${#REFS[@]} -gt 1 ]; then
        echo "--tree checks ONE tree; name a second ref by fetching it (a baseline needs its own ref)" >&2
        exit 2
    fi
    [ ${#REFS[@]} -eq 0 ] && REFS=("$(basename "$TREE")")
fi

# Gate baseline-vs-change unless told otherwise: a verdict about one ref alone cannot say
# whether you made things better or worse.
if [ ${#REFS[@]} -eq 0 ]; then
    REFS=(origin/main)
    [ -n "${GSVTK_BRANCH:-}" ] && REFS+=("$GSVTK_BRANCH")
fi
# The single-sample and batch entrypoints plus the two WDLs that churn most: enough to
# catch a broken call binding without checking all ~120 files.
if [ ${#WFS[@]} -eq 0 ]; then
    WFS=(SVShell GATKSVPipelineSingleSample GenotypeBatch MakeCohortVcf)
fi

if [ ! -x "$MINIWDL" ] && ! command -v "$MINIWDL" >/dev/null 2>&1; then
    echo "miniwdl not found as '$MINIWDL'. Install it (make setup, or python -m pip" >&2
    echo "install --user miniwdl), or point MINIWDL at an existing install." >&2
    echo "  (checked: MINIWDL, PATH, the bin next to $GSVTK_PYTHON, $ROOT/.venv/bin)" >&2
    exit 3
fi
[ -n "${GSVTK_GATK_SV_CHECKOUT:-}" ] || {
    # Only required when something has to be FETCHED or RENDERED from a clone. `--tree` plus
    # `--inputs-root` is a self-contained pair (a tree and JSONs you already have), and demanding a
    # checkout for it would be a prerequisite with no job -- the shape this script's whole skip idiom
    # exists to avoid.
    if [ -z "$TREE" ] || [ -z "$INPUTS_ROOT" ]; then
        echo "GSVTK_GATK_SV_CHECKOUT is unset: which gatk-sv clone are we gating?" >&2; exit 4
    fi
}

# The semantics scan needs the WDL *module*, not the `miniwdl` command, and those can live in
# different environments: miniwdl resolved from a user install and an interpreter that never saw it.
# So ask each candidate the question rather than assuming the CLI's environment is the one to run in.
# An empty SEM_PY is not a silent skip: it prints a SKIPPED row and fails the gate, because "the
# guard never fired" and "the guard could not fire" otherwise print the same thing.
SEM_PY=""
for _cand in "${GSVTK_PYTHON:-}" "$ROOT/.venv/bin/python" "$(dirname "$MINIWDL")/python3" python3; do
    [ -n "$_cand" ] && "$_cand" -c 'import WDL' >/dev/null 2>&1 && { SEM_PY="$_cand"; break; }
done

# The input-binding layer is two interpreters deep and they want different packages: rendering
# gatk-sv's templates needs `jinja2` (which gatk-sv's build_inputs.py imports, not this repo), and
# answering the question needs the WDL module. Probing one and assuming the other is how a layer
# gets written off as unavailable on the machine that has half of it.
JINJA_PY=""
for _cand in "${GSVTK_PYTHON:-}" "$ROOT/.venv/bin/python" "$(dirname "$MINIWDL")/python3" python3; do
    [ -n "$_cand" ] && "$_cand" -c 'import jinja2' >/dev/null 2>&1 && { JINJA_PY="$_cand"; break; }
done

OUT="$(gsvtk_work wdl-gate)"
status=0
delta_bad=0
SEM_LINES=()
# Counted skips, summed over refs: a prerequisite that was missing prints a named line per ref and
# lands here, where --strict turns the whole set into a failure.
skip_prereq=0                          # no jinja2 / no WDL module / no womtool jar / no java
skip_nopairs=0                         # CI itself has no input JSON for that workflow
printf '%-30s %-26s %-6s %-16s %-15s %s\n' WORKFLOW REF EXIT INCOMPLETECALL STALE-BINDINGS MISSING-INPUTS
for ref in "${REFS[@]}"; do
    # A ref is user input that becomes a directory name under a `rm -rf`. `.` and `..` are
    # legal-ish git spellings and would point that wipe at the cache parent -- into the work
    # tree on Linux, where GNU rm obliges. Refuse them by name, and prove the target is a
    # child of the cache before removing anything.
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
    # Which input JSONs exist at all is a property of the REF (upstream's renderer decides file by
    # file whether a values bundle defines everything a template touches), so it is rendered here and
    # reused by every --wf below. ~1.5 s of git archive plus jinja2, never inside the workflow loop.
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
    for wf in "${WFS[@]}"; do
        # An absent workflow is a failure, not a blank cell. A typo'd name, or a name that only
        # exists on newer refs (v1.1.1 has no SVShell.wdl), used to contribute nothing to the exit
        # code even under --strict -- so the gate certified "no hard errors" having checked
        # nothing, which is the exact shape of wrong answer this script exists to catch.
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

        # The column CI would have filled in at 7fbf1171: is every input the workflow REQUIRES
        # present in the key set of the input JSONs CI pairs with this WDL? A missing one is not a
        # count to diff -- it is the submission that dies, and CI is red on it, so it fails the gate
        # with or without --strict.
        icell="-"
        idetail=""
        if [ -n "$inputs_note" ]; then
            icell="SKIPPED"
            skip_prereq=$((skip_prereq + 1))
        else
            iargs=(--wdl-dir "$dir" --wf "$wf" --inputs-root "$inputs_root")
            [ "$TERRA" -eq 0 ] && iargs+=(--no-terra)
            iout="$("$SEM_PY" "$HERE/wdl_inputs_check.py" "${iargs[@]}" 2>&1)"; irc=$?
            iline=$(printf '%s\n' "$iout" | grep -F "GSVTK-INPUTS wf=$wf " | head -1 | sed 's/^[^ ]* //')
            wline=$(printf '%s\n' "$iout" | grep -F "GSVTK-WOMTOOL wf=$wf " | head -1 | sed 's/^[^ ]* //')
            if [ -z "$iline" ]; then
                # No verdict line means the checker itself failed. Report it as the tool's failure,
                # with its own output, rather than as a blank cell or a pass.
                icell="CHECK-FAILED"
                idetail="exited $irc without a verdict; its output:"
                status=1
            else
                istatus=$(printf '%s' "$iline" | sed -n 's/.* status=\([A-Za-z-]*\).*/\1/p')
                imissing=$(printf '%s' "$iline" | sed -n 's/.* missing=\([0-9]*\).*/\1/p')
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
                   || printf '%s\n' "$iout" | grep -q '^  DISAGREES'; then
                    idetail="$(printf '%s\n' "$iout" | grep -v '^GSVTK-')"
                fi
            fi
        fi
        printf '%-30s %-26s %-6s %-16s %-15s %s\n' "$wf" "$refsha" "$rc" "$inc" "$stale" "$icell"
        grep -oE "No such input [A-Za-z0-9_]+" "$log" 2>/dev/null | sort -u | sed 's/^/        stale binding: /'
        [ -n "$idetail" ] && printf '%s\n' "$idetail" | sed 's/^/        /'
        if [ "$rc" -ne 0 ] || { [ "$STRICT" -eq 1 ] && { [ "${inc:-0}" -ne 0 ] || [ "${stale:-0}" -ne 0 ]; }; }; then
            status=1
            [ "$rc" -ne 0 ] && echo "        hard error — full report: $log"
        fi
    done

    # One INPUTS-JSON and one WOMTOOL row per ref: both are prerequisites with a job to do, and the
    # gate must distinguish "ran and agreed with CI" from "had nothing to run on" from "could not
    # run", which a blank cell cannot.
    if [ -n "$inputs_note" ]; then
        printf '%-30s %-26s %s\n' INPUTS-JSON "$refsha" "SKIPPED — $inputs_note"
        echo "        no required-input question was answered for ${#WFS[@]} workflow(s) at $ref."
        echo "        Nothing here agrees or disagrees with CI's 'Test with WOMtool' step."
    fi
    if [ "$wt_skip" -gt 0 ]; then
        printf '%-30s %-26s %s\n' WOMTOOL "$refsha" \
            "SKIPPED — $wt_skip of ${#WFS[@]} workflow(s): ${wt_reason:-no womtool jar}"
        echo "        CI's own command was not reproduced for those workflows. A counted skip, never"
        echo "        a pass: --strict fails on it (WOMTOOL_JAR=<path>, or make setup for jinja2)."
    elif [ "$wt_fail" -gt 0 ]; then
        printf '%-30s %-26s %s\n' WOMTOOL "$refsha" "FAILED — womtool rejected ${wt_fail} pair(s) of $wt_run"
    elif [ "$wt_run" -gt 0 ]; then
        printf '%-30s %-26s %s\n' WOMTOOL "$refsha" "RUN=$wt_run FAIL=0 (womtool validate, CI's command)"
    elif [ "$wt_nopairs" -gt 0 ]; then
        printf '%-30s %-26s %s\n' WOMTOOL "$refsha" "NO-PAIRS — CI has no input JSON for these either"
    fi
    skip_prereq=$((skip_prereq + wt_skip))
    # Counted separately on purpose: a prerequisite skip fails --strict, a CI blind spot does not.
    skip_nopairs=$((skip_nopairs + ref_nopairs + wt_nopairs))

    # One SEMANTICS row per ref, not per workflow: these four rules read every WDL in the tree, and
    # a per-workflow row would imply the scan followed imports from one entrypoint, which it does not.
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

# Two refs is the whole point: the tree already carries findings (some deliberate, some inherited),
# so the question is never "is it zero" but "did you move it". Only a RISE is a finding; a fall is a
# fix, and a row that did not move says nothing and prints nothing.
if [ ${#SEM_LINES[@]} -ge 2 ]; then
    base_line="${SEM_LINES[0]}"; head_line="${SEM_LINES[$((${#SEM_LINES[@]} - 1))]}"
    for kv in $head_line; do
        k="${kv%%=*}"; want="${kv##*=}"
        base=""
        # Match the KEY, not a prefix of the line. The first version asked sed to strip `^$k=` and
        # took whatever was left, so for `WRITE-SCOPE` it captured `0 DEFINED-ONLY=2 PIPEFAIL=3 ...`
        # and the `((want - base))` below then evaluated that as arithmetic — which in bash 3.2 reads
        # `DEFINED-ONLY` as a subtraction of two unset names and dies with "DEFINED: unbound
        # variable". A field boundary has to be a boundary, not a substring.
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

# A layer that could not run is not a layer that passed. --strict fails on a PREREQUISITE skip (no
# womtool jar, no java, no jinja2, no WDL module) because that is the gate certifying a check it never
# ran -- the exact shape of the 7fbf1171 wrong answer. A workflow CI itself has no input JSON for is
# NOT a failure even here: CI is green on such a ref, and this gate's promise is parity with CI at a
# ref, not a stricter verdict. It is counted and printed instead, so the blank is never read as a pass.
if [ "$status" -eq 0 ] && [ "$STRICT" -eq 1 ] && [ "$skip_prereq" -gt 0 ]; then
    status=1
fi

if [ "$status" -eq 0 ] && [ "$STRICT" -eq 1 ]; then
    echo "no hard errors, and --strict is satisfied: no IncompleteCall, no stale binding, no missing"
    echo "required input in any CI-matched input JSON, no womtool failure, no rise in any SEMANTICS"
    echo "count, no layer skipped for a missing prerequisite, and every workflow named was present."
elif [ "$status" -eq 0 ]; then
    echo "no hard errors. Re-run with --strict to treat IncompleteCall / stale bindings / a rise in"
    echo "the SEMANTICS counts / any prerequisite skip as failure (today's gatk-sv carries a few of"
    echo "each on purpose, so --strict is a diff-against-baseline decision, not a default). A MISSING-"
    echo "INPUTS count is NOT in that list: a required input no CI input JSON binds is a hard finding"
    echo "either way, because CI is red on it."
else
    echo "gate FAILED: see the rows above; rc=2 is a hard error, IncompleteCall means a required"
    echo "             input is never bound (unlaunchable), stale binding means the callee has"
    echo "             dropped or renamed an input this caller still passes, and MISSING-INPUTS means"
    echo "             a required input is absent from the input JSONs CI renders -- which is what"
    echo "             'Required workflow input ... not specified' in CI says, and miniwdl cannot see."
fi
if [ "$skip_nopairs" -gt 0 ]; then
    echo "note: $skip_nopairs workflow-run(s) had no CI input JSON to check, so the required-input"
    echo "      question was not asked for them (see the NO-INPUT-JSON rows above)."
fi
exit "$status"
