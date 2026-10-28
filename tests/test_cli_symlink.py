"""gsvtk run through a symlink (one on PATH, say) still finds its checkout."""
import subprocess

from conftest import ROOT


def test_symlinked_gsvtk_finds_the_checkout(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gsvtk").symlink_to(ROOT / "gsvtk")
    # A relative link in a chain, too: bin/gsvtk2 -> gsvtk -> <checkout>/gsvtk.
    (bindir / "gsvtk2").symlink_to("gsvtk")
    for name in ("gsvtk", "gsvtk2"):
        r = subprocess.run([str(bindir / name), "repo"], capture_output=True, text=True,
                           cwd=str(tmp_path), timeout=60)
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == str(ROOT.resolve())
