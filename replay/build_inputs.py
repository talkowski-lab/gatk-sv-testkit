#!/usr/bin/env python3
"""Build a replayable input JSON from a captured Cromwell `script` dump.

Source of truth: the Cromwell-generated `script` left in the workspace bucket at
  <bucket>/submissions/<subId>/SVShell/<wfId>/call-RunSVShell/script
which contains the fully-substituted `jq --arg NAME "value" ...` list, i.e. the exact
task-input set of that run. That is better evidence than a method config: it is what the
run actually used, and it survives Terra stripping workflow metadata.
The prefix is whatever `--wdl`'s root workflow declares (SVShell.wdl -> `SVShell.`,
GATKSVPipelineSingleSample.wdl -> that name), because a builder that hardcodes one workflow's
name cannot build the other workflow's inputs -- and the single-sample arm of a replay is exactly
the one that needs it (GAP-REVIEW-single-sample-blocking.md T19).

Layout
  parse that list -> {name: value}
  un-localize /mnt/disks/cromwell_root/<b>/<p>  ->  gs://<b>/<p>
  load the target WDL with miniwdl -> its root workflow's name, declared inputs, types,
                                      optionality
  join by name, then classify every declared input:
      RECOVERED   literal came from the captured script
      DOCKER      supplied per-arm from --images
      DEFAULTED   optional in the WDL, so left unset (WDL default applies)
      UNRESOLVED  required and NOT in the captured script  -> written to unresolved.json, never guessed

A captured argument that is NOT a declared input of the WDL you passed is reported under
`task_args_not_in_wdl` and nothing else. That is where an upstream rename shows up, and it is
reported rather than translated: this file used to carry a private copy of gatk-sv's #961 rename
(`genotyping_rd_table` -> `genotyping_rd_depth_table` + `genotyping_rd_pesr_table`) behind a
`--translate-rd-keys` flag, which hid the rename in the one place a reader would never look and
duplicated a check that `checks/svshell_contract_check.py` already owns (its docstring names that
exact rename as the bug class). A generic input builder is not where a past upstream rename gets
memorized.

Read-only w.r.t. Terra: it only reads the WDL and writes local files.

  build_inputs.py --script data_vj/runsvshell_script.sh \
      --wdl /tmp/svshell_main.wdl --images testkit/svshell-replay/images_his.json \
      --out-dir testkit/svshell-replay/out --arm main_his_images
"""
from __future__ import annotations

import argparse
import os
import sys as _sys

_sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
import config  # noqa: E402
import json
import pathlib
import re
import sys

try:
    import WDL  # miniwdl
except ImportError:  # pragma: no cover
    # Deferred to main() so `--help` still works, and stated correctly: miniwdl is a PACKAGE, so
    # prepending a venv's bin to PATH does not make `import WDL` work on another interpreter --
    # you have to run this with that venv's python (or install it).
    WDL = None

LOCAL_PREFIX = '/mnt/disks/cromwell_root/'
ARG_RE = re.compile(r'--arg(json)?\s+([A-Za-z0-9_.]+)\s+("(?:[^"\\]|\\.)*"|.+?)(?=\s*\\?\n|\s+--arg|\s*$)')


def unlocalize(v: str) -> str:
    """Cromwell stages gs:// objects under /mnt/disks/cromwell_root/<bucket>/<path>."""
    if v.startswith(LOCAL_PREFIX):
        return 'gs://' + v[len(LOCAL_PREFIX):]
    if v.startswith('/'):
        return 'gs://' + v.lstrip('/')
    return v


def parse_script(path: pathlib.Path) -> dict:
    """Pull the task-input literals out of Cromwell's substituted command."""
    text = path.read_text()
    found = {}
    for is_json, name, raw in ARG_RE.findall(text):
        val = raw.strip().rstrip('\\').strip()
        if val.startswith('"') and val.endswith('"'):
            val = val[1:-1].replace('\\"', '"').replace('\\\\', '\\')
        found[name] = ('json' if is_json else 'str', val)
    return found


