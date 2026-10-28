#!/usr/bin/env python3
"""Read a module profile: `profiles/<module>.json`, the data half of a head-to-head chain.

This is the only reader of `profiles/`, and the only place the `{frz}`/`{new}` suffix tokens and the
`@`-over-callers expansion are implemented. Shell consumers ask a subcommand here for flat output
(`--print-exports`) instead of parsing the JSON themselves.

Schema rules (see docs/module-profiles.md):
  * `wdl` (file basename) and `workflow` (declared workflow name) are separate fields; they differ
    for some WDLs (`DepthClustering` -> `ClusterDepth`).
  * `inputs` is the COMPLETE map, never a delta: Terra has no inheritance, so an omitted input
    silently takes the WDL default.
  * a value is a path iff it starts with `this.`/`workspace.`; anything else is a JSON literal.
  * index/sidecar files are handled in code, not in the profile.
  * `_why_<key>` siblings carry rationale; a `_why_` with no matching key is refused.
  * `schema_version` is mandatory; an unknown version is refused.

`export`: per step, the bare new-side attribute names a downstream tool (e.g.
`terra/batch_fetch_compare.sh`) fetches from the entity after the chain finishes. It is not the
`outputs` map, and the loader does not compare the two (scripts/selftest.d/profiles.sh does). It must
be a non-empty list of bare names, not `this.<attr>{new}` paths.

`entity_row`: where the step's entity row NAME comes from: a config-layer key (`BATCH`, read through
the resolver), or `--entity` when the operator must supply it. Absent means `--entity`. Unknown keys
are refused here, since `kit/gsvtk-config::get` raises `KeyError` on them. The entity TYPE is derived
from the step's own `this.<etype>_id` read (`derive_entity_type`); `rootEntityType` is a cross-check.

`images`: per step, one entry per `*_docker` input, valued with a TAGGED ref or `--image` (only the
per-call flag states the ref). Don't copy URIs from upstream's `dockers.json`; a pin states which
image YOUR change ran in. An untagged ref is refused unless written as an object declaring where its
version lives:

    "GATKSVPipelineSingleSample.linux_docker": {"ref": ".../google/ubuntu1804", "versioned_by": "path"}

`path` is the only accepted value, and only when the last path segment ends in digits, so the field
can't turn a floating ref into a pinned one. `--image` entries and refs that already carry a tag or
digest cannot carry `versioned_by`. Pins are returned as `Loaded.image_pins`, not inside `configs`;
the launcher merges them over the `workspace.<attr>` binding when it builds the body.

`load()` never raises, exits or creates a file, because `batch_configs.py` calls it at import (and
`--help` must work without repo data). Problems come back as findings; callers that need the tables
call `require()`, which exits 4 naming the file and every required field.

python 3.9+, stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config                                          # noqa: E402  (kit/ is a sys.path entry)

SCHEMA_VERSIONS = (1,)
DEFAULT_MODULE = "genotyping"

# The only two prefixes that make a value a path. Everything else is a literal.
PATH_PREFIXES = ("this.", "workspace.")
# Suffix tokens (`{frz}`/`{new}`) and `@` are only valid on attribute paths, never on literals.
TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
KNOWN_TOKENS = ("frz", "new")

TOP_FIELDS = ("schema_version", "name", "callers", "steps")
# Top-level fields a profile may omit. `freeze` says what `terra/batch_freeze.py` copies and writes.
# `unused_callers`: callers upstream wires that this module's cohort does not run; their per-caller
# keys are expected to be absent from the profile.
OPTIONAL_TOP_FIELDS = ("freeze", "unused_callers")
def terra_literal(value):
    """A profile value as a Terra method config stores it: always a STRING.

    Rawls types the input map as `Map[String, String]` and rejects other JSON types with HTTP 400.
    Non-strings are serialized with default `json.dumps` (e.g. `'["SR", "SD", "RM"]'`), matching what
    Terra itself stores.
    """
    return value if isinstance(value, str) else json.dumps(value)


# Step fields that explain a known difference from upstream. Only scripts/derive_profile.py reads them;
# each needs a `_why_<field>` beside it.
DRIFT_FIELDS = ("branch_only_outputs", "upstream_only_outputs", "template_overrides")
STEP_FIELDS = ("step", "wdl", "workflow", "dockstore", "literals", "rootEntityType", "entity_row", "inputs", "outputs",
               "branch_only_inputs", "export", "images") + DRIFT_FIELDS
# The `entity_row` value meaning "the operator supplies the row name via --entity".
ROW_SOURCE_OPERATOR = "--entity"
# The `images` value meaning "the operator supplies this ref via --image KEY=REF".
IMAGE_SOURCE_OPERATOR = "--image"
# Fields of an `images` entry written as an object: `ref`, and optionally `versioned_by`, which says
# where a tagless ref's version lives (e.g. `marketplace.gcr.io/google/ubuntu1804` has no tag but is
# versioned by its path).
IMAGE_ENTRY_REF = "ref"
IMAGE_ENTRY_VERSIONED_BY = "versioned_by"
IMAGE_ENTRY_FIELDS = (IMAGE_ENTRY_REF, IMAGE_ENTRY_VERSIONED_BY)
# Closed vocabulary: unknown values are refused.
VERSIONED_BY_PATH = "path"
VERSIONED_BY_VALUES = (VERSIONED_BY_PATH,)
# Optional prefix on config key names; stripped when canonicalising.
KEY_PREFIX = "GSVTK_"
# The suffix of a Terra name key (`this.<etype>_id`), which states the entity type a step runs on.
NAME_KEY_SUFFIX = "_id"
# `_why_` keys that pair with no bound key: rationale for inputs deliberately left unbound.
RESERVED_WHY = frozenset({"_why_unbound"})

# `freeze`: which baseline attributes `terra/batch_freeze.py` copies into the sandbox bucket and which
# entity row it writes them onto. Absent means the module has no frozen baseline.
FREEZE_FIELDS = ("entity_type", "entity_row", "attrs", "optional_attrs", "attr_suffix", "row_mode", "tag")
FREEZE_REQUIRED = ("entity_type", "entity_row", "attrs", "attr_suffix", "row_mode")
# `merge`: the frozen attributes are added to a row the sandbox already holds (the cohort batch row).
# `create`: the TSV also creates the row, so `plan` shows it before anything is written.
FREEZE_ROW_MODES = ("merge", "create")
# The written attribute name is `<attr><suffix>`: `{frz}` (FROZEN_SUFFIX) beside the baseline's own
# attribute, or "" when the frozen copy IS the attribute the step reads.
FREEZE_SUFFIXES = ("{frz}", "")

# Every field a profile may carry; printed in full when a profile is refused.
FIELD_HELP = (
    "  top level     schema_version (1), name, callers (list of caller names, may be empty),\n"
    "                steps (list, at least one)\n"
    "  every step    step            the config name, e.g. \"10-GenotypeBatch\"\n"
    "                wdl             the WDL FILE basename, without .wdl\n"
    "                workflow        the DECLARED workflow name (different from `wdl` for 12 of the\n"
    "                                109 workflow-bearing WDLs at main: DepthClustering->ClusterDepth)\n"
    "                rootEntityType  the Terra entity the step runs on (e.g. sample, sample_set, sample_set_set)\n"
    "                inputs          the COMPLETE input map, never a delta\n"
    "                outputs         the COMPLETE output map\n"
    "  optional top  unused_callers    callers upstream wires that this cohort does not run\n"
    "  optional step branch_only_inputs  keys the ref under test does not declare\n"
    "                branch_only_outputs    outputs the branch adds; absent upstream\n"
    "                upstream_only_outputs  upstream outputs the branch removes\n"
    "                template_overrides     inputs bound here that upstream's template leaves to\n"
    "                                       the WDL default\n"
    "                export            bare new-side attribute names a downstream tool fetches\n"
    "                                (not the `outputs` map, and not `this.<attr>` values -- names)\n"
    "                images            per *_docker input of THIS step: a TAGGED image ref, or\n"
    "                                `--image` when nothing offline states it and only the per-call\n"
    "                                flag does. Never a `workspace.`/`this.` path. Untagged is refused\n"
    "                                UNLESS the entry is an object declaring\n"
    "                                `\"versioned_by\": \"path\"`, meaning the version IS a path segment\n"
    "                                (`.../google/ubuntu1804`); a `path` declaration on a ref whose path\n"
    "                                names no version token is refused -- the field says where a version\n"
    "                                lives, it cannot invent one.\n"
    "                dockstore       the Dockstore ENTRY the method resolves from, when that is NOT the\n"
    "                                declared name: upstream publishes /wdl/GATKSVPipelineSingleSample.wdl\n"
    "                                as `SingleSamplePipeline` (see .github/.dockstore.yml). A launch that\n"
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
    "  optional top  freeze          what terra/batch_freeze.py freezes for this module: entity_type,\n"
    "                                entity_row (config key or `--entity`), attrs, optional_attrs,\n"
    "                                attr_suffix (\"{frz}\" or \"\"), row_mode (merge|create), tag (names\n"
    "                                the manifest, TSV and bucket prefix; \"\" keeps the unsuffixed names)\n"
    "  anywhere      _why_<key>       a rationale beside the key it explains; `_why_unbound` for the\n"
    "                                keys deliberately left to a WDL default")


class Loaded:
    """Result of reading one profile: the tables, or the findings.

    `configs`, `callers` and `branch_only_inputs` are the binding tables. `exports` maps step -> its
    `export` names; `row_sources` maps step -> the canonical config key its `entity_row` names;
    `image_pins` maps step -> {`*_docker` key: ImagePin(ref, versioned_by)}. All are in profile order.
    `problem` is the message printed when the tables can't be used; `found=False` means no profile
    file exists. `freeze` is the normalised `freeze` block (see `freeze_spec`), or None when the
    profile has none.

    Steps with no `entity_row` (or `--entity`) are absent from `row_sources`, and steps with no
    `images` map are absent from `image_pins`; the launcher refuses those by name where it needs them.
    """

    def __init__(self, path, found, configs, callers, branch_only, exports, rows, pins, problems,
                 notes, freeze=None):
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
        self.freeze = freeze

    @property
    def problem(self) -> str:
        return "\n".join(self.problems)

    def require(self, tag: str = "") -> None:
        """Exit 4 with the file, the findings and the complete field list if the profile is unusable."""
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
        lines.append("  from; docs/module-profiles.md explains what each field means and why.")
        # Exit 4, the config layer's code for a missing required value (as `gsvtk-config require`).
        print("\n".join(lines), file=sys.stderr)
        raise SystemExit(4)

    def require_exports(self, tag: str = "") -> list:
        """Every step's `export` names, de-duplicated in profile order; exit 4 if there are none.

        Individual steps may omit `export`, but an empty list for the whole profile is refused rather
        than returned, so a caller can't plan an empty fetch.
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
            "  (docs/module-profiles.md). It is NOT the `outputs` map: `outputs` says what a step",
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
    """Name (not path) of the module the loops are driving."""
    return config.get("MODULE", DEFAULT_MODULE) or DEFAULT_MODULE


