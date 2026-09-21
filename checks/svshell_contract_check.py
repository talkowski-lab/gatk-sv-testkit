#!/usr/bin/env python3
"""Structural contract check for gatk-sv's sv_shell single-sample flow.

`src/sv_shell/single_sample_pipeline.sh` chains 14 module scripts, feeding each one an
`inputs.json` that a `jq -n` block assembles out of the top-level inputs and the previous
modules' `outputs.json`. `jq -r ".missing_key"` yields the *string* "null" rather than
failing, so a renamed key silently becomes `--some-flag null` several stages later. There
is no CI coverage for any of this, and Copilot review found exactly such a break
(`genotyping_rd_table` -> `genotyping_rd_depth_table` with a reader left behind).

This checker compares, statically:

  A. driver `$inputs[0].K` reads   vs  the top-level JSON supply:
       - `src/sv_shell/sample_inputs/single_sample_pipeline*.json` (shipped fixtures)
       - `wdl/SVShell.wdl` `--arg`/`--argjson` names (its jq is `$ARGS.named`, so the
         argument names ARE the keys)
  B. per-module `jq -r ".K"` reads vs  the keys the driver's producer block writes into
     that module's inputs.json
  C. keys that are supplied/written but never read (dead weight; reported, not fatal)

Exit code 1 if any read key is unsupplied (the null-producing class of bug).
No data, no docker, no network: it only reads the tree.

Usage: svshell_contract_check.py [--repo DIR] [-v]
"""
import argparse
import json
import pathlib
import re
import sys

DRIVER = 'src/sv_shell/single_sample_pipeline.sh'
MODULE_DIR = 'src/sv_shell'
FIXTURE_GLOB = 'src/sv_shell/sample_inputs/single_sample_pipeline*.json'
WDL = 'wdl/SVShell.wdl'

READ_INPUTS = re.compile(r'\$inputs\[0\]\.([A-Za-z_][A-Za-z0-9_]*)')
JQ_BLOCK_END = re.compile(r">\s*\"\$\{?([A-Za-z0-9_]+)\}?\"")
CALL = re.compile(r'bash /opt/sv_shell/([a-z0-9_]+\.sh)')
MODULE_READ = re.compile(r'jq\s+-[a-zA-Z]+\s+"\.([A-Za-z_][A-Za-z0-9_]*)(?:\[\])?"\s+"\$\{?input_json')
WDL_ARG = re.compile(r'--arg(json)?\s+([A-Za-z_][A-Za-z0-9_]*)')
WRITE_KEY = re.compile(r'^\s{2,}"([A-Za-z_][A-Za-z0-9_]*)"\s*:')


def driver_blocks(text):
    """Split the driver into (writes_to, block_body) for each `jq -n ... > "${var}"`."""
    lines = text.splitlines()
    blocks = {}
    i = 0
    while i < len(lines):
        if re.match(r'\s*jq\s+-n', lines[i]):
            start = i
            # a jq invocation ends at the line whose redirect target we see, or next `jq`
            j = i + 1
            target = None
            while j < len(lines) and j < i + 400:
                m = JQ_BLOCK_END.search(lines[j])
                if m:
                    target = m.group(1)
                    break
                if re.match(r'\s*jq\s+-n', lines[j]):
                    break
                j += 1
            if target:
                blocks.setdefault(target, []).append('\n'.join(lines[start:j + 1]))
            i = j + 1
        else:
            i += 1
    return {k: '\n'.join(v) for k, v in blocks.items()}


def call_targets(text):
    """Map module script name -> the inputs.json variable it is handed."""
    out = []
    lines = text.splitlines()
    for idx, ln in enumerate(lines):
        m = CALL.search(ln)
        if not m:
            continue
        window = '\n'.join(lines[idx:idx + 8])
        args = re.findall(r'"\$\{?([A-Za-z0-9_]+)\}?"', window)
        in_json = next((a for a in args if a.endswith('inputs_json_filename')), None)
        out.append((m.group(1), in_json, idx + 1))
    return out


