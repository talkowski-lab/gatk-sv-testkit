#!/usr/bin/env python3
"""Rerun ONE finished WDL task's rendered script inside an image, on that run's real inputs.

A Cromwell task's `script` file is the command exactly as it ran, with every File input already
rendered as a localized path (`/cromwell_root/<bucket>/<object>`, or `/mnt/disks/cromwell_root/...`
on the GCP Batch backend). This tool:

  1. reads that script and the call's metadata record (offline from saved files, or read-only from
     Terra through terra/batch_peek.py's helpers);
  2. works out which `gs://` objects must sit at which localized paths, looks up their sizes, and
     refuses past `--max-gb`;
  3. assembles three pieces for checks/image-check/run_in_image.sh: a host-side staging script
     (curl + the VM's metadata token, so the image needs no gsutil), the probe that runs the
     script inside `--image` with the localized root bind-mounted, and a host-side script that
     prints each output's size and sha256;
  4. runs it: by default as `run_in_image.sh --dry-run` (free, no project needed), and only with
     `--confirm` on a real VM.

Two `--image` values make two arms over the same inputs. Without `--pair` the arms are compared
(rc and output hashes). With `--pair` the SIGPIPE-style condition from checks/sigpipe_pair.sh
applies: arm A (unguarded) must FAIL and arm B (guarded) must pass, otherwise the pair proves
nothing and says so.

    # offline: a saved script plus its call record (what --save-dir writes)
    python replay/task_replay.py --script saved/script --call-json saved/call.json --image REF
    # a local metadata export (batch_save_metadata.py); the script is read from the call dir
    python replay/task_replay.py --metadata run.json --task GatherBatchEvidence --shard 0 --image REF
    # live, read-only metadata read
    python replay/task_replay.py --ns NS --ws WS --submission SUB --workflow WF --task T --image REF
    # image from a docker/image_capture.py capture
    python replay/task_replay.py --script s --call-json c.json --image-from cap.json --image-key sv_pipeline_docker
    # the two-arm guard proof, then the real run
    python replay/task_replay.py --script s --call-json c.json --image A --image B --pair --confirm

Exit codes: 0 ok (plan printed, or the run passed) · 1 the task failed, the arms differ, or the pair
is VACUOUS · 2 usage or a refusal (no image, over --max-gb, read-only, sizes unknown under --confirm)
· 3 a missing dependency · 4 missing config · 5 NOT SCORED (run_in_image.sh measured nothing).
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "kit"))
import config  # noqa: E402

RUN_IN_IMAGE = ROOT / "checks" / "image-check" / "run_in_image.sh"
PRICES = ROOT / "prices" / "us-central1.json"

# Localized-input roots: PAPI v2 and the GCP Batch backend.
CROMWELL_ROOTS = ("/mnt/disks/cromwell_root", "/cromwell_root")
_BUCKET = r"[a-z0-9][a-z0-9._-]{1,221}"
_PATHCH = r"[^\s\"'`;|&<>()$]+"
LOCAL_RE = re.compile(r"(/mnt/disks/cromwell_root|/cromwell_root)/(" + _BUCKET + r")/(" + _PATHCH + r")")
GS_RE = re.compile(r"gs://(" + _BUCKET + r")/(" + _PATHCH + r")")
# Cromwell's own bookkeeping files in the root; never reported as task outputs.
BOOKKEEPING = ("script", "rc", "rc.tmp", "stdout", "stderr", ".replay_start")

DEFAULT_MAX_GB = 50.0
PD_SSD_GB_MONTH = 0.17          # us-central1 pd-ssd list price, $/GB-month
HOURS_PER_MONTH = 730.0
EXIT_NOT_SCORED = 5


# ----------------------------------------------------------------------------------- small helpers
def fmt_bytes(n) -> str:
    if n is None:
        return "UNKNOWN"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n} B"


def read_only_on() -> bool:
    v = os.environ.get("GSVTK_READ_ONLY", "")
    return bool(v) and v != "0"


def refuse(msg: str, code: int = 2):
    print(f"REFUSED: {msg}", file=sys.stderr)
    raise SystemExit(code)


def gs_values(obj) -> list:
    """Every gs:// string anywhere in a metadata value (lists, structs, pairs), in order, unique."""
    out = []

    def walk(v):
        if isinstance(v, str):
            if v.startswith("gs://") and not v.endswith("/") and v not in out:
                out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
    walk(obj)
    return out


def split_gs(uri: str):
    rest = uri[len("gs://"):]
    bucket, _, obj = rest.partition("/")
    return bucket, obj


