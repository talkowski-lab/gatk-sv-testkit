#!/usr/bin/env bash
# scripts/selftest.d/rerun.sh — assertions about terra/batch_rerun_step.py and the step map.
#
# STANDALONE:  bash scripts/selftest.d/rerun.sh .venv/bin/python
# prints its own tally and exits nonzero on any failure. bash 3.2 compatible, no arrays (an empty
# array under `set -u` is an "unbound variable" on stock macOS bash).
#
# Nothing here touches the network: only `show` and early refusals run as real commands, `submit` cases
# replace Terra entry points in process (recorders or raisers, or a capture at fiss's request boundary),
# and GSVTK_TERRA_API_ROOT points at a closed local port. The profile is a placeholder
# (scripts/selftest.d/fixtures/rerun.profile.env).
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
# Same interpreter rule as `make helpsweep`: prefer ./.venv when the caller's python3 lacks firecloud.
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1 && [ -x .venv/bin/python ] \
   && .venv/bin/python -c 'import firecloud' >/dev/null 2>&1; then PY=.venv/bin/python; fi
# Declared-vs-reached accounting (see declared.sh). SELF is absolute so it works from any cwd.
SELF=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")
. "$(dirname "$SELF")/declared.sh"
# The verdict-recording callees, as derived by
#   gsvtk_declared_funset scripts/selftest.d/rerun.sh | awk '{print $1}'
# The reporter re-derives the set on every run and fails if it disagrees with this line.
GSVTK_DECL_FUNCS="run run_absent bytes inproc"
# The whole-phase bail below withholds everything after this line, so it is declared as a region.
gsvtk_guard_to_end "rerun-no-firecloud"
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1; then
    printf 'rerun: SKIP — %s cannot import firecloud (python -m pip install -r requirements.txt)\n' "$PY"
    printf 'rerun: 0 ok, 0 failed, 1 skipped (a SKIP here is not a pass in CI)\n'
    # Run the declared/reached report on the bail path too. init creates its own temp dir here, and the
    # verdict totals are 0 because nothing has been recorded yet.
    gsvtk_declared_init
    gsvtk_note_skip "rerun-no-firecloud"
    gsvtk_declared_report "rerun" "$SELF" 0 1 || exit 1
    exit 0
fi

FIX=scripts/selftest.d/fixtures/rerun.profile.env
GOLD=scripts/selftest.d/golden
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# The site ledger lives in this phase's temp dir, so the EXIT trap above cleans it up.
gsvtk_declared_init "$TMP"

export GSVTK_CONFIG="$FIX"
export GSVTK_WORK="$TMP/work"
export GSVTK_REPO_ROOT="$ROOT"
# Offline guard (see header): nothing legitimate reaches this address.
export GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/"
unset GSV_WDL_VERSION GSVTK_GATK_SV_CHECKOUT

# Placeholder image refs that satisfy the pin guard.
R=us.gcr.io/your-dev-project/test
PIN10="--image gatk_docker=$R/gatk:YOUR_TAG --image sv_base_mini_docker=$R/sv-base-mini:YOUR_TAG --image sv_pipeline_docker=$R/sv-pipeline:YOUR_TAG"
PIN09="$PIN10"
PIN07="--image sv_pipeline_docker=$R/sv-pipeline:YOUR_TAG"

ok=0; fail=0; skipped=0; QUIET=

# run DESC WANT_RC NEEDLE CMD... — checks the exit code AND a line of output.
run() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # the line that called run()
    local desc="$1" want="$2" needle="$3"; shift 3
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    if [ "$rc" != "$want" ]; then
        fail=$((fail + 1))
        [ -n "$QUIET" ] || { printf '  FAIL  %s (exit %s, want %s)\n' "$desc" "$rc" "$want"
                             printf '%s\n' "$out" | head -6 | sed 's/^/          /'; }
    elif [ -n "$needle" ] && ! grep -qF -- "$needle" <<< "$out"; then
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
    gsvtk_note_site "${BASH_LINENO[0]}"   # see run() above
    local desc="$1" want="$2" absent="$3"; shift 3
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    if [ "$rc" != "$want" ]; then
        fail=$((fail + 1)); printf '  FAIL  %s (exit %s, want %s)\n' "$desc" "$rc" "$want"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    elif grep -qF -- "$absent" <<< "$out"; then
        fail=$((fail + 1)); printf '  FAIL  %s (the output says %s, which it must not)\n' "$desc" "$absent"
        printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# bytes DESC FILE_A FILE_B — the byte-identity claim, not "looks the same".
bytes() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see run() above
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
    gsvtk_note_site "${BASH_LINENO[0]}"   # see run() above
    local desc="$1" want="$2"; shift 2
    local out rc
    out="$("$PY" "$@" 2>&1)"; rc=$?
    if [ "$rc" != "$want" ]; then
        fail=$((fail + 1)); printf '  FAIL  %s (exit %s, want %s)\n' "$desc" "$rc" "$want"
        printf '%s\n' "$out" | head -8 | sed 's/^/          /'
    elif ! grep -qF -- 'PASS' <<< "$out"; then
        fail=$((fail + 1)); printf '  FAIL  %s (exit %s but it never printed PASS)\n' "$desc" "$rc"
        printf '%s\n' "$out" | head -8 | sed 's/^/          /'
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# ------------------------------------------------------------------ the harness's own harness
# Prove both detectors can fail before trusting either.
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
# The body a step-10 rerun would POST, captured before per-step data moved into terra/steps.py.
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
# The default is step 10: the flag spelled out gives the same bytes.
# shellcheck disable=SC2086
"$PY" terra/batch_rerun_step.py --step 10 show $PIN10 > "$TMP/step10-explicit.json" 2>/dev/null
bytes "--step 10 explicitly == no --step at all (the default did not move)" \
    "$TMP/step10.json" "$TMP/step10-explicit.json"
# stdout carries only the JSON, so it can be piped to jq.
inproc "that stdout is the config JSON, with the name and root entity the golden says" 0 -c \
    'import json,sys
d = json.load(open(sys.argv[1]))
assert d["name"] == "10-GenotypeBatch-rerun", d["name"]
assert d["rootEntityType"] == "sample_set", d["rootEntityType"]
assert d["useCallCache"] is False, "useCallCache drifted off the default"
print("PASS")' "$TMP/step10.json"

# --------------------------------------------------- 2. per-step name / entity / methodUri / prefix
# Expectations are stated independently of the code. The URI has every slash %-encoded, which Rawls
# requires (a malformed one is a 404 "Cannot get dockstore://... from method repo").
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

# ---------------- 2b. the step map and the config map agree
# terra/steps.py and profiles/<module>.json both carry step -> workflow -> root entity; a mismatch
# fails here rather than at submission time.
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

# Positive control: a module profile naming a sixth step changes what the map reports, which shows
# the profile is the source of the map.
mkdir -p "$TMP/sixsteps"
"$PY" - "$TMP/sixsteps/genotyping.json" <<'PY'
import json, sys
d = json.load(open("profiles/genotyping.json"))
s = json.loads(json.dumps(d["steps"][0]))
s["step"], s["wdl"], s["workflow"] = "11-ExtraStep", "ExtraStep", "ExtraStep"
s.pop("entity_row", None)        # deliberately no row source: the leg below asserts how that PRINTS
# The name key is part of what makes a step a step now: `terra/steps.py` derives the entity type from it,
# so an entry that binds only `workspace.*` is refused by section 6b's legs, not mapped.
s["inputs"] = {"ExtraStep.batch": "this.sample_set_id",
               "ExtraStep.ped_file": "workspace.cohort_ped_file"}
s["outputs"] = {"ExtraStep.out": "workspace.extra_out"}
d["steps"].append(s)
json.dump(d, open(sys.argv[1], "w"), indent=1)
PY
run "a step added to the module profile appears in the step map (the table is data, not literals)" 0 \
    "11     ExtraStep" env GSVTK_MODULE_DIR="$TMP/sixsteps" "$PY" terra/steps.py
run "and a step whose profile names no row source prints the answer, not a blank column" 0 "PASS" \
    "$PY" -c "
import os, subprocess, sys
env = dict(os.environ, GSVTK_MODULE_DIR=sys.argv[1])
out = subprocess.run([sys.executable, 'terra/steps.py'], capture_output=True, text=True, env=env)
line = [l for l in out.stdout.splitlines() if l.startswith('11     ExtraStep')]
assert len(line) == 1, out.stdout
assert line[0].endswith('--entity'), line[0]
print('PASS %s' % line[0].strip())" "$TMP/sixsteps"
# An entry with no two-digit prefix must be refused, naming the entry, not silently skipped.
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

# ------------------------------------------ 2c. --tag TAG: two reruns of ONE step, side by side
# `--tag TAG` changes exactly two names, both derived in `terra/steps.py`, so two reruns of one step
# don't overwrite each other:
#     config    NN-<Wf>-TAG       default  NN-<Wf>-rerun
#     outputs   this.<attr>_TAG   default  this.<attr>_new      (the tag is appended after `_new`)
# Inputs are unchanged. With no flag the output is byte-identical to the pre-`--tag` code.
RUNTAG=h2h
echo
echo "selftest: rerun: --tag, i.e. two runs of one step that do not overwrite each other"

# Both bodies of one step, written by one python call and compared with `cmp`.
cat > "$TMP/both_bodies.py" <<'PY'
"""Build step 10's body twice -- no tag, then `--tag` -- and split each into its input and output maps.

    python both_bodies.py IN_PLAIN IN_TAGGED OUT_PLAIN OUT_TAGGED      # prints PASS with the counts

The point of writing four FILES is that the assertions after this one are `cmp` assertions. "the input
map did not move" and "the output map did" are claims about sets of bindings, and the only recorder
that can make them is one that compares the whole sorted map, not one that greps for a name chosen by
the same code being graded.
"""
import json
import os
import subprocess
import sys

repo = os.environ["GSVTK_REPO_ROOT"]
REG = "us.gcr.io/your-dev-project/test"                      # placeholder registry, as the fixture
PINS = ["--image", "gatk_docker=%s/gatk:YOUR_TAG" % REG,
        "--image", "sv_base_mini_docker=%s/sv-base-mini:YOUR_TAG" % REG,
        "--image", "sv_pipeline_docker=%s/sv-pipeline:YOUR_TAG" % REG]


def body(extra):
    cmd = [sys.executable, os.path.join(repo, "terra", "batch_rerun_step.py")] + extra + PINS + ["show"]
    out = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    assert out.returncode == 0, (out.returncode, out.stdout[:200], out.stderr[-500:])
    return json.loads(out.stdout)


def dump(path, mapping):
    with open(path, "w") as fh:
        fh.write("".join("%s = %s\n" % (k, v) for k, v in sorted(mapping.items())))


plain, tagged = body([]), body(["--tag", os.environ["GSVTK_RUNTAG"]])
assert plain["name"] != tagged["name"], (plain["name"], tagged["name"])
dump(sys.argv[1], plain["inputs"]); dump(sys.argv[2], tagged["inputs"])
dump(sys.argv[3], plain["outputs"]); dump(sys.argv[4], tagged["outputs"])
print("PASS %d inputs, %d outputs each way (%s vs %s)"
      % (len(plain["inputs"]), len(plain["outputs"]), plain["name"], tagged["name"]))
PY
# shellcheck disable=SC2086   # GSVTK_RUNTAG is a literal set a few lines above, not user input
GSVTK_RUNTAG="$RUNTAG" "$PY" "$TMP/both_bodies.py" "$TMP/in-plain.txt" "$TMP/in-tagged.txt" \
    "$TMP/out-plain.txt" "$TMP/out-tagged.txt" > "$TMP/both.log" 2>&1
run "one fixture built step 10's body with and without --tag (the four files above are real)" 0 \
    "PASS" /bin/cat "$TMP/both.log"
bytes "the INPUT map is IDENTICAL with and without --tag: a tagged run reads the chain's attributes" \
    "$TMP/in-plain.txt" "$TMP/in-tagged.txt"
# Positive control for the comparison above: the output maps must differ.
run "and the OUTPUT map is NOT identical (so the input comparison above compared two different runs)" \
    1 "" cmp -s "$TMP/out-plain.txt" "$TMP/out-tagged.txt"
run "and the inputs are not an empty map either: 16+ bindings, so 'identical' is not 'both empty'" 0 \
    "PASS" "$PY" -c "
import sys
text = open(sys.argv[1]).read()
n = len([l for l in text.splitlines() if ' = ' in l])
assert n >= 16, 'only %d input bindings in the compared map: the identity above proves nothing' % n
assert open(sys.argv[2]).read() == text
print('PASS %d input bindings, byte-for-byte equal both ways' % n)" "$TMP/in-plain.txt" "$TMP/in-tagged.txt"

# `show` must state the config name and attribute names it would use.
# shellcheck disable=SC2086
run "show --tag names the config it would POST, and says which flag made it that name" 0 \
    "config 10-GenotypeBatch-$RUNTAG  (--tag $RUNTAG)" \
    "$PY" terra/batch_rerun_step.py --tag "$RUNTAG" show $PIN10
run "and the output attribute names it would write, named rather than counted" 0 \
    "this.genotyped_depth_vcf_new_$RUNTAG" \
    "$PY" terra/batch_rerun_step.py --tag "$RUNTAG" show $PIN10
run "naming the tag on that line too, so a tagged write is not mistakable for the default one" 0 \
    "output attribute(s), tagged '$RUNTAG' (--tag)" \
    "$PY" terra/batch_rerun_step.py --tag "$RUNTAG" show $PIN10
run_absent "and the name the untagged run owns never appears in a tagged run's output" 0 \
    "10-GenotypeBatch-rerun" "$PY" terra/batch_rerun_step.py --tag "$RUNTAG" show $PIN10
# The untagged run prints the same two lines, in their default spelling.
run "show with NO --tag prints the same two answers in their default spelling" 0 \
    "writes 10 output attribute(s) (no --tag): this.genotyped_depth_vcf_new" \
    "$PY" terra/batch_rerun_step.py show $PIN10

# Per step, both names, so the tag is checked for every step and not only step 10.
cat > "$TMP/tag_name.py" <<'PY'
"""One step's config name under `--tag`, against a name TYPED IN THE SHELL, not read off the tool."""
import json
import os
import subprocess
import sys

step, wf, tag, want = sys.argv[1:5]
repo = os.environ["GSVTK_REPO_ROOT"]
out = subprocess.run(
    [sys.executable, os.path.join(repo, "terra", "batch_rerun_step.py"), "--step", step,
     "--tag", tag, "show", "--allow-unpinned-docker"],
    cwd=repo, capture_output=True, text=True)
assert out.returncode == 0, (out.returncode, out.stdout[:200], out.stderr[-500:])
body = json.loads(out.stdout)
assert body["name"] == want, (body["name"], want)
assert body["name"] != "%s-%s-rerun" % (step, wf), \
    "the tag built the name the untagged run already owns: %r -- nothing was separated" % body["name"]
line = [l for l in out.stderr.splitlines() if l.startswith("[rerun] step ")]
assert len(line) == 1, out.stderr[:300]
assert ("config " + want) in line[0], "the resolution line does not name the body: %s" % line[0]
assert "(--tag " + tag + ")" in line[0], "the resolution line hides which flag named it: %s" % line[0]
print("PASS %s -> %s" % (step, want))
PY
n=0
for s in 06 07 08 09 10; do
    wf=$("$PY" -c "import sys; sys.path.insert(0,'terra'); import steps; print(steps.workflow('$s'))")
    inproc "step $s with --tag $RUNTAG POSTs ${s}-${wf}-${RUNTAG} (typed here; NOT ${s}-${wf}-rerun)" 0 \
        "$TMP/tag_name.py" "$s" "$wf" "$RUNTAG" "${s}-${wf}-${RUNTAG}"
    n=$((n + 1))
done
run "all five steps were checked under a tag (not a loop that ran zero times)" 0 "5" \
    /usr/bin/printf '%s' "$n"

cat > "$TMP/tag_outputs.py" <<'PY'
"""What one step WRITES under `--tag`: same keys, EVERY value `_<tag>`, derived from the profile's map.

The expectation is the rule `--tag` promises -- `this.foo_new` becomes `this.foo_new_TAG` -- typed out
here and applied to every entry, with the untagged body of the SAME step as the reference. A leg that
asserted only "some output changed" would pass with one attribute renamed and the other nine on top of
the run it is supposed to sit beside.
"""
import json
import os
import subprocess
import sys

step, wf, tag = sys.argv[1:4]
repo = os.environ["GSVTK_REPO_ROOT"]


def body(extra):
    out = subprocess.run(
        [sys.executable, os.path.join(repo, "terra", "batch_rerun_step.py"), "--step", step] + extra
        + ["show", "--allow-unpinned-docker"],
        cwd=repo, capture_output=True, text=True)
    assert out.returncode == 0, (out.returncode, out.stderr[-500:])
    return json.loads(out.stdout)


plain, tagged = body([]), body(["--tag", tag])
assert list(tagged["outputs"]) == list(plain["outputs"]), \
    "the workflow's output KEYS moved; --tag names attributes, not the WDL's outputs"
untouched = [k for k in plain["outputs"] if tagged["outputs"][k] == plain["outputs"][k]]
assert not untouched, \
    "%s: %d/%d output(s) land on the SAME attribute with and without the tag (%s), so this run "\
    "overwrites the one it is meant to sit beside" % (step, len(untouched), len(plain["outputs"]),
                                                      untouched[:3])
wrong = [(k, plain["outputs"][k], tagged["outputs"][k]) for k in plain["outputs"]
         if tagged["outputs"][k] != plain["outputs"][k] + "_" + tag]
