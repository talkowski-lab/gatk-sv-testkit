#!/usr/bin/env python3
"""Recompute the head-to-head cost table from the Cromwell metadata ALREADY ON DISK.

`batch_save_metadata.py` re-fetches from Terra and rewrites `work/metadata/*.json`;
this script never touches the network, so it is the cheap, repeatable check on any cost
figure you publish. If the two disagree, this one is the one you can re-run.

    python terra/batch_cost.py            # per-step + chain totals
    python terra/batch_cost.py --groups 10-baseline
    python terra/batch_cost.py --json      # machine-readable

Accounting (identical to `batch_save_metadata.vm_minutes`, imported, not re-implemented):
  VM-min = SUM(vmEndTime - vmStartTime) over EVERY call record at ANY nesting depth that
  carries both timestamps; jobs = the count of those records. Root `call start->end` spans
  are printed separately because they are what produced the bogus "382"/"885" figures.
A metadata tree with `_missingSubWorkflows` is a FLOOR, not a measurement - it is flagged.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
from batch_save_metadata import count_missing, vm_minutes  # noqa: E402

STEP_RE = re.compile(r"^(\d\d)-(new|baseline)\.")


def load(outdir: str) -> dict:
    out = {}
    for path in sorted(glob.glob(os.path.join(outdir, "*.json"))):
        step, side = STEP_RE.match(os.path.basename(path)).groups()
        with open(path) as fh:
            out[(step, side)] = (path, json.load(fh))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--outdir", default=str(config.work_dir("metadata")))
    ap.add_argument("--groups", action="append", default=[],
                    help="also print the top-level call-group roll-up for this target (e.g. 10-baseline)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    data = load(a.outdir)
    steps = sorted({s for s, _ in data})
    rows, totals, warns = [], {s: {"new": [0.0, 0], "baseline": [0.0, 0]} for s in steps}, []
    for s in steps:
        row = {"step": s}
        for side in ("new", "baseline"):
            key = (s, side)
            if key not in data:
                row[side], row[f"{side}_span"], row[f"{side}_wall"] = None, None, None
                continue
            path, meta = data[key]
            jobs, vm, span = vm_minutes(meta)
            missing = count_missing(meta)
            wall = 0.0
            if meta.get("start") and meta.get("end"):
                from batch_save_metadata import ts
                wall = (ts(meta["end"]) - ts(meta["start"])).total_seconds() / 60.0
            row[side] = (vm, len(jobs))
            row[f"{side}_span"] = span
            row[f"{side}_wall"] = wall
            totals[s][side] = [vm, len(jobs)]
            if missing:
                warns.append(f"{s}/{side}: {missing} sub-workflow(s) FAILED to fetch -> cost is a FLOOR")
        rows.append(row)

    chain = {side: [sum(totals[s][side][0] for s in steps if totals[s][side][0] is not None),
                    sum(totals[s][side][1] for s in steps if totals[s][side][1] is not None)]
             for side in ("new", "baseline")}

    if a.json:
        print(json.dumps({"per_step": [{k: v for k, v in r.items()} for r in rows],
                          "chain": chain, "warnings": warns}, indent=1, default=str))
        return 0

    print("# Terra chain 06->10 cost, recomputed from saved metadata (no network)")
    print(f"#   {'step':22s} {'new VM-min (jobs)':>20s}   {'v1.1.1 VM-min (jobs)':>21s}   "
          f"{'new wall':>8s} {'base wall':>9s}   {'new span':>9s} {'base span':>9s}")
    for r in rows:
        def cell(v):
            return "        n/a        " if v is None else f"{v[0]:12.1f} ({v[1]:4d})"
        print(f"#   {r['step']:22s} {cell(r['new']):>20s}   {cell(r['baseline']):>21s}   "
              f"{(r.get('new_wall') or 0):8.1f} {(r.get('baseline_wall') or 0):9.1f}   "
              f"{(r.get('new_span') or 0):9.1f} {(r.get('baseline_span') or 0):9.1f}")
    print(f"#   {'chain 06->10':22s} {chain['new'][0]:12.1f} ({chain['new'][1]:4d})   "
          f"{chain['baseline'][0]:12.1f} ({chain['baseline'][1]:4d})")
    # A fresh clone has no saved metadata, so both sides are legitimately 0 here. Dividing by
    # that is not a ratio, and printing it as one would be worse than printing nothing.
    if chain['baseline'][0] and chain['baseline'][1]:
        print(f"#   ratio new/baseline: cost {chain['new'][0] / chain['baseline'][0]:.3f}"
              f"  jobs {chain['new'][1] / chain['baseline'][1]:.3f}")
    else:
        print("#   ratio new/baseline: n/a (no baseline VM-minutes in the saved metadata -- run\n"
              "#     terra/batch_save_metadata.py --outdir <dir> first; see docs/terra-head-to-head.md)")
    for w in warns:
        print(f"  !! {w}")
    if not warns:
        print("  every metadata tree complete -> these are measurements, not floors")

    for target in a.groups:
        step, side = target.split("-", 1)
        if (step, side) not in data:
            print(f"  (no saved metadata for {target})")
            continue
        _, meta = data[(step, side)]
        jobs, _, _ = vm_minutes(meta)
        grp = defaultdict(float)
        for name, mins in jobs:
            grp[name.split("/")[0]] += mins
        print(f"\n# {target}: VM-min by top-level call group")
        for g, v in sorted(grp.items(), key=lambda kv: -kv[1]):
            print(f"    {v:9.1f}  {g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
