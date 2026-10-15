#!/usr/bin/env python3
"""Why did this workflow fail: the task, or the cloud that ran it?

Measured case (2026-10-09, single-sample step 01 rerun, submission `a3ced18f`, workflow
`f9df3129-385a-4efe-b4ac-798afb714360`, 1589 VM-minutes): the submission reported `Failed` and
`batch_status` named one failed scope, `MakeCohortVcf`. Reading the expanded Cromwell metadata by hand found 8 failing
entries and none of them was the pipeline's fault:

    5  Task ... failed. Job exited without an error, exit code 0. GCP Batch task exited with VMPreemption(50001)
    3  Unable to complete Batch request ... NOT_FOUND: Resource 'projects/terra-.../jobs/job-...'
    5  Workflow failed                       <- the root scope, an echo of the above
    0  tasks that exited with a nonzero return code

The distinction decides what to do next. A nonzero return code means the body (an input, an image, a path) is wrong and
must be fixed before spending again. A preemption or a vanished Batch job means the *cloud* blinked, the body is
unimplicated, and the correct next action is a different failure policy or simply another attempt -- not a code change.
Getting that backwards costs a debugging session that finds nothing, or a resubmission that fails the same way.

Read-only, offline, no Terra call: point it at a metadata file. `terra/batch_save_metadata.py` writes one, or ask
`firecloud.api.get_workflow_metadata(ns, ws, submission, workflow, expand_sub_workflows=True)` -- without that flag the
sub-workflow scopes arrive with no tasks under them and every real failure is invisible, which is how this run's first
read produced exactly one useless line (`Workflow failed`).

    checks/failure_cause.py --metadata /tmp/md2.json
    checks/failure_cause.py --selftest
"""

import argparse
import json
import sys

INFRA_MARKS = (
    ("VMPreemption", "backend lost the VM (GCP Batch VMPreemption)"),
    ("NOT_FOUND: Resource 'projects/", "Batch job resource vanished mid-run (service-side NOT_FOUND)"),
    ("Task failed immediately due to a transient", "transient GCP Batch failure"),
)
ROOT_ECHO = "Workflow failed"


def classify(meta: dict) -> dict:
    """Walk a Cromwell metadata document (sub-workflows included) and count failure causes.

    Returns `{"counts": {...}, "nonzero": [...], "scopes": N, "status": ...}`. `nonzero` is the list of tasks that
    actually exited nonzero -- the ones that indict the launch. It stays empty for a pure-infrastructure failure, and a
    caller that treats "Failed" as "my inputs are wrong" has no way to notice the difference without it.
    """
    counts = {}
    nonzero = []
    scopes = 0

    def bump(kind):
        counts[kind] = counts.get(kind, 0) + 1

    def walk(node, depth):
        nonlocal scopes
        for cr, calls in (node.get("calls") or {}).items():
            scopes += 1
            leaf = cr.rsplit(".", 1)[-1]
            for c in calls:
                rc = c.get("returnCode")
                if rc not in (0, None):
                    nonzero.append(f"{leaf} shard={c.get('shardIndex')} rc={rc}")
                    bump("task exited nonzero")
                for f in (c.get("failures") or []):
                    msg = str(f.get("message") or "")
                    for mark, label in INFRA_MARKS:
                        if mark in msg:
                            bump(label)
                            break
                    else:
                        bump("root echo: 'Workflow failed'" if ROOT_ECHO in msg else "unclassified failure message")
                sub = c.get("subWorkflowMetadata")
                if isinstance(sub, dict) and depth < 6:
                    walk(sub, depth + 1)
        sub = node.get("subWorkflowMetadata")
        if isinstance(sub, dict) and depth < 6:
            walk(sub, depth + 1)

    walk(meta, 0)
    return {"counts": counts, "nonzero": nonzero, "scopes": scopes, "status": meta.get("status")}


