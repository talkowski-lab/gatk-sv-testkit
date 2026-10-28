#!/usr/bin/env python3
"""Persist Cromwell workflow metadata for the sandbox submissions to disk.

Saving the raw expanded metadata lets `batch_cost.py` recompute costs offline.

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
from terra import fapi  # noqa: E402
import batch_configs as tc  # noqa: E402
import steps  # noqa: E402
# `quality` is re-exported for batch_cost.py.
from tree_quality import MAX_DEPTH, quality  # noqa: E402, F401

# Baseline and sandbox runs live in different workspaces; steps match by numeric config prefix.
def sides() -> dict:
    """side -> (namespace, workspace), resolved on call so an unset profile fails late."""
    ns, ws, _ = tc.require_target()
    return {"new": (ns, ws), "baseline": (terra.BASELINE_NS, terra.BASELINE_WS)}
STEPS = steps.STEPS


def targets() -> dict:
    """key -> (namespace, workspace, submission, workflow), discovered per workspace.

    Use --ns/--ws/--submission/--workflow to pin a workflow explicitly.
    """
    out = {}
    for side, (ns, ws) in sides().items():
        for step, wdl in STEPS.items():
            try:
                found = terra.latest_workflow(ns, ws, f"{step}-")
            except terra.TerraError as e:
                # Report once per side; the error is the same for every step.
                print(f"  [skip] {side} side ({ns}/{ws}): {str(e).splitlines()[0]}", file=sys.stderr)
                break
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


def fetch_tree(ns, ws, sub, wf, depth=0, max_depth=MAX_DEPTH):
    """Recursively fetch a workflow's metadata plus every nested sub-workflow's.

    Terra's Cromwell does not expand sub-workflows even with expand_sub_workflows=True, so
    each `subWorkflowId` is fetched separately.
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
                    if child is None:  # retry once; a missing subtree under-counts cost
                        child = fetch_tree(ns, ws, sub, sid, depth + 1, max_depth)
                    if child is not None:
                        child["_callName"] = name
                        kids.append(child)
                    else:
                        meta["_missingSubWorkflows"] = (meta.get("_missingSubWorkflows") or []) + [sid]
    else:
        # Record what the depth cap left unexpanded so callers know the tree is truncated.
        blocked = [c.get("subWorkflowId") for calls in (meta.get("calls") or {}).values()
                   for c in calls if c.get("subWorkflowId")]
        if blocked:
            meta["_unexpandedSubWorkflows"] = blocked
    meta["_children"] = kids
    return meta


def vm_minutes(meta):
    """Return (jobs, nested VM minutes, root call span) for a metadata tree.

    A job is any call record, at any depth, with both vmStartTime and vmEndTime."""
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
    # The directory is created after parsing, so --help creates nothing.
    ap.add_argument("--outdir", default=str(config.work_path("metadata")))
    ap.add_argument("--target", action="append", metavar="KEY",
                    help="e.g. 10-new / 06-baseline (default: every step found on both sides)")
    ap.add_argument("--step", action="append", choices=steps.known_steps(),
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
        wall = ((ts(meta["end"]) - ts(meta["start"])).total_seconds() / 60.0
                if meta.get("end") and meta.get("start") else float("nan"))  # still running
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
