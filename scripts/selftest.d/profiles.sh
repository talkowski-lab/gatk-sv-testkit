#!/usr/bin/env bash
# scripts/selftest.d/profiles.sh — the module-profile assertions, standalone.
#
#     bash scripts/selftest.d/profiles.sh .venv/bin/python
#
# What this phase owns that nothing else does
# -------------------------------------------
# `make test` already sweeps --help, pyflakes and the 18 reproduced-defect probes. None of those read a
# profile: the probes in `scripts/probe_fixes.py` synthesize their "declared" side FROM
# `batch_configs.CONFIGS` itself, so a binding lost between the table and the profile vanishes from both
# sides of every one of them. This file is the phase whose whole subject is the data — the golden, the
# loader's refusals, and the one template finding the WDL cannot give you.
#
# Own tally, own exit code, wired into `make selftest` as one added line. Run it alone while working on
# the profile: it takes a couple of seconds and needs no user profile, no gatk-sv checkout, and no
# network (the template case reads a fixture tree built below with `--wdl-dir`/`--tmpl-dir`, so no ref is
# ever resolved). bash 3.2 compatible.
#
# ONE block is the exception, and says so out loud: section 11e grades the SECOND module (`
# profiles/single_sample.json`) against a real gatk-sv checkout at a pinned ref, because the finding it
# makes is about that tree's WDL and template, not about this repo's data. It is a NAMED, COUNTED skip
# (region `single-sample-check`) wherever the checkout or miniwdl is absent, which is CI, and the skip
# line prints how many assertions it withheld.
#
# The last assertion is the important one, and it is a FAILURE assertion: a dropped binding must make the
# golden check FAIL. A check that cannot fail is the thing this repo keeps meeting.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi

FIXTURE="$ROOT/scripts/selftest.d/fixtures/genotyping.profile.env"
ok=0; fail=0; skipped=0
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"

# ------------------------------------------------- declared vs reached (docs/gap-ledger.md C10)
# Both numbers count assertion call SITES, so they can be required to be EQUAL: this phase has no jar mode, no
# checkout mode and no per-machine constant, and the pair checks that whatever this file declares is what the
# run actually reached — by line number when it is not. The callees are what gsvtk_declared_funset derives from
# this file (`want`, `canary`), re-derived by the reporter on every run. Note the 23 this phase prints against
# the 22 sites: the scratch-directory check in section 10 records its verdict in a top-level if/fi, outside any
# recorder, so it is a verdict and not a site — which is why the reporter prints both totals side by side
# instead of one.
SELF="$ROOT/scripts/selftest.d/profiles.sh"
. "$ROOT/scripts/selftest.d/declared.sh"
GSVTK_DECL_FUNCS="want canary"
gsvtk_declared_init "$TMP"          # the ledger is the set of reached sites (bash 3.2: no associative arrays)

# want DESC WANT_RC NEEDLE... -- CMD...
#
# Asserts the exit code AND that every needle appears in the combined output. Exit code alone cannot tell
# "refused for the right reason" from "refused because the file was missing", which is the difference
# between a guard and a coin flip.
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
        printf '%s' "$out" | grep -qF -- "$n" || miss="$miss
          missing: $n"
    done
    if [ "$rc" = "$wantrc" ] && [ -z "$miss" ]; then
        ok=$((ok + 1)); printf '  ok    %s (exit %s)\n' "$desc" "$rc"
    else
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s, want %s)%s\n' "$desc" "$rc" "$wantrc" "$miss"
        printf '%s\n' "$out" | head -8 | sed 's/^/          /'
    fi
}

# A canary for THIS file only: `selftest.sh` proves its own harness is alive, but this file is a separate
# script with its own tally, and a tally that cannot go down certifies nothing.
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

echo
echo "selftest: the module profile is the same data the maps were (offline, no user profile)"
canary

