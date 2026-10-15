#!/usr/bin/env bash
# scripts/selftest.d/reach.sh — the `checks/wdl_reach.py --images` assertions, standalone.
#
#     bash scripts/selftest.d/reach.sh .venv/bin/python
#
# What this phase owns that nothing else does
# -------------------------------------------
# `reach` already answers "which workflows does this change reach", and `wdl_reach.py --selftest`
# guards that graph in both directions. This file guards the NEXT question, the one A11 was opened
# for: which container images does the reached set bind, where does each value come from, and which of
# those images cannot be a build of the commit under review — because a workflow bound to a pre-change
# image runs the old code and prints nothing suspicious.
#
# Every case runs against `fixtures/reach/`: seven synthetic WDLs, four synthetic rendered input JSONs,
# a synthetic `inputs/values/dockers.json`, and a synthetic one-step module profile
# (`fixtures/reach.json`, reached through GSVTK_MODULE/GSVTK_MODULE_DIR so the ONE profile reader is the
# one under test). The shapes are gatk-sv's — `<branch>-<sha6>` vs a date-prefixed release tag, a
# `${workspace.x}` Terra binding, a workflow whose declared name differs from its file basename — but
# nothing here is copied from the real checkout, and nothing here needs it: no checkout, no network, no
# jar, no write outside a temp dir.
#
# The assertions are deliberately about what a READER would catch. "A per-workflow list that left a
# workflow out" is the failure this feature exists to prevent, so the counted header line, the
# "binds NO docker input" line and the UNRESOLVED reasons are asserted as loudly as the image strings.
#
# Own tally, own exit code, one Makefile line to wire in. bash 3.2 compatible.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi

FIX="$ROOT/scripts/selftest.d/fixtures/reach"
FIXMOD="$ROOT/scripts/selftest.d/fixtures"
HEAD_SHA="9a34dc00000000000000000000000000000000aa"      # matches the fixtures' branch-minted tag
ok=0; fail=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"
mkdir -p "$TMP/no-inputs"

# ------------------------------------------------- declared vs reached (docs/gap-ledger.md C10)
# Both numbers count assertion call SITES, so they can be required to be EQUAL on every run and the phase can
# say, by LINE NUMBER, which declared assertion never executed. The callees are what gsvtk_declared_funset
# derives from this file (the four `want*`/`countis` recorders plus `canary`), re-derived by the reporter on
# every run; `runsc` is deliberately absent because running a scenario records no verdict. The 30 this phase
# prints against the 29 sites is not an error: the `--images` artifact-comparison check at the end records its
# verdict in a top-level if/fi, outside any recorder — a verdict, not a site, which is why the reporter states
# both totals. The ledger file IS the set of reached sites, because bash 3.2 has no associative arrays.
SELF="$ROOT/scripts/selftest.d/reach.sh"
. "$ROOT/scripts/selftest.d/declared.sh"
GSVTK_DECL_FUNCS="wantf wantline wantabsent countis canary"
gsvtk_declared_init "$TMP"

# runsc NAME CMD... — run once, keep the combined output and the exit status for the checks below.
# Scenarios are run once and asserted many times: the same rule as the tree loader, N assertions
# should never cost N runs of the tool.
runsc() {
    local name="$1"; shift
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    printf '%s\n' "$out" > "$TMP/$name.out"
    printf '%s\n' "$rc" > "$TMP/$name.rc"
}

# wantf DESC NAME WANT_RC NEEDLE... — the recorded run exited WANT_RC and every needle appears.
wantf() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # the line that CALLED wantf(); one entry per call SITE, not per loop turn
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

# wantline DESC NAME PATTERN — one line matches the regex, so a label can be pinned to the input that
# carried it. "PRE-CHANGE appears somewhere" would still pass if the tool attached it to the wrong row.
wantline() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf() above
    local desc="$1" name="$2" pat="$3"
    if grep -qE -- "$pat" "$TMP/$name.out" 2>/dev/null; then
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    else
        fail=$((fail + 1)); printf '  FAIL  %s\n          no line matched: %s\n' "$desc" "$pat"
    fi
}

