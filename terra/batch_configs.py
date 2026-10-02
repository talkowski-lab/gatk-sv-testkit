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

The input maps are a SNAPSHOT of one branch's WDL signature, while GSVTK_BRANCH only chooses the
Dockstore URL. Point the URL at a ref the maps were not written against and they carry keys that ref
never declared -- which Rawls rejects as an extra input at submission. That and a `Cannot get
dockstore://... from method repo` 404 are the same defect seen twice: one map, two refs. `check` is
the offline form of `validate` (it reads your own checkout with miniwdl, so it does not need the ref
published on Dockstore), and `create`/`validate` run it before doing anything else.

The `outputs` map drifts the same way, and used to be checked less. Rawls answers `invalidOutputs: 0`
for an output name the workflow never declares, so a wrong map CREATES, sits in the workspace looking
created, and dies at SUBMISSION in a message that names no key -- one measured review put that at
$18.87 and 20 wall-clock hours. `check` now compares BOTH maps against one WDL load, and an output key
the workflow does not declare is a nonzero exit there rather than a purchase. Three states that used
to be conflated get three reports: a key the workflow does not declare, a workflow that declares no
workflow-level outputs to bind, and a config that binds no outputs at all.
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

# The workspace is where results are written; the branch is what is under test.
# Both are stated, never guessed -- see docs/config.md.
# Read leniently at import so `show` and `--help` work with an empty profile; the
# commands that actually talk to Terra call require_target() and get the written fix.
NS = config.get("TERRA_NAMESPACE")
WS = config.get("TERRA_WORKSPACE")
BRANCH = config.get("BRANCH")


def require_target(writes=False):
    """Resolve (NS, WS, BRANCH) or exit 4 naming the profile key that is missing.

    `writes=True` also refuses the shared baseline workspace (see
    terra.assert_writable_target). An unset target used to reach the HTTP layer as an empty path
    segment -- `POST /api/workspaces//methodconfigs` -- which is a 405 from the edge instead of a
    clear message from this tool, and which really happened to a reviewer probing this code path.
    """
    global NS, WS, BRANCH
    NS = NS or config.require("TERRA_NAMESPACE", "where these configs are POSTed")
    WS = WS or config.require("TERRA_WORKSPACE", "where these configs are POSTed")
    BRANCH = BRANCH or config.require("BRANCH", "the gatk-sv branch whose WDL each config runs")
    if writes:
        terra.assert_writable_target(NS, WS, "create or overwrite method configs",
                                    allow="--allow-shared-target" in sys.argv)
    return NS, WS, BRANCH

# Attribute suffixes are read by the profile loader (`kit/module_profile.py`), which is the one place
# that turns a profile's `{frz}`/`{new}` tokens into `_frz`/`_new`; this file used to interpolate them
# into 100 binding strings by hand, which is the same two config keys parsed in two places.
# A path, not a directory: `show` and `--help` import this module and must create nothing.
# create() makes the directory when it actually writes.
DUMP = str(config.work_path("manifests") / "batch_configs.json")

# The three tables below are DATA, and the data now lives in `profiles/<MODULE>.json`
# (`GSVTK_MODULE`, `GSVTK_MODULE_DIR`; docs/module-profiles.md §3). What used to sit here was 64 input
# and 36 output bindings, the caller list, the branch-only key set, and a 12-line rationale for one of
# those keys -- all of it a hand-copy of upstream facts, which is what §1 calls the cost of
# transcription. That cost is paid once now, in the profile, and the rationale travels with the key it
# explains as a `_why_*` sibling instead of being stranded here as a comment.
#
# `BRANCH_ONLY_INPUTS` keeps the shape this file has always offered: a dict keyed by config name whose
# values are sets of FULL binding keys ("GenotypeBatch.training_vcf"), so `check` can still distinguish
# "known branch-only input" from "this key is news to us", and `--drop-branch-only-inputs` still finds
# the same keys. The rationale for the one key this module carries is in the profile beside it.
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
# Input maps follow inputs/templates/terra_workspaces/cohort_mode/workflow_configurations/* on the
# branch, with frozen inputs read from the `{frz}` suffix and chain outputs from the `{new}` one --
# both suffix tokens expand from GSVTK_FROZEN_SUFFIX / GSVTK_NEW_SUFFIX, in ONE place
# (`kit/module_profile.py`), and `@` fans out over the profile's caller list. The profile lists the
# keys upstream produced nothing for (dragen/melt in this cohort) rather than binding them.
#
# Why these are still plain dicts at module level, populated here rather than fetched per call:
# five probes and `terra/batch_rerun_step.py` read `CONFIGS` as an attribute, and `docs/module-
# profiles.md` §7's zero-config rule says `--help` must not depend on repo data. So the load happens
# once, through a loader that never raises and never creates a file, and a profile that cannot be used
# shows up as EMPTY tables plus a `PROFILE` full of findings -- which the commands that need a table
# report with `require_module()` (exit 4, naming the file and every field). An empty `CONFIGS` is
# therefore never a pass: `show` prints nothing, `check` refuses 0 configs, and `body()` refuses.
PROFILE = module_profile.load()
CONFIGS: dict = PROFILE.configs
CALLERS: list = PROFILE.callers
BRANCH_ONLY_INPUTS.update(PROFILE.branch_only_inputs)


