"""Offline tests for replay/task_replay.py: plan, localization, refusals, gating, verdicts.

Nothing here boots a VM: the plan path calls `run_in_image.sh --dry-run` (no project needed), and the
--confirm path runs with an injected fake in place of run_in_image.sh.
"""
import json
import subprocess
import sys

import pytest

from conftest import ROOT, clean_env

sys.path.insert(0, str(ROOT / "replay"))
import task_replay as tr  # noqa: E402

TOOL = "replay/task_replay.py"
IMAGE = "us.gcr.io/example/sv-pipeline:branch-abc123"

SCRIPT = """#!/bin/bash
cd /cromwell_root
tmpDir=$(mkdir -p "/cromwell_root/tmp.1a2b" && echo "/cromwell_root/tmp.1a2b")
(
cd /cromwell_root
svtk vcf2bed /cromwell_root/fc-abc/sub/call-X/in.vcf.gz out.bed
gsutil cat gs://ref-bucket/ref/contigs.list | head -1
)  > "/cromwell_root/stdout" 2> "/cromwell_root/stderr"
echo $? > /cromwell_root/rc.tmp
mv /cromwell_root/rc.tmp /cromwell_root/rc
"""

CALL = {"inputs": {"vcf": "gs://fc-abc/sub/call-X/in.vcf.gz",
                   "contigs": "gs://ref-bucket/ref/contigs.list",
                   "extra": ["gs://fc-abc/x/y.txt"]},
        "outputs": {"bed": "gs://fc-abc/sub/wf/call-T/out.bed"},
        "callRoot": "gs://fc-abc/sub/wf/call-T",
        "runtimeAttributes": {"cpu": "4", "memory": "15 GB", "disks": "local-disk 100 HDD"},
        "start": "2026-01-01T00:00:00Z", "end": "2026-01-01T01:00:00Z",
        "shardIndex": -1, "attempt": 1}

SIZES = {"gs://fc-abc/sub/call-X/in.vcf.gz": 1_000_000, "gs://fc-abc/x/y.txt": 20}


@pytest.fixture
def saved(tmp_path):
    (tmp_path / "script").write_text(SCRIPT)
    (tmp_path / "call.json").write_text(json.dumps(CALL))
    (tmp_path / "sizes.json").write_text(json.dumps(SIZES))
    return tmp_path


def base_args(d):
    return ["--script", d / "script", "--call-json", d / "call.json", "--sizes", d / "sizes.json"]


# ------------------------------------------------------------------------------ plan output
def test_plan_prints_inputs_probe_and_dry_run(run_tool, saved):
    r = run_tool(TOOL, *base_args(saved), "--image", IMAGE)
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout
    assert "free: nothing is created" in out
    assert "-> /cromwell_root/fc-abc/sub/call-X/in.vcf.gz" in out
    assert "-> /cromwell_root/fc-abc/x/y.txt  (declared input)" in out
    assert "left as gs:// in the script" in out and "gs://ref-bucket/ref/contigs.list" in out
    assert "outputs  : 1 declared" in out and "out.bed" in out
    assert "e2-standard-4" in out and "cost     : ~$" in out
    # the probe, both host scripts and run_in_image.sh's own dry-run are all shown
    assert "===== probe (runs inside the image) =====" in out
    assert "fetch 'gs://fc-abc/x/y.txt' '/cromwell_root/fc-abc/x/y.txt' '20'" in out
    assert "REPLAY_OUT" in out
    assert "--dry-run: nothing was created" in out
    assert "mounts   : /cromwell_root:/cromwell_root" in out
    assert "lines of startup script omitted" in out
    assert "plan only" in out


def test_save_dir_round_trips_from_metadata(run_tool, saved, tmp_path):
    meta = {"calls": {"Wf.T": [dict(CALL, attempt=1), dict(CALL, attempt=2)]}}
    (tmp_path / "meta.json").write_text(json.dumps(meta))
    out = tmp_path / "saved"
    r = run_tool(TOOL, "--metadata", tmp_path / "meta.json", "--task", "T", "--script", saved / "script",
                 "--sizes", saved / "sizes.json", "--image", IMAGE, "--save-dir", out)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((out / "call.json").read_text())["attempt"] == 2
    assert (out / "script").read_text() == SCRIPT


