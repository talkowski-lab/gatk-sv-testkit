"""Offline tests for replay/svshell_run.py: driver contract, input validation, plan, gating, compare.

A synthetic gatk-sv tree stands in for the checkout; the --confirm path runs with a fake in place of
run_in_image.sh and gsutil, and the real compare/vcf_body_diff.py judges the fetched VCF.
"""
import json
import subprocess
import sys

import pytest

from conftest import ROOT, clean_env

sys.path.insert(0, str(ROOT / "replay"))
import svshell_run as sr  # noqa: E402

TOOL = "replay/svshell_run.py"
IMAGE = "us.gcr.io/example/sv-shell:branch-abc123"

DRIVER = """#!/bin/bash
input_json=${1}
batch=$(jq -r ".batch" "$input_json")
opt_tar=$(jq -r '.opt_tar // empty' "$input_json")
ref_files_list=$(jq -r ".ref_files_list" "$input_json")
mapfile -t ref_files < "${ref_files_list}"

jq -n \\
  --slurpfile inputs "${input_json}" \\
  '{
    "cram": $inputs[0].bam_or_cram_file,
    "soft": $inputs[0].soft_key,
    "hard": $inputs[0].hard_key
  }' > "${m1_inputs_json}"

bash /opt/sv_shell/m1.sh \\
  "${m1_inputs_json}"
"""
MODULE = ('cram=$(jq -r ".cram" "${input_json}")\n'
          'soft=$(jq -r ".soft // 5" "${input_json}")\n'
          'hard=$(jq -r ".hard" "${input_json}")\n')

INPUTS = {"batch": "NA12878", "bam_or_cram_file": "gs://bkt/NA12878.cram",
          "hard_key": "x", "ref_files_list": ["gs://bkt/pe/a.txt.gz", "gs://bkt/pe/b.txt.gz"]}
SIZES = {"gs://bkt/NA12878.cram": 2 * 10**9, "gs://bkt/pe/a.txt.gz": 100,
         "gs://bkt/pe/b.txt.gz": 100, "gs://bkt/pe/a.txt.gz.tbi": 10}

HEADER = "##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\n"
REC1 = "chr1\t100\tsv1\tN\t<DEL>\t.\tPASS\tSVTYPE=DEL\n"
REC2 = "chr1\t200\tsv2\tN\t<DUP>\t.\tPASS\tSVTYPE=DUP\n"


@pytest.fixture
def tree(tmp_path):
    d = tmp_path / "gatk-sv" / "src" / "sv_shell"
    d.mkdir(parents=True)
    (d / "single_sample_pipeline.sh").write_text(DRIVER)
    (d / "m1.sh").write_text(MODULE)
    (tmp_path / "inputs.json").write_text(json.dumps(INPUTS))
    (tmp_path / "sizes.json").write_text(json.dumps(SIZES))
    (tmp_path / "base.vcf").write_text(HEADER + REC1 + REC2)
    return tmp_path


def args(t, *extra):
    return ["--inputs", t / "inputs.json", "--checkout", t / "gatk-sv", "--sizes", t / "sizes.json",
            "--baseline", t / "base.vcf", "--image", IMAGE, *extra]


# ----------------------------------------------------------------------------- driver contract
def test_contract_from_a_synthetic_driver(tree):
    c = sr.driver_contract(DRIVER, tree / "gatk-sv")
    assert c["required"] == {"batch", "ref_files_list", "bam_or_cram_file", "hard_key"}
    assert c["optional"] == {"opt_tar", "soft_key"}
    assert c["softened"] == {"soft_key"}            # m1.sh reads `.soft // 5`
    assert c["list_keys"] == {"ref_files_list"}
    # without the module scripts nothing is softened: soft_key stays required
    assert "soft_key" in sr.driver_contract(DRIVER)["required"]


def test_missing_required_key_is_refused_and_allow_missing_overrides(run_tool, tree):
    bad = dict(INPUTS)
    del bad["hard_key"]
    (tree / "inputs.json").write_text(json.dumps(bad))
    r = run_tool(TOOL, *args(tree))
    assert r.returncode == 2
    assert "lacks 1 key(s)" in r.stderr and "hard_key" in r.stderr
    ok = run_tool(TOOL, *args(tree, "--allow-missing", "hard_key"))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "hard_key is missing and allowed" in ok.stdout


