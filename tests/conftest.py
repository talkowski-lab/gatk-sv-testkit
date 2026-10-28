import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Exit code every tool uses for a missing optional dependency (see `gsvtk --help`).
EXIT_MISSING_DEP = 3


def clean_env(**extra):
    """The caller's environment minus GSVTK_*, so a test never reads the developer's own config."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GSVTK_")}
    env.update(extra)
    return env


@pytest.fixture
def run_tool():
    """Run a repo script with this interpreter; returns CompletedProcess (text mode)."""
    def run(rel, *args, env=None, cwd=None, timeout=300):
        return subprocess.run([sys.executable, str(ROOT / rel), *map(str, args)],
                              capture_output=True, text=True, timeout=timeout,
                              env=env if env is not None else clean_env(), cwd=cwd or ROOT)
    return run
