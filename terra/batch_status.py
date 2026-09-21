#!/usr/bin/env python3
"""One-shot status table for the sandbox submissions (+ Cromwell cost/duration).

    python terra/batch_status.py           # all steps, one line each
    python terra/batch_status.py --wait 06 # poll until terminal
    python terra/batch_status.py --costs   # per-workflow VM minutes

Submissions are discovered from the workspace, keyed by method config name, so nothing has
to be remembered between sessions. Cost/duration come from Cromwell workflow metadata
(`gcpBatch` calls -> vmCostPerHour x vm span), the same accounting used for the v1.1.1
baseline; `cost` on the submission record itself is a Terra estimate and stays 0 while running.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from terra import fapi  # noqa: E402  # via terra: one friendly missing-dependency message

import terra  # noqa: E402
import batch_configs as tc  # noqa: E402

STEPS = ["06-GenerateBatchMetrics", "07-FilterBatchSites", "08-FilterBatchSamples",
         "09-MergeBatchSites", "10-GenotypeBatch"]
TERMINAL = ("Done", "Aborted")


def latest_submissions() -> dict:
    """config name -> newest submission summary (a rerun after a failure wins)."""
    subs = terra.submissions(tc.NS, tc.WS)
    subs = subs if isinstance(subs, list) else subs.get("submissions", [])
    out = {}
    for s in subs:
        cfg = f"{s.get('methodConfigurationName')}"
        if cfg not in out or (s.get("submissionDate") or "") > (out[cfg].get("submissionDate") or ""):
            out[cfg] = s
    return out


def workflow_detail(sub_id: str) -> list:
    """[(workflowId, status, cost, messages)] for every workflow of a submission."""
    d = fapi.get_submission(tc.NS, tc.WS, sub_id).json()
    rows = []
    for w in d.get("workflows") or []:
        rows.append((w.get("workflowId"), w.get("status"), w.get("cost"),
                     (w.get("messages") or [None])[-1]))
    return rows


def vm_minutes(wid: str, sub_id: str) -> tuple:
    """(vm minutes, call count, failed calls) from Cromwell metadata."""
    try:
        m = fapi.get_workflow_metadata(tc.NS, tc.WS, sub_id, wid).json()
    except Exception as exc:  # metadata may not be delocalized yet
        return (0.0, 0, f"metadata unavailable: {exc}")
    minutes, failed, n = 0.0, [], 0

    def span(call):
        t0, t1 = call.get("vmStartTime") or call.get("start"), call.get("vmEndTime") or call.get("end")
        if not t0 or not t1:
            return None
        fmt = lambda t: datetime.fromisoformat(t.replace("Z", "+00:00"))
        return (fmt(t1) - fmt(t0)).total_seconds() / 60.0

    for name, shards in (m.get("calls") or {}).items():
        for c in shards:
            n += 1
            sp = span(c)
            if sp:
                minutes += sp
            if str(c.get("executionStatus")) in ("Failed", "RetryableFailure"):
                failed.append(f"{name}:{c.get('shardIndex')}")
    return (minutes, n, failed[:6] or "none")


def report(costs: bool) -> None:
    subs = latest_submissions()
    for step in STEPS:
        s = subs.get(step)
        if not s:
            print(f"{step:24s} - not submitted")
            continue
        wf = workflow_detail(s["submissionId"])
        status = s.get("status")
        wstat = ",".join(f"{w[1]}" for w in wf) or "queued"
        line = (f"{step:24s} {status:9s} {s.get('submissionDate')}  {s['submissionId'][:8]}  "
                f"wf={wstat}")
        if costs:
            for wid, wst, cost, _ in wf:
                mins, calls, failed = vm_minutes(wid, s["submissionId"])
                line += f"\n    {wid[:8]} vm_min={mins:.0f} calls={calls} failed={failed} cost={cost}"
        print(line)
    extra = sorted(set(subs) - set(STEPS))
    for cfg in extra:
        print(f"(other config) {cfg}: {subs[cfg].get('status')}")


def wait(prefix: str) -> None:
    """Poll the newest submission whose config starts with `prefix` until terminal."""
    while True:
        s = {k: v for k, v in latest_submissions().items() if k.startswith(prefix)}
        if not s:
            print(f"no submission matching {prefix}")
            return
        cfg, sub = sorted(s.items())[0]
        wf = workflow_detail(sub["submissionId"])
        print(f"{time.strftime('%H:%M:%S')} {cfg} submission={sub.get('status')} "
              f"workflows={[w[1] for w in wf]}", flush=True)
        if sub.get("status") in TERMINAL:
            for wid, wst, cost, msg in wf:
                mins, calls, failed = vm_minutes(wid, sub["submissionId"])
                print(f"  {wid} {wst} vm_min={mins:.0f} calls={calls} failed={failed}")
                if msg:
                    print(f"  msg: {str(msg)[:400]}")
            return
        time.sleep(120)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", metavar="PREFIX", help="poll until terminal, e.g. 06")
    ap.add_argument("--costs", action="store_true", help="add Cromwell VM minutes per workflow")
    a = ap.parse_args()
    tc.require_target()  # after --help has been served, before the first API call
    if a.wait:
        wait(a.wait)
    else:
        report(a.costs)
