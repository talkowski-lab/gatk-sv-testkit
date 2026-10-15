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
# Both directions, and why the second one is allowed to block a run
# ------------------------------------------------------------------
# §10-§11 are the mirror question: a key the input JSON names that the WDL never declared, which is
# womtool's `Unexpected input provided` and the second thing gatk-sv CI fails on. It is a hard finding in
# the gate like the required half, and the licence for that is a measurement, not a preference: this
# layer's expected-key set is a SUPERSET of `womtool inputs` for 109/109 workflows with a primary
# callable at each of gatk-sv origin/main, 7fbf1171 and 01107996, so a key it calls extra is a key womtool
# calls extra too -- it can under-report, it cannot invent a finding. The fixtures are the four legal
# shapes it must keep quiet about (call-qualified task input, defaulted task input, flat struct member
# path, unknown member inside a struct object) and the two bogus ones it must name; §11 pins the answer to
# womtool's own captured words on the same input, including the one case where the two deliberately
# disagree. Numbers, and the four under-reports that come with it: docs/static-checks.md.
#
# Fixtures this phase owns (none of them needs a checkout, a jar or a network to grade)
# ------------------------------------------------------------------------------------
#   scripts/selftest.d/fixtures/womtool/wdl/Extras.wdl          the four legal shapes, in one WDL
#   .../womtool/clean/inputs/...                               those shapes only: extras=0
#   .../womtool/extra/inputs/...                               the same plus two bogus keys, test + -t
#   scripts/selftest.d/fixtures/jar/womtool84-extras-crosscheck.{wdl,inputs.json,validate.txt}
#                          pair A of the cross-check: womtool-84's verbatim answer to one input, and the
#                          one key it names in it
#   .../jar/womtool84-shapematrix-flatmember.{inputs.json,validate.txt}
#                          pair B: the pinned divergence, womtool's words on a flat struct member path
#   .../jar/womtool84-validate-extra-key.txt                   pair C (already here): the same class of
#                          key, captured off a REAL rendered gatk-sv input JSON at 01107996
#   scripts/selftest.d/fixtures/womtool/render/                §12's stand-in for gatk-sv's renderer:
#                          `scripts/inputs/build_default_inputs.sh` plus the two `inputs/values/` files
#                          the two fake refs render from (see §12 for what is fixture and what is real)
#
# The render destination, and the guard that has to be able to fire (§12)
# -----------------------------------------------------------------------
# `--render-only` deletes its destination before it renders, and the default destination is one directory
# per ref under the shared work dir. It now stamps the tree it is about to own, and replaces a destination
# only when that stamp names the ref being rendered: an un-stamped non-empty tree is refused, because a
# tool that deletes directories it never made is two callers stomping each other's rendered input JSONs.
# The rule's full argument is in `checks/wdl_inputs_check.py`'s module docstring; what lives HERE that
# nothing else does is the POSITIVE CONTROL (a non-empty un-stamped directory planted at the destination,
# which the refusing branch has to be reachable at), the four arms that make a stamp file a CLAIM rather
# than a filename (foreign tool, unparseable, no ref, ref not a string — each gets its own leg, because an
# arm no test can reach is an arm that can be deleted in silence), the stamped-same-ref replacement proved
# by a content change rather than by a log line, the shared default destination in both directions (a run
# with no `--dest` must render and stamp `$WORK/wdl-inputs/<ref>`, and a run that renders nothing must
# create no directory at all), and one real caller — `checks/wdl_gate.sh` run twice against a throwaway
# checkout, once clean and once with someone else's tree planted where the gate would render. It also owns
# POSITIVE CONTROL 12.9: the renderer run on an interpreter that HAS jinja2 and CANNOT import WDL, which
# is the interpreter `checks/wdl_gate.sh`'s own `JINJA_PY` probe hands it. Every other leg in this file
# runs on `$PY`, so a phase that greened `.venv/bin/python` could not see a defect that only bites an
# interpreter without miniwdl — and `make test` drives this file with `python3` (`Makefile:13`), which is
# precisely such an interpreter here. That is how the gate came to print `SKIPPED — could not render the
# ref's default inputs: Traceback … import WDL` while three §12.7 legs went red, and how `make test` sat
# red from cfd0184 through two reviews that both ran the venv.
# `checks/wdl_reach.py` also passes its own `--dest` and that is SOURCE-READ ONLY: running it is a
# full-tree miniwdl load, which is not something a phase graded offline may spend. The `jinja2` the gate
# legs probe for is a stub on PYTHONPATH, because the fixture renderer imports nothing and a machine
# without the real package must grade this section the same way one with it does — a layer that never ran
# reads exactly like a guard that never fired. The same reasoning is why 12.9 will fabricate the ABSENCE of
# miniwdl (a `WDL` stub that raises ImportError) rather than skip, on a box where every interpreter has it.
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
# Absolute, and exported under the knob `kit/config.sh` and `gsvtk` both honor. This suite drives
# checks/wdl_gate.sh directly, and the gate resolves its own interpreter: without this it took "python3 on
# PATH" and then looked for miniwdl in $ROOT/.venv/bin, so every gate-backed assertion below was really
# testing whether whoever ran it keeps a venv inside the checkout. On a checkout without one they did not
# skip — they died on "miniwdl not found", fifteen at a time, which is a red suite telling you nothing
# about the code.
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
FIXR="$FIXW/render"                                  # §12: the fixture renderer and its values files

