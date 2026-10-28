"""kit/gsvtk-config: profile precedence and parsing."""
import subprocess
import sys

import pytest

from conftest import ROOT, clean_env


def config(tmp_path, *args, home=None, cwd=None, **env):
    e = clean_env(HOME=str(home or tmp_path / "home"), **env)
    return subprocess.run([sys.executable, str(ROOT / "kit" / "gsvtk-config"), *args],
                          capture_output=True, text=True, env=e, cwd=cwd or tmp_path)


@pytest.fixture
def home(tmp_path):
    d = tmp_path / "home" / ".config" / "gatk-sv-testkit"
    d.mkdir(parents=True)
    return d


@pytest.mark.parametrize("low, high", [
    ("GSVTK_PROJECT=low", "PROJECT=high"),
    ("PROJECT=low", "GSVTK_PROJECT=high"),
    ("GSVTK_PROJECT=low", "GSVTK_PROJECT=high"),
])
def test_later_profile_wins_whatever_the_spelling(tmp_path, home, low, high):
    # cwd/testkit.env has lower precedence than ~/.config/gatk-sv-testkit/env.
    (tmp_path / "testkit.env").write_text(low + "\n")
    (home / "env").write_text(high + "\n")
    r = config(tmp_path, "get", "PROJECT")
    assert r.stdout.strip() == "high", r.stderr


def test_environment_beats_profiles(tmp_path, home):
    (home / "env").write_text("PROJECT=profile\n")
    assert config(tmp_path, "get", "PROJECT", GSVTK_PROJECT="env").stdout.strip() == "env"


@pytest.mark.parametrize("line, want", [
    ("PROJECT=a # note", "a"),
    ("PROJECT=a\t# note", "a"),
    ("PROJECT=path#with#hash", "path#with#hash"),
    ('PROJECT="quoted # kept"', "quoted # kept"),
    ("export PROJECT=a", "a"),
])
def test_value_parsing(tmp_path, line, want):
    f = tmp_path / "p.env"
    f.write_text(line + "\n")
    assert config(tmp_path, "get", "PROJECT", GSVTK_CONFIG=str(f)).stdout.strip() == want


@pytest.mark.parametrize("cmd", ["get", "require"])
def test_unknown_key_is_a_usage_error(tmp_path, cmd):
    r = config(tmp_path, cmd, "NO_SUCH_KEY", GSVTK_CONFIG="/dev/null")
    assert r.returncode == 2
    assert "unknown key" in r.stderr


def test_require_unset_exits_4(tmp_path):
    r = config(tmp_path, "require", "PROJECT", GSVTK_CONFIG="/dev/null")
    assert r.returncode == 4
