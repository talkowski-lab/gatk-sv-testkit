#!/usr/bin/env python3
"""Materialize a gatk-sv `wdl/` tree at one git ref into a directory.

Why this exists instead of committing the WDLs here: a copy of 125 WDL files goes stale
against gatk-sv `main` within days, and a stale copy is worse than none — checks pass
against bytes that no longer exist. `git archive` of a ref is exact, cheap, and never
drifts, because the ref is named every time.

The "one ref" part matters. WDL imports are resolved by filename in the same directory,
so if you mix files from two refs the imports resolve against the wrong version and you
get either false failures or, worse, false passes.

    scripts/fetch_wdl.py                       # -> <work>/wdl/main, ref = origin/main
    scripts/fetch_wdl.py --ref v1.1.1          # a release, e.g. to read the baseline's WDLs
    scripts/fetch_wdl.py --ref HEAD --dest /tmp/x
    scripts/fetch_wdl.py --list --ref HEAD     # names only, no extraction

Uses a local clone (GSVTK_GATK_SV_CHECKOUT) and never clones from the network: if you are
testing a branch, the bytes you check should be the bytes you are pushing.
"""
from __future__ import annotations

import argparse
import os
import io
import subprocess
import sys
import tarfile
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
import config  # noqa: E402


def run(repo: Path, *args: str) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed in {repo}:\n{r.stderr.decode(errors='replace')}")
    return r.stdout


def resolve_ref(repo: Path, ref: str) -> str:
    """Full commit SHA for a ref, so the output records exactly what was extracted."""
    return run(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()


def extract(repo: Path, ref: str, dest: Path, subdir: str = "wdl") -> list[str]:
    sha = resolve_ref(repo, ref)
    dest.mkdir(parents=True, exist_ok=True)
    # `git archive` gives a byte-exact tar of just that subtree at that commit: no
    # working-tree state, no .git pollution. Buffered rather than streamed so tarfile
    # can seek; the gatk-sv wdl/ tree is a couple of MB.
    raw = run(repo, "archive", sha, subdir)
    names: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            target = dest / Path(member.name).name  # flatten wdl/: imports resolve by filename
            target.write_bytes(handle.read())
            names.append(target.name)
    if not names:
        raise SystemExit(f"no '{subdir}/' files at {ref} in {repo} — is that ref a gatk-sv tree?")
    with open(dest / ".provenance", "w") as fh:
        fh.write(f"repo={repo}\nref={ref}\nsha={sha}\nfiles={len(names)}\n")
    return sorted(names)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=config.get("GATK_SV_CHECKOUT"),
                    help="local gatk-sv clone (default GSVTK_GATK_SV_CHECKOUT)")
    ap.add_argument("--ref", default="origin/main",
                    help="branch, tag or SHA (default origin/main)")
    ap.add_argument("--dest", default=None,
                    help="output dir (default <work>/wdl/<ref>)")
    ap.add_argument("--subdir", default="wdl", help="subtree inside the repo (default wdl)")
    ap.add_argument("--list", action="store_true", help="print file names, extract nothing")
    a = ap.parse_args()

    if not a.repo:
        raise SystemExit("GSVTK_GATK_SV_CHECKOUT is not set and --repo was not given "
                         "(docs/config.md) — a ref is meaningless without a repository.")
    repo = config.checkout("GATK_SV_CHECKOUT") if a.repo == config.get("GATK_SV_CHECKOUT") else Path(a.repo).expanduser()
    if not (repo / ".git").exists():
        raise SystemExit(f"{repo} is not a git repository")

    if a.list:
        sha = resolve_ref(repo, a.ref)
        files = run(repo, "ls-tree", "-r", "--name-only", sha, a.subdir).decode().split()
        print(f"# {repo} @ {a.ref} = {sha[:12]}  {len(files)} file(s) under {a.subdir}/")
        for f in files:
            print(f)
        return 0

    dest = Path(a.dest).expanduser() if a.dest else config.work_dir("wdl", a.ref.replace("/", "-"))
    names = extract(repo, a.ref, dest, a.subdir)
    print(f"{len(names)} WDL file(s) from {repo} @ {a.ref} -> {dest}")
    print(f"provenance recorded in {dest / '.provenance'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
