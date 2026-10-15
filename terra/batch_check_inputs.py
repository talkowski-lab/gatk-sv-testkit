#!/usr/bin/env python3
"""Re-check the step-10 (GenotypeBatch) input gates against Terra + GCS, read-only.

Answers, with evidence rather than confidence: does the config bind every input the WDL
requires, did the last submission actually resolve them to values, and do the `.tbi`
sidecars that a `+ ".tbi"` binding implies really exist in GCS?

    python terra/batch_check_inputs.py                # config vs WDL + sidecars
    python terra/batch_check_inputs.py --no-sidecars   # skip the gsutil loop
    python terra/batch_check_inputs.py --step 09       # any 06..10 config

Checks, in order:
  1. REQUIRED set from the branch WDL via `womtool inputs`. A missing call-input binding never
     fails WDL typecheck -- it fails at input-resolution time, after the submission has started.
     No jar and no captured JSON is a NAMED SKIP here, not a death: the required set is withheld,
     check 1 and the required-set half of check 3 say so by name and count, and everything below
     still runs.
  2. What the sandbox method config actually binds (set-diff both ways).
  2b. Whether every `this.*` / `workspace.*` reference in THAT CONFIG's bindings actually sits on the
     row / in the workspace today, three ways: ABSENT, PRESENT-EMPTY, PRESENT. Absence of this check
     is what produced issue #4.
  3. What the LAST SUBMISSION of that config resolved to (`inputsResolutions`) - i.e. proof the
     expressions evaluated to real values on the entity, not just that keys exist.
  4. For every resolved gs:// value, whether `<value>.tbi` also exists in GCS. The count of
     sidecars is reported, not assumed.

Check 1 reads `womtool inputs` JSON. `GSVTK_WOMTOOL_INPUTS_JSON=<file>` supplies that JSON from a
file instead of running java, which is how the optionality predicate is graded offline (see
`--help`, docs/config.md, and scripts/selftest.d/jarshape.sh). It changes check 1's input only:
the other checks still want Terra, and the jar is still what a real run uses. Check 2b follows the
same precedent with `GSVTK_ENTITY_JSON=<file>`, and BOTH seams print which source answered, so a
seam run can never be read as a live one.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
from terra import fapi  # noqa: E402  # via terra: one friendly missing-dependency message
import terra  # noqa: E402
import batch_configs as tc  # noqa: E402
import steps  # noqa: E402  # one reader for the step -> workflow map (see terra/steps.py)

# womtool is the only thing that can tell you a WDL's REQUIRED call inputs: a missing
# input binding does not fail WDL typecheck, it fails later, at input-resolution time,
# mid-submission. Fetch it once (docs/static-checks.md) and point WOMTOOL_JAR at it.
# gatk-sv's CI pins the artifact it downloads (`.github/workflows/testwdls.yaml`):
# https://github.com/broadinstitute/cromwell/releases/download/84/womtool-84.jar
WOMTOOL = os.environ.get("WOMTOOL_JAR", "")
JAVA = os.environ.get("JAVA", "java")
# The test seam for the optionality predicate: a captured `womtool inputs` JSON, read instead of
# shelling out to java. Documented in this tool's --help and in docs/config.md, asserted by
# scripts/selftest.d/jarshape.sh. Deliberately named GSVTK_* and deliberately NOT a shortcut past
# Terra: with it set, this tool still refuses to invent a required set if the file is unreadable.
WOMTOOL_INPUTS_JSON = os.environ.get("GSVTK_WOMTOOL_INPUTS_JSON", "")

# The same seam shape for check 2b: a captured Terra row read, graded offline, so the legs run with
# no network and no credentials. Named GSVTK_* like the one above, refused-by-name like it, and the
# source of the row data is PRINTED on every run -- the one property that matters most here, because
# "the workspace has no such attribute" and "a fixture file says a fixture row has no such attribute"
# are different sentences and a reader has to be able to tell them apart without re-deriving anything.
ENTITY_JSON = os.environ.get("GSVTK_ENTITY_JSON", "")

# How the live row read works is stated by row_attributes_live() below and printed by --explain: one
# point lookup per row. There is deliberately no page budget here -- a bounded walk whose budget is
# unknown to the reader looks exactly like a complete answer, and the walk is gone rather than enlarged.

# What optionality looks like in `womtool inputs` output, MEASURED at womtool-84 rather than
# assumed. Over the whole gatk-sv tree at 7fbf1171 (118 WDLs, 109 of which have a primary callable
# for womtool to describe: 4902 per-input entries):
#
#   * 4901 values are STRINGS, and an optional input's string always ends in the marker
#     " (optional)" or " (optional, default = <expr>)" -- 3140 and 396 entries respectively, with
#     ZERO entries carrying the word `optional` anywhere else in the string. Ground truth pair:
#     `"IntegrateGDVcf.sample_id": "String"` at 7fbf1171 (CI rejected that commit: "Required
#     workflow input 'IntegrateGDVcf.sample_id' not specified") vs
#     `"IntegrateGDVcf.sample_id": "String? (optional)"` at 01107996.
#   * 1 value is a DICT: a struct-typed input is expanded into its members,
#     e.g. `"BenchmarkGqFilter.original_scores": {"label": "String", ...,
#     "pickled_scores_file": "File? (optional)"}`. That entry says nothing about whether the INPUT
#     is optional (an optional struct arrives as a STRING with the marker, measured), and a member's
#     own "(optional)" sits in the dict's VALUES, which `"optional" not in value` never looks at.
#
# So the upstream predicate this file copied (`if "optional" in womtool_inputs[inp]`, gatk-sv's
# scripts/test/terra_validation.py) means two different things depending on shape: a SUBSTRING test
# on a string, and a DICT-KEY test on a struct. The second is the direction that cannot fail loudly:
# measured at womtool-84, `struct HasOptionalMember { String optional\n Int count }` as a REQUIRED
# input emits `{"optional": "String", "count": "Int"}`, and key membership then drops a required
# input out of the required set. Hence: test the marker, on strings only, and treat any shape that
# cannot carry the marker as required. Under-demanding a binding is invisible; over-demanding one is
# a loud FAIL naming the input, which is the failure direction this check has always chosen.
_OPTIONAL_MARKER = re.compile(r"\(optional\b")


def checkout() -> str:
    """A local gatk-sv clone: the checks must read the WDL of the code under test."""
    return str(config.checkout("GATK_SV_CHECKOUT"))


def is_optional_input(value) -> bool:
    """Whether one `womtool inputs` entry marks its input as NOT required (see _OPTIONAL_MARKER)."""
    if isinstance(value, str):
        return bool(_OPTIONAL_MARKER.search(value))
    return False          # dict (struct expanded into members): cannot carry the marker at 84


def womtool_source() -> str:
    """Where check 1's womtool JSON comes from. Printed, so a seam run is never read as a jar run."""
    if WOMTOOL_INPUTS_JSON:
        return "captured womtool JSON %s (GSVTK_WOMTOOL_INPUTS_JSON)" % WOMTOOL_INPUTS_JSON
    return "womtool jar %s (WOMTOOL_JAR)" % (WOMTOOL or "unset")


