#!/usr/bin/env bash
# scripts/selftest.d/rerun.sh — assertions about terra/batch_rerun_step.py and the step map.
#
# What this pins: `--step` selects the step (config name, input-key prefix, Dockstore methodUri and
# root entity all move with it), the step-10 golden is still reproduced BYTE FOR BYTE, and every
# refusal is a refusal that can actually fire. The tool used to hold five module-level literals for
# step 10, so "rerun 08" silently reran GenotypeBatch -- a claim about five steps carried by code that
# implemented one.
#
# STANDALONE:  bash scripts/selftest.d/rerun.sh .venv/bin/python
# prints its own tally and exits nonzero on any failure. bash 3.2 compatible, no arrays (an empty
# array under `set -u` is an "unbound variable" on stock macOS bash, and this must run there).
#
# NOTHING HERE TOUCHES THE NETWORK. Three things keep that true, and the third is the one that would
# notice if the first two ever stop holding:
#   * only `show` runs as a real command, plus the refusals, which exit before the first request;
#   * the one case that exercises `submit` replaces the submission call with a recorder, in process,
#     and turns every other Terra entry point into a raiser, so a request that tried is a failure;
#   * GSVTK_TERRA_API_ROOT points at a closed port on this host, so a request that escapes the first
#     two is a connection refused in milliseconds -- never a POST to Terra with someone's credentials.
# The environment is a profile of PLACEHOLDER coordinates
# (scripts/selftest.d/fixtures/rerun.profile.env) and a throwaway work directory: no user config, no
# credentials, nothing real.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
# The same interpreter rule `make helpsweep` uses: a phase that SKIPs because the caller's python3 has
# no firecloud while ./.venv has it is a phase that reported nothing. Only skip when there is no venv.
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1 && [ -x .venv/bin/python ] \
   && .venv/bin/python -c 'import firecloud' >/dev/null 2>&1; then PY=.venv/bin/python; fi
# Declared-vs-reached, one implementation for every phase that can bail (docs/gap-ledger.md C10). SELF is
# this file so the count survives being invoked by relative path from the gate or standalone.
SELF=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")
. "$(dirname "$SELF")/declared.sh"
GSVTK_DECL_FUNCS="run run_absent bytes inproc"
# Declared, not assumed: exactly ONE site in this file runs inside a loop — the `for s in 06 07 08 09 10` step
# sweep, five executions of one call site — so the executions exceed the sites by 4. Declaring the number is
# what keeps the pair strict: an unexplained surplus is now a failure, and if someone adds a second loop site
# this line goes red rather than quietly widening the blind spot. Verify with `bash scripts/selftest.d/rerun.sh
# <python>` and read the declared/reached line.
GSVTK_DECL_SURPLUS=4
DECLARED=$(gsvtk_declared_count "$SELF")
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1; then
    printf 'rerun: SKIP — %s cannot import firecloud (python -m pip install -r requirements.txt)\n' "$PY"
    printf 'rerun: 0 ok, 0 failed, 1 skipped (a SKIP here is not a pass in CI)\n'
    # The counted skip counts the BAIL, so alone it reads "one thing was skipped" when in fact the whole phase
    # — every assertion below this line — did not run. Name that number, derived from this file, not typed.
    printf 'rerun: declares %s assertion call(s); this run reached 0, so %s did not run behind this 1 named skip\n' \
        "$DECLARED" "$DECLARED"
    exit 0
fi

FIX=scripts/selftest.d/fixtures/rerun.profile.env
GOLD=scripts/selftest.d/golden
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export GSVTK_CONFIG="$FIX"
export GSVTK_WORK="$TMP/work"
export GSVTK_REPO_ROOT="$ROOT"
# The offline belt: see the header. Nothing legitimate ever reaches this address.
export GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/"
unset GSV_WDL_VERSION GSVTK_GATK_SV_CHECKOUT

# Placeholder image refs, so the pin guard is satisfied by bytes this file can state out loud.
R=us.gcr.io/your-dev-project/test
PIN10="--image gatk_docker=$R/gatk:YOUR_TAG --image sv_base_mini_docker=$R/sv-base-mini:YOUR_TAG --image sv_pipeline_docker=$R/sv-pipeline:YOUR_TAG"
PIN09="$PIN10"
PIN07="--image sv_pipeline_docker=$R/sv-pipeline:YOUR_TAG"

ok=0; fail=0; QUIET=

