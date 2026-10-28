#!/usr/bin/env python3
"""check_doc_flags.py — every flag the docs tell you to type is a flag the tool actually accepts.

Grades the commands in README.md and every docs/*.md:

    tool        a `*.py` / `*.sh` path in a code span or fence must exist in the repo
    exec bit    if the doc runs it as `./path`, the file must be tracked executable (mode 100755)
    flags       every `--long-flag` in that command must appear in `tool --help`
    task names  `--block X` / `--task X` are only checked for existence (they name upstream data)

Tools run under this process's interpreter. A tool whose --help cannot be obtained is reported as
ungradable, never skipped silently.

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


# Scripts whose `--help` would run them instead of describing them: scripts/selftest.sh and every
# gate phase in scripts/selftest.d/ (which take $1 as an interpreter and fall back when it isn't
# one). They are reported as ungradable and never executed.
HARNESS = {"scripts/selftest.sh"}
HARNESS_DIRS = ("scripts/selftest.d/",)


def is_harness(rel: str) -> bool:
    """True when `--help` would RUN the tool rather than describe it. Graded by shape, not by name."""
    return rel in HARNESS or rel.startswith(HARNESS_DIRS)


# How many documented tools may be ungradable before the gate fails. Currently the gate phases,
# scripts/selftest.sh and scripts/check_artifacts.py (whose --help is a real run). The names print
# every run. Lower it by giving one of them a usage path.
UNGRADABLE_CEILING = 12


def command_tool(span: str) -> str:
    """The tool a command INVOKES, or "" when the span is not invoking a repo tool.

    Only the command position counts: `--probe scripts/x.sh` names an argument, and
    `<ref>:src/x.sh` is a git revision path.
    """
    toks = span.strip().split()
    if not toks:
        return ""
    first = toks[0].strip("'")
    tool = toks[1] if (first in INTERPRETERS or first.endswith("/python") or first.endswith("/python3")) \
        and len(toks) > 1 else first
    if not re.search(r"\.(py|sh)$", tool):
        return ""
    # Globs and absolute/variable paths are not repo tools.
    if "*" in tool or "?" in tool or tool.startswith("/") or "$" in tool:
        return ""
    return tool


def resolve(rel: str) -> str:
    """Repo-relative path of a tool named in a doc — docs often write `wdl_gate.sh`, not the path.

    Exact path first, then a unique tracked basename match. An ambiguous basename is left
    unresolved and reported.
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
    if is_harness(tool_rel):
        return "", ("is a harness, not a CLI: `--help` would run it rather than describe it "
                    "(scripts/selftest.sh and every scripts/selftest.d/ phase take $1 as an "
                    "interpreter and fall back when it is not executable)")
    argv = [sys.executable, abs_tool] if tool_rel.endswith(".py") else (
        ["bash", abs_tool] if tool_rel.endswith(".sh") else [abs_tool])
    try:
        # stdin=DEVNULL so a tool that reads stdin can't block on the gate's own stdin.
        r = run(list(argv) + ["--help"], cwd=GIT_ROOT, capture_output=True, text=True, timeout=25,
                stdin=subprocess.DEVNULL)
    except Exception as exc:
        return "", f"{type(exc).__name__}: {str(exc)[:120]}"
    if r.returncode != 0:
        return "", f"--help exited {r.returncode}: {(r.stdout or r.stderr or '')[:200].strip()}"
    return (r.stdout or "") + (r.stderr or ""), ""