assert not wrong, "not `<attribute>_TAG` for: %r" % (wrong[:3],)
bad = [v for v in tagged["outputs"].values() if not v.startswith(("this.", "workspace."))]
assert not bad, "a tagged output left the binding grammar it started in: %r" % (bad[:3],)
print("PASS %s: %d/%d output attributes tagged (%s)" % (step, len(tagged["outputs"]),
                                                        len(plain["outputs"]),
                                                        sorted(tagged["outputs"].values())[0]))
PY
n=0
for s in 06 07 08 09 10; do
    wf=$("$PY" -c "import sys; sys.path.insert(0,'terra'); import steps; print(steps.workflow('$s'))")
    inproc "step $s with --tag $RUNTAG writes EVERY output as <attribute>_$RUNTAG (none left on the default)" \
        0 "$TMP/tag_outputs.py" "$s" "$wf" "$RUNTAG"
    n=$((n + 1))
done
run "all five steps' output maps were checked under a tag" 0 "5" /usr/bin/printf '%s' "$n"

# The tag goes into a config name and an attribute name, so shapes either would reject are refused
# up front. An empty `--tag` matters most: it would silently fall back to the default (overwriting) names.
run "--tag with no argument is a usage error" 1 "--tag wants a run name" \
    "$PY" terra/batch_rerun_step.py show --tag
run "--tag with an EMPTY value is refused, not read as 'no tag' (the unset-variable trap)" 1 \
    "An empty tag resolves to the DEFAULT config name" "$PY" terra/batch_rerun_step.py --tag "" show
run "and it says what an empty tag would have done, not just that it is empty" 1 \
    "overwrites the one already there" "$PY" terra/batch_rerun_step.py --tag "" show
run "--tag with a hyphen is refused: legal in a config name, refused as an attribute name" 1 \
    "letters, digits and underscore only" "$PY" terra/batch_rerun_step.py --tag rc-1 show
run "--tag rerun is refused: it spells the name the untagged run already owns" 1 \
    "an UNTAGGED rerun already uses" "$PY" terra/batch_rerun_step.py --tag rerun show
# The same refusals via `resolved()`, which `submit` calls directly and which reads argv itself.
run "a direct call to resolved() refuses an empty --tag too (probes do not go through the parser)" 1 \
    "resolved to an empty value" "$PY" -c "
import sys
sys.path.insert(0, 'terra'); sys.path.insert(0, 'kit')
import batch_rerun_step as rr
rr.sys.argv = ['batch_rerun_step.py', '--step', '10', '--tag', '', 'submit', '--confirm']
rr.resolved()
print('NO REFUSAL')"

# Per-step dump files must carry the tag too, or two tagged runs of one step overwrite each other's record.
inproc "the dump file stem carries the tag, and is exactly the step when there is none" 0 -c "
import importlib, sys
sys.path.insert(0, 'terra'); sys.path.insert(0, 'kit')
import batch_rerun_step as rr
rr.sys.argv = ['batch_rerun_step.py', '--step', '10', '--tag', 'h2h', 'submit', '--confirm']
stem = rr._run_stem(rr.resolved())
assert stem == '10.h2h', stem
importlib.reload(rr)
rr.sys.argv = ['batch_rerun_step.py', '--step', '10', 'submit', '--confirm']
assert rr._run_stem(rr.resolved()) == '10', 'the no-flag dump name moved: %r' % rr._run_stem(rr.resolved())
print('PASS 10.h2h tagged / 10 untagged')"

# -------------------------------------------------------------------- 3. the pin guard fires for OTHER steps too
# The fixture's tagged GSVTK_IMAGE_REPO / GSVTK_GATK_IMAGE_REPO pin step 07's one docker, so 07 builds.
# Steps 08 and 09 refuse: `sv_base_mini_docker` and `linux_docker` can only come from --image.
run "step 08 refuses unpinned *_docker, naming its own keys" 1 "unpinned:  FilterBatchSamples.linux_docker" \
    "$PY" terra/batch_rerun_step.py --step 08 show
run "step 09 refuses on the docker no profile can name" 1 "unpinned:  MergeBatchSites.sv_base_mini_docker" \
    "$PY" terra/batch_rerun_step.py --step 09 show
# Positive control: once its one docker is pinned, the step builds.
# shellcheck disable=SC2086
run "control: step 07 builds its body once its one docker is tagged" 0 "07-FilterBatchSites-rerun" \
    "$PY" terra/batch_rerun_step.py --step 07 show $PIN07
run "an image pinned but UNTAGGED is refused as a floating reference, not a pin" 1 "pinned but unversioned" \
    "$PY" terra/batch_rerun_step.py --step 07 show --image sv_pipeline_docker=$R/sv-pipeline

# --------------------- 3b. a pin the PROFILE states counts, and one it infers does not
# config.resolve() reports sources as `profile:<path>` and `derived from PROJECT`; `_stated` must accept
# the first, so a profile pin satisfies the guard.
run "profile-stated pin satisfies the guard (step 07 builds with no --image)" 0 "$R/gatk-sv:YOUR_TAG" \
    "$PY" terra/batch_rerun_step.py --step 07 show
run "and it says so: that pin is announced as profile/env, not --image" 0 "sv_pipeline_docker=profile/env" \
    "$PY" terra/batch_rerun_step.py --step 07 show
run "a --image pin is announced as --image (a profile ref cannot impersonate a per-run flag)" 0 \
    "sv_pipeline_docker=--image" \
    "$PY" terra/batch_rerun_step.py --step 07 show --image sv_pipeline_docker=$R/sv-pipeline:OTHER_TAG
# A profile value with no tag floats to whatever is current at pull time, so it is not a pin: the step
# gets the ordinary `unpinned` refusal, not the `untagged` one.
printf 'GSVTK_TERRA_NAMESPACE=your-namespace\nGSVTK_TERRA_WORKSPACE=w\nGSVTK_BRANCH=b\nGSVTK_IMAGE_REPO=%s/sv-pipeline\n' "$R" > "$TMP/untagged.env"
run "a profile value with NO TAG is no pin at all, so the step refuses as unpinned" 1 \
    "unpinned:  FilterBatchSites.sv_pipeline_docker" \
    env GSVTK_CONFIG="$TMP/untagged.env" "$PY" terra/batch_rerun_step.py --step 07 show
# A config-layer value ending in digits is a family whose version is its path segment.
# `images_from_config` asks the predicate without a declaration, so it answers `''` and the step refuses
# as `unpinned`. The second leg shows the same ref answering `path` once a declaration is supplied.
printf 'GSVTK_TERRA_NAMESPACE=your-namespace\nGSVTK_TERRA_WORKSPACE=w\nGSVTK_BRANCH=b\nGSVTK_IMAGE_REPO=%s/ubuntu1804\n' "$R" > "$TMP/pathversioned.env"
run "a config-layer value versioned-by-path is DECLINED (a policy: no declaration at this call site)" 1 \
    "unpinned:  FilterBatchSites.sv_pipeline_docker" \
    env GSVTK_CONFIG="$TMP/pathversioned.env" "$PY" terra/batch_rerun_step.py --step 07 show
run "...and the predicate itself answers \`path\` for it, so the refusal above is policy, not blindness" 0 "PASS" \
    "$PY" -c "
import sys; sys.path.insert(0,'kit')
import module_profile as m
family = 'us.gcr.io/p/ubuntu1804'
assert m.ref_version_kind(family, m.VERSIONED_BY_PATH) == 'path', m.ref_version_kind(family)
assert m.ref_version_kind(family) == '', m.ref_version_kind(family)
assert m.ref_version_kind('us.gcr.io/p/x:1') == 'tag'
assert m.ref_version_kind('us.gcr.io/p/x@sha256:' + 'a'*64) == 'digest'
print('PASS')"

# The four labels config.resolve() can answer: two count, two must not.
run "_stated() accepts exactly the two labels a user stated (measured set, not a guess)" 0 "PASS" \
    "$PY" -c "
import sys; sys.path.insert(0,'terra')
import batch_rerun_step as r
good = ['env', 'profile:/srv/x.env']
bad  = ['derived from PROJECT', 'unset', 'profile', 'profilex:/x', '']
assert all(r._stated(s) for s in good), [s for s in good if not r._stated(s)]
assert not any(r._stated(s) for s in bad), [s for s in bad if r._stated(s)]
print('PASS')"
# Reproduce the captured refusal with the exact command from the golden's README (no --image).
# GSVTK_TERRA_API_ROOT is unset for this capture only, because terra.py prints a note on stderr when it
# differs from the default. `show` makes no requests.
env -u GSVTK_TERRA_API_ROOT "$PY" terra/batch_rerun_step.py show > /dev/null 2> "$TMP/all.err"
# Compare the refusal block from its first line to the end with `cmp`, so a wording change fails.
sed -n '/^image inputs are not fully pinned/,$p' "$TMP/all.err" > "$TMP/unpinned.err"
bytes "step 10 with no --image reproduces the captured refusal byte for byte" \
    "$GOLD/rerun-step10-unpinned.txt" "$TMP/unpinned.err"

# ---------------- 3c. the two states the pin guard answers, reported separately
# `google/cloud-sdk` (no tag or digest) stays refused. `marketplace.gcr.io/google/ubuntu1804` (a GCE image
# family versioned by its path) is accepted only when the profile declares `versioned_by: "path"`, and
# `ref_version_kind` refuses that field on a ref whose path names no version. The planted profiles tag
# every other `*_docker`, so the only possible refusal is the one under test.
a26_plant() {     # a26_plant <dir> <image-key-suffix> <entry-as-json>
    "$PY" - "$TMP/$1" "$2" "$3" <<'PY'
import json, os, sys

out, suffix, entry = sys.argv[1], sys.argv[2], sys.argv[3]
os.makedirs(out, exist_ok=True)
doc = json.load(open(os.path.join(os.environ["GSVTK_REPO_ROOT"], "profiles",
                                 "single_sample.json")))
reg = "us.gcr.io/your-dev-project/test"
for step in doc["steps"]:
    for key in step.get("images") or {}:
        step["images"][key] = (json.loads(entry) if key.endswith(suffix)
                               else "%s/%s:YOUR_TAG" % (reg, key.split(".")[-1][:-7]))
json.dump(doc, open(os.path.join(out, "single_sample.json"), "w"))
print("PLANTED %s" % suffix)
PY
}
R01="$R/google/ubuntu1804"                    # a path that DOES name a version (the GCE family shape)
BAD01="google/cloud-sdk"                      # a path that names none: implicit `latest`
# Double-quoted JSON literals with the refs interpolated, so every entry parses.
a26_plant a26-path      linux_docker "{\"ref\": \"$R01\", \"versioned_by\": \"path\"}"
a26_plant a26-launder   linux_docker "{\"ref\": \"$BAD01\", \"versioned_by\": \"path\"}"
a26_plant a26-tagged    linux_docker "{\"ref\": \"$R/ubuntu:TAG\", \"versioned_by\": \"path\"}"
a26_plant a26-marker    linux_docker "{\"ref\": \"--image\", \"versioned_by\": \"path\"}"
a26_plant a26-vocab     linux_docker "{\"ref\": \"$R01\", \"versioned_by\": \"registry\"}"
a26_plant a26-field     linux_docker "{\"ref\": \"$R01\", \"versioned_by\": \"path\", \"because\": \"trust me\"}"
# The same ref with no declaration: the loader refuses it (`UNTAGGED`).
a26_plant a26-plain     linux_docker "{\"ref\": \"$R/google/ubuntu1804\"}"   # same ref, no declaration

# Positive control: the same untagged ref with the declaration present builds the body.
run "POSITIVE CONTROL: a profile-declared path-versioned ref builds the body (no flag taken)" 0 \
    "01-GATKSVPipelineSingleSample-rerun" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-path" GSVTK_BRANCH=main \
    "$PY" terra/batch_rerun_step.py --step 01 show --entity NA12878
run "and the announcement names the carve-out as its own source, beside the ref it covers" 0 \
    "linux_docker=profile/images +PATH-VERSIONED (profile versioned_by)" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-path" GSVTK_BRANCH=main \
    "$PY" terra/batch_rerun_step.py --step 01 show --entity NA12878
run "and the ref in the body is the declared one, untagged, quoted like every other pin" 0 \
    "\\\"$R01\\\"" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-path" GSVTK_BRANCH=main \
    "$PY" terra/batch_rerun_step.py --step 01 show --entity NA12878
# The same untagged ref with no declaration is refused at load (`UNTAGGED`, exit 4).
run "the same untagged ref with NO declaration is refused at load, by the loader, exit 4" 4 \
    "UNTAGGED" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-plain" GSVTK_GATK_SV_CHECKOUT= \
    "$PY" kit/module_profile.py --print
# The two states print separately, and when only the second is in play the consent flag is not offered:
# it is consent to a workspace attribute answering, and this key already has a stated ref.
run "the PINNED BUT UNVERSIONED refusal says what the state is, and names the ref that is floating" 1 \
    "pinned but unversioned:  GATKSVPipelineSingleSample.linux_docker = $R/ubuntu1804-latest" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-path" GSVTK_BRANCH=main \
    "$PY" terra/batch_rerun_step.py --step 01 show --entity NA12878 \
    --image "GATKSVPipelineSingleSample.linux_docker=$R/ubuntu1804-latest"
run_absent "and that refusal does NOT offer --allow-unpinned-docker, which is consent to something else" 1 \
    "--allow-unpinned-docker" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-path" GSVTK_BRANCH=main \
    "$PY" terra/batch_rerun_step.py --step 01 show --entity NA12878 \
    --image "GATKSVPipelineSingleSample.linux_docker=$R/ubuntu1804-latest"
run "it names the FIELD that answers it, in the sentence that used to offer the flag" 1 \
    "versioned_by" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-path" GSVTK_BRANCH=main \
    "$PY" terra/batch_rerun_step.py --step 01 show --entity NA12878 \
    --image "GATKSVPipelineSingleSample.linux_docker=$R/ubuntu1804-latest"
run "and the FILE the field lives in, with the step it belongs to" 1 \
    "single_sample.json -> steps[step='01'].images" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-path" GSVTK_BRANCH=main \
    "$PY" terra/batch_rerun_step.py --step 01 show --entity NA12878 \
    --image "GATKSVPipelineSingleSample.linux_docker=$R/ubuntu1804-latest"
run "the carve-out does not excuse a FLOATING --image ref on the declared key" 1 \
    "pinned but unversioned:  GATKSVPipelineSingleSample.linux_docker = $R/ubuntu1804-latest" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-path" GSVTK_BRANCH=main \
    "$PY" terra/batch_rerun_step.py --step 01 show --entity NA12878 \
    --image "GATKSVPipelineSingleSample.linux_docker=$R/ubuntu1804-latest"
# Load-time refusals, exit 4 naming the field. `versioned_by` cannot turn `google/cloud-sdk` into a version.
run "REFUSED AT LOAD: versioned_by on a ref whose path names no version (google/cloud-sdk)" 4 \
    "names no version" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-launder" GSVTK_GATK_SV_CHECKOUT= \
    "$PY" kit/module_profile.py --print
run "and the load refusal names the key, the field and the value it could not read" 4 \
    "GATKSVPipelineSingleSample.linux_docker'] declares versioned_by='path'" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-launder" GSVTK_GATK_SV_CHECKOUT= \
    "$PY" kit/module_profile.py --print
run "REFUSED AT LOAD: versioned_by on an entry that states --image (the operator supplies that ref)" 4 \
    "states '--image' AND carries versioned_by" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-marker" GSVTK_GATK_SV_CHECKOUT= \
    "$PY" kit/module_profile.py --print
run "REFUSED AT LOAD: versioned_by with a value this build does not read, listing what it reads" 4 \
    "is 'registry', which this build does not read ('path')" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-vocab" GSVTK_GATK_SV_CHECKOUT= \
    "$PY" kit/module_profile.py --print
run "REFUSED AT LOAD: versioned_by beside a tag, which states two provenances for one ref" 4 \
    "which states its own version, and also carries versioned_by='path'" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-tagged" GSVTK_GATK_SV_CHECKOUT= \
    "$PY" kit/module_profile.py --print
run "REFUSED AT LOAD: a field name on the entry that nothing reads" 4 \
    "carries field(s) 'because', which this build does not read" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-field" GSVTK_GATK_SV_CHECKOUT= \
    "$PY" kit/module_profile.py --print
# `submit` refuses the second state too: the guard runs before the read-back and before any request.
run "submit refuses the unversioned state as well, with the same words" 1 \
    "pinned but unversioned" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-path" GSVTK_BRANCH=main \
    "$PY" terra/batch_rerun_step.py --step 01 submit --confirm --entity NA12878 \
    --image "GATKSVPipelineSingleSample.linux_docker=$R/ubuntu1804-latest"
run "and the outer read-only gate still refuses that submit, carve-out taken or not" 2 \
    "GSVTK_READ_ONLY" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-path" GSVTK_BRANCH=main \
    GSVTK_CONFIG="$FIX" GSVTK_WORK="$TMP/work-ro" GSVTK_REPO_ROOT="$ROOT" \
    GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/" \
    "$ROOT/gsvtk" --read-only terra rerun-submit --step 01 --entity NA12878 --confirm

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
# Positive control: the same empty profile makes a mode exit 4 naming a key, so the resolver still works.
run "control: with no config a MODE names the missing key (exit 4)" 4 "GSVTK_TERRA_NAMESPACE" \
    env GSVTK_CONFIG="$TMP/empty.env" "$PY" terra/batch_rerun_step.py --step 07 show
run "and an unknown step is refused before any of that (map only, no config)" 1 "unknown step '11'" \
    env GSVTK_CONFIG="$TMP/empty.env" "$PY" terra/batch_rerun_step.py --step 11 show

