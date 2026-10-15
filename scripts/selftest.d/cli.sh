#!/usr/bin/env bash
# scripts/selftest.d/cli.sh — assertions for ./gsvtk, the repo's command-line entry point.
#
# Standalone:  bash scripts/selftest.d/cli.sh .venv/bin/python
# Wired into the gate by ONE line in the Makefile's `selftest` target, which is why this file prints
# its own tally instead of folding into scripts/selftest.sh's count.
#
# Why a separate file: scripts/selftest.sh is 762 lines with a pinned check count and a canary; this
# suite is about a dispatcher whose whole job is choosing which OTHER file to run, so nearly every
# assertion here needs a stand-in tree. Keeping it here also keeps the merge narrow.
#
# What is actually asserted, and why each one would fail for a real reason:
#
#   * --help / no-args with an EMPTY profile. An entry point that touches the resolver on its way to
#     printing usage fails for the first thing a new user does, which is why helpsweep grades --help
#     too; this adds the exit-code convention (--help 0, no-args 2, refused 2, missing dependency 3).
#   * every mutating mode refused under GSVTK_READ_ONLY=1 with the dependency GENUINELY ABSENT
#     (GSVTK_TERRA_PY=/usr/bin/false, PATH stripped to /usr/bin:/bin, so it is /bin/bash 3.2 running
#     it). If the whitelist were checked after the interpreter probe, these would print "no
#     interpreter with firecloud" instead of a refusal, and a refusal that depends on what is
#     installed is a claim about the machine, not about the contract.
#   * POSITIVE CONTROLS for that: eight of them, in two tiers. Six run under the same stripped PATH with
#     firecloud present (a benign read-only dispatch, a plan-only download, the free build plan, a
#     comparator, an image-check --dry-run, the rerun body printer), and two run under the IDENTICAL shim
#     the refusals above use — no firecloud, no venv — where a comparator and the build plan still reach
#     their script.
#     Without the controls, 17 refusals are also what a CLI that cannot parse its own arguments, or
#     cannot find its interpreter, reports.
#   * each subcommand reaches the RIGHT script: the stand-ins echo the resolved command line, so the
#     assertion is about the path and the arguments, not about a stamp alone. Nothing here touches the
#     network: no stub imports anything, and the Terra stubs never load terra.py.
#   * flags are DISPATCHED, not forwarded: `check`'s --compare-to reaching svshell_contract_check.py
#     (which has no such flag) is an argparse error that reads like a broken checker, so one assertion
#     requires the line WITHOUT it.
#   * the free/mutating split the entry point PRINTS is graded by placement, not by a needle. The block an
#     unknown mode answers with holds both halves, so a mode name matches whichever side it is on; the one
#     name where the two halves are a single edit apart is rerun-show, whose tool owns three POSTers.
#   * the outer gate is additive: freeze's `--write` must arrive with the user's --confirm consumed,
#     and batch_configs' own `--confirm` must arrive forwarded. One assertion per direction.
#   * a mutating mode prints where/what-it-costs/how-to-undo BEFORE acting, and without --confirm
#     exits 2 naming --confirm and dispatching nothing.
#   * the replay preflight measures: java major 17 passes, java 8 is refused as TOO OLD, a missing jar
#     is refused by name, and an empty jar is refused. All four run against stub `java`/`bcftools`
#     binaries in a temp bin, so the result does not depend on the JDK of whoever runs the gate — the
#     machine that runs this suite is not the machine that decides what a trainer needs.
#   * the LAST block leaves the stand-in tree, because `terra/batch_status.py --costs` is the one place
#     the CLI's job ends and a NUMBER begins: what a config outside the chain is priced at, what the
#     TOTAL line is allowed to claim, and that a cost this tool cannot read prints as unknown instead
#     of being summed as zero. It drives the real `batch_status.main` against a fake that answers the
#     three Terra calls the tool makes and raises on every other one, and it byte-compares the
#     no-`--costs` table against the same file at b5c0d03 through that same fake. That is a wider
#     charter than "which script did the CLI pick", and it is here rather than in a new phase because
#     this lane was told the legs go in this file; a new scripts/selftest.d/*.sh would also have meant
#     a line in SELFTEST_PHASES, which is shared ground.
#
# bash 3.2 compatible. Nothing here writes outside a temp dir, needs credentials, or uses the network:
# the Terra legs answer from a fixture, every other `fapi` verb is a raiser, and the closed-port
# GSVTK_TERRA_API_ROOT is the belt — graded by one leg that runs the REAL tool against it and requires
# a named failure rather than an empty table. (Reading this machine's ambient ADC to BUILD a request is
# not a request; that is also all scripts/selftest.d/rerun.sh's wire legs do.)

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
# GSVTK_TERRA_PY wants an ABSOLUTE interpreter: the stand-in tree has no .venv of its own.
# Three shapes, not two. A slash-free name is a PATH lookup, NOT a file in this directory:
# `dirname python3` is ".", so the two-arm version of this case turned "python3" into
# "<repo root>/python3" -- a path that exists on no machine, which is exactly what CI handed this
# file (its runner has the gate's dependencies installed into the interpreter `python3` resolves to,
# so $(PYTHON) arrives as a bare name and no ./.venv exists to rescue it). Five controls then died
# with "no interpreter with firecloud" while the same file was green on a laptop whose $(PYTHON) is
# "./.venv/bin/python" -- a path, with a slash, that the old case absolutized correctly. That is the
# whole defect: the file was portable only to machines that happen to own a venv.
# scripts/selftest.d/rerun.sh's leg below asserts this resolves, so the next shape that is not a
# path stops five controls downstream is named here instead.
case "$PY" in
  /*)   PYABS="$PY" ;;
  */*)  PYABS="$(cd "$(dirname "$PY")" && pwd -P)/$(basename "$PY")" ;;
  *)    PYABS="$(command -v "$PY" 2>/dev/null || printf '%s' "$PY")" ;;
esac

ok=0; skip=0; fail=0; skiplist=""
TMP="$(mktemp -d "${TMPDIR:-/tmp}/gsvtk-cli.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"                       # the EMPTY profile every run below points at

# ------------------------------------------------- declared vs reached (docs/gap-ledger.md C10)
# Both numbers count assertion call SITES, and the pair requires the two SETS to be equal: this file is the
# biggest phase in the gate (117 verdicts) and the one where a block orphaned by a refactor is easiest to lose.
# The callees are what gsvtk_declared_funset derives from this file — every function that records a verdict and
# is called from top-level code — re-derived by the reporter on every run, so a sixth recorder that skips the
# accounting is named instead of silently dropping the reached count. `skipped` is not among them: it counts
# bails, not verdicts. The 117 this phase prints against 101 sites is not an error and not a loop either,
# measured against the ledger (101 writes, 101 distinct): the terra-refusal sweep over sixteen modes records
# sixteen verdicts inline in its loop body, outside every recorder, and the reporter prints both totals so the
# difference is stated rather than hidden. The ledger file IS the set of reached sites, because bash 3.2
# has no associative arrays; if it cannot be written the phase fails loudly instead of reaching nothing.
SELF="$ROOT/scripts/selftest.d/cli.sh"
. "$ROOT/scripts/selftest.d/declared.sh"
GSVTK_DECL_FUNCS="check expect absent line_absent count"
gsvtk_declared_init "$TMP"
SAVED_PATH="$PATH"

# The two harness helpers are COPIED from scripts/selftest.sh, not sourced: that file IS the gate, and
# running it from here would be a recursive gate inside the gate (check_doc_flags.py carries a note
# about exactly that trap, found by timing a scan at five minutes). Same rules: `expect` needs at
# least one NEEDLE, because an assertion that names no string cannot fail for the right reason.
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

