#!/usr/bin/env python3
"""Re-check the step-10 (GenotypeBatch) input gates against Terra + GCS, read-only.

Checks whether the config binds every input the WDL requires, whether the last submission
resolved them to values, and whether the `.tbi` sidecars implied by `+ ".tbi"` bindings exist.

    python terra/batch_check_inputs.py                # config vs WDL + sidecars
    python terra/batch_check_inputs.py --no-sidecars   # skip the gsutil loop
    python terra/batch_check_inputs.py --step 09       # any 06..10 config

Checks, in order:
  1. REQUIRED set from the branch WDL via `womtool inputs`. A missing input binding passes WDL
     typecheck and only fails at input-resolution time. With no jar and no captured JSON, this
     check (and the required-set half of check 3) is skipped by name; the rest still runs.
  2. What the sandbox method config binds (set-diff both ways).
  2b. Whether every `this.*` / `workspace.*` reference in the config's bindings exists on the row /
     workspace now: ABSENT, PRESENT-EMPTY or PRESENT.
  2c. Whether the suffix in force in this shell matches the suffix the workspace's stored NN-* method
     configs write their outputs with. Needs no entity row. Configs are assigned to steps by
     `terra/steps.py`; unscored configs are named and counted. If two stored configs of one step
     write different suffixes, both are printed and no expected value is claimed.
  3. What the last submission of that config resolved to (`inputsResolutions`).
  4. For every resolved gs:// value, whether `<value>.tbi` exists in GCS.

Test seams (see `--help`, docs/config.md, scripts/selftest.d/jarshape.sh):
`GSVTK_WOMTOOL_INPUTS_JSON=<file>` replaces the womtool run for check 1, `GSVTK_ENTITY_JSON=<file>`
replaces the row read for check 2b, and `GSVTK_METHODCONFIG_LIST_JSON=<file>` replaces the
method-config listing for check 2c. Each check prints which source answered.

`--strict` makes check 2c's withheld comparison a failure. Check 1's and 2b's skips still exit 0.
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

# womtool reports a WDL's REQUIRED call inputs. gatk-sv's CI uses
# https://github.com/broadinstitute/cromwell/releases/download/84/womtool-84.jar
WOMTOOL = os.environ.get("WOMTOOL_JAR", "")
JAVA = os.environ.get("JAVA", "java")
# Test seam: a captured `womtool inputs` JSON read instead of running java.
WOMTOOL_INPUTS_JSON = os.environ.get("GSVTK_WOMTOOL_INPUTS_JSON", "")

# Test seam for check 2b: a captured Terra row read.
ENTITY_JSON = os.environ.get("GSVTK_ENTITY_JSON", "")

# Test seam for check 2c: a captured `GET .../methodconfigs` listing.
METHODCONFIG_LIST_JSON = os.environ.get("GSVTK_METHODCONFIG_LIST_JSON", "")

# In `womtool inputs` output (womtool-84), an optional input's value is a string ending in
# " (optional)" or " (optional, default = <expr>)". A struct-typed input is expanded into a dict of its
# members, which cannot carry the marker; an optional struct arrives as a marked string. So: test the
# marker on strings only, and treat any other shape as required. Testing `"optional" in value` would
# match a struct member named `optional` and drop a required input.
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

    A seam that is set but unusable raises SystemExit naming the variable. No seam and no jar
    returns `(None, reason)` so the caller can print a skip and continue.
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
    """The skip line check 1 prints when its input is withheld; it also names check 3 as skipped."""
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

    Exits if the womtool input is withheld; `main()` uses `womtool_spec()` directly to print a skip.
    """
    spec, withheld = womtool_spec(wdl_path)
    if spec is None:
        raise SystemExit(f"no required-input set for {wdl_path}: {withheld}")
    return required_from_spec(spec, prefix)


# ----------------------------------------------------------------------------- check 2b
# Does each attribute the config binds actually exist on the row now?
_REFERENCE = re.compile(r"\b(this|workspace)\.([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*)")

# "missing" and "empty" are kept distinct. UNKNOWN means the reference could not be read.
STATE_PRESENT = "PRESENT"
STATE_EMPTY = "PRESENT-EMPTY"
STATE_ABSENT = "ABSENT"
STATE_UNKNOWN = "UNKNOWN"        # nothing answered it: no row name, or the row could not be read
STATE_UNGRADED = "UNGRADED"      # not this check's question: member reads, shapes it refuses to guess


