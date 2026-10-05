#!/usr/bin/env python3
"""wdl_inputs_check.py — does the input JSON that CI renders actually bind what the WDL requires?

Why this exists, with a real reproduction
-----------------------------------------
gatk-sv `7fbf1171` was merged with `String sample_id` (required, no default,
`wdl/IntegrateGDVcf.wdl:31`) while `inputs/templates/test/IntegrateGDVcf/IntegrateGDVcf.json.tmpl`
carried **zero** occurrences of `sample_id`. gatk-sv's CI said no:

    Required workflow input 'IntegrateGDVcf.sample_id' not specified

`checks/wdl_gate.sh` — miniwdl, IncompleteCall, stale bindings, the semantics scan — said clean,
exit 0. It was right and incomplete: `sample_id` IS bound at the call site (so no IncompleteCall),
and miniwdl never looks at an input file at all. The question CI asks is a different one: **given
the input JSONs this repo renders, is every input the workflow requires actually present?** That is
womtool's `Test with WOMtool` step (`.github/workflows/testwdls.yaml`), and until this file existed
the gate ran half of CI and called it a pass. `01107996` is the fix (`String? sample_id`), and
against it this check finds nothing — which is the half of the story that keeps it honest.

What is mirrored, and what is re-implemented
--------------------------------------------
CI is two commands:

    scripts/inputs/build_default_inputs.sh -d .       # render inputs/build/**.json from the templates
    scripts/test/validate.sh -t -d . -j womtool.jar   # womtool-validate the JSONs whose names match

**Rendering is shelled out to gatk-sv's own script, not reimplemented.** Which JSONs exist at all is
decided by upstream's `build_inputs.py`: a template that touches an undefined value is *skipped*, so
the file set is a function of `inputs/values/*.json` and of the alias combinations in
`build_default_inputs.sh`. Reimplementing that is how a mirror starts passing a tree CI rejects. So
the ref's own `scripts/inputs/build_default_inputs.sh` runs — inside a temp tree built by
`git archive` of `inputs/` and `scripts/inputs/` (about 1 MB), because the gatk-sv checkout this reads
is read-only and that script's whole job is writing into the tree. Nothing here ever writes to a
checkout.

**The pairing and the verdict ARE re-implemented**, because upstream offers no way to ask about one
workflow: `validate.sh` loops over all ~118 `wdl/*.wdl` with no filter and refuses to start without a
jar. Its pairing rule is copied as literally as bash-to-python allows, and named where it comes from
(`TEST_DIRS`, `TERRA_DIRS`), including the omission of `NA19240` from the test loop. Parity at a ref
is the promise, not a better check.

The other half of the question: EXTRA keys
------------------------------------------
Everything above asks one direction: is every REQUIRED key in the JSON. The opposite direction — is
every key in the JSON something the WDL declares — was measured before it was written, because a check
that cries wolf on a clean tree is worse than the blindness it closes. Three real trees, every pair
this file pairs (38 workflows, 77 input JSONs each), expected set taken from womtool-84's own
`womtool inputs`: gatk-sv `origin/main` (c0314afa), `7fbf1171` (CI-red), `01107996` (its fix):

    origin/main   pairs=77  extras=0        7fbf1171  pairs=77  extras=0        01107996  pairs=77  extras=0

Zero on a CI-green tree AND on a CI-red one, so an extra key is CI-red material and this half is a
finding, not a warning — the same call `MISSING-INPUTS` made. What is NOT in the expected set is not
merely the workflow's own declarations: `womtool inputs` also lists every input of every call the call
site does not bind (`Workflow.call_name.input`, nested workflow calls included), so binding a task
input from the JSON is legal, not extra. Measured across those three refs and the 109 workflows with a
primary callable: this file's expected set is a SUPERSET of womtool's for every one of them (0 keys
womtool expects that this file does not), which is the property that makes a nonzero `extras` a finding
rather than a coin flip — a key this file cannot explain is a key womtool cannot explain either.

Three ways this file is LOOSER than womtool here, each measured at womtool-84 and each costing
blindness rather than a false finding (see `extra_keys`): a key BELOW a declared key is read as a member
of a value, which womtool also accepts inside a struct literal; a key that is a namespace CONTAINER is
not reported, because womtool does not name it either; and a struct member path written flat as its own
key is legal here because `required_inputs` already calls that same spelling bound — where womtool
really does answer `Unexpected input provided: ShapeProbe.struct_opt.label`. That last one is the known,
pinned divergence, and the WOMTOOL row is where it shows.

Key sets, never values
----------------------
womtool checks **key presence**, not values: the same input file with every value replaced by
`gs://nowhere/x` gets the same verdict, and gatk-sv's Terra configs are nothing but
`${this.whatever}` placeholders that no filesystem could open. So this file reads each matched JSON's
**key set** and never inspects a value — not for type, not for existence, not for sanity. The tests in
`scripts/selftest.d/womtool.sh` assert exactly that: two fixtures differing only in values give the
same verdict, and two differing in a key give different ones. What this cannot tell you — and says so
— is whether a bound value is *usable*: that is a submission question, and `terra/batch_check_inputs.py`
is the tool that asks it.

The jar, when you have one
--------------------------
With `WOMTOOL_JAR` set (and `java` on PATH) each pair ALSO gets CI's exact command,
`java -jar $WOMTOOL_JAR validate <wdl> -i <json>`, run locally. Two answers per pair are the point:
womtool's is authoritative, this file's is offline, and when they disagree the disagreement is
printed and the exit code is nonzero, because one of the two mirrors is wrong. Without a jar nothing
is pretended: the layer prints a named, counted `SKIPPED` line — the idiom every other prerequisite
in this repo uses — and `checks/wdl_gate.sh --strict` fails on it. "Green" from a check that never ran
is the bug this file exists to close, so a skip is never a pass.

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
import config  # noqa: E402  # GATK_SV_CHECKOUT and the work dir: one source of truth for both
import WDL  # noqa: E402  # miniwdl: the AST is the only thing that knows what the WDL requires

# scripts/test/validate.sh:
#   JSONS=(`find inputs/build/ref_panel_1kg/test -name "${name}.*json``
#          `find inputs/build/hgdp/test  -name "${name}.*json``
#          `find inputs/build/NA12878/test -name "${name}.*json"`)
# NA19240 IS rendered by build_default_inputs.sh and is NOT in that list, so CI never validates it.
# Copying that omission is deliberate: parity at a ref is the promise, not a stricter check.
TEST_DIRS = ("ref_panel_1kg/test", "hgdp/test", "NA12878/test")
# scripts/test/terra_validation.py: TERRA_INPUTS_PATHS, paired by basename.split(".")[0] + ".wdl"
# over os.listdir — non-recursive, so output_configurations/ is not in CI either. Both details are
# the pairing, so both are copied.
TERRA_DIRS = ("ref_panel_1kg/terra/workflow_configurations", "NA12878/terra")
BUILD = "inputs/build"

# gatk-sv's own renderer, run from the root of the temp tree exactly as the workflow runs it.
RENDER_SCRIPT = "scripts/inputs/build_default_inputs.sh"
RENDER_SUBTREES = ("inputs", "scripts/inputs")

# Statuses where the honest answer is "nothing was compared", not "clean".
NOTHING = ("NO-WDL", "LOAD-FAILURE", "BAD-JSON", "NO-INPUTS")


def required_inputs(doc) -> list:
    """Tuples (json_key, type_text, wdl_line, parent_key) that an input file MUST carry for this WDL.

    Required means: declared in the workflow's input section, no default expression, not optional.
    Anything else (optional, or given `= ...` in the WDL) is satisfied by the WDL itself, and
    demanding it in the JSON is how a checker manufactures findings.
    """
    wf = doc.workflow
    if wf is None:
        return []
    ns = wf.name
    out = []
    for decl in wf.inputs:
        if decl.expr is not None:
            continue                                     # the WDL supplies it; the JSON must not
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

    NOT just the workflow's declarations. `womtool inputs` also lists each input of each call that the
    call site does not bind — `Workflow.call_name.input`, and the same recursion through nested workflow
    calls — and miniwdl's `Workflow.available_inputs` is exactly that set (its own docstring: "the
    workflow's input declarations ... [and] available inputs of all calls, namespaced by the call
    names"), including inputs the task gave defaults to. Taking only the unbound-and-required subset
    would make every optional call input look extra, which is the wolf-crying shape this function exists
    to avoid; `_*` names are miniwdl's synthetic `runtime`-override placeholder, not WDL inputs.

    A struct-typed input needs no special case here: a member path written flat (`W.attrs.cpu`) is
    handled by `extra_keys`, which treats anything below a declared key as a member of a value.
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
    """Keys the input JSON names that no declaration of this WDL can name: womtool's own words are
    `WARNING: Unexpected input provided: <key> (expected inputs: [...])`, and gatk-sv's CI fails on it
    twice over — `validate.sh` on the test JSONs, and `scripts/test/terra_validation.py`'s own loop
    (`for inp in terra_inputs: if inp not in womtool_inputs: ... "Unexpected input"; valid = False`).

    Two ways this is LOOSER than womtool, both measured at womtool-84 on a synthetic WDL, both costing
    blindness rather than noise, so neither can turn a clean tree red:

    * a key under a declared key is treated as a member of a value, not as a key. womtool accepts an
      unknown member inside a struct literal outright (`{"ShapeProbe.struct_req": {"label": "x",
      "bogus_member": 3}}` -> `Success!`), and a `Map` is spelled as an object too, so nothing below a
      declared key is anybody's finding. That one rule is also what makes a flat member path of a STRUCT
      typed input legal here — `W.attrs.cpu` sits under the declared key `W.attrs` — which is the
      deliberate divergence from womtool, measured on the pinned capture (pair B of
      `scripts/selftest.d/womtool.sh`): there it answers `Unexpected input provided:
      ShapeProbe.struct_opt.label`. Under-reporting that is the price of not contradicting
      `required_inputs`, which calls the same spelling bound.
    * a key that is a namespace CONTAINER (`{"ShapeProbe": {"plain_req": ...}}`) is not reported: at
      womtool-84 that spelling is silently unfulfilling rather than unexpected (every required input
      comes back `not specified` and the container key is never named), and the required half already
      reads it as bound by flattening it.

    Only the top-most unexplained key of a nested value is reported, so one stale binding is one finding.
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
    """The declared key this one is closest to, for a human reading a rename. Advisory only: a finding
    never depends on it, and an empty answer is a normal answer for a key from another workflow."""
    hit = difflib.get_close_matches(key, sorted(expected), n=1, cutoff=0.8)
    return hit[0] if hit else ""