def womtool_spec(wdl_path: str) -> tuple:
    """`(womtool inputs JSON | None, withheld_reason | None)` for one WDL.

    Two different answers to two different problems, because their cures differ.

    * A seam that is CONFIGURED AND BROKEN (set, but not a file / not a womtool object) is a refusal:
      the caller asked for a specific capture, and answering that request with a skip would hide a
      typo behind a green run. Those still `raise SystemExit` naming the variable and the shape.
    * No seam and no jar is a SKIP, returned as `(None, reason)`. It used to be a `SystemExit`, which
      is what made this tool unrunnable on a machine without a jar: dying at check 1 withheld checks
      2, 2b, 3 and 4 -- including 2b, the one that answers the question that issue #4 was about, and
      the one that needs no jar at all. A withheld input is now reported as a withheld input (see
      `womtool_skip()`), which is the same standard the phases use (`withheld by the declared skip
      region …` in scripts/selftest.d/entity.sh): name the thing withheld and its extent, never
      silently pass.

    What did NOT move: refusing to invent a REQUIRED set. A required set derived from nothing is a
    check that cannot fail, and the whole reason this file exists is that a missing call-input binding
    surfaces mid-submission.
    """
    if WOMTOOL_INPUTS_JSON:
        if not os.path.isfile(WOMTOOL_INPUTS_JSON):
            raise SystemExit(f"GSVTK_WOMTOOL_INPUTS_JSON={WOMTOOL_INPUTS_JSON!r} is not a file, and no "
                             "required-input set is being guessed. Unset it to use the jar "
                             "(WOMTOOL_JAR=\u2026), or point it at a captured "
                             "`womtool inputs <wdl>` JSON (see --help).")
        with open(WOMTOOL_INPUTS_JSON) as fh:
            spec = json.load(fh)
        if not isinstance(spec, dict):
            raise SystemExit(f"GSVTK_WOMTOOL_INPUTS_JSON={WOMTOOL_INPUTS_JSON!r} does not hold a JSON "
                             "object keyed by '<Workflow>.<input>', which is what `womtool inputs` "
                             "prints.")
        return spec, None
    if not WOMTOOL or not os.path.exists(WOMTOOL):
        return None, (f"no womtool jar (WOMTOOL_JAR={'unset' if not WOMTOOL else repr(WOMTOOL) + ' is not a file'}) "
                      "and no captured `womtool inputs` JSON (GSVTK_WOMTOOL_INPUTS_JSON unset)")
    out = subprocess.run([JAVA, "-jar", WOMTOOL, "inputs", wdl_path],
                         cwd=checkout(), capture_output=True, text=True, check=True).stdout
    return json.loads(out), None


def womtool_skip(wdl: str, reason: str, bound: int) -> str:
    """The skip line check 1 prints when its input is withheld -- thing, extent, dependents.

    The dependents are the part that used to be invisible. Check 3 scans `inputsResolutions` for the
    REQUIRED names; with no required set that scan has nothing to scan, and it answered "PASS every
    required input resolved to a value" over an empty list, which is the silent pass this repo names
    as its failure class. So the skip names check 3 as well as check 1, and check 3 prints SKIPPED
    rather than PASS when `req` is withheld (see main()).
    """
    return (f"# SKIP womtool (check 1): {reason}. Withheld: the REQUIRED set of {wdl}, so {bound} "
            "bound input(s) were NOT cross-checked for a missing required binding (check 1), and "
            "check 3's unresolved scan has no required set to scan (it prints SKIPPED, not PASS). "
            "Everything that does not need womtool still ran: check 2 (bindings), check 2b "
            "(attribute existence), check 3 (submission status), check 4 (sidecars). Cure: export "
            "WOMTOOL_JAR=/path/to/womtool-<cromwell>.jar, or GSVTK_WOMTOOL_INPUTS_JSON=<captured "
            "`womtool inputs` JSON> -- see --help and docs/static-checks.md")


