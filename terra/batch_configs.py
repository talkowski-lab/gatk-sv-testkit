#!/usr/bin/env python3
"""Create + validate the 06->10 joint-calling method configs for a head-to-head.

Every config runs the *branch* WDL (dockstore://github.com/broadinstitute/gatk-sv/<W>/
<GSVTK_BRANCH>) against the branch images, and writes its results into NEW entity
attributes, so the baseline attributes your clone inherited are never clobbered and
both sides stay addressable inside one workspace:

    *{FROZEN_SUFFIX}   frozen baseline upstream inputs, copied into the sandbox
                       bucket by batch_freeze.py
    *{NEW_SUFFIX}      outputs of this chain

Steps 06->10 are the batch half of the joint-calling chain (GenerateBatchMetrics,
FilterBatchSites, FilterBatchSamples, MergeBatchSites, GenotypeBatch). Extend CONFIGS
to cover other steps; the shape of every entry is the same.

    python terra/batch_configs.py show                  # print the maps (offline, no auth)
    python terra/batch_configs.py check --against main  # keys vs that ref's WDL (offline)
    python terra/batch_configs.py create                # POST the configs (mutation)
    python terra/batch_configs.py validate              # Terra-side WDL validation
    python terra/batch_configs.py --selftest            # the output-map cases, on fixtures in a temp dir

The input and output maps describe one branch's WDL signature, while GSVTK_BRANCH only chooses the
Dockstore URL; a mismatch shows up as an extra-input rejection or an output that fails at submission.
`check` compares both maps against the WDL at a ref, offline, using your own checkout and miniwdl;
`create`/`validate` run it first. It also reads upstream's `.json.tmpl` for each step and reports any
key the template binds twice (`DUPLICATE-KEY`, non-fatal).
"""
from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
sys.path.insert(0, HERE)
import config  # noqa: E402
import module_profile  # noqa: E402  (the reader of profiles/<MODULE>.json; kit/ is on sys.path)
import terra  # noqa: E402
from terra import fapi  # noqa: E402  # via terra: one friendly missing-dependency message

# Read leniently at import so `show` and `--help` work without a profile; commands that talk to Terra
# call require_target().
NS = config.get("TERRA_NAMESPACE")
WS = config.get("TERRA_WORKSPACE")
BRANCH = config.get("BRANCH")


def require_target(writes=False):
    """Resolve (NS, WS, BRANCH) or exit 4 naming the profile key that is missing.

    `writes=True` also refuses the shared baseline workspace (see terra.assert_writable_target).
    """
    global NS, WS, BRANCH
    NS = NS or config.require("TERRA_NAMESPACE", "where these configs are POSTed")
    WS = WS or config.require("TERRA_WORKSPACE", "where these configs are POSTed")
    BRANCH = BRANCH or config.require("BRANCH", "the gatk-sv branch whose WDL each config runs")
    if writes:
        terra.assert_writable_target(NS, WS, "create or overwrite method configs",
                                    allow="--allow-shared-target" in sys.argv)
    return NS, WS, BRANCH

# Attribute suffixes come from the profile loader. `FZ`/`NW` are public: other modules read them.
_SUFFIXES = module_profile.suffixes()
FZ = _SUFFIXES["frz"]
NW = _SUFFIXES["new"]
# Only a path: importing this module must not create anything; create() makes the directory.
DUMP = str(config.work_path("manifests") / "batch_configs.json")

# Config name -> set of full binding keys ("GenotypeBatch.training_vcf") that exist only on the
# branch, loaded from the module profile below.
BRANCH_ONLY_INPUTS: dict[str, set[str]] = {}




def dockstore(workflow: str) -> dict:
    if not BRANCH:
        config.require("BRANCH", "the Dockstore version each config pins")
    path = f"github.com/broadinstitute/gatk-sv/{workflow}"
    return {"sourceRepo": "dockstore", "methodPath": path, "methodVersion": BRANCH,
            "methodUri": f"dockstore://{path.replace('/', '%2F')}/{BRANCH}"}



# ---------------------------------------------------------------------------------------------
# The maps: read from the module profile, then held as module-level dicts.
#
# Frozen inputs use the `{frz}` suffix and chain outputs the `{new}` one (expanded in
# `kit/module_profile.py`). The loader never raises or creates files, so `--help` works without a
# profile; an unusable profile gives empty tables, and commands that need them call `require_module()`.
PROFILE = module_profile.load()
CONFIGS: dict = PROFILE.configs
CALLERS: list = PROFILE.callers
BRANCH_ONLY_INPUTS.update(PROFILE.branch_only_inputs)


def require_module(tag: str = "") -> None:
    """Exit 4 naming the profile file and every problem with it when no usable module profile is loaded."""
    PROFILE.require(tag or "batch_configs")


# `--drop-branch-only-inputs`: remove branch-only bindings the target ref does not declare instead of
# failing. This changes the pipeline (e.g. GenotypeBatch's PE/SR training input), so it is opt-in,
# loud (on stderr, so `show | jq` still works), and only applied when the ref can actually be read.
DROP_FLAG = "--drop-branch-only-inputs"


def _adapt_inputs(name: str, spec: dict, inputs: dict) -> dict:
    """Remove known-branch-only bindings the target ref does not declare. Prints what it dropped."""
    known = BRANCH_ONLY_INPUTS.get(name, set())
    candidates = {k: v for k, v in inputs.items() if k in known}
    if not candidates or DROP_FLAG not in sys.argv:
        return inputs
    # Same ref that preflight() checks.
    ref = _posted_ref()
    got = declared_for_config(name, ref)
    if got is None:
        print(f"{DROP_FLAG} ignored for {name}: cannot read the WDL at "
              f"{ref or 'GSVTK_BRANCH (unset)'}, and an unverified ref is not evidence that a "
              f"binding is absent.\n  Set GSVTK_GATK_SV_CHECKOUT and a resolvable GSVTK_BRANCH, or "
              f"post it as-is and accept the rejection.", file=sys.stderr)
        return inputs
    declared = got[0]
    dropped = [k for k in sorted(candidates) if k.split(".")[-1] not in declared]
    for k in dropped:
        del inputs[k]
    if dropped:
        print(f"{DROP_FLAG}: dropped {len(dropped)} binding(s) from {name} that "
              f"{ref} does not declare:", file=sys.stderr)
        for k in dropped:
            print(f"  - {k}", file=sys.stderr)
        print(f"  This is a SEMANTIC change, not a fix: main's GenotypeBatch trains PE/SR from `vcf`, "
              f"the\n  branch from a separate training VCF. You asked for {ref}, which is NOT the ref "
              f"these\n  maps were written against -- if you meant to run your own branch, unset "
              f"GSVTK_BRANCH instead\n  of using this flag. What you see in `show` is what gets "
              f"POSTed.", file=sys.stderr)
    return inputs


# ---------------------------------------------------------------------------------------------
# Call caching is off by default and opted into with --call-cache. Cross-workspace cache copies have
# timed out and failed submissions, and caching between A/B arms is wrong if an image tag is mutable.
CALL_CACHE_FLAG = "--call-cache"
NO_CALL_CACHE_FLAG = "--no-call-cache"


def call_cache(tag: str = "") -> bool:
    """The call-cache choice from argv; passing both flags is an error."""
    on = CALL_CACHE_FLAG in sys.argv
    if on and NO_CALL_CACHE_FLAG in sys.argv:
        prefix = f"{tag}: " if tag else ""
        raise SystemExit(
            f"{prefix}refusing -- {CALL_CACHE_FLAG} and {NO_CALL_CACHE_FLAG} are both on this command\n"
            f"  line. Caching is a choice, not a position on argv, and it is the one config field whose\n"
            "  wrong value can look like a clean run:\n"
            f"    {CALL_CACHE_FLAG}      Cromwell reuses call outputs whose command and inputs match. Right\n"
            "                    across retries; wrong between A/B arms if a tag is mutable, and the\n"
            "                    cross-workspace copies it attempts have timed out mid-run and failed a\n"
            "                    submission.\n"
            f"    {NO_CALL_CACHE_FLAG}   every call re-runs. The default, and what both arms of a\n"
            "                    two-arm comparison want.")
    return on


