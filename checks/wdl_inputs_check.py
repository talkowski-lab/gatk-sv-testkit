#!/usr/bin/env python3
"""wdl_inputs_check.py — does the input JSON that CI renders actually bind what the WDL requires?

Mirrors the input-JSON half of gatk-sv CI (`.github/workflows/testwdls.yaml`), which `miniwdl check`
never looks at. Example: a workflow gains a required `String sample_id` that is bound at every call
site but absent from the test input template; miniwdl passes, CI fails with
`Required workflow input 'IntegrateGDVcf.sample_id' not specified`.

CI is two commands:

    scripts/inputs/build_default_inputs.sh -d .       # render inputs/build/**.json from the templates
    scripts/test/validate.sh -t -d . -j womtool.jar   # womtool-validate the JSONs whose names match

Rendering runs the ref's own `build_default_inputs.sh` (which JSONs exist depends on upstream logic
that should not be reimplemented) inside a temp tree made by `git archive` of `inputs/` and
`scripts/inputs/`, so the checkout is never written to. Pairing workflows with JSONs and the verdict
are reimplemented, because `validate.sh` cannot be asked about one workflow; the pairing rules copy
upstream's (`TEST_DIRS`, `TERRA_DIRS`), including its omissions.

Two directions are checked, on key sets only (womtool checks key presence, not values):
  * missing: a required workflow input that a paired JSON does not name.
  * extras:  a key a JSON names that the WDL cannot accept. The expected set is miniwdl's
    `available_inputs` (workflow inputs plus unbound call inputs), a superset of `womtool inputs`.
    `extra_keys` is deliberately looser than womtool in a few struct/namespace spellings.

Render destination: `--render-only` deletes its destination before rendering, so it writes a
`.gsvtk-render-stamp` file (tool + ref) first and only replaces a tree it stamped for the same ref:

    destination absent or empty          render
    exists and is not a directory        refuse: --dest names a file, not a tree
    stamped by this tool, same ref       replace it and re-stamp
    stamped by this tool, another ref    refuse: use a distinct --dest per ref
    non-empty, not stamped by this tool  refuse: never delete a directory this tool did not create

Refusals print `GSVTK-RENDER status=REFUSED jsons=0 reason=<un-stamped|other-ref|not-a-directory>` and
exit 1.

With `WOMTOOL_JAR` set (and `java` on PATH) each pair also runs CI's exact command,
`java -jar $WOMTOOL_JAR validate <wdl> -i <json>`, and disagreements are reported. Without a jar that
layer prints a counted `SKIPPED` line, which `checks/wdl_gate.sh --strict` fails on.

Usage
-----
    checks/wdl_inputs_check.py --wdl-dir DIR --wf IntegrateGDVcf          # one workflow, one verdict
    checks/wdl_inputs_check.py --wdl-dir DIR --wf SVShell --wf GenotypeBatch
    checks/wdl_inputs_check.py --wdl-dir DIR --inputs-root TREE --wf X    # already-rendered JSONs
    checks/wdl_inputs_check.py --render-only --repo <gatk-sv> --ref HEAD --dest DIR
    checks/wdl_inputs_check.py --wdl-dir DIR --wf X --womtool-jar /path/womtool.jar

`--inputs-root` is a rendered gatk-sv tree root: the directory HOLDING `inputs/build/...`, which is
what `build_default_inputs.sh -d .` writes. Without it, pass `--repo`/`--ref` and this renders first.
`--no-terra` drops the `-t` half (Terra cohort and single-sample configs).

Machine-readable lines are prefixed `GSVTK-` so `checks/wdl_gate.sh` can read a verdict while the
human lines beside them stay readable. Three per workflow: `GSVTK-INPUTS` (a required key the JSON
does not name), `GSVTK-EXTRAS` (a key the JSON names that no declaration of the WDL can name) and
`GSVTK-WOMTOOL` (CI's own command, or the named reason it could not run). Exit: 0 clean, 1 findings, 2
nothing could be checked (a tree that will not load, or no input JSON for that workflow) — a different
answer from "clean", and printed as one.
"""
from __future__ import annotations

import argparse
import difflib
import fnmatch
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
import config  # noqa: E402  # GATK_SV_CHECKOUT and the work dir

# miniwdl (`WDL`) is imported lazily; see `check_workflow`.

