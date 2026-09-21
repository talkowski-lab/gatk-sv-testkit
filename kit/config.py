"""Python entry point to the shared configuration.

Import this instead of hardcoding a project, workspace, registry or scratch path:

    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "kit"))
    import config
    ns = config.require("TERRA_NAMESPACE")     # exits 4 with an instruction if unset
    batch = config.get("BATCH", "all_samples") # never fails
    out = config.work_dir("manifests")         # .../work/manifests, created

Resolution is delegated to the sibling ``gsvtk-config`` program so that bash and
Python cannot drift; see ``docs/config.md`` for the precedence rules and
``testkit.env.example`` for every key.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = ROOT / "kit" / "gsvtk-config"


def _load():
    # gsvtk-config has no .py suffix (it is an executable), so load it by path.
    spec = importlib.util.spec_from_loader(
        "gsvtk_config_core", importlib.machinery.SourceFileLoader("gsvtk_config_core", str(_CONFIG_PATH))
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["gsvtk_config_core"] = module
    spec.loader.exec_module(module)
    return module


_core = _load()

REPO_ROOT = _core.REPO_ROOT
KEYS = tuple(_core.DEFAULTS)


def get(key: str, fallback: str = "") -> str:
    """Value for KEY (with or without the GSVTK_ prefix), else FALLBACK."""
    return _core.get(key) or fallback


def require(key: str, hint: str = "") -> str:
    """Value for KEY, or exit 4 printing which profile file to edit."""
    try:
        return _core.require(key)
    except SystemExit:
        if hint:
            print(f"  {hint}", file=sys.stderr)
        raise


def describe(key: str) -> str:
    """One-line help for KEY, for argparse defaults and error text."""
    entry = _core.DEFAULTS.get(key.removeprefix("GSVTK_").upper())
    return entry[1] if entry else ""


def resolve() -> dict[str, tuple[str, str]]:
    """key -> (value, provenance) for every known key."""
    return _core.resolve()


def work_dir(*subdirs: str) -> Path:
    """Absolute scratch dir, creating any subdirs. Never a source path."""
    root = Path(require("WORK")).expanduser()
    path = root.joinpath(*subdirs) if subdirs else root
    path.mkdir(parents=True, exist_ok=True)
    return path


def checkout(kind: str = "GATK_SV_CHECKOUT") -> Path:
    """A local clone of gatk-sv (or gatk), verified to be a git repo."""
    raw = require(kind, f"{describe(kind)}")
    path = Path(raw).expanduser()
    if not (path / ".git").exists():
        print(f"kit/config: {kind}={path} is not a git repository; "
              f"clone it or point the key at an existing checkout", file=sys.stderr)
        raise SystemExit(4)
    return path


def pythonpath_hint() -> str:
    """How a sibling tool should have found this module — for error text."""
    return f'sys.path.insert(0, r"{ROOT / "kit"}")'
