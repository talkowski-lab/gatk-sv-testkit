#!/usr/bin/env python3
"""One artifact shape for every comparator in this directory.

Every comparator takes `--json PATH` and writes the same envelope:

    { "tool": <name>, "argv": [...], "inputs": {label: {path, bytes?, sha256_16?, ...}},
      "rule": { the aggregation/tolerance/join decisions that make the number mean something },
      ... metric keys ...,
      "verdict" / "verdicts": ...,
      "compared_something": true|false }

`inputs` carries a content hash for each input file so the artifact can be re-checked later. `rule`
records the comparison rule (see docs/comparators.md) next to the number it produced.
`compared_something: false` means exit 2. Also holds the small I/O helpers every comparator shares. Stdlib only.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import sys

GZIP_MAGIC = b"\x1f\x8b"


def die(msg: str, code: int = 2) -> None:
    """Print msg to stderr and exit with code (2 = usage / nothing compared)."""
    sys.stdout.flush()          # so a piped run shows its output before the error
    sys.stderr.write(msg.rstrip("\n") + "\n")
    sys.exit(code)


def open_text(path: str, dir_hint: str = ""):
    """Open plain or gzip/BGZF text, detected from the magic bytes; exit 2 on a missing file.

    `dir_hint` is appended to the refusal when `path` is a directory.
    """
    if os.path.isdir(path):
        die(f"{path} is a directory" + (f"; {dir_hint}" if dir_hint else ""))
    if not os.path.isfile(path):
        die(f"no such file: {path}")
    with open(path, "rb") as raw:
        magic = raw.read(2)
    if magic == GZIP_MAGIC:
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace", newline="")
    return open(path, "r", encoding="utf-8", errors="replace", newline="")


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
