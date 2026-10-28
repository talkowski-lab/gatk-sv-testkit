"""Run every tool's built-in `--selftest` as one pytest case each."""
import subprocess

import pytest

from conftest import EXIT_MISSING_DEP, ROOT


def _tools_with_selftest():
    out = subprocess.run(["git", "grep", "-l", "--", "--selftest", "--", "*.py"],
                         cwd=ROOT, capture_output=True, text=True).stdout.split()
    return sorted(p for p in out if not p.startswith(("tests/", "scripts/selftest.d/")))


@pytest.mark.parametrize("tool", _tools_with_selftest())
def test_selftest(tool, run_tool):
    r = run_tool(tool, "--selftest", timeout=600)
    # Exit 3 is the documented code; the terra/ import guard exits 1 with this message instead.
    if r.returncode == EXIT_MISSING_DEP or "missing dependency" in r.stdout + r.stderr:
        pytest.skip(f"{tool}: missing optional dependency: {(r.stdout + r.stderr).strip()[-200:]}")
    assert r.returncode == 0, f"{tool} --selftest exited {r.returncode}\n{r.stdout[-3000:]}\n{r.stderr[-2000:]}"
