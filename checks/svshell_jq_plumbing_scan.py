#!/usr/bin/env python3
"""Execute every `jq -n` block in sv_shell's single_sample_pipeline.sh and fail on nulls.

Why this shape of test: `src/sv_shell/single_sample_pipeline.sh` is a 1300-line driver whose
fragility is JSON plumbing -- 14 modules each fed by a `jq -n ... > "${x_inputs_json_filename}"`
block, reading `--slurpfile`-bound inputs. `jq -r ".missing_key"` and `$x[0].missing_key` yield
*null*, which the shell then forwards as the literal argument `null` (this is how Copilot's
finding #1 on PR #961 would have failed: `--rd-depth-table null`). The driver also inline-runs
`/opt/sv-pipeline/scripts/...` by absolute path, so a full local dry-run is impossible without
the container -- but the jq blocks are pure and self-contained, so they can be extracted and
executed against fixtures in seconds, with no data and no docker.

Method per block:
  * locate every `jq -n` invocation, whatever shape it is written in, ending at its
    `> "${target}"` redirect
  * substitute shell variables: the top-level input file -> the real fixture; any variable this
    driver PRODUCES -> a stub carrying exactly the keys that producer block wrote (a key the
    driver reads from a variable it does NOT produce is stubbed as present and COUNTED in the
    report -- see --list-keys -- this tool cannot prove the WDL supplied it)
  * run the block verbatim with real jq (a boundary error shows up as a jq parse error, so
    extraction cannot silently pass)
  * collect every key whose resulting value is null / empty string / the string "null"

Two passes, because a producer's output is what defines its consumers' stub. Pass 1 runs with
permissive stubs to learn each produced key set (including which of those keys came out null, so
a null propagates downstream in pass 2 exactly as it would at runtime).

What this proves is bounded, and the bounds are printed: "N of M blocks executed". A scan that
executed fewer blocks than the driver contains is a coverage failure and exits nonzero -- the
first version of this file silently executed 1 block out of 5 in a reformatting-shaped driver and
still printed "every jq block executed".

Compare branch vs a baseline ref to answer "does this change introduce new nulls?". That gate is
only meaningful with full coverage, so --compare-to also fails on incomplete coverage.

Usage:
  svshell_jq_plumbing_scan.py --repo <worktree> [--fixture PATH] [--list-keys]
  svshell_jq_plumbing_scan.py --repo <worktree> --compare-to main     # PR gate form
  svshell_jq_plumbing_scan.py --selftest                              # no repo, no data
"""
import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
import config  # noqa: E402

DRIVER = 'src/sv_shell/single_sample_pipeline.sh'
# A `jq -n` invocation however written: leading assignments and any number of flags allowed, and
# the flag order is not assumed. Anchored on a word boundary that is not glued to a path so
# `/opt/.../jq` still counts and a jq program mentioning the word does not.
JQ_N = re.compile(r'(^|[^\w./-])jq\s+(?:-\S+\s+)*-n\b')
# The block's end: a redirect into a braced shell variable. Anything after it (2> log, || die,
# | tee) is the driver's business and must not stop us finding the target.
REDIRECT = re.compile(r'>\s*"?\$\{?([A-Za-z0-9_]+)\}?')
VAR_ANY = re.compile(r'\$\{([A-Za-z0-9_]+)\}|\$([a-z_][a-z0-9_]*)')
SLURPED_KEY = re.compile(r'\$([a-z_][a-z0-9_]*)\[0\]\.([A-Za-z_][A-Za-z0-9_]*)')
SLURP_ARG = re.compile(r'--slurpfile\s+([A-Za-z_][A-Za-z0-9_]*)\s+"\$\{([A-Za-z0-9_]+)\}"')
ARGJSON_ARG = re.compile(r'--argjson\s+([A-Za-z_][A-Za-z0-9_]*)\s+"\$\{([A-Za-z0-9_]+)\}"')
# Keys are read out of the fixture by name; a fixture line is a comment if it starts with one.
STUB_OUT = {}