TMP="$(mktemp -d "${TMPDIR:-/tmp}/gsvtk-womtool.XXXXXX")"
TMP="$(cd "$TMP" && pwd -P)"                 # physical: the stub compares paths against these
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/empty.env"
ok=0; fail=0; skipped=0

# ------------------------------------------------- declared vs reached (docs/gap-ledger.md C10)
# Both numbers count assertion call SITES, which is what lets this phase compare them at all in a file whose
# verdict total moves with the environment. Measured on this box, no WOMTOOL_JAR: 79 passed, 2 counted
# skips, and 79 of 83 sites reached with the other four printed by line number. The jar-in-hand mode this
# box cannot run adds 5 verdicts at 4 sites that §12 does not touch — §12's 29 sites sit outside every skip
# region, so they are reached in both modes — which makes that mode 84 verdicts against 83 of 83, DERIVED
# from the 55-of-54 measured before §12 existed rather than re-measured here. The 84 verdicts against 83
# sites are not an error: the agreeing-mirrors check below records its verdict at top level rather than
# through a recorder, and the reporter prints both totals so that gap is visible instead of being smoothed
# into one number. The callees are what gsvtk_declared_funset derives from this file, and the reporter
# re-derives them every run. The ledger lives in this phase's temp dir, so the
# EXIT trap above already owns it; bash 3.2 has no associative arrays, so that file IS the set of sites this
# run reached.
SELF="$ROOT/scripts/selftest.d/womtool.sh"
. "$ROOT/scripts/selftest.d/declared.sh"
GSVTK_DECL_FUNCS="want want_no canary same"
gsvtk_declared_init "$TMP"

# want DESC WANT_RC NEEDLE... -- CMD...
# Exit code AND every needle, because a refusal and a crash can share an exit code, and a verdict you
# cannot read is not a verdict.
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
    gsvtk_note_site "${BASH_LINENO[0]}"   # see want() above
    local out rc
    out="$(false 2>&1)"; rc=$?
    if [ "$rc" -ne 0 ]; then
        ok=$((ok + 1)); printf '  ok    this file notices a failing command (canary)\n'
    else
        fail=$((fail + 1)); printf '  FAIL  the harness in %s is vacuous\n' "$0"
    fi
}

# want_no DESC WANT_RC ABSENT... -- CMD...   ...and NONE of the ABSENT strings may appear ANYWHERE in
# the output. Absence is the harder half of an extras check: "the bogus key was reported" is worthless
# unless the four legal shapes were NOT, and a needle-only suite cannot say that.
want_no() {
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
    if [ "$rc" = "$wantrc" ] && [ -z "$hit" ]; then
        ok=$((ok + 1)); printf '  ok    %s (exit %s)\n' "$desc" "$rc"
    else
        fail=$((fail + 1))
        printf '  FAIL  %s (exit %s, want %s)%s\n' "$desc" "$rc" "$wantrc" "$hit"
        printf '%s\n' "$out" | head -10 | sed 's/^/          /'
    fi
}

