"""Offline unit tests for terra/ helpers (no network: only pure functions are called)."""
import sys

import pytest

from conftest import ROOT

pytest.importorskip("firecloud")
sys.path[:0] = [str(ROOT / "terra"), str(ROOT / "kit")]


@pytest.fixture(scope="module")
def batch_status():
    import batch_status
    return batch_status


@pytest.mark.parametrize("state", ["Succeeded", "Failed", "Aborted"])
def test_finished_workflow_shows_terra_estimate(batch_status, state):
    assert batch_status.fmt_est(1.25, state) == "cost=1.25"


@pytest.mark.parametrize("state", ["Running", "Submitted", "Queued"])
def test_running_workflow_is_pending(batch_status, state):
    assert batch_status.fmt_est(1.25, state).startswith("cost=pending")


def test_region_slice_keeps_records(monkeypatch, tmp_path):
    import stage_inputs
    seen = []

    class FakePopen:
        def __init__(self, argv, **kw):
            seen.append(argv)
            self.stdout = None
            self.returncode = 0

        def communicate(self, *a, **k):
            return b"", b""

        def wait(self, *a, **k):
            return 0

    monkeypatch.setattr(stage_inputs.subprocess, "Popen", FakePopen)
    try:
        stage_inputs.tabix_slice("gs://b/x.vcf.gz", "chr1:1-100", str(tmp_path / "out.vcf.gz"))
    except Exception:
        pass  # only the tabix argv matters here
    tabix = next(a for a in seen if a[0] == "tabix")
    assert "-h" in tabix and "-H" not in tabix


def test_config_listing_asks_for_every_repo(monkeypatch):
    # Without allRepos Terra lists Agora configs only, so a Dockstore workspace reads as empty.
    import terra
    seen = {}

    def fake_rest(path, what, params=None):
        seen["rest"] = params
        return [{"name": "c"}]

    def fake_fiss(ns, name, **kw):
        seen["fiss"] = kw
        return [{"name": "c"}]

    monkeypatch.setattr(terra, "rest_get", fake_rest)
    monkeypatch.setattr(terra, "_j", lambda r, what: r)
    monkeypatch.setattr(terra.fapi, "list_workspace_configs", fake_fiss)
    assert terra.workspace_configs("ns", "ws") == [{"name": "c"}]
    assert seen == {"rest": {"allRepos": "true"}, "fiss": {"allRepos": True}}
