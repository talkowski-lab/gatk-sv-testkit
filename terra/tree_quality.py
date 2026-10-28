#!/usr/bin/env python3
"""One reader for WHAT IS MISSING from a saved Cromwell metadata tree.

Shared by `terra/batch_cost.py` and `checks/cost_estimate.py` so both count truncation the
same way. Stdlib-only, so the stdlib-only `checks/` selftests can import it.

The marker keys are written by `batch_save_metadata.fetch_tree`; `scripts/probe_fixes.py`
asserts the two stay in sync.
"""
from __future__ import annotations

MARKER_CHILDREN = "_children"                # set on every fetched node
MARKER_MISSING = "_missingSubWorkflows"      # sub-workflows whose fetch failed twice
MARKER_DEPTH = "_unexpandedSubWorkflows"     # calls the depth cap left unexpanded
MAX_DEPTH = 6                                # fetch_tree's max_depth


def quality(meta) -> dict:
    """Count what is missing from a metadata tree.

      fetch_failed     sub-workflows whose fetch failed (_missingSubWorkflows)
      unexpanded       sub-workflow calls with no child tree (depth cap, old or trimmed file)
      half_timestamped vmStartTime without vmEndTime (running or aborted); dropped from totals

    A child tree under Cromwell's own `subWorkflowMetadata` (not `_children`) counts as
    unexpanded, which errs toward reporting a floor.
    """
    q = {"fetch_failed": 0, "unexpanded": 0, "half_timestamped": 0, "calls_seen": 0}

    def walk(m):
        q["fetch_failed"] += len(m.get(MARKER_MISSING) or [])
        sids = [c.get("subWorkflowId") for calls in (m.get("calls") or {}).values()
                for c in calls if c.get("subWorkflowId")]
        q["unexpanded"] += len(m.get(MARKER_DEPTH) or [])
        q["unexpanded"] += max(0, len(sids) - len(m.get(MARKER_CHILDREN) or []))
        for calls in (m.get("calls") or {}).values():
            for c in calls:
                q["calls_seen"] += 1
                if bool(c.get("vmStartTime")) != bool(c.get("vmEndTime")):
                    q["half_timestamped"] += 1
        for kid in (m.get(MARKER_CHILDREN) or []):
            walk(kid)

    walk(meta)
    return q


def is_marked(meta) -> bool:
    """True if the tree carries batch_save_metadata's `_children` marker anywhere.

    `quality()` returns all zeros for an unmarked dump too, so zeros alone do not mean complete.
    """
    found = [False]

    def walk(m):
        if MARKER_CHILDREN in m:
            found[0] = True
        for kid in (m.get(MARKER_CHILDREN) or []):
            if isinstance(kid, dict):
                walk(kid)

    walk(meta)
    return found[0]
