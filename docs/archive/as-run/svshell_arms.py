#!/usr/bin/env python3
"""svshell_arms.py -- baseline-vs-branch method configs for the SVShell (sv-shell image) path.

Why this file exists
--------------------
`wdl/SVShell.wdl` is the only workflow that actually runs the `sv-shell` image, and it is *not*
launchable from a bare input JSON: `task RunSVShell` declares 102 required inputs (103 on the
branch) and the WDL `call` binds only 28 of them. The remaining 74 have to be supplied as
call-scope config keys (`SVShell.RunSVShell.<name>`) -- which is how the real single-sample users'
method configs work. miniwdl reports this as `IncompleteCall`; Cromwell/WOM is more permissive and
resolves those names from the inputs map, so it runs.

Value sources, in the order tried (deliberately NOT a colleague's private workspace buckets):
  1. per-arm overrides           images + the RD table(s), which are the point of the A/B
  2. public/canonical gs paths   taken verbatim from a captured real run's rendered command
  3. the repo's own value files  inputs/values/{resources_hg38,ref_panel_1kg}.json (+ aliases)
  4. the sv_shell fixture        src/sv_shell/sample_inputs/single_sample_pipeline_dragen.json
Anything unresolved is *listed*, never guessed -- a silently missing knob would make the A/B
meaningless.

Subcommands (only the last two mutate, and `submit` refuses without --confirm):
  prep                       print the A/B delta and any unresolved inputs (read-only)
  create                     POST both method configs into my workspace
  validate                   submission-time validation (the only Terra gate that checks completeness)
  submit --confirm           start both arms
"""
from __future__ import annotations
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..', '..'))   # docs/archive/as-run -> repo root
sys.path.insert(0, os.path.join(ROOT, 'terra'))
sys.path.insert(0, os.path.join(ROOT, 'kit'))
import config  # noqa: E402
import terra                        # noqa: E402
import firecloud.api as fapi        # noqa: E402  (pip name `firecloud`)

NS = os.environ['GSVTK_TERRA_NAMESPACE']
WS = os.environ['GSVTK_TERRA_WORKSPACE']
CNS = os.environ.get('GSVTK_BILLING_PROJECT', '<billing-project>')
WDL_PATH = 'github.com/broadinstitute/gatk-sv/SVShell'
ENTITY, ETYPE = 'NA12878', 'sample'
REPO = os.environ.get('GSVTK_GATK_SV_CHECKOUT', '<gatk-sv-checkout>')
CAPTURED = os.environ.get("CAPTURED_SCRIPT") or os.path.join(config.get("WORK") or ".",
                                                             "captured", "runsvshell_script.sh")
REPO = config.get("GATK_SV_CHECKOUT")          # the clone whose refs the two arms point at
FIXTURE = None                                  # set from REPO below, once REPO is known
MINIWDL = os.environ.get("MINIWDL_PYTHON", "python3")
OUT = config.get("WORK")

# The two arms are DATA, not code: they name a Dockstore version, a git ref for its WDL,
# the exact images each arm runs, and the RD tables it is fed. Described in a file
# (arms.example.json) so the same harness runs your pair instead of the pair this session
# happened to use.  --arms / GSVTK_ARMS_JSON selects it; there is no built-in default,
# because a default would silently compare somebody else's two images.
ARMS = {}


def load_arms(path: str) -> dict:
    """Read the arm spec; refuse anything that looks half-filled."""
    with open(path) as fh:
        arms = json.load(fh)
    if not isinstance(arms, dict) or len(arms) < 2:
        raise SystemExit(f"{path}: expected a JSON object naming at least two arms")
    for name, arm in arms.items():
        for field in ("version", "repo_ref", "images"):
            if not arm.get(field):
                raise SystemExit(f"{path}: arm {name!r} has no {field!r}")
        for key, ref in arm["images"].items():
            if "YOUR_" in ref or not ref.startswith("gs://") and not ref.startswith("us.") \
                    and not ref.startswith("gcr.io") and not ref.startswith("docker.io") \
                    and not ref.startswith("marketplace"):
                raise SystemExit(f"{path}: arm {name!r} image {key}={ref!r} is a placeholder")
    return arms