def wdl_inputs(wdl_path: pathlib.Path) -> tuple[str, dict]:
    """(prefix, declared inputs) of `wdl_path`'s root workflow: prefix -> {name: {type, optional}}.

    The prefix is the root workflow's own name plus a dot, taken from the document that was loaded,
    not from a string in this file. Five hardcoded `SVShell.` prefixes meant a single-sample WDL
    produced `SVShell.`-keyed JSON that no single-sample submission could read -- and it failed
    quietly, because every key is a string to JSON.
    """
    doc = WDL.load(str(wdl_path))
    wf = doc.workflow
    if wf is None:
        sys.exit(f'{wdl_path}: no workflow found')
    out = {}
    for dec in wf.inputs:
        out[dec.name] = {'type': str(dec.type), 'optional': bool(getattr(dec.type, 'optional', False))}
    return f'{wf.name}.', out


def coerce(wdl_type: str, value: str):
    """JSON-coerce a recovered string literal according to the declared WDL type."""
    t = wdl_type.strip()
    if t.startswith('Int'):
        return int(float(value))
    if t.startswith('Float'):
        return float(value)
    if t.startswith('Boolean'):
        return str(value).strip().lower() in ('true', '1', 'yes')
    if t.startswith('String'):
        return value
    if t.startswith('File') or t.startswith('Array[File'):
        return unlocalize(value)
    return value  # e.g. Array[String], Object -- handed through for a human to check


