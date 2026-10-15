#!/usr/bin/env bash
# scripts/selftest.d/jarshape.sh — the REQUIRED-input predicate, graded against what womtool
# actually prints, with no jar, no java, no network and no gatk-sv checkout.
#
#     bash scripts/selftest.d/jarshape.sh .venv/bin/python
#
# What is measured, and where the number comes from
# -------------------------------------------------
# `terra/batch_check_inputs.py::required_inputs` decides optionality out of `womtool inputs` JSON,
# with the predicate gatk-sv's own `scripts/test/terra_validation.py` uses (`if "optional" in
# womtool_inputs[inp]`). Whether that can ever be wrong is a question about ONE VERSION'S OUTPUT
# SHAPE, so it was measured at the version gatk-sv's CI pins (`.github/workflows/testwdls.yaml`
# downloads womtool-84.jar): one `womtool inputs` run per WDL over gatk-sv `7fbf1171`'s whole `wdl/`
# tree — 118 WDLs, 109 of which have a primary callable womtool can describe (the other 9 are
# task-only libraries: "Cannot convert WOM bundle to executable. No primary callable was available"),
# 4902 per-input entries — and the derived REQUIRED set compared against the miniwdl AST for every
# workflow-level input of those 109: 3255 keys compared, 3255 agreements, 0 in either direction.
#
# That sweep is also what found the shape the copied predicate was never written for:
#
#   {                                                        # verbatim `womtool inputs` on
#     "BenchmarkGqFilter.original_scores": {                 #  gatk-sv 7fbf1171 wdl/BenchmarkGqFilter.wdl
#         "label": "String", ...,
#         "pickled_scores_file": "File? (optional)" } }
#
# A struct-typed input arrives as an OBJECT of its members, not a string. `"optional" not in value`
# is a substring test on a string and a DICT-KEY test on an object, so it asks a different question
# per shape — and one of those answers is silent. Measured at womtool-84 (see the shape-matrix
# fixture below): a REQUIRED input of type `struct HasOptionalMember { String optional\n Int count }`
# emits `{"optional": "String", "count": "Int"}`, and key membership then drops that required input
# out of the required set. A required set missing an input is a check that cannot fail, and what it
# cannot fail on is a missing call-input binding that surfaces mid-submission — the exact failure
# this tool exists to catch. The marker womtool-84 really uses lives in STRINGS, as a trailing
# ` (optional)` or ` (optional, default = <expr>)`: 3140 and 396 of the 4902 entries, with zero
# entries carrying the word anywhere else in the string. So the predicate tests that marker, on
# strings, and treats any shape that cannot carry it as required — over-demanding a binding prints a
# FAIL naming the input; an empty required set prints nothing.
#
# Fixtures (scripts/selftest.d/fixtures/jar/, none of which contains a machine path)
# ----------------------------------------------------------------------------------
#   womtool84-IntegrateGDVcf-7fbf1171.inputs.json  verbatim `womtool inputs wdl/IntegrateGDVcf.wdl`
#   womtool84-IntegrateGDVcf-01107996.inputs.json  stdout at gatk-sv 7fbf1171 / 01107996. That pair IS
#                                                  the ground truth, one broken ref and its fix:
#                                                  `String sample_id` (CI rejected 7fbf1171 with
#                                                  "Required workflow input 'IntegrateGDVcf.sample_id'
#                                                  not specified") vs `String? sample_id`.
#   womtool84-BenchmarkGqFilter-7fbf1171.inputs.json  verbatim; holds the one real dict-valued entry.
#   womtool84-shapematrix.inputs.json              verbatim, but of a SYNTHETIC WDL: 12 inputs written
#                                                  to cover every value shape (required, optional,
#                                                  defaulted, Array, Map, required / optional /
#                                                  defaulted struct, struct with a member named
#                                                  `optional`). The womtool output is real; the WDL is
#                                                  not from gatk-sv. Its source sits beside it
#                                                  (`womtool84-shapematrix.wdl`) so a machine that HAS
#                                                  a jar can re-derive the capture — the one assertion
#                                                  here that is a re-run rather than a paste, and it
#                                                  prints a named, counted skip where no jar exists.
#
# Machine-independent by construction: every graded run reads a fixture through
# `GSVTK_WOMTOOL_INPUTS_JSON` (documented in `terra/batch_check_inputs.py --help` and in
# docs/config.md), so no java, no jar, no network and no checkout is touched — one assertion runs
# with `JAVA=/usr/bin/false` and an empty `PATH` to prove that claim rather than assert it. The
# refusals run the real tool end to end and must name what they want. bash 3.2 compatible; owns its
# own tally.
#
# Sections 10-12 (GitHub issue #5) grade the other two things that file does, with the same
# no-network rule and a SECOND seam, `GSVTK_ENTITY_JSON`:
#
#   10. womtool is a COUNTED SKIP, not a death. `womtool_spec()` used to `SystemExit` with neither a
#       jar nor a capture, which made checks 2, 2b, 3 and 4 unreachable — the tool answered none of its
#       questions on a machine without java. It now returns `(None, reason)` and the line printed names
#       the withheld thing, its extent and the checks that depended on it (the standard
#       `withheld by the declared skip region …` sets in scripts/selftest.d/entity.sh). One dependent
#       was a silent pass waiting to happen: check 3 scans `inputsResolutions` for the required names,
#       and with no required set it printed "PASS every required input resolved to a value" over
#       nothing. It prints SKIPPED now — and so does check 1 when a capture answers ZERO required
#       inputs, because a womtool JSON of another workflow reads exactly like a WDL needing nothing.
#   11. CHECK 2b: do the attributes this config binds actually exist? The check the issue asked for and
#       the tool never had — it has no entity read anywhere, and its `inputsResolutions` check answers
#       "did a previous run resolve these", not "do these sit on my row now". Three states
#       (PRESENT / PRESENT-EMPTY / ABSENT) plus UNKNOWN and UNGRADED; one line per reference printing
#       type/row/attr/suffix; one count line a leg asserts literally. Suffix INFERENCE is NOT built
#       here (the next lane's line, coupled to these). `this.<collection>.<attr>` member reads are
#       UNGRADED with their reason: looking a member attribute up on a cohort row is the mistake
#       terra/steps.py names for 09-MergeBatchSites.
#   12. The LIVE row read, graded offline: one point lookup per row, through the attribute-level
#       reader this repo did not have when the section was written (`terra.entity_attributes()`). The
#       five ways `GET …/entities/<type>/<name>` can answer are five scenarios, and the rules the legs
#       hold are unchanged in kind: 404 is the only answer allowed to mean absence, and anything that
#       cannot answer (a 500, a 200 with no `attributes` member, an unreadable capture) is UNKNOWN and
#       never ABSENT. The listing call stays faked ONLY as a tripwire: the CALLS leg goes red if the
#       tool ever goes back to walking pages. The 500 and the odd body are fixtures, not measured
#       Terra failures; the fiss handle here is a fixture-backed stand-in, and the raw
#       REST client of terra/terra.py is stubbed to FAIL, which is that file's documented fallback path.
#
# Fixtures for 10-12, all HAND-AUTHORED (real shapes and names, placeholder values: attribute values on
# these rows are object paths, and a sanitised live dump is still a coordinate leak):
#
#   checkinputs-GenotypeBatch-config.json                the get_workspace_config answer, 12 references
#   checkinputs-GenotypeBatch-config-member-read.json    the same + 2 `this.sample_sets.<attr>` reads
#   checkinputs-GenotypeBatch-synthetic-womtool-inputs.json  SYNTHETIC shape-match: 12 required, 3 not
#   checkinputs-GenotypeBatch-run-and-workspace.json     submissions, the submission, workspace attributes
#   checkinputs-GenotypeBatch-entity-capture.json        GSVTK_ENTITY_JSON: 8 present, 1 empty, 3 absent
#   checkinputs-GenotypeBatch-entity-capture-complete.json   the all-clean state, for the PASS leg
#   checkinputs-GenotypeBatch-entity-lookup.json         6 live scenarios: the five ways the point
#                                                        row 404, row on page 25 against a 20-page budget

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
# Same rule `make helpsweep` and scripts/selftest.d/rerun.sh use: a phase that skips because the
# caller's python3 has no firecloud while ./.venv has it is a phase that reported nothing.
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1 && [ -x .venv/bin/python ] \
   && .venv/bin/python -c 'import firecloud' >/dev/null 2>&1; then PY=.venv/bin/python; fi
