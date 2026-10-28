#!/usr/bin/env python3
"""Run gatk-sv's sv-shell single-sample pipeline in a branch's `sv-shell` image, then diff its VCF.

`src/sv_shell/single_sample_pipeline.sh` is a shell driver over the sv_shell module scripts,
shipped inside the `sv-shell` image at /opt/sv_shell/. It is not a Terra workflow, so the head-to-head
launcher cannot reach it. This tool runs it on a throwaway VM through
checks/image-check/run_in_image.sh, with the same staging machinery as replay/task_replay.py:

  1. validates your inputs JSON against the keys the driver reads (from the gatk-sv checkout:
     `--checkout`, default GSVTK_GATK_SV_CHECKOUT). A key the driver reads with no `// default`
     that is absent from your JSON is refused: jq would hand the driver the string "null";
  2. stages every gs:// value onto the VM under /sv/inputs/<bucket>/<object> and rewrites the JSON
     to those paths. For a key the driver reads with `mapfile` (a list of files, e.g.
     ref_pesr_disc_files_list) the value may be a JSON array of gs:// URIs, a local file, or a gs://
     file; its gs:// lines are staged too and the list file is rebuilt with the local paths;
  3. runs the driver in the image, uploads final.vcf.gz (+ outputs.json, the driver log) to
     `--out-uri`, fetches the VCF back, and compares it with `--baseline` using
     compare/vcf_body_diff.py (or vcf_paired_diff.py). The exit code is the comparator's.

Inputs JSON shape: the driver's own top-level inputs (src/sv_shell/sample_inputs/
single_sample_pipeline.json is the template), with file values as gs:// URIs instead of /inputs/...
paths. Scalars and string arrays pass through unchanged.

    # free plan: validation, inputs + sizes, the image, the VM command (run_in_image.sh --dry-run)
    python replay/svshell_run.py --inputs na12878.json --image REF --baseline base.vcf.gz
    # the real run: boots one VM, uploads three files to --out-uri, fetches one back
    python replay/svshell_run.py --inputs na12878.json --image REF --baseline base.vcf.gz \
        --out-uri gs://my-bucket/svshell-runs --confirm
    # compare an already-fetched VCF again (local, free)
    python replay/svshell_run.py --fetched work/svshell/x/final.vcf.gz --baseline base.vcf.gz

Exit codes: the comparator's (0 same, 1 differs, 2 nothing compared) after a run; 0 for a printed
plan; 1 the pipeline failed or produced no VCF; 2 usage or refusal (missing key, no image, over
--max-gb, read-only, no --out-uri); 3 missing dependency; 4 missing config (no gatk-sv checkout);
5 NOT SCORED (run_in_image.sh measured nothing).
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import task_replay as tr  # noqa: E402
from task_replay import config, refuse  # noqa: E402

ROOT = tr.ROOT
DRIVER_REL = "src/sv_shell/single_sample_pipeline.sh"
DRIVER_IN_IMAGE = "/opt/sv_shell/single_sample_pipeline.sh"
SV = "/sv"                                  # host dir, bind-mounted at the same path
COMPARATORS = {"body": ROOT / "compare" / "vcf_body_diff.py",
               "paired": ROOT / "compare" / "vcf_paired_diff.py"}
INDEX_SUFFIX = {".vcf.gz": ".tbi", ".bed.gz": ".tbi", ".tsv.gz": ".tbi", ".txt.gz": ".tbi"}

# Driver reads: `$inputs[0].K` inside jq blocks, and `jq -r ".K" "$input_json"` at the top.
READ_SLURP = re.compile(r"\$inputs\[0\]\.([A-Za-z_][A-Za-z0-9_]*)(\s*//)?")
READ_DIRECT = re.compile(
    r"""jq\s+-r\s+(['"])\.([A-Za-z_][A-Za-z0-9_]*)(\s*//[^'"]*)?\1\s+"?\$\{?input_json\}?"?""")
ASSIGN = re.compile(r"""^\s*([A-Za-z_][A-Za-z0-9_]*)=\$\(jq\s+-r\s+(['"])\.([A-Za-z_][A-Za-z0-9_]*)""", re.M)
MAPFILE = re.compile(r"""mapfile\s+-t\s+\w+\s*<\s*"?\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?"?""")


# ------------------------------------------------------------------------- driver contract
PASS_THROUGH = re.compile(
    r'^\s*"?([A-Za-z0-9_.\-]+)"?\s*:\s*\$inputs\[0\]\.([A-Za-z_][A-Za-z0-9_]*)\s*,?\s*$', re.M)


def _contract_check():
    sys.path.insert(0, str(ROOT / "checks"))
    import svshell_contract_check
    return svshell_contract_check


def driver_contract(text: str, repo=None) -> dict:
    """{required, optional, list_keys, softened} from the driver's source.

    A key is optional if every read of it has a `//` default. A key the driver only passes through
    (`"x": $inputs[0].k` into a module's inputs.json) is also optional when that module reads `x`
    only with a default, or never reads it; that needs the module scripts beside the driver
    (`repo`), and is skipped without them. list_keys are the keys whose value the driver reads line
    by line with `mapfile`.
    """
    plain, defaulted = set(), set()
    slurp_plain = {}
    for m in READ_SLURP.finditer(text):
        (defaulted if m.group(2) else plain).add(m.group(1))
        if not m.group(2):
            slurp_plain[m.group(1)] = slurp_plain.get(m.group(1), 0) + 1
    direct_plain = set()
    for m in READ_DIRECT.finditer(text):
        (defaulted if m.group(3) else plain).add(m.group(2))
        if not m.group(3):
            direct_plain.add(m.group(2))
    softened = set()
    if repo is not None:
        cc = _contract_check()
        blocks = cc.driver_blocks(text)
        var_mod = {v: mod for mod, v, _l in cc.call_targets(text, blocks) if v}
        soft_count = {}
        for var, body in blocks.items():
            mod = var_mod.get(var)
            if not mod:
                continue
            mod_plain, _d = cc.module_reads(pathlib.Path(repo), mod)
            if mod_plain is None:
                continue
            for written, key in PASS_THROUGH.findall(body):
                if written.split(".")[0] not in mod_plain:
                    soft_count[key] = soft_count.get(key, 0) + 1
        softened = {k for k, n in soft_count.items()
                    if k in plain and k not in direct_plain and n >= slurp_plain.get(k, 0)}
    var_key = {m.group(1): m.group(3) for m in ASSIGN.finditer(text)}
    lists = {var_key[v] for v in MAPFILE.findall(text) if v in var_key}
    required = plain - softened
    return {"required": required, "optional": (defaulted | softened) - required,
            "list_keys": lists, "softened": softened}


def find_driver(a) -> pathlib.Path:
    if a.driver:
        p = pathlib.Path(a.driver)
    else:
        repo = a.checkout or config.get("GATK_SV_CHECKOUT")
        if not repo:
            print("no gatk-sv checkout: pass --checkout DIR or --driver FILE, or set "
                  "GSVTK_GATK_SV_CHECKOUT (docs/setup.md)", file=sys.stderr)
            raise SystemExit(4)
        p = pathlib.Path(repo) / DRIVER_REL
    if not p.is_file():
        print(f"no sv-shell driver at {p}: pass --checkout <gatk-sv checkout> or --driver FILE",
              file=sys.stderr)
        raise SystemExit(4)
    return p


def validate_inputs(inputs, contract: dict, allow=()) -> dict:
    """{missing, empty, unknown}; `missing` (minus --allow-missing) is what refuses."""
    if not isinstance(inputs, dict):
        refuse(f"the inputs JSON must be an object of the driver's top-level keys, not a "
               f"{type(inputs).__name__}")
    known = contract["required"] | contract["optional"]
    return {"missing": sorted(contract["required"] - set(inputs) - set(allow)),
            "allowed": sorted((contract["required"] - set(inputs)) & set(allow)),
            "empty": sorted(k for k in contract["required"] & set(inputs) if inputs[k] in (None, "")),
            "unknown": sorted(set(inputs) - known)}


# --------------------------------------------------------------------------- input staging
def local_path(uri: str) -> str:
    b, o = tr.split_gs(uri)
    return f"{SV}/inputs/{b}/{o}"


def gsutil_cat(uri: str, run=subprocess.run):
    """Text of a gs:// object (read-only), or None."""
    try:
        p = run(["gsutil", "-q", "cat", uri], capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return p.stdout if p.returncode == 0 else None


def expand_lists(inputs: dict, list_keys, read=gsutil_cat) -> tuple:
    """(inputs with each list key as a JSON array of lines, notes, unreadable keys)."""
    out, notes, unreadable = dict(inputs), [], []
    for k in sorted(list_keys):
        v = out.get(k)
        if isinstance(v, list) or v in (None, ""):
            continue
        v = str(v)
        if v.startswith("gs://"):
            text = read(v)
            src = "gsutil cat (read-only)"
        else:
            try:
                text = pathlib.Path(v).read_text()
            except OSError:
                text = None
            src = "local file"
        if text is None:
            unreadable.append(k)
            notes.append(f"{k}: could not read the list {v}; its entries cannot be staged")
            continue
        out[k] = [ln.strip() for ln in text.splitlines() if ln.strip()]
        notes.append(f"{k}: {len(out[k])} line(s) read from {v} ({src}); gs:// lines are staged")
    return out, notes, unreadable


def rewrite_inputs(inputs: dict, list_keys, image_paths: bool) -> tuple:
    """(rewritten JSON, [gs uris], {key: [localized lines]} list files, refusals)."""
    uris, lists, bad = [], {}, []

    def loc(k, v):
        if isinstance(v, str) and v.startswith("gs://"):
            if v not in uris:
                uris.append(v)
            return local_path(v)
        if isinstance(v, str) and v.startswith("/") and not image_paths:
            bad.append(f"{k}: {v} is a local path; the VM cannot see it. Upload it and give the gs:// "
                       "URI (or pass --image-paths if it exists inside the image)")
        return v

    out = {}
    for k, v in inputs.items():
        if k in list_keys and isinstance(v, list):
            lists[k] = [loc(k, x) for x in v]
            out[k] = f"{SV}/lists/{k}.list"
        elif isinstance(v, list):
            out[k] = [loc(k, x) for x in v]
        else:
            out[k] = loc(k, v)
    return out, uris, lists, bad


def index_siblings(uris) -> list:
    sib = []
    for u in uris:
        for suf, idx in INDEX_SUFFIX.items():
            if u.endswith(suf) and (u + idx) not in uris:
                sib.append(u + idx)
    return sib


def lists_script(lists: dict) -> str:
    lines = [f"mkdir -p '{SV}/lists'"]
    for k, entries in sorted(lists.items()):
        tag = tr._heredoc_tag("\n".join(entries))
        lines.append(f"cat > '{SV}/lists/{k}.list' <<'{tag}'\n" + "\n".join(entries) + f"\n{tag}")
        lines.append(f"echo 'wrote {SV}/lists/{k}.list ({len(entries)} lines)'")
    return "\n".join(lines)


def svshell_probe(inputs_json: str, driver: str) -> str:
    tag = tr._heredoc_tag(inputs_json)
    return f"""set -u
export SV_SHELL_BASE_DIR='{SV}/wd' TMPDIR='{SV}/wd/tmp'
mkdir -p "$TMPDIR" '{SV}/out'
cat > '{SV}/inputs.json' <<'{tag}'
{inputs_json}
{tag}
echo "driver: {driver}"
bash '{driver}' '{SV}/inputs.json' '{SV}/out/outputs.json' '{SV}/out' </dev/null > '{SV}/out/driver.log' 2>&1
RC=$?
echo "SVSHELL_RC=$RC"
tail -n 40 '{SV}/out/driver.log'
if [ "$RC" -eq 0 ]; then
  FV=$(jq -r '.final_vcf // empty' '{SV}/out/outputs.json' 2>/dev/null)
  if [ -n "$FV" ] && [ -f "$FV" ]; then
    cp "$FV" '{SV}/out/final.vcf.gz' && echo "SVSHELL_FINAL=$FV"
  else
    echo "SVSHELL_NO_FINAL_VCF: outputs.json names '$FV'"
    RC=1
  fi
fi
exit "$RC"
"""


UPLOAD_FN = r'''set -uo pipefail
token() {
  curl -s -H "Metadata-Flavor: Google" \
    http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token \
    | jq -r .access_token
}
upload() {   # upload FILE gs://bucket/object
  local f="$1" uri="$2" rest bucket obj enc
  rest="${uri#gs://}"; bucket="${rest%%/*}"; obj="${rest#*/}"
  enc=$(jq -rn --arg o "$obj" '$o|@uri')
  curl -sf -X POST --data-binary @"$f" -H "Authorization: Bearer $(token)" \
    -H "Content-Type: application/octet-stream" \
    "https://storage.googleapis.com/upload/storage/v1/b/${bucket}/o?uploadType=media&name=${enc}" >/dev/null \
    && echo "SVSHELL_UPLOADED $uri" || { echo "UPLOAD FAILED: $f -> $uri"; return 1; }
}
'''


def svshell_post(dest: str) -> str:
    return UPLOAD_FN + f"""fails=0
for f in final.vcf.gz outputs.json driver.log; do
  p='{SV}/out/'"$f"
  [ -f "$p" ] || {{ echo "SVSHELL_ABSENT $f"; continue; }}
  echo "REPLAY_OUT $(sha256sum "$p" | cut -d' ' -f1) $(stat -c %s "$p") $f"
  upload "$p" '{dest}/'"$f" || fails=$((fails+1))
done
[ "$fails" -eq 0 ]
"""


# --------------------------------------------------------------------------------- compare
def compare(baseline: str, fetched: str, which: str, extra, run=subprocess.run) -> int:
    if not pathlib.Path(fetched).is_file():
        print(f"NOTHING COMPARED: no fetched VCF at {fetched}")
        return 2
    if not pathlib.Path(baseline).is_file():
        print(f"NOTHING COMPARED: no baseline VCF at {baseline}")
        return 2
    argv = [sys.executable, str(COMPARATORS[which]), baseline, fetched, *extra]
    if which == "body":
        argv += ["--label-a", "baseline", "--label-b", "branch"]
    print(f"== compare: {' '.join(argv)}")
    proc = run(argv, capture_output=True, text=True)
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")
    print(f"== comparator exit {proc.returncode}")
    return proc.returncode


def fetch_gs(uri: str, dest: pathlib.Path, run=subprocess.run) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        p = run(["gsutil", "-q", "cp", uri, str(dest)], capture_output=True, text=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"fetch failed: gsutil cp {uri}: {exc}")
        return False
    if p.returncode != 0:
        print(f"fetch failed: gsutil cp {uri} -> exit {p.returncode}: {(p.stderr or '')[:300]}")
        return False
    return True


# ------------------------------------------------------------------------------------ main
def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog="Free by default. --confirm boots one VM and writes to --out-uri.")
    ap.add_argument("--inputs", help="your inputs JSON (the driver's top-level keys; gs:// file values)")
    ap.add_argument("--image", action="append", help="the branch's sv-shell image ref")
    ap.add_argument("--image-from", help="a docker/image_capture.py capture file")
    ap.add_argument("--image-key", default="", help="its entry (e.g. sv_shell_docker)")
    ap.add_argument("--checkout", help="gatk-sv checkout whose driver is validated against "
                                       "(default GSVTK_GATK_SV_CHECKOUT); match it to the image's branch")
    ap.add_argument("--driver", help="the driver file itself (overrides --checkout)")
    ap.add_argument("--driver-in-image", default=DRIVER_IN_IMAGE)
    ap.add_argument("--allow-missing", action="append", default=[], metavar="KEY",
                    help="run even though KEY (a required driver input) is absent; it reads as null")
    ap.add_argument("--image-paths", action="store_true",
                    help="accept absolute non-gs:// paths as paths inside the image")
    ap.add_argument("--baseline", help="baseline final VCF (local path or gs://)")
    ap.add_argument("--comparator", choices=sorted(COMPARATORS), default="body")
    ap.add_argument("--compare-arg", action="append", default=[],
                    help="passed to the comparator (repeat; e.g. --compare-arg=--unordered)")
    ap.add_argument("--fetched", help="skip the run: compare this local VCF with --baseline")
    ap.add_argument("--out-uri", help="gs:// prefix the VM uploads final.vcf.gz, outputs.json and "
                                      "driver.log to (required for --confirm)")
    ap.add_argument("--sizes", help="JSON {gs_uri: bytes}: size lookup with no network")
    ap.add_argument("--max-gb", type=float, default=300.0,
                    help="refuse to stage more than this (default 300; a CRAM plus the reference "
                         "panel is typically 50-150 GB)")
    ap.add_argument("--billing-project", default="", help="userProject for requester-pays buckets")
    ap.add_argument("--machine", default="e2-standard-16")
    ap.add_argument("--disk", type=int, help="boot disk GB (default: 3x inputs + 150)")
    ap.add_argument("--hours", type=float, default=10.0, help="runtime used by the cost estimate")
    ap.add_argument("--timeout", type=int, help="poll ceiling, minutes (default: 2x --hours)")
    ap.add_argument("--project", default="")
    ap.add_argument("--zone", default="")
    ap.add_argument("--show-startup", action="store_true")
    ap.add_argument("--work-dir", help="local dir for generated scripts and the fetched VCF")
    ap.add_argument("--confirm", action="store_true", help="boot the VM, upload, fetch, compare")
    return ap


def main(argv=None, run=subprocess.run, read=None) -> int:
    a = build_parser().parse_args(argv)
    if a.confirm and tr.read_only_on():
        refuse("--confirm boots a VM and uploads to --out-uri, and GSVTK_READ_ONLY is set. Nothing "
               "was read or created; omit --confirm for the free plan.")
    if a.fetched:
        if not a.baseline:
            refuse("--fetched needs --baseline: there is nothing to compare it with")
        return compare(a.baseline, a.fetched, a.comparator, a.compare_arg, run=run)

    images = tr.resolve_images(a)
    if len(images) != 1:
        refuse("sv-shell runs one image per call; run the baseline image separately to make a baseline")
    image = images[0]
    if not a.inputs:
        refuse("no --inputs JSON (the driver's top-level keys, file values as gs:// URIs)")
    if not a.baseline:
        refuse("no --baseline VCF: a run with nothing to compare against answers nothing")
    if not a.baseline.startswith("gs://") and not pathlib.Path(a.baseline).is_file():
        refuse(f"--baseline {a.baseline} is not a file")
    if a.confirm and not (a.out_uri and a.out_uri.startswith("gs://") and "/" in a.out_uri[5:].strip("/")):
        refuse("--confirm needs --out-uri gs://<bucket>/<prefix>: where the VM puts the VCF to fetch back")

    driver = find_driver(a)
    repo = driver.parent.parent.parent if (driver.parent.parent / "sv_shell").is_dir() else None
    contract = driver_contract(driver.read_text(errors="replace"), repo)
    if not contract["required"]:
        refuse(f"{driver} yields no top-level reads; this is not the driver this tool knows how to read")
    try:
        inputs = json.loads(pathlib.Path(a.inputs).read_text())
    except (OSError, ValueError) as exc:
        refuse(f"--inputs {a.inputs}: {type(exc).__name__}: {exc}")
    v = validate_inputs(inputs, contract, a.allow_missing)
    print(f"== sv-shell plan ({'--confirm: WILL RUN' if a.confirm else 'free: nothing is created'})")
    print(f"   driver   : {driver} ({len(contract['required'])} required, "
          f"{len(contract['optional'])} optional top-level keys, {len(contract['softened'])} of them "
          f"passed through to a module that defaults them; {len(contract['list_keys'])} list keys)")
    print(f"   in image : {a.driver_in_image}")
    print(f"   image    : {image}")
    if v["missing"]:
        refuse(f"--inputs {a.inputs} lacks {len(v['missing'])} key(s) the driver reads with no "
               f"default (jq would yield the string \"null\"): {', '.join(v['missing'])}")
    for k in v["allowed"]:
        print(f"   note: {k} is missing and allowed by --allow-missing; the driver will read null")
    for k in v["empty"]:
        print(f"   note: {k} is supplied as {json.dumps(inputs[k])}; the driver forwards it as-is")
    if v["unknown"]:
        print(f"   note: {len(v['unknown'])} key(s) the driver never reads: {', '.join(v['unknown'][:10])}")

    expanded, notes, unreadable = expand_lists(inputs, contract["list_keys"],
                                               read=read or (lambda u: gsutil_cat(u, run=run)))
    for n in notes:
        print(f"   list: {n}")
    rewritten, uris, lists, bad = rewrite_inputs(expanded, contract["list_keys"], a.image_paths)
    if bad:
        refuse("inputs that cannot reach the VM:\n  " + "\n  ".join(bad))
    sib = index_siblings(uris)
    look = uris + sib + ([a.baseline] if a.baseline.startswith("gs://") else [])
    sizes, how = tr.lookup_sizes(look, a.sizes, run=run)
    unknown_all = how.startswith("UNKNOWN")
    present_sib = [u for u in sib if sizes.get(u) is not None]
    stage_uris = uris + present_sib
    absent = [] if unknown_all else [u for u in uris if sizes.get(u) is None]
    total = None if unknown_all else sum(sizes[u] for u in stage_uris if sizes.get(u) is not None)
    stage = [{"uri": u, "path": local_path(u), "why": "index" if u in present_sib else "input"}
             for u in stage_uris]
    gb = (total or 0) / 1e9
    disk = a.disk or int(math.ceil(gb * 3 + 150))
    timeout = a.timeout or int(math.ceil(a.hours * 2 * 60))
    cost, basis = tr.cost_estimate(a.machine, disk, a.hours)
    run_id = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = f"{a.out_uri.rstrip('/')}/{run_id}" if a.out_uri else "<--out-uri>/" + run_id

    print(f"   sizes    : {how}")
    print(f"   inputs   : {len(stage)} object(s) to stage under {SV}/inputs, total {fmt(total)} "
          f"(limit {a.max_gb:g} GB)")
    for s in stage:
        print(f"     {fmt(sizes.get(s['uri'])):>10}  {s['uri']}  ({s['why']})")
    for u in absent:
        print(f"     NOT FOUND: {u}")
    print(f"   VM       : {a.machine}, {disk} GB pd-ssd, poll ceiling {timeout} min")
    print(f"   cost     : {'~$%.2f' % cost if cost is not None else 'not estimated'} ({basis})")
    print(f"   upload   : {dest}/{{final.vcf.gz,outputs.json,driver.log}}  (undo: gsutil -m rm -r {dest})")
    print(f"   compare  : {COMPARATORS[a.comparator].relative_to(ROOT)} {a.baseline} <fetched final.vcf.gz>")
    print("   undo VM  : run_in_image.sh deletes the VM after its verdict, or prints the delete command")

    if absent:
        refuse(f"{len(absent)} input object(s) do not exist (listed above); nothing was created")
    if total is not None and gb > a.max_gb:
        refuse(f"the inputs total {fmt(total)} ({gb:.1f} GB), over --max-gb {a.max_gb:g}. Raise "
               "--max-gb if you mean it; nothing was created.")
    if a.confirm and unknown_all:
        refuse(f"input sizes are {how}, so --max-gb cannot be enforced; pass --sizes or put gsutil on PATH")
    if a.confirm and unreadable:
        refuse(f"list key(s) {', '.join(unreadable)} could not be read, so their files cannot be staged")

    if a.work_dir:
        wd = pathlib.Path(a.work_dir)
        wd.mkdir(parents=True, exist_ok=True)
        cleanup = None
    elif a.confirm:
        wd = config.work_dir("svshell", run_id)
        cleanup = None
    else:
        wd = pathlib.Path(tempfile.mkdtemp(prefix="svshell-run-"))
        cleanup = wd
    try:
        probe = wd / "probe.sh"
        probe.write_text(svshell_probe(json.dumps(rewritten, indent=1, sort_keys=True), a.driver_in_image))
        pre = wd / "host_pre.sh"
        pre.write_text(tr.host_pre_script(stage, sizes, a.billing_project,
                                          extra=lists_script(lists) if lists else ""))
        post = wd / "host_post.sh"
        post.write_text(svshell_post(dest))
        print("===== probe (runs inside the image) =====")
        print(probe.read_text().rstrip("\n"))
        print(f"===== host-pre: stages {len(stage)} object(s) on the VM host, writes {len(lists)} "
              f"list file(s){'' if a.show_startup else ' (--show-startup prints it)'} =====")
        if a.show_startup:
            print(pre.read_text().rstrip("\n"))
        print("===== host-post (hashes and uploads the outputs) =====")
        print(post.read_text().rstrip("\n").split("fails=0", 1)[-1].strip())
        project = a.project
        if a.confirm and not project:
            project = config.require("PROJECT", "the VM project; or pass --project")
        argv = tr.rii_argv(image, probe, pre, post, [f"{SV}:{SV}"], a.machine, disk, timeout,
                           dry_run=not a.confirm, project=project, zone=a.zone)
        print(f"===== VM: {' '.join(argv)}")
        if not a.confirm:
            proc = run(argv, capture_output=True, text=True)
            tr.show_dry_run((proc.stdout or "") + (proc.stderr or ""), a.show_startup)
            if proc.returncode != 0:
                print(f"run_in_image.sh --dry-run refused (exit {proc.returncode})")
                return proc.returncode
            print("== plan only. Add --confirm (and --out-uri) to boot the VM, upload, fetch and compare.")
            return 0
        rc, text = tr.run_arm(argv, run=run)
        if rc != 0:
            print(f"== {'FAILED' if rc == 1 else 'NOT SCORED'}: run_in_image.sh exit {rc}; "
                  "no VCF to compare")
            return rc
        if f"SVSHELL_UPLOADED {dest}/final.vcf.gz" not in text:
            print(f"== the pipeline passed but final.vcf.gz was not uploaded to {dest}; nothing to compare")
            return 1
        fetched = wd / "final.vcf.gz"
        if not fetch_gs(f"{dest}/final.vcf.gz", fetched, run=run):
            return 1
        baseline = a.baseline
        if baseline.startswith("gs://"):
            local_base = wd / ("baseline." + baseline.rsplit("/", 1)[-1])
            if not fetch_gs(baseline, local_base, run=run):
                return 1
            baseline = str(local_base)
        return compare(baseline, str(fetched), a.comparator, a.compare_arg, run=run)
    finally:
        if cleanup:
            shutil.rmtree(cleanup, ignore_errors=True)


def fmt(n) -> str:
    return tr.fmt_bytes(n)


if __name__ == "__main__":
    sys.exit(main())
