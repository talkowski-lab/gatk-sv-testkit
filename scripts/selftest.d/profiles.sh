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

printf 'profiles selftest: %s ok, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]