def init_arms(argv):
    global ARMS, FIXTURE
    spec = None
    for i, a in enumerate(argv):
        if a == "--arms" and i + 1 < len(argv):
            spec = argv[i + 1]
    spec = spec or os.environ.get("GSVTK_ARMS_JSON")
    if not spec:
        raise SystemExit("no --arms spec given. Copy examples/arms.example.json, fill in your "
                         "images/tables, then pass --arms <file> (or export GSVTK_ARMS_JSON).")
    ARMS = load_arms(spec)
    if not REPO:
        raise SystemExit("GSVTK_GATK_SV_CHECKOUT is unset: the arms name git refs, and refs mean "
                         "nothing without a repository.")
    FIXTURE = os.path.join(REPO, "src", "sv_shell", "sample_inputs",
                           "single_sample_pipeline_dragen.json")


# A Terra job runs as the requesting user's *pet service account* in that workspace, not as
# anyone who could read the run being replayed. Inputs left pointing at another workspace's
# private bucket therefore fail at localization time, so only these public prefixes are
# referenced in place; everything else is copied into this workspace's own bucket first.
PUBLIC_PREFIXES = ('gs://gatk-sv-resources-public/', 'gs://gatk-sv-ref-panel-1kg-v1-1/',
                   'gs://gcp-public-data--broad-references/')


def dstore(version: str) -> dict:
    return {'sourceRepo': 'dockstore', 'methodPath': WDL_PATH, 'methodVersion': version,
            'methodUri': f"dockstore://{WDL_PATH.replace('/', '%2F')}/{version}"}


def captured_args() -> dict:
    """`--arg name "value"` pairs from a real run's rendered command -- the ground-truth knob set."""
    txt = open(CAPTURED).read()
    out = {}
    for k, v in re.findall(r'--arg\s+([A-Za-z0-9_]+)\s+"((?:[^"\\]|\\.)*)"', txt):
        v = v.replace('\\"', '"').replace('\\\\', '\\')
        if v.startswith('/mnt/disks/cromwell_root/'):                    # un-localize
            v = 'gs://' + v[len('/mnt/disks/cromwell_root/'):]
        out[k] = v
    return out


# `SVShell.RunSVShell.<x>` name -> the key that same asset has in the repo's own value files.
# Verified by basename against the captured real run (`data_vj/runsvshell_script.sh`), not by guess:
# e.g. ref_panel_median_cov wanted basename `all_samples_medianCov.transposed.bed`, which is what
# ref_panel_1kg.json:medianfile points at. Every resolved object is then existence-checked on GCS by
# `verify_gcs()`, so a wrong alias here shows up as "missing on GCS" instead of a failed 4-hour run.
ALIAS = {
    'HERVK_reference': 'hervk_reference', 'LINE1_reference': 'line1_reference',
    'pesr_exclude_intervals': 'pesr_exclude_list',
    'pesr_exclude_intervals_index': 'pesr_exclude_list_index',
    'gq_recalibrator_model_file': 'hgsvc_release3_gq_model_file',
    'qc_definitions': 'single_sample_qc_definitions',
    'clustering_track_bed_files': 'clustering_tracks',
    'genome_tracks': 'recalibrate_gq_genome_tracks',
    'genome_tracks_indices': 'recalibrate_gq_genome_tracks_indices',
    'ref_panel_vcf': 'clean_vcf', 'ref_panel_vcf_index': 'clean_vcf_index',
    'ref_panel_bincov_matrix': 'merged_rd_file', 'ref_panel_bincov_matrix_index': 'merged_rd_file_index',
    'ref_panel_del_bed': 'del_bed', 'ref_panel_dup_bed': 'dup_bed',
    'ref_panel_median_cov': 'medianfile',
    'ref_ped_file': 'ped_file', 'ref_samples_list': 'samples_list',
    'ref_pesr_split_files': 'SR_files', 'ref_pesr_split_files_indices': 'SR_files_index',
    'ref_pesr_split_files_list': 'SR_files_list',
    'ref_pesr_disc_files': 'PE_files', 'ref_pesr_disc_files_indices': 'PE_files_index',
    'ref_pesr_disc_files_list': 'PE_files_list',
    'ref_pesr_sd_files': 'SD_files', 'ref_pesr_sd_files_indices': 'SD_files_index',
    'ref_pesr_sd_files_list': 'SD_files_list',
    'contig_ploidy_model_tar': 'contig_ploidy_model_tar',
}
# Inputs with no file behind them: literal values, each with where the value came from.
EXTRA = {
    # inputs/build/NA12878/terra/GATKSVPipelineSingleSample.json:
    #   "GATKSVPipelineSingleSample.RefineComplexVariants.n_per_split": "15000"
    'refine_complex_variants_n_per_split': 15000,
}
# Per-sample data comes from the Terra entity, exactly as the March config does
# (`GATKSVPipelineSingleSample.bam_or_cram_file = this.bam_or_cram_file`); `~expr~` = emit raw WDL.
ENTITY_EXPR = {'bam_or_cram_file': '~expr~this.bam_or_cram_file',
               'bam_or_cram_index': '~expr~this.bam_or_cram_index',
               # Sample identity must come from the entity too. Without these two the value came from the
               # mini-test fixture and the run would have been labelled `RGP_1153_3` -- caught by
               # comparing every resolved scalar against the captured successful run's rendered args.
               'sample_id': '~expr~this.sample_id',
               'batch': '~expr~this.sample_id'}