def _struct_members(stype, prefix: str, line: int, seen: list) -> list:
    """Required members of a struct, recursively; `seen` guards a self-referential struct.

    Cromwell wants a struct as `Workflow.attr.member`, so each required member is a required key —
    with `prefix` kept as the parent escape hatch, since an input file may hand over the whole struct
    as one object and that satisfies every member.
    """
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

    `{"W.st": {"a": 1}}` is Cromwell's `W.st.a`, so nested OBJECTS are walked. Values are never read
    beyond that — they are not this check's question (see the module docstring). Lists are values.
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


def check_workflow(wdl_dir: str, name: str, build_root: str, terra: bool = True) -> dict:
    """One workflow's verdict. Ordinary failures come back as a `status`, never as a traceback.

    Two answers come back, for the two directions the question has: `missing` (a required key the JSON
    does not name) and `extras` (a key the JSON names that no declaration can name). `status` is the
    required half ONLY — every existing reader of it, in the gate and in `report`, keeps meaning exactly
    what it meant before the extras half existed; the extras half carries `extra_status`.
    """
    path = os.path.join(wdl_dir, name + ".wdl")
    if not os.path.isfile(path):
        return {"status": "NO-WDL", "pairs": [], "missing": [], "extras": [], "note":
                "no %s.wdl in %s" % (name, wdl_dir)}
    try:
        doc = WDL.load(path, path=[wdl_dir], import_max_depth=15)
    except Exception as exc:
        # Reported, never dropped, and never a narrower catch: miniwdl's SyntaxError, ValidationError,
        # MultipleValidationErrors and ImportError each subclass Exception directly — there is no
        # shared `WDLError` base to catch, and naming one that does not exist turns a reported failure
        # into an AttributeError on the handler itself.
        detail = str(exc)
        if isinstance(exc, WDL.Error.MultipleValidationErrors) and exc.exceptions:
            detail = str(exc.exceptions[0])
        return {"status": "LOAD-FAILURE", "pairs": [], "missing": [], "extras": [],
                "note": "%s: %s" % (type(exc).__name__, detail.strip().splitlines()[0] if detail.strip() else "")}
    if doc.workflow is None:
        return {"status": "NO-WDL", "pairs": [], "missing": [], "extras": [],
                "note": "%s.wdl declares tasks only: there is nothing to bind" % name}
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
    # The other direction, per pair: `extra_keys` is a set difference against every key the WDL can
    # legally be handed, so a key showing up here is one neither this file nor womtool can attribute to
    # a declaration (see `extra_keys` for the two ways that is looser than womtool, and docs).
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


