"""Re-run ONE numbered step of the batch chain (06, 07, 08, 09, 10) against your branch + images.

Why a separate script: the head-to-head method configs are gone from the sandbox
workspace (list_workspace_configs -> 0), and the image attributes should not be
rewritten in place (they are the provenance of the completed Terra chain), so
this creates ONE config with the docker images as literals and everything else
exactly as batch_configs.py specified it.

    python terra/batch_rerun_step.py show                     # read-only (step 10, the default)
    python terra/batch_rerun_step.py --step 08 show           # any step of 06..10
    python terra/batch_rerun_step.py --step 09 create         # POST config
    python terra/batch_rerun_step.py --step 09 validate       # Terra-side WDL check
    python terra/batch_rerun_step.py --step 08 submit         # run it (mutation)
    python terra/batch_rerun_step.py status                   # submissions + Cromwell id

The step is `--step`, and it defaults to 10 because that is the step this tool was first written for.
Everything the step decides -- config name, the `<Workflow>.` input-key prefix, the Dockstore method
path, the root entity type -- comes from `terra/steps.py`, never from a literal in this file: there
used to be five of those here, and `--step 08` was not a step, just a flag the parser accepted while
it reran GenotypeBatch. Single-sample / participant-rooted modules are out of scope by operator
decision: the freeze loop cannot write a participant entity at all (docs/module-profiles.md §11 q3),
so a rerun of those would be a claim nothing can check.

Images are pinned explicitly so the run cannot silently inherit a pre-change
image from a workspace attribute. Literal File values must be quoted inside the
expression (Terra parses a bare `us.gcr.io/...` as an unquoted identifier and
rejects it: "The value you entered is not in the correct format for this data
type"), hence the embedded double quotes.

Caching is the second thing this tool used to inherit rather than choose. It imports `body()` from
batch_configs.py, so when that builder shipped `"useCallCache": True` this rerun POSTed `true` --
and `show` printed it, but nothing here said it was a choice. A rerun exists to ask "did my change
alter the output", which a cache hit answers before the VM boots, so the shared default is now False
and this file states the mode it resolved (and refuses `--call-cache --no-call-cache` together).
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
sys.path.insert(0, HERE)
import config  # noqa: E402
import terra                      # noqa: E402
from terra import fapi      # noqa: E402  # via terra: one friendly missing-dependency message
import batch_configs as tc          # noqa: E402  (NS/WS are read THROUGH this module)
import steps as st                        # noqa: E402  # the step -> workflow/entity map, one copy
from batch_configs import body             # noqa: E402
# dstore() below deliberately duplicates tc.dockstore() with a version override; scripts/probe_fixes.py
# asserts the two are byte-identical for the same version, so the duplication cannot drift.

# What --step resolved to, once. Built lazily and CACHED because `create`/`submit` ask for it more
# than once and probes call those functions directly (with sys.argv set) instead of going through the
# parser below -- so it reads sys.argv at call time, which keeps a direct call with no --step on the
# command line the documented default step rather than an AttributeError on unset state.
_RESOLVED: dict = {}


def _opt(flag: str) -> str:
    """The value after FLAG on this command line, or '' (a hand-rolled parser, like the one below)."""
    a = sys.argv
    i = a.index(flag) if flag in a else -1
    return a[i + 1] if i >= 0 and len(a) > i + 1 and not a[i + 1].startswith("--") else ""


def resolved() -> dict:
    """The per-step data this invocation runs: config name, input prefix, method path, root entity.

    Refuses a step the map has never heard of (naming the steps it does know) and a step whose two
    copies of the root entity disagree -- see `_reconcile`.
    """
    if not _RESOLVED:
        r = st.rerun(_opt("--step") or st.DEFAULT_STEP)
        _reconcile(r)
        r["entity"] = _row_name(r)      # '' when the row cannot be known; `entity()` is what refuses
        _RESOLVED.update(r)
    return _RESOLVED


def _reconcile(r: dict) -> None:
    """Refuse when `steps.py` and `batch_configs.CONFIGS` describe the same step differently.

    The root entity and the workflow name exist in BOTH tables (the chain builder needs them too, and
    docs/terra-head-to-head.md §3 calls `rootEntityType` there the authority). Two copies of one value
    is this repo's named failure class, so neither copy is trusted on its own here: they are compared,
    and a disagreement is a refusal rather than whichever one the code happened to read first. A step
    `batch_configs` ships no map for is refused too -- there is no input map to POST, so there is no
    rerun.
    """
    spec = tc.CONFIGS.get(r["config"]) if isinstance(tc.CONFIGS, dict) else None
    if not spec:
        raise SystemExit(
            f"no input map for step {r['step']} ({r['workflow']}) in batch_configs.CONFIGS, so this "
            f"step\n  has no bindings to rerun. The rerun posts a body built from that map; it does not\n"
            f"  invent one. Steps with a map: {', '.join(sorted(tc.CONFIGS))}.")
    disagree = [("workflow", r["workflow"], spec.get("workflow")),
                ("root entity", r["root_entity"], spec.get("rootEntityType"))]
    both = [(w, mine, theirs) for w, mine, theirs in disagree if theirs and mine != theirs]
    if both:
        raise SystemExit(
            f"step {r['step']} is described two ways, and which one wins would decide what runs:\n"
            + "".join(f"    {w}:  terra/steps.py says {mine!r}, "
                      f"batch_configs.CONFIGS[{r['config']!r}] says {theirs!r}\n"
                      for w, mine, theirs in both)
            + "  They cannot both be right, and a submission cannot be reviewed from the outside when "
              "it\n  happens. Fix terra/steps.py RERUN_ROOT_ENTITY / STEPS (the rerun map) and "
              "batch_configs\n  CONFIGS (the chain map) to agree, then re-run.")

# Image refs are PINNED LITERALLY into the config rather than left as workspace
# attribute references -- that is the whole reason this tool exists separately from
# batch_configs.py. If they stay as `workspace.sv_pipeline_docker`, a rerun silently
# inherits whatever image the attribute happened to point at, and you measure the old
# code. So they are never a *derived* default. Either pass them per call --
#     --image gatk_docker=.../gatk:<tag> --image sv_pipeline_docker=.../sv-pipeline:<tag>
# (see docs/docker-builds.md for building them) -- or name them explicitly in the profile as
# GSVTK_IMAGE_REPO / GSVTK_GATK_IMAGE_REPO. `show` prints the exact resolved config either way.
def parse_images(argv: list[str], prefix: str = "") -> dict:
    """`--image gatk_docker=REF` -> `{<Workflow>.gatk_docker: "REF"}`, for the step being rerun.

    The prefix is the workflow's, from `steps.py`: a body for 08 whose keys say `GenotypeBatch.` binds
    inputs that workflow never declared, which is the extra-input rejection Rawls answers at submission.
    """
    out = {}
    for spec in argv:
        if "=" not in spec:
            raise SystemExit(f"--image wants key=IMAGE_REF, got {spec!r}")
        key, ref = spec.split("=", 1)
        key = key.split(".")[-1]
        if not key.endswith("_docker"):
            key += "_docker"
        out[f"{prefix}{key}"] = f'"{ref}"'   # literal File values must be quoted
    return out


def images_from_config(prefix: str = "") -> dict:
    """Fall back to an image the profile names EXPLICITLY.

    Only an explicit `GSVTK_IMAGE_REPO` / `GSVTK_GATK_IMAGE_REPO` counts. A value merely
    *derived* from the project is not a statement about which code ran, and guessing here is
    exactly the failure this tool exists to prevent -- so a derived default still stops.
    """
    res = config.resolve()
    out = {}
    for key, ck in (("gatk_docker", "GATK_IMAGE_REPO"), ("sv_pipeline_docker", "IMAGE_REPO")):
        value, source = res.get(ck, ("", ""))
        # An untagged registry path is not a pin: `.../sv-pipeline` floats to whatever is current at
        # pull time, so the "which code ran" question has no answer afterwards.
        if value and source in ("env", "profile") and ":" in value.rsplit("/", 1)[-1]:
            out[f"{prefix}{key}"] = f'"{value}"'
    return out


IMAGES: dict[str, str] = {}
CONFIRMED = False        # set only by an explicit --confirm on the command line
ALLOW_UNPINNED = False   # set only by an explicit --allow-unpinned-docker
WDL_VERSION = os.environ.get("GSV_WDL_VERSION") or config.get("BRANCH")
# This config's Dockstore pin is WDL_VERSION, which GSV_WDL_VERSION can move off GSVTK_BRANCH. Tell
# batch_configs which ref to compare bindings against, or its pre-check would grade BRANCH while this
# body points elsewhere -- a pass about a document nobody is about to run.
tc.VERIFY_REF = WDL_VERSION


def dstore(version: str, workflow: str = "") -> dict:
    """Exact same construction as batch_configs.dockstore(), with a version override.

    Rawls validates this URI on overwrite (a malformed one -> HTTP 404 "Cannot get
    dockstore://... from method repo"), so it has to be byte-exact: every slash in the
    path is %-encoded, including the ones in github.com.

    `workflow` is the workflow this config runs. Left off, it means "the default step" -- the call
    `scripts/probe_fixes.py`'s `dstore_drift` makes to compare this function against `tc.dockstore()`
    for one known workflow, which is the guard that the two hand-built URIs have not drifted.
    """
    path = f"github.com/broadinstitute/gatk-sv/{workflow or st.workflow(st.DEFAULT_STEP)}"
    return {"sourceRepo": "dockstore", "methodPath": path, "methodVersion": version,
            "methodUri": f"dockstore://{path.replace('/', '%2F')}/{version}"}


def _upstream(r: dict) -> tuple:
    """(earlier chain steps this step reads, the frozen-baseline file it needs) -- both derived.

    Nothing new is stated here: the producer of an attribute is read out of `batch_configs`' own
    `outputs` maps, and an input is "chain-written" or "frozen" by the suffix `batch_configs` gives it
    (`tc.NW` / `tc.FZ`). A rerun of step 07 after a main-shaped 06 measures nothing about 07 unless you
    know 06 wrote the row 07 read, and the tool that proves the attributes exist is
    `terra/batch_check_inputs.py --step <NN>` -- this only names what to check, because whether the
    attributes are really in your workspace is a question about your workspace.
    """
    producer: dict[str, str] = {}
    for name, spec in tc.CONFIGS.items():
        for value in (spec.get("outputs") or {}).values():
            producer[str(value).rsplit(".", 1)[-1]] = name
    chain, frozen = set(), set()
    for value in (tc.CONFIGS[r["config"]].get("inputs") or {}).values():
        value = str(value)
        leaf = value.rsplit(".", 1)[-1]
        if tc.NW and value.endswith(tc.NW):
            chain.add(producer.get(leaf) or f"an earlier run of this chain ({leaf})")
        elif tc.FZ and value.endswith(tc.FZ):
            frozen.add(leaf)
    return sorted(chain), sorted(frozen)


def announce(r: dict, mode: str) -> None:
    """Say which step this resolved to, on STDERR: `show | jq` must stay valid JSON.

    A rerun that silently reran the wrong step is the defect this file had; the step, the workflow, the
    config name, the root entity and the Dockstore pin are now stated in every mode, and the upstream
    attributes are named so a rerun of a mid-chain step cannot read last run's output and call it a
    result.
    """
    print(f"[rerun] step {r['step']}  workflow {r['workflow']}  config {r['rerun_config']}\n"
          f"        root entity {r['root_entity']}  "
          f"(row: {r['entity'] or 'UNRESOLVED: pass --entity <name>'})\n"
          f"        Dockstore {r['method_path']} @ {WDL_VERSION or 'GSVTK_BRANCH unset'} "
          f"-- the ref the binding check grades too", file=sys.stderr)
    chain, frozen = _upstream(r)
    if chain or frozen:
        print(f"        reads "
              + (f"chain outputs of: {', '.join(chain)}" if chain else "no chain outputs")
              + (f"  |  frozen baseline files: {len(frozen)} (published by batch_freeze.py)"
                 if frozen else ""), file=sys.stderr)
        print(f"        prove those attributes exist first: "
              f"python terra/batch_check_inputs.py --step {r['step']}   ({mode} does not read "
              "your workspace)", file=sys.stderr)


def _row_name(r: dict) -> str:
    """The row this step submits against, or '' when it cannot be known. Never a guess.

    `GSVTK_BATCH` names a `sample_set` row, which is the right row for 06/07/08/10 and the wrong KIND
    for 09: `MergeBatchSites` is rooted in `sample_set_set`, so its row is a COHORT, and no profile key
    holds one. `--entity` is how you say it.
    """
    return _opt("--entity") or (config.get("BATCH", "all_samples")
                                if r["root_entity"] == "sample_set" else "")


def entity(r: dict) -> str:
    """The row to submit against -- or a refusal naming the missing precondition and the fix.

    Submitting 09 with the batch name as if it were a cohort is how a submission gets accepted, boots a
    fleet and binds nothing, so the missing row is refused by name instead of guessed.
    """
    name = _row_name(r)
    if not name:
        raise SystemExit(
            f"step {r['step']} ({r['workflow']}) is submitted against a "
            f"{r['root_entity']} row, and\n  "
            f"  GSVTK_BATCH={config.get('BATCH', 'all_samples')!r} names a sample_set, not a "
            f"{r['root_entity']}.\n"
            "  pass --entity <cohort-name> (the sample_set_set row your workspace holds -- the row the\n"
            "      09-* submissions batch_status.py lists were made against). This tool will not guess "
            "it: the\n      wrong row binds every this.* input to nothing, and the submission still "
            "looks accepted.")
    return name


def make_body() -> dict:
    # Resolves the target *now*, and note NS/WS are read through `tc` afterwards: they are
    # module attributes there, so a value that was empty at import time is still picked up.
    tc.require_target()
    r = resolved()
    b = body(r["config"])
    b["name"] = r["rerun_config"]
    b["methodRepoMethod"] = dstore(WDL_VERSION, r["workflow"])
    b["inputs"].update(IMAGES)
    # docs/terra-head-to-head.md promises every `*_docker` is pinned literally. A `*_docker` still
    # written as an expression (`workspace.sv_pipeline_docker`) resolves to whatever that attribute
    # points at TODAY, so the rerun does not reproduce the code that ran -- and nothing in its output
    # says so. Fail closed rather than publish a config that quietly breaks the one guarantee this
    # tool exists to provide.
    unpinned = sorted(k for k, v in b["inputs"].items()
                      if k.endswith("_docker") and k not in IMAGES)
    untagged = sorted(k for k, v in IMAGES.items()
                      if ":" not in str(v).strip('\"').rsplit("/", 1)[-1])
    if (unpinned or untagged) and not ALLOW_UNPINNED:
        raise SystemExit(
            "image inputs are not fully pinned -- a submit with these runs whatever the workspace\n"
            "  attribute points at today, which is exactly what this tool exists to prevent:\n"
            + "".join(f"    unpinned:  {k}\n" for k in unpinned)
            + ("" if not untagged else
               "  pinned but untagged (a floating reference; today's bytes are not tomorrow's):\n"
               + "".join(f"    {k} = {str(IMAGES[k]).strip(chr(34))}\n" for k in untagged))
            + "  pass --image KEY=REF for each one (see --help), or --allow-unpinned-docker if you\n"
              "  genuinely want the attribute-resolved images for this rerun.")
    return b


def create() -> None:
    if not CONFIRMED:
        raise SystemExit("create overwrites a method config that submissions read.\n"
                         "  check `show` printed the images you meant, then re-run with --confirm.")
    rd = resolved()
    announce(rd, "create")
    # Identity and shared-target refusal BEFORE the first request, exactly as batch_configs.py
    # does it. This POSTs a config that the next submission reads back, so it is a write on the
    # same footing as `configs create` -- and an unresolved target used to reach Terra as an
    # empty workspace path segment (`POST /api/workspaces//methodconfigs`).
    tc.require_target(writes=True)
    # The other half of the guard: batch_configs gained a pre-check, but this path imports only
    # `body()` from it, so without this call the ONE path that actually submits was the unguarded
    # one. A main-shaped rerun reached Rawls and died at submission with an extra input, exactly like
    # the config path did before its own pre-check.
    tc.preflight("rerun create")
    b = make_body()
    resp = fapi.create_workspace_config(tc.NS, tc.WS, b)
    if resp.status_code == 409:
        resp = fapi.overwrite_workspace_config(tc.NS, tc.WS, tc.NS, rd["rerun_config"], b)
    print(f"create {rd['rerun_config']}: HTTP {resp.status_code}"
          f"{'' if resp.status_code in (200, 201) else resp.text[:300]}")
    if resp.status_code not in (200, 201):
        raise SystemExit(1)


def validate() -> None:
    # Resolve the workspace first: with no target this used to POST to
    # /api/workspaces//methodconfigs/.../validate and report whatever the edge said.
    rd = resolved()
    announce(rd, "validate")
    tc.require_target()
    tc.preflight("rerun validate")     # free and offline; validate costs a round-trip either way
    resp = fapi.validate_config(tc.NS, tc.WS, tc.NS, rd["rerun_config"])
    d = resp.json() if resp.status_code == 200 else {"error": resp.text[:300]}
    print(json.dumps(d, indent=1))
    # Per-step file names: with five rerunnable steps, one shared `rerun_config_validation.json` means a
    # 07 validate quietly replaces the record of the 10 you meant to look at.
    terra.dump(d, str(config.work_dir("metadata") / f"rerun_config_validation.{rd['step']}.json"))
    valid = d.get("invalid") or []
    if valid:
        print("\nINVALID:")
        for v in valid:
            print("  ", v)
        raise SystemExit(1)


def submit() -> None:
    # A joint-calling step is a fleet of VMs, not a function call. `confirm=True` below is
    # terra.py's own belt-and-braces; this is the one that stops an accidental invocation.
    if not CONFIRMED:
        raise SystemExit("submit starts real compute and spends real money.\n"
                         "  re-run with --confirm, and check `show` printed the images you meant.")
    rd = resolved()
    announce(rd, "submit")
    # The row before anything else: `--step 09` is rooted in sample_set_set and `GSVTK_BATCH` is not a
    # cohort, so the missing precondition is named here -- before the target check, before the pre-check,
    # well before anything that could boot a fleet.
    row = entity(rd)
    # Resolve first: assert_writable_target compares two strings, so with an unresolved target it
    # compared ('','') against the baseline coordinates and passed -- the guard that exists to
    # stop a submission into the shared reference run was defeated by the very misconfiguration
    # it should have caught.
    tc.require_target()
    terra.assert_writable_target(tc.NS, tc.WS, "submit a workflow",
                                allow="--allow-shared-target" in sys.argv)
    # Last offline moment before VMs boot. The config in the workspace may have been created from an
    # older map or a different ref than this invocation's, and the map this tool builds is the one it
    # assumes is there -- so compare that map to the WDL it will actually run before the fleet does.
    tc.preflight("rerun submit")
    # Named before the fleet boots, the same way the images are: a rerun whose calls were served from
    # a cache did not run the code you came to measure, and its output still looks like a pass. Read
    # from the same one rule batch_configs.body() applies to the body, never from a copy of its result.
    cache = tc.call_cache("rerun")
    print(f"rerun submit: {rd['rerun_config']} against {row} ({rd['root_entity']}), "
          f"useCallCache={str(cache).lower()} -- "
          + ("calls whose command+inputs match may be served from the cache"
             if cache else "every call re-runs, which is the whole point of a rerun"))
    d = terra.submit(tc.NS, tc.WS, tc.NS, rd["rerun_config"], row, rd["root_entity"], None,
                     confirm=True)
    print(json.dumps(d, indent=1)[:600])
    terra.dump(d, str(config.work_dir("metadata") / f"rerun_submission.{rd['step']}.json"))


def status() -> None:
    announce(resolved(), "status")
    tc.require_target()
    subs = terra.submissions(tc.NS, tc.WS, limit=8)["submissions"]
    for s in subs:
        print(" ", s.get("submissionId"), "|", s.get("status"), "|", s.get("submissionDate"),
              "| externalIds:", s.get("externalIds"))


def usage(code=0):
    print(f"""usage: batch_rerun_step.py [--step NN] [--image KEY=REF ...] [show|create|validate|submit|status]

Reruns ONE step of the batch chain with the images pinned literally into the config, so
the run cannot inherit a stale image from a workspace attribute. The step, its workflow,
its config name, its input-key prefix and its root entity all come from terra/steps.py.

  --step NN         one of {', '.join(st.known_steps())} (default {st.DEFAULT_STEP}):
                    {', '.join(f'{s}={st.step_names()[i]}' for i, s in enumerate(st.known_steps()))}
                    Every mode takes it. It prints what it resolved to, on stderr, in every mode.
  --entity NAME     the row to submit against. Defaults to GSVTK_BATCH for a sample_set step;
                    required for 09-MergeBatchSites, which is rooted in sample_set_set (a cohort row),
                    and refused rather than guessed.
  --image KEY=REF   repeat once per *_docker input, e.g.
                    --image gatk_docker=us.gcr.io/PROJ/NS/gatk:TAG
                    --image sv_pipeline_docker=us.gcr.io/PROJ/NS/sv-pipeline:TAG
  show              print the config body that would be POSTed (no mutation)
  create / validate POST it, then ask Terra to typecheck it
                    (create needs --confirm and refuses the shared baseline workspace unless
                     --allow-shared-target, exactly like batch_configs.py create)
  submit --confirm  start it (real compute; --confirm is mandatory)
  --allow-unpinned-docker   let a *_docker input resolve from a workspace attribute (off by
                            default: it breaks reproducibility, so it has to be asked for)
  --drop-branch-only-inputs  build the config for a ref that does NOT declare this branch's extra
                            inputs (e.g. GSV_WDL_VERSION=main) by dropping them. A SEMANTIC change:
                            it prints what it dropped and which ref it compared.
  --call-cache / --no-call-cache  whether Cromwell may reuse call outputs. Off unless asked for, and
                            this tool now says so instead of inheriting batch_configs' default: a
                            cached call never ran your image. (docs/terra-head-to-head.md §5, and the
                            cross-workspace cache copy that timed out mid-run in docs/handoff/003.)
  status            recent submissions in the configured workspace (every step's, not just --step's)""",
          file=sys.stderr if code else sys.stdout)
    raise SystemExit(code)


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        usage()
    # Hand-rolled parsing, because an unknown flag must be a usage error and not a KeyError
    # from a dispatch dict -- and `--image` takes a value that is itself `key=value`.
    specs, argv, confirm = [], [], False
    rest = sys.argv[1:]
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--image":
            if i + 1 >= len(rest):
                raise SystemExit("--image wants a KEY=IMAGE_REF argument")
            specs.append(rest[i + 1]); i += 2
        elif a.startswith("--image="):
            specs.append(a.split("=", 1)[1]); i += 1
        elif a == "--confirm":
            confirm = True; i += 1
        elif a == "--step":
            # Validated HERE, against the map, so a typo is a usage-shaped error naming the steps that
            # exist -- and so no mode can quietly reran the default step instead of the one asked for.
            if i + 1 >= len(rest):
                raise SystemExit(f"--step wants one of {', '.join(st.known_steps())}")
            st.workflow(rest[i + 1])
            i += 2
        elif a == "--entity":
            if i + 1 >= len(rest):
                raise SystemExit("--entity wants a row name (a sample_set for 06/07/08/10, a "
                                 "sample_set_set for 09)")
            i += 2
        elif a == "--allow-unpinned-docker":
            globals()["ALLOW_UNPINNED"] = True; i += 1
        elif a == "--drop-branch-only-inputs":
            # Accepted, and deliberately not stored here: batch_configs.body() reads it from sys.argv
            # while building the map. Listed in usage() because a flag that changes what runs must be
            # discoverable, and because this parser's job is to make typos a usage error -- an option
            # the guard needs but the parser rejects would be an option that cannot be used here.
            i += 1
        elif a in ("--call-cache", "--no-call-cache"):
            # Accepted here, resolved later by tc.call_cache(): the same rule as the drop flag (that
            # one is read from sys.argv by batch_configs.body()). Listed in usage() because a flag that
            # changes what runs has to be discoverable, and because this parser's job is to make a
            # typo a usage error rather than an unusable option.
            i += 1
        elif a.startswith("--"):
            raise SystemExit(f"unknown option {a!r}   (--help for the options)")
        else:
            argv.append(a); i += 1

    globals()["IMAGES"] = parse_images(specs, resolved()["input_prefix"])
    globals()["CONFIRMED"] = confirm
    # Called for its refusal only, at dispatch, so `--call-cache --no-call-cache` is refused by THIS
    # tool in any mode before anything is built, POSTed or submitted. The value itself is read where it
    # is used -- body() for the body that gets POSTed, submit() for the line printed before compute --
    # so nothing here holds a copy that could disagree with what runs.
    tc.call_cache("rerun")
    if not globals()["IMAGES"]:
        globals()["IMAGES"] = images_from_config(resolved()["input_prefix"])
        if globals()["IMAGES"] and argv and argv[0] != "show":
            print("[rerun] images taken from the profile (set explicitly):")
            for k, v in sorted(globals()["IMAGES"].items()):
                print(f"          {k.split('.')[-1]} = {v.strip(chr(34))}")
    if not globals()["IMAGES"] and argv and argv[0] != "show":
        raise SystemExit(
            "no --image given and no explicit GSVTK_IMAGE_REPO/GSVTK_GATK_IMAGE_REPO in the profile.\n"
            "  A rerun whose images come from workspace attributes cannot prove which code ran.\n"
            "  pass --image KEY=REF for every *_docker input, or name the repo in the profile.\n"
            "  (docs/terra-head-to-head.md)")
    what = argv[0] if argv else "show"
    if len(argv) > 1:
        raise SystemExit(f"one mode at a time; got {' '.join(argv)!r}   (--help)")
    if what == "show":
        announce(resolved(), "show")
        print(json.dumps(make_body(), indent=1))
    elif what in ("create", "validate", "submit", "status"):
        {"create": create, "validate": validate, "submit": submit, "status": status}[what]()
    else:
        usage(2)
