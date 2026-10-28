#!/usr/bin/env python3
"""wdl_semantics.py — does this WDL actually RUN, or does it only typecheck?

Static checks for WDL defects that `miniwdl check`, `womtool validate` and `checks/wdl_gate.sh` all
pass, because they live in what the workflow means or in the rendered shell:

  WRITE-SCOPE   a workflow-scope `write_lines`/`write_tsv`/`write_json`/`write_map`. Cromwell on
                PAPIv2 cannot materialize a workflow-level File decl, so the submission fails.

  DEFINED-ONLY  a `File` input whose only appearance in the command is inside `defined(...)`.
                Cromwell still localizes the file, so it is copied to a shard that never opens it.

  PIPEFAIL      a pipe whose consumer stops reading early (`head`, `grep -m`, `sed ... q`,
                `awk ... exit`) after the task has set `pipefail`. The producer can die of SIGPIPE
                (rc 141), typically only on large inputs.

  SHELL-SYNTAX  the task's command, rendered and checked with `bash -n`. WDL validators treat the
                command as a string and accept e.g. an unbalanced paren.

  LOAD-FAILURES files that could not be parsed. Nonzero means every other count is partial.

These are counts to diff against a base ref, not oracles: gatk-sv already carries some findings
(deliberate DEFINED-ONLY inputs, renderer artifacts), so the delta is what matters. Use `--strict`
only once you have a baseline.

Rendering uses miniwdl's `task.command.parts` (literal strings and placeholders), so placeholders
are replaced by the stub `X` without any brace or quote parsing. The stub causes known false
positives: 2 command blocks in `TasksMakeCohortVcf` at gatk-sv `main` fail `bash -n` because a
placeholder expands to a leading pipe. Those are part of the baseline.

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

# Rules in report order. Names are CLI tokens (--rule) and are parsed by wdl_gate.sh.
RULES = ("write_scope", "defined_only", "pipefail", "shell_syntax")
COLUMNS = {"write_scope": "WRITE-SCOPE", "defined_only": "DEFINED-ONLY",
           "pipefail": "PIPEFAIL", "shell_syntax": "SHELL-SYNTAX", "load_failures": "LOAD-FAILURES"}

# --- rule: pipefail ---------------------------------------------------------------------------------
# Any `set` line naming pipefail arms it (`-o pipefail`, fused `-euo pipefail`, ...). Only pipes after
# that line count: some gatk-sv tasks read a header before setting pipefail.
PIPEFAIL_SET = re.compile(r'\bset\b[^\n]*pipefail')

# Early-exiting consumers: `head`, `grep -m N`, `sed ... q`, and awk with a bare `exit`. Readers
# that drain to EOF (`wc`, `sort`, `tail`) are not. Matched against the segment's command word only.
CONSUMER_SED_Q = re.compile(r'\b[0-9]+q\b|;\s*q\b|\bq\s*[\'"]\s*$')
AWK_PROG = re.compile(r"awk\s+(-F\S+\s+)?(['\"])((?:\\\\.|[^\\\\])*?)\2")
END_BLOCK = re.compile(r'END\s*\{[^}]*\}')
# An explicit swallow (`|| true`, `|| :`, ...) on the same line marks the pipe as guarded.
GUARD_SAME_LINE = re.compile(r'\|\|\s*(true|:|echo|exit\s+0|grep|awk|head)')
COMMENT = re.compile(r'^\s*#')


def _awk_exits_early(prog: str) -> bool:
    """True if an awk program can call `exit` before EOF (an `exit` in END does not count)."""
    stripped = END_BLOCK.sub('', prog)
    return re.search(r'(?<!\w)exit(?!\s*\()\s*[;}\d]?|\bexit\b\s*\d', stripped) is not None


def _pipes(line: str) -> list:
    """Split a line on `|`, ignoring pipes inside quotes.

    Parentheses are not tracked, so pipes inside `$( ... )` are still found.
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
    """The first word of a pipe segment, skipping group openers and `VAR=x` prefixes."""
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
                    continue
                if PIPEFAIL_SET.search(line):
                    armed = True
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
    """Every Decl under a workflow body, including inside `if`/`scatter` blocks."""
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
        # Identifiers under defined(...) are flagged; any other use counts as opening the file.
        flag = inside_defined or str(getattr(expr, 'function_name', '')) == 'defined'
        for child in expr.children:
            yield from _idents(child, flag)
        return
    for child in expr.children:
        yield from _idents(child, inside_defined)


# --- the renderer ------------------------------------------------------------------------------------
def render(task, stub: str = 'X') -> str:
    """A task's command block with placeholders replaced by `stub`."""
    return ''.join(p if isinstance(p, str) else stub for p in task.command.parts)


def bash_syntax_ok(script: str) -> tuple:
    """(ok, stderr_tail) from `bash -n`. The temp path is stripped so output is stable across runs."""
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
    """(file, task, stderr) for a command block that `bash -n` will not parse."""
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
    """(docs, failures). A file that will not parse is returned as a failure, never skipped.

    Imports resolve by filename, so all files should come from one directory.
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

    Top-level and picklable so it can run in a worker process; parsing dominates scan time.
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
    """(findings_by_rule, failures, stats, seconds, how).

    Runs in a process pool; any pool failure (e.g. a sandbox that blocks subprocesses) falls back
    to a serial scan.
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
# Inline fixtures: each rule has a bad case and a clean control.
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
    # WRITE-SCOPE: top level and inside a scatter
    'write_scope.wdl': """version 1.0
workflow Bad {
  input { Array[String] samples }
  File names = write_lines(samples)
  scatter (s in samples) {
    File inner = write_tsv([[s, s]])
  }
}
""",
    # DEFINED-ONLY: `open_me` is opened, `test_me` is only tested, `unused_me` never appears (not reported)
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
    # PIPEFAIL: two findings plus the false-positive shapes the rule must skip
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
    # SHELL-SYNTAX: an unclosed `$( ...` that WDL validators accept
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
    # miniwdl must accept the broken block, or this rule adds nothing
    try:
        WDL.load(str(root / 'shell_syntax.wdl'))
        print('  ok    miniwdl parses the block bash -n rejects (so the rule is not redundant)')
    except Exception as e:
        bad.append(f'shell_syntax control invalid: miniwdl itself rejected it ({e})')
        print(f'  FAIL  miniwdl rejected the fixture, so the rule proves nothing: {e}')

    for r in RULES:
        print(f'        {COLUMNS[r]:13s} {len(got[r])} finding(s) across {len(docs)} fixture file(s)')
    # --block
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
        return 2                                 # partial scan
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