def check_file(path: str, run=subprocess.run, cache=None) -> list:
    """Problems as (kind, doc, command, detail). Never silently drops a file it cannot grade."""
    problems = []
    # One --help per tool for the whole run: the caller's cache is shared across files.
    cache = {} if cache is None else cache
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError as exc:
        return [("unreadable", path, "", str(exc))]
    rel_doc = os.path.relpath(path, GIT_ROOT)
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
        if tool.startswith("./"):
            # Use the tracked mode, which is what a clone gets; the working tree's bit can differ
            # (e.g. core.fileMode=false). Untracked files fall back to the local bit.
            mode = tracked_mode(rel)
            if mode:
                runnable = mode.endswith("755") or mode.endswith("754") or mode.endswith("750")
                why = f"{rel} is tracked mode {mode}"
            else:
                runnable = os.access(os.path.join(GIT_ROOT, rel), os.X_OK)
                why = f"{rel} is not tracked and has no exec bit here"
            if not runnable:
                problems.append(("no-exec-bit", rel_doc, span.strip()[:100],
                                 f"{why}, so `./{rel}` cannot run as written "
                                 "(git update-index --chmod=+x <path> is the cure)"))
        if rel not in cache:
            cache[rel] = help_text(rel, run=run)
        htext, herr = cache[rel]
        if herr:
            # Ungradable, not passing: reported and counted against the ceiling.
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

        # ---- the --help cache is shared across files -----------------------------------------------
        # Counts probes: the same tool named by two docs must start one interpreter.
        probes = []

        def probe_run(argv, **kw):
            probes.append(list(argv))
            return subprocess.CompletedProcess(list(argv), 0, "usage: --tail N\n", "")

        def grade_with_probe_counter(bodies):
            """Grade one doc per body, all through ONE cache, counting every --help actually run."""
            import check_doc_flags as cdf
            old = cdf.GIT_ROOT
            cdf.GIT_ROOT = d
            try:
                seen = {}
                for i, body in enumerate(bodies):
                    with open(os.path.join(d, "doc%d.md" % i), "w") as fh:
                        fh.write(body)
                    seen[i] = cdf.check_file(os.path.join(d, "doc%d.md" % i), run=probe_run,
                                             cache=seen_cache)
                return seen
            finally:
                cdf.GIT_ROOT = old

        seen_cache = {}
        grade_with_probe_counter(["Run `./good.py --tail 3`", "And again: `./good.py --tail 3`"])
        ck("CONTROL: the same tool named by TWO docs costs ONE --help probe, not two (the hoisted "
           "cache survived the file loop; a `cache = {}` inside it re-spawned every probe)",
           len(probes) == 1, f"{len(probes)} probe(s) for 2 docs")
        probes[:] = []
        seen_cache.clear()
        grade_with_probe_counter(["Run `./good.py --tail 3`", "Run `./fails.py --tail 3`"])
        ck("and two DIFFERENT tools still cost two probes — the cache is keyed by tool, not a switch "
           "that turns probing off",
           len(probes) == 2, f"{len(probes)} probe(s)")

        # ---- a documented gate phase is named ungradable and never executed ------------------------
        # Graded against the real tree: one `no-help` problem and zero probes.
        def grade_real_tree(body):
            """Grade fixture prose against the REAL repo tree: GIT_ROOT is unpatched here, and the
            phase files being graded are real files that only the real tree has."""
            probes[:] = []
            with open(os.path.join(d, "phase-doc.md"), "w") as fh:
                fh.write(body)
            return cdf.check_file(os.path.join(d, "phase-doc.md"), run=probe_run)

        got_phase = grade_real_tree("Run `bash scripts/selftest.d/womtool.sh python3` now")
        ck("a documented scripts/selftest.d/ PHASE is named not-graded and NEVER PROBED: `--help` on a "
           "phase runs the phase, and the 25 s timeout does not bound it because the killed phase's "
           "grandchildren hold the capture pipe open",
           [p[0] for p in got_phase] == ["no-help"] and not probes,
           f"{[p[0] for p in got_phase]} / {len(probes)} probe(s)")
        ck("...and the reason it prints says harness, so a reader cannot mistake it for a broken tool",
           bool(got_phase) and "harness" in got_phase[0][3], got_phase[0][3][:70] if got_phase else "")
        ck("harness-by-shape covers the directory, not one remembered name",
           cdf.is_harness("scripts/selftest.d/any-new-phase.sh") and
           not cdf.is_harness("checks/wdl_gate.sh"))

        # ---- the ungradable ceiling decides the verdict -----------------------------------------
        # Runs main() on a doc naming a tool whose --help exits 3, at two ceilings.
        import contextlib
        import io

        def run_main(body, ceiling):
            import check_doc_flags as cdf
            old_root, old_argv, old_ceil = cdf.GIT_ROOT, list(sys.argv), cdf.UNGRADABLE_CEILING
            cdf.GIT_ROOT = d
            cdf.UNGRADABLE_CEILING = ceiling
            buf = io.StringIO()
            try:
                with open(os.path.join(d, "doc.md"), "w") as fh:
                    fh.write(body)
                sys.argv = ["check_doc_flags", "--doc", os.path.join(d, "doc.md")]
                with contextlib.redirect_stdout(buf):
                    rc = cdf.main()
            finally:
                cdf.GIT_ROOT, sys.argv, cdf.UNGRADABLE_CEILING = old_root, old_argv, old_ceil
            return rc, buf.getvalue()

        leaky = "Run `./fails.py --tail 3`"
        rc_red, out_red = run_main(leaky, 0)
        rc_green, _ = run_main(leaky, 1)
        ck("a doc naming a tool with no obtainable --help is RED when it exceeds the declared ceiling, "
           "and the ceiling line is printed as the reason (a checker that cannot run must not pass by "
           "default — this file's own header rule)",
           rc_red == 1 and "UNGRADABLE_CEILING = 0" in out_red and "1 tool(s) ungradable" in out_red,
           f"rc={rc_red}")
        ck("CONTROL for that red: the SAME doc is green one ceiling point higher, so the verdict is "
           "the ceiling and not the mere presence of an ungradable tool",
           rc_green == 0, f"rc={rc_green}")
        # ---- the ceiling is checked against the real doc set ---------------------------------------
        # Counts harness tools in README.md + docs/*.md with a stub `run`, so nothing is executed.
        # The count must be nonzero and within the ceiling. Tools only discoverable by a real --help
        # run (e.g. check_artifacts.py) are graded by the full-scan leg in scripts/selftest.sh.
        def probe_help(argv, **kw):
            """Every tool but a harness answers --help, so counting the set costs no subprocess."""
            class _Answer:
                returncode, stdout, stderr = 0, "usage: stub\n", ""
            return _Answer()

        real_scan, real_cache = [], {}
        for real_doc in cdf.markdown_files(None):
            real_scan += cdf.check_file(real_doc, run=probe_help, cache=real_cache)
        # Same derivation main() uses for its count.
        real_ungrad = sorted({p[3].split(":")[0] for p in real_scan if p[0] == "no-help"})
        ck("the declared ceiling is priced against the REAL doc set: the documented tools this checker "
           "cannot grade, counted off README.md + docs/*.md without a single subprocess, sit inside it "
           "— and that count is not zero, because an inequality over an empty set is the pass this "
           "file's own header refuses (raise the constant with the measurement beside it, never to "
           "silence a red)",
           isinstance(cdf.UNGRADABLE_CEILING, int) and 0 < len(real_ungrad) <= cdf.UNGRADABLE_CEILING,
           f"{len(real_ungrad)} ungradable tool(s) counted off the tree, "
           f"ceiling={cdf.UNGRADABLE_CEILING}")

        # ---- the exec-bit grade reads git's mode, not this checkout's bit --------------------------
        # In a throwaway repo, with the working-tree bit set opposite to the tracked mode both ways.
        repo = os.path.join(d, "gitrepo")
        os.makedirs(repo)

        def gg(*a):
            subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c",
                            "user.email=t@example.invalid", "-c", "commit.gpgsign=false", *a],
                           capture_output=True, check=True)

        gg("init", "-q")
        for nm, mode in (("ships.py", 0o755), ("doc_only.py", 0o644)):
            with open(os.path.join(repo, nm), "w") as fh:
                fh.write("#!/usr/bin/env python3\nimport argparse\np = argparse.ArgumentParser()\n"
                         "p.add_argument('--tail', type=int)\np.parse_args()\n")
            os.chmod(os.path.join(repo, nm), mode)
        gg("add", ".")
        # Flip the working-tree bits without re-staging.
        os.chmod(os.path.join(repo, "doc_only.py"), 0o755)   # bit here, mode 100644 in git
        os.chmod(os.path.join(repo, "ships.py"), 0o644)      # mode 100755 in git, no bit here

        def grade_repo(body):
            import check_doc_flags as cdf
            old = cdf.GIT_ROOT
            cdf.GIT_ROOT = repo
            try:
                with open(os.path.join(repo, "doc.md"), "w") as fh:
                    fh.write(body)
                return [p[0] for p in cdf.check_file(os.path.join(repo, "doc.md"), run=probe_run)]
            finally:
                cdf.GIT_ROOT = old

        ck("a file whose TRACKED mode is not 755 is reported even when this working tree has the bit "
           "(the clean checkout is what a doc promises)",
           "no-exec-bit" in grade_repo("Run `./doc_only.py --tail 3`"))
        ck("CONTROL: a file tracked 100755 is silent even when this working tree LACKS the bit — the "
           "core.fileMode=false machine that used to hide the finding, seen from the other side",
           "no-exec-bit" not in grade_repo("Run `./ships.py --tail 3`"),
           str(grade_repo("Run `./ships.py --tail 3`")))
        def tracked_in_repo(rel):
            """tracked_mode() reads the mode out of GIT_ROOT, so grade it with GIT_ROOT on the fixture."""
            import check_doc_flags as cdf
            old = cdf.GIT_ROOT
            cdf.GIT_ROOT = repo
            try:
                return cdf.tracked_mode(rel)
            finally:
                cdf.GIT_ROOT = old

        ck("tracked_mode has a call site now (it shipped with zero, which is how the header bug "
           "survived it having been written)",
           tracked_in_repo("ships.py") == "100755" and tracked_in_repo("doc_only.py") == "100644",
           f"ships.py={tracked_in_repo('ships.py')} doc_only.py={tracked_in_repo('doc_only.py')}")

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
    scope = f"--doc {a.doc}" if a.doc else "README.md + docs/*.md"
    print(f"check_doc_flags: {len(markdown_files(a.doc))} file(s), {spans} code span(s), "
          f"{len(hard)} problem(s), {len(ungradable)} tool(s) ungradable "
          f"(ceiling {UNGRADABLE_CEILING}; scope: {scope})")
    # Ungradable tools are graded against UNGRADABLE_CEILING rather than zero; the names print
    # every run.
    if len(ungradable) > UNGRADABLE_CEILING:
        print(f"  FAIL  {len(ungradable)} tool(s) ungradable exceeds the declared ceiling "
              f"UNGRADABLE_CEILING = {UNGRADABLE_CEILING}: the names above lost their `--help`, so "
              f"their documented flags are no longer graded by anything. Scope of this run: {scope}.")
        return 1
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