# Same-named assets where the repo has more than one and the *single-sample* one is correct: the
# captured real run used `single_sample.qc_definitions.tsv`, while ref_panel_1kg.json's flat
# `qc_definitions` is the batch-mode thresholds (which would silently mis-QC-filter a single sample).
PREFER = {'qc_definitions': 'single_sample_qc_definitions'}


def repo_values() -> dict:
    """inputs/values/*.json flattened to {key: value}.

    These files are FLAT one-level JSON objects whose keys are already the WDL input names
    (resources_hg38.json: 103 keys, ref_panel_1kg.json: 110), and many values are *arrays*. The
    original version of this function assumed a two-level structure (`for grp in d.values(): if
    isinstance(grp, dict)`) and therefore read essentially nothing out of them -- that is why 44
    inputs that the repo states verbatim came out "unresolved".
    """
    vals = {}
    for f in ('resources_hg38.json', 'ref_panel_1kg.json', 'dockers.json'):
        p = os.path.join(REPO, 'inputs/values/' + f)
        if not os.path.exists(p):
            continue
        d = json.load(open(p))
        for k, v in d.items():
            if isinstance(v, dict):                       # a nested group: flatten one level
                for k2, v2 in v.items():
                    if isinstance(v2, (str, int, float, bool, list)):
                        vals.setdefault(k2, v2)
            elif isinstance(v, (str, int, float, bool, list)):
                vals.setdefault(k, v)
    # Canonical ordered track lists the flat files only express per-track keys.
    if 'recalibrate_gq_genome_tracks' not in vals:
        # Order taken from the repo's own production single-sample input
        # (inputs/build/NA12878/terra/GATKSVPipelineSingleSample.json:
        #  RepeatMasker, Segmental-Dups, Simple-Repeats, umap_s100, umap_s24) so the track order --
        # which is a feature order downstream -- matches a real single-sample run, not resources_hg38
        # key order. Same 5 objects either way; verified present on GCS.
        tracks = [t for t in ('repeatmasker', 'segdup', 'simple_repeats', 'umap100', 'umap24')
                  if f'recalibrate_gq_genome_track_{t}' in vals]
        if tracks:
            vals['recalibrate_gq_genome_tracks'] = [vals[f'recalibrate_gq_genome_track_{t}'] for t in tracks]
            vals['recalibrate_gq_genome_tracks_indices'] = [
                vals[f'recalibrate_gq_genome_track_{t}_index'] for t in tracks]
    for k, src in ALIAS.items():                          # alias never overwrites a real same-named key
        if k not in vals and src in vals:
            vals[k] = vals[src]
    for k, src in PREFER.items():                          # ...but PREFER does (single- vs batch-mode)
        if src in vals:
            vals[k] = vals[src]
    return vals


def wdl_literal(v) -> str:
    """Render a Python value as a Rawls input expression.

    Config input values are WDL expressions, not JSON: a string needs quotes, a number/bool must be
    bare, and an array is written as a JSON array literal (`["SR", "SD"]`), which Rawls parses.
    """
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    s = str(v)
    if s.startswith('~expr~'):                       # raw WDL/RAWLS expression, e.g. this.bam_or_cram_file
        return s[len('~expr~'):]
    if re.fullmatch(r'-?\d+(\.\d+)?', s):
        return s
    if s.lower() in ('true', 'false'):
        return s.lower()
    return f'"{s}"'