def require_module(tag: str = "") -> None:
    """Refuse, by name and in full, when this run has no usable module profile.

    Called by the commands that build or compare a config, never at import: `--help` and `--selftest`
    answer with zero configuration, and `helpsweep` proves it. An unusable profile exits 4 naming the
    file, what is wrong with it, and every field a working profile carries -- not a subset, because the
    first error text here named 5 of the fields a profile needed and following it produced a file that
    could not drive a chain (docs/module-profiles.md §6).
    """
    PROFILE.require(tag or "batch_configs")


# `--drop-branch-only-inputs`: build a ref-shaped config by removing bindings the ref does not
# declare, INSTEAD of failing. Deliberately opt-in, and deliberate about being loud:
#
#   Dropping `GenotypeBatch.training_vcf` is not cosmetic. On the branch, GenotypeBatch is handed
#   this batch's own filtered PESR VCF as its PE/SR training sites; on main it trains from `vcf`
#   itself. Both are coherent pipelines -- they are different pipelines. A tool that quietly picked
#   one would produce a green run of the other one, which is the failure this repo names in
#   batch_rerun_step's own docstring.
#
#   So the flag is only honoured when the ref can actually be READ (a checkout + a ref that resolves
#   + miniwdl). Unverifiable means we do not drop, because "I could not check" is not evidence that
#   the key is absent. And it prints to stderr, so `batch_rerun_step.py show | jq` stays valid JSON.
DROP_FLAG = "--drop-branch-only-inputs"


def _adapt_inputs(name: str, spec: dict, inputs: dict) -> dict:
    """Remove known-branch-only bindings the target ref does not declare. Prints what it dropped."""
    known = BRANCH_ONLY_INPUTS.get(name, set())
    candidates = {k: v for k, v in inputs.items() if k in known}
    if not candidates or DROP_FLAG not in sys.argv:
        return inputs
    # The SAME ref preflight() grades, from one place, so the guard and the body cannot be pointed at
    # two documents. _posted_ref() refuses rather than guessing when --against names a third one, and
    # it belongs here as much as in preflight(): by this point the message the user is reading already
    # names the key it is removing.
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
# The call cache is now something you ASK FOR. This builder shipped `"useCallCache": True` with no
# way to say otherwise, and two measurements -- taken on two different branches, for two different
# reasons -- both point the other way:
#
#   A single-sample submission went out with useCallCache=true and died mid-run on a CROSS-WORKSPACE
#   cache copy: `TimeoutException ... waiting to copy gs://<another-workspace-bucket>/
#   all_samples.RD.txt.gz`, submission Failed. The re-run used false and had zero failures, and the
#   handoff records it as a durable fact: "Terra single-sample runs need useCallCache=false"
#   (docs/handoff/003-single-sample-blockers-pr966.md, corrections #5, gotcha #4, and its durable-facts list).
#
#   docs/terra-head-to-head.md §5 tells a two-arm A/B that `useCallCache: true` is "not what you want
#   between arms if a tag is mutable" -- advice the shipped tool made impossible to follow, so both
#   arms of the manta tloc A/B had to set it False by hand (GAP-REVIEW-manta-tloc.md §3.2).
#
# So the default is False and caching is opted into by name. `show` prints which mode it built, and
# batch_rerun_step.py states its own choice instead of inheriting this one.
CALL_CACHE_FLAG = "--call-cache"
NO_CALL_CACHE_FLAG = "--no-call-cache"


def call_cache(tag: str = "") -> bool:
    """The call-cache choice for this command line, or a refusal. Never a silent inheritance.

    Both flags is a contradiction, not last-one-wins: which arm of an A/B gets the cache must not be
    decided by argv order.
    """
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
            "                    submission (docs/handoff/003-single-sample-blockers-pr966.md).\n"
            f"    {NO_CALL_CACHE_FLAG}   every call re-runs. The default, and what both arms of a\n"
            "                    two-arm comparison want.")
    return on


