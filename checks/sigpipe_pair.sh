#!/usr/bin/env bash
# Shows that a SIGPIPE guard on a producer works by running guarded and unguarded arms over the same input.
#
# Under `set -o pipefail`, a producer killed by SIGPIPE when its consumer quits early (`... | head -1`) makes
# the pipeline exit 141. The pair must show 141 on the unguarded arm A and 0 on the guarded arm B; if arm A
# does not fail, the result is VACUOUS because nothing was tested.
#
#   checks/sigpipe_pair.sh             # run the pair on a synthetic stream
#   checks/sigpipe_pair.sh --selftest   # the pair, plus the mutant where arm A is guarded too (must refuse)
set -euo pipefail

LINES="${SIGPIPE_PAIR_LINES:-20000}"
rc_a=0 rc_b=0

# Arm A must be able to die of SIGPIPE. A process that inherits SIGPIPE as ignored (e.g. under GitHub
# Actions, where node's children inherit SIG_IGN) gets EPIPE instead and never exits 141. bash cannot
# undo an inherited ignore, so re-exec once under perl with SIGPIPE reset to default. The marker
# variable prevents recursion; without perl the result stays VACUOUS.
if [ -z "${SIGPIPE_PAIR_SIGRESET:-}" ] && command -v perl >/dev/null 2>&1; then
    rc_probe=0
    ( set -o pipefail; seq 1 "$LINES" | head -n 1 >/dev/null ) 2>/dev/null || rc_probe=$?
    if [ "$rc_probe" -ne 141 ]; then
        export SIGPIPE_PAIR_SIGRESET=1
        # Run through `bash`: the file is not executable.
        exec perl -e '$SIG{PIPE} = "DEFAULT"; exec @ARGV or do { print STDERR "sigpipe_pair: could not re-exec under a default SIGPIPE ($!)\n"; exit 127; };' -- bash "$0" ${1+"$@"}
    fi
fi

produce() {                       # a producer that keeps writing until somebody stops it
    seq 1 "$LINES"
}

run_pair() {
    set -o pipefail
    ( produce | head -n 1 >/dev/null ) && rc_a=0 || rc_a=$?
    # The guard: absorb 141 (SIGPIPE) only; any other status still propagates.
    ( { produce || { s=$?; [ "$s" -eq 141 ] || exit "$s"; }; } | head -n 1 >/dev/null ) && rc_b=0 || rc_b=$?
}

verdict() {                       # 0 = the guard is evidenced; 4 = the pair proves nothing
    if [ "$rc_a" -ne 141 ]; then
        echo "  VACUOUS: arm A (unguarded) exited $rc_a, not 141. This input never put the producer in a SIGPIPE" \
             " state, so nothing here is evidence about the guard -- raise LINES, or check that the consumer really" \
             " quits early (an unbuffered consumer can drain the pipe before the producer is killed)." \
             " And check the disposition: a producer that inherited SIG_IGN cannot die of a closed pipe at all," \
             " which is the case this file re-execs under perl to repair (see SIGPIPE_PAIR_SIGRESET)."
        return 4
    fi
    if [ "$rc_b" -ne 0 ]; then
        echo "  GUARD DOES NOT WORK: arm A exited $rc_a and the guarded arm exited $rc_b -- a guard that changes" \
             " nothing is a comment, not a fix."
        return 1
    fi
    echo "  PAIRED: unguarded pipeline rc=$rc_a (SIGPIPE), guarded pipeline rc=$rc_b, same $LINES-line producer."
    echo "  A guard is only evidence when its unguarded twin visibly fails; that is the condition this prints."
    return 0
}

if [ "${1:-}" = "--selftest" ]; then
    ok=0; bad=0
    run_pair
    if verdict && [ "$rc_a" = 141 ] && [ "$rc_b" = 0 ]; then ok=$((ok+1)); else bad=$((bad+1)); fi
    # Mutant: arm A guarded too, so nothing fails and the verdict must be VACUOUS.
    guarded_a() { { produce || { s=$?; [ "$s" -eq 141 ] || exit "$s"; }; }; }
    set -o pipefail
    ( guarded_a | head -n 1 >/dev/null ) && rc_a=0 || rc_a=$?
    if ! verdict && [ "$rc_a" -eq 0 ]; then
        echo "  ok    the mutant (arm A guarded) is refused as VACUOUS rather than reported as a pass"; ok=$((ok+1))
    else
        echo "  FAIL  the mutant slipped through: verdict passed with rc_a=$rc_a"; bad=$((bad+1))
    fi
    printf 'sigpipe_pair: %d ok, %d failed\n' "$ok" "$bad"
    [ "$bad" -eq 0 ] || exit 1
    exit 0
fi

run_pair
verdict
