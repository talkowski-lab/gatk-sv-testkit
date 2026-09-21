#!/usr/bin/env bash
# scripts/selftest.sh — the offline self-assertions, in a real shell script.
#
# Why a script and not the Makefile recipe it came from
# -----------------------------------------------------
# In a recipe, `$$($("$@") 2>&1)` is expanded by MAKE before bash ever sees it: make reads the
# inner `$("$@")` as a variable reference, expands it to nothing, and hands bash `out="$( 2>&1)"`.
# The command never runs, so `$?` is the substitution's own success -- every assertion reported
# "ok" no matter what it tested. Verified with a two-line Makefile: `check "..." false` printed ok.
# That made eight config-layer assertions (including the env > profile precedence rule, the one
# that decides which project gets billed) permanently vacuous, and nothing in the output hinted
# at it. Bash has no such trap, so the assertions live here now.
#
# Two rules this file keeps:
#   * canary() asserts that a command KNOWN TO FAIL is reported as failing. If the harness ever
#     becomes vacuous again, the gate fails on the canary instead of passing everything.
#   * every checker assertion needs a POSITIVE CONTROL: a number a real run must produce, not
#     merely "exit code was acceptable". Deleting a checker's detection used to print
#     "ok ... ran every jq block clean".
#
# Nothing here writes outside a temp dir, needs credentials, or touches the network.
# bash 3.2 compatible (macOS stock /bin/bash).

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
# The checker selftests need only stdlib; the clone-backed ones need the checkout. Pick up the
# project venv when it exists so `make test` behaves the same with or without one.
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi

ok=0; skip=0; fail=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"
printf 'GSVTK_PROJECT=profile-value-here\n' > "$TMP/prof.env"

# check DESC CMD... — passes when CMD exits 0.
check() {
    local desc="$1"; shift
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    if [ "$rc" -eq 0 ]; then
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    else
        fail=$((fail + 1)); printf '  FAIL  %s (exit %s)\n' "$desc" "$rc"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    fi
}

# canary -- the assertion that guards the assertions.
canary() {
    local out rc
    out="$(/bin/false 2>&1)"; rc=$?
    if [ "$rc" -ne 0 ]; then
        ok=$((ok + 1)); printf '  ok    the harness notices a failing command (canary)\n'
    else
        fail=$((fail + 1))
        printf '  FAIL  canary: /bin/false returned 0, so EVERY assertion in this file is meaningless\n'
        printf '        (this is the failure mode that made the old Makefile recipe fake)\n'
    fi
}

# covcheck DESC CMD... — a jq-plumbing scan whose block coverage must be complete.
covcheck() {
    local desc="$1"; shift
    local out rc pre exe
    out="$("$@" 2>&1)"; rc=$?
    pre="$(printf '%s' "$out" | sed -n 's/^blocks: \([0-9]\{1,\}\) in file.*/\1/p')"
    exe="$(printf '%s' "$out" | sed -n 's/^blocks: .*[^0-9]\([0-9]\{1,\}\) executed.*/\1/p')"
    if [ "$rc" -ne 0 ] && [ "$rc" -ne 1 ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s: only 0=clean or 1=findings is acceptable; a traceback is not "reported findings")\n' "$desc" "$rc"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    elif [ -z "$pre" ] || [ "$pre" != "$exe" ] || [ "$pre" -lt 10 ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s: block coverage %s/%s -- a verdict from a partial scan means nothing\n' "$desc" "$exe" "$pre"
        printf '%s\n' "$out" | head -4 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    %s (%s/%s blocks executed)\n' "$desc" "$exe" "$pre"
    fi
}

# stagecheck DESC WANT CMD... — a contract check that must compare WANT+ stage calls.
stagecheck() {
    local desc="$1" want="$2"; shift 2
    local out rc n
    out="$("$@" 2>&1)"; rc=$?
    n="$(printf '%s' "$out" | sed -n 's/.*(\([0-9]\{1,\}\) compared.*/\1/p')"
    if [ "$rc" -ne 0 ] && [ "$rc" -ne 1 ]; then
        fail=$((fail + 1)); printf '  FAIL  %s (exit %s: only 0-or-1 is acceptable)\n' "$desc" "$rc"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    elif [ -z "$n" ] || [ "$n" -lt "$want" ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s: compared only %s of >=%s stage calls (a parser blind spot is a FAIL)\n' "$desc" "$n" "$want"
        printf '%s\n' "$out" | head -4 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    %s (%s stage calls compared)\n' "$desc" "$n"
    fi
}

echo "selftest: config layer (interpreter: $PY)"
canary

# The loader swallows a failed resolver BY DESIGN (so --help works unconfigured), which means
# "sourcing returned 0" proves nothing. Assert the exported VALUE arrived.
check "kit/config.sh loads AND really exports the resolved values" \
    env GSVTK_CONFIG="$TMP/empty.env" bash -c \
    '. kit/config.sh; [ "${GSVTK_ZONE:-}" = us-central1-a ] && [ -n "${GSVTK_ROOT:-}" ]'

check "kit/config.sh warns (not silently empty) when the resolver is broken" \
    env GSVTK_PYTHON=/nonexistent-interpreter bash -c \
    '{ . kit/config.sh; } 2>&1 | grep -q "configuration layer produced no exports"'

check "gsvtk_default returns the fallback for an unconfigured key" \
    env GSVTK_CONFIG="$TMP/empty.env" bash -c \
    '. kit/config.sh; [ "$(gsvtk_default THIS_KEY_DOES_NOT_EXIST_42 fallback-value)" = "fallback-value" ]'

# Precedence, pinned in BOTH directions and against a POPULATED profile. Against the empty file
# the chain is replaced, so the old assertion could only prove "an env var beats nothing" -- a
# profile silently beating the environment (which changes whose project gets billed) passed it.
check "an exported variable beats a POPULATED profile (env > profile)" \
    env GSVTK_CONFIG="$TMP/prof.env" GSVTK_PROJECT=gate-proj \
    bash -c '[ "$(./kit/gsvtk-config get PROJECT)" = gate-proj ]'

check "the profile supplies the value when the environment does not (profile > default)" \
    env -u GSVTK_PROJECT GSVTK_CONFIG="$TMP/prof.env" \
    bash -c '[ "$(./kit/gsvtk-config get PROJECT)" = profile-value-here ]'

check "GSVTK_CONFIG replaces the profile chain instead of joining it" \
    env -u GSVTK_PROJECT GSVTK_CONFIG="$TMP/empty.env" bash -c \
    '! ./kit/gsvtk-config show | grep -q profile-value-here'

# gsvtk_work must create the subdir under the caller's GSVTK_WORK, not under wherever the
# checkout happens to live -- the scratch dir is where tens of GB land.
work_dir_check() {
    local p
    p="$(GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" bash -c \
        '. "'"$ROOT"'/kit/config.sh" >/dev/null 2>&1; gsvtk_work runs/x' 2>/dev/null)"
    if [ "$p" = "$TMP/work/runs/x" ] && [ -d "$p" ]; then
        ok=$((ok + 1)); printf '  ok    GSVTK_WORK is honoured and gsvtk_work creates subdirs\n'
    else
        fail=$((fail + 1))
        printf '  FAIL  GSVTK_WORK: gsvtk_work runs/x gave [%s], want [%s]\n' "$p" "$TMP/work/runs/x"
    fi
}
work_dir_check