def test_inputs_must_be_an_object(run_tool, tree):
    (tree / "inputs.json").write_text("[1, 2]")
    r = run_tool(TOOL, *args(tree))
    assert r.returncode == 2 and "must be an object" in r.stderr


def test_missing_checkout_exits_4(run_tool, tree):
    r = run_tool(TOOL, "--inputs", tree / "inputs.json", "--driver", tree / "nope.sh",
                 "--baseline", tree / "base.vcf", "--image", IMAGE)
    assert r.returncode == 4 and "no sv-shell driver" in r.stderr


# ---------------------------------------------------------------------------------- plan output
def test_plan_stages_rewrites_and_dry_runs(run_tool, tree):
    r = run_tool(TOOL, *args(tree, "--show-startup"))
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "free: nothing is created" in out
    assert "gs://bkt/NA12878.cram  (input)" in out
    assert "gs://bkt/pe/a.txt.gz.tbi  (index)" in out          # sibling index found by the lookup
    assert "gs://bkt/pe/b.txt.gz.tbi" not in out                # absent sibling: not staged
    # the inputs JSON is rewritten to staged paths and the list key becomes a rebuilt list file
    assert '"bam_or_cram_file": "/sv/inputs/bkt/NA12878.cram"' in out
    assert '"ref_files_list": "/sv/lists/ref_files_list.list"' in out
    assert "/sv/inputs/bkt/pe/a.txt.gz\n/sv/inputs/bkt/pe/b.txt.gz" in out
    assert "bash '/opt/sv_shell/single_sample_pipeline.sh' '/sv/inputs.json'" in out
    assert "mounts   : /sv:/sv" in out and "--dry-run: nothing was created" in out
    assert "e2-standard-16" in out and "plan only" in out


def test_list_key_read_from_a_local_file(run_tool, tree):
    lst = tree / "refs.list"
    lst.write_text("gs://bkt/pe/a.txt.gz\ngs://bkt/pe/b.txt.gz\n")
    (tree / "inputs.json").write_text(json.dumps({**INPUTS, "ref_files_list": str(lst)}))
    r = run_tool(TOOL, *args(tree))
    assert r.returncode == 0, r.stderr
    assert "ref_files_list: 2 line(s) read from" in r.stdout


def test_local_path_value_is_refused(run_tool, tree):
    (tree / "inputs.json").write_text(json.dumps({**INPUTS, "bam_or_cram_file": "/inputs/x.cram"}))
    r = run_tool(TOOL, *args(tree))
    assert r.returncode == 2 and "the VM cannot see it" in r.stderr
    ok = run_tool(TOOL, *args(tree, "--image-paths"))
    assert ok.returncode == 0, ok.stderr


def test_absent_input_object_is_refused(run_tool, tree):
    sizes = dict(SIZES)
    del sizes["gs://bkt/NA12878.cram"]
    (tree / "sizes.json").write_text(json.dumps(sizes))
    r = run_tool(TOOL, *args(tree))
    assert r.returncode == 2 and "NOT FOUND: gs://bkt/NA12878.cram" in r.stdout


# ------------------------------------------------------------------------------------ refusals
def test_max_gb_refusal_and_control(run_tool, tree):
    r = run_tool(TOOL, *args(tree, "--max-gb", "1"))
    assert r.returncode == 2 and "over --max-gb 1" in r.stderr
    assert run_tool(TOOL, *args(tree, "--max-gb", "3")).returncode == 0


def test_missing_image_refusal(run_tool, tree):
    a = [x for x in args(tree) if x not in ("--image", IMAGE)]
    r = run_tool(TOOL, *a)
    assert r.returncode == 2 and "no --image given" in r.stderr


def test_read_only_refuses_confirm_but_not_the_plan(run_tool, tree):
    env = clean_env(GSVTK_READ_ONLY="1")
    r = run_tool(TOOL, *args(tree, "--confirm", "--out-uri", "gs://o/p"), env=env)
    assert r.returncode == 2 and "GSVTK_READ_ONLY" in r.stderr
    assert run_tool(TOOL, *args(tree), env=env).returncode == 0


def test_confirm_needs_out_uri(run_tool, tree):
    r = run_tool(TOOL, *args(tree, "--confirm"))
    assert r.returncode == 2 and "--out-uri" in r.stderr


