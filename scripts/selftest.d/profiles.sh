#!/usr/bin/env bash
# scripts/selftest.d/profiles.sh — the module-profile assertions, standalone.
#
#     bash scripts/selftest.d/profiles.sh .venv/bin/python
#
# Covers the profile data itself: the golden capture, the loader's refusals, and the template finding the
# WDL alone cannot give. Needs no user profile, no gatk-sv checkout and no network (the template case reads
# a fixture tree via `--wdl-dir`/`--tmpl-dir`), except section 11e, which grades profiles/single_sample.json
# against a real checkout at a pinned ref and is a named, counted skip (region `single-sample-check`) where
# the checkout or miniwdl is absent.
#
# The last assertion is a failure assertion: a dropped binding must make the golden check FAIL.
# Own tally, own exit code. bash 3.2 compatible.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi

FIXTURE="$ROOT/scripts/selftest.d/fixtures/genotyping.profile.env"
ok=0; fail=0; skipped=0

# Some legs need miniwdl (a dev dependency, not installed by `make setup`). When it is missing, the tool's
# own refusal is recorded as a counted skip, checked before the verdict so an absence assertion cannot pass
# vacuously.
HAS_WDL=1
"$PY" -c 'import WDL' >/dev/null 2>&1 || HAS_WDL=0

no_miniwdl() {
    [ "$HAS_WDL" = 0 ] || return 1
    # The three tools that need miniwdl each name it in their own words; any other failure still fails.
    grep -qE "status=NO-MINIWDL|needs the miniwdl package|miniwdl not found" <<< "$2" || return 1
    skipped=$((skipped + 1))
    printf '  SKIP  %s — the required-input layer read no AST (the tool named miniwdl as missing)\n' "$1"
    printf '        (pip install -r requirements-dev.txt; CI installs it, which is where these run)\n'
    return 0
}
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"

# ------------------------------------------------- declared vs reached
# The declared assertion call sites must equal the sites the run reached. Verdicts can outnumber sites: a
# `want` inside a loop is one site with many verdicts, and section 10 records a verdict outside any recorder.
SELF="$ROOT/scripts/selftest.d/profiles.sh"
. "$ROOT/scripts/selftest.d/declared.sh"
GSVTK_DECL_FUNCS="want canary want_not"
# §11c/§11d shell out to `./gsvtk`, which needs an interpreter with firecloud; let an exported value win.
gsvtk_phase_interpreters
gsvtk_declared_init "$TMP"          # the ledger is the set of reached sites (bash 3.2: no associative arrays)

# want DESC WANT_RC NEEDLE... -- CMD...
# Asserts the exit code AND that every needle appears in the combined output.
want() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # the line that CALLED want(); one entry per call SITE, not per loop turn
    local desc="$1" wantrc="$2"; shift 2
    local needles=() out rc
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do needles=("${needles[@]-}" "$1"); shift; done
    shift                                   # the -- separator
    out="$("$@" 2>&1)"; rc=$?
    local miss="" n
    for n in "${needles[@]-}"; do
        [ -n "$n" ] || continue
        grep -qF -- "$n" <<< "$out" || miss="$miss
          missing: $n"
    done
    if no_miniwdl "$desc" "$out"; then
        :
    elif [ "$rc" = "$wantrc" ] && [ -z "$miss" ]; then
        ok=$((ok + 1)); printf '  ok    %s (exit %s)\n' "$desc" "$rc"
    else
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s, want %s)%s\n' "$desc" "$rc" "$wantrc" "$miss"
        printf '%s\n' "$out" | head -8 | sed 's/^/          /'
    fi
}

# This file's own canary: a tally that cannot go down certifies nothing.
canary() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see want() above
    local out rc
    out="$(false 2>&1)"; rc=$?
    if [ "$rc" -ne 0 ]; then
        ok=$((ok + 1)); printf '  ok    this file notices a failing command (canary)\n'
    else
        fail=$((fail + 1)); printf '  FAIL  the harness in %s is vacuous\n' "$0"
    fi
}

# want_not DESC WANT_RC NEEDLE... -- FORBIDDEN... -- CMD...
# Like `want`, and additionally no FORBIDDEN string may appear (e.g. CURRENT and STALE in the same run).
want_not() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # the line that CALLED want_not(); one entry per call SITE
    local desc="$1" wantrc="$2"; shift 2
    local needles=() forbidden=() out rc n miss=""
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do needles=("${needles[@]-}" "$1"); shift; done
    shift                                   # the first -- separator
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do forbidden=("${forbidden[@]-}" "$1"); shift; done
    shift                                   # the second one
    out="$("$@" 2>&1)"; rc=$?
    for n in "${needles[@]-}"; do
        [ -n "$n" ] || continue
        grep -qF -- "$n" <<< "$out" || miss="$miss
          missing: $n"
    done
    for n in "${forbidden[@]-}"; do
        [ -n "$n" ] || continue
        if grep -qF -- "$n" <<< "$out"; then
            miss="$miss
          printed what it must not: $n"
        fi
    done
    if no_miniwdl "$desc" "$out"; then
        :
    elif [ "$rc" = "$wantrc" ] && [ -z "$miss" ]; then
        ok=$((ok + 1)); printf '  ok    %s (exit %s)\n' "$desc" "$rc"
    else
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s, want %s)%s\n' "$desc" "$rc" "$wantrc" "$miss"
        printf '%s\n' "$out" | head -8 | sed 's/^/          /'
    fi
}

echo
echo "selftest: the module profile is the same data the maps were (offline, no user profile)"
canary

# 1. The golden: `show` and all five `body()` maps, byte-equal to a checked-in capture.
want "the maps match the golden byte for byte" 0 \
    "byte-identical to the goldens" "5 config(s), 64 input binding(s), 39 output binding(s)" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" "$PY" scripts/golden_configs.py

# 2. Control for the golden: dropping one input and one output binding must make the check FAIL, and the
#    untouched capture must pass first.
want "a planted dropped binding makes the golden check FAIL" 0 \
    "planted drop of GenerateBatchMetrics.median_file from 06-GenerateBatchMetrics.inputs -> the golden check FAILS" \
    "planted drop of GenotypeBatch.regeno_coverage_medians from 10-GenotypeBatch.outputs -> the golden check FAILS" \
    "control: ok" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" "$PY" scripts/golden_configs.py --control

# Refusals are tested against a temp MODULE_DIR holding one broken copy, never the tracked profile.
broken() {                                  # broken() NAME EDIT... -> a copy of the profile carrying EDIT
    local name="$1"; shift
    mkdir -p "$TMP/mod-$name"
    "$PY" - "$ROOT/profiles/genotyping.json" "$TMP/mod-$name/genotyping.json" "$@" <<'PY'
import json, sys
src, dst = sys.argv[1], sys.argv[2]
doc = json.load(open(src))
for e in sys.argv[3:]:
    field, _, value = e.partition("=")
    if field == "schema_version":
        doc["schema_version"] = int(value)
    elif field == "orphan_why":
        doc["steps"][4][value] = "an orphan rationale for a key nothing binds"
    elif field == "literal_token":
        doc["steps"][0]["inputs"][value] = "100{frz}"
    elif field == "split_wdl":
        step = doc["steps"][0]
        step["wdl"], step["workflow"] = "DepthClustering", "ClusterDepth"
    elif field == "rename_output":                        # old:new attribute name, on the LAST step
        old, _colon, new = value.partition(":")
        step = doc["steps"][-1]
        outs = step["outputs"]
        # What drifts in real life is the ATTRIBUTE the step writes (the value), so the value moves with
        # the key: a guard that read only the binding keys would not notice either.
        for k in list(outs):
            if k.endswith("." + old):
                outs[k[:-len(old)] + new] = outs.pop(k).replace("this." + old, "this." + new)
    elif field == "entity_row":                           # a JSON value for the LAST step's `entity_row`
        doc["steps"][-1]["entity_row"] = json.loads(value)
    elif field == "export":                               # a JSON value for the LAST step's `export`
        doc["steps"][-1]["export"] = json.loads(value)
    elif field == "steps":                                 # the whole `steps` list, for the empty case
        doc["steps"] = json.loads(value)
    elif field == "export_strip":                          # no `export` anywhere in the profile
        step = doc["steps"][-1]
        step.pop("export", None)
        # `_why_export` goes with it. Rule 6's pairing is enforced, so a stripped `export` and a kept
        # rationale refuse for the ORPHAN reason -- a different claim, and the one that would make the
        # no-export refusal below assert the wrong sentence.
        step.pop("_why_export", None)
json.dump(doc, open(dst, "w"), indent=2)
PY
}

# 3. An unknown `schema_version` must refuse, not best-effort parse.
broken schema_version schema_version=99
want "an unknown schema_version refuses rather than parsing anyway" 4 \
    "is unusable" "schema_version 99 is not a version this build reads" \
    "genotyping.json" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-schema_version" \
    "$PY" terra/batch_configs.py show

# 4. A missing module names the file to write and every field a working profile needs.
want "a missing module exits 4, names the file, and lists every field in full" 4 \
    "is missing" "profiles/copynumber.json" "schema_version" "callers" "steps" \
    "step " "wdl " "workflow" "rootEntityType" "inputs" "outputs" "branch_only_inputs" \
    "bare new-side attribute names" "entity_row" "_why_" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE=copynumber \
    "$PY" terra/batch_configs.py show

# 4b. A profile that parses but declares `steps: []`, refused by every reader of the tables.
#
# Exit codes differ by door: `Loaded.require()` (behind `batch_configs show|check|create|validate|body()`)
# exits 4, while `terra/steps.py` builds the step map at import and exits 1. Both name the file.
broken steps_empty steps='[]'
want "a profile that parses but declares no steps: check exits 4 and says the empty chain is the finding" 4 \
    "is unusable" "\`steps\` is not a non-empty list" "drives no step" \
    "an empty chain is exactly what reads like nothing was wrong" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-steps_empty" \
    "$PY" terra/batch_configs.py check
want "and the same empty module is refused by \`show\`, the read-only door, at 4 as well" 4 \
    "show: module profile" "is unusable" "$TMP/mod-steps_empty/genotyping.json" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-steps_empty" \
    "$PY" terra/batch_configs.py show
# `terra/steps.py` builds the step map at import, so every terra/ tool inherits this refusal.
want "the launcher's reader refuses the same empty module at import, naming the file and the finding" 1 \
    "terra/steps.py cannot build the step map" "$TMP/mod-steps_empty/genotyping.json" \
    "\`steps\` is not a non-empty list" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-steps_empty" \
    "$PY" -c 'import sys; sys.path.insert(0, "terra"); import steps; print(sorted(steps.STEPS))'
want "and an ABSENT profile file is the same door, at the same code, with the file it looked for" 1 \
    "terra/steps.py cannot build the step map" "no profile at" "nosuchmodule-this-leg-invented-it.json" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE=nosuchmodule-this-leg-invented-it \
    "$PY" -c 'import sys; sys.path.insert(0, "terra"); import steps; print(sorted(steps.STEPS))'
# Control: the same doors answer on the shipped profile, so the refusals above are about the data.
want "CONTROL: the shipped profile drives both doors to an answer (so the refusals above are about data)" 0 \
    "['06', '07', '08', '09', '10']" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" \
    "$PY" -c 'import sys; sys.path.insert(0, "terra"); import steps; print(sorted(steps.STEPS))'

# 5. An orphan `_why_` key refuses.
broken orphan_why orphan_why=_why_GenotypeBatch.nope_bound
want "an orphan _why_ sibling refuses, by name" 4 \
    "orphan rationale" "_why_GenotypeBatch.nope_bound" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-orphan_why" \
    "$PY" terra/batch_configs.py show

# 6. Literal-vs-path typing (§3 rule 3): a suffix token on a non-path value is a contradiction.
broken literal_token literal_token=GenerateBatchMetrics.chr_x
want "a suffix token on a non-path value refuses as a typing error" 4 \
    "carries a suffix token but is not a path" "this./workspace." -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-literal_token" \
    "$PY" terra/batch_configs.py show

# 7. `wdl` and `workflow` are separate fields; this case makes them differ.
broken split_wdl split_wdl=x
want "wdl and workflow are carried separately, not as one string serving three lookups" 0 \
    "wdl=DepthClustering workflow=ClusterDepth" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-split_wdl" \
    "$PY" kit/module_profile.py --print

# 8. Template states: DERIVED / JINJA-NEUTRALISED / NO TEMPLATE. Built from fixtures with --wdl-dir and
#    --tmpl-dir. The control removes the optional key from the template, and the finding must go away.
TD="$TMP/tmpl"; WD="$TMP/wd"; CD="$TMP/cfg"
mkdir -p "$TD" "$WD" "$CD" "$TMP/empty-templates"
cat > "$WD/Tiny.wdl" <<'WDL'
version 1.1
workflow Tiny {
  input {
    String batch
    File? upstream_pins_this
  }
  call tiny_task
  output { File out = tiny_task.one }
}
task tiny_task {
  command <<< echo x > one.txt >>>
  output { File one = "one.txt" }
  runtime { docker: "x" }
}
WDL
cat > "$CD/tiny.json" <<'CFG'
{"namespace": "selftest", "name": "tiny", "rootEntityType": "sample_set",
 "methodRepoMethod": {"sourceRepo": "dockstore",
                      "methodPath": "github.com/broadinstitute/gatk-sv/Tiny",
                      "methodVersion": "selftest-ref"},
 "inputs": {"Tiny.batch": "this.sample_set_id"},
 "outputs": {"Tiny.out": "this.tiny_out_new"}}
CFG
cat > "$TD/Tiny.json.tmpl" <<'TMPL'
{
  "Tiny.batch": "${this.sample_set_id}",
  "Tiny.upstream_pins_this": {{ values.upstream_pins_this | tojson }},
  "Tiny.a_jinja_only_key": {{ values.other | tojson }}
}
TMPL
want "a Jinja template reads as JINJA-NEUTRALISED and names the optional input production pins" 0 \
    "TEMPLATE JINJA-NEUTRALISED" "2 value(s) were" \
    "optional-bound-upstream-but-omitted  Tiny.upstream_pins_this" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" "$PY" terra/batch_configs.py check \
    --config "$CD/tiny.json" --wdl-dir "$WD" --tmpl-dir "$TD"
