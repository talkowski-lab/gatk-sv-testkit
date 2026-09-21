#!/usr/bin/env python3
"""single_sample_arms.py -- build/validate/submit the two single-sample Terra arms for PR #961.

Arms differ in exactly three things, which is the point:
  A  baseline  : Dockstore SingleSamplePipeline@main        + origin/main's inputs/values/dockers.json
  B  branch    : Dockstore SingleSamplePipeline@<branch-under-test> + this branch's dockers.json
Everything else (the other 11 caller images, all reference files, the entity, the WDL inputs) is
byte-identical between arms, so any output difference is attributable to the PR.

The branch renamed one workflow input (`genotyping_rd_table` -> `genotyping_rd_depth_table` +
`genotyping_rd_pesr_table`), so arm B supplies the two split tables produced by my own step-10
rerun (staged into this workspace's own bucket), and arm A keeps the single v1.1-era ref-panel
table that main's `call genotypebatch.GenotypeSVs` still expects.

Nothing here mutates Terra unless you run `patch-entity`, `create` or `submit --confirm`.
Only ever run against my own workspace.

  python svshell-replay/single_sample_arms.py prep      # read-only report
  python svshell-replay/single_sample_arms.py create    # POST both configs
  python svshell-replay/single_sample_arms.py validate  # Terra-side WDL check
  python svshell-replay/single_sample_arms.py submit --confirm
"""
from __future__ import annotations
import json, os, subprocess, sys, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))   # docs/archive/as-run -> repo root
sys.path.insert(0, os.path.join(ROOT, 'terra'))
sys.path.insert(0, os.path.join(ROOT, 'kit'))
import config  # noqa: E402
import terra                     # noqa: E402  (fiss wrapper)
import firecloud.api as fapi        # noqa: E402  (pip name is `firecloud`)

NS = os.environ['GSVTK_TERRA_NAMESPACE']
WS = os.environ['GSVTK_TERRA_WORKSPACE']
CNS = os.environ.get('GSVTK_BILLING_PROJECT', '<billing-project>')
BASE_CFG = 'gatk-sv-single-sample'           # the 99-input config that ran this workspace in March
WDL_PATH = 'github.com/broadinstitute/gatk-sv/SingleSamplePipeline'
SAMPLE = 'NA12878'
ENTITY = SAMPLE
ETYPE = 'sample'
BUCKET = 'gs://<workspace-bucket>'      # this workspace's own bucket
RD_DEPTH = f'{BUCKET}/genotyping_tables/all_samples.rd_depth_geno_params.tsv'
RD_PESR = f'{BUCKET}/genotyping_tables/all_samples.rd_pesr_geno_params.tsv'
REPO = os.environ.get('GSVTK_GATK_SV_CHECKOUT', '<gatk-sv-checkout>')
# The March-era config predates the GD/annotator inputs; these 11 required workflow inputs exist in
# neither the config nor `validate_config`'s idea of "valid" -- only submission-time validation
# catches them. Values taken from the repo's own built Terra inputs (NA12878/terra).
EXTRA = json.load(open(os.path.join(HERE, 'single_sample_extra_inputs.json')))
OUT = config.get('WORK')
ARMS = {'A_baseline_main': 'main', 'B_branch_<branch-under-test>': '<branch-under-test>'}


def dstore(version: str) -> dict:
    """Byte-exact Dockstore URI: Rawls re-resolves it on write and 404s on any deviation,
    including under-encoded slashes (learned the hard way on the head-to-head config)."""
    return {'sourceRepo': 'dockstore', 'methodPath': WDL_PATH, 'methodVersion': version,
            'methodUri': f"dockstore://{WDL_PATH.replace('/', '%2F')}/{version}"}


def dockers(rev: str) -> dict:
    """inputs/values/dockers.json as of a given ref (main = released, HEAD = branch)."""
    out = subprocess.run(['git', '-C', REPO, 'show', f'{rev}:inputs/values/dockers.json'],
                         capture_output=True, text=True, check=True).stdout
    return {k: v for k, v in json.loads(out).items() if isinstance(v, str)}