def body(name: str) -> dict:
    require_module("body")
    spec = CONFIGS[name]
    # Copy: _adapt_inputs may delete keys, and spec["inputs"] is the module-level table.
    inputs = _adapt_inputs(name, spec, dict(spec["inputs"]))
    return {"namespace": NS, "name": name, "rootEntityType": spec["rootEntityType"],
            # The Dockstore path uses the WDL file basename, which can differ from the workflow name.
            "methodRepoMethod": dockstore(spec["wdl"]),
            # Rawls rejects the body without it (400 "missing required member
            # 'methodConfigVersion'"); the server bumps it on every overwrite.
            "methodConfigVersion": 1,
            "deleted": False,  # also required by Rawls (400 "missing required member 'deleted'")
            "inputs": inputs, "outputs": spec["outputs"],
            "prerequisites": {}, "deleteIntermediateOutputFiles": False,
            "useCallCache": call_cache(), "maxMessageSize": None}


def call_cache_line() -> str:
    """The call-cache line `show` prints."""
    on = call_cache()
    what = ("calls whose command+inputs match may be reused" if on else
            "every call runs; nothing is reused")
    return f"useCallCache={str(on).lower()}  ({what}; {CALL_CACHE_FLAG} / {NO_CALL_CACHE_FLAG})"


def show():
    require_module("show")
    print(call_cache_line())
    for name in CONFIGS:
        b = body(name)
        print(f"===== {name}  [{b['rootEntityType']}]  {b['methodRepoMethod']['methodUri']}")
        for k, v in sorted(b["inputs"].items()):
            print(f"   in  {k:52s} = {v}")
        for k, v in sorted(b["outputs"].items()):
            print(f"   out {k:52s} -> {v}")


# ------------------------------------------------------------------ WDL-vs-map check
def _flag_value(flag: str) -> str | None:
    a = sys.argv
    i = a.index(flag) if flag in a else -1
    return a[i + 1] if i >= 0 and len(a) > i + 1 else None


_WDLCACHE: dict[str, str] = {}


def _wdl_dir_from_ref(ref: str) -> str:
    """Extract <checkout>/wdl at <ref> into a temp dir with `git archive` (never touches the checkout).

    Raises CannotCheck so only the one config becomes unverifiable. Cached per ref.
    """
    if ref in _WDLCACHE:
        return _WDLCACHE[ref]
    ck = config.get("GATK_SV_CHECKOUT")
    if not ck or not os.path.isdir(ck):
        raise CannotCheck("no GSVTK_GATK_SV_CHECKOUT checkout to read the WDL from")
    tmp = tempfile.mkdtemp(prefix="gsvtk-wdl-")
    atexit.register(shutil.rmtree, tmp, True)
    r = subprocess.run(["git", "-C", ck, "archive", ref, "wdl"], capture_output=True)
    if r.returncode:
        why = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise CannotCheck(f"git -C {ck} archive {ref} wdl: {(why or ['?'])[:1][0]}")
    if subprocess.run(["tar", "-x", "-C", tmp], input=r.stdout).returncode:
        raise CannotCheck("failed to unpack the WDL tree from git archive")
    _WDLCACHE[ref] = os.path.join(tmp, "wdl")
    return _WDLCACHE[ref]


class CannotCheck(Exception):
    """This config could not be compared. Reported as a finding, never as a pass."""


# ------------------------------------------------------------------ template read
# Upstream's `.json.tmpl` files say which attribute or literal feeds each WDL input.
TEMPLATE_SUBPATH = "inputs/templates"
TEMPLATE_PREFERRED = "terra_workspaces/cohort_mode/workflow_configurations"

# Templates are parsed, not rendered: Jinja `{{...}}` expressions are replaced with `null`, which
# makes them valid JSON without needing jinja2 or upstream's values bundle.
JINJA_RE = re.compile(r"\{\{.*?\}\}", re.S)

_TREES: dict = {}     # (ref, subpath) -> the unpacked directory, so one `git archive` per pair


def _tree_from_ref(ref: str, subpath: str) -> str:
    """Extract one subdirectory of <checkout> at <ref> into a temp dir with `git archive`. Cached."""
    key = (ref, subpath)
    if key in _TREES:
        return _TREES[key]
    ck = config.get("GATK_SV_CHECKOUT")
    if not ck or not os.path.isdir(ck):
        raise CannotCheck("no GSVTK_GATK_SV_CHECKOUT checkout to read it from")
    tmp = tempfile.mkdtemp(prefix="gsvtk-tree-")
    atexit.register(shutil.rmtree, tmp, True)
    r = subprocess.run(["git", "-C", ck, "archive", ref, subpath], capture_output=True)
    if r.returncode:
        why = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise CannotCheck(f"git -C {ck} archive {ref} {subpath}: {(why or ['?'])[:1][0]}")
    if subprocess.run(["tar", "-x", "-C", tmp], input=r.stdout).returncode:
        raise CannotCheck(f"failed to unpack {subpath} from git archive")
    _TREES[key] = os.path.join(tmp, subpath.split("/")[0])
    return _TREES[key]


def find_template(tmpl_dir: str, workflow: str) -> str:
    """The `.json.tmpl` for one workflow, preferring the cohort_mode one; "" if none."""
    if not tmpl_dir:
        return ""
    want = f"{workflow}.json.tmpl"
    hits = sorted(p for p in Path(tmpl_dir).rglob(want))
    if not hits:
        return ""
    pref = [p for p in hits if TEMPLATE_PREFERRED in str(p.relative_to(tmpl_dir)).replace(os.sep, "/")]
    return str((pref or hits)[0])


def _dup_key_hook(pairs: list, dups: list) -> dict:
    """`object_pairs_hook` that records keys bound more than once, then keeps json's last-wins dict.

    `checks/terra_entity_check.py::read_config` does the same; `scripts/selftest.d/profiles.sh` checks
    they agree. Inside a nested object the recorded name is the leaf, not a dotted path.
    """
    names = [k for k, _ in pairs]
    for k in names:
        if names.count(k) > 1 and k not in [d["key"] for d in dups]:
            vals = [v for kk, v in pairs if kk == k]
            dups.append({"key": k, "count": len(vals), "kept": vals[-1], "dropped": vals[:-1]})
    return dict(pairs)


def _tpl_value(value) -> str:
    """Repr of a template value; a neutralised `{{ ... }}` (None) is described as unread."""
    return "a Jinja value, unread without rendering" if value is None else repr(value)


def read_template(path: str) -> dict:
    """Parse one template: {state, keys, neutralised, dupkeys, error}.

    `state` is DERIVED, JINJA-NEUTRALISED, NO TEMPLATE or CANNOT PARSE. `keys` maps each top-level
    binding to its value (None where it was Jinja). `dupkeys` lists `{key, count, kept, dropped}` for
    every key bound more than once.
    """
    if not path:
        return {"state": "NO TEMPLATE", "keys": {}, "neutralised": 0, "dupkeys": [], "error": ""}
    try:
        text = open(path).read()
    except OSError as e:
        return {"state": "CANNOT PARSE", "keys": {}, "neutralised": 0, "dupkeys": [], "error": str(e)}
    n = len(JINJA_RE.findall(text))
    dups: list = []

    def hook(pairs):
        return _dup_key_hook(pairs, dups)

    try:
        obj = json.loads(JINJA_RE.sub("null", text), object_pairs_hook=hook)
    except ValueError as e:
        return {"state": "CANNOT PARSE", "keys": {}, "neutralised": n, "dupkeys": [], "error": str(e)}
    if not isinstance(obj, dict):
        return {"state": "CANNOT PARSE", "keys": {}, "neutralised": n, "dupkeys": [],
                "error": f"top level is a {type(obj).__name__}"}
    keys = {str(k): v for k, v in obj.items()}
    return {"state": "JINJA-NEUTRALISED" if n else "DERIVED", "keys": keys,
            "neutralised": n, "dupkeys": dups, "error": ""}


def templates_for_ref(ref: str) -> str:
    """The templates tree at `ref`, or '' when there is no readable ref (e.g. `check --wdl-dir`)."""
    if not ref:
        return ""
    try:
        return _tree_from_ref(ref, TEMPLATE_SUBPATH)
    except CannotCheck as e:
        print(f"  templates NOT READ: {e} -- the optional-bound-upstream finding cannot run, so "
              f"'nothing to report' below is about the WDL only")
        return ""


# Ref to compare a config's bindings against; empty means GSVTK_BRANCH. batch_rerun_step.py sets it
# from GSV_WDL_VERSION, which can differ from GSVTK_BRANCH.
VERIFY_REF = ""


def _verify_ref() -> str:
    return VERIFY_REF or BRANCH


