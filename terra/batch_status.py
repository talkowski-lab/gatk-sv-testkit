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

What `--costs PREFIX` prices that the table cannot
--------------------------------------------------
Without a prefix the table is the chain's: one row per step of `steps.step_names()`, submitted or
not. A config outside the chain -- a single-sample launch, a one-off -- is not in that list, and used
to be reported as `(other config) <name>: Done` with no number next to it, which is the monitor
shrugging at the one question that spends money. `--costs PREFIX` prices every submission whose config
starts with PREFIX, chain or not, as one table with one total -- the same selector `--wait` already
takes, so the prefix that watches a run is the prefix that prices it.

Two refusals, both argument-shaped holes this repo has already been paid for once: an EMPTY prefix
(`--costs "$CFG"` with `CFG` unset) is refused rather than read as "price everything" -- the
`--entity "$ROW"` hole closed at `terra/batch_rerun_step.py` -- and a prefix that matches no
submission exits 1 naming the prefix and what it looked at, because an empty price table and an
unpriced run are otherwise the same output.

The unknown-not-zero rule
-------------------------
Every figure here prints as `UNKNOWN`, with the reason, when it cannot be read, and is NOT added to the
total: the workflow metadata is not readable yet, the metadata holds no call records yet, and the
workflow's own Terra `cost` estimate, which Terra reports as 0 until the workflow finishes. A figure
with work still open, or work nested under a sub-workflow this tool does not fetch, prints with a
trailing `+` and makes the TOTAL a FLOOR rather than a measurement. `terra/batch_cost.py` applies the
same rule on the saved-metadata side (a step with no file is a named hole that refuses the chain
total); this is the live side of it.

The two are deliberately NOT the same number. This one sums the ROOT call records of one workflow at
one metadata request per workflow -- cheap enough to be a monitor, and a floor for any workflow that
does its work in sub-workflows. `terra/batch_save_metadata.py` expands every sub-workflow tree (at one
request per sub-workflow) and `terra/batch_cost.py` recomputes from those files; quote THAT arithmetic
in anything you publish, because this figure cannot see below the root tree and can be off by orders
of magnitude (see `batch_save_metadata.py`'s own docstring: unexpanded, the step-10 baseline read 918
VM-min instead of 1672.8).
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
import steps  # noqa: E402  # one reader for the step -> workflow map (see terra/steps.py)

STEPS = steps.step_names()
TERMINAL = ("Done", "Aborted")
# argparse `const` for `--costs` spelled with no PREFIX. It cannot be None (that is "flag absent") and
# cannot be "" (that is `--costs ""`, an unset variable, which is refused below), so the three states
# of the option need three different values or the refusal cannot tell them apart.
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
    d = fapi.get_submission(tc.NS, tc.WS, sub_id).json()
    rows = []
    for w in d.get("workflows") or []:
        rows.append((w.get("workflowId"), w.get("status"), w.get("cost"),
                     (w.get("messages") or [None])[-1]))
    return rows


def vm_minutes(wid: str, sub_id: str) -> tuple:
    """(vm minutes | None, calls | None, failed, open calls, sub-workflow delegations).

    `None`, never `0.0`, when the figure cannot be read: a workflow whose cost is unknown and a
    workflow that cost nothing are two answers, and printing one as the other is the defect this
    file's docstring is named for. `open_calls` counts call records with no VM end timestamp (still
    running, or aborted mid-flight) and `nested` counts records that hand off to a sub-workflow whose
    tree is NOT fetched here -- either one makes the sum a floor, which is the most the total is
    allowed to claim.
    """
    try:
        m = fapi.get_workflow_metadata(tc.NS, tc.WS, sub_id, wid).json()
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
        # Zero call records is not a workflow that cost nothing. It is metadata with no calls in it
        # yet -- queued, or not delocalized -- and an empty result scored as 0 is the failure class
        # CONTRIBUTING refuses to let this repo report.
        return (None, 0, "no call records in the metadata yet", 0, 0)
    return (minutes, n, failed[:6] or "none", open_calls, nested)


