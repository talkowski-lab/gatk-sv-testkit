#!/usr/bin/env python3
"""fetch_outputs.py -- get a run's artifacts by WORKFLOW OUTPUT NAME, and leave a checksummed
manifest behind so the next person can check the bytes instead of trusting this session.

Why this exists (GAP-REVIEW-manta-tloc.md §1, GAP-REVIEW-synthesis.md A5): the fetcher in this
repo, ``terra/batch_fetch_compare.sh``, reads ``sample_set`` entity attributes carrying a
``*<GSVTK_NEW_SUFFIX>`` suffix -- a literal list of seven names, for one workflow, on one entity
type. The case it cannot serve is the one the review actually hit: **the Terra entity attributes
stayed empty and only Cromwell's ``outputs`` had the paths.** A name read out of the run's own
metadata also cannot go stale the way a transcription of one branch's signature does, so this tool
reads ``outputs`` and nothing else.

Read-only, and offline by construction: it opens a Cromwell metadata JSON that is already on disk
(``terra/batch_save_metadata.py`` writes them) and shells out to ``gsutil cp`` at most -- no POST,
no mutation, no Terra call, nothing that can spend money. It deliberately does not import
``terra.py``, so it runs with no credentials and no ``firecloud`` installed.

Two rules it is built around, both from this repo's own history:
  * **Name the thing it wanted.** An output that cannot be resolved stops the run with its name and
    the names that ARE declared. Nothing is fetched and NO manifest is written when any requested
    name is unresolved -- a manifest with a hole in it reads like "the run had no such artifact",
    which is the silent-empty failure ``compare/artifact.py`` refuses with ``compared_something``.
  * **"Declared" and "present" are different claims, so they fail differently** (exit 3 vs exit 4),
    and both print the workflow's own status -- because a Failed run legitimately has no outputs.

Which paths are proven offline, and which are not
------------------------------------------------
EXERCISED OFFLINE -- the ``fetch_outputs`` assertions in ``scripts/selftest.sh``, run against a
metadata fixture written under $TMPDIR (no credentials, no network, no ``gsutil``/``gcloud`` call):
resolving a name out of the metadata
``outputs`` map, all three outcomes (0 present / 3 never declared / 4 declared-with-no-value), the
``--dry-run`` plan including the EXACT command line a fetch would run, and the copy itself via
``--link-dir``, which is an ``os.link``/``shutil.copyfile`` of a file already on disk.

NEEDS REAL CLOUD ACCESS, and is therefore NOT exercised here: the BYTES of a ``gs://`` object.
That is one call, ``gsutil -m cp -n gs://…``, and its command line is still proven offline --
``--dry-run`` prints exactly what a fetch would exec, and ``main()``/``download()`` take an
injectable ``run=`` (the seam ``terra/batch_peek.py``'s ``rc_tally(prefix, run=subprocess.run)``
uses) so a harness can record the argv, the ``rc!=0`` refusal and the missing-binary refusal with no
transport at all. What no offline run can confirm is that a bucket still holds the bytes an adopted
or already-present local file has; those are recorded as "NOT verified against the object", and only
a real download re-hashes what the bucket is serving today.

    python terra/fetch_outputs.py --metadata work/metadata/10-new.abcd1234.json \
        --output GenotypeBatch.genotyped_pesr_vcf                 # one output by name
    python terra/fetch_outputs.py --metadata <file> --all --dry-run
    python terra/fetch_outputs.py --verify                        # re-hash a written manifest

Exit codes: 0 fetched or verified clean, 2 usage-shaped refusals (ambiguous name, --all with
--output, nothing requested), 3 a requested output the run does not declare, 4 an output that IS
declared but carries no usable value, 5 no readable metadata with an ``outputs`` map, 6 a fetch
that did not land or a re-hash that found drift. Everything is written under ``$GSVTK_WORK``
(``outputs/`` and ``manifests/`` only), and ``--help`` creates nothing at all. The manifest
APPENDS: a second ``--output`` adds its line and keeps the lines an earlier call wrote, so a
manifest can never lose an artifact and look like an artifact that was never fetched.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kit"))
import config  # noqa: E402  (one resolver for bash and python alike; docs/config.md)

# The metadata key that holds the answer. Cromwell puts every workflow output in the root `outputs`
# map keyed `<Workflow>.<name>` (scatter outputs carry the scatter name too, and an Array[File]
# output is a list of paths). This tool reads THAT map and no other source: the entity attributes
# are the thing that lied, so borrowing them back in here would re-import the bug.
OUTPUTS_KEY = "outputs"
HASH_CHUNK = 1024 * 1024


def refuse(msg: str, code: int):
    """Fail with the name of what was wanted, and a code that says WHICH failure it was."""
    print(f"fetch_outputs: {msg}", file=sys.stderr)
    sys.exit(code)


def p(msg: str) -> None:
    print(msg, flush=True)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(HASH_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def load_metadata(path: Path) -> dict:
    if not path.is_file():
        refuse(f"no metadata file at {path}\n"
               f"  write one with:  python terra/batch_save_metadata.py --outdir "
               f"{config.work_path('metadata')}\n"
               "  (that call is read-only against Terra; this tool never fetches metadata itself, "
               "so it needs\n   no credentials and cannot submit anything)", 5)
    try:
        doc = json.loads(path.read_text())
    except ValueError as e:
        refuse(f"cannot parse {path} as JSON: {e}", 5)
    if not isinstance(doc, dict):
        refuse(f"{path} holds a {type(doc).__name__}, not a Cromwell metadata object; --metadata "
               "wants the file terra/batch_save_metadata.py wrote", 5)
    return doc


def default_metadata_file() -> Path:
    """The dump to read when --metadata was not given -- or a refusal naming the candidates.

    More than one candidate is an ERROR, not a race to the first hit: ``terra/stage_inputs.py``
    already refuses to choose between two local captures of one object name, and picking by glob or
    sort order is exactly how a stale table becomes a "baseline" input.
    """
    root = config.work_path("metadata")
    found = sorted(root.glob("*.json")) if root.is_dir() else []
    if not found:
        refuse(f"no --metadata given and nothing to discover under {root}\n"
               f"  write one first:  python terra/batch_save_metadata.py --outdir {root}\n"
               "  or name the file:  --metadata <run>.<workflow-id>.json", 5)
    if len(found) > 1:
        refuse(f"{len(found)} metadata files under {root}, and picking one by sort order is not a "
               "resolution:\n  " + "\n  ".join(str(f) for f in found[:8]) +
               "\n  pass --metadata <one of those>", 2)
    return found[0]


def outputs_map(meta: dict, src: Path) -> dict:
    outs = meta.get(OUTPUTS_KEY)
    if not isinstance(outs, dict) or not outs:
        refuse(f"{src} has no non-empty \"{OUTPUTS_KEY}\" map"
               + (f" (its root status is {meta.get('status')!r})" if meta.get("status") else "")
               + "\n  This tool reads Cromwell's outputs and nothing else, on purpose: the case it "
                 "exists for is a run\n  whose Terra entity attributes stayed empty. An absent "
                 "outputs map is NOT \"no outputs\" --\n  re-dump the metadata with "
                 "terra/batch_save_metadata.py and check the workflow id.", 5)
    return outs


def resolve(name: str, outs: dict):
    """Requested name -> (declared key, value). Refusals name what they wanted and what exists.

    The exact key is tried first, then a trailing-component match, because Cromwell keys are
    ``<Workflow>.<name>`` and nobody wants to type the workflow prefix -- but an ambiguous tail match
    is refused rather than resolved to whichever key came first.
    """
    if name in outs:
        return name, outs[name]
    tail = [(k, v) for k, v in outs.items() if k.rsplit(".", 1)[-1] == name]
    if len(tail) == 1:
        return tail[0]
    if len(tail) > 1:
        refuse(f"output {name!r} is ambiguous -- {len(tail)} declared keys end in it:\n  "
               + "\n  ".join(sorted(k for k, _ in tail))
               + "\n  name the one you want with its workflow prefix.", 2)
    listed = "\n  ".join(sorted(outs)[:20])
    extra = "" if len(outs) <= 20 else f"\n  ... ({len(outs) - 20} more)"
    refuse(f"this run declares no output named {name!r}.\n"
           f"  {len(outs)} output(s) declared in the metadata:\n  {listed}{extra}\n"
           "  An unresolved name is a failure, not a skipped row: a manifest missing an artifact "
           "reads like \"the run\n  produced nothing\", which is how a dead arm becomes a result "
           "(GAP-REVIEW-manta-tloc.md §2.4).", 3)


def values_of(key: str, value, status) -> list:
    """Declared value -> the list of gs:// objects it names, or a refusal / an empty list.

    STATUS (the run's own) goes into the refusal text: the fact that separates "declared but no
    value" from "no such output" is whether the workflow finished, and a reader should not have to
    go and look it up. It is passed in rather than added by catching the SystemExit, because
    ``SystemExit(str(e))`` on a code-carrying exit stringifies the CODE -- which is how "4" once
    appeared in the middle of this message and the exit status came out as 1.
    """
    if value is None:
        # (c) Declared, but no value -- NOT the same failure as a name that was never declared, so
        # it exits 4 with the workflow status beside it: a Failed or Aborted run produces declared
        # keys with nothing behind them, and that is a diagnosis, not a missing name.
        refuse(f"output {key!r} IS declared but carries no value (null).\n"
               "  This is not \"the workflow has no such output\" (exit 3): the binding exists and "
               "the run did not\n  produce a file for it. Read the task's own executionStatus "
               "before re-running anything.\n"
               f"  workflow status recorded in the metadata: {status!r}", 4)
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        return []                     # a scalar output (Int/String/Boolean): a value, not an artifact
    bad = [i for i in items if not isinstance(i, str) or not i.startswith("gs://")]
    if bad:
        refuse(f"output {key!r} has {len(bad)} value(s) that are not gs:// objects: {bad[:3]}\n"
               "  A binding that resolved to a non-object is a submission bug, not a download bug; "
               "refusing to\n  guess which part of it to fetch.", 4)
    return items


def adopt(uri: str, link_dirs: list) -> str:
    """A local capture of this object, or "". Refuses rather than guessing between candidates."""
    base = os.path.basename(uri)
    cands = []
    for d in link_dirs:
        root = Path(os.path.expanduser(d))
        cands += [Path(c) for c in sorted(glob.glob(str(root / "**" / base), recursive=True))]
    if not cands:
        return ""
    if len(cands) > 1:
        refuse(f"{len(cands)} local files are named {base}, so --link-dir adoption cannot pick "
               "one:\n  " + "\n  ".join(str(c) for c in cands[:6]) +
               "\n  Narrow --link-dir, or drop it and download the object.", 2)
    return str(cands[0])


def origin_of(uri: str, dest: Path, link_dirs: list) -> tuple:
    """Where one object would come from -- (kind, path), decided WITHOUT touching anything.

    One function answers this for both --dry-run and the fetch, so the command --dry-run prints is
    the command that then runs, rather than a second guess at the same decision.
    """
    if dest.is_file():
        return ("already-there", str(dest))
    local = adopt(uri, link_dirs) if link_dirs else ""
    if local:
        return ("adopt", local)
    return ("download", "")


def fetch_cmd(uri: str, dest: Path) -> list:
    """The ONE spelling of the download command, so what --dry-run prints is what a fetch runs.

    ``-n`` (no-clobber) is load-bearing: a re-fetch must not overwrite bytes an earlier manifest
    already hashed -- that is the drift the previous-manifest comparison below exists to catch.
    """
    return ["gsutil", "-m", "cp", "-n", uri, str(dest)]


def download(uri: str, dest: Path, run=subprocess.run) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = fetch_cmd(uri, dest)
    p(f"  + {' '.join(cmd)}")
    try:
        r = run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        # Named the way terra/batch_peek.py's rc_tally() names it -- the missing binary, plus the
        # offline way through -- rather than a traceback over a subprocess that never started.
        refuse(f"gsutil is not on PATH (install the Cloud CLI, docs/setup.md) and "
               f"{os.path.basename(uri)} is in no --link-dir, so {uri} cannot be fetched.\n"
               "  --dry-run prints the command without running it; --link-dir adopts a local "
               "capture of this object instead.", 6)
    if r.returncode != 0:
        refuse(f"gsutil cp failed (rc {r.returncode}) for {uri}\n"
               f"  {(r.stderr or r.stdout or '').strip()[:400]}\n"
               "  Nothing is recorded for an object that did not land.", 6)


def main(argv: list, run=subprocess.run) -> int:
    """Run one fetch. ``run`` is the transport seam ``terra/batch_peek.py``'s ``rc_tally`` uses: a
    harness can pass a recorder, so the ``gsutil`` command line is provable without a network."""
    ap = argparse.ArgumentParser(
        prog="fetch_outputs.py",
        description="Fetch a run's artifacts by Cromwell output NAME into $GSVTK_WORK and write a "
                    "sha256 manifest. Read-only: gsutil cp only, no Terra call, no mutation.",
        epilog="Paths resolve lazily so --help works with no profile: objects land under "
               "$(./kit/gsvtk-config work outputs)/ and the manifest under "
               "$(./kit/gsvtk-config work manifests)/outputs_manifest.json.")
    ap.add_argument("--metadata", metavar="FILE",
                    help="Cromwell metadata JSON (default: the only file under $GSVTK_WORK/metadata "
                         "-- more than one there is a refusal, not a guess)")
    ap.add_argument("--output", action="append", default=[], metavar="NAME",
                    help="workflow output name to fetch; repeatable. A trailing-component match "
                         "against the declared keys is allowed; an ambiguous one is refused.")
    ap.add_argument("--all", action="store_true",
                    help="every declared output that carries a gs:// object (scalar outputs such as "
                         "an Int are recorded, not fetched). This is a bulk download.")
    ap.add_argument("--subdir", default="outputs",
                    help="directory under $GSVTK_WORK to write into (default: outputs)")
    ap.add_argument("--manifest", metavar="FILE",
                    help="where the manifest goes (default: $GSVTK_WORK/manifests/"
                         "outputs_manifest.json)")
    ap.add_argument("--link-dir", action="append", default=[], metavar="DIR",
                    help="adopt an already-local capture of an object from here instead of "
                         "downloading it (same convention as terra/stage_inputs.py --link-dir); the "
                         "manifest records that it was adopted, not downloaded")
    ap.add_argument("--dry-run", action="store_true",
                    help="resolve every name and print the plan, INCLUDING the exact command each "
                         "object would be fetched with; download nothing, write nothing")
    ap.add_argument("--verify", action="store_true",
                    help="do not fetch: re-hash what the manifest at --manifest recorded and report "
                         "drift, missing files and unreadable records")
    a = ap.parse_args(argv)

    manifest_path = (Path(a.manifest).expanduser() if a.manifest
                     else config.work_path("manifests") / "outputs_manifest.json")

    # ---------------------------------------------- verify: the manifest is what is under test
    if a.verify:
        if not manifest_path.is_file():
            refuse(f"--verify needs an existing manifest; none at {manifest_path}", 5)
        try:
            doc = json.loads(manifest_path.read_text())
        except OSError as e:
            refuse(f"--verify cannot read {manifest_path}: {e}", 6)
        except ValueError as e:
            refuse(f"--verify cannot parse {manifest_path}: {str(e)[:200]}\n"
                   "  An unparseable manifest is not a manifest that verified clean.", 6)
        if not isinstance(doc, dict) or not isinstance(doc.get("files"), dict):
            # Same rule as terra/terra.py's _as_items: an unexpected shape is named, never read as
            # "nothing to check, all clean".
            refuse(f"--verify: {manifest_path} carries no \"files\" map "
                   f"(it is a {type(doc).__name__}) -- refusing to read that as \"nothing to "
                   "verify, all clean\"", 6)
        files = doc["files"]
        bad = 0
        for name in sorted(files):
            rec = files[name]
            if not isinstance(rec, dict):
                p(f"  BAD      {name}: its record is a bare {type(rec).__name__}, not an object "
                  "that can be re-hashed")
                bad += 1
                continue
            local = Path(rec.get("local", ""))
            if not local.is_file():
                p(f"  MISSING  {name}: the manifest says {local}, which is not there")
                bad += 1
                continue
            got = sha256_of(local)
            if got != rec.get("sha256"):
                p(f"  DRIFT    {name}: sha256 {got[:12]}... is not the recorded "
                  f"{str(rec.get('sha256'))[:12]}...")
                bad += 1
            else:
                p(f"  ok       {name}  {rec.get('bytes')} bytes  sha256 {got[:12]}...")
        p(f"verify: {len(files)} recorded, {bad} problem(s), manifest {manifest_path}")
        return 6 if bad else 0

    # ------------------------------------ resolve everything, then fetch, then write -- in that order
    src = Path(a.metadata).expanduser() if a.metadata else default_metadata_file()
    meta = load_metadata(src)
    outs = outputs_map(meta, src)

    if a.all and a.output:
        refuse("--all and --output together is a contradiction: say which", 2)
    if not a.all and not a.output:
        # No implicit bulk fetch: a whole chain's outputs are tens of GB, so "which ones" is the
        # caller's call, not a default. The declared names are printed so the answer is one line.
        refuse(f"say what to fetch -- {len(outs)} output(s) are declared in {src.name} and none was "
               "requested.\n  " + "\n  ".join(sorted(outs)[:25])
               + (f"\n  ... ({len(outs) - 25} more)" if len(outs) > 25 else "")
               + "\n  --output NAME (repeatable) for the ones you need, or --all for every gs:// "
                 "object this run\n  produced -- size that download before asking for it.", 2)

    plan, values = [], {}
    run_status = meta.get("status")
    for name in list(a.output):
        key, value = resolve(name, outs)          # exits 3, naming `name`, if it is not declared
        uris = values_of(key, value, run_status)      # exits 4 for null / non-object shapes
        if not uris:
            values[key] = value                   # a scalar output: recorded, never fetched
            p(f"  [value]  {key} = {value!r} (not an object; recorded in the manifest, not fetched)")
            continue
        for i, uri in enumerate(uris):
            plan.append((key if len(uris) == 1 else f"{key}[{i}]", uri))
    if a.all:
        for key in sorted(outs):
            uris = values_of(key, outs[key], run_status)
            if not uris:
                values[key] = outs[key]
                continue
            for i, uri in enumerate(uris):
                plan.append((key if len(uris) == 1 else f"{key}[{i}]", uri))

    out_dir = config.work_path(a.subdir)
    p(f"== {src.name}: {len(plan)} object(s) from {len(set(k.split('[')[0] for k, _ in plan))} "
      f"output name(s)  ->  {out_dir}")
    for key, uri in plan:
        p(f"  plan     {key}  {uri}")
    if a.dry_run:
        # The command is printed, not summarised, so an offline run can prove what a real one would
        # execute. --link-dir lines are the ones that ARE runnable offline (a local cp of a file
        # already on disk); the gsutil lines need the bucket.
        for key, uri in plan:
            dest = out_dir / os.path.basename(uri)
            kind, detail = origin_of(uri, dest, a.link_dir)
            if kind == "already-there":
                p(f"  would    {key}: use {dest}, already on disk")
            elif kind == "adopt":
                p(f"  would    {key}: cp {detail} {dest}   "
                  "(local --link-dir capture; no cloud access)")
            else:
                p(f"  would    {key}: {' '.join(fetch_cmd(uri, dest))}   (needs real cloud access)")
        p(f"  --dry-run: nothing downloaded and nothing written (the manifest would be "
          f"{manifest_path})")
        return 0
    if not plan and not values:
        refuse("nothing resolved to anything fetchable, and an empty manifest is not a result", 3)

    prev_doc = {}
    if manifest_path.is_file():
        try:
            prev_doc = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            p("  [note] the existing manifest is unreadable; writing a fresh one over it")
        if not isinstance(prev_doc, dict):
            p(f"  [note] the existing manifest holds a {type(prev_doc).__name__}, not an object; "
              "writing a fresh one over it")
            prev_doc = {}
    prev = prev_doc.get("files") if isinstance(prev_doc.get("files"), dict) else {}
    files, missing = {}, []
    for key, uri in plan:
        dest = out_dir / os.path.basename(uri)
        kind, detail = origin_of(uri, dest, a.link_dir)
        present = kind == "already-there"
        source = ""
        if kind == "adopt":
            dest.parent.mkdir(parents=True, exist_ok=True)
            if Path(detail).resolve() != dest.resolve():
                try:
                    # Hardlink, not copy: these are multi-GB VCFs and --link-dir exists to avoid a
                    # second copy. The consequence is that $GSVTK_WORK/outputs/<name> and the
                    # capture under --link-dir are ONE file -- edit one and you have edited the
                    # capture, which is why the record says "adopted", never "downloaded".
                    os.link(detail, dest)
                except OSError:
                    # Cross-device, or a filesystem without hardlinks: copy instead.
                    shutil.copyfile(detail, dest)
            source = f"adopted from --link-dir ({detail}); NOT verified against the object"
        elif kind == "download":
            download(uri, dest, run)
            source = "downloaded with gsutil cp"
        if not dest.is_file() or dest.stat().st_size == 0:
            missing.append(f"{key} -> {dest}")
            continue
        # One hash pass per object (these are multi-GB VCFs), used both for the record and for the
        # comparison below.
        digest = sha256_of(dest)
        if present:
            # Three states, and the middle one used to be reported like the first: the file matches
            # the previous manifest / its hash DIFFERS / there is no previous record. Printing
            # "already present" for a drifted file is how a stale capture ends up quoted as the
            # current object (terra/stage_inputs.py learned this the hard way).
            recorded_of = prev.get(key)
            recorded = recorded_of.get("sha256") if isinstance(recorded_of, dict) else None
            if recorded == digest:
                source = "already present, and sha256 matches the previous manifest"
            elif recorded:
                source = (f"!! already present but the sha256 DIFFERS from the previous manifest "
                          f"record ({str(recorded)[:12]}... -> {digest[:12]}...): this local file is "
                          "not the bytes that fetch recorded")
                p(f"  [drift]  {key}: the local bytes differ from the previous manifest; delete the "
                  "file and re-fetch before quoting them")
            else:
                source = "already present; no previous manifest record to compare it against"
        files[key] = {"gs": uri, "local": str(dest), "sha256": digest,
                      "bytes": dest.stat().st_size, "source": source}
        p(f"  [ok]     {key}  {files[key]['bytes']} bytes  sha256={digest}")
        p(f"           via {source}")
    if missing:
        refuse(f"{len(missing)} object(s) did not land:\n  " + "\n  ".join(missing)
               + "\n  No manifest is written for a partial fetch.", 6)

    if not files and not values:
        # compare/artifact.py's `compared_something: false` rule, applied to a fetcher: a run that
        # recorded nothing must not look like a run that found nothing to record.
        refuse("nothing was recorded; an empty manifest is not a result", 3)

    # APPEND, never replace. `--output A` then `--output B` adds B's line and keeps A's; a manifest
    # that quietly dropped A the moment B was fetched reads like "A was never fetched" -- the same
    # silent hole the resolve rules above refuse. A row for a key this run did resolve is refreshed
    # in place (this run's record wins), so re-fetching one artifact cannot double-record it.
    prev_values = prev_doc.get("values") if isinstance(prev_doc.get("values"), dict) else {}
    carried_files = {k: v for k, v in prev.items()
                     if isinstance(v, dict) and k not in files and k not in values}
    carried_values = {k: v for k, v in prev_values.items()
                      if k not in values and k not in files and k not in carried_files}
    if (carried_files or carried_values) and prev_doc.get("metadata_file") not in (None, str(src)):
        p(f"  [note] {manifest_path} already held records resolved from "
          f"{prev_doc.get('metadata_file')}, now kept alongside this run's -- one manifest per run "
          "is the rule, so pass --manifest to keep two runs apart")
    files = {**carried_files, **files}
    values = {**carried_values, **values}
    doc = {"generated": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
           "metadata_file": str(src),
           "workflow_id": meta.get("id"),
           "workflow_status": meta.get("status"),
           "resolved_from": f"metadata {OUTPUTS_KEY} (not entity attributes)",
           "values": values,
           "files": files}
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    p(f"== manifest {manifest_path}: {len(files)} file(s) recorded "
      f"({len(files) - len(carried_files)} fetched this run, {len(carried_files)} carried from the "
      f"previous manifest), {len(values)} scalar value(s)")
    p(f"   re-check the bytes later:  python terra/fetch_outputs.py --verify --manifest "
      f"{manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
