#!/usr/bin/env bash
# scripts/selftest.d/entity.sh — checks/terra_entity_check.py, graded against a synthetic corpus that
# contains every refusal shape the deriver can make.
#
#     bash scripts/selftest.d/entity.sh .venv/bin/python
#
# What this phase owns that nothing else does
# -------------------------------------------
# Stage A of docs/plan-launch-any-module.md turns "which entity row does this launch config run against"
# into a derivation: the name key `${this.<etype>_id}`, cross-checked against the type of a SHIPPED
# entity table, refusing on zero name keys, many name keys, a type with no table, and a read that is not a
# column. Stage B's first half asks a different question of the same corpus: what does the flavor's own
# `workspace.tsv.tmpl` declare, since that first column is the offline oracle for every `${workspace.X}`
# binding — and what bundle every value in every config comes from. Both are graded here for the same
# reason: one reader, six flavors, four statuses (OK / ABSENT / MALFORMED / DUPE), and the two words on the
# oracle census that say what it did and did not do — `values=not-read` (the second column is still never
# opened) and `enforced=yes` (the value layer now refuses, so `GSVTK-ENTITY-UNCHECKED`'s workspace-read number
# went to a measured 0 and `GSVTK-VALUE` / `GSVTK-VALUE-SUMMARY` carry the value census). Every one of those
# is a claim about a corpus, and the corpus that proves them is
# upstream's — which a CI machine does not have. So the shapes are committed here instead: one synthetic
# `inputs/templates/terra_workspaces/` tree, six flavors, five entity/membership tables, six
# workspace-named TSVs, forty-two configs, one config per shape, including the trap (a config whose member
# collection is a DIFFERENT type from its name key) and the bait table that makes the dead member rule look
# right.
#
# Fixtures (scripts/selftest.d/fixtures/entity/, no machine path and no upstream data in any of them)
# -----------------------------------------------------------------------------------
#   alpha/entity_sample_set.tsv.tmpl       entity:sample_set_id  merged_bincov median_cov sample_sets
#   alpha/entity_sample_sets.tsv.tmpl      entity:sample_sets_id ploidy_table outlier_filtered_pesr_vcf
#                                          ^ the bait: the MEMBER type's table, which the member rule
#                                            would happily accept as the answer for MemberTrap
#   alpha/entity_sample_a|b.tsv.tmpl       two tables of one type (sample), each carrying half of what
#                                          TwoTablesSplit reads — the no-union rule, as a fixture
#   beta/entity_participant.tsv.tmpl       name column only, no attribute column (upstream's real shape)
#   beta/workspace.tsv.tmpl                the oracle, ONE row, carrying the `workspace:` prefix on row 1
#                                          (upstream's real shape): it declares `cloud_sdk_docker`
#   beta/unreadable_header.tsv.tmpl        `entity:{{ flavour }}_id` — an unreadable type, named not guessed
#   alpha/workspace_other.tsv.tmpl         a workspace table under a NEAR-MISS name: named when alpha's
#                                          oracle is missing, and never read
#   alpha/workflow_configurations/workspace.tsv.tmpl
#                                          the oracle's EXACT name in the wrong directory. The flavor's own
#                                          table is a direct child of the flavor directory, because the
#                                          flavor is read off where its configs live
#   gamma/workspace.tsv.tmpl               the kitchen sink: a `workspace:` prefix on a row that is NOT the
#                                          first, the duplicate that strip creates (`sv_pipeline_docker`,
#                                          rows 2 and 3), a `{{ }}` name, a name with a space in it, and a
#                                          row with three tab-separated fields
#   gamma/GammaValues.json.tmpl            a config, so the flavor exists: the flavor list comes from where
#   epsilon/EpsilonValues.json.tmpl        configs live, never from where tables were found, or `ABSENT`
#   zeta/ZetaValues.json.tmpl              could never be reported and the check could never fire
#   delta/DeltaNoTables.json.tmpl          a flavor with a config and no TSV at all: ABSENT, said out loud
#   epsilon/workspace.tsv.tmpl             a duplicate and nothing else, so `status=DUPE` is REACHABLE (gamma
#                                          is both, and the earlier word wins the label); that flavor's config
#                                          binds a SECOND, non-`_docker` value beside the `*_docker` one,
#                                          because the docker rule wins by precedence and would otherwise
#                                          leave `no-oracle-dupe` a word with no instance anywhere
#   zeta/workspace.tsv.tmpl                present and EMPTY (0 bytes): `attrs=0 status=OK`, which is a
#                                          different answer from ABSENT and must never collapse into it
#   .../SingleNameKey.json.tmpl            the pass, with a ${workspace.*} binding and a {{ jinja }} value
#   .../ZeroNameKey.json.tmpl              reads an attribute but no name key
#   .../ZeroNameKeyNoThis.json.tmpl        reads nothing through `this.` at all (upstream's two write-back
#                                          configs are this shape)
#   .../TwoNameKeys.json.tmpl              two distinct <etype>s -> refuse
#   .../MemberTrap.json.tmpl               ${this.sample_set_set_id} + three ${this.sample_sets.<attr>} —
#                                          the MergeBatchSites shape, the trap this whole lane is about
#   .../MemberCollection.json.tmpl         member collection IS a column of the derived table -> pass,
#                                          with the member ATTRIBUTES reported as unchecked
#   .../CollectionNotColumn.json.tmpl      `${this.samples.*}` where no shipped table has `samples`
#   .../LacksReadColumn.json.tmpl          derived type shipped, one read not a column
#   .../NoTableAtAll.json.tmpl             derived type (`donor`) shipped nowhere at all
#   .../TwoTablesSplit.json.tmpl           two tables of the type, each lacking a different read
#   .../SplitOneTableOK.json.tmpl          ... and one table that does cover it is enough
#   .../IdNoType.json.tmpl                 `${this.id}` — name-key-shaped, carries no type
#   .../ListForm.json.tmpl                 `${[this.a, this.b]}` — the grammar that a "starts with `this.`"
#                                          reader loses (upstream really ships one of these)
#   .../NestedRead.json.tmpl               a read inside a nested object, reported as nested
#   .../BareThis.json.tmpl                 `this.x` outside `${ }` — a literal, counted, not read
#   .../DuplicateKey.json.tmpl             one key bound twice, last-wins named
#   .../DroppedList.json.tmpl              one key bound twice whose DROPPED value is a LIST carrying a
#                                          `{{ }}` marker and a `${workspace.*}` read: both are counted, one
#                                          is refused (D1)
#   RootFlavor.json.tmpl                   a config directly under the corpus: flavor `(root)`, whose own
#                                          oracle is ABSENT (G1)
#   .../TwoWorkflows.json.tmpl              two binding prefixes in one file: `wf=` prints BOTH, and
#                                          `namematch=no` is the honest label (G4)
#   beta/entity_panel.tsv.tmpl             a header whose THIRD column is `{{ flavour }}_unprintable`: the
#                                          column is counted unreadable, never guessed (G2)
#   beta/membership_batch.tsv.tmpl         the corpus's only `membership:` table, plus
#   .../MembershipTable.json.tmpl          the config that picks it: `table_kind=membership` (G3)
#   .../UnbracedBinding.json.tmpl          a literal carrying a `${` no reader can close: `unbraced=1` and
#   .../UnterminatedBundle.json.tmpl       `cannot-parse`, and the same refusal in the value layer;
#                                          `{{ reference_resources.tracks` with no close, refused both ways
#   .../MultiLineBundle.json.tmpl          a real `{{ ... }}` SPANNING LINES: neutralised once, disclosed,
#                                          never refused — the control for `re.S` reaching this file (M2)
#   .../DroppedList.json.tmpl              one key bound twice whose DROPPED value is a LIST carrying a
#                                          `{{ }}` marker and a `${workspace.*}` read: both counted, the
#                                          unattributed-bundle refusal stays silent (D1)
#   beta/KeyLeafDocker.json.tmpl           the same `gs://` image under `manta_docker` AND `manta_image`:
#   beta/KeyLeafMissing.json.tmpl          both refuse; the same pair for an undeclared
#                                          `${workspace.X}`: an exclusion no longer outranks a refusal (K1)
#   zeta/undecodable.tsv.tmpl              a header line carrying ONE raw 0x92 byte: `UNREADABLE`, the same
#                                          word an unreadable header earns — never a traceback (U2)
#   .../Undecodable.json.tmpl              well-formed JSON apart from one raw 0x92 byte: `cannot-parse`
#                                          AND `values-unreadable`, because both layers read the file (U1)
#   .../CannotParse.json.tmpl              truncated JSON: a finding about the document, not a licence to
#                                          say nothing
#   .../NoValues.json.tmpl                 one key whose value is an EMPTY object: 0 scalar(s), so
#                                          `configs-no-values` has an instance of its own and can never be
#                                          read as `configs-unclassified` — "nothing in it" and "nobody can
#                                          read it" are two answers (V7 pins the pair, and V1/E17e the other
#                                          two of the four)
#   .../beta/TestBundleRoot.json.tmpl      a `{{ test_gatksv_cohort.* }}` render: a TEST root recognised by
#                                          NAME, bound in a tree that ships a DIFFERENT test profile
#   .../beta/TestBundleCoordinate.json.tmpl  `gs://` inside a string carrying no read at all — the value
#                                          `literal` would otherwise have supplied to a launch
#   .../beta/TestBundleShipped.json.tmpl   a root naming the stem of a profile this tree really ships.
#                                          Those three ARE the POSITIVE CONTROL for `test-bundle` (V8):
#                                          B0 measured 0 instances at `e1909d2f`, so this corpus is the
#                                          class's entire evidence, one leg per arm
#   inputs/values/test_single_sample_NA12878.json   the shipped test profile that config names. Only its
#   inputs/values/dockers.json             NAME is ever read: `SENTINEL-NEVER-OPENED` sits in both files and
#                                          appears in no output (V8 asserts that), and `dockers` is the
#                                          shipped NON-test profile the name rule must not list
#   .../delta/MixedNoOracle.json.tmpl      `${[workspace.x, this.y]}` in a flavor with NO workspace table:
#   .../beta/MixedWorkspaceMissing.json.tmpl  `mixed-no-oracle-absent`. The same shape in an OK flavor
#                                          naming an undeclared attribute: `mixed-workspace-missing`, a
#                                          refusal that leaves `unresolved=` alone (V9 pins that pair)
#   .../beta/CommandCarriesDocker.json.tmpl
#   .../beta/CommandCarriesNoDocker.json.tmpl
#                                          the R20 pair (V10): one missing `${workspace.*}` attribute, twice,
#                                          in a command that also names `${workspace.cloud_sdk_docker}` and
#                                          in the same command without the image. Before R20 the first was
#                                          `docker` (an exclusion, so it refused nothing) and the second
#                                          `mixed` (which does): the same missing attribute, opposite answers,
#                                          decided by whether somebody concatenated
#   .../output_configurations/SyntheticOutputs.json.tmpl
#                                          binding prefix `Synthetic`, file stem `SyntheticOutputs`: the
#                                          workflow comes from the prefix, and the mismatch is printed
#
# The cure legs (ruling R21), and why they copy the corpus instead of editing it
# -----------------------------------------------------------------------------------
# Every workspace refusal now NAMES the fix (`To clear it: …`) in the same sentence as its diagnosis, and the
# C-legs run each named fix instead of describing it: `cp -R` of the fixture tree into this phase's own `$TMP`,
# the target filename / directory / attribute name needles OUT OF THE PRINTED SENTENCE, the edit applied to the
# path that sentence named, and the refusal required to clear. The mirror legs apply the SAME row to the two
# decoys (`alpha/workspace_other.tsv.tmpl`, `alpha/workflow_configurations/workspace.tsv.tmpl`) and require the
# refusal to SURVIVE, because a refusal that points at a near-miss file is worse than one that says nothing —
# and both decoys still carrying their row when the cure finally lands is what shows the pass was not made by
# tidying. The fixtures themselves are never touched: they are also E1's pinned corpus, and a leg that edited
# them would move numbers that W1/V3/V4 own.
#
# Machine-independent by construction: every graded run reads the committed tree with --tree, so no
# checkout, no network, no credentials, no jar, no write outside a temp dir. One optional block DOES read
# a real gatk-sv checkout, at a PINNED ref (e1909d2f, overridable with GSVTK_ENTITY_REF), because an
# unpinned cross-check would fail on a machine whose checkout moved rather than report a finding. With no
# checkout, or without that commit, it prints a NAMED, COUNTED skip; and the tally line prints what this
# file declares against what the run reached, which is the reporting shape C10 of docs/gap-ledger.md says
# is still missing everywhere else. bash 3.2 compatible.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2
SELF="${BASH_SOURCE[0]:-$0}"

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi

FIX="$ROOT/scripts/selftest.d/fixtures/entity"
FIXREQ="$ROOT/scripts/selftest.d/fixtures/entity-required"
TOOL="$ROOT/checks/terra_entity_check.py"
PINNED="${GSVTK_ENTITY_REF:-e1909d2f}"
ok=0; fail=0; skipped=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"
mkdir -p "$TMP/nothing"

# Which checkout the optional cross-check may read, resolved BEFORE GSVTK_CONFIG is pinned to an empty file
# below — and deliberately the DEVELOPER'S real one: the cross-check exists to measure a tree this repo does
# not ship, so CK is the ambient answer, empty or not, and an empty CK is what prints the named skip.
#
# What CK is NOT is switchable by exporting `GSVTK_GATK_SV_CHECKOUT=` empty, which this comment used to claim.
# `kit/gsvtk-config:164` is `if os.environ.get(env_name)` and `kit/config.py:45-47` is `or fallback`, so an
# EMPTY value is falsy and falls through to the profile chain and the default — which on a configured box is
# that same real checkout. Measured on this file: `GSVTK_GATK_SV_CHECKOUT=` reaches every leg and withholds
# 0; `GSVTK_GATK_SV_CHECKOUT=/nonexistent` withholds the whole `entity-no-checkout` block (the SKIP line
# prints how many sites that is). So reproducing the CI skip takes a path that is NOT there, never `=` empty,
# and CI is fine only because there the variable is UNSET *and* no profile or default names a checkout that
# exists. The empty `GSVTK_GATK_SV_CHECKOUT=` in BASE below does a narrower job: it neutralises an INHERITED
# export, so a graded run cannot reach a real tree through somebody's shell. The resolver is not being fixed:
# empty-means-unset is deliberate and load-bearing there too, and `gsvtk-config:91` (`if explicit:`) is the
# same choice in the file that decides which profile files are read at all.
CK="$("$PY" -c 'import sys; sys.path.insert(0, "kit"); import config; print(config.get("GATK_SV_CHECKOUT"))' \
     2>/dev/null | tail -1)"

# Does THIS interpreter carry miniwdl? The required-input layer (the Q legs, and the required half of the
# real-tree block) is the only part of this phase that needs it, and `requirements-dev.txt` says the gate
# SKIPS miniwdl rather than failing without it — which is the same choice the tool makes when it discloses
# `capture=NO-MINIWDL` instead of refusing. So the phase runs the miniwdl legs when the interpreter has it
# and prints a NAMED, COUNTED skip when it does not, exactly like the checkout block below.
#
# Which way this goes is a property of the interpreter that reaches this phase, and NOT of what `make test`
# was typed with: `Makefile:13` defaults `PYTHON` to the bare name `python3`, and the `$PY` resolution above
# upgrades a bare name to `./.venv/bin/python` whenever that exists. Measured here: `make test` (no `PYTHON`)
# and `bash scripts/selftest.d/entity.sh python3` BOTH run the miniwdl arm on a box whose `.venv` carries it,
# because the argument is a name and not a path. So the skipped arm cannot be left to whatever the box
# happens to have — driving it deliberately takes an interpreter named by ABSOLUTE path with no `WDL` (here
# `/opt/homebrew/bin/python3`), never `PYTHON=python3`. Both directions are therefore exercised somewhere,
# and the NO-MINIWDL arm is additionally run AGAINST A BLINDED INTERPRETER (the `Qblind` legs below) on every
# run, miniwdl present or not: a skip nobody ever executes is a skip that can rot.
if "$PY" -c 'import WDL' >/dev/null 2>&1; then HAVE_MINIWDL=yes; else HAVE_MINIWDL=no; fi

# runsc NAME CMD... — run once; keep combined output and exit status. Scenarios run once and are asserted
# many times, so N assertions never cost N runs of the tool.
runsc() {
    local name="$1"; shift
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    printf '%s\n' "$out" > "$TMP/$name.out"
    printf '%s\n' "$rc" > "$TMP/$name.rc"
}

# wantf DESC NAME WANT_RC NEEDLE... — the recorded run exited WANT_RC and every needle appears.
wantf() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # the line that CALLED wantf; distinct per call site, once per call
    local desc="$1" name="$2" wantrc="$3"; shift 3
    local miss="" n rc
    rc="$(cat "$TMP/$name.rc" 2>/dev/null)"
    for n in "$@"; do
        grep -qF -- "$n" "$TMP/$name.out" 2>/dev/null || miss="$miss
          missing: $n"
    done
    if [ "$rc" = "$wantrc" ] && [ -z "$miss" ]; then
        ok=$((ok + 1)); printf '  ok    %s (exit %s)\n' "$desc" "$rc"
    else
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s, want %s)%s\n' "$desc" "$rc" "$wantrc" "$miss"
        head -8 "$TMP/$name.out" 2>/dev/null | sed 's/^/          /'
    fi
}

# wantline DESC NAME PATTERN — one line matches, so a label is pinned to the row that carries it.
wantline() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf above
    local desc="$1" name="$2" pat="$3"
    if grep -qE -- "$pat" "$TMP/$name.out" 2>/dev/null; then
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    else
        fail=$((fail + 1)); printf '  FAIL  %s\n          no line matched: %s\n' "$desc" "$pat"
    fi
}

# wantabsent DESC NAME PATTERN — a verdict the fixtures never produced must not be printed. Counting on
# absence is what catches a refusal quietly becoming a pass.
wantabsent() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf above
    local desc="$1" name="$2" pat="$3"
    if grep -qE -- "$pat" "$TMP/$name.out" 2>/dev/null; then
        fail=$((fail + 1)); printf '  FAIL  %s\n          unexpected line: %s\n' "$desc" \
            "$(grep -E -- "$pat" "$TMP/$name.out" | head -1)"
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# countis DESC NAME PATTERN EXPECTED — how many lines match. "one line per config" cannot be a substring.
countis() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf above
    local desc="$1" name="$2" pat="$3" want="$4" got
    got="$(grep -cE -- "$pat" "$TMP/$name.out" 2>/dev/null)"
    if [ "$got" = "$want" ]; then
        ok=$((ok + 1)); printf '  ok    %s (%s)\n' "$desc" "$got"
    else
        fail=$((fail + 1)); printf '  FAIL  %s — %s line(s), want %s\n' "$desc" "$got" "$want"
    fi
}

# wanteq DESC GOT WANT — a number computed outside the tool against one it printed.
#
# An EMPTY GOT IS A FAILURE, never a match: several legs below compare one run's PRINTED LINE against
# another's, so the day that line stops printing — renamed, moved behind a condition, dropped by an
# emitter change — the leg compares two empty strings and prints `ok ()`: an equality grading nothing,
# counted as a verdict. `want` stays unrestricted, because a recomputation may legitimately come out
# empty and an empty `want` against a non-empty `got` is a red either way. Same reason `countis` exists.
wanteq() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf above
    local desc="$1" got="$2" want="$3"
    if [ -z "$got" ]; then
        fail=$((fail + 1)); printf '  FAIL  %s — the graded side printed NOTHING (want was [%s]), so this equality compares two empties\n' "$desc" "$want"
    elif [ "$got" = "$want" ]; then
        ok=$((ok + 1)); printf '  ok    %s (%s)\n' "$desc" "$got"
    else
        fail=$((fail + 1)); printf '  FAIL  %s — got %s, want %s\n' "$desc" "$got" "$want"
    fi
}

# selfcanary — this file's own harness must be able to notice a failure.
selfcanary() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf above
    local rc
    false; rc=$?
    if [ "$rc" -ne 0 ]; then
        ok=$((ok + 1)); printf '  ok    this file notices a failing command (canary)\n'
    else
        fail=$((fail + 1)); printf '  FAIL  the harness in %s is vacuous\n' "$SELF"
    fi
}

