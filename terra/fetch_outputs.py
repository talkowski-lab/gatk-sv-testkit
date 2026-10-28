#!/usr/bin/env python3
"""fetch_outputs.py -- get a run's artifacts by workflow output name, and write a sha256 manifest.

Reads the root ``outputs`` map of a Cromwell metadata JSON already on disk (written by
``terra/batch_save_metadata.py``), not Terra entity attributes, which can stay empty when the run
has outputs. Read-only: it runs ``gsutil cp`` at most, makes no Terra call, and does not import
``terra.py`` (except under ``--resolve-cached --online``), so it needs no credentials or
``firecloud``.

An unresolved output name stops the run before anything is fetched or written. "Not declared"
(exit 3) and "declared but empty" (exit 4) are reported separately, with the workflow status.
``main()`` and ``download()`` take an injectable ``run=`` so tests can record the gsutil argv.

    python terra/fetch_outputs.py --metadata work/metadata/10-new.abcd1234.json \
        --output GenotypeBatch.genotyped_pesr_vcf                 # one output by name
    python terra/fetch_outputs.py --metadata <file> --all --dry-run
    python terra/fetch_outputs.py --verify                        # re-hash a written manifest

``--resolve-cached`` labels each requested output by where its bytes really are:
``RESOLVED-DIRECT`` (this run wrote it), ``RESOLVED-CACHECOPY`` (a cacheCopy the cached call's
outputs name; bytes identical to the hit's) or ``RESOLVED-VIA-HIT <wf>:<call>:<shard>`` (the cached
call names nothing, so the path comes from the hit call in that workflow's metadata, read from
``--hit-metadata FILE`` or, with ``--online``, from Terra by read-only GETs -- the only mode that
imports ``terra.py``). It follows Cromwell's documented ``callCaching`` fields and is UNVERIFIED until
checked against a real cache-hit run. The manifest records each file's ``route``.

    python terra/fetch_outputs.py --metadata <file> --output svVCF --resolve-cached \
        --hit-metadata work/metadata/earlier.<wf>.json --dry-run

Exit codes: 0 fetched or verified clean, 2 usage-shaped refusals (ambiguous name, --all with
--output, nothing requested), 3 a requested output the run does not declare, 4 an output that IS
declared but carries no usable value, 5 no readable metadata with an ``outputs`` map, 6 a fetch
that did not land or a re-hash that found drift, 7 --refuse-cached found a call-cache copy, or
--resolve-cached could not follow a cache hit (result absent or malformed, hit workflow not found,
hit call/shard or its output missing). A --resolve-cached output with no path and no cache hit
behind it is exit 4, and an ambiguous or unknown --call is exit 2.
Everything is written under ``$GSVTK_WORK`` (``outputs/`` and ``manifests/`` only), and ``--help``
creates nothing. The manifest appends: a later ``--output`` keeps the records an earlier call wrote.
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kit"))
import config  # noqa: E402  (one resolver for bash and python alike; docs/config.md)

# Cromwell's root `outputs` map, keyed `<Workflow>.<name>`; an Array[File] output is a list of paths.
OUTPUTS_KEY = "outputs"
HASH_CHUNK = 1024 * 1024


def refuse(msg: str, code: int):
    """Print an error and exit with the given code."""
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
    """The only metadata file under $GSVTK_WORK/metadata; more than one is an error."""
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
    """Requested name -> (declared key, value).

    Tries the exact key, then a unique match on the last component (so the workflow prefix is
    optional). An ambiguous match is refused.
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
           "reads like \"the run\n  produced nothing\", which is how a dead arm becomes a result.", 3)


CACHE_MARK = "/cacheCopy/"


def cache_report(outs: dict) -> tuple:
    """`(cached, fresh)`: declared outputs whose object path is a Cromwell call-cache copy.

    A `/cacheCopy/` path means this run did not produce those bytes, so a comparison using them did
    not exercise the code under test.
    """
    cached, fresh = [], []
    for key, value in sorted(outs.items()):
        flat = json.dumps(value) if not isinstance(value, str) else value
        (cached if CACHE_MARK in flat else fresh).append(key)
    return cached, fresh