# 1. THE GOLDEN. Byte equality of `show` and of all five `body()` maps against a checked-in capture,
#    regenerated from a placeholder profile so CI reproduces it with no user configuration.
want "the maps match the golden byte for byte" 0 \
    "byte-identical to the goldens" "5 config(s), 64 input binding(s), 36 output binding(s)" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" "$PY" scripts/golden_configs.py

# 2. THE CONTROL for that golden, and the reason the golden is evidence: drop one input binding from step
#    06 and one output binding from step 10 out of a copy of the table, and the check must FAIL both
#    times. It also asserts the untouched capture passes first, so "it failed" cannot come from a check
#    that was already broken.
want "a planted dropped binding makes the golden check FAIL" 0 \
    "planted drop of GenerateBatchMetrics.median_file from 06-GenerateBatchMetrics.inputs -> the golden check FAILS" \
    "planted drop of GenotypeBatch.regeno_coverage_medians from 10-GenotypeBatch.outputs -> the golden check FAILS" \
    "control: ok" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" "$PY" scripts/golden_configs.py --control

# The refusal text is asserted against a profile directory that holds one broken file at a time. MODULE_DIR
# points into a temp tree so the tracked profile is never edited to test a refusal.
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

# 3. `schema_version` is mandatory BECAUSE it is the only way to tell a stale reader from a stale file
#    (§3 rule 9). A version this build does not implement must refuse, not best-effort parse.
broken schema_version schema_version=99
want "an unknown schema_version refuses rather than parsing anyway" 4 \
    "is unusable" "schema_version 99 is not a version this build reads" \
    "genotyping.json" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-schema_version" \
    "$PY" terra/batch_configs.py show

# 4. A missing module names the file to write AND every field a working profile needs. The earlier error
#    text named 5 of the fields and following it produced a file that could not drive a chain, so the
#    COMPLETE list is the assertion here, not a substring.
want "a missing module exits 4, names the file, and lists every field in full" 4 \
    "is missing" "profiles/copynumber.json" "schema_version" "callers" "steps" \
    "step " "wdl " "workflow" "rootEntityType" "inputs" "outputs" "branch_only_inputs" \
    "bare new-side attribute names" "entity_row" "_why_" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE=copynumber \
    "$PY" terra/batch_configs.py show

# 5. An orphan `_why_` is a refusal, not a comment. The pairing is the only thing that keeps a rationale
#    from rotting into a sentence about a key that no longer exists.
broken orphan_why orphan_why=_why_GenotypeBatch.nope_bound
want "an orphan _why_ sibling refuses, by name" 4 \
    "orphan rationale" "_why_GenotypeBatch.nope_bound" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-orphan_why" \
    "$PY" terra/batch_configs.py show

# 6. Literal-vs-path typing (§3 rule 3): a value is a path iff it starts this./workspace., so a suffix
#    token on a literal is a contradiction -- a literal has no attribute to append `_frz` to.
broken literal_token literal_token=GenerateBatchMetrics.chr_x
want "a suffix token on a non-path value refuses as a typing error" 4 \
    "carries a suffix token but is not a path" "this./workspace." -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-literal_token" \
    "$PY" terra/batch_configs.py show

# 7. `wdl` and `workflow` stay two fields. For these five steps they agree, so nothing here would notice a
#    collapse -- which is exactly why the split needs a case where they differ. 12 of the 109
#    workflow-bearing WDLs at main are like this (DepthClustering->ClusterDepth, Genotype_2->Regenotype).
broken split_wdl split_wdl=x
want "wdl and workflow are carried separately, not as one string serving three lookups" 0 \
    "wdl=DepthClustering workflow=ClusterDepth" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" GSVTK_MODULE_DIR="$TMP/mod-split_wdl" \
    "$PY" kit/module_profile.py --print

