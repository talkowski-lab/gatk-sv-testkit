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

# womtool is the only thing that can tell you a WDL's REQUIRED call inputs: a missing
# input binding does not fail WDL typecheck, it fails later, at input-resolution time,
# mid-submission. Fetch it once (docs/static-checks.md) and point WOMTOOL_JAR at it.
WOMTOOL = os.environ.get("WOMTOOL_JAR", "")
JAVA = os.environ.get("JAVA", "java")
WDLS = {"06": "GenerateBatchMetrics", "07": "FilterBatchSites", "08": "FilterBatchSamples",
        "09": "MergeBatchSites", "10": "GenotypeBatch"}


def checkout() -> str:
    """A local gatk-sv clone: the checks must read the WDL of the code under test."""
    return str(config.checkout("GATK_SV_CHECKOUT"))


def required_inputs(wdl_path: str, prefix: str) -> set:
    """Names (prefix-stripped) that WOM reports as required (no 'optional' in the type string)."""
    if not WOMTOOL or not os.path.exists(WOMTOOL):
        raise SystemExit(f"womtool jar not found (WOMTOOL_JAR={WOMTOOL!r}). Download the womtool jar "
                         "matching your cromwell version and export WOMTOOL_JAR=/path/to/womtool.jar "
                         "\u2014 see docs/static-checks.md.")
    out = subprocess.run([JAVA, "-jar", WOMTOOL, "inputs", wdl_path],
                         cwd=checkout(), capture_output=True, text=True, check=True).stdout
    spec = json.loads(out)
    return {k.split(".", 1)[1] for k, v in spec.items()
            if "optional" not in v and k.startswith(prefix + ".")}


def last_submission(config_name: str) -> tuple:
    subs = terra.submissions(tc.NS, tc.WS, limit=30).get("submissions", [])
    for s in subs:
        if s.get("methodConfigurationName") == config_name:
            return s["submissionId"], s
    raise SystemExit(f"no submission found for {config_name}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--step", default="10", choices=sorted(WDLS))
    ap.add_argument("--no-sidecars", action="store_true")
    a = ap.parse_args()

    wf = WDLS[a.step]
    config_name = next(c for c in tc.CONFIGS if c.startswith(a.step + "-"))
    req = required_inputs(f"wdl/{wf}.wdl", wf)
    payload = terra.config_payload(tc.NS, tc.WS, tc.NS, config_name)
    bound = {k.split(".", 1)[1] for k in payload.get("inputs", {})}

    print(f"# {config_name}  (WDL wdl/{wf}.wdl @ {tc.BRANCH}, "
          f"methodConfigVersion {payload.get('methodConfigVersion')})")
    print(f"#   required by WOM: {len(req)} | bound by config: {len(bound)}")
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
        print(f"  FAIL expression-shaped bindings (Cromwell evaluates these; a WDL default can "
              f"win):\n        " + "\n        ".join(expr))
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
