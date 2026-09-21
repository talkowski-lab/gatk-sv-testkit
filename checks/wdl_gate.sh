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
# Needs miniwdl (pip install miniwdl) and a local gatk-sv clone. Both are checked up
# front, with the fix printed rather than a stack trace.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd -P)"
. "$ROOT/kit/config.sh"

REFS=()
WFS=()
STRICT=0
MINIWDL="${MINIWDL:-miniwdl}"

while [ $# -gt 0 ]; do
    case "$1" in
        --wf|--workflow) WFS+=("$2"); shift 2;;
        --strict)        STRICT=1; shift;;
        --miniwdl)       MINIWDL="$2"; shift 2;;
        -h|--help)       sed -n "2,22p" "$0" | sed "s/^# \{0,1\}//"; exit 0;;
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

command -v "$MINIWDL" >/dev/null 2>&1 || {
    echo "miniwdl not found as '$MINIWDL'. Install it (python -m pip install --user miniwdl)" >&2
    echo "or point MINIWDL at an existing install." >&2
    exit 3
}
[ -n "${GSVTK_GATK_SV_CHECKOUT:-}" ] || { echo "GSVTK_GATK_SV_CHECKOUT is unset: which gatk-sv clone are we gating?" >&2; exit 4; }

OUT="$(gsvtk_work wdl-gate)"
status=0
printf '%-30s %-16s %-6s %-16s %s\n' WORKFLOW REF EXIT INCOMPLETECALL STALE-BINDINGS
for ref in "${REFS[@]}"; do
    # A ref is user input that becomes a directory name under a `rm -rf`. `.` and `..` are
    # legal-ish git spellings and would point that wipe at the cache parent -- into the work
    # tree on Linux, where GNU rm obliges. Refuse them by name, and prove the target is a
    # child of the cache before removing anything.
    case "$ref" in
      ""|"."|".."|*..*)
        printf '%-30s %-16s %s\n' "(ref)" "${ref:-(empty)}" \
               "REJECTED — name a real ref (a branch, tag or sha), not '.' or '..'"
        status=1; continue;;
    esac
    dir="$OUT/${ref//\//-}"
    case "$dir" in "$OUT"/*) rm -rf "$dir" ;; esac
    "$GSVTK_PYTHON" "$ROOT/scripts/fetch_wdl.py" --ref "$ref" --dest "$dir" >/dev/null || {
        printf '%-30s %-16s %s\n' "(fetch)" "$ref" "FAILED — is $ref a real ref in ${GSVTK_GATK_SV_CHECKOUT}?"
        status=1; continue
    }
    sha=$(sed -n 's/^sha=//p' "$dir/.provenance" 2>/dev/null | cut -c1-8)
    for wf in "${WFS[@]}"; do
        # An absent workflow is a failure, not a blank cell. A typo'd name, or a name that only
        # exists on newer refs (v1.1.1 has no SVShell.wdl), used to contribute nothing to the exit
        # code even under --strict -- so the gate certified "no hard errors" having checked
        # nothing, which is the exact shape of wrong answer this script exists to catch.
        if [ ! -f "$dir/$wf.wdl" ]; then
            printf '%-30s %-16s %s\n' "$wf" "$ref" "ABSENT at $sha"
            status=1
            echo "        nothing was checked for $wf at $ref: wrong name, or it did not exist at"
            echo "        that ref. This is a failure, not a pass."
            continue
        fi
        log="$dir/$wf.check.txt"
        ( cd "$dir" && "$MINIWDL" check "$wf.wdl" >"$log" 2>&1 ); rc=$?
        inc=$(grep -c "IncompleteCall" "$log" 2>/dev/null || true)
        stale=$(grep -oE "No such input [A-Za-z0-9_]+" "$log" 2>/dev/null | sort -u | wc -l | tr -d ' ')
        printf '%-30s %-16s %-6s %-16s %s\n' "$wf" "${ref##*/}@$sha" "$rc" "$inc" "$stale"
        grep -oE "No such input [A-Za-z0-9_]+" "$log" 2>/dev/null | sort -u | sed 's/^/        stale binding: /'
        if [ "$rc" -ne 0 ] || { [ "$STRICT" -eq 1 ] && { [ "${inc:-0}" -ne 0 ] || [ "${stale:-0}" -ne 0 ]; }; }; then
            status=1
            [ "$rc" -ne 0 ] && echo "        hard error — full report: $log"
        fi
    done
done

if [ "$status" -eq 0 ] && [ "$STRICT" -eq 1 ]; then
    echo "no hard errors, and --strict is satisfied: no IncompleteCall, no stale binding, and"
    echo "every workflow named was actually present at the ref given."
elif [ "$status" -eq 0 ]; then
    echo "no hard errors. Re-run with --strict to treat IncompleteCall / stale bindings as failure"
    echo "(today's gatk-sv carries a few IncompleteCall warnings on purpose, so --strict is a"
    echo " diff-against-baseline decision, not a default)."
else
    echo "gate FAILED: see the rows above; rc=2 is a hard error, IncompleteCall means a required"
    echo "             input is never bound (unlaunchable), stale binding means the callee has"
    echo "             dropped or renamed an input this caller still passes."
fi
exit "$status"