# Absolute, because one assertion runs under `env -i` with an empty PATH on purpose. What was here was
# `PY="$ROOT/${PY#./}"` -- correct for exactly one of the three ways a caller can name a python, and wrong
# for the other two: an absolute path became `/checkout//Users/.../python` and a bare `python3` became
# `/checkout/python3`. Neither exists, and a python that does not exist is not a failure here, it is a
# SKIP: on a checkout with no `.venv` of its own the suite reported "1 passed, 1 skipped" while 48
# assertions had quietly not run at all.
case "$PY" in
    /*)  : ;;                                        # already absolute
    ./*) PY="$ROOT/${PY#./}" ;;
    */*) if [ -x "$PY" ]; then PY="$ROOT/$PY"; fi ;; # repo-relative without the leading ./
    *)   PY="$(command -v "$PY" 2>/dev/null || printf '%s' "$PY")" ;;
esac

TOOL="$ROOT/terra/batch_check_inputs.py"
FIX="$ROOT/scripts/selftest.d/fixtures/jar"
REQ_7F="$FIX/womtool84-IntegrateGDVcf-7fbf1171.inputs.json"
REQ_01="$FIX/womtool84-IntegrateGDVcf-01107996.inputs.json"
STRUCT_FIX="$FIX/womtool84-BenchmarkGqFilter-7fbf1171.inputs.json"
MATRIX="$FIX/womtool84-shapematrix.inputs.json"
MATRIX_WDL="$FIX/womtool84-shapematrix.wdl"
# The Terra-side fixtures (all HAND-AUTHORED: real shapes and names, placeholder values, never a
# sanitised live dump — `make -s audit` flags coordinates in tracked files, and attribute values on
# these rows are object paths). Together they let checks 2b/3/4 be graded with no network:
CI_CFG="$FIX/checkinputs-GenotypeBatch-config.json"            # the method config payload
CI_CFG_MEMBER="$FIX/checkinputs-GenotypeBatch-config-member-read.json"   # + 2 member reads
CI_WT="$FIX/checkinputs-GenotypeBatch-synthetic-womtool-inputs.json"     # synthetic, says so in the name
CI_RUN="$FIX/checkinputs-GenotypeBatch-run-and-workspace.json"  # submissions + workspace attributes
CI_CAPTURE="$FIX/checkinputs-GenotypeBatch-entity-capture.json"          # GSVTK_ENTITY_JSON: 3 states
CI_CAPTURE_FULL="$FIX/checkinputs-GenotypeBatch-entity-capture-complete.json"
CI_SUFFIX_H2H="$FIX/checkinputs-GenotypeBatch-entity-suffix-h2h.json"
CI_SUFFIX_NEW="$FIX/checkinputs-GenotypeBatch-entity-suffix-new.json"
CI_SUFFIX_BLIND="$FIX/checkinputs-GenotypeBatch-entity-absentonly.json"
CI_PAGES="$FIX/checkinputs-GenotypeBatch-entity-lookup.json"     # the live point lookup's 6 scenarios

TMP="$(mktemp -d "${TMPDIR:-/tmp}/gsvtk-jarshape.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/no-config.env"                  # an empty profile: nothing from a user's config leaks in
ok=0; fail=0; skipped=0

# ------------------------------------------------- declared vs reached (docs/gap-ledger.md C10)
# Both numbers count assertion call SITES. That is the whole reason this exists for THIS phase: jarshape is the
# file whose verdict total moves with the environment — 49 verdicts with WOMTOOL_JAR unset, 50 with a jar in
# hand — so a reached count in EXECUTIONS needed a constant this file could not state honestly, and the same
# commit would have been green on one machine and red on another. Sites tell one story in both modes,
# measured: 31 declared; 31 reached with a jar; 30 reached without one, and the missing one is printed as a
# line number beside the named skip rather than as a mismatch against a guessed constant. What a skip is allowed
# to withhold is DECLARED too, as the TEXT of the block it names (three regions: no firecloud, no miniwdl, no
# jar), so a site that did not run for some other reason is a failure by line number even in a skipping mode.
# The callees are what
# gsvtk_declared_funset derives from this file: `want`, `want_no` and `canary` — and NOT `say_ok`/`say_fail`,
# which record the verdict but are only ever called from inside `want`, so their lines are printer lines, not
# assertion sites. That distinction is derived, not asserted: a function called only from another function
# never enters the set, which is what keeps a green run from failing on a `say_fail` line that only a failing
# assertion would ever reach.
SELF="$ROOT/scripts/selftest.d/jarshape.sh"
. "$ROOT/scripts/selftest.d/declared.sh"
GSVTK_DECL_FUNCS="want want_no want_no_sub canary"
gsvtk_declared_init "$TMP"          # the ledger is the set of reached sites (bash 3.2: no associative arrays)

# --------------------------------------------------------------------------- harness
# want DESC WANT_RC NEEDLE... -- CMD...      (as scripts/selftest.d/womtool.sh: exit code AND every
# want_no DESC WANT_RC ABSENT... -- CMD...    needle, because a refusal and a crash share exit codes)
#       ...and NONE of the ABSENT needles may appear as a whole output line. This is how "that input
#       is NOT in the required set" gets asserted, which is the only way to notice an input silently
#       disappearing from it.
say_ok()   { ok=$((ok + 1)); printf '  ok    %s (exit %s)\n' "$1" "$2"; }
say_fail() { fail=$((fail + 1)); printf '  FAIL  %s (exit %s, want %s)%s\n' "$1" "$2" "$3" "$4"; }

want_no_sub() {
    # The same recorder as want_no, for needles that are PHRASES inside a line rather than a whole line:
    # want_no greps -qxF (exact whole line), which for a dynamic sentence -- a suffix-disagreement note,
    # a tally with counts in it -- can never match, so a leg written that way passes forever. Measured,
    # not assumed: the agreeing/absence legs first written against want_no stayed green with the note
    # firing on both rows. Substring match, same rc discipline.
    gsvtk_note_site "${BASH_LINENO[0]}"   # see want() above
    local desc="$1" wantrc="$2"; shift 2
    local absent=() out rc hit="" n
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do absent=("${absent[@]-}" "$1"); shift; done
    shift
    out="$("$@" 2>&1)"; rc=$?
    for n in "${absent[@]-}"; do
        [ -n "$n" ] || continue
        printf '%s' "$out" | grep -qF -- "$n" && hit="$hit
          unexpectedly present: $n"
    done
    if [ "$rc" = "$wantrc" ] && [ -z "$hit" ]; then say_ok "$desc" "$rc"; else
        say_fail "$desc" "$rc" "$wantrc" "$hit"; printf '%s\n' "$out" | head -6 | sed 's/^/          /'
    fi
}

want() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # the line that CALLED want(); one entry per call SITE, not per loop turn
    local desc="$1" wantrc="$2"; shift 2
    local needles=() out rc miss="" n
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do needles=("${needles[@]-}" "$1"); shift; done
    shift
    out="$("$@" 2>&1)"; rc=$?
    for n in "${needles[@]-}"; do
        [ -n "$n" ] || continue
        printf '%s' "$out" | grep -qF -- "$n" || miss="$miss
          missing: $n"
    done
    if [ "$rc" = "$wantrc" ] && [ -z "$miss" ]; then say_ok "$desc" "$rc"; else
        say_fail "$desc" "$rc" "$wantrc" "$miss"; printf '%s\n' "$out" | head -8 | sed 's/^/          /'
    fi
}