# same DESC GOT WANT -- for answers that have to be COMPUTED from two sources and compared, which no
# fixed-needle grep can express: "the key this layer names is the key womtool named" is that shape. The
# non-empty test is part of the assertion: two empty strings agreeing is exactly the vacuous pass this
# suite exists to refuse.
same() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see want() above
    local desc="$1" got="$2" wantv="$3"
    if [ -n "$got" ] && [ "$got" = "$wantv" ]; then
        ok=$((ok + 1)); printf '  ok    %s (%s)\n' "$desc" "$got"
    else
        fail=$((fail + 1))
        printf '  FAIL  %s\n          got:  %s\n          want: %s\n' "$desc" "${got:-(nothing)}" "$wantv"
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

# 10. THE OTHER DIRECTION: keys the input JSON names that the WDL never declared. This is the half the
#     layer was blind to exactly as A12 was blind on the required side, and the fixtures are the four
#     shapes that make it safe to let it block a run. They are checked-in files, not heredocs, because
#     the same pair of files is also the cross-check against womtool's own words below (§11), and a
#     capture has to be pinned to bytes somebody can re-run.
#
#     The clean tree binds a call-qualified task input (`Extras.MakeThing.cpu`), a DEFAULTED task input,
#     a struct member path written flat (`Extras.attrs.cpu`), and a struct handed over as an object with
#     an unknown member in it. A naive "is this key a declared workflow input?" test calls all four
#     extras; on gatk-sv's own trees those four shapes are what 4 and 8 of the 77 CI-matched JSONs
#     actually do, so a check that called them extras would be noise on every clean ref there is.
FIX_BAD="$FIXW/extra/inputs/build/ref_panel_1kg/test/Extras/Extras.json"
want "a call-qualified, defaulted or member-path key is not an extra" 0 \
    "GSVTK-EXTRAS wf=Extras pairs=1 extras=0 status=OK" \
    "GSVTK-INPUTS wf=Extras pairs=1 missing=0 status=OK" -- \
    run --wdl-dir "$FIXW/wdl" --inputs-root "$FIXW/clean" --wf Extras

#    The bogus ones, with the required half CLEAN in the same run: that is what makes the exit code here
#    attributable to the extras half alone -- the same assertion on a tree that also has a missing input
#    would pass for the wrong reason.
want "a key no declaration names is a finding, with the required half clean" 1 \
    "GSVTK-INPUTS wf=Extras pairs=2 missing=0 status=OK" \
    "GSVTK-EXTRAS wf=Extras pairs=2 extras=2 status=FINDING" \
    "EXTRA-KEY Extras.this_key_does_not_exist_in_the_wdl is named by 2 of 2 CI-matched input JSONs" \
    "EXTRA-KEY Extras.MakeThing.cpua is named by 1 of 2 CI-matched input JSONs" -- \
    run --wdl-dir "$FIXW/wdl" --inputs-root "$FIXW/extra" --wf Extras

#    Per-pair attribution, not a union: the typo is in the test JSON only, the invented key in both, and
#    the [test]/[terra] tags below each line are the only thing that tells a reader which file to edit.
want "and each extra is attributed to the JSON that actually carries it" 1 \
    "[terra] " "[test] " "Nearest declared key: Extras.MakeThing.cpu" \
    "No declared key is close to it" -- \
    run --wdl-dir "$FIXW/wdl" --inputs-root "$FIXW/extra" --wf Extras

#    The -t half again, this time for the extras answer: dropping it must halve the PAIRS and still find
#    both keys, in exactly one file each.
want "--no-terra drops the Terra JSON from the extras answer too" 1 \
    "GSVTK-EXTRAS wf=Extras pairs=1 extras=2 status=FINDING" \
    "named by 1 of 1 CI-matched input JSON and" -- \
    run --wdl-dir "$FIXW/wdl" --inputs-root "$FIXW/extra" --wf Extras --no-terra

#    Presence, not values, holds on this half as much as the other: the same keys with every value
#    replaced by a path that exists nowhere must give the same two findings.
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