def required_from_spec(spec: dict, prefix: str) -> set:
    """Names (prefix-stripped) in a `womtool inputs` object that its marker says are required."""
    return {k.split(".", 1)[1] for k, v in spec.items()
            if not is_optional_input(v) and k.startswith(prefix + ".")}


def required_inputs(wdl_path: str, prefix: str) -> set:
    """Names (prefix-stripped) that WOM reports as required, per is_optional_input.

    A caller of THIS function asked for the set and cannot act on its absence, so a withheld womtool
    input is the old refusal here, naming the reason `womtool_spec` gave: an empty required set handed
    back to a caller that asked for one is a check that cannot fail. `main()` is the caller that CAN act
    on the absence, and it reads `womtool_spec()` directly and prints the skip.
    """
    spec, withheld = womtool_spec(wdl_path)
    if spec is None:
        raise SystemExit(f"no required-input set for {wdl_path}: {withheld}")
    return required_from_spec(spec, prefix)


# ----------------------------------------------------------------------------- check 2b
# The check this file never had. `womtool inputs` answers what the WDL NEEDS; `inputsResolutions`
# answers what a PAST submission RESOLVED. Neither answers "does the attribute this config binds
# really sit on my row now", and the issue that asked for it (#4) was wrong about the tool having a
# partial version of it: there is no entity read anywhere in this file. Its absence is what let an
# agent report step-10 inputs as missing, then clone a fresh workspace that really did lack them.
# `terra/batch_rerun_step.py::announce()` has been saying "prove those attributes exist first:
# python terra/batch_check_inputs.py --step <NN>" for two lanes now; this is the thing it pointed at.

_REFERENCE = re.compile(r"\b(this|workspace)\.([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")

# The three states, kept three words apart on purpose: "missing" and "empty" have different cures and
# a reader who sees one word for both cannot act on either. UNKNOWN and UNGRADED are the honest
# neighbours of PASS -- a reference this check could not read is reported as not read, never as fine.
STATE_PRESENT = "PRESENT"
STATE_EMPTY = "PRESENT-EMPTY"
STATE_ABSENT = "ABSENT"
STATE_UNKNOWN = "UNKNOWN"        # nothing answered it: no row name, row not in the listing, budget out
STATE_UNGRADED = "UNGRADED"      # not this check's question: member reads, shapes it refuses to guess


def binding_references(payload: dict) -> list:
    """Every `this.*` / `workspace.*` reference in a config's INPUT bindings, deduped.

    OUTPUTS are excluded from the question: a step's outputs name attributes it is about to WRITE,
    and grading those would call every unrun step broken. They are still read -- for the suffix, see
    `output_suffixes()` -- just not as requirements on the row.
    """
    refs = []
    for value in (payload.get("inputs") or {}).values():
        text = "" if value is None else str(value)
        for m in _REFERENCE.finditer(text):
            ref = f"{m.group(1)}.{m.group(2)}"
            if ref not in refs:
                refs.append(ref)
    return sorted(refs)


def output_suffixes(payload: dict) -> list:
    """The suffixes THIS CONFIG writes its outputs with, read off its own outputs map.

    The batch chain moves values between steps through suffixed attributes (`_frz` frozen baseline,
    `_new` chain output -- `tc.FZ`/`tc.NW`), so the suffix a step writes with is the clue to which
    suffix its inputs should carry. Derived from the payload rather than hardcoded here, so a config
    that writes a third suffix reports that one instead of being scored against the two shipped
    defaults. Longest first, so a name ending in both candidates matches the more specific one.
    """
    found = set()
    for value in (payload.get("outputs") or {}).values():
        leaf = str(value or "").rsplit(".", 1)[-1]
        for candidate in (tc.FZ, tc.NW):
            if candidate and leaf.endswith(candidate) and len(leaf) > len(candidate):
                found.add(candidate)
    return sorted(found, key=len, reverse=True)


def suffix_in_play(attr: str, suffixes: list) -> str:
    """Which of `suffixes` the attribute NAME carries, verbatim, or '-'.

    That is all this says. It is not an answer to "which attribute did the author mean": deciding
    that `this.cutoffs_new` is really `this.cutoffs_frz` is suffix INFERENCE, is a different check, and
    is deliberately not built here. The suffix is printed per reference so that check can be written on
    top of this line without re-deriving the payload or the suffix vocabulary.
    """
    for candidate in suffixes:
        if attr.endswith(candidate) and len(attr) > len(candidate):
            return candidate
    return "-"


