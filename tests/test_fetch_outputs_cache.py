"""terra/fetch_outputs.py --resolve-cached, on synthetic Cromwell metadata (offline, no network).

The callCaching shapes here follow Cromwell's documented metadata; no real cache-hit run was
sampled, so these pin the logic, not the field layout.
"""
import copy
import json
import sys
import types

import pytest

from conftest import ROOT, clean_env

TOOL = "terra/fetch_outputs.py"
HIT_WF = "aaaaaaaa-0000-0000-0000-000000000001"
RUN_WF = "bbbbbbbb-0000-0000-0000-000000000002"


def hit_cc(result=f"Cache Hit: {HIT_WF}:W.C:-1"):
    cc = {"hit": True, "effectiveCallCachingMode": "ReadAndWriteCache", "allowResultReuse": True,
          "hashes": {"command template": "ABC"}}
    if result is not None:
        cc["result"] = result
    return cc


def run_meta():
    return {
        "id": RUN_WF, "status": "Succeeded", "workflowName": "W",
        "outputs": {"W.direct_vcf": "gs://b/run/call-A/direct.vcf",
                    "W.cached_vcf": "gs://b/run/call-B/cacheCopy/cached.vcf",
                    "W.hit_vcf": None,
                    "W.n": 7},
        "calls": {
            "W.A": [{"shardIndex": -1, "attempt": 1, "executionStatus": "Done",
                     "outputs": {"direct_vcf": "gs://b/run/call-A/direct.vcf"},
                     "callCaching": {"hit": False, "result": "Cache Miss"}}],
            "W.B": [{"shardIndex": -1, "attempt": 1, "executionStatus": "Done",
                     "outputs": {"cached_vcf": "gs://b/run/call-B/cacheCopy/cached.vcf"},
                     "callCaching": hit_cc(f"Cache Hit: {HIT_WF}:W.B:-1")}],
            # The cached call keeps the output's name but no path for it.
            "W.C": [{"shardIndex": -1, "attempt": 1, "executionStatus": "Done",
                     "outputs": {"hit_vcf": None}, "callCaching": hit_cc()}],
        }}


def hit_meta():
    return {"id": HIT_WF, "status": "Succeeded",
            "calls": {"W.C": [{"shardIndex": -1, "attempt": 1, "executionStatus": "Done",
                               "outputs": {"hit_vcf": "gs://b/old/call-C/hit.vcf"}}]}}


@pytest.fixture
def env(tmp_path):
    (tmp_path / "empty.env").write_text("")
    work = tmp_path / "work"
    objs = tmp_path / "objects"
    objs.mkdir()
    for name in ("direct.vcf", "cached.vcf", "hit.vcf"):
        (objs / name).write_text(f"bytes of {name}\n")
    e = clean_env(GSVTK_CONFIG=str(tmp_path / "empty.env"), GSVTK_WORK=str(work))
    return types.SimpleNamespace(tmp=tmp_path, work=work, objs=objs, env=e,
                                 manifest=work / "manifests" / "outputs_manifest.json")


def write(tmp, name, doc):
    path = tmp / name
    path.write_text(json.dumps(doc))
    return path


@pytest.fixture
def tool(run_tool, env):
    def go(meta, *args, hit=None):
        mpath = write(env.tmp, "run.json", meta)
        extra = []
        if hit is not None:
            extra = ["--hit-metadata", write(env.tmp, "hit.json", hit)]
        return run_tool(TOOL, "--metadata", mpath, *args, *extra, env=env.env)
    return go


# ------------------------------------------------------------------------------ routes

