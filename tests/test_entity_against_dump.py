"""checks/terra_entity_check.py --against-dump and terra/dump_configs.py, offline (synthetic dump, fake terra)."""
import json
import os
import sys
import types

from conftest import ROOT, clean_env

TOOL = "checks/terra_entity_check.py"


def _cfg(name, root, inputs):
    return {"namespace": "cns", "name": name, "rootEntityType": root, "inputs": inputs,
            "outputs": {}, "methodRepoMethod": {"methodUri": "dockstore://x"}}


def _dump(tmp_path, configs, fn="dump.json"):
    p = tmp_path / fn
    p.write_text(json.dumps({"namespace": "ns", "workspace": "ws", "listed": len(configs),
                             "configs": configs, "errors": []}))
    return p


AGREE = _cfg("Agree", "sample_set", {"W.batch": "this.sample_set_id",
                                     "W.vcfs": "this.samples.vcf", "W.ref": "workspace.ref"})
# Template form `${...}` is read as well as Terra's bare form.
AGREE_TMPL = _cfg("AgreeTmpl", "sample_set_set", {"M.cohort": "${this.sample_set_set_id}"})
DISAGREE = _cfg("Disagree", "sample_set", {"M.cohort": "this.sample_set_set_id"})
NO_KEY = _cfg("NoKey", "sample", {"W.x": "this.bam", "W.lit": "\"this.sample_id\""})
MANY = _cfg("Many", "sample", {"W.a": "this.sample_id", "W.b": "this.batch_id"})
NO_INPUTS = _cfg("NoInputs", "sample", {})


def test_all_agree_exits_0(run_tool, tmp_path):
    r = run_tool(TOOL, "--against-dump", _dump(tmp_path, [AGREE, AGREE_TMPL]))
    assert r.returncode == 0, r.stdout
    assert "GSVTK-ENTITY-TERRA config=cns/Agree AGREE" in r.stdout
    assert "GSVTK-ENTITY-TERRA config=cns/AgreeTmpl AGREE" in r.stdout


def test_disagree_exits_1(run_tool, tmp_path):
    r = run_tool(TOOL, "--against-dump", _dump(tmp_path, [AGREE, DISAGREE, NO_KEY]))
    assert r.returncode == 1, r.stdout
    assert ("GSVTK-ENTITY-TERRA config=cns/Disagree DISAGREE derived=sample_set_set terra=sample_set"
            in r.stdout)


def test_underivable_reasons(run_tool, tmp_path):
    r = run_tool(TOOL, "--against-dump", _dump(tmp_path, [AGREE, NO_KEY, MANY, NO_INPUTS]))
    assert r.returncode == 0, r.stdout
    # A quoted "this.sample_id" is a string literal to Terra, not a read.
    assert "config=cns/NoKey UNDERIVABLE no-name-key" in r.stdout
    assert "config=cns/Many UNDERIVABLE many-name-keys(batch,sample)" in r.stdout
    assert "config=cns/NoInputs UNDERIVABLE no-inputs" in r.stdout


def test_summary_line(run_tool, tmp_path):
    r = run_tool(TOOL, "--against-dump", _dump(tmp_path, [AGREE, DISAGREE, NO_KEY]))
    assert r.stdout.splitlines()[-1] == (
        "GSVTK-ENTITY-TERRA-SUMMARY configs=3 agree=1 disagree=1 underivable=1 derivable=2 "
        "verdict=DISAGREE")


def test_empty_dump_is_refused(run_tool, tmp_path):
    r = run_tool(TOOL, "--against-dump", _dump(tmp_path, []))
    assert r.returncode == 2, r.stdout
    assert "configs=0 agree=0 disagree=0 underivable=0 derivable=0 verdict=NOTHING-DERIVABLE" in r.stdout
    assert "refused:" in r.stdout


def test_only_underivable_is_refused(run_tool, tmp_path):
    r = run_tool(TOOL, "--against-dump", _dump(tmp_path, [NO_KEY, MANY]))
    assert r.returncode == 2, r.stdout


