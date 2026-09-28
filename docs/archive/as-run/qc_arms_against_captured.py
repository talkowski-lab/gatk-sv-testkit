#!/usr/bin/env python3
"""qc_arms_against_captured.py -- check a built SVShell A/B against a *known-good* real run.

`svshell_arms.py prep` proves every input is present and that every gs:// object exists. It cannot
prove a value is the *right* asset when several plausible ones share a name, and existence checks are
blind to a wrong-scheme typo. The last successful SVShell run (a colleague's HG00512 manta run, see README)
left its fully rendered command in `data_vj/runsvshell_script.sh`, so for every input name the two
agree on we can compare:

  * scalars  -> must match the captured run's `--arg`/`--argjson` value, unless the value is a Terra
                entity expression (`this.x`), which is the correct single-sample idiom;
  * paths    -> basenames must agree (the captured paths are private workspace copies, so only the
                basename is comparable).

It also flags anything still holding a mini-test `/inputs/` path, an unsubstituted `${`, or a URI
whose scheme is not gs/http(s) -- the class of defect that passes a "is it non-null" check and then
fails 3 hours into a paid run.

Read-only. Usage: qc_arms_against_captured.py [arms.json]
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))   # docs/archive/as-run -> repo root
sys.path.insert(0, os.path.join(ROOT, 'kit'))
import config  # noqa: E402

ARMS = os.path.join(config.get('WORK'), 'svshell_arms.json')
CAPTURED = os.environ.get('CAPTURED_SCRIPT', 'captured/runsvshell_script.sh')
# The repo's own production single-sample input. Most of its values are `${workspace.*}` refs (which the
# workspace attributes resolve), but the ones it states *literally* are directly comparable, and that is
# where array ordering shows up (genome_tracks).
PRODUCTION = os.path.join(ROOT, '..', 'gatk-sv-checkout', 'inputs', 'build', 'NA12878', 'terra',
                          'GATKSVPipelineSingleSample.json')

# Values that legitimately differ from a different sample's run.
EXPECTED_DIFF = {'bam_or_cram_file', 'bam_or_cram_index', 'sample_id', 'batch'}
# Assets the caller derived with Cromwell's write_lines(), so the captured basename is a temp filename.
DERIVED_BASENAME = re.compile(r'^write_lines_[0-9a-f]{8,}\b')


def main(path: str) -> int:
    arms = json.load(open(path))
    txt = open(CAPTURED).read()
    args = dict(re.findall(r'--arg\s+([A-Za-z0-9_]+)\s+"((?:[^"\\]|\\.)*)"', txt))
    args.update({k: v for k, v in
                 re.findall(r'--argjson\s+([A-Za-z0-9_.]+)\s+(\S+)', txt)})
    problems = scalar_diff = path_diff = compared = prod_diff = 0
    prod = {}
    if os.path.exists(PRODUCTION):
        for k, v in json.load(open(PRODUCTION)).items():
            sv = json.dumps(v)
            if '${' not in sv:
                prod[k.split('.')[-1]] = v
        print(f'(production single-sample reference: {len(prod)} literal values from '
              f'inputs/build/NA12878/terra/GATKSVPipelineSingleSample.json)')
    for arm_name, arm in arms.items():
        inputs = arm.get('inputs', arm)
        print(f'\n== {arm_name}: {len(inputs)} config inputs ==')
        for key, raw in sorted(inputs.items()):
            name = key.split('.')[-1]
            val = raw.strip('"') if isinstance(raw, str) else str(raw)
            if '/inputs/' in val:
                print(f'  PROBLEM {name}: mini-test container path {val[:60]}')
                problems += 1
            if '${' in val:
                print(f'  PROBLEM {name}: unsubstituted template ref {val[:60]}')
                problems += 1
            if '://' in val and not re.match(r'^"?\[?"?(gs|https?)://', val):
                print(f'  PROBLEM {name}: malformed URI {val[:60]}')
                problems += 1
            ref = args.get(name)
            if name in prod and not val.startswith('this.'):
                p = prod[name]
                same = (val == json.dumps(p)) or (str(val) == str(p)) \
                    or (val.startswith('[') and json.loads(val) == p)
                if not same:
                    print(f'  PROD    {name}: ours={val[:56]}  repo-production={json.dumps(p)[:56]}')
                    prod_diff += 1
            if ref is None or ref.startswith('$(jq'):
                continue
            compared += 1
            if val.startswith('this.'):
                if name not in EXPECTED_DIFF:
                    print(f'  note    {name}: entity expression, captured run used a literal')
                continue
            if val.startswith('gs://') or val.startswith('['):
                if not val.startswith('gs://'):
                    continue
                mine = os.path.basename(val)
                theirs = os.path.basename(ref.replace('/mnt/disks/cromwell_root/', ''))
                if DERIVED_BASENAME.match(theirs):
                    continue
                if mine != theirs:
                    print(f'  PATH    {name}: ours={mine[:52]}  captured={theirs[:52]}')
                    path_diff += 1
            elif not val.startswith('['):
                if val != str(ref):
                    print(f'  SCALAR  {name}: ours={val[:30]}  captured={str(ref)[:30]}')
                    scalar_diff += 1
    print(f'\ncompared {compared} names against the captured run: '
          f'{scalar_diff} scalar mismatch(es), {path_diff} basename mismatch(es), '
          f'{prod_diff} disagreement(s) with the repo production input, '
          f'{problems} hard problem(s)')
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ARMS))
