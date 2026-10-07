#!/usr/bin/env python3
"""terra_entity_check.py — for every Terra launch config at a ref: which entity does it run against,
and does a shipped entity table actually carry every attribute it reads?

Why this exists
---------------
`docs/plan-launch-any-module.md` Stage A needs one answer before any module outside the batch chain can
be launched: the entity type a method config runs against. A wrong type is not a loud failure —
`terra/steps.py` already says a wrong type "resolves every `this.*` binding to nothing at runtime, after
the VMs booted". Three candidate sources were measured; two are dead:

  * the WDL: `git grep -l 'this\\.' -- 'wdl/*.wdl'` at gatk-sv `e1909d2f` returns **0 files**. `this.<x>`
    is Terra's entity-passing syntax; it lives in method configs and input templates, never in a WDL.
  * the member rule (`this.<collection>.<attr>` names the type): dead. `MergeBatchSites.json.tmpl` reads
    `${this.sample_set_set_id}` once and `${this.sample_sets.<attr>}` three times. The member rule
    derives `sample_sets`; the row is a `sample_set_set`. Deriving from the member collection is how a
    wrong type gets printed with confidence.
  * the **name key** (`${this.<etype>_id}`, one path component): holds, and this tool measures it per ref
    instead of quoting the plan.

So the rule is the name key, and it refuses rather than guessing.

What is looked at, and how it is read
------------------------------------
Corpus: every `*.json.tmpl` under `inputs/templates/terra_workspaces/**` at the ref. Not `wdl/*.wdl`,
and nothing is assumed about the layout inside that directory: the cohort flavor keeps its configs under
`workflow_configurations/` (write-back configs one level deeper), while the single-sample config sits
directly in its flavor directory. The flavor is read off the path and printed, never used to pick a rule.

The tree is read with `git archive` into a temp directory — the discipline of
`checks/wdl_inputs_check.py::render()` and `terra/batch_configs.py::_tree_from_ref()`: `git archive`
cannot touch a checkout's working tree, index or HEAD, and nothing here writes to a checkout. `--tree`
takes an already-materialized tree instead, which is how the offline selftest grades this file.

The templates are JSON with `${...}` and `{{ ... }}` inside strings. `terra/batch_configs.py` already
carries the one substitution that survives them (`JINJA_RE`: a `{{ ... }}` expression becomes `null`,
because rendering would need jinja2 *and* upstream's `inputs/values/**` bundle, which answers "what did
that values profile bind" instead of "what does this ref bind"). This file defines no second pattern: it
reads `JINJA_RE` out of that source with `ast` (`_shared_expander()`), so §10's "one expander per kind"
stays true by construction — one definition in the repo, and a missing one is a prerequisite refusal.

The four questions, and the refusal each one can print
------------------------------------------------------
1. **workflow** — the prefix of the binding keys (`"MergeBatchSites.cohort"` → `MergeBatchSites`), not
   the file stem. Those differ for real: `output_configurations/MergeBatchSitesOutputs.json.tmpl` binds
   `MergeBatchSites.*`. Both print (`wf=` and `filestem=`) so the mismatch is visible.
2. **name key(s)** — every read `${this.<etype>_id>` whose path is ONE component. A two-component read is
   a member read, never a name key: `CombineBatches.json.tmpl` reads `${this.sample_set_set_id}` and
   `${this.sample_sets.sample_set_id}`, and counting the second would turn a single-answer config into
   "many". Zero name keys, or two distinct `<etype>`s, refuse. `${this.id}` (nothing before `_id`)
   carries no type, so it is not a name key either, and the zero-name-key reason names it.
3. **member collections** — the `<collection>` of each `${this.<collection>.<attr>}`. The collection NAME
   is checked against the derived table (it must be a column of the root row); the member ATTRIBUTE is
   reported and counted but NOT checked, because checking it needs the member entity type, and
   plural-to-singular is a guess. That is the same refusal `terra/steps.py` makes.
4. **the shipped-table cross-check** — the derived `<etype>` must be the type of a TSV in the corpus whose
   first header column is `entity:<etype>_id` or `membership:<etype>_id`, and every single-component
   `${this.<attr>}` and every member collection must be a column of it (`<etype>_id` itself counts: it is
   the name column). Tables match by TYPE across the whole corpus and the covering table's path prints, so
   "which file answered, and is it this flavor's own" is readable from the output. One table must cover
   ALL the reads, because one table is what gets imported: two tables of the same type each lacking a
   different column is a refusal naming both gaps, not a pass by union. `${workspace.*}` bindings are out
   of scope — that is Stage B's `workspace.tsv.tmpl` oracle.

Refusal precedence is fixed, because the first failure makes the later ones unanswerable:
`cannot-parse` → `no-name-key` / `many-name-keys` → `no-shipped-table` → `columns-missing`.

A pass does NOT mean
--------------------
It means the derived type is a shipped table's type and the read names are its columns. It does not mean
the entity ROW exists (that is the read-only `terra.entity_sample()` call the plan puts before any
submission), it does not mean the config's VALUES are right (Stage B), and it does not mean Terra's own
`rootEntityType` agrees — that string appears in no file upstream and is still the plan's open,
credential-using decision.

Measured on the tree this file was written against (gatk-sv `e1909d2f`); run the command in
docs/static-checks.md and it prints all of this itself. 31 configs: 29 with exactly one name key, 2 with
none (both `output_configurations/*` write-back files), 0 with many. Derived types: `sample_set_set` 16,
`sample_set` 10, `sample` 3, none 2. Shipped-table check: 3 pass (`GatherSampleEvidence`,
`StripyWorkflow`, `GATKSVPipelineSingleSample`), 16 have no shipped table of the derived type, 10 have a
table that lacks a column they read, 0 fail to parse. The same corpus also carries one `${[this.a, …]}`
binding holding **five** reads (`PlotSVCountsPerSample.vcfs`), **1** key bound twice with different values
(`GATKSVPipelineSingleSample.mei_bed` — `json.loads` is last-wins, so `${workspace.reference_mei_bed}`
survives), 0 reads inside nested objects, 0 `this.` outside `${ }` and 30 `{{ ... }}` expressions across 9
configs. Every one of those shapes is reported per config (`nested=`, `barethis=`, `dupkeys=`,
`neutralised=`) rather than assumed away: the list binding is the plan's own warning — "a grammar that
treats a value as a path iff it begins `this.` mis-reads `${[this.a, this.b, …]}`" — and with that
grammar `PlotSVCountsPerSample` loses 5 of its 6 reads and passes the table check it honestly fails.

Usage
-----
    checks/terra_entity_check.py --repo ~/gatk-sv --ref main            # read a ref (git archive)
    checks/terra_entity_check.py --tree /tmp/gatk-sv-at-ref             # an already-materialized tree
    checks/terra_entity_check.py                                        # GSVTK_GATK_SV_CHECKOUT / _REF
    checks/terra_entity_check.py --tree T --corpus inputs/templates/terra_workspaces

Stdlib only: no network, no credentials, no Terra API, no write outside a temp dir.

Exit codes
----------
    0  every config in the corpus answered (nothing UNRESOLVED)
    1  findings — at least one config is UNRESOLVED (the refusal, with its reason, is the answer)
    2  usage error (bad flag combination)
    3  prerequisite missing, so the question could not be asked at all: no checkout, a ref that will not
       archive, no corpus at that ref, no config in it, or no `terra/batch_configs.py` to read the
       one expander from

Machine lines
-------------
One `GSVTK-ENTITY` line per config, key=value, so a caller can read verdicts while the human table beside
it stays readable; plus one `GSVTK-ENTITY-SUMMARY` line with the census counters, so a phase can assert
that the per-config lines add up, plus one `GSVTK-ENTITY-UNCHECKED` line counting what this tool deliberately
did NOT evaluate (member attributes, `${workspace.*}` bindings, Terra's own `rootEntityType`). That line prints
on a clean run too: no refusal found is not the same fact as nothing left unexamined, and a reader must be able
to tell the two apart without reading this file.

    GSVTK-ENTITY config=<relpath> flavor=<f> wf=<workflow> filestem=<s> namematch=yes|no
      state=OK|UNRESOLVED namekeys=<n> namekey=<etype|-> collections=<a,b|-> reads_direct=<n>
      reads_member=<n> member_attrs=<n> tables_of_type=<n> table=<path|-> table_kind=entity|membership|-
      columns=<n> missing=<a,b|-> nested=<n> barethis=<n> dupkeys=<n> neutralised=<n> unbraced=<n>
      reason=<code|->
"""
from __future__ import annotations

