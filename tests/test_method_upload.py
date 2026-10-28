"""terra/method_upload.py and the rerun's `--method agora://...` (offline: no request leaves the process)."""
import json
import subprocess
import sys

import pytest

from conftest import ROOT, clean_env

sys.path[:0] = [str(ROOT / "terra"), str(ROOT / "kit")]

TOOL = "terra/method_upload.py"
RERUN = "terra/batch_rerun_step.py"
FIX = ROOT / "scripts" / "selftest.d" / "fixtures" / "rerun.profile.env"
R = "us.gcr.io/your-dev-project/test"
PIN10 = ["--image", f"gatk_docker={R}/gatk:T", "--image", f"sv_base_mini_docker={R}/sv-base-mini:T",
         "--image", f"sv_pipeline_docker={R}/sv-pipeline:T"]

WF = """version 1.0

import "Tasks.wdl" as tasks

workflow Toy {
  input {
    String name
  }
  call tasks.Hello { input: name = name }
}
"""
TASKS = """version 1.0

task Hello {
  input {
    String name
  }
  command <<< echo ~{name} >>>
  output {
    String out = read_string(stdout())
  }
}
"""


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def checkout(tmp_path):
    """A synthetic gatk-sv clone: wdl/Toy.wdl importing wdl/Tasks.wdl, one commit on `feature`."""
    ck = tmp_path / "gatk-sv"
    (ck / "wdl").mkdir(parents=True)
    (ck / "wdl" / "Toy.wdl").write_text(WF)
    (ck / "wdl" / "Tasks.wdl").write_text(TASKS)
    git(tmp_path, "init", "-q", str(ck))
    git(ck, "checkout", "-q", "-b", "feature")
    git(ck, "add", "-A")
    git(ck, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "-m", "toy")
    git(ck, "remote", "add", "origin", "https://github.com/example/gatk-sv.git")
    # An uncommitted edit must not reach the upload: the tool reads the commit, not the tree.
    (ck / "wdl" / "Tasks.wdl").write_text(TASKS.replace("echo", "UNCOMMITTED"))
    return ck


