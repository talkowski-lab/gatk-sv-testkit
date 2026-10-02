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

# want DESC WANT_RC NEEDLE... -- CMD...
#
# Asserts the exit code AND that every needle appears in the combined output. Exit code alone cannot tell
# "refused for the right reason" from "refused because the file was missing", which is the difference
# between a guard and a coin flip.
want() {
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
    "_why_" -- \
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

# 11. The fetch loop's export list, and the one attribute the profile says the chain also writes.
#
# `terra/batch_fetch_compare.sh` still carries its fetched-attribute list as literals (the last item of
# docs/module-profiles.md §9 step 4 that this lane could not collapse: `export` is not a profile field --
# §3 rule 5 -- so `kit/module_profile.py` refuses it as `unknown field 'export'`, and the data for it is
# not in profiles/genotyping.json either). What ships here is the GUARD, so that the literal can be
# replaced by data in one safe move later: it compares the names the script plans to fetch, read off its
# own `fetch --dry-run` output, with the attributes the module profile says the chain writes.
#
# `fetch --dry-run` touches no Terra and no bucket: it prints the plan and returns (the entity read is
# behind `if [ "$DRY_RUN" -eq 1 ]`), which is what makes this assertion machine-independent. It needs no
# dependency either -- no firecloud, no miniwdl, no checkout -- so unlike most of `make selftest` it has
# no SKIP branch at all: it either runs or the phase fails.
#
# The delta is stated, not smoothed over. Measured here, at this commit:
#   * derived from the profile -- terminal step's `this.*` outputs, `_index` siblings excluded because §3
#     rule 4 makes index closure a code rule -- 8 names;
#   * fetched as REQUIRED by the script: 7 names;
#   * the one difference: `regeno_coverage_medians`, which the script lists as OPTIONAL.
# That difference is the finding. "Required" is the script's exit code (`fetch incomplete: N required
# *<new> attribute(s) not set yet` and `return 1`), so deriving the list from the terminal step's outputs
# would make every baseline whose chain never wrote `regeno_coverage_medians` fail a fetch that succeeds
# today. That question needs a real run in hand, not a refactor whose promise was "collapse, don't
# duplicate", so the literal stays and §9 step 4 names the schema field that would let it go.
cat > "$TMP/export-guard.py" <<'PY'
"""The fetch loop's export list vs the attributes the module profile says the chain writes.

Both sides are read, never typed: the script's list comes from its own dry-run plan, the chain's from
`kit/module_profile.load()` (so `GSVTK_MODULE_DIR` moves the derived side, which is how the control below
breaks it). The suffix that turns an attribute into a workspace attribute name comes from the one
resolver both languages use, `module_profile.suffixes()`.
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
# CONTROL, and the reason the agreement above is a claim about the data rather than a paraphrase of the
# script: rename one terminal-step output IN THE PROFILE and the derived side moves while the script's
# literals cannot. On hand-typed-equality code nothing would change; here the guard fails by name.
broken rename_output rename_output=genotyping_pe_table:genotyping_pe_table_v2
want "CONTROL: renaming a terminal-step output in the profile breaks the export-list guard, by name" 1 \
    "FAIL" "genotyping_pe_table_v2" "export-list guard: " -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/guardwork2" GSVTK_MODULE_DIR="$TMP/mod-rename_output" \
    "$PY" "$TMP/export-guard.py" "$ROOT" "$TMP/guardwork2"

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
      "pick_config prefers the LIVE config over the per-submission snapshot that shares its number "
      "prefix (`sorted(matches)[0]` handed back whichever sorted first, which is what froze the wrong "
      "config in the runs this repo measured)", ", ".join(order))

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
    "fetch_baseline counted-gap guard: 8 ok, 0 failed" -- \
    env GSVTK_CONFIG="$FIXTURE" GSVTK_WORK="$TMP/baseline-work" \
    GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/" \
    "$PY" "$TMP/fetch-baseline-guard.py" "$ROOT" "$TMP/baseline-work"

printf 'profiles selftest: %s ok, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