def required_names(repo_ref: str) -> tuple[set, set, set]:
    """(required workflow inputs, required RunSVShell task inputs, names bound by the WDL `call`)."""
    import tempfile, shutil
    d = tempfile.mkdtemp()
    subprocess.run(['git', '-C', REPO, 'archive', repo_ref, 'wdl'], check=True,
                   stdout=open(os.path.join(d, 'w.tar'), 'wb'))
    subprocess.run(['tar', '-x', '-C', d, '-f', os.path.join(d, 'w.tar')], check=True)
    probe = os.path.join(HERE, '_svshell_probe.py')
    if not os.path.exists(probe):
        raise SystemExit(f'missing {probe}')
    r = subprocess.run([MINIWDL, probe, os.path.join(d, 'wdl/SVShell.wdl')], capture_output=True, text=True)
    shutil.rmtree(d)
    if r.returncode != 0:
        raise SystemExit(r.stderr[:400])
    j = json.loads(r.stdout)
    return set(j['wf_required']), set(j['task_required']), set(j.get('bound_at_call', []))


def verify_gcs(paths: set) -> dict:
    """Existence of every literal gs:// object, via `gsutil -q -m ls` (prints only the ones that exist).

    This is the gate that turns "the value file had a key with that name" into "the run will be able to
    read it". It is what catches the typo in inputs/values/resources_hg38.json (`sl_cutoff_table` is
    `ggs://...`, two g's) and any wrong entry in ALIAS. Results are cached per object.
    """
    cache_p = os.path.join(OUT, 'svshell_gcs_check.json')
    cache = json.load(open(cache_p)) if os.path.exists(cache_p) else {}
    todo = sorted(p for p in paths if p.startswith('gs://') and p not in cache)
    for i in range(0, len(todo), 250):
        chunk = todo[i:i + 250]
        r = subprocess.run(['gsutil', '-q', '-m', 'ls', *chunk], capture_output=True, text=True)
        found = {ln.strip() for ln in r.stdout.splitlines() if ln.startswith('gs://')}
        for p in chunk:
            cache[p] = p in found
    if todo:
        os.makedirs(OUT, exist_ok=True)
        json.dump(cache, open(cache_p, 'w'), indent=0)
    return {p: bool(cache.get(p)) for p in paths if p.startswith('gs://')}


def gs_paths_of(v) -> list:
    """Every gs:// URI inside a resolved value (a bare path, or inside a list/array literal)."""
    if isinstance(v, (list, tuple)):
        return [x for x in v if isinstance(x, str)]
    s = str(v)
    if s.startswith('['):
        try:
            return [x for x in json.loads(s) if isinstance(x, str)]
        except Exception:
            return []
    return [s] if s.startswith('gs://') else []


