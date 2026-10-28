"""Re-run one numbered step of the batch chain (06-10) against your branch and images.

Creates one method config with the docker images pinned as literals and everything else as
batch_configs.py specifies it, leaving the original configs untouched.

    python terra/batch_rerun_step.py show                     # read-only (step 10, the default)
    python terra/batch_rerun_step.py --step 08 show           # any step of 06..10
    python terra/batch_rerun_step.py --step 09 create         # POST config
    python terra/batch_rerun_step.py --step 09 validate       # Terra-side WDL check
    python terra/batch_rerun_step.py --step 08 submit         # run it (mutation)
    python terra/batch_rerun_step.py status                   # submissions + Cromwell id

Step data (config name, input prefix, Dockstore path, root entity) comes from `terra/steps.py`.
Participant-rooted modules are not supported.

Image refs are quoted inside the expression because Terra rejects a bare `us.gcr.io/...` literal.
`--image-from <capture.json>` reads refs from a `docker/image_capture.py` capture; per key the
precedence is --image, capture, profile `images`, config layer.

`submit` reads the workspace copy of the config back and refuses unless it matches the body built
here, since Terra runs whatever the workspace holds under that name. Call caching is off unless
requested, and the value in the body is the value sent with the submission.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
sys.path.insert(0, HERE)
import config  # noqa: E402
import module_profile  # noqa: E402
import terra                      # noqa: E402
from terra import fapi      # noqa: E402
import batch_configs as tc          # noqa: E402
import steps as st                        # noqa: E402
import method_upload as mu                # noqa: E402
from batch_configs import body             # noqa: E402
# dstore() duplicates tc.dockstore() with a version override; scripts/probe_fixes.py checks they match.

# Resolved --step/--tag data, cached. Reads sys.argv at call time so in-process callers get the
# default step.
_RESOLVED: dict = {}


def _opt(flag: str) -> str:
    """The value after FLAG on this command line, or '' (a hand-rolled parser, like the one below)."""
    a = sys.argv
    i = a.index(flag) if flag in a else -1
    return a[i + 1] if i >= 0 and len(a) > i + 1 and not a[i + 1].startswith("--") else ""


def _tag() -> str:
    """This command line's run tag, or "" when --tag is absent. An empty --tag is refused."""
    return st.check_tag(_opt("--tag")) if "--tag" in sys.argv else ""


def resolved() -> dict:
    """Per-step data for this invocation: config name, input prefix, method path, root entity, tag."""
    if not _RESOLVED:
        r = st.rerun(_opt("--step") or st.DEFAULT_STEP, _tag())
        _reconcile(r)
        r["entity"] = _row_name(r)      # '' when unknown; entity() refuses
        _RESOLVED.update(r)
    return _RESOLVED


def _reconcile(r: dict) -> None:
    """Refuse when steps.py and batch_configs.CONFIGS disagree about a step, or CONFIGS has no map for it.

    A mismatched root entity would submit a run that binds nothing.
    """
    spec = tc.CONFIGS.get(r["config"]) if isinstance(tc.CONFIGS, dict) else None
    if not spec:
        raise SystemExit(
            f"no input map for step {r['step']} ({r['workflow']}) in batch_configs.CONFIGS, so this "
            f"step\n  has no bindings to rerun. The rerun posts a body built from that map; it does not\n"
            f"  invent one. Steps with a map: {', '.join(sorted(tc.CONFIGS))}.")
    disagree = [("workflow", r["workflow"], spec.get("workflow")),
                ("root entity", r["root_entity"], spec.get("rootEntityType"))]
    both = [(w, mine, theirs) for w, mine, theirs in disagree if theirs and mine != theirs]
    if both:
        raise SystemExit(
            f"step {r['step']} is described two ways, and which one wins would decide what runs:\n"
            + "".join(f"    {w}:  terra/steps.py says {mine!r}, "
                      f"batch_configs.CONFIGS[{r['config']!r}] says {theirs!r}\n"
                      for w, mine, theirs in both)
            + "  The root entity is derived from the step's own `this.<etype>_id` read; "
              "`rootEntityType`\n  is the profile's stated answer, and a disagreement between them is "
              "the drift itself.\n"
            + "  They cannot both be right, and a submission cannot be reviewed from the outside when "
              "it\n  happens. Fix terra/steps.py RERUN_ROOT_ENTITY / STEPS (the rerun map) and "
              "batch_configs\n  CONFIGS (the chain map) to agree, then re-run.")

# Image refs are pinned literally into the config; a workspace attribute reference would let a rerun
# silently use whatever image the attribute currently points at. Pass them with
#     --image gatk_docker=.../gatk:<tag> --image sv_pipeline_docker=.../sv-pipeline:<tag>
# or set GSVTK_IMAGE_REPO / GSVTK_GATK_IMAGE_REPO in the profile.
def parse_images(argv: list[str], prefix: str = "") -> dict:
    """`--image gatk_docker=REF` -> `{<Workflow>.gatk_docker: "REF"}`, for the step being rerun."""
    out = {}
    for spec in argv:
        if "=" not in spec:
            raise SystemExit(f"--image wants key=IMAGE_REF, got {spec!r}")
        key, ref = spec.split("=", 1)
        key = key.split(".")[-1]
        if not key.endswith("_docker"):
            key += "_docker"
        out[f"{prefix}{key}"] = f'"{ref}"'   # literal File values must be quoted
    return out


def _stated(source: str) -> bool:
    """True if the user stated the value (env or profile file) rather than the resolver deriving it.

    `config.resolve()` labels sources `env`, `profile:<path>`, `derived from PROJECT` or `unset`.
    """
    return source == "env" or source.startswith("profile:")


def images_from_config(prefix: str = "") -> dict:
    """Image pins from an explicit GSVTK_IMAGE_REPO / GSVTK_GATK_IMAGE_REPO (env or profile).

    Values derived from the project are ignored. Covers only gatk_docker and sv_pipeline_docker; the
    step's profile `images` map covers the rest.
    """
    res = config.resolve()
    out = {}
    for key, ck in (("gatk_docker", "GATK_IMAGE_REPO"), ("sv_pipeline_docker", "IMAGE_REPO")):
        value, source = res.get(ck, ("", ""))
        # Require a tag or digest. No `versioned_by` is passed, so a path-versioned ref cannot be
        # pinned through this route.
        if value and _stated(source) and module_profile.ref_version_kind(value) in ("tag", "digest"):
            out[f"{prefix}{key}"] = f'"{value}"'
    return out


IMAGES: dict[str, str] = {}
# *_docker keys named with --image on this command line.
CLI_IMAGE_KEYS: set = set()
# Keys the step profile pins, and keys it marks as --image only.
PROFILE_IMAGE_KEYS: set = set()
FLAG_ONLY_IMAGE_KEYS: set = set()
# Keys pinned by an --image-from capture -> the capture file path.
CAPTURE_IMAGE_KEYS: dict = {}
# Keys the profile declares `versioned_by: "path"` (versioned by a path segment, no tag).
PATH_VERSIONED_KEYS: set = set()


def images_from_profile(step: str = "") -> tuple:
    """(stated refs, --image-only keys, path-versioned keys) from this step's profile `images` map.

    Refs are quoted the same way `parse_images` quotes them.
    """
    pins = tc.PROFILE.image_pins.get(step or resolved()["config"], {})
    stated, flag_only, path_versioned = {}, [], []
    for key, pin in pins.items():
        if pin.ref == module_profile.IMAGE_SOURCE_OPERATOR:
            flag_only.append(key)
        else:
            stated[key] = f'"{pin.ref}"'
            if pin.versioned_by:
                path_versioned.append(key)
    return stated, sorted(flag_only), sorted(path_versioned)
CONFIRMED = False        # set only by an explicit --confirm on the command line
ALLOW_UNPINNED = False   # set only by an explicit --allow-unpinned-docker
ALLOW_STALE_DOCKSTORE = False   # set only by an explicit --allow-stale-dockstore
# Last publish-check verdict, reported by submit().
PUBLISH_VERDICT = ""
OVERWRITE = ""           # set only by --overwrite <the exact config name>; never a default
# `--method agora://ns/name/N`: run that method-repository snapshot instead of the Dockstore entry.
METHOD = ""
WDL_VERSION = os.environ.get("GSV_WDL_VERSION") or config.get("BRANCH")
# Point batch_configs' binding check at the same ref this config pins.
tc.VERIFY_REF = WDL_VERSION


def dstore(version: str, workflow: str = "") -> dict:
    """Same construction as batch_configs.dockstore(), with a version override.

    Rawls validates this URI on overwrite, so it must be byte-exact: every slash in the path is
    %-encoded. `workflow` defaults to the default step's workflow.
    """
    path = f"github.com/broadinstitute/gatk-sv/{workflow or st.workflow(st.DEFAULT_STEP)}"
    return {"sourceRepo": "dockstore", "methodPath": path, "methodVersion": version,
            "methodUri": f"dockstore://{path.replace('/', '%2F')}/{version}"}