# scripts/test/validate.sh:
#   JSONS=(`find inputs/build/ref_panel_1kg/test -name "${name}.*json``
#          `find inputs/build/hgdp/test  -name "${name}.*json``
#          `find inputs/build/NA12878/test -name "${name}.*json"`)
# NA19240 is rendered but not validated by CI; that omission is copied deliberately.
TEST_DIRS = ("ref_panel_1kg/test", "hgdp/test", "NA12878/test")
# scripts/test/terra_validation.py: TERRA_INPUTS_PATHS, paired by basename.split(".")[0] + ".wdl"
# over os.listdir (non-recursive, so output_configurations/ is excluded, as in CI).
TERRA_DIRS = ("ref_panel_1kg/terra/workflow_configurations", "NA12878/terra")
BUILD = "inputs/build"

# gatk-sv's own renderer, run from the root of the temp tree exactly as the workflow runs it.
RENDER_SCRIPT = "scripts/inputs/build_default_inputs.sh"
RENDER_SUBTREES = ("inputs", "scripts/inputs")

# Statuses meaning "nothing was compared", not "clean".
NOTHING = ("NO-WDL", "LOAD-FAILURE", "BAD-JSON", "NO-INPUTS", "NO-MINIWDL")


def required_inputs(doc) -> list:
    """Tuples (json_key, type_text, wdl_line, parent_key) that an input file MUST carry for this WDL.

    Required means: a workflow input with no default that is not optional.
    """
    import WDL                    # lazy; see `check_workflow`
    wf = doc.workflow
    if wf is None:
        return []
    ns = wf.name
    out = []
    for decl in wf.inputs:
        if decl.expr is not None:
            continue                                     # has a default
        typ = decl.type
        if getattr(typ, "optional", False):
            continue
        line = getattr(getattr(decl, "pos", None), "line", 0)
        key = "%s.%s" % (ns, decl.name)
        if isinstance(typ, WDL.Type.StructInstance):
            members = _struct_members(typ, key, line, [])
            if members:
                out.extend(members)
                continue
        out.append((key, str(typ), line, None))
    return out


def expected_input_keys(doc) -> set:
    """Every key an input file may legally name for this WDL, spelled the way `womtool inputs` spells it.

    This is miniwdl's `Workflow.available_inputs`: workflow inputs plus every unbound call input
    (`Workflow.call_name.input`, recursively). `_*` names are miniwdl's synthetic runtime overrides.
    """
    wf = doc.workflow
    if wf is None:
        return set()
    ns = wf.name
    out = set()
    for binding in wf.available_inputs:
        if binding.name.startswith("_"):
            continue
        out.add("%s.%s" % (ns, binding.name))
    return out


def extra_keys(provided: set, expected: set) -> list:
    """Keys the input JSON names that the WDL does not accept (womtool: "Unexpected input provided").

    Looser than womtool in two ways, so it never reports a false finding: keys below a declared key
    (struct/map members, including flat struct member paths) are accepted, and namespace containers
    are not reported. Only the top-most unexplained key of a nested value is reported.
    """
    out = []
    for key in sorted(provided, key=lambda k: (k.count("."), k)):
        if key in expected:
            continue
        if any(key.startswith(k + ".") for k in expected):        # a member of something declared
            continue
        if any(k.startswith(key + ".") for k in expected):        # a namespace container, not a key
            continue
        if any(key.startswith(k + ".") for k in out):             # already named by its parent
            continue
        out.append(key)
    return out


def nearest_expected(key: str, expected: set) -> str:
    """The closest declared key, as a rename hint ('' if none)."""
    hit = difflib.get_close_matches(key, sorted(expected), n=1, cutoff=0.8)
    return hit[0] if hit else ""


def _struct_members(stype, prefix: str, line: int, seen: list) -> list:
    """Required members of a struct, recursively; `seen` guards a self-referential struct.

    Each member is returned with `prefix` as its parent key, since binding the whole struct as one
    object satisfies every member.
    """
    import WDL                    # lazy; see `check_workflow`
    if stype.type_name in seen:
        return []
    out = []
    for mname, mtype in stype.members.items():
        if getattr(mtype, "optional", False):
            continue
        if isinstance(mtype, WDL.Type.StructInstance):
            out.extend(_struct_members(mtype, "%s.%s" % (prefix, mname), line, seen + [stype.name]))
        else:
            out.append(("%s.%s" % (prefix, mname), str(mtype), line, prefix))
    return out