# 8. The template side: DERIVED / JINJA-NEUTRALISED / NO TEMPLATE, and the one finding only the template
#    can give. Built from fixtures with --wdl-dir and --tmpl-dir, so no ref is resolved and no checkout is
#    read. The control is the same tree with the optional key removed from the template: the finding must
#    go away, which is what makes it appear to be about the data rather than about the code.
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

# 8b. The duplicate-key arm of the SAME reader, on the SAME fixture. `read_template` neutralises Jinja
#     and parses strictly, and a strict parse is last-wins: a template binding `Tiny.mei_bed` twice
#     printed "N key(s) bound by production" and ONE value for it, which is a true statement about what
#     a submission binds and a false statement about the file a reviewer is reading. `json.loads` is not
#     changed here -- the fact is recorded next to it.
#     Two sites, a pair, because the fire alone proves nothing: the census prints the same sentence shape
#     at 0 and at 1, so only the 0 arm shows the number is counting documents rather than reciting text.
#     The exit code is asserted on BOTH: non-fatal is the measured decision, not the polite one, because
#     upstream's own templates at `e1909d2f` carry three duplicates (docs/module-profiles.md §9 step 3) and
#     a fatal version of this would refuse launches that launch fine today.
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

# One duplicate, two readers of the same bytes. `terra/batch_configs.py` reads a step's template to grade
# ONE module's map; `checks/terra_entity_check.py` reads the whole upstream corpus and prints `dupkeys=`.
# They implement the rule separately, exactly like the name-key strip below, so this runs one file
# through BOTH commands and compares the NAMES they print -- a drift is one reader silently dropping
# duplicates while the other keeps refusing to be quiet about them. Neither side is imported (that would
# drag `firecloud` into an offline leg for one function), so both sides are the shipped CLIs and this leg
# has no SKIP branch: it runs, or the phase fails.
cat > "$TMP/dupkey-sibling.py" <<'PY'
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

tmp = tempfile.mkdtemp()
atexit.register(shutil.rmtree, tmp, True)     # reading two checkers must not litter, same as _tree_from_ref


def batch_names(dirpath):
    """(names, exit code) from `check`: the DUPLICATE-KEY lines it printed, and whether it stayed fatal."""
    r = subprocess.run([PY, "terra/batch_configs.py", "check", "--config", CFG,
                        "--wdl-dir", WD, "--tmpl-dir", dirpath],
                       cwd=ROOT, env=ENV, capture_output=True, text=True)
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
    assert rc_b == 0, (arm, "a duplicate key moved the exit code:", rc_b)
    assert found_b == expect, (arm, "the template reader said", found_b, "want", expect)
    assert found_e == expect, (arm, "the corpus reader said", found_e, "want", expect)

print("PASS two readers, one document: the duplicate is named by both and the clean document is named "
      "by neither")
PY
want "the template reader here and the offline corpus checker name the SAME duplicate on the SAME bytes" 0 \
    "PASS" -- "$PY" "$TMP/dupkey-sibling.py" "$ROOT" "$WD" "$CD/tiny.json" "$FIXTURE" "$TMP/work"

# 9. `show` answers which module and why that one, on a door that already existed: MODULE resolves with no
#    profile at all, and MODULE_DIR is derived to the checkout's own profiles/ rather than to a path that
#    would be wrong on another machine.
want "gsvtk-config show answers which module, from where, and whether it exists" 0 \
    "module genotyping [default]" "profiles/genotyping.json" "[derived: <repo>/profiles]" \
    "present" -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" ./kit/gsvtk-config show
want "and the same door says MISSING rather than implying a module is configured" 0 \
    "module copynumber [env]" "MISSING" -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" GSVTK_MODULE=copynumber \
    ./kit/gsvtk-config show

# 10. The loader creates nothing. `--help` and a read-only `show` must not leave a scratch directory
#     behind, which is the `probe_help_writes_nothing` contract applied to the new reader.
want "reading a profile creates no file and no directory" 0 \
    "byte-identical" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/never-created" "$PY" scripts/golden_configs.py
