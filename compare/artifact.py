#!/usr/bin/env python3
"""One artifact shape for every comparator in this directory.

Why this exists
---------------
Five of the six comparators that shipped here printed their result and threw it away. `compare/`
could tell you what two callsets did on the afternoon you ran it, and could not tell you whether
the same two callsets still do that next week — which is the only way a measurement survives being
quoted. Worse, some of the expected values lived *inside docstrings* ("Expected for the 2026-09-15
head-to-head PESR pair: 74,241 shared VIDs, exact 0.990512"), so the thing to compare against was
prose, and prose has no schema.

So every comparator takes `--json PATH` and writes the same envelope:

    { "tool": <name>, "argv": [...], "inputs": {label: {path, bytes?, sha256_16?, ...}},
      "rule": { the aggregation/tolerance/join decisions that make the number mean something },
      ... metric keys ...,
      "verdict" / "verdicts": ...,
      "compared_something": true|false }

`inputs` carries a content hash wherever the input is a file the caller owns, because an artifact
that does not name the bytes it measured cannot be re-checked after a re-fetch. `rule` is not
metadata: it is the written-down rule docs/comparators.md exists to demand, recorded next to the
number it produced. `compared_something: false` means exit 2 — an empty result is never silent.

Not a library on purpose: no dependency between the tools' *logic*, only the shape of what they emit
is shared. Nothing here reads or writes anything else, and the whole module is stdlib.
"""
from __future__ import annotations

import json
import os
import sys


def digest(path: str, chars: int = 16) -> str:
    import hashlib
    h = hashlib.sha256()
    try:
        with open(path, "rb") as raw:
            for chunk in iter(lambda: raw.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return "unreadable"
    return h.hexdigest()[:chars]


def input_file(label: str, path: str, **extra) -> dict:
    """One side of the comparison, described by the bytes it was actually made of."""
    entry = {"path": str(path)}
    try:
        entry["bytes"] = os.path.getsize(path)
    except OSError:
        entry["bytes"] = None
    entry["sha256_16"] = digest(path)
    entry.update(extra)
    return {label: entry}


def envelope(tool: str, inputs: dict, rule: dict, metrics: dict, verdict=None,
             compared_something: bool = True) -> dict:
    doc = {"tool": tool, "argv": sys.argv[1:], "inputs": inputs, "rule": rule}
    doc.update(metrics)
    if verdict is not None:
        doc["verdict"] = verdict
    doc["compared_something"] = bool(compared_something)
    return doc


def write(path, doc: dict) -> None:
    """Write the artifact, or do nothing when no --json path was given."""
    if not path:
        return
    parent = os.path.dirname(os.path.abspath(str(path)))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True, default=str)
        fh.write("\n")
    print(f"wrote artifact -> {path}")
