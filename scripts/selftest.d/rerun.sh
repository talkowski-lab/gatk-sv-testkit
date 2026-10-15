#!/usr/bin/env bash
# scripts/selftest.d/rerun.sh — assertions about terra/batch_rerun_step.py and the step map.
#
# What this pins: `--step` selects the step (config name, input-key prefix, Dockstore methodUri and
# root entity all move with it), the step-10 golden is still reproduced BYTE FOR BYTE, every refusal is
# a refusal that can actually fire, and `submit` does not POST unless the config it reads back from the
# workspace IS the body it just built (section 7). The tool used to hold five module-level literals for
# step 10, so "rerun 08" silently reran GenotypeBatch -- a claim about five steps carried by code that
# implemented one. The read-back is here because the same shape of bug survived the literals: a
# submission POSTs a config NAME, so the run executes the workspace's copy, and `submit` never built a
# body, never read one back, and never ran this file's own image guard.
#
# STANDALONE:  bash scripts/selftest.d/rerun.sh .venv/bin/python
# prints its own tally and exits nonzero on any failure. bash 3.2 compatible, no arrays (an empty
# array under `set -u` is an "unbound variable" on stock macOS bash, and this must run there).
#
# NOTHING HERE TOUCHES THE NETWORK. Three things keep that true, and the third is the one that would
# notice if the first two ever stop holding:
#   * only `show` runs as a real command, plus the refusals, which exit before the first request;
#   * the cases that exercise `submit` replace the submission call with a recorder, in process, and turn
#     every other Terra entry point into a raiser, so a request that tried is a failure. The call-cache
#     legs (section 8) need the REAL submission call, because the defect lives inside it, so they stop
#     one layer lower instead: fiss's own request boundary is captured and every other verb there is a
#     raiser, which records the body that would have gone out without sending it;
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
# The callees, DERIVED and then pinned so a drift is loud. Derived with
#   gsvtk_declared_funset scripts/selftest.d/rerun.sh | awk '{print $1}'
# which reads this file and prints every function that records a verdict AND is called from top-level code: the
# four below, no more (`run`/`run_absent`/`bytes`/`inproc`). The reporter re-derives the set on every run and
# fails if this line and the file disagree, so the list cannot rot when a fifth recorder is added — and it names
# any recorder that never calls gsvtk_note_site, which is the omission that would otherwise look like a phase
# that lost all its sites at once.
GSVTK_DECL_FUNCS="run run_absent bytes inproc"
# The whole-phase bail below withholds EVERYTHING after this line, so it declares itself as a region instead of
# printing a hand-counted sentence about it: the extent is this file's text, and the guard then requires the
# sites this run did not reach to be exactly the sites inside that region. A site added ABOVE this marker stays
# required to run even when the bail fires — which is where a canary would go to hide.
gsvtk_guard_to_end "rerun-no-firecloud"
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1; then
    printf 'rerun: SKIP — %s cannot import firecloud (python -m pip install -r requirements.txt)\n' "$PY"
    printf 'rerun: 0 ok, 0 failed, 1 skipped (a SKIP here is not a pass in CI)\n'
    # The pair runs here, not just on the path that reaches the end of the file: a bail is the mode where an
    # orphaned assertion is easiest to lose, so it is the mode that must not skip the guard. No temp dir exists
    # yet at this point, so init mktemps one and owns it — gsvtk_declared_report removes it. Verdict totals are
    # literal 0s because `ok`/`fail` are not initialised until further down, and nothing above has recorded one.
    gsvtk_declared_init
    gsvtk_note_skip "rerun-no-firecloud"
    gsvtk_declared_report "rerun" "$SELF" 0 1 || exit 1
    exit 0
fi

FIX=scripts/selftest.d/fixtures/rerun.profile.env
GOLD=scripts/selftest.d/golden
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
# The site ledger lives in this phase's own temp dir, so the EXIT trap above already owns its whole life. See
# the header of scripts/selftest.d/declared.sh: the file IS the set of reached sites (bash 3.2 has no
# associative arrays), so a ledger that cannot be written is a failing run, not a run that reached 0 sites.
gsvtk_declared_init "$TMP"

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

