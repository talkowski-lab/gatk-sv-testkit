#!/usr/bin/env bash
# scripts/selftest.d/womtool.sh — tests for checks/wdl_inputs_check.py, the CI-parity half of the WDL
# gate, without a womtool jar.
#
#     bash scripts/selftest.d/womtool.sh .venv/bin/python
#
# miniwdl never opens an input file, so it cannot see a required input that the rendered input JSON
# does not carry (gatk-sv 7fbf1171: `Required workflow input 'IntegrateGDVcf.sample_id' not
# specified`). `checks/wdl_inputs_check.py` checks that, and must report nothing at the fix (01107996).
#
# Sections 10-11 cover the other direction: keys the input JSON names that the WDL never declared
# (womtool's `Unexpected input provided`). This layer's expected-key set is a superset of
# `womtool inputs`, so it can under-report but not invent a finding; see docs/static-checks.md.
#
# Fixtures:
#   fixtures/womtool/wdl/Extras.wdl           four legal key shapes in one WDL
#   fixtures/womtool/clean/inputs/...         those shapes only: extras=0
#   fixtures/womtool/extra/inputs/...         the same plus two bogus keys, test + -t
#   fixtures/jar/womtool84-extras-crosscheck.{wdl,inputs.json,validate.txt}
#                                             cross-check pair A: womtool-84's verbatim answer
#   fixtures/jar/womtool84-shapematrix-flatmember.{inputs.json,validate.txt}
#                                             pair B: a known divergence (flat struct member path)
#   fixtures/jar/womtool84-validate-extra-key.txt  pair C: captured off a real rendered gatk-sv JSON
#   fixtures/womtool/render/                  stand-in for gatk-sv's input renderer (section 12)
#
# Section 12 covers `--render-only`'s destination guard: the destination is stamped, and replaced only
# when the stamp names this tool and the ref being rendered; an un-stamped non-empty tree is refused.
# It also runs `checks/wdl_gate.sh` against a throwaway checkout, and (12.9) runs the renderer on an
# interpreter that has jinja2 but cannot import WDL, which is what the gate's `JINJA_PY` probe may pick.
# jinja2 is a stub on PYTHONPATH so the section grades the same with or without the real package.
#
# There is no womtool jar here, so the jar layer is driven by a stub `java` (a private PATH entry plus
# `$JAVA`): it fails with womtool's words for any WDL under one named directory and passes everything
# else. WDLs are written into a temp tree with input JSONs in a fake `inputs/build/**` layout; nothing
# reads a ref, checkout or real value. bash 3.2 compatible.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
# Absolute, and exported as the interpreter checks/wdl_gate.sh should use; otherwise the gate picks
# its own and gate-backed assertions depend on a venv inside the checkout.
case "$PY" in
    */*) if [ -x "$PY" ]; then
             PYABS="$(cd "$(dirname "$PY")" && pwd -P)/$(basename "$PY")"
         else
             PYABS="$PY"
         fi ;;
    *)   PYABS="$(command -v "$PY" 2>/dev/null || printf '%s' "$PY")" ;;
esac
export GSVTK_PY="$PYABS"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi

CHECK="$ROOT/checks/wdl_inputs_check.py"
GATE="$ROOT/checks/wdl_gate.sh"
FIXJ="$ROOT/scripts/selftest.d/fixtures/jar"          # verbatim womtool-84 stdout, shared with jarshape.sh
FIXW="$ROOT/scripts/selftest.d/fixtures/womtool"     # the extra-key WDL and the two input trees beside it
FIXR="$FIXW/render"                                  # section 12: the fixture renderer and its values files

TMP="$(mktemp -d "${TMPDIR:-/tmp}/gsvtk-womtool.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"                 # physical: the stub compares paths against these
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"
ok=0; fail=0; skipped=0

# miniwdl is a dev dependency. Without it the tool answers `status=NO-MINIWDL` (exit 2), and only that
# answer is turned into a counted skip. The check runs before the pass/fail decision, since absence
# legs (`want_no`) and comparison legs (`same`) would otherwise pass vacuously. HAS_WDL is ANDed in so
# the skip is unreachable on an interpreter that has miniwdl.
HAS_WDL=1
"$PY" -c 'import WDL' >/dev/null 2>&1 || HAS_WDL=0

# no_miniwdl DESC OUT — record a counted skip and return 0 when the tool said the AST was never read;
# return 1 otherwise. The render layer of checks/wdl_gate.sh shells out to the `miniwdl` CLI separately
# and degrades (exit 0, `render_value=(no rendered JSON)`) when it is missing, so it is probed apart.
HAS_RENDER=0
if [ -n "${MINIWDL:-}" ] || command -v miniwdl >/dev/null 2>&1 \
        || [ -x "$(dirname "$PY")/miniwdl" ]; then HAS_RENDER=1; fi

no_miniwdl() {
    # Each absence is recognized only by the tool's own words; no leg uses these strings as a needle.
    [ "$HAS_WDL" = 0 ] || [ "$HAS_RENDER" = 0 ] || return 1
    case "$2" in
        *"status=NO-MINIWDL"*|*"needs the miniwdl package"*) [ "$HAS_WDL" = 0 ] || return 1 ;;
        *"miniwdl not found"*|*"(no rendered JSON)"*) [ "$HAS_RENDER" = 0 ] || return 1 ;;
        *) return 1 ;;
    esac
    skipped=$((skipped + 1))
    printf '  SKIP  %s — no WDL analyzer here (module and/or CLI), so this leg had nothing to read\n' "$1"
    printf '        (pip install -r requirements-dev.txt, or MINIWDL=<the CLI>; CI installs both)\n'
    return 0
}

# Declared-vs-reached site accounting (see declared.sh). Sites, not verdicts, because the verdict count
# depends on whether a jar is present. The agreeing-mirrors check below records a verdict outside any
# recorder, so verdicts can exceed sites by one.
SELF="$ROOT/scripts/selftest.d/womtool.sh"
. "$ROOT/scripts/selftest.d/declared.sh"
GSVTK_DECL_FUNCS="want want_no canary same"
gsvtk_declared_init "$TMP"

# want DESC WANT_RC NEEDLE... -- CMD...   exit code matches and every needle appears.
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
    if no_miniwdl "$desc" "$out"; then
        :
    elif [ "$rc" = "$wantrc" ] && [ -z "$miss" ]; then
        ok=$((ok + 1)); printf '  ok    %s (exit %s)\n' "$desc" "$rc"
    else
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s, want %s)%s\n' "$desc" "$rc" "$wantrc" "$miss"
        printf '%s\n' "$out" | head -10 | sed 's/^/          /'
    fi
}

# canary: proves this file's tally can go down.
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

# want_no DESC WANT_RC ABSENT... -- CMD...   exit code matches and no ABSENT string appears anywhere.
want_no() {
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
    if no_miniwdl "$desc" "$out"; then
        :
    elif [ "$rc" = "$wantrc" ] && [ -z "$hit" ]; then
        ok=$((ok + 1)); printf '  ok    %s (exit %s)\n' "$desc" "$rc"
    else
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s, want %s)%s\n' "$desc" "$rc" "$wantrc" "$hit"
        printf '%s\n' "$out" | head -10 | sed 's/^/          /'
    fi
}

# same DESC GOT WANT — compare two computed answers. Two empty strings do not count as agreement.
same() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see want() above
    local desc="$1" got="$2" wantv="$3"
    if [ "$HAS_WDL" = 0 ]; then
        # The operands came from runs that said NO-MINIWDL, so the phase-level probe decides.
        skipped=$((skipped + 1))
        printf '  SKIP  %s — status=NO-MINIWDL upstream: both sides of this comparison come from the AST\n' "$desc"
    elif [ -n "$got" ] && [ "$got" = "$wantv" ]; then
        ok=$((ok + 1)); printf '  ok    %s (%s)\n' "$desc" "$got"
    else
        fail=$((fail + 1))
        printf '  FAIL  %s\n          got:  %s\n          want: %s\n' "$desc" "${got:-(nothing)}" "$wantv"
    fi
}

# --------------------------------------------------------------------------- fixtures
# One WDL with one required input, and the two ways upstream fixed it: bind the key or make it optional.
wdl_with() {                              # wdl_with <dest-dir> <sample_id-declaration>
    mkdir -p "$1"
    cat > "$1/Widget.wdl" <<WDL
version 1.0

struct WidgetOpt {
    Int cpu
}

workflow Widget {
  input {
    File vcf
    $2
    String? note
    WidgetOpt? opts
  }
  call MakeWidget {
    input:
      vcf = vcf,
      sample_id = sample_id
  }
  output {
    File out = MakeWidget.out
  }
}

task MakeWidget {
  input {
    File vcf
    String? sample_id                # optional here so BOTH fixtures typecheck: the pair differs only
  }                                  # in the WORKFLOW declaration, which is where the bug lives
  command <<<
    echo "~{default="" sample_id}" > out.txt
  >>>
  output { File out = "out.txt" }
  runtime { docker: "ubuntu:22.04" }
}
WDL
}

# The input JSONs CI pairs with wdl/Widget.wdl: one test JSON and one Terra workflow_configuration (-t).
# Only the "values" tree differs in values, so "presence, not values" can be asserted.
inputs_with() {                    # inputs_with <dir> <with-sample_id:0|1> <value-style>
    local dir="$1" have="$2" style="$3" vcf val
    mkdir -p "$dir/inputs/build/ref_panel_1kg/test/Widget" \
             "$dir/inputs/build/ref_panel_1kg/terra/workflow_configurations"
    case "$style" in
        real)   vcf="gs://gatk-sv-ref-panel-1kg/sub/x/call-A/all.b.vcf.gz" ;;
        bogus)  vcf="gs://nowhere-at-all/x.vcf.gz" ;;
        *)      vcf='${this.filtered_vcf}' ;;
    esac
    val="$vcf"
    {
        printf '{\n'
        printf '  "Widget.vcf": %s,\n' "\"$vcf\""
        [ "$have" = 1 ] && printf '  "Widget.sample_id": "SAMPLE_1",\n'
        printf '  "Widget.note": %s\n' "\"$val\""
        printf '}\n'
    } > "$dir/inputs/build/ref_panel_1kg/test/Widget/Widget.json"
    sed 's/"Widget.note": ".*"/"Widget.note": "${this.note}"/' \
        "$dir/inputs/build/ref_panel_1kg/test/Widget/Widget.json" \
        > "$dir/inputs/build/ref_panel_1kg/terra/workflow_configurations/Widget.json"
}

wdl_with "$TMP/wdl-bug" 'String sample_id'
wdl_with "$TMP/wdl-fix" 'String? sample_id'
inputs_with "$TMP/in-bug"  0 real
inputs_with "$TMP/in-ok"   1 real
inputs_with "$TMP/in-values" 1 bogus         # identical key set to in-ok, every value replaced

# A required STRUCT input: Cromwell wants one key per required member, while a Terra config may bind
# the whole struct with one placeholder. Both are OK.
mkdir -p "$TMP/wdl-struct"
cat > "$TMP/wdl-struct/WidgetStruct.wdl" <<'WDL'
version 1.0

struct WidgetOpt {
    Int cpu
    Int? disk_gb
}

workflow WidgetStruct {
  input {
    File vcf
    WidgetOpt opts
  }
  scatter (c in ["1", "2"]) {
    call DoIt { input: vcf = vcf, cpu = opts.cpu }
  }
  output {}
}

task DoIt {
  input {
    File vcf
    Int cpu
  }
  command <<<
    echo "~{cpu}" > o.txt
  >>>
  output { File out = "o.txt" }
  runtime { docker: "ubuntu:22.04" }
}
WDL
mk_struct() {                                 # mk_struct <dir> <body-of-keys>
    mkdir -p "$1/inputs/build/ref_panel_1kg/test/WidgetStruct"
    printf '{\n%s\n}\n' "$2" > "$1/inputs/build/ref_panel_1kg/test/WidgetStruct/WidgetStruct.json"
}
mk_struct "$TMP/in-struct-ok"  '  "WidgetStruct.vcf": "gs://x/y.vcf.gz",
  "WidgetStruct.opts.cpu": 4'
mk_struct "$TMP/in-struct-no"  '  "WidgetStruct.vcf": "gs://x/y.vcf.gz",
  "WidgetStruct.opts.disk_gb": 8'
mk_struct "$TMP/in-struct-all" '  "WidgetStruct.vcf": "gs://x/y.vcf.gz",
  "WidgetStruct.opts": "${this.whole_struct}"'

# No rendered JSON, and a tree that will not parse: both must report "nothing was checked", not clean.
mkdir -p "$TMP/in-empty/inputs/build/ref_panel_1kg/test"
mkdir -p "$TMP/wdl-broken"
printf 'version 1.0\nworkflow Widget { input { File vcf\n' > "$TMP/wdl-broken/Widget.wdl"

# The stub jar and stub `java`. Failure is keyed on where the WDL lives, so the same stub fails the
# broken tree and passes the fixed one.
mkdir -p "$TMP/bin"
cat > "$TMP/bin/java" <<'STUB'
#!/bin/sh
# Stands in for `java -jar womtool.jar validate <wdl> -i <json>`. It does not judge WDLs: it fails,
# with womtool's own message, for anything under $WOMTOOL_STUB_FAILDIR and passes everything else.
jar=""; sub=""; wdl=""; json=""
while [ $# -gt 0 ]; do
    case "$1" in
        -jar) jar="$2"; shift 2;;
        -i)   json="$2"; shift 2;;
        validate|inputs) sub="$1"; shift;;
        *)    [ -z "$wdl" ] && wdl="$1"; shift;;
    esac
done
[ -f "$jar" ] || { echo "Error: Unable to access jarfile $jar"; exit 1; }
[ -n "$json" ] && [ -f "$json" ] || { echo "Error: no input file $json"; exit 1; }
echo "stub womtool $sub $jar $wdl $json"
if [ -n "${WOMTOOL_STUB_FAILDIR:-}" ] \
   && [ "${wdl#"$WOMTOOL_STUB_FAILDIR"}" != "$wdl" ]; then
    echo "Required workflow input 'Widget.sample_id' not specified"
    exit 1
fi
echo "Validation successful"
exit 0
STUB
chmod +x "$TMP/bin/java"
: > "$TMP/fake-womtool.jar"                    # a file, not a jar: only its existence is checked

echo
echo "selftest: the rendered input JSONs bind what the WDL requires (offline, stub jar, no checkout)"
canary

# Pin the jar setting for every run so an ambient WOMTOOL_JAR cannot change a verdict: `run` has no
# jar, `run_jar` has the stub, and the gate wrappers are the same pair.
cfgenv=(GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work"
        GSVTK_GATK_SV_CHECKOUT="$TMP/not-a-checkout")
G_FAILDIR=""
run()      { env -u WOMTOOL_JAR -u JAVA "$PY" "$CHECK" "$@"; }
run_jar()  { env "${cfgenv[@]-}" WOMTOOL_JAR="$TMP/fake-womtool.jar" JAVA="$TMP/bin/java" \
                 WOMTOOL_STUB_FAILDIR="$G_FAILDIR" "$PY" "$CHECK" "$@"; }
gate_nojar() { env -u WOMTOOL_JAR -u JAVA "${cfgenv[@]-}" bash "$GATE" "$@"; }
gate_jar()   { env "${cfgenv[@]-}" WOMTOOL_JAR="$TMP/fake-womtool.jar" JAVA="$TMP/bin/java" \
                 WOMTOOL_STUB_FAILDIR="$G_FAILDIR" bash "$GATE" "$@"; }

# 1. The bug as gatk-sv 7fbf1171 shipped it: required by the WDL, missing from both paired JSONs.
want "a required input absent from every matched input JSON is a finding" 1 \
    "GSVTK-INPUTS wf=Widget pairs=2 missing=1 status=FINDING" \
    "MISSING-INPUT Widget.sample_id (String, Widget.wdl:10)" \
    "absent from 2 of 2 CI-matched input JSONs" \
    "[test] " "[terra] " -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget

# 2. Both fixes (optional input, or bound key) must be silent.
want "making the input optional clears the finding" 0 \
    "GSVTK-INPUTS wf=Widget pairs=2 missing=0 status=OK" -- \
    run --wdl-dir "$TMP/wdl-fix" --inputs-root "$TMP/in-bug" --wf Widget
want "binding the key in the JSON clears the finding" 0 \
    "GSVTK-INPUTS wf=Widget pairs=2 missing=0 status=OK" -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-ok" --wf Widget

# 3. Presence, not values: same key set with unusable values gives the same verdict.
want "values that differ (and are unusable) change nothing" 0 \
    "GSVTK-INPUTS wf=Widget pairs=2 missing=0 status=OK" -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-values" --wf Widget
#    The Terra half is only `${this.x}` expressions.
want "a Terra placeholder value is still a bound key" 0 \
    "pairs=2" "status=OK" -- \
    run --wdl-dir "$TMP/wdl-fix" --inputs-root "$TMP/in-bug" --wf Widget

# 4. CI's pairing: `-t` adds the Terra configs. Assert the pair count so a dropped half is noticed.
want "the -t half is paired: 2 JSONs" 1 "pairs=2" -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget
want "--no-terra drops it: 1 JSON" 1 "pairs=1" "absent from 1 of 1 CI-matched input JSON" -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget --no-terra

# 5. Structs: required members are required keys, optional members are not, and a Terra config may
#    bind the whole struct with one placeholder.
want "a required struct member is a required key" 1 \
    "MISSING-INPUT WidgetStruct.opts.cpu (Int, WidgetStruct.wdl:11)" -- \
    run --wdl-dir "$TMP/wdl-struct" --inputs-root "$TMP/in-struct-no" --wf WidgetStruct
want "binding the struct member flat satisfies it" 0 "status=OK" -- \
    run --wdl-dir "$TMP/wdl-struct" --inputs-root "$TMP/in-struct-ok" --wf WidgetStruct
want "binding the whole struct as one key satisfies its members" 0 "status=OK" -- \
    run --wdl-dir "$TMP/wdl-struct" --inputs-root "$TMP/in-struct-all" --wf WidgetStruct

# 6. Checking nothing is not clean: four ways, each named and nonzero.
want "a workflow with no rendered JSON says nothing was checked" 2 \
    "GSVTK-INPUTS wf=Widget pairs=0 missing=0 status=NO-INPUTS" \
    "CI's blind spot" -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-empty" --wf Widget
want "a WDL that will not parse is a failure, not a zero" 2 \
    "status=LOAD-FAILURE" "NOTHING-CHECKED" -- \
    run --wdl-dir "$TMP/wdl-broken" --inputs-root "$TMP/in-bug" --wf Widget
want "a --wf name with no such WDL is reported" 2 \
    "status=NO-WDL" "no Scaffold.wdl" -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Scaffold
want "--inputs-root at a tree with no inputs/build is not 'CI has no JSON'" 2 \
    "status=NO-BUILD" "NOTHING-CHECKED" -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/no-such-tree" --wf Widget

# 7. The jar layer with a stub: rc=1 becomes a finding, and the same stub passes the fixed tree.
G_FAILDIR="$TMP/wdl-bug"
want "womtool rc=1 becomes a finding" 1 \
    "GSVTK-WOMTOOL wf=Widget status=RUN pairs=2 failures=2" \
    "womtool validate rc=1" "Required workflow input 'Widget.sample_id' not specified" -- \
    run_jar --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget
want "the control: the same stub passes the fixed tree" 0 \
    "GSVTK-WOMTOOL wf=Widget status=RUN pairs=2 failures=0" -- \
    run_jar --wdl-dir "$TMP/wdl-fix" --inputs-root "$TMP/in-bug" --wf Widget
#    The cross-check must be able to fire: stub says OK, this layer says missing, the gate refuses.
G_FAILDIR=""
want "womtool OK against a tree this check calls broken is a DISAGREES" 1 \
    "DISAGREES" "start there" -- \
    run_jar --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget

# 8. No jar, no java: named, counted skips, each with its fix.
want "no jar: a named, counted skip, not a pass" 1 \
    "GSVTK-WOMTOOL wf=Widget status=SKIPPED pairs=0 failures=0 reason=no-jar" \
    "WOMTOOL_JAR is unset" "counted skip, not a pass" -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget
want "a jar path that is not a file is named, not a stack trace" 1 \
    "reason=jar-missing" "no such jar:" -- \
    env -u JAVA WOMTOOL_JAR="$TMP/nope/womtool.jar" "$PY" "$CHECK" \
        --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget
want "no java is named, with the knob to fix it" 1 \
    "reason=no-java" "on PATH" -- \
    env JAVA="$TMP/no-such-bin/java" WOMTOOL_JAR="$TMP/fake-womtool.jar" "$PY" "$CHECK" \
        --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget

# 9. The gate end to end with --tree / --inputs-root (no clone, ref or jar). The finding reaches the
#    table and exit code; the skip reaches the summary.
want "the gate prints MISSING-INPUTS=1 and fails on the finding alone" 1 \
    "MISSING-INPUTS" "MISSING-INPUT Widget.sample_id" \
    "SKIPPED — 1 of 1 workflow(s): WOMTOOL_JAR is unset" "gate FAILED" -- \
    gate_nojar --tree "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget

#    Without --strict the skip is tolerated; with it, the gate refuses.
want "no jar, no --strict: printed skip, exit 0" 0 \
    "WOMTOOL" "SKIPPED — 1 of 1 workflow(s)" "no hard errors" -- \
    gate_nojar --tree "$TMP/wdl-fix" --inputs-root "$TMP/in-bug" --wf Widget
want "--strict turns the missing-jar skip into a failure" 1 \
    "SKIPPED — 1 of 1 workflow(s)" -- \
    gate_nojar --strict --tree "$TMP/wdl-fix" --inputs-root "$TMP/in-bug" --wf Widget
G_FAILDIR="$TMP/wdl-bug"
want "with a jar in hand, --strict is satisfiable and womtool agrees" 0 \
    "WOMTOOL" "RUN=1 FAIL=0" "--strict is satisfied" -- \
    gate_jar --strict --tree "$TMP/wdl-fix" --inputs-root "$TMP/in-bug" --wf Widget
want "and the gate fails when womtool rejects the pair" 1 \
    "MISSING-INPUT Widget.sample_id" "FAILED — womtool rejected 1 pair(s) of 1" -- \
    gate_jar --tree "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget

#    An unrenderable tree (not a repository, no --inputs-root) prints a counted skip.
want "an unrenderable tree prints a counted INPUTS-JSON skip" 0 \
    "INPUTS-JSON" "SKIPPED — could not render" "no gatk-sv clone" -- \
    gate_nojar --tree "$TMP/wdl-fix" --wf Widget
want "and --strict fails on that skip too" 1 \
    "INPUTS-JSON" "SKIPPED — could not render" -- \
    gate_nojar --strict --tree "$TMP/wdl-fix" --wf Widget

#    A workflow with no input JSON is shown and counted, but does not fail --strict (CI is green on it).
want "no input JSON at all shows as NO-INPUT-JSON and is counted" 0 \
    "NO-INPUT-JSON" "had no CI input JSON to check" -- \
    gate_nojar --tree "$TMP/wdl-bug" --inputs-root "$TMP/in-empty" --wf Widget
want "a workflow absent from the tree is still a failure" 1 \
    "ABSENT at" "nothing was checked" -- \
    gate_nojar --tree "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf NotThere

# 10. The other direction: keys the input JSON names that the WDL never declared. The fixtures are
#     checked-in files because section 11 cross-checks them against womtool's captured words.
#
#     The clean tree uses four legal shapes that a naive "is this a declared workflow input?" test
#     would flag: a call-qualified task input (`Extras.MakeThing.cpu`), a defaulted task input, a flat
#     struct member path (`Extras.attrs.cpu`), and a struct object with an unknown member.
FIX_BAD="$FIXW/extra/inputs/build/ref_panel_1kg/test/Extras/Extras.json"
want "a call-qualified, defaulted or member-path key is not an extra" 0 \
    "GSVTK-EXTRAS wf=Extras pairs=1 extras=0 status=OK" \
    "GSVTK-INPUTS wf=Extras pairs=1 missing=0 status=OK" -- \
    run --wdl-dir "$FIXW/wdl" --inputs-root "$FIXW/clean" --wf Extras

#    The bogus keys, with the required half clean, so the exit code is attributable to extras alone.
want "a key no declaration names is a finding, with the required half clean" 1 \
    "GSVTK-INPUTS wf=Extras pairs=2 missing=0 status=OK" \
    "GSVTK-EXTRAS wf=Extras pairs=2 extras=2 status=FINDING" \
    "EXTRA-KEY Extras.this_key_does_not_exist_in_the_wdl is named by 2 of 2 CI-matched input JSONs" \
    "EXTRA-KEY Extras.MakeThing.cpua is named by 1 of 2 CI-matched input JSONs" -- \
    run --wdl-dir "$FIXW/wdl" --inputs-root "$FIXW/extra" --wf Extras

#    Each extra is attributed to the JSON that carries it ([test] / [terra]).
want "and each extra is attributed to the JSON that actually carries it" 1 \
    "[terra] " "[test] " "Nearest declared key: Extras.MakeThing.cpu" \
    "No declared key is close to it" -- \
    run --wdl-dir "$FIXW/wdl" --inputs-root "$FIXW/extra" --wf Extras

#    --no-terra drops the Terra JSON from the extras answer too.
want "--no-terra drops the Terra JSON from the extras answer too" 1 \
    "GSVTK-EXTRAS wf=Extras pairs=1 extras=2 status=FINDING" \
    "named by 1 of 1 CI-matched input JSON and" -- \
    run --wdl-dir "$FIXW/wdl" --inputs-root "$FIXW/extra" --wf Extras --no-terra

#    Presence, not values, on this half too.
mkdir -p "$TMP/in-xvalues/inputs/build/ref_panel_1kg/test/Extras"
env "${cfgenv[@]-}" "$PY" - "$FIX_BAD" \
    "$TMP/in-xvalues/inputs/build/ref_panel_1kg/test/Extras/Extras.json" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
out = {k: ({"replaced": True} if isinstance(v, dict) else "gs://nowhere-at-all/x")
       for k, v in doc.items()}
json.dump(out, open(sys.argv[2], "w"), indent=2)
PY
want "values that differ change the extras answer not at all" 1 \
    "GSVTK-EXTRAS wf=Extras pairs=1 extras=2 status=FINDING" \
    "Extras.this_key_does_not_exist_in_the_wdl" "Extras.MakeThing.cpua" -- \
    run --wdl-dir "$FIXW/wdl" --inputs-root "$TMP/in-xvalues" --wf Extras

#    The gate fails on extras just like MISSING-INPUTS. A clean cell reads `clean`, not a count.
want "the gate fails on extra keys alone: 2 extra, and the row says so" 1 \
    "EXTRA-KEYS" "2 extra" "FAILED — 2 un-declared key(s) in 1 of 1 workflow(s)" "gate FAILED" \
    "EXTRA-KEY Extras.this_key_does_not_exist_in_the_wdl" -- \
    gate_nojar --tree "$FIXW/wdl" --inputs-root "$FIXW/extra" --wf Extras
want "the control: clean in that column, a CLEAN row, and exit 0" 0 \
    "clean" "CLEAN — 1 workflow(s)' input JSON name no key the WDL does not declare" \
    "no hard errors" -- \
    gate_nojar --tree "$FIXW/wdl" --inputs-root "$FIXW/clean" --wf Extras

#    A prerequisite that stopped the layer is named, counted and fatal under --strict, never `clean`.
want "no input JSON to render: the extras column SKIPS by name and count" 0 \
    "EXTRA-KEYS" "SKIPPED — 1 of 1 workflow(s): no extra-key answer was produced" \
    "not answered clean" -- \
    gate_nojar --tree "$FIXW/wdl" --wf Extras
want "and --strict refuses to certify a skipped extras answer" 1 \
    "EXTRA-KEYS" "SKIPPED — 1 of 1 workflow(s)" -- \
    gate_nojar --strict --tree "$FIXW/wdl" --wf Extras
want "CI's own blind spot is NOT-CHECKED here, never clean" 0 \
    "NO-INPUT-JSON" "NOT-CHECKED" "had no comparable input JSON at this ref" -- \
    gate_nojar --tree "$TMP/wdl-bug" --inputs-root "$TMP/in-empty" --wf Widget

# 11. Cross-check against womtool's own words on one input: a real WDL, its input JSON, and the
#     verbatim womtool-84 `validate` answer (rc=1, one WARNING). What womtool names, this layer must
#     name; what it accepted, this layer must not flag. Only a couple of pairs are pinned because each
#     costs a JVM start; the full sweep is in docs/static-checks.md. Re-derived from a real jar where
#     one exists, otherwise a counted skip.
want "the extras fixtures are present to be graded" 0 "$FIX_BAD" -- \
    env "${cfgenv[@]-}" "$PY" - "$FIX_BAD" "$FIXW/clean/inputs/build/ref_panel_1kg/test/Extras/Extras.json" \
        "$FIXJ/womtool84-extras-crosscheck.validate.txt" "$FIXJ/womtool84-extras-crosscheck.wdl" \
        "$FIXJ/womtool84-extras-crosscheck.inputs.json" \
        "$FIXJ/womtool84-shapematrix-flatmember.validate.txt" \
        "$FIXJ/womtool84-shapematrix-flatmember.inputs.json" <<'PY'
import os, sys
missing = [p for p in sys.argv[1:] if not os.path.isfile(p)]
if missing:
    print("MISSING " + " ".join(missing))
    sys.exit(1)
print(sys.argv[1])
PY
mkdir -p "$TMP/cross/tree" "$TMP/cross/in/inputs/build/ref_panel_1kg/test/CrossCheck"
cp "$FIXJ/womtool84-extras-crosscheck.wdl" "$TMP/cross/tree/CrossCheck.wdl"
cp "$FIXJ/womtool84-extras-crosscheck.inputs.json" \
   "$TMP/cross/in/inputs/build/ref_panel_1kg/test/CrossCheck/CrossCheck.json"
cc_theirs=$(sed -n 's/^WARNING: Unexpected input provided: \([A-Za-z0-9_.]*\).*/\1/p' \
    "$FIXJ/womtool84-extras-crosscheck.validate.txt" | head -1)
