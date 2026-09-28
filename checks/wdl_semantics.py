#!/usr/bin/env python3
"""wdl_semantics.py — does this WDL actually RUN, or does it only typecheck?

Why this exists
---------------
`checks/wdl_gate.sh` answers one question well: are the *call bindings* right (IncompleteCall,
stale inputs). Three gatk-sv branches each fixed a defect that the gate — and `miniwdl check`, and
`womtool validate` — reported as clean. **Measured here, on a real tree with one `)` deleted from a
`command <<<` block: `miniwdl check` exits 0 with output byte-identical to the clean tree.** The
defects lived one layer down, in what the workflow *means* and in what the rendered shell *is*:

  WRITE-SCOPE   a workflow-scope `write_lines`/`write_tsv`/`write_json`/`write_map`. Cromwell on
                PAPIv2 cannot materialize a workflow-level File decl: the submission dies in
                seconds, having booted nothing and spent nothing, which is the only mercy available.
                One branch spent exactly that.

  DEFINED-ONLY  a `File` input whose ONLY appearance in the command is inside `defined(...)`.
                Cromwell still localizes the file — `defined()` is answered from the localization
                table, not from the script — so a two-genome CRAM pair you only *test* for is two
                whole CRAMs copied to a shard that never opens them. Measured cost on a real run:
                $6.47 and two attempts, and the same trap is still standing on five more inputs
                that nobody has gotten to yet.

  PIPEFAIL      a pipe whose consumer stops reading early (`head`, `grep -m`, `sed ... q`,
                `awk ... exit`) inside a task that has set `pipefail`. The producer then dies of
                SIGPIPE and rc is **141**. It passes on a small input and fails on a big one, so
                the branch that found it spent 11h47m discovering it, and it exists on module
                paths that branch never ran. macOS ships bash 3.2, which has no `pipefail`, so this
                class cannot be reproduced on the laptop at all — reading the tree is the cheap
                half, and this rule is that half.

  SHELL-SYNTAX  render the task's command and `bash -n` it. Both WDL validators accept an
                unbalanced paren inside a command block because to them it is a string. Nothing
                else in this repo looks at the shell a task will actually run.

  LOAD-FAILURES counted as a rule because it is the failure mode this repo names: a scan that
                could not parse a file adds nothing to any count, so "no findings" and "no files
                read" print the same thing. A nonzero value here means every count beside it is a
                partial answer.

These are **counts to diff against a base ref, not oracles.** gatk-sv as it stands already carries
DEFINED-ONLY findings on inputs its owners left deliberately (they are a known shape, not a bug),
and a renderer that stubs placeholders will always mis-render something somewhere — so the value is
the delta, exactly like `wdl_gate.sh`'s IncompleteCall column. `--strict` exists, but reaching for
it before you have a baseline turns a diff tool into a wall of inherited findings.

The renderer, and why it does not use a regex
---------------------------------------------
`miniwdl` already knows where the placeholders are: `task.command.parts` is the command as an
ordered list of literal strings and `Placeholder` expressions. Rendering is therefore "join the
literals, stub the placeholders" — no brace counting, no quote handling, and no guessing about
`~{true='(' false=')'}`, which a regex substitution mangles into a syntax error that is nobody's bug.

Stubbing is the one place the tool must lie, and the lie is the classic false-positive source. The
stub is `X`, a word, and the honest consequence is measured: **2 of 315 command blocks at gatk-sv
`main` fail `bash -n` that bash would not object to at run time.** Both are in `TasksMakeCohortVcf`,
where a placeholder expands to a leading pipe (`~{if do_filter then "| " + cmd else ""}`), so
substituting a word puts two words where a redirect expects one. An earlier attempt at removing them
checked a second rendering with an *empty* stub and required both to fail: measured across the tree
that discards 60 findings, every one of which came from an empty stub deleting the operand of
`done <`, `if`, or `>` — it removes the two artifacts by removing the check. So the artifact rows
stay, which is precisely why this rule is a **count to diff against a base ref**: `SHELL-SYNTAX=2` on
your ref versus `2` on the baseline is nothing changed, `3` is your bug, and `0` means you found
something nobody had to see before. A deleted parenthesis fails under this render too, because
structure survives stubbing where word count does not.

Usage
-----
    checks/wdl_semantics.py --dir DIR                 # scan a tree, print counts + findings
    checks/wdl_semantics.py --dir DIR --summary-only   # one machine-readable line (for the gate)
    checks/wdl_semantics.py --dir DIR --rule pipefail -v
    checks/wdl_semantics.py --dir DIR --block CondenseReadCounts   # print the rendered command
    checks/wdl_semantics.py --dir DIR --json PATH     # machine-readable artifact
    checks/wdl_semantics.py --selftest                # fixtures: each rule must fire, controls must not

DIR is a materialized WDL tree (`scripts/fetch_wdl.py --dest`), or a gatk-sv clone, or any directory
holding `*.wdl`; a `wdl/` subdirectory is preferred when present. `--repo` is accepted as a synonym
for `--dir` because the sibling checkers use that name.

Needs miniwdl (make setup, or pip install miniwdl). No data, no docker, no network.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

try:
    import WDL
except ImportError:                                    # pragma: no cover - the friendly path
    WDL = None

WRITE_FNS = ("write_lines", "write_tsv", "write_json", "write_map")

# The rules, in report order. A rule name is a stable CLI token (--rule pipefail), so renaming one
# is a breaking change to the gate's parse line, not a code edit.
RULES = ("write_scope", "defined_only", "pipefail", "shell_syntax")
COLUMNS = {"write_scope": "WRITE-SCOPE", "defined_only": "DEFINED-ONLY",
           "pipefail": "PIPEFAIL", "shell_syntax": "SHELL-SYNTAX", "load_failures": "LOAD-FAILURES"}

# --- rule: pipefail ---------------------------------------------------------------------------------
# `set -euo pipefail`, `set -o pipefail`, `set -o pipefail -e`: all of them arm the trap, and the
# order within the line does not matter to bash. What DOES matter is order in the block: a pipe on a
# line *before* pipefail is set is not a finding, and gatk-sv has tasks that read a header line
# first and set pipefail a few lines later (`MakeBincovMatrix.wdl`'s `SetBins`). Asking "does this
# task contain pipefail" is therefore wrong; asking "was it armed by the time this pipe ran" is right.
# `set -o pipefail`, `set -euo pipefail`, `set -eu -o pipefail`: bash accepts the flag alone, fused
# into `-euo`, or after other flags, so matching the literal `-o pipefail` misses the fused form —
# which is the form gatk-sv writes in most tasks. Anything on a `set` line naming pipefail arms it.
PIPEFAIL_SET = re.compile(r'\bset\b[^\n]*pipefail')

# Early-exiting consumers: the reader stops, the writer gets SIGPIPE.
# `head` always (even `head -c 1`, which is how gatk-sv peeks at a gzipped header), `grep -m N`
# (stops at N matches), `sed ... q` (quits the stream), and an awk program with a bare `exit`.
# Deliberately NOT here: `wc -l`, `sort`, `tail`, `md5sum` and friends, which read to EOF and so
# never close the writer's pipe early. A `tail -f`-style reader is not in this tree.
# Each is tested against the segment's COMMAND WORD (see _command_word), never against the line,
# or `fgrep "#" > head.txt` becomes a finding because the output file is named head.txt.
CONSUMER_SED_Q = re.compile(r'\b[0-9]+q\b|;\s*q\b|\bq\s*[\'"]\s*$')
AWK_PROG = re.compile(r"awk\s+(-F\S+\s+)?(['\"])((?:\\\\.|[^\\\\])*?)\2")
END_BLOCK = re.compile(r'END\s*\{[^}]*\}')
# `|| true`, `|| :`, `|| echo ...`, `|| exit 0`: whatever the shape, an explicit swallow. Only the
# SAME LINE counts. The first prototype looked ahead 12 lines, which is a crude stand-in for "the
# same `$( ... )`" and it mislabelled a real pair at gatk-sv `main` as guarded — a guard credited to
# a site it never guarded is worse than no guard, because it removes the finding without fixing it.
GUARD_SAME_LINE = re.compile(r'\|\|\s*(true|:|echo|exit\s+0|grep|awk|head)')
COMMENT = re.compile(r'^\s*#')


def _awk_exits_early(prog: str) -> bool:
    """True if an awk program can call `exit` while the stream is still open.

    `awk 'END{exit 1}'` reads everything: by the time `exit` runs the input is closed and there is
    no SIGPIPE to earn. Three of the ten residual hits in the first prototype were that shape, or
    an `exit` reached through an unrelated `if/else`, so the END block is stripped before looking.
    """
    stripped = END_BLOCK.sub('', prog)
    return re.search(r'(?<!\w)exit(?!\s*\()\s*[;}\d]?|\bexit\b\s*\d', stripped) is not None


def _pipes(line: str) -> list:
    """Split a line on `|`, ignoring pipes inside quotes.

    Parentheses are deliberately NOT tracked. The first version treated `$( ... )` as opaque, and
    that hid most of the real sites, because the shape gatk-sv writes is `x=$(zcat f | head -n1)` —
    a command substitution is where the short-reader pipes live, and a pipe inside one has exactly
    the same SIGPIPE semantics as one at top level. Only quoting hides a pipe: `echo "a|b"` is one
    field. (`case` pattern alternations do split, and are harmless: a pattern is not a command name,
    so nothing downstream matches it.)
    """
    out, buf, quote = [], [], ''
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == '\\' and quote != "'":
                i += 2
                continue
            if ch == quote:
                quote = ''
        elif ch in "\"'":
            quote = ch
        elif ch == '|' and line[i:i + 2] != '||' and (i == 0 or line[i - 1] != '|'):
            out.append(''.join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    out.append(''.join(buf))
    return out if len(out) > 1 else []


def _command_word(segment: str) -> str:
    """The command a pipe segment runs, not the words that appear inside it.

    Without this, `zcat f | fgrep "#" > head.txt` is a finding because the *output file* is called
    `head.txt`. Only a segment's first word is a command (after group openers and `VAR=x ` env
    prefixes), so that is the only position anything below looks at.
    """
    s = re.sub(r'^[\s!{(\$`]+', '', segment)
    s = re.sub(r'^(?:\w+=\S+\s+)+', '', s)
    words = s.split()
    return words[0] if words else ''


def _early_consumer(segment: str):
    """Name the early-exiting consumer in a pipe's reader side, or None."""
    if COMMENT.match(segment):
        return None
    cmd = _command_word(segment)
    if cmd == 'head':
        return 'head'                      # head -n 1, head -c 1: reads a prefix, closes the pipe
    if cmd == 'grep' and re.search(r'(^|\s)(-m\s*\d+|--max-count(=|\s)\d+)', segment):
        return 'grep -m'
    if cmd == 'sed' and CONSUMER_SED_Q.search(segment):
        return 'sed q'
    if cmd == 'awk':
        for m in AWK_PROG.finditer(segment):
            if _awk_exits_early(m.group(3)):
                return 'awk exit'
    return None