cat > "$TD/Tiny.json.tmpl" <<'TMPL'
{
  "Tiny.batch": "${this.sample_set_id}"
}
TMPL
want "CONTROL: the same tree with nothing optional bound upstream says DERIVED and stays silent" 0 \
    "TEMPLATE DERIVED" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" "$PY" terra/batch_configs.py check \
    --config "$CD/tiny.json" --wdl-dir "$WD" --tmpl-dir "$TD"

# 8b. Duplicate keys in a template. A strict JSON parse is last-wins, so the duplicate is reported. The
#     pair shows the census counts documents (0 vs 1), and the exit code stays 0 in both: upstream's own
#     templates carry duplicates, so making this fatal would refuse launches that work.
cat > "$TD/Tiny.json.tmpl" <<'TMPL'
{
  "Tiny.batch": "${this.sample_set_id}",
  "Tiny.mei_bed": {{ values.first | tojson }},
  "Tiny.mei_bed": "${this.median_cov}",
  "Tiny.upstream_pins_this": {{ values.upstream_pins_this | tojson }}
}
TMPL
want "a key bound twice is named, both sides are printed, and it does NOT move the exit code" 0 \
    "DUPLICATE-KEY  Tiny.mei_bed -- bound 2 time(s)" '`json.loads` keeps the' \
    "earlier binding(s) (a Jinja value, unread without rendering) are dropped silently" \
    "template duplicate-key census: 1 key(s) bound more than once in 1 of 1" \
    "1 binding(s) dropped by last-wins" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" "$PY" terra/batch_configs.py check \
    --config "$CD/tiny.json" --wdl-dir "$WD" --tmpl-dir "$TD"
cat > "$TD/Tiny.json.tmpl" <<'TMPL'
{
  "Tiny.batch": "${this.sample_set_id}",
  "Tiny.mei_bed": "${this.median_cov}",
  "Tiny.upstream_pins_this": {{ values.upstream_pins_this | tojson }}
}
TMPL
want "CONTROL: the same template with every key bound once counts 0 of 1 READ, not 'census absent'" 0 \
    "template duplicate-key census: 0 key(s) bound more than once in 0 of 1" \
    "0 binding(s) dropped by last-wins" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" "$PY" terra/batch_configs.py check \
    --config "$CD/tiny.json" --wdl-dir "$WD" --tmpl-dir "$TD"
want "CONTROL: NO TEMPLATE is its own state, and it says the finding had no source" 0 \
    "NO TEMPLATE" "its absence means nothing" \
    "template duplicate-key census: NOT READ" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" "$PY" terra/batch_configs.py check \
    --config "$CD/tiny.json" --wdl-dir "$WD" --tmpl-dir "$TMP/empty-templates"

# `terra/batch_configs.py` and `checks/terra_entity_check.py` implement the duplicate-key rule separately,
# so run one template through both CLIs and compare the names they print.
cat > "$TMP/dupkey-sibling.py" <<'PYCODE'
"""One template, both duplicate-key readers, compared by the NAMES they print.

argv: ROOT WDL-DIR CONFIG-JSON TEMPLATE-FIXTURE WORK

The duplicate arm must be named by both readers; the clean arm must be named by NEITHER. The second half
is the control, and it is the half a grep-for-a-needle leg cannot express at all: without it, a reader
that reported a duplicate in every document it ever saw would pass this leg.
"""
import atexit
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT, WD, CFG, FIXTURE, WORK = sys.argv[1:6]
DUP = ('{\n  "Tiny.batch": "${this.sample_set_id}",\n'
       '  "Tiny.mei_bed": "${workspace.reference_mei_bed}",\n'
       '  "Tiny.mei_bed": "${this.median_cov}"\n}\n')
CLEAN = ('{\n  "Tiny.batch": "${this.sample_set_id}",\n'
         '  "Tiny.mei_bed": "${this.median_cov}"\n}\n')
ENV = dict(os.environ, GSVTK_CONFIG=FIXTURE, GSVTK_WORK=WORK)
PY = sys.executable
LAST = {}     # tail of the last tool output, so an assertion says WHY the tool moved its exit code

tmp = tempfile.mkdtemp()
atexit.register(shutil.rmtree, tmp, True)     # reading two checkers must not litter, same as _tree_from_ref


def batch_names(dirpath):
    """(names, exit code) from `check`: the DUPLICATE-KEY lines it printed, and whether it stayed fatal."""
    r = subprocess.run([PY, "terra/batch_configs.py", "check", "--config", CFG,
                        "--wdl-dir", WD, "--tmpl-dir", dirpath],
                       cwd=ROOT, env=ENV, capture_output=True, text=True)
    # One interpreter lacks miniwdl and cannot answer what the workflow declares; `check` says so in words
    # ("needs the miniwdl package"). Propagate THAT sentence on our own stdout and stop, so the phase's
    # recorder sees the tool's own words and records a prerequisite skip. Exiting 0 with an empty name list
    # would be the worse answer: two checkers "agreeing" on nothing is exactly the vacuous agreement this
    # leg exists to refuse.
    # See `no_miniwdl` for why three sets of words and not one. Propagating the tool's own sentence and
    # stopping is the honest answer here; exiting 0 with an empty name list would be two checkers "agreeing"
    # on nothing, which is exactly the vacuous agreement this leg exists to refuse.
    bad = r.stdout + r.stderr
    LAST["tail"] = bad.strip().splitlines()[-1][:140] if bad.strip() else ""
    if any(w in bad for w in ("needs the miniwdl package", "miniwdl not found", "status=NO-MINIWDL")):
        print("needs the miniwdl package (propagated from terra/batch_configs.py check: " + LAST["tail"] + ")")
        sys.exit(70)
    return re.findall(r"DUPLICATE-KEY\s+(\S+)", r.stdout), r.returncode


def corpus_names(tree):
    """Names off the `GSVTK-ENTITY ... dupkeys=` field; `0` is that field's word for none.

    `--tree` wants the TREE ROOT and derives `inputs/templates/terra_workspaces` itself; handing it the
    leaf it unpacks to is a corpus with no config in it, which is that checker's exit 3 and prints zero
    `dupkeys=` fields -- an empty answer that looks exactly like a clean one.
    """
    r = subprocess.run([PY, "checks/terra_entity_check.py", "--tree", tree],
                       cwd=ROOT, env=ENV, capture_output=True, text=True)
    assert "GSVTK-ENTITY config=" in r.stdout, ("the corpus reader named nothing at all", r.stdout[:300])
    return [d for d in re.findall(r"\bGSVTK-ENTITY.*?\bdupkeys=(\S+)", r.stdout) if d != "0"]


for doc, expect, arm in ((DUP, ["Tiny.mei_bed"], "duplicate"), (CLEAN, [], "control")):
    b = os.path.join(tmp, "batch-" + arm)
    tree = os.path.join(tmp, "sib-" + arm)
    os.makedirs(b)
    os.makedirs(os.path.join(tree, "inputs", "templates", "terra_workspaces", "zeta"))
    for path in (os.path.join(b, "Tiny.json.tmpl"),
                 os.path.join(tree, "inputs", "templates", "terra_workspaces", "zeta",
                              "Tiny.json.tmpl")):
        open(path, "w").write(doc)
    found_b, rc_b = batch_names(b)
    found_e = corpus_names(tree)
    assert rc_b == 0, (arm, "a duplicate key moved the exit code:", rc_b, LAST.get("tail", ""))
    assert found_b == expect, (arm, "the template reader said", found_b, "want", expect)
    assert found_e == expect, (arm, "the corpus reader said", found_e, "want", expect)

print("PASS two readers, one document: the duplicate is named by both and the clean document is named "
      "by neither")
PYCODE
want "the template reader here and the offline corpus checker name the SAME duplicate on the SAME bytes" 0 \
    "PASS" -- "$PY" "$TMP/dupkey-sibling.py" "$ROOT" "$WD" "$CD/tiny.json" "$FIXTURE" "$TMP/work"

# 9. `gsvtk-config show` reports the module and its source; MODULE_DIR derives to the repo's profiles/.
want "gsvtk-config show answers which module, from where, and whether it exists" 0 \
    "module genotyping [default]" "profiles/genotyping.json" "[derived: <repo>/profiles]" \
    "present" -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" ./kit/gsvtk-config show
want "and the same door says MISSING rather than implying a module is configured" 0 \
    "module copynumber [env]" "MISSING" -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_MODULE=copynumber \
    ./kit/gsvtk-config show

# 10. Reading a profile must not create the scratch directory.
want "reading a profile creates no file and no directory" 0 \
    "byte-identical" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/never-created" "$PY" scripts/golden_configs.py
if [ -e "$TMP/never-created" ]; then
    fail=$((fail + 1)); printf '  FAIL  reading the profile created %s\n' "$TMP/never-created"
else
    ok=$((ok + 1)); printf '  ok    and the scratch directory it could have created is still absent\n'
fi

# 11. The export list.
#
# export-guard.py checks the DATA: the attributes `fetch` requires vs what the profile says the chain
# writes. export-data.py checks the WIRING: that `fetch` plans what `steps[].export` says. A shell that
# hardcoded the list would pass the guard, so the probe is needed too. `fetch --dry-run` touches no Terra
# or network, so neither has a SKIP branch.
#
# The known delta: the terminal step writes 8 non-index attributes, `export` holds 7, and the difference
# (`regeno_coverage_medians`) is fetched as optional. Making it required would make `fetch` exit 1 for any
# baseline that never wrote it.
cat > "$TMP/export-guard.py" <<'PY'
"""The export list the fetch plans vs the attributes the module profile says the chain writes.

Both sides are read, never typed: the script's list comes from its own dry-run plan, the chain's from
`kit/module_profile.load()` (so `GSVTK_MODULE_DIR` moves the derived side, which is how the control below
breaks it). The suffix that turns an attribute into a workspace attribute name comes from the one
resolver both languages use, `module_profile.suffixes()`.

What this does NOT check is on purpose: it never asks whether the shell read `export`. That is
`export-data.py`'s job, and merging them would hide the case that matters -- a shell that still plans a
list it typed itself would satisfy every assertion here.
"""
import os
import subprocess
import sys

ROOT = sys.argv[1]
sys.path.insert(0, os.path.join(ROOT, "kit"))
import module_profile                                          # noqa: E402  (kit/ is a sys.path entry)

SFX = module_profile.suffixes()["new"]
REQUIRED_PLAN = "gsutil cp"                       # the line shape of a REQUIRED attribute in --dry-run
OPTIONAL_PLAN = "fetched only if the config exports it"
# The known delta, stated as data so the guard fails the day it stops being the whole story.
KNOWN_EXTRA = "regeno_coverage_medians"

bad, good = [], []


def check(cond, desc, detail=""):
    (good if cond else bad).append(desc)
    print(("  ok    " if cond else "  FAIL  ") + desc + (("  <%s>" % detail) if detail else ""))


def plan_lines(work):
    """(exit, required names, optional names) from `batch_fetch_compare.sh fetch --dry-run`."""
    env = dict(os.environ)
    env.update({"GSVTK_WORK": work, "GSVTK_PYTHON": sys.executable, "TERRA_PY": sys.executable,
                # the offline belt this phase's sibling rerun.sh uses: an escaped request is a refused
                # connection here, never a call to Terra
                "GSVTK_TERRA_API_ROOT": "http://127.0.0.1:9/api/"})
    r = subprocess.run(["bash", "terra/batch_fetch_compare.sh", "fetch", "--dry-run"],
                       cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True)
    req, opt = [], []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.startswith("[plan] "):
            continue
        name = line[len("[plan] "):].split(":", 1)[0].strip()
        if name.endswith(SFX):
            name = name[:-len(SFX)]
        if REQUIRED_PLAN in line:
            req.append(name)
        elif OPTIONAL_PLAN in line:
            opt.append(name)
    return r.returncode, req, opt, r.stdout


def leaf(value):
    """`this.cutoffs_new` / `workspace.merge_batch_sites_vcf_new` -> the attribute name."""
    for prefix in ("this.", "workspace."):
        if value.startswith(prefix):
            name = value[len(prefix):]
            return name[:-len(SFX)] if name.endswith(SFX) else name
    return None


os.chdir(ROOT)
rc, req, opt, out = plan_lines(sys.argv[2])
check(rc == 0 and len(req) == 7 and len(opt) == 10,
      "fetch --dry-run plans exactly 7 required + 10 optional attributes, and exits 0 (a plan loop that "
      "ran zero times would print neither)", "exit %s, %d required, %d optional" % (rc, len(req), len(opt)))

prof = module_profile.load()
prof.require("profiles.sh export guard")
steps = sorted(prof.configs)
terminal = steps[-1]
term_out = set(filter(None, (leaf(v) for v in prof.configs[terminal]["outputs"].values())))
term_nonindex = {a for a in term_out if not a.endswith("_index")}
all_out = set()
for s in steps:
    all_out |= set(filter(None, (leaf(v) for v in prof.configs[s]["outputs"].values())))

check(terminal.startswith("10-") and len(term_out) == 10 and len(term_nonindex) == 8,
      "the module profile's terminal step writes 10 attributes, 8 of them not an `_index` sibling "
      "(index closure is §3 rule 4's code rule, so an index is never itself a required fetch)",
      "%s writes %d, %d non-index" % (terminal, len(term_out), len(term_nonindex)))
check(not (set(req) - term_nonindex),
      "every attribute the script calls REQUIRED is one the terminal step actually writes (a fetch entry "
      "for an attribute no step writes is a permanent `[missing]` line)",
      "not written: %s" % ", ".join(sorted(set(req) - term_nonindex)))
check(not (set(opt) - all_out),
      "every OPTIONAL name is an attribute some step of the chain writes, across all %d steps "
      "(the optional list is chain data too, not a wishlist)" % len(steps),
      "not written: %s" % ", ".join(sorted(set(opt) - all_out)))