ok=0; fail=0; skipped=0; QUIET=

# run DESC WANT_RC NEEDLE CMD... — exit code AND a line of output, because a refusal that exits 1
# having printed nothing has refused nothing.
run() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # the line that CALLED run(); once per call, distinct per call site
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
    gsvtk_note_site "${BASH_LINENO[0]}"   # see run() above: the same one line of accounting per recorder
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

# ------------------------------------------ 2c. --tag TAG: two reruns of ONE step, side by side
# The defect `--tag` removes: `rerun-*` derived ONE config name (`NN-<Wf>-rerun`) and its outputs came
# from the profile's own `{new}` expansion, so a SECOND rerun of one step POSTed over the first config
# AND wrote over the first run's output attributes. Two arms could not sit in one sandbox, which is the
# head-to-head this tool exists for; on 2026-10-14 that forced a hand-built config and hand-named
# outputs, i.e. outside the code this file grades. `--tag TAG` moves exactly two names, both derived ONCE
# in `terra/steps.py` (`rerun`'s `rerun_config`, `rerun_outputs`) and read everywhere else:
#
#     config    NN-<Wf>-TAG       was  NN-<Wf>-rerun
#     outputs   this.<attr>_TAG   was  this.<attr>_new      (the `_new` stays; the tag is APPENDED)
#
# and moves nothing else: inputs keep their `{frz}`/`{new}` expansions, so a tagged run reads the chain's
# existing attributes and the two arms differ in one variable. With no flag every byte is what it was
# before the flag -- asserted against the pre-`--tag` file at the end of this section, not assumed.
# `h2h` is a placeholder run name; nothing in this section reaches the wire (see the header).
RUNTAG=h2h
echo
echo "selftest: rerun: --tag, i.e. two runs of one step that do not overwrite each other"

# Both bodies of one step, from one fixture and one set of pins, split into the two maps a reader
# actually asks about. Written by ONE python call so the two files cannot come from two environments,
# and compared by `cmp` below rather than by a grep for one key.
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
# The positive control on the line above: it compares two files, so it would also pass if `--tag` did
# nothing at all. `cmp` exit 1 here says the two bodies ARE different runs, which is what makes the
# exit-0 above a finding about inputs rather than an artefact of comparing one file with itself.
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

# What `show` SAYS, as opposed to what the body contains: a tagged run has to state the config name it
# would POST and the attribute names it would write, or the operator reads one name and POSTs another.
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
# The untagged run still says both things too -- it is the same line in both modes, which is the only
# reason a reader can compare two runs' output by reading it.
run "show with NO --tag prints the same two answers in their default spelling" 0 \
    "writes 10 output attribute(s) (no --tag): this.genotyped_depth_vcf_new" \
    "$PY" terra/batch_rerun_step.py show $PIN10

# Per step, both names, expected by name: a flag that works for the step it was written for and not for
# the other four is this file's own history (`--step 08` silently reran 10), so a tag asserted on step 10
# alone would prove as little as the old step assertion did.
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

# The tag is pasted into TWO Terra identifiers -- a config name and the tail of an attribute name -- so
# the shapes that survive one and are refused by the other are refused here, before anything is built.
# `--tag` with an empty value is the one that matters most: it resolves to the DEFAULT name and outputs,
# i.e. the overwrite, arriving behind the flag typed to prevent it. Same rule this file already applies
# to `--entity ''`.
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
# The same refusals on the money path, where a probe can reach them without the parser: `resolved()` is
# what `submit` calls, and it reads argv itself.
run "a direct call to resolved() refuses an empty --tag too (probes do not go through the parser)" 1 \
    "resolved to an empty value" "$PY" -c "
import sys
sys.path.insert(0, 'terra'); sys.path.insert(0, 'kit')
import batch_rerun_step as rr
rr.sys.argv = ['batch_rerun_step.py', '--step', '10', '--tag', '', 'submit', '--confirm']
rr.resolved()
print('NO REFUSAL')"

# The artifact names, one layer down: `validate`/`submit` dump per-step files, so two tagged runs of ONE
# step share a file name and the second replaces the first run's record -- the same defect, one layer
# down, in the artifact you compare the arms with.
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