def rule_pipefail(docs) -> list:
    """(file, task, lineno, line, 'UNGUARDED'|'GUARDED') for every armed pipe with a short reader."""
    findings = []
    for path, doc in docs:
        for task in doc.tasks:
            armed = False
            for n, line in enumerate(render(task, 'X').splitlines(), 1):
                if COMMENT.match(line):
                    continue                           # a commented `set -o pipefail` arms nothing
                if PIPEFAIL_SET.search(line):
                    armed = True                       # order-aware: armed only from here on
                    continue
                if not armed or '|' not in line:
                    continue
                segs = _pipes(line)
                if not segs:
                    continue
                hit = next((c for seg in segs[1:] for c in [_early_consumer(seg)] if c), None)
                if not hit:
                    continue
                guard = 'GUARDED' if GUARD_SAME_LINE.search(line) else 'UNGUARDED'
                findings.append((path.name, task.name, n, hit, line.strip()[:110], guard))
    return findings


# --- rule: write_* at workflow scope ------------------------------------------------------------------
def rule_write_scope(docs) -> list:
    """Workflow-body decls computed with write_*: they have no task to materialize them in."""
    findings = []
    for path, doc in docs:
        wf = doc.workflow
        if wf is None:
            continue
        for node in _walk_decls(wf):
            expr = getattr(node, 'expr', None)
            if isinstance(expr, WDL.Expr.Apply) and str(getattr(expr, 'function_name', '')) in WRITE_FNS:
                findings.append((path.name, wf.name, node.name, str(expr.function_name)))
    return findings


