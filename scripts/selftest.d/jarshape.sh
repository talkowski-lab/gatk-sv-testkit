#!/usr/bin/env bash
# scripts/selftest.d/jarshape.sh — tests for terra/batch_check_inputs.py, graded against captured
# womtool-84 output. No jar, java, network or gatk-sv checkout needed.
#
#     bash scripts/selftest.d/jarshape.sh .venv/bin/python
#
# `required_inputs()` decides optionality from `womtool inputs` JSON. womtool-84 marks optional inputs
# with a trailing ` (optional)` or ` (optional, default = <expr>)` in a type STRING. A struct-typed
# input arrives as an OBJECT of its members instead, e.g.
#
#   "BenchmarkGqFilter.original_scores": { "label": "String", "pickled_scores_file": "File? (optional)" }
#
# so a naive `"optional" not in value` becomes a dict-key test, and a required struct with a member
# named `optional` drops out of the required set. The predicate therefore tests the marker on strings
# only and treats any other shape as required.
#
# Fixtures (scripts/selftest.d/fixtures/jar/):
#   womtool84-IntegrateGDVcf-{7fbf1171,01107996}.inputs.json  verbatim captures at two gatk-sv commits:
#                                                  `String sample_id` (rejected by CI) vs `String? sample_id`
#   womtool84-BenchmarkGqFilter-7fbf1171.inputs.json  verbatim; holds a real dict-valued entry
#   womtool84-shapematrix.inputs.json              real womtool output for a synthetic WDL
#                                                  (womtool84-shapematrix.wdl) covering every value
#                                                  shape; re-derived when a jar is available
#
# Every graded run reads a fixture through `GSVTK_WOMTOOL_INPUTS_JSON`; one assertion runs with
# `JAVA=/usr/bin/false` and an empty PATH to prove nothing shells out. bash 3.2 compatible.
#
# Sections 10-14 cover the tool's Terra-side checks, offline, through a second seam,
# `GSVTK_ENTITY_JSON`, and a fixture-backed stand-in for the fiss API:
#   10. no womtool is a counted skip, and dependent checks print SKIPPED rather than passing on nothing
#   11. check 2b: do the attributes the config binds exist on the row (PRESENT / PRESENT-EMPTY / ABSENT)
#   12. the live row read: only a 404 means absence; anything that cannot answer is UNKNOWN
#   13. a wrong output suffix is diagnosed separately from a missing input
#   14. check 2c: the expected suffix derived from the stored method configs
#
# The Terra fixtures are hand-authored: real shapes and names, placeholder values.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
# Prefer ./.venv when the caller's python has no firecloud.
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1 && [ -x .venv/bin/python ] \
   && .venv/bin/python -c 'import firecloud' >/dev/null 2>&1; then PY=.venv/bin/python; fi
# Make PY absolute: one assertion runs under `env -i` with an empty PATH.
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
# Terra-side fixtures (hand-authored, placeholder values).
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
# Stored method-config listings, the input to check 2c: another suffix / the in-force one / none.
CI_MC_H2H="$FIX/checkinputs-methodconfig-list-h2h.json"
CI_MC_NEW="$FIX/checkinputs-methodconfig-list-new.json"
CI_MC_NOSUFFIX="$FIX/checkinputs-methodconfig-list-nosuffix.json"
CI_MC_TWOSUFFIX="$FIX/checkinputs-methodconfig-list-steptwosuffix.json"   # one step, two suffixes

TMP="$(mktemp -d "${TMPDIR:-/tmp}/gsvtk-jarshape.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/no-config.env"                  # an empty profile: nothing from a user's config leaks in
ok=0; fail=0; skipped=0

# Declared-vs-reached site accounting (see declared.sh). The verdict count varies with whether a jar is
# present, so sites are counted instead; what each skip withholds is declared as a guarded region.
# `say_ok`/`say_fail` are only called from inside `want`, so they are not sites.
SELF="$ROOT/scripts/selftest.d/jarshape.sh"
. "$ROOT/scripts/selftest.d/declared.sh"
GSVTK_DECL_FUNCS="want want_no want_no_sub canary"
gsvtk_declared_init "$TMP"          # the ledger is the set of reached sites (bash 3.2: no associative arrays)