def selftest() -> int:
    """Offline fixtures for the one rule this tool exists to keep: the prefix comes from the WDL you
    passed, never from a name typed into this file.

    R2 deleted five hardcoded `SVShell.` prefixes, and a test that only asserted "the keys say
    SVShell." would have passed on the very literal that caused the bug. So the CONTROL is a fixture
    whose workflow really is named SVShell — same expected string, reached by loading the document —
    beside a differently named fixture that must produce a different prefix. Needs miniwdl, and says
    so by name when it is absent rather than reporting a clean zero.
    """
    import shutil
    import tempfile

    if WDL is None:
        print("selftest: SKIP -- miniwdl is required (`WDL` is the module wdl_inputs loads with). Run "
              "it with the kit's interpreter: docs/setup.md")
        return 0
    fails = []

    def ck(name, cond, extra=""):
        print(("  ok    " if cond else "  FAIL  ") + name + (f"  <{extra}>" if extra else ""))
        if not cond:
            fails.append(name)

    print("selftest: replay/build_inputs.py")
    d = tempfile.mkdtemp(prefix="build-inputs-selftest-")
    try:
        def fixture(name: str) -> pathlib.Path:
            p = pathlib.Path(d) / f"{name}.wdl"
            p.write_text('version 1.0\nworkflow %s {\n  input {\n    String ref_fasta\n'
                         '    File? baf_table\n    Array[String] samples = []\n  }\n  call t\n}\n'
                         'task t { command {} }\n' % name)
            return p

        prefix, inputs = wdl_inputs(fixture("MyCoolPipeline"))
        ck("CONTROL: the prefix is the loaded root workflow's own name plus a dot", prefix ==
           "MyCoolPipeline.", prefix)
        p2, _i2 = wdl_inputs(fixture("SVShell"))
        ck("a fixture whose workflow IS named SVShell yields 'SVShell.' by LOADING it — the hardcoded "
           "literal this file used to carry could not tell those two fixtures apart",
           p2 == "SVShell.", p2)
        ck("declared inputs are classified with optionality (a File? is not a File: one is a "
           "conditional in the WDL, the other is required)",
           set(inputs) == {"ref_fasta", "baf_table", "samples"}
           and inputs["baf_table"]["optional"] and not inputs["ref_fasta"]["optional"], str(inputs))

        scr = pathlib.Path(d) / "script.sh"
        scr.write_text('set -euxo pipefail\nsv_tools \\\n  --arg ref_fasta '
                       '"/mnt/disks/cromwell_root/b/k/ref.fa" \\\n  --argjson use_baf true \\\n'
                       '  --arg sample NA12878\n')
        got = parse_script(scr)
        ck("Cromwell's substituted command is parsed: names recovered, quotes and the '\\\\' "
           "continuation gone, --argjson marked as json",
           set(got) == {"ref_fasta", "use_baf", "sample"}
           and got["ref_fasta"][1] == "/mnt/disks/cromwell_root/b/k/ref.fa"
           and got["use_baf"][0] == "json", str(sorted(got)))
        ck("staged paths unlocalise back to gs:// (that is what the emitted JSON has to say), and a "
           "gs:// already in place is left alone",
           unlocalize("/mnt/disks/cromwell_root/b/k/ref.fa") == "gs://b/k/ref.fa"
           and unlocalize("/bare/path") == "gs://bare/path"
           and unlocalize("gs://a/b") == "gs://a/b")
        ck("types coerce off the DECLARED type, not off what the string looks like",
           coerce("Int", "12.0") == 12 and coerce("Boolean", "true") is True
           and coerce("String", "12.0") == "12.0"
           and coerce("File", "/mnt/disks/cromwell_root/b/k") == "gs://b/k",
           f"{coerce('Int', '12.0')}")
        ck("an unrecognised type is handed through for a human to check, not guessed into a value",
           coerce("Array[String]", "a,b") == "a,b")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print(("  FAIL  " + str(len(fails)) + " assertion(s) failed") if fails
          else "  all selftest assertions passed")
    return 1 if fails else 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    ap = argparse.ArgumentParser()
    # Advertised here so `--help` tells the truth about the flag (scripts/check_doc_flags.py grades every
    # documented flag against the tool's own help). Handled above parse_args, because every other argument
    # on this tool is required and a selftest needs none of them.
    ap.add_argument('--selftest', action='store_true',
                    help='run the offline fixture checks (prefix derivation, script parsing, coercion) '
                         'and exit; needs miniwdl, and prints a named SKIP when it is absent')
    ap.add_argument('--script', required=True, type=pathlib.Path)
    ap.add_argument('--wdl', required=True, type=pathlib.Path,
                    help='the arm\'s root WDL; its workflow name is the prefix every emitted key '
                         'carries, and its declared inputs are what gets classified')
    ap.add_argument('--images', required=True, type=pathlib.Path, help='JSON map of docker input -> image')
    ap.add_argument('--out-dir', required=True, type=pathlib.Path)
    ap.add_argument('--arm', required=True)
    ap.add_argument('--set', action='append', default=[], metavar='NAME=JSON',
                    help='value recovered by other means (evidence, not guesswork); recorded in the report')
    ap.add_argument('--set-note', action='append', default=[], metavar='NAME=TEXT',
                    help='why that value is trustworthy (goes into <arm>.report.json)')
    ap.add_argument('--namespace', default=config.get('TERRA_NAMESPACE') or '')
    ap.add_argument('--workspace', default=config.get('TERRA_WORKSPACE') or '')
    a = ap.parse_args()
    if WDL is None:
        raise SystemExit(
            "miniwdl is required (this reads each arm's declared inputs out of the WDL).\n"
            "  python -m pip install miniwdl\n"
            "  or run it with the interpreter that has it:  <that venv>/bin/python replay/build_inputs.py\n"
            "  (docs/setup.md)")

    recovered = parse_script(a.script)
    prefix, declared = wdl_inputs(a.wdl)
    images = json.loads(a.images.read_text())
    out_dir = a.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f'  recovered from script : {len(recovered)} task inputs')
    print(f'  declared in WDL       : {len(declared)} workflow inputs '
          f'({sum(1 for d in declared.values() if not d["optional"])} required) '
          f'of {prefix[:-1]} -> keys are prefixed "{prefix}"')

    # operator-supplied values must carry their evidence, or they are guesses
    supplied, ev = {}, {}
    for item in a.set:
        k, _, v = item.partition('=')
        supplied[k.strip()] = json.loads(v)
    for item in a.set_note:
        k, _, v = item.partition('=')
        ev[k.strip()] = v
    for k in supplied:
        if k not in ev:
            sys.exit(f'--set {k} has no --set-note: refuse to inject an unsourced value')

    inputs, unresolved, notes = {}, [], {}
    for name, spec in declared.items():
        t = spec['type']
        if name in images and ('docker' in name.lower() or 'image' in name.lower()):
            inputs[f'{prefix}{name}'] = images[name]
            notes[name] = 'DOCKER'
            continue
        if name in supplied:
            inputs[f'{prefix}{name}'] = supplied[name]
            notes[name] = f'EVIDENCED ({ev[name][:90]})'
            continue
        if name in recovered:
            kind, raw = recovered[name]
            # An optional File that arrived EMPTY must be OMITTED: "" is a defined value in WDL,
            # so `if (defined(dragen_cnv_vcf))` would flip from false to true and change the graph.
            if spec['optional'] and str(raw).strip() == '':
                notes[name] = 'OMITTED (empty in the captured run; passing "" would make defined() true)'
                continue
            try:
                inputs[f'{prefix}{name}'] = (json.loads(raw) if kind == 'json' else coerce(t, raw))
                notes[name] = 'RECOVERED'
            except Exception as e:  # keep going; report it
                unresolved.append({'input': name, 'type': t, 'why': f'coercion failed: {e}', 'raw': raw})
            continue
        if spec['optional']:
            notes[name] = 'DEFAULTED'
            continue
        if name in images:  # docker declared with a non-matching name
            inputs[f'{prefix}{name}'] = images[name]
            notes[name] = 'DOCKER'
            continue
        unresolved.append({'input': name, 'type': t, 'why': 'required but absent from the captured run'})

    leftovers = sorted(set(recovered) - set(declared))
    payload = dict(sorted(inputs.items()))
    json.dump(payload, (out_dir / f'{a.arm}.inputs.json').open('w'), indent=1, sort_keys=True)

    req = {k for k, v in declared.items() if not v['optional']}
    print(f'\n  per-arm classification ({a.arm}):')
    for tag in ('RECOVERED', 'DOCKER', 'DEFAULTED'):
        n = sum(1 for v in notes.values() if v == tag)
        if n:
            print(f'    {tag:<48} {n}')
    print(f'    {"UNRESOLVED":<48} {len(unresolved)}')

    # The honest part: what a human must still decide.
    report = {'arm': a.arm,
              # Where this arm would be submitted, recorded rather than merely accepted: a built
              # arm with no provenance is how an experiment gets run against the wrong workspace
              # a week later.
              'target': {'namespace': a.namespace, 'workspace': a.workspace},
              'unresolved_required': unresolved,
              'task_args_not_in_wdl': leftovers,
              'required_inputs_missing': sorted(f'{prefix}{n}' for n in req if f'{prefix}{n}' not in payload),
              'images_used': images, 'operator_supplied': {k: {'value': v, 'evidence': ev[k]} for k, v in supplied.items()}}
    json.dump(report, (out_dir / f'{a.arm}.report.json').open('w'), indent=1)
    if unresolved:
        print('\n  UNRESOLVED (never guessed -- these need a real value or a WDL default check):')
        for u in unresolved:
            print(f'    {u["input"]} ({u["type"]}): {u["why"]}')
    if leftovers:
        print(f'\n  in the captured script but not a workflow input (set inside the workflow): {len(leftovers)}')
        for n in leftovers[:12]:
            print(f'    {n}')
    print(f'\n  wrote {out_dir / (a.arm + ".inputs.json")}  ({len(payload)} inputs)')
    print(f'  wrote {out_dir / (a.arm + ".report.json")}')
    return 1 if report['required_inputs_missing'] else 0


if __name__ == '__main__':
    sys.exit(main())