echo
echo "selftest: entity -- the name-key derivation and the shipped-table cross-check (offline, synthetic)"
# How many assertion call SITES this file contains, derived FROM this file (never typed), so that a block going
# missing moves the number. Used by the skip message and by the declared/reached pair at the end. The callee
# names below are the ones gsvtk_declared_funset derives from this file — every function that records a verdict
# and is called from top-level code — and the reporter re-derives them on every run, so the list cannot rot and
# a recorder that forgot gsvtk_note_site is named instead of silently dropping this phase's reached count to 0.
# The three aliases this file used to keep (wantc / wantclin / wantcount) are gone: their own comment said they
# existed "only to make that grep expressible", and under site accounting they would have counted one assertion
# twice — the alias line and the call inside it — inflating the very withheld count C10 exists to state.
. "$ROOT/scripts/selftest.d/declared.sh"
GSVTK_DECL_FUNCS="wantf wantline wantabsent countis wanteq selfcanary"
gsvtk_declared_init "$TMP"          # the ledger rides in this phase's temp dir, under its existing EXIT trap
DECLARED="$(gsvtk_declared_count "$SELF")"
selfcanary

BASE=(env GSVTK_CONFIG="$TMP/empty.env" GSVTK_GATK_SV_CHECKOUT= GSVTK_GATK_SV_REF=)

# --- E1: the corpus runs, and the run says where its expander came from -----------------------------
# One run grades everything; the scenarios below re-run only where a different invocation is the point.
# "6 other TSV(s)" are the six tables whose first column is neither `entity:` nor `membership:`: the four
# workspace-shaped ones (beta's oracle, gamma's, epsilon's, zeta's empty one), alpha's near-miss
# `workspace_other.tsv.tmpl`, and alpha's misfiled `workflow_configurations/workspace.tsv.tmpl`.
runsc E1 "${BASE[@]}" "$PY" "$TOOL" --tree "$FIX"
wantf "E1: the synthetic corpus runs and exits 1 (findings exist, and a finding is exit 1 here)" E1 1 \
    "expander: JINJA_RE from terra/batch_configs.py" \
    "shipped tables: 7 entity/membership TSV(s), type(s) batch x1, panel x1, participant x1, sample x2, sample_set x1, sample_sets x1" \
    "6 other TSV(s) excluded" \
    "UNREADABLE table: beta/unreadable_header.tsv.tmpl"
countis "E1: one machine line per config, no config swallowed" E1 '^GSVTK-ENTITY config=' 42
countis "E1: exactly one summary line" E1 '^GSVTK-ENTITY-SUMMARY ' 1
wantf "E1: the census the fixtures were built to produce" E1 1 \
    "GSVTK-ENTITY-SUMMARY configs=42 ok=19 no-name-key=12 many-name-keys=1 no-shipped-table=2 columns-missing=4 cannot-parse=4 namekeys-one=25 namekeys-zero=16 namekeys-many=1"
# Anti-vacuity, counted outside the tool: every `.json.tmpl` in the fixture tree earns exactly one line.
files="$(find "$FIX" -name '*.json.tmpl' | wc -l | tr -d ' ')"
lines="$(grep -cE '^GSVTK-ENTITY config=' "$TMP/E1.out")"
wanteq "E1: configs found by find() == configs answered by the tool" "$lines" "$files"

# --- E2: the pass, and what a pass may NOT be read as ----------------------------------------------
wantf "E2: one name key derives the type, the covering table is named, the value that was Jinja is counted" \
    E1 1 'config=alpha/workflow_configurations/SingleNameKey.json.tmpl flavor=alpha wf=SingleNameKey filestem=SingleNameKey namematch=yes state=OK namekeys=1 namekey=sample_set collections=- reads_direct=2 reads_member=0 member_attrs=0 tables_of_type=1 table=alpha/entity_sample_set.tsv.tmpl table_kind=entity columns=3 missing=- nested=0 barethis=0 dupkeys=0 neutralised=1 unbraced=0 reason=-'
wantf "E2: a pass still says row existence was not checked" E1 1 \
    "NOTE alpha/workflow_configurations/SingleNameKey.json.tmpl" \
    "row existence is NOT checked here"
wantabsent "E2: a \${workspace.*} binding is never counted as an entity read" E1 \
    'SingleNameKey.json.tmpl .*reads_direct=3'

# --- E3: zero name keys, both shapes ---------------------------------------------------------------
wantline "E3a: a config that reads attributes but binds no name key refuses" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/ZeroNameKey.json.tmpl .* state=UNRESOLVED namekeys=0 .* reason=no-name-key$'
wantf "E3a: and the reason names the read it DID find" E1 1 \
    "UNRESOLVED alpha/workflow_configurations/ZeroNameKey.json.tmpl" \
    "the 1 single-component read(s) it does make are \`this.merged_bincov\`"
wantf "E3b: the write-back shape (nothing through this. at all) is named as such, with its workspace count" E1 1 \
    "no \${this.<etype>_id} read: this config reads nothing through \`this.\` at all (2 binding(s), 2 of them through \${workspace.*})"
wantline "E3c: \${this.id} is name-key-shaped but carries no type, so it is zero, not one" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/IdNoType.json.tmpl .* namekeys=0 .* reason=no-name-key$'
wantf "E3c: and the reason says why it is not a name key" E1 1 \
    "it also reads \`this.id\`, which carries no <etype> before \`_id\`"

# --- E4: many name keys ----------------------------------------------------------------------------
wantline "E4: two distinct types refuse, and both are printed" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/TwoNameKeys.json.tmpl .* state=UNRESOLVED namekeys=2 namekey=sample_set,donor .* reason=many-name-keys$'
wantf "E4: with a reason that says it refuses rather than picking" E1 1 \
    "2 different entity types appear as \${this.<etype>_id}: \`sample_set\`, \`donor\`"

# --- E5: the trap. This is the assertion the lane exists for --------------------------------------
# The member rule reads three \${this.sample_sets.<attr>} and derives `sample_sets` — a type the corpus
# DOES ship, with all three attributes as columns. The name-key rule derives `sample_set_set`, finds no
# such table, and refuses. If this ever reads OK, the deriver has silently become the member rule.
wantline "E5: MemberTrap derives its type from the name key, not the member collection" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/MemberTrap.json.tmpl .* state=UNRESOLVED namekeys=1 namekey=sample_set_set collections=sample_sets .* reason=no-shipped-table$'
wantabsent "E5: and it does not borrow the member type's table as its answer" E1 \
    'MemberTrap.json.tmpl .* table=alpha/'
wantf "E5: the refusal names the type it looked for and shows the table it refused to use" E1 1 \
    "no shipped entity table of type \`sample_set_set\`: looked in every TSV under inputs/templates/terra_workspaces for a first header column \`entity:sample_set_set_id\` or \`membership:sample_set_set_id\`" \
    "alpha/entity_sample_sets.tsv.tmpl (entity:sample_sets_id, columns: ploidy_table, outlier_filtered_pesr_vcf)"

# --- E6/E7: member collections are checked against the derived table -------------------------------
wantline "E6a: a collection that IS a column of the derived table passes, with member attrs unchecked" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/MemberCollection.json.tmpl .* state=OK namekeys=1 namekey=sample_set collections=sample_sets reads_direct=2 reads_member=1 member_attrs=2 .* missing=- .* reason=-$'
wantf "E6a: and the unchecked member attributes are listed, not dropped" E1 1 \
    "2 member attribute read(s) NOT checked (sample_sets.ploidy_table, sample_sets.sample_set_id)"
wantline "E6b: a collection that is NOT a column of that table is a finding naming it" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/CollectionNotColumn.json.tmpl .* state=UNRESOLVED .* collections=samples .* missing=samples .* reason=columns-missing$'

# --- E8/E9: the table check itself ----------------------------------------------------------------
wantline "E8a: derived type shipped, one read not a column -> named column, not a shrug" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/LacksReadColumn.json.tmpl .* state=UNRESOLVED namekeys=1 namekey=sample_set .* missing=ploidy_table .* reason=columns-missing$'
wantf "E8a: and the candidate table is shown with the columns it does carry" E1 1 \
    "no shipped table of type \`sample_set\` carries 1 column(s) this config reads: \`ploidy_table\`" \
    "alpha/entity_sample_set.tsv.tmpl (columns: merged_bincov, median_cov, sample_sets)"
wantline "E9: a derived type with no shipped table at all refuses" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/NoTableAtAll.json.tmpl .* state=UNRESOLVED namekeys=1 namekey=donor .* tables_of_type=0 table=- table_kind=- .* reason=no-shipped-table$'

# --- E10/E11: two tables of one type, and the no-union rule --------------------------------------
wantline "E10a: two tables that cover the reads BETWEEN them is not a pass" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/TwoTablesSplit.json.tmpl .* state=UNRESOLVED namekeys=1 namekey=sample .* tables_of_type=2 .* missing=case_stripy_file .* reason=columns-missing$'
wantf "E10a: and both tables are printed, so the reader can see the union being refused" E1 1 \
    "The 2 table(s) of that type, and what each carries: alpha/entity_sample_a.tsv.tmpl (columns: bam_or_cram_file); alpha/entity_sample_b.tsv.tmpl (columns: case_stripy_file)" \
    "two tables covering the set between them is reported here, not passed on their union"
wantline "E11: one table that covers all of them IS enough, and it is the one named" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/SplitOneTableOK.json.tmpl .* state=OK namekeys=1 namekey=sample .* tables_of_type=2 table=alpha/entity_sample_a.tsv.tmpl .* reason=-$'
wantline "E11b: a table whose only column is the name column still answers a name-key-only config" E1 \
    '^GSVTK-ENTITY config=beta/GATKSVPipelineSynth.json.tmpl .* state=OK namekeys=1 namekey=participant .* table=beta/entity_participant.tsv.tmpl table_kind=entity columns=0 .* reason=-$'
wantline "E11c: and that same table carries no attribute, so an attribute read is a finding" E1 \
    '^GSVTK-ENTITY config=beta/ParticipantReadsColumn.json.tmpl .* state=UNRESOLVED namekeys=1 namekey=participant .* columns=0 missing=bam_or_cram_file .* reason=columns-missing$'

# --- E12: the binding grammar, three shapes a starts-with reader loses ----------------------------
wantline "E12a: \${[this.a, this.b]} carries both reads" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/ListForm.json.tmpl .* state=OK namekeys=1 namekey=sample_set .* reads_direct=3 reads_member=0 .* reason=-$'
wantline "E12b: a read inside a nested object is found and labelled nested" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/NestedRead.json.tmpl .* state=OK namekeys=1 namekey=sample_set .* reads_direct=2 .* nested=1 barethis=0 '
wantline "E12c: \`this.x\` outside \${ } is a literal: counted, and NOT read as an entity attribute" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/BareThis.json.tmpl .* state=OK namekeys=1 namekey=sample_set .* reads_direct=1 .* nested=0 barethis=1 '
wantf "E12c: with the literal/classification sentence printed, not just the counter" E1 1 \
    "carry \`this.\` outside \`\${ ... }\`, which Terra reads as a LITERAL"
wantf "E13: a key bound twice is named, and last-wins is stated rather than decided silently" E1 1 \
    "1 key(s) bound more than once (\`DuplicateKey.value\`)" \
    "\`json.loads\` keeps the LAST value"

# --- E14: the workflow comes from the binding keys, not the file name ------------------------------
wantline "E14: prefix != file stem is printed as a mismatch (upstream's write-back files are this shape)" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/output_configurations/SyntheticOutputs.json.tmpl .* wf=Synthetic filestem=SyntheticOutputs namematch=no .* reason=no-name-key$'

# --- E15: a document that will not parse is a finding about the document ---------------------------
wantline "E15: truncated JSON is cannot-parse, not zero name keys and not a pass" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/CannotParse.json.tmpl .* state=UNRESOLVED .* reason=cannot-parse$'
wantf "E15: and it says what it tried (the one substitution) before giving up" E1 1 \
    "its bindings cannot be read at all: after the one \`{{ ... }}\` -> null substitution"

# --- E16: the positive control: exit 0 exists, and it is reachable --------------------------------
# A tool that always exits 1 is as useless as one that always exits 0. Build a corpus that answers
# cleanly in a temp dir and require exit 0 with no refusal block.
mkdir -p "$TMP/clean/inputs/templates/terra_workspaces/one"
printf 'entity:cohort_id\tsites_vcf\n' > "$TMP/clean/inputs/templates/terra_workspaces/one/cohort.tsv.tmpl"
printf '{\n  "CleanJob.cohort": "${this.cohort_id}",\n  "CleanJob.sites": "${this.sites_vcf}"\n}\n' \
    > "$TMP/clean/inputs/templates/terra_workspaces/one/CleanJob.json.tmpl"
runsc E16 "${BASE[@]}" "$PY" "$TOOL" --tree "$TMP/clean"
wantf "E16: a corpus that answers everything exits 0 and prints no refusal" E16 0 \
    "(none — every config in the corpus answered)" \
    "GSVTK-ENTITY-SUMMARY configs=1 ok=1 no-name-key=0 many-name-keys=0 no-shipped-table=0 columns-missing=0 cannot-parse=0"

# --- E17: the shapes that must not be answered at all (exit 3 / exit 2) ---------------------------
runsc E17a "${BASE[@]}" "$PY" "$TOOL" --tree "$TMP/nothing"
wantf "E17a: an empty tree is a stated prerequisite, never a clean exit" E17a 3 \
    "PREREQUISITE no inputs/templates/terra_workspaces under"
runsc E17b "${BASE[@]}" "$PY" "$TOOL" --tree "$FIX" --ref HEAD
wantf "E17b: --tree with --ref is a usage error (two answers to 'which tree')" E17b 2 \
    "usage error: --tree already fixes the tree"
runsc E17c "${BASE[@]}" "$PY" "$TOOL" --tree "$FIX" --corpus inputs/templates/nowhere
wantf "E17c: a corpus that is not there exits 3 naming it" E17c 3 \
    "PREREQUISITE no inputs/templates/nowhere"
runsc E17d env GSVTK_CONFIG="$TMP/empty.env" GSVTK_GATK_SV_CHECKOUT= GSVTK_GATK_SV_REF= \
    "$PY" "$TOOL"
wantf "E17d: no checkout and no --tree is a named prerequisite (exit 3), not a vacuous 0" E17d 3 \
    "PREREQUISITE no gatk-sv checkout to read"
mkdir -p "$TMP/noconf/inputs/templates/terra_workspaces/orphan"
printf 'workspace:only_attr\t{{ dockers.only_attr }}\n' \
    > "$TMP/noconf/inputs/templates/terra_workspaces/orphan/workspace.tsv.tmpl"
runsc E17e "${BASE[@]}" "$PY" "$TOOL" --tree "$TMP/noconf"
wantf "E17e: a corpus with a table and no config exits 3 and prints ALL THREE zero censuses" E17e 3 \
    "PREREQUISITE 0 configs" \
    "GSVTK-ENTITY-SUMMARY configs=0 ok=0 no-name-key=0 many-name-keys=0 no-shipped-table=0 " \
    "columns-missing=0 cannot-parse=0 namekeys-one=0 namekeys-zero=0 namekeys-many=0" \
    "GSVTK-ORACLE-SUMMARY flavors=0 ok=0 absent=0 malformed=0 dupes=0 names=0 values=not-read enforced=yes" \
    "GSVTK-VALUE-SUMMARY configs=0 bindings=0 workspace-attr=0 workspace-missing=0 entity=0 literal=0 " \
    "needs-bundle=0 test-bundle=0 docker=0 mixed=0 no-oracle=0 " \
    "nested-reads=0 workspace-reads=0 workspace-resolved-reads=0 workspace-missing-reads=0 " \
    "no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 " \
    "unresolved=0 configs-unresolved=0 configs-no-oracle=0 configs-needs-bundle=0 " \
    "configs-no-values=0 configs-unclassified=0"
# The E17e value needle above is the WHOLE line, field by field, and the missing backslash that used to
# truncate it is why it is written in six fragments: line 367 ended after `literal=0 "`, so line 368 ran as a
# COMMAND (the phase printed `needs-bundle=0: command not found` on every run), nothing failed (no `set -e`,
# and the tally exits on `$fail`), and the leg pinned 6 of 9 class fields and none of the read-level or
# `configs-*` fields. `bash -n` cannot see that shape — it is valid shell that is not the command its author
# meant — so the proof is the run itself: `bash scripts/selftest.d/entity.sh .venv/bin/python 2>&1 | grep -c
# 'command not found'` must print 0, and pinning the tail of the line is what makes the fragments load-bearing
# again (a fragment that stops being an argument is a field that stops being asserted).
runsc E18 env GSVTK_CONFIG="$TMP/empty.env" "$PY" "$TOOL" --help
wantf "E18: --help works with an empty profile and states the four exit codes" E18 0 \
    "Exit codes" "3  prerequisite missing" "GSVTK-ENTITY-SUMMARY"

# The SCOPE CENSUS: what this tool did not evaluate, printed on every run including a clean one. Asserted
# against the committed fixture corpus, so it costs nothing and needs no checkout. The counts are pinned because
# the corpus is committed — change a fixture and this moves with it, which is the whole point of pinning it here
# rather than describing it in prose. The row A22 records claimed coverage the tool did not have; these
# assertions are what stop the next reader having to re-derive that by reading 656 lines. `workspace-binding-reads`
# is 0 HERE because the value layer classifies every read of every document it can read — a measured 0, not a
# cleared gap. What genuinely stays unexamined is printed beside it: the workspace tables' ATTRIBUTE VALUES
# (12 of them here, the second column, never opened), the documents that will not parse (U1 adds the second
# one, an undecodable byte, to `CannotParse`'s truncated JSON — their reads can be reported as unreadable but
# never counted), and Terra's own rootEntityType.
wantf "the unchecked census prints with counts on a run that also has findings" E1 1 \
    "GSVTK-ENTITY-UNCHECKED configs=42 member-attr-reads=7 configs-with-member-attrs=3 " \
    "workspace-binding-reads=0 configs-with-workspace-reads=0 workspace-attribute-values-unread=12 " \
    "configs-with-unclassified-values=4 "
# The two required-input fields of the SAME line, pinned in the direction that holds whichever interpreter
# runs this: this corpus ships exactly one WDL, so most configs' required inputs went unasked whether or not
# miniwdl is present (with it: NO-WDL for 35 + NO-DOCUMENT for 4; without it: NO-MINIWDL for the rest). The
# exact pair is pinned inside the `entity-no-miniwdl` region below; what is pinned HERE, in every mode, is
# that the field is a COUNT of the corpus rather than a 0 the tool wished for, and that the cause is more
# than one word — which is what `mixed` exists to refuse to hide.
wantline "the census counts the configs whose required inputs were never asked, and says why in more than one word" \
    E1 '^GSVTK-ENTITY-UNCHECKED .* required-configs-uncaptured=([3-9][0-9]) required-uncaptured-because=mixed '
wantline "it names the required inputs it never enumerated as a scope item, not a verdict" E1 \
    "required-configs-uncaptured counts the configs whose workflow was never opened"
countis "the census is one machine-readable line, not a paragraph" E1 "^GSVTK-ENTITY-UNCHECKED " 1
countis "and exactly one line says what those counts mean" E1 "SCOPE, not verdicts" 1
wantline "it names member attributes as counted-but-not-checked" E1 "member attributes are counted and listed but"
wantline "it names the workspace attribute VALUES as the gap that did NOT close" E1 \
    "the workspace ATTRIBUTE VALUES"
wantline "it says the workspace READS are now classified, so the 0 above is earned, not claimed" E1 \
    "reads are now classified and enforced"
wantline "it says Terra's own rootEntityType is never asked" E1 "rootEntityType is never asked"
wantf "a CLEAN run (exit 0) still prints the census, so a gap is never signalled only by silence" E16 0 \
    "GSVTK-ENTITY-UNCHECKED"
wantabsent "and the census carries no verdict word, so it cannot be read as a pass or a fail of the unchecked thing" \
    E16 "^GSVTK-ENTITY-UNCHECKED.*(PASS|FAIL|ok=|UNRESOLVED)"

