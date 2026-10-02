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

Several targets, one load
-------------------------
`--target` is repeatable, and repeating it is what makes a batch question affordable: loading a whole
gatk-sv tree costs ~35 s while a walk after it costs milliseconds, so N names asked as N processes
cost N loads and N names asked as ONE process cost one. The rule the run keeps:

    the tree loads once, the `tree:` provenance line prints once, and then one answer block per
    target, in the order the names were given (duplicates included — nothing is deduped, because
    reordering a caller's list is not this tool's question to answer).

An unknown name among several does not stop the rest: each name that resolves gets its answer block,
each name that does not gets its own `unknown target` report with its own closest-name list, and the
run exits 2 if ANY target was unknown. That asymmetry is the same one the single-target case already
keeps — `NOT REACHED` is an answer (exit 0) and an unknown name is not — and a batch run that quietly
dropped the name it could not resolve would be the loudest possible version of the lie this file
exists to refuse: a clean exit over a question that went unanswered.

Usage
-----
    checks/wdl_reach.py --dir DIR --target NAME              # what NAME reaches
    checks/wdl_reach.py --dir DIR --target NAME --reverse    # what reaches NAME
    checks/wdl_reach.py --dir DIR --target A --target B      # both, ONE tree load, in that order
    checks/wdl_reach.py --dir DIR --target Foo.wdl -v        # every hit, with its full chain
    checks/wdl_reach.py --dir DIR --target NAME --json OUT   # machine-readable artifact
    checks/wdl_reach.py --selftest                           # 4 fixture WDLs, both directions,
                                                             # one load answering several targets

DIR is the tree `wdl_semantics.py` reads, by way of `wdl_semantics.wdl_files`: a `wdl/` subdirectory
is preferred when present (a gatk-sv clone), else `DIR` itself (a `scripts/fetch_wdl.py` ref dir).
`--repo` is accepted as a synonym for `--dir`, as in the sibling checkers.

NAME is a file (`Structs.wdl`), a callable (`MakeCohortVcf`, or `MakeCohortVcf.wdl::MakeCohortVcf`),
or a script basename (`mantatloccheck.sh`). A name that matches several nodes unions them and says
so — quietly choosing one is the same class of lie as an empty answer. A name that matches nothing is
named as unknown, lists the closest names the tree does know, and makes the run exit 2 (with several
names, the other names are still answered first).

`--json` writes ONE artifact for the whole run. `targets` holds every answer in the order given (each
block carries its own `nodes`/`counts`/`hits`/`reached`, plus `unknown` and `closest` for a name that
did not resolve); `unknown_targets` and `target_count` summarize the run. The older keys — `target`,
`counts`, `hits`, `reached` — still describe ONE target, the first name given, exactly as they did
when one name was the only possibility, so a single-target artifact only gained keys.

Exit codes: 0 answered (reached, or NOT REACHED — an orphan is an answer), 2 any target unknown / no
tree / a partial answer, 3 miniwdl missing. Loading a whole gatk-sv tree is ~35 s and the traversal
after it is instant, so ask several questions in one run (repeat `--target`, or `--json` once and
read the artifact) instead of one run per question.

No data, no docker, no network, nothing written but `--json`.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import json
import os
import pathlib
import re
import subprocess
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
        # abspath -> the miniwdl document, kept because `--images` reads input declarations off the
        # AST. The node records carry a rendered `owner` sentence for the table, which is not
        # something to re-derive a workflow's inputs from.
        self.docs = {}

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
        g.docs[abspath] = doc
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


def report_unknown(g: Graph, root, files, name: str) -> list:
    """The unknown-name answer for ONE target: what was looked for, plus that name's closest matches.

    Returns the close-match list so the artifact can carry it. Goes to stderr because an unknown name
    is not an answer, and must never be mistaken for the `NOT REACHED` that is one.

    The flush is load-bearing, not decoration: stdout is block-buffered when it is a pipe, so without
    it the answer blocks of the other targets would land AFTER this report and the printed order would
    not be the order the names were given.
    """
    known = sorted({n for rec in g.nodes.values() for n in rec['names']
                    if '::' not in n and '/' not in n})
    close = difflib.get_close_matches(name, known, n=6, cutoff=0.55)
    sys.stdout.flush()
    print(f'unknown target {name!r}: no file, workflow, task or script of that name is in '
          f'{root} ({len(files)} .wdl file(s) scanned, {len(g.nodes)} nodes known). A file target '
          f'needs its .wdl suffix; a script target is the basename as the command block spells it; '
          f'a workflow or task target is the bare name.', file=sys.stderr)
    if close:
        print('  closest: ' + ', '.join(close), file=sys.stderr)
    return close


def hit_records(g: Graph, hits: dict, reverse: bool) -> list:
    """One target's hits as artifact rows, shortest path first."""
    return [{'kind': g.nodes[n]['kind'], 'node': g.display(n), 'file': g.nodes[n]['file'],
             'owner': g.nodes[n]['owner'], 'depth': h['depth'], 'edge': h['via']['why'],
             'edge_kind': h['via']['kind'], 'edge_site': f'{h["via"]["file"]}:{h["via"]["line"]}',
             'chain': [g.display(x) for x in
                       (h['chain'] if reverse else list(reversed(h['chain'])))]}
            for n, h in sorted(hits.items(), key=lambda kv: (kv[1]['depth'], g.display(kv[0])))]


def target_answer(g: Graph, name: str, targets: list, hits: dict, reverse: bool) -> dict:
    """One NAME's slice of the artifact: what it resolved to and the reach answer about it.

    `unknown` separates the two ways this block can hold no hits: an unanswered name (`unknown: true`)
    and a name nothing reaches (`unknown: false`, `reached: false`) — the second is an answer, the
    first is not, and a reader must be able to tell them apart without parsing prose.
    """
    return {'name': name,
            'mode': 'reverse' if reverse else 'forward',
            'unknown': not targets,
            'nodes': [[g.nodes[n]['kind'], g.display(n), g.nodes[n]['file']] for n in targets],
            'counts': {'hits': len(hits),
                       'by_depth': {str(d): c for d, c in sorted(
                           collections.Counter(h['depth'] for h in hits.values()).items())},
                       'by_kind': dict(sorted(collections.Counter(
                           g.nodes[n]['kind'] for n in hits).items()))},
            'hits': hit_records(g, hits, reverse),
            'reached': bool(hits)}


