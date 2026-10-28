#!/usr/bin/env bash
# scripts/selftest.d/reach.sh — the `checks/wdl_reach.py --images` assertions, standalone.
#
#     bash scripts/selftest.d/reach.sh .venv/bin/python
#
# Tests which container images the reached workflows bind, where each value comes from, and which
# images cannot be a build of the commit under review.
#
# Every case runs against synthetic fixtures under `fixtures/reach/` plus the one-step module profile
# `fixtures/reach.json`. No checkout, no network, no jar, no writes outside a temp dir. bash 3.2 compatible.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi

FIX="$ROOT/scripts/selftest.d/fixtures/reach"
FIXMOD="$ROOT/scripts/selftest.d/fixtures"
HEAD_SHA="9a34dc00000000000000000000000000000000aa"      # matches the fixtures' branch-minted tag
ok=0; fail=0; skipped=0

# `checks/wdl_reach.py` needs miniwdl (a dev dependency). Without it the tool refuses, and every assertion
# against a refused scenario is recorded as a SKIP rather than a failure (or a false pass, for the
# absence-based recorders).
HAS_WDL=1
"$PY" -c 'import WDL' >/dev/null 2>&1 || HAS_WDL=0

# no_wdl DESC NAME — 0 (after recording a skip) when scenario NAME never ran because the analyzer is absent.
no_wdl() {
    [ "$HAS_WDL" = 0 ] || return 1
    grep -qF 'miniwdl is required' "$TMP/$2.out" 2>/dev/null || return 1
    skipped=$((skipped + 1))
    printf '  SKIP  %s — the tool refused: miniwdl is required, so this scenario never ran\n' "$1"
    printf '        (pip install -r requirements-dev.txt; CI installs it, which is where these run)\n'
    return 0
}
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"
mkdir -p "$TMP/no-inputs"

# Declared vs reached assertion call sites (see declared.sh). `runsc` records no verdict, so it is not
# listed. The `--images` artifact comparison at the end records one verdict outside any recorder, so the
# verdict count is one higher than the site count.
SELF="$ROOT/scripts/selftest.d/reach.sh"
. "$ROOT/scripts/selftest.d/declared.sh"
GSVTK_DECL_FUNCS="wantf wantline wantabsent countis canary"
gsvtk_declared_init "$TMP"

# runsc NAME CMD... — run once, keep the combined output and the exit status for the checks below.
runsc() {
    local name="$1"; shift
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    printf '%s\n' "$out" > "$TMP/$name.out"
    printf '%s\n' "$rc" > "$TMP/$name.rc"
}

# wantf DESC NAME WANT_RC NEEDLE... — the recorded run exited WANT_RC and every needle appears.
wantf() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # the calling line: one entry per call site
    local desc="$1" name="$2" wantrc="$3"; shift 3
    local miss="" n rc
    rc="$(cat "$TMP/$name.rc" 2>/dev/null)"
    for n in "$@"; do
        grep -qF -- "$n" "$TMP/$name.out" 2>/dev/null || miss="$miss
          missing: $n"
    done
    if no_wdl "$desc" "$name"; then
        :
    elif [ "$rc" = "$wantrc" ] && [ -z "$miss" ]; then
        ok=$((ok + 1)); printf '  ok    %s (exit %s)\n' "$desc" "$rc"
    else
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s, want %s)%s\n' "$desc" "$rc" "$wantrc" "$miss"
        head -8 "$TMP/$name.out" 2>/dev/null | sed 's/^/          /'
    fi
}

# wantline DESC NAME PATTERN — one line matches the regex, so a label is pinned to its row.
wantline() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf() above
    local desc="$1" name="$2" pat="$3"
    if no_wdl "$desc" "$name"; then
        :
    elif grep -qE -- "$pat" "$TMP/$name.out" 2>/dev/null; then
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    else
        fail=$((fail + 1)); printf '  FAIL  %s\n          no line matched: %s\n' "$desc" "$pat"
    fi
}

# wantabsent DESC NAME PATTERN — no line matches (catches a value printed in the wrong bucket).
wantabsent() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf() above
    local desc="$1" name="$2" pat="$3"
    if no_wdl "$desc" "$name"; then
        :
    elif grep -qE -- "$pat" "$TMP/$name.out" 2>/dev/null; then
        fail=$((fail + 1)); printf '  FAIL  %s\n          unexpected line: %s\n' "$desc" \
            "$(grep -E -- "$pat" "$TMP/$name.out" | head -1)"
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# countis DESC NAME PATTERN EXPECTED — exactly EXPECTED lines match the pattern.
countis() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see wantf() above
    local desc="$1" name="$2" pat="$3" want="$4" got
    got="$(grep -cE -- "$pat" "$TMP/$name.out" 2>/dev/null)"
    if no_wdl "$desc" "$name"; then
        :
    elif [ "$got" = "$want" ]; then
        ok=$((ok + 1)); printf '  ok    %s (%s)\n' "$desc" "$got"
    else
        fail=$((fail + 1)); printf '  FAIL  %s — %s line(s), want %s\n' "$desc" "$got" "$want"
    fi
}