def _posted_ref(tag: str = "") -> str:
    """The ref both the preflight guard and body() use; refuses an `--against` that names a different one.

    `--against` is only meaningful for `check`, which posts nothing. On a posting command, grading one
    ref while posting another would make the guard's pass meaningless.
    """
    ref = _verify_ref()
    against = _flag_value("--against")
    if against and against != ref:
        prefix = f"{tag}: " if tag else ""
        raise SystemExit(
            f"{prefix}refusing -- --against {against} is not the WDL this command runs "
            f"({ref or 'GSVTK_BRANCH unset'}),\n"
            "  and every config it POSTs pins its Dockstore version to that ref. Grading another ref "
            "can only\n  produce a confident pass about a document nothing is about to run, and with "
            f"{DROP_FLAG}\n  it prunes by one ref while posting the other.\n"
            f"  To post {against}'s shape: GSVTK_BRANCH={against} (that pins Dockstore there too).\n"
            f"  To see that ref's findings without posting: check --against {against} {DROP_FLAG}.")
    return ref


# Override for the duplicate-key refusal. It takes the exact key so the log records which binding
# was accepted.
DUP_KEY_FLAG = "--allow-duplicate-key"


def _flag_values(flag: str) -> list:
    """Every value a repeatable flag was given, in command-line order, `--flag=value` included."""
    argv = sys.argv[1:]
    out, i = [], 0
    while i < len(argv):
        a = argv[i]
        if a == flag:
            if i + 1 >= len(argv):
                raise SystemExit(f"{flag} wants a key name, typed out: {flag} <Workflow.key>")
            out.append(argv[i + 1])
            i += 2
            continue
        if a.startswith(flag + "="):
            out.append(a.split("=", 1)[1])
        i += 1
    if any(not v.strip() for v in out):
        raise SystemExit(f"{flag} wants a key name. An empty value names nothing, which is the same "
                         "refusal\n  as the empty `--tag \"$TAG\"` in batch_rerun_step.py: a flag that "
                         "resolves to 'no key\'\n  silently turns the override on for every key.")
    return out


def _posted_for(posted: dict, dupkey: str):
    """What the posted body binds for a template's repeated key: (key, value) or ('', None).

    Matches the exact key first, then the leaf name, since `_dup_key_hook` records nested keys by leaf.
    """
    if dupkey in posted:
        return dupkey, posted[dupkey]
    leaf = dupkey.rsplit(".", 1)[-1]
    for k, v in posted.items():
        if k.rsplit(".", 1)[-1] == leaf:
            return k, v
    return "", None


def classify_dropped_binding(item: dict, posted: dict) -> dict:
    """Classify one repeated template key against the map about to be POSTed.

      `nothing-lost`  every binding has the same value. Disclosed.
      `unbound-here`  the map doesn't bind this key. Disclosed.
      `pinned`        the map binds it to a literal, which settles the disagreement. Disclosed.
      `silent`        the map binds it as a `this.`/`workspace.` read, so which template line won
                      decides what runs. Refused.

    Values are compared as text; `${...}` expressions are not interpreted.
    """
    kept = item.get("kept")
    dropped = item.get("dropped") or []
    if all(str(d) == str(kept) for d in dropped):
        return {"cls": "nothing-lost", "posted_key": "", "posted": None, **item}
    pk, pv = _posted_for(posted, str(item.get("key")))
    if not pk:
        return {"cls": "unbound-here", "posted_key": "", "posted": None, **item}
    kind, _ = module_profile.binding_kind(pv)
    return {"cls": "pinned" if kind == "literal" else "silent", "posted_key": pk, "posted": pv, **item}


def require_no_dropped_bindings(tag: str, workflow: str, posted: dict) -> None:
    """Refuse a POST when the template binds a key twice and the body leaves the choice to document order.

    Runs at the POST boundary rather than in `preflight()`, which `--allow-unknown-inputs` skips.
    An unreadable template is reported as a skip and the POST proceeds.
    """
    ref = _posted_ref(tag)
    # `--tmpl-dir` reads templates from a directory instead of the ref (used by tests).
    tmpl_dir = _flag_value("--tmpl-dir")
    src = f"--tmpl-dir {tmpl_dir}"
    if not tmpl_dir:
        ck = config.get("GATK_SV_CHECKOUT")
        if not ref or not ck or not os.path.isdir(ck):
            print(f"{tag}: dropped-binding check NOT RUN -- need a ref ({ref or 'unset'}) and a "
                  f"checkout ({ck or 'unset'}).\n      Not a pass:  python terra/batch_configs.py "
                  f"check --against <ref>")
            return
        tmpl_dir = templates_for_ref(ref)
        src = f"the templates tree at {ref}"
    path = find_template(tmpl_dir, workflow) if tmpl_dir else ""
    if not path:
        print(f"{tag}: dropped-binding check NOT RUN -- no {workflow}.json.tmpl under "
              f"{tmpl_dir or 'the templates tree'} (read from {src}; templates NOT READ is a different "
              f"state and says so above). Not a pass.")
        return
    read = read_template(path)
    if read["state"] == "CANNOT PARSE":
        print(f"{tag}: dropped-binding check NOT RUN -- {path} refused a strict parse "
              f"({read['error']}).\n      A document this reader cannot open is not a document it can "
              f"clear.")
        return
    dups = read.get("dupkeys") or []
    if not dups:
        print(f"{tag}: dropped-binding check: 0 key(s) bound more than once in "
              f"{os.path.relpath(path, tmpl_dir)} ({src}) -- every binding in the body is the only "
              f"binding upstream states.")
        return
    named = _flag_values(DUP_KEY_FLAG)
    items = [classify_dropped_binding(d, posted) for d in dups]
    fatal = [i for i in items if i["cls"] == "silent" and str(i["key"]) not in named]
    shown = [i for i in items if str(i["key"]) not in named]
    for i in shown:
        if i["cls"] != "silent":
            print(f"{tag}: dropped-binding disclosure {i['key']} bound {i['count']} time(s) in "
                  f"{os.path.relpath(path, tmpl_dir)} -> {i['cls']}, so the POST proceeds "
                  f"({DUP_KEY_FLAG} is not needed for this one)")
    if named:
        unseen = [k for k in named if k not in {str(i["key"]) for i in items}]
        if unseen:
            # An override that matches nothing is probably a typo.
            raise SystemExit(
                f"{tag}: {DUP_KEY_FLAG} names {', '.join(unseen)}, which this template does not bind "
                f"twice.\n  {path} binds: "
                f"{', '.join(str(i['key']) for i in items) or '(nothing repeated)'}.\n"
                "  An override that matches nothing is a typo, and honouring it would let the next real "
                "duplicate\n  through on a decision nobody made about it.")
    if fatal:
        lines = "".join(
            f"    {i['key']}  bound {i['count']}x in {os.path.relpath(path, tmpl_dir)};\n"
            f"      upstream's last line wins: {kept!r} kept, {', '.join(repr(d) for d in i['dropped'])} "
            f"dropped;\n"
            f"      the body POSTs {i['posted_key']} = {i['posted']!r}, a read, so which attribute the "
            f"run\n      resolves is an accident of document order -- and a binding Rawls accepts "
            f"quietly.\n"
            for i in fatal for kept in [i["kept"]])
        raise SystemExit(
            f"{tag}: refusing to POST -- the template upstream binds "
            f"{len(fatal)} key(s) twice and the map still lets `dict()` decide:\n{lines}"
            f"  Which of those attributes was meant is a fact about a document, not a judgement this\n"
            "  tool can make, and a wrong one is invisible: an input bound to nothing still validates,\n"
            f"  the run finishes, and the attribute it was supposed to write is simply not there.\n"
            f"  Fix the map (bind it as a literal, the way `single_sample` pins `mei_bed`), or accept\n"
            f"  this one deliberately:  {DUP_KEY_FLAG} <exact key>   (repeatable)")
    if named:
        print(f"{tag}: dropped-binding check: {len(named)} key(s) accepted by {DUP_KEY_FLAG} "
              f"({', '.join(named)}) -- those bindings are POSTed with a value upstream disagrees with, "
              f"on this command line's authority")


def declared_for_config(name: str, ref: str | None = None):
    """(declared, required) for one config's workflow at `ref`, or None if it can't be read.

    Callers must treat None as unknown, not as "fits".
    """
    ref = ref or _verify_ref()
    if not ref:
        return None
    try:
        return _declared_inputs(_wdl_dir_from_ref(ref), CONFIGS[name]["workflow"])
    except CannotCheck:
        return None


_WFDOC: dict[str, object] = {}      # one miniwdl load per WDL file, shared by BOTH maps


