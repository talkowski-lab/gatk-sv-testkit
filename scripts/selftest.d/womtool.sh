#!/usr/bin/env bash
# scripts/selftest.d/womtool.sh — the CI-parity half of the WDL gate, asserted without a jar.
#
#     bash scripts/selftest.d/womtool.sh .venv/bin/python
#
# What this phase owns that nothing else does
# -------------------------------------------
# `checks/wdl_gate.sh` used to run half of gatk-sv's CI and call the result a pass. At `7fbf1171` the
# gate printed `0 IncompleteCall`, exit 0, while CI's "Test with WOMtool" step rejected the same commit
# with `Required workflow input 'IntegrateGDVcf.sample_id' not specified` — a required input the WDL
# declares, the call site binds, and the RENDERED INPUT JSON never carries. miniwdl cannot see that
# because it never opens an input file. `checks/wdl_inputs_check.py` is the other half, and this file is
# the assertions for it: `01107996` makes that input optional, and a check that reports the bug must
# report NOTHING at the fix.
#
# Why stubs, and what they are allowed to prove
# ---------------------------------------------
# **There is no womtool jar on this machine and this file must pass anyway**, offline, with no network
# and no gatk-sv checkout. So the jar layer is driven by a stub `java` (a private PATH entry plus
# `$JAVA`, the same idiom `scripts/selftest.d/cli.sh` uses for `java`/`bcftools`). The stub does not
# reimplement womtool's judgement — it stands in for it: fail with womtool's own words for any WDL
# under one named directory, pass everything else. That is enough to assert the only things this repo
# is responsible for: that rc=1 becomes a FINDING, rc=0 becomes a pass, rc=0 against a tree this repo
# calls broken becomes a DISAGREES, and a missing jar becomes a counted skip that --strict fails on.
# Every assertion names the number or the phrase it needs, because "exit code was acceptable" is how
# the old suite certified a check that never ran.
#
# The WDL fixtures are written into a temp tree and the input JSONs sit in a fake `inputs/build/**`
# layout, so nothing here reads a ref, a checkout, or a real value. Values are the point of three
# assertions: womtool grades key PRESENCE, so two trees differing only in values must give the same
# verdict and two differing in a key must not. bash 3.2 compatible; owns its own tally.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi

CHECK="$ROOT/checks/wdl_inputs_check.py"
GATE="$ROOT/checks/wdl_gate.sh"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/gsvtk-womtool.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"                 # physical: the stub compares paths against these
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"
ok=0; fail=0

# want DESC WANT_RC NEEDLE... -- CMD...
# Exit code AND every needle, because a refusal and a crash can share an exit code, and a verdict you
# cannot read is not a verdict.
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
    if [ "$rc" = "$wantrc" ] && [ -z "$miss" ]; then
        ok=$((ok + 1)); printf '  ok    %s (exit %s)\n' "$desc" "$rc"
    else
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s, want %s)%s\n' "$desc" "$rc" "$wantrc" "$miss"
        printf '%s\n' "$out" | head -10 | sed 's/^/          /'
    fi
}

# canary: this file has its own tally, so it carries its own proof that the tally can go down.
canary() {
    local out rc
    out="$(false 2>&1)"; rc=$?
    if [ "$rc" -ne 0 ]; then
        ok=$((ok + 1)); printf '  ok    this file notices a failing command (canary)\n'
    else
        fail=$((fail + 1)); printf '  FAIL  the harness in %s is vacuous\n' "$0"
    fi
}

# --------------------------------------------------------------------------- fixtures
# One WDL, one required input, and the two ways upstream fixed it: bind the key, or make it optional.
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

# The input JSONs CI would pair with wdl/Widget.wdl: one test JSON (the `find` in validate.sh) and one
# Terra workflow_configuration (the `-t` half). Key sets differ between the trees; VALUES differ only in
# the "values" tree, which is what makes "presence, not values" an assertion instead of a claim.
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

# A workflow whose required input is a STRUCT: Cromwell wants one key per required member, and a
# Terra config may hand the whole struct over as one placeholder instead. Both are OK; neither is a
# key the naive checker would name.
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