def module_dir() -> str:
    """Directory holding module profiles (GSVTK_MODULE_DIR)."""
    return config.get("MODULE_DIR") or str(Path(__file__).resolve().parent.parent / "profiles")


def path(name: str = "") -> str:
    return os.path.join(module_dir(), f"{name or module_name()}.json")


def suffixes() -> dict:
    """The attribute suffixes `{frz}`/`{new}` expand to (config keys FROZEN_SUFFIX / NEW_SUFFIX)."""
    return {"frz": "_" + (config.get("FROZEN_SUFFIX", "frz") or "frz"),
            "new": "_" + (config.get("NEW_SUFFIX", "new") or "new")}


def binding_kind(value):
    """('path' | 'literal', value) for one bound value, decided by its prefix."""
    if isinstance(value, str) and value.startswith(PATH_PREFIXES):
        return "path", value
    return "literal", value


def name_key_type(attr: str) -> str:
    """<etype> for a name-key attribute, or "" if it carries no type.

    `sample_set_set_id` -> `sample_set_set` (the whole stem); `id` -> "". Must match
    `checks/terra_entity_check.py::name_key_type`; scripts/selftest.d/profiles.sh checks they agree.
    """
    if attr.endswith(NAME_KEY_SUFFIX) and len(attr) > len(NAME_KEY_SUFFIX):
        return attr[:-len(NAME_KEY_SUFFIX)]
    return ""


