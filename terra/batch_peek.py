#!/usr/bin/env python3
"""Bounded call-level peek: what is running now, what retried, what broke first, is it still writing.

The thing that answered "is it progressing or is the image broken" on all five trio runs was a 1.4 KB
`gsvpeek.sh` in a scratch directory — it read this repo's own config, bypassed fiss with curl, and
printed a bounded `executionStatus` tally (GAP-REVIEW-trio-calling.md §1 and §6 rec 6). Every branch
hand-rolled it again. This is that tool, in the repo it was always being called from.

Two measurements decide the shape (both in docs/terra-head-to-head.md §8):

  * Per-workflow metadata is a CACHED SNAPSHOT — two fetches 30+ min apart returned byte-identical JSON
    while the scratch bucket proved the run had advanced. So the `rc` tally over the Cromwell execution
    dir prints beside the call tally: object count and newest object time is the liveness signal that
    cannot go stale.
  * Top-level `calls` UNDER-COUNTS the run — one submission sat at 9 root calls while ~200 tasks ran
    inside one sub-workflow, and `?expandSubWorkflows=true` returns ~45 MB. So this prints a tally and
    never the document; the full call graph is batch_save_metadata.py's job.

Output is bounded by construction (tallies, capped lists). Exit codes, so a loop can drive it:
0 terminal and nothing failed · 2 terminal with failures/aborts · 3 still in flight ·
1 it could not see the submission at all (HTTP status or credential error, printed — never an empty table).

    python terra/batch_peek.py --ns NS --ws WS --submission SUB --workflow WF
    python terra/batch_peek.py --ns NS --ws WS --submission SUB --workflow WF --no-scratch
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import terra  # noqa: E402

# A call in one of these states still has a VM that could write another object. "Aborting" is
# deliberately absent: it is Cromwell still tearing things down, and a run sitting in it for 20
# minutes is exactly the state a peek should surface as not-finished.
TERMINAL = ("Done", "Failed", "Aborted", "ExecutorFailed")
TROUBLE = ("Failed", "Aborted", "ExecutorFailed", "RetryableFailure")

# Bounded output is the tool's one promise, so the caps are named, not buried in the loops.
MAX_INFLIGHT = 12
MAX_RETRIED = 10
MAX_FAILURE_DETAILS = 3
MAX_MESSAGE = 300


def fetch(ns: str, ws: str, submission: str, workflow: str) -> dict:
    """One non-expanded workflow metadata read, over raw REST.

    Same endpoint the scratch-dir peek used (curl `…/submissions/$SID/workflows/$WF`, quoted in the
    branch's own progress doc). `expandSubWorkflows` is deliberately NOT asked for: ~45 MB for a tally
    of it (docs/terra-head-to-head.md §8).
    """
    return terra.rest_get(f"workspaces/{ns}/{ws}/submissions/{submission}/workflows/{workflow}",
                          f"workflow metadata {workflow[:8]} in {ns}/{ws}")


def message_of(rec: dict) -> str:
    """The first sentence Cromwell offers about a call, from whichever key that backend filled in.

    PAPIv2 is inconsistent about where the reason goes (`failureMessage`, `messages`,
    `backendMessages`, `backendStatus`, `statusDetails`), and the run whose message you need is always
    the one that used a key you have not seen. Everything is truncated — this never dumps a record.
    """
    for key in ("failureMessage", "messages", "backendMessages", "backendStatus", "statusDetails"):
        v = rec.get(key)
        if isinstance(v, list):
            v = " | ".join(str(x) for x in v)
        elif isinstance(v, dict):
            v = json.dumps(v, default=str)
        if v is not None and str(v).strip():
            return str(v).strip()[:MAX_MESSAGE]
    return ""


def attempt_summary(records: list) -> str:
    """`attempt 1 RetryableFailure rc=141, attempt 2 Done rc=0` — preemption, spelled out.

    The reason this exists: a call that came back on its second attempt looks like an unstable image
    from `executionStatus` alone, and the branch spent a submission discovering that the first attempt
    was a preemption. Per-call, per-attempt counts settle it without opening a single log.
    """
    by_attempt: dict[int, list] = {}
    for rec in records:
        by_attempt.setdefault(int(rec.get("attempt") or 1), []).append(rec)
    parts = []
    for attempt in sorted(by_attempt):
        recs = by_attempt[attempt]
        statuses = "/".join(sorted({str(r.get("executionStatus") or "Unknown") for r in recs}))
        rcs = "/".join(sorted({str(r["returnCode"]) for r in recs if r.get("returnCode") is not None}))
        parts.append(f"attempt {attempt}" + (f" x{len(recs)}" if len(recs) > 1 else "")
                     + f" {statuses}" + (f" rc={rcs}" if rcs else ""))
    return ", ".join(parts)


def tally(meta: dict) -> dict:
    """Reduce workflow metadata to counts: statuses, in-flight calls, retries, failures, scratch root."""
    calls = meta.get("calls") or {}
    counts: Counter = Counter()
    inflight, retried, failures = [], [], []
    records = 0
    for name, shards in sorted(calls.items()):
        for rec in shards:
            records += 1
            counts[str(rec.get("executionStatus") or "Unknown")] += 1
        status = str(shards[-1].get("executionStatus") or "Unknown") if shards else "Unknown"
        if status not in TERMINAL:
            inflight.append({"call": name, "status": status, "records": len(shards),
                             "start": shards[-1].get("start") if shards else None})
        if max((int(r.get("attempt") or 1) for r in shards), default=1) > 1 \
           or any(str(r.get("executionStatus")) == "RetryableFailure" for r in shards):
            retried.append({"call": name, "summary": attempt_summary(shards)})
        for rec in shards:
            if str(rec.get("executionStatus")) in TROUBLE:
                failures.append({
                    "call": name, "shard": rec.get("shardIndex"),
                    "attempt": int(rec.get("attempt") or 1),
                    "status": str(rec.get("executionStatus")),
                    "rc": rec.get("returnCode"), "message": message_of(rec),
                    "stderr": rec.get("stderr") or rec.get("jobOutputLogs"),
                    "end": rec.get("end") or rec.get("start"),
                })
    failures.sort(key=lambda f: str(f["end"] or ""))
    root_status = meta.get("status") or meta.get("executionStatus") or ""
    return {"status_counts": counts, "call_count": len(calls), "record_count": records,
            "inflight": inflight, "retried": retried, "failures": failures,
            "root_status": str(root_status), "workflow_name": meta.get("workflowName") or "",
            "cost": meta.get("cost"), "start": meta.get("start"), "end": meta.get("end"),
            "scratch_root": meta.get("root") or "",
            "workflow_message": message_of(meta)}


ROOT_TERMINAL = ("Succeeded", "Failed", "Aborted")


def exit_code(counts: Counter, inflight: list, failures: list, root_status: str) -> int:
    """0 clean-and-terminal, 2 terminal with trouble, 3 still in flight.

    "Terminal" comes from the root status when Cromwell has one, and otherwise from the NEWEST record
    per call — never from every record. An exhausted retry leaves a `RetryableFailure` record in the
    metadata of a finished run (that is how the branch's rc=141 double-death reads afterwards), so a
    check that counted every record would report a completed run as in flight forever, and a loop
    driven by this exit code would never end.
    """
    root = str(root_status)
    if root in ROOT_TERMINAL:
        return 2 if failures or root in ("Failed", "Aborted") else 0
    if inflight or root in ("Running", "Starting", "Aborting", "Prelaunched"):
        return 3
    if not counts:
        return 3                       # submitted, nothing started: not clean, just early
    return 2 if failures else 0


def render(t: dict, max_inflight: int = MAX_INFLIGHT, max_retried: int = MAX_RETRIED,
          max_failure_details: int = MAX_FAILURE_DETAILS) -> list:
    """The bounded text. Every list here is capped and says how much it dropped."""
    lines = []
    counts = t["status_counts"]
    tally_text = "  ".join(f"{n} {s}" for s, n in sorted(counts.items(), key=lambda kv: -kv[1]))
    lines.append(f"calls: {tally_text or 'NO CALLS YET (nothing has been submitted/started)'}")
    lines.append(f"  {t['record_count']} call records over {t['call_count']} call name(s); "
                 "top-level only — sub-workflow tasks are NOT counted here "
                 "(docs/terra-head-to-head.md §8: 9 root calls hid ~200 tasks). "
                 "terra/batch_save_metadata.py fetches the whole graph.")
    if t["inflight"]:
        for row in t["inflight"][:max_inflight]:
            lines.append(f"  RUNNING {row['call']} [{row['status']}] shards={row['records']} "
                         f"since={row['start']}")
        if len(t["inflight"]) > max_inflight:
            lines.append(f"  … and {len(t['inflight']) - max_inflight} more call(s) not terminal")
    for row in t["retried"][:max_retried]:
        lines.append(f"  RETRIED {row['call']}: {row['summary']}")
    if len(t["retried"]) > max_retried:
        lines.append(f"  … and {len(t['retried']) - max_retried} more call(s) with retries")

    by_call: Counter = Counter()
    for f in t["failures"]:
        by_call[f["call"]] += 1
    if t["failures"]:
        first = t["failures"][0]
        lines.append(f"FIRST FAILURE {first['call']} shard={first['shard']} "
                     f"attempt={first['attempt']} {first['status']} "
                     f"rc={'?' if first['rc'] is None else first['rc']}")
        lines.append(f"  {first['message'] or '(no message in metadata — read the log below)'}")
        if first["stderr"]:
            lines.append(f"  stderr: {first['stderr']}")
        for f in t["failures"][1:max_failure_details]:
            lines.append(f"  also: {f['call']} shard={f['shard']} attempt={f['attempt']} "
                         f"{f['status']} rc={'?' if f['rc'] is None else f['rc']} "
                         f"{f['message'][:120]}")
        if len(t["failures"]) > max_failure_details:
            lines.append(f"  … and {len(t['failures']) - max_failure_details} more failed records, "
                         "by call: " + ", ".join(f"{n}x{c}" for n, c in by_call.most_common(12)))
    elif counts:
        lines.append("  no failed/aborted call records")
    if t["workflow_message"]:
        lines.append(f"workflow message: {t['workflow_message']}")
    return lines


def rc_tally(prefix: str, run=subprocess.run) -> dict:
    """Count `rc` objects under the Cromwell execution prefix — liveness the cache cannot fake.

    `gsutil ls -r` over the whole execution root rather than a `call-*/**/rc` glob: the globs that look
    shorter are the ones docs/handoff/003 §8 measured as traps (`ls -d <dir>/*/` lists files and hides
    the attempt layout; `-m -d` died on `"ls" command does not support "file://" URLs`). One listing,
    one obvious command, filtered here to the `rc` blobs. The command is printed with the result so a
    person can narrow it by hand.
    """
    cmd = ["gsutil", "ls", "-r", prefix.rstrip("/")]
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=300)
    except FileNotFoundError:
        return {"error": "gsutil is not on PATH (install google-cloud-cli, docs/setup.md) — "
                         "the rc tally could not run; pass --no-scratch to say so on purpose."}
    except Exception as exc:                        # a timeout is a named gap, not an empty tally
        return {"error": f"{cmd[0]} {cmd[1]} raised {type(exc).__name__}: {str(exc)[:200]}"}
    if proc.returncode != 0:
        return {"error": f"{' '.join(cmd)} -> exit {proc.returncode}: "
                         f"{(proc.stderr or proc.stdout or '')[:300].strip()}"}
    count, newest, objects = 0, "", 0
    attempts: Counter = Counter()
    for line in (proc.stdout or "").splitlines():
        parts = line.split()
        objects += 1
        url = parts[-1]
        if len(parts) < 2 or not url.startswith("gs://") or not url.endswith("/rc"):
            continue
        count += 1
        if len(parts) >= 4:                     # `<date> <time> <tz> <size> <url>`
            newest = max(newest, " ".join(parts[:3]))
        # The attempt dir is a path component of the URL, not a column: gsutil prints
        # `<date> <time> <tz> <size> gs://…/call-X/shard-0/attempt-1/rc` as five fields.
        parent = url.rstrip("/").split("/")[-2] if "/" in url.rstrip("/") else "?"
        attempts[parent if parent.startswith("attempt-") else "no attempt dir"] += 1
    return {"command": " ".join(cmd), "rc_count": count, "newest": newest,
            "attempts": dict(attempts), "objects_listed": objects}


def render_scratch(s: dict) -> list:
    if s.get("error"):
        return [f"scratch rc tally: UNAVAILABLE — {s['error']}"]
    att = "  ".join(f"{k}: {v}" for k, v in sorted(s["attempts"].items(), key=lambda kv: str(kv[0])))
    lines = [f"scratch rc tally: {s['rc_count']} rc files "
             f"({s['objects_listed']} objects listed under the execution root)"]
    lines.append(f"  newest: {s['newest'] or 'none — no rc object yet, so no attempt has finished'}"
                 + (f" | by attempt dir: {att}" if att else ""))
    lines.append(f"  {s['command']}")
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ns", help="workspace namespace (explicit target; all four are required)")
    ap.add_argument("--ws", help="workspace name")
    ap.add_argument("--submission", help="submission id")
    ap.add_argument("--workflow", help="workflow id")
    ap.add_argument("--no-scratch", action="store_true",
                    help="skip the gsutil rc tally (the metadata tally still prints; the run may be "
                         "advancing while its cached metadata is not)")
    a = ap.parse_args()
    if not (a.ns and a.ws and a.submission and a.workflow):
        raise SystemExit("an explicit target needs all four of --ns --ws --submission --workflow "
                         "(batch_save_metadata.py's rule; this tool discovers nothing, so a partial "
                         "target cannot be resolved to 'the one you meant')")

    try:
        meta = fetch(a.ns, a.ws, a.submission, a.workflow)
    except terra.TerraError as e:
        # The whole point of this branch: a peek that prints nothing and exits 0 reads like a quiet
        # run. Name the status/body Terra gave instead.
        print(f"cannot see workflow {a.workflow[:8]}: {e}")
        return 1
    if not isinstance(meta, dict):
        print(f"unexpected metadata shape ({type(meta).__name__}) — not walking it")
        return 1

    t = tally(meta)
    print(f"submission {a.submission[:8]}  workflow {a.workflow[:8]}  {t['workflow_name']}  "
          f"status={t['root_status'] or 'unknown'}  cost={t['cost'] if t['cost'] is not None else '?'}"
          f"  {t['start']} -> {t['end'] or '(not finished)'}")
    for line in render(t):
        print(line)
    if a.no_scratch:
        print("scratch rc tally: skipped (--no-scratch). Cached metadata can be byte-identical while "
              "the run advances; this is the check that catches that.")
    elif not t["scratch_root"]:
        print("scratch rc tally: UNKNOWN — this metadata carries no `root` key, so the Cromwell "
              "execution prefix cannot be derived. Not guessing a bucket.")
    else:
        print(f"scratch root: {t['scratch_root']}")
        for line in render_scratch(rc_tally(t["scratch_root"])):
            print(line)
    code = exit_code(t["status_counts"], t["inflight"], t["failures"], t["root_status"])
    print(f"peek exit {code}: " + {0: "terminal, no failed/aborted calls",
                                   2: "terminal WITH failed/aborted calls",
                                   3: "still in flight"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main())