def body(name: str) -> dict:
    require_module("body")
    spec = CONFIGS[name]
    # dict(): spec["inputs"] IS the module-level table. Pruning without copying would delete the key
    # from CONFIGS for the rest of the process -- so `show` after one adapted `create` would report a
    # 16-input map even when run without the flag, and the branch-only table would no longer match
    # the map it sits beside.
    inputs = _adapt_inputs(name, spec, dict(spec["inputs"]))
    return {"namespace": NS, "name": name, "rootEntityType": spec["rootEntityType"],
            # `wdl`, not `workflow`: the Dockstore path is the WDL FILE basename, and the declared
            # workflow name is a separate fact (they differ for 12 of the 109 workflow-bearing WDLs at
            # main -- DepthClustering/ClusterDepth, Genotype_2/Regenotype). docs/module-profiles.md §3.
            "methodRepoMethod": dockstore(spec["wdl"]),
            # Rawls rejects the body without it (400 "missing required member
            # 'methodConfigVersion'"); the server bumps it on every overwrite.
            "methodConfigVersion": 1,
            "deleted": False,  # also required by Rawls (400 "missing required member 'deleted'")
            "inputs": inputs, "outputs": spec["outputs"],
            "prerequisites": {}, "deleteIntermediateOutputFiles": False,
            # Default False; --call-cache opts in. See CALL_CACHE_FLAG for the two measurements that
            # retired the old hardcoded True, including the rerun step that imported this builder and
            # so inherited a cache it never chose.
            "useCallCache": call_cache(), "maxMessageSize": None}


def call_cache_line() -> str:
    """What `show` prints, because `show`'s promise is that it is what gets POSTed."""
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
    """Materialize <checkout>/wdl at <ref> into a temp dir. Read-only by construction.

    `git archive` only: it cannot touch the checkout's working tree, index or HEAD -- the same
    discipline scripts/fetch_wdl.py keeps, for the same reason (docs/static-checks.md).

    Raises CannotCheck (not SystemExit) because body() consults it while building one config, and a
    ref that cannot be read must make THAT config unverifiable rather than kill the whole run. Cached
    per ref: `create` asks for five configs and would otherwise run five `git archive`s.
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
    """This one config could not be compared. Reported as a finding, never as a pass -- a checker
    that skips a workflow and still says 'clean' is the failure mode this repo keeps meeting."""


# ------------------------------------------------------------------ template read (§9 step 3)
# Where production says what it binds. `wdl/*.wdl` answers what an input IS; the `.json.tmpl` answers
# which attribute or literal FEEDS it, and nothing else in the repo holds that wiring: 196 of the 473
# bindings across the cohort templates point at an attribute whose leaf name differs from the input
# name (`median_coverage<-this.median_cov`, `rd_file<-this.merged_bincov`).
TEMPLATE_SUBPATH = "inputs/templates"
TEMPLATE_PREFERRED = "terra_workspaces/cohort_mode/workflow_configurations"

# The one substitution §4 of docs/module-profiles.md measured: 28 of 28 templates parse with `json.loads`
# once the Jinja braces are neutralised to `null`. Rendered, they would need `jinja2` AND upstream's
# `inputs/values/` bundle (their renderer skips a whole template when a referenced value is undefined and
# defaults `ref_panel -> ref_panel_empty`), which would answer "what did that values profile bind" rather
# than "what does this ref bind". So: neutralise, parse strictly, never render.
JINJA_RE = re.compile(r"\{\{.*?\}\}", re.S)

_TREES: dict = {}     # (ref, subpath) -> the unpacked directory, so one `git archive` per pair


def _tree_from_ref(ref: str, subpath: str) -> str:
    """Materialize one subdirectory of <checkout> at <ref> into a temp dir. Read-only by construction.

    `_wdl_dir_from_ref()` is this with subpath `wdl`; the templates need the same discipline, because
    the only difference between reading a ref and disturbing someone's checkout is `git archive`. It
    cannot touch the working tree, the index or HEAD, which is the rule `scripts/fetch_wdl.py` keeps.
    """
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
    """The `.json.tmpl` for one workflow, preferring the cohort deployment.

    Upstream keeps several templates per workflow name (`cohort_mode/workflow_configurations/`,
    `test/<W>/`, `single_sample/`), and the cohort one is what the chain in this repo runs. Preferring
    it is a lookup rule, not a guess: the path is printed with the verdict, so "which file did you
    read" is answerable from the output rather than from this function's source.
    """
    if not tmpl_dir:
        return ""
    want = f"{workflow}.json.tmpl"
    hits = sorted(p for p in Path(tmpl_dir).rglob(want))
    if not hits:
        return ""
    pref = [p for p in hits if TEMPLATE_PREFERRED in str(p.relative_to(tmpl_dir)).replace(os.sep, "/")]
    return str((pref or hits)[0])


def read_template(path: str) -> dict:
    """One template read STRUCTURALLY: {state, keys, neutralised, error}.

    `state` is what `check` prints per step: DERIVED (strict parse, no Jinja), JINJA-NEUTRALISED (the
    braces were replaced by `null` first, and the count of them is stated), NO TEMPLATE (no such file at
    this ref), CANNOT PARSE (a strict `json.loads` refused it after the one substitution -- a finding
    about the document, not a licence to say nothing).

    `keys` maps each top-level binding to its value as read, and a value that was Jinja becomes None --
    which is honest: neutralising is lossy about the VALUE, and this file never pretends to render.
    Keys are counted, never scraped with a regex: `_declared_inputs()` refuses regex for a documented
    reason (a nested object like `runtime_override_plot_qc_per_family: {"mem_gb": 15}` was once
    miscounted that way, 12 reported where the template binds 10), and this is the same class of
    document.
    """
    if not path:
        return {"state": "NO TEMPLATE", "keys": {}, "neutralised": 0, "error": ""}
    try:
        text = open(path).read()
    except OSError as e:
        return {"state": "CANNOT PARSE", "keys": {}, "neutralised": 0, "error": str(e)}
    n = len(JINJA_RE.findall(text))
    try:
        obj = json.loads(JINJA_RE.sub("null", text))
    except ValueError as e:
        return {"state": "CANNOT PARSE", "keys": {}, "neutralised": n, "error": str(e)}
    if not isinstance(obj, dict):
        return {"state": "CANNOT PARSE", "keys": {}, "neutralised": n,
                "error": f"top level is a {type(obj).__name__}"}
    keys = {str(k): v for k, v in obj.items()}
    return {"state": "JINJA-NEUTRALISED" if n else "DERIVED", "keys": keys,
            "neutralised": n, "error": ""}


def templates_for_ref(ref: str) -> str:
    """The templates tree at `ref`, or '' when this command has no readable ref to read one from.

    '' is a stated state, not a silent one: `check --wdl-dir <dir>` points at a dirty tree with no ref
    at all, so the per-step line prints NO TEMPLATE with the reason. A source that could not be read is
    never reported as a comparison that passed.
    """
    if not ref:
        return ""
    try:
        return _tree_from_ref(ref, TEMPLATE_SUBPATH)
    except CannotCheck as e:
        print(f"  templates NOT READ: {e} -- the optional-bound-upstream finding cannot run, so "
              f"'nothing to report' below is about the WDL only")
        return ""


# Which ref a config's bindings should be compared to. Empty means GSVTK_BRANCH. Set it when the
# config points somewhere else: batch_rerun_step.py honours GSV_WDL_VERSION, so its Dockstore pin can
# legitimately differ from GSVTK_BRANCH -- checking BRANCH there would be a guard pointed at a ref
# nothing is about to run, i.e. a confident pass about the wrong document.
VERIFY_REF = ""


def _verify_ref() -> str:
    return VERIFY_REF or BRANCH


def _posted_ref(tag: str = "") -> str:
    """The one ref the guard and the body may read -- or a refusal, never two answers.

    `--against` belongs to `check`: that command POSTs nothing, and asking about a ref you chose is the
    whole reason it exists. On a command that POSTs it was a defect rather than a convenience.
    `preflight()` graded `_flag_value("--against")` while `_adapt_inputs()` graded `_verify_ref()`, so
    `create --confirm --against <a> --drop-branch-only-inputs` let the guard pass -- with total
    confidence -- the pruned map of ref `a` while `body()` built and POSTed the map of
    `GSVTK_BRANCH`/`GSV_WDL_VERSION`, which is the Dockstore pin inside that very config and so the WDL
    Terra will actually run. A guard passing about a document nothing runs is the failure this file
    already refuses elsewhere by carrying VERIFY_REF; with the drop flag it is worse than a wrong pass,
    because the guard prints `DROPPED for this ref ... this is what body() posts` about a body that is
    not the one being POSTed, and nothing but Terra can reveal the difference.
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