# womtool's own words, through a banner, a slf4j line, and sometimes a stack trace: the line that says
# what is wrong is picked by shape, not position, because "the first line" is a jar-not-found message
# more often than it is the answer.
ERROR_MARKS = ("not specified", "unrecognized", "out-of-place", "error", "invalid", "exception",
               "failed", "unable", "mismatch", "unexpected input")

# Which QUESTION a rejection asks, because only one of them is this file's question. Measured with the
# jar CI pins (womtool-84, `java -jar womtool-84.jar validate`) against gatk-sv `01107996`:
# `validate` does not stop at key presence — it also EVALUATES and COERCES every value the JSON hands
# it, so a Terra workflow_configuration whose `Array[File]` input carries the placeholder
# `"${this.sample_sets.ploidy_table}"` comes back
#
#     Failed to evaluate input 'ploidy_tables' (reason 1 of 1): No coercion defined from
#     '"${this.sample_sets.ploidy_table}"' of type 'spray.json.JsString' to 'Array[File]'
#
# with every required key present. That is not one mirror calling the other wrong: this file reads key
# sets and never a value (module docstring), and CI does not put that question to these files either
# — `validate.sh -t` runs `terra_validation.py`, whose own docstring says "Does not perform
# type-checking", precisely because Terra configs are `${this.x}` placeholders. So a value-class
# rejection is reported as what it is, and a DISAGREES is only claimed when both sides answered the
# same question. womtool's verdict still prints and still fails the run either way.
#
# The third class is the gap this lane just closed, and the measurement that closed it is in the module
# docstring. Measured by adding one key to a real rendered test JSON and re-running the same command
# (gatk-sv `01107996`):
#
#     WARNING: Unexpected input provided: IntegrateGDVcf.this_key_does_not_exist_in_the_wdl
#     (expected inputs: [...])                                      rc=1
#
# An input file naming a key the WDL never declares is a KEY question, and it is now this file's
# question too (`extra_keys`): a rejection of this class is comparable with the GSVTK-EXTRAS line, and
# `report` says whether the two agree or whether one of them saw something the other did not.
KEY_MARKS = ("not specified", "no such key", "required workflow input")
EXTRAS_MARKS = ("unexpected input", "unrecognized", "unrecognised", "out-of-place")
VALUE_MARKS = ("failed to evaluate input", "no coercion defined", "coercion", "was not found in the",
               "not a valid", "illegal")


