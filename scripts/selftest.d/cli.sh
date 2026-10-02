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
#   * POSITIVE CONTROLS for that: seven of them, in two tiers. Five run under the same stripped PATH
#     with firecloud present (a benign read-only dispatch, a plan-only download, the free build plan,
#     a comparator, an image-check --dry-run), and two run under the IDENTICAL shim the refusals above
#     use — no firecloud, no venv — where a comparator and the build plan still reach their script.
#     Without the controls, 17 refusals are also what a CLI that cannot parse its own arguments, or
#     cannot find its interpreter, reports.
#   * each subcommand reaches the RIGHT script: the stand-ins echo the resolved command line, so the
#     assertion is about the path and the arguments, not about a stamp alone. Nothing here touches the
#     network: no stub imports anything, and the Terra stubs never load terra.py.
#   * flags are DISPATCHED, not forwarded: `check`'s --compare-to reaching svshell_contract_check.py
#     (which has no such flag) is an argparse error that reads like a broken checker, so one assertion
#     requires the line WITHOUT it.
#   * the outer gate is additive: freeze's `--write` must arrive with the user's --confirm consumed,
#     and batch_configs' own `--confirm` must arrive forwarded. One assertion per direction.
#   * a mutating mode prints where/what-it-costs/how-to-undo BEFORE acting, and without --confirm
#     exits 2 naming --confirm and dispatching nothing.
#   * the replay preflight measures: java major 17 passes, java 8 is refused as TOO OLD, a missing jar
#     is refused by name, and an empty jar is refused. All four run against stub `java`/`bcftools`
#     binaries in a temp bin, so the result does not depend on the JDK of whoever runs the gate — the
#     machine that runs this suite is not the machine that decides what a trainer needs.
#
# bash 3.2 compatible. Nothing here writes outside a temp dir, needs credentials, or uses the network.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
# GSVTK_TERRA_PY wants an ABSOLUTE interpreter: the stand-in tree has no .venv of its own.
case "$PY" in
  /*) PYABS="$PY" ;;
  *)  PYABS="$(cd "$(dirname "$PY")" && pwd -P)/$(basename "$PY")" ;;
esac

ok=0; skip=0; fail=0; skiplist=""
TMP="$(mktemp -d "${TMPDIR:-/tmp}/gsvtk-cli.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"                       # the EMPTY profile every run below points at
SAVED_PATH="$PATH"

# The two harness helpers are COPIED from scripts/selftest.sh, not sourced: that file IS the gate, and
# running it from here would be a recursive gate inside the gate (check_doc_flags.py carries a note
# about exactly that trap, found by timing a scan at five minutes). Same rules: `expect` needs at
# least one NEEDLE, because an assertion that names no string cannot fail for the right reason.
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

expect() {
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
    expect "a checker's findings come back as exit 1, not 0 (findings are not a verdict)" 1 \
        'STUB checks/svshell_contract_check.py' -- env STUB_RC=1 GSVTK_CONFIG="$TMP/empty.env" \
        GSVTK_WORK="$TMP/work" GSVTK_TERRA_PY="$PYABS" bash "$CLI" check
    expect 'no ref given: wdl_gate is skipped and the CLI SAYS so (never a silent pass)' 0 \
        'no ref given: skipping wdl_gate' -- run check
fi

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
        out="$(env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_PYTHON="$PYABS" \
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
else
    skipped "reach-real" "miniwdl absent: the reach answer needs the WDL module, so the composition proof cannot run (a SKIP here is not a pass in CI)"
fi

expect 'a target is FORWARDED: --wf NAME becomes wdl_reach --target NAME' 0 \
    'STUB checks/wdl_reach.py --repo /tmp --target GenotypeBatch' \
    -- run check --reach --repo /tmp --wf GenotypeBatch
expect 'two names are two answers, not one name silently dropped' 0 \
    'STUB checks/wdl_reach.py --repo /tmp --target GenotypeBatch' \
    'STUB checks/wdl_reach.py --repo /tmp --target MakeCohortVcf' \
    -- run check --reach --repo /tmp --wf GenotypeBatch --wf MakeCohortVcf
expect 'and --reach with no target is a usage error, stated as one' 2 \
    'check --reach needs a target: pass --wf NAME' -- run check --reach
absent 'refused BEFORE any checker ran (a usage error must not spend a scan)' 2 \
    'STUB checks/svshell_contract_check.py' -- run check --reach
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

printf '\ncli selftest: %s ok, %s skipped, %s failed\n' "$ok" "$skip" "$fail"
[ "$fail" -eq 0 ]