def derive_entity_type(values) -> tuple:
    """(etype, candidates) from a step's input bindings: the single `this.<etype>_id` it reads.

    Only single-component `this.` reads ending in `_id` count; member reads such as
    `this.sample_sets.<attr>` name the member type, not the row's, and are skipped. Pass input values
    only, not outputs.

    Returns ("", candidates) when zero or several types are found, so the caller can refuse with the
    right message. Candidates are in binding order.
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
    """Validate one step's `entity_row`: a known config key name, or `--entity`. Returns problems."""
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
    """The canonical key an `entity_row` names, or "" for `--entity`/absent. Assumes a valid value."""
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
    """A profile map -> the binding map the tool posts, with `@` expanded in place (order preserved)."""
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
    """Every reason this document cannot drive a chain. [] means usable.

    Strict, so a typo'd field name is reported instead of reading as an absent field.
    """
    problems = []
    if not isinstance(doc, dict):
        return [f"top level is a {type(doc).__name__}, not one profile object"]
    for key in doc:
        if str(key).startswith("_"):
            continue
        if key not in TOP_FIELDS + OPTIONAL_TOP_FIELDS:
            problems.append(f"top level: unknown field {key!r}; this schema knows "
                            f"{', '.join(TOP_FIELDS + OPTIONAL_TOP_FIELDS)} (a typo here would read as "
                            f"a missing field)")
    for key in TOP_FIELDS:
        if key not in doc:
            problems.append(f"top level: missing {key!r}")
    if doc.get("schema_version") not in SCHEMA_VERSIONS:
        problems.append(f"schema_version {doc.get('schema_version')!r} is not a version this build "
                        f"reads ({', '.join(str(v) for v in SCHEMA_VERSIONS)}); a stale reader and a "
                        f"stale file must not be guessed apart")
    if "callers" in doc and not isinstance(doc["callers"], list):
        problems.append(f"`callers` is a {type(doc['callers']).__name__}, not a list of caller names")
    if "unused_callers" in doc:
        unused = doc["unused_callers"]
        if not isinstance(unused, list) or not all(isinstance(c, str) and c for c in unused):
            problems.append("`unused_callers` is not a list of caller names")
        elif isinstance(doc.get("callers"), list) and set(unused) & set(doc["callers"]):
            problems.append(f"`unused_callers` and `callers` both name "
                            f"{', '.join(sorted(set(unused) & set(doc['callers'])))}")
        if "_why_unused_callers" not in doc:
            problems.append("`unused_callers` has no `_why_unused_callers`: an unexplained exemption "
                            "reads as agreement")
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        problems.append("`steps` is not a non-empty list, so this profile drives no step -- and an "
                        "empty chain is exactly what reads like nothing was wrong")
        return problems
    for i, step in enumerate(steps):
        problems += [f"step {i}: {p}" for p in _validate_step(step, steps)]
    if "freeze" in doc:
        problems += _validate_freeze(doc["freeze"], steps)
    return problems


