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
# column. Every one of those is a claim about a corpus, and the corpus that proves them is upstream's —
# which a CI machine does not have. So the shapes are committed here instead: one synthetic
# `inputs/templates/terra_workspaces/` tree, two flavors, five tables, twenty configs, one config per
# shape, including the trap (a config whose member collection is a DIFFERENT type from its name key) and
# the bait table that makes the dead member rule look right.
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
#   beta/workspace.tsv.tmpl                a workspace table: excluded, and counted as excluded
#   beta/unreadable_header.tsv.tmpl        `entity:{{ flavour }}_id` — an unreadable type, named not guessed
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
#   .../CannotParse.json.tmpl              truncated JSON: a finding about the document, not a licence to
#                                          say nothing
#   .../output_configurations/SyntheticOutputs.json.tmpl
#                                          binding prefix `Synthetic`, file stem `SyntheticOutputs`: the
#                                          workflow comes from the prefix, and the mismatch is printed
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
TOOL="$ROOT/checks/terra_entity_check.py"
PINNED="${GSVTK_ENTITY_REF:-e1909d2f}"
ok=0; fail=0; skipped=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"
mkdir -p "$TMP/nothing"

# Which checkout the optional cross-check may read, resolved BEFORE GSVTK_CONFIG is pinned to an empty
# file below. Empty means "no checkout", which is the CI case and skips by name.
CK="$("$PY" -c 'import sys; sys.path.insert(0, "kit"); import config; print(config.get("GATK_SV_CHECKOUT"))' \
     2>/dev/null | tail -1)"

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
wanteq() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf above
    local desc="$1" got="$2" want="$3"
    if [ "$got" = "$want" ]; then
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
runsc E1 "${BASE[@]}" "$PY" "$TOOL" --tree "$FIX"
wantf "E1: the synthetic corpus runs and exits 1 (findings exist, and a finding is exit 1 here)" E1 1 \
    "expander: JINJA_RE from terra/batch_configs.py" \
    "shipped tables: 5 entity/membership TSV(s), type(s) participant x1, sample x2, sample_set x1, sample_sets x1" \
    "1 other TSV(s) excluded" \
    "UNREADABLE table: beta/unreadable_header.tsv.tmpl"
countis "E1: one machine line per config, no config swallowed" E1 '^GSVTK-ENTITY config=' 20
countis "E1: exactly one summary line" E1 '^GSVTK-ENTITY-SUMMARY ' 1
wantf "E1: the census the fixtures were built to produce" E1 1 \
    "GSVTK-ENTITY-SUMMARY configs=20 ok=8 no-name-key=4 many-name-keys=1 no-shipped-table=2 columns-missing=4 cannot-parse=1 namekeys-one=14 namekeys-zero=5 namekeys-many=1"
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
    "not JSON that \`json.loads\` accepts after the one \`{{ ... }}\` -> null substitution"

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
runsc E18 env GSVTK_CONFIG="$TMP/empty.env" "$PY" "$TOOL" --help
wantf "E18: --help works with an empty profile and states the four exit codes" E18 0 \
    "Exit codes" "3  prerequisite missing" "GSVTK-ENTITY-SUMMARY"

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
    runsc R1 "${BASE[@]}" "$PY" "$TOOL" --repo "$CK" --ref "$PINNED"
    wantf "R1: the real corpus runs (findings exist at this ref)" R1 1 \
        "shipped tables: 5 entity/membership TSV(s)" \
        "cohort_mode/sample_set_membership_1kgp.tsv.tmpl membership:sample_set_id  columns: sample" \
        "single_sample/sample.tsv.tmpl"
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
fi

# --- the tally, and the declared/reached pair (docs/gap-ledger.md C10) ------------------------------
# C10's open half: a counted skip that counts the BAIL rather than the assertions it withheld. The number of
# assertion call SITES this file contains is derived FROM this file, not typed, so a block going missing moves
# the number — and when nothing skipped the declared set and the reached set must be EQUAL, in every mode
# including the one where this file's optional real-tree block ran. One implementation, in
# scripts/selftest.d/declared.sh, shared with the phases that can bail. What it still cannot see is a DELETION:
# remove a site and both numbers fall together, so this file can lose assertions and the pair stays green.
ran=$((ok + fail))
printf '\nentity selftest: %s passed, %s failed, %s skipped\n' "$ok" "$fail" "$skipped"
gsvtk_declared_report "entity selftest" "$SELF" "$ran" "$skipped" || fail=$((fail + 1))
[ "$fail" -eq 0 ] || exit 1
exit 0