want_no() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see want() above
    local desc="$1" wantrc="$2"; shift 2
    local absent=() out rc hit="" n
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do absent=("${absent[@]-}" "$1"); shift; done
    shift
    out="$("$@" 2>&1)"; rc=$?
    for n in "${absent[@]-}"; do
        [ -n "$n" ] || continue
        printf '%s' "$out" | grep -qxF -- "$n" && hit="$hit
          unexpectedly present: $n"
    done
    if [ "$rc" = "$wantrc" ] && [ -z "$hit" ]; then say_ok "$desc" "$rc"; else
        say_fail "$desc" "$rc" "$wantrc" "$hit"; printf '%s\n' "$out" | head -8 | sed 's/^/          /'
    fi
}

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

# The driver imports the tool under test by path and prints what bash can grep. `required` goes
# through required_inputs(), i.e. through the same seam and the same predicate the tool uses — never
# through a re-typed copy of the predicate, which would test the copy.
cat > "$TMP/probe.py" <<'PY'
import importlib.util
import json
import os
import sys

ROOT, mode = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(ROOT, "terra"))
sys.path.insert(0, os.path.join(ROOT, "kit"))
spec = importlib.util.spec_from_file_location("batch_check_inputs",
                                              os.path.join(ROOT, "terra", "batch_check_inputs.py"))
bci = importlib.util.module_from_spec(spec)
sys.modules["batch_check_inputs"] = bci
spec.loader.exec_module(bci)

if mode == "required":
    names = sorted(bci.required_inputs(sys.argv[3], sys.argv[4]))
    print("COUNT=%s" % len(names))
    for name in names:
        print(name)
elif mode == "diff":
    # The two captures, through the tool's own predicate: which names does the pair turn on?
    def req(path):
        return bci.required_from_spec(json.load(open(path)), sys.argv[5])
    moved = req(sys.argv[3]) ^ req(sys.argv[4])
    print("DIFF=%s %s" % (len(moved), " ".join(sorted(moved))))
elif mode == "shape":
    entry = json.load(open(sys.argv[3]))[sys.argv[4]]
    print("SHAPE=%s" % ("optional" if bci.is_optional_input(entry) else "required"))
    print("KIND=%s" % ("dict" if isinstance(entry, dict) else type(entry).__name__))
elif mode == "value":
    value = json.loads(sys.argv[3])
    print("SHAPE=%s" % ("optional" if bci.is_optional_input(value) else "required"))
elif mode == "run":
    # run <pages-fixture> <scenario> [tool args...] -- call the REAL main(), with a fixture-backed
    # stand-in for the fiss handle and nothing else. No check is re-implemented here: the stand-in
    # answers HTTP and the tool decides. Three things it enforces rather than claims:
    #   * while GSVTK_ENTITY_JSON is set, no entity/workspace endpoint may be touched at all -- a tool
    #     that read the seam variable and then went to Terra anyway would print SEAM-WAS-IGNORED;
    #   * every endpoint call is counted, so the row read being memoised per (type, row) is graded
    #     (`CALLS=get_entity:1,...`) rather than promised;
    #   * the raw REST client of terra/terra.py is stubbed to FAIL, which is that file's documented
    #     fallback path (docs/terra-head-to-head.md §8), so nothing in a leg can reach a network and the
    #     notice it prints is part of the output a leg reads.
    pages_path, scenario_name = sys.argv[3], sys.argv[4]
    argv = sys.argv[5:]
    seam_set = bool(os.environ.get("GSVTK_ENTITY_JSON"))
    fixture_dir = os.path.join(ROOT, "scripts", "selftest.d", "fixtures", "jar")
    run_doc = json.load(open(os.path.join(fixture_dir,
                                         "checkinputs-GenotypeBatch-run-and-workspace.json")))
    pages = json.load(open(pages_path))["scenarios"].get(scenario_name)
    if pages is None and not seam_set:
        sys.exit("probe.py run: no scenario %r in %s" % (scenario_name, pages_path))
    if pages is None:                                 # `unused`: with the capture in hand no page of any
        pages = {"entities": [], "row_exists": True}   # seam in play: no scenario is reachable
    config_path = os.environ.get("GSVTK_TEST_CONFIG") \
        or os.path.join(fixture_dir, "checkinputs-GenotypeBatch-config.json")
    config_doc = json.load(open(config_path))
    calls = []
    terra_mod = sys.modules["terra"]

    class Resp:
        def __init__(self, payload, code=200):
            self._p, self.status_code = payload, code
            self.text = json.dumps(payload)

        def json(self):
            return self._p

    class FakeApi:
        def get_workspace_config(self, ns, name, cns, cname):
            calls.append("get_workspace_config")
            return Resp(config_doc)

        def list_submissions(self, ns, name, *a, **kw):
            calls.append("list_submissions")
            return Resp(run_doc["submissions"])

        def get_submission(self, ns, name, sid):
            calls.append("get_submission")
            return Resp(run_doc["submission"])

        def _refuse_seam(self, what):
            raise AssertionError("SEAM-WAS-IGNORED: %s ran while GSVTK_ENTITY_JSON=%s was set"
                                 % (what, os.environ.get("GSVTK_ENTITY_JSON")))

        def get_entity(self, ns, name, etype, ename):
            # The point lookup, answered five ways -- see the fixture's `_about`. This is the ONLY seam
            # the live row read uses now; `get_entities_query` below stays as a tripwire, because a tool
            # that went back to walking listings would be caught by the CALLS leg rather than by review.
            calls.append("get_entity")
            if seam_set:
                self._refuse_seam("get_entity")
            if pages.get("http"):
                return Resp({"message": pages.get("message", "upstream refused")}, pages["http"])
            if not pages.get("row_exists"):
                return Resp({"message": "no such row"}, 404)   # the only answer allowed to mean absence
            for e in pages.get("entities", []):
                if e.get("entityName") != ename:
                    continue
                attrs = e.get("attributes", {})
                if pages.get("body") == "no-attributes":
                    return Resp({"entityName": ename, "entityType": etype})
                if pages.get("nested"):                        # the envelope shape (terra.py's rule)
                    return Resp({"entity": {"name": ename, "attributes": attrs}})
                return Resp({"entityName": ename, "entityType": etype, "attributes": attrs})
            return Resp({"message": "no such row"}, 404)

        def get_entities_query(self, ns, name, etype, page=1, page_size=50, **kw):
            calls.append("get_entities_query")
            if seam_set:
                self._refuse_seam("the entity listing")
            items = [e for e in pages["entities"] if e.get("page") == page]
            fill = pages.get("fill_full") or []
            if fill == "*" or str(page) in fill:
                n = 0
                while len(items) < page_size:
                    n += 1
                    items.append({"entityName": "EXAMPLE_FILLER_%s_%s" % (page, n),
                                  "entityType": etype, "attributes": {}})
            return Resp({"entities": items,
                         "resultMetadata": {"totalPages": 999, "total": 99999}})

        def get_workspace(self, ns, name, fields=None):
            calls.append("get_workspace")
            if seam_set:
                self._refuse_seam("the workspace read")
            return Resp(run_doc["workspace"])

    fake = FakeApi()
    bci.fapi = fake
    terra_mod.fapi = fake

    def no_rest(*a, **kw):
        raise terra_mod.TerraError("offline leg: the raw REST client is stubbed out")

    terra_mod.rest_get = no_rest
    sys.argv = ["batch_check_inputs.py"] + list(argv)
    rc = bci.main()
    tally = {}
    for name in calls:
        tally[name] = tally.get(name, 0) + 1
    print("CALLS=" + ",".join("%s:%s" % (k, tally[k]) for k in sorted(tally)))
    print("RC=%s" % rc)
    sys.exit(rc)
else:
    sys.exit("probe.py: unknown mode %r" % mode)
PY

probe() { "$PY" "$TMP/probe.py" "$ROOT" "$@"; }