def count_jq_n(lines):
    """How many `jq -n` invocations the file contains.

    Deliberately a line count with the same pattern the extractor starts from: that is not
    independence, and it is not claimed as such. It catches the failure that actually happened --
    an extractor that stops early and then cascades past the following block, so `executed` drifts
    below `present` while nothing compares them.
    """
    return sum(1 for ln in lines
               if not ln.lstrip().startswith('#') and JQ_N.search(ln))


def extract_blocks(lines):
    """Return [(target_var_or_None, start_line_1based, block_text)].

    target is None when no redirect was found before the next block started: that block did not
    execute, and it is reported rather than dropped. Resumption after such a block is at
    start+1, never past the scan -- resuming past it is what used to swallow the next block too.
    """
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if line.lstrip().startswith('#') or not JQ_N.search(line):
            i += 1
            continue
        start = i
        m = REDIRECT.search(line)
        if m:                                     # one-liner: jq -n ... '{...}' > "${x}"
            out.append((m.group(1), start + 1, line))
            i += 1
            continue
        target, j = None, i + 1
        while j < n:
            nxt = lines[j]
            if nxt.lstrip().startswith('#'):
                j += 1
                continue
            if JQ_N.search(nxt):                  # next block began: this one never closed
                break
            m = REDIRECT.search(nxt)
            if m:
                target = m.group(1)
                break
            j += 1
        if target:
            out.append((target, start + 1, '\n'.join(lines[start:j + 1])))
            i = j + 1
        else:
            out.append((None, start + 1, '\n'.join(lines[start:min(j, start + 60)])))
            i = start + 1
    return out


def _argjson_literal(m):
    """Whole `--argjson NAME "${VAR}"` -> a valid JSON array literal, single-quoted for bash."""
    return f"--argjson {m.group(1)} '[\"stub://{m.group(2)}\"]'"


def shell_subst(block, fixture, stub_for, dummy_dir, target=None, out_path=None):
    """Replace ${VAR} so the block can run standalone."""
    # Any var handed to `--slurpfile` must name a real, parseable JSON file or jq aborts before
    # evaluating the program -- which would look like a defect in the driver rather than in us.
    slurped = {v for _, v in SLURP_ARG.findall(block)}
    # `--argjson` values in this driver are always bash arrays built with `jq -R . | jq -s -c .`
    # (see single_sample_pipeline.sh:126-142), i.e. JSON arrays of strings. Handing jq a bare path
    # instead makes jq abort with "invalid JSON text", which would look like a driver defect.
    block = ARGJSON_ARG.sub(_argjson_literal, block)   # replace the quoted argument as a whole

    def rep(m):
        name = m.group(1)
        if not name:
            return m.group(0)
        low = name.lower()
        if target is not None and name == target:
            return out_path            # the block's own redirect target: keep it addressable
        if low == 'input_json':
            return fixture             # also slurped, but the real fixture is the point of the test
        if name in slurped or 'outputs_json' in low:
            return stub_for(name)
        if low in ('working_dir', 'output_dir', 'base_dir', 'log_dir'):
            return dummy_dir
        return f'{dummy_dir}/stub_{name}'
    # Only braced ${VAR} is a shell reference in these blocks; bare $name occurrences live inside
    # the single-quoted jq program and are jq variables, which must be left untouched.
    return re.sub(r'\$\{([A-Za-z0-9_]+)\}', rep, block)


def build_stub(driver_text):
    """Fallback stub: every key the driver reads from any slurped var.

    This is the shape that cannot expose a rename, so it is now only used for variables this
    driver does not produce. Those selects are counted and printed; they are not proven.
    """
    keys = sorted({k for _, k in SLURPED_KEY.findall(driver_text)})
    return {k: f'stub://{k}' for k in keys}


def scan_nulls(obj, prefix=''):
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            hits += scan_nulls(v, f'{prefix}.{k}' if prefix else k)
    elif isinstance(obj, list):
        for n, v in enumerate(obj):
            hits += scan_nulls(v, f'{prefix}[{n}]')
    elif obj is None:
        hits.append((prefix, 'null'))
    elif isinstance(obj, str) and obj.strip() in ('', 'null', 'None'):
        hits.append((prefix, repr(obj)))
    return hits


