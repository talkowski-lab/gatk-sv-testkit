#!/usr/bin/env python3
"""Upload one gatk-sv workflow, flattened at a git ref, to Terra's method repository (Agora).

A rerun config normally names a Dockstore entry, and Dockstore only carries published branches. This
puts the WDL of any commit into the method repository instead, as a private snapshot under your own
namespace, so `terra/batch_rerun_step.py --method agora://<ns>/<name>/<snapshot>` can run it.

    python terra/method_upload.py GenotypeBatch --ref my-branch            # plan (free, default)
    python terra/method_upload.py GenotypeBatch --ref my-branch upload --confirm

The WDL is read out of GSVTK_GATK_SV_CHECKOUT with `git archive <sha> wdl` into a temp dir, so the
checkout's working tree and index are never touched, and an uncommitted edit is never uploaded. The
flattening is `terra/wdl_flat.py`. Provenance (origin URL, full commit sha, ref, workflow) goes into
the synopsis, the documentation, the snapshot comment and a comment block at the top of the WDL
itself, so it survives in Terra with the snapshot.

`upload` writes a record under `gsvtk-config work method-upload`; batch_rerun_step.py reads it to
grade the config's bindings against the uploaded commit rather than GSVTK_BRANCH.

Exit codes: 0 ok, 1 failure, 2 usage error or refused mode, 3 missing dependency, 4 missing config.
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
sys.path.insert(0, HERE)
import config  # noqa: E402
import wdl_flat  # noqa: E402

# Agora accepts letters, digits, `-`, `_` and `.` in a namespace and a method name.
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
AGORA_RE = re.compile(r"^agora://([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/([1-9][0-9]*)$")
SYNOPSIS_MAX = 80          # Agora rejects a longer synopsis
RECORD_DIR = "method-upload"


def read_only() -> bool:
    v = os.environ.get("GSVTK_READ_ONLY", "")
    return bool(v) and v != "0"


def refuse(msg: str, code: int = 2) -> None:
    sys.stderr.write(f"method_upload: {msg}\n")
    raise SystemExit(code)


# ------------------------------------------------------------------ Agora URIs (used by the rerun)

def parse_agora_uri(uri: str) -> tuple:
    """`agora://ns/name/N` -> (ns, name, N). Raises ValueError naming the expected form."""
    m = AGORA_RE.match(str(uri or "").strip())
    if not m:
        raise ValueError(f"{uri!r} is not agora://<namespace>/<name>/<snapshot> "
                         "(snapshot is a positive integer, as `method_upload.py upload` prints it)")
    return m.group(1), m.group(2), int(m.group(3))


def agora_method(uri: str) -> dict:
    """The methodRepoMethod a method config states for an Agora snapshot."""
    ns, name, snap = parse_agora_uri(uri)
    return {"sourceRepo": "agora", "methodNamespace": ns, "methodName": name,
            "methodVersion": snap, "methodUri": f"agora://{ns}/{name}/{snap}"}


def record_path(ns: str, name: str, snap) -> str:
    """Where `upload` records a snapshot's provenance (no directory is created)."""
    return str(config.work_path(RECORD_DIR) / f"{ns}__{name}__{snap}.json")