def _load_workflow(wdl_dir: str, workflow: str):
    """Load one workflow with miniwdl (cached per file); used for both inputs and outputs.

    miniwdl rather than a regex because it parses `input {}` blocks reliably and resolves imports.
    Raises CannotCheck for a tree it cannot read.
    """
    try:
        import WDL                                   # the miniwdl *package*, not the CLI
    except ImportError:
        raise SystemExit(
            f"check needs the miniwdl package in the interpreter running this tool (it is "
            f"{sys.executable}):\n"
            "    ./.venv/bin/python terra/batch_configs.py check --against <ref>\n"
            "  or: python -m pip install -r requirements-dev.txt") from None
    f = next((os.path.join(wdl_dir, sub, f"{workflow}.wdl")
              for sub in ("", "wdl") if os.path.isfile(os.path.join(wdl_dir, sub, f"{workflow}.wdl"))),
             None)
    if not f:
        raise CannotCheck(f"wdl/{workflow}.wdl is not in this tree")
    if f in _WFDOC:
        return _WFDOC[f]
    try:
        doc = WDL.load(f, path=[os.path.dirname(f)])
    except Exception as e:                           # a WDL that will not load is the finding
        raise CannotCheck(f"miniwdl could not load it: {type(e).__name__}: {str(e)[:160]}") from None
    wf = getattr(doc, "workflow", None) or getattr(doc, "wf", None)
    if wf is None or wf.name != workflow:
        raise CannotCheck(f"{f} declares no workflow named {workflow}")
    _WFDOC[f] = wf
    return wf


def _declared_inputs(wdl_dir: str, workflow: str):
    """(declared, required) input names for one workflow, from miniwdl -- not from a regex."""
    # `wf.inputs` is None when there is no `input {}` block.
    decls = list(_load_workflow(wdl_dir, workflow).inputs or [])
    # `type.optional` is the flag (miniwdl's Decl has no `.optional`); `expr is None` means no
    # default, so required = declared without `?` and without `= ...`.
    declared = {str(d.name) for d in decls}
    required = {str(d.name) for d in decls if not d.type.optional and d.expr is None}
    return declared, required


def _declared_outputs(wdl_dir: str, workflow: str):
    """(names in declaration order, has_output_block) for the workflow-level outputs.

    Only workflow outputs can be bound by a method config, so task outputs are not collected.
    `has_output_block` distinguishes no `output {}` block (None) from an empty one ([]).
    """
    outs = _load_workflow(wdl_dir, workflow).outputs
    return [str(d.name) for d in (outs or [])], outs is not None


def _workflow_of_config(name: str, spec: dict) -> str:
    """The workflow name from the config's methodRepoMethod.methodPath."""
    path = str((spec.get("methodRepoMethod") or {}).get("methodPath") or "").strip()
    wf = path.replace("%2F", "/").rstrip("/").split("/")[-1].strip()
    if wf:
        return wf
    raise SystemExit(f"{name}: cannot tell which workflow this config runs, so nothing can be compared "
                     f"against it.\n  `check` grades both maps against the declared keys of ONE file, "
                     "and reads that name from\n  methodRepoMethod.methodPath -- the same value "
                     "`dockstore()` above writes, e.g.\n  github.com/broadinstitute/gatk-sv/"
                     "GenotypeBatch. This JSON does not carry it.")


def _read_config_json(path: str) -> dict:
    """One method-config JSON: the body `create` POSTs, or the payload Terra returns for one."""
    try:
        with open(path) as fh:
            spec = json.load(fh)
    except ValueError as e:
        raise SystemExit(f"{path}: not readable JSON ({e}), so its bindings cannot be compared "
                         "to anything.") from None
    if not isinstance(spec, dict):
        raise SystemExit(f"{path}: a JSON {type(spec).__name__}, not one method-config object.")
    if not any(k in spec for k in ("methodRepoMethod", "inputs", "outputs")):
        raise SystemExit(f"{path}: no inputs / outputs / methodRepoMethod at the top level, so this is not "
                         "one config.\n  A manifest keyed by config name (what `create` writes to "
                         f"{DUMP}) is not a config either:\n  pass one config's JSON -- the body "
                         "Terra returns for it, or one you wrote.")
    name = str(spec.get("name") or os.path.splitext(os.path.basename(path))[0])
    return {"name": name, "workflow": _workflow_of_config(name, spec),
            "inputs": spec.get("inputs") or {}, "outputs": spec.get("outputs") or {},
            "origin": path}


def configs_to_check(only: str | None) -> list[dict]:
    """What `check` compares: every config in CONFIGS, one by name, or one config JSON read from disk.

    Each entry is {name, workflow, inputs, outputs, origin}. A name that matches neither CONFIGS nor a
    file exits rather than selecting nothing.
    """
    if not only:
        return [{"name": n, "workflow": s["workflow"], "inputs": s["inputs"],
                 "outputs": s.get("outputs") or {}, "origin": "CONFIGS"}
                for n, s in CONFIGS.items()]
    if only in CONFIGS:
        s = CONFIGS[only]
        return [{"name": only, "workflow": s["workflow"], "inputs": s["inputs"],
                 "outputs": s.get("outputs") or {}, "origin": "CONFIGS"}]
    cands = [only] + ([] if only.endswith(".json") else [only + ".json"])
    for cand in cands:
        if os.path.isfile(cand):
            return [_read_config_json(cand)]
    raise SystemExit(
        f"check: no config named {only!r}, so NOTHING was compared -- and this command does not exit "
        f"0\n  for a comparison that did not run.\n"
        f"  CONFIGS holds:  {', '.join(CONFIGS)}\n"
        "  any other name is read as a method-config JSON (the body `create` POSTs, or one exported\n"
        f"  from your workspace): looked for {', '.join(cands)}")


def binds_no_outputs(config_out) -> bool:
    """True when a config's outputs map binds nothing (absent, None or `{}`).

    Shared by `check` (`NO OUTPUTS BOUND`) and `batch_rerun_step.require_binds_outputs`: Rawls accepts
    such a config and the run succeeds but saves nothing.
    """
    return not dict(config_out or {})


def _classify_outputs(config_out: dict, declared_out: list[str], has_block: bool) -> dict:
    """Classify an outputs map against the workflow's declared outputs. Pure, no printing.

      empty_config    the config binds no outputs; the run succeeds and saves nothing.
      empty_workflow  the workflow declares no outputs, so nothing can be compared. Disclosed, not scored.
      undeclared      bound keys the workflow does not declare. Rawls accepts these at create time and
                      fails at submission without naming the key.
      nested          3-segment keys (`Wf.Call.out`), reported UNCHECKED.
    """
    outs = dict(config_out or {})
    bound, nested = {}, {}
    for k in outs:
        parts = k.split(".")
        if len(parts) > 2:
            nested[k] = ".".join(parts[1:-1])          # full key -> the callee it binds on
        else:
            bound[parts[-1]] = k
    empty_config, empty_workflow = binds_no_outputs(outs), not declared_out
    undeclared = [] if (empty_config or empty_workflow) else \
        sorted((k, outs[k]) for leaf, k in bound.items() if leaf not in declared_out)
    unsaved = [] if empty_workflow else sorted(d for d in declared_out if d not in bound)
    # `empty_workflow` is not counted as a problem; `_report_outputs` still reports it.
    return {"outs": outs, "bound": bound, "nested": nested, "undeclared": undeclared,
            "empty_config": empty_config, "empty_workflow": empty_workflow, "has_block": has_block,
            "unsaved": unsaved,
            "problems": len(undeclared) + (1 if empty_config else 0)}