def run_blocks(repo, fixture, passes=2):
    """`repo` is a repo root (src/sv_shell/...) or an installed tree (the dir holding *.sh).

    Returns (results, coverage). coverage['present'] is the file's own count of `jq -n`, so a
    caller can tell "clean" apart from "did not look".
    """
    global STUB_OUT
    driver = _driver_path(repo).read_text(errors='replace')
    lines = driver.splitlines()
    STUB_OUT = build_stub(driver)
    blocks = extract_blocks(lines)
    jqvar_to_shell = dict(SLURP_ARG.findall(driver))     # jq name -> shell var it was bound to
    coverage = {'present': count_jq_n(lines), 'extracted': len(blocks),
                'executed': 0, 'errors': 0, 'unresolved': [], 'external_vars': set()}
    results = {}
    produced = {}                       # shell var -> {key: value}, from its producer block
    for attempt in range(max(1, passes)):
        results = {}
        produced_next = {}
        # Per-pass, not cumulative: pass 1 has no producer key sets yet, so everything looks
        # external there. Coverage must describe the final, fully-informed pass.
        unresolved, external = [], set()
        with tempfile.TemporaryDirectory() as dd:
            cache = {}

            def stub_for(name):
                if name in cache:
                    return cache[name]
                keys = produced.get(name)
                if keys is None:
                    external.add(name)
                    content = STUB_OUT
                else:
                    # Exactly what the producer wrote -- nulls included, so a null that a producer
                    # emits propagates into its consumers here just as it does at runtime.
                    content = dict(keys)
                p = pathlib.Path(dd) / f'{name}.json'
                p.write_text(json.dumps(content))
                cache[name] = str(p)
                return cache[name]

            for target, lineno, block in blocks:
                if target is None:
                    unresolved.append(lineno)
                    results[f'<no redirect>@L{lineno}'] = {
                        'error': 'no > "${var}" redirect before the next jq block: block NOT '
                                 'executed, so its output is unproven'}
                    continue
                out = pathlib.Path(dd) / f'{target}.json'
                script = shell_subst(block, str(fixture), stub_for, dd, target, str(out))
                with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as fh:
                    fh.write('set -euo pipefail\n' + script + '\n')
                    path = fh.name
                r = subprocess.run(['bash', path], capture_output=True, text=True)
                if r.returncode != 0:
                    last = (r.stderr or r.stdout).strip().splitlines()
                    results[f'{target}@L{lineno}'] = {'error': last[-1] if last else 'failed'}
                    continue
                try:
                    obj = json.loads(out.read_text())
                except Exception as exc:                                    # noqa: BLE001
                    results[f'{target}@L{lineno}'] = {'error': f'unparseable output: {exc}'}
                    continue
                info = {'nulls': dict(scan_nulls(obj))}
                if isinstance(obj, dict):
                    # Top-level keys this block wrote, with nulls kept null: that is what makes a
                    # producer's own defect visible to its consumers on the next pass.
                    top_null = {p for p, _ in scan_nulls(obj) if '.' not in p and '[' not in p}
                    info['keys'] = {k: (None if k in top_null else f'stub://{k}') for k in obj}
                    produced_next[target] = info['keys']
                results[f'{target}@L{lineno}'] = info
        coverage['executed'] = sum(1 for v in results.values() if 'error' not in v)
        coverage['errors'] = len(results) - coverage['executed']
        coverage['unresolved'], coverage['external_vars'] = unresolved, external
        produced = produced_next
        if attempt + 1 >= passes:
            break
    coverage['unprovable'] = sum(1 for jq, _k in SLURPED_KEY.findall(driver)
                                 if jqvar_to_shell.get(jq) in coverage['external_vars'])
    return results, coverage


def _driver_path(root):
    """Repo layout puts the driver at src/sv_shell/; an installed image puts it at <dir>/ itself."""
    p = pathlib.Path(root)
    if (p / DRIVER).exists():
        return p / DRIVER
    if (p / 'single_sample_pipeline.sh').exists():
        return p / 'single_sample_pipeline.sh'
    raise SystemExit(f'no single_sample_pipeline.sh found under {p} (tried {DRIVER})')