def _walk_decls(node):
    """Every Decl under a workflow body, including inside `if`/`scatter` blocks.

    `doc.workflow.body` is only the top level. A `write_lines` inside a scatter is no more
    materializable than one at the top — the enclosing scope is still the workflow — so a scan of
    the top level alone would certify the same defect as safe depending on indentation.
    """
    for child in node.children:
        if isinstance(child, WDL.Decl):
            yield child
        else:
            yield from _walk_decls(child)


# --- rule: a File input only ever tested with defined() ----------------------------------------------
def rule_defined_only(docs) -> list:
    """(file, task, input, type) for File inputs the script never opens, only checks for."""
    findings = []
    for path, doc in docs:
        for task in doc.tasks:
            uses = {}
            for name, inside_defined in _command_idents(task):
                uses.setdefault(name, []).append(inside_defined)
            for inp in task.inputs:
                if 'File' not in str(inp.type):
                    continue
                appearances = uses.get(inp.name)
                if appearances and all(appearances):
                    findings.append((path.name, task.name, inp.name, str(inp.type)))
    return findings


def _command_idents(task):
    """(root_name, inside_defined) for every identifier in every command placeholder."""
    for part in task.command.parts:
        if isinstance(part, str):
            continue
        yield from _idents(part.expr, False)