def reference_shape(ref: str, etype: str, row: str, ws: str) -> tuple:
    """`(kind, entity_type, row_label, attribute)` -- which row a reference is read from.

    `this.<attr>` is the row's own attribute. `this.<collection>.<attr>` is a MEMBER read: the value
    sits on each member row, not on `row`. terra/steps.py is explicit that confusing the two is what
    came out wrong for 09-MergeBatchSites (which reads `this.sample_sets.<attr>` three times and
    `this.sample_set_set_id` once), so a member read is UNGRADED here -- answering it needs the member
    rows, and looking a member attribute up on the cohort row would print a confident ABSENT about
    nothing. `workspace.<attr>` is a workspace attribute: the workspace is the row.
    """
    scope, _, tail = ref.partition(".")
    parts = tail.split(".")
    if scope == "workspace":
        if len(parts) == 1:
            return ("workspace", "workspace", ws or "?", parts[0])
        return ("ungraded", "workspace", ws or "?", parts[-1])
    if len(parts) == 1:
        return ("row", etype, row or "?", parts[0])
    if len(parts) == 2:
        return ("member", parts[0], f"members of {row or '?'}", parts[1])
    return ("ungraded", etype, row or "?", parts[-1])


def entity_source() -> str:
    """Where check 2b's row data came from. Printed, so a seam run is never read as a live one."""
    if ENTITY_JSON:
        return f"captured Terra read {ENTITY_JSON} (GSVTK_ENTITY_JSON) \u2014 NOT a live read"
    return ("live Terra reads: terra.entity_attributes() -- one point lookup per row "
            "(GET .../entities/<type>/<name>): 404 is absence, a non-200 or an unrecognised body is "
            "UNKNOWN and never absence")


def entity_capture() -> tuple:
    """`(rows, workspace_attributes)` from GSVTK_ENTITY_JSON, or an exit naming the variable.

    `rows` is `{(entityType, entityName): attributes}`; `workspace_attributes` is a dict or None when
    the capture says nothing about the workspace (which is NOT the same as an empty workspace: a
    capture is what someone chose to capture, and silence in it is silence, not absence). Refuses a
    file with neither key -- the wrong file read as "the workspace has no attributes" is a hundred
    false ABSENTs, which is the direction this tool refuses on everywhere else.
    """
    if not os.path.isfile(ENTITY_JSON):
        raise SystemExit(f"GSVTK_ENTITY_JSON={ENTITY_JSON!r} is not a file, and no attribute is being "
                         "called absent on a row nothing read. Unset it to read the live workspace, "
                         "or point it at a captured read (see --help).")
    try:
        with open(ENTITY_JSON) as fh:
            doc = json.load(fh)
    except ValueError as exc:
        raise SystemExit(f"GSVTK_ENTITY_JSON={ENTITY_JSON!r} is not readable JSON ({exc}); nothing is "
                         "being guessed about your row.")
    if not isinstance(doc, dict) or not ({"entities", "workspace"} & set(doc)):
        raise SystemExit(f"GSVTK_ENTITY_JSON={ENTITY_JSON!r} holds neither `entities` nor `workspace`, "
                         "which are the only two things check 2b reads. Expected an object with an "
                         "`entities` list of {entityName, entityType, attributes} and/or a `workspace` "
                         "object carrying `attributes` (see --help).")
    rows = {}
    for item in doc.get("entities") or []:
        if not isinstance(item, dict) or "entityName" not in item or not isinstance(item.get("attributes"), dict):
            raise SystemExit(f"GSVTK_ENTITY_JSON={ENTITY_JSON!r} has an `entities` entry that is not "
                             "{entityName, entityType, attributes{}}: "
                             f"{str(item)[:120]!r}. Refusing to walk that as an empty row.")
        rows[(str(item.get("entityType") or ""), str(item["entityName"]))] = item["attributes"]
    ws_attrs = None
    if "workspace" in doc:
        wsdoc = doc["workspace"]
        if isinstance(wsdoc, dict) and isinstance(wsdoc.get("workspace"), dict):
            wsdoc = wsdoc["workspace"]               # the {"workspace": {…}} shape fiss also answers
        if not isinstance(wsdoc, dict) or not isinstance(wsdoc.get("attributes"), dict):
            raise SystemExit(f"GSVTK_ENTITY_JSON={ENTITY_JSON!r} has a `workspace` that is not an "
                             "object carrying an `attributes` object. Refusing to read that as an "
                             "empty workspace.")
        ws_attrs = wsdoc["attributes"]
    return rows, ws_attrs


def row_attributes_live(ns: str, ws: str, etype: str, row: str) -> tuple:
    """`(attributes | None, exists | None, note)` for one row, read through terra.py's OWN reader.

    ONE point lookup (`GET .../entities/<type>/<name>`), which is what `terra.entity_attributes()` wraps.
    This function used to walk `entity_sample()` pages instead, because this repo had no attribute-level
    reader -- and the walk is why this function had to keep saying "the listing was exhausted, so
    UNKNOWN" about a row it could have asked directly. The point lookup removes the sentence rather than
    answering it better: 404 IS absence, 200 IS the row, and anything else is a question this check
    declines to guess. The listing page stays what `terra.entity_exists()` says it is: truthy whether or
    not it holds the row, and therefore never the decider.

    The three arms, because the difference between the last two is the whole point:
      * dict (possibly EMPTY) + exists True  -> the row, and what it binds. Empty means bound-to-nothing,
        which is a different report from "no row" and gets different text below.
      * None + exists False                  -> 404. ABSENT, and the only arm allowed to say so.
      * None + exists None                   -> could not look: expired token, 403, or a 200 body this
        reader does not recognise. UNKNOWN, never ABSENT -- if that distinction goes away, this check
        starts manufacturing missing-input reports, which is issue #4 verbatim.
    """
    try:
        attrs = terra.entity_attributes(ns, ws, etype, row)
    except Exception as exc:                        # an expired ADC token and a 403 both land here
        return None, None, (f"terra.entity_attributes() could not answer ({type(exc).__name__}: "
                            f"{str(exc)[:120]}); existence is not being guessed")
    if attrs is None:
        return None, False, f"terra.entity_attributes() answered 404: no {etype} row named {row!r}"
    return attrs, True, "read with one point lookup (terra.entity_attributes())"