def provided_keys(path: str) -> set:
    """The KEY SET of an input JSON, flattened the way Cromwell flattens it.

    `{"W.st": {"a": 1}}` yields `W.st` and `W.st.a`. Lists are values and are not walked.
    """
    with open(path) as fh:
        doc = json.load(fh)
    if not isinstance(doc, dict):
        raise ValueError("%s is not a JSON object at the top level" % path)
    keys = set()

    def walk(obj: dict, prefix: str) -> None:
        for key, val in obj.items():
            name = "%s.%s" % (prefix, key) if prefix else key
            keys.add(name)
            if isinstance(val, dict):
                walk(val, name)

    walk(doc, "")
    return keys


def find_pairs(build_root: str, name: str, terra: bool) -> list:
    """(path, kind) for the JSONs CI would pair with `wdl/<name>.wdl`, per the two upstream rules."""
    out = []
    if not os.path.isdir(build_root):
        return out
    for sub in TEST_DIRS:
        root = os.path.join(build_root, sub)
        if not os.path.isdir(root):
            continue
        for dirpath, _dirs, files in os.walk(root):
            for base in sorted(files):
                if fnmatch.fnmatchcase(base, name + ".*json"):
                    out.append((os.path.join(dirpath, base), "test"))
    if terra:
        for sub in TERRA_DIRS:
            root = os.path.join(build_root, sub)
            if not os.path.isdir(root):
                continue
            for base in sorted(os.listdir(root)):
                if base.endswith(".json") and base.split(".")[0] == name:
                    out.append((os.path.join(root, base), "terra"))
    return sorted(set(out))


def load_workflow(wdl_dir: str, name: str):
    """(doc, status, note) for `<wdl_dir>/<name>.wdl`, loaded with its directory on the import path.

    `status` is "" on success, else `NO-WDL` (missing, or tasks only), `LOAD-FAILURE` or `NO-MINIWDL`.
    Also used by `checks/terra_entity_check.py`.
    """
    try:
        import WDL                                 # miniwdl
    except ImportError:
        # miniwdl is a dev dependency (requirements-dev.txt); report a status, not a traceback.
        return None, "NO-MINIWDL", ("this interpreter cannot import WDL (miniwdl): "
                                    "pip install -r requirements-dev.txt")
    path = os.path.join(wdl_dir, name + ".wdl")
    if not os.path.isfile(path):
        return None, "NO-WDL", "no %s.wdl in %s" % (name, wdl_dir)
    try:
        doc = WDL.load(path, path=[wdl_dir], import_max_depth=15)
    except Exception as exc:
        # miniwdl's errors share no common base class, so catch Exception. Report only the first
        # of multiple validation errors.
        detail = str(exc)
        if isinstance(exc, WDL.Error.MultipleValidationErrors) and exc.exceptions:
            detail = str(exc.exceptions[0])
        return None, "LOAD-FAILURE", "%s: %s" % (
            type(exc).__name__, detail.strip().splitlines()[0] if detail.strip() else "")
    if doc.workflow is None:
        return None, "NO-WDL", "%s.wdl declares tasks only: there is nothing to bind" % name
    return doc, "", ""


def check_workflow(wdl_dir: str, name: str, build_root: str, terra: bool = True) -> dict:
    """One workflow's verdict. Ordinary failures come back as a `status`, never as a traceback.

    `status` covers missing required keys; `extra_status` covers extra keys.

    miniwdl is imported lazily (in `load_workflow` and helpers) so `--render-only` and `--help` work
    on an interpreter that has jinja2 but not miniwdl, which is what `checks/wdl_gate.sh` uses to render.
    """
    doc, bad, note = load_workflow(wdl_dir, name)
    if doc is None:
        return {"status": bad, "pairs": [], "missing": [], "extras": [], "note": note}
    req = required_inputs(doc)
    expected = expected_input_keys(doc)
    ns = doc.workflow.name
    pairs = find_pairs(build_root, name, terra)
    if not pairs:
        return {"status": "NO-INPUTS", "pairs": [], "missing": [], "extras": [], "required": len(req),
                "expected": len(expected), "ns": ns,
                "note": "no rendered input JSON pairs with %s" % name}
    keys = {}
    for ppath, _kind in pairs:
        try:
            keys[ppath] = provided_keys(ppath)
        except (ValueError, OSError) as exc:
            return {"status": "BAD-JSON", "pairs": pairs, "missing": [], "extras": [], "note": str(exc)}
    missing = []
    for key, type_text, line, parent in req:
        absent = [(p, k) for p, k in pairs
                  if key not in keys[p] and not (parent and parent in keys[p])]
        if absent:
            missing.append({"key": key, "type": type_text, "line": line,
                            "absent": absent, "of": len(pairs)})
    # Extra keys, per pair.
    unexplained = {ppath: set(extra_keys(keys[ppath], expected)) for ppath in keys}
    extras = []
    for key in sorted({k for s in unexplained.values() for k in s}):
        extras.append({"key": key, "found": [(p, kind) for p, kind in pairs if key in unexplained[p]],
                       "of": len(pairs), "near": nearest_expected(key, expected)})
    namespaces = set()
    for ppath in keys:
        namespaces |= {k.split(".")[0] for k in keys[ppath]}
    note = ""
    if namespaces != {ns}:
        note = ("the key namespace in those JSONs is %s, not the workflow name %s: womtool would call "
                "every key out of place" % ("/".join(sorted(namespaces)), ns))
    return {"status": "FINDING" if missing else "OK", "pairs": pairs, "missing": missing,
            "extra_status": "FINDING" if extras else "OK", "extras": extras,
            "required": len(req), "expected": len(expected), "ns": ns, "note": note}