# ci [EXTRA=ENV ...] <womtool-seam|none> <entity-capture|none> <config-fixture|default> <scenario> [args]
#
# Run the REAL `main()` against the fixture-backed fiss stand-in in probe.py's `run` mode. Every leg
# states its own seams, because the difference between them is what is being graded: `none` for the
# entity capture means "this run read the live page fixture", and `unused` as the scenario then means
# exactly that -- no page scenario is reachable while the capture answers.
# `GSVTK_CONFIG` points at a profile holding placeholder coordinates, so `tc.NS`/`tc.WS`/`tc.BATCH`
# resolve to names this file can print without printing anybody's workspace.
ci() {
    local -a extra=()
    while [ "$#" -gt 0 ]; do
        case "$1" in *=*) extra+=("$1"); shift ;; *) break ;; esac
    done
    local wt="$1" ent="$2" cfg="$3" scen="$4"; shift 4
    local -a unsets=() assigns=()
    # `env` stops option-parsing at the first VAR=value, so every -u goes before every assignment.
    unsets+=(-u WOMTOOL_JAR)
    if [ "$wt" = none ]; then unsets+=(-u GSVTK_WOMTOOL_INPUTS_JSON); else assigns+=("GSVTK_WOMTOOL_INPUTS_JSON=$wt"); fi
    if [ "$ent" = none ]; then unsets+=(-u GSVTK_ENTITY_JSON); else assigns+=("GSVTK_ENTITY_JSON=$ent"); fi
    if [ "$cfg" = default ]; then unsets+=(-u GSVTK_TEST_CONFIG); else assigns+=("GSVTK_TEST_CONFIG=$cfg"); fi
    assigns+=("GSVTK_CONFIG=$TMP/checkinputs.env")
    env ${unsets[@]+"${unsets[@]}"} ${assigns[@]+"${assigns[@]}"} ${extra[@]+"${extra[@]}"} \
        "$PY" "$TMP/probe.py" "$ROOT" run "$CI_PAGES" "$scen" --step 10 --no-sidecars "$@"
}

# The placeholder coordinates the legs print. Deliberately the `your-*` convention
# scripts/selftest.d/golden/ already uses, so nothing here is a machine coordinate.
cat > "$TMP/checkinputs.env" <<EOF
GSVTK_TERRA_NAMESPACE=your-namespace
GSVTK_TERRA_WORKSPACE=your-workspace
GSVTK_BATCH=EXAMPLE_SET_A
GSVTK_BRANCH=your-branch-under-test
EOF

# --------------------------------------------------------------------------- the phase
echo
echo "selftest: the womtool optionality predicate, graded against captured womtool-84 output"
canary
# Everything below this line is behind the firecloud bail: no import, no assertions, exit 0. Declared as a
# region so the pair still runs on that path (it used to `exit 0` before reaching the reporter, which is a skip
# that withholds the accounting too), and so an assertion written ABOVE this line — the canary — is still
# required to have run even when the bail fires.
gsvtk_guard_to_end "jarshape-no-firecloud"
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1; then
    printf '  SKIP  jarshape: %s cannot import firecloud, which terra/batch_check_inputs.py imports (python -m pip install -r requirements.txt)\n' "$PY"
    skipped=$((skipped + 1))
    gsvtk_note_skip "jarshape-no-firecloud"
    printf '\njarshape selftest: %s passed, %s failed, %s skipped (a SKIP here is not a pass in CI)\n' \
        "$ok" "$fail" "$skipped"
    # The pair runs here as everywhere else: a bail is the mode where an unreachable assertion is hardest to
    # see, so it is the mode that must not skip the guard. What it cannot do is see a site hidden INSIDE the
    # bailed region — see the header of scripts/selftest.d/declared.sh.
    gsvtk_declared_report "jarshape selftest" "$SELF" "$((ok + fail))" "$skipped" || fail=$((fail + 1))
    [ "$fail" -eq 0 ] || exit 1
    exit 0
fi
for f in "$REQ_7F" "$REQ_01" "$STRUCT_FIX" "$MATRIX" "$MATRIX_WDL" \
         "$FIX/womtool84-validate-not-specified.txt" "$FIX/womtool84-validate-coercion.txt" \
         "$FIX/womtool84-validate-extra-key.txt" "$FIX/womtool84-validate-value-and-missing.txt" \
         "$CI_CFG" "$CI_CFG_MEMBER" "$CI_WT" "$CI_RUN" "$CI_CAPTURE" "$CI_CAPTURE_FULL" "$CI_PAGES"; do
    if [ ! -f "$f" ]; then
        printf '  FAIL  the fixture %s is missing; nothing below this line can be graded\n' "${f#"$ROOT"/}"
        printf '\njarshape selftest: 0 passed, 1 failed, 0 skipped\n'
        exit 1
    fi
done

# 1. THE GROUND-TRUTH PAIR: real womtool-84 stdout from two real gatk-sv commits, one broken and one
#    fixed. `sample_id` must come out REQUIRED at 7fbf1171 — the commit CI rejected with "Required
#    workflow input 'IntegrateGDVcf.sample_id' not specified" — and must NOT at 01107996, where the
#    declaration is `String?`. A predicate wrong on either half of this pair cannot be trusted on the
#    money path, and a suite that only asserts the first half cannot tell a working predicate from one
#    that calls everything required.
want "gatk-sv 7fbf1171: sample_id is classified REQUIRED" 0 \
    "COUNT=11" "sample_id" "vcf" "contig_list" -- \
    env -u WOMTOOL_JAR JAVA=/usr/bin/false GSVTK_WOMTOOL_INPUTS_JSON="$REQ_7F" \
        "$PY" "$TMP/probe.py" "$ROOT" required wdl/IntegrateGDVcf.wdl IntegrateGDVcf
want_no "gatk-sv 01107996: the same predicate classifies sample_id NOT required" 0 \
    "sample_id" -- \
    env -u WOMTOOL_JAR JAVA=/usr/bin/false GSVTK_WOMTOOL_INPUTS_JSON="$REQ_01" \
        "$PY" "$TMP/probe.py" "$ROOT" required wdl/IntegrateGDVcf.wdl IntegrateGDVcf
want "and at 01107996 the set is one name smaller, that name" 0 "COUNT=10" -- \
    env -u WOMTOOL_JAR GSVTK_WOMTOOL_INPUTS_JSON="$REQ_01" \
        "$PY" "$TMP/probe.py" "$ROOT" required wdl/IntegrateGDVcf.wdl IntegrateGDVcf
want "the two captures turn on exactly that one input" 0 "DIFF=1 sample_id" -- \
    "$PY" "$TMP/probe.py" "$ROOT" diff "$REQ_7F" "$REQ_01" IntegrateGDVcf

# 2. THE MARKER, ENTRY BY ENTRY, from the 7fbf1171 capture: what womtool-84 writes for a plain
#    required input, an Array, an `X?` input, a defaulted task input, and an optional struct (which
#    arrives as a STRING with the marker, not as members).
for key_shape in \
        "IntegrateGDVcf.vcf:required" \
        "IntegrateGDVcf.gd_output_tarballs:required" \
        "IntegrateGDVcf.integrate_args:optional" \
        "IntegrateGDVcf.ConcatVcfs.allow_overlaps:optional" \
        "IntegrateGDVcf.runtime_attr_override_prepare:optional"; do
    key="${key_shape%:*}"; want_shape="${key_shape##*:}"
    want "captured entry ${key##*.} is $want_shape" 0 "SHAPE=$want_shape" -- \
        probe shape "$REQ_7F" "$key"
done

# 3. THE DICT-VALUED ENTRY — the reason this file exists. A struct-typed input, required in the WDL,
#    emitted as its members, one of which carries "(optional)" in its OWN type string. Reading the
#    members is what breaks: the members are not the input.
want "captured dict-valued struct entry is dict-shaped and required" 0 \
    "KIND=dict" "SHAPE=required" -- \
    probe shape "$STRUCT_FIX" "BenchmarkGqFilter.original_scores"
want "the required struct reaches the required set" 0 \
    "COUNT=6" "original_scores" "comparison_scores" -- \
    env -u WOMTOOL_JAR GSVTK_WOMTOOL_INPUTS_JSON="$STRUCT_FIX" \
        "$PY" "$TMP/probe.py" "$ROOT" required wdl/BenchmarkGqFilter.wdl BenchmarkGqFilter
want_no "and a defaulted Array[String] input stays out of it" 0 "benchmark_args" -- \
    env -u WOMTOOL_JAR GSVTK_WOMTOOL_INPUTS_JSON="$STRUCT_FIX" \
        "$PY" "$TMP/probe.py" "$ROOT" required wdl/BenchmarkGqFilter.wdl BenchmarkGqFilter