# run DESC WANT_RC NEEDLE CMD... — exit code AND a line of output, because a refusal that exits 1
# having printed nothing has refused nothing.
run() {
    local desc="$1" want="$2" needle="$3"; shift 3
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    if [ "$rc" != "$want" ]; then
        fail=$((fail + 1))
        [ -n "$QUIET" ] || { printf '  FAIL  %s (exit %s, want %s)\n' "$desc" "$rc" "$want"
                             printf '%s\n' "$out" | head -6 | sed 's/^/          /'; }
    elif [ -n "$needle" ] && ! printf '%s\n' "$out" | grep -qF -- "$needle"; then
        fail=$((fail + 1))
        [ -n "$QUIET" ] || { printf '  FAIL  %s (exit %s is right, but the output never says %s)\n' \
                                 "$desc" "$rc" "$needle"
                             printf '%s\n' "$out" | head -6 | sed 's/^/          /'; }
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# run_absent DESC WANT_RC ABSENT CMD... — the assertion that a path DID NOT fire.
run_absent() {
    local desc="$1" want="$2" absent="$3"; shift 3
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    if [ "$rc" != "$want" ]; then
        fail=$((fail + 1)); printf '  FAIL  %s (exit %s, want %s)\n' "$desc" "$rc" "$want"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    elif printf '%s\n' "$out" | grep -qF -- "$absent"; then
        fail=$((fail + 1)); printf '  FAIL  %s (the output says %s, which it must not)\n' "$desc" "$absent"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# bytes DESC FILE_A FILE_B — the byte-identity claim, not "looks the same".
bytes() {
    local desc="$1" a="$2" b="$3"
    if cmp -s "$a" "$b"; then
        ok=$((ok + 1)); printf '  ok    %s (%s bytes, byte-identical)\n' "$desc" "$(wc -c < "$a" | tr -d ' ')"
    else
        fail=$((fail + 1)); printf '  FAIL  %s\n' "$desc"
        cmp "$a" "$b" 2>&1 | head -4 | sed 's/^/          /'
        diff "$a" "$b" 2>/dev/null | head -8 | sed 's/^/          /'
    fi
}

# inproc DESC WANT_RC PYFILE [ARG...] — a case that must print PASS itself.
inproc() {
    local desc="$1" want="$2"; shift 2
    local out rc
    out="$("$PY" "$@" 2>&1)"; rc=$?
    if [ "$rc" != "$want" ]; then
        fail=$((fail + 1)); printf '  FAIL  %s (exit %s, want %s)\n' "$desc" "$rc" "$want"
        printf '%s\n' "$out" | head -8 | sed 's/^/          /'
    elif ! printf '%s\n' "$out" | grep -qF -- 'PASS'; then
        fail=$((fail + 1)); printf '  FAIL  %s (exit %s but it never printed PASS)\n' "$desc" "$rc"
        printf '%s\n' "$out" | head -8 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# ------------------------------------------------------------------ the harness's own harness
# A selftest that cannot fail is worse than no selftest: this repo already shipped eight assertions
# that reported "ok" whatever they tested, because make expanded the command away. Prove both
# detectors work before trusting either.
QUIET=1
before=$fail
run "canary: a wrong exit code is noticed" 1 no-such-file /bin/cat "$TMP/not-here-at-all"
if [ "$fail" -eq $((before + 1)) ]; then
    fail=$before; ok=$((ok + 1)); printf '  ok    canary: a wrong exit code IS reported (harness works)\n'
else
    fail=$((fail + 1)); printf '  FAIL  canary: a failing command did not increment fail\n'
fi
before=$fail
run "canary: a missing needle is noticed" 0 "not-in-this-output" /bin/echo "hello world"
if [ "$fail" -eq $((before + 1)) ]; then
    fail=$before; ok=$((ok + 1)); printf '  ok    canary: an absent needle IS reported (harness works)\n'
else
    fail=$((fail + 1)); printf '  FAIL  canary: a missing needle did not increment fail\n'
fi
QUIET=""


printf 'rerun selftest (interpreter: %s; profile: %s; Terra root: %s = closed port, offline)\n' \
    "$PY" "$FIX" "$GSVTK_TERRA_API_ROOT"

# ------------------------------------------------------------- 1. the step-10 golden, byte for byte
# The control for the whole refactor: the body a step-10 rerun would POST, captured by the commit that
# introduced this file, before any per-step data moved into terra/steps.py. It is also the control that
# batch_configs' bodies stay byte-identical behind that module's own changes.
# shellcheck disable=SC2086   # the flag list is deliberately word-split on spaces
"$PY" terra/batch_rerun_step.py show $PIN10 > "$TMP/step10.json" 2> "$TMP/step10.err"
bytes "step 10 show reproduces the golden body byte for byte (stdout)" \
    "$GOLD/rerun-step10-body.json" "$TMP/step10.json"
# shellcheck disable=SC2086
run "and it says which step it resolved to (on stderr, so show | jq stays JSON)" 0 \
    "step 10  workflow GenotypeBatch  config 10-GenotypeBatch-rerun" \
    "$PY" terra/batch_rerun_step.py show $PIN10
run "the resolution line names the root entity and the ref the config pins" 0 \
    "root entity sample_set" "$PY" -c 'import sys; print(open(sys.argv[1]).read())' "$TMP/step10.err"
# The default IS step 10: same bytes with the flag spelled out, so "default" cannot drift.
# shellcheck disable=SC2086
"$PY" terra/batch_rerun_step.py --step 10 show $PIN10 > "$TMP/step10-explicit.json" 2>/dev/null
bytes "--step 10 explicitly == no --step at all (the default did not move)" \
    "$TMP/step10.json" "$TMP/step10-explicit.json"
# stdout carries the JSON and nothing else: probe_fixes parses it, and a reader pipes it to jq.
inproc "that stdout is the config JSON, with the name and root entity the golden says" 0 -c \
    'import json,sys
d = json.load(open(sys.argv[1]))
assert d["name"] == "10-GenotypeBatch-rerun", d["name"]
assert d["rootEntityType"] == "sample_set", d["rootEntityType"]
assert d["useCallCache"] is False, "useCallCache drifted off the default"
print("PASS")' "$TMP/step10.json"

# --------------------------------------------------- 2. per-step name / entity / methodUri / prefix
# The expectations are stated in the fixture file below, independently of the code: the URI is spelled
# out character by character (every slash %-encoded, including the ones in github.com, which is what
# Rawls demands -- a malformed one is a 404 "Cannot get dockstore://... from method repo").
cat > "$TMP/fields.py" <<'PY'
"""One step's resolved config, against expectations typed in this file rather than derived from the
tool. Prints PASS on success; an AssertionError exits 1, which the shell harness notices."""
import json, os, subprocess, sys

step, wf, root, name = sys.argv[1:5]
repo = os.environ["GSVTK_REPO_ROOT"]
out = subprocess.run([sys.executable, os.path.join(repo, "terra", "batch_rerun_step.py"),
                      "--step", step, "show", "--allow-unpinned-docker"],
                     cwd=repo, capture_output=True, text=True)
body = json.loads(out.stdout)
branch = "your-branch-under-test"                       # the fixture profile's GSVTK_BRANCH
want_uri = "dockstore://github.com%2Fbroadinstitute%2Fgatk-sv%2F" + wf + "/" + branch
m = body["methodRepoMethod"]
assert body["name"] == name, (body["name"], name)
assert body["rootEntityType"] == root, (body["rootEntityType"], root)
assert m["methodPath"] == "github.com/broadinstitute/gatk-sv/" + wf, m["methodPath"]
assert m["methodUri"] == want_uri, (m["methodUri"], want_uri)     # byte-exact, every slash encoded
assert m["sourceRepo"] == "dockstore" and m["methodVersion"] == branch, m
bad = [k for k in body["inputs"] if not k.startswith(wf + ".")]
assert not bad, ("input keys outside this step's own prefix: %r" % (bad,))
assert body["useCallCache"] is False, body.get("useCallCache")
rrline = [l for l in out.stderr.splitlines() if l.startswith("[rerun] ")]
assert rrline and rrline[0].startswith("[rerun] step " + step), out.stderr[:160]
# The two hand-built Dockstore URIs must agree byte for byte, or a rerun points at a method Terra
# cannot read: compare against the chain builder's own construction for the same workflow.
sys.path.insert(0, os.path.join(repo, "kit"))
sys.path.insert(0, os.path.join(repo, "terra"))
import batch_configs as tc
tc.BRANCH = branch
assert m == tc.dockstore(wf), ("rerun dstore() != batch_configs.dockstore(): %r vs %r"
                               % (m, tc.dockstore(wf)))
print("PASS %s %s %s %d inputs" % (name, root, want_uri, len(body["inputs"])))
PY
n=0
for s in 06 07 08 09 10; do
    wf=$("$PY" -c "import sys; sys.path.insert(0,'terra'); import steps; print(steps.workflow('$s'))")
    ent=$("$PY" -c "import sys; sys.path.insert(0,'terra'); import steps; print(steps.root_entity('$s'))")
    inproc "step $s -> $wf: config name, root entity ($ent), methodUri byte-exact, every key prefixed $wf." \
        0 "$TMP/fields.py" "$s" "$wf" "$ent" "${s}-${wf}-rerun"
    n=$((n + 1))
done
run "all five steps were checked (not a loop that ran zero times)" 0 "5" /usr/bin/printf '%s' "$n"

# ---------------- 2b. the step map and the config map are ONE fact, checked not assumed
# docs/module-profiles.md §9.4's remaining copy: the step -> workflow -> root entity triple lived in
# terra/steps.py as two dicts WHILE profiles/<module>.json carried the same three fields per step
# (batch_configs.CONFIGS is built from that file, and `batch_rerun_step._reconcile` refuses when the two
# disagree). Pinned BEFORE the literals move, so it holds on both sides of the change: if a future edit
# touches one copy and not the other, it fails here rather than at submission time in a message that
# names no key.
run "steps.py agrees with the module profile on workflow and root entity, for every step it knows" 0 \
    "PASS" "$PY" -c "
import sys; sys.path.insert(0,'terra'); sys.path.insert(0,'kit')
import steps, module_profile
prof = module_profile.load()
assert not prof.problems, 'profile would not load: %s' % prof.problem
bad = []
for step in steps.known_steps():
    key = '%s-%s' % (step, steps.workflow(step))
    spec = prof.configs.get(key)
    if not spec:
        bad.append('%s: steps.py answers for a step the profile does not carry (%s)'
                   % (key, ', '.join(sorted(prof.configs))))
        continue
    if spec['workflow'] != steps.workflow(step):
        bad.append('%s: workflow %r but the profile says %r' % (key, steps.workflow(step), spec['workflow']))
    if spec['rootEntityType'] != steps.root_entity(step):
        bad.append('%s: root entity %r but the profile says %r' % (key, steps.root_entity(step), spec['rootEntityType']))
assert bad == [], bad
print('PASS  %d steps, workflow and root entity both read from %s' % (len(steps.known_steps()), prof.path))"

# POSITIVE CONTROL for the derivation, and the reason the equality check above is not a tautology: a
# module profile naming a SIXTH step changes what the map reports. Hand-typed tables cannot know a step
# the file does not list, so this case fails on the pre-derivation code -- which is what proves the
# profile is the source rather than a decoration beside it.
mkdir -p "$TMP/sixsteps"
"$PY" - "$TMP/sixsteps/genotyping.json" <<'PY'
import json, sys
d = json.load(open("profiles/genotyping.json"))
s = json.loads(json.dumps(d["steps"][0]))
s["step"], s["wdl"], s["workflow"] = "11-ExtraStep", "ExtraStep", "ExtraStep"
s["inputs"] = {"ExtraStep.ped_file": "workspace.cohort_ped_file"}
s["outputs"] = {"ExtraStep.out": "workspace.extra_out"}
d["steps"].append(s)
json.dump(d, open(sys.argv[1], "w"), indent=1)
PY
run "a step added to the module profile appears in the step map (the table is data, not literals)" 0 \
    "11     ExtraStep" env GSVTK_MODULE_DIR="$TMP/sixsteps" "$PY" terra/steps.py
# The guard on that derivation: an entry with no two-digit prefix must be REFUSED, not skipped. A map
# quietly missing a step reads exactly like a chain that never had it, which is the failure this repo
# keeps meeting -- so the sentence has to name the entry and what it would have hidden.
mkdir -p "$TMP/unnumbered"
"$PY" - "$TMP/unnumbered/genotyping.json" <<'PY'
import json, sys
d = json.load(open("profiles/genotyping.json"))
d["steps"][0]["step"] = "GenerateBatchMetrics"
json.dump(d, open(sys.argv[1], "w"), indent=1)
PY
run "a step key with no number is refused, not silently dropped from the map" 1 \
    "does not start with a two-digit step number" \
    env GSVTK_MODULE_DIR="$TMP/unnumbered" "$PY" terra/steps.py

# -------------------------------------------------------------------- 3. the pin guard fires for OTHER steps too
# Asserting this for step 10 alone would prove nothing about 07 or 08: the guard reads the *step's*
# input map, and each step declares a different set of *_docker inputs (07 one, 08 three, 10 three).
#
# These expectations CHANGED when the profile route was fixed (see _stated in batch_rerun_step.py).
# `GSVTK_IMAGE_REPO` / `GSVTK_GATK_IMAGE_REPO` in this fixture are tagged, so they now satisfy the guard,
# which they never used to: step 07's ONE docker is therefore pinned by the profile alone and 07 builds,
# and step 09's remaining hole is `sv_base_mini_docker` rather than `gatk_docker`. Steps 08 and 09 still
# refuse, because `sv_base_mini_docker` and `linux_docker` exist in no profile vocabulary at all and can
# only arrive by --image. Do not "restore" the old needles: they asserted that a stated pin did not count.
run "step 08 refuses unpinned *_docker, naming its own keys" 1 "unpinned:  FilterBatchSamples.linux_docker" \
    "$PY" terra/batch_rerun_step.py --step 08 show
run "step 09 refuses on the docker no profile can name" 1 "unpinned:  MergeBatchSites.sv_base_mini_docker" \
    "$PY" terra/batch_rerun_step.py --step 09 show
# POSITIVE CONTROL: the same step, once its one docker really is pinned, builds its body.
# shellcheck disable=SC2086
run "control: step 07 builds its body once its one docker is tagged" 0 "07-FilterBatchSites-rerun" \
    "$PY" terra/batch_rerun_step.py --step 07 show $PIN07
run "an image pinned but UNTAGGED is refused as a floating reference, not a pin" 1 "pinned but untagged" \
    "$PY" terra/batch_rerun_step.py --step 07 show --image sv_pipeline_docker=$R/sv-pipeline

# --------------------- 3b. a pin the PROFILE states counts, and one it infers does not
# The defect: the guard asked `source in ("env", "profile")` while config.resolve() answers
# `profile:<path>` and `derived from PROJECT`, so the documented profile route (this file's own fixture,
# docs/terra-head-to-head.md §4) matched nothing and every profile pin fell through to a refusal. Falsified
# by reverting `_stated` to that tuple: "profile-stated pin satisfies the guard" flips to a failure (exit 1)
# while every refusal case above stays green, which is the shape of a control that is watching this and
# only this.
run "profile-stated pin satisfies the guard (step 07 builds with no --image)" 0 "$R/gatk-sv:YOUR_TAG" \
    "$PY" terra/batch_rerun_step.py --step 07 show
run "and it says so: that pin is announced as profile/env, not --image" 0 "sv_pipeline_docker=profile/env" \
    "$PY" terra/batch_rerun_step.py --step 07 show
run "a --image pin is announced as --image (a profile ref cannot impersonate a per-run flag)" 0 \
    "sv_pipeline_docker=--image" \
    "$PY" terra/batch_rerun_step.py --step 07 show --image sv_pipeline_docker=$R/sv-pipeline:OTHER_TAG
# A profile value with no tag is a registry path that floats to whatever is current at pull time, so it
# is not a pin AT ALL: it never enters IMAGES, and the step falls into the ordinary `unpinned` refusal
# rather than the `untagged` one -- a distinction worth naming, because both refusals are correct and
# only one of them mentions the profile.
printf 'GSVTK_TERRA_NAMESPACE=your-namespace\nGSVTK_TERRA_WORKSPACE=w\nGSVTK_BRANCH=b\nGSVTK_IMAGE_REPO=%s/sv-pipeline\n' "$R" > "$TMP/untagged.env"
run "a profile value with NO TAG is no pin at all, so the step refuses as unpinned" 1 \
    "unpinned:  FilterBatchSites.sv_pipeline_docker" \
    env GSVTK_CONFIG="$TMP/untagged.env" "$PY" terra/batch_rerun_step.py --step 07 show
# The four labels config.resolve() can answer, asserted at the predicate itself: two count, two must not.
run "_stated() accepts exactly the two labels a user stated (measured set, not a guess)" 0 "PASS" \
    "$PY" -c "
import sys; sys.path.insert(0,'terra')
import batch_rerun_step as r
good = ['env', 'profile:/srv/x.env']
bad  = ['derived from PROJECT', 'unset', 'profile', 'profilex:/x', '']
assert all(r._stated(s) for s in good), [s for s in good if not r._stated(s)]
assert not any(r._stated(s) for s in bad), [s for s in bad if r._stated(s)]
print('PASS')"
# The captured refusal, reproduced: the exact command from the golden's README (no --image at all).
# The offline belt is dropped for THIS capture only, because terra.py prints one note on stderr when
# GSVTK_TERRA_API_ROOT differs from the default and the byte compare is about the tool's own words.
# `show` is the read-only mode: it builds a dict and prints it -- no request path runs at all, which is
# what the byte-identical body above is itself evidence of.
env -u GSVTK_TERRA_API_ROOT "$PY" terra/batch_rerun_step.py show > /dev/null 2> "$TMP/all.err"
# The refusal block, from its own first line to the end: the resolution block `announce()` adds is new
# stderr output, asserted by its own cases above, and cutting it off does not touch one byte of what the
# refusal itself says. `cmp`, not grep -- a refusal whose wording changes still fails here.
sed -n '/^image inputs are not fully pinned/,$p' "$TMP/all.err" > "$TMP/unpinned.err"
bytes "step 10 with no --image reproduces the captured refusal byte for byte" \
    "$GOLD/rerun-step10-unpinned.txt" "$TMP/unpinned.err"

# --------------------------------------- 4. an unknown step names the steps the map actually knows
run "--step 11 is refused, naming every step the map knows" 1 "unknown step '11': this map knows 06, 07, 08, 09, 10" \
    "$PY" terra/batch_rerun_step.py --step 11 show
run "--step with no argument is a usage error, not a KeyError" 1 "--step wants one of 06, 07, 08, 09, 10" \
    "$PY" terra/batch_rerun_step.py --step
run "a typo'd flag is still a usage error" 1 "unknown option '--steps'" \
    "$PY" terra/batch_rerun_step.py --steps 09 show

# ------------------------- 5. --help with NO config at all (the help path must not resolve one)
: > "$TMP/empty.env"
run "--help with GSVTK_CONFIG empty (no profile, no credentials, no network)" 0 "  --step NN" \
    env GSVTK_CONFIG= "$PY" terra/batch_rerun_step.py --help
run "--help with an empty profile FILE still prints --step and --entity" 0 "  --entity NAME" \
    env GSVTK_CONFIG="$TMP/empty.env" "$PY" terra/batch_rerun_step.py --help
run "and the flags this tool inherits from batch_configs stay documented" 0 \
    "--drop-branch-only-inputs" env GSVTK_CONFIG= "$PY" terra/batch_rerun_step.py --help
# POSITIVE CONTROL for that silence: the same empty profile DOES make a mode exit 4 naming a key, so
# "--help printed fine" is evidence about the help path and not about a resolver that stopped working.
run "control: with no config a MODE names the missing key (exit 4)" 4 "GSVTK_TERRA_NAMESPACE" \
    env GSVTK_CONFIG="$TMP/empty.env" "$PY" terra/batch_rerun_step.py --step 07 show
run "and an unknown step is refused before any of that (map only, no config)" 1 "unknown step '11'" \
    env GSVTK_CONFIG="$TMP/empty.env" "$PY" terra/batch_rerun_step.py --step 11 show

# ------------------------------------- 6. the 09 row: refused by name, and the guard CAN fire
# 09-MergeBatchSites is rooted in sample_set_set while GSVTK_BATCH names a sample_set. The refusal has
# to come BEFORE anything that could reach Terra, which the second assertion pins: the pre-check line
# only prints after target resolution, so seeing it would mean the guard had moved late.
# shellcheck disable=SC2086
run "--step 09 submit refuses by naming the missing cohort row and --entity" 1 \
    "pass --entity <cohort-name>" "$PY" terra/batch_rerun_step.py --step 09 submit --confirm $PIN09
# shellcheck disable=SC2086
run_absent "and it refused before the Terra-side pre-check could run" 1 "pre-check" \
    "$PY" terra/batch_rerun_step.py --step 09 submit --confirm $PIN09
# shellcheck disable=SC2086
run "the refusal names the entity type it wanted, not just 'missing argument'" 1 \
    "sample_set_set row" "$PY" terra/batch_rerun_step.py --step 09 submit --confirm $PIN09

# POSITIVE CONTROL, still offline: with a cohort row named, submit() really does run, and what reaches
# the submission call is the resolved config name, row and entity type. The submission call is replaced
# by a recorder and every other Terra entry point by a raiser, in process.
cat > "$TMP/submit_wiring.py" <<'PY'
"""09 with --entity: the resolved config/row/entity must reach the one call that would submit.
Same recorder discipline as scripts/probe_fixes.py -- the point is not to fake Terra, it is to make
"no request left this machine" observable."""
import os, sys
repo = os.environ["GSVTK_REPO_ROOT"]
sys.path.insert(0, os.path.join(repo, "kit"))
sys.path.insert(0, os.path.join(repo, "terra"))
sys.argv = ["batch_rerun_step.py", "--step", "09", "--entity", "your-cohort", "submit", "--confirm"]
import terra
import batch_rerun_step as rr

SENT = []


def _boom(name):
    def f(*a, **k):
        raise AssertionError("a real Terra request was attempted: %s%s" % (name, a))
    return f


for _n in ("create_workspace_config", "overwrite_workspace_config", "validate_config",
           "create_submission", "list_submissions", "get_submission"):
    setattr(terra.fapi, _n, _boom(_n))
terra.submit = lambda *a, **k: (SENT.append((a, k)), {"submissionId": "recorded-offline"})[1]
rr.CONFIRMED = True
rr.submit()
assert len(SENT) == 1, SENT
args, kwargs = SENT[0]
config, entity, etype = args[3], args[4], args[5]
assert (config, entity, etype) == ("09-MergeBatchSites-rerun", "your-cohort", "sample_set_set"), args
assert kwargs.get("confirm") is True, kwargs           # the tool never drops its own confirm
print("PASS submitted %s against %s (%s), 0 requests attempted" % (config, entity, etype))
PY
inproc "control: --step 09 --entity DOES reach submit(), with 09's config name, cohort row and entity type" \
    0 "$TMP/submit_wiring.py"

# And the same for a sample_set step: GSVTK_BATCH is the row, and no --entity is needed. Proves the
# refusal above is the missing row and not a broken code path.
cat > "$TMP/submit_row.py" <<'PY'
"""10 with no --entity: the row comes from GSVTK_BATCH and the entity type is sample_set."""
import os, sys
repo = os.environ["GSVTK_REPO_ROOT"]
sys.path.insert(0, os.path.join(repo, "kit"))
sys.path.insert(0, os.path.join(repo, "terra"))
sys.argv = ["batch_rerun_step.py", "submit", "--confirm"]
import terra
import batch_rerun_step as rr
SENT = []
terra.submit = lambda *a, **k: (SENT.append((a, k)), {"submissionId": "recorded-offline"})[1]
rr.CONFIRMED = True
rr.submit()
args = SENT[0][0]
assert (args[3], args[4], args[5]) == ("10-GenotypeBatch-rerun", "all_samples", "sample_set"), args
print("PASS submitted %s against %s (%s)" % (args[3], args[4], args[5]))
PY
inproc "control: a sample_set step takes its row from GSVTK_BATCH and needs no --entity" 0 \
    "$TMP/submit_row.py"

# POSITIVE CONTROL for the guards that exist to catch two tables disagreeing: each must be reachable,
# so each is fired by mutating one copy in process and requiring the refusal. Nothing is written: every
# mutation is an in-memory dict, in a child process, that dies with it.
cat > "$TMP/guards.py" <<'PY'
"""Three guards that only fire when a table is wrong -- fired here by making one wrong."""
import importlib, os, sys
repo = os.environ["GSVTK_REPO_ROOT"]
sys.path.insert(0, os.path.join(repo, "kit"))
sys.path.insert(0, os.path.join(repo, "terra"))
import steps
# (1) steps.RERUN_ROOT_ENTITY vs batch_configs.CONFIGS, mutated until they disagree.
sys.argv = ["batch_rerun_step.py", "--step", "08", "show"]
import batch_configs as tc
import batch_rerun_step as rr
tc.CONFIGS["08-FilterBatchSamples"]["rootEntityType"] = "sample_set_set"
try:
    rr.resolved()
    raise AssertionError("no disagreement was reported: the two tables can still drift silently")
except SystemExit as e:
    msg = str(e)
    assert "described two ways" in msg and "sample_set_set" in msg and "terra/steps.py" in msg, msg
# (2) a step the map knows but the chain builder ships no input map for -> nothing to POST.
tc.CONFIGS["08-FilterBatchSamples"]["rootEntityType"] = "sample_set"      # put (1) back
sys.argv = ["batch_rerun_step.py", "--step", "06", "show"]                # resolved() re-reads argv
importlib.reload(rr)          # a fresh module: the cache is per import, and the refusal left it empty
rr.tc.CONFIGS.pop("06-GenerateBatchMetrics")
try:
    rr.resolved()
    raise AssertionError("a step with no input map was accepted, so it would POST a body of nothing")
except SystemExit as e:
    assert "no input map for step 06" in str(e), e
# (3) root_entity() on a step missing from the table: refuses, never guesses `sample_set`.
sys.argv = ["batch_rerun_step.py", "show"]
steps.RERUN_ROOT_ENTITY.pop("07")
try:
    steps.root_entity("07")
    raise AssertionError("a step with no root entity fell through to a guess")
except SystemExit as e:
    # The refusal must name the step AND where to fix it. It used to point at RERUN_ROOT_ENTITY in this
    # file; the table is derived from the profile now, so the sentence points at `rootEntityType` in the
    # profile instead -- same guarantee (a reader can act on it), one fewer stale pointer.
    assert "07" in str(e) and "rootEntityType" in str(e) and ".json" in str(e), e
print("PASS 3 guards fired: table disagreement, missing input map, missing root entity")
PY
inproc "controls: the disagreement / no-input-map / no-root-entity guards all fire when provoked" \
    0 "$TMP/guards.py"

# `--entity ""` is the shape a script produces when it passes --entity "$ROW" with ROW unset. It used to be
# indistinguishable from not passing --entity at all, and the fall-through was the workspace's default batch
# row -- a different cohort, submitted, reported as accepted. Measured before the fix: `--step 10 --entity ''
# submit` reached terra.submit with row='all_samples'. The control below is what keeps this a guard rather
# than a wall: a named row must still go straight through.
cat > "$TMP/empty_entity.py" <<'PY'
"""--entity given an empty value must refuse; a named row must still pass."""
import os, sys
repo = os.environ["GSVTK_REPO_ROOT"]
sys.path.insert(0, os.path.join(repo, "kit"))
sys.path.insert(0, os.path.join(repo, "terra"))
import batch_rerun_step as rr
step = {"step": "10", "workflow": "GenotypeBatch", "root_entity": "sample_set"}
sys.argv = ["batch_rerun_step.py", "--step", "10", "--entity", "", "submit", "--confirm"]
try:
    fell = rr.entity(step)
except SystemExit as e:
    assert "empty value" in str(e) and "ROW" in str(e), str(e)
else:
    raise AssertionError("--entity '' fell through to %r: a different cohort, submitted" % (fell,))
sys.argv = ["batch_rerun_step.py", "--step", "10", "--entity", "my_cohort", "submit"]
assert rr.entity(step) == "my_cohort", rr.entity(step)
print("PASS --entity with an empty value is refused; a named row is still honoured")
PY
inproc "--entity with an empty value is refused, not defaulted to the workspace's batch row" \
    0 "$TMP/empty_entity.py"

# The resolution line must point at the tool that CAN check the attributes a mid-chain step reads --
# derived from batch_configs' own maps, so it cannot rot into a sentence about a different chain.
run "a mid-chain step names the upstream steps whose outputs it reads (and the check that proves them)" 1 \
    "reads chain outputs of: 06-GenerateBatchMetrics, 08-FilterBatchSamples" \
    "$PY" terra/batch_rerun_step.py --step 09 show
run "and it points at batch_check_inputs.py rather than claiming the attributes exist" 1 \
    "prove those attributes exist first: python terra/batch_check_inputs.py --step 09" \
    "$PY" terra/batch_rerun_step.py --step 09 show
run "a step fed by the freeze loop says so, naming the frozen file count not a chain step" 0 \
    "frozen baseline files: 4 (published by batch_freeze.py)" \
    "$PY" terra/batch_rerun_step.py --step 10 show $PIN10

printf 'rerun: %s ok, %s failed\n' "$ok" "$fail"
# What this pair does and does not prove, stated exactly, because an earlier version of this comment
# understated its own blindness. `reached` exceeds `declared` by the DECLARED surplus of 4 (one site inside the
# `for s in 06 07 08 09 10` sweep), and the surplus has to match EXACTLY — measured: declaring 3 fails,
# declaring 6 fails, declaring 4 passes. Consequences:
#   - Hiding an assertion behind a condition that never holds is caught from the FIRST one, because 43 against
#     40 is a surplus of 3, which this file does not declare. An adversarial review measured 4 hideable under
#     the previous rule (any surplus tolerated); exact matching is what closed that.
#   - DELETING a call site is still NEVER caught, because declared and reached drop together and the surplus
#     stays 4. All 40 sites in this file can be deleted and the pair still prints an all-clear. The pair's
#     teeth are against sites that exist and do not run — see the header of scripts/selftest.d/declared.sh and
#     docs/gap-ledger.md C10.
# The whole-phase bail further up also exits 0, deliberately: a machine without `firecloud` must still be able
# to run the offline gate. That is why the bail line itself says a SKIP is not a pass in CI, and why the
# declared/reached line beside it names all 40.
gsvtk_declared_report "rerun" "$SELF" "$((ok + fail))" 0 || fail=$((fail + 1))
[ "$fail" -eq 0 ] || exit 1
exit 0