import argparse
import ast
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
import config  # noqa: E402  # GATK_SV_CHECKOUT / GATK_SV_REF: one source of truth for both

DEFAULT_CORPUS = "inputs/templates/terra_workspaces"
# Where the one Jinja-neutralising pattern lives. Read out of that source, never copied in here.
EXPANDER_SOURCE = os.path.join(os.path.dirname(HERE), "terra", "batch_configs.py")
EXPANDER_NAME = "JINJA_RE"

# A Terra binding is `${ ... }`; one value may carry several, or carry them around other text.
BINDING_RE = re.compile(r"\$\{([^{}]*)\}")
# Inside one binding: `this.<path>`. A deeper path keeps its tail (it is still one member read).
THIS_TOKEN_RE = re.compile(r"this\.([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")
# `this.` outside `${ }`: Terra binds expressions inside `${ }`, so these are literals. Counted, not read.
BARE_THIS_RE = re.compile(r"this\.[A-Za-z_][A-Za-z0-9_.]*")
# First header column of a shipped entity table. `entity:` is a row table, `membership:` a collection
# table; both declare a type and both are importable as that type.
TABLE_HEADER_RE = re.compile(r"^(entity|membership):(.+)_id$")
CONFIG_SUFFIX = ".json.tmpl"

# Refusal codes, in precedence order. Every one prints the paths it tried.
REASONS = ("cannot-parse", "no-name-key", "many-name-keys", "no-shipped-table", "columns-missing")
PLACEHOLDER = "\x00"