def detect_root(script: str) -> str:
    """The localization root this script was rendered against (the Batch root wins a tie)."""
    batch = script.count(CROMWELL_ROOTS[0])
    papi = script.count(CROMWELL_ROOTS[1]) - batch
    return CROMWELL_ROOTS[0] if batch and batch >= papi else CROMWELL_ROOTS[1]


# ------------------------------------------------------------------------------- localization plan
def plan_localization(script: str, inputs=None, rewrite_gs: bool = False) -> dict:
    """Which gs:// object goes to which path, and the script to run.

    Candidates are (a) every localized-looking path the script names (`<root>/<bucket>/<obj>`) and
    (b) every gs:// value among the call's declared inputs. A declared gs:// value whose literal
    gs:// form appears in the script was a String, not a localized File, so it is not staged unless
    `rewrite_gs`. With `rewrite_gs`, every gs:// literal in the script is rewritten to its localized
    path and staged. The size lookup later decides which candidates exist.
    """
    root = detect_root(script)
    stage = {}          # local path -> {"uri", "path", "why"}

    def add(uri, why):
        b, o = split_gs(uri)
        path = f"{root}/{b}/{o}"
        stage.setdefault(path, {"uri": uri, "path": path, "why": why})

    for m in LOCAL_RE.finditer(script):
        if m.group(1) != root and root == CROMWELL_ROOTS[0]:
            continue    # `/cromwell_root` inside `/mnt/disks/cromwell_root` is the same match
        add(f"gs://{m.group(2)}/{m.group(3)}", "named in the script")
    literals = sorted({m.group(0) for m in GS_RE.finditer(script)})
    for uri in gs_values(inputs or {}):
        if uri in literals and not rewrite_gs:
            continue
        add(uri, "declared input")
    out_script = script
    left = []
    if rewrite_gs:
        for uri in literals:
            add(uri, "gs:// literal, rewritten")
        out_script = GS_RE.sub(lambda m: f"{root}/{m.group(1)}/{m.group(2)}", script)
    else:
        left = literals
    return {"root": root, "stage": list(stage.values()), "script": out_script, "gs_left": left}


# --------------------------------------------------------------------------------------- sizes
def parse_ls_l(text: str) -> dict:
    """`gsutil ls -l` lines -> {uri: bytes}. The TOTAL line and errors are ignored."""
    out = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0].isdigit() and parts[-1].startswith("gs://"):
            out[parts[-1]] = int(parts[0])
    return out