# No rendered JSON at all, and a tree that will not parse: both must be reported as "nothing was
# checked", which is a different answer from "clean".
mkdir -p "$TMP/in-empty/inputs/build/ref_panel_1kg/test"
mkdir -p "$TMP/wdl-broken"
printf 'version 1.0\nworkflow Widget { input { File vcf\n' > "$TMP/wdl-broken/Widget.wdl"

# The stub jar, and the stub `java` that runs it. Failing is keyed on WHERE the WDL came from, so the
# same stub answers the broken tree and the fixed tree differently — which is what makes "the fix
# makes it pass" a control rather than a restatement.
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

# Every fixture run says which jar story it wants, so an ambient WOMTOOL_JAR on the runner's shell
# cannot change a verdict: `run` is the no-jar world, `run_jar` the stub-jar world, and the two gate
# wrappers are the same pair at the gate level.
cfgenv=(GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/work"
        GSVTK_GATK_SV_CHECKOUT="$TMP/not-a-checkout")
G_FAILDIR=""
run()      { env -u WOMTOOL_JAR -u JAVA "$PY" "$CHECK" "$@"; }
run_jar()  { env "${cfgenv[@]-}" WOMTOOL_JAR="$TMP/fake-womtool.jar" JAVA="$TMP/bin/java" \
                 WOMTOOL_STUB_FAILDIR="$G_FAILDIR" "$PY" "$CHECK" "$@"; }
gate_nojar() { env -u WOMTOOL_JAR -u JAVA "${cfgenv[@]-}" bash "$GATE" "$@"; }
gate_jar()   { env "${cfgenv[@]-}" WOMTOOL_JAR="$TMP/fake-womtool.jar" JAVA="$TMP/bin/java" \
                 WOMTOOL_STUB_FAILDIR="$G_FAILDIR" bash "$GATE" "$@"; }

# 1. THE BUG. The shape gatk-sv 7fbf1171 shipped: the WDL requires it, the JSON does not carry it,
#    and BOTH of CI's paired JSONs (test + Terra -t) are missing it. Exit 1, not a warning.
want "a required input absent from every matched input JSON is a finding" 1 \
    "GSVTK-INPUTS wf=Widget pairs=2 missing=1 status=FINDING" \
    "MISSING-INPUT Widget.sample_id (String, Widget.wdl:10)" \
    "absent from 2 of 2 CI-matched input JSONs" \
    "[test] " "[terra] " -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget

# 2. THE FIX, TWICE, and both must be silent. Upstream made the input optional; a template could also
#    have bound it. A checker that still reports either one is a checker that manufactures findings.
want "making the input optional clears the finding" 0 \
    "GSVTK-INPUTS wf=Widget pairs=2 missing=0 status=OK" -- \
    run --wdl-dir "$TMP/wdl-fix" --inputs-root "$TMP/in-bug" --wf Widget
want "binding the key in the JSON clears the finding" 0 \
    "GSVTK-INPUTS wf=Widget pairs=2 missing=0 status=OK" -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-ok" --wf Widget

# 3. PRESENCE, NOT VALUES — the claim that lets this run without womtool or a values bundle. Same key
#    set, every value replaced by a path that exists nowhere. Same verdict, or the whole design lie.
want "values that differ (and are unusable) change nothing" 0 \
    "GSVTK-INPUTS wf=Widget pairs=2 missing=0 status=OK" -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-values" --wf Widget
#    ...and the Terra half is nothing but `${this.x}` expressions, which no filesystem can open.
want "a Terra placeholder value is still a bound key" 0 \
    "pairs=2" "status=OK" -- \
    run --wdl-dir "$TMP/wdl-fix" --inputs-root "$TMP/in-bug" --wf Widget

# 4. CI'S OWN PAIRING, both halves. `-t` adds the Terra configs: assert the pair COUNT, because a
#    silently-dropped half would halve the coverage with the same "0 missing" answer.
want "the -t half is paired: 2 JSONs" 1 "pairs=2" -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget
want "--no-terra drops it: 1 JSON" 1 "pairs=1" "absent from 1 of 1 CI-matched input JSON" -- \
    run --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget --no-terra