def test_direct(tool):
    r = tool(run_meta(), "--output", "direct_vcf", "--resolve-cached", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "RESOLVED-DIRECT  W.direct_vcf  gs://b/run/call-A/direct.vcf" in r.stdout
    assert "UNVERIFIED" in r.stdout
    assert "--dry-run: nothing downloaded" in r.stdout


def test_cachecopy(tool):
    r = tool(run_meta(), "--output", "cached_vcf", "--resolve-cached", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "RESOLVED-CACHECOPY  W.cached_vcf  gs://b/run/call-B/cacheCopy/cached.vcf" in r.stdout
    assert "bytes identical to the cache hit's" in r.stdout


def test_cachecopy_from_call_outputs_when_root_is_null(tool):
    meta = run_meta()
    meta["calls"]["W.C"][0]["outputs"] = {"hit_vcf": "gs://b/run/call-C/cacheCopy/hit.vcf"}
    r = tool(meta, "--output", "hit_vcf", "--resolve-cached", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "RESOLVED-CACHECOPY  W.hit_vcf  gs://b/run/call-C/cacheCopy/hit.vcf" in r.stdout


def test_via_hit_offline(tool):
    r = tool(run_meta(), "--output", "hit_vcf", "--resolve-cached", "--dry-run", hit=hit_meta())
    assert r.returncode == 0, r.stderr
    assert f"RESOLVED-VIA-HIT {HIT_WF}:W.C:-1  W.hit_vcf  gs://b/old/call-C/hit.vcf" in r.stdout
    assert "gsutil -m cp -n gs://b/old/call-C/hit.vcf" in r.stdout


def test_via_hit_in_saved_tree_child(tool):
    # A hit workflow saved as a sub-workflow node of a batch_save_metadata tree is found by id.
    tree = {"id": "root-wf", "calls": {}, "_children": [hit_meta()]}
    r = tool(run_meta(), "--output", "hit_vcf", "--resolve-cached", "--dry-run", hit=tree)
    assert r.returncode == 0, r.stderr
    assert "RESOLVED-VIA-HIT" in r.stdout


def test_via_hit_scattered_shards_keep_their_own_route(tool):
    meta = run_meta()
    meta["calls"]["W.C"] = [
        {"shardIndex": 0, "attempt": 1, "executionStatus": "Done",
         "outputs": {"hit_vcf": "gs://b/run/call-C/shard-0/fresh.vcf"}},
        {"shardIndex": 1, "attempt": 1, "executionStatus": "Done", "outputs": {"hit_vcf": None},
         "callCaching": hit_cc(f"Cache Hit: {HIT_WF}:W.C:1")}]
    hit = hit_meta()
    hit["calls"]["W.C"] = [{"shardIndex": 1, "attempt": 1, "executionStatus": "Done",
                            "outputs": {"hit_vcf": "gs://b/old/shard-1/hit.vcf"}}]
    r = tool(meta, "--output", "hit_vcf", "--resolve-cached", "--dry-run", hit=hit)
    assert r.returncode == 0, r.stderr
    assert "RESOLVED-DIRECT  W.hit_vcf[0]  gs://b/run/call-C/shard-0/fresh.vcf" in r.stdout
    assert f"RESOLVED-VIA-HIT {HIT_WF}:W.C:1  W.hit_vcf[1]  gs://b/old/shard-1/hit.vcf" in r.stdout


def test_call_flag_names_an_undeclared_output(tool):
    meta = run_meta()
    meta["calls"]["W.C"][0]["outputs"] = {}
    r = tool(meta, "--output", "W.side_vcf", "--resolve-cached", "--dry-run",
             "--call", "W.side_vcf=W.C:-1@hit_vcf", hit=hit_meta())
    assert r.returncode == 0, r.stderr
    assert f"RESOLVED-VIA-HIT {HIT_WF}:W.C:-1  W.side_vcf" in r.stdout


def test_all_records_scalars_and_resolves_the_rest(tool):
    r = tool(run_meta(), "--all", "--resolve-cached", "--dry-run", hit=hit_meta())
    assert r.returncode == 0, r.stderr
    for line in ("RESOLVED-DIRECT  W.direct_vcf", "RESOLVED-CACHECOPY  W.cached_vcf",
                 "RESOLVED-VIA-HIT", "[value]  W.n = 7"):
        assert line in r.stdout


def test_retry_attempts_use_the_last_done_attempt(tool):
    meta = run_meta()
    meta["calls"]["W.C"] = [
        {"shardIndex": -1, "attempt": 1, "executionStatus": "Failed", "outputs": {}},
        {"shardIndex": -1, "attempt": 2, "executionStatus": "Done", "outputs": {"hit_vcf": None},
         "callCaching": hit_cc()}]
    r = tool(meta, "--output", "hit_vcf", "--resolve-cached", "--dry-run", hit=hit_meta())
    assert r.returncode == 0, r.stderr
    assert "RESOLVED-VIA-HIT" in r.stdout


# ------------------------------------------------------------------------------ manifest

def test_manifest_records_route_per_file(tool, env):
    r = tool(run_meta(), "--output", "direct_vcf", "--output", "cached_vcf", "--output", "hit_vcf",
             "--resolve-cached", "--link-dir", env.objs, hit=hit_meta())
    assert r.returncode == 0, r.stderr
    doc = json.loads(env.manifest.read_text())
    files = doc["files"]
    assert files["W.direct_vcf"]["route"] == "RESOLVED-DIRECT"
    assert files["W.cached_vcf"]["route"] == "RESOLVED-CACHECOPY"
    assert files["W.hit_vcf"]["route"] == f"RESOLVED-VIA-HIT {HIT_WF}:W.C:-1"
    assert files["W.hit_vcf"]["gs"] == "gs://b/old/call-C/hit.vcf"
    assert all(len(f["sha256"]) == 64 for f in files.values())
    assert "UNVERIFIED" in doc["cache_resolution"]
    v = tool(run_meta(), "--verify")
    assert v.returncode == 0 and "3 recorded, 0 problem(s)" in v.stdout, v.stdout


def test_manifest_without_resolve_has_no_route(tool, env):
    r = tool(run_meta(), "--output", "direct_vcf", "--link-dir", env.objs)
    assert r.returncode == 0, r.stderr
    doc = json.loads(env.manifest.read_text())
    assert "route" not in doc["files"]["W.direct_vcf"]
    assert "cache_resolution" not in doc


# ------------------------------------------------------------------------------ refusals

def refused(r, code, name):
    assert r.returncode == code, (r.returncode, r.stdout, r.stderr)
    assert f"REFUSED-{name}" in r.stdout
    assert "nothing was fetched" in r.stderr


def test_refuse_result_absent(tool, env):
    meta = run_meta()
    meta["calls"]["W.C"][0]["callCaching"] = hit_cc(result=None)
    r = tool(meta, "--output", "hit_vcf", "--resolve-cached", "--link-dir", env.objs,
             hit=hit_meta())
    refused(r, 7, "HIT-RESULT-ABSENT")
    assert not env.manifest.exists()


@pytest.mark.parametrize("result", ["Cache Hit", "Cache Hit: onlyawf", f"Cache Hit: {HIT_WF}:W.C:x",
                                    "Cache Miss"])
def test_refuse_result_malformed(tool, result):
    meta = run_meta()
    meta["calls"]["W.C"][0]["callCaching"] = hit_cc(result=result)
    refused(tool(meta, "--output", "hit_vcf", "--resolve-cached", "--dry-run", hit=hit_meta()),
            7, "HIT-RESULT-MALFORMED")


def test_refuse_hit_workflow_not_found_offline(tool):
    r = tool(run_meta(), "--output", "hit_vcf", "--resolve-cached", "--dry-run")
    refused(r, 7, "HIT-WORKFLOW-NOT-FOUND")
    assert "--hit-metadata FILE" in r.stdout


def test_refuse_hit_call_or_shard_missing(tool):
    hit = hit_meta()
    hit["calls"]["W.C"][0]["shardIndex"] = 3
    refused(tool(run_meta(), "--output", "hit_vcf", "--resolve-cached", "--dry-run", hit=hit),
            7, "HIT-CALL-NOT-FOUND")
    hit = hit_meta()
    hit["calls"] = {"W.Other": hit["calls"]["W.C"]}
    refused(tool(run_meta(), "--output", "hit_vcf", "--resolve-cached", "--dry-run", hit=hit),
            7, "HIT-CALL-NOT-FOUND")


def test_refuse_hit_output_absent(tool):
    hit = hit_meta()
    hit["calls"]["W.C"][0]["outputs"] = {"other": "gs://b/x"}
    refused(tool(run_meta(), "--output", "hit_vcf", "--resolve-cached", "--dry-run", hit=hit),
            7, "HIT-OUTPUT-ABSENT")


def test_refuse_not_a_cache_hit(tool):
    meta = run_meta()
    meta["calls"]["W.C"][0]["callCaching"] = {"hit": False, "result": "Cache Miss"}
    refused(tool(meta, "--output", "hit_vcf", "--resolve-cached", "--dry-run"), 4, "NOT-A-CACHE-HIT")


def test_refuse_no_producing_call(tool):
    meta = run_meta()
    del meta["calls"]["W.C"]
    refused(tool(meta, "--output", "hit_vcf", "--resolve-cached", "--dry-run"), 4,
            "NO-PRODUCING-CALL")


def test_refuse_no_producing_call_names_cache_hit_candidates(tool):
    # A cached call whose outputs map is empty cannot be matched by name; it is offered for --call.
    meta = run_meta()
    meta["calls"]["W.C"][0]["outputs"] = {}
    r = tool(meta, "--output", "hit_vcf", "--resolve-cached", "--dry-run", hit=hit_meta())
    refused(r, 4, "NO-PRODUCING-CALL")
    assert "W.C:-1" in r.stdout


def test_refuse_ambiguous_call(tool):
    meta = run_meta()
    meta["calls"]["W.D"] = copy.deepcopy(meta["calls"]["W.C"])
    r = tool(meta, "--output", "hit_vcf", "--resolve-cached", "--dry-run", hit=hit_meta())
    refused(r, 2, "AMBIGUOUS-CALL")
    assert "--call W.hit_vcf=" in r.stdout


def test_every_output_is_reported_before_exit(tool):
    meta = run_meta()
    meta["calls"]["W.C"][0]["callCaching"] = hit_cc(result=None)
    r = tool(meta, "--all", "--resolve-cached", "--dry-run")
    refused(r, 7, "HIT-RESULT-ABSENT")
    assert "RESOLVED-DIRECT  W.direct_vcf" in r.stdout
    assert "plan " not in r.stdout


@pytest.mark.parametrize("args,needle", [
    (["--hit-metadata", "x.json"], "only apply with --resolve-cached"),
    (["--online"], "only apply with --resolve-cached"),
    (["--resolve-cached", "--refuse-cached"], "contradiction"),
    (["--resolve-cached", "--hit-submission", "s1"], "needs --online"),
    (["--resolve-cached", "--max-submissions", "0"], "--max-submissions 0"),
    (["--resolve-cached", "--call", "nonsense"], "want NAME=CALL_FQN"),
])
def test_usage_refusals(tool, args, needle):
    r = tool(run_meta(), "--output", "hit_vcf", *args, "--dry-run")
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert needle in r.stderr


def test_unreadable_hit_metadata(tool, env):
    r = tool(run_meta(), "--output", "hit_vcf", "--resolve-cached", "--dry-run",
             "--hit-metadata", env.tmp / "nope.json")
    assert r.returncode == 5
    assert "--hit-metadata" in r.stderr


def test_online_needs_a_workspace(tool):
    r = tool(run_meta(), "--output", "hit_vcf", "--resolve-cached", "--online", "--dry-run")
    assert r.returncode == 4, (r.stdout, r.stderr)


def test_read_only_mode_still_resolves(tool, env):
    # The tool never mutates cloud state, so GSVTK_READ_ONLY=1 does not refuse it.
    env.env["GSVTK_READ_ONLY"] = "1"
    r = tool(run_meta(), "--output", "hit_vcf", "--resolve-cached", "--dry-run", hit=hit_meta())
    assert r.returncode == 0, r.stderr


# ------------------------------------------------------------------------------ online (faked terra)

class FakeTerra(types.ModuleType):
    """Stands in for terra.py: two submissions, the hit workflow in the second."""

    class TerraError(RuntimeError):
        pass

    def __init__(self, metas):
        super().__init__("terra")
        self.metas = metas
        self.calls = []
        fake = self

        class Resp:
            def __init__(self, body, code=200):
                self.body, self.status_code = body, code

            def json(self):
                return self.body

        class Fapi:
            @staticmethod
            def get_workflow_metadata(ns, ws, sub, wf):
                fake.calls.append(("metadata", sub, wf))
                if wf in fake.metas:
                    return Resp(fake.metas[wf])
                return Resp({"message": "not found"}, 404)

        self.fapi = Fapi

    def submissions(self, ns, ws, limit=20):
        self.calls.append(("submissions", ns, ws, limit))
        return {"submissions": [{"submissionId": "s1"}, {"submissionId": "s2"}][:limit]}

    def submission(self, ns, ws, sid):
        self.calls.append(("submission", sid))
        wfs = {"s1": [{"workflowId": RUN_WF}], "s2": [{"workflowId": HIT_WF}]}[sid]
        return {"workflows": wfs}

    def _j(self, resp, what):
        if resp.status_code != 200:
            raise self.TerraError(f"{what}: HTTP {resp.status_code}")
        return resp.json()


@pytest.fixture
def inproc(env, monkeypatch, capsys):
    monkeypatch.setenv("GSVTK_CONFIG", env.env["GSVTK_CONFIG"])
    monkeypatch.setenv("GSVTK_WORK", env.env["GSVTK_WORK"])
    for k in ("GSVTK_TERRA_NAMESPACE", "GSVTK_TERRA_WORKSPACE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.syspath_prepend(str(ROOT / "kit"))
    monkeypatch.syspath_prepend(str(ROOT / "terra"))
    import fetch_outputs

    def go(fake, *args):
        monkeypatch.setitem(sys.modules, "terra", fake)
        mpath = write(env.tmp, "run.json", run_meta())
        try:
            code = fetch_outputs.main(["--metadata", str(mpath), "--output", "hit_vcf",
                                       "--resolve-cached", "--online", "--ns", "NS", "--ws", "WS",
                                       "--dry-run", *args])
        except SystemExit as e:
            code = e.code
        out = capsys.readouterr()
        return code, out.out, out.err
    return go


def test_online_search_finds_the_hit(inproc):
    fake = FakeTerra({HIT_WF: hit_meta()})
    code, out, err = inproc(fake)
    assert code == 0, err
    assert f"RESOLVED-VIA-HIT {HIT_WF}:W.C:-1  W.hit_vcf  gs://b/old/call-C/hit.vcf" in out
    assert ("metadata", "s2", HIT_WF) in fake.calls
    assert ("submissions", "NS", "WS", 50) in fake.calls


def test_online_hit_submission_skips_the_search(inproc):
    fake = FakeTerra({HIT_WF: hit_meta()})
    code, out, err = inproc(fake, "--hit-submission", "s9")
    assert code == 0, err
    assert fake.calls == [("metadata", "s9", HIT_WF)]


def test_online_search_is_capped(inproc):
    fake = FakeTerra({HIT_WF: hit_meta()})
    code, out, err = inproc(fake, "--max-submissions", "1")
    assert code == 7
    assert "REFUSED-HIT-WORKFLOW-NOT-FOUND" in out and "--max-submissions 1" in out
    assert [c for c in fake.calls if c[0] == "submission"] == [("submission", "s1")]


def test_online_metadata_error_is_a_named_refusal(inproc):
    fake = FakeTerra({})
    code, out, err = inproc(fake, "--hit-submission", "s2")
    assert code == 7
    assert "REFUSED-HIT-WORKFLOW-NOT-FOUND" in out and "HTTP 404" in out


def test_offline_hit_metadata_wins_over_online(inproc, env):
    fake = FakeTerra({})
    hit = write(env.tmp, "hit.json", hit_meta())
    code, out, err = inproc(fake, "--hit-metadata", str(hit))
    assert code == 0, err
    assert fake.calls == []


# ------------------------------------------------------------------------------ unchanged behaviour

def cached_fixture(env):
    return write(env.tmp, "md-cached.json", {
        "id": "wf1", "status": "Succeeded",
        "outputs": {"W.final_vcf": "gs://b/run/x/final.vcf.gz",
                    "W.sv_calls": "gs://b/wf1/call-abc/cacheCopy/sv.vcf", "W.n": "7"}})


def test_refuse_cached_unchanged(run_tool, env):
    r = run_tool(TOOL, "--metadata", cached_fixture(env), "--refuse-cached", env=env.env)
    assert r.returncode == 7
    assert "resolve into a call-cache copy" in r.stderr
    assert "CACHED  W.sv_calls" in r.stdout and "1 of 3 declared output(s)" in r.stdout


def test_cache_report_unchanged(run_tool, env):
    r = run_tool(TOOL, "--metadata", cached_fixture(env), "--cache-report", env=env.env)
    assert r.returncode == 0
    assert "1 of 3 declared output(s)" in r.stdout and "the count above is the finding" in r.stdout


def test_null_output_without_resolve_is_still_exit_4(tool):
    r = tool(run_meta(), "--output", "hit_vcf", "--dry-run")
    assert r.returncode == 4
    assert "IS declared but carries no value" in r.stderr


def test_cache_report_runs_alongside_resolve(tool):
    r = tool(run_meta(), "--output", "cached_vcf", "--cache-report", "--resolve-cached", "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "CACHED  W.cached_vcf" in r.stdout and "RESOLVED-CACHECOPY" in r.stdout


def test_string_outputs_are_values_not_artifacts():
    import fetch_outputs
    assert fetch_outputs.values_of("Wf.count", "7", "Succeeded") == []
    assert fetch_outputs.values_of("Wf.names", ["a", "b"], "Succeeded") == []
    assert fetch_outputs.values_of("Wf.vcf", "gs://b/x.vcf.gz", "Succeeded") == ["gs://b/x.vcf.gz"]


def test_mixed_file_and_non_file_values_still_refuse():
    import fetch_outputs
    with pytest.raises(SystemExit):
        fetch_outputs.values_of("Wf.mixed", ["gs://b/x", "not-a-path"], "Succeeded")
