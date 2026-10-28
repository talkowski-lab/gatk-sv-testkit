#!/usr/bin/env python3
"""Price a finished launch in dollars from its expanded Cromwell metadata.

  * Rates come from a JSON file that must carry `source` and `as_of`; a file without them is refused.
  * Cost is priced from vCPU-hours (`cpu` x wall time per call). Cromwell metadata has no RAM or machine type, so
    memory is unpriced unless the rates bundle it, and the output says the total is a floor.
  * A call with no `cpu` or no start/end is reported as UNKNOWN, never summed as zero.
  * Truncation markers written by `terra/batch_save_metadata.py` are read through `terra/tree_quality.py` (the
    same reader `terra/batch_cost.py` uses). A truncated tree prints vCPU-hours as a floor and no dollar figure
    (exit 4). A dump with no marker is reported as `tree: NO MARKER` and still priced.

Exit: 0 priced (a complete or an unmarked tree), 2 arguments missing, 4 the answer is not there -- a rates file that
cannot name its source, or a tree that reports sub-workflows it could not read.

    checks/cost_estimate.py --metadata /tmp/md2.json --rates prices/us-central1.json
    checks/cost_estimate.py --selftest
"""

import argparse
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "terra"))
# Shared with terra/batch_cost.py so both tools agree on truncation. Stdlib-only, unlike batch_save_metadata.
from tree_quality import MARKER_CHILDREN, MAX_DEPTH, is_marked, quality  # noqa: E402

PRICE_KEYS = ("vcpu_hour", "vcpu_hour_spot", "ram_gb_hour")


def load_rates(path: str) -> dict:
    """Read a rates file, refusing one that cannot say where its numbers came from."""
    with open(path, encoding="utf-8") as fh:
        r = json.load(fh)
    missing = [k for k in ("source", "as_of") if not r.get(k)]
    if missing:
        print(f"  RATES UNUSABLE: {', '.join(missing)} absent from {path}. A dollar figure has to name the price "
              "source and the date it was read, because GCP prices move and differ by region.")
        raise SystemExit(4)
    if not any(k in r for k in PRICE_KEYS):
        print(f"  RATES UNUSABLE: {path} carries none of {list(PRICE_KEYS)}. Nothing to multiply.")
        raise SystemExit(4)
    return r


