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
  * locate `jq -n \\` .. `> "${target}"`
  * substitute shell variables: the top-level input file -> a fixture; any `*outputs_json*` var
    -> a synthesized stub carrying every key the driver reads from any slurped var (so the
    outputs side can never be the reason a key is missing -- we are testing the inputs side,
    which is where this PR's change lives)
  * run the block verbatim with real jq (a boundary error shows up as a jq parse error, so
    extraction cannot silently pass)
  * collect every key whose resulting value is null / empty string / the string "null"

Compare branch vs a baseline ref to answer "does this change introduce new nulls?".

Usage:
  svshell_jq_plumbing_scan.py --repo <worktree> [--fixture PATH] [--list-keys]
  svshell_jq_plumbing_scan.py --repo <worktree> --compare-to main     # PR gate form
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys
import tempfile

DRIVER = 'src/sv_shell/single_sample_pipeline.sh'
BLOCK_START = re.compile(r'^\s*jq\s+-n\s+\\\s*$')
BLOCK_END = re.compile(r'^\s*(?:.*?>\s*)?"?\$\{?([A-Za-z0-9_]+)\}?"?\s*$')
VAR_ANY = re.compile(r'\$\{([A-Za-z0-9_]+)\}|\$([a-z_][a-z0-9_]*)')
SLURPED_KEY = re.compile(r'\$([a-z_][a-z0-9_]*)\[0\]\.([A-Za-z_][A-Za-z0-9_]*)')


def extract_blocks(lines):
    """Return [(target_var, start_line, block_text)] for every jq -n block."""
    out, i = [], 0
    while i < len(lines):
        if BLOCK_START.match(lines[i]):
            start = i
            j = i + 1
            target = None
            while j < len(lines):
                m = BLOCK_END.match(lines[j])
                if m and '--' not in lines[j] and not BLOCK_START.match(lines[j]):
                    target = m.group(1)
                    break
                if BLOCK_START.match(lines[j]):
                    break
                j += 1
            if target:
                out.append((target, start + 1, '\n'.join(lines[start:j + 1])))
            i = j + 1
        else:
            i += 1
    return out


SLURP_ARG = re.compile(r'--slurpfile\s+([A-Za-z_][A-Za-z0-9_]*)\s+"\$\{([A-Za-z0-9_]+)\}"')
ARGJSON_ARG = re.compile(r'--argjson\s+([A-Za-z_][A-Za-z0-9_]*)\s+"\$\{([A-Za-z0-9_]+)\}"')


def _argjson_literal(m):
    """Whole `--argjson NAME "${VAR}"` -> a valid JSON array literal, single-quoted for bash."""
    return f"--argjson {m.group(1)} '[\"stub://{m.group(2)}\"]'"


def shell_subst(block, fixture, stub_paths, dummy_dir, target=None, out_path=None):
    """Replace ${VAR} / $var so the block can run standalone."""
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
            return stub_paths.setdefault(name, _write_stub(name, dummy_dir))
        if low in ('working_dir', 'output_dir', 'base_dir', 'log_dir'):
            return dummy_dir
        return f'{dummy_dir}/stub_{name}'
    # Only braced ${VAR} is a shell reference in these blocks; bare $name occurrences live inside
    # the single-quoted jq program and are jq variables, which must be left untouched.
    return re.sub(r'\$\{([A-Za-z0-9_]+)\}', rep, block)


def _write_stub(name, dummy_dir):
    p = pathlib.Path(dummy_dir) / f'{name}.json'
    p.write_text(json.dumps(STUB_OUT))
    return str(p)


STUB_OUT = {}


def build_stub(driver_text):
    """Every key the driver reads from any slurped var, so outputs can't cause a null."""
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


def run_blocks(repo, fixture):
    """`repo` is a repo root (src/sv_shell/...) or an installed tree (the dir holding *.sh)."""
    driver = _driver_path(repo).read_text(errors='replace')
    lines = driver.splitlines()
    global STUB_OUT
    STUB_OUT = build_stub(driver)
    results = {}
    with tempfile.TemporaryDirectory() as dd:
        dummy = dd
        stubs = {}
        for target, lineno, block in extract_blocks(lines):
            out = pathlib.Path(dd) / f'{target}.json'
            script = shell_subst(block, str(fixture), stubs, dummy, target, str(out))
            if not out.exists():
                pass  # created by the run below
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
            results[f'{target}@L{lineno}'] = {'nulls': dict(scan_nulls(obj))}
    return results


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


import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
import config  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tree', help='installed sv_shell tree (e.g. an extracted /opt/sv_shell); '
                                   'scans the shipped bytes instead of a git checkout')
    ap.add_argument('--repo', default=config.get('GATK_SV_CHECKOUT') or '.',
                    help='gatk-sv checkout to read (default: GSVTK_GATK_SV_CHECKOUT, else cwd)')
    ap.add_argument('--fixture', default='src/sv_shell/sample_inputs/single_sample_pipeline.json')
    ap.add_argument('--compare-to', help='git ref to baseline against (PR gate form)')
    ap.add_argument('--list-keys', action='store_true')
    a = ap.parse_args()
    repo = pathlib.Path(a.tree or a.repo).resolve()
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

    res = run_blocks(repo, fixture)
    nblocks = len(res)
    nerr = sum(1 for v in res.values() if 'error' in v)
    nnull = {k: v['nulls'] for k, v in res.items() if v.get('nulls')}
    print(f'blocks executed: {nblocks}   jq errors: {nerr}   blocks with null/empty values: {len(nnull)}')
    for tgt, info in res.items():
        if 'error' in info:
            print(f'  ERROR {tgt}: {info["error"]}')
    if a.list_keys:
        for tgt, hits in sorted(nnull.items()):
            print(f'  {tgt}: {len(hits)} null/empty -> {", ".join(sorted(hits)[:14])}'
                  f'{" ..." if len(hits) > 14 else ""}')
    else:
        for tgt, hits in sorted(nnull.items()):
            print(f'  {tgt}: {len(hits)} null/empty')

    if a.compare_to:
        base = _baseline(repo, a.compare_to, a.fixture)
        # keyed by block (redirect target), since line numbers move between refs
        base = {k.split('@')[0]: v for k, v in base.items()}
        nnull_n = {k.split('@')[0]: v for k, v in nnull.items()}
        new = {t: {k: v for k, v in hits.items() if k not in base.get(t, {})} for t, hits in nnull_n.items()}
        new = {t: h for t, h in new.items() if h}
        gone = {t: sorted(set(base.get(t, {})) - set(nnull_n.get(t, {}))) for t in base}
        gone = {t: g for t, g in gone.items() if g}
        print(f'\nvs baseline {a.compare_to}: NEW null/empty in {sum(len(h) for h in new.values())} key(s) '
              f'across {len(new)} block(s)')
        for t, h in sorted(new.items()):
            print(f'  REGRESSION {t}: {", ".join(sorted(h))}')
        for t, g in sorted(gone.items()):
            print(f'  fixed {t}: {", ".join(g)}')
        if new:
            print('=> FAIL: this change introduces nulls that reach module arguments')
            return 1
        print('=> OK: no new nulls introduced')
        return 0

    # Without --compare-to this is an absolute scan, and the docstring promises it fails on
    # nulls. Note that upstream gatk-sv has pre-existing nulls, so a plain run is red there by
    # design: --compare-to <ref> is the form that gates a change (docs/static-checks.md).
    if nerr:
        print(f'=> FAIL: {nerr} jq block(s) did not execute -- the plumbing was not proven')
        return 1
    if nnull:
        print(f'=> FAIL: {len(nnull)} block(s) put null/empty into a module argument '
              f'(gate a change with --compare-to <ref>)')
        return 1
    print('=> OK: every jq block executed and none produced a null argument')
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
        return {k: v.get('nulls', {}) for k, v in run_blocks(troot, fx).items()}


if __name__ == '__main__':
    sys.exit(main())