# Canary: proves this file's tally can go down.
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

# --- R1: one shared-library task reached from a script --------------------------------------------
# The answer is the invoking tasks, the file that holds them, and the three workflows that call them,
# each binding a different kind of image value.
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
# The workflow name and file basename differ; the basename fallback is labelled.
wantline "R1: the file-stem lookup is labelled as such, never used silently" R1 \
    'rendered input JSON \(matched by file stem, not by workflow name\)'
# Buckets: 3 published, 1 stale build, 1 build of the commit under review, no CANNOT-SAY (--head-sha given).
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

# --- classify_image uses the launcher's floating-tag predicate ------------------------------------
# Called directly because the R1 corpus has no path-versioned value. A floating ref keeps "the registry
# serves whatever is current", a digit-ending family does not, and both stay in bucket 3.
CLASSIFY_SRC='
import sys
sys.path.insert(0, "checks")
import wdl_reach as w

head = sys.argv[1]
FLOATING = "us.gcr.io/acme-test/gatk-sv/gatk"
FAMILY = "marketplace.gcr.io/acme-test/ubuntu1804"
BRANCH = "us.gcr.io/acme-test/gatk-sv/gatk:main-9a34dc"
DIGEST = "us.gcr.io/acme-test/gatk-sv/g@sha256:" + "a" * 64
assert w.version_kind(FLOATING) == "", w.version_kind(FLOATING)
assert w.version_kind(FAMILY) == "path", w.version_kind(FAMILY)
assert w.version_kind(BRANCH) == "tag", w.version_kind(BRANCH)
assert w.version_kind(DIGEST) == "digest", w.version_kind(DIGEST)
b, label, reason = w.classify_image(FLOATING, {}, head)
assert (b, label) == (3, "CANNOT-SAY"), (b, label)
assert "registry serves whatever is current" in reason, reason
b2, label2, reason2 = w.classify_image(FAMILY, {}, head)
assert (b2, label2) == (3, "CANNOT-SAY"), (b2, label2)
assert "ends in digits" in reason2 and "versioned_by" in reason2, reason2
assert "registry serves whatever is current" not in reason2, reason2
assert w.classify_image(BRANCH, {}, head)[:2] == (2, "BRANCH-BUILT"), w.classify_image(BRANCH, {}, head)
# A digest is neither tag shape, so reach cannot say which commit it is.
assert "matches neither shape" in w.classify_image(DIGEST, {}, head)[2], w.classify_image(DIGEST, {}, head)
print("PASS")
'
runsc CLASSIFY "$PY" -c "$CLASSIFY_SRC" "$HEAD_SHA"
wantf "A29: a floating ref and a version-in-path family get two DIFFERENT sentences and the same \
bucket (3), while a branch tag and a digest keep the answers they had" CLASSIFY 0 "PASS"

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
# Multiple targets under --images: one load, one block per answered name, and an unknown name
# forces exit 2 without hiding the other answers.
wantf "R2: an unknown name among several still exits 2 and is named" R2 2 \
    "unknown target 'nope.zzz'" "UNKNOWN TARGETS"
countis "R2: --images does not add a second tree load (one tree: line)" R2 '^tree: ' 1
countis "R2: each answered name keeps its own block, in the order asked" R2 '^target: ' 3
wantline "R2: the run-level union is printed once, over the 3 answered names" R2 \
    '^images: the whole run \(3 answered target\(s\)'

# --- R3: the mode could not run, so it does not exit 0 ---------------------------------------------
# "No images involved" and "could not tell" must not look the same.
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
# The same question with and without --images must produce the same JSON artifact apart from the
# image keys, `argv` and `load_seconds`.
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
if [ "$HAS_WDL" = 0 ]; then
    # Both scenarios refused, so there is nothing to compare. This verdict is recorded outside any recorder.
    skipped=$((skipped + 1))
    printf '  SKIP  the artifact-without---images comparison: both documents come from scenarios that never ran (miniwdl is required)\n'
else
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
fi

printf 'reach selftest: %s ok, %s failed, %s skipped (a SKIP here is not a pass in CI)\n' \
    "$ok" "$fail" "$skipped"
if [ "$HAS_WDL" = 0 ]; then
    printf 'reach selftest: one cause behind those skips — this interpreter ('"$PY"') cannot import WDL, so\n'
    printf '        `checks/wdl_reach.py` refused to walk a graph it could not parse. That is a DEV\n'
    printf '        dependency, not a defect: pip install -r requirements-dev.txt.\n'
fi
# Declared and reached must be equal; a deleted call site lowers both (see declared.sh).
gsvtk_declared_report "reach selftest" "$SELF" "$((ok + fail + skipped))" "$skipped" || fail=$((fail + 1))
[ "$fail" -eq 0 ]