cc_ours=$(run --wdl-dir "$TMP/cross/tree" --inputs-root "$TMP/cross/in" --wf CrossCheck 2>&1 \
    | sed -n 's/^  EXTRA-KEY \([A-Za-z0-9_.]*\) is named by.*/\1/p' | head -1)
same "offline (no jar) and captured womtool-84 name the same extra key" "$cc_ours" "$cc_theirs"
want "and the answer quotes womtool's own sentence for that key" 1 \
    "GSVTK-EXTRAS wf=CrossCheck pairs=1 extras=1 status=FINDING" \
    "Unexpected input provided: CrossCheck.this_key_does_not_exist_at_all" -- \
    run --wdl-dir "$TMP/cross/tree" --inputs-root "$TMP/cross/in" --wf CrossCheck
#    The shapes womtool accepted on that file must not come back as extras.
want "the answer on that input names exactly one key, no more" 1 \
    "GSVTK-EXTRAS wf=CrossCheck pairs=1 extras=1 status=FINDING" -- \
    run --wdl-dir "$TMP/cross/tree" --inputs-root "$TMP/cross/in" --wf CrossCheck
want_no "a call-qualified or struct-member key is absent from the findings on that input" 1 \
    "EXTRA-KEY CrossCheck.MakeThing.cpu " "EXTRA-KEY CrossCheck.MakeThing.threads " \
    "EXTRA-KEY CrossCheck.attrs " "EXTRA-KEY CrossCheck.vcf " -- \
    run --wdl-dir "$TMP/cross/tree" --inputs-root "$TMP/cross/in" --wf CrossCheck