def _shared_expander():
    """The one `{{ ... }}`-neutralising pattern, read out of terra/batch_configs.py by name.

    Importing that module would drag in `firecloud` (it is the tool that POSTs method configs), which an
    offline check must not need, and copying the pattern would put a second expander in the repo — the
    drift §10 of docs/module-profiles.md exists to close. So the pattern is parsed out of the source with
    `ast`: one definition in the tree, and a tree whose file no longer carries it gets a named refusal.
    Returns (compiled or None, "where it was found").
    """
    try:
        src = open(EXPANDER_SOURCE).read()
    except OSError:
        return None, "unreadable"
    try:
        tree = ast.parse(src, EXPANDER_SOURCE)
    except SyntaxError as e:
        return None, "%s does not parse (%s)" % (EXPANDER_SOURCE, e)
    for node in tree.body:
        if not (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == EXPANDER_NAME for t in node.targets)):
            continue
        call = node.value
        if (isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "compile"
                and call.args and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)):
            where = "%s:%s" % (os.path.relpath(EXPANDER_SOURCE, os.path.dirname(HERE)), node.lineno)
            return re.compile(call.args[0].value), where
        return None, "%s:%s is not a re.compile(<string>) assignment" % (
            os.path.relpath(EXPANDER_SOURCE, os.path.dirname(HERE)), node.lineno)
    return None, "%s carries no %s assignment" % (EXPANDER_SOURCE, EXPANDER_NAME)