expect() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
    local desc="$1" want="$2"; shift 2
    local needles=()
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do needles+=("$1"); shift; done
    [ "$#" -gt 0 ] && shift
    local out rc missing="" n
    out="$("$@" 2>&1)"; rc=$?
    if [ "${#needles[@]}" -eq 0 ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s (expect was called with no NEEDLE: assert the words, not just the code)\n' "$desc"
        return
    fi
    for n in "${needles[@]}"; do
        printf '%s\n' "$out" | grep -qF -- "$n" || missing="$missing[$n] "
    done
    if [ "$rc" -ne "$want" ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s, want %s%s)\n' "$desc" "$rc" "$want" "${missing:+, and said none of}"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    elif [ -n "$missing" ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s is right but the output never says %s)\n' "$desc" "$rc" "$missing"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# absent DESC WANT_RC NEEDLE CMD... — the needle must NOT appear and the exit must be WANT_RC. The half of a
# "flags belong to different tools" claim that `expect` cannot make: the flag being PRESENT somewhere
# is what proves nothing, because it is present for the tool that owns it.
absent() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
    local desc="$1" want="$2" needle="$3"; shift 3
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done   # the same `--` marker expect uses
    [ "$#" -gt 0 ] && shift
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    if [ "$rc" -ne "$want" ]; then
        fail=$((fail + 1)); printf '  FAIL  %s (exit %s, wanted %s)\n' "$desc" "$rc" "$want"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    elif printf '%s' "$out" | grep -qF -- "$needle"; then
        fail=$((fail + 1)); printf '  FAIL  %s — the output says %s\n' "$desc" "$needle"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# line_absent DESC LINE-PATTERN NEEDLE CMD... — the tool identified by LINE-PATTERN ran, and did not
# receive NEEDLE. Whole-output `absent` cannot express this when two tools are dispatched at once.
line_absent() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
    local desc="$1" pat="$2" needle="$3"; shift 3
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done
    [ "$#" -gt 0 ] && shift
    local out line
    out="$("$@" 2>&1)"
    line="$(printf '%s\n' "$out" | grep -F -- "$pat" | head -1)"
    if [ -z "$line" ]; then
        fail=$((fail + 1)); printf '  FAIL  %s — no line matched %s, so the tool never ran\n' "$desc" "$pat"
        printf '%s\n' "$out" | head -8 | sed 's/^/          /'
    elif printf '%s' "$line" | grep -qF -- "$needle"; then
        fail=$((fail + 1)); printf '  FAIL  %s — that line says %s: %s\n' "$desc" "$needle" "$line"
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

skipped() {
    skip=$((skip + 1)); skiplist="$skiplist $1"
    printf '  SKIP  %s\n' "$2"
}

# count DESC PATTERN WANT CMD... — how many output LINES contain PATTERN. `expect` can only prove a
# needle is present, and "one tree load answers N names" is a claim about a COUNT: one call carrying
# both flags and two calls each carrying one both contain the same needles. grep -c exits 1 when it
# finds nothing, so its status is ignored and only the printed number is read (bash 3.2, no set -e).
count() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see check() above
    local desc="$1" pat="$2" want="$3"; shift 3
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do shift; done   # the same `--` marker expect uses
    [ "$#" -gt 0 ] && shift
    local out got
    out="$("$@" 2>&1)"
    got="$(printf '%s\n' "$out" | grep -cF -- "$pat")"
    if [ "${got:-0}" -ne "$want" ]; then
        fail=$((fail + 1))
        printf '  FAIL  %s — %s line(s) contain %s, wanted exactly %s\n' "$desc" "${got:-0}" "$pat" "$want"
        printf '%s\n' "$out" | head -8 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# ---------------------------------------------------------------- the stand-in tree
# A copy of the entry point plus a kit/ and one stand-in per dispatched script. The stand-ins print
# the path they were reached as and the arguments they were given -- which is the whole point: the
# claim under test is "the CLI resolved to THIS file with THESE flags", and a stub that echoed
# nothing could not tell a right dispatch from a refusal.
FAKE="$TMP/kit"
mkdir -p "$FAKE"/{checks/image-check,terra,docker,compare,replay,examples,scripts}
cp -R "$ROOT/kit" "$FAKE/" 2>/dev/null || true
rm -rf "$FAKE/kit/__pycache__"
cp "$ROOT/gsvtk" "$FAKE/gsvtk"
chmod +x "$FAKE/gsvtk"
# Canonicalise: $TMPDIR can carry a trailing slash, and the CLI answers with `pwd -P` of its own directory, so
# a needle built from the un-canonicalised path would not match the path the CLI prints.
FAKE="$(cd "$FAKE" && pwd -P)"

stub_py() {   # stub_py <relative/path.py>
    printf '#!/usr/bin/env python3\nimport os, sys\nprint("STUB %s " + " ".join(sys.argv[1:]))\n' \
      "$1" > "$FAKE/$1"
    printf 'sys.exit(int(os.environ.get("STUB_RC", "0")))\n' >> "$FAKE/$1"
}
stub_sh() {   # stub_sh <relative/path.sh>
    printf '#!/usr/bin/env bash\necho "STUB %s $*"\nexit "${STUB_RC:-0}"\n' "$1" > "$FAKE/$1"
    chmod +x "$FAKE/$1"
}
for f in terra/recon.py terra/batch_configs.py terra/batch_freeze.py terra/batch_rerun_step.py \
         terra/batch_status.py terra/batch_cost.py terra/batch_peek.py terra/batch_check_inputs.py \
         terra/batch_save_metadata.py terra/fetch_outputs.py checks/svshell_contract_check.py \
         checks/svshell_jq_plumbing_scan.py checks/wdl_semantics.py checks/wdl_reach.py \
         checks/terra_entity_check.py \
         compare/table_diff.py compare/compare_batch_tables.py compare/artifact_tally.py \
         replay/build_inputs.py examples/recompute_het_population.py; do stub_py "$f"; done
for f in docker/gatk-sv-build.sh terra/batch_fetch_compare.sh checks/wdl_gate.sh \
         checks/image-check/run_in_image.sh checks/image-check/svshell_image_check.sh \
         checks/image-check/jar_flag_probe.sh examples/run_train_chr20.sh \
         examples/run_train_full.sh examples/run_train_definitive.sh \
         examples/run_rd_population_probe.sh examples/replay_reference_run.sh; do stub_sh "$f"; done

CLI="$FAKE/gsvtk"
# Every run below points GSVTK_CONFIG at an EMPTY file: no profile, no credentials, no defaults that
# belong to this machine. A tool that needs a value must exit 4 naming the key, and that is what makes
# "refused" and "unconfigured" two different answers with two different exit codes.
run() {
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_TERRA_PY="$PYABS" \
        bash "$CLI" "$@"
}
# The same CLI with the dependency GENUINELY ABSENT: an interpreter that certainly cannot import
# firecloud, a PATH with no venv in it, and `env -i` so nothing from this shell leaks in. On macOS
# /bin/bash is 3.2, so this is also the bash-3.2 proof for the paths under test.
run_bare() {
    env -i PATH=/usr/bin:/bin HOME="$HOME" GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
        GSVTK_TERRA_PY=/usr/bin/false bash "$CLI" "$@"
}
# The same bare environment WITH the read-only stamp — the shim every refusal below runs under.
# Why a second bare variant exists: if the read-only whitelist were consulted AFTER the interpreter
# probe, every refusal in this file would turn into "no interpreter with firecloud" and the suite
# would be proving that this machine lacks a module instead of proving that the CLI refuses. With the
# dependency genuinely absent, a refusal that still names the mode and the command to run by hand can
# only have come from the policy.
run_bare_ro() {
    env -i PATH=/usr/bin:/bin HOME="$HOME" GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
        GSVTK_TERRA_PY=/usr/bin/false GSVTK_READ_ONLY=1 bash "$CLI" "$@"
}
# The CONTROL pair to run_bare_ro: the identical stripped environment, but the interpreter CAN import
# firecloud, so any difference in outcome can only have come from the mode being refused and not from
# the machine. (The free modes are controlled under run_bare_ro itself, further down, because they
# need no interpreter at all — that is the tighter pair: same shim, same missing module.)
run_readonly() {
    env -i PATH=/usr/bin:/bin HOME="$HOME" GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
        GSVTK_TERRA_PY="$PYABS" GSVTK_READ_ONLY=1 bash "$CLI" "$@"
}

echo
echo "selftest: ./gsvtk — the entry point, against a stand-in tree (offline, no credentials)"
echo

# First leg, because everything below this file's shim trusts it: `run`, `run_readonly` and the wire legs
# all hand $PYABS to the CLI as GSVTK_TERRA_PY, and `gsvtk`'s probe runs it under an `env -i PATH=/usr/bin:/bin`
# shim. A $PYABS that is not absolute resolves against the shim's CWD (the stand-in tree), and one that is
# absolute but absent fails the `import firecloud` probe, so the CLI dies naming its own fallback and five
# controls read as a broken machine. Measured, not assumed: CI's `$(PYTHON)` is the bare name `python3`, the
# old two-arm case made it "<root>/python3", and the legs went red here while a laptop with ./.venv stayed
# green. This leg is the version of that failure that names itself.
expect 'the interpreter this file hands the CLI is an ABSOLUTE path to a file that is executable' 0 \
    'absolute and executable' -- bash -c '\
        case "$1" in /*) : ;; *) printf "not absolute: [%s] (a shim with a stripped PATH resolves it against the stand-in tree)\n" "$1" >&2; exit 1 ;; esac
        [ -x "$1" ] || { printf "not executable: %s (GSVTK_TERRA_PY must be a real interpreter, not a name made from dirname)\n" "$1" >&2; exit 1; }
        printf "absolute and executable: %s\n" "$1"' _ "$PYABS"

echo "selftest: cli: usage and exit codes with an EMPTY profile"
expect '--help works with an empty profile and prints the subcommands' 0 \
    'gsvtk <command>' 'compare <name>' 'replay <mode>' -- run --help
expect '-h is the same page' 0 'gsvtk <command>' -- run -h
expect 'help is the same page' 0 'gsvtk <command>' -- run help
expect 'no-args prints the usage and exits 2 (the convention --help is the only 0)' 2 \
    'gsvtk <command>' -- run
expect 'a global flag before the command does not need a profile' 0 \
    'read-only' -- run --read-only --help
expect 'an unknown command is refused by name, not passed to a script' 2 \
    'unknown command "frobnicate"' -- run frobnicate
expect 'the usage states the exit-code table it keeps' 0 '4 missing required config' -- run --help
check 'repo prints the checkout path and nothing else' \
    bash -c '[ "$($1 repo 2>/dev/null)" = "'"$(cd "$FAKE" && pwd -P)"'" ]' _ "$CLI"
expect 'version prints the checkout identity (sha, not a magic number)' 0 \
    'repo CLI @' -- run version
expect 'doctor --redact works unconfigured and names the key that has no default' 0 \
    'GSVTK_PROJECT' -- run doctor --redact
expect 'doctor refuses a misspelled flag rather than printing live values' 2 \
    'Refusing to print values' -- run doctor --redacted
expect 'tools still reports the same fields it always did' 0 \
    'checkout' 'terra' 'wdl_gate' 'build' 'inputs' -- run tools
expect 'locate still reports the venv and the tool inventory' 0 \
    'venv' 'tool ' -- run locate

echo
echo "selftest: cli: every mutating mode refused with the dependency genuinely absent"
# /usr/bin/false cannot import firecloud, so IF the whitelist were tested after the interpreter probe
# every one of these would print "no interpreter with firecloud" instead of a refusal. The check that
# the output does NOT mention firecloud is what pins the order for real, on any machine.
# /usr/bin/false cannot import firecloud (see run_bare_ro), so IF the whitelist were tested after the
# interpreter probe every one of these would print "no interpreter with firecloud" instead of a
# refusal. The check that the output does NOT mention firecloud is what pins the order for real, on
# any machine — including one that has the module installed.
for mode in configs-create configs-validate freeze-copy freeze-attrs rerun-create rerun-validate \
            rerun-submit fetch fetch-compare copy submit attrs create validate profile ""; do
    out="$(run_bare_ro terra $mode 2>&1)"; rc=$?
    if [ "$rc" -ne 2 ]; then
        fail=$((fail + 1)); printf '  FAIL  terra %s: exit %s, want 2\n' "${mode:-(empty)}" "$rc"
        printf '%s\n' "$out" | head -5 | sed 's/^/          /'
    elif ! printf '%s' "$out" | grep -qi 'refus'; then
        fail=$((fail + 1)); printf '  FAIL  terra %s: exit 2 but no refusal printed\n' "${mode:-(empty)}"
        printf '%s\n' "$out" | head -5 | sed 's/^/          /'
    elif printf '%s' "$out" | grep -qi 'firecloud\|interpreter'; then
        fail=$((fail + 1))
        printf '  FAIL  terra %s: reported a missing INTERPRETER, so the whitelist is tested after\n' \
          "${mode:-(empty)}"
        printf '        the environment probe and "refused" is a claim about this machine\n'
        printf '%s\n' "$out" | head -5 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    terra %s refused before any dependency check\n' "${mode:-(empty)}"
    fi
done
expect 'and the refusal prints the exact command to run in the checkout' 2 \
    "cd $FAKE && python terra/batch_configs.py create" -- run_bare_ro terra configs-create
expect 'a boot-compute check mode is refused too (it boots a VM, which is not a check)' 2 \
    'REFUSED' 'check image run_in_image' -- run_bare_ro check image run_in_image \
    --image reg/foo:tag --probe /dev/null
expect 'so is the real build, even though --confirm was typed' 2 \
    'REFUSED' 'docker/gatk-sv-build.sh my-branch' -- run_bare_ro build my-branch --confirm
expect 'and a bulk download is refused by the same rule' 2 \
    'REFUSED' 'terra/fetch_outputs.py' -- run_bare_ro terra fetch --all
# The plan-token hole, pinned. A --dry-run / --check ANYWHERE in the argument list used to skip both gates
# at once, so a run stamped read-only reached a method-config write, an entity-attribute upload and
# `submit`. The exemption is now decided by the mode name, because a token is only a plan when the tool on
# the other end implements it -- and in all three shapes below it does not: batch_configs.py's positional
# loop discards unknown flags, batch_freeze.py gates on --write alone, batch_rerun_step.py rejects
# --dry-run outright. The two legitimate exemptions (fetch --dry-run, check image ... --dry-run) are pinned
# as dispatching above, so this cannot be satisfied by sealing every plan mode.
expect 'read-only + --dry-run: a mutating mode is still refused (configs-create)' 2 \
    'REFUSED' 'terra configs-create' -- run_readonly terra configs-create --dry-run --confirm
expect 'read-only + --dry-run: a submission is still refused (rerun-submit)' 2 \
    'REFUSED' 'terra rerun-submit' -- run_readonly terra rerun-submit --dry-run --confirm
expect 'read-only + --check --write: the attribute upload is still refused (freeze-attrs)' 2 \
    'REFUSED' 'terra freeze-attrs' -- run_readonly terra freeze-attrs --check --write
# The mirror of the three pins above. All four are needed because "the exemption is decided by the MODE
# NAME" fails in two directions, and a token can be misread as a classifier from either side: above a
# planning token wrongly exempts a POSTer, here a mutating token wrongly convicts a printer. rerun-show is
# the mode to grade it on, because the undo sentence a create pre-flight prints tells an operator to run
# it BEFORE re-POSTing (gsvtk:754) — and an operator doing that next to a rerun word is exactly the one
# carrying --confirm by habit. Refusing it, or printing a pre-flight that claims something will change,
# is the guard punishing the step it asks for. Exit 0 is what proves the dispatch happened: a refusal here
# would be exit 2, and the needle is the pre-flight's own header, the first line announce_write prints.
# The forwarding is NOT the `check --compare-to` defect the dispatch section warns about: this tool lists
# --confirm in its own usage, its parser accepts it in every position (terra/batch_rerun_step.py:723), and
# only create and submit read the result (:548, :597), so `show --confirm` is a deliberate no-op inside.
absent 'a free mode is free by NAME: rerun-show --confirm dispatches under the stamp, no pre-flight' \
    0 'MUTATING mode' -- run_readonly terra rerun-show --confirm

# The refusal must say so itself, not merely be indistinguishable from one by luck: the CLI states
# that it consulted no dependency, credential or config value. Asserting that sentence is what stops
# a future reordering from passing this section on the exit code alone.
expect 'the refusal states it is policy, not a broken environment' 2 \
    'no dependency, credential or' 'not a broken environment' -- run_bare_ro terra rerun-submit

echo
echo "selftest: cli: the controls — the same stripped environment still dispatches the free modes"
# Three controls, because 17 refusals are also what a CLI that cannot parse its own arguments prints.
expect 'CONTROL: a read-only terra mode reaches its script with the stripped PATH' 0 \
    'STUB terra/batch_configs.py show' -- run_readonly terra show
expect 'CONTROL: a plan-only download (--dry-run) is not bulk and still dispatches' 0 \
    'STUB terra/fetch_outputs.py --dry-run' -- run_readonly terra fetch --dry-run
expect 'CONTROL: build without --confirm IS the plan, so read-only runs it' 0 \
    'STUB docker/gatk-sv-build.sh --check my-branch' \
    'STUB docker/gatk-sv-build.sh --dry-run my-branch' -- run_readonly build my-branch
expect 'CONTROL: a comparator is never mutating, and dispatches under read-only' 0 \
    'STUB compare/table_diff.py a.tsv b.tsv' -- run_readonly compare table_diff a.tsv b.tsv
expect 'CONTROL: --dry-run on the VM-booting image check is free, so it dispatches' 0 \
    'STUB checks/image-check/run_in_image.sh --dry-run' -- run_readonly check image run_in_image \
    --dry-run --image reg/foo:tag --probe /dev/null
# rerun-show is the free name among the four that share terra/batch_rerun_step.py, and the name is the
# confusable one: rerun-submit POSTs and boots a fleet, rerun-show prints the body that submission would
# POST. The sweep above pins the three mutating siblings; nothing yet pinned THIS side of that one tool,
# and free-list membership is exactly what a dispatch under `run` (no stamp) cannot see — measured: a
# stamp-only refusal of rerun-show (`if read_only_on` in front of the whitelist) made this leg the ONLY
# red one in the run, with the unstamped dispatch leg printing `ok`. Under the stamp only
# membership in the free list survives, which is also what makes the undo sentence a create pre-flight
# prints ("print the current bodies FIRST (gsvtk terra show / gsvtk terra rerun-show)") executable rather
# than aspirational.
expect 'CONTROL: rerun-show is free by NAME, so the stamp still dispatches the rerun body printer' 0 \
    'STUB terra/batch_rerun_step.py show' -- run_readonly terra rerun-show
# The tighter control pair: the SAME shim the 17 refusals above ran under (env -i, PATH with no venv,
# an interpreter that cannot import firecloud). These reach their script, which proves the refusals
# above were policy: 17 refusals are also what a CLI in a broken environment prints, and a suite that
# only ever refuses in an environment where nothing could run has not distinguished the two.
expect 'CONTROL: the SAME bare shim still dispatches a comparator (no interpreter needed)' 0 \
    'STUB compare/table_diff.py a.tsv b.tsv' -- run_bare_ro compare table_diff a.tsv b.tsv
expect 'CONTROL: and the same bare shim still runs the free build plan (--check then --dry-run)' 0 \
    'STUB docker/gatk-sv-build.sh --check my-branch' \
    'STUB docker/gatk-sv-build.sh --dry-run my-branch' -- run_bare_ro build my-branch

echo
echo "selftest: cli: each subcommand reaches the right script (echoed command lines, no network)"
expect 'terra recon'     0 'STUB terra/recon.py'                        -- run terra recon
expect 'terra show'      0 'STUB terra/batch_configs.py show'           -- run terra show
expect 'terra check'     0 'STUB terra/batch_configs.py check'          -- run terra check --against some-ref
expect 'terra plan'      0 'STUB terra/batch_freeze.py plan'            -- run terra plan
expect 'terra verify'    0 'STUB terra/batch_freeze.py verify'          -- run terra verify
expect 'terra status'    0 'STUB terra/batch_status.py --costs'         -- run terra status --costs
expect 'terra cost'      0 'STUB terra/batch_cost.py --json'            -- run terra cost --json
expect 'terra peek'      0 'STUB terra/batch_peek.py --tail 20'         -- run terra peek --tail 20
expect 'terra inputs'    0 'STUB terra/batch_check_inputs.py --step 10' -- run terra inputs --step 10
expect 'terra save-metadata' 0 'STUB terra/batch_save_metadata.py'      -- run terra save-metadata
expect 'terra rerun-show dispatches mode-first (argv[0] decides --image)' 0 \
    'STUB terra/batch_rerun_step.py show' -- run terra rerun-show
# The CLI promises verbatim forwarding in the block it prints for an unknown mode ("Each mode forwards its
# own flags verbatim: --against, --image KEY=REF, --write, ... Nothing here adds a flag to a tool or drops
# one"), and rerun-show is the mode to grade that promise on: it carries the most flags of any free mode,
# and terra/batch_rerun_step.py answers an unknown option with `unknown option '--x'` and exit 1, so a CLI
# that ADDED a marker of its own would read as a broken tool. The step is 09, not the default 10, on
# purpose: that tool's own default is step 10, so a dropped --step is a rerun of a different step, and a
# needle naming only the file would never see it. Under the stamp because the printer is what you run
# WHILE read-only -- the flags a rerun really uses have to reach it without stepping out of the guard.
expect 'rerun-show under the stamp: the step and the image pin travel verbatim, mode still first' 0 \
    'STUB terra/batch_rerun_step.py show --step 09 --image gatk_docker=us.gcr.io/proj/ns/gatk:abc123' \
    -- run_readonly terra rerun-show --step 09 --image gatk_docker=us.gcr.io/proj/ns/gatk:abc123
expect 'every dispatch stamps the checkout it resolved, on stderr' 0 \
    "repo  $FAKE @" -- run terra status
expect 'compare dispatches the file stem verbatim' 0 \
    'STUB compare/table_diff.py --key id' -- run compare table_diff --key id
expect 'compare accepts the hyphen spelling of an underscore stem' 0 \
    'STUB compare/table_diff.py a b' -- run compare table-diff a b
expect 'compare --list names the comparators rather than inventing them' 0 \
    'compare/table_diff.py' 'compare/artifact_tally.py' -- run compare --list
# The header of that list names `gsvtk compare` in backticks, and it used to sit inside DOUBLE quotes
# -- where a backtick is a command substitution, not typography. The list printed "by the name  takes"
# and leaked "gsvtk: command not found" from a subshell looking for the entry point on PATH, while the
# assertion above still passed because it only read the file paths. Quote the typography, and assert
# the sentence that carries it.
expect 'and its header is TYPOGRAPHY, not a command substitution that runs gsvtk' 0 \
    'by the name `gsvtk compare` takes' -- run compare --list
absent 'so --list never shells out to a gsvtk that may not be on PATH' 0 \
    'command not found' -- run compare --list
expect 'compare refuses the reader MODULE and points at its CLI' 2 \
    'artifact_tally.py' -- run compare artifact
expect 'compare refuses a name that is not a comparator' 2 \
    'no comparator' -- run compare no-such-diff
expect 'replay inputs reaches build_inputs' 0 'STUB replay/build_inputs.py --wdl x.wdl' \
    -- run replay inputs --wdl x.wdl
expect 'replay het-population reaches the examples driver' 0 \
    'STUB examples/recompute_het_population.py' -- run replay het-population
expect 'an unknown terra mode is refused with both mode lists' 2 \
    'refusing "frobnicate"' 'rerun-show' 'fetch-compare' -- run terra frobnicate
# Those two lists are the read-only contract in the form an agent reads BEFORE deciding to bypass
# anything, and rerun-show is the name in them most worth grading: rerun-submit POSTs and boots a fleet,
# rerun-show prints the body that submission would POST, both belong to terra/batch_rerun_step.py, and a
# mis-sweep of the rerun family therefore lands on exactly one of the two lines. The leg above proves the
# word APPEARS, which is all a needle can do here — the block prints BOTH halves, so 'rerun-show' matches
# whether it sits under "dispatch freely" or under the mutating list. Which half it is on is the entire
# claim, so the assertion is a slice: the name must be inside the free block, and must not appear from the
# mutating header onwards. It runs under the same bare shim the 17 refusals use, because this block is
# printed before the dependency probe — on the machine with no firecloud, which is the machine that most
# needs the list to be right.
rerun_show_listed_free() {
    local out free_line mutate_line
    out="$(run_bare_ro terra 2>&1)"
    free_line="$(printf '%s\n' "$out" | grep -nF 'read-only modes (dispatch freely)' | head -1 | cut -d: -f1)"
    mutate_line="$(printf '%s\n' "$out" | grep -nF 'mutating modes' | head -1 | cut -d: -f1)"
    if [ -z "$free_line" ] || [ -z "$mutate_line" ] || [ "$free_line" -ge "$mutate_line" ]; then
        printf 'the printed block lost a half or its order (free header at %s, mutating at %s):\n%s\n' \
               "${free_line:-none}" "${mutate_line:-none}" "$out"
        return 1
    fi
    if ! printf '%s\n' "$out" | sed -n "$((free_line + 1)),$((mutate_line - 1))p" | grep -qF 'rerun-show'; then
        printf 'rerun-show is not on the side that dispatches freely:\n%s\n' "$out"; return 1
    fi
    if printf '%s\n' "$out" | sed -n "${mutate_line},\$p" | grep -qF 'rerun-show'; then
        printf 'rerun-show is ALSO in the mutating half, so a grep of this block answers twice:\n%s\n' "$out"
        return 1
    fi
    return 0
}
check 'the printed mode list puts rerun-show on the FREE side, not merely somewhere in the block' \
    rerun_show_listed_free
expect 'build takes no script flags but --confirm, and names the script instead' 2 \
    'gatk-sv-build.sh flag' 'docker/gatk-sv-build.sh --check' -- run build --check my-branch

echo
echo "selftest: cli: the outer --confirm gate (additive: the tool's own gate stays)"
expect 'freeze copy without --confirm exits 2 naming --confirm' 2 \
    'refusing "terra freeze-copy"' '--confirm' -- run terra freeze-copy
absent 'and nothing was dispatched when it refused' 2 'STUB terra/batch_freeze.py' \
    -- run terra freeze-copy
expect 'freeze copy with --confirm AND --write dispatches --write, with the pre-flight print' 0 \
    'STUB terra/batch_freeze.py copy --write' 'MUTATING mode' 'what' 'where' 'cost' 'undo' \
    -- run terra freeze-copy --confirm --write
line_absent 'the outer --confirm is CONSUMED, not handed to a tool that gates on --write' \
    'STUB terra/batch_freeze.py' '--confirm' -- run terra freeze-copy --confirm --write
expect "batch_configs' own --confirm IS forwarded, so the inner gate still sees it" 0 \
    'STUB terra/batch_configs.py create --confirm' -- run terra configs-create --confirm
expect 'and the pre-flight names the resolved target, the cost, and the undo' 0 \
    'GSVTK_TERRA_NAMESPACE' 'no undo in Terra' 'no VM cost' \
    -- run terra configs-create --confirm
expect 'rerun submit without --confirm is refused with the cost stated' 2 \
    'refusing "terra rerun-submit"' 'batch of VMs' -- run terra rerun-submit --image k=r
# The gate is not an environment failure in disguise either: under the bare shim (no firecloud, no
# venv on PATH, env -i) the refusal still names --confirm rather than the missing interpreter.
expect 'without --confirm the gate survives a MISSING dependency: it names --confirm, not firecloud' 2 \
    'refusing "terra configs-create"' 'need --confirm' -- run_bare terra configs-create
expect 'the build says where the images land, what the VM is, and how to undo it' 0 \
    'MUTATING mode' 'IMAGE_REPO' 'e2-standard-8' 'VM-minutes' 'instances delete' \
    -- run build my-branch sv-pipeline --confirm
absent 'the build gate consumes --confirm too (the script has no such flag)' \
    0 '--confirm' -- run build my-branch --confirm
expect 'unconfigured target prints the KEY, not an empty workspace' 0 \
    'GSVTK_TERRA_WORKSPACE' -- run terra configs-create --confirm

echo
echo "selftest: cli: check dispatches each flag to the tool that implements it"
if ! command -v jq >/dev/null 2>&1; then
    skipped "check-flags(no-jq)" "jq absent: the default check set skips the jq plumbing scan, so the flag-routing assertion cannot run"
else
    expect 'gate gets --wf/--strict and the ref; the checkers get --repo; the scan gets --compare-to' 0 \
        'STUB checks/wdl_gate.sh --wf SVShell --strict base-ref' \
        'STUB checks/svshell_contract_check.py --repo /tmp' \
        'STUB checks/svshell_jq_plumbing_scan.py --repo /tmp --compare-to base-ref' \
        -- run check base-ref --wf SVShell --strict --repo /tmp
    line_absent 'the contract check never receives the jq scan --compare-to' \
        'STUB checks/svshell_contract_check.py' '--compare-to' \
        -- run check base-ref --wf SVShell --strict --repo /tmp
    line_absent 'wdl_gate never receives --repo (it takes a ref positionally)' \
        'STUB checks/wdl_gate.sh' '--repo' \
        -- run check base-ref --wf SVShell --strict --repo /tmp
    absent 'the two tree-wide checkers are opt-in, not in the default set' \
        0 'STUB checks/wdl_semantics.py' -- run check base-ref
    expect 'they run when asked for, and reach gets the --target its own usage demands' 0 \
        'STUB checks/wdl_semantics.py --repo /tmp' \
        'STUB checks/wdl_reach.py --repo /tmp --target SVShell' \
        -- run check base-ref --semantics --reach --wf SVShell --repo /tmp
    expect 'an unknown check flag is refused, not forwarded to someone who would misread it' 2 \
        'check: unknown flag --wfdf' -- run check base-ref --wfdf
    # `check --help` used to be one of those unknown flags: the subcommand's own usage was unreachable
    # from the subcommand, and the entry point's exit-code table promises 0 for --help. A user who
    # cannot read the flags has to guess them, and guessing `--reach` needs a target is how the bug
    # above got reported instead of avoided.
    expect 'check --help prints the check synopsis and exits 0' 0 \
        'usage: gsvtk check' '  --reach' 'Exit codes:' -- run check --help
    expect 'and -h is the same page' 0 'usage: gsvtk check' -- run check -h
    expect "a checker's findings come back as exit 1, not 0 (findings are not a verdict)" 1 \
        'STUB checks/svshell_contract_check.py' -- env STUB_RC=1 GSVTK_CONFIG="$TMP/empty.env" \
        GSVTK_WORK="$TMP/work" GSVTK_TERRA_PY="$PYABS" bash "$CLI" check
    expect 'no ref given: wdl_gate is skipped and the CLI SAYS so (never a silent pass)' 0 \
        'no ref given: skipping wdl_gate' -- run check
fi

# ------------------------------------------------------------------ entity
# `gsvtk entity` is the one command that reaches checks/terra_entity_check.py from the entry point. What is
# under test here is the DISPATCH (right file, right flags, right exit code), not the derivation — that has its
# own phase, scripts/selftest.d/entity.sh, graded against a committed fixture corpus. These runs use the stub, so
# a needle like `--repo /repo --ref main` is the CLI's own argv, not an assertion about any checkout.
expect 'entity --help works with an empty profile and names the tool it dispatches' 0 \
    'usage: gsvtk entity' 'terra_entity_check.py' -- run entity --help
expect 'entity --help states the exit codes it passes through, including the prerequisite one' 0 \
    '3 missing prerequisite' -- run entity --help
expect 'entity dispatches checks/terra_entity_check.py with --repo and --ref and nothing invented' 0 \
    'STUB checks/terra_entity_check.py --repo /repo --ref main' -- run entity --repo /repo --ref main
expect 'entity forwards --corpus rather than assuming where the configs live' 0 \
    'STUB checks/terra_entity_check.py --corpus inputs/x' -- run entity --corpus inputs/x
expect 'entity --tree sends --tree alone (one tree, one answer)' 0 \
    'STUB checks/terra_entity_check.py --tree /tree' -- run entity --tree /tree
expect 'entity refuses --tree together with --ref: two answers to "which tree"' 2 \
    'second answer' -- run entity --tree /tree --ref main
expect 'entity refuses an unknown flag by name instead of forwarding it' 2 \
    'entity: unknown flag --nope' -- run entity --nope
expect 'entity refuses a bare positional instead of guessing what it meant' 2 \
    "unexpected argument 'genotype'" -- run entity genotype
expect "a checker's findings come back as exit 1 through entity too (findings are not a verdict)" 1 \
    'STUB checks/terra_entity_check.py' -- env STUB_RC=1 GSVTK_CONFIG="$TMP/empty.env" \
    GSVTK_WORK="$TMP/work" GSVTK_TERRA_PY="$PYABS" bash "$CLI" entity --tree /tree
expect 'the read-only stamp does not disable a check that only reads' 0 \
    'STUB checks/terra_entity_check.py --tree /tree' -- run_bare_ro entity --tree /tree

# ------------------------------------------------------------------ check --reach / --semantics
# This section exists because of a real bug: `check --reach` could never answer. wdl_reach.py requires
# a target (`--target`, one name) and the CLI forwarded only the tree, so the flag printed that tool's
# `name both a tree and a target` usage and exited 2, every time, on every machine. `--semantics` had
# the sibling defect: it refuses without a tree, and pyargs was empty unless the caller happened to
# type --repo.
#
# The assertion that used to cover this read `STUB checks/wdl_reach.py --repo /tmp` and PASSED -- it
# pinned the broken argument list, because an echo-based dispatch test proves which file ran and
# nothing about whether that file could act on the flags it was handed. So the first assertion below
# runs the REAL parser on a real two-file tree with NO stub in the path.
if "$PYABS" -c 'import WDL' >/dev/null 2>&1; then
    mkdir -p "$TMP/reach-tree"
    printf 'version 1.0\nworkflow Foo {\n  call Bar\n}\ntask Bar {\n  command <<< echo hi >>>\n}\n' \
        > "$TMP/reach-tree/Foo.wdl"
    real_reach() {
        local out rc
        out="$(env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_PY="$PYABS" \
                 bash "$ROOT/gsvtk" check --reach --repo "$TMP/reach-tree" --wf Foo 2>&1)"; rc=$?
        # 0 or 1: on a two-node tree svshell_contract_check has nothing to compare, and the CLI reports
        # that as findings-not-a-verdict. What may NOT happen is the reach tool answering with its own
        # usage line -- that is the defect, and rc would have been 1 either way.
        if [ "$rc" -gt 1 ]; then printf 'unexpected exit %s\n%s\n' "$rc" "$out"; return 1; fi
        if ! printf '%s\n' "$out" | grep -qF 'target: Foo.wdl::Foo [workflow]'; then
            printf 'no reach answer in:\n%s\n' "$out"; return 1
        fi
        if ! printf '%s\n' "$out" | grep -qF 'calls task Bar at Foo.wdl:3'; then
            printf 'answer carries no edge provenance:\n%s\n' "$out"; return 1
        fi
        if printf '%s\n' "$out" | grep -qF 'name both a tree and a target'; then
            printf 'the tool refused instead of answering:\n%s\n' "$out"; return 1
        fi
        return 0
    }
    check 'REAL composition, no stub anywhere: check --reach answers the question it advertises' \
        real_reach

    # --images is a DISPATCHED flag, and the two ways it can be broken are different: swallowed by the
    # dispatcher (the answer comes back with no image section), or answered by a section that quietly
    # omits what it could not resolve. This tree declares no docker input at all, so the honest answer
    # is "binds none" -- and the tool must SAY that rather than print an empty list, because an empty
    # image list is exactly what a caller reads as "nothing to rebuild".
    real_reach_images() {
        local out rc src
        out="$(env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_PY="$PYABS" \
                 bash "$ROOT/gsvtk" check --reach --images --repo "$TMP/reach-tree" --wf Foo 2>&1)"; rc=$?
        if [ "$rc" -gt 1 ]; then printf 'unexpected exit %s\n%s\n' "$rc" "$out"; return 1; fi
        if ! printf '%s\n' "$out" | grep -qF 'target: Foo.wdl::Foo [workflow]'; then
            printf -- '--images lost the reach answer it was added to:\n%s\n' "$out"; return 1
        fi
        if ! printf '%s\n' "$out" | grep -qF 'image sources:'; then
            printf -- '--images never reached wdl_reach.py (no image section):\n%s\n' "$out"; return 1
        fi
        if ! printf '%s\n' "$out" | grep -qF 'bind no docker input'; then
            printf 'a workflow binding no image was dropped instead of reported:\n%s\n' "$out"; return 1
        fi
        # THREE modes exist, not two, and all three answer honestly. With no checkout configured at all
        # the renderer says `SKIPPED — nothing to render from: GSVTK_GATK_SV_CHECKOUT is unset …`; with
        # an unusable one, `SKIPPED — the renderer said: status=SKIPPED jsons=0 reason=no-checkout`;
        # with a real one, `read 81 JSON(s)`. What may not happen is the source going silent.
        # Pinning only SKIPPED made this leg pass in exactly the mode `make test` happens to run and
        # stop testing the others (measured: 126 ok / 1 failed with the checkout set, 127 / 0 unset).
        # And pinning the WORDING of the excuse was my own first attempt's bug: requiring `reason=`
        # refused the unset-mode answer, which is honest and simply words itself differently -- the gate
        # caught it, not me. So the assert is on the shape: reported-and-skipped, or a count.
        src="$(printf '%s\n' "$out" | grep -F 'rendered input JSON:' | head -1)"
        case "$src" in
            *SKIPPED*) ;;                                          # could not run, and says so
            *'read '[0-9]*' JSON(s)'*) ;;                          # ran, and counted what it read
            *) printf 'the render source answered with neither a count nor a skip: [%s]\n' "${src:-<no rendered input JSON line at all>}"; return 1 ;;
        esac
        return 0
    }
    check 'REAL composition: --reach --images answers AND reports every source it could not use' \
        real_reach_images

    # The same composition proof for SEVERAL names, on the same two-node tree: the dispatcher used to
    # run one wdl_reach process per name, so two names cost two whole-tree parses. `tree:` is printed
    # once per parse, which makes it the counter — and it is counted here, on the real tool over a real
    # tree, because a stub cannot testify that it loaded the tree once. Foo answers with its call edge,
    # Bar with its own NOT REACHED: if the second block were the first one echoed, or missing, one of
    # the two counts or the order check fails.
    #
    # Both needles are ANCHORED (`^tree: `), and that is not pedantry: the first unanchored draft of
    # this count read 2 loads for a run that did one, because the NOT REACHED detail line ends
    # `… under /tmp/…/reach-tree: 3 nodes, 3 edges` and the fixture's own directory name completed the
    # needle. An assertion that counts a path suffix is an assertion that can never go green.
    real_reach_two() {
        local out rc n_tree n_block n1 n2
        out="$(env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_PY="$PYABS" \
                 bash "$ROOT/gsvtk" check --reach --repo "$TMP/reach-tree" --wf Foo --wf Bar 2>&1)"; rc=$?
        if [ "$rc" -gt 1 ]; then printf 'unexpected exit %s\n%s\n' "$rc" "$out"; return 1; fi
        n_tree="$(printf '%s\n' "$out" | grep -cE '^tree: ')"
        if [ "${n_tree:-0}" -ne 1 ]; then
            printf 'two names cost %s tree load(s); the point of the change is exactly ONE:\n%s\n' \
                   "${n_tree:-0}" "$out"; return 1
        fi
        n_block="$(printf '%s\n' "$out" | grep -cE '^target: ')"
        if [ "${n_block:-0}" -ne 2 ]; then
            printf 'two names produced %s answer block(s), not 2:\n%s\n' "${n_block:-0}" "$out"; return 1
        fi
        printf '%s\n' "$out" | grep -qF 'calls task Bar at Foo.wdl:3' || {
            printf 'the answer for the first name is missing:\n%s\n' "$out"; return 1; }
        printf '%s\n' "$out" | grep -qF 'NOT REACHED — nothing in the tree is reached by Foo.wdl::Bar' || {
            printf 'the second name did not get its OWN answer (it reaches nothing, and must say so):\n%s\n' \
                   "$out"; return 1; }
        n1="$(printf '%s\n' "$out" | grep -nF -- 'target: Foo.wdl::Foo [workflow]' | head -1 | cut -d: -f1)"
        n2="$(printf '%s\n' "$out" | grep -nF -- 'target: Foo.wdl::Bar [task]' | head -1 | cut -d: -f1)"
        if [ -z "$n1" ] || [ -z "$n2" ] || [ "$n1" -ge "$n2" ]; then
            printf 'the answers are not in the order asked (Foo at %s, Bar at %s):\n%s\n' \
                   "${n1:-none}" "${n2:-none}" "$out"; return 1
        fi
        printf '%s\n' "$out" | grep -qF 'name both a tree and a target' && {
            printf 'the tool refused instead of answering:\n%s\n' "$out"; return 1; }
        return 0
    }
    check 'REAL composition, two names: ONE tree load (tree: counted once), both blocks answered in order' \
        real_reach_two

    # Unknown-among-many, asserted through a PIPE on purpose. In the Python selftest both streams are
    # captured in-process and keep call order whatever happens; only a pipe can reorder them, because
    # stdout is block-buffered when it is not a terminal. So this is the one place that proves the
    # flush before the unknown-name report: without it, Foo's answer block lands AFTER the complaint
    # about NoSuchThing and the printed order stops being the order asked.
    real_reach_unknown() {
        local out rc n1 n2
        out="$(env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_PY="$PYABS" \
                 bash "$ROOT/gsvtk" check --reach --repo "$TMP/reach-tree" --wf Foo --wf NoSuchThing 2>&1)"; rc=$?
        # 1, not 2: wdl_reach exits 2 on an unknown name and the CLI reports a checker's nonzero as
        # findings (its 2 means "nothing ran"). What is being proved is WHICH names got answered.
        if [ "$rc" -ne 1 ]; then printf 'unexpected exit %s\n%s\n' "$rc" "$out"; return 1; fi
        printf '%s\n' "$out" | grep -qF "unknown target 'NoSuchThing'" || {
            printf 'the unknown name was not reported by name:\n%s\n' "$out"; return 1; }
        printf '%s\n' "$out" | grep -qF 'UNKNOWN TARGETS' || {
            printf 'the run never said which names went unanswered:\n%s\n' "$out"; return 1; }
        printf '%s\n' "$out" | grep -qF 'calls task Bar at Foo.wdl:3' || {
            printf 'the name that DID resolve was not answered:\n%s\n' "$out"; return 1; }
        n1="$(printf '%s\n' "$out" | grep -nF -- 'target: Foo.wdl::Foo [workflow]' | head -1 | cut -d: -f1)"
        n2="$(printf '%s\n' "$out" | grep -nF -- "unknown target 'NoSuchThing'" | head -1 | cut -d: -f1)"
        if [ -z "$n1" ] || [ -z "$n2" ] || [ "$n1" -ge "$n2" ]; then
            printf 'the answer (line %s) did not come before the unknown report (line %s) — out of order:\n%s\n' \
                   "${n1:-none}" "${n2:-none}" "$out"; return 1
        fi
        return 0
    }
    check 'REAL composition, piped: an unknown name is reported by name and does not swallow or reorder the answered one' \
        real_reach_unknown
    # GSVTK_PY has to be the interpreter that RUNS, not one that is merely set. The guard wrapping this
    # block asks whether $PYABS can import WDL, and then handed the CLI a variable called GSVTK_PYTHON
    # that nothing in the repo reads: the CLI resolved its own interpreter ($ROOT/.venv, else python3),
    # so on a checkout with no .venv the guard described one python and the tool ran under another, and
    # four assertions here went red for a reason none of them could state. Pinning the knob is what makes
    # the guard's claim true; the marker below is the proof, because a knob that is only read by --help
    # would leave it unwritten.
    real_reach_interpreter() {
        local marker="$TMP/py-ran"
        printf '#!/bin/sh\nprintf ran > "%s"\nexec "%s" "$@"\n' "$marker" "$PYABS" > "$TMP/fakepy"
        chmod +x "$TMP/fakepy"
        rm -f "$marker"
        env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_PY="$TMP/fakepy" \
            bash "$ROOT/gsvtk" check --reach --repo "$TMP/reach-tree" --wf Foo >/dev/null 2>&1
        if [ ! -f "$marker" ]; then
            printf 'the dispatched checker did not run under GSVTK_PY — nothing wrote the marker, so the\n'
            printf 'guard above and the interpreter under test are different pythons again:\n%s\n' "$PYABS"
            return 1
        fi
    }
    check 'GSVTK_PY is the interpreter the dispatched python tool actually runs under' \
        real_reach_interpreter