# ------------------------------------- 6. the 09 row: refused by name, and the guard CAN fire
# 09-MergeBatchSites is rooted in sample_set_set while GSVTK_BATCH names a sample_set. The refusal must
# come before anything that could reach Terra: the pre-check line only prints after target resolution.
# shellcheck disable=SC2086
run "--step 09 submit refuses by naming the missing row and --entity" 1 \
    "pass --entity <name>" "$PY" terra/batch_rerun_step.py --step 09 submit --confirm $PIN09
# shellcheck disable=SC2086
run_absent "and it refused before the Terra-side pre-check could run" 1 "pre-check" \
    "$PY" terra/batch_rerun_step.py --step 09 submit --confirm $PIN09
# shellcheck disable=SC2086
run "the refusal names the entity type it wanted, not just 'missing argument'" 1 \
    "sample_set_set row" "$PY" terra/batch_rerun_step.py --step 09 submit --confirm $PIN09

# Positive control, offline: with a cohort row named, submit() runs and the recorded submission call
# receives the resolved config name, row and entity type. Every other Terra entry point raises. The
# controls state their image pins and give submit() a matching workspace copy for the read-back guard.
cat > "$TMP/rerun_submit_lib.py" <<'PY'
"""Shared harness for this phase's `submit` legs: in process, 0 requests attempted.

The same discipline as scripts/probe_fixes.py's recorder -- the point is not to fake Terra, it is to
make "no request left this machine" observable. Every mutating fiss entry point raises, the submission
call records, and the config read-back answers with ONE payload the leg builds rather than with the
tool's own dict, so a leg that passes because the guard was unsatisfiable is caught by the leg whose
payload is deliberately made to agree.

A leg that needs the POST *body* rather than the submission *call* asks `setup(wire=True)`, which keeps
`terra.submit`/`create_submission` real and captures one layer lower, at fiss's request boundary.
Section 8 explains why that seam is the only one that can see the defect it grades.

The legs live in this directory, so `sys.path.insert(__file__'s dir)` finds this file.
"""
import copy
import io
import contextlib
import os
import sys

REG = "us.gcr.io/your-dev-project/test"                        # placeholder registry, as the fixture
PINSPEC = {"gatk_docker": REG + "/gatk:PINNED",
           "sv_base_mini_docker": REG + "/sv-base-mini:PINNED",
           "sv_pipeline_docker": REG + "/sv-pipeline:PINNED"}
TRACE = []           # ("read", coords) / ("submit", args), in the order they happened
WIRE = []            # (methcall, body) per POST, when a leg captures at fiss's request boundary


class Blocked(Exception):
    """Raised by a mutating Terra entry point: reaching it is the leg's failure."""


def _raiser(name):
    def f(*a, **k):
        raise Blocked("a mutating Terra call was attempted: %s%r" % (name, a))
    return f


def _record_submit(*a, **k):
    TRACE.append(("submit", a, k))
    return {"submissionId": "recorded-offline"}


class _WireResp:
    """The response a captured POST hands back, so `submit` finishes the way it would have."""

    status_code = 200
    text = '{"submissionId": "captured-at-the-wire"}'

    def json(self):
        return {"submissionId": "captured-at-the-wire", "message": "captured, never sent"}


def _capture_post(methcall, headers=None, root_url=None, **kwargs):
    WIRE.append((methcall, kwargs.get("json")))
    return _WireResp()


def wire_capture(terra):
    """Keep the real submission call and take the body one layer lower, at fiss's request boundary.

    Why this seam and not `create_submission`: the defect the call-cache legs grade is a DEFAULT INSIDE
    fiss (`create_submission(..., use_callcache=True)`, stated in the body it builds unconditionally),
    so a leg that records the arguments of the fiss call would pass while the body that would have gone
    over the wire said the opposite. Replacing `__post` keeps `create_submission` real -- which is the
    only way this phase sees the bytes Terra would receive -- and the other three verbs stay raisers, so
    a leg that reads or PUTs is a failure rather than a request. Nothing here can reach Terra: this
    phase's GSVTK_TERRA_API_ROOT is a closed port besides.
    """
    setattr(terra.fapi, "__post", _capture_post)
    for name in ("__get", "__put", "__delete"):
        setattr(terra.fapi, name, _raiser(name))
    return WIRE


def setup(step="10", extra_argv=(), exempt=False, wire=False, row_exists=True,
          mode="submit", module="", pins=None, keep=(), capture=None):
    """Import batch_rerun_step the way __main__ leaves it: argv, IMAGES, CLI_IMAGE_KEYS, CONFIRMED.

    Calling submit() directly skips that dispatch, and `IMAGES` is exactly what dispatch fills from the
    command line -- so a leg that forgot it would be testing a rerun with no image pinned at all.
    `exempt=True` leaves one *_docker unpinned and asks for it by flag, which is what dispatch does for
    `--allow-unpinned-docker`.

    `wire=True` is for the legs that need the POST body rather than the call: `terra.submit` and
    `create_submission` stay the real ones and the request boundary is captured (see `wire_capture`), so
    `create_submission` is NOT turned into a raiser in that mode.

    Three knobs the module-outside-the-chain legs (section 11) added, all inert at their defaults:
      * `module` -- `GSVTK_MODULE` for this leg. It is written BEFORE the import below because
        `kit/config.py` resolves the module once, at import, and `batch_configs` builds every map out of
        the profile that resolution picked; a leg that set it after the import would drive the chain's
        maps while believing it drove another module's.
      * `mode`  -- the verb `__main__` dispatches (`show|create|validate|submit`), because `create()` and
        `validate()` need the same argv-shaped state `submit()` does and the mode token is part of it.
      * `keep`  -- fiss entry points to leave REAL even though the verb is mutating. A leg that wants the
        POST *bytes* of `create` or `validate` keeps that one call real and lets `wire_capture` stop it at
        the request boundary; the rest stay raisers.
      * `capture` -- one capture path (or a list of them) for the `--image-from` door. They go into argv
        AND through the tool's own reader (`rr.apply_captures`), called HERE instead of hand-setting the
        new `CAPTURE_IMAGE_KEYS` global, because that function is what `__main__` dispatches to: it holds
        the refusal vocabulary and the precedence, and a harness that set the globals by hand would be
        grading a fixture rather than the door. It sits BELOW the raisers on purpose, so a leg that
        planted a bad capture takes the tool's real SystemExit with `TRACE` and `WIRE` still empty -- the
        in-process form of "refused before it read anything".

    With `module` set and no `pins`, the pin list is the step's own profile read through the tool's
    reader (`images_from_profile`) -- that profile marks EVERY `*_docker` `--image`, so the alternative was
    a second image list in this file, which is the thing that goes stale when a profile gains an input.
    """
    repo = os.environ["GSVTK_REPO_ROOT"]
    sys.path.insert(0, os.path.join(repo, "kit"))
    sys.path.insert(0, os.path.join(repo, "terra"))
    if module:
        os.environ["GSVTK_MODULE"] = module
    import terra
    import batch_rerun_step as rr
    cap_paths = [capture] if isinstance(capture, str) else list(capture or ())
    base = (["batch_rerun_step.py", "--step", step] + list(extra_argv)
            + [x for p in cap_paths for x in ("--image-from", p)]
            + [mode, "--confirm"] + (["--allow-unpinned-docker"] if exempt else []))
    # argv WITHOUT the pins first, because deriving the pins asks the tool which keys this step binds and
    # that answer comes from `resolved()`, which reads `--step`/`--tag`/`--entity` off argv. `resolved()`
    # caches those three and nothing else, so appending `--image` pairs afterwards cannot make the first
    # resolution stale -- and `IMAGES` is assigned below, exactly where __main__ assigns it.
    sys.argv = base
    if pins is None:
        # ALL the *_docker keys this step binds, not just the ones the profile marks `--image`: a leg
        # that means "a fully pinned body" has to pin the keys a profile STATES too. Deriving from [1]
        # alone was right while every entry was a marker, and quietly stopped pinning `linux_docker` the
        # day that entry declared its version by its path -- the legs below then died on `unpinned:`.
        stated, flag_only = rr.images_from_profile("")[:2]
        pins = ({k: REG + "/x:PINNED" for k in sorted(set(stated) | set(flag_only))}
                if module else PINSPEC)
    specs = sorted("%s=%s" % (k, v) for k, v in pins.items())
    if exempt:
        specs = [s for s in specs if "sv_base_mini" not in s]
    sys.argv = base + [x for s in specs for x in ("--image", s)]
    blocked = ["create_workspace_config", "overwrite_workspace_config", "validate_config",
               "list_submissions", "get_submission"]
    blocked = [n for n in blocked if n not in keep]
    if not wire:                       # the wire legs need the real create_submission
        blocked.append("create_submission")
    for name in blocked:
        setattr(terra.fapi, name, _raiser(name))
    if wire:
        wire_capture(terra)
    else:
        terra.submit = _record_submit
    rr.CONFIRMED = True
    # `submit` now pre-flights the entity before it writes anything, so every leg needs an answer to
    # `get_entity`. Default is "the row is there" -- the case the existing legs were written against. A
    # stub that always said "missing" would make the whole phase refuse, and a stub that raised would hide
    # the guard behind an error; both are fixtures agreeing with the thing under test.
    class _Row:
        status_code = 200 if row_exists else 404
        text = "" if row_exists else '{"message": "not found (selftest stub)"}'

    terra.fapi.get_entity = lambda *a, **k: _Row()
    rr.ALLOW_UNPINNED = exempt
    rr.IMAGES = rr.parse_images(specs, rr.resolved()["input_prefix"])
    rr.CLI_IMAGE_KEYS = set(rr.IMAGES)
    # The `--image-from` door, run through the tool's own reader rather than emulated here: `apply_captures`
    # fills `CAPTURE_IMAGE_KEYS` and decides every capture refusal, exactly as dispatch does, and it returns
    # only the keys the capture WINS (--image still beats it). See the `capture=` note above for why this
    # call sits after the raisers went in.
    if cap_paths:
        rr.IMAGES = dict(rr.IMAGES, **rr.apply_captures(cap_paths, rr.IMAGES))
    return terra, rr


REAL_TOP_LEVEL = ("deleted", "inputs", "methodConfigVersion", "methodRepoMethod", "name",
                  "namespace", "outputs", "prerequisites", "rootEntityType")


def server_copy(body, mutate=None):
    """A read-back payload shaped like a REAL method config, not like the body we just built.

    Shaping it from REAL_TOP_LEVEL is the whole point. This function used to start from
    `copy.deepcopy(body)`, so the fake workspace answered exactly the keys the body stated -- and
    `config_diffs` refuses on a deciding field the copy does not answer, which made the branch that
    catches an unsatisfiable field unreachable: every leg that should have walked into it strode
    past, and `submit` against any real workspace was in fact refusing unconditionally. A fixture
    that agrees with the thing under test by construction is this repo's oldest failure mode, and
    this one survived a whole review round.

    The key list is the top level of a payload read from a live workspace
    (`recon-live/live-config-01-SingleSample-devfix.json`, see `scripts/selftest.d/golden/README.md`
    for why that file is not versioned). There is no `workspace` member in a real read-back either,
    so this one has none.
    """
    held = {k: body.get(k) for k in REAL_TOP_LEVEL}
    # `namespace` is not a deciding field (the guard compares nothing but DECIDING), and the
    # generated leg module has no target-config object in scope here, so it gets a value that is
    # present-but-irrelevant rather than a name that does not exist.
    held["namespace"] = body.get("namespace") or "config-namespace"
    held["prerequisites"] = dict(body.get("prerequisites") or {})
    # Both maps are copied, not shared: the sweep mutators edit `held` in place, and a fixture that
    # aliases the body edits the body, which makes every comparison agree by reference -- the same
    # class of lie as the deepcopy it replaced, pointing the other way.
    held["inputs"] = copy.deepcopy(body["inputs"])
    held["outputs"] = copy.deepcopy(body["outputs"])
    held["deleted"] = None
    held["methodConfigVersion"] = 7               # bumped by the server on every overwrite
    held["methodRepoMethod"] = dict(body["methodRepoMethod"], snapshotId=None)
    missing = [k for k in ("name", "rootEntityType", "inputs", "outputs") if k not in body]
    assert not missing, \
        "the fake read-back cannot be shaped from a real config's members because the builder does " \
        "not state %s; deepcopy(body) would have hidden that by answering only what was asked" % missing
    if mutate:
        mutate(held)
    return held


def read_with(payload=None, raises=None):
    """Install the read-back: one call recorded, then either the payload or the exception."""
    import terra

    def _payload(ns, name, cns, cname):
        TRACE.append(("read", (ns, name, cns, cname)))
        if raises:
            raise raises
        return payload
    terra.config_payload = _payload


def run_submit(rr):
    """(refusal_text_or_None, stdout). A SystemExit is a refusal, which is what most legs want."""
    out = io.StringIO()
    refused = None
    try:
        with contextlib.redirect_stdout(out):
            rr.submit()
    except SystemExit as e:
        refused = str(e)
    return refused, out.getvalue()


def docker(held, which):
    """The prefixed input key for `which`, so a leg never hardcodes a step's workflow name."""
    hits = [k for k in held["inputs"] if k.endswith("." + which)]
    assert len(hits) == 1, hits
    return hits[0]
PY

cat > "$TMP/submit_wiring.py" <<'PY'
"""09 with --entity: the resolved config/row/entity must reach the one call that would submit."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

terra, rr = L.setup("09", extra_argv=("--entity", "your-cohort"))
L.read_with(L.server_copy(rr.make_body()))
refused, out = L.run_submit(rr)
assert refused is None, refused
sent = [t for t in L.TRACE if t[0] == "submit"]
assert len(sent) == 1, L.TRACE
args, kwargs = sent[0][1], sent[0][2]
config, entity, etype = args[3], args[4], args[5]
assert (config, entity, etype) == ("09-MergeBatchSites-rerun", "your-cohort", "sample_set_set"), args
assert kwargs.get("confirm") is True, kwargs           # the tool never drops its own confirm
assert [t[0] for t in L.TRACE] == ["read", "submit"], L.TRACE   # the read came first
print("PASS submitted %s against %s (%s), 0 requests attempted" % (config, entity, etype))
PY
inproc "control: --step 09 --entity DOES reach submit(), with 09's config name, cohort row and entity type" \
    0 "$TMP/submit_wiring.py"

# The same for a sample_set step: GSVTK_BATCH is the row and no --entity is needed.
cat > "$TMP/submit_row.py" <<'PY'
"""10 with no --entity: the row comes from GSVTK_BATCH and the entity type is sample_set."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

terra, rr = L.setup("10")
L.read_with(L.server_copy(rr.make_body()))
refused, out = L.run_submit(rr)
assert refused is None, refused
args = [t for t in L.TRACE if t[0] == "submit"][0][1]
assert (args[3], args[4], args[5]) == ("10-GenotypeBatch-rerun", "all_samples", "sample_set"), args
print("PASS submitted %s against %s (%s)" % (args[3], args[4], args[5]))
PY
inproc "control: a sample_set step takes its row from GSVTK_BATCH and needs no --entity" 0 \
    "$TMP/submit_row.py"

# Positive control for each table-consistency guard: mutate one copy in memory, in a child process,
# and require the refusal.
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
    # The two sides are named for what they are now: one derived from the step's bindings, one stated by
    # the profile. A refusal that said only "two copies disagree" would describe the pre-derivation code.
    assert "derived from the step's own `this.<etype>_id`" in msg, msg
    assert "the profile's stated answer" in msg, msg
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

# `--entity ""` is what `--entity "$ROW"` produces with ROW unset. It must be refused rather than fall
# through to the workspace's default batch row. A named row must still go straight through.
cat > "$TMP/empty_entity.py" <<'PY'
"""--entity given an empty value must refuse; a named row must still pass.

The step dict is the shape `resolved()` hands `entity()` -- step/workflow/root_entity/row_source --
because `row_source` is what the row resolution reads now. Leaving it out would grade a dict no caller
ever builds.
"""
import os, sys
repo = os.environ["GSVTK_REPO_ROOT"]
sys.path.insert(0, os.path.join(repo, "kit"))
sys.path.insert(0, os.path.join(repo, "terra"))
import batch_rerun_step as rr
step = {"step": "10", "workflow": "GenotypeBatch", "root_entity": "sample_set", "row_source": "BATCH"}
sys.argv = ["batch_rerun_step.py", "--step", "10", "--entity", "", "submit", "--confirm"]
try:
    fell = rr.entity(step)
except SystemExit as e:
    assert "empty value" in str(e) and "ROW" in str(e), str(e)
else:
    raise AssertionError("--entity '' fell through to %r: a different cohort, submitted" % (fell,))
sys.argv = ["batch_rerun_step.py", "--step", "10", "--entity", "my_cohort", "submit"]
assert rr.entity(step) == "my_cohort", rr.entity(step)
# The no-`--entity` arm: the row comes from the key the profile named, not from a literal in this file.
sys.argv = ["batch_rerun_step.py", "--step", "10", "submit"]
assert rr._row_name(step) == rr.config.get("BATCH"), rr._row_name(step)
print("PASS --entity with an empty value is refused; a named row is still honoured")
PY
inproc "--entity with an empty value is refused, not defaulted to the workspace's batch row" \
    0 "$TMP/empty_entity.py"

# ------------------- 6b. the row is derived data: the name key decides the type, entity_row the source
# Each failure mode is fired here: zero name keys, two name keys, and a row source that is not the
# chain's key.