def _report_template(tmpl_dir: str, workflow: str, tpl: dict, omitted: list, label: str) -> None:
    """Print the template state for one step, plus the findings only the template can give.

    `optional-bound-upstream-but-omitted`: an optional WDL input that upstream's template pins but this
    profile leaves to the WDL default. `DUPLICATE-KEY`: a key the template binds more than once. Both are
    non-fatal; upstream's own templates contain duplicates.
    """
    where = (os.path.relpath(find_template(tmpl_dir, workflow), tmpl_dir)
             if tmpl_dir and find_template(tmpl_dir, workflow) else "")
    if tpl["state"] == "NOT READ":
        print(f"      TEMPLATE NOT READ  {tpl['error']} -- the production binding map was not "
              f"compared, so\n        nothing above is evidence about which inputs production pins.")
        return
    if tpl["state"] == "NO TEMPLATE":
        print(f"      NO TEMPLATE   no {workflow}.json.tmpl under {TEMPLATE_SUBPATH} at {label}.")
        print("        Upstream may bind this workflow elsewhere (test/, single_sample/) or not at all;\n"
              "        either way the optional-omitted finding had no source, so its absence means nothing.")
        return
    extra = (f"; {tpl['neutralised']} value(s) were {{{{ ... }}}} and are read as null, so the KEYS are\n"
             f"        known and those VALUES are not -- rendering is a different, bundle-dependent thing") \
        if tpl["neutralised"] else ""
    if tpl["state"] == "CANNOT PARSE":
        print(f"      TEMPLATE CANNOT PARSE  {where}: {tpl['error']}\n"
              "        after the one substitution, so nothing was derived from it. Not counted as a\n"
              "        problem with this config: it is a finding about that document.")
        return
    print(f"      TEMPLATE {tpl['state']:<16} {where}  -- {len(tpl['keys'])} key(s) bound by "
          f"production at {label}{extra}")
    for d in tpl["dupkeys"]:
        dropped = ", ".join(_tpl_value(v) for v in d["dropped"])
        print(f"      DUPLICATE-KEY  {d['key']} -- bound {d['count']} time(s) in {where}; `json.loads` "
              f"keeps the\n        LAST value, so what production binds is {_tpl_value(d['kept'])} and "
              f"the {len(d['dropped'])}\n        earlier binding(s) ({dropped}) are dropped silently. "
              f"NON-FATAL, and not a defect in THIS\n        map: it is a fact about that document -- "
              f"but the value a submission gets is the one\n        nobody reads first, and `show` prints "
              f"neither side. `checks/terra_entity_check.py`\n        names the same key as `dupkeys=` "
              f"(one rule, two readers: docs/module-profiles.md).")
    for k in omitted:
        v = tpl["keys"][k]
        print(f"      optional-bound-upstream-but-omitted  {k} -- upstream's template binds it"
              + (f" to {v!r}" if v is not None else " (a Jinja value, unread without rendering)")
              + f",\n        {workflow} declares it optional, and this profile binds nothing, so the WDL "
              f"default wins.\n        NON-FATAL, and for this module deliberate: see `_why_unbound` in "
              f"the profile. For a new\n        module it is the case docs/module-profiles.md describes "
              f"-- an optional input nobody\n        asks about is exactly how a default becomes "
              f"production behaviour.")


TEMPLATE_STATES_READ = ("DERIVED", "JINJA-NEUTRALISED")


def _report_dupkey_census(templates: list) -> None:
    """Print one duplicate-key total for the run, distinguishing NOT RUN, NOT READ and a real count."""
    if not templates:
        print("check: template duplicate-key census: NOT RUN -- no config reached its template block, so\n"
              "       no document was asked. Not 0 found.")
        return
    read = [t for t in templates if t["state"] in TEMPLATE_STATES_READ]
    absent = sum(1 for t in templates if t["state"] == "NO TEMPLATE")
    broken = sum(1 for t in templates if t["state"] == "CANNOT PARSE")
    unread = sum(1 for t in templates if t["state"] == "NOT READ")
    if not read:
        print(f"check: template duplicate-key census: NOT READ -- {len(templates)} template block(s) ran "
              f"and\n       none was readable ({absent} absent, {broken} unreadable, {unread} with no "
              f"source to read\n       at). The last-wins question was never asked of any document, so a "
              f"clean exit says nothing\n       about duplicate keys here. Not reported as 0 found.")
        return
    keys = sum(len(t["dupkeys"]) for t in read)
    hits = sum(1 for t in read if t["dupkeys"])
    dropped = sum(len(d["dropped"]) for t in read for d in t["dupkeys"])
    print(f"check: template duplicate-key census: {keys} key(s) bound more than once in {hits} of "
          f"{len(read)}\n       template(s) read ({dropped} binding(s) dropped by last-wins; {absent} "
          f"template(s) absent,\n       {broken} unreadable, {unread} with no source to read at). "
          f"NON-FATAL by design: this\n       command's exit code is about these maps vs the WDL, and a "
          f"duplicate is a fact about\n       upstream's document; each one is named above with BOTH "
          f"values. A duplicate inside a\n       nested object is named by its LEAF, not its path: that "
          f"is all an\n       `object_pairs_hook` can see, and a path here would be a guess.")


def _report_outputs(name: str, workflow: str, declared_out: list[str], g: dict) -> None:
    """Print the verdict per output key, or one line for an empty state (decided by `_classify_outputs`)."""
    if g["empty_config"]:
        print(f"      NO OUTPUTS BOUND   {name} binds 0 output(s); {workflow} declares "
              f"{len(declared_out)}.")
        print("        Neither of the other two states: the WDL is not silent and no key is "
              "misnamed -- there\n        is no key here at all. A config with no `outputs` is LEGAL "
              "to Rawls, so the workflow\n        runs, SUCCEEDS, and every result is discarded with "
              "the scratch bucket: no entity\n        attribute, no workspace key, nothing for the next "
              "step to read. Bind the outputs\n        you mean, or say in the config that discarding "
              "them is the point.")
        return
    if g["empty_workflow"]:
        block = ("the `output {}` block is absent" if not g["has_block"]
                 else "the block is there and declares nothing")
        print(f"      NO OUTPUTS DECLARED  {workflow}.wdl declares no workflow-level output to bind "
              f"({block});\n        this config binds {len(g['outs'])}.")
        print("        NOT counted as a problem -- and NOT a pass either: with nothing declared these "
              "keys are\n        UNVERIFIED, the same standing `CannotCheck` gives an unreadable ref. "
              "One state, one\n        sentence, no per-key findings: renaming a key cannot match a "
              f"workflow that declares\n        nothing, so enumerating them would print a wrong "
              f"remedy {len(g['outs'])} time(s).\n        Two usual causes: the workflow this config "
              "names (methodRepoMethod.methodPath) is\n        not the one that produces these "
              "attributes, or the ref under --against/--wdl-dir\n        predates that `output {}` "
              "block.")
        return
    for key, val in g["outs"].items():                 # config order: what `show` prints, what gets POSTed
        parts = key.split(".")
        if len(parts) > 2:
            print(f"      out UNCHECKED    {key} -> {val}")
            print(f"        binds a CALL inside {parts[1]!r}; a call-level output is legitimate Terra "
                  f"wiring, and\n        grading it needs the sub-workflow's own outputs. "
                  f"{workflow}'s workflow-level\n        outputs are the only thing compared here, so "
                  f"this key is neither passed nor\n        failed. Not counted as a problem.")
            continue
        leaf = parts[-1]
        if leaf in declared_out:
            print(f"      out DECLARED     {key} -> {val}")
            continue
        print(f"      out UNDECLARED   {key} -> {val}")
        print(f"        {workflow} declares {len(declared_out)} workflow-level output(s) and "
              f"{leaf!r} is not one of them:")
        print("          " + ", ".join(declared_out))
        print("        Rawls still answers `invalidOutputs: 0`, so the config CREATES and looks "
              "created. The\n        failure surfaces at SUBMISSION, in a message that does not name "
              "the key -- which is\n        how a wrong map becomes a paid-for run. Either the ref is "
              "not the one this map was\n        written against (point GSVTK_BRANCH at it), or this "
              f"binding is not what the WDL\n        produces: fix it in CONFIGS[{name!r}]['outputs'] "
              "(`show` prints what would be POSTed).")


