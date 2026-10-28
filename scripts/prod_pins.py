#!/usr/bin/env python3
"""Print the production python pins gatk-sv actually ships, out of its own dockerfiles.

Pins are read from the ref you name rather than copied here, so they can't go stale. Useful when
a local venv must match production (e.g. a newer pysam breaks a code path the shipped image runs).

    scripts/prod_pins.py --repo <gatk-sv clone> --ref <branch|tag|sha>
    scripts/prod_pins.py --repo <clone> --ref main --requirements /tmp/prod-pins.txt
    scripts/prod_pins.py --requirements -            # repo/ref from the profile, file to stdout

stdout is stable `key=value` lines, sorted, nothing else (provenance goes to stderr), so it can be
grepped or diffed. `python` is the interpreter the env is built on and pip cannot install it; it is
carried as a key anyway because a local venv has to match it, and `--requirements` turns it into a
comment.

Files read at `--ref` (relative to the repo root):

    dockerfiles/sv-pipeline-virtual-env/Dockerfile     the python env svtk runs in
    dockerfiles/samtools-cloud-virtual-env/Dockerfile  the python base that env copies from
    dockerfiles/sv-utils-env/Dockerfile                optional; cross-checks the pysam pin

They are read with `git show <ref>:<path>`, so the clone's working tree, index and HEAD are never
touched.

A missing file or a missing expected pin is a refusal naming the path, never an empty table.

Only `ARG`-carried pins are reported. Versions on install lines are ignored: the pysam block in
`sv-pipeline-virtual-env` installs one setuptools version temporarily and then restores another.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
import config  # noqa: E402

# Where the pins live. No version literals in this file.
PIPELINE_ENV = "dockerfiles/sv-pipeline-virtual-env/Dockerfile"      # the env svtk runs in
PYTHON_BASE = "dockerfiles/samtools-cloud-virtual-env/Dockerfile"    # its python base image
SV_UTILS_ENV = "dockerfiles/sv-utils-env/Dockerfile"                 # optional cross-check
OPTIONAL_FILES = (SV_UTILS_ENV,)

# ARG names that carry a python-level pin. OS/conda `*_RELEASE` ARGs are not mapped.
ARG_KEYS = {"PYSAM_VERSION": "pysam", "PYTHON_RELEASE": "python"}

# Pins whose absence means the dockerfile changed shape. Others are reported only if present.
REQUIRED_PINS = {"pysam": PIPELINE_ENV, "python": PYTHON_BASE, "pybedtools": PIPELINE_ENV}

# An exact pinned token in an ARG value: `numpy=1.22.3` (conda) or `pybedtools==0.9.0` (pip).
# Ranges are not pins. The lookbehind keeps paths and flags from matching.
TOKEN = re.compile(r"(?<![\w./-])([A-Za-z][A-Za-z0-9._+-]*)==?([0-9][0-9A-Za-z.]*)")

# One `ARG NAME=VALUE` line. Quoted or bare; `ARG` with no `=` (a pass-through) is not a pin.
ARG_LINE = re.compile(r"^\s*ARG\s+([A-Za-z_][A-Za-z0-9_]*)(?:\s*=\s*(.*))?$")


def arg_lines(text: str) -> list[str]:
    """`ARG` lines with Docker's `\\` continuations joined (gatk-sv splits its conda list)."""
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    return [ln for ln in joined.splitlines() if ln.lstrip().startswith("ARG")]


def strip_comment(raw: str) -> str:
    """Drop a trailing ` # ...` note: `ARG PYTHON_RELEASE="3.10.4"  # get releases from conda search`."""
    return raw.split(" #", 1)[0].strip().strip('"').strip("'").strip()


def git(repo: Path, *args: str) -> bytes | None:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def resolve_ref(repo: Path, ref: str) -> str:
    """Full SHA, or a refusal: a ref that does not resolve is not a ref that pins nothing."""
    out = git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if out is None:
        raise SystemExit(f"prod_pins: refusing -- {repo} has no ref {ref!r}.\n"
                         f"  git -C {repo} rev-parse --verify '{ref}^{{commit}}' failed. Fetch it, or "
                         f"name a branch/tag/SHA that exists.")
    return out.decode().strip()