# 11b. Pair B is a known divergence: a flat struct member path. womtool-84 calls it unexpected; this
#      layer calls it bound, consistent with required_inputs(). Pinned so it is not mistaken for
#      coverage. Pair C is womtool-84 on a real IntegrateGDVcf test JSON with one bogus key added; it
#      matches our fixture finding up to the namespace prefix.
mkdir -p "$TMP/cross/in2/inputs/build/ref_panel_1kg/test/ShapeProbe"
cp "$FIXJ/womtool84-shapematrix.wdl" "$TMP/cross/tree/ShapeProbe.wdl"
cp "$FIXJ/womtool84-shapematrix-flatmember.inputs.json" \
   "$TMP/cross/in2/inputs/build/ref_panel_1kg/test/ShapeProbe/ShapeProbe.json"
fm_theirs=$(sed -n 's/^WARNING: Unexpected input provided: \([A-Za-z0-9_.]*\).*/\1/p' \
    "$FIXJ/womtool84-shapematrix-flatmember.validate.txt" | head -1)
want "pair B is pinned: womtool's own words name the flat struct member path" 0 \
    "Unexpected input provided: ShapeProbe.struct_opt.label" -- \
    env "${cfgenv[@]-}" "$PY" -c 'import sys; sys.stdout.write(open(sys.argv[1]).read())' \
        "$FIXJ/womtool84-shapematrix-flatmember.validate.txt"
