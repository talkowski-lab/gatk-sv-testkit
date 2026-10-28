#!/usr/bin/env bash
# scripts/selftest.d/cli.sh — assertions for ./gsvtk, the repo's command-line entry point.
#
# Standalone:  bash scripts/selftest.d/cli.sh .venv/bin/python
# Wired into the gate by one line in the Makefile's `selftest` target; prints its own tally.
#
# Covers: --help/no-args exit codes with an empty profile; every mutating mode refused under
# GSVTK_READ_ONLY=1 with the dependency genuinely absent, plus positive controls under the same shim;
# each subcommand reaching the right script with the right flags (stand-ins echo their command line);
# the outer --confirm gate and its pre-flight print; the replay preflight against stub java/bcftools;
# a module outside the batch chain; terra/batch_status.py --costs against a fake Terra API; and
# docker/image_capture.py.
#
# bash 3.2 compatible. Nothing here writes outside a temp dir, needs credentials, or uses the network.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
# GSVTK_TERRA_PY wants an absolute interpreter: the stand-in tree has no .venv of its own.
# A slash-free name is a PATH lookup, not a file in this directory.
case "$PY" in
  /*)   PYABS="$PY" ;;
  */*)  PYABS="$(cd "$(dirname "$PY")" && pwd -P)/$(basename "$PY")" ;;
  *)    PYABS="$(command -v "$PY" 2>/dev/null || printf '%s' "$PY")" ;;
esac

ok=0; skip=0; fail=0; skiplist=""
TMP="$(mktemp -d "${TMPDIR:-/tmp}/gsvtk-cli.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"                       # the EMPTY profile every run below points at

# ------------------------------------------------- declared vs reached
# Every declared assertion call site must be reached (see declared.sh). The terra-refusal loops record
# verdicts inline, so the verdict total exceeds the site count.
SELF="$ROOT/scripts/selftest.d/cli.sh"
. "$ROOT/scripts/selftest.d/declared.sh"
GSVTK_DECL_FUNCS="check expect absent line_absent count"
gsvtk_declared_init "$TMP"
# Legs that go through `bash "$ROOT/gsvtk"` need an interpreter even without a .venv (fresh clone, CI).
gsvtk_phase_interpreters
SAVED_PATH="$PATH"

# Harness helpers copied from scripts/selftest.sh rather than sourced (sourcing would run the gate
# recursively). `expect` requires at least one NEEDLE.
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
        grep -qF -- "$n" <<< "$out" || missing="${missing}[$n] "
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

# absent DESC WANT_RC NEEDLE CMD... — the needle must NOT appear and the exit must be WANT_RC.
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
    elif grep -qF -- "$needle" <<< "$out"; then
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
    elif grep -qF -- "$needle" <<< "$line"; then
        fail=$((fail + 1)); printf '  FAIL  %s — that line says %s: %s\n' "$desc" "$needle" "$line"
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

skipped() {
    skip=$((skip + 1)); skiplist="$skiplist $1"
    printf '  SKIP  %s\n' "$2"
}

# count DESC PATTERN WANT CMD... — exactly WANT output lines contain PATTERN.
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
# A copy of the entry point plus kit/ and one stand-in per dispatched script. Each stand-in echoes the
# path it was reached as and its arguments.
FAKE="$TMP/kit"
mkdir -p "$FAKE"/{checks/image-check,terra,docker,compare,replay,examples,scripts}
cp -R "$ROOT/kit" "$FAKE/" 2>/dev/null || true
rm -rf "$FAKE/kit/__pycache__"
cp "$ROOT/gsvtk" "$FAKE/gsvtk"
chmod +x "$FAKE/gsvtk"
# Canonicalise: the CLI prints `pwd -P` of its own directory.
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
# Every run uses an EMPTY profile, so a tool that needs a value must exit 4 naming the key.
run() {
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_TERRA_PY="$PYABS" \
        bash "$CLI" "$@"
}
# The same CLI with the dependency genuinely absent: no firecloud, no venv on PATH, `env -i`.
# On macOS /bin/bash is 3.2, so this also covers bash 3.2.
run_bare() {
    env -i PATH=/usr/bin:/bin HOME="$HOME" GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
        GSVTK_TERRA_PY=/usr/bin/false bash "$CLI" "$@"
}
# Bare environment plus the read-only stamp: the shim every refusal below runs under. With the
# dependency absent, a refusal can only have come from the policy, not the interpreter probe.
run_bare_ro() {
    env -i PATH=/usr/bin:/bin HOME="$HOME" GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
        GSVTK_TERRA_PY=/usr/bin/false GSVTK_READ_ONLY=1 bash "$CLI" "$@"
}
# Control for run_bare_ro: same stripped environment, but the interpreter can import firecloud.
run_readonly() {
    env -i PATH=/usr/bin:/bin HOME="$HOME" GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
        GSVTK_TERRA_PY="$PYABS" GSVTK_READ_ONLY=1 bash "$CLI" "$@"
}

echo
echo "selftest: ./gsvtk — the entry point, against a stand-in tree (offline, no credentials)"
echo

# Everything below hands $PYABS to the CLI as GSVTK_TERRA_PY under a stripped-PATH shim, so it must be
# an absolute path to an executable.
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
# /usr/bin/false cannot import firecloud, so if the whitelist were checked after the interpreter probe
# these would report a missing interpreter instead of a refusal.
for mode in configs-create configs-validate freeze-copy freeze-attrs rerun-create rerun-validate \
            rerun-submit fetch fetch-compare copy submit attrs create validate profile ""; do
    out="$(run_bare_ro terra $mode 2>&1)"; rc=$?
    if [ "$rc" -ne 2 ]; then
        fail=$((fail + 1)); printf '  FAIL  terra %s: exit %s, want 2\n' "${mode:-(empty)}" "$rc"
        printf '%s\n' "$out" | head -5 | sed 's/^/          /'
    elif ! grep -qi 'refus' <<< "$out"; then
        fail=$((fail + 1)); printf '  FAIL  terra %s: exit 2 but no refusal printed\n' "${mode:-(empty)}"
        printf '%s\n' "$out" | head -5 | sed 's/^/          /'
    elif grep -qi 'firecloud\|interpreter' <<< "$out"; then
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
# A --dry-run/--check token does not exempt a mutating mode: the exemption is decided by mode name,
# because none of these tools implements those tokens as a plan.
expect 'read-only + --dry-run: a mutating mode is still refused (configs-create)' 2 \
    'REFUSED' 'terra configs-create' -- run_readonly terra configs-create --dry-run --confirm
expect 'read-only + --dry-run: a submission is still refused (rerun-submit)' 2 \
    'REFUSED' 'terra rerun-submit' -- run_readonly terra rerun-submit --dry-run --confirm
expect 'read-only + --check --write: the attribute upload is still refused (freeze-attrs)' 2 \
    'REFUSED' 'terra freeze-attrs' -- run_readonly terra freeze-attrs --check --write
# The mirror case: a mutating token must not convict a free mode. rerun-show is what the create
# pre-flight tells an operator to run before re-POSTing; batch_rerun_step.py accepts --confirm as a
# no-op for show.
absent 'a free mode is free by NAME: rerun-show --confirm dispatches under the stamp, no pre-flight' \
    0 'MUTATING mode' -- run_readonly terra rerun-show --confirm

