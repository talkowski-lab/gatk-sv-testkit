#!/usr/bin/env python3
"""Persist Cromwell workflow metadata for the sandbox submissions to disk.

Why this exists: a cost number read live off Terra is not evidence. Once the session that
read it is gone, nobody can re-derive it, and a reviewer is right to refuse to treat it as
auditable. Save the raw expanded metadata here and the numbers become recomputable offline
by `batch_cost.py`, forever, with the accounting spelled out in code.

Read-only against Terra: `fapi.get_workflow_metadata(..., expand_sub_workflows=True)` only.

    python terra/batch_save_metadata.py           # all targets
    ... batch_save_metadata.py --target 10-new --outdir work/metadata
"""
import argparse
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
sys.path.insert(0, HERE)
import config  # noqa: E402
import terra  # noqa: E402
from terra import fapi  # noqa: E402  (one friendly missing-dependency message, in terra.py)
import batch_configs as tc  # noqa: E402

# The two sides of the head-to-head sit in different workspaces: the reference run in
# the baseline workspace, your chain in the sandbox. Steps are matched by the numeric
# prefix both workspaces give their method configs.
def sides() -> dict:
    """side -> (namespace, workspace). Resolved on call, so an unset profile fails
    with the written fix rather than at import of an unrelated subcommand."""
    ns, ws, _ = tc.require_target()
    return {"new": (ns, ws), "baseline": (terra.BASELINE_NS, terra.BASELINE_WS)}
STEPS = {"06": "GenerateBatchMetrics", "07": "FilterBatchSites", "08": "FilterBatchSamples",
         "09": "MergeBatchSites", "10": "GenotypeBatch"}


def targets() -> dict:
    """key -> (namespace, workspace, submission, workflow), discovered per workspace.

    Discovered rather than a remembered list of UUIDs: submission ids go stale the
    moment a chain is rerun, and a tool that only works against one person's
    completed run is not a tool. Pass --ns/--ws/--submission/--workflow to pin any
    workflow explicitly.
    """
    out = {}
    for side, (ns, ws) in sides().items():
        for step, wdl in STEPS.items():
            found = terra.latest_workflow(ns, ws, f"{step}-")
            if not found or not found.get("workflow_id"):
                print(f"  [skip] {step}-{wdl} ({side}): no submission in {ns}/{ws}",
                      file=sys.stderr)
                continue
            if found["workflow_count"] > 1:
                print(f"  [note] {step}-{side}: submission fanned out to "
                      f"{found['workflow_count']} workflows; cost covers the first only",
                      file=sys.stderr)
            out[f"{step}-{side}"] = (ns, ws, found["submission_id"], found["workflow_id"])
    return out


def count_missing(meta):
    n = len(meta.get("_missingSubWorkflows") or [])
    for k in (meta.get("_children") or []):
        n += count_missing(k)
    return n


def ts(s):
    """Cromwell timestamps are ISO-8601 UTC with a trailing Z."""
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def fetch_tree(ns, ws, sub, wf, depth=0, max_depth=6):
    """Recursively fetch a workflow's metadata plus every nested sub-workflow's.

    Terra's Cromwell returns NO `subWorkflowMetadata`/`subWorkflowList` even with
    expand_sub_workflows=True (verified: baseline step 10 comes back with 8 root calls, 3 of them
    VM-backed, 9.8 VM-min -- the nested 260-odd jobs are simply absent). The nested workflows are
    reachable via the `subWorkflowId` on each sub-workflow call record, so expansion has to be done
    by fetching each one. Without this the baseline looks ~170x cheaper than it is.
    """
    r = fapi.get_workflow_metadata(ns, ws, sub, wf)
    if r.status_code != 200:
        return None     # callers record this as _missingSubWorkflows; quality() counts it
    meta = r.json()
    kids = []
    if depth < max_depth:
        for name, calls in (meta.get("calls") or {}).items():
            for c in calls:
                sid = c.get("subWorkflowId")
                if sid:
                    child = fetch_tree(ns, ws, sub, sid, depth + 1, max_depth)
                    if child is None:  # one retry: a transient non-200 silently truncates the tree,
                        child = fetch_tree(ns, ws, sub, sid, depth + 1, max_depth)  # and truncation
                    if child is not None:  # under-counts cost (it made baseline step 10 read 918
                        child["_callName"] = name  # VM-min instead of 1672.8 -- a 2x error)
                        kids.append(child)
                    else:
                        meta["_missingSubWorkflows"] = (meta.get("_missingSubWorkflows") or []) + [sid]
    else:
        # Hitting the depth cap is truncation, and unrecorded truncation looks exactly like a
        # complete tree. Record what is still below here so batch_cost.py can refuse to call the
        # number it computes a measurement.
        blocked = [c.get("subWorkflowId") for calls in (meta.get("calls") or {}).values()
                   for c in calls if c.get("subWorkflowId")]
        if blocked:
            meta["_unexpandedSubWorkflows"] = blocked
    meta["_children"] = kids
    return meta