def fmt_est(est, wst: str) -> str:
    """The submission record's `cost`, printed only where a number can mean something.

    Terra's per-workflow `cost` is an ESTIMATE, and while the workflow is running the record says 0
    (the module docstring's caveat, and the reason the minutes are computed from metadata at all).
    Printing that 0 as a cost is an unknown reported as zero, so a non-terminal workflow prints as
    `pending` and the figure is never summed anywhere in this file.
    """
    if est is None:
        return "cost=unknown(no estimate on the record)"
    if wst not in TERMINAL:
        return "cost=pending(Terra reports 0 until the workflow finishes)"
    if not est:
        # A finished workflow whose estimate is still 0 is not a workflow that came free; it is a
        # field Terra left at its default. Printed, because it is the record's own answer, and
        # labelled, because the only figure this file ever sums is the VM minutes.
        return f"cost={est}(Terra's estimate, recorded as 0 and never summed here)"
    return f"cost={est}"


def price(sub_id: str, wid: str, wst: str, est) -> tuple:
    """(cost text, minutes | None, why-a-floor) for one workflow.

    The `None` is the unknown-not-zero rule written into the return type: a caller adds `minutes` to a
    total and has no way to add an unknown, so an unreadable workflow cannot quietly become a cheaper
    run. `why-a-floor` is empty exactly when the text is a measurement.
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
    """One total, labelled with what it covers and with how much of it was readable.

    `prices` is [(row handle, minutes | None, why-a-floor)]. The unknowns are absent from the sum and
    named in the sentence -- counted AND addressed, the way `batch_cost.py` prints the steps missing
    from a chain total -- because "2 rows were unreadable" is a count a reader cannot go and check.
    With nothing readable the line prints UNKNOWN instead of `0.0`: "cost nothing" and "could not be
    read" must never be the same output. `MEASURED` is reserved for a figure with neither a floor nor a
    hole in it, which is also `batch_cost.py`'s rule for a published chain number.
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
    """` [a, b, c]` for the rows a total refused to add, capped so a broken workspace cannot page the
    terminal. A count with no names is a finding the reader cannot go and look at."""
    if not handles:
        return ""
    shown = ", ".join(handles[:6])
    if len(handles) > 6:
        shown += f", +{len(handles) - 6} more"
    return f" [{shown}]"


def report(costs: bool, prefix: str = None) -> int:
    """Print the table, price what was asked for, and say what kind of number the total is.

    Without `--costs` this prints EXACTLY what it printed before the prefix arm existed -- same rows,
    same `(other config)` tail, nothing added -- because that table is what gets diffed between two
    minutes of a run, and `scripts/selftest.d/cli.sh` compares this file's bytes against the bytes at
    `b5c0d03` to hold it. The chain rows key on the step name EXACTLY (`subs.get(step)`), which is
    what keeps a finished run's per-submission snapshot copy (`<config>_<random>`, `terra/steps.py`) out
    of the table by never being looked for. The cost of that rule is visible here rather than hidden: a
    rerun config (`10-GenotypeBatch-rerun`) is not the step name, so it lands among `(other config)` and
    is not in the chain total -- `--costs <prefix>` is how you price it, and the printed hint says so.
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
                # A submission with no workflows on it yet has no cost to read, and leaving it out of
                # `prices` entirely would let the total say nothing about a run that exists.
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
            # Printed ONLY when costs were asked for: without them the table is the same table it
            # always was, byte for byte, and a hint is not worth breaking that for a tool someone may
            # be diffing between two minutes of a run. With them, this is the line that stops the
            # total above being read as a bill for the whole workspace.
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
        cfg, sub = sorted(s.items())[0]
        wf = workflow_detail(sub["submissionId"])
        print(f"{time.strftime('%H:%M:%S')} {cfg} submission={sub.get('status')} "
              f"workflows={[w[1] for w in wf]}", flush=True)
        if sub.get("status") in TERMINAL:
            for wid, wst, cost, msg in wf:
                # The same priced line as the table, so the watcher and the table cannot print two
                # different numbers for one workflow.
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