def declared_for_config(name: str, ref: str | None = None):
    """(declared, required) for one config's workflow at `ref` (default: what it will run).

    None when it cannot be established. Callers must treat None as 'unknown', which is NOT 'fits':
    dropping a binding you never verified is how a main-shaped run happens by accident.
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
    """The miniwdl workflow object for one config's workflow -- the one loader behind both maps.

    Inputs and outputs are declared by the same document, so they must come from the same load: two
    loaders means two import guards and two notions of "readable", which is how one map gets graded
    against a file the other map never saw. Cached per file for the reason _WDLCACHE is -- `check`
    and `create` ask about five configs, and each question used to re-parse a WDL tree that imports
    most of the others.

    miniwdl, not a regex: a first attempt at this check reported 13 unknown bindings against main
    because its regex missed whole `input {}` blocks, and main really has exactly one. miniwdl also
    resolves imports, so a key sourced from another file is read as declared rather than extra.
    Raises CannotCheck (never SystemExit) for a tree it cannot read -- see that class.
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
    # `wf.inputs` is None for a workflow with no `input {}` block at all (miniwdl types it
    # Optional[List[Decl]]), and iterating None leaves check_maps by crash -- the same defect class
    # this file already documents once for a 3-segment binding key, where the guard refused by
    # crashing rather than by reporting and its own "not checked" report was unreachable.
    decls = list(_load_workflow(wdl_dir, workflow).inputs or [])
    # `type.optional` is the flag (miniwdl's Decl has no `.optional`); `expr is None` means no
    # default, so required = declared without `?` and without `= ...`.
    declared = {str(d.name) for d in decls}
    required = {str(d.name) for d in decls if not d.type.optional and d.expr is None}
    return declared, required