#    THE GATE, both halves of the column. Hard either way, like MISSING-INPUTS: an extra this layer names
#    is an extra womtool names (the superset measurement in the module docstring), and CI is red on it,
#    so a clean cell is the only passable answer and `clean` is spelled differently from a count on
#    purpose -- the column beside it already answers a different question with bare numbers.
want "the gate fails on extra keys alone: 2 extra, and the row says so" 1 \
    "EXTRA-KEYS" "2 extra" "FAILED — 2 un-declared key(s) in 1 of 1 workflow(s)" "gate FAILED" \
    "EXTRA-KEY Extras.this_key_does_not_exist_in_the_wdl" -- \
    gate_nojar --tree "$FIXW/wdl" --inputs-root "$FIXW/extra" --wf Extras
want "the control: clean in that column, a CLEAN row, and exit 0" 0 \
    "clean" "CLEAN — 1 workflow(s)' input JSON name no key the WDL does not declare" \
    "no hard errors" -- \
    gate_nojar --tree "$FIXW/wdl" --inputs-root "$FIXW/clean" --wf Extras

#    A prerequisite that stopped the layer must not read as `clean` here any more than it does in the
#    WOMTOOL row: named, counted, and fatal under --strict.
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

# 11. THE CROSS-CHECK: this layer's answer and womtool's own words, on ONE input file. The three fixture
#     files are a real WDL, the input JSON beside it, and the VERBATIM `java -jar womtool-84.jar
#     validate` answer for that pair (rc=1, one WARNING). On that same input womtool accepted a
#     call-qualified task input, a defaulted one, and a struct object carrying an unknown member, and
#     complained about exactly one key -- so this pair pins both directions at once: what it names, this
#     layer must name; what it waved through, this layer must not invent.
#
#     TWO PAIRS, NOT 77, ON PURPOSE. Each pair is a JVM start (~1.2 s) and the 77-pair sweep over three
#     refs was already run out of band (measure4: `pairs_with_'unexpected_input'=0` at every ref, in
#     docs/static-checks.md); re-running it here would cost ~15 minutes of wall clock to say the same
#     sentence. What generalises a two-pair pin is not the pair count, it is the measured relation
#     between the two expected sets: this file's set is a SUPERSET of `womtool inputs` for 109/109
#     workflows with a primary callable at all three refs, so any key this layer calls extra is a key
#     womtool calls extra too, at any input. Pinned below against the captures (no java needed), and
#     re-derived from a real jar where one exists -- with a named, counted skip where there is none.
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
#    The same input through the four shapes womtool waved through on that very file: none of them may
#    come back as an extra, or the finding above is worthless.
want "the answer on that input names exactly one key, no more" 1 \
    "GSVTK-EXTRAS wf=CrossCheck pairs=1 extras=1 status=FINDING" -- \
    run --wdl-dir "$TMP/cross/tree" --inputs-root "$TMP/cross/in" --wf CrossCheck
want_no "a call-qualified or struct-member key is absent from the findings on that input" 1 \
    "EXTRA-KEY CrossCheck.MakeThing.cpu " "EXTRA-KEY CrossCheck.MakeThing.threads " \
    "EXTRA-KEY CrossCheck.attrs " "EXTRA-KEY CrossCheck.vcf " -- \
    run --wdl-dir "$TMP/cross/tree" --inputs-root "$TMP/cross/in" --wf CrossCheck

