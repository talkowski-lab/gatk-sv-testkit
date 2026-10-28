#!/usr/bin/env python3
"""One-shot status table for the sandbox submissions (+ Cromwell cost/duration).

    python terra/batch_status.py                     # all steps, one line each
    python terra/batch_status.py --wait 06           # poll until terminal
    python terra/batch_status.py --costs             # + VM minutes per workflow, and a total
    python terra/batch_status.py --costs 01-Single   # price a config OUTSIDE the chain

Submissions are discovered from the workspace, keyed by method config name, so nothing has
to be remembered between sessions. Cost/duration come from Cromwell workflow metadata
(`gcpBatch` calls -> the VM span of each call), the same shape of accounting used for the v1.1.1
baseline; `cost` on the submission record itself is a Terra estimate and stays 0 while running.

`--costs PREFIX` prices every submission whose config starts with PREFIX, including configs
outside the chain. An empty PREFIX is refused, and a prefix that matches nothing exits 1.

A figure that cannot be read prints as `UNKNOWN` and is not added to the total. A figure with open
calls or unfetched sub-workflows prints with a trailing `+`, and the total is then a FLOOR.

This tool sums only root call records, one metadata request per workflow, so it can badly
under-count workflows that do their work in sub-workflows. For published numbers use
`batch_save_metadata.py` (full tree) and `batch_cost.py`.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime

sys.path.insert(0, __file__.rsplit("/", 1)[0])

from terra import fapi  # noqa: E402

import terra  # noqa: E402
import batch_configs as tc  # noqa: E402
import steps  # noqa: E402

STEPS = steps.step_names()
TERMINAL = ("Done", "Aborted")                    # submission states
WF_TERMINAL = ("Succeeded", "Failed", "Aborted")  # workflow states
# Sentinel for bare `--costs`, distinct from None (absent) and "" (refused).
CHAIN = object()


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
    d = terra._j(fapi.get_submission(tc.NS, tc.WS, sub_id), f"get_submission {sub_id}")
    rows = []
    for w in d.get("workflows") or []:
        rows.append((w.get("workflowId"), w.get("status"), w.get("cost"),
                     (w.get("messages") or [None])[-1]))
    return rows


def vm_minutes(wid: str, sub_id: str) -> tuple:
    """(vm minutes | None, calls | None, failed, open calls, sub-workflow delegations).

    Minutes are `None` (not 0) when unreadable. `open_calls` (no end timestamp) and `nested`
    (unfetched sub-workflow calls) each make the sum a floor.
    """
    try:
        m = terra._j(fapi.get_workflow_metadata(tc.NS, tc.WS, sub_id, wid), f"workflow metadata {wid}")
    except Exception as exc:  # metadata may not be delocalized yet
        return (None, None, f"metadata unavailable: {exc}", 0, 0)
    minutes, failed, n, open_calls, nested = 0.0, [], 0, 0, 0

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
            if sp is None:
                open_calls += 1
            else:
                minutes += sp
            if c.get("subWorkflowId"):
                nested += 1
            if str(c.get("executionStatus")) in ("Failed", "RetryableFailure"):
                failed.append(f"{name}:{c.get('shardIndex')}")
    if not n:
        # No call records yet (queued or not delocalized): unknown, not zero.
        return (None, 0, "no call records in the metadata yet", 0, 0)
    return (minutes, n, failed[:6] or "none", open_calls, nested)


def fmt_est(est, wst: str) -> str:
    """Format Terra's cost estimate; it reads 0 while running, so it is never summed."""
    if est is None:
        return "cost=unknown(no estimate on the record)"
    if wst not in WF_TERMINAL:
        return "cost=pending(Terra reports 0 until the workflow finishes)"
    if not est:
        return f"cost={est}(Terra's estimate, recorded as 0 and never summed here)"
    return f"cost={est}"


def price(sub_id: str, wid: str, wst: str, est) -> tuple:
    """(cost text, minutes | None, why-a-floor) for one workflow.

    `why-a-floor` is empty exactly when the figure is a measurement.
    """
    minutes, calls, failed, open_calls, nested = vm_minutes(wid, sub_id)
    est_txt = fmt_est(est, wst)
    if minutes is None:
        return (f"vm_min=UNKNOWN({failed}) calls=unknown failed=unknown {est_txt}", None,
                [str(failed)])
    why = []
    if open_calls:
        why.append(f"{open_calls} call(s) with no VM end yet (running or aborted)")
    if nested:
        why.append(f"{nested} call(s) below a sub-workflow, whose tree is not fetched here")
    text = (f"vm_min={minutes:.1f}{'+' if why else ''} calls={calls} failed={failed} {est_txt}")
    return (text + (f"  FLOOR({' ; '.join(why)})" if why else ""), minutes, why)


def total(prices: list, scope: str, unsubmitted: list) -> str:
    """One labelled total over `prices` [(row handle, minutes | None, why-a-floor)].

    Unknown rows are named and left out of the sum. The total is MEASURED only with no floors,
    unknowns or unsubmitted steps; otherwise FLOOR, or UNKNOWN if nothing was readable.
    """
    read = [p for p in prices if p[1] is not None]
    blind = [p for p in prices if p[1] is None]
    floored = [p for p in read if p[2]]
    holes = named([p[0] for p in blind])
    if not prices:
        return (f"TOTAL {scope}: vm_min=UNKNOWN  nothing was priced"
                + (f"; {len(unsubmitted)} chain step(s) never submitted" if unsubmitted else ""))
    if not read:
        return (f"TOTAL {scope}: vm_min=UNKNOWN  0 of {len(prices)} row(s) readable"
                " -- an unreadable run is not a free one" + holes
                + (f"; {len(unsubmitted)} chain step(s) never submitted" if unsubmitted else ""))
    mins = sum(p[1] for p in read)
    kind = "MEASURED" if not (floored or blind or unsubmitted) else "FLOOR"
    parts = [f"{len(read)}/{len(prices)} row(s) readable"]
    if floored:
        parts.append(f"{len(floored)} with open or nested work (the + figures)")
    if blind:
        parts.append(f"{len(blind)} UNKNOWN and NOT counted{holes}")
    if unsubmitted:
        parts.append(f"{len(unsubmitted)} chain step(s) never submitted "
                     f"({', '.join(u[:2] for u in unsubmitted)}) -> not a whole-chain figure")
    return (f"TOTAL {scope}: vm_min={mins:.1f}{'+' if floored else ''}  {kind}"
            f"  ({'; '.join(parts)})")


