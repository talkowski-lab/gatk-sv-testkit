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

    python terra/batch_configs.py show       # print the maps (offline, no auth)
    python terra/batch_configs.py create     # POST the configs (mutation)
    python terra/batch_configs.py validate   # Terra-side WDL validation
"""
from __future__ import annotations

import json
import os
import sys

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


def require_target():
    """Resolve (NS, WS, BRANCH) or exit 4 naming the profile key that is missing."""
    global NS, WS, BRANCH
    NS = NS or config.require("TERRA_NAMESPACE", "where these configs are POSTed")
    WS = WS or config.require("TERRA_WORKSPACE", "where these configs are POSTed")
    BRANCH = BRANCH or config.require("BRANCH", "the gatk-sv branch whose WDL each config runs")
    return NS, WS, BRANCH

# Attribute suffixes: baseline inputs in, this chain's outputs out.
FZ = "_" + config.get("FROZEN_SUFFIX", "frz")
NW = "_" + config.get("NEW_SUFFIX", "new")
DUMP = str(config.work_dir("manifests") / "batch_configs.json")


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


def body(name: str) -> dict:
    spec = CONFIGS[name]
    return {"namespace": NS, "name": name, "rootEntityType": spec["rootEntityType"],
            "methodRepoMethod": dockstore(spec["workflow"]),
            # Rawls rejects the body without it (400 "missing required member
            # 'methodConfigVersion'"); the server bumps it on every overwrite.
            "methodConfigVersion": 1,
            "deleted": False,  # also required by Rawls (400 "missing required member 'deleted'")
            "inputs": spec["inputs"], "outputs": spec["outputs"],
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


def create():
    out = {}
    for name in CONFIGS:
        b = body(name)
        r = fapi.create_workspace_config(NS, WS, b)
        if r.status_code == 409:
            r = fapi.overwrite_workspace_config(NS, WS, NS, name, b)
        ok = r.status_code in (200, 201)
        print(f"{name}: create HTTP {r.status_code}{'' if ok else ' ' + r.text[:300]}")
        out[name] = r.json() if ok else {"error": r.text}
    json.dump(out, open(DUMP, "w"), indent=1)
    print("->", DUMP)
    if not all(v.get("name") for v in out.values()):
        raise SystemExit(1)


def validate():
    """Terra resolves the Dockstore WDL and reports per-input binding: the cheapest real gate
    before spending money. Response shape is extraInputs / invalidInputs / invalidOutputs /
    missingInputs / validInputs - there is no boolean 'valid' key."""
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


def main():
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        usage()
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(args) > 1:
        raise SystemExit(f"one mode at a time; got {' '.join(args)!r}   (--help)")
    mode = args[0] if args else "show"
    if mode not in ("show", "create", "validate"):
        usage(2)                      # a typo must be a usage error, not a traceback
    if mode == "create" and "--confirm" not in sys.argv:
        raise SystemExit(
            "create POSTs (and overwrites) method configs in your workspace: a config with a\n"
            "  wrong binding is worse than no config, because the next submission will use it.\n"
            "  review `show` first, then re-run with --confirm.")
    {"show": show, "create": create, "validate": validate}[mode]()


def usage(code=0):
    print("""usage: batch_configs.py [show|create|validate]

  show      print every input/output map (offline, no auth; needs GSVTK_BRANCH
              because the branch is part of every Dockstore URI it prints)
  create    POST the configs into GSVTK_TERRA_NAMESPACE/GSVTK_TERRA_WORKSPACE
              (requires --confirm: it overwrites configs a submission will read)
  validate  ask Terra to typecheck each config against its Dockstore WDL

The branch under test is GSVTK_BRANCH; attribute suffixes are GSVTK_FROZEN_SUFFIX /
GSVTK_NEW_SUFFIX and must match what batch_freeze.py and batch_fetch_compare.sh use.""",
          file=sys.stderr if code else sys.stdout)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