def agora_from_flag(uri: str) -> None:
    """Set METHOD from `--method`, and grade bindings against the commit the snapshot was built from.

    The commit comes from the local `method_upload.py upload` record. Without one, the binding check
    grades GSV_WDL_VERSION / GSVTK_BRANCH, which `announce` says.
    """
    try:
        mu.parse_agora_uri(uri)
    except ValueError as exc:
        raise SystemExit(f"--method: {exc}")
    globals()["METHOD"] = uri.strip()
    rec = mu.read_record(METHOD)
    if rec.get("sha"):
        tc.VERIFY_REF = rec["sha"]


def _method_line(r: dict) -> str:
    """The announcement line naming the method this config runs."""
    if not METHOD:
        return (f"        Dockstore {r['method_path']} @ {WDL_VERSION or 'GSVTK_BRANCH unset'} "
                f"-- the ref the binding check grades too\n")
    rec = mu.read_record(METHOD)
    if rec.get("sha"):
        if rec.get("workflow") and rec["workflow"] != r["workflow"]:
            raise SystemExit(
                f"--method {METHOD} holds wdl/{rec['workflow']}.wdl (per {mu.record_path(*mu.parse_agora_uri(METHOD))}),\n"
                f"  and step {r['step']} runs {r['workflow']}. A config whose inputs are keyed for one "
                f"workflow\n  binds nothing in another. Upload {r['workflow']} and name that snapshot.")
        return (f"        Agora {METHOD} (method repository, not Dockstore) -- built from "
                f"{rec.get('repo', '?')}\n          @ {rec['sha']} ({rec.get('ref', '?')}); "
                f"the binding check grades that commit\n")
    return (f"        Agora {METHOD} (method repository, not Dockstore) -- no local upload record, so "
            f"its commit is\n          UNKNOWN here; the binding check grades "
            f"{WDL_VERSION or 'GSVTK_BRANCH unset'}, which may not be what the snapshot holds\n")


def _upstream(r: dict) -> tuple:
    """(earlier chain steps this step reads, frozen-baseline files it needs), from batch_configs.

    `terra/batch_check_inputs.py --step NN` checks that the attributes actually exist.
    """
    producer: dict[str, str] = {}
    for name, spec in tc.CONFIGS.items():
        for value in (spec.get("outputs") or {}).values():
            producer[str(value).rsplit(".", 1)[-1]] = name
    chain, frozen = set(), set()
    for value in (tc.CONFIGS[r["config"]].get("inputs") or {}).values():
        value = str(value)
        leaf = value.rsplit(".", 1)[-1]
        if tc.NW and value.endswith(tc.NW):
            chain.add(producer.get(leaf) or f"an earlier run of this chain ({leaf})")
        elif tc.FZ and value.endswith(tc.FZ):
            frozen.add(leaf)
    return sorted(chain), sorted(frozen)


# -----------------------------------------------------------------------------------------------
# --image-from: reader for captures written by docker/image_capture.py.
#
# Re-checks the capture's invariants: refs must be digests, BUILT entries must match the build's tag
# and sha, EXTERNAL entries must state an origin, and every *_docker key the step binds must be
# answered. Never fetches or resolves a digest.
# -----------------------------------------------------------------------------------------------
CAPTURE_KIND = "gsvtk-image-capture"      # the capture document's own `kind`
CAPTURE_VERSION = "1"                     # the only schema version this reader speaks
CAPTURE_STATES = ("BUILT", "EXTERNAL")    # the two states a capture entry may claim
EXIT_CAPTURE_ABSENT = 2                   # nothing at that path
EXIT_CAPTURE_BAD = 3                      # not a usable capture (incl. tracked); same code as body_golden_diff.py


def _capture_refuse(code: int, headline: str, detail: str) -> None:
    """Print a capture refusal to stderr and exit with `code` (2 = absent, 3 = unusable)."""
    body = "".join(("  " + ln if ln.strip() else ln) + "\n"
                   for ln in str(detail).rstrip("\n").splitlines())
    sys.stderr.write(
        f"--image-from: {headline}\n{body}"
        "  A capture is BUILT, by `python docker/image_capture.py --from-serial <build log> --module <m>\n"
        "      --step <NN> --checkout <clone> --sha <sha> --resolve`, which writes one under\n"
        "      `gsvtk-config work image-capture`. This flag only READS one: it never contacts a registry\n"
        "      and never resolves a digest, so a key the capture could not answer stays unanswered here.\n")
    raise SystemExit(code)


def _capture_tracked(path: str) -> None:
    """Refuse a capture file that git tracks, before opening it. If git can't answer, refuse too."""
    try:
        r = subprocess.run(["git", "-C", REPO_ROOT, "ls-files", "--error-unmatch", "--", path],
                           capture_output=True, text=True)
    except OSError as exc:
        _capture_refuse(EXIT_CAPTURE_BAD, "CAPTURE NOT PROVEN UNTRACKED",
                        f"cannot ask git whether {path} is tracked ({exc}), and a capture nobody has\n"
                        "  proved untracked may be one that was committed. Refused without reading it.")
    if r.returncode == 0:
        _capture_refuse(EXIT_CAPTURE_BAD, "CAPTURE IS TRACKED",
                        f"`git ls-files --error-unmatch` says this repo TRACKS {path}, so it was not read.\n"
                        "  A capture carries a real registry path and a real project id: it belongs in an\n"
                        "  ignored path (`work/` is ignored at every depth; `recon-live/` is the precedent\n"
                        "  for a captured artifact), never in the tracked set `make audit` grades. Move it\n"
                        "  out, or build a fresh one, and pass THAT path.\n")


def _capture_tag(ref: str) -> str:
    """The tag segment of a capture ref (between the last `:` and `@sha`), or ''.

    Only used to compare against the capture's stated image_tag, not to decide whether a ref is pinned.
    """
    last = str(ref).rsplit("/", 1)[-1].split("@sha", 1)[0]
    return last.rsplit(":", 1)[-1] if ":" in last else ""


def _capture_ref(key: str, entry, source: dict, path: str) -> str:
    """The quoted ref one capture entry states, after checking the entry's invariants.

    The ref must be a digest on its own; no `versioned_by` exemption applies.
    """
    if not isinstance(entry, dict):
        _capture_refuse(EXIT_CAPTURE_BAD, f"images[{key!r}] is not an object",
                        "a capture entry states ref/state (and target+at_sha, or source_ref+\n"
                        f"  source_ref_origin); this one is a {type(entry).__name__}: {entry!r}")
    ref = str(entry.get("ref") or "").strip()
    if not ref:
        _capture_refuse(EXIT_CAPTURE_BAD, f"images[{key!r}] states no ref",
                        "an entry that names no ref states nothing, which is the silence a capture\n"
                        "  exists to prevent. Put the key in `unresolved` with a reason instead.")
    if module_profile.ref_version_kind(ref) != "digest":
        _capture_refuse(EXIT_CAPTURE_BAD, f"images[{key!r}].ref is not a digest ref",
                        f"  {ref}\n"
                        "  A capture's whole product is an immutable ref, so `@sha256:` is required here\n"
                        "  even though a bare `:tag` would pass the launcher's own guard: a tag can be\n"
                        "  re-pushed, and `docker/image_capture.py --resolve` is what takes the digest.\n"
                        "  (`module_profile.ref_version_kind` is the one predicate; it answers"
        f" {module_profile.ref_version_kind(ref)!r} for this ref.)\n")
    state = str(entry.get("state") or "")
    if state not in CAPTURE_STATES:
        _capture_refuse(EXIT_CAPTURE_BAD, f"images[{key!r}].state is {state!r}",
                        f"the vocabulary is exactly {', '.join(CAPTURE_STATES)}: BUILT means this build\n"
                        "  pushed it, EXTERNAL means the digest was taken of a ref somebody else built.\n")
    if state == "BUILT":
        head = str(source.get("head_sha") or "")
        tag = str(source.get("image_tag") or "")
        missing = [n for n, v in (("target", entry.get("target")), ("at_sha", entry.get("at_sha")),
                                 ("source.head_sha", head), ("source.image_tag", tag)) if not v]
        if missing:
            _capture_refuse(EXIT_CAPTURE_BAD, f"images[{key!r}] claims BUILT and states none of\n"
                                              f"  {', '.join(missing)}",
                            "BUILT is a claim about a build: which target, at which sha, under which tag.\n"
                            "  Without those it is a tag with a story, and the capture cannot say which\n"
                            "  branch's bytes this is.\n")
        if str(entry.get("at_sha")) != head:
            _capture_refuse(EXIT_CAPTURE_BAD, f"images[{key!r}] is BUILT at {entry.get('at_sha')!r}"
                                              f" while the capture's source.head_sha is {head!r}",
                            "One capture, one commit: an entry built somewhere else is EXTERNAL with an\n"
                            "  origin, not BUILT.\n")
        if _capture_tag(ref) != tag:
            _capture_refuse(EXIT_CAPTURE_BAD, f"images[{key!r}] carries the tag {_capture_tag(ref)!r}"
                                              f" while the capture's image_tag is {tag!r}",
                            "A BUILT ref is tagged with the tag THIS build pushed, which is the only\n"
                            "  thing tying the digest to the branch the capture names.\n")
    else:
        missing = [n for n in ("source_ref", "source_ref_origin") if not entry.get(n)]
        if missing:
            _capture_refuse(EXIT_CAPTURE_BAD, f"images[{key!r}] claims EXTERNAL and states none of\n"
                                              f"  {', '.join(missing)}",
                            "An EXTERNAL digest with no stated origin is a digest nobody can account for:\n"
                            "  which ref was it taken of, and read out of where?\n")
    return f'"{ref}"'      # same quoting as parse_images