# 11b. PAIR B OF THE CROSS-CHECK, and it is the pair that does not agree: the shape-matrix WDL with a
#      struct member path written flat as its own key. womtool-84's captured words call that key
#      unexpected; this layer calls it bound, because required_inputs() already accepts that spelling and
#      a tool that called the same key bound in one half and extra in the other would be unreadable. That
#      is a MEASURED UNDER-REPORT, pinned here so it cannot quietly become a claimed coverage.
#      PAIR C is the real rendered tree, pinned by its captured words alone (verbatim womtool-84 on the
#      real IntegrateGDVcf test JSON at 01107996 with one bogus key added), because a selftest must not
#      need a gatk-sv clone: the key it names and the key our fixture finding names differ only in the
#      namespace prefix, which is the same finding on a differently-named workflow.
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
# Counted, not grepped-as-present: `same` refuses an empty left side, so a findings list that names the
# captured key zero times or twice fails this instead of quietly agreeing.
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
    if printf '%s\n' "$cross_jar_out" | grep -q 'EXTRAS-CONFIRMED' && [ "$ccrc" -eq 1 ]; then
        ok=$((ok + 1)); printf '  ok    the two mirrors agree out loud, and it still fails (exit 1)\n'
    else
        fail=$((fail + 1))
        printf '  FAIL  agreeing mirrors did not print EXTRAS-CONFIRMED (exit %s)\n' "$ccrc"
        printf '%s\n' "$cross_jar_out" | head -8 | sed 's/^/          /'
    fi
    #    Pair B through the live jar: the pinned words reproduce, and this layer still stays quiet about
    #    it. That is the divergence asserted in BOTH directions, on the tool itself rather than on a file.
    live=$("$java_bin" -jar "$jar" validate "$TMP/cross/tree/ShapeProbe.wdl" \
        -i "$TMP/cross/in2/inputs/build/ref_panel_1kg/test/ShapeProbe/ShapeProbe.json" 2>&1)
    live_key=$(printf '%s\n' "$live" \
        | sed -n 's/.*Unexpected input provided: \([A-Za-z0-9_.]*\).*/\1/p' | head -1)
    same "with the jar in hand, pair B still names the flat member path it names" "$live_key" "$fm_theirs"
    #    rc=1 here is the point, not an accident: the offline half stays quiet about a flat struct member
    #    path and womtool's own row refuses the pair, so the divergence costs a finding only to this
    #    mirror and never to the gate. The no-jar twin of this assertion above expects rc=0 for the same
    #    file, which is the same fact seen from the other side.
    want_no "and this layer still does not report it (the pinned divergence, live)" 1 \
        "EXTRA-KEY ShapeProbe.struct_opt.label" -- \
        env "${cfgenv[@]-}" WOMTOOL_JAR="$jar" JAVA="$java_bin" "$PY" "$CHECK" \
            --wdl-dir "$TMP/cross/tree" --inputs-root "$TMP/cross/in2" --wf ShapeProbe
    gsvtk_guard_sites_end "womtool-no-jar"
else
    printf '  SKIP  womtool: WOMTOOL_JAR=%s, so the cross-check was not re-derived from the jar — both pinned pairs (the agreeing one and the diverging one) were graded against the captured words instead, and nothing above needed java\n' "${jar:-unset}"
    skipped=$((skipped + 2))
    # ONE region for TWO counted skips: the count is this file's business (the pair only requires that fewer
    # regions fire than it counted), and the region is the unit the guard prices — it names the sites this
    # branch withholds, which are four call sites, not two, and were last printed as "4 did not run".
    gsvtk_note_skip "womtool-no-jar"
fi

# 12. THE RENDER DESTINATION: a tree this process did not create is not ours to delete.
#
#     `--render-only` deletes its destination before it renders, and the default destination is one
#     directory per ref under the shared work dir. What used to decide whether the delete happened was
#     `os.path.isdir(dest)` and nothing else, so a run removed whatever it found — another tool's tree,
#     a render an older release left behind, the directory a typo'd --dest pointed at. The rule now is
#     that a render stamps the destination it is about to own, and a destination is replaced only when
#     the stamp names this tool AND the ref being rendered.
#
#     WHICH LEG IS A FIXTURE AND WHICH IS REAL, because the difference decides what these assertions
#     can prove. Everything about the render is real: a `git init`'d throwaway repository, `git archive`
#     of `inputs/` + `scripts/inputs/`, the `tar`-unpack into the destination, the stamp, the
#     `bash <render script> -d .` call, and the JSON count. What is a fixture is UPSTREAM: the renderer at
#     `$FIXR/scripts/inputs/build_default_inputs.sh` stands in for gatk-sv's own, which a selftest machine
#     does not have. `inputs/values/render.json` is ref `first`'s values and `render-B.json` is what the
#     phase installs for ref `HEAD`, so the two refs render DIFFERENT BYTES — which is what lets "the
#     refusal did not touch the tree" be a content assertion rather than a hope. The `jinja2` that
#     `render()` and the gate probe for is a stub on PYTHONPATH: the fixture renderer never imports it,
#     and stubbing the probe is what makes this section grade the same way on a machine that has jinja2
#     and one that does not (the gate would otherwise SKIP the whole layer, and a guard that never runs
#     reads exactly like a guard that never fired).
#
#     WHAT THIS SECTION OWENS BEYOND THE STAMP RULE, since two of its legs are about the render layer's
#     PREREQUISITES rather than about its destination: §12.7 runs the real caller twice (clean, and with
#     somebody else's tree planted at the gate's render destination), and §12.9 runs the renderer on an
#     interpreter that has jinja2 and cannot import WDL at all — the interpreter the gate's `JINJA_PY`
#     probe is allowed to hand it — and requires it to render. That leg is the control for the lazy
#     `import WDL` in `checks/wdl_inputs_check.py`; without it the whole section could be graded on an
#     interpreter that has miniwdl and never notice that the gate's own interpreter could not import the
#     file at all.
FIXR_STAMP=".gsvtk-render-stamp"
#     Fixtures are outside `SH_FILES` (`Makefile:19` matches `scripts/selftest.d/*.sh`, not the fixtures
#     directory), so `make syntax` never parses this file — the phase is the only thing that will notice a
#     syntax error in it, and it notices here, before a broken renderer looks like a render-layer defect.
#     An empty needle: `bash -n` prints nothing on success, so the exit code IS the assertion.
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