def binding_references(payload: dict) -> list:
    """Every `this.*` / `workspace.*` reference in a config's INPUT bindings, deduped.

    Outputs are excluded: they name attributes the step is about to write.
    """
    refs = []
    for value in (payload.get("inputs") or {}).values():
        text = "" if value is None else str(value)
        for m in _REFERENCE.finditer(text):
            ref = f"{m.group(1)}.{m.group(2)}"
            if ref not in refs:
                refs.append(ref)
    return sorted(refs)


def output_suffixes(payload: dict, derive_unmatched: bool = False) -> list:
    """The suffixes this config writes its outputs with, read off its own outputs map.

    Candidates are the chain's suffixes (`tc.FZ`/`tc.NW`), longest first. With `derive_unmatched`
    (check 2c only), an output whose attribute name is its declared output name plus one `_token`
    yields that token as the suffix, e.g. `this.genotyped_pesr_vcf_h2h` -> `_h2h`. A tag that itself
    contains `_` is not recognised and yields no suffix.
    """
    found = set()
    for key, value in (payload.get("outputs") or {}).items():
        leaf = str(value or "").rsplit(".", 1)[-1]
        matched = False
        for candidate in (tc.FZ, tc.NW):
            if candidate and leaf.endswith(candidate) and len(leaf) > len(candidate):
                found.add(candidate)
                matched = True
        if derive_unmatched and not matched:
            declared = str(key or "").rsplit(".", 1)[-1]
            if declared and leaf.startswith(declared) and len(leaf) > len(declared):
                suffix = leaf[len(declared):]
                if suffix.startswith("_") and "_" not in suffix[1:]:
                    found.add(suffix)
    return sorted(found, key=len, reverse=True)


def suffix_in_play(attr: str, suffixes: list) -> str:
    """Which of `suffixes` the attribute name carries, verbatim, or '-'. No suffix inference."""
    for candidate in suffixes:
        if attr.endswith(candidate) and len(attr) > len(candidate):
            return candidate
    return "-"