@pytest.fixture
def env(tmp_path, checkout):
    return clean_env(HOME=str(tmp_path / "home"), GSVTK_CONFIG="/dev/null",
                     GSVTK_WORK=str(tmp_path / "work"), GSVTK_GATK_SV_CHECKOUT=str(checkout),
                     GSVTK_TERRA_NAMESPACE="my-ns", GSVTK_BASELINE_NAMESPACE="shared-ns",
                     GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/")


@pytest.fixture
def inproc(monkeypatch, env):
    """Import method_upload with the test environment in place."""
    for k in [k for k in list(__import__("os").environ) if k.startswith("GSVTK_")]:
        monkeypatch.delenv(k)
    for k, v in env.items():
        if k.startswith("GSVTK_") or k == "HOME":
            monkeypatch.setenv(k, v)
    import method_upload
    return method_upload


def test_plan_prints_target_sha_size_and_request(run_tool, env, checkout, tmp_path):
    sha = git(checkout, "rev-parse", "feature")
    r = run_tool(TOOL, "Toy", "--ref", "feature", env=env)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "nothing is sent" in out
    assert "my-ns/Toy" in out
    assert f"feature -> {sha}" in out
    assert "POST http://127.0.0.1:9/api/methods" in out
    assert "sha256" in out and "bytes" in out
    wdl = tmp_path / "work" / "method-upload" / f"Toy-{sha[:12]}.wdl"
    req = json.loads((tmp_path / "work" / "method-upload" / f"Toy-{sha[:12]}.request.json").read_text())
    assert req["payload"] == wdl.read_text()
    assert "UNCOMMITTED" not in req["payload"]
    assert "upload --confirm" in out
    assert "DELETE" in out


def test_payload_shape_carries_provenance(inproc, checkout):
    sha = git(checkout, "rev-parse", "feature")
    b = inproc.build("Toy", "feature", "my-ns", "Toy", str(checkout))
    req = b["request"]
    assert set(req) == {"namespace", "name", "entityType", "payload", "synopsis", "documentation",
                        "snapshotComment"}
    assert (req["namespace"], req["name"], req["entityType"]) == ("my-ns", "Toy", "Workflow")
    assert len(req["synopsis"]) <= inproc.SYNOPSIS_MAX and sha[:12] in req["synopsis"]
    for field in ("documentation", "payload"):
        assert sha in req[field] and "https://github.com/example/gatk-sv.git" in req[field]
    assert sha in req["snapshotComment"]
    # The flattened document: one version line, the import gone, the qualified call rewritten.
    p = req["payload"]
    assert p.startswith("version 1.0\n") and p.count("version 1.0") == 1
    assert "import " not in p and "call Hello" in p and "tasks." not in p
    assert b["sha256"] in req["documentation"]


def test_upload_without_confirm_is_refused(run_tool, env):
    r = run_tool(TOOL, "Toy", "upload", "--ref", "feature", env=env)
    assert r.returncode == 2
    assert "--confirm" in r.stderr


def test_upload_is_refused_read_only(run_tool, env):
    env = dict(env, GSVTK_READ_ONLY="1")
    r = run_tool(TOOL, "Toy", "upload", "--confirm", "--ref", "feature", env=env)
    assert r.returncode == 2
    assert "GSVTK_READ_ONLY" in r.stderr


def test_shared_namespace_is_refused(run_tool, env):
    r = run_tool(TOOL, "Toy", "upload", "--confirm", "--ref", "feature", "--namespace", "shared-ns",
                 env=env)
    assert r.returncode == 2
    assert "baseline namespace" in r.stderr and "--allow-shared-target" in r.stderr


def test_unknown_ref_is_refused(run_tool, env):
    r = run_tool(TOOL, "Toy", "--ref", "no-such-branch", env=env)
    assert r.returncode == 2 and "does not name a commit" in r.stderr


def test_missing_namespace_exits_4(run_tool, env):
    env = {k: v for k, v in env.items() if k != "GSVTK_TERRA_NAMESPACE"}
    r = run_tool(TOOL, "Toy", "--ref", "feature", env=env)
    assert r.returncode == 4


def test_upload_posts_the_planned_request_and_records_it(inproc, checkout, monkeypatch, capsys,
                                                         tmp_path):
    sha = git(checkout, "rev-parse", "feature")
    sent = []

    class Resp:
        status_code = 200

        def json(self):
            return {"namespace": "my-ns", "name": "Toy", "snapshotId": 7}

    monkeypatch.setattr(inproc, "post", lambda req: sent.append(req) or Resp())
    assert inproc.main(["Toy", "upload", "--confirm", "--ref", "feature"]) == 0
    out = capsys.readouterr().out
    assert "agora://my-ns/Toy/7" in out and "DELETE" in out
    assert sent[0] == inproc.build("Toy", "feature", "my-ns", "Toy", str(checkout))["request"]
    rec = json.loads((tmp_path / "work" / "method-upload" / "my-ns__Toy__7.json").read_text())
    assert rec["sha"] == sha and rec["workflow"] == "Toy" and rec["methodUri"] == "agora://my-ns/Toy/7"


def test_agora_uri_parsing(inproc):
    assert inproc.agora_method("agora://a/b/3") == {
        "sourceRepo": "agora", "methodNamespace": "a", "methodName": "b", "methodVersion": 3,
        "methodUri": "agora://a/b/3"}
    for bad in ("agora://a/b", "agora://a/b/0", "dockstore://a/b/1", "agora://a/b/x", ""):
        with pytest.raises(ValueError):
            inproc.parse_agora_uri(bad)


# ------------------------------------------------------------------ the rerun with an Agora method

def rerun_env(tmp_path):
    return clean_env(HOME=str(tmp_path / "home"), GSVTK_CONFIG=str(FIX),
                     GSVTK_WORK=str(tmp_path / "work"), GSVTK_REPO_ROOT=str(ROOT),
                     GSVTK_TERRA_API_ROOT="http://127.0.0.1:9/api/")


def test_rerun_show_uses_the_agora_method(run_tool, tmp_path):
    pytest.importorskip("firecloud")
    r = run_tool(RERUN, "--method", "agora://my-ns/GenotypeBatch/3", "show", *PIN10,
                 env=rerun_env(tmp_path))
    assert r.returncode == 0, r.stderr
    m = json.loads(r.stdout)["methodRepoMethod"]
    assert m == {"sourceRepo": "agora", "methodNamespace": "my-ns", "methodName": "GenotypeBatch",
                 "methodVersion": 3, "methodUri": "agora://my-ns/GenotypeBatch/3"}
    assert "Agora agora://my-ns/GenotypeBatch/3" in r.stderr and "UNKNOWN" in r.stderr
    assert "Dockstore github.com" not in r.stderr


def test_rerun_show_without_method_stays_dockstore(run_tool, tmp_path):
    pytest.importorskip("firecloud")
    r = run_tool(RERUN, "show", *PIN10, env=rerun_env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["methodRepoMethod"]["sourceRepo"] == "dockstore"


def test_rerun_names_the_recorded_commit_and_refuses_another_workflow(run_tool, tmp_path):
    pytest.importorskip("firecloud")
    d = tmp_path / "work" / "method-upload"
    d.mkdir(parents=True)
    sha = "a" * 40
    (d / "my-ns__GenotypeBatch__3.json").write_text(json.dumps(
        {"workflow": "GenotypeBatch", "sha": sha, "ref": "feature", "repo": "https://x/gatk-sv.git"}))
    (d / "my-ns__Other__1.json").write_text(json.dumps({"workflow": "Other", "sha": sha}))
    ok = run_tool(RERUN, "--method", "agora://my-ns/GenotypeBatch/3", "show", *PIN10,
                  env=rerun_env(tmp_path))
    assert ok.returncode == 0, ok.stderr
    assert sha in ok.stderr and "grades that commit" in ok.stderr
    bad = run_tool(RERUN, "--method", "agora://my-ns/Other/1", "show", *PIN10, env=rerun_env(tmp_path))
    assert bad.returncode == 1 and "binds nothing in another" in bad.stderr


@pytest.mark.parametrize("value", ["", "agora://ns/name", "--confirm"])
def test_rerun_refuses_a_bad_method_flag(run_tool, tmp_path, value):
    pytest.importorskip("firecloud")
    r = run_tool(RERUN, "show", "--method", value, *PIN10, env=rerun_env(tmp_path))
    assert r.returncode == 1 and "--method" in r.stderr


@pytest.fixture
def rerun(monkeypatch, tmp_path):
    pytest.importorskip("firecloud")
    for k in [k for k in list(__import__("os").environ) if k.startswith("GSVTK_")]:
        monkeypatch.delenv(k)
    for k, v in rerun_env(tmp_path).items():
        if k.startswith("GSVTK_"):
            monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "argv", ["batch_rerun_step.py", "show"])
    import batch_rerun_step
    monkeypatch.setattr(batch_rerun_step, "METHOD", "")
    return batch_rerun_step


def test_publish_check_does_not_apply_to_agora(rerun, monkeypatch, capsys):
    monkeypatch.setattr(rerun, "METHOD", "agora://my-ns/GenotypeBatch/3")
    # A capture that would otherwise be read; the Agora branch must return before opening it.
    monkeypatch.setenv("GSVTK_PUBLISH_CAPTURE", "/nonexistent/capture.json")
    monkeypatch.setenv("GSVTK_PUBLISH_TIP", "b" * 40)
    rerun._publish_precheck({"method_path": "x"}, "submit")
    assert rerun.PUBLISH_VERDICT == "NOT APPLICABLE"
    assert "NOT APPLICABLE" in capsys.readouterr().out


def test_publish_check_still_applies_to_dockstore(rerun, monkeypatch, capsys):
    monkeypatch.delenv("GSVTK_PUBLISH_CAPTURE", raising=False)
    rerun._publish_precheck({"method_path": "x"}, "submit")
    assert rerun.PUBLISH_VERDICT == "NOT RUN"


def test_readback_compares_the_agora_form(rerun):
    m = {"sourceRepo": "agora", "methodNamespace": "n", "methodName": "W", "methodVersion": 3,
         "methodUri": "agora://n/W/3"}
    body = {"name": "c", "rootEntityType": "sample_set", "methodRepoMethod": m, "inputs": {},
            "outputs": {}}
    # The server may echo the snapshot as a string; that is the same method.
    same = dict(body, methodRepoMethod=dict(m, methodVersion="3"))
    assert rerun.config_diffs(body, same)[0] == []
    other = dict(body, methodRepoMethod=dict(m, methodVersion=4, methodUri="agora://n/W/4"))
    diffs = rerun.config_diffs(body, other)[0]
    assert any("methodVersion" in d for d in diffs) and any("methodUri" in d for d in diffs)
    dock = dict(body, methodRepoMethod={"sourceRepo": "dockstore", "methodPath": "p",
                                        "methodVersion": "b", "methodUri": "dockstore://p/b"})
    assert any("sourceRepo" in d for d in rerun.config_diffs(body, dock)[0])