# ===============================================================================================
# THE UNDECODABLE-BYTE LEGS: one byte that is not UTF-8 must cost one config's answer, never the report.
#
# The defect was `except OSError` alone around a text-mode `open().read()`: `UnicodeDecodeError` is a
# ValueError, so it escaped as a traceback. A traceback prints ZERO `GSVTK-*` lines and exits 1 — the same
# exit code that means "findings" — so a caller reading the exit code saw a finding and a caller reading
# stdout saw nothing at all, for a corpus of 32 configs of which exactly one was unreadable. The fix is one
# more exception in the tuple, which routes the fact into a refusal the file already had. Three readers took
# it (`read_config`, `read_tables`, `parse_with_bundle_marks`); the workspace-table reader already had it, and
# its comment is where the wording of the other three came from.
#
# POSITIVE CONTROL (CONTRIBUTING.md: a fix ships with a named control that fails without it): two committed
# fixtures, each carrying one raw 0x92 byte, one per layer that has to refuse it —
#   * `alpha/workflow_configurations/Undecodable.json.tmpl` is a well-formed document apart from the byte, so
#     the only thing wrong with it is undecodability. It must earn `cannot-parse` on the ENTITY line AND
#     `values-unreadable` on the VALUE line: both layers open the same file, so both must refuse it — a
#     refusal in one layer and a pass in the other is the shape this lane keeps closing.
#   * `zeta/undecodable.tsv.tmpl` is a header line plus the byte, and must become an `UNREADABLE table` line —
#     the same word `beta/unreadable_header.tsv.tmpl` earns for a header nobody can read.
# The last leg is the one that would have caught the original bug from the outside: the census must still
# print, because "one config is unreadable" is a finding and "the tool crashed" is not.
# --- U1: an undecodable CONFIG is a refusal in both layers, and its counters move -------------------
wantline "U1: the undecodable config is cannot-parse on the entity line, not a traceback" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/Undecodable\.json\.tmpl .* state=UNRESOLVED .* reason=cannot-parse$'
wantline "U1: and the SAME document is values-unreadable on the value line (two readers, one answer)" E1 \
    '^GSVTK-VALUE config=alpha/workflow_configurations/Undecodable\.json\.tmpl .* bindings=0 .* reasons=values-unreadable$'
wantf "U1: THE CONTROL — the refusal is counted, so cannot-parse and configs-unclassified both moved" E1 1 \
    "columns-missing=4 cannot-parse=4" "configs-no-values=1 configs-unclassified=4"
wantf "U1: and the reason quotes the decode error instead of inventing a cause" E1 1 \
    "UNRESOLVED alpha/workflow_configurations/Undecodable.json.tmpl" \
    "'utf-8' codec can't decode byte 0x92"
# --- U2: an undecodable TABLE header is a named UNREADABLE table ------------------------------------
wantf "U2: the undecodable TSV is UNREADABLE, the same word an unreadable header already earns" E1 1 \
    "UNREADABLE table: zeta/undecodable.tsv.tmpl" "invalid start byte"
wantline "U2: and it is not counted as a shipped table or as an excluded one, because it was never read" E1 \
    '^shipped tables: 7 entity/membership TSV\(s\).*; 6 other TSV\(s\) excluded'
# --- U3: the report survives ---------------------------------------------------------------------------------
wantabsent "U3: nothing on this run is a traceback — no 'Traceback', no 'UnicodeDecodeError' in any line" E1 \
    'Traceback|UnicodeDecodeError'
wantf "U3: and all three censuses still print, so one bad byte costs one config and not the report" E1 1 \
    "GSVTK-ENTITY-SUMMARY " "GSVTK-ORACLE-SUMMARY " "GSVTK-VALUE-SUMMARY "

# ===============================================================================================
# The WORKSPACE ORACLE: `checks/terra_entity_check.py`'s reader for each flavor's own
# `workspace.tsv.tmpl`, asserted against the committed fixtures, which carry every shape the reader can
# reach. These are the assertions that hold the reader honest BEFORE anything enforces them: the layer is
# additive (no exit code moves, no binding is refused) when it shipped, so the machine lines and the two
# honest words (`values=not-read`, `enforced=no`) were the whole deliverable, and an exact number was the
# only proof the reader ran. The value layer that came after it did move exit codes and did turn `ABSENT`,
# `MALFORMED` and `DUPE` into refusals, which is why the census word below now reads `enforced=yes`; the
# buckets themselves are unchanged (ruling R11), and they are still what these legs pin. `ok`/`absent`/`malformed`/`dupes` are FLAVOR-status counters that add up to `flavors`, so a
# flavor that is both malformed and duplicated is counted once — and both findings still print, which is
# what W3 asserts.
#
# POSITIVE CONTROL (CONTRIBUTING.md: a probe first proves the guarded path was reachable). Two halves, and
# W8 is the one that matters: W1's `flavors=6 ok=2 names=8` proves the reader ran and counted on a corpus
# where the oracle DOES answer, and W8 rebuilds one flavor in a temp dir, adds a declared row, and requires
# `attrs` to move 1 -> 2. An assertion on an exact attribute count fails if the reader stops reading; an
# assertion that a line was merely printed would pass forever. W4's beta needle and W3's `sv_pipeline_docker`
# duplicate are the same trick on the `workspace:` strip: unstrip it and beta prints
# `workspace:cloud_sdk_docker` (missing the needle) and gamma loses its duplicate and reads attrs=6.
# --- W1: the census, and one machine line per flavor ------------------------------------------------
countis "W1: one oracle line per flavor, including the flavors with no table" E1 '^GSVTK-ORACLE flavor=' 7
countis "W1: exactly one oracle census line" E1 '^GSVTK-ORACLE-SUMMARY ' 1
wantf "W1: the seven flavors the fixtures were built to produce, and the 8 names they declare between them" E1 1 \
    "GSVTK-ORACLE-SUMMARY flavors=7 ok=2 absent=3 malformed=1 dupes=1 names=8 values=not-read enforced=yes"
# --- W2: ABSENT is its own answer, and it names what the flavor DOES ship --------------------------
wantf "W2: alpha has configs and no oracle, so ABSENT — and the four entity tables it ships are named" E1 1 \
    "GSVTK-ORACLE flavor=alpha file=- attrs=0 status=ABSENT" \
    "TSV(s) that flavor does ship: alpha/entity_sample_a.tsv.tmpl, alpha/entity_sample_b.tsv.tmpl, " \
    "alpha/entity_sample_set.tsv.tmpl, alpha/entity_sample_sets.tsv.tmpl"
wantf "W3a: a near-miss name is named and never read: alpha still reads attrs=0 with workspace_other present" E1 1 \
    "GSVTK-ORACLE flavor=alpha file=- attrs=0 status=ABSENT" \
    "alpha/workspace_other.tsv.tmpl"
wantf "W3b: and so is the oracle's exact name in the wrong directory, with the rule said back" E1 1 \
    "1 of them carries the oracle's own name but is not the flavor's table: " \
    "alpha/workflow_configurations/workspace.tsv.tmpl — the table has to sit beside the flavor's configs " \
    "(alpha/workspace.tsv.tmpl)"
# --- W4: the oracle answers, and the `workspace:` prefix is gone from the name --------------------
wantf "W4: beta's one-row table is the oracle, and the declared name is cloud_sdk_docker, not the prefixed row" E1 1 \
    "GSVTK-ORACLE flavor=beta file=beta/workspace.tsv.tmpl attrs=1 status=OK" \
    "declares 1 attribute(s) from 1 row(s): cloud_sdk_docker"
wantabsent "W4: the strip is real — the prefixed spelling appears in no oracle line" E1 \
    'GSVTK-ORACLE.*workspace:cloud_sdk_docker|^.*declares 1 attribute\(s\) from 1 row\(s\): workspace:'
# --- W5: gamma reports EVERY structural oddity it finds, instead of picking a side ----------------
wantf "W5: gamma is MALFORMED, declares the 5 names that are readable, and prints all four findings" E1 1 \
    "GSVTK-ORACLE flavor=gamma file=gamma/workspace.tsv.tmpl attrs=5 status=MALFORMED" \
    "MALFORMED row 6: \`{{ flavour }}_docker\` is not an attribute name ([A-Za-z0-9_.]+)" \
    "MALFORMED row 7: \`bad name\` is not an attribute name ([A-Za-z0-9_.]+)" \
    "MALFORMED row 8: 3 tab-separated field(s), not the documented 2 — name \`extra_field_row\` is still declared" \
    "DUPE \`sv_pipeline_docker\` is declared 2 times (rows 2, 3)"
wantline "W5: the DUPE above is the prefix-strip control — row 2 is prefixed, row 3 is not" E1 \
    'declares 5 attribute\(s\) from 8 row\(s\): sv_base_mini_docker, sv_pipeline_docker,'
# --- W6/W7: the two states gamma cannot show, because the earlier word wins the label -------------
wantline "W6: epsilon carries a duplicate and nothing else, so status=DUPE is reachable, not decorative" E1 \
    '^GSVTK-ORACLE flavor=epsilon file=epsilon/workspace\.tsv\.tmpl attrs=2 status=DUPE$'
wantf "W7: an empty table is attrs=0 OK, and says it is not the same answer as a missing one" E1 1 \
    "GSVTK-ORACLE flavor=zeta file=zeta/workspace.tsv.tmpl attrs=0 status=OK" \
    "declares 0 attribute(s) from 0 row(s)" \
    "the table exists and declares nothing — a different answer from ABSENT"
wantf "W7: and delta, which ships no TSV at all, is ABSENT and says so (empty != absent)" E1 1 \
    "GSVTK-ORACLE flavor=delta file=- attrs=0 status=ABSENT" \
    "that flavor ships no TSV of any kind"
# --- W8: the positive control: change the declared names and the assertion moves ------------------
mkdir -p "$TMP/probe/inputs/templates/terra_workspaces/probeflavor"
printf '{\n  "OracleProbe.docker": "${workspace.cloud_sdk_docker}"\n}\n' \
    > "$TMP/probe/inputs/templates/terra_workspaces/probeflavor/OracleProbe.json.tmpl"
printf 'workspace:cloud_sdk_docker\t{{ dockers.cloud_sdk_docker }}\n' \
    > "$TMP/probe/inputs/templates/terra_workspaces/probeflavor/workspace.tsv.tmpl"
runsc W8a "${BASE[@]}" "$PY" "$TOOL" --tree "$TMP/probe"
wantf "W8a: a one-row table answers attrs=1 (the same reader, a tree this file built)" W8a 1 \
    "GSVTK-ORACLE flavor=probeflavor file=probeflavor/workspace.tsv.tmpl attrs=1 status=OK" \
    "GSVTK-ORACLE-SUMMARY flavors=1 ok=1 absent=0 malformed=0 dupes=0 names=1 values=not-read enforced=yes"
printf 'gatk_docker\t{{ dockers.gatk_docker }}\n' \
    >> "$TMP/probe/inputs/templates/terra_workspaces/probeflavor/workspace.tsv.tmpl"
runsc W8b "${BASE[@]}" "$PY" "$TOOL" --tree "$TMP/probe"
wantf "W8b: THE CONTROL — one more declared row moves attrs 1->2, so the count is read, not remembered" W8b 1 \
    "GSVTK-ORACLE flavor=probeflavor file=probeflavor/workspace.tsv.tmpl attrs=2 status=OK" \
    "GSVTK-ORACLE-SUMMARY flavors=1 ok=1 absent=0 malformed=0 dupes=0 names=2 values=not-read enforced=yes" \
    "declares 2 attribute(s) from 2 row(s): cloud_sdk_docker, gatk_docker"
# --- W9: the two honest words, on a clean run too -------------------------------------------------
wantf "W9: a CLEAN run (exit 0) still prints the oracle census, with its flavor ABSENT and enforcing live" E16 0 \
    "GSVTK-ORACLE flavor=one file=- attrs=0 status=ABSENT" \
    "GSVTK-ORACLE-SUMMARY flavors=1 ok=0 absent=1 malformed=0 dupes=0 names=0 values=not-read enforced=yes"
countis "W9: exactly one line says the values were not read" E1 "^  values=not-read: no row's second field was parsed" 1
countis "W9: exactly one line says the \${workspace.*} refusal is live" E1 "^  enforced=yes: .*is UNRESOLVED and moves the exit code" 1
wantabsent "and no oracle line carries a verdict word, so a census cannot be read as a refusal" E1 \
    "^GSVTK-ORACLE.*(PASS|FAIL|UNRESOLVED|REASON)"

# ===============================================================================================
# The VALUE LAYER: `GSVTK-VALUE` per config, `GSVTK-VALUE-SUMMARY` as its census, and the pair of
# corpora that proves the word `enforced=yes` means what it says.
#
# V2 IS THE CONTROL FOR ENFORCEMENT. It is a PAIR with V1, because one leg cannot show that an exit code
# moved: nothing else in this repo moves exit 0 -> exit 1 because of a VALUE. E16 answers cleanly and binds
# no `${workspace.*}` at all; W8a/W8b exit 1 through the ENTITY layer (`no-name-key`); the real ref already
# exits 1 from 28 entity findings. So every exit-1 run in the suite was already 1 before the value layer
# existed, and `enforced=yes` was a word with no witness. V1 and V2 read the SAME corpus built here (one
# flavor, one entity table, one config binding one name key and one `${workspace.reference_fasta}`) and the
# ONLY difference between the two runs is that V2 deletes the single row of the flavor's
# `workspace.tsv.tmpl`. The `GSVTK-ENTITY` line is asserted byte-identical in both, the oracle's `status`
# stays `OK` in both (an empty-but-present table is a real answer, and here it is the answer that refuses),
# and the exit code goes 0 -> 1. That is the flip, and nothing but a value caused it.
#
# The pair is anti-vacuous in both directions, and the row is written with the `workspace:` prefix while the
# binding is not so that V1 leans on the oracle's strip too: a reader that stopped stripping reads that row's
# name as `workspace:reference_fasta`, which is not an attribute name, the flavor goes MALFORMED and V1 exits
# 1 under `no-oracle-malformed` instead of 0 (measured, by breaking the strip and re-running this pair). And
# V1 requires exit 0 while V2 requires exit 1 over the same corpus, so a value layer that refused nothing
# could not satisfy V2 and one that refused everything could not satisfy V1. V1 is also the first leg anywhere
# that requires the LAUNCHABLE class to be populated (`workspace-attr=1 workspace-resolved-reads=1 state=OK`)
# and the first that requires a non-zero numeric field on `GSVTK-ENTITY-UNCHECKED`
# (`workspace-attribute-values-unread=1`) on a run that exits 0.
# --- V1: the launchable class, and the exit 0 it earns -------------------------------------------
ENF="$TMP/enforce/inputs/templates/terra_workspaces/enforce"
mkdir -p "$ENF"
printf 'entity:cohort_id\tsites_vcf\n' > "$ENF/cohort.tsv.tmpl"
printf '{\n  "EnforceJob.cohort": "${this.cohort_id}",\n  "EnforceJob.ref_fasta": "${workspace.reference_fasta}"\n}\n' \
    > "$ENF/EnforceJob.json.tmpl"
printf 'workspace:reference_fasta\t{{ reference_resources.reference_fasta }}\n' > "$ENF/workspace.tsv.tmpl"
runsc V1 "${BASE[@]}" "$PY" "$TOOL" --tree "$TMP/enforce"
wantf "V1: THE CONTROL, first half — a config that binds a name the flavor's table declares exits 0" V1 0 \
    "GSVTK-VALUE config=enforce/EnforceJob.json.tmpl flavor=enforce wf=EnforceJob bindings=2 workspace-attr=1 workspace-missing=0 entity=1 literal=0 needs-bundle=0 test-bundle=0 docker=0 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=1 workspace-resolved-reads=1 workspace-missing-reads=0 no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=0 state=OK reasons=-" \
    "GSVTK-VALUE-SUMMARY configs=1 bindings=2 workspace-attr=1 workspace-missing=0 entity=1 literal=0 " \
    "workspace-reads=1 workspace-resolved-reads=1 workspace-missing-reads=0 no-oracle-reads=0 " \
    "unresolved=0 configs-unresolved=0"
wantf "V1: and a clean run still says one attribute VALUE of one table went unopened" V1 0 \
    "GSVTK-ENTITY-UNCHECKED configs=1 member-attr-reads=0 configs-with-member-attrs=0 " \
    "workspace-binding-reads=0 configs-with-workspace-reads=0 workspace-attribute-values-unread=1 " \
    "configs-with-unclassified-values=0 " \
    "GSVTK-ORACLE flavor=enforce file=enforce/workspace.tsv.tmpl attrs=1 status=OK"
# The same corpus, in the required layer's vocabulary: a clean exit 0 whose ONE config had its required
# inputs asked about by nobody — this scratch tree ships no `wdl/`, and with no miniwdl the cause is the
# interpreter instead. Either word is the honest one; what must NOT be possible is a clean run that reads as
# "every required input is bound".
wantline "V1: the clean run says the required inputs of its one config were never read (no wdl/, or no miniwdl)" \
    V1 '^GSVTK-ENTITY-UNCHECKED .* required-configs-uncaptured=1 required-uncaptured-because=(NO-WDL-DIR|NO-MINIWDL) '
wantf "V1: the entity verdict V2 must reproduce unchanged — same line, same corpus, one row still there" V1 0 \
    "GSVTK-ENTITY config=enforce/EnforceJob.json.tmpl flavor=enforce wf=EnforceJob filestem=EnforceJob namematch=yes state=OK namekeys=1 namekey=cohort collections=- reads_direct=1 reads_member=0 member_attrs=0 tables_of_type=1 table=enforce/cohort.tsv.tmpl table_kind=entity columns=1 missing=- nested=0 barethis=0 dupkeys=0 neutralised=0 unbraced=0 reason=-" \
    "(none — every config in the corpus answered)"
wantabsent "V1: and nothing on the clean twin is a refused value, so V2's refusal is the row, not the setup" V1 \
    '^GSVTK-VALUE .* state=UNRESOLVED'
# --- V2: THE control — the same corpus, one row deleted, exit 1 for a VALUE and nothing else ----
: > "$ENF/workspace.tsv.tmpl"
runsc V2 "${BASE[@]}" "$PY" "$TOOL" --tree "$TMP/enforce"
wantf "V2: THE CONTROL FOR ENFORCEMENT — delete that one row and the SAME corpus exits 1 on a value alone" V2 1 \
    "GSVTK-VALUE config=enforce/EnforceJob.json.tmpl flavor=enforce wf=EnforceJob bindings=2 workspace-attr=0 workspace-missing=1 entity=1 literal=0 needs-bundle=0 test-bundle=0 docker=0 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=1 workspace-resolved-reads=0 workspace-missing-reads=1 no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=1 state=UNRESOLVED reasons=workspace-missing" \
    "GSVTK-VALUE-SUMMARY configs=1 bindings=2 workspace-attr=0 workspace-missing=1 entity=1 literal=0 " \
    "workspace-reads=1 workspace-resolved-reads=0 workspace-missing-reads=1 no-oracle-reads=0 " \
    "unresolved=1 configs-unresolved=1"
wantf "V2: the refusal names the file, the name, and what that file does declare (status=OK, 0 names)" V2 1 \
    "UNRESOLVED-VALUE EnforceJob.ref_fasta [workspace-missing] EnforceJob.ref_fasta reads \`reference_fasta\` and \`enforce/workspace.tsv.tmpl\` does not declare it" \
    "declares 0 attribute(s) from 0 row(s)"
wantf "V2: and the ENTITY line is the byte-identical line V1 printed, so the 1 belongs to the value layer" V2 1 \
    "GSVTK-ENTITY config=enforce/EnforceJob.json.tmpl flavor=enforce wf=EnforceJob filestem=EnforceJob namematch=yes state=OK namekeys=1 namekey=cohort collections=- reads_direct=1 reads_member=0 member_attrs=0 tables_of_type=1 table=enforce/cohort.tsv.tmpl table_kind=entity columns=1 missing=- nested=0 barethis=0 dupkeys=0 neutralised=0 unbraced=0 reason=-" \
    "(none — every config in the corpus answered)"
wantabsent "V2: no entity-layer finding of any kind, so the exit code has exactly one cause" V2 \
    '^GSVTK-ENTITY .* state=UNRESOLVED|^UNRESOLVED enforce/'

# --- V3: a GSVTK-VALUE line is asserted to EXIST, per config, with its census pinned --------------
# Until these legs existed, `grep -c GSVTK-VALUE` over this file returned 2 — one comment and one truncated
# needle — so a config could stop printing its value line, or the census could stop adding up, and every leg
# in the file stayed green. The count is pinned twice over: against the fixed corpus size, and against `find`
# over the fixture tree (the same outside-the-tool pair E1 uses for the entity lines).
countis "V3: one GSVTK-VALUE line per config, so no config can drop its value census unseen" E1 \
    '^GSVTK-VALUE config=' 42