def check_maps(wdl_dir: str, only: str | None = None, drop: bool = False, ref: str = "",
               tmpl_dir: str = "") -> int:
    """Compare both maps' top-level keys against what the WDL declares, and required inputs against bindings.

    Call-level keys (`Workflow.Call.x`) are named UNCHECKED and not counted. Outputs are compared against
    workflow-level outputs (`_classify_outputs`). With `drop`, branch-only inputs the ref doesn't declare
    are removed first, matching what `_adapt_inputs()` will POST. `ref` is only the label printed. Template
    duplicate keys are reported per step and totalled by `_report_dupkey_census`.
    """
    bad = 0
    ncomp = 0
    tpl_reads: list = []      # one tpl dict per config that reached its template block, for the census
    label = ref or _verify_ref() or "the ref under test"
    targets = configs_to_check(only)
    for tgt in targets:
        name, workflow = tgt["name"], tgt["workflow"]
        try:
            declared, required = _declared_inputs(wdl_dir, workflow)
        except CannotCheck as e:
            bad += 1
            print(f"  BAD {name:<24} CANNOT CHECK: {e}")
            print("        unverified is not verified: neither this config's inputs NOR its outputs "
                  "were compared\n        to anything, so nothing about them is known.")
            continue
        ncomp += 1
        declared_out, has_block = _declared_outputs(wdl_dir, workflow)
        g = _classify_outputs(tgt["outputs"], declared_out, has_block)
        # Nested call bindings are keyed on the full key, since two can share a leaf.
        bound, nested = {}, {}
        for k in tgt["inputs"]:
            parts = k.split(".")
            if len(parts) > 2:
                nested[k] = ".".join(parts[1:-1])          # full key -> the callee it binds on
            else:
                bound[parts[-1]] = k
        # The same rule _adapt_inputs() applies, so the guard and the body cannot disagree.
        dropped = []
        if drop:
            dropped = [key for leaf, key in sorted(bound.items())
                       if leaf not in declared and key in BRANCH_ONLY_INPUTS.get(name, set())]
            for key in dropped:
                bound.pop(key.split(".")[-1], None)
        unknown = [k for k in sorted(bound) if k not in declared]
        unbound = sorted(k for k in required if k not in bound)
        # What upstream's template binds, read at the same ref.
        tpl = read_template(find_template(tmpl_dir, workflow)) if tmpl_dir else \
            {"state": "NOT READ", "keys": {}, "neutralised": 0, "dupkeys": [], "error": "no ref to read at"}
        tpl_reads.append(tpl)
        bound_here = set(tgt["inputs"]) | {f"{workflow}.{k}" for k in bound}
        omitted = sorted(k for k in tpl["keys"]
                         if k.split(".")[-1] in declared and k.split(".")[-1] not in required
                         and k not in bound_here and f"{workflow}.{k.split('.')[-1]}" not in bound_here)
        summary = (f"{len(bound)} bound vs {len(declared)} declared"
                   + (f", {len(nested)} nested-call binding(s) NOT checked" if nested else "")
                   + (f", {len(dropped)} dropped by {DROP_FLAG}" if dropped else "")
                   + f"  |  {len(g['bound'])} out vs {len(declared_out)} declared"
                   + (f", {len(g['unsaved'])} declared but unbound (not saved; not a defect)"
                      if g["unsaved"] else "")
                   + (f"  |  template {tpl['state'].replace(' ', '-').lower()}"
                      f" ({len(tpl['keys'])} key(s) bound upstream)" if tpl["keys"] else
                      f"  |  template {tpl['state'].lower()}")
                   + ("" if tgt["origin"] == "CONFIGS" else f"   [{tgt['origin']}]")
                   )
        print(f"  {'BAD' if unknown or unbound or g['problems'] else 'ok '} {name:<24} {summary}")
        # Listed per key so unchecked bindings are visible; not counted as problems.
        for key in sorted(nested):
            print(f"      UNCHECKED {key} -- binds a call into {nested[key]!r}; comparing that needs "
                  f"the sub-workflow's own declared inputs.")
        for key in dropped:
            print(f"      DROPPED for {label}  {key} -- a known branch-only input that ref does not "
                  f"declare.\n        {DROP_FLAG} is set, so the guard graded the map with it REMOVED "
                  f"-- the same rule\n        `_adapt_inputs()` applies to the body it builds at that "
                  f"ref. SEMANTIC change, not a fix:\n        see the warning `_adapt_inputs()` "
                  f"prints.")
        for k in unknown:
            bad += 1
            key = bound[k]
            known = key in BRANCH_ONLY_INPUTS.get(name, set())
            print(f"      EXTRA  {key}")
            if known:
                print("        a KNOWN branch-only input: declared on the branch under test, absent "
                      "from the ref\n        you checked. This is not a surprise, it is a mismatch "
                      "between the map's snapshot and the ref.")
            print("        Rawls rejects the whole config as an extra input at SUBMISSION, so it "
                  "would sit in\n        the workspace looking created until someone submitted it. "
                  "Point GSVTK_BRANCH at\n        a ref that declares it, or drop the key from "
                  f"CONFIGS[{name!r}]['inputs'].")
        for k in unbound:
            bad += 1
            print(f"      MISSING {workflow}.{k} -- required by the WDL, bound by nothing, "
                  f"no default to fall back on")
        _report_template(tmpl_dir, workflow, tpl, omitted, label)
        _report_outputs(name, workflow, declared_out, g)
        bad += g["problems"]
    _report_dupkey_census(tpl_reads)
    if bad:
        print(f"check: {bad} problem(s) across {ncomp} config(s) compared. Either the ref is not the "
              f"one these maps\nwere written against or the maps are stale. `validate` asks Terra the "
              f"same question but needs\nthe ref published on\nDockstore; this needs only your "
              f"checkout.")
        return 1
    print(f"check: clean -- {ncomp} config(s) compared; every bound input key is declared, nothing "
          f"the\nWDL requires is unbound, and every output key is a workflow-level output of the WDL "
          f"that\nwould run. A workflow that declares none at all is disclosed above as UNVERIFIED "
          f"and\nis not scored, because those keys had nothing to be compared against.")
    return 0


def cmd_check() -> int:
    require_module("check")
    ref = _flag_value("--against") or BRANCH
    wd = _flag_value("--wdl-dir")
    if not wd:
        if not ref:
            raise SystemExit("check needs a ref: --against <ref|branch|sha>, or GSVTK_BRANCH.\n"
                             "  --wdl-dir <dir>/wdl also works, and is the way to check a dirty tree.")
        try:
            wd = _wdl_dir_from_ref(ref)
        except CannotCheck as e:
            raise SystemExit(f"cannot read the WDL at {ref}: {e}") from None
        print(f"WDL read from {config.get('GATK_SV_CHECKOUT')} @ {ref}")
    # --tmpl-dir is the template counterpart of --wdl-dir.
    td = _flag_value("--tmpl-dir") or ("" if _flag_value("--wdl-dir") else templates_for_ref(ref))
    return check_maps(wd, only=_flag_value("--config"), drop=DROP_FLAG in sys.argv, ref=ref,
                      tmpl_dir=td)


def preflight(tag: str) -> None:
    """The same comparison, before anything that POSTs or burns a validation round-trip."""
    # Resolved before the override so a conflicting --against is still refused.
    ref = _posted_ref(tag)
    if "--allow-unknown-inputs" in sys.argv:
        print(f"{tag}: OVERRIDE --allow-unknown-inputs taken: NEITHER the input map NOR the output map "
              f"is\n      compared to the WDL. A config\n      Terra rejects as an extra input will still "
              f"be created, and so will an output key the\n      workflow does not declare -- which is the "
              f"one that costs money, because Rawls\n      reports invalidOutputs: 0 for it at create time "
              f"and names no key at submission.")
        return
    ck = config.get("GATK_SV_CHECKOUT")
    if not ref or not ck or not os.path.isdir(ck):
        print(f"{tag}: pre-check SKIPPED -- need a ref ({ref or 'unset'}) and a checkout "
              f"({ck or 'unset'}).\n      Not a pass:  python terra/batch_configs.py check "
              f"--against <ref>")
        return
    # For a rerun the ref is GSV_WDL_VERSION, which may differ from GSVTK_BRANCH.
    extra = "" if ref == BRANCH else f" (GSVTK_BRANCH is {BRANCH or 'unset'})"
    print(f"{tag}: comparing every binding against the gatk-sv WDL at {ref}{extra}")
    try:
        wd = _wdl_dir_from_ref(ref)
    except CannotCheck as e:
        raise SystemExit(f"{tag}: refusing to continue -- cannot read the WDL at {ref}: {e}.\n"
                         f"  Unverified is not verified. Fix the ref/checkout, or say you mean it "
                         f"with --allow-unknown-inputs.") from None
    if check_maps(wd, drop=DROP_FLAG in sys.argv, ref=ref, tmpl_dir=templates_for_ref(ref)):
        raise SystemExit(f"{tag}: refusing to continue -- these maps do not fit the WDL at {ref}.\n"
                         f"  Fix the ref, fix the map, or say you mean it with --allow-unknown-inputs.")


def create():
    require_module("create")
    preflight("create")                    # offline: a config Terra will reject must not be POSTed
    print(call_cache_line())               # the mode that is about to be POSTed, not the default
    # Build and check every body before POSTing any, so a refusal can't leave a partial set created.
    built = []
    for name in CONFIGS:
        b = body(name)
        # Look the template up by the profile's workflow name, as `check` does (not the Dockstore name).
        require_no_dropped_bindings(f"create {name}", str(CONFIGS[name]["workflow"]), b["inputs"])
        built.append((name, b))
    out = {}
    for name, b in built:
        r = fapi.create_workspace_config(NS, WS, b)
        if r.status_code == 409:
            r = fapi.overwrite_workspace_config(NS, WS, NS, name, b)
        ok = r.status_code in (200, 201)
        print(f"{name}: create HTTP {r.status_code}{'' if ok else ' ' + r.text[:300]}")
        out[name] = r.json() if ok else {"error": r.text}
    config.work_dir("manifests")        # the write makes its own directory
    json.dump(out, open(DUMP, "w"), indent=1)
    print("->", DUMP)
    if not all(v.get("name") for v in out.values()):
        raise SystemExit(1)


