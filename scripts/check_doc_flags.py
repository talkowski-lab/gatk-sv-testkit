#!/usr/bin/env python3
"""check_doc_flags.py — every flag the docs tell you to type is a flag the tool actually accepts.

An audit of this repo found the gap ledger was not copy-pasteable: it wrote `./terra/batch_peek.py`
while that file was tracked 100644 (Permission denied on a clean checkout), it named a task
(`--block Condense`) that does not exist upstream, and it quoted `--ref <sha>` for a script that takes
refs positionally. `check_docs.py` grades fences and links, so nothing noticed — and a doc whose
command cannot run is worse than no doc, because it is the artifact a reader trusts most.

So this grades the *commands themselves*, in README.md and every docs/*.md:

    tool        a `*.py` / `*.sh` path in a code span or fence must exist in the repo
    exec bit    if the doc runs it as `./path`, the file must be tracked executable (mode 100755)
    flags       every `--long-flag` in that command must appear in `tool --help`
    task names  `--block X` / `--task X` are only checked for existence (they name upstream data)

Interpreter-dependent tools (a `firecloud`/`miniwdl` guard prints instead of crashing) are run with
this process's interpreter, which is the kit's when the gate runs it. Anything whose --help cannot be
obtained is reported, never skipped silently: a checker that cannot run passes by default, and that is
the failure class this file exists to catch.

    ./scripts/check_doc_flags.py                  # README.md + docs/*.md
    ./scripts/check_doc_flags.py --doc FILE       # one file
    ./scripts/check_doc_flags.py --selftest       # fixtures: each defect found, clean doc silent
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile

# `./terra/batch_peek.py`, `python terra/x.py`, `bash checks/wdl_gate.sh` — the tool is the path token.
FLAG_RE = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
SPAN_RE = re.compile(r"`([^`\n]+)`")
FENCE_RE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
GIT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def code_spans(text: str) -> list:
    """Inline code spans and fenced-block lines — the places a reader copies a command from."""
    out = list(SPAN_RE.findall(text))
    for block in FENCE_RE.findall(text):
        out.extend(block.splitlines())
    return out


def tracked_mode(path: str) -> str:
    """The git mode ('100644'/'100755') of a tracked file, or '' when it is not tracked."""
    try:
        r = subprocess.run(["git", "ls-files", "-s", "--", path], cwd=GIT_ROOT,
                           capture_output=True, text=True, timeout=30)
    except Exception:
        return ""
    for line in (r.stdout or "").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            return parts[0]
    return ""


_TRACKED = []


INTERPRETERS = ("python", "python3", "bash", "sh", "zsh", "$GSVTK_PYTHON", "$PY", "exec")


# Scripts whose `--help` is not a usage printer. `scripts/selftest.sh` takes no arguments at all: it
# IS the gate, so asking it for help RAN the whole suite — 5 minutes of recursive gate inside the gate,
# found by timing the scan at 5m. Probing such a tool is not grading it, so it is named instead.
HARNESS = {"scripts/selftest.sh"}


def command_tool(span: str) -> str:
    """The tool a command INVOKES, or "" when the span is not invoking a repo tool.

    Only the command position counts: a span like `--probe scripts/test/test_sigpipe.sh` names an
    argument that lives upstream, and `<ref>:src/sv_shell/x.sh` is a git revision path, not a tool
    this repo owns. Grading those as commands is how a checker invents 25 findings and gets ignored.
    """
    toks = span.strip().split()
    if not toks:
        return ""
    first = toks[0].strip("'")
    tool = toks[1] if (first in INTERPRETERS or first.endswith("/python") or first.endswith("/python3")) \
        and len(toks) > 1 else first
    if not re.search(r"\.(py|sh)$", tool):
        return ""
    # A glob (`checks/image-check/*.sh`) is a set being described, and an absolute path
    # (`/tmp/gsv-launch.sh`) is somebody's scratch file quoted in a recipe. Neither is a tool this
    # repo can grade a flag of; inventing findings for them is how a checker gets disabled.
    if "*" in tool or "?" in tool or tool.startswith("/") or "$" in tool:
        return ""
    return tool


def resolve(rel: str) -> str:
    """Repo-relative path of a tool named in a doc — docs often write `wdl_gate.sh`, not the path.

    Exact path first; then a unique tracked basename match. A basename that matches two files is left
    unresolved and reported, because guessing which one the doc meant is how a check ends up grading
    the wrong thing and calling the result clean.
    """
    global _TRACKED
    if os.path.isfile(os.path.join(GIT_ROOT, rel)):
        return rel
    base = rel.rsplit("/", 1)[-1]
    if not _TRACKED:
        try:
            r = subprocess.run(["git", "ls-files", "*"], cwd=GIT_ROOT, capture_output=True,
                               text=True, timeout=30)
            _TRACKED = (r.stdout or "").splitlines()
        except Exception:
            _TRACKED = []
    hits = [p for p in _TRACKED if p.rsplit("/", 1)[-1] == base]
    return hits[0] if len(hits) == 1 else rel


def help_text(tool_rel: str, run=subprocess.run) -> tuple:
    """(text, error). Runs `tool --help` with the interpreter its extension asks for."""
    abs_tool = os.path.join(GIT_ROOT, tool_rel)
    if tool_rel in HARNESS:
        return "", "is a harness, not a CLI: `--help` would run it rather than describe it"
    argv = [sys.executable, abs_tool] if tool_rel.endswith(".py") else (
        ["bash", abs_tool] if tool_rel.endswith(".sh") else [abs_tool])
    try:
        r = run(list(argv) + ["--help"], cwd=GIT_ROOT, capture_output=True, text=True, timeout=25)
    except Exception as exc:
        return "", f"{type(exc).__name__}: {str(exc)[:120]}"
    if r.returncode != 0:
        return "", f"--help exited {r.returncode}: {(r.stdout or r.stderr or '')[:200].strip()}"
    return (r.stdout or "") + (r.stderr or ""), ""


def check_file(path: str, run=subprocess.run, cache=None) -> list:
    """Problems as (kind, doc, command, detail). Never silently drops a file it cannot grade."""
    problems = []
    # One --help per tool for the WHOLE run, not per file: with a per-file cache the 13 graded files
    # re-spawned ~40 interpreter-starting subprocesses each, which added minutes to the gate. Hoisting
    # it is also why this can live in `make test` at all.
    cache = {} if cache is None else cache
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError as exc:
        return [("unreadable", path, "", str(exc))]
    rel_doc = os.path.relpath(path, GIT_ROOT)
    cache = {}
    for span in code_spans(text):
        tool = command_tool(span)
        if not tool:
            continue
        explicit = tool.startswith("./")
        rel = resolve(tool[2:] if explicit else tool)
        if not os.path.isfile(os.path.join(GIT_ROOT, rel)):
            if explicit or "/" in tool:
                problems.append(("missing-tool", rel_doc, span.strip()[:100],
                                 f"{tool} does not exist"))
            continue                          # a bare name in prose is not a repo command
        if tool.startswith("./") and not os.access(os.path.join(GIT_ROOT, rel), os.X_OK):
            problems.append(("no-exec-bit", rel_doc, span.strip()[:100],
                             f"{rel} is not executable, so `./{rel}` cannot run as written "
                             "(tracked mode is what a clean checkout gets)"))
        if rel not in cache:
            cache[rel] = help_text(rel, run=run)
        htext, herr = cache[rel]
        if herr:
            # A tool with no --help is not a passing grade, it is an UNGRADEABLE one: reported, and
            # counted, so the gate can see the checker's own coverage shrink.
            problems.append(("no-help", rel_doc, span.strip()[:100], f"{rel}: {herr}"))
            continue
        for flag in FLAG_RE.findall(span):
            if flag == "--help":
                continue
            if flag not in htext:
                problems.append(("unknown-flag", rel_doc, span.strip()[:100],
                                 f"{rel} does not document {flag} in its --help"))
    return problems


def markdown_files(one: str) -> list:
    if one:
        return [one]
    files = [os.path.join(GIT_ROOT, "README.md")]
    d = os.path.join(GIT_ROOT, "docs")
    for name in sorted(os.listdir(d)):
        if name.endswith(".md"):
            files.append(os.path.join(d, name))          # docs/handoff and docs/archive are history
    return files


def selftest() -> int:
    """Every defect must be found, and a clean doc must be silent — plus a control that the grader
    could have failed (the same fixture text with the defect put back)."""
    import check_doc_flags as cdf
    import shutil

    fails = []

    def ck(name, cond, extra=""):
        print(("  ok    " if cond else "  FAIL  ") + name + (f"  <{extra}>" if extra else ""))
        if not cond:
            fails.append(name)

    print("selftest: scripts/check_doc_flags.py")
    d = tempfile.mkdtemp(prefix="docflags-")
    try:
        good = os.path.join(d, "good.py")
        with open(good, "w") as fh:
            fh.write("#!/usr/bin/env python3\nimport argparse\np = argparse.ArgumentParser()\n"
                     "p.add_argument('--tail', type=int)\nprint(p.parse_args().__dict__)\n")
        os.chmod(good, 0o755)

        def grader(tool_abs):
            """Grade a fixture doc whose tool path is absolute-ish by monkeypatching GIT_ROOT."""
            import check_doc_flags as cdf
            old = cdf.GIT_ROOT
            cdf.GIT_ROOT = d
            try:
                return cdf.check_file(os.path.join(d, "doc.md"))
            finally:
                cdf.GIT_ROOT = old

        def write_doc(body):
            with open(os.path.join(d, "doc.md"), "w") as fh:
                fh.write(body)

        kinds = lambda body: [p[0] for p in grader(write_doc(body))]  # noqa: E731

        ck("CONTROL: a correct command in a doc produces no problem at all",
           kinds("Run `./good.py --tail 3` now") == [], str(kinds("Run `./good.py --tail 3` now")))
        ck("an invented flag is reported (this is the whole audit finding in one line)",
           "unknown-flag" in kinds("Run `./good.py --nope 3`"))
        nonexec = os.path.join(d, "lib.py")
        with open(nonexec, "w") as fh:
            fh.write("#!/usr/bin/env python3\nprint(1)\n")
        os.chmod(nonexec, 0o644)
        ck("a doc that runs a non-executable file as `./x` is reported (Permission denied on a "
           "clean checkout)", "no-exec-bit" in kinds("Run `./lib.py`"))
        ck("...and the SAME file invoked through the interpreter is not a defect",
           "no-exec-bit" not in kinds("Run `python3 lib.py`"))
        ck("a path that does not exist is reported rather than the doc being believed",
           "missing-tool" in kinds("Run `./no-such-tool.py --tail 3`"))
        with open(os.path.join(d, "fails.py"), "w") as fh:
            fh.write("#!/usr/bin/env python3\nimport sys\nsys.exit(3)\n")
        os.chmod(os.path.join(d, "fails.py"), 0o755)
        ck("a tool whose --help exits non-zero is named as UNGRADEABLE, not passed silently",
           "no-help" in kinds("Run `./fails.py --tail 3`"))
        ck("a documented tool with no documented flag is graded through its own --help (the fixture "
           "offers --tail and the doc uses it)", kinds("Run `./good.py --tail 3`") == [])
        harness = os.path.join(d, "harness.sh")
        with open(harness, "w") as fh:
            fh.write("#!/usr/bin/env bash\n# no arg parsing: any invocation RUNS it\nsleep 0\n")
        os.chmod(harness, 0o755)
        cdf.HARNESS.add("harness.sh")
        try:
            got = kinds("Run `./harness.sh --whatever x`")
        finally:
            cdf.HARNESS.discard("harness.sh")
        ck("a HARNESS is named ungradable rather than EXECUTED (--help on scripts/selftest.sh ran the "
           "whole suite: 5 minutes of gate inside the gate)", got == ["no-help"], str(got))
        ck("and the real repo's harness is in that set", "scripts/selftest.sh" in HARNESS)
        ck("fenced blocks are graded too, not only inline spans",
           "unknown-flag" in kinds("```bash\n./good.py --bogus\n```"))
        ck("a file that cannot be read is reported (never an empty problem list)",
           check_file(os.path.join(d, "nope.md"))[0][0] == "unreadable")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print(("  FAIL  " + str(len(fails)) + " assertion(s) failed") if fails
          else "  all selftest assertions passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--doc", help="grade one markdown file instead of README.md + docs/*.md")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    problems, spans, ungradable = [], 0, set()
    help_cache = {}
    for path in markdown_files(a.doc):
        found = check_file(path, cache=help_cache)
        with open(path) as fh:
            spans += len(code_spans(fh.read()))
        problems.extend(found)
        ungradable.update(p[3].split(":")[0] for p in found if p[0] == "no-help")
    for kind, doc, cmd, detail in problems:
        if kind == "no-help":
            continue
        print(f"  {kind:<13} {doc}: {detail}\n                 in: {cmd}")
    if ungradable:
        print(f"  no --help (flags unverifiable, named so the count cannot grow silently): "
              f"{len(ungradable)} tool(s): {', '.join(sorted(ungradable))}")
    hard = [p for p in problems if p[0] != "no-help"]
    print(f"check_doc_flags: {len(markdown_files(a.doc))} file(s), {spans} code span(s), "
          f"{len(hard)} problem(s), {len(ungradable)} tool(s) ungradable")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
