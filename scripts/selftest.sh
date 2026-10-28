#!/usr/bin/env bash
# scripts/selftest.sh — offline self-assertions for the config layer, checkers, comparators and
# compute-path guards.
#
# These live in a script rather than a Makefile recipe because make expands `$(...)` inside a
# recipe before bash sees it, which silently turned every assertion into a pass.
#
# Two rules:
#   * canary() asserts that a command known to fail is reported as failing, so a harness that
#     goes vacuous fails the gate.
#   * every checker assertion pins a positive control (a number a real run must print), not
#     just an acceptable exit code.
#
# Writes only to a temp dir; no credentials or network. bash 3.2 compatible.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
# Use the project venv when present so `make test` behaves the same with or without one.
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi

ok=0; skip=0; fail=0; skiplist=""
# miniwdl is a dev dependency; legs that need it become named, counted skips when it is missing.
HAS_WDL=1
"$PY" -c 'import WDL' >/dev/null 2>&1 || HAS_WDL=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"
printf 'GSVTK_PROJECT=profile-value-here\n' > "$TMP/prof.env"

# --- declared vs reached: every assertion call site is recorded by line number, and the reporter
# checks that the declared and reached sets match whenever nothing skipped.
SELF="$ROOT/scripts/selftest.sh"
. "$ROOT/scripts/selftest.d/declared.sh"
GSVTK_DECL_FUNCS="check canary expect covcheck stagecheck work_dir_check require_exits_4 doctor_exits_4 probecount auditcount budget"
gsvtk_declared_init "$TMP"

# check DESC CMD... — passes when CMD exits 0.
check() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # the line that CALLED check(); one entry per call SITE, not per loop turn
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
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
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

# expect DESC WANT_RC NEEDLE [NEEDLE...] -- CMD...
# Asserts both the exit code (0 clean, 1 difference, 2 compared nothing) and that each NEEDLE
# appears in the output.
expect() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
    local desc="$1" want="$2"; shift 2
    local needles=()
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do needles+=("$1"); shift; done
    [ "$#" -gt 0 ] && shift
    local out rc missing=""
    out="$("$@" 2>&1)"; rc=$?
    local n
    # An assertion that names no needle cannot fail for the right reason.
    if [ "${#needles[@]}" -eq 0 ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s (expect was called with no NEEDLE: assert the number, not just the exit code)\n' "$desc"
        return
    fi
    for n in "${needles[@]}"; do
        grep -qF -- "$n" <<< "$out" || missing="${missing}[$n] "
    done
    if [ "$rc" -ne "$want" ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s, want %s%s)\n' "$desc" "$rc" "$want" \
            "${missing:+, and the output never says}"
        printf '%s\n' "$out" | head -5 | sed 's/^/          /'
    elif [ -n "$missing" ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s is right but the output never says %s)\n' "$desc" "$rc" "$missing"
        printf '%s\n' "$out" | head -5 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# covcheck DESC CMD... — a jq-plumbing scan whose block coverage must be complete.
covcheck() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
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

# budget DESC CAP_SECS NEEDLE [NEEDLE...] -- CMD...
# Like expect (want exit 0), plus a wall-clock ceiling: catches a doc scan that ends up executing a
# gate phase it was only meant to describe.
budget() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
    local desc="$1" cap="$2"; shift 2
    local needles=()
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do needles+=("$1"); shift; done
    [ "$#" -gt 0 ] && shift
    local out rc el missing="" n t0
    t0=$SECONDS
    out="$("$@" 2>&1)"; rc=$?
    el=$((SECONDS - t0))
    if [ "${#needles[@]}" -eq 0 ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s (budget was called with no NEEDLE: assert the number, not just the clock)\n' "$desc"
        return
    fi
    for n in "${needles[@]}"; do
        grep -qF -- "$n" <<< "$out" || missing="${missing}[$n] "
    done
    if [ "$rc" -ne 0 ]; then
        fail=$((fail + 1)); printf '  FAIL  %s (exit %s, want 0, after %ss)\n' "$desc" "$rc" "$el"
        printf '%s\n' "$out" | head -5 | sed 's/^/          /'
    elif [ "$el" -gt "$cap" ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s: %ss elapsed over a %ss budget — a scan that runs long is running something it was only meant to describe\n' "$desc" "$el" "$cap"
        printf '%s\n' "$out" | tail -3 | sed 's/^/          /'
    elif [ -n "$missing" ]; then
        fail=$((fail + 1)); printf '  FAIL  %s (%ss, right clock, but the output never says %s)\n' "$desc" "$el" "$missing"
        printf '%s\n' "$out" | head -5 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    %s (%ss elapsed, budget %ss)\n' "$desc" "$el" "$cap"
    fi
}

# stagecheck DESC WANT CMD... — a contract check that must compare WANT+ stage calls.
stagecheck() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
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

# The loader swallows a failed resolver by design, so assert the exported value, not the exit code.
check "kit/config.sh loads AND really exports the resolved values" \
    env GSVTK_CONFIG="$TMP/empty.env" bash -c \
    '. kit/config.sh; [ "${GSVTK_ZONE:-}" = us-central1-a ] && [ -n "${GSVTK_ROOT:-}" ]'

check "kit/config.sh warns (not silently empty) when the resolver is broken" \
    env GSVTK_PYTHON=/gsvtk-selftest-not-an-interpreter bash -c \
    '{ . kit/config.sh; } 2>&1 | grep -q "configuration layer produced no exports"'

check "gsvtk_default returns the fallback for an unconfigured key" \
    env GSVTK_CONFIG="$TMP/empty.env" bash -c \
    '. kit/config.sh; [ "$(gsvtk_default THIS_KEY_DOES_NOT_EXIST_42 fallback-value)" = "fallback-value" ]'

# Precedence in both directions, against a populated profile (this decides which project is billed).
check "an exported variable beats a POPULATED profile (env > profile)" \
    env GSVTK_CONFIG="$TMP/prof.env" GSVTK_PROJECT=gate-proj \
    bash -c '[ "$(./kit/gsvtk-config get PROJECT)" = gate-proj ]'

check "the profile supplies the value when the environment does not (profile > default)" \
    env -u GSVTK_PROJECT GSVTK_CONFIG="$TMP/prof.env" \
    bash -c '[ "$(./kit/gsvtk-config get PROJECT)" = profile-value-here ]'

check "GSVTK_CONFIG replaces the profile chain instead of joining it" \
    env -u GSVTK_PROJECT GSVTK_CONFIG="$TMP/empty.env" bash -c \
    '! ./kit/gsvtk-config show | grep -q profile-value-here'

# gsvtk_work must create subdirs under GSVTK_WORK (where tens of GB land), not under the checkout.
work_dir_check() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
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
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
    local out rc
    out="$(env -u GSVTK_PROJECT -u GSVTK_TERRA_NAMESPACE GSVTK_CONFIG="$TMP/empty.env" \
        ./kit/gsvtk-config require PROJECT 2>&1)"; rc=$?
    if [ "$rc" -eq 4 ] && grep -q GSVTK_PROJECT <<< "$out"; then
        ok=$((ok + 1)); printf '  ok    require PROJECT exits 4 and names GSVTK_PROJECT\n'
    else
        fail=$((fail + 1)); printf '  FAIL  require PROJECT (exit %s)\n%s\n' "$rc" "$out"
    fi
}
require_exits_4

doctor_exits_4() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
    local out rc
    out="$(env -u GSVTK_PROJECT -u GSVTK_TERRA_NAMESPACE GSVTK_CONFIG="$TMP/empty.env" \
        ./kit/gsvtk-config doctor 2>&1)"; rc=$?
    if [ "$rc" -eq 4 ] && grep -q 'MISS *PROJECT' <<< "$out"; then
        ok=$((ok + 1)); printf '  ok    doctor exits 4 and reports MISS for each unset required key\n'
    else
        fail=$((fail + 1)); printf '  FAIL  doctor (exit %s)\n%s\n' "$rc" "$out"
    fi
}
doctor_exits_4

check "a documented default still resolves with no profile (ZONE)" \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" ./kit/gsvtk-config require ZONE

# Provenance must name the profile file, since the profile chain is last-wins.
check "show names the profile file a value came from" \
    env -u GSVTK_PROJECT GSVTK_CONFIG="$TMP/prof.env" \
    bash -c './kit/gsvtk-config show | grep "GSVTK_PROJECT" | grep -q "profile:.*prof.env"'