# ---------------- the no-flag path, compared BYTES against the pre-`--tag` file (not remembered)
# "with no flag everything stays byte-identical to today" is a claim about bytes, and the only recorder
# that can make it is `cmp` against the pre-change program -- run through the SAME fixture, with the
# same pins, in the same environment. `git show`/`git archive` is read-only by construction (the same
# discipline `batch_configs._wdl_dir_from_ref` states), and the whole base tree -- not one file -- is
# extracted, because the tool resolves `steps.py`, `batch_configs.py` and `profiles/` relative to its
# OWN directory: a lone pre-change file beside the new `steps.py` would grade a mix of the two.
BASE=fa4d5c9                     # the commit this lane started from
mkdir -p "$TMP/base"
if git archive "$BASE" terra kit profiles 2>/dev/null | tar -x -C "$TMP/base" 2>/dev/null \
   && [ -s "$TMP/base/terra/batch_rerun_step.py" ] \
   && git show "$BASE:terra/steps.py" 2>/dev/null | grep -q "rerun_config"; then
    BASE_OK=1
else
    BASE_OK=""
fi
gsvtk_guard_sites "rerun-base-blob"
if [ -z "$BASE_OK" ]; then
    gsvtk_note_skip "rerun-base-blob"
    # The bump belongs beside the note: naming a region tells the declared/reached reporter WHICH sites
    # were withheld, and the bump is what tells the phase's own tally that a skip happened. Naming without
    # bumping is the defect CI caught -- the region fired on a shallow checkout (the frozen blob is not in a
    # depth-1 clone), this branch printed a SKIP, and the tally still said `0 skipped`, which is a phase
    # that cannot price its own coverage: 1 region withheld sites while its arithmetic claims none happened.
    skipped=$((skipped + 1))
    printf '  SKIP  no --tag is byte-identical to %s — that blob is not readable from this tree (shallow clone, exported tree). A SKIP here is not a pass in CI\n' "$BASE"
else
    # One env, two trees: every variable is spelled here for BOTH sides, so a difference in the bytes can
    # only come from the code that produced them.
    # shellcheck disable=SC2086
    env GSVTK_CONFIG="$ROOT/$FIX" GSVTK_WORK="$TMP/work-base" GSVTK_REPO_ROOT="$ROOT" \
        GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/" \
        "$PY" "$TMP/base/terra/batch_rerun_step.py" show $PIN10 \
        > "$TMP/base-body.json" 2> "$TMP/base-err.txt"
    # shellcheck disable=SC2086
    env GSVTK_CONFIG="$ROOT/$FIX" GSVTK_WORK="$TMP/work-base" GSVTK_REPO_ROOT="$ROOT" \
        GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/" \
        "$PY" "$ROOT/terra/batch_rerun_step.py" show $PIN10 \
        > "$TMP/now-body.json" 2> "$TMP/now-err.txt"
    # Both sides content-checked BEFORE the compare: a base tree that booted nothing would leave two
    # files `cmp` agrees about, and the byte claim would be vacuous. Same shape as the two content
    # controls cli.sh runs before its own byte comparison.
    for side in base now; do
        run "the $side program built a real step-10 body, not an empty one (the control on cmp)" 0 \
            "PASS" "$PY" -c "
import json, sys
d = json.load(open(sys.argv[1]))
assert d['name'] == '10-GenotypeBatch-rerun', d['name']
assert len(d['inputs']) >= 16, len(d['inputs'])
assert len(d['outputs']) == 10, len(d['outputs'])
assert d['rootEntityType'] == 'sample_set', d['rootEntityType']
print('PASS %s: %d inputs, %d outputs, %s' % (sys.argv[2], len(d['inputs']), len(d['outputs']),
                                              d['name']))" "$TMP/$side-body.json" "$side"
    done
    bytes "no --tag is byte-identical to $BASE: the config body a step-10 rerun POSTs" \
        "$TMP/base-body.json" "$TMP/now-body.json"
    # And what DID change on stderr is exactly one line, stated rather than assumed: the new `writes`
    # line. Without this leg the byte claim would cover stdout only, and a no-flag run that also started
    # printing something wrong would still be green here.
    run "the only stderr difference between the two trees is the new writes line" 0 "PASS" "$PY" -c "