# Used to pick womtool's error line out of banners, logging and stack traces.
ERROR_MARKS = ("not specified", "unrecognized", "out-of-place", "error", "invalid", "exception",
               "failed", "unable", "mismatch", "unexpected input")

# Classify a womtool rejection by what it is about. `womtool validate` also evaluates values (e.g. a
# Terra `${this.x}` placeholder fails to coerce to Array[File]); this check reads only keys, so value
# rejections are reported separately rather than as a disagreement.
KEY_MARKS = ("not specified", "no such key", "required workflow input")
EXTRAS_MARKS = ("unexpected input", "unrecognized", "unrecognised", "out-of-place")
VALUE_MARKS = ("failed to evaluate input", "no coercion defined", "coercion", "was not found in the",
               "not a valid", "illegal")


def rejection_class(text: str) -> str:
    """'keys' (missing required input), 'extras' (undeclared key), 'value', or 'other'; checked in that order."""
    low = text.lower()
    if any(mark in low for mark in KEY_MARKS):
        return "keys"
    if any(mark in low for mark in EXTRAS_MARKS):
        return "extras"
    if any(mark in low for mark in VALUE_MARKS):
        return "value"
    return "other"


def headline(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for ln in lines:
        low = ln.lower()
        if any(mark in low for mark in ERROR_MARKS):
            return ln
    return lines[0] if lines else "(no output)"


def womtool_run(wdl_dir: str, name: str, pairs: list, jar: str, java: str) -> dict:
    """CI's own command per pair: `java -jar $WOMTOOL_JAR validate <wdl> -i <json>`.

    Offline. `class_counts` tallies failures by `rejection_class`; `value_failures` counts every
    failure that is not a missing-key rejection.
    """
    base = {"pairs": 0, "failures": 0, "value_failures": 0, "lines": [],
            "class_counts": {"keys": 0, "extras": 0, "value": 0, "other": 0}}
    if not jar:
        return dict(base, status="SKIPPED", reason="no-jar", detail="WOMTOOL_JAR is unset")
    if not os.path.isfile(jar):
        return dict(base, status="SKIPPED", reason="jar-missing", detail="no such jar: %s" % jar)
    if shutil.which(java) is None:
        return dict(base, status="SKIPPED", reason="no-java",
                    detail="no '%s' on PATH (set JAVA=/path/to/java)" % java)
    if not pairs:
        return dict(base, status="NO-PAIRS", reason="no-input-json",
                    detail="CI has no input JSON for this workflow either")
    res = dict(base, status="RUN", reason="", detail="", pairs=len(pairs))
    for ppath, _kind in pairs:
        cmd = [java, "-jar", jar, "validate", os.path.join(wdl_dir, name + ".wdl"), "-i", ppath]
        try:
            proc = subprocess.run(cmd, cwd=wdl_dir, capture_output=True, text=True, timeout=600)
        except (OSError, subprocess.SubprocessError) as exc:
            res["failures"] += 1
            res["class_counts"]["other"] += 1
            res["value_failures"] += 1
            res["lines"].append("womtool could not run (%s): %s" % (" ".join(cmd), exc))
            continue
        first = headline(proc.stdout + proc.stderr)
        if proc.returncode == 0:
            res["lines"].append("womtool validate rc=0 %s" % ppath)
        else:
            cls = rejection_class(proc.stdout + proc.stderr)
            res["failures"] += 1
            res["class_counts"][cls] += 1
            if cls != "keys":
                res["value_failures"] += 1
            res["lines"].append("womtool validate rc=%s %s\n      %s" % (proc.returncode, ppath, first))
    return res


# Ownership stamp at the root of a rendered tree (git archive never writes there).
STAMP = ".gsvtk-render-stamp"
STAMP_TOOL = "checks/wdl_inputs_check.py"


def stamp_ref(dest: str) -> str:
    """The ref this tree was stamped with, or '' when nothing here says gsvtk rendered it.

    A stamp that will not parse, names another tool, or names no ref counts as absent.
    """
    try:
        with open(os.path.join(dest, STAMP)) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return ""
    if not isinstance(doc, dict) or doc.get("tool") != STAMP_TOOL:
        return ""
    ref = doc.get("ref")
    return ref if isinstance(ref, str) else ""


def write_stamp(dest: str, ref: str) -> None:
    """Stamp the destination before any content is written."""
    with open(os.path.join(dest, STAMP), "w") as fh:
        json.dump({"tool": STAMP_TOOL, "ref": ref}, fh, sort_keys=True)
        fh.write("\n")


def dest_refusal(dest: str, ref: str) -> dict:
    """May this destination be replaced by a render of `ref`? {} means yes; a dict is the refusal.

    Only an empty directory or one stamped by this tool for the same ref may be replaced.
    """
    if not os.path.exists(dest):
        return {}
    if not os.path.isdir(dest):
        return {"reason": "not-a-directory",
                "detail": "%s is not a directory, so it cannot be a render destination: refusing to "
                          "touch it. Point --dest at a directory of its own." % dest}
    if not os.listdir(dest):
        return {}                                        # empty
    stamped = stamp_ref(dest)
    if not stamped:
        return {"reason": "un-stamped",
                "detail": "%s is not empty and carries no gsvtk render stamp (%s): this tool deletes "
                          "its destination before it renders, so it will not delete a directory it "
                          "did not create. Render into a directory of its own (--dest), or remove it "
                          "yourself if nothing is reading it." % (dest, os.path.join(dest, STAMP))}
    if stamped != ref:
        return {"reason": "other-ref",
                "detail": "%s was rendered by this tool at ref %s, not ref %s: two refs cannot share "
                          "one destination. Give one of them a directory of its own (--dest) to have "
                          "both trees." % (dest, stamped, ref)}
    return {}


def render(repo: str, ref: str, dest: str) -> dict:
    """`git archive` the two subtrees CI needs into a temp tree, then run CI's own renderer.

    Returns `status=REFUSED` without deleting anything if `dest_refusal` objects.
    """
    repo = os.path.expanduser(repo)
    if not repo or not os.path.isdir(os.path.join(repo, ".git")):
        return {"status": "SKIPPED", "reason": "no-checkout", "jsons": 0,
                "detail": "no gatk-sv clone at %r (set GSVTK_GATK_SV_CHECKOUT, or pass --inputs-root)"
                          % (repo or "")}
    refusal = dest_refusal(dest, ref)
    if refusal:
        return {"status": "REFUSED", "reason": refusal["reason"], "jsons": 0, "detail": refusal["detail"]}
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    write_stamp(dest, ref)
    arc = subprocess.run(["git", "-C", repo, "archive", ref, *RENDER_SUBTREES], capture_output=True)
    if arc.returncode != 0:
        # Only the stamp we just wrote is here, so removing it is safe.
        shutil.rmtree(dest, ignore_errors=True)
        return {"status": "FAILED", "reason": "git-archive", "jsons": 0,
                "detail": (arc.stderr or b"").decode(errors="replace").strip()[:300]}
    untar = subprocess.run(["tar", "-x", "-C", dest], input=arc.stdout, capture_output=True)
    if untar.returncode != 0:
        return {"status": "FAILED", "reason": "tar", "jsons": 0,
                "detail": (untar.stderr or b"").decode(errors="replace").strip()[:300]}
    if not os.path.isfile(os.path.join(dest, RENDER_SCRIPT)):
        return {"status": "SKIPPED", "reason": "no-render-script", "jsons": 0,
                "detail": "%s is absent at %s, so this ref has no default-inputs renderer to mirror"
                          % (RENDER_SCRIPT, ref)}
    py = os.environ.get("GSVTK_RENDER_PYTHON") or sys.executable
    if not _imports_jinja2(py):
        return {"status": "SKIPPED", "reason": "no-jinja2", "jsons": 0,
                "detail": "%s cannot import jinja2, which %s imports "
                          "(python -m pip install jinja2)" % (py, "build_inputs.py")}
    # Upstream invokes build_inputs.py via `#!/usr/bin/env python`; put a `python` shim for `py`
    # first on PATH.
    bindir = os.path.join(dest, ".gsvtk-bin")
    os.makedirs(bindir)
    shim = os.path.join(bindir, "python")
    with open(shim, "w") as fh:
        fh.write("#!/bin/sh\nexec \"%s\" \"$@\"\n" % py)
    os.chmod(shim, 0o755)
    env = dict(os.environ, PATH=bindir + os.pathsep + os.environ.get("PATH", ""))
    proc = subprocess.run(["bash", RENDER_SCRIPT, "-d", "."], cwd=dest, env=env,
                          capture_output=True, text=True, timeout=900)
    jsons = count_jsons(dest)
    if proc.returncode != 0 or jsons == 0:
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
        return {"status": "FAILED", "reason": "renderer", "jsons": jsons,
                "detail": "rc=%s %s" % (proc.returncode, " | ".join(tail))}
    return {"status": "OK", "reason": "", "jsons": jsons, "detail": dest}


def _imports_jinja2(py: str) -> bool:
    try:
        return subprocess.run([py, "-c", "import jinja2"], capture_output=True).returncode == 0
    except OSError:
        return False


def count_jsons(dest: str) -> int:
    """Number of input JSONs the renderer produced."""
    root = os.path.join(dest, BUILD)
    if not os.path.isdir(root):
        return 0
    return sum(len([f for f in files if f.endswith(".json")]) for _d, _s, files in os.walk(root))


def report(name: str, res: dict, wom: dict) -> int:
    """Print one workflow's answer in the gate's idiom: machine line first, then human detail."""
    print("GSVTK-INPUTS wf=%s pairs=%s missing=%s status=%s"
          % (name, len(res["pairs"]), len(res["missing"]), res["status"]))
    rc = 0
    for miss in res["missing"]:
        rc = 1
        print("  MISSING-INPUT %s (%s, %s.wdl:%s) is required by the workflow and absent from %s of "
              "%s CI-matched input JSON%s"
              % (miss["key"], miss["type"], name, miss["line"], len(miss["absent"]), miss["of"],
                 "" if miss["of"] == 1 else "s"))
        for ppath, kind in miss["absent"]:
            print("      [%s] %s" % (kind, ppath))
    if res["status"] in NOTHING:
        rc = 2
        print("  NOTHING-CHECKED %s: %s" % (name, res["note"]))
        if res["status"] == "NO-INPUTS":
            print("        CI womtool-validates nothing for %s either: %s/**/%s.*json does not exist"
                  % (name, BUILD, name))
            print("        at this ref (nor any Terra workflow_configuration). That is CI's blind spot")
            print("        on this workflow, not a pass: an unbound required input here stays invisible")
            print("        until somebody renders a JSON for it.")
        if res["status"] == "NO-MINIWDL":
            print("        miniwdl is a DEV dependency: pip install -r requirements-dev.txt (or use the")
            print("        interpreter that has it). Nothing was compared: the required keys live in the AST,")
            print("        and the AST was never read.")
    elif res["note"]:
        print("  NOTE %s" % res["note"])

    print("GSVTK-EXTRAS wf=%s pairs=%s extras=%s status=%s"
          % (name, len(res["pairs"]), len(res["extras"]),
             res["status"] if res["status"] in NOTHING else res.get("extra_status", "OK")))
    for extra in res["extras"]:
        rc = 1
        print("  EXTRA-KEY %s is named by %s of %s CI-matched input JSON%s and declared nowhere in %s"
              % (extra["key"], len(extra["found"]), extra["of"], "" if extra["of"] == 1 else "s",
                 name + ".wdl"))
        for ppath, kind in extra["found"]:
            print("      [%s] %s" % (kind, ppath))
        print("      This is what womtool calls \"Unexpected input provided: %s\", and gatk-sv CI fails" %
              extra["key"])
        print("      on it twice: validate.sh womtool-validates the test JSONs, and terra_validation.py's")
        print("      own loop is the -t half.")
        print("      %s" % ("Nearest declared key: %s — a rename leaves the old binding behind, which is "
                            "the usual reason for this line." % extra["near"] if extra["near"]
                            else "No declared key is close to it, so this is not a rename left behind: a "
                                 "binding was added for an input the WDL never had."))

    print("GSVTK-WOMTOOL wf=%s status=%s pairs=%s failures=%s%s%s"
          % (name, wom["status"], wom["pairs"], wom["failures"],
             (" reason=%s" % wom["reason"]) if wom["reason"] else "",
             (" detail=%s" % wom["detail"]) if wom["detail"] else ""))
    if wom["status"] == "SKIPPED":
        print("  SKIPPED womtool validate for %s: %s — CI's own verdict was not reproduced here. This"
              % (name, wom["detail"]))
        print("        is a counted skip, not a pass; --strict fails on it.")
    for ln in wom["lines"]:
        print("  %s" % ln)
    if wom["failures"]:
        rc = 1
    if wom["status"] == "RUN":
        ours, theirs = res["status"] == "FINDING", wom["failures"] > 0
        counts = wom.get("class_counts") or {}
        extras_theirs = counts.get("extras", 0)
        extras_ours = len(res["extras"])
        if theirs and extras_theirs and extras_ours:
            # Both sides found extra keys.
            print("  EXTRAS-CONFIRMED womtool rejected %s pair(s) for keys the WDL does not declare, and "
                  % extras_theirs)
            print("        this layer named %s of them on the same file(s). Two independent mirrors, the "
                  % extras_ours)
            print("        same finding: the binding is stale, and CI is red on it either way.")
        elif theirs and extras_theirs:
            # womtool found an extra key that the looser `extra_keys` rule accepts.
            print("  EXTRAS-GAP womtool rejected %s pair(s) because the input JSON names a key the WDL "
                  % extras_theirs)
            print("        does not declare, and this layer's own GSVTK-EXTRAS line found none on the same")
            print("        file(s). Its rule is the deliberately looser of the two (see `extra_keys`), so on")
            print("        this input it does not ask the question womtool just asked — it is not that one")
            print("        mirror is wrong. womtool is the one CI runs: act on the line above.")
        elif theirs and wom["value_failures"] == wom["failures"]:
            # Every rejection was about values, which this check does not read.
            print("  OUT-OF-LAYER all %s womtool rejection(s) are about a VALUE it could not evaluate,"
                  % wom["failures"])
            print("        not about which inputs the JSON names. This check reads key sets and never a")
            print("        value, so it neither predicts that rejection nor contradicts it — and CI does")
            print("        not put that question to Terra workflow_configurations either: validate.sh's")
            print("        -t half is terra_validation.py, which says of itself \"Does not perform")
            print("        type-checking\", because those files are ${this.x} placeholders. The womtool")
            print("        verdict above stands and still fails this run; it is not a verdict on this")
            print("        check's answer.")
            if res["missing"]:
                print("        And because womtool stops at its first error per pair, on these file(s) it")
                print("        never reached key presence: the %s MISSING-INPUT line(s) above are"
                      % len(res["missing"]))
                print("        confirmed by neither side.")
        elif ours != theirs:
            rc = 1
            print("  DISAGREES this check says %s and womtool says %s for the same %d file(s). One of"
                  % ("missing-keys" if ours else "clean", "missing-keys" if theirs else "clean",
                     len(res["pairs"])))
            print("        the two mirrors is wrong; womtool is the one CI runs, so start there. The")
            print("        likely causes are a struct member this file expands differently, or a call")
            print("        -level key womtool accepts and this pairing does not.")
        elif extras_ours:
            # Should not happen (our expected set is a superset of womtool's); likely a WDL/render
            # ref mismatch.
            print("  EXTRAS-UNCONFIRMED this layer named %s extra key(s) and womtool accepted every "
                  % extras_ours)
            print("        pair, which the two rules cannot both be right about: a key this file cannot")
            print("        attribute to a declaration is one `womtool inputs` should not list either (that")
            print("        superset relation is measured, not assumed). Check that the WDL and the render")
            print("        belong to the same ref before believing either answer.")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--wdl-dir", help="a materialized WDL tree (scripts/fetch_wdl.py --dest)")
    ap.add_argument("--wf", action="append", default=[],
                    help="workflow to check, by WDL file name (repeatable)")
    ap.add_argument("--inputs-root", help="a tree holding inputs/build/**.json; skips rendering")
    ap.add_argument("--repo", default=config.get("GATK_SV_CHECKOUT"),
                    help="gatk-sv clone to render from (default GSVTK_GATK_SV_CHECKOUT)")
    ap.add_argument("--ref", default="origin/main", help="ref to render from (default origin/main)")
    ap.add_argument("--dest", help="where to render (default: one work-dir directory per ref). A "
                                   "destination is replaced only when this tool stamped it at this "
                                   "ref: a non-empty directory that is not stamped, or is stamped at "
                                   "another ref, is refused rather than deleted")
    ap.add_argument("--render-only", action="store_true",
                    help="render the default inputs and stop (what the gate does once per ref)")
    ap.add_argument("--no-terra", action="store_true",
                    help="skip the Terra cohort/single-sample configs, which CI checks only with -t")
    ap.add_argument("--womtool-jar", default=os.environ.get("WOMTOOL_JAR", ""),
                    help="a womtool jar (default $WOMTOOL_JAR); without one the layer is a counted skip")
    ap.add_argument("--java", default=os.environ.get("JAVA", "java"),
                    help="the `java` used to run it (default $JAVA, then java on PATH)")
    a = ap.parse_args()

    # `work_path` does not create the directory; render() does, only when it renders.
    dest = a.dest or str(config.work_path("wdl-inputs", a.ref.replace("/", "-")))
    if a.render_only:
        res = render(str(a.repo or ""), a.ref, dest)
        print("GSVTK-RENDER status=%s jsons=%s%s%s"
              % (res["status"], res["jsons"],
                 (" reason=%s" % res["reason"]) if res["reason"] else "",
                 (" dest=%s" % res["detail"]) if res["status"] == "OK" else ""))
        if res["status"] != "OK":
            print("  RENDER %s (%s): %s" % (res["status"], res["reason"], res["detail"]))
            return 1
        return 0

    if not a.wf:
        ap.error("name at least one workflow with --wf")
    if not a.wdl_dir:
        ap.error("--wdl-dir is required (or use --render-only)")
    build_root = os.path.join(a.inputs_root, BUILD) if a.inputs_root else None
    if build_root is not None and not os.path.isdir(build_root):
        # Distinct from NO-INPUTS: the given tree has no rendered inputs at all.
        for name in a.wf:
            print("GSVTK-INPUTS wf=%s pairs=0 missing=0 status=NO-BUILD" % name)
            print("GSVTK-EXTRAS wf=%s pairs=0 extras=0 status=NO-BUILD" % name)
            print("  NOTHING-CHECKED %s: %s does not exist. Render it (--repo/--ref, or "
                  % (name, build_root))
            print("        gatk-sv's own scripts/inputs/build_default_inputs.sh) or pass the tree you")
            print("        actually rendered to --inputs-root.")
            print("GSVTK-WOMTOOL wf=%s status=SKIPPED pairs=0 failures=0 reason=no-build" % name)
        return 2
    if build_root is None:
        res = render(str(a.repo or ""), a.ref, dest)
        print("GSVTK-RENDER status=%s jsons=%s%s%s"
              % (res["status"], res["jsons"],
                 (" reason=%s" % res["reason"]) if res["reason"] else "",
                 (" dest=%s" % res["detail"]) if res["status"] == "OK" else ""))
        if res["status"] != "OK":
            # A refusal is not a missing prerequisite, so the human line does not say SKIPPED.
            if res["status"] == "REFUSED":
                print("  RENDER-REFUSED no input JSON was read: %s" % res["detail"])
            else:
                print("  SKIPPED the input-binding layer: the renderer %s (%s)"
                      % (res["status"], res["detail"]))
            # The machine lines report the layer as skipped (no JSON reached it); the selftest pins
            # these tokens, so do not rename them.
            for name in a.wf:
                print("GSVTK-INPUTS wf=%s pairs=0 missing=0 status=RENDER-SKIPPED" % name)
                print("GSVTK-EXTRAS wf=%s pairs=0 extras=0 status=RENDER-SKIPPED" % name)
                print("GSVTK-WOMTOOL wf=%s status=SKIPPED pairs=0 failures=0 reason=render" % name)
            return 1
        build_root = os.path.join(dest, BUILD)

    # 1 (finding) outranks 2 (nothing checked).
    worst = 0
    for name in a.wf:
        res = check_workflow(a.wdl_dir, name, build_root, terra=not a.no_terra)
        pairs = [] if res["status"] in NOTHING else res["pairs"]
        wom = womtool_run(a.wdl_dir, name, pairs, a.womtool_jar, a.java)
        code = report(name, res, wom)
        if code == 1:
            worst = 1
        elif code == 2 and worst == 0:
            worst = 2
    return worst


if __name__ == "__main__":
    sys.exit(main())
