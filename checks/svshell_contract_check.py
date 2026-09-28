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

# Same tolerant `jq -n` start as checks/svshell_jq_plumbing_scan.py: if the two tools disagree
# about how many producer blocks exist, their coverage numbers mean nothing next to each other.
JQ_N = re.compile(r'^\s*(?:\w+=\S+\s+)*jq\s+(?:-\S+\s+)*-n\b')
READ_INPUTS = re.compile(r'\$inputs\[0\]\.([A-Za-z_][A-Za-z0-9_]*)')
JQ_BLOCK_END = re.compile(r">\s*\"\$\{?([A-Za-z0-9_]+)\}?\"")
# A stage call, with or without shell options and however the path is spelled: upstream writes
# `bash /opt/sv_shell/x.sh`, `bash -x /opt/sv_shell/x.sh` and `bash "${SV_SHELL}/x.sh"`. The old
# pattern matched only the first, and a stage that vanished from this list vanished from the check
# without changing any count that looked wrong.
CALL = re.compile(r'\b(?:bash|sh)\b[^\n]*?([A-Za-z0-9_.\-]+\.sh)')
# A module reading one key out of its inputs.json. Both quote styles appear upstream (202 of the
# reads in src/sv_shell/*.sh are single-quoted, and the old pattern saw none of them), keys can
# contain digits and dashes, and a read carrying `// default` cannot yield null -- so it is
# collected separately instead of being reported as unsupplied.
MODULE_READ = re.compile(
    r'''jq\s+-[a-zA-Z]+\s+['"]\.([A-Za-z0-9_.\-]+?)(?:\[\])?\s*(?:(//)[^'"]*)?['"]\s+"\$\{?input_json''')
WDL_ARG = re.compile(r'--arg(json)?\s+([A-Za-z_][A-Za-z0-9_]*)')
# re.M is not optional: this runs over a whole multi-line jq block, so without it `^` matches only
# the block's first line and `written` is empty for every block in the tree -- which made every
# per-module FAIL on upstream a false positive (genotype_svs.sh: "reads 13 key(s) absent", true
# answer 0). checks/svshell_contract_check.py --selftest asserts this against a real block.
# Same charset as the reads: producers write keys like "99-ANALYTE" too, and a key this pattern
# cannot see on the write side becomes a false "absent from" FAIL on the read side. Quotes are
# optional because jq accepts bare object keys -- most of gather_batch_evidence's block is written
# `min_svsize: $inputs[0].min_svsize,`, and requiring quotes reported 15 keys as absent that the
# block writes on the very next line.
WRITE_KEY = re.compile(r'^\s{2,}"?([A-Za-z0-9_.\-]+)"?\s*:', re.M)


def driver_blocks(text):
    """Split the driver into (writes_to, block_body) for each `jq -n ... > "${var}"`."""
    lines = text.splitlines()
    blocks = {}
    i = 0
    while i < len(lines):
        if JQ_N.match(lines[i]):
            start = i
            # a jq invocation ends at the line whose redirect target we see, or at the next `jq -n`
            j = i
            target = None
            inline = JQ_BLOCK_END.search(lines[i])
            if inline:                            # one-line block: jq -n ... '{...}' > "${x}"
                blocks.setdefault(inline.group(1), []).append(lines[i])
                i += 1
                continue
            j = i + 1
            while j < len(lines) and j < i + 400:
                m = JQ_BLOCK_END.search(lines[j])
                if m:
                    target = m.group(1)
                    break
                if JQ_N.match(lines[j]):
                    break
                j += 1
            if target:
                blocks.setdefault(target, []).append('\n'.join(lines[start:j + 1]))
                i = j + 1
            else:
                # No redirect: this block writes nothing we can name. Resume at start+1 so the
                # next block is still seen (resuming at j used to swallow it).
                i = start + 1
        else:
            i += 1
    return {k: '\n'.join(v) for k, v in blocks.items()}


def call_targets(text, blocks):
    """[(module_script, inputs_json_var_or_None, lineno)] for every stage call.

    The inputs variable is the argument that names a producer block, falling back to anything that
    looks like an inputs json. Requiring the literal `_inputs_json_filename` suffix used to
    demote 9 of the 14 real stages (`${gather_sample_evidence_inputs_json}` and friends) to a note
    that never affected the verdict -- the check silently covered 5 of 14.
    """
    out = []
    lines = text.splitlines()
    for idx, ln in enumerate(lines):
        m = CALL.search(ln)
        if not m:
            continue
        window = '\n'.join(lines[idx:idx + 8])
        args = re.findall(r'"\$\{?([A-Za-z0-9_]+)\}?"', window)
        in_json = next((a for a in args if a in blocks), None)
        if in_json is None:
            in_json = next((a for a in args if re.search(r'_inputs_json(_filename)?$', a)), None)
        out.append((m.group(1), in_json, idx + 1))
    return out