# ------------------------------------------------------------------ call-cache resolution
#
# Built on Cromwell's documented call metadata, not on an observed cache-hit run, so every route
# below is UNVERIFIED until one is checked against a real submission that hit the cache. A cached
# call carries `callCaching: {hit: true, result: "Cache Hit: <workflowId>:<callFqn>:<shard>"}`, and
# its `outputs` normally point at copies under the new call's cacheCopy/ directory. When they do not
# name the output, the hit call's own metadata does. Anything missing along that chain is a named
# refusal, never a guess.

CACHE_HIT_RE = re.compile(r"^\s*Cache Hit:\s*([^:\s]+):([^:\s]+):(-?\d+)\s*$")
ROUTE_DIRECT = "RESOLVED-DIRECT"
ROUTE_CACHECOPY = "RESOLVED-CACHECOPY"
ROUTE_VIA_HIT = "RESOLVED-VIA-HIT"
UNVERIFIED_NOTE = ("cache resolution follows Cromwell's documented callCaching metadata and is "
                   "UNVERIFIED against a real cache-hit run")


class Refusal(Exception):
    """One output that could not be resolved: a named reason, a message and an exit code."""

    def __init__(self, name: str, msg: str, code: int = 7):
        super().__init__(msg)
        self.name, self.msg, self.code = name, msg, code


def gs_paths(value) -> list:
    """Every gs:// string inside a call or workflow output value (str, list, or map)."""
    if isinstance(value, str):
        return [value] if value.startswith("gs://") else []
    if isinstance(value, list):
        return [x for v in value for x in gs_paths(v)]
    if isinstance(value, dict):
        return [x for v in value.values() for x in gs_paths(v)]
    return []


def metadata_nodes(meta: dict):
    """Each workflow node in a metadata tree: the root, batch_save_metadata's `_children`, and
    Cromwell's own `subWorkflowMetadata`."""
    stack = [meta]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        yield node
        stack.extend(k for k in (node.get("_children") or []) if isinstance(k, dict))
        for entries in (node.get("calls") or {}).values():
            for c in entries if isinstance(entries, list) else []:
                if isinstance(c, dict) and isinstance(c.get("subWorkflowMetadata"), dict):
                    stack.append(c["subWorkflowMetadata"])


def call_entries(node: dict) -> list:
    """`(fqn, shard, entry)` for one node's calls, one entry per shard.

    Retries list several attempts per shard; the last Done attempt wins, else the last attempt.
    """
    out = []
    for fqn, entries in sorted((node.get("calls") or {}).items()):
        by_shard = {}
        for c in entries if isinstance(entries, list) else []:
            if isinstance(c, dict):
                by_shard.setdefault(c.get("shardIndex", -1), []).append(c)
        for shard in sorted(by_shard, key=lambda s: s if isinstance(s, int) else -1):
            group = by_shard[shard]
            done = [c for c in group if c.get("executionStatus") == "Done"] or group
            out.append((fqn, shard, max(done, key=lambda c: c.get("attempt") or 0)))
    return out


def is_cache_hit(entry: dict) -> bool:
    cc = entry.get("callCaching")
    return isinstance(cc, dict) and cc.get("hit") is True


def parse_hit(fqn: str, shard, entry: dict) -> tuple:
    """`(workflowId, callFqn, shard)` from a cached call's `callCaching.result`, or a Refusal."""
    where = f"{fqn}:{shard}"
    result = (entry.get("callCaching") or {}).get("result")
    if not isinstance(result, str) or not result.strip():
        raise Refusal("HIT-RESULT-ABSENT",
                      f"call {where} is a cache hit whose callCaching.result is absent, so the call "
                      "it copied cannot be named", 7)
    m = CACHE_HIT_RE.match(result)
    if not m:
        raise Refusal("HIT-RESULT-MALFORMED",
                      f"call {where} has callCaching.result {result[:120]!r}, not "
                      "\"Cache Hit: <workflowId>:<callFqn>:<shard>\"", 7)
    return m.group(1), m.group(2), int(m.group(3))