def _declared_outputs(wdl_dir: str, workflow: str):
    """(names in declaration order, has_output_block) for the workflow-level outputs.

    `wf.outputs` is that workflow's own `output {}` block and nothing else -- which is the scope a
    method config can bind: Terra writes back what the WORKFLOW returns. A task's outputs are not
    addressable as `Workflow.thatOutput`, so collecting `doc.tasks[*].outputs` here would certify
    keys Rawls can never write. The one exception is call-level wiring written as
    `Workflow.Call.out`, which `check_maps` reports UNCHECKED instead of guessing at.

    `has_output_block` is False when the workflow has no `output {}` block at all (miniwdl gives
    None) as opposed to a block declaring nothing ([]). The two read the same in the summary line and
    mean different things: absent points at the wrong workflow, empty points at a workflow
    mid-refactor. Both are findings, never "nothing to complain about".
    """
    outs = _load_workflow(wdl_dir, workflow).outputs
    return [str(d.name) for d in (outs or [])], outs is not None


def _workflow_of_config(name: str, spec: dict) -> str:
    """Which workflow's WDL declares this config's keys -- read from the config's own method pin."""
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
    """What `check` compares: the five shipped maps, or one config JSON read from disk.

    Each entry is {name, workflow, inputs, outputs, origin}; `workflow` is the file whose declared
    keys the two maps get compared against (for a JSON, the one its own methodRepoMethod names, not
    whatever GSVTK_BRANCH happens to be set to).

    `--config <name>` used to select out of CONFIGS and nothing else, so ANY other value -- a typo, a
    config another module owns, a JSON exported from a workspace -- selected nothing, the loop ran
    zero times, and the command printed `check: clean` with exit 0. An empty read as success by the
    one command whose whole job is to say what it compared. A name CONFIGS holds still works; any
    other name is a config JSON to read; a name that is neither is a refusal that lists what it
    looked for. Never an empty selection.
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


def _classify_outputs(config_out: dict, declared_out: list[str], has_block: bool) -> dict:
    """Sort an outputs map and the workflow's declared outputs into the three states. Pure, no printing.

    Computed before anything prints, so the summary line and the findings cannot disagree about the
    count. Three states, three remedies -- which is the whole reason they are not one "bad outputs"
    number:

      empty_config    the config binds no outputs (absent key or `{}`): Rawls accepts that, so the
                      workflow RUNS, SUCCEEDS, and throws every result away with the scratch bucket.
                      The next step reads an empty attribute and the failure surfaces two steps on.
      empty_workflow  the workflow declares no workflow-level outputs, so nothing here CAN be
                      compared. Disclosed, NOT scored: it is a property of the document under
                      --against/--wdl-dir rather than of this config, and scoring it per key turns a
                      fixture or a half-finished refactor into ten findings -- the same conflation
                      this function exists to remove, running the other way. It is not a pass either:
                      the report says the keys are unverified.
      undeclared      bound leaves the workflow does not declare -- the expensive one: Rawls reports
                      `invalidOutputs: 0` for these at create/validate time, so the config is created
                      and looks created, and it fails at submission in a message that names no key.
      nested          3-segment keys (`Wf.Call.out`): legitimate call-level wiring this check cannot
                      grade, reported UNCHECKED exactly as the input side reports its own.
    """
    outs = dict(config_out or {})
    bound, nested = {}, {}
    for k in outs:
        parts = k.split(".")
        if len(parts) > 2:
            nested[k] = ".".join(parts[1:-1])          # full key -> the callee it binds on
        else:
            bound[parts[-1]] = k
    empty_config, empty_workflow = not outs, not declared_out
    undeclared = [] if (empty_config or empty_workflow) else \
        sorted((k, outs[k]) for leaf, k in bound.items() if leaf not in declared_out)
    unsaved = [] if empty_workflow else sorted(d for d in declared_out if d not in bound)
    # `empty_workflow` deliberately contributes 0 to the count (docstring). The header still prints
    # `N out vs 0 declared` and `_report_outputs` names the state, so "not scored" cannot read as
    # "the outputs were checked and fit".
    return {"outs": outs, "bound": bound, "nested": nested, "undeclared": undeclared,
            "empty_config": empty_config, "empty_workflow": empty_workflow, "has_block": has_block,
            "unsaved": unsaved,
            "problems": len(undeclared) + (1 if empty_config else 0)}


def _report_template(tmpl_dir: str, workflow: str, tpl: dict, omitted: list, label: str) -> None:
    """The template verdict for one step, and the one finding class only the template can give.

    Four states, four sentences, because they mean different remedies: DERIVED and JINJA-NEUTRALISED are
    both a successful read (the second says values were replaced by `null`, so the KEYS are known and the
    VALUES are not); NO TEMPLATE says upstream ships nothing for this workflow at this ref; CANNOT PARSE
    says the document refused the strict parse after the one substitution. None of them is a pass, and
    none of them is a failure of this config.

    `optional-bound-upstream-but-omitted` is the finding `check_maps` could not see before the templates
    were read at all: an input the WDL makes OPTIONAL, that production PINS, and that this profile leaves
    to the WDL default. Nothing else distinguishes "optional, deliberately default" from "optional,
    forgotten" -- miniwdl is silent about who supplies an optional input, and `validate()` prints
    `missingInputs` without failing. NON-FATAL by design: for genotyping the answer is recorded in the
    profile (`_why_unbound` in `profiles/genotyping.json` says `n_RD_genotype_bins` and
    `fail_on_degenerate_sr_cutoffs` come from the WDL defaults on purpose), and a non-fatal named line is
    what makes that deliberate choice visible to the next module's author instead of invisible.
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
    for k in omitted:
        v = tpl["keys"][k]
        print(f"      optional-bound-upstream-but-omitted  {k} -- upstream's template binds it"
              + (f" to {v!r}" if v is not None else " (a Jinja value, unread without rendering)")
              + f",\n        {workflow} declares it optional, and this profile binds nothing, so the WDL "
              f"default wins.\n        NON-FATAL, and for this module deliberate: see `_why_unbound` in "
              f"the profile. For a new\n        module it is the case §4 of docs/module-profiles.md names "
              f"-- an optional input nobody\n        asks about is exactly how a default becomes "
              f"production behaviour.")