# 4. THE SHAPE MATRIX (measured at womtool-84). The first two lines are the direction that cannot
#    fail loudly, and they are the whole finding: the struct's member is literally named `optional`,
#    so the copied predicate's `"optional" not in value` matched a DICT KEY and the required input
#    vanished from the required set.
want "REQUIRED struct whose member is named 'optional' stays REQUIRED" 0 \
    "KIND=dict" "SHAPE=required" -- probe shape "$MATRIX" "ShapeProbe.named_optional_member_req"
want "and it is in the derived required set, not silently dropped" 0 \
    "COUNT=8" "named_optional_member_req" -- \
    env -u WOMTOOL_JAR GSVTK_WOMTOOL_INPUTS_JSON="$MATRIX" \
        "$PY" "$TMP/probe.py" "$ROOT" required ShapeProbe.wdl ShapeProbe
for key_shape in \
        "ShapeProbe.plain_req:required" \
        "ShapeProbe.plain_opt:optional" \
        "ShapeProbe.defaulted:optional" \
        "ShapeProbe.file_req:required" \
        "ShapeProbe.array_req:required" \
        "ShapeProbe.array_opt:optional" \
        "ShapeProbe.map_req:required" \
        "ShapeProbe.struct_req:required" \
        "ShapeProbe.struct_opt:optional" \
        "ShapeProbe.array_of_struct_req:required"; do
    key="${key_shape%:*}"; want_shape="${key_shape##*:}"
    want "captured entry ${key##*.} is $want_shape" 0 "SHAPE=$want_shape" -- \
        probe shape "$MATRIX" "$key"
done
#    PINNED LIMITATION, not a silent one: at womtool-84 a DEFAULTED struct input is emitted exactly
#    like a required one — an object of members, marker nowhere — so it counts as required. Measured
#    cost on gatk-sv 7fbf1171: 114 defaulted workflow-level inputs, ZERO of them struct-typed, so
#    nothing is over-demanded there today; and the day something is, the answer is a FAIL naming an
#    input, which is the loud direction this check chooses on purpose.
want "a defaulted struct is indistinguishable from a required one (pinned limitation)" 0 \
    "KIND=dict" "SHAPE=required" -- probe shape "$MATRIX" "ShapeProbe.struct_defaulted"

# 5. NO LOOSER THAN THE MEASURED MARKER, and never on a member name. The first line is the shape the
#    gap ledger itself feared — an `optional` KEY whose value is false — which is not what womtool-84
#    emits, and is refused by the same rule.
for value_shape in \
        '{"optional": false}:required' \
        '{"optional": "String"}:required' \
        '{"pickled_scores_file": "File? (optional)"}:required' \
        '"String optional":required' \
        '"String (optional)":optional' \
        '"Array[WomCompositeType { x -> Int }] (optional, default = [])":optional' \
        '"Array[File]":required'; do
    value="${value_shape%:*}"; want_shape="${value_shape##*:}"
    want "value ${value:0:34} is $want_shape" 0 "SHAPE=$want_shape" -- probe value "$value"
done

# 6. THE SEAM REALLY BYPASSES JAVA AND THE JAR. Not a claim about a code path: PATH is empty and
#    JAVA is /usr/bin/false, so anything that shelled out would fail this assertion instead of the
#    fixture answering 11 required inputs.
mkdir -p "$TMP/emptybin"
want "captured JSON is graded with no jar, no java, no PATH and no checkout" 0 \
    "COUNT=11" "sample_id" -- \
    env -i PATH="$TMP/emptybin" JAVA=/usr/bin/false GSVTK_WOMTOOL_INPUTS_JSON="$REQ_7F" \
        "$PY" "$TMP/probe.py" "$ROOT" required wdl/IntegrateGDVcf.wdl IntegrateGDVcf

# 7. REFUSALS, END TO END, BY NAME. These run the real tool: with nothing to read the required set
#    from, it stops at check 1 and names what it wants — never answering with a guessed set, and
#    never reaching Terra to find out. The seam is not a way to guess either: pointed at a file that
#    is not there, or at JSON that is not a womtool object, it refuses and names the variable.
want "no jar, no seam: check 1 no longer kills the run — it names the skip, and the Terra-side checks run" 0 \
    "# SKIP womtool (check 1): no womtool jar (WOMTOOL_JAR=unset)" "GSVTK_WOMTOOL_INPUTS_JSON" \
    "CALLS=get_submission:1,get_workspace_config:1,list_submissions:1" -- \
    ci none "$CI_CAPTURE_FULL" default unused
want "a WOMTOOL_JAR that is not a file is the same named skip, quoting the path it tried" 0 \
    "no womtool jar (WOMTOOL_JAR='" "is not a file" \
    'and no captured `womtool inputs` JSON (GSVTK_WOMTOOL_INPUTS_JSON unset)' -- \
    ci WOMTOOL_JAR="$TMP/no-such-dir/womtool.jar" none "$CI_CAPTURE_FULL" default unused
want "the seam pointed at a missing file refuses instead of guessing" 1 \
    "GSVTK_WOMTOOL_INPUTS_JSON" "is not a file" "no required-input set is being guessed" -- \
    env -u WOMTOOL_JAR GSVTK_CONFIG="$TMP/no-config.env" \
        GSVTK_WOMTOOL_INPUTS_JSON="$TMP/not-here.json" "$PY" "$TOOL" --step 10
printf '[1, 2]\n' > "$TMP/not-an-object.json"
want "the seam pointed at JSON that is not a womtool object refuses, naming the shape" 1 \
    "GSVTK_WOMTOOL_INPUTS_JSON" "does not hold a JSON object" -- \
    env -u WOMTOOL_JAR GSVTK_CONFIG="$TMP/no-config.env" \
        GSVTK_WOMTOOL_INPUTS_JSON="$TMP/not-an-object.json" "$PY" "$TOOL" --step 10
want "the knob is documented in the tool's own --help" 0 \
    "GSVTK_WOMTOOL_INPUTS_JSON=<file>" "no jar, no java" "scripts/selftest.d/jarshape.sh" -- \
    env -u WOMTOOL_JAR -u GSVTK_WOMTOOL_INPUTS_JSON "$PY" "$TOOL" --help

# 8. THE JAR'S OTHER OUTPUT: `womtool validate` words, and which of them are comparable with the
#    key-set layer. All three messages are fixtures now, each one VERBATIM womtool-84 output on gatk-sv
#    `01107996` / `7fbf1171`: the missing-required one, the value-coercion one (every required key
#    present), and the extra-key one —
#
#      WARNING: Unexpected input provided: IntegrateGDVcf.this_key_does_not_exist_in_the_wdl
#
#    which is what happens when an input file names a key the WDL never declared. That third one is the
#    offline layer's real blind spot (it compares required keys INTO the JSON and never the JSON's keys
#    back into the WDL), and it is measured rather than supposed: the capture came from adding one bogus
#    key to a real rendered test JSON and re-running CI's command. Measured cost of the gap on these
#    two refs: 0 extra keys across all 29 Terra workflow_configurations and both rendered IntegrateGDVcf
#    test JSONs.
msg_keys="$FIX/womtool84-validate-not-specified.txt"
msg_value="$FIX/womtool84-validate-coercion.txt"
msg_extra="$FIX/womtool84-validate-extra-key.txt"

if "$PY" -c 'import WDL' >/dev/null 2>&1; then
    gsvtk_guard_sites "jarshape-no-miniwdl"     # every assertion in here is behind the miniwdl skip below
    cat > "$TMP/classify.py" <<'PY'
import importlib.util
import os
import sys

ROOT, path_to_msg = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(ROOT, "checks"))
sys.path.insert(0, os.path.join(ROOT, "kit"))
spec = importlib.util.spec_from_file_location(
    "wdl_inputs_check", os.path.join(ROOT, "checks", "wdl_inputs_check.py"))
