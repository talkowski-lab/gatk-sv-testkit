#!/usr/bin/env python3
"""Re-check the step-10 (GenotypeBatch) input gates against Terra + GCS, read-only.

Answers, with evidence rather than confidence: does the config bind every input the WDL
requires, did the last submission actually resolve them to values, and do the `.tbi`
sidecars that a `+ ".tbi"` binding implies really exist in GCS?

    python terra/batch_check_inputs.py                # config vs WDL + sidecars
    python terra/batch_check_inputs.py --no-sidecars   # skip the gsutil loop
    python terra/batch_check_inputs.py --step 09       # any 06..10 config

Checks, in order:
  1. REQUIRED set from the branch WDL via `womtool inputs`. A missing call-input binding never
     fails WDL typecheck -- it fails at input-resolution time, after the submission has started.
  2. What the sandbox method config actually binds (set-diff both ways).
  3. What the LAST SUBMISSION of that config resolved to (`inputsResolutions`) - i.e. proof the
     expressions evaluated to real values on the entity, not just that keys exist.
  4. For every resolved gs:// value, whether `<value>.tbi` also exists in GCS. The count of
     sidecars is reported, not assumed.

Check 1 reads `womtool inputs` JSON. `GSVTK_WOMTOOL_INPUTS_JSON=<file>` supplies that JSON from a
file instead of running java, which is how the optionality predicate is graded offline (see
`--help`, docs/config.md, and scripts/selftest.d/jarshape.sh). It changes check 1's input only:
checks 2-4 still want Terra, and the jar is still what a real run uses.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
from terra import fapi  # noqa: E402  # via terra: one friendly missing-dependency message
import terra  # noqa: E402
import batch_configs as tc  # noqa: E402
import steps  # noqa: E402  # one reader for the step -> workflow map (see terra/steps.py)

# womtool is the only thing that can tell you a WDL's REQUIRED call inputs: a missing
# input binding does not fail WDL typecheck, it fails later, at input-resolution time,
# mid-submission. Fetch it once (docs/static-checks.md) and point WOMTOOL_JAR at it.
# gatk-sv's CI pins the artifact it downloads (`.github/workflows/testwdls.yaml`):
# https://github.com/broadinstitute/cromwell/releases/download/84/womtool-84.jar
WOMTOOL = os.environ.get("WOMTOOL_JAR", "")
JAVA = os.environ.get("JAVA", "java")
# The test seam for the optionality predicate: a captured `womtool inputs` JSON, read instead of
# shelling out to java. Documented in this tool's --help and in docs/config.md, asserted by
# scripts/selftest.d/jarshape.sh. Deliberately named GSVTK_* and deliberately NOT a shortcut past
# Terra: with it set, this tool still refuses to invent a required set if the file is unreadable.
WOMTOOL_INPUTS_JSON = os.environ.get("GSVTK_WOMTOOL_INPUTS_JSON", "")

# What optionality looks like in `womtool inputs` output, MEASURED at womtool-84 rather than
# assumed. Over the whole gatk-sv tree at 7fbf1171 (118 WDLs, 109 of which have a primary callable
# for womtool to describe: 4902 per-input entries):
#
#   * 4901 values are STRINGS, and an optional input's string always ends in the marker
#     " (optional)" or " (optional, default = <expr>)" -- 3140 and 396 entries respectively, with
#     ZERO entries carrying the word `optional` anywhere else in the string. Ground truth pair:
#     `"IntegrateGDVcf.sample_id": "String"` at 7fbf1171 (CI rejected that commit: "Required
#     workflow input 'IntegrateGDVcf.sample_id' not specified") vs
#     `"IntegrateGDVcf.sample_id": "String? (optional)"` at 01107996.
#   * 1 value is a DICT: a struct-typed input is expanded into its members,
#     e.g. `"BenchmarkGqFilter.original_scores": {"label": "String", ...,
#     "pickled_scores_file": "File? (optional)"}`. That entry says nothing about whether the INPUT
#     is optional (an optional struct arrives as a STRING with the marker, measured), and a member's
#     own "(optional)" sits in the dict's VALUES, which `"optional" not in value` never looks at.
#
# So the upstream predicate this file copied (`if "optional" in womtool_inputs[inp]`, gatk-sv's
# scripts/test/terra_validation.py) means two different things depending on shape: a SUBSTRING test
# on a string, and a DICT-KEY test on a struct. The second is the direction that cannot fail loudly:
# measured at womtool-84, `struct HasOptionalMember { String optional\n Int count }` as a REQUIRED
# input emits `{"optional": "String", "count": "Int"}`, and key membership then drops a required
# input out of the required set. Hence: test the marker, on strings only, and treat any shape that
# cannot carry the marker as required. Under-demanding a binding is invisible; over-demanding one is
# a loud FAIL naming the input, which is the failure direction this check has always chosen.
_OPTIONAL_MARKER = re.compile(r"\(optional\b")


def checkout() -> str:
    """A local gatk-sv clone: the checks must read the WDL of the code under test."""
    return str(config.checkout("GATK_SV_CHECKOUT"))


def is_optional_input(value) -> bool:
    """Whether one `womtool inputs` entry marks its input as NOT required (see _OPTIONAL_MARKER)."""
    if isinstance(value, str):
        return bool(_OPTIONAL_MARKER.search(value))
    return False          # dict (struct expanded into members): cannot carry the marker at 84


def womtool_source() -> str:
    """Where check 1's womtool JSON comes from. Printed, so a seam run is never read as a jar run."""
    if WOMTOOL_INPUTS_JSON:
        return "captured womtool JSON %s (GSVTK_WOMTOOL_INPUTS_JSON)" % WOMTOOL_INPUTS_JSON
    return "womtool jar %s (WOMTOOL_JAR)" % (WOMTOOL or "unset")


