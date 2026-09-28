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
# Each ref is materialized to its OWN directory by scripts/fetch_wdl.py. That is not
# tidiness: imports resolve by filename within the directory, so a mixed tree resolves
# against the wrong version and reports a result that is neither the old bug nor the new
# one. One directory per ref, one ref per directory.
#
#   checks/wdl_gate.sh                      # origin/main vs $GSVTK_BRANCH, default workflows
#   checks/wdl_gate.sh v1.1.1 HEAD          # any two refs
#   checks/wdl_gate.sh --wf SVShell --wf ResolveCpxSvGenotyping HEAD
#   checks/wdl_gate.sh --strict HEAD        # nonzero exit if anything is unlaunchable
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
# activated, and a PATH-only lookup used to call the whole loop unavailable.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd -P)"
. "$ROOT/kit/config.sh"

REFS=()
WFS=()
STRICT=0
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
        -h|--help)       sed -n "2,31p" "$0" | sed "s/^# \{0,1\}//"; exit 0;;
        -*)              echo "unknown flag: $1 (see --help)" >&2; exit 2;;
        *)               REFS+=("$1"); shift;;
    esac
done

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
[ -n "${GSVTK_GATK_SV_CHECKOUT:-}" ] || { echo "GSVTK_GATK_SV_CHECKOUT is unset: which gatk-sv clone are we gating?" >&2; exit 4; }

# The semantics scan needs the WDL *module*, not the `miniwdl` command, and those can live in
# different environments: miniwdl resolved from a user install and an interpreter that never saw it.
# So ask each candidate the question rather than assuming the CLI's environment is the one to run in.
# An empty SEM_PY is not a silent skip: it prints a SKIPPED row and fails the gate, because "the
# guard never fired" and "the guard could not fire" otherwise print the same thing.
SEM_PY=""
for _cand in "${GSVTK_PYTHON:-}" "$ROOT/.venv/bin/python" "$(dirname "$MINIWDL")/python3" python3; do
    [ -n "$_cand" ] && "$_cand" -c 'import WDL' >/dev/null 2>&1 && { SEM_PY="$_cand"; break; }
done

OUT="$(gsvtk_work wdl-gate)"
status=0
delta_bad=0
SEM_LINES=()
printf '%-30s %-26s %-6s %-16s %s\n' WORKFLOW REF EXIT INCOMPLETECALL STALE-BINDINGS
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
    case "$dir" in "$OUT"/*) rm -rf "$dir" ;; esac
    "$GSVTK_PYTHON" "$ROOT/scripts/fetch_wdl.py" --ref "$ref" --dest "$dir" >/dev/null || {
        printf '%-30s %-26s %s\n' "(fetch)" "$ref" "FAILED — is $ref a real ref in ${GSVTK_GATK_SV_CHECKOUT}?"
        status=1; continue
    }
    sha=$(sed -n 's/^sha=//p' "$dir/.provenance" 2>/dev/null | cut -c1-8)
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
        printf '%-30s %-26s %-6s %-16s %s\n' "$wf" "${ref##*/}@$sha" "$rc" "$inc" "$stale"
        grep -oE "No such input [A-Za-z0-9_]+" "$log" 2>/dev/null | sort -u | sed 's/^/        stale binding: /'
        if [ "$rc" -ne 0 ] || { [ "$STRICT" -eq 1 ] && { [ "${inc:-0}" -ne 0 ] || [ "${stale:-0}" -ne 0 ]; }; }; then
            status=1
            [ "$rc" -ne 0 ] && echo "        hard error — full report: $log"
        fi
    done

    # One SEMANTICS row per ref, not per workflow: these four rules read every WDL in the tree, and
    # a per-workflow row would imply the scan followed imports from one entrypoint, which it does not.
    refsha="${ref##*/}@$sha"
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

if [ "$status" -eq 0 ] && [ "$STRICT" -eq 1 ]; then
    echo "no hard errors, and --strict is satisfied: no IncompleteCall, no stale binding, no rise in"
    echo "any SEMANTICS count, and every workflow named was actually present at the ref given."
elif [ "$status" -eq 0 ]; then
    echo "no hard errors. Re-run with --strict to treat IncompleteCall / stale bindings / a rise in"
    echo "the SEMANTICS counts as failure (today's gatk-sv carries a few of each on purpose, so"
    echo " --strict is a diff-against-baseline decision, not a default)."
else
    echo "gate FAILED: see the rows above; rc=2 is a hard error, IncompleteCall means a required"
    echo "             input is never bound (unlaunchable), stale binding means the callee has"
    echo "             dropped or renamed an input this caller still passes."
fi
exit "$status"