def named(handles: list) -> str:
    """` [a, b, c]` for the given row handles, capped at six."""
    if not handles:
        return ""
    shown = ", ".join(handles[:6])
    if len(handles) > 6:
        shown += f", +{len(handles) - 6} more"
    return f" [{shown}]"


def report(costs: bool, prefix: str = None) -> int:
    """Print the status table, optionally priced, with a labelled total.

    Without `--costs` the output must stay unchanged; the selftest compares it. Chain rows match
    the step name exactly, so snapshot and rerun configs appear under `(other config)`.
    """
    subs = latest_submissions()
    if prefix is None:
        rows = [(step, subs.get(step)) for step in STEPS]
        unsubmitted = [step for step, s in rows if not s]
        scope = "chain 06->10"
    else:
        rows = sorted((c, s) for c, s in subs.items() if c.startswith(prefix))
        if not rows:
            seen = ", ".join(sorted(subs)) or "not one config with a submission"
            raise SystemExit(f"no submission whose config starts with {prefix!r} in {tc.NS}/{tc.WS} "
                             f"-- {len(subs)} config(s) with a submission there: {seen[:400]}\n"
                             f"   (a prefix matches the CONFIG name, as submitted; a workspace that "
                             f"answers empty says so rather than printing an empty table)")
        unsubmitted = []
        scope = f"matching {prefix!r} over {len(rows)} config(s)"
    prices = []
    for cfg, s in rows:
        if not s:
            print(f"{cfg:24s} - not submitted")
            continue
        wf = workflow_detail(s["submissionId"])
        status = s.get("status")
        wstat = ",".join(f"{w[1]}" for w in wf) or "queued"
        line = (f"{cfg:24s} {status:9s} {s.get('submissionDate')}  {s['submissionId'][:8]}  "
                f"wf={wstat}")
        if costs:
            for wid, wst, cost, _ in wf:
                text, mins, why = price(s["submissionId"], wid, wst, cost)
                prices.append((wid[:8], mins, why))
                line += f"\n    {wid[:8]} {text}"
            if not wf:
                # Count it as unknown so the total reflects the run.
                prices.append((s["submissionId"][:8], None, ["submission has no workflows yet"]))
                line += "\n    (no workflows on this submission yet)  vm_min=UNKNOWN(queued)"
        print(line)
    if costs:
        print(total(prices, scope, unsubmitted))
    if prefix is None:
        extra = sorted(set(subs) - set(STEPS))
        for cfg in extra:
            print(f"(other config) {cfg}: {subs[cfg].get('status')}")
        if extra and costs:
            # Only with --costs, so the plain table stays unchanged.
            print(f"# {len(extra)} config(s) above are outside the chain, so none of them is in "
                  f"that total; price one by prefix: --costs {extra[0]}")
    return 0


def wait(prefix: str) -> None:
    """Poll the newest submission whose config starts with `prefix` until terminal."""
    while True:
        s = {k: v for k, v in latest_submissions().items() if k.startswith(prefix)}
        if not s:
            print(f"no submission matching {prefix}")
            return
        cfg, sub = max(s.items(), key=lambda kv: kv[1].get("submissionDate") or "")
        wf = workflow_detail(sub["submissionId"])
        print(f"{time.strftime('%H:%M:%S')} {cfg} submission={sub.get('status')} "
              f"workflows={[w[1] for w in wf]}", flush=True)
        if sub.get("status") in TERMINAL:
            for wid, wst, cost, msg in wf:
                text, _, _ = price(sub["submissionId"], wid, wst, cost)
                print(f"  {wid} {wst} {text}")
                if msg:
                    print(f"  msg: {str(msg)[:400]}")
            return
        time.sleep(120)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", metavar="PREFIX", help="poll until terminal, e.g. 06")
    ap.add_argument("--costs", nargs="?", metavar="PREFIX", const=CHAIN, default=None,
                    help="add VM minutes per workflow and a labelled TOTAL; with PREFIX, price every "
                         "config whose name starts with PREFIX -- chain or not, so a config outside "
                         "the chain (01-SingleSample-*) can be priced at all")
    a = ap.parse_args()
    if a.costs == "":
        raise SystemExit("--costs: an empty PREFIX prices nothing, which is not the same as pricing "
                         "everything.\n"
                         "   Drop the value to price the chain (06->10), or name a real prefix, e.g. "
                         "--costs 01-.\n"
                         '   (--costs "$CFG" with CFG unset is how this gets typed.)')
    if a.costs is not None and a.wait:
        raise SystemExit("--costs and --wait are two questions: --wait polls until terminal and prints "
                         "the same vm_min line for every workflow when it gets there.")
    tc.require_target()  # after --help has been served, before the first API call
    if a.wait:
        wait(a.wait)
        return 0
    if a.costs is None:
        return report(False, None)
    if a.costs is CHAIN:
        return report(True, None)
    return report(True, a.costs)


if __name__ == "__main__":
    sys.exit(main())