def validate():
    """Run Terra's config validation for every config.

    The response has extraInputs / invalidInputs / invalidOutputs / missingInputs / validInputs;
    there is no boolean 'valid' key.
    """
    require_module("validate")
    preflight("validate")                  # free + offline first; this step costs a round-trip each
    bad = 0
    for name in CONFIGS:
        r = fapi.validate_config(NS, WS, NS, name)
        if r.status_code != 200:
            print(f"{name}: HTTP {r.status_code} {r.text[:200]}")
            bad += 1
            continue
        d = r.json()
        extra = d.get("extraInputs", [])
        missing = [m for m in d.get("missingInputs", [])]
        inval = d.get("invalidInputs", {}) or {}
        inval_o = d.get("invalidOutputs", {}) or {}
        nvalid = len(d.get("validInputs", []) or [])
        flag = "OK " if not (extra or inval or inval_o) else "BAD"
        # missingInputs is only fatal for inputs the WDL has no default for; the branch WDLs
        # default the rest, so list it rather than fail on it.
        print(f"{flag} {name}: bound={nvalid} extra={extra} invalidIn={inval} invalidOut={inval_o}")
        if missing:
            print(f"      missingInputs({len(missing)}): {', '.join(missing)[:400]}")
        if flag == "BAD":
            bad += 1
    if bad:
        raise SystemExit(f"{bad} configs failed validation")


# Flags that consume the next argument, so their values aren't mistaken for modes.
VALUE_FLAGS = ("--against", "--wdl-dir", "--tmpl-dir", "--config", "--allow-duplicate-key")


def positional(argv: list[str]) -> list[str]:
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in VALUE_FLAGS:
            skip = True
            continue
        if a.startswith("-"):
            continue
        out.append(a)
    return out


# ---------------------------------------------------------------------------------------------
# --selftest: the output-map cases, on fixtures built in a temp dir.
#
# Offline: every case passes --wdl-dir, so no ref, checkout, identity or network is needed. The
# fixtures are a control WDL with two declared outputs, one with no `output {}` block, and one with
# an empty block.
SELFTEST_WDL = {
    "Tiny.wdl": (
        "version 1.1\n"
        "workflow Tiny {\n"
        "  input {\n    String batch\n  }\n"
        "  call tiny_task\n"
        "  output {\n    File out_one = tiny_task.one\n    File out_two = tiny_task.two\n  }\n"
        "}\n"
        'task tiny_task {\n  command <<< echo x > one.txt && echo y > two.txt >>>\n'
        '  output { File one = "one.txt"\n           File two = "two.txt" }\n'
        '  runtime { docker: "x" }\n}\n'),
    "TinyNoOutputBlock.wdl": (
        "version 1.1\n"
        "workflow TinyNoOutputBlock {\n"
        "  input {\n    String batch\n  }\n"
        "  call tiny_task\n"
        "}\n"
        'task tiny_task {\n  command <<< echo x > one.txt >>>\n'
        '  output { File one = "one.txt" }\n  runtime { docker: "x" }\n}\n'),
    "TinyEmptyOutputBlock.wdl": (
        "version 1.1\n"
        "workflow TinyEmptyOutputBlock {\n"
        "  input {\n    String batch\n  }\n"
        "  call tiny_task\n"
        "  output {\n  }\n"
        "}\n"
        'task tiny_task {\n  command <<< echo x > one.txt >>>\n'
        '  output { File one = "one.txt" }\n  runtime { docker: "x" }\n}\n'),
}


def _selftest_config(dirpath: str, fname: str, workflow: str, outputs: dict) -> str:
    """Write one method-config JSON fixture, using `dockstore()` like `create` does."""
    p = os.path.join(dirpath, fname)
    with open(p, "w") as fh:
        json.dump({"namespace": "selftest", "name": os.path.splitext(fname)[0],
                   "rootEntityType": "sample_set", "methodRepoMethod": dockstore(workflow),
                   "inputs": {f"{workflow}.batch": "this.sample_set_id"},
                   "outputs": outputs}, fh, indent=1)
    return p


def _run_check(*argv: str):
    """Run `check` in-process with the given argv; returns (exit code, stdout+stderr)."""
    import contextlib
    import io
    out = io.StringIO()
    saved = list(sys.argv)
    sys.argv = ["batch_configs.py", "check"] + list(argv)
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = cmd_check()
            except SystemExit as e:
                code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
                if isinstance(e.code, str):
                    out.write(e.code + "\n")
    finally:
        sys.argv = saved
    return code, out.getvalue()


