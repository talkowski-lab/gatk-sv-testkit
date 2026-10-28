#!/usr/bin/env python3
"""terra_entity_check.py — for every Terra launch config at a ref: which entity does it run against,
and does a shipped entity table actually carry every attribute it reads?

Reads every `*.json.tmpl` under `inputs/templates/terra_workspaces/**` at a gatk-sv ref (via `git archive`,
so the checkout is never touched) and answers four questions per config, plus three further layers:

  1. workflow     — the prefix of the binding keys (`MergeBatchSites.cohort` -> `MergeBatchSites`).
  2. name key     — the entity type, from the one-component read `${this.<etype>_id}`. Zero or several
                    distinct types refuse rather than guess.
  3. collections  — each `${this.<collection>.<attr>}`; the collection must be a column of the root row.
                    Member attributes are counted but not checked.
  4. shipped table — a corpus TSV headed `entity:<etype>_id` or `membership:<etype>_id` must carry every
                    direct read and member collection, in one table.

  Workspace oracle — each flavor's `workspace.tsv.tmpl` (a direct child of the flavor directory) declares
                    the attribute names `${workspace.X}` may bind. Status is OK, ABSENT, MALFORMED or DUPE.
                    The value column is never read.
  Value provenance — every bound value is classed: workspace-attr, workspace-missing, entity, literal,
                    needs-bundle, test-bundle, docker, mixed, no-oracle. workspace-missing, no-oracle and
                    test-bundle refuse; needs-bundle is disclosed and exits 0; `*_docker` bindings are
                    excluded by rule (images must be stated with `--image` or a profile pin).
  Required inputs  — each workflow's required inputs (via `checks/wdl_inputs_check.py`, which needs miniwdl)
                    must be carried by a launchable value class; `unbound` refuses. Without miniwdl every
                    config reports `capture=NO-MINIWDL` and the exit code is unaffected.

Entity refusal precedence: `cannot-parse` -> `no-name-key` / `many-name-keys` -> `no-shipped-table` ->
`columns-missing`.

A pass does not mean the entity row exists in a workspace, that a disclosed bundle was stated, that
workspace attribute values were read, or that Terra's `rootEntityType` agrees.

Usage
-----
    checks/terra_entity_check.py --repo ~/gatk-sv --ref main            # read a ref (git archive)
    checks/terra_entity_check.py --tree /tmp/gatk-sv-at-ref             # an already-materialized tree
    checks/terra_entity_check.py                                        # GSVTK_GATK_SV_CHECKOUT / _REF
    checks/terra_entity_check.py --tree T --corpus inputs/templates/terra_workspaces
    checks/terra_entity_check.py --against-dump work/configs/NS__WS.json   # vs Terra's rootEntityType

`--against-dump` reads a file saved by `terra/dump_configs.py` (read-only) and, per config, derives the
type from that config's own inputs with the name-key rule above and compares it with Terra's
`rootEntityType`: `AGREE`, `DISAGREE derived=X terra=Y`, or `UNDERIVABLE <reason>`, then a
`GSVTK-ENTITY-TERRA-SUMMARY` line. Exit 0 every derivable config agrees, 1 any DISAGREE, 2 nothing was
derivable (an empty dump included), an unreadable dump, or a flag combined with it.

No network, credentials or Terra API. Standard library only, except the required-input layer (miniwdl).
The WDL tree is always `<tree>/wdl`; there is no `--wdl-dir`.

Exit codes
----------
    0  every config in the corpus answered, and every value it binds is classifiable and launchable. With the
       required layer that is a SCOPE statement, not a clean audit: read `capture=` and
       `required-configs-uncaptured=` before reading this 0 as "every required input is bound"
    1  findings — at least one config is UNRESOLVED, by the entity derivation, by value provenance, or by a
       required input nothing binds (the refusal, with its reason, is the answer)
    2  usage error (bad flag combination)
    3  prerequisite missing, so the question could not be asked at all: no checkout, a ref that will not
       archive, no corpus at that ref, no config in it, or no `terra/batch_configs.py` to read the
       one expander from

Machine lines
-------------
One line per config per layer, plus a summary per layer. `GSVTK-ENTITY-UNCHECKED` lists what the run did not
evaluate and prints on every run. These prefixes are stable: `GSVTK-ENTITY`, `GSVTK-ENTITY-SUMMARY`,
`GSVTK-ENTITY-UNCHECKED`, `GSVTK-ORACLE`, `GSVTK-ORACLE-SUMMARY`, `GSVTK-VALUE`, `GSVTK-VALUE-SUMMARY`,
`GSVTK-REQUIRED`, `GSVTK-REQUIRED-SUMMARY`.

    GSVTK-ENTITY config=<relpath> flavor=<f> wf=<workflow> filestem=<s> namematch=yes|no
      state=OK|UNRESOLVED namekeys=<n> namekey=<etype|-> collections=<a,b|-> reads_direct=<n>
      reads_member=<n> member_attrs=<n> tables_of_type=<n> table=<path|-> table_kind=entity|membership|-
      columns=<n> missing=<a,b|-> nested=<n> barethis=<n> dupkeys=<n> neutralised=<n> unbraced=<n>
      reason=<code|->

    GSVTK-ORACLE flavor=<f> file=<relpath|-> attrs=<n> status=OK|ABSENT|MALFORMED|DUPE
    GSVTK-ORACLE-SUMMARY flavors=<n> ok=<n> absent=<n> malformed=<n> dupes=<n> names=<n>
      values=not-read enforced=yes

    GSVTK-VALUE config=<relpath> flavor=<f> wf=<workflow> bindings=<n> workspace-attr=<n>
      workspace-missing=<n> entity=<n> literal=<n> needs-bundle=<n> test-bundle=<n> docker=<n> mixed=<n>
      no-oracle=<n> nested-reads=<n> workspace-reads=<n> workspace-resolved-reads=<n>
      workspace-missing-reads=<n> no-oracle-reads=<n> bundles=<a,b|-> dropped-values=<n>
      dropped-workspace-reads=<n> unresolved=<n> state=OK|UNRESOLVED reasons=<a,b,c|->
    GSVTK-VALUE-SUMMARY configs=<n> bindings=<n> <the same class fields, summed> nested-reads=<n>
      workspace-reads=<n> workspace-resolved-reads=<n> workspace-missing-reads=<n> no-oracle-reads=<n>
      bundles=<a,b|-> dropped-values=<n> dropped-workspace-reads=<n> unresolved=<n> configs-unresolved=<n>
      configs-no-oracle=<n> configs-needs-bundle=<n> configs-no-values=<n> configs-unclassified=<n>

    GSVTK-REQUIRED config=<relpath> flavor=<f> wf=<a,b> workflows=<n> capture=<word> required=<n>
      workspace-attr=<n> workspace-missing=<n> entity=<n> literal=<n> needs-bundle=<n> test-bundle=<n>
      docker=<n> mixed=<n> no-oracle=<n> container=<n> unbound=<n> files=<a.wdl,b.wdl|->
      state=OK|UNRESOLVED|NOT-CAPTURED reason=<code|->
    GSVTK-REQUIRED-SUMMARY configs=<n> required=<n> <the same class fields, summed> workflows=<n>
      workflows-captured=<n> configs-ok=<n> configs-unsatisfied=<n> configs-not-captured=<n>
      capture-no-miniwdl=<n> capture-no-wdl-dir=<n> capture-no-document=<n> capture-load-failure=<n>
      capture-no-workflow=<n> capture-no-wdl=<n> capture-ok=<n>

`bindings=` and `required=` are the sums of their class fields. `capture=` is one of NO-MINIWDL, NO-WDL-DIR,
NO-DOCUMENT, LOAD-FAILURE, NO-WORKFLOW, NO-WDL, OK (first applicable wins); `state=NOT-CAPTURED` means the
question was not asked and is counted as `required-configs-uncaptured=` on `GSVTK-ENTITY-UNCHECKED`.
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
# The `{{ ... }}`-neutralising pattern is read from this source, not copied.
EXPANDER_SOURCE = os.path.join(os.path.dirname(HERE), "terra", "batch_configs.py")
EXPANDER_NAME = "JINJA_RE"

# A Terra binding is `${ ... }`; one value may carry several, or carry them around other text.
BINDING_RE = re.compile(r"\$\{([^{}]*)\}")
# Inside one binding: `this.<path>`.
THIS_TOKEN_RE = re.compile(r"this\.([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")
# `this.` outside `${ }` is a literal to Terra. Counted, not read.
BARE_THIS_RE = re.compile(r"this\.[A-Za-z_][A-Za-z0-9_.]*")
# First header column of a shipped entity (`entity:`) or collection (`membership:`) table.
TABLE_HEADER_RE = re.compile(r"^(entity|membership):(.+)_id$")
CONFIG_SUFFIX = ".json.tmpl"
# Flavor name for a config that sits directly in the corpus root.
ROOT_FLAVOR = "(root)"

# The workspace attribute table: a direct child of each flavor directory, whose first column lists the
# attribute names that flavor declares.
WORKSPACE_TABLE = "workspace.tsv.tmpl"
# Optional prefix on an attribute name, stripped on any row (not just row 1).
WORKSPACE_PREFIX = "workspace:"
WORKSPACE_NAME_RE = re.compile(r"[A-Za-z0-9_.]+\Z")
# Declared names listed in the human block before "and N more".
ORACLE_NAMES_SHOWN = 8

# Cure sentences name exactly one file, the one the diagnosis printed, since other TSVs nearby are decoys.
CURE_ONLY_FILE = ("— that exact file, and no other TSV that flavor ships, because only this one is read —")
# Per-flavor statuses in precedence order; the first applicable one is the counted label.
ORACLE_STATUSES = ("ABSENT", "MALFORMED", "DUPE", "OK")

# Value-provenance classes, in machine-line order. Each is also its field name, and `bindings=` is computed
# as their sum (see `_value_class_tail()`).
VALUE_CLASSES = ("workspace-attr", "workspace-missing", "entity", "literal", "needs-bundle",
                 "test-bundle", "docker", "mixed", "no-oracle")
# Classes that refuse a launch. `needs-bundle` is disclosed, not refused.
VALUE_REFUSAL_CLASSES = ("workspace-missing", "no-oracle", "test-bundle")
# Container images are excluded by rule: a key whose leaf ends `_docker`, or a value that is exactly one
# `${workspace.*_docker}` read. The image must be stated explicitly (see
# `terra/batch_rerun_step.py::_stated()`), never resolved here.
DOCKER_KEY_SUFFIX = "_docker"
DOCKER_CLASS = "docker"
# Test values profiles and test-sample coordinates refuse wherever they appear in a value.
TEST_COORDINATE_MARKERS = ("gs://", "fc-", "NA12878", "NA19240")
TEST_PROFILE_DIR = os.path.join("inputs", "values")
TEST_PROFILE_PREFIX = "test_"
# Only a `status=OK` oracle resolves a workspace read; each other status refuses under its own word.
NO_ORACLE_REASONS = {"ABSENT": "no-oracle-absent", "MALFORMED": "no-oracle-malformed",
                     "DUPE": "no-oracle-dupe"}
MIXED_NO_ORACLE_REASONS = {"ABSENT": "mixed-no-oracle-absent",
                           "MALFORMED": "mixed-no-oracle-malformed",
                           "DUPE": "mixed-no-oracle-dupe"}
# One `${workspace.<name>}` read, and the two whole-value forms. A name keeps its dots.
WORKSPACE_READ_RE = re.compile(r"workspace\.([A-Za-z0-9_.]+)")
WORKSPACE_WHOLE_RE = re.compile(r"\A\$\{workspace\.([A-Za-z0-9_.]+)\}\Z")
THIS_WHOLE_RE = re.compile(r"\A\$\{this\.([A-Za-z_][A-Za-z0-9_.]*)\}\Z")
# The root of `{{ root.attr | tojson }}`: a bundle name, never resolved to a file.
BUNDLE_ROOT_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)")
# Marker substituted for each `{{ ... }}` so bundle reads stay visible after neutralising. The source form
# is JSON-escaped (a raw 0x01 is not valid JSON); json.loads returns the raw character.
JINJA_MARK_SRC = "\\u0001gsvtk-jinja-%d\\u0001"
JINJA_MARK_RE = re.compile(r"\x01gsvtk-jinja-(\d+)\x01")

# Refusal codes, in precedence order. Every one prints the paths it tried.
REASONS = ("cannot-parse", "no-name-key", "many-name-keys", "no-shipped-table", "columns-missing")
PLACEHOLDER = "\x00"

# --------------------------------------------------------------------------- required-input acceptance
# The WDL directory, read as a second subtree of the same ref. Deliberately not configurable.
WDL_SUBTREE = "wdl"
# The required-input predicate is imported from the other WDL checker so the two never diverge.
REQUIRED_SOURCE = os.path.join(HERE, "wdl_inputs_check.py")
REQUIRED_WHERE = os.path.relpath(REQUIRED_SOURCE, os.path.dirname(HERE))
# Required-layer classes: the value class of whatever carries the input, plus
#   `container` — the key is bound to a whole JSON object or list, and
#   `unbound`   — nothing in the config names the key (refuses).
# `required=` is computed as their sum.
REQUIRED_CLASSES = VALUE_CLASSES + ("container", "unbound")
# A required input carried by a refusing value class is refused too, under its own word.
REQUIRED_REASONS = ("required-unbound", "required-workspace-missing", "required-test-bundle",
                    "required-no-oracle")
REQUIRED_REASON_OF = dict([("unbound", "required-unbound")] +
                           [(c, "required-" + c) for c in VALUE_REFUSAL_CLASSES])
# What asking one workflow for its required inputs can answer, in precedence order (first applicable wins):
#   NO-MINIWDL    — `WDL` is not importable (disclosed and counted, not a finding).
#   NO-WDL-DIR    — this tree/ref ships no `wdl/` subtree, or it would not archive.
#   NO-DOCUMENT   — the config cannot be parsed, so it names no workflow.
#   LOAD-FAILURE  — miniwdl will not load the file.
#   NO-WORKFLOW   — the file loads but declares tasks only.
#   NO-WDL        — no `<wdl>/<workflow>.wdl` in the tree.
REQUIRED_CAPTURES = ("NO-MINIWDL", "NO-WDL-DIR", "NO-DOCUMENT", "LOAD-FAILURE", "NO-WORKFLOW",
                     "NO-WDL", "OK")
# `NOT-CAPTURED`: the question was never asked. Counted on `GSVTK-ENTITY-UNCHECKED`.
REQUIRED_STATES = ("OK", "UNRESOLVED", "NOT-CAPTURED")
# Corpus-wide causes, stated once in the run-level banner rather than per config.
REQUIRED_BANNER_CAUSES = ("NO-MINIWDL", "NO-WDL-DIR", "NO-WDL")
REQUIRED_UNCAPTURED_SHOWN = 8


def _shared_expander():
    """The `{{ ... }}`-neutralising pattern, parsed out of terra/batch_configs.py with `ast`.

    Importing that module would pull in `firecloud`; copying the pattern would let the two drift.
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
            # Carry the flags too (e.g. `re.S` for multi-line expressions), or this reader would disagree
            # with the launcher. A flag that cannot be evaluated is a refusal.
            flags = 0
            for arg in list(call.args[1:]) + [kw.value for kw in call.keywords]:
                if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name) \
                        and arg.value.id == "re" and hasattr(re, arg.attr):
                    flags |= getattr(re, arg.attr)
                elif isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                    flags |= arg.value
                else:
                    return None, "%s:%s carries a flag this reader cannot evaluate" % (
                        os.path.relpath(EXPANDER_SOURCE, os.path.dirname(HERE)), node.lineno)
            return re.compile(call.args[0].value, flags), where
        return None, "%s:%s is not a re.compile(<string>) assignment" % (
            os.path.relpath(EXPANDER_SOURCE, os.path.dirname(HERE)), node.lineno)
    return None, "%s carries no %s assignment" % (EXPANDER_SOURCE, EXPANDER_NAME)