def parse_call_spec(spec: str) -> tuple:
    """`NAME=FQN[:SHARD][@OUTPUT]` -> (NAME, FQN, shard or None, OUTPUT or None)."""
    name, sep, rest = spec.partition("=")
    if not sep or not name or not rest:
        refuse(f"--call {spec!r}: want NAME=CALL_FQN[:SHARD][@CALL_OUTPUT]", 2)
    rest, _, outkey = rest.partition("@")
    fqn, shard = rest, None
    head, colon, tail = rest.rpartition(":")
    if colon and tail.lstrip("-").isdigit():
        fqn, shard = head, int(tail)
    if not fqn:
        refuse(f"--call {spec!r}: no call name before the shard", 2)
    return name, fqn, shard, (outkey or None)


class HitIndex:
    """Workflow metadata nodes by id: the run's own tree, --hit-metadata files, then (only with
    --online) Terra, read-only."""

    def __init__(self, docs: list, online=None):
        self.nodes = {}
        for doc in docs:
            for node in metadata_nodes(doc):
                if node.get("id"):
                    self.nodes.setdefault(node["id"], node)
        self.online = online            # callable(workflow_id) -> metadata dict, or a Refusal
        self.searched = {}

    def get(self, wf: str) -> dict:
        if wf in self.nodes:
            return self.nodes[wf]
        if self.online is None:
            raise Refusal("HIT-WORKFLOW-NOT-FOUND",
                          f"the hit workflow {wf} is in neither this run's metadata nor any "
                          "--hit-metadata file.\n    Save its metadata (terra/batch_save_metadata.py --ns "
                          "--ws --submission --workflow --outdir DIR) and pass --hit-metadata FILE, "
                          "or pass --online to look it up in Terra.", 7)
        if wf not in self.searched:
            try:
                doc = self.online(wf)
            except Refusal as r:
                self.searched[wf] = r
            else:
                for node in metadata_nodes(doc):
                    if node.get("id"):
                        self.nodes.setdefault(node["id"], node)
                self.searched[wf] = None
        if isinstance(self.searched[wf], Refusal):
            raise self.searched[wf]
        if wf not in self.nodes:
            raise Refusal("HIT-WORKFLOW-NOT-FOUND",
                          f"Terra returned metadata, but no node in it has id {wf}", 7)
        return self.nodes[wf]