# Zero name keys: the step reads only `workspace.*`, so nothing states its row. Refused, naming the
# reads it found.
mkdir -p "$TMP/no-namekey"
"$PY" - "$TMP/no-namekey/genotyping.json" <<'PY'
import json, sys
d = json.load(open("profiles/genotyping.json"))
d["steps"][0]["inputs"] = {"GenerateBatchMetrics.ped_file": "workspace.cohort_ped_file"}
json.dump(d, open(sys.argv[1], "w"), indent=1)
PY
run "a step that reads no this.<etype>_id name key is refused, not defaulted to the majority type" 1 \
    'reads no `this.<etype>_id` name key' \
    env GSVTK_MODULE_DIR="$TMP/no-namekey" "$PY" terra/steps.py
run "and the refusal names the this.* reads it DID find, so the member-rule trap is not a guess" 1 \
    'the `this.*` reads it does carry' \
    env GSVTK_MODULE_DIR="$TMP/no-namekey" "$PY" terra/steps.py

# Two name keys: two rows in one step. Both are printed and neither is taken.
mkdir -p "$TMP/two-namekeys"
"$PY" - "$TMP/two-namekeys/genotyping.json" <<'PY'
import json, sys
d = json.load(open("profiles/genotyping.json"))
d["steps"][0]["inputs"]["GenerateBatchMetrics.cohort"] = "this.sample_set_set_id"
json.dump(d, open(sys.argv[1], "w"), indent=1)
PY
run "a step that reads two entity types as name keys is refused, naming both" 1 \
    "more than one entity type as" \
    env GSVTK_MODULE_DIR="$TMP/two-namekeys" "$PY" terra/steps.py
run "and both candidates are printed, not just a count" 1 "sample_set, sample_set_set" \
    env GSVTK_MODULE_DIR="$TMP/two-namekeys" "$PY" terra/steps.py

# 09 reads `this.sample_sets.<attr>` three times and `this.sample_set_set_id` once. The derived type
# must be the cohort type; the member collection must not count towards it.
run "the member-collection trap derives sample_set_set, not sample_sets (09 is the witness)" 0 "PASS" \
    "$PY" -c "
import sys; sys.path.insert(0,'kit'); sys.path.insert(0,'terra')
import module_profile, steps
prof = module_profile.load()
spec = prof.configs['09-MergeBatchSites']
direct = [v for v in spec['inputs'].values() if isinstance(v, str) and v.startswith('this.')
          and '.' not in v[len('this.'):]]
member = [v for v in spec['inputs'].values() if isinstance(v, str) and v.startswith('this.')
          and '.' in v[len('this.'):]]
assert len(member) == 3 and len(direct) == 1, (direct, member)     # the measured shape of the step
etype, found = module_profile.derive_entity_type(spec['inputs'].values())
assert etype == 'sample_set_set', (etype, found)
assert found == ['sample_set_set'], found                          # the member read never counted
assert steps.root_entity('09') == 'sample_set_set'
print('PASS 09 derives %s from %s while reading %d member paths' % (etype, direct[0], len(member)))"

# The cross-check itself is fired in `guards.py` case 1, by mutating only the stated
# `batch_configs.CONFIGS[...]["rootEntityType"]`.

# A step whose row comes from a key other than GSVTK_BATCH resolves its row from the key its profile
# names. `ZONE` is deliberately implausible as a row name, so only reading the right key can pass. The
# long spelling also checks the reader canonicalises what it stores.
mkdir -p "$TMP/otherkey"
"$PY" - "$TMP/otherkey/genotyping.json" <<'PY'
import json, sys
d = json.load(open("profiles/genotyping.json"))
d["steps"][4]["entity_row"] = "GSVTK_ZONE"      # the long spelling, canonicalised by the reader
d["steps"][4]["_why_entity_row"] = "selftest fixture: proves the row source is the key named here"
json.dump(d, open(sys.argv[1], "w"), indent=1)
PY
run "a step whose entity_row names another config key takes its row from THAT key" 0 \
    "row: us-central1-a from GSVTK_ZONE" \
    env GSVTK_MODULE_DIR="$TMP/otherkey" GSVTK_TERRA_API_ROOT=http://127.0.0.1:9/api/ \
    "$PY" terra/batch_rerun_step.py --step 10 show --allow-unpinned-docker
run "and the reader canonicalises the key it stores, so both views spell it one way" 0 "row=ZONE" \
    env GSVTK_MODULE_DIR="$TMP/otherkey" "$PY" kit/module_profile.py --print

# A step may state that nothing offline holds its row. The reader prints that, and the launcher
# refuses, naming the field and the flag.
mkdir -p "$TMP/operatorrow"
"$PY" - "$TMP/operatorrow/genotyping.json" <<'PY'
import json, sys
d = json.load(open("profiles/genotyping.json"))
d["steps"][4]["entity_row"] = "--entity"
json.dump(d, open(sys.argv[1], "w"), indent=1)
PY
run "entity_row: --entity is read as 'no offline source', and prints as that" 0 \
    "[sample_set] row=--entity" \
    env GSVTK_MODULE_DIR="$TMP/operatorrow" "$PY" kit/module_profile.py --print
run "and that step's submit refuses by naming entity_row and --entity, not GSVTK_BATCH" 0 "PASS" \
    "$PY" -c "
import os, subprocess, sys
env = dict(os.environ, GSVTK_MODULE_DIR=sys.argv[1],
           GSVTK_TERRA_API_ROOT='http://127.0.0.1:9/api/')
out = subprocess.run([sys.executable, 'terra/batch_rerun_step.py', '--step', '10', 'submit',
                      '--confirm', '--allow-unpinned-docker'], capture_output=True, text=True, env=env)
text = out.stdout + out.stderr
assert out.returncode == 1, (out.returncode, text[-500:])
assert 'submitted against a sample_set row' in text, text[-600:]
assert 'entity_row' in text and '--entity <name>' in text, text[-600:]
assert 'GSVTK_BATCH=' not in text, text[-600:]      # no stale claim about the chain's batch row
print('PASS a step stating --entity refuses by name, on a sample_set step, with 0 requests')" \
    "$TMP/operatorrow"

# The resolution line names the upstream steps (derived from batch_configs' maps) and the check that
# verifies the attributes a mid-chain step reads.
run "a mid-chain step names the upstream steps whose outputs it reads (and the check that proves them)" 1 \
    "reads chain outputs of: 06-GenerateBatchMetrics, 08-FilterBatchSamples" \
    "$PY" terra/batch_rerun_step.py --step 09 show
run "and it points at batch_check_inputs.py rather than claiming the attributes exist" 1 \
    "prove those attributes exist first: python terra/batch_check_inputs.py --step 09" \
    "$PY" terra/batch_rerun_step.py --step 09 show
run "a step fed by the freeze loop says so, naming the frozen file count not a chain step" 0 \
    "frozen baseline files: 4 (published by batch_freeze.py)" \
    "$PY" terra/batch_rerun_step.py --step 10 show $PIN10

# ------------- 6c. --image-from: a capture pin is the same pin, labelled as a capture
# `--image-from <capture.json>` reads the digest capture `docker/image_capture.py` builds. Checked:
#   (a) a capture produces the same body as the equivalent --image flags;
#   (b) both provenance printers say `capture:<file>`;
#   (c) a capture ref cannot borrow a profile's `versioned_by` declaration;
#   (d) refusals happen before the first request;
#   (e) absent file is exit 2, unusable or tracked capture is exit 3, and a key the capture marks
#       `unresolved` is disclosed and left to the existing pin guard (so the refusal block in
#       `scripts/selftest.d/golden/rerun-step10-unpinned.txt` is unchanged).
# The fixtures are hand-written placeholders under $TMP; nothing resolves a digest.
CAPD="$TMP/cap"
SHA64="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
# Written to a file rather than captured in `$( ... <<EOF )`: bash 3.2 mis-parses an apostrophe in a
# comment inside a command substitution.
"$PY" - "$TMP" "$SHA64" > "$TMP/cap-flags.txt" <<'CAPFIX'
"""Hand-author the capture fixtures; print the --image spellings of the full one."""
import json
import os
import sys

T, sha = sys.argv[1], sys.argv[2]
out = os.path.join(T, "cap")
os.makedirs(out, exist_ok=True)
REG = "us.gcr.io/your-dev-project/test/gatk-sv"      # placeholder path: no real registry, ever
HEAD = "0123456789abcdef0123456789abcdef01234567"
TAG = "your-branch-under-test-012345"


def entry(key, ref="", stem=None):
    """One EXTERNAL entry: the digest, the ref it was taken of, and where that ref was read from."""
    stem = stem if stem is not None else key.split(".")[-1][:-len("_docker")]
    ref = ref or "%s/%s:%s@sha256:%s" % (REG, stem, TAG, sha)
    return {"ref": ref, "state": "EXTERNAL",
            "source_ref": "%s/%s:%s" % (REG, stem, TAG),
            "source_ref_origin": "inputs/values/dockers.json @ " + HEAD}


def doc(images, unresolved=None):
    return {"kind": "gsvtk-image-capture", "version": 1, "captured_at": "2026-10-21T20:00:00Z",
            "source": {"branch": "your-branch-under-test", "head_sha": HEAD, "base_sha": "",
                       "image_tag": TAG, "docker_repo": REG,
                       "log_source": "gatk-sv-build.sh serial stream"},
            "built_targets": ["sv-pipeline"], "images": images, "unresolved": unresolved or {}}


def write(name, document):
    with open(os.path.join(out, name), "w") as fh:
        json.dump(document, fh, indent=1)


# The keys are the profile's own, never retyped here: the same rule the SS_KEYS list above follows.
ss = sorted(json.load(open("profiles/single_sample.json"))["steps"][0]["images"])
full = {k: entry(k) for k in ss}
write("full.json", doc(full))
write("unresolved.json", doc({k: v for k, v in full.items() if k != ss[0]},
                             {ss[0]: "docker hub image: no GCR/AR digest to describe"}))
write("empty.json", doc({}))                                    # resolved nothing at all
bad = doc(full); bad["kind"] = "gsvtk-method-config-capture"; write("kind.json", bad)
bad = doc(full); bad["version"] = 2; write("version.json", bad)
write("silent.json", doc({k: v for k, v in full.items() if k != ss[1]}))    # in neither bucket
tag = dict(full); tag[ss[2]] = entry(ss[2], ref=full[ss[2]]["ref"].split("@")[0])
write("tagonly.json", doc(tag))                                 # a tag is not a digest
noo = dict(full); noo[ss[3]] = {k: v for k, v in full[ss[3]].items() if k != "source_ref_origin"}
write("noorigin.json", doc(noo))                                # EXTERNAL with nowhere to check it
lau = dict(full); lau[ss[4]] = entry(ss[4], ref=REG + "/google/ubuntu1804", stem="google/ubuntu1804")
write("launder.json", doc(lau))                                 # the 3c declaration, borrowed
gt = [k for s in json.load(open("profiles/genotyping.json"))["steps"]
      if str(s.get("step", "")).startswith("07")
      for k in (s.get("inputs") or {}) if k.endswith("_docker")]
write("step07.json", doc({k: entry(k) for k in gt}))            # a capture for a chain step

# A step that binds NO *_docker input, for the `0 of 0` arm: the shipped module with every docker binding
# (and the `images` map its `_why_images` sibling explains) taken out.
import copy

plant = os.path.join(T, "plant-nodocker")
os.makedirs(plant, exist_ok=True)
nod = copy.deepcopy(json.load(open("profiles/single_sample.json")))
for step in nod["steps"]:
    step["inputs"] = {k: v for k, v in step["inputs"].items() if not k.endswith("_docker")}
    step.pop("images", None)
    step.pop("_why_images", None)
with open(os.path.join(plant, "single_sample.json"), "w") as fh:
    json.dump(nod, fh)
print(" ".join(x for k in ss for x in ("--image", "%s=%s" % (k.split(".")[-1], full[k]["ref"]))))
CAPFIX
CAPFLAGS="$(/bin/cat "$TMP/cap-flags.txt")"
if [ -z "$CAPFLAGS" ]; then printf 'rerun: the capture fixtures did not build\n' >&2; exit 1; fi

# (a) BYTES: the capture, then the equivalent --image flags, in two clean processes.
# shellcheck disable=SC2086
env GSVTK_MODULE=single_sample "$PY" terra/batch_rerun_step.py --step 01 show --image-from "$CAPD/full.json" \
    > "$TMP/cap-body.json" 2> "$TMP/cap-body.err"
# shellcheck disable=SC2086
env GSVTK_MODULE=single_sample "$PY" terra/batch_rerun_step.py --step 01 show $CAPFLAGS \
    > "$TMP/flag-body.json" 2> /dev/null
bytes "the capture door and fourteen --image flags produce ONE body, byte for byte" \
    "$TMP/flag-body.json" "$TMP/cap-body.json"
run "and it says how much it supplied, with the step's own binding count as the denominator" 0 \
    "14 of 14 keys supplied by $CAPD/full.json" /bin/cat "$TMP/cap-body.err"
run "the image pins: ladder gained the rung, and it names the FILE that stated the ref" 0 \
    "gatk_docker=capture:$CAPD/full.json" /bin/cat "$TMP/cap-body.err"
run_absent "so no pin of that run is mislabelled profile/env" 0 "profile/env" /bin/cat "$TMP/cap-body.err"
run_absent "nor profile/images: a capture ref is not a profile ref" 0 "profile/images" \
    /bin/cat "$TMP/cap-body.err"
run "and a capture ref lands QUOTED, the bytes parse_images produces, digest and all" 0 \
    "\\\"$R/gatk-sv/gatk:your-branch-under-test-012345@sha256:$SHA64\\\"" /bin/cat "$TMP/cap-body.json"

# (b) The second printer. `show` never prints it, but `create` without --confirm prints the dispatch
# block before it refuses.
run "the SECOND printer (the taken block) names the capture too" 1 "(capture:$CAPD/full.json)" \
    env GSVTK_MODULE=single_sample "$PY" terra/batch_rerun_step.py --step 01 create \
    --image-from "$CAPD/full.json"
run_absent "and its old ternary no longer calls a capture pin profile/env" 1 "profile/env" \
    env GSVTK_MODULE=single_sample "$PY" terra/batch_rerun_step.py --step 01 create \
    --image-from "$CAPD/full.json"

# (c) Precedence: a capture sits above the config layer and below --image. Step 07's one docker is a
# profile/env pin without any flag.
run "a capture beats the config-layer registry it sits above" 0 \
    "sv_pipeline_docker=capture:$CAPD/step07.json" \
    "$PY" terra/batch_rerun_step.py --step 07 show --image-from "$CAPD/step07.json"
run_absent "and the value it beat is gone from the body, not merely out-labelled" 0 \
    "$R/gatk-sv:YOUR_TAG" \
    "$PY" terra/batch_rerun_step.py --step 07 show --image-from "$CAPD/step07.json"
run "CONTROL: with --image naming the key too, --image is still what the line credits" 0 \
    "sv_pipeline_docker=--image" \
    "$PY" terra/batch_rerun_step.py --step 07 show --image-from "$CAPD/step07.json" \
    --image sv_pipeline_docker=$R/sv-pipeline:OTHER_TAG

# (c2) Uses section 3c's plant, where the profile declares `linux_docker` versioned_by path. The capture
# ref is still refused, because `ref_version_kind` is called without the declaration.
run "a capture ref cannot borrow the profile's versioned_by declaration: refused, exit 3" 3 \
    "is not a digest ref" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-path" GSVTK_BRANCH=main \
        "$PY" terra/batch_rerun_step.py --step 01 submit --confirm --entity NA12878 \
        --image-from "$CAPD/launder.json"
run_absent "and it refused at the door, before the read-back was even attempted" 3 \
    "cannot read the config" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/a26-path" GSVTK_BRANCH=main \
        "$PY" terra/batch_rerun_step.py --step 01 submit --confirm --entity NA12878 \
        --image-from "$CAPD/launder.json"

# (e) The three answers on `submit --confirm`. `cannot read the config` is the read-back's first line,
# so its absence shows the refusal came before any read. Each arm is driven from a TSV in two passes
# rather than through a helper, so each `run` call site stays distinct in the site ledger.
cat > "$TMP/cap_arms.tsv" <<'ARMS'
missing|2|CAPTURE NOT FOUND|@TMP@/no-such-capture.json|no capture at that path is the ABSENT answer, exit 2, and nothing was read
tracked|3|CAPTURE IS TRACKED|@FIX@|a capture this repo TRACKS is refused before it is opened, at exit 3
kind|3|is not an image capture|@CAPD@/kind.json|a capture of another kind is refused, not repurposed
version|3|is capture version 2|@CAPD@/version.json|a capture of a version this reader was not taught is refused
empty|3|holds 0 entries|@CAPD@/empty.json|an EMPTY capture is its own answer, not the absent one
silent|3|says NOTHING about 1 of the 14|@CAPD@/silent.json|silence about a key the step binds is a malformed capture, not a workspace attribute
wrongstep|3|says NOTHING about 14 of the 14|@CAPD@/step07.json|a capture built for ANOTHER workflow's step cannot pin this one
tagonly|3|is not a digest ref|@CAPD@/tagonly.json|a tag-only capture ref is refused even though a tag passes the launcher's own guard
noorigin|3|claims EXTERNAL and states none of|@CAPD@/noorigin.json|an EXTERNAL digest with no stated origin is refused
ARMS
# The tracked arm points at a fixture that is not JSON; the expected needle also proves the tracked
# check comes before the open. Pass one: exit code and needle.
while IFS='|' read -r arm want needle path desc; do
    cap="${path//@TMP@/$TMP}"; cap="${cap//@FIX@/$FIX}"; cap="${cap//@CAPD@/$CAPD}"
run "$desc" "$want" "$needle" env GSVTK_MODULE=single_sample "$PY" terra/batch_rerun_step.py \
    --step 01 submit --confirm --entity NA12878 --image-from "$cap"