def test_unreadable_dump_and_flag_mix(run_tool, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    r = run_tool(TOOL, "--against-dump", bad)
    assert r.returncode == 2 and "refused: cannot read" in r.stdout
    r = run_tool(TOOL, "--against-dump", _dump(tmp_path, [AGREE]), "--tree", tmp_path)
    assert r.returncode == 2 and "usage error: --against-dump" in r.stdout


def test_bare_list_dump_accepted(run_tool, tmp_path):
    p = tmp_path / "list.json"
    p.write_text(json.dumps([AGREE]))
    assert run_tool(TOOL, "--against-dump", p).returncode == 0


# --------------------------------------------------------------------------- dump tool (fake terra, no network)

class _FakeTerra(types.ModuleType):
    def __init__(self, payloads, fail=()):
        super().__init__("terra")
        self.payloads, self.fail, self.calls = payloads, set(fail), []

    def workspace_configs(self, ns, ws):
        self.calls.append(("list", ns, ws))
        return [{"namespace": p["namespace"], "name": p["name"],
                 "rootEntityType": p["rootEntityType"]} for p in self.payloads]

    def config_payload(self, ns, ws, cns, name):
        self.calls.append(("get", ns, ws, cns, name))
        if name in self.fail:
            raise RuntimeError("HTTP 404")
        return next(p for p in self.payloads if p["name"] == name)

    def dump(self, obj, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)       # as terra.dump does
        with open(path, "w") as fh:
            json.dump(obj, fh)
        return path

    def __getattr__(self, attr):
        # Any mutating helper (submit, upload_entities_tsv, create_workspace, ...) is a test failure.
        raise AssertionError("dump_configs touched terra.%s" % attr)


def _load_dump_tool(monkeypatch, fake, env):
    for k in [k for k in list(os.environ) if k.startswith("GSVTK_")]:
        monkeypatch.delenv(k)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setitem(sys.modules, "terra", fake)
    monkeypatch.syspath_prepend(str(ROOT / "terra"))
    sys.modules.pop("dump_configs", None)
    import dump_configs
    return dump_configs


def test_dump_tool_is_read_only(monkeypatch, tmp_path, capsys):
    fake = _FakeTerra([AGREE, DISAGREE])
    out = tmp_path / "d.json"
    tool = _load_dump_tool(monkeypatch, fake, {"GSVTK_READ_ONLY": "1",
                                                  "GSVTK_CONFIG": str(tmp_path / "none.env")})
    rc = tool.main(["--ns", "bns", "--ws", "bws", "--out", str(out)])
    assert rc == 0
    assert [c[0] for c in fake.calls] == ["list", "get", "get"]
    assert capsys.readouterr().out.splitlines()[-1] == str(out)
    data = json.loads(out.read_text())
    assert data["namespace"] == "bns" and data["workspace"] == "bws"
    assert [c["name"] for c in data["configs"]] == ["Agree", "Disagree"]
    assert data["configs"][0]["inputs"]["W.batch"] == "this.sample_set_id"


def test_dump_tool_defaults_to_sandbox_and_feeds_the_check(monkeypatch, tmp_path, capsys, run_tool):
    fake = _FakeTerra([AGREE])
    env = tmp_path / "p.env"
    env.write_text("GSVTK_TERRA_NAMESPACE=sns\nGSVTK_TERRA_WORKSPACE=sws\nGSVTK_WORK=%s\n" % tmp_path)
    tool = _load_dump_tool(monkeypatch, fake, {"GSVTK_CONFIG": str(env)})
    assert tool.main([]) == 0
    path = capsys.readouterr().out.splitlines()[-1]
    assert path == str(tmp_path / "configs" / "sns__sws.json")
    assert fake.calls[0] == ("list", "sns", "sws")
    r = run_tool(TOOL, "--against-dump", path, env=clean_env())
    assert r.returncode == 0 and "workspace sns/sws" in r.stdout


def test_dump_tool_records_fetch_failure(monkeypatch, tmp_path, capsys):
    fake = _FakeTerra([AGREE, DISAGREE], fail=["Disagree"])
    out = tmp_path / "d.json"
    tool = _load_dump_tool(monkeypatch, fake, {"GSVTK_CONFIG": str(tmp_path / "none.env")})
    assert tool.main(["--ns", "a", "--ws", "b", "--out", str(out)]) == 1
    data = json.loads(out.read_text())
    assert len(data["configs"]) == 1 and data["errors"][0]["name"] == "Disagree"