else
    skipped "reach-real" "miniwdl absent: the reach answer needs the WDL module, so the composition proof cannot run (a SKIP here is not a pass in CI)"
fi

expect 'a target is FORWARDED: --wf NAME becomes wdl_reach --target NAME' 0 \
    'STUB checks/wdl_reach.py --repo /tmp --target GenotypeBatch' \
    -- run check --reach --repo /tmp --wf GenotypeBatch
# This assertion used to require TWO stub lines, one per name, because that is what the dispatcher
# did: `for t in "${reach_targets[@]}"; do wdl_reach --target "$t"; done`. That loop paid the tree
# load once per name (~35 s each on a gatk-sv tree), and `wdl_reach.py --target` is now repeatable,
# so the contract flipped: N names are ONE call carrying N --target flags, and the tool prints one
# answer block per name. `expect` can only prove a needle is PRESENT, and a single line carrying both
# flags satisfies both needles just as well as two lines do — so the COUNT is the assertion here.
# Two stub lines for two names is now the regression, not the pass.
expect 'two names are ONE call carrying BOTH --target flags (one tree load, not two)' 0 \
    'STUB checks/wdl_reach.py --repo /tmp --target GenotypeBatch --target MakeCohortVcf' \
    -- run check --reach --repo /tmp --wf GenotypeBatch --wf MakeCohortVcf