# ------------------------------------------------------------------------- localization logic
def test_localization_keeps_string_gs_literals_unless_rewritten():
    loc = tr.plan_localization(SCRIPT, CALL["inputs"])
    uris = {s["uri"] for s in loc["stage"]}
    assert uris == {"gs://fc-abc/sub/call-X/in.vcf.gz", "gs://fc-abc/x/y.txt"}
    assert loc["gs_left"] == ["gs://ref-bucket/ref/contigs.list"]
    assert loc["script"] == SCRIPT
    # single-component paths (tmp dirs, rc, stdout) are never candidates
    assert not any("tmp.1a2b" in s["path"] for s in loc["stage"])

    rw = tr.plan_localization(SCRIPT, CALL["inputs"], rewrite_gs=True)
    assert "gs://" not in rw["script"]
    assert "/cromwell_root/ref-bucket/ref/contigs.list | head -1" in rw["script"]
    assert {s["path"] for s in rw["stage"]} >= {"/cromwell_root/ref-bucket/ref/contigs.list"}
    assert rw["gs_left"] == []


def test_localization_detects_the_batch_root():
    script = "cd /mnt/disks/cromwell_root\ncat /mnt/disks/cromwell_root/bkt/a/b.txt > out\n"
    loc = tr.plan_localization(script, {})
    assert loc["root"] == "/mnt/disks/cromwell_root"
    assert [s["path"] for s in loc["stage"]] == ["/mnt/disks/cromwell_root/bkt/a/b.txt"]
    assert loc["stage"][0]["uri"] == "gs://bkt/a/b.txt"


def test_absent_objects_are_not_staged(run_tool, saved):
    (saved / "sizes.json").write_text(json.dumps({"gs://fc-abc/x/y.txt": 20}))
    r = run_tool(TOOL, *base_args(saved), "--image", IMAGE)
    assert r.returncode == 0
    assert "not found, NOT staged: gs://fc-abc/sub/call-X/in.vcf.gz" in r.stdout
    assert "fetch 'gs://fc-abc/sub/call-X/in.vcf.gz'" not in r.stdout


def test_parse_ls_l():
    text = ("      1234  2026-01-01T00:00:00Z  gs://b/o1\n"
            "CommandException: One or more URLs matched no objects.\n"
            "TOTAL: 1 objects, 1234 bytes (1.21 KiB)\n")
    assert tr.parse_ls_l(text) == {"gs://b/o1": 1234}


def test_probe_reads_the_rc_file_and_falls_back_to_the_wrapper(tmp_path):
    # The probe runs here with a temp dir as its root: the rc file is the answer when it exists.
    root = tmp_path / "root"
    with_rc = f"echo 3 > {root}/rc\nexit 0\n"
    p = subprocess.run(["bash", "-s"], input=tr.task_probe(with_rc, str(root)),
                       capture_output=True, text=True)
    assert p.returncode == 3 and "REPLAY_TASK_RC=3 (from" in p.stdout
    no_rc = "exit 7\n"
    p = subprocess.run(["bash", "-s"], input=tr.task_probe(no_rc, str(root)),
                       capture_output=True, text=True)
    assert p.returncode == 7 and "no usable rc file" in p.stdout
    assert (root / ".replay_start").exists()


def test_probe_heredoc_tag_never_collides():
    script = "echo GSVTK_REPLAY_EOF\n"
    probe = tr.task_probe(script, "/cromwell_root")
    assert "<<'GSVTK_REPLAY_EOF_X'" in probe