def artifact(argv, root, files, answers, g, secs, failures, unresolved, outside) -> dict:
    """The ONE artifact for a whole run, however many names were asked.

    Compatibility by construction, not by rename: `target`, `counts`, `hits` and `reached` still
    describe one target — the FIRST name given, which for a single-target run is the only one — so an
    artifact from a one-name run differs from the old shape by ADDING `targets`, `unknown_targets`
    and `target_count`. `targets` is the whole run, in the order the names were given.
    """
    first = answers[0]
    return {'tool': 'wdl_reach.py', 'argv': argv, 'inputs': {'dir': str(root), 'files': len(files)},
            # the old three keys, plus `unknown`; the hits stay top-level (and in `targets`) rather
            # than being written a second time inside `target`.
            'target': {k: first[k] for k in ('name', 'mode', 'nodes', 'unknown')},
            'targets': answers,
            'unknown_targets': [a['name'] for a in answers if a['unknown']],
            'target_count': len(answers),
            'rule': {'edge_kinds': ['import: file -> file', 'call: workflow -> callable (miniwdl-resolved)',
                                    'invoke: task command block -> script basename',
                                    'contains: file -> its own workflows and tasks'],
                     'script_match': 'raw command block text, whole-line comments dropped, basename key',
                     'target_set_excluded_from_hits': True},
            'counts': {'nodes': len(g.nodes), 'edges': g.n_edges,
                       'hits': first['counts']['hits'], 'by_depth': first['counts']['by_depth'],
                       'by_kind': first['counts']['by_kind']},
            'imports_outside': [list(o) for o in outside],
            'unresolved_calls': [list(u) for u in unresolved],
            'load_failures': [list(f) for f in failures],
            'hits': first['hits'],
            'reached': first['reached'],
            'files_scanned': len(files), 'scanned_something': len(files) > 0,
            'load_seconds': round(secs, 2)}


# --- images: the containers the reached set binds, and whether the commit under review built them ----
# A11. `--target` answers which workflows a change reaches. The next question in the same review is
# which dockers have to be rebuilt — and the dangerous half of it: a reaching workflow bound to an
# image your commit did NOT build runs the pre-change code and prints nothing suspicious. Nothing new
# has to be invented to answer it, because every input is already data. This section reads the four
# places that hold it, and says which one answered:
#
#   the WDL                     a workflow's own `*_docker` input declaration, with its default
#                               expression as written, off `doc.workflow.inputs`
#   a rendered input JSON       `inputs/build/**/<Wf>.*json` — gatk-sv's own renderer's output, the
#                               file CI validates (from --inputs-root, or from a render this run asks
#                               `checks/wdl_inputs_check.py --render-only` for)
#   a module profile            `profiles/<module>.json` — what a Terra chain binds, which is
#                               `workspace.<key>`, a value the tree cannot see
#   inputs/values/dockers.json  gatk-sv's pinboard: the exact image:tag each input is expected to
#                               hold. Equality with it says "this value is committed into the tree",
#                               which is context for the verdict rather than the verdict itself
#
# The verdict itself comes from gatk-sv's own two tag conventions, read off the files that mint them:
# `docker/gatk-sv-build.sh` mints `${BRANCH_TAG}-${SHA:0:6}` and comments it "Branch-scoped TEST tags
# only", and `docs/docker-builds.md` reserves "release-style tags (v1.1, v1.1.1, date-prefixed upstream
# tags …)" for "production pushes". So a date- or `v`-prefixed tag is a production/CI push and cannot
# contain an edit that exists only on your branch, while a `<branch>-<sha>` tag names the commit that
# produced it — which is then compared against the commit under review. A value matching neither shape
# is printed as CANNOT-SAY rather than assumed into either bucket.
DOCKER_INPUT = re.compile(r'(?:^|_)docker$')      # every container input gatk-sv spells <name>_docker
JSON_KEY = re.compile(r'^(?P<wf>[A-Za-z0-9_]+)\.(?P<key>[A-Za-z0-9_.]+)$')
# `${workspace.sv_pipeline_docker}`: Rawls substitutes it at submission time, so no file in the tree
# holds the image a run actually gets.
PLACEHOLDER = re.compile(r'^\$\{[^}]*\}$')
RELEASE_TAG = re.compile(r'^(?:\d{4}-\d{2}-\d{2}|v\d)')
BRANCH_MINTED_TAG = re.compile(r'^(?P<body>[A-Za-z0-9][\w.-]*)-(?P<sha>[0-9a-f]{6,40})$')
QUOTED = re.compile(r'^"(.*)"$')
# The four verdict buckets, printed in this order, mutually exclusive, and 3 is as loud as 0.
BUCKETS = ('NOT A BUILD OF THE COMMIT UNDER REVIEW', 'BUILT FROM A DIFFERENT COMMIT',
           'BUILT FROM THE COMMIT UNDER REVIEW', 'CANNOT SAY')


def kit_path() -> None:
    """Put `kit/` on sys.path once, so `config` and `module_profile` are importable from `checks/`."""
    k = str(_HERE.parent / 'kit')
    if k not in sys.path:
        sys.path.insert(0, k)


def cfg_get(key: str) -> str:
    """One config key through the resolver, or '' when the config layer is unavailable."""
    try:
        kit_path()
        import config
    except ImportError:
        return ''
    return config.get(key, '')


def cfg_work(sub: str) -> str:
    """The scratch dir the config layer owns, created only because something is about to be written."""
    try:
        kit_path()
        import config
        return str(config.work_dir(sub))
    except Exception:                        # noqa: BLE001 — no config layer is a skip, not a crash
        d = pathlib.Path(tempfile.gettempdir()) / 'gsvtk-reach-images'
        d.mkdir(parents=True, exist_ok=True)
        return str(d)


def image_tag(value: str) -> str:
    """The tag of an image:tag, or '' when the value is untagged (the registry's current entry)."""
    last = value.rsplit('/', 1)[-1]
    return last.rsplit(':', 1)[1] if ':' in last else ''