if [ -e "$TMP/never-created" ]; then
    fail=$((fail + 1)); printf '  FAIL  reading the profile created %s\n' "$TMP/never-created"
else
    ok=$((ok + 1)); printf '  ok    and the scratch directory it could have created is still absent\n'
fi

# 11. The export list: the data relationship, and whether the shell obeys the data.
#
# Two assertions in one numbered block, because they answer two different questions and the second one is
# what makes the first worth reading:
#
#   THE GUARD (`export-guard.py`) pins the DATA: the 7 attributes `fetch` demands against the attributes
#   the profile says the chain writes, delta stated as a fact rather than smoothed over.
#   THE PROBE (`export-data.py`) pins the WIRING: `fetch` asks `kit/module_profile.py` for `steps[].export`
#   and plans what it is told, so a second module needs a profile rather than a shell edit
#   (docs/gap-ledger.md A21, docs/module-profiles.md §9 step 4).
#
# Reverting `terra/batch_fetch_compare.sh` to the version that owned the list literally leaves THE GUARD
# GREEN -- its 7 literals still agree with the profile's 8 non-index outputs, which is the whole story it
# ever told -- and fails THE PROBE on four of its five checks. That asymmetry is why the probe exists: a
# guard that cannot notice a shell ignoring its data is a check on the data only, and the change made here
# is a change to the shell. Falsified that way before it was trusted; see the probe's docstring.
#
# `fetch --dry-run` touches no Terra, no bucket and no dependency (the entity read sits behind
# `if [ "$DRY_RUN" -eq 1 ]`), so neither of these has a SKIP branch: no firecloud, no miniwdl, no
# checkout, no network. It either runs or the phase fails.
#
# The delta the guard states, measured at this commit:
#   * what the terminal step's `this.*` outputs give -- `_index` siblings excluded, because §3 rule 4 makes
#     index closure a code rule -- 8 names;
#   * what `export` holds and the script demands: 7 names;
#   * the one difference: `regeno_coverage_medians`, written by the step and listed OPTIONAL by the script.
# Promoting it was asked and REJECTED: "required" is the script's exit code (`fetch incomplete: N required
# *<new> attribute(s) not set yet` → `return 1`), so a required attribute that some chain never wrote
# turns every such baseline into a fetch that exits 1 where it passes today. That is a product decision
# with a money-path consequence, to be taken with a real run in hand -- and §9 step 4 and the profile's
# `_why_export` both carry the number so nobody re-derives it.
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
# CONTROL, and the reason the agreement above is a claim about the DATA rather than a tautology: rename
# one terminal-step output IN THE PROFILE and the `outputs` side moves, while the `export` list -- separate
# data, which the loader deliberately does NOT recompute from `outputs` -- cannot follow. The guard fails by
# name, on both sides of the delta. If the loader did derive `export` from `outputs`, this control could
# not fail; that is a large part of why the loader does not.
broken rename_output rename_output=genotyping_pe_table:genotyping_pe_table_v2
want "CONTROL: renaming a terminal-step output in the profile breaks the export-list guard, by name" 1 \
    "FAIL" "genotyping_pe_table_v2" "export-list guard: " -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/guardwork2" GSVTK_MODULE_DIR="$TMP/mod-rename_output" \
    "$PY" "$TMP/export-guard.py" "$ROOT" "$TMP/guardwork2"

# 11b. The same list, the other question: does the shell OBEY the field?
#
# This probe is the positive control for the change that made `export` a field. It edits a TEMPORARY copy
# of the profile -- the tracked file is never opened for writing -- and requires the plan to move.
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
# The same refusal at the shell's own door, needles and exit code asserted rather than inferred from the
# probe: exit 4 is the config layer's "a value you need is not there", kept distinct from the generic 1.
broken export_strip export_strip=x
want "a profile with no export field: fetch refuses with 4, names every step it looked on, plans nothing" 4 \
    "exports nothing" 'steps `export` was looked for on' "06-GenerateBatchMetrics" "10-GenotypeBatch" \
    "Refusing" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/stripwork" GSVTK_MODULE_DIR="$TMP/mod-export_strip" \
    bash terra/batch_fetch_compare.sh fetch --dry-run