extra, missing = sorted(term_nonindex - set(req)), sorted(set(req) - term_nonindex)
check(extra == [KNOWN_EXTRA] and not missing,
      "the delta between the derived 8 and the fetched 7 is exactly the one named attribute, and it is "
      "listed by the script as OPTIONAL rather than required",
      "derived-not-fetched %s, fetched-not-derived %s, optional list holds it: %s"
      % (extra or "-", missing or "-", KNOWN_EXTRA in opt))
check(KNOWN_EXTRA in opt,
      "and the delta name is fetched when present, so nothing is dropped -- only not demanded",
      "optional plan: %s" % ", ".join(opt))

print("export-list guard: %d ok, %d failed  (delta: %s is derived-and-optional, never derived-and-required)"
      % (len(good), len(bad), KNOWN_EXTRA))
sys.exit(1 if bad else 0)
PY
want "the fetch loop's 7 required attributes agree with what the module profile says the chain writes" 0 \
    "export-list guard: 6 ok, 0 failed" "regeno_coverage_medians is derived-and-optional" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/guardwork" \
    "$PY" "$TMP/export-guard.py" "$ROOT" "$TMP/guardwork"
# Control: rename a terminal-step output in the profile. `export` is separate data and does not follow, so
# the guard must fail by name.
broken rename_output rename_output=genotyping_pe_table:genotyping_pe_table_v2
want "CONTROL: renaming a terminal-step output in the profile breaks the export-list guard, by name" 1 \
    "FAIL" "genotyping_pe_table_v2" "export-list guard: " -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/guardwork2" GSVTK_MODULE_DIR="$TMP/mod-rename_output" \
    "$PY" "$TMP/export-guard.py" "$ROOT" "$TMP/guardwork2"

# 11b. Does the shell obey `export`? Edits a temp copy of the profile and requires the plan to move.
cat > "$TMP/export-data.py" <<'PY'
"""Does `fetch` plan what `export` says? The positive control for the list-as-data change.

Five checks, all about the WIRING rather than the data, because the guard above cannot see any of them:

  1. `fetch --dry-run`'s required plan IS the profile's `export` list -- same names, same order -- so the
     script holds no copy of the list it fetches.
  2. `fetch --dry-run` PRINTS what it derived and names the field it came from, because a plan you cannot
     read is a plan you cannot check.
  3. Rename one name in `export` in a temp profile and the plan names the new attribute and stops naming
     the old one. This is what makes check 1 a claim about behaviour rather than a paraphrase.
  4. Strip `export` from every step and `fetch` REFUSES nonzero and prints no required download line at
     all. An empty plan that exits 0 is this repo's named failure class.
  5. ... and the refusal says WHERE it looked: the field, and every step it was looked for on.

FALSIFIED against the consumer, not the data. With `terra/batch_fetch_compare.sh` restored to the version
that typed the seven names, the guard above still prints "export-list guard: 6 ok, 0 failed" -- those seven
literals agree with the profile's eight non-index outputs, and that is all it ever looked at -- while FOUR
of the five checks here FAIL: 2 (no provenance line), 3 (the plan does not move when the field moves), 4 (a
profile with no `export` still plans seven downloads and exits 0) and 5 (no refusal text exists). Check 1
PASSES there, and that is the finding worth keeping: seven hand-typed names in the right order agree with
seven names in a field, so agreement ALONE certifies nothing -- 3, 4 and 5 are the checks that say whether
the shell is asking. The measurement is in the message of the commit that added this probe.
"""
import json
import os
import subprocess
import sys

ROOT, WORK = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(ROOT, "kit"))
import module_profile                                          # noqa: E402  (kit/ is a sys.path entry)

SFX = module_profile.suffixes()["new"]
OPTIONAL_PLAN = "fetched only if the config exports it"
FROM, TO = "genotyping_pe_table", "genotyping_pe_table_v2"
bad, good = [], []


def check(cond, desc, detail=""):
    (good if cond else bad).append(desc)
    print(("  ok    " if cond else "  FAIL  ") + desc + (("  <%s>" % detail) if detail else ""))


def plan(module_dir="", work=WORK):
    """(rc, required names in plan order, combined output) from `fetch --dry-run`.

    The required lines are read structurally -- a `[plan] <attr><suffix>:` line that is not an optional
    one -- rather than by matching the `gsutil` string, so the check does not quietly depend on what the
    operator's $GSUTIL happens to be. Dry-run never executes a download either way.
    """
    env = dict(os.environ)
    env.update({"GSVTK_WORK": work, "GSVTK_PYTHON": sys.executable, "TERRA_PY": sys.executable,
                # the offline belt this phase's sibling rerun.sh uses: an escaped request is a refused
                # connection here, never a call to Terra
                "GSVTK_TERRA_API_ROOT": "http://127.0.0.1:9/api/"})
    if module_dir:
        env["GSVTK_MODULE_DIR"] = module_dir
    r = subprocess.run(["bash", "terra/batch_fetch_compare.sh", "fetch", "--dry-run"], cwd=ROOT,
                       env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True)
    req = []
    for line in r.stdout.splitlines():
        body = line.strip()
        if not body.startswith("[plan] "):
            continue
        name = body[len("[plan] "):].split(":", 1)[0].strip()
        if not name.endswith(SFX) or OPTIONAL_PLAN in body:
            continue
        req.append(name[:-len(SFX)])
    return r.returncode, req, r.stdout


def variant(tag, mutate):
    """A copy of the tracked profile in a temp MODULE_DIR, with `export` edited. Never the tracked file."""
    d = os.path.join(WORK, "mod-" + tag)
    os.makedirs(d, exist_ok=True)
    doc = json.load(open(os.path.join(ROOT, "profiles", "genotyping.json")))
    mutate(doc["steps"][-1], doc)
    json.dump(doc, open(os.path.join(d, "genotyping.json"), "w"), indent=2)
    return d


os.chdir(ROOT)
reader = module_profile.load()
reader.require("export-data probe")
exported = reader.require_exports("export-data probe")

rc, req, out = plan()
check(rc == 0 and req == exported,
      "the required plan IS the profile's `export` list, same names in the same order "
      "(the script holds no copy of it: order is fetch order, so order is asserted too)",
      "exit %s, planned %s, export %s" % (rc, req, exported))
check("[export]" in out and "steps[].export" in out and ("%d required attribute(s)" % len(exported)) in out,
      "and fetch prints the list it derived, how many it derived, and which field it came from",
      "no [export] provenance line" if "[export]" not in out else "")


def rename(step, _doc):
    step["export"] = [TO if a == FROM else a for a in step["export"]]


rc2, req2, _o2 = plan(variant("rename", rename))
check(rc2 == 0 and TO in req2 and FROM not in req2 and len(req2) == len(exported),
      "CONTROL: renaming one name in `export` in a TEMP profile renames what the shell plans -- the old "
      "name is gone from the plan, not merely joined by a new one",
      "exit %s, planned %s" % (rc2, req2))


def strip(step, _doc):
    step.pop("export", None)
    # `_why_export` goes with it: rule 6's pairing is enforced, so a kept rationale would refuse for the
    # ORPHAN reason, which is a different (already-pinned) claim and would let this check pass on the
    # wrong sentence.
    step.pop("_why_export", None)


rc3, req3, out3 = plan(variant("strip", strip))
check(rc3 != 0 and not req3,
      "CONTROL: a profile with no `export` makes fetch exit nonzero and plan NOT ONE download "
      "(an empty plan that exits 0 would write an empty MANIFEST and call the run clean)",
      "exit %s, %d required line(s)" % (rc3, len(req3)))
check("exports nothing" in out3 and "was looked for on" in out3
      and "06-GenerateBatchMetrics" in out3 and "10-GenotypeBatch" in out3,
      "and the refusal names the profile, the field, and EVERY step it looked for `export` on",
      "refusal text missing steps or field")

print("export-data control: %d ok, %d failed  (the data guard cannot see one of these)"
      % (len(good), len(bad)))
sys.exit(1 if bad else 0)
PY
want "fetch plans exactly the profile's export list, and a temp profile's export list moves the plan" 0 \
    "export-data control: 5 ok, 0 failed" 'CONTROL: renaming one name in `export`' -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/datawork" \
    "$PY" "$TMP/export-data.py" "$ROOT" "$TMP/datawork"
# The same refusal at the shell: exit 4 is the config layer's "missing value", distinct from the generic 1.
broken export_strip export_strip=x
want "a profile with no export field: fetch refuses with 4, names every step it looked on, plans nothing" 4 \
    "exports nothing" 'steps `export` was looked for on' "06-GenerateBatchMetrics" "10-GenotypeBatch" \
    "Refusing" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/stripwork" GSVTK_MODULE_DIR="$TMP/mod-export_strip" \
    bash terra/batch_fetch_compare.sh fetch --dry-run

# 11c. `export` shape refusals: a path copied from `outputs`, an empty list, and a duplicate.
broken export_path export='["this.genotyped_pesr_vcf{new}"]'
want "an export entry copied out of the outputs map refuses: it is a path, not an attribute name" 4 \
    "export[0]" "a path, not an attribute name" "this.genotyped_pesr_vcf{new}" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-export_path" \
    "$PY" kit/module_profile.py --print-exports
broken export_empty export='[]'
want "an empty export list refuses rather than reporting a step that fetches nothing" 4 \
    '`export` is an empty list' "refuses out loud" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-export_empty" \
    "$PY" kit/module_profile.py --print-exports
broken export_dup export='["genotyped_pesr_vcf", "genotyped_pesr_vcf"]'
want "a name listed twice in export refuses, naming the index and the name" 4 \
    "export[1]" "genotyped_pesr_vcf" "already listed" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-export_dup" \
    "$PY" kit/module_profile.py --print-exports

# 11b. `entity_row`: shape refusals, and the name-key rule shared with the offline checker.
#
# An unknown key would raise KeyError in `kit/gsvtk-config::get`, so it must refuse at load with exit 4.
# `terra/steps.py` and `checks/terra_entity_check.py` both derive entity type from the `this.<etype>_id`
# read; the two implementations are compared here.

# Control first: the shipped value loads. Each refusal below changes one value.
want "entity_row: the shipped profile's value loads and prints as the key it names" 0 \
    "10-GenotypeBatch: wdl=GenotypeBatch workflow=GenotypeBatch [sample_set] row=BATCH" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" "$PY" kit/module_profile.py --print
broken er_unknown entity_row='"COHORT_ROW"'
want "an entity_row naming a key the config layer does not know refuses, naming the file that owns keys" 4 \
    "entity_row" "COHORT_ROW" "not a key this repo's config layer knows" "kit/gsvtk-config" \
    "docs/config.md" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-er_unknown" \
    "$PY" terra/batch_configs.py show
broken er_path entity_row='"workspace.cohort_name"'
want "an entity_row copied out of the inputs map refuses: it is a binding value, not a row source" 4 \
    "a binding VALUE, not a row source" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-er_path" \
    "$PY" terra/batch_configs.py show
broken er_blank entity_row='""'
want "a blank entity_row refuses rather than naming a row that is nothing" 4 \
    'entity_row` is blank' -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-er_blank" \
    "$PY" terra/batch_configs.py show
broken er_list entity_row='["BATCH"]'
want "an entity_row that is a list refuses by type, not by silence" 4 \
    'entity_row` is a list' -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-er_list" \
    "$PY" terra/batch_configs.py show
broken er_token entity_row='"BATCH{frz}"'
want "a suffix token in entity_row refuses: expansion has no business in a key name" 4 \
    "carries expansion syntax" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-er_token" \
    "$PY" terra/batch_configs.py show
broken er_operator entity_row='"--entity"'
want "and the one value that names no key is accepted, printed as itself" 0 \
    "10-GenotypeBatch: wdl=GenotypeBatch workflow=GenotypeBatch [sample_set] row=--entity" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-er_operator" \
    "$PY" kit/module_profile.py --print

# Every shipped step states its entity_row: omitting it behaves like `--entity`, which no launch path flags.
want "every step of the shipped profile states its entity_row (absence would read as --entity)" 0 "PASS" -- \
    "$PY" -c "
import json, sys
sys.path.insert(0, 'kit')
import config, module_profile
prof = module_profile.load()
assert not prof.problems, prof.problem
doc = json.load(open(prof.path))
quiet = [s['step'] for s in doc['steps'] if 'entity_row' not in s]
bad = [('%s: %r' % (s['step'], s.get('entity_row'))) for s in doc['steps']
       if s.get('entity_row') != module_profile.ROW_SOURCE_OPERATOR and s.get('entity_row') not in config.KEYS]
assert not quiet, ['steps stating no row source: %s' % ', '.join(quiet)]
assert not bad, ['row sources that are not config keys: %s' % ', '.join(bad)]
assert prof.row_sources.get('09-MergeBatchSites', '') == '', prof.row_sources
print('PASS %d steps state entity_row; %d resolve to a config key, the rest to --entity'
      % (len(doc['steps']), len(prof.row_sources)))"

# The two name-key strips agree on the known vocabulary plus `id` and `_id`, which must yield no type.
want "the launcher's name-key strip and the offline checker's are the same rule, not two near-misses" 0 \
    "PASS" -- "$PY" -c "
import sys
sys.path.insert(0, 'kit'); sys.path.insert(0, 'checks')
import module_profile, terra_entity_check as tec
NAMES = ['sample_set_id', 'sample_set_set_id', 'participant_id', 'sample_id',
         'id', '_id', 'bam_or_cram_file', 'sample_set_set', 'merged_PE_frz']
drift = [(n, tec.name_key_type(n), module_profile.name_key_type(n))
         for n in NAMES if tec.name_key_type(n) != module_profile.name_key_type(n)]
assert not drift, drift
etype, found = module_profile.derive_entity_type(
    ['this.sample_set_set_id', 'this.sample_sets.cutoffs_new', 'workspace.gatk_docker', '"6"', 6])
