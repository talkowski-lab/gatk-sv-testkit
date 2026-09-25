#!/usr/bin/env python3
"""wdl_reach.py — if I change this file, what reaches it?

Why this exists
---------------
Two reviews of this repo needed the same join and, from opposite ends, each hand-rolled it. One
started from a shell script (`mantatloccheck.sh`) and asked which tasks run it, so it could name
which module a fix lands in. The other started from a WDL module (`Structs.wdl`) and asked what
imports it, so it could say how much of the tree a struct change touches. Both are the same question
along the same graph, and neither wrote the graph: they grepped. A grep answers "where does this
string appear", which is not the relation "what reaches this" — it cannot invert, it misses the
transitive hop (a.wdl imports b.wdl imports c.wdl), and it counts a mention in a comment as a caller.

So this tool holds the graph and answers in either direction:

    FORWARD   --target X             what X reaches: its imports, the callables it runs, the
                                     scripts those tasks invoke.
    REVERSE   --target X --reverse   what reaches X: callers, importers, the tasks that invoke a
                                     script, and transitively everything upstream of them.

The edges are the three a WDL tree actually carries, plus the one hop that makes them transitive:

    import      file -> file        `import "X.wdl"`, resolved against the importing file's own
                                    directory, so an import out of the tree stays its own node.
    call        workflow -> callable  resolved through miniwdl's own `call.callee`, so a namespaced
                                    `call Cluster.CombineBatches` lands on the workflow inside
                                    CombineBatches.wdl rather than on a string match.
    invoke      task -> script      the task's command block names a script. Taken from the RAW
                                    block text, not a placeholder-stubbed render, because a name can
                                    live inside a placeholder default (`~{default="/opt/x.sh" x}`)
                                    that a render erases — an edge that silently goes missing is
                                    exactly the failure class this file was written to refuse.
    contains    file -> its own workflows/tasks  the hop that turns a caller into its file and a
                                    file into whoever imports it. Without it a reverse answer stops
                                    at the first callable instead of reaching the pipeline.

What a hit prints
-----------------
Depth (1 = a direct edge), kind, node, the workflow a file's answer belongs to, and the edge that
carried the reach with its file:line (`imports Structs.wdl (Structs.wdl) at AnnotateVcf.wdl:3`).
Depth is the useful half: depth 1 is the code you are about to break, depth 3 is the pipeline that
ships it.

A target nothing reaches prints `NOT REACHED` with the number of files scanned, because the failure
this repo names is the scan that read nothing and reported clean. The other three ways a reach tool
can lie are refused the same way: a file miniwdl cannot parse is reported and makes the answer
partial (exit 2, never 0), a `call` miniwdl cannot resolve is reported as UNRESOLVED-CALLS instead of
dropped, and an import pointing outside the scanned directory is counted as `imports-outside` so a
short answer is never mistaken for a closed one.

Usage
-----
    checks/wdl_reach.py --dir DIR --target NAME              # what NAME reaches
    checks/wdl_reach.py --dir DIR --target NAME --reverse    # what reaches NAME
    checks/wdl_reach.py --dir DIR --target Foo.wdl -v        # every hit, with its full chain
    checks/wdl_reach.py --dir DIR --target NAME --json OUT   # machine-readable artifact
    checks/wdl_reach.py --selftest                           # 4 fixture WDLs, both directions

DIR is the tree `wdl_semantics.py` reads, by way of `wdl_semantics.wdl_files`: a `wdl/` subdirectory
is preferred when present (a gatk-sv clone), else `DIR` itself (a `scripts/fetch_wdl.py` ref dir).
`--repo` is accepted as a synonym for `--dir`, as in the sibling checkers.

NAME is a file (`Structs.wdl`), a callable (`MakeCohortVcf`, or `MakeCohortVcf.wdl::MakeCohortVcf`),
or a script basename (`mantatloccheck.sh`). A name that matches several nodes unions them and says
so — quietly choosing one is the same class of lie as an empty answer. A name that matches nothing
exits 2 and lists the closest names the tree does know.

Exit codes: 0 answered (reached, or NOT REACHED — an orphan is an answer), 2 unknown name / no tree /
a partial answer, 3 miniwdl missing. Loading a whole gatk-sv tree is ~35 s and the traversal after it
is instant, so for repeated questions run it once with `--json` and read the artifact.

No data, no docker, no network, nothing written but `--json`.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import pathlib
import re
import sys
import tempfile
import time

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import wdl_semantics as _ws        # noqa: E402 — wdl_files + load_tree: the DIR convention and the honest loader

WDL = _ws.WDL

# A script name inside a command block. The extensions gatk-sv actually invokes (.sh, .py, .R, .jar)
# plus what a portable WDL would reach for. Deliberately NOT `.wdl`: a command that names a WDL is
# invoking a workflow, and a real `call` already covers that edge — a string match would invent one
# (cromwell-submitted sub-workflows included, which is a reach claim nobody verified).
SCRIPT_EXT = r'(?:sh|bash|ksh|zsh|py|py3|pl|pm|rb|perl|R|jar|cwl|twf)'
SCRIPT_TOKEN = re.compile(r'[\w./+-]*\.' + SCRIPT_EXT + r'(?![\w.])')
COMMENT_LINE = re.compile(r'^\s*#')
KINDS = ('file', 'workflow', 'task', 'script', 'unresolved')


# --- the graph ---------------------------------------------------------------------------------------
class Graph:
    """Nodes, forward edges, and the same edges indexed by target for the reverse question.

    A node is a tuple: ('file', abspath), ('callable', abspath, name), ('script', basename),
    ('unresolved', dotted_call_name). Files are keyed by absolute path so two same-named files in
    different directories stay two nodes; scripts are keyed by basename because that is what a human
    types into `--target`, while the token as written (`/opt/sv-pipeline/.../mantatloccheck.sh`)
    stays on the edge as evidence and is a lookup name too.
    """

    def __init__(self):
        self.nodes = {}                            # node tuple -> record
        self.fwd = collections.defaultdict(list)   # node -> [edge out]
        self.rev = collections.defaultdict(list)   # node -> [edge in]

    def add_node(self, node, kind, file, display, owner, names):
        if node not in self.nodes:
            self.nodes[node] = {'kind': kind, 'file': file, 'display': display,
                                'owner': owner, 'names': set(names), 'outside': False}
        return self.nodes[node]

    def add_edge(self, src, dst, kind, why, file, line):
        edge = {'src': src, 'dst': dst, 'kind': kind, 'why': why, 'file': file, 'line': line}
        self.fwd[src].append(edge)
        self.rev[dst].append(edge)

    def display(self, node):
        return self.nodes[node]['display'] if node in self.nodes else str(node[-1])

    @property
    def n_edges(self):
        return sum(len(v) for v in self.fwd.values())

    @property
    def kind_counts(self):
        return collections.Counter(rec['kind'] for rec in self.nodes.values())


def key_path(p) -> str:
    """One canonical spelling for a path-keyed node id.

    miniwdl reports an import or callee as `os.path.abspath`, while `Path.resolve()` on macOS
    rewrites `/var/...` to `/private/var/...`. Keyed by two spellings, the same file becomes two
    nodes and every answer about it is half right, so everything path-keyed comes through here.
    """
    return str(pathlib.Path(p).resolve())


def command_script_hits(doc, task) -> list:
    """(basename, as_written, source_line) for every script a task's command block names.

    Read from the file's own source lines between `command` and the closing delimiter, so the line
    number is where the human would find it and the text is as written, placeholders included.
    Whole-line comments are dropped: a commented-out invocation is documentation, and crediting it
    as a caller buries the caller that is real.
    """
    pos = task.command.pos
    out = []
    for lineno in range(pos.line, min(pos.end_line, len(doc.source_lines)) + 1):
        line = doc.source_lines[lineno - 1]
        if lineno == pos.line:
            line = line[pos.column - 1:]
        if lineno == pos.end_line:
            line = line[:max(0, pos.end_column - 1)]
        if COMMENT_LINE.match(line):
            continue
        for tok in SCRIPT_TOKEN.findall(line):
            out.append((tok.rsplit('/', 1)[-1], tok, lineno))
    return out


def walk_calls(node) -> list:
    """Every `call` in a workflow body, at whatever scatter/if nesting depth it sits at."""
    out, stack = [], [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, WDL.Tree.Call):
            out.append(cur)
        stack.extend(getattr(cur, 'body', None) or [])
    return out


def build_graph(files: list) -> tuple:
    """(graph, load_failures, unresolved_calls, imports_outside). Nothing unread is dropped.

    `wdl_semantics.load_tree` is reused rather than re-written: it is the loader that already
    promises a returned failure instead of a `continue`, and re-deriving that promise here is exactly
    where a second implementation would quietly lose it.
    """
    docs, failures = _ws.load_tree(files)
    known = {key_path(f) for f in files}
    g = Graph()
    unresolved, outside = [], []

    for path, doc in docs:
        abspath = key_path(path)
        wflow = doc.workflow.name if doc.workflow else None
        owner = (f'workflow {wflow}' if wflow else
                 (f'{len(doc.tasks)} task(s)' if doc.tasks else 'no workflow'))
        fnode = ('file', abspath)
        g.add_node(fnode, 'file', path.name, path.name, owner, {path.name, abspath})

        for imp in doc.imports:
            tgt = key_path(path.parent / imp.uri)
            tnode = ('file', tgt)
            g.add_node(tnode, 'file', pathlib.Path(tgt).name, pathlib.Path(tgt).name, '',
                       {pathlib.Path(tgt).name, tgt})
            g.add_edge(fnode, tnode, 'import', f'imports {pathlib.Path(tgt).name}',
                       path.name, imp.pos.line)
            if tgt not in known:
                outside.append((path.name, imp.uri))
                g.nodes[tnode]['outside'] = True

        # containment: a file reaches what runs inside it, so a reverse answer climbs from a callable
        # to its file and from that file to whoever imports it.
        callables = []
        if wflow:
            g.add_node(('callable', abspath, wflow), 'workflow', path.name,
                       f'{path.name}::{wflow}', '',
                       {wflow, f'{path.name}::{wflow}', f'{abspath}::{wflow}'})
            callables.append(('callable', abspath, wflow))
        for task in doc.tasks:
            cn = ('callable', abspath, task.name)
            g.add_node(cn, 'task', path.name, f'{path.name}::{task.name}', '',
                       {task.name, f'{path.name}::{task.name}', f'{abspath}::{task.name}'})
            callables.append(cn)
            for base, written, lineno in command_script_hits(doc, task):
                sn = ('script', base)
                g.add_node(sn, 'script', path.name, base, '', {base, written})
                g.add_edge(cn, sn, 'invoke', f'invokes {base} ({written})', path.name, lineno)
        for cn in callables:
            g.add_edge(fnode, cn, 'contains', f'contains {g.nodes[cn]["kind"]} {cn[2]}',
                       path.name, 0)

        if wflow:
            wnode = ('callable', abspath, wflow)
            for call in walk_calls(doc.workflow):
                callee = call.callee
                if callee is None or not getattr(callee, 'pos', None):
                    name = '.'.join(call.callee_id)
                    g.add_node(('unresolved', name), 'unresolved', path.name, name, '', {name})
                    g.add_edge(wnode, ('unresolved', name), 'call?',
                               f'calls {name} (callee not resolved)', path.name, call.pos.line)
                    unresolved.append((path.name, name, call.pos.line))
                    continue
                cab = key_path(callee.pos.abspath)
                kind = 'task' if isinstance(callee, WDL.Task) else 'workflow'
                cn = ('callable', cab, callee.name)
                if cn not in g.nodes:
                    g.add_node(cn, kind, pathlib.Path(cab).name,
                               f'{pathlib.Path(cab).name}::{callee.name}', '',
                               {callee.name, f'{pathlib.Path(cab).name}::{callee.name}'})
                g.add_edge(wnode, cn, 'call',
                           f'calls {kind} {callee.name}'
                           + ('' if cab == abspath else f' ({pathlib.Path(cab).name})'),
                           path.name, call.pos.line)
    return g, failures, unresolved, outside


# --- traversal ---------------------------------------------------------------------------------------
def traverse(g: Graph, sources: list, reverse: bool) -> dict:
    """BFS from the target(s): node -> {'depth', 'via', 'prev', 'chain'}. The target set is excluded.

    `via` is the edge that carried this hit onto a shortest path and `chain` is that whole path, so a
    hit can be checked against the source instead of trusted. In reverse the chain reads hit -> ...
    -> target; forward it reads target -> ... -> hit.
    """
    adj = g.rev if reverse else g.fwd
    targets = set(sources)
    out = {}
    frontier = list(targets)
    depth = 0
    while frontier:
        depth += 1
        nxt = []
        for cur in frontier:
            for edge in adj[cur]:
                step = edge['src'] if reverse else edge['dst']
                if step in targets or step in out:
                    continue
                out[step] = {'depth': depth, 'via': edge, 'prev': cur}
                nxt.append(step)
        frontier = nxt
    for node, hit in out.items():
        chain = [node]
        cur = hit['prev']
        while cur not in targets:
            chain.append(cur)
            cur = out[cur]['prev']
        chain.append(cur)
        hit['chain'] = chain
    return out


def find_targets(g: Graph, want: str) -> list:
    """Every node NAME could mean. Exact matches only — a name is not fuzzy-searched into a verdict."""
    want = want.strip()
    return sorted((n for n, rec in g.nodes.items() if want in rec['names'] or rec['display'] == want),
                  key=lambda n: (g.nodes[n]['kind'], g.display(n)))


# --- reporting ---------------------------------------------------------------------------------------
def report(g: Graph, root, files, targets, hits, reverse: bool, top: int, verbose: bool) -> None:
    label = ', '.join(f'{g.display(n)} [{g.nodes[n]["kind"]}]' for n in targets)
    print(f'target: {label}   mode: {"REVERSE — what reaches it" if reverse else "FORWARD — what it reaches"}')
    reach, verb = (len(hits), 'reaches') if reverse else (len(hits), 'is reached by')
    if not hits:
        print(f'\nNOT REACHED — nothing in the tree {verb} {g.display(targets[0])} '
              f'({"upstream" if reverse else "downstream"} of it).')
        print(f'  scanned {len(files)} .wdl file(s) under {root}: {len(g.nodes)} nodes, {g.n_edges} '
              f'edges. An orphan is an answer, not a failure — but confirm '
              f'{root} is the tree and the ref you meant.')
        return

    by_depth = collections.Counter(h['depth'] for h in hits.values())
    by_kind = collections.Counter(g.nodes[n]['kind'] for n in hits)
    print(f'reach: {reach} node(s)   depths ' + ' '.join(f'{d}={by_depth[d]}' for d in sorted(by_depth))
          + '   kinds ' + ' '.join(f'{k}={by_kind[k]}' for k in KINDS if by_kind[k]))
    print(f'  {"DEPTH":<5} {"KIND":<9} {"NODE":<46} {"OWNER":<20} | VIA')
    order = sorted(hits.items(), key=lambda kv: (kv[1]['depth'], g.nodes[kv[0]]['kind'], g.display(kv[0])))
    shown = order if verbose else order[:top]
    for node, hit in shown:
        rec, edge = g.nodes[node], hit['via']
        why = edge['why'] + (f' at {edge["file"]}:{edge["line"]}' if edge['line'] else f' ({edge["file"]})')
        print(f'  {hit["depth"]:<5} {rec["kind"]:<9} {g.display(node):<46} {rec["owner"]:<20} | {why}')
        if verbose:
            chain = hit['chain'] if reverse else list(reversed(hit['chain']))
            print(f'         chain: {" -> ".join(g.display(n) for n in chain)}')
    if len(order) > len(shown):
        print(f'  … {len(order) - len(shown)} more (use -v)')


def artifact(argv, root, files, target, targets, hits, g, reverse, secs, failures, unresolved, outside) -> dict:
    return {'tool': 'wdl_reach.py', 'argv': argv, 'inputs': {'dir': str(root), 'files': len(files)},
            'target': {'name': target, 'mode': 'reverse' if reverse else 'forward',
                       'nodes': [[g.nodes[n]['kind'], g.display(n), g.nodes[n]['file']] for n in targets]},
            'rule': {'edge_kinds': ['import: file -> file', 'call: workflow -> callable (miniwdl-resolved)',
                                    'invoke: task command block -> script basename',
                                    'contains: file -> its own workflows and tasks'],
                     'script_match': 'raw command block text, whole-line comments dropped, basename key',
                     'target_set_excluded_from_hits': True},
            'counts': {'nodes': len(g.nodes), 'edges': g.n_edges, 'hits': len(hits),
                       'by_depth': {str(d): c for d, c in sorted(
                           collections.Counter(h['depth'] for h in hits.values()).items())},
                       'by_kind': dict(sorted(collections.Counter(
                           g.nodes[n]['kind'] for n in hits).items()))},
            'imports_outside': [list(o) for o in outside],
            'unresolved_calls': [list(u) for u in unresolved],
            'load_failures': [list(f) for f in failures],
            'hits': [{'kind': g.nodes[n]['kind'], 'node': g.display(n), 'file': g.nodes[n]['file'],
                      'owner': g.nodes[n]['owner'], 'depth': h['depth'], 'edge': h['via']['why'],
                      'edge_kind': h['via']['kind'], 'edge_site': f'{h["via"]["file"]}:{h["via"]["line"]}',
                      'chain': [g.display(x) for x in
                                (h['chain'] if reverse else list(reversed(h['chain'])))]}
                     for n, h in sorted(hits.items(), key=lambda kv: (kv[1]['depth'], g.display(kv[0])))],
            'reached': bool(hits), 'files_scanned': len(files), 'scanned_something': len(files) > 0,
            'load_seconds': round(secs, 2)}


# --- selftest ----------------------------------------------------------------------------------------
# Four WDLs: a imports b imports c, and d is an orphan. The chain is one file deeper than a two-file
# fixture would make it, because the transitive hop is the thing a grep gets wrong. The task in c
# names a script and carries a commented-out second one, so the script edge and its control live in
# the same file.
FIXTURES = {
    'a.wdl': '''version 1.0

import "b.wdl" as b

workflow A {
  input { Array[Int] shards }
  scatter (s in shards) {
    call b.B
  }
}
''',
    'b.wdl': '''version 1.0

import "c.wdl" as c

workflow B {
  input { File in_vcf }
  call c.C { input: in_vcf = in_vcf }
  output { Array[String] out = C.out }
}
''',
    'c.wdl': '''version 1.0

task C {
  input { File in_vcf }
  command <<<
    set -euo pipefail
    # analyze_commented.sh --this is documentation, not a caller
    analyze_vcf.sh --in ~{in_vcf}
  >>>
  output { Array[String] out = read_lines(stdout()) }
  runtime { docker: "x" cpu: 1 memory: "1G" preemptible: 0 }
}
''',
    'd.wdl': '''version 1.0

workflow D {
  input { Int n }
  output { Int twice = n * 2 }
}
''',
}


def selftest() -> int:
    """Build the four fixtures, resolve the chain both ways, and name the orphan as an orphan."""
    if WDL is None:
        print('selftest CANNOT RUN: miniwdl is not installed (make setup, or pip install miniwdl).\n'
              '  It exits nonzero rather than skipping clean: a reach tool that parsed nothing would\n'
              '  otherwise print NOT REACHED for every target in the tree and call that a pass.',
              file=sys.stderr)
        return 3
    root = pathlib.Path(tempfile.mkdtemp(prefix='wdl-reach-selftest-'))
    for name, text in FIXTURES.items():
        (root / name).write_text(text)
    files = _ws.wdl_files(root)
    g, failures, unresolved, _outside = build_graph(files)
    bad = []
    if len(files) != len(FIXTURES):
        bad.append(f'fixtures: wrote {len(FIXTURES)}, found {len(files)}')
    if failures:
        bad.append(f'fixtures did not parse (a fixture that does not parse proves nothing): {failures}')
    if unresolved:
        bad.append(f'fixtures left an unresolved call: {unresolved}')
    if bad:
        print('  FAIL ' + '\n  FAIL '.join(bad))
        return 1

    def run(*argv):
        """main() on the fixture tree, with the report captured so the printed words are asserted too."""
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = main(['wdl_reach.py', '--dir', str(root)] + list(argv))
        return rc, buf.getvalue()

    def want(cond, label, extra=''):
        print(f'  {"ok  " if cond else "FAIL"}  {label}{(" (" + extra + ")") if extra else ""}')
        if not cond:
            bad.append(label)

    def depth(hits, display):
        got = {g.display(n): h['depth'] for n, h in hits.items()}
        return got.get(display)

    fwd_a = traverse(g, find_targets(g, 'a.wdl'), False)
    fwd_c = traverse(g, find_targets(g, 'c.wdl'), False)
    rev_c = traverse(g, find_targets(g, 'c.wdl'), True)
    rev_c_task = traverse(g, find_targets(g, 'C'), True)
    rev_script = traverse(g, find_targets(g, 'analyze_vcf.sh'), True)

    # the edge kinds are all present, or the graph below is proving less than it claims
    kinds = {e['kind'] for edges in g.fwd.values() for e in edges}
    want(kinds == {'import', 'contains', 'invoke', 'call'},
         'all four edge kinds exist in the fixture graph', ' '.join(sorted(kinds)))
    # forward: a.wdl -> b.wdl -> c.wdl -> task C -> the script
    want(depth(fwd_a, 'b.wdl') == 1, 'forward: a.wdl imports b.wdl at depth 1', str(depth(fwd_a, 'b.wdl')))
    want(depth(fwd_a, 'c.wdl') == 2, 'forward: a.wdl reaches c.wdl THROUGH b.wdl (the transitive hop)')
    want(depth(fwd_a, 'c.wdl::C') == 3,
         'forward: a.wdl reaches task c.wdl::C (file -> workflow A -> workflow B -> task C)')
    want(depth(fwd_a, 'analyze_vcf.sh') == 4,
         'forward: a.wdl reaches the script its task eventually runs, four hops out')
    want('analyze_commented.sh' not in g.nodes, 'control: a commented-out invocation is not an edge')
    want(depth(fwd_c, 'c.wdl::C') == 1 and depth(fwd_c, 'analyze_vcf.sh') == 2,
         'forward: a file reaches its own task and that task\'s script (containment)')
    # reverse from the far end of the import chain
    want(depth(rev_c, 'b.wdl') == 1, 'reverse: c.wdl is reached by b.wdl, which imports it')
    want(depth(rev_c, 'a.wdl') == 2, 'reverse: c.wdl is reached by a.wdl, two hops upstream')
    want(depth(rev_c, 'd.wdl') is None, 'control: the orphan d.wdl is not in c.wdl\'s fan-in')
    # reverse from a callable: caller, the caller's file, and that file's importers
    want(depth(rev_c_task, 'b.wdl::B') == 1, 'reverse: workflow B reaches task C (a resolved call)')
    want(depth(rev_c_task, 'a.wdl::A') == 2, 'reverse: workflow A reaches task C through B')
    want(depth(rev_c_task, 'a.wdl') == 3, 'reverse: the FILE a.wdl reaches task C, via its own import')
    want(depth(rev_c_task, 'c.wdl') == 1, 'reverse: task C is reached by c.wdl, the file that holds it')
    # reverse from a script — the question a grep cannot answer at all
    want(depth(rev_script, 'c.wdl::C') == 1, 'reverse: task C reaches analyze_vcf.sh (command-block edge)')
    want(depth(rev_script, 'a.wdl::A') == 3, 'reverse: workflow A is upstream of the script, 3 hops out')
    # the printed answers
    rc, out = run('--target', 'd.wdl', '--reverse')
    want(rc == 0 and 'NOT REACHED' in out and 'scanned 4 .wdl file(s)' in out,
         'reverse: d.wdl prints NOT REACHED and names the 4 files scanned', f'rc={rc}')
    # An orphan in the direction that matters: nothing imports d.wdl, so its fan-in is empty. Its
    # workflow is NOT an orphan in the same graph — the file that runs it reaches it — and asserting
    # that single hit keeps the containment hop honest: it reaches the file and stops there.
    rev_d = traverse(g, find_targets(g, 'D'), True)
    want({g.display(n) for n in rev_d} == {'d.wdl'} and {e['via']['kind'] for e in rev_d.values()} == {'contains'},
         'reverse: workflow D is reached only by the file that runs it, and by nothing upstream',
         str(sorted(g.display(n) for n in rev_d)))
    rc, out = run('--target', 'analyze_vcf.sh', '--reverse')
    want(rc == 0 and 'c.wdl::C' in out and 'invokes analyze_vcf.sh' in out,
         'reverse via main(): the script report names the task and the edge that carried it')
    rc, out = run('--target', 'c.wdl', '--reverse')
    want(rc == 0 and 'b.wdl' in out and 'a.wdl' in out, 'reverse via main(): both importers are listed')
    rc, out = run('--target', 'Structs.wdl')
    want(rc == 2 and 'Structs.wdl' in out, 'an unknown target exits nonzero and names what it looked for',
         f'rc={rc}')
    rc, out = run('--target', 'zz_nope_zz')
    want(rc == 2 and 'zz_nope_zz' in out, 'a second invented name also fails loudly, never quietly')

    if bad:
        print('selftest: FAIL\n  ' + '\n  '.join(bad))
        return 1
    print(f'selftest: ok — {len(FIXTURES)} fixtures, {len(g.nodes)} nodes, {g.n_edges} edges; '
          f'the chain resolves in both directions and the orphan is named as one')
    return 0


# --- main --------------------------------------------------------------------------------------------
def main(argv: list) -> int:
    ap = argparse.ArgumentParser(
        prog='wdl_reach.py', description=__doc__.split('\n\n')[0],
        epilog='forward = what it reaches, --reverse = what reaches it. NOT REACHED is an answer; an '
               'unknown name is not. A whole gatk-sv tree takes ~35 s to load, then the walk is free.')
    ap.add_argument('--dir', '--repo', dest='dir',
                    help='tree of *.wdl: a gatk-sv clone (DIR/wdl preferred) or a fetched ref dir')
    ap.add_argument('--target',
                    help='file (Structs.wdl), callable (MakeCohortVcf / File.wdl::Name), or script '
                         '(mantatloccheck.sh)')
    ap.add_argument('--reverse', action='store_true',
                    help='what reaches the target, instead of what the target reaches')
    ap.add_argument('--json', metavar='PATH', help='write the machine-readable artifact here')
    ap.add_argument('--top', type=int, default=25, help='hits shown before truncating (default 25)')
    ap.add_argument('-v', '--verbose', action='store_true', help='list every hit, with its full chain')
    ap.add_argument('--selftest', action='store_true', help='4 fixture WDLs, resolved in both directions')
    a = ap.parse_args(argv[1:])

    if a.selftest:
        return selftest()
    if WDL is None:
        print('miniwdl is required: make setup, or python -m pip install miniwdl\n'
              '  (it provides the `WDL` module this scan walks)', file=sys.stderr)
        return 3
    if not a.dir or not a.target:
        print('name both a tree and a target: --dir DIR --target NAME  (or --selftest, --help)',
              file=sys.stderr)
        return 2
    root = pathlib.Path(a.dir)
    files = _ws.wdl_files(root)
    if not files:
        print(f'no *.wdl under {root} (looked in ./ and ./wdl/). Name the directory '
              f'scripts/fetch_wdl.py materialized, or a gatk-sv clone.', file=sys.stderr)
        return 2

    t0 = time.time()
    g, failures, unresolved, outside = build_graph(files)
    secs = time.time() - t0
    kc = g.kind_counts
    print(f'tree: {root}   files={len(files)} nodes={len(g.nodes)} edges={g.n_edges}   load={secs:.1f}s')
    print('  kinds ' + ' '.join(f'{k}={kc[k]}' for k in KINDS if kc[k])
          + f'   imports-outside={len(outside)} unresolved-calls={len(unresolved)}')

    targets = find_targets(g, a.target)
    if not targets:
        known = sorted({n for rec in g.nodes.values() for n in rec['names']
                        if '::' not in n and '/' not in n})
        close = difflib.get_close_matches(a.target, known, n=6, cutoff=0.55)
        print(f'unknown target {a.target!r}: no file, workflow, task or script of that name is in '
              f'{root} ({len(files)} .wdl file(s) scanned, {len(g.nodes)} nodes known). A file target '
              f'needs its .wdl suffix; a script target is the basename as the command block spells it; '
              f'a workflow or task target is the bare name.', file=sys.stderr)
        if close:
            print('  closest: ' + ', '.join(close), file=sys.stderr)
        return 2
    if len(targets) > 1:
        print(f'  NOTE {a.target} names {len(targets)} nodes; the answer below is their union: '
              + ', '.join(f'{g.display(n)} [{g.nodes[n]["kind"]}]' for n in targets))

    hits = traverse(g, targets, a.reverse)
    report(g, root, files, targets, hits, a.reverse, a.top, a.verbose)

    if a.json:
        with open(a.json, 'w') as fh:
            json.dump(artifact(argv, root, files, a.target, targets, hits, g, a.reverse, secs,
                               failures, unresolved, outside), fh, indent=1, sort_keys=True)
        print(f'artifact: {a.json}')

    if failures or unresolved:
        if failures:
            print(f'\nLOAD-FAILURES — {len(failures)} file(s) miniwdl could not parse. Every answer '
                  f'above is a partial answer:', file=sys.stderr)
            for name, err in failures:
                print(f'  {name}  {err}', file=sys.stderr)
        if unresolved:
            print(f'\nUNRESOLVED-CALLS — {len(unresolved)} call(s) miniwdl could not resolve, so the '
                  f'edge to their target is missing from the answer above:', file=sys.stderr)
            for name, callee, line in unresolved:
                print(f'  {name}:{line}  call {callee}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