want "pair B: this layer reports nothing extra on that same input" 0 \
    "GSVTK-EXTRAS wf=ShapeProbe pairs=1 extras=0 status=OK" \
    "GSVTK-INPUTS wf=ShapeProbe pairs=1 missing=0 status=OK" -- \
    run --wdl-dir "$TMP/cross/tree" --inputs-root "$TMP/cross/in2" --wf ShapeProbe
want_no "pair B: the key womtool named is NOT in this layer's findings (the under-report)" 0 \
    "EXTRA-KEY ShapeProbe" -- \
    run --wdl-dir "$TMP/cross/tree" --inputs-root "$TMP/cross/in2" --wf ShapeProbe
extra_theirs=$(sed -n 's/^WARNING: Unexpected input provided: \([A-Za-z0-9_.]*\).*/\1/p' \
    "$FIXJ/womtool84-validate-extra-key.txt" | head -1)
case "$extra_theirs" in *.*) extra_tail="${extra_theirs##*.}";; *) extra_tail="$extra_theirs";; esac
# Counted: `same` refuses an empty left side, so zero or two hits fail.
our_hits=$(run --wdl-dir "$FIXW/wdl" --inputs-root "$FIXW/extra" --wf Extras 2>&1 \
    | sed -n "s/^  EXTRA-KEY \([A-Za-z0-9_.]*\.${extra_tail}\) is named by.*/x/p" | wc -l | tr -d ' ')