def module_reads(repo, module):
    """(keys_read_without_default, keys_read_with_default) from one module, or (None, None)."""
    p = repo / MODULE_DIR / module
    if not p.exists():
        return None, None
    plain, defaulted = set(), set()
    for path, fallback in MODULE_READ.findall(p.read_text(errors='replace')):
        key = path.split('.')[0]                     # `.a.b` reads a; compare against written keys
        if not key:
            continue
        (defaulted if fallback else plain).add(key)
    return plain, defaulted


import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
import config  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=config.get('GATK_SV_CHECKOUT') or '.',
                    help='gatk-sv checkout to read (default: GSVTK_GATK_SV_CHECKOUT, else cwd)')
    ap.add_argument('-v', '--verbose', action='store_true')
    ap.add_argument('--strict', action='store_true',
                    help='also fail when some stage calls could not be compared, instead of '
                         'reporting them as notes (the honest choice for a CI gate)')
    ap.add_argument('--selftest', action='store_true',
                    help='assert the parser against a built-in mini tree; needs no checkout')
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
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
    calls = call_targets(dtxt, blocks)
    checked = 0
    for module, in_var, lineno in calls:
        reads, defaulted = module_reads(repo, module)
        if reads is None:
            problems.append(f'{DRIVER}:{lineno}: module {module} not found under {MODULE_DIR}/')
            continue
        if not in_var:
            notes.append(f'{DRIVER}:{lineno}: {module}: could not identify its inputs.json variable '
                         f'-- its reads were NOT compared')
            continue
        body = blocks.get(in_var)
        if body is None:
            notes.append(f'{DRIVER}:{lineno}: {module}: inputs.json var {in_var} has no jq producer '
                         f'block (built elsewhere?) -- its reads were NOT compared')
            continue
        checked += 1
        written = set(WRITE_KEY.findall(body))
        missing = sorted(reads - written)
        if missing:
            problems.append(f'{module}: reads {len(missing)} key(s) absent from {in_var} built at '
                            f'{DRIVER}: {", ".join(missing)}')
        if a.verbose:
            print(f'  [{module}] reads={len(reads)} defaulted={len(defaulted - reads)} '
                  f'written={len(written)} unsupplied={len(missing)}')

    unresolved = len(calls) - checked
    print(f'sv_shell contract check: {DRIVER} + {len(calls)} stage calls '
          f'({checked} compared, {unresolved} not comparable), '
          f'{len(top_read)} top-level keys read')
    for n in notes:
        print('  note: ' + n)
    for p in problems:
        print('  FAIL: ' + p)
    if problems:
        print(f'=> {len(problems)} unsupplied-read problem(s)')
        if unresolved:
            # Both at once: findings AND a blind spot. Reporting only the findings implies the
            # rest was checked, which is the misleading half of the message.
            print(f'=> also NOT PROVEN for {unresolved} of {len(calls)} stage calls '
                  f'(see notes above)')
        return 1
    if unresolved:
        # "OK: every read has a supplier" must not be printable when some stage was never
        # compared: that is exactly how a rename in an uncovered stage walks through the gate.
        if a.strict:
            print(f'=> FAIL (--strict): {unresolved} of {len(calls)} stage calls could not be '
                  f'compared; see the notes above')
            return 1
        print(f'=> NOT PROVEN: {checked} of {len(calls)} stage calls compared. The rest are in the '
              f'notes; reads there were not checked. Use --strict to gate on this.')
        return 0
    print('=> OK: every statically-reachable read has a supplier')
    return 0


# --selftest asserts the PARSER, which is where every one of this tool's silent holes lived. Each
# check below mirrors a shape that exists in real src/sv_shell/*.sh.
_ST = {
    'single_sample_pipeline.sh': """#!/usr/bin/env bash
jq -n \\
--slurpfile inputs "${input_json}" '{
    "written_key": $inputs[0].top_key,
    "good_key": "g",
    "99-ANALYTE": "a",
    bare_key: "b"
}' > "${m1_inputs_json_filename}"

jq -n \\
--slurpfile inputs "${input_json}" '{
    "written_key": $inputs[0].top_key
}' > "${m2_inputs_json}"

bash /opt/sv_shell/m1.sh \\
  "${m1_inputs_json_filename}" \\
  "${log_dir}"

bash -x /opt/sv_shell/m2.sh \\
  "${m2_inputs_json}"

bash "${SV_SHELL}/m3.sh" \\
  "${m3_inputs_json}"
""",
    'm1.sh': 'k=$(jq -r ".written_key" "${input_json}")\n'
             'd=$(jq -r ".absent_key // 5" "${input_json}")\n'
             'a=$(jq -r ".99-ANALYTE" "${input_json}")\n'
             'b=$(jq -r ".bare_key" "${input_json}")\n',
    'm2.sh': "k=$(jq -r '.single_quoted_reader' \"${input_json}\")\n",
    'm3.sh': 'k=$(jq -r ".never_mind" "${input_json}")\n',
}