def reference_shape(ref: str, etype: str, row: str, ws: str) -> tuple:
    """`(kind, entity_type, row_label, attribute)`: which row a reference is read from.

    `this.<attr>` is the row's own attribute. `this.<collection>.<attr>` is a member read and is
    UNGRADED, since the value sits on member rows. `workspace.<attr>` is a workspace attribute.
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

    `rows` is `{(entityType, entityName): attributes}`; `workspace_attributes` is None when the
    capture has no workspace (not the same as an empty workspace). A file with neither is refused.
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
    """`(attributes | None, exists | None, note)` for one row, via `terra.entity_attributes()`.

      * dict (possibly empty) + exists True  -> the row.
      * None + exists False                  -> 404: ABSENT.
      * None + exists None                   -> could not look (auth error, 403, unrecognised body):
                                                UNKNOWN, never ABSENT.
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

    Values are object paths, which should not end up in logs.
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

    For each missing name ending in the expected suffix, looks on the same row for the same stem under
    another suffix. Only rows that were actually read are consulted, and a row that explains nothing
    prints nothing. `src` is `(value, source)` from `config.resolve()`, so the line names where the
    suffix in force came from.
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

    Prints its own PASS / FAIL / SKIP lines and returns failure strings. The row type comes from
    `steps.root_entity()` and the row name from `row_name()`.
    """
    etype = steps.root_entity(step)
    refs = binding_references(payload)
    written = output_suffixes(payload)
    # Shipped suffixes are added for matching because inputs can carry suffixes this step never writes.
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
    # One read per (row, type) and one per workspace, however many references point at them.
    row_cache: dict = {}
    workspace_cache: list = []
    # Attributes of every row actually read, for suffix_disagreement().
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
        # If the row has the same names under another suffix, say so.
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


# ----------------------------------------------------------------------------- check 2c
# The expected suffix, from the workspace's stored method configs. Needs no row, so it works on a fresh
# sandbox before anything is submitted.


def methodconfig_source() -> str:
    """Where check 2c's listing came from. Printed, so a seam run is never read as a live one.

    A capture also bypasses the two-client cross-check in `terra.workspace_configs()`, and says so.
    """
    if METHODCONFIG_LIST_JSON:
        return (f"captured Terra read {METHODCONFIG_LIST_JSON} (GSVTK_METHODCONFIG_LIST_JSON) "
                "\u2014 NOT a live read, and NOT a two-client answer: this one file stands in for "
                "terra.workspace_configs(), so the `_read_both` cross-check that asks raw REST AND "
                "fapi.list_workspace_configs (and prints both counts when they disagree, "
                "docs/terra-head-to-head.md) did NOT run. Nothing here was confirmed by a "
                "second client.")
    return ("live Terra read: terra.workspace_configs() -- GET .../methodconfigs, read through "
            "_read_both (BOTH clients are asked every time: raw REST GET + "
            "fapi.list_workspace_configs, with a DISAGREE line printed when their counts differ), and "
            "an empty listing RAISES rather than answering 'nothing there', because this repo measured "
            "that endpoint answering [] over a non-empty workspace")


def methodconfig_capture() -> list:
    """The captured listing in GSVTK_METHODCONFIG_LIST_JSON, or a refusal naming the variable.

    Called before the first Terra read so a bad path is reported first. Unwrapped with
    `terra._as_items()`, the same as the live listing.
    """
    if not os.path.isfile(METHODCONFIG_LIST_JSON):
        raise SystemExit(f"GSVTK_METHODCONFIG_LIST_JSON={METHODCONFIG_LIST_JSON!r} is not a file, and no "
                         "expected output suffix is being guessed. Unset it to read the live listing "
                         "(terra.workspace_configs()), or point it at a captured "
                         "`GET .../methodconfigs` answer (see --help).")
    try:
        with open(METHODCONFIG_LIST_JSON) as fh:
            doc = json.load(fh)
    except ValueError as exc:
        raise SystemExit(f"GSVTK_METHODCONFIG_LIST_JSON={METHODCONFIG_LIST_JSON!r} is not readable JSON "
                         f"({exc}); nothing is being guessed about your workspace's configs.")
    try:
        return terra._as_items(doc, f"GSVTK_METHODCONFIG_LIST_JSON={METHODCONFIG_LIST_JSON}")
    except Exception as exc:
        raise SystemExit(f"GSVTK_METHODCONFIG_LIST_JSON={METHODCONFIG_LIST_JSON!r} is not a method-config "
                         f"listing. Expected the array `GET .../methodconfigs` answers (or an object "
                         f"carrying one), of objects with `name` and `outputs`. It said: {exc}")


def methodconfig_listing() -> tuple:
    """`(items | None, withheld_reason)`: the workspace's stored method configs, seam first.

    A broken seam is refused; no seam and an unreadable live listing return a withheld reason.
    """
    if METHODCONFIG_LIST_JSON:
        return methodconfig_capture(), ""
    try:
        return terra.workspace_configs(tc.NS, tc.WS), ""
    except Exception as exc:                # a 404, an expired ADC token, and _read_both's empty-listing refusal
        return None, (f"terra.workspace_configs() could not list the workspace's method configs "
                      f"(GET .../methodconfigs: {type(exc).__name__}: {listing_refusal(exc)})")


def listing_refusal(exc: Exception) -> str:
    """One printable line from a `_read_both` failure, keeping every client's message."""
    return " ".join(str(exc).split())[:420]


def config_attribution(name: str) -> tuple:
    """`(bucket, step)` for one stored config name, using `steps.py` to interpret names.

      `chain`          the exact `<NN>-<Workflow>` name this profile posts;
      `rerun`          a rerun of a known step (`10-GenotypeBatch-rerun`, `10-GenotypeBatch-h2h`);
      `bare`           a numberless config named after the workflow (`GenotypeBatch`);
      `numbered-other` carries a known step number but is neither of those names, e.g. a
                       per-submission snapshot copy (`<config>_<random>`);
      `no-step`        matches no step.

    The first three are scored against their step. `numbered-other` is scored only when the step has
    no `chain`/`bare` config. `no-step` is never scored. Unscored configs are named and counted.
    """
    leaf = str(name or "").rsplit("/", 1)[-1]
    for step in steps.known_steps():
        r = steps.rerun(step)
        workflow = r["workflow"]
        if leaf == r["config"]:
            return "chain", step
        if leaf == r["rerun_config"] or leaf.lower() == workflow.lower():
            return "rerun" if leaf == r["rerun_config"] else "bare", step
        prefix = r["config"] + "-"
        if leaf.startswith(prefix):
            tag = leaf[len(prefix):]
            try:
                if tag and steps.rerun(step, tag)["rerun_config"] == leaf:
                    return "rerun", step
            except SystemExit:            # check_tag refuses it, so it is not a rerun name after all
                pass
            return "numbered-other", step
        if leaf.startswith(step + "-"):
            return "numbered-other", step
    return "no-step", ""