same "pair C: the real render's captured key is one this layer names, once" "$our_hits" "1"

jar="${WOMTOOL_JAR:-}"; java_bin="${JAVA:-java}"
if [ -n "$jar" ] && [ -f "$jar" ] && command -v "$java_bin" >/dev/null 2>&1; then
    gsvtk_guard_sites "womtool-no-jar"          # everything the no-jar SKIP below withholds, and nothing else
    cross_jar_out=$(env "${cfgenv[@]-}" WOMTOOL_JAR="$jar" JAVA="$java_bin" "$PY" "$CHECK" \
        --wdl-dir "$TMP/cross/tree" --inputs-root "$TMP/cross/in" --wf CrossCheck 2>&1); ccrc=$?
    cc_live=$(printf '%s\n' "$cross_jar_out" \
        | sed -n 's/.*Unexpected input provided: \([A-Za-z0-9_.]*\).*/\1/p' | head -1)
    cc_offline=$(printf '%s\n' "$cross_jar_out" \
        | sed -n 's/^  EXTRA-KEY \([A-Za-z0-9_.]*\) is named by.*/\1/p' | head -1)
    same "with the jar in hand, its own words name the captured key too" "$cc_live" "$cc_theirs"
    same "and this layer's answer on the same file is that key" "$cc_offline" "$cc_theirs"
    if grep -q 'EXTRAS-CONFIRMED' <<< "$cross_jar_out" && [ "$ccrc" -eq 1 ]; then
        ok=$((ok + 1)); printf '  ok    the two mirrors agree out loud, and it still fails (exit 1)\n'
    else
        fail=$((fail + 1))
        printf '  FAIL  agreeing mirrors did not print EXTRAS-CONFIRMED (exit %s)\n' "$ccrc"
        printf '%s\n' "$cross_jar_out" | head -8 | sed 's/^/          /'
    fi