# probecount DESC WANT CMD... -- runs scripts/probe_fixes.py and checks how many probes RAN.
# Skips do not count toward WANT.
probecount() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
    local desc="$1" want="$2"; shift 2
    local out rc tally run_n skip_n fail_n total_n
    out="$("$@" 2>&1)"; rc=$?
    tally="$(printf '%s' "$out" | sed -n 's/^probes: \([0-9]\{1,\}\) ok, \([0-9]\{1,\}\) skipped, \([0-9]\{1,\}\) failed$/\1 \2 \3/p' | tail -1)"
    # run + skipped must equal the declared total, so a probe cannot vanish without naming a prerequisite.
    total_n="$(printf '%s' "$out" | sed -n 's/^probes: \([0-9]\{1,\}\) defects.*/\1/p' | tail -1)"
    if [ -z "$tally" ]; then
        fail=$((fail + 1)); printf '  FAIL  %s: no "probes: N ok, M skipped, K failed" line (exit %s)\n' "$desc" "$rc"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
        return
    fi
    run_n="${tally%% *}"; local rest="${tally#* }"; skip_n="${rest%% *}"; fail_n="${rest##* }"
    if [ "$fail_n" != "0" ]; then
        # Checked first: a failing probe is the more actionable message.
        fail=$((fail + 1)); printf '  FAIL  %s: %s probe(s) FAILED\n' "$desc" "$fail_n"
        printf '%s\n' "$out" | grep -E '^  (FAIL|SKIP)' | head -6 | sed 's/^/          /'
    elif [ -n "$total_n" ] && [ $((run_n + skip_n)) -ne "$total_n" ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s: %s ran + %s skipped is not the %s probes the tool says it HAS — a probe stopped\n' \
               "$desc" "$run_n" "$skip_n" "$total_n"
        printf '        running without naming a prerequisite, which is the one thing this leg is for.\n'
        printf '%s\n' "$out" | grep -E '^  (ok|SKIP|FAIL)' | head -8 | sed 's/^/          /'
    elif [ "$run_n" -lt "$want" ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s: only %s of >=%s probes RAN (%s skipped) -- a skipped probe proves nothing\n' \
               "$desc" "$run_n" "$want" "$skip_n"
        printf '        the SKIP lines above name what is missing. requirements.txt covers the Terra\n'
        printf '        calls; requirements-dev.txt adds miniwdl and flake8 (docs/setup.md)\n'
        printf '%s\n' "$out" | grep -E '^  (ok|SKIP|FAIL)' | head -8 | sed 's/^/          /'
    else
        ok=$((ok + 1))
        printf '  ok    %s (%s ok, %s skipped)\n' "$desc" "$run_n" "$skip_n"
    fi
}

echo
echo "selftest: the checkers parse what they claim to parse"
check "svshell_jq_plumbing_scan finds every block shape and counts its own coverage" \
    "$PY" checks/svshell_jq_plumbing_scan.py --selftest
check "svshell_contract_check sees bare/quoted keys, both quote styles and every stage call" \
    "$PY" checks/svshell_contract_check.py --selftest
# Without miniwdl these tools parse nothing, so report a SKIP that names the install, never an ok.
if "$PY" -c 'import WDL' >/dev/null 2>&1; then
    gsvtk_guard_sites "no-wdl"              # first statement of the block the two SKIPs below withhold
    check "wdl_semantics fires on each of its four rules and stays silent on each control" \
        "$PY" checks/wdl_semantics.py --selftest
    check "build_inputs takes its key prefix from the WDL you passed, not from a name in the file" \
        "$PY" replay/build_inputs.py --selftest
    gsvtk_guard_sites_end "no-wdl"          # last statement of that block; the extent is this range
else
    echo "  SKIP  wdl_semantics --selftest: this interpreter cannot import WDL."
    echo "        Install miniwdl (make setup, or $PY -m pip install miniwdl) to run it."
    skip=$((skip + 1)); skiplist="$skiplist wdl_semantics-selftest(no-WDL)"
    echo "  SKIP  build_inputs --selftest: same reason — its fixtures ARE WDL documents, so the check"
    echo "        cannot run without the parser. Install miniwdl (make setup) to run it."
    skip=$((skip + 1)); skiplist="$skiplist build_inputs-selftest(no-WDL)"
    gsvtk_note_skip "no-wdl"                # the two sites above are claimed, by range, not by opinion
fi

echo
echo "selftest: checkers and fetchers, against a local gatk-sv clone if present"
CK="$(./kit/gsvtk-config get GATK_SV_CHECKOUT 2>/dev/null || true)"
if [ ! -d "$CK" ]; then
    echo "  SKIP  the four clone-backed self-tests: GSVTK_GATK_SV_CHECKOUT is unset or not a"
    echo "        directory. Set it in testkit.env to run them (docs/config.md)."
    # Four checks: jq scan, contract check, fetch_wdl and the wdl_semantics summary.
    skip=$((skip + 4)); skiplist="$skiplist clone-backed(x4)"
    # The withheld call sites are the gsvtk_guard_sites region below.
    gsvtk_note_skip "clone-backed"
else
    gsvtk_guard_sites "clone-backed"          # first statement of the block the SKIP withholds
    printf '  (clone: %s)\n' "$CK"
    if command -v jq >/dev/null 2>&1; then
        gsvtk_guard_sites "no-jq"             # nested: jq missing withholds covcheck ALONE, clone or no clone
        covcheck "svshell_jq_plumbing_scan exercises every jq block in real gatk-sv" \
            "$PY" checks/svshell_jq_plumbing_scan.py --repo "$CK"
        gsvtk_guard_sites_end "no-jq"
        printf '        (nulls it reports upstream are pre-existing by design; gate a change\n'
        printf '        with --compare-to <ref> -- docs/static-checks.md)\n'
    else
        echo "  SKIP  jq not on PATH: svshell_jq_plumbing_scan cannot execute the jq blocks"
        skip=$((skip + 1)); skiplist="$skiplist jq-plumbing-scan(no-jq)"
        gsvtk_note_skip "no-jq"
    fi
    stagecheck "svshell_contract_check compares the stage calls of real gatk-sv" 12 \
        "$PY" checks/svshell_contract_check.py --repo "$CK"
    printf '        its findings are a list to DIFF against a base ref, not a verdict\n'
    check "fetch_wdl --list materializes a ref's file list offline (git archive)" \
        "$PY" scripts/fetch_wdl.py --repo "$CK" --ref HEAD --list
    # Positive control on a real tree: counts are upstream's and not pinned, but no file may fail to load.
    if "$PY" -c 'import WDL' >/dev/null 2>&1; then
        # GSVTK_SEMANTICS_JOBS caps miniwdl parse workers (default min(8, cpus), a few hundred MB
        # each); set it to 1 on a memory-constrained machine.
        semsum="$("$PY" checks/wdl_semantics.py --dir "$CK" --summary-only \
            --jobs "${GSVTK_SEMANTICS_JOBS:-0}" 2>/dev/null)"
        if grep -qE '^WRITE-SCOPE=[0-9]+ DEFINED-ONLY=[0-9]+ PIPEFAIL=[0-9]+ SHELL-SYNTAX=[0-9]+ LOAD-FAILURES=0$' <<< "$semsum"; then
            ok=$((ok + 1)); printf '  ok    wdl_semantics scans real gatk-sv without losing a file\n'
            printf '        %s\n        (these are upstream counts: diff two refs, never read one)\n' "$semsum"
        else
            fail=$((fail + 1)); printf '  FAIL  wdl_semantics on the real clone: %s\n' "${semsum:-no output}"
        fi
    else
        echo "  SKIP  wdl_semantics against the clone: no WDL module for this interpreter"
        skip=$((skip + 1)); skiplist="$skiplist wdl-semantics-clone(no-WDL)"
    fi
    gsvtk_guard_sites_end "clone-backed"      # last statement of the block; the extent is now this range
fi

echo
echo "selftest: the agent skill in skills/ still describes this checkout"
# The skill tells an agent which modes POST; check_skill.py verifies it against ./gsvtk and
# includes a vacuity control. No count in the label: check_skill prints its own.
check "scripts/check_skill.py verifies the shipped skill against ./gsvtk" \
    "$PY" scripts/check_skill.py
printf '        (frontmatter, --read-only on every agent command, prose-vs-whitelist refusals EXECUTED,\n'
printf '        no home paths -- and a control proving the comparisons are not vacuous)\n'

# Catches doc fences that swallow prose and relative links to missing files.
check "scripts/check_docs.py finds broken fences and dead relative links" \
    "$PY" scripts/check_docs.py

# The README's compatibility table is generated from compat.json; a hand edit or a stale copy fails here.
check "README's compatibility table matches compat.json" \
    "$PY" scripts/check_compat.py table --check README.md

# auditcount DESC WANT_HITS WANT_FILES CMD... -- an audit run whose exact hit and file counts must match.
auditcount() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
    local desc="$1" want_h="$2" want_f="$3"; shift 3
    local out rc h f
    out="$("$@" 2>&1)"; rc=$?
    h="$(printf '%s' "$out" | sed -n 's/^audit: tally: hits=\([0-9]\{1,\}\) files=[0-9]*$/\1/p')"
    f="$(printf '%s' "$out" | sed -n 's/^audit: tally: hits=[0-9]* files=\([0-9]\{1,\}\)$/\1/p')"
    printf '%s\n' "$out" > "$TMP/audit.out"
    if [ "$rc" -ne 1 ] || [ -z "$h" ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s: wanted exit 1 with a tally line, got exit %s\n' "$desc" "$rc"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    elif [ "$h" != "$want_h" ] || [ "$f" != "$want_f" ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s: %s hit(s) in %s file(s), wanted %s in %s\n' "$desc" "$h" "$f" "$want_h" "$want_f"
        printf '%s\n' "$out" | command grep 'HIT' | head -8 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    %s (%s hit(s) in %s file(s))\n' "$desc" "$h" "$f"
    fi
}

echo
echo "selftest: scripts/audit.py detects what it claims, and no more"
# Fixture secrets are assembled from pieces so this file never contains a literal one (the audit
# has no exempt files).
A="$TMP/auditrepo"; mkdir -p "$A/onlyclean"
SA="12345678901""2-comp""ute@developer.gserv""iceaccount.com"
KEYHDR="-----BEGIN OPENSSH PR""IVATE KEY-----"
HOMELEAK="/home/""alice/work"
PLACEHOLDER_SA="<project-number>-comp""ute@developer.gserv""iceaccount.com"
COORD="squeamish-ossifrage"          # a fake workspace name: nobody's real coordinate
SEG="ossifrage"                      # the piece of it that leaks on its own

# Lines a published doc may contain (placeholders, public paths, numbers): must produce zero hits.
# No backticks: inside an unquoted heredoc they are command substitution.
cat > "$A/clean.md" <<EOF
- $PLACEHOLDER_SA
us.gcr.io/YOUR_PROJECT/YOUR_NAMESPACE/gatk-sv
cd /Users/you/repos/gatk-sv && make test
SHA="00000000000000000000"
totalMemory()=15032385536, PEQ = 34.743558552260145
EOF
cp "$A/clean.md" "$A/onlyclean/clean.md"

printf '%s\n%s\n%s/x\n' "$SA" "$KEYHDR" "$HOMELEAK"            > "$A/leak-shapes.txt"
printf 'the arm published %s:latest\n' "$COORD"                 > "$A/leak-personal.md"
# A documented placeholder sharing a line with a real leak must not hide it.
printf 'cd /Users/you/x && docker push %s:latest\n' "$COORD"     > "$A/mixed.md"
printf 'binary %s then a NUL:\0end\n' "$COORD"                   > "$A/blob.bin"
printf 'no file is exempt: %s/secret\n' "$HOMELEAK"              > "$A/Makefile"
# A leak carrying only a segment of the coordinate (e.g. a runs_<tag>/ path).
printf 'metadata lived in runs_%s/ for that arm\n' "$SEG"        > "$A/segment.md"
printf 'GSVTK_TERRA_WORKSPACE=%s\n' "$COORD"                     > "$TMP/vals.env"

check "scripts/audit.py's shipped shapes each fire, and documented placeholders do not" \
    "$PY" scripts/audit.py --selftest
check "documented placeholders and public numbers produce zero hits" \
    "$PY" scripts/audit.py --root "$A/onlyclean" --values "$TMP/vals.env" --no-git
# 8 hits in 6 files: three shapes, the personal value in markdown, mixed and binary files, the
# segment-only leak, and a file named Makefile.
auditcount "the audit finds every planted leak" 8 6 \
    "$PY" scripts/audit.py --root "$A" --values "$TMP/vals.env" --no-git

# Each property asserted individually, not just via the count.
for want in 'mixed.md' 'personal: GSVTK_TERRA_WORKSPACE' 'Makefile' 'blob.bin' 'segment.md'; do
    case "$want" in
        mixed.md)                     why="a documented placeholder on the same line cannot hide a leak";;
        'personal: GSVTK_TERRA_WORKSPACE') why="a derived value is labelled with the key it came from";;
        Makefile)                     why="no file is exempt -- a leak in a Makefile is reported";;
        blob.bin)                     why="a binary file is reported by filename, not skipped";;
        segment.md)                   why="a leak carrying only a SEGMENT of a coordinate still fires";;
    esac
    if command grep -q "$want" "$TMP/audit.out"; then
        ok=$((ok + 1)); printf '  ok    %s\n' "$why"
    else
        fail=$((fail + 1)); printf '  FAIL  %s (no %s in the output)\n' "$why" "$want"
    fi
done

# A waiver must be announced, and waiving the coordinate leaves its segments live (still 8 in 6).
printf 'waive %s\n' "$COORD" > "$A/audit.local.txt"
auditcount "waiving a coordinate alone does not waive its segments" 8 6 \
    "$PY" scripts/audit.py --root "$A" --values "$TMP/vals.env" --no-git
if command grep -q 'waived as public' "$TMP/audit.out"; then
    ok=$((ok + 1)); printf '  ok    a waived value is announced in the header\n'
else
    fail=$((fail + 1)); printf '  FAIL  the waiver was applied but never announced\n'
fi

# Waiving the segments too leaves only the shape hits.
printf 'waive %s\nwaive squeamish\nwaive %s\n' "$COORD" "$SEG" > "$A/audit.local.txt"
auditcount "waiving the coordinate AND its segments leaves only the shape hits" 4 2 \
    "$PY" scripts/audit.py --root "$A" --values "$TMP/vals.env" --no-git
if command grep -q '3 value(s) waived as public' "$TMP/audit.out"; then
    ok=$((ok + 1)); printf '  ok    the header counts every waiver, not just the first\n'
else
    fail=$((fail + 1)); printf '  FAIL  three waivers were applied and the header does not say 3\n'
fi
rm -f "$A/audit.local.txt"

# ------------------------------------------------------------------ the gate itself
echo
echo "selftest: the publish-scope audit — the object store, not only the working set"
# `make audit` grades the working tree; this grades every reachable blob, since a later scrub does
# not unpublish a committed leak. Control: a coordinate in a file deleted by a later commit is found.
check "scripts/audit_history.py grades all four exposure classes, the message scan, and its control" \
    "$PY" scripts/audit_history.py --selftest
check "no credential shape and no coordinate of THIS machine at HEAD or in the index" \
    "$PY" scripts/audit_history.py
printf '        (HISTORY is reported, not failed, in this mode. `make audit-history PUBLISH=1` is the\n'
printf '        pre-push gate; docs/static-checks.md lists what it reports here today and why each is\n'
printf '        acceptable rather than quietly waived)\n'

echo
echo "the gate itself: what 'make test' claims to run is what it runs"
# The docs say `make test` runs the publish audit; assert it does.
check "make test runs the publish audit (the docs claim it; a leak shipped when it did not)" \
    sh -c "grep -A 9 '^test:' Makefile | grep -q -- '--no-print-directory audit'"