# Every render leg runs against an empty profile and a work dir of its own, because the one thing
# under test is which directory gets deleted: it must never be a real $GSVTK_WORK.
RENDER_ENV=(GSVTK_CONFIG="$TMP/empty.env" GSVTK_WORK="$TMP/render-work" PYTHONPATH="$TMP/pylibs")
run_render() { env "${RENDER_ENV[@]-}" "$PY" "$CHECK" "$@"; }
# Reads one rendered tree and prints what is true about it: the ref and tool the stamp names (or that
# there is no stamp), the render_value the tree holds, whether named files survived, and whether the
# shared work-dir default exists at all. It is a reporter, not a judge: the NEEDLES in each `want`
# decide what has to be true, and a tree that cannot be read makes this exit nonzero (a traceback) so a
# missing tree can never satisfy an assertion by saying nothing.
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
#     12.1 A render into a destination that is not there at all: the baseline the rest of the section
#     modifies. `jsons=1` is the anti-vacuity number — the fixture renderer really ran and really wrote.
want "a destination that does not exist renders, and the fixture renderer's JSON is in it" 0 \
    "GSVTK-RENDER status=OK jsons=1 dest=$RDEST" -- \
    run_render --render-only --repo "$FAKE" --ref first --dest "$RDEST"
#     12.2 THE STAMP ROUND-TRIP: after a real render the stamp exists and says what the tool claims.
#     `same` refuses an empty left side, so a stamp that was never written, or was written unreadable,
#     fails here instead of agreeing with a missing value.
same "the stamp names the ref that was rendered" \
    "$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1]))["ref"])' \
        "$RDEST/$FIXR_STAMP" 2>/dev/null)" "first"
same "and the stamp names the tool that wrote it" \
    "$($PY -c 'import json,sys; print(json.load(open(sys.argv[1]))["tool"])' \
        "$RDEST/$FIXR_STAMP" 2>/dev/null)" "checks/wdl_inputs_check.py"
want "and the tree it rendered is the content that ref's values describe" 0 \
    "stamp-ref=first" "stamp-tool=checks/wdl_inputs_check.py" "jsons=1" "render_value=render-A" -- \
    render_state - "$RDEST"

#     12.3 THE POSITIVE CONTROL, the leg that makes the refusal a guard rather than decoration: a
#     non-empty directory that carries no stamp, which is the exact shape of "a tree somebody else
#     made". Exit 1, the machine reason, the path, and the plain words "will not delete a directory it
#     did not create" — and the planted file still there, because a refusal that deletes anyway is a
#     refusal that lies.
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

#     12.3b WHAT MAKES A STAMP A CLAIM. `stamp_ref` refuses a stamp that will not parse, one naming a
#     different tool, one naming no ref, and one whose ref is not a string — those four arms are the whole
#     reason a hand-written `.gsvtk-render-stamp` is not provenance, and an arm nobody can reach is an arm
#     that can be deleted without anything noticing (CONTRIBUTING: a guard that can never fire passes
#     forever). Each leg plants its own shape and requires the SAME `reason=un-stamped`, because each of
#     them is a tree this tool cannot vouch for. The last one is the sharpest: drop the `isinstance` check
#     and `{"ref": 7}` becomes an `other-ref` refusal instead, so this leg is what pins that check.
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