assert etype == 'sample_set_set' and found == ['sample_set_set'], (etype, found)
assert module_profile.derive_entity_type(['this.a_id', 'this.b_id'])[0] == ''
assert module_profile.derive_entity_type(['workspace.x', 'this.x.bar'])[0] == ''
print('PASS %d names agree, member reads and literals never count' % len(NAMES))"

# 11d. The second module, profiles/single_sample.json, graded offline through `--print` and `terra/steps.py`.
#
# Also covers two rules the loader does not check: every output writes the attribute its own key names, and
# the image rationale matches what is pinned. Both are shown to fire on planted copies.
want "the second module loads and states its step/wdl/workflow/type/row triple" 0 \
    "callers bound: (none)" \
    "wdl=GATKSVPipelineSingleSample workflow=GATKSVPipelineSingleSample [sample] row=--entity" \
    "113 in / 15 out (33 path, 95 literal)" -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
    "$PY" kit/module_profile.py --module single_sample --print

# The `--print` header must name the module actually loaded (the stem of the resolved path), not the
# environment's default. Four legs: presence, absence of the default, the bare default still named, and the
# env door agreeing with the flag door.
want "its header names the module ACTUALLY LOADED, not the environment's default" 0 \
    "module single_sample  profile " -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
    "$PY" kit/module_profile.py --module single_sample --print
want_not "and it does not state the default module above the other module's bindings" 0 \
    "module single_sample  profile " -- "module genotyping" -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
    "$PY" kit/module_profile.py --module single_sample --print
want "the bare --print still names the default, so the header is not an unconditional echo" 0 \
    "module genotyping  profile " -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
    "$PY" kit/module_profile.py --print
want "and the env door answers the same question as the flag door" 0 \
    "module single_sample  profile " -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_MODULE=single_sample \
    "$PY" kit/module_profile.py --print

# The step map, root entity (derived from the name key) and row source all come from the profile.
want "and the step map, the derived root entity and the row source all come from that one file" 0 \
    "PASS" -- "$PY" -c "
import os, sys
os.environ['GSVTK_MODULE'] = 'single_sample'
os.environ.setdefault('GSVTK_WORK', '/tmp')
sys.path.insert(0, 'terra'); sys.path.insert(0, 'kit')
import steps
assert steps.STEPS == {'01': 'GATKSVPipelineSingleSample'}, steps.STEPS
assert steps.RERUN_ROOT_ENTITY == {'01': 'sample'}, steps.RERUN_ROOT_ENTITY
assert steps.RERUN_ROW_SOURCE == {'01': ''}, steps.RERUN_ROW_SOURCE
print('PASS step 01 -> GATKSVPipelineSingleSample on sample, row named only by --entity')"

cat > "$TMP/single-sample-guard.py" <<'SSPG'
"""Grade profiles/single_sample.json's data rules, and prove each one can fail.

argv: ROOT WORK

Every rule below is asserted twice: once on the tracked file (it must hold) and, for the two the loader
deliberately cannot check, once on a planted copy (it must NOT hold). A rule only the clean file satisfies
is a rule nobody has seen fire.
"""
import copy
import json
import os
import sys

ROOT, WORK = sys.argv[1:3]
sys.path.insert(0, os.path.join(ROOT, "kit"))
os.environ["GSVTK_CONFIG"] = os.devnull
os.environ["GSVTK_WORK"] = WORK
os.environ["GSVTK_MODULE"] = "single_sample"
import module_profile                                            # noqa: E402

PATH = os.path.join(ROOT, "profiles", "single_sample.json")
RAW = json.load(open(PATH))
STEP = RAW["steps"][0]
WF = "GATKSVPipelineSingleSample"
# The four keys upstream feeds from the `reference_resources` values bundle. Named here as the CLAIM the
# file makes, so the leg fails if the file quietly drops one or quietly fills one.
# The four keys this list used to hold (clustering_track_names, clustering_track_bed_files,
# external_af_population, genome_tracks) are BOUND in the tracked profile now, so the list is gone rather than left
# as a live-looking record of a closed hole. `v_named_gaps` grades the surface that is still open (every *_docker
# stated, and the rationale matching it), and profiles.sh's ref block controls the closure by DELETING the two
# required arrays in a temporary copy and requiring `check` to go red naming them.


def v_counts(step):
    bad = []
    if len(step["inputs"]) != 113:
        bad.append("inputs is %d, not the 113 this profile states (the 111 keys the launch template at e1909d2f "
                   "binds, plus flank_exclusion_intervals and use_melt, which upstream leaves to a WDL default "
                   "and the golden binds)" % len(step["inputs"]))
    if len(step["outputs"]) != 15:
        bad.append("outputs is %d, not the 15 names in that WDL's output block" % len(step["outputs"]))
    return bad


def v_name_key(step):
    etype, found = module_profile.derive_entity_type(list(step["inputs"].values()))
    return [] if (etype, found) == ("sample", ["sample"]) else \
        ["name key derives %r from %r, want ('sample', ['sample'])" % (etype, found)]


def v_no_frozen(step):
    """No frozen read: `batch_freeze.py` writes one `entity:sample_set_id` row, so this flavor has no
    `{frz}` side to read (see docs/roadmap.md: frozen baseline per flavor). A `{frz}` here would
    bind an attribute nothing can create."""
    bad = [k for m in ("inputs", "outputs") for k, v in step[m].items()
           if isinstance(v, str) and ("{frz}" in v or v.endswith("_frz"))]
    return ["%s reads a frozen attribute this module can never have" % k for k in bad]


def v_writeback_names(step):
    """The attribute an output writes is named after the output that saves it."""
    bad = []
    for k, v in step["outputs"].items():
        leaf = k.split(".")[-1]
        want = "this.%s{new}" % leaf
        if v != want:
            bad.append("%s writes %r, want %r" % (k, v, want))
    return bad


def v_named_gaps(step):
    """Every `*_docker` must be STATED -- pinned or marked -- and the rationale must match what is stated.

    This arm used to grade four inputs upstream bound and this profile did not. That hole is closed: measured, the
    profile binds all 111 keys the template carries, and the two required arrays that were missing are here. So the
    arm MOVED rather than becoming a loop over an empty list -- a guard that can never fire passes forever, which is
    the failure mode this repo names as its own. It grades the money surface still genuinely open, in both
    directions: an image input with no entry is unpinned AND unstated, and a rationale that still describes the
    shipped state after somebody pins a ref is the same class of lie pointing the other way.
    """
    bad = []
    marker = getattr(module_profile, "IMAGE_SOURCE_OPERATOR", "--image")
    imgs = step.get("images") or {}
    why = step.get("_why_images", "")
    for k in sorted(k for k in step["inputs"] if k.endswith("_docker")):
        if k not in imgs:
            bad.append("%s is a *_docker input with no entry in `images`: unpinned AND unstated" % k)
    pinned = [k for k, v in sorted(imgs.items()) if v != marker]
    if pinned and "stated as --image" in why:
        bad.append("`images` states %s as a ref, but _why_images still claims every *_docker is the --image "
                   "marker" % pinned[0])
    if not pinned and marker not in why:
        bad.append("every image is the %s marker and _why_images never says why nothing is pinned" % marker)
    return bad


RULES = [("census", v_counts), ("name key", v_name_key), ("no frozen read", v_no_frozen),
         ("write-back names", v_writeback_names), ("named gaps", v_named_gaps)]

good, bad = [], []
for name, rule in RULES:
    got = rule(STEP)
    (good if not got else bad).append("%s: %s" % (name, got or "ok"))

# The loader's own verdict on the same bytes: an orphan `_why_`, a token on a literal, a bad `entity_row`
# each arrives as a problem here, so `--print` exiting 0 is asserted as data rather than as a green shell
# line somebody could satisfy with a typo'd module name.
prof = module_profile.load("single_sample")
(good if not prof.problems else bad).append("loader: %s" % (prof.problems or "no findings"))
(good if list(prof.configs) == [STEP["step"]] else bad).append(
    "loader binds exactly the one step %r" % STEP["step"])

# CONTROL 1: rename one write-back. `module_profile` cannot see this -- the leaf is not compared to the
# key -- so if this arm does not fail, `v_writeback_names` is the check that cannot fail.
planted = copy.deepcopy(STEP)
planted["outputs"]["%s.qc_file" % WF] = "this.qc_file_typo{new}"
got = v_writeback_names(planted)
(good if len(got) == 1 and "GATKSVPipelineSingleSample.qc_file" in got[0] else bad).append(
    "control: a planted write-back rename is caught (%r)" % got)

# CONTROL 2: fill a named gap without editing the rationale that calls it a gap. The stale rationale is
# PLANTED here rather than borrowed from the shipped file: `profiles/single_sample.json` no longer claims
# all fourteen are the marker (it states `linux_docker`), so a control that relied on the shipped text to
# be stale would have been a control that expired the moment the file it grades was corrected.
planted2 = copy.deepcopy(STEP)
planted2["_why_images"] = ("Every *_docker this step binds is stated as --image, meaning the flag decides. "
                           "(planted by CONTROL 2; the shipped rationale no longer says this)")
planted2["images"]["%s.gatk_docker" % WF] = "us.gcr.io/some-project/gatk-sv/gatk:A_TAG"
got2 = v_named_gaps(planted2)
(good if len(got2) == 1 and "gatk_docker" in got2[0] and "_why_images" in got2[0] else bad).append(
    "control: pinning a ref under a rationale that still claims every image is a marker is caught (%r)" % got2)

print("single_sample profile guard: %d ok, %d failed" % (len(good), len(bad)))
for b in bad:
    print("  FAIL %s" % b)
sys.exit(1 if bad else 0)
SSPG
want "the second module's own data rules hold, and both of the two the loader cannot see are shown firing" 0 \
    "single_sample profile guard: 9 ok, 0 failed" -- \
    "$PY" "$TMP/single-sample-guard.py" "$ROOT" "$TMP/work"

# 11e. The second module against a real gatk-sv checkout at a pinned ref. Every number in this block is
#      about that tree. No checkout holding the ref, or no miniwdl, is a named counted skip.
CK="$("$PY" -c 'import sys; sys.path.insert(0, "kit"); import config; print(config.get("GATK_SV_CHECKOUT"))' \
     2>/dev/null | tail -1)"
PINNED=e1909d2f
# Decide which prerequisite is missing before branching, so the skip line can name it.
if "$PY" -c 'import WDL' >/dev/null 2>&1; then HAS_MINI=1; else HAS_MINI=0; fi
if [ -n "$CK" ] && [ -e "$CK/.git" ] \
   && git -C "$CK" rev-parse --verify --quiet "${PINNED}^{commit}" >/dev/null 2>&1 \
   && [ "$HAS_MINI" = 1 ]; then
    gsvtk_guard_sites "single-sample-check"
    cat > "$TMP/single-sample-check.py" <<'SSCC'
"""`check` vs the second module at a pinned ref: the hole is EXACTLY the two named keys, no more.

argv: ROOT CK REF WORK

Arm A grades the tracked profile and requires a CLEAN verdict at this ref, plus the census that makes "clean" mean
something rather than "the grader looked at nothing": zero MISSING, zero EXTRA, no undeclared output, all 15
outputs declared, the five call bindings named as UNCHECKED, the template read with 111 keys and the duplicate
named. This block used to require exactly two MISSING lines -- the two required clustering arrays the profile did
not bind -- and they are bound now, measured against the 111 keys the template itself carries.
Arm B is the control that keeps arm A from grading a file nobody could break: the SAME profile with those two
arrays DELETED in a temporary copy must go back to exit 1 naming exactly those two. Without arm B, "clean" is a
verdict nobody proved was reachable-by-breaking.
"""
import json
import os
import re
import subprocess
import sys

ROOT, CK, REF, WORK = sys.argv[1:5]
PY = sys.executable


