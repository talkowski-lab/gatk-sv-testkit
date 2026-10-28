#!/usr/bin/env python3
"""Diff the config body this repo would POST against a method config captured from a workspace
where it already ran.

Unlike `scripts/golden_configs.py` (which checks this repo's own table against a checked-in,
regenerable capture), the oracle here is an operator-supplied live capture that is never committed.

The capture was typically recorded with call caching on and dev image tags, so a naive field-by-field
diff would fail on exactly the fields that matter. Instead:

  (i)  A structural diff over binding keys and symbolic bindings (`this.*` / `workspace.*`). Literal
       values are reported (`LITERAL-VALUES`) but never count toward the verdict. Nothing is
       normalized.

  (ii) Two assertions graded on the body alone: `useCallCache` must be stated and false, and every
       `*_docker` must be a quoted literal that names a version (tag, digest, or a path whose last
       segment is the version, per `module_profile.ref_version_kind`). The capture's values for these
       are printed for information only.

Exit codes
----------
    0  structural agreement, and both assertions hold
    1  findings: a structural bucket is non-empty, or an assertion failed
    2  usage, or a side that cannot be read or parsed
    3  the capture path is tracked by this repo (or git cannot tell); refused before it is opened
    4  an empty or absent `inputs`/`outputs` map on either side
    5  the capture has no `_gsvtk_capture` provenance block, or the block lacks a required key
    6  `rootEntityType` differs between the sides, or one states none
    7  the capture's binding keys cover too little of the body (see --min-coverage)

By default, values are printed only for symbolic bindings; differing literals are hashed and
`*_docker` values are never printed, since a live config holds workspace bucket paths and dev tags.
`--show-values` prints literals (not safe to publish).

Read-only: opens two files, no network, no profile, no checkout. `--selftest` uses synthetic fixtures.
python 3.9+, stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "kit"))
import module_profile  # noqa: E402  (stdlib-only; decides whether a docker ref names a version)

# A value starting with one of these is a binding; anything else is a literal (see kit/module_profile.py).
SYMBOLIC = ("this.", "workspace.")

DOCKER_SUFFIX = "_docker"          # the key suffix that makes an input a container ref

# Provenance a capture must carry so it can be re-captured.
PROVENANCE_KEY = "_gsvtk_capture"
PROVENANCE_REQUIRED = ("captured_utc", "command", "workspace", "config_name", "config_namespace")

# Top-level dict fields graded besides inputs/outputs (`rootEntityType` is a refusal, not a finding).
# This is terra/batch_rerun_step.py's DECIDING set minus `name`; scripts/selftest.d/profiles.sh keeps
# the two lists in sync.
TOP_GRADED_DICT = ("methodRepoMethod", "prerequisites")

TOP_NOT_GRADED = {
    "name": "the oracle's own label: a hand-assembled config is named by whoever assembled it, so a "
            "difference here is a fact about two documents, not about what ran",
    "namespace": "whose workspace holds the oracle; decides nothing about the launch this diff audits",
    "methodConfigVersion": "server-owned — bumped on every overwrite (see DECIDING's comment in "
                           "terra/batch_rerun_step.py)",
    "deleted": "a POST-time required member that the server owns",
    "deleteIntermediateOutputFiles": "a method config cannot state a submission option, so the capture "
                                     "can neither vouch for it nor contradict it",
    "useCallCache": "asserted directly instead (money rule ii) — this line is why that assertion exists",
    "maxMessageSize": "sent as a constant by the builder; not a field the oracle answers",
}

EXIT_OK, EXIT_FINDINGS, EXIT_USAGE, EXIT_TRACKED, EXIT_EMPTY, EXIT_PROVENANCE, \
    EXIT_ROOT_ENTITY, EXIT_VACUOUS = range(8)

_SHOW_VALUES = False               # set once, from --show-values, by run_diff()


class Refuse(Exception):
    """A side this tool will not score: its own exit code, and the name of what is missing."""

    def __init__(self, code: int, label: str, message: str):
        super().__init__(message)
        self.code, self.label, self.message = code, label, message


# ------------------------------------------------------------------------------------------- reading
def git_tracked(path: str) -> bool:
    """Is `path` tracked by this repo? (`git ls-files --error-unmatch` exits 0 only if tracked.)

    An ignored capture (e.g. under `recon-live/`) is fine; a committed one leaks workspace coordinates.
    """
    try:
        r = subprocess.run(["git", "-C", ROOT, "ls-files", "--error-unmatch", "--", path],
                           capture_output=True, text=True)
    except OSError as exc:                        # no git
        raise Refuse(EXIT_TRACKED, "GIT UNAVAILABLE",
                     "cannot establish whether the capture is tracked: `git` could not be run (%s), and a "
                     "capture nobody proved untracked may be one that was committed.\n"
                     "  refused without reading: %s" % (exc, path))
    return r.returncode == 0


def parse(text: str, side: str, path: str, dups: list) -> dict:
    try:
        doc = json.loads(text, object_pairs_hook=_pairs_hook(dups))
    except ValueError as exc:
        raise Refuse(EXIT_USAGE, "NOT JSON",
                     "the %s at %s is not one JSON object: %s" % (side, path, exc))
    if not isinstance(doc, dict):
        raise Refuse(EXIT_USAGE, "NOT AN OBJECT",
                     "the %s at %s is a %s, not a method-config object" % (side, path, type(doc).__name__))
    return doc


def load_body(spec: str, dups: list) -> dict:
    """The body under audit: a file, or `-` for the launcher's own stdout."""
    if spec == "-":
        if sys.stdin is not None and sys.stdin.isatty():
            raise Refuse(EXIT_USAGE, "NO BODY",
                         "--body is `-` (the default) and stdin is a terminal, so there is no body to "
                         "audit.\n  pipe one in — `GSVTK_MODULE=… ./gsvtk terra rerun-show --step NN "
                         "--entity ROW … |\\n  %s --capture <path>` — or pass --body FILE."
                         % os.path.relpath(os.path.abspath(__file__), ROOT))
        text = sys.stdin.read()
        if not text.strip():
            raise Refuse(EXIT_USAGE, "EMPTY BODY",
                         "stdin carried no body at all (0 bytes read). An empty input is not a body that "
                         "agrees with everything: name the file or fix the pipe.")
        return parse(text, "body", "stdin", dups)
    try:
        with open(spec, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise Refuse(EXIT_USAGE, "UNREADABLE", "cannot read the body %s: %s" % (spec, exc))
    return parse(text, "body", spec, dups)


def load_capture(path: str, dups: list) -> dict:
    """Read the capture, refusing first if this repo tracks it."""
    if not os.path.exists(path):
        raise Refuse(EXIT_USAGE, "CAPTURE NOT FOUND",
                     "no such capture file: %s\n"
                     "  a live capture is read-only evidence from the workspace that already ran. Capture "
                     "it with\n"
                     "  `terra.config_payload(<ns>, <ws>, <ns>, <config>)` or `gsvtk terra show`, put it "
                     "in an\n"
                     "  ignored path (`recon-live/`), and pass that path. --help lists every exit code "
                     "this tool\n"
                     "  can refuse with." % path)
    if git_tracked(path):
        raise Refuse(EXIT_TRACKED, "CAPTURE IS TRACKED",
                     "refusing to read %s: `git ls-files --error-unmatch` says this repo TRACKS it.\n"
                     "  A method config captured from a live workspace is nothing but that workspace's\n"
                     "  coordinates — bucket paths, dev registry tags, the workspace name — so it belongs\n"
                     "  in an ignored path (`recon-live/`) and never in the tracked set. `make audit`\n"
                     "  grades the tracked set, which is why committing one turns the gate red one commit\n"
                     "  later rather than never. Move it out of the tree, or capture it again — and never\n"
                     "  regenerate one from the body you meant to be auditing\n"
                     "  (scripts/selftest.d/golden/README.md: re-capture, never regenerate)." % path)
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        raise Refuse(EXIT_USAGE, "UNREADABLE", "cannot read the capture %s: %s" % (path, exc))
    return parse(text, "capture", path, dups)


def _pairs_hook(log: list):
    """Keep `json.loads`'s last-wins behavior (as a POST would) but record duplicate keys in `log`."""
    def hook(pairs):
        out, seen, dups = {}, {}, []
        for key, value in pairs:
            seen[key] = seen.get(key, 0) + 1
            if seen[key] > 1:
                dups.append((key, seen[key]))
            out[key] = value
        if dups:
            log.append((id(out), dups))
        return out
    return hook


def duplicate_scopes(doc: dict, log: list) -> list:
    """[(where, key, times)] — which object carried each duplicate, matched by identity after parsing."""
    scopes = {"at the top level": id(doc)}
    for name in ("inputs", "outputs", "methodRepoMethod"):
        if isinstance(doc.get(name), dict):
            scopes["in `%s`" % name] = id(doc[name])
    by_id = {i: where for where, i in scopes.items()}
    out = []
    for obj_id, dups in log:
        where = by_id.get(obj_id, "in a nested object")
        out += [(where, key, times) for key, times in dups]
    return sorted(out)


def require_map(doc: dict, side: str, name: str) -> dict:
    """The map, or a refusal if it is absent, not a dict, or empty."""
    if name not in doc:
        raise Refuse(EXIT_EMPTY, "EMPTY-MAP",
                     "EMPTY-MAP %s.%s — the %s states no `%s` map at all.\n"
                     "  A side with no bindings cannot disagree with anything, so scoring one would turn\n"
                     "  \"I read nothing\" into \"the two agree\": this repo's named failure class, and the\n"
                     "  reason an empty input has its own exit code instead of a 0."
                     % (side, name, side, name))
    value = doc[name]
    if not isinstance(value, dict):
        raise Refuse(EXIT_EMPTY, "EMPTY-MAP",
                     "EMPTY-MAP %s.%s — the %s's `%s` is a %s, not a map of bindings."
                     % (side, name, side, name, type(value).__name__))
    if not value:
        raise Refuse(EXIT_EMPTY, "EMPTY-MAP",
                     "EMPTY-MAP %s.%s — the %s's `%s` map holds 0 binding(s).\n"
                     "  Zero bindings is not agreement, it is an empty input: every count this tool would\n"
                     "  print would be 0, and every 0 would read as clean. Pass the side that carries it\n"
                     "  (--help names both), or re-capture the whole config."
                     % (side, name, side, name))
    return value


def require_provenance(cap: dict, path: str) -> dict:
    """The capture's own provenance block, or a refusal naming every key it is missing."""
    block = cap.get(PROVENANCE_KEY)
    if block is None:
        raise Refuse(EXIT_PROVENANCE, "PROVENANCE MISSING",
                     "PROVENANCE MISSING — %s carries no `%s` block.\n"
                     "  An oracle with no provenance cannot be re-captured, and a golden that cannot be\n"
                     "  re-captured is a claim nobody can re-check. The block names:\n"
                     "    %s.\n"
                     "  Capture the config read-only off the workspace that ran and write the block with\n"
                     "  it. Do NOT hand-assemble a golden out of the body you meant to be auditing\n"
                     "  (scripts/selftest.d/golden/README.md: re-capture, never regenerate)."
                     % (path, PROVENANCE_KEY, ", ".join(PROVENANCE_REQUIRED)))
    if not isinstance(block, dict):
        raise Refuse(EXIT_PROVENANCE, "PROVENANCE UNUSABLE",
                     "PROVENANCE UNUSABLE — %s's `%s` is a %s, not the object that names what was "
                     "captured." % (path, PROVENANCE_KEY, type(block).__name__))
    missing = [k for k in PROVENANCE_REQUIRED if not str(block.get(k, "")).strip()]
    if missing:
        raise Refuse(EXIT_PROVENANCE, "PROVENANCE INCOMPLETE",
                     "PROVENANCE INCOMPLETE — %s's `%s` block states no: %s.\n"
                     "  A re-capture needs: %s."
                     % (path, PROVENANCE_KEY, ", ".join(missing), ", ".join(PROVENANCE_REQUIRED)))
    return block


def require_root_entity(body: dict, cap: dict) -> str:
    """The shared root entity type, or a refusal if either side lacks one or they differ."""
    b, c = body.get("rootEntityType"), cap.get("rootEntityType")
    if not b or not c:
        raise Refuse(EXIT_ROOT_ENTITY, "ROOT ENTITY UNSTATED",
                     "ROOT ENTITY UNSTATED — %s states no `rootEntityType`.\n"
                     "  Nothing can be compared: without a type on both sides, every `this.*` binding this\n"
                     "  diff would score names a column of a row nobody named."
                     % ("the body" if not b else "the capture"))
    if b != c:
        raise Refuse(EXIT_ROOT_ENTITY, "ROOT-ENTITY MISMATCH",
                     "ROOT-ENTITY MISMATCH body=%r capture=%r\n"
                     "  The entity type is not a label: a wrong one \"resolves every `this.*` binding to\n"
                     "  nothing at runtime, after the VMs booted\" (terra/steps.py). Two sides rooted in\n"
                     "  two types cannot share bindings in the sense this diff means, so this is refused\n"
                     "  rather than filed as one finding among many." % (b, c))
    return str(b)


# ------------------------------------------------------------------------------------------------ diff
def is_symbolic(value) -> bool:
    return isinstance(value, str) and value.startswith(SYMBOLIC)


DOCKER_FORMS_PINNED = ("quoted-tagged", "quoted-path-versioned")


def docker_form(value) -> str:
    """The form of one `*_docker` value (never the value itself).

    Terra rejects an unquoted ref, and an unversioned ref is not reproducible. Versioning is decided by
    `module_profile.ref_version_kind`, the same predicate the launcher uses (a digest counts as tagged).
    No profile is read here, so any untagged ref whose last path segment ends in digits is accepted as
    path-versioned; that is wider than the launcher, which requires the profile to declare it.
    """
    if not isinstance(value, str):
        return "not-a-string"
    if is_symbolic(value):
        return "reads-a-binding"
    quoted = len(value) >= 2 and value.startswith('"') and value.endswith('"')
    if not quoted:
        return "not-quoted"
    kind = module_profile.ref_version_kind(value[1:-1], module_profile.VERSIONED_BY_PATH)
    if kind == "path":
        return "quoted-path-versioned"
    return "quoted-tagged" if kind else "quoted-untagged"


DOCKER_WORDS = {
    "reads-a-binding": "NOT A PIN: reads a workspace/entity attribute, so the image that runs is whatever "
                       "that attribute holds TODAY (measured in docs/troubleshooting.md: 8 of 14 differ "
                       "from the stored config's tags)",
    "not-quoted": "not a quoted literal — Terra parses a bare ref as an identifier and rejects the body",
    "quoted-untagged": "quoted but UNTAGGED — a floating reference: today's bytes are not tomorrow's",
    "quoted-path-versioned": "PINNED WITHOUT A TAG: the ref's last path segment ENDS IN DIGITS, which is "
                             "the launcher's own rule (module_profile.ref_version_kind) for a family whose "
                             "version IS its path, e.g. a GCE image family like …/ubuntu1804. NOT A TAG, "
                             "and no digest either — nothing here looked up what those bytes were. This "
                             "file reads no profile, so it cannot tell whether the profile behind this "
                             "body DECLARED the ref versioned_by:path; a body carrying one that was not "
                             "declared could only have come from --allow-unpinned-docker, which the "
                             "launcher prints when it takes it",
    "not-a-string": "not a string, so it is not a ref at all",
}


def key_coverage(body_keys: set, cap_keys: set) -> float:
    """Shared keys divided by the larger key set."""
    larger = max(len(body_keys), len(cap_keys))
    return (len(body_keys & cap_keys) / larger) if larger else 0.0


def diff_map(name: str, body: dict, cap: dict, min_cov: float) -> dict:
    """One map's structural diff. Literal values are bucketed separately from findings."""
    bk, ck = set(body), set(cap)
    cov = key_coverage(bk, ck)
    if cov < min_cov:
        raise Refuse(EXIT_VACUOUS, "VACUOUS DIFF",
                     "VACUOUS DIFF in `%s`: the capture shares %d of %d binding key(s) with the body "
                     "(coverage %.3f, floor --min-coverage %.3f).\n"
                     "  A structural verdict over a handful of keys is a verdict about nothing, and the\n"
                     "  shape this guard exists for is the one that happens: a hand-written note or a stub\n"
                     "  with two keys in it, which agrees with any body. Pass the capture that actually\n"
                     "  ran, or lower the floor deliberately and read the coverage off the SUMMARY line."
                     % (name, len(bk & ck), max(len(bk), len(ck)), cov, min_cov))
    out = {"name": name, "coverage": cov, "only_body": sorted(bk - ck), "only_capture": sorted(ck - bk),
           "sym_match": [], "sym_differs": [], "source_differs": [], "literal_agrees": [],
           "literal_differs": []}
    for key in sorted(bk & ck):
        bv, cv = body[key], cap[key]
        bs, cs = is_symbolic(bv), is_symbolic(cv)
        if bs and cs:
            (out["sym_match"] if bv == cv else out["sym_differs"]).append((key, bv, cv))
        elif bs != cs:
            out["source_differs"].append((key, "a binding" if bs else "a literal",
                                          "a binding" if cs else "a literal"))
        else:
            (out["literal_agrees"] if bv == cv else out["literal_differs"]).append((key, bv, cv))
    return out


def diff_top(body: dict, cap: dict) -> tuple:
    """The graded non-map fields: (findings, disclosed capture-side extras).

    Graded on the keys the body states; capture-only members are disclosed, not findings.
    """
    findings, extras = [], []
    for field in TOP_GRADED_DICT:
        bval, cval = body.get(field), cap.get(field)
        bmap = bval if isinstance(bval, dict) else ({} if bval is None else None)
        cmap = cval if isinstance(cval, dict) else ({} if cval is None else None)
        if bmap is None or cmap is None:
            if bval != cval:
                findings.append("%s: body %s, capture %s" % (field, _brief(bval), _brief(cval)))
            continue
        for key in sorted(bmap):
            if key not in cmap:
                findings.append("%s.%s: this body states %s, the capture answers no such key"
                                % (field, key, _brief(bmap[key])))
            elif bmap[key] != cmap[key]:
                findings.append("%s.%s: body %s != capture %s"
                                % (field, key, _brief(bmap[key]), _brief(cmap[key])))
        extras += ["%s.%s" % (field, k) for k in sorted(set(cmap) - set(bmap))]
    return findings, extras


def money(body: dict, cap: dict) -> dict:
    """The useCallCache and *_docker assertions, graded on the body; the capture's state is informational."""
    out = {"cache_body": body.get("useCallCache", "<absent>"),
           "cache_capture": cap.get("useCallCache", "<absent>"), "cache_finding": "",
           "docker_body": {}, "docker_capture": {}, "docker_findings": []}
    if "useCallCache" not in body:
        out["cache_finding"] = ("the body states no `useCallCache` at all, so the submission inherits a "
                                "default instead of making a choice — terra.submit refuses an unstated "
                                "choice for exactly this reason, and a cache hit answers \"did my change "
                                "alter the output\" before the VM boots")
    elif body["useCallCache"] is not False:
        out["cache_finding"] = ("the body says useCallCache=%r: a rerun exists to ask what the change did, "
                                "which a cache hit answers without running" % (body["useCallCache"],))
    for side, doc in (("body", body), ("capture", cap)):
        inputs = doc.get("inputs") or {}
        out["docker_%s" % side] = {k: docker_form(inputs[k])
                                   for k in sorted(inputs) if k.endswith(DOCKER_SUFFIX)}
    out["docker_findings"] = sorted(k for k, form in out["docker_body"].items()
                                    if form not in DOCKER_FORMS_PINNED)
    return out


# ------------------------------------------------------------------------------------------------ report
def _brief(value) -> str:
    """A value for a finding line: symbolic and non-path values in full, path-like values hashed
    (unless --show-values).
    """
    if _SHOW_VALUES or is_symbolic(value):
        return str(value)
    text = str(value)
    if "/" not in text:
        return text
    return "<path-like %s, %d chars, sha8=%s>" % (type(value).__name__, len(text),
                                                  hashlib.sha256(text.encode("utf-8")).hexdigest()[:8])


def _print(items: list, cap: int, fmt, out: list) -> None:
    for item in items[:cap]:
        out.append("      " + fmt(item))
    if len(items) > cap:
        out.append("      ... and %d more not shown (raise --max-list %d; the counts are never truncated)"
                   % (len(items) - cap, cap))


def report(body: dict, cap: dict, path: str, maps: tuple, top: tuple, mon: dict,
           dups: list, max_list: int) -> int:
    """Print part 1, part 2, what was not graded, then the verdict. Returns the exit code."""
    out: list = []
    prove = cap.get(PROVENANCE_KEY) or {}
    out.append("capture: %s%s  (from %s)"
               % (prove.get("config_name", "?"),
                  "" if not prove else " @ %s/%s, %s, by %s"
                  % (prove.get("config_namespace", "?"), prove.get("workspace", "?"),
                     prove.get("captured_utc", "?"), prove.get("command", "?")), path))
    out.append("body:    %s  root entity %s  (method %s @ %s)"
               % (body.get("name", "?"), body.get("rootEntityType", "?"),
                  (body.get("methodRepoMethod") or {}).get("methodPath", "?"),
                  (body.get("methodRepoMethod") or {}).get("methodVersion", "?")))
    out.append("")
    out.append("PART 1 — STRUCTURAL: binding keys and symbolic bindings (`%s`) only"
               % "` / `".join(SYMBOLIC))
    findings: list = []
    for d in maps:
        out.append("  `%s`: body %d key(s), capture %d key(s), shared %d, coverage %.3f"
                   % (d["name"], len(body[d["name"]]), len(cap[d["name"]]),
                      len(set(body[d["name"]]) & set(cap[d["name"]])), d["coverage"]))
        buckets = (("KEYS-BODY-ONLY", d["only_body"],
                    lambda k: "%s  — this body binds %s, the capture binds nothing there"
                              % (k, _brief(body[d["name"]][k]))),
                   ("KEYS-CAPTURE-ONLY", d["only_capture"],
                    lambda k: "%s  — the capture binds %s, this body binds nothing there"
                              % (k, _brief(cap[d["name"]][k]))),
                   ("SYMBOLIC-DIFFERS", d["sym_differs"],
                    lambda t: "%s  body %s != capture %s" % (t[0], t[1], t[2])),
                   ("BINDING-SOURCE-DIFFERS", d["source_differs"],
                    lambda t: "%s  body feeds %s, the capture feeds %s" % (t[0], t[1], t[2])))
        for word, items, fmt in buckets:
            if not items:
                continue
            findings += [word] * len(items)
            out.append("    %s %s: %d key(s)" % (word, d["name"], len(items)))
            _print(items, max_list, fmt, out)
        out.append("    SYMBOLIC-MATCH %s: %d key(s) bound to the same read on both sides"
                   % (d["name"], len(d["sym_match"])))
        out.append("    LITERAL-VALUES %s: %d value(s) byte-identical, %d value(s) differing — NEITHER "
                   "count is structural agreement or disagreement"
                   % (d["name"], len(d["literal_agrees"]), len(d["literal_differs"])))
        _print(d["literal_differs"], max_list,
               lambda t: "LITERAL-DIFFERS %s  body %s, capture %s"
                         % (t[0], _brief(t[1]), _brief(t[2])), out)

    top_findings, top_extras = top
    if top_findings:
        findings += ["top-level"] * len(top_findings)
        out.append("    TOP-LEVEL-DIFFERS: %d field or member(s) that decide what runs, graded on the keys "
                   "this body states" % len(top_findings))
        _print(top_findings, max_list, lambda t: t, out)
    if top_extras:
        out.append("    disclosed, never graded: capture-side members this body does not state: %s"
                   % ", ".join(top_extras))

    out.append("")
    out.append("PART 2 — ASSERTED, NOT VOUCHED FOR: the capture is not evidence for either of these")
    out.append("  MONEY useCallCache: body=%r -> %s"
               % (mon["cache_body"], "FINDING" if mon["cache_finding"] else "OK"))
    if mon["cache_finding"]:
        findings.append("useCallCache")
        out.append("      useCallCache: %s" % mon["cache_finding"])
    out.append("      disclosed, never graded: the capture says useCallCache=%r. Its value neither passes "
               "nor fails this run, in either direction — that is what \"the golden may not vouch for it\" "
               "means." % (mon["cache_capture"],))

    bad = mon["docker_findings"]
    findings += bad
    by_form = mon["docker_body"]
    pathed = sorted(k for k, form in by_form.items() if form == "quoted-path-versioned")
    # Tagged and path-versioned refs are counted separately; both are pinned.
    out.append("  MONEY *_docker (body): %d key(s), %d quoted tagged literal(s), "
               "%d pinned versioned-by-path%s -> %s"
               % (len(mon["docker_body"]),
                  sum(1 for f in by_form.values() if f == "quoted-tagged"), len(pathed),
                  " " + _census(mon["docker_body"]) if mon["docker_body"] else "",
                  "FINDING" if bad else "OK"))
    _print(bad, max_list, lambda k: "%-58s %s" % (k, DOCKER_WORDS.get(mon["docker_body"][k],
                                                                      mon["docker_body"][k])), out)
    if pathed:
        out.append("      PINNED WITHOUT A TAG: %d key(s) whose version is a path segment, accepted by the "
                   "launcher's predicate and disclosed here as its own state — never counted as a tag, "
                   "never counted as a finding"
                   % len(pathed))
        _print(pathed, max_list, lambda k: "%-58s %s" % (k, DOCKER_WORDS["quoted-path-versioned"]), out)
    out.append("      disclosed, never graded: the capture's %d `*_docker` value(s): %s. Those tags are "
               "somebody's dev builds and those gaps are Terra's defaults; neither excuses nor condemns "
               "what this body states, which is why the body's FORM is asserted instead of its values "
               "being compared."
               % (len(mon["docker_capture"]), _census(mon["docker_capture"]) or "none"))
    if dups:
        for where, key, times in dups:
            out.append("    DUPLICATE-KEY %s %s: bound %d time(s) — `json.loads` is last-wins, which is "
                       "what a POST binds; the dropped binding is named because a structural read that "
                       "picks a side has to say so" % (where, key, times))
    out.append("    duplicate-key census: %d key(s) bound more than once across the two documents "
               "(reported, not fatal — the same measured call terra/batch_configs.py makes)" % len(dups))

    out.append("")
    out.append("TOP-LEVEL fields this diff does NOT grade:")
    for field, why in TOP_NOT_GRADED.items():
        if field in body or field in cap:
            out.append("  %-32s %s" % (field, why))
    out.append("  graded: `rootEntityType` (a refusal, above) and %s, per key."
               % " and ".join("`%s`" % f for f in TOP_GRADED_DICT))

    shared = {d["name"]: len(set(body[d["name"]]) & set(cap[d["name"]])) for d in maps}
    out.append("")
    out.append("BODY-GOLDEN-SUMMARY inputs body=%d capture=%d shared=%d coverage=%.3f | outputs body=%d "
               "capture=%d shared=%d coverage=%.3f | literal-differs=%d | findings=%d"
               % (len(body["inputs"]), len(cap["inputs"]), shared["inputs"], maps[0]["coverage"],
                  len(body["outputs"]), len(cap["outputs"]), shared["outputs"], maps[1]["coverage"],
                  len(maps[0]["literal_differs"]) + len(maps[1]["literal_differs"]), len(findings)))
    print("\n".join(out))
    if findings:
        print("VERDICT BODY-GOLDEN-DIFF: %d finding(s). The body and the capture do not bind the same "
              "things, or a field this repo controls is wrong. Literal values were not counted."
              % len(findings))
        return EXIT_FINDINGS
    print("VERDICT BODY-GOLDEN-MATCH: structural agreement over binding keys and symbolic bindings, "
          "useCallCache stated false, every *_docker a quoted literal naming a version (tag, digest, or a "
          "path segment ending in digits — see PINNED WITHOUT A TAG above if any are listed). Literal "
          "values were NOT part of that verdict — see LITERAL-VALUES above.")
    return EXIT_OK


def _census(forms: dict) -> str:
    counts: dict = {}
    for form in forms.values():
        counts[form] = counts.get(form, 0) + 1
    return ", ".join("%s=%d" % kv for kv in sorted(counts.items()))


# ------------------------------------------------------------------------------------------------ main
def run_diff(args) -> int:
    global _SHOW_VALUES
    _SHOW_VALUES = bool(args.show_values)
    if not 0.0 < args.min_coverage <= 1.0:
        raise Refuse(EXIT_USAGE, "BAD --min-coverage",
                     "--min-coverage %r is not a fraction in (0,1]" % (args.min_coverage,))
    if args.max_list < 1:
        raise Refuse(EXIT_USAGE, "BAD --max-list",
                     "--max-list %r would hide every finding while still printing a verdict"
                     % args.max_list)
    body_dups: list = []
    cap_dups: list = []
    body = load_body(args.body, body_dups)
    cap = load_capture(args.capture, cap_dups)
    for side, doc in (("body", body), ("capture", cap)):
        require_map(doc, side, "inputs")
        require_map(doc, side, "outputs")
    require_provenance(cap, args.capture)
    require_root_entity(body, cap)
    maps = (diff_map("inputs", body["inputs"], cap["inputs"], args.min_coverage),
            diff_map("outputs", body["outputs"], cap["outputs"], args.min_coverage))
    dups = duplicate_scopes(body, body_dups) + duplicate_scopes(cap, cap_dups)
    return report(body, cap, args.capture, maps, diff_top(body, cap), money(body, cap), dups,
                  args.max_list)


def build_parser() -> argparse.ArgumentParser:
    here = os.path.relpath(os.path.abspath(__file__), os.getcwd())
    ap = argparse.ArgumentParser(
        prog="body_golden_diff.py", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Diff the config body this repo would POST against a hand-assembled method config "
                    "from a live workspace: structurally (binding keys and symbolic bindings), with two "
                    "fields asserted that the capture is explicitly not trusted for.",
        epilog="""\
examples
  # the body this repo builds, straight from the read-only launcher, against a live capture
  GSVTK_MODULE=single_sample GSVTK_BRANCH=main ./gsvtk terra rerun-show --step 01 --entity ROW \\
      --allow-unpinned-docker | %(here)s --capture recon-live/live-config-01-SingleSample-devfix.json

  # the same, with every image stated: money rule (ii) grades the BODY, so this is the arm that can
  # go green — one --image KEY=REG/PATH:TAG per *_docker input
  ./gsvtk terra rerun-show --step 01 --entity ROW --image KEY=REG/PATH:TAG … | %(here)s --capture …

  %(here)s --selftest          # synthetic fixtures, hand-derived expectations, no checkout, no network

exit codes
  0 structural agreement, both money assertions hold   1 findings (a diff, not a crash)
  2 usage / unreadable / not JSON                      3 the capture is TRACKED by this repo
  4 an empty or absent inputs/outputs map              5 no usable `_gsvtk_capture` provenance block
  6 rootEntityType differs, or one side states none     7 the capture's keys cover too little to diff over

boundary  scripts/golden_configs.py proves this repo's own table did not move, against a checked-in
          capture it can regenerate. This file proves this repo's body agrees with somebody else's
          proven config, against a capture it must never commit and cannot regenerate.
""" % {"here": here})
    ap.add_argument("--body", default="-", metavar="PATH",
                    help="the config body to audit, as JSON: a file, or `-` (default) for stdin. Feed it "
                         "the document a POST would send — `terra rerun-show` prints `make_body()`'s "
                         "dict, the same one `create` sends and the read-back compares, so a pipe from "
                         "it is that document (default: -)")
    ap.add_argument("--capture", metavar="PATH",
                    help="the hand-assembled method config captured from the workspace that already ran "
                         "(required, unless --selftest). Must be a path, never `-`: the file's "
                         "tracked-ness is decided with `git ls-files --error-unmatch` before it is "
                         "opened, and a tracked capture is refused")
    ap.add_argument("--min-coverage", type=float, default=0.90, metavar="F",
                    help="floor for (shared binding keys / the larger key set), per map, below which the "
                         "diff is REFUSED as vacuous rather than reported as agreement (default 0.90). A "
                         "ratio, so two small sides that agree still score 1.000: the floor answers the "
                         "shape that actually happens, an oracle with a couple of keys in it")
    ap.add_argument("--max-list", type=int, default=20, metavar="N",
                    help="findings printed per bucket before counting the rest (default 20; the counts "
                         "on the SUMMARY line are never truncated)")
    ap.add_argument("--show-values", action="store_true",
                    help="print literal values as well as their hashes. Off by default, and off is the "
                         "publishable setting: a captured config's literal values are somebody's bucket "
                         "paths and dev tags, and this tool's output is the kind of thing people paste "
                         "into docs. Do not paste this flag's output anywhere")
    ap.add_argument("--selftest", action="store_true",
                    help="build synthetic fixtures in a temp directory and check this file's own "
                         "behaviour: hand-derived counts, each refusal's exit code, both money rules, "
                         "and the tracked-capture refusal driven by a file this repo really does track")
    return ap


def main(argv: list = None) -> int:
    args = build_parser().parse_args(argv)
    if args.selftest:
        return selftest()
    if not args.capture:
        # argparse usage error: exit 2.
        build_parser().error("the following arguments are required: --capture (only --selftest needs no "
                             "documents to read)")
    try:
        return run_diff(args)
    except Refuse as r:
        print("%s\n  (exit %d: %s)" % (r.message, r.code, r.label), file=sys.stderr)
        return r.code


# --------------------------------------------------------------------------------------------- selftest
# Synthetic fixtures: plausible SHAPE, invented VALUES, no live coordinate anywhere.
WF = "TinyPipelineSingle"
DOCKER_KEY = WF + ".gatk_docker"


def fixture_body(**over) -> dict:
    """A body-shaped document: what `terra rerun-show` prints for a tiny module."""
    doc = {
        "namespace": "example-namespace",
        "name": "01-TinyPipelineSingle-rerun",
        "rootEntityType": "sample",
        "methodRepoMethod": {"sourceRepo": "dockstore",
                             "methodPath": "example.org/gatk-sv/TinyPipelineSingle",
                             "methodUri": "dockstore://example.org%2Fgatk-sv%2FTinyPipelineSingle/v1",
                             "methodVersion": "v1"},
        "methodConfigVersion": 1,
        "deleted": False,
        "inputs": {WF + ".sample_id": "this.sample_id",
                   WF + ".batch": "this.sample_id",
                   WF + ".cutoffs": '"example-bucket/ref/all_samples.cutoffs"',
                   DOCKER_KEY: '"example.org/gatk-sv/gatk:1.2.3"'},
        "outputs": {WF + ".final_vcf": "this.final_vcf_new",
                    WF + ".qc_file": "this.qc_file_new"},
        "prerequisites": {},
        "deleteIntermediateOutputFiles": False,
        "useCallCache": False,
    }
    doc.update(over)
    return doc


def fixture_capture(body: dict) -> dict:
    """The body minus fields a real method-config payload lacks (e.g. `useCallCache`), plus provenance."""
    out = {k: v for k, v in body.items()
           if k not in ("useCallCache", "deleteIntermediateOutputFiles", "maxMessageSize")}
    out["name"] = "01-TinyPipelineSingle-hand"
    out[PROVENANCE_KEY] = {"captured_utc": "2026-01-01T00:00:00Z",
                           "command": "terra.config_payload(<ns>, <ws>, <ns>, <config>)",
                           "config_name": "01-TinyPipelineSingle-hand",
                           "config_namespace": "example-namespace",
                           "workspace": "example-workspace", "note": "synthetic fixture"}
    return out


def selftest() -> int:
    import tempfile

    fails, total = [], [0]
    tool = os.path.abspath(__file__)

    def check(desc: str, cond: bool, detail: str = "") -> None:
        total[0] += 1
        print(("  ok    " if cond else "  FAIL  ") + desc
              + (("          " + detail) if (detail and not cond) else ""))
        if not cond:
            fails.append(desc)

    def run(args: list, stdin: str = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, tool] + args, capture_output=True, text=True,
                              input=stdin, cwd=ROOT)

    def write(dirpath: str, name: str, doc) -> str:
        path = os.path.join(dirpath, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(doc if isinstance(doc, str) else json.dumps(doc, indent=1))
        return path

    def both(r: subprocess.CompletedProcess) -> str:
        return "exit %s\n%s%s" % (r.returncode, r.stdout, r.stderr)

    with tempfile.TemporaryDirectory() as dd:
        body_a = write(dd, "body-a.json", fixture_body())
        cap_a = write(dd, "capture-a.json", fixture_capture(fixture_body()))

        r = run(["--body", body_a, "--capture", cap_a])
        check("4 input keys and 2 output keys shared, symbolically identical, cache off, image pinned: "
              "exit 0 and the match verdict says which of that it did NOT count",
              r.returncode == EXIT_OK and "VERDICT BODY-GOLDEN-MATCH" in r.stdout
              and "findings=0" in r.stdout, both(r))
        check("the summary's numbers are this fixture's own, derived by hand: 4/4/4 inputs and 2/2/2 "
              "outputs at coverage 1.000 on both maps",
              "inputs body=4 capture=4 shared=4 coverage=1.000" in r.stdout
              and "outputs body=2 capture=2 shared=2 coverage=1.000" in r.stdout, r.stdout)
        check("2 input and 2 output bindings are SYMBOLIC-MATCH; the 2 input literals are reported as "
              "NOT structural agreement",
              "SYMBOLIC-MATCH inputs: 2 key(s)" in r.stdout
              and "SYMBOLIC-MATCH outputs: 2 key(s)" in r.stdout
              and "LITERAL-VALUES inputs: 2 value(s) byte-identical, 0 value(s) differing" in r.stdout,
              r.stdout)
        check("money rule (i) as designed: the capture carries no useCallCache, and that is DISCLOSED — "
              "a golden's silence neither passes nor fails the run",
              "the capture says useCallCache='<absent>'" in r.stdout
              and "MONEY useCallCache: body=False -> OK" in r.stdout, r.stdout)
        check("money rule (ii): 1 of 1 body image refs is a quoted tagged literal, and the capture's is "
              "graded by FORM into the disclosure with no value printed",
              "*_docker (body): 1 key(s), 1 quoted tagged literal(s), 0 pinned versioned-by-path "
              "quoted-tagged=1 -> OK" in r.stdout
              and "disclosed, never graded: the capture's 1 `*_docker` value(s): quoted-tagged=1"
              in r.stdout, r.stdout)

        # Path-versioned refs: accepted only when the last segment ends in digits.
        for name, value in [("family", '"example.org/gatk-sv/ubuntu1804"'),
                            ("launder", '"example.org/gatk-sv/ubuntu1804-latest"'),
                            ("noversion", '"example.org/gatk-sv/cloud-sdk"')]:
            rp = run(["--body", write(dd, "body-path-%s.json" % name,
                                      fixture_body(inputs=dict(fixture_body()["inputs"],
                                                               **{DOCKER_KEY: value}))),
                      "--capture", cap_a])
            if name == "family":
                mline = next((l for l in rp.stdout.splitlines() if "MONEY *_docker (body)" in l), "")
                check("POSITIVE CONTROL: a quoted untagged ref whose last segment ends in DIGITS is "
                      "the launcher's own version-in-path answer — PINNED, in its own named bucket, at "
                      "exit 0, and NOT counted as a tag (the BODY's census says not one quoted-tagged; the "
                      "capture's own tagged ref is disclosed on its own line and moves nothing)",
                      rp.returncode == EXIT_OK and "findings=0" in rp.stdout
                      and "quoted-path-versioned=1" in rp.stdout
                      and mline.startswith("  MONEY *_docker (body): 1 key(s)") and "-> OK" in mline
                      and "quoted-tagged" not in mline
                      and "PINNED WITHOUT A TAG: 1 key(s)" in rp.stdout
                      and DOCKER_KEY in rp.stdout, both(rp))
            else:
                check("POSITIVE CONTROL: %s — the same version-in-path shape WITHOUT the digit rule "
                      "satisfied is still a money FINDING, so the accept above cannot be accept-anything"
                      % name,
                      rp.returncode == EXIT_FINDINGS and "UNTAGGED" in rp.stdout
                      and "findings=1" in rp.stdout
                      and "quoted-path-versioned" not in rp.stdout, both(rp))
        check("and no value the money rule graded is echoed: the fixture's bucket path and its image "
              "registry string stay out of the publishable default output",
              "example-bucket" not in r.stdout and "example.org/gatk-sv/gatk" not in r.stdout, r.stdout)

        # Change one output binding: must be a finding.
        moved = fixture_capture(fixture_body(
            outputs={WF + ".final_vcf": "this.final_vcf_new", WF + ".qc_file": "this.qc_file"}))
        r2 = run(["--body", body_a, "--capture", write(dd, "capture-moved.json", moved)])
        check("PLANT one output write-back (this.qc_file_new -> this.qc_file): exit 1, that key named "
              "with both values, and exactly one finding",
              r2.returncode == EXIT_FINDINGS and "SYMBOLIC-DIFFERS outputs: 1 key(s)" in r2.stdout
              and (WF + ".qc_file  body this.qc_file_new != capture this.qc_file") in r2.stdout
              and "findings=1" in r2.stdout, both(r2))
        check("the match verdict word cannot survive in a red run", "BODY-GOLDEN-MATCH" not in r2.stdout,
              r2.stdout)

        # Coverage floor.
        dropped = fixture_capture(fixture_body(
            inputs={k: v for k, v in fixture_body()["inputs"].items() if k != WF + ".batch"}))
        dropped_path = write(dd, "capture-dropped.json", dropped)
        r3 = run(["--body", body_a, "--capture", dropped_path])
        check("one input key missing from the capture is 3 of 4 shared = coverage 0.750, under the 0.900 "
              "floor: refused as VACUOUS at exit 7, never scored as agreement",
              r3.returncode == EXIT_VACUOUS and "VACUOUS DIFF in `inputs`" in r3.stderr
              and "shares 3 of 4 binding key(s)" in r3.stderr and "--min-coverage" in r3.stderr
              and "VERDICT" not in r3.stdout, both(r3))
        r3b = run(["--body", body_a, "--capture", dropped_path, "--min-coverage", "0.7"])
        check("CONTROL for that floor: the SAME two documents at --min-coverage 0.7 are scored, and the "
              "missing key surfaces as a named KEYS-BODY-ONLY finding",
              r3b.returncode == EXIT_FINDINGS and "coverage 0.750" in r3b.stdout
              and "KEYS-BODY-ONLY inputs: 1 key(s)" in r3b.stdout
              and (WF + ".batch") in r3b.stdout and "findings=1" in r3b.stdout, both(r3b))
        r4 = run(["--body", body_a, "--capture", cap_a, "--min-coverage", "1.5"])
        check("--min-coverage outside (0,1] is a usage error naming the flag, before either document is "
              "read", r4.returncode == EXIT_USAGE and "BAD --min-coverage" in r4.stderr, r4.stderr)
        r4b = run(["--capture", cap_a, "--max-list", "0"])
        check("--max-list 0 is a usage error rather than a verdict with the findings deleted",
              r4b.returncode == EXIT_USAGE and "BAD --max-list" in r4b.stderr, r4b.stderr)

        # Each refusal's exit code and label.
        r5 = run(["--body", body_a, "--capture", write(dd, "capture-empty-in.json",
                                                     fixture_capture(fixture_body(inputs={})))])
        check("a capture whose inputs map is EMPTY is refused at exit 4, named as capture.inputs, and "
              "prints no verdict at all",
              r5.returncode == EXIT_EMPTY and "EMPTY-MAP capture.inputs" in r5.stderr
              and "holds 0 binding(s)" in r5.stderr and "VERDICT" not in r5.stdout, both(r5))
        r5b = run(["--body", write(dd, "body-empty-out.json", fixture_body(outputs={})),
                   "--capture", cap_a])
        check("the same refusal on the BODY's outputs map: a side that writes nothing has not agreed "
              "with anything", r5b.returncode == EXIT_EMPTY and "EMPTY-MAP body.outputs" in r5b.stderr,
              r5b.stderr)
        r5c = run(["--body", write(dd, "body-no-inputs.json",
                                   {k: v for k, v in fixture_body().items() if k != "inputs"}),
                   "--capture", cap_a])
        check("and an ABSENT map is the same refusal family with its own wording (`states no `inputs` "
              "map at all`), so absent and empty stay distinguishable",
              r5c.returncode == EXIT_EMPTY and "EMPTY-MAP body.inputs" in r5c.stderr
              and "states no `inputs` map at all" in r5c.stderr, r5c.stderr)

        no_prove = {k: v for k, v in fixture_capture(fixture_body()).items() if k != PROVENANCE_KEY}
        r6 = run(["--body", body_a, "--capture", write(dd, "capture-noprovenance.json", no_prove)])
        check("a capture with no `%s` block is refused at exit 5, naming the block and every key a "
              "re-capture needs" % PROVENANCE_KEY,
              r6.returncode == EXIT_PROVENANCE and "PROVENANCE MISSING" in r6.stderr
              and PROVENANCE_KEY in r6.stderr and all(k in r6.stderr for k in PROVENANCE_REQUIRED),
              both(r6))
        half = fixture_capture(fixture_body())
        half[PROVENANCE_KEY] = dict(half[PROVENANCE_KEY], command="   ")
        r6b = run(["--body", body_a, "--capture", write(dd, "capture-blank-command.json", half)])
        check("a provenance block stating a blank command is INCOMPLETE at exit 5, naming ONLY the key "
              "that is missing",
              r6b.returncode == EXIT_PROVENANCE and "states no: command." in r6b.stderr
              and "no: captured_utc" not in r6b.stderr, both(r6b))

        r7 = run(["--body", body_a, "--capture",
                  write(dd, "capture-root.json", fixture_capture(fixture_body(rootEntityType="sample_set")))])
        check("a capture rooted in another entity type is refused at exit 6 with BOTH values quoted: a "
              "wrong type binds every this.* to nothing, so the key census would score the unscoreable",
              r7.returncode == EXIT_ROOT_ENTITY and "ROOT-ENTITY MISMATCH body='sample' "
              "capture='sample_set'" in r7.stderr, both(r7))
        r7b = run(["--body", write(dd, "body-noroot.json",
                                   {k: v for k, v in fixture_body().items() if k != "rootEntityType"}),
                   "--capture", cap_a])
        check("a side that states no rootEntityType at all is its own refusal (ROOT ENTITY UNSTATED), "
              "not a mismatch blamed on the other side",
              r7b.returncode == EXIT_ROOT_ENTITY and "ROOT ENTITY UNSTATED" in r7b.stderr
              and "the body" in r7b.stderr, both(r7b))

        # Tracked-capture refusal, using a tracked file; exit 3 (not 5) shows it is checked before opening.
        tracked = os.path.join("profiles", "genotyping.json")
        is_tracked = subprocess.run(["git", "-C", ROOT, "ls-files", "--error-unmatch", "--", tracked],
                                    capture_output=True).returncode == 0
        check("the leg's own precondition: %s is tracked here, so the refusal below cannot be a "
              "missing-file accident" % tracked, is_tracked, tracked)
        r8 = run(["--body", body_a, "--capture", tracked])
        check("a capture path this repo TRACKS is refused at exit 3 before being read, and the message "
              "names the command that decided it and where a capture belongs instead",
              r8.returncode == EXIT_TRACKED and "CAPTURE IS TRACKED" in r8.stderr
              and "git ls-files --error-unmatch" in r8.stderr and "recon-live/" in r8.stderr
              and "PROVENANCE" not in r8.stderr, both(r8))

        r9 = run(["--body", body_a, "--capture", os.path.join(dd, "nowhere.json")])
        check("a capture path that does not exist is refused at exit 2 naming the path: ABSENT and EMPTY "
              "stay two different answers, as they are everywhere else in this repo",
              r9.returncode == EXIT_USAGE and "CAPTURE NOT FOUND" in r9.stderr
              and "nowhere.json" in r9.stderr, both(r9))
        r9b = run(["--body", body_a, "--capture", write(dd, "capture-unparseable.json", "{not json")])
        check("a capture that is not JSON is refused at exit 2, not read as a side with no bindings",
              r9b.returncode == EXIT_USAGE and "NOT JSON" in r9b.stderr, both(r9b))
        with open(body_a, encoding="utf-8") as fh:
            body_text = fh.read()
        r9c = run(["--capture", cap_a], stdin="   ")
        check("an empty stdin body is EMPTY BODY at exit 2, not a body of zero bindings that agrees "
              "with everything", r9c.returncode == EXIT_USAGE and "EMPTY BODY" in r9c.stderr, both(r9c))
        r9d = run(["--capture", cap_a], stdin=body_text)
        check("--body defaults to stdin, so the launcher's stdout pipes straight in",
              r9d.returncode == EXIT_OK and "findings=0" in r9d.stdout, both(r9d))

        # useCallCache rule.
        no_cache = fixture_body()
        del no_cache["useCallCache"]
        r10 = run(["--body", write(dd, "body-nocache.json", no_cache), "--capture", cap_a])
        check("the BODY stating no useCallCache is a finding at exit 1 naming the field, while the "
              "capture's silence on it stays a disclosure beside it",
              r10.returncode == EXIT_FINDINGS
              and "MONEY useCallCache: body='<absent>' -> FINDING" in r10.stdout
              and "inherit" in r10.stdout and "findings=1" in r10.stdout, both(r10))
        r10b = run(["--body", write(dd, "body-cache-true.json", fixture_body(useCallCache=True)),
                    "--capture", cap_a])
        check("useCallCache=true in the body is refused just the same, whatever the capture says — the "
              "oracle's own value is never the reason either way",
              r10b.returncode == EXIT_FINDINGS and "MONEY useCallCache: body=True -> FINDING" in r10b.stdout
              and "findings=1" in r10b.stdout, both(r10b))

        # *_docker rule: three wrong shapes. A workspace read is two findings (docker rule + source differs).
        for name, value, needle, want_findings in (
                ("workspace-read", "workspace.gatk_docker", "NOT A PIN", 2),
                ("untagged", '"example.org/gatk-sv/gatk"', "UNTAGGED", 1),
                ("unquoted", "example.org/gatk-sv/gatk:1.2.3", "not a quoted", 1)):
            r11 = run(["--body", write(dd, "body-docker-%s.json" % name,
                                       fixture_body(inputs=dict(fixture_body()["inputs"],
                                                                **{DOCKER_KEY: value}))),
                       "--capture", cap_a])
            check("a body whose gatk_docker is %s is %d finding(s) at exit 1, naming the key and the "
                  "reason" % (name, want_findings),
                  r11.returncode == EXIT_FINDINGS and DOCKER_KEY in r11.stdout and needle in r11.stdout
                  and "findings=%d" % want_findings in r11.stdout, both(r11))
            check("...and no part of the image ref is echoed for being reported: the finding is the key "
                  "plus its form", "example.org/gatk-sv/gatk" not in r11.stdout, r11.stdout[:600])
        cap_untagged = fixture_capture(fixture_body(inputs=dict(fixture_body()["inputs"],
                                                                **{DOCKER_KEY: '"example.org/gatk-sv/gatk"'})))
        r12 = run(["--body", body_a, "--capture", write(dd, "capture-untagged.json", cap_untagged)])
        check("the CAPTURE's untagged image is disclosed and the run still exits 0 — the oracle's gap is "
              "neither a pass nor a failure, which is the half a naive diff gets wrong in both directions",
              r12.returncode == EXIT_OK and "quoted-untagged=1" in r12.stdout
              and "disclosed, never graded" in r12.stdout, both(r12))

        # Coordinate safety: a differing literal is reported without echoing either value.
        cap_lit = fixture_capture(fixture_body(inputs=dict(
            fixture_body()["inputs"], **{WF + ".cutoffs": "gs://not-a-real-bucket-01234567"
                                                         "/ref/all_samples.cutoffs"})))
        cap_lit_path = write(dd, "capture-literal.json", cap_lit)
        r13 = run(["--body", body_a, "--capture", cap_lit_path])
        check("a differing literal lands in a named NON-structural bucket, hashed not echoed, and does "
              "not move the verdict — the bucket cannot make a run green or red",
              r13.returncode == EXIT_OK
              and "LITERAL-VALUES inputs: 1 value(s) byte-identical, 1 value(s) differing" in r13.stdout
              and ("LITERAL-DIFFERS " + WF + ".cutoffs") in r13.stdout
              and "not-a-real-bucket" not in r13.stdout and "findings=0" in r13.stdout, both(r13))
        r13b = run(["--body", body_a, "--capture", cap_lit_path, "--show-values"])
        check("--show-values is the only way to see a literal, which is what makes the default worth "
              "having", r13b.returncode == EXIT_OK and "not-a-real-bucket" in r13b.stdout,
              r13b.stdout[:400])

        # Duplicate keys: reported, attributed to the object that carried them, non-fatal.
        raw = json.dumps(fixture_capture(fixture_body()), indent=1).splitlines(True)
        at = [i for i, line in enumerate(raw) if ('"%s.batch"' % WF) in line]
        check("the duplicate fixture has a real line to duplicate (the binding exists in the text, not "
              "just in the dict)", len(at) == 1, str(len(at)))
        raw.insert(at[0], raw[at[0]])
        r14 = run(["--body", body_a, "--capture", write(dd, "capture-dupkey.json", "".join(raw))])
        check("a key bound twice is named with its scope and its count, the census counts it, and the "
              "exit code is unchanged — last-wins is what a POST binds; the FACT is what a reader is owed",
              r14.returncode == EXIT_OK and "DUPLICATE-KEY in `inputs` %s.batch: bound 2 time(s)" % WF
              in r14.stdout and "duplicate-key census: 1 key(s)" in r14.stdout, both(r14))

        # One graded non-map field, moved: which WDL the launch names.
        stale = fixture_capture(fixture_body())
        stale["methodRepoMethod"] = dict(stale["methodRepoMethod"], methodVersion="someone-elses-branch")
        stale["methodRepoMethod"]["methodUri"] = (
            "dockstore://example.org%2Fgatk-sv%2FTinyPipelineSingle/someone-elses-branch")
        r15 = run(["--body", body_a, "--capture", write(dd, "capture-stalemethod.json", stale)])
        check("a capture pointing at a different Dockstore version is a structural finding on BOTH "
              "members — which WDL runs is not a literal value to be disclosed away",
              r15.returncode == EXIT_FINDINGS and "TOP-LEVEL-DIFFERS: 2 field or member(s)" in r15.stdout
              and "methodRepoMethod.methodVersion: body v1 != capture someone-elses-branch" in r15.stdout
              and "findings=2" in r15.stdout, both(r15))

    print("body_golden_diff selftest: %d ok, %d failed" % (total[0] - len(fails), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
