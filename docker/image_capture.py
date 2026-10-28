#!/usr/bin/env python3
"""Turn a finished branch build into digest-pinned image refs for the launcher.

`docker/gatk-sv-build.sh` reports only a tag, which is a floating reference. This tool reads the
build's streamed output, works out which `*_docker` inputs the module profile's step needs, resolves
each to a digest, and writes a capture file for `terra/batch_rerun_step.py --image-from`.

It parses these lines from the build output:

    building at <sha>                       (docker/remote-build.sh, the clone's real HEAD)
    Building image: <target>:<tag> ...      (build_docker.py, once per built target)
    docker push <repo>/<name>:<tag>         (build_docker.py, once per pushed image)
    == updated dockers.json entries ==      (remote-build.sh, LAST act before the SUCCESS marker)
    +"key": "<repo>/<name>:<tag>",              (git diff of the pinboard: the keys THIS build refreshed)

The dockers.json diff is the authoritative list of keys this build produced, since build_docker.py
decides which keys to refresh. The capture is built locally from text the driver already holds, and
digests are resolved after the VM is gone.

Keys the branch build does not produce (e.g. images from Docker Hub, or targets build_docker.py
does not build) are recorded as `EXTERNAL` (digest of the ref from the branch's dockers.json) or as
`unresolved` with a reason. Every key is in exactly one of `images` and `unresolved`.

EXIT CODES: 0 wrote a capture. 2 usage, or the capture/serial/checkout is ABSENT. 3 what was handed over
is UNUSABLE (wrong shape, no refresh block, a tracked capture path). 4 the inputs are readable but the
capture would say NOTHING (every key unresolved, or a BUILT key with no digest).
"""

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "kit"))

import module_profile  # noqa: E402  (after the sys.path line above; kit/ is not a package)

KIND = "gsvtk-image-capture"
VERSION = 1
CAPTURE_DIR = "image-capture"

# Patterns for the builder output lines listed in the module docstring.
RE_BUILDING_AT = re.compile(r"building at ([0-9a-f]{7,40})")
# GCE's serial console prefixes each startup-script line with
# `<ts> <instance> google_metadata_script_runner[pid]: startup-script: `. deconsole() strips it so the
# anchored patterns below match.
SERIAL_LABEL = "startup-script: "
RE_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
RE_REFRESH_HDR = re.compile(r"==\s*updated dockers\.json entries\s*==")
RE_REFRESHED = re.compile(r'^\+\s*"([^"]+)"\s*:\s*"([^"]+)"', re.M)
RE_PUSHED = re.compile(r"^docker push (\S+)", re.M)
RE_BUILT_TARGET = re.compile(r"Building image:\s*([\w][\w.-]*):")
# remote-build.sh's SUCCESS marker: "images pushed to <repo> :<tag>" (note the space before the colon).
RE_PUSH_TARGET = re.compile(r"images pushed to (\S+)\s+:(\S+?)(?:\s|\))")
DOCKERFILE_TARGETS = ("manta", "melt", "wham", "str", "stripy", "sv-base-mini", "samtools-cloud-virtual-env",
                      "samtools-cloud", "sv-base-virtual-env", "sv-base", "cnmops-virtual-env", "cnmops",
                      "sv-pipeline-virtual-env", "sv-pipeline", "sv-utils-env", "sv-utils", "sv-shell")

ABSENT, UNUSABLE, EMPTY = 2, 3, 4


def fail(code, headline, *lines):
    """Print an error with detail lines and exit with `code`."""
    print(f"image_capture: {headline}", file=sys.stderr)
    for ln in lines:
        print(f"  {ln}", file=sys.stderr)
    raise SystemExit(code)


def target_of(ref):
    """The image name inside a ref, derived as build_docker.py's `get_target_from_image` does.

    A digest suffix is stripped first.
    """
    text = str(ref).strip().strip('"')
    last = text.split("@", 1)[0].rsplit("/", 1)[-1]
    return last.split(":", 1)[0]