done < "$TMP/cap_arms.tsv"
# Pass two: the absence of the read-back line, on the same commands.
while IFS='|' read -r arm want needle path desc; do
    cap="${path//@TMP@/$TMP}"; cap="${cap//@FIX@/$FIX}"; cap="${cap//@CAPD@/$CAPD}"
run_absent "$desc: and it refused before the first request" "$want" "cannot read the config" \
    env GSVTK_MODULE=single_sample "$PY" terra/batch_rerun_step.py --step 01 submit --confirm \
    --entity NA12878 --image-from "$cap"
done < "$TMP/cap_arms.tsv"

# (e2) An unresolved key is not refused by the capture door; the existing guard decides.
env GSVTK_MODULE=single_sample "$PY" terra/batch_rerun_step.py --step 01 show \
    --image-from "$CAPD/unresolved.json" > /dev/null 2> "$TMP/unres.err"
run "an unresolved key is disclosed by key and by the capture's own reason" 0 \
    "unresolved in the capture: cloud_sdk_docker -- docker hub image" /bin/cat "$TMP/unres.err"
run "and it says what happens next instead of pretending to stop it" 0 \
    "An unresolved key is NOT a refusal" /bin/cat "$TMP/unres.err"
run "uncovered, the UNCHANGED pinned-image guard refuses, naming that key" 1 \
    "unpinned:  GATKSVPipelineSingleSample.cloud_sdk_docker" \
    env GSVTK_MODULE=single_sample "$PY" terra/batch_rerun_step.py --step 01 submit --confirm \
    --entity NA12878 --image-from "$CAPD/unresolved.json"
run_absent "and it is THAT guard which refused, not a second one at the door" 1 "cannot read the config" \
    env GSVTK_MODULE=single_sample "$PY" terra/batch_rerun_step.py --step 01 submit --confirm \
    --entity NA12878 --image-from "$CAPD/unresolved.json"
# Positive control: `--image` covers the unresolved key, and the same capture builds the body.
run "CONTROL: --image covering the unresolved key makes the SAME capture build the body" 0 \
    "cloud_sdk_docker=--image" \
    env GSVTK_MODULE=single_sample "$PY" terra/batch_rerun_step.py --step 01 show \
    --image-from "$CAPD/unresolved.json" --image cloud_sdk_docker=$R/cloud-sdk:YOUR_TAG
run "the coverage line counts what the capture supplied, not what the step ended up with" 0 \
    "13 of 14 keys supplied by" /bin/cat "$TMP/unres.err"
# A step that binds no *_docker input prints `0 of 0` rather than staying quiet about the file it read.
run "a step binding no *_docker input still says 0 of 0, with the keys it could not use" 0 \
    "0 of 0 keys supplied by" \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/plant-nodocker" \
        "$PY" terra/batch_rerun_step.py --step 01 show --image-from "$CAPD/full.json"

# The flag and its exit codes are documented in --help.
run "--image-from is documented in --help" 0 "  --image-from FILE" \
    env GSVTK_CONFIG= "$PY" terra/batch_rerun_step.py --help
run "and --help carries the exit codes, so a caller can tell absent from unusable without parsing prose" 0 \
    "no capture at that path" env GSVTK_CONFIG= "$PY" terra/batch_rerun_step.py --help
run "an EMPTY --image-from is refused at the parser, not read as 'no capture at all'" 1 \
    "--image-from wants the path to a capture file" \
    env GSVTK_MODULE=single_sample "$PY" terra/batch_rerun_step.py --step 01 show --image-from ""

# (d) The money path, in process. The capture is applied after every mutating verb is made to raise
# (and, for wire=True, fiss's request boundary is captured), so empty TRACE and WIRE show the order.
cat > "$TMP/capture_wire.py" <<'PY'
"""One POST mode, one unusable capture: the door closes with the request boundary captured."""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

mode, cap = sys.argv[1], sys.argv[2]
err, refused, code = io.StringIO(), None, 0
with contextlib.redirect_stderr(err):
    try:
        L.setup("01", module="single_sample", mode=mode, wire=True, capture=cap,
                extra_argv=("--entity", "NA12878") if mode == "submit" else (),
                keep=("create_workspace_config",) if mode == "create" else ())
    except SystemExit as exc:                    # the door closed: that IS the arm
        code = exc.code if isinstance(exc.code, int) else 1
        refused = err.getvalue()
assert refused is not None, "%s: a capture with 0 `images` entries reached %s()" % (mode, mode)
assert code == 3, "%s: exit %s, want 3 (the `not a usable capture` answer)" % (mode, code)
for needle in ("--image-from", "docker/image_capture.py", "holds 0 entries"):
    assert needle in refused, "%r never appears in the refusal:\n%s" % (needle, refused[:400])
assert L.TRACE == [], "a read or a submission was attempted before the refusal: %r" % (L.TRACE,)
assert L.WIRE == [], "a POST body was captured before the refusal: %r" % (L.WIRE,)
print("PASS %s: refused at the door at exit 3, 0 requests attempted, 0 bodies captured" % mode)
PY
n=0
for mode in submit create; do
    inproc "--image-from refuses $mode at the door, with 0 requests attempted and 0 POSTs captured" 0 \
        "$TMP/capture_wire.py" "$mode" "$CAPD/empty.json"
    n=$((n + 1))
done
run "both POST modes reached that arm (not a loop that ran zero times)" 0 "2" /usr/bin/printf '%s' "$n"

# --------- 7. submit() reads the config back, and refuses before the POST unless it IS the body
# A submission POSTs a config NAME, so what runs is whatever the workspace holds under that name. In
# process and offline: the submission call records, mutating fiss calls raise, and the read-back returns
# one payload built by the leg.
cat > "$TMP/readback.py" <<'PY'
"""One read-back case per run, named by argv[1]. Prints PASS, or fails with the text it wanted.

The payload is deliberately not the tool's own dict: it is the config as stored, plus the fields only
the server states (`methodConfigVersion` bumped, `deleted`, a derived key inside `methodRepoMethod`, a
`workspace` block). The `agree` case carries those extras too, which is what makes it a control on the
comparison rather than a copy -- a guard that compared whole dicts would refuse it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

case = sys.argv[1]
terra, rr = L.setup("10", exempt=(case == "exemption"))
body = rr.make_body()
pfx = rr.resolved()["input_prefix"]
EXTRA = pfx + "some_branch_only_input"          # a binding another branch POSTed
GONE = pfx + "ploidy_table"                     # a binding this body has and the copy lacks


def _image(held):
    held["inputs"][L.docker(held, "gatk_docker")] = '"%s/gatk:SOMEONE_ELSES_TAG"' % L.REG


def _cache(held):
    held["useCallCache"] = True


def _extra(held):
    held["inputs"][EXTRA] = "workspace.no_such_attribute_new"


def _missing(held):
    del held["inputs"][GONE]


def _entity(held):
    held["rootEntityType"] = "sample_set_set"


def _unpinned(held):
    held["inputs"][L.docker(held, "sv_base_mini_docker")] = "workspace.sv_base_mini_docker"


NOT_FOUND = terra.TerraError('get_workspace_config: HTTP 404 {"message": "not found"}')
FORBIDDEN = terra.TerraError("get_workspace_config: HTTP 403 no access to this workspace")

# case -> (what must happen, how the copy answers, what the refusal must name)
CASES = {
    "agree":            ("submit", {}, ["read back 10-GenotypeBatch-rerun",
                                        "agrees with the body built here",
                                        "server-side fields not compared", "snapshotId"]),
    "image-drift":      ("refuse", {"mutate": _image},
                         ["is NOT the body this rerun built", "gatk_docker",
                          "SOMEONE_ELSES_TAG", "Nothing was submitted"]),
    # Re-pointed, not deleted. This case used to expect a refusal when the workspace copy stated a
    # different `useCallCache`, which looked like a control on the money field and was actually a
    # control on an impossible payload: a method config has no such member, so the only way to make
    # the copy disagree about it was a fixture built from the body. What it now pins is the measured
    # truth -- a submission-level option the copy happens to carry is NOT a config difference (the
    # cache choice is enforced on the POST itself, and the legs for that are the `cache=` cases),
    # while `entity-drift` below keeps the refusal for a field a config really can state.
    "submission-only-field": ("submit", {"mutate": _cache},
                         ["read back 10-GenotypeBatch-rerun", "agrees with the body built here"]),
    "extra-input":      ("refuse", {"mutate": _extra}, [EXTRA, "the copy binds"]),
    "missing-input":    ("refuse", {"mutate": _missing}, [GONE, "the copy does not"]),
    "entity-drift":     ("refuse", {"mutate": _entity},
                         ["rootEntityType", "body sample_set != workspace sample_set_set"]),
    "workspace-unpinned": ("refuse", {"mutate": _unpinned},
                           ["sv_base_mini_docker", "is NOT the body this rerun built"]),
    "absent":           ("refuse", {"raises": NOT_FOUND},
                         ["no config by that name", "create --confirm", "Nothing was submitted"]),
    "unreadable":       ("refuse", {"raises": FORBIDDEN},
                         ["cannot read the config this submission would run",
                          "Nothing was submitted"]),
    "not-an-object":    ("refuse", {"payload": "[]"}, ["not a method config"]),
    # The flag honoured: the copy agrees (it holds the same workspace attribute this body holds), so
    # submit runs -- and says which key it did not pin, and that the flag is why.
    "exemption":        ("submit", {}, ["--allow-unpinned-docker authorised",
                                        pfx + "sv_base_mini_docker"]),
}

outcome, spec, needles = CASES[case]
if "payload" in spec:                     # a payload that is not a method config at all
    L.read_with(payload=spec["payload"])
elif "raises" in spec:                     # 404 / no access / no network: the copy was not read
    L.read_with(raises=spec["raises"])
else:
    L.read_with(payload=L.server_copy(body, spec.get("mutate")))

refused, out = L.run_submit(rr)
text = out if outcome == "submit" else refused
if outcome == "submit":
    assert refused is None, "%s: refused although the copy agrees: %s" % (case, refused)
    assert [t[0] for t in L.TRACE] == ["read", "submit"], L.TRACE
else:
    assert refused, "%s: submitted against a copy that is not this body: %r" % (case, L.TRACE)
    assert [t for t in L.TRACE if t[0] == "submit"] == [], L.TRACE
missing = [n for n in needles if n not in text]
assert not missing, "%s: its answer never says %r. It said:\n%s" % (case, missing, (text or "")[:700])
reads = [t[1] for t in L.TRACE if t[0] == "read"]
assert reads[:1] == [(rr.tc.NS, rr.tc.WS, rr.tc.NS, rr.resolved()["rerun_config"])], reads
print("PASS %s: %s, reading %s first" % (case, outcome, reads[0][3]))
PY
n=0
for c in agree image-drift submission-only-field extra-input missing-input entity-drift workspace-unpinned \
         absent unreadable not-an-object exemption; do
    inproc "submit read-back, case $c: the POST happens only when the copy is this body" 0 \
        "$TMP/readback.py" "$c"
    n=$((n + 1))
done
run "all eleven read-back cases ran (not a loop that ran zero times)" 0 "11" /usr/bin/printf '%s' "$n"

# The read-back under `--tag`: the fake workspace answers with the name it was asked for, as a real GET
# does, so a read-back of the wrong (untagged) config is caught. Reusing `server_copy(body)` would carry
# the tagged name regardless and pass by construction.
cat > "$TMP/readback_tag.py" <<'PY'
"""`submit --tag TAG` reads back `NN-<Wf>-TAG` and submits that same name. Prints PASS."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

TAG, WANT, DEFAULT = "h2h", "10-GenotypeBatch-h2h", "10-GenotypeBatch-rerun"
terra, rr = L.setup("10", extra_argv=("--tag", TAG))
body = rr.make_body()
assert body["name"] == WANT, ("the body is not the tagged one either: %s" % body["name"])

ASKED = []


def payload(ns, ws, cns, cname):
    ASKED.append(cname)
    held = L.server_copy(body)
    held["name"] = cname              # what a workspace answers is the config it was asked for
    L.TRACE.append(("read", (ns, ws, cns, cname)))
    return held


terra.config_payload = payload
refused, out = L.run_submit(rr)
assert refused is None, \
    "submit refused a workspace that holds exactly the body it built: %s" % refused
assert ASKED == [WANT], \
    "the read-back read %r, not the tagged config this body IS (%s): the agreement would have been "\
    "checked against the wrong config" % (ASKED, WANT)
sent = [t for t in L.TRACE if t[0] == "submit"]
assert len(sent) == 1, L.TRACE
args = sent[0][1]
assert args[3] == WANT, ("the submission names %r, not %r" % (args[3], WANT))
assert [t[0] for t in L.TRACE] == ["read", "submit"], L.TRACE
line = [l for l in out.splitlines() if "read back" in l]
assert len(line) == 1 and WANT in line[0], line
assert DEFAULT not in out and DEFAULT not in str(ASKED), \
    "the untagged name shows up in a tagged run's answer: %r" % out[:300]
print("PASS read back %s and submitted it; %s was never named" % (ASKED[0], DEFAULT))
PY
inproc "submit --tag reads the TAGGED config back and submits that name (the default name is never read)" \
    0 "$TMP/readback_tag.py"

