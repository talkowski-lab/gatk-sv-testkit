#!/usr/bin/env python3
"""check_skill.py — the pi skill shipped in .pi/skills/ must agree with this checkout.

Why this exists
---------------
`.pi/skills/gatk-sv-testkit/` is executable documentation: it tells an agent which Terra modes
POST, which dependency decides whether a loop runs, and which version of the wrapper it was written
against. Every one of those claims was true when the skill was written and none of them is checked
by any other phase of `make test`, because nothing in the repo *reads* the skill. That is exactly
the shape that has repeatedly produced docs confidently describing code that no longer exists here.

Checks, all offline, no credentials, no network:

  1. frontmatter: `name` matches the directory (the Agent Skills standard requires it; pi is lenient,
     the publishable set is not), `description` non-empty and within the spec's 1024 chars.
  2. `metadata.version` in SKILL.md equals `VERSION=` in scripts/gsvtk -- an invariant the skill
     states about itself in a comment, which is not the same as it being enforced.
  3. every `scripts/*` file with a shell shebang passes `bash -n`.
  4. no home-anchored absolute path anywhere in the skill: this is the publishable set, and that is
     what `make audit` exists to catch (it scans tracked files, so this catches it earlier).
  5. the refusal contract, in two halves, because one live run cannot prove both:
     * each mode SKILL.md names (plus the set hardcoded in EXPECTED_REFUSED) is executed through
       `gsvtk terra <mode>` and must answer with a refusal, not with a dispatch and not with an
       environment error;
     * the ordering claim -- "the whitelist is tested BEFORE the interpreter, so a refusal never
       looks like a broken setup" -- is checked **textually**, because on a machine that has the
       checkout's venv the environment probe succeeds whatever the order is, so no live run there
       can distinguish the two messages. Saying "tested by running it" would be a claim this file
       cannot make on the machine it ships to.
     A prose edit that drops a mode from the refused list is also a finding: the list is the safety
     contract an agent reads before deciding to bypass the wrapper.
  6. control: the same checks run against a copy with the version stamp perturbed MUST fail. Without
     this, "0 problems" is also what a checker that compares nothing reports.

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

# The modes the wrapper must refuse, restated here ON PURPOSE. Deriving them from the wrapper is
# impossible (its `case` lists what is ALLOWED; "refused" is everything else, which is not a list)
# and deriving them only from SKILL.md lets a prose edit shrink the safety contract silently -- a
# mutation test that deleted `submit` from that sentence produced no finding at all. So: SKILL.md
# must name every mode in this set, and every mode it names must really be refused.
EXPECTED_REFUSED = {"create", "submit", "copy", "attrs", "fetch", "profile", "validate"}

# Flags the skill's MUTATING prose tells an agent to reach for, restated with the file that must
# implement each one. A general "every `--flag` in the prose must exist somewhere" rule was tried on
# the way here and rejected: the prose legitimately names other tools' flags (`gcloud
# --impersonate-service-account`, the `--flag null` that a renamed sv_shell key produces several
# stages later), so it reported real sentences as defects -- and a check that cries wolf gets
# ignored, which is worse than not having it. These six are the ones where the skill is the
# *instruction*: if the flag is renamed in the code, the advice silently becomes wrong, which is the
# class of drift this whole file exists to catch (a doc line said "drop the key from CONFIGS" while
# nothing in the code pruned anything). Key = flag, value = (file, the line shape that IS the
# implementation).
#
# The pattern is restated per flag rather than inferred, because "the string appears in the file" and
# "the file implements it" are different claims and the first one is unfalsifiable: every one of these
# flags is also named inside its own refusal messages, so a full rename that left any message behind
# still "found" the old flag. Mutation-tested: renaming the real `DROP_FLAG` assignment, and renaming
# `--allow-unknown-inputs` everywhere except the comments, are both findings here.
SAFETY_FLAGS = {
    "--allow-unknown-inputs": ("terra/batch_configs.py",
                               r'"--allow-unknown-inputs" in sys\.argv'),
    "--drop-branch-only-inputs": ("terra/batch_configs.py",
                                  r'DROP_FLAG = "--drop-branch-only-inputs"'),
    "--against": ("terra/batch_configs.py", r'_flag_value\("--against"\)'),
    "--allow-unverified": ("terra/batch_freeze.py", r'"--allow-unverified" in sys\.argv'),
    "--allow-shared-target": ("terra/batch_rerun_step.py", r'"--allow-shared-target" in sys\.argv'),
    "--allow-unpinned-docker": ("terra/batch_rerun_step.py",
                                r'a == "--allow-unpinned-docker"'),
}


def code_lines(text: str) -> str:
    """The file with comment-only lines removed -- comments are the skill's neighbour, not its code.

    This is the difference between a check and a decoration. The first version asked whether the flag
    appeared anywhere in the file, and a mutation that renamed the real `DROP_FLAG = "--drop-..."`
    assignment passed: the old name survives in the explanation comment above it and in the refusal
    messages, so "the string is present" was measuring the prose, not the implementation.
    """
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
    # The other direction, scoped to the naming convention the repo uses for overrides: anything
    # backticked as `--allow-*` in the prose is a permission the skill grants an agent, so it must be
    # pinned to an implementation. Wider than that (every flag in every sentence) is the over-capture
    # the comment above refuses to import.
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


def nested(fm: str, key: str) -> str:
    m = re.search(rf"^  {key}:\s*([^#\n]+)", fm, re.M)
    return m.group(1).strip() if m else ""


def whitelist(script: Path) -> set[str]:
    """The read-only modes scripts/gsvtk will actually dispatch (`case "$what" in a|b|c)`)."""
    m = re.search(r'case "\$what" in\s*\n\s*([a-z|_-]+)\)', read(script), re.M)
    return set(m.group(1).split("|")) if m else set()


def prose_refused(skill_md: Path) -> set[str]:
    """The modes SKILL.md's 'Read-only by construction' PARAGRAPH lists as refused.

    Scoped to the one paragraph on purpose. A generous window picked up the next paragraph's
    "`show` before `submit --confirm`", which re-added `submit` after the refusal list had been
    edited to drop it -- a parser that reads "anywhere near the heading" cannot see the list being
    shortened, which is the only drift this check exists for.
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