def read_at_ref(repo: Path, sha: str, path: str) -> str:
    """One file's bytes at one commit, without touching the clone."""
    out = git(repo, "show", f"{sha}:{path}")
    if out is None:
        raise SystemExit(f"prod_pins: refusing -- {path} is not in {repo} @ {sha[:12]}.\n"
                         f"  git -C {repo} show {sha[:12]}:{path} failed. Either the ref is not a "
                         f"gatk-sv tree or upstream moved the dockerfile;\n  this tool prints pins "
                         f"it read, so it will not print an empty answer for a file it could not open.")
    return out.decode("utf-8", "replace")


def pins_from(text: str, source: str) -> list[tuple[str, str, str]]:
    """[(key, version, source)] from `ARG` lines only. Duplicates are kept for `merge()`."""
    found = []
    for line in arg_lines(text):
        m = ARG_LINE.match(line)
        if not m:
            continue
        name, raw = m.group(1), strip_comment(m.group(2) or "")
        if not raw or "$" in raw:                     # a pass-through or a derived path, not a pin
            continue
        if name in ARG_KEYS:
            found.append((ARG_KEYS[name], raw, source))
            continue
        found.extend((pkg, ver, source) for pkg, ver in TOKEN.findall(raw))
    return found


def merge(all_pins: dict[str, tuple[str, str]], found: list[tuple[str, str, str]],
          conflicts: list[str]) -> None:
    """Merge pins in, recording disagreements (pysam is pinned in two dockerfiles) as conflicts."""
    for key, ver, source in found:
        if key not in all_pins:
            all_pins[key] = (ver, source)
        elif all_pins[key][0] != ver:
            first_ver, first_src = all_pins[key]
            if first_src == source:
                conflicts.append(f"{key} is pinned twice in {source}, to {first_ver} and to {ver}")
            else:
                conflicts.append(f"{key} is pinned {first_ver} in {first_src} but {ver} in {source}")


def requirements_text(pins: dict[str, tuple[str, str]], repo: Path, ref: str, sha: str) -> str:
    """A pip-installable approximation of the shipped env, with its limits written into it."""
    py = pins.get("python", ("?", ""))[0]
    head = [
        f"# gatk-sv production python pins, read by scripts/prod_pins.py from {repo} @ {sha[:12]}",
    ]
    for source in sorted({s for _, s in pins.values()}):
        head.append(f"#   {source}  (ref {ref})")
    head += [
        f"# Build the venv on python {py}: most of these are conda pins resolved for that",
        "# interpreter, and the older numpy/scipy builds here have no wheels for a much newer one.",
        "# Conda pins are mapped to pip `==`. This is an approximation of the shipped env, not the",
        "# env: production builds pysam from a source tarball (the PYSAM_VERSION block, which also",
        "# downgrades setuptools for the build), so pip gives you the same version, not the same",
        "# build. `python` is a comment, not a requirement -- pip cannot install an interpreter.",
    ]
    if "pysam" in pins:
        head.append("# A local pysam newer than this is what produces "
                    "`[E::vcf_format] Invalid BCF, the INFO tag id=16 is too large`.")
    body = [f"{k}=={v}" for k, (v, _) in sorted(pins.items()) if k != "python"]
    return "\n".join(head + body) + "\n"