def wrap(text: str, indent: str, width: int = 104) -> list:
    """`text` as indented lines. A reason that wraps to nothing is the reason nobody reads."""
    lines, cur = [], indent
    for word in text.split():
        if len(cur) + len(word) + 1 > width and cur.strip() != '':
            lines.append(cur.rstrip())
            cur = indent
        cur += (' ' if cur.strip() else '') + word
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def classify_image(value: str, pinboard: dict, head_sha: str) -> tuple:
    """(bucket, label, reason) for one literal image value. Never a silent "looks fine".

    Bucket 0 is the answer A11 exists for: a run binding this value tests an image that cannot contain
    the change under review. Bucket 3 (CANNOT SAY) is deliberately not folded into 2 — an unverified
    branch tag is the same failure class as a gate that certified nothing — so it gets its own counted
    line and its own reason, naming what would settle it.
    """
    tag = image_tag(value)
    keys = pinboard.get(value, [])
    pinned = ('dockers.json pins this value as ' + ', '.join(keys)) if keys \
        else 'no dockers.json key holds this value'
    if not tag:
        return 3, 'CANNOT-SAY', ('untagged: the registry serves whatever is current, so nothing in '
                                 'the tree says which build a run gets; ' + pinned)
    if RELEASE_TAG.match(tag):
        return 0, 'PRE-CHANGE', ('release-shaped tag ' + repr(tag) + ': docs/docker-builds.md '
                                 'reserves date-prefixed and vN tags for production pushes, so this '
                                 'image cannot hold an edit that exists only on your branch; ' + pinned)
    m = BRANCH_MINTED_TAG.match(tag)
    if not m:
        return 3, 'CANNOT-SAY', ('tag ' + repr(tag) + ' matches neither shape this repo can read '
                                 '(production release-style, or <branch>-<sha> from '
                                 'docker/gatk-sv-build.sh); ' + pinned)
    sha = m.group('sha')
    if not head_sha:
        return 3, 'BRANCH-UNVERIFIED', ('branch-minted tag ' + repr(tag) + ' carries commit ' + sha[:7]
                                        + ', but the commit under review is unknown here (name the '
                                        'checkout, or pass --head-sha), so its freshness is not '
                                        'checkable; ' + pinned)
    if head_sha.startswith(sha):
        return 2, 'BRANCH-BUILT', ('branch-minted tag ' + repr(tag) + ' carries commit ' + sha[:7]
                                   + ', which is the commit under review; ' + pinned)
    return 1, 'STALE-BRANCH-BUILD', ('branch-minted tag ' + repr(tag) + ' carries commit ' + sha[:7]
                                     + ', which is not the commit under review (' + head_sha[:7]
                                     + '): a run binding it tests that older build; ' + pinned)


def default_as_written(doc, decl) -> str:
    """A declaration's default expression exactly as the source spells it, or '' when it has none.

    Sliced out of the file's own lines rather than rendered off the AST: miniwdl's expression node has
    no stable source-text attribute across versions, and a default like
    `if (!defined(manta_vcfs_input) && use_manta) then manta_docker else NONE_STRING_` is worth
    quoting to a human rather than reconstructing from nodes.
    """
    expr = getattr(decl, 'expr', None)
    pos = getattr(expr, 'pos', None)
    if expr is None or pos is None:
        return ''
    try:
        line = doc.source_lines[pos.line - 1]
    except IndexError:
        return ''
    return line[pos.column - 1:].strip()


def docker_input_decls(doc) -> list:
    """(name, type_text, default_as_written, line) for the container inputs a workflow DECLARES.

    Off the AST's input section, not off a name grep: gatk-sv's top-level workflows carry derived body
    declarations such as `String? manta_docker_ = if (!defined(manta_vcfs_input) …)`
    (GATKSVPipelineBatch.wdl:158-161) that a caller cannot bind, and reporting them as inputs would
    invent a binding surface the workflow does not have.
    """
    wf = getattr(doc, 'workflow', None)
    out = []
    for name, decl in sorted((getattr(wf, 'inputs', None) or {}).items()):
        if not DOCKER_INPUT.search(name):
            continue
        out.append((name, str(decl.type), default_as_written(doc, decl), decl.pos.line))
    return out


def read_input_jsons(build_root: str) -> tuple:
    """(index, files_read, unreadable, call_level_keys) over every rendered JSON under build_root.

    The index is keyed by whatever precedes the first dot, which is the workflow an input JSON names
    its keys after. A key with a dot inside the input part (`Wf.call.sv_pipeline_docker`) binds a CALL
    rather than a workflow input, so it is counted and not folded in silently: it answers a different
    question, and the count is what tells a reader the scan saw it.
    """
    index, n, bad, call_level = {}, 0, [], 0
    root = pathlib.Path(build_root)
    for p in sorted(root.rglob('*.json')):
        try:
            doc = json.loads(p.read_text())
        except (ValueError, OSError) as exc:
            bad.append((p.name, str(exc)[:80]))
            continue
        if not isinstance(doc, dict):
            bad.append((p.name, 'not a JSON object'))
            continue
        n += 1
        for key, value in doc.items():
            m = JSON_KEY.match(str(key))
            if not m or not DOCKER_INPUT.search(m.group('key')):
                continue
            if '.' in m.group('key'):
                call_level += 1
                continue
            index.setdefault(m.group('wf'), {}).setdefault(m.group('key'), []).append(
                (value, str(p.relative_to(root))))
    return index, n, bad, call_level


def read_pinboard(tree: str) -> tuple:
    """inputs/values/dockers.json as value -> [keys], plus (status, reason). Context, not a verdict."""
    p = pathlib.Path(tree) / 'inputs' / 'values' / 'dockers.json' if tree else None
    if p is None or not p.is_file():
        return {}, 'skipped', 'no inputs/values/dockers.json under ' + str(tree or '(no tree read)')
    try:
        doc = json.loads(p.read_text())
    except (ValueError, OSError) as exc:
        return {}, 'skipped', str(p) + ' could not be read (' + str(exc)[:80] + ')'
    pin = {}
    for key, value in doc.items():
        if isinstance(value, str):
            pin.setdefault(value, []).append(str(key))
    for keys in pin.values():
        keys.sort()
    return pin, 'read', str(p)


def read_profile_bindings() -> tuple:
    """profiles/<module>.json as workflow -> input -> (value, step), through the ONE profile reader.

    `kit/module_profile` is the only loader allowed to interpret a profile (one reader, one expander),
    so its refusals arrive here as findings and are printed as a named skip rather than reading as
    "the profile binds nothing" — which is the difference between a source that was absent and one
    that was present and refused.
    """
    try:
        kit_path()
        import module_profile
    except ImportError as exc:
        return {}, 'skipped', 'kit/module_profile.py could not be imported (' + str(exc) + ')'
    loaded = module_profile.load()
    if not loaded.found or loaded.problems:
        why = loaded.problem.replace('\n', ' ') or 'the profile bound no steps'
        return {}, 'skipped', str(loaded.path) + ': ' + why
    out = {}
    for step, spec in loaded.configs.items():
        wf = str(spec.get('workflow') or '')
        for key, value in (spec.get('inputs') or {}).items():
            head, _, member = str(key).partition('.')
            if member and DOCKER_INPUT.search(member):
                out.setdefault(head or wf, {}).setdefault(member, (value, step))
    return out, 'read', str(loaded.path)