def build(verbose: bool = True) -> tuple[dict, dict]:
    args = captured_args()
    vals = repo_values()
    fx = json.load(open(FIXTURE))
    bodies, report = {}, {}
    literal = {}
    for name, arm in ARMS.items():
        wf_req, task_req, bound_at_call = required_names(arm['repo_ref'])
        # A task input the workflow body already binds (e.g.
        # `ref_pesr_split_files = read_lines(ref_pesr_split_files_list)` at SVShell.wdl:34/67) must NOT
        # also be given as `SVShell.RunSVShell.<name>`: that is a second definition of the same input,
        # which is a Cromwell input-resolution conflict, not a harmless override. Supply the *workflow*
        # input instead (it is in wf_required) and let the WDL derive the array.
        task_req_needing_config = task_req - bound_at_call
        ins, unresolved = {}, []
        want = {k: v for k, v in list(arm['images'].items()) + list(arm['tables'].items())}
        for key, value in list(want.items()):
            if key in wf_req:
                ins[f'SVShell.{key}'] = f'"{value}"'
            elif key in task_req_needing_config:
                ins[f'SVShell.RunSVShell.{key}'] = f'"{value}"'
        for n in sorted(wf_req | task_req_needing_config):
            scoped = f'SVShell.{n}' if n in wf_req else f'SVShell.RunSVShell.{n}'
            if scoped in ins:
                continue
            if n in ENTITY_EXPR:                                  # per-sample data: read from the entity
                ins[scoped] = wdl_literal(ENTITY_EXPR[n])
                continue
            if n in EXTRA and n not in vals:                       # literal knob, provenance in EXTRA
                ins[scoped] = wdl_literal(EXTRA[n])
                continue
            cand = None
            if n in args and str(args[n]).startswith(PUBLIC_PREFIXES):
                cand = args[n]
            elif n in vals:
                cand = vals[n]
            elif n in fx:
                cand = fx[n]
            elif n.endswith('_index'):
                # Only name in the whole input set that the repo value files do not state. Derived here
                # and then existence-checked like everything else: mei.bed.gz -> <same>.bed.gz.tbi
                # (verified present; .csi is not). An index that did not exist would be reported.
                stem = n[:-len('_index')]
                base = vals.get(stem, args.get(stem))
                if isinstance(base, str) and base.startswith('gs://'):
                    cand = next((s for s in (base + '.tbi', base + '.csi') if verify_gcs({s}).get(s)), None)
            if cand is None:
                unresolved.append(n)
                continue
            # The fixture is the *unit-test* input set: some of its paths are container paths from the
            # mini reference ("/inputs/..."). Those must never reach a real run, so they are refused
            # here and surface in `unresolved` rather than silently becoming wrong inputs.
            probe = json.dumps(cand) if isinstance(cand, (list, dict)) else str(cand)
            if '/inputs/' in probe or '${' in probe:
                unresolved.append(n + '   (refused: mini-test container path or unsubstituted ' 
                                      + '${} ref: ' + probe[:48] + ')')
                if n in ALIAS and vals.get(ALIAS[n]) is not None:   # try the aliased asset instead
                    alt = vals[ALIAS[n]]
                    altprobe = json.dumps(alt) if isinstance(alt, (list, dict)) else str(alt)
                    if '/inputs/' not in altprobe and '${' not in altprobe:
                        unresolved.pop()
                        cand = alt
                    else:
                        continue
                else:
                    continue
            ins[scoped] = wdl_literal(cand)
            literal.setdefault(arm['repo_ref'], {})[n] = cand
        # Existence-check every literal object we are about to hand Cromwell. A missing one is a real
        # input gap (and usually a wrong alias), not something to discover 3 hours into a paid run.
        paths = {p for v in literal.get(arm['repo_ref'], {}).values() for p in gs_paths_of(v)}
        # A URI-ish value with the wrong scheme (inputs/values/ref_panel_1kg.json:2688 ships
        # `ggs://` for sl_cutoff_table) is not a gs:// path, so verify_gcs would never look at it.
        for n, v in sorted(literal.get(arm['repo_ref'], {}).items()):
            s = json.dumps(v) if isinstance(v, (list, dict)) else str(v)
            if '://' in s and not re.match(r'^"?\[?"?(gs|https?)://', s):
                scoped = f'SVShell.{n}' if n in wf_req else f'SVShell.RunSVShell.{n}'
                ins.pop(scoped, None)
                unresolved.append(f'{n}   (MALFORMED URI, not gs://: {s[:60]})')
        paths |= {p for v in arm['tables'].values() for p in gs_paths_of(v)}
        paths |= {p for v in arm['images'].values() if str(v).startswith('gs://')}
        exist = verify_gcs(paths) if paths else {}
        for n, v in sorted(literal.get(arm['repo_ref'], {}).items()):
            missing = [p for p in gs_paths_of(v) if not exist.get(p, True)]
            if missing:
                scoped = f'SVShell.{n}' if n in wf_req else f'SVShell.RunSVShell.{n}'
                ins.pop(scoped, None)
                bad = ','.join(missing[:2]) + ('' if len(missing) == 1 else f' (+{len(missing)-1} more)')
                unresolved.append(f'{n}   (NOT ON GCS: {bad})')
        bodies[name] = {
            'namespace': CNS, 'name': name, 'rootEntityType': ETYPE, 'prerequisites': {},
            'inputs': ins, 'outputs': {}, 'methodRepoMethod': dstore(arm['version']),
            'methodConfigVersion': int(os.environ.get('GSV_CFG_VERSION', '1')), 'deleted': False,
            'deleteIntermediateOutputFiles': False, 'useCallCache': True, 'mode': 'FIXED',
        }
        report[name] = {'wf_required': len(wf_req), 'task_required': len(task_req),
                        'bound_at_call_excluded': sorted(task_req & bound_at_call),
                        'supplied': len(ins), 'unresolved': sorted(unresolved)}
        if verbose:
            print(f'  {name}: wf_required={len(wf_req)} task_required={len(task_req)} '
                  f'(bound at call: {len(task_req & bound_at_call)}, needing config keys: '
                  f'{len(task_req_needing_config)}) supplied={len(ins)} UNRESOLVED={len(unresolved)}')
            for u in sorted(unresolved)[:24]:
                print('     ?', u)
    return bodies, report


