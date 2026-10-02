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

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
# Same rule `make helpsweep` and scripts/selftest.d/rerun.sh use: a phase that skips because the
# caller's python3 has no firecloud while ./.venv has it is a phase that reported nothing.
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1 && [ -x .venv/bin/python ] \
   && .venv/bin/python -c 'import firecloud' >/dev/null 2>&1; then PY=.venv/bin/python; fi
# Absolute, because one assertion runs under `env -i` with an empty PATH on purpose.
PY="$ROOT/${PY#./}"

TOOL="$ROOT/terra/batch_check_inputs.py"
FIX="$ROOT/scripts/selftest.d/fixtures/jar"
REQ_7F="$FIX/womtool84-IntegrateGDVcf-7fbf1171.inputs.json"
REQ_01="$FIX/womtool84-IntegrateGDVcf-01107996.inputs.json"
STRUCT_FIX="$FIX/womtool84-BenchmarkGqFilter-7fbf1171.inputs.json"
MATRIX="$FIX/womtool84-shapematrix.inputs.json"
MATRIX_WDL="$FIX/womtool84-shapematrix.wdl"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/gsvtk-jarshape.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/no-config.env"                  # an empty profile: nothing from a user's config leaks in
ok=0; fail=0; skipped=0

# --------------------------------------------------------------------------- harness
# want DESC WANT_RC NEEDLE... -- CMD...      (as scripts/selftest.d/womtool.sh: exit code AND every
# want_no DESC WANT_RC ABSENT... -- CMD...    needle, because a refusal and a crash share exit codes)
#       ...and NONE of the ABSENT needles may appear as a whole output line. This is how "that input
#       is NOT in the required set" gets asserted, which is the only way to notice an input silently
#       disappearing from it.
say_ok()   { ok=$((ok + 1)); printf '  ok    %s (exit %s)\n' "$1" "$2"; }
say_fail() { fail=$((fail + 1)); printf '  FAIL  %s (exit %s, want %s)%s\n' "$1" "$2" "$3" "$4"; }

want() {
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
else:
    sys.exit("probe.py: unknown mode %r" % mode)
PY

probe() { "$PY" "$TMP/probe.py" "$ROOT" "$@"; }

# --------------------------------------------------------------------------- the phase
echo
echo "selftest: the womtool optionality predicate, graded against captured womtool-84 output"
canary
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1; then
    printf '  SKIP  jarshape: %s cannot import firecloud, which terra/batch_check_inputs.py imports (python -m pip install -r requirements.txt)\n' "$PY"
    skipped=$((skipped + 1))
    printf '\njarshape selftest: %s passed, %s failed, %s skipped (a SKIP here is not a pass in CI)\n' \
        "$ok" "$fail" "$skipped"
    [ "$fail" -eq 0 ] || exit 1
    exit 0
fi
for f in "$REQ_7F" "$REQ_01" "$STRUCT_FIX" "$MATRIX" "$MATRIX_WDL" \
         "$FIX/womtool84-validate-not-specified.txt" "$FIX/womtool84-validate-coercion.txt" \
         "$FIX/womtool84-validate-extra-key.txt" "$FIX/womtool84-validate-value-and-missing.txt"; do
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
want "no jar, no seam: refuses by name, naming WOMTOOL_JAR, the download and the seam" 1 \
    "womtool jar not found (WOMTOOL_JAR='')" "Download the womtool jar" \
    "GSVTK_WOMTOOL_INPUTS_JSON" -- \
    env -u WOMTOOL_JAR -u GSVTK_WOMTOOL_INPUTS_JSON -u GSVTK_WORK \
        GSVTK_CONFIG="$TMP/no-config.env" "$PY" "$TOOL" --step 10
want "a WOMTOOL_JAR that is not a file is the same refusal, with the path quoted" 1 \
    "womtool jar not found (WOMTOOL_JAR='" "Download the womtool jar" -- \
    env -u GSVTK_WOMTOOL_INPUTS_JSON GSVTK_CONFIG="$TMP/no-config.env" \
        WOMTOOL_JAR="$TMP/no-such-dir/womtool.jar" "$PY" "$TOOL" --step 10
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
else
    printf '  SKIP  jarshape: %s cannot import miniwdl (WDL), so womtool-84 validate output was not classified (python -m pip install -r requirements-dev.txt)\n' "$PY"
    skipped=$((skipped + 1))
fi

# 9. WITH A JAR IN HAND, THE CAPTURE IS A CAPTURE: re-run womtool on the shape-matrix WDL and require
#    the parsed JSON to equal what is checked in, so a fixture that drifted from the tool is caught
#    rather than trusted. Where there is no jar this is a NAMED, COUNTED SKIP, because a check that
#    could not run is not a check that passed.
jar="${WOMTOOL_JAR:-}"
java_bin="${JAVA:-java}"
if [ -n "$jar" ] && [ -f "$jar" ] && command -v "$java_bin" >/dev/null 2>&1; then
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
else
    printf '  SKIP  jarshape: WOMTOOL_JAR=%s, so the shape-matrix capture was not re-derived from the jar — every graded assertion above ran without java\n' "${jar:-unset}"
    skipped=$((skipped + 1))
fi

printf '\njarshape selftest: %s passed, %s failed, %s skipped\n' "$ok" "$fail" "$skipped"
[ "$fail" -eq 0 ] || exit 1
exit 0
