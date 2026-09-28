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
STEPS: dict[str, str] = {
    "06": "GenerateBatchMetrics",
    "07": "FilterBatchSites",
    "08": "FilterBatchSamples",
    "09": "MergeBatchSites",
    "10": "GenotypeBatch",
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
    for s in known_steps():
        print(f"{s}  {STEPS[s]}")
