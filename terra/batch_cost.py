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

What is missing is printed alongside what is present, because every one of these forms produces a
tidy wrong number: a step with no saved metadata counted as ZERO, a sub-workflow whose tree was
never expanded counted as its one parent call, a call with no `vmEndTime` dropped from both the
minutes and the job count. The chain total and the new/baseline ratio are the numbers people quote,
so they are refused (exit 1) unless every step of both sides is present and every tree is expanded.
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
from batch_save_metadata import count_missing, quality, vm_minutes  # noqa: E402

STEP_RE = re.compile(r"^(\d\d)-(new|baseline)\.")


def load(outdir: str) -> dict:
    """(step, side) -> (path, metadata) for step dumps in `outdir`; anything else is skipped.

    A `*.json` that is not a step dump used to raise AttributeError from `STEP_RE.match(...)`
    returning None, so putting any other file in the directory (an extra submission dump, a
    `06-new.copy.json`) killed the whole table. Skipping is correct: this directory is a bag of
    step dumps, not an exclusive one.
    """
    out = {}
    for path in sorted(glob.glob(os.path.join(outdir, "*.json"))):
        m = STEP_RE.match(os.path.basename(path))
        if not m:
            continue
        with open(path) as fh:
            out[(m.group(1), m.group(2))] = (path, json.load(fh))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # work_path, not work_dir: an argparse default is evaluated before parse_args, so `--help`
    # would otherwise create the scratch directory it is only describing.
    ap.add_argument("--outdir", default=str(config.work_path("metadata")))
    ap.add_argument("--groups", action="append", default=[],
                    help="also print the top-level call-group roll-up for this target (e.g. 10-baseline)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    data = load(a.outdir)
    if not data:
        print(f"# no step metadata in {a.outdir} (expected files like 06-new.json / 06-baseline.json)")
        print("#   fetch it with: terra/batch_save_metadata.py --outdir <dir> -- docs/terra-head-to-head.md")
        return 1
    steps = sorted({s for s, _ in data})
    rows, totals, warns = [], {s: {"new": [0.0, 0], "baseline": [0.0, 0]} for s in steps}, []
    qtot = {"fetch_failed": 0, "unexpanded": 0, "half_timestamped": 0}
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
            q = quality(meta)
            for k in qtot:
                qtot[k] += q[k]
            if q["unexpanded"]:
                warns.append(f"{s}/{side}: {q['unexpanded']} sub-workflow call(s) have no expanded tree "
                             f"below them (depth cap, older or trimmed file) -> cost is a FLOOR")
            if q["half_timestamped"]:
                warns.append(f"{s}/{side}: {q['half_timestamped']} call(s) have only one vm timestamp "
                             f"(running/aborted) -> excluded from BOTH minutes and job count")
        rows.append(row)

    # A step with no saved metadata was `continue`d above, so its totals row stayed at [0.0, 0]
    # and the chain summed it as zero: deleting one baseline file flipped the published verdict
    # from "4.5x cheaper" to "20x more expensive" while the header still said `n/a` up top and the
    # exit code stayed 0. Name the holes, refuse the ratio, fail the run.
    holes = [f"{s}/{side}" for s in steps for side in ("new", "baseline") if (s, side) not in data]

    chain = {side: [sum(totals[s][side][0] for s in steps if totals[s][side][0] is not None),
                    sum(totals[s][side][1] for s in steps if totals[s][side][1] is not None)]
             for side in ("new", "baseline")}

    if a.json:
        print(json.dumps({"per_step": [{k: v for k, v in r.items()} for r in rows],
                          "chain": chain, "chain_complete": not holes,
                          "missing_steps": holes, "tree_quality": qtot,
                          "warnings": warns}, indent=1, default=str))
        return 1 if holes else 0

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
          f"{chain['baseline'][0]:12.1f} ({chain['baseline'][1]:4d})"
          + ("   <- PARTIAL" if holes else ""))
    if holes:
        print(f"#   chain total is PARTIAL: no saved metadata for {', '.join(holes)}.")
        print("#   Those steps counted as ZERO above. Fill them in with terra/batch_save_metadata.py")
        print("#   before quoting a chain total or a ratio.")
    # A fresh clone has no saved metadata, so both sides are legitimately 0 here. Dividing by
    # that is not a ratio, and printing it as one would be worse than printing nothing.
    if holes:
        print("#   ratio new/baseline: n/a (chain incomplete -- see PARTIAL above)")
    elif chain['baseline'][0] and chain['baseline'][1]:
        print(f"#   ratio new/baseline: cost {chain['new'][0] / chain['baseline'][0]:.3f}"
              f"  jobs {chain['new'][1] / chain['baseline'][1]:.3f}")
    else:
        print("#   ratio new/baseline: n/a (no baseline VM-minutes in the saved metadata -- run\n"
              "#     terra/batch_save_metadata.py --outdir <dir> first; see docs/terra-head-to-head.md)")
    for w in warns:
        print(f"  !! {w}")
    if not warns and not holes:
        print("  every step present on both sides, every tree expanded -> these are measurements")
    elif not warns and holes:
        print("  trees that exist are expanded, but the chain is incomplete -> see PARTIAL above")
    else:
        print(f"  these are FLOORS, not measurements (fetch_failed={qtot['fetch_failed']}, "
              f"unexpanded={qtot['unexpanded']}, half-timestamped={qtot['half_timestamped']})")

    rc = 1 if holes else 0
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
    return rc


if __name__ == "__main__":
    sys.exit(main())