expect 'and the banner names every target it asked for, not just the first' 0 \
    '=== wdl_reach.py GenotypeBatch, MakeCohortVcf ===' \
    -- run check --reach --repo /tmp --wf GenotypeBatch --wf MakeCohortVcf
count 'ONE wdl_reach invocation answers both names (a second call is the ~35 s regression)' \
    'STUB checks/wdl_reach.py' 1 \
    -- run check --reach --repo /tmp --wf GenotypeBatch --wf MakeCohortVcf
expect 'a single name is unchanged: one call, one flag, the same banner as ever' 0 \
    '=== wdl_reach.py GenotypeBatch ===' \
    'STUB checks/wdl_reach.py --repo /tmp --target GenotypeBatch' \
    -- run check --reach --repo /tmp --wf GenotypeBatch
expect 'and --reach with no target is a usage error, stated as one' 2 \
    'check --reach needs a target: pass --wf NAME' -- run check --reach
absent 'refused BEFORE any checker ran (a usage error must not spend a scan)' 2 \
    'STUB checks/svshell_contract_check.py' -- run check --reach
# --images qualifies --reach. Two separate claims, because two different bugs pass a single one: the
# flag reaching the tool (forwarded once, at the end of that tool's flags), and the flag meaning
# nothing on its own (a bare --images would print a clean check that never looked at images).
expect '--images is forwarded to the ONE wdl_reach call, not swallowed by the dispatcher' 0 \
    'STUB checks/wdl_reach.py --repo /tmp --target GenotypeBatch --images' \
    -- run check --reach --images --repo /tmp --wf GenotypeBatch