# The submission request itself must name the tagged config too, since `terra.submit` takes the name
# as an argument.
cat > "$TMP/wire_tag.py" <<'PY'
"""The captured submission body names the tagged config. Prints PASS."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

WANT = "10-GenotypeBatch-h2h"
terra, rr = L.setup("10", extra_argv=("--tag", "h2h"), wire=True)
L.read_with(L.server_copy(rr.make_body()))
refused, out = L.run_submit(rr)
assert refused is None, refused
assert len(L.WIRE) == 1, L.WIRE
where, body = L.WIRE[0]
assert body.get("methodConfigurationName") == WANT, \
    "the POST names %r, not %r: %r" % (body.get("methodConfigurationName"), WANT, body)
assert "10-GenotypeBatch-rerun" not in str(body), body
print("PASS the submission POST carries methodConfigurationName=%s to %s, 0 requests sent"
      % (WANT, where))
PY
inproc "the submission REQUEST Terra would receive names the tagged config (captured at the wire)" 0 \
    "$TMP/wire_tag.py"

# `create` with a config name already taken: Terra answers 409, and the tool must refuse rather than
# overwrite under the same --confirm that authorised the create.
cat > "$TMP/collision.py" <<'PY'
"""One collision case per run, named by argv[1]. Prints PASS, or fails with the text it wanted."""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

case = sys.argv[1]                                  # taken | repeat | wrong-name
terra, rr = L.setup("10")
CALLS = []


class Resp:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


def create_409(ns, ws, body):
    CALLS.append(("create", body["name"]))
    return Resp(409, "Workspace already contains method config " + body["name"])


def overwrite(ns, ws, cns, cname, body):
    CALLS.append(("overwrite", cname))
    return Resp(200)


def read_copy(ns, ws, cns, cname):
    CALLS.append(("read", cname))
    return {"methodConfigVersion": 4, "inputs": {"a": "1", "b": "2", "c": "3"}}


terra.fapi.create_workspace_config = create_409
terra.fapi.overwrite_workspace_config = overwrite
terra.config_payload = read_copy
rr.CONFIRMED = True
name = rr.resolved()["rerun_config"]
if case == "repeat":
    rr.OVERWRITE = name
elif case == "wrong-name":
    rr.OVERWRITE = name + "-some-other-config"

def capture(fn):
    """Run a money-path call, keep its output and its SystemExit code. The phase has no such helper."""
    out_s, err_s = io.StringIO(), io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(out_s), contextlib.redirect_stderr(err_s):
            fn()
    except SystemExit as exc:
        if isinstance(exc.code, int):
            code = exc.code
        else:
            # A refusal raised as `SystemExit(message)` prints on the interpreter's stderr, which
            # redirect_stderr never sees -- so the message is collected here, the way a terminal
            # would show it. Without this the leg reads only the prints and grades an empty refusal.
            code = 1
            err_s.write("%s\n" % (exc.code,))
    return out_s.getvalue(), err_s.getvalue(), code


out, err, code = capture(rr.create)
refused = code != 0
text = out + err

if case == "taken":
    assert refused, "a taken config name was accepted without --overwrite:\n%s" % text
    for needle in (name, "--overwrite", "methodConfigVersion", "10-GenotypeBatch-rerun"):
        assert needle in text, "%r never appears in the refusal:\n%s" % (needle, text)
    assert "methodConfigVersion=4" in text, "the refusal must show what is there now:\n%s" % text
    assert ("overwrite", name) not in CALLS, "it overwrote anyway: %r" % CALLS
    assert CALLS[:1] == [("create", name)], CALLS        # the 409 is what triggered this
    print("PASS taken: refused, showed the copy, did not overwrite")
elif case == "repeat":
    assert not refused, "repeating the name still refused:\n%s" % text
    assert ("overwrite", name) in CALLS, "--overwrite %s did not overwrite: %r" % (name, CALLS)
    assert "overwriting" in text and name in text, "no line says it is overwriting:\n%s" % text
    print("PASS repeat: --overwrite with the exact name overwrote")
else:
    assert refused, "a DIFFERENT name in --overwrite was treated as consent:\n%s" % text
    assert ("overwrite", name) not in CALLS, "a wrong name overwrote anyway: %r" % CALLS
    assert name in text, "the refusal did not repeat the name it wants:\n%s" % text
    print("PASS wrong-name: --overwrite is the name, not a yes")
PY
n=0
for c in taken repeat wrong-name; do
    inproc "create on a taken config name, case $c" 0 "$TMP/collision.py" "$c"
    n=$((n + 1))
done
run "all three collision cases ran (not a loop that ran zero times)" 0 "3" /usr/bin/printf '%s' "$n"

# The entity row itself. Rawls accepts a submission naming a row that does not exist and the failure
# arrives later, per workflow, at compute cost.
cat > "$TMP/rowcheck.py" <<'PY'
"""One row-existence case per run, named by argv[1]. Prints PASS, or fails with the text it wanted."""
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

case = sys.argv[1]                                 # present | absent
terra, rr = L.setup("10", row_exists=(case == "present"))
rr.CONFIRMED = True
row = rr.entity(rr.resolved())
# The read-back must AGREE before the row guard is reachable at all (the guard sits last, right before the
# POST), so stub the stored copy exactly as the read-back legs do. Without this the leg refuses at the read
# and grades nothing about the row.
L.read_with(payload=L.server_copy(rr.make_body()))
out_s, err_s = io.StringIO(), io.StringIO()
code = 0
try:
    with contextlib.redirect_stdout(out_s), contextlib.redirect_stderr(err_s):
        rr.submit()
except SystemExit as exc:
    if isinstance(exc.code, int):
        code = exc.code
    else:
        code, _ = 1, err_s.write("%s\n" % (exc.code,))
text = out_s.getvalue() + err_s.getvalue()

if case == "absent":
    assert code != 0, "a submission naming a row Terra does not have went through:\n%s" % text
    for needle in ("has no", "compute cost", row):
        assert needle in text, "%r never appears in the refusal:\n%s" % (needle, text)
    assert not [t for t in L.TRACE if t[0] == "submit"], "create_submission was reached: %r" % L.TRACE
    # The read-back DOES come first, on purpose: everything decidable offline refuses before this tool reads,
    # and a row is only decidable by reading. What must never happen is the POST.
    assert [t for t in L.TRACE if t[0] == "read"], "the read-back was skipped -- the order moved: %r" % L.TRACE
    print("PASS absent: refused after the reads, before the POST, naming the row")
else:
    assert "has no" not in text, "the row guard refused a row the stub says exists:\n%s" % text
    print("PASS present: the row guard lets an existing row through (the phase's other legs grade the POST)")
PY
n=0
for c in present absent; do
    inproc "submit pre-flights the entity row, case $c" 0 "$TMP/rowcheck.py" "$c"
    n=$((n + 1))
done
run "both row-existence cases ran" 0 "2" /usr/bin/printf '%s' "$n"

# The golden refusal, via `submit --confirm`. Same cut and same golden as the `show` leg above.
# shellcheck disable=SC2086
run "submit --confirm refuses an unpinned *_docker before it reads anything (exit 1)" 1 \
    "image inputs are not fully pinned" "$PY" terra/batch_rerun_step.py submit --confirm
run_absent "and it refused before the read-back, not after it" 1 "cannot read the config" \
    "$PY" terra/batch_rerun_step.py submit --confirm
env -u GSVTK_TERRA_API_ROOT "$PY" terra/batch_rerun_step.py submit --confirm >/dev/null 2> "$TMP/su.err"
sed -n '/^image inputs are not fully pinned/,$p' "$TMP/su.err" > "$TMP/su-refusal.txt"
bytes "and in the SAME bytes the golden captures from show (no rewording on the money path)" \
    "$GOLD/rerun-step10-unpinned.txt" "$TMP/su-refusal.txt"

# Fail closed when the read-back cannot be read. Fully pinned, and the Terra root is a closed port.
# shellcheck disable=SC2086
run "a fully pinned submit refuses when the workspace copy cannot be read" 1 \
    "cannot read the config this submission would run" \
    "$PY" terra/batch_rerun_step.py submit --confirm $PIN10
# shellcheck disable=SC2086
run "and it says nothing was submitted, rather than exiting quiet" 1 "Nothing was submitted" \
    "$PY" terra/batch_rerun_step.py submit --confirm $PIN10
# shellcheck disable=SC2086
run_absent "so the line that names the row never prints for a run that never submitted" 1 \
    "against all_samples (sample_set), useCallCache" \
    "$PY" terra/batch_rerun_step.py submit --confirm $PIN10
# --allow-unpinned-docker keeps its documented meaning (docs/terra-head-to-head.md section 4); submit
# names the exempted keys before the POST.
run "submit names the exemption it honoured, by flag, before the POST" 1 \
    "--allow-unpinned-docker authorised" \
    "$PY" terra/batch_rerun_step.py submit --confirm --allow-unpinned-docker
run "naming the exact key the flag left workspace-resolved, not just 'some keys'" 1 \
    "GenotypeBatch.sv_base_mini_docker" \
    "$PY" terra/batch_rerun_step.py submit --confirm --allow-unpinned-docker

# -------------------- 8. the call cache submit PRINTS is the one the submission request POSTs
# fiss's `create_submission(..., use_callcache=True)` default must not override the printed mode. These
# legs capture at fiss's request boundary (`wire=True`), and check `useCallCache` by identity (`is`).
cat > "$TMP/wire_cache.py" <<'PY'
"""One call-cache arm, from argv to the body fiss would have POSTed. Prints PASS."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

# arm -> (flag to pass, the value that must reach BOTH the printed line and the POST body)
ARMS = {"default":         ([], False),
        "no-call-cache":   (["--no-call-cache"], False),
        "call-cache":      (["--call-cache"], True)}
arm = sys.argv[1]
extra, want = ARMS[arm]
terra, rr = L.setup("10", extra_argv=extra, wire=True)
L.read_with(L.server_copy(rr.make_body()))          # the copy agrees: this arm gets to the POST
refused, out = L.run_submit(rr)
assert refused is None, "%s: refused although the copy agrees: %s" % (arm, refused)
assert len(L.WIRE) == 1, L.WIRE
where, body = L.WIRE[0]
assert where == "workspaces/%s/%s/submissions" % (rr.tc.NS, rr.tc.WS), where
assert body.get("methodConfigurationName") == rr.resolved()["rerun_config"], body
assert "inputs" not in body, "that is a method config body, not a submission request: %r" % (body,)
got = body.get("useCallCache")
assert got is want, "%s: the POST body says %r, this arm asked for %r (%r)" % (arm, got, want, body)
line = [l for l in out.splitlines() if "useCallCache=" in l]
assert len(line) == 1, "submit printed %d lines naming useCallCache: %r" % (len(line), line)
said = line[0].split("useCallCache=")[1].split()[0]
assert said == ("true" if want else "false"), "%s: the line printed %s, this arm asked for %r" % (
    arm, said, want)
assert said == str(got).lower(), "print and POST disagree: %s vs %r" % (said, got)
assert [t[0] for t in L.TRACE] == ["read"], L.TRACE      # the read-back still came first, nothing else
print("PASS %s: printed useCallCache=%s, the submission POST says %r to %s, 0 requests sent"
      % (arm, said, got, where))
PY
n=0
for arm in default no-call-cache call-cache; do
    inproc "call-cache arm $arm: the line submit prints is the value the submission POST carries" 0 \
        "$TMP/wire_cache.py" "$arm"
    n=$((n + 1))
done
run "all three call-cache arms reached the wire (not a loop that ran zero times)" 0 "3" \
    /usr/bin/printf '%s' "$n"

# A caller that states no call-cache choice is refused rather than given fiss's default. The three arms
# above are its control.
cat > "$TMP/wire_no_choice.py" <<'PY'
"""`terra.submit` with no stated call cache refuses, and captures 0 POSTs."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

terra, rr = L.setup("10", wire=True)
try:
    terra.submit(rr.tc.NS, rr.tc.WS, rr.tc.NS, rr.resolved()["rerun_config"], "all_samples",
                 "sample_set", None, confirm=True)
    raise AssertionError("an unstated call cache reached the wire; fiss would have POSTed true")
except terra.TerraError as e:
    missing = [w for w in ("call-cache", "useCallCache", "Nothing was submitted") if w not in str(e)]
    assert not missing, "the refusal never says %r: %s" % (missing, str(e)[:300])
assert L.WIRE == [], "it POSTed anyway: %r" % (L.WIRE,)
print("PASS unstated choice refused before the POST, 0 bodies captured")
PY
inproc "an unstated call cache is refused by terra.submit; fiss's true default is unreachable by omission" \
    0 "$TMP/wire_no_choice.py"

# ---------------------------------------------------------------------------
# 9. The publish precondition on the POST path.
#
# What a POST does with each `checks/version_resolves.py` verdict:
#   CURRENT   -> proceeds, and says the two shas are the same commit.
#   STALE     -> refuses, naming both shas and the remedy; `--allow-stale-dockstore` proceeds and prints
#                the exemption before the POST.
#   ABSENT    -> refuses too (same override), and says which verdict it is.
#   REFUSED   -> the check declined, so the pin is UNVERIFIED and the POST goes ahead.
# The probe writes its own tip files. The capture without commitID rows is the real TRS listing.
# ---------------------------------------------------------------------------
cat > "$TMP/publish_pre.py" <<'PY'
"""What create()/submit() do with a publish verdict, driven by capture + tip instead of by network.