def _fixture_for(root, name):
    p = pathlib.Path(root)
    for cand in (p / 'src/sv_shell/sample_inputs' / name, p / 'sample_inputs' / name):
        if cand.exists():
            return cand
    raise SystemExit(f'fixture {name} not found under {p}')


def report(res, cov, list_keys=False):
    """Print the run. The headline is coverage first: 'clean' only means something if M of M ran."""
    print(f"blocks: {cov['present']} in file, {cov['extracted']} extracted, "
          f"{cov['executed']} executed, {cov['errors']} errored, "
          f"{cov.get('nulls', 0)} with null/empty")
    for tgt, info in res.items():
        if 'error' in info:
            print(f'  ERROR {tgt}: {info["error"]}')
    for tgt, hits in sorted(cov['null_items']):
        print(f'  {tgt}: {len(hits)} null/empty'
              + (f" -> {', '.join(sorted(hits)[:14])}{' ...' if len(hits) > 14 else ''}"
                 if list_keys else ''))
    if cov['unprovable']:
        print(f"  note: {cov['unprovable']} select(s) read keys from "
              f"{len(cov['external_vars'])} variable(s) this driver does not produce "
              f"({', '.join(sorted(n for n in cov['external_vars'] if 'outputs_json' in n.lower())[:4])}"
              f"{'' if len([n for n in cov['external_vars'] if 'outputs_json' in n.lower()]) <= 4 else ' ...'}). "
              f"Their keys are stubbed as present, so a rename there is INVISIBLE to this run.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tree', help='installed sv_shell tree (e.g. an extracted /opt/sv_shell); '
                                   'scans the shipped bytes instead of a git checkout')
    ap.add_argument('--repo', default=None,
                    help='gatk-sv checkout to read (default: GSVTK_GATK_SV_CHECKOUT, else cwd)')
    ap.add_argument('--fixture', default='src/sv_shell/sample_inputs/single_sample_pipeline.json')
    ap.add_argument('--compare-to', help='git ref to baseline against (PR gate form)')
    ap.add_argument('--list-keys', action='store_true')
    ap.add_argument('--selftest', action='store_true',
                    help='assert extraction/coverage/stale-reader detection on a built-in fixture')
    a = ap.parse_args()
    if a.selftest:
        return _selftest()
    repo = pathlib.Path(a.tree or a.repo or (config.get('GATK_SV_CHECKOUT') or '.')).resolve()
    fxname = pathlib.Path(a.fixture).name
    fixture = pathlib.Path(a.fixture) if pathlib.Path(a.fixture).is_absolute() else _fixture_for(repo, fxname)
    if a.tree:
        d = _driver_path(repo)
        import hashlib
        print(f'tree: {repo}')
        print(f'  driver  {d.relative_to(repo)}  md5={hashlib.md5(d.read_bytes()).hexdigest()}  '
              f'{len(d.read_text().splitlines())} lines')
        print(f'  fixture {fixture}  md5={hashlib.md5(fixture.read_bytes()).hexdigest()}')
        if a.compare_to:
            raise SystemExit('--compare-to needs a git checkout; scan the baseline image/tree '
                             'separately and diff the two reports')

    res, cov = run_blocks(repo, fixture)
    cov['null_items'] = [(k, v['nulls']) for k, v in res.items() if v.get('nulls')]
    cov['nulls'] = len(cov['null_items'])
    report(res, cov, a.list_keys)
    nerr = cov['errors'] + len(cov['unresolved'])
    incomplete = (cov['present'] != cov['executed']) or bool(cov['unresolved'])

    if a.compare_to:
        base, bcov = _baseline(repo, a.compare_to, a.fixture)
        # keyed by block (redirect target), since line numbers move between refs
        base = {k.split('@')[0]: v for k, v in base.items()}
        nnull_n = {k.split('@')[0]: v for k, v in cov['null_items']}
        new = {t: {k: v for k, v in hits.items() if k not in base.get(t, {})}
               for t, hits in nnull_n.items()}
        new = {t: h for t, h in new.items() if h}
        gone = {t for t in base if t not in nnull_n and t not in {k.split('@')[0] for k in res}}
        print(f'\nvs baseline {a.compare_to}: NEW null/empty in {sum(len(h) for h in new.values())} '
              f'key(s) across {len(new)} block(s)')
        for t, h in sorted(new.items()):
            print(f'  REGRESSION {t}: {", ".join(sorted(h))}')
        for t in sorted(gone):
            print(f'  NOTE {t}: this block reported nulls at {a.compare_to} and is not in this '
                  f'report at all -- deleted, or no longer extracted')
        # The compare branch used to consult only the null diff, so a branch where a producer
        # stopped compiling -- or was reformatted out of the extractor -- was certified green.
        if incomplete or bcov['present'] != bcov['executed']:
            print(f'=> FAIL: coverage is incomplete ({cov["executed"]}/{cov["present"]} now, '
                  f'{bcov["executed"]}/{bcov["present"]} at {a.compare_to}); "no new nulls" is not '
                  f'established by a partial scan')
            return 1
        if new:
            print('=> FAIL: this change introduces nulls that reach module arguments')
            return 1
        print('=> OK: full coverage on both refs and no new nulls introduced')
        return 0

    # Without --compare-to this is an absolute scan, and the docstring promises it fails on
    # nulls. Note that upstream gatk-sv has pre-existing nulls, so a plain run is red there by
    # design: --compare-to <ref> is the form that gates a change (docs/static-checks.md).
    if incomplete or nerr:
        print(f'=> FAIL: {cov["executed"]} of {cov["present"]} blocks executed -- the plumbing was '
              f'not fully exercised (errored={cov["errors"]}, no redirect={len(cov["unresolved"])})')
        return 1
    if cov['nulls']:
        print(f'=> FAIL: {cov["nulls"]} block(s) put null/empty into a module argument '
              f'(gate a change with --compare-to <ref>)')
        return 1
    print(f'=> OK: all {cov["executed"]} jq block(s) executed and none produced a null argument'
          + (f" ({cov['unprovable']} select(s) against externally-supplied variables, noted above)"
             if cov['unprovable'] else ''))
    return 0