def base_config() -> dict:
    d = terra.config_payload(NS, WS, CNS, BASE_CFG)
    mc = d.get('methodConfig') or d
    if not mc.get('inputs'):
        raise SystemExit(f'could not read {BASE_CFG}: {str(d)[:200]}')
    return mc


def image_overrides(table: dict, keys: list[str]) -> tuple[dict, list[str]]:
    """Point every *config* image input at a literal from this arm's dockers.json, matched on the
    last dotted segment so nested keys (Some.SubWorkflow.wham_docker) are covered too."""
    over, unmatched = {}, []
    for k in keys:
        leaf = k.rsplit('.', 1)[-1]
        if leaf in table:
            over[k] = f'"{table[leaf]}"'        # Terra literals must be quoted; a bare path is a parse error
        elif leaf.endswith('_docker') or leaf.endswith('docker'):
            unmatched.append(k)
    return over, unmatched


def build() -> dict:
    mc = base_config()
    inputs, outputs = dict(mc['inputs']), dict(mc.get('outputs') or {})
    keys = list(inputs)
    main_tbl, br_tbl = dockers('origin/main'), dockers('HEAD')
    bodies = {}
    for name, version in ARMS.items():
        table = main_tbl if version == 'main' else br_tbl
        ins = dict(inputs)
        over, unmatched = image_overrides(table, keys)
        if unmatched:
            raise SystemExit(f'image inputs with no dockers.json key (refusing to leave a stale pin): {unmatched}')
        ins.update(over)
        ins.update({k: f'"{v}"' for k, v in EXTRA.items()})
        if version == 'main':
            pass    # arm A keeps the config's own single RD table (workspace.ref_panel_genotyping_rd_table)
        else:
            ins.pop('GATKSVPipelineSingleSample.genotyping_rd_table', None)
            ins['GATKSVPipelineSingleSample.genotyping_rd_depth_table'] = f'"{RD_DEPTH}"'
            ins['GATKSVPipelineSingleSample.genotyping_rd_pesr_table'] = f'"{RD_PESR}"'
        # Guard: `${workspace.x}` is a *template* reference, not a value. Quoting one into a config
        # silently makes it a literal path that Cromwell only discovers at runtime.
        tmpl = [k for k, v in ins.items() if '${' in str(v)]
        if tmpl:
            raise SystemExit(f'{name}: {len(tmpl)} inputs still hold unresolved template refs: {tmpl[:4]}')
        bodies[name] = {
            'namespace': CNS, 'name': name, 'rootEntityType': mc.get('rootEntityType') or ETYPE,
            'prerequisites': dict(mc.get('prerequisites') or {}),
            'inputs': ins, 'outputs': outputs,
            'methodRepoMethod': dstore(version),
            'deleteIntermediateOutputFiles': False, 'useCallCache': True, 'mode': 'FIXED',
            # Rawls' create payload is flat and requires these two (a config GET returns both);
            # omitting them is a 400, not a default.
            'deleted': False,
        }
    return bodies


def prep() -> None:
    bodies = build()
    a, b = bodies['A_baseline_main']['inputs'], bodies['B_branch_<branch-under-test>']['inputs']
    da = {k: v for k, v in a.items() if k not in b or b[k] != v}
    db = {k: v for k, v in b.items() if k not in a or a[k] != v}
    print(f'  arm A inputs: {len(a)}   arm B inputs: {len(b)}')
    print(f'  differing values (A side {len(da)}, B side {len(db)}):')
    for k in sorted(set(da) | set(db)):
        print(f'    {k.split("GATKSVPipelineSingleSample.")[-1]}')
        print(f'      A {str(da.get(k, "(same)"))[:76]}')
        print(f'      B {str(db.get(k, "(same)"))[:76]}')
    terra.dump(bodies, os.path.join(OUT, 'single_sample_arms.json'))
    print(f'  wrote {OUT}/single_sample_arms.json')


