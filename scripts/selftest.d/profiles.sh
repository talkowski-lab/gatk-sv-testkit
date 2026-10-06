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
# The last assertion is the important one, and it is a FAILURE assertion: a dropped binding must make the
# golden check FAIL. A check that cannot fail is the thing this repo keeps meeting.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi

FIXTURE="$ROOT/scripts/selftest.d/fixtures/genotyping.profile.env"
ok=0; fail=0
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
    "bare new-side attribute names" "_why_" -- \
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
want "CONTROL: NO TEMPLATE is its own state, and it says the finding had no source" 0 \
    "NO TEMPLATE" "its absence means nothing" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/work" "$PY" terra/batch_configs.py check \
    --config "$CD/tiny.json" --wdl-dir "$WD" --tmpl-dir "$TMP/empty-templates"

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

printf 'profiles selftest: %s ok, %s failed\n' "$ok" "$fail"
# Equal sets, every run. What the pair cannot see is a DELETION: remove a call site and declared and reached
# drop together, so the all-clear survives the loss — see the header of scripts/selftest.d/declared.sh.
gsvtk_declared_report "profiles selftest" "$SELF" "$((ok + fail))" 0 || fail=$((fail + 1))
[ "$fail" -eq 0 ]