def run(moddir):
    env = dict(os.environ, GSVTK_CONFIG=os.devnull, GSVTK_WORK=WORK,
               GSVTK_MODULE="single_sample", GSVTK_MODULE_DIR=moddir,
               GSVTK_GATK_SV_CHECKOUT=CK)
    r = subprocess.run([PY, "terra/batch_configs.py", "check", "--against", REF],
                       cwd=ROOT, env=env, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


rc, out = run(os.path.join(ROOT, "profiles"))
miss = sorted(re.findall(r"^\s*MISSING (\S+)", out, re.M))
checks = [
    (rc == 0, "exit 0 (clean) at the pinned ref: %s" % rc),
    (miss == [], "no MISSING lines: the two required arrays this profile used to omit are bound (%s)" % miss),
    (len(miss) == len(re.findall(r"^\s*MISSING ", out, re.M)), "the MISSING lines are the whole count"),
    (not re.search(r"^\s*EXTRA ", out, re.M), "no EXTRA key: the map binds nothing the WDL drops"),
    ("out UNDECLARED" not in out and out.count("out DECLARED") == 15,
     "15/15 outputs declared, none undeclared"),
    (out.count("UNCHECKED GATKSVPipelineSingleSample.") == 5,
     "5 call-level bindings named and not graded"),
    ("DUPLICATE-KEY  GATKSVPipelineSingleSample.mei_bed" in out,
     "the duplicate binding is named on the step that carries it"),
    ("TEMPLATE JINJA-NEUTRALISED" in out and "111 key(s) bound by production" in out,
     "the template read is the source of the 111-key census"),
]

# Arm B: the control that keeps arm A from being a tautology about a file nobody could break. Deleting the two
# required arrays from a TEMPORARY copy must send `check` back to exit 1 naming exactly those two -- that is what
# proves the tracked values are doing the work rather than a grader that stopped looking. Written to a temp
# MODULE_DIR, never to the tracked file.
raw = json.load(open(os.path.join(ROOT, "profiles", "single_sample.json")))
for _k in ("GATKSVPipelineSingleSample.clustering_track_names",
           "GATKSVPipelineSingleSample.clustering_track_bed_files"):
    raw["steps"][0]["inputs"].pop(_k, None)
    # Their per-key rationale goes too: leaving `_why_<key>` behind is an orphan, and the loader refuses the whole
    # profile for it (exit 4) before `check` ever grades keys. That refusal is correct -- it just is not the arm
    # being controlled here, and a control that fails for an unrelated reason proves nothing.
    raw["steps"][0].pop("_why_" + _k, None)
ctrl = os.path.join(WORK, "mod-ss-check")
os.makedirs(ctrl, exist_ok=True)
open(os.path.join(ctrl, "single_sample.json"), "w").write(json.dumps(raw, indent=2))
rc2, out2 = run(ctrl)
checks += [
    (rc2 == 1, "control: deleting those two keys sends check back to exit 1 (got %s)" % rc2),
    (out2.count("MISSING GATKSVPipelineSingleSample.clustering_track") == 2,
     "control: and it names exactly those two as MISSING, not some count nobody parsed"),
]

print("single_sample check-vs-ref guard: %d ok, %d failed" % (sum(1 for c, _ in checks if c),
                                                              sum(1 for c, _ in checks if not c)))
for ok_, what in checks:
    if not ok_:
        print("  FAIL %s" % what)
        print("\n".join(("        " + l) for l in out.splitlines()[:20]))
sys.exit(1 if [c for c, _ in checks if not c] else 0)
SSCC
    want "check at $PINNED grades the second module CLEAN, and deleting the two required arrays reopens it" 0 \
        "single_sample check-vs-ref guard: 10 ok, 0 failed" -- \
        "$PY" "$TMP/single-sample-check.py" "$ROOT" "$CK" "$PINNED" "$TMP/work"
    gsvtk_guard_sites_end "single-sample-check"
else
    # Name the one prerequisite that is missing.
    if [ -z "$CK" ] || [ ! -e "$CK/.git" ]; then
        missing="no gatk-sv checkout (GSVTK_GATK_SV_CHECKOUT is \"${CK:-unset}\")"
    elif ! git -C "$CK" rev-parse --verify --quiet "${PINNED}^{commit}" >/dev/null 2>&1; then
        missing="$CK does not hold $PINNED"
    else
        missing="$PY cannot import miniwdl (requirements-dev.txt carries it); the checkout and $PINNED are readable"
    fi
    printf '  SKIP  profiles: %s: the second module was not graded against the WDL and template it transcribes, so the two MISSING lines its profile names are UNVERIFIED here (a withheld assertion is not a passed one)\n' \
        "$missing"
    skipped=$((skipped + 1))
    gsvtk_note_skip "single-sample-check"
fi

# 12. `fetch_baseline.py`: a workspace missing a requested step must report a counted, named hole and exit 1.
#
# `terra` is stubbed in process, so this needs no firecloud and has no SKIP branch. An unexpected request
# raises, and the API root is a closed port.
cat > "$TMP/fetch-baseline-guard.py" <<'PY'
"""`fetch_baseline.py`'s missing-step path, run against a fake workspace.

`terra` is replaced in `sys.modules` before the tool is imported: the four entry points it uses are
stubbed, and anything else it asks for raises with the URL in the message. The tool's own `main()` runs,
so the argparse surface, the ordering rule, the manifest bytes and the exit code are all the real thing.
"""
import io
import json
import os
import sys
import types
from contextlib import redirect_stdout

ROOT = sys.argv[1]
WORK = sys.argv[2]
sys.path.insert(0, os.path.join(ROOT, "kit"))
sys.path.insert(0, os.path.join(ROOT, "terra"))

STATE = {"names": []}


class TerraError(Exception):
    pass


class _Resp:
    def __init__(self, payload):
        self._p, self.status_code = payload, 200

    def json(self):
        return self._p

    @property
    def text(self):
        return ""


class _Session:
    """Two reads: the method-config listing, and one config by name. Anything else is a failure."""

    def get(self, url, timeout=None):
        if "methodconfigs?allRepos" in url:
            return _Resp([{"name": n, "namespace": "ns"} for n in STATE["names"]])
        if "/methodconfigs/" in url:
            name = url.rsplit("/", 1)[-1]
            return _Resp({"name": name, "rootEntityType": "sample_set",
                          "methodRepoMethod": {"methodUri": "dockstore://fake/%s/1" % name},
                          "inputs": {"GenotypeBatch.batch": "this.sample_set_id"},
                          "outputs": {"GenotypeBatch.genotyped_pesr_vcf":
                                      "this.genotyped_pesr_vcf_new"}})
        raise AssertionError("fetch_baseline asked for something this stub does not model: %s" % url)


def _workspace(ns, ws):
    return {"workspace": {"workspaceId": "fake-id", "bucketName": "fake-bucket",
                          "attributes": {"bin_exclude": "gs://fake/bin.bed"}}}


def _entity_sample(ns, ws, etype, page_size=200, page=1):
    if etype == "sample":
        return {"results": []}
    return {"results": [{"name": "all_samples",
                         "attributes": {"merged_PE": "gs://fake/pe.txt"}}]}


def _dump(obj, path):
    if not os.path.isdir(os.path.dirname(path)):
        os.makedirs(os.path.dirname(path))
    with open(path, "w") as fh:
        json.dump(obj, fh, default=str)


fake = types.ModuleType("terra")
fake.TerraError = TerraError
fake.TERRA_API = "https://fake.invalid/api/"
fake.BASELINE_NS = "baseline-ns"
fake.BASELINE_WS = "baseline-ws"
fake.session = lambda: _Session()
fake.workspace = _workspace
fake.entity_sample = _entity_sample
fake.workspace_configs = lambda ns, ws: [{"name": n, "namespace": "ns"} for n in STATE["names"]]
fake.dump = _dump
sys.modules["terra"] = fake

import fetch_baseline as fb                                    # noqa: E402  (fake terra is installed above)
import steps                                                  # noqa: E402  (the lookup fetch declines)

bad, good = [], []


def check(cond, desc, detail=""):
    (good if cond else bad).append(desc)
    print(("  ok    " if cond else "  FAIL  ") + desc + (("  <%s>" % detail) if detail else ""))


def run(steps_wanted, names):
    """(exit code, stdout, manifest) for one fake workspace."""
    STATE["names"] = list(names)
    argv = ["fetch_baseline.py", "--ns", "ns", "--ws", "ws", "--entity", "all_samples",
            "--steps"] + list(steps_wanted)
    old, buf = sys.argv, io.StringIO()
    sys.argv = argv
    try:
        with redirect_stdout(buf):
            try:
                fb.main()
                rc = 0
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 1
    finally:
        sys.argv = old
    path = os.path.join(WORK, "manifests", "baseline_run.json")
    return rc, buf.getvalue(), (json.load(open(path)) if os.path.isfile(path) else {})


order = fb.pick_config({"10-GenotypeBatch": {}, "10-GenotypeBatch_Ab12Cd": {},
                        "09-MergeBatchSites": {}}, "10")
check(order == ["10-GenotypeBatch", "10-GenotypeBatch_Ab12Cd"],
      "the LIVE config is listed before the per-submission snapshot that carries its number "
      "(docs/terra-head-to-head.md §8: a finished submission keeps `<name>_<random>` like "
      "`single-sample-trio-a0e10b99_B0EJFlC5SLk`, so the live name is a prefix of its own copy) -- "
      "MEASURED, AND ON ITS OWN THIS CHECK CANNOT FAIL: a prefix always sorts first, so "
      "`sorted(cand)` returns the same answer here. The next line is the one with teeth",
      ", ".join(order))
# The discriminating case, and the reason it is here: two configs can carry the same step number
# without either name being a prefix of the other (a hand-named variant, a copy pulled in from another
# branch). That is where `pick_config`'s length-first rule and a plain `sorted(cand)` disagree, so if
# someone collapses the rule, this is the assertion that moves.
disc = fb.pick_config({"10-GenotypeBatch": {}, "10-BatchCopy_from_another_branch": {}}, "10")
check(disc == ["10-GenotypeBatch", "10-BatchCopy_from_another_branch"],
      "the ordering rule really is `shortest name first`, not `alphabetical`: two same-numbered "
      "configs, neither a prefix of the other, and alphabetical puts the long-named copy first -- which "
      "is the `sorted(matches)[0]` class of bug this function exists to avoid",
      ", ".join(disc))

bare = ["GenotypeBatch"]
check(fb.pick_config({n: {} for n in bare}, "10") == []
      and steps.match_configs("10", bare) == bare,
      "the two lookups still answer an UN-NUMBERED chain name differently, which is the whole reason "
      "fetch_baseline declines steps.match_configs: it enumerates a workspace, it does not request a "
      "step",
      "pick_config=%r steps.match_configs=%r"
      % (fb.pick_config({n: {} for n in bare}, "10"), steps.match_configs("10", bare)))

rc, out, man = run(["05", "06", "07", "08", "09", "10"],
                   ["06-GenerateBatchMetrics", "10-GenotypeBatch"])
check(rc == 1,
      "a workspace holding two of the six requested steps exits NONZERO, so a caller cannot mistake a "
      "partial baseline for the baseline", "exit %s" % rc)
check(all(("!! no config for step %s" % s) in out for s in ("05", "07", "08", "09")),
      "and each skipped step is named on a line of its own, not folded into a total",
      "%d '!!' line(s)" % sum(1 for l in out.splitlines() if "!!" in l))
check("!! 4 of 6 step(s) could not be resolved (no config, or no entity row): 05, 07, 08, 09" in out,
      "the count is a fraction of what was asked for, with every name after it")
check(man.get("steps_missing") == ["05", "07", "08", "09"] and len(man.get("steps") or {}) == 2,
      "the hole is recorded INSIDE baseline_run.json, the artifact a later loop reads, beside the two "
      "steps it did freeze", "steps_missing=%s, steps=%d"
      % (man.get("steps_missing"), len(man.get("steps") or {})))
check("PARTIAL" in out,
      "and the log tells the human the manifest is PARTIAL rather than leaving silence to interpret")
# What was TRIED, not just that nothing was found: `pick_config` deliberately does not use
# `steps.match_configs`, so the sentence cannot borrow that tool's three-pattern message -- it has to
# state its own one pattern and the size of the haystack it searched. Without this, "no config for step
# 05" is one character away from "this workspace has no configs at all".
check("starts with '05-'" in out and "starts with '07-'" in out
      and "2 method config" in out,
      "a skipped step names the pattern it tried and how many config names it looked at, so an empty "
      "answer cannot be read as \"the workspace is empty\"",
      "lines carrying '!!': %s" % sum(1 for l in out.splitlines() if "!!" in l))

rc2, out2, man2 = run(["06", "07", "08", "09", "10"],
                      ["06-GenerateBatchMetrics", "07-FilterBatchSites", "08-FilterBatchSamples",
                       "09-MergeBatchSites", "10-GenotypeBatch"])
check(rc2 == 0 and "steps_missing" not in man2 and "!!" not in out2,
      "CONTROL: the same harness on a workspace that HAS every requested step exits 0, records no "
      "`steps_missing` and prints no `!!` line -- so the failure above is about the data, not about the "
      "stub", "exit %s, steps=%d, steps_missing present: %s"
      % (rc2, len(man2.get("steps") or {}), "steps_missing" in man2))

print("fetch_baseline counted-gap guard: %d ok, %d failed"
      % (len(good), len(bad)))
sys.exit(1 if bad else 0)
PY
want "fetch_baseline records a counted, named, PARTIAL hole for every step a workspace lacks, and exits 1" 0 \
    "fetch_baseline counted-gap guard: 10 ok, 0 failed" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/baseline-work" \
    GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/" \
    "$PY" "$TMP/fetch-baseline-guard.py" "$ROOT" "$TMP/baseline-work"

# 11c. Image pins: a step can name its images, the loader grades their shape, and `--image` takes precedence.
# The shipped profile pins nothing (every `*_docker` is the `--image` marker).
pin_copy() {                              # pin_copy CASE -> $TMP/pin-CASE/single_sample.json
    mkdir -p "$TMP/pin-$1"
    "$PY" - "$ROOT" "$TMP/pin-$1" "$1" <<'PY'
import json, os, sys
root, outdir, case = sys.argv[1], sys.argv[2], sys.argv[3]
d = json.load(open(os.path.join(root, "profiles", "single_sample.json")))
s = d["steps"][0]
dk = [k for k in s["inputs"] if k.endswith("_docker")]
s["images"] = {
    "tagged":    {k: "us.gcr.io/pinned-in-profile/gatk-sv/x:%s" % k.split(".")[-1] for k in dk},
    "untagged":  {dk[0]: "us.gcr.io/pinned-in-profile/gatk-sv/no-tag-here"},
    "workspace": {dk[0]: "workspace.some_docker"},
    "unbound":   {"GATKSVPipelineSingleSample.not_an_image_input": "us.gcr.io/p/g/x:1"},
    "nonstring": {dk[0]: ["us.gcr.io/p/g/x:1"]},
}[case]
open(os.path.join(outdir, "single_sample.json"), "w").write(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
PY
}
RERUN_FIXTURE="$ROOT/scripts/selftest.d/fixtures/rerun.profile.env"
for c in untagged workspace unbound nonstring; do
    pin_copy "$c"
done
pin_copy tagged
GKEY=$("$PY" -c "import json;d=json.load(open('$ROOT/profiles/single_sample.json'));\
print([k for k in d['steps'][0]['images'] if k.endswith('gatk_docker')][0])")

# The shipped profile must still refuse: nothing is pinned by default.
want "the shipped single_sample profile still refuses: every image is the --image marker, nothing pinned by default" 1 \
    "not fully pinned" "UNPINNED" "profile" -- \
    env GSVTK_MODULE=single_sample GSVTK_CONFIG="$RERUN_FIXTURE" GSVTK_WORK="$TMP/pinwork" GSVTK_READ_ONLY=1 \
    "$ROOT/gsvtk" --read-only terra rerun-show --step 01 --entity NA12878
want "POSITIVE CONTROL: the same step with tagged refs in its profile builds -- exit 0 is the assertion, since the\
 refusal above is exit 1" 0 \
    "pinned-in-profile" '"rootEntityType": "sample"' '"useCallCache": false' -- \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/pin-tagged" GSVTK_CONFIG="$RERUN_FIXTURE" \
    GSVTK_WORK="$TMP/pinwork2" GSVTK_READ_ONLY=1 \
    "$ROOT/gsvtk" --read-only terra rerun-show --step 01 --entity NA12878
# Count the workspace-attribute image reads: unpinned builds some, fully pinned builds none. The count is 11
# under this fixture, not 14: the fixture's GSVTK_IMAGE_REPO / GSVTK_GATK_IMAGE_REPO answer two keys, and one
# entry states its ref with `versioned_by: path`.
want "unpinned builds workspace-attribute image reads (11 under this fixture); a fully pinned build builds zero" 0 \
    "reads-unpinned=11 reads-pinned=0" -- sh -c \
    "u=\$(env GSVTK_MODULE=single_sample GSVTK_BRANCH=main GSVTK_CONFIG='$RERUN_FIXTURE' GSVTK_WORK='$TMP/pinwork' GSVTK_READ_ONLY=1 '$ROOT/gsvtk' --read-only terra rerun-show --step 01 --entity NA12878 --allow-unpinned-docker 2>/dev/null | grep -c ': \"workspace\\.'); \
p=\$(env GSVTK_MODULE=single_sample GSVTK_BRANCH=main GSVTK_MODULE_DIR='$TMP/pin-tagged' GSVTK_CONFIG='$RERUN_FIXTURE' GSVTK_WORK='$TMP/pinwork' GSVTK_READ_ONLY=1 '$ROOT/gsvtk' --read-only terra rerun-show --step 01 --entity NA12878 2>/dev/null | grep -c ': \"workspace\\.'); \
echo \"reads-unpinned=\$u reads-pinned=\$p\""

# Precedence: `--image` overrides a profile pin on its key; the other keys keep their pins.
want "CONTROL: --image beats a profile pin on that key while the other 13 keys keep their profile pin" 0 \
    "CLI_TAG" "pinned-in-profile" -- \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/pin-tagged" GSVTK_CONFIG="$RERUN_FIXTURE" \
    GSVTK_WORK="$TMP/pinwork3" GSVTK_READ_ONLY=1 \
    "$ROOT/gsvtk" --read-only terra rerun-show --step 01 --entity NA12878 \
    --image "$GKEY=us.gcr.io/probe-project/gatk-sv/gatk:CLI_TAG"
for arm in untagged workspace unbound nonstring; do
    case "$arm" in
        untagged)  needle="UNTAGGED" ;;
        workspace) needle="a binding VALUE, not a pin" ;;
        unbound)   needle="is not a *_docker" ;;
        nonstring) needle="is a list, not a" ;;
    esac
    want "the loader refuses a profile image pin that is $arm, and names the key" 4 \
        "is unusable" "$needle" "GATKSVPipelineSingleSample" -- \
        env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/pin-$arm" \
        GSVTK_GATK_SV_CHECKOUT="" "$PY" "$ROOT/kit/module_profile.py" --print
    unset needle