def read_capture(path: str) -> dict:
    """Load and validate the capture at PATH, or exit (2 = absent, 3 = unusable).

    Keys marked `unresolved` are not an error here; `apply_captures` reports them and the pinned-image
    guard decides.
    """
    if not os.path.exists(path):
        _capture_refuse(EXIT_CAPTURE_ABSENT, f"CAPTURE NOT FOUND: no capture at {path!r}, nothing was read",
                        "This is the `absent` answer, not the `empty capture` one, and it is its own exit\n"
                        "  code so a script can tell them apart. Look in the directory captures land in:\n"
                        "      ls $(gsvtk-config work image-capture)\n"
                        "  or drop --image-from and pin with --image KEY=REF, or with this step's profile\n"
                        "  `images` map (profiles/<module>.json -> steps[].images).\n")
    _capture_tracked(path)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError as exc:
        _capture_refuse(EXIT_CAPTURE_BAD, f"cannot read {path!r}: {exc}",
                        "The file is there and is not readable (a directory, a permission, a file that\n"
                        "  vanished between the check and the open). That is the `unusable` answer and not\n"
                        "  the `absent` one: exit 3 rather than 2, and the cure is the same -- point\n"
                        "  --image-from at a capture `docker/image_capture.py` actually wrote.\n")
    except ValueError as exc:
        _capture_refuse(EXIT_CAPTURE_BAD, f"{path!r} is not one JSON object: {exc}",
                        "A capture is written by `docker/image_capture.py`; a hand-edited one that no\n"
                        "  longer parses is that tool's input, not this one's.\n")
    if not isinstance(doc, dict):
        _capture_refuse(EXIT_CAPTURE_BAD, f"{path!r} is a {type(doc).__name__}, not a capture object",
                        "The capture document is one object with kind/version/images/unresolved.\n")
    if str(doc.get("kind") or "") != CAPTURE_KIND:
        _capture_refuse(EXIT_CAPTURE_BAD,
                        f"{path!r} is not an image capture (kind is {doc.get('kind')!r})",
                        f"this reader speaks kind {CAPTURE_KIND!r} only. A live method-config capture\n"
                        "  (what `checks/body_golden_diff.py` grades) is a different artifact and is not\n"
                        "  a list of image refs.\n")
    if str(doc.get("version") or "") != CAPTURE_VERSION:
        _capture_refuse(EXIT_CAPTURE_BAD,
                        f"{path!r} is capture version {doc.get('version')!r}, and this build reads {CAPTURE_VERSION}",
                        "Refusing rather than reading a schema it has not been taught: a newer capture may\n"
                        "  state refs this launcher would grade with the wrong rule.\n")
    images = doc.get("images")
    if not isinstance(images, dict) or not images:
        _capture_refuse(EXIT_CAPTURE_BAD, "`images` holds 0 entries",
                        "Refusing to score an empty input: a capture that resolved NOTHING is not a\n"
                        "  capture that pins nothing, it is a build whose digests were never taken -- and\n"
                        "  that is a different failure with a different cure than the missing file above.\n")
    unresolved = doc.get("unresolved") or {}
    if not isinstance(unresolved, dict):
        _capture_refuse(EXIT_CAPTURE_BAD, "`unresolved` is not an object",
                        "`unresolved` maps each key the capture cannot answer to a one-line reason.\n")
    no_reason = sorted(k for k, v in unresolved.items() if not str(v or "").strip())
    if no_reason:
        _capture_refuse(EXIT_CAPTURE_BAD,
                        "these keys are `unresolved` with no reason stated: " + ", ".join(no_reason),
                        "Silence about a key is the failure this whole feature exists to prevent, and an\n"
                        "  empty reason is silence in a key's clothes.\n")
    both = sorted(set(images) & set(unresolved))
    if both:
        _capture_refuse(EXIT_CAPTURE_BAD,
                        "these keys appear in `images` AND in `unresolved`: " + ", ".join(both),
                        "One key, one answer. A ref and a reason for the same key means the writer and\n"
                        "  the reader disagree about what happened to it, and the reader would have to pick.\n")
    source = doc.get("source") if isinstance(doc.get("source"), dict) else {}
    return {"path": path,
            "refs": {k: _capture_ref(k, v, source, path) for k, v in sorted(images.items())},
            "unresolved": {k: str(v).strip() for k, v in unresolved.items()},
            "source": source,
            "captured_at": str(doc.get("captured_at") or "")}


def apply_captures(paths, cli: dict) -> dict:
    """Read every --image-from capture, refuse bad ones, and report what each supplies.

    Returns the refs not overridden by --image and sets CAPTURE_IMAGE_KEYS. When several captures state
    a key, the last one wins.
    """
    r = resolved()
    want = sorted(k for k in (tc.CONFIGS[r["config"]].get("inputs") or {}) if k.endswith("_docker"))
    caps = [(p, read_capture(p)) for p in paths]
    refs: dict = {}
    src: dict = {}
    unres: dict = {}
    foreign: dict = {}
    for path, cap in caps:
        mine = {k: v for k, v in cap["refs"].items() if k in want}
        # Last capture to state a key wins.
        refs.update(mine)
        src.update({k: path for k in mine})
        unres[path] = {k: cap["unresolved"][k] for k in want if k in cap["unresolved"]}
        foreign[path] = sorted((set(cap["refs"]) | set(cap["unresolved"])) - set(want))
    silent = [k for k in want if k not in refs and not any(k in v for v in unres.values())]
    if silent:
        _capture_refuse(EXIT_CAPTURE_BAD,
                        f"the capture says NOTHING about {len(silent)} of the {len(want)} *_docker input(s)\n"
                        f"  step {r['step']} ({r['workflow']}) binds",
                        "Every key is in `images` XOR in `unresolved`; a key in neither is a malformed\n"
                        "  capture, and this launch would inherit a workspace attribute for it -- the exact\n"
                        "  hole a capture exists to close. The keys left silent:\n"
                        + "".join(f"      {k}\n" for k in silent)
                        + "  Keys are named `<Workflow>.<key>_docker`. This step's prefix is\n"
                          f"  {r['input_prefix']!r}: a capture built for another workflow's step cannot\n"
                          "  pin this one's inputs, and that is what a mismatch this size usually is.\n")
    won = {k: v for k, v in refs.items() if k not in cli}
    globals()["CAPTURE_IMAGE_KEYS"] = {k: src[k] for k in won}
    # Report on stderr so `show | jq` stays valid JSON.
    for path, cap in caps:
        mine = sorted(k for k in refs if src.get(k) == path)
        restated = [k for k in mine if k in cli]
        bits = [f"{len(mine)} of {len(want)} keys supplied by {path}"]
        tag = str((cap["source"] or {}).get("image_tag") or "")
        if tag:
            bits.append(f"tag {tag}")
        if cap["captured_at"]:
            bits.append(f"captured {cap['captured_at']}")
        if restated:
            bits.append(f"--image restates {len(restated)} of them, and --image wins")
        if foreign[path]:
            bits.append(f"{len(foreign[path])} key(s) it names this step does not bind, unused")
        if not want:
            bits.append(f"step {r['step']} binds no *_docker input, so nothing here is bindable")
        print("[rerun] image capture: " + "   ".join(bits), file=sys.stderr)
        for key in sorted(unres[path]):
            print(f"        unresolved in the capture: {key.split('.')[-1]}"
                  f" -- {unres[path][key]}", file=sys.stderr)
        if unres[path]:
            print("        An unresolved key is NOT a refusal: --image, this step's profile `images`, or\n"
                  "          GSVTK_GATK_IMAGE_REPO/GSVTK_IMAGE_REPO may still state it. If nothing does,\n"
                  "          the pinned-image guard below refuses the launch, capture file present or not.\n"
                  "          This tool will not resolve one for you: --resolve belongs to\n"
                  "          `docker/image_capture.py`, outside a launch.", file=sys.stderr)
    return won


