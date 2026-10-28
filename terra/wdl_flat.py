#!/usr/bin/env python3
"""Flatten a WDL + its imports into ONE document, so Terra can hold it as a workspace method.

Terra method configs point at Dockstore or at a single-file workspace method, so a workflow that
gatk-sv does not publish to Dockstore can only be submitted on its own once it is bundled. This is a
dependency-free equivalent of `womtool bundle` for the import style gatk-sv uses. Anything it does
not understand is an error rather than a pass-through, so the flattened document compiles the same
as the modular one; `--check` runs `miniwdl check` on the output.

    python terra/wdl_flat.py ResolveCpxSv                      # print the flattened doc
    python terra/wdl_flat.py ResolveCpxSv --out X.wdl --check  # write it + miniwdl check it

Limits:
  - imports are by filename within `wdl/`, with no subdirectories and no struct/type references
    through an import alias;
  - the entry's closure must contain exactly one workflow (so a stage that imports another
    workflow, e.g. TinyResolve via GetShardInputs, is refused);
  - two different files in one closure may not declare the same task, struct or workflow name.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
import config  # noqa: E402

VERSION_RE = re.compile(r"^\s*version\s+1\.0\s*$")
IMPORT_RE = re.compile(r'^\s*import\s+"([^"]+)"(\s+as\s+([A-Za-z_][A-Za-z0-9_]*))?\s*$')
DEF_RE = re.compile(r"^(workflow|task|struct)\s+([A-Za-z_][A-Za-z0-9_]*)")

REPO = config.get("GATK_SV_CHECKOUT")


def wdl_dir() -> str:
    if not REPO or not os.path.isdir(os.path.join(REPO, "wdl")):
        raise SystemExit(
            "no gatk-sv wdl/ directory: set GSVTK_GATK_SV_CHECKOUT to a checkout "
            "(docs/config.md); it may point at a worktree")
    return os.path.join(REPO, "wdl")


def read(path: str) -> list[str]:
    with open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def parse(path: str) -> tuple[list[str], list[tuple[str, str | None]], set[str]]:
    """-> (lines with version/import stripped, [(imported file, alias)], {names defined here})."""
    lines, imports, defs = [], [], {}
    for line in read(path):
        if VERSION_RE.match(line):
            continue
        m = IMPORT_RE.match(line)
        if m:
            imports.append((m.group(1), m.group(3)))
            continue
        d = DEF_RE.match(line)
        if d:
            defs[d.group(2)] = d.group(1)      # name -> workflow | task | struct
        lines.append(line)
    return lines, imports, defs


def resolve(root: str, wf: str):
    """-> (topologically ordered [(file, lines, defs)] deps first, rewrites, raw aliases).

    rewrites[file] maps a qualified reference (`util.UntarFiles`) to the bare name it becomes
    (`UntarFiles`) once the imported file is inlined in the same document; raw[file] keeps the
    aliases themselves so an unrewired `util.` can be reported instead of shipped.
    """
    order: list[tuple[str, list[str], dict[str, str]]] = []
    seen: dict[str, dict[str, str]] = {}
    rewrites: dict[str, dict[str, str]] = {}
    raw: dict[str, set[str]] = {}

    def visit(name: str, stack: list[str]) -> None:
        if name in seen:
            return
        if name in stack:
            raise SystemExit(f"import cycle: {' -> '.join(stack + [name])}")
        path = os.path.join(root, name)
        if not os.path.isfile(path):
            raise SystemExit(f"{stack[-1] if stack else name}: imports {name!r}, not in {root}/")
        lines, imports, defs = parse(path)
        seen[name] = defs
        rw: dict[str, str] = {}
        aliases: set[str] = set()
        for imp, alias in imports:
            visit(imp, stack + [name])
            if alias:
                aliases.add(alias)
                # Only rewrite names the imported file defines, so any leftover `alias.` is an error.
                for d in seen[imp]:
                    rw[f"{alias}.{d}"] = d
        rewrites[name] = rw
        raw[name] = aliases
        order.append((name, lines, defs))

    visit(f"{wf}.wdl", [])
    return order, rewrites, raw


def flatten(wf: str, root: str | None = None) -> str:
    """The flattened document for <root>/<wf>.wdl; `root` defaults to the configured checkout's wdl/."""
    root = root or wdl_dir()
    order, rewrites, raw = resolve(root, wf)

    # Terra picks the root workflow itself, so a document holding two workflows cannot be submitted.
    wfs = [f"{name}: workflow {d}" for name, _, defs in order for d, k in defs.items() if k == "workflow"]
    if len(wfs) > 1:
        raise SystemExit(
            f"cannot flatten to one method: {len(wfs)} workflows in the closure\n  "
            + "\n  ".join(wfs)
            + "\n  Terra resolves the root workflow itself, and a document with two has no primary, so\n"
              "  a workspace method made from this fails at submission, looking like a broken WDL.\n"
              "  Submit the published parent instead -- .github/.dockstore.yml lists what Dockstore\n"
              "  carries (TinyResolve is not among them; GatherBatchEvidence is).")

    # resolve() emits each file once, so a name seen twice here is declared by two different files.
    # Refuse rather than pick one: the declarations are not known to be interchangeable.
    emitted: dict[str, str] = {}
    chunks: list[str] = []
    for name, lines, defs in order:
        for d in defs:
            if d in emitted:
                raise SystemExit(
                    f"cannot flatten: {d} is declared by both {emitted[d]} and {name}. One document\n"
                    f"  cannot hold it twice, and choosing which declaration survives is not this\n"
                    f"  tool's call -- that choice changes what the pipeline runs.")
            emitted[d] = name
        out = []
        for line in lines:
            for qual, bare in rewrites[name].items():
                if qual in line:
                    line = re.sub(rf"\b{re.escape(qual)}\b", bare, line)
            out.append(line)
        chunks.append(f"# ---- flattened from {name} ----\n" + "\n".join(out).strip("\n"))

    text = "version 1.0\n\n" + "\n\n".join(chunks) + "\n"

    # A surviving `alias.` means a reference this bundler could not rewire: fail, do not ship it.
    all_aliases = sorted({a for s in raw.values() for a in s})
    leftovers = sorted({line.strip() for line in text.splitlines()
                        for a in all_aliases
                        if re.search(rf"\b{re.escape(a)}\.[A-Za-z_]", line)})
    if leftovers:
        raise SystemExit("unrewired import aliases remain:\n  " + "\n  ".join(leftovers))
    return text


