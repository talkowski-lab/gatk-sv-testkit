#!/usr/bin/env python3
"""Read a module profile: `profiles/<module>.json`, the data half of a head-to-head chain.

One reader, in one language
--------------------------
The profile carries `{frz}`/`{new}` suffix tokens and `@`-over-callers expansion. That expansion
exists exactly once, here, in Python. No shell file ever parses `profiles/`: a future bash consumer
asks a Python subcommand for flat shell-friendly output rather than growing a second expander, which
closes docs/module-profiles.md §10's two-expander risk by construction instead of by a probe. Today's
three Python/bash consumers already share the plain `KEY=value` grammar of the config layer, and
`docs/config.md` names what happens when two readers disagree about a value ("an empty comparison, not
an error"). Profiles are JSON, so a second reader would be a second JSON parser AND a second expander.

What the file is allowed to hold, and what it is not
---------------------------------------------------
Rule 1 of §3: `wdl` (the file basename) and `workflow` (the declared workflow name) are SEPARATE
fields, because they differ for 12 of the 109 workflow-bearing WDLs at `main` (`DepthClustering` ->
`ClusterDepth`, `Genotype_2` -> `Regenotype`) and one string cannot serve `dockstore()`,
`_declared_inputs()` and `wdl_gate.sh` at once. Rule 2: `inputs` is the COMPLETE map, never a delta --
Terra has no "inherit from upstream", so an omitted input silently takes the WDL default and a
"required + deltas" profile is a different pipeline that grades clean. Rule 3: a value is a path iff
it starts `this.`/`workspace.`; otherwise it is a JSON literal and carries its real JSON type.
Rule 4: index/sidecar closure is a CODE rule, so it is not in here at all. Rule 6: `_why_*` siblings
carry the rationale JSON cannot hold as comments, and this loader ENFORCES the pairing -- an orphan
`_why_` (naming a key that is not bound) is a refusal, because an unenforced pairing rots into prose
nobody reads. Rule 9: `schema_version` is mandatory, so a stale reader is distinguishable from a stale
file: a version this build does not implement refuses rather than best-effort parses.

`export`: what it is, and the two things it deliberately is not
--------------------------------------------------------------
`export` (§3's rule 5) is one step's list of new-side attribute names a downstream tool FETCHES off the
entity after the chain finishes. It exists now because `terra/batch_fetch_compare.sh` carried those seven
names as literals and no field this schema had could hold them: measured at gatk-sv `main`, the terminal
step's `outputs` are 10 names, 8 once rule 4's `_index` siblings are excluded, and the fetched set is 7.
No rule derives the 7 from any field that existed, which is docs/gap-ledger.md A21 -- so the schema grew
one field instead of the shell keeping the list, and §9 step 4 keeps the measurement.

It is NOT `outputs`. `outputs` says what a step writes; `export` says what someone downstream demands, and
today they differ by exactly one name (`regeno_coverage_medians`: written, fetched as optional). The
loader deliberately does NOT compare the two: `scripts/selftest.d/profiles.sh`'s export-list guard is what
compares them, and an equality test inside the loader would make that guard unable to fail -- a check that
cannot fail is the thing this repo keeps meeting.

It is NOT a promise that the attribute is in a workspace. This reader has no workspace, no run and no
entity; a name in `export` that nobody wrote is a `[missing]` line and a nonzero fetch, which is the
answer the tool already gives and the honest one.

Shape, refused by name like every other field here: a list, non-empty when present, of BARE attribute
names. `this.genotyped_pesr_vcf{new}` is `outputs` syntax (§3 rule 3), not an attribute name, and copying
it into `export` would ask the entity for an attribute no workspace holds -- so it is a finding, not a
string. `freeze` (§3 rule 7) and `compare` (rule 8) stay out, with the per-module literals of
`batch_rerun_step.py` / `build_inputs.py` / `wdl_gate.sh` (§9 step 4); §9 says so.

`entity_row`: where the row NAME comes from, and the one value that names no key
--------------------------------------------------------------------------------
`rootEntityType` says what TYPE a step runs on. Nothing in the schema said where the NAME of that row
comes from, so `batch_rerun_step._row_name` asked `root_entity == "sample_set"` and read `GSVTK_BATCH` --
the chain's shape written into the launcher, which answered any step outside the chain with "no row",
whatever its profile said. `entity_row` is that missing fact: one config-layer key (`BATCH`, read as
`GSVTK_BATCH` through the resolver so provenance has one answer), or `--entity` for "nothing offline holds
this row; the operator names it" -- which is `09-MergeBatchSites`, whose row is a cohort and no key holds
one. Absent means the same as `--entity`; both are refused by name where the row is needed, never
defaulted. A row source that is not a known key is a refusal and not a `config.get` that answers nothing:
`kit/gsvtk-config::get` raises `KeyError` on an unknown key, so a typo in a profile would otherwise be a
traceback on the path that submits.

The TYPE, meanwhile, stopped being copied at all: `terra/steps.py` derives it from the step's own
`this.<etype>_id` read with `derive_entity_type` below, and `rootEntityType` became the cross-check on
that answer -- compared on the launch path by `batch_rerun_step._reconcile`, and for every other reader by
`scripts/selftest.d/rerun.sh`. That is why the field this schema was missing is the row SOURCE and not a
second type field: docs/plan-launch-any-module.md Stage C.

`images`: what a step pins, and the one marker that says "nobody pins this"
--------------------------------------------------------------------------
`rootEntityType` says what TYPE a step runs on and `entity_row` says where the row NAME comes from.
Neither said anything about the container images, and a launch body has 1 to 14 of them: a step's own
`*_docker` bindings are the whole list (`batch_rerun_step.make_body` derives it from the map by name
suffix and refuses a body carrying one that is not a pinned literal), while the offline vocabulary for
naming a ref was TWO config keys hardcoded in `batch_rerun_step.images_from_config`
(`GSVTK_IMAGE_REPO` -> `sv_pipeline_docker`, `GSVTK_GATK_IMAGE_REPO` -> `gatk_docker`). So a step with
more than those two images could never be fully pinned from data: measured at this tree, launching
`01-GATKSVPipelineSingleSample` prints 14 image inputs, 2 of them `profile/env`, 12 `UNPINNED`, and the
cure was 12 repeated `--image` flags. `images` is that missing fact, per step: one entry per `*_docker`
input this step binds, valued either with a TAGGED ref or with `IMAGE_SOURCE_OPERATOR` (`"--image"`) for
"nothing offline states this ref; only the flag does" — the same duality `entity_row` has with
`--entity`.

What it may NOT hold is upstream's answer. A URI copied out of the ref's `inputs/values/dockers.json`
answers "which image does this ref use", which is the ref's question, not this file's (rule 5's ban on
transcribing image URIs, and the reason the ban exists: a transcribed tag goes stale the moment the ref
moves and reads as evidence forever). A pin answers a question no ref can answer — "which image did MY
change run in" — and the repo already states that answer in two other places (`--image`,
`GSVTK_*_IMAGE_REPO`). That is the whole line: transcribed upstream URIs are refused by this rule, a
stated pin is the field's purpose, and a stated pin must be TAGGED because `.../sv-pipeline` with no
`:` floats to whatever is current at pull time, which is not a statement about which code ran (the same
test `make_body` runs before it will POST a body). No tracked profile in this repo states a ref: a real
tag is a per-machine, per-branch coordinate, and the one the last lane committed was evicted by the
coordinate leg within the hour. The loader accepts a tagged ref because that is what makes the field
able to pin at all; the shipped profiles state `--image` because that is the honest answer offline.

Carried as `Loaded.image_pins`, never inside `configs`, for the identical reason `export` and
`entity_row` are: `CONFIGS` is the POSTed table, and a pin is not a binding — the step's `inputs` entry
stays the `workspace.<attr>` path it is upstream, and the launcher MERGES the pin over it at body-build
time so the provenance line can still say which ref came from where.

Never at import
---------------
`load()` never raises, never exits and never creates a file: `batch_configs.py` calls it at import so
that `CONFIGS` stays a module-level dict attribute (five probes and `terra/batch_rerun_step.py` read it
as an attribute), and a `--help` path must not depend on repo data or leave a directory behind
(`helpsweep`, `probe_help_writes_nothing`). Everything that goes wrong is returned as a list of
findings, and the command that needs the table calls `require()` -- which is where an unusable profile
exits 4 naming the file and every field it needs.

python 3.9+, stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config                                          # noqa: E402  (kit/ is a sys.path entry)

SCHEMA_VERSIONS = (1,)
DEFAULT_MODULE = "genotyping"

# §3 rule 3: the only two prefixes that make a value a path. Everything else is a literal.
PATH_PREFIXES = ("this.", "workspace.")
# A suffix token is the ONLY brace syntax a value may carry. `@` and `{frz}`/`{new}` are meaningful on
# an attribute path; on a literal they are a bug, so a literal carrying one is a finding, not a string.
TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
KNOWN_TOKENS = ("frz", "new")

TOP_FIELDS = ("schema_version", "name", "callers", "steps")
def terra_literal(value):
    """A profile value in the form a Terra method config stores it in: every input value is a STRING.

    Rawls types an input map as `Map[String, String]`, and it rejects anything else while parsing the body, before
    it looks at a single binding: a real `rerun-create` against a live workspace answered `HTTP 400 "The request
    content was malformed: unexpected json type"` because eight values arrived as parsed JSON (`30`, `false`, and
    one list). The profile holds them parsed because that is how the upstream `.json.tmpl` holds them, which is the
    right thing to read and the wrong thing to POST. The live capture is the form to match: 0 non-string values of
    112, arrays serialized the way `json.dumps` does it by default (`'["SR", "SD", "RM"]'`, comma-space), so this is
    `json.dumps` for everything that is not already a string, and a no-op for everything else.
    """
    return value if isinstance(value, str) else json.dumps(value)


STEP_FIELDS = ("step", "wdl", "workflow", "dockstore", "literals", "rootEntityType", "entity_row", "inputs", "outputs",
               "branch_only_inputs", "export", "images")
# The one value `entity_row` may carry that names no config key: the row is not held anywhere offline,
# and the operator is the only source. Spelled as the flag that supplies it, so a profile reads as the
# instruction it is. Anything else in the field must be a key this repo's config layer actually knows.
ROW_SOURCE_OPERATOR = "--entity"
# The one value an `images` entry may carry that states NO image: the ref is not held anywhere offline
# and only the per-call `--image KEY=REF` can state it. Spelled as the flag that supplies it, exactly as
# `entity_row` spells `--entity`, so a profile reads as the instruction it is and a reader can tell a
# stated ref apart from a ref the profile declines to state. Anything else in the field is a literal.
IMAGE_SOURCE_OPERATOR = "--image"
# The prefix the config layer accepts on a key name (`kit/gsvtk-config::get` strips it), so a profile may
# spell the key either way and the reader canonicalises it instead of keeping two vocabularies alive.
KEY_PREFIX = "GSVTK_"
# The suffix that makes an attribute a Terra NAME key (`this.<etype>_id`), which is what states the entity
# type a step runs on. One spelling, and `derive_entity_type` is this file's only reader of it.
NAME_KEY_SUFFIX = "_id"
# A `_why_` key normally pairs with a bound key. The reserved siblings carry the rationale for what a
# map deliberately does NOT bind, which is the rationale §1 calls the price of transcription and which
# has no key to hang off. Explicit list, so an orphan typo still gets refused.
RESERVED_WHY = frozenset({"_why_unbound"})

# Every field a working profile must carry. The refusal prints this LIST rather than a subset: the
# earlier error text named 5 of the 9 fields a profile needed, and following it produced a file that
# could not drive a chain (docs/module-profiles.md §6).
FIELD_HELP = (
    "  top level     schema_version (1), name, callers (list of caller names, may be empty),\n"
    "                steps (list, at least one)\n"
    "  every step    step            the config name, e.g. \"10-GenotypeBatch\"\n"
    "                wdl             the WDL FILE basename, without .wdl\n"
    "                workflow        the DECLARED workflow name (different from `wdl` for 12 of the\n"
    "                                109 workflow-bearing WDLs at main: DepthClustering->ClusterDepth)\n"
    "                rootEntityType  the Terra entity the step runs on (sample_set, sample_set_set)\n"
    "                inputs          the COMPLETE input map, never a delta\n"
    "                outputs         the COMPLETE output map\n"
    "  optional step branch_only_inputs  keys the ref under test does not declare\n"
    "                export            bare new-side attribute names a downstream tool fetches\n"
    "                                (not the `outputs` map, and not `this.<attr>` values -- names)\n"
    "                images            per *_docker input of THIS step: a TAGGED image ref, or\n"
    "                                `--image` when nothing offline states it and only the per-call\n"
    "                                flag does. Never a `workspace.`/`this.` path, never untagged.\n"
    "                dockstore       the Dockstore ENTRY the method resolves from, when that is NOT the\n"
    "                                declared name: upstream publishes /wdl/GATKSVPipelineSingleSample.wdl\n"
    "                                as `SingleSamplePipeline` (.github/.dockstore.yml:256-257). A launch that\n"
    "                                used the declared name posted a config pointing at an entry that does\n"
    "                                not exist, which Terra creates without complaint and fails downstream.\n"
    "                                Absent means the same as `workflow`, so chain profiles are unchanged.\n"
    "                literals        leaf -> the exact string Terra stores, for a binding whose upstream\n"
    "                                template value is a `workspace.<attr>` read. That works only in a\n"
    "                                workspace someone populated first; elsewhere submission refuses it\n"
    "                                (`HTTP 400 Invalid inputs`) AFTER `validate` passed, so a launch states\n"
    "                                its own resources. Applied by leaf match (a leaf two bindings share gets\n"
    "                                both), and a literal naming no bound leaf is a refusal, not a drop.\n"
    "                entity_row        where the step's entity ROW NAME comes from: one config-layer\n"
    "                                key (`BATCH`, or `GSVTK_BATCH`), or `--entity` when nothing offline\n"
    "                                holds one. Absent means the same as `--entity`: a launcher refuses a\n"
    "                                row it was never told rather than defaulting it to a `sample_set`\n"
    "  anywhere      _why_<key>       a rationale beside the key it explains; `_why_unbound` for the\n"
    "                                keys deliberately left to a WDL default")


class Loaded:
    """What one profile read produced: the tables, or the findings. Never both empty.

    `configs`/`callers`/`branch_only_inputs` are the shapes the tools have always read; `exports` is the
    one this reader added for `batch_fetch_compare.sh` (step -> its `export` names, profile order);
    `row_sources` is the one added for the launcher (step -> the config key its `entity_row` names,
    canonicalised, in profile order); `image_pins` is the one added for the SAME launcher's other
    question (step -> the ref its `images` map states for each of its `*_docker` inputs, verbatim — a
    tagged ref or the `--image` marker, never canonicalised because there is nothing to canonicalise);
    and `problem` is the single sentence a command prints when the tables cannot be used. `found=False`
    says why an absent profile is fine -- it is how `--help` answers without repo data.

    A step carrying no `entity_row`, or carrying `--entity`, is absent from `row_sources`. Both spell the
    same answer ("only `--entity` names this row"), and the launcher's refusal is what makes that answer
    loud. Absent is never "no row source was considered". A step carrying no `images` map is absent from
    `image_pins` the same way, and there the reading is narrower: absent means the step states NO ref for
    any of its images, which is what every profile written before this field meant. It is not a statement
    that the step has no images -- `make_body` derives those from the bindings by name suffix, and a
    launch path reports an unpinned one by name.
    """

    def __init__(self, path, found, configs, callers, branch_only, exports, rows, pins, problems,
                 notes):
        self.path = path
        self.found = found
        self.configs = configs
        self.callers = callers
        self.branch_only_inputs = branch_only
        self.exports = exports
        self.row_sources = rows
        self.image_pins = pins
        self.problems = problems
        self.notes = notes

    @property
    def problem(self) -> str:
        return "\n".join(self.problems)

    def require(self, tag: str = "") -> None:
        """Exit 4 with the file, the findings and the COMPLETE field list. Never a traceback."""
        if not self.problems and self.configs:
            return
        prefix = f"{tag}: " if tag else ""
        if not self.configs and not self.problems:
            self.problems.append("the profile bound no steps, so there is nothing to build")
        lines = [f"{prefix}module profile {self.path} "
                 + ("is unusable:" if self.found else "is missing:")]
        for p in self.problems:
            lines.append(f"  - {p}")
        lines.append("  A profile is the data half of a chain: it answers which WDL each step runs,")
        lines.append("  what each step binds, and which bindings the ref does not declare. The fields")
        lines.append("  it must carry, all of them:")
        lines.append(FIELD_HELP)
        lines.append("  Write that file, or point GSVTK_MODULE at a module whose profile exists.")
        lines.append(f"  (GSVTK_MODULE_DIR is {os.path.dirname(str(self.path)) or '.'}).")
        lines.append("  `./kit/gsvtk-config show` prints which module was chosen and where the key came")
        lines.append("  from; docs/module-profiles.md §3 explains what each field means and why.")
        # Exit 4, the config layer's own number for "a value you need is not there": the same 4
        # `gsvtk-config require` prints for a missing key, because a missing module IS a missing value
        # -- it is just carried in a file instead of a key. `SystemExit(str)` would be a 1, and a 1 is
        # what every other refusal in this repo prints, so a caller could not tell the two apart.
        print("\n".join(lines), file=sys.stderr)
        raise SystemExit(4)

    def require_exports(self, tag: str = "") -> list:
        """The fetch list -- every step's `export` names, in profile order, de-duplicated -- or exit 4.

        The refusal is the whole point of this method. A consumer that asked for the list and got `[]`
        would fetch nothing and report a clean plan, which is this repo's named failure class: an empty
        that reads like nothing was wrong. So the message names the module, the file, EVERY step it
        looked for `export` on, and the field it looked for, and says what to write.

        Steps that carry no `export` contribute nothing rather than refusing: a step whose outputs no
        tool fetches legitimately has no export list, and the profile as a whole is the unit that must
        answer. Same 4 as `require()` -- "the value you need is not there".
        """
        names, seen = [], set()
        for step, attrs in self.exports.items():
            for a in attrs:
                if a not in seen:
                    seen.add(a)
                    names.append(a)
        if names:
            return names
        prefix = f"{tag}: " if tag else ""
        steps = list(self.configs) or ["(no usable steps)"]
        lines = [
            f"{prefix}module profile {self.path} exports nothing: no step of it carries a non-empty",
            "`export` list, so there is nothing to fetch and this command refuses to plan an empty run.",
            f"  steps `export` was looked for on: {', '.join(steps)}",
            "  `export` is per-step, so the profile as a whole has to answer. It is the list of the",
            "  new-side attributes a downstream tool reads off the entity after the chain runs",
            "  (docs/module-profiles.md §3 rule 5). It is NOT the `outputs` map: `outputs` says what a step",
            "  writes, `export` says what someone downstream demands. It is not a promise the attribute is",
            "  in a workspace either -- it is a list of names to go and look for, and a name nobody wrote",
            "  is the `[missing]` line the fetch already prints.",
            "  Write it beside the step's `outputs`, as bare attribute names:",
            '      "export": ["genotyped_pesr_vcf", "genotyping_pe_table"]',
            "  Planning an empty fetch and exiting 0 is not an acceptable answer here.",
        ]
        print("\n".join(lines), file=sys.stderr)
        raise SystemExit(4)


def module_name() -> str:
    """Which module the loops are driving, and it is a name, never a path (§6: one resolver)."""
    return config.get("MODULE", DEFAULT_MODULE) or DEFAULT_MODULE


def module_dir() -> str:
    """Where module profiles live: the resolved GSVTK_MODULE_DIR (§6's single precedence chain)."""
    return config.get("MODULE_DIR") or str(Path(__file__).resolve().parent.parent / "profiles")


def path(name: str = "") -> str:
    return os.path.join(module_dir(), f"{name or module_name()}.json")


def suffixes() -> dict:
    """The attribute suffixes the `{frz}`/`{new}` tokens expand to, from the config layer.

    One source for both languages, exactly as `batch_fetch_compare.sh` reads them: the tokens are not
    a private grammar of this loader, they are the two config keys spelled in a form JSON can hold.
    """
    return {"frz": "_" + (config.get("FROZEN_SUFFIX", "frz") or "frz"),
            "new": "_" + (config.get("NEW_SUFFIX", "new") or "new")}


def binding_kind(value):
    """('path' | 'literal', detail) for one bound value, by §3 rule 3 -- the prefix decides.

    Exposed rather than inlined because the finding worth having is the one `check_maps` cannot see:
    `check_maps` builds its `bound` set from KEYS and never examines a value, so the one profile field
    that can change which pipeline runs is invisible to it (docs/module-profiles.md §7).
    """
    if isinstance(value, str) and value.startswith(PATH_PREFIXES):
        return "path", value
    return "literal", value


def name_key_type(attr: str) -> str:
    """<etype> for a name-key-shaped attribute, or "" when the read carries no type.

    `sample_set_set_id` -> `sample_set_set` -- the whole stem, not the first word, which is the mistake
    that turns `09-MergeBatchSites` into a `sample_set` step. `id` -> "": a name key with nothing before
    the suffix carries no type, and deriving an empty entity type would ask for a row no workspace has.

    The same rule as `checks/terra_entity_check.py::name_key_type`. Two files, two corpora (profile values
    here, `${this.<x>}` template strings there), ONE rule: `scripts/selftest.d/profiles.sh` compares the
    two strips on the measured names, because a drift between them is a launcher that disagrees with the
    checker that grades it.
    """
    if attr.endswith(NAME_KEY_SUFFIX) and len(attr) > len(NAME_KEY_SUFFIX):
        return attr[:-len(NAME_KEY_SUFFIX)]
    return ""


def derive_entity_type(values) -> tuple:
    """(etype, candidates) from a step's own bindings: the single `this.<etype>_id` it reads.

    The name-key rule, from docs/plan-launch-any-module.md Stage A, applied to the artifact a launcher
    actually holds -- the profile's expanded binding map, which is what gets POSTed. It replaces the two
    rules measured and killed there:

      * the WDL contains no `this.` at all (0 files at gatk-sv `e1909d2f`), and
      * `this.<collection>.<attr>` names the MEMBER type: `MergeBatchSites` reads
        `this.sample_sets.<attr>` three times and `this.sample_set_set_id` once, and the member rule
        derives the wrong one with confidence.

    So only a SINGLE-component read ending `_id` states the row, and only a *read* does: `outputs` say what
    a step writes, which is no evidence about the row it was submitted against. A member read is skipped,
    never counted -- counting one is what turns a single-answer step into "many".

    Returns ("", candidates) when the bindings answer zero or many types. "The profile cannot answer" and
    "the profile answers two things" need different sentences, so the caller refuses rather than this
    function picking. Candidates keep binding order, which is the profile's order.
    """
    found = []
    for value in values:
        if not (isinstance(value, str) and value.startswith("this.")):
            continue                                        # a literal, or a workspace.<attr>
        attr = value[len("this."):]
        if "." in attr:                                      # a member read: never a name key
            continue
        et = name_key_type(attr)
        if et and et not in found:
            found.append(et)
    return (found[0] if len(found) == 1 else ""), found


def _validate_row_source(value, name: str) -> list:
    """Shape of one step's `entity_row`: a config-layer key name, or the one reserved word.

    One canonical form on purpose: `BATCH` and `GSVTK_BATCH` are the same key to the config layer, so the
    reader stores one spelling. What is refused is what a real author reaches for: a BINDING VALUE written
    into the field. `workspace.cohort_name` is a path (§3 rule 3), and a launcher holding it would submit
    against a row named "workspace.cohort_name". An unknown key is refused rather than handed to
    `config.get`, which answers an unknown key with `KeyError`.
    """
    if not isinstance(value, str):
        return [f"{name}: `entity_row` is a {type(value).__name__}, not a config key name (BATCH) or "
                f"{ROW_SOURCE_OPERATOR!r}"]
    text = value.strip()
    if not text:
        return [f"{name}: `entity_row` is blank. A row that cannot be named says so with "
                f"{ROW_SOURCE_OPERATOR!r}; an empty string reads as a row named nothing, which is what "
                f"`--entity \"$ROW\"` with ROW unset looks like -- the exact shape this repo already "
                f"refuses on the command line"]
    if text.startswith(PATH_PREFIXES):
        return [f"{name}: `entity_row` is {value!r}, a binding VALUE, not a row source. `this.`/"
                f"`workspace.` paths name what a step binds; this field names where the ROW NAME comes "
                f"from, which is a config-layer key or {ROW_SOURCE_OPERATOR!r}"]
    if TOKEN_RE.search(text) or "@" in text:
        return [f"{name}: `entity_row` is {value!r}, which carries expansion syntax. Names here are "
                f"literal: no {{frz}}/{{new}} token, no `@` caller fan-out"]
    if text == ROW_SOURCE_OPERATOR:
        return []                                  # the operator's `--entity`, from no offline source
    if _row_source_key(text) not in config.KEYS:
        return [f"{name}: `entity_row` names {value!r}, which is not a key this repo's config layer knows "
                f"(kit/gsvtk-config; every key is listed in docs/config.md). A row source that is not a "
                f"key resolves to nothing, which reads as 'the workspace holds no row'"]
    return []


def _row_source_key(value) -> str:
    """The canonical key an `entity_row` names, or "" when it names none (`--entity`, or absent).

    The canonicalisation half of the field; `_validate_step` is the shape half. Reached only for a document
    that passed validation, so it never has to decide whether a value is legal -- and never repairs one
    either.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text or text == ROW_SOURCE_OPERATOR:
        return ""
    key = text.upper()
    return key[len(KEY_PREFIX):] if key.startswith(KEY_PREFIX) else key


def _expand(value, callers, sfx, where, problems):
    """One value -> zero, one, or len(callers) bindings, with the tokens replaced.

    Returns a list of (key, value) because `@` fans one profile line out into one binding per caller.
    """
    if not isinstance(value, str):
        return [(None, value)]                                   # a typed literal: nothing to expand
    if "@" in value and where.endswith("]") and "@" not in where:
        problems.append(f"{where}: the VALUE carries `@` but its key does not, so this line would fan "
                        f"out to {len(callers)} values on one key and keep the last")
        return []
    kind, _ = binding_kind(value)
    out = []
    for key in ([None] if "@" not in value else list(callers)):
        k = value if key is None else None                        # keys are handled by the caller
        v = value.replace("@", key) if key and "@" in value else value
        tokens = set(TOKEN_RE.findall(v))
        unknown = sorted(tokens - set(KNOWN_TOKENS))
        if unknown:
            problems.append(f"{where}: unknown token(s) {', '.join('{%s}' % t for t in unknown)} in "
                            f"{value!r}; the only tokens are "
                            f"{', '.join('{%s}' % t for t in KNOWN_TOKENS)}")
            continue
        if tokens and kind != "path":
            problems.append(f"{where}: {value!r} carries a suffix token but is not a path -- a value is "
                            f"a path iff it starts {'/'.join(PATH_PREFIXES)}, otherwise it is a literal "
                            f"and a literal has no attribute suffix")
            continue
        if "@" in value and kind != "path":
            problems.append(f"{where}: {value!r} carries `@` but is not a path; `@` fans out over "
                            f"callers on an attribute, never on a literal")
            continue
        for t in KNOWN_TOKENS:
            v = v.replace("{" + t + "}", sfx[t])
        out.append((k, v))
    return out


def expand_map(mapping, callers, sfx, where, problems):
    """A profile map -> the binding map the tool posts, in profile order, `@` expanded in place.

    Position matters: `show` prints map order and `body()` serialises it, so an expansion that
    appended the caller bindings at the end would reorder the POSTed body and break the byte-for-byte
    golden for reasons nobody intended.
    """
    out = {}
    for key, value in (mapping or {}).items():
        at = "@" in str(key)
        if str(key).startswith("_"):
            problems.append(f"{where}: {key!r} is a rationale key INSIDE a binding map. A `_why_*` "
                            f"sibling belongs beside the map, not in it: everything in here is POSTed "
                            f"as a binding.")
            continue
        w = f"{where}[{key}]"
        if not at:
            got = _expand(value, callers, sfx, w, problems)
            for _k, v in got:
                out[key] = v
            continue
        if not callers:
            problems.append(f"{where}[{key}]: `@` needs a caller list and this profile's `callers` is "
                            f"empty, so this binding would expand to nothing and silently shrink the "
                            f"cohort")
            continue
        base = key.split("@", 1)
        stem, leaf = base[0], base[1]
        for c in callers:
            full = f"{stem}{c}{leaf}"
            got = _expand(value, [c], sfx, w, problems)
            for _k, v in got:
                out[full] = v
    return out


def validate(doc) -> list:
    """Every reason this document cannot drive a chain, as findable sentences. [] means usable.

    Strict on purpose. The failure this schema is designed against is a profile that is valid JSON and
    still wrong, and the cheapest way to get there is a typo in a field name: it reads as "field
    absent", which reads as "this step binds nothing", which posts a config with a hole in it.
    """
    problems = []
    if not isinstance(doc, dict):
        return [f"top level is a {type(doc).__name__}, not one profile object"]
    for key in doc:
        if str(key).startswith("_"):
            continue
        if key not in TOP_FIELDS:
            problems.append(f"top level: unknown field {key!r}; this schema knows "
                            f"{', '.join(TOP_FIELDS)} (a typo here would read as a missing field)")
    for key in TOP_FIELDS:
        if key not in doc:
            problems.append(f"top level: missing {key!r}")
    if doc.get("schema_version") not in SCHEMA_VERSIONS:
        problems.append(f"schema_version {doc.get('schema_version')!r} is not a version this build "
                        f"reads ({', '.join(str(v) for v in SCHEMA_VERSIONS)}); a stale reader and a "
                        f"stale file must not be guessed apart")
    if "callers" in doc and not isinstance(doc["callers"], list):
        problems.append(f"`callers` is a {type(doc['callers']).__name__}, not a list of caller names")
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        problems.append("`steps` is not a non-empty list, so this profile drives no step -- and an "
                        "empty chain is exactly what reads like nothing was wrong")
        return problems
    for i, step in enumerate(steps):
        problems += [f"step {i}: {p}" for p in _validate_step(step, steps)]
    return problems


def _validate_step(step, all_steps) -> list:
    problems = []
    if not isinstance(step, dict):
        return [f"is a {type(step).__name__}, not a step object"]
    name = str(step.get("step") or "")
    if not name:
        name = "steps[%d]" % (all_steps.index(step) if step in all_steps else -1)
    for key in step:
        if str(key).startswith("_"):
            continue
        if key not in STEP_FIELDS:
            problems.append(f"{name}: unknown field {key!r}; this schema knows "
                            f"{', '.join(STEP_FIELDS)}")
    for key in ("step", "wdl", "workflow", "rootEntityType", "inputs", "outputs"):
        if key not in step:
            problems.append(f"{name}: missing {key!r}")
    for key in ("step", "wdl", "workflow", "rootEntityType"):
        if key in step and not isinstance(step[key], str):
            problems.append(f"{name}: {key} is a {type(step[key]).__name__}, not a name")
    for key in ("inputs", "outputs"):
        if key in step and not isinstance(step[key], dict):
            problems.append(f"{name}: {key} is a {type(step[key]).__name__}, not a map")
    if isinstance(step.get("inputs"), dict) and not step["inputs"]:
        problems.append(f"{name}: `inputs` is empty. Terra has no inherit-from-upstream, so an empty "
                        "input map is a workflow run entirely on WDL defaults, not a step with no "
                        "parameters to speak of")
    if "entity_row" in step:
        problems += _validate_row_source(step["entity_row"], name)
    if "images" in step:
        problems += _validate_images(step["images"], name, step)
    if "export" in step:
        problems += _validate_export(step["export"], name)
    # `_why_` pairing, the rule that keeps a rationale from rotting into a comment about a key that no
    # longer exists. An orphan is a refusal, not a warning: the whole reason the sibling exists is that
    # the pairing is checkable.
    bound = set(step.get("inputs") or {}) | set(step.get("outputs") or {})
    for key in step:
        if not str(key).startswith("_why"):
            continue
        if key in RESERVED_WHY:
            continue
        referent = key[len("_why_"):] if key.startswith("_why_") else None
        if referent is None:
            problems.append(f"{name}: {key!r} is not a `_why_<key>` sibling and is not one of "
                            f"({', '.join(sorted(RESERVED_WHY))})")
        elif referent not in bound and referent not in step:
            problems.append(f"{name}: {key!r} explains {referent!r}, which this step does not bind -- "
                            f"an orphan rationale is worse than none, because it will be read as true")
    return problems


def _validate_images(value, name: str, step) -> list:
    """Shape of one step's `images` map: every entry pins ONE of that step's own images.

    The key is the step's OWN binding key, spelled exactly as `inputs` spells it, for the same reason
    `branch_only_inputs` spells it that way: membership in `inputs` is then a direct test, and a pin
    naming a key this step does not bind is a finding rather than a ref nobody will ever POST. A pin for
    a key that is not `*_docker` is refused too, because the launcher's pin test IS the name suffix
    (`make_body` asks which `*_docker` values are not literals) -- a map entry for a non-image input
    would satisfy nothing and hide one.

    What the refusals are aimed at, in the order a real author meets them: a value pasted from the
    `inputs` map right below it (`workspace.manta_docker` is a BINDING, and holding it as a pin is the
    exact laundering this field exists to stop -- the value would resolve to whatever the attribute
    points at today while reading like a pin); a registry path with no tag; a ref somebody pre-quoted
    (the launcher quotes it, and a doubled quote is what Terra's expression parser rejects); and an empty
    map, which is this schema's named failure class -- a step whose images all arrive by flag leaves the
    field out, or states each one as `--image`.

    One rule deliberately NOT checked here: whether every `*_docker` binding the step carries HAS an
    entry. That coverage is what a launch path refuses by name (an unpinned `*_docker` is a refusal in
    every mode but `show`), and a loader that refused it too would leave the launcher's own guard unable
    to fail -- the same reasoning that keeps the `export`-vs-`outputs` comparison out of this file.
    """
    if not isinstance(value, dict):
        return [f"{name}: `images` is a {type(value).__name__}, not a map of this step's *_docker "
                f"binding key -> image ref"]
    if not value:
        return [f"{name}: `images` is an empty map. A step whose images all arrive by {IMAGE_SOURCE_OPERATOR!r} "
                f"on the command line should say so by naming each one that, or leave the field out; an "
                f"empty map reads as \"this step has no images\", and the bindings say otherwise"]
    bound = set(step.get("inputs") or {})
    problems = []
    for key, ref in value.items():
        if str(key).startswith("_"):
            problems.append(f"{name}: images key {key!r} is a rationale INSIDE the map. A `_why_*` "
                            f"sibling belongs beside `images`, not in it: everything in here is a pin")
            continue
        if not isinstance(key, str) or not key.strip():
            problems.append(f"{name}: images has a blank key; a pin is keyed by the binding it pins "
                            f"({IMAGE_SOURCE_OPERATOR!r} or a tagged ref)")
            continue
        # No duplicate check, unlike `export`: a JSON object key can only appear once by the time
        # `json.load` answers, so a branch for it here could never run -- and a refusal that cannot fire
        # is the thing this repo keeps meeting.
        if "@" in key or "{" in key:
            problems.append(f"{name}: images key {key!r} carries expansion syntax. Keys here are the "
                            f"step's own binding keys, literal: no `@` caller fan-out, no "
                            f"{{frz}}/{{new}} token")
            continue
        if not key.endswith("_docker"):
            problems.append(f"{name}: images key {key!r} is not a *_docker input. The launcher's pin "
                            f"test IS that suffix, so an entry here would pin nothing while making one "
                            f"fewer image look unpinned")
            continue
        if key not in bound:
            problems.append(f"{name}: images key {key!r} is not bound in this step's `inputs`, so no "
                            f"launch ever posts it and this ref would sit here unread")
            continue
        if not isinstance(ref, str):
            problems.append(f"{name}: images[{key!r}] is a {type(ref).__name__}, not a tagged image "
                            f"ref (or {IMAGE_SOURCE_OPERATOR!r} for \"only --image states this\")")
            continue
        text = ref.strip()
        if not text:
            problems.append(f"{name}: images[{key!r}] is blank. A pin that names no image says nothing "
                            f"about what ran -- name the ref, or {IMAGE_SOURCE_OPERATOR!r}")
            continue
        if text == IMAGE_SOURCE_OPERATOR:
            continue                                   # the one value that states no image, on purpose
        if text.startswith(PATH_PREFIXES):
            problems.append(f"{name}: images[{key!r}] is {ref!r}, a binding VALUE, not a pin. "
                            f"`this.`/`workspace.` resolves at run time to whatever that attribute "
                            f"points at TODAY, which is the stale image this field exists to refuse; "
                            f"state the ref, or {IMAGE_SOURCE_OPERATOR!r}")
            continue
        if "@" in text or "{" in text:
            problems.append(f"{name}: images[{key!r}] is {ref!r}, which carries expansion syntax. A ref "
                            f"here is literal")
            continue
        if '"' in text:
            problems.append(f"{name}: images[{key!r}] is {ref!r}, already quoted. The launcher quotes a "
                            f"literal for Terra's expression parser itself, so this would post a "
                            f"doubly-quoted value")
            continue
        if ":" not in text.rsplit("/", 1)[-1]:
            problems.append(f"{name}: images[{key!r}] is {ref!r}, UNTAGGED: a registry path with no "
                            f"`:` floats to whatever is current at pull time, which answers no question "
                            f"about which code ran. Tag it (<repo>:<tag> or <repo>@sha256:...), or "
                            f"{IMAGE_SOURCE_OPERATOR!r}")
            continue
    return problems


def _validate_export(value, name: str) -> list:
    """Shape of `export`: a non-empty list of BARE attribute names, each finding naming the index.

    What is refused here is what a name is NOT, because that is the mistake a real author makes: pasting
    `this.genotyped_pesr_vcf{new}` out of the `outputs` map above. That is a PATH with a suffix token
    (§3 rule 3), and a consumer holding it would ask the entity for an attribute called
    `genotyped_pesr_vcf_new_new` -- or `this.genotyped_pesr_vcf{new}` -- and never find it. A duplicate
    would fetch the same object twice, and an empty list is the named failure class, not a step with
    nothing to fetch: a step nobody fetches from leaves the field out.

    What is deliberately NOT checked: whether the step's `outputs` writes each name, or whether anything
    in the map is fetchable. That comparison is `scripts/selftest.d/profiles.sh`'s export-list guard;
    doing it here too would leave the guard unable to fail.
    """
    if not isinstance(value, list):
        return [f"{name}: `export` is a {type(value).__name__}, not a list of attribute names"]
    if not value:
        return [f"{name}: `export` is an empty list. A step nothing fetches from leaves the field out; "
                f"an empty list reads as \"run this step and bring back nothing\", which is the emptiness "
                f"this schema refuses out loud"]
    problems, seen = [], set()
    for i, attr in enumerate(value):
        if not isinstance(attr, str):
            problems.append(f"{name}: export[{i}] is a {type(attr).__name__}, not an attribute name")
            continue
        if not attr.strip():
            problems.append(f"{name}: export[{i}] is blank")
            continue
        if attr.startswith(PATH_PREFIXES):
            problems.append(f"{name}: export[{i}] is {attr!r}, a path, not an attribute name. `export` "
                            f"holds bare names; the `this.`/`workspace.` prefix and the suffix token "
                            f"belong to `outputs`, which already carries them (§3 rule 3), so "
                            f"copying one in here asks the entity for an attribute no workspace has")
            continue
        if "{" in attr or "@" in attr:
            problems.append(f"{name}: export[{i}] is {attr!r}, which carries expansion syntax. Names "
                            f"here are literal: no {{frz}}/{{new}} token, no `@` caller fan-out")
            continue
        if attr in seen:
            problems.append(f"{name}: export[{i}] is {attr!r}, already listed -- a name twice would "
                            f"fetch the same attribute twice")
            continue
        seen.add(attr)
    return problems


def load(name: str = "") -> "Loaded":
    """Read one module profile. NEVER raises, NEVER exits, NEVER creates a file.

    Every outcome -- no file, unreadable bytes, an unknown schema_version, an orphan `_why_`, a token
    on a literal -- comes back as a `Loaded` with `problems` filled and the tables empty. Callers that
    need the tables call `Loaded.require()`, which is where the exit 4 belongs: `--help` reads this
    module and must answer with zero configuration.
    """
    p = path(name)
    if not os.path.isfile(p):
        return Loaded(p, False, {}, [], {}, {}, {}, {}, [f"no profile at {p}"], [])
    try:
        with open(p) as fh:
            doc = json.load(fh)
    except ValueError as e:
        return Loaded(p, True, {}, [], {}, {}, {}, {}, [f"{p} is not readable JSON ({e})"], [])
    except OSError as e:
        return Loaded(p, False, {}, [], {}, {}, {}, {}, [f"{p} could not be read ({e.strerror})"], [])
    problems = validate(doc)
    if problems:
        return Loaded(p, True, {}, [], {}, {}, {}, {}, problems, [])
    callers = [str(c) for c in doc["callers"]]
    sfx = suffixes()
    configs, branch_only, exports, rows, pins = {}, {}, {}, {}, {}
    notes = []
    for step in doc["steps"]:
        where = str(step["step"])
        here = []
        inputs = {k: terra_literal(v) for k, v in
                  expand_map(step["inputs"], callers, sfx, f"{where}.inputs", here).items()}
        # A profile may pin a value that upstream's template leaves as a `workspace.<attr>` read. Applied by leaf
        # match rather than by prefix construction, so a literal can only ever replace a binding this step already
        # declares -- a typo'd leaf is reported as a problem instead of silently adding a 114th input.
        lit = step.get("literals") or {}
        if lit:
            by_leaf: dict[str, list[str]] = {}
            for key in inputs:
                by_leaf.setdefault(key.rsplit(".", 1)[-1], []).append(key)
            for leaf in sorted(lit):
                if leaf.endswith("_docker"):
                    here.append(f"{where}.literals.{leaf}: an image key belongs to the image rule "
                                f"(--image, or a tagged ref stated once in steps[].images), not to literals: "
                                f"a literal here makes some registry's default the code that runs when no "
                                f"--image is given")
                    continue
                hits = by_leaf.get(leaf)
                if not hits:
                    here.append(f"{where}.literals.{leaf}: no input of this step carries that leaf, so the literal "
                                f"would be silently dropped (bound leaves come from the step's own inputs map)")
                    continue
                # Applied to EVERY binding sharing the leaf, not the first. Two of this module's inputs end in
                # `par_bed` (the top-level one and a sub-workflow's), and preferring one would have left the other
                # reading `workspace.par_bed` -- the identical failure the literals exist to remove, arriving
                # silently because a dict keyed by leaf cannot hold both.
                for key in hits:
                    inputs[key] = terra_literal(lit[leaf])
        outputs = expand_map(step["outputs"], callers, sfx, f"{where}.outputs", here)
        problems += here
        # `wdl` travels with the spec even though this module's five steps agree, because the field
        # that agrees today is not the field that always will: `dockstore()` wants the file basename and
        # `_declared_inputs()` wants the declared name, and for 12 of the 109 workflow-bearing WDLs at
        # `main` they differ. Keeping both here is what stops the next module from collapsing them.
        configs[where] = {"workflow": str(step["workflow"]), "wdl": str(step["wdl"]),
                          # The Dockstore entry, which for this module is NOT the WDL name; falls back to
                          # `workflow` so every pre-existing profile resolves exactly as before.
                          "dockstore": str(step.get("dockstore") or step["workflow"]),
                          "rootEntityType": str(step["rootEntityType"]),
                          "inputs": inputs, "outputs": outputs}
        if step.get("branch_only_inputs"):
            unknown = [k for k in step["branch_only_inputs"] if k not in inputs]
            for k in unknown:
                problems.append(f"{where}: branch_only_inputs names {k!r}, which this step does not "
                                f"bind, so the drop flag could never reach it")
            if not unknown:
                branch_only[where] = set(step["branch_only_inputs"])
        # The fetch list, carried per step and in profile order. Not folded into `configs` on purpose:
        # `CONFIGS` is the POSTed-config table that five probes and `batch_rerun_step.py` read as an
        # attribute, and an extra key in every step spec would ride into `show`/`body()` for a name that
        # is never POSTed. The consumer that wants it asks for it.
        if step.get("export"):
            exports[where] = [str(a) for a in step["export"]]
        # The row source, canonicalised and carried apart from `configs` for the same reason `export` is:
        # `CONFIGS` is the POSTed-config table that five probes and `batch_rerun_step.py` read as an
        # attribute, and `entity_row` is never POSTed -- it decides what the launcher passes as the row
        # NAME. Its SHAPE is refused by `_validate_step`, one home for that rule, so by this line the
        # document is already clean. A step naming `--entity` (or nothing) is absent, and the launcher
        # refuses that step by name rather than defaulting it to a `sample_set`.
        key = _row_source_key(step.get("entity_row"))
        if key:
            rows[where] = key
        # The image pins, carried verbatim and in profile order. Not expanded, not canonicalised, and not
        # folded into `configs`: the POSTed map keeps its `workspace.<attr>` binding for this key, and the
        # launcher merges the stated ref OVER it at body-build time so the provenance line can still say
        # where each ref came from. `_validate_step` refused every bad shape, so by this line the map is
        # either a tagged ref or the `--image` marker for a key this step really binds.
        if "images" in step:
            pins[where] = {str(k): str(v) for k, v in step["images"].items()}
        npath = sum(1 for v in list(inputs.values()) + list(outputs.values())
                    if binding_kind(v)[0] == "path")
        ntot = len(inputs) + len(outputs)
        notes.append(f"{where}: {len(inputs)} in / {len(outputs)} out "
                     f"({npath} path, {ntot - npath} literal)")
    if problems:
        return Loaded(p, True, {}, [], {}, {}, {}, {}, problems, notes)
    return Loaded(p, True, configs, callers, branch_only, exports, rows, pins, [], notes)


def main(argv=None) -> int:
    """The one door a shell consumer would knock on, and a readable view for a person.

    Not wired into `make` and not advertised: §6 says `cat` is the human view of a profile and that
    inventing discovery commands is a priced tax. This exists so `--print` can answer the two questions
    a profile answers differently from the file itself -- what the tokens expand to on THIS machine,
    and which values are paths rather than literals -- which is also what the selftest asserts.

    `--print-exports` is the door `terra/batch_fetch_compare.sh` knocks on, and it is the reason bash
    never opens `profiles/*.json` (§10's two-expander rule: a second reader would be a second JSON parser
    AND a second expander). Flat by contract -- one attribute name per line on stdout, provenance on
    stderr -- in the same `KEY=value`-flat spirit as the config layer's own bash door.
    """
    import argparse
    ap = argparse.ArgumentParser(description="show one module profile expanded (offline, read-only)")
    ap.add_argument("--module", default="", help=f"default {module_name()!r}")
    ap.add_argument("--print", action="store_true",
                    help="print the expanded bindings and their kind (path|literal)")
    ap.add_argument("--print-exports", action="store_true",
                    help="print the profile's export attribute names, one per line (for a shell "
                         "consumer); refuses with exit 4 naming every step it looked on")
    a = ap.parse_args(argv)
    r = load(a.module)
    if a.print_exports:
        # Two refusal classes, both exit 4: `require()` for a profile that cannot be used at all, and
        # `require_exports()` for one that loads fine and still has no export list. A caller must not be
        # able to read "no names" as "nothing to fetch, all clear".
        r.require("module_profile --print-exports")
        names = r.require_exports("module_profile --print-exports")
        print("module_profile: %d export attribute(s) for module %s, from %s (%s)"
              % (len(names), a.module or module_name(),
                 ", ".join(f"{s}.export" for s in r.exports), r.path), file=sys.stderr)
        for n in names:
            print(n)
        return 0
    if not r.found:
        print(f"module {a.module or module_name()}: {r.problems[0]}")
        print(FIELD_HELP)
        return 4
    if r.problems:
        print(f"module profile {r.path} is unusable:")
        for p in r.problems:
            print(f"  - {p}")
        return 4
    print(f"module {module_name()}  profile {r.path}")
    print(f"  callers bound: {', '.join(r.callers) or '(none)'}")
    for name, spec in r.configs.items():
        print(f"  {name}: wdl={spec['wdl']} workflow={spec['workflow']} "
              f"[{spec['rootEntityType']}] row={r.row_sources.get(name) or ROW_SOURCE_OPERATOR}")
        if a.print:
            for m in ("inputs", "outputs"):
                for k, v in spec[m].items():
                    kind, _ = binding_kind(v)
                    print(f"      {m[:-1]:5s} {k:56s} {kind:8s} {v}")
            # The pins, after the bindings they will be merged over: a reader comparing what `show`
            # prints against what the file states needs to see both, and which one wins.
            for k, v in r.image_pins.get(name, {}).items():
                print(f"      image {k:56s} {'stated' if v != IMAGE_SOURCE_OPERATOR else 'by flag':8s}"
                      f" {v}")
    for n in r.notes:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