# "required and missing" is the whole point of the config layer: exit 4, naming the key.
require_exits_4() {
    local out rc
    out="$(env -u GSVTK_PROJECT -u GSVTK_TERRA_NAMESPACE GSVTK_CONFIG="$TMP/empty.env" \
        ./kit/gsvtk-config require PROJECT 2>&1)"; rc=$?
    if [ "$rc" -eq 4 ] && printf '%s' "$out" | grep -q GSVTK_PROJECT; then
        ok=$((ok + 1)); printf '  ok    require PROJECT exits 4 and names GSVTK_PROJECT\n'
    else
        fail=$((fail + 1)); printf '  FAIL  require PROJECT (exit %s)\n%s\n' "$rc" "$out"
    fi
}
require_exits_4

doctor_exits_4() {
    local out rc
    out="$(env -u GSVTK_PROJECT -u GSVTK_TERRA_NAMESPACE GSVTK_CONFIG="$TMP/empty.env" \
        ./kit/gsvtk-config doctor 2>&1)"; rc=$?
    if [ "$rc" -eq 4 ] && printf '%s' "$out" | grep -q 'MISS *PROJECT'; then
        ok=$((ok + 1)); printf '  ok    doctor exits 4 and reports MISS for each unset required key\n'
    else
        fail=$((fail + 1)); printf '  FAIL  doctor (exit %s)\n%s\n' "$rc" "$out"
    fi
}
doctor_exits_4

check "a documented default still resolves with no profile (ZONE)" \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" ./kit/gsvtk-config require ZONE

# Provenance must name the FILE, not just "profile": the profile chain is last-wins, so the
# coordinates that decide what gets written can come from a file nobody is looking at.
check "show names the profile file a value came from" \
    env -u GSVTK_PROJECT GSVTK_CONFIG="$TMP/prof.env" \
    bash -c './kit/gsvtk-config show | grep "GSVTK_PROJECT" | grep -q "profile:.*prof.env"'

echo
echo "selftest: the two sv_shell checkers parse what they claim to parse"
check "svshell_jq_plumbing_scan finds every block shape and counts its own coverage" \
    "$PY" checks/svshell_jq_plumbing_scan.py --selftest
check "svshell_contract_check sees bare/quoted keys, both quote styles and every stage call" \
    "$PY" checks/svshell_contract_check.py --selftest

echo
echo "selftest: checkers and fetchers, against a local gatk-sv clone if present"
CK="$(./kit/gsvtk-config get GATK_SV_CHECKOUT 2>/dev/null || true)"
if [ ! -d "$CK" ]; then
    echo "  SKIP  the three clone-backed self-tests: GSVTK_GATK_SV_CHECKOUT is unset or not a"
    echo "        directory. Set it in testkit.env to run them (docs/config.md)."
    skip=$((skip + 3))
else
    printf '  (clone: %s)\n' "$CK"
    if command -v jq >/dev/null 2>&1; then
        covcheck "svshell_jq_plumbing_scan exercises every jq block in real gatk-sv" \
            "$PY" checks/svshell_jq_plumbing_scan.py --repo "$CK"
        printf '        (nulls it reports upstream are pre-existing by design; gate a change\n'
        printf '        with --compare-to <ref> -- docs/static-checks.md)\n'
    else
        echo "  SKIP  jq not on PATH: svshell_jq_plumbing_scan cannot execute the jq blocks"
        skip=$((skip + 1))
    fi
    stagecheck "svshell_contract_check compares the stage calls of real gatk-sv" 12 \
        "$PY" checks/svshell_contract_check.py --repo "$CK"
    printf '        its findings are a list to DIFF against a base ref, not a verdict\n'
    check "fetch_wdl --list materializes a ref's file list offline (git archive)" \
        "$PY" scripts/fetch_wdl.py --repo "$CK" --ref HEAD --list
fi

echo
printf 'selftest: %s ok, %s skipped, %s failed\n' "$ok" "$skip" "$fail"
[ "$fail" -eq 0 ]