def selftest() -> int:
    """Build a two-dockerfile repo in a temp dir and check every refusal fires, each with its control.

    The fixture's conda list spans a backslash continuation, and the full pin count is asserted.
    Everything runs through the CLI.
    """
    import tempfile
    ENV = "dockerfiles/sv-pipeline-virtual-env/Dockerfile"
    BASE = "dockerfiles/samtools-cloud-virtual-env/Dockerfile"
    CONDA = ('ARG CONDA_PKGS="python=3.10.4 \\\n'
             '    numpy=1.22.3 pandas=1.4.2 pysam=0.15.4 scipy=1.7.3 \\\n'
             '    matplotlib=3.5.1 intervaltree=3.1.0 natsort=8.1.0 \\\n'
             '    pybedtools==0.9.0"   # conda style `=`, pip style `==` on the last one\n')
    PYSAM_BLOCK = "ARG PYSAM_VERSION=0.15.4\n"
    BASE_TXT = 'ARG PYTHON_RELEASE="3.10.4"  # get releases from conda search\n'
    FULL = {"python": "3.10.4", "numpy": "1.22.3", "pandas": "1.4.2", "pysam": "0.15.4",
            "scipy": "1.7.3", "matplotlib": "3.5.1", "intervaltree": "3.1.0", "natsort": "8.1.0",
            "pybedtools": "0.9.0"}

    ok = fail = 0

    def say(passed: bool, desc: str, detail: str = "") -> None:
        nonlocal ok, fail
        if passed:
            ok += 1
            print(f"  ok    {desc}")
        else:
            fail += 1
            print(f"  FAIL  {desc}{' :: ' + detail[:300] if detail else ''}")

    def build(root: Path, env_txt: str, base_txt: str | None) -> Path:
        (root / "dockerfiles" / "sv-pipeline-virtual-env").mkdir(parents=True, exist_ok=True)
        (root / ENV).write_text("FROM x\n" + env_txt)
        if base_txt is not None:
            (root / BASE).parent.mkdir(parents=True, exist_ok=True)
            (root / BASE).write_text("FROM y\n" + base_txt)
        for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@t"),
                     ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@t")):
            os.environ.setdefault(k, v)
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "fixture"]):
            subprocess.run(["git", "-C", str(root), *cmd], check=True, capture_output=True)
        return root

    def run(repo: Path, *extra: str) -> tuple[int, str]:
        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--repo", str(repo),
                            "--ref", "HEAD", *extra], capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    def shows(out: str, key: str, ver: str) -> bool:
        """key and ver on one line, tolerant of the column layout the tool prints."""
        return any(re.search(rf"(^|[\s=]){re.escape(key)}([\s=]|$)", ln) and ver in ln
                   for ln in out.splitlines())

    with tempfile.TemporaryDirectory(prefix="prod-pins-selftest-") as td:
        root = build(Path(td) / "full", CONDA + PYSAM_BLOCK, BASE_TXT)
        rc, out = run(root)
        missing = [f"{k}={v}" for k, v in FULL.items() if not shows(out, k, v)]
        # Assert the full count, so a parser that misses the continuation fails.
        n = sum(1 for ln in out.splitlines() if re.search(r"[A-Za-z][\w.-]*={1,2}[0-9]", ln))
        say(rc == 0 and not missing, "CONTROL: a complete two-file repo prints every pin",
            f"rc={rc} missing={missing} :: {out[:200]}")
        say(rc == 0 and n >= len(FULL), f"all {len(FULL)} pins survive the `\\` continuation",
            f"only {n} version-bearing lines")
        say(any("pybedtools" in ln and "0.9.0" in ln for ln in out.splitlines()),
            "pip-style `==` pins parse as well as conda-style `=`")

        rc2, req = run(root, "--requirements", "-")
        eq = [ln for ln in req.splitlines() if re.match(r"^[a-z][\w.-]*==[0-9]", ln)]
        say(rc2 == 0 and any(ln == "pysam==0.15.4" for ln in eq) and not
            any(ln.startswith("python==") for ln in eq)
            and len(eq) >= len(FULL) - 1,
            "--requirements emits pip-installable lines and leaves python a comment",
            f"rc={rc2} lines={eq}")

        drift = build(Path(td) / "drift", CONDA + "ARG PYSAM_VERSION=0.24.0\n", BASE_TXT)
        rc3, out3 = run(drift)
        say(rc3 != 0 and "0.15.4" in out3 and "0.24.0" in out3,
            "a package pinned twice to two versions refuses, naming BOTH",
            f"rc={rc3} :: {out3[-200:]}")

        nopin = build(Path(td) / "nopin", CONDA.replace(" pysam=0.15.4", ""), BASE_TXT)
        rc4, out4 = run(nopin)
        say(rc4 != 0 and "pysam" in out4 and "sv-pipeline-virtual-env" in out4,
            "a required pin that is gone refuses, naming the pin AND the file",
            f"rc={rc4} :: {out4[-200:]}")

        nofile = build(Path(td) / "nofile", CONDA + PYSAM_BLOCK, None)
        rc5, out5 = run(nofile)
        say(rc5 != 0 and "samtools-cloud-virtual-env" in out5,
            "a dockerfile that is not there refuses by path (not an empty answer)",
            f"rc={rc5} :: {out5[-200:]}")

        rc6, out6 = run(root, "--ref", "no-such-ref")
        say(rc6 != 0 and "no-such-ref" in out6,
            "a ref that does not resolve refuses -- a missing ref is not a ref that pins nothing",
            f"rc={rc6} :: {out6[-160:]}")

        plain = Path(td) / "plain"
        plain.mkdir()
        rc7, out7 = run(plain, "--ref", "HEAD")
        say(rc7 != 0 and "git repositor" in out7,
            "a --repo that is not a git repository refuses by name", f"rc={rc7} :: {out7[-160:]}")

    print(f"prod_pins selftest: {ok} ok, {fail} failed")
    return 1 if fail else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", default=config.get("GATK_SV_CHECKOUT"),
                    help="local gatk-sv clone (default GSVTK_GATK_SV_CHECKOUT)")
    ap.add_argument("--ref", default="origin/main",
                    help="branch, tag or SHA to read the dockerfiles at (default origin/main)")
    ap.add_argument("--requirements", nargs="?", const="-", default=None, metavar="PATH",
                    help="also emit a pip-installable file for a production-pinned local venv; "
                         "PATH, or bare for stdout")
    ap.add_argument("--selftest", action="store_true",
                    help="build a two-dockerfile fixture repo in a temp dir and prove the "
                         "refusals and the pin count (offline; reads nothing outside the temp dir)")
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    if not a.repo:
        raise SystemExit("prod_pins: refusing -- no --repo and GSVTK_GATK_SV_CHECKOUT is unset "
                         "(docs/config.md). A ref is meaningless without a repository.")
    repo = Path(a.repo).expanduser()
    if not (repo / ".git").exists():
        raise SystemExit(f"prod_pins: refusing -- {repo} is not a git repository "
                         f"(it wants a gatk-sv clone, read read-only).")
    sha = resolve_ref(repo, a.ref)

    # Required files first: a pin "absent" because the file moved must blame the file, not the pin.
    sources = [(p, read_at_ref(repo, sha, p)) for p in (PIPELINE_ENV, PYTHON_BASE)]
    for p in OPTIONAL_FILES:
        text = git(repo, "show", f"{sha}:{p}")
        if text is not None:
            sources.append((p, text.decode("utf-8", "replace")))

    pins: dict[str, tuple[str, str]] = {}
    conflicts: list[str] = []
    counts = []
    for path, text in sources:
        found = pins_from(text, path)
        merge(pins, found, conflicts)
        counts.append((path, len(found)))

    # Absent where it is required is a refusal; absent elsewhere is just not a pin upstream ships.
    for key, path in REQUIRED_PINS.items():
        if key not in pins:
            shape = (f"ARG {next(k for k, v in ARG_KEYS.items() if v == key)}=<version>"
                     if key in ARG_KEYS else f"{key}=<version> inside an ARG CONDA_PKGS/PIP_PKGS list")
            raise SystemExit(f"prod_pins: refusing -- no {key} pin found in {path} @ {sha[:12]}.\n"
                             f"  looked in: {repo} @ {a.ref} = {sha[:12]}:{path}\n"
                             f"  expected a line shaped like: {shape}\n"
                             "  An empty answer here would read as 'production pins nothing', so it "
                             "is an error instead:\n  either upstream renamed the ARG (update "
                             "ARG_KEYS/REQUIRED_PINS here) or the ref predates the pin.")

    for path, n in counts:
        print(f"prod_pins: {repo} @ {sha[:12]} ({a.ref})  {path}: {n} pin(s)", file=sys.stderr)
    for key, ver in sorted(pins.items()):
        print(f"{key}={ver[0]}")
    rc = 0
    for c in conflicts:
        # Printed, not swallowed: this is the tool's own reason for existing, caught upstream.
        print(f"prod_pins: DRIFT -- {c}\n  which pin is meant to win is not this tool's call, so the "
              f"answer is not unique and the exit code says so.", file=sys.stderr)
        rc = 1

    if a.requirements is not None:
        text = requirements_text(pins, repo, a.ref, sha)
        if a.requirements == "-":
            sys.stdout.write(text)
        else:
            dest = Path(a.requirements).expanduser()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
            print(f"prod_pins: wrote {dest}", file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
