#!/usr/bin/env python3
"""Flatten a WDL + its imports into ONE document, so Terra can hold it as a workspace method.

Why this exists: gatk-sv's `.github/.dockstore.yml` publishes ~30 named workflows, and a workflow
that is only ever called as a sub-workflow (`TinyResolve`, called from
`GatherBatchEvidence.wdl:412`) is NOT among them. Terra method configs can point at Dockstore or at a
workspace method, and a workspace method is a single descriptor file. So the only way to submit
`TinyResolve` by itself -- which is what a cheap head-to-head of a preprocessing-stage change needs,
instead of rerunning a whole batch step -- is to bundle it. `womtool bundle` does this job; this is a
0-download dependency-free equivalent for the import style gatk-sv actually uses.

It is deliberately strict rather than clever. Anything it does not understand is an error, not a
silent pass-through, because a flattened WDL that *compiles differently* than the modular one turns a
head-to-head into a measurement of the bundler.

Proof, not hope: `--check` runs `miniwdl check` on the output and refuses to report success without
it. A flatten that resolves imports and typechecks is a flatten that means what the tree means.

    python terra/wdl_flat.py ResolveCpxSv                      # print the flattened doc
    python terra/wdl_flat.py ResolveCpxSv --out X.wdl --check  # write it + miniwdl check it

Known limit (by design): gatk-sv imports by filename within `wdl/`, with no relative subdirectories,
and no struct/type references through an import alias. `--check` catches the rest.

Second limit, found against the real tree rather than in theory: the entry's closure must contain
exactly ONE workflow. `TinyResolve` -- the case that motivated this tool -- imports
`GetShardInputs.wdl`, which declares a workflow of its own, so it is refused. Stages whose closure
is single-workflow flatten cleanly (`ResolveCpxSv`: 1 workflow, 23 tasks, 1600 lines); a
sub-workflow-only stage does not. That is a gap in the motivation above, not a usage error, and it
is not solved by relaxing the check: a two-workflow document is exactly what this tool exists to
prevent. Cromwell can itself pick a root (`--workflow-root`, cromwell.readthedocs.io CommandLine),
so a sub-workflow stage may be submittable if a Terra method config can carry that choice -- NOT
verified against Terra's method-config path, which is the only thing that would settle it, and the
only proof of that is a POST. Establish it that way rather than by bundling a document this tool
refused.
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
                # Only names the imported file really defines: a leftover `alias.` after this is
                # then provably a reference to nothing, which is worth failing on.
                for d in seen[imp]:
                    rw[f"{alias}.{d}"] = d
        rewrites[name] = rw
        raw[name] = aliases
        order.append((name, lines, defs))

    visit(f"{wf}.wdl", [])
    return order, rewrites, raw


def flatten(wf: str) -> str:
    root = wdl_dir()
    order, rewrites, raw = resolve(root, wf)
    main = f"{wf}.wdl"

    # A Terra workspace method is ONE descriptor, and WOMTool picks the root workflow itself: a
    # document holding two workflows has no primary and cannot be submitted at all. gatk-sv hits
    # this for anything that calls a sub-workflow (TinyResolve -> GetShardInputs), which is exactly
    # why bundling is not a way to run a sub-workflow standalone.
    wfs = [f"{name}: workflow {d}" for name, _, defs in order for d, k in defs.items() if k == "workflow"]
    if len(wfs) > 1:
        raise SystemExit(
            f"cannot flatten to one method: {len(wfs)} workflows in the closure\n  "
            + "\n  ".join(wfs)
            + "\n  Terra resolves the root workflow itself, and a document with two has no primary, so\n"
              "  a workspace method made from this fails at submission, looking like a broken WDL.\n"
              "  Submit the published parent instead -- .github/.dockstore.yml lists what Dockstore\n"
              "  carries (TinyResolve is not among them; GatherBatchEvidence is).")

    # Deduplicate definitions across the closure: Structs.wdl is imported by both the entrypoint and
    # Utils.wdl, and a duplicated `struct RuntimeAttr` is a compile error.
    emitted: set[str] = set()
    chunks: list[str] = []
    for name, lines, defs in order:
        for d in defs:
            if d in emitted and name != main:
                continue
            if d in emitted and name == main:
                raise SystemExit(
                    f"{main} defines {d}, already emitted from another file -- this bundler does not "
                    "guess which definition wins")
            emitted.add(d)
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
    mini = os.environ.get("MINIWDL", "miniwdl")
    if not shutil.which(mini):
        raise SystemExit(f"miniwdl not found as {mini!r}: python -m pip install miniwdl, "
                         "or export MINIWDL=<path>")
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