def _num(value):
    """A number, or None. Accepts int, float, or a numeric string (Cromwell renders runtimeAttributes as strings)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def tree_state(meta: dict) -> dict:
    """Truncation state from the writer's markers: complete, TRUNCATED, or NO MARKER (not written by
    batch_save_metadata.py, so wholeness is unknown).
    """
    q = quality(meta)
    return {"unreadable": q["fetch_failed"], "unexpanded": q["unexpanded"], "marked": is_marked(meta)}


def usage(meta: dict) -> dict:
    """Per-call `cpu` x wall time over the whole expanded tree, with the unpriceable named rather than dropped."""
    vcpu_hours = 0.0
    ram_gb_hours = 0.0
    unknown = []
    ran = 0

    def span(c):
        st, en = c.get("start"), c.get("end")
        if not st or not en:
            return None
        try:
            a = datetime.fromisoformat(str(st).replace("Z", "+00:00"))
            b = datetime.fromisoformat(str(en).replace("Z", "+00:00"))
        except ValueError:
            return None
        secs = (b - a).total_seconds()
        return secs if secs > 0 else None

    def walk(node, depth):
        nonlocal vcpu_hours, ram_gb_hours, ran
        for cr, calls in (node.get("calls") or {}).items():
            leaf = cr.rsplit(".", 1)[-1]
            for c in calls:
                ra = c.get("runtimeAttributes") or {}
                cpu = _num(ra.get("cpu"))
                ram = _num(ra.get("ram"))
                secs = span(c)
                if cpu is None or secs is None:
                    if len(unknown) < 40:
                        unknown.append(f"{leaf} shard={c.get('shardIndex')} "
                                       f"cpu={'-' if cpu is None else cpu} "
                                       f"span={'-' if secs is None else 'ok'}")
                else:
                    ran += 1
                    vcpu_hours += cpu * secs / 3600.0
                    if ram is not None:
                        ram_gb_hours += ram * secs / 3600.0
                sub = c.get("subWorkflowMetadata")
                if isinstance(sub, dict) and depth < MAX_DEPTH:
                    walk(sub, depth + 1)
        sub = node.get("subWorkflowMetadata")
        if isinstance(sub, dict) and depth < MAX_DEPTH:
            walk(sub, depth + 1)
        # batch_save_metadata.py saves its fetched sub-workflows under `_children`, not `subWorkflowMetadata`.
        for kid in (k for k in (node.get(MARKER_CHILDREN) or []) if isinstance(k, dict)):
            if depth < MAX_DEPTH:
                walk(kid, depth + 1)

    walk(meta, 0)
    return {"vcpu_hours": vcpu_hours, "ram_gb_hours": ram_gb_hours, "unknown": unknown, "priced_calls": ran}


def price(u: dict, rates: dict, tree: dict = None) -> list:
    """The report lines, each naming its rate and source.

    `tree` is `tree_state()` for the same metadata; None omits the tree line (selftest.sh compares that output).
    """
    bundled = rates.get("bundled_ram_gb_per_vcpu")
    if u["ram_gb_hours"]:
        ram_note = f"; {u['ram_gb_hours']:.2f} GB-hours priced separately"
    elif bundled:
        ram_note = (f"; RAM is bundled in the quoted rate at {bundled} GB/vCPU, so a task that asked for more "
                    "memory per vCPU than that is UNDERSTATED, not free")
    else:
        ram_note = "; RAM absent from this metadata, so memory is NOT priced and the total is a FLOOR"
    lines = [f"source: {rates['source']} (read {rates['as_of']})"]
    unreadable, unexpanded, marked = 0, 0, True
    if tree is not None:
        unreadable, unexpanded, marked = tree["unreadable"], tree["unexpanded"], tree["marked"]
        if unreadable or unexpanded:
            lines.append(f"tree: TRUNCATED -- {unreadable} sub-workflow(s) FAILED to fetch, {unexpanded} "
                         "sub-workflow call(s) have no expanded tree below them (this file's own markers say so)")
        elif marked:
            lines.append(f"tree: COMPLETE -- this file carries the writer's '{MARKER_CHILDREN}' marker and names "
                         "0 unreadable sub-workflow(s)")
        else:
            lines.append(f"tree: NO MARKER -- no '{MARKER_CHILDREN}' in it, so this dump did not come from "
                         "terra/batch_save_metadata.py and cannot say whether its sub-workflow tree is whole "
                         "(the server prunes it). The figure below may be a floor; re-fetch with "
                         "batch_save_metadata.py to make it a measurement")
    lines.append(f"priced: {u['priced_calls']} call(s); {u['vcpu_hours']:.2f} vCPU-hours{ram_note}"
                 + (f"  FLOOR({unreadable} sub-workflow(s) unreadable, {unexpanded} unexpanded)"
                    if (unreadable or unexpanded) else ""))
    if u["unknown"]:
        lines.append(f"UNKNOWN, not summed as zero: {len(u['unknown'])} call(s) lack cpu or start/end -- "
                     f"e.g. {', '.join(u['unknown'][:3])}")
    if unreadable or unexpanded:
        # Withhold the dollar line: a floor times a rate reads like a real cost once copied out of context.
        lines.append(f"TREE TRUNCATED, NO DOLLAR FIGURE: this file's own markers report {unreadable + unexpanded} "
                     "sub-workflow(s) below this one as unreadable or never expanded, so the vCPU-hours above are "
                     "the visible half and any product of them is a lower bound wearing a price.")
        lines.append("        Re-fetch while the metadata is still readable -- terra/batch_save_metadata.py "
                     "--target <step>-<side> --outdir <dir> -- and price the tree it writes; "
                     "docs/terra-head-to-head.md")
        return lines
    for key, label in (("vcpu_hour", "on-demand"), ("vcpu_hour_spot", "spot")):
        rate = rates.get(key)
        if isinstance(rate, (int, float)):
            dollars = u["vcpu_hours"] * rate + (u["ram_gb_hours"] * rates["ram_gb_hour"]
                                                if u["ram_gb_hours"] and rates.get("ram_gb_hour") else 0.0)
            lines.append(f"${dollars:,.2f}  {label:<10} @ ${rate}/vCPU-hour")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Price a finished launch in dollars from its own metadata.")
    ap.add_argument("--metadata", help="expanded Cromwell metadata JSON")
    ap.add_argument("--rates", help="rates JSON with source + as_of (see module docstring)")
    ap.add_argument("--selftest", action="store_true", help="grade synthetic fixtures with hand-derived totals")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not (a.metadata and a.rates):
        print("  pass --metadata and --rates (or --selftest). This tool will not guess a price.")
        return 2
    with open(a.metadata, encoding="utf-8") as fh:
        meta = json.load(fh)
    u, tree = usage(meta), tree_state(meta)
    try:
        rates = load_rates(a.rates)
    except SystemExit as e:
        return int(e.code)
    for line in price(u, rates, tree):
        print("  " + line)
    # 4 means "a needed value is missing", distinct from 1 ("found a difference").
    return 4 if (tree["unreadable"] or tree["unexpanded"]) else 0


def _selftest() -> int:
    """Expected numbers derived by hand from the fixture, never read back off this tool.

    cpu 4 for 1800 s = 2.00 vCPU-h; cpu 2 for 3600 s = 2.00 vCPU-h -> 4.00 vCPU-h.
    At 0.03/vCPU-h that is $0.12; at 0.015 it is $0.06.

    Tree fixtures: `whole` is parent cpu 4 x 1800 s + child cpu 2 x 1800 s = 3.00 vCPU-h ($0.09); `capped` has 2
    unexpanded ids plus 1 call with no child tree = 3 unexpanded.
    """
    ok = bad = 0
    meta = {"status": "Succeeded", "calls": {"W.X": [
        {"shardIndex": 0, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T12:30:00Z",
         "runtimeAttributes": {"cpu": 4}},
        {"shardIndex": 1, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T13:00:00Z",
         "runtimeAttributes": {"cpu": 2}},
        {"shardIndex": 2, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T12:10:00Z",
         "runtimeAttributes": {}}]}}
    # `trunc`: a fetch that could not read some sub-workflows. `whole`: the child tree saved under `_children`.
    trunc = {"status": "Succeeded", "calls": {"W.Sub": [
        {"shardIndex": 0, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T12:04:00Z",
         "runtimeAttributes": {"cpu": "2"}, "subWorkflowId": "kid-0"}]},
        "_missingSubWorkflows": ["kid-1", "kid-2"], "_children": []}
    whole = {"status": "Succeeded", "calls": {"W.Sub": [
        {"shardIndex": 0, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T12:30:00Z",
         "runtimeAttributes": {"cpu": "4"}, "subWorkflowId": "kid-0"}]},
        "_children": [{"_callName": "W.Sub", "calls": {"W.Sub.Inner": [
            {"shardIndex": 0, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T12:30:00Z",
             "runtimeAttributes": {"cpu": 2}}]}}]}
    # `capped`: a fetch that hit its depth cap. Nothing failed; only `_unexpandedSubWorkflows` records it.
    capped = {"status": "Succeeded", "calls": {"W.Sub": [
        {"shardIndex": 0, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T12:04:00Z",
         "runtimeAttributes": {"cpu": "2"}, "subWorkflowId": "kid-9"}]},
        "_unexpandedSubWorkflows": ["kid-9", "kid-10"], "_children": []}
    rates = {"source": "synthetic-fixture", "as_of": "2026-10-09", "vcpu_hour": 0.03, "vcpu_hour_spot": 0.015}
    u = usage(meta)
    lines = price(u, rates)
    tl = price(u, rates, tree_state(meta))
    tu = usage(trunc)
    tn = price(tu, rates, tree_state(trunc))
    cu = usage(whole)
    cn = price(cu, rates, tree_state(whole))
    dn = price(usage(capped), rates, tree_state(capped))

    def check(label, cond):
        nonlocal ok, bad
        print(f"  {'ok  ' if cond else 'FAIL'} {label}")
        if cond:
            ok += 1
        else:
            bad += 1

    check("4.00 vCPU-hours, hand-derived (4x0.5h + 2x1.0h)", abs(u["vcpu_hours"] - 4.0) < 1e-9)
    check("the call with no cpu is UNKNOWN and not summed", len(u["unknown"]) == 1 and u["priced_calls"] == 2)
    check("on-demand figure $0.12 appears", any("$0.12" in ln and "on-demand" in ln for ln in lines))
    check("spot figure $0.06 appears", any("$0.06" in ln and "spot" in ln for ln in lines))
    check("the rate source and date are echoed", any("synthetic-fixture" in ln and "2026-10-09" in ln for ln in lines))
    check("and RAM's absence is stated, not silently priced", any("FLOOR" in ln for ln in lines))

    # Tree markers: a truncated dump prints no dollar figure.
    check("a dump with no writer marker is NO MARKER, never 'complete'",
          not tree_state(meta)["marked"] and any("NO MARKER" in ln for ln in tl)
          and any("$0.12" in ln and "on-demand" in ln for ln in tl))
    check("a marker-named truncation is TRUNCATED with the count it names, and so is a depth-capped tree with "
          "nothing unreadable (0 FAILED, 2 named unexpanded + the call under it)",
          tree_state(trunc)["unreadable"] == 2 and tree_state(trunc)["unexpanded"] == 1
          and any("TRUNCATED" in ln for ln in tn)
          and tree_state(capped) == {"unreadable": 0, "unexpanded": 3, "marked": True}
          and any("tree: TRUNCATED -- 0 sub-workflow(s) FAILED to fetch, 3 sub-workflow call(s) have no expanded "
                  "tree below them" in ln for ln in dn)
          and not any(ln.lstrip().startswith("$") for ln in dn))
    check("a truncated tree prints NO dollar line at all", not any(ln.lstrip().startswith("$") for ln in tn))
    check("and labels the figure with the existing FLOOR vocabulary, over the visible half only",
          any("FLOOR(2 sub-workflow(s) unreadable" in ln for ln in tn)
              and any(ln.startswith("priced: 1 call(s); 0.13 vCPU-hours") for ln in tn)
              and abs(tu["vcpu_hours"] - 0.1333) < 1e-3)
    check("a marked, whole tree is COMPLETE and prices its child under `_children` (2.00 + 1.00 = 3.00)",
          tree_state(whole) == {"unreadable": 0, "unexpanded": 0, "marked": True}
          and abs(cu["vcpu_hours"] - 3.0) < 1e-9
          and any("$0.09" in ln and "on-demand" in ln for ln in cn)
          and any("tree: COMPLETE" in ln for ln in cn))
    print(f"cost_estimate: {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