def render_inputs(checkout: str, ref: str, dest: str) -> dict:
    """Ask `checks/wdl_inputs_check.py --render-only` for gatk-sv's own rendered input JSONs.

    THE COUPLING, stated where a reviewer can see it: that file belongs to another lane, and this
    function depends on its `--repo/--ref/--dest/--render-only` flags, on the machine line
    `GSVTK-RENDER status=… jsons=… dest=…` it prints, and on the layout it leaves behind
    (`<dest>/inputs/build/**.json`, plus `<dest>/inputs/values/dockers.json`, which travels because
    the git archive takes the whole `inputs/` subtree). It is invoked, never edited, and it renders
    from `git archive` into a temp tree, so the checkout stays read-only. If any of that changes, the
    answer here degrades to a named skip with UNRESOLVED values — never to a fabricated image.
    """
    script = _HERE / 'wdl_inputs_check.py'
    if not script.is_file():
        return {'status': 'skipped', 'reason': str(script) + ' is not here'}
    cmd = [sys.executable, str(script), '--render-only', '--repo', checkout, '--ref', ref,
           '--dest', dest]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        return {'status': 'skipped', 'reason': 'could not run ' + ' '.join(cmd) + ' (' + str(exc) + ')'}
    out = proc.stdout + proc.stderr
    line = next((ln for ln in out.splitlines() if ln.startswith('GSVTK-RENDER')), '')
    if not line:
        tail = out.strip().splitlines()[-1:]
        return {'status': 'skipped',
                'reason': 'no GSVTK-RENDER line from ' + script.name + ' (rc=' + str(proc.returncode)
                          + (', ' + tail[0][:120] if tail else '') + '); the coupling to it is broken '
                          + 'and no input JSON was read'}
    fields = dict(t.split('=', 1) for t in line.split() if '=' in t)
    if fields.get('status') != 'OK':
        return {'status': 'skipped', 'reason': line[len('GSVTK-RENDER'):].strip(),
                'jsons': int(fields.get('jsons') or 0)}
    return {'status': 'read', 'reason': line, 'jsons': int(fields.get('jsons') or 0),
            'dest': fields.get('dest', dest)}