#     12.4 A tree the tool DID stamp, at the ref the stamp names, is ours to replace — that is what a
#     re-render means, and this leg is what stops the guard from having silently become "render once".
#     The proof is a content change this phase causes by hand: a stale JSON planted inside the stamped
#     tree must be gone, and `jsons=1` (not 2) is the same fact counted.
printf '{\n  "Rendered.render_value": "written-by-hand"\n}\n' \
    > "$RDEST/inputs/build/ref_panel_1kg/test/Rendered/STALE.json"
want "re-rendering the ref the stamp names replaces the tree, as intended" 0 \
    "GSVTK-RENDER status=OK jsons=1" -- \
    run_render --render-only --repo "$FAKE" --ref first --dest "$RDEST"
want "and the replacement really rendered: the hand-written JSON is gone and the rendered one is back" 0 \
    "jsons=1" "render_value=render-A" "kept(STALE.json)=False" -- \
    render_state - "$RDEST" "$RDEST/inputs/build/ref_panel_1kg/test/Rendered/STALE.json"

#     12.5 One destination, two refs: refused, with BOTH refs in the message, because "which one is in
#     here" is the question a reader arriving here has to answer without opening the directory.
want "a destination stamped at another ref is refused, naming the stamped ref and the requested one" 1 \
    "GSVTK-RENDER status=REFUSED jsons=0 reason=other-ref" \
    "$RDEST was rendered by this tool at ref first, not ref HEAD" \
    "two refs cannot share one destination" \
    "directory of its own (--dest)" -- \
    run_render --render-only --repo "$FAKE" --ref HEAD --dest "$RDEST"
want "and a refusal renders nothing: the tree still holds the ref it was rendered at" 0 \
    "stamp-ref=first" "render_value=render-A" -- render_state - "$RDEST"

#     12.6 The two edges the rule has to answer without deleting or crashing. An empty directory is
#     nothing to lose, so it renders; a destination that is a FILE used to reach `os.makedirs` and die
#     on FileExistsError, so this leg also pins "no traceback" (the old shape raised through argparse).
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

#     12.6b THE SAME REFUSAL THROUGH THE CHECKING PATH, which is the other `render()` caller. This repo
#     reserves the word SKIPPED for a prerequisite that could not run — no jar, no jinja2, no clone — and a
#     refusal is the opposite of that: the renderer ran, looked at the destination, and said no. The
#     machine tokens (`status=RENDER-SKIPPED`, `reason=render`) are untouched on purpose: they report what
#     happened to the layer, which was skipped, and nothing parses them for a pass. It is the human line
#     that used to read `SKIPPED … the renderer REFUSED`, which is one sentence contradicting the other.
want "a refusal in the checking path is printed as a refusal" 1 \
    "GSVTK-RENDER status=REFUSED jsons=0 reason=un-stamped" \
    "RENDER-REFUSED no input JSON was read" -- \
    run_render --wdl-dir "$TMP/wdl-fix" --wf Widget --repo "$FAKE" --ref first --dest "$TMP/dest-planted"
want_no "and it does not borrow the word SKIPPED, which belongs to a missing prerequisite" 1 \
    "SKIPPED the input-binding layer" -- \
    run_render --wdl-dir "$TMP/wdl-fix" --wf Widget --repo "$FAKE" --ref first --dest "$TMP/dest-planted"

#     12.7 THE CALLER THAT IS ASSERTED, and the one that is not. `checks/wdl_gate.sh` renders
#     `$GSVTK_WORK/wdl-gate/<ref>.inputs` beside the WDL tree it fetches, and these two legs RUN it against
#     the fake checkout, so the destination it asks for is created by a real render and the shared default
#     is asserted not to exist. `checks/wdl_reach.py` also passes its own `--dest`
#     (`reach-images/render-<ref>`) and that is SOURCE-READ ONLY, at `render_inputs` — running it would mean
#     a full-tree miniwdl load in a phase that is graded offline, and `scripts/selftest.d/reach.sh` always
#     passes `--inputs-root`, so it never renders at all. Say which is which rather than claiming the pair.
#     Drop `--dest` from the gate and 12.7a goes red on `default-dest=CREATED`; plant an un-stamped tree at
#     the gate's own destination and 12.7b goes red by losing its refusal — and in both cases the tree it
#     was refused is asserted intact, because the gate is the caller that would have done the deleting.
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