def expected_suffix_from_configs(items: list) -> dict:
    """What the workspace's stored configs say, grouped by the step each one belongs to.

    Uses `output_suffixes(derive_unmatched=True)`. Returns `{by_step, silent, unscored, scored,
    stated}`: the suffixes each scored step states, configs that stated nothing, the unscored buckets
    by name, and the counts of each.
    """
    candidates: dict = {}                       # step -> {bucket: [(name, suffixes)]}
    unscored: dict = {}
    for entry in items:
        name = str(entry.get("name") or "")
        bucket, step = config_attribution(name)
        if not step:
            unscored.setdefault(bucket, []).append(name)
            continue
        candidates.setdefault(step, {}).setdefault(bucket, []).append(
            (name, output_suffixes(entry, derive_unmatched=True)))

    by_step: dict = {}
    silent: dict = {}
    scored: dict = {}
    for step, buckets in candidates.items():
        live = buckets.get("chain", []) + buckets.get("bare", [])
        if live and buckets.get("numbered-other"):
            unscored.setdefault("snapshot-copy-of-a-step-with-a-live-config", []).extend(
                n for n, _s in buckets["numbered-other"])
        group = (live or buckets.get("numbered-other", [])) + buckets.get("rerun", [])
        scored[step] = len(group)
        for name, suffixes in group:
            if not suffixes:
                silent.setdefault(step, []).append(name)
                continue
            for suffix in suffixes:
                by_step.setdefault(step, {}).setdefault(suffix, []).append(name)
    return {"by_step": by_step, "silent": silent, "unscored": unscored, "scored": scored,
            "stated": sum(len(v) for v in by_step.values())}


def unscored_clause(unscored: dict) -> str:
    """The named, counted 'these were NOT scored' tail every check 2c line carries. Names only."""
    if not unscored:
        return ""
    total = sum(len(v) for v in unscored.values())
    parts = [f"{', '.join(sorted(names))} [{bucket.replace('-', ' ')}]"
             for bucket, names in sorted(unscored.items())]
    return (f" -- NOT scored against any step: {total} config(s), named: "
            + "; ".join(parts) + " (a config that is not this chain's is a bucket, not a silence)")


def expected_suffix_verdict(step: str, derived: dict, value: str, provenance: str) -> tuple:
    """`(verdict_or_skip, withheld_label)`: the one-line answer, or the reason there is no answer.

      1. Two configs of one step write two suffixes: print every suffix with its configs, pick none,
         and return a withheld label.
      2. Every scored step names one suffix and none is the one in force: print the verdict.

    Agreement prints nothing.
    """
    by_step = derived["by_step"]
    clause = unscored_clause(derived["unscored"])
    disagreements = {step_: suffixes for step_, suffixes in by_step.items() if len(suffixes) > 1}
    if disagreements:
        lines = []
        for step_, suffixes in sorted(disagreements.items()):
            wrote = " and ".join(f"`{suffix}` ({', '.join(sorted(names))})"
                                 for suffix, names in sorted(suffixes.items(),
                                                             key=lambda kv: (-len(kv[1]), kv[0])))
            lines.append(f"  note    TWO STORED CONFIGS OF STEP {step_} write DIFFERENT suffixes: {wrote}")
        refused = "; ".join(
            f"step {step_} ({', '.join(sorted({n for ns in suffixes.values() for n in ns}))})"
            for step_, suffixes in sorted(disagreements.items()))
        head = (f"# DISAGREE stored-config suffix (check 2c): {len(disagreements)} step(s) of this "
                f"workspace's STORED configs write more than one suffix -- {refused} (the step under "
                f"test is step {step}). No EXPECTED "
                f"output suffix is derived from them and the suffix IN FORCE in this shell "
                f"({tc.NW!r}, NEW_SUFFIX={value!r}, source: {provenance}) is NOT scored against either, "
                f"because choosing between two stored suffixes would be a guess wearing the workspace's "
                f"name. Both are printed above, neither is picked")
        print(f"{head}{clause}. Each disagreeing step follows, with both suffixes and the configs that "
              f"write them; neither is picked. [source: {methodconfig_source()}] Cure: one of the two "
              "configs is the one this chain really posts -- delete, retag or republish the other so "
              "one step writes one suffix; --strict fails until it does")
        for line in lines:
            print(line)
        return "", ("check 2c (expected output suffix: two stored configs of one step write "
                    "different suffixes, so none is derived)")

    expected: dict = {}
    for suffixes in by_step.values():
        for suffix, names in suffixes.items():
            expected.setdefault(suffix, set()).update(names)
    expected = {suffix: sorted(names) for suffix, names in expected.items()}
    if tc.NW in expected:
        return "", ""
    wrote = ", ".join(f"`{suffix}` x{len(names)} ({', '.join(names)})"
                      for suffix, names in sorted(expected.items(), key=lambda kv: (-len(kv[1]), kv[0])))
    mine = "; ".join(f"step {step_} writes {', '.join(f'`{s}`' for s in sorted(suffixes))}"
                     for step_, suffixes in sorted(by_step.items()) if len(suffixes) == 1)
    return (f"  note    EXPECTED output suffix, derived from this workspace's STORED configs: {wrote}, "
            f"but the suffix IN FORCE in this shell is {tc.NW!r} (NEW_SUFFIX={value!r}, source: "
            f"{provenance}) -- expected and in-force disagree BEFORE anything is submitted, and no "
            f"entity row was read to say it [{mine}]{clause}"), ""