# Drive the real selftest target. Both legs clear MAKEFLAGS so a SELFTEST_PHASES override from an
# outer make does not leak into the nested one. Without make, they are a named skip.
if command -v make >/dev/null 2>&1; then
    gsvtk_guard_sites "no-make"              # first statement of the block the SKIP below withholds
    # Leg one: red / green / red stand-ins. Later phases must run and both reds be named. `make`
    # exits 2 on failure, so the leg maps the verdict to 0/1 itself.
    LD="$TMP/phase-loop"; mkdir -p "$LD"
    printf '#!/bin/sh\necho LOOP-RAN-FIRST\nexit 1\n'  > "$LD/red1.sh"
    printf '#!/bin/sh\necho LOOP-RAN-SECOND\nexit 0\n' > "$LD/green.sh"
    printf '#!/bin/sh\necho LOOP-RAN-THIRD\nexit 4\n'  > "$LD/red2.sh"
    chmod +x "$LD/red1.sh" "$LD/green.sh" "$LD/red2.sh"
    expect "make selftest runs every phase and names every red one (it used to stop at the first)" 1 \
        'LOOP-RAN-SECOND' 'LOOP-RAN-THIRD' '2 of 3 red: red1 red2' -- \
        env -u MAKEFLAGS PHASES="red1:$LD/red1.sh green:$LD/green.sh red2:$LD/red2.sh" \
        bash -c 'out="$(make --no-print-directory selftest PYTHON=true SELFTEST_PHASES="$PHASES" 2>&1)"; rc=$?; echo "$out"; [ $rc -eq 0 ]'
    # Leg two: graded against the phase list make actually parses (`make -n`). Every phase must also
    # source declared.sh and call gsvtk_declared_report, or the declared/reached pair cannot see it.
    # declared.sh is the shared helper, not a phase, and is excluded by name.
    expect "every selftest.d phase is wired into the selftest target, and every one adopted by the declared/reached pair (a dropped entry leaves the gate silently; an unadopted phase leaves the pair blind to it)" 0 \
        'ALL-PHASES-WIRED' 'ALL-PHASES-ADOPTED' -- \
        env -u MAKEFLAGS sh -c 'list="$(make -n selftest 2>/dev/null)"
            for s in scripts/selftest.d/*.sh; do
                case "$s" in scripts/selftest.d/declared.sh) continue ;; esac
                case "$list" in *"$s"*) ;; *) echo "NOT-WIRED $s"; exit 1 ;; esac
                grep -E "^[[:space:]]*([.]|source)[[:space:]]" "$s" | grep -q "declared[.]sh" || { echo "NOT-ADOPTED $s does not source declared.sh"; exit 1; }
                grep -qE "^[[:space:]]*gsvtk_declared_report[[:space:]]" "$s" || { echo "NOT-ADOPTED $s never calls gsvtk_declared_report"; exit 1; }
            done
            case "$list" in *"scripts/selftest.sh"*) ;; *) echo "NOT-WIRED scripts/selftest.sh"; exit 1 ;; esac
            echo ALL-PHASES-WIRED
            echo ALL-PHASES-ADOPTED'
    gsvtk_guard_sites_end "no-make"         # last statement of the block; the extent is now this range
else
    echo "  SKIP  the two selftest-target legs: no make on PATH, so the gate's own loop cannot be run."
    echo "        Install make (Xcode Command Line Tools on macOS) to run them."
    skip=$((skip + 1)); skiplist="$skiplist selftest-target-legs(no-make)"
    gsvtk_note_skip "no-make"
fi

# --------------------------------------------------------------------------- comparators
echo
echo "comparators: every tool pinned to a synthetic pair whose differences are written down"
# Each comparator against a small synthetic pair whose expected numbers are documented in
# compare/make_fixtures.py.
CMP="$TMP/cmp-fixtures"
if "$PY" compare/make_fixtures.py "$CMP" >"$TMP/fixtures.out" 2>&1; then
    ok=$((ok + 1)); printf '  ok    compare/make_fixtures.py writes the fixture pair\n'
else
    fail=$((fail + 1)); printf '  FAIL  compare/make_fixtures.py could not write its fixtures\n'
    head -6 "$TMP/fixtures.out" | sed 's/^/          /'
fi
if [ -d "$CMP" ]; then
    expect "table_diff: permuted columns and re-sorted rows are NOT a difference (15 cells, 0 deltas),
             and the duplicate SV_1 key is disclosed rather than folded" 0 \
        'MATCH 15, DELTA 0' 'duplicate join keys: A 1, R 1' -- \
        "$PY" compare/table_diff.py "$CMP/tbl_a.tsv" "$CMP/tbl_reordered.tsv" --key vid --label-a A --label-b R
    expect "table_diff: the planted delta is found, and the column only B has is reported, not diffed" 1 \
        'DELTA 1' 'ONE_SIDED columns (not diffed): pe_min_support' -- \
        "$PY" compare/table_diff.py "$CMP/tbl_a.tsv" "$CMP/tbl_b.tsv" --key vid
    expect "table_diff: two files with no key in common exit 2, they do not report a clean comparison" 2 \
        'FATAL: 0 shared vid values, so nothing was compared' -- \
        "$PY" compare/table_diff.py "$CMP/tbl_a.tsv" "$CMP/tbl_disjoint.tsv" --key vid
    expect "table_diff: gatk-sv-profile's gzipped same-schema twins diff cell by cell (an integer column
             and a float column in one table)" 1 'mean_gq' 'DELTA 1' -- \
        "$PY" compare/table_diff.py "$CMP/profile_a.tsv.gz" "$CMP/profile_b.tsv.gz" --key metric
    expect "matrix_diff: a file compared to itself is exact, and the cell count is the whole grid" 0 \
        'inside tolerance 20 (100.0000%)' -- \
        "$PY" compare/matrix_diff.py "$CMP/wide_a.tsv" "$CMP/wide_a.tsv"
    expect "matrix_diff: shuffled sample columns still find exactly the one moved cell, and a cohort
             change is reported as a cohort change" 1 'shared 4, baseline-only 1, new-only 1' \
        'inside tolerance 14 (93.3333%)' -- \
        "$PY" compare/matrix_diff.py "$CMP/wide_a.tsv" "$CMP/wide_b.tsv"
    expect "matrix_diff: a grid that diverges mid-file is refused, not half-compared" 2 'diverged' -- \
        "$PY" compare/matrix_diff.py "$CMP/wide_a.tsv" "$CMP/wide_c.tsv"
    expect "vcf_paired_diff: permuted sample columns give exact 100%, and a repeated VID is not paired
             against a different variant (this bug produced 3 phantom cells before the fixture caught it)" \
        0 'exact 100.0000%' 'were NOT compared' -- \
        "$PY" compare/vcf_paired_diff.py "$CMP/geno_a.vcf" "$CMP/geno_same.vcf" --field FORMAT:GQ
    expect "vcf_paired_diff: the planted GQ, RD_CN and PCC moves are each found" 1 \
        'FORMAT:GQ=DELTA' 'FORMAT:RD_CN=DELTA' 'FORMAT:PCC=DELTA' -- \
        "$PY" compare/vcf_paired_diff.py "$CMP/geno_a.vcf" "$CMP/geno_b.vcf" \
        --field FORMAT:GQ --field FORMAT:RD_CN --field FORMAT:PCC
    expect "vcf_paired_diff: a Float FORMAT field is not tested with an integer tolerance" 1 \
        'values are NOT integers here' -- \
        "$PY" compare/vcf_paired_diff.py "$CMP/geno_a.vcf" "$CMP/geno_b.vcf" --field FORMAT:PCC
    expect "site_set_diff: the dropped INV and the added BND are a set difference with the strata named" 1 \
        'shared keys 4  baseline-only 1  new-only 1' -- \
        "$PY" compare/site_set_diff.py "$CMP/geno_a.vcf" "$CMP/geno_b.vcf"
    expect "lineset_diff: sample lists that differ by one name on each side are reported by name" 1 \
        'shared 2, only in A 1, only in B 1' 'SAMPLE2' 'SAMPLE4' -- \
        "$PY" compare/lineset_diff.py "$CMP/list_a.txt" "$CMP/list_b.txt"
    expect "lineset_diff: chr20 vs 20 is a difference UNTIL normalization is asked for, and the tool
             says which it did either way" 1 'normalization: NONE' -- \
        "$PY" compare/lineset_diff.py "$CMP/list_chr.txt" "$CMP/list_nochr.txt"
    expect "lineset_diff: the same pair with --normalize chr is equal, and says so" 0 \
        'normalization: chr' -- \
        "$PY" compare/lineset_diff.py "$CMP/list_chr.txt" "$CMP/list_nochr.txt" --normalize chr
    expect "json_diff: an inputs.json pair differing in two paths, one number and one added key is
             counted per kind" 1 '1 added' '1 numeric' '2 value/type' -- \
        "$PY" compare/json_diff.py "$CMP/inputs_a.json" "$CMP/inputs_b.json"
    expect "json_diff: --fold-paths removes the path differences and ONLY those" 1 \
        '2 difference(s)' -- \
        "$PY" compare/json_diff.py "$CMP/inputs_a.json" "$CMP/inputs_b.json" --fold-paths
    expect "tar_manifest: a same-size byte change is NOT claimed as identical when --hash was not given,
             and the output says so instead of implying the bundles match" 1 \
        'byte identity not checked' -- \
        "$PY" compare/tar_manifest.py "$CMP/bundle_a.tar.gz" "$CMP/bundle_b.tar.gz"
    expect "tar_manifest: with --hash the same-size byte change is found" 1 '1 byte-differing' -- \
        "$PY" compare/tar_manifest.py "$CMP/bundle_a.tar.gz" "$CMP/bundle_b.tar.gz" --hash
    # Shared VIDs and samples but every cell GQ=. must exit 2 (nothing compared), not 1 (a difference).
    GQ0="$TMP/gq-nocells"; mkdir -p "$GQ0"
    printf '##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS1\tS2\n' \
        > "$GQ0/a.vcf"
    printf '1\t100\tSITE1\tA\tT\t.\t.\t.\tGT:GQ\t./.:.\t./.:.\n' >> "$GQ0/a.vcf"
    printf '2\t200\tSITE2\tG\tC\t.\t.\t.\tGT:GQ\t./.:.\t./.:.\n' >> "$GQ0/a.vcf"
    cp "$GQ0/a.vcf" "$GQ0/b.vcf"
    expect "vcf_paired_diff: shared VIDs and shared samples, but every shared cell is GQ=. — refused
             with the nothing-compared exit code, not measured as a difference" 2 \
        'NOTHING COMPARABLE' 'FORMAT:GQ=NOTHING' -- \
        "$PY" compare/vcf_paired_diff.py "$GQ0/a.vcf" "$GQ0/b.vcf" --field FORMAT:GQ --json "$GQ0/one.json"
    check 'and that refusal prints no traceback' \
        sh -c 'out="$("$@" 2>&1)"; case "$out" in *Traceback*) printf "%s\n" "$out" | tail -4; exit 1 ;; esac' \
        sh "$PY" compare/vcf_paired_diff.py "$GQ0/a.vcf" "$GQ0/b.vcf" --field FORMAT:GQ
    check 'the artifact for that refusal says compared_something:false' \
        sh -c 'grep -q "compared_something.: false" "$1"' sh "$GQ0/one.json"
    expect "gq_scale_compare: a Float field is binned finely enough to report its real distribution
             (it used to print mean 0.00 / zero 100%)" 0 'PCC=1/1000' -- \
        "$PY" compare/gq_scale_compare.py "$CMP/geno_a.vcf" "$CMP/geno_b.vcf" --fields GQ,PCC
    expect "gq_scale_compare: an uncompressed VCF is read, not a traceback" 0 'GQ=1/1' -- \
        "$PY" compare/gq_scale_compare.py "$CMP/geno_a.vcf" "$CMP/geno_b.vcf" --fields GQ
    # vcf_body_diff, gd_calls_diff and sample_census: header-only VCF changes, panel-vs-case GD
    # changes, and a merged evidence table that lost a sample.
    expect "vcf_body_diff: identical record bodies under a rewritten header and a different container
             ARE identical, and every volatile line the rule ignored is listed on the page" 0 \
        'BODY-IDENTICAL' 'HEADERS-VOLATILE-ONLY' 'bcftools_viewCommand' -- \
        "$PY" compare/vcf_body_diff.py "$CMP/body_a.vcf" "$CMP/body_b.vcf.gz"
    expect "vcf_body_diff: a recomputed INFO/AC IS a record-body difference, named by INFO key and by
             record rather than printed as two long INFO strings" 1 \
        'BODY-DIFFERS' 'INFO{AC}' '20/2000/SV_2' -- \
        "$PY" compare/vcf_body_diff.py "$CMP/body_a.vcf" "$CMP/body_c.vcf"
    expect "vcf_body_diff: a header-only VCF is NOTHING COMPARED at exit 2, never an agreement with
             the other side" 2 \
        'NOTHING COMPARED' -- \
        "$PY" compare/vcf_body_diff.py "$CMP/body_a.vcf" "$CMP/body_empty.vcf"
    expect "vcf_body_diff --selftest pins the order rule, the cohort rule and every refusal it claims" 0 \
        'all selftest assertions passed' -- "$PY" compare/vcf_body_diff.py --selftest
    expect "gd_calls_diff: the panel moved and the case did not — exit 1, and the CASE line says 0" 1 \
        'CASE NA12878' 'cn_state changes 0' '-> CLEAN' -- \
        "$PY" compare/gd_calls_diff.py "$CMP/gd_a.tsv.gz" "$CMP/gd_b.tsv.gz" --case-sample NA12878
    expect "gd_calls_diff: a cn_probabilities map rewritten in another key order is NOT a change, and
             the one that really moved is named with its largest delta" 1 \
        'unchanged 2' 'largest |Δ|' 'PANEL1/GD_A CN 1' -- \
        "$PY" compare/gd_calls_diff.py "$CMP/gd_a.tsv.gz" "$CMP/gd_b.tsv.gz" --case-sample NA12878
    expect "gd_calls_diff: re-minted GD_IDs refuse at 2 and name the namespace trap, instead of an
             empty diff that reads like agreement" 2 \
        'NOTHING COMPARED' 'GD_ID namespace' -- \
        "$PY" compare/gd_calls_diff.py "$CMP/gd_a.tsv.gz" "$CMP/gd_rekey.tsv.gz"
    expect "gd_calls_diff --selftest pins every bucket apart, including the vanishing case row" 0 \
        'all selftest assertions passed' -- "$PY" compare/gd_calls_diff.py --selftest
    expect "sample_census: distinct samples, not rows, streamed, with the memory that proves it" 0 \
        'distinct values: 3' 'Peak RSS' '6 data line(s) read' -- \
        "$PY" compare/sample_census.py "$CMP/baf_a.txt.gz" --expect 3
    expect "sample_census: the merged table that lost a sample FAILS the declared count and prints
             both numbers" 1 \
        'EXPECTED 3 distinct sample(s), FOUND 2 -> VIOLATED' -- \
        "$PY" compare/sample_census.py "$CMP/baf_b.txt.gz" --expect 3
    expect "sample_census: a wide matrix is the wrong shape for a value-column census, and the refusal
             names the tool that fits" 2 \
        'WIDE matrix' 'matrix_diff' -- \
        "$PY" compare/sample_census.py "$CMP/wide_a.tsv"
    expect "sample_census --selftest pins the header decision, the short rows and every refusal" 0 \
        'all selftest assertions passed' -- "$PY" compare/sample_census.py --selftest
    check "--json writes the one envelope for every comparator (tool/argv/inputs+hash/rule/verdict)" \
        "$PY" scripts/check_artifacts.py "$CMP"
fi

echo
echo "selftest: one artifact, counted independently (compare/ was pairwise-only)"
# Single-artifact tools. terra/batch_configs.py --selftest is the only test that the
# `out UNDECLARED` and `NO OUTPUTS BOUND` checks fire.
gsvtk_guard_sites "no-wdl-batchconfigs"     # the one site below is withheld by the SKIP in the other arm
if [ "$HAS_WDL" = 0 ]; then
    # The output comparison needs the WDL AST; without miniwdl the tool refuses.
    echo "  SKIP  terra/batch_configs.py --selftest: this interpreter cannot import WDL, so the output"
    echo "        comparison its controls rest on never ran (pip install -r requirements-dev.txt)."
    skip=$((skip + 1)); skiplist="$skiplist batch_configs-selftest(no-WDL)"
    gsvtk_note_skip "no-wdl-batchconfigs"
else
    expect 'terra/batch_configs.py --selftest catches an output name the workflow never declared' 0 \
        'selftest: ok' -- "$PY" terra/batch_configs.py --selftest
    gsvtk_guard_sites_end "no-wdl-batchconfigs"
fi
# Every command in README.md and docs/*.md must name a real, runnable tool with real flags.
expect 'scripts/check_doc_flags.py --selftest finds each defect and stays silent on a clean doc' 0 \
    'all selftest assertions passed' -- "$PY" scripts/check_doc_flags.py --selftest
check "docs: every command in README.md and docs/ names a real tool, runnable as written, with real flags" \
    "$PY" scripts/check_doc_flags.py
# Wall-clock legs: a documented gate phase must be reported ungradable, never run. The ceiling
# (UNGRADABLE_CEILING in scripts/check_doc_flags.py) must move together with the number here.
budget 'the whole doc set grades inside its budget, green at the declared ungradable ceiling' 60 \
    '0 problem(s)' 'ceiling 12' -- "$PY" scripts/check_doc_flags.py
expect 'compare/artifact_tally.py --selftest finds and refuses every case it claims' 0 \
    'all selftest assertions passed' -- "$PY" compare/artifact_tally.py --selftest
gsvtk_guard_sites "no-wdl-reach"            # the one site below is withheld by the SKIP in the other arm
if [ "$HAS_WDL" = 0 ]; then
    # Without miniwdl, wdl_reach refuses (exit 3) rather than report every target NOT REACHED.
    echo "  SKIP  checks/wdl_reach.py --selftest: no WDL module, so the graph it resolves was never parsed"
    echo "        (pip install -r requirements-dev.txt; CI installs it, which is where this runs)."
    skip=$((skip + 1)); skiplist="$skiplist wdl_reach-selftest(no-WDL)"
    gsvtk_note_skip "no-wdl-reach"
else
    expect 'checks/wdl_reach.py --selftest resolves the graph both ways and names the orphan' 0 \
        'selftest: ok' -- "$PY" checks/wdl_reach.py --selftest
    gsvtk_guard_sites_end "no-wdl-reach"
fi

echo
echo "selftest: production pins are read from upstream, not transcribed here"
# prod_pins reads production pins from upstream; the pin count is asserted too. Raise it with the
# file, never lower it.
expect 'terra/batch_peek.py --selftest proves the bounded tally, the rc liveness signal and the task-artifact read' 0 \
    'all selftest assertions passed' -- "$PY" terra/batch_peek.py --selftest
expect 'scripts/prod_pins.py --selftest: every pin found, every refusal named' 0 \
    'prod_pins selftest: 9 ok, 0 failed' -- "$PY" scripts/prod_pins.py --selftest

echo
echo "selftest: the in-image runner refuses before a VM exists (offline; creates no instance)"
# run_in_image.sh refusals, asserted by their words. The CONTROL legs prove the refusals are not
# the script failing to parse its own arguments.
RII=checks/image-check/run_in_image.sh
RIIT=$(mktemp -d "${TMPDIR:-/tmp}/gsvtk-rii.XXXXXX")
printf '#!/bin/bash\necho hi\n' > "$RIIT/probe.sh"
: > "$RIIT/empty.sh"
expect 'no --image: refuses and names where a real ref comes from' 2 \
    'no --image given' 'IMAGE_REPO' -- bash "$RII" --dry-run --probe "$RIIT/probe.sh"
expect 'image ref containing a space: refused, a reference is one token' 2 \
    'whitespace' 'one token' -- bash "$RII" --dry-run --image "reg/foo tag" --probe "$RIIT/probe.sh"
# The allowlist arm uses a parameter expansion, not `tr`: GNU and BSD tr disagree on the `_-/`
# range, which left the guard dead on Linux.
expect 'the allowlist arm, not the denylist: $( in a reference is refused by character, not by shape' 2 \
    'carries "$()"' 'root shell' -- bash "$RII" --dry-run --image 'reg/foo/$(id)' --probe "$RIIT/probe.sh"
expect '... and so is a pipe, `>`, a backslash: the characters every denylist arm passed' 2 \
    'carries "|"' -- bash "$RII" --dry-run --image 'reg/foo/a|b' --probe "$RIIT/probe.sh"
# Controls: an allowlist that refuses everything is as useless as one that refuses nothing.
expect 'CONTROL for that arm: a port-numbered reference is a real one, and it passes' 0 \
    'nothing was created' 'registry.example.com:5000/a/b:1' -- bash "$RII" --dry-run \
    --image 'registry.example.com:5000/a/b:1' --probe "$RIIT/probe.sh"
expect 'CONTROL for that arm: a @sha256 digest reference (uppercase in the TAG half) passes too' 0 \
    'nothing was created' 'us.gcr.io/x/y@sha256:deadbeefCAFEBABE0123456789' -- bash "$RII" --dry-run \
    --image 'us.gcr.io/x/y@sha256:deadbeefCAFEBABE0123456789' --probe "$RIIT/probe.sh"
expect '0-byte probe: refused, since booting for it measures nothing' 2 \
    '0 BYTES' -- bash "$RII" --dry-run --image reg/foo:tag --probe "$RIIT/empty.sh"
expect 'CONTROL: a probe with real bytes gets through --dry-run' 0 \
    'non-comment bytes' 'nothing was created' -- bash "$RII" --dry-run --image reg/foo:tag \
    --probe "$RIIT/probe.sh"
rm -rf "$RIIT"

# ------------------------------------------------------------------ compute-path legs
# These guard code that runs behind `gcloud compute instances create`, graded offline. $CB holds stub
# gcloud/docker/apt-get/git/sleep/timeout/systemctl/uname placed first on PATH; each stub appends
# its call to $CC, and assertions count those calls (e.g. one `instances delete`, zero creates).
# Unmodelled gcloud subcommands log UNHANDLED and exit 40, and each section asserts its shims
# resolve first, so no leg can reach a real project.
#
# Guest scripts (startup-script, scp'd launchers) are captured into $CD by the stubs and run here
# with their host scratch paths rewritten into $CD; the rewrite is counted.
#
# Each section pairs its refusals with a positive control on the same stub, since "the guard never
# fired" and "the guard could not fire" look the same. Nothing here touches the network or writes
# outside $TMP.
CB="$TMP/compute-bin"                 # the only gcloud/docker/apt-get/git/... these legs can reach
MIN="$TMP/compute-minbin"             # a PATH with no flock on it, on every machine
CC="$TMP/compute-calls"               # the recorded call list: one line per stub call
CD="$TMP/compute-work"                # fixtures, captured guest bytes, saved runs
mkdir -p "$CB" "$MIN" "$CD"
for _t in bash cat date rm tee; do                    # the remote-build legs need a PATH where
    _p="$(command -v "$_t" 2>/dev/null || true)"      # `command -v flock` finds nothing
    [ -n "$_p" ] && ln -sf "$_p" "$MIN/$_t"
done                                                  # (util-linux ships flock; macOS does not)
: > "$CC"

cat > "$CB/gcloud" <<'STUBGCLOUD'
#!/bin/sh
# Records what the laptop asked for and answers from files the legs write. Anything these five
# scripts do not already do is UNHANDLED: a leg must not be able to reach a real project by
# forgetting to model a call.
printf 'gcloud %s\n' "$*" >> "$GSVTK_LEG_CALLS"
case "$*" in
  *"compute images list"*)   echo "ubuntu-2404-lts-amd64-20260101-fixture" ;;
  *"compute instances create"*)
      for a in "$@"; do
          case "$a" in
              startup-script=*) cp "${a#startup-script=}" "$GSVTK_LEG_HOME/startup.sh" ;;
          esac
      done
      echo "instance $2 created" ;;
  *"compute instances delete"*) exit "${GSVTK_LEG_DELETE_RC:-0}" ;;
  *"compute instances stop"* | *"compute instances start"*) : ;;
  *"instances get-serial-port-output"*)
      [ -f "${GSVTK_LEG_SERIAL:-}" ] && cat "$GSVTK_LEG_SERIAL" ;;
  *"instances describe"*)
      case "$*" in
          *created-by*) echo gsv-build ;;
          *) [ -f "${GSVTK_LEG_STATUS:-}" ] && cat "$GSVTK_LEG_STATUS" ;;
      esac ;;
  *"compute ssh"*)
      cmd=""; prev=""
      for a in "$@"; do [ "$prev" = "--command" ] && cmd="$a"; prev="$a"; done
      case "$cmd" in
          *gsv-launch.sh*)  printf '%s\n' "${GSVTK_LEG_LAUNCH:-GSV_LAUNCH_FAILED}" ;;
          *GSV_SENTINEL*)   printf '%s\n' "${GSVTK_LEG_POLL:-}" ;;
          *"wc -l < "*)     echo 7 ;;
          *)                exit "${GSVTK_LEG_SSH_RC:-0}" ;;
      esac ;;
  *"compute scp"*)  cp "$3" "$GSVTK_LEG_HOME/scp-${3##*/}" ;;
  *"zones describe"* | *"service-accounts list"* | *"projects describe"* | *"storage ls"*) : ;;
  *) printf 'UNHANDLED gcloud %s\n' "$*" >> "$GSVTK_LEG_CALLS"; exit 40 ;;
