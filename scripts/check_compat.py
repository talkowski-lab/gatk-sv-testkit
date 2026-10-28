#!/usr/bin/env python3
"""check_compat.py — is this testkit compatible with a gatk-sv ref? Recorded in compat.json.

A ref plays one of two roles:

  branch    the gatk-sv commit a branch under test is based on. The module profiles describe it, so
            every profile must agree with it: `scripts/derive_profile.py check` reports zero
            disagreements (differences the profile explains are listed as expected, not counted).
  baseline  the release the frozen baseline run used. Its bindings come from the Terra workspace,
            not from the profiles, so only the layout is checked.

Both roles check the layout the kit reads: `wdl/<file>.wdl` and the Dockstore entry for every profile
step, the Terra workspace templates, `inputs/values/dockers.json` and `scripts/inputs/build_inputs.py`.
A branch ref also needs the sv-shell driver and sample inputs, which only the branch-side tools read
(`replay svshell`, the sv-shell contract check).

    python scripts/check_compat.py check --ref origin/main --role branch
    python scripts/check_compat.py check --ref v1.1.1 --role baseline --record
    python scripts/check_compat.py table               # the README table, from compat.json
    python scripts/check_compat.py table --check README.md
    python scripts/check_compat.py note                # one warning line if the checkout is untested

The gatk-sv clone is `--repo`, else GSVTK_GATK_SV_CHECKOUT. It is read with git only (`rev-parse`,
`cat-file`, `show`), so its working tree is never touched. `--record` writes compat.json; nothing else
is written.

Exit codes
    0  check: compatible.  table: printed, or --check found it current.  note: always
    1  check: not compatible (each failing check listed).  table --check: README is stale
    2  usage error or unresolvable ref
    3  `git` is not installed
    4  no gatk-sv checkout configured and no --repo
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit"))
import config                                   # noqa: E402

COMPAT = ROOT / "compat.json"
PROFILES = ROOT / "profiles"
ROLES = ("branch", "baseline")
DOCKSTORE_YML = ".github/.dockstore.yml"
# Paths outside the profiles that the kit reads at a gatk-sv ref.
LAYOUT = (
    "inputs/templates/terra_workspaces",
    "inputs/values/dockers.json",
    "scripts/inputs/build_inputs.py",
)
LAYOUT_BRANCH = (
    "src/sv_shell/single_sample_pipeline.sh",
    "src/sv_shell/sample_inputs",
)
TABLE_BEGIN = "<!-- compat-table:begin (scripts/check_compat.py table) -->"
TABLE_END = "<!-- compat-table:end -->"


class Refusal(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo)] + list(args), capture_output=True, text=True)


def resolve_repo(arg: str) -> Path:
    raw = arg or config.get("GATK_SV_CHECKOUT")
    if not raw:
        raise Refusal(4, "check_compat: GSVTK_GATK_SV_CHECKOUT is not set and --repo was not given")
    repo = Path(raw).expanduser()
    if not (repo / ".git").exists():
        raise Refusal(4, "check_compat: %s is not a git clone" % repo)
    return repo


def resolve_sha(repo: Path, ref: str) -> str:
    r = git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    if r.returncode != 0:
        raise Refusal(2, "check_compat: %r is not a commit in %s (fetch it first?)" % (ref, repo))
    return r.stdout.strip()


def load_profiles() -> list:
    """(module name, [step dicts]) for every profile under profiles/."""
    out = []
    for p in sorted(PROFILES.glob("*.json")):
        doc = json.loads(p.read_text())
        out.append((p.stem, doc.get("steps") or []))
    return out


def check_layout(repo: Path, sha: str, role: str) -> list:
    """[(check name, ok, detail)] for every path and Dockstore entry the kit reads in this role."""
    results = []
    for path in LAYOUT + (LAYOUT_BRANCH if role == "branch" else ()):
        ok = git(repo, "cat-file", "-e", "%s:%s" % (sha, path)).returncode == 0
        results.append(("layout " + path, ok, "present" if ok else "missing"))
    yml = git(repo, "show", "%s:%s" % (sha, DOCKSTORE_YML))
    entries = set(re.findall(r"^\s*-?\s*name:\s*(\S+)\s*$", yml.stdout, re.M)) if yml.returncode == 0 else set()
    seen = set()
    for module, steps in load_profiles():
        for step in steps:
            wdl = "wdl/%s.wdl" % step.get("wdl")
            if wdl not in seen:
                seen.add(wdl)
                ok = git(repo, "cat-file", "-e", "%s:%s" % (sha, wdl)).returncode == 0
                results.append(("layout " + wdl, ok, "present" if ok else "missing"))
            entry = step.get("dockstore") or step.get("workflow")
            if entry not in seen:
                seen.add(entry)
                ok = entry in entries
                results.append(("dockstore " + entry, ok,
                                "listed" if ok else "not in %s" % DOCKSTORE_YML))
    return results


def check_profiles(repo: Path, sha: str) -> list:
    """[(check name, ok, detail)]: each profile must agree with upstream at this ref."""
    results = []
    for module, _steps in load_profiles():
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "derive_profile.py"), "check",
                            "--module", module, "--repo", str(repo), "--ref", sha],
                           capture_output=True, text=True)
        m = re.search(r"GSVTK-DERIVE-CHECK compared=(\d+) disagree=(\d+) expected=(\d+)", r.stdout)
        if not m:
            tail = (r.stderr.strip().splitlines() or ["no output"])[-1]
            results.append(("profile " + module, False, "check did not run (exit %d: %s)"
                            % (r.returncode, tail)))
            continue
        compared, disagree, expected = (int(x) for x in m.groups())
        ok = r.returncode == 0 and disagree == 0
        results.append(("profile " + module, ok, "%d compared, %d disagree, %d expected"
                        % (compared, disagree, expected)))
    return results


def read_compat() -> dict:
    if not COMPAT.exists():
        return {"refs": []}
    return json.loads(COMPAT.read_text())


def write_compat(doc: dict) -> None:
    COMPAT.write_text(json.dumps(doc, indent=2) + "\n")


def cmd_check(a) -> int:
    repo = resolve_repo(a.repo)
    sha = resolve_sha(repo, a.ref)
    results = check_layout(repo, sha, a.role)
    if a.role == "branch":
        results += check_profiles(repo, sha)
    ok = all(r[1] for r in results)
    print("gatk-sv %s @ %s, role %s" % (a.ref, sha[:12], a.role))
    for name, good, detail in results:
        print("  %-4s  %-45s %s" % ("ok" if good else "FAIL", name, detail))
    verdict = "compatible" if ok else "not compatible"
    print("GSVTK-COMPAT ref=%s sha=%s role=%s verdict=%s failed=%d"
          % (a.ref, sha[:12], a.role, verdict.replace(" ", "-"), sum(1 for r in results if not r[1])))
    if a.record:
        # A remote-tracking name is local to one clone; record the branch name.
        name = re.sub(r"^(refs/remotes/)?origin/", "", a.ref)
        doc = read_compat()
        entry = {"ref": name, "sha": sha, "role": a.role, "verdict": verdict,
                 "checked": datetime.date.today().isoformat(),
                 "checks": {name: detail for name, _good, detail in results if not name.startswith("layout ")}}
        entry["checks"]["layout"] = ("%d of %d paths present"
                                     % (sum(1 for n, g, _d in results if n.startswith("layout ") and g),
                                        sum(1 for n, _g, _d in results if n.startswith("layout "))))
        doc["refs"] = [e for e in doc.get("refs", []) if (e.get("ref"), e.get("role")) != (name, a.role)]
        doc["refs"].append(entry)
        doc["refs"].sort(key=lambda e: (ROLES.index(e["role"]), e["ref"]))
        write_compat(doc)
        print("recorded in %s" % COMPAT)
    return 0 if ok else 1


def render_table(doc: dict) -> str:
    rows = ["| gatk-sv ref | commit | role | verdict | checked |", "|---|---|---|---|---|"]
    for e in doc.get("refs", []):
        rows.append("| `%s` | `%s` | %s | %s | %s |" % (e["ref"], e["sha"][:8], e["role"], e["verdict"],
                                                       e["checked"]))
    return "\n".join(rows)


def cmd_table(a) -> int:
    table = render_table(read_compat())
    if not a.check:
        print(table)
        return 0
    text = Path(a.check).read_text()
    i, j = text.find(TABLE_BEGIN), text.find(TABLE_END)
    if i < 0 or j < i:
        print("check_compat: %s has no %s ... %s block" % (a.check, TABLE_BEGIN, TABLE_END))
        return 1
    current = text[i + len(TABLE_BEGIN):j].strip()
    if current != table:
        print("check_compat: the table in %s does not match compat.json. Regenerate it with:\n"
              "  python scripts/check_compat.py table" % a.check)
        return 1
    print("check_compat: the table in %s matches compat.json (%d ref(s))"
          % (a.check, len(read_compat().get("refs", []))))
    return 0


def cmd_note(a) -> int:
    """Warn on stderr when the checkout's HEAD is not a recorded compatible branch commit. Never fails."""
    try:
        repo = resolve_repo(a.repo)
    except Refusal:
        return 0
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    tested = [e for e in read_compat().get("refs", [])
              if e.get("role") == "branch" and e.get("verdict") == "compatible"]
    if not head or not tested or any(e["sha"] == head for e in tested):
        return 0
    newest = max(tested, key=lambda e: e["checked"])
    if git(repo, "merge-base", "--is-ancestor", newest["sha"], head).returncode == 0:
        n = git(repo, "rev-list", "--count", "%s..%s" % (newest["sha"], head)).stdout.strip()
        where = "%s commit(s) past" % n
    else:
        where = "not descended from"
    print("gsvtk: note: your gatk-sv checkout (%s) is %s the newest commit this testkit was checked "
          "against (%s %s, %s). Check it with: gsvtk compat --ref HEAD"
          % (head[:8], where, newest["ref"], newest["sha"][:8], newest["checked"]), file=sys.stderr)
    return 0


def main(argv: list) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("check", "table", "note"))
    ap.add_argument("--repo", default="", help="gatk-sv clone (default GSVTK_GATK_SV_CHECKOUT)")
    ap.add_argument("--ref", default="HEAD", help="check: branch, tag or SHA (default HEAD)")
    ap.add_argument("--role", choices=ROLES, default="branch", help="check: what the ref is used as")
    ap.add_argument("--record", action="store_true", help="check: write the result to compat.json")
    ap.add_argument("--check", metavar="FILE", default="",
                    help="table: compare FILE's table with compat.json instead of printing")
    a = ap.parse_args(argv)
    if a.mode == "table":
        return cmd_table(a)
    if shutil.which("git") is None:
        if a.mode == "note":
            return 0
        print("check_compat: git is not installed", file=sys.stderr)
        return 3
    try:
        return cmd_check(a) if a.mode == "check" else cmd_note(a)
    except Refusal as e:
        print(str(e), file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
