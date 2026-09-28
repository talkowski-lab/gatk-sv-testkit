#!/usr/bin/env python3
"""check_docs.py — the prose in this repo is a deliverable, so its structure gets checked.

Why
---
CONTRIBUTING says `make test` catches `--help` drift, not doc drift, and that has been true: flags
in docs were wrong while every phase stayed green. This checks the three mechanical things that make
a markdown file *wrong to read*, all of which have actually happened here:

  1. **Unbalanced fences.** `docs/setup.md` shipped with the fence opened, then a blockquote, then a
     bare line `bash`, then the commands. Rendered, the privacy warning telling you not to attach
     `recon/*.json` to an issue displayed *as shell code*, and the snippet gained a bogus `bash`
     command line. One stray fence does that, and no checker looked.
  2. **A swallowed info string**: a line inside a code block that is exactly a language tag. That is
     what defect (1) leaves behind, so it is checked directly rather than only via parity.
  3. **Relative links that point at nothing.** The docs cross-link constantly (and the skill links
     `references/workflows.md`); a renamed file silently turns "see docs/config.md" into a lie.

Out of scope, deliberately: prose quality, house style, line length. A checker that nags about
wording gets ignored, which is worse than not having it.

Exit 0 = clean, 1 = findings printed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

GLOBS = ("README.md", "CONTRIBUTING.md", "docs/**/*.md", ".pi/skills/**/*.md")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
# A line that is nothing but a language tag inside a fenced block is a fence that lost its opener.
LANGS = {"bash", "sh", "shell", "console", "text", "plain", "json", "yaml", "yml", "toml", "ini",
         "env", "diff", "patch", "python", "py", "wdl", "makefile", "md", "markdown", "csv", "tsv"}


def files() -> list[Path]:
    out: list[Path] = []
    for g in GLOBS:
        out += sorted(REPO.glob(g))
    return sorted({p for p in out if p.is_file()})


def check_file(path: Path) -> list[str]:
    bad: list[str] = []
    # the control runs the same function on copies outside the repo, so this must not assume it
    try:
        rel = path.relative_to(REPO)
    except ValueError:
        rel = path.name
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    open_at = None
    for n, line in enumerate(lines, start=1):
        m = FENCE.match(line)
        if m:
            if open_at is None:
                info = m.group(2).strip()
                # An info string is a language and maybe attributes: `bash title="x"` is legal,
                # "``` the commands below" is a fence someone typed where prose was meant.
                if info and not re.fullmatch(r"[A-Za-z0-9_+.#-]+(\s+[\w.=\"-]+)*", info):
                    bad.append(f"{rel}:{n}: fence opener has prose in its info string: {info[:48]!r}")
                open_at = n
            else:
                open_at = None
            continue
        if open_at is not None and line.strip().lower() in LANGS and line.strip() == line.strip().lower():
            bad.append(f"{rel}:{n}: bare {line.strip()!r} INSIDE the code block opened at "
                       f":{open_at} -- that is an info string that lost its ``` (a previous defect "
                       f"here rendered a privacy warning as shell code)")
    if open_at is not None:
        bad.append(f"{rel}:{open_at}: code fence opened here and never closed (everything after it "
                   f"is a code block)")
    for n, line in enumerate(lines, start=1):
        for target in re.findall(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", line):
            if target.startswith(("http://", "https://", "mailto:", "#", "/")):
                continue
            if "<" in target or target.startswith(("(", )):      # example syntax in prose, not a link
                continue
            dest = (path.parent / target.split("#")[0]).resolve()
            if not target.split("#")[0] or dest.exists():
                continue
            bad.append(f"{rel}:{n}: relative link -> {target!r} matches no file")
    return bad


def control(checked: list[Path]) -> list[str]:
    """Break a real file in a temp copy; the checks MUST see it. Otherwise 'clean' means nothing."""
    import tempfile, shutil
    src = next((p for p in checked if p.name == "setup.md"), checked[0])
    tmp = Path(tempfile.mkdtemp(prefix="docsctl-"))
    try:
        # mirror the two mutations inside a copy of the tree layout the checker reasons about
        for name, mutation in (
                ("dropped-closing-fence", lambda t: t.replace("\n```\n", "\n", 1)),
                ("swallowed-info-string",
                 lambda t: t.replace("```bash", "```\n> note\nbash", 1))):
            d = tmp / "docs"
            d.mkdir(parents=True, exist_ok=True)
            copy = d / src.name
            shutil.copyfile(src, copy)
            copy.write_text(mutation(src.read_text(encoding="utf-8")), encoding="utf-8")
            # Only a FENCE finding counts. The copy sits outside the repo, so its relative links
            # cannot resolve -- accepting "any problem" would let the control pass for an
            # unrelated reason and prove nothing about the fence checks.
            hits = [b for b in check_file(copy) if "fence" in b or "info string" in b]
            if not hits:
                return [f"control: {name} applied to {src.name} was NOT reported as a fence "
                        f"problem -- the fence checks above are vacuous"]
        return []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help") for a in argv):        # takes no arguments; say so, do not run blind
        sys.stdout.write("usage: check_docs.py            (no arguments)\n"
                         "Checks README/CONTRIBUTING/docs/.pi skills for unbalanced fences, "
                         "swallowed info strings and dead relative links.\n")
        return 0
    targets = files()
    if not targets:
        print("check_docs: found no markdown to check -- that is a finding, not a pass")
        return 1
    problems: list[str] = []
    for p in targets:
        problems += check_file(p)
    problems += control(targets)
    for p in problems:
        print(f"  PROBLEM  {p}")
    if problems:
        print(f"check_docs: {len(problems)} problem(s) across {len(targets)} file(s)")
        return 1
    print(f"check_docs: {len(targets)} file(s) -- fences balanced, no swallowed info strings, "
          f"every relative link resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