def _validate_names(value, where: str, allow_empty: bool = False) -> list:
    """A list of bare, unique attribute names (no path prefix, no expansion syntax). Returns problems."""
    if not isinstance(value, list):
        return [f"{where} is a {type(value).__name__}, not a list of attribute names"]
    if not value and not allow_empty:
        return [f"{where} is an empty list; a freeze that names nothing copies nothing"]
    problems, seen = [], set()
    for i, attr in enumerate(value):
        if not isinstance(attr, str) or not attr.strip():
            problems.append(f"{where}[{i}] is {attr!r}, not an attribute name")
        elif attr.startswith(PATH_PREFIXES) or "{" in attr or "@" in attr or "." in attr:
            problems.append(f"{where}[{i}] is {attr!r}; names here are bare row attributes (no "
                            f"`this.`/`workspace.`, no {{frz}}/{{new}} token, no `@`)")
        elif attr in seen:
            problems.append(f"{where}[{i}] is {attr!r}, already listed")
        else:
            seen.add(attr)
    return problems


def _validate_freeze(value, steps) -> list:
    """Validate the top-level `freeze` block. Returns problems."""
    where = "freeze"
    if not isinstance(value, dict):
        return [f"{where} is a {type(value).__name__}, not an object"]
    problems = []
    for key in value:
        k = str(key)
        if k.startswith("_why_"):
            if k[len("_why_"):] not in value:
                problems.append(f"{where}: {k!r} explains {k[len('_why_'):]!r}, which this block "
                                f"does not set")
            continue
        if k.startswith("_"):
            continue
        if k not in FREEZE_FIELDS:
            problems.append(f"{where}: unknown field {k!r}; this schema knows {', '.join(FREEZE_FIELDS)}")
    for key in FREEZE_REQUIRED:
        if key not in value:
            problems.append(f"{where}: missing {key!r}")
    etype = value.get("entity_type")
    if "entity_type" in value:
        roots = [s.get("rootEntityType") for s in steps if isinstance(s, dict)]
        if not isinstance(etype, str) or not etype.strip():
            problems.append(f"{where}.entity_type is {etype!r}, not an entity type name")
        elif etype not in roots:
            problems.append(f"{where}.entity_type is {etype!r}, which no step runs on "
                            f"({', '.join(sorted(set(str(r) for r in roots)))}); a frozen row no step "
                            f"reads is a copy nobody uses")
    if "entity_row" in value:
        problems += _validate_row_source(value["entity_row"], where)
    if "attrs" in value:
        problems += _validate_names(value["attrs"], f"{where}.attrs")
    if "optional_attrs" in value:
        problems += _validate_names(value["optional_attrs"], f"{where}.optional_attrs", allow_empty=True)
        if isinstance(value.get("attrs"), list) and isinstance(value["optional_attrs"], list):
            both = sorted(set(map(str, value["attrs"])) & set(map(str, value["optional_attrs"])))
            if both:
                problems.append(f"{where}: {', '.join(both)} listed in both attrs and optional_attrs")
    if "attr_suffix" in value and value["attr_suffix"] not in FREEZE_SUFFIXES:
        problems.append(f"{where}.attr_suffix is {value['attr_suffix']!r}; it is one of "
                        f"{', '.join(repr(x) for x in FREEZE_SUFFIXES)}")
    if "row_mode" in value and value["row_mode"] not in FREEZE_ROW_MODES:
        problems.append(f"{where}.row_mode is {value['row_mode']!r}; it is one of "
                        f"{', '.join(FREEZE_ROW_MODES)}")
    if "tag" in value:
        tag = value["tag"]
        if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9_-]*", tag):
            problems.append(f"{where}.tag is {tag!r}; it names files and a bucket prefix, so it is "
                            f"letters, digits, `_` or `-` (\"\" for the unsuffixed names)")
    # A bare-named copy replaces the attribute a step reads, so some step on that entity must read it.
    if value.get("attr_suffix") == "" and isinstance(etype, str):
        reads = set()
        for s in steps:
            if isinstance(s, dict) and s.get("rootEntityType") == etype:
                reads |= {v for v in (s.get("inputs") or {}).values() if isinstance(v, str)}
        names = [a for k in ("attrs", "optional_attrs") for a in (value.get(k) or [])
                 if isinstance(a, str)]
        for a in names:
            if f"this.{a}" not in reads:
                problems.append(f"{where}: {a!r} is written under its own name (attr_suffix \"\") but "
                                f"no {etype} step reads `this.{a}`")
    return problems