def _idents(expr, inside_defined):
    if isinstance(expr, WDL.Expr.Ident):
        yield expr.name, inside_defined
    if isinstance(expr, WDL.Expr.Apply):
        # `defined(x)` is the one call that must not localize, and it takes exactly the argument we
        # are asking about. Anything else (select_first, if/then via At) is ordinary use.
        flag = inside_defined or str(getattr(expr, 'function_name', '')) == 'defined'
        for child in expr.children:
            yield from _idents(child, flag)
        return
    for child in expr.children:
        yield from _idents(child, inside_defined)


# --- the renderer ------------------------------------------------------------------------------------
def render(task, stub: str = 'X') -> str:
    """A task's command block with placeholders replaced by `stub`.

    Empty string is a legitimate stub: a placeholder is allowed to expand to nothing, and half of
    gatk-sv's conditionals rely on it. `X` is the other half — the placeholder that supplies a word.
    Neither is the truth, which is why the syntax rule requires both to fail.
    """
    return ''.join(p if isinstance(p, str) else stub for p in task.command.parts)


def bash_syntax_ok(script: str) -> tuple:
    """(ok, stderr_tail). `bash -n` parses without executing, which is all any static check can do.

    The temp path is stripped from the message: a finding that embeds `<TMPDIR>/tmpXXXX.sh`
    differs on every run, and an artifact nobody can diff against its own baseline is not evidence.
    """
    with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run(['bash', '-n', path], capture_output=True, text=True, timeout=30)
        errs = [(line or '').replace(f'{path}: ', '') for line in (r.stderr or '').strip().splitlines()[-1:]]
        return r.returncode == 0, (errs or [''])
    except FileNotFoundError:
        return True, ['bash not found — SHELL-SYNTAX was NOT checked']
    finally:
        os.unlink(path)


def rule_shell_syntax(docs) -> list:
    """(file, task, stderr) for a command block that `bash -n` will not parse.

    One rendering, one stub. Measured at `main`: 2 findings, both renderer artifacts, both described
    in the module docstring — that pair IS the baseline a ref gets diffed against.
    """
    findings = []
    for path, doc in docs:
        for task in doc.tasks:
            ok, err = bash_syntax_ok(render(task, 'X'))
            if not ok:
                findings.append((path.name, task.name, err[0][:200]))
    return findings


# --- tree loading ------------------------------------------------------------------------------------
def wdl_files(directory: pathlib.Path) -> list:
    """The tree to scan: `DIR/wdl/*.wdl` when present (a clone), else `DIR/*.wdl` (a fetched ref)."""
    for cand in (directory / 'wdl', directory):
        found = sorted(cand.glob('*.wdl'))
        if found:
            return found
    return []


def load_tree(files: list) -> tuple:
    """(docs, failures). A file that will not parse is RETURNED AS A FAILURE, never skipped.

    `continue` on a load error is how a scan silently loses coverage: the prototype scans in two of
    the reviews this repo's reviewers wrote did exactly that, and `check_docs.py` and
    `svshell_jq_plumbing_scan.py` both refused to. Imports resolve by filename inside the directory,
    which is why callers hand this function one ref's directory and nothing else.
    """
    docs, failures = [], []
    for f in files:
        try:
            docs.append((f, WDL.load(str(f))))
        except Exception as e:                         # one bad file must not end the scan
            failures.append((f.name, f'{type(e).__name__}: {str(e)[:120]}'))
    return docs, failures


def scan_file(path_str: str):
    """Load one file and return (name, {rule: findings} or None, error, stats).

    Top-level, and returning only tuples, so a worker process can pickle the result back. Loading is
    33 s of this scan's 40 s on gatk-sv's 118 WDLs (all 315 `bash -n` calls together are 2.5 s), so
    the work is fanned out per file instead of done in one interpreter.
    """
    path = pathlib.Path(path_str)
    try:
        doc = WDL.load(str(path))
    except Exception as e:                             # reported, never dropped
        return (path.name, None, f'{type(e).__name__}: {str(e)[:120]}', None)
    docs = [(path, doc)]
    found = {'write_scope': rule_write_scope(docs), 'defined_only': rule_defined_only(docs),
             'pipefail': rule_pipefail(docs), 'shell_syntax': rule_shell_syntax(docs)}
    stats = {'files': 1, 'tasks': len(doc.tasks), 'workflows': 1 if doc.workflow else 0}
    return (path.name, found, None, stats)