def deconsole(text):
    """Strip ANSI colour and the serial console's line prefix; other lines pass through unchanged."""
    lines = []
    for line in text.splitlines():
        line = RE_ANSI.sub("", line)
        i = line.rfind(SERIAL_LABEL)
        if i != -1:
            line = line[i + len(SERIAL_LABEL):]
        lines.append(line)
    return "\n".join(lines)


def parse_build(text):
    """Parse what the build did from its streamed text. Missing facts are left empty."""
    text = deconsole(text)
    out = {"head_sha": "", "image_tag": "", "docker_repo": "", "refreshed": {}, "pushed": [], "targets": []}
    m = RE_BUILDING_AT.search(text)
    if m:
        out["head_sha"] = m.group(1)
    m = RE_PUSH_TARGET.search(text)
    if m:
        out["docker_repo"], out["image_tag"] = m.group(1), m.group(2)
    for line in text.splitlines():
        # The `+++ b/...` diff header does not match RE_REFRESHED, so it needs no special case.
        if RE_REFRESH_HDR.search(line):
            rest = "\n".join(text.splitlines()[text.splitlines().index(line):])
            for key, ref in RE_REFRESHED.findall(rest):
                out["refreshed"][key] = ref
            break
    out["pushed"] = RE_PUSHED.findall(text)
    seen, targets = set(), []
    for t in RE_BUILT_TARGET.findall(text):
        if t not in seen:
            seen.add(t)
            targets.append(t)
    out["targets"] = targets
    return out


def needed_keys(module, step):
    """The `*_docker` keys this step must have pinned, read from the module profile.

    Returns (config name, keys, {key: ref the profile states}, {keys declared versioned by path}).
    """
    loaded = module_profile.load(module)
    cfg = next((c for c in loaded.configs if c.startswith(f"{step}-") or c == step), None)
    if cfg is None:
        fail(ABSENT, f"module profile {module!r} has no step {step!r}",
             f"  steps this profile knows: {', '.join(loaded.configs) or '(none)'}")
    pins = loaded.image_pins.get(cfg, {})
    keys = sorted(k for k in pins if k.endswith("_docker"))
    stated = {k: pins[k].ref for k in keys}
    # Only the profile may declare a key versioned by its path; a capture's own `versioned_by` is never trusted.
    declared = {k for k in keys if pins[k].versioned_by == module_profile.VERSIONED_BY_PATH}
    return cfg, keys, stated, declared