def patch_entity() -> None:
    """The sample row carries bam_or_cram_file but not sample_id, which the config needs for
    sample_id/batch. Additive merge on my own workspace's row. Rawls wants a single object here,
    not a list (a list fails with `Object expected in field 'name'`)."""
    # Rawls reserves `sample_id` for an entity of type `sample`: it is the entity NAME, bound
    # automatically, so `this.sample_id` in the config already resolves and writing it is an error
    # ("Attribute name sample_id is reserved and cannot be overwritten"). Kept as a probe.
    body = {'op': 'merge', 'entityType': ETYPE, 'name': ENTITY, 'attributes': {'arm_probe': 'noop'}}
    r = terra.session().post(f'https://api.firecloud.org/api/workspaces/{NS}/{WS}/entities', json=body, timeout=240)
    print(f'  upsert {ETYPE}/{ENTITY} sample_id: HTTP {r.status_code} {"" if r.status_code in (200, 204) else r.text[:200]}')
    if r.status_code not in (200, 204):
        raise SystemExit(1)


def create() -> None:
    for name, body in build().items():
        # Rawls requires methodConfigVersion on write; the payload we cloned carries the *source*
        # config's version, which would make the two arms share one.
        body = dict(body, methodConfigVersion=int(os.environ.get('GSV_CFG_VERSION', '1')))  # Rawls wants a JsNumber
        r = fapi.create_workspace_config(NS, WS, body)
        if r.status_code == 409:
            r = fapi.overwrite_workspace_config(NS, WS, CNS, name, body)
        ok = r.status_code in (200, 201)
        print(f'  create {name}: HTTP {r.status_code} {"" if ok else r.text[:240]}')
        if not ok:
            raise SystemExit(1)


def validate() -> None:
    """Terra-side WDL/input check.

    Caution, measured: validate_config on a config that does NOT exist returns HTTP 200 with an
    empty `invalid` list, i.e. it reports VALID for nothing. So existence is asserted first; a
    green result means nothing unless the config is actually there.
    """
    for name in ARMS:
        got = fapi.get_workspace_config(NS, WS, CNS, name)
        if got.status_code != 200:
            print(f'  {name}: NOT PRESENT (HTTP {got.status_code}) — refusing to call it valid')
            raise SystemExit(1)
        r = fapi.validate_config(NS, WS, CNS, name)
        d = r.json() if r.status_code == 200 else {'error': r.text[:300]}
        bad = d.get('invalid') or []
        print(f'  {name}: present, {len((d.get("validInputs") or {}) if isinstance(d.get("validInputs"), dict) else [])} '
              f'valid-input keys, {"VALID" if not bad else "INVALID"}'
              + ('' if not bad else '\n' + '\n'.join(f'    {v}' for v in bad[:8])))
        terra.dump(d, os.path.join(OUT, f'single_sample_{name}_validation.json'))
        if bad:
            raise SystemExit(1)


def submit(confirm: bool) -> None:
    if not confirm:
        raise SystemExit('refusing to submit without --confirm (this starts two real single-sample pipelines)')
    for name in ARMS:
        d = terra.submit(NS, WS, CNS, name, ENTITY, ETYPE, None, confirm=True)
        sid = d.get('submissionId') if isinstance(d, dict) else None
        print(f'  submitted {name}: {sid}')
        terra.dump(d, os.path.join(OUT, f'single_sample_{name}_submission.json'))


if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'prep'
    if what == 'prep':
        prep()
    elif what == 'patch-entity':
        patch_entity()
    elif what == 'create':
        create()
    elif what == 'validate':
        validate()
    elif what == 'submit':
        submit('--confirm' in sys.argv)
    else:
        raise SystemExit(__doc__)