# ---------------------------------------------------------------------------------- refusals
def test_max_gb_refusal_and_its_positive_control(run_tool, saved):
    (saved / "sizes.json").write_text(json.dumps({**SIZES, "gs://fc-abc/x/y.txt": 3 * 10**9}))
    r = run_tool(TOOL, *base_args(saved), "--image", IMAGE, "--max-gb", "1")
    assert r.returncode == 2
    assert "over --max-gb 1" in r.stderr
    ok = run_tool(TOOL, *base_args(saved), "--image", IMAGE, "--max-gb", "5")
    assert ok.returncode == 0, ok.stderr


def test_missing_image_refusal(run_tool, saved):
    r = run_tool(TOOL, *base_args(saved))
    assert r.returncode == 2
    assert "no --image given" in r.stderr


def test_too_many_images_and_pair_without_two(run_tool, saved):
    r = run_tool(TOOL, *base_args(saved), "--image", IMAGE, "--pair")
    assert r.returncode == 2 and "--pair needs exactly two images" in r.stderr
    r = run_tool(TOOL, *base_args(saved), "--image", "a:1", "--image", "b:1", "--image", "c:1")
    assert r.returncode == 2


def test_read_only_refuses_confirm_but_not_the_plan(run_tool, saved):
    env = clean_env(GSVTK_READ_ONLY="1")
    r = run_tool(TOOL, *base_args(saved), "--image", IMAGE, "--confirm", env=env)
    assert r.returncode == 2
    assert "GSVTK_READ_ONLY" in r.stderr
    assert "task replay plan" not in r.stdout          # refused before anything was read
    plan = run_tool(TOOL, *base_args(saved), "--image", IMAGE, env=env)
    assert plan.returncode == 0


def test_no_task_source_is_refused(run_tool):
    r = run_tool(TOOL, "--image", IMAGE)
    assert r.returncode == 2 and "no task to replay" in r.stderr


def test_image_from_capture(tmp_path):
    cap = {"kind": "gsvtk-image-capture", "version": "1",
           "images": {"Wf.sv_pipeline_docker": {"ref": "r/sv-pipeline:t@sha256:" + "a" * 64},
                      "Wf.sv_base_mini_docker": {"ref": "r/sv-base-mini:t@sha256:" + "b" * 64}}}
    p = tmp_path / "cap.json"
    p.write_text(json.dumps(cap))
    assert tr.image_from_capture(str(p), "sv_pipeline_docker").endswith("a" * 64)
    with pytest.raises(SystemExit) as e:
        tr.image_from_capture(str(p), "")              # two entries, no key: ambiguous
    assert e.value.code == 2
    p.write_text(json.dumps({"kind": "other"}))
    with pytest.raises(SystemExit):
        tr.image_from_capture(str(p), "sv_pipeline_docker")


# ---------------------------------------------------------------------------- --confirm gating
class FakeRun:
    """Stands in for subprocess.run: records argv, answers run_in_image.sh with canned output."""

    def __init__(self, rcs=(0,), outputs=None):
        self.calls = []
        self.rcs = list(rcs)
        self.outputs = outputs or ["### post| REPLAY_OUT " + "c" * 64 + " 42 out.bed\n### HOST_POST_RC=0\n"]

    def __call__(self, argv, **kw):
        self.calls.append(list(map(str, argv)))
        if str(argv[1]).endswith("run_in_image.sh") and "--dry-run" not in argv:
            i = len([c for c in self.calls if "--dry-run" not in c]) - 1
            return subprocess.CompletedProcess(argv, self.rcs[min(i, len(self.rcs) - 1)],
                                               self.outputs[min(i, len(self.outputs) - 1)], "")
        return subprocess.run(argv, **kw)


def test_plan_never_calls_run_in_image_without_dry_run(saved, capsys):
    fake = FakeRun()
    rc = tr.main([*map(str, base_args(saved)), "--image", IMAGE], run=fake)
    assert rc == 0
    rii = [c for c in fake.calls if c[1].endswith("run_in_image.sh")]
    assert len(rii) == 1 and "--dry-run" in rii[0]