def prep() -> None:
    bodies, report = build()
    json.dump(report, open(os.path.join(OUT, 'svshell_arms_report.json'), 'w'), indent=1)
    a, b = bodies['A_svshell_main']['inputs'], bodies['B_svshell_branch']['inputs']
    diff = sorted({k for k in set(a) | set(b) if a.get(k) != b.get(k)})
    print(f'\n  A/B differing keys ({len(diff)}):')
    for k in diff:
        print(f'    {k.replace("SVShell.RunSVShell.", "")}')
        print(f'      A {str(a.get(k, "(absent)"))[:74]}')
        print(f'      B {str(b.get(k, "(absent)"))[:74]}')
    terra.dump(bodies, os.path.join(OUT, 'svshell_arms.json'))
    print(f'  wrote {OUT}/svshell_arms.json')


def create() -> None:
    bodies, _ = build(verbose=False)
    for name, body in bodies.items():
        r = fapi.create_workspace_config(NS, WS, body)
        if r.status_code == 409:
            r = fapi.overwrite_workspace_config(NS, WS, CNS, name, body)
        print(f'  create {name}: HTTP {r.status_code} {"" if r.status_code in (200, 201) else r.text[:220]}')
        if r.status_code not in (200, 201):
            raise SystemExit(1)


def validate() -> None:
    s = terra.session()
    failures = []
    for name in ARMS:
        got = fapi.get_workspace_config(NS, WS, CNS, name)
        if got.status_code != 200:
            print(f'  {name}: NOT PRESENT (HTTP {got.status_code}) -- refusing to call it valid')
            raise SystemExit(1)
        body = {'methodConfigurationNamespace': CNS, 'methodConfigurationName': name,
                'entityType': ETYPE, 'entityName': ENTITY, 'useCallCache': True,
                'deleteIntermediateOutputFiles': False}
        r = s.post(f'https://api.firecloud.org/api/workspaces/{NS}/{WS}/submissions/validate',
                   json=body, timeout=300)
        # The status code IS the verdict: with a required input removed Rawls answers HTTP 400 with an
        # empty-ish body, so a check that only greps the JSON for "Missing inputs:" reports CLEAN for a
        # config it just rejected. Require 200 AND no invalidEntities AND no invalid[] entries.
        payload = r.json() if r.content else {}
        bad = (payload.get('invalidEntities') or []) or (payload.get('invalid') or [])
        miss = re.search(r'Missing inputs: ([^"]*)', json.dumps(payload))
        verdict = ('CLEAN' if r.status_code == 200 and not bad and not miss
                   else f'NOT CLEAN (HTTP {r.status_code}) ' + (miss.group(1)[:110] if miss else
                                                                json.dumps(payload)[:160]))
        print(f'  {name}: submit-validate -> HTTP {r.status_code} {verdict}')
        terra.dump(payload, os.path.join(OUT, f'svshell_{name}_submitvalidate.json'))
        if verdict != 'CLEAN':
            failures.append(name)
    if failures:
        raise SystemExit('submit-validate failed for: ' + ', '.join(failures))


def submit(confirm: bool) -> None:
    if not confirm:
        raise SystemExit('refusing to submit without --confirm (two real single-sample runs)')
    for name in ARMS:
        d = terra.submit(NS, WS, CNS, name, ENTITY, ETYPE, None, confirm=True)
        print(f'  submitted {name}: {d.get("submissionId")}')
        terra.dump(d, os.path.join(OUT, f'svshell_{name}_submission.json'))


if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'prep'
    {'prep': prep, 'create': create, 'validate': validate,
     'submit': lambda: submit('--confirm' in sys.argv)}[what]()