wic = importlib.util.module_from_spec(spec)
sys.modules["wdl_inputs_check"] = wic
spec.loader.exec_module(wic)
text = open(path_to_msg).read()
print("CLASS=%s" % wic.rejection_class(text))
PY
    want "the measured 'not specified' rejection is the key-presence question" 0 "CLASS=keys" -- \
        "$PY" "$TMP/classify.py" "$ROOT" "$msg_keys"
    want "the measured coercion rejection is NOT, and is not mistaken for it" 0 "CLASS=value" -- \
        "$PY" "$TMP/classify.py" "$ROOT" "$msg_value"
    want "the measured extra-key rejection is its own class, not keys and not value" 0 \
        "CLASS=extras" -- "$PY" "$TMP/classify.py" "$ROOT" "$msg_extra"
    #    Measured on a Terra config with BOTH problems at once (this capture is womtool-84's whole
    #    3-line answer: two value-coercion lines and one `not specified` line, because womtool prints
    #    every problem it finds rather than the first). The mixed pair must classify as the comparable
    #    class, or a missing required input could hide behind a value error and the gate would report
    #    "out-of-layer" over the one finding it is supposed to make.
    want "a pair rejected for a value AND a missing input still classifies as the key question" 0 \
        "CLASS=keys" -- "$PY" "$TMP/classify.py" "$ROOT" \
        "$FIX/womtool84-validate-value-and-missing.txt"

    #    And the branch each decision drives, end to end, with no jar: the same tree, the same input
    #    JSON, the offline layer clean, and only womtool's WORDS changing. A value-class rejection must
    #    print OUT-OF-LAYER and not DISAGREES; an extra-key one must name the gap; a key-class one must
    #    still be called a disagreement.
    mkdir -p "$TMP/tree" "$TMP/in/inputs/build/ref_panel_1kg/test/Wad" "$TMP/bin"
    cat > "$TMP/tree/Wad.wdl" <<'WDL'
version 1.0

workflow Wad {
  input {
    File vcf
  }
  call T { input: vcf = vcf }
  output { File out = T.out }
}

task T {
  input { File vcf }
  command <<< echo hi > out.txt >>>
  output { File out = "out.txt" }
  runtime { docker: "ubuntu:22.04" }
}
WDL
    printf '{\n  "Wad.vcf": "gs://somewhere/x.vcf.gz"\n}\n' \
        > "$TMP/in/inputs/build/ref_panel_1kg/test/Wad/Wad.json"
    cat > "$TMP/bin/java" <<'STUB'
#!/bin/sh
# Stands in for `java -jar womtool.jar validate <wdl> -i <json>`: prints the message it is handed and
# exits with the rc it is handed. The message files are verbatim womtool-84 output, so the words this
# layer has to sort are the real ones while nothing here needs java.
while [ $# -gt 0 ]; do
    case "$1" in -jar) jar="$2"; shift 2;; *) shift;; esac
done
[ -f "$jar" ] || { echo "Error: Unable to access jarfile $jar"; exit 1; }
[ -n "${WOMTOOL_STUB_MSG:-}" ] && [ -f "$WOMTOOL_STUB_MSG" ] && cat "$WOMTOOL_STUB_MSG"
echo "Validation successful"
exit "${WOMTOOL_STUB_RC:-1}"
STUB
    chmod +x "$TMP/bin/java"
    : > "$TMP/fake-womtool.jar"
    run_cls() {
        env GSVTK_CONFIG="$TMP/no-config.env" GSVTK_WORK="$TMP/work" \
            WOMTOOL_JAR="$TMP/fake-womtool.jar" JAVA="$TMP/bin/java" \
            WOMTOOL_STUB_MSG="$1" WOMTOOL_STUB_RC="${2:-1}" \
            "$PY" "$ROOT/checks/wdl_inputs_check.py" \
            --wdl-dir "$TMP/tree" --inputs-root "$TMP/in" --wf Wad
    }
    want_no "a value-class womtool rejection is not called a DISAGREES" 1 \
        "DISAGREES" -- run_cls "$msg_value" 1
    want "and is reported as out-of-layer, still failing, still quoting womtool's own words" 1 \
        "OUT-OF-LAYER" "Does not perform" "No coercion defined from" \
        "GSVTK-WOMTOOL wf=Wad status=RUN pairs=1 failures=1" -- run_cls "$msg_value" 1
    want_no "an extra-key rejection is neither a DISAGREES nor out-of-layer" 1 \
        "DISAGREES" "OUT-OF-LAYER" -- run_cls "$msg_extra" 1
    want "and names the gap it falls through, quoting womtool's own words" 1 \
        "EXTRAS-GAP" "Unexpected input provided" "does not ask" "Unexpected input" \
        "GSVTK-WOMTOOL wf=Wad status=RUN pairs=1 failures=1" -- run_cls "$msg_extra" 1
    want "a key-class womtool rejection against a clean key set IS still a DISAGREES" 1 \
        "DISAGREES" "start there" -- run_cls "$msg_keys" 1
    want_no "and womtool passing a clean key set prints neither of those verdicts" 0 \
        "OUT-OF-LAYER" "DISAGREES" -- run_cls "$msg_keys" 0
    gsvtk_guard_sites_end "jarshape-no-miniwdl"
else
    printf '  SKIP  jarshape: %s cannot import miniwdl (WDL), so womtool-84 validate output was not classified (python -m pip install -r requirements-dev.txt)\n' "$PY"
    skipped=$((skipped + 1))
    gsvtk_note_skip "jarshape-no-miniwdl"
fi

# 9. WITH A JAR IN HAND, THE CAPTURE IS A CAPTURE: re-run womtool on the shape-matrix WDL and require
#    the parsed JSON to equal what is checked in, so a fixture that drifted from the tool is caught
#    rather than trusted. Where there is no jar this is a NAMED, COUNTED SKIP, because a check that
#    could not run is not a check that passed.
jar="${WOMTOOL_JAR:-}"
java_bin="${JAVA:-java}"
if [ -n "$jar" ] && [ -f "$jar" ] && command -v "$java_bin" >/dev/null 2>&1; then
    gsvtk_guard_sites "jarshape-no-jar"         # the ONE site this file withholds when no jar is in hand
    want "the shape-matrix capture reproduces with the jar in hand" 0 "MATCH 12 entries" -- \
        "$PY" -c '
import json, os, subprocess, sys
java, jar, wdl, fixture = sys.argv[1:5]
run = subprocess.run([java, "-jar", jar, "inputs", wdl], capture_output=True, text=True,
                     cwd=os.path.dirname(wdl))
got, want = json.loads(run.stdout), json.load(open(fixture))
if got == want:
    print("MATCH %s entries" % len(want))
else:
    for key in sorted(set(got) | set(want)):
        if got.get(key) != want.get(key):
            print("DIFF %s: got %r want %r" % (key, got.get(key), want.get(key)))
    sys.exit(1)
' "$java_bin" "$jar" "$MATRIX_WDL" "$MATRIX"
    gsvtk_guard_sites_end "jarshape-no-jar"
else
    printf '  SKIP  jarshape: WOMTOOL_JAR=%s, so the shape-matrix capture was not re-derived from the jar — every graded assertion above ran without java\n' "${jar:-unset}"
    skipped=$((skipped + 1))
    gsvtk_note_skip "jarshape-no-jar"
fi

# =============================================================================================
# 10. WOMTOOL IS A COUNTED SKIP, NOT A DEATH (GitHub issue #5, the half of it that was right).
#
# `womtool_spec()` used to `raise SystemExit` when WOMTOOL_JAR was empty and no capture was named, so
# every later check in terra/batch_check_inputs.py was unreachable on a machine without a jar --
# including check 2b below, which needs no jar at all. A missing optional TOOL is a withheld input, and
# this repo's standard for one is `withheld by the declared skip region …` (scripts/selftest.d/entity.sh):
# name the withheld thing, name its extent, name what depended on it. The dependents are the part that
# used to be invisible in a NEW way: with the required set gone, check 1's "every required input is
# bound" and check 3's "every required input resolved to a value" were both true of NOTHING, so the skip
# line names check 3 and check 3 prints SKIPPED instead of a PASS over an empty list.
#
# What did NOT move: a seam that is CONFIGURED AND BROKEN still refuses (section 7 above), and no
# required set is ever invented. These legs drive the tool's own main() through the fixture-backed fiss
# stand-in in probe.py's `run` mode; the real HTTP path is not exercised by any leg here (offline gate),
# and says so.