def merge_images(r: dict, cli: dict, capture: dict = None) -> dict:
    """Merge image pins per key and set the provenance key sets.

    Precedence, lowest first: config layer, step profile `images`, capture, --image.
    CAPTURE_IMAGE_KEYS is set by `apply_captures`.
    """
    stated, flag_only, path_versioned = images_from_profile(r["config"])
    merged = dict(images_from_config(r["input_prefix"]))
    merged.update(stated)
    merged.update(capture or {})
    merged.update(cli)
    globals()["PROFILE_IMAGE_KEYS"] = {k for k in stated if k not in cli}
    globals()["FLAG_ONLY_IMAGE_KEYS"] = {k for k in flag_only if k not in cli and k not in merged}
    # Not filtered by --image: the declaration is about the key, not the ref.
    globals()["PATH_VERSIONED_KEYS"] = set(path_versioned)
    return merged


def _pin_source(key: str, from_config: set) -> str:
    """Where this key's ref came from, checked in `merge_images` precedence order."""
    if key in CLI_IMAGE_KEYS:
        return "--image"
    if key in CAPTURE_IMAGE_KEYS:
        return f"capture:{CAPTURE_IMAGE_KEYS[key]}"
    if key in PROFILE_IMAGE_KEYS:
        return "profile/images"
    if key in FLAG_ONLY_IMAGE_KEYS:
        return "UNPINNED (--image only, per the step's profile)"
    if key in from_config:
        return "profile/env"
    if key in IMAGES:
        return "UNKNOWN-SOURCE"      # in the body, from none of the above: say so if reached
    if ALLOW_UNPINNED:
        return "workspace attribute (--allow-unpinned-docker)"
    return "UNPINNED"


def _image_provenance(r: dict) -> str:
    """Per-key provenance of this step's `*_docker` pins, for the announcement line.

    Sources: `--image`, `capture:<file>`, `profile/images`, `profile/env`, or UNPINNED.
    """
    from_config = set(images_from_config(r["input_prefix"]))
    parts = []
    for key in sorted(k for k in (tc.CONFIGS[r["config"]].get("inputs") or {})
                      if k.endswith("_docker")):
        name = key.split(".")[-1]
        src = _pin_source(key, from_config)
        if key in PATH_VERSIONED_KEYS:
            # Show that the missing tag is the declared path-versioned case.
            src += " +PATH-VERSIONED (profile versioned_by)"
        parts.append(f"{name}={src}")
    return "   ".join(parts)


def announce(r: dict, mode: str) -> None:
    """Print the resolved step, config, outputs and upstream inputs to stderr (keeps `show | jq` valid)."""
    tag = r.get("tag") or ""
    print(f"[rerun] step {r['step']}  workflow {r['workflow']}  config {r['rerun_config']}"
          + (f"  (--tag {tag})" if tag else "") + "\n"
          f"        root entity {r['root_entity']}  (row: {_row_line(r)})\n"
          + _method_line(r) +
          f"        image pins: {_image_provenance(r)}", file=sys.stderr)
    outs = list(r["rerun_outputs"].values())
    print(f"        writes {len(outs)} output attribute(s)"
          + (f", tagged '{tag}' (--tag): " if tag else " (no --tag): ")
          + (", ".join(outs) if outs else "nothing -- this step's profile declares no outputs"),
          file=sys.stderr)
    chain, frozen = _upstream(r)
    if chain or frozen:
        print("        reads "
              + (f"chain outputs of: {', '.join(chain)}" if chain else "no chain outputs")
              + (f"  |  frozen baseline files: {len(frozen)} (published by batch_freeze.py)"
                 if frozen else ""), file=sys.stderr)
        print("        prove those attributes exist first: "
              f"python terra/batch_check_inputs.py --step {r['step']}   ({mode} does not read "
              "your workspace)", file=sys.stderr)


def _run_stem(r: dict) -> str:
    """Dump-file stem: the step, plus the tag when one was given."""
    return f"{r['step']}.{r['tag']}" if r.get("tag") else str(r["step"])


def _row_name(r: dict) -> str:
    """The row to submit against: --entity, else the config key named by the profile's `entity_row`.

    Returns '' when neither gives a value; `entity()` refuses that.
    """
    stated = _opt("--entity")
    if stated:
        return stated
    return config.get(r["row_source"]) if r["row_source"] else ""


def _row_line(r: dict) -> str:
    """The row and its source for the announcement line, or why it is unresolved."""
    name, key = r["entity"], r["row_source"]
    if name and name == _opt("--entity"):
        return f"{name}, named by --entity"
    if name:
        return f"{name} from GSVTK_{key}"
    if key:
        return f"UNRESOLVED: GSVTK_{key} is empty, pass --entity <name>"
    return (f"UNRESOLVED: `entity_row` is {module_profile.ROW_SOURCE_OPERATOR}, "
            "so pass --entity <name>")


def entity(r: dict) -> str:
    """The row to submit against, or exit naming what is missing and how to set it."""
    if "--entity" in sys.argv and not _opt("--entity"):
        raise SystemExit(
            "refusing: --entity was given, and it resolved to an empty value.\n"
            "  This is what `--entity \"$ROW\"` looks like when ROW is unset (and what `--entity --confirm`\n"
            "  looks like too). The alternative was to fall through to the workspace's default batch row,\n"
            "  which submits a DIFFERENT cohort than the one asked for, and a submission like that reports\n"
            "  itself as accepted. Drop --entity to take the default deliberately, or name the row.")
    name = _row_name(r)
    if not name:
        key = r["row_source"]
        why = (f"GSVTK_{key}={config.get(key)!r} resolves to an empty value, so the key\n"
               f"      the profile names does not answer either"
               if key else
               f"the profile's `entity_row` for this step is {module_profile.ROW_SOURCE_OPERATOR}: no "
               f"configuration key holds a\n      {r['root_entity']} row, and this command line gave no "
               f"--entity value")
        raise SystemExit(
            f"step {r['step']} ({r['workflow']}) is submitted against a {r['root_entity']} row, and "
            f"nothing\n  names the {r['root_entity']} row to use:\n    {why}.\n"
            f"  pass --entity <name>{f', or set GSVTK_{key}=<name>' if key else ''} "
            f"(the {r['root_entity']} row your workspace holds -- the row the\n"
            f"      {r['step']}-* submissions batch_status.py lists were made against). This tool will "
            f"not guess\n      it: the wrong row binds every this.* input to nothing, and the submission "
            f"still\n      looks accepted.")
    return name


def make_body() -> dict:
    # Resolve the target now; NS/WS are read through `tc` so late values are picked up.
    tc.require_target()
    r = resolved()
    b = body(r["config"])
    b["name"] = r["rerun_config"]
    # Output attribute names come from steps.py (--tag changes them). Inputs are left as-is.
    outs = dict(r["rerun_outputs"])
    if set(outs) != set(b["outputs"]):
        raise SystemExit(
            f"this step's OUTPUT map is answered two ways, so nothing is POSTed:\n"
            f"  terra/steps.py rerun_outputs({r['step']!r}, {r['tag']!r}) answers {len(outs)} "
            f"output(s),\n"
            f"  batch_configs.body({r['config']!r}) answers {len(b['outputs'])}.\n"
            f"  Keys only the first states: {', '.join(sorted(set(outs) - set(b['outputs']))) or '(none)'}\n"
            f"  Keys only the second states: {', '.join(sorted(set(b['outputs']) - set(outs))) or '(none)'}\n"
            "  One rerun writes the second set and the read-back below grades the first, so the two are\n"
            "  not a style question. Both are built from profiles/<module>.json's `outputs`; make them\n"
            "  agree there, or fix whichever reader stopped reading it.")
    b["outputs"] = outs
    # Use the Dockstore entry name, which can differ from the workflow name.
    b["methodRepoMethod"] = (mu.agora_method(METHOD) if METHOD
                             else dstore(WDL_VERSION, st.dockstore_of(r["step"])))
    b["inputs"].update(IMAGES)
    # Every `*_docker` input must be pinned literally. Two refusals:
    #   unpinned             nothing states the ref, so the workspace attribute decides. The message is
    #                        captured in scripts/selftest.d/golden/, so keep it stable.
    #   pinned, unversioned  a ref is stated but has no tag or digest. Fix with a tag or digest, or
    #                        declare `versioned_by: "path"` in the profile for path-versioned images.
    unpinned = sorted(k for k, v in b["inputs"].items()
                      if k.endswith("_docker") and k not in IMAGES)
    # Same predicate the profile loader uses.
    unversioned = [k for k in sorted(IMAGES)
                   if k.endswith("_docker")
                   and module_profile.ref_version_kind(
                       str(IMAGES[k]).strip('"'),
                       "path" if k in PATH_VERSIONED_KEYS else "") == ""]
    if unpinned and not ALLOW_UNPINNED:
        raise SystemExit(
            "image inputs are not fully pinned -- a submit with these runs whatever the workspace\n"
            "  attribute points at today, which is exactly what this tool exists to prevent:\n"
            + "".join(f"    unpinned:  {k}\n" for k in unpinned)
            + "  pass --image KEY=REF for each one (see --help), or --allow-unpinned-docker if you\n"
              "  genuinely want the attribute-resolved images for this rerun."
            + _unversioned_refusal(unversioned, r))
    if unversioned and not ALLOW_UNPINNED:
        # Only reached when nothing is unpinned; the unpinned message takes precedence.
        raise SystemExit(_unversioned_refusal(unversioned, r).lstrip("\n"))
    return b