def read_record(uri: str) -> dict:
    """The local upload record for this snapshot, or {} when this machine did not upload it."""
    ns, name, snap = parse_agora_uri(uri)
    try:
        with open(record_path(ns, name, snap), encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return {}
    return rec if isinstance(rec, dict) else {}


# ------------------------------------------------------------------ build

def git(ck: str, *args: str, binary: bool = False):
    r = subprocess.run(["git", "-C", ck, *args], capture_output=True, text=not binary)
    if r.returncode:
        err = r.stderr if not binary else r.stderr.decode("utf-8", "replace")
        raise RuntimeError(f"git -C {ck} {' '.join(args)}: {(err or '').strip()[:300]}")
    return r.stdout


def resolve_sha(ck: str, ref: str) -> str:
    try:
        return git(ck, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except RuntimeError:
        refuse(f"{ref!r} does not name a commit in {ck}.\n"
               "  Fetch it first (git -C <checkout> fetch <remote> <branch>), or pass a sha.")
    return ""   # unreachable


def repo_url(ck: str) -> str:
    try:
        return git(ck, "remote", "get-url", "origin").strip()
    except RuntimeError:
        return f"(no origin remote; local checkout {ck})"


def extract_wdl(ck: str, sha: str) -> str:
    """<tmp>/wdl holding the tree's wdl/ at sha, via git archive (the checkout is not touched)."""
    tmp = tempfile.mkdtemp(prefix="gsvtk-method-")
    atexit.register(shutil.rmtree, tmp, True)
    tar = git(ck, "archive", sha, "wdl", binary=True)
    if subprocess.run(["tar", "-x", "-C", tmp], input=tar).returncode:
        refuse(f"could not unpack wdl/ at {sha}", 1)
    return os.path.join(tmp, "wdl")


def provenance(wf: str, ref: str, sha: str, url: str) -> dict:
    return {"workflow": wf, "ref": ref, "sha": sha, "repo": url,
            "tool": "gatk-sv-testkit terra/method_upload.py (terra/wdl_flat.py)"}


def stamp(text: str, prov: dict) -> str:
    """Put the provenance in a comment block after `version 1.0`; the WDL itself is unchanged."""
    head, sep, rest = text.partition("\n")
    block = ("# gatk-sv-testkit method_upload: flattened, not hand-edited\n"
             f"#   repo:     {prov['repo']}\n"
             f"#   commit:   {prov['sha']}\n"
             f"#   ref:      {prov['ref']}\n"
             f"#   workflow: wdl/{prov['workflow']}.wdl\n")
    return head + sep + block + rest


def synopsis(prov: dict) -> str:
    s = f"gatk-sv {prov['workflow']} @ {prov['sha'][:12]} ({prov['ref']})"
    return s if len(s) <= SYNOPSIS_MAX else s[:SYNOPSIS_MAX - 3] + "..."


def documentation(prov: dict, sha256: str) -> str:
    return (f"Flattened `wdl/{prov['workflow']}.wdl` of gatk-sv.\n\n"
            f"- repository: {prov['repo']}\n"
            f"- commit: {prov['sha']}\n"
            f"- ref at upload: {prov['ref']}\n"
            f"- payload sha256: {sha256}\n"
            f"- built by: {prov['tool']}\n\n"
            "The commit is the provenance; the ref may have moved since. Rebuild with "
            f"`python terra/method_upload.py {prov['workflow']} --ref {prov['sha']}` and compare the "
            "sha256.\n")


def build(wf: str, ref: str, ns: str, name: str, ck: str) -> dict:
    """-> {prov, wdl, sha256, request}. `request` is exactly what `upload` POSTs."""
    sha = resolve_sha(ck, ref)
    prov = provenance(wf, ref, sha, repo_url(ck))
    wdl = stamp(wdl_flat.flatten(wf, extract_wdl(ck, sha)), prov)
    digest = hashlib.sha256(wdl.encode("utf-8")).hexdigest()
    request = {"namespace": ns, "name": name, "entityType": "Workflow", "payload": wdl,
               "synopsis": synopsis(prov), "documentation": documentation(prov, digest),
               "snapshotComment": f"gatk-sv {ref} @ {sha}"}
    return {"prov": prov, "wdl": wdl, "sha256": digest, "request": request}


def api_root() -> str:
    return config.get("TERRA_API_ROOT", "https://api.firecloud.org/api/")


def check_target(ns: str, allow_shared: bool) -> None:
    """Refuse the shared baseline namespace, as terra.assert_writable_target does for a workspace.

    The method repository has no workspace, so the namespace alone decides.
    """
    base = config.get("BASELINE_NAMESPACE")
    if ns and base and ns == base and not allow_shared:
        refuse(f"refusing to upload into the baseline namespace {ns!r}.\n"
               "  That namespace holds the shared reference run's methods, not your sandbox's. Set\n"
               "  GSVTK_TERRA_NAMESPACE (or pass --namespace) to your own, or pass\n"
               "  --allow-shared-target if you really mean this namespace.")


# ------------------------------------------------------------------ modes

def plan(b: dict, ns: str, name: str, out_dir) -> None:
    prov, req = b["prov"], b["request"]
    stem = f"{name}-{prov['sha'][:12]}"
    wdl_path = os.path.join(str(out_dir), stem + ".wdl")
    req_path = os.path.join(str(out_dir), stem + ".request.json")
    with open(wdl_path, "w", encoding="utf-8") as fh:
        fh.write(b["wdl"])
    with open(req_path, "w", encoding="utf-8") as fh:
        json.dump(req, fh, indent=1, sort_keys=True)
    shown = dict(req, payload=f"<the flattened WDL: {len(b['wdl'].encode('utf-8'))} bytes, "
                              f"sha256 {b['sha256']}>")
    print(f"method upload plan (nothing is sent)\n"
          f"  target:    {ns}/{name}  (a NEW snapshot; existing snapshots are not changed)\n"
          f"  workflow:  wdl/{prov['workflow']}.wdl\n"
          f"  ref:       {prov['ref']} -> {prov['sha']}\n"
          f"  repo:      {prov['repo']}\n"
          f"  WDL:       {len(b['wdl'].encode('utf-8'))} bytes, sha256 {b['sha256']}\n"
          f"             written to {wdl_path}\n"
          f"  request:   POST {api_root()}methods   (body below; full body in {req_path})")
    print(json.dumps(shown, indent=1, sort_keys=True))
    print("  to send it: re-run with `upload --confirm`. The snapshot is private to you; delete\n"
          "  (redact) it afterwards with DELETE {root}methods/{ns}/{name}/<snapshotId>."
          .format(root=api_root(), ns=ns, name=name))


def post(request: dict):
    """POST the request through the ADC session; returns the response object."""
    import terra      # imported here so `plan` needs neither firecloud nor credentials
    return terra.session().post(f"{terra.TERRA_API}methods", json=request, timeout=120)


def upload(b: dict, ns: str, name: str) -> None:
    req = b["request"]
    print(f"method upload: POST {api_root()}methods -> {ns}/{name}\n"
          f"  commit {b['prov']['sha']} ({b['prov']['ref']}), WDL sha256 {b['sha256']}\n"
          f"  undo: DELETE {api_root()}methods/{ns}/{name}/<snapshotId> (printed below once known)")
    resp = post(req)
    code = getattr(resp, "status_code", None)
    if code not in (200, 201):
        refuse(f"POST methods answered HTTP {code}: {getattr(resp, 'text', '')[:400]}", 1)
    try:
        d = resp.json()
        snap = int(d["snapshotId"])
    except (ValueError, KeyError, TypeError) as exc:
        refuse(f"POST methods answered HTTP {code} without a snapshotId ({exc}); the snapshot may\n"
               f"  exist anyway: list {api_root()}methods?namespace={ns}&name={name}", 1)
    uri = f"agora://{ns}/{name}/{snap}"
    rec = dict(b["prov"], namespace=ns, name=name, snapshotId=snap, methodUri=uri,
               sha256=b["sha256"])
    path = record_path(ns, name, snap)
    config.work_dir(RECORD_DIR)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=1, sort_keys=True)
    print(f"  snapshotId: {snap}\n"
          f"  methodUri:  {uri}\n"
          f"  record:     {path}\n"
          f"  use it:     python terra/batch_rerun_step.py --step NN --method {uri} show\n"
          f"  undo:       DELETE {api_root()}methods/{ns}/{name}/{snap}   (redacts the snapshot), e.g.\n"
          f"              python -c \"import sys; sys.path.insert(0,'terra'); import terra; "
          f"print(terra.fapi.delete_repository_method('{ns}', '{name}', {snap}).status_code)\"")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("workflow", help="WDL filename without .wdl, resolved in <gatk-sv>/wdl/ at --ref")
    ap.add_argument("mode", nargs="?", default="plan", choices=("plan", "upload"))
    ap.add_argument("--ref", help="branch, tag or sha in GSVTK_GATK_SV_CHECKOUT (default GSVTK_BRANCH)")
    ap.add_argument("--namespace", help="method namespace (default GSVTK_TERRA_NAMESPACE)")
    ap.add_argument("--name", help="method name (default: the workflow name)")
    ap.add_argument("--confirm", action="store_true", help="required for upload")
    ap.add_argument("--allow-shared-target", action="store_true",
                    help="allow uploading into GSVTK_BASELINE_NAMESPACE")
    a = ap.parse_args(argv)

    if a.mode == "upload":
        # Refusals that need no config or checkout come first.
        if read_only():
            refuse("upload POSTs to Terra, and GSVTK_READ_ONLY is set. `plan` is the read-only mode.")
        if not a.confirm:
            refuse("upload creates a method snapshot in Terra. Check `plan`, then re-run with --confirm.")
    ns = a.namespace or config.require("TERRA_NAMESPACE", "the method namespace to upload into")
    name = a.name or a.workflow
    for what, v in (("namespace", ns), ("name", name)):
        if not NAME_RE.match(v):
            refuse(f"{what} {v!r}: Agora allows only letters, digits, '-', '_' and '.'")
    check_target(ns, a.allow_shared_target)
    ref = a.ref or config.require("BRANCH", "the gatk-sv ref to upload (or pass --ref)")
    ck = str(config.checkout())
    b = build(a.workflow, ref, ns, name, ck)
    if a.mode == "plan":
        plan(b, ns, name, config.work_dir(RECORD_DIR))
    else:
        upload(b, ns, name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
