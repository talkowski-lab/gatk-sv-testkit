#!/usr/bin/env python3
"""Derive a module profile's upstream facts from gatk-sv's own Terra templates and WDLs at one ref.

A profile (`profiles/<module>.json`, read by `kit/module_profile.py`) is hand-written. Most of what it
states is already written down upstream:

    workflow / template   the `inputs/templates/terra_workspaces/**/*.json.tmpl` whose binding keys all
                          carry the step's workflow prefix
    wdl                   the `wdl/*.wdl` file that declares `workflow <name>`
    rootEntityType        the single `${this.<etype>_id}` name key the template reads (the rule and the
                          functions of `checks/terra_entity_check.py`)
    dockstore             the `.github/.dockstore.yml` entry whose primaryDescriptorPath is that WDL
    inputs                each template binding, `${this.x}` -> `this.x`, `${workspace.x}` -> `workspace.x`
    outputs               the keys of the workflow's `output {}` section; the attribute each writes only
                          where upstream ships an `output_configurations/*.json.tmpl` for it

Everything else is NOT DERIVED FROM UPSTREAM, and is said so field by field with the reason: the
`{frz}`/`{new}` suffixes, `callers`, `entity_row`, `export`, `images`, `literals`, `_why_*`, and every
binding whose value is Jinja-rendered (`{{ ... }}`; the expression is reported, never evaluated).

    scripts/derive_profile.py show  --module genotyping [--ref main] [--repo ~/gatk-sv]
    scripts/derive_profile.py show  --workflow GenotypeBatch --ref v1.1.1      # no profile needed
    scripts/derive_profile.py show  --module genotyping --write-draft /tmp/genotyping.draft.json
    scripts/derive_profile.py check --module genotyping --ref main
    scripts/derive_profile.py check --profile some/profile.json --ref HEAD

Upstream is read with `git ls-tree`, `git grep <sha>` and `git show <sha>:<path>` only, so the
checkout's working tree, index and HEAD are never touched. The repo is `--repo`, else
GSVTK_GATK_SV_CHECKOUT. `--ref` defaults to `HEAD` of that clone.

`--write-draft PATH` writes a draft profile (derived values; hand-written-only fields carried over from
the profile, never derived) and `PATH.lock.json` recording the repo, ref, resolved sha and the sha256
of every upstream file read. Nothing else is written. It is free; nothing touches Terra or the cloud.

`check` compares every derivable field with the hand-written profile. Suffix tokens are stripped and
`@` keys expanded over the profile's `callers` before comparing. A difference the profile explains is
listed as expected, not as a disagreement: a `branch_only_inputs` or `branch_only_outputs` key absent
upstream, an `upstream_only_outputs` key absent from the profile, a `template_overrides` input that
upstream's template leaves to the WDL default, and any upstream key of a caller in `unused_callers`.

Exit codes
    0  show: something was derived.  check: profile and upstream agree on every derivable field
    1  check: at least one disagreement (each listed with both values)
    2  usage error, unresolvable ref, or nothing could be derived (refused, never read as agreement)
    3  `git` is not installed
    4  no gatk-sv checkout configured (GSVTK_GATK_SV_CHECKOUT) and no --repo, or no profile for check
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "kit"))
sys.path.insert(0, str(ROOT / "checks"))
import config                                   # noqa: E402
import module_profile as mp                     # noqa: E402
import terra_entity_check as tec                # noqa: E402

CORPUS = tec.DEFAULT_CORPUS
OUTPUT_DIR_MARK = "/output_configurations/"
DOCKSTORE_YML = ".github/.dockstore.yml"
WDL_DIR = "wdl"

DERIVED = "DERIVED"
NOT_DERIVED = "NOT DERIVED FROM UPSTREAM"

# Tokens kept literal when a profile map is expanded for the draft, and stripped for comparison.
KEEP_TOKENS = {t: "{%s}" % t for t in mp.KNOWN_TOKENS}
NO_TOKENS = {t: "" for t in mp.KNOWN_TOKENS}

# Why each hand-written-only field is not derived. Stated once, printed per step.
WHY_NOT = {
    "suffixes": "{frz}/{new} namespace the testkit's frozen-vs-new attributes; upstream binds bare "
                "attribute names, so the suffix is a testkit fact",
    "entity_row": "which config key (or --entity) names the row is a testkit choice; upstream names an "
                  "entity TYPE, never a row",
    "export": "what a downstream fetch reads after the chain is a testkit consumer's choice",
    "images": "which image a run used is stated per change (--image or a tagged pin); upstream's "
              "dockers.json is deliberately not copied",
    "literals": "a literal replaces an upstream `workspace.<attr>` read with a value the launch states "
                "itself; upstream ships the read, not the value",
    "callers": "which callers produced output is a property of the cohort, not of the templates",
}


class Refusal(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


# --------------------------------------------------------------------------- reading upstream, read-only
class Upstream:
    """One gatk-sv repo at one resolved commit. Every byte read is recorded for the lock file."""

    def __init__(self, repo: Path, ref: str):
        self.repo, self.ref = repo, ref
        out = self._git("rev-parse", "--verify", "%s^{commit}" % ref)
        if out is None:
            raise Refusal(2, "derive_profile: refusing -- %s has no ref %r (`git -C %s rev-parse --verify "
                             "'%s^{commit}'` failed). Fetch it, or name a branch/tag/SHA that exists."
                          % (repo, ref, repo, ref))
        self.sha = out.decode().strip()
        self.read: dict = {}                      # path -> {"sha256", "blob"}

    def _git(self, *args: str):
        r = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True)
        return r.stdout if r.returncode == 0 else None

    def ls(self, subdir: str) -> dict:
        """{path: blob} under `subdir` at the commit (`git ls-tree`, read-only)."""
        out = self._git("ls-tree", "-r", self.sha, "--", subdir) or b""
        files = {}
        for line in out.decode("utf-8", "replace").splitlines():
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) == 3 and parts[1] == "blob":
                files[path] = parts[2]
        return files

    def grep(self, pattern: str, subdir: str, fixed: bool = False) -> list:
        """Paths under `subdir` at the commit whose content matches (`git grep <sha>`, read-only)."""
        flag = "-F" if fixed else "-E"
        out = self._git("grep", "-l", flag, "-e", pattern, self.sha, "--", subdir)
        if out is None:
            return []
        prefix = self.sha + ":"
        return sorted(p[len(prefix):] if p.startswith(prefix) else p
                      for p in out.decode("utf-8", "replace").splitlines() if p)

    def show(self, path: str):
        """Bytes of one file at the commit, or None. Recorded in `read` with its sha256 and blob id."""
        data = self._git("show", "%s:%s" % (self.sha, path))
        if data is None:
            return None
        blob = (self._git("rev-parse", "%s:%s" % (self.sha, path)) or b"").decode().strip()
        self.read[path] = {"sha256": hashlib.sha256(data).hexdigest(), "blob": blob}
        return data


# --------------------------------------------------------------------------- WDL declaration scan
def _strip_comments(text: str) -> str:
    """WDL source with `#` comments removed, strings left intact."""
    out, i, n, quote = [], 0, len(text), ""
    while i < n:
        c = text[i]
        if quote:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
            out.append(c)
        elif c == "#":
            while i < n and text[i] != "\n":
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _block(text: str, open_at: int) -> tuple:
    """(body, end) of the `{ ... }` opening at `open_at`, strings skipped. ("", -1) if unbalanced."""
    depth, i, n, quote = 0, open_at, len(text), ""
    while i < n:
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i], i
        i += 1
    return "", -1


def _statements(body: str) -> list:
    """Top-level statements of a block: newline-separated at bracket depth 0."""
    stmts, cur, depth, quote = [], [], 0, ""
    for c in body:
        if quote:
            if c == quote:
                quote = ""
        elif c in "\"'":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "\n" and depth <= 0 and not quote:
            stmts.append("".join(cur))
            cur = []
            continue
        cur.append(c)
    stmts.append("".join(cur))
    return [s.strip() for s in stmts if s.strip()]


DECL_RE = re.compile(r"\A([A-Z][A-Za-z0-9_\[\], ?+]*?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*(=[\s\S]*)?\Z")


def _decls(block: str) -> list:
    out = []
    for s in _statements(block):
        m = DECL_RE.match(s)
        if m:
            out.append(m.group(2))
    return out


def wdl_workflow_io(text: str, name: str) -> tuple:
    """(inputs, outputs, error) of `workflow <name>` in one WDL source. A declaration scan, not a parser:
    names of top-level declarations in its `input {}` and `output {}` blocks."""
    src = _strip_comments(text)
    m = re.search(r"(?m)^\s*workflow\s+%s\s*\{" % re.escape(name), src)
    if not m:
        return [], [], "declares no `workflow %s`" % name
    body, end = _block(src, m.end() - 1)
    if end < 0:
        return [], [], "`workflow %s` has unbalanced braces" % name
    found = {}
    for section in ("input", "output"):
        sm = None
        # Only a section at the workflow's own top level counts (calls have `input:` without braces).
        depth = 0
        for tok in re.finditer(r"[{}]|\b%s\s*\{" % section, body):
            t = tok.group(0)
            if t == "{":
                depth += 1
            elif t == "}":
                depth -= 1
            elif depth == 0:
                sm = tok
                break
            else:
                depth += 1
        if sm is None:
            found[section] = None
            continue
        blk, bend = _block(body, sm.end() - 1)
        found[section] = _decls(blk) if bend >= 0 else None
    return found["input"] or [], found["output"], ""


# --------------------------------------------------------------------------- template reading
def _norm(value):
    """A parsed template value as a profile would write it: `${this.x}` -> `this.x`; else unchanged."""
    if isinstance(value, str):
        m = tec.THIS_WHOLE_RE.match(value)
        if m:
            return "this." + m.group(1)
        m = tec.WORKSPACE_WHOLE_RE.match(value)
        if m:
            return "workspace." + m.group(1)
    return value


def read_template(up: Upstream, path: str, scratch: str, jinja_re) -> dict:
    """{ok, error, bindings: {key: value}, jinja: {key: [expr]}, etype, namekeys, prefixes}."""
    data = up.show(path)
    out = {"path": path, "ok": False, "error": "", "bindings": {}, "jinja": {}, "etype": "",
           "namekeys": [], "prefixes": []}
    if data is None:
        out["error"] = "git show failed"
        return out
    local = os.path.join(scratch, path.replace("/", "__"))
    with open(local, "wb") as fh:
        fh.write(data)
    cfg = tec.read_config(local, jinja_re)
    if not cfg["ok"]:
        out["error"] = cfg["error"]
        return out
    marked = tec.parse_with_bundle_marks(local, jinja_re)
    if not marked["ok"]:
        out["error"] = marked["error"]
        return out
    for key, value in marked["obj"].items():
        marks = tec._reads_of(value)[0]
        if marks:
            out["jinja"][key] = [marked["exprs"][i] for i in marks]
        else:
            out["bindings"][key] = _norm(value)
    v = tec.grade(cfg, [])                         # tables are not needed for the name key
    out.update(ok=True, etype=v["etype"], namekeys=v["namekey_types"],
               prefixes=sorted({k.split(".")[0] for k in cfg["keys"]}))
    return out


def find_templates(up: Upstream, workflow: str, scratch: str, jinja_re) -> tuple:
    """([input templates], [output templates]) whose keys all carry `workflow.` as prefix."""
    ins, outs = [], []
    for path in up.grep('"%s.' % workflow, CORPUS, fixed=True):
        if not path.endswith(tec.CONFIG_SUFFIX):
            continue
        t = read_template(up, path, scratch, jinja_re)
        if not t["ok"] or t["prefixes"] != [workflow]:
            continue
        (outs if OUTPUT_DIR_MARK in "/" + path else ins).append(t)
    return ins, outs


def _same(templates: list) -> bool:
    first = templates[0]
    return all(t["bindings"] == first["bindings"] and t["jinja"] == first["jinja"] for t in templates)


def dockstore_entries(text: str) -> list:
    """[(name, primaryDescriptorPath)] from a .dockstore.yml, by line scan."""
    entries, cur = [], {}
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("- "):
            if cur:
                entries.append(cur)
            cur = {}
            s = s[2:].strip()
        k, sep, v = s.partition(":")
        if sep and k in ("name", "primaryDescriptorPath"):
            cur[k] = v.strip().strip("'\"")
    if cur:
        entries.append(cur)
    return [(e["name"], e["primaryDescriptorPath"]) for e in entries
            if "name" in e and "primaryDescriptorPath" in e]


# --------------------------------------------------------------------------- derivation
def F(status: str, value=None, why: str = "") -> dict:
    return {"status": status, "value": value, "why": why}


def derive_step(up: Upstream, workflow: str, scratch: str, jinja_re, dockstore_cache: dict) -> dict:
    """Every field of one step, each DERIVED (with its source) or NOT DERIVED (with the reason)."""
    d = {"workflow": workflow, "fields": {}, "inputs": {}, "outputs": {}, "wdl_inputs": None,
         "wdl_outputs": None, "output_keys_from": ""}
    f = d["fields"]
    ins, outs = find_templates(up, workflow, scratch, jinja_re)
    tmpl = None
    if not ins:
        why = "no %s under %s at %s binds only `%s.*` keys" % (tec.CONFIG_SUFFIX, CORPUS, up.sha[:12], workflow)
        f["workflow"] = F(NOT_DERIVED, None, why)
        f["template"] = F(NOT_DERIVED, None, why)
        f["rootEntityType"] = F(NOT_DERIVED, None, "no template to read a name key from")
    elif len(ins) > 1 and not _same(ins):
        paths = ", ".join(t["path"] for t in ins)
        f["workflow"] = F(DERIVED, workflow, "key prefix of %s" % paths)
        f["template"] = F(NOT_DERIVED, None, "%d templates bind `%s.*` and differ: %s" % (len(ins), workflow, paths))
        f["rootEntityType"] = F(NOT_DERIVED, None, "ambiguous template")
    else:
        tmpl = ins[0]
        others = "" if len(ins) == 1 else " (and %d identical copies)" % (len(ins) - 1)
        f["workflow"] = F(DERIVED, workflow, "key prefix of every binding in %s" % tmpl["path"])
        f["template"] = F(DERIVED, tmpl["path"] + others, "")
        if tmpl["etype"]:
            f["rootEntityType"] = F(DERIVED, tmpl["etype"], "name key this.%s_id (terra_entity_check rule)"
                                    % tmpl["etype"])
        elif len(tmpl["namekeys"]) > 1:
            f["rootEntityType"] = F(NOT_DERIVED, None, "template reads several name keys: %s"
                                    % ", ".join(tmpl["namekeys"]))
        else:
            f["rootEntityType"] = F(NOT_DERIVED, None, "template reads no `this.<etype>_id` name key")
        for key, value in tmpl["bindings"].items():
            d["inputs"][key] = F(DERIVED, value, tmpl["path"])
        for key, exprs in tmpl["jinja"].items():
            d["inputs"][key] = F(NOT_DERIVED, None, "Jinja-rendered at workspace build: %s" % " ".join(exprs))

    # The WDL file that declares this workflow.
    pattern = r"^[[:space:]]*workflow[[:space:]]+%s[[:space:]]*\{" % re.escape(workflow)
    wdls = [p for p in up.grep(pattern, WDL_DIR) if p.endswith(".wdl")]
    if len(wdls) == 1:
        wpath = wdls[0]
        text = (up.show(wpath) or b"").decode("utf-8", "replace")
        wins, wouts, err = wdl_workflow_io(text, workflow)
        stem = os.path.basename(wpath)[:-len(".wdl")]
        f["wdl"] = F(DERIVED, stem, "%s declares `workflow %s`" % (wpath, workflow))
        if err:
            f["wdl_io"] = F(NOT_DERIVED, None, "%s %s" % (wpath, err))
        else:
            d["wdl_inputs"] = ["%s.%s" % (workflow, n) for n in wins]
            if wouts is not None:
                d["wdl_outputs"] = ["%s.%s" % (workflow, n) for n in wouts]
                d["output_keys_from"] = wpath
        d["wdl_path"] = wpath
    else:
        why = ("no file under %s/ declares `workflow %s`" % (WDL_DIR, workflow) if not wdls else
               "%d files declare `workflow %s`: %s" % (len(wdls), workflow, ", ".join(wdls)))
        f["wdl"] = F(NOT_DERIVED, None, why)

    # Dockstore entry for that WDL.
    if "wdl_path" in d:
        if "entries" not in dockstore_cache:
            raw = up.show(DOCKSTORE_YML)
            dockstore_cache["entries"] = None if raw is None else dockstore_entries(raw.decode("utf-8", "replace"))
        entries = dockstore_cache["entries"]
        if entries is None:
            f["dockstore"] = F(NOT_DERIVED, None, "%s is absent at %s" % (DOCKSTORE_YML, up.sha[:12]))
        else:
            names = [n for n, p in entries if p.lstrip("/") == d["wdl_path"]]
            if len(names) == 1:
                f["dockstore"] = F(DERIVED, names[0], "%s entry for /%s" % (DOCKSTORE_YML, d["wdl_path"]))
            else:
                f["dockstore"] = F(NOT_DERIVED, None, "%s has %d entries for /%s%s" % (
                    DOCKSTORE_YML, len(names), d["wdl_path"], (": " + ", ".join(names)) if names else ""))
    else:
        f["dockstore"] = F(NOT_DERIVED, None, "no WDL file derived")

    # Outputs: keys from the WDL, attribute values only from an upstream output template.
    out_tmpl = None
    if len(outs) == 1 or (outs and _same(outs)):
        out_tmpl = outs[0]
    elif outs:
        f["output_template"] = F(NOT_DERIVED, None, "%d output templates differ: %s"
                                 % (len(outs), ", ".join(t["path"] for t in outs)))
    if out_tmpl:
        f["output_template"] = F(DERIVED, out_tmpl["path"], "")
    keys = list(d["wdl_outputs"] or [])
    if out_tmpl:
        keys += [k for k in list(out_tmpl["bindings"]) + list(out_tmpl["jinja"]) if k not in keys]
    for key in keys:
        if out_tmpl and key in out_tmpl["bindings"]:
            d["outputs"][key] = F(DERIVED, out_tmpl["bindings"][key], out_tmpl["path"])
        elif out_tmpl and key in out_tmpl["jinja"]:
            d["outputs"][key] = F(NOT_DERIVED, None, "Jinja-rendered: %s" % " ".join(out_tmpl["jinja"][key]))
        else:
            d["outputs"][key] = F(NOT_DERIVED, None, "key declared by %s; the attribute it writes is set in "
                                  "Terra, and upstream ships no output template binding it"
                                  % (d["output_keys_from"] or "?"))
    if d["wdl_outputs"] is None and "wdl_path" in d and not out_tmpl:
        f["outputs"] = F(NOT_DERIVED, None, "%s has no top-level output section this scan could read"
                         % d["wdl_path"])
    for name in ("suffixes", "entity_row", "export", "images", "literals"):
        f[name] = F(NOT_DERIVED, None, WHY_NOT[name])
    return d


def derived_count(d: dict) -> int:
    return (sum(1 for k, v in d["fields"].items() if v["status"] == DERIVED and k != "workflow")
            + sum(1 for v in d["inputs"].values() if v["status"] == DERIVED)
            + (1 if d["wdl_outputs"] else 0)
            + sum(1 for v in d["outputs"].values() if v["status"] == DERIVED))


# --------------------------------------------------------------------------- printing
def _show_value(v) -> str:
    return v if isinstance(v, str) else json.dumps(v)


def print_step(step: str, d: dict) -> None:
    print("\nstep %s  (workflow %s)" % (step, d["workflow"]))
    rows = [(k, v) for k, v in d["fields"].items()]
    for k, v in d["inputs"].items():
        rows.append(("inputs[%s]" % k, v))
    if d["wdl_inputs"] is not None:
        rows.append(("wdl inputs", F(DERIVED, "%d declared" % len(d["wdl_inputs"]), d.get("wdl_path", ""))))
    if d["wdl_outputs"] is not None:
        rows.append(("outputs (keys)", F(DERIVED, "%d declared" % len(d["wdl_outputs"]), d["output_keys_from"])))
    for k, v in d["outputs"].items():
        rows.append(("outputs[%s]" % k, v))
    width = max(len(r[0]) for r in rows)
    for name, v in rows:
        if v["status"] == DERIVED:
            src = ("  [%s]" % v["why"]) if v["why"] else ""
            print("  %-*s  %s  %s%s" % (width, name, DERIVED, _show_value(v["value"]), src))
        else:
            print("  %-*s  %s  (%s)" % (width, name, NOT_DERIVED, v["why"]))


# --------------------------------------------------------------------------- profile + comparison
def load_profile(path: str) -> tuple:
    """(doc, problems). Validated with module_profile.validate, never loaded through config."""
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except OSError as e:
        return None, ["%s could not be read (%s)" % (path, e.strerror)]
    except ValueError as e:
        return None, ["%s is not readable JSON (%s)" % (path, e)]
    return doc, mp.validate(doc)


def expand(step: dict, which: str, callers: list, sfx: dict) -> dict:
    problems: list = []
    return mp.expand_map(step.get(which) or {}, callers, sfx, "%s.%s" % (step.get("step"), which), problems)


def strip_tokens(v):
    return mp.TOKEN_RE.sub("", v) if isinstance(v, str) else v


def _caller_of(key: str, unused: list) -> str:
    """The unused caller a key belongs to (`W.melt_vcf`, `W.outlier_filtered_melt_vcf_index`), or ""."""
    leaf = key.split(".", 1)[-1]
    for c in unused:
        if re.search(r"(^|_)%s(_|$)" % re.escape(c), leaf):
            return c
    return ""


def compare_step(pstep: dict, callers: list, d: dict, unused: list = ()) -> tuple:
    """([disagreement], [noted], compared, not_compared) for one profile step."""
    bad, noted, compared, skipped = [], [], 0, []
    out_branch = set(pstep.get("branch_only_outputs") or [])
    out_upstream = set(pstep.get("upstream_only_outputs") or [])
    overrides = set(pstep.get("template_overrides") or [])
    f = d["fields"]
    name = pstep["step"]

    def cmp(field, ours, theirs):
        nonlocal compared
        compared += 1
        if ours != theirs:
            bad.append((name, field, ours, theirs, "differs"))

    if f["wdl"]["status"] == DERIVED:
        cmp("wdl", pstep.get("wdl"), f["wdl"]["value"])
    if f["rootEntityType"]["status"] == DERIVED:
        cmp("rootEntityType", pstep.get("rootEntityType"), f["rootEntityType"]["value"])
    if f["dockstore"]["status"] == DERIVED:
        cmp("dockstore", pstep.get("dockstore") or pstep.get("workflow"), f["dockstore"]["value"])

    branch_only = set(pstep.get("branch_only_inputs") or [])
    prof_in = {k: strip_tokens(v) for k, v in expand(pstep, "inputs", callers, NO_TOKENS).items()}
    if f["template"]["status"] == DERIVED:
        for key in list(d["inputs"]) + [k for k in prof_in if k not in d["inputs"]]:
            up = d["inputs"].get(key)
            if up is None:
                if key in branch_only:
                    noted.append((name, "inputs[%s]" % key, "branch_only_inputs: absent upstream, as the "
                                  "profile states"))
                    continue
                wins = d["wdl_inputs"]
                undeclared = wins is not None and key not in wins and key.count(".") == 1
                if key in overrides and not undeclared:
                    noted.append((name, "inputs[%s]" % key, "template_overrides: bound here, left to the "
                                  "WDL default upstream"))
                    continue
                where = ("the WDL at this ref does not declare it" if undeclared
                         else "upstream's template leaves it to the WDL default")
                compared += 1
                bad.append((name, "inputs[%s]" % key, prof_in[key], None, "profile-only: " + where))
            elif up["status"] != DERIVED:
                skipped.append((name, "inputs[%s]" % key, up["why"]))
            elif key not in prof_in:
                if _caller_of(key, unused):
                    noted.append((name, "inputs[%s]" % key, "unused_callers: %s" % _caller_of(key, unused)))
                    continue
                compared += 1
                bad.append((name, "inputs[%s]" % key, None, up["value"], "upstream-only"))
            else:
                compared += 1
                if mp.terra_literal(prof_in[key]) != mp.terra_literal(up["value"]):
                    bad.append((name, "inputs[%s]" % key, prof_in[key], up["value"], "differs"))
    else:
        skipped.append((name, "inputs", f["template"]["why"]))

    prof_out = {k: strip_tokens(v) for k, v in expand(pstep, "outputs", callers, NO_TOKENS).items()}
    if d["wdl_outputs"] is not None or "output_template" in f:
        for key in list(d["outputs"]) + [k for k in prof_out if k not in d["outputs"]]:
            up = d["outputs"].get(key)
            if up is None and key in out_branch:
                noted.append((name, "outputs[%s]" % key, "branch_only_outputs: absent upstream, as the "
                              "profile states"))
                continue
            if up is not None and key not in prof_out and (key in out_upstream or _caller_of(key, unused)):
                why = ("upstream_only_outputs: the branch removes it" if key in out_upstream
                       else "unused_callers: %s" % _caller_of(key, unused))
                noted.append((name, "outputs[%s]" % key, why))
                continue
            if up is None:
                compared += 1
                bad.append((name, "outputs[%s]" % key, prof_out[key], None,
                            "profile-only: not a declared output at this ref"))
            elif key not in prof_out:
                compared += 1
                bad.append((name, "outputs[%s]" % key, None, up["value"] if up["status"] == DERIVED
                            else "(declared output)", "upstream-only"))
            elif up["status"] == DERIVED:
                compared += 1
                if mp.terra_literal(prof_out[key]) != mp.terra_literal(up["value"]):
                    bad.append((name, "outputs[%s]" % key, prof_out[key], up["value"], "differs"))
            else:
                compared += 1                      # the key agrees; its attribute value is not derivable
    else:
        skipped.append((name, "outputs", (f.get("outputs") or f["wdl"])["why"]))
    return bad, noted, compared, skipped


# --------------------------------------------------------------------------- draft
def draft_step(pstep, callers: list, d: dict, step_name: str, unused: list = ()) -> tuple:
    """(draft step, [not carried]) from derived values plus the profile's hand-written-only fields."""
    f = d["fields"]
    pstep = pstep or {}
    dropped = []
    if f["template"]["status"] != DERIVED and not pstep:
        return None, ["%s: nothing derived and no hand-written step to carry" % step_name]
    if f["template"]["status"] != DERIVED:
        carried = dict(pstep)
        return carried, ["%s: carried verbatim from the profile (%s)" % (step_name, f["template"]["why"])]
    prof_in = expand(pstep, "inputs", callers, KEEP_TOKENS)
    prof_out = expand(pstep, "outputs", callers, KEEP_TOKENS)
    s = {"step": step_name,
         "wdl": f["wdl"]["value"] if f["wdl"]["status"] == DERIVED else pstep.get("wdl"),
         "workflow": d["workflow"]}
    if f["wdl"]["status"] != DERIVED:
        dropped.append("%s.wdl: %s" % (step_name, "carried from profile" if pstep.get("wdl") else "unknown"))
    ds = f["dockstore"]["value"] if f["dockstore"]["status"] == DERIVED else pstep.get("dockstore")
    if ds and ds != d["workflow"]:
        s["dockstore"] = ds
    rt = f["rootEntityType"]["value"] if f["rootEntityType"]["status"] == DERIVED else pstep.get("rootEntityType")
    if rt:
        s["rootEntityType"] = rt
    else:
        dropped.append("%s.rootEntityType: not derived and not in the profile" % step_name)
    if "entity_row" in pstep:
        s["entity_row"] = pstep["entity_row"]

    inputs = {}
    for key, v in d["inputs"].items():
        if v["status"] == DERIVED:
            pv = prof_in.get(key)
            # Carry the hand-written suffix only when the bare attribute agrees with upstream.
            inputs[key] = pv if (pv is not None and strip_tokens(pv) == v["value"] and pv != v["value"]) \
                else v["value"]
        elif key in prof_in:
            inputs[key] = prof_in[key]
            dropped.append("%s.inputs[%s]: value carried from the profile (%s)" % (step_name, key, v["why"]))
        else:
            dropped.append("%s.inputs[%s]: omitted (%s)" % (step_name, key, v["why"]))
    branch_only = [k for k in (pstep.get("branch_only_inputs") or []) if k in prof_in]
    overrides = [k for k in (pstep.get("template_overrides") or []) if k in prof_in]
    for key in branch_only + overrides:
        inputs.setdefault(key, prof_in[key])
    for key in [k for k in inputs if _caller_of(k, unused)]:
        del inputs[key]
    for key in prof_in:
        if key not in inputs:
            dropped.append("%s.inputs[%s]: hand-written binding upstream does not carry; not in the draft"
                           % (step_name, key))
    s["inputs"] = inputs
    if branch_only:
        s["branch_only_inputs"] = branch_only
    if overrides:
        s["template_overrides"] = overrides

    outputs = {}
    for key, v in d["outputs"].items():
        if v["status"] == DERIVED:
            pv = prof_out.get(key)
            outputs[key] = pv if (pv is not None and strip_tokens(pv) == v["value"]) else v["value"]
        elif key in prof_out:
            outputs[key] = prof_out[key]
        else:
            dropped.append("%s.outputs[%s]: omitted (%s)" % (step_name, key, v["why"]))
    out_branch = [k for k in (pstep.get("branch_only_outputs") or []) if k in prof_out]
    for key in out_branch:
        outputs.setdefault(key, prof_out[key])
    out_upstream = [k for k in (pstep.get("upstream_only_outputs") or []) if k in d["outputs"]]
    for key in out_upstream + [k for k in outputs if _caller_of(k, unused)]:
        outputs.pop(key, None)
    for key in prof_out:
        if key not in outputs:
            dropped.append("%s.outputs[%s]: not a declared output at this ref; not in the draft" % (step_name, key))
    s["outputs"] = outputs
    if out_branch:
        s["branch_only_outputs"] = out_branch
    if out_upstream:
        s["upstream_only_outputs"] = out_upstream

    if pstep.get("export"):
        s["export"] = list(pstep["export"])
    if pstep.get("images"):
        imgs = {k: v for k, v in pstep["images"].items() if k in inputs}
        if imgs:
            s["images"] = imgs
    if pstep.get("literals"):
        leaves = {k.rsplit(".", 1)[-1] for k in inputs}
        lits = {k: v for k, v in pstep["literals"].items() if k in leaves}
        if lits:
            s["literals"] = lits
        for k in pstep["literals"]:
            if k not in leaves:
                dropped.append("%s.literals[%s]: no draft input carries that leaf" % (step_name, k))
    for key, v in pstep.items():
        if not key.startswith("_why"):
            continue
        ref = key[len("_why_"):]
        if key in mp.RESERVED_WHY or ref in inputs or ref in outputs or ref in s:
            s[key] = v
        else:
            dropped.append("%s.%s: its key is not in the draft" % (step_name, key))
    return s, dropped