vlines="$(grep -cE '^GSVTK-VALUE config=' "$TMP/E1.out")"
wanteq "V3: and the lines come from files: the same find() pair E1 runs, on the value prefix" "$vlines" "$files"
countis "V3: and exactly one value census line, so the totals have one home" E1 '^GSVTK-VALUE-SUMMARY ' 1
wantf "V3: the fixture value census, pinned field by field (a moved number is a fixture changed)" E1 1 \
    "GSVTK-VALUE-SUMMARY configs=42 bindings=85 workspace-attr=0 workspace-missing=4 entity=54 literal=2 " \
    "needs-bundle=2 test-bundle=5 docker=9 mixed=5 no-oracle=4 " \
    "nested-reads=1 workspace-reads=22 workspace-resolved-reads=1 workspace-missing-reads=7 " \
    "no-oracle-reads=14 bundles=fixtures,test_gatksv_cohort,test_single_sample_NA12878 " \
    "dropped-values=2 dropped-workspace-reads=1 unresolved=13 configs-unresolved=20 configs-no-oracle=4 " \
    "configs-needs-bundle=2 configs-no-values=1 configs-unclassified=4"
# --- V4: R15's identity, RECOMPUTED off the printed lines ----------------------------------------
# R15 asks for the invariant, not the fields: restating `bindings=60` beside `no-oracle=8` is two numbers that
# drift together, while summing the printed class fields and comparing against the printed `bindings=` breaks
# when a class is added to `VALUE_CLASSES` without being added to the list below (the sum then misses it while
# `bindings=` does not). Both directions are graded: the census, and every one of the 42 per-config lines.
VCLASS_FIELDS="workspace-attr workspace-missing entity literal needs-bundle test-bundle docker mixed no-oracle"
# classsum [FILE...] — print "<bindings> <sum-of-VCLASS_FIELDS>" for each machine line on stdin/FILE, read off
# the fields the tool printed. `$1 in want` is what makes the list above the only spelling of those names.
classsum() {
    VCL="$VCLASS_FIELDS" awk '
        BEGIN { n = split(ENVIRON["VCL"], f, " "); for (i = 1; i <= n; i++) want[f[i]] = 1 }
        { b = -1; s = 0
          for (i = 1; i <= NF; i++) {
              p = index($i, "="); if (!p) continue
              k = substr($i, 1, p - 1); v = substr($i, p + 1) + 0
              if (k == "bindings") b = v; else if (k in want) s += v
          }
          print b, s }' "$@"
}
sum_pair="$(grep '^GSVTK-VALUE-SUMMARY ' "$TMP/E1.out" | classsum)"
wanteq "V4: R15 recomputed — the printed bindings= equals the sum of the printed class fields (census)" \
    "${sum_pair% *}" "${sum_pair#* }"
badlines="$(grep -E '^GSVTK-VALUE config=' "$TMP/E1.out" | classsum | awk '$1 != $2 { n++ } END { print n + 0 }')"
wanteq "V4: and it holds on EVERY per-config line, not only in the total" "$badlines" 0
classmis="$(printf 'GSVTK-VALUE config=x bindings=7 docker=5 mixed=1\n' | classsum | awk '$1 != $2 { print "caught" }')"
wanteq "V4: the recomputation itself notices a skew — a class field left out of the list is a finding" \
    "$classmis" "caught"
# --- V5: R16 — three states, three words, and the instance epsilon had to earn -------------------
# One reason for three different states is a refusal that cannot tell an operator what to fix. The words are
# pinned per state off the committed corpus (alpha ABSENT, gamma MALFORMED, epsilon DUPE), and then counted:
# three DISTINCT words, counted off the run rather than off the source.
wantline "V5: ABSENT refuses under \`no-oracle-absent\`" E1 \
    '^GSVTK-VALUE config=alpha/workflow_configurations/ZeroNameKeyNoThis\.json\.tmpl .* reasons=no-oracle-absent$'
wantline "V5: MALFORMED under \`no-oracle-malformed\`" E1 \
    '^GSVTK-VALUE config=gamma/GammaValues\.json\.tmpl .* reasons=no-oracle-malformed$'
wantline "V5: DUPE under \`no-oracle-dupe\`, reachable only because epsilon binds a non-\`_docker\` value too" E1 \
    '^GSVTK-VALUE config=epsilon/EpsilonValues\.json\.tmpl .* reasons=no-oracle-dupe$'
oracle_words="$(grep -oE 'reasons=[^ ]*' "$TMP/E1.out" | tr ',' '\n' \
    | sed -n 's/.*\(no-oracle-[a-z]*\)$/\1/p' | sort -u)"
wanteq "V5: R16 — the three states print three DIFFERENT words (pairwise distinct, counted off the run)" \
    "$(printf '%s\n' "$oracle_words" | grep -c .)" 3
wantf "V5: and the DUPE refusal says which attribute is declared twice, which is the thing to fix" E1 1 \
    "UNRESOLVED-VALUE EpsilonValues.cohort_ped_file [no-oracle]" \
    "\`gatk_docker\` is declared 2 times (rows 1, 3)"

# --- V6: R18 — the docker rule reads BOTH ends of a binding ---------------------------------------
# `*_docker` used to be a test on the KEY alone, so `ZeroNameKey.docker` — bound `${workspace.sv_base_mini_docker}`
# — was classed by whatever the flavor's oracle said about it, and `DeltaNoTables.docker` refused for want of a
# table. R18 rules that this is backwards: "the image must be stated" is a claim about the IMAGE, so a value
# naming a `*_docker` workspace attribute is docker-shaped whoever the key is called. Both directions are
# pinned, because a rule with one direction is a rule nobody checked:
#   * the committed corpus, where four fixtures bind a `*_docker` attribute under a key that does NOT end
#     `_docker` (IdThing.docker, ZeroNameKey.docker, ZeroNameKeyNoThis.docker, DeltaNoTables.docker). Three
#     of them sat in `no-oracle` before; all four are `docker` now, and delta's went from a REFUSAL to an
#     EXCLUSION, which is why `unresolved=` reads smaller — the image is withheld, the launch is not blocked.
#   * a corpus built here, where the same shape under an ordinary attribute name stays `workspace-attr`:
#     `R18Pair.ref` binds `${workspace.reference_fasta}`, the flavor declares it, it resolves, and only the
#     image key is named beside DOCKER-MUST-BE-STATED. Measured 0 instances of the value-shaped arm at
#     `e1909d2f` (R1), so no real number moves: this leg is the shape's only witness anywhere.
R18F="$TMP/r18/inputs/templates/terra_workspaces/r18"
mkdir -p "$R18F"
printf 'entity:cohort_id\tsites_vcf\n' > "$R18F/cohort.tsv.tmpl"
printf 'workspace:reference_fasta\t{{ reference_resources.reference_fasta }}\nsv_pipeline_docker\t{{ dockers.sv_pipeline_docker }}\n' \
    > "$R18F/workspace.tsv.tmpl"
printf '{\n  "R18Pair.cohort": "${this.cohort_id}",\n  "R18Pair.image": "${workspace.sv_pipeline_docker}",\n  "R18Pair.ref": "${workspace.reference_fasta}"\n}\n' \
    > "$R18F/R18Pair.json.tmpl"
runsc V6 "${BASE[@]}" "$PY" "$TOOL" --tree "$TMP/r18"
wantf "V6: R18 both ways in one line — the image key is docker, the declared attribute beside it is WORKSPACE-ATTR" V6 0 \
    "GSVTK-VALUE config=r18/R18Pair.json.tmpl flavor=r18 wf=R18Pair bindings=3 workspace-attr=1 workspace-missing=0 entity=1 literal=0 needs-bundle=0 test-bundle=0 docker=1 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=2 workspace-resolved-reads=2 workspace-missing-reads=0 no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=0 state=OK reasons=-" \
    "declares 2 attribute(s) from 2 row(s): reference_fasta, sv_pipeline_docker"
# `workspace-resolved-reads=2` beside `workspace-attr=1` is the two questions staying separate: the oracle
# ANSWERED both names (it declares both), and the class still withholds the image, because "a row exists"
# was never the same claim as "this launch may carry this value".
wantf "V6: the oracle answered both reads; only the ordinary one became a value the launch may carry" V6 0 \
    "workspace-reads=2 workspace-resolved-reads=2" \
    "workspace-attr=1 workspace-missing=0"
wantf "V6: and only the image is withheld — the sentence says both ends of the binding it just applied" V6 0 \
    "DOCKER-MUST-BE-STATED 1 key(s) (R18Pair.image)" \
    "a binding is excluded when its leaf key ends \`_docker\` OR when the value it binds IS a single \`\${workspace.*_docker}\` read"
wantabsent "V6: the launchable value is not swept into the docker class, and an exclusion is not a refusal" V6 \
    'R18Pair\.ref|GSVTK-VALUE .* state=UNRESOLVED'
wantf "V6: the committed corpus carries the shape four times — a leaf named \`docker\` is not a \`_docker\` key" E1 1 \
    "GSVTK-VALUE config=alpha/workflow_configurations/IdNoType.json.tmpl flavor=alpha wf=IdThing bindings=2 workspace-attr=0 workspace-missing=0 entity=1 literal=0 needs-bundle=0 test-bundle=0 docker=1 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=1 workspace-resolved-reads=0 workspace-missing-reads=0 no-oracle-reads=1 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=0 state=OK reasons=-" \
    "DOCKER-MUST-BE-STATED 1 key(s) (IdThing.docker)"
wantline "V6: delta's value became an exclusion, not a refusal (the corpus refuses 4 fewer values: V3 pins it)" E1 \
    '^GSVTK-VALUE config=delta/DeltaNoTables\.json\.tmpl .* docker=1 mixed=0 no-oracle=0 .* unresolved=0 state=OK reasons=-$'

# --- V7: FOUR answers, four prints ---------------------------------------------------------------
# "no configs", "a config that binds nothing", "a config nobody can read" and "everything resolved" are four
# different questions, and the census is the only place a caller can tell them apart — so it must never let
# one 0 stand for two of them. Until `NoValues.json.tmpl` existed, TWO of the four had no instance anywhere:
# `configs-no-values` was printed at 0 on every corpus in this repo, so a reader could not see what its 1
# would mean, and the pair that did exist (`configs-no-values` vs `configs-unclassified`) was distinguishable
# only by reading the source. The new fixture is one key whose value is an EMPTY object — a container that
# binds no scalar — which is "nothing in it", said next to `CannotParse`'s "nobody can read it", both counted,
# neither printed as the other. U1 later adds a SECOND unreadable document (a config with one undecodable
# byte), so `configs-unclassified=2` is two unreadable documents and `configs-no-values=1` is still one empty
# one — the pair stays two answers with different counts, which is the point. The remaining two answers are
# pinned off runs this file already has: E17e (0 configs, exit 3) and V1 (everything resolved, exit 0), each
# named with BOTH counters at 0 in the same leg.
wantf "V7: a config that binds nothing says \`no-values\` — 0 scalars is not an unreadable document" E1 1 \
    "GSVTK-VALUE config=alpha/workflow_configurations/NoValues.json.tmpl flavor=alpha wf=NoValues bindings=0 workspace-attr=0 workspace-missing=0 entity=0 literal=0 needs-bundle=0 test-bundle=0 docker=0 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=0 workspace-resolved-reads=0 workspace-missing-reads=0 no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=0 state=UNRESOLVED reasons=no-values" \
    "UNRESOLVED-VALUE - [no-values] the document binds no value at all (0 scalar(s))"
wantline "V7: CannotParse says \`values-unreadable\`, a DIFFERENT reason for its own 0 scalars" E1 \
    '^GSVTK-VALUE config=alpha/workflow_configurations/CannotParse\.json\.tmpl .* bindings=0 .* reasons=values-unreadable$'
wantf "V7: the census counts the unreadable documents and the empty one separately" E1 1 \
    "configs-no-values=1 configs-unclassified=4"
countis "V7: exactly one config answers \`no-values\`" E1 '^GSVTK-VALUE config=.* reasons=no-values$' 1
countis "V7: and four answer values-unreadable: truncated JSON, an undecodable byte (U1), two templates" E1 \
    '^GSVTK-VALUE config=.* reasons=values-unreadable$' 4
wantabsent "V7: and no config is ever handed both words at once" E1 \
    'reasons=[^ ]*no-values[^ ]*values-unreadable|reasons=[^ ]*values-unreadable[^ ]*no-values'
wantf "V7: the third answer — a corpus with no config — prints configs=0 with BOTH counters at 0" E17e 3 \
    "GSVTK-VALUE-SUMMARY configs=0 bindings=0 " \
    "unresolved=0 configs-unresolved=0 configs-no-oracle=0 configs-needs-bundle=0 configs-no-values=0 " \
    "configs-unclassified=0"
wantf "V7: the fourth — everything resolved — is exit 0, unresolved=0, and both counters at 0 again" V1 0 \
    "unresolved=0 configs-unresolved=0 configs-no-oracle=0 configs-needs-bundle=0 configs-no-values=0 " \
    "configs-unclassified=0"

# --- V8: POSITIVE CONTROL for `test-bundle`: three arms, three shapes, one class --------------------
# B0 measured 0 test markers in upstream's configs and workspace tables at `e1909d2f` — R1 prints
# `test-bundle=0` — so NOTHING on real data proves this class exists, exactly as the docstring says. The three
# committed configs are therefore the class's entire evidence, and this is the shape CONTRIBUTING.md and §12.3b
# of scripts/selftest.d/womtool.sh call a positive control: one leg per arm, each requiring the SAME
# `reason=test-bundle`, because each is a value that must never reach a launch:
#   * `TestBundleRoot` binds `{{ test_gatksv_cohort.* }}` — the ROOT says test, in a tree that ships a
#     DIFFERENT test profile, so the refusal has to name both facts: the root it matched, and what is shipped;
#   * `TestBundleCoordinate` carries `gs://` inside a string with no read and no `{{ }}` in it at all — the
#     value `literal` (the one class that supplies a real value) would have handed the launch;
#   * `TestBundleShipped` names `test_single_sample_NA12878`, the stem of a profile THIS tree ships, so the
#     parenthetical quotes a real stem instead of `none`. That is the same claim the `git ls-tree` fix had to
#     make true on the `--repo/--ref` path (R-profiles), proven here off the directory listing `--tree` uses.
# `inputs/values/dockers.json` ships beside the test profile and must NOT join the list: recognition is by
# name. And no profile is ever opened — the sentinel in both files appears nowhere in the run, which is the
# only way to show a file that is listed was not read.
wantf "V8: a \`{{ }}\` root named \`test_*\` refuses, and quotes the profiles the tree ships" E1 1 \
    "GSVTK-VALUE config=beta/TestBundleRoot.json.tmpl flavor=beta wf=TestBundleRoot bindings=1 workspace-attr=0 workspace-missing=0 entity=0 literal=0 needs-bundle=0 test-bundle=1 docker=0 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=0 workspace-resolved-reads=0 workspace-missing-reads=0 no-oracle-reads=0 bundles=test_gatksv_cohort dropped-values=0 dropped-workspace-reads=0 unresolved=1 state=UNRESOLVED reasons=test-bundle" \
    "UNRESOLVED-VALUE TestBundleRoot.tracks [test-bundle]" \
    "shipped test profile(s): test_single_sample_NA12878; no profile was opened to decide this)"
wantf "V8: a \`gs://\` inside an otherwise-literal string refuses too — the coordinate rule reaches in" E1 1 \
    "GSVTK-VALUE config=beta/TestBundleCoordinate.json.tmpl flavor=beta wf=TestBundleCoordinate bindings=1 workspace-attr=0 workspace-missing=0 entity=0 literal=0 needs-bundle=0 test-bundle=1 docker=0 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=0 workspace-resolved-reads=0 workspace-missing-reads=0 no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=1 state=UNRESOLVED reasons=test-bundle" \
    "UNRESOLVED-VALUE TestBundleCoordinate.ref_fasta [test-bundle] TestBundleCoordinate.ref_fasta belongs to a TEST bundle: it carries the test-sample coordinate \`gs://\`"
wantf "V8: and a root naming a profile this tree SHIPS is refused by name, listing the stem it matched" E1 1 \
    "GSVTK-VALUE config=beta/TestBundleShipped.json.tmpl flavor=beta wf=TestBundleShipped bindings=1 workspace-attr=0 workspace-missing=0 entity=0 literal=0 needs-bundle=0 test-bundle=1 docker=0 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=0 workspace-resolved-reads=0 workspace-missing-reads=0 no-oracle-reads=0 bundles=test_single_sample_NA12878 dropped-values=0 dropped-workspace-reads=0 unresolved=1 state=UNRESOLVED reasons=test-bundle" \
    "UNRESOLVED-VALUE TestBundleShipped.tracks [test-bundle] TestBundleShipped.tracks belongs to a TEST bundle: its \`{{ test_single_sample_NA12878.* }}\` read names a test values profile by root"
countis "V8: three arms, three configs, one class — and nothing else in the corpus is classed test-bundle" E1 \
    '^GSVTK-VALUE config=.* test-bundle=1 ' 3
wantabsent "V8: a shipped NON-test profile is not mistaken for one, because the rule is a name rule" E1 \
    'shipped test profile\(s\): [^)]*dockers'
wantabsent "V8: and no values profile was ever OPENED — the sentinel in both files is in no line of the run" E1 \
    'SENTINEL-NEVER-OPENED'

# --- V9: MIXED refuses under its own words, and `unresolved=` does NOT widen ------------------------
# MIXED is text around reads, so it has no whole-value answer to refuse: its refusals live in `reasons=` and
# NOWHERE else, because ruling 2 keeps `unresolved=` the sum of the three refusing CLASSES — which is what
# makes R15's identity (`bindings == sum of the class fields`) survive a MIXED refusal. That is a rule, so it
# gets pinned rather than remembered: both legs below assert `unresolved=0` IN THE SAME LINE as
# `state=UNRESOLVED reasons=mixed-…`, on two configs whose ENTITY layer answered OK, so the refusal has one
# cause and a counter that visibly did not move. The two arms are the two states MIXED can meet: a flavor
# whose table is ABSENT (`mixed-no-oracle-absent`) and an OK flavor naming an undeclared attribute
# (`mixed-workspace-missing`); the `dupe`/`malformed` variants share the code path and the words V5 counts.
wantf "V9: \${[ … ]} in a flavor with NO workspace table refuses under \`mixed-no-oracle-absent\`" E1 1 \
    "GSVTK-VALUE config=delta/MixedNoOracle.json.tmpl flavor=delta wf=MixedNoOracle bindings=1 workspace-attr=0 workspace-missing=0 entity=0 literal=0 needs-bundle=0 test-bundle=0 docker=0 mixed=1 no-oracle=0 nested-reads=0 workspace-reads=1 workspace-resolved-reads=0 workspace-missing-reads=0 no-oracle-reads=1 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=0 state=UNRESOLVED reasons=mixed-no-oracle-absent" \
    "UNRESOLVED-VALUE MixedNoOracle.inputs [mixed]"
wantf "V9: the same shape naming an undeclared attribute in an OK flavor: \`mixed-workspace-missing\`" E1 1 \
    "GSVTK-VALUE config=beta/MixedWorkspaceMissing.json.tmpl flavor=beta wf=MixedWorkspaceMissing bindings=1 workspace-attr=0 workspace-missing=0 entity=0 literal=0 needs-bundle=0 test-bundle=0 docker=0 mixed=1 no-oracle=0 nested-reads=0 workspace-reads=1 workspace-resolved-reads=0 workspace-missing-reads=1 no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=0 state=UNRESOLVED reasons=mixed-workspace-missing" \
    "UNRESOLVED-VALUE MixedWorkspaceMissing.inputs [mixed]"