def quality(meta) -> dict:
    """Count what is MISSING from a metadata tree -- the labels a published cost number needs.

    vm_minutes() can only sum what the file contains, and three different kinds of absence produce
    the same tidy figure:
      fetch_failed     _missingSubWorkflows: the fetch returned non-200 twice (the old floor sign)
      unexpanded       a sub-workflow call with no child tree below it -- a depth cap, an older
                       saved file, or a file trimmed to keep it small. The nested minutes are
                       simply not there (baseline step 10 read 918 VM-min instead of 1672.8 that
                       way, which is the whole reason this function exists).
      half_timestamped vmStartTime without vmEndTime (still running, or aborted): dropped from the
                       minutes AND the job count, so 'jobs' under-reports too.
    """
    q = {"fetch_failed": 0, "unexpanded": 0, "half_timestamped": 0, "calls_seen": 0}

    def walk(m):
        q["fetch_failed"] += len(m.get("_missingSubWorkflows") or [])
        sids = [c.get("subWorkflowId") for calls in (m.get("calls") or {}).values()
                for c in calls if c.get("subWorkflowId")]
        q["unexpanded"] += len(m.get("_unexpandedSubWorkflows") or [])
        q["unexpanded"] += max(0, len(sids) - len(m.get("_children") or []))
        for calls in (m.get("calls") or {}).values():
            for c in calls:
                q["calls_seen"] += 1
                if bool(c.get("vmStartTime")) != bool(c.get("vmEndTime")):
                    q["half_timestamped"] += 1
        for kid in (m.get("_children") or []):
            walk(kid)

    walk(meta)
    return q


def vm_minutes(meta):
    """Nested-accurate roll-up: every call record at any depth that carries BOTH vmStartTime and
    vmEndTime counts as one VM-backed job; sum(vmEnd-vmStart). Root call spans are counted
    separately because that is the accounting that produced the old '382' figure."""
    jobs, span_min = [], 0.0

    def walk(m, path=""):
        nonlocal span_min
        for name, calls in (m.get("calls") or {}).items():
            for c in calls:
                s, e = c.get("vmStartTime"), c.get("vmEndTime")
                if s and e:
                    mins = (ts(e) - ts(s)).total_seconds() / 60.0
                    jobs.append((path + name, mins))
                    span_min += mins
        for kid in (m.get("_children") or []):
            walk(kid, path + (kid.get("_callName") or "sub").split(".")[-1] + "/")

    span = 0.0
    for name, calls in (meta.get("calls") or {}).items():
        for c in calls:
            if c.get("start") and c.get("end"):
                span += (ts(c["end"]) - ts(c["start"])).total_seconds() / 60.0
    walk(meta)
    return jobs, span_min, span


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # work_path: an argparse default is built before parse_args, and `os.makedirs(a.outdir)` below
    # is where the directory actually appears -- `--help` must create nothing.
    ap.add_argument("--outdir", default=str(config.work_path("metadata")))
    ap.add_argument("--target", action="append", metavar="KEY",
                    help="e.g. 10-new / 06-baseline (default: every step found on both sides)")
    ap.add_argument("--step", action="append", choices=sorted(STEPS),
                    help="restrict discovered targets to these step numbers")
    ap.add_argument("--side", action="append", choices=("new", "baseline"),
                    help="restrict discovered targets to one side of the head-to-head")
    ap.add_argument("--ns", help="explicit target: workspace namespace")
    ap.add_argument("--ws", help="explicit target: workspace name")
    ap.add_argument("--submission", help="explicit target: submission id")
    ap.add_argument("--workflow", help="explicit target: workflow id")
    ap.add_argument("--per-call", action="store_true", help="list VM-backed attempt counts per call path")
    a = ap.parse_args()

    if a.submission or a.workflow:
        if not (a.submission and a.workflow and a.ns and a.ws):
            raise SystemExit("an explicit target needs all four of --ns --ws --submission --workflow")
        wanted = {"explicit": (a.ns, a.ws, a.submission, a.workflow)}
    else:
        wanted = targets()
        if a.step:
            wanted = {k: v for k, v in wanted.items() if k[:2] in a.step}
        if a.side:
            wanted = {k: v for k, v in wanted.items() if k.split("-", 1)[1] in a.side}
        if a.target:
            missing = set(a.target) - set(wanted)
            if missing:
                raise SystemExit(f"not found on either side: {sorted(missing)}"
                                 f" (available: {sorted(wanted) or 'none'})")
            wanted = {k: wanted[k] for k in a.target}
    if not wanted:
        raise SystemExit("no submissions found -- has the chain been run in the configured "
                         "workspace? (GSVTK_TERRA_NAMESPACE / GSVTK_TERRA_WORKSPACE)")

    os.makedirs(a.outdir, exist_ok=True)
    for key in sorted(wanted):
        ns, ws, sub, wf = wanted[key]
        meta = fetch_tree(ns, ws, sub, wf)
        if meta is None:
            print(f"  {key}: fetch failed - not saved")
            continue
        path = os.path.join(a.outdir, f"{key}.{wf[:8]}.json")
        with open(path, "w") as fh:
            json.dump(meta, fh)
        jobs, vm, span = vm_minutes(meta)
        wall = (ts(meta["end"]) - ts(meta["start"])).total_seconds() / 60.0
        missing = count_missing(meta)
        print(f"  {key:12s} wall {wall:8.1f} min | VM jobs {len(jobs):4d} | nested VM-min {vm:9.1f}"
              f" | root call-span {span:8.1f} | {os.path.getsize(path)/1e6:.1f} MB -> {path}"
              + (f"  ** {missing} sub-workflow(s) FAILED to fetch: tree TRUNCATED, cost is a floor"
                 if missing else "  (tree complete)"))
        if a.per_call:
            agg = {}
            for name, mins in jobs:
                c, m = agg.get(name, (0, 0.0))
                agg[name] = (c + 1, m + mins)
            for name in sorted(agg, key=lambda k: -agg[k][1]):
                print(f"        {agg[name][0]:4d} attempts  {agg[name][1]:8.1f} VM-min  {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