# --------------------------------------------------------------------------- main
def _resolve_repo(arg: str) -> Path:
    raw = arg or config.get("GATK_SV_CHECKOUT")
    if not raw:
        raise Refusal(4, "derive_profile: GSVTK_GATK_SV_CHECKOUT is not set and --repo was not given "
                         "(docs/config.md) -- a ref is meaningless without a repository.")
    repo = Path(raw).expanduser()
    if not (repo / ".git").exists():
        raise Refusal(4, "derive_profile: %s is not a git repository" % repo)
    return repo


def run(a) -> int:
    if shutil.which("git") is None:
        raise Refusal(3, "derive_profile: `git` is not on PATH")
    if a.mode == "check" and a.write_draft:
        raise Refusal(2, "derive_profile: --write-draft belongs to `show`; `check` writes nothing")
    if a.mode == "check" and a.workflow:
        raise Refusal(2, "derive_profile: `check` compares a profile's own steps; --workflow is for `show`")

    prof_path = a.profile or mp.path(a.module)
    doc, problems = (None, []) if (a.workflow and not a.profile and not os.path.isfile(prof_path)) \
        else load_profile(prof_path)
    if doc is None and not a.workflow:
        raise Refusal(4, "derive_profile: no usable profile: %s. Name one with --module/--profile, or derive "
                         "ad hoc with `show --workflow NAME`." % "; ".join(problems))
    if problems:
        print("derive_profile: note -- %s does not validate:" % prof_path, file=sys.stderr)
        for p in problems:
            print("  - %s" % p, file=sys.stderr)
        if a.mode == "check":
            raise Refusal(2, "derive_profile: refusing to compare against a profile that does not validate")

    repo = _resolve_repo(a.repo)
    ref = a.ref or "HEAD"
    up = Upstream(repo, ref)
    jinja_re, where = tec._shared_expander()
    if jinja_re is None:
        raise Refusal(3, "derive_profile: cannot read the shared Jinja pattern (%s)" % where)

    callers = [str(c) for c in (doc or {}).get("callers") or []]
    unused = [str(c) for c in (doc or {}).get("unused_callers") or []]
    if a.workflow:
        by_wf = {s.get("workflow"): s for s in (doc or {}).get("steps") or []}
        steps = [(by_wf.get(w, {}).get("step") or w, w, by_wf.get(w)) for w in a.workflow]
    else:
        steps = [(s["step"], s["workflow"], s) for s in doc["steps"]]

    print("# %s @ %s = %s  (read with git ls-tree/grep/show; checkout untouched)" % (repo, ref, up.sha))
    print("# profile: %s" % (prof_path if doc is not None else "(none)"))
    print("# top level: callers  %s  (%s)" % (NOT_DERIVED, WHY_NOT["callers"]))
    scratch = tempfile.mkdtemp(prefix="gsvtk-derive-")
    try:
        derived, cache = [], {}
        for name, wf, pstep in steps:
            d = derive_step(up, wf, scratch, jinja_re, cache)
            derived.append((name, d, pstep))
            print_step(name, d)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    total = sum(derived_count(d) for _n, d, _p in derived)
    print("\nGSVTK-DERIVE-SUMMARY ref=%s sha=%s steps=%d derived-fields=%d files-read=%d"
          % (ref, up.sha[:12], len(derived), total, len(up.read)))
    if total == 0:
        print("derive_profile: refusing -- nothing could be derived from %s @ %s for %s. That is not "
              "agreement: no template binds these workflows and no WDL declares them at this ref."
              % (repo, up.sha[:12], ", ".join(wf for _n, wf, _p in steps)), file=sys.stderr)
        return 2

    if a.mode == "check":
        bad, noted, compared, skipped = [], [], 0, []
        for name, d, pstep in derived:
            b, n, c, s = compare_step(pstep, callers, d, unused)
            bad += b
            noted += n
            compared += c
            skipped += s
        print("\n## check: %s against %s @ %s" % (prof_path, repo, up.sha[:12]))
        for step, field, why in skipped:
            print("  not compared  %s %s  (%s)" % (step, field, why))
        for step, field, why in noted:
            print("  expected      %s %s  (%s)" % (step, field, why))
        for step, field, ours, theirs, kind in bad:
            print("  DISAGREE      %s %s  [%s]\n                profile:  %s\n                upstream: %s"
                  % (step, field, kind, "(absent)" if ours is None else _show_value(ours),
                     "(absent)" if theirs is None else _show_value(theirs)))
        print("GSVTK-DERIVE-CHECK compared=%d disagree=%d expected=%d not-compared=%d"
              % (compared, len(bad), len(noted), len(skipped)))
        if compared == 0:
            print("derive_profile: refusing -- no field could be compared", file=sys.stderr)
            return 2
        return 1 if bad else 0

    if a.write_draft:
        draft_steps, dropped = [], []
        for name, d, pstep in derived:
            s, why = draft_step(pstep, callers, d, name, unused)
            dropped += why
            if s is not None:
                draft_steps.append(s)
        out = {"schema_version": (doc or {}).get("schema_version", mp.SCHEMA_VERSIONS[-1]),
               "name": (doc or {}).get("name") or a.module or "draft",
               "callers": callers, "steps": draft_steps}
        if unused:
            out["unused_callers"] = unused
        for k, v in (doc or {}).items():
            if k.startswith("_why"):
                out[k] = v
        lock = {"repo": str(repo), "ref": ref, "sha": up.sha, "corpus": CORPUS,
                "profile": prof_path if doc is not None else None,
                "files": [{"path": p, "sha256": m["sha256"], "blob": m["blob"]}
                          for p, m in sorted(up.read.items())],
                "not_derived_or_dropped": dropped}
        dest = Path(a.write_draft).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(out, indent=2) + "\n")
        lock_path = Path(str(dest) + ".lock.json")
        lock_path.write_text(json.dumps(lock, indent=2) + "\n")
        issues = mp.validate(out)
        print("\ndraft  -> %s  (%d step(s); %d field(s) not derived or dropped, listed in the lock)"
              % (dest, len(draft_steps), len(dropped)))
        print("lock   -> %s  (%d upstream file(s), sha256 each)" % (lock_path, len(up.read)))
        if issues:
            print("draft does not validate as a profile yet (review before use):")
            for p in issues:
                print("  - %s" % p)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Exit: 0 ok/agree, 1 disagree, 2 refused/nothing derived, "
                                        "3 no git, 4 no checkout/profile.")
    ap.add_argument("mode", choices=("show", "check"))
    ap.add_argument("--module", default="", help="profile name under GSVTK_MODULE_DIR (default GSVTK_MODULE)")
    ap.add_argument("--profile", default="", help="profile JSON path (overrides --module)")
    ap.add_argument("--workflow", action="append", default=[],
                    help="show: derive this workflow (repeatable) instead of the profile's steps")
    ap.add_argument("--repo", default="", help="gatk-sv clone (default GSVTK_GATK_SV_CHECKOUT)")
    ap.add_argument("--ref", default="", help="branch, tag or SHA (default HEAD)")
    ap.add_argument("--write-draft", default="", metavar="PATH",
                    help="show: write a draft profile to PATH and PATH.lock.json")
    a = ap.parse_args(argv)
    try:
        return run(a)
    except Refusal as r:
        print(str(r), file=sys.stderr)
        return r.code


if __name__ == "__main__":
    sys.exit(main())