def _report_outputs(name: str, workflow: str, declared_out: list[str], g: dict) -> None:
    """The verdict per output key, and one sentence per empty state. `_classify_outputs` decided."""
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
    """Every top-level bound key of BOTH maps vs what that ref declares; every required input vs what is bound.

    Call-site bindings (`Workflow.Call.input`) are counted and named but NOT compared: this reads the
    top-level workflow's declared inputs only, and saying so per key is the difference between a
    footnote and a blind spot. They used to crash this function instead -- see probe_fixes.py's
    nested_bindings probe. Output keys shaped `Workflow.Call.out` get the same treatment on the
    output side: named, not compared, not counted as a problem.

    The outputs map is compared against the workflow's WORKFLOW-LEVEL outputs, and the three states
    that used to be conflated are reported apart (`_classify_outputs`). It costs a review's worth of
    money to conflate them: an undeclared output key is accepted at config creation with
    `invalidOutputs: 0` and only fails at submission, without naming the key.

    `drop` grades the map `body()` would actually POST. With `--drop-branch-only-inputs`,
    `_adapt_inputs()` removes known-branch-only bindings the ref does not declare, so comparing the
    raw table here refused -- with total confidence -- a document Rawls accepts: `show` printed a
    16-key body, and `create` on the same command line refused to POST it while naming the key it had
    just dropped. Same rule, same `BRANCH_ONLY_INPUTS` table, same declared set. It can only fire
    here because `declared` came from miniwdl: the `CannotCheck` branch above already aborted when the
    ref could not be read, and an unverified ref is not evidence that a binding is absent.

    `ref` is only a label, but the label is the claim: `check --against <a>` grades `<a>`, while
    `create`/`validate` grade the ref their own config runs (`_posted_ref`). Both print it, so
    "graded" never silently means "graded against something other than what you asked for".
    """
    bad = 0
    ncomp = 0
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
        # The same _load_workflow call as above, so the two maps cannot be graded against two
        # documents; the cache makes the second call a dict lookup rather than a second parse.
        declared_out, has_block = _declared_outputs(wdl_dir, workflow)
        g = _classify_outputs(tgt["outputs"], declared_out, has_block)
        # `nested` was `[]` with `.update()` called on it, so the FIRST key shaped
        # `Workflow.Call.input` raised AttributeError out of check_maps -- and check_maps is what
        # preflight() runs before create/validate, so the refusal happened by crashing rather than by
        # reporting. The "not checked" report below was therefore unreachable, while upstream binds
        # call inputs itself: 5 in single_sample/GATKSVPipelineSingleSample.json.tmpl
        # (`GATKSVPipelineSingleSample.MakeCohortVcf.HERVK_reference`, ...) and 80 in the
        # GATKSVPipelineBatch test fixture. Pinned by probe_fixes.py's nested_bindings probe.
        #
        # Keyed on the FULL key, not the leaf: two distinct call bindings can share a leaf
        # (`A.Cohort.x` / `A.Other.x`), and `.update({leaf: key})` collapsed whichever came second.
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
        # The template side (§9 step 3): what production binds, read structurally at the same ref.
        tpl = read_template(find_template(tmpl_dir, workflow)) if tmpl_dir else \
            {"state": "NOT READ", "keys": {}, "neutralised": 0, "error": "no ref to read at"}
        bound_here = set(tgt["inputs"]) | {f"{workflow}.{k}" for k in bound}
        omitted = sorted(k for k in tpl["keys"]
                         if k.split(".")[-1] in declared and k.split(".")[-1] not in required
                         and k not in bound_here and f"{workflow}.{k.split('.')[-1]}" not in bound_here)
        # One line per config, both maps on it: the reader sees in one place that the output side was
        # compared too, how many keys each side had, and which file a JSON config came from.
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
        # Reported per key, not as a count: a binding nobody compared has to be findable in the output,
        # or "3 not checked" reads as a footnote and a renamed sub-workflow input stays invisible --
        # which is the class this whole check exists to catch. Not counted as a problem either: a call
        # binding is legitimate wiring, and this check compares the TOP-LEVEL workflow's inputs only.
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
            # Matched on the FULL key ("GenotypeBatch.training_vcf"), which is how the table is
            # written. Matching the bare name meant the known-branch-only branch never fired and
            # every explanation below read as "we have never seen this input".
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
        # The outputs map, after the input findings, so one config's report is one block. Its
        # problems are counted with everything else; the header already showed the verdict.
        _report_outputs(name, workflow, declared_out, g)
        bad += g["problems"]
    if bad:
        print(f"check: {bad} problem(s) across {ncomp} config(s) compared. Either the ref is not the "
              f"one these maps\nwere written against or the maps are stale. `validate` asks Terra the "
              f"same question but needs\nthe ref published on\nDockstore; this needs only your "
              f"checkout.")
        return 1
    # The count of configs compared is in the sentence, because "clean" about zero configs is the
    # exact shape this command shipped with (`--config <typo>` printed it) and a reader cannot check a
    # claim whose population is unstated.
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
            # A ref you cannot read is not a ref that fits: say what failed, and do not exit 0.
            raise SystemExit(f"cannot read the WDL at {ref}: {e}") from None
        print(f"WDL read from {config.get('GATK_SV_CHECKOUT')} @ {ref}")
    # --tmpl-dir is the template side of `--wdl-dir`: a dirty tree has no ref to archive, so the bytes
    # you want compared are the ones on disk. Both point at a directory, and both are stated on the
    # per-step line, so "which document did you read" is never a guess.
    td = _flag_value("--tmpl-dir") or ("" if _flag_value("--wdl-dir") else templates_for_ref(ref))
    return check_maps(wd, only=_flag_value("--config"), drop=DROP_FLAG in sys.argv, ref=ref,
                      tmpl_dir=td)