def _tree_from_ref(repo: str, ref: str, subpath: str) -> str:
    """One subtree of <repo> at <ref>, unpacked into a temp dir. Read-only by construction.

    `git archive` streams the tree: it cannot touch the checkout's index, HEAD or working tree, and the
    tarball lands in a directory this process removes on exit. Same discipline as
    `checks/wdl_inputs_check.py::render()`.
    """
    repo = os.path.expanduser(repo)
    tmp = tempfile.mkdtemp(prefix="gsvtk-entity-")
    atexit.register(shutil.rmtree, tmp, True)
    arc = subprocess.run(["git", "-C", repo, "archive", ref, subpath], capture_output=True)
    if arc.returncode != 0:
        why = (arc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        print("PREREQUISITE `git -C %s archive %s %s` failed: %s"
              % (repo, ref, subpath, (why or ["?"])[0][:200]))
        return ""
    untar = subprocess.run(["tar", "-x", "-C", tmp], input=arc.stdout, capture_output=True)
    if untar.returncode != 0:
        print("PREREQUISITE could not unpack `git archive %s %s` from %s" % (ref, subpath, repo))
        return ""
    return tmp


def read_config(path: str, jinja_re) -> dict:
    """One launch config read STRUCTURALLY: {ok, keys, neutralised, dupkeys, unbraced, error}.

    `keys` keeps the document's nesting, because a `${this.x}` inside a nested object is still a binding
    (0 of them in the corpus at `e1909d2f`, and this file walks for them rather than trusting that count
    at the next ref). `dupkeys` names a key bound more than once, and the value that survives is the LAST
    one — that is what `json.loads` does, so reporting it is the honest description of what a structural
    reader sees (the risk the plan names for `mei_bed`).
    """
    out = {"ok": False, "keys": {}, "neutralised": 0, "dupkeys": [], "unbraced": 0, "error": ""}
    try:
        text = open(path).read()
    except OSError as e:
        out["error"] = str(e)
        return out
    out["neutralised"] = len(jinja_re.findall(text))
    neutral = jinja_re.sub("null", text)

    def keep_pairs(pairs):
        names = [k for k, _ in pairs]
        for k in names:
            if names.count(k) > 1 and k not in out["dupkeys"]:
                out["dupkeys"].append(k)
        return dict(pairs)                                # last wins, exactly json.loads' own rule

    try:
        obj = json.loads(neutral, object_pairs_hook=keep_pairs)
    except ValueError as e:
        out["error"] = str(e)
        return out
    if not isinstance(obj, dict):
        out["error"] = "top level is a %s, not an object of bindings" % type(obj).__name__
        return out
    out["ok"] = True
    out["keys"] = obj
    # A `${` with no closing `}` would drop a binding silently, so it is counted instead.
    out["unbraced"] = len(re.findall(r"\$\{", neutral)) - len(BINDING_RE.findall(neutral))
    return out


def walk_strings(value, path: str = "", depth: int = 1) -> list:
    """[(key_path, string, depth)] for every string in a parsed value, nested objects/lists included.

    `depth` 1 means the string is a binding value of the config map itself; 2+ means it sits inside a
    nested object or list under that binding. The distinction is reported, not assumed: the corpus holds
    0 nested reads at gatk-sv `e1909d2f`, and nothing guarantees the next ref holds none.
    """
    out = []
    if isinstance(value, str):
        out.append((path, value, depth))
    elif isinstance(value, dict):
        for k, v in value.items():
            out.extend(walk_strings(v, "%s.%s" % (path, k), depth + 1))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(walk_strings(v, "%s[%d]" % (path, i), depth + 1))
    return out


def binding_strings(keys: dict) -> list:
    """[(key_path, string, is_nested)] for every string binding in a config document.

    `is_nested` is True when the string sits inside an object or list BELOW a binding key rather than
    being the binding's value. Reported per config (`nested=`), never assumed: the corpus carries 0 of
    them at gatk-sv `e1909d2f`, and a reader who cannot see the difference cannot see it appear.
    """
    return [(p, t, d > 1) for (p, t, d) in walk_strings(keys, "", 0)]


def reads_in(text: str) -> tuple:
    """(paths, bare_this_count) for one config value.

    `paths` are the `this.<path>` bindings inside `${ ... }`, including the ones inside
    `${[this.a, this.b]}` that a starts-with grammar loses. `bare_this_count` is `this.` outside
    `${ }`: Terra binds expressions inside `${ }` only, so those are literal strings, and printing the
    count is what stops "that read was not seen" from turning into a silent pass.
    """
    reads, rest = [], text
    for inner in BINDING_RE.findall(text):
        reads.extend(THIS_TOKEN_RE.findall(inner))
        rest = rest.replace("${" + inner + "}", PLACEHOLDER)
    return reads, len(BARE_THIS_RE.findall(rest))


def split_read(path: str) -> tuple:
    """("direct", attr, "") | ("member", collection, attr_tail) for one `this.<path>`."""
    parts = path.split(".")
    if len(parts) == 1:
        return ("direct", parts[0], "")
    return ("member", parts[0], ".".join(parts[1:]))


def name_key_type(attr: str) -> str:
    """<etype> for a name-key-shaped read, or "" when the read carries no type.

    `sample_set_set_id` -> `sample_set_set`. `id` -> "": a name key with no type is not a name key, and
    saying so beats deriving an empty entity type that no table could match.
    """
    if attr.endswith("_id") and len(attr) > len("_id"):
        return attr[:-len("_id")]
    return ""


def read_tables(corpus_dir: str) -> tuple:
    """([table], other_tsv_count, unreadable[]) for the TSVs shipped in the corpus.

    A table is {etype, kind, path, columns}. A header carrying a `{{ ... }}` expression is UNREADABLE and
    is named, not guessed: inventing a column name out of a Jinja expression would let a column check pass
    on a name nobody wrote.
    """
    tables, other, unreadable = [], 0, []
    for dirpath, _dirs, files in os.walk(corpus_dir):
        for fn in sorted(files):
            if not (fn.endswith(".tsv") or fn.endswith(".tsv.tmpl")):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, corpus_dir).replace(os.sep, "/")
            try:
                head = open(full).readline().rstrip("\n").rstrip("\r")
            except OSError as e:
                unreadable.append("%s (%s)" % (rel, e))
                continue
            fields = head.split("\t")
            first = fields[0].strip()
            if "{{" in first:
                unreadable.append("%s (its type-declaring first column carries a {{ ... }} "
                                  "expression)" % rel)
                continue
            m = TABLE_HEADER_RE.match(first)
            if not m:
                other += 1                                  # workspace.tsv.tmpl and friends: not a table
                continue
            cols, unreadable_cols = [], 0
            for c in fields[1:]:
                c = c.strip()
                if not c:
                    continue
                if "{{" in c:
                    unreadable_cols += 1                    # a name nobody wrote down; never guessed
                    continue
                cols.append(c)
            tables.append({"etype": m.group(2), "kind": m.group(1), "path": rel,
                           "columns": cols, "columns_unreadable": unreadable_cols})
    return sorted(tables, key=lambda t: (t["etype"], t["path"])), other, unreadable