def _unversioned_refusal(keys: list, r: dict) -> str:
    """Message text for pinned-but-unversioned refs; '' when there are none."""
    if not keys:
        return ""
    return (
        "\n  and these state a ref that names NO VERSION -- implicit `latest`, so the registry answers\n"
        "  with whatever it serves tomorrow, workspace attribute involved or not:\n"
        + "".join(f"    pinned but unversioned:  {k} = {str(IMAGES[k]).strip(chr(34))}\n"
                  for k in keys)
        + "  STATE: PINNED BUT UNTAGGED, and this is the dangerous one of the two -- `latest` under a\n"
          "  name is a floating reference whether or not an attribute is involved. Two cures, both\n"
          "  literal, and neither of them is a flag about workspace attributes:\n"
          "    1. name the version: --image KEY=<repo>:<tag>, or an @sha256: digest.\n"
          "    2. when the version IS a path segment -- a GCE image family such as\n"
          "       marketplace.gcr.io/google/ubuntu1804, which has no tag to add -- declare it once, in\n"
          "       the profile that declares the ref:\n"
          f"         {tc.PROFILE.path} -> steps[step={r['step']!r}].images ->\n"
          f"         \"{keys[0]}\": {{\"ref\": \"<the ref>\", \"versioned_by\": \"path\"}}\n"
          "       The loader refuses that field on a ref whose path names no version token, so it\n"
          "       cannot stand in for a tag nobody looked up (kit/module_profile.py:_validate_images)."
    )


def require_binds_outputs(tag: str, r: dict, b: dict) -> None:
    """Refuse a config with an empty outputs map, before any request.

    Rawls accepts such a config and the run's results are discarded. There is no override flag; fix the
    profile.
    """
    if not tc.binds_no_outputs(b.get("outputs")):
        return
    raise SystemExit(
        f"{tag}: refusing to POST -- step {r['step']} ({r['workflow']}) binds "
        f"{len(b['inputs'])} input(s) and 0 output(s).\n"
        f"  Rawls accepts that: the config validates, the run boots, the workflows SUCCEED, and every\n"
        f"  result is discarded with the scratch bucket -- no entity attribute, no workspace key,\n"
        f"  nothing for the next step to read. `check` calls this NO OUTPUTS BOUND; a launch never\n"
        f"  runs `check`, which is why this refusal exists.\n"
        f"  The field at fault is `outputs` on the step whose `step` is {r['step']!r} in\n"
        f"  {tc.PROFILE.path}. Fix it there -- this tool will not POST a body it has to invent\n"
        f"  the write-back names for, and there is no flag here that buys consent to discard them.")


# Method-config fields that decide what a submission runs, compared on read-back.
# `methodConfigVersion` and `deleted` are server-managed. `useCallCache` and
# `deleteIntermediateOutputFiles` are submission options a method config never echoes back, so
# comparing them would always fail; call caching is enforced by `terra.submit` instead.
DECIDING = ("name", "rootEntityType", "methodRepoMethod", "inputs", "outputs")

# Compared by key in both directions; other dict fields only on the keys the body states.
BY_KEYS = ("inputs", "outputs")

# Cap on differences shown in a refusal.
MAX_DIFF_LINES = 6


def _one_line(value) -> str:
    """A binding on one quoted line, shortened: the refusal has to be readable in a terminal."""
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    return text if len(text) <= 78 else text[:75] + "..."


def _same(field: str, key: str, want, got) -> bool:
    """Equality for one dict key; an Agora snapshot number may come back as a string or an int."""
    if field == "methodRepoMethod" and key == "methodVersion" and isinstance(want, int):
        return str(want) == str(got)
    return want == got


def config_diffs(b: dict, held: dict) -> tuple:
    """(differences, extra server-side fields). No differences means the configs agree."""
    diffs: list = []
    extra: list = []
    for field in DECIDING:
        want = b.get(field)
        if field not in held:
            diffs.append(f"{field}: this body states {_one_line(want)}, the workspace copy answers no "
                         f"such field")
            continue
        got = held.get(field)
        if field in BY_KEYS:
            want, got = want or {}, got or {}
            for key in sorted(set(want) | set(got)):
                if key not in want:
                    diffs.append(f"{field}  + {key}: the copy binds {_one_line(got[key])} and this body "
                                 f"binds nothing there")
                elif key not in got:
                    diffs.append(f"{field}  - {key}: this body binds {_one_line(want[key])} and the copy "
                                 f"does not")
                elif want[key] != got[key]:
                    diffs.append(f"{field}  ~ {key}: body {_one_line(want[key])} != workspace "
                                 f"{_one_line(got[key])}")
        elif isinstance(want, dict) and isinstance(got, dict):
            for key in sorted(want):
                if key not in got:
                    diffs.append(f"{field}.{key}: this body states {_one_line(want[key])}, the copy "
                                 f"answers no such key")
                elif not _same(field, key, want[key], got[key]):
                    diffs.append(f"{field}.{key}: body {_one_line(want[key])} != workspace "
                                 f"{_one_line(got[key])}")
            extra += [f"{field}.{k}={_one_line(got[k])}" for k in sorted(set(got) - set(want))]
        elif want != got:
            diffs.append(f"{field}: body {_one_line(want)} != workspace {_one_line(got)}")
    return diffs, extra


def assert_matches_workspace(r: dict, b: dict) -> None:
    """Refuse to submit unless the workspace's config matches the body built here.

    Terra submits by config name, so it runs whatever the workspace holds. Read-only; any read failure
    or missing field is a refusal.
    """
    where = f"{tc.NS}/{tc.WS} method config {r['rerun_config']}"
    # Any read failure refuses; only a 404 gets its own advice.
    try:
        held = terra.config_payload(tc.NS, tc.WS, tc.NS, r["rerun_config"])
    except Exception as exc:
        why = str(exc).strip() or type(exc).__name__
        raise SystemExit(
            "cannot read the config this submission would run, so nothing is submitted.\n"
            f"  GET {where}: {type(exc).__name__}: {why[:200]}\n"
            + ("  The workspace holds no config by that name. `create --confirm` POSTs the body this\n"
               "      tool built -- read it first with `show`. Nothing was submitted.\n"
               if "404" in why else
               "  Agreement between the body built here and the config the server will run is a\n"
               "      precondition, not a hope: fix the read (credentials, target, network) and re-run.\n"
               "      Nothing was submitted.\n"))
    if not isinstance(held, dict) or not held:
        raise SystemExit(
            "cannot compare the config in the workspace against the body built here, so nothing is\n"
            f"  submitted. {where} answered {type(held).__name__}, not a method config: "
            f"{_one_line(held)}\n"
            "  Refusing to read that as agreement. Nothing was submitted.")
    diffs, extra = config_diffs(b, held)
    if diffs:
        shown = diffs[:MAX_DIFF_LINES]
        raise SystemExit(
            f"the config in the workspace is NOT the body this rerun built ({len(diffs)} "
            f"difference(s),\n"
            f"  {len(shown)} shown), and a submission runs the WORKSPACE copy -- this body is only\n"
            f"  what `show` printed. Reading it back: {where}\n"
            + "".join(f"    {d}\n" for d in shown)
            + (f"    and {len(diffs) - len(shown)} more; `show` prints the body this call built\n"
               if len(diffs) > len(shown) else "")
            + ("  (fields only the server states, not compared: " + ", ".join(extra[:4]) + ")\n"
               if extra else "")
            + "  Nothing was submitted. To make them the same bytes: `create --confirm` POSTs exactly\n"
              "  this body (it OVERWRITES a config other submissions read), or re-run `submit` with\n"
              "  the --step/--image/--call-cache flags that describe what is already there.")
    print(f"rerun submit: read back {r['rerun_config']} from {tc.NS}/{tc.WS} -- the config the "
          f"submission\n        will run agrees with the body built here "
          f"({len(b['inputs'])} inputs, {len(b['outputs'])} outputs"
          + (f"; server-side fields not compared: {', '.join(extra[:4])}" if extra else "") + ")")