def _tree_from_ref(repo: str, ref: str, subpath: str, quiet: bool = False) -> tuple:
    """One subtree of <repo> at <ref>, unpacked into a temp dir: (dir, why_it_failed).

    `git archive` never touches the checkout; the temp dir is removed on exit. `quiet` suppresses the
    PREREQUISITE line for a subtree whose absence is reported as a state (`wdl/` -> `NO-WDL-DIR`).
    """
    repo = os.path.expanduser(repo)
    tmp = tempfile.mkdtemp(prefix="gsvtk-entity-")
    atexit.register(shutil.rmtree, tmp, True)
    arc = subprocess.run(["git", "-C", repo, "archive", ref, subpath], capture_output=True)
    if arc.returncode != 0:
        why = (arc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        why = (why or ["?"])[0][:200]
        if not quiet:
            print("PREREQUISITE `git -C %s archive %s %s` failed: %s" % (repo, ref, subpath, why))
        return "", "`git -C %s archive %s %s` failed: %s" % (repo, ref, subpath, why)
    untar = subprocess.run(["tar", "-x", "-C", tmp], input=arc.stdout, capture_output=True)
    if untar.returncode != 0:
        why = "could not unpack `git archive %s %s` from %s" % (ref, subpath, repo)
        if not quiet:
            print("PREREQUISITE %s" % why)
        return "", why
    return tmp, ""


def unfinished_template(neutral: str) -> tuple:
    """(`unbraced` count, why this substituted document cannot be read — "" when it can).

    An unclosed `${` hides a binding, and an unclosed `{{` would survive as raw template text classed
    `literal`. Neither is a JSON error, so both are refused here. Shared by both readers.
    """
    unbraced = len(re.findall(r"\$\{", neutral)) - len(BINDING_RE.findall(neutral))
    unterm = neutral.count("{{")
    why = []
    if unbraced:
        why.append("%d `${` with no closing `}`: a binding sits behind it that no reader here can see"
                   % unbraced)
    if unterm:
        why.append("%d `{{` with no closing `}}`: the shared expander cannot match it, so it survives "
                   "substitution as raw template text, which is not a value a launch may carry" % unterm)
    return unbraced, "; ".join(why)


def read_config(path: str, jinja_re) -> dict:
    """One launch config read STRUCTURALLY: {ok, keys, neutralised, dupkeys, unbraced, error}.

    `keys` keeps the document's nesting. `dupkeys` names keys bound more than once; the last value wins,
    as in `json.loads`.
    """
    out = {"ok": False, "keys": {}, "neutralised": 0, "dupkeys": [], "unbraced": 0, "error": ""}
    try:
        text = open(path).read()
    except (OSError, UnicodeDecodeError) as e:
        # UnicodeDecodeError is a ValueError, not an OSError; report it as cannot-parse, not a traceback.
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
    out["unbraced"], why = unfinished_template(neutral)
    if why:
        out["error"] = why
        return out
    out["ok"] = True
    out["keys"] = obj
    return out


def walk_strings(value, path: str = "", depth: int = 1) -> list:
    """[(key_path, string, depth)] for every string in a parsed value, nested objects/lists included.

    `depth` 1 is a binding value of the config map itself; 2+ is nested under a binding.
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
    """[(key_path, string, is_nested)] for every string binding in a config document."""
    return [(p, t, d > 1) for (p, t, d) in walk_strings(keys, "", 0)]


def reads_in(text: str) -> tuple:
    """(paths, bare_this_count) for one config value.

    `paths` are the `this.<path>` reads inside `${ ... }`, including list forms like `${[this.a, this.b]}`.
    `bare_this_count` counts `this.` outside `${ }`, which Terra treats as literal text.
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
    """<etype> for a name-key read (`sample_set_set_id` -> `sample_set_set`), or "" (e.g. for `id`)."""
    if attr.endswith("_id") and len(attr) > len("_id"):
        return attr[:-len("_id")]
    return ""


def read_tables(corpus_dir: str) -> tuple:
    """([table], other_tsv_count, unreadable[]) for the TSVs shipped in the corpus.

    A table is {etype, kind, path, columns}. A type column carrying `{{ ... }}` is reported as unreadable.
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
            except (OSError, UnicodeDecodeError) as e:
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
    # One table must carry every direct read and every member collection. The name column is implicit.
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
    """How many bindings read `${workspace.*}` (quoted by the no-name-key reason)."""
    n = 0
    for _p, text, _nested in binding_strings(cfg["keys"]):
        n += sum(1 for inner in BINDING_RE.findall(text) if "workspace." in inner)
    return n


def _row_shown(row: str) -> str:
    """One workspace-table row, printable on one line: tabs and CRs shown, so a finding quotes the bytes."""
    return row.replace("\t", "\\t").replace("\r", "\\r")


def read_workspace_table(full: str, rel: str) -> dict:
    """Parse one flavor's `workspace.tsv.tmpl`: the names it declares, and every structural finding.

    Only the first tab-separated field (the name) is parsed; the value field is counted but never read.
    There is no header row. Findings quote the row and repair nothing; `names` is in table order, unique.
    """
    o = {"found": True, "path": rel, "rows": 0, "names": [], "malformed": [], "dupes": [],
         "value_cells": 0}
    try:
        text = open(full).read()
    except (OSError, UnicodeDecodeError) as e:
        # The table exists but cannot be read: report MALFORMED rather than ABSENT or a traceback.
        o["malformed"].append("the flavor's own workspace table would not open and read as text (%s), so "
                              "nothing it declares is known" % e)
        return o
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()                                       # the final newline's artifact, not a blank row
    seen = {}
    for i, row in enumerate(lines, 1):
        o["rows"] += 1
        fields = row.split("\t")
        if len(fields) > 1 and fields[1].strip():
            # Counted before the name is judged, so malformed rows' values are still counted as unread.
            o["value_cells"] += 1
        name = fields[0].strip()
        if name.startswith(WORKSPACE_PREFIX):
            name = name[len(WORKSPACE_PREFIX):].strip()
        if not WORKSPACE_NAME_RE.match(name):
            o["malformed"].append("row %d: `%s` is not an attribute name ([A-Za-z0-9_.]+) — row: `%s`"
                                  % (i, name, _row_shown(row)))
            continue
        if len(fields) > 2:
            # A stray tab in the value half: still a finding, but the name is still declared.
            o["malformed"].append("row %d: %d tab-separated field(s), not the documented 2 — name `%s` is "
                                  "still declared; row: `%s`"
                                  % (i, len(fields), name, _row_shown(row)))
        seen.setdefault(name, []).append(i)
        if name not in o["names"]:
            o["names"].append(name)
    for name in o["names"]:
        rows_at = seen[name]
        if len(rows_at) > 1:
            o["dupes"].append("`%s` is declared %d times (rows %s) — one row per attribute is the shape "
                              "Terra imports, so which value that attribute ends up with is not in this file"
                              % (name, len(rows_at), ", ".join(str(r) for r in rows_at)))
    return o


def nearby_tsvs(flavor_dir: str, corpus_dir: str) -> list:
    """Every TSV a flavor ships, corpus-relative and sorted; printed when its oracle is ABSENT."""
    if not os.path.isdir(flavor_dir):
        return []
    found = []
    for dirpath, _dirs, files in os.walk(flavor_dir):
        for fn in files:
            if fn.endswith(".tsv") or fn.endswith(".tsv.tmpl"):
                found.append(os.path.relpath(os.path.join(dirpath, fn), corpus_dir).replace(os.sep, "/"))
    return sorted(found)


def read_workspace_oracle(corpus_dir: str, flavors: list) -> list:
    """One answer per flavor: the attribute names that flavor's own workspace table declares.

    `flavors` comes from where the configs live (so ABSENT is reachable). Status precedence is ABSENT,
    MALFORMED, DUPE, OK; all findings are kept regardless of the label.
    """
    out = []
    for flavor in sorted(set(flavors)):
        sub = "" if flavor == ROOT_FLAVOR else flavor
        expected = WORKSPACE_TABLE if flavor == ROOT_FLAVOR else "%s/%s" % (flavor, WORKSPACE_TABLE)
        flavor_dir = os.path.join(corpus_dir, sub)
        full = os.path.join(flavor_dir, WORKSPACE_TABLE)
        if os.path.isfile(full):
            o = read_workspace_table(full, expected)
        else:
            o = {"found": False, "path": None, "rows": 0, "names": [], "malformed": [], "dupes": [],
                 "value_cells": 0}
        o["flavor"] = flavor
        o["expected"] = expected
        o["attrs"] = len(o["names"])
        o["nearby"] = nearby_tsvs(flavor_dir, corpus_dir) if not o["found"] else []
        o["status"] = ("OK" if o["found"] and not o["malformed"] and not o["dupes"]
                       else "ABSENT" if not o["found"]
                       else "MALFORMED" if o["malformed"] else "DUPE")
        out.append(o)
    return out


def oracle_machine_line(o: dict) -> str:
    """The machine line for one flavor. `attrs=0 status=OK` (an empty table) is not `status=ABSENT`."""
    return "GSVTK-ORACLE flavor=%s file=%s attrs=%s status=%s" % (
        o["flavor"], o["path"] or "-", o["attrs"], o["status"])


def oracle_report(o: dict) -> list:
    """The human lines for one flavor: what was looked for, what answered, every finding quoted."""
    fmt = "  %-14s %-9s "                 # flavor, status, then a space before the sentence
    label = fmt % (o["flavor"][:14], o["status"])
    pad = " " * len(fmt % ("", ""))       # continuation lines line up under the sentence
    if o["status"] == "ABSENT":
        looked = ("no %s — that flavor has configs and ships no workspace attribute table, so if any of the "
                  "${workspace.*} names its bindings read, none of them can be checked against anything "
                  "(reads are not attributed to a flavor here: what is missing is the table, which is what "
                  "every such read would have to be found in" % o["expected"])
        out = [label + looked]
        if o["nearby"]:
            out.append("%sTSV(s) that flavor does ship: %s" % (pad, ", ".join(o["nearby"])))
        else:
            out.append("%sthat flavor ships no TSV of any kind" % pad)
        misfiled = [n for n in o["nearby"] if os.path.basename(n) == WORKSPACE_TABLE]
        if misfiled:
            out.append("%s%d of them carries the oracle's own name but is not the flavor's table: %s — the "
                       "table has to sit beside the flavor's configs (%s), because the flavor is read off "
                       "where its configs live, not off where a table happens to be"
                       % (pad, len(misfiled), ", ".join(misfiled), o["expected"]))
        return out
    listed = ", ".join(o["names"][:ORACLE_NAMES_SHOWN])
    if len(o["names"]) > ORACLE_NAMES_SHOWN:
        listed += " (+%d more, not listed)" % (len(o["names"]) - ORACLE_NAMES_SHOWN)
    out = ["%s%s declares %d attribute(s) from %d row(s): %s"
           % (label, o["path"], o["attrs"], o["rows"], listed or "-")]
    if not o["names"] and not o["malformed"]:
        out.append("%sthe table exists and declares nothing — a different answer from ABSENT, which says "
                   "there is no table to declare anything" % pad)
    out += ["%sMALFORMED %s" % (pad, f) for f in o["malformed"]]
    out += ["%sDUPE %s" % (pad, f) for f in o["dupes"]]
    return out


def _json_string_spans(text: str) -> list:
    """[(start, end)] for the JSON string literals in `text`, quotes included, escapes honoured.

    Used to tell whether a `{{ ... }}` sits inside a string or bare in value position, which decides
    whether its marker needs quotes.
    """
    spans, i, n = [], 0, len(text)
    while i < n:
        if text[i] != '"':
            i += 1
            continue
        j = i + 1
        while j < n:
            if text[j] == "\\":
                j += 2
                continue
            if text[j] == '"':
                break
            j += 1
        spans.append((i, min(j + 1, n)))
        i = spans[-1][1]
    return spans


def parse_with_bundle_marks(path: str, jinja_re) -> dict:
    """Re-read one config with every `{{ ... }}` replaced by a MARKER STRING instead of `null`.

    A marker lets a bundle render be traced to the key that carries it. `exprs` holds each expression in
    document order; `claimed` holds every marker seen in any key or value, including values dropped by
    duplicate keys. `dropped` counts those dropped values and the workspace reads they carried (the last
    value wins, as in `json.loads`).
    """
    out = {"ok": False, "obj": {}, "exprs": [], "claimed": set(), "dropped": {"values": 0, "ws_reads": 0},
           "error": ""}
    try:
        text = open(path).read()
    except (OSError, UnicodeDecodeError) as e:
        out["error"] = str(e)
        return out
    spans = _json_string_spans(text)
    exprs = []

    def swap(m):
        src = JINJA_MARK_SRC % len(exprs)
        exprs.append(m.group(0))
        return src if any(s < m.start() < e for s, e in spans) else '"%s"' % src

    claimed = set()
    dropped = out["dropped"]

    def keep(pairs):
        last = {}
        for k, val in pairs:
            for side in (k, val):
                # Walks containers, so markers inside lists and objects are claimed too.
                claimed.update(str(m) for m in _reads_of(side)[0])
            if k in last:
                # `dict(pairs)` keeps the last value, so the earlier one (`last[k]`) is what gets dropped.
                dropped["values"] += 1
                _marks, ws, _ths = _reads_of(last[k])
                dropped["ws_reads"] += len(ws)
            last[k] = val
        return dict(pairs)                                # last wins, exactly json.loads' own rule

    substituted = jinja_re.sub(swap, text)
    try:
        obj = json.loads(substituted, object_pairs_hook=keep)
    except ValueError as e:
        out["error"] = str(e)
        return out
    if not isinstance(obj, dict):
        out["error"] = "top level is a %s, not an object of bindings" % type(obj).__name__
        return out
    _unbraced, why = unfinished_template(substituted)
    if why:
        # Refuse the same documents the entity layer refuses.
        out["error"] = why
        return out
    out.update(ok=True, obj=obj, exprs=exprs, claimed=claimed)
    return out


def walk_values(value, path: str = "", depth: int = 0) -> list:
    """[(key_path, scalar, depth)] for every JSON scalar in a parsed config, containers entered.

    Unlike `walk_strings`, non-string scalars are included (they count as `literal`). `depth` 1 is a
    binding value of the config map itself; 2+ is nested.
    """
    out = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.extend(walk_values(v, "%s.%s" % (path, k), depth + 1))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(walk_values(v, "%s[%d]" % (path, i), depth + 1))
    else:
        out.append((path, value, depth))
    return out


def _leaf_key(path: str) -> str:
    """The leaf key name of a walk path: `.Foo.manta_docker` -> `manta_docker`, `.Foo.args[2]` -> `args`."""
    return re.sub(r"(?:\[\d+\])+\Z", "", path.split(".")[-1])


def _reads_of(value):
    """(bundle marker indexes, workspace names, this paths) carried by one config value, containers walked."""
    marks, ws, ths = [], [], []
    for _path, text, _depth in walk_strings(value):
        marks.extend(int(m) for m in JINJA_MARK_RE.findall(text))
        for inner in BINDING_RE.findall(text):
            ws.extend(WORKSPACE_READ_RE.findall(inner))
            ths.extend(THIS_TOKEN_RE.findall(inner))
    return marks, ws, ths


def _bundle_root(expr: str) -> str:
    """The root key of one `{{ root.attr | tojson }}` expression — the bundle's NAME, never a file."""
    m = BUNDLE_ROOT_RE.search(expr)
    return m.group(1) if m else "?"


def _test_profile_stem(name: str) -> str:
    """The stem of a test profile file name (`test_*.json`), or "". Profiles are never opened."""
    if name.startswith(TEST_PROFILE_PREFIX) and name.endswith(".json"):
        return name[:-len(".json")]
    return ""


def test_profile_stems(tree: str) -> list:
    """Stems of the `inputs/values/test_*.json` profiles in a materialized tree (`--tree`)."""
    out = []
    for dirpath, _dirs, files in os.walk(os.path.join(tree, TEST_PROFILE_DIR)):
        for fn in files:
            stem = _test_profile_stem(fn)
            if stem:
                out.append(stem)
    return sorted(set(out))


def test_profile_stems_at_ref(repo: str, ref: str) -> list:
    """The same stems, listed at a ref with `git ls-tree` (the archived temp tree holds only the corpus).

    A failed listing returns []; that only affects the wording of a refusal, never whether it refuses.
    """
    lst = subprocess.run(["git", "-C", os.path.expanduser(repo), "ls-tree", "--name-only", ref,
                          TEST_PROFILE_DIR + "/"], capture_output=True)
    if lst.returncode != 0:
        return []
    out = []
    for line in (lst.stdout or b"").decode("utf-8", "replace").splitlines():
        stem = _test_profile_stem(os.path.basename(line.strip()))
        if stem:
            out.append(stem)
    return sorted(set(out))


def _test_bundle_reason(value, roots: list, test_profiles: list) -> str:
    """Why this value belongs to a TEST bundle, or "" when nothing here says it does.

    Either a `{{ root.* }}` whose root begins `test_` or is a shipped test profile stem, or a string
    containing a test-sample coordinate anywhere in it.
    """
    for root in roots:
        if root.lower().startswith(TEST_PROFILE_PREFIX):
            return ("its `{{ %s.* }}` read names a test values profile by root (shipped test profile(s): "
                    "%s; no profile was opened to decide this)" % (root, ", ".join(test_profiles) or "none"))
        # Currently unreachable (every stem starts `test_`); kept for its more specific message.
        if root in test_profiles:
            return "its `{{ %s.* }}` read names the shipped test profile inputs/values/%s.json" % (root, root)
    if isinstance(value, str):
        for marker in TEST_COORDINATE_MARKERS:
            if marker in value:
                return ("it carries the test-sample coordinate `%s`, the shape a test bundle's data comes in"
                        % marker)
    return ""


def classify_values(full: str, rel: str, flavor: str, wf: str, oracle: dict, jinja_re,
                    test_profiles: list) -> dict:
    """Every value of one config, each classed by the bundle it came from. Deep, one class per value.

    `oracle` is that flavor's record from `read_workspace_oracle()`; only `status == "OK"` resolves
    workspace reads. The file is re-read with bundle markers (see `parse_with_bundle_marks`).

    Class precedence: docker (a `*_docker` key, or a value that is exactly one `${workspace.*_docker}`
    read), then test-bundle, then the whole-value forms, then `mixed` for anything still carrying a read,
    then `literal`. A `mixed` value with an unanswerable workspace read refuses under a `mixed-` reason.
    """
    classes = dict((c, 0) for c in VALUE_CLASSES)
    v = {"config": rel, "flavor": flavor, "wf": wf, "classes": classes, "bindings": 0, "nested_reads": 0,
         "ws_reads": 0, "ws_resolved": 0, "ws_missing": 0, "ws_no_oracle": 0, "bundles": [],
         "dropped_values": 0, "dropped_ws_reads": 0, "docker_keys": [], "docker_unenforced": [],
         "findings": [], "reasons": [], "unresolved": 0, "state": "OK", "unclassified": "",
         # Per-key class and container paths, used by the required-input layer.
         "class_of": {}, "containers": []}
    parsed = parse_with_bundle_marks(full, jinja_re)
    if not parsed["ok"]:
        # Distinct from `no-values`.
        v["unclassified"] = "values-unreadable"
        v["reasons"].append("values-unreadable")
        v["findings"].append({"key": "-", "cls": "-", "reason": "values-unreadable",
                              "detail": parsed["error"], "names": []})
        v["state"] = "UNRESOLVED"
        return v
    clean = bool(oracle) and oracle["status"] == "OK"
    declared = set(oracle["names"]) if clean else set()
    status = oracle["status"] if oracle else "ABSENT"
    obj, exprs = parsed["obj"], parsed["exprs"]
    seen = set()
    for path, value, depth in walk_values(obj):
        key = path[1:] if path.startswith(".") else path
        marks, ws, ths = _reads_of(value)
        seen.update(str(m) for m in marks)
        for name in ws:
            v["ws_reads"] += 1
            if not clean:
                v["ws_no_oracle"] += 1
            elif name in declared:
                v["ws_resolved"] += 1
            else:
                v["ws_missing"] += 1
        if depth > 1:
            v["nested_reads"] += len(marks) + len(ws) + len(ths)
        roots = [_bundle_root(exprs[i]) for i in marks if i < len(exprs)]
        for root in roots:
            if root not in v["bundles"]:
                v["bundles"].append(root)
        whole_ws = WORKSPACE_WHOLE_RE.match(value) if isinstance(value, str) else None
        whole_mark = JINJA_MARK_RE.fullmatch(value) if isinstance(value, str) else None
        whole_this = THIS_WHOLE_RE.match(value) if isinstance(value, str) else None
        test_bundle = _test_bundle_reason(value, roots, test_profiles)
        # Docker exclusion: a value that is exactly one `${workspace.*_docker}` read is an image whatever
        # the key is called (checked first). A `*_docker` key leaf is checked last, and only for a value that
        # met no refusal, so a key name can never turn a refusal into a pass.
        whole_docker = bool(whole_ws) and whole_ws.group(1).endswith(DOCKER_KEY_SUFFIX)
        leaf_docker = _leaf_key(path).endswith(DOCKER_KEY_SUFFIX)
        refused = False                       # did this VALUE meet a refusal, whatever class it landed in
        if whole_docker:
            cls = DOCKER_CLASS
        elif test_bundle:
            cls = "test-bundle"
            refused = True
            v["findings"].append({"key": key, "cls": cls, "reason": "test-bundle",
                                  "detail": test_bundle, "names": []})
        elif whole_mark:
            cls = "needs-bundle"
        elif whole_ws:
            name = whole_ws.group(1)
            if not clean:
                cls = "no-oracle"
                refused = True
                v["findings"].append({"key": key, "cls": cls, "reason": NO_ORACLE_REASONS[status],
                                      "detail": name, "names": [name]})
            elif name in declared:
                cls = "workspace-attr"
            else:
                cls = "workspace-missing"
                refused = True
                v["findings"].append({"key": key, "cls": cls, "reason": "workspace-missing",
                                      "detail": name, "names": [name]})
        elif whole_this:
            cls = "entity"
        elif marks or ws or ths:
            cls = "mixed"
            if ws and not clean:
                refused = True
                v["findings"].append({"key": key, "cls": cls, "reason": MIXED_NO_ORACLE_REASONS[status],
                                      "detail": "", "names": sorted(set(ws))})
            elif ws and any(n not in declared for n in ws):
                refused = True
                v["findings"].append({"key": key, "cls": cls, "reason": "mixed-workspace-missing",
                                      "detail": "", "names": sorted(n for n in ws if n not in declared)})
        else:
            cls = "literal"
        if leaf_docker and not refused:
            cls = DOCKER_CLASS
        if cls == DOCKER_CLASS:
            v["docker_keys"].append(key)
            if not leaf_docker:
                # Docker by value only: the launcher's pin gate keys on the key name, so it won't ask.
                v["docker_unenforced"].append(key)
        classes[cls] += 1
        v["class_of"][key] = cls
        v["bindings"] += 1
    # Same path spelling as `walk_values`; the document root is not a key.
    v["containers"] = [p[1:] if p.startswith(".") else p
                       for p, _kind in walk_containers(obj) if p]
    v["dropped_values"] = parsed["dropped"]["values"]
    v["dropped_ws_reads"] = parsed["dropped"]["ws_reads"]
    # Internal consistency check: every marker should be seen by one of the two scans. No input should
    # reach this; the selftest triggers it by injection.
    unclaimed = [i for i in range(len(exprs)) if str(i) not in seen and str(i) not in parsed["claimed"]]
    if unclaimed:
        v["findings"].append({"key": "-", "cls": "-", "reason": "bundle-read-unattributed",
                              "detail": "%d marker(s) in a document whose every other marker is claimed"
                                        % len(unclaimed),
                              "names": [exprs[i].strip() for i in unclaimed]})
    if not v["bindings"]:
        v["reasons"].append("no-values")
    for f in v["findings"]:
        if f["reason"] not in v["reasons"]:
            v["reasons"].append(f["reason"])
    v["unresolved"] = sum(classes[c] for c in VALUE_REFUSAL_CLASSES)
    if v["unresolved"] or v["reasons"]:
        v["state"] = "UNRESOLVED"
    return v


def _value_class_tail(classes: dict) -> str:
    """The class fields, generated FROM `VALUE_CLASSES` so a class cannot exist without being printed."""
    return " ".join("%s=%s" % (cls, classes[cls]) for cls in VALUE_CLASSES)


def _value_read_tail(nested, ws, resolved, missing, no_oracle, bundles, dropped_values,
                     dropped_reads) -> str:
    """The read-level fields, shared by the per-config line and the census.

    `workspace-reads` is the sum of the resolved, missing and no-oracle read counts.
    """
    return ("nested-reads=%s workspace-reads=%s workspace-resolved-reads=%s workspace-missing-reads=%s "
            "no-oracle-reads=%s bundles=%s dropped-values=%s dropped-workspace-reads=%s"
            % (nested, ws, resolved, missing, no_oracle, ",".join(bundles) or "-", dropped_values,
               dropped_reads))


def value_machine_line(v: dict) -> str:
    """The value line for one config, printed beside its `GSVTK-ENTITY` line.

    `bindings=` is the sum of the class fields. `unresolved=` counts values in refusing classes;
    `state=UNRESOLVED` also covers refusals listed only in `reasons=`.
    """
    return ("GSVTK-VALUE config=%s flavor=%s wf=%s bindings=%s %s %s unresolved=%s state=%s reasons=%s"
            % (v["config"], v["flavor"], v["wf"], v["bindings"], _value_class_tail(v["classes"]),
               _value_read_tail(v["nested_reads"], v["ws_reads"], v["ws_resolved"], v["ws_missing"],
                                v["ws_no_oracle"], v["bundles"], v["dropped_values"],
                                v["dropped_ws_reads"]), v["unresolved"], v["state"],
               ",".join(v["reasons"]) or "-"))


def value_summary_line(records: list) -> str:
    """The census of the value lines; every total is summed from the per-config records.

    The `configs-*` counters keep "no values", "unreadable" and "all resolved" distinct.
    """
    tot = dict((c, 0) for c in VALUE_CLASSES)
    keys = ("bindings", "nested_reads", "ws_reads", "ws_resolved", "ws_missing", "ws_no_oracle",
            "dropped_values", "dropped_ws_reads", "unresolved")
    sums = dict((k, 0) for k in keys)
    bundles = []
    cfg = dict((k, 0) for k in ("unresolved", "no-oracle", "needs-bundle", "no-values", "unclassified"))
    for v in records:
        for cls in VALUE_CLASSES:
            tot[cls] += v["classes"][cls]
        for k in keys:
            sums[k] += v[k]
        bundles.extend(b for b in v["bundles"] if b not in bundles)
        if v["state"] != "OK":
            cfg["unresolved"] += 1
        if v["classes"]["no-oracle"]:
            cfg["no-oracle"] += 1
        if v["classes"]["needs-bundle"]:
            cfg["needs-bundle"] += 1
        # An unreadable document counts as unclassified only, not also as no-values.
        if not v["bindings"] and not v["unclassified"]:
            cfg["no-values"] += 1
        if v["unclassified"]:
            cfg["unclassified"] += 1
    return ("GSVTK-VALUE-SUMMARY configs=%s bindings=%s %s %s unresolved=%s configs-unresolved=%s "
            "configs-no-oracle=%s configs-needs-bundle=%s configs-no-values=%s configs-unclassified=%s"
            % (len(records), sums["bindings"], _value_class_tail(tot),
               _value_read_tail(sums["nested_reads"], sums["ws_reads"], sums["ws_resolved"],
                                sums["ws_missing"], sums["ws_no_oracle"], bundles, sums["dropped_values"],
                                sums["dropped_ws_reads"]), sums["unresolved"], cfg["unresolved"],
               cfg["no-oracle"], cfg["needs-bundle"], cfg["no-values"], cfg["unclassified"]))


def _declared_names(oracle: dict) -> str:
    """The names an oracle declares, capped for a human line the way the oracle block caps them."""
    names = oracle["names"] if oracle else []
    listed = ", ".join("`%s`" % n for n in names[:ORACLE_NAMES_SHOWN])
    if len(names) > ORACLE_NAMES_SHOWN:
        listed += " (+%d more, not listed)" % (len(names) - ORACLE_NAMES_SHOWN)
    return listed or "none"


def value_reason_text(f: dict, oracle: dict) -> str:
    """One refused value as a sentence: what was looked for, in which file, what was found, and the cure.

    The cure depends on the oracle status (add a row, create the file, repair or collapse rows) and always
    names the same file the diagnosis named. The selftest applies each cure to verify it.
    """
    reason, key = f["reason"], f["key"]
    table = (oracle["path"] or oracle["expected"]) if oracle else "?"
    status = oracle["status"] if oracle else "ABSENT"
    attrs = oracle["attrs"] if oracle else 0
    names = ", ".join("`%s`" % n for n in f["names"]) or "-"
    mixed = reason.startswith("mixed-")
    shape = ("" if not mixed else
             "the value is text around reads, so it has no whole-value answer to grade, and the "
             "${workspace.*} read inside it is what was looked for: ")
    if reason in ("workspace-missing", "mixed-workspace-missing"):
        return ("%s%s reads %s and `%s` does not declare it: that file declares %d attribute name(s), "
                "status=%s, and none of them is this one. Looked for that first field (an optional "
                "`workspace:` prefix stripped) in that file and no other; what it does declare: %s. A name "
                "with no row cannot be launched — the alternative is a submission that discovers the missing "
                "input after the VMs booted. To clear it: add a row whose FIRST field is each name quoted "
                "above to `%s` %s or stop binding `%s`"
                % (shape, key, names, table, attrs, status, _declared_names(oracle),
                   table, CURE_ONLY_FILE, key))
    if mixed:
        head = "%s%s reads %s; `%s` is status=%s" % (shape, key, names, table, status)
    else:
        head = "%s reads `%s`; `%s` is status=%s" % (key, f["detail"], table, status)
    if status == "ABSENT":
        # Nothing to edit: the cure is a file to create.
        where = os.path.dirname(table) or "the top of that corpus directory"
        return ("%s because no such file exists: that flavor has configs and ships no workspace attribute "
                "table, so there is nothing this name could be checked against and NOTHING read through it "
                "resolves. We resolve a workspace attribute only from a table we read cleanly, and here "
                "there is no table at all. To clear it there is nothing to edit and a file to CREATE: `%s`, "
                "in %s, the directory that flavor's configs live in; every other TSV that flavor ships is "
                "something else and none of them is read for a workspace attribute. Add a first row whose "
                "FIRST field is each name quoted above (a leading `workspace:` on that field is optional; it "
                "is stripped before the name is compared), or stop binding `%s` — and until that file exists, "
                "nothing read through it resolves. What that flavor ships today is what EXISTS, not what to "
                "edit: %s"
                % (head, table, where, key,
                   ", ".join(oracle["nearby"]) or "none — it ships no TSV of any kind"))
    findings = ((oracle["malformed"] if status == "MALFORMED" else oracle["dupes"]) or [])
    why = ("a stray column means the file's shape is not what was assumed about its FIRST column, so a name "
           "read cleanly out of it is not a name that can be trusted" if status == "MALFORMED" else
           "an attribute declared twice has no single value to carry, so which one a launch would use is not "
           "in this file")
    cure = ("repair the row(s) quoted above in `%s` %s until the file reads cleanly" % (table, CURE_ONLY_FILE)
            if status == "MALFORMED" else
            "collapse the row(s) quoted above in `%s` %s to one row per attribute" % (table, CURE_ONLY_FILE))
    return ("%s (%d finding(s); first: %s), so the %d name(s) it did read cleanly do NOT resolve either: "
            "%s. We resolve a workspace attribute only from a table we read cleanly — this one was read, and "
            "it did not read cleanly. To clear it: %s, or stop binding `%s`"
            % (head, len(findings), findings[0] if findings else "-", attrs, why, cure, key))


def value_findings_text(f: dict) -> str:
    """The refusals that are not about an oracle at all, said once outside the flavor's vocabulary."""
    if f["reason"] == "test-bundle":
        return ("%s belongs to a TEST bundle: %s. Never launchable, and never resolved into a launch "
                "argument by this tool: the sources a launch may carry are the config's own literal, a "
                "${workspace.*} row this flavor declares, or a bundle an operator states"
                % (f["key"], f["detail"]))
    if f["reason"] == "values-unreadable":
        return ("no value of this config was classified: the document will not parse even after the one "
                "`{{ ... }}` -> marker substitution (%s), so this is `values-unreadable`, not `no-values`, "
                "and never a pass" % f["detail"])
    if f["reason"] == "bundle-read-unattributed":
        return ("%d `{{ ... }}` expression(s) in this file could not be tied to a binding value or key (%s), "
                "so this tool refuses to guess which key would have carried them"
                % (len(f["names"]), ", ".join(f["names"])))
    if f["reason"] == "no-values":
        return ("the document binds no value at all (0 scalar(s)), so there is nothing to resolve and no "
                "clean verdict to print: `no configs`, `no values`, `values-unreadable` and `every value "
                "resolved` are FOUR different answers, and one 0 never stands for two of them")
    return ""


def value_report(v: dict, oracle: dict) -> list:
    """The human lines for one config, printed only when there is a refusal or disclosure to explain."""
    pad = " " * 8
    said = []
    for f in v["findings"]:
        text = value_findings_text(f) or value_reason_text(f, oracle)
        said.append("        UNRESOLVED-VALUE %s [%s] %s" % (f["key"], f["cls"], text))
    if not v["findings"] and "no-values" in v["reasons"]:
        said.append("        UNRESOLVED-VALUE - [no-values] %s" % value_findings_text({"reason": "no-values"}))
    if v["classes"]["needs-bundle"] or v["bundles"]:
        said.append("%sNEEDS-BUNDLE bundle=%s (%d value(s)): a `{{ ... }}` render is NAMED here and never "
                    "resolved — this tool renders nothing and never opens a values profile, so a root is a "
                    "bundle NAME and not a file, and no claim is made that a profile answering it exists. A "
                    "launch may not carry these values until an operator states the bundle (exit 0: this is "
                    "a disclosure, not a refusal)"
                    % (pad, ",".join(v["bundles"]), v["classes"]["needs-bundle"]))
    if v["docker_keys"]:
        said.append("%sDOCKER-MUST-BE-STATED %d key(s) (%s): a container image is never resolved from an "
                    "ambient profile here — `--image KEY=REF` or a profile pin is the only source "
                    "(terra/batch_rerun_step.py::_stated()); a binding is excluded when its leaf key ends "
                    "`_docker` OR when the value it binds IS a single `${workspace.*_docker}` read (ruling "
                    "R18: the rule is about the image, not about what the key is called; narrowed by R20 to "
                    "the whole value, so a command that only MENTIONS an image stays a command and is still "
                    "graded for every other read it carries); a refusal always beats this exclusion, so a "
                    "`*_docker` key bound to a test bundle or to an attribute this flavor does not declare "
                    "is reported as that refusal and not as a withheld image"
                    % (pad, len(v["docker_keys"]), ", ".join(v["docker_keys"])))
    if v["docker_unenforced"]:
        # The launcher asks for a pin by key name (`_stated()`), so value-only docker keys go unenforced.
        said.append("%sDOCKER-KEY-UNENFORCED %d of those key(s) (%s): docker-shaped by their VALUE, not by a "
                    "leaf ending `_docker`, and the launcher's pin gate keys on the NAME "
                    "(terra/batch_rerun_step.py::_stated()) — so this tool withholds the value, and nothing "
                    "upstream will ever ask for a pin under this key. State the image under a `_docker` key, "
                    "or the launcher runs without checking it"
                    % (pad, len(v["docker_unenforced"]), ", ".join(v["docker_unenforced"])))
    if v["nested_reads"]:
        said.append("%s%d read(s) sit below binding level, inside a nested object or list — the shape a "
                    "reader that looks only at binding values loses" % (pad, v["nested_reads"]))
    if v["dropped_values"]:
        said.append("%s%d value(s) dropped by a duplicated key. This is the PARSED reader, so `json.loads`' "
                    "last-wins chose the side and the dropped side's %d ${workspace.*} read(s) are counted "
                    "nowhere here; the key is named as `dupkeys=` on the entity line, and a raw-text reader "
                    "sees %d read(s) where this one sees %d"
                    % (pad, v["dropped_values"], v["dropped_ws_reads"],
                       v["ws_reads"] + v["dropped_ws_reads"], v["ws_reads"]))
    if not said:
        return []
    census = " ".join("%s=%s" % (c, v["classes"][c]) for c in VALUE_CLASSES if v["classes"][c])
    return ["  %s (%s): %d value(s), %s\n%s"
            % (v["config"], v["flavor"], v["bindings"], census or "no class", "\n".join(said))]


# ------------------------------------------------------------------------------------------- required inputs
# Every required input of every workflow a launch config binds must be carried by a launchable value.

def walk_containers(value, path: str = "") -> list:
    """[(key_path, kind)] for every object and list node in a parsed config (`walk_values`'s complement).

    A struct input can be bound as one object, so its key has no scalar; this keeps it from reading as
    `unbound`.
    """
    out = []
    if isinstance(value, dict):
        out.append((path, "object"))
        for k, v in value.items():
            out.extend(walk_containers(v, "%s.%s" % (path, k)))
    elif isinstance(value, list):
        out.append((path, "list"))
        for i, v in enumerate(value):
            out.extend(walk_containers(v, "%s[%d]" % (path, i)))
    return out


def _required_reader():
    """Import `required_inputs` and `load_workflow` from wdl_inputs_check: ((fn, loader) or None, where).

    Shared so defaults, optionals and struct members are handled the same way in both checkers.
    """
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    try:
        import wdl_inputs_check
    except Exception as exc:                       # noqa: BLE001 - a missing sibling is a state, not a traceback
        return None, "%s cannot be imported (%s: %s)" % (
            os.path.relpath(REQUIRED_SOURCE, os.path.dirname(HERE)), type(exc).__name__, exc)
    fn = getattr(wdl_inputs_check, "required_inputs", None)
    loader = getattr(wdl_inputs_check, "load_workflow", None)
    if fn is None or loader is None:
        return None, "%s carries no required_inputs()/load_workflow() to reuse" % REQUIRED_WHERE
    return (fn, loader), "%s (one predicate, one loader, imported not copied)" % REQUIRED_WHERE


def _wdl_module():
    """(True, "") when miniwdl is importable, else (False, why)."""
    try:
        import WDL                                 # noqa: F401 - the probe IS the answer
    except ImportError as exc:
        return False, "miniwdl is not importable (%s)" % exc
    return True, ""


def capture_workflow(wf: str, ctx: dict, cache: dict) -> dict:
    """One workflow's required-input set, loaded at most once per run and cached under its name.

    The file is `<wdl_dir>/<workflow>.wdl`; `file` is printed tree-relative (`wdl/<workflow>.wdl`), not as
    the temp-dir path.
    """
    if wf in cache:
        return cache[wf]
    shown = "%s/%s.wdl" % (WDL_SUBTREE, wf)
    cap = {"wf": wf, "status": "OK", "file": shown, "required": [], "note": ""}
    if not ctx["miniwdl_ok"]:
        cap.update(status="NO-MINIWDL", file="-", note=ctx["miniwdl_note"])
    elif not ctx["dir_ok"]:
        # This note is printed once in the banner, so it must make sense on its own.
        cap.update(status="NO-WDL-DIR", file="-", note=ctx["dir_note"] or (
            "no %s/ directory under the tree, so no workflow's required inputs can be read" % WDL_SUBTREE))
    else:
        doc, bad, note = ctx["loader"](ctx["wdl_dir"], wf)
        if doc is None:
            # `files=` lists only what was actually opened.
            cap.update(status=bad, file="-", note=note)
            if bad == "NO-WDL" and os.path.isfile(os.path.join(ctx["wdl_dir"], wf + ".wdl")):
                # The loader reports a tasks-only file as NO-WDL; split it out here.
                cap["status"] = "NO-WORKFLOW"
        else:
            cap["required"] = ctx["reader"](doc)
    cache[wf] = cap
    return cap


def grade_required(values: dict, prefixes: list, ctx: dict, cache: dict) -> dict:
    """One config's required-input answer: what its workflow(s) demand, against what this config gives.

    Each required input takes the value layer's class for the key that carries it (or `container` /
    `unbound`). A config with an unsatisfied input is UNRESOLVED even if another workflow was not captured;
    otherwise any uncaptured workflow makes it NOT-CAPTURED.
    """
    r = {"config": values["config"], "flavor": values["flavor"], "wf": values["wf"],
         "classes": dict((c, 0) for c in REQUIRED_CLASSES), "required": 0, "captures": [],
         "capture": "OK", "state": "OK", "reason": "-", "unsatisfied": [], "files": [], "notes": []}
    if values["unclassified"] == "values-unreadable":
        r["capture"] = "NO-DOCUMENT"
        r["state"] = "NOT-CAPTURED"
        r["notes"].append("this config is a document neither reader here can parse, so it names no workflow "
                          "whose required inputs could be asked for")
        return r
    if not prefixes:
        r["capture"] = "NO-DOCUMENT"
        r["state"] = "NOT-CAPTURED"
        r["notes"].append("the document binds no key at all, so it names no workflow whose required inputs "
                          "could be asked for")
        return r
    seen = set()
    for wf in prefixes:
        cap = capture_workflow(wf, ctx, cache)
        r["captures"].append(cap)
        if cap["file"] != "-" and cap["file"] not in r["files"]:
            r["files"].append(cap["file"])
        if cap["status"] != "OK":
            continue
        for key, type_text, line, _parent in cap["required"]:
            if key in seen:                        # two files declaring one namespace: ask a key once
                continue
            seen.add(key)
            if key in values["class_of"]:
                cls = values["class_of"][key]
            elif key in values["containers"]:
                cls = "container"
            else:
                cls = "unbound"
            r["classes"][cls] += 1
            r["required"] += 1
            if cls in REQUIRED_REASON_OF:
                r["unsatisfied"].append({"key": key, "cls": cls,
                                         "reason": REQUIRED_REASON_OF[cls], "type": type_text,
                                         "line": line, "file": cap["file"], "config": values["config"]})
    for word in REQUIRED_CAPTURES:
        if word != "OK" and any(c["status"] == word for c in r["captures"]):
            r["capture"] = word
            break
    if r["unsatisfied"]:
        r["state"] = "UNRESOLVED"
        for word in REQUIRED_REASONS:
            if any(f["reason"] == word for f in r["unsatisfied"]):
                r["reason"] = word
                break
    elif r["capture"] != "OK":
        r["state"] = "NOT-CAPTURED"
    return r


def _required_class_tail(classes: dict) -> str:
    """The class fields, generated from `REQUIRED_CLASSES`."""
    return " ".join("%s=%s" % (c, classes[c]) for c in REQUIRED_CLASSES)


def required_machine_line(r: dict) -> str:
    """The required line for one config, printed beside its entity and value lines.

    `required=` is the sum of the class fields. `capture=` is not a verdict: `capture=NO-WDL` goes with
    `state=NOT-CAPTURED`, never `OK`.
    """
    return ("GSVTK-REQUIRED config=%s flavor=%s wf=%s workflows=%s capture=%s required=%s %s "
            "files=%s state=%s reason=%s"
            % (r["config"], r["flavor"], r["wf"], len(r["captures"]), r["capture"], r["required"],
               _required_class_tail(r["classes"]), ",".join(r["files"]) or "-", r["state"], r["reason"]))


def required_summary_line(records: list) -> str:
    """The census of the required lines, summed from the per-config records.

    `required` is the sum of the class fields; `configs` equals both the sum of the state counters and the
    sum of the capture-word counters.
    """
    tot = dict((c, 0) for c in REQUIRED_CLASSES)
    states = dict((s, 0) for s in REQUIRED_STATES)
    captures = dict((w, 0) for w in REQUIRED_CAPTURES)
    workflows, captured = set(), set()
    for r in records:
        for cls in REQUIRED_CLASSES:
            tot[cls] += r["classes"][cls]
        states[r["state"]] += 1
        captures[r["capture"]] += 1
        for cap in r["captures"]:
            workflows.add(cap["wf"])
            if cap["status"] == "OK":
                captured.add(cap["wf"])
    return ("GSVTK-REQUIRED-SUMMARY configs=%s required=%s %s workflows=%s workflows-captured=%s "
            "configs-ok=%s configs-unsatisfied=%s configs-not-captured=%s "
            "capture-no-miniwdl=%s capture-no-wdl-dir=%s capture-no-document=%s "
            "capture-load-failure=%s capture-no-workflow=%s capture-no-wdl=%s capture-ok=%s"
            % (len(records), sum(tot.values()), _required_class_tail(tot), len(workflows), len(captured),
               states["OK"], states["UNRESOLVED"], states["NOT-CAPTURED"],
               captures["NO-MINIWDL"], captures["NO-WDL-DIR"], captures["NO-DOCUMENT"],
               captures["LOAD-FAILURE"], captures["NO-WORKFLOW"], captures["NO-WDL"], captures["OK"]))


def required_uncaptured_cause(records: list) -> str:
    """The one capture word explaining why required inputs went unasked, `mixed`, or `-`."""
    words = {r["capture"] for r in records if r["capture"] != "OK"}
    if not words:
        return "-"
    return words.pop() if len(words) == 1 else "mixed"


def uncaptured_workflows(records: list) -> tuple:
    """(capped list of workflow names this run could not read, total count)."""
    names = sorted({c["wf"] for r in records for c in r["captures"] if c["status"] != "OK"})
    shown = names[:REQUIRED_UNCAPTURED_SHOWN]
    tail = " (+%d more, not listed)" % (len(names) - len(shown)) if len(names) > len(shown) else ""
    return (", ".join(shown) + tail if shown else "none", len(names))


def required_reason_text(f: dict) -> str:
    """One unsatisfied required input, with where it is declared and how to fix it.

    For refusals inherited from the value layer, this points at that finding instead of repeating it.
    """
    where = "%s:%s" % (f["file"], f["line"]) if f["line"] else f["file"]
    if f["reason"] == "required-unbound":
        decl = f["key"].rsplit(".", 1)[-1]
        return ("%s (%s) is declared with no default and not optional at `%s`, and `%s` binds no key for "
                "it — not a `${this.…}` entity read, not a `${workspace.…}` attribute, not a literal. "
                "Cromwell discovers this at input-resolution time, after the VMs booted. To clear it: bind "
                "\"%s\" in `%s` (a literal, an entity read, a workspace attribute this flavor declares, or "
                "a `{{ … }}` bundle render an operator states), or make `%s` optional / give it a default "
                "in `%s` — a defaulted or optional declaration is satisfied by the WDL itself"
                % (f["key"], f["type"], where, f["config"], f["key"], f["config"], decl, f["file"]))
    return ("%s (%s) is a REQUIRED input — declared at `%s` — and the value that carries it is refused "
            "above as [%s] %s. The cure is the one that finding prints; until it is cleared this key has "
            "no value to launch with"
            % (f["key"], f["type"], where, f["cls"], f["config"]))


def required_report(r: dict) -> list:
    """The human lines for one config's required inputs: refusals, disclosures, uncaptured workflows."""
    pad = " " * 8
    said = []
    for f in r["unsatisfied"]:
        said.append("        UNRESOLVED-REQUIRED %s [%s] %s" % (f["key"], f["cls"], required_reason_text(f)))
    for cap in r["captures"]:
        if cap["status"] not in ("OK",) + REQUIRED_BANNER_CAUSES and cap["note"]:
            # Corpus-wide causes are printed once in the banner instead.
            said.append("%sREQUIRED-NOT-CAPTURED %s [%s] %s — the required-input question was never asked "
                        "of this workflow, so nothing about it passed or failed"
                        % (pad, cap["wf"], cap["status"], cap["note"]))
    for note in r["notes"]:
        said.append("%sREQUIRED-NOT-CAPTURED %s" % (pad, note))
    if r["classes"]["docker"]:
        said.append("%s%d required input(s) are container images: excluded by rule from anything this tool "
                    "resolves, so they arrive only under `--image KEY=REF` or a profile pin "
                    "(terra/batch_rerun_step.py::_stated()). They are counted as bound here because the "
                    "config DOES name them; a launch that never states the image still cannot run"
                    % (pad, r["classes"]["docker"]))
    if r["classes"]["needs-bundle"]:
        said.append("%s%d required input(s) carry a `{{ … }}` render: NAMED here and resolved by nobody, "
                    "because no values profile is opened anywhere in this tool. The plan's revision 1 "
                    "resolved everything by rendering upstream's inputs and came out somebody else's "
                    "sample, which is why an unstated bundle is disclosed (exit 0) and never quietly "
                    "satisfied"
                    % (pad, r["classes"]["needs-bundle"]))
    if not said:
        return []
    census = " ".join("%s=%s" % (c, r["classes"][c]) for c in REQUIRED_CLASSES if r["classes"][c])
    return ["  %s (%s): %d required input(s), %s\n%s"
            % (r["config"], r["flavor"], r["required"], census or "none",
               "\n".join(said))]


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
        # Covers both JSON errors and unfinished `${` / `{{`; `v["error"]` says which.
        return ("its bindings cannot be read at all: after the one `{{ ... }}` -> null substitution "
                "(%d substituted), %s" % (v["neutralised"], v["error"]))
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


# --------------------------------------------------------------------------- Terra rootEntityType cross-check
# Terra stores an input as a bare expression (`this.sample_set_id`); a template carries `${this.x}`. Both
# forms are read. A `this.` token inside a JSON string literal of a bare expression is literal text.
DUMP_PREFIX = "GSVTK-ENTITY-TERRA"


def expression_reads(text: str) -> list:
    """The `this.<path>` reads in one stored input value, bare-expression or `${ }` form."""
    if "${" in text:
        return reads_in(text)[0]
    spans = _json_string_spans(text)
    return [m.group(1) for m in THIS_TOKEN_RE.finditer(text)
            if not any(s <= m.start() < e for s, e in spans)
            and (m.start() == 0 or not re.match(r"[A-Za-z0-9_.]", text[m.start() - 1]))]


def derive_from_inputs(inputs) -> tuple:
    """(etype, "") from the name-key rule over one config's inputs, or ("", reason)."""
    if not isinstance(inputs, dict):
        return "", "no-inputs-object"
    if not inputs:
        return "", "no-inputs"
    key_types = []
    for _path, text, _depth in walk_strings(inputs):
        for path in expression_reads(text):
            kind, first, _tail = split_read(path)
            et = name_key_type(first) if kind == "direct" else ""
            if et and et not in key_types:
                key_types.append(et)
    if not key_types:
        return "", "no-name-key"
    if len(key_types) > 1:
        return "", "many-name-keys(%s)" % ",".join(sorted(key_types))
    return key_types[0], ""


def read_dump(path: str) -> tuple:
    """(workspace label, [config payload], error). Accepts `{"configs": [...]}` or a bare list."""
    try:
        with open(path) as fh:
            obj = json.load(fh)
    except (OSError, ValueError) as e:
        return "", [], "cannot read %s as JSON (%s)" % (path, e)
    label = "-"
    if isinstance(obj, dict):
        label = "%s/%s" % (obj.get("namespace", "?"), obj.get("workspace", "?"))
        obj = obj.get("configs")
    if not isinstance(obj, list) or any(not isinstance(c, dict) for c in obj):
        return label, [], "%s holds no list of config objects (expected {\"configs\": [...]})" % path
    return label, obj, ""


def compare_dump(configs: list) -> list:
    """[(name, verdict, detail)] with verdict AGREE | DISAGREE | UNDERIVABLE, one per config."""
    out = []
    for c in configs:
        name = "%s/%s" % (c.get("namespace", "?"), c.get("name", "?"))
        derived, why = derive_from_inputs(c.get("inputs"))
        terra = c.get("rootEntityType") or ""
        if not derived:
            out.append((name, "UNDERIVABLE", why))
        elif derived == terra:
            out.append((name, "AGREE", "derived=%s terra=%s" % (derived, terra)))
        else:
            out.append((name, "DISAGREE", "derived=%s terra=%s" % (derived, terra or "-")))
    return out


def against_dump(path: str) -> int:
    """Compare each dumped config's derived entity type with Terra's rootEntityType. Exit 0/1/2."""
    label, configs, err = read_dump(path)
    if err:
        print("refused: %s" % err)
        return 2
    print("terra rootEntityType cross-check — dump %s (workspace %s, %d config(s))"
          % (path, label, len(configs)))
    rows = compare_dump(configs)
    counts = {"AGREE": 0, "DISAGREE": 0, "UNDERIVABLE": 0}
    for name, verdict, detail in rows:
        counts[verdict] += 1
        if verdict == "AGREE":
            print("%s config=%s AGREE" % (DUMP_PREFIX, name))
        else:
            print("%s config=%s %s %s" % (DUMP_PREFIX, name, verdict, detail))
    derivable = counts["AGREE"] + counts["DISAGREE"]
    rc = 1 if counts["DISAGREE"] else (2 if not derivable else 0)
    print("%s-SUMMARY configs=%d agree=%d disagree=%d underivable=%d derivable=%d verdict=%s"
          % (DUMP_PREFIX, len(rows), counts["AGREE"], counts["DISAGREE"], counts["UNDERIVABLE"], derivable,
             {0: "AGREE", 1: "DISAGREE", 2: "NOTHING-DERIVABLE"}[rc]))
    if rc == 2:
        print("refused: no config in the dump yields a name-key entity type, so nothing was compared "
              "(an empty or underivable dump is not a pass)")
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="terra_entity_check.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default="", help="gatk-sv clone to read (default: GSVTK_GATK_SV_CHECKOUT)")
    ap.add_argument("--ref", default="", help="ref to read (default: GSVTK_GATK_SV_REF, else HEAD)")
    ap.add_argument("--tree", default="", help="already-materialized gatk-sv tree root (skips git archive)")
    ap.add_argument("--corpus", default=DEFAULT_CORPUS,
                    help="corpus subpath inside the tree (default: %s)" % DEFAULT_CORPUS)
    ap.add_argument("--against-dump", default="", metavar="FILE",
                    help="compare a terra/dump_configs.py dump's rootEntityType with the derived type")
    args = ap.parse_args(argv)

    if args.against_dump:
        if args.tree or args.repo or args.ref or args.corpus != DEFAULT_CORPUS:
            print("usage error: --against-dump reads only the dump; drop --tree/--repo/--ref/--corpus")
            return 2
        return against_dump(args.against_dump)

    jinja_re, where = _shared_expander()
    if jinja_re is None:
        print("PREREQUISITE no single `{{ ... }}` expander to reuse (%s) — this file refuses to define a "
              "second one" % where)
        return 3
    reader, required_where = _required_reader()
    if reader is None:
        # A missing sibling checker is a broken checkout (exit 3); missing miniwdl is disclosed below.
        print("PREREQUISITE no required-input predicate to reuse (%s) — the required-input half of this run "
              "would otherwise need a second, divergent definition" % required_where)
        return 3
    required_inputs, load_workflow = reader
    if args.tree and args.ref:
        print("usage error: --tree already fixes the tree; drop --ref, or use --repo with --ref")
        return 2
    if args.tree:
        if not os.path.isdir(args.tree):
            print("usage error: --tree %s is not a directory" % args.tree)
            return 2
        tree, source = args.tree, "tree %s; shipped test profiles from the directory listing" % args.tree
        profiles = test_profile_stems(tree)
        # A missing `wdl/` is reported as NO-WDL-DIR, not an error.
        wdl_dir = os.path.join(tree, WDL_SUBTREE)
        wdl_note = ("" if os.path.isdir(wdl_dir)
                    else "no %s/ directory under %s, so no workflow's required inputs can be read" %
                         (WDL_SUBTREE, tree))
    else:
        repo = args.repo or config.get("GATK_SV_CHECKOUT")
        # GSVTK_GATK_SV_REF is an env var, not a profile key (`config.get` refuses unknown keys).
        ref = args.ref or os.environ.get("GSVTK_GATK_SV_REF") or "HEAD"
        if not repo or not os.path.isdir(os.path.expanduser(repo)):
            print("PREREQUISITE no gatk-sv checkout to read (set GSVTK_GATK_SV_CHECKOUT, pass --repo, or "
                  "pass --tree for an already-materialized tree)")
            return 3
        repo = os.path.expanduser(repo)
        if not os.path.isdir(os.path.join(repo, ".git")):
            print("PREREQUISITE %s is not a git repository, so there is no ref to archive" % repo)
            return 3
        tree, why = _tree_from_ref(repo, ref, args.corpus)
        if not tree:
            return 3
        source = "git archive %s from %s; shipped test profiles from git ls-tree" % (ref, repo)
        # The archive holds only the corpus, so test profiles are listed from the ref with git ls-tree.
        profiles = test_profile_stems_at_ref(repo, ref)
        wdl_tree, wdl_why = _tree_from_ref(repo, ref, WDL_SUBTREE, quiet=True)
        wdl_dir = os.path.join(wdl_tree, WDL_SUBTREE) if wdl_tree else ""
        wdl_note = ("" if wdl_dir else wdl_why)

    corpus_dir = os.path.join(tree, args.corpus)
    if not os.path.isdir(corpus_dir):
        # Don't fall back to the whole tree; `--corpus .` says that explicitly.
        print("PREREQUISITE no %s under %s (%s) — the corpus this tool asks about is not there, and "
              "'no configs' is a stated answer, never a clean one" % (args.corpus, tree, source))
        return 3

    configs = []
    for dirpath, _dirs, files in os.walk(corpus_dir):
        configs.extend(os.path.join(dirpath, fn) for fn in files if fn.endswith(CONFIG_SUFFIX))
    configs.sort()
    tables, other_tsv, unreadable = read_tables(corpus_dir)
    miniwdl_ok, miniwdl_note = _wdl_module()
    ctx = {"wdl_dir": wdl_dir, "dir_ok": bool(wdl_dir) and os.path.isdir(wdl_dir), "dir_note": wdl_note,
           "miniwdl_ok": miniwdl_ok, "miniwdl_note": miniwdl_note,
           "reader": required_inputs, "loader": load_workflow}
    cap_cache = {}

    print("terra entity derivation — corpus %s (%s)" % (args.corpus, source))
    print("expander: %s from %s (one definition; `{{ ... }}` -> null, never rendered)"
          % (EXPANDER_NAME, where))
    # Say whether the WDL tree was actually readable, so the path is not mistaken for "opened".
    if not miniwdl_ok:
        wdl_state = miniwdl_note
    elif not ctx["dir_ok"]:
        wdl_state = "no %s/ directory here" % WDL_SUBTREE
    else:
        wdl_state = "miniwdl present"
    print("required inputs: %s; WDL tree %s (%s)" % (required_where, wdl_dir or "-", wdl_state))
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
        # Every summary line prints even at zero, so callers never special-case a missing line.
        print("GSVTK-ORACLE-SUMMARY flavors=0 ok=0 absent=0 malformed=0 dupes=0 names=0 "
              "values=not-read enforced=yes")
        print(value_summary_line([]))
        print(required_summary_line([]))
        return 3

    rows = []
    prefixes_of = {}
    for full in configs:
        rel = os.path.relpath(full, corpus_dir).replace(os.sep, "/")
        cfg = read_config(full, jinja_re)
        prefixes = sorted({k.split(".")[0] for k in cfg["keys"]}) if cfg["ok"] else []
        prefixes_of[rel] = [p for p in prefixes if p]
        v = grade(cfg, tables)
        v.update(config=rel, filestem=os.path.basename(full)[:-len(CONFIG_SUFFIX)],
                 flavor=rel.split("/")[0] if "/" in rel else ROOT_FLAVOR,
                 wf=prefixes[0] if len(prefixes) == 1 else ",".join(prefixes) or "?",
                 workspace_reads=workspace_reads(cfg) if cfg["ok"] else 0)
        rows.append(v)

    # The flavor list comes from the configs, so a flavor with no table reports ABSENT.
    oracles = read_workspace_oracle(corpus_dir, [r["flavor"] for r in rows])
    # Each config's values are classed against its own flavor's oracle.
    oracle_of = dict((o["flavor"], o) for o in oracles)
    for r in rows:
        r["values"] = classify_values(os.path.join(corpus_dir, r["config"].replace("/", os.sep)),
                                      r["config"], r["flavor"], r["wf"], oracle_of.get(r["flavor"]),
                                      jinja_re, profiles)
    # One cache per run: a WDL loads with its whole import closure, which can take seconds.
    for r in rows:
        r["required"] = grade_required(r["values"], prefixes_of.get(r["config"], []), ctx, cap_cache)

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
    vals = [r["values"] for r in rows]
    print("value provenance: %d value(s) classified, %d refused by class, %d disclosed as needing a bundle, "
          "%d config(s) refusing, %d with no value at all, %d the layer could not read"
          % (sum(v["bindings"] for v in vals), sum(v["unresolved"] for v in vals),
             sum(v["classes"]["needs-bundle"] for v in vals),
             sum(1 for v in vals if v["state"] != "OK"), sum(1 for v in vals if not v["bindings"]),
             sum(1 for v in vals if v["unclassified"])))
    reqs = [r["required"] for r in rows]
    print("required-input acceptance: %d required input(s) across %d captured workflow(s) asked of %d "
          "config(s), %d not bound by the config that names them, %d already refused by the value layer, "
          "%d config(s) whose workflow(s) this run could not read"
          % (sum(x["required"] for x in reqs),
             len({c["wf"] for x in reqs for c in x["captures"] if c["status"] == "OK"}),
             sum(1 for x in reqs if x["state"] != "NOT-CAPTURED"),
             sum(x["classes"]["unbound"] for x in reqs),
             sum(x["classes"][c] for x in reqs for c in VALUE_REFUSAL_CLASSES),
             sum(1 for x in reqs if x["capture"] != "OK")))
    print("\n== workspace oracle ==")
    for o in oracles:
        for line_text in oracle_report(o):
            print(line_text)
    print("  values=not-read: no row's second field was parsed, resolved or used in any decision — it is an "
          "upstream values-profile expression, and reading it would answer \"what did that profile bind\", not "
          "\"what does this ref declare\". A finding quotes its whole row, because the shape of the row IS the "
          "finding; those quoted bytes decide nothing here.")
    print("  enforced=yes: `${workspace.*}` names ARE enforced now, by the value layer below: a name with no "
          "row in its flavor's own workspace table, or in a flavor whose table did not read cleanly "
          "(ABSENT, MALFORMED, DUPE), is UNRESOLVED and moves the exit code to 1. The oracle reader above "
          "still grades nothing by itself — what it decides is `status`, and `status` is what the refusal "
          "keys off, so a table that was read but did not read cleanly resolves nothing either.")

    print("\n== value provenance ==")
    said_any = False
    for r in rows:
        for line_text in value_report(r["values"], oracle_of.get(r["flavor"])):
            print(line_text)
            said_any = True
    if not said_any:
        print("  (no config had anything to say: no refused value, no `{{ ... }}` bundle read to disclose, "
              "no `*_docker` key, no read below binding level, nothing dropped by a duplicate)")
    value_findings = [r for r in rows if r["values"]["state"] != "OK"]
    print("\n== required inputs ==")
    reqs = [r["required"] for r in rows]
    partial = [x for x in reqs if x["capture"] != "OK"]
    if partial:
        # One banner for the whole run rather than a line per config.
        shown, count = uncaptured_workflows(reqs)
        print("  REQUIRED-NOT-CAPTURED %d of %d config(s), cause %s (%d workflow(s) not read: %s): at least "
              "one workflow behind these could not be read, so the required-input census is incomplete for "
              "them and says NOT-CAPTURED where it could ask nothing. An exit code of 0 below means the "
              "entity and value questions were answered for every config; it does NOT mean every required "
              "input is bound."
              % (len(partial), len(rows), required_uncaptured_cause(reqs), count, shown))
    said_any = False
    for r in rows:
        for line_text in required_report(r["required"]):
            print(line_text)
            said_any = True
    if not said_any:
        print("  (nothing to say: every required input of every captured workflow is bound by the config "
              "that names it, with no unsatisfied key, no uncaptured workflow, no image and no bundle read "
              "to disclose)")
    required_findings = [r for r in rows if r["required"]["state"] == "UNRESOLVED"]
    for r in rows:
        print(machine_line(r))
        print(value_machine_line(r["values"]))
        print(required_machine_line(r["required"]))
    print("GSVTK-ENTITY-SUMMARY configs=%d ok=%d no-name-key=%d many-name-keys=%d no-shipped-table=%d "
          "columns-missing=%d cannot-parse=%d namekeys-one=%d namekeys-zero=%d namekeys-many=%d"
          % (len(rows), counts["ok"], counts["no-name-key"], counts["many-name-keys"],
             counts["no-shipped-table"], counts["columns-missing"], counts["cannot-parse"],
             one, zero, many))
    # What this tool did not evaluate, printed on every run including a clean one.
    member_cfg = sum(1 for r in rows if r["member_attrs"])
    member_reads = sum(len(r["member_attrs"]) for r in rows)
    # Workspace reads not examined: only those inside documents the value layer could not parse.
    unread = [r for r in rows if r["values"]["unclassified"]]
    ws_reads = sum(r.get("workspace_reads", 0) for r in unread)
    ws_cfg = sum(1 for r in unread if r.get("workspace_reads"))
    attr_values = sum(o["value_cells"] for o in oracles)
    reqs = [r["required"] for r in rows]
    print("GSVTK-ENTITY-UNCHECKED configs=%d member-attr-reads=%d configs-with-member-attrs=%d "
          "workspace-binding-reads=%d configs-with-workspace-reads=%d "
          "workspace-attribute-values-unread=%d configs-with-unclassified-values=%d "
          "required-configs-uncaptured=%d required-uncaptured-because=%s "
          "root-entity-type=never-read"
          % (len(rows), member_reads, member_cfg, ws_reads, ws_cfg, attr_values, len(unread),
             sum(1 for x in reqs if x["capture"] != "OK"), required_uncaptured_cause(reqs)))
    print("  (the counts above are SCOPE, not verdicts, and none of them is 0 because the tool wished it: "
          "member attributes are counted and listed but never checked against a table, because "
          "plural-to-singular is a guess; the workspace ATTRIBUTE VALUES — the second column of every "
          "flavor table — are counted and never opened, because reading one answers \"what did that values "
          "profile bind\" instead of \"what does this ref declare\"; ${workspace.*} reads are now classified "
          "and enforced, so the reads still unexamined are only the ones inside documents this layer could "
          "not parse, and a document that will not parse can only have its unreadability reported, never its "
          "reads counted — which is what configs-with-unclassified-values= says; required-configs-uncaptured "
          "counts the configs whose workflow was never opened, so the required-input census does not cover "
          "them and their machine lines say capture=<word> rather than OK — the cause is that field, broken "
          "down per word on GSVTK-REQUIRED-SUMMARY, and a run with miniwdl absent reports EVERY config here "
          "because no WDL could be read at all; and Terra's own "
          "rootEntityType is never asked, because that is a credentialed read this tool must not make.)")
    for o in oracles:
        print(oracle_machine_line(o))
    buckets = dict((s, 0) for s in ORACLE_STATUSES)
    for o in oracles:
        buckets[o["status"]] += 1
    print("GSVTK-ORACLE-SUMMARY flavors=%d ok=%d absent=%d malformed=%d dupes=%d names=%d "
          "values=not-read enforced=yes"
          % (len(oracles), buckets["OK"], buckets["ABSENT"], buckets["MALFORMED"], buckets["DUPE"],
             sum(o["attrs"] for o in oracles)))
    print(value_summary_line([r["values"] for r in rows]))
    print(required_summary_line([r["required"] for r in rows]))
    # Any layer's finding fails the run.
    return 1 if (findings or value_findings or required_findings) else 0


if __name__ == "__main__":
    sys.exit(main())