# `wdl_reach --images` defaults its commit-under-review to the checkout's HEAD (its own --help says the
# default is HEAD "because the question is about YOUR branch"). A caller who typed a ref asked about a
# different commit, so the ref must travel — otherwise the "NOT A BUILD OF THIS COMMIT" verdict is
# computed against the wrong commit, which is the failure the feature exists to catch.
expect 'a named ref travels with --images, so the verdict is about the ref asked about' 0 \
    'STUB checks/wdl_reach.py --repo /tmp --target GenotypeBatch --images --inputs-ref base-ref' \
    -- run check base-ref --reach --images --wf GenotypeBatch --repo /tmp
expect '--images without --reach is a usage error, stated as one' 2 \
    'check --images needs --reach' -- run check --images --wf GenotypeBatch
absent 'and that refusal happens before anything ran: no gate, no scan' 2 \
    'STUB checks/wdl_gate.sh' -- run check --images --wf GenotypeBatch
# The tree the opt-in checkers read comes from the resolver, not from the caller remembering --repo.
expect 'with no --repo, the tree is what the resolver says, so --semantics can answer' 0 \
    'STUB checks/wdl_semantics.py --repo /tmp/resolved-checkout' \
    -- env GSVTK_GATK_SV_CHECKOUT=/tmp/resolved-checkout GSVTK_CONFIG="$TMP/empty.env" \
        GSVTK_WORK="$TMP/work" GSVTK_TERRA_PY="$PYABS" bash "$CLI" check --semantics