ci_values_leak() {          # rc 0 only when the run printed no attribute VALUE anywhere
    local out
    out="$(ci "$@")"
    if printf '%s\n' "$out" | grep -qF 'EXAMPLE-PLACEHOLDER'; then
        printf '%s\n' "$out" | grep -F 'EXAMPLE-PLACEHOLDER' | head -3
        return 1
    fi
    printf 'NO-VALUES-PRINTED\n'
}

want "no jar, no seam: the skip names what was withheld and its extent" 0 \
    "# SKIP womtool (check 1): no womtool jar (WOMTOOL_JAR=unset)" \
    "Withheld: the REQUIRED set of wdl/GenotypeBatch.wdl" \
    "14 bound input(s) were NOT cross-checked for a missing required binding" -- \
    ci none "$CI_CAPTURE_FULL" default unused
want "and names the check that depended on it, rather than passing over an empty required set" 0 \
    "check 3's unresolved scan has no required set to scan" \
    "SKIP womtool's required set is withheld (check 1 above)" -- \
    ci none "$CI_CAPTURE_FULL" default unused
want_no "a withheld required set prints no PASS in check 1" 0 \
    "  PASS every required input is bound" -- \
    ci none "$CI_CAPTURE_FULL" default unused
want_no "and no PASS in check 3 either (a scan of nothing is the silent pass this file exists against)" 0 \
    "  PASS every required input resolved to a value" -- \
    ci none "$CI_CAPTURE_FULL" default unused
want "the skip names both cures by variable" 0 \
    "WOMTOOL_JAR=/path/to/womtool-" "GSVTK_WOMTOOL_INPUTS_JSON=<captured" -- \
    ci none "$CI_CAPTURE_FULL" default unused
want "with a capture, check 1 still prints where its JSON came from" 0 \
    "[source: captured womtool JSON $CI_WT (GSVTK_WOMTOOL_INPUTS_JSON)]" \
    "required by WOM: 12" "  PASS every required input is bound" \
    "  PASS every required input resolved to a value" -- \
    ci "$CI_WT" "$CI_CAPTURE_FULL" default unused
want "a capture whose prefix matches nothing is refused a PASS, and prints the file it read" 0 \
    "SKIP womtool answered, but reports NO required input for GenotypeBatch" \
    "[source: captured womtool JSON $REQ_01" \
    "  SKIP womtool's required set is empty (check 1 above)" -- \
    ci "$REQ_01" "$CI_CAPTURE_FULL" default unused
want_no "an empty required set does not print PASS in check 1 (a vacuous PASS is the point of this file)" 0 \
    "  PASS every required input is bound" -- \
    ci "$REQ_01" "$CI_CAPTURE_FULL" default unused
want "the new knob is documented in the tool's own --help, with its never-a-live-run promise" 0 \
    "GSVTK_ENTITY_JSON=<file>" "never mistaken for a live one" "Values are never printed" -- \
    env -u WOMTOOL_JAR -u GSVTK_ENTITY_JSON "$PY" "$TOOL" --help

# =============================================================================================
# 11. CHECK 2b: DO THE ATTRIBUTES THIS CONFIG BINDS ACTUALLY EXIST?
#
# The check the tool never had. `womtool inputs` answers what the WDL needs; `inputsResolutions` answers
# what a PAST submission resolved -- which is "did a previous run resolve these", not "do these
# attributes exist on my row now". There was no entity read anywhere in the file (verified: the only
# `fapi.get_entity(` in the repo sits behind terra.entity_exists(), with no caller reading attributes),
# and the absence of this check is what produced issue #4. Three states, three counts, printed on every
# run, with the entity type / row name / suffix on each reference line.
#
# Graded through GSVTK_ENTITY_JSON (the captured-read seam, same precedent as
# GSVTK_WOMTOOL_INPUTS_JSON) and, in section 12, through the live readers. No leg prints a value: the
# placeholder below is what a leaked value would look like, and one leg's whole job is its absence.

want "the three states are three counts, and the count line is a literal" 1 \
    "# attribute existence: refs=12 graded=12 present=8 present-empty=1 absent=3 unknown=0 ungraded=0" \
    "RC=1" -- \
    ci none "$CI_CAPTURE" default unused
want "an ABSENT line names the type, the row and the suffix in play" 1 \
    "type=sample_set row=EXAMPLE_SET_A attr=this.cutoffs_new suffix=_new" \
    "attr=this.outlier_filtered_pesr_vcf_new suffix=_new" -- \
    ci none "$CI_CAPTURE" default unused
want "PRESENT-BUT-EMPTY is its own word, its own count and its own FAIL" 1 \
    "state=PRESENT-EMPTY" "attr=this.median_cov_frz suffix=_frz" \
    "PRESENT BUT EMPTY there" -- \
    ci none "$CI_CAPTURE" default unused
want "ABSENT from the WORKSPACE is graded too, on the workspace row" 1 \
    "type=workspace row=your-workspace attr=workspace.bin_exclude suffix=-" -- \
    ci none "$CI_CAPTURE" default unused
want "the FAIL lines name the attributes, so a reader can act without reading 12 lines" 1 \
    "FAIL 3 bound attribute(s) are ABSENT from the row/workspace that answers them" \
    "this.cutoffs_new, this.outlier_filtered_pesr_vcf_new, workspace.bin_exclude" -- \
    ci none "$CI_CAPTURE" default unused
want "a captured run prints the capture as its source (never readable as a live answer)" 1 \
    "#   attribute source: captured Terra read $CI_CAPTURE (GSVTK_ENTITY_JSON)" \
    "NOT a live read" -- \
    ci none "$CI_CAPTURE" default unused
want "and the captured run touched NO entity endpoint: the seam was read, not ignored" 0 \
    "CALLS=get_submission:1,get_workspace_config:1,list_submissions:1" -- \
    ci "$CI_WT" "$CI_CAPTURE_FULL" default unused
want "no attribute VALUE is printed on a captured run (they are object paths; the audit flags them)" 0 \
    "NO-VALUES-PRINTED" -- \
    ci_values_leak "$CI_WT" "$CI_CAPTURE_FULL" default unused
want "a healthy row prints PASS and exits 0 -- the check can distinguish clean from not-run" 0 \
    "PASS every one of the 12 graded reference(s) resolves to a non-empty value" \
    "absent=0 unknown=0" "RC=0" -- \
    ci "$CI_WT" "$CI_CAPTURE_FULL" default unused
want "the ROW NAME is load-bearing: the same config on the other row is absent=1, not absent=3" 1 \
    "# attribute existence: refs=12 graded=12 present=11 present-empty=0 absent=1 unknown=0 ungraded=0" \
    "row=EXAMPLE_SET_B" -- \
    ci none "$CI_CAPTURE" default unused --entity EXAMPLE_SET_B
want "a row the capture says nothing about is UNKNOWN, and no PASS is claimed" 1 \
    "unknown=8" "the capture says nothing about this row" \
    "attribute existence does not claim PASS: 8 of 12" -- \
    ci none "$CI_CAPTURE" default unused --entity EXAMPLE_SET_ZED
want_no "a withheld reference is never counted as a pass" 1 \
    "  PASS every one of the 12 graded reference(s) resolves to a non-empty value on the row/workspace it is read from" -- \
    ci none "$CI_CAPTURE" default unused --entity EXAMPLE_SET_ZED
want "a MEMBER READ (this.sample_sets.<attr>) is UNGRADED with its reason, not looked up on the wrong row" 0 \
    "type=sample_sets row=members of EXAMPLE_SET_A attr=this.sample_sets.cutoffs_frz" \
    "member read: the value sits on each row of the collection, not on this row" \
    "ungraded=2" -- \
    ci "$CI_WT" "$CI_CAPTURE_FULL" "$CI_CFG_MEMBER" unused
want_no "and 2 member reads keep the PASS off the line even when all 12 graded ones are clean" 0 \
    "  PASS every one of the 12 graded reference(s) resolves to a non-empty value on the row/workspace it is read from" -- \
    ci "$CI_WT" "$CI_CAPTURE_FULL" "$CI_CFG_MEMBER" unused