# want    DESC WANT_RC NEEDLE... -- CMD...   exit code matches and every needle appears.
# want_no DESC WANT_RC ABSENT... -- CMD...   exit code matches and no ABSENT needle appears as a whole
#                                            output line (e.g. an input missing from the required set).
say_ok()   { ok=$((ok + 1)); printf '  ok    %s (exit %s)\n' "$1" "$2"; }
say_fail() { fail=$((fail + 1)); printf '  FAIL  %s (exit %s, want %s)%s\n' "$1" "$2" "$3" "$4"; }

want_no_sub() {
    # Like want_no, but ABSENT needles are substrings rather than whole lines.
    gsvtk_note_site "${BASH_LINENO[0]}"   # see want() above
    local desc="$1" wantrc="$2"; shift 2
    local absent=() out rc hit="" n
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do absent=("${absent[@]-}" "$1"); shift; done
    shift
    out="$("$@" 2>&1)"; rc=$?
    for n in "${absent[@]-}"; do
        [ -n "$n" ] || continue
        grep -qF -- "$n" <<< "$out" && hit="$hit
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
        grep -qF -- "$n" <<< "$out" || miss="$miss
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
        grep -qxF -- "$n" <<< "$out" && hit="$hit
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

# The driver imports the tool by path and goes through its own required_inputs(), never a copy of
# the predicate.
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

        def list_workspace_configs(self, ns, name, *a, **kw):
            # The listing check 2c reads. Offline it can only REFUSE (this repo has no network in the
            # gate), and the refusal is still counted in CALLS: that count is the positive control for
            # check 2c's live arm, the proof the tool really reached `terra.workspace_configs()` rather
            # than printing its skip from a path that never ran.
            calls.append("list_workspace_configs")
            if os.environ.get("GSVTK_METHODCONFIG_LIST_JSON"):
                self._refuse_seam("the method-config listing")
            raise terra_mod.TerraError("offline leg: no listing is answerable here")

        def get_workspace(self, ns, name, fields=None):
            calls.append("get_workspace")
            if seam_set:
                self._refuse_seam("the workspace read")
            return Resp(run_doc["workspace"])

    fake = FakeApi()
    bci.fapi = fake
    terra_mod.fapi = fake

    def no_rest(*a, **kw):
        # Counted, not just raised: `_read_both` asks raw REST FIRST and then the fiss client, so a
        # check 2c skip that proves only `list_workspace_configs` was reached would be a leg passing on
        # one client's answer. `kw` may carry the path; the caller passes it positionally.
        calls.append("rest_get:" + str(a[0].rsplit("/", 1)[-1]) if a else "rest_get")
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
# Run the real main() against the fiss stand-in (probe.py `run` mode). `none` for the entity capture
# means the live page fixture is read; scenario `unused` means no page scenario is reachable.
# GSVTK_CONFIG points at placeholder coordinates.
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

# Placeholder coordinates, using the `your-*` convention from scripts/selftest.d/golden/.
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
# Everything below is behind the firecloud bail, declared as a region so the site accounting still runs.
gsvtk_guard_to_end "jarshape-no-firecloud"
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1; then
    printf '  SKIP  jarshape: %s cannot import firecloud, which terra/batch_check_inputs.py imports (python -m pip install -r requirements.txt)\n' "$PY"
    skipped=$((skipped + 1))
    gsvtk_note_skip "jarshape-no-firecloud"
    printf '\njarshape selftest: %s passed, %s failed, %s skipped (a SKIP here is not a pass in CI)\n' \
        "$ok" "$fail" "$skipped"
    gsvtk_declared_report "jarshape selftest" "$SELF" "$((ok + fail))" "$skipped" || fail=$((fail + 1))
    [ "$fail" -eq 0 ] || exit 1
    exit 0
fi
for f in "$REQ_7F" "$REQ_01" "$STRUCT_FIX" "$MATRIX" "$MATRIX_WDL" \
         "$FIX/womtool84-validate-not-specified.txt" "$FIX/womtool84-validate-coercion.txt" \
         "$FIX/womtool84-validate-extra-key.txt" "$FIX/womtool84-validate-value-and-missing.txt" \
         "$CI_CFG" "$CI_CFG_MEMBER" "$CI_WT" "$CI_RUN" "$CI_CAPTURE" "$CI_CAPTURE_FULL" "$CI_PAGES" \
         "$CI_MC_H2H" "$CI_MC_NEW" "$CI_MC_NOSUFFIX" "$CI_MC_TWOSUFFIX"; do
    if [ ! -f "$f" ]; then
        printf '  FAIL  the fixture %s is missing; nothing below this line can be graded\n' "${f#"$ROOT"/}"
        printf '\njarshape selftest: 0 passed, 1 failed, 0 skipped\n'
        exit 1
    fi
done

# 1. Two real womtool-84 captures, one broken and one fixed: `sample_id` is REQUIRED at 7fbf1171
#    (`String`) and not at 01107996 (`String?`). Both halves are needed to tell a working predicate
#    from one that calls everything required.
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

# 2. The marker, entry by entry, from the 7fbf1171 capture: required, Array, `X?`, defaulted task
#    input, and an optional struct (a string with the marker, not members).
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

# 3. The dict-valued entry: a required struct emitted as its members, one of which has "(optional)"
#    in its own type string. The members are not the input.
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

# 4. The shape matrix. The first two lines are the key case: a struct member literally named
#    `optional` must not make the required input disappear.
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
#    Known limitation: womtool-84 emits a defaulted struct exactly like a required one, so it counts
#    as required. That errs on the loud side (a FAIL naming the input).
want "a defaulted struct is indistinguishable from a required one (pinned limitation)" 0 \
    "KIND=dict" "SHAPE=required" -- probe shape "$MATRIX" "ShapeProbe.struct_defaulted"

# 5. No looser than the marker, and never on a member name: an `optional` key with a false value is
#    refused too.
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

# 6. The seam bypasses java and the jar: PATH is empty and JAVA is /usr/bin/false.
mkdir -p "$TMP/emptybin"
want "captured JSON is graded with no jar, no java, no PATH and no checkout" 0 \
    "COUNT=11" "sample_id" -- \
    env -i PATH="$TMP/emptybin" JAVA=/usr/bin/false GSVTK_WOMTOOL_INPUTS_JSON="$REQ_7F" \
        "$PY" "$TMP/probe.py" "$ROOT" required wdl/IntegrateGDVcf.wdl IntegrateGDVcf

# 7. Refusals, end to end: with nothing to read the required set from, the tool names what it wants.
#    A seam pointed at a missing file or non-womtool JSON is refused and names the variable.
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

# 8. `womtool validate` messages (verbatim womtool-84 fixtures) and which are comparable with the
#    key-set layer: missing required input, value coercion, and an extra key such as
#
#      WARNING: Unexpected input provided: IntegrateGDVcf.this_key_does_not_exist_in_the_wdl
#
#    The extra-key case is the offline layer's blind spot: it checks required keys into the JSON,
#    never the JSON's keys back into the WDL.
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
    #    womtool prints every problem it finds. A pair rejected for both a value and a missing input
    #    must classify as the key question, so the missing input is not hidden.
    want "a pair rejected for a value AND a missing input still classifies as the key question" 0 \
        "CLASS=keys" -- "$PY" "$TMP/classify.py" "$ROOT" \
        "$FIX/womtool84-validate-value-and-missing.txt"

    #    The branch each classification drives, end to end with no jar: only womtool's words change.
    #    value-class -> OUT-OF-LAYER, extra-key -> names the gap, key-class -> DISAGREES.
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

# 9. With a jar, re-run womtool on the shape-matrix WDL and require it to equal the checked-in
#    capture. Without one this is a named, counted skip.
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
# 10. No womtool is a counted skip, not an exit.
#
# Without a jar or capture, the skip line names what was withheld and which checks depended on it, and
# check 3 prints SKIPPED instead of passing over an empty required set. A seam that is configured but
# broken still refuses (section 7). These legs drive main() through the fiss stand-in; no leg
# exercises the real HTTP path.

ci_values_leak() {          # rc 0 only when the run printed no attribute VALUE anywhere
    local out
    out="$(ci "$@")"
    if grep -qF 'EXAMPLE-PLACEHOLDER' <<< "$out"; then
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
# 11. Check 2b: do the attributes this config binds exist on the row?
#
# `inputsResolutions` only says what a past submission resolved. This check reads the row and reports
# three states with the entity type / row / suffix on each reference line. Graded through
# GSVTK_ENTITY_JSON here and through the live reader in section 12. No leg may print a value: the
# placeholder below is what a leaked value would look like.

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
# 12. The live row read, graded offline.
#
# `row_attributes_live()` calls `terra.entity_attributes()` (one `GET .../entities/<type>/<name>`:
# attributes, None for a 404, raise otherwise). `get_entities_query` stays faked as a tripwire so the
# CALLS leg catches any return to walking listings. The legs check:
#   * an existing row: 8 present, 1 present-but-empty, 3 absent;
#   * the same row nested under `entity` tallies identically;
#   * 404, HTTP 500 and a 200 with no `attributes` all land on UNKNOWN with the same tally and three
#     different sentences, so a lookup that cannot answer never reports missing inputs.
# The 500 and the odd body are fixtures, not observed Terra failures.

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
# 13. A wrong suffix is a different diagnosis from a missing input.
#
# Outputs written under another suffix (e.g. a `--tag h2h` rerun) would otherwise all read as ABSENT.
# The note may fire only on names the row actually carries: 6 present / 2 absent where the suffix
# disagrees, 8 / 0 where it agrees. The 4 UNKNOWN in both arms are member reads, withheld by design.

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



# =============================================================================================
# 14. Check 2c: the expected suffix, derived from the stored method configs.
#
# Needs no row: the workspace's stored configs state in their `outputs` maps which suffix the chain
# writes, and check 2c compares that with the suffix this shell resolves. The legs cover:
#   * derivation through `output_suffixes()`, including a suffix outside the known vocabulary (`_h2h`);
#     the unnumbered entry in the `_h2h` capture writes the in-force suffix and must be filtered out;
#   * an empty or unusable listing is a counted skip that `--strict` fails on. The seam arm asserts the
#     listing endpoint was not called; the no-seam arm asserts by CALLS that it was;
#   * agreement prints nothing extra (byte-identity control below);
#   * `workspace_configs()` reads two clients via `_read_both`. The seam skips that cross-check and says
#     so; the live arm's skip must name both clients and CALLS must show both were reached;
#   * which configs belong to the chain is decided by steps.py; unscored configs are named and counted;
#   * two configs of one step writing two suffixes is a disagreement: both printed, neither picked,
#     and `--strict` fails it.

want 'the stored configs writing another suffix is said, with the in-force side and its provenance' 0 \
     'EXPECTED output suffix, derived from this workspace' \
     'x5 (06-GenerateBatchMetrics, 07-FilterBatchSites' \
     "(NEW_SUFFIX='new', source: default)" \
     'no entity row was read to say it' 'NOT a live read' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_H2H" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
want_no_sub 'and only the NUMBERED configs count: the unnumbered `_new` config never rescues the mismatch' 0 \
     '`_new` x' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_H2H" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
want_no_sub 'configs that write the suffix in force print NOTHING new (no verdict, and no skip)' 0 \
     'EXPECTED output suffix' '# SKIP stored-config suffix (check 2c)' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_NEW" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
want_no_sub 'the seam was read rather than ignored: no listing endpoint was touched' 0 \
     ',list_workspace_configs' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_H2H" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
want 'both arms can speak at once: the row-derived note and the derived expectation, each naming its source' 1 \
     'note    0 of 2 expected output(s) under `_new`' \
     'EXPECTED output suffix, derived from this workspace' \
     'attribute source: captured Terra read' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_H2H" "$CI_WT" "$CI_SUFFIX_H2H" default unused \
        --entity EXAMPLE_SET_A
want 'no seam: the withheld expectation is a named, COUNTED skip — and the live read really ran' 0 \
     '# SKIP stored-config suffix (check 2c):' 'terra.workspace_configs() could not list' \
     'Withheld: the EXPECTED output suffix' 'did NOT run for step 10' \
     'list_workspace_configs:1' -- \
     ci "$CI_WT" "$CI_SUFFIX_NEW" default unused --entity EXAMPLE_SET_A
want 'a listing that answers and carries no suffix is its own named skip, never read as agreement' 0 \
     '# SKIP stored-config suffix (check 2c): the listing answered and yielded NO expected suffix' \
     '5 of 5 listing entrie(s)' 'an empty answer from a listing is not an agreement' \
     'NOT a live read' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_NOSUFFIX" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
want '--strict turns the withheld expectation into a failure (a comparison that did not run is not a pass)' 1 \
     'FAIL --strict' 'a comparison that did not run is not a comparison that passed' \
     'list_workspace_configs:1' 'RC=1' -- \
     ci "$CI_WT" "$CI_SUFFIX_NEW" default unused --entity EXAMPLE_SET_A --strict
want_no_sub 'and --strict is satisfied the moment the listing answers and agrees' 0 \
     '--strict' 'FAIL' 'SKIP stored-config suffix' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_NEW" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A --strict
printf 'not json {\n' > "$TMP/mc-unparsable.json"
printf '{"entities": []}\n' > "$TMP/mc-wrong-shape.json"
want 'a seam that is not a file is refused by name, not answered with a skip' 1 \
     'GSVTK_METHODCONFIG_LIST_JSON' 'is not a file' 'no expected output suffix is being guessed' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$TMP/not-a-capture.json" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
want 'unparsable JSON in the seam is refused, naming the file it tried to read' 1 \
     'GSVTK_METHODCONFIG_LIST_JSON' 'is not readable JSON' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$TMP/mc-unparsable.json" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
want 'a shape that is not a listing is refused rather than read as "no configs in this workspace"' 1 \
     'GSVTK_METHODCONFIG_LIST_JSON' 'is not a method-config listing' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$TMP/mc-wrong-shape.json" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
want 'the seam and the flag are both in the tool'"'"'s own --help' 0 \
     'GSVTK_METHODCONFIG_LIST_JSON=<file>' 'flags:' '--strict' \
     'NOT a live read' -- \
     env -u WOMTOOL_JAR -u GSVTK_ENTITY_JSON -u GSVTK_METHODCONFIG_LIST_JSON \
         "$PY" "$TOOL" --help

# --- the two-client boundary (`_read_both`), named on both arms.
want 'the seam says what it skips: one file answering where workspace_configs() asks two clients' 0 \
     'NOT a live read, and NOT a two-client answer' '_read_both' 'fapi.list_workspace_configs' \
     'confirmed by a second client' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_H2H" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
want 'the live arm names BOTH clients it asked, and CALLS proves both were reached' 0 \
     'neither client could list it' \
     'raw REST GET /api/workspaces/your-namespace/your-workspace/methodconfigs' \
     'fapi.list_workspace_configs' 'rest_get:methodconfigs:1' 'list_workspace_configs:1' -- \
     ci "$CI_WT" "$CI_SUFFIX_NEW" default unused --entity EXAMPLE_SET_A

# --- what was NOT scored is a named bucket (steps.py decides membership; no `NN-` pattern lives here).
want 'a config matching no step is named and counted, never silently dropped by a numbering filter' 0 \
     'NOT scored against any step: 1 config(s), named: GenotypeBatch-unnumbered-copy [no step]' \
     'is a bucket, not a silence' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_H2H" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
cat > "$TMP/mc-bare-name.json" <<'JJ'
{"methodconfigs": [{"name": "GenotypeBatch", "outputs":
  {"GenotypeBatch.regeno_coverage_medians": "this.regeno_coverage_medians_h2h"}}]}
JJ
want 'a numberless config named after the workflow IS scored as its step (steps.match_configs)' 0 \
     'EXPECTED output suffix, derived from this workspace' '`_h2h` x1 (GenotypeBatch)' \
     'step 10 writes `_h2h`' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$TMP/mc-bare-name.json" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
cat > "$TMP/mc-snapshot.json" <<'JJ'
{"methodconfigs": [{"name": "10-GenotypeBatch",
  "outputs": {"GenotypeBatch.regeno_coverage_medians": "this.regeno_coverage_medians_h2h"}},
 {"name": "10-GenotypeBatch_7fk2",
  "outputs": {"GenotypeBatch.regeno_coverage_medians": "this.regeno_coverage_medians_new"}}]}
JJ
want 'a per-submission snapshot copy cannot outvote the live config, and says it was not scored' 0 \
     'EXPECTED output suffix, derived from this workspace' '`_h2h` x1 (10-GenotypeBatch)' \
     'NOT scored against any step: 1 config(s), named: 10-GenotypeBatch_7fk2' \
     '[snapshot copy of a step with a live config]' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$TMP/mc-snapshot.json" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
want_no_sub 'and the snapshot'"'"'s own suffix never reaches the verdict' 0 '`_new` x' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$TMP/mc-snapshot.json" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A

# --- two configs of one step, two suffixes: printed, refused, and --strict fails the refusal.
want 'two stored configs of ONE STEP write two suffixes: both printed, neither picked' 0 \
     '# DISAGREE stored-config suffix (check 2c):' \
     'TWO STORED CONFIGS OF STEP 10 write DIFFERENT suffixes' \
     '`_h2h` (10-GenotypeBatch-h2h)' '`_new` (10-GenotypeBatch)' \
     'step 10 (10-GenotypeBatch, 10-GenotypeBatch-h2h)' 'NOT a live read' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_TWOSUFFIX" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
want_no_sub 'and the refusal claims no single EXPECTED suffix, in either spelling' 0 \
     'EXPECTED output suffix, derived from this workspace' 'writes `_new`' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_TWOSUFFIX" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A
want '--strict fails a REFUSED comparison too: two stored suffixes is not a comparison that ran' 1 \
     'FAIL --strict' 'two stored configs of one step write different suffixes' 'RC=1' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_TWOSUFFIX" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A --strict
want 'the tagged rerun is attributed through steps.rerun, so the disagreement names the tag that wrote it' 0 \
     '10-GenotypeBatch-h2h' -- \
     ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_TWOSUFFIX" "$CI_WT" "$CI_SUFFIX_NEW" default unused \
        --entity EXAMPLE_SET_A

# Byte-identity control: both sides print the shipped note; the side with an agreeing listing must add
# nothing. The marker is printed only when the note is present on both sides, so two empty outputs
# cannot pass.
byte_identity_control() {
    local use_seam out
    for use_seam in 0 1; do
        out="$TMP/shipped-note.$use_seam"
        if [ "$use_seam" = 1 ]; then
            ci GSVTK_METHODCONFIG_LIST_JSON="$CI_MC_NEW" "$CI_WT" "$CI_SUFFIX_H2H" default unused \
               --entity EXAMPLE_SET_A > "$out.all" 2>&1
        else
            ci "$CI_WT" "$CI_SUFFIX_H2H" default unused --entity EXAMPLE_SET_A \
               > "$out.all" 2>&1
        fi
        grep -F 'note    0 of' "$out.all" > "$out"
    done
    if [ ! -s "$TMP/shipped-note.0" ] || [ ! -s "$TMP/shipped-note.1" ]; then
        printf 'NOTE-MISSING the shipped row-derived note did not print on both sides; nothing was compared\n'
        return 1
    fi
    if ! cmp -s "$TMP/shipped-note.0" "$TMP/shipped-note.1"; then
        diff "$TMP/shipped-note.0" "$TMP/shipped-note.1" | head -4
        return 1
    fi
    printf 'NOTE-BYTES-IDENTICAL %s line(s)\n' "$(grep -c . "$TMP/shipped-note.1")"
}
want 'the shipped row-derived note is byte-identical with and without the config seam (and it DID print)' 0 \
     'NOTE-BYTES-IDENTICAL 1 line(s)' -- byte_identity_control

printf '\njarshape selftest: %s passed, %s failed, %s skipped\n' "$ok" "$fail" "$skipped"
# The declared count is the same with or without a jar; a site that did not run outside a declared skip
# region is a failure by line number.
gsvtk_declared_report "jarshape selftest" "$SELF" "$((ok + fail))" "$skipped" || fail=$((fail + 1))
[ "$fail" -eq 0 ] || exit 1
exit 0