def terra_module():
    """Import terra.py on demand, so the default mode still needs no firecloud or credentials."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import terra
    except SystemExit as e:             # terra.py exits with an install hint when firecloud is absent
        refuse(f"--online needs firecloud: {e}", 3)
    return terra


def online_lookup(ns: str, ws: str, submission_id: str, max_submissions: int, tm=None):
    """A `workflow_id -> metadata` callable that reads Terra (GETs only).

    With `submission_id` it reads that submission directly. Otherwise it walks at most
    `max_submissions` of the workspace's newest submissions for one listing the workflow id. A
    sub-workflow id is never listed there, so that case needs --hit-submission.
    """
    def lookup(wf: str) -> dict:
        t = tm or terra_module()
        sub = submission_id
        try:
            if not sub:
                listing = t.submissions(ns, ws, limit=max_submissions).get("submissions", [])
                for s in listing[:max_submissions]:
                    sid = s.get("submissionId")
                    full = t.submission(ns, ws, sid)
                    if any(w.get("workflowId") == wf for w in (full.get("workflows") or [])):
                        sub = sid
                        break
                if not sub:
                    raise Refusal("HIT-WORKFLOW-NOT-FOUND",
                                  f"none of the {min(len(listing), max_submissions)} newest "
                                  f"submission(s) in {ns}/{ws} lists workflow {wf} (cap "
                                  f"--max-submissions {max_submissions}).\n    A sub-workflow id is "
                                  "never listed at submission level: pass --hit-submission ID, or "
                                  "--hit-metadata FILE.", 7)
            print(f"  [online] GET workflow metadata {ns}/{ws} submission {sub} workflow {wf}",
                  flush=True)
            return t._j(t.fapi.get_workflow_metadata(ns, ws, sub, wf),
                        f"get_workflow_metadata {sub}/{wf}")
        except t.TerraError as e:
            raise Refusal("HIT-WORKFLOW-NOT-FOUND",
                          f"Terra lookup for hit workflow {wf} in {ns}/{ws} failed: "
                          f"{str(e).splitlines()[0][:200]}", 7)
    return lookup


def producers_for(key: str, meta: dict, spec) -> tuple:
    """The call entries that produced one workflow output: `([(fqn, shard, entry)], output_key)`.

    Used when the workflow output declares no path. An explicit --call wins; otherwise the calls
    declaring an output with the same last name component. Calls of more than one name are
    refused as ambiguous; several shards of one call are an array output, in shard order.
    """
    entries = [e for node in metadata_nodes(meta) for e in call_entries(node)]
    tail = key.rsplit(".", 1)[-1]
    if spec:
        _, fqn, shard, outkey = spec
        found = [e for e in entries if e[0] == fqn and (shard is None or e[1] == shard)]
        if not found:
            raise Refusal("NO-PRODUCING-CALL",
                          f"--call names {fqn}{'' if shard is None else f':{shard}'}, which this "
                          "run's metadata does not contain", 2)
        return found, outkey or tail
    found = [e for e in entries if tail in (e[2].get("outputs") or {})]
    if len({e[0] for e in found}) > 1:
        raise Refusal("AMBIGUOUS-CALL",
                      f"{len({e[0] for e in found})} calls declare an output named {tail!r}: "
                      + ", ".join(sorted({e[0] for e in found})[:6])
                      + f"\n    name one with --call {key}=CALL_FQN[:SHARD][@OUTPUT]", 2)
    return found, tail


def resolve_cached(key: str, value, meta: dict, hits: HitIndex, spec=None) -> list:
    """One workflow output -> `[(uri, route, detail)]`, or a Refusal.

    A declared gs:// path is used as is: RESOLVED-CACHECOPY when it sits under cacheCopy/ or its
    producing call was a cache hit, else RESOLVED-DIRECT. With no declared path (or --call), each
    producing call's own outputs are used, and when a cache-hit call names nothing, the hit call's
    outputs are taken from its workflow's metadata (RESOLVED-VIA-HIT).
    """
    declared = gs_paths(value) if spec is None else []
    if declared:
        # Only a call whose outputs hold the declared path counts as its producer here.
        producers = [e for node in metadata_nodes(meta) for e in call_entries(node)
                     if set(declared) & set(gs_paths(e[2].get("outputs") or {}))]
        out = []
        for uri in declared:
            prod = next((e for e in producers if uri in gs_paths(e[2].get("outputs") or {})), None)
            hit = prod is not None and is_cache_hit(prod[2])
            if CACHE_MARK in uri or hit:
                via = f"call {prod[0]}:{prod[1]}" if prod else "path under " + CACHE_MARK
                out.append((uri, ROUTE_CACHECOPY,
                            f"{via}; a cacheCopy, bytes identical to the cache hit's"))
            else:
                out.append((uri, ROUTE_DIRECT, f"call {prod[0]}:{prod[1]}" if prod else "declared"))
        return out
    producers, outkey = producers_for(key, meta, spec)
    if not producers:
        bare = sorted(f"{f}:{sh}" for node in metadata_nodes(meta) for f, sh, e in call_entries(node)
                      if is_cache_hit(e) and not gs_paths(e.get("outputs") or {}))
        raise Refusal("NO-PRODUCING-CALL",
                      f"{key} declares no path and no call in this run's metadata outputs "
                      f"{outkey!r}; name the call with --call {key}=CALL_FQN[:SHARD][@OUTPUT]"
                      + (f"\n    cache-hit calls with no output paths: {', '.join(bare[:8])}"
                         if bare else ""), 4)
    out = []
    for fqn, shard, entry in producers:
        own = gs_paths((entry.get("outputs") or {}).get(outkey))
        if own:
            route = ROUTE_CACHECOPY if (is_cache_hit(entry) or any(CACHE_MARK in u for u in own)) \
                else ROUTE_DIRECT
            note = "; a cacheCopy, bytes identical to the cache hit's" if route == ROUTE_CACHECOPY else ""
            out += [(u, route, f"call {fqn}:{shard} outputs.{outkey}{note}") for u in own]
            continue
        if not is_cache_hit(entry):
            raise Refusal("NOT-A-CACHE-HIT",
                          f"call {fqn}:{shard} has no gs:// value for {outkey!r} and was not served "
                          "from the call cache, so there is no earlier call to resolve through", 4)
        wf, hit_fqn, hit_shard = parse_hit(fqn, shard, entry)
        node = hits.get(wf)
        match = [e for e in call_entries(node) if e[0] == hit_fqn and e[1] == hit_shard]
        if not match:
            raise Refusal("HIT-CALL-NOT-FOUND",
                          f"the hit workflow {wf} has no call {hit_fqn} shard {hit_shard} in its "
                          "metadata", 7)
        theirs = gs_paths((match[0][2].get("outputs") or {}).get(outkey))
        if not theirs:
            raise Refusal("HIT-OUTPUT-ABSENT",
                          f"the hit call {wf}:{hit_fqn}:{hit_shard} has no gs:// value for output "
                          f"{outkey!r}", 7)
        tag = f"{wf}:{hit_fqn}:{hit_shard}"
        out += [(u, f"{ROUTE_VIA_HIT} {tag}", f"call {fqn}:{shard} hit {tag} outputs.{outkey}")
                for u in theirs]
    return out


def values_of(key: str, value, status) -> list:
    """Declared value -> the list of gs:// objects it names ([] for a scalar output).

    ``status`` is the workflow status, included in the refusal for a null value.
    """
    if value is None:
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
    if bad and len(bad) == len(items):
        return []                     # a String / Array[String] output: values, not artifacts
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
    """Where one object would come from: (kind, path). Shared by --dry-run and the real fetch."""
    if dest.is_file():
        return ("already-there", str(dest))
    local = adopt(uri, link_dirs) if link_dirs else ""
    if local:
        return ("adopt", local)
    return ("download", "")


def fetch_cmd(uri: str, dest: Path) -> list:
    """The download command, shared by --dry-run and the fetch.

    ``-n`` (no-clobber) keeps a re-fetch from overwriting bytes an earlier manifest already hashed.
    """
    return ["gsutil", "-m", "cp", "-n", uri, str(dest)]


def download(uri: str, dest: Path, run=subprocess.run) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = fetch_cmd(uri, dest)
    p(f"  + {' '.join(cmd)}")
    try:
        r = run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        refuse(f"gsutil is not on PATH (install the Cloud CLI, docs/setup.md) and "
               f"{os.path.basename(uri)} is in no --link-dir, so {uri} cannot be fetched.\n"
               "  --dry-run prints the command without running it; --link-dir adopts a local "
               "capture of this object instead.", 6)
    if r.returncode != 0:
        refuse(f"gsutil cp failed (rc {r.returncode}) for {uri}\n"
               f"  {(r.stderr or r.stdout or '').strip()[:400]}\n"
               "  Nothing is recorded for an object that did not land.", 6)


def resolve_plan(a, meta: dict, outs: dict, call_specs: dict) -> tuple:
    """--resolve-cached: `(plan, values, routes)` for every requested output, or exit.

    Every output is resolved and its line printed before any refusal exits, so one run names all
    the problems. Nothing is fetched when any output is refused.
    """
    docs = [meta]
    for f in a.hit_metadata:
        path = Path(f).expanduser()
        try:
            doc = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            refuse(f"--hit-metadata {path}: cannot read it as JSON ({str(e)[:160]})", 5)
        if not isinstance(doc, dict):
            refuse(f"--hit-metadata {path} holds a {type(doc).__name__}, not workflow metadata", 5)
        docs.append(doc)
    online = None
    if a.online:
        ns = a.ns or config.require("TERRA_NAMESPACE", "the workspace to look the hit workflow up in")
        ws = a.ws or config.require("TERRA_WORKSPACE", "the workspace to look the hit workflow up in")
        online = online_lookup(ns, ws, a.hit_submission, a.max_submissions)
    hits = HitIndex(docs, online)

    keys = []
    for name in a.output:
        if name in call_specs and name not in outs and not any(
                k.rsplit(".", 1)[-1] == name for k in outs):
            keys.append(name)             # --call stands in for a key the root map never declared
        else:
            keys.append(resolve(name, outs)[0])   # exits 2/3 as without --resolve-cached
    if a.all:
        keys += sorted(outs)
    plan, values, routes, refused = [], {}, {}, []
    p(f"call cache: resolving {len(keys)} output(s); {UNVERIFIED_NOTE}")
    for key in dict.fromkeys(keys):
        value = outs.get(key)
        spec = call_specs.get(key) or call_specs.get(key.rsplit(".", 1)[-1])
        if spec is None and value is not None:
            uris = values_of(key, value, meta.get("status"))   # exits 4 for a non-gs:// list
            if not uris:
                values[key] = value
                p(f"  [value]  {key} = {value!r} (not an object; recorded, not fetched)")
                continue
        try:
            items = resolve_cached(key, value, meta, hits, spec)
        except Refusal as r:
            refused.append(r)
            p(f"  REFUSED-{r.name}  {key}: {r.msg}")
            continue
        for i, (uri, route, detail) in enumerate(items):
            k = key if len(items) == 1 else f"{key}[{i}]"
            plan.append((k, uri))
            routes[k] = route
            p(f"  {route}  {k}  {uri}\n           ({detail})")
    if refused:
        refuse(f"{len(refused)} output(s) could not be resolved through the call cache ("
               + ", ".join(sorted({r.name for r in refused})) + "); nothing was fetched", refused[0].code)
    return plan, values, routes


def main(argv: list, run=subprocess.run) -> int:
    """Run one fetch. ``run`` can be replaced with a recorder in tests."""
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
    ap.add_argument("--cache-report", action="store_true",
                    help="before fetching anything, count the declared outputs whose path is a Cromwell call-cache "
                         "copy (/cacheCopy/) and name them. A cached call did not run on this submission, so its "
                         "artifacts are evidence about the code that produced them earlier, not about this run")
    ap.add_argument("--refuse-cached", action="store_true",
                    help="--cache-report, and exit 7 if ANY resolved output is cache-served: for an A/B, one cached "
                         "arm is not a result, and finding that out after downloading 40 GB is a bad afternoon")
    rc = ap.add_argument_group(
        "call-cache resolution (UNVERIFIED until checked against a real cache-hit run)")
    rc.add_argument("--resolve-cached", action="store_true",
                    help="resolve each requested output through Cromwell's callCaching metadata and "
                         "label it RESOLVED-DIRECT, RESOLVED-CACHECOPY or RESOLVED-VIA-HIT "
                         "<wf>:<call>:<shard>; any output that cannot be resolved is a named "
                         "refusal and nothing is fetched. The manifest records each file's route")
    rc.add_argument("--hit-metadata", action="append", default=[], metavar="FILE",
                    help="saved metadata of a workflow a cached call hit (repeatable); searched "
                         "before Terra")
    rc.add_argument("--call", action="append", default=[], metavar="NAME=FQN[:SHARD][@OUTPUT]",
                    help="the call that produced workflow output NAME, when no path or same-named "
                         "call output identifies it")
    rc.add_argument("--online", action="store_true",
                    help="look up a hit workflow missing from local metadata in Terra (read-only "
                         "GETs; needs firecloud and credentials)")
    rc.add_argument("--hit-submission", metavar="ID",
                    help="with --online: the submission holding the hit workflow, instead of "
                         "searching for it")
    rc.add_argument("--max-submissions", type=int, default=50, metavar="N",
                    help="with --online: newest submissions to search for the hit workflow "
                         "(default 50)")
    rc.add_argument("--ns", help="with --online: workspace namespace (default GSVTK_TERRA_NAMESPACE)")
    rc.add_argument("--ws", help="with --online: workspace name (default GSVTK_TERRA_WORKSPACE)")
    ap.add_argument("--verify", action="store_true",
                    help="do not fetch: re-hash what the manifest at --manifest recorded and report "
                         "drift, missing files and unreadable records")
    a = ap.parse_args(argv)

    resolve_only = [f for f, on in (("--hit-metadata", a.hit_metadata), ("--call", a.call),
                                     ("--online", a.online), ("--hit-submission", a.hit_submission),
                                     ("--ns", a.ns), ("--ws", a.ws)) if on]
    if resolve_only and not a.resolve_cached:
        refuse(f"{', '.join(resolve_only)} only apply with --resolve-cached", 2)
    if a.resolve_cached and a.refuse_cached:
        refuse("--resolve-cached and --refuse-cached together is a contradiction: one resolves "
               "cache-served outputs, the other refuses them", 2)
    if a.resolve_cached and a.verify:
        refuse("--verify re-hashes a written manifest and resolves nothing; drop --resolve-cached", 2)
    if a.hit_submission and not a.online:
        refuse("--hit-submission is a Terra lookup; it needs --online", 2)
    if a.max_submissions < 1:
        refuse(f"--max-submissions {a.max_submissions}: searching no submissions finds nothing", 2)
    call_specs = {}
    for spec in a.call:
        parsed = parse_call_spec(spec)
        if parsed[0] in call_specs:
            refuse(f"--call names {parsed[0]!r} twice", 2)
        call_specs[parsed[0]] = parsed

    manifest_path = (Path(a.manifest).expanduser() if a.manifest
                     else config.work_path("manifests") / "outputs_manifest.json")

    # --verify: re-hash what the manifest recorded.
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

    # Resolve everything, then fetch, then write.
    src = Path(a.metadata).expanduser() if a.metadata else default_metadata_file()
    meta = load_metadata(src)
    outs = outputs_map(meta, src)

    if a.cache_report or a.refuse_cached:
        cached, fresh = cache_report(outs)
        asked = bool(a.all or a.output)
        print(f"call cache: {len(cached)} of {len(cached) + len(fresh)} declared output(s) resolve to a path under "
              f"{CACHE_MARK}, {len(fresh)} to a path this run wrote")
        for key in cached[:12]:
            print(f"  CACHED  {key}")
        if len(cached) > 12:
            print(f"  ... ({len(cached) - 12} more)")
        if a.refuse_cached and cached:
            refuse(f"{len(cached)} output(s) resolve into a call-cache copy, so this submission did not produce "
                   f"them.\n  An arm served from cache is not a re-run: name the workflow id you meant, or re-submit "
                   f"with call caching off (the launcher default is off; see CALL_CACHE_FLAG in batch_configs.py).", 7)
        if not asked:
            # A cache check with nothing to fetch is a complete pre-flight, not a usage error.
            print("  (asked for no artifact, so nothing was fetched -- this was the check)" if not cached
                  else "  (asked for no artifact: the count above is the finding)")
            return 0

    if a.all and a.output:
        refuse("--all and --output together is a contradiction: say which", 2)
    if not a.all and not a.output:
        # No implicit bulk fetch: a chain's outputs can be tens of GB.
        refuse(f"say what to fetch -- {len(outs)} output(s) are declared in {src.name} and none was "
               "requested.\n  " + "\n  ".join(sorted(outs)[:25])
               + (f"\n  ... ({len(outs) - 25} more)" if len(outs) > 25 else "")
               + "\n  --output NAME (repeatable) for the ones you need, or --all for every gs:// "
                 "object this run\n  produced -- size that download before asking for it.", 2)

    plan, values, routes = [], {}, {}
    run_status = meta.get("status")
    if a.resolve_cached:
        plan, values, routes = resolve_plan(a, meta, outs, call_specs)
    for name in ([] if a.resolve_cached else list(a.output)):
        key, value = resolve(name, outs)          # exits 3, naming `name`, if it is not declared
        uris = values_of(key, value, run_status)      # exits 4 for null / non-object shapes
        if not uris:
            values[key] = value                   # a scalar output: recorded, never fetched
            p(f"  [value]  {key} = {value!r} (not an object; recorded in the manifest, not fetched)")
            continue
        for i, uri in enumerate(uris):
            plan.append((key if len(uris) == 1 else f"{key}[{i}]", uri))
    if a.all and not a.resolve_cached:
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
        p(f"  plan     {key}  {uri}" + (f"  [{routes[key]}]" if key in routes else ""))
    if a.dry_run:
        # Print the exact command each object would be fetched with.
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
                    # Hardlink to avoid copying multi-GB files; both paths are then the same file.
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
        # One hash pass per object, used for the record and the drift check below.
        digest = sha256_of(dest)
        if present:
            # Compare against the previous manifest: match, drift, or no previous record.
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
        if key in routes:
            files[key]["route"] = routes[key]
        p(f"  [ok]     {key}  {files[key]['bytes']} bytes  sha256={digest}")
        p(f"           via {source}")
    if missing:
        refuse(f"{len(missing)} object(s) did not land:\n  " + "\n  ".join(missing)
               + "\n  No manifest is written for a partial fetch.", 6)

    if not files and not values:
        refuse("nothing was recorded; an empty manifest is not a result", 3)

    # Append to the previous manifest; records for keys resolved in this run replace the old ones.
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
    if routes:
        doc["cache_resolution"] = UNVERIFIED_NOTE
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