def check_expected_suffix(step: str) -> str:
    """Check 2c: is the suffix in force here the one the stored configs write with? '' or a withheld label.

    Prints a verdict only when they differ, and never changes the exit code by itself. Returns a
    withheld label (which `--strict` fails on) when the listing did not answer, yielded nothing, or
    two stored configs of one step disagree.
    """
    value, provenance = config.resolve().get("NEW_SUFFIX", ("", "unknown"))
    in_force = f"{tc.NW!r}, from NEW_SUFFIX={value!r} source: {provenance}"
    items, withheld = methodconfig_listing()
    if items is None:
        print(f"# SKIP stored-config suffix (check 2c): {withheld}. Withheld: the EXPECTED output suffix "
              f"derived from the workspace's stored NN-* method configs, so the expected-vs-in-force "
              f"comparison did NOT run for step {step} -- 0 of 0 stored config(s) consulted, and nothing "
              f"here is claiming the suffix in force ({in_force}) is the right one. [source: "
              f"{methodconfig_source()}] Cure: "
              "GSVTK_METHODCONFIG_LIST_JSON=<captured `GET .../methodconfigs` answer>, or make the live "
              "listing readable; --strict turns this skip into a failure")
        return "check 2c (expected output suffix: the stored-config listing did not answer)"

    derived = expected_suffix_from_configs(items)
    scored = sum(derived["scored"].values())
    clause = unscored_clause(derived["unscored"])
    if not derived["stated"]:
        quiet = "; ".join(f"{step_}: {', '.join(sorted(names))}"
                          for step_, names in sorted(derived["silent"].items()))
        quiet = quiet or "(nothing was attributed to a step at all, so there is no silent config to name)"
        print(f"# SKIP stored-config suffix (check 2c): the listing answered and yielded NO expected "
              f"suffix -- {scored} of {len(items)} listing entrie(s) belong to a step of this profile "
              f"(steps.py decides which, by name, not by an `NN-` pattern) and 0 of those {scored} state "
              f"a suffix this reader can read in their `outputs` maps. Silent, because the attribute "
              f"each of them writes is not named after its own declared output, so the subtraction "
              f"`output_suffixes()` falls back to has nothing to subtract: {quiet}. Withheld: the EXPECTED "
              f"output suffix, so the comparison did NOT run for step {step} against the suffix in force "
              f"({in_force}); an empty answer from a listing is not an agreement. [source: "
              f"{methodconfig_source()}]{clause} Cure: the chain's configs are the ones "
              "steps.config_name names -- if none of them is in the listing, that is the finding; "
              "--strict turns this skip into a failure")
        return "check 2c (expected output suffix: the stored-config listing yielded nothing)"

    verdict, refused = expected_suffix_verdict(step, derived, value, provenance)
    if refused:
        return refused
    if verdict:
        print(f"{verdict}. [source: {methodconfig_source()}] Cure: GSVTK_NEW_SUFFIX=<the suffix the "
              "stored configs write with> for this shell, or fix the stored configs "
              "(docs/troubleshooting.md)")
    return ""


