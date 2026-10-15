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

The third thing this tool used to assume is that the config it built is the config that runs. It is
not necessarily: `submit` POSTs a config NAME, so the server runs whatever body the workspace already
holds under `NN-<Wf>-rerun` -- possibly written by another branch, an earlier session, or another
person (docs/plan-launch-any-module.md, the money charter's finding: `submit` ran none of the guards
this file builds). So `submit` now builds the body, reads the workspace copy back with the read-only
`terra.config_payload()`, and refuses before the POST unless the two agree field by field. It POSTs
nothing to make them agree: overwriting a config other submissions read stays `create --confirm`'s
decision. `--allow-unpinned-docker` keeps its documented meaning here (it is an operator decision, not
a guard), and when it is honoured the keys it left workspace-resolved are named on stdout before the
POST, so a submission log can answer "which images were not pinned, and who said so".

Caching is the second thing this tool used to inherit rather than choose. It imports `body()` from
batch_configs.py, so when that builder shipped `"useCallCache": True` this rerun POSTed `true` --
and `show` printed it, but nothing here said it was a choice. A rerun exists to ask "did my change
alter the output", which a cache hit answers before the VM boots, so the shared default is now False
and this file states the mode it resolved (and refuses `--call-cache --no-call-cache` together).

Stating it was not the same as sending it, and that second half was the worse defect: `submit` printed
the mode it read off argv while the submission request carried fiss's own default, so every rerun here
asked Terra for `"useCallCache": true` under a line that said `useCallCache=false` (measured offline at
fiss's request boundary; docs/gap-ledger.md C1). The value now comes from the config body -- the same
`useCallCache` the read-back above grades against the workspace copy -- and travels into the submission
POST, so one value is printed, compared and sent. `terra.submit` refuses a call cache it was not
given, which is what keeps the inherited default from coming back by omission.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
sys.path.insert(0, HERE)
import config  # noqa: E402
import module_profile  # noqa: E402  (ROW_SOURCE_OPERATOR: the one spelling of "no offline row source")
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


def _tag() -> str:
    """This command line's run tag, or the untagged default -- never an empty tag.

    `--tag` absent is "" (the documented default run). `--tag` PRESENT and empty is refused, by the same
    rule `entity()` applies to `--entity ''`: it is what `--tag "$TAG"` looks like with TAG unset, and it
    resolves to the DEFAULT config name and the DEFAULT output attributes -- i.e. the run silently
    overwrites the previous one, through the flag that was typed to stop that. Falling back to the
    default is the failure this tool has already been audited for, so an empty tag is an exit, not a
    default.
    """
    return st.check_tag(_opt("--tag")) if "--tag" in sys.argv else ""


def resolved() -> dict:
    """The per-step data this invocation runs: config name, input prefix, method path, root entity.

    Refuses a step the map has never heard of (naming the steps it does know) and a step whose two
    copies of the root entity disagree -- see `_reconcile`.

    The run tag (`--tag`) is read HERE, next to `--step`, because both decide the config name and the
    name has one definer (`terra/steps.py.rerun`). Every mode, every reader in this file -- `make_body`,
    the read-back, `--overwrite`, the submission call -- then reads that one name off this dict; a tag
    that reached only `show` would print a config the submission does not run.
    """
    if not _RESOLVED:
        r = st.rerun(_opt("--step") or st.DEFAULT_STEP, _tag())
        _reconcile(r)
        r["entity"] = _row_name(r)      # '' when the row cannot be known; `entity()` is what refuses
        _RESOLVED.update(r)
    return _RESOLVED


def _reconcile(r: dict) -> None:
    """Refuse when `steps.py` and `batch_configs.CONFIGS` describe the same step differently.

    The root entity and the workflow name exist in BOTH tables (the chain builder needs them too, and
    docs/terra-head-to-head.md §3 explains why `rootEntityType` there is the stated side). They are no
    longer two copies of one fact: `steps.py` DERIVES the entity type from the step's own
    `this.<etype>_id` read, and the profile's typed `rootEntityType` is the CROSS-CHECK on that answer.
    This is where the two are compared, because this is the path that spends money -- a submission whose
    config states one type and whose `entityType` argument says another boots a fleet and binds nothing,
    and `submit` POSTs both (the body's `rootEntityType` from the map, the submission's from the
    derivation). A step `batch_configs` ships no map for is refused too -- there is no input map to POST,
    so there is no rerun.
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
            + "  The root entity is derived from the step's own `this.<etype>_id` read; "
              "`rootEntityType`\n  is the profile's stated answer, and a disagreement between them is "
              "the drift itself.\n"
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


def _stated(source: str) -> bool:
    """Did the user STATE this value, or did the resolver ARRIVE at it? One source of that answer.

    The bug this function exists to keep fixed: the test used to be `source in ("env", "profile")`,
    and `config.resolve()` labels a profile value `profile:<path>` (measured: `profile:/srv/x.env`)
    and a project-derived one `derived from PROJECT` -- never the bare words. So the `profile` half of
    that tuple matched NOTHING, and a pin named in the profile, which this docstring and
    docs/terra-head-to-head.md §4 both promise counts, never satisfied the guard. Matching the whole
    label was also unreadable: two of the four labels carry data after a colon.

    Measured labels, all four, so this line is not a guess again: `env`, `profile:<path>`,
    `derived from PROJECT`, `unset`. Only the first two are a statement by the user, and refusing the
    last two is the entire purpose -- a registry path the resolver inferred from the project id says
    nothing about which code ran, which is the question a rerun exists to answer.
    """
    return source == "env" or source.startswith("profile:")


def images_from_config(prefix: str = "") -> dict:
    """Fall back to an image the CONFIG LAYER names EXPLICITLY.

    Only an explicit `GSVTK_IMAGE_REPO` / `GSVTK_GATK_IMAGE_REPO` counts, whether it arrives from the
    environment or from a profile file (`_stated`). A value merely *derived* from the project is not a
    statement about which code ran, and guessing here is exactly the failure this tool exists to
    prevent -- so a derived default still stops.

    This route is capped at two keys by construction -- one registry per builder, `sv_pipeline_docker`
    and `gatk_docker` -- while a step's image list is whatever its bindings name, and
    `GATKSVPipelineSingleSample` carries fourteen. So this can never be the whole answer, which is the
    hole the step's own `images` map (`images_from_profile`, below) exists to close.
    """
    res = config.resolve()
    out = {}
    for key, ck in (("gatk_docker", "GATK_IMAGE_REPO"), ("sv_pipeline_docker", "IMAGE_REPO")):
        value, source = res.get(ck, ("", ""))
        # An untagged registry path is not a pin: `.../sv-pipeline` floats to whatever is current at
        # pull time, so the "which code ran" question has no answer afterwards.
        if value and _stated(source) and ":" in value.rsplit("/", 1)[-1]:
            out[f"{prefix}{key}"] = f'"{value}"'
    return out


IMAGES: dict[str, str] = {}
# Which *_docker inputs THIS call named with --image. Kept apart from IMAGES because both a --image ref
# and a profile ref land in the body as the same quoted literal, and `announce` has to say which one
# this rerun is standing on (see _image_provenance).
CLI_IMAGE_KEYS: set = set()
# The image refs THIS step's module profile states, and the ones it explicitly declines to state.
# Populated by the SAME dispatch that fills CLI_IMAGE_KEYS, because in-process callers (probes,
# scripts/selftest.d/rerun.sh) set the pins directly and must be able to fill this the same way.
PROFILE_IMAGE_KEYS: set = set()
FLAG_ONLY_IMAGE_KEYS: set = set()


def images_from_profile(step: str = "") -> tuple:
    """(the refs this step's profile STATES, the keys whose ref it says only --image can state).

    `profiles/<module>.json` -> `images` is the route that scales to a step's whole image list; the
    config layer's two registry keys are not (`images_from_config`). Each entry is either a TAGGED ref --
    a statement about which code ran, which is the only thing that satisfies this file's guard -- or the
    marker `--image`, meaning "nothing offline states this one". Both answers matter: a stated ref
    becomes a pin exactly as `--image` would, and a marker is what lets the refusal say that the profile
    was asked and answered "the flag decides", which is a different sentence from silence.

    Quoted like `parse_images` quotes: a Terra expression needs a quoted literal, and a pin from the
    profile and a pin from the flag must land as the same bytes -- the ONLY difference between them is
    the provenance line, which is the point of keeping the two key sets apart.

    Read off `tc.PROFILE` rather than by loading the profile again: that is the same one read that built
    `CONFIGS`, so the refs and the bindings they override cannot come from two different files.
    """
    pins = tc.PROFILE.image_pins.get(step or resolved()["config"], {})
    stated, flag_only = {}, []
    for key, ref in pins.items():
        if ref == module_profile.IMAGE_SOURCE_OPERATOR:
            flag_only.append(key)
        else:
            stated[key] = f'"{ref}"'
    return stated, sorted(flag_only)
CONFIRMED = False        # set only by an explicit --confirm on the command line
ALLOW_UNPINNED = False   # set only by an explicit --allow-unpinned-docker
OVERWRITE = ""           # set only by --overwrite <the exact config name>; never a default
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


def merge_images(r: dict, cli: dict) -> dict:
    """The pins this call stands on, ONE source per key, and the two key sets `_image_provenance` reads.

    Precedence, most specific last because it overwrites: the two generic config-layer registry keys,
    then the step's own `images` map, then `--image`. Per key, deliberately not all-or-nothing: the
    shipped rule was "any --image means the profile routes are not consulted at all", which is harmless
    for a one-image step and absurd for a fourteen-image one, where overriding the one image you rebuilt
    would then mean repeating thirteen refs you did not change. Nothing is weaker for the change -- a key
    taken from a file rather than from the flag was already a legal pin when no flag was given at all
    (`_stated`), and every key names its own source on the `image pins:` line, which is the guard's own
    answer to "did a stale ref ride in looking like fresh evidence".

    Sets the two provenance globals so the line and the body cannot disagree, and returns the merged map
    so the caller has one assignment to make.
    """
    stated, flag_only = images_from_profile(r["config"])
    merged = dict(images_from_config(r["input_prefix"]))
    merged.update(stated)
    merged.update(cli)
    globals()["PROFILE_IMAGE_KEYS"] = {k for k in stated if k not in cli}
    globals()["FLAG_ONLY_IMAGE_KEYS"] = {k for k in flag_only if k not in cli and k not in merged}
    return merged


def _image_provenance(r: dict) -> str:
    """Where each `*_docker` pin of THIS step came from, stated in every mode.

    Needed because a profile pin and a `--image` pin arrive in the body as the same quoted literal, and
    since `_stated` was fixed both satisfy the guard. After that, the only thing separating a ref the
    user stated for THIS rerun from one that has been sitting in a config file since the last branch is
    this line -- so a stale pin is visible at the moment of submission rather than looking exactly like
    fresh evidence. `--allow-unpinned-docker` makes a workspace attribute a legal answer, so it is named
    here too rather than left to be inferred from its absence.

    THREE sources, named apart because they are three different strengths of statement: `--image` (the
    operator, this call), `profile/images` (the step's own map in profiles/<module>.json: specific to
    this step and this module, but a file that can go stale), and `profile/env`
    (`GSVTK_GATK_IMAGE_REPO`/`GSVTK_IMAGE_REPO`, which can only ever name `gatk_docker` and
    `sv_pipeline_docker`). Precedence runs in that order, per key: the more specific statement wins, and
    the key is labelled with the one that actually made it into the body. A key the step's own map marks
    `--image` prints as `--image only`, which is the profile answering rather than a hole: the step
    was asked and said the flag decides.

    `UNPINNED` on a step whose profile carries no `images` map is still not automatically a bug -- every
    profile written before this field looked exactly like that.
    """
    from_config = set(images_from_config(r["input_prefix"]))
    parts = []
    for key in sorted(k for k in (tc.CONFIGS[r["config"]].get("inputs") or {})
                      if k.endswith("_docker")):
        name = key.split(".")[-1]
        if key in CLI_IMAGE_KEYS:
            src = "--image"
        elif key in PROFILE_IMAGE_KEYS:
            src = "profile/images"
        elif key in FLAG_ONLY_IMAGE_KEYS:
            src = "UNPINNED (--image only, per the step's profile)"
        elif key in from_config:
            src = "profile/env"
        elif key in IMAGES:
            src = "UNKNOWN-SOURCE"      # in the body, from none of the above: say so if reached
        elif ALLOW_UNPINNED:
            src = "workspace attribute (--allow-unpinned-docker)"
        else:
            src = "UNPINNED"
        parts.append(f"{name}={src}")
    return "   ".join(parts)


def announce(r: dict, mode: str) -> None:
    """Say which step this resolved to, on STDERR: `show | jq` must stay valid JSON.

    A rerun that silently reran the wrong step is the defect this file had; the step, the workflow, the
    config name, the root entity and the Dockstore pin are now stated in every mode, and the upstream
    attributes are named so a rerun of a mid-chain step cannot read last run's output and call it a
    result.

    The WRITTEN attribute names are stated next to the config name, in the same block, because both are
    names the run is identified by and both are what `--tag` moves. A tagged run whose config name is
    distinct but whose outputs still land on `*<GSVTK_NEW_SUFFIX>` has not produced a second arm at all
    -- it has produced a fresh run that overwrites the first one's evidence, which is the shape that
    forced a hand-built config outside this tool on 2026-10-14. Printing them is what makes the
    difference checkable without reading the JSON body.
    """
    tag = r.get("tag") or ""
    print(f"[rerun] step {r['step']}  workflow {r['workflow']}  config {r['rerun_config']}"
          + (f"  (--tag {tag})" if tag else "") + "\n"
          f"        root entity {r['root_entity']}  (row: {_row_line(r)})\n"
          f"        Dockstore {r['method_path']} @ {WDL_VERSION or 'GSVTK_BRANCH unset'} "
          f"-- the ref the binding check grades too\n"
          f"        image pins: {_image_provenance(r)}", file=sys.stderr)
    outs = list(r["rerun_outputs"].values())
    print(f"        writes {len(outs)} output attribute(s)"
          + (f", tagged '{tag}' (--tag): " if tag else " (no --tag): ")
          + (", ".join(outs) if outs else "nothing -- this step's profile declares no outputs"),
          file=sys.stderr)
    chain, frozen = _upstream(r)
    if chain or frozen:
        print("        reads "
              + (f"chain outputs of: {', '.join(chain)}" if chain else "no chain outputs")
              + (f"  |  frozen baseline files: {len(frozen)} (published by batch_freeze.py)"
                 if frozen else ""), file=sys.stderr)
        print("        prove those attributes exist first: "
              f"python terra/batch_check_inputs.py --step {r['step']}   ({mode} does not read "
              "your workspace)", file=sys.stderr)


def _run_stem(r: dict) -> str:
    """The stem of this run's dump files: the step, plus the tag when one was named.

    Same defect as the config name, one layer down. `validate` and `submit` already name their dumps per
    step (`rerun_submission.09.json`) because five steps sharing one file means a 07 replaces the 09
    record you came to look at; two tagged runs of ONE step share the step name, so a second tagged run
    would replace the first run's submission record -- the artifact you compare the arms with. With no
    tag the stem is exactly the step, so every existing file name is unchanged.
    """
    return f"{r['step']}.{r['tag']}" if r.get("tag") else str(r["step"])


def _row_name(r: dict) -> str:
    """The row this step submits against, or '' when it cannot be known. Never a guess.

    Two sources, in this order, and no third:
      * `--entity` -- the operator's answer, which always wins;
      * the config-layer key the step's profile `entity_row` names, read through `kit/config.py` so its
        provenance stays the one answer `gsvtk-config show` gives (`BATCH` -> `GSVTK_BATCH` for the
        chain's `sample_set` steps).

    A step whose profile answers `--entity` -- `09-MergeBatchSites`, whose row is a COHORT and no key
    holds one -- has no offline source, so this returns '' and `entity()` is what refuses it. What lived
    here before was `config.get("BATCH") if r["root_entity"] == "sample_set" else ""`: the chain's shape
    written into the launcher, which answered every row outside the chain with "none" and is exactly the
    conditional the `entity_row` field exists to replace.
    """
    stated = _opt("--entity")
    if stated:
        return stated
    return config.get(r["row_source"]) if r["row_source"] else ""


def _row_line(r: dict) -> str:
    """One clause for the resolution line: the row, and what named it -- or what is missing and why.

    Naming the source is the point. "row: all_samples" reads the same whether the operator typed it, the
    profile's key supplied it, or a default filled it -- and only the first two are a statement about the
    run this invocation means.
    """
    name, key = r["entity"], r["row_source"]
    if name and name == _opt("--entity"):
        return f"{name}, named by --entity"
    if name:
        return f"{name} from GSVTK_{key}"
    if key:
        return f"UNRESOLVED: GSVTK_{key} is empty, pass --entity <name>"
    return (f"UNRESOLVED: `entity_row` is {module_profile.ROW_SOURCE_OPERATOR}, "
            "so pass --entity <name>")


def entity(r: dict) -> str:
    """The row to submit against -- or a refusal naming the missing precondition and the fix.

    Submitting a cohort-rooted step with a batch name is how a submission gets accepted, boots a fleet and
    binds nothing, so the missing row is refused by name instead of guessed. The refusal has two arms
    because there are two ways to be missing: the profile states no offline source at all, or it names a
    key that resolves to nothing. Both say what to set, and both are reached before any target resolution,
    so neither can send a request.
    """
    if "--entity" in sys.argv and not _opt("--entity"):
        raise SystemExit(
            "refusing: --entity was given, and it resolved to an empty value.\n"
            "  This is what `--entity \"$ROW\"` looks like when ROW is unset (and what `--entity --confirm`\n"
            "  looks like too). The alternative was to fall through to the workspace's default batch row,\n"
            "  which submits a DIFFERENT cohort than the one asked for, and a submission like that reports\n"
            "  itself as accepted. Drop --entity to take the default deliberately, or name the row.")
    name = _row_name(r)
    if not name:
        key = r["row_source"]
        why = (f"GSVTK_{key}={config.get(key)!r} resolves to an empty value, so the key\n"
               f"      the profile names does not answer either"
               if key else
               f"the profile's `entity_row` for this step is {module_profile.ROW_SOURCE_OPERATOR}: no "
               f"configuration key holds a\n      {r['root_entity']} row, and this command line gave no "
               f"--entity value")
        raise SystemExit(
            f"step {r['step']} ({r['workflow']}) is submitted against a {r['root_entity']} row, and "
            f"nothing\n  names the {r['root_entity']} row to use:\n    {why}.\n"
            f"  pass --entity <name>{f', or set GSVTK_{key}=<name>' if key else ''} "
            f"(the {r['root_entity']} row your workspace holds -- the row the\n"
            f"      {r['step']}-* submissions batch_status.py lists were made against). This tool will "
            f"not guess\n      it: the wrong row binds every this.* input to nothing, and the submission "
            f"still\n      looks accepted.")
    return name


def make_body() -> dict:
    # Resolves the target *now*, and note NS/WS are read through `tc` afterwards: they are
    # module attributes there, so a value that was empty at import time is still picked up.
    tc.require_target()
    r = resolved()
    b = body(r["config"])
    b["name"] = r["rerun_config"]
    # The attribute names this run WRITES, read from `terra/steps.py` rather than edited here. The keys
    # are the workflow's own output names and never move; only the attribute each one lands in does, and
    # `--tag` moves it (`this.foo_new` -> `this.foo_new_TAG`). INPUTS are untouched on purpose: a rerun
    # reads the chain's existing attributes, which is what lets two runs sit side by side over one input
    # set. Without a tag the map is the profile's own, value for value, so the default body is the body
    # it always was -- and the leg that compares this file's output against the pre-`--tag` file checks
    # that claim instead of trusting it.
    outs = dict(r["rerun_outputs"])
    if set(outs) != set(b["outputs"]):
        raise SystemExit(
            f"this step's OUTPUT map is answered two ways, so nothing is POSTed:\n"
            f"  terra/steps.py rerun_outputs({r['step']!r}, {r['tag']!r}) answers {len(outs)} "
            f"output(s),\n"
            f"  batch_configs.body({r['config']!r}) answers {len(b['outputs'])}.\n"
            f"  Keys only the first states: {', '.join(sorted(set(outs) - set(b['outputs']))) or '(none)'}\n"
            f"  Keys only the second states: {', '.join(sorted(set(b['outputs']) - set(outs))) or '(none)'}\n"
            "  One rerun writes the second set and the read-back below grades the first, so the two are\n"
            "  not a style question. Both are built from profiles/<module>.json's `outputs`; make them\n"
            "  agree there, or fix whichever reader stopped reading it.")
    b["outputs"] = outs
    # The Dockstore ENTRY, not the workflow name: `dstore()` builds both `methodPath` and `methodUri` from its
    # argument, and upstream publishes this module's WDL under a different name (see steps.dockstore_of). Using the
    # workflow name here posted a config whose method pointed at an entry Dockstore has never heard of.
    b["methodRepoMethod"] = dstore(WDL_VERSION, st.dockstore_of(r["step"]))
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


# The fields of a method config that decide what a submission RUNS, and so the only fields the
# read-back comparison grades: which config, rooted in which entity, which WDL at which version, what
# it binds, where it writes, whether calls may be served from a cache, whether intermediates survive.
# Two body fields are left out on purpose, both for the same reason -- the body carries them only
# because Rawls rejects a body without them, and the server owns their values: `methodConfigVersion`
# is bumped on every overwrite (batch_configs.py says so above its own `"methodConfigVersion": 1`),
# and `deleted` is a POST-time required member. Comparing either would refuse every config that was
# ever re-POSTed, over a field that decides nothing. `prerequisites`, `maxMessageSize` and `namespace`
# are out for the mirror-image reason: this builder sends them as constants, a read-back is not
# obliged to echo a constant, and a false refusal blocks a correct submission just as effectively as a
# missed one lets a wrong one through -- so the list stays at the fields a rerun actually controls.
# `useCallCache` and `deleteIntermediateOutputFiles` are NOT in this list, and that is a measured
# fact rather than a tidy-up: a real method-config payload's top-level keys are exactly `deleted`,
# `inputs`, `methodConfigVersion`, `methodRepoMethod`, `name`, `namespace`, `outputs`,
# `prerequisites`, `rootEntityType` (captured from a live workspace; see
# `recon-live/live-config-01-SingleSample-mwfix.json` and `scripts/selftest.d/golden/README.md`).
# A method config cannot state a submission option, so a read-back can never echo either of them,
# and `config_diffs` treats a deciding field the copy does not answer as a difference -- with those
# two listed, every `submit` against every real workspace refused unconditionally. Fail-closed, so
# nothing wrong ever POSTed, but a guard that cannot be satisfied is not a check, it is a wall.
# The choice they encode is still enforced, one layer down where it actually lives: `submit` passes
# the body's own `useCallCache` to `terra.submit`, which refuses an unstated choice outright.
DECIDING = ("name", "rootEntityType", "methodRepoMethod", "inputs", "outputs")

# The two maps whose KEYS are the launch surface: a binding the copy holds that this body does not is
# a value the workflow will receive, and one this body holds that the copy lacks is an input the WDL
# resolves from its own default. Those are compared in BOTH directions. Every other field is compared
# on the keys this body states, because a read-back legitimately carries fields the builder never
# POSTed -- the server derives some of them from the ones we send. Extra fields there are printed, not
# refused; a field the two sides disagree about is refused wherever it sits.
BY_KEYS = ("inputs", "outputs")

# A config that drifted everywhere would print a screenful and bury the fix, so the refusal shows this
# many differences and counts the rest.
MAX_DIFF_LINES = 6


def _one_line(value) -> str:
    """A binding on one quoted line, shortened: the refusal has to be readable in a terminal."""
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    return text if len(text) <= 78 else text[:75] + "..."


def config_diffs(b: dict, held: dict) -> tuple:
    """(differences, fields the copy carries that this body does not state) -- empty diffs == agree.

    One direction is not enough and the two directions do not mean the same thing, which is why this
    returns two lists rather than one boolean: a difference in the deciding fields is a refusal, an
    extra server-side field is information.
    """
    diffs: list = []
    extra: list = []
    for field in DECIDING:
        want = b.get(field)
        if field not in held:
            diffs.append(f"{field}: this body states {_one_line(want)}, the workspace copy answers no "
                         f"such field")
            continue
        got = held.get(field)
        if field in BY_KEYS:
            want, got = want or {}, got or {}
            for key in sorted(set(want) | set(got)):
                if key not in want:
                    diffs.append(f"{field}  + {key}: the copy binds {_one_line(got[key])} and this body "
                                 f"binds nothing there")
                elif key not in got:
                    diffs.append(f"{field}  - {key}: this body binds {_one_line(want[key])} and the copy "
                                 f"does not")
                elif want[key] != got[key]:
                    diffs.append(f"{field}  ~ {key}: body {_one_line(want[key])} != workspace "
                                 f"{_one_line(got[key])}")
        elif isinstance(want, dict) and isinstance(got, dict):
            for key in sorted(want):
                if key not in got:
                    diffs.append(f"{field}.{key}: this body states {_one_line(want[key])}, the copy "
                                 f"answers no such key")
                elif want[key] != got[key]:
                    diffs.append(f"{field}.{key}: body {_one_line(want[key])} != workspace "
                                 f"{_one_line(got[key])}")
            extra += [f"{field}.{k}={_one_line(got[k])}" for k in sorted(set(got) - set(want))]
        elif want != got:
            diffs.append(f"{field}: body {_one_line(want)} != workspace {_one_line(got)}")
    return diffs, extra


def assert_matches_workspace(r: dict, b: dict) -> None:
    """Refuse a submission unless the config in the workspace IS the body this invocation built.

    Why the tool needs this: `terra.submit` POSTs a config NAME, so what boots is the body the
    workspace already holds under this step's rerun name -- possibly written by another branch, an
    earlier session, or another person. Everything else in this file pins what THIS call would have
    POSTed, and until now nothing compared that to what it will actually run.

    The read is `terra.config_payload()`: read-only. This function POSTs nothing, because making the
    two agree by overwriting the config other submissions read is `create --confirm`'s decision, not a
    side effect of submitting.

    It fails CLOSED in every direction, because "could not check" is not evidence of agreement: an
    unreadable copy, a 404, a payload that is not an object, and a deciding field the copy does not
    answer are all refusals. The alternative is a fleet of VMs running bytes nobody reviewed.
    """
    where = f"{tc.NS}/{tc.WS} method config {r['rerun_config']}"
    # A 404, an expired application-default token and an unreachable API all land in the same arm, and
    # operationally they are one answer: the copy was not read. Only the 404 has a different cure, so
    # only the 404 is asked for by name.
    try:
        held = terra.config_payload(tc.NS, tc.WS, tc.NS, r["rerun_config"])
    except Exception as exc:
        why = str(exc).strip() or type(exc).__name__
        raise SystemExit(
            "cannot read the config this submission would run, so nothing is submitted.\n"
            f"  GET {where}: {type(exc).__name__}: {why[:200]}\n"
            + ("  The workspace holds no config by that name. `create --confirm` POSTs the body this\n"
               "      tool built -- read it first with `show`. Nothing was submitted.\n"
               if "404" in why else
               "  Agreement between the body built here and the config the server will run is a\n"
               "      precondition, not a hope: fix the read (credentials, target, network) and re-run.\n"
               "      Nothing was submitted.\n"))
    if not isinstance(held, dict) or not held:
        raise SystemExit(
            "cannot compare the config in the workspace against the body built here, so nothing is\n"
            f"  submitted. {where} answered {type(held).__name__}, not a method config: "
            f"{_one_line(held)}\n"
            "  Refusing to read that as agreement. Nothing was submitted.")
    diffs, extra = config_diffs(b, held)
    if diffs:
        shown = diffs[:MAX_DIFF_LINES]
        raise SystemExit(
            f"the config in the workspace is NOT the body this rerun built ({len(diffs)} "
            f"difference(s),\n"
            f"  {len(shown)} shown), and a submission runs the WORKSPACE copy -- this body is only\n"
            f"  what `show` printed. Reading it back: {where}\n"
            + "".join(f"    {d}\n" for d in shown)
            + (f"    and {len(diffs) - len(shown)} more; `show` prints the body this call built\n"
               if len(diffs) > len(shown) else "")
            + ("  (fields only the server states, not compared: " + ", ".join(extra[:4]) + ")\n"
               if extra else "")
            + "  Nothing was submitted. To make them the same bytes: `create --confirm` POSTs exactly\n"
              "  this body (it OVERWRITES a config other submissions read), or re-run `submit` with\n"
              "  the --step/--image/--call-cache flags that describe what is already there.")
    print(f"rerun submit: read back {r['rerun_config']} from {tc.NS}/{tc.WS} -- the config the "
          f"submission\n        will run agrees with the body built here "
          f"({len(b['inputs'])} inputs, {len(b['outputs'])} outputs"
          + (f"; server-side fields not compared: {', '.join(extra[:4])}" if extra else "") + ")")


def refuse_collision(rd) -> None:
    """A config name already in use is a second write under one identity: stop unless repeated.

    `create` used to swallow HTTP 409 and overwrite in the same breath, under the same single
    `--confirm` that authorised the create. That is not an idempotent re-run: overwriting bumps
    `methodConfigVersion`, and a submission already in flight resolves that name, so the write
    changes what somebody else's running job means. Measured precedent in this repo's own handoff
    notes: `10-GenotypeBatch-rerun` was overwritten silently.

    The cure asks the operator to type the name again, which is the point -- an option that means
    "yes, overwrite whatever is there" would be a flag, and a flag is what failed here once.
    """
    name = rd["rerun_config"]
    if OVERWRITE == name:
        print(f"rerun create: overwriting {name} as instructed (--overwrite names it)")
        return
    try:
        held = terra.config_payload(tc.NS, tc.WS, tc.NS, name)
        detail = ("  the copy there now: methodConfigVersion=%s, %d inputs"
                  % (held.get("methodConfigVersion"), len(held.get("inputs") or {})))
    except Exception as exc:                    # unreadable is a reason to refuse, not to proceed
        detail = f"  could not read the copy there now ({exc}); refusing rather than guessing"
    raise SystemExit(
        f"create: {tc.NS}/{tc.WS} already has a method config named {name}.\n{detail}\n"
        "  Overwriting it bumps methodConfigVersion, and a submission already in flight reads that\n"
        "  name, so this write changes what somebody else's running job means. It is not a re-run.\n"
        f"  If that is what you mean, repeat the name: --overwrite {name}")


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
        refuse_collision(rd)                    # returns only when --overwrite repeats the name
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
    terra.dump(d, str(config.work_dir("metadata") / f"rerun_config_validation.{_run_stem(rd)}.json"))
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
    # Build the body `show`/`create` would POST, for two reasons, and the second is the reason this is
    # not a one-liner. First: the unpinned/untagged image refusal lives in `make_body()`, and until now
    # the money path was the one path that never called it (docs/plan-launch-any-module.md's money
    # charter measured exactly that: `show` refusing over `sv_base_mini_docker` while `submit --confirm`
    # carried on). Second: the body is the only thing the read-back below can be compared against.
    b = make_body()
    # The image exemption, named before the POST rather than left in argv. `--allow-unpinned-docker` is
    # a documented operator decision with its own usage text and its own section in
    # docs/terra-head-to-head.md §4, so submit honours it -- and says out loud which keys it left
    # resolved by the workspace, because a submission log has to answer "which images were not pinned,
    # and who said so" without anyone re-deriving it from a command line. `make_body()` is what keeps
    # this list empty when the flag was NOT given, so a non-empty list here always has an author.
    exempt = sorted(k for k in b["inputs"] if k.endswith("_docker") and k not in IMAGES)
    if exempt:
        print("rerun submit: --allow-unpinned-docker authorised these *_docker inputs to stay\n"
              "        workspace attributes, so this rerun does NOT pin them: "
              + ", ".join(exempt))
    # The agreement, before the POST: a submission runs the config the WORKSPACE holds, so that copy is
    # read back and compared to the body above. It runs on the bytes that will actually run, exemption
    # included -- which is also why an unpinned workspace copy still gets refused when this call pinned
    # the image: that is a field difference, and the first half of the guard catches it.
    assert_matches_workspace(rd, b)
    # Named before the fleet boots, the same way the images are: a rerun whose calls were served from a
    # cache did not run the code you came to measure, and its output still looks like a pass. Read off
    # the body built above -- the same `useCallCache` the read-back just graded against the workspace
    # copy -- rather than from a second read of argv, so the line printed here and the value the
    # submission POSTs are ONE value. They used to be two reads and the POST was not this one: fiss's
    # create_submission defaults use_callcache=True and always states it, so every submission here
    # carried `true` while this line printed `false` (docs/gap-ledger.md C1). `terra.submit` now refuses
    # a choice it was not given, so the inherited default cannot come back by omission.
    # The row itself, last before the POST. Rawls ACCEPTS a submission naming an entity that does not exist:
    # the POST answers 200, the workflows fail per-call, and the money is spent discovering that. Measured on
    # today's two candidate targets -- `sample/NA12878` is HTTP 200 in one workspace and HTTP 404 in the
    # other, which is the whole difference between a run and a receipt. It sits HERE rather than at the top of
    # submit() because everything decidable offline (unpinned images, the row source, the target, the read-back
    # agreement) must refuse before this tool makes even a read, and a leg that asserts "refused before it read
    # anything" is the thing keeping that order honest.
    try:
        have_row = terra.entity_exists(tc.NS, tc.WS, rd["root_entity"], row)
    except Exception as exc:
        raise SystemExit(f"rerun submit: cannot check whether the {rd['root_entity']} row {row!r} exists in "
                         f"{tc.NS}/{tc.WS} ({exc}).\n"
                         "  Refusing rather than POSTing a submission whose entity is unknown: 'could not "
                         "check' is not\n  evidence the row is there.")
    if not have_row:
        raise SystemExit(f"rerun submit: {tc.NS}/{tc.WS} has no {rd['root_entity']} row named {row!r}.\n"
                         "  Terra would accept this submission anyway and then have nothing to run, per "
                         "workflow, at\n  compute cost. Name a row that exists (--entity, or the row source "
                         "named by the profile's\n  `entity_row`), or point GSVTK_TERRA_WORKSPACE at the "
                         "workspace that holds it.")
    cache = b["useCallCache"]
    print(f"rerun submit: {rd['rerun_config']} against {row} ({rd['root_entity']}), "
          f"useCallCache={str(cache).lower()} -- "
          + ("calls whose command+inputs match may be served from the cache"
             if cache else "every call re-runs, which is the whole point of a rerun"))
    d = terra.submit(tc.NS, tc.WS, tc.NS, rd["rerun_config"], row, rd["root_entity"], None,
                     confirm=True, use_call_cache=cache)
    print(json.dumps(d, indent=1)[:600])
    terra.dump(d, str(config.work_dir("metadata") / f"rerun_submission.{_run_stem(rd)}.json"))


def status() -> None:
    announce(resolved(), "status")
    tc.require_target()
    subs = terra.submissions(tc.NS, tc.WS, limit=8)["submissions"]
    for s in subs:
        print(" ", s.get("submissionId"), "|", s.get("status"), "|", s.get("submissionDate"),
              "| externalIds:", s.get("externalIds"))


def usage(code=0):
    print(f"""usage: batch_rerun_step.py [--step NN] [--tag TAG] [--image KEY=REF ...] [show|create|validate|submit|status]

Reruns ONE step of the batch chain with the images pinned literally into the config, so
the run cannot inherit a stale image from a workspace attribute. The step, its workflow,
its config name, its input-key prefix and its root entity all come from terra/steps.py.

  --step NN         one of {', '.join(st.known_steps())} (default {st.DEFAULT_STEP}):
                    {', '.join(f'{s}={st.step_names()[i]}' for i, s in enumerate(st.known_steps()))}
                    Every mode takes it. It prints what it resolved to, on stderr, in every mode.
  --tag TAG         name THIS run, so a second rerun of one step does not overwrite the first. It
                    moves exactly two things, both printed by `show`:
                      the config name   NN-<Workflow>-TAG      (untagged: NN-<Workflow>-rerun)
                      the outputs       this.<attr>_TAG        (untagged: this.<attr>_new)
                    INPUTS DO NOT MOVE: the run still reads the chain's existing frozen (`_frz`) and
                    chain (`_new`) attributes, so a tagged run and the run beside it answer the same
                    question from the same input. That is the point -- `submit` POSTs a config NAME, so
                    an untagged second rerun POSTs over the first config AND writes over the first
                    run's output attributes, and two arms cannot be compared afterwards. The tag is
                    letters/digits/underscore (it becomes part of an attribute name), and `rerun` is
                    refused because it spells the name the untagged run already owns.
                    The read-back `submit` does before the POST compares the TAGGED config, and
                    --overwrite must repeat that same tagged name.
  --entity NAME     the row to submit against. Defaults to the config key the step's profile
                    `entity_row` names (GSVTK_BATCH for the chain's sample_set steps). Required --
                    and refused rather than guessed -- for a step whose `entity_row` is `--entity`,
                    which is how a profile says no configuration key holds that row (09 is one:
                    its row is a sample_set_set cohort, and GSVTK_BATCH names a sample_set).
  --image KEY=REF   repeat once per *_docker input, e.g.
                    --image gatk_docker=us.gcr.io/PROJ/NS/gatk:TAG
                    --image sv_pipeline_docker=us.gcr.io/PROJ/NS/sv-pipeline:TAG
                    A step that carries more than the two images the config layer can name
                    (GSVTK_GATK_IMAGE_REPO, GSVTK_IMAGE_REPO) can state its refs once in its own
                    profile -- profiles/<module>.json -> steps[].images -- and then needs no flag at
                    all, or only the one flag overriding one image. Per key the order is --image,
                    then that map, then the config layer, and `show` says which one every pin came
                    from. An entry there may also say `--image`, which states no ref: it is the step
                    answering that only this flag decides that one.
  show              print the config body that would be POSTed (no mutation)
  create / validate POST it, then ask Terra to typecheck it
                    (create needs --confirm and refuses the shared baseline workspace unless
                     --allow-shared-target, exactly like batch_configs.py create)
  submit --confirm  start it (real compute; --confirm is mandatory). Before the POST it builds the
                    body, reads this step's rerun config BACK out of the workspace with the read-only
                    config_payload(), and refuses unless the two agree field by field -- a submission
                    runs the config the workspace HOLDS, which may have been POSTed by another branch,
                    an earlier session or another person. Nothing is POSTed to make them agree: that is
                    `create --confirm`'s decision. An unreadable copy, a 404 and a field the copy does
                    not answer all refuse too: "could not check" is not evidence of agreement.
  --allow-unpinned-docker   let a *_docker input resolve from a workspace attribute (off by
                            default: it breaks reproducibility, so it has to be asked for). submit
                            honours it and names the keys it left workspace-resolved before the POST,
                            so a submission log says which images were not pinned and who said so.
  --overwrite CONFIG        `create` only, and it must repeat the exact config name. A name already in
                            the workspace makes Terra answer 409; that used to be swallowed into a
                            silent overwrite, which bumps methodConfigVersion on a config a submission
                            in flight is reading. Nothing overwrites unless the name is typed twice.
  --drop-branch-only-inputs  build the config for a ref that does NOT declare this branch's extra
                            inputs (e.g. GSV_WDL_VERSION=main) by dropping them. A SEMANTIC change:
                            it prints what it dropped and which ref it compared.
  --call-cache / --no-call-cache  whether Cromwell may reuse call outputs. Off unless asked for, and
                            this tool now says so instead of inheriting batch_configs' default: a
                            cached call never ran your image. The value `submit` prints is the value the
                            submission POSTs (both read off the built body; `terra.submit` refuses a
                            choice it was not given, because fiss defaults that field to true).
                            (docs/terra-head-to-head.md §5, and the cross-workspace cache copy that
                            timed out mid-run in docs/handoff/003.)
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
        elif a == "--tag":
            # Validated at the parser, like `--step`: a tag is a name that reaches a Terra config name and
            # every output attribute this run writes, so a typo is a usage error here rather than a config
            # POSTed under a mangled name. An EMPTY value is refused rather than read as "no tag": that
            # spelling resolves to the default name, which is the overwrite the flag was typed to prevent.
            if i + 1 >= len(rest) or not rest[i + 1].strip() or rest[i + 1].startswith("--"):
                raise SystemExit("--tag wants a run name (letters, digits, underscore: `h2h`, `rc1`).\n"
                                 "  An empty tag resolves to the DEFAULT config name and outputs, so the run\n"
                                 "  overwrites the one already there -- `--tag \"$TAG\"` with TAG unset is a\n"
                                 "  refusal, not a default. Drop --tag to take the default deliberately.")
            st.check_tag(rest[i + 1])
            i += 2
        elif a == "--entity":
            if i + 1 >= len(rest):
                raise SystemExit("--entity wants a row name. The row's TYPE is the step's root "
                                 "entity; `python terra/steps.py`\n  prints it per step (--help does "
                                 "not resolve a profile, so it cannot name it here)")
            i += 2
        elif a == "--overwrite":
            if i + 1 >= len(rest) or not rest[i + 1].strip():
                raise SystemExit("--overwrite wants the exact config name you are about to replace, "
                                 "not a yes.\n  It is printed by `show` and by the refusal itself.")
            globals()["OVERWRITE"] = rest[i + 1]; i += 2
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
    globals()["CLI_IMAGE_KEYS"] = set(IMAGES)
    globals()["CONFIRMED"] = confirm
    # Called for its refusal only, at dispatch, so `--call-cache --no-call-cache` is refused by THIS
    # tool in any mode before anything is built, POSTed or submitted. The value itself is read where it
    # is used -- body() for the body that gets POSTed, submit() for the line printed before compute --
    # so nothing here holds a copy that could disagree with what runs.
    tc.call_cache("rerun")
    # The three pin sources, merged PER KEY (see `merge_images`): --image, then this step's `images` map,
    # then the two config-layer registry keys. Anything the command line did not name is said out loud,
    # with the route it came from, because a ref from a file is weaker evidence than one stated now and
    # the only thing that keeps it from looking like fresh evidence is naming it.
    globals()["IMAGES"] = merge_images(resolved(), IMAGES)
    taken = {k: v for k, v in globals()["IMAGES"].items() if k not in CLI_IMAGE_KEYS}
    if taken and argv and argv[0] != "show":
        print("[rerun] image pins taken from the profile, not from this command line:")
        for k, v in sorted(taken.items()):
            print(f"          {k.split('.')[-1]} = {v.strip(chr(34))}"
                  f"  ({'profile/images' if k in PROFILE_IMAGE_KEYS else 'profile/env'})")
    if not globals()["IMAGES"] and argv and argv[0] != "show":
        flag_only = sorted(k.split(".")[-1] for k in FLAG_ONLY_IMAGE_KEYS)
        raise SystemExit(
            "nothing states these image refs, so nothing is built.\n"
            + (f"  this step's profile names {len(flag_only)} *_docker input(s) and marks every one of\n"
               f"  them {module_profile.IMAGE_SOURCE_OPERATOR!r} -- it is saying the flag decides, and no flag was given:\n"
               f"      {', '.join(flag_only)}\n"
               if flag_only else
               "  no `images` entry in this step's profile, and no tagged\n"
               "  GSVTK_IMAGE_REPO/GSVTK_GATK_IMAGE_REPO in the config layer.\n")
            + "  A rerun whose images come from workspace attributes cannot prove which code ran.\n"
              "  pass --image KEY=REF for every *_docker input, or state the tagged ref once per step in\n"
              "  profiles/<module>.json -> steps[].images, or name the repo in the config layer.\n"
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
