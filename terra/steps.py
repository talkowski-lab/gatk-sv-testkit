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

    python terra/steps.py        # print the map (offline: no config, no credentials, no network)
"""
from __future__ import annotations

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
STEPS: dict[str, str] = {
    "06": "GenerateBatchMetrics",
    "07": "FilterBatchSites",
    "08": "FilterBatchSamples",
    "09": "MergeBatchSites",
    "10": "GenotypeBatch",
}


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
# `batch_configs.CONFIGS[*]["rootEntityType"]` carries the same value for the chain builder, and this
# repo's named failure class is two copies of one value drifting, so `terra/batch_rerun_step.py`
# RECONCILES the two at resolve time and refuses if they ever disagree, rather than trusting either.
RERUN_ROOT_ENTITY: dict[str, str] = {
    "06": "sample_set",
    "07": "sample_set",
    "08": "sample_set",
    "09": "sample_set_set",
    "10": "sample_set",
}


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
            f"({', '.join(STEPS.values())}). Add it to terra/steps.py -- every tool reads it from here.")


def step_names() -> list[str]:
    """`["06-GenerateBatchMetrics", ...]` -- the numbered chain in launch order."""
    return [f"{s}-{STEPS[s]}" for s in known_steps()]


def root_entity(step: str) -> str:
    """The entity type `step` is submitted against, or an exit -- never a guessed `sample_set`.

    Defaulting a step that is missing from `RERUN_ROOT_ENTITY` to the majority type would submit it
    against the wrong row and empty every `this.*` binding, which is the exact defect this table
    exists to prevent. A missing row is therefore a refusal naming the step and the file to add it to.
    """
    try:
        return RERUN_ROOT_ENTITY[step]
    except KeyError:
        raise SystemExit(
            f"step {step} ({workflow(step)}) has no root entity in terra/steps.py "
            f"RERUN_ROOT_ENTITY, so nothing can be submitted against it.\n"
            f"  Known: {', '.join(f'{k}={v}' for k, v in sorted(RERUN_ROOT_ENTITY.items()))}.\n"
            "  Guessing `sample_set` is not a fallback: 09-MergeBatchSites is `sample_set_set`, and a "
            "wrong\n  type resolves every this.* binding to nothing at runtime, after the VMs booted.")


def rerun(step: str) -> dict:
    """Everything a rerun of `step` needs, derived here: config name, input prefix, method path.

    `terra/batch_rerun_step.py` used to hold these as module-level literals for step 10 only, which
    meant "rerun 08" silently reran 10. Deriving them from this map is what makes the five steps one
    code path: the prefix, the Dockstore path and the config name are all the workflow name in a
    different coat, and the workflow name lives here.
    """
    wf = workflow(step)
    return {"step": step, "workflow": wf,
            "config": f"{step}-{wf}",                        # the name batch_configs' CONFIGS keys on
            "rerun_config": f"{step}-{wf}-rerun",             # the name the rerun POSTs
            "input_prefix": f"{wf}.",                         # the prefix of every input key
            "method_path": f"github.com/broadinstitute/gatk-sv/{wf}",
            "root_entity": root_entity(step)}


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
    print("# step  workflow                         root entity   rerun config")
    for s in known_steps():
        r = rerun(s)
        print(f"{s}     {r['workflow']:<32} {r['root_entity']:<13} {r['rerun_config']}")