# A second tree where every compared read IS supplied, and the only defect is that one stage's
# inputs.json is built outside this driver. Coverage, not findings, is what must change here.
_ST_CLEAN = {
    'single_sample_pipeline.sh': """#!/usr/bin/env bash
jq -n \\
--slurpfile inputs "${input_json}" '{
    "written_key": $inputs[0].top_key
}' > "${m1_inputs_json_filename}"

bash /opt/sv_shell/m1.sh "${m1_inputs_json_filename}"

bash /opt/sv_shell/m3.sh "${m3_inputs_json}"
""",
    'm1.sh': 'k=$(jq -r ".written_key" "${input_json}")\n',
    'm3.sh': 'k=$(jq -r ".whatever" "${input_json}")\n',
}


def _write_tree(root, files):
    (root / MODULE_DIR / 'sample_inputs').mkdir(parents=True, exist_ok=True)
    (root / DRIVER).write_text(files['single_sample_pipeline.sh'])
    for name, text in files.items():
        if name.endswith('.sh') and name != 'single_sample_pipeline.sh':
            (root / MODULE_DIR / name).write_text(text)
    (root / 'src/sv_shell/sample_inputs/single_sample_pipeline.json').write_text(
        json.dumps({'top_key': 't'}))


def _selftest():
    import subprocess
    import tempfile
    fails = []
    exe = str(pathlib.Path(__file__).resolve())

    def check(desc, cond, detail=''):
        print(('  ok    ' if cond else '  FAIL  ') + desc
              + (f'\n          {detail}' if (detail and not cond) else ''))
        if not cond:
            fails.append(desc)

    with tempfile.TemporaryDirectory() as dd:
        root = pathlib.Path(dd) / 'a'
        _write_tree(root, _ST)
        dtxt = _ST['single_sample_pipeline.sh']

        # 1. the bug that made every per-module FAIL a false positive
        blocks = driver_blocks(dtxt)
        some_block = blocks.get('m1_inputs_json_filename', '')
        check('WRITE_KEY finds the keys a producer block writes (re.M)',
              len(WRITE_KEY.findall(some_block)) == 4,
              f'found={WRITE_KEY.findall(some_block)}')
        check('a bare (unquoted) jq object key counts as written',
              'bare_key' in WRITE_KEY.findall(some_block),
              f'found={WRITE_KEY.findall(some_block)}')

        # 2. stage discovery does not depend on spelling
        calls = call_targets(dtxt, blocks)
        names = sorted(m for m, _v, _l in calls)
        check('stage calls found with options and a ${SV_SHELL} prefix, not just a literal path',
              names == ['m1.sh', 'm2.sh', 'm3.sh'], f'names={names}')

        # 3. an inputs variable without the _filename suffix still resolves
        byname = {m: v for m, v, _l in calls}
        check('an inputs variable named ${x}_inputs_json (no _filename) is resolved',
              byname.get('m2.sh') == 'm2_inputs_json', f'byname={byname}')

        # 4. reads in single quotes, and keys with digits/dashes, are seen
        plain, defaulted = module_reads(root, 'm1.sh')
        check('reads of keys with digits and dashes are seen', '99-ANALYTE' in plain,
              f'plain={sorted(plain)}')
        p2, d2 = module_reads(root, 'm2.sh')
        check('a single-quoted jq program is seen as a read', 'single_quoted_reader' in p2,
              f'plain={sorted(p2)}')
        check('a read carrying // default is not counted as a null risk',
              'absent_key' not in plain and 'absent_key' in defaulted,
              f'plain={sorted(plain)} defaulted={sorted(defaulted)}')

        # 5. end to end: what the tool says about this tree
        r = subprocess.run([sys.executable, exe, '--repo', str(root), '-v'],
                           capture_output=True, text=True)
        out = r.stdout + r.stderr
        check('a genuinely unsupplied single-quoted reader is reported',
              'single_quoted_reader' in out and 'absent from' in out, out[-400:])
        check('keys a producer writes (dashed, bare) are never reported as absent',
              '99-ANALYTE' not in out and 'bare_key' not in out and 'written_key' not in out,
              out[-400:])
        check('a stage with no producer block is reported as NOT compared',
              'm3.sh' in out and 'NOT compared' in out, out[-400:])
        check('findings and a blind spot are both stated, never findings alone',
              'unsupplied-read problem' in out and 'NOT PROVEN for' in out, out[-400:])

        # 6. a tree with no findings but incomplete coverage must not print a bare OK
        clean = pathlib.Path(dd) / 'b'
        _write_tree(clean, _ST_CLEAN)
        rc = subprocess.run([sys.executable, exe, '--repo', str(clean)],
                            capture_output=True, text=True)
        out2 = rc.stdout + rc.stderr
        check('partial coverage with no findings says NOT PROVEN, not OK',
              'NOT PROVEN' in out2 and '=> OK' not in out2, out2[-300:])
        rstrict = subprocess.run([sys.executable, exe, '--repo', str(clean), '--strict'],
                                 capture_output=True, text=True)
        check('--strict turns partial coverage into a failure', rstrict.returncode == 1,
              f'rc={rstrict.returncode}')
    print('selftest: ' + ('PASS' if not fails else f'{len(fails)} FAILED'))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