done

# The Dockstore entry is `SingleSamplePipeline`, while the config name and binding prefixes use the WDL's
# name, `GATKSVPipelineSingleSample`. Assert both halves.
want "step 01 resolves its METHOD from the Dockstore entry while its config name stays on the WDL name" 0 \
    "Dockstore github.com/broadinstitute/gatk-sv/SingleSamplePipeline" \
    "config 01-GATKSVPipelineSingleSample-rerun" "root entity sample" -- \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/pin-tagged" GSVTK_CONFIG="$RERUN_FIXTURE" \
    GSVTK_WORK="$TMP/pinwork4" GSVTK_READ_ONLY=1 \
    "$ROOT/gsvtk" --read-only terra rerun-show --step 01 --entity NA12878

# Rawls types the input map as Map[String, String] and rejects the body if any value is a parsed literal,
# so check the resolved map, not just the printed body.
want "every resolved input value is a string, because Rawls types the map Map[String, String]" 0 \
    "all inputs are strings" -- \
    env GSVTK_MODULE=single_sample "$PY" -c \
    "import sys; sys.path[:0]=['$ROOT/kit']; import module_profile as mp
m = mp.load().configs
bad = {k: type(v).__name__ for c in m.values() for k, v in c['inputs'].items() if not isinstance(v, str)}
print('all inputs are strings' if not bad else f'NON-STRING VALUES: {bad}')"

# A rerun must state its own resources: no single-sample binding may read a workspace attribute, since the
# target workspace may not be populated and `validate` never resolves them. `*_docker` keys are excluded
# because the image rule owns them.
want "no single-sample binding is left reading a workspace attribute, because a rerun must state its own resources" 0 \
    "no workspace attribute reads" -- \
    env GSVTK_MODULE=single_sample "$PY" -c \
    "import sys; sys.path[:0]=['$ROOT/kit']; import module_profile as mp
ws = {k: v for c in mp.load().configs.values() for k, v in c['inputs'].items()
        if isinstance(v, str) and v.startswith('workspace.') and not k.endswith('_docker')}
print('no workspace attribute reads' if not ws else f'{len(ws)} BINDINGS READ WORKSPACE ATTRS: {sorted(ws)[:4]}')"

# The loader refuses a `*_docker` literal: image keys belong to the image rule. Planted to show it fires.
mkdir -p "$TMP/pin-docker-literal"
"$PY" - "$ROOT/profiles/single_sample.json" "$TMP/pin-docker-literal/single_sample.json" <<'PYEOF'
import json, sys, io
d = json.load(io.open(sys.argv[1], encoding='utf-8'))
d['steps'][0]['literals']['sv_pipeline_docker'] = '"us.gcr.io/probe-project/gatk-sv/sv-pipeline:TAG"'
io.open(sys.argv[2], 'w', encoding='utf-8').write(json.dumps(d))
PYEOF
want "the loader refuses a *_docker literal, because image keys belong to the image rule" 4 \
    "is unusable" "belongs to the image rule" "sv_pipeline_docker" -- \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/pin-docker-literal" \
    GSVTK_GATK_SV_CHECKOUT="" "$PY" "$ROOT/kit/module_profile.py" --print

# checks/version_resolves.py: unpublished entries and versions whose branch is gone. Fixtures are synthetic
# because a real TRS listing contains other people's branch names.
mkdir -p "$TMP/trs"
"$PY" - "$TMP/trs/published.json" "$TMP/trs/empty.json" <<'PYEOF'
import json, sys
tools = [{"id": "#workflow/github.com/broadinstitute/gatk-sv/SingleSamplePipeline",
          "versions": [{"name": n} for n in ("main", "v1.1.1", "dev_probe")]}]
json.dump(tools, open(sys.argv[1], "w"))
json.dump([], open(sys.argv[2], "w"))
PYEOF
want "a published entry+version resolves, and an unpublished one is refused by name" 0 \
    "RESOLVES github.com/broadinstitute/gatk-sv/SingleSamplePipeline @ main" \
    "3 versions listed" -- \
    env GSVTK_MODULE=single_sample "$PY" "$ROOT/checks/version_resolves.py" \
    --entry github.com/broadinstitute/gatk-sv/SingleSamplePipeline --version main \
    --from-file "$TMP/trs/published.json"
want "and the version whose branch was deleted is refused, not quietly defaulted" 1 \
    "NO SUCH VERSION" "dev_fix_single_sample_blocking" -- \
    env GSVTK_MODULE=single_sample "$PY" "$ROOT/checks/version_resolves.py" \
    --entry github.com/broadinstitute/gatk-sv/SingleSamplePipeline \
    --version dev_fix_single_sample_blocking --from-file "$TMP/trs/published.json"
want "and an empty listing is refused rather than read as evidence of absence" 4 \
    "LISTING UNUSABLE" "not evidence" -- \
    env GSVTK_MODULE=single_sample "$PY" "$ROOT/checks/version_resolves.py" \
    --entry github.com/broadinstitute/gatk-sv/SingleSamplePipeline --version main \
    --from-file "$TMP/trs/empty.json"

# ---------------- depth 2: does the version a rerun names still point at the branch tip?
#
# Dockstore only syncs a branch while `.github/.dockstore.yml` lists it, so a version name can keep resolving
# while serving a stale commit. SERVED and TIP below are a real stale pair. Fixtures are under
# `scripts/selftest.d/fixtures/trs/` (see its README); the tip comes from a `git ls-remote`-shaped file, so
# nothing here touches a network.
TRSFIX="$ROOT/scripts/selftest.d/fixtures/trs"
TRS="$TRSFIX/dockstore-trs-genotypebatch-2026-10-14.json"
WV="$TRSFIX/workflowversions-genotypebatch-shape-holder.json"
GB=github.com/broadinstitute/gatk-sv/GenotypeBatch
SERVED=62d7f80d0b32eca55ca5eaeae770cf56eccbfeca
TIP=af68ae488725dddf7d43b66547c9bfa0324db1e1
MAIN=743dd9d41edeee98bdd97cde0638a89a4432cfe7

want_not "a version whose commitID IS the tip prints CURRENT and never STALE" 0 \
    "CURRENT" "$SERVED" -- "STALE" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --from-file "$WV" --entry "$GB" \
    --version dev_genotype_scale --tip-from-file "$TRSFIX/tip-ls-remote-current.txt"
want "a version behind the tip is STALE, naming BOTH shas as literals" 1 \
    "STALE" "$SERVED" "$TIP" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --from-file "$WV" --entry "$GB" \
    --version dev_genotype_scale --tip-from-file "$TRSFIX/tip-ls-remote-stale.txt"
# The stale verdict must print the full remedy.
want "the stale verdict prints the remedy: a <branch>_terra at the tip plus the filter commit" 1 \
    "REMEDY" "dev_genotype_scale_terra" "filters.branches" ".github/.dockstore.yml" \
    "readonly_check.yaml" "under a minute" "GSV_WDL_VERSION=dev_genotype_scale_terra" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --from-file "$WV" --entry "$GB" \
    --version dev_genotype_scale --tip-from-file "$TRSFIX/tip-ls-remote-stale.txt"
want_not "a version Dockstore does not list is ABSENT (and never CURRENT), with the same remedy" 1 \
    "ABSENT" "dev_fix_single_sample_blocking" "REMEDY" -- "CURRENT" "STALE" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --from-file "$WV" --entry "$GB" \
    --version dev_fix_single_sample_blocking --tip-from-file "$TRSFIX/tip-ls-remote-stale.txt"
# The tip fixture holds two `refs/heads/` lines, so the tip must be read per branch, not by position.
want_not "the tip file is read per branch: the same file says CURRENT for main and STALE for the other ref" 0 \
    "CURRENT" "$MAIN" -- "STALE" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --from-file "$WV" --entry "$GB" \
    --version main --branch main --tip-from-file "$TRSFIX/tip-ls-remote-stale.txt"
# The unauthenticated TRS listing has no commit field, so depth 2 must refuse rather than report CURRENT.
want_not "the committed REAL capture has no commitID, so depth 2 refuses rather than reporting CURRENT" 4 \
    "REFUSED" "NO COMMIT TO COMPARE" "0 of 2" -- "CURRENT" "STALE" "ABSENT" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --from-file "$TRS" --entry "$GB" \
    --version dev_genotype_scale --tip "$TIP"
want "and that same real capture still answers depth 1: the name resolves" 0 \
    "RESOLVES github.com/broadinstitute/gatk-sv/GenotypeBatch @ dev_genotype_scale" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --from-file "$TRS" --entry "$GB" \
    --version dev_genotype_scale
want_not "an empty capture is refused in depth 2 too, and never reported as ABSENT" 4 \
    "REFUSED" "LISTING UNUSABLE" "not evidence" -- "ABSENT" "CURRENT" "STALE" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --from-file "$TMP/trs/empty.json" --entry "$GB" \
    --version dev_genotype_scale --tip "$TIP"
want_not "a tip source that omits the asked-for branch refuses instead of reading absence" 4 \
    "REFUSED" "refs/heads/dev_genotype_scale_terra" -- "ABSENT" "CURRENT" "STALE" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --from-file "$WV" --entry "$GB" \
    --version dev_genotype_scale --branch dev_genotype_scale_terra \
    --tip-from-file "$TRSFIX/tip-ls-remote-stale.txt"
want "asking for a branch with no tip at all is a refusal that prints the tip command" 4 \
    "REFUSED" "git -C <gatk-sv checkout> ls-remote origin refs/heads/dev_genotype_scale" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --from-file "$WV" --entry "$GB" \
    --version dev_genotype_scale --branch dev_genotype_scale
# --all-steps reports a verdict per entry plus a counting line.
want "--all-steps answers for every entry the launcher names, and counts the verdicts" 1 \
    "publish check:" "1 STALE," "named by the launcher" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --from-file "$WV" --all-steps \
    --version dev_genotype_scale --tip-from-file "$TRSFIX/tip-ls-remote-stale.txt"

# The CLI dispatches under GSVTK_READ_ONLY=1, since it only grades a file. No leg runs `--ls-remote` or a
# live `--fetch`; the fetch legs grade its offline refusal.
want_not "gsvtk publish dispatches under --read-only and says STALE (no refusal, both shas)" 1 \
    "STALE" "$SERVED" "$TIP" "dev_genotype_scale_terra" -- "gsvtk: refusing" "usage:" -- \
    env GSVTK_READ_ONLY=1 "$ROOT/gsvtk" --read-only publish --from-file "$WV" --entry "$GB" \
    --version dev_genotype_scale --tip-from-file "$TRSFIX/tip-ls-remote-stale.txt"
want "--ls-remote with a --tip already typed is refused: one comparison per verdict" 2 \
    "would replace the tip you already passed" -- \
    env GSVTK_READ_ONLY=1 "$ROOT/gsvtk" --read-only publish --ls-remote --tip "$TIP" \
    --entry "$GB" --version dev_genotype_scale
want "--ls-remote with no branch named is refused too, rather than reading some ref at random" 2 \
    "needs to know which branch to ask for" -- \
    env GSVTK_READ_ONLY=1 "$ROOT/gsvtk" --read-only publish --ls-remote --from-file "$WV" --entry "$GB"