#    Pair B through the live jar: womtool's words reproduce and this layer stays quiet.
    live=$("$java_bin" -jar "$jar" validate "$TMP/cross/tree/ShapeProbe.wdl" \
        -i "$TMP/cross/in2/inputs/build/ref_panel_1kg/test/ShapeProbe/ShapeProbe.json" 2>&1)
    live_key=$(printf '%s\n' "$live" \
        | sed -n 's/.*Unexpected input provided: \([A-Za-z0-9_.]*\).*/\1/p' | head -1)
    same "with the jar in hand, pair B still names the flat member path it names" "$live_key" "$fm_theirs"
#    rc=1 is expected: womtool's own row refuses the pair, so the divergence never costs the gate a
#    finding. The no-jar twin above expects rc=0 for the same file.
    want_no "and this layer still does not report it (the pinned divergence, live)" 1 \
        "EXTRA-KEY ShapeProbe.struct_opt.label" -- \
        env "${cfgenv[@]-}" WOMTOOL_JAR="$jar" JAVA="$java_bin" "$PY" "$CHECK" \
            --wdl-dir "$TMP/cross/tree" --inputs-root "$TMP/cross/in2" --wf ShapeProbe
    gsvtk_guard_sites_end "womtool-no-jar"
else
    printf '  SKIP  womtool: WOMTOOL_JAR=%s, so the cross-check was not re-derived from the jar — both pinned pairs (the agreeing one and the diverging one) were graded against the captured words instead, and nothing above needed java\n' "${jar:-unset}"
    skipped=$((skipped + 2))
    # One region covers both skips; it names the sites this branch withholds.
    gsvtk_note_skip "womtool-no-jar"
fi

# 12. The render destination: a tree this process did not create is not ours to delete.
#
#     `--render-only` deletes its destination before rendering. A render stamps the destination, and
#     a destination is replaced only when the stamp names this tool and the ref being rendered.
#
#     The render itself is real (throwaway `git init` repo, `git archive`, unpack, stamp, render
#     script call). The renderer is a fixture standing in for gatk-sv's: `render.json` holds ref
#     `first`'s values and `render-B.json` ref `HEAD`'s, so the two refs render different bytes and
#     "the refusal did not touch the tree" is a content check. jinja2 is a stub on PYTHONPATH.
#     12.7 runs the real gate; 12.9 renders on an interpreter that cannot import WDL.
FIXR_STAMP=".gsvtk-render-stamp"
#     The fixtures are not in `make syntax`'s SH_FILES, so check the renderer parses here.
want "the fixture renderer parses as shell, which nothing outside this phase checks" 0 "" -- \
    bash -n "$FIXR/scripts/inputs/build_default_inputs.sh"
mkdir -p "$TMP/pylibs/jinja2"
cat > "$TMP/pylibs/jinja2/__init__.py" <<'STUBJINJA'
"""A stub standing in for the jinja2 package, for section 12 of scripts/selftest.d/womtool.sh.

It renders nothing: the fixture renderer imports no module at all. It exists so the prerequisite
probe in checks/wdl_inputs_check.py and checks/wdl_gate.sh answers "an interpreter here has jinja2"
the same way on every machine, which is what keeps the render assertions from turning into a SKIP on
the box that lacks the real package -- a layer that never ran reads exactly like a guard that never
fired, which is the thing this whole repo refuses to ship.
"""
STUBJINJA

FAKE="$TMP/fake-gatk-sv"
mkdir -p "$FAKE"
git init -q "$FAKE" >/dev/null 2>&1
cp -R "$FIXR/." "$FAKE/"
git -C "$FAKE" add -A >/dev/null 2>&1
git -C "$FAKE" -c user.name=gsvtk-selftest -c user.email=selftest@invalid -c commit.gpgsign=false \
    commit -q -m render-A >/dev/null 2>&1
git -C "$FAKE" branch -q first
cp "$FIXR/inputs/values/render-B.json" "$FAKE/inputs/values/render.json"
git -C "$FAKE" add -A >/dev/null 2>&1
git -C "$FAKE" -c user.name=gsvtk-selftest -c user.email=selftest@invalid -c commit.gpgsign=false \
    commit -q -m render-B >/dev/null 2>&1