def scan_tree(files: list, jobs: int = 0) -> tuple:
    """(findings_by_rule, failures, stats, seconds, how). Parallel where it works, serial where not.

    The fallback is not decoration: process startup is the thing that fails in a locked-down
    container, and a scan that refuses to run because it tried to be fast is worse than a slow one.
    Any exception from the pool, including a worker dying, falls through to the serial loop.
    """
    import time
    t0 = time.time()
    found = {r: [] for r in RULES}
    failures = []
    stats = {'files': 0, 'tasks': 0, 'workflows': 0}
    jobs = jobs or min(8, (os.cpu_count() or 1))
    if jobs > 1 and len(files) > 4:
        try:
            from concurrent.futures import ProcessPoolExecutor
            with ProcessPoolExecutor(max_workers=jobs) as pool:
                for name, res, err, st in pool.map(scan_file, [str(f) for f in files]):
                    if err:
                        failures.append((name, err))
                        continue
                    for r, items in res.items():
                        found[r].extend(items)
                    for k, v in st.items():
                        stats[k] += v
            return {r: sorted(v) for r, v in found.items()}, failures, stats, time.time() - t0, f'{jobs} workers'
        except Exception as e:
            print(f'  (parallel scan unavailable: {type(e).__name__}; falling back to one process)')
    docs, failures = load_tree(files)
    rules = {'write_scope': rule_write_scope, 'defined_only': rule_defined_only,
             'pipefail': rule_pipefail, 'shell_syntax': rule_shell_syntax}
    for r in RULES:
        found[r] = rules[r](docs)
    stats = {'files': len(docs), 'tasks': sum(len(d.tasks) for _, d in docs),
             'workflows': sum(1 for _, d in docs if d.workflow)}
    return found, failures, stats, time.time() - t0, 'serial'


# --- selftest ----------------------------------------------------------------------------------------
# Fixtures are written here rather than shipped as files so the control blocks cannot rot: a fixture
# nobody regenerates stops exercising its case long before anything notices. Every rule gets a bad
# case AND a good case, and the good case must be PRESENT (asserted), because "the guard never fired"
# and "the guard could not fire" otherwise print the same thing.
FIXTURES = {
    # control: clean on all four rules
    'good.wdl': """version 1.0
workflow Good {
  input { Array[String] samples }
  call Echo { input: lines = samples }
  output { Array[String] out = Echo.out }
}
task Echo {
  input {
    Array[String] lines
    File? maybe
  }
  command <<<
    set -euo pipefail
    wc -l < ~{write_lines(lines)}
    if [ -n "~{default='false' maybe}" ]; then cat "~{maybe}"; fi
    echo done
  >>>
  output { Array[String] out = read_lines(stdout()) }
  runtime { docker: "ubuntu:22.04" }
}
""",
    # WRITE-SCOPE: the shape that cost a submission, plus the *inside-a-scatter* shape that a
    # top-level-only scan would miss.
    'write_scope.wdl': """version 1.0
workflow Bad {
  input { Array[String] samples }
  File names = write_lines(samples)
  scatter (s in samples) {
    File inner = write_tsv([[s, s]])
  }
}
""",
    # DEFINED-ONLY: `open_me` is opened, `test_me` is only tested for, `unused_me` never appears at
    # all (a different shape, and deliberately NOT reported — see the rule's docstring).
    'defined_only.wdl': """version 1.0
workflow D {
  input { File a }
  call Peek { input: open_me = a, test_me = a, unused_me = a }
}
task Peek {
  input {
    File open_me
    File? test_me
    File? unused_me
  }
  Boolean have_it = defined(test_me)
  command <<<
    set -euo pipefail
    head -c 1 "~{open_me}" > /dev/null || true
    echo ~{if defined(test_me) then "yes" else "no"}
  >>>
  runtime { docker: "ubuntu:22.04" }
}
""",
    # PIPEFAIL: six cases on purpose. Two of them are findings; the other four are the
    # false-positive classes, each of which cost a measurement to learn (see the rule's comments).
    'pipefail.wdl': """version 1.0
workflow P {
  input { File big }
  call Pipes { input: big = big }
}
task Pipes {
  input { File big }
  command <<<
    set -eu
    zcat "~{big}" | awk 'NR>1{print}' | head -n 1
    set -o pipefail
    zcat "~{big}" | head -n 1
    FIRST=$(zcat "~{big}" | head -n 1)
    zcat "~{big}" | fgrep "#" > head.txt
    zcat "~{big}" | awk 'END{exit 1}'
    zcat "~{big}" | awk 'NR==1{exit} {print}' | wc -l || true
    zcat "~{big}" | wc -l
  >>>
  runtime { docker: "ubuntu:22.04" }
}
""",
    # SHELL-SYNTAX: exactly the defect both WDL validators certified clean, reached the way it is
    # reached in practice — a mechanical edit that moved a guard outside its `$( ... )`.
    'shell_syntax.wdl': """version 1.0
workflow S {
  input { File counts }
  call Condense { input: counts = counts }
}
task Condense {
  input { File counts }
  command <<<
    set -euo pipefail
    rd_header=$(zcat "~{counts}" | head -n 1 || true
    echo "header taken"
  >>>
  runtime { docker: "ubuntu:22.04" }
}
""",
}