def grade(cfg: dict, tables: list) -> dict:
    """The verdict for one parsed config against the corpus's shipped tables."""
    v = dict(cfg)
    reads, bare, nested = [], 0, 0
    for _where, text, is_nested in binding_strings(cfg["keys"]):
        found, b = reads_in(text)
        reads.extend(found)
        bare += b
        if is_nested:
            nested += len(found)
    direct, member, attrs = [], [], []
    for path in reads:
        kind, first, tail = split_read(path)
        if kind == "direct":
            if first not in direct:
                direct.append(first)
        else:
            if first not in member:
                member.append(first)
            attrs.append("%s.%s" % (first, tail))
    key_types = []
    for attr in direct:
        et = name_key_type(attr)
        if et and et not in key_types:
            key_types.append(et)
    v.update(direct=direct, member=member, member_attrs=attrs, bare_this=bare, nested=nested,
             namekey_types=key_types, this_reads=len(reads), etype="", table=None, table_kind="",
             columns=0, missing=[], tables_of_type=0)

    if not cfg["ok"]:
        v.update(state="UNRESOLVED", reason="cannot-parse")
        return v
    if not key_types:
        v.update(state="UNRESOLVED", reason="no-name-key")
        return v
    if len(key_types) > 1:
        v.update(state="UNRESOLVED", reason="many-name-keys")
        return v
    v["etype"] = key_types[0]
    of_type = [t for t in tables if t["etype"] == key_types[0]]
    v["tables_of_type"] = len(of_type)
    if not of_type:
        v.update(state="UNRESOLVED", reason="no-shipped-table")
        return v
    # What the type has to carry: every single-component read, and every member collection (a collection
    # is a column of the root row before anything can scatter over it). The name column is implicit.
    wanted = sorted(set(direct) | set(member))
    cover, best = None, None
    for t in of_type:
        cols = set(t["columns"]) | {"%s_id" % key_types[0]}
        miss = [w for w in wanted if w not in cols]
        if not miss:
            cover = t
            break
        if best is None or len(miss) < len(best[1]):
            best = (t, miss)
    chosen = cover or (best[0] if best else None)
    v["table"] = chosen["path"] if chosen else None
    v["table_kind"] = chosen["kind"] if chosen else ""
    v["columns"] = len(chosen["columns"]) if chosen else 0
    v["missing"] = [] if cover else (best[1] if best else [])
    v.update(state="OK" if cover else "UNRESOLVED", reason="" if cover else "columns-missing")
    return v


def workspace_reads(cfg: dict) -> int:
    """How many bindings read `${workspace.*}` — quoted by the zero-name-key reason, never guessed."""
    n = 0
    for _p, text, _nested in binding_strings(cfg["keys"]):
        n += sum(1 for inner in BINDING_RE.findall(text) if "workspace." in inner)
    return n


def table_columns(t: dict) -> str:
    """A table's columns as a reader wants them, with the unreadable count said out loud."""
    cols = ", ".join(t["columns"]) or "-"
    if t.get("columns_unreadable"):
        cols += " (+%d column name(s) unreadable: their header carries a {{ ... }} expression)"\
                % t["columns_unreadable"]
    return cols