def refuse_collision(rd) -> None:
    """Refuse to overwrite an existing config unless --overwrite repeats its name.

    Overwriting bumps methodConfigVersion, which changes what an in-flight submission reads.
    """
    name = rd["rerun_config"]
    if OVERWRITE == name:
        print(f"rerun create: overwriting {name} as instructed (--overwrite names it)")
        return
    try:
        held = terra.config_payload(tc.NS, tc.WS, tc.NS, name)
        detail = ("  the copy there now: methodConfigVersion=%s, %d inputs"
                  % (held.get("methodConfigVersion"), len(held.get("inputs") or {})))
    except Exception as exc:                    # unreadable is a reason to refuse, not to proceed
        detail = f"  could not read the copy there now ({exc}); refusing rather than guessing"
    raise SystemExit(
        f"create: {tc.NS}/{tc.WS} already has a method config named {name}.\n{detail}\n"
        "  Overwriting it bumps methodConfigVersion, and a submission already in flight reads that\n"
        "  name, so this write changes what somebody else's running job means. It is not a re-run.\n"
        f"  If that is what you mean, repeat the name: --overwrite {name}")


def _publish_precheck(rd: dict, mode: str) -> None:
    """Check that the Dockstore pin is the branch tip, before touching Terra.

    Reads GSVTK_PUBLISH_CAPTURE and GSVTK_PUBLISH_TIP via checks/version_resolves.py; never fetches.
      * CURRENT             proceed.
      * STALE / ABSENT      refuse unless --allow-stale-dockstore.
      * not run / declined  print and proceed; --allow-stale-dockstore does not apply.
    """
    if METHOD:
        # Dockstore publication says nothing about an Agora snapshot, so there is nothing to grade.
        globals()["PUBLISH_VERDICT"] = "NOT APPLICABLE"
        print(f"        publish check: NOT APPLICABLE -- the method is {METHOD}, not a Dockstore entry")
        return
    cap = os.environ.get("GSVTK_PUBLISH_CAPTURE", "").strip()
    tipsrc = os.environ.get("GSVTK_PUBLISH_TIP", "").strip()
    missing = [n for n, v in (("GSVTK_PUBLISH_CAPTURE", cap), ("GSVTK_PUBLISH_TIP", tipsrc)) if not v]
    if missing:
        globals()["PUBLISH_VERDICT"] = "NOT RUN"
        print(f"        publish check: NOT RUN ({', '.join(missing)} unset) -- the name resolving is not"
              " evidence the pin is the tip, and this check never fetches the answer for you.\n"
              "          To get the answer now:  ./gsvtk publish-check <branch>\n"
              "        one unauthenticated GET per Dockstore entry plus the branch tip itself; nothing is"
              " POSTed.\n"
              "        To make THIS run answer instead of counting a skip, set GSVTK_PUBLISH_CAPTURE to a"
              " capture and\n"
              "        GSVTK_PUBLISH_TIP to a sha or to `git ls-remote` output. Offline, from files you"
              " already hold:\n"
              "          ./gsvtk publish --all-steps --from-file <capture> --tip-from-file <tip>\n"
              "        and --ls-remote takes no argument -- it reads the checkout GSVTK_GATK_SV_CHECKOUT"
              " names.")
        return
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "checks"))
        import version_resolves as vr
        with open(cap, encoding="utf-8") as fh:
            parsed = vr.parse_capture(json.load(fh))
        branch = str(WDL_VERSION or "")
        tip = tipsrc
        if os.path.exists(tipsrc):        # an `ls-remote` capture is the friendlier spelling
            tips = vr.tips_from_ls_remote(open(tipsrc, encoding="utf-8").read())
            tip = tips.get(branch) or tips.get(f"{branch}_terra") or ""
        rc, verdict = vr.grade_publish(parsed, rd["method_path"], str(WDL_VERSION), tip, branch)
    except (OSError, ValueError, ImportError) as exc:
        globals()["PUBLISH_VERDICT"] = "UNKNOWN"
        print(f"        publish check: UNKNOWN (cannot read the capture or the checker: {exc}) -- this is"
              " NOT a finding that the version is unpublished, and it does not stop the POST")
        return
    head = verdict.split("\n")[0]
    if rc == 0:
        globals()["PUBLISH_VERDICT"] = "CURRENT"
        print(f"        publish check: {head}")
        return
    if rc == vr.REFUSE:
        globals()["PUBLISH_VERDICT"] = "UNKNOWN"
        print(f"        publish check: {head}\n        -- the check declined, so the pin is UNVERIFIED."
              " This is not \"not published\" and --allow-stale-dockstore does not apply: there is no"
              " measurement here to override")
        return
    globals()["PUBLISH_VERDICT"] = "STALE" if verdict.startswith(vr.STALE) else "ABSENT"
    if not ALLOW_STALE_DOCKSTORE:
        raise SystemExit(f"refusing to {mode}: the Dockstore pin is not current ({PUBLISH_VERDICT}).\n"
                         f"  {verdict}\n"
                         "  publish the tip as the version this rerun names (the remedy above is the whole\n"
                         "    procedure), or pass --allow-stale-dockstore if running the older descriptor is\n"
                         "    what you mean -- a rerun of a stale WDL still bills, and its differences from\n"
                         "    the baseline will not be reproducible.")
    print(f"        publish check: {PUBLISH_VERDICT}, proceeding per --allow-stale-dockstore -- {head}")


def create() -> None:
    if not CONFIRMED:
        raise SystemExit("create overwrites a method config that submissions read.\n"
                         "  check `show` printed the images you meant, then re-run with --confirm.")
    rd = resolved()
    announce(rd, "create")
    _publish_precheck(rd, "create")
    # Refuse an unresolved or shared target before the first request.
    tc.require_target(writes=True)
    # Check the input map against the WDL.
    tc.preflight("rerun create")
    b = make_body()
    require_binds_outputs("rerun create", rd, b)
    # Refuse a template that binds one key twice; tc.preflight() checks only keys.
    tc.require_no_dropped_bindings("rerun create", rd["workflow"], b["inputs"])
    resp = fapi.create_workspace_config(tc.NS, tc.WS, b)
    if resp.status_code == 409:
        refuse_collision(rd)                    # returns only when --overwrite repeats the name
        resp = fapi.overwrite_workspace_config(tc.NS, tc.WS, tc.NS, rd["rerun_config"], b)
    print(f"create {rd['rerun_config']}: HTTP {resp.status_code}"
          f"{'' if resp.status_code in (200, 201) else resp.text[:300]}")
    if resp.status_code not in (200, 201):
        raise SystemExit(1)


def validate() -> None:
    # Resolve the workspace before any request.
    rd = resolved()
    announce(rd, "validate")
    tc.require_target()
    tc.preflight("rerun validate")     # free and offline; validate costs a round-trip either way
    resp = fapi.validate_config(tc.NS, tc.WS, tc.NS, rd["rerun_config"])
    d = resp.json() if resp.status_code == 200 else {"error": resp.text[:300]}
    print(json.dumps(d, indent=1))
    # Per-step file name so validates of different steps don't overwrite each other.
    terra.dump(d, str(config.work_dir("metadata") / f"rerun_config_validation.{_run_stem(rd)}.json"))
    valid = d.get("invalid") or []
    if valid:
        print("\nINVALID:")
        for v in valid:
            print("  ", v)
        raise SystemExit(1)