def selftest() -> int:
    """Build the fixture tree, scan it, and assert every rule fires on its bad case only."""
    if WDL is None:
        print('selftest SKIP: miniwdl is not installed (make setup, or pip install miniwdl)')
        return 0
    tmp = tempfile.mkdtemp(prefix='wdl-semantics-selftest-')
    root = pathlib.Path(tmp)
    for name, text in FIXTURES.items():
        (root / name).write_text(text)
    files = sorted(root.glob('*.wdl'))
    docs, failures = load_tree(files)
    bad = []
    if len(files) != len(FIXTURES):
        bad.append(f'fixtures: wrote {len(FIXTURES)}, found {len(files)}')
    if failures:
        bad.append(f'fixtures did not parse (a fixture that does not parse proves nothing): {failures}')
        print('  FAIL', '\n  FAIL '.join(bad))
        return 1
    got = {'write_scope': rule_write_scope(docs), 'defined_only': rule_defined_only(docs),
           'pipefail': rule_pipefail(docs), 'shell_syntax': rule_shell_syntax(docs)}

    def want(rule, pred, label):
        n = sum(1 for f in got[rule] if pred(f))
        print(f'  {"ok  " if n >= 1 else "FAIL"}  {rule}: {label} ({n} found)')
        if n < 1:
            bad.append(f'{rule}: {label}')

    def want_none(rule, pred, label):
        n = sum(1 for f in got[rule] if pred(f))
        print(f'  {"ok  " if n == 0 else "FAIL"}  {rule}: {label} ({n} found)')
        if n:
            bad.append(f'{rule}: {label} — found {n}')

    # write_scope: both the top-level decl and the one buried in a scatter
    want('write_scope', lambda f: f[2] == 'names' and f[3] == 'write_lines', 'top-level write_lines found')
    want('write_scope', lambda f: f[3] == 'write_tsv', 'write_tsv inside a scatter found')
    want_none('write_scope', lambda f: f[0] == 'good.wdl', 'nothing on the clean control')
    # defined_only: the tested-only input, and NOT the one that is opened or the clean control
    want('defined_only', lambda f: f[2] == 'test_me', 'defined()-only File found')
    want_none('defined_only', lambda f: f[2] == 'open_me', 'a File actually opened is not a finding')
    want_none('defined_only', lambda f: f[0] == 'good.wdl', 'nothing on the clean control')
    # pipefail: only the pipe that runs after pipefail was armed and has no guard
    want('pipefail', lambda f: f[5] == 'UNGUARDED' and f[3] == 'head', 'unguarded head after pipefail found')
    want_none('pipefail', lambda f: "NR>1{print}" in f[4], 'a pipe BEFORE set -o pipefail is not a finding')
    want_none('pipefail', lambda f: 'END{' in f[4], 'awk END{exit} is not an early exit')
    want_none('pipefail', lambda f: f[5] == 'UNGUARDED' and '|| true' in f[4], 'a guarded site is credited as guarded')
    want_none('pipefail', lambda f: f[4].rstrip().endswith('| wc -l'), 'a consumer that drains the pipe is not a finding')
    want('pipefail', lambda f: 'FIRST=' in f[4] and f[5] == 'UNGUARDED', 'a short reader inside $( ... ) is still a pipe')
    want_none('pipefail', lambda f: 'head.txt' in f[4], 'a file NAMED head.txt is not the head command')
    # and the counts themselves: exactly two unguarded sites, exactly one credited as guarded
    pf = [f for f in got['pipefail'] if f[0] == 'pipefail.wdl']
    unguarded = [f for f in pf if f[5] == 'UNGUARDED']
    guarded = [f for f in pf if f[5] == 'GUARDED']
    print(f'  {"ok  " if len(unguarded) == 2 else "FAIL"}  pipefail: exactly 2 UNGUARDED in the fixture ({len(unguarded)})')
    print(f'  {"ok  " if len(guarded) == 1 else "FAIL"}  pipefail: exactly 1 GUARDED in the fixture ({len(guarded)})')
    if len(unguarded) != 2 or len(guarded) != 1:
        bad.append(f'pipefail: expected 2 unguarded + 1 guarded in pipefail.wdl, got {pf}')
    # shell_syntax: the unbalanced paren, and nothing else
    want('shell_syntax', lambda f: f[1] == 'Condense', 'unbalanced ( inside command <<< found by bash -n')
    want_none('shell_syntax', lambda f: f[0] != 'shell_syntax.wdl', 'no other fixture trips bash -n')
    # the renderer's own control: miniwdl must accept the broken block, or this rule is not adding
    # anything and its whole argument is a story.
    try:
        WDL.load(str(root / 'shell_syntax.wdl'))
        print('  ok    miniwdl parses the block bash -n rejects (so the rule is not redundant)')
    except Exception as e:
        bad.append(f'shell_syntax control invalid: miniwdl itself rejected it ({e})')
        print(f'  FAIL  miniwdl rejected the fixture, so the rule proves nothing: {e}')

    for r in RULES:
        print(f'        {COLUMNS[r]:13s} {len(got[r])} finding(s) across {len(docs)} fixture file(s)')
    # --block: the extraction verb is how a branch grades its own WDL instead of a copy of the
    # block, so it is exercised here rather than discovered broken on the first real use.
    import io
    import contextlib
    buf, errbuf = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(errbuf):
        brc = print_block(files, 'Pipes', False)
    body = buf.getvalue()
    block_ok = brc == 0 and 'zcat "X"' in body and '~{' not in body
    print(f'  {"ok  " if block_ok else "FAIL"}  --block renders one task (placeholders gone, shell kept)')
    if not block_ok:
        bad.append(f'--block returned {brc}; body starts {body[:60]!r}')
    buf2, errbuf2 = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(buf2), contextlib.redirect_stderr(errbuf2):
        rrc = print_block(files, 'NopeNotATask', False)
    print(f'  {"ok  " if rrc == 1 else "FAIL"}  --block on a name that does not exist fails rather than printing nothing')
    if rrc != 1:
        bad.append(f'--block on an unknown task returned {rrc}, expected 1')
    if bad:
        print('selftest: FAIL\n  ' + '\n  '.join(bad))
        return 1
    print(f'selftest: ok — {len(FIXTURES)} fixtures, 4 rules, every bad case found and every control silent')
    return 0