def attribute_state(attrs: dict, attr: str) -> str:
    """PRESENT / PRESENT-EMPTY / ABSENT for one attribute name. Never prints the value.

    Values on these rows are object paths, and printing them would put workspace coordinates in a
    terminal, a CI log and anything that shells this tool into a file -- `make -s audit` flags exactly
    those. So the state is the whole output, which is also the honest size of the claim: this check
    says whether a NAME is bound to something, not what it points at.
    """
    if attr not in attrs:
        return STATE_ABSENT
    value = attrs[attr]
    if value is None or (isinstance(value, str) and not value.strip()) \
       or (isinstance(value, (list, dict)) and not value):
        return STATE_EMPTY
    return STATE_PRESENT


def suffix_disagreement(missing: list, seen: dict, expected: str, src: tuple) -> str:
    """"0 of N under `_new`, N of N under `_h2h`" when a row's outputs exist under ANOTHER suffix.

    A bare `ABSENT` is true and useless in the most common shape of a mismatched sandbox: the step ran,
    it wrote its outputs, and it wrote them under a different suffix -- a `--tag` rerun writes `_TAG`, a
    second chain in one workspace carries its own `GSVTK_NEW_SUFFIX`, and a suffix exported for one run
    and not this one is the version of that mistake that looks like a broken branch. The data is there;
    the NAME this config asks for is not. Saying only ABSENT sends the reader to fetch outputs that exist.

    So for every missing name that ends with the expected suffix, look on the SAME row for the same stem
    under some other suffix, and report what the row actually carries. Three limits, because this is the
    kind of helpful line that becomes a lie if it is allowed to guess:
      * only rows that were READ are consulted (`seen` is filled from a real read, capture or live), so a
        row that answered 404, or a lookup that declined, can never contribute a counterpart;
      * the numbers are counted, not asserted -- `found of candidates`, and the two can differ;
      * a row that explains nothing prints NOTHING. An agreeing sandbox must not see this line, which is
        the arm of the contract that keeps the diagnostic from becoming noise.

    `src` is `(value, source)` straight from `config.resolve()`, so the line says whether the suffix in
    force is the shipped default or this shell's override -- the same provenance `gsvtk doctor --redact`
    prints, next to the claim that depends on it.
    """
    if not expected or not missing or not seen:
        return ""
    out = []
    for (rtype, rname), names in sorted(seen.items()):
        candidates = 0
        explained: dict = {}
        for ref in missing:
            base = ref.rsplit(".", 1)[-1]
            if not base.endswith(expected):
                continue
            candidates += 1
            stem = base[:-len(expected)]
            for nm in names:
                if nm != base and nm.startswith(stem + "_"):
                    explained.setdefault(nm[len(stem):], []).append(base)
        if not explained or not candidates:
            continue
        sfx, refs = max(explained.items(), key=lambda kv: (len(kv[1]), kv[0]))
        value, source = src
        out.append(f"      note    0 of {candidates} expected output(s) under `{expected}`, "
                   f"{len(refs)} of {candidates} under `{sfx}` -- on {rtype}/{rname} the outputs ARE "
                   f"there under a different suffix, so this is a suffix mismatch and not a missing "
                   f"input. The suffix in force is NEW_SUFFIX={value!r} (source: {source}); re-run with "
                   f"GSVTK_NEW_SUFFIX={sfx.lstrip('_')} set, or read the `{sfx}` attributes "
                   f"(a rerun launched with --tag {sfx.lstrip('_')} is what writes names like that).")
    return "\n".join(out)