def git_head(what: str, ref: str) -> tuple:
    """(sha, whence) for REF in a checkout, read-only. '' plus a reason when there is no commit."""
    if not what or not os.path.isdir(os.path.join(what, '.git')):
        return '', 'no git checkout in play'
    try:
        proc = subprocess.run(['git', '-C', what, 'rev-parse', ref], capture_output=True,
                              text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return '', 'git rev-parse failed (' + str(exc) + ')'
    if proc.returncode != 0:
        return '', 'git rev-parse ' + ref + ' failed: ' + (proc.stderr or '').strip()[:80]
    return (proc.stdout or '').strip(), 'git rev-parse ' + ref + ' in ' + what


def gather_image_sources(a, root) -> dict:
    """Everything the image answer reads — with a reason attached to each part, including the misses."""
    src = {'asked': True, 'json_root': '', 'json_status': 'skipped', 'json_reason': '',
           'json_files': 0, 'json_bad': [], 'call_level': 0, 'index': {},
           'pinboard': {}, 'pinboard_status': 'skipped', 'pinboard_reason': '',
           'profile': {}, 'profile_status': 'skipped', 'profile_reason': '', 'profile_path': '',
           'head_sha': '', 'head_whence': '', 'render': None}
    tree = ''
    if a.inputs_root:
        tree = a.inputs_root
        candidate = os.path.join(a.inputs_root, 'inputs', 'build')
        if os.path.isdir(candidate):
            src['json_root'] = candidate
            src['json_reason'] = 'given by --inputs-root '
        else:
            src['json_reason'] = ('no inputs/build under --inputs-root ' + a.inputs_root
                                  + ' (render one with checks/wdl_inputs_check.py --render-only)')
    else:
        checkout = (a.checkout or (str(root) if os.path.isdir(os.path.join(str(root), 'inputs'))
                                   else '') or cfg_get('GATK_SV_CHECKOUT'))
        if not checkout:
            src['json_reason'] = ('nothing to render from: GSVTK_GATK_SV_CHECKOUT is unset and neither '
                                  '--inputs-root nor --checkout was given')
        else:
            dest = os.path.join(cfg_work('reach-images'),
                                'render-' + re.sub(r'[^\w.-]', '-', a.inputs_ref))
            src['render'] = render_inputs(checkout, a.inputs_ref, dest)
            if src['render']['status'] == 'read':
                tree = src['render'].get('dest') or dest
                src['json_root'] = os.path.join(tree, 'inputs', 'build')
                src['json_reason'] = ('rendered by checks/wdl_inputs_check.py --render-only from '
                                      + checkout + '@' + a.inputs_ref + ', '
                                      + str(src['render']['jsons']) + ' JSON(s)')
            else:
                src['json_reason'] = 'the renderer said: ' + src['render']['reason']
    if src['json_root']:
        src['index'], src['json_files'], src['json_bad'], src['call_level'] = \
            read_input_jsons(src['json_root'])
        src['json_status'] = 'read'
    src['pinboard'], src['pinboard_status'], src['pinboard_reason'] = read_pinboard(tree)
    src['profile'], src['profile_status'], src['profile_reason'] = read_profile_bindings()
    if src['profile_status'] == 'read':
        src['profile_path'] = src['profile_reason']
    if a.head_sha:
        src['head_sha'], src['head_whence'] = a.head_sha, '--head-sha'
    else:
        src['head_sha'], src['head_whence'] = git_head(tree or a.checkout or cfg_get('GATK_SV_CHECKOUT'),
                                                       a.inputs_ref)
    return src


def answer_workflows(g: Graph, targets: list, hits: dict) -> tuple:
    """Every workflow in ONE answer, with the role that put it there. Nothing drops for graph reasons.

    A reverse answer from a task in a library file reaches FILES, not workflow nodes: the callers are
    reached file -> file -> contains -> task, so the workflow has to come out of the file (gatk-sv
    carries one workflow per file, and the reach table's OWNER column already names it). Both spellings
    are read here, and the tallies come back so the printed arithmetic is checkable: answer nodes by
    kind, how many workflows that is, how many deduped, and which reached files hold no workflow at all.
    """
    entries, plain, other = [], [], 0
    seen, deduped = {}, 0
    by_kind = collections.Counter()

    def add(abspath, depth, role):
        nonlocal deduped
        doc = g.docs.get(abspath)
        wf = getattr(doc, 'workflow', None) if doc is not None else None
        display = pathlib.Path(abspath).name
        if wf is None:
            if doc is not None and display not in plain:
                plain.append(display)
            return
        key = (abspath, str(wf.name))
        if key in seen:
            seen[key]['roles'] += ' + ' + role
            seen[key]['depth'] = min(seen[key]['depth'], depth)
            deduped += 1
            return
        seen[key] = {'file': display, 'workflow': str(wf.name), 'abspath': abspath, 'doc': doc,
                     'depth': depth, 'roles': role}
        entries.append(seen[key])

    for node in targets:
        kind = g.nodes.get(node, {}).get('kind', 'unresolved')
        by_kind[kind] += 1
        if kind == 'file' or kind == 'workflow':
            add(node[1], 0, 'target')
        else:
            other += 1
    for node, hit in sorted(hits.items(), key=lambda kv: (kv[1]['depth'], g.display(kv[0]))):
        kind = g.nodes[node]['kind']
        by_kind[kind] += 1
        if kind == 'file':
            add(node[1], hit['depth'], 'depth ' + str(hit['depth']) + ' file')
        elif kind == 'workflow':
            add(node[1], hit['depth'], 'depth ' + str(hit['depth']) + ' workflow')
        else:
            other += 1
    entries.sort(key=lambda e: (e['depth'], e['file']))
    tally = {'answer_nodes': len(targets) + len(hits),
             'by_kind': {k: by_kind[k] for k in KINDS if by_kind[k]},
             'workflows': len(entries), 'deduped': deduped, 'no_workflow_files': sorted(plain),
             'non_workflow_nodes': other}
    return entries, tally


def bindings_of(entry: dict, key: str, default_text: str, default_line: int, src: dict) -> list:
    """[(source_label, value, where)] binding ONE docker input, from every place that binds it.

    The workflow name and the file basename are both tried against the rendered JSONs and the label
    says which spelling hit: a profile keys by `workflow` while a template keys by whatever the file
    is called, and 12 of the 109 workflow-bearing WDLs at main have those two names differ. A binding
    missed because of a spelling is the silent omission this block exists to refuse.
    """
    out = []
    if default_text:
        out.append(('WDL default', default_text, entry['file'] + ':' + str(default_line)))
    index = src['index']
    got, label = index.get(entry['workflow'], {}).get(key), 'rendered input JSON'
    if not got:
        got = index.get(pathlib.Path(entry['abspath']).stem, {}).get(key)
        label = 'rendered input JSON (matched by file stem, not by workflow name)'
    for value, where in (got or []):
        out.append((label, value, where))
    prof = src['profile'].get(entry['workflow'], {}).get(key) \
        or src['profile'].get(pathlib.Path(entry['abspath']).stem, {}).get(key)
    if prof:
        out.append(('module profile ' + os.path.basename(src['profile_path']), prof[0],
                    'step ' + prof[1]))
    return out


def literal_of(source_label: str, value) -> tuple:
    """(literal_image, why_not) for one binding: '' plus a reason when it is not a literal image.

    A WDL default that is an expression, a `${workspace.x}` placeholder and a `workspace.x` profile
    binding are all real bindings and none of them is an image, so each gets its own reason instead of
    being dropped or quoted back as if it were a value a runner could pull.
    """
    text = str(value).strip()
    if source_label == 'WDL default':
        q = QUOTED.match(text)
        if q:
            return q.group(1), ''
        return '', ('the WDL default is the expression ' + repr(text)
                    + ', which is computed at run time, not a literal image')
    if PLACEHOLDER.match(text):
        return '', (repr(text) + ' is a Rawls placeholder substituted at submission time, so no file '
                    'in the tree says which image a run gets')
    if text.startswith(('workspace.', 'this.')):
        return '', (repr(text) + ' names an entity attribute rather than an image; its value lives in '
                    'the workspace, not in this tree')
    return text, ''


def image_answer(entries: list, src: dict) -> tuple:
    """(rows, bindings, unresolved) for ONE answer: what each reaching workflow binds, and from where.

    Every workflow in `entries` gets a row even when it binds nothing, because a per-workflow list
    that quietly leaves one out is the exact lie this feature was added to prevent.
    """
    rows, bindings, unresolved = [], [], []
    for entry in entries:
        inputs = []
        for name, type_text, default_text, line in docker_input_decls(entry['doc']):
            values, holes = [], []
            for label, value, where in bindings_of(entry, name, default_text, line, src):
                literal, why = literal_of(label, value)
                if literal:
                    bucket, verdict, reason = classify_image(literal, src['pinboard'], src['head_sha'])
                    values.append({'source': label, 'where': where, 'image': literal,
                                   'bucket': bucket, 'verdict': verdict, 'reason': reason})
                else:
                    holes.append({'source': label, 'value': str(value), 'where': where, 'reason': why})
            if not values and not holes:
                holes.append({'source': 'nothing binds it', 'value': None, 'where': '',
                              'reason': 'the WDL declares ' + type_text + ' ' + name + ' at '
                                        + entry['file'] + ':' + str(line) + ' with no default, and no '
                                        'rendered input JSON and no module profile binds '
                                        + entry['workflow'] + '.' + name})
            inputs.append({'input': name, 'type': type_text, 'wdl_line': line,
                           'values': values, 'holes': holes})
        rows.append({'workflow': entry['workflow'], 'file': entry['file'], 'depth': entry['depth'],
                     'roles': entry['roles'], 'inputs': inputs})
        for inp in inputs:
            for v in inp['values']:
                bindings.append(dict(v, wf=entry['workflow'] + '.' + inp['input']))
            for h in inp['holes']:
                unresolved.append({'wf': entry['workflow'] + '.' + inp['input'],
                                   'reason': h['reason']})
    return rows, bindings, unresolved


def print_image_answer(rows: list, tally: dict, top: int, verbose: bool) -> None:
    """The per-answer image block: every workflow in the answer, including the ones binding none."""
    kinds = ' '.join(k + '=' + str(v) for k, v in sorted(tally['by_kind'].items()))
    n_none = sum(1 for r in rows if not r['inputs'])
    print('images: ' + str(tally['workflows']) + ' workflow(s) in this answer, ' + str(len(rows))
          + ' listed (' + str(n_none) + ' of them bind no docker input)')
    print('  from ' + str(tally['answer_nodes']) + ' answer node(s) ' + kinds
          + ('; ' + str(tally['deduped']) + ' dedup(s)' if tally['deduped'] else ''))
    if tally['no_workflow_files']:
        listed = ', '.join(tally['no_workflow_files'][:6])
        if len(tally['no_workflow_files']) > 6:
            listed += ' (+' + str(len(tally['no_workflow_files']) - 6) + ' more)'
        print('  ' + str(len(tally['no_workflow_files'])) + ' reached file(s) hold no workflow, so they '
              'bind no docker input of their own: ' + listed)
    if tally['non_workflow_nodes']:
        print('  ' + str(tally['non_workflow_nodes']) + ' answer node(s) are tasks, scripts or '
              'unresolved calls: they run inside someone else\'s image, named by their caller above')
    shown = rows if verbose else rows[:top]
    for row in shown:
        print('  ' + row['file'] + '::' + row['workflow'] + '  [' + row['roles'] + ']' +
              ('   binds NO docker input' if not row['inputs'] else
               '   ' + str(len(row['inputs'])) + ' docker input(s)'))
        for inp in row['inputs']:
            for v in inp['values']:
                print('      ' + inp['input'].ljust(24) + v['verdict'].ljust(20) + v['image'])
                for ln in wrap('<- ' + v['source'] + ': ' + v['where'] + '   |   ' + v['reason'],
                               ' ' * 26):
                    print(ln)
            for h in inp['holes']:
                print('      ' + inp['input'].ljust(24) + 'UNRESOLVED')
                for ln in wrap('<- ' + h['reason'], ' ' * 26):
                    print(ln)
    if len(rows) > len(shown):
        print('  … detail for ' + str(len(rows) - len(shown)) + ' more workflow(s) withheld (-v); '
              'their bindings are still counted in the verdict block')


def image_verdict(bindings: list, unresolved: list) -> dict:
    """The distinction, not the list: which of these images cannot be a build of what is under review.

    Exit codes stay reach's business: an UNRESOLVED binding is an answer about the data (CI renders no
    JSON for some workflows at all, and a Terra config binds a workspace attribute by design), so it is
    counted and named rather than failed. A layer that could not run at all is a different thing and is
    refused in main().
    """
    by_value = {}
    for b in bindings:
        rec = by_value.setdefault(b['image'], {'bucket': b['bucket'], 'verdict': b['verdict'],
                                               'reason': b['reason'], 'wfs': []})
        rec['wfs'].append(b['wf'])
    counts = collections.Counter(b['bucket'] for b in bindings)
    for value, rec in by_value.items():
        rec['wfs'] = sorted(set(rec['wfs']))
    lines = ['images: ' + str(len(by_value)) + ' distinct image value(s) in ' + str(len(bindings))
             + ' binding(s)']
    for bucket, name in enumerate(BUCKETS):
        values = sorted((v, r) for v, r in by_value.items() if r['bucket'] == bucket)
        if not values:
            continue
        lines.append('  [' + str(bucket) + '] ' + name + ' (' + str(len(values)) + ')')
        for value, rec in values:
            wfs = rec['wfs']
            listed = ', '.join(wfs[:4]) + (' (+' + str(len(wfs) - 4) + ' more)' if len(wfs) > 4 else '')
            lines += wrap(value + '   [' + rec['verdict'] + ']   bound by: ' + listed, '    ')
            lines += wrap(rec['reason'], '        ')
    if not bindings:
        lines.append('  no docker-shaped image is bound by anything in this answer: every reaching '
                     'workflow binds none, which is an answer rather than a gap')
    if unresolved:
        lines.append('  binding(s) whose value is not a literal image (' + str(len(unresolved)) + '):')
        for u in unresolved:
            lines += wrap(u['wf'] + ': ' + u['reason'], '    ')
    return {'lines': lines, 'values': by_value,
            'buckets': {str(b): counts[b] for b in range(len(BUCKETS))},
            'unresolved': unresolved, 'bindings': len(bindings)}


def image_provenance(src: dict) -> list:
    """Where each value came from — including the source that could not be read, and why."""
    if src['json_status'] == 'read':
        json_line = ('read ' + str(src['json_files']) + ' JSON(s) from ' + src['json_root']
                     + ' (' + src['json_reason'] + ')')
    else:
        json_line = 'SKIPPED — ' + src['json_reason'] + ': every value below is a WDL default or '
        json_line += 'UNRESOLVED'
    if src['pinboard_status'] == 'read':
        pin_line = (str(len(src['pinboard'])) + ' value(s) from ' + src['pinboard_reason'])
    else:
        pin_line = ('SKIPPED — ' + src['pinboard_reason'] + ': the "is this value committed into the '
                    'tree" context is absent from every verdict below')
    if src['profile_status'] == 'read':
        prof_line = (str(sum(len(v) for v in src['profile'].values())) + ' docker binding(s) from '
                     + src['profile_path'])
    else:
        prof_line = 'SKIPPED — ' + src['profile_reason'] + ': no module-profile binding was consulted'
    head_line = (src['head_sha'] + ' (' + src['head_whence'] + ')') if src['head_sha'] else \
        'UNKNOWN — ' + src['head_whence'] + ', so a <branch>-<sha> tag cannot be checked against it'
    lines = ['image sources:', '  rendered input JSON: ' + json_line,
             '  dockers.json pinboard: ' + pin_line, '  module profile: ' + prof_line,
             '  commit under review: ' + head_line]
    if src['call_level']:
        lines.append('  ' + str(src['call_level']) + ' docker key(s) in those JSON(s) bind a CALL '
                     '(dotted key), not a workflow input: counted, not folded into the answer')
    for path, why in src['json_bad']:
        lines.append('  JSON unreadable: ' + path + ' (' + why + ')')
    return lines




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

    # --- several targets, ONE load ---------------------------------------------------------------
    # The claim is about the LOAD, not only the answers. `tree:` is main()'s provenance line and is
    # printed once per build_graph(), so COUNTING it counts loads: an assertion that only required both
    # answers to appear would still pass if the tool re-parsed the tree per name (which is the ~35 s
    # cost this path exists to remove), and would pass if it answered the first name and dropped the
    # rest, because `out` holds both either way. Hence the count, the block count, and the order.
    def n_lines(text, prefix):
        """How many LINES start with PREFIX — a count, not a substring match.

        Anchored on purpose: an unanchored `tree: ` also matches the tail of a printed path ending in
        `.../reach-tree: 3 nodes`, which turns a count of loads into a count of sentences.
        """
        return len(re.findall(r'(?m)^' + re.escape(prefix), text))

    def in_order(text, *markers):
        """Every marker printed, each BEFORE the next: the printed order is the order asked.

        `find` and not `index`, so a missing block reads as a failed assertion rather than a
        ValueError traceback — an assertion that crashes instead of naming the marker that went absent
        cannot tell the next person which answer is missing, which is the thing under test.
        """
        at = [text.find(m) for m in markers]
        return all(i >= 0 for i in at) and at == sorted(at) and len(set(at)) == len(at)

    rc, out = run('--target', 'a.wdl', '--target', 'c.wdl', '--reverse')
    want(rc == 0, 'two names that both resolve exit 0: an orphan plus a reach table is not an error',
         f'rc={rc}')
    want(n_lines(out, 'tree: ') == 1, 'two names, ONE tree load: the tree/provenance line appears ONCE',
         f'{n_lines(out, "tree: ")} line(s)')
    want(n_lines(out, 'target: ') == 2, 'and each name gets its OWN answer block',
         f'{n_lines(out, "target: ")} block(s)')
    want('NOT REACHED' in out and 'scanned 4 .wdl file(s)' in out,
         'the first name prints its orphan answer, naming the files scanned, as before')
    want('reach:' in out and 'b.wdl' in out and 'imports b.wdl at a.wdl:3' in out,
         'the second name is answered too: its own reach table, naming the hop that carried a.wdl')
    want(in_order(out, 'target: a.wdl', 'NOT REACHED', 'target: c.wdl', 'reach:'),
         'the blocks come in the ORDER ASKED (a.wdl first), not sorted and not first-only')
    rc, out = run('--target', 'c.wdl', '--target', 'c.wdl', '--reverse')
    want(rc == 0 and n_lines(out, 'target: ') == 2,
         'the same name twice gets two blocks: a caller\'s list is neither deduped nor sorted',
         f'rc={rc}, {n_lines(out, "target: ")} block(s)')

    # an unknown name among several: answer what resolves, report each unknown as itself, exit 2
    rc, out = run('--target', 'c.wl', '--target', 'c.wdl', '--target', 'd.wl', '--reverse')
    want(rc == 2, 'one unknown among answered names exits 2 (a batch is not allowed to lose a name)',
         f'rc={rc}')
    want("unknown target 'c.wl'" in out and "unknown target 'd.wl'" in out,
         'each unknown name is reported by name, not lumped into one complaint')
    want(out.count('closest:') == 2, 'each unknown carries its OWN closest-name list',
         f'{out.count("closest:")} list(s)')
    want('closest: c.wdl,' in out and 'closest: d.wdl,' in out,
         'and each list is that name\'s neighbours (c.wl -> c.wdl, d.wl -> d.wdl), not one shared list')
    want('target: c.wdl [file]' in out and 'b.wdl' in out,
         'the name that does resolve is still answered, in the middle of the failures')
    want(in_order(out, "unknown target 'c.wl'", 'target: c.wdl', "unknown target 'd.wl'"),
         'unknown names are reported IN PLACE, so the printed order is still the order asked')

    def artifact_at(path):
        """The artifact a run wrote, or {} when it wrote none: a missing artifact is a FAILed
        assertion below, not a FileNotFoundError traceback that hides the other assertions."""
        return json.loads(path.read_text()) if path.exists() else {}

    # One artifact for the whole run. The first name is deliberately the one nothing reaches, so an
    # implementation that reported the UNION or the LAST answer at top level could not pass.
    jp = root / 'multi.json'
    rc, out = run('--target', 'a.wdl', '--target', 'c.wdl', '--reverse', '--json', str(jp))
    art = artifact_at(jp)
    blocks = art.get('targets', [])
    want(rc == 0 and [b['name'] for b in blocks] == ['a.wdl', 'c.wdl'],
         '--json writes ONE artifact covering every target, in the order given', f'rc={rc}')
    want(art.get('unknown_targets') == [] and art.get('target_count') == 2,
         'the run-level keys count the names asked, not the nodes matched',
         str(art.get('unknown_targets')))
    want(len(blocks) == 2 and blocks[0]['reached'] is False and blocks[1]['reached'] is True
         and art.get('counts', {}).get('hits') == 0 and len(blocks[1]['hits']) > 0,
         'the old top-level keys describe the FIRST target (not a union), so a single-target reader '
         'sees what it saw before')
    jp2 = root / 'unknown.json'
    rc, out = run('--target', 'c.wdl', '--target', 'zz_nope_zz', '--reverse', '--json', str(jp2))
    art2 = artifact_at(jp2)
    blocks2 = art2.get('targets', [])
    want(rc == 2 and art2.get('unknown_targets') == ['zz_nope_zz']
         and len(blocks2) == 2 and blocks2[0]['unknown'] is False and blocks2[1]['unknown'] is True,
         'an unanswered block is marked unknown=true, so it can never be read as NOT REACHED',
         f'rc={rc}')

    if bad:
        print('selftest: FAIL\n  ' + '\n  '.join(bad))
        return 1
    print(f'selftest: ok — {len(FIXTURES)} fixtures, {len(g.nodes)} nodes, {g.n_edges} edges; '
          f'the chain resolves in both directions, the orphan is named as one, and ONE load answers '
          f'several targets in the order asked')
    return 0


# --- main --------------------------------------------------------------------------------------------
def main(argv: list) -> int:
    ap = argparse.ArgumentParser(
        prog='wdl_reach.py', description=__doc__.split('\n\n')[0],
        epilog='forward = what it reaches, --reverse = what reaches it. NOT REACHED is an answer; an '
               'unknown name is not, and stays unknown when it is one name among several: every '
               'unknown --target is reported and exits 2, whatever the others answered. A whole '
               'gatk-sv tree takes ~35 s to load and the walk after it is free, so ask several '
               'questions in ONE run by repeating --target.')
    ap.add_argument('--dir', '--repo', dest='dir',
                    help='tree of *.wdl: a gatk-sv clone (DIR/wdl preferred) or a fetched ref dir')
    ap.add_argument('--target', action='append', metavar='NAME',
                    help='file (Structs.wdl), callable (MakeCohortVcf / File.wdl::Name), or script '
                         '(mantatloccheck.sh). Repeatable: one tree load answers every name, one '
                         'answer block each, in the order given. A name that matches nothing is '
                         'reported on its own with its closest matches and the run exits 2 — the '
                         'other names are still answered, because NOT REACHED is an answer and an '
                         'unknown name is not')
    ap.add_argument('--reverse', action='store_true',
                    help='what reaches the target, instead of what the target reaches')
    ap.add_argument('--images', action='store_true',
                    help='also print the container images the reached set binds: per reaching '
                         'workflow the *_docker inputs it binds, the value, and WHERE that value '
                         'comes from (a WDL default, a rendered input JSON, a module profile). Then '
                         'the distinction that matters, not the list: which of those images cannot be '
                         'a build of the commit under review, so a run binding one tests a pre-change '
                         'image. A workflow that binds no docker input is printed as binding none; a '
                         'value that cannot be resolved prints as UNRESOLVED with its reason. Costs '
                         'a tree load plus ~2 s of input rendering; adds no lines unless you ask.')
    ap.add_argument('--inputs-root', metavar='DIR',
                    help='with --images: a tree holding inputs/build/**.json (a rendered gatk-sv '
                         'root, or a --render-only destination) to read image values and '
                         'inputs/values/dockers.json from, instead of rendering one')
    ap.add_argument('--checkout', metavar='DIR',
                    help='with --images: the gatk-sv clone to render the input JSONs from '
                         '(default: --dir when it holds inputs/, else GSVTK_GATK_SV_CHECKOUT)')
    ap.add_argument('--inputs-ref', default='HEAD', metavar='REF',
                    help='with --images: the ref to render the input JSONs from and to take the '
                         'commit-under-review from (default HEAD, because the question is about '
                         'YOUR branch; wdl_inputs_check.py alone defaults to origin/main)')
    ap.add_argument('--head-sha', metavar='SHA',
                    help='with --images: the commit a run with these bindings would be testing '
                         '(default: the checkout answers it via git rev-parse --inputs-ref). It is '
                         'what a <branch>-<sha> image tag is compared against, and naming it is how '
                         'you ask the question about a commit the checkout is not sitting on')
    ap.add_argument('--json', metavar='PATH',
                    help='write ONE machine-readable artifact here, covering every --target given')
    ap.add_argument('--top', type=int, default=25, help='hits shown before truncating (default 25)')
    ap.add_argument('-v', '--verbose', action='store_true', help='list every hit, with its full chain')
    ap.add_argument('--selftest', action='store_true',
                    help='4 fixture WDLs: both directions, the orphan, and several targets on one load')
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

    # --images reads a second set of files (input JSONs, dockers.json, a profile) that the graph does
    # not need, so it is gathered once here and printed per answer below. Nothing is printed at all on
    # a run that did not ask for it.
    img_src = gather_image_sources(a, root) if a.images else None
    if img_src is not None:
        for line in image_provenance(img_src):
            print(line)

    # One load above, then one answer block per name below, in the order the caller listed them. The
    # tree is never re-parsed per name: the ~35 s is the load, and N processes would pay it N times.
    answers, unknown = [], []
    image_bindings, run_verdict = [], None
    for i, name in enumerate(a.target):
        if i:
            print()                       # separates this answer block from the previous one
        nodes = find_targets(g, name)
        if not nodes:
            unknown.append(name)
            ans = target_answer(g, name, (), {}, a.reverse)
            ans['closest'] = report_unknown(g, root, files, name)
            answers.append(ans)
            continue
        if len(nodes) > 1:
            print(f'  NOTE {name} names {len(nodes)} nodes; the answer below is their union: '
                  + ', '.join(f'{g.display(n)} [{g.nodes[n]["kind"]}]' for n in nodes))
        hits = traverse(g, nodes, a.reverse)
        report(g, root, files, nodes, hits, a.reverse, a.top, a.verbose)
        ans = target_answer(g, name, nodes, hits, a.reverse)
        if img_src is not None:
            entries, tally = answer_workflows(g, nodes, hits)
            rows, bindings, unres = image_answer(entries, img_src)
            print_image_answer(rows, tally, a.top, a.verbose)
            verdict = image_verdict(bindings, unres)
            for line in verdict['lines']:
                print(line)
            image_bindings += bindings
            ans['images'] = {'workflows': tally, 'rows': rows, 'buckets': verdict['buckets'],
                             'values': verdict['values'], 'unresolvable': unres}
        answers.append(ans)

    if img_src is not None and sum(1 for ans in answers if not ans['unknown']) > 1:
        # More than one name answered: the union is the list of dockers to rebuild, and it is not the
        # same list as any single answer's. Printed once, over the answered names only — an unknown
        # name contributed nothing to it, which the UNKNOWN TARGETS report below already says.
        union_unres = [u for ans in answers for u in (ans.get('images') or {}).get('unresolvable', [])]
        run_verdict = image_verdict(image_bindings, union_unres)
        print('\nimages: the whole run (' + str(sum(1 for ans in answers if not ans['unknown']))
              + ' answered target(s), the union, not one answer\'s list)')
        for line in run_verdict['lines']:
            print(line)

    if a.json:
        art = artifact(argv, root, files, answers, g, secs, failures, unresolved, outside)
        if img_src is not None:
            art['images'] = {
                'sources': {k: img_src[k] for k in ('json_root', 'json_status', 'json_reason',
                                                    'json_files', 'call_level', 'pinboard_status',
                                                    'pinboard_reason', 'profile_status',
                                                    'profile_reason', 'profile_path', 'head_sha',
                                                    'head_whence')},
                'json_unreadable': img_src['json_bad'],
                'rule': 'bucket 0 = the tag is release-shaped (date-prefixed or vN), which '
                        'docs/docker-builds.md reserves for production pushes, so it cannot contain '
                        'an edit that exists only on the commit under review; bucket 1/2 = a '
                        '<branch>-<sha> tag as docker/gatk-sv-build.sh mints it, compared against '
                        'the commit under review; bucket 3 = the tree cannot tell',
                'run': run_verdict['buckets'] if run_verdict else None,
                'run_values': run_verdict['values'] if run_verdict else None}
        with open(a.json, 'w') as fh:
            json.dump(art, fh, indent=1, sort_keys=True)
        print(f'artifact: {a.json}')

    rc = 0
    if img_src is not None and img_src['json_status'] != 'read' and \
            any(r['inputs'] for ans in answers for r in (ans.get('images') or {}).get('rows', [])) \
            and not image_bindings:
        # Workflows that DO bind docker inputs, an image set that came back empty, and no values file
        # to read: the mode could not run. Named and counted, because a check that could not run is
        # not a check that passed, and exit 0 here would read as "no images involved".
        print('\nIMAGE ANSWER INCOMPLETE — ' + str(sum(1 for ans in answers
              for r in (ans.get('images') or {}).get('rows', []) if r['inputs']))
              + ' docker input(s) are bound by the reached workflows and NONE could be resolved: '
              + img_src['json_reason'] + '. The image set this change forces is not known to this '
              'run, which is why it does not exit 0.', file=sys.stderr)
        rc = 2
    if unknown:
        print(f'\nUNKNOWN TARGETS — {len(unknown)} of the {len(a.target)} name(s) given are not in '
              f'{root}: ' + ', '.join(repr(u) for u in unknown) + '. The block(s) above are real '
              'answers; these names were not answered at all, which is why this run does not exit 0.',
              file=sys.stderr)
        rc = 2
    if failures or unresolved:
        if failures:
            print(f'\nLOAD-FAILURES — {len(failures)} file(s) miniwdl could not parse. Every answer '
                  f'above is a partial answer:', file=sys.stderr)
            for bad_file, err in failures:
                print(f'  {bad_file}  {err}', file=sys.stderr)
        if unresolved:
            print(f'\nUNRESOLVED-CALLS — {len(unresolved)} call(s) miniwdl could not resolve, so the '
                  f'edge to their target is missing from the answer block(s) above:', file=sys.stderr)
            for bad_file, callee, line in unresolved:
                print(f'  {bad_file}:{line}  call {callee}', file=sys.stderr)
        rc = 2
    return rc


if __name__ == '__main__':
    sys.exit(main(sys.argv))