# wantabsent DESC NAME PATTERN — a bucket the fixtures never produced must not be printed. Counting on
# absence is how a misclassified value (a published tag quietly reported as a branch build) gets caught.
wantabsent() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf() above
    local desc="$1" name="$2" pat="$3"
    if grep -qE -- "$pat" "$TMP/$name.out" 2>/dev/null; then
        fail=$((fail + 1)); printf '  FAIL  %s\n          unexpected line: %s\n' "$desc" \
            "$(grep -E -- "$pat" "$TMP/$name.out" | head -1)"
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# countis DESC NAME PATTERN EXPECTED — how many lines start the pattern. This is the "one tree load"
# assertion and the "every asked name got its own block" assertion; a substring match cannot count.
countis() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf() above
    local desc="$1" name="$2" pat="$3" want="$4" got
    got="$(grep -cE -- "$pat" "$TMP/$name.out" 2>/dev/null)"
    if [ "$got" = "$want" ]; then
        ok=$((ok + 1)); printf '  ok    %s (%s)\n' "$desc" "$got"
    else
        fail=$((fail + 1)); printf '  FAIL  %s — %s line(s), want %s\n' "$desc" "$got" "$want"
    fi
}

# A canary for THIS file: its own tally must be able to go down.
canary() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf() above
    local rc
    false; rc=$?
    if [ "$rc" -ne 0 ]; then
        ok=$((ok + 1)); printf '  ok    this file notices a failing command (canary)\n'
    else
        fail=$((fail + 1)); printf '  FAIL  the harness in %s is vacuous\n' "$0"
    fi
}

echo
echo "selftest: reach --images names the images and the distinction (offline, synthetic tree)"
canary

BASE=(env GSVTK_CONFIG="$TMP/empty.env" GSVTK_MODULE=reach GSVTK_MODULE_DIR="$FIXMOD"
      GSVTK_GATK_SV_CHECKOUT= GSVTK_GATK_CHECKOUT=)

# --- R1: one shared-library task, the whole distinction in one answer ------------------------------
# Reverse from a script, the direction a grep cannot answer at all: the answer is the tasks that invoke
# it, the file that holds the task, and the THREE workflows that call it. Each binds a different kind of
# value, and the whole point of the mode is that they do not all mean the same thing.
runsc R1 "${BASE[@]}" "$PY" checks/wdl_reach.py --dir "$FIX" --inputs-root "$FIX" \
    --reverse --target count_things.sh --images --head-sha "$HEAD_SHA"
wantf "R1: a shared task's fan-out answered with images, and it exits 0" R1 0 \
    "images: 3 workflow(s) in this answer, 3 listed" \
    "from 9 answer node(s) file=4 script=1 task=1 workflow=3; 3 dedup(s)" \
    "1 reached file(s) hold no workflow, so they bind no docker input of their own: SharedTasks.wdl"
wantf "R1: every value source is named, including which JSON answered" R1 0 \
    "rendered input JSON: read 4 JSON(s) from $FIX/inputs/build (given by --inputs-root)" \
    "dockers.json pinboard: 5 value(s)" \
    "module profile: 1 docker binding(s) from $FIXMOD/reach.json" \
    "commit under review: $HEAD_SHA (--head-sha)"
# The distinction, per row: the same workflow binds one pre-change image and one branch image.
wantline "R1: Alpha's JSON-bound gatk_docker is reported PRE-CHANGE, not as a branch build" R1 \
    '^ +gatk_docker +PRE-CHANGE +us\.gcr\.io/acme-test/gatk-sv/gatk:2026-09-17-v1\.1\.1-67b318e7$'
wantline "R1: Alpha's WDL-default sv_pipeline_docker is reported as built from THIS commit" R1 \
    '^ +sv_pipeline_docker +BRANCH-BUILT +us\.gcr\.io/acme-test/gatk-sv/sv-pipeline:my-branch-9a34dc$'
wantline "R1: Beta binds the published sv-pipeline tag, the pre-change half of the story" R1 \
    '^ +sv_pipeline_docker +PRE-CHANGE +us\.gcr\.io/acme-test/gatk-sv/sv-pipeline:2026-09-17-v1\.1\.1-67b318e7$'