esac
exit "${GSVTK_LEG_GCLOUD_RC:-0}"
STUBGCLOUD

cat > "$CB/docker" <<'STUBDOCKER'
#!/bin/sh
printf 'docker %s\n' "$*" >> "$GSVTK_LEG_CALLS"
case "$1" in
  pull) exit "${GSVTK_LEG_PULL_RC:-0}" ;;
  run)  if [ -n "${GSVTK_LEG_HELP:-}" ] && [ -f "$GSVTK_LEG_HELP" ]; then cat "$GSVTK_LEG_HELP"; fi
        exit "${GSVTK_LEG_RUN_RC:-0}" ;;
  *)    echo "Docker version 27.5.1, stub" ;;
esac
exit 0
STUBDOCKER

cat > "$CB/apt-get" <<'STUBAPT'
#!/bin/sh
printf 'apt-get %s\n' "$*" >> "$GSVTK_LEG_CALLS"
# The pidfile's state at the builder's first real command: this is what turns "the pidfile is gone"
# into a statement about a file this run wrote instead of one that was never there.
if [ -n "${GSVTK_LEG_PIDCAP:-}" ] && [ -f "${GSV_PIDFILE:-}" ]; then
    cat "$GSV_PIDFILE" > "$GSVTK_LEG_PIDCAP"
fi
exit "${GSVTK_LEG_APT_RC:-0}"
STUBAPT

cat > "$CB/git" <<'STUBGIT'
#!/bin/sh
printf 'git %s\n' "$*" >> "$GSVTK_LEG_CALLS"
case "$1" in
  ls-remote) printf '9a34dc00000000000000000000000000000000aa\trefs/heads/%s\n' "${3##*/}" ;;
esac
exit "${GSVTK_LEG_GIT_RC:-0}"
STUBGIT

cat > "$CB/systemctl" <<'STUBSYS'
#!/bin/sh
printf 'systemctl %s\n' "$*" >> "$GSVTK_LEG_CALLS"
exit "${GSVTK_LEG_SYSTEMCTL_RC:-0}"
STUBSYS

cat > "$CB/uname" <<'STUBUNAME'
#!/bin/sh
printf 'uname %s\n' "$*" >> "$GSVTK_LEG_CALLS"
case "$*" in
    *-m*) printf '%s\n' "${GSVTK_LEG_ARCH:-x86_64}" ;;
    *)    printf 'Darwin\n' ;;
esac
STUBUNAME

# The wait loops sleep 20-45 s a turn; the stub makes them instant while still running every turn.
cat > "$CB/sleep" <<'STUBSLEEP'
#!/bin/sh
printf 'sleep %s\n' "$*" >> "$GSVTK_LEG_CALLS"
exit 0
STUBSLEEP

# macOS has no `timeout`; the scripts bound cleanup with `timeout 300 gcloud ...`.
cat > "$CB/timeout" <<'STUBTIMEOUT'
#!/bin/sh
shift
exec "$@"
STUBTIMEOUT