absent 'and an UNRESOLVED checkout does not become a silent scan of the cwd' 0 \
    'STUB checks/wdl_semantics.py --repo' -- env GSVTK_GATK_SV_CHECKOUT= GSVTK_CONFIG="$TMP/empty.env" \
        GSVTK_WORK="$TMP/work" GSVTK_TERRA_PY="$PYABS" bash "$CLI" check --semantics

echo
echo "selftest: cli: the replay preflight measures java, the jar and the disk"
# Stub `java`/`bcftools` on a private PATH, so the result does not depend on whose machine runs the
# gate. Each refusal has its counterpart that passes: a guard nobody could have tripped is the same
# as a guard that never fires.
mkdir -p "$TMP/bin17" "$TMP/bin8" "$TMP/bin-nojava"
for d in bin17 bin8; do
    printf '#!/usr/bin/env bash\necho x\n' > "$TMP/$d/bcftools"; chmod +x "$TMP/$d/bcftools"
done
# `java -version` answers on STDERR and puts the version in quotes; these two are the whole point of
# the fixture, so they are written with a quoted heredoc rather than through another printf (the
# first attempt escaped the quotes away and the stub printed an unquoted version, which the parser in
# the CLI correctly refused to read -- a fixture that fails silently is the bug class again).
cat > "$TMP/bin17/java" <<'JAVA17'
#!/usr/bin/env bash
printf 'openjdk version "17.0.9" 2023-10-17\n' >&2
JAVA17
cat > "$TMP/bin8/java" <<'JAVA8'
#!/usr/bin/env bash
printf 'openjdk version "1.8.0_292" 2021-04-20\n' >&2
JAVA8
# A PATH with a JDK 17 and no bcftools: absolute bash, because that PATH has no bash in it, which is
# also what keeps the REAL bcftools on this machine from answering for the fixture.
mkdir -p "$TMP/bin-nobcftools"
cp "$TMP/bin17/java" "$TMP/bin-nobcftools/java"
chmod +x "$TMP/bin17/java" "$TMP/bin8/java" "$TMP/bin-nobcftools/java" "$TMP/bin-nobcftools"
printf 'not a jar, but it has bytes\n' > "$TMP/has-bytes.jar"
: > "$TMP/empty.jar"

expect 'JDK 17 + a jar present: preflight passes and the driver runs' 0 \
    'java     ok (major 17)' 'preflight: ok' 'STUB examples/run_train_chr20.sh' \
    -- env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" JAR="$TMP/has-bytes.jar" \
       PATH="$TMP/bin17:$SAVED_PATH" bash "$CLI" replay train-chr20
expect 'CONTROL counterpart: java 8 is refused as TOO OLD, not accepted as "a java"' 3 \
    'TOO OLD' 'JDK 17' -- env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
       JAR="$TMP/has-bytes.jar" PATH="$TMP/bin8:$SAVED_PATH" bash "$CLI" replay train-chr20
absent 'and the driver never ran when the JDK was refused' 3 'STUB examples/run_train_chr20.sh' \
    -- env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" JAR="$TMP/has-bytes.jar" \
         PATH="$TMP/bin8:$SAVED_PATH" bash "$CLI" replay train-chr20
expect 'a jar named by JAR that does not exist is refused by name' 3 \
    'JAR=' 'does not exist' -- env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
       JAR="$TMP/no-such.jar" PATH="$TMP/bin17:$SAVED_PATH" bash "$CLI" replay train-chr20
expect 'a 0-byte jar is refused: an rc from nothing is not a result' 3 \
    '0 bytes' -- env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" JAR="$TMP/empty.jar" \
       PATH="$TMP/bin17:$SAVED_PATH" bash "$CLI" replay train-chr20
expect 'no checkout and no JAR: it says where to look and how to build one' 3 \
    'NOT DISCOVERABLE' 'gradlew localJar' -- env -u JAR -u GSVTK_GATK_CHECKOUT \
       GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" PATH="$TMP/bin17:$SAVED_PATH" \
       bash "$CLI" replay train-chr20
expect 'bcftools missing is named (the drivers slice VCFs with it)' 3 \
    'bcftools MISSING' 'java     ok (major 17)' -- \
       env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" JAR="$TMP/has-bytes.jar" \
       PATH="$TMP/bin-nobcftools:/usr/bin:/bin" /bin/bash "$CLI" replay train-chr20
# The driver dispatch itself, with the fixture in place: the preflight passed, so the example ran.
expect 'the train drivers reach the examples script once the preflight passes' 0 \
    'STUB examples/run_train_chr20.sh' -- env GSVTK_GATK_CHECKOUT="$FAKE" JAR="$TMP/has-bytes.jar" \
    PATH="$TMP/bin17:$SAVED_PATH" bash "$CLI" replay train-chr20
expect 'disk is MEASURED and reported, never invented' 0 \
    'GB free at' -- env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
       JAR="$TMP/has-bytes.jar" PATH="$TMP/bin17:$SAVED_PATH" bash "$CLI" replay preflight
expect 'reference-run names the two capture files it needs' 3 \
    'CAPTURED' 'IMAGES' -- env -u CAPTURED -u IMAGES GSVTK_CONFIG="$TMP/empty.env" \
       GSVTK_WORK="$TMP/work" bash "$CLI" replay reference-run
check 'replay preflight exits 0 when nothing blocks it (the tally line, not prose)' \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" JAR="$TMP/has-bytes.jar" \
        PATH="$TMP/bin17:$SAVED_PATH" bash "$CLI" replay preflight train-chr20

expect 'terra status forwards the PREFIX verbatim to batch_status' 0 \
    'STUB terra/batch_status.py --costs 01-' -- run terra status --costs 01-

# ============================================================================================
# ------------------------------------- terra/batch_status.py: what a price line may claim ----
# ============================================================================================
# Everything above grades WHICH script the entry point reaches. This block grades what
# `terra/batch_status.py` then PRINTS, because the flag it takes is `--costs` and the thing on the
# screen is the number someone writes into a PR. Three rules, none of which a dispatch leg can see:
#
#   1. `--costs PREFIX` prices a config OUTSIDE the chain. The table keys on `steps.step_names()`
#      EXACTLY, so a single-sample config, a rerun config, and a finished run's per-submission
#      snapshot copy are all invisible to it — the old code printed `(other config) <name>: Done` and
#      no number at all.
#   2. There is a TOTAL, and it states its scope (`chain 06->10` vs `matching '01-' over 1 config(s)`)
#      and its kind (`MEASURED` only with neither a floor nor a hole in it, else `FLOOR`).
#   3. An unknown is never a zero and never part of a total. Unreadable metadata, metadata with no
#      call records yet, a call with no VM end, work below a sub-workflow, and Terra's own `cost`
#      (which the record reports as 0 until the workflow finishes) print as UNKNOWN/pending and are
#      counted AND named instead of summed. `DECIDING` lost `useCallCache` at 66d22a5 for the same
#      shape — output that reads like evidence and is not — so the case where Terra says nothing about
#      cost has to say `unknown`, and no path through this file may turn it into a smaller bill.
#
# HOW IT RUNS OFFLINE, and the control on that. The fake answers at the three places the tool asks
# (`terra.submissions`, `fapi.get_submission`, `fapi.get_workflow_metadata`) and turns every OTHER
# `fapi` verb into a raiser, so a request that tried is a named failure; `GSVTK_TERRA_API_ROOT` is a
# closed port besides. Two controls keep that from being a claim: one leg runs the REAL tool against
# that port and requires "neither client could list it" rather than an empty table, and the byte
# comparison below runs the pre-change file through the SAME fake, so a fixture that answered nothing
# would make both sides empty and both sides identical — which is why each side is also content-checked.
# The fixture coordinates are placeholders written into this phase's own temp dir, and the one leg
# that builds a real request (the belt) reads only this machine's ambient ADC, exactly as
# scripts/selftest.d/rerun.sh's wire legs do.
#
# The pre-change file is read out of git at b5c0d03 — the commit this lane started from — because
# "byte-identical with no --costs" is a claim about BYTES and `cmp` is the only recorder that can make
# it. If that object is unreachable (a shallow clone, an exported tree) the leg names itself as a skip
# instead of passing quietly, in the same shape as this file's two other named skips.
echo
echo "selftest: cli: what batch_status --costs prices, and what it refuses to print as zero"
TERRAPY="$PY"
if ! "$TERRAPY" -c 'import firecloud' >/dev/null 2>&1 && [ -x .venv/bin/python ] \
   && .venv/bin/python -c 'import firecloud' >/dev/null 2>&1; then
    TERRAPY=.venv/bin/python       # the helpsweep rule: skipping for a module ./.venv has is reporting nothing
