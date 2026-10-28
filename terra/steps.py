#!/usr/bin/env python3
"""The step -> workflow map, shared by every tool in terra/.

`STEPS`, `RERUN_ROOT_ENTITY` and `RERUN_ROW_SOURCE` are derived at import from `profiles/<module>.json`
(the same file `batch_configs.CONFIGS` is built from). This module adds the config-name lookups, the
default step and the run-tag rules.

`match_configs` tries the exact numbered name, then the number prefix, then the bare workflow name. It
prefers the exact name because a finished submission leaves a snapshot copy of its config
(`<config>_<random>`) that shares the `NN-` prefix. Lookups that fail exit with a message naming the
step and the patterns tried.

    python terra/steps.py        # print the map (offline: no config, no credentials, no network)
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
import module_profile  # noqa: E402  (the reader of profiles/<MODULE>.json; kit/ is on sys.path)

# Keys are the two-digit step numbers that prefix the workspace's method configs; values are workflow
# NAMES (as served by Dockstore at github.com/broadinstitute/gatk-sv/<name>), which can differ from the
# WDL file basename. The root entity is derived from the step's own `this.<etype>_id` binding;
# the profile's `rootEntityType` is only a cross-check (see `batch_rerun_step._reconcile`).
_PROFILE = module_profile.load()


def _tables(profile):
    """`(step -> workflow, step -> root entity, step -> row source)` from the module profile.

    Exits rather than returning an empty map, and refuses steps without a two-digit number prefix.
    The root entity comes from `module_profile.derive_entity_type`, the same rule
    `checks/terra_entity_check.py` uses.
    """
    if profile.problems or not profile.configs:
        raise SystemExit(
            f"terra/steps.py cannot build the step map: {profile.path} gave no usable steps.\n"
            f"  {profile.problem or 'the profile carries no steps[] entries'}\n"
            "  Every tool in this directory reads the step map from here, so none of them run until the\n"
            "  profile loads; `python kit/module_profile.py --module <name>` names the field at fault.")
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
    """The entity type named by the step's `this.<etype>_id` binding; exits if there are zero or several."""
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


# Default step for tools that take one: 10-GenotypeBatch.
DEFAULT_STEP = "10"

# Root entity type per step. Not uniform: 09-MergeBatchSites runs on `sample_set_set`, the others on
# `sample_set`. Submitting against the wrong type leaves `this.*` bindings empty at runtime.
RERUN_ROOT_ENTITY: dict[str, str] = _TABLES[1]

# Config key that holds each step's entity row name (`BATCH` -> `GSVTK_BATCH`), or "" when only
# `--entity` can name it.
RERUN_ROW_SOURCE: dict[str, str] = _TABLES[2]


# Last segment of the rerun config name when no `--tag` is given (`10-GenotypeBatch-rerun`).
# `--tag TAG` replaces this segment.
RERUN_TAG_DEFAULT = "rerun"

# A tag goes into both the config name and output attribute names; attribute names are stricter,
# so only letters, digits and underscore are allowed.
_TAG_OK = re.compile(r"[A-Za-z0-9_]+\Z")


def check_tag(tag: str) -> str:
    """Validate a `--tag` value. Empty tags and the default tag are refused, since both would overwrite
    the untagged rerun."""
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
    """The attribute-name suffix a rerun writes: `_TAG`, or "" for the untagged default."""
    return f"_{tag}" if tag else ""


def rerun_outputs(step: str, tag: str = "") -> dict:
    """The output map a rerun of `step` writes, with the tag suffix on every attribute name.

    Inputs are not tagged, so tagged runs share the same inputs.
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
    """The Dockstore entry a step's method resolves from: the profile's `dockstore`, else `workflow`.

    These can differ, e.g. GATKSVPipelineSingleSample.wdl is published as `SingleSamplePipeline`.
    """
    for name, cfg in _PROFILE.configs.items():
        if name.startswith(f"{step}-"):
            return str(cfg.get("dockstore") or cfg.get("workflow") or workflow(step))
    return workflow(step)


def step_names() -> list[str]:
    """`["06-GenerateBatchMetrics", ...]` -- the numbered chain in launch order."""
    return [f"{s}-{STEPS[s]}" for s in known_steps()]


def root_entity(step: str) -> str:
    """The entity type `step` is submitted against; exits for an unknown step rather than guessing."""
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
    """The config key that names `step`'s row, or "" when only the operator can name it."""
    return RERUN_ROW_SOURCE.get(step, "")


def rerun(step: str, tag: str = "") -> dict:
    """Everything a rerun of `step` needs: config name, input prefix, method path, root entity, etc.

    `tag` changes only the rerun config name (`NN-<Wf>-TAG`) and the output attribute names.
    """
    wf = workflow(step)
    tag = check_tag(tag) if tag else ""
    return {"step": step, "workflow": wf,
            "config": f"{step}-{wf}",                        # the name batch_configs' CONFIGS keys on
            # `--tag` replaces the last segment rather than appending to it.
            "rerun_config": f"{step}-{wf}-{tag or RERUN_TAG_DEFAULT}",
            "tag": tag,                                      # '' == the untagged default run
            "rerun_suffix": rerun_suffix(tag),               # what every OUTPUT attribute ends with
            "rerun_outputs": rerun_outputs(step, tag),       # the attribute names this run writes
            "input_prefix": f"{wf}.",                         # the prefix of every input key
            # The Dockstore entry name, which may differ from the workflow name (see dockstore_of).
            "method_path": f"github.com/broadinstitute/gatk-sv/{dockstore_of(step)}",
            "root_entity": root_entity(step),
            "row_source": row_source(step)}                   # '' == only --entity names the row


def match_configs(step: str, names) -> list[str]:
    """Every config in `names` that could be this step's, best match first. Never raises.

    Callers that need exactly one should use `config_name`, or print `lookup_description` on an empty result.
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
    """The best-matching config for `step`, or an exit naming the step and the patterns tried."""
    names = list(names)
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