def lookup_sizes(uris, sizes_file=None, run=subprocess.run) -> tuple:
    """({uri: bytes or None}, how). None = absent or unknown; `how` says which.

    `sizes_file` (JSON {uri: bytes}) makes this offline. Otherwise `gsutil ls -l`, read-only.
    """
    uris = list(uris)
    if sizes_file:
        with open(sizes_file) as fh:
            known = json.load(fh)
        return {u: (int(known[u]) if u in known else None) for u in uris}, f"--sizes {sizes_file}"
    if not uris:
        return {}, "nothing to look up"
    if not shutil.which("gsutil") and run is subprocess.run:
        return {u: None for u in uris}, "UNKNOWN (gsutil is not on PATH; pass --sizes FILE)"
    found = {}
    for i in range(0, len(uris), 100):
        chunk = uris[i:i + 100]
        try:
            proc = run(["gsutil", "ls", "-l", *chunk], capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {u: None for u in uris}, f"UNKNOWN (gsutil ls -l failed: {exc})"
        found.update(parse_ls_l(proc.stdout or ""))
    return {u: found.get(u) for u in uris}, "gsutil ls -l (read-only)"


# ---------------------------------------------------------------------------- generated scripts
HOST_FETCH_FN = r'''# Runs on the VM HOST. curl + the instance's metadata token: no gsutil, no ADC.
set -uo pipefail
USER_PROJECT="__USER_PROJECT__"
token() {
  curl -s -H "Metadata-Flavor: Google" \
    http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token \
    | jq -r .access_token
}
fetch() {   # fetch URI DEST EXPECTED_BYTES
  local uri="$1" dest="$2" want="$3" rest bucket obj enc url got
  rest="${uri#gs://}"; bucket="${rest%%/*}"; obj="${rest#*/}"
  enc=$(jq -rn --arg o "$obj" '$o|@uri')
  url="https://storage.googleapis.com/storage/v1/b/${bucket}/o/${enc}?alt=media"
  [ -n "$USER_PROJECT" ] && url="${url}&userProject=${USER_PROJECT}"
  mkdir -p "$(dirname "$dest")"
  if ! curl -sfL --retry 3 -H "Authorization: Bearer $(token)" -o "$dest" "$url"; then
    echo "STAGE FAILED: $uri -> $dest"; return 1
  fi
  got=$(stat -c %s "$dest")
  if [ -n "$want" ] && [ "$got" != "$want" ]; then
    echo "STAGE SIZE MISMATCH: $uri -> $dest is $got bytes, the plan expected $want"; return 1
  fi
  echo "staged $uri -> $dest ($got bytes)"
}
'''


def host_pre_script(stage, sizes, user_project: str = "", extra: str = "") -> str:
    lines = [HOST_FETCH_FN.replace("__USER_PROJECT__", user_project), "fails=0"]
    for s in stage:
        want = sizes.get(s["uri"])
        lines.append(f"fetch '{s['uri']}' '{s['path']}' '{'' if want is None else want}' || fails=$((fails+1))")
    if extra:
        lines.append(extra)
    lines.append('df -h / | tail -1 | sed "s/^/disk after staging: /"')
    lines.append('[ "$fails" -eq 0 ] || { echo "$fails object(s) failed to stage"; exit 1; }')
    lines.append(f'echo "staged {len(stage)} object(s)"')
    return "\n".join(lines) + "\n"


def _heredoc_tag(text: str) -> str:
    tag = "GSVTK_REPLAY_EOF"
    while tag in text:
        tag += "_X"
    return tag


def task_probe(script: str, root: str) -> str:
    """The probe run inside the image: write the script to <root>/script, run it, report its rc.

    Cromwell's wrapper records the command's status in <root>/rc and usually exits 0 itself, so the
    rc file is the answer when it exists. stdin is /dev/null: the probe itself arrives on stdin.
    """
    tag = _heredoc_tag(script)
    return (f"set -u\nROOT='{root}'\nmkdir -p \"$ROOT\" && cd \"$ROOT\" || exit 97\n"
            f"rm -f \"$ROOT/rc\" \"$ROOT/rc.tmp\"\n"
            f"cat > \"$ROOT/script\" <<'{tag}'\n{script.rstrip(chr(10))}\n{tag}\n"
            "touch \"$ROOT/.replay_start\"\n"
            "bash \"$ROOT/script\" </dev/null\nSRC=$?\n"
            "RCF=$(tr -d ' \\n' < \"$ROOT/rc\" 2>/dev/null)\n"
            "case \"$RCF\" in\n"
            "  ''|*[!0-9]*) RC=$SRC; echo \"REPLAY_TASK_RC=$RC (no usable rc file: the wrapper's own status)\" ;;\n"
            "  *) RC=$RCF; echo \"REPLAY_TASK_RC=$RC (from $ROOT/rc; the wrapper returned $SRC)\" ;;\n"
            "esac\n"
            "echo '--- stderr tail'\ntail -n 20 \"$ROOT/stderr\" 2>/dev/null\n"
            "exit \"$RC\"\n")


def host_post_script(root: str, named=()) -> str:
    """Runs on the host after the probe: size + sha256 of every file the run wrote under root."""
    excl = " ".join(f"! -name '{b}'" for b in BOOKKEEPING)
    lines = [f"ROOT='{root}'", "echo \"task rc as seen by the host: ${RUN_IN_IMAGE_RC:-?}\"",
             '[ -f "$ROOT/.replay_start" ] || { echo "REPLAY_NO_START_MARKER"; exit 1; }',
             f'find "$ROOT" -type f -newer "$ROOT/.replay_start" {excl} | sort | head -n 300 |',
             '  while IFS= read -r f; do',
             '    echo "REPLAY_OUT $(sha256sum "$f" | cut -d" " -f1) $(stat -c %s "$f") ${f#$ROOT/}"',
             '  done']
    for rel in named:
        lines.append(f"[ -f \"$ROOT/{rel}\" ] || echo 'REPLAY_MISSING {rel}'")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------- VM sizing/cost
MACHINES = [("e2-standard-2", 2, 8), ("e2-standard-4", 4, 16), ("e2-standard-8", 8, 32),
            ("e2-standard-16", 16, 64), ("e2-standard-32", 32, 128), ("n2-highmem-32", 32, 256),
            ("n2-highmem-64", 64, 512)]


def mem_gb(text) -> float | None:
    m = re.match(r"\s*([0-9.]+)\s*([KMGT]i?B?)?", str(text or ""), re.I)
    if not m:
        return None
    unit = (m.group(2) or "G").upper()[0]
    return float(m.group(1)) * {"K": 1e-6, "M": 1e-3, "G": 1.0, "T": 1e3}[unit]


def pick_machine(runtime: dict) -> str:
    cpu = int(float((runtime or {}).get("cpu") or 0) or 0)
    mem = mem_gb((runtime or {}).get("memory")) or 0
    for name, n, gb in MACHINES:
        if n >= cpu and gb >= mem:
            return name
    return MACHINES[-1][0]


def machine_vcpus(machine: str) -> int | None:
    small = {"e2-micro": 2, "e2-small": 2, "e2-medium": 2}
    if machine in small:
        return small[machine]
    m = re.search(r"-(\d+)$", machine)
    return int(m.group(1)) if m else None


def runtime_disk_gb(runtime: dict) -> int:
    m = re.search(r"local-disk\s+(\d+)", str((runtime or {}).get("disks") or ""))
    return int(m.group(1)) if m else 0


def call_hours(rec: dict) -> float | None:
    try:
        a = datetime.datetime.fromisoformat(str(rec["start"]).replace("Z", "+00:00"))
        b = datetime.datetime.fromisoformat(str(rec["end"]).replace("Z", "+00:00"))
        return max((b - a).total_seconds() / 3600.0, 0.0)
    except (KeyError, ValueError, TypeError):
        return None


def cost_estimate(machine: str, disk_gb: int, hours: float) -> tuple:
    """(dollars or None, the assumption line). Same rate file the cost tools read."""
    try:
        rate = json.loads(PRICES.read_text())["vcpu_hour"]
    except (OSError, ValueError, KeyError):
        return None, f"no rate ({PRICES} unreadable)"
    n = machine_vcpus(machine)
    if n is None:
        return None, f"no vCPU count for machine type {machine}"
    vm = n * rate * hours
    disk = disk_gb * PD_SSD_GB_MONTH / HOURS_PER_MONTH * hours
    return vm + disk, (f"{n} vCPU x ${rate}/vCPU-h ({PRICES.name}, an n1 bundled rate) + "
                       f"{disk_gb} GB pd-ssd x ${PD_SSD_GB_MONTH}/GB-month, for {hours:.2f} h; "
                       "GCS reads in the same region are free, cross-region egress is not counted")


# --------------------------------------------------------------------------------- image selection
def image_from_capture(path: str, key: str = "") -> str:
    try:
        cap = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as exc:
        refuse(f"--image-from {path}: unreadable ({type(exc).__name__}: {exc})")
    if not isinstance(cap, dict) or cap.get("kind") != "gsvtk-image-capture":
        refuse(f"--image-from {path}: not a docker/image_capture.py capture (kind != gsvtk-image-capture)")
    images = cap.get("images") or {}
    if key:
        hits = [k for k in images if k == key or k.endswith("." + key)]
    else:
        hits = list(images)
    if len(hits) != 1:
        refuse(f"--image-from {path}: {'--image-key ' + key + ' matches' if key else 'no --image-key, and the capture has'} "
               f"{len(hits)} entr{'y' if len(hits) == 1 else 'ies'} ({', '.join(sorted(images)) or 'none'}); "
               "name exactly one with --image-key")
    ref = str((images[hits[0]] or {}).get("ref") or "")
    if "@sha256:" not in ref:
        refuse(f"--image-from {path}: images[{hits[0]!r}].ref is not a digest ref: {ref!r}")
    return ref


def resolve_images(a) -> list:
    imgs = list(a.image or [])
    if a.image_from:
        imgs.append(image_from_capture(a.image_from, a.image_key or ""))
    if not imgs:
        refuse("no --image given: name the image to run the task in (an explicit ref, or "
               "--image-from <docker/image_capture.py capture> --image-key KEY). An unnamed image "
               "cannot be evidence.")
    if len(imgs) > 2:
        refuse(f"{len(imgs)} images given; one arm, or two (A and B), nothing else")
    if getattr(a, "pair", False) and len(imgs) != 2:
        refuse("--pair needs exactly two images: --image <unguarded A> --image <guarded B>")
    return imgs


# --------------------------------------------------------------------------------- run_in_image
def rii_argv(image, probe, host_pre, host_post, mounts, machine, disk_gb, timeout_min,
             dry_run: bool, project: str = "", zone: str = "", max_lines: int = 80) -> list:
    argv = ["bash", str(RUN_IN_IMAGE), "--image", image, "--probe", str(probe),
            "--machine", machine, "--disk", str(disk_gb), "--timeout", str(timeout_min),
            "--max-lines", str(max_lines)]
    for m in mounts:
        argv += ["--mount", m]
    if host_pre:
        argv += ["--host-pre", str(host_pre)]
    if host_post:
        argv += ["--host-post", str(host_post)]
    if project:
        argv += ["--project", project]
    if zone:
        argv += ["--zone", zone]
    if dry_run:
        argv.append("--dry-run")
    return argv


def show_dry_run(text: str, full: bool) -> None:
    """Print run_in_image's dry-run, eliding the assembled startup script unless `full`."""
    skipping, skipped = False, 0
    for line in text.splitlines():
        if line.startswith("===== assembled startup script"):
            skipping = not full
            print(line)
            continue
        if line.startswith("===== end of startup script"):
            if skipped:
                print(f"    ({skipped} lines of startup script omitted; --show-startup prints them)")
            skipping = False
        if skipping:
            skipped += 1
            continue
        print(line)


OUT_RE = re.compile(r"REPLAY_OUT ([0-9a-f]{64}) (\d+) (.+?)\s*$")
MISSING_RE = re.compile(r"REPLAY_MISSING (\S+)")
POST_RC_RE = re.compile(r"HOST_POST_RC=(\d+)")


def parse_run_output(text: str) -> dict:
    outs = {}
    for line in text.splitlines():
        m = OUT_RE.search(line)
        if m:
            outs[m.group(3)] = {"sha256": m.group(1), "bytes": int(m.group(2))}
    post = POST_RC_RE.findall(text)
    return {"outputs": outs, "missing": sorted(set(MISSING_RE.findall(text))),
            "host_post_rc": int(post[-1]) if post else None}


def run_arm(argv, run=subprocess.run) -> tuple:
    """(rc, combined output) of one run_in_image.sh call, echoed as it is returned."""
    proc = run(argv, capture_output=True, text=True)
    text = (proc.stdout or "") + (proc.stderr or "")
    print(text, end="" if text.endswith("\n") else "\n")
    return proc.returncode, text


# ------------------------------------------------------------------------------- source reading
def load_call(a) -> tuple:
    """(script_text, call_record, source description). Raises SystemExit with a reason."""
    rec = {}
    source = []
    if a.call_json:
        try:
            rec = json.loads(pathlib.Path(a.call_json).read_text())
        except (OSError, ValueError) as exc:
            refuse(f"--call-json {a.call_json}: {type(exc).__name__}: {exc}")
        source.append(f"call record {a.call_json}")
    elif a.metadata or a.ns:
        if not a.task:
            refuse("--task is required with --metadata or --ns/--ws/--submission/--workflow")
        if a.metadata:
            try:
                meta = json.loads(pathlib.Path(a.metadata).read_text())
            except (OSError, ValueError) as exc:
                refuse(f"--metadata {a.metadata}: {type(exc).__name__}: {exc}")
            source.append(f"metadata {a.metadata}")
        else:
            if not (a.ws and a.submission and a.workflow):
                refuse("a live read needs all four of --ns --ws --submission --workflow")
            bp = _batch_peek()
            try:
                meta = bp.fetch(a.ns, a.ws, a.submission, a.workflow)
            except Exception as exc:     # terra.TerraError and transport errors
                print(f"cannot read workflow {a.workflow[:8]}: {exc}", file=sys.stderr)
                raise SystemExit(1)
            source.append(f"live Terra metadata {a.ns}/{a.ws} {a.workflow[:8]} (read-only)")
        rec = select_record(meta, a.task, a.shard, a.attempt)
    if a.script:
        try:
            script = pathlib.Path(a.script).read_text()
        except OSError as exc:
            refuse(f"--script {a.script}: {exc}")
        source.insert(0, f"script {a.script}")
    elif rec:
        bp = _batch_peek()
        ta = bp.task_artifacts(rec, attempt=a.attempt)
        res = ta["files"].get("script") or {}
        if res.get("error") or not res.get("text"):
            print(f"cannot read the rendered script from the call dir ({res.get('error') or 'empty'}).\n"
                  "  Copy it yourself and pass --script.", file=sys.stderr)
            raise SystemExit(1)
        script = res["text"]
        source.insert(0, f"script {res.get('uri')}")
    else:
        refuse("no task to replay: pass --script FILE (with --call-json), or --metadata FILE --task T, "
               "or --ns/--ws/--submission/--workflow --task T")
    if not script.strip():
        refuse("the rendered script is empty: there is nothing to replay")
    return script, rec, "; ".join(source)


def _batch_peek():
    sys.path.insert(0, str(ROOT / "terra"))
    try:
        import batch_peek
    except ImportError as exc:
        print(f"terra/batch_peek.py could not be imported ({exc}); `make setup` installs its "
              "dependencies", file=sys.stderr)
        raise SystemExit(3)
    return batch_peek


def select_record(meta: dict, task: str, shard=None, attempt=None) -> dict:
    calls = (meta or {}).get("calls") or {}
    name = task if task in calls else next((k for k in calls if k.split(".")[-1] == task), None)
    if not name:
        print(f"task {task}: not in this metadata ({len(calls)} call name(s): "
              f"{', '.join(sorted(calls)[:6])})", file=sys.stderr)
        raise SystemExit(1)
    cand = [r for r in calls[name] if shard is None or int(r.get("shardIndex") or 0) == shard]
    if attempt is not None:
        cand = [r for r in cand if int(r.get("attempt") or 1) == attempt]
    if not cand:
        print(f"task {name}: no record for shard={shard} attempt={attempt}", file=sys.stderr)
        raise SystemExit(1)
    return max(cand, key=lambda r: int(r.get("attempt") or 1))


def named_outputs(rec: dict, root: str) -> list:
    """Paths (relative to root) where the call's declared outputs land."""
    outs = gs_values(rec.get("outputs") or {})
    cdir = ""
    for key in ("callRoot", "call_root"):
        if rec.get(key):
            cdir = str(rec[key]).rstrip("/") + "/"
    rels = []
    for uri in outs:
        rel = uri[len(cdir):] if cdir and uri.startswith(cdir) else uri.rsplit("/", 1)[-1]
        if rel and rel not in rels:
            rels.append(rel)
    return rels


# ------------------------------------------------------------------------------------- main
def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Free by default: prints the plan and run_in_image.sh --dry-run. "
                                        "--confirm boots one VM per arm.")
    src = ap.add_argument_group("which task")
    src.add_argument("--script", help="the task's saved rendered `script` file (offline)")
    src.add_argument("--call-json", help="the call's metadata record as JSON (offline; --save-dir writes one)")
    src.add_argument("--metadata", help="a local Cromwell/Terra metadata export (batch_save_metadata.py)")
    src.add_argument("--ns")
    src.add_argument("--ws")
    src.add_argument("--submission")
    src.add_argument("--workflow")
    src.add_argument("--task", help="call name in the metadata (with or without the workflow prefix)")
    src.add_argument("--shard", type=int)
    src.add_argument("--attempt", type=int)
    src.add_argument("--save-dir", help="also save the script and call record here, for offline reruns")
    img = ap.add_argument_group("image")
    img.add_argument("--image", action="append", help="image ref; give twice for arms A and B")
    img.add_argument("--image-from", help="a docker/image_capture.py capture file")
    img.add_argument("--image-key", help="which capture entry (e.g. sv_pipeline_docker)")
    img.add_argument("--pair", action="store_true",
                     help="two arms as a guard proof: A (unguarded) must fail, B (guarded) must pass")
    loc = ap.add_argument_group("inputs")
    loc.add_argument("--rewrite-gs", action="store_true",
                     help="also localize gs:// literals in the script and rewrite them to local paths")
    loc.add_argument("--sizes", help="JSON {gs_uri: bytes}: size lookup with no network")
    loc.add_argument("--max-gb", type=float, default=DEFAULT_MAX_GB,
                     help=f"refuse to stage more than this (default {DEFAULT_MAX_GB:g})")
    loc.add_argument("--billing-project", default="",
                     help="userProject for requester-pays buckets")
    vm = ap.add_argument_group("VM")
    vm.add_argument("--machine", help="machine type (default: sized from the call's runtime attributes)")
    vm.add_argument("--disk", type=int, help="boot disk GB (default: sized from inputs and runtime)")
    vm.add_argument("--timeout", type=int, help="poll ceiling in minutes (default: from the call's duration)")
    vm.add_argument("--hours", type=float, help="runtime used by the cost estimate")
    vm.add_argument("--project", default="")
    vm.add_argument("--zone", default="")
    ap.add_argument("--show-startup", action="store_true", help="print the full assembled startup script")
    ap.add_argument("--work-dir", help="where the probe and host scripts are written (default: a temp dir "
                                       "for a plan, `gsvtk-config work task-replay/<stamp>` for --confirm)")
    ap.add_argument("--confirm", action="store_true", help="boot the VM(s) and run")
    return ap