def check_attribute_existence(payload: dict, step: str, ns: str, ws: str, row: str) -> list:
    """Check 2b: every reference in the config's bindings, looked up on the row it is read from.

    Returns failure strings for main()'s tally, and prints its own PASS / FAIL / SKIP lines: this is
    its own check, and it moves the exit code on its own, without womtool, without a submission and
    without a jar. The row's TYPE comes from `steps.root_entity()` (derived from the step's own
    `this.<etype>_id` read, refusing rather than defaulting to `sample_set`), and the row's NAME from
    `--entity` or the config key the profile names -- the same two sources, in the same order, as
    `terra/batch_rerun_step.py::_row_name`, because a launcher and a checker that disagree about the
    row disagree about the submission.
    """
    etype = steps.root_entity(step)
    refs = binding_references(payload)
    written = output_suffixes(payload)
    # What this config WRITES with, and what a reference NAME gets matched against. The shipped
    # suffixes are added to the second list because an INPUT can carry a suffix this step never writes
    # (`_frz` arrives from the freeze, `_new` from the chain before it), and printing `-` for those
    # would hide the clue the suffix exists to give.
    suffixes = written + [s for s in (tc.FZ, tc.NW) if s and s not in written]
    rows = ws_attrs = None
    if ENTITY_JSON:
        rows, ws_attrs = entity_capture()
    fails = []
    counts = {STATE_PRESENT: 0, STATE_EMPTY: 0, STATE_ABSENT: 0, STATE_UNKNOWN: 0, STATE_UNGRADED: 0}

    print(f"# check 2b: do the attributes {payload.get('name') or 'this config'} binds actually exist "
          f"(row type={etype}, {len(refs)} reference(s))")
    print(f"#   attribute source: {entity_source()}")
    print(f"#   output suffix this config writes with: {', '.join(written) or '(none: no suffixed output)'}"
          f" | reference names matched against: {', '.join(suffixes) or '(none)'}")

    lines = []
    states = []
    # ONE read per (row, type) and one per workspace, however many references point at them. Without
    # this the live path asked for the same row once per `this.*` binding -- 8 point reads plus 8 page
    # walks for a step-10 config, which reads as a check and behaves like a denial of service on a
    # read-only endpoint. `CALLS=` in scripts/selftest.d/jarshape.sh counts it, so the cache is graded
    # and not just claimed.
    row_cache: dict = {}
    workspace_cache: list = []
    # What each row the check ACTUALLY READ carries, by name. `suffix_disagreement` is the only consumer,
    # and it is the reason this exists: the absence verdict says what is not there, and this is the part
    # that can say what is -- without it a wrong suffix reads as a missing input.
    seen_names: dict = {}

    def read_row(ref_type: str, ref_row: str) -> tuple:
        key = (ref_type, ref_row)
        if key not in row_cache:
            row_cache[key] = row_attributes_live(ns, ws, ref_type, ref_row)
        return row_cache[key]

    def read_workspace() -> tuple:
        if not workspace_cache:
            try:
                doc = terra.workspace(ns, ws)
            except Exception as exc:
                workspace_cache.append((None, f"terra.workspace() could not answer ({str(exc)[:120]})"))
                return workspace_cache[-1]
            body = doc.get("workspace") if isinstance(doc.get("workspace"), dict) else doc
            attrs = body.get("attributes") if isinstance(body, dict) else None
            workspace_cache.append((attrs, "") if isinstance(attrs, dict) else
                                   (None, "the workspace read carried no `attributes` object; absence "
                                          "is not being guessed from its shape"))
        return workspace_cache[-1]

    for ref in refs:
        kind, ref_type, ref_row, attr = reference_shape(ref, etype, row, ws)
        state = STATE_UNGRADED
        why = ""
        read_attrs = None        # the row/workspace this reference resolved to, when one answered
        if kind == "member":
            why = "member read: the value sits on each row of the collection, not on this row"
        elif kind == "ungraded":
            why = "reference shape this check does not resolve"
        elif not ref_row or ref_row == "?":
            state = STATE_UNKNOWN
            source = steps.row_source(step)
            why = ("no row name: --entity did not name one and "
                   + (f"the config key it falls back to ({source}) holds nothing"
                      if source else "this step's profile names no row source at all")
                   + "; a row is not guessed")
        elif ENTITY_JSON:
            attrs = ws_attrs if kind == "workspace" else rows.get((ref_type, ref_row))
            read_attrs = attrs
            if attrs is None:
                state = STATE_UNKNOWN
                why = ("the capture says nothing about this row" if kind != "workspace"
                       else "the capture carries no `workspace` attributes")
            else:
                state = attribute_state(attrs, attr)
        elif kind == "workspace":
            attrs, why = read_workspace()
            read_attrs = attrs
            if attrs is None:
                state = STATE_UNKNOWN
            else:
                state = attribute_state(attrs, attr)
        else:
            attrs, _exists, note = read_row(ref_type, ref_row)
            read_attrs = attrs
            if attrs is None:
                state, why = STATE_UNKNOWN, note
            else:
                state = attribute_state(attrs, attr)
        if isinstance(read_attrs, dict):
            seen_names.setdefault(("workspace" if kind == "workspace" else ref_type,
                                   ref_row), set()).update(read_attrs)
        counts[state] += 1
        states.append(state)
        lines.append((state, f"      state={state:<13} type={ref_type} row={ref_row} "
                             f"attr={ref} suffix={suffix_in_play(attr, suffixes)}"
                             + (f"  [{why}]" if why else "")))

    for _state, line in sorted(lines, key=lambda t: (t[0], t[1])):
        print(line)

    graded = counts[STATE_PRESENT] + counts[STATE_EMPTY] + counts[STATE_ABSENT]
    print(f"# attribute existence: refs={len(refs)} graded={graded} "
          f"present={counts[STATE_PRESENT]} present-empty={counts[STATE_EMPTY]} "
          f"absent={counts[STATE_ABSENT]} unknown={counts[STATE_UNKNOWN]} "
          f"ungraded={counts[STATE_UNGRADED]}")

    missing = sorted(ref for ref, state in zip(refs, states) if state == STATE_ABSENT)
    empties = sorted(ref for ref, state in zip(refs, states) if state == STATE_EMPTY)
    if missing:
        fails.append(f"absent attributes: {missing}")
        print(f"  FAIL {len(missing)} bound attribute(s) are ABSENT from the row/workspace that answers "
              f"them (row type={etype}, row={row or 'unknown'}; see the state=ABSENT lines above): "
              f"{', '.join(missing)}")
        # ...and when the row is full of the SAME names under another suffix, say that too. This is the
        # whole of GitHub issue #4: nothing recorded which suffix a sandbox's outputs really sit under,
        # so a wrong one looked like a step that had not run.
        note = suffix_disagreement(missing, seen_names, tc.NW,
                                   config.resolve().get("NEW_SUFFIX", ("", "unknown")))
        if note:
            print(note)
    if empties:
        fails.append(f"empty attributes: {empties}")
        print(f"  FAIL {len(empties)} bound attribute(s) are PRESENT BUT EMPTY there "
              "(the key is on the row, the value is empty, and Cromwell resolves an empty File the "
              f"way it resolves nothing): {', '.join(empties)}")
    withheld = counts[STATE_UNKNOWN] + counts[STATE_UNGRADED]
    if withheld:
        print(f"  SKIP  attribute existence does not claim PASS: {withheld} of {len(refs)} reference(s) "
              f"were withheld ({counts[STATE_UNKNOWN]} UNKNOWN, {counts[STATE_UNGRADED]} UNGRADED "
              "-- see the [..] reason on each line above). A reference this check could not read is "
              "not a reference that passed.")
    elif not missing and not empties:
        print(f"  PASS every one of the {graded} graded reference(s) resolves to a non-empty value on "
              f"the row/workspace it is read from")
    return fails