def dockers_at(checkout, sha):
    """gatk-sv's dockers.json at a commit, read with `git show` (the clone is not modified)."""
    try:
        proc = subprocess.run(["git", "-C", str(checkout), "show", f"{sha}:inputs/values/dockers.json"],
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        fail(ABSENT, f"CHECKOUT OBJECT MISSING: cannot read {sha}:inputs/values/dockers.json",
             f"  checkout: {checkout}   error: {type(exc).__name__}",
             "  this tool never fetches; the commit must already be in the clone it is pointed at")
    if proc.returncode != 0:
        fail(ABSENT, f"CHECKOUT OBJECT MISSING: {sha}:inputs/values/dockers.json",
             f"  checkout: {checkout}",
             f"  git: {proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else 'no such object'}")
    try:
        return json.loads(proc.stdout)
    except ValueError:
        fail(UNUSABLE, f"CHECKOUT OBJECT UNUSABLE: {sha}:inputs/values/dockers.json is not JSON")
    return {}


def _digest_from_json(raw):
    """The digest in a `gcloud ... --format json` answer, or "".

    Handles both `list-tags` (list with bare hex `digest`) and `images describe` (digest under
    `image`). Anything other than a full 64-hex digest is rejected.
    """
    if not raw or not raw.strip():
        return ""
    try:
        doc = json.loads(raw)
    except ValueError:
        return ""
    cand = []
    if isinstance(doc, list) and doc:
        cand.append(doc[0].get("digest") if isinstance(doc[0], dict) else None)
    if isinstance(doc, dict):
        cand.append(doc.get("digest"))
        img = doc.get("image")
        if isinstance(img, dict):
            cand.append(img.get("digest"))
    for c in cand:
        c = str(c or "").strip()
        if re.fullmatch(r"[0-9a-f]{64}", c):
            return f"sha256:{c}"
        if re.fullmatch(r"sha256:[0-9a-f]{64}", c):
            return c
    return ""


def digests_for(refs, seam, timeout=120, errs=None):
    """Return ({ref: digest}, source description, {ref: error}).

    Reads GSVTK_IMAGE_DIGESTS_JSON when set (for offline tests), otherwise queries the registry. Refs
    with no digest are left out of the map.
    """
    if seam:
        p = pathlib.Path(seam)
        if not p.is_file():
            fail(ABSENT, f"GSVTK_IMAGE_DIGESTS_JSON names {seam!r}, which is not a file",
                 "  unset it to resolve digests live, or point it at a capture -- it is never fetched")
        try:
            raw = json.loads(p.read_text())
        except ValueError:
            fail(UNUSABLE, f"GSVTK_IMAGE_DIGESTS_JSON {seam!r} is not JSON")
            return {}, ""
        if not isinstance(raw, dict) or not raw:
            fail(UNUSABLE, f"GSVTK_IMAGE_DIGESTS_JSON {seam!r} is empty or not an object",
                 "  an empty digest map would make this run's capture say nothing about every image")
        return ({str(k): str(v) for k, v in raw.items()},
                f"captured digest map {p} -- NOT a live registry read", {})
    out, errs = {}, (errs if errs is not None else {})
    for ref in refs:
        # Try `list-tags` first: `images describe` rejects a tag ref on some gcloud versions.
        host_and_repo, sep, tag = ref.rpartition(":")
        tag = tag if (sep and "/" in host_and_repo) else ""
        # Use json: gcloud's value() format can silently truncate the digest.
        cmds = []
        if tag:
            cmds.append(["gcloud", "container", "images", "list-tags", host_and_repo,
                         "--filter", f"tags={tag}", "--format", "json"])
        cmds.append(["gcloud", "container", "images", "describe", ref, "--format", "json"])
        val, err = "", ""
        for cmd in cmds:
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            except (OSError, subprocess.SubprocessError) as exc:
                val, err = "", f"{type(exc).__name__} after {timeout}s"
                continue
            val = _digest_from_json(proc.stdout)
            err = (proc.stderr.strip().splitlines() or [""])[-1][:200]
            if val.startswith("sha256:"):
                break
        if val.startswith("sha256:"):
            out[ref] = val
        elif err:
            errs[ref] = err
    return out, "gcloud container images describe / list-tags (live registry GET)", errs


def is_tracked(path):
    """True if writing here would put a real registry path into the publishable set."""
    p = pathlib.Path(path).resolve()
    d = p if p.is_dir() else p.parent
    if not str(d):
        return False
    try:
        proc = subprocess.run(["git", "-C", str(d), "ls-files", "--error-unmatch", "--", p.name],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def build_capture(source, keys, stated, refreshed, board, resolved, tag, head_sha, origin, declared=frozenset()):
    """Assemble the capture. Every key ends up in `images` or `unresolved`.

    Each key's ref comes from, in order: `refreshed` (this build pushed it), `board` (dockers.json at
    the built commit), then `stated` (the module profile). `declared` is the set of keys the profile
    declares versioned by path.
    """
    # dockers.json keys are bare (`sv_pipeline_docker`); profile keys carry the workflow prefix. Join on the leaf.
    leaf = {k: k.split(".")[-1] for k in keys}
    by_leaf = {k.split(".")[-1]: v for k, v in refreshed.items()}
    board_by_leaf = {k.split(".")[-1]: v for k, v in board.items()}
    images, unresolved = {}, {}
    for key in keys:
        ref, built = None, False
        if leaf[key] in by_leaf:
            ref, built = by_leaf[leaf[key]], True
        elif leaf[key] in board_by_leaf:
            ref = board_by_leaf[leaf[key]]
        else:
            s = stated.get(key)
            if s not in (None, "", module_profile.IMAGE_SOURCE_OPERATOR):
                ref = s
        if ref is None:
            unresolved[key] = "nothing states this key: not refreshed by this build, absent from the " \
                              "branch's dockers.json, and the profile leaves it to " \
                              f"{module_profile.IMAGE_SOURCE_OPERATOR}"
            continue
        digest = resolved.get(ref)
        if not digest:
            # A path-versioned key is pinned by the profile, not the capture, so it stays unresolved here.
            if not built and key in declared:
                graded = module_profile.ref_version_kind(ref, "path")
                unresolved[key] = (
                    f"{ref} has no digest to take, and the module profile declares this key versioned by its "
                    f"path ({graded!r}) -- the ref that pins it is in profiles/<module>.json, not in a capture"
                    if graded == "path" else
                    f"no digest obtained for {ref}, and the profile's `versioned_by: path` declaration was "
                    f"refused by the digit test in module_profile.ref_version_kind, which pins nothing")
                continue
            unresolved[key] = f"no digest obtained for {ref}"
            continue
        full = f"{ref}@{digest}"
        kind = module_profile.ref_version_kind(full)
        if kind != "digest":
            # The launcher applies the same check, so refuse it here with a clearer reason.
            unresolved[key] = f"MALFORMED REF: {full!r} answers {kind!r}, not 'digest'"
            continue
        entry = {"ref": full, "state": "BUILT" if built else "EXTERNAL"}
        if built:
            entry["target"] = target_of(ref)
            entry["at_sha"] = head_sha
        else:
            entry["source_ref"] = ref
            entry["source_ref_origin"] = origin
        images[key] = entry
    capture = {"kind": KIND, "version": VERSION,
               "captured_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "source": source, "built_targets": [t for t in source.get("targets", []) if t],
               "images": images, "unresolved": unresolved}
    capture["source"]["image_tag"] = tag
    capture["source"]["head_sha"] = head_sha
    return capture


def check_invariants(capture):
    """Apply the same invariants the reader checks."""
    for key, e in capture["images"].items():
        # Only the profile may state versioned_by.
        if e.get("versioned_by"):
            fail(UNUSABLE, f"{key} states versioned_by={str(e['versioned_by'])!r}: a capture entry is a "
                           f"digest or it is `unresolved`")
        if module_profile.ref_version_kind(str(e.get("ref", ""))) != "digest":
            fail(UNUSABLE, f"MALFORMED REF written for {key}: {e.get('ref')!r} names no digest")
        if e.get("state") == "BUILT" and e.get("at_sha") != capture["source"].get("head_sha"):
            fail(UNUSABLE, f"BUILT key {key} names a commit its capture does not: {e.get('at_sha')!r} "
                           f"vs source.head_sha {capture['source'].get('head_sha')!r}")
        if e.get("state") == "EXTERNAL" and not e.get("source_ref_origin"):
            fail(UNUSABLE, f"EXTERNAL key {key} carries a digest with no stated origin")
    both = set(capture["images"]) & set(capture["unresolved"])
    if both:
        fail(UNUSABLE, f"a key is both resolved and unresolved: {', '.join(sorted(both))}")


def selftest():
    """Built-in assertions against hand-written build text."""
    ok, fails = 0, []

    def check(desc, cond):
        nonlocal ok
        if cond:
            ok += 1
        else:
            fails.append(desc)

    serial = "\n".join([
        "building at 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
        "Building image: sv-pipeline:probe-branch-1a2b3c ...",
        "docker push us.gcr.io/<project>/<ns>/gatk-sv/sv-pipeline:probe-branch-1a2b3c",
        "== updated dockers.json entries ==",
        "+++ b/inputs/values/dockers.json",
        '+    "sv_pipeline_docker": "us.gcr.io/<project>/<ns>/gatk-sv/sv-pipeline:probe-branch-1a2b3c",',
        '+    "sv_pipeline_qc_docker": "us.gcr.io/<project>/<ns>/gatk-sv/sv-pipeline:probe-branch-1a2b3c",',
        "### GATK_SV_BUILD_RESULT=SUCCESS (images pushed to "
        "us.gcr.io/<project>/<ns>/gatk-sv :probe-branch-1a2b3c) ###",
    ])
    got = parse_build(serial)
    check("the builder's own HEAD is read, not the sha the operator typed",
          got["head_sha"].startswith("1a2b3c4d"))
    check("push repo and tag come from the builder's close line",
          got["docker_repo"].endswith("/gatk-sv") and got["image_tag"] == "probe-branch-1a2b3c")
    check("the refresh block is the source of truth for which keys this build produced",
          set(got["refreshed"]) == {"sv_pipeline_docker", "sv_pipeline_qc_docker"})
    check("the diff header line is not mistaken for a refreshed key", "b/inputs" not in str(got["refreshed"]))
    check("built targets are named once each", got["targets"] == ["sv-pipeline"])
    check("the target of a refreshed ref is derived the way build_docker.py derives it",
          target_of("us.gcr.io/x/gatk-sv/sv-pipeline:t@sha256:" + "a" * 64) == "sv-pipeline")
    check("a hyphen/underscore difference is not confusable (sv_base_docker <-> sv-base)",
          target_of(".../sv-base:2026-09-09-v1.1.1-3a13069d") == "sv-base")
    check("a build that never reached the pinboard yields no refresh keys",
          parse_build("building at 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b")["refreshed"] == {})

    src = {"branch": "probe-branch", "base_sha": "", "docker_repo": "us.gcr.io/<project>/<ns>/gatk-sv",
           "log_source": "selftest fixture", "targets": ["sv-pipeline"]}
    keys = ["Wf.sv_pipeline_docker", "Wf.wham_docker", "Wf.cloud_sdk_docker"]
    stated = {k: "--image" for k in keys}
    ref = "us.gcr.io/<project>/<ns>/gatk-sv/sv-pipeline:probe-branch-1a2b3c"
    resolved = {ref: "sha256:" + "a" * 64,
                "us.gcr.io/<project>/<ns>/gatk-sv/wham:2024-10-25-v1.1.1-5ea22a52": "sha256:" + "b" * 64}
    cap = build_capture(dict(src), keys, stated, {"sv_pipeline_docker": ref},
                        {"wham_docker": "us.gcr.io/<project>/<ns>/gatk-sv/wham:2024-10-25-v1.1.1-5ea22a52"},
                        resolved, "probe-branch-1a2b3c",
                        "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b",
                        "inputs/values/dockers.json @ 1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b")
    check("a refreshed key lands as BUILT with a digest and its own commit",
          cap["images"]["Wf.sv_pipeline_docker"]["state"] == "BUILT"
          and cap["images"]["Wf.sv_pipeline_docker"]["ref"].endswith("@" + "sha256:" + "a" * 64)
          and cap["images"]["Wf.sv_pipeline_docker"]["target"] == "sv-pipeline")
    check("a key nobody states, and the pinboard does not carry either, is unresolved with a reason naming "
          "the flag that would supply it",
          "--image" in cap["unresolved"]["Wf.cloud_sdk_docker"])
    check("a key this build did NOT refresh still gets a digest, from the branch's own pinboard, and is "
          "labelled EXTERNAL rather than passed off as a build of the commit",
          cap["images"]["Wf.wham_docker"]["state"] == "EXTERNAL"
          and cap["images"]["Wf.wham_docker"]["ref"].endswith("@sha256:" + "b" * 64)
          and cap["images"]["Wf.wham_docker"]["source_ref"].endswith(":2024-10-25-v1.1.1-5ea22a52")
          and "at_sha" not in cap["images"]["Wf.wham_docker"])
    check("an EXTERNAL entry names where its source ref came from, so a digest is never unattributed",
          "dockers.json" in cap["images"]["Wf.wham_docker"]["source_ref_origin"])
    check("every key is in images XOR unresolved -- silence about one key is the defect this file exists for",
          set(cap["images"]) | set(cap["unresolved"]) == set(keys)
          and not (set(cap["images"]) & set(cap["unresolved"])))
    check("the writer checks the reader's invariants too", check_invariants(cap) is None)

    bad = json.loads(json.dumps(cap))
    bad["images"]["Wf.sv_pipeline_docker"]["ref"] = ref          # a tag, no digest
    try:
        check_invariants(bad)
        check("a tag-only ref must be refused, not captured", False)
    except SystemExit as e:
        check("a tag-only ref must be refused, not captured", "MALFORMED REF" in str(e.code) or e.code == UNUSABLE)
    bad2 = json.loads(json.dumps(cap))
    bad2["unresolved"]["Wf.sv_pipeline_docker"] = "also here"
    try:
        check_invariants(bad2)
        check("one key answered two ways is refused", False)
    except SystemExit as e:
        check("one key answered two ways is refused", e.code == UNUSABLE)

    # Path-versioned keys.
    dec = {"Wf.sv_pipeline_docker"}
    pathboard = {"sv_pipeline_docker": "marketplace.gcr.io/google/ubuntu1804"}
    pcap = build_capture(json.loads(json.dumps(cap))["source"], ["Wf.sv_pipeline_docker"], {}, {},
                         pathboard, {}, "t", "deadbeef", "board", dec)
    check("a profile-declared path key is left to the profile rather than claimed by the capture",
          "Wf.sv_pipeline_docker" not in pcap["images"]
          and "profiles/<module>.json" in pcap["unresolved"]["Wf.sv_pipeline_docker"]
          and "versioned by its path" in pcap["unresolved"]["Wf.sv_pipeline_docker"])
    nod = build_capture(json.loads(json.dumps(cap))["source"], ["Wf.sv_pipeline_docker"], {}, {},
                        pathboard, {}, "t", "deadbeef", "board")
    check("the same ref without a profile declaration is a plain unresolved no-digest, no ceremony",
          nod["unresolved"]["Wf.sv_pipeline_docker"] == "no digest obtained for "
          "marketplace.gcr.io/google/ubuntu1804")
    laundered = json.loads(json.dumps(cap))
    laundered["images"]["Wf.wham_docker"]["versioned_by"] = "path"
    try:
        check_invariants(laundered)
        check("a capture entry that states versioned_by is refused outright, not graded", False)
    except SystemExit as e:
        check("a capture entry that states versioned_by is refused outright, not graded", e.code == UNUSABLE)

    # Serial console prefixes and gcloud json digest shapes.
    console = "\n".join([
        '[ 2881.344677] google_metadata_script_runner[1449]: startup-script: == updated dockers.json entries ==',
        '[ 2881.344700] google_metadata_script_runner[1449]: startup-script: +  "sv_pipeline_docker": "x/y:t",',
        '2026-10-22T16:53:43.4Z gsv-<instance> google_metadata_script_runner[1449]: startup-script: docker push x/y:t',
        'startup-script: ### GATK_SV_BUILD_RESULT=SUCCESS (images pushed to x/y :t) ###'])
    cparsed = parse_build(console)
    check("the serial console's per-line label is stripped, so the pinned anchors still anchor",
          cparsed["refreshed"].get("sv_pipeline_docker") == "x/y:t" and cparsed["pushed"] == ["x/y:t"]
          and cparsed["docker_repo"] == "x/y" and cparsed["image_tag"] == "t")
    check("a clean log (no console label) parses through the same function, unchanged",
          parse_build('== updated dockers.json entries ==\n+  "sv_base_docker": "a/b:c",\n')["refreshed"] == {"sv_base_docker": "a/b:c"})
    check("list-tags json (bare hex, no prefix) is normalised to digest form",
          _digest_from_json('[{"digest":"%s","tags":["t"]}]' % ("a" * 64)) == "sha256:" + "a" * 64)
    check("describe json (nested under image) is read too",
          _digest_from_json('{"image":{"digest":"sha256:%s"}}' % ("b" * 64)) == "sha256:" + "b" * 64)
    check("the 12-hex value gcloud's VALUE printer truncates to is refused, not shortened-into a digest",
          _digest_from_json('[{"digest":"9c4b764e01be"}]') == ""
          and _digest_from_json('[{"digest":"%s"}]' % ("c" * 63)) == "")

    nosrc = parse_build("nothing the builder printed here at all")
    check("a serial with no refresh block is empty, so the caller must refuse rather than score it",
          nosrc["refreshed"] == {} and nosrc["head_sha"] == "")
    seam_dir = pathlib.Path(tempfile.mkdtemp(prefix="gsvtk-cap-selftest-"))
    tracked_here = pathlib.Path(__file__).resolve().parent.parent / "README.md"
    check("a capture path inside the tracked set is refused before anything is written",
          tracked_here.is_file() and is_tracked(str(tracked_here)) is True)
    check("the refusal fires on the tracked file's own name, not on a same-named file elsewhere",
          is_tracked(str(seam_dir / "README.md")) is False)
    check("a scratch path is not tracked, so the real capture can live there",
          is_tracked(str(seam_dir / "probe-1a2b3c.json")) is False)
    print(f"image_capture: {ok} ok, {len(fails)} failed")
    for f in fails:
        print(f"  FAILED: {f}")
    return 1 if fails else 0


def why_with_registry(why, digest_errs):
    """Append the registry's error text to a printed reason (it is not stored in the capture file)."""
    for ref, err in (digest_errs or {}).items():
        if ref and ref in why and "[" not in why:
            return f"{why}   [{err}]"
    return why


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="docker/image_capture.py",
        description="Turn a finished branch build into the immutable image refs it entitles you to run.",
        epilog="Writes under $GSVTK_WORK/image-capture/ by default. Prints a digest source line on every "
               "run, because a capture built from a captured map is not an answer about the registry.")
    ap.add_argument("--from-serial", metavar="FILE", help="the streamed build text (the driver saves it)")
    ap.add_argument("--module", default="single_sample", help="module profile holding the step (default: single_sample)")
    ap.add_argument("--step", default="01", help="step whose *_docker keys must be pinned (default: 01)")
    ap.add_argument("--checkout", help="gatk-sv clone, for dockers.json at the built commit")
    ap.add_argument("--resolve", action="store_true",
                    help="take digests (a GET per image). Without it every key lands in 'unresolved'.")
    ap.add_argument("--out", help="capture path (default: $GSVTK_WORK/image-capture/<branch>-<sha6>.json)")
    ap.add_argument("--dry-run", action="store_true", help="print the capture, write nothing")
    ap.add_argument("--at-sha", default="", metavar="SHA",
                    help="the commit the build was of: `docker/gatk-sv-build.sh` knows it (it resolved the "
                         "branch before booting anything) and the serial text does NOT state it, so the "
                         "driver passes it here rather than this tool guessing from a tag suffix")
    ap.add_argument("--selftest", action="store_true", help="run the built-in assertions and exit")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.from_serial:
        fail(ABSENT, "no build text: pass --from-serial (docker/gatk-sv-build.sh saves it)",
             "  this tool never fetches and never re-runs a build; it reads what the build printed")
    serial_path = pathlib.Path(a.from_serial)
    if not serial_path.is_file():
        fail(ABSENT, f"--from-serial {a.from_serial!r} is not a file")
    parsed = parse_build(serial_path.read_text(errors="replace"))
    if not parsed["refreshed"]:
        fail(UNUSABLE, "NO REFRESH BLOCK: the build text never reached "
                       "'== updated dockers.json entries ==', so nothing is known about what it published",
             "  a build that died before the pinboard diff pushed nothing this capture could honestly name",
             f"  text read: {serial_path}")
    if a.at_sha:
        if not re.fullmatch(r"[0-9a-f]{7,40}", a.at_sha):
            fail(ABSENT, f"--at-sha {a.at_sha!r} is not a hex commit", "  pass the sha the driver resolved, not a branch name")
        parsed["head_sha"] = a.at_sha
    if parsed["refreshed"] and not parsed["head_sha"]:
        fail(ABSENT, "NO COMMIT NAMED: the build refreshed the pinboard, but nothing here says which commit "
                     "it built",
             "  a BUILT entry is a claim about a commit, so stating the ref without one would say \"these\n"
             "    bytes came from somewhere\" -- which is the tag-with-a-story this file refuses elsewhere\n"
             "  pass --at-sha <sha> (the driver has it: it resolved the branch before creating the VM),\n"
             "    or read a text that carries a 'building at <sha>' line")
    if not parsed["docker_repo"] or not parsed["image_tag"]:
        fail(UNUSABLE, "NO PUSH TARGET: the text has no 'images pushed to <repo> :<tag>' close line",
             "  without a push target every ref here would be a name with nowhere to resolve")
    cfg, keys, stated, declared = needed_keys(a.module, a.step)
    if not keys:
        fail(UNUSABLE, f"CAPTURE WOULD SCORE NOTHING: step {cfg!r} binds no *_docker inputs at all",
             "  a step with no image inputs needs no capture; refusing keeps 'empty' from reading as 'clean'")
    origin = f"inputs/values/dockers.json @ {parsed['head_sha']}"
    board = {}
    if a.checkout:
        if not pathlib.Path(a.checkout, ".git").exists() and not pathlib.Path(a.checkout).is_dir():
            fail(ABSENT, f"--checkout {a.checkout!r} is not a directory", "  it is read with `git show` only; nothing is fetched")
        board = dockers_at(a.checkout, parsed["head_sha"])
        if not board:
            fail(ABSENT, f"CHECKOUT OBJECT MISSING: {parsed['head_sha']}:inputs/values/dockers.json is empty")
    resolved, src_line, digest_errs = ({}, "digest resolution not requested (--resolve), so nothing is claimed", {})
    if a.resolve:
        seam = os.environ.get("GSVTK_IMAGE_DIGESTS_JSON", "")
        want = sorted(set(list(parsed["refreshed"].values()) + list(board.values())
                          + [v for v in stated.values() if v and v != module_profile.IMAGE_SOURCE_OPERATOR]))
        resolved, src_line, digest_errs = digests_for(want, seam)
        # Always say whether digests came from the registry or from a file.
        print(f"image_capture: digest source: {src_line}")
    source = {"branch": parsed["image_tag"].rsplit("-", 1)[0], "base_sha": "",
              "docker_repo": parsed["docker_repo"], "log_source": f"file:{serial_path.name}",
              "targets": parsed["targets"]}
    cap = build_capture(source, keys, stated, parsed["refreshed"], board, resolved,
                        parsed["image_tag"], parsed["head_sha"], origin, declared)
    check_invariants(cap)
    if not cap["images"]:
        for k in sorted(cap["unresolved"])[:6]:
            print(f"image_capture:   unresolved {k}: {why_with_registry(cap['unresolved'][k], digest_errs)}")
        if len(cap["unresolved"]) > 6:
            print(f"image_capture:   ... and {len(cap['unresolved']) - 6} more, all in the same state")
        fail(EMPTY, "CAPTURE WOULD SCORE NOTHING: every key this step needs is unresolved",
             "  nothing was written; a capture that names no digest cannot pin a launch",
             f"  {src_line}")
    out = a.out
    if not out:
        work = os.environ.get("GSVTK_WORK")
        if not work:
            sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "kit"))
            import config
            work = str(config.work_dir(CAPTURE_DIR))
        else:
            work = str(pathlib.Path(work) / CAPTURE_DIR)
            pathlib.Path(work).mkdir(parents=True, exist_ok=True)
        out = str(pathlib.Path(work) / f"{source['branch']}-{parsed['head_sha'][:6]}.json")
    if is_tracked(out):
        fail(UNUSABLE, f"CAPTURE IS TRACKED: {out} sits inside the git-tracked set",
             "  a capture holds real registry coordinates; the audit grades exactly that set",
             "  write it under $GSVTK_WORK/image-capture/ (the default) instead")
    if a.dry_run:
        print(json.dumps(cap, indent=2, sort_keys=True))
        print(f"image_capture: --dry-run, nothing written (would write {out})")
        return 0
    pathlib.Path(out).parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path(out).write_text(json.dumps(cap, indent=2, sort_keys=True) + "\n")
    print(f"image_capture: wrote {out}")
    print(f"image_capture:   {len(cap['images'])} of {len(keys)} key(s) pinned by this step, "
          f"{len(cap['unresolved'])} unresolved, source: {src_line}")
    for key, why in sorted(cap["unresolved"].items()):
        print(f"image_capture:   unresolved {key.split('.')[-1]}: "
              f"{why_with_registry(why, digest_errs)}")
    print(f"image_capture:   read it with: terra/batch_rerun_step.py --image-from {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