def selftest() -> int:
    """Each output state must fire, the control must stay silent, and no two states may print alike."""
    tmp = tempfile.mkdtemp(prefix="gsvtk-batch-configs-selftest-")
    atexit.register(shutil.rmtree, tmp, True)
    global BRANCH
    BRANCH = BRANCH or "selftest-ref"        # `dockstore()` refuses without a version; no profile needed
    for name, text in SELFTEST_WDL.items():
        with open(os.path.join(tmp, name), "w") as fh:
            fh.write(text)
    cfg = {
        # the control: both keys declared, and the workflow's one required input bound
        "ok": _selftest_config(tmp, "tiny-correct.json", "Tiny",
                              {"Tiny.out_one": "this.tiny_one_new",
                               "Tiny.out_two": "this.tiny_two_new"}),
        # one key the workflow does not declare
        "wrong": _selftest_config(tmp, "tiny-wrong-name.json", "Tiny",
                                 {"Tiny.out_one": "this.tiny_one_new",
                                  "Tiny.out_three": "this.tiny_three_new"}),
        # the config binds nothing
        "nobinding": _selftest_config(tmp, "tiny-empty-outputs.json", "Tiny", {}),
        # the workflow declares no outputs: block absent vs empty. Not scored.
        "wfsilent": _selftest_config(tmp, "tiny-wf-no-output-block.json", "TinyNoOutputBlock",
                                     {"TinyNoOutputBlock.out_one": "this.tiny_one_new"}),
        "wfempty": _selftest_config(tmp, "tiny-wf-empty-output-block.json", "TinyEmptyOutputBlock",
                                    {"TinyEmptyOutputBlock.out_one": "this.tiny_one_new"}),
        # a call-level output binding: reported, not compared, and NOT a failure
        "nested": _selftest_config(tmp, "tiny-nested-output.json", "Tiny",
                                  {"Tiny.tiny_task.one": "this.tiny_nested_new"}),
    }
    wd = ["--wdl-dir", tmp]
    bad = []

    def case(tag, want_rc, must, must_not, label):
        rc, txt = _run_check("--config", cfg[tag], *wd)
        hit = [n for n in must if n not in txt]
        miss = [n for n in must_not if n in txt]
        good = rc == want_rc and not hit and not miss
        print(f"  {'ok  ' if good else 'FAIL'}  {label} (exit {rc}, want {want_rc})")
        for n in hit:
            print(f"        the output never says {n!r}")
        for n in miss:
            print(f"        the output says {n!r}, which belongs to a DIFFERENT case")
        if not good:
            bad.append(f"{label}: exit {rc} (want {want_rc})"
                       + (f", missing {hit}" if hit else "") + (f", leaked {miss}" if miss else ""))
            print("        " + "\n        ".join(txt.splitlines()[-14:]))
        return rc, txt

    print(f"selftest: outputs map vs declared outputs (interpreter: {sys.executable})")
    # 1. The control must pass, or the other cases prove nothing.
    case("ok", 0, ["out DECLARED     Tiny.out_one -> this.tiny_one_new",
                   "out DECLARED     Tiny.out_two -> this.tiny_two_new",
                   "1 config(s) compared"],
         ["UNDECLARED", "NO OUTPUTS"],
         "control: a config whose two keys the workflow declares passes, naming both")
    # 2. an undeclared key is named, with the declared names beside it
    case("wrong", 1, ["out UNDECLARED   Tiny.out_three", "'out_three' is not one of them",
                      "out_one, out_two", "invalidOutputs: 0"],
         ["NO OUTPUTS BOUND", "NO OUTPUTS DECLARED", "check: clean"],
         "a key the workflow does not declare fails, names the key, and lists what IS declared")
    # 3. a config with no outputs
    case("nobinding", 1, ["NO OUTPUTS BOUND", "binds 0 output(s); Tiny declares 2"],
         ["out UNDECLARED", "NO OUTPUTS DECLARED", "check: clean"],
         "a config with no outputs fails as NO OUTPUTS BOUND, not as a naming problem")
    # 4. a workflow with no outputs (two shapes): its own state, no per-key findings, not scored
    case("wfsilent", 0, ["NO OUTPUTS DECLARED", "the `output {}` block is absent", "UNVERIFIED",
                        "check: clean -- 1 config(s) compared"],
         ["out UNDECLARED", "out DECLARED", "NO OUTPUTS BOUND"],
         "a workflow with no output block: its own state, no per-key findings, not scored as a finding")
    case("wfempty", 0, ["NO OUTPUTS DECLARED", "the block is there and declares nothing", "UNVERIFIED"],
         ["out UNDECLARED", "out DECLARED", "NO OUTPUTS BOUND",
          "the `output {}` block is absent"],
         "an empty output block is told from an absent one, and from a wrong key")
    # 5. a call-level output: disclosed, not compared, not a failure
    case("nested", 0, ["out UNCHECKED    Tiny.tiny_task.one", "Not counted as a problem"],
         ["out UNDECLARED"],
         "a Workflow.Call.out binding is reported UNCHECKED and does not fail the check")

    # 6. --config: shipped names resolve, a JSON path is read, anything else is refused.
    five = configs_to_check(None)
    one = configs_to_check("10-GenotypeBatch")
    keep = [n for n in CONFIGS if any(t["name"] == n for t in five)]
    good = len(five) == len(CONFIGS) == len(keep) and len(one) == 1 \
        and one[0]["workflow"] == "GenotypeBatch" and one[0]["outputs"] == CONFIGS["10-GenotypeBatch"]["outputs"]
    print(f"  {'ok  ' if good else 'FAIL'}  --config still selects the five shipped maps "
          f"({len(five)} with no --config, {len(one)} for 10-GenotypeBatch)")
    if not good:
        bad.append(f"the five shipped configs no longer enumerate the same way: {len(five)}/"
                   f"{len(CONFIGS)}, single={len(one)}")
    rc, txt = _run_check("--config", "no-such-config-anywhere", *wd)
    good = rc == 1 and "no config named" in txt and "10-GenotypeBatch" in txt and "check: clean" not in txt
    print(f"  {'ok  ' if good else 'FAIL'}  --config on a name that is neither a map nor a file "
          f"refuses (exit {rc}, want 1)")
    if not good:
        bad.append(f"an unresolvable --config name was not refused outright: exit {rc}, {txt[:160]!r}")
        print("        " + "\n        ".join(txt.splitlines()[-8:]))
    # 7. fixture workflows are not in CONFIGS, so the passes above came from the JSON path.
    good = "Tiny" not in {s["workflow"] for s in CONFIGS.values()}
    print(f"  {'ok  ' if good else 'FAIL'}  every fixture workflow is outside CONFIGS, so the JSON "
          f"path really was exercised")
    if not good:
        bad.append("a fixture workflow collides with a shipped config's workflow: the JSON path "
                   "was not proven")
    if bad:
        print("selftest: FAIL\n  " + "\n  ".join(bad))
        return 1
    print(f"selftest: ok -- {len(SELFTEST_WDL)} WDL fixtures, {len(cfg)} config fixtures: the "
          f"undeclared key\nand the empty config each fail in their own words; the empty WORKFLOW is "
          f"disclosed as its own\nUNVERIFIED state and never enumerated as findings; the control "
          f"passes; and a --config name\nthat resolves to nothing is a refusal, not a clean report.")
    return 0


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        usage()
    if "--selftest" in sys.argv:
        # Before mode dispatch, so it never falls through to `show`.
        raise SystemExit(selftest())
    args = positional(sys.argv[1:])
    if len(args) > 1:
        raise SystemExit(f"one mode at a time; got {' '.join(args)!r}   (--help)")
    mode = args[0] if args else "show"
    if mode not in ("show", "check", "create", "validate"):
        usage(2)                      # a typo must be a usage error, not a traceback
    if mode == "check":
        # Before require_target: check is offline and needs no workspace.
        raise SystemExit(cmd_check())
    if mode == "create" and "--confirm" not in sys.argv:
        raise SystemExit(
            "create POSTs (and overwrites) method configs in your workspace: a config with a\n"
            "  wrong binding is worse than no config, because the next submission will use it.\n"
            "  review `show` first, then re-run with --confirm.")
    if mode in ("create", "validate"):
        require_target(writes=(mode == "create"))
    {"show": show, "create": create, "validate": validate}[mode]()


def usage(code=0):
    print("""usage: batch_configs.py [show|check|create|validate|--selftest]

  show      print every input/output map (offline, no auth; needs GSVTK_BRANCH
              because the branch is part of every Dockstore URI it prints)
  check     compare every bound key of BOTH maps against the WDL at a ref, offline, from
              your own checkout (miniwdl). --against <ref|branch|sha> (default
              GSVTK_BRANCH), --wdl-dir <dir> for a dirty tree, --config <name> for one
              config. Outputs are compared against the workflow's WORKFLOW-LEVEL outputs,
              and the three states are reported apart: a key the workflow does not declare
              (out UNDECLARED, nonzero -- Rawls says invalidOutputs:0 for these at create
              time and names no key at submission), a config binding no outputs (NO OUTPUTS
              BOUND, nonzero: legal to Rawls, so the run succeeds and writes nothing back),
              and a workflow declaring none to bind (NO OUTPUTS DECLARED: disclosed, not
              scored -- those keys are unverified, not wrong, and are not enumerated as
              findings). 3-segment keys are call-level wiring: disclosed UNCHECKED, not
              compared, not a finding.
              --tmpl-dir <dir> reads the production `.json.tmpl` files from a directory instead of
              `git archive`-ing inputs/templates at the ref -- the template side of --wdl-dir, for a
              dirty tree and for fixtures. Without either, no template is read and the command says
              TEMPLATE NOT READ per step rather than implying it compared production's bindings.
              --config takes any of the five shipped names OR a path to a method-config
              JSON (the body create POSTs / Terra returns); which workflow to read comes
              from that config's own methodRepoMethod. A name that is neither is a refusal
              -- it used to compare nothing and report itself clean.
              `create` and `validate` run this first; --allow-unknown-inputs says you
              mean to post a map that does not fit the ref (it prints that it did, for both
              maps). --against belongs to THIS command only: a command that POSTs grades
              the ref its own config runs (GSVTK_BRANCH, or GSV_WDL_VERSION on a
              rerun), because that is the WDL Terra will actually read.
  create    POST the configs into GSVTK_TERRA_NAMESPACE/GSVTK_TERRA_WORKSPACE
              (requires --confirm: it overwrites configs a submission will read;
               refuses the shared baseline workspace unless --allow-shared-target)
  validate  ask Terra to typecheck each config against its Dockstore WDL
  --allow-duplicate-key <Workflow.key>  (create; repeatable) accept, by NAME, one key that upstream's
              template binds more than once. `create` refuses a POST when the template it reads binds a
              key twice AND the map still carries a `this.`/`workspace.` read for it: which attribute the
              run resolves is then decided by document order, and a binding like that validates without
              complaint and writes back nothing. Keys where every binding agrees, where the map binds
              nothing, or where the profile pins a literal are DISCLOSED and proceed (that is how
              `single_sample` carries `mei_bed`); a name that matches no duplicate in that template is
              itself a refusal, because honouring a typo would clear the next real one. Unreadable
              template/no ref is a stated NOT RUN that proceeds — not a pass, and no flag silences it.
  --selftest  the three output states against WDL + config fixtures built in a temp dir:
              each must fire, the control must not, and no two states may print alike.
              Offline, no profile, no credentials (--wdl-dir, so no ref is resolved).

  --call-cache / --no-call-cache  whether Cromwell may reuse call outputs. Off unless asked
              for: a cross-workspace cache copy has timed out mid-run and failed a submission
              and two-arm A/Bs want each arm to actually run (docs/terra-head-to-head.md). Both flags together is a refusal, not a tie-break.

The branch under test is GSVTK_BRANCH; attribute suffixes are GSVTK_FROZEN_SUFFIX /
GSVTK_NEW_SUFFIX and must match what batch_freeze.py and batch_fetch_compare.sh use.""",
          file=sys.stderr if code else sys.stdout)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