# --- main --------------------------------------------------------------------------------------------
def main(argv: list) -> int:
    ap = argparse.ArgumentParser(
        prog='wdl_semantics.py', description=__doc__.split('\n\n')[0],
        epilog='counts are a baseline diff, not an oracle: run it on a base ref and on your ref.')
    ap.add_argument('--dir', '--repo', dest='dir', help='tree of *.wdl to scan (a fetched ref dir, or a gatk-sv clone)')
    ap.add_argument('--rule', action='append', choices=RULES, help='run only this rule (repeatable)')
    ap.add_argument('--block', metavar='TASK', help='print the rendered command block of one task and exit')
    ap.add_argument('--raw', action='store_true', help='with --block: the literal WDL text, not the render')
    ap.add_argument('--summary-only', action='store_true', help='print one KEY=N line and nothing else')
    ap.add_argument('--json', metavar='PATH', help='write the machine-readable artifact here')
    ap.add_argument('--top', type=int, default=20, help='findings shown per rule before truncating (default 20)')
    ap.add_argument('--jobs', type=int, default=0, help='worker processes for the parse (0 = min(8, cpus); 1 forces serial)')
    ap.add_argument('-v', '--verbose', action='store_true', help='list every finding, not the first --top')
    ap.add_argument('--strict', action='store_true', help='exit 1 on any finding (needs a baseline first)')
    ap.add_argument('--selftest', action='store_true', help='scan the shipped fixtures and check the checks')
    a = ap.parse_args(argv[1:])

    if a.selftest:
        return selftest()
    if WDL is None:
        print('miniwdl is required: make setup, or python -m pip install miniwdl\n'
              '  (it provides the `WDL` module this scan walks)', file=sys.stderr)
        return 3
    if not a.dir:
        print('name a tree: --dir DIR  (or --selftest, or --help)', file=sys.stderr)
        return 2
    root = pathlib.Path(a.dir)
    files = wdl_files(root)
    if not files:
        print(f'no *.wdl under {root} (looked in ./ and ./wdl/). Name the directory '
              f'scripts/fetch_wdl.py materialized, or a gatk-sv clone.', file=sys.stderr)
        return 2

    if a.block:
        return print_block(files, a.block, a.raw)

    scanned, failures, stats, secs, how = scan_tree(files, a.jobs)
    ran = a.rule or list(RULES)
    scanned = {r: (scanned[r] if r in ran else []) for r in RULES}
    counts = {r: len(scanned[r]) for r in RULES}
    unguarded = sum(1 for f in scanned['pipefail'] if f[5] == 'UNGUARDED')

    summary = (f'{COLUMNS["write_scope"]}={counts["write_scope"]} '
               f'{COLUMNS["defined_only"]}={counts["defined_only"]} '
               f'{COLUMNS["pipefail"]}={unguarded} '
               f'{COLUMNS["shell_syntax"]}={counts["shell_syntax"]} '
               f'{COLUMNS["load_failures"]}={len(failures)}')
    if a.summary_only:
        print(summary)
    else:
        print(f'tree: {root}   files={stats["files"]} tasks={stats["tasks"]} '
              f'workflows={stats["workflows"]}   scan={secs:.1f}s ({how})')
        print(f'  {summary}')
        for r in RULES:
            if r not in ran:
                continue
            found = scanned[r]
            if not found:
                continue
            print(f'\n{COLUMNS[r]} — {len(found)}')
            shown = found if a.verbose else found[:a.top]
            for f in shown:
                if r == 'write_scope':
                    print(f'  {f[0]}::{f[1]}  {f[2]} = {f[3]}(...) at workflow scope — PAPIv2 cannot materialize it')
                elif r == 'defined_only':
                    print(f'  {f[0]}::{f[1]}  input {f[2]}: {f[3]} — localized, never opened')
                elif r == 'pipefail':
                    print(f'  {f[0]}::{f[1]}:{f[2]}  {f[5]} ({f[3]})  {f[4]}')
                else:
                    print(f'  {f[0]}::{f[1]}  {f[2]}')
            if len(found) > len(shown):
                print(f'  … {len(found) - len(shown)} more (use -v)')
        if failures:
            print(f'\nLOAD-FAILURES — {len(failures)} file(s) miniwdl could not parse. Every count above '
                  f'is a partial answer:', file=sys.stderr)
            for name, err in failures:
                print(f'  {name}  {err}', file=sys.stderr)
        if not any(counts[r] for r in ran) and not failures:
            print('\nno findings on the rules run. On gatk-sv as it stands several of these rules are '
                  'not zero, so a zero here means either a fix or a tree that was not what you '
                  'meant to scan — diff it against the base ref before believing it.')

    if a.json:
        doc = {'tool': 'wdl_semantics.py', 'argv': argv, 'inputs': {'dir': str(root), 'files': len(files)},
               'rule': {'stub_render': 'X (a word; see the module docstring for the 2 artifact rows '
                                       'this leaves at gatk-sv main)',
                        'pipefail_order_aware': True, 'rules_run': ran},
               'counts': counts, 'unguarded_pipefail': unguarded, 'stats': stats,
               'scan_seconds': round(secs, 2), 'scan_how': how,
               'load_failures': [list(f) for f in failures],
               'findings': {r: [list(f) for f in scanned[r]] for r in ran},
               'scanned_something': stats['files'] > 0}
        with open(a.json, 'w') as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
        print(f'artifact: {a.json}')

    if failures:
        return 2                                 # a partial answer is never a pass
    if a.strict and any(counts[r] for r in ran):
        return 1
    return 0


def print_block(files: list, want_task: str, raw: bool) -> int:
    """--block: one task's command, rendered (or literal). The entry point for running a real block."""
    hits = []
    for f in files:
        try:
            doc = WDL.load(str(f))
        except Exception:
            continue
        for t in doc.tasks:
            if t.name == want_task:
                hits.append((f, t))
    if not hits:
        print(f'no task named {want_task} in any file under the tree', file=sys.stderr)
        return 1
    if len(hits) > 1:
        print(f'{want_task} is declared in {len(hits)} files — pass one explicitly is not possible '
              f'here, so rename or grep:', file=sys.stderr)
        for f, _ in hits:
            print(f'  {f}', file=sys.stderr)
        return 1
    path, task = hits[0]
    if raw:
        src = path.read_text().splitlines(keepends=True)
        pos = task.command.pos
        print(''.join(src[pos.line - 1:pos.end_line])[pos.column - 1:], end='')
    else:
        print(render(task, 'X'), end='')
    print(f'\n# {path.name}::{task.name}  lines {task.command.pos.line}-{task.command.pos.end_line}  '
          f'(placeholders stubbed as X; --raw for the WDL text)', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