def freeze_spec(doc_freeze, module: str) -> dict:
    """The normalised `freeze` block of a validated profile.

    `row_source` is the canonical config key (`BATCH`) or "" for `--entity`; `suffix` is the expanded
    attribute suffix; `tag` defaults to the module name.
    """
    sfx = suffixes()
    raw_suffix = doc_freeze["attr_suffix"]
    return {"entity_type": str(doc_freeze["entity_type"]),
            "row_source": _row_source_key(doc_freeze["entity_row"]),
            "attrs": [str(a) for a in doc_freeze["attrs"]],
            "optional_attrs": [str(a) for a in doc_freeze.get("optional_attrs") or []],
            "suffix": sfx["frz"] if raw_suffix == "{frz}" else "",
            "row_mode": str(doc_freeze["row_mode"]),
            "tag": str(doc_freeze["tag"]) if "tag" in doc_freeze else module}


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
    problems += _validate_drift(step, name)
    # Every `_why_<key>` must name a bound key (or be a reserved sibling).
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


def _validate_drift(step, name: str) -> list:
    """The DRIFT_FIELDS: lists of keys of this step's workflow, each with its `_why_` reason."""
    problems = []
    inputs, outputs = step.get("inputs") or {}, step.get("outputs") or {}
    wf = str(step.get("workflow") or "")
    for field in DRIFT_FIELDS:
        if field not in step:
            continue
        keys = step[field]
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
            problems.append(f"{name}: {field} is not a non-empty list of keys")
            continue
        if "_why_" + field not in step:
            problems.append(f"{name}: {field} has no `_why_{field}`: an unexplained exemption reads "
                            f"as agreement")
        for k in keys:
            if wf and not k.startswith(wf + "."):
                problems.append(f"{name}: {field} names {k!r}, which is not a {wf}.* key")
            elif field == "branch_only_outputs" and k not in outputs:
                problems.append(f"{name}: branch_only_outputs names {k!r}, which `outputs` does not list")
            elif field == "upstream_only_outputs" and k in outputs:
                problems.append(f"{name}: upstream_only_outputs names {k!r}, but `outputs` still lists it")
            elif field == "template_overrides" and k not in inputs:
                problems.append(f"{name}: template_overrides names {k!r}, which `inputs` does not bind")
    return problems


