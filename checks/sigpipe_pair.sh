#!/usr/bin/env bash
# Does the SIGPIPE guard in a producer do anything? Proven by running both arms over the SAME input.
#
# Why this exists (docs/gap-ledger.md A16). The row's pass condition is not "the guarded line works" -- it is that the
# UNGUARDED line visibly fails on the same input. A guard nobody ever saw fire is indistinguishable from a guard that
# was never needed, and the reason this repo cares is `pipefail`: under `set -o pipefail`, a producer killed by SIGPIPE
# while its consumer quits early makes the whole pipeline exit 141, so a `... | head -1` inside a checked script fails
# the script for doing exactly what was asked. That is a real defect class in the sv-shell scripts, which pipe long
# gsutil/jq chains into `head`.
#
# This is the offline half of A16. The compute half -- replaying one changed TASK on real inputs -- still needs a VM and
# still stops for approval. What is proven here is only the harness that makes such a replay a result rather than an
# anecdote: the pair must show 141 on arm A and 0 on arm B, and it must REFUSE when arm A does not fail, because a pair
# where nothing failed proves nothing about the guard.
#
#   checks/sigpipe_pair.sh              # run the pair on a synthetic stream
#   checks/sigpipe_pair.sh --selftest   # the pair, plus the mutant where arm A is guarded too (must refuse)
set -euo pipefail

LINES="${SIGPIPE_PAIR_LINES:-20000}"
rc_a=0 rc_b=0

# Arm A must be able to DIE of SIGPIPE, or this file grades nothing.
#
# A child inherits SIGPIPE's disposition, and a process that starts with SIG_IGN cannot be killed by a
# closed pipe: the producer's write fails with EPIPE instead, `seq` reports a write error, and arm A
# exits 1 on Linux (0 on macOS) rather than 141 -- so the pair prints VACUOUS and the phase goes red on
# a machine where the guard is perfectly fine. Measured: green from a normal shell (arm A 141), red
# under GitHub's runner, whose job shell is node and whose children inherit SIG_IGN from libuv. This is
# not a bug in the guard and the VACUOUS verdict is not wrong -- the disposition, not the code, is what
# changed. bash cannot undo an inherited ignore (`trap - PIPE` is a no-op for a signal ignored on entry:
# measured rc=0 with the trap and rc=0 without it), so the reset is made by the one interpreter present
# on both runners that will set SIG_DFL explicitly, and this script re-execs itself once underneath it.
# The marker keeps that non-recursive; perl absent leaves the honest VACUOUS in place.
if [ -z "${SIGPIPE_PAIR_SIGRESET:-}" ] && command -v perl >/dev/null 2>&1; then
    rc_probe=0
    ( set -o pipefail; seq 1 "$LINES" | head -n 1 >/dev/null ) 2>/dev/null || rc_probe=$?
    if [ "$rc_probe" -ne 141 ]; then
        export SIGPIPE_PAIR_SIGRESET=1
        # Through `bash`, not the file itself: this script is mode 644 and is always run as
        # `bash checks/sigpipe_pair.sh`, so exec'ing the path directly fails with 127 (measured).
        exec perl -e '$SIG{PIPE} = "DEFAULT"; exec @ARGV or do { print STDERR "sigpipe_pair: could not re-exec under a default SIGPIPE ($!)\n"; exit 127; };' -- bash "$0" ${1+"$@"}
    fi
fi

produce() {                       # a producer that keeps writing until somebody stops it
    seq 1 "$LINES"
}

run_pair() {
    set -o pipefail
    ( produce | head -n 1 >/dev/null ) && rc_a=0 || rc_a=$?
    # The guard: absorb 141 (SIGPIPE) and nothing else. A producer whose consumer walked away is not a failure of the
    # producer's work; any other status still propagates, which is why this is `== 141` and not `|| true`.
    ( { produce || { s=$?; [ "$s" -eq 141 ] || exit "$s"; }; } | head -n 1 >/dev/null ) && rc_b=0 || rc_b=$?
}

verdict() {                       # 0 = the guard is evidenced; 4 = the pair proves nothing
    if [ "$rc_a" -ne 141 ]; then
        echo "  VACUOUS: arm A (unguarded) exited $rc_a, not 141. This input never put the producer in a SIGPIPE" \
             " state, so nothing here is evidence about the guard -- raise LINES, or check that the consumer really" \
             " quits early (an unbuffered consumer can drain the pipe before the producer is killed)." \
             " And check the disposition: a producer that inherited SIG_IGN cannot die of a closed pipe at all,"
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
    # The mutant: arm A guarded too. Now nothing fails, and the harness must call that vacuous rather than pass it.
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