def module_reads(repo, module):
    p = repo / MODULE_DIR / module
    if not p.exists():
        return None
    return set(MODULE_READ.findall(p.read_text(errors='replace')))


import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
import config  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=config.get('GATK_SV_CHECKOUT') or '.',
                    help='gatk-sv checkout to read (default: GSVTK_GATK_SV_CHECKOUT, else cwd)')
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args()
    repo = pathlib.Path(a.repo).resolve()
    # Same contract as kit/config.py's checkout(): name the problem and the fix, never a
    # traceback. `--repo .` in the wrong directory is the single most likely invocation error.
    if not (repo / DRIVER).exists():
        sys.stderr.write(
            f"not a gatk-sv checkout: {repo} has no {DRIVER}\n"
            f"  pass --repo <gatk-sv checkout>, or set GSVTK_GATK_SV_CHECKOUT   (docs/setup.md)\n")
        raise SystemExit(4)
    dtxt = (repo / DRIVER).read_text(errors='replace')
    problems, notes = [], []

    # ---- A: top-level contract
    top_read = set(READ_INPUTS.findall(dtxt))
    fixtures = sorted(repo.glob(FIXTURE_GLOB))
    if not fixtures:
        problems.append(f'no top-level fixtures matched {FIXTURE_GLOB}')
    for fx in fixtures:
        keys = set(json.loads(fx.read_text()))
        missing = sorted(top_read - keys)
        unused = sorted(keys - top_read)
        tag = fx.relative_to(repo)
        if missing:
            problems.append(f'{tag}: driver reads {len(missing)} key(s) the fixture never supplies '
                            f'(jq -> the string "null"): {", ".join(missing)}')
        if unused:
            notes.append(f'{tag}: {len(unused)} key(s) supplied but never read by the driver: '
                         f'{", ".join(unused[:8])}{" ..." if len(unused) > 8 else ""}')

    wdl = repo / WDL
    if wdl.exists():
        supplied = {m.group(2) for m in WDL_ARG.finditer(wdl.read_text(errors='replace'))}
        missing = sorted(top_read - supplied)
        if missing:
            problems.append(f'{WDL}: supplies no {len(missing)} key(s) the driver reads '
                            f'(jq is $ARGS.named, so arg names are the keys): {", ".join(missing)}')
        if a.verbose:
            print(f'  [{WDL}] args={len(supplied)}  driver top reads={len(top_read)}')

    # ---- B: per-module contract
    blocks = driver_blocks(dtxt)
    for module, in_var, lineno in call_targets(dtxt):
        reads = module_reads(repo, MODULE_DIR + '/' + module) if module else None
        reads = module_reads(repo, module)
        if reads is None:
            problems.append(f'{DRIVER}:{lineno}: module {module} not found under {MODULE_DIR}/')
            continue
        if not in_var:
            notes.append(f'{DRIVER}:{lineno}: {module}: could not identify its inputs.json variable')
            continue
        body = blocks.get(in_var)
        if body is None:
            notes.append(f'{DRIVER}:{lineno}: {module}: inputs.json var {in_var} has no jq producer block '
                         f'(built elsewhere? skipped)')
            continue
        written = set(WRITE_KEY.findall(body))
        missing = sorted(reads - written)
        if missing:
            problems.append(f'{module}: reads {len(missing)} key(s) absent from {in_var} built at '
                            f'{DRIVER}: {", ".join(missing)}')
        if a.verbose:
            print(f'  [{module}] reads={len(reads)} written={len(written)} '
                  f'unsupplied={len(missing)}')

    print(f'sv_shell contract check: {DRIVER} + {len(call_targets(dtxt))} stage calls, '
          f'{len(top_read)} top-level keys read')
    for n in notes:
        print('  note: ' + n)
    for p in problems:
        print('  FAIL: ' + p)
    if problems:
        print(f'=> {len(problems)} unsupplied-read problem(s)')
        return 1
    print('=> OK: every statically-reachable read has a supplier')
    return 0


if __name__ == '__main__':
    sys.exit(main())