def verdict(r: dict) -> tuple:
    """`(rc, lines)`: 0 infrastructure-only, 3 body implicated, 4 nothing to grade."""
    counts, nonzero = r["counts"], r["nonzero"]
    if not counts and r["status"] in ("Failed", "Aborted"):
        return 4, [f"NOTHING GRADED: status {r['status']} with {r['scopes']} scope(s) and 0 failure messages. "
                   "This metadata is not expanded -- fetch it with expand_sub_workflows=True, because without it "
                   "sub-workflow tasks are absent and every real cause is invisible."]
    if not counts and not nonzero:
        return 4, ["NOTHING GRADED: no failures found at all. A success document is not a diagnosis."]
    lines = []
    for kind, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {n:3d}  {kind}")
    if nonzero:
        lines.append(f"BODY IMPLICATED: {len(nonzero)} task(s) exited nonzero -- fix the launch before spending again:")
        lines += [f"  {x}" for x in nonzero[:8]]
        return 3, lines
    lines.append("INFRASTRUCTURE: no task exited nonzero. The body ran and the backend lost it; a code change here "
                 "would find nothing. Options are a retry, a different failure policy, or a different pool -- "
                 "not a different input.")
    return 0, lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Classify a failed Cromwell workflow's causes: task vs cloud.")
    ap.add_argument("--metadata", help="a Cromwell metadata JSON (expanded; see the module docstring)")
    ap.add_argument("--selftest", action="store_true", help="grade synthetic fixtures")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.metadata:
        print("  pass --metadata FILE (see CAPTURE in the module docstring) or --selftest")
        return 2
    with open(a.metadata, encoding="utf-8") as fh:
        r = classify(json.load(fh))
    rc, lines = verdict(r)
    print(f"  {len(r['counts'])} cause class(es) over {r['scopes']} scope(s), status {r['status']}")
    for line in lines:
        print(line)
    return rc


def _selftest() -> int:
    ok = bad = 0

    def check(label, want_rc, needle, meta):
        nonlocal ok, bad
        rc, lines = verdict(classify(meta))
        hit = rc == want_rc and any(needle in ln for ln in lines)
        print(f"  {'ok  ' if hit else 'FAIL'} {label} (exit {rc}, want {want_rc})")
        if not hit:
            print(f"        lines: {' | '.join(lines)[:120]}")
            bad += 1
        else:
            ok += 1

    preempted = {"shardIndex": 18, "returnCode": 0, "failures": [{"message":
        "Task X.ExtractSRVariantLists:18:1 failed. Job exited without an error, exit code 0. "
        "GCP Batch task exited with VMPreemption(50001)."}]}
    inner = {"calls": {"W.MakeCohortVcf.CombineBatches.ExtractSRVariantLists": [preempted]}}
    scope = {"shardIndex": 18, "attempt": 1, "returnCode": None, "subWorkflowMetadata": inner}
    echo = {"shardIndex": -1, "attempt": 1, "failures": [{"message": "Workflow failed"}]}
    pre = {"status": "Failed", "calls": {"W.MakeCohortVcf": [scope, echo]}}
    check("a preempted task with exit code 0 is infrastructure, not a body failure", 0, "INFRASTRUCTURE", pre)
    check("and the root's 'Workflow failed' echo does not count as a cause of its own", 0, "root echo", pre)
    check("expanded metadata is required: an unexpanded doc refuses instead of passing", 4, "NOTHING GRADED",
          {"status": "Failed", "calls": {"W.MakeCohortVcf": [{"shardIndex": -1, "failures": []}]}})
    check("a task that really exits nonzero indicts the launch", 3, "BODY IMPLICATED",
          {"status": "Failed", "calls": {"W.CleanVcf": [{"shardIndex": 3, "returnCode": 1,
                                                         "failures": [{"message": "return code 1"}]}]}})
    check("a vanished Batch job is infrastructure even when it aborts a scope", 0, "INFRASTRUCTURE",
          {"status": "Failed", "calls": {"W.CleanVcf": [{"shardIndex": -1, "failures": [
              {"message": "Unable to complete Batch request due to a problem with the request "
                          "(io.grpc.StatusRuntimeException: NOT_FOUND: Resource "
                          "'projects/terra-110fd9b7/locations/us-central1/jobs/job-d869191c-x')"}]}]}})
    print(f"failure_cause: {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