# One `images` entry: `ref` is the ref (or `--image`); `versioned_by` is "" when absent.
ImagePin = namedtuple("ImagePin", ("ref", "versioned_by"))


def ref_version_kind(ref: str, versioned_by: str = "") -> str:
    """`digest` | `tag` | `path` | `""`: how (if at all) this ref names a version.

    `""` means the ref floats (implicit `latest`). A `:` in the last path segment is a tag (so a
    registry port is ignored), `@sha256:` is a digest, and `path` applies only when `versioned_by` is
    `path` and the last path segment ends in digits (`.../google/ubuntu1804`, not `google/cloud-sdk`
    or `.../ubuntu1804-latest`). Used both at load and by the launcher's guard, since an `--image`
    ref can differ from the profile's.
    """
    text = str(ref).strip()
    last = text.rsplit("/", 1)[-1]
    if "@sha" in last:
        return "digest"
    if ":" in last:
        return "tag"
    if versioned_by == VERSIONED_BY_PATH and last[-1:].isdigit():
        return "path"
    return ""


def image_entry(key, value):
    """(ImagePin, "") or (None, problem) for one raw `images` entry.

    Accepts either the plain-string form (a ref, or `IMAGE_SOURCE_OPERATOR`) or an object with `ref`
    and optional `versioned_by`, and normalises both to `ImagePin`. Checks shape only;
    `_validate_images` checks versioning.
    """
    if isinstance(value, dict):
        extra = sorted(set(str(f) for f in value) - set(IMAGE_ENTRY_FIELDS))
        if extra:
            return None, (f"images key {key!r} carries field(s) {', '.join(repr(f) for f in extra)}, "
                          f"which this build does not read. An object entry holds "
                          f"{', '.join(IMAGE_ENTRY_FIELDS)} only -- a field nobody reads is a "
                          f"declaration that was never made")
        versioned_by = value.get(IMAGE_ENTRY_VERSIONED_BY, "")
        if not isinstance(versioned_by, str):
            return None, (f"images[{key!r}][{IMAGE_ENTRY_VERSIONED_BY!r}] is a "
                          f"{type(versioned_by).__name__}, not one of "
                          f"({', '.join(repr(v) for v in VERSIONED_BY_VALUES)})")
        ref = value.get(IMAGE_ENTRY_REF, "")
        if IMAGE_ENTRY_VERSIONED_BY in value and not str(versioned_by).strip():
            return None, (f"images[{key!r}][{IMAGE_ENTRY_VERSIONED_BY!r}] is blank. Name one of "
                          f"({', '.join(repr(v) for v in VERSIONED_BY_VALUES)}), or leave the field out "
                          f"and the tag rule applies")
    else:
        ref, versioned_by = value, ""
    return ImagePin(ref, versioned_by), ""