def row_name(step: str, stated: str) -> str:
    """The row check 2b reads: `--entity` wins, else the config key the profile names, else ''.

    Same sources and order as `terra/batch_rerun_step.py::_row_name`. '' becomes a named skip.
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
  GSVTK_METHODCONFIG_LIST_JSON=<file>  TEST SEAM for check 2c: answer "what suffix do the method configs
                                       STORED in this workspace write their outputs with" from a captured
                                       `GET .../methodconfigs` answer in FILE instead of listing the
                                       workspace, so the EXPECTED half of the suffix verdict is gradable
                                       with no network and no credentials. Accepted shape: the array that
                                       endpoint answers (or an object carrying one), of objects with
                                       `name` and `outputs`; a path that is not a file, unreadable JSON
                                       and a shape that is not a listing are three refusals, each naming
                                       the variable. WHICH source answered is printed on every line this
                                       check can print. `NOT a live read` is the start of what the seam
                                       arm says, not the whole of it: `terra.workspace_configs()` asks
                                       TWO clients (raw REST and
                                       `fapi.list_workspace_configs`) through `_read_both`, so a capture
                                       also BYPASSES that two-client cross-check and says so. Both
                                       statements matter more here than for the other two seams, because
                                       this repo MEASURED this endpoint answering [] for a workspace that
                                       held configs (docs/terra-head-to-head.md), so an empty listing
                                       is never read as "no configs here": it is a named, counted skip.
                                       Which configs were SCORED is `steps.py`'s decision (chain config,
                                       tagged rerun, numberless workflow name), and whatever it did not
                                       score is named and counted on the same line.
                                       Fixtures/assertions as above.

flags:
  --strict                             Turn check 2c's withheld comparison into a failure: the listing
                                       did not answer, it answered and yielded nothing, or two stored
                                       configs of one step write two different suffixes (in which case
                                       no expected value is derived, so the comparison did not run).
                                       Only check 2c, deliberately: check 1's missing-jar skip and check
                                       2b's withheld-reference line shipped as DISCLOSED scope (issue
                                       #5), with legs grading the 0 exit, and a new flag does not
                                       rewrite that contract silently. What is new here had no contract
                                       to break, and the rule it follows is checks/wdl_gate.sh --strict:
                                       a comparison that did not run is not a comparison that passed.
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
    ap.add_argument("--strict", action="store_true",
                    help="fail (exit 1) if check 2c could not derive the EXPECTED output suffix from the "
                         "workspace's stored method configs, instead of printing it as a named, counted "
                         "SKIP. Deliberately that arm alone: check 1's missing-jar skip and check 2b's "
                         "withheld-reference line shipped as disclosed scope, graded at exit 0, and this "
                         "flag does not rewrite them silently. Same rule as checks/wdl_gate.sh --strict")
    a = ap.parse_args()

    if METHODCONFIG_LIST_JSON:
        # Refused before the first request: see methodconfig_capture().
        methodconfig_capture()

    wf = steps.workflow(a.step)
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
        # With no required set, a PASS for checks 1 and 3 would be vacuous.
        print(womtool_skip(f"wdl/{wf}.wdl", womtool_withheld, len(bound)))
    else:
        if miss:
            fails.append(f"unbound: {miss}")
        if not req:
            # Zero required inputs usually means a capture of the wrong workflow; don't print a PASS.
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

    # An expression-shaped binding can evaluate to nothing usable, and Cromwell then silently
    # falls back to the WDL default.
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

    # Checks 2b and 2c run before the submission lookup, which exits if the config was never submitted.
    fails += check_attribute_existence(payload, a.step, tc.NS, tc.WS, row_name(a.step, a.entity))

    withheld_2c = check_expected_suffix(a.step)
    if withheld_2c and a.strict:
        fails.append(f"--strict: {withheld_2c}")
        print(f"  FAIL --strict: {withheld_2c}, and a comparison that did not run is not a comparison "
              "that passed. The # SKIP line above names the reason, the extent and the cure "
              "(the same rule checks/wdl_gate.sh --strict applies to its prerequisite skips)")

    sub_id, _ = last_submission(config_name)
    full = terra._j(fapi.get_submission(tc.NS, tc.WS, sub_id), f"get_submission {sub_id}")
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