import sys
old = [l for l in open(sys.argv[1]).read().splitlines() if not l.startswith('[terra]')]
new = [l for l in open(sys.argv[2]).read().splitlines() if not l.startswith('[terra]')]
added = [l for l in new if l not in old]
removed = [l for l in old if l not in new]
assert not removed, 'the no-flag path LOST a line it used to print: %r' % (removed,)
assert len(added) == 1 and added[0].lstrip().startswith('writes '), \
    'the no-flag path gained %d line(s), expected only the writes line: %r' % (len(added), added)
print('PASS 1 added line (%s), 0 removed' % added[0].strip()[:58])" \
        "$TMP/base-err.txt" "$TMP/now-err.txt"
fi
gsvtk_guard_sites_end "rerun-base-blob"

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
run "--step 09 submit refuses by naming the missing row and --entity" 1 \
    "pass --entity <name>" "$PY" terra/batch_rerun_step.py --step 09 submit --confirm $PIN09
# shellcheck disable=SC2086
run_absent "and it refused before the Terra-side pre-check could run" 1 "pre-check" \
    "$PY" terra/batch_rerun_step.py --step 09 submit --confirm $PIN09
# shellcheck disable=SC2086
run "the refusal names the entity type it wanted, not just 'missing argument'" 1 \
    "sample_set_set row" "$PY" terra/batch_rerun_step.py --step 09 submit --confirm $PIN09

# POSITIVE CONTROL, still offline: with a cohort row named, submit() really does run, and what reaches
# the submission call is the resolved config name, row and entity type. The submission call is replaced
# by a recorder and every other Terra entry point by a raiser, in process.
#
# Both controls below now also have to satisfy the read-back guard `submit` grew, so they state their
# image pins the way a fully-pinned command line does and hand submit() a workspace copy that agrees.
# That is not decoration: before this change these two legs passed while printing
# `sv_base_mini_docker=UNPINNED`, which is the defect the money charter found.
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