The captures are SYNTHESISED from the shipped row's own shape and name the pin this rerun actually
carries (`rr.WDL_VERSION`, which the sandbox config sets to `your-branch-under-test`, not `main`).
A leg that borrowed the shipped fixture's version names would silently grade ABSENT while claiming to
grade STALE -- measured, not assumed: that is exactly what the first draft of this section did.
"""
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

TRS = os.path.join(os.environ["GSVTK_REPO_ROOT"], "scripts/selftest.d/fixtures/trs")
CASE = sys.argv[1]
OLD = "1111111111111111111111111111111111111111"      # what Dockstore serves
NEW = "2222222222222222222222222222222222222222"      # what the branch tip is
tmp = os.path.dirname(os.path.abspath(__file__))

terra, rr = L.setup("10")                       # every request but the captured POST is a raiser
ver = str(rr.WDL_VERSION)
entry = rr.resolved()["method_path"]
holder = json.load(open(os.path.join(TRS, "workflowversions-genotypebatch-shape-holder.json"), encoding="utf-8"))
ROW = holder[0] if isinstance(holder, list) else holder


def capture(rows, name):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)
    return path


def rows_for(names, sha):
    out = []
    for n in names:
        row = copy.deepcopy(ROW)
        row["workflowPath"] = entry
        row["name"] = n
        if sha is None:
            row.pop("commitID", None)           # the real TRS listing has no commit field at all
        else:
            row["commitID"] = sha
        out.append(row)
    return out


def tip(sha):
    path = os.path.join(tmp, "tip-%s.txt" % sha[-4:])
    # Two refs, so picking the wrong branch is caught here rather than by luck.
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("%s\trefs/heads/%s\n%s\trefs/heads/some-other-branch\n" % (sha, ver, OLD))
    return path


CAP = os.environ["GSVTK_PUBLISH_CAPTURE"] = capture(rows_for([ver], OLD), "capture-stale.json")
TIP = os.environ["GSVTK_PUBLISH_TIP"] = tip(NEW)


def proceed(label, *needles):
    stage()
    refused, out = L.run_submit(rr)
    assert refused is None, "%s: stopped the POST:\n%s" % (label, refused[:700])
    for n in needles:
        assert n in out, "%s: output never said %r\n%s" % (label, n, out[:900])
    assert [t[0] for t in L.TRACE] == ["read", "submit"], L.TRACE
    print("PASS %s" % label)


def refuse(label, *needles):
    refused, out = L.run_submit(rr)
    assert refused, "%s: submitted anyway\n%s" % (label, out[-900:])
    for n in needles:
        assert n in refused, "%s: refusal never said %r\n%s" % (label, n, refused[:900])
    assert [t for t in L.TRACE if t[0] == "submit"] == [], L.TRACE   # refused BEFORE any request
    print("PASS %s" % label)


def stage():
    """The workspace copy that agrees with this body, so a PROCEED case dies at nothing.

    Without it every proceeding leg refuses at the read-back -- a real refusal, for a reason this
    section is not about, and it looks exactly like the guard failing to fire.
    """
    L.read_with(L.server_copy(rr.make_body()))


if CASE == "current":
    os.environ["GSVTK_PUBLISH_CAPTURE"] = capture(rows_for([ver], NEW), "capture-current.json")
    proceed("CURRENT proceeds and names both shas as the same commit", "publish check: CURRENT", NEW,
            "refs/heads/%s" % ver)

elif CASE == "stale":
    refuse("STALE refuses submit with both shas, the remedy and the flag",
           "refusing to submit", "STALE", OLD, NEW, "REMEDY", "--allow-stale-dockstore", entry)

elif CASE == "stale-override":
    rr.ALLOW_STALE_DOCKSTORE = True
    stage()
    refused, out = L.run_submit(rr)
    assert refused is None, refused
    for n in ("STALE, proceeding per --allow-stale-dockstore", "authorised the Dockstore pin to stay STALE",
              "not the branch tip"):
        assert n in out, (n, out[-900:])
    assert [t[0] for t in L.TRACE] == ["read", "submit"], L.TRACE
    print("PASS the override proceeds AND prints the exemption before the POST")

elif CASE == "absent":
    os.environ["GSVTK_PUBLISH_CAPTURE"] = capture(rows_for(["not-the-pin-this-rerun-names"], OLD),
                                                  "capture-absent.json")
    refuse("ABSENT refuses submit too and names the override that covers it",
           "refusing to submit", "(ABSENT)", "--allow-stale-dockstore")

elif CASE == "refused":
    os.environ["GSVTK_PUBLISH_CAPTURE"] = capture(rows_for([ver], None), "capture-nocommit.json")
    proceed("a REFUSED capture proceeds as a counted skip, labelled UNVERIFIED and never absence",
            "publish check: REFUSED", "NO COMMIT TO COMPARE", "UNVERIFIED", "does not apply")
    stage()
    refused, out = L.run_submit(rr)
    # Scoped to the publish line's own literal, because a second guard on this path (the dropped-binding
    # check, A24) can print its own "NOT RUN" about a DIFFERENT thing, and a bare token would then read as
    # this check having declined. The emitter is `publish check: NOT RUN (...)` and nothing else.
    for forbidden in ("publish check: NOT RUN", "publish check: ABSENT", "authorised the Dockstore pin"):
        assert forbidden not in out, "a declined check read as %s" % forbidden
    print("PASS a declined check is never read as absence, unrun, or authorised")

elif CASE == "refused-flag":
    # The override must not reach an unmeasured pin: there is no decision here to authorise.
    os.environ["GSVTK_PUBLISH_CAPTURE"] = capture(rows_for([ver], None), "capture-nocommit.json")
    rr.ALLOW_STALE_DOCKSTORE = True
    proceed("--allow-stale-dockstore does not launder a refused check into an authorised pin",
            "does not apply")
    stage()
    refused, out = L.run_submit(rr)
    assert "authorised the Dockstore pin" not in out, out[-900:]
    print("PASS an unmeasured pin is never reported as authorised")

elif CASE == "real-capture":
    # The shipped REAL Dockstore bytes, against this rerun's real pin: the entry is there, the version
    # is not, and the shape is the TRS one that carries no commit at all. This is the case that makes
    # the override's ABSENT arm honest -- a capture covering 1 entry of 341 says ABSENT about a pin it
    # never saw, which is a statement about the capture, not about Dockstore.
    os.environ["GSVTK_PUBLISH_CAPTURE"] = os.path.join(TRS, "dockstore-trs-genotypebatch-2026-10-14.json")
    refuse("the shipped real capture says ABSENT about a pin it does not cover, and names the override",
           "(ABSENT)", "2 version(s) listed for this entry", "your-branch-under-test", "--allow-stale-dockstore")

elif CASE == "notrun":
    # The behaviour half of the unmeasured arm only: a counted skip that does NOT stop the POST. The
    # advice TEXT it prints is graded one phrase per leg off `$TMP/notrun-cure.txt` below, so deleting
    # one sentence of the cure reddens exactly one leg rather than the whole case.
    del os.environ["GSVTK_PUBLISH_CAPTURE"], os.environ["GSVTK_PUBLISH_TIP"]
    proceed("with no capture the check counts a skip and does not stop the POST",
            "publish check: NOT RUN")

elif CASE == "notrun-advice":
    # One run of that arm, with the advice printed to stdout so bash can grade it. The sentinel is what
    # keeps those legs honest: it prints ONLY when this reached the submission call, so a leg cannot
    # pass by grepping bytes an earlier crash left in the file.
    del os.environ["GSVTK_PUBLISH_CAPTURE"], os.environ["GSVTK_PUBLISH_TIP"]
    stage()
    refused, out = L.run_submit(rr)
    assert refused is None, "the NOT RUN arm stopped the POST:\n%s" % refused[:700]
    assert [t[0] for t in L.TRACE] == ["read", "submit"], L.TRACE
    sys.stdout.write(out)
    print("ADVICE-CAPTURED-AT-SUBMIT")

elif CASE == "notrun-offline":
    # The forbidden arm, made observable. Nothing supplied means NOTHING fetched -- and "never" is only
    # a measurement if something would notice, so the socket and process boundaries become tripwires for
    # the duration of the call. The closed-port Terra API root cannot catch this one: a Dockstore GET or
    # a `git ls-remote` never touches it, which is exactly why this leg exists.
    del os.environ["GSVTK_PUBLISH_CAPTURE"], os.environ["GSVTK_PUBLISH_TIP"]

    def _trip(*a, **k):
        raise AssertionError("the unmeasured publish arm tried to leave this machine")

    import socket
    import subprocess
    for _mod, _name in ((socket, "socket"), (socket, "create_connection"),
                        (socket, "getaddrinfo"), (subprocess, "Popen")):
        setattr(_mod, _name, _trip)
    proceed("and with nothing supplied it fetches nothing: socket and Popen are tripwired and untripped",
            "publish check: NOT RUN")

elif CASE == "create-stale":
    refused = None
    try:
        rr.create()
    except SystemExit as exc:
        refused = str(exc)
    assert refused and "refusing to create" in refused and "STALE" in refused, refused[:700]
    assert [t for t in L.TRACE if t[0] in ("submit", "create_config")] == [], L.TRACE
    print("PASS create() carries the same precondition, before the config POST")

else:
    raise SystemExit("no such case: %s" % CASE)
PY
for c in current stale stale-override absent refused refused-flag real-capture notrun notrun-offline create-stale; do
    inproc "publish check on the POST path: $c" 0 "$TMP/publish_pre.py" "$c"
done

# The NOT RUN advice, checked one phrase per leg from one run that reached the submission call (the
# sentinel leg proves that), so a missing sentence is named. Needles are fixed strings (`run` uses grep -qF).
"$PY" "$TMP/publish_pre.py" notrun-advice > "$TMP/notrun-cure.txt" 2>&1
run "that cure was graded off a run that really reached the submission call, not off a stale file" 0 \
    "ADVICE-CAPTURED-AT-SUBMIT" /bin/cat "$TMP/notrun-cure.txt"
run "and it names BOTH vars, so the reader knows which two to set" 0 \
    "GSVTK_PUBLISH_CAPTURE, GSVTK_PUBLISH_TIP unset" /bin/cat "$TMP/notrun-cure.txt"
run "it still says the name resolving is not evidence, so a skip is not an all-clear" 0 \
    "the name resolving is not evidence" /bin/cat "$TMP/notrun-cure.txt"
run "it leads with the live one-liner that now exists: gsvtk publish-check <branch>" 0 \
    "publish-check <branch>" /bin/cat "$TMP/notrun-cure.txt"
run "and the offline form is spelled in flags publish really takes" 0 \
    "--from-file <capture> --tip-from-file <tip>" /bin/cat "$TMP/notrun-cure.txt"
run "it corrects the mis-statement: --ls-remote takes no argument, it reads the checkout from the env" 0 \
    "--ls-remote takes no argument" /bin/cat "$TMP/notrun-cure.txt"
run_absent "and the flag that does not exist is gone: the cure never names --capture" 0 \
    "--capture" /bin/cat "$TMP/notrun-cure.txt"

# =============================================================================================
# 10. docs/test-a-branch.md keeps its promises.
# Each leg hangs on one phrase of the page. Flag existence is checked by `scripts/check_doc_flags.py`,
# and pointers into the page by `scripts/check_docs.py`.
page() { sed -n '1,$p' "$ROOT/docs/test-a-branch.md"; }

run "the page names the output suffix a sandbox writes under" 0 \
    "GSVTK_NEW_SUFFIX" page
run "and says a wrong suffix is a suffix mismatch, not a missing input (what #4 made visible)" 0 \
    "suffix mismatch" page
run "the rerun sequence reaches validate BEFORE submit, in table order" 0 \
    "ORDER-OK" sh -c "p='$ROOT/docs/test-a-branch.md'
a=\$(grep -n 'rc1 show' \"\$p\" | cut -d: -f1 | head -1)
b=\$(grep -n 'rc1 create' \"\$p\" | cut -d: -f1 | head -1)
c=\$(grep -n 'rc1 validate' \"\$p\" | cut -d: -f1 | head -1)
d=\$(grep -n 'rc1 submit' \"\$p\" | cut -d: -f1 | head -1)
[ -n \"\$a\" ] && [ -n \"\$b\" ] && [ -n \"\$c\" ] && [ -n \"\$d\" ] || exit 9
[ \"\$a\" -lt \"\$b\" ] && [ \"\$b\" -lt \"\$c\" ] && [ \"\$c\" -lt \"\$d\" ] && echo ORDER-OK"
run "the page says a read-only refusal is the gate working, not a broken tool" 0 \
    "not a broken binary" page
run "and where the mutating steps belong: the checkout, not the read-only agent" 0 \
    "in the live checkout" page
run "that a frozen copy is the deliberate exception -- naming the bug that cost the day" 0 \
    "methodConfigVersion" page
run "the check row asks about YOUR branch under test, in the --wf form" 0 \
    "check your-branch-under-test --wf" page
run_absent "and it does not send the reader to check main, the one branch that is not theirs" 0 \
    "check origin/main" page
run "the publish row offers the live one-liner as well as the offline capture" 0 \
    "publish-check" page
run "and the Dockstore cure is on the page: throwaway <branch>_terra plus the filter commit (item 3)" 0 \
    "filters.branches" page


# =============================================================================================
# 11. A module outside the batch chain POSTs end to end.
# =============================================================================================
# `profiles/single_sample.json` step 01, rooted in a `sample` row, past the request boundary (cli.sh
# covers the entry point):
#   (a) the POST body equals the JSON `show` printed;
#   (b) the written attribute map is the profile's own, and `--tag` moves every output and the config name;
#   (c) the submission POST carries entityName/entityType/useCallCache, with entityType = rootEntityType;
#   (d) read-back, missing-row and missing-`--entity` refusals make 0 POSTs (the last, 0 reads too);
#   (e) the shared-baseline-workspace guard applies, with `--allow-shared-target` as its control;
#   (f) the config-name collision guard;
#   (g) `validate`'s endpoint and dump file.
# Same in-process harness as sections 7-8, with `setup` knobs `module=`, `mode=`, `keep=`.
# The *_docker keys this step's profile marks `--image`, derived from the profile rather than typed here
# (the same rule cli.sh's NC_KEYS follows), and the placeholder pins built from them.
SS_KEYS="$("$PY" -c 'import json
d = json.load(open("profiles/single_sample.json"))
print(" ".join(sorted(k.split(".")[-1] for k in d["steps"][0]["images"])))' 2>/dev/null)"
PINSS=""
for _k in $SS_KEYS; do PINSS="$PINSS --image $_k=$R/x:PINNED"; done

# (a) Two processes must agree: the clean `show` run and `create()` inside the harness with its own
# `make_body()` call.
cat > "$TMP/nonchain_body.py" <<'PY'
"""The config a NON-CHAIN create POSTs is the document `show` printed for the same argv."""
import io
import contextlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

PINS = sys.argv[1:]                      # --image / KEY=REF pairs, split by the caller
repo = os.environ["GSVTK_REPO_ROOT"]
terra, rr = L.setup("01", module="single_sample", mode="create", wire=True,
                    keep=("create_workspace_config",))

# `show`, run the way an operator runs it: no harness, no patched module, the tool's own stdout.
r = subprocess.run([sys.executable, os.path.join(repo, "terra", "batch_rerun_step.py"),
                    "show", "--step", "01"] + PINS, capture_output=True, text=True,
                   env=dict(os.environ, GSVTK_MODULE="single_sample"))
assert r.returncode == 0, "show exited %s:\n%s" % (r.returncode, (r.stdout + r.stderr)[:900])
printed = json.loads(r.stdout)

assert L.WIRE == [], "a POST happened before create() was even called: %r" % (L.WIRE,)
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    rr.create()
assert len(L.WIRE) == 1, "create made %d requests, not one: %r" % (len(L.WIRE), [w[0] for w in L.WIRE])
where, posted = L.WIRE[0]
assert where == "workspaces/%s/%s/methodconfigs" % (rr.tc.NS, rr.tc.WS), where
assert json.dumps(posted, sort_keys=True) == json.dumps(printed, sort_keys=True), (
    "the config create() POSTs is NOT what `show` printed. POSTed keys: %r; printed keys: %r; first "
    "differing field: %s" % (sorted(posted)[:4], sorted(printed)[:4],
                             [k for k in set(posted) | set(printed)
                              if posted.get(k) != printed.get(k)]))

# And the field-level facts, on the POSTed document rather than on a bash grep of the printed one: a
# config whose method path is the WDL name points at a Dockstore entry that has never existed (c52ee65),
# and a `*_docker` that is a bare path is an identifier Terra refuses to parse.
assert posted["name"] == "01-GATKSVPipelineSingleSample-rerun", posted["name"]
assert posted["rootEntityType"] == "sample", posted["rootEntityType"]
mrm = posted["methodRepoMethod"]
assert mrm["methodPath"] == "github.com/broadinstitute/gatk-sv/SingleSamplePipeline", mrm
assert mrm["methodUri"].startswith("dockstore://github.com%2Fbroadinstitute%2Fgatk-sv%2FSingleSamplePipeline/"), mrm
assert all(k.startswith("GATKSVPipelineSingleSample.") for k in posted["inputs"]), \
    "inputs are keyed by the WDL name, not the Dockstore entry"
assert posted["useCallCache"] is False, posted["useCallCache"]
dock = {k: v for k, v in posted["inputs"].items() if k.endswith("_docker")}
assert dock, "the second module binds no *_docker input, so this leg graded nothing"
assert all(str(v).startswith('"') and str(v).endswith('"') and ":" in str(v) for v in dock.values()), \
    "an image ref reached the wire unquoted or untagged: %r" % {k: dock[k] for k in sorted(dock)[:3]}
print("PASS create: the wire body IS show's body (%d inputs, %d outputs), %d *_docker quoted+tagged"
      % (len(posted["inputs"]), len(posted["outputs"]), len(dock)))
PY
# shellcheck disable=SC2086
inproc "a non-chain create POSTs exactly the body show printed, at fiss's request boundary" 0 \
    "$TMP/nonchain_body.py" $PINSS

# (b) The write-back map: the profile's own names, and `--tag` moving every one of them.
cat > "$TMP/nonchain_outputs.py" <<'PY'
"""The attributes a NON-CHAIN rerun writes: the profile's own names, and `--tag` moves every one.

Both bodies come from the REAL tool run twice as a subprocess, not from two in-process calls:
`batch_rerun_step.resolved()` caches what `--step`/`--tag` resolved to, so a second in-process call in the
same interpreter answers with the FIRST run's name -- a harness artifact that would have made the tag arm
grade nothing at all.
"""
import json
import os
import subprocess
import sys

repo = os.environ["GSVTK_REPO_ROOT"]
# The module tier is resolved at import, and `steps.py` reads it to decide which steps exist -- so the
# write has to precede the import below, exactly as the harness's `setup(module=...)` does it.
os.environ["GSVTK_MODULE"] = "single_sample"
sys.path.insert(0, os.path.join(repo, "kit"))
sys.path.insert(0, os.path.join(repo, "terra"))
import config                      # noqa: E402  (the resolved new-attribute suffix, one resolver)
import steps as st                 # noqa: E402  (the launcher's own reader for the written map)

PINS = sys.argv[1:]


def show(*extra):
    r = subprocess.run([sys.executable, os.path.join(repo, "terra", "batch_rerun_step.py"),
                        "show", "--step", "01"] + list(extra) + PINS,
                       capture_output=True, text=True, env=dict(os.environ, GSVTK_MODULE="single_sample"))
    assert r.returncode == 0, "show %r exited %s:\n%s" % (extra, r.returncode, (r.stdout + r.stderr)[:900])
    return json.loads(r.stdout)


plain, tagged = show(), show("--tag", "arm_b")
raw = json.load(open(os.path.join(repo, "profiles", "single_sample.json")))["steps"][0]["outputs"]

# The profile's own answer, read through the launcher's reader. A leg that hardcoded 15 would still pass
# the day the profile gains or loses an output -- and that day is the day the leg lied.
wanted = st.rerun_outputs("01", "")
assert wanted, "the second module's profile declares NO outputs, so this leg graded an empty map"
assert len(raw) == len(wanted) == len(plain["outputs"]), \
    "profile %d, steps.py %d, body %d -- three answers to one question" % (
        len(raw), len(wanted), len(plain["outputs"]))
assert plain["outputs"] == wanted, (set(wanted.items()) ^ set(plain["outputs"].items()))
assert set(plain["outputs"]) == set(raw)
suffix = "_" + (config.get("NEW_SUFFIX") or "")
assert all(v.startswith("this.") and v.endswith(suffix) for v in plain["outputs"].values()), plain["outputs"]

# A second arm must not overwrite the first: `--tag` moves the config name and every attribute this run
# WRITES, and moves no INPUT -- because a rerun reads the attributes the first run read.
assert tagged["name"] == "01-GATKSVPipelineSingleSample-arm_b", tagged["name"]
assert tagged["outputs"] == {k: v + "_arm_b" for k, v in wanted.items()}, tagged["outputs"]
assert tagged["inputs"] == plain["inputs"], \
    "--tag moved an input: the tagged arm would read a different run's attributes"
print("PASS outputs: %d write-backs, each this.<attr>%s from the profile; --tag moves all %d, 0 inputs"
      % (len(wanted), suffix, len(wanted)))
PY
inproc "a non-chain run's written attributes are the profile's own, and --tag moves every one" 0 \
    "$TMP/nonchain_outputs.py" $PINSS

# (c) The submission request, for a step whose root entity is neither of the two the chain knows.
cat > "$TMP/nonchain_submit.py" <<'PY'
"""One non-chain submission case: what the POST carries, or which guard stopped it."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

case = sys.argv[1]
ARGS = {
    "submit":    dict(extra_argv=("--entity", "NA12878")),
    "entity":    dict(extra_argv=("--entity", "NA12878")),
    "no-entity": dict(),
    "drift":     dict(extra_argv=("--entity", "NA12878")),
    "row":       dict(extra_argv=("--entity", "NOSUCHROW"), row_exists=False),
}[case]
terra, rr = L.setup("01", module="single_sample", wire=True, **ARGS)
body = rr.make_body()
MUTATE = {"drift": lambda held: held.__setitem__("rootEntityType", "sample_set")}
L.read_with(payload=L.server_copy(body, MUTATE.get(case)))
refused, out = L.run_submit(rr)
posts = [w[0] for w in L.WIRE]
reads = [t for t in L.TRACE if t[0] == "read"]

if case == "submit":
    assert refused is None, "refused although the workspace copy is this body: %s" % refused
    assert posts == ["workspaces/%s/%s/submissions" % (rr.tc.NS, rr.tc.WS)], posts
    sent = L.WIRE[0][1]
    assert sent["methodConfigurationName"] == "01-GATKSVPipelineSingleSample-rerun", sent
    assert sent["entityName"] == "NA12878", sent
    # `entityType` is the field fiss DROPS when the argument is falsy, so it is asserted by value and not
    # by key: a wrong type resolves every this.* binding to nothing after the VMs boot, and a missing one
    # makes Rawls evaluate the root entity from the config instead -- two silent failures, one line.
    assert sent["entityType"] == body["rootEntityType"] == "sample", sent
    assert sent["useCallCache"] is False, sent
    assert "inputs" not in sent, "that is a config body in the submission slot: %r" % sorted(sent)
    line = [l for l in out.splitlines() if "useCallCache=" in l]
    assert len(line) == 1 and "useCallCache=false" in line[0] and "(sample)" in line[0], line
    assert reads and not [t for t in L.TRACE if t[0] == "submit"], L.TRACE   # read first, POST at the wire
    print("PASS submit: 1 POST to %s, entityType=sample, useCallCache=False, 0 requests sent" % posts[0])
elif case == "no-entity":
    assert refused, "a sample-rooted submit went through with no row named: %r" % (L.TRACE,)
    for needle in ("is submitted against a sample row", "entity_row", "--entity"):
        assert needle in refused, "%r missing from the refusal:\n%s" % (needle, refused[:600])
    assert not L.TRACE and not L.WIRE, "it read or posted before refusing: %r %r" % (L.TRACE, L.WIRE)
    print("PASS no-entity: refused with 0 reads and 0 posts, naming the field and the flag")
elif case == "drift":
    assert refused, "submitted against a copy rooted in a DIFFERENT entity type: %r" % (posts,)
    assert "body sample != workspace sample_set" in refused, refused[:600]
    assert posts == [], "the submission POST went out anyway: %r" % posts
    print("PASS drift: the read-back refused a sample/sample_set disagreement, 0 posts")
else:
    assert refused, "a submission naming a row Terra does not have went through: %r" % (posts,)
    assert ("has no sample row named 'NOSUCHROW'" in refused), refused[:600]
    assert posts == [] and reads, "the POST went out, or the read order moved: %r %r" % (posts, reads)
    print("PASS row: refused after the reads, before the POST, naming the row")
PY
for c in submit no-entity drift row; do
    inproc "non-chain submit, case $c: the POST happens only when every guard is satisfied" 0 \
        "$TMP/nonchain_submit.py" "$c"
done

# (e) The shared-target guard, off the chain. The workspace comes from the config layer; the control
# uses the override and gets past the target check to the read-back, which fails on the closed port.
BLS="$("$PY" -c 'import sys; sys.path.insert(0, "kit"); import config
print(config.get("BASELINE_NAMESPACE"), config.get("BASELINE_WORKSPACE"))' 2>/dev/null | tail -1)"
set -- $BLS
BLS_NS=$1; BLS_WS=$2
run "the baseline-workspace guard refuses a non-chain submit too, before any read" 1 \
    "refusing to submit a workflow in the baseline workspace" \
    env GSVTK_MODULE=single_sample GSVTK_TERRA_NAMESPACE="$BLS_NS" GSVTK_TERRA_WORKSPACE="$BLS_WS" \
        "$PY" terra/batch_rerun_step.py submit --step 01 --entity NA12878 --confirm $PINSS
run_absent "and it stopped at the guard, not at the read-back" 1 "cannot read the config" \
    env GSVTK_MODULE=single_sample GSVTK_TERRA_NAMESPACE="$BLS_NS" GSVTK_TERRA_WORKSPACE="$BLS_WS" \
        "$PY" terra/batch_rerun_step.py submit --step 01 --entity NA12878 --confirm $PINSS
run "CONTROL: --allow-shared-target walks past the target check into the read-back" 1 \
    "cannot read the config this submission would run" \
    env GSVTK_MODULE=single_sample GSVTK_TERRA_NAMESPACE="$BLS_NS" GSVTK_TERRA_WORKSPACE="$BLS_WS" \
        "$PY" terra/batch_rerun_step.py submit --step 01 --entity NA12878 --confirm $PINSS \
        --allow-shared-target

