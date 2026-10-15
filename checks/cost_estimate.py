#!/usr/bin/env python3
"""What did this launch cost in dollars -- and what this repo refuses to claim about it.

`terra/batch_status.py` deliberately answers VM-minutes and job counts and prints the sentence `never a price`. That was
the right call for a watcher (a live watcher has no business inventing a rate), but it leaves the question the objective
asks -- what did it COST -- with no answer in the repo. This is that answer, with its honesty conditions built in:

  * A price comes from a rates file with a `source` and an `as_of` date. A number without both is a claim, so it is
    refused, not printed. GCP prices change and differ by region; a figure that cannot say where its rate came from is
    not auditable a week later.
  * Money is priced from **vCPU-hours**, computed per call as `cpu x (end - start)`. `cpu` is in Cromwell's
    `runtimeAttributes` for the calls that declared it; RAM is NOT in this metadata at all (measured: 2332 of 2562 call
    entries carry `cpu`; `ram` appears nowhere, and neither does `machineType`). So the figure is vCPU-only unless the
    rates file supplies `ram_gb_hour` AND the metadata grows a `ram` key, and the output says which one it is. Real GCP
    pricing bills memory separately, so an unpriced RAM term is stated as a floor ("at least this"), never hidden.
  * An entry with no `cpu`, or with a missing start/end, is counted and named as UNKNOWN. It is never summed as zero --
    the same rule `--costs` in `batch_status.py` applies to an unknown config: an unknown priced as nothing is a
    discount nobody approved.

    checks/cost_estimate.py --metadata /tmp/md2.json --rates prices/us-central1.json
    checks/cost_estimate.py --selftest
"""

import argparse
import json
import sys
from datetime import datetime

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
    """A number, or None. Cromwell renders runtimeAttributes values as STRINGS (`cpu: "1"`), so a bare isinstance
    check rejects every real entry and prices a 1589-VM-minute run at $0.00 while printing `0 call(s)` -- which reads
    like a cheap run, not like a parser that matched nothing. Accepts int, float, and a numeric string; refuses units."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


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
                if isinstance(sub, dict) and depth < 6:
                    walk(sub, depth + 1)
        sub = node.get("subWorkflowMetadata")
        if isinstance(sub, dict) and depth < 6:
            walk(sub, depth + 1)

    walk(meta, 0)
    return {"vcpu_hours": vcpu_hours, "ram_gb_hours": ram_gb_hours, "unknown": unknown, "priced_calls": ran}


def price(u: dict, rates: dict) -> list:
    """The report lines. Every number on them says what it was multiplied by and where the rate came from."""
    bundled = rates.get("bundled_ram_gb_per_vcpu")
    if u["ram_gb_hours"]:
        ram_note = f"; {u['ram_gb_hours']:.2f} GB-hours priced separately"
    elif bundled:
        ram_note = (f"; RAM is bundled in the quoted rate at {bundled} GB/vCPU, so a task that asked for more "
                    "memory per vCPU than that is UNDERSTATED, not free")
    else:
        ram_note = "; RAM absent from this metadata, so memory is NOT priced and the total is a FLOOR"
    lines = [f"source: {rates['source']} (read {rates['as_of']})",
             f"priced: {u['priced_calls']} call(s); {u['vcpu_hours']:.2f} vCPU-hours{ram_note}"]
    if u["unknown"]:
        lines.append(f"UNKNOWN, not summed as zero: {len(u['unknown'])} call(s) lack cpu or start/end -- "
                     f"e.g. {', '.join(u['unknown'][:3])}")
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
        u = usage(json.load(fh))
    try:
        rates = load_rates(a.rates)
    except SystemExit as e:
        return int(e.code)
    for line in price(u, rates):
        print("  " + line)
    return 0


def _selftest() -> int:
    """Expected numbers derived by hand from the fixture, never read back off this tool.

    cpu 4 for 1800 s = 2.00 vCPU-h; cpu 2 for 3600 s = 2.00 vCPU-h -> 4.00 vCPU-h.
    At 0.03/vCPU-h that is $0.12; at 0.015 it is $0.06.
    """
    ok = bad = 0
    meta = {"status": "Succeeded", "calls": {"W.X": [
        {"shardIndex": 0, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T12:30:00Z",
         "runtimeAttributes": {"cpu": 4}},
        {"shardIndex": 1, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T13:00:00Z",
         "runtimeAttributes": {"cpu": 2}},
        {"shardIndex": 2, "start": "2026-10-09T12:00:00Z", "end": "2026-10-09T12:10:00Z",
         "runtimeAttributes": {}}]}}
    rates = {"source": "synthetic-fixture", "as_of": "2026-10-09", "vcpu_hour": 0.03, "vcpu_hour_spot": 0.015}
    u = usage(meta)
    lines = price(u, rates)

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
    print(f"cost_estimate: {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