def preflight(tag: str) -> None:
    """The same comparison, before anything that POSTs or burns a validation round-trip."""
    # Resolved BEFORE the override is honoured: with --allow-unknown-inputs nothing gets compared, but
    # body() still prunes by the ref its config runs, so an --against pointing elsewhere is still a
    # command that means two different things and must not reach the network.
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
        # Stated, never silent: this is the reason a wrong map used to reach submission at all.
        print(f"{tag}: pre-check SKIPPED -- need a ref ({ref or 'unset'}) and a checkout "
              f"({ck or 'unset'}).\n      Not a pass:  python terra/batch_configs.py check "
              f"--against <ref>")
        return
    # Name the ref that was compared, because for a rerun it is the Dockstore pin (GSV_WDL_VERSION),
    # not necessarily GSVTK_BRANCH -- a pass against the wrong ref is worse than no check.
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
    out = {}
    for name in CONFIGS:
        b = body(name)
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
    """Terra resolves the Dockstore WDL and reports per-input binding: the cheapest real gate
    before spending money. Response shape is extraInputs / invalidInputs / invalidOutputs /
    missingInputs / validInputs - there is no boolean 'valid' key."""
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


# Flags that CONSUME the next token. `show` used to be found by "every argument that does not start
# with -", which meant `check --against main` was reported as two modes -- and the fix for that must
# not be to accept any stray positional, because `create foo` should still be a usage error.
VALUE_FLAGS = ("--against", "--wdl-dir", "--tmpl-dir", "--config")


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
# Why fixtures and not the shipped maps: the three states this check exists to tell apart are
# invisible in a real config, because a real one is right about 36 keys and wrong about at most one.
# On a two-output workflow the expected answer is obvious by inspection, so "the check found it" and
# "the check compared nothing" cannot be confused -- the same reason checks/wdl_semantics.py
# --selftest builds its own tree instead of scanning gatk-sv, and the same reason every control here
# is paired with a case that must NOT fire.
#
# Offline by construction: every phase passes --wdl-dir, so no ref is resolved, no checkout is read,
# no identity is required and no request leaves the machine. It runs on an empty profile.
#
# FIXTURES: one WDL is the control (two declared outputs); the other two are the empty side of the
# two empty states -- no `output {}` block at all, and a block that declares nothing. miniwdl reports
# those as None and [], which is why the messages say "absent" and "declares nothing" apart.
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
    """One method-config JSON, built the way `create` builds its body -- Dockstore URI and all.

    Not a hand-written shape: going through `dockstore()` means `_workflow_of_config()` is reading
    the same methodPath that will be POSTed, so the fixture cannot drift from the real payload.
    """
    p = os.path.join(dirpath, fname)
    with open(p, "w") as fh:
        json.dump({"namespace": "selftest", "name": os.path.splitext(fname)[0],
                   "rootEntityType": "sample_set", "methodRepoMethod": dockstore(workflow),
                   "inputs": {f"{workflow}.batch": "this.sample_set_id"},
                   "outputs": outputs}, fh, indent=1)
    return p