def _baseline(repo, ref, fixture_arg):
    """Same scan against the driver/fixtures as of `ref`, extracted with `git show`.

    Extracted into a temp tree rather than `git worktree add` because the parent clone under
    ~/IdeaProjects is read-only by policy and this workspace must not touch its metadata.
    """
    with tempfile.TemporaryDirectory() as tmp:
        troot = pathlib.Path(tmp)
        (troot / 'src/sv_shell/sample_inputs').mkdir(parents=True)
        r = subprocess.run(['git', '-C', str(repo), 'show', f'{ref}:{DRIVER}'], capture_output=True)
        if r.returncode != 0:
            raise SystemExit(f'git show {ref}:{DRIVER} failed: {r.stderr.decode()[:200]}')
        (troot / DRIVER).write_bytes(r.stdout)
        fx_rel = pathlib.PurePath(fixture_arg).name
        r = subprocess.run(['git', '-C', str(repo), 'show',
                            f'{ref}:src/sv_shell/sample_inputs/{fx_rel}'], capture_output=True)
        fx = troot / 'src/sv_shell/sample_inputs' / fx_rel
        if r.returncode == 0:
            fx.write_bytes(r.stdout)
        else:                                      # fixture did not exist at that ref
            fx.write_bytes(pathlib.Path(fixture_arg).read_bytes())
        res, cov = run_blocks(troot, fx)
        return {k: v.get('nulls', {}) for k, v in res.items()}, cov