# ------------------------------------------------------------------- --confirm with fake cloud
class FakeCloud:
    """run_in_image.sh and gsutil are faked; everything else (the comparator) runs for real."""

    def __init__(self, fetched_text, rii_rc=0, uploaded=True):
        self.fetched_text = fetched_text
        self.rii_rc = rii_rc
        self.uploaded = uploaded
        self.calls = []

    def __call__(self, argv, **kw):
        argv = list(map(str, argv))
        self.calls.append(argv)
        if argv[0] == "bash" and argv[1].endswith("run_in_image.sh") and "--dry-run" not in argv:
            post = open(argv[argv.index("--host-post") + 1]).read()
            dest = post.split("upload \"$p\" '", 1)[1].split("/'", 1)[0]
            out = "### SVSHELL_RC=%d\n" % self.rii_rc
            if self.uploaded:
                out += f"### post| SVSHELL_UPLOADED {dest}/final.vcf.gz\n"
            return subprocess.CompletedProcess(argv, self.rii_rc, out, "")
        if argv[0] == "gsutil" and argv[2] == "cp":
            with open(argv[4], "w") as fh:
                fh.write(self.fetched_text)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "gsutil":
            raise AssertionError(f"unexpected gsutil call {argv}")
        return subprocess.run(argv, **kw)


def confirm_args(t):
    return [*map(str, args(t)), "--confirm", "--out-uri", "gs://out-bkt/runs", "--project", "p",
            "--work-dir", str(t / "wd")]


@pytest.mark.parametrize("fetched,code", [
    (HEADER + REC1 + REC2, 0),            # identical body
    (HEADER + REC1, 1),                   # a record is gone
    (HEADER, 2),                          # header only: nothing compared
])
def test_confirm_returns_the_comparator_code(tree, monkeypatch, capsys, fetched, code):
    monkeypatch.delenv("GSVTK_READ_ONLY", raising=False)
    fake = FakeCloud(fetched)
    assert sr.main(confirm_args(tree), run=fake) == code
    rii = [c for c in fake.calls if c[0] == "bash"]
    assert len(rii) == 1 and "--dry-run" not in rii[0] and "--mount" in rii[0]
    assert "comparator exit %d" % code in capsys.readouterr().out


def test_confirm_pipeline_failure_skips_the_compare(tree, monkeypatch):
    monkeypatch.delenv("GSVTK_READ_ONLY", raising=False)
    fake = FakeCloud(HEADER + REC1, rii_rc=1)
    assert sr.main(confirm_args(tree), run=fake) == 1
    assert not any(c[0] == "gsutil" for c in fake.calls)


def test_confirm_without_upload_is_not_a_pass(tree, monkeypatch):
    monkeypatch.delenv("GSVTK_READ_ONLY", raising=False)
    fake = FakeCloud(HEADER + REC1 + REC2, uploaded=False)
    assert sr.main(confirm_args(tree), run=fake) == 1


def test_not_scored_is_passed_through(tree, monkeypatch):
    monkeypatch.delenv("GSVTK_READ_ONLY", raising=False)
    assert sr.main(confirm_args(tree), run=FakeCloud("", rii_rc=5)) == 5


# --------------------------------------------------------------------------- --fetched compare
@pytest.mark.parametrize("fetched,code", [(HEADER + REC1 + REC2, 0), (HEADER + REC2, 1), (HEADER, 2)])
def test_fetched_compare_exit_codes(run_tool, tree, fetched, code):
    f = tree / "fetched.vcf"
    f.write_text(fetched)
    r = run_tool(TOOL, "--fetched", f, "--baseline", tree / "base.vcf")
    assert r.returncode == code, r.stdout + r.stderr


def test_fetched_missing_file_is_nothing_compared(run_tool, tree):
    r = run_tool(TOOL, "--fetched", tree / "absent.vcf", "--baseline", tree / "base.vcf")
    assert r.returncode == 2 and "NOTHING COMPARED" in r.stdout


def test_contract_matches_svshell_contract_check_reads(tree):
    # The keys this tool calls required are a subset of what the contract checker says the driver reads.
    sys.path.insert(0, str(ROOT / "checks"))
    import svshell_contract_check as cc
    reads = set(cc.READ_INPUTS.findall(DRIVER))
    c = sr.driver_contract(DRIVER, tree / "gatk-sv")
    assert reads <= c["required"] | c["optional"]