wantline "R1: a branch tag from another commit is a STALE build, not a passing one" R1 \
    '^ +sv_pipeline_docker +STALE-BRANCH-BUILD +us\.gcr\.io/acme-test/gatk-sv/sv-pipeline:stale-branch-112233$'
wantf "R1: the two value sources are told apart (WDL default vs a named rendered JSON)" R1 0 \
    "<- WDL default: Alpha.wdl:12" \
    "<- rendered input JSON: ref_panel_1kg/test/Beta/Beta.json"
wantf "R1: a profile binding is attributed to the profile, not invented as an image" R1 0 \
    "<- module profile reach.json: step 99-Renamed"
# The workflow name and the file basename differ, and only ONE of the two JSON spellings is the
# workflow's own name. The fallback is labelled, so a reader can see which lookup answered.
wantline "R1: the file-stem lookup is labelled as such, never used silently" R1 \
    'rendered input JSON \(matched by file stem, not by workflow name\)'
# The buckets: 3 published values, 1 stale build, 1 build of the commit under review, and no
# CANNOT-SAY, because --head-sha settled every tag that needed settling.
wantf "R1: the verdict block counts the published images as NOT a build of this commit" R1 0 \
    "[0] NOT A BUILD OF THE COMMIT UNDER REVIEW (3)" \
    "[1] BUILT FROM A DIFFERENT COMMIT (1)" \
    "[2] BUILT FROM THE COMMIT UNDER REVIEW (1)"
wantabsent "R1: no image lands in CANNOT-SAY when the commit under review is known" R1 \
    '\[3\] CANNOT SAY'
wantf "R1: a workspace-attribute binding is UNRESOLVED and counted, not dropped" R1 0 \
    "sv_pipeline_docker      UNRESOLVED" \
    "binding(s) whose value is not a literal image (1)" \
    "ClusterDepthByProfile.sv_pipeline_docker: 'workspace.sv_pipeline_docker' names an entity attribute"

# --- R2: binds none, nothing binds it, a placeholder, and an unknown name ---------------------------
runsc R2 "${BASE[@]}" "$PY" checks/wdl_reach.py --dir "$FIX" --inputs-root "$FIX" \
    --target Gamma.wdl --target Delta.wdl --target Epsilon.wdl --target nope.zzz --images
wantf "R2: a workflow that binds no docker input is PRINTED as binding none, not omitted" R2 2 \
    "Gamma.wdl::Gamma  [target + depth 1 workflow]   binds NO docker input" \
    "images: 1 workflow(s) in this answer, 1 listed (1 of them bind no docker input)" \
    "no reaching workflow here binds a literal image"
wantf "R2: an unbound input is UNRESOLVED with the reason, not a guessed image" R2 2 \
    "Delta.wdl::Delta  [target + depth 1 workflow]   1 docker input(s)" \
    "manta_docker            UNRESOLVED" \
    "Delta.sv_utils_docker: the WDL declares String sv_utils_docker at Delta.wdl:9 with no default" \
    "is a Rawls placeholder substituted at"
wantabsent "R2: Delta's dockers.json string is NOT reported as its image (no fabrication)" R2 \
    '^ +sv_utils_docker +(PRE-CHANGE|BRANCH-BUILT|BRANCH-UNVERIFIED|STALE-BRANCH-BUILD|CANNOT-SAY)'
# The multi-target contract, under --images: ONE load, one block per answered name, and an unknown name
# still forces exit 2 without swallowing the answers around it.
wantf "R2: an unknown name among several still exits 2 and is named" R2 2 \
    "unknown target 'nope.zzz'" "UNKNOWN TARGETS"
countis "R2: --images does not add a second tree load (one tree: line)" R2 '^tree: ' 1
countis "R2: each answered name keeps its own block, in the order asked" R2 '^target: ' 3
wantline "R2: the run-level union is printed once, over the 3 answered names" R2 \
    '^images: the whole run \(3 answered target\(s\)'