def submit() -> None:
    # Submitting starts real compute; require --confirm.
    if not CONFIRMED:
        raise SystemExit("submit starts real compute and spends real money.\n"
                         "  re-run with --confirm, and check `show` printed the images you meant.")
    rd = resolved()
    announce(rd, "submit")
    _publish_precheck(rd, "submit")
    # Check the row source before anything else.
    row = entity(rd)
    # Resolve first: with an empty target, assert_writable_target would compare empty strings and pass.
    tc.require_target()
    terra.assert_writable_target(tc.NS, tc.WS, "submit a workflow",
                                allow="--allow-shared-target" in sys.argv)
    # Last offline check of the input map against the WDL before VMs boot.
    tc.preflight("rerun submit")
    # Build the body: it runs the image-pin guard and is what the read-back compares against.
    b = make_body()
    require_binds_outputs("rerun submit", rd, b)
    # Offline guards go before assert_matches_workspace, the first request.
    tc.require_no_dropped_bindings("rerun submit", rd["workflow"], b["inputs"])
    # Log any exemptions (stale Dockstore pin, unpinned images) before the POST.
    if PUBLISH_VERDICT in ("STALE", "ABSENT"):
        print(f"rerun submit: --allow-stale-dockstore authorised the Dockstore pin to stay "
              f"{PUBLISH_VERDICT} -- this submission runs the descriptor Dockstore last synced,"
              " not the branch tip (gsvtk publish --help for the remedy)")
    elif PUBLISH_VERDICT == "UNKNOWN":
        print("rerun submit: the Dockstore pin is UNVERIFIED (the publish check declined) -- not stale,"
              " not current, simply not answered; see the line above")
    exempt = sorted(k for k in b["inputs"] if k.endswith("_docker") and k not in IMAGES)
    if exempt:
        print("rerun submit: --allow-unpinned-docker authorised these *_docker inputs to stay\n"
              "        workspace attributes, so this rerun does NOT pin them: "
              + ", ".join(exempt))
    # Terra runs the workspace copy, so compare it to the body.
    assert_matches_workspace(rd, b)
    # Rawls accepts a submission naming a nonexistent entity, so check the row exists. This is the
    # first read; everything decidable offline has already run. useCallCache comes from the body, so
    # the printed value and the submitted value are the same.
    try:
        have_row = terra.entity_exists(tc.NS, tc.WS, rd["root_entity"], row)
    except Exception as exc:
        raise SystemExit(f"rerun submit: cannot check whether the {rd['root_entity']} row {row!r} exists in "
                         f"{tc.NS}/{tc.WS} ({exc}).\n"
                         "  Refusing rather than POSTing a submission whose entity is unknown: 'could not "
                         "check' is not\n  evidence the row is there.")
    if not have_row:
        raise SystemExit(f"rerun submit: {tc.NS}/{tc.WS} has no {rd['root_entity']} row named {row!r}.\n"
                         "  Terra would accept this submission anyway and then have nothing to run, per "
                         "workflow, at\n  compute cost. Name a row that exists (--entity, or the row source "
                         "named by the profile's\n  `entity_row`), or point GSVTK_TERRA_WORKSPACE at the "
                         "workspace that holds it.")
    cache = b["useCallCache"]
    print(f"rerun submit: {rd['rerun_config']} against {row} ({rd['root_entity']}), "
          f"useCallCache={str(cache).lower()} -- "
          + ("calls whose command+inputs match may be served from the cache"
             if cache else "every call re-runs, which is the whole point of a rerun"))
    d = terra.submit(tc.NS, tc.WS, tc.NS, rd["rerun_config"], row, rd["root_entity"], None,
                     confirm=True, use_call_cache=cache)
    print(json.dumps(d, indent=1)[:600])
    terra.dump(d, str(config.work_dir("metadata") / f"rerun_submission.{_run_stem(rd)}.json"))


def status() -> None:
    announce(resolved(), "status")
    tc.require_target()
    subs = terra.submissions(tc.NS, tc.WS, limit=8)["submissions"]
    for s in subs:
        print(" ", s.get("submissionId"), "|", s.get("status"), "|", s.get("submissionDate"),
              "| externalIds:", s.get("externalIds"))