def reason_text(v: dict, corpus: str, tables: list) -> str:
    """The sentence: what was looked for, and where. A bare code is not a reason."""
    r = v["reason"]
    if r == "cannot-parse":
        return ("not JSON that `json.loads` accepts after the one `{{ ... }}` -> null substitution "
                "(%d substituted), so its bindings cannot be read at all: %s"
                % (v["neutralised"], v["error"]))
    if r == "no-name-key":
        typed = " it also reads `this.id`, which carries no <etype> before `_id`;" if "id" in v["direct"] else ""
        if not v["this_reads"]:
            return ("no ${this.<etype>_id} read:%s this config reads nothing through `this.` at all "
                    "(%d binding(s), %d of them through ${workspace.*}); a config that binds no entity "
                    "attribute cannot say what row it runs against"
                    % (typed, len(v["keys"]), workspace_reads(v)))
        return ("no ${this.<etype>_id} read:%s the %d single-component read(s) it does make are %s"
                % (typed, len(v["direct"]), ", ".join("`this.%s`" % a for a in v["direct"])))
    if r == "many-name-keys":
        return ("%d different entity types appear as ${this.<etype>_id}: %s. Which row a submission runs "
                "against is one answer, so this one refuses rather than picking"
                % (len(v["namekey_types"]), ", ".join("`%s`" % t for t in v["namekey_types"])))
    if r == "no-shipped-table":
        seen = sorted("%s (%s:%s_id, columns: %s)" % (t["path"], t["kind"], t["etype"], table_columns(t))
                      for t in tables)
        return ("no shipped entity table of type `%s`: looked in every TSV under %s for a first header "
                "column `entity:%s_id` or `membership:%s_id`. The tables this ref ships are: %s"
                % (v["etype"], corpus, v["etype"], v["etype"], "; ".join(seen) or "none at all"))
    if r == "columns-missing":
        of_type = [t for t in tables if t["etype"] == v["etype"]]
        return ("no shipped table of type `%s` carries %d column(s) this config reads: %s. "
                "The %d table(s) of that type, and what each carries: %s. One table has to carry them "
                "all, because one table is what gets imported — two tables covering the set between them "
                "is reported here, not passed on their union"
                % (v["etype"], len(v["missing"]), ", ".join("`%s`" % m for m in v["missing"]),
                   len(of_type), "; ".join("%s (columns: %s)" % (t["path"], table_columns(t))
                                           for t in of_type)))
    return ""


def note_text(v: dict) -> list:
    """What a pass or a refusal does NOT cover for this config, one line per fact that applies."""
    notes = []
    if v["dupkeys"]:
        notes.append("%d key(s) bound more than once (%s); `json.loads` keeps the LAST value, so which "
                     "one a submission would use is not in this file"
                     % (len(v["dupkeys"]), ", ".join("`%s`" % k for k in v["dupkeys"])))
    if v["nested"]:
        notes.append("%d read(s) sit inside a nested object/list, not at binding level" % v["nested"])
    if v["bare_this"]:
        notes.append("%d value(s) carry `this.` outside `${ ... }`, which Terra reads as a LITERAL and "
                     "this file therefore does not count as an entity read" % v["bare_this"])
    if v["unbraced"]:
        notes.append("%d `${` with no closing `}`, so a binding may have gone unread" % v["unbraced"])
    if v["member_attrs"]:
        notes.append("%d member attribute read(s) NOT checked (%s); the member entity type is not "
                     "derived, and plural-to-singular is a guess that binds nothing when wrong"
                     % (len(v["member_attrs"]), ", ".join(sorted(set(v["member_attrs"])))))
    if v["state"] == "OK":
        notes.append("row existence is NOT checked here: that is `terra.entity_sample()` before any "
                     "submission, and Terra's own rootEntityType is a credential-using read")
    return notes