def check(path: str) -> None:
    # Same lookup as checks/wdl_gate.sh: ./.venv/bin may not be on PATH, so which() alone is not enough.
    mini = os.environ.get("MINIWDL") or config.miniwdl() or "miniwdl"
    if not (os.path.isfile(mini) and os.access(mini, os.X_OK)) and not shutil.which(mini):
        raise SystemExit(f"miniwdl not found as {mini!r}: make setup, or python -m pip install "
                         "miniwdl, or export MINIWDL=<path> (checked MINIWDL, PATH, the bin next "
                         "to this interpreter, and <repo>/.venv/bin)")
    r = subprocess.run([mini, "check", path], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"miniwdl check FAILED on the flattened document (exit {r.returncode})")
    print(f"miniwdl check: OK ({path})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("workflow", help="WDL filename without .wdl, resolved in <gatk-sv>/wdl/")
    ap.add_argument("--out", help="write here instead of stdout")
    ap.add_argument("--check", action="store_true",
                    help="run miniwdl check on the result and refuse to claim success without it")
    a = ap.parse_args()

    text = flatten(a.workflow)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {a.out} ({len(text.splitlines())} lines, "
              f"{len(text.splitlines()) and os.path.getsize(a.out)} bytes)")
        if a.check:
            check(a.out)
        else:
            print("  not verified: pass --check (needs miniwdl) before trusting this for a submission")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