# 11c. `export`'s shape, refused by name like every other field in this schema. The three that a real
# author reaches for: pasting a `this.x{new}` value out of `outputs` (that is a PATH, §3 rule 3, and it
# would ask the entity for an attribute no workspace holds), an empty list (a step nobody fetches from
# leaves the field out -- an empty list is the named failure class), and a duplicate (fetches twice).
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

# 11b. `entity_row`: the row's SOURCE is data, and the name-key rule is shared with the deriver.
#
# Two claims live here that no other phase owns.
#
# THE SHAPE refusals. `entity_row` names the config-layer key that holds a step's row NAME, and a value
# that is not a known key must not survive to `kit/gsvtk-config::get` -- that raises `KeyError`, so a typo
# in a profile would be a traceback on a launch path instead of an exit 4 naming the field. The same
# refusal family as `export`'s: a value out of the `inputs` map (`workspace.cohort_name`) is a BINDING,
# not a source, and a launcher holding it would submit against a row named "workspace.cohort_name".
#
# THE SHARED RULE. `terra/steps.py` derives each step's entity type from its own `this.<etype>_id` read
# (`module_profile.name_key_type`), and `checks/terra_entity_check.py` derives the type of an upstream
# launch config the same way. Two corpora, one rule -- and the two strips are compared here, because a
# drift between them is a launcher that disagrees with the checker that grades it, which is this repo's
# named failure class wearing a new hat.

# The control first: the shipped value loads and answers. Every refusal below changes ONE value against
# this, so "it refused" cannot be about the field's presence rather than its content.
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

# The shipped data, censused rather than assumed: every step states where its row comes from. This is the
# half the launcher cannot check -- a step that OMITS the field behaves exactly like one that states
# `--entity`, so nothing on any launch path ever notices the difference. Here it is a finding.
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

# One name-key rule, two readers of it. The list is the measured vocabulary plus the two shapes that must
# answer "no type": a bare `id`, and a suffix with nothing in front of it.
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

# 11d. The SECOND module: `profiles/single_sample.json`, and the facts a reader cannot get from anywhere else.
#
# `profiles/genotyping.json` is the module every other assertion in this file is about. A schema with one
# implementation is a schema that has never been tested against a second answer, so this section grades the
# file that answer is — offline, through the same two doors the loader offers a person (`--print` and
# `terra/steps.py`), with no checkout and no network.
#
# What it owns that no other section can: the two things the loader deliberately does NOT check, because a
# loader that checks them makes the guard that checks them unable to fail. (a) Every output writes the
# attribute its own key names — `GATKSVPipelineSingleSample.qc_file` -> `this.qc_file{new}`. Nothing compares
# an output's LEAF to the attribute it writes, and while authoring this very profile that rule was broken in
# two ways at once (`this.stripy_vcf_vcf{new}`, and a `_new` typed by hand where the `{new}` token belongs);
# both load perfectly, both print, and both silently write back an attribute the next step never reads.
# (b) A named gap stays a gap: four keys are recorded unbound in `_why_unbound`, and the leg refuses a
# profile that binds one while the rationale still says it is missing. Both halves run against a PLANTED
# copy, so the leg is shown to be able to fail rather than asserted to.
want "the second module loads and states its step/wdl/workflow/type/row triple" 0 \
    "callers bound: (none)" \
    "wdl=GATKSVPipelineSingleSample workflow=GATKSVPipelineSingleSample [sample] row=--entity" \
    "113 in / 15 out (33 path, 95 literal)" -- \
    env GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work" \
    "$PY" kit/module_profile.py --module single_sample --print