def womtool_spec(wdl_path: str) -> dict:
    """The `womtool inputs` JSON object for one WDL. Refuses rather than guessing.

    Refusing is the point: a REQUIRED set derived from nothing is a check that cannot fail, and the
    whole reason this file exists is that a missing call-input binding surfaces mid-submission.
    """
    if WOMTOOL_INPUTS_JSON:
        if not os.path.isfile(WOMTOOL_INPUTS_JSON):
            raise SystemExit(f"GSVTK_WOMTOOL_INPUTS_JSON={WOMTOOL_INPUTS_JSON!r} is not a file, and no "
                             "required-input set is being guessed. Unset it to use the jar "
                             "(WOMTOOL_JAR=\u2026), or point it at a captured "
                             "`womtool inputs <wdl>` JSON (see --help).")
        with open(WOMTOOL_INPUTS_JSON) as fh:
            spec = json.load(fh)
        if not isinstance(spec, dict):
            raise SystemExit(f"GSVTK_WOMTOOL_INPUTS_JSON={WOMTOOL_INPUTS_JSON!r} does not hold a JSON "
                             "object keyed by '<Workflow>.<input>', which is what `womtool inputs` "
                             "prints.")
        return spec
    if not WOMTOOL or not os.path.exists(WOMTOOL):
        raise SystemExit(f"womtool jar not found (WOMTOOL_JAR={WOMTOOL!r}). Download the womtool jar "
                         "matching your cromwell version and export WOMTOOL_JAR=/path/to/womtool.jar "
                         "\u2014 see docs/static-checks.md. To grade the required-input predicate "
                         "without a jar, export GSVTK_WOMTOOL_INPUTS_JSON=<captured womtool inputs "
                         "JSON> (see --help).")
    out = subprocess.run([JAVA, "-jar", WOMTOOL, "inputs", wdl_path],
                         cwd=checkout(), capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def required_from_spec(spec: dict, prefix: str) -> set:
    """Names (prefix-stripped) in a `womtool inputs` object that its marker says are required."""
    return {k.split(".", 1)[1] for k, v in spec.items()
            if not is_optional_input(v) and k.startswith(prefix + ".")}


def required_inputs(wdl_path: str, prefix: str) -> set:
    """Names (prefix-stripped) that WOM reports as required, per is_optional_input."""
    return required_from_spec(womtool_spec(wdl_path), prefix)


def last_submission(config_name: str) -> tuple:
    subs = terra.submissions(tc.NS, tc.WS, limit=30).get("submissions", [])
    for s in subs:
        if s.get("methodConfigurationName") == config_name:
            return s["submissionId"], s
    raise SystemExit(f"no submission found for {config_name}")


HELP_EPILOG = """\
environment:
  WOMTOOL_JAR=/path/to/womtool.jar     the jar check 1 runs (`java -jar $WOMTOOL_JAR inputs <wdl>`).
  JAVA=/path/to/java                   the java used for it (default: java on PATH).
  GSVTK_WOMTOOL_INPUTS_JSON=<file>     TEST SEAM: read check 1's `womtool inputs` JSON from FILE
                                       instead of running java, so the optionality predicate can be
                                       graded against a captured womtool output with no jar, no java
                                       and no network. It changes nothing else: the Terra and GCS
                                       checks still run, and a missing/unreadable FILE is refused by
                                       name rather than answered with a guessed REQUIRED set.
                                       Fixtures: scripts/selftest.d/fixtures/jar/ ; assertions:
                                       scripts/selftest.d/jarshape.sh.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 epilog=HELP_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", default="10", choices=steps.known_steps())
    ap.add_argument("--no-sidecars", action="store_true")
    a = ap.parse_args()

    wf = steps.workflow(a.step)
    # steps.config_name, not `next(c for c in tc.CONFIGS if c.startswith(step + "-"))`: that raised a
    # bare StopIteration for a step no config carries, naming neither the step nor what it tried
    # (docs/module-profiles.md §9.4).
    config_name = steps.config_name(a.step, tc.CONFIGS)
    spec = womtool_spec(f"wdl/{wf}.wdl")
    req = required_from_spec(spec, wf)
    payload = terra.config_payload(tc.NS, tc.WS, tc.NS, config_name)
    bound = {k.split(".", 1)[1] for k in payload.get("inputs", {})}

    print(f"# {config_name}  (WDL wdl/{wf}.wdl @ {tc.BRANCH}, "
          f"methodConfigVersion {payload.get('methodConfigVersion')})")
    print(f"#   required by WOM: {len(req)} | bound by config: {len(bound)}  [source: {womtool_source()}]")
    miss, extra = sorted(req - bound), sorted(bound - req)
    fails = [] if not miss else [f"unbound: {miss}"]
    print("  " + ("PASS every required input is bound" if not miss else f"FAIL unbound: {miss}"))
    if extra:
        print(f"  note: bound but not required (optional/defaulted): {extra}")

    # A binding that is an EXPRESSION rather than a value is the failure mode that survives
    # create/validate: Cromwell evaluates it, gets nothing usable, and quietly falls back to the
    # WDL's own default -- so the run completes with a plausibly wrong input. Cheap to detect here.
    expr = []
    for key, val in payload.get("inputs", {}).items():
        text = "" if val is None else str(val)
        stripped = text.strip()
        if "~{" in text or stripped.startswith(("{", "[", "if ", "select_all", "read_")) \
           or (stripped and stripped[0].isspace()):
            expr.append(f"{key.split('.', 1)[-1]}={text[:60]!r}")
    if expr:
        fails.append(f"expression-shaped bindings: {expr}")
        print("  FAIL expression-shaped bindings (Cromwell evaluates these; a WDL default can "
              "win):\n        " + "\n        ".join(expr))
    else:
        print("  PASS every binding is a literal or a plain workspace/attribute reference")

    sub_id, _ = last_submission(config_name)
    full = fapi.get_submission(tc.NS, tc.WS, sub_id).json()
    resolutions, status = {}, []
    for wfl in full.get("workflows", []):
        status.append(wfl.get("status"))
        for r in (wfl.get("inputResolutions") or []):
            if r.get("value") is not None:
                resolutions[r["inputName"]] = r["value"]
    unresolvable = [k for k in (f"{wf}.{n}" for n in req) if k not in resolutions]
    if unresolvable:
        fails.append(f"unresolved: {unresolvable}")
    print(f"#   last submission {sub_id[:8]} status={status} "
          f"resolved inputs={len(resolutions)}")
    print("  " + ("PASS every required input resolved to a value" if not unresolvable
                  else f"FAIL unresolved: {unresolvable}"))

    if a.no_sidecars:
        if fails:
            print(f"# FAIL: {'; '.join(fails)}   (docs/terra-head-to-head.md)")
            return 1
        return 0
    files = {k: v for k, v in resolutions.items()
             if isinstance(v, str) and re.match(r"gs://[^\t]+$", v.strip())}
    have = need = 0
    print(f"# sidecar probe ({len(files)} gs:// inputs)")
    for k, v in sorted(files.items()):
        v = v.strip()
        r = subprocess.run(["gsutil", "-q", "stat", v + ".tbi"], capture_output=True, text=True)
        ok = r.returncode == 0
        need += 1
        have += ok
        print(f"    {'tbi OK  ' if ok else 'no tbi  '} {k} = {v}")
    print(f"# sidecars present: {have}/{need}")
    if fails:
        print(f"# FAIL: {'; '.join(fails)}   (docs/terra-head-to-head.md)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