def test_confirm_runs_without_dry_run_and_reports_hashes(saved, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GSVTK_READ_ONLY", raising=False)
    fake = FakeRun()
    rc = tr.main([*map(str, base_args(saved)), "--image", IMAGE, "--confirm", "--project", "p",
                  "--work-dir", str(tmp_path / "wd")], run=fake)
    out = capsys.readouterr().out
    assert rc == 0, out
    rii = [c for c in fake.calls if c[1].endswith("run_in_image.sh")]
    assert len(rii) == 1 and "--dry-run" not in rii[0] and "--project" in rii[0]
    assert "c" * 64 in out and "PASS" in out


def test_confirm_refuses_when_sizes_are_unknown(saved, tmp_path, monkeypatch):
    monkeypatch.delenv("GSVTK_READ_ONLY", raising=False)
    monkeypatch.setattr(tr.shutil, "which", lambda name: None)
    args = ["--script", str(saved / "script"), "--call-json", str(saved / "call.json"),
            "--image", IMAGE, "--work-dir", str(tmp_path / "wd")]
    assert tr.main(args) == 0                               # the plan is still printed
    with pytest.raises(SystemExit) as e:
        tr.main(args + ["--confirm", "--project", "p"])
    assert e.value.code == 2


def test_two_arms_compare_hashes(saved, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GSVTK_READ_ONLY", raising=False)
    same = "### post| REPLAY_OUT " + "c" * 64 + " 42 out.bed\n"
    other = "### post| REPLAY_OUT " + "d" * 64 + " 42 out.bed\n"
    args = [*map(str, base_args(saved)), "--image", "a/x:1", "--image", "b/x:2", "--confirm",
            "--project", "p", "--work-dir", str(tmp_path / "wd")]
    assert tr.main(args, run=FakeRun(rcs=(0, 0), outputs=[same, same])) == 0
    assert "SAME" in capsys.readouterr().out
    assert tr.main(args, run=FakeRun(rcs=(0, 0), outputs=[same, other])) == 1
    assert "differs: out.bed" in capsys.readouterr().out


# ---------------------------------------------------------------------------------- verdicts
@pytest.mark.parametrize("rcs,code,word", [
    ((1, 0), 0, "PAIRED"),
    ((0, 0), 1, "VACUOUS"),
    ((1, 1), 1, "GUARD DOES NOT WORK"),
    ((5, 0), 5, "NOT SCORED"),
])
def test_pair_verdicts(rcs, code, word, capsys):
    outs = [{"outputs": {}, "missing": [], "host_post_rc": 0}] * 2
    assert tr.report(["a", "b"], list(rcs), outs, [], pair=True) == code
    assert word in capsys.readouterr().out


def test_single_arm_missing_declared_output_fails(capsys):
    outs = [{"outputs": {}, "missing": ["out.bed"], "host_post_rc": 0}]
    assert tr.report(["a"], [0], outs, ["out.bed"], pair=False) == 1


def test_parse_run_output():
    text = ("  ### ### post| REPLAY_OUT " + "e" * 64 + " 17 sub dir/out file.txt\n"
            "  ### ### post| REPLAY_MISSING out.bed\n  ### ### HOST_POST_RC=0\n")
    got = tr.parse_run_output(text)
    assert got["outputs"] == {"sub dir/out file.txt": {"sha256": "e" * 64, "bytes": 17}}
    assert got["missing"] == ["out.bed"] and got["host_post_rc"] == 0


def test_named_outputs_relative_to_call_root():
    assert tr.named_outputs(CALL, "/cromwell_root") == ["out.bed"]


def test_machine_and_cost():
    assert tr.pick_machine({"cpu": "4", "memory": "15 GB"}) == "e2-standard-4"
    assert tr.pick_machine({"cpu": "2", "memory": "40 GiB"}) == "e2-standard-16"
    cost, basis = tr.cost_estimate("e2-standard-4", 100, 2.0)
    assert cost and cost > 0 and "vCPU" in basis
