#!/usr/bin/env python3
"""Assert every comparator emits the same artifact envelope.

`compare/artifact.py` documents the shape; this script is why the shape is not a suggestion. Twelve
of the thirteen comparators can be pointed at the synthetic fixture pair (compare/make_fixtures.py),
so each one is run here with `--json`, and the resulting file must carry:

    tool            the name the tool knows it has
    argv            the invocation that produced this, so a stale artifact can be re-run
    inputs          every side, each file with a sha256 prefix — an artifact that does not name the
                    bytes it measured cannot be re-checked after a re-fetch
    rule            the join / tolerance / aggregation decisions that make the number mean something
    compared_something  true here, because every case below really does compare

Two comparators are not covered and say so in the output rather than being quietly dropped:
`profile_summarize` needs a completed gatk-sv-profile output directory and `compare_batch_tables`
needs two run directories of batch tables; both are exercised by `make smoke` against real data.

    python scripts/check_artifacts.py /tmp/gsvtk-fixtures
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

REQUIRED = ("tool", "argv", "inputs", "rule", "compared_something")

# tool name -> arguments, with $F expanded to the fixture directory by build()
CASES = {
    "table_diff": ["$F/tbl_a.tsv", "$F/tbl_reordered.tsv", "--key", "vid"],
    "matrix_diff": ["$F/wide_a.tsv", "$F/wide_a.tsv"],
    "vcf_paired_diff": ["$F/geno_a.vcf", "$F/geno_same.vcf", "--field", "FORMAT:GQ"],
    "site_set_diff": ["$F/geno_a.vcf", "$F/geno_b.vcf"],
    "lineset_diff": ["$F/list_a.txt", "$F/list_b.txt"],
    "json_diff": ["$F/inputs_a.json", "$F/inputs_b.json"],
    "tar_manifest": ["$F/bundle_a.tar.gz", "$F/bundle_b.tar.gz"],
    "gq_paired_compare": ["$F/geno_a.vcf", "$F/geno_same.vcf", "--field", "GQ", "--scale", "1/1"],
    "gq_scale_compare": ["$F/geno_a.vcf", "$F/geno_b.vcf", "--fields", "GQ"],
    "diff_rd_states": ["$F/geno_b.vcf", "--baseline", "$F/geno_a.vcf"],
}
NOT_COVERED = ("profile_summarize (needs a gatk-sv-profile run dir; `make smoke` runs it on real data)",
               "compare_batch_tables (needs two run dirs; `make smoke` runs it on real data)")


def main() -> int:
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        print(__doc__.strip().splitlines()[-1])
        return 2
    fixtures = os.path.abspath(sys.argv[1])
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    failures = 0
    for tool, argv in CASES.items():
        if tool == "diff_rd_states" and shutil.which("bcftools") is None:
            print("  --    diff_rd_states reads VCFs through bcftools, which is not on PATH here")
            continue
        args = [a.replace("$F", fixtures) for a in argv]
        artifact_path = os.path.join(fixtures, f"artifact-{tool}.json")
        cmd = [sys.executable, os.path.join(root, "compare", f"{tool}.py")] + args + ["--json", artifact_path]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=root)
        if proc.returncode not in (0, 1):
            print(f"  FAIL  {tool}: --json is not accepted, or the run failed (exit "
                  f"{proc.returncode})")
            tail = (proc.stderr or proc.stdout).strip().splitlines()
            print("        " + (tail[-1] if tail else "(the tool printed nothing at all)"))
            failures += 1
            continue
        if not os.path.isfile(artifact_path):
            print(f"  FAIL  {tool}: exit {proc.returncode} but no artifact was written to "
                  f"{artifact_path}")
            failures += 1
            continue
        with open(artifact_path) as fh:
            doc = json.load(fh)
        problems = [f"missing key '{k}'" for k in REQUIRED if k not in doc]
        if doc.get("tool") != tool:
            problems.append(f"tool says {doc.get('tool')!r}")
        if not doc.get("compared_something"):
            problems.append("compared_something is false, so this case compares nothing and must "
                            "not be counted as a pass")
        if not doc.get("inputs"):
            problems.append("inputs is empty")
        for label, side in (doc.get("inputs") or {}).items():
            if isinstance(side, dict) and side.get("path") and "sha256_16" not in side:
                problems.append(f"input {label} names a path but not its bytes")
        if not doc.get("rule"):
            problems.append("rule is empty — the number arrived from nowhere")
        if problems:
            print(f"  FAIL  {tool}: " + "; ".join(problems))
            failures += 1
        else:
            metrics = len([k for k in doc if k not in REQUIRED])
            print(f"  ok    {tool}: envelope complete ({metrics} metric key(s), "
                  f"{len(doc['inputs'])} input side(s))")
    for note in NOT_COVERED:
        print(f"  --    {note} not covered here")
    if failures:
        print(f"  {failures} comparator(s) do not emit the documented envelope")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