fi
git show b5c0d03:terra/batch_status.py > "$TMP/status-before.py" 2>/dev/null || : > "$TMP/status-before.py"
cat > "$TMP/status.env" <<'ENVT'
GSVTK_TERRA_NAMESPACE=your-namespace
GSVTK_TERRA_WORKSPACE=your-sandbox-workspace
GSVTK_BRANCH=your-branch-under-test
ENVT
cat > "$TMP/status-terra.py" <<'PYFAKE'
"""Drive terra/batch_status.py against a FAKE Terra: no network, no credentials, one answer per question.

    python status-terra.py SCENARIO [batch_status arg...]        # GSVTK_STATUS_FILE picks the file

WHY THE SEAM IS `terra.submissions` + TWO `fapi` CALLS, AND NOT `latest_submissions`
------------------------------------------------------------------------------------
Every rule the block above grades lives BELOW `latest_submissions`. Patching that function would grade
a re-telling of the code rather than the code, which is how a selftest ends up asserting its own
fixture. So the fake answers at the three places the real tool asks and NOTHING else is replaced, and
the file under test is run with runpy under `__main__` — the real argparse, the real refusals, the
real `sys.exit`. Every other `fapi` verb raises, so a request that tried is a failure the phase prints.
"""
import contextlib
import datetime as dt
import io
import os
import runpy
import sys

repo = os.environ["GSVTK_REPO_ROOT"]
sys.path.insert(0, os.path.join(repo, "terra"))
sys.path.insert(0, os.path.join(repo, "kit"))

scenario, argv = sys.argv[1], sys.argv[2:]

import terra                                    # noqa: E402
from terra import fapi                          # noqa: E402

T0 = dt.datetime(2026, 9, 22, 10, 0, tzinfo=dt.timezone.utc)