wantf "V9: the 0 in \`unresolved=0\` is MIXED not widening it, not a clean config: the ENTITY line beside it is OK" E1 1 \
    "GSVTK-ENTITY config=delta/MixedNoOracle.json.tmpl flavor=delta wf=MixedNoOracle filestem=MixedNoOracle namematch=yes state=OK namekeys=1 namekey=sample_set collections=- reads_direct=1 reads_member=0 member_attrs=0 tables_of_type=1 table=alpha/entity_sample_set.tsv.tmpl table_kind=entity columns=3 missing=- nested=0 barethis=0 dupkeys=0 neutralised=0 unbraced=0 reason=-" \
    "GSVTK-ENTITY config=beta/MixedWorkspaceMissing.json.tmpl flavor=beta wf=MixedWorkspaceMissing filestem=MixedWorkspaceMissing namematch=yes state=OK namekeys=1 namekey=sample_set collections=- reads_direct=1 reads_member=0 member_attrs=0 tables_of_type=1 table=alpha/entity_sample_set.tsv.tmpl table_kind=entity columns=3 missing=- nested=0 barethis=0 dupkeys=0 neutralised=0 unbraced=0 reason=-"
mixed_words="$(grep -oE 'reasons=[^ ]*' "$TMP/E1.out" | tr ',' '\n' \
    | sed -n 's/.*\(mixed-[a-z-]*\)$/\1/p' | sort -u)"
wanteq "V9: MIXED prints its OWN words — two counted off the run, absent and workspace-missing" \
    "$(printf '%s\n' "$mixed_words" | grep -c .)" 2
wantabsent "V9: and a MIXED value never borrows the whole-value word: the prefix says there is no whole value" E1 \
    '^GSVTK-VALUE config=(delta/MixedNoOracle|beta/MixedWorkspaceMissing)\.json\.tmpl .* reasons=(workspace-missing|no-oracle-[a-z]+)(,|$)'

# --- V10: R20 — the docker-by-value arm reads the WHOLE value, so an exclusion cannot swallow grading ----
# `1401bba` implemented R18's value end as "any value CARRYING a `*_docker` workspace read". `docker` is
# `DOCKER-MUST-BE-STATED`: an EXCLUSION that refuses nothing. So the wide arm made a value that mentions an
# image stop being graded for every OTHER read it carried, and the two committed probes measured it as the
# same missing attribute with opposite answers — `--img ${workspace.cloud_sdk_docker} --ref ${workspace.X}`
# was `docker=1 mixed=0 state=OK` while `--img ${workspace.X}` was `mixed=1 state=UNRESOLVED`. Whether the
# launch is launchable cannot depend on whether somebody concatenated, so R20 narrows the arm to a value that
# IS the image (`whole_docker`, a fullmatch of one `${workspace.X}`).
# Both directions are pinned, and the third leg is the direction that must NOT move: `delta/DeltaNoTables` is
# the whole-value form whose LEAF is `docker`, so only the value rule can class it docker and its `docker=1`
# is that rule still alive. `V3` carries the census evidence for the rest of the corpus: `docker=9` before
# this pair existed and `docker=9` after it, which is the nine fixture docker reads all being whole-value.
wantf "V10: a command that CARRIES an image is a command — the read beside it is graded, and it refuses" E1 1 \
    "GSVTK-VALUE config=beta/CommandCarriesDocker.json.tmpl flavor=beta wf=GATKSVPipelineSynth bindings=2 workspace-attr=0 workspace-missing=0 entity=1 literal=0 needs-bundle=0 test-bundle=0 docker=0 mixed=1 no-oracle=0 nested-reads=0 workspace-reads=2 workspace-resolved-reads=1 workspace-missing-reads=1 no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=0 state=UNRESOLVED reasons=mixed-workspace-missing" \
    "GSVTK-ENTITY config=beta/CommandCarriesDocker.json.tmpl flavor=beta wf=GATKSVPipelineSynth filestem=CommandCarriesDocker namematch=no state=OK namekeys=1 namekey=participant collections=- reads_direct=1 reads_member=0 member_attrs=0 tables_of_type=1 table=beta/entity_participant.tsv.tmpl table_kind=entity columns=0 missing=- nested=0 barethis=0 dupkeys=0 neutralised=0 unbraced=0 reason=-" \
    "UNRESOLVED-VALUE GATKSVPipelineSynth.args [mixed]"
wantf "V10: the control — the same missing attribute in a value naming no image gets the SAME answer" E1 1 \
    "GSVTK-VALUE config=beta/CommandCarriesNoDocker.json.tmpl flavor=beta wf=GATKSVPipelineSynth bindings=2 workspace-attr=0 workspace-missing=0 entity=1 literal=0 needs-bundle=0 test-bundle=0 docker=0 mixed=1 no-oracle=0 nested-reads=0 workspace-reads=1 workspace-resolved-reads=0 workspace-missing-reads=1 no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=0 state=UNRESOLVED reasons=mixed-workspace-missing" \
    "UNRESOLVED-VALUE GATKSVPipelineSynth.args [mixed]"
wantline "V10: the whole-value form is untouched — a leaf named \`docker\` bound to one \${workspace.*_docker} is still docker" E1 \
    '^GSVTK-VALUE config=delta/DeltaNoTables\.json\.tmpl .* docker=1 mixed=0 .* unresolved=0 state=OK reasons=-$'
wantabsent "V10: neither command is swept into the docker class, which is what let the missing read through" E1 \
    '^GSVTK-VALUE config=beta/CommandCarries.*\.json\.tmpl .* docker=1 '
countis "V10: three configs refuse under \`mixed-workspace-missing\` — the pre-existing one and this pair" E1 \
    '^GSVTK-VALUE config=.* reasons=mixed-workspace-missing$' 3

# --- V11: the two `mixed-` words that no fixture reaches, pinned as WORDS -------------------------------
# V9 counts the `mixed-` words a run PRINTS, and the corpus prints two: `mixed-no-oracle-absent` and
# `mixed-workspace-missing`. The other two entries of `MIXED_NO_ORACLE_REASONS` — `-malformed` and `-dupe` —
# have NO instance anywhere in this repo: they appear in the dictionary and nowhere else, which is why V9's
# "exactly two distinct" could stay green if those two words were rewritten to be IDENTICAL to `absent` and
# every leg in this file kept passing. That gap is stated here rather than papered over: what follows pins
# WORD DISTINCTNESS (R16 applies to the `mixed-` words too — a refusal that cannot tell an operator which of
# the three states it met is a refusal nobody can act on) and it does NOT pin ARM REACHABILITY. The
# `MALFORMED` and `DUPE` arms of the MIXED branch still have no instance: reaching them needs a value that
# is text around reads in a flavor whose `workspace.tsv.tmpl` is malformed or duplicated, and no fixture is
# that shape — `gamma` and `epsilon` are those two flavors and each ships only whole-value configs
# (V5's `no-oracle-malformed` / `no-oracle-dupe` are those whole-value refusals, reached from the other
# branch). A second leg names the gap from the run's side: the two words are in the module and in no line of
# the corpus output. This needs no checkout, so it sits OUTSIDE the `entity-no-checkout` guard: it reads this
# repo's own source, and a CI run that withholds the real tree still has to answer for these words.
MIXED_PROBE='import sys
sys.path.insert(0, "checks")
import terra_entity_check as t
m = t.MIXED_NO_ORACLE_REASONS
print("GSVTK-MIXED-WORDS %s" % ", ".join("%s=%s" % (k, m[k]) for k in ("ABSENT", "MALFORMED", "DUPE")))
print("GSVTK-MIXED-DISTINCT %s" % len(set(m.values())))
print("GSVTK-MIXED-DISJOINT %s" % (set(m.values()).isdisjoint(set(t.NO_ORACLE_REASONS.values()))))'
runsc V11 "${BASE[@]}" "$PY" -c "$MIXED_PROBE"
wantf "V11: the three MIXED words are three DIFFERENT words — pinned as words, because only two have an instance" V11 0 \
    "GSVTK-MIXED-WORDS ABSENT=mixed-no-oracle-absent, MALFORMED=mixed-no-oracle-malformed, DUPE=mixed-no-oracle-dupe" \
    "GSVTK-MIXED-DISTINCT 3" \
    "GSVTK-MIXED-DISJOINT True"
wantabsent "V11: and the two words above that the corpus never prints are not in any line of the corpus run — the arms have no instance" E1 \
    'mixed-no-oracle-(malformed|dupe)'

# ===============================================================================================
# THE DROP, THE KEY NAME, THE UNFINISHED TEMPLATE, AND THE FOUR STATES NOTHING COULD FAIL ON.
#
# What the three defects below share is the DIRECTION of the lie: each printed a machine line saying
# `state=OK` (or an exclusion that refuses nothing) about something this file had already learned to refuse.
# Each therefore ships a fixture, a leg naming the counter that MOVES, and a leg naming the thing that must
# NOT happen — the second half is what stops a "fix" that silences the symptom: D1 requires the refusal to
# stay absent, M1 requires the value line to stop saying OK, K1 requires both halves of each pair to agree.
# The G legs then take the same argument one step further, because items D/K/M are the proof: a state that no
# leg can reach is indistinguishable from a state that cannot happen, and all three defects were documented
# as unreachable, then reached, then found crashing.
# --- D: a duplicated key whose DROPPED value is a container ----------------------------------------
# `parse_with_bundle_marks` claimed the markers of every key and value, but only those sitting directly in a
# STRING, and the duplicate-key scan asked `_reads_of` the same way. A key bound twice whose EARLIER value is
# a LIST carrying a `{{ }}` marker therefore held a marker neither reader saw, which reached the `unclaimed`
# guard — and that guard built its sentence with `%d` against a string, so it died with
# `TypeError: %d format: a real number is required, not str`: zero machine lines, exit 1, for a config whose
# values are perfectly attributable. Two fixes, both pinned: the scan walks the strings inside containers, and
# a firing PRINTS. The scan had a third half — it asked about the SURVIVING value, not the dropped one, so a
# dropped read was counted twice and `dropped-workspace-reads` stayed 0. Invisible at `e1909d2f`, where
# `mei_bed`'s two values each read exactly one attribute; immediate in this fixture.
wantf "D1: the container-duplicate fixture, with the dropped read COUNTED on its own line" E1 1 \
    "GSVTK-VALUE config=alpha/workflow_configurations/DroppedList.json.tmpl flavor=alpha wf=DroppedList bindings=2 workspace-attr=0 workspace-missing=0 entity=2 literal=0 needs-bundle=0 test-bundle=0 docker=0 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=0 workspace-resolved-reads=0 workspace-missing-reads=0 no-oracle-reads=0 bundles=- dropped-values=1 dropped-workspace-reads=1 unresolved=0 state=OK reasons=-" \
    "1 key(s) bound more than once (\`DroppedList.tracks\`)"
wantf "D1: THE CONTROL — the corpus counters move: 2 dropped values with 1 read among them (was 1 and 0)" E1 1 \
    "dropped-values=2 dropped-workspace-reads=1"
wantf "D1: and the human line names the dropped sides read, not zero" E1 1 \
    "1 value(s) dropped by a duplicated key" "the dropped side's 1 \${workspace.*} read(s)"
wantabsent "D1: the unattributed-bundle refusal does NOT fire on any fixture, and nothing crashes" E1 \
    'bundle-read-unattributed|TypeError|Traceback'
countis "D1: no config in the committed corpus reaches the tripwire (it is a tripwire, not a normal answer)" E1 \
    '^GSVTK-VALUE config=.* reasons=bundle-read-unattributed$' 0
# The tripwire controls itself. `claimed` (the pair scan) and `seen` (the value walk) are BOTH exhaustive
# now, so no INPUT can raise this reason: the guard stays as a check on THIS FILE's two scans agreeing, and
# the only way to reach it is to hand the classifier a parse whose `exprs` holds a marker neither scan
# claims — which is what the probe below injects. What it asserts is the other half of the bug: a firing
# prints, so the guard is worth having at all.
UNCLAIM_F="$TMP/unclaim/inputs/templates/terra_workspaces/unclaim"
mkdir -p "$UNCLAIM_F"
printf 'entity:cohort_id\tsites_vcf\n' > "$UNCLAIM_F/cohort.tsv.tmpl"
printf '{\n  "UnclaimJob.cohort": "${this.cohort_id}",\n  "UnclaimJob.sites": "${this.sites_vcf}"\n}\n' \
    > "$UNCLAIM_F/UnclaimJob.json.tmpl"
UNCLAIM_PROBE='import sys
sys.path.insert(0, "checks"); sys.path.insert(0, "kit")
import terra_entity_check as t
real = t.parse_with_bundle_marks
def doctored(path, jinja_re):
    p = real(path, jinja_re)
    if p["ok"]:
        p["exprs"] = p["exprs"] + ["{{ phantom.root | tojson }}"]
    return p
t.parse_with_bundle_marks = doctored
sys.exit(t.main(["--tree", sys.argv[1]]))'
runsc D2 "${BASE[@]}" "$PY" -c "$UNCLAIM_PROBE" "$TMP/unclaim"
wantf "D2: with the two scans FORCED to disagree the refusal is filed, counted, and the config refuses" D2 1 \
    "GSVTK-VALUE config=unclaim/UnclaimJob.json.tmpl flavor=unclaim wf=UnclaimJob bindings=2 workspace-attr=0 workspace-missing=0 entity=2 literal=0 needs-bundle=0 test-bundle=0 docker=0 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=0 workspace-resolved-reads=0 workspace-missing-reads=0 no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=0 state=UNRESOLVED reasons=bundle-read-unattributed" \
    "1 \`{{ ... }}\` expression(s) in this file could not be tied to a binding value or key ({{ phantom.root | tojson }})" \
    "refuses to guess which key would have carried them"
wantabsent "D2: and a FIRING prints — no TypeError and no traceback, which is the whole reason it is assertable" D2 \
    'TypeError|Traceback'
# --- K: a `*_docker` KEY used to outrank two refusals ----------------------------------------------
# The key-leaf exclusion sat at the TOP of the class chain, so `X.manta_docker` bound to a `gs://` test path
# was `docker` (an exclusion: refuses nothing) while the identical value under `X.manta_image` was
# `test-bundle` (a refusal); and `X.manta_docker` bound to a `${workspace.X}` the flavor does not declare was
# `docker` while the same value under `X.manta_image` was `workspace-missing`. Same value, same bundle,
# opposite answers, decided by nothing but the spelling of the key. The exclusion now runs LAST and applies
# to whatever the refusals did not claim; `DOCKER-MUST-BE-STATED` stays the answer for a value that genuinely
# is an image. Measured 0 instances of either swallowed shape at `e1909d2f` — all 101 docker bindings there
# are a `_docker` leaf bound to a `_docker` workspace read — which is what lets this land on real data with
# no number moved (`unresolved=16 configs-unresolved=15` before and after).
wantf "K1: TEST BUNDLE beats the *_docker key — both spellings in one config, both refused, neither docker" E1 1 \
    "GSVTK-VALUE config=beta/KeyLeafDocker.json.tmpl flavor=beta wf=KeyLeafDocker bindings=3 workspace-attr=0 workspace-missing=0 entity=1 literal=0 needs-bundle=0 test-bundle=2 docker=0 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=0 workspace-resolved-reads=0 workspace-missing-reads=0 no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=2 state=UNRESOLVED reasons=test-bundle" \
    "UNRESOLVED-VALUE KeyLeafDocker.manta_docker [test-bundle]" \
    "UNRESOLVED-VALUE KeyLeafDocker.manta_image [test-bundle]"
wantf "K1: an UNDECLARED ATTRIBUTE beats the *_docker key too, and both spellings refuse alike" E1 1 \
    "GSVTK-VALUE config=beta/KeyLeafMissing.json.tmpl flavor=beta wf=KeyLeafMissing bindings=3 workspace-attr=0 workspace-missing=2 entity=1 literal=0 needs-bundle=0 test-bundle=0 docker=0 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=2 workspace-resolved-reads=0 workspace-missing-reads=2 no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=2 state=UNRESOLVED reasons=workspace-missing" \
    "UNRESOLVED-VALUE KeyLeafMissing.manta_docker [workspace-missing]" \
    "UNRESOLVED-VALUE KeyLeafMissing.manta_image [workspace-missing]"
wantabsent "K1: so a key name is not a licence — neither config is classed docker on either arm" E1 \
    '^GSVTK-VALUE config=beta/KeyLeaf[A-Za-z]*\.json\.tmpl .* docker=[1-9] '
wantline "K1: and the exclusion still EXCLUDES a value that IS an image, under a key that does not say docker" E1 \
    '^GSVTK-VALUE config=delta/DeltaNoTables\.json\.tmpl .* docker=1 .* unresolved=0 state=OK reasons=-$'
wantf "K1: the sentence says the new precedence out loud, next to the rule it does not repeal" E1 1 \
    "a refusal always beats this exclusion"
# --- M: an unfinished brace pair is not launch data -----------------------------------------------
# `JINJA_RE` is `\{\{.*?\}\}`, so a placeholder missing its `}}` is never neutralised and never seen again:
# a binding whose value is `{{ reference_resources.tracks` with no close parsed, classified `literal`, and
# `literal` is the ONE class this file says a launch may carry — raw template text as launch data, printed
# with `state=OK` beside it. The `unbraced=` counter was the only witness to the `${` twin and was asserted
# solely as `0`. Both halves are refused now, in BOTH readers, under words that already existed
# (`cannot-parse`, `values-unreadable`) — a refusal in one layer and a pass in the other is the split this
# file keeps closing, so the value line is asserted to stop saying OK, not just the entity line to start
# saying no. `configs-unclassified` moves 1 -> 4 for exactly that reason: CannotParse, U1's undecodable byte,
# and these two.
wantline "M1: the unterminated {{ half is cannot-parse on the entity line" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/UnterminatedBundle\.json\.tmpl .* state=UNRESOLVED .* reason=cannot-parse$'
wantline "M1: and values-unreadable on the value line, with 0 bindings classified" E1 \
    '^GSVTK-VALUE config=alpha/workflow_configurations/UnterminatedBundle\.json\.tmpl .* bindings=0 .* reasons=values-unreadable$'
wantabsent "M1: THE CONTROL — neither unfinished document prints state=OK, which is the half a 1-layer fix keeps" E1 \
    '^GSVTK-VALUE config=alpha/workflow_configurations/(UnterminatedBundle|UnbracedBinding)\.json\.tmpl .* state=OK '
wantabsent "M1: and neither is ever classed literal, which is what made it launch data" E1 \
    '^GSVTK-VALUE config=alpha/workflow_configurations/(UnterminatedBundle|UnbracedBinding)\.json\.tmpl .* literal=[1-9] '
wantline "M1: the unbraced dollar-brace half refuses too, and its counter is non-zero for the first time here" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/UnbracedBinding\.json\.tmpl .*unbraced=1 reason=cannot-parse$'
wantf "M1: so four documents are unreadable now, and the census says four, not two" E1 1 \
    "configs-no-values=1 configs-unclassified=4"
# The NEGATIVE control, which earned its keep by finding a bug on the way in: `JINJA_RE` carries `re.S`, so an
# expression that merely spans lines is a legitimate template and must classify as it always did. It did not.
# `_shared_expander` read `args[0]` out of the launcher's `re.compile(...)` and dropped the FLAGS, so this
# file's copy of the pattern was narrower than the launcher's and a multi-line `| tojson` render came back
# `cannot-parse` — a FALSE refusal on a document that launches, which is the expensive direction to be wrong
# in. The fixture is the control for the flag extraction, and for any later edit that reads the pattern
# without its flags. `0` real instances at `e1909d2f` is a fact about one ref, not a protection for the next.
wantline "M2: a {{ }} SPANNING LINES is matched by the shared pattern, neutralised once, never refused" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/MultiLineBundle\.json\.tmpl .* state=OK namekeys=1 namekey=sample_set .* neutralised=1 unbraced=0 reason=-$'
wantline "M2: and it classifies as the disclosure it always classified as" E1 \
    '^GSVTK-VALUE config=alpha/workflow_configurations/MultiLineBundle\.json\.tmpl .* needs-bundle=1 .* bundles=fixtures .* unresolved=0 state=OK reasons=-$'
# --- N: the checkers docker advice names only keys the launcher will actually ask about ------------
# This tool withholds a value that IS a single `${workspace.*_docker}` read whoever the key is called (ruling
# R18); `terra/batch_rerun_step.py` demands a stated pin only for a key ENDING `_docker` (`_stated()` and the
# two callers that build a launch around it). So for four committed configs — leaf `docker`, no underscore:
# IdThing, ZeroNameKey, ZeroNameKeyNoThis, DeltaNoTables — this file was telling an operator to state a pin
# under a key the launcher will never look at. Aligned CHECKER-SIDE, because 0 instances exist on the real
# tree at `e1909d2f` and the launcher is a paid path: the exclusion stands (an image resolved from an ambient
# profile is still never launch data), and the sentence now says who enforces it.
wantf "N1: a key that is docker-shaped by its VALUE alone is named as one the launcher will not enforce" E1 1 \
    "DOCKER-MUST-BE-STATED 1 key(s) (IdThing.docker)" \
    "DOCKER-KEY-UNENFORCED 1 of those key(s) (IdThing.docker)" \
    "terra/batch_rerun_step.py::_stated()"