# --selftest: assertions, not a demo. Each one reproduces a hole this file actually shipped with:
# extraction that only matched one formatting style, blocks dropped without a word, coverage
# claimed rather than counted, and an outputs stub that supplied every key a reader might want.
_ST_DRIVER = """#!/usr/bin/env bash
set -euo pipefail

jq -n \\
--slurpfile inputs "${input_json}" \\
--arg gatk_jar "${gatk_jar}" \\
'{
  "rd_file": $inputs[0].rd_depth_table
}' > "${median_cov_inputs_json_filename}"

jq -n --slurpfile inputs "${input_json}" '{"pe_table": $inputs[0].genotyping_pe_table_RENAMED}' > "${evidence_qc_inputs_json_filename}"

jq -n \\
--slurpfile inputs "${input_json}" \\
'{
  "merged_PE": "gs://stub/pe.vcf.gz",
  "batch": "all"
}' > "${gather_batch_evidence_outputs_json_filename}" 2> gather.stderr

jq -n \\
--slurpfile gbe "${gather_batch_evidence_outputs_json_filename}" \\
'{
  "merged_pe": $gbe[0].merged_PE_vcf_LEFTOVER_READER
}' > "${cnmops_inputs_json_filename}"

jq -n \\
--slurpfile inputs "${input_json}" \\
'{
  "orphan": $inputs[0].output_prefix
}'

jq -n \\
--slurpfile inputs "${input_json}" \\
'{
  "vcf": $inputs[0].output_prefix
}' > "${genotype_svs_inputs_json_filename}"
"""
_ST_FIXTURE = '{"sample_name":"S","output_prefix":"out","rd_depth_table":"rd.txt",' \
              '"genotyping_pe_table":"pe.txt"}\n'


def _selftest():
    fails = []

    def check(desc, cond, detail=''):
        print(('  ok    ' if cond else '  FAIL  ') + desc + (f'\n          {detail}'
                                                            if (detail and not cond) else ''))
        if not cond:
            fails.append(desc)

    if not shutil.which('jq'):
        print('  SKIP  jq is not on PATH: the plumbing selftest cannot execute any block')
        return 0
    with tempfile.TemporaryDirectory() as dd:
        root = pathlib.Path(dd)
        (root / 'src/sv_shell/sample_inputs').mkdir(parents=True)
        (root / DRIVER).write_text(_ST_DRIVER)
        fx = root / 'src/sv_shell/sample_inputs/single_sample_pipeline.json'
        fx.write_text(_ST_FIXTURE)
        lines = _ST_DRIVER.splitlines()

        # 1. extraction shape-independence: canonical, one-line, and `2> log` after the redirect
        blocks = extract_blocks(lines)
        check('extract_blocks finds all 6 jq -n invocations regardless of formatting',
              len(blocks) == 6 and count_jq_n(lines) == 6,
              f'extracted={len(blocks)} present={count_jq_n(lines)}')

        # 2. a block with no redirect is reported, and does not swallow its successor
        check('a block with no redirect is reported instead of silently dropped',
              any(t is None for t, _l, _b in blocks),
              f'targets={[t for t, _l, _b in blocks]}')
        check('the block AFTER an unterminated one is still extracted',
              any(t == 'genotype_svs_inputs_json_filename' for t, _l, _b in blocks),
              f'targets={[t for t, _l, _b in blocks]}')

        res, cov = run_blocks(root, fx)
        # 3. coverage is counted, and a missing block is not "clean"
        check('coverage counts 6 present / 5 executed (the orphan did not run)',
              cov['present'] == 6 and cov['executed'] == 5 and len(cov['unresolved']) == 1,
              f'present={cov["present"]} executed={cov["executed"]} '
              f'unresolved={cov["unresolved"]}')

        # 4. the rename this tool exists for is detected on the inputs side
        nulls = {k.split('@')[0]: v['nulls'] for k, v in res.items() if v.get('nulls')}
        check('a stale reader of a renamed fixture key yields a null',
              'pe_table' in str(nulls.get('evidence_qc_inputs_json_filename', {})),
              f'nulls={nulls}')

        # 5. ... and on the outputs side, which the old all-keys stub made unrepresentable
        check('a stale reader of another block outputs.json is detected (per-producer stub)',
              'merged_pe' in str(nulls.get('cnmops_inputs_json_filename', {})),
              f'nulls={nulls}')
        check('externally-supplied variables are counted as unprovable, not silently stubbed',
              cov['unprovable'] == 0, f"unprovable={cov['unprovable']} "
                                      f"external={sorted(cov['external_vars'])}")
    print('selftest: ' + ('PASS' if not fails else f'{len(fails)} FAILED'))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
