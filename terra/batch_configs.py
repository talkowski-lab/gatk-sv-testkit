#!/usr/bin/env python3
"""Create + validate the 06->10 joint-calling method configs for a head-to-head.

Every config runs the *branch* WDL (dockstore://github.com/broadinstitute/gatk-sv/<W>/
<GSVTK_BRANCH>) against the branch images, and writes its results into NEW entity
attributes, so the baseline attributes your clone inherited are never clobbered and
both sides stay addressable inside one workspace:

    *{FROZEN_SUFFIX}   frozen baseline upstream inputs, copied into the sandbox
                       bucket by batch_freeze.py
    *{NEW_SUFFIX}      outputs of this chain

Steps 06->10 are the batch half of the joint-calling chain (GenerateBatchMetrics,
FilterBatchSites, FilterBatchSamples, MergeBatchSites, GenotypeBatch). Extend CONFIGS
to cover other steps; the shape of every entry is the same.

    python terra/batch_configs.py show                  # print the maps (offline, no auth)
    python terra/batch_configs.py check --against main  # keys vs that ref's WDL (offline)
    python terra/batch_configs.py create                # POST the configs (mutation)
    python terra/batch_configs.py validate              # Terra-side WDL validation

The input maps are a SNAPSHOT of one branch's WDL signature, while GSVTK_BRANCH only chooses the
Dockstore URL. Point the URL at a ref the maps were not written against and they carry keys that ref
never declared -- which Rawls rejects as an extra input at submission. That and a `Cannot get
dockstore://... from method repo` 404 are the same defect seen twice: one map, two refs. `check` is
the offline form of `validate` (it reads your own checkout with miniwdl, so it does not need the ref
published on Dockstore), and `create`/`validate` run it before doing anything else.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
sys.path.insert(0, HERE)
import config  # noqa: E402
import terra  # noqa: E402
from terra import fapi  # noqa: E402  # via terra: one friendly missing-dependency message

# The workspace is where results are written; the branch is what is under test.
# Both are stated, never guessed -- see docs/config.md.
# Read leniently at import so `show` and `--help` work with an empty profile; the
# commands that actually talk to Terra call require_target() and get the written fix.
NS = config.get("TERRA_NAMESPACE")
WS = config.get("TERRA_WORKSPACE")
BRANCH = config.get("BRANCH")


def require_target(writes=False):
    """Resolve (NS, WS, BRANCH) or exit 4 naming the profile key that is missing.

    `writes=True` also refuses the shared baseline workspace (see
    terra.assert_writable_target). An unset target used to reach the HTTP layer as an empty path
    segment -- `POST /api/workspaces//methodconfigs` -- which is a 405 from the edge instead of a
    clear message from this tool, and which really happened to a reviewer probing this code path.
    """
    global NS, WS, BRANCH
    NS = NS or config.require("TERRA_NAMESPACE", "where these configs are POSTed")
    WS = WS or config.require("TERRA_WORKSPACE", "where these configs are POSTed")
    BRANCH = BRANCH or config.require("BRANCH", "the gatk-sv branch whose WDL each config runs")
    if writes:
        terra.assert_writable_target(NS, WS, "create or overwrite method configs",
                                    allow="--allow-shared-target" in sys.argv)
    return NS, WS, BRANCH

# Attribute suffixes: baseline inputs in, this chain's outputs out.
FZ = "_" + config.get("FROZEN_SUFFIX", "frz")
NW = "_" + config.get("NEW_SUFFIX", "new")
# A path, not a directory: `show` and `--help` import this module and must create nothing.
# create() makes the directory when it actually writes.
DUMP = str(config.work_path("manifests") / "batch_configs.json")


# Inputs that exist only on the branch under test. Data, not prose, so `check` can distinguish
# "known branch-only input" from "this key is news to us", and so the fix is written next to the key:
#
#   GenotypeBatch.training_vcf   from "Train PE/SR genotyping on a separate batch-level VCF". main's
#                                GenotypeBatch declares 21 inputs (18 + 3 dockers) and trains PE/SR
#                                from `vcf` itself; the branch declares 26, adding training_vcf,
#                                genotype_args, training_args, n_RD_genotype_bins and
#                                fail_on_degenerate_sr_cutoffs. This map binds only training_vcf --
#                                the other four have WDL defaults -- so it is the single key a
#                                main-shaped run has to drop. It was posted against a main-derived
#                                ref and rejected as an extra input; that is what happened, and this
#                                table is why `check` can name it instead of just failing.
BRANCH_ONLY_INPUTS: dict[str, set[str]] = {
    "10-GenotypeBatch": {"GenotypeBatch.training_vcf"},
}


def dockstore(workflow: str) -> dict:
    if not BRANCH:
        config.require("BRANCH", "the Dockstore version each config pins")
    path = f"github.com/broadinstitute/gatk-sv/{workflow}"
    return {"sourceRepo": "dockstore", "methodPath": path, "methodVersion": BRANCH,
            "methodUri": f"dockstore://{path.replace('/', '%2F')}/{BRANCH}"}


# --------------------------------------------------------------------------------------
# Input maps follow inputs/templates/terra_workspaces/cohort_mode/workflow_configurations/*
# on the branch, with frozen inputs read from *_frz and chain outputs from *_new.
# Optional (File?) inputs with no value are simply omitted.
# --------------------------------------------------------------------------------------
CALLERS = ["manta", "wham", "scramble"]  # dragen/melt produced nothing in this cohort

CONFIGS = {
    "06-GenerateBatchMetrics": {
        "workflow": "GenerateBatchMetrics",
        "rootEntityType": "sample_set",
        "inputs": {
            "GenerateBatchMetrics.batch": "this.sample_set_id",
            "GenerateBatchMetrics.pe_file": f"this.merged_PE{FZ}",
            "GenerateBatchMetrics.sr_file": f"this.merged_SR{FZ}",
            "GenerateBatchMetrics.baf_file": f"this.merged_BAF{FZ}",
            "GenerateBatchMetrics.rd_file": f"this.merged_bincov{FZ}",
            "GenerateBatchMetrics.median_file": f"this.median_cov{FZ}",
            "GenerateBatchMetrics.ped_file": "workspace.cohort_ped_file",
            "GenerateBatchMetrics.depth_vcf": f"this.clustered_depth_vcf{FZ}",
            **{f"GenerateBatchMetrics.{c}_vcf": f"this.clustered_{c}_vcf{FZ}" for c in CALLERS},
            "GenerateBatchMetrics.primary_contigs_list": "workspace.primary_contigs_list",
            "GenerateBatchMetrics.chr_x": "workspace.chr_x",
            "GenerateBatchMetrics.chr_y": "workspace.chr_y",
            "GenerateBatchMetrics.rmsk": "workspace.rmsk",
            "GenerateBatchMetrics.segdups": "workspace.segdups",
            "GenerateBatchMetrics.reference_dict": "workspace.reference_dict",
            "GenerateBatchMetrics.gatk_docker": "workspace.gatk_docker",
            "GenerateBatchMetrics.sv_pipeline_docker": "workspace.sv_pipeline_docker",
            "GenerateBatchMetrics.sv_base_mini_docker": "workspace.sv_base_mini_docker",
        },
        "outputs": {
            "GenerateBatchMetrics.metrics": f"this.metrics{NW}",
            "GenerateBatchMetrics.metrics_file_batchmetrics": f"this.metrics_file_batchmetrics{NW}",
            "GenerateBatchMetrics.ploidy_table": f"this.ploidy_table{NW}",
        },
    },
    "07-FilterBatchSites": {
        "workflow": "FilterBatchSites",
        "rootEntityType": "sample_set",
        "inputs": {
            "FilterBatchSites.batch": "this.sample_set_id",
            "FilterBatchSites.evidence_metrics": f"this.metrics{NW}",
            "FilterBatchSites.depth_vcf": f"this.clustered_depth_vcf{FZ}",
            **{f"FilterBatchSites.{c}_vcf": f"this.clustered_{c}_vcf{FZ}" for c in CALLERS},
            "FilterBatchSites.sv_pipeline_docker": "workspace.sv_pipeline_docker",
            "FilterBatchSites.N_IQR_cutoff_plotting": "6",
        },
        "outputs": {
            "FilterBatchSites.sites_filtered_depth_vcf": f"this.sites_filtered_depth_vcf{NW}",
            "FilterBatchSites.sites_filtered_manta_vcf": f"this.sites_filtered_manta_vcf{NW}",
            "FilterBatchSites.sites_filtered_scramble_vcf": f"this.sites_filtered_scramble_vcf{NW}",
            "FilterBatchSites.sites_filtered_wham_vcf": f"this.sites_filtered_wham_vcf{NW}",
            "FilterBatchSites.cutoffs": f"this.cutoffs{NW}",
            "FilterBatchSites.scores": f"this.scores{NW}",
            "FilterBatchSites.RF_intermediate_files": f"this.RF_intermediate_files{NW}",
            "FilterBatchSites.sites_filtered_sv_counts": f"this.sites_filtered_sv_counts{NW}",
            "FilterBatchSites.sites_filtered_sv_count_plots": f"this.sites_filtered_sv_count_plots{NW}",
            "FilterBatchSites.sites_filtered_outlier_samples_preview": f"this.sites_filtered_outlier_samples_preview{NW}",
            "FilterBatchSites.sites_filtered_outlier_samples_with_reason": f"this.sites_filtered_outlier_samples_with_reason{NW}",
            "FilterBatchSites.sites_filtered_num_outlier_samples": f"this.sites_filtered_num_outlier_samples{NW}",
        },
    },
    "08-FilterBatchSamples": {
        "workflow": "FilterBatchSamples",
        "rootEntityType": "sample_set",
        "inputs": {
            "FilterBatchSamples.batch": "this.sample_set_id",
            "FilterBatchSamples.N_IQR_cutoff": "10000",
            "FilterBatchSamples.depth_vcf": f"this.sites_filtered_depth_vcf{NW}",
            **{f"FilterBatchSamples.{c}_vcf": f"this.sites_filtered_{c}_vcf{NW}" for c in CALLERS},
            "FilterBatchSamples.sv_pipeline_docker": "workspace.sv_pipeline_docker",
            "FilterBatchSamples.sv_base_mini_docker": "workspace.sv_base_mini_docker",
            "FilterBatchSamples.linux_docker": "workspace.linux_docker",
        },
        "outputs": {
            "FilterBatchSamples.outlier_filtered_depth_vcf": f"this.outlier_filtered_depth_vcf{NW}",
            "FilterBatchSamples.outlier_filtered_depth_vcf_index": f"this.outlier_filtered_depth_vcf_index{NW}",
            "FilterBatchSamples.outlier_filtered_manta_vcf": f"this.outlier_filtered_manta_vcf{NW}",
            "FilterBatchSamples.outlier_filtered_scramble_vcf": f"this.outlier_filtered_scramble_vcf{NW}",
            "FilterBatchSamples.outlier_filtered_wham_vcf": f"this.outlier_filtered_wham_vcf{NW}",
            "FilterBatchSamples.outlier_filtered_pesr_vcf": f"this.outlier_filtered_pesr_vcf{NW}",
            "FilterBatchSamples.outlier_filtered_pesr_vcf_index": f"this.outlier_filtered_pesr_vcf_index{NW}",
            "FilterBatchSamples.filtered_batch_samples_file": f"this.filtered_batch_samples_file{NW}",
            "FilterBatchSamples.outlier_samples_excluded_file": f"this.outlier_samples_excluded_file{NW}",
        },
    },
    "09-MergeBatchSites": {
        "workflow": "MergeBatchSites",
        "rootEntityType": "sample_set_set",
        "inputs": {
            "MergeBatchSites.cohort": "this.sample_set_set_id",
            "MergeBatchSites.pesr_vcfs": f"this.sample_sets.outlier_filtered_pesr_vcf{NW}",
            "MergeBatchSites.depth_vcfs": f"this.sample_sets.outlier_filtered_depth_vcf{NW}",
            "MergeBatchSites.ploidy_tables": f"this.sample_sets.ploidy_table{NW}",
            "MergeBatchSites.reference_fasta": "workspace.reference_fasta",
            "MergeBatchSites.reference_fasta_fai": "workspace.reference_index",
            "MergeBatchSites.reference_dict": "workspace.reference_dict",
            "MergeBatchSites.sv_base_mini_docker": "workspace.sv_base_mini_docker",
            "MergeBatchSites.sv_pipeline_docker": "workspace.sv_pipeline_docker",
            "MergeBatchSites.gatk_docker": "workspace.gatk_docker",
        },
        "outputs": {
            "MergeBatchSites.merge_batch_sites_vcf": f"workspace.merge_batch_sites_vcf{NW}",
            "MergeBatchSites.merge_batch_sites_vcf_index": f"workspace.merge_batch_sites_vcf_index{NW}",
        },
    },
    "10-GenotypeBatch": {
        "workflow": "GenotypeBatch",
        "rootEntityType": "sample_set",
        "inputs": {
            "GenotypeBatch.batch": "this.sample_set_id",
            # sites to genotype = PESR+depth merged by step 09 (production wiring);
            # PE/SR *training* sites = this batch's own filtered PESR VCF from step 08
            "GenotypeBatch.vcf": f"workspace.merge_batch_sites_vcf{NW}",
            "GenotypeBatch.training_vcf": f"this.outlier_filtered_pesr_vcf{NW}",
            "GenotypeBatch.rf_cutoffs": f"this.cutoffs{NW}",
            "GenotypeBatch.median_coverage": f"this.median_cov{FZ}",
            "GenotypeBatch.rd_file": f"this.merged_bincov{FZ}",
            "GenotypeBatch.pe_file": f"this.merged_PE{FZ}",
            "GenotypeBatch.sr_file": f"this.merged_SR{FZ}",
            "GenotypeBatch.reference_dict": "workspace.reference_dict",
            "GenotypeBatch.training_intervals": "workspace.depth_training_bed",
            "GenotypeBatch.ploidy_table": f"this.ploidy_table{NW}",
            "GenotypeBatch.depth_exclusion_intervals": "workspace.bin_exclude",
            "GenotypeBatch.pesr_exclusion_intervals": "workspace.pesr_exclude_list",
            "GenotypeBatch.contig_list": "workspace.primary_contigs_list",
            "GenotypeBatch.gatk_docker": "workspace.gatk_docker",
            "GenotypeBatch.sv_base_mini_docker": "workspace.sv_base_mini_docker",
            "GenotypeBatch.sv_pipeline_docker": "workspace.sv_pipeline_docker",
            # n_RD_genotype_bins (100000) and fail_on_degenerate_sr_cutoffs (true) come from
            # the WDL defaults - the point of the branch is that these are now correct by default.
        },
        "outputs": {
            "GenotypeBatch.genotyped_depth_vcf": f"this.genotyped_depth_vcf{NW}",
            "GenotypeBatch.genotyped_depth_vcf_index": f"this.genotyped_depth_vcf_index{NW}",
            "GenotypeBatch.genotyped_pesr_vcf": f"this.genotyped_pesr_vcf{NW}",
            "GenotypeBatch.genotyped_pesr_vcf_index": f"this.genotyped_pesr_vcf_index{NW}",
            "GenotypeBatch.genotyping_rd_depth_table": f"this.genotyping_rd_depth_table{NW}",
            "GenotypeBatch.genotyping_rd_pesr_table": f"this.genotyping_rd_pesr_table{NW}",
            "GenotypeBatch.genotyping_pe_table": f"this.genotyping_pe_table{NW}",
            "GenotypeBatch.genotyping_sr_table": f"this.genotyping_sr_table{NW}",
            "GenotypeBatch.genotyping_sr_cutoff_diagnostics": f"this.genotyping_sr_cutoff_diagnostics{NW}",
            "GenotypeBatch.regeno_coverage_medians": f"this.regeno_coverage_medians{NW}",
        },
    },
}


# `--drop-branch-only-inputs`: build a ref-shaped config by removing bindings the ref does not
# declare, INSTEAD of failing. Deliberately opt-in, and deliberate about being loud:
#
#   Dropping `GenotypeBatch.training_vcf` is not cosmetic. On the branch, GenotypeBatch is handed
#   this batch's own filtered PESR VCF as its PE/SR training sites; on main it trains from `vcf`
#   itself. Both are coherent pipelines -- they are different pipelines. A tool that quietly picked
#   one would produce a green run of the other one, which is the failure this repo names in
#   batch_rerun_step's own docstring.
#
#   So the flag is only honoured when the ref can actually be READ (a checkout + a ref that resolves
#   + miniwdl). Unverifiable means we do not drop, because "I could not check" is not evidence that
#   the key is absent. And it prints to stderr, so `batch_rerun_step.py show | jq` stays valid JSON.
DROP_FLAG = "--drop-branch-only-inputs"


def _adapt_inputs(name: str, spec: dict, inputs: dict) -> dict:
    """Remove known-branch-only bindings the target ref does not declare. Prints what it dropped."""
    known = BRANCH_ONLY_INPUTS.get(name, set())
    candidates = {k: v for k, v in inputs.items() if k in known}
    if not candidates or DROP_FLAG not in sys.argv:
        return inputs
    # The SAME ref preflight() grades, from one place, so the guard and the body cannot be pointed at
    # two documents. _posted_ref() refuses rather than guessing when --against names a third one, and
    # it belongs here as much as in preflight(): by this point the message the user is reading already
    # names the key it is removing.
    ref = _posted_ref()
    got = declared_for_config(name, ref)
    if got is None:
        print(f"{DROP_FLAG} ignored for {name}: cannot read the WDL at "
              f"{ref or 'GSVTK_BRANCH (unset)'}, and an unverified ref is not evidence that a "
              f"binding is absent.\n  Set GSVTK_GATK_SV_CHECKOUT and a resolvable GSVTK_BRANCH, or "
              f"post it as-is and accept the rejection.", file=sys.stderr)
        return inputs
    declared = got[0]
    dropped = [k for k in sorted(candidates) if k.split(".")[-1] not in declared]
    for k in dropped:
        del inputs[k]
    if dropped:
        print(f"{DROP_FLAG}: dropped {len(dropped)} binding(s) from {name} that "
              f"{ref} does not declare:", file=sys.stderr)
        for k in dropped:
            print(f"  - {k}", file=sys.stderr)
        print(f"  This is a SEMANTIC change, not a fix: main's GenotypeBatch trains PE/SR from `vcf`, "
              f"the\n  branch from a separate training VCF. You asked for {ref}, which is NOT the ref "
              f"these\n  maps were written against -- if you meant to run your own branch, unset "
              f"GSVTK_BRANCH instead\n  of using this flag. What you see in `show` is what gets "
              f"POSTed.", file=sys.stderr)
    return inputs


def body(name: str) -> dict:
    spec = CONFIGS[name]
    # dict(): spec["inputs"] IS the module-level table. Pruning without copying would delete the key
    # from CONFIGS for the rest of the process -- so `show` after one adapted `create` would report a
    # 16-input map even when run without the flag, and the branch-only table would no longer match
    # the map it sits beside.
    inputs = _adapt_inputs(name, spec, dict(spec["inputs"]))
    return {"namespace": NS, "name": name, "rootEntityType": spec["rootEntityType"],
            "methodRepoMethod": dockstore(spec["workflow"]),
            # Rawls rejects the body without it (400 "missing required member
            # 'methodConfigVersion'"); the server bumps it on every overwrite.
            "methodConfigVersion": 1,
            "deleted": False,  # also required by Rawls (400 "missing required member 'deleted'")
            "inputs": inputs, "outputs": spec["outputs"],
            "prerequisites": {}, "deleteIntermediateOutputFiles": False,
            "useCallCache": True, "maxMessageSize": None}


def show():
    for name in CONFIGS:
        b = body(name)
        print(f"===== {name}  [{b['rootEntityType']}]  {b['methodRepoMethod']['methodUri']}")
        for k, v in sorted(b["inputs"].items()):
            print(f"   in  {k:52s} = {v}")
        for k, v in sorted(b["outputs"].items()):
            print(f"   out {k:52s} -> {v}")


# ------------------------------------------------------------------ WDL-vs-map check
def _flag_value(flag: str) -> str | None:
    a = sys.argv
    i = a.index(flag) if flag in a else -1
    return a[i + 1] if i >= 0 and len(a) > i + 1 else None


_WDLCACHE: dict[str, str] = {}


def _wdl_dir_from_ref(ref: str) -> str:
    """Materialize <checkout>/wdl at <ref> into a temp dir. Read-only by construction.

    `git archive` only: it cannot touch the checkout's working tree, index or HEAD -- the same
    discipline scripts/fetch_wdl.py keeps, for the same reason (docs/static-checks.md).

    Raises CannotCheck (not SystemExit) because body() consults it while building one config, and a
    ref that cannot be read must make THAT config unverifiable rather than kill the whole run. Cached
    per ref: `create` asks for five configs and would otherwise run five `git archive`s.
    """
    if ref in _WDLCACHE:
        return _WDLCACHE[ref]
    ck = config.get("GATK_SV_CHECKOUT")
    if not ck or not os.path.isdir(ck):
        raise CannotCheck("no GSVTK_GATK_SV_CHECKOUT checkout to read the WDL from")
    tmp = tempfile.mkdtemp(prefix="gsvtk-wdl-")
    atexit.register(shutil.rmtree, tmp, True)
    r = subprocess.run(["git", "-C", ck, "archive", ref, "wdl"], capture_output=True)
    if r.returncode:
        why = (r.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise CannotCheck(f"git -C {ck} archive {ref} wdl: {(why or ['?'])[:1][0]}")
    if subprocess.run(["tar", "-x", "-C", tmp], input=r.stdout).returncode:
        raise CannotCheck("failed to unpack the WDL tree from git archive")
    _WDLCACHE[ref] = os.path.join(tmp, "wdl")
    return _WDLCACHE[ref]


class CannotCheck(Exception):
    """This one config could not be compared. Reported as a finding, never as a pass -- a checker
    that skips a workflow and still says 'clean' is the failure mode this repo keeps meeting."""


# Which ref a config's bindings should be compared to. Empty means GSVTK_BRANCH. Set it when the
# config points somewhere else: batch_rerun_step.py honours GSV_WDL_VERSION, so its Dockstore pin can
# legitimately differ from GSVTK_BRANCH -- checking BRANCH there would be a guard pointed at a ref
# nothing is about to run, i.e. a confident pass about the wrong document.
VERIFY_REF = ""


def _verify_ref() -> str:
    return VERIFY_REF or BRANCH


def _posted_ref(tag: str = "") -> str:
    """The one ref the guard and the body may read -- or a refusal, never two answers.

    `--against` belongs to `check`: that command POSTs nothing, and asking about a ref you chose is the
    whole reason it exists. On a command that POSTs it was a defect rather than a convenience.
    `preflight()` graded `_flag_value("--against")` while `_adapt_inputs()` graded `_verify_ref()`, so
    `create --confirm --against <a> --drop-branch-only-inputs` let the guard pass -- with total
    confidence -- the pruned map of ref `a` while `body()` built and POSTed the map of
    `GSVTK_BRANCH`/`GSV_WDL_VERSION`, which is the Dockstore pin inside that very config and so the WDL
    Terra will actually run. A guard passing about a document nothing runs is the failure this file
    already refuses elsewhere by carrying VERIFY_REF; with the drop flag it is worse than a wrong pass,
    because the guard prints `DROPPED for this ref ... this is what body() posts` about a body that is
    not the one being POSTed, and nothing but Terra can reveal the difference.
    """
    ref = _verify_ref()
    against = _flag_value("--against")
    if against and against != ref:
        prefix = f"{tag}: " if tag else ""
        raise SystemExit(
            f"{prefix}refusing -- --against {against} is not the WDL this command runs "
            f"({ref or 'GSVTK_BRANCH unset'}),\n"
            "  and every config it POSTs pins its Dockstore version to that ref. Grading another ref "
            "can only\n  produce a confident pass about a document nothing is about to run, and with "
            f"{DROP_FLAG}\n  it prunes by one ref while posting the other.\n"
            f"  To post {against}'s shape: GSVTK_BRANCH={against} (that pins Dockstore there too).\n"
            f"  To see that ref's findings without posting: check --against {against} {DROP_FLAG}.")
    return ref


def declared_for_config(name: str, ref: str | None = None):
    """(declared, required) for one config's workflow at `ref` (default: what it will run).

    None when it cannot be established. Callers must treat None as 'unknown', which is NOT 'fits':
    dropping a binding you never verified is how a main-shaped run happens by accident.
    """
    ref = ref or _verify_ref()
    if not ref:
        return None
    try:
        return _declared_inputs(_wdl_dir_from_ref(ref), CONFIGS[name]["workflow"])
    except CannotCheck:
        return None


def _declared_inputs(wdl_dir: str, workflow: str):
    """(declared, required) input names for one workflow, from miniwdl -- not from a regex.

    Regex is how you get a confident wrong answer here: a first attempt at this check reported 13
    unknown bindings against main because its regex missed whole `input {}` blocks, and main really
    has exactly one. miniwdl also resolves imports, so an input sourced from another file is read
    as declared rather than flagged as extra.
    """
    try:
        import WDL                                   # the miniwdl *package*, not the CLI
    except ImportError:
        raise SystemExit(
            f"check needs the miniwdl package in the interpreter running this tool (it is "
            f"{sys.executable}):\n"
            "    ./.venv/bin/python terra/batch_configs.py check --against <ref>\n"
            "  or: python -m pip install -r requirements-dev.txt") from None
    f = next((os.path.join(wdl_dir, sub, f"{workflow}.wdl")
              for sub in ("", "wdl") if os.path.isfile(os.path.join(wdl_dir, sub, f"{workflow}.wdl"))),
             None)
    if not f:
        raise CannotCheck(f"wdl/{workflow}.wdl is not in this tree")
    try:
        doc = WDL.load(f, path=[os.path.dirname(f)])
    except Exception as e:                           # a WDL that will not load is the finding
        raise CannotCheck(f"miniwdl could not load it: {type(e).__name__}: {str(e)[:160]}") from None
    wf = getattr(doc, "workflow", None) or getattr(doc, "wf", None)
    if wf is None or wf.name != workflow:
        raise CannotCheck(f"{f} declares no workflow named {workflow}")
    # `type.optional` is the flag (miniwdl's Decl has no `.optional`); `expr is None` means no
    # default, so required = declared without `?` and without `= ...`.
    declared = {str(d.name) for d in wf.inputs}
    required = {str(d.name) for d in wf.inputs if not d.type.optional and d.expr is None}
    return declared, required


def check_maps(wdl_dir: str, only: str | None = None, drop: bool = False, ref: str = "") -> int:
    """Every bound key vs what that ref declares, and every required input vs what is bound.

    `drop` grades the map `body()` would actually POST. With `--drop-branch-only-inputs`,
    `_adapt_inputs()` removes known-branch-only bindings the ref does not declare, so comparing the
    raw table here refused -- with total confidence -- a document Rawls accepts: `show` printed a
    16-key body, and `create` on the same command line refused to POST it while naming the key it had
    just dropped. Same rule, same `BRANCH_ONLY_INPUTS` table, same declared set. It can only fire
    here because `declared` came from miniwdl: the `CannotCheck` branch above already aborted when the
    ref could not be read, and an unverified ref is not evidence that a binding is absent.

    `ref` is only a label, but the label is the claim: `check --against <a>` grades `<a>`, while
    `create`/`validate` grade the ref their own config runs (`_posted_ref`). Both print it, so
    "graded" never silently means "graded against something other than what you asked for".
    """
    bad = 0
    label = ref or _verify_ref() or "the ref under test"
    for name, spec in CONFIGS.items():
        if only and name != only:
            continue
        try:
            declared, required = _declared_inputs(wdl_dir, spec["workflow"])
        except CannotCheck as e:
            bad += 1
            print(f"  BAD {name:<24} CANNOT CHECK: {e}")
            print("        unverified is not verified: this config's bindings were not compared to "
                  "anything.")
            continue
        bound, nested = {}, []
        for k in spec["inputs"]:
            parts = k.split(".")
            (nested if len(parts) > 2 else bound).update({parts[-1]: k})
        # The same rule _adapt_inputs() applies, so the guard and the body cannot disagree.
        dropped = []
        if drop:
            dropped = [key for leaf, key in sorted(bound.items())
                       if leaf not in declared and key in BRANCH_ONLY_INPUTS.get(name, set())]
            for key in dropped:
                bound.pop(key.split(".")[-1], None)
        unknown = [k for k in sorted(bound) if k not in declared]
        unbound = sorted(k for k in required if k not in bound)
        print(f"  {'BAD' if unknown or unbound else 'ok '} {name:<24} "
              f"{len(bound)} bound vs {len(declared)} declared"
              + (f", {len(nested)} nested-call binding(s) not checked" if nested else "")
              + (f", {len(dropped)} dropped by {DROP_FLAG}" if dropped else ""))
        for key in dropped:
            print(f"      DROPPED for {label}  {key} -- a known branch-only input that ref does not "
                  f"declare.\n        {DROP_FLAG} is set, so the guard graded the map with it REMOVED "
                  f"-- the same rule\n        `_adapt_inputs()` applies to the body it builds at that "
                  f"ref. SEMANTIC change, not a fix:\n        see the warning `_adapt_inputs()` "
                  f"prints.")
        for k in unknown:
            bad += 1
            key = bound[k]
            # Matched on the FULL key ("GenotypeBatch.training_vcf"), which is how the table is
            # written. Matching the bare name meant the known-branch-only branch never fired and
            # every explanation below read as "we have never seen this input".
            known = key in BRANCH_ONLY_INPUTS.get(name, set())
            print(f"      EXTRA  {key}")
            if known:
                print("        a KNOWN branch-only input: declared on the branch under test, absent "
                      "from the ref\n        you checked. This is not a surprise, it is a mismatch "
                      "between the map's snapshot and the ref.")
            print("        Rawls rejects the whole config as an extra input at SUBMISSION, so it "
                  "would sit in\n        the workspace looking created until someone submitted it. "
                  "Point GSVTK_BRANCH at\n        a ref that declares it, or drop the key from "
                  f"CONFIGS[{name!r}]['inputs'].")
        for k in unbound:
            bad += 1
            print(f"      MISSING {spec['workflow']}.{k} -- required by the WDL, bound by nothing, "
                  f"no default to fall back on")
    if bad:
        print(f"check: {bad} problem(s). Either the ref is not the one these maps were written "
              f"against\nor the maps are stale. `validate` asks Terra the same question but needs "
              f"the ref published on\nDockstore; this needs only your checkout.")
        return 1
    print("check: clean -- every bound key is declared, and nothing the WDL requires is unbound.")
    return 0


def cmd_check() -> int:
    ref = _flag_value("--against") or BRANCH
    wd = _flag_value("--wdl-dir")
    if not wd:
        if not ref:
            raise SystemExit("check needs a ref: --against <ref|branch|sha>, or GSVTK_BRANCH.\n"
                             "  --wdl-dir <dir>/wdl also works, and is the way to check a dirty tree.")
        try:
            wd = _wdl_dir_from_ref(ref)
        except CannotCheck as e:
            # A ref you cannot read is not a ref that fits: say what failed, and do not exit 0.
            raise SystemExit(f"cannot read the WDL at {ref}: {e}") from None
        print(f"WDL read from {config.get('GATK_SV_CHECKOUT')} @ {ref}")
    return check_maps(wd, only=_flag_value("--config"), drop=DROP_FLAG in sys.argv, ref=ref)


def preflight(tag: str) -> None:
    """The same comparison, before anything that POSTs or burns a validation round-trip."""
    # Resolved BEFORE the override is honoured: with --allow-unknown-inputs nothing gets compared, but
    # body() still prunes by the ref its config runs, so an --against pointing elsewhere is still a
    # command that means two different things and must not reach the network.
    ref = _posted_ref(tag)
    if "--allow-unknown-inputs" in sys.argv:
        print(f"{tag}: OVERRIDE --allow-unknown-inputs taken: bindings are NOT compared to the WDL. "
              f"A config\n      Terra rejects as an extra input will still be created, and will fail "
              f"at submission.")
        return
    ck = config.get("GATK_SV_CHECKOUT")
    if not ref or not ck or not os.path.isdir(ck):
        # Stated, never silent: this is the reason a wrong map used to reach submission at all.
        print(f"{tag}: pre-check SKIPPED -- need a ref ({ref or 'unset'}) and a checkout "
              f"({ck or 'unset'}).\n      Not a pass:  python terra/batch_configs.py check "
              f"--against <ref>")
        return
    # Name the ref that was compared, because for a rerun it is the Dockstore pin (GSV_WDL_VERSION),
    # not necessarily GSVTK_BRANCH -- a pass against the wrong ref is worse than no check.
    extra = "" if ref == BRANCH else f" (GSVTK_BRANCH is {BRANCH or 'unset'})"
    print(f"{tag}: comparing every binding against the gatk-sv WDL at {ref}{extra}")
    try:
        wd = _wdl_dir_from_ref(ref)
    except CannotCheck as e:
        raise SystemExit(f"{tag}: refusing to continue -- cannot read the WDL at {ref}: {e}.\n"
                         f"  Unverified is not verified. Fix the ref/checkout, or say you mean it "
                         f"with --allow-unknown-inputs.") from None
    if check_maps(wd, drop=DROP_FLAG in sys.argv, ref=ref):
        raise SystemExit(f"{tag}: refusing to continue -- these maps do not fit the WDL at {ref}.\n"
                         f"  Fix the ref, fix the map, or say you mean it with --allow-unknown-inputs.")


def create():
    preflight("create")                    # offline: a config Terra will reject must not be POSTed
    out = {}
    for name in CONFIGS:
        b = body(name)
        r = fapi.create_workspace_config(NS, WS, b)
        if r.status_code == 409:
            r = fapi.overwrite_workspace_config(NS, WS, NS, name, b)
        ok = r.status_code in (200, 201)
        print(f"{name}: create HTTP {r.status_code}{'' if ok else ' ' + r.text[:300]}")
        out[name] = r.json() if ok else {"error": r.text}
    config.work_dir("manifests")        # the write makes its own directory
    json.dump(out, open(DUMP, "w"), indent=1)
    print("->", DUMP)
    if not all(v.get("name") for v in out.values()):
        raise SystemExit(1)


def validate():
    """Terra resolves the Dockstore WDL and reports per-input binding: the cheapest real gate
    before spending money. Response shape is extraInputs / invalidInputs / invalidOutputs /
    missingInputs / validInputs - there is no boolean 'valid' key."""
    preflight("validate")                  # free + offline first; this step costs a round-trip each
    bad = 0
    for name in CONFIGS:
        r = fapi.validate_config(NS, WS, NS, name)
        if r.status_code != 200:
            print(f"{name}: HTTP {r.status_code} {r.text[:200]}")
            bad += 1
            continue
        d = r.json()
        extra = d.get("extraInputs", [])
        missing = [m for m in d.get("missingInputs", [])]
        inval = d.get("invalidInputs", {}) or {}
        inval_o = d.get("invalidOutputs", {}) or {}
        nvalid = len(d.get("validInputs", []) or [])
        flag = "OK " if not (extra or inval or inval_o) else "BAD"
        # missingInputs is only fatal for inputs the WDL has no default for; the branch WDLs
        # default the rest, so list it rather than fail on it.
        print(f"{flag} {name}: bound={nvalid} extra={extra} invalidIn={inval} invalidOut={inval_o}")
        if missing:
            print(f"      missingInputs({len(missing)}): {', '.join(missing)[:400]}")
        if flag == "BAD":
            bad += 1
    if bad:
        raise SystemExit(f"{bad} configs failed validation")


# Flags that CONSUME the next token. `show` used to be found by "every argument that does not start
# with -", which meant `check --against main` was reported as two modes -- and the fix for that must
# not be to accept any stray positional, because `create foo` should still be a usage error.
VALUE_FLAGS = ("--against", "--wdl-dir", "--config")


def positional(argv: list[str]) -> list[str]:
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        if a in VALUE_FLAGS:
            skip = True
            continue
        if a.startswith("-"):
            continue
        out.append(a)
    return out


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        usage()
    args = positional(sys.argv[1:])
    if len(args) > 1:
        raise SystemExit(f"one mode at a time; got {' '.join(args)!r}   (--help)")
    mode = args[0] if args else "show"
    if mode not in ("show", "check", "create", "validate"):
        usage(2)                      # a typo must be a usage error, not a traceback
    if mode == "check":
        # Deliberately BEFORE require_target: check is offline, needs no workspace and no auth, and
        # is the tool you run precisely while the target is still undecided.
        raise SystemExit(cmd_check())
    if mode == "create" and "--confirm" not in sys.argv:
        raise SystemExit(
            "create POSTs (and overwrites) method configs in your workspace: a config with a\n"
            "  wrong binding is worse than no config, because the next submission will use it.\n"
            "  review `show` first, then re-run with --confirm.")
    if mode in ("create", "validate"):
        # Identity is resolved here, before any request: an unset workspace must be exit 4 with a
        # named key, never an empty path segment in a POST to Terra.
        require_target(writes=(mode == "create"))
    {"show": show, "create": create, "validate": validate}[mode]()


def usage(code=0):
    print("""usage: batch_configs.py [show|check|create|validate]

  show      print every input/output map (offline, no auth; needs GSVTK_BRANCH
              because the branch is part of every Dockstore URI it prints)
  check     compare every bound key against the WDL at a ref, offline, from your own
              checkout (miniwdl). --against <ref|branch|sha> (default GSVTK_BRANCH),
              --wdl-dir <dir> for a dirty tree, --config <name> for one config.
              `create` and `validate` run this first; --allow-unknown-inputs says you
              mean to post a map that does not fit the ref (it prints that it did).
              --against belongs to THIS command only: a command that POSTs grades
              the ref its own config runs (GSVTK_BRANCH, or GSV_WDL_VERSION on a
              rerun), because that is the WDL Terra will actually read.
  create    POST the configs into GSVTK_TERRA_NAMESPACE/GSVTK_TERRA_WORKSPACE
              (requires --confirm: it overwrites configs a submission will read;
               refuses the shared baseline workspace unless --allow-shared-target)
  validate  ask Terra to typecheck each config against its Dockstore WDL

The branch under test is GSVTK_BRANCH; attribute suffixes are GSVTK_FROZEN_SUFFIX /
GSVTK_NEW_SUFFIX and must match what batch_freeze.py and batch_fetch_compare.sh use.""",
          file=sys.stderr if code else sys.stdout)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