want "a step whose profile names no row source says so and guesses nothing (09 is a COHORT row)" 1 \
    "row type=sample_set_set" \
    "no row name: --entity did not name one and this step's profile names no row source at all" \
    "no submission found for 09-MergeBatchSites" -- \
    ci "$CI_WT" "$CI_CAPTURE" default unused --step 09
want "and check 2b ran BEFORE that refusal: it answers a question about the row, not about a past run" 1 \
    "# check 2b: do the attributes 10-GenotypeBatch binds actually exist" \
    "no submission found for 09-MergeBatchSites" -- \
    ci "$CI_WT" "$CI_CAPTURE" default unused --step 09

# =============================================================================================
# 12. THE LIVE ROW READ, GRADED OFFLINE: what a lookup that cannot answer refuses to say.
#
# The LIVE row read: one point lookup, and the five ways its endpoint can answer.
#
# `row_attributes_live()` calls `terra.entity_attributes()`, which wraps `GET .../entities/<type>/<name>`
# -- one request, the row's attributes, `None` for a 404, and a raise for anything else. It replaced a
# walk of `terra.entity_sample()` pages, which existed only because terra/terra.py had no attribute-level
# reader at the time; the walk is gone rather than tuned, and the CALLS leg below is the proof rather
# than this sentence (a tool that went back to listings would go red there, and `get_entities_query`
# remains faked only as the tripwire that makes that leg mean something).
#
# What the legs hold, in the order the ladder runs:
#   * a row that EXISTS answers for itself: 8 present, 1 present-but-empty, 3 absent. ABSENT is only
#     ever said about a name on a row that was read;
#   * the same bytes nested under `entity` must tally IDENTICALLY. That is `a21cb3b`'s lesson enforced in
#     code, not remembered in prose: a report of "no attributes" is usually someone reading one level
#     too shallow, so both shapes are read, and a 200 with neither raises instead of returning `{}`;
#   * 404, HTTP 500, and a 200 with no `attributes` member all land on UNKNOWN with the SAME tally and
#     three DIFFERENT sentences. Collapsing them is how a check starts manufacturing missing-input
#     reports -- issue #4 verbatim -- which is why there are three legs and not one.
# The 500 and the odd body are fixtures, not measured Terra failures: no offline leg can make the real
# API refuse, and neither claim is presented as if it were.

TALLY_FOUND="# attribute existence: refs=12 graded=12 present=8 present-empty=1 absent=3 unknown=0 ungraded=0"
TALLY_BLIND="# attribute existence: refs=12 graded=4 present=3 present-empty=0 absent=1 unknown=8 ungraded=0"
SOURCE_LIVE="attribute source: live Terra reads: terra.entity_attributes() -- one point lookup per row"

want "the live read grades the SAME tally the capture graded, from one row read" 1 \
    "$TALLY_FOUND" "$SOURCE_LIVE" -- \
    ci none none default row-found
want "the row is read ONCE and the entity listing is never touched -- the page walk is gone, by CALLS" 1 \
    "CALLS=get_entity:1,get_submission:1,get_workspace:1,get_workspace_config:1,list_submissions:1" -- \
    ci none none default row-found
want 'the same row nested under entity tallies IDENTICALLY (the a21cb3b envelope trap, enforced)' 1 \
    "$TALLY_FOUND" "$SOURCE_LIVE" -- \
    ci none none default row-found-nested
want "a row that exists but binds NOTHING is nine ABSENTs, not an unread row" 1 \
    "# attribute existence: refs=12 graded=12 present=3 present-empty=0 absent=9 unknown=0 ungraded=0" \
    "$SOURCE_LIVE" -- \
    ci none none default present-empty
want "a row the API does not have says so, tallies it as UNKNOWN, and does not call it ABSENT" 1 \
    "terra.entity_attributes() answered 404: no sample_set row named 'EXAMPLE_SET_A'" "$TALLY_BLIND" -- \
    ci none none default row-gone
want "a lookup that FAILS (HTTP 500) is UNKNOWN with a different sentence, never absence" 1 \
    "terra.entity_attributes() could not answer (TerraError" "existence is not being guessed" "$TALLY_BLIND" -- \
    ci none none default lookup-500
want 'a 200 with no attributes member is UNKNOWN too, and is never read as binds-nothing' 1 \
    'no `attributes` object' "existence is not being guessed" "$TALLY_BLIND" -- \
    ci none none default body-no-attributes
want "and no live-path leg leaks an attribute value either" 0 \
    "NO-VALUES-PRINTED" -- \
    ci_values_leak none none default row-found


# =============================================================================================
# 13. THE WRONG SUFFIX IS A DIFFERENT DIAGNOSIS FROM A MISSING INPUT (GitHub issue #4).
#
# Nothing here recorded which suffix a sandbox's outputs actually sit under. `NEW_SUFFIX` is one global
# default (`kit/gsvtk-config`, `new`), so a workspace whose outputs were written under a different one --
# a rerun with `--tag h2h`, a second chain in the same workspace, a `GSVTK_NEW_SUFFIX` exported for one
# run and not this one -- reported every output of that step as ABSENT. That reads as "the step did not
# run" and sends the reader to fetch files that exist.
#
# Both arms grade off captures, never live Terra, and the agreeing arm is what keeps the diagnostic
# honest: the line may be fired only by NAMES THE ROW ACTUALLY CARRIES, not by absence in general. The
# two tallies differ in the one number that matters -- 6 present / 2 absent where the suffix disagrees,
# 8 / 0 where it agrees.
#
# Labels kept: `_h2h` is what `--tag` writes (measured by rerun.sh's own legs), but no leg here has a live
# workspace, so "a real sandbox looks like this" is an inference from the derivation, not a measurement.
# The 4 UNKNOWN in both arms are the member reads this check withholds by design.

want 'a suffix mismatch is named as a suffix mismatch, with counts and the source of the suffix' 1 \
     'note    0 of 2 expected output(s) under `_new`, 2 of 2 under `_h2h`' \
     'suffix mismatch and not a missing input' 'source: default' '--tag h2h' \
     '# attribute existence: refs=12 graded=12 present=10 present-empty=0 absent=2 unknown=0 ungraded=0' -- \
     ci none "$CI_SUFFIX_H2H" default unused --entity EXAMPLE_SET_A
want_no_sub 'an AGREEING sandbox prints no suffix line at all (8 present, 0 absent -- nothing to explain)' 0 \
        'note    0 of' 'suffix mismatch' -- \
        ci none "$CI_SUFFIX_NEW" default unused --entity EXAMPLE_SET_A
want 'the agreeing row grades every reference under the suffix it asks for (arms differ by suffix only)' 0 \
     '# attribute existence: refs=12 graded=12 present=12 present-empty=0 absent=0 unknown=0 ungraded=0' -- \
     ci none "$CI_SUFFIX_NEW" default unused --entity EXAMPLE_SET_A
want_no_sub 'a GENUINELY missing input is not explained away as a suffix problem' 1 \
        'note    0 of' 'suffix mismatch' -- \
        ci none "$CI_SUFFIX_BLIND" default unused --entity EXAMPLE_SET_A
want 'and the suffix in force is reported WITH where it came from (doctor, redacted view)' 0 \
     'GSVTK_NEW_SUFFIX' -- \
     sh -c "cd '$ROOT' && ./gsvtk doctor --redact 2>/dev/null | grep -E 'GSVTK_NEW_SUFFIX.*[[]default[]]'"

printf '\njarshape selftest: %s passed, %s failed, %s skipped\n' "$ok" "$fail" "$skipped"
# The pair's line is where this phase's mode-invariance is visible: the same declared count against the same
# reached count whether or not a jar was in hand, because what the jar skip withholds is ONE SITE read off this
# file's own text (its region markers), not a constant that had to guess the mode. A site that did not run and
# is not inside a region some skip named is a FAILURE by line number. What it still cannot see is a DELETION —
# declared and reached drop together.
gsvtk_declared_report "jarshape selftest" "$SELF" "$((ok + fail))" "$skipped" || fail=$((fail + 1))
[ "$fail" -eq 0 ] || exit 1
exit 0
