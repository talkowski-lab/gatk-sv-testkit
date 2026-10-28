"""scripts/check_compat.py against a synthetic gatk-sv repo built in tmp_path. Offline."""
import importlib.util
import json
import subprocess
from types import SimpleNamespace

import pytest

from conftest import ROOT

TOOL = "scripts/check_compat.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_compat", ROOT / TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def _write(repo, rel, text="x\n"):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def mod():
    return _load()


@pytest.fixture
def gsv(tmp_path, mod):
    """A repo carrying every path the layout check reads, for the real profiles' steps."""
    repo = tmp_path / "gatk-sv"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    for path in mod.LAYOUT + mod.LAYOUT_BRANCH:
        _write(repo, path + ("/a.json" if "." not in path.rsplit("/", 1)[-1] else ""))
    entries = []
    for _module, steps in mod.load_profiles():
        for step in steps:
            _write(repo, "wdl/%s.wdl" % step["wdl"])
            entries.append(step.get("dockstore") or step["workflow"])
    _write(repo, mod.DOCKSTORE_YML,
           "version: 1.2\nworkflows:\n" + "".join("  - name: %s\n" % e for e in sorted(set(entries))))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    return repo


def _args(**kw):
    base = dict(repo="", ref="HEAD", role="baseline", record=False, check="")
    base.update(kw)
    return SimpleNamespace(**base)


def test_baseline_layout_is_compatible(mod, gsv, capsys):
    assert mod.cmd_check(_args(repo=str(gsv))) == 0
    out = capsys.readouterr().out
    assert "verdict=compatible failed=0" in out
    assert "src/sv_shell" not in out                      # sv-shell is branch-only


def test_missing_path_is_not_compatible(mod, gsv, capsys):
    _git(gsv, "rm", "-q", "-r", "inputs/values")
    _git(gsv, "commit", "-q", "-m", "drop")
    assert mod.cmd_check(_args(repo=str(gsv))) == 1
    out = capsys.readouterr().out
    assert "FAIL  layout inputs/values/dockers.json" in out and "verdict=not-compatible failed=1" in out


def test_branch_role_needs_profiles_to_agree(mod, gsv, capsys, monkeypatch):
    def fake_run(cmd, **kw):
        module = cmd[cmd.index("--module") + 1]
        line = "GSVTK-DERIVE-CHECK compared=9 disagree=%d expected=1 not-compared=0" % (
            2 if module == "genotyping" else 0)
        return SimpleNamespace(returncode=1 if module == "genotyping" else 0, stdout=line, stderr="")
    real_run = subprocess.run
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: (
        fake_run(cmd) if "derive_profile.py" in " ".join(map(str, cmd)) else real_run(cmd, **kw)))
    assert mod.cmd_check(_args(repo=str(gsv), role="branch")) == 1
    out = capsys.readouterr().out
    assert "FAIL  profile genotyping" in out and "2 disagree" in out
    assert "ok    profile single_sample" in out
    assert "ok    layout src/sv_shell/single_sample_pipeline.sh" in out


def test_record_writes_one_entry_per_ref_and_role(mod, gsv, tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "COMPAT", tmp_path / "compat.json")
    sha = _git(gsv, "rev-parse", "HEAD")
    _git(gsv, "update-ref", "refs/remotes/origin/main", sha)
    for _ in range(2):
        assert mod.cmd_check(_args(repo=str(gsv), ref="origin/main", record=True)) == 0
    doc = json.loads((tmp_path / "compat.json").read_text())
    assert [(e["ref"], e["role"], e["sha"], e["verdict"]) for e in doc["refs"]] == \
        [("main", "baseline", sha, "compatible")]


def test_table_check(mod, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mod, "COMPAT", tmp_path / "compat.json")
    (tmp_path / "compat.json").write_text(json.dumps({"refs": [
        {"ref": "main", "sha": "a" * 40, "role": "branch", "verdict": "compatible", "checked": "2026-01-01"}]}))
    table = mod.render_table(mod.read_compat())
    readme = tmp_path / "README.md"
    readme.write_text("x\n%s\n%s\n%s\ny\n" % (mod.TABLE_BEGIN, table, mod.TABLE_END))
    assert mod.cmd_table(_args(check=str(readme))) == 0
    readme.write_text(readme.read_text().replace("2026-01-01", "2025-01-01"))
    assert mod.cmd_table(_args(check=str(readme))) == 1
    readme.write_text("no markers\n")
    assert mod.cmd_table(_args(check=str(readme))) == 1
    assert "`aaaaaaaa`" in table


def test_note_counts_commits_past_the_checked_one(mod, gsv, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mod, "COMPAT", tmp_path / "compat.json")
    sha = _git(gsv, "rev-parse", "HEAD")
    (tmp_path / "compat.json").write_text(json.dumps({"refs": [
        {"ref": "main", "sha": sha, "role": "branch", "verdict": "compatible", "checked": "2026-01-01"}]}))
    assert mod.cmd_note(_args(repo=str(gsv))) == 0
    assert capsys.readouterr().err == ""                  # HEAD is the checked commit
    _write(gsv, "more.txt")
    _git(gsv, "add", "-A")
    _git(gsv, "commit", "-q", "-m", "two")
    assert mod.cmd_note(_args(repo=str(gsv))) == 0
    assert "is 1 commit(s) past the newest commit" in capsys.readouterr().err
    _git(gsv, "checkout", "-q", "--orphan", "other")
    _git(gsv, "commit", "-q", "-m", "unrelated")
    assert mod.cmd_note(_args(repo=str(gsv))) == 0
    assert "is not descended from" in capsys.readouterr().err


def test_refusals(run_tool, gsv, tmp_path):
    r = run_tool(TOOL, "check", "--repo", gsv, "--ref", "no-such-ref")
    assert r.returncode == 2 and "is not a commit" in r.stderr
    r = run_tool(TOOL, "check", "--repo", tmp_path / "nowhere")
    assert r.returncode == 4 and "is not a git clone" in r.stderr
    r = run_tool(TOOL, "note", "--repo", tmp_path / "nowhere")
    assert r.returncode == 0 and r.stderr == ""