def run_gsvtk(skill_dir: Path, mode: str) -> tuple[int, str]:
    """`gsvtk terra <mode>` with an interpreter that cannot import firecloud."""
    # A program that certainly cannot import firecloud, so the only way this call can produce a
    # refusal (rather than "no interpreter") is the whitelist being tested first.
    noenv = next((p for p in ("/usr/bin/false", "/bin/false") if Path(p).exists()), "/usr/bin/false")
    env = {"GSVTK_HOME": str(REPO), "GSVTK_TERRA_PY": noenv, "PATH": "/usr/bin:/bin"}
    r = subprocess.run([str(skill_dir / "scripts" / "gsvtk"), "terra", mode],
                       capture_output=True, text=True, env=env, errors="replace", timeout=60,
                       cwd=str(REPO))
    return r.returncode, (r.stdout + r.stderr)


# Built from pieces on purpose. `make audit` fails any tracked file containing a literal
# home-anchored path, and a checker whose own source spells the thing out is itself a HIT -- which
# is exactly what happened the first time this file was staged. Concatenation reads as what it is
# (a marker, not a path) and keeps both checks telling the truth.
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


def check_tree(skill_dir: Path, label: str = "") -> list[str]:
    """All claims, as a list of problems. Empty list == coherent."""
    pre = f"{label}: " if label else ""
    bad: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    script = skill_dir / "scripts" / "gsvtk"
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
                   f"(pi allows this; the Agent Skills standard and a shared skill dir do not)")
    desc = scalar(fm, "description")
    if not desc:
        bad.append(f"{pre}frontmatter `description` is missing -- pi only ever shows this line")
    elif len(desc) > 1024:
        bad.append(f"{pre}description is {len(desc)} chars (spec max 1024)")
    sv = nested(fm, "version")
    mv = re.search(r'^VERSION="([^"]+)"', read(script), re.M) if script.is_file() else None
    if not sv or not mv:
        bad.append(f"{pre}version stamp missing on one side (SKILL.md metadata.version="
                   f"{sv or '(none)'}, scripts/gsvtk VERSION={mv.group(1) if mv else '(none)'}")
    elif sv != mv.group(1):
        bad.append(f"{pre}version drift: SKILL.md says {sv}, scripts/gsvtk says {mv.group(1)} "
                   f"-- an unstamped skill cannot be told apart from a stale one")
    if script.is_file():
        wl = whitelist(script)
        if not wl:
            bad.append(f"{pre}could not parse the read-only whitelist out of scripts/gsvtk "
                       f"(its shape changed; this check and the skill's prose need rereading)")
        claimed = prose_refused(skill_md)
        if not claimed:
            bad.append(f"{pre}could not parse the refused-mode list out of SKILL.md's "
                       f"'Read-only by construction' paragraph (prose was reworded)")
        else:
            both = sorted(claimed & wl)
            if both:
                bad.append(f"{pre}SKILL.md says these POST/spend and are refused, but the wrapper "
                           f"dispatches them: {', '.join(both)}")
            missing = sorted(EXPECTED_REFUSED - claimed)
            if missing:
                bad.append(f"{pre}SKILL.md no longer names {', '.join(missing)} among the refused "
                           f"modes. The wrapper still refuses them; the prose is what an agent reads "
                           f"before deciding to bypass the wrapper, so shrink it deliberately (add "
                           f"the mode to EXPECTED_REFUSED in scripts/check_skill.py when you do)")
        # "The whitelist is checked BEFORE the interpreter, so a refusal is not an environment "
        # error" is a claim about line order, and on a machine that HAS the venv the environment
        # probe succeeds no matter what -- so no live run can distinguish the two messages here.
        # Checked textually, and SCOPED TO THE FUNCTION: `cmd_tools` also calls `py_for terra` and
        # sits earlier in the file, which is what a whole-file find() keeps tripping over.
        m2 = re.search(r"^cmd_terra\(\)\s*\{.*?^\}", read(script), re.S | re.M)
        if not m2:
            bad.append(f"{pre}could not find cmd_terra()'s body to check the refusal order "
                       f"(renamed? this check and the claim it guards both need rereading)")
        else:
            body = m2.group(0)
            w = body.find('case "$what" in')
            e = body.find("py_for terra")
            if w < 0 or e < 0:
                bad.append(f"{pre}cmd_terra no longer contains both the whitelist case and the "
                           f"interpreter probe ({w=}, {e=}) -- the order check cannot run")
            elif e < w:
                bad.append(f"{pre}scripts/gsvtk probes for a firecloud-capable interpreter BEFORE "
                           f"the whitelist: a refusal would then read as 'your environment is "
                           f"broken' on machines missing the venv, which is the exact failure the "
                           f"order exists to prevent")
    bad += audit_dir(skill_dir)
    for s in sorted((skill_dir / "scripts").glob("*")) if (skill_dir / "scripts").is_dir() else []:
        head = read(s).split("\n", 1)[0]
        if "#!/usr/bin/env bash" in head or "#!/bin/bash" in head or "#!/bin/sh" in head:
            r = subprocess.run(["bash", "-n", str(s)], capture_output=True, text=True)
            if r.returncode != 0:
                bad.append(f"{pre}{s.name}: bash -n says {r.stderr.strip().splitlines()[:1]}")
    return bad