# The three tables three tools used to copy are derived from the profile for THIS module too, and the root
# entity is derived from the name key rather than read off `rootEntityType`: the profile says `sample` and
# `this.sample_id` says `sample`, and this leg asserts the derived answer, not the typed one.
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
    `{frz}` side to read (docs/gap-ledger.md A18 is the unbuilt freeze loop for it). A `{frz}` here would
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

# CONTROL 2: fill a named gap without editing the rationale that calls it a gap.
planted2 = copy.deepcopy(STEP)
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

# 11e. The second module against a REAL gatk-sv ref -- the one block in this file that reads a tree this
#      repo does not ship, and the only place the profile's remaining hole is measured rather than claimed.
#
# Pinned to `e1909d2f` because every number below is a claim about THAT tree (the WDL declares 381 inputs /
# 15 outputs; the template binds 111 keys and carries the duplicate that docs/gap-ledger.md A24 counts).
# No checkout holding that commit, or no miniwdl in this interpreter, is a NAMED COUNTED SKIP -- the CI
# case -- and the profile's `_why_unbound` is what a machine without either still gets to read.
CK="$("$PY" -c 'import sys; sys.path.insert(0, "kit"); import config; print(config.get("GATK_SV_CHECKOUT"))' \
     2>/dev/null | tail -1)"
PINNED=e1909d2f
# Which prerequisite is missing, decided BEFORE the branch, because a skip line that blames both blames
# neither: `entity.sh` separates its checkout region from its miniwdl region for the same reason, and
# `docs/gap-ledger.md` C10's whole point is that a named skip has to name the thing it withheld.
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
    # Name the ONE prerequisite that is missing. `entity.sh` splits its checkout region from its miniwdl
    # region for the same reason (docs/gap-ledger.md C10): a skip that blames both blames neither, and a
    # reader who cannot tell which door is shut cannot tell what to install.
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