want "the tool's own suite runs on the gate, with its fixture count" 0 "17 ok, 0 failed" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --selftest

# ---------------- the anonymous published-entry route
#
# `/published?include=versions` answers without a token and carries a `commitID` on every row. The fixture
# is an excerpt of the real body. In it, `workflow_path` is the descriptor file and `path` is the repo, so
# the entry must be read from `full_workflow_path`; reading it from either of the others would answer ABSENT.
PUB="$TRSFIX/dockstore-published-genotypebatch-excerpt.json"

want_not "the real published-entry capture parses as its own shape, with a commit on every row" 1 \
     "capture: Dockstore published entry (workflowVersions), 1 entry, 4 version rows, 4 carrying a commitID" \
     -- "/Dockstore.cwl" "unrecognised" -- \
     "$PY" "$ROOT/checks/version_resolves.py" --from-file "$PUB" --entry "$GB" \
     --version dev_genotype_scale --tip "$TIP"
want "and it grades the field case STALE from Dockstore's own bytes, naming both shas" 1 \
     "STALE" "$SERVED" "$TIP" "dev_genotype_scale_terra" -- \
     "$PY" "$ROOT/checks/version_resolves.py" --from-file "$PUB" --entry "$GB" \
     --version dev_genotype_scale --tip "$TIP"
want_not "and CURRENT when the tip IS the commit Dockstore serves (same file, different tip)" 0 \
     "CURRENT github.com/broadinstitute/gatk-sv/GenotypeBatch @ dev_genotype_scale" -- "STALE" -- \
     "$PY" "$ROOT/checks/version_resolves.py" --from-file "$PUB" --entry "$GB" \
     --version dev_genotype_scale --tip "$SERVED"
want_not "the entry comes from full_workflow_path, never from the descriptor path or the repo path" 1 \
     "STALE" -- "/Dockstore.cwl" "github.com/broadinstitute/gatk-sv @" -- \
     "$PY" "$ROOT/checks/version_resolves.py" --from-file "$PUB" --entry "$GB" \
     --version dev_genotype_scale --tip "$TIP"
# The fetcher reads its entry list from the grader rather than keeping its own.
want "--print-entries prints the entry a fetch would read, from the launcher's own derivation" 0 \
     "github.com/broadinstitute/gatk-sv/GenotypeBatch" -- \
     "$PY" "$ROOT/checks/version_resolves.py" --print-entries --step 10
# Offline fetch: a blackholed API root must yield REFUSED at exit 3, never a partial capture that would
# report the unseen entry as ABSENT.
want_not "a fetch that cannot reach Dockstore is REFUSED at exit 3, and never becomes an ABSENT verdict" 3 \
     "REFUSED" "never read" "cannot be called ABSENT" -- "CURRENT" "STALE" "ABSENT github" -- \
     env GSVTK_DOCKSTORE_API_ROOT=http://127.0.0.1:9/ "$ROOT/gsvtk" publish --fetch --step 10 \
         --branch dev_genotype_scale --tip "$TIP"
want_not "publish-check <branch> is the live one-liner, and --read-only does NOT refuse it (a GET is not a write)" 3 \
     "REFUSED" -- "gsvtk: refusing" "CURRENT" "STALE" -- \
     env GSVTK_READ_ONLY=1 GSVTK_DOCKSTORE_API_ROOT=http://127.0.0.1:9/ "$ROOT/gsvtk" publish-check dev_genotype_scale
want "--fetch together with --from-file is a usage error, not a precedence" 2 \
     "would replace the capture passed with --from-file" -- \
     env GSVTK_READ_ONLY=1 "$ROOT/gsvtk" publish --fetch --from-file "$PUB" --entry "$GB" --version dev_genotype_scale

# ---------------- 13. `checks/body_golden_diff.py`: the non-chain body against a capture that already ran
#
# The oracle was recorded with `useCallCache: true` and dev image tags, so the money fields are asserted on
# OUR body rather than normalized away. Fixtures are synthetic; no checkout, ref, network or jar needed.
GD="$ROOT/checks/body_golden_diff.py"
GDFIX="$TMP/golden-diff"
mkdir -p "$GDFIX"
# BODY has 4 inputs / 2 outputs; CAPTURE shares all 6 keys and has provenance but no `useCallCache`, like a
# real method-config payload.
"$PY" - "$GDFIX" <<'GDFIXGEN'
"""Synthetic body/capture pair for the body-vs-capture legs. Invented values only."""
import json
import os
import sys

d = sys.argv[1]
WF = "TinyPipelineSingle"
body = {
    "namespace": "example-namespace", "name": "01-%s-rerun" % WF, "rootEntityType": "sample",
    "methodRepoMethod": {"sourceRepo": "dockstore",
                         "methodPath": "example.org/gatk-sv/%s" % WF,
                         "methodUri": "dockstore://example.org%2Fgatk-sv%2F" + WF + "/v1",
                         "methodVersion": "v1"},
    "methodConfigVersion": 1, "deleted": False,
    "inputs": {WF + ".sample_id": "this.sample_id",
               WF + ".batch": "this.sample_id",
               WF + ".cutoffs": '"example-bucket/ref/all_samples.cutoffs"',
               WF + ".gatk_docker": '"example.org/gatk-sv/gatk:1.2.3"'},
    "outputs": {WF + ".final_vcf": "this.final_vcf_new", WF + ".qc_file": "this.qc_file_new"},
    "prerequisites": {}, "deleteIntermediateOutputFiles": False, "useCallCache": False,
}
capture = {k: v for k, v in body.items()
           if k not in ("useCallCache", "deleteIntermediateOutputFiles", "maxMessageSize")}
capture["name"] = "01-%s-hand" % WF
capture["_gsvtk_capture"] = {"captured_utc": "2026-01-01T00:00:00Z",
                             "command": "terra.config_payload(<ns>, <ws>, <ns>, <config>)",
                             "config_name": "01-%s-hand" % WF, "config_namespace": "example-namespace",
                             "workspace": "example-workspace", "note": "synthetic fixture"}


def write(name, doc):
    with open(os.path.join(d, name), "w") as fh:
        json.dump(doc, fh, indent=1)


write("body.json", body)
write("capture.json", capture)
moved = json.loads(json.dumps(capture))
moved["outputs"][WF + ".qc_file"] = "this.qc_file"          # the '{new}' suffix, moved by hand
write("capture-moved.json", moved)
empty_out = json.loads(json.dumps(capture))
empty_out["outputs"] = {}                                    # the empty-input class, on the far side
write("capture-empty-outputs.json", empty_out)
no_prove = {k: v for k, v in capture.items() if k != "_gsvtk_capture"}
write("capture-no-provenance.json", no_prove)
wrong_root = json.loads(json.dumps(capture))
wrong_root["rootEntityType"] = "sample_set"
write("capture-wrong-root.json", wrong_root)
unpinned = json.loads(json.dumps(body))
unpinned["inputs"][WF + ".gatk_docker"] = "workspace.gatk_docker"     # the money shape, unpinned
write("body-unpinned-image.json", unpinned)
print("synthetic body/capture fixtures written to %s" % d)
GDFIXGEN

# The tool's own suite. The count is not pinned; "0 failed" is, and "0 ok," is forbidden.
want "the body-vs-capture checker's own suite runs here, and reports no failure" 0 \
    "body_golden_diff selftest: " "0 failed" -- "$PY" "$GD" --selftest
want_not "and a suite that reported no passing assertion is refused, not passed" 0 \
    "body_golden_diff selftest: " -- "0 ok," -- "$PY" "$GD" --selftest

# 13.1 Green arm: full coverage on both maps, both money rules satisfied.
want "structural agreement over binding keys and symbolic bindings: exit 0, coverage 1.000 both maps, 0 findings" 0 \
    "VERDICT BODY-GOLDEN-MATCH" "inputs body=4 capture=4 shared=4 coverage=1.000" \
    "outputs body=2 capture=2 shared=2 coverage=1.000" "literal-differs=0 | findings=0" \
    "MONEY useCallCache: body=False -> OK" "1 quoted tagged literal(s), 0 pinned versioned-by-path quoted-tagged=1 -> OK" -- \
    "$PY" "$GD" --body "$GDFIX/body.json" --capture "$GDFIX/capture.json"

# 13.2 One output write-back moved: the key and both values are reported, and MATCH must not appear.
want "PLANT one output write-back: exit 1, the key named with both values, one finding" 1 \
    "SYMBOLIC-DIFFERS outputs: 1 key(s)" "body this.qc_file_new != capture this.qc_file" \
    "findings=1" -- "$PY" "$GD" --body "$GDFIX/body.json" --capture "$GDFIX/capture-moved.json"
want_not "and the agreement verdict word cannot appear in a run that found something" 1 \
    "VERDICT BODY-GOLDEN-DIFF" -- "BODY-GOLDEN-MATCH" -- \
    "$PY" "$GD" --body "$GDFIX/body.json" --capture "$GDFIX/capture-moved.json"

# 13.3 An image ref left as a `workspace.` read is a money finding.
want "an image ref left reading a workspace attribute is a MONEY finding naming the key" 1 \
    "MONEY *_docker (body): 1 key(s), 0 quoted tagged literal(s)" "NOT A PIN" \
    "TinyPipelineSingle.gatk_docker" -- \
    "$PY" "$GD" --body "$GDFIX/body-unpinned-image.json" --capture "$GDFIX/capture.json"
# The capture's missing `useCallCache` is disclosed but not graded.
want "the capture's useCallCache silence is disclosed, never graded — the golden may not vouch either way" 0 \
    "disclosed, never graded: the capture says useCallCache='<absent>'" "VERDICT BODY-GOLDEN-MATCH" -- \
    "$PY" "$GD" --body "$GDFIX/body.json" --capture "$GDFIX/capture.json"

# 13.4 Refusals, one exit code each, each naming what it wanted.
want "an empty outputs map on the capture side is EMPTY-MAP at exit 4, not a score" 4 \
    "EMPTY-MAP capture.outputs" "holds 0 binding(s)" -- \
    "$PY" "$GD" --body "$GDFIX/body.json" --capture "$GDFIX/capture-empty-outputs.json"
want_not "and the refusal prints no verdict line at all, so a caller cannot read the bail as agreement" 4 \
    "EMPTY-MAP" -- "VERDICT" -- \
    "$PY" "$GD" --body "$GDFIX/body.json" --capture "$GDFIX/capture-empty-outputs.json"
want "a capture with no provenance block is refused at exit 5, naming every key a re-capture needs" 5 \
    "PROVENANCE MISSING" "_gsvtk_capture" "captured_utc" "command" "workspace" "config_name" \
    "config_namespace" -- \
    "$PY" "$GD" --body "$GDFIX/body.json" --capture "$GDFIX/capture-no-provenance.json"
want "a different rootEntityType is refused at exit 6 with BOTH values quoted" 6 \
    "ROOT-ENTITY MISMATCH body='sample' capture='sample_set'" -- \
    "$PY" "$GD" --body "$GDFIX/body.json" --capture "$GDFIX/capture-wrong-root.json"
# A tracked capture path is refused at exit 3 before it is read (`profiles/genotyping.json` parses, so
# exit 3 rather than 5 shows the tracked check runs first).
want "a capture path this repo TRACKS is refused at exit 3, before it is read, naming the deciding command" 3 \
    "CAPTURE IS TRACKED" "git ls-files --error-unmatch" "recon-live/" -- \
    "$PY" "$GD" --body "$GDFIX/body.json" --capture profiles/genotyping.json

# 13.5 Coverage floor: one of four input keys missing (0.750) is VACUOUS at the 0.900 default and scored
# at 0.7.
"$PY" - "$GDFIX" <<'GDFIXDROP'
"""The same capture with one input key gone, for the vacuity pair and its control."""
import json
import os
import sys

d = sys.argv[1]
doc = json.load(open(os.path.join(d, "capture.json")))
doc["inputs"].pop("TinyPipelineSingle.batch", None)
json.dump(doc, open(os.path.join(d, "capture-one-key-short.json"), "w"), indent=1)
GDFIXDROP
want "a capture one key short of the body is VACUOUS at exit 7 (3 of 4 shared = 0.750), never scored" 7 \
    "VACUOUS DIFF in" "shares 3 of 4 binding key(s)" "coverage 0.750" --min-coverage -- \
    "$PY" "$GD" --body "$GDFIX/body.json" --capture "$GDFIX/capture-one-key-short.json"
want "CONTROL: the SAME pair at --min-coverage 0.7 is scored, and the missing key is a named finding" 1 \
    "KEYS-BODY-ONLY inputs: 1 key(s)" "TinyPipelineSingle.batch" "coverage 0.750" "findings=1" -- \
    "$PY" "$GD" --body "$GDFIX/body.json" --capture "$GDFIX/capture-one-key-short.json" --min-coverage 0.7

# 13.6 The real launcher, offline: the money rules on `rerun-show --step 01`, unpinned and profile-pinned,
# each diffed against a capture derived from its own body.
cat > "$TMP/gd-real-body.py" <<'GDREAL'
"""The money rules against the body the shipped launcher really builds for step 01, offline.

argv: ROOT FIXTURE PINNED_DIR WORK OUT_DIR

Two arms, one module, one flag apart:
  UNPINNED  'rerun-show --step 01 --allow-unpinned-docker' — every *_docker the profile marks '--image'
            falls through to a 'workspace.<attr>' read, so money rule (ii) must be red on all 14.
  PINNED    the same step with the temp profile that names tagged refs (section 11c's 'pin-tagged'), so
            money rule (ii) must be green — 14 of 14 quoted tagged literals.
The capture each arm is diffed against is DERIVED from that arm's own body, in the shape a live capture has
(the three submission-only fields removed, one input key absent, the write-backs without '{new}') plus a
provenance block. That derivation plants two structural differences on purpose — 15 '{new}' write-backs and
the one key it drops — so the structural count is the same in both arms and the ONLY difference between the
arms' findings is the 14 money findings. A capture that agrees with the body about the images still cannot
pass money rule (ii): that is the whole design, and it is what the last assertion below pins.
Values are never written into the tracked tree: this file writes two JSONs into OUT_DIR and reads them back.
"""
import json
import os
import subprocess
import sys