def usage(code=0):
    print(f"""usage: batch_rerun_step.py [--step NN] [--tag TAG] [--image KEY=REF ...] [show|create|validate|submit|status]

Reruns ONE step of the batch chain with the images pinned literally into the config, so
the run cannot inherit a stale image from a workspace attribute. The step, its workflow,
its config name, its input-key prefix and its root entity all come from terra/steps.py.

  --step NN         one of {', '.join(st.known_steps())} (default {st.DEFAULT_STEP}):
                    {', '.join(f'{s}={st.step_names()[i]}' for i, s in enumerate(st.known_steps()))}
                    Every mode takes it. It prints what it resolved to, on stderr, in every mode.
  --tag TAG         name THIS run, so a second rerun of one step does not overwrite the first. It
                    moves exactly two things, both printed by `show`:
                      the config name   NN-<Workflow>-TAG      (untagged: NN-<Workflow>-rerun)
                      the outputs       this.<attr>_TAG        (untagged: this.<attr>_new)
                    INPUTS DO NOT MOVE: the run still reads the chain's existing frozen (`_frz`) and
                    chain (`_new`) attributes, so a tagged run and the run beside it answer the same
                    question from the same input. That is the point -- `submit` POSTs a config NAME, so
                    an untagged second rerun POSTs over the first config AND writes over the first
                    run's output attributes, and two arms cannot be compared afterwards. The tag is
                    letters/digits/underscore (it becomes part of an attribute name), and `rerun` is
                    refused because it spells the name the untagged run already owns.
                    The read-back `submit` does before the POST compares the TAGGED config, and
                    --overwrite must repeat that same tagged name.
  --entity NAME     the row to submit against. Defaults to the config key the step's profile
                    `entity_row` names (GSVTK_BATCH for the chain's sample_set steps). Required --
                    and refused rather than guessed -- for a step whose `entity_row` is `--entity`,
                    which is how a profile says no configuration key holds that row (09 is one:
                    its row is a sample_set_set cohort, and GSVTK_BATCH names a sample_set).
  --image KEY=REF   repeat once per *_docker input, e.g.
                    --image gatk_docker=us.gcr.io/PROJ/NS/gatk:TAG
                    --image sv_pipeline_docker=us.gcr.io/PROJ/NS/sv-pipeline:TAG
                    A step that carries more than the two images the config layer can name
                    (GSVTK_GATK_IMAGE_REPO, GSVTK_IMAGE_REPO) can state its refs once in its own
                    profile -- profiles/<module>.json -> steps[].images -- and then needs no flag at
                    all, or only the one flag overriding one image. Per key the order is --image, then
                    the --image-from capture, then that map, then the config layer, and `show` says which
                    one every pin came from. An entry there may also say `--image`, which states no ref:
                    it is the step
                    answering that only this flag decides that one.
  --image-from FILE take the refs from an image-digest CAPTURE instead of typing them, e.g.
                    --image-from $(gsvtk-config work image-capture)/YOUR_BRANCH-abcdef123456.json
                    A capture is the artifact `python docker/image_capture.py` builds out of a build's
                    own push log plus the digests it resolved: one `@sha256` ref per *_docker key, plus
                    how it knows. Repeatable (the LAST one to state a key wins, per key). Precedence per
                    key is --image, then the capture, then the step's profile `images` map, then the
                    config layer, and the `image pins:` line labels a capture ref `capture:<file>`.
                    A capture ref is graded by the SAME predicate a --image ref is (and by no
                    `versioned_by` declaration: a capture ref has to be a digest on its own bytes), so
                    this door cannot smuggle a floating ref past the pinned-image guard.
                    It NEVER fetches and never resolves a digest -- that is `docker/image_capture.py
                    --resolve`'s job, outside a launch. Three answers, three exit codes:
                      2   no capture at that path (nothing was read)
                      3   the file is not a usable capture: this repo TRACKS it (CAPTURE IS TRACKED),
                          git cannot prove it untracked, it is unreadable / not JSON / not an object,
                          the kind or version is not this reader's, `images` holds 0 entries, a key
                          this step binds is in neither `images` nor `unresolved`, or an entry breaks
                          the capture's own invariants (a ref that is not @sha256, a BUILT entry whose
                          tag/sha is not the build's, an EXTERNAL digest with no stated origin)
                    A key the capture marks `unresolved` is NOT an error here: 5 of a single-sample
                    step's 14 keys are answered by no build at all, so the key is named on stderr with
                    the capture's reason and the pinned-image guard then decides -- it proceeds when
                    --image, the profile or the config layer states that key, and refuses when nothing
                    does. Every refusal above fires at the flag, before any body is built and so before
                    the first request.
  show              print the config body that would be POSTed (no mutation)
  create / validate POST it, then ask Terra to typecheck it
                    (create needs --confirm and refuses the shared baseline workspace unless
                     --allow-shared-target, exactly like batch_configs.py create)
  submit --confirm  start it (real compute; --confirm is mandatory). Before the POST it builds the
                    body, reads this step's rerun config BACK out of the workspace with the read-only
                    config_payload(), and refuses unless the two agree field by field -- a submission
                    runs the config the workspace HOLDS, which may have been POSTed by another branch,
                    an earlier session or another person. Nothing is POSTed to make them agree: that is
                    `create --confirm`'s decision. An unreadable copy, a 404 and a field the copy does
                    not answer all refuse too: "could not check" is not evidence of agreement.
  --method agora://NS/NAME/N  run that method-repository snapshot instead of the step's Dockstore
                            entry (`terra/method_upload.py` uploads a flattened branch and prints the
                            URI). The publish check does not apply to it. When this machine made the
                            upload, its record names the commit, and the binding check grades that
                            commit and refuses a snapshot of a different workflow.
  --allow-stale-dockstore   proceed when the publish check says the Dockstore pin is behind the branch
                            tip, or is not in the capture at all (checks/version_resolves.py, needs
                            GSVTK_PUBLISH_CAPTURE + GSVTK_PUBLISH_TIP). It does NOT silence a check that
                            declined to answer: an unmeasured pin is reported, not authorised.
  --allow-unpinned-docker   let a *_docker input resolve from a workspace attribute (off by
                            default: it breaks reproducibility, so it has to be asked for). submit
                            honours it and names the keys it left workspace-resolved before the POST,
                            so a submission log says which images were not pinned and who said so.
  --allow-duplicate-key KEY  accept ONE key that upstream's template binds more than once, by naming it
                            (repeatable). `create` and `submit` refuse when the template binds a key twice
                            AND the body still carries a `this.`/`workspace.` read for it -- which
                            attribute the run resolves would then be an accident of document order, and
                            Rawls validates such a binding without complaint while it writes back
                            nothing. A duplicate the profile pins to a literal (what `single_sample` does
                            with `mei_bed`) or one where both bindings agree is DISCLOSED and proceeds, so
                            this flag is needed for neither. A name matching no duplicate in that template
                            is a refusal, not a pass, and an unreadable template is a stated NOT RUN.
  --overwrite CONFIG        `create` only, and it must repeat the exact config name. A name already in
                            the workspace makes Terra answer 409; that used to be swallowed into a
                            silent overwrite, which bumps methodConfigVersion on a config a submission
                            in flight is reading. Nothing overwrites unless the name is typed twice.
  --drop-branch-only-inputs  build the config for a ref that does NOT declare this branch's extra
                            inputs (e.g. GSV_WDL_VERSION=main) by dropping them. A SEMANTIC change:
                            it prints what it dropped and which ref it compared.
  --call-cache / --no-call-cache  whether Cromwell may reuse call outputs. Off unless asked for, and
                            this tool now says so instead of inheriting batch_configs' default: a
                            cached call never ran your image. The value `submit` prints is the value the
                            submission POSTs (both read off the built body; `terra.submit` refuses a
                            choice it was not given, because fiss defaults that field to true).
                            (docs/terra-head-to-head.md: a cross-workspace cache copy can
                            time out mid-run.)
  status            recent submissions in the configured workspace (every step's, not just --step's)""",
          file=sys.stderr if code else sys.stdout)
    raise SystemExit(code)


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        usage()
    # Hand-rolled parsing: unknown flags are usage errors, and --image takes a key=value argument.
    specs, argv, confirm, capture_paths = [], [], False, []
    rest = sys.argv[1:]
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--image":
            if i + 1 >= len(rest):
                raise SystemExit("--image wants a KEY=IMAGE_REF argument")
            specs.append(rest[i + 1]); i += 2
        elif a.startswith("--image="):
            specs.append(a.split("=", 1)[1]); i += 1
        elif a == "--image-from":
            # An empty or option-shaped value is refused (likely an unset variable). Repeatable.
            if i + 1 >= len(rest) or not rest[i + 1].strip() or rest[i + 1].startswith("--"):
                raise SystemExit(
                    "--image-from wants the path to a capture file, e.g.\n"
                    "  --image-from $(gsvtk-config work image-capture)/YOUR_BRANCH-abcdef123456.json\n"
                    "  An empty value is what `--image-from \"$CAPTURE\"` looks like with CAPTURE unset, and\n"
                    "  falling back to 'no capture' would silently un-pin every key the file was typed for.\n"
                    "  `python docker/image_capture.py --help` builds one.")
            capture_paths.append(rest[i + 1]); i += 2
        elif a == "--confirm":
            confirm = True; i += 1
        elif a == "--step":
            # Validate against the step map so a typo errors instead of running the default step.
            if i + 1 >= len(rest):
                raise SystemExit(f"--step wants one of {', '.join(st.known_steps())}")
            st.workflow(rest[i + 1])
            i += 2
        elif a == "--tag":
            # Validate here; an empty tag is refused because it would overwrite the default run.
            if i + 1 >= len(rest) or not rest[i + 1].strip() or rest[i + 1].startswith("--"):
                raise SystemExit("--tag wants a run name (letters, digits, underscore: `h2h`, `rc1`).\n"
                                 "  An empty tag resolves to the DEFAULT config name and outputs, so the run\n"
                                 "  overwrites the one already there -- `--tag \"$TAG\"` with TAG unset is a\n"
                                 "  refusal, not a default. Drop --tag to take the default deliberately.")
            st.check_tag(rest[i + 1])
            i += 2
        elif a == "--entity":
            if i + 1 >= len(rest):
                raise SystemExit("--entity wants a row name. The row's TYPE is the step's root "
                                 "entity; `python terra/steps.py`\n  prints it per step (--help does "
                                 "not resolve a profile, so it cannot name it here)")
            i += 2
        elif a == "--overwrite":
            if i + 1 >= len(rest) or not rest[i + 1].strip():
                raise SystemExit("--overwrite wants the exact config name you are about to replace, "
                                 "not a yes.\n  It is printed by `show` and by the refusal itself.")
            globals()["OVERWRITE"] = rest[i + 1]; i += 2
        elif a == "--method":
            if i + 1 >= len(rest) or not rest[i + 1].strip() or rest[i + 1].startswith("--"):
                raise SystemExit("--method wants agora://<namespace>/<name>/<snapshot>, as\n"
                                 "  `python terra/method_upload.py <Workflow> upload --confirm` prints it.\n"
                                 "  An empty value would fall back to the Dockstore entry silently.")
            agora_from_flag(rest[i + 1]); i += 2
        elif a == "--allow-stale-dockstore":
            globals()["ALLOW_STALE_DOCKSTORE"] = True; i += 1
        elif a == "--allow-shared-target":
            # Accepted but not stored: terra.assert_writable_target reads it from sys.argv.
            i += 1
        elif a == "--allow-unpinned-docker":
            globals()["ALLOW_UNPINNED"] = True; i += 1
        elif a == "--allow-duplicate-key":
            # Value consumed but not stored: batch_configs.require_no_dropped_bindings reads it from sys.argv.
            if i + 1 >= len(rest) or not rest[i + 1].strip() or rest[i + 1].startswith("--"):
                raise SystemExit("--allow-duplicate-key wants the exact key it clears, e.g. "
                                 "--allow-duplicate-key GATKSVPipelineSingleSample.mei_bed.\n"
                                 "  A bare or empty value would clear EVERY repeated key, which is the "
                                 "opposite\n  of the decision this flag exists to record.")
            i += 2
        elif a == "--drop-branch-only-inputs":
            # Read from sys.argv by batch_configs.body().
            i += 1
        elif a in ("--call-cache", "--no-call-cache"):
            # Read later by tc.call_cache().
            i += 1
        elif a.startswith("--"):
            raise SystemExit(f"unknown option {a!r}   (--help for the options)")
        else:
            argv.append(a); i += 1

    globals()["IMAGES"] = parse_images(specs, resolved()["input_prefix"])
    globals()["CLI_IMAGE_KEYS"] = set(IMAGES)
    globals()["CONFIRMED"] = confirm
    # Called here only to refuse `--call-cache --no-call-cache` before anything is built.
    tc.call_cache("rerun")
    # Read captures at dispatch, before any body is built or request made.
    cap = apply_captures(capture_paths, IMAGES) if capture_paths else {}
    # Merge pins per key (see merge_images) and name any that did not come from --image.
    globals()["IMAGES"] = merge_images(resolved(), IMAGES, cap)
    taken = {k: v for k, v in globals()["IMAGES"].items() if k not in CLI_IMAGE_KEYS}
    if taken and argv and argv[0] != "show":
        print("[rerun] image pins taken from a file, not from this command line (each names which):")
        from_config = set(images_from_config(resolved()["input_prefix"]))
        for k, v in sorted(taken.items()):
            print(f"          {k.split('.')[-1]} = {v.strip(chr(34))}  ({_pin_source(k, from_config)})")
    if not globals()["IMAGES"] and argv and argv[0] not in ("show", "status"):  # status only lists submissions
        flag_only = sorted(k.split(".")[-1] for k in FLAG_ONLY_IMAGE_KEYS)
        raise SystemExit(
            "nothing states these image refs, so nothing is built.\n"
            + (f"  this step's profile names {len(flag_only)} *_docker input(s) and marks every one of\n"
               f"  them {module_profile.IMAGE_SOURCE_OPERATOR!r} -- it is saying the flag decides, and no flag was given:\n"
               f"      {', '.join(flag_only)}\n"
               if flag_only else
               "  no `images` entry in this step's profile, and no tagged\n"
               "  GSVTK_IMAGE_REPO/GSVTK_GATK_IMAGE_REPO in the config layer.\n")
            + "  A rerun whose images come from workspace attributes cannot prove which code ran.\n"
              "  pass --image KEY=REF for every *_docker input, or state the tagged ref once per step in\n"
              "  profiles/<module>.json -> steps[].images, or name the repo in the config layer,\n"
              "  or point --image-from at a capture (`python docker/image_capture.py` builds one).\n"
              "  (docs/terra-head-to-head.md)")
    what = argv[0] if argv else "show"
    if len(argv) > 1:
        raise SystemExit(f"one mode at a time; got {' '.join(argv)!r}   (--help)")
    if what == "show":
        announce(resolved(), "show")
        print(json.dumps(make_body(), indent=1))
    elif what in ("create", "validate", "submit", "status"):
        {"create": create, "validate": validate, "submit": submit, "status": status}[what]()
    else:
        usage(2)