# 5. STRUCTS. Cromwell wants one key per required member (`opts.cpu`), an optional member is nobody's
#    requirement, and a Terra config may bind the whole struct with one placeholder.
want "a required struct member is a required key" 1 \
    "MISSING-INPUT WidgetStruct.opts.cpu (Int, WidgetStruct.wdl:11)" -- \
    run --wdl-dir "$TMP/wdl-struct" --inputs-root "$TMP/in-struct-no" --wf WidgetStruct
want "binding the struct member flat satisfies it" 0 "status=OK" -- \
    run --wdl-dir "$TMP/wdl-struct" --inputs-root "$TMP/in-struct-ok" --wf WidgetStruct
want "binding the whole struct as one key satisfies its members" 0 "status=OK" -- \
    run --wdl-dir "$TMP/wdl-struct" --inputs-root "$TMP/in-struct-all" --wf WidgetStruct

# 6. NOTHING-CHECKED IS NOT CLEAN. Four different ways to check nothing, each named, each nonzero.
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

# 7. THE JAR LAYER, WITH A STUB. rc=1 must become a finding; the SAME stub must pass the fixed tree —
#    that pair is the control, and it is the half a stub that only ever says "valid" could never show.
G_FAILDIR="$TMP/wdl-bug"
want "womtool rc=1 becomes a finding" 1 \
    "GSVTK-WOMTOOL wf=Widget status=RUN pairs=2 failures=2" \
    "womtool validate rc=1" "Required workflow input 'Widget.sample_id' not specified" -- \
    run_jar --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget
want "the control: the same stub passes the fixed tree" 0 \
    "GSVTK-WOMTOOL wf=Widget status=RUN pairs=2 failures=0" -- \
    run_jar --wdl-dir "$TMP/wdl-fix" --inputs-root "$TMP/in-bug" --wf Widget
#    The cross-check has to be able to fire, or it is decoration: the stub says OK, this mirror says
#    missing, and the gate must refuse to certify either answer.
G_FAILDIR=""
want "womtool OK against a tree this check calls broken is a DISAGREES" 1 \
    "DISAGREES" "start there" -- \
    run_jar --wdl-dir "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget

# 8. NO JAR, NO JAVA, NO CRASH — and no silence either. Three named skips, each carrying its own fix.
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

# 9. THE GATE, end to end, on a tree and JSONs already in hand (--tree / --inputs-root: no clone, no
#    ref, no jar). The finding must reach the table and the exit code; the skip must reach the summary.
want "the gate prints MISSING-INPUTS=1 and fails on the finding alone" 1 \
    "MISSING-INPUTS" "MISSING-INPUT Widget.sample_id" \
    "SKIPPED — 1 of 1 workflow(s): WOMTOOL_JAR is unset" "gate FAILED" -- \
    gate_nojar --tree "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf Widget

#    Without --strict the skip is printed and tolerated; with it, the gate refuses. That pair is what
#    makes "never a silent skip" a tested property instead of an intention.
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

#    A tree that cannot be rendered produces a named counted line, never a quiet green: the "checkout"
#    here is a directory that is not a repository, and --inputs-root was deliberately left out.
want "an unrenderable tree prints a counted INPUTS-JSON skip" 0 \
    "INPUTS-JSON" "SKIPPED — could not render" "no gatk-sv clone" -- \
    gate_nojar --tree "$TMP/wdl-fix" --wf Widget
want "and --strict fails on that skip too" 1 \
    "INPUTS-JSON" "SKIPPED — could not render" -- \
    gate_nojar --strict --tree "$TMP/wdl-fix" --wf Widget

#    The CI blind spot: a workflow with no input JSON at all is a printed column and a closing note,
#    not a silent 0 — and it does NOT fail --strict, because CI is green on such a ref.
want "no input JSON at all shows as NO-INPUT-JSON and is counted" 0 \
    "NO-INPUT-JSON" "had no CI input JSON to check" -- \
    gate_nojar --tree "$TMP/wdl-bug" --inputs-root "$TMP/in-empty" --wf Widget
want "a workflow absent from the tree is still a failure" 1 \
    "ABSENT at" "nothing was checked" -- \
    gate_nojar --tree "$TMP/wdl-bug" --inputs-root "$TMP/in-bug" --wf NotThere

printf '\nwomtool selftest: %s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ] || exit 1
exit 0
