#!/usr/bin/env python3
"""Build a replayable SVShell input JSON from a captured Cromwell `script` dump.

Source of truth: the Cromwell-generated `script` left in the workspace bucket at
  <bucket>/submissions/<subId>/SVShell/<wfId>/call-RunSVShell/script
which contains the fully-substituted `jq --arg NAME "value" ...` list, i.e. the exact
task-input set of that run. That is better evidence than a method config: it is what the
run actually used, and it survives Terra stripping workflow metadata.

Layout
  parse that list -> {name: value}
  un-localize /mnt/disks/cromwell_root/<b>/<p>  ->  gs://<b>/<p>
  load the target WDL with miniwdl -> its declared workflow inputs + types + optionality
  join by name, then classify every declared input:
      RECOVERED   literal came from the captured script
      DOCKER      supplied per-arm from --images
      DEFAULTED   optional in the WDL, so left unset (WDL default applies)
      UNRESOLVED  required and NOT in the captured script  -> written to unresolved.json, never guessed

Then emit one JSON per arm, with the #961 key translation applied to the arm that uses the
new WDL: `genotyping_rd_table` is replaced by `genotyping_rd_depth_table` +
`genotyping_rd_pesr_table`. For a plumbing regression we point both at the SAME single table
he used -- that keeps the comparison byte-fair; a scientific comparison would retrain instead.

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

# What renaming the RD-table inputs did to SVShell's input contract -- the class of
# change that is invisible to a WDL typecheck and visible only to a contract check.
RD_TABLE_OLD = 'genotyping_rd_table'
RD_TABLE_NEW = ['genotyping_rd_depth_table', 'genotyping_rd_pesr_table']


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


def wdl_inputs(wdl_path: pathlib.Path) -> dict:
    """Declared workflow-level inputs of workflow SVShell: name -> {type, optional}."""
    doc = WDL.load(str(wdl_path))
    wf = doc.workflow
    if wf is None:
        sys.exit(f'{wdl_path}: no workflow found')
    out = {}
    for dec in wf.inputs:
        out[dec.name] = {'type': str(dec.type), 'optional': bool(getattr(dec.type, 'optional', False))}
    return out


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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--script', required=True, type=pathlib.Path)
    ap.add_argument('--wdl', required=True, type=pathlib.Path, help='SVShell.wdl of the arm being built')
    ap.add_argument('--images', required=True, type=pathlib.Path, help='JSON map of docker input -> image')
    ap.add_argument('--out-dir', required=True, type=pathlib.Path)
    ap.add_argument('--arm', required=True)
    ap.add_argument('--translate-rd-keys', action='store_true',
                    help='apply the #961 genotyping_rd_table -> {depth,pesr} split (arms on the new WDL)')
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
    declared = wdl_inputs(a.wdl)
    images = json.loads(a.images.read_text())
    out_dir = a.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f'  recovered from script : {len(recovered)} task inputs')
    print(f'  declared in WDL       : {len(declared)} workflow inputs '
          f'({sum(1 for d in declared.values() if not d["optional"])} required)')

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
            inputs[f'SVShell.{name}'] = images[name]
            notes[name] = 'DOCKER'
            continue
        if name in supplied:
            inputs[f'SVShell.{name}'] = supplied[name]
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
                inputs[f'SVShell.{name}'] = (json.loads(raw) if kind == 'json' else coerce(t, raw))
                notes[name] = 'RECOVERED'
            except Exception as e:  # keep going; report it
                unresolved.append({'input': name, 'type': t, 'why': f'coercion failed: {e}', 'raw': raw})
            continue
        # the old single RD table can feed both new keys when asked
        if a.translate_rd_keys and name in RD_TABLE_NEW and RD_TABLE_OLD in recovered:
            _, raw = recovered[RD_TABLE_OLD]
            inputs[f'SVShell.{name}'] = coerce(t, raw)
            notes[name] = f'RECOVERED (translated from {RD_TABLE_OLD})'
            continue
        if spec['optional']:
            notes[name] = 'DEFAULTED'
            continue
        if name in images:  # docker declared with a non-matching name
            inputs[f'SVShell.{name}'] = images[name]
            notes[name] = 'DOCKER'
            continue
        unresolved.append({'input': name, 'type': t, 'why': 'required but absent from the captured run'})

    leftovers = sorted(set(recovered) - set(declared) - {RD_TABLE_OLD})
    payload = dict(sorted(inputs.items()))
    json.dump(payload, (out_dir / f'{a.arm}.inputs.json').open('w'), indent=1, sort_keys=True)

    req = {k for k, v in declared.items() if not v['optional']}
    print(f'\n  per-arm classification ({a.arm}):')
    for tag in ('RECOVERED', 'RECOVERED (translated from genotyping_rd_table)', 'DOCKER', 'DEFAULTED'):
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
              'required_inputs_missing': sorted(f'SVShell.{n}' for n in req if f'SVShell.{n}' not in payload),
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