def rejection_class(text: str) -> str:
    """'keys' (a required input the JSON does not name), 'extras' (a key the JSON names that the WDL does
    not declare — the `GSVTK-EXTRAS` question), 'value' (what a value evaluates to), or 'other'. Order
    matters: a rejection that names a missing required input is comparable no matter what else it says."""
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

    Local and offline — womtool parses, it launches nothing. The verdict is the exit code plus
    womtool's own first error line, so a finding here reads like the CI log. `class_counts` sorts the
    failures by the question each rejection asks (see rejection_class): every class still counts as a
    failure, `keys` is comparable with the required half and `extras` with `GSVTK-EXTRAS`, and
    `value_failures` is the total of everything that is neither (value + other + extras) — which is what
    the OUT-OF-LAYER branch tests, after the extras branches have had their say.
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


def render(repo: str, ref: str, dest: str) -> dict:
    """`git archive` the two subtrees CI needs into a temp tree, then run CI's own renderer."""
    repo = os.path.expanduser(repo)
    if not repo or not os.path.isdir(os.path.join(repo, ".git")):
        return {"status": "SKIPPED", "reason": "no-checkout", "jsons": 0,
                "detail": "no gatk-sv clone at %r (set GSVTK_GATK_SV_CHECKOUT, or pass --inputs-root)"
                          % (repo or "")}
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    arc = subprocess.run(["git", "-C", repo, "archive", ref, *RENDER_SUBTREES], capture_output=True)
    if arc.returncode != 0:
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
    # Upstream's scripts call each other by relative path and invoke build_inputs.py through its
    # `#!/usr/bin/env python` shebang, which CI satisfies with setup-python. Give them a `python`
    # that is THIS one, in a bin directory that exists only for this call and dies with it.
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
    """How many input JSONs the renderer actually produced (the anti-vacuity number)."""
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
    elif res["note"]:
        print("  NOTE %s" % res["note"])

    # The second direction, on its own machine-readable line: `GSVTK-INPUTS ... missing=` keeps meaning
    # exactly what it meant before this existed, and nothing that reads it has to change.
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
            # Both mirrors now ask the question, and both answered it: the strongest form of the answer
            # this file can give, so it is said out loud rather than left to the reader to line up.
            print("  EXTRAS-CONFIRMED womtool rejected %s pair(s) for keys the WDL does not declare, and "
                  % extras_theirs)
            print("        this layer named %s of them on the same file(s). Two independent mirrors, the "
                  % extras_ours)
            print("        same finding: the binding is stale, and CI is red on it either way.")
        elif theirs and extras_theirs:
            # womtool saw an un-declared key that this layer did NOT name. That is the looser half of
            # `extra_keys` firing (a flat struct member path, a key below a declared one, a namespace
            # container): this file does not ask about those, so it is not calling womtool wrong.
            print("  EXTRAS-GAP womtool rejected %s pair(s) because the input JSON names a key the WDL "
                  % extras_theirs)
            print("        does not declare, and this layer's own GSVTK-EXTRAS line found none on the same")
            print("        file(s). Its rule is the deliberately looser of the two (see `extra_keys`), so on")
            print("        this input it does not ask the question womtool just asked — it is not that one")
            print("        mirror is wrong. womtool is the one CI runs: act on the line above.")
        elif theirs and wom["value_failures"] == wom["failures"]:
            # Every rejection asked a question this file does not ask. Named rather than folded into a
            # DISAGREES, because "one of the two mirrors is wrong" would send a reader to fix a mirror
            # that is answering a different question (see rejection_class).
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
            # The one combination the measured relationship says should not happen: this file's expected
            # set is a superset of `womtool inputs` on every workflow measured, so a key it cannot explain
            # is a key womtool should refuse too. Named rather than swallowed, because the likeliest
            # explanations are two versions of the WDL or a stale render — not a passing pair.
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
    ap.add_argument("--dest", help="where to render (default: one work-dir directory per ref)")
    ap.add_argument("--render-only", action="store_true",
                    help="render the default inputs and stop (what the gate does once per ref)")
    ap.add_argument("--no-terra", action="store_true",
                    help="skip the Terra cohort/single-sample configs, which CI checks only with -t")
    ap.add_argument("--womtool-jar", default=os.environ.get("WOMTOOL_JAR", ""),
                    help="a womtool jar (default $WOMTOOL_JAR); without one the layer is a counted skip")
    ap.add_argument("--java", default=os.environ.get("JAVA", "java"),
                    help="the `java` used to run it (default $JAVA, then java on PATH)")
    a = ap.parse_args()

    dest = a.dest or os.path.join(config.work_dir("wdl-inputs"), a.ref.replace("/", "-"))
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
        # "You pointed me at a tree with no rendered inputs" is a different answer from "CI has no
        # input JSON for this workflow", and the second one is a claim about gatk-sv. Keep them apart.
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
            print("  SKIPPED the input-binding layer: the renderer %s (%s)"
                  % (res["status"], res["detail"]))
            for name in a.wf:
                print("GSVTK-INPUTS wf=%s pairs=0 missing=0 status=RENDER-SKIPPED" % name)
                print("GSVTK-EXTRAS wf=%s pairs=0 extras=0 status=RENDER-SKIPPED" % name)
                print("GSVTK-WOMTOOL wf=%s status=SKIPPED pairs=0 failures=0 reason=render" % name)
            return 1
        build_root = os.path.join(dest, BUILD)

    # 1 (a finding) outranks 2 (a workflow nothing could be checked for), because "found a defect" is
    # the answer the caller asked for and must never be masked by a sibling's blank cell.
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
