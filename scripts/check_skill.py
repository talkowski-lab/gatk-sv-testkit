#!/usr/bin/env python3
"""check_skill.py — the agent skill in skills/ must agree with this checkout.

The skill in `skills/gatk-sv-testkit/` tells an agent to run `gsvtk --read-only`, which modes that
refuses, and which override flags exist. Nothing else in `make test` reads it, so this checks those
claims against `./gsvtk`.

Checks, all offline, no credentials, no network:

  1. frontmatter: `name` matches the directory, `description` is non-empty and at most 1024 chars.
  2. every `gsvtk` command the skill gives an agent carries `--read-only`; only a person's
     `--confirm` command may omit it.
  3. no home-anchored absolute path anywhere in the skill.
  4. the refusal contract:
     * each mode SKILL.md names as refused (plus EXPECTED_REFUSED) is run through
       `gsvtk --read-only terra <mode>` and must be refused, not dispatched and not an environment
       error;
     * the whitelist comes before the interpreter probe in cmd_terra (checked textually, since a
       machine with the venv can't tell the two apart at run time).
  5. every override flag the skill names is still implemented where SAFETY_FLAGS says.
  6. control: single-edit mutations of the skill must each produce their own expected finding.

Exit 0 = coherent, 1 = findings.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# The CLI the skill drives; the whitelist and refusal order are read from it.
REPO_CLI = REPO / "gsvtk"

# Modes --read-only must refuse. Kept here as well as in SKILL.md so a prose edit cannot shrink the
# list silently: SKILL.md must name every one, and every one it names must really be refused.
EXPECTED_REFUSED = {"create", "submit", "copy", "attrs", "fetch", "profile", "validate"}

# Override flags the skill tells agents to use, each with the file and the line shape that
# implements it. Matched against code lines only, since each flag also appears in comments and
# error messages.
SAFETY_FLAGS = {
    "--allow-unknown-inputs": ("terra/batch_configs.py",
                               r'"--allow-unknown-inputs" in sys\.argv'),
    "--drop-branch-only-inputs": ("terra/batch_configs.py",
                                  r'DROP_FLAG = "--drop-branch-only-inputs"'),
    "--against": ("terra/batch_configs.py", r'_flag_value\("--against"\)'),
    "--allow-unverified": ("terra/batch_freeze.py", r'add_argument\("--allow-unverified"'),
    "--allow-shared-target": ("terra/batch_rerun_step.py", r'"--allow-shared-target" in sys\.argv'),
    "--allow-unpinned-docker": ("terra/batch_rerun_step.py",
                                r'a == "--allow-unpinned-docker"'),
}


def code_lines(text: str) -> str:
    """The file with comment-only lines removed."""
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))


def check_safety_flags(repo: Path, skill_files: list[Path]) -> list[str]:
    """Every override flag the skill names must still be implemented where the skill says it is."""
    named = set()
    for p in skill_files:
        named.update(re.findall(r"`(--[a-z][a-z0-9-]+)`", read(p)))
    bad = []
    for flag, (src, shape) in sorted(SAFETY_FLAGS.items()):
        f = repo / src
        if not f.is_file():
            bad.append(f"{src}: named as the implementer of {flag}, but the file is gone")
            continue
        body = code_lines(read(f))
        if not re.search(shape, body):
            bad.append(f"{flag} is no longer implemented where the skill says it is: no line matching "
                       f"{shape!r} in {src}. The skill tells agents to reach for that override -- "
                       f"rename it in the prose and here together, or remove it from both")
    # Any `--allow-*` flag the prose names is an override and must be pinned in SAFETY_FLAGS.
    for flag in sorted(named):
        if flag.startswith("--allow-") and flag not in SAFETY_FLAGS:
            bad.append(f"{flag} is named in the skill prose as an override but pinned to no "
                       f"implementation in SAFETY_FLAGS -- add the file that implements it")
    return bad


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def frontmatter(text: str) -> str:
    m = re.match(r"\s*---\n(.*?)\n---\n", text, re.S)
    return m.group(1) if m else ""


def scalar(fm: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.*)$", fm, re.M)
    if not m:
        return ""
    v = m.group(1).strip()
    if v in (">-", ">", "|", "|-"):                      # block scalar: take the first body line
        rest = fm[m.end():].splitlines()
        body = [l.strip() for l in rest if l.strip() and (l.startswith(" ") or l.startswith("\t"))]
        return body[0] if body else ""
    return v.strip().strip('"').strip("'")


def whitelist(script: Path) -> set[str]:
    """The read-only modes a dispatcher accepts (`case "$what" in a|b|c)` on one line)."""
    m = re.search(r'case "\$what" in\s*\n\s*([a-z|_-]+)\)', read(script), re.M)
    return set(m.group(1).split("|")) if m else set()


def prose_refused(skill_md: Path) -> set[str]:
    """The modes SKILL.md's 'Read-only by construction' PARAGRAPH lists as refused.

    Scoped to that one paragraph, so a later mention of a mode elsewhere can't mask its removal.
    """
    text = read(skill_md)
    i = text.find("Read-only by construction")
    if i < 0:
        return set()
    block = text[i:].split("\n", 1)[1].lstrip("\n").split("\n\n", 1)[0]   # the heading is its own
                                                            # line; the claim is the paragraph after
    if "refused" not in block:
        return set()                            # prose reworded: report that, do not half-parse it
    modes = set()
    for tok in re.findall(r"`([a-z][a-z -]{1,20})`", block):
        head = tok.split()[0]
        if head in EXPECTED_REFUSED | {"push"}:
            modes.add(head)
    return modes


def run_gsvtk(mode: str) -> tuple[int, str]:
    """`gsvtk --read-only terra <mode>` with an interpreter that cannot import firecloud."""
    # An interpreter that cannot import firecloud, so only a whitelist-first order yields a refusal.
    noenv = next((p for p in ("/usr/bin/false", "/bin/false") if Path(p).exists()), "/usr/bin/false")
    env = {"GSVTK_TERRA_PY": noenv, "PATH": "/usr/bin:/bin"}
    r = subprocess.run([str(REPO_CLI), "--read-only", "terra", mode],
                       capture_output=True, text=True, env=env, errors="replace", timeout=60,
                       cwd=str(REPO))
    return r.returncode, (r.stdout + r.stderr)


def unguarded_calls(skill_dir: Path) -> list[str]:
    """`gsvtk` commands in the skill that an agent would run without --read-only.

    Only code is read: fenced blocks and inline code spans. A line that passes --confirm is a
    person's command, shown for them to run, so it is exempt.
    """
    bad = []
    for p in sorted(skill_dir.rglob("*.md")):
        fenced = False
        for n, line in enumerate(read(p).splitlines(), 1):
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            code = line if fenced else "\n".join(re.findall(r"`([^`]+)`", line))
            if "--confirm" in code:
                continue
            for m in re.finditer(r"(?<![\w/-])(?:\./)?gsvtk (?!--read-only)([a-z][\w-]*)", code):
                if m.group(1) in ("repo", "version"):          # print a path or sha; nothing to guard
                    continue
                bad.append(f"{p.relative_to(skill_dir)}:{n}: `{m.group(0)}` has no --read-only")
    return bad


# Built from pieces so this file doesn't contain a literal home path for `make audit` to flag.
HOME_MARKERS = ("/" + "Users" + "/", "/" + "home" + "/", "C:" + "\\" + "Users" + "\\")


def audit_dir(skill_dir: Path) -> list[str]:
    bad = []
    for p in sorted(skill_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(skill_dir)
        if any(part.startswith(".") for part in rel.parts):
            continue
        body = read(p)
        for pat in HOME_MARKERS:
            if pat in body:
                bad.append(f"{rel}: contains a home-anchored absolute path {pat!r} "
                           f"(this file gets published with the repo)")
    return bad


def check_tree(skill_dir: Path, label: str = "", cli: Path = None) -> list[str]:
    """All claims, as a list of problems. Empty list == coherent.

    `cli` is the dispatcher holding the whitelist; it defaults to the repo entry point, so the
    control can run this on a copied skill tree.
    """
    pre = f"{label}: " if label else ""
    bad: list[str] = []
    cli = cli or REPO_CLI
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return [f"{pre}no SKILL.md"]
    fm = frontmatter(read(skill_md))
    if not fm:
        bad.append(f"{pre}SKILL.md has no --- frontmatter block")
    name = scalar(fm, "name")
    if not name:
        bad.append(f"{pre}frontmatter `name` is missing")
    elif name != skill_dir.name:
        bad.append(f"{pre}frontmatter name {name!r} != directory {skill_dir.name!r} "
                   f"(the Agent Skills format requires them to match)")
    desc = scalar(fm, "description")
    if not desc:
        bad.append(f"{pre}frontmatter `description` is missing -- it is what decides when an agent loads the skill")
    elif len(desc) > 1024:
        bad.append(f"{pre}description is {len(desc)} chars (spec max 1024)")
    bad += unguarded_calls(skill_dir)

    if cli.is_file():
        wl = whitelist(cli)
        if not wl:
            bad.append(f"{pre}could not parse the read-only whitelist out of ./gsvtk (the repo CLI "
                       f"implements it, so its shape changed and this check plus the skill's prose "
                       f"need rereading)")
        claimed = prose_refused(skill_md)
        if not claimed:
            bad.append(f"{pre}could not parse the refused-mode list out of SKILL.md's "
                       f"'Read-only by construction' paragraph (prose was reworded)")
        else:
            both = sorted(claimed & wl)
            if both:
                bad.append(f"{pre}SKILL.md says these POST/spend and are refused, but the repo CLI "
                           f"dispatches them: {', '.join(both)}")
            missing = sorted(EXPECTED_REFUSED - claimed)
            if missing:
                bad.append(f"{pre}SKILL.md no longer names {', '.join(missing)} among the refused "
                           f"modes. The CLI still refuses them; the prose is what an agent reads "
                           f"before deciding to bypass the gate, so shrink it deliberately (add the "
                           f"mode to EXPECTED_REFUSED in scripts/check_skill.py when you do)")
        # The whitelist must come before the interpreter probe. Checked textually and scoped to
        # cmd_terra, since cmd_tools also calls `py_for terra` earlier in the file.
        m2 = re.search(r"^cmd_terra\(\)\s*\{.*?^\}", read(cli), re.S | re.M)
        if not m2:
            bad.append(f"{pre}could not find cmd_terra()'s body in ./gsvtk to check the refusal order "
                       f"(renamed? this check and the claim it guards both need rereading)")
        else:
            body = m2.group(0)
            w = body.find('case "$what" in')
            e = body.find("py_for terra")
            if w < 0 or e < 0:
                bad.append(f"{pre}cmd_terra no longer contains both the whitelist case and the "
                           f"interpreter probe ({w=}, {e=}) -- the order check cannot run")
            elif e < w:
                bad.append(f"{pre}./gsvtk probes for a firecloud-capable interpreter BEFORE the "
                           f"whitelist: a refusal would then read as 'your environment is broken' "
                           f"on machines missing the venv, which is the exact failure the order exists "
                           f"to prevent")
    else:
        bad.append(f"{pre}{cli} is missing, so the whitelist / ordering claims have nothing to grade")
    bad += audit_dir(skill_dir)
    return bad


def live_refusals(skill_dir: Path) -> list[str]:
    """Actually run `gsvtk --read-only` and require it to refuse the modes the prose names."""
    bad = []
    wl = whitelist(REPO_CLI)
    claimed = prose_refused(skill_dir / "SKILL.md")
    if not claimed:
        return ["refused-mode prose could not be parsed; nothing was executed"]
    for mode in sorted(claimed | EXPECTED_REFUSED):
        if mode in wl:
            continue                                   # already reported by check_tree
        rc, out = run_gsvtk(mode)
        low = out.lower()
        if "firecloud" in low:
            bad.append(f"terra {mode}: reported a MISSING INTERPRETER instead of refusing -- the "
                       f"whitelist must be tested before the environment, or 'refused' is a claim "
                       f"about this machine rather than about the CLI")
        elif rc == 0 or "refus" not in low:
            bad.append(f"terra {mode}: exit {rc}, no refusal printed (SKILL.md promises one)")
    rc, out = run_gsvtk("definitely-not-a-mode")
    if "refus" not in out.lower():
        bad.append(f"the catch-all did not refuse an unknown mode (exit {rc}): {out[:80]!r}")
    return bad


def control(skill_dir: Path) -> list[str]:
    """Mutations that must each be reported with their own finding, so the checks aren't vacuous.

    Variants: --read-only dropped from one command, a refused mode dropped from the prose, and a
    home path planted in the skill.
    """
    tmp = Path(tempfile.mkdtemp(prefix="skillctl-"))
    bad: list[str] = []

    def run_mutation(name: str, edit, needle: str) -> None:
        """Copy the skill, apply one textual edit to SKILL.md, and require the finding it should cause."""
        copy = tmp / ("v-" + name)
        shutil.copytree(skill_dir, copy)
        target = copy / "SKILL.md"
        target.write_text(edit(read(target)), encoding="utf-8")
        problems = check_tree(copy, "control")
        if not any(needle in p for p in problems):
            bad.append(f"control/{name}: nothing reported matching {needle!r} "
                       f"(got {len(problems)} finding(s)), so the check that should have caught it "
                       f"is vacuous")

    run_mutation("unguarded", lambda t: t.replace("gsvtk --read-only locate", "gsvtk locate", 1),
                 "has no --read-only")
    run_mutation("refused-mode", lambda t: t.replace("`profile` are refused", "nothing else is refused", 1),
                 "no longer names profile")
    run_mutation("home-path", lambda t: t + "\n" + HOME_MARKERS[0] + "someone/x\n",
                 "home-anchored")
    shutil.rmtree(tmp, ignore_errors=True)
    return bad


USAGE = "usage: check_skill.py [SKILL_DIR]   (default skills/gatk-sv-testkit)\n" \
        "Checks the shipped skill's claims against ./gsvtk. Exit 0 = coherent.\n"


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help") for a in argv):        # not argparse: the one positional is a path
        sys.stdout.write(USAGE)
        return 0
    skill = REPO / "skills" / "gatk-sv-testkit"
    args = [a for a in argv if not a.startswith("-")]
    if args:
        skill = Path(args[0]).resolve()
    if not skill.is_dir():
        print(f"check_skill: no skill at {skill} — SKIP is a finding here, the skill is shipped "
              f"in-repo now")
        return 1

    skill_md = [skill / "SKILL.md"] + sorted((skill / "references").glob("*.md"))
    problems = (check_tree(skill) + live_refusals(skill) + control(skill)
                + check_safety_flags(REPO, [p for p in skill_md if p.is_file()]))
    here = skill.relative_to(REPO) if skill.is_relative_to(REPO) else skill
    for p in problems:
        print(f"  PROBLEM  {p}")
    if problems:
        print(f"check_skill: {len(problems)} problem(s) in {here}")
        return 1
    print(f"check_skill: 6 claim groups verified in {here} "
          f"(frontmatter, --read-only on every agent command, prose-vs-whitelist refusals executed, "
          f"override flags still implemented, no home paths) + the three-mutation control, each "
          f"graded by its own finding")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