countis "N1: four configs in the corpus are docker by value alone, and all four say so" E1 \
    '^ *DOCKER-KEY-UNENFORCED' 4
wantabsent "N1: and a key whose LEAF ends _docker is never named there — the gate really is the key name" E1 \
    'DOCKER-KEY-UNENFORCED .*SingleNameKey\.sv_base_mini_docker'
# --- G: the four states no leg could fail on, and the four exits nothing asserted ------------------
# Items D/K/M are the argument for the cost: each of those was a documented-as-unreachable shape that was
# reached and found broken. A state no run prints is indistinguishable from a state that cannot happen.
# --- G1: the flavor a config earns by sitting in the corpus root -----------------------------------
wantline "G1: a config directly under the corpus is flavor (root), named, not folded into a neighbor" E1 \
    '^GSVTK-ENTITY config=RootFlavor\.json\.tmpl flavor=\(root\) wf=RootFlavor filestem=RootFlavor namematch=yes state=OK '
wantf "G1: and (root) is a flavor the oracle answers for — ABSENT, because the root ships no table" E1 1 \
    "GSVTK-ORACLE flavor=(root) file=- attrs=0 status=ABSENT" \
    "no workspace.tsv.tmpl"
countis "G1: the root flavor is in the census, so the flavors= count includes it" E1 '^GSVTK-ORACLE flavor=\(root\)' 1
# --- G2: a table with a column name nobody wrote down ---------------------------------------------
wantf "G2: columns_unreadable prints, per table, instead of quietly shortening the column list" E1 1 \
    "beta/entity_panel.tsv.tmpl                     entity:panel_id  columns: sites_file (+1 column name(s) unreadable: their header carries a {{ ... }} expression)"
countis "G2: and exactly one TABLE in the corpus earns that clause" E1 '^  [^ ]+\.tsv\.tmpl .*column name\(s\) unreadable' 1
# --- G3: a membership table is a table, and the machine line says which kind -----------------------
wantline "G3: table_kind=membership is reachable and printed (every table here was an entity table before)" E1 \
    '^GSVTK-ENTITY config=beta/MembershipTable\.json\.tmpl .* state=OK namekeys=1 namekey=batch .* table=beta/membership_batch\.tsv\.tmpl table_kind=membership .* reason=-$'
# --- G4: a config that binds two workflows --------------------------------------------------------
wantline "G4: two binding prefixes is ONE config with two workflows, printed as both, never one chosen" E1 \
    '^GSVTK-ENTITY config=alpha/workflow_configurations/TwoWorkflows\.json\.tmpl .* wf=AlphaWork,BetaWork filestem=TwoWorkflows namematch=no state=OK namekeys=1 '
# --- G5-G8: the four exits that no leg asserted ---------------------------------------------------
runsc G5 "${BASE[@]}" "$PY" "$TOOL" --tree "$TMP/empty.env"
wantf "G5: --tree aimed at a NON-DIRECTORY is a usage error (exit 2), not a crash and not a prerequisite" G5 2 \
    "usage error: --tree" "is not a directory"
mkdir -p "$TMP/nogit"
runsc G6 "${BASE[@]}" "$PY" "$TOOL" --repo "$TMP/nogit" --ref HEAD
wantf "G6: a directory that is not a git repository says so (exit 3), before any archive is attempted" G6 3 \
    "is not a git repository, so there is no ref to archive"
# A `.git` DIRECTORY that is not a repository: past the is-a-repository test, into the archive, where git
# itself refuses. The worktree this phase runs in cannot be used for either leg: a linked worktrees `.git`
# is a FILE, so it answers the first test, not the second.
mkdir -p "$TMP/badgit/.git"
runsc G7 "${BASE[@]}" "$PY" "$TOOL" --repo "$TMP/badgit" --ref HEAD
wantf "G7: a failing git archive is a named PREREQUISITE quoting the command and gits own words" G7 3 \
    "PREREQUISITE \`git -C" "archive HEAD inputs/templates/terra_workspaces\` failed"
# The expander is read out of `terra/batch_configs.py` by name so this file cannot grow a second one. The arm
# that refuses when the source carries no such assignment had no witness, and the only way to reach it
# without touching the launcher is to point the constant at a file that does not carry it — which is what
# this probe does. Refusing BEFORE the first config is read is the point: a checker that could not find the
# shared pattern must not grade a corpus with a private one.
NOEXP_PROBE='import sys
sys.path.insert(0, "checks"); sys.path.insert(0, "kit")
import terra_entity_check as t
t.EXPANDER_SOURCE = sys.argv[2]
print("GSVTK-NOEXP rc=%s" % t.main(["--tree", sys.argv[1]]))'
printf 'X = 1\n' > "$TMP/noexp.py"
runsc G8 "${BASE[@]}" "$PY" -c "$NOEXP_PROBE" "$FIX" "$TMP/noexp.py"
wantf "G8: no single expander to reuse is a stated prerequisite (exit 3), never a private pattern" G8 0 \
    "PREREQUISITE no single" "carries no JINJA_RE assignment" "GSVTK-NOEXP rc=3"
wantabsent "G8: and it refuses before reading a single config, so no verdict prints under a private pattern" G8 \
    'GSVTK-ENTITY config='
# ===============================================================================================
# THE CURE LEGS (ruling R21): the file the refusal names is the file that clears it, run for real.
#
# A refusal that stops at the diagnosis sends the operator off to find the file, and THIS corpus is built out
# of the ways to find the wrong one: `alpha/workspace_other.tsv.tmpl` is a workspace table under a near-miss
# name, `alpha/workflow_configurations/workspace.tsv.tmpl` is the oracle's EXACT name one directory down, and
# four more TSVs beside them are ENTITY tables whose first column is `entity:…`. All of them sit in the list
# the ABSENT sentence prints, so "here is what the flavor ships" printed next to a cure reads like progress
# about the decoy — which is worse than no cure, because a wrong cure reads like a right one. So R21 asks for
# two things at once: the sentence has to NAME the cure, and the cure has to be the one it names.
#
# CONTRIBUTING.md forbids documenting a fix nobody performed, so these legs perform it: the committed corpus is
# COPIED into this phase's own temp dir (the fixtures themselves are never edited), the filename, the directory
# and the attribute name are needles OUT OF THE PRINTED SENTENCE, the edit goes to the path that sentence
# named, and the refusal is required to clear. Then the same row goes to each decoy and the refusal is required
# to survive. Needling the target out of the message is what stops the test drifting from the prose: rename the
# file the cure names and leg (a) edits a different path and fails, rather than passing beside a message that
# now points somewhere else. None of this needs a checkout, so none of it sits behind `entity-no-checkout`.
# --- C0: the copy, and the words the cure legs are going to read their instructions from ----------
CURE="$TMP/cure"
mkdir -p "$CURE"
cp -R "$FIX/inputs" "$CURE/"
WS="$CURE/inputs/templates/terra_workspaces"
runsc C0 "${BASE[@]}" "$PY" "$TOOL" --tree "$CURE"
wantf "C0: the copy refuses exactly what the corpus refuses, decoys included — this is the fixture, moved" C0 1 \
    "UNRESOLVED-VALUE ZeroNameKeyNoThis.ref_fasta [no-oracle]" \
    "GSVTK-ORACLE flavor=alpha file=- attrs=0 status=ABSENT" \
    "alpha/workflow_configurations/workspace.tsv.tmpl, alpha/workspace_other.tsv.tmpl"
cure_ref="$(grep 'UNRESOLVED-VALUE ZeroNameKeyNoThis\.ref_fasta \[no-oracle\]' "$TMP/C0.out" | head -1)"
cure_name="$(printf '%s\n' "$cure_ref" | sed -n 's/.*reads `\([^`]*\)`.*/\1/p')"
cure_file="$(printf '%s\n' "$cure_ref" | sed -n 's/.*a file to CREATE: `\([^`]*\)`.*/\1/p')"
cure_diag="$(printf '%s\n' "$cure_ref" | sed -n 's/.*; `\([^`]*\)` is status=ABSENT.*/\1/p')"
cure_dir="$(printf '%s\n' "$cure_ref" | sed -n 's/.*`, in \([^,]*\), the directory that flavor.*/\1/p')"
wanteq "C0: every needle found a string — an empty extraction would make the equality below vacuously true" \
    "$([ -n "$cure_name" ] && [ -n "$cure_file" ] && [ -n "$cure_dir" ] && echo all-three)" "all-three"
wanteq "C: the name the sentence tells you to declare is the name the binding read" "$cure_name" "reference_fasta"
wanteq "C: the file the cure names is the file the diagnosis named — one variable, printed twice, not a rebuilt path" \
    "$cure_file" "$cure_diag"
wanteq "C: and the directory it names is the directory that very path sits in" "$cure_dir" "$(dirname "$cure_file")"
wantf "C: the ABSENT refusal says the cure is a file to CREATE (nothing to edit) in the directory above, and relabels what it ships as what EXISTS" C0 1 \
    "there is nothing to edit and a file to CREATE: \`$cure_file\`, in $cure_dir, the directory that flavor's configs live in" \
    "a leading \`workspace:\` on that field is optional" \
    "What that flavor ships today is what EXISTS, not what to edit"
# --- C1/C2 (b): the decoys. The same row, where a reader skimming that list would put it ----------
printf '%s\t{{ reference_resources.%s }}\n' "$cure_name" "$cure_name" >> "$WS/alpha/workspace_other.tsv.tmpl"
wanteq "C1: the near-miss decoy really carries the row — the edit landed, so the refusal below has a cause" \
    "$(grep -c "$cure_name" "$WS/alpha/workspace_other.tsv.tmpl")" 1
runsc C1 "${BASE[@]}" "$PY" "$TOOL" --tree "$CURE"
wantf "C1(b): adding the row to the NEAR-MISS name does not clear it, and alpha's oracle is still ABSENT" C1 1 \
    "UNRESOLVED-VALUE ZeroNameKeyNoThis.ref_fasta [no-oracle]" \
    "GSVTK-ORACLE flavor=alpha file=- attrs=0 status=ABSENT"
printf '%s\t{{ reference_resources.%s }}\n' "$cure_name" "$cure_name" >> "$WS/alpha/workflow_configurations/workspace.tsv.tmpl"
runsc C2 "${BASE[@]}" "$PY" "$TOOL" --tree "$CURE"
wantf "C2(b): and neither does adding it to the file with the oracle's EXACT name in the wrong directory" C2 1 \
    "UNRESOLVED-VALUE ZeroNameKeyNoThis.ref_fasta [no-oracle]" \
    "1 of them carries the oracle's own name but is not the flavor's table: alpha/workflow_configurations/workspace.tsv.tmpl"
# --- C3/C4 (a): the cure, applied to the path the sentence named ----------------------------------
printf '%s\t{{ reference_resources.%s }}\n' "$cure_name" "$cure_name" > "$WS/$cure_file"
runsc C3 "${BASE[@]}" "$PY" "$TOOL" --tree "$CURE"
wantabsent "C3(a): THE CURE — one row in the file the sentence named, and that value's refusal is gone" C3 \
    'UNRESOLVED-VALUE ZeroNameKeyNoThis\.ref_fasta'
wantf "C3(a): the path it named is now that flavor's oracle, read and counted" C3 1 \
    "GSVTK-ORACLE flavor=alpha file=$cure_file attrs=1 status=OK"
wantf "C3(a): the value changed class and state, on a run where BOTH decoys still carry the same row" C3 1 \
    "GSVTK-VALUE config=alpha/workflow_configurations/ZeroNameKeyNoThis.json.tmpl flavor=alpha wf=ZeroNameKeyNoThis bindings=2 workspace-attr=1 workspace-missing=0 entity=0 literal=0 needs-bundle=0 test-bundle=0 docker=1 mixed=0 no-oracle=0 nested-reads=0 workspace-reads=2 workspace-resolved-reads=1 workspace-missing-reads=1 no-oracle-reads=0 bundles=- dropped-values=0 dropped-workspace-reads=0 unresolved=0 state=OK reasons=-"
wanteq "C3: both decoys still hold the row this lane appended — nothing was tidied away to manufacture the pass" \
    "$(grep -c "$cure_name" "$WS/alpha/workspace_other.tsv.tmpl" \
        "$WS/alpha/workflow_configurations/workspace.tsv.tmpl" | awk -F: '{ s += $2 } END { print s }')" 2
printf 'workspace:%s\t{{ reference_resources.%s }}\n' "$cure_name" "$cure_name" > "$WS/$cure_file"
runsc C4 "${BASE[@]}" "$PY" "$TOOL" --tree "$CURE"
wantabsent "C4: the \`workspace:\` prefix the cure calls optional really is — prefixed, the same row still clears" C4 \
    'UNRESOLVED-VALUE ZeroNameKeyNoThis\.ref_fasta'
# --- C5: the ADD arm in a flavor whose table EXISTS, and the `[mixed]` sentence that inherits it ----
# `workspace-missing` and `mixed-workspace-missing` print the SAME clause from the SAME code path, so both
# filenames and both attribute names are needles here: if the mixed arm ever restated the cure with its own
# path, or lost the file the diagnosis named, the pair below stops being equal.
mixed_ref="$(grep 'UNRESOLVED-VALUE GATKSVPipelineSynth\.args \[mixed\]' "$TMP/C0.out" | head -1)"
miss_ref="$(grep 'UNRESOLVED-VALUE GATKSVPipelineSynth\.cutoffs \[workspace-missing\]' "$TMP/C0.out" | head -1)"
mixed_name="$(printf '%s\n' "$mixed_ref" | sed -n 's/.*args reads `\([^`]*\)` and.*/\1/p')"
miss_name="$(printf '%s\n' "$miss_ref" | sed -n 's/.*cutoffs reads `\([^`]*\)` and.*/\1/p')"
mixed_file="$(printf '%s\n' "$mixed_ref" | sed -n 's/.*quoted above to `\([^`]*\)`.*/\1/p')"
miss_file="$(printf '%s\n' "$miss_ref" | sed -n 's/.*quoted above to `\([^`]*\)`.*/\1/p')"
wanteq "C5: both sentences named a table, so the equality below is two strings and not two empties" \
    "$([ -n "$mixed_file" ] && [ -n "$miss_file" ] && [ -n "$mixed_name" ] && echo all-three)" "all-three"
wanteq "C5: the [mixed] sentence and the [workspace-missing] sentence name ONE table — inherited, not restated" \
    "$mixed_file" "$miss_file"
countis "C5: three configs refuse under \`mixed-workspace-missing\` before the cure" C0 \
    '^GSVTK-VALUE config=.* reasons=mixed-workspace-missing$' 3
printf '%s\t{{ reference_resources.%s }}\n%s\t{{ reference_resources.%s }}\n' \
    "$mixed_name" "$mixed_name" "$miss_name" "$miss_name" >> "$WS/$miss_file"
runsc C5 "${BASE[@]}" "$PY" "$TOOL" --tree "$CURE"
wantabsent "C5(a): the two rows the sentences named clear the whole-value refusal AND both [mixed] ones" C5 \
    'UNRESOLVED-VALUE GATKSVPipelineSynth\.(cutoffs|args)'
wantf "C5(a): the name no sentence quoted still refuses, and says so with the same ADD clause — the cure is per name" C5 1 \
    "UNRESOLVED-VALUE MixedWorkspaceMissing.inputs [mixed]" \
    "To clear it: add a row whose FIRST field is each name quoted above to \`beta/workspace.tsv.tmpl\`"
countis "C5: so the count falls 3 -> 1, one undeclared name left in that flavor" C5 \
    '^GSVTK-VALUE config=.* reasons=mixed-workspace-missing$' 1
# --- C6/C7: the two cures that are NOT an add, run — collapse under DUPE, repair under MALFORMED ----
# Both clauses name the file the finding was about, and both say what to do to the ROWS, which is a different
# instruction from "add a row" — the point of conditioning the cure on the printed state. Running them shows
# the flip from the other side: the TABLE goes clean, and the value keeps refusing under its own other word
# because the NAME is a separate fact. A refusal that promised "add a row" under DUPE would be advice about the
# wrong defect; these say what was done, and the word that follows is the one for what is left.
# epsilon's DUPE is `gatk_docker` on rows 1 and 3 (V5 pins it), so "collapse the row(s) quoted above … to one
# row per attribute" is: drop the later one.
sed -e '3d' "$WS/epsilon/workspace.tsv.tmpl" > "$WS/epsilon/workspace.tsv.tmpl.c" \
    && mv "$WS/epsilon/workspace.tsv.tmpl.c" "$WS/epsilon/workspace.tsv.tmpl"
runsc C6 "${BASE[@]}" "$PY" "$TOOL" --tree "$CURE"
wantf "C6: collapsing the duplicated row in the file the DUPE sentence named makes that flavor clean" C6 1 \
    "GSVTK-ORACLE flavor=epsilon file=epsilon/workspace.tsv.tmpl attrs=2 status=OK"
wantf "C6: and the value's refusal changes WORD, because the name is still undeclared — the cure was for the state" C6 1 \
    "UNRESOLVED-VALUE EpsilonValues.cohort_ped_file [workspace-missing] EpsilonValues.cohort_ped_file reads \`cohort_ped_file\` and \`epsilon/workspace.tsv.tmpl\` does not declare it" \
    "To clear it: add a row whose FIRST field is each name quoted above to \`epsilon/workspace.tsv.tmpl\`"
wantabsent "C6: the DUPE refusal is gone, so the collapse is what moved, not a coincidence of counting" C6 \
    'UNRESOLVED-VALUE EpsilonValues\.cohort_ped_file \[no-oracle\]'
# gamma's three MALFORMED findings are rows 6, 7 and 8 and its DUPE is rows 2 and 3 (W5 pins all four against
# this fixture), so "repair the row(s) quoted above" is these four lines: delete the duplicate's second row and
# the three rows the findings quote.
sed -e '3d' -e '6d' -e '7d' -e '8d' "$WS/gamma/workspace.tsv.tmpl" > "$WS/gamma/workspace.tsv.tmpl.c" \
    && mv "$WS/gamma/workspace.tsv.tmpl.c" "$WS/gamma/workspace.tsv.tmpl"
runsc C7 "${BASE[@]}" "$PY" "$TOOL" --tree "$CURE"
wantf "C7: repairing exactly the rows the MALFORMED findings quoted leaves that flavor clean" C7 1 \
    "GSVTK-ORACLE flavor=gamma file=gamma/workspace.tsv.tmpl attrs=4 status=OK"
wantf "C7: and its refusal becomes the ADD word for the name it never declared, naming the same file" C7 1 \
    "UNRESOLVED-VALUE GammaValues.cohort_ped_file [workspace-missing]" \
    "To clear it: add a row whose FIRST field is each name quoted above to \`gamma/workspace.tsv.tmpl\`"
wantabsent "C7: no MALFORMED finding and no \`no-oracle\` refusal survives the repair" C7 \
    '^ *MALFORMED row [678]:|UNRESOLVED-VALUE GammaValues\.cohort_ped_file \[no-oracle\]'
printf 'cohort_ped_file\t{{ reference_resources.cohort_ped_file }}\n' >> "$WS/gamma/workspace.tsv.tmpl"
runsc C8 "${BASE[@]}" "$PY" "$TOOL" --tree "$CURE"
wantabsent "C8: then the ADD clause the repaired file's own sentence prints clears it — the whole ladder, run" C8 \
    'UNRESOLVED-VALUE GammaValues\.cohort_ped_file'