# Every render leg uses an empty profile and its own work dir, never a real $GSVTK_WORK.
RENDER_ENV=(GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/render-work" PYTHONPATH="$TMP/pylibs")
run_render() { env "${RENDER_ENV[@]-}" "$PY" "$CHECK" "$@"; }
# Print facts about one rendered tree: stamp ref/tool, render_value, which named files survived, and
# whether the shared default destination exists. A missing tree makes it exit nonzero.
render_state() {                     # render_state <work-dir-to-check-default-in|-> <tree> <files-kept...>
    local work="$1" tree="$2"; shift 2
    env "${cfgenv[@]-}" "$PY" - "$tree" "$work" "$FIXR_STAMP" "$@" <<'PY'
import json, os, sys
tree, work, stamp_name = sys.argv[1], sys.argv[2], sys.argv[3]
kept = sys.argv[4:]
stamp = os.path.join(tree, stamp_name)
if os.path.isfile(stamp):
    doc = json.load(open(stamp))
    print("stamp-ref=%s" % (doc.get("ref") or "(none)"))
    print("stamp-tool=%s" % (doc.get("tool") or "(none)"))
else:
    print("stamp=absent")
hits = []
for dirpath, _dirs, files in os.walk(os.path.join(tree, "inputs", "build")):
    for base in sorted(files):
        if base.endswith(".json"):
            hits.append(os.path.join(dirpath, base))
print("jsons=%s" % len(hits))
if hits:
    print("render_value=%s" % json.load(open(sorted(hits)[0])).get("Rendered.render_value"))
else:
    print("render_value=(no rendered JSON)")
for keep in kept:
    print("kept(%s)=%s" % (os.path.basename(keep), os.path.isfile(keep)))
if work == "-":
    print("default-dest=(not asked)")
elif os.path.exists(os.path.join(work, "wdl-inputs")):
    print("default-dest=CREATED")                   # somebody ate the shared default
else:
    print("default-dest=absent")
PY
}

RDEST="$TMP/render-dest"
#     12.1 Render into a destination that does not exist. `jsons=1` shows the renderer really ran.
want "a destination that does not exist renders, and the fixture renderer's JSON is in it" 0 \
    "GSVTK-RENDER status=OK jsons=1 dest=$RDEST" -- \
    run_render --render-only --repo "$FAKE" --ref first --dest "$RDEST"
#     12.2 After a render the stamp exists and names the ref.
same "the stamp names the ref that was rendered" \
    "$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["ref"])' \
        "$RDEST/$FIXR_STAMP" 2>/dev/null)" "first"
same "and the stamp names the tool that wrote it" \
    "$($PY -c 'import json,sys; print(json.load(open(sys.argv[1]))["tool"])' \
        "$RDEST/$FIXR_STAMP" 2>/dev/null)" "checks/wdl_inputs_check.py"
want "and the tree it rendered is the content that ref's values describe" 0 \
    "stamp-ref=first" "stamp-tool=checks/wdl_inputs_check.py" "jsons=1" "render_value=render-A" -- \
    render_state - "$RDEST"

#     12.3 Positive control: a non-empty, un-stamped directory is refused (exit 1, reason, path) and
#     the planted file survives.
mkdir -p "$TMP/dest-planted/inputs"
printf 'somebody else is reading this\n' > "$TMP/dest-planted/inputs/whatever.txt"
want "POSITIVE CONTROL: a non-empty un-stamped destination is refused, by path and in so many words" 1 \
    "GSVTK-RENDER status=REFUSED jsons=0 reason=un-stamped" \
    "$TMP/dest-planted is not empty and carries no gsvtk render stamp" \
    "it will not delete a directory it did not create" \
    "Render into a directory of its own (--dest)" -- \
    run_render --render-only --repo "$FAKE" --ref first --dest "$TMP/dest-planted"
want "and the refused tree is untouched: no stamp, and the planted file is where it was" 0 \
    "stamp=absent" "kept(whatever.txt)=True" -- \
    render_state - "$TMP/dest-planted" "$TMP/dest-planted/inputs/whatever.txt"

#     12.3b A stamp that will not parse, names another tool, names no ref, or has a non-string ref is
#     treated as un-stamped. The last one pins the `isinstance` check (`{"ref": 7}` would otherwise be
#     an `other-ref` refusal).
plant_stamp() {                      # plant_stamp <dir> <stamp-content> — plus one file so it is not empty
    mkdir -p "$1/inputs"
    printf 'planted next to the stamp\n' > "$1/inputs/keep.txt"
    printf '%s\n' "$2" > "$1/$FIXR_STAMP"
}
plant_stamp "$TMP/stamp-foreign" '{"ref": "first", "tool": "some-other-tool"}'
want "a stamp naming another tool is not a claim, even when it names the ref being rendered" 1 \
    "GSVTK-RENDER status=REFUSED jsons=0 reason=un-stamped" \
    "is not empty and carries no gsvtk render stamp" -- \
    run_render --render-only --repo "$FAKE" --ref first --dest "$TMP/stamp-foreign"
plant_stamp "$TMP/stamp-unparseable" 'not json, and not ours'
want "a stamp that will not parse is refused, not a traceback and not a render" 1 \
    "GSVTK-RENDER status=REFUSED jsons=0 reason=un-stamped" -- \
    run_render --render-only --repo "$FAKE" --ref first --dest "$TMP/stamp-unparseable"
plant_stamp "$TMP/stamp-noref" '{"tool": "checks/wdl_inputs_check.py"}'
want "a stamp from this tool that names no ref claims no tree" 1 \
    "GSVTK-RENDER status=REFUSED jsons=0 reason=un-stamped" -- \
    run_render --render-only --repo "$FAKE" --ref first --dest "$TMP/stamp-noref"
plant_stamp "$TMP/stamp-badref" '{"tool": "checks/wdl_inputs_check.py", "ref": 7}'
want "a stamp whose ref is not a string is not a claim either (not an other-ref refusal)" 1 \
    "GSVTK-RENDER status=REFUSED jsons=0 reason=un-stamped" -- \
    run_render --render-only --repo "$FAKE" --ref first --dest "$TMP/stamp-badref"

#     12.4 A tree stamped at the same ref is replaced: a stale JSON planted inside it must be gone.
printf '{\n  "Rendered.render_value": "written-by-hand"\n}\n' \
    > "$RDEST/inputs/build/ref_panel_1kg/test/Rendered/STALE.json"
want "re-rendering the ref the stamp names replaces the tree, as intended" 0 \
    "GSVTK-RENDER status=OK jsons=1" -- \
    run_render --render-only --repo "$FAKE" --ref first --dest "$RDEST"
want "and the replacement really rendered: the hand-written JSON is gone and the rendered one is back" 0 \
    "jsons=1" "render_value=render-A" "kept(STALE.json)=False" -- \
    render_state - "$RDEST" "$RDEST/inputs/build/ref_panel_1kg/test/Rendered/STALE.json"

#     12.5 A destination stamped at another ref is refused, naming both refs.
want "a destination stamped at another ref is refused, naming the stamped ref and the requested one" 1 \
    "GSVTK-RENDER status=REFUSED jsons=0 reason=other-ref" \
    "$RDEST was rendered by this tool at ref first, not ref HEAD" \
    "two refs cannot share one destination" \
    "directory of its own (--dest)" -- \
    run_render --render-only --repo "$FAKE" --ref HEAD --dest "$RDEST"
want "and a refusal renders nothing: the tree still holds the ref it was rendered at" 0 \
    "stamp-ref=first" "render_value=render-A" -- render_state - "$RDEST"

#     12.6 Edge cases: an empty directory renders; a destination that is a file is refused without a
#     traceback.
mkdir -p "$TMP/dest-empty"
want "an empty destination is not a tree anybody is reading, so it renders" 0 \
    "GSVTK-RENDER status=OK jsons=1" -- \
    run_render --render-only --repo "$FAKE" --ref first --dest "$TMP/dest-empty"
: > "$TMP/dest-file"
want_no "a destination that is a file is refused, not a traceback" 1 "Traceback" -- \
    run_render --render-only --repo "$FAKE" --ref first --dest "$TMP/dest-file"
want "and it is refused by name, with the fix beside it" 1 \
    "GSVTK-RENDER status=REFUSED jsons=0 reason=not-a-directory" \
    "is not a directory, so it cannot be a render destination" -- \
    run_render --render-only --repo "$FAKE" --ref first --dest "$TMP/dest-file"

#     12.6b The same refusal through the checking path (the other `render()` caller). The human line
#     says "refused", not SKIPPED; the machine tokens (`status=RENDER-SKIPPED`, `reason=render`) are
#     unchanged.
want "a refusal in the checking path is printed as a refusal" 1 \
    "GSVTK-RENDER status=REFUSED jsons=0 reason=un-stamped" \
    "RENDER-REFUSED no input JSON was read" -- \
    run_render --wdl-dir "$TMP/wdl-fix" --wf Widget --repo "$FAKE" --ref first --dest "$TMP/dest-planted"
want_no "and it does not borrow the word SKIPPED, which belongs to a missing prerequisite" 1 \
    "SKIPPED the input-binding layer" -- \
    run_render --wdl-dir "$TMP/wdl-fix" --wf Widget --repo "$FAKE" --ref first --dest "$TMP/dest-planted"

#     12.7 `checks/wdl_gate.sh` is run against the fake checkout: it renders into
#     `$GSVTK_WORK/wdl-gate/<ref>.inputs` and must not create the shared default. 12.7b plants an
#     un-stamped tree at its destination; it must be refused and left intact. `checks/wdl_reach.py`'s
#     `--dest` is not exercised here (running it needs a full miniwdl load).
GW1="$TMP/work-gate-own"
want_no "the gate still renders now that the renderer owns its destination by stamping it" 0 \
    "could not render the ref's default inputs" "status=REFUSED" -- \
    env -u WOMTOOL_JAR -u JAVA "${cfgenv[@]-}" GSVTK_WORK="$GW1" \
        GSVTK_GATK_SV_CHECKOUT="$FAKE" PYTHONPATH="$TMP/pylibs" \
        bash "$GATE" --tree "$TMP/wdl-fix" --wf Widget first
want "and it rendered into the gate's OWN destination; the shared default was never created" 0 \
    "stamp-ref=first" "render_value=render-A" "default-dest=absent" -- \
    render_state "$GW1" "$GW1/wdl-gate/first.inputs"

GW2="$TMP/work-gate-refused"
mkdir -p "$GW2/wdl-gate/first.inputs/inputs"
printf 'the gate must not delete this\n' > "$GW2/wdl-gate/first.inputs/inputs/keep.txt"
want "a tree the gate did not stamp makes the gate report a refusal, not a clean row" 0 \
    "INPUTS-JSON" "SKIPPED — could not render the ref's default inputs" \
    "status=REFUSED" "reason=un-stamped" -- \
    env -u WOMTOOL_JAR -u JAVA "${cfgenv[@]-}" GSVTK_WORK="$GW2" \
        GSVTK_GATK_SV_CHECKOUT="$FAKE" PYTHONPATH="$TMP/pylibs" \
        bash "$GATE" --tree "$TMP/wdl-fix" --wf Widget first
want "and the gate left the tree it was refused exactly as it found it" 0 \
    "stamp=absent" "kept(keep.txt)=True" "default-dest=absent" -- \
    render_state "$GW2" "$GW2/wdl-gate/first.inputs" "$GW2/wdl-gate/first.inputs/inputs/keep.txt"

#     12.8 The shared default destination, used when no `--dest` is given. Here `default-dest=CREATED`
#     is the pass condition; a run that renders nothing must not create it.
RWD="$TMP/work-default"
want "with no --dest at all the render goes to the shared default, one directory per ref" 0 \
    "GSVTK-RENDER status=OK jsons=1 dest=$RWD/wdl-inputs/first" -- \
    env "${RENDER_ENV[@]-}" GSVTK_WORK="$RWD" "$PY" "$CHECK" \
        --render-only --repo "$FAKE" --ref first
want "and it is the default directory that was stamped, with the ref it rendered" 0 \
    "stamp-ref=first" "stamp-tool=checks/wdl_inputs_check.py" "jsons=1" \
    "render_value=render-A" "default-dest=CREATED" -- \
    render_state "$RWD" "$RWD/wdl-inputs/first"
RWN="$TMP/work-no-render"
want "a --wdl-dir plus --inputs-root run reads JSONs and renders nothing" 0 \
    "GSVTK-INPUTS wf=Widget pairs=2 missing=0 status=OK" -- \
    env "${RENDER_ENV[@]-}" GSVTK_WORK="$RWN" "$PY" "$CHECK" \
        --wdl-dir "$TMP/wdl-fix" --inputs-root "$TMP/in-ok" --wf Widget
want "and that run created no render directory at all: work_path, not work_dir" 0 \
    "stamp=absent" "default-dest=absent" -- render_state "$RWN" "$RWN/wdl-inputs"

#     12.9 The renderer on an interpreter that has jinja2 but cannot import WDL, which is what
#     checks/wdl_gate.sh's `JINJA_PY` probe may hand it. This is the control for the lazy `import WDL`
#     in checks/wdl_inputs_check.py; it names its own interpreter instead of inheriting `$PY`.
#     No skip region: where every interpreter has miniwdl, a `WDL` stub that raises ImportError
#     supplies the absence. The `nowdl-` needles print which interpreter was graded.
NOWDL_PY=""; NOWDL_LIBS="$TMP/pylibs"
for _cand in python3 "$PY" "$ROOT/.venv/bin/python"; do
    [ -n "$_cand" ] || continue
    env PYTHONPATH="$TMP/pylibs" "$_cand" -c 'import jinja2' >/dev/null 2>&1 || continue
    env PYTHONPATH="$TMP/pylibs" "$_cand" -c 'import WDL'  >/dev/null 2>&1 && continue
    NOWDL_PY="$_cand"; break
done
if [ -z "$NOWDL_PY" ]; then
    mkdir -p "$TMP/pylibs-nowdl/WDL" "$TMP/pylibs-nowdl/jinja2"
    cp "$TMP/pylibs/jinja2/__init__.py" "$TMP/pylibs-nowdl/jinja2/__init__.py"
    cat > "$TMP/pylibs-nowdl/WDL/__init__.py" <<'STUBNOWDL'
"""A stand-in for miniwdl being ABSENT, for §12.9 of scripts/selftest.d/womtool.sh.

Raising ImportError on import is what "not installed" looks like to every importer, including the
`python -c 'import WDL'` probes in checks/wdl_gate.sh and in this section. The leg that uses it has to
run on an interpreter that cannot import WDL; where every interpreter on the box can, the fixture
supplies the absence so the leg grades there too instead of skipping over it.
"""
raise ImportError("no miniwdl here (gsvtk selftest fixture: stands for the package being absent)")
STUBNOWDL
    NOWDL_PY="$PY"; NOWDL_LIBS="$TMP/pylibs-nowdl"
fi
# One command so the premise and the verdict are one assertion. GSVTK_RENDER_PYTHON is unset so the
# interpreter cannot be swapped underneath it.
render_without_miniwdl() {
    printf 'nowdl-interpreter=%s\n' "$NOWDL_PY"
    if env PYTHONPATH="$NOWDL_LIBS" "$NOWDL_PY" -c 'import jinja2' >/dev/null 2>&1; then
        printf 'nowdl-jinja2=yes\n'
    else
        printf 'nowdl-jinja2=no\n'
    fi
    if env PYTHONPATH="$NOWDL_LIBS" "$NOWDL_PY" -c 'import WDL' >/dev/null 2>&1; then
        printf 'nowdl-miniwdl=present\n'
    else
        printf 'nowdl-miniwdl=absent\n'
    fi
    env -u GSVTK_RENDER_PYTHON "${RENDER_ENV[@]-}" GSVTK_WORK="$TMP/work-nowdl" \
        PYTHONPATH="$NOWDL_LIBS" "$NOWDL_PY" "$CHECK" \
        --render-only --repo "$FAKE" --ref first --dest "$TMP/render-nowdl"
}
want "POSITIVE CONTROL 12.9: the renderer renders on an interpreter that has jinja2 and no miniwdl" 0 \
    "nowdl-jinja2=yes" "nowdl-miniwdl=absent" \
    "GSVTK-RENDER status=OK jsons=1 dest=$TMP/render-nowdl" -- \
    render_without_miniwdl

# The default --wf list must include IntegrateGDVcf, the workflow that regressed in gatk-sv PR #966.
# Read from the gate's own offline output (one ABSENT row per default name).
want "the default set names the workflow that really regressed (PR #966), so a bare check can catch it" 1 \
    "IntegrateGDVcf" "ABSENT at" -- gate_nojar --tree "$TMP/wdl-fix"

printf '\nwomtool selftest: %s passed, %s failed, %s skipped (a SKIP here is not a pass in CI)\n' \
    "$ok" "$fail" "$skipped"
if [ "$HAS_WDL" = 0 ]; then
    # One summary line for all the skips with a single cause.
    printf 'womtool selftest: the skips above are ONE cause — this interpreter ('"$PY"') cannot import WDL,\n'
    printf '        so the required-input layer read no AST. That is a DEV dependency, not a defect:\n'
    printf '        pip install -r requirements-dev.txt. CI installs it, which is where these run.\n'
fi
# With the jar skip, every unreached site must be inside womtool-no-jar; with a jar, declared and
# reached must be equal.
gsvtk_declared_report "womtool selftest" "$SELF" "$((ok + fail))" "$skipped" || fail=$((fail + 1))
[ "$fail" -eq 0 ] || exit 1
exit 0