# (f) The collision guard on a non-chain name: a taken name refuses, the exact name repeated overwrites,
# and a different name in --overwrite is not consent.
cat > "$TMP/nonchain_collision.py" <<'PY'
"""`create` on a name the workspace already holds, for a step outside the chain."""
import io
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

case = sys.argv[1]                       # taken | repeat | wrong-name
terra, rr = L.setup("01", module="single_sample", mode="create")
CALLS = []


class Resp:
    def __init__(self, code, text=""):
        self.status_code, self.text = code, text


terra.fapi.create_workspace_config = lambda ns, ws, b: CALLS.append(("create", b["name"])) or Resp(
    409, "Workspace already contains method config " + b["name"])
terra.fapi.overwrite_workspace_config = lambda ns, ws, cns, cname, b: CALLS.append(
    ("overwrite", cname)) or Resp(200)
terra.config_payload = lambda ns, ws, cns, cname: CALLS.append(("read", cname)) or {
    "methodConfigVersion": 4, "inputs": {"a": "1", "b": "2"}}
name = rr.resolved()["rerun_config"]
if case == "repeat":
    rr.OVERWRITE = name
elif case == "wrong-name":
    rr.OVERWRITE = name + "-another-config"
out, err, code = io.StringIO(), io.StringIO(), 0
try:
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rr.create()
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else 1
    if code != 0:
        err.write("%s\n" % (exc.code,))
text = out.getvalue() + err.getvalue()
if case == "taken":
    assert code != 0, "a taken name was overwritten without consent:\n%s" % text
    assert ("overwrite", name) not in CALLS, "it overwrote anyway: %r" % CALLS
    assert name in text and "--overwrite" in text and "methodConfigVersion=4" in text, text[:600]
    print("PASS taken: 01-GATKSVPipelineSingleSample-rerun refused, showed the copy, did not overwrite")
elif case == "repeat":
    assert code == 0, "repeating the exact name still refused:\n%s" % text
    assert ("overwrite", name) in CALLS, "--overwrite %s did not overwrite: %r" % (name, CALLS)
    print("PASS repeat: --overwrite with the exact name overwrote the non-chain config")
else:
    assert code != 0 and ("overwrite", name) not in CALLS, "a different name bought consent: %r" % CALLS
    print("PASS wrong-name: --overwrite is the name, not a yes, for step 01 too")
PY
for c in taken repeat wrong-name; do
    inproc "non-chain create on a taken config name, case $c" 0 "$TMP/nonchain_collision.py" "$c"
done

# (g) `validate` for a non-chain config: the endpoint it POSTs and the per-step dump file it writes, so
# validating one step does not replace another step's record.
cat > "$TMP/nonchain_validate.py" <<'PY'
"""`validate` POSTs the non-chain config's own validate endpoint and dumps under its own step name."""
import io
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

POSTS = []


class Resp:
    status_code = 200
    text = "{}"

    def json(self):
        return {"invalid": [], "unparseable": []}


terra, rr = L.setup("01", module="single_sample", mode="validate", keep=("validate_config",))


def _get(methcall, headers=None, root_url=None, **kwargs):
    POSTS.append((methcall, None))
    return Resp()


# `fapi.validate_config` is a GET (`api.py:757`, `return __get(uri)`), so the capture goes on the verb the
# call actually uses -- and the assertion below that NO POST happened is the claim that a `validate` is
# not a write, not an accident of patching the wrong verb.
setattr(terra.fapi, "__get", _get)
setattr(terra.fapi, "__post", lambda methcall, headers=None, root_url=None, **kw: POSTS.append(
    ("POST " + methcall, kw.get("json"))) or Resp())
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    rr.validate()
assert len(POSTS) == 1, POSTS
where, _ = POSTS[0]
assert not where.startswith("POST "), "validate POSTed: %s" % where
want = "workspaces/%s/%s/method_configs/%s/%s/validate" % (
    rr.tc.NS, rr.tc.WS, rr.tc.NS, rr.resolved()["rerun_config"])
assert where == want, where
dumped = os.path.join(os.environ["GSVTK_WORK"], "metadata", "rerun_config_validation.01.json")
assert os.path.exists(dumped), "no per-step dump at %s" % dumped
print("PASS validate: 1 GET to %s, 0 writes, dump named by the step" % want)
PY
inproc "non-chain validate POSTs its own config name and dumps under its own step name" 0 \
    "$TMP/nonchain_validate.py"

# Control: the non-chain harness can reach the boundary, so the legs above are not passing only
# because nothing can POST.
inproc "control: the non-chain harness reaches the boundary at all (the legs above count a POST that CAN happen)" 0 \
    "$TMP/nonchain_body.py" $PINSS


# =============================================================================================
# 12. The post-boundary dropped-binding guard, every arm.
# `batch_configs.require_no_dropped_bindings()` refuses a POST whose template binds one key twice while
# the map still reads it from `this.`/`workspace.`. Shipped data cannot reach the refusal, so planted
# templates are fed in through `--tmpl-dir`. The NOT RUN arm points the checkout at a missing directory.

: > "$TMP/empty.env"                       # no profile: the guard resolves nothing from a user config
cat > "$TMP/db_guard.py" <<'DBEOF'
"""Drive the dropped-binding guard over planted templates; write one file per arm."""
import contextlib, io, os, sys
sys.path.insert(0, "kit")
sys.path.insert(0, "terra")
import batch_configs as bc

OUT = os.path.join(os.environ["DBOUT"], "db")
os.makedirs(OUT, exist_ok=True)
FIX = os.path.join(OUT, "templates", "single_sample")
os.makedirs(FIX, exist_ok=True)


def plant(name, text):
    # RAW TEXT, deliberately: `json.dump` of a python dict cannot express a duplicate key at all (the
    # literal collapses to the last one before anything is written), so building the fixture with a dict
    # would have planted nothing and every fatal-arm leg below would have been a leg about a template
    # that has no duplicate -- green, and about nothing.
    with open(os.path.join(FIX, name + ".json.tmpl"), "w") as fh:
        fh.write(text)
    return name


plant("PlantedWorkflow",
      '{"inputs": {"PlantedWorkflow.alpha": "${workspace.first_attr}", '
      '"PlantedWorkflow.beta": "{{ jinja_neutralised }}", '
      '"PlantedWorkflow.alpha": "${workspace.second_attr}"}, "outputs": {}}\n')
plant("EqualWorkflow",
      '{"inputs": {"EqualWorkflow.alpha": "${workspace.same_attr}", '
      '"EqualWorkflow.alpha": "${workspace.same_attr}"}, "outputs": {}}\n')
plant("CleanWorkflow", '{"inputs": {"CleanWorkflow.only": "${workspace.a}"}, "outputs": {}}\n')

TD = ["--tmpl-dir", os.path.join(OUT, "templates")]
ARMS = {
    "silent": (TD, {"PlantedWorkflow.alpha": "workspace.second_attr"}, "PlantedWorkflow"),
    "named": (TD + ["--allow-duplicate-key", "PlantedWorkflow.alpha"],
              {"PlantedWorkflow.alpha": "workspace.second_attr"}, "PlantedWorkflow"),
    "typo": (TD + ["--allow-duplicate-key", "PlantedWorkflow.nope"],
             {"PlantedWorkflow.alpha": "workspace.second_attr"}, "PlantedWorkflow"),
    "pinned": (TD, {"PlantedWorkflow.alpha": '"gs://example-bucket/x.mei.bed"'}, "PlantedWorkflow"),
    "unbound": (TD, {"PlantedWorkflow.beta": "workspace.b"}, "PlantedWorkflow"),
    "equal": (TD, {"EqualWorkflow.alpha": "workspace.same_attr"}, "EqualWorkflow"),
    "clean": (TD, {"CleanWorkflow.only": "workspace.a"}, "CleanWorkflow"),
    "notrun": ([], {"PlantedWorkflow.alpha": "workspace.second_attr"}, "PlantedWorkflow"),
}
want = sys.argv[1:] or sorted(ARMS)
rc_all = 0
for arm in want:
    argv, posted, wf = ARMS[arm]
    sys.argv = ["db_guard"] + argv
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            bc.require_no_dropped_bindings("leg", wf, posted)
        rc = 0
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 1
        buf.write("\n" + str(exc) + "\n")
    text = buf.getvalue()
    state = "unchecked"
    if arm == "notrun":
        # The NOT RUN arm's own assertions live HERE rather than in three bash legs, because a skip that
        # cannot be scored by its caller has to be scored by the thing that ran it: it must say NOT RUN,
        # must say it is not a pass, and -- the part that matters -- must print NO COUNT, so "I could not
        # look" can never be misread as "no duplicates". rc=0 is the fourth fact: a skip proceeds.
        state = ("ok" if ("dropped-binding check NOT RUN" in text and "Not a pass" in text
                          and "bound more than once in" not in text and rc == 0)
                 else "BAD: rc=%d" % rc)
    with open(os.path.join(OUT, arm + ".txt"), "w") as fh:
        fh.write("# ARM %s rc=%d state=%s\n%s\n" % (arm, rc, state, text))
        print("ARM %s rc=%d state=%s" % (arm, rc, state))
    rc_all = rc_all or (1 if arm == "silent" and rc != 1 else 0)
    rc_all = rc_all or (1 if arm == "notrun" and state != "ok" else 0)
sys.exit(rc_all)
DBEOF

run "the driver ran, and the fatal arm really refused (rc=1)" 0 "ARM silent rc=1 state=unchecked" \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_BRANCH=main DBOUT="$TMP" "$PY" "$TMP/db_guard.py"
run "no ref and no checkout is a stated skip that proceeds" 0 "ARM notrun rc=0 state=ok" \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_BRANCH=main GSVTK_GATK_SV_CHECKOUT=/nonexistent-clone \
    DBOUT="$TMP" "$PY" "$TMP/db_guard.py" notrun
# The arms run as a sweep (one call site, several executions). This also works around an unexplained
# loss: the last recorder call in this phase is sometimes not recorded by `gsvtk_note_site`. Inside a
# sweep that dropped call is an execution of a site already recorded, so the declared/reached pair holds.
cat > "$TMP/db_arms.tsv" <<'ARMEOF'
silent|present|PlantedWorkflow.alpha  bound 2x in single_sample/PlantedWorkflow.json.tmpl|the refusal names the key and how many times the template binds it
silent|present|'${workspace.second_attr}' kept, '${workspace.first_attr}' dropped|and says which value won and which one died
silent|present|a read, so which attribute the run|and it says out loud that the body carries a READ, which is the whole reason it refuses
silent|absent|on this command line's authority|no flag, no claim: the guard never says an override was taken when none was
named|present|ARM named rc=0|naming the key in the override clears exactly that key and the run proceeds
named|present|on this command line's authority|and the log carries whose authority cleared it, and for which key
typo|present|ARM typo rc=1|an override naming a key the template does not repeat is itself a refusal
typo|present|binds: PlantedWorkflow.alpha.|and it answers the typo by listing what the template really repeats
pinned|present|-> pinned, so the POST proceeds|a duplicate the profile pins to a literal is disclosed and proceeds
unbound|present|-> unbound-here, so the POST proceeds|a key the body binds nothing for is disclosed as unbound-here
equal|present|-> nothing-lost, so the POST proceeds|two bindings that agree lose nothing, so they are not findings
clean|present|0 key(s) bound more than once|a template with no duplicate at all says so instead of staying quiet
notrun|present|# ARM notrun rc=0 state=ok|no ref and no checkout: stated skip, labelled not-a-pass, prints no count, proceeds
ARMEOF
# Two passes rather than one loop with a `case`, so the site scanner sees both recorder call sites.
# Each arm appears in exactly one pass.
while IFS='|' read -r arm mode needle desc; do
    [ "$mode" = present ] || continue
run "$desc" 0 "$needle" /bin/cat "$TMP/db/$arm.txt"
done < "$TMP/db_arms.tsv"
while IFS='|' read -r arm mode needle desc; do
    [ "$mode" = absent ] || continue
run_absent "$desc" 0 "$needle" /bin/cat "$TMP/db/$arm.txt"
done < "$TMP/db_arms.tsv"

# =============================================================================================
# 13. An empty output map is refused at the POST boundary, not only by `check`.
# =============================================================================================
# Rawls accepts a config with no outputs, and the run then discards every result. The launch loop never
# runs `check`, so `create`/`submit` must refuse. Positive control: the same planted profile with outputs
# restored POSTs one body. The planted profile lives under $GSVTK_WORK via GSVTK_MODULE_DIR.
cat > "$TMP/empty_outputs.py" <<'PY'
"""`outputs: {}` on the launch path: who refuses it, with how many requests, and who still builds."""
import copy
import io
import json
import contextlib
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rerun_submit_lib as L

case = sys.argv[1]
EMPTY = case.endswith("-empty")          # show-empty | create-empty | submit-empty
repo = os.environ["GSVTK_REPO_ROOT"]
SHIPPED = json.load(open(os.path.join(repo, "profiles", "single_sample.json")))
want = list(SHIPPED["steps"][0]["outputs"])          # the shipped names, not a constant typed here

# The planted module dir. `outputs` is emptied for every step (the profile carries one step today; a
# module with five would need the same guard on each, so the loop is the honest shape).
PLANT = os.path.join(os.environ["GSVTK_WORK"], "plant-" + case)
os.makedirs(PLANT, exist_ok=True)
doc = copy.deepcopy(SHIPPED)
for step in doc["steps"]:
    if EMPTY:
        step["outputs"] = {}
with open(os.path.join(PLANT, "single_sample.json"), "w") as fh:
    json.dump(doc, fh)
os.environ["GSVTK_MODULE_DIR"] = PLANT               # BEFORE the harness imports config/steps

NEEDLE = ["refusing to POST", "step 01", "0 output(s)", "113 input(s)",
          os.path.join(PLANT, "single_sample.json"), "`outputs`"] if EMPTY else []

if case == "show-empty":
    # Read-only mode keeps DISCLOSING and keeps building: `show` is what an operator reads to find this
    # out, and a refusal there would take away the tool that answers "what would this POST?".
    r = subprocess.run([sys.executable, os.path.join(repo, "terra", "batch_rerun_step.py"),
                        "show", "--step", "01", "--allow-unpinned-docker"],
                       capture_output=True, text=True,
                       env=dict(os.environ, GSVTK_MODULE="single_sample"))
    assert r.returncode == 0, "show exited %s:\n%s" % (r.returncode, (r.stdout + r.stderr)[:700])
    body = json.loads(r.stdout)
    assert len(body["outputs"]) == 0 and len(body["inputs"]) == 113, \
        "show %d/%d, not the disclosed 113 in / 0 out" % (len(body["inputs"]), len(body["outputs"]))
    assert "writes 0 output attribute(s)" in r.stderr, r.stderr[-700:]
    print("PASS show: exit 0, body still built (113 in / 0 out), the empty state disclosed on stderr")
    sys.exit(0)

terra, rr = L.setup("01", module="single_sample",
                    mode="create" if case.startswith("create") else "submit",
                    extra_argv=() if case.startswith("create") else ("--entity", "NA12878"),
                    wire=case == "create-planted",
                    keep=("create_workspace_config",) if case == "create-planted" else ())
# A read-back that RECORDS, so "0 requests" is a counted fact rather than the absence of a counter.
L.read_with(payload={})
out, refused = io.StringIO(), None
try:
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        (rr.create if case.startswith("create") else rr.submit)()
except SystemExit as exc:
    refused = str(exc)
if case == "create-planted":
    assert refused is None, "the planted profile with its 15 outputs refused:\n%s" % refused[:700]
    assert len(L.WIRE) == 1, "create made %d requests: %r" % (len(L.WIRE), [w[0] for w in L.WIRE])
    posted = L.WIRE[0][1]
    assert len(posted["outputs"]) == len(want) == 15, \
        "planted outputs %d, shipped %d, posted %d" % (len(posted["outputs"]), len(want),
                                                       len(posted["outputs"]))
    assert set(posted["outputs"]) == set(want), set(posted["outputs"]) ^ set(want)
    print("PASS control: the same planted profile, outputs restored, reaches the boundary and POSTs "
          "one body with %d write-backs" % len(posted["outputs"]))
    sys.exit(0)
assert refused, "%s posted anyway\n%s" % (case, out.getvalue()[-700:])
for needle in NEEDLE:
    assert needle in refused, "%s: refusal never said %r\n%s" % (case, needle, refused[:900])
assert not L.WIRE, "%s made requests: %r" % (case, [w[0] for w in L.WIRE])
assert [t for t in L.TRACE if t[0] == "read"] == [], "%s read before refusing: %r" % (case, L.TRACE)
print("PASS %s: refused with 0 requests and 0 reads, naming the step, the file, the field, 113/0"
      % case)
PY
for c in show-empty create-empty submit-empty create-planted; do
    inproc "empty output map, case $c" 0 "$TMP/empty_outputs.py" "$c"
done

if [ "$skipped" -gt 0 ]; then
    # The skipped count is printed only when non-zero, so the full-run line stays unchanged.
    printf 'rerun: %s ok, %s failed, %s skipped\n' "$ok" "$fail" "$skipped"
else
    printf 'rerun: %s ok, %s failed\n' "$ok" "$fail"
fi

# Both numbers count call sites, so a sweep counts once on each side, and the reporter prints any site
# that never ran. Deleting a call site is not caught. The firecloud bail above also exits 0 so the offline
# gate runs without firecloud.



gsvtk_declared_report "rerun" "$SELF" "$((ok + fail))" "$skipped" || fail=$((fail + 1))
[ "$fail" -eq 0 ] || exit 1
exit 0