def call(start, minutes, **kw):
    """One Cromwell call record whose VM span is exactly `minutes`, starting +`start` minutes out.

    `vmEndTime=None` is the running/aborted shape: a start with no end, which the tool must not score
    as a zero-minute call. `subWorkflowId` is the hand-off this tool does not follow.
    """
    def f(m):
        return (T0 + dt.timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = {"executionStatus": "Done", "shardIndex": -1, "attempt": 1,
         "start": f(start), "end": f(start + minutes),
         "vmStartTime": f(start), "vmEndTime": f(start + minutes)}
    d.update(kw)
    for k in [k for k, v in d.items() if v is None]:
        del d[k]
    return d


def sub(cfg, sid, date, status="Done"):
    return {"methodConfigurationName": cfg, "submissionId": sid,
            "submissionDate": date, "status": status}


def wfs(by_sub):
    """submission id -> the get_submission payload Terra would answer with."""
    return {sid: {"workflows": [{"workflowId": wid, "status": st, "cost": cost, "messages": msgs}
                                for (wid, st, cost, msgs) in rows]}
            for sid, rows in by_sub.items()}


# Minutes are stated here and asserted in cli.sh: 45 = 45, 48 = 40 + 8, 93 = 45 + 40 + 8, never a
# total this file computes out of its own rows. Workflow ids differ inside their first 8 characters,
# because that is how much of an id the table prints.
CHAIN5 = ["06-GenerateBatchMetrics", "07-FilterBatchSites", "08-FilterBatchSamples",
          "09-MergeBatchSites", "10-GenotypeBatch"]
MINUTES = {"06": 45.0, "07": 20.0, "08": 10.0, "09": 5.0, "10": 100.0}
SS = {"wf-ssa11111": {"calls": {"SS.call": [call(0, 30.0), call(30, 10.0)]}},     # 40.0 = 30 + 10
      "wf-ssb22222": {"calls": {"SS.call": [call(0, 8.0)]}}}                       # 8.0

SCENARIOS = {
    # Every step of the chain, every workflow readable and closed: the shape where MEASURED is
    # allowed, and the control that UNKNOWN is not this tool's default answer.
    "chain": {
        "submissions": [sub(c, "sub-" + c[:2], "2026-09-22T0%d:00:00Z" % (int(c[:2]) - 5))
                        for c in CHAIN5],
        "workflows": wfs({"sub-" + c[:2]: [("wf-%saaaaaaaa" % c[:2], "Done", 1.5, [])]
                          for c in CHAIN5}),
        "metadata": dict([("wf-%saaaaaaaa" % c[:2], {"calls": {"X.a": [call(0, MINUTES[c[:2]])]}})
                          for c in CHAIN5]),
    },
    # The mixed workspace the prefix arm exists for: one chain step, one single-sample config OUTSIDE
    # the chain (two workflows, because a submission fans out), and a finished run's snapshot copy.
    "mixed": {
        "submissions": [sub("06-GenerateBatchMetrics", "sub-06", "2026-09-22T01:00:00Z"),
                        sub("01-SingleSample-mwfix", "sub-ss", "2026-09-23T09:00:00Z"),
                        sub("10-GenotypeBatch_r3f2k", "sub-snap", "2026-09-24T11:00:00Z")],
        "workflows": wfs({"sub-06": [("wf-06aaaaaaaa", "Done", 3.25, [])],
                          "sub-ss": [("wf-ssa11111", "Done", 4.0, []),
                                     ("wf-ssb22222", "Done", 0.8, [])],
                          "sub-snap": [("wf-snap0001", "Done", 7.0, [])]}),
        "metadata": dict([("wf-06aaaaaaaa", {"calls": {"G.a": [call(0, 45.0)]}}),
                          ("wf-snap0001", {"calls": {"G.b": [call(0, 100.0)]}})] + list(SS.items())),
    },
    # Only a non-chain config has ever been submitted, so the CHAIN table has nothing to price at all.
    "solo": {
        "submissions": [sub("01-SingleSample-mwfix", "sub-ss", "2026-09-23T09:00:00Z")],
        "workflows": wfs({"sub-ss": [("wf-ssa11111", "Done", 4.0, []),
                                     ("wf-ssb22222", "Done", 0.8, [])]}),
        "metadata": dict(SS),
    },
    # Two ways a figure cannot be read, both of which the old code printed as `vm_min=0`: the metadata
    # fetch fails, and the metadata holds no call records yet.
    "opaque": {
        "submissions": [sub("06-GenerateBatchMetrics", "sub-06", "2026-09-22T01:00:00Z"),
                        sub("07-FilterBatchSites", "sub-07", "2026-09-22T02:00:00Z", "Running")],
        "workflows": wfs({"sub-06": [("wf-06aaaaaaaa", "Done", 0.0, [])],
                          "sub-07": [("wf-07aaaaaaaa", "Submitted", 0.0, [])]}),
        "metadata": {"wf-06aaaaaaaa": ("RAISE", "HTTP 404: workflow metadata not delocalized yet"),
                     "wf-07aaaaaaaa": {"calls": {}}},
    },
    # A run in flight, and a run whose work sits below a sub-workflow: both readable, neither a
    # measurement, so both carry the `+` and pull the total down to FLOOR.
    "open": {
        "submissions": [sub("06-GenerateBatchMetrics", "sub-06", "2026-09-22T01:00:00Z", "Running"),
                        sub("07-FilterBatchSites", "sub-07", "2026-09-22T02:00:00Z")],
        "workflows": wfs({"sub-06": [("wf-06aaaaaaaa", "Running", 0.0, [])],
                          "sub-07": [("wf-07aaaaaaaa", "Done", 6.0, [])]}),
        "metadata": {"wf-06aaaaaaaa": {"calls": {"F.a": [call(0, 10.0)],
                                                 "F.b": [call(0, 0, vmEndTime=None, end=None)]}},
                     "wf-07aaaaaaaa": {"calls": {"G.sub": [call(0, 20.0, subWorkflowId="wf-child")]}}},
    },
    # A submission Terra accepted and has not started: the record carries no workflows at all.
    "queued": {
        "submissions": [sub("01-SingleSample-mwfix", "sub-ss", "2026-09-23T09:00:00Z")],
        "workflows": {"sub-ss": {"workflows": []}},
        "metadata": {},
    },
}

if scenario not in SCENARIOS:
    sys.exit("status-terra.py: no scenario %r (have: %s)" % (scenario, ", ".join(sorted(SCENARIOS))))
S = SCENARIOS[scenario]


class Resp:
    """The `.json()` half of the fiss response, which is all batch_status asks an answer for."""

    def __init__(self, payload):
        self._p = payload
        self.status_code = 200

    def json(self):
        return self._p


def get_submission(ns, ws, sid):
    if sid not in S["workflows"]:
        raise AssertionError("fake: no submission %s was put on the table" % sid)
    return Resp(S["workflows"][sid])


def get_workflow_metadata(ns, ws, sid, wid):
    m = S["metadata"].get(wid, "MISSING")
    if m == "MISSING":
        raise AssertionError("fake: no metadata was put on the table for %s" % wid)
    if isinstance(m, tuple) and m[0] == "RAISE":
        raise RuntimeError(m[1])
    return Resp(m)


terra.submissions = lambda ns, ws, limit=20: {"submissions": S["submissions"]}
for _n in dir(fapi):
    if _n.startswith("_") or not callable(getattr(fapi, _n)):
        continue
    if _n == "get_submission":
        setattr(fapi, _n, get_submission)
    elif _n == "get_workflow_metadata":
        setattr(fapi, _n, get_workflow_metadata)
    else:
        def raiser(n=_n):
            def f(*a, **k):
                raise AssertionError("the status table reached the wire: fapi.%s" % n)
            return f
        setattr(fapi, _n, raiser())

target = os.environ.get("GSVTK_STATUS_FILE") or os.path.join(repo, "terra", "batch_status.py")
sys.argv = ["batch_status.py"] + argv
buf = io.StringIO()
try:
    with contextlib.redirect_stdout(buf):
        runpy.run_path(target, run_name="__main__")
except SystemExit as exc:                    # a refusal: the message IS the finding
    rc = exc.code if isinstance(exc.code, int) else 1
    sys.stdout.write(buf.getvalue())
    if exc.code is not None and not isinstance(exc.code, int):
        sys.stdout.write("%s\n" % exc.code)
    sys.stdout.flush()
    sys.exit(rc)
sys.stdout.write(buf.getvalue())
sys.stdout.flush()
sys.exit(0)
PYFAKE

# stat_case [--before] SCENARIO [batch_status arg...] — one priced table, from either file.
stat_case() {
    local f="$ROOT/terra/batch_status.py"
    if [ "${1:-}" = "--before" ]; then f="$TMP/status-before.py"; shift; fi
    env GSVTK_STATUS_FILE="$f" GSVTK_REPO_ROOT="$ROOT" GSVTK_CONFIG="$TMP/status.env" \
        GSVTK_WORK="$TMP/work" GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/" \
        "$TERRAPY" "$TMP/status-terra.py" "$@"
}

# A region, not a bare skip: every site below this marker is withheld by the one skip, and the reporter
# can then tell "these 47 legs could not run here" from "a refactor orphaned one of them" (the guard is
# scripts/selftest.d/declared.sh's, and a skip that names no region explains nothing).
gsvtk_guard_to_end "cli-status-pricing"
if ! "$TERRAPY" -c 'import firecloud' >/dev/null 2>&1 || [ ! -s "$TMP/status-before.py" ]; then
    gsvtk_note_skip "cli-status-pricing"
    skipped "status-pricing(firecloud+git blob)" \
        "no firecloud-capable interpreter, or b5c0d03's batch_status.py is not readable from this tree: the pricing legs and the byte comparison cannot run (a SKIP here is not a pass in CI)"
else
    # --- the belt, graded: nothing reaches Terra and lies ------------------------------------------
    expect "the offline belt holds: the real tool names the unusable listing, it does not answer empty" 1 \
        'neither client could list it' -- \
        env GSVTK_REPO_ROOT="$ROOT" GSVTK_CONFIG="$TMP/status.env" GSVTK_WORK="$TMP/work" \
            GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/" "$TERRAPY" terra/batch_status.py --costs
    absent "and it does not fall through to a price table built on nothing" 1 'TOTAL' -- \
        env GSVTK_REPO_ROOT="$ROOT" GSVTK_CONFIG="$TMP/status.env" GSVTK_WORK="$TMP/work" \
            GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/" "$TERRAPY" terra/batch_status.py --costs 01-

    # --- no --costs is the SAME BYTES it always was --------------------------------------------------
    # Both sides are content-checked first: a fake that answered nothing would leave two empty files,
    # `cmp` would agree, and the byte claim would be vacuous.
    expect 'the CURRENT file still prints the non-chain config as an unpriced (other config) row' 0 \
        '(other config) 01-SingleSample-mwfix: Done' -- stat_case mixed
    absent "the PRE-change file gives it no figure at all (so it is the pre-change program)" 0 \
        'vm_min=40.0' -- stat_case --before mixed
    stat_case --before mixed > "$TMP/tab-before.txt" 2>&1
    stat_case mixed > "$TMP/tab-now.txt" 2>&1
    check "no --costs is byte-identical to b5c0d03 on a workspace holding configs outside the chain" \
        cmp -s "$TMP/tab-before.txt" "$TMP/tab-now.txt"
    count "and the table that prints adds nothing: zero vm_min lines" 'vm_min' 0 -- stat_case mixed

    # --- the prefix arm prices what the chain walk cannot reach --------------------------------------
    expect "the chain table cannot price the single-sample config: it lists the name and stops" 0 \
        '(other config) 01-SingleSample-mwfix: Done' -- stat_case mixed --costs
    absent "and prints no number for it: 40.0 is that config's own minutes, absent from that table" 0 \
        'vm_min=40.0' -- stat_case mixed --costs
    expect "--costs PREFIX prices the config outside the chain, with its own total (30+10+8=48.0)" 0 \
        "TOTAL matching '01-' over 1 config(s): vm_min=48.0  MEASURED  (2/2 row(s) readable)" \
        -- stat_case mixed --costs 01-
    expect "its fan-out is priced per workflow through the same vm_minutes path: 30 + 10 = 40.0" 0 \
        'wf-ssa11 vm_min=40.0 calls=2 failed=none cost=4.0' -- stat_case mixed --costs 01-
    expect "and 8.0 for the second, both summed into the one total above" 0 \
        'wf-ssb22 vm_min=8.0 calls=1 failed=none cost=0.8' -- stat_case mixed --costs 01-
    absent "the prefix table holds only what matched: no chain row in it" 0 \
        '06-GenerateBatchMetrics' -- stat_case mixed --costs 01-
    absent "and no (other config) tail, because the prefix already stated the scope" 0 \
        '(other config)' -- stat_case mixed --costs 01-
    expect "one prefix spanning two configs sums across both: 45 + 40 + 8 = 93.0" 0 \
        "TOTAL matching '0' over 2 config(s): vm_min=93.0  MEASURED  (3/3 row(s) readable)" \
        -- stat_case mixed --costs 0
    absent "a prefix is a PREFIX, not a substring: '0' leaves the 10- config alone" 0 \
        'wf-snap0' -- stat_case mixed --costs 0
    expect "a finished run's snapshot copy is priceable too — no step is called 10-GenotypeBatch_r3f2k" 0 \
        'vm_min=100.0  MEASURED  (1/1 row(s) readable)' -- stat_case mixed --costs 10-
    expect "CONTROL that the prefix arm is new: the pre-change tool rejects the same command line" 2 \
        'unrecognized arguments: 01-' -- stat_case --before mixed --costs 01-

    # --- the total says what it covers and what kind of number it is ---------------------------------
    expect "the total states its SCOPE, so a one-config figure is never quoted as a chain bill" 0 \
        'TOTAL chain 06->10' -- stat_case chain --costs
    expect "a complete chain is the only shape that gets MEASURED (45+20+10+5+100 = 180.0)" 0 \
        'TOTAL chain 06->10: vm_min=180.0  MEASURED  (5/5 row(s) readable)' -- stat_case chain --costs
    expect "an incomplete chain is a FLOOR and names every step it could not price" 0 \
        '4 chain step(s) never submitted (07, 08, 09, 10)' -- stat_case mixed --costs
    expect "and the same run names the configs the total deliberately excludes" 0 \
        'so none of them is in that total; price one by prefix: --costs 01-SingleSample-mwfix' \
        -- stat_case mixed --costs
    expect "a run in flight is a FLOOR, and the + travels from the row to the total" 0 \
        'TOTAL chain 06->10: vm_min=30.0+  FLOOR' -- stat_case open --costs
    absent "a floor total never claims MEASURED" 0 'MEASURED' -- stat_case open --costs
    expect "a FLOOR total counts the rows it read out of the rows it priced" 0 \
        '2/2 row(s) readable; 2 with open or nested work (the + figures)' -- stat_case open --costs

    # --- the unknown-not-zero rule --------------------------------------------------------------------
    expect "unreadable metadata prints UNKNOWN with the reason it is unknown" 0 \
        'vm_min=UNKNOWN(metadata unavailable: HTTP 404: workflow metadata not delocalized yet)' \
        -- stat_case opaque --costs
    expect "metadata with no call records yet is an unknown too, not a workflow that cost nothing" 0 \
        'vm_min=UNKNOWN(no call records in the metadata yet)' -- stat_case opaque --costs
    expect "its call count is unknown as well: zero calls would read as a workflow that did nothing" 0 \
        'calls=unknown' -- stat_case opaque --costs
    absent "NOT ONCE does an unreadable figure print as a number: no vm_min=0 in the whole run" 0 \
        'vm_min=0' -- stat_case opaque --costs
    expect "the total refuses to add them, and names the rows it refused to add" 0 \
        'vm_min=UNKNOWN  0 of 2 row(s) readable -- an unreadable run is not a free one [wf-06aaa, wf-07aaa]' \
        -- stat_case opaque --costs
    expect "CONTROL: the same tool with readable metadata prints numbers, so UNKNOWN is not a default" 0 \
        'vm_min=180.0  MEASURED' -- stat_case chain --costs
    expect "a call still open is named, and the figure carries a + instead of a claim" 0 \
        'vm_min=10.0+ calls=2 failed=none' -- stat_case open --costs
    expect "and the reason sits next to it, not left for the reader to infer" 0 \
        'FLOOR(1 call(s) with no VM end yet (running or aborted))' -- stat_case open --costs
    expect "work below a sub-workflow is named as uncounted, never counted as nothing" 0 \
        'FLOOR(1 call(s) below a sub-workflow, whose tree is not fetched here)' \
        -- stat_case open --costs
    expect "Terra's 0-while-running estimate prints as pending, not as a cost of 0" 0 \
        'cost=pending(Terra reports 0 until the workflow finishes)' -- stat_case open --costs
    expect "a finished workflow whose estimate is still 0 is labelled, because 0 is the field's default" 0 \
        "cost=0.0(Terra's estimate, recorded as 0 and never summed here)" -- stat_case opaque --costs
    expect "a chain nobody has submitted prints UNKNOWN over nothing, not 0.0" 0 \
        'TOTAL chain 06->10: vm_min=UNKNOWN  nothing was priced; 5 chain step(s) never submitted' \
        -- stat_case solo --costs
    absent "and the empty chain table prints no vm_min=0.0 anywhere either" 0 'vm_min=0.0' \
        -- stat_case solo --costs
    expect "a submission with no workflows yet is an unknown, not a free run" 0 \
        'vm_min=UNKNOWN(queued)' -- stat_case queued --costs 01-
    expect "and its total says 0 of 1 readable rather than totalling it to 0.0" 0 \
        "TOTAL matching '01-' over 1 config(s): vm_min=UNKNOWN  0 of 1 row(s) readable" \
        -- stat_case queued --costs 01-
    # The wording `scripts/check_doc_flags.py` grades against: a documented `--costs X` is only real if
    # the parser shows the value is OPTIONAL, which is the whole difference between the two arms.
    expect "--help shows the PREFIX as optional, with an EMPTY profile and no credentials" 0 \
        '--costs [PREFIX]' -- \
        env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" "$TERRAPY" terra/batch_status.py --help

    # --- the refusals, each with the control that proves it can fire ---------------------------------
    expect "--costs with an empty PREFIX is refused, not read as 'price everything'" 1 \
        'an empty PREFIX prices nothing' -- stat_case mixed --costs ""
    expect "and it names the shell shape that produces it, because that is how it gets typed" 1 \
        'CFG unset is how this gets typed' -- stat_case mixed --costs ""
    expect "CONTROL: the same command with a real prefix is accepted (the refusal above can fire)" 0 \
        "TOTAL matching '01-'" -- stat_case mixed --costs 01-
    expect "a prefix that matches no submission is a named empty answer, exit 1" 1 \
        "no submission whose config starts with '99'" -- stat_case mixed --costs 99
    expect "and it lists the configs the workspace holds, so a typo is findable from the answer" 1 \
        '01-SingleSample-mwfix, 06-GenerateBatchMetrics, 10-GenotypeBatch_r3f2k' \
        -- stat_case mixed --costs 99
    expect "--costs and --wait together is refused: two questions, one answer each" 1 'two questions' \
        -- stat_case mixed --costs 01- --wait 01-
    expect "CONTROL: --wait ALONE still dispatches the poller (that refusal is not a blanket ban)" 0 \
        '01-SingleSample-mwfix submission=Done' -- stat_case mixed --wait 01-
    expect "the watcher's terminal dump prints the SAME priced line as the table, not a second accounting" 0 \
        'wf-ssa11111 Done vm_min=40.0 calls=2 failed=none cost=4.0' -- stat_case mixed --wait 01-
fi

# The entry point side of the same flag, with no interpreter beyond the stub: the CLI promises to
# forward a mode's flags verbatim, and `--costs` changed shape (store_true -> optional value), so a
# dispatcher that swallowed the PREFIX would leave `terra status --costs 01-` pricing the chain.
printf '\ncli selftest: %s ok, %s skipped, %s failed\n' "$ok" "$skip" "$fail"
# Equal sets, every run; with a named skip the reporter prints which sites the skip withheld, by line. What the
# pair cannot see is a DELETION — declared and reached drop together (see scripts/selftest.d/declared.sh).
gsvtk_declared_report "cli selftest" "$SELF" "$((ok + fail))" "$skip" || fail=$((fail + 1))
[ "$fail" -eq 0 ]
