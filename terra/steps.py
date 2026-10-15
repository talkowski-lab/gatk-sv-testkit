#!/usr/bin/env python3
"""The step -> workflow map, in one reader.

Three tools each carried their own copy of this table -- `WDLS` in `batch_check_inputs.py`, `STEPS` in
`batch_save_metadata.py`, `STEPS` in `batch_status.py` (the three docs/module-profiles.md §9.4 names) --
so a step added for one of them stayed invisible to the other two. What makes this a file rather than a
dict is the two lookups those copies did. Both were this repo's named failure class, an empty that reads
like nothing was wrong:

  * `next(c for c in CONFIGS if c.startswith(step + "-"))` raised a bare `StopIteration` for a step no
    config carries that number -- no step name, no candidates it tried, no exit code to act on.
  * `name.startswith(step + "-")` can never match an un-numbered chain name, so a workspace holding a
    config called `GenotypeBatch` answers "no config for step 10" and the caller moves on
    (`fetch_baseline.py` prints `!! no config for step` and writes a manifest with no entry for it).

`match_configs` therefore tries the exact numbered name, then the number prefix, then the bare workflow
name -- and prefers the exact one, because a finished submission leaves a per-submission snapshot copy of
its config behind (`<config>_<random>`, docs/terra-head-to-head.md §8) which shares the `NN-` prefix, so
`sorted(matches)[0]` over a prefix can hand back the frozen copy instead of the live config.

Both lookups fail with a sentence naming the step and everything it tried, as `SystemExit`: that is how
the sibling tools in this directory already report an unusable argument (`batch_check_inputs`'s womtool
message, `batch_save_metadata`'s four-flag explicit-target rule), so a reader gets the sentence and an
exit code rather than a traceback or a shrug.

The MAP is not in this file: `STEPS`, `RERUN_ROOT_ENTITY` and `RERUN_ROW_SOURCE` are derived at import
from `profiles/<module>.json`, the same read `batch_configs.CONFIGS` is built from, so a step is added in
one place and one refusal path covers a profile that will not load. What stays here is the two lookups,
the default step, and the refusals.

    python terra/steps.py        # print the map (offline: no config, no credentials, no network)
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
import module_profile  # noqa: E402  (the reader of profiles/<MODULE>.json; kit/ is on sys.path)

# Steps 06->10 are the batch half of the joint-calling chain. The keys are the number the workspace's
# method configs are prefixed with -- the same number batch_configs.CONFIGS keys on -- and the values
# are the WDL/workflow names Dockstore serves at github.com/broadinstitute/gatk-sv/<name>.
#
# That last segment is the workflow NAME, not the WDL file basename, and the two are not the same
# thing upstream. Measured (read-only, `git -C "$GSVTK_GATK_SV_CHECKOUT" archive <ref> wdl | tar -t`
# plus the `workflow <name>` declaration inside each file) at two refs, a branch head and origin/main:
# 12 of the 109 WDLs that declare a top-level workflow declare a name other than their file's basename
# -- wdl/DepthClustering.wdl declares `ClusterDepth`, wdl/PloidyEstimation.wdl declares `Ploidy`,
# wdl/Genotype_2.wdl declares `Regenotype` (the same twelve at both refs). For the five steps below it
# was checked per step and each one AGREES: `wdl/GenerateBatchMetrics.wdl` declares
# `workflow GenerateBatchMetrics`, and likewise FilterBatchSites, FilterBatchSamples, MergeBatchSites
# and GenotypeBatch. So `workflow()` may serve as that path segment for 06->10 -- and a step added to
# this map whose file and workflow differ must carry the NAME here, because the basename would 404
# against Dockstore (`Cannot get dockstore://... from method repo`).
# The two tables below are DERIVED from `profiles/<module>.json`, the same file `batch_configs.CONFIGS`
# is built from, so the step -> workflow -> root-entity triple now has one home. They were literals here
# while that JSON carried the identical three fields per step -- docs/module-profiles.md §9.4's "collapse,
# don't duplicate", and the reason `batch_rerun_step` carries a `_reconcile` that refuses when the two
# copies disagree. That reconciliation is now a check on one source rather than an arbitration between
# two, and `scripts/selftest.d/rerun.sh` pins the agreement step by step, before and after this move.
#
# What the profile states, verbatim: `steps[].step` (`06-GenerateBatchMetrics`), `workflow`,
# `rootEntityType` (the cross-check, below), `entity_row` (where the row NAME comes from). The step NUMBER
# is the two-digit prefix of `step`, the key batch_configs keys CONFIGS on; an entry whose key does not
# start with one is refused below rather than skipped, because a step map missing a step reads exactly
# like a chain that does not contain it.
#
# What is NOT copied verbatim: the root entity. `rootEntityType` used to be the answer here and it is now
# the cross-check on an answer derived from the step's own bindings -- see `_tables`.
_PROFILE = module_profile.load()


def _tables(profile):
    """`(step -> workflow, step -> root entity, step -> row source)` from the module profile.

    Never an empty map. An empty one answers "no such step" to every question in this file, which is the
    shape the whole module argues against, and it would arrive silently: `known_steps()` becomes argparse
    `choices`, so an empty table turns into a tool that accepts no step at all.

    The root entity is DERIVED, not copied: it is the single `this.<etype>_id` read in the step's own
    binding map (`module_profile.derive_entity_type`), because that key is the one thing in the profile
    that states which row the step runs on -- and it is the same rule `checks/terra_entity_check.py`
    applies to upstream's launch configs, so the launcher and the checker answer from one rule rather
    than two. The profile's typed `rootEntityType` is now the CROSS-CHECK on that answer, not the answer:
    `batch_rerun_step._reconcile` refuses on the money path when the derived type and the stated one
    disagree, and `rerun.sh` refuses in CI for every other reader. A profile that states a type its own
    bindings contradict is described two ways, and which one wins decides what runs.
    """
    if profile.problems or not profile.configs:
        raise SystemExit(
            f"terra/steps.py cannot build the step map: {profile.path} gave no usable steps.\n"
            f"  {profile.problem or 'the profile carries no steps[] entries'}\n"
            "  Every tool in this directory reads the step map from here, so none of them run until the\n"
            "  profile loads; `python kit/module_profile.py validate` names the field at fault.")
    names: dict[str, str] = {}
    roots: dict[str, str] = {}
    rows: dict[str, str] = {}
    for key, spec in profile.configs.items():
        number, _, tail = str(key).partition("-")
        if len(number) != 2 or not number.isdigit():
            raise SystemExit(
                f"module profile step {key!r} does not start with a two-digit step number, so it has\n"
                f"  no key in the step map (it names {tail!r} as its workflow). Every tool matches\n"
                f"  workspace method configs on that number, so an unnumbered entry is invisible to all\n"
                f"  of them -- add the number, do not delete the entry.")
        names[number] = str(spec["workflow"])
        roots[number] = _require_derived_entity(key, spec)
        rows[number] = profile.row_sources.get(key, "")
    return names, roots, rows


def _require_derived_entity(key: str, spec: dict) -> str:
    """The entity type a step's own bindings state, or an exit that names what was looked for.

    Zero and many are different refusals, because their cures are. Zero means the step reads no name key
    at all -- so nothing in the profile states which row it runs on, and the typed `rootEntityType` is
    the only candidate left, which is exactly the value this derivation stopped trusting. Many means the
    bindings name two rows, and picking one is a guess with a submission behind it.
    """
    values = list((spec.get("inputs") or {}).values())
    etype, found = module_profile.derive_entity_type(values)
    if etype:
        return etype
    reads = sorted({v for v in values
                    if isinstance(v, str) and v.startswith("this.")})
    if not found:
        raise SystemExit(
            f"module profile step {key!r} ({spec.get('workflow')}) reads no `this.<etype>_id` name key,\n"
            f"  so nothing in it states which entity row the step runs on.\n"
            f"  the `this.*` reads it does carry ({len(reads)}): {', '.join(reads) or '(none)'}\n"
            "  A member read (`this.sample_sets.cutoffs`) names the MEMBER type, not the row: that is the\n"
            "  rule that came out wrong for 09-MergeBatchSites, which reads `this.sample_sets.<attr>`\n"
            "  three times and `this.sample_set_set_id` once. Bind the row's own name key, or launch this\n"
            "  step from a profile that does -- `rootEntityType` alone is no longer enough, because a type\n"
            f"  that nothing in the config reads resolves every this.* binding to nothing at runtime.\n"
            f"  (profile: {_PROFILE.path})")
    raise SystemExit(
        f"module profile step {key!r} ({spec.get('workflow')}) reads more than one entity type as\n"
        f"  `this.<etype>_id`: {', '.join(found)}. Which row it runs on decides what a submission boots,\n"
        f"  and the profile states {len(found)} answers, so neither one is trusted here.\n"
        f"  (profile: {_PROFILE.path})")


_TABLES = _tables(_PROFILE)
STEPS: dict[str, str] = _TABLES[0]


# The step a caller gets when it does not say: `10-GenotypeBatch`, the stage the rerun tool was
# written for. Kept here rather than repeated as a literal in a tool's flag default, so "which step
# is the default" has one answer in the repo.
DEFAULT_STEP = "10"

# The root entity each step is submitted against -- per-step data a rerun cannot invent. It is NOT
# uniform: `09-MergeBatchSites` is rooted in `sample_set_set` (one row per cohort; its inputs read
# `this.sample_sets.<attr>`, one value per member sample_set), while 06/07/08/10 are rooted in
# `sample_set` (one row per batch). Submit against the wrong type and the batch-level `this.*`
# bindings resolve to nothing -- at runtime, after the fleet booted, in a message that names no key
# (docs/terra-head-to-head.md §3).
#
# It is no longer a second copy, and it is no longer even copied: the value is DERIVED from the step's
# own `this.<etype>_id` read (`_tables`), and the profile's typed `rootEntityType` is the cross-check on
# it. `batch_rerun_step._reconcile` is where the two are compared -- on the launch path, where a wrong
# type is a submission rather than a red test -- and `rerun.sh` compares them for every other reader.
# A disagreement is a refusal either way; it is the kind of drift that made this repo's named failure
# class in the first place.
RERUN_ROOT_ENTITY: dict[str, str] = _TABLES[1]

# Where each step's entity ROW NAME comes from: the config-layer key its profile's `entity_row` names
# (`BATCH` -> `GSVTK_BATCH`), or "" when the profile says nothing offline holds one. `""` is not an empty
# answer -- it is the answer "only `--entity` can name this row", and `batch_rerun_step.entity()` is what
# refuses it. This table is why `_row_name` no longer asks "is the type sample_set?": that question was
# the chain's shape written into the launcher, and it answered every row outside the chain with "none".
RERUN_ROW_SOURCE: dict[str, str] = _TABLES[2]


# What the rerun config's last segment is when the operator does not name one: `rerun`, so the
# default name is `10-GenotypeBatch-rerun`. `--tag TAG` replaces this segment (it does not append to
# it), which is why the no-flag path needs no special case: the tag IS the last segment, and its
# default spelling is this literal. Kept here rather than in `batch_rerun_step.py` for the reason the
# whole file exists: the config name has one definer, and a name spelled in two places is the drift
# every leg in `scripts/selftest.d/rerun.sh` was built to catch.
RERUN_TAG_DEFAULT = "rerun"

# A tag is pasted into TWO Terra identifiers: the config name (`10-GenotypeBatch-TAG`) and the tail of
# every output attribute this run writes (`this.foo_new_TAG`). Attribute names are the stricter of the
# two -- a hyphen or a dot survives in a config name and is refused (or silently mangled) as an
# attribute -- so the tag takes the intersection of what both accept: letters, digits, underscore.
_TAG_OK = re.compile(r"[A-Za-z0-9_]+\Z")


def check_tag(tag: str) -> str:
    """A run tag from a command line, or a refusal naming what a tag has to be.

    Called only where `--tag` WAS given: an absent flag is the documented default (`rerun()` with no
    tag), while `--tag ""` is an unset shell variable arriving as a name, which is the `--entity ''`
    shape this file already refuses elsewhere. An empty tag would resolve to the default config name
    while reading like a request for a distinct run, and the run would overwrite the previous one --
    the exact defect the flag exists to prevent, arriving through the flag meant to prevent it.
    """
    if not tag or not tag.strip():
        raise SystemExit(
            "refusing: --tag was given, and it resolved to an empty value.\n"
            "  This is what `--tag \"$TAG\"` looks like when TAG is unset. An empty tag resolves to the\n"
            "  DEFAULT config name and the DEFAULT output attributes, so the rerun would overwrite the\n"
            "  run already sitting there -- which is the thing --tag is for. Name the run, or drop --tag.")
    text = tag.strip()
    if not _TAG_OK.match(text):
        raise SystemExit(
            f"--tag {tag!r} is not usable as a run name.\n"
            "  A tag is pasted into two Terra identifiers: the method config name\n"
            "  (`NN-<Workflow>-TAG`) and the tail of every output attribute this run writes\n"
            "  (`this.<attribute>_TAG`). Attribute names accept letters, digits and underscore only,\n"
            "  so a hyphen, dot, slash or space survives the config name and is refused by the\n"
            "  attribute -- a failure at submission time, after the fleet booted. Use `h2h`, `rc1`,\n"
            "  `2026_10_14`; a hyphen is the character that fails most often.")
    if text == RERUN_TAG_DEFAULT:
        raise SystemExit(
            f"--tag {text!r} is refused: it builds the config name an UNTAGGED rerun already uses\n"
            f"  (`NN-<Workflow>-{RERUN_TAG_DEFAULT}`), so the run would overwrite the previous one --\n"
            "  the defect this flag exists to remove, arriving behind the flag typed to prevent it.\n"
            "  Name the arm instead: `main`, `rc1`, `2026_10_14`.")
    return text


def rerun_suffix(tag: str = "") -> str:
    """The attribute-name suffix a rerun writes: `_TAG`, or "" for the untagged default.

    One spelling, read by `rerun_outputs()` and by every leg that asserts what a run writes. "" (not
    `"_"`) for no tag is what keeps the no-flag path byte-identical to the profile's own map.
    """
    return f"_{tag}" if tag else ""


def rerun_outputs(step: str, tag: str = "") -> dict:
    """The OUTPUT map a rerun of `step` writes, with the tag applied to every attribute name.

    The output half of `rerun()`'s answer, derived here for the same reason the config name is: the
    profile states each output once (`this.<attribute>{new}`), and a second definition of the written
    name -- one in the launcher, one in the tool that reads a run back -- is the drift this file exists
    to prevent. Keys (the workflow's own output names) never move; only the attribute each one lands in
    does. INPUTS are deliberately untouched here: a rerun reads the chain's existing attributes, so
    tagging outputs while leaving inputs alone is what lets two runs sit side by side over one input.
    """
    spec = _PROFILE.configs.get(f"{step}-{workflow(step)}") or {}
    suffix = rerun_suffix(tag)
    return {k: f"{v}{suffix}" for k, v in (spec.get("outputs") or {}).items()}


def known_steps() -> list[str]:
    """The step numbers this map knows, for argparse `choices`."""
    return sorted(STEPS)


def workflow(step: str) -> str:
    """The workflow a step number runs, naming the step when the map has never heard of it."""
    try:
        return STEPS[step]
    except KeyError:
        raise SystemExit(
            f"unknown step {step!r}: this map knows {', '.join(known_steps())} "
            f"({', '.join(STEPS.values())}). The map comes from the module profile "
            f"({_PROFILE.path});\n  add the step there -- every tool reads it from here.")


def dockstore_of(step: str) -> str:
    """The Dockstore ENTRY a step's method resolves from -- not necessarily its workflow name.

    Equal to `workflow()` for every step whose WDL is published under its own file name, which is every step of
    the batch chain; that equality is why the distinction stayed hidden until a module where the two differ:
    upstream publishes `/wdl/GATKSVPipelineSingleSample.wdl` as `SingleSamplePipeline`
    (.github/.dockstore.yml:256-257), and the live golden's `methodRepoMethod.methodUri` says the same. A launch
    that used the workflow name here would POST a config pointing at a Dockstore entry that does not exist --
    which Terra accepts, then fails downstream. Never invented: no `dockstore` in the profile means `workflow`.
    """
    for name, cfg in _PROFILE.configs.items():
        if name.startswith(f"{step}-"):
            return str(cfg.get("dockstore") or cfg.get("workflow") or workflow(step))
    return workflow(step)


def step_names() -> list[str]:
    """`["06-GenerateBatchMetrics", ...]` -- the numbered chain in launch order."""
    return [f"{s}-{STEPS[s]}" for s in known_steps()]


def root_entity(step: str) -> str:
    """The entity type `step` is submitted against, or an exit -- never a guessed `sample_set`.

    The value is derived from the step's bindings at import (see `_tables`), so the two ways this can
    fail are both refusals by then: a profile whose step reads no name key, or reads two, exits there
    naming what was looked for. What is left is a step the map does not carry at all. Defaulting that to
    the majority type would submit it against the wrong row and empty every `this.*` binding, which is the
    exact defect this table exists to prevent, so it is a refusal naming the step and the file to fix.
    """
    try:
        return RERUN_ROOT_ENTITY[step]
    except KeyError:
        raise SystemExit(
            f"step {step} ({workflow(step)}) has no rootEntityType in the module profile "
            f"({_PROFILE.path}),\n  so nothing can be submitted against it.\n"
            f"  Known: {', '.join(f'{k}={v}' for k, v in sorted(RERUN_ROOT_ENTITY.items()))}.\n"
            "  The value is derived from the step's own `this.<etype>_id` read, so the entry to add or "
            "fix is\n  the step in that profile (its `rootEntityType` is the cross-check, not the source).\n"
            "  Guessing `sample_set` is not a fallback: 09-MergeBatchSites is `sample_set_set`, and a "
            "wrong\n  type resolves every this.* binding to nothing at runtime, after the VMs booted.")


def row_source(step: str) -> str:
    """The config key that names `step`'s row, or "" when only the operator can name it.

    Never a refusal: "no offline source" is a legal answer and a common one (the cohort row of a
    `sample_set_set` step), and the tool that needs the row is the one that has to say so. An unknown
    step is not asked about here -- `rerun()` resolves the step first, which is where that refusal lives.
    """
    return RERUN_ROW_SOURCE.get(step, "")


def rerun(step: str, tag: str = "") -> dict:
    """Everything a rerun of `step` needs, derived here: config name, input prefix, method path.

    `terra/batch_rerun_step.py` used to hold these as module-level literals for step 10 only, which
    meant "rerun 08" silently reran 10. Deriving them from this map is what makes the five steps one
    code path: the prefix, the Dockstore path and the config name are all the workflow name in a
    different coat, and the workflow name lives here.

    `tag` is the optional run name (`--tag`) and it moves exactly two answers, both derived here:
    the config name (`NN-<Wf>-TAG` instead of `NN-<Wf>-rerun`) and the output attributes (every value
    in `rerun_outputs` gains `_TAG`). Everything else -- input map, input prefix, method path, root
    entity, row source -- is the same run read from the same profile, because a tagged rerun is the
    SAME question asked a second time, not a different question. With no tag the dict is what it always
    was, byte for byte: `tag` is "", the name ends in `rerun` (`RERUN_TAG_DEFAULT`), the suffix is "".
    """
    wf = workflow(step)
    tag = check_tag(tag) if tag else ""
    return {"step": step, "workflow": wf,
            "config": f"{step}-{wf}",                        # the name batch_configs' CONFIGS keys on
            # The name the rerun POSTs. `--tag` replaces the last segment rather than appending to it,
            # so the default is this map's own default: `RERUN_TAG_DEFAULT` is what sits there when no
            # tag is given. Tagging by APPENDING (`-rerun-TAG`) would leave two spellings of one
            # pattern for the readers to agree on, which is the shape of the bug in the first place.
            "rerun_config": f"{step}-{wf}-{tag or RERUN_TAG_DEFAULT}",
            "tag": tag,                                      # '' == the untagged default run
            "rerun_suffix": rerun_suffix(tag),               # what every OUTPUT attribute ends with
            "rerun_outputs": rerun_outputs(step, tag),       # the attribute names this run writes
            "input_prefix": f"{wf}.",                         # the prefix of every input key
            # The Dockstore entry, not the WDL name: upstream publishes /wdl/GATKSVPipelineSingleSample.wdl as
            # `SingleSamplePipeline` (.github/.dockstore.yml:256-257), and the live golden's methodUri agrees.
            # For every chain step the two are equal, which is why this only shows up with the second module --
            # and it shows up as a config pointing at a Dockstore entry that does not exist.
            "method_path": f"github.com/broadinstitute/gatk-sv/{dockstore_of(step)}",
            "root_entity": root_entity(step),
            "row_source": row_source(step)}                   # '' == only --entity names the row


def match_configs(step: str, names) -> list[str]:
    """Every config in `names` that could be this step's, most trustworthy first. Never raises.

    `names` is whatever the caller has: this repo's CONFIGS keys, or the `name` fields of a workspace
    listing. Callers that cannot act on an empty result must say so with `config_name`, or print
    `lookup_description` next to the empty list -- the point is that "nothing matched" always arrives
    with what was looked for.
    """
    names = list(names)
    wf = workflow(step)
    exact = sorted(n for n in names if n == f"{step}-{wf}")
    if exact:
        return exact
    numbered = sorted(n for n in names if n.startswith(f"{step}-"))
    if numbered:
        return numbered
    return sorted(n for n in names if n.rsplit("/", 1)[-1].lower() == wf.lower())


def lookup_description(step: str) -> str:
    """The three patterns `match_configs` tried, for the caller's own "no config" line."""
    wf = STEPS.get(step, f"<workflow for {step}>")
    return f"'{step}-{wf}', then '{step}-*', then a bare '{wf}'"


def config_name(step: str, names) -> str:
    """The one config a step must use, or an exit that names the step and what it tried.

    Replaces `next(c for c in CONFIGS if c.startswith(step + "-"))`, whose StopIteration named nothing
    at all -- and whose empty case was indistinguishable from "the workspace has no configs".
    """
    matches = match_configs(step, names)
    if not matches:
        tried = lookup_description(step)
        raise SystemExit(
            f"no method config for step {step} ({workflow(step)}): tried {tried} and matched "
            f"none of the {len(list(names))} config names given.")
    return matches[0]


if __name__ == "__main__":
    print("# terra/steps.py: the step -> workflow map (the only copy)")
    print("# step  workflow                         root entity   rerun config              row source")
    for s in known_steps():
        r = rerun(s)
        print(f"{s}     {r['workflow']:<32} {r['root_entity']:<13} {r['rerun_config']:24} "
              f"{r['row_source'] or module_profile.ROW_SOURCE_OPERATOR}")
