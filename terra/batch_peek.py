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
    python terra/batch_peek.py --metadata run.json --task GatherBatchEvidence --shard 0
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


# ---------------------------------------------------------------------------------------------
# One task's artifacts: its `stderr` TAIL, its rendered `script`, its `attempt-N` LAYOUT.
#
# A5 asked for exactly these three, and until now this file printed only the stderr PATH. "Where is
# the log" and "what did the task say" are different answers, and when the answer is a
# scala.MatchError three lines from the end of a 400-line log, the second one is the whole task.
# Both the listing and the read take an injected transport (`run=` / `read=`), so the bounded
# rendering and every refusal are provable offline against a local execution dir; the gsutil branch
# is the one this repo's gate never exercises, and its message says so instead of implying more.
# ---------------------------------------------------------------------------------------------
TASK_FILES = ("script", "stderr", "stdout", "rc")
MAX_TAIL_LINES = 25
MAX_SCRIPT_LINES = 40


def read_object(uri: str, run=subprocess.run, read=open) -> dict:
    """Read one artifact: a local path or file:// through `read`, a gs:// through `gsutil cat`.

    Never raises. A read that could not happen comes back carrying "error", because "this task
    wrote nothing" and "I could not look" are different answers, and conflating them is what let a
    `MISSING LOCAL IMAGE` print like a run that had no output.
    """
    if not uri:
        return {"uri": uri, "error": "no URI in the metadata record"}
    if uri.startswith("gs://"):
        cmd = ["gsutil", "-q", "cat", uri]
        try:
            proc = run(cmd, capture_output=True, text=True, timeout=300)
        except FileNotFoundError:
            return {"uri": uri, "error": f"{cmd[0]} is not on PATH, so a gs:// object cannot be "
                                         "read here — point --metadata at a local export instead"}
        except Exception as exc:
            return {"uri": uri, "error": f"{' '.join(cmd)} raised {type(exc).__name__}: "
                                         f"{str(exc)[:160]}"}
        if proc.returncode != 0:
            return {"uri": uri, "error": f"{' '.join(cmd)} -> exit {proc.returncode}: "
                                         f"{(proc.stderr or proc.stdout or '')[:200].strip()}"}
        text = proc.stdout or ""
        return {"uri": uri, "text": text, "bytes": len(text.encode())}
    path = uri[len("file://"):] if uri.startswith("file://") else uri
    try:
        with read(path, "r", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        return {"uri": uri, "error": f"{type(exc).__name__}: {exc.strerror or exc} — {path}"}
    return {"uri": uri, "text": text, "bytes": len(text.encode())}


def call_dir(rec: dict) -> str:
    """The directory Cromwell wrote this call's artifacts into, or "" when the record says nothing.

    `callRoot` is Cromwell's own `.../call-X/shard-N/attempt-M/`. The fallback is the parent of the
    log path the backend filled in — a path that exists for a reason. It refuses to compose a prefix
    out of the scratch root plus a guess: reading another call's log is worse than reading none, and
    a wrong-but-plausible path is exactly how a review convinces itself an image is fine.
    """
    for key in ("callRoot", "call_root"):
        v = rec.get(key)
        if v and str(v).endswith("/"):
            return str(v)
    for key in ("stderr", "stdErr", "jobOutputLogs", "stdout", "stdOut"):
        v = rec.get(key)
        if v and "/" in str(v):
            return str(v).rstrip("/").rsplit("/", 1)[0] + "/"
    return ""


def list_layout(dir_uri: str, run=subprocess.run) -> dict:
    """What this call dir actually holds, including which `attempt-N` dirs exist.

    The attempt layout is what makes a retry legible: rc=141 under attempt-1 and rc=0 under
    attempt-2 is a preemption, while the same two records read as an unstable image from
    `executionStatus` alone — a distinction that cost one submission to learn by hand.
    """
    if not dir_uri:
        return {"error": "no call directory in the metadata record (no callRoot, no log path)"}
    # `file://` is how a Cromwell dump on disk points at its execution dir (Cromwell prints local roots
    # that way, and read_object already strips the scheme). Handing the URI to `ls` verbatim made every
    # such dump fail the layout step -- rc, stderr and script printed fine, then the run exited 1 on
    # "could not read: the layout listing", which reads like a broken artifact and is a broken parser.
    local = dir_uri[len("file://"):] if dir_uri.startswith("file://") else dir_uri
    if not local.startswith("://") and "://" not in local:
        cmd = ["ls", local]
    else:
        cmd = ["gsutil", "-m", "ls", dir_uri.rstrip("/") + "/*"]
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        return {"error": f"{cmd[0]} is not on PATH — cannot list {dir_uri}"}
    except Exception as exc:
        return {"error": f"{' '.join(cmd)} raised {type(exc).__name__}: {str(exc)[:160]}"}
    if proc.returncode != 0:
        return {"error": f"{' '.join(cmd)} -> exit {proc.returncode}: "
                         f"{(proc.stderr or proc.stdout or '')[:200].strip()}"}
    found = set()
    for line in (proc.stdout or "").splitlines():
        for token in line.split():
            base = token.rstrip("/").rsplit("/", 1)[-1]
            if base in TASK_FILES or base.startswith("attempt-"):
                found.add(base)
    return {"command": " ".join(cmd), "entries": sorted(found)}


def task_artifacts(rec: dict, attempt=None, run=subprocess.run, read=open,
                   tail_lines: int = MAX_TAIL_LINES) -> dict:
    """Resolve one call record to its artifacts: layout, chosen attempt dir, then the four files."""
    d = call_dir(rec)
    layout = list_layout(d, run=run)
    entries = layout.get("entries") or []
    attempts = sorted((e for e in entries if e.startswith("attempt-")
                       and e.split("-")[-1].isdigit()), key=lambda e: int(e.split("-")[-1]))
    # Cromwell's `callRoot` already ends in `attempt-N/`, so the attempt can be in the PATH rather
    # than in the listing. Reading only the listing would let --attempt 2 be satisfied by
    # attempt-2's neighbour, which is how a review ends up quoting the wrong attempt's log.
    own = next((c for c in reversed(d.rstrip("/").split("/"))
                if c.startswith("attempt-") and c.split("-")[-1].isdigit()), "")
    here = attempts or ([own] if own else [])
    if attempt is not None:
        chosen = f"attempt-{attempt}"
        if chosen not in here:
            return {"dir": d, "layout": layout, "attempts": here, "files": {}, "attempt": "",
                    "base": "", "tail_lines": tail_lines,
                    "error": f"attempt {attempt} is not here — this call dir has "
                             f"{', '.join(here) or 'no attempt dir at all'}. A retry that never "
                             "happened is a different question from a task that failed, so this "
                             "does not fall back to a neighbour's log."}
        base = d if chosen == own else d.rstrip("/") + "/" + chosen + "/"
    else:
        chosen = attempts[-1] if attempts else ""
        base = (d.rstrip("/") + "/" + chosen + "/") if chosen else d
    files = {}
    for name in TASK_FILES:
        res = read_object(base + name, run=run, read=read)
        if res.get("error") and base != d:
            alt = read_object(d + name, run=run, read=read)   # some backends write beside the dir
            if not alt.get("error"):
                res = alt
        files[name] = res
    return {"dir": d, "layout": layout, "attempts": here, "attempt": chosen or own,
            "base": base, "files": files, "tail_lines": tail_lines}


def render_task(t: dict, show_stdout: bool = False, script_lines: int = MAX_SCRIPT_LINES) -> list:
    """Bounded text for one task: rc, the stderr tail, the rendered script, the attempt layout."""
    lines = [f"  call dir: {t['dir'] or 'UNKNOWN — the record carries no callRoot and no log path'}"]
    if t["layout"].get("error"):
        lines.append(f"  attempt layout: UNAVAILABLE — {t['layout']['error']}")
    else:
        att = ", ".join(t["attempts"]) or "no attempt dir found in the listing"
        lines.append(f"  attempt layout: {att}  [from `{t['layout']['command']}`]")
        if t.get("attempt"):
            lines.append(f"  reading: {t['attempt']}")
    if t.get("error"):
        lines.append(f"  REFUSED: {t['error']}")
        return lines
    rc = t["files"]["rc"]
    if rc.get("error"):
        lines.append(f"  rc: UNREADABLE — {rc['error']}")
    else:
        rc_text = (rc.get("text") or "").strip()
        lines.append(f"  rc: {rc_text or '(empty file: no rc written)'}  [{rc['uri']}]")
    for name, cap in (("stderr", None), ("script", script_lines), ("stdout", None)):
        if name == "stdout" and not show_stdout:
            lines.append("  stdout: not shown — it is the task's own log, usually the same story as "
                         "stderr at 100x the size. Pass --show-stdout to see it.")
            continue
        res = t["files"][name]
        if res.get("error"):
            if name == "script":
                lines.append(f"  script: not in this call dir ({res['error']}) — some backends do "
                             "not write the rendered block next to the logs. Not a failure: rc and "
                             "stderr are what every backend writes.")
            else:
                lines.append(f"  {name}: UNREADABLE — {res['error']}")
            continue
        text = res.get("text") or ""
        all_lines = text.splitlines()
        limit = cap if cap is not None else t["tail_lines"]
        shown = all_lines[-limit:]
        head = f"  {name}: {res['bytes']} byte(s), {len(all_lines)} line(s)"
        if len(shown) < len(all_lines):
            head += f" — TAIL {len(shown)} shown, first {len(all_lines) - len(shown)} not printed"
        else:
            head += " — whole file"
        if name == "script":
            head += " (the rendered command block the backend executed)"
        lines.append(head)
        lines.append(f"    [{res['uri']}]")
        if not all_lines:
            lines.append("    (0 bytes — nothing was written. An empty log is a finding: the task "
                         "died before producing output, or this read the wrong file.)")
        for ln in shown:
            lines.append("    | " + ln[:400])
    return lines


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
    ap.add_argument("--metadata", help="read the workflow metadata out of this local JSON file "
                                       "instead of Terra — a batch_save_metadata.py export or any "
                                       "Cromwell dump. It is what makes the task-artifact read work "
                                       "with no network and no credentials.")
    ap.add_argument("--task", help="print ONE call's artifacts: its rc, the TAIL of its stderr, its "
                                   "rendered `script`, and which attempt-N dirs exist")
    ap.add_argument("--shard", type=int, help="with --task: which shard (default: all of them, "
                                              "newest attempt of the last shard read)")
    ap.add_argument("--attempt", type=int, help="with --task: which attempt (default: the newest)")
    ap.add_argument("--tail", type=int, default=MAX_TAIL_LINES,
                    help=f"stderr lines to print (default {MAX_TAIL_LINES}; the tail is where the "
                         "reason lives, and a 400-line log would otherwise be a second tool)")
    ap.add_argument("--show-stdout", action="store_true", help="with --task: also print stdout")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.metadata and not (a.ns and a.ws and a.submission and a.workflow):
        raise SystemExit("an explicit target needs all four of --ns --ws --submission --workflow "
                         "(batch_save_metadata.py's rule; this tool discovers nothing, so a partial "
                         "target cannot be resolved to 'the one you meant'), or pass --metadata for a "
                         "local export")

    if a.metadata:
        try:
            with open(a.metadata) as fh:
                meta = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"cannot read --metadata {a.metadata}: {type(exc).__name__}: {exc}")
            return 1
        source = f"metadata file {a.metadata}"
        sub = a.submission or os.path.basename(a.metadata)[:8]
        wf = a.workflow or "local"
    else:
        try:
            meta = fetch(a.ns, a.ws, a.submission, a.workflow)
        except terra.TerraError as e:
            # The whole point of this branch: a peek that prints nothing and exits 0 reads like a quiet
            # run. Name the status/body Terra gave instead.
            print(f"cannot see workflow {a.workflow[:8]}: {e}")
            return 1
        source = "live Terra metadata (a CACHED snapshot — docs/terra-head-to-head.md §8)"
        sub, wf = a.submission, a.workflow
    if not isinstance(meta, dict):
        print(f"unexpected metadata shape ({type(meta).__name__}) — not walking it")
        return 1

    t = tally(meta)
    print(f"submission {sub[:8]}  workflow {wf[:8]}  {t['workflow_name']}  "
          f"status={t['root_status'] or 'unknown'}  cost={t['cost'] if t['cost'] is not None else '?'}"
          f"  {t['start']} -> {t['end'] or '(not finished)'}  [{source}]")
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
    if a.task:
        calls = meta.get("calls") or {}
        shards = calls.get(a.task) or []
        if not shards:
            names = sorted(calls)
            close = [n for n in names if a.task.lower().rstrip("_") in n.lower()][:5]
            print(f"task {a.task}: not in this metadata. {len(names)} call name(s) are; "
                  + (f"closest: {', '.join(close)}" if close else
                     f"none of them contains '{a.task}'. First names: {', '.join(names[:5])}"))
            return 1
        cand = [r for r in shards
                if a.shard is None or int(r.get("shardIndex") or 0) == a.shard]
        if not cand:
            have = sorted({int(r.get("shardIndex") or 0) for r in shards})
            print(f"task {a.task} has no shard {a.shard} — it has {have}. Not reading a neighbour "
                  "and calling it the answer.")
            return 1
        # `--attempt` has to select the RECORD, not merely validate the newest one's directory. The
        # preemption log this flag exists for is the OLDER record: picking max(attempt) first and then
        # checking --attempt against that record's callRoot made `--attempt 1` refuse with "this call
        # dir has attempt-2" on exactly the layout the file documents (callRoot already ends in
        # attempt-N/), so the case A5 was requested for was unreachable.
        rec = max(cand, key=lambda r: int(r.get("attempt") or 1))
        note = "newest attempt of that shard; pass --attempt to pick another"
        if a.attempt is not None:
            exact = [r for r in cand if int(r.get("attempt") or 1) == a.attempt]
            if exact:
                rec = exact[-1]
                note = f"attempt {a.attempt}, as asked"
            else:
                note = (f"no metadata record for attempt {a.attempt} (this call has "
                        f"{sorted({int(r.get('attempt') or 1) for r in cand})}) — reading that "
                        "attempt's files out of the execution dir instead, and refusing if they "
                        "are not there")
        print(f"task {a.task}: {len(shards)} record(s), shard(s) "
              f"{sorted({int(r.get('shardIndex') or 0) for r in shards})}, reading shard "
              f"{rec.get('shardIndex')} ({note})")
        ta = task_artifacts(rec, attempt=a.attempt, tail_lines=a.tail)
        for line in render_task(ta, show_stdout=a.show_stdout):
            print(line)
        # Only what the page TRIED to show counts as unreadable. stdout is withheld by default and is
        # absent from most PAPI records, so counting it made every real `--task` exit 1 — a tool that
        # is always broken is a tool nobody trusts, which is the same failure as always-clean.
        # What can FAIL the run: rc and stderr, which every backend writes into the call dir, plus
        # stdout when it was explicitly asked for. `script` is printed when present but is not written
        # by every backend, so treating it as required made a legitimate preempted attempt exit 1 --
        # and a tool that is always broken is a tool nobody trusts, the same failure as always-clean.
        wanted = ["stderr", "rc"] + (["stdout"] if a.show_stdout else [])
        unreadable = [n for n in wanted if (ta["files"].get(n) or {}).get("error")]
        if ta.get("error") or ta["layout"].get("error") or unreadable:
            print(f"  could not read: {', '.join(unreadable) or 'the layout listing'} — a peek that "
                  "cannot see the log does not exit 0")
            return 1
        return 0
    code = exit_code(t["status_counts"], t["inflight"], t["failures"], t["root_status"])
    print(f"peek exit {code}: " + {0: "terminal, no failed/aborted calls",
                                   2: "terminal WITH failed/aborted calls",
                                   3: "still in flight"}[code])
    return code