# --- R3: the mode could not run, so it does not exit 0 ---------------------------------------------
# A named, counted refusal beats a clean exit over a question that went unanswered: "no images
# involved" and "I could not tell you" must not print the same way.
runsc R3 "${BASE[@]}" "$PY" checks/wdl_reach.py --dir "$FIX" --target Delta.wdl \
    --images --inputs-root "$TMP/no-inputs"
wantf "R3: --images with no readable input JSON is a NAMED incomplete answer, exit 2" R3 2 \
    "IMAGE ANSWER INCOMPLETE" \
    "no inputs/build under --inputs-root $TMP/no-inputs" \
    "rendered input JSON: SKIPPED"

# --- R4: a source that is absent is named; a source that refuses is named too -----------------------
runsc R4 "${BASE[@]}" GSVTK_MODULE=reach-absent "$PY" checks/wdl_reach.py --dir "$FIX" \
    --inputs-root "$FIX" --reverse --target count_things.sh --images --head-sha "$HEAD_SHA"
wantf "R4: a module profile that is not there is reported as a named skip" R4 0 \
    "module profile: SKIPPED" "reach-absent.json: no profile at" \
    "no module-profile binding was consulted"
wantabsent "R4: with the profile gone the workspace-attribute hole is gone too, and said so" R4 \
    'not a literal image'
wantf "R4: and the rest of the answer still stands without the profile" R4 0 \
    "[0] NOT A BUILD OF THE COMMIT UNDER REVIEW (3)" \
    "[2] BUILT FROM THE COMMIT UNDER REVIEW (1)"

# --- R5: the contract callers who did not ask for images already depend on -------------------------
# Two runs of the same question, one with --images and one without, must agree everywhere except in the
# image half. The artifact is compared key for key after the image keys and `argv` are removed, so
# "byte-identical" here is the whole document, not a few sampled lines.
runsc R5 "${BASE[@]}" "$PY" checks/wdl_reach.py --dir "$FIX" \
    --target Alpha.wdl --target nope.zzz --target Beta.wdl --json "$TMP/plain.json"
wantf "R5: a plain multi-target run still exits 2 on the unknown name" R5 2 \
    "unknown target 'nope.zzz'"
countis "R5: still ONE tree load for three names" R5 '^tree: ' 1
countis "R5: and still one block per answered name" R5 '^target: ' 2
wantabsent "R5: a run that did not ask for images prints no image line at all" R5 '^(images|image sources)'
runsc R6 "${BASE[@]}" "$PY" checks/wdl_reach.py --dir "$FIX" \
    --target Alpha.wdl --target nope.zzz --target Beta.wdl \
    --inputs-root "$FIX" --images --head-sha "$HEAD_SHA" --json "$TMP/images.json"
wantf "R6: --images adds the image half to the same two answer blocks" R6 2 \
    "images: the whole run (2 answered target(s)"
if "$PY" - "$TMP/plain.json" "$TMP/images.json" <<'PY'
import json, sys
# Two runs of the same question, once with --images and once without. Everything is compared except the
# two fields that CANNOT match across two runs: `argv` (one of them names --images) and `load_seconds`
# (a timer — proved by the diff: it is the only field that differs when --images is absent).
plain = json.load(open(sys.argv[1]))
imgs = json.load(open(sys.argv[2]))
for doc in (plain, imgs):
    doc.pop('argv', None)
    doc.pop('load_seconds', None)
imgs.pop('images', None)
for t in imgs.get('targets', []):
    t.pop('images', None)
sys.exit(0 if plain == imgs else 1)
PY
then
    ok=$((ok + 1)); printf '  ok    the artifact without --images is the same document, key for key\n'
else
    fail=$((fail + 1)); printf '  FAIL  --images changed the artifact a plain caller reads\n'
fi

printf 'reach selftest: %s ok, %s failed\n' "$ok" "$fail"
# Equal sets, every run. What the pair cannot see is a DELETION: declared and reached drop together when a call
# site is removed — see the header of scripts/selftest.d/declared.sh and docs/gap-ledger.md C10.
gsvtk_declared_report "reach selftest" "$SELF" "$((ok + fail))" 0 || fail=$((fail + 1))
[ "$fail" -eq 0 ]