def row_name(step: str, stated: str) -> str:
    """The row check 2b reads: `--entity` wins, else the config key the profile names, else ''. Never a guess.

    The same two sources in the same order as `terra/batch_rerun_step.py::_row_name`, so the tool that
    submits and the tool that checks cannot name different rows for the same step. '' means only the
    operator can answer it (`steps.row_source()` says so for `09-MergeBatchSites`, whose row is a
    cohort), and check 2b turns that into a named skip rather than a lookup on a made-up name.
    """
    if stated:
        return stated
    source = steps.row_source(step)
    return config.get(source) if source else ""


def last_submission(config_name: str) -> tuple:
    subs = terra.submissions(tc.NS, tc.WS, limit=30).get("submissions", [])
    for s in subs:
        if s.get("methodConfigurationName") == config_name:
            return s["submissionId"], s
    raise SystemExit(f"no submission found for {config_name}")


HELP_EPILOG = """\
environment:
  WOMTOOL_JAR=/path/to/womtool.jar     the jar check 1 runs (`java -jar $WOMTOOL_JAR inputs <wdl>`).
  JAVA=/path/to/java                   the java used for it (default: java on PATH).
  GSVTK_WOMTOOL_INPUTS_JSON=<file>     TEST SEAM: read check 1's `womtool inputs` JSON from FILE
                                       instead of running java, so the optionality predicate can be
                                       graded against a captured womtool output with no jar, no java
                                       and no network. It changes nothing else: the Terra and GCS
                                       checks still run, and a missing/unreadable FILE is refused by
                                       name rather than answered with a guessed REQUIRED set.
                                       Fixtures: scripts/selftest.d/fixtures/jar/ ; assertions:
                                       scripts/selftest.d/jarshape.sh.
                                       Neither this nor WOMTOOL_JAR is required to RUN the tool any
                                       more: with neither, check 1 and the required-set half of check
                                       3 print a named, counted SKIP and the rest still runs.
  GSVTK_ENTITY_JSON=<file>             TEST SEAM: answer check 2b's "does this attribute sit on my
                                       row" from a captured Terra read in FILE instead of reading
                                       Terra, so that check is gradable with no network and no
                                       credentials. Accepted shape: a JSON object with an optional
                                       `entities` list of {entityName, entityType, attributes} (the
                                       row shape a listing answers with, and the same one a point
                                       lookup carries) and an optional `workspace`
                                       object carrying `attributes` (what terra.workspace answers);
                                       at least one of the two is required, and anything else is
                                       refused naming the variable. The run prints which source
                                       answered, so a seam run is never mistaken for a live one.
                                       Values are never printed: attribute values on these rows are
                                       object paths, and `make -s audit` flags those.
                                       Fixtures/assertions as above.
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 epilog=HELP_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", default="10", choices=steps.known_steps())
    ap.add_argument("--no-sidecars", action="store_true")
    ap.add_argument("--entity", default="",
                    help="the entity ROW check 2b reads (of the step's root entity type). Wins over "
                         "the config key the step's profile names (see `gsvtk-config show`); with "
                         "neither, check 2b reports the row half as withheld rather than guessing a "
                         "row")
    a = ap.parse_args()

    wf = steps.workflow(a.step)
    # steps.config_name, not `next(c for c in tc.CONFIGS if c.startswith(step + "-"))`: that raised a
    # bare StopIteration for a step no config carries, naming neither the step nor what it tried
    # (docs/module-profiles.md §9.4).
    config_name = steps.config_name(a.step, tc.CONFIGS)
    spec, womtool_withheld = womtool_spec(f"wdl/{wf}.wdl")
    req = required_from_spec(spec, wf) if spec is not None else set()
    payload = terra.config_payload(tc.NS, tc.WS, tc.NS, config_name)
    bound = {k.split(".", 1)[1] for k in payload.get("inputs", {})}

    print(f"# {config_name}  (WDL wdl/{wf}.wdl @ {tc.BRANCH}, "
          f"methodConfigVersion {payload.get('methodConfigVersion')})")
    print(f"#   required by WOM: {'WITHHELD' if womtool_withheld else len(req)} "
          f"| bound by config: {len(bound)}  [source: {womtool_source()}]")
    fails = []
    miss, extra = sorted(req - bound), sorted(bound - req)
    if womtool_withheld:
        # The skip goes where the PASS used to be printable: an empty required set makes BOTH "every
        # required input is bound" and check 3's "every required input resolved" true of nothing, and
        # a PASS over nothing is the silent pass this file was written against.
        print(womtool_skip(f"wdl/{wf}.wdl", womtool_withheld, len(bound)))
    else:
        if miss:
            fails.append(f"unbound: {miss}")
        if not req:
            # A REQUIRED set of ZERO from a source that answered is almost always the wrong capture: a
            # `womtool inputs` JSON for another workflow reads exactly like a WDL that needs nothing, and
            # the prefix filter would then select nothing. "PASS every required input is bound" over an
            # empty set is true of nothing, so it is not printed -- the same rule that governs the
            # withheld case above, applied to a source that answered empty rather than not at all. A WDL
            # that genuinely requires nothing is rarer than that mistake, and this line refutes neither.
            print(f"# SKIP womtool answered, but reports NO required input for {wf}: 0 of the "
                  f"{len(bound)} bound input(s). "
                  f"[source: {womtool_source()}] -- if a set of 0 is not what you expect, that source is "
                  "a capture of another workflow. No PASS is printed for check 1 or check 3: both would "
                  "be true of nothing, and a WDL that truly requires nothing is the rarer case")
            print("  note: bound but not required (optional/defaulted): "
                  f"{extra}" if extra else "  note: nothing is bound")
        else:
            print("  " + ("PASS every required input is bound" if not miss
                          else f"FAIL unbound: {miss}"))
            if extra:
                print(f"  note: bound but not required (optional/defaulted): {extra}")

    # A binding that is an EXPRESSION rather than a value is the failure mode that survives
    # create/validate: Cromwell evaluates it, gets nothing usable, and quietly falls back to the
    # WDL's own default -- so the run completes with a plausibly wrong input. Cheap to detect here.
    expr = []
    for key, val in payload.get("inputs", {}).items():
        text = "" if val is None else str(val)
        stripped = text.strip()
        if "~{" in text or stripped.startswith(("{", "[", "if ", "select_all", "read_")) \
           or (stripped and stripped[0].isspace()):
            expr.append(f"{key.split('.', 1)[-1]}={text[:60]!r}")
    if expr:
        fails.append(f"expression-shaped bindings: {expr}")
        print("  FAIL expression-shaped bindings (Cromwell evaluates these; a WDL default can "
              "win):\n        " + "\n        ".join(expr))
    else:
        print("  PASS every binding is a literal or a plain workspace/attribute reference")

    # Check 2b, before the submission is even looked at: it decides nothing about a PAST run, it
    # answers a question about the ROW, and `last_submission` below exits when the config has never
    # been submitted -- a fresh workspace, which is exactly the state in which attribute existence is
    # the thing worth knowing.
    fails += check_attribute_existence(payload, a.step, tc.NS, tc.WS, row_name(a.step, a.entity))

    sub_id, _ = last_submission(config_name)
    full = fapi.get_submission(tc.NS, tc.WS, sub_id).json()
    resolutions, status = {}, []
    for wfl in full.get("workflows", []):
        status.append(wfl.get("status"))
        for r in (wfl.get("inputResolutions") or []):
            if r.get("value") is not None:
                resolutions[r["inputName"]] = r["value"]
    unresolvable = [k for k in (f"{wf}.{n}" for n in req) if k not in resolutions]
    print(f"#   last submission {sub_id[:8]} status={status} "
          f"resolved inputs={len(resolutions)}")
    if womtool_withheld or not req:
        print(f"  SKIP womtool's required set is {'withheld (check 1 above)' if womtool_withheld else 'empty (check 1 above)'}"
              f", so there is no required set to look for among the {len(resolutions)} resolved "
              "input(s). This line prints PASS only when a required set existed to scan -- the "
              "submission status above is still shown, because it is a fact and this is not a reason "
              "to hide it")
    else:
        if unresolvable:
            fails.append(f"unresolved: {unresolvable}")
        print("  " + ("PASS every required input resolved to a value" if not unresolvable
                      else f"FAIL unresolved: {unresolvable}"))

    if a.no_sidecars:
        if fails:
            print(f"# FAIL: {'; '.join(fails)}   (docs/terra-head-to-head.md)")
            return 1
        return 0
    files = {k: v for k, v in resolutions.items()
             if isinstance(v, str) and re.match(r"gs://[^\t]+$", v.strip())}
    have = need = 0
    print(f"# sidecar probe ({len(files)} gs:// inputs)")
    for k, v in sorted(files.items()):
        v = v.strip()
        r = subprocess.run(["gsutil", "-q", "stat", v + ".tbi"], capture_output=True, text=True)
        ok = r.returncode == 0
        need += 1
        have += ok
        print(f"    {'tbi OK  ' if ok else 'no tbi  '} {k} = {v}")
    print(f"# sidecars present: {have}/{need}")
    if fails:
        print(f"# FAIL: {'; '.join(fails)}   (docs/terra-head-to-head.md)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