def main(argv=None, run=subprocess.run) -> int:
    a = build_parser().parse_args(argv)
    if a.confirm and read_only_on():
        refuse("--confirm boots a VM, and GSVTK_READ_ONLY is set. Nothing was read or created; "
               "drop GSVTK_READ_ONLY to run it, or omit --confirm for the free plan.")
    images = resolve_images(a)
    script, rec, source = load_call(a)
    if a.save_dir:
        d = pathlib.Path(a.save_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "script").write_text(script)
        (d / "call.json").write_text(json.dumps(rec, indent=1, sort_keys=True))
        print(f"saved {d / 'script'} and {d / 'call.json'} (rerun offline with --script/--call-json)")

    loc = plan_localization(script, rec.get("inputs"), rewrite_gs=a.rewrite_gs)
    root = loc["root"]
    sizes, how = lookup_sizes([s["uri"] for s in loc["stage"]], a.sizes, run=run)
    present = [s for s in loc["stage"] if sizes.get(s["uri"]) is not None]
    unknown_all = how.startswith("UNKNOWN")
    if unknown_all:
        stage = loc["stage"]
    else:
        stage = present
    absent = [] if unknown_all else [s for s in loc["stage"] if sizes.get(s["uri"]) is None]
    total = None if unknown_all else sum(sizes[s["uri"]] for s in stage)
    named = named_outputs(rec, root)

    runtime = rec.get("runtimeAttributes") or {}
    machine = a.machine or pick_machine(runtime)
    gb = (total or 0) / 1e9
    disk = a.disk or max(60, runtime_disk_gb(runtime) + 40, int(math.ceil(gb * 2 + 40)))
    took = call_hours(rec)
    hours = a.hours or ((took + 0.25) if took is not None else 1.0)
    timeout = a.timeout or max(30, int(math.ceil(hours * 2 * 60)))
    cost, basis = cost_estimate(machine, disk, hours)

    print(f"== task replay plan ({'--confirm: WILL RUN' if a.confirm else 'free: nothing is created'})")
    print(f"   source   : {source}")
    print(f"   task     : {a.task or '(from --script)'}"
          f"  shard={rec.get('shardIndex', '-')} attempt={rec.get('attempt', '-')}")
    print(f"   root     : {root} (bind-mounted at the same path in the container)")
    for label, ref in zip("AB", images):
        print(f"   image {label}  : {ref}")
    if a.pair:
        print("   pair     : A must FAIL (unguarded) and B must PASS (guarded); otherwise VACUOUS")
    print(f"   sizes    : {how}")
    print(f"   inputs   : {len(stage)} object(s) to stage, total {fmt_bytes(total)} (limit {a.max_gb:g} GB)")
    for s in stage:
        print(f"     {fmt_bytes(sizes.get(s['uri'])):>10}  {s['uri']}\n                 -> {s['path']}  ({s['why']})")
    for s in absent:
        print(f"     not found, NOT staged: {s['uri']} ({s['why']})")
    for g in loc["gs_left"]:
        print(f"     left as gs:// in the script (read by the container itself, if it can): {g}")
    if loc["gs_left"]:
        print("     (--rewrite-gs localizes and rewrites those instead)")
    if named:
        print(f"   outputs  : {len(named)} declared, each reported with size + sha256: {', '.join(named[:8])}"
              f"{' ...' if len(named) > 8 else ''}")
    print(f"   VM       : {machine}, {disk} GB pd-ssd, poll ceiling {timeout} min, one per arm")
    print(f"   cost     : {'~$%.2f per arm' % cost if cost is not None else 'not estimated'} ({basis})")
    print("   undo     : run_in_image.sh deletes each VM after reading its verdict; on any failure it "
          "prints the delete command and keeps the VM so the log can be read")

    if total is not None and gb > a.max_gb:
        refuse(f"the inputs total {fmt_bytes(total)} ({gb:.1f} GB), over --max-gb {a.max_gb:g}. "
               "Raise --max-gb if you mean it; nothing was created.")
    if a.confirm and unknown_all and stage:
        refuse(f"input sizes are {how}, so --max-gb cannot be enforced. Pass --sizes, or put gsutil "
               "on PATH; nothing was created.")

    if a.work_dir:
        wd = pathlib.Path(a.work_dir)
        wd.mkdir(parents=True, exist_ok=True)
        cleanup = None
    elif a.confirm:
        wd = config.work_dir("task-replay", datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        cleanup = None
    else:
        wd = pathlib.Path(tempfile.mkdtemp(prefix="task-replay-"))
        cleanup = wd
    try:
        probe = wd / "probe.sh"
        probe.write_text(task_probe(loc["script"], root))
        pre = wd / "host_pre.sh"
        pre.write_text(host_pre_script(stage, sizes, a.billing_project))
        post = wd / "host_post.sh"
        post.write_text(host_post_script(root, named))
        print("===== probe (runs inside the image) =====")
        print(probe.read_text().rstrip("\n"))
        print("===== host-pre (stages inputs on the VM host) =====")
        print(pre.read_text().rstrip("\n"))
        print("===== host-post (hashes outputs on the VM host) =====")
        print(post.read_text().rstrip("\n"))

        project = a.project
        if a.confirm and not project:
            project = config.require("PROJECT", "the VM project; or pass --project")
        rcs, outs = [], []
        for label, ref in zip("AB", images):
            argv = rii_argv(ref, probe, pre, post, [f"{root}:{root}"], machine, disk, timeout,
                            dry_run=not a.confirm, project=project, zone=a.zone)
            print(f"===== arm {label}: {' '.join(argv)}")
            if not a.confirm:
                proc = run(argv, capture_output=True, text=True)
                show_dry_run((proc.stdout or "") + (proc.stderr or ""), a.show_startup)
                if proc.returncode != 0:
                    print(f"run_in_image.sh --dry-run refused arm {label} (exit {proc.returncode})")
                    return proc.returncode
                continue
            rc, text = run_arm(argv, run=run)
            rcs.append(rc)
            outs.append(parse_run_output(text))
        if not a.confirm:
            print("== plan only. Add --confirm to boot "
                  f"{len(images)} VM(s) and run{' (GSVTK_READ_ONLY refuses that)' if read_only_on() else ''}.")
            return 0
        return report(images, rcs, outs, named, a.pair)
    finally:
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)