def live_refusals(skill_dir: Path) -> list[str]:
    """Actually run the wrapper and require it to refuse the modes the prose names."""
    bad = []
    wl = whitelist(skill_dir / "scripts" / "gsvtk")
    claimed = prose_refused(skill_dir / "SKILL.md")
    if not claimed:
        return ["refused-mode prose could not be parsed; nothing was executed"]
    for mode in sorted(claimed | EXPECTED_REFUSED):
        if mode in wl:
            continue                                   # already reported by check_tree
        rc, out = run_gsvtk(skill_dir, mode)
        low = out.lower()
        if "firecloud" in low:
            bad.append(f"terra {mode}: reported a MISSING INTERPRETER instead of refusing -- the "
                       f"whitelist must be tested before the environment, or 'refused' is a claim "
                       f"about this machine rather than about the wrapper")
        elif rc == 0 or "refus" not in low:
            bad.append(f"terra {mode}: exit {rc}, no refusal printed (SKILL.md promises one)")
    rc, out = run_gsvtk(skill_dir, "definitely-not-a-mode")
    if "refus" not in out.lower():
        bad.append(f"the catch-all did not refuse an unknown mode (exit {rc}): {out[:80]!r}")
    return bad


def control(skill_dir: Path) -> list[str]:
    """A copy with the version stamps disagreeing MUST be reported. Proof the checks compare."""
    tmp = Path(tempfile.mkdtemp(prefix="skillctl-"))
    try:
        copy = tmp / skill_dir.name
        shutil.copytree(skill_dir, copy)
        sk = copy / "SKILL.md"
        sk.write_text(read(sk).replace("version:", "version: 9.9.9  #", 1), encoding="utf-8")
        if check_tree(copy, "control"):
            return []
        return ["control: a copy whose version stamps disagree was reported as COHERENT, so the "
                "version/name/refusal comparisons above are vacuous"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


USAGE = "usage: check_skill.py [SKILL_DIR]   (default .pi/skills/gatk-sv-testkit)\n" \
        "Checks the shipped skill's claims against the wrapper it documents. Exit 0 = coherent.\n"


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help") for a in argv):        # not argparse: the one positional is a path
        sys.stdout.write(USAGE)
        return 0
    skill = REPO / ".pi" / "skills" / "gatk-sv-testkit"
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
    # "5 claim groups" is not a boast about coverage, it is the count the assertions above actually
    # run; if a group ever parses to nothing it reports a problem instead of vanishing.
    print(f"check_skill: 6 claim groups verified in {here} "
          f"(frontmatter, version stamp, prose-vs-whitelist refusals executed, override flags still "
          f"implemented, no home paths, bash -n) + the vacuity control")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