ROOT, FIXTURE, PINNED, WORK, OUT = sys.argv[1:6]
WF = "GATKSVPipelineSingleSample"
DROP_INPUT = WF + ".case_stripy_file"            # the one key the live capture does not carry


def show(pinned):
    env = dict(os.environ, GSVTK_MODULE="single_sample", GSVTK_BRANCH="main",
               GSVTK_CONFIG=FIXTURE, GSVTK_WORK=WORK, GSVTK_READ_ONLY="1")
    args = [os.path.join(ROOT, "gsvtk"), "--read-only", "terra", "rerun-show",
            "--step", "01", "--entity", "NA12878"]
    if pinned:
        env["GSVTK_MODULE_DIR"] = PINNED
    else:
        args.append("--allow-unpinned-docker")
    r = subprocess.run(args, cwd=ROOT, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(("rerun-show exited %s\n%s" % (r.returncode, (r.stdout + r.stderr)[-1500:])))
    return json.loads(r.stdout)


def derive_capture(body, path):
    """A live-capture-shaped document out of our own body: no submission-only fields, no '{new}', provenance."""
    cap = {k: v for k, v in body.items()
           if k not in ("useCallCache", "deleteIntermediateOutputFiles", "maxMessageSize")}
    cap["inputs"] = {k: v for k, v in body["inputs"].items() if k != DROP_INPUT}
    cap["outputs"] = {k: (v[:-4] if v.endswith("_new") else v) for k, v in body["outputs"].items()}
    cap["_gsvtk_capture"] = {"captured_utc": "2026-01-01T00:00:00Z",
                             "command": "synthetic: derived from rerun-show output for the selftest",
                             "config_name": cap["name"], "config_namespace": cap["namespace"],
                             "workspace": "example-workspace", "note": "temp fixture, never committed"}
    json.dump(cap, open(path, "w"), indent=1)
    return cap


def check(cond, desc, detail=""):
    print(("  ok    " if cond else "  FAIL  ") + desc + (("  <%s>" % detail) if detail else ""))
    return bool(cond)


def _money_line(out):
    """The 'MONEY *_docker' line the checker printed, for a failure detail."""
    return next((line.strip() for line in out.splitlines() if "MONEY *_docker" in line), "(none printed)")


def _census(forms):
    """The `form=n, …` census string, in the same spelling the checker prints — one reading of the body."""
    counts = {}
    for f in forms.values():
        counts[f] = counts.get(f, 0) + 1
    return ", ".join("%s=%d" % kv for kv in sorted(counts.items()))


def find_count(out) -> str:
    """The 'findings=N' the checker printed, or -1: compared between arms, never typed as a constant."""
    for field in out.replace("|", " ").split():
        if field.startswith("findings="):
            return field[len("findings="):].strip(".")
    return "?"


bad, counts, forms_by_arm, money_by_arm = [], {}, {}, {}
for arm, pinned in (("unpinned", False), ("pinned", True)):
    body = show(pinned)
    body_path = os.path.join(OUT, "body-real-%s.json" % arm)
    json.dump(body, open(body_path, "w"), indent=1)          # a temp file, never the tracked tree
    cap_path = os.path.join(OUT, "capture-real-%s.json" % arm)
    cap = derive_capture(body, cap_path)
    images = sorted(k for k in body["inputs"] if k.endswith("_docker"))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "checks", "body_golden_diff.py"),
                        "--body", body_path, "--capture", cap_path],
                       cwd=ROOT, capture_output=True, text=True)
    out = r.stdout + r.stderr
    counts[arm] = find_count(out)
    money_by_arm[arm] = _money_line(out)
    # How many of the 14 image inputs this arm really leaves unpinned, read off the body rather than typed:
    # under this fixture profile the config layer's two image-repo keys answer 2 of the 14 and the profile
    # states a third itself, so 11 fall through to `workspace.<attr>` (section 11c pins that same 11; on a
    # machine with neither repo key it is 14).
    reads = [k for k in images if str(body["inputs"][k]).startswith("workspace.")]
    # The structural part the derivation plants, identical in both arms: 15 `{new}` write-backs plus the one
    # input key the derived capture does not bind. Hand-derived, so a checker that stopped counting
    # structural findings is caught here and not merely by the money lines.
    structural = len(body["outputs"]) + 1
    # Money rule (ii) is red on every image input whose ref NAMES NO VERSION — a set narrower than "not a
    # quoted tagged literal", because a ref may name its version in its LAST PATH SEGMENT instead of after
    # a colon. The checker
    # asks `module_profile.ref_version_kind`, the predicate the launcher's pin guard asks, so the one ref
    # the shipped profile declares `versioned_by: "path"` (`…/ubuntu1804`, a GCE image FAMILY) is pinned
    # here as it is there, and is DISCLOSED in its own bucket rather than counted. Counting `reads` alone
    # would still under-state the findings, so the set below is derived the same way the checker derives it
    # — by importing that predicate, not by restating it (a copy here would be the third definition of one
    # predicate, in the file whose job is to notice when the first two disagree).
    sys.path.insert(0, os.path.join(ROOT, "kit"))
    import module_profile

    def _form(v):
        text = str(v)
        if text.startswith("workspace.") or text.startswith("this."):
            return "reads-a-binding"
        if not (text.startswith('"') and text.endswith('"')):
            return "not-a-quoted-literal"
        kind = module_profile.ref_version_kind(text[1:-1], module_profile.VERSIONED_BY_PATH)
        return ("quoted-path-versioned" if kind == "path"
                else "quoted-tagged" if kind else "quoted-untagged")

    forms = {k: _form(body["inputs"][k]) for k in images}
    forms_by_arm[arm] = forms
    not_pinned = [k for k, f in forms.items() if f not in ("quoted-tagged", "quoted-path-versioned")]
    declared_path = [k for k, f in forms.items() if f == "quoted-path-versioned"]
    want_total = structural + len(not_pinned)
    bad += [c for c in (
        check(len(images) == 14 and len(body["outputs"]) == 15 and len(body["inputs"]) == 113,
              "%s: the step-01 body is the measured 113 inputs / 15 outputs, 14 of them image inputs"
              % arm.upper(), "got %s / %s / %s" % (len(body["inputs"]), len(body["outputs"]),
                                                   len(images))),
        check((arm == "unpinned") == bool(reads) and (not pinned or not reads),
              "%s: %d of the 14 image inputs read a workspace attribute in the body this built"
              % (arm.upper(), len(reads)), "keys: %s" % (reads or "(none)")),
        check("findings=%d" % want_total in out,
              "%s: %d findings = the %d structural ones the derivation plants + the %d image keys whose "
              "ref names no version" % (arm.upper(), want_total, structural, len(not_pinned)),
              "printed findings=%s, exit %s" % (counts[arm], r.returncode)),
        check("MONEY useCallCache: body=False -> OK" in out,
              "%s: the body states useCallCache false — the assertion a real capture cannot make, since a "
              "method-config payload carries no such field" % arm.upper()),
    ) if not c]
    if arm == "unpinned":
        bad += [c for c in (
            check("MONEY *_docker (body): 14 key(s), 2 quoted tagged literal(s), 1 pinned versioned-by-path "
                  "quoted-path-versioned=1, quoted-tagged=2, reads-a-binding=11 -> FINDING" in out,
                  "UNPINNED: the 11 refs neither the fixture's config layer nor the profile answers are "
                  "workspace reads, so money rule (ii) is red on 11 of 14 for this profile, and the one "
                  "ref the profile declares version-by-path is counted as pinned, not as a finding",
                  "exit %s, printed %s" % (r.returncode, _money_line(out))),
            # The pair that keeps the line above from being read off the checker's own output: the census
            # this file derives from the BODY (with the launcher's own predicate) must be the census the
            # checker printed. Two tools, one body, one answer — which is the whole claim A29 closed.
            check(_census(forms) == "quoted-path-versioned=1, quoted-tagged=2, reads-a-binding=11"
                  and " " + _census(forms) + " -> FINDING" in out
                  and declared_path == ["%s.linux_docker" % WF],
                  "UNPINNED: the census this file derived with module_profile.ref_version_kind from the "
                  "body IS the census the checker printed, and the one version-in-path ref is "
                  "GATKSVPipelineSingleSample.linux_docker (A29: one predicate, both sides of the pair)",                  "derived %s; declared-path keys: %s; printed %s"
                  % (_census(forms), declared_path, _money_line(out))),
            check("PINNED WITHOUT A TAG: 1 key(s)" in out
                  and "%s.linux_docker" % WF in out and "NOT A TAG" in out,
                  "UNPINNED: the version-in-path ref is DISCLOSED in its own bucket with its own words "
                  "(never counted as a tag, never as a finding) instead of silently joining the tagged",
                  "printed %s" % out[out.find("PINNED WITHOUT A TAG"):][:400] if "PINNED WITHOUT A TAG" in out
                  else "no PINNED WITHOUT A TAG line; %s" % _money_line(out)),
        ) if not c]
    else:
        bad += [c for c in (
            check("MONEY *_docker (body): 14 key(s), 14 quoted tagged literal(s), 0 pinned versioned-by-path "
                  "quoted-tagged=14 -> OK" in out,
                  "PINNED: the same step, one flag and one profile apart, is green on money rule (ii), and "
                  "says so with ZERO version-by-path keys because this profile tags all 14",
                  "exit %s, printed %s" % (r.returncode, _money_line(out))),
            check(_census(forms) == "quoted-tagged=14" and "PINNED WITHOUT A TAG" not in out,
                  "PINNED: the census derived here matches the printed one, and no version-in-path bucket "
                  "appears when every ref carries a tag — the new bucket cannot be a free pass that fires "
                  "on a body that never needed it",
                  "derived %s; printed %s" % (_census(forms), _money_line(out))),
            check(DROP_INPUT in out,
                  "and the derived capture's one dropped input key is named as a KEYS-BODY-ONLY finding",
                  out[-400:]),
        ) if not c]
# The discriminating pair, which is the reason both arms run: the two captures AGREE with their own bodies
# about every image ref (they were derived from them), so a diff that trusted the capture would report
# nothing about the images in either arm. The money assertion does not trust it: one flag moves the count by
# exactly the image keys that stop being findings, in a run where the structural side is byte-for-byte the
# same story. The delta is 11 and not 14 now, and the reason is A29's whole subject: one of the 14 is
# version-in-path in BOTH arms (the profile declares it, the `pin-tagged` fixture replaces it with a tag),
# so the pair that moves is the 11 workspace reads — counted off the bodies, never typed.
_not_pinned = lambda d: {k for k, f in d.items()
                         if f not in ("quoted-tagged", "quoted-path-versioned")}
moves = sorted(_not_pinned(forms_by_arm.get("unpinned", {})) - _not_pinned(forms_by_arm.get("pinned", {})))
bad += [c for c in (
    check(counts.get("unpinned", "?").isdigit() and counts.get("pinned", "?").isdigit()
          and int(counts["pinned"]) + len(moves) == int(counts["unpinned"]) and len(moves) == 11,
          "the two arms differ by exactly the %d image keys that stop being findings (%s vs %s), on the "
          "same structural count" % (len(moves), counts.get("pinned"), counts.get("unpinned")),
          "unpinned=%s pinned=%s movers=%d" % (counts.get("unpinned"), counts.get("pinned"), len(moves))),
) if not c]
# Printed on every run so the counts are visible on a green run too.
_measured = []
for _arm in ("unpinned", "pinned"):
    _measured.append("A29 MEASURED %s %s | findings=%s | version-in-path keys: %s"
                     % (_arm.upper().ljust(9), _census(forms_by_arm.get(_arm, {})), counts.get(_arm, "?"),
                        ", ".join(sorted(k for k, f in forms_by_arm.get(_arm, {}).items()
                                         if f == "quoted-path-versioned")) or "none"))
# Written to OUT_DIR as well as asserted: the phase swallows a GREEN child's stdout, so a proof column that
# quoted an asserted-only number would name a line no reader can see on a passing run. It is echoed by the
# phase itself, below the `want` that pins it.
open(os.path.join(OUT, "a29-measured.txt"), "w").write("\n".join(_measured) + "\n")
for _line in _measured:
    print(_line)
print("body-vs-capture money arms: %d failed" % len(bad))
sys.exit(1 if bad else 0)
GDREAL
# The script writes one temp JSON per arm, never into the tracked tree.
want "the money rules on the body the shipped launcher builds for step 01, unpinned and pinned" 0 \
    "body-vs-capture money arms: 0 failed" "UNPINNED: the 11 refs neither the fixture's config layer nor the profile answers" \
    "one version-in-path ref is GATKSVPipelineSingleSample.linux_docker" \
    "A29 MEASURED UNPINNED  quoted-path-versioned=1, quoted-tagged=2, reads-a-binding=11 | findings=27 | version-in-path keys: GATKSVPipelineSingleSample.linux_docker" \
    "A29 MEASURED PINNED    quoted-tagged=14 | findings=16 | version-in-path keys: none" \
    "PINNED: the same step, one flag and one profile apart, is green on money rule (ii)" \
    "the two arms differ by exactly the 11 image keys" -- \
    "$PY" "$TMP/gd-real-body.py" "$ROOT" "$RERUN_FIXTURE" "$TMP/pin-tagged" "$TMP/gd-work" "$GDFIX"
# Echo the measured money lines so they are visible on a passing run.
[ -f "$GDFIX/a29-measured.txt" ] && sed 's/^/  measured  /' "$GDFIX/a29-measured.txt"

printf 'profiles selftest: %s ok, %s failed, %s skipped\n' "$ok" "$fail" "$skipped"
# Declared and reached cannot detect a deleted call site (both drop together); see declared.sh.
gsvtk_declared_report "profiles selftest" "$SELF" "$((ok + fail))" "$skipped" || fail=$((fail + 1))
[ "$fail" -eq 0 ]