def setup(step="10", extra_argv=(), exempt=False, wire=False, row_exists=True):
    """Import batch_rerun_step the way __main__ leaves it: argv, IMAGES, CLI_IMAGE_KEYS, CONFIRMED.

    Calling submit() directly skips that dispatch, and `IMAGES` is exactly what dispatch fills from the
    command line -- so a leg that forgot it would be testing a rerun with no image pinned at all.
    `exempt=True` leaves one *_docker unpinned and asks for it by flag, which is what dispatch does for
    `--allow-unpinned-docker`.

    `wire=True` is for the legs that need the POST body rather than the call: `terra.submit` and
    `create_submission` stay the real ones and the request boundary is captured (see `wire_capture`), so
    `create_submission` is NOT turned into a raiser in that mode.
    """
    repo = os.environ["GSVTK_REPO_ROOT"]
    sys.path.insert(0, os.path.join(repo, "kit"))
    sys.path.insert(0, os.path.join(repo, "terra"))
    specs = sorted("%s=%s" % (k, v) for k, v in PINSPEC.items())
    if exempt:
        specs = [s for s in specs if "sv_base_mini" not in s]
    sys.argv = (["batch_rerun_step.py", "--step", step] + list(extra_argv)
                + ["submit", "--confirm"] + (["--allow-unpinned-docker"] if exempt else [])
                + [x for s in specs for x in ("--image", s)])
    import terra
    import batch_rerun_step as rr
    blocked = ["create_workspace_config", "overwrite_workspace_config", "validate_config",
               "list_submissions", "get_submission"]
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
    (`recon-live/live-config-01-SingleSample-mwfix.json`, see `scripts/selftest.d/golden/README.md`
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

# And the same for a sample_set step: GSVTK_BATCH is the row, and no --entity is needed. Proves the
# refusal above is the missing row and not a broken code path.
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

# `--entity ""` is the shape a script produces when it passes --entity "$ROW" with ROW unset. It used to be
# indistinguishable from not passing --entity at all, and the fall-through was the workspace's default batch
# row -- a different cohort, submitted, reported as accepted. Measured before the fix: `--step 10 --entity ''
# submit` reached terra.submit with row='all_samples'. The control below is what keeps this a guard rather
# than a wall: a named row must still go straight through.
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

# ------------------- 6b. the row is DERIVED data: the name key decides the type, entity_row the source
# Stage C's first two bullets, in one section: the launcher used to carry `root_entity == "sample_set"`
# as the row rule, and the profile's typed `rootEntityType` used to be the type's source. Both are data
# now, so each of the three ways that can go wrong is fired here rather than argued in a docstring --
# zero name keys, two name keys, and a row source that is not the chain's key.

# Zero name keys: the step reads `workspace.*` only, so NOTHING in the profile states which row it runs
# on, and `rootEntityType` is the one candidate left standing. Refused, naming the reads it did find.
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

# Two name keys: two rows in one step. Choosing one is a guess with a submission behind it, so both are
# printed and neither is taken.
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

# The trap the plan names: 09 reads `this.sample_sets.<attr>` THREE times and `this.sample_set_set_id`
# once, and the member rule derives the wrong type with confidence. The derived answer must be the cohort
# type, and the member collection must not have counted towards it.
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

# The cross-check itself is fired with the guards below (`guards.py` case 1): mutating ONLY the stated
# side of the comparison -- `batch_configs.CONFIGS[...]["rootEntityType"]` -- while the derived side comes
# from the step's own name key. That refusal is the profile's `rootEntityType` doing its new job.

# The generalization, fired: a step whose row comes from a key that is NOT GSVTK_BATCH resolves its row
# from the key its profile names. `ZONE` is nonsense as a row name -- that is the point: what is under
# test is which key the launcher reads, and a fixture that named a plausible key could pass while the
# code still read BATCH. The long spelling also proves the reader canonicalises what it stores.
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

# The `--entity` answer as DATA: a step may state that nothing offline holds its row. The reader prints
# that answer rather than inventing a key, and the launcher for that step refuses -- by naming the field
# and the flag, with no stale claim about the chain's batch row in it.
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

# --------- 7. submit() reads the config back, and refuses before the POST unless it IS the body
# A submission POSTs a config NAME, so what boots is the body the WORKSPACE holds under this step's
# rerun name -- possibly POSTed by another branch, an earlier session or another person. The money
# charter (docs/plan-launch-any-module.md) measured that `submit` ran none of this file's own guards
# while `show` ran all of them, so the pair below is the pair that has to hold: the image refusal
# reaches the money path byte for byte, and a workspace copy that is not this body is refused.
#
# Everything here is in process, offline, and counts requests: the submission call records, every
# mutating fiss entry point raises, and the read-back answers with ONE payload the leg builds.
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

# The read-back, under `--tag`. This is the leg the feature would otherwise introduce a bug with:
# `submit` POSTs a config NAME, so the agreement it checks is between the body it built and whichever
# config the name it asks for resolves to. Ask for the untagged name while submitting the tagged body
# and every field still "agrees" -- with somebody else's config -- and the submission runs that one.
#
# So the fake workspace here answers with the name it was ASKED FOR, the way a real GET does. Had this
# leg reused `server_copy(body)` unchanged, the payload would carry the tagged name however the tool
# asked, the `name` field would agree by construction, and a read-back pointed at the wrong config would
# have been graded green. That fixture-agrees-with-the-test shape is this repo's oldest failure mode (see
# `server_copy`'s own docstring), and it is why this leg does not share `readback.py`'s payload.
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

# The same claim one layer lower, at the bytes: the submission REQUEST must name the tagged config too.
# `terra.submit` takes the name as an argument, so the leg above could pass while a later edit re-derived
# the name for the POST only -- and the POST is the thing Terra runs.
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

# `create` and a config name that is already taken. Terra answers 409, and until this commit the tool
# swallowed it and overwrote, under the same single --confirm that authorised the create. The precedent
# is in this repo's own handoff notes: `10-GenotypeBatch-rerun` was replaced silently.
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

# The entity itself. Rawls accepts a submission naming a row that does not exist -- the POST says 200 and
# the failure arrives later, per workflow, at compute cost. Measured today on the two candidate targets:
# `sample/NA12878` is HTTP 200 in one workspace and HTTP 404 in the other.
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

# The golden refusal, on the money path. `show` produced these bytes for two phases; `submit --confirm`
# used to skip the whole guard and carry on printing `sv_base_mini_docker=UNPINNED`. Same cut as the
# `show` leg above (announce()'s block is stderr output that predates the refusal), same golden.
# shellcheck disable=SC2086
run "submit --confirm refuses an unpinned *_docker before it reads anything (exit 1)" 1 \
    "image inputs are not fully pinned" "$PY" terra/batch_rerun_step.py submit --confirm
run_absent "and it refused before the read-back, not after it" 1 "cannot read the config" \
    "$PY" terra/batch_rerun_step.py submit --confirm
env -u GSVTK_TERRA_API_ROOT "$PY" terra/batch_rerun_step.py submit --confirm >/dev/null 2> "$TMP/su.err"
sed -n '/^image inputs are not fully pinned/,$p' "$TMP/su.err" > "$TMP/su-refusal.txt"
bytes "and in the SAME bytes the golden captures from show (no rewording on the money path)" \
    "$GOLD/rerun-step10-unpinned.txt" "$TMP/su-refusal.txt"

# Fail closed on the read itself. Fully pinned, so the read-back is the last gate -- and this phase's
# Terra root is a closed port, so what the leg grades is the tool's answer to not being able to read.
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
# --allow-unpinned-docker keeps its documented meaning (docs/terra-head-to-head.md section 4), so
# submit honours it -- and names the exemption before the POST, key by key, instead of leaving it in
# argv for someone else to re-derive from a submission log.
run "submit names the exemption it honoured, by flag, before the POST" 1 \
    "--allow-unpinned-docker authorised" \
    "$PY" terra/batch_rerun_step.py submit --confirm --allow-unpinned-docker
run "naming the exact key the flag left workspace-resolved, not just 'some keys'" 1 \
    "GenotypeBatch.sv_base_mini_docker" \
    "$PY" terra/batch_rerun_step.py submit --confirm --allow-unpinned-docker

# -------------------- 8. the call cache submit PRINTS is the one the submission request POSTs
# Section 7 grades the config the workspace holds; this section grades the field the SUBMISSION carries,
# which is a different request and was a different lie. `submit` printed the mode it re-read off argv
# while the submission request carried fiss's own default (`create_submission(..., use_callcache=True)`,
# stated unconditionally in the body it builds), so every rerun asked Terra for `useCallCache: true`
# under a line that said `useCallCache=false` -- on the one field whose wrong value still looks like a
# pass, because a cached call answers before the VM boots (docs/gap-ledger.md C1).
#
# These legs therefore capture at fiss's request boundary with the real submission call (`wire=True`),
# not at `create_submission`, whose arguments would agree while the body disagreed. Two arms of the SAME
# fixture produce two different captured values, which is the positive control on the capture: a seam
# that cannot see the difference cannot grade it either. `useCallCache` is asserted by identity (`is`),
# so a string `"false"` or a missing key is a failure, not a truthiness accident.
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

# And the guard that keeps fiss's default from coming back by omission: a caller that states no choice
# is refused rather than handed `true`. Its control is the three arms above, which POST through this
# same function WITH a stated choice -- without them this leg cannot tell a guard from a dead path.
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
# 9. The publish precondition on the POST path (GitHub issue #3).
#
# `checks/version_resolves.py` answers the question; this section pins what a POST DOES with the answer,
# which is the half that can spend money. The four verdicts must NOT collapse into "refuse / allow":
#   CURRENT   -> proceeds, and says the two shas are the same commit.
#   STALE     -> refuses, naming both shas and the remedy; `--allow-stale-dockstore` proceeds and the
#                exemption is PRINTED before the POST, the same way the image exemption is.
#   ABSENT    -> refuses too (a capture that covers one entry of 341 can produce ABSENT for a published
#                version, which is why the same override covers it), and says which one it is.
#   REFUSED   -> the check declined, so the pin is UNVERIFIED and the POST goes ahead. This is the arm
#                with the trap in it: reading "I could not look" as "not published" is the misread that
#                cost the 2026-10-13 session a day, and hard-stopping here would rebuild the very
#                hard-stop issue #5 just removed from batch_check_inputs.py.
# The tip files are written by the probe, not shipped: `main` happens to be current in the shipped pair,
# so a leg that borrowed it could only ever have measured one of the two arms. The capture with commitID
# rows is issue3's shape-holder; the capture WITHOUT them is the real TRS listing, which is what makes
# the REFUSED arm a measurement of Dockstore's actual API and not of a fixture's mood.
# Nothing here reaches Terra: this phase's API root is a closed port, and the check runs before the
# first request anyway.
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
    for forbidden in ("NOT RUN", "ABSENT", "authorised the Dockstore pin"):
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
    del os.environ["GSVTK_PUBLISH_CAPTURE"], os.environ["GSVTK_PUBLISH_TIP"]
    proceed("with no capture the check says NOT RUN, names both vars, and points at the fix",
            "publish check: NOT RUN", "GSVTK_PUBLISH_CAPTURE, GSVTK_PUBLISH_TIP unset",
            "gsvtk publish --ls-remote", "the name resolving is not evidence")

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
for c in current stale stale-override absent refused refused-flag real-capture notrun create-stale; do
    inproc "publish check on the POST path: $c" 0 "$TMP/publish_pre.py" "$c"
done

if [ "$skipped" -gt 0 ]; then
    # The skipped count is printed when it is non-zero and omitted when it is not, so the string every
    # ledger row and handoff quotes for a full run stays byte-identical on a machine that skipped nothing.
    printf 'rerun: %s ok, %s failed, %s skipped\n' "$ok" "$fail" "$skipped"
else
    printf 'rerun: %s ok, %s failed\n' "$ok" "$fail"
fi
# What this pair does and does not prove, stated exactly, because an earlier version of this comment
# understated its own blindness. BOTH numbers count call SITES now, so the `for s in 06 07 08 09 10` sweep below
# — one call site, five executions — counts ONE site on each side, and the constant this file used to carry
# (GSVTK_DECL_SURPLUS=4) is gone. Measured here: 63 sites declared, 63 sites reached, 79 verdicts — and 79 is
# exactly 63 plus the 16 extra executions of the three sweep sites (5 for the step sweep, 11 for the read-back
# cases, 3 for the call-cache arms of section 8). Consequences:
#   - Hiding an assertion behind a condition that cannot hold is caught by the FIRST one, by line number: the
#     reporter prints the site lines that never ran. Under the old execution count this file could hide four of
#     them (an adversarial review measured that many), because four missing sites looked like the declared loop
#     surplus. Exact set equality is now the only tolerance, in every mode.
#   - DELETING a call site is STILL not caught, and that limit survived the rewrite to sites: declared and
#     reached drop together, so every site in this file can be deleted and the pair still prints an all-clear.
#     Re-measured after the rewrite, not assumed. The pair's teeth are against sites that exist and do not run —
#     see the header of scripts/selftest.d/declared.sh and docs/gap-ledger.md C10. If deletion-detection is ever
#     wanted, diff this number against the value in the PREVIOUS commit; do not pin a constant.
#   - 64 verdicts against 50 sites is itself the honest statement of a third gap: the two canary blocks above
#     record verdicts at top level rather than through a recorder, so they are verdicts and not sites. That is
#     why the line the reporter prints states both totals side by side instead of one number.
# The whole-phase bail further up also exits 0, deliberately: a machine without `firecloud` must still be able
# to run the offline gate. That is why the bail line itself says a SKIP is not a pass in CI, and why the
# declared/reached line beside it is the SAME guard as this one, run over the rerun-no-firecloud region that
# covers the rest of this file.

gsvtk_declared_report "rerun" "$SELF" "$((ok + fail))" "$skipped" || fail=$((fail + 1))
[ "$fail" -eq 0 ] || exit 1
exit 0