def report(images, rcs, outs, named, pair: bool) -> int:
    print("== replay result")
    for label, ref, rc, o in zip("AB", images, rcs, outs):
        print(f"  arm {label}: rc={rc}  image={ref}")
        for rel, h in sorted(o["outputs"].items()):
            print(f"    {h['sha256']}  {h['bytes']:>12}  {rel}")
        for m in o["missing"]:
            print(f"    MISSING declared output: {m}")
        if o["host_post_rc"] not in (0, None):
            print(f"    host-post rc={o['host_post_rc']}: the output listing may be incomplete")
    if any(rc == EXIT_NOT_SCORED for rc in rcs):
        print("== NOT SCORED: an arm measured nothing (see run_in_image.sh's reason above)")
        return EXIT_NOT_SCORED
    if any(rc not in (0, 1) for rc in rcs):
        print("== NOT SCORED: run_in_image.sh did not reach a verdict for every arm")
        return max(rcs)
    if len(rcs) == 1:
        if rcs[0] == 0 and not outs[0]["missing"]:
            print("== PASS: the task returned 0 and every declared output exists")
            return 0
        print("== FAIL: the task returned nonzero or a declared output is missing")
        return 1
    if pair:
        if rcs[0] == 0:
            print("== VACUOUS: arm A (unguarded) passed, so nothing here is evidence about the guard")
            return 1
        if rcs[1] != 0:
            print(f"== GUARD DOES NOT WORK: arm A rc={rcs[0]} and arm B rc={rcs[1]}")
            return 1
        print(f"== PAIRED: unguarded arm A failed (rc={rcs[0]}), guarded arm B passed, same inputs")
        return 0
    same_rc = rcs[0] == rcs[1]
    a_out, b_out = outs[0]["outputs"], outs[1]["outputs"]
    diff = sorted(k for k in set(a_out) | set(b_out) if a_out.get(k) != b_out.get(k))
    for k in diff:
        print(f"  differs: {k}  A={(a_out.get(k) or {}).get('sha256', 'absent')[:12]}  "
              f"B={(b_out.get(k) or {}).get('sha256', 'absent')[:12]}")
    if same_rc and not diff and a_out:
        print(f"== SAME: both arms rc={rcs[0]}, {len(a_out)} output(s) byte-identical")
        return 0
    if not a_out and not b_out:
        print("== NOTHING COMPARED: neither arm wrote an output file")
        return 1
    print(f"== DIFFER: rc A={rcs[0]} B={rcs[1]}, {len(diff)} output(s) differ")
    return 1


if __name__ == "__main__":
    sys.exit(main())