def machine_line(d: dict) -> str:
    """The one machine-readable line per config. Lists join with `,`; an empty list prints `-`."""
    def or_dash(xs):
        return ",".join(xs) if xs else "-"
    return ("GSVTK-ENTITY config=%s flavor=%s wf=%s filestem=%s namematch=%s state=%s namekeys=%s "
            "namekey=%s collections=%s reads_direct=%s reads_member=%s member_attrs=%s "
            "tables_of_type=%s table=%s table_kind=%s columns=%s missing=%s nested=%s barethis=%s "
            "dupkeys=%s neutralised=%s unbraced=%s reason=%s"
            % (d["config"], d["flavor"], d["wf"] or "-", d["filestem"],
               "yes" if d["wf"] == d["filestem"] else "no", d["state"], len(d["namekey_types"]),
               or_dash(d["namekey_types"]), or_dash(d["member"]), len(d["direct"]), len(d["member"]),
               len(d["member_attrs"]), d["tables_of_type"], d["table"] or "-", d["table_kind"] or "-",
               d["columns"], or_dash(d["missing"]), d["nested"], d["bare_this"],
               or_dash(d["dupkeys"]) if d["dupkeys"] else "0", d["neutralised"], d["unbraced"],
               d["reason"] or "-"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="terra_entity_check.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="", help="gatk-sv clone to read (default: GSVTK_GATK_SV_CHECKOUT)")
    ap.add_argument("--ref", default="", help="ref to read (default: GSVTK_GATK_SV_REF, else HEAD)")
    ap.add_argument("--tree", default="", help="already-materialized gatk-sv tree root (skips git archive)")
    ap.add_argument("--corpus", default=DEFAULT_CORPUS,
                    help="corpus subpath inside the tree (default: %s)" % DEFAULT_CORPUS)
    args = ap.parse_args(argv)

    jinja_re, where = _shared_expander()
    if jinja_re is None:
        print("PREREQUISITE no single `{{ ... }}` expander to reuse (%s) — this file refuses to define a "
              "second one, which is §10's rule" % where)
        return 3
    if args.tree and args.ref:
        print("usage error: --tree already fixes the tree; drop --ref, or use --repo with --ref")
        return 2
    if args.tree:
        if not os.path.isdir(args.tree):
            print("usage error: --tree %s is not a directory" % args.tree)
            return 2
        tree, source = args.tree, "tree %s" % args.tree
    else:
        repo = args.repo or config.get("GATK_SV_CHECKOUT")
        # GSVTK_GATK_SV_REF is an environment variable, not a profile key (kit/gsvtk-config's `get` refuses
        # an unknown key rather than returning "", so asking it for a key that does not exist is a crash).
        ref = args.ref or os.environ.get("GSVTK_GATK_SV_REF") or "HEAD"
        if not repo or not os.path.isdir(os.path.expanduser(repo)):
            print("PREREQUISITE no gatk-sv checkout to read (set GSVTK_GATK_SV_CHECKOUT, pass --repo, or "
                  "pass --tree for an already-materialized tree)")
            return 3
        repo = os.path.expanduser(repo)
        if not os.path.isdir(os.path.join(repo, ".git")):
            print("PREREQUISITE %s is not a git repository, so there is no ref to archive" % repo)
            return 3
        tree = _tree_from_ref(repo, ref, args.corpus)
        if not tree:
            return 3
        source = "git archive %s from %s" % (ref, repo)

    corpus_dir = os.path.join(tree, args.corpus)
    if not os.path.isdir(corpus_dir):
        # Not "use the tree as the corpus" — that guess would let a typo'd --corpus grade the whole tree
        # and print a confident answer about a corpus nobody asked about. `--corpus .` is the way to say it.
        print("PREREQUISITE no %s under %s (%s) — the corpus this tool asks about is not there, and "
              "'no configs' is a stated answer, never a clean one" % (args.corpus, tree, source))
        return 3

    configs = []
    for dirpath, _dirs, files in os.walk(corpus_dir):
        configs.extend(os.path.join(dirpath, fn) for fn in files if fn.endswith(CONFIG_SUFFIX))
    configs.sort()
    tables, other_tsv, unreadable = read_tables(corpus_dir)

    print("terra entity derivation — corpus %s (%s)" % (args.corpus, source))
    print("expander: %s from %s (one definition; `{{ ... }}` -> null, never rendered)"
          % (EXPANDER_NAME, where))
    by_type = {}
    for t in tables:
        by_type.setdefault(t["etype"], []).append(t)
    print("shipped tables: %d entity/membership TSV(s), type(s) %s; %d other TSV(s) excluded (first "
          "column is neither `entity:<x>_id` nor `membership:<x>_id`)"
          % (len(tables), ", ".join("%s x%d" % (k, len(v)) for k, v in sorted(by_type.items())) or "-",
             other_tsv))
    for t in tables:
        print("  %-46s %s:%s_id  columns: %s" % (t["path"], t["kind"], t["etype"], table_columns(t)))
    for bad in unreadable:
        print("  UNREADABLE table: %s" % bad)
    if not configs:
        print("PREREQUISITE 0 configs: no %s file under %s, so there is nothing to derive an entity from"
              % (CONFIG_SUFFIX, corpus_dir))
        print("GSVTK-ENTITY-SUMMARY configs=0 ok=0 no-name-key=0 many-name-keys=0 no-shipped-table=0 "
              "columns-missing=0 cannot-parse=0 namekeys-one=0 namekeys-zero=0 namekeys-many=0")
        return 3

    rows = []
    for full in configs:
        rel = os.path.relpath(full, corpus_dir).replace(os.sep, "/")
        cfg = read_config(full, jinja_re)
        prefixes = sorted({k.split(".")[0] for k in cfg["keys"]}) if cfg["ok"] else []
        v = grade(cfg, tables)
        v.update(config=rel, filestem=os.path.basename(full)[:-len(CONFIG_SUFFIX)],
                 flavor=rel.split("/")[0] if "/" in rel else "(root)",
                 wf=prefixes[0] if len(prefixes) == 1 else ",".join(prefixes) or "?",
                 workspace_reads=workspace_reads(cfg) if cfg["ok"] else 0)
        rows.append(v)

    width = max([len(r["config"]) for r in rows] + [6])
    line = "%%-14s %%-%ds %%-22s %%-16s %%-12s %%-36s %%s" % width
    print("\n" + line % ("FLAVOR", "CONFIG", "WORKFLOW", "NAME KEY(S)", "COLLECTIONS", "TABLE", "STATE"))
    for r in rows:
        tbl = r["table"] or "-"
        if len(tbl) > 36:
            tbl = "..." + tbl[-33:]
        print(line % (r["flavor"][:14], r["config"], (r["wf"] or "?")[:22],
                      ",".join(r["namekey_types"]) or "-", ",".join(r["member"]) or "-", tbl,
                      r["state"] + ((" (%s)" % r["reason"]) if r["reason"] else "")))

    print("\n== refusals, naming what was looked for and where ==")
    findings = [r for r in rows if r["state"] != "OK"]
    if not findings:
        print("  (none — every config in the corpus answered)")
    for r in findings:
        print("UNRESOLVED %s\n    %s" % (r["config"], reason_text(r, args.corpus, tables)))
        for n in note_text(r):
            print("    note: %s" % n)
    for r in rows:
        notes = note_text(r) if r["state"] == "OK" else []
        if notes:
            print("NOTE %s\n    %s" % (r["config"], "\n    ".join(notes)))

    counts = dict((k, 0) for k in ("ok",) + REASONS)
    for r in rows:
        counts["ok" if r["state"] == "OK" else r["reason"]] += 1
    one = sum(1 for r in rows if len(r["namekey_types"]) == 1)
    zero = sum(1 for r in rows if len(r["namekey_types"]) == 0)
    many = sum(1 for r in rows if len(r["namekey_types"]) > 1)
    print("\n== census ==")
    print("configs %d | name keys: one %d, zero %d, many %d | shipped-table check: pass %d, no table %d, "
          "missing column %d, unparsable %d"
          % (len(rows), one, zero, many, counts["ok"], counts["no-shipped-table"],
             counts["columns-missing"], counts["cannot-parse"]))
    print("member collections across the corpus: %s"
          % (", ".join(sorted({c for r in rows for c in r["member"]})) or "none"))
    for r in rows:
        print(machine_line(r))
    print("GSVTK-ENTITY-SUMMARY configs=%d ok=%d no-name-key=%d many-name-keys=%d no-shipped-table=%d "
          "columns-missing=%d cannot-parse=%d namekeys-one=%d namekeys-zero=%d namekeys-many=%d"
          % (len(rows), counts["ok"], counts["no-name-key"], counts["many-name-keys"],
             counts["no-shipped-table"], counts["columns-missing"], counts["cannot-parse"],
             one, zero, many))
    # The scope statement, printed every run whether or not anything refused. Everything above answers "did the
    # entity a config names hold the columns it reads"; it does NOT answer whether the values are right, and a
    # reader who only looks for UNRESOLVED lines will not notice the difference. So the things this tool
    # deliberately does not evaluate get a census of their own, with counts, on every run — including a clean
    # one. An absent line here would mean the corpus holds none of these reads, which is a fact about the
    # corpus and never a pass.
    member_cfg = sum(1 for r in rows if r["member_attrs"])
    member_reads = sum(len(r["member_attrs"]) for r in rows)
    ws_cfg = sum(1 for r in rows if r.get("workspace_reads"))
    ws_reads = sum(r.get("workspace_reads", 0) for r in rows)
    print("GSVTK-ENTITY-UNCHECKED configs=%d member-attr-reads=%d configs-with-member-attrs=%d "
          "workspace-binding-reads=%d configs-with-workspace-reads=%d root-entity-type=never-read"
          % (len(rows), member_reads, member_cfg, ws_reads, ws_cfg))
    print("  (the three counts above are SCOPE, not verdicts: member attributes are counted and listed but "
          "never checked against a table, because plural-to-singular is a guess; ${workspace.*} bindings are "
          "counted but not resolved, which is a later stage's job and resolving them from a test sample would "
          "be a lie; and Terra's own rootEntityType is never asked, because that is a credentialed read this "
          "tool must not make.)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