# The refusal must say it is policy, so a reordering cannot pass this section on exit code alone.
expect 'the refusal states it is policy, not a broken environment' 2 \
    'no dependency, credential or' 'not a broken environment' -- run_bare_ro terra rerun-submit

echo
echo "selftest: cli: the controls — the same stripped environment still dispatches the free modes"
# Controls: refusals alone are also what a CLI that cannot parse its own arguments prints.
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
# rerun-show shares terra/batch_rerun_step.py with POSTing modes; under the stamp only its membership
# in the free list lets it dispatch.
expect 'CONTROL: rerun-show is free by NAME, so the stamp still dispatches the rerun body printer' 0 \
    'STUB terra/batch_rerun_step.py show' -- run_readonly terra rerun-show
# Tighter controls: the same shim the refusals ran under still dispatches modes that need no
# interpreter, which shows the refusals above were policy.
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
# Free modes forward their flags verbatim. --step 09 (not the tool's default 10) makes a dropped flag
# visible.
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
# The header names `gsvtk compare` in backticks; it must not become a command substitution.
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
# The unknown-mode block prints both the free and the mutating list, so a needle cannot tell which side
# rerun-show is on; check its position instead. The block prints before the dependency probe, so run bare.
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
    if ! grep -qF 'rerun-show' <<< "$(printf '%s\n' "$out" | sed -n "$((free_line + 1)),$((mutate_line - 1))p")"; then
        printf 'rerun-show is not on the side that dispatches freely:\n%s\n' "$out"; return 1
    fi
    if grep -qF 'rerun-show' <<< "$(printf '%s\n' "$out" | sed -n "${mutate_line},\$p")"; then
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
    # check --help prints the subcommand's own usage and exits 0.
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
# Dispatch of checks/terra_entity_check.py only (file, flags, exit code); the derivation itself is
# tested in scripts/selftest.d/entity.sh.
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
# wdl_reach.py needs a --target and wdl_semantics.py needs a tree, so the CLI must supply both. A stub
# only proves which file ran, so these legs run the real parser on a real two-file tree.
if "$PYABS" -c 'import WDL' >/dev/null 2>&1; then
    gsvtk_guard_sites "reach-real"          # first statement of the block the SKIP below withholds
    mkdir -p "$TMP/reach-tree"
    printf 'version 1.0\nworkflow Foo {\n  call Bar\n}\ntask Bar {\n  command <<< echo hi >>>\n}\n' \
        > "$TMP/reach-tree/Foo.wdl"
    real_reach() {
        local out rc
        out="$(env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_PY="$PYABS" \
                 bash "$ROOT/gsvtk" check --reach --repo "$TMP/reach-tree" --wf Foo 2>&1)"; rc=$?
        # 0 or 1 (nothing to compare on this tree counts as findings); the reach tool must not answer
        # with its own usage line.
        if [ "$rc" -gt 1 ]; then printf 'unexpected exit %s\n%s\n' "$rc" "$out"; return 1; fi
        if ! grep -qF 'target: Foo.wdl::Foo [workflow]' <<< "$out"; then
            printf 'no reach answer in:\n%s\n' "$out"; return 1
        fi
        if ! grep -qF 'calls task Bar at Foo.wdl:3' <<< "$out"; then
            printf 'answer carries no edge provenance:\n%s\n' "$out"; return 1
        fi
        if grep -qF 'name both a tree and a target' <<< "$out"; then
            printf 'the tool refused instead of answering:\n%s\n' "$out"; return 1
        fi
        return 0
    }
    check 'REAL composition, no stub anywhere: check --reach answers the question it advertises' \
        real_reach

    # --images must reach wdl_reach.py, and a tree with no docker input must say so rather than print
    # an empty list.
    real_reach_images() {
        local out rc src
        out="$(env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_PY="$PYABS" \
                 bash "$ROOT/gsvtk" check --reach --images --repo "$TMP/reach-tree" --wf Foo 2>&1)"; rc=$?
        if [ "$rc" -gt 1 ]; then printf 'unexpected exit %s\n%s\n' "$rc" "$out"; return 1; fi
        if ! grep -qF 'target: Foo.wdl::Foo [workflow]' <<< "$out"; then
            printf -- '--images lost the reach answer it was added to:\n%s\n' "$out"; return 1
        fi
        if ! grep -qF 'image sources:' <<< "$out"; then
            printf -- '--images never reached wdl_reach.py (no image section):\n%s\n' "$out"; return 1
        fi
        if ! grep -qF 'bind no docker input' <<< "$out"; then
            printf 'a workflow binding no image was dropped instead of reported:\n%s\n' "$out"; return 1
        fi
        # The render source has three modes (checkout unset, unusable, real): accept a SKIPPED report
        # or a count, never silence.
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

    # Several names share one tree load (`tree:` prints once per parse). Needles are anchored because
    # the NOT REACHED detail line can end in a path containing "reach-tree: ".
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
        grep -qF 'calls task Bar at Foo.wdl:3' <<< "$out" || {
            printf 'the answer for the first name is missing:\n%s\n' "$out"; return 1; }
        grep -qF 'NOT REACHED — nothing in the tree is reached by Foo.wdl::Bar' <<< "$out" || {
            printf 'the second name did not get its OWN answer (it reaches nothing, and must say so):\n%s\n' \
                   "$out"; return 1; }
        n1="$(printf '%s\n' "$out" | grep -nF -- 'target: Foo.wdl::Foo [workflow]' | head -1 | cut -d: -f1)"
        n2="$(printf '%s\n' "$out" | grep -nF -- 'target: Foo.wdl::Bar [task]' | head -1 | cut -d: -f1)"
        if [ -z "$n1" ] || [ -z "$n2" ] || [ "$n1" -ge "$n2" ]; then
            printf 'the answers are not in the order asked (Foo at %s, Bar at %s):\n%s\n' \
                   "${n1:-none}" "${n2:-none}" "$out"; return 1
        fi
        grep -qF 'name both a tree and a target' <<< "$out" && {
            printf 'the tool refused instead of answering:\n%s\n' "$out"; return 1; }
        return 0
    }
    check 'REAL composition, two names: ONE tree load (tree: counted once), both blocks answered in order' \
        real_reach_two

    # Unknown name among several, through a pipe: only a pipe can reorder block-buffered stdout against
    # stderr, so this checks the flush before the unknown-name report.
    real_reach_unknown() {
        local out rc n1 n2
        out="$(env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_PY="$PYABS" \
                 bash "$ROOT/gsvtk" check --reach --repo "$TMP/reach-tree" --wf Foo --wf NoSuchThing 2>&1)"; rc=$?
        # 1, not 2: the CLI reports a checker's nonzero exit as findings.
        if [ "$rc" -ne 1 ]; then printf 'unexpected exit %s\n%s\n' "$rc" "$out"; return 1; fi
        grep -qF "unknown target 'NoSuchThing'" <<< "$out" || {
            printf 'the unknown name was not reported by name:\n%s\n' "$out"; return 1; }
        grep -qF 'UNKNOWN TARGETS' <<< "$out" || {
            printf 'the run never said which names went unanswered:\n%s\n' "$out"; return 1; }
        grep -qF 'calls task Bar at Foo.wdl:3' <<< "$out" || {
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
    # GSVTK_PY must be the interpreter the dispatched tool actually runs under, so the guard above
    # (which probes $PYABS) describes the python under test.
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
    gsvtk_guard_sites_end "reach-real"      # last statement of that block
else
    skipped "reach-real" "miniwdl absent: the reach answer needs the WDL module, so the composition proof cannot run (a SKIP here is not a pass in CI)"
    gsvtk_note_skip "reach-real"            # the SKIP above covers that region's sites
fi

expect 'a target is FORWARDED: --wf NAME becomes wdl_reach --target NAME' 0 \
    'STUB checks/wdl_reach.py --repo /tmp --target GenotypeBatch' \
    -- run check --reach --repo /tmp --wf GenotypeBatch
# Several names are one wdl_reach call with repeated --target (one tree load), so count the stub lines.
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
# --images qualifies --reach: it must be forwarded, and on its own it is a usage error.
expect '--images is forwarded to the ONE wdl_reach call, not swallowed by the dispatcher' 0 \
    'STUB checks/wdl_reach.py --repo /tmp --target GenotypeBatch --images' \
    -- run check --reach --images --repo /tmp --wf GenotypeBatch
# wdl_reach --images defaults to the checkout's HEAD, so a ref the caller typed must be forwarded.
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
# Stub `java`/`bcftools` on a private PATH so the result does not depend on the local JDK. Each
# refusal has a passing counterpart.
mkdir -p "$TMP/bin17" "$TMP/bin8" "$TMP/bin-nojava"
for d in bin17 bin8; do
    printf '#!/usr/bin/env bash\necho x\n' > "$TMP/$d/bcftools"; chmod +x "$TMP/$d/bcftools"
done
# `java -version` answers on stderr with the version in quotes; a quoted heredoc keeps the quotes.
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
# ------------- a module OUTSIDE the batch chain: the 01 launch route, gated, POSTing nothing -
# ============================================================================================
# A module outside the genotyping chain is a wired route: the rerun launcher resolves its steps from
# profiles/<module>.json like any other. These legs cover, for step 01 of single_sample:
#   the switch: `--module NAME` is on the usage page and turns `--step 01` into a resolved config; an
#     empty or unknown name is refused; `doctor --redact` reports it as the resolver's [env] tier;
#   (a) the body printer prints the body a POST would send, and no request is made;
#   (b) the three mutating modes are refused under GSVTK_READ_ONLY=1 with the dependency absent, and
#     the free sibling still dispatches under the same stamp;
#   (c) the row requirement fires for a step with no offline row source, names `entity_row` and
#     `sample`, refuses before any request, and is satisfied by `--entity`;
#   (d) the unpinned-image refusals fire on the POSTers and name every `--image` input, and stop once
#     they are all pinned.
#
# "No POST" is graded: GSVTK_TERRA_PY points at a wrapper that replaces every firecloud HTTP verb with
# a raiser that prints `GSVTK-BLOCKED <verb> <path>` before raising (printing first, because `submit`
# catches Exception around `entity_exists`). Legs meaning "nothing was requested" assert the marker is
# absent; positive controls show it can print. The target is placeholder coordinates (docs/cli.md).
#
# These legs need an interpreter that can import firecloud; without one they are a counted skip.
TERRAPY="$PY"
if ! "$TERRAPY" -c 'import firecloud' >/dev/null 2>&1 && [ -x .venv/bin/python ] \
   && .venv/bin/python -c 'import firecloud' >/dev/null 2>&1; then
    TERRAPY=.venv/bin/python       # prefer ./.venv when it has firecloud
fi

echo
echo "selftest: cli: a module outside the chain — --module, the body printer, and the POST boundary"

# Placeholder target (docs/cli.md legend). The checkout path deliberately does not exist, so
# `preflight()` takes its `pre-check SKIPPED` arm on every machine.
cat > "$TMP/nonchain.env" <<'CFG'
GSVTK_TERRA_NAMESPACE=your-namespace
GSVTK_TERRA_WORKSPACE=your-sandbox-workspace
GSVTK_BRANCH=main
GSVTK_GATK_SV_CHECKOUT=/nonexistent-checkout-this-leg-never-reads-it
CFG

# Blocking driver: run a terra/ tool with every firecloud HTTP verb replaced by a raiser. Exit 7 means
# "reached the request boundary", distinct from a tool's own refusal (1/2) or a clean run (0).
cat > "$TMP/nonchain-block.py" <<'PYFAKE'
"""Run a terra/ tool with EVERY firecloud HTTP verb replaced by a raiser that says so out loud.

    python nonchain-block.py <tool.py> [tool args...]        # GSVTK_REPO_ROOT names the checkout

The seam is `firecloud.api.__post/__get/__put/__delete/__patch` — the last function a request passes
through before the socket — so this grades the real tool and not a re-telling of it: the real argparse,
the real gates, the real body. The raiser PRINTS before it raises because `terra/batch_rerun_step.py`
catches `Exception` around `entity_exists`; a raiser that only raised would make an attempted request
read as a clean refusal. Nothing here can reach Terra: no verb is left unpatched, and the legs below
that expect a refusal assert this marker is ABSENT rather than trusting the exit code.
"""
import os
import runpy
import sys

repo_root = os.environ["GSVTK_REPO_ROOT"]
sys.path.insert(0, os.path.join(repo_root, "terra"))
sys.path.insert(0, os.path.join(repo_root, "kit"))

tool, sys.argv = sys.argv[1], sys.argv[1:]

import terra                                  # noqa: E402,F401  (the friendly missing-dependency message)
from terra import fapi                        # noqa: E402  == firecloud.api: the request boundary


class Blocked(Exception):
    """Raised by every HTTP verb: reaching one is the leg's failure, printed as it happens."""


def _raiser(verb):
    def raiser(methcall, headers=None, root_url=None, **kwargs):
        sys.stdout.flush()
        print("GSVTK-BLOCKED %s %s" % (verb, methcall), file=sys.stderr)
        sys.stderr.flush()
        raise Blocked(verb)
    return raiser


for _verb in ("__post", "__get", "__put", "__delete", "__patch"):
    setattr(fapi, _verb, _raiser(_verb))

try:
    runpy.run_path(tool, run_name="__main__")
except Blocked:
    sys.exit(7)
PYFAKE

# The wrapper the CLI probes: `-c` goes to the real interpreter (the `py_for terra` probe), and every
# dispatched script goes to the blocking driver.
cat > "$TMP/nonchain-py.sh" <<'SHWRAP'
#!/bin/sh
# GSVTK_TERRA_PY for the non-chain legs: the real python for the CLI's interpreter probe, the blocking
# driver for whatever script the CLI dispatches.
if [ "$1" = "-c" ]; then shift; exec "$GSVTK_REAL_PY" -c "$1"; fi
exec "$GSVTK_REAL_PY" "$GSVTK_BLOCKER" "$@"
SHWRAP
chmod +x "$TMP/nonchain-py.sh"

# ncrun ARGS — the real entry point on the placeholder config, behind the blocking interpreter.
# GSVTK_MODULE is unset so a leg that wants the default module gets it.
ncrun() {
    env -u GSVTK_MODULE GSVTK_CONFIG="$TMP/nonchain.env" GSVTK_WORK="$TMP/work" \
        GSVTK_REPO_ROOT="$ROOT" GSVTK_REAL_PY="$TERRAPY" GSVTK_BLOCKER="$TMP/nonchain-block.py" \
        GSVTK_TERRA_PY="$TMP/nonchain-py.sh" bash "$ROOT/gsvtk" "$@"
}
# The same entry point under the read-only stamp WITH a working interpreter: the CONTROL arm of (b).
ncro() {
    env -u GSVTK_MODULE GSVTK_CONFIG="$TMP/nonchain.env" GSVTK_WORK="$TMP/work" GSVTK_READ_ONLY=1 \
        bash "$ROOT/gsvtk" "$@"
}

# The *_docker inputs the profile marks `--image`, bare, derived from the profile.
NC_KEYS="$($PY -c 'import json
d = json.load(open("profiles/single_sample.json"))
print(" ".join(sorted(k.split(".")[-1] for k, v in d["steps"][0]["images"].items()
                      if v == "--image")))' 2>/dev/null)"
# The keys the profile states instead; the refusal must name none of them.
NC_STATED="$($PY -c 'import json
d = json.load(open("profiles/single_sample.json"))
print(" ".join(sorted(k.split(".")[-1] for k, v in d["steps"][0]["images"].items()
                      if v != "--image")))' 2>/dev/null)"
NC_PINS=()
for _k in $NC_KEYS $NC_STATED; do NC_PINS+=(--image "$_k=us.gcr.io/your-dev-project/test/gatk:PINNED"); done

ncrefusal_omits_stated() {   # a key the profile STATES must not appear in the refusal it prints
    local hit="" k
    if [ -z "$NC_STATED" ]; then
        printf 'the profile states no *_docker key, so this check scored nothing\n'
        return 1
    fi
    for k in $NC_STATED; do
        grep -qE -- "unpinned:[^ ]*\\.${k}\$" "$1" && hit="$hit $k"
    done
    if [ -n "$hit" ]; then
        printf 'the refusal listed as unpinned a key the profile already answers:%s\n' "$hit"
        printf '  a declared version is not an unpinned one, and refusing it anyway is a guard that\n'
        printf '  refuses its own fix\n'
        return 1
    fi
    return 0
}

ncrefusal_names_every_key() {   # every bare *_docker key must appear in the file the refusal printed
    local missing="" k
    if [ -z "$NC_KEYS" ]; then
        printf 'no *_docker keys could be read from the profile, so this check scored nothing\n'
        return 1
    fi
    # Word-split, not `while read`: NC_KEYS is one line.
    for k in $NC_KEYS; do
        grep -qF -- "$k" "$1" || missing="$missing $k"
    done
    if [ -n "$missing" ]; then
        printf 'the refusal never named:%s — it names the inputs it refuses, or it refuses nothing\n' \
          "$missing"
        return 1
    fi
    printf 'every *_docker key the profile marks --image is named by the refusal\n'
}

if ! "$TERRAPY" -c 'import firecloud' >/dev/null 2>&1; then
    gsvtk_note_skip "cli-nonchain"
    skipped "nonchain(firecloud)" "no interpreter that can import firecloud: the non-chain route's body-printer and POST-boundary legs cannot run (a SKIP here is not a pass in CI)"
else
gsvtk_guard_sites "cli-nonchain"
# ---- the switch: --module NAME ------------------------------------------------------------------
expect '--module NAME is on the usage page, with an EMPTY profile (a route must be discoverable)' 0 \
    '--module NAME' -- run --help
expect 'an EMPTY --module is refused, not defaulted to the shipped module' 2 \
    'wants a module NAME' 'silently select the resolved default module' \
    -- ncrun --module '' terra rerun-show --step 01
expect 'and so is a --module whose value is another flag (--module "$FLAG" is the same mistake)' 2 \
    'wants a module NAME' -- ncrun --module --confirm terra rerun-show
expect 'CONTROL for the switch: the shipped map refuses a step outside its chain, by name' 1 \
    "unknown step '01'" 'this map knows 06, 07, 08, 09, 10' -- ncrun terra rerun-show --step 01
# Exit 1: `show` resolves the step, then refuses over unpinned images (graded in (d)).
expect '--module single_sample turns that refusal into a resolved step, whose NEXT gate is the image one' 1 \
    'config 01-GATKSVPipelineSingleSample-rerun' 'root entity sample' 'image inputs are not fully pinned' \
    -- ncrun --module single_sample terra rerun-show --step 01
expect 'the flag selects a MODULE, it is not a single-sample special case: genotyping still resolves' 1 \
    'config 10-GenotypeBatch-rerun' -- ncrun --module genotyping terra rerun-show
expect 'an unknown module names the profile file it looked for (name the missing thing)' 1 \
    'no profile at' -- ncrun --module nosuchmodule-this-leg-invented-it terra rerun-show
# The value is reported as the resolver's env tier, not a private variable of this CLI.
expect 'the value arrives as the RESOLVER'"'"'s env tier, which gsvtk-config reports (one chain)' 0 \
    'GSVTK_MODULE' '[env]' -- ncrun --module single_sample doctor --redact

# ---- (a) the body printer, and the request boundary it never crosses ----------------------------
expect 'the body printer prints the body a POST would send for a step OUTSIDE the chain' 0 \
    '"name": "01-GATKSVPipelineSingleSample-rerun"' '"rootEntityType": "sample"' \
    '"methodPath": "github.com/broadinstitute/gatk-sv/SingleSamplePipeline"' \
    -- ncrun --module single_sample terra rerun-show --step 01 --allow-unpinned-docker
absent 'and NO request of any verb was attempted in order to print it: the raiser never printed' 0 \
    'GSVTK-BLOCKED' -- ncrun --module single_sample terra rerun-show --step 01 --allow-unpinned-docker
# Control: the absent marker above means something only if the marker can appear.
expect 'CONTROL that the boundary is real: the SAME step'"'"'s create reaches __post and the raiser stops it' 7 \
    'GSVTK-BLOCKED __post' 'methodconfigs' \
    -- ncrun --module single_sample terra rerun-create --step 01 --confirm "${NC_PINS[@]}"

# ---- (b) the read-only refusals, for a step no chain leg covers, dependency GENUINELY ABSENT ----
# With no interpreter that can import firecloud, a refusal can only come from the whitelist. Verdicts
# are recorded inline, one per loop turn.
for mode in rerun-create rerun-validate rerun-submit; do
    out="$(env -i PATH=/usr/bin:/bin HOME="$HOME" GSVTK_CONFIG="$TMP/empty.env" \
               GSVTK_WORK="$TMP/work" GSVTK_TERRA_PY=/usr/bin/false GSVTK_READ_ONLY=1 \
               bash "$ROOT/gsvtk" --module single_sample terra $mode --step 01 2>&1)"; rc=$?
    if [ "$rc" -ne 2 ]; then
        fail=$((fail + 1)); printf '  FAIL  non-chain terra %s: exit %s, want 2\n' "$mode" "$rc"
        printf '%s\n' "$out" | head -5 | sed 's/^/          /'
    elif ! grep -qi 'refus' <<< "$out"; then
        fail=$((fail + 1)); printf '  FAIL  non-chain terra %s: exit 2 but no refusal printed\n' "$mode"
    elif grep -qi 'firecloud\|interpreter' <<< "$out"; then
        fail=$((fail + 1))
        printf '  FAIL  non-chain terra %s: reported a missing INTERPRETER, so the whitelist is tested\n' "$mode"
        printf '        after the environment probe and "refused" is a claim about this machine\n'
        printf '%s\n' "$out" | head -5 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    non-chain terra %s refused before any dependency check\n' "$mode"
    fi
done
expect 'and the refusal prints the command to run by hand, with the non-chain step in it' 2 \
    "cd $ROOT && python terra/batch_rerun_step.py submit --step 01" \
    -- env -i PATH=/usr/bin:/bin HOME="$HOME" GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
           GSVTK_TERRA_PY=/usr/bin/false GSVTK_READ_ONLY=1 bash "$ROOT/gsvtk" \
           --module single_sample terra rerun-submit --step 01
expect 'CONTROL: the same stamp still dispatches the non-chain body printer (free by NAME)' 0 \
    '"rootEntityType": "sample"' -- ncro --module single_sample terra rerun-show --step 01 --allow-unpinned-docker

# ---- (c) the row: a profile that states no offline source means the flag decides, or nothing runs --
NC_ROW_PIN=(--allow-unpinned-docker --image "gatk_docker=us.gcr.io/your-dev-project/test/gatk:PINNED")
expect 'the row refusal fires for a step whose profile states no offline row source, naming the FIELD' 1 \
    'is submitted against a sample row' 'the profile'"'"'s `entity_row` for this step is --entity' \
    'pass --entity <name>' \
    -- ncrun --module single_sample terra rerun-submit --step 01 --confirm "${NC_ROW_PIN[@]}"
absent 'and it refused BEFORE the request boundary: no read, no write, nothing' 1 'GSVTK-BLOCKED' \
    -- ncrun --module single_sample terra rerun-submit --step 01 --confirm "${NC_ROW_PIN[@]}"
absent 'CONTROL: a NAMED row clears it (the refusal above can fire, and does not fire here)' 1 \
    'is submitted against a sample row' \
    -- ncrun --module single_sample terra rerun-submit --step 01 --confirm "${NC_ROW_PIN[@]}" \
       --entity NA12878
expect 'and what it clears into is the read-back: the blocked READ is named, not swallowed' 1 \
    'GSVTK-BLOCKED __get' 'cannot read the config this submission would run' \
    -- ncrun --module single_sample terra rerun-submit --step 01 --confirm "${NC_ROW_PIN[@]}" \
       --entity NA12878

# ---- (d) images: what a profile that answers SOME of its image keys changes, per POSTer ----
# Step 01's profile states `linux_docker` (versioned_by: path), so the "nothing states these image
# refs" refusal cannot fire there; the per-key `unpinned:` guard refuses instead. It fires on create
# and submit but not on validate, whose image check sits behind the config read.
NC_REFUSE_IMG=(--step 01 --confirm)
expect "the per-key unpinned refusal fires on rerun-create, for the non-chain step" 1 \
    'image inputs are not fully pinned' 'unpinned:' \
    'cloud_sdk_docker' 'stripy_docker' 'samtools_cloud_docker' 'wham_docker' \
    -- ncrun --module single_sample terra rerun-create "${NC_REFUSE_IMG[@]}"
ncrun --module single_sample terra rerun-create "${NC_REFUSE_IMG[@]}" > "$TMP/nonchain-img.txt" 2>&1
check 'and it names EVERY *_docker input the profile leaves to the flag, not one convenient example' \
    ncrefusal_names_every_key "$TMP/nonchain-img.txt"
check 'and it names NONE of the keys the profile states itself — a declared version is not an unpinned one' \
    ncrefusal_omits_stated "$TMP/nonchain-img.txt"
# create refuses before touching anything; validate reaches the config read first, so it gets a
# control that the read was attempted.
absent 'FINDING: rerun-validate reaches no image refusal for an unpinned step 01' 7 \
    'image inputs are not fully pinned' 'nothing states these image refs' \
    -- ncrun --module single_sample terra rerun-validate "${NC_REFUSE_IMG[@]}"
expect 'and the control that validate got that far: the read it attempted is named, not swallowed' 7 \
    'GSVTK-BLOCKED __get' \
    -- ncrun --module single_sample terra rerun-validate "${NC_REFUSE_IMG[@]}"
# submit refuses once a row is named: the entity-row guard sits in front of the image guard.
expect 'the unpinned refusal still fires on rerun-submit once a row is named (the row guard is in front)' 1 \
    'image inputs are not fully pinned' 'unpinned:' \
    -- ncrun --module single_sample terra rerun-submit --step 01 --confirm --entity NA12878
absent 'and it refuses BEFORE the read: no request marker on the refusal path' 1 \
    'GSVTK-BLOCKED' \
    -- ncrun --module single_sample terra rerun-submit --step 01 --confirm --entity NA12878
# The "nothing states these image refs" refusal, aimed at a step whose profile has no `images` map.
# Its other arm (every *_docker input marked --image) is unreachable with the shipped profiles.
for mode in rerun-create rerun-validate rerun-submit; do
    expect "'nothing states these image refs' still fires on $mode for a step whose profile states none" 1 \
        'nothing states these image refs' 'no `images` entry in this step' 'steps[].images' \
        -- ncrun --module genotyping terra $mode --step 10 --confirm
done
absent 'CONTROL: state them all and the refusal is gone — a missing-statement guard, not a wall' 7 \
    'nothing states these image refs' \
    -- ncrun --module single_sample terra rerun-create --step 01 --confirm "${NC_PINS[@]}"
gsvtk_guard_sites_end "cli-nonchain"
fi

# ------------------------------------- terra/batch_status.py: what a price line may claim ----
# ============================================================================================
# What `terra/batch_status.py --costs` prints:
#   1. `--costs PREFIX` prices configs outside the chain (single-sample, rerun, snapshot copies).
#   2. The TOTAL states its scope (`chain 06->10` vs `matching '01-' over 1 config(s)`) and its kind
#      (`MEASURED` only with no floor or hole in it, else `FLOOR`).
#   3. An unknown is never a zero and never part of a total: unreadable metadata, no call records,
#      a call with no VM end, work below a sub-workflow, and Terra's own `cost` (0 until the workflow
#      finishes) print as UNKNOWN/pending and are counted and named.
#
# Runs offline: the fake answers `terra.submissions`, `fapi.get_submission` and
# `fapi.get_workflow_metadata`, every other `fapi` verb raises, and `GSVTK_TERRA_API_ROOT` is a closed
# port. One leg runs the real tool against that port and requires a named failure.
#
echo
echo "selftest: cli: what batch_status --costs prices, and what it refuses to print as zero"
# TERRAPY was resolved above.
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
                        sub("01-SingleSample-devfix", "sub-ss", "2026-09-23T09:00:00Z"),
                        sub("10-GenotypeBatch_r3f2k", "sub-snap", "2026-09-24T11:00:00Z")],
        "workflows": wfs({"sub-06": [("wf-06aaaaaaaa", "Succeeded", 3.25, [])],
                          "sub-ss": [("wf-ssa11111", "Succeeded", 4.0, []),
                                     ("wf-ssb22222", "Succeeded", 0.8, [])],
                          "sub-snap": [("wf-snap0001", "Succeeded", 7.0, [])]}),
        "metadata": dict([("wf-06aaaaaaaa", {"calls": {"G.a": [call(0, 45.0)]}}),
                          ("wf-snap0001", {"calls": {"G.b": [call(0, 100.0)]}})] + list(SS.items())),
    },
    # Only a non-chain config has ever been submitted, so the CHAIN table has nothing to price at all.
    "solo": {
        "submissions": [sub("01-SingleSample-devfix", "sub-ss", "2026-09-23T09:00:00Z")],
        "workflows": wfs({"sub-ss": [("wf-ssa11111", "Succeeded", 4.0, []),
                                     ("wf-ssb22222", "Succeeded", 0.8, [])]}),
        "metadata": dict(SS),
    },
    # Two ways a figure cannot be read, both of which the old code printed as `vm_min=0`: the metadata
    # fetch fails, and the metadata holds no call records yet.
    "opaque": {
        "submissions": [sub("06-GenerateBatchMetrics", "sub-06", "2026-09-22T01:00:00Z"),
                        sub("07-FilterBatchSites", "sub-07", "2026-09-22T02:00:00Z", "Running")],
        "workflows": wfs({"sub-06": [("wf-06aaaaaaaa", "Succeeded", 0.0, [])],
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
                          "sub-07": [("wf-07aaaaaaaa", "Succeeded", 6.0, [])]}),
        "metadata": {"wf-06aaaaaaaa": {"calls": {"F.a": [call(0, 10.0)],
                                                 "F.b": [call(0, 0, vmEndTime=None, end=None)]}},
                     "wf-07aaaaaaaa": {"calls": {"G.sub": [call(0, 20.0, subWorkflowId="wf-child")]}}},
    },
    # A submission Terra accepted and has not started: the record carries no workflows at all.
    "queued": {
        "submissions": [sub("01-SingleSample-devfix", "sub-ss", "2026-09-23T09:00:00Z")],
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

# stat_case SCENARIO [batch_status arg...] — one priced table.
stat_case() {
    env GSVTK_STATUS_FILE="$ROOT/terra/batch_status.py" GSVTK_REPO_ROOT="$ROOT" GSVTK_CONFIG="$TMP/status.env" \
        GSVTK_WORK="$TMP/work" GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/" \
        "$TERRAPY" "$TMP/status-terra.py" "$@"
}

# Skip region: one skip withholds every site below this marker (see declared.sh).
gsvtk_guard_to_end "cli-status-pricing"
if ! "$TERRAPY" -c 'import firecloud' >/dev/null 2>&1; then
    gsvtk_note_skip "cli-status-pricing"
    skipped "status-pricing(firecloud)" \
        "no firecloud-capable interpreter: the pricing legs cannot run (a SKIP here is not a pass in CI)"
else
    # --- the offline belt: the real tool against a closed port ---------------------------------------
    expect "the offline belt holds: the real tool names the unusable listing, it does not answer empty" 1 \
        'neither client could list it' -- \
        env GSVTK_REPO_ROOT="$ROOT" GSVTK_CONFIG="$TMP/status.env" GSVTK_WORK="$TMP/work" \
            GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/" "$TERRAPY" terra/batch_status.py --costs
    absent "and it does not fall through to a price table built on nothing" 1 'TOTAL' -- \
        env GSVTK_REPO_ROOT="$ROOT" GSVTK_CONFIG="$TMP/status.env" GSVTK_WORK="$TMP/work" \
            GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/" "$TERRAPY" terra/batch_status.py --costs 01-

    # --- without --costs -----------------------------------------------------------------------------
    expect 'without --costs the non-chain config prints as an unpriced (other config) row' 0 \
        '(other config) 01-SingleSample-devfix: Done' -- stat_case mixed
    count "and the table that prints adds nothing: zero vm_min lines" 'vm_min' 0 -- stat_case mixed

    # --- the prefix arm prices what the chain walk cannot reach --------------------------------------
    expect "the chain table cannot price the single-sample config: it lists the name and stops" 0 \
        '(other config) 01-SingleSample-devfix: Done' -- stat_case mixed --costs
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

    # --- the total says what it covers and what kind of number it is ---------------------------------
    expect "the total states its SCOPE, so a one-config figure is never quoted as a chain bill" 0 \
        'TOTAL chain 06->10' -- stat_case chain --costs
    expect "a complete chain is the only shape that gets MEASURED (45+20+10+5+100 = 180.0)" 0 \
        'TOTAL chain 06->10: vm_min=180.0  MEASURED  (5/5 row(s) readable)' -- stat_case chain --costs
    expect "an incomplete chain is a FLOOR and names every step it could not price" 0 \
        '4 chain step(s) never submitted (07, 08, 09, 10)' -- stat_case mixed --costs
    expect "and the same run names the configs the total deliberately excludes" 0 \
        'so none of them is in that total; price one by prefix: --costs 01-SingleSample-devfix' \
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
    # scripts/check_doc_flags.py relies on --help showing the PREFIX as optional.
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
        '01-SingleSample-devfix, 06-GenerateBatchMetrics, 10-GenotypeBatch_r3f2k' \
        -- stat_case mixed --costs 99
    expect "--costs and --wait together is refused: two questions, one answer each" 1 'two questions' \
        -- stat_case mixed --costs 01- --wait 01-
    expect "CONTROL: --wait ALONE still dispatches the poller (that refusal is not a blanket ban)" 0 \
        '01-SingleSample-devfix submission=Done' -- stat_case mixed --wait 01-
    expect "the watcher's terminal dump prints the SAME priced line as the table, not a second accounting" 0 \
        'wf-ssa11111 Succeeded vm_min=40.0 calls=2 failed=none cost=4.0' -- stat_case mixed --wait 01-
fi

# ------------------------------------------------ docker/image_capture.py
# The build side of the digest handoff, offline: digests come from GSVTK_IMAGE_DIGESTS_JSON (which the
# tool must announce as not a live registry read), and the dockers.json board is a throwaway git repo
# because the tool reads it with `git show`.
printf '\nimage_capture: the capture a build leaves behind\n'
BOARD="$TMP/board"
mkdir -p "$BOARD/inputs/values"
python3 - "$BOARD/inputs/values/dockers.json" <<'PY'
import json, sys
# The 12 step-01 keys a branch build cannot refresh plus the two it can, at placeholder paths. Hand-authored
# shape, not a sanitised live dump: the key NAMES are the real ones (they are the join), the values are not.
keys = ["cnmops_docker", "gatk_docker", "genomes_in_the_cloud_docker", "linux_docker",
        "manta_docker", "samtools_cloud_docker", "scramble_docker", "stripy_docker", "sv_base_docker",
        "sv_base_mini_docker", "sv_pipeline_docker", "sv_pipeline_qc_docker", "wham_docker"]
d = {"name": "dockers"}
for k in keys:
    d[k] = f"us.gcr.io/<project>/<ns>/gatk-sv/{k.replace('_','-').replace('-docker','')}:{'2026-10-08-v1.1.1-a55c498f'}"
d["sv_pipeline_qc_docker"] = d["sv_pipeline_docker"]
# linux_docker is deliberately left as what upstream really carries: a GCE image FAMILY with no tag in it.
# Nothing resolves it to a digest (`gcloud container images describe` needs a tag or a digest to name), which
# so the capture states it as unresolved instead of inventing a version.
d["linux_docker"] = "marketplace.gcr.io/google/ubuntu1804"
# and `cloud_sdk_docker` is what upstream actually carries: a DOCKER HUB name with no tag in it. Not GCR or
# Artifact Registry, so `gcloud container images describe` has nothing to describe and the name floats --
# the second state A26 found, and the one this tool does NOT claim to answer.
d["cloud_sdk_docker"] = "google/cloud-sdk"
json.dump(d, open(sys.argv[1], "w"), indent=2)
PY
( cd "$BOARD" && git init -q . >/dev/null 2>&1 \
  && git -c user.email=selftest@example.invalid -c user.name=selftest add -A >/dev/null 2>&1 \
  && git -c user.email=selftest@example.invalid -c user.name=selftest commit -qm fixture >/dev/null 2>&1 )
BOARD_SHA="$(cd "$BOARD" && git rev-parse HEAD 2>/dev/null)"
TAG="selftest-branch-${BOARD_SHA:0:6}"
SER="$TMP/build.serial"
cat > "$SER" <<EOF
building at $BOARD_SHA
Building image: sv-pipeline:$TAG ...
docker push us.gcr.io/<project>/<ns>/gatk-sv/sv-pipeline:$TAG
== updated dockers.json entries ==
+++ b/inputs/values/dockers.json
+    "sv_pipeline_docker": "us.gcr.io/<project>/<ns>/gatk-sv/sv-pipeline:$TAG",
+    "sv_pipeline_qc_docker": "us.gcr.io/<project>/<ns>/gatk-sv/sv-pipeline:$TAG",
### GATK_SV_BUILD_RESULT=SUCCESS (images pushed to us.gcr.io/<project>/<ns>/gatk-sv :$TAG) ###
EOF
python3 - "$TMP/digests.json" "$TAG" <<'PY'
import json, sys
out_path, tag = sys.argv[1], sys.argv[2]
d = {f"us.gcr.io/<project>/<ns>/gatk-sv/sv-pipeline:{tag}": "sha256:" + "a" * 64}
for k in ["cloud-sdk", "cnmops", "gatk", "genomes-in-the-cloud", "linux-docker", "manta", "samtools-cloud",
          "scramble", "stripy", "sv-base", "sv-base-mini", "wham"]:
    d[f"us.gcr.io/<project>/<ns>/gatk-sv/{k}:2026-10-08-v1.1.1-a55c498f"] = "sha256:" + "b" * 64
json.dump(d, open(out_path, "w"))
PY
CAP="$TMP/capture.json"
IC_ENV=(env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_IMAGE_DIGESTS_JSON="$TMP/digests.json")
expect "a build's own text yields a digest for 12 of the 14 keys this step needs" 0 \
    "12 of 14 key(s) pinned" -- "${IC_ENV[@]}" "$PY" docker/image_capture.py \
    --from-serial "$SER" --resolve --module single_sample --step 01 --checkout "$BOARD" --out "$CAP"
expect "and the two keys the build refreshed are BUILT with the target they came from, not passed through as refs" 0 \
    '"state": "BUILT"' '"target": "sv-pipeline"' -- grep -o -E '"state": "BUILT"|"target": "sv-pipeline"' "$CAP"
expect "the keys no build can produce are EXTERNAL with their origin named, never labelled a build of the commit" 0 \
    '"state": "EXTERNAL"' 'inputs/values/dockers.json @' -- grep -o -E \
    '"state": "EXTERNAL"|"source_ref_origin": "inputs/values/dockers.json @[^"]*"' "$CAP"
expect "every ref the capture states is digest-form, which is the launcher's own guard, not a style rule" 0 \
    '@sha256:' -- grep -o '@sha256:[0-9a-f]*' "$CAP"
expect "the two refs upstream ships WITHOUT a tag are named as unresolved, each with its own reason" 0 \
    'no digest obtained for google/cloud-sdk' 'versioned by its path' -- "$PY" -c "
import json, subprocess, sys
d = json.load(open('$CAP'))
for k in ('GATKSVPipelineSingleSample.cloud_sdk_docker', 'GATKSVPipelineSingleSample.linux_docker'):
    print(d['unresolved'][k])
"
expect "and the run says its digests were captured, so a seam run is never read as an answer about the registry" 0 \
    'NOT a live registry read' -- "${IC_ENV[@]}" "$PY" docker/image_capture.py \
    --from-serial "$SER" --resolve --module single_sample --step 01 --checkout "$BOARD" --out "$CAP"
expect "--dry-run prints the capture and writes nothing at all" 0 'nothing written' -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_IMAGE_DIGESTS_JSON="$TMP/digests.json" \
    "$PY" docker/image_capture.py --from-serial "$SER" --resolve --module single_sample --step 01 \
    --checkout "$BOARD" --out "$TMP/never.json" --dry-run
check "and the file --dry-run named really does not exist" test ! -e "$TMP/never.json"
# --- refusals, each naming the flag it wants -------------------------------------------------------
expect "a missing build text is refused naming --from-serial, and this tool never re-runs a build" 2 \
    'no build text' '--from-serial' -- env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
    "$PY" docker/image_capture.py
printf 'building at %s\nno pinboard diff here\n' "$BOARD_SHA" > "$TMP/dead.serial"
expect "a build that never reached the pinboard is NO REFRESH BLOCK, not a capture that quietly names nothing" 3 \
    'NO REFRESH BLOCK' -- env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
    "$PY" docker/image_capture.py --from-serial "$TMP/dead.serial"
printf '{"x":"not-a-digest"}\n' > "$TMP/bad.digests.json"
expect "a seam whose values are not digests cannot smuggle a tag through, and a capture that would name nothing is refused" 4 \
    'CAPTURE WOULD SCORE NOTHING' -- env GSVTK_CONFIG="$TMP/empty.env" \
    GSVTK_IMAGE_DIGESTS_JSON="$TMP/bad.digests.json" "$PY" docker/image_capture.py \
    --from-serial "$SER" --resolve --module single_sample --step 01 --checkout "$BOARD" --out "$CAP"
expect "a step that binds no image input is refused for having none, rather than scored as clean" 3 \
    'binds no *_docker inputs' -- env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
    "$PY" docker/image_capture.py --from-serial "$SER" --module genotyping --step 10
printf '{}' > "$TMP/empty.digests.json"
expect "an EMPTY digest map ({}) is refused as unusable, not read as 'this build produced nothing'" 3 \
    'is empty or not an object' -- env GSVTK_CONFIG="$TMP/empty.env" \
    GSVTK_IMAGE_DIGESTS_JSON="$TMP/empty.digests.json" "$PY" docker/image_capture.py \
    --from-serial "$SER" --resolve --module single_sample --step 01 --checkout "$BOARD" --out "$CAP"
expect "and the seam refusal names the variable, so a typo is findable from the answer" 2 \
    'GSVTK_IMAGE_DIGESTS_JSON' -- env GSVTK_CONFIG="$TMP/empty.env" GSVTK_IMAGE_DIGESTS_JSON="$TMP/nope.json" \
    "$PY" docker/image_capture.py --from-serial "$SER" --resolve --module single_sample --step 01 --out "$CAP"
expect "a capture path inside the git-tracked set is REFUSED before it is written, because it holds coordinates" 3 \
    'CAPTURE IS TRACKED' -- "${IC_ENV[@]}" "$PY" docker/image_capture.py --from-serial "$SER" --resolve \
    --module single_sample --step 01 --checkout "$BOARD" --out "$ROOT/README.md"
expect "CONTROL: the same command with an untracked --out writes (that refusal is not a blanket ban)" 0 \
    'wrote' -- "${IC_ENV[@]}" "$PY" docker/image_capture.py --from-serial "$SER" --resolve \
    --module single_sample --step 01 --checkout "$BOARD" --out "$TMP/ok.json"
expect "a module profile with no such step is refused by name, listing the steps that do exist" 2 \
    'has no step' -- "${IC_ENV[@]}" "$PY" docker/image_capture.py --from-serial "$SER" --resolve \
    --module single_sample --step 99 --checkout "$BOARD" --out "$CAP"
expect "the key nothing can resolve is left to the profile, and the reason says where the answer lives" 0 \
    'versioned by its path' 'profiles/<module>.json' 'marketplace.gcr.io/google/ubuntu1804' -- grep -o -E \
    'versioned by its path|profiles/<module>\.json|marketplace\.gcr\.io/google/ubuntu1804' "$CAP"
expect "CONTROL: one capture plus the profile's one declaration supplies the launch with NO exemption flag" 4 \
    'image capture: 12 of 14 keys supplied' 'linux_docker' 'profile/images' -- "$PY" -c "
import json, os, subprocess, sys
cap = json.load(open('$TMP/ok.json'))
p = '$TMP/clean.json'; json.dump(cap, open(p, 'w'))
rc = subprocess.call([sys.executable, 'terra/batch_rerun_step.py', 'show', '--step', '01', '--image-from', p],
                     env={**os.environ, 'GSVTK_MODULE': 'single_sample', 'GSVTK_CONFIG': '$TMP/empty.env',
                          'GSVTK_WORK': '$TMP/work'})
sys.exit(rc)
"

# With a capture, the profile's declaration and one --image for the untagged upstream name, a POSTing
# mode gets no image refusal. It then fails on the config read (no credentials); the wrapper prints that.
expect "capture + profile declaration + one flag pins 14 of 14: no image refusal, no exemption flag" 0 \
    'no image refusal' -- "$PY" -c "
import json, os, subprocess, sys
p = '$TMP/ok.json'
env = {**os.environ, 'GSVTK_MODULE': 'single_sample', 'GSVTK_CONFIG': '$TMP/empty.env',
       'GSVTK_WORK': '$TMP/work', 'GSVTK_GATK_SV_CHECKOUT': '/nonexistent-checkout'}
argv = [sys.executable, 'terra/batch_rerun_step.py', 'create', '--step', '01', '--confirm',
        '--image-from', p, '--image', 'cloud_sdk_docker=google/cloud-sdk:481.0.0']
out = subprocess.run(argv, env=env, capture_output=True, text=True)
text = out.stdout + out.stderr
bad = [n for n in ('not fully pinned', 'nothing states these image refs', 'pinned but untagged')
       if n in text]
print('exit', out.returncode, '| tail:', text.strip().splitlines()[-1][:90] if text.strip() else '')
print('no image refusal' if not bad else 'IMAGE REFUSAL: ' + ', '.join(bad))
"

# A real build log as the GCE serial console prints it, with digests from the seam.
cat > "$TMP/console.serial" <<'CSERIAL'
[ 2880.992780] google_metadata_script_runner[1449]: startup-script: == updated dockers.json entries ==
[ 2880.992900] google_metadata_script_runner[1449]: startup-script: +  "sv_pipeline_docker": "us.gcr.io/<project>/<namespace>/gatk-sv/sv-pipeline:probe-branch-abc123",
[ 2880.992920] google_metadata_script_runner[1449]: startup-script: +  "sv_pipeline_qc_docker": "us.gcr.io/<project>/<namespace>/gatk-sv/sv-pipeline:probe-branch-abc123",
2026-10-22T16:53:43.460561+00:00 gsv-<instance> google_metadata_script_runner[1449]: startup-script: docker push us.gcr.io/<project>/<namespace>/gatk-sv/sv-pipeline:probe-branch-abc123
2026-10-22T16:53:44.112200+00:00 gsv-<instance> google_metadata_script_runner[1449]: startup-script: Building image: sv-pipeline
### GATK_SV_BUILD_RESULT=SUCCESS (images pushed to us.gcr.io/<project>/<namespace>/gatk-sv :probe-branch-abc123) ###
CSERIAL
printf '{"us.gcr.io/<project>/<namespace>/gatk-sv/sv-pipeline:probe-branch-abc123": "sha256:%s"}\n' \
    9c4b764e01be6e4b7a0e0a3f2b8e6b1d4a0c8f3e7d9b1a2c3d4e5f60718293a4 > "$TMP/console.digests.json"
# The serial console prefixes every startup-script line (syslog stamp or kernel timestamp), so
# `$`-anchored patterns no longer anchor.
expect "a build log the serial console re-labelled still yields a capture" 0 \
    'wrote' '2 of 14 key(s) pinned' -- env GSVTK_CONFIG="$TMP/empty.env" \
    GSVTK_IMAGE_DIGESTS_JSON="$TMP/console.digests.json" "$PY" docker/image_capture.py \
    --from-serial "$TMP/console.serial" --resolve --module single_sample --step 01 \
    --at-sha e9bf3d125adb4d5021683318cd3c4221d2711fde --out "$TMP/console.json"
# Count BUILT in the file, not the summary line.
expect "and BUILT provenance survives the console label: 2 keys, 1 image, neither degraded to EXTERNAL" 0 \
    'EXACTLY_TWO_BUILT' -- sh -c 'test "$(grep -c BUILT "$1")" = 2 && echo EXACTLY_TWO_BUILT' _ \
    "$TMP/console.json"
expect "digests without a commit named are refused, because a capture that cannot say WHICH build is a guess" 2 \
    'NO COMMIT NAMED' -- env GSVTK_CONFIG="$TMP/empty.env" \
    GSVTK_IMAGE_DIGESTS_JSON="$TMP/console.digests.json" "$PY" docker/image_capture.py \
    --from-serial "$TMP/console.serial" --resolve --module single_sample --step 01 --out "$TMP/nosha.json"

expect "the tool's own suite runs in the gate (28 assertions, none of which need a network)" 0 \
    'image_capture:' ', 0 failed' -- "$PY" docker/image_capture.py --selftest

printf '\ncli selftest: %s ok, %s skipped, %s failed\n' "$ok" "$skip" "$fail"
# Declared and reached site sets must match (see declared.sh).
gsvtk_declared_report "cli selftest" "$SELF" "$((ok + fail))" "$skip" || fail=$((fail + 1))
[ "$fail" -eq 0 ]
