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
#
# That set was ONE literal name, and the comment above recorded the hazard having been found once
# already, so the same class came back through the door the comment did not close. Every file under
# scripts/selftest.d/ is the same shape of script: a gate phase that reads `$1` as an interpreter and
# FALLS BACK SILENTLY when `$1` is not executable. So the documented
# `bash scripts/selftest.d/womtool.sh python3` (docs/plan-launch-any-module.md:188) handed `--help` to
# a phase, the phase could not exec it as an interpreter, and carried on with its own default: a full
# phase run inside `make check`. The 25 s timeout does not bound that either — after the kill,
# communicate() is called again with NO timeout while the phase's python/java grandchildren still hold
# the capture pipe's write end. So the directory is a harness by shape, not a name to remember.
HARNESS = {"scripts/selftest.sh"}
HARNESS_DIRS = ("scripts/selftest.d/",)


def is_harness(rel: str) -> bool:
    """True when `--help` would RUN the tool rather than describe it. Graded by shape, not by name."""
    return rel in HARNESS or rel.startswith(HARNESS_DIRS)


# How many tools this repo cannot grade, and the ceiling the gate fails above. See the verdict in
# main() for why a ceiling rather than zero, and what it costs to move it.
#
# Measured on this box at 6aa94af with `scripts/check_doc_flags.py` run over README.md + docs/*.md:
# **11 tools ungradable** — the 9 gate phases in scripts/selftest.d/, scripts/selftest.sh, and
# scripts/check_artifacts.py (`--help` is a real run there, exit 2). Before the harness-by-shape fix
# the same run printed 4, because the other 9 phases had been EXECUTED as `--help` probes and their
# tallies mistaken for usage text.
UNGRADABLE_CEILING = 11


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
    if is_harness(tool_rel):
        return "", ("is a harness, not a CLI: `--help` would run it rather than describe it "
                    "(scripts/selftest.sh and every scripts/selftest.d/ phase take $1 as an "
                    "interpreter and fall back when it is not executable)")
    argv = [sys.executable, abs_tool] if tool_rel.endswith(".py") else (
        ["bash", abs_tool] if tool_rel.endswith(".sh") else [abs_tool])
    try:
        # stdin=DEVNULL: a tool that reads stdin sees EOF at once instead of the gate's own stdin, on
        # which `make test` may be waiting. A probe that blocks there is not bounded by timeout=25.
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
    # One --help per tool for the WHOLE run, not per file: the graded files otherwise re-spawn an
    # interpreter-starting subprocess for every tool each of them names, which is minutes in the gate
    # and is why this checker can live in `make test` at all. So the CALLER's cache is kept, and the
    # hoisted cache is what main() passes. The line that used to sit two lines below this one said
    # "hoisted" and then did `cache = {}`, re-emptying it on every file — a comment describing code
    # that was not there. Its positive control counts the probes, not the wall clock.
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
            # THE TRACKED MODE is what a clean checkout gets. `os.access(..., X_OK)` grades this
            # machine's working tree: with core.fileMode=false a documented `./terra/batch_peek.py`
            # tracked 100644 passes here and dies on a clone — the exact defect this file's header
            # describes. An UNtracked file has no mode in git, so the local bit is the only evidence.
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

        # ---- the --help CACHE is the caller's, not the file's -------------------------------------
        # The bug was a `cache = {}` two lines after the caller's cache was honoured, under a comment
        # claiming the cache was hoisted. The control counts PROBES, because a wall clock is not
        # evidence: grading the same tool from two docs must start ONE interpreter, not two.
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

        # ---- a documented GATE PHASE is named ungradable and never executed ------------------------
        # Every scripts/selftest.d/ phase takes $1 as an interpreter and falls back when it is not
        # executable, so `bash scripts/selftest.d/womtool.sh python3` (docs/plan-launch-any-module.md)
        # handed `--help` to a phase and RAN THE PHASE inside `make check`. The control grades a
        # fixture span against the REAL tree and requires: one `no-help` problem, and not one probe.
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

        # ---- an ungradable tool cannot pass silently: the ceiling is the verdict -------------------
        # main() is the verdict, so the control runs main(). The fixture doc names a tool whose --help
        # exits 3, which is UNGRADEABLE rather than clean; the ceiling decides, and both arms are
        # asserted so the red cannot be about anything except the ceiling line.
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
        # ---- the ceiling is priced against the tree, not against the literal in this file ----------
        # This leg used to read `cdf.UNGRADABLE_CEILING == 11`: a constant compared with a copy of
        # itself. It could notice the CONSTANT being edited and could not notice the COUNT it exists to
        # price moving, so the sentence above it ("the count this run prints") described a check that
        # never ran anything. Measured, the count is 11 today (`./scripts/check_doc_flags.py` prints
        # `11 tool(s) ungradable (ceiling 11)`), and ten of those eleven are harnesses — a status this
        # file decides from `HARNESS`/`HARNESS_DIRS` without executing anything. So price those here,
        # off the real doc set, with `run` a stub that answers every other tool's --help: the scan still
        # boots no gate phase (that is the whole point of `HARNESS`), and the number below comes from
        # the tree rather than from the constant. The eleventh name, `scripts/check_artifacts.py`, is
        # ungradable because its REAL --help exits 2 — only a real run can discover that, and the gate
        # leg that runs the whole scan is where that one is graded (scripts/selftest.sh, the 60 s
        # budget leg). What this leg can now catch: the constant lowered under the count the repo
        # itself produces, and a count of nothing — `count <= ceiling` over an empty set is the vacuous
        # pass this file's header refuses. A PARTIAL loss in the `HARNESS` list belongs to the two legs
        # above (a phase that stops being a harness gets PROBED, and the shape is graded by name), and
        # raising the constant is graded by the gate leg that prints the number — this leg's job is the
        # relation between the constant and what the tree measures.
        def probe_help(argv, **kw):
            """Every tool but a harness answers --help, so counting the set costs no subprocess."""
            class _Answer:
                returncode, stdout, stderr = 0, "usage: stub\n", ""
            return _Answer()

        real_scan, real_cache = [], {}
        for real_doc in cdf.markdown_files(None):
            real_scan += cdf.check_file(real_doc, run=probe_help, cache=real_cache)
        # The same derivation main() prints its count from, so the leg and the summary line cannot
        # disagree about what "a tool this checker cannot grade" means.
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
        # os.access(X_OK) graded the working tree: with core.fileMode=false a documented
        # `./terra/batch_peek.py` tracked 100644 passed here and died on a clean clone — the defect
        # this file's header describes and `tracked_mode` existed to catch but never did, with zero
        # call sites. Both directions, in a throwaway git repo, because only `ls-files -s` knows what
        # a clone gets.
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
        # Now make the working tree LIE in both directions, without re-staging.
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
    # An ungradable tool is NOT a pass — this file's header rule is that a checker which cannot run
    # passes by default, and that is the class this file exists to catch — but the COUNT of them is a
    # property of the repo's tools, so it is graded against UNGRADABLE_CEILING (where the measurement
    # and the way to lower it are recorded) rather than against zero. The names print every run.
    if len(ungradable) > UNGRADABLE_CEILING:
        print(f"  FAIL  {len(ungradable)} tool(s) ungradable exceeds the declared ceiling "
              f"UNGRADABLE_CEILING = {UNGRADABLE_CEILING}: the names above lost their `--help`, so "
              f"their documented flags are no longer graded by anything. Scope of this run: {scope}.")
        return 1
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