def _run_check(*argv: str):
    """`check` with a constructed argv: (exit code, stdout+stderr). No subprocess: same interpreter.

    The exit CODE is what is asserted, not the return value, because a refusal travels as
    SystemExit("text") and 1 is the number `make test` sees. stderr is folded in for the same reason
    the shell assertions use 2>&1.
    """
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
        # the $18.87 case: one key the workflow does not declare
        "wrong": _selftest_config(tmp, "tiny-wrong-name.json", "Tiny",
                                 {"Tiny.out_one": "this.tiny_one_new",
                                  "Tiny.out_three": "this.tiny_three_new"}),
        # state 3: the config binds nothing
        "nobinding": _selftest_config(tmp, "tiny-empty-outputs.json", "Tiny", {}),
        # state 2a/2b: the WORKFLOW is the empty side, block absent vs block declaring nothing. Not
        # scored (the keys are unverified, not wrong); asserted as "no per-key findings, own message".
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
    # 1. THE CONTROL. Everything else is only evidence if this exits 0: a comparator that always fails
    #    looks identical to a comparator that found the planted defect.
    case("ok", 0, ["out DECLARED     Tiny.out_one -> this.tiny_one_new",
                   "out DECLARED     Tiny.out_two -> this.tiny_two_new",
                   "1 config(s) compared"],
         ["UNDECLARED", "NO OUTPUTS"],
         "control: a config whose two keys the workflow declares passes, naming both")
    # 2. the expensive one: undeclared key, named, with the workflow's real names beside it
    case("wrong", 1, ["out UNDECLARED   Tiny.out_three", "'out_three' is not one of them",
                      "out_one, out_two", "invalidOutputs: 0"],
         ["NO OUTPUTS BOUND", "NO OUTPUTS DECLARED", "check: clean"],
         "a key the workflow does not declare fails, names the key, and lists what IS declared")
    # 3. the config's own emptiness -- a different sentence, and it must not borrow the other two's
    case("nobinding", 1, ["NO OUTPUTS BOUND", "binds 0 output(s); Tiny declares 2"],
         ["out UNDECLARED", "NO OUTPUTS DECLARED", "check: clean"],
         "a config with no outputs fails as NO OUTPUTS BOUND, not as a naming problem")
    # 4. the workflow's own emptiness, two shapes: its own state, no per-key findings, and NOT scored
    #    as a problem -- those keys are unverified, not wrong. What is asserted is that the tool does
    #    not enumerate 10 findings against a document that declares nothing, and does not call it a
    #    comparison that passed.
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

    # 6. --config names: the five stay reachable, a JSON path is read, a name that is neither refuses.
    #    The last one is the bug this widening fixes: `--config <typo>` used to compare zero configs
    #    and print `check: clean` with exit 0.
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
    # 7. the workflow a JSON points at, not the one GSVTK_BRANCH points at: `Tiny` is not in CONFIGS
    #    and no gatk-sv workflow is named Tiny, so a pass above is evidence the name came from the
    #    config's own methodRepoMethod.
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
        # Before any mode dispatch: it needs no profile, no target and no network, and it must not
        # fall through to the default mode (`show`), which would exit 0 having tested nothing.
        raise SystemExit(selftest())
    args = positional(sys.argv[1:])
    if len(args) > 1:
        raise SystemExit(f"one mode at a time; got {' '.join(args)!r}   (--help)")
    mode = args[0] if args else "show"
    if mode not in ("show", "check", "create", "validate"):
        usage(2)                      # a typo must be a usage error, not a traceback
    if mode == "check":
        # Deliberately BEFORE require_target: check is offline, needs no workspace and no auth, and
        # is the tool you run precisely while the target is still undecided.
        raise SystemExit(cmd_check())
    if mode == "create" and "--confirm" not in sys.argv:
        raise SystemExit(
            "create POSTs (and overwrites) method configs in your workspace: a config with a\n"
            "  wrong binding is worse than no config, because the next submission will use it.\n"
            "  review `show` first, then re-run with --confirm.")
    if mode in ("create", "validate"):
        # Identity is resolved here, before any request: an unset workspace must be exit 4 with a
        # named key, never an empty path segment in a POST to Terra.
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
  --selftest  the three output states against WDL + config fixtures built in a temp dir:
              each must fire, the control must not, and no two states may print alike.
              Offline, no profile, no credentials (--wdl-dir, so no ref is resolved).

  --call-cache / --no-call-cache  whether Cromwell may reuse call outputs. Off unless asked
              for: a cross-workspace cache copy has timed out mid-run and failed a submission
              (docs/handoff/003), and two-arm A/Bs want each arm to actually run (docs/
              terra-head-to-head.md §5). Both flags together is a refusal, not a tie-break.

The branch under test is GSVTK_BRANCH; attribute suffixes are GSVTK_FROZEN_SUFFIX /
GSVTK_NEW_SUFFIX and must match what batch_freeze.py and batch_fetch_compare.sh use.""",
          file=sys.stderr if code else sys.stdout)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