def _validate_images(value, name: str, step) -> list:
    """Validate one step's `images` map. Returns problems.

    Each key must be a `*_docker` key bound in the step's `inputs`. Refused values include
    `workspace.`/`this.` paths, untagged refs (unless `versioned_by: path` applies), pre-quoted refs
    (the launcher adds quotes), and an empty map (omit the field instead).

    Whether every `*_docker` binding has an entry is not checked here; the launch path refuses
    unpinned images by name.
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
        pin, err = image_entry(key, ref)
        if err:
            problems.append(f"{name}: {err}")
            continue
        ref, versioned_by = pin.ref, pin.versioned_by
        if str(key).startswith("_"):
            problems.append(f"{name}: images key {key!r} is a rationale INSIDE the map. A `_why_*` "
                            f"sibling belongs beside `images`, not in it: everything in here is a pin")
            continue
        if not isinstance(key, str) or not key.strip():
            problems.append(f"{name}: images has a blank key; a pin is keyed by the binding it pins "
                            f"({IMAGE_SOURCE_OPERATOR!r} or a tagged ref)")
            continue
        # No duplicate-key check: json.load has already collapsed duplicate keys.
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
        if versioned_by and versioned_by not in VERSIONED_BY_VALUES:
            problems.append(f"{name}: images[{key!r}][{IMAGE_ENTRY_VERSIONED_BY!r}] is {versioned_by!r}, "
                            f"which this build does not read ({', '.join(repr(v) for v in VERSIONED_BY_VALUES)}) "
                            f"-- a stale reader and a stale file must not be guessed apart, the rule "
                            f"`schema_version` enforces")
            continue
        if text == IMAGE_SOURCE_OPERATOR:
            if versioned_by:
                problems.append(f"{name}: images[{key!r}] states {IMAGE_SOURCE_OPERATOR!r} AND carries "
                                f"{IMAGE_ENTRY_VERSIONED_BY}={versioned_by!r}. That ref is the operator's "
                                f"to state on the command line, so this file cannot also assert how it is "
                                f"versioned -- drop one of the two")
                continue
            continue                                   # the operator supplies the ref
        if text.startswith(PATH_PREFIXES):
            problems.append(f"{name}: images[{key!r}] is {ref!r}, a binding VALUE, not a pin. "
                            f"`this.`/`workspace.` resolves at run time to whatever that attribute "
                            f"points at TODAY, which is the stale image this field exists to refuse; "
                            f"state the ref, or {IMAGE_SOURCE_OPERATOR!r}")
            continue
        # `@` and `{` are expansion syntax and don't belong in a ref, except `@sha256:` digests.
        digest = "@sha256:" in text
        if (("@" in text and not digest) or "{" in text):
            problems.append(f"{name}: images[{key!r}] is {ref!r}, which carries expansion syntax. A ref "
                            f"here is literal")
            continue
        if '"' in text:
            problems.append(f"{name}: images[{key!r}] is {ref!r}, already quoted. The launcher quotes a "
                            f"literal for Terra's expression parser itself, so this would post a "
                            f"doubly-quoted value")
            continue
        kind = ref_version_kind(text, versioned_by)
        if versioned_by and kind in ("tag", "digest"):
            problems.append(f"{name}: images[{key!r}] is {ref!r}, which states its own version, and also "
                            f"carries {IMAGE_ENTRY_VERSIONED_BY}={versioned_by!r}. Two provenances for one "
                            f"ref is one too many: the tag/digest IS the statement, so leave the field out")
            continue
        if kind in ("tag", "digest", "path"):
            continue                       # tagged or digest-pinned
        if versioned_by:
            # `versioned_by: path` only states where a version lives; it can't make a floating ref pinned.
            problems.append(f"{name}: images[{key!r}] declares {IMAGE_ENTRY_VERSIONED_BY}={versioned_by!r} "
                            f"but {ref!r} names no version -- its last path segment "
                            f"({text.rsplit('/', 1)[-1]!r}) holds no version token, so this ref answers "
                            f"with whatever is current at pull time. {IMAGE_ENTRY_VERSIONED_BY} says where "
                            f"the version lives ({VERSIONED_BY_PATH!r}: "
                            f"marketplace.gcr.io/google/ubuntu1804), it does not make a floating ref a "
                            f"pinned one: tag it, or state {IMAGE_SOURCE_OPERATOR!r}")
            continue
        problems.append(f"{name}: images[{key!r}] is {ref!r}, UNTAGGED: a registry path with no "
                        f"`:` floats to whatever is current at pull time, which answers no question "
                        f"about which code ran. Tag it (<repo>:<tag> or <repo>@sha256:...), or "
                        f"{IMAGE_SOURCE_OPERATOR!r}")
    return problems


def _validate_export(value, name: str) -> list:
    """Validate `export`: a non-empty list of bare, unique attribute names. Returns problems.

    Rejects path syntax such as `this.genotyped_pesr_vcf{new}` copied from `outputs`. Whether `outputs`
    writes each name is checked by scripts/selftest.d/profiles.sh, not here.
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
                            f"belong to `outputs`, which already carries them, so "
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
    """Read one module profile. Never raises, exits, or creates a file.

    Every failure (no file, bad JSON, unknown schema_version, orphan `_why_`, a token on a literal) is
    returned as a `Loaded` with `problems` filled and empty tables. Callers that need the tables call
    `Loaded.require()`.
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
        # Literals replace bindings whose upstream value is a `workspace.<attr>` read. Matched by leaf
        # name, so a literal naming no bound leaf is reported instead of adding a new input.
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
                # Apply to every binding sharing the leaf (e.g. both `par_bed` inputs), not just the first.
                for key in hits:
                    inputs[key] = terra_literal(lit[leaf])
        outputs = expand_map(step["outputs"], callers, sfx, f"{where}.outputs", here)
        problems += here
        # Keep both `wdl` (file basename, for dockstore()) and `workflow` (declared name, for
        # _declared_inputs()); they differ for some WDLs.
        configs[where] = {"workflow": str(step["workflow"]), "wdl": str(step["wdl"]),
                          # Dockstore entry name; defaults to `workflow`.
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
        # Kept out of `configs`, which is the POSTed table; `export` is never POSTed.
        if step.get("export"):
            exports[where] = [str(a) for a in step["export"]]
        # Row source, canonicalised; kept out of `configs` (never POSTed). Steps naming `--entity`
        # (or nothing) are omitted, and the launcher refuses them by name.
        key = _row_source_key(step.get("entity_row"))
        if key:
            rows[where] = key
        # Image pins, verbatim and in profile order, kept out of `configs`: the launcher merges each pin
        # over its `workspace.<attr>` binding when building the body.
        if "images" in step:
            # Already validated; reuse image_entry so both readers get the same ImagePin shape.
            pins[where] = {}
            for k, v in step["images"].items():
                pin, _err = image_entry(k, v)
                pins[where][str(k)] = pin if pin is not None else ImagePin(str(v), "")
        npath = sum(1 for v in list(inputs.values()) + list(outputs.values())
                    if binding_kind(v)[0] == "path")
        ntot = len(inputs) + len(outputs)
        notes.append(f"{where}: {len(inputs)} in / {len(outputs)} out "
                     f"({npath} path, {ntot - npath} literal)")
    if problems:
        return Loaded(p, True, {}, [], {}, {}, {}, {}, problems, notes)
    freeze = freeze_spec(doc["freeze"], Path(p).stem) if "freeze" in doc else None
    return Loaded(p, True, configs, callers, branch_only, exports, rows, pins, [], notes, freeze)


def main(argv=None) -> int:
    """CLI: a readable view of a profile, and flat output for shell consumers.

    `--print` shows what the tokens expand to on this machine and which values are paths vs literals.
    `--print-exports` prints one attribute name per line on stdout (provenance on stderr); it is how
    `terra/batch_fetch_compare.sh` reads exports without parsing JSON.
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
        # Both refusals exit 4: an unusable profile, or one with no export list.
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
    # Print the module actually loaded (from the resolved path), not module_name(), which ignores
    # --module.
    print(f"module {Path(str(r.path)).stem}  profile {r.path}")
    print(f"  callers bound: {', '.join(r.callers) or '(none)'}")
    for name, spec in r.configs.items():
        print(f"  {name}: wdl={spec['wdl']} workflow={spec['workflow']} "
              f"[{spec['rootEntityType']}] row={r.row_sources.get(name) or ROW_SOURCE_OPERATOR}")
        if a.print:
            for m in ("inputs", "outputs"):
                for k, v in spec[m].items():
                    kind, _ = binding_kind(v)
                    print(f"      {m[:-1]:5s} {k:56s} {kind:8s} {v}")
            # Print pins after the bindings they override.
            for k, v in r.image_pins.get(name, {}).items():
                kind = ("by flag" if v.ref == IMAGE_SOURCE_OPERATOR
                        else f"{v.versioned_by}-versioned" if v.versioned_by else "stated")
                print(f"      image {k:56s} {kind:8s} {v.ref}")
    for n in r.notes:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