# 12. `fetch_baseline.py`: a baseline that skipped a step must not read like a baseline that had none.
#
# The last name on docs/module-profiles.md §9 step 4's list. `fetch_baseline.py` enumerates a workspace
# rather than requesting a known step, so `pick_config` deliberately declines `steps.match_configs`
# (its comment says refusing would turn "report what is there" into "agree with my table") -- which
# leaves the emptiness handled, not avoided: `!! no config for step NN`, a `steps_missing` list inside
# `baseline_run.json`, a counted summary and exit 1. That behaviour shipped in 0029972 with **no test at
# all**, and this repo's position on an unguarded guard is in its own header: a check that cannot fail is
# not a check.
#
# It lives in this phase rather than `rerun.sh` because the thing under test is what §9 step 4 made
# single-sourced -- who is a step, and what happens when a step is missing -- and because the harness
# stubs `terra` in process, so unlike the rerun phase it needs no `firecloud` and has no SKIP branch:
# it either runs, or the phase fails. Nothing here reaches a network: an unexpected request raises
# instead of asking, and the API root is a closed port.
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
check("!! 4 of 6 step(s) had no config to freeze: 05, 07, 08, 09" in out,
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

# 11c. Image pins: a step can NAME its images, the loader grades the shape, and the more specific route wins.
#
# The gap this grades: 14 `*_docker` inputs needed a stated image and the profile had vocabulary for two, so
# `rerun-show --step 01` could not print a body at all -- which is the same refusal that cost a real run its
# reproducibility when it went the other way (`*_docker` resolving from workspace attributes). Shipped data pins
# nothing (all 14 are the `--image` marker); a machine that wants pins in a file puts tagged refs here.
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

# Shipped data first: a profile that names every image must NOT be the thing that unlocks the money rule.
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
# The retry trap, counted rather than described. A step whose profile marks every image `--image` builds a body that
# reads all 14 refs from workspace attributes -- and a workspace can stop having them (measured on the real single-sample
# workspace: `attributes` empty, while its stored config holds 14 literal tagged refs). That body still previews clean,
# so the count is asserted here: unpinned means reads, fully pinned means ZERO reads. 12 under this fixture, not 14 --
# measured, and the difference is the fixture's own config layer, whose GSVTK_IMAGE_REPO / GSVTK_GATK_IMAGE_REPO answer
# two of the shipped profile's 14 `*_docker` keys, leaving 12 to fall through to workspace attributes. On the real
# workspace, with neither the attributes nor those two repo keys present, all 14 fell through.
want "unpinned builds workspace-attribute image reads (12 under this fixture); a fully pinned build builds zero" 0 \
    "reads-unpinned=12 reads-pinned=0" -- sh -c \
    "u=\$(env GSVTK_MODULE=single_sample GSVTK_BRANCH=main GSVTK_CONFIG='$RERUN_FIXTURE' GSVTK_WORK='$TMP/pinwork' GSVTK_READ_ONLY=1 '$ROOT/gsvtk' --read-only terra rerun-show --step 01 --entity NA12878 --allow-unpinned-docker 2>/dev/null | grep -c ': \"workspace\\.'); \
p=\$(env GSVTK_MODULE=single_sample GSVTK_BRANCH=main GSVTK_MODULE_DIR='$TMP/pin-tagged' GSVTK_CONFIG='$RERUN_FIXTURE' GSVTK_WORK='$TMP/pinwork' GSVTK_READ_ONLY=1 '$ROOT/gsvtk' --read-only terra rerun-show --step 01 --entity NA12878 2>/dev/null | grep -c ': \"workspace\\.'); \
echo \"reads-unpinned=\$u reads-pinned=\$p\""

# Precedence, the thing most likely to be got wrong: a pin sitting in a file must not outvote the operator, and
# must not silently stand in for a choice the operator forgot to make. Both directions, same run, one key apart.
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

# The three names, in one leg: the Dockstore ENTRY is `SingleSamplePipeline` (.github/.dockstore.yml:256-257, and
# the live golden's methodUri) while the config name and every binding prefix stay on the name the WDL declares,
# `GATKSVPipelineSingleSample` (wdl/GATKSVPipelineSingleSample.wdl:35). Before this field existed the launcher used
# one name for both, which meant the config pointed at a Dockstore entry that does not exist -- and Terra accepts
# such a config, then fails downstream. Asserting both halves here is the point: a leg on the entry alone would also
# pass if the config name had been moved to the entry name too.
want "step 01 resolves its METHOD from the Dockstore entry while its config name stays on the WDL name" 0 \
    "Dockstore github.com/broadinstitute/gatk-sv/SingleSamplePipeline" \
    "config 01-GATKSVPipelineSingleSample-rerun" "root entity sample" -- \
    env GSVTK_MODULE=single_sample GSVTK_MODULE_DIR="$TMP/pin-tagged" GSVTK_CONFIG="$RERUN_FIXTURE" \
    GSVTK_WORK="$TMP/pinwork4" GSVTK_READ_ONLY=1 \
    "$ROOT/gsvtk" --read-only terra rerun-show --step 01 --entity NA12878

# Rawls types the input map as Map[String, String] and rejects the whole body (`HTTP 400 "unexpected json type"`,
# measured on a real create attempt) if any value arrives as a parsed literal. The profile stores upstream's parsed
# values, so the coercion to Terra's string form is load-bearing and was ungraded: this asserts it on the resolved
# map, in the shape the live capture has (0 non-string values of 112). A leg that only checked the printed body would
# pass while some other caller still POSTed the parsed form.
want "every resolved input value is a string, because Rawls types the map Map[String, String]" 0 \
    "all inputs are strings" -- \
    env GSVTK_MODULE=single_sample "$PY" -c \
    "import sys; sys.path[:0]=['$ROOT/kit']; import module_profile as mp
m = mp.load().configs
bad = {k: type(v).__name__ for c in m.values() for k, v in c['inputs'].items() if not isinstance(v, str)}
print('all inputs are strings' if not bad else f'NON-STRING VALUES: {bad}')"

# Upstream's template leaves 84 of this module's bindings as `workspace.<attr>` reads, which work only in a workspace
# someone populated first. The target workspace was not populated, so the submission was refused (`HTTP 400 Validation
# errors: Invalid inputs`) AFTER the config had passed `validate` -- validate typechecks against the WDL and never
# resolves a workspace attribute. This asserts the rule the run depends on: a launch states its own resources. Scoped
# to the module rather than to every config, because the batch chain's own workspaces do carry those attributes and
# its configs legitimately read them. `*_docker` is excluded because the image rule owns those keys: they read
# `workspace.gatk_docker` until a --image states them, and 14 legs already grade that -- not this one.
want "no single-sample binding is left reading a workspace attribute, because a rerun must state its own resources" 0 \
    "no workspace attribute reads" -- \
    env GSVTK_MODULE=single_sample "$PY" -c \
    "import sys; sys.path[:0]=['$ROOT/kit']; import module_profile as mp
ws = {k: v for c in mp.load().configs.values() for k, v in c['inputs'].items()
        if isinstance(v, str) and v.startswith('workspace.') and not k.endswith('_docker')}
print('no workspace attribute reads' if not ws else f'{len(ws)} BINDINGS READ WORKSPACE ATTRS: {sorted(ws)[:4]}')"

# The literal layer must not carry image refs: a `*_docker` literal makes some registry's default the code that runs
# on a launch where no --image was given, and the live capture's own `sv_pipeline_docker` was a personal registry path
# carrying a username -- exactly the value a head-to-head must never silently fall back to. Planted, so the refusal is
# shown to fire rather than asserted to.
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

# checks/version_resolves.py: the class that cost two real POST failures today -- an entry that was never published, and
# a version whose branch is gone. Fixtures are synthetic and built here: a real TRS listing is a list of other people's
# branch names, and the publish audit (correctly) refuses that in a tracked file.
mkdir -p "$TMP/trs"
"$PY" - "$TMP/trs/published.json" "$TMP/trs/empty.json" <<'PYEOF'
import json, sys
tools = [{"id": "#workflow/github.com/broadinstitute/gatk-sv/SingleSamplePipeline",
          "versions": [{"name": n} for n in ("main", "v1.1.1", "mw_probe")]}]
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
    "NO SUCH VERSION" "mw_fix_single_sample_blocking" -- \
    env GSVTK_MODULE=single_sample "$PY" "$ROOT/checks/version_resolves.py" \
    --entry github.com/broadinstitute/gatk-sv/SingleSamplePipeline \
    --version mw_fix_single_sample_blocking --from-file "$TMP/trs/published.json"
want "and an empty listing is refused rather than read as evidence of absence" 4 \
    "LISTING UNUSABLE" "not evidence" -- \
    env GSVTK_MODULE=single_sample "$PY" "$ROOT/checks/version_resolves.py" \
    --entry github.com/broadinstitute/gatk-sv/SingleSamplePipeline --version main \
    --from-file "$TMP/trs/empty.json"
want "the checker's own suite runs on the gate, with its fixture count" 0 "5 ok, 0 failed" -- \
    "$PY" "$ROOT/checks/version_resolves.py" --selftest

printf 'profiles selftest: %s ok, %s failed, %s skipped\n' "$ok" "$fail" "$skipped"
# Equal sets, every run. What the pair cannot see is a DELETION: remove a call site and declared and reached
# drop together, so the all-clear survives the loss — see the header of scripts/selftest.d/declared.sh.
gsvtk_declared_report "profiles selftest" "$SELF" "$((ok + fail))" "$skipped" || fail=$((fail + 1))
[ "$fail" -eq 0 ]