chmod +x "$CB"/* || exit 2     # the stubs are these legs' oracle; an unexecutable one is no oracle

LEGP="$CB:$PATH"                                    # the PATH every compute leg runs under
CFGJ="$CD/compute.env"                              # an explicit profile: never the developer's
printf 'GSVTK_PROJECT=gsvtk-fixture-project\nGSVTK_ZONE=us-central1-a\nGSVTK_IMAGE_REPO=us.gcr.io/gsvtk-fixture/gatk-sv\n' > "$CFGJ"
JAR="us.gcr.io/gsvtk-fixture/gatk-sv/sv-shell:my-branch-9a34dc"

# legrun NAME CMD... — run once, save output and exit status for `legout`.
legrun() {
    local name="$1"; shift
    local rc=0
    "$@" > "$CD/$name.out" 2>&1 </dev/null || rc=$?
    printf '%s\n' "$rc" > "$CD/$name.rc"
}
# legout NAME — replay a saved run: its output on stdout, its exit status as the status.
legout() {
    cat "$CD/$1.out" 2>/dev/null
    exit "$(cat "$CD/$1.rc" 2>/dev/null || echo 1)"
}
# stubcount WANT PATTERN [FILE] — count PATTERN in the call list (or FILE); prints hits=got/want.
stubcount() {
    local want="$1" pat="$2" f="${3:-$CC}" got
    got="$(grep -c -F -- "$pat" "$f" 2>/dev/null || true)"; got="${got:-0}"
    printf 'hits=%s/%s counting=[%s]\n' "$got" "$want" "$pat"
    [ "$got" = "$want" ]
}
# nohit PATTERN FILE — the bucket this run must NOT have produced.
nohit() {
    local pat="$1" file="$2" n
    n="$(grep -c -E -- "$pat" "$file" 2>/dev/null || true)"; n="${n:-0}"
    printf 'no-hit=%s (matches=%s for [%s])\n' "$([ "$n" = 0 ] && echo 1 || echo 0)" "$n" "$pat"
    [ "$n" = 0 ]
}
# holds FILE WANT — the content of a file the legs care about is a PID, so name it in the verdict.
holds() {
    local f="$1" want="$2" got
    if [ -f "$f" ]; then got="$(cat "$f" 2>/dev/null)"; else got=gone; fi
    printf 'holds=%s want=%s\n' "$got" "$want"
    [ "$got" = "$want" ]
}
# wrote FILE — non-empty, i.e. the pid really was recorded before it disappeared.
wrote() {
    local f="$1"
    if [ -s "$f" ]; then printf 'wrote=pid (%s)\n' "$(cat "$f")"; return 0; fi
    printf 'wrote=none\n'; return 1
}
# rotated WANT FILE — did the shared log get rotated? 1 = it did, 0 = the run refused first.
rotated() {
    local want="$1" f="$2" got=0
    [ -f "$f" ] && got=1
    printf 'rotated=%s/%s (%s)\n' "$got" "$want" "$f"
    [ "$got" = "$want" ]
}
# minbin DIR — the five tools the minimal PATH must resolve.
minbin() {
    local d="$1" n=0 t
    for t in bash cat date rm tee; do [ -x "$d/$t" ] && n=$((n + 1)); done
    printf 'tools=%s/5\n' "$n"
    [ "$n" = 5 ]
}
# shimfirst TOOL — where the leg PATH resolves TOOL. A leg whose shim is not first is not offline.
shimfirst() {
    local t="$1" p
    p="$(PATH="$LEGP" command -v "$t" 2>/dev/null || true)"
    printf 'shim=%s resolved=%s\n' "$t" "$p"
    [ "$p" = "$CB/$t" ]
}
# legint NAME WAIT-FOR CMD... — send a driver SIGINT once WAIT-FOR appears in its output.
# `set -m` is required: a background job in a non-interactive shell inherits SIGINT ignored, which
# cannot be trapped. Monitor mode gives the child its own process group, as a terminal would.
legint() {
    local name="$1" waitfor="$2"; shift 2
    : > "$CD/$name.out"
    ( set -m
      "$@" > "$CD/$name.out" 2>&1 </dev/null &
      local pid=$! i=0 rc=0 w=0
      while [ "$i" -lt 200 ]; do
          grep -q -F -- "$waitfor" "$CD/$name.out" 2>/dev/null && break
          i=$((i + 1)); sleep 0.05
      done
      kill -INT "$pid" 2>/dev/null
      # Watchdog (bash 3.2 has no `wait -n`): bash defers an INT trap until its foreground child
      # returns, so a signal landing in a long `sleep` could block for the tool's whole timeout.
      # Reap with KILL after 90 s and say so in the leg's output.
      # A lone SIGINT is occasionally never acted on (seen on CI runners), so resend it every 5 s,
      # as a person would press Ctrl-C again. cleanup_vm deletes at most once, so a repeat is safe.
      ( i=0
        while [ "$i" -lt 90 ]; do
            sleep 1; i=$((i + 1))
            kill -0 "$pid" 2>/dev/null || exit 0
            [ $((i % 5)) -eq 0 ] && kill -INT "$pid" 2>/dev/null
        done
        printf '%s\n' "legint: SIGINT went unanswered for ${i}s — the INT trap never ran; reaping with KILL" \
            >> "$CD/$name.out"
        kill -KILL "$pid" 2>/dev/null ) &
      local wd=$!
      wait "$pid"; rc=$?
      kill "$wd" 2>/dev/null; wait "$wd" 2>/dev/null
      printf '%s\n' "$rc" > "$CD/$name.rc"
    ) 2> "$CD/$name.job"
}
# rewritten SRC DST WANT — count the guest's /tmp/help.txt paths moved into $CD.
rewritten() {
    local src="$1" dst="$2" want="$3" before after moved
    before="$(grep -o -F -- '/tmp/help.txt' "$src" 2>/dev/null | wc -l | tr -d ' ')"
    after="$(grep -o -F -- '/tmp/help.txt' "$dst" 2>/dev/null | wc -l | tr -d ' ')"
    moved="$(grep -o -F -- "$CD/help.txt" "$dst" 2>/dev/null | wc -l | tr -d ' ')"
    printf 'help-scratch before=%s after=%s moved=%s\n' "$before" "$after" "$moved"
    [ "$before" = "$want" ] && [ "$after" = 0 ] && [ "$moved" = "$want" ]
}
# launchpaths SRC DST — same for the build launcher's two host scratch paths.
launchpaths() {
    local src="$1" dst="$2" pb pa lb la
    pb="$(grep -o -F -- '/tmp/gsv-build.pid' "$src" 2>/dev/null | wc -l | tr -d ' ')"
    pa="$(grep -o -F -- '/tmp/gsv-build.pid' "$dst" 2>/dev/null | wc -l | tr -d ' ')"
    lb="$(grep -o -F -- '/var/log/' "$src" 2>/dev/null | wc -l | tr -d ' ')"
    la="$(grep -o -F -- '/var/log/' "$dst" 2>/dev/null | wc -l | tr -d ' ')"
    printf 'launch-paths pid=%s/%s log=%s/%s\n' "$pb" "$pa" "$lb" "$la"
    [ "$pb" -gt 0 ] && [ "$pa" = 0 ] && [ "$lb" -gt 0 ] && [ "$la" = 0 ]
}
# orderis FILE EARLY LATE — EARLY must appear on an earlier line than LATE.
orderis() {
    local f="$1" early="$2" late="$3" ne nl
    ne="$(grep -n -F -- "$early" "$f" 2>/dev/null | head -1 | cut -d: -f1)"; ne="${ne:-0}"
    nl="$(grep -n -F -- "$late" "$f" 2>/dev/null | head -1 | cut -d: -f1)"; nl="${nl:-0}"
    printf 'early@%s late@%s ordered=%s\n' "$ne" "$nl" \
        "$([ "$ne" -gt 0 ] && [ "$ne" -lt "$nl" ] && echo 1 || echo 0)"
    [ "$ne" -gt 0 ] && [ "$ne" -lt "$nl" ]
}

# --- jar probe: an unreachable image is no verdict ------------------------------------------------
# The guest must stop with IMAGE_ABSENT/NO_HELP instead of printing DONE over empty help text, and
# the driver must treat those markers as a refusal, not as "flag absent".
echo
echo "selftest: the jar probe treats an unreachable image as NO VERDICT, not as an absent flag (stub gcloud+docker)"
: > "$CC"
printf 'startup-script: ### pulled %s\nstartup-script: ###   --rd-depth-table = PRESENT\nstartup-script: JAR_FLAG_PROBE=DONE\n' "$JAR" > "$CD/serial_done.txt"
printf 'startup-script: ### pulled nothing\nstartup-script: JAR_FLAG_PROBE=IMAGE_ABSENT %s\n' "$JAR" > "$CD/serial_absent.txt"
: > "$CD/serial_none.txt"
printf 'Usage: GenotypeSVs [options]\n  --rd-depth-table FILE\n  --rd-pesr-table FILE\n  --rd-table FILE\n' > "$CD/help_ok.txt"
: > "$CD/help_empty.txt"
expect 'C1: this leg PATH resolves gcloud to the stub, not to a real one' 0 \
    "shim=gcloud resolved=$CB/" -- shimfirst gcloud
expect 'C1: ... and docker to the stub too (the guest arms below run it)' 0 \
    "shim=docker resolved=$CB/" -- shimfirst docker
legrun j_done env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" \
    GSVTK_LEG_CALLS="$CC" GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_done.txt" \
    bash checks/image-check/jar_flag_probe.sh --image "$JAR"
expect 'C1: CONTROL -- a serial log that really does carry DONE is graded: exit 0, and the flag rows
       print' 0 'what the shipped jar accepts' -- legout j_done
expect 'C1: a graded probe still hands its VM back (one delete, by the exit trap)' 0 'hits=1/1' \
    -- stubcount 1 'compute instances delete'
check 'C1: the startup script this run shipped to the VM was captured, so the guest arms below run
       these bytes and not a hand-copy' test -s "$CD/startup.sh"
# The guest writes its scratch to /tmp/help.txt; it runs here only with that path moved into $CD.
sed "s#/tmp/help.txt#$CD/help.txt#g" "$CD/startup.sh" > "$CD/jar-guest.sh"
expect 'C1: the captured guest script runs with its scratch file inside the temp dir -- 5 paths in,
       5 moved, 0 left naming a host path' 0 'help-scratch before=5 after=0 moved=5' \
    -- rewritten "$CD/startup.sh" "$CD/jar-guest.sh" 5
: > "$CC"
legrun j_pull env PATH="$LEGP" GSVTK_LEG_CALLS="$CC" GSVTK_LEG_HOME="$CD" GSVTK_LEG_PULL_RC=1 \
    bash "$CD/jar-guest.sh"
expect 'C1: a pull that fails says IMAGE_ABSENT and stops (guest exit 0: it chose to stop, it did not
       crash)' 0 'JAR_FLAG_PROBE=IMAGE_ABSENT' -- legout j_pull
expect 'C1: ... and it does NOT print DONE, the line the driver reads as a verdict' 0 'no-hit=1' \
    -- nohit 'JAR_FLAG_PROBE=DONE' "$CD/j_pull.out"
legrun j_nohelp env PATH="$LEGP" GSVTK_LEG_CALLS="$CC" GSVTK_LEG_HOME="$CD" \
    GSVTK_LEG_HELP="$CD/help_empty.txt" bash "$CD/jar-guest.sh"
expect 'C1: an image that pulls but yields no help text says NO_HELP, not three confident absents' 0 \
    'JAR_FLAG_PROBE=NO_HELP' -- legout j_nohelp
expect 'C1: ... and again no DONE over an empty file' 0 'no-hit=1' \
    -- nohit 'JAR_FLAG_PROBE=DONE' "$CD/j_nohelp.out"
: > "$CC"
legrun j_help env PATH="$LEGP" GSVTK_LEG_CALLS="$CC" GSVTK_LEG_HOME="$CD" \
    GSVTK_LEG_HELP="$CD/help_ok.txt" bash "$CD/jar-guest.sh"
expect 'C1: CONTROL on the guest -- the same script, given help text to grade, DOES reach DONE and
       grades all three long names' 0 'JAR_FLAG_PROBE=DONE' '--rd-depth-table = PRESENT' \
    '--rd-pesr-table = PRESENT' '--rd-table = PRESENT' -- legout j_help
expect 'C1: ... which also means it really executed the jar twice (2 docker runs), not a probe that
       skipped its own work' 0 'hits=2/2' -- stubcount 2 'docker run'
: > "$CC"
legrun j_absent env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_absent.txt" \
    bash checks/image-check/jar_flag_probe.sh --image "$JAR"
expect 'C1: the driver stops on IMAGE_ABSENT with exit 5 and says this is NO verdict, not a finding' 5 \
    'there is NO verdict here' 'This is NOT a finding' -- legout j_absent
expect 'C1: ... and it reads the marker on its FIRST poll, not after the 15-minute ceiling' 0 \
    'hits=1/1' -- stubcount 1 'get-serial-port-output'
expect 'C1: the exit-5 path still deletes the VM (a new early exit that skipped cleanup would bill)' 0 \
    'hits=1/1' -- stubcount 1 'compute instances delete'
: > "$CC"
legrun j_quiet env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_none.txt" \
    bash checks/image-check/jar_flag_probe.sh --image "$JAR"
expect 'C1: CONTROL for that 5 -- a serial log with no marker at all is still not a verdict: exit 1,
       named, after the whole ceiling' 1 'no answer captured' -- legout j_quiet
expect 'C1: ... and the 45-turn ceiling was really run, not a leg that bailed early' 0 'hits=45/45' \
    -- stubcount 45 'get-serial-port-output'
expect 'C1: nothing here asked for a gcloud call the stub does not model' 0 'no-hit=1' \
    -- nohit 'UNHANDLED' "$CC"

# --- build driver: a launch that never reached its poll loop stops the VM ------------------------
# The stop guard is armed once the VM exists and disarmed when the poll loop takes over cleanup, so
# a healthy run must show exactly one stop.
echo
echo "selftest: the build driver stops a VM that never started a build, and lets go when one did (stub gcloud)"
: > "$CC"
echo GONE > "$CD/status_gone"            # no VM yet: this invocation creates it
echo RUNNING > "$CD/status_running"      # the warm persistent builder the mode exists for
printf '### GATK_SV_BUILD_RESULT=SUCCESS (fixture) ###\n__GSV_SENTINEL__\n' > "$CD/poll_success.txt"
BUILD=(bash docker/gatk-sv-build.sh --persistent --project gsvtk-fixture-project
       --docker-repo us.gcr.io/gsvtk-fixture/gatk-sv --instance gsv-fixture-builder my-branch)
expect 'C2: this leg PATH resolves gcloud to the stub' 0 "shim=gcloud resolved=$CB/" \
    -- shimfirst gcloud
expect 'C2: ... and git to the stub (git ls-remote resolves the branch tip, so unstubbed this leg
       would reach the network)' 0 "shim=git resolved=$CB/" -- shimfirst git
: > "$CC"
legrun b_ssh env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_STATUS="$CD/status_gone" "${BUILD[@]}"
expect 'C2: SSH never comes up: exit 1, named, and the VM this run created is stopped' 1 \
    'SSH never came up' 'build never reached its poll loop' -- legout b_ssh
expect 'C2: ... the VM really was created by this run (1 create), so the stop cannot be incidental' 0 \
    'hits=1/1' -- stubcount 1 'compute instances create'
expect 'C2: ... and it was really stopped (1 stop, by the guard)' 0 'hits=1/1' \
    -- stubcount 1 'compute instances stop'
: > "$CC"
legrun b_keeprun env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_STATUS="$CD/status_gone" \
    bash docker/gatk-sv-build.sh --persistent --keep-running --project gsvtk-fixture-project \
    --docker-repo us.gcr.io/gsvtk-fixture/gatk-sv --instance gsv-fixture-builder my-branch
expect 'C2: CONTROL -- --keep-running is an explicit request, so the guard leaves the box up and says
       why it is not stopping' 1 'stays up because --keep-running' -- legout b_keeprun
expect 'C2: ... and nothing was stopped (0 stops: the guard is not unconditional)' 0 'hits=0/0' \
    -- stubcount 0 'compute instances stop'
: > "$CC"
legrun b_ok env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_STATUS="$CD/status_running" GSVTK_LEG_LAUNCH=GSV_LAUNCHED \
    GSVTK_LEG_POLL="$(cat "$CD/poll_success.txt")" "${BUILD[@]}"
expect 'C2: CONTROL on the disarm -- a build that launched and printed SUCCESS reaches the poll loop
       and exits 0' 0 'pushed to' -- legout b_ok
expect 'C2: ... and the stop count is exactly 1, which is the disarm: 2 stops would be the guard
       stopping a box the poll loop already owns' 0 'hits=1/1' -- stubcount 1 'compute instances stop'
expect 'C2: ... and the guard never fired on that path' 0 'no-hit=1' \
    -- nohit 'build never reached its poll loop' "$CD/b_ok.out"
: > "$CC"
legrun b_busy env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_STATUS="$CD/status_running" \
    GSVTK_LEG_LAUNCH="GSV_BUSY pid=4321
### tail -n 3 of the shared log" "${BUILD[@]}"
expect 'C2: a live builder on a shared VM is refused by name with its pid, and is not reported as a
       build that failed to start' 1 'a build is already running on gsv-fixture-builder' 'pid=4321' \
    -- legout b_busy
expect 'C2: ... that refusal is a death before the poll loop, so the guard stops the VM' 0 \
    'hits=1/1' -- stubcount 1 'compute instances stop'
expect 'C2: ... and it did not create a box of its own to argue about' 0 'hits=0/0' \
    -- stubcount 0 'compute instances create'
LF="$(ls -t "$CD"/scp-gsv-launch-* 2>/dev/null | head -1)"
check 'C2: the launcher this run scp/d to the VM was captured, so the ordering below is about these
       bytes and not a hand-copy' test -f "$LF"
expect 'C2: the busy check runs BEFORE the shared log is rotated -- a refused second build must not
       hide the healthy one by deleting its log' 0 'ordered=1' \
    -- orderis "$LF" 'echo "GSV_BUSY pid=$p"' 'mv -f /var/log/gsv-build.log'
expect 'C2: ... and before the pidfile it must not delete is removed' 0 'ordered=1' \
    -- orderis "$LF" 'echo "GSV_BUSY pid=$p"' 'rm -f /tmp/gsv-build.pid'
# Run the captured launcher with its host paths rewritten into $CD (counted).
sed -e "s#/tmp/gsv-build.pid#$CD/gsv-build.pid#g" \
    -e "s#/var/log/gsv-build#$CD/gsv-build#g" \
    -e "s#/tmp/gsv-remote-build.sh#$CD/noop-remote-build.sh#g" "$LF" > "$CD/launch-run.sh"
printf '#!/bin/sh\nexit 0\n' > "$CD/noop-remote-build.sh"
chmod +x "$CD/noop-remote-build.sh"
expect 'C2: the launcher rewrite moved every host scratch path it names -- pidfile AND log, the
       rotated .prev.log included, so nothing this run executes can touch /var/log' 0 \
    'launch-paths pid=3/0 log=6/0' -- launchpaths "$LF" "$CD/launch-run.sh"
printf '%s\n' "$$" > "$CD/gsv-build.pid"          # $$ of this shell: alive, by definition
legrun l_busy env PATH="$LEGP" GSVTK_LEG_CALLS="$CC" GSVTK_LEG_HOME="$CD" bash "$CD/launch-run.sh"
expect 'C2: the launcher run against a LIVE pid says GSV_BUSY and exits 1 (the pidfile is the only
       liveness signal the laptop has, which is why d6b3c0b matters)' 1 'GSV_BUSY pid='"$$" \
    -- legout l_busy
expect 'C2: ... and it refused before touching the shared log: no rotation happened' 0 'rotated=0/0' \
    -- rotated 0 "$CD/gsv-build.prev.log"
printf '2147483647\n' > "$CD/gsv-build.pid"       # past every pid_max: dead by construction
: > "$CD/gsv-build.log"
legrun l_gone env PATH="$LEGP" GSVTK_LEG_CALLS="$CC" GSVTK_LEG_HOME="$CD" bash "$CD/launch-run.sh"
expect 'C2: CONTROL -- the same launcher with a DEAD pid gets PAST the busy check, so the refusal
       above is not every run refusing (and its own exit 1 is what the driver reads as
       "builder failed to start")' 1 'GSV_LAUNCH_FAILED' -- legout l_gone
expect 'C2: ... and it then really did rotate the log, which is the thing the busy check exists to
       prevent' 0 'rotated=1/1' -- rotated 1 "$CD/gsv-build.prev.log"
expect 'C2: nothing here asked for a gcloud call the stub does not model' 0 'no-hit=1' \
    -- nohit 'UNHANDLED' "$CC"

# --- remote build: only the builder that wrote the pidfile may remove it --------------------------
# The laptop tests liveness with `kill -0 $(cat /tmp/gsv-build.pid)`, so clearing another builder's
# pidfile makes a healthy build look dead. These arms run under a PATH with no flock (asserted
# below) so the builder always reaches its pidfile write, on Linux and macOS alike.
echo
echo "selftest: a builder that dies owning the pidfile clears it; one that never owned it does not (stub apt)"
: > "$CC"; MINP="$CB:$MIN"
expect 'C3: no flock is reachable on that PATH anywhere, so the lock block is skipped identically on
       a Linux runner and here (asserted, not assumed)' 0 'flock=absent' -- env -i "PATH=$MINP" \
    "HOME=$HOME" bash -c 'command -v flock >/dev/null 2>&1 && echo "flock=present" || echo "flock=absent"'
expect 'C3: ... and that PATH still resolves all five tools the reached path of the builder needs' 0 \
    'tools=5/5' -- minbin "$MIN"
RB=(env -i "PATH=$MINP" "HOME=$HOME" "GSV_PIDFILE=$CD/gsv-build.pid" "GSV_LOG=$CD/rb.log"
    "GSVTK_LEG_CALLS=$CC" "GSVTK_LEG_HOME=$CD" "GSVTK_LEG_APT_RC=1"
    "GSVTK_LEG_PIDCAP=$CD/pid.wrote" GSV_BRANCH=my-branch
    GSV_DOCKER_REPO=us.gcr.io/gsvtk-fixture/gatk-sv GSV_IMAGE_TAG=my-branch-9a34dc
    GSV_TARGETS=sv-pipeline)
printf '424242\n' > "$CD/gsv-build.pid"           # a live builder's pid, left by the run BEFORE this one
legrun r_foreign "${RB[@]}" GSVTK_LEG_ARCH=aarch64 "$MIN/bash" docker/remote-build.sh
expect 'C3: a successor that refuses BEFORE writing a pidfile leaves the live builder alone (the arch
       guard is the first refusal after the guards, and it must not clear what it never wrote)' 1 \
    'GATK_SV_BUILD_RESULT=FAILURE (arch is aarch64' -- legout r_foreign
expect 'C3: the foreign pid is still in there -- that number is what the laptop reads as "the builder
       is alive", and clearing it is what stopped a healthy build mid-push' 0 'holds=424242' \
    -- holds "$CD/gsv-build.pid" 424242
expect 'C3: and the refusal still reached the laptop: the marker is in the on-disk log too, which is
       the copy that survives a wedged pipe (a finish() that only declined to delete and stayed
       silent would leave the driver waiting out the whole timeout)' 0 'hits=2/2' \
    -- stubcount 2 'GATK_SV_BUILD_RESULT=FAILURE' "$CD/rb.log"
rm -f "$CD/gsv-build.pid" "$CD/pid.wrote"
legrun r_own "${RB[@]}" GSVTK_LEG_ARCH=x86_64 "$MIN/bash" docker/remote-build.sh
expect 'C3: CONTROL -- the same builder owning its own pid DELETES the pidfile on the way out, so the
       arm above is not every run leaving it behind' 1 \
    'GATK_SV_BUILD_RESULT=FAILURE (apt update' -- legout r_own
expect 'C3: ... it really did write one first (captured at its first real command, so "gone" below
       cannot mean "was never there")' 0 'wrote=pid' -- wrote "$CD/pid.wrote"
expect 'C3: ... and it is gone now' 0 'holds=gone' -- holds "$CD/gsv-build.pid" gone
expect 'C3: nothing on either path failed for want of a tool on that minimal PATH -- a missing `rm`
       hides behind `2>/dev/null || true` and silently un-fixes this fix' 0 'no-hit=1' \
    -- nohit 'command not found' "$CD/r_own.out"
expect 'C3: nothing here asked for a call the stub does not model' 0 'no-hit=1' \
    -- nohit 'UNHANDLED' "$CC"

# --- sv-shell check: interrupt cleanup, and keeping the VM when no marker arrived -----------------
# An INT/TERM trap deletes the VM (unless --keep). With no marker the result is NOT SCORED and the
# VM is kept so its serial output stays re-fetchable. Every arm counts deletes.
echo
echo "selftest: the sv-shell check cleans up on interrupt and keeps the VM when it learned nothing (stub gcloud)"
: > "$CC"
printf 'startup-script: ### SVSHELL_IMAGE_CHECK=PROVEN (both md5s matched) image=x ###\nstartup-script: ### SVSCAN_DONE\n' > "$CD/serial_proven.txt"
printf 'startup-script: ### SVSHELL_IMAGE_CHECK=FAILURE (the shipped jar is stale) ###\n' > "$CD/serial_failure.txt"
expect 'C4: this leg PATH resolves gcloud to the stub -- the arm below creates an instance NAME, and
       a shim that is not first would be creating it for real' 0 "shim=gcloud resolved=$CB/" \
    -- shimfirst gcloud
: > "$CC"
legrun v_proven env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_proven.txt" \
    bash checks/image-check/svshell_image_check.sh --image "$JAR" --project gsvtk-fixture-project
expect 'C4: CONTROL -- a captured PROVEN marker is graded PROVEN, exit 0' 0 'RESULT: PROVEN' \
    -- legout v_proven
expect 'C4: ... and a graded run deletes its VM exactly once -- the EXIT trap must not run a second
       delete and print a WARN about nothing' 0 'hits=1/1' -- stubcount 1 'compute instances delete'
expect 'C4: ... with no cleanup failure claimed' 0 'no-hit=1' \
    -- nohit 'CLEANUP FAILED' "$CD/v_proven.out"
: > "$CC"
legrun v_failure env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_failure.txt" \
    bash checks/image-check/svshell_image_check.sh --image "$JAR" --project gsvtk-fixture-project
expect 'C4: a FAILURE marker is a finding, not a timeout (exit 1), and the VM is still deleted' 1 \
    'RESULT: FAILURE' -- legout v_failure
expect 'C4: ... one delete on that path too' 0 'hits=1/1' -- stubcount 1 'compute instances delete'
# The marker extractor must read the whole token (`[A-Z][A-Z_]*`): SCAN_CLEAN truncated to SCAN
# fell through to the timeout arm. The third assertion is the control.
: > "$CC"
printf 'startup-script: ### SVSHELL_IMAGE_CHECK=SCAN_CLEAN (byte identity not proven: no expectations supplied) ###\n' > "$CD/serial_scan_clean.txt"
legrun v_scan_clean env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_scan_clean.txt" \
    bash checks/image-check/svshell_image_check.sh --image "$JAR" --project gsvtk-fixture-project
expect 'C4: SCAN_CLEAN is classified SCAN_CLEAN, not truncated to SCAN and lost to the timeout arm' 0 \
    'SCAN CLEAN, BYTE IDENTITY NOT PROVEN' -- legout v_scan_clean
expect 'C4: ... a graded outcome, so exit 0 and exactly one delete, like any other known result' 0 'hits=1/1' \
    -- stubcount 1 'compute instances delete'
expect 'C4: CONTROL -- these same bytes under the old [A-Z]* extractor printed a phantom TIMEOUT' 0 \
    'no-hit=1' -- nohit 'RESULT: TIMEOUT' "$CD/v_scan_clean.out"
# Static check: the runtime legs below cannot prove SIGINT reaches the driver, so pin that the
# cleanup trap covers both INT and TERM and exits 130.
expect 'C4: the cleanup trap is armed for INT *and* TERM and re-raises 130, read off the script itself' \
    0 'TRAP-ARMED' -- sh -c 'l=$(grep "^trap .*cleanup_vm" checks/image-check/svshell_image_check.sh \
    | grep "INT" | grep "TERM" | grep "exit 130" || true); [ -n "$l" ] && echo TRAP-ARMED \
    || { echo "no cleanup trap armed for INT+TERM exiting 130"; exit 1; }'
: > "$CC"
legint v_int "waiting for the result marker" env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" \
    PATH="$LEGP" GSVTK_LEG_CALLS="$CC" GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_none.txt" \
    bash checks/image-check/svshell_image_check.sh --image "$JAR" --project gsvtk-fixture-project
expect 'C4: Ctrl-C while waiting exits 130 -- the trap ran and re-raised the status a person expects' 130 \
    'waiting for the result marker' -- legout v_int
expect 'C4: ... and the VM IS deleted on that path: the defect left an e2-standard-8 and 60 GB
       billing and printed nothing' 0 'hits=1/1' -- stubcount 1 'compute instances delete'
: > "$CC"
legint v_keep "waiting for the result marker" env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" \
    PATH="$LEGP" GSVTK_LEG_CALLS="$CC" GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_none.txt" \
    bash checks/image-check/svshell_image_check.sh --image "$JAR" --project gsvtk-fixture-project --keep
expect 'C4: CONTROL -- interrupted with --keep it still exits 130, but a VM asked for by name is a VM
       asked to stay' 130 'waiting for the result marker' -- legout v_keep
expect 'C4: ... so ZERO deletes -- this is what proves the arm above is not an unconditional delete' 0 \
    'hits=0/0' -- stubcount 0 'compute instances delete'
: > "$CC"
legint v_dirty "waiting for the result marker" env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" \
    PATH="$LEGP" GSVTK_LEG_CALLS="$CC" GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_none.txt" \
    GSVTK_LEG_DELETE_RC=1 bash checks/image-check/svshell_image_check.sh --image "$JAR" \
    --project gsvtk-fixture-project
expect 'C4: when cleanup itself fails the hand command is printed: an interruption that silently
       leaves a box running is the incident this trap was copied for' 130 'CLEANUP FAILED' \
    'gcloud compute instances delete' -- legout v_dirty
: > "$CC"
legrun v_nomark env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_none.txt" \
    bash checks/image-check/svshell_image_check.sh --image "$JAR" --project gsvtk-fixture-project --timeout 1
expect 'C4: no marker at all is NOT SCORED, exit 3, with the VM KEPT so the serial stays
       re-fetchable -- the opposite of the two arms above, deliberately' 3 'NOT SCORED' \
    'is being KEPT' -- legout v_nomark
expect 'C4: ... so ZERO deletes, the ONE-MINUTE ceiling (--timeout 1) having run out. This arm was
       written against the hardcoded 2400 s default, which left a phase in this file that could outlast
       the whole rest of the suite -- measured: the gate ran 30 minutes without reaching the next phase.
       The knob, not a shorter sleep, is the cure: a caller who wants a short answer should be able to
       ask for one.' 0 'hits=0/0' \
    -- stubcount 0 'compute instances delete'
expect 'C4: ... and the delete command is handed over rather than left to memory' 3 \
    'gcloud compute instances delete' -- legout v_nomark
expect 'C4: nothing here asked for a gcloud call the stub does not model' 0 'no-hit=1' \
    -- nohit 'UNHANDLED' "$CC"

# --- image-check launchers screen --image before creating anything --------------------------------
# All three launchers write the reference into a startup script that runs as root on the VM; the
# shared guard in checks/image-check/image_ref.sh must refuse injection shapes with zero creates.
# The second half checks that the serial poll passes --zone.
echo
echo "selftest: every image-check launcher screens --image before it creates anything (stub gcloud)"
: > "$CC"
legrun v_quote env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" bash checks/image-check/svshell_image_check.sh --project gsvtk-fixture-project \
    --image 'x/y:tag"; id > /tmp/GSVTK-PWNED; "'
expect 'C5: svshell_image_check.sh refuses the reference that used to escape its generated IMAGE="…" line' 2 \
    'refusing "--image' -- legout v_quote
# The call list, not the wording: a refusal that still created the VM would be a refusal AND a bill.
expect 'C5: ... and zero instances were created -- the guard runs before the name is derived' 0 \
    'hits=0/0' -- stubcount 0 'compute instances create'
: > "$CC"
legrun j_newline env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" bash checks/image-check/jar_flag_probe.sh \
    --image "$(printf 'x/y:tag\nid > /tmp/GSVTK-PWNED')"
expect 'C5: jar_flag_probe.sh refuses the newline that used to land in its guest as a command line' 2 \
    'refusing "--image' -- legout j_newline
expect 'C5: ... zero creates here too' 0 'hits=0/0' -- stubcount 0 'compute instances create'
# `$(` has no whitespace or separator, so only the allowlist catches it.
: > "$CC"
legrun v_dollar env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" bash checks/image-check/svshell_image_check.sh --project gsvtk-fixture-project \
    --image 'us.gcr.io/gsvtk-fixture/$(id)'
expect 'C5: the allowlist arm reaches the siblings too -- a $() reference is refused, by character' 2 \
    'carries "$()"' 'root shell' -- legout v_dollar
expect 'C5: ... refused before the instance name that would have been built from it' 0 'hits=0/0' \
    -- stubcount 0 'compute instances create'
: > "$CC"
legrun j_dollar env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" bash checks/image-check/jar_flag_probe.sh --image 'us.gcr.io/gsvtk-fixture/$(id)'
expect 'C5: ... and the same for the jar probe, which interpolates the reference into its heredoc' 2 \
    'carries "$()"' -- legout j_dollar
expect 'C5: ... zero creates' 0 'hits=0/0' -- stubcount 0 'compute instances create'
# Controls: a guard that refuses every reference would also pass the legs above.
: > "$CC"
legrun v_ok env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_proven.txt" \
    bash checks/image-check/svshell_image_check.sh --image "$JAR" --project gsvtk-fixture-project
expect 'C5: CONTROL -- the fixture reference clears the guard and is graded PROVEN, exit 0' 0 \
    'RESULT: PROVEN' -- legout v_ok
expect 'C5: ... so the four refusals above are about the reference, not about every reference' 0 \
    'hits=1/1' -- stubcount 1 'compute instances create'
: > "$CC"
legrun j_ok env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_done.txt" \
    bash checks/image-check/jar_flag_probe.sh --image "$JAR"
expect 'C5: CONTROL for the jar probe -- the same reference reaches its verdict, graded and deleted' 0 \
    'what the shipped jar accepts' -- legout j_ok
expect 'C5: ... one create, one delete, and nothing the stub does not model' 0 'hits=1/1' \
    -- stubcount 1 'compute instances delete'

# Instances are zonal, so the serial poll must pass --zone like create and delete do. The stub
# answers regardless of flags, so count the flag. The poll itself is graded first.
: > "$CC"
legrun j_zone env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_done.txt" \
    bash checks/image-check/jar_flag_probe.sh --image "$JAR" --zone europe-west1-b
expect 'C5: the poll ran exactly once under a NON-default zone, so the count below has a poll to grade' 0 \
    'hits=1/1' -- stubcount 1 'instances get-serial-port-output'
expect 'C5: ... and ALL THREE calls (create, poll, delete) name that zone -- the poll was the one that did not' 0 \
    'hits=3/3' -- stubcount 3 '--zone=europe-west1-b'
expect 'C5: nothing here asked for a gcloud call the stub does not model' 0 'no-hit=1' \
    -- nohit 'UNHANDLED' "$CC"

# run_in_image.sh: injection shapes are refused by the character allowlist with the boot path live
# (no --dry-run), so a hole would show up as a recorded create; the serial fixture keeps such a run
# out of the poll loop. Every hostile reference passes the denylist arms, so "no docker reference
# can contain" can only come from the allowlist. Honest refs, a digest and a port must still pass.
echo
echo "selftest: an --image carrying shell metacharacters is refused while the boot path is live, and honest refs still pass (stub gcloud)"
: > "$CC"
printf '#!/bin/bash\necho hi\n' > "$CD/c5-probe.sh"
printf 'startup-script: ### output: PROBE_OUT_LINES=7 captured, first 60 shown\nstartup-script: RUN_IN_IMAGE=RC0\n' > "$CD/serial_rc0.txt"
expect 'C7: this leg PATH resolves gcloud to the stub -- the arms below run WITHOUT --dry-run, so a
       guard with a hole would be creating a real instance in whoever is running this gate' 0 \
    "shim=gcloud resolved=$CB/" -- shimfirst gcloud
# Injection shapes, boot path live.
for hostile in '$(id)' '`id`' 'reg/foo:tag|tee' 'reg/foo:tag&bg' 'reg/foo:tag>out'; do
    _tag="c5_$(printf '%s' "$hostile" | tr -c 'A-Za-z0-9' '_')"
    legrun "$_tag" env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
        GSVTK_LEG_HOME="$CD" GSVTK_LEG_SERIAL="$CD/serial_rc0.txt" \
        bash checks/image-check/run_in_image.sh --image "$hostile" --timeout 1 \
        --probe "$CD/c5-probe.sh"
    expect "C7: --image \"$hostile\": refused by the ALLOWLIST arm, which is the one that names the
           characters rather than the mistake" 2 'no docker reference can contain' \
        'Nothing was created' -- legout "$_tag"
done
# Controls: $JAR (the reference these legs boot with), a digest, and a registry port.
legrun c5_jar env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" bash checks/image-check/run_in_image.sh --dry-run --image "$JAR" \
    --probe "$CD/c5-probe.sh"
expect 'C7: CONTROL -- the reference these legs themselves boot with passes, and its bytes are named
       back' 0 "--dry-run: nothing was created" "image    : $JAR" -- legout c5_jar
legrun c5_digest env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" bash checks/image-check/run_in_image.sh --dry-run \
    --image "$JAR@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef" \
    --probe "$CD/c5-probe.sh"
expect 'C7: ... and so does the pinned-digest form, which is what a reproducible probe names (the `@`
       is in the allowlist for exactly this reason)' 0 \
    'image    : '"$JAR"'@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef' \
    -- legout c5_digest
legrun c5_port env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD" bash checks/image-check/run_in_image.sh --dry-run \
    --image 'registry.example.com:5000/a/b:My_Tag-1.2' --probe "$CD/c5-probe.sh"
expect 'C7: ... and a private registry on a port, with an UPPERCASE LETTER IN THE TAG: the lowercase
       rule is about the repository name half, and an allowlist that mistook a tag for a name would
       refuse every hand-built image' 0 'image    : registry.example.com:5000/a/b:My_Tag-1.2' \
    -- legout c5_port
# Why the refusal matters: the accepted reference is interpolated, inside double quotes, into the
# startup script the VM runs as root.
expect 'C7: WHY that refusal is worth an exit 2 -- the accepted reference really is interpolated into
       the startup script the VM runs as root, quotes and all' 0 \
    "IMAGE=\"$JAR\"" 'docker pull "$IMAGE"' -- legout c5_jar
expect 'C7: nothing in this section reached gcloud at all -- the five refusals happened before the
       create call (they were not fused out of it), and the three controls stopped at --dry-run' 0 \
    'hits=0/0' -- stubcount 0 'gcloud '

# --- the image check's plan-only exemption is decided per launcher -------------------------------
# `./gsvtk check image` exempts --dry-run from the read-only and --confirm gates only for
# run_in_image, the one launcher that implements it. Each refusal is paired with the same launcher
# dispatching, and every arm sets GSVTK_READ_ONLY explicitly so an exported value cannot change
# the results.
echo
echo "selftest: the image check's plan-only exemption is decided per launcher, with a stub gcloud recording nothing (stub gcloud)"
: > "$CC"
printf '#!/bin/bash\necho hi\n' > "$CD/c6-probe.sh"
expect 'C8: this leg PATH resolves gcloud to the stub -- the arms below that are NOT read-only get as
       far as their launcher, and one typo from here that would be a real create' 0 \
    "shim=gcloud resolved=$CB/" -- shimfirst gcloud
GS=(env GSVTK_CONFIG="$CFGJ" GSVTK_PYTHON="$PY" PATH="$LEGP" GSVTK_LEG_CALLS="$CC" \
    GSVTK_LEG_HOME="$CD")
legrun p_run_ro "${GS[@]}" GSVTK_READ_ONLY=1 ./gsvtk check image run_in_image --dry-run \
    --image "$JAR" --probe "$CD/c6-probe.sh"
expect 'C8: read-only + run_in_image --dry-run: exempt, and the line says WHICH launcher implements
       the mode rather than crediting the token' 0 \
    'plan-only mode (--dry-run, implemented by run_in_image)' '--dry-run: nothing was created' \
    -- legout p_run_ro
legrun p_jar_ro "${GS[@]}" GSVTK_READ_ONLY=1 ./gsvtk check image jar_flag_probe --dry-run \
    --image "$JAR"
expect 'C8: read-only + jar_flag_probe --dry-run: REFUSED -- the same token, the next launcher over.
       Under the token scan this cell dispatched, and only the launcher behind it stood between
       it and an instance' 2 'REFUSED' 'check image jar_flag_probe' -- legout p_jar_ro
legrun p_svs_ro "${GS[@]}" GSVTK_READ_ONLY=1 ./gsvtk check image svshell_image_check --dry-run \
    --image "$JAR"
expect 'C8: ... and the third launcher is refused by the same rule, named by name' 2 'REFUSED' \
    'check image svshell_image_check' -- legout p_svs_ro
legrun p_jar_ro2 "${GS[@]}" GSVTK_READ_ONLY=1 ./gsvtk check image jar_flag_probe --image "$JAR"
expect 'C8: CONTROL on that pair -- jar_flag_probe WITHOUT the token is refused identically, so the
       arm above grades an unexempt LAUNCHER and not the word "--dry-run" triggering something' 2 \
    'REFUSED' 'check image jar_flag_probe' -- legout p_jar_ro2
legrun p_jar_nc "${GS[@]}" GSVTK_READ_ONLY=0 ./gsvtk check image jar_flag_probe --image "$JAR"
expect 'C8: out of read-only, the SECOND gate the scan used to skip is still there: the same launcher
       without --confirm is refused by the confirm gate' 2 'mutating modes need --confirm' \
    -- legout p_jar_nc
legrun p_run_free "${GS[@]}" GSVTK_READ_ONLY=0 ./gsvtk check image run_in_image --dry-run --image "$JAR" \
    --probe "$CD/c6-probe.sh"
expect 'C8: CONTROL -- with read-only EXPLICITLY off, run_in_image --dry-run is still plan-only and skips
       BOTH gates: that is the exemption the per-launcher rule exists to keep, not an accident of it' 0 \
    'plan-only mode' '--dry-run: nothing was created' -- legout p_run_free
expect 'C8: ... and it skipped the confirm gate honestly: no confirm refusal on a mode that changes
       nothing' 0 'no-hit=1' -- nohit 'need --confirm' "$CD/p_run_free.out"
legrun p_jar_reach "${GS[@]}" GSVTK_READ_ONLY=0 ./gsvtk check image jar_flag_probe --dry-run --image "$JAR" --confirm
expect 'C8: the premise, measured: past BOTH gates jar_flag_probe receives the token and rejects it in
       ITS OWN words (`unknown arg $1`, no colon) -- that run is the launcher stating it has no such
       mode, which is what makes "decide per launcher" the honest shape' 2 'unknown arg --dry-run' \
    -- legout p_jar_reach
legrun p_svs_reach "${GS[@]}" GSVTK_READ_ONLY=0 ./gsvtk check image svshell_image_check --dry-run --image "$JAR" \
    --confirm
expect 'C8: ... and the same for the sv-shell check, whose parser spells the refusal differently
       (`unknown arg: $1`). Both launchers are run; neither is taken on trust' 2 \
    'unknown arg: --dry-run' -- legout p_svs_reach
legrun p_smuggle "${GS[@]}" GSVTK_READ_ONLY=1 ./gsvtk check image run_in_image --image "$JAR" \
    --probe "$CD/c6-probe.sh" --run 'echo --dry-run'
expect 'C8: a payload cannot smuggle an exemption: --run "echo --dry-run" carries the word for the
       docker layer, not for the gate, so the ONE launcher that has a plan mode still needs the token
       typed as its own argument' 2 'REFUSED' 'check image run_in_image' -- legout p_smuggle
expect 'C8: ZERO gcloud calls across the whole section -- every refusal happened at the gate and every
       dispatch stopped at --dry-run or at a parser. The count IS the claim: "no instance was created"
       is what this fix was for' 0 'hits=0/0' -- stubcount 0 'gcloud '

# --- --help output and the image guard's portability ----------------------------------------------
# --help must not print a fixed line range that can truncate the usage text, and image_ref.sh must
# not use `tr` (GNU and BSD disagree on descending ranges).
# c9_uses_tr FILE — print USES-tr if a whole-word `tr` appears on a non-comment line, else NO-tr.
c9_uses_tr() {
    awk '!/^[[:space:]]*#/ { if ($0 ~ /(^|[^A-Za-z0-9_])tr([[:space:]]|$)/) { print "USES-tr"; ok=1; exit } }
         END { if (!ok) print "NO-tr" }' "$1"
}

SVS=checks/image-check/svshell_image_check.sh
JFP=checks/image-check/jar_flag_probe.sh
expect 'C9: svshell --help names the flags it implements -- --image, --keep, and the --timeout that was missing' 0 \
    '--image' '--keep' '--timeout' -- bash "$SVS" --help
expect 'C9: ... and the jar probe --help names --image' 0 '--image' -- bash "$JFP" --help
expect 'C9: CONTROL against truncation -- the LAST line of svshell header survives, so the window is self-bounding' 0 \
    'no marker' -- bash "$SVS" --help
expect 'C9: ... and the jar probe header tail survives too (a window that truncated would lose this one)' 0 \
    'name the image whose jar' -- bash "$JFP" --help
expect 'C9: the shared guard derives its character set WITHOUT shelling out to tr (GNU vs BSD ranges)' 0 \
    'NO-tr' -- c9_uses_tr checks/image-check/image_ref.sh
expect 'C9: CONTROL -- that file really is the guard (it holds the bash pattern), so the leg above is not' 0 \
    'a-zA-Z0-9' -- grep -m1 'a-zA-Z0-9' checks/image-check/image_ref.sh
expect 'C9: the jar probe passes --zone on its serial-port poll (zonal instances; create/delete did, poll did not)' 0 \
    'get-serial-port-output' '--zone=' -- grep -m1 'get-serial-port-output' checks/image-check/jar_flag_probe.sh
expect 'C9: CONTROL -- the poll call is found at all, so that leg is about --zone and not about a renamed call' 0 \
    'get-serial-port-output' -- grep -m1 'get-serial-port-output' checks/image-check/jar_flag_probe.sh


echo
echo "selftest: fetch_outputs.py resolves by WORKFLOW OUTPUT NAME, offline, and refuses to invent one"
# Resolves outputs from Cromwell's `outputs` map in a metadata fixture. Nothing contacts a bucket:
# bytes come from a local --link-dir and the gsutil line is checked as --dry-run text.
FO="$TMP/fo"
mkdir -p "$FO/meta" "$FO/objects"
cat > "$FO/meta/run1.aaaa1111.json" <<'JSON'
{"workflowName": "GATKSVPipelineSingleSample", "id": "aaaa1111-2222-3333-3333-555566667777",
 "status": "Succeeded",
 "outputs": {"GATKSVPipelineSingleSample.svVCF": "gs://gsvtk-fixture/run1/sv.vcf.gz",
             "GATKSVPipelineSingleSample.pedFile": "gs://gsvtk-fixture/run1/pedfile.txt",
             "GATKSVPipelineSingleSample.depthRatioQCMatrix":
                 "gs://gsvtk-fixture/run1/depthRatioQCMatrix.txt",
             "GATKSVPipelineSingleSample.svDisensVCF": null}}
JSON
printf 'sv call lines\n' > "$FO/objects/sv.vcf.gz"
printf 'ped lines\n' > "$FO/objects/pedfile.txt"
FOMETA="$FO/meta/run1.aaaa1111.json"
FOWORK="$FO/work"
FOFOM="$FOWORK/manifests/outputs_manifest.json"
expect 'fetch_outputs --help needs no profile and no credentials' 0 \
    'Fetch a run'\''s artifacts by Cromwell output NAME' -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$FOWORK" "$PY" terra/fetch_outputs.py --help
check 'and --help created no scratch directory on the way out' \
    test ! -e "$FOWORK"
expect 'an output name the run never declared: refused, and the declared names are printed' 3 \
    "declares no output named 'GATKSVPipelineSingleSample.svAnnotateVcf'" \
    'GATKSVPipelineSingleSample.svVCF' -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$FOWORK" "$PY" terra/fetch_outputs.py \
    --metadata "$FOMETA" --output GATKSVPipelineSingleSample.svAnnotateVcf
expect 'declared-with-no-value is a DIFFERENT answer than a wrong name (exit 4, and it says so)' 4 \
    'IS declared but carries no value' 'This is not "the workflow has no such output" (exit 3)' \
    -- env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$FOWORK" "$PY" terra/fetch_outputs.py \
    --metadata "$FOMETA" --output GATKSVPipelineSingleSample.svDisensVCF
check 'neither refusal wrote a manifest: a manifest with a hole in it is not a result' \
    test ! -e "$FOFOM"
expect '--dry-run prints the exact command a fetch would run, labelled as needing real cloud access' 0 \
    'gsutil -m cp -n gs://gsvtk-fixture/run1/depthRatioQCMatrix.txt' \
    'needs real cloud access' -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$FOWORK" "$PY" terra/fetch_outputs.py \
    --metadata "$FOMETA" --output depthRatioQCMatrix --dry-run
check 'and --dry-run put no bytes on disk' test ! -e "$FOWORK/outputs/depthRatioQCMatrix.txt"
expect 'present: the output is copied (offline, from a local capture) and hashed' 0 \
    'adopted from --link-dir' 'sha256=' -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$FOWORK" "$PY" terra/fetch_outputs.py \
    --metadata "$FOMETA" --output GATKSVPipelineSingleSample.svVCF --link-dir "$FO/objects"
check 'the manifest row is keyed by the WORKFLOW OUTPUT NAME, not by a bucket path' \
    command grep -qF '"GATKSVPipelineSingleSample.svVCF"' "$FOFOM"
expect 'a second --output appends its line and keeps the first' 0 \
    '1 fetched this run, 1 carried from the previous manifest' -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$FOWORK" "$PY" terra/fetch_outputs.py \
    --metadata "$FOMETA" --output pedFile --link-dir "$FO/objects"
check 'and both output names are still in that manifest (append, never replace)' \
    test "$(command grep -cF '"gs://' "$FOFOM")" -eq 2
expect '--verify re-hashes what the manifest recorded and calls it clean' 0 \
    'verify: 2 recorded, 0 problem(s)' -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$FOWORK" "$PY" terra/fetch_outputs.py --verify
printf 'tampered bytes\n' > "$FOWORK/outputs/pedfile.txt"
expect 'and the same --verify finds one tampered file, rather than exiting 0' 6 'DRIFT' -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$FOWORK" "$PY" terra/fetch_outputs.py --verify

# A SIGPIPE guard is evidence only if its unguarded twin fails on the same input (exit 141 vs 0).
expect 'the sigpipe pair shows the guard changing the exit code, and refuses a pair that shows nothing' 0 \
    'PAIRED' 'VACUOUS' '2 ok, 0 failed' -- bash checks/sigpipe_pair.sh --selftest

# Cost from run metadata: cpu "4" for 30 min + cpu 2 for 60 min = 4.00 vCPU-hours, $0.12 on-demand,
# $0.06 spot. Cromwell renders cpu as a string, so that case is covered.
"$PY" - "$TMP/ce-meta.json" "$TMP/ce-rates.json" "$TMP/ce-nosource.json" <<'CEEOF'
import json, sys
json.dump({"status": "Succeeded", "calls": {"W.X": [
    {"shardIndex": 0, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T12:30:00Z",
     "runtimeAttributes": {"cpu": "4"}},
    {"shardIndex": 1, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T13:00:00Z",
     "runtimeAttributes": {"cpu": 2}},
    {"shardIndex": 2, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T12:10:00Z",
     "runtimeAttributes": {}}]}}, open(sys.argv[1], "w"))
json.dump({"source": "selftest-fixture", "as_of": "2026-10-09", "vcpu_hour": 0.03,
           "vcpu_hour_spot": 0.015}, open(sys.argv[2], "w"))
json.dump({"vcpu_hour": 0.03}, open(sys.argv[3], "w"))
CEEOF
expect 'a run is priced in dollars from its own metadata, string cpu included, and the unknown call is not zero' 0 \
    '4.00 vCPU-hours' '$0.12' '$0.06' 'UNKNOWN, not summed as zero: 1' -- \
    "$PY" checks/cost_estimate.py --metadata "$TMP/ce-meta.json" --rates "$TMP/ce-rates.json"
expect 'a rates file that cannot name its source and date is refused, not printed' 4 'RATES UNUSABLE' 'as_of' -- \
    "$PY" checks/cost_estimate.py --metadata "$TMP/ce-meta.json" --rates "$TMP/ce-nosource.json"
expect 'and RAM not being in this metadata is stated as a floor, not silently priced' 0 'FLOOR' -- \
    "$PY" checks/cost_estimate.py --metadata "$TMP/ce-meta.json" --rates "$TMP/ce-rates.json"
# Fixture count pinned as a literal so a suite that lost checks cannot pass on "0 failed".
expect 'the cost checker itself runs on the gate with its fixture count' 0 '11 ok, 0 failed' -- \
    "$PY" checks/cost_estimate.py --selftest

# Preemption is infrastructure (resubmit); a nonzero task exit implicates the launch (fix code).
"$PY" - "$TMP/fc-preempt.json" "$TMP/fc-nonzero.json" "$TMP/fc-flat.json" <<'FCEOF'
import json, sys
inner = {"calls": {"W.MakeCohortVcf.CombineBatches.ExtractSRVariantLists": [{
    "shardIndex": 18, "returnCode": 0, "failures": [{"message":
        "Task X.ExtractSRVariantLists:18:1 failed. Job exited without an error, exit code 0. "
        "GCP Batch task exited with VMPreemption(50001)."}]}]}}
scope = {"shardIndex": 18, "returnCode": None, "subWorkflowMetadata": inner}
echo = {"shardIndex": -1, "failures": [{"message": "Workflow failed"}]}
json.dump({"status": "Failed", "calls": {"W.MakeCohortVcf": [scope, echo]}}, open(sys.argv[1], "w"))
json.dump({"status": "Failed", "calls": {"W.CleanVcf": [
    {"shardIndex": 3, "returnCode": 1, "failures": [{"message": "return code 1"}]}]}}, open(sys.argv[2], "w"))
json.dump({"status": "Failed", "calls": {"W.MakeCohortVcf": [{"shardIndex": -1, "failures": []}]}},
          open(sys.argv[3], "w"))
FCEOF
expect 'a preempted task that exited 0 is infrastructure, and says a code change would find nothing' 0 \
    'INFRASTRUCTURE' 'backend lost the VM' -- \
    "$PY" checks/failure_cause.py --metadata "$TMP/fc-preempt.json"
expect 'but a task that really exits nonzero indicts the launch instead' 3 'BODY IMPLICATED' -- \
    "$PY" checks/failure_cause.py --metadata "$TMP/fc-nonzero.json"
expect 'and metadata without expanded sub-workflows refuses rather than reporting a clean body' 4 \
    'NOTHING GRADED' 'expand_sub_workflows' -- \
    "$PY" checks/failure_cause.py --metadata "$TMP/fc-flat.json"
expect 'and the checker itself runs on the gate with its fixture count' 0 '5 ok, 0 failed' -- \
    "$PY" checks/failure_cause.py --selftest

# An output under /cacheCopy/ came from an earlier run, so the code under test never executed.
"$PY" - "$TMP/md-cached.json" "$TMP/md-fresh.json" <<'FOEOF'
import json, sys
json.dump({"id": "wf1", "status": "Succeeded",
           "outputs": {"W.final_vcf": "gs://b/run/x/final.vcf.gz",
                       "W.sv_calls": "gs://b/wf1/call-abc/cacheCopy/sv.vcf", "W.n": "7"}}, open(sys.argv[1], "w"))
json.dump({"id": "wf2", "status": "Succeeded",
           "outputs": {"W.final_vcf": "gs://b/run/y/final.vcf.gz", "W.n": "7"}}, open(sys.argv[2], "w"))
FOEOF
expect 'a cache-served output is counted, named, and refused by --refuse-cached' 7 \
    'resolve into a call-cache copy' 'CACHED  W.sv_calls' '1 of 3 declared output(s)' -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$FOWORK" "$PY" terra/fetch_outputs.py \
    --metadata "$TMP/md-cached.json" --refuse-cached
expect 'and a run that wrote its own paths passes without being asked what to fetch' 0 \
    '0 of 2 declared output(s)' 'nothing was fetched' -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$FOWORK" "$PY" terra/fetch_outputs.py \
    --metadata "$TMP/md-fresh.json" --refuse-cached

echo
echo "selftest: probes for the defects a review confirmed (offline, no network, no creds)"
# scripts/probe_fixes.py pins defects that were reproduced before being fixed; its module docstring
# lists them. The floor is 25 when the machine has every prerequisite (CI does); otherwise it is
# conservation-only: run + skipped equals the declared total, nothing fails, and each skip names
# what it needs.
PROBE_MIN=25
if [ "$HAS_WDL" = 0 ] || ! command -v gcloud >/dev/null 2>&1; then
    PROBE_MIN=0
    printf '  NOTE  probe floor is conservation-only for THIS run: no WDL module and/or no gcloud on PATH,\n'
    printf '        so probes name a prerequisite and skip. What is still asserted: the declared total equals\n'
    printf '        ran + skipped, nothing failed, and every skip names what it needs. CI holds 25 of 25.\n'
fi
probecount "scripts/probe_fixes.py pins every confirmed defect with a control" "$PROBE_MIN" \
    "$PY" scripts/probe_fixes.py
printf '        (each probe also asserts a POSITIVE CONTROL, so a guard that cannot fire is a\n'
printf '        FAIL rather than a pass -- see the module docstring for what each one pins)\n'

echo
# The selftest count in `make help` must match: with no skips, ok equals the claim; with skips,
# ok plus named skips must, so a vanished check cannot hide among them.
CLAIM="$(command grep -o '[0-9][0-9]* selftests' Makefile | command grep -o '[0-9][0-9]*' | head -1)"
if [ -n "$CLAIM" ]; then
    if [ "$skip" -eq 0 ] && [ "$CLAIM" != "$ok" ]; then
        fail=$((fail + 1))
        printf '  FAIL  make help claims %s selftests and %s ran\n' "$CLAIM" "$ok"
        printf '        the claim is documentation that rots silently; change the number in the Makefile,\n'
        printf '        and only upward if what you added is an assertion (a probe you deleted is a finding)\n'
    elif [ "$skip" -gt 0 ] && [ "$((ok + skip))" != "$CLAIM" ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s ran + %s skipped = %s, but make help claims %s\n' "$ok" "$skip" \
            "$((ok + skip))" "$CLAIM"
        printf '        a skipped check is honest only if it is NAMED and COUNTED: the skips were%s\n' \
            "${skiplist:+ ($skiplist)}"
        printf '        an uncounted check in a SKIP branch is how a selftest disappears quietly\n'
    elif [ "$skip" -gt 0 ]; then
        printf '  ok    make help claims %s: %s ran + %s skipped for a named reason%s\n' \
            "$CLAIM" "$ok" "$skip" "$skiplist"
        printf '        (skips are listed so a check that vanished cannot hide among them; install\n'
        printf '        miniwdl / jq / a gatk-sv clone to make this phase assert at full strength)\n'
    fi
fi
printf 'selftest: %s ok, %s skipped, %s failed\n' "$ok" "$skip" "$fail"
# Declared and reached call sites must match when nothing skipped. A deleted call site drops from
# both, so the pair cannot catch deletions.
gsvtk_declared_report "selftest" "$SELF" "$((ok + fail))" "$skip" || fail=$((fail + 1))
[ "$fail" -eq 0 ]