# ===============================================================================================
# THE REQUIRED-INPUT ACCEPTANCE (Q): the criterion docs/plan-launch-any-module.md carried as un-built.
#
# Stage B's acceptance says: EVERY required input of EVERY workflow a launch config binds is either given a
# value by that config or named by a workspace attribute the flavor's own table declares. Until now nothing
# printed it, because the required-input set lives in `checks/wdl_inputs_check.py` (miniwdl's AST) and the
# oracle lives in `checks/terra_entity_check.py`; the placement ruled for the second, which reads `wdl/` as a
# SECOND subtree of the same ref and reuses the other checker's predicate and loader by IMPORT (a copied
# `"optional" in value` is the mistake row C8 of docs/gap-ledger.md records). This block grades:
#
#   * `GSVTK-REQUIRED` per config and `GSVTK-REQUIRED-SUMMARY` once, with three identities a reader can
#     check against the line itself: `required=` is the sum of the class fields, and `configs=` is the sum of
#     the state counters AND the sum of the capture-word counters;
#   * the value CLASS of the binding that carries each required input, taken from the value layer's own
#     verdict on those bytes (`class_of`), so the two layers cannot disagree about one document;
#   * the states that are NOT verdicts — `NOT-CAPTURED` with its cause word (`NO-MINIWDL`, `NO-WDL-DIR`,
#     `NO-DOCUMENT`, `LOAD-FAILURE`, `NO-WORKFLOW`, `NO-WDL`) — counted on `GSVTK-ENTITY-UNCHECKED` so a
#     green run cannot be read as "every required input is bound";
#   * the refusal precedence: a finding is never hidden by a disclosure (`AcceptPair`), and an input the
#     config hands a whole object is `container`, never a false `unbound` (`AcceptWhole`).
#
# FIXTURES: `scripts/selftest.d/fixtures/entity-required/` — one flavor, one entity table, one workspace
# table, six configs, six WDLs (five of them asked for; `AcceptPairB.wdl` is deliberately NOT shipped, which
# is what makes `NO-WDL` reachable without editing anything). Every config passes the ENTITY layer and the
# VALUE layer on purpose: the required layer is then the only thing that can move the exit code, which is
# what the Q-cure pair grades (bind the two missing keys the sentences name and the corpus goes 1 -> 0).
# The main fixture corpus ships ONE WDL (`wdl/GATKSVPipelineSynth.wdl`) and no others: it must keep showing
# what the tool prints when a tree has no WDLs behind most of its configs.
#
# miniwdl is a dev dependency, so every EXACT pin in this block is behind the `entity-no-miniwdl` region and
# a machine without it gets a NAMED, COUNTED skip. The opposite arm is not left unrun: `Qblind` re-runs the
# corpus on an interpreter whose `WDL` import is replaced by a stub that raises, so `NO-MINIWDL` is EXECUTED
# on machines that have miniwdl too. The legs that hold in either mode (line counts, the identities, the
# census fields, "an uncaptured config never reads as OK") sit outside the region.
#
# --- Q0: the acceptance corpus ---------------------------------------------------------------
runsc Q0 "${BASE[@]}" "$PY" "$TOOL" --tree "$FIXREQ"
countis "Q0: one required line per config of the acceptance corpus, none swallowed" Q0 '^GSVTK-REQUIRED config=' 6
countis "Q0: exactly one required census line" Q0 '^GSVTK-REQUIRED-SUMMARY ' 1
wantline "Q0: the run says which predicate it reused and which WDL tree it opened" Q0 \
    '^required inputs: checks/wdl_inputs_check.py .* WDL tree '
# An uncaptured config must never read as a verdict it did not earn — in EITHER mode, which is why this is
# outside the miniwdl region.
wantabsent "Q0: a config whose workflow was not read never says state=OK" Q0 \
    '^GSVTK-REQUIRED .* capture=(NO-MINIWDL|NO-WDL-DIR|NO-DOCUMENT|LOAD-FAILURE|NO-WORKFLOW|NO-WDL) required=[0-9]+ .* state=OK '
wantline "Q0: the scope line carries the two required fields, in the units of the corpus" Q0 \
    '^GSVTK-ENTITY-UNCHECKED .* required-configs-uncaptured=[0-9]+ required-uncaptured-because=[A-Za-z-]+ root-entity-type=never-read$'
# THE THREE IDENTITIES, read off the census line itself. The field names come from the tool's own tuples
# (one spelling of the vocabulary), not from a second list typed here, so a class added to `REQUIRED_CLASSES`
# without its field skews a number instead of coexisting with it.
qfields="$("$PY" -c 'import sys; sys.path.insert(0, "checks")
import terra_entity_check as t
print("%s|%s|%s" % (" ".join(t.REQUIRED_CLASSES), " ".join(t.REQUIRED_STATES), " ".join(t.REQUIRED_CAPTURES)))' \
      2>/dev/null | tail -1)"
qclasses="${qfields%%|*}"; qrest="${qfields#*|}"; qstates="${qrest%%|*}"; qcaptures="${qrest#*|}"
wanteq "Q0: the identity legs have field names to work with — an empty list would make all three vacuous" \
    "$([ -n "$qclasses" ] && [ -n "$qstates" ] && [ -n "$qcaptures" ] && echo all-three)" "all-three"
qcensus="$(grep '^GSVTK-REQUIRED-SUMMARY ' "$TMP/Q0.out" | head -1)"
qfield() { printf '%s\n' "$qcensus" | awk -v k="$1" '{for (i = 1; i <= NF; i++) {split($i, kv, "="); if (kv[1] == k) print kv[2]}}'; }
qsumf() { printf '%s\n' "$qcensus" | awk -v names="$1" \
    'BEGIN { n = split(names, a, " ") } { for (i = 1; i <= NF; i++) { split($i, kv, "="); for (j = 1; j <= n; j++) if (kv[1] == a[j]) s += kv[2] } } END { print s + 0 }'; }
wanteq "Q0: required= IS the sum of the class fields, on the census line" \
    "$(qfield required)" "$(qsumf "$qclasses")"
# The state counter NAMES are spelled here and not derived from `REQUIRED_STATES`: the state is called
# `UNRESOLVED` while its counter counts CONFIGS and is called `configs-unsatisfied`, and coining one field
# name to reconcile the two would be the invention this block exists to avoid. A renamed field makes the sum
# short, which is red, not silently smaller. The capture counters ARE a pure function of that tuple
# (`NO-WDL-DIR` -> `capture-no-wdl-dir`), so that list stays derived: a capture word added without its counter
# skews this sum instead of coexisting with it.
wanteq "Q0: configs= IS the sum of the state counters, spelled as this line prints them" \
    "$(qfield configs)" "$(qsumf "configs-ok configs-unsatisfied configs-not-captured")"
wanteq "Q0: all three state counters are ON the line, so that sum cannot be one field short" \
    "$([ -n "$(qfield configs-ok)" ] && [ -n "$(qfield configs-unsatisfied)" ] && [ -n "$(qfield configs-not-captured)" ] && echo three)" "three"
wanteq "Q0: configs= IS the sum of the capture-word counters" \
    "$(qfield configs)" "$(qsumf "$(printf '%s\n' "$qcaptures" | tr 'A-Z' 'a-z' | tr ' ' '\n' | sed 's/^/capture-/' | tr '\n' ' ')")"
# Per-config, the same identity on every line — one violating line is enough to make this red.
qbad="$(grep '^GSVTK-REQUIRED config=' "$TMP/Q0.out" | awk -v names="$qclasses" \
    'BEGIN { n = split(names, a, " ") } { r = -1; s = 0; for (i = 1; i <= NF; i++) { split($i, kv, "=");
      if (kv[1] == "required") r = kv[2]; for (j = 1; j <= n; j++) if (kv[1] == a[j]) s += kv[2] }
      if (r != s) c++ } END { print c + 0 }')"
wanteq "Q0: every per-config line also has required= == sum of its class fields" "$qbad" 0
# The required layer must not touch what the other two layers say about the same bytes. Two runs of ONE
# corpus, differing only in whether `wdl/` is in the tree: the entity and value censuses must be identical.
QCOPY="$TMP/q-nowdl"
mkdir -p "$QCOPY"
cp -R "$FIXREQ/." "$QCOPY/" && rm -rf "$QCOPY/wdl"
runsc Qnowdl "${BASE[@]}" "$PY" "$TOOL" --tree "$QCOPY"
wanteq "Q0: deleting the WDL subtree moves no value-layer number — the census, identical across the two runs" \
    "$(grep '^GSVTK-VALUE-SUMMARY ' "$TMP/Q0.out" | head -1)" \
    "$(grep '^GSVTK-VALUE-SUMMARY ' "$TMP/Qnowdl.out" | head -1)"
wanteq "Q0: and no entity number either" \
    "$(grep '^GSVTK-ENTITY-SUMMARY ' "$TMP/Q0.out" | head -1)" \
    "$(grep '^GSVTK-ENTITY-SUMMARY ' "$TMP/Qnowdl.out" | head -1)"
countis "Q0: with no wdl/ at all every config still earns its required line" Qnowdl '^GSVTK-REQUIRED config=' 6
wantline "Q0: and the scope line still counts them, whichever cause the interpreter's miniwdl left it with" Qnowdl \
    '^GSVTK-ENTITY-UNCHECKED .* required-configs-uncaptured=6 required-uncaptured-because=(NO-WDL-DIR|NO-MINIWDL) '
# --- Qblind: the NO-MINIWDL arm, RUN, not asserted ---------------------------------------------
# An interpreter whose `WDL` is a file that raises is the only way to execute this arm on the machines that
# HAVE miniwdl (which is where the tool's own skip sentence would otherwise never be read). The stub sits on
# PYTHONPATH, ahead of site-packages; nothing else in the tool imports `WDL` at module level, which is the
# laziness `checks/wdl_inputs_check.py` documents.
mkdir -p "$TMP/no-miniwdl"
printf 'raise ImportError("GSVTK selftest stub: no miniwdl in this run")\n' > "$TMP/no-miniwdl/WDL.py"
runsc Qblind "${BASE[@]}" PYTHONPATH="$TMP/no-miniwdl" "$PY" "$TOOL" --tree "$FIXREQ"
wantf "Qblind: miniwdl missing is NAMED, not swallowed: the run says what it could not import" Qblind 0 \
    "miniwdl is not importable (GSVTK selftest stub: no miniwdl in this run)"
countis "Qblind: every config says NO-MINIWDL, because no WDL anywhere was readable" Qblind \
    '^GSVTK-REQUIRED .* capture=NO-MINIWDL .* state=NOT-CAPTURED ' 6
wantf "Qblind: the census attributes all of them to that one cause" Qblind 0 \
    "GSVTK-REQUIRED-SUMMARY configs=6 required=0 " \
    "capture-no-miniwdl=6 capture-no-wdl-dir=0 capture-no-document=0 " \
    "capture-load-failure=0 capture-no-workflow=0 capture-no-wdl=0 capture-ok=0" \
    "GSVTK-ENTITY-UNCHECKED configs=6 member-attr-reads=0 configs-with-member-attrs=0 " \
    "workspace-binding-reads=0 configs-with-workspace-reads=0 workspace-attribute-values-unread=1 " \
    "configs-with-unclassified-values=0 required-configs-uncaptured=6 required-uncaptured-because=NO-MINIWDL"
wantf "Qblind: a green run says so IN THE SAME BREATH — exit 0 with the whole corpus uncaptured" Qblind 0 \
    "REQUIRED-NOT-CAPTURED 6 of 6 config(s), cause NO-MINIWDL"
wantabsent "Qblind: and nothing is reported as captured, so no count can be read as coverage" Qblind \
    '^GSVTK-REQUIRED .* capture=OK '