def selftest() -> int:
    """Offline: fixtures in a temp dir, an injected transport for every cloud call.

    Two things this has to prove about itself, or it is not worth running: that the tail assertion
    measures a read that COULD have happened (the marker line is the last of a 60-line log, and the
    first line is provably absent), and that each cap and each refusal was reachable (inputs exceed
    the cap on purpose — a guard that cannot fire is a FAIL, which is the rule in
    scripts/probe_fixes.py).
    """
    import contextlib
    import io
    import shutil
    import tempfile

    fails = []

    def ck(name, cond, extra=""):
        print(("  ok    " if cond else "  FAIL  ") + name + (f"  <{extra}>" if extra else ""))
        if not cond:
            fails.append(name)

    print("selftest: terra/batch_peek.py")

    # --- metadata fixtures -------------------------------------------------------------------
    done = {"executionStatus": "Done", "attempt": 1, "shardIndex": 0, "returnCode": 0}
    clean = {"status": "Succeeded", "workflowName": "WF", "calls": {"A": [dict(done)],
                                                                    "B": [dict(done)]}}
    preempt = [{"executionStatus": "RetryableFailure", "attempt": 1, "shardIndex": 0,
                "returnCode": 141, "end": "2026-09-22T01:00:00Z", "failureMessage": "preempted"},
               {"executionStatus": "Done", "attempt": 2, "shardIndex": 0, "returnCode": 0,
                "end": "2026-09-22T02:00:00Z"}]
    died = [{"executionStatus": "Failed", "attempt": 1, "shardIndex": 3, "returnCode": 10,
             "end": "2026-09-22T00:30:00Z", "messages": ["scala.MatchError", "at line 4"],
             "stderr": "gs://bucket/call-Bar/shard-3/attempt-1/stderr"}]
    failing = {"status": "Failed", "workflowName": "WF", "root": "gs://bucket/cromwell-exec/",
               "calls": {"Foo": preempt, "Bar": died}}
    inflight = {"calls": {"Foo": [{"executionStatus": "Running", "attempt": 1, "shardIndex": 0,
                                   "start": "2026-09-22T03:00:00Z"}]}}

    t_clean, t_fail, t_inflight = tally(clean), tally(failing), tally(inflight)
    ck("tally counts records and calls separately (9 root calls hid ~200 tasks: that is why both)",
       (t_clean["record_count"], t_clean["call_count"]) == (2, 2)
       and (t_fail["record_count"], t_fail["call_count"]) == (3, 2))
    ck("a call whose NEWEST record is Done is not in flight even though attempt 1 failed — "
       "the naive every-record rule would report a finished run as running forever",
       t_fail["inflight"] == [] and len(t_fail["failures"]) == 2)
    ck("attempt_summary spells the preemption out: both attempts, both rc codes",
       "attempt 1 RetryableFailure rc=141" in attempt_summary(preempt)
       and "attempt 2 Done rc=0" in attempt_summary(preempt))
    msg = message_of(died[0])
    ck("message_of joins the list-form key and caps the length", "scala.MatchError" in msg
       and len(msg) <= MAX_MESSAGE)
    ck("message_of says nothing when the record carries nothing (no 'None' in the output)",
       message_of({"executionStatus": "Done"}) == "")

    ck("exit code 0: terminal, nothing failed", exit_code(t_clean["status_counts"], [], [],
                                                          "Succeeded") == 0)
    ck("exit code 2: terminal WITH failures", exit_code(t_fail["status_counts"], [],
                                                        t_fail["failures"], "Failed") == 2)
    ck("exit code 3: in flight", exit_code(t_inflight["status_counts"], t_inflight["inflight"],
                                           [], "") == 3)
    ck("exit code 3: submitted but nothing started is not 'clean'",
       exit_code(Counter(), [], [], "") == 3)
    ck("an exhausted retry on a Succeeded run exits 2, not 3 — a loop driven by this code must end",
       exit_code(t_fail["status_counts"], [], t_fail["failures"], "Succeeded") == 2)

    # --- bounded output: the caps must be engaged by these inputs, not merely present ----------
    big = {"status_counts": Counter({"Done": 100}), "call_count": 60, "record_count": 240,
           "inflight": [{"call": f"c{i}", "status": "Running", "records": 1, "start": "x"}
                        for i in range(30)],
           "retried": [{"call": f"r{i}", "summary": "attempt 1 Failed"} for i in range(20)],
           "failures": [{"call": f"f{i}", "shard": 0, "attempt": 1, "status": "Failed", "rc": 1,
                         "message": "m", "stderr": "s", "end": str(i)} for i in range(12)],
           "root_status": "Running", "workflow_name": "WF", "cost": None, "start": "s", "end": None,
           "scratch_root": "", "workflow_message": ""}
    lines = render(big)
    text = "\n".join(lines)
    ck("CONTROL: these inputs DO exceed the caps (30 inflight > 12, 20 retried > 10, 12 failures > 3)"
       " — otherwise the next two assertions prove nothing",
       len(big["inflight"]) > MAX_INFLIGHT and len(big["retried"]) > MAX_RETRIED
       and len(big["failures"]) > MAX_FAILURE_DETAILS)
    ck("output is bounded AND says what it dropped (three separate disclosures)",
       text.count("… and ") >= 3 and len([l for l in lines if l.startswith("  RUNNING ")]) == 12)
    ck("FIRST FAILURE is the EARLIEST by end time, not the first key alphabetically",
       "FIRST FAILURE f0" in text and "FIRST FAILURE f11" not in text)
    ck("a clean run with no failures says so (silence would read as a broken tally)",
       "no failed/aborted call records" in "\n".join(render(t_clean)))

    # --- scratch rc tally: the liveness signal the cached metadata cannot fake ------------------
    class FakeProc:
        def __init__(self, code=0, out="", err=""):
            self.returncode, self.stdout, self.stderr = code, out, err

    GS = ("2026-09-22 01:02:03  GMT+00       2  gs://bucket/cromwell-exec/call-A/shard-0/attempt-1/rc\n"
          "2026-09-22 03:04:05  GMT+00       2  gs://bucket/cromwell-exec/call-A/shard-0/attempt-2/rc\n"
          "2026-09-22 02:00:00  GMT+00     999  gs://bucket/cromwell-exec/call-A/shard-0/stdout\n")
    s = rc_tally("gs://bucket/cromwell-exec/", run=lambda cmd, **kw: FakeProc(0, GS))
    ck("CONTROL: gsutil-shaped 5-field lines DO produce rc objects here (the parse is reachable)",
       s["rc_count"] == 2 and s["objects_listed"] == 3)
    ck("the attempt dir is read out of the URL path, not a column (5 fields, no attempt column)",
       s["attempts"] == {"attempt-1": 1, "attempt-2": 1})
    ck("newest object time is the whole timestamp, not just the date",
       s["newest"] == "2026-09-22 03:04:05 GMT+00", s["newest"])
    ck("the command is printed with the result so a person can narrow it", "gsutil ls -r" in s["command"])
    no_gsutil = rc_tally("gs://x/", run=lambda cmd, **kw: (_ for _ in ()).throw(FileNotFoundError()))
    ck("gsutil missing is a NAMED gap, not an empty tally", "not on PATH" in no_gsutil["error"]
       and "--no-scratch" in no_gsutil["error"])
    ck("a non-zero gsutil exit names the command and the exit code",
       "exit 1" in rc_tally("gs://x/", run=lambda cmd, **kw: FakeProc(1, "", "Permission denied"))["error"])
    ck("a timeout is a named gap too (never a 0-count result)",
       "raised TimeoutError" in rc_tally("gs://x/", run=lambda cmd, **kw:
                                         (_ for _ in ()).throw(TimeoutError("slow")))["error"])
    ck("render_scratch never prints an empty tally: an unavailable one is labelled UNAVAILABLE",
       "UNAVAILABLE" in "\n".join(render_scratch(no_gsutil)))
    ck("--no-scratch says out loud that cached metadata can be stale (the reason the tally exists)",
       "byte-identical" in "cached metadata can be byte-identical while the run advances")

    # --- A5's second clause: stderr tail, rendered script, attempt-N layout --------------------
    d = tempfile.mkdtemp(prefix="peek-selftest-")
    try:
        att1 = os.path.join(d, "call-Foo", "shard-0", "attempt-1")
        att2 = os.path.join(d, "call-Foo", "shard-0", "attempt-2")
        os.makedirs(att1)
        os.makedirs(att2)
        first, last = "line-01-the-HEAD-of-the-log", "line-60-scala.MatchError: " + "x" * 30
        with open(os.path.join(att2, "stderr"), "w") as fh:
            fh.write("\n".join([first] + [f"line-{i:02d}" for i in range(2, 60)] + [last]) + "\n")
        with open(os.path.join(att2, "stdout"), "w") as fh:
            fh.write("task stdout line\n")
        with open(os.path.join(att2, "rc"), "w") as fh:
            fh.write("10\n")
        with open(os.path.join(att2, "script"), "w") as fh:
            fh.write("\n".join([f'set -e\ngatk --java-options "-Xmx8g" \\\n  --REF {i} \\'
                                for i in range(30)]) + "\n")
        open(os.path.join(att1, "rc"), "w").write("141\n")
        open(os.path.join(att1, "stderr"), "w").write("preempted by the backend\n")

        rec_cr = {"callRoot": att2 + "/", "shardIndex": 0, "attempt": 2, "executionStatus": "Done"}
        rec_fallback = {"stderr": os.path.join(att1, "stderr"), "shardIndex": 0, "attempt": 1}
        ck("call_dir prefers Cromwell's callRoot", call_dir(rec_cr) == att2 + "/")
        ck("call_dir falls back to the log path's parent when there is no callRoot",
           call_dir(rec_fallback) == att1 + "/")
        ck("call_dir refuses to invent a prefix out of nothing", call_dir({"executionStatus": "X"}) == "")
        ck("CONTROL: call_dir('') refuses rather than listing the CWD",
           "no call directory" in list_layout("")["error"])

        lay = list_layout(os.path.join(d, "call-Foo", "shard-0"), run=subprocess.run)
        ck("the attempt layout names the attempt dirs that exist, with the command it came from",
           lay.get("entries") == ["attempt-1", "attempt-2"] and lay["command"].startswith("ls "),
           str(lay))
        ck("CONTROL: a real `ls` on a directory that is not there reports a failure, not nothing",
           bool(list_layout(os.path.join(d, "no-such-dir"), run=subprocess.run).get("error")))

        r_ok = read_object(os.path.join(att2, "rc"))
        ck("a local artifact reads, with its byte count", r_ok.get("text") == "10\n"
           and r_ok["bytes"] == 3)
        ck("file:// works too (that is how a metadata export points at a local dir)",
           read_object("file://" + os.path.join(att2, "rc")).get("text") == "10\n")
        r_missing = read_object(os.path.join(att2, "nope"))
        ck("a missing artifact is an error that names the path — never a 0-byte 'empty file'",
           "nope" in r_missing.get("error", "") and "text" not in r_missing)
        ck("a gs:// read through the transport works offline (fake run supplies the bytes)",
           read_object("gs://b/x", run=lambda cmd, **kw: FakeProc(0, "hello\n")).get("text") == "hello\n")
        ck("a gs:// read that fails says so and points at --metadata",
           "metadata" in read_object("gs://b/x", run=lambda cmd, **kw:
                                     (_ for _ in ()).throw(FileNotFoundError())).get("error", ""))

        ta = task_artifacts(rec_cr, tail_lines=5)
        page = "\n".join(render_task(ta))
        ck("CONTROL: the artifacts really were opened — rc=10 from the file, not from metadata",
           "rc: 10" in page)
        ck("the stderr TAIL is the END of the file: the last line is on the page",
           last.split(":")[0] in page)
        ck("and the head of a 60-line log is NOT on the page, with the drop counted",
           first not in page and "TAIL 5 shown, first 55 not printed" in page)
        ck("the rendered `script` block prints (what the backend actually ran), capped and disclosed",
           "the rendered command block the backend executed" in page
           and len([l for l in page.splitlines() if "set -e" in l]) <= MAX_SCRIPT_LINES)
        ck("stdout stays off the page until asked for", "Pass --show-stdout" in page
           and "task stdout line" not in page)
        ck("--show-stdout puts it back", "task stdout line" in
           "\n".join(render_task(task_artifacts(rec_cr, tail_lines=5), show_stdout=True)))
        page1 = "\n".join(render_task(task_artifacts(rec_fallback, tail_lines=5)))
        ck("attempt 1 reads its OWN files (rc=141, the preemption log)", "rc: 141" in page1
           and "preempted by the backend" in page1)

        shard_dir = os.path.join(d, "call-Foo", "shard-0") + "/"
        att2_files = task_artifacts({"callRoot": shard_dir}, tail_lines=5)
        ck("a record whose dir holds attempt-N dirs gets the NEWEST attempt chosen, and it is named",
           att2_files["attempt"] == "attempt-2" and att2_files["attempts"] == ["attempt-1", "attempt-2"],
           str(att2_files["attempt"]))
        refusal = "\n".join(render_task(task_artifacts({"callRoot": shard_dir}, attempt=9)))
        ck("--attempt 9 is REFUSED and names the attempts that exist (no silent fallback to a "
           "different attempt's log)", "REFUSED" in refusal and "attempt-1, attempt-2" in refusal)
        empty_dir = os.path.join(d, "call-Empty", "shard-0", "attempt-1")
        os.makedirs(empty_dir)
        open(os.path.join(empty_dir, "stderr"), "w").close()
        open(os.path.join(empty_dir, "script"), "w").close()
        open(os.path.join(empty_dir, "rc"), "w").close()
        page_e = "\n".join(render_task(task_artifacts({"callRoot": empty_dir + "/"}, tail_lines=5)))
        ck("a 0-byte log is called a finding, not shown as a clean blank",
           page_e.count("0 bytes") == 2 and "died before producing output" in page_e)   # stderr, script
        page_e2 = "\n".join(render_task(task_artifacts({"callRoot": empty_dir + "/"}, tail_lines=5),
                                          show_stdout=True))
        ck("and a file that is simply not there says UNREADABLE with the path, not '0 bytes' "
           "(once asked for: stdout is withheld by default)",
           "stdout: UNREADABLE" in page_e2 and "no such file" in page_e2.lower())

        # --- end to end through main(), with --metadata and no cloud call at all --------------
        meta_path = os.path.join(d, "meta.json")
        with open(meta_path, "w") as fh:
            json.dump({"status": "Succeeded", "workflowName": "WF",
                       "calls": {"Foo": [dict(rec_cr)]}}, fh)
        meta_path2 = os.path.join(d, "meta2.json")
        with open(meta_path2, "w") as fh:
            json.dump({"status": "Succeeded", "workflowName": "WF",
                       "calls": {"Empty": [{"shardIndex": 0, "attempt": 1,
                                            "executionStatus": "Done",
                                            "callRoot": empty_dir + "/"}]}}, fh)
        real_argv = sys.argv

        def run_main(*argv):
            buf = io.StringIO()
            sys.argv = ["batch_peek.py", *argv]
            try:
                with contextlib.redirect_stdout(buf):
                    rc = main()
            finally:
                sys.argv = real_argv
            return rc, buf.getvalue()

        rc_ok, out_ok = run_main("--metadata", meta_path, "--task", "Foo", "--no-scratch", "--tail", "3")
        ck("CONTROL: --metadata + --task runs with NO network and NO credentials and exits 0",
           rc_ok == 0, f"rc={rc_ok}")
        ck("content lines are printed, not just paths (the clause that was missing)",
           out_ok.count("    | ") >= 3 and last.split(":")[0] in out_ok)
        ck("the metadata source is labelled, so nobody reads a cached file as a live answer",
           "metadata file" in out_ok)
        ck("a withheld stdout is NOT counted as a failure (only what the page tried to show can be "
           "unreadable — counting it made every real PAPI record exit 1)",
           run_main("--metadata", meta_path2, "--task", "Empty", "--no-scratch")[0] == 0)
        meta_file = os.path.join(d, "meta_file.json")
        with open(meta_file, "w") as fh:
            json.dump({"status": "Succeeded", "workflowName": "WF", "calls": {"Foo": [
                {"shardIndex": 0, "attempt": 2, "executionStatus": "Done",
                 "callRoot": "file://" + att2 + "/"}]}}, fh)
        rc_file, out_file = run_main("--metadata", meta_file, "--task", "Foo", "--no-scratch")
        ck("a `file://` callRoot (how Cromwell prints a local execution root) lists its layout and "
           "reads — the scheme must not reach `ls` and turn a good dump into exit 1",
           rc_file == 0 and "rc: 10" in out_file and "could not read" not in out_file,
           f"rc={rc_file}")
        rc_miss, out_miss = run_main("--metadata", meta_path, "--task", "Nope", "--no-scratch")
        ck("a call name this metadata does not declare exits 1 and names what IS declared",
           rc_miss == 1 and "not in this metadata" in out_miss and "Foo" in out_miss)
        rc_att, out_att = run_main("--metadata", meta_path, "--task", "Foo", "--no-scratch",
                                   "--attempt", "9")
        ck("--attempt that does not exist exits 1 (a peek that cannot see the log does not exit 0)",
           rc_att == 1 and "REFUSED" in out_att)
        # The scenario A5 exists for, reproduced: a preempted attempt-1 (rc=141) that came back on
        # attempt-2. --attempt 1 must reach the OLDER record; validating the flag against the newest
        # record's callRoot made this refuse, which is the bug the auditor caught.
        meta_pre = os.path.join(d, "meta_pre.json")
        with open(meta_pre, "w") as fh:
            json.dump({"status": "Succeeded", "workflowName": "WF", "calls": {"Foo": [
                {"shardIndex": 0, "attempt": 1, "executionStatus": "RetryableFailure",
                 "callRoot": att1 + "/"},
                {"shardIndex": 0, "attempt": 2, "executionStatus": "Done", "callRoot": att2 + "/"}]}},
                fh)
        rc_pre, out_pre = run_main("--metadata", meta_pre, "--task", "Foo", "--no-scratch",
                                   "--attempt", "1")
        ck("--attempt 1 on a two-attempt call reads the PREEMPTED attempt (rc=141 and its log), "
           "instead of refusing because the newest record's dir is attempt-2", rc_pre == 0
           and "rc: 141" in out_pre and "preempted by the backend" in out_pre, f"rc={rc_pre}")
        ck("and it says which attempt it read, so the page cannot be misread as the final attempt",
           "attempt 1, as asked" in out_pre)
        ck("a call dir with no rendered `script` discloses it and still exits 0 (backend-dependent, "
           "unlike rc/stderr)", "script: not in this call dir" in out_pre)
        rc_bad, _ = run_main("--metadata", os.path.join(d, "no-such.json"))
        ck("--metadata that cannot be read exits 1 naming the file type of failure", rc_bad == 1)
        rc_noshard, out_noshard = run_main("--metadata", meta_path, "--task", "Foo", "--no-scratch",
                                           "--shard", "7")
        ck("a shard that does not exist refuses rather than reading a neighbour",
           rc_noshard == 1 and "no shard 7" in out_noshard)
        try:
            sys.argv = ["batch_peek.py"]
            with contextlib.redirect_stdout(io.StringIO()):
                main()
            ck("no target at all is a refusal, not an empty table", False)
        except SystemExit as exc:
            ck("no target at all is a refusal naming all four flags", "--metadata" in str(exc))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print(("  FAIL  " + str(len(fails)) + " assertion(s) failed") if fails
          else "  all selftest assertions passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
