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
STEP_FIELDS = ("step", "wdl", "workflow", "rootEntityType", "inputs", "outputs",
               "branch_only_inputs", "export")
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
    "  anywhere      _why_<key>       a rationale beside the key it explains; `_why_unbound` for the\n"
    "                                keys deliberately left to a WDL default")


class Loaded:
    """What one profile read produced: the tables, or the findings. Never both empty.

    `configs`/`callers`/`branch_only_inputs` are the shapes the tools have always read; `exports` is the
    one this reader added for `batch_fetch_compare.sh` (step -> its `export` names, profile order), and
    `problem` is the single sentence a command prints when the tables cannot be used. `found=False` says
    why an absent profile is fine -- it is how `--help` answers without repo data.
    """

    def __init__(self, path, found, configs, callers, branch_only, exports, problems, notes):
        self.path = path
        self.found = found
        self.configs = configs
        self.callers = callers
        self.branch_only_inputs = branch_only
        self.exports = exports
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
        return Loaded(p, False, {}, [], {}, {}, [f"no profile at {p}"], [])
    try:
        with open(p) as fh:
            doc = json.load(fh)
    except ValueError as e:
        return Loaded(p, True, {}, [], {}, {}, [f"{p} is not readable JSON ({e})"], [])
    except OSError as e:
        return Loaded(p, False, {}, [], {}, {}, [f"{p} could not be read ({e.strerror})"], [])
    problems = validate(doc)
    if problems:
        return Loaded(p, True, {}, [], {}, {}, problems, [])
    callers = [str(c) for c in doc["callers"]]
    sfx = suffixes()
    configs, branch_only, exports = {}, {}, {}
    notes = []
    for step in doc["steps"]:
        where = str(step["step"])
        here = []
        inputs = expand_map(step["inputs"], callers, sfx, f"{where}.inputs", here)
        outputs = expand_map(step["outputs"], callers, sfx, f"{where}.outputs", here)
        problems += here
        # `wdl` travels with the spec even though this module's five steps agree, because the field
        # that agrees today is not the field that always will: `dockstore()` wants the file basename and
        # `_declared_inputs()` wants the declared name, and for 12 of the 109 workflow-bearing WDLs at
        # `main` they differ. Keeping both here is what stops the next module from collapsing them.
        configs[where] = {"workflow": str(step["workflow"]), "wdl": str(step["wdl"]),
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
        npath = sum(1 for v in list(inputs.values()) + list(outputs.values())
                    if binding_kind(v)[0] == "path")
        ntot = len(inputs) + len(outputs)
        notes.append(f"{where}: {len(inputs)} in / {len(outputs)} out "
                     f"({npath} path, {ntot - npath} literal)")
    if problems:
        return Loaded(p, True, {}, [], {}, {}, problems, notes)
    return Loaded(p, True, configs, callers, branch_only, exports, [], notes)


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
              f"[{spec['rootEntityType']}]")
        if a.print:
            for m in ("inputs", "outputs"):
                for k, v in spec[m].items():
                    kind, _ = binding_kind(v)
                    print(f"      {m[:-1]:5s} {k:56s} {kind:8s} {v}")
    for n in r.notes:
        print(f"  {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