if [ "$HAVE_MINIWDL" = yes ]; then
    gsvtk_guard_sites "entity-no-miniwdl"     # the exact pins the NO-MINIWDL stub-run above cannot stand in for
    # --- Q1: the pass, and what it is a pass ABOUT ----------------------------------------------
    wantf "Q1: THE PASS — four required inputs, four classes, none of them refused" Q0 1 \
        'GSVTK-REQUIRED config=acc/AcceptAll.json.tmpl flavor=acc wf=AcceptAll workflows=1 capture=OK required=4 workspace-attr=0 workspace-missing=0 entity=1 literal=1 needs-bundle=1 test-bundle=0 docker=1 mixed=0 no-oracle=0 container=0 unbound=0 files=wdl/AcceptAll.wdl state=OK reason=-' \
        "1 required input(s) carry a \`{{ … }}\` render"
    wantf "Q1: an image and a render are DISCLOSED beside a pass, not satisfied and not refused" Q0 1 \
        "1 required input(s) are container images: excluded by rule from anything this tool resolves" \
        "a launch that never states the image still cannot run" \
        "which is why an unstated bundle is disclosed (exit 0) and never quietly satisfied"
    wantabsent "Q1: the pass owes its OK to nothing withheld — the corpus still refuses elsewhere" Q0 \
        'config=acc/AcceptAll.json.tmpl .* state=UNRESOLVED'
    # --- Q2: the refusal this layer exists to print, and that nothing else prints ---------------
    wantf "Q2: a required input the config does not name refuses, and the reason says so" Q0 1 \
        'GSVTK-REQUIRED config=acc/AcceptUnbound.json.tmpl flavor=acc wf=AcceptUnbound workflows=1 capture=OK required=1 workspace-attr=0 workspace-missing=0 entity=0 literal=0 needs-bundle=0 test-bundle=0 docker=0 mixed=0 no-oracle=0 container=0 unbound=1 files=wdl/AcceptUnbound.wdl state=UNRESOLVED reason=required-unbound'
    wantline "Q2: THE FALSIFICATION — that same config is state=OK on the entity layer" Q0 \
        '^GSVTK-ENTITY config=acc/AcceptUnbound\.json\.tmpl .* state=OK .* reason=-$'
    wantline "Q2: and state=OK on the value layer, so this refusal is NEW, not one restated" Q0 \
        '^GSVTK-VALUE config=acc/AcceptUnbound\.json\.tmpl .* unresolved=0 state=OK reasons=-$'
    wantf "Q2: and the finding names the declaration, its line, the config, and the file the cure belongs in" Q0 1 \
        "UNRESOLVED-REQUIRED AcceptUnbound.sample_id [unbound] AcceptUnbound.sample_id (String) is declared with no default and not optional at \`wdl/AcceptUnbound.wdl:11\`, and \`acc/AcceptUnbound.json.tmpl\` binds no key for it" \
        "Cromwell discovers this at input-resolution time, after the VMs booted"
    wantline 'Q2: a struct handed over as one object is `container` — bound, not the false refusal' Q0 \
        '^GSVTK-REQUIRED config=acc/AcceptWhole\.json\.tmpl .* container=1 unbound=0 .* state=OK reason=-$'
    # --- Q3: the states that are not verdicts ---------------------------------------------------
    wantline "Q3: a WDL that will not load is LOAD-FAILURE, with no count that could read as coverage" Q0 \
        '^GSVTK-REQUIRED config=acc/AcceptBroken\.json\.tmpl .* capture=LOAD-FAILURE required=0 .* files=- state=NOT-CAPTURED reason=-$'
    wantf "Q3: and the line carries miniwdl's own words, not a paraphrase of them" Q0 1 \
        'REQUIRED-NOT-CAPTURED AcceptBroken [LOAD-FAILURE] SyntaxError:'
    wantline "Q3: a file that declares tasks only is NO-WORKFLOW — the file exists and binds nothing" Q0 \
        '^GSVTK-REQUIRED config=acc/AcceptTasksOnly\.json\.tmpl .* capture=NO-WORKFLOW required=0 .* files=- state=NOT-CAPTURED reason=-$'
    wantf "Q3: and it is said APART from NO-WDL, because empty and absent are different answers" Q0 1 \
        'REQUIRED-NOT-CAPTURED AcceptTasksOnly [NO-WORKFLOW] AcceptTasksOnly.wdl declares tasks only: there is nothing to bind'
    wantf "Q3: two workflows behind one config: the finding is NOT hidden by the uncaptured workflow" Q0 1 \
        'GSVTK-REQUIRED config=acc/AcceptPair.json.tmpl flavor=acc wf=AcceptPairA,AcceptPairB workflows=2 capture=NO-WDL required=1 workspace-attr=0 workspace-missing=0 entity=0 literal=0 needs-bundle=0 test-bundle=0 docker=0 mixed=0 no-oracle=0 container=0 unbound=1 files=wdl/AcceptPairA.wdl state=UNRESOLVED reason=required-unbound'
    wantf "Q3: the census the acceptance corpus was built to print, field by field" Q0 1 \
        "GSVTK-REQUIRED-SUMMARY configs=6 required=7 workspace-attr=0 workspace-missing=0 entity=1 literal=1 " \
        "needs-bundle=1 test-bundle=0 docker=1 mixed=0 no-oracle=0 container=1 unbound=2 workflows=7 " \
        "workflows-captured=4 configs-ok=2 configs-unsatisfied=2 configs-not-captured=2 " \
        "capture-no-miniwdl=0 capture-no-wdl-dir=0 capture-no-document=0 capture-load-failure=1 " \
        "capture-no-workflow=1 capture-no-wdl=1 capture-ok=3" \
        "GSVTK-ENTITY-UNCHECKED configs=6 member-attr-reads=0 configs-with-member-attrs=0 " \
        "workspace-binding-reads=0 configs-with-workspace-reads=0 workspace-attribute-values-unread=1 " \
        "configs-with-unclassified-values=0 required-configs-uncaptured=3 required-uncaptured-because=mixed " \
        "root-entity-type=never-read"
    wantf "Q3: the banner states the tree-level cause ONCE, names the workflows, and caps the list by count" Q0 1 \
        "REQUIRED-NOT-CAPTURED 3 of 6 config(s), cause mixed (3 workflow(s) not read: AcceptBroken, " \
        "AcceptPairB, AcceptTasksOnly):"
    countis "Q3: the per-config uncaptured lines are the two causes that ARE per-config" Q0 \
        '^        REQUIRED-NOT-CAPTURED ' 2
    wantabsent "Q3: NO-WDL is not repeated per config after the banner said it (35 configs, one sentence)" Q0 \
        '^ +REQUIRED-NOT-CAPTURED AcceptPairB'
    # NO-WDL-DIR moves NO number and refuses nothing — the required question was simply never asked — so the
    # exit is 0 and the disclosure is carried by the census, the scope line, the banner and the header alone.
    wantf "Q3: with no wdl/ in the tree the cause is the missing subtree, and every config says it" Qnowdl 0 \
        "GSVTK-REQUIRED-SUMMARY configs=6 required=0 " \
        "capture-no-miniwdl=0 capture-no-wdl-dir=6 capture-no-document=0 capture-load-failure=0 " \
        "capture-no-workflow=0 capture-no-wdl=0 capture-ok=0" \
        "required-configs-uncaptured=6 required-uncaptured-because=NO-WDL-DIR"
    wantline "Q3: the provenance line says the directory is NOT there — a bare path would read as opened" Qnowdl \
        '^required inputs: .* WDL tree .*/wdl \(no wdl/ directory here\)$'
    # --- Q4: the cure, run. The sentence names a key and a file; the leg edits that file ---------
    CURED="$TMP/q-cure"
    mkdir -p "$CURED"
    cp -R "$FIXREQ/." "$CURED/"
    for qkey in AcceptUnbound.sample_id AcceptPairA.unbound_key; do
        qline="$(grep "UNRESOLVED-REQUIRED ${qkey} \[unbound\]" "$TMP/Q0.out" | head -1)"
        qbind="$(printf '%s\n' "$qline" | sed -nE 's/.*To clear it: bind "([^"]*)" in `([^`]*)`.*/\1/p')"
        qfile="$(printf '%s\n' "$qline" | sed -nE 's/.*To clear it: bind "([^"]*)" in `([^`]*)`.*/\2/p')"
        # Checked BEFORE the tree is touched: a broken extraction would otherwise delete a fixture's shape
        # silently and print a cure that cured nothing.
        wanteq "Q4: the sentence for $qkey names a key and a file to put it in — not two empties" \
            "$([ -n "$qbind" ] && [ -n "$qfile" ] && echo both)" "both"
        wanteq "Q4: the key the cure names is the key the finding is about" "$qbind" "$qkey"
        wanteq "Q4: and the file it names is a config of this corpus, not a path out of it" \
            "$([ -f "$CURED/inputs/templates/terra_workspaces/$qfile" ] && echo inplace)" "inplace"
        awk -v k="$qbind" 'NR == 1 { print; printf "  \"%s\": \"synthetic/cured.bed\",\n", k; next } { print }' \
            "$CURED/inputs/templates/terra_workspaces/$qfile" > "$CURED/inputs/templates/terra_workspaces/$qfile.tmp" \
            && mv "$CURED/inputs/templates/terra_workspaces/$qfile.tmp" \
                  "$CURED/inputs/templates/terra_workspaces/$qfile"
    done
    runsc Q4 "${BASE[@]}" "$PY" "$TOOL" --tree "$CURED"
    wantf "Q4: THE CURE — bind the two keys the sentences named and the corpus stops refusing" Q4 0 \
        "configs-ok=3 configs-unsatisfied=0 configs-not-captured=3" "unbound=0"
    wantabsent "Q4: and no required finding survives — the refusals were the bindings, not the fixture" Q4 \
        'UNRESOLVED-REQUIRED '
    wantline "Q4: what DOES survive is the three uncaptured arms, said as scope and not as a verdict" Q4 \
        '^GSVTK-ENTITY-UNCHECKED .* required-configs-uncaptured=3 required-uncaptured-because=mixed '
    wantf "Q4: curing a finding does not promote the config whose OTHER workflow was never read" Q4 0 \
        'GSVTK-REQUIRED config=acc/AcceptPair.json.tmpl flavor=acc wf=AcceptPairA,AcceptPairB workflows=2 capture=NO-WDL required=1 workspace-attr=0 workspace-missing=0 entity=0 literal=1 needs-bundle=0 test-bundle=0 docker=0 mixed=0 no-oracle=0 container=0 unbound=0 files=wdl/AcceptPairA.wdl state=NOT-CAPTURED reason=-'
    # The same leg from the other side: a key bound to a name the flavor does NOT declare must NOT clear the
    # required refusal — it changes its WORD. A layer that only asked "is the key present?" would pass this.
    WORD="$TMP/q-word"
    mkdir -p "$WORD"
    cp -R "$FIXREQ/." "$WORD/"
    awk 'NR == 1 { print; print "  \"AcceptUnbound.sample_id\": \"${workspace.not_declared_anywhere}\","; next } { print }' \
        "$WORD/inputs/templates/terra_workspaces/acc/AcceptUnbound.json.tmpl" \
        > "$WORD/inputs/templates/terra_workspaces/acc/AcceptUnbound.json.tmpl.tmp" \
        && mv "$WORD/inputs/templates/terra_workspaces/acc/AcceptUnbound.json.tmpl.tmp" \
              "$WORD/inputs/templates/terra_workspaces/acc/AcceptUnbound.json.tmpl"
    runsc Q4w "${BASE[@]}" "$PY" "$TOOL" --tree "$WORD"
    wantline "Q4w: binding the key to an UNDECLARED attribute changes the WORD, it does not clear the finding" Q4w \
        '^GSVTK-REQUIRED config=acc/AcceptUnbound\.json\.tmpl .* workspace-missing=1 .* unbound=0 .* state=UNRESOLVED reason=required-workspace-missing$'
    wantf "Q4w: and the finding says the value carrying it is the one the value layer refused above" Q4w 1 \
        "UNRESOLVED-REQUIRED AcceptUnbound.sample_id [workspace-missing]" \
        "is a REQUIRED input — declared at \`wdl/AcceptUnbound.wdl:11\`"
    wantline "Q4w: curing one key does not clear the other config's refusal — no blanket green" Q4w \
        '^GSVTK-REQUIRED config=acc/AcceptPair\.json\.tmpl .* unbound=1 .* state=UNRESOLVED reason=required-unbound$'
    gsvtk_guard_sites_end "entity-no-miniwdl"
else
    # No count typed here: the region's extent, and how many sites it withheld, is printed by the
    # declared/reached pair below (`withheld by the declared skip region entity-no-miniwdl: …`), which reads
    # both from this file's own markers. Two regions can withhold at once on a CI box (no checkout AND no
    # miniwdl), and `DECLARED - reached` would attribute both totals to whichever line printed last.
    printf '  SKIP  entity: %s cannot import miniwdl, so the required-input pins above (the exact capture words, the refusal and its cure, and the NO-WDL-DIR arm) did not run — the Qblind legs DID run the NO-MINIWDL arm, and the pair below names the sites this skip withheld\n' "$PY"
    skipped=$((skipped + 1))
    gsvtk_note_skip "entity-no-miniwdl"
fi

# ===============================================================================================
# The optional cross-check against a real gatk-sv checkout, at a PINNED ref.
#
# Pinned because these assertions are claims about a specific tree (the census the plan carries), and an
# unpinned claim about somebody's checkout fails on a machine whose checkout moved instead of reporting a
# finding. Absent checkout or absent commit => a NAMED, COUNTED skip, which is the CI case. Every assertion in
# this block is a call site of a recorder, which is how the tally block below can name how many of them the skip
# withheld — the ledger of reached sites is written by the recorders, not by a grep over these names.
if [ -n "$CK" ] && [ -d "$CK/.git" ] \
   && git -C "$CK" rev-parse --verify --quiet "${PINNED}^{commit}" >/dev/null 2>&1; then
    gsvtk_guard_sites "entity-no-checkout"      # the real-tree block the SKIP below withholds
    runsc R1 "${BASE[@]}" "$PY" "$TOOL" --repo "$CK" --ref "$PINNED"
    wantf "R1: the real corpus runs (findings exist at this ref)" R1 1 \
        "shipped tables: 5 entity/membership TSV(s)" \
        "cohort_mode/sample_set_membership_1kgp.tsv.tmpl membership:sample_set_id  columns: sample" \
        "single_sample/sample.tsv.tmpl"
    # WHICH LISTER RAN, read off the run rather than off the branch. `main` gets the shipped test profile stems
    # two ways — a directory walk for `--tree`, `git ls-tree` for `--repo/--ref` — and the R-profiles legs below
    # call BOTH by name, so nothing in this file could tell which one the run itself used. On this ref the
    # outputs are byte-identical anyway (`test-bundle=0` there), so swapping the two branch selections was
    # undetectable from the census. The tool now says which lister it used on the `source` line it already
    # printed, and these two legs read that sentence from the real `--repo/--ref` run: the plumbing name
    # present, the filesystem name absent.
    wantf "R: the run says its shipped test profiles came from \`git ls-tree\` — the plumbing lister, on the plumbing path" R1 1 \
        "shipped test profiles from git ls-tree"
    wantabsent "R: and the filesystem lister is named nowhere on that run, so swapping the branch selection is a finding" R1 \
        "shipped test profiles from the directory listing"
    wantf "R1: the census upstream's plan carries, measured rather than quoted" R1 1 \
        "configs 31 | name keys: one 29, zero 2, many 0" \
        "GSVTK-ENTITY-SUMMARY configs=31 ok=3 no-name-key=2 many-name-keys=0 no-shipped-table=16 columns-missing=10 cannot-parse=0 namekeys-one=29 namekeys-zero=2 namekeys-many=0"
    wantf "R1: the two tables that decide the single-sample answer, with their columns" R1 1 \
        "single_sample/sample.tsv.tmpl                  entity:sample_id  columns: bam_or_cram_file, bam_or_cram_index, participant, case_stripy_file" \
        "single_sample/participant.tsv.tmpl             entity:participant_id  columns: -"
    wantline "R: MergeBatchSites answers sample_set_set on the real tree, and refuses for want of a table" R1 \
        'config=cohort_mode/workflow_configurations/MergeBatchSites.json.tmpl .* namekeys=1 namekey=sample_set_set collections=sample_sets .* reason=no-shipped-table'
    wantline "R: \${this.sample_sets.sample_set_id} is a member read, never a second name key" R1 \
        'config=cohort_mode/workflow_configurations/CombineBatches.json.tmpl .* namekeys=1 namekey=sample_set_set'
    wantline "R: the single-sample config passes on sample.tsv.tmpl and reports its duplicated key" R1 \
        'config=single_sample/GATKSVPipelineSingleSample.json.tmpl .* state=OK namekeys=1 namekey=sample .* table=single_sample/sample.tsv.tmpl .* dupkeys=GATKSVPipelineSingleSample.mei_bed'
    wantline "R: the one \${[this.a, ...]} binding in the corpus yields all five of its reads" R1 \
        'config=cohort_mode/workflow_configurations/PlotSVCountsPerSample.json.tmpl .* reads_direct=6'
    countis "R: both write-back configs refuse on zero name keys" R1 \
        '^GSVTK-ENTITY config=cohort_mode/workflow_configurations/output_configurations/.* reason=no-name-key$' 2
    countis "R: every config under the corpus at that ref earns exactly one line" R1 '^GSVTK-ENTITY config=' 31
    countis "R: nothing on that tree is reported as many-name-keys (the rule has one answer per config)" R1 \
        'state=UNRESOLVED namekeys=[2-9] ' 0
    # The oracle half of the same run. 76 and 98 are the row counts of the two `workspace.tsv.tmpl` files at
    # this ref — measured here, not quoted from the plan — and they are the numbers a later layer's refusals
    # will be counted against. `(+68 more, not listed)` is what proves the reader read every row and merely
    # declined to dump the table: the count is 76 while the list is capped.
    wantf "R: the flavor's own workspace table is the oracle, and both flavors ship one, clean" R1 1 \
        "GSVTK-ORACLE flavor=cohort_mode file=cohort_mode/workspace.tsv.tmpl attrs=76 status=OK" \
        "GSVTK-ORACLE flavor=single_sample file=single_sample/workspace.tsv.tmpl attrs=98 status=OK" \
        "GSVTK-ORACLE-SUMMARY flavors=2 ok=2 absent=0 malformed=0 dupes=0 names=174 " \
        "values=not-read enforced=yes"
    countis "R: one oracle line per flavor on that tree (flavors come from configs, not from tables)" R1 \
        '^GSVTK-ORACLE flavor=' 2
    wantf "R: 76 and 98 rows really read — the names truncate, the count does not" R1 1 \
        "cohort_mode/workspace.tsv.tmpl declares 76 attribute(s) from 76 row(s): cloud_sdk_docker" \
        "(+68 more, not listed)" \
        "single_sample/workspace.tsv.tmpl declares 98 attribute(s) from 98 row(s): cloud_sdk_docker" \
        "(+90 more, not listed)"
    wantline "R: the reader says it never parsed a value, on a tree whose tables carry 174 of them" R1 \
        "values=not-read: no row's second field was parsed, resolved or used in any decision"
    # THE SHIPPED-STEM ARM, ON THE PATH THAT READS REAL DATA. `_tree_from_ref` archives ONE subtree, so the
    # temp tree behind `--repo/--ref` holds `inputs/templates/terra_workspaces` and no `inputs/values/` at
    # all: the shipped-stem half of the test-bundle rule used to be dead on this path, and the sentence in
    # `_test_bundle_reason` printed `shipped test profile(s): none` about a ref that really ships
    # `test_single_sample_NA12878.json` and `…_NA19240.json`. The claim is about NAMES, so the ref is now
    # listed with `git ls-tree` instead of unpacked: this leg asks the tool's own two listing paths (ref for
    # `--repo/--ref`, directory walk for `--tree`), prints the stems, and prints the refusal sentence each
    # list would produce. `none` must remain reachable for a tree that ships none — that is the other half —
    # and no profile is opened by any of it: `inputs/values` is listed, never read.
    PROFILE_PROBE='import sys
sys.path.insert(0, "checks")
import terra_entity_check as t
repo, ref, tree = sys.argv[1], sys.argv[2], sys.argv[3]
ref_stems = t.test_profile_stems_at_ref(repo, ref)
print("GSVTK-SHIPPED-PROFILES ref=%s tree=%s" % (",".join(ref_stems) or "-", ",".join(t.test_profile_stems(tree)) or "-"))
root = ref_stems[:1] or ["test_absent_profile"]
print("GSVTK-SHIPPED-REASON %s" % t._test_bundle_reason("", root, ref_stems))
print("GSVTK-SHIPPED-REASON-NOLIST %s" % t._test_bundle_reason("", root, []))'
    runsc R-profiles "${BASE[@]}" "$PY" -c "$PROFILE_PROBE" "$CK" "$PINNED" "$FIX"
    wantf "R: the shipped test profiles are NAMES off the ref — one ls-tree, no second subtree unpacked" \
        R-profiles 0 \
        "GSVTK-SHIPPED-PROFILES ref=test_single_sample_NA12878,test_single_sample_NA19240"
    wantf "R: so the refusal sentence names the profiles the ref ships, instead of saying it ships none" \
        R-profiles 0 \
        "shipped test profile(s): test_single_sample_NA12878, test_single_sample_NA19240; no profile was " \
        "opened to decide this)"
    wantf "R: and a corpus that really ships no profile still gets the honest 'none'" R-profiles 0 \
        "GSVTK-SHIPPED-REASON-NOLIST its \`{{ test_single_sample_NA12878.* }}\` read names a test values " \
        "profile by root (shipped test profile(s): none; no profile was opened to decide this)"
    # --- the required-input census on the REAL tree ----------------------------------------------
    # Its OWN region inside the checkout's, because the two guards are independent: a box with the checkout
    # and without miniwdl withholds these legs and nothing else, and the declared/reached pair attributes
    # each total to its own region. Pinned COUNTS, and no leg here demands a green census — 13 configs are
    # unsatisfied at this ref and that is the honest statement, so the legs pin the numbers and the
    # arithmetic, not a verdict the tree does not earn.
    if [ "$HAVE_MINIWDL" = yes ]; then
        gsvtk_guard_sites "entity-real-miniwdl"
        wantf "R: every workflow behind every config was readable at this ref — the precondition the census needs" R1 1 \
            "capture-no-miniwdl=0 capture-no-wdl-dir=0 capture-no-document=0 capture-load-failure=0 " \
            "capture-no-workflow=0 capture-no-wdl=0 " \
            "GSVTK-ENTITY-UNCHECKED configs=31 member-attr-reads=64 configs-with-member-attrs=14 " \
            "workspace-binding-reads=0 configs-with-workspace-reads=0 workspace-attribute-values-unread=174 " \
            "configs-with-unclassified-values=0 required-configs-uncaptured=0 required-uncaptured-because=- " \
            "root-entity-type=never-read"
        wantf "R: the required census this tree actually produces, measured: 427 inputs over 29 workflows" R1 1 \
            'GSVTK-REQUIRED-SUMMARY configs=31 required=427 workspace-attr=152 workspace-missing=11 entity=109 literal=35 needs-bundle=6 test-bundle=0 docker=91 mixed=1 no-oracle=0 container=0 unbound=22 workflows=29 workflows-captured=29 configs-ok=18 configs-unsatisfied=13 configs-not-captured=0'
        wantf "R: and that census is NOT green at this ref — 18 pass, 13 refuse, said as numbers" R1 1 \
            "configs-ok=18 configs-unsatisfied=13 configs-not-captured=0"
        wantf "R: THE PLAN'S OWN SENTENCE, printed by the tool: GATKSVPipelineSingleSample's 83 required inputs," \
            R1 1 \
            'GSVTK-REQUIRED config=single_sample/GATKSVPipelineSingleSample.json.tmpl flavor=single_sample wf=GATKSVPipelineSingleSample workflows=1 capture=OK required=83 workspace-attr=57 workspace-missing=0 entity=2 literal=12 needs-bundle=2 test-bundle=0 docker=10 mixed=0 no-oracle=0 container=0 unbound=0 files=wdl/GATKSVPipelineSingleSample.wdl state=OK reason=-'
        wantf "R: ... NONE of them unbound, with its 10 images and 2 bundle renders disclosed, not resolved" R1 1 \
            "single_sample/GATKSVPipelineSingleSample.json.tmpl (single_sample): 83 required input(s), workspace-attr=57 entity=2 literal=12 needs-bundle=2 docker=10" \
            "10 required input(s) are container images"
        countis "R: 11 configs bind a REQUIRED input to an attribute their own flavor's table never declares" R1 \
            '^GSVTK-REQUIRED .* reason=required-workspace-missing$' 11
        countis "R: the 22 unbound inputs belong to the two write-back files under output_configurations/" R1 \
            '^GSVTK-REQUIRED config=cohort_mode/workflow_configurations/output_configurations/.* reason=required-unbound$' 2
        countis "R: those two ALREADY refuse at the entity layer, so the required layer names no new broken config" R1 \
            '^GSVTK-ENTITY config=cohort_mode/workflow_configurations/output_configurations/.* reason=no-name-key$' 2
        wantabsent "R: files= prints ref-relative paths, never the temp directory \`git archive\` unpacked" R1 \
            '^GSVTK-REQUIRED .* files=[^ ]*(gsvtk-entity|/var/|/private/|/tmp/)'
        # The acceptance criterion's sharp end, counted off the three machine lines: does any config refuse
        # ONLY because of required inputs? Zero means every required refusal at this ref is a config that was
        # already broken above — the new layer reports, it does not manufacture.
        qsole="$(awk '
            /^GSVTK-ENTITY config=/  { p=""; s=""; for (i=1;i<=NF;i++){ if ($i ~ /^config=/) p=substr($i,8); if ($i ~ /^state=/) s=substr($i,7) } e[p]=s; next }
            /^GSVTK-VALUE config=/   { p=""; s=""; for (i=1;i<=NF;i++){ if ($i ~ /^config=/) p=substr($i,8); if ($i ~ /^state=/) s=substr($i,7) } v[p]=s; next }
            /^GSVTK-REQUIRED config=/{ p=""; s=""; for (i=1;i<=NF;i++){ if ($i ~ /^config=/) p=substr($i,8); if ($i ~ /^state=/) s=substr($i,7) } r[p]=s; next }
            END { n = 0; for (p in r) if (r[p] == "UNRESOLVED" && e[p] == "OK" && v[p] == "OK") n++; print n + 0 }' \
            "$TMP/R1.out")"
        wanteq "R: no config's required refusal is its ONLY finding at this ref — each also refuses above" "$qsole" 0
        gsvtk_guard_sites_end "entity-real-miniwdl"
    else
        printf '  SKIP  entity: %s cannot import miniwdl, so the real-tree required-input legs did not run — the fixture Qblind legs still ran the NO-MINIWDL arm, and the pair below names the sites this skip withheld\n' "$PY"
        skipped=$((skipped + 1))
        gsvtk_note_skip "entity-real-miniwdl"
    fi
    gsvtk_guard_sites_end "entity-no-checkout"
else
    # How many sites this skip withholds, in the same units the pair uses: the declared total minus the DISTINCT
    # sites this run recorded. It used to be a second `grep -cE` over the aliases this file has now dropped,
    # and it was right by coincidence, because every site matching those names happened to sit inside the skipped
    # block (3 + 4 + 3 = 10). One such call anywhere else in the file would have silently inflated the number
    # printed as "withheld", which is the opposite of what a withheld count is for. Subtracting reached SITES
    # cannot drift that way: the ledger is written by the recorders themselves, so a site that ran is a site
    # subtracted no matter where in the file it sits.
    withheld=$((DECLARED - $(gsvtk_reached_sites)))
    printf '  SKIP  entity: no gatk-sv checkout holding %s (GSVTK_GATK_SV_CHECKOUT is "%s"): the real-tree cross-check did not run, so %s of the %s assertion(s) this file declares are withheld behind this named skip (that line is what CI prints, and a withheld assertion is not a passed one)\n' \
        "$PINNED" "${CK:-unset}" "$withheld" "$DECLARED"
    skipped=$((skipped + 1))
    # The same number from the other side: the region is the block, read off this file, and the guard requires
    # every site that did not run to be inside it. `withheld` above is what this run subtracted from the
    # declared total; the region is what the skip is ALLOWED to subtract, and the pair compares the two.
    gsvtk_note_skip "entity-no-checkout"
fi

# --- the tally, and the declared/reached pair (docs/gap-ledger.md C10) ------------------------------
# C10's open half: a counted skip that counts the BAIL rather than the assertions it withheld. The number of
# assertion call SITES this file contains is derived FROM this file, not typed, so a block going missing moves
# the number — and when nothing skipped the declared set and the reached set must be EQUAL, in every mode
# including the one where this file's optional real-tree block ran — and every site that did NOT run has to be
# inside the entity-no-checkout region, which is the block that skipped and nothing else. One implementation, in
# scripts/selftest.d/declared.sh, shared with the phases that can bail. What it still cannot see is a DELETION:
# remove a site and both numbers fall together, so this file can lose assertions and the pair stays green.
ran=$((ok + fail))
printf '\nentity selftest: %s passed, %s failed, %s skipped\n' "$ok" "$fail" "$skipped"
gsvtk_declared_report "entity selftest" "$SELF" "$ran" "$skipped" || fail=$((fail + 1))
[ "$fail" -eq 0 ] || exit 1
exit 0