#     12.8 THE SHARED DEFAULT ITSELF — the destination the requirement is actually about, and the one every
#     leg above avoids by naming a `--dest`. The gate legs' `default-dest=absent` proves only the negative
#     half (a caller that passes `--dest` never touches it), so nothing yet proved the default still WORKS,
#     which is the half a caller without a `--dest` depends on. Two work dirs of their own, so neither leg
#     can be satisfied by the other's directories — and note the inversion: here `default-dest=CREATED` is
#     the pass condition, because that run really did render. It is the defect only when a run renders
#     nothing, which is what the second pair of legs pins.
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

#     12.9 THE RENDERER ON THE INTERPRETER THE GATE'S PROBE HANDS IT — the leg that decides whether
#     §12.7's green gate means "the render layer ran" or "the render layer was skipped, and the leg that
#     would have noticed was never written". `checks/wdl_gate.sh` picks its rendering interpreter by
#     asking each candidate to `import jinja2` and NOTHING else (its own comment: the two layers want
#     different packages), so the interpreter it hands `--render-only` may never have seen miniwdl — and
#     on a machine with no venv, that is `python3`. This leg uses exactly that interpreter: one that can
#     import jinja2 (the stub above, so the choice does not depend on what this box installed) and
#     CANNOT import WDL, asked to render, and required to render successfully.
#
#     It is the positive control for the lazy `import WDL` in `checks/wdl_inputs_check.py`, and its
#     absence is what kept this red for two reviews: with the import at module level, `python3` (which
#     has jinja2 with the stub, and no miniwdl) died on a traceback in `--render-only`, so §12.7's gate
#     legs printed `SKIPPED — could not render the ref's default inputs: Traceback … import WDL`, and
#     `python3 checks/wdl_inputs_check.py --help` exited 1 for the same reason. Run the phase with
#     `.venv/bin/python` — which HAS miniwdl — and every one of those legs went green on the bug, which
#     is the trap this leg closes: it names its own interpreter instead of inheriting `$PY`.
#
#     NO SKIP REGION, and none is possible: the premise is a property of an interpreter, not a
#     prerequisite a box may lack. The two `nowdl-` needles ARE the premise, printed by the leg so a
#     reader can see which interpreter it graded, and they go red rather than silent if the interpreter
#     stops being the one claimed — which is why, on a box where every interpreter really does import
#     miniwdl, the fixture supplies the absence the same way §12 supplies jinja2: a stub that raises
#     ImportError, which is indistinguishable from not having the package to any importer, probes
#     included.
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
# One command, so the premise and the verdict are graded as one assertion: the interpreter it names,
# whether that interpreter can import jinja2, whether it cannot import WDL, then the render's own
# answer. `GSVTK_RENDER_PYTHON` is unset because `render()` honours it for the shim, and a leg that
# claims "this interpreter rendered it" must not have that interpreter swapped out underneath it.
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

# The default --wf list is the only thing between "gsvtk check <ref>" and "gsvtk check --wf <the one
# workflow that broke>". IntegrateGDVcf is the workflow that actually regressed in gatk-sv PR #966, so
# the list has to name it. An offline --tree run prints exactly one row per default name (each ABSENT,
# because the fixture tree holds a Widget), which is how the list is read here with no checkout, no
# network and no jar -- and it is read from the gate's own output rather than from grepping its source,
# so a rename in either place moves this assertion.
want "the default set names the workflow that really regressed (PR #966), so a bare check can catch it" 1 \
    "IntegrateGDVcf" "ABSENT at" -- gate_nojar --tree "$TMP/wdl-fix"

printf '\nwomtool selftest: %s passed, %s failed, %s skipped (a SKIP here is not a pass in CI)\n' \
    "$ok" "$fail" "$skipped"
# The jar region is the whole story of this phase's two modes: with a named skip the pair prints the sites that
# did not run, and each of them must be INSIDE womtool-no-jar or the phase fails by line number; with no skip —
# the jar-in-hand mode — the declared and reached sets must be equal. What the pair cannot see is a DELETION:
# remove a call and both sets lose it together.
gsvtk_declared_report "womtool selftest" "$SELF" "$((ok + fail))" "$skipped" || fail=$((fail + 1))
[ "$fail" -eq 0 ] || exit 1
exit 0
