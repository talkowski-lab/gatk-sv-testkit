#!/usr/bin/env python3
"""Offline probes for defects a review of this toolkit confirmed, and for their fixes.

Why a probe file instead of prose
--------------------------------
Every defect below was proven by *running* it once (the review record quotes the output), and a
fix that is only described in a commit message regresses the moment someone touches the file
again. Each probe therefore drives the guard through the exact invocation that used to defeat it
and asserts the guard fires — **plus a positive control** proving the guarded code path was
reachable at all, because "the guard never fired" and "the guard could not have fired" print the
same way. Same rule `scripts/selftest.sh` keeps for the checkers.

Nothing here contacts Terra or GCE, needs credentials, or writes outside one temp directory:
the Terra layer is replaced with a recorder that counts requests, so "zero requests left the
machine" is a measured number rather than an expectation.

    python scripts/probe_fixes.py            # all probes, tally + nonzero on any failure
    python scripts/probe_fixes.py -v         # also print each probe's own detail lines

Probes (each names the defect it pins):
    rerun_guards         batch_rerun_step POSTed to an unresolved or shared-baseline target
    stage_batch_row      stage_inputs hardcoded one batch name and selected nothing at exit 0
    adopt_fallback       the copy-after-failed-hardlink path raised TypeError instead of copying
    wdl_flat_dup         two files declaring one name were both emitted into one document
    publish_guard        frozen attributes published with no recorded crc32c verify
    miniwdl_resolver     the checker was reported absent while installed in the project venv
    help_writes_nothing  printing --help created scratch directories
    dstore_drift         the rerun config duplicates batch_configs' Dockstore URI by hand
    map_vs_wdl           method-config keys were never compared to the target ref's WDL offline, so
                         a branch-only input reached Rawls and was rejected as an extra input
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "kit"))
sys.path.insert(0, str(ROOT / "terra"))

VERBOSE = False
_PROFILE_N = 0
TMP = Path(tempfile.mkdtemp(prefix="gsvtk-probe-"))


def say(msg: str) -> None:
    if VERBOSE:
        print(f"        {msg}")


def tmpdir(name: str) -> Path:
    p = TMP / name
    p.mkdir(parents=True, exist_ok=True)
    return p


@contextlib.contextmanager
def env(**kv):
    """Set (or with None, unset) environment variables for the block."""
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def fresh(profile: dict, *modules: str):
    """Point GSVTK_CONFIG at `profile` and (re)import `modules` so they read it.

    Module-level config reads (`BATCH`, `STAGE`, `MANIFEST`, `REPO`, the Terra target) happen at
    import, so a probe that changes a key has to import the module again — which is also exactly
    what a fresh process would do. WORK always goes to the temp tree: a probe must never drop a
    file inside a real $GSVTK_WORK.
    """
    import config
    global _PROFILE_N
    _PROFILE_N += 1
    i = _PROFILE_N
    path = TMP / f"profile-{i}.env"
    lines = dict(profile)
    lines.setdefault("GSVTK_WORK", str(TMP / "work"))
    path.write_text("".join(f"{k}={v}\n" for k, v in lines.items()))
    os.environ["GSVTK_CONFIG"] = str(path)
    importlib.reload(config)
    mods = []
    for name in modules:
        mods.append(importlib.reload(sys.modules[name])
                    if name in sys.modules else importlib.import_module(name))
    return mods


def have(dep: str) -> bool:
    try:
        return importlib.util.find_spec(dep) is not None
    except (ImportError, ValueError):
        return False


class Skip(Exception):
    """A probe whose optional dependency is not installed (not a failure)."""


# ---------------------------------------------------------------- Terra recorder
REQUESTS: list = []


class _Resp:
    status_code = 201
    text = '{"name":"recorded"}'

    def json(self):
        return {"name": "recorded", "invalid": [], "extraInputs": []}


def install_recorder(terra) -> None:
    """Replace every mutating/reading fiss call the rerun tool makes with a counter.

    The point is not to fake Terra; it is to make "no request left this machine" observable. The
    control phase of the same probe proves the recorder does record, so a silent recorder can
    never make a refusal look like a fix.
    """
    def rec(name):
        def f(*a, **k):
            REQUESTS.append((name,) + tuple(str(x) for x in a))
            return _Resp()
        return f
    for n in ("create_workspace_config", "overwrite_workspace_config", "validate_config",
              "create_submission", "list_submissions", "get_submission"):
        setattr(terra.fapi, n, rec(n))


# ------------------------------------------------------------------ the probes
def probe_rerun_guards() -> str:
    """batch_rerun_step must refuse an unresolved target and the shared baseline workspace.

    Before the fix, `create` never called assert_writable_target (batch_configs.py create does),
    and `validate`/`submit` never resolved the target, so both POSTed with an empty workspace path
    -- and submit's shared-target guard compared ('','') against the baseline coordinates and
    passed.
    """
    if not have("firecloud"):
        raise Skip("firecloud")
    import terra
    install_recorder(terra)
    base_ns = importlib.reload(importlib.import_module("config")).get("BASELINE_NAMESPACE")
    base_ws = importlib.import_module("config").get("BASELINE_WORKSPACE")
    if not (base_ns and base_ws):
        raise Skip("baseline workspace defaults are unset in this profile")

    no_target = {"GSVTK_PROJECT": "probe-project", "GSVTK_BRANCH": "probe-branch"}
    sandbox = dict(no_target, GSVTK_TERRA_NAMESPACE="probe-sandbox-ns",
                   GSVTK_TERRA_WORKSPACE="probe-sandbox-ws")
    baseline = dict(no_target, GSVTK_TERRA_NAMESPACE=base_ns, GSVTK_TERRA_WORKSPACE=base_ws)

    refused = []
    # (a) unresolved target: validate and submit must exit 4 naming a key, sending nothing.
    (bc, r) = fresh(no_target, "batch_configs", "batch_rerun_step")
    r.CONFIRMED = r.ALLOW_UNPINNED = True
    for mode in ("validate", "submit"):
        REQUESTS.clear()
        try:
            getattr(r, mode)()
            raise AssertionError(f"{mode}() reached the network with no target resolved")
        except SystemExit as e:
            if e.code != 4:
                raise AssertionError(f"{mode}() exited {e.code}, expected 4 (name the missing key)")
            if REQUESTS:
                raise AssertionError(f"{mode}() sent {len(REQUESTS)} request(s): {REQUESTS}")
        refused.append(mode)

    # (b) target IS the shared baseline workspace: create must refuse before any request.
    (bc, r) = fresh(baseline, "batch_configs", "batch_rerun_step")
    r.CONFIRMED = r.ALLOW_UNPINNED = True
    REQUESTS.clear()
    try:
        r.create()
        raise AssertionError("create() POSTed a method config into the shared baseline workspace")
    except (SystemExit, terra.TerraError) as e:
        if "baseline workspace" not in str(e):
            raise AssertionError(f"create() refused for the wrong reason: {str(e)[:120]}")
        if REQUESTS:
            raise AssertionError(f"create() sent {len(REQUESTS)} request(s): {REQUESTS}")
    refused.append("create(shared baseline)")

    # POSITIVE CONTROL: the same create() against a real sandbox posts exactly one config.
    (bc, r) = fresh(sandbox, "batch_configs", "batch_rerun_step")
    r.CONFIRMED = r.ALLOW_UNPINNED = True
    REQUESTS.clear()
    r.create()
    if len(REQUESTS) != 1:
        raise AssertionError(f"control: sandbox create sent {len(REQUESTS)} requests, expected 1")
    say(f"control: posted {REQUESTS[0][0]} into {REQUESTS[0][1]}/{REQUESTS[0][2]}")
    return f"{len(refused)} states refused with 0 requests, control posted 1"


def probe_stage_batch_row() -> str:
    """stage_inputs must read the configured sample_set row, not one hardcoded batch name."""
    if not have("firecloud"):
        raise Skip("firecloud")
    manifest = {"steps": {}, "entities": {"sample_set": {
        "probe_batch": {"cutoffs": "gs://probe-bucket/probe.cutoffs",
                        "median_cov": "gs://probe-bucket/probe_medianCov.transposed.bed"},
        "other_batch": {"cutoffs": "gs://probe-bucket/other.cutoffs"}}}}

    (si,) = fresh({"GSVTK_PROJECT": "p", "GSVTK_BATCH": "probe_batch"}, "stage_inputs")
    if si.BATCH != "probe_batch":
        raise AssertionError(f"stage_inputs.BATCH is {si.BATCH!r}, not the configured GSVTK_BATCH")
    want = si.collect(manifest, set(), ["cutoffs", "median_cov"])
    if set(want) != {"cutoffs", "median_cov"}:
        raise AssertionError(f"configured batch selected {sorted(want)}, expected both attributes")
    if want["cutoffs"] != "gs://probe-bucket/probe.cutoffs":
        raise AssertionError(f"selected the wrong object: {want['cutoffs']}")
    say(f"control: selected {len(want)} objects from row 'probe_batch'")

    # A batch name that is not in the manifest must SELECT NOTHING *and SAY SO*: the original bug
    # was silent (no note at all, exit 0), so an empty selection is only half the assertion.
    (si,) = fresh({"GSVTK_PROJECT": "p", "GSVTK_BATCH": "absent_batch"}, "stage_inputs")
    buf = io.StringIO()
    code = None
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            want2 = si.collect(manifest, set(), ["cutoffs"])
    except SystemExit as e:
        code, want2 = e.code, {}
    out = buf.getvalue() + f" exit={code}"
    if want2:
        raise AssertionError("a batch row that does not exist selected objects anyway")
    if code not in (1, 2):
        raise AssertionError(f"a missing batch row exited {code!r}: selecting nothing on a wrong "
                             f"GSVTK_BATCH must stop the run, not exit 0")
    if "absent_batch" not in out or "probe_batch" not in out:
        raise AssertionError(f"the refusal did not name the batch and the rows present: {out!r}")
    return f"configured row -> {len(want)} objects; absent row -> exit {code} naming the rows"


def probe_adopt_fallback() -> str:
    """When the hardlink cannot be made, the local copy must be COPIED, not raise TypeError."""
    if not have("firecloud"):
        raise Skip("firecloud")
    (si,) = fresh({"GSVTK_PROJECT": "p"}, "stage_inputs")
    src = tmpdir("adopt_src")
    binlog = tmpdir("adopt_bin")
    dest_dir = tmpdir("adopt_dest")
    cand = src / "probe.cutoffs"
    cand.write_bytes(b"probe" * 10)
    log = binlog / "calls.log"
    log.write_text("")
    stub = binlog / "gsutil"
    # Records its argv, then behaves like `gsutil -m cp -n SRC DEST` for our one use.
    stub.write_text("#!/bin/sh\n"
                    "printf '%s\\n' \"$*\" >> \"$GSV_STUB_LOG\"\n"
                    "[ \"$1\" = \"-m\" ] && shift\n"
                    "if [ -f \"$3\" ] && [ ! -e \"$4\" ]; then cp \"$3\" \"$4\"; fi\n"
                    "exit 0\n")
    stub.chmod(0o755)

    si.remote_size = lambda uri: cand.stat().st_size
    si.verify_against_remote = lambda local, uri, size: "crc32c:ok"
    uri = "gs://probe-bucket/probe.cutoffs"
    os.environ["GSV_STUB_LOG"] = str(log)
    with env(PATH=f"{binlog}{os.pathsep}{os.environ['PATH']}"):
        # (1) hardlink works -> adoption links, and the copy path is never touched.
        dest_ok = dest_dir / "linked.cutoffs"
        got = si.adopt_from_local(uri, [str(src)], str(dest_ok))
        if not got or got["path"] != str(cand):
            raise AssertionError(f"hardlink adoption returned {got!r}")
        if os.stat(dest_ok).st_ino != os.stat(cand).st_ino:
            raise AssertionError("control: adoption did not hardlink the candidate")
        if log.read_text().strip():
            raise AssertionError("control: gsutil ran even though the hardlink succeeded")
        say("control: hardlinked (same inode), no copy attempted")

        # (2) force the EXDEV that a --link-dir on another volume really produces.
        real_link = os.link

        def exdev(*a, **k):
            raise OSError(18, "Invalid cross-device link", a[0] if a else "")
        os.link = exdev
        try:
            dest_copy = dest_dir / "copied.cutoffs"
            log.write_text("")
            got2 = si.adopt_from_local(uri, [str(src)], str(dest_copy))
        finally:
            os.link = real_link
        calls = [l for l in log.read_text().splitlines() if l.strip()]
        if not got2:
            raise AssertionError("cross-device adoption returned nothing")
        if len(calls) != 1 or "cp" not in calls[0] or str(dest_copy) not in calls[0]:
            raise AssertionError(f"cross-device adoption ran gsutil {calls}, expected one cp to "
                                 f"{dest_copy}")
        if not dest_copy.exists():
            raise AssertionError("the copy fallback did not produce the destination file")
    return "linked when possible (0 copies), copied via gsutil when os.link fails (1 call)"


def _wdl_repo(name: str, second_task: str) -> Path:
    """A synthetic gatk-sv-shaped checkout: <repo>/wdl/{Entry,B,C}.wdl."""
    repo = tmpdir(name)
    wdl = repo / "wdl"
    wdl.mkdir(exist_ok=True)
    (wdl / "Entry.wdl").write_text(
        'version 1.0\nimport "B.wdl"\nimport "C.wdl"\n\nworkflow Entry {\n  call Dup\n'
        f"  call {second_task}\n}}\n")
    (wdl / "B.wdl").write_text("version 1.0\n\ntask Dup {\n  command {}\n}\n")
    (wdl / "C.wdl").write_text(f"version 1.0\n\ntask {second_task} {{\n  command {{}}\n}}\n")
    return repo


def probe_wdl_flat_dup() -> str:
    """Two different files declaring one name must be refused, not emitted twice."""
    dup = _wdl_repo("flat_dup", "Dup")
    (wf,) = fresh({"GSVTK_PROJECT": "p", "GSVTK_GATK_SV_CHECKOUT": str(dup)}, "wdl_flat")
    try:
        wf.flatten("Entry")
        raise AssertionError("flattened a document declaring task Dup twice")
    except SystemExit as e:
        msg = str(e)
        if "B.wdl" not in msg or "C.wdl" not in msg:
            raise AssertionError(f"refused without naming both files: {msg[:160]}")
        say(f"refused: {msg.splitlines()[0]}")

    ok = _wdl_repo("flat_ok", "Other")
    (wf,) = fresh({"GSVTK_PROJECT": "p", "GSVTK_GATK_SV_CHECKOUT": str(ok)}, "wdl_flat")
    text = wf.flatten("Entry")
    tasks = [l for l in text.splitlines() if l.startswith("task ")]
    wfs = [l for l in text.splitlines() if l.startswith("workflow ")]
    if len(tasks) != 2 or len(wfs) != 1:
        raise AssertionError(f"control: expected 2 tasks + 1 workflow, got {len(tasks)} + {len(wfs)}")
    return f"collision refused naming both files; control flattened {len(tasks)} tasks"


def probe_publish_guard() -> str:
    """attrs must refuse to publish without a PASSED verify on record -- all three states."""
    if not have("firecloud"):
        raise Skip("firecloud")
    (bf,) = fresh({"GSVTK_PROJECT": "p", "GSVTK_TERRA_NAMESPACE": "probe-sandbox-ns",
                   "GSVTK_TERRA_WORKSPACE": "probe-sandbox-ws"}, "batch_freeze")
    d = tmpdir("publish")
    missing = d / "nope.json"
    bad = d / "failed.json"
    bad.write_text(json.dumps({"verified": False, "mismatches": [{"attr": "merged_PE"}]}))
    corrupt = d / "corrupt.json"
    corrupt.write_text("{not json")
    good = d / "good.json"
    good.write_text(json.dumps({"verified": True, "mismatches": []}))

    n_refused = 0
    for label, path in (("no manifest", missing), ("failed verify", bad), ("unreadable", corrupt)):
        try:
            bf.publish_guard(str(path))
            raise AssertionError(f"publish_guard let {label} through")
        except SystemExit as e:
            if "verify" not in str(e):
                raise AssertionError(f"{label}: refusal does not name `verify`: {str(e)[:100]}")
            n_refused += 1
            say(f"refused {label}")
    bf.publish_guard(str(good))                                  # must NOT raise
    bf.publish_guard(str(missing), allow_unverified=True)         # explicit escape, prints a note
    return f"{n_refused} unproven states refused; verified:true and --allow-unverified pass"


def _load_config_copy(name: str):
    """Load kit/gsvtk-config as a second, independently patchable module instance."""
    path = ROOT / "kit" / "gsvtk-config"
    spec = importlib.util.spec_from_loader(name, importlib.machinery.SourceFileLoader(name, str(path)))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def probe_miniwdl_resolver() -> str:
    """The checker must be found in the project venv, not reported absent.

    This is the shape that broke: `.venv/bin/python terra/wdl_flat.py --check` in a shell that
    never activated the venv, where `command -v miniwdl` / `shutil.which` see nothing.
    """
    fake = tmpdir("fake_repo/.venv/bin")
    fake.mkdir(parents=True, exist_ok=True)
    exe = fake / "miniwdl"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    empty = tmpdir("empty_repo")
    mod = _load_config_copy("gsvtk_config_probe")
    orig_repo, orig_exe = mod.REPO_ROOT, sys.executable
    try:
        with env(PATH=str(tmpdir("empty_path")), MINIWDL=None):
            mod.REPO_ROOT = tmpdir("fake_repo")
            sys.executable = str(fake / "python")            # sibling-of-interpreter candidate
            found = mod.find_miniwdl()
            if found != str(exe):
                raise AssertionError(f"resolver returned {found!r}, expected the venv script {exe}")
            say(f"resolved through the venv: {found}")

            sys.executable = str(empty / "python")            # nothing anywhere
            mod.REPO_ROOT = empty
            none = mod.find_miniwdl()
            if none:
                raise AssertionError(f"resolver invented a path: {none!r}")

            with env(MINIWDL=str(exe)):                       # explicit override wins
                if mod.find_miniwdl() != str(exe):
                    raise AssertionError("MINIWDL override ignored")
    finally:
        mod.REPO_ROOT, sys.executable = orig_repo, orig_exe
    cli = subprocess.run([sys.executable, str(ROOT / "kit" / "gsvtk-config"), "miniwdl"],
                         capture_output=True, text=True)
    if cli.returncode != 0:
        raise AssertionError(f"`gsvtk-config miniwdl` exited {cli.returncode}: {cli.stderr[:120]}")
    if not os.access(cli.stdout.strip(), os.X_OK):
        raise AssertionError(f"the CLI resolver printed a non-executable: {cli.stdout!r}")
    return f"venv sibling resolved, absent -> '' , override honoured; CLI -> {cli.stdout.strip()}"


def probe_help_writes_nothing() -> str:
    """`--help` must not create scratch directories (it is what import-time path building did)."""
    tools = ["terra/wdl_flat.py", "compare/diff_rd_states.py", "examples/recompute_het_population.py"]
    if have("firecloud"):
        tools += ["terra/batch_configs.py", "terra/batch_freeze.py", "terra/stage_inputs.py",
                  "terra/recon.py"]
    work = TMP / "help_work"
    profile = TMP / "help_profile.env"
    profile.write_text("GSVTK_PROJECT=probe-project\n")
    ran = 0
    for t in tools:
        r = subprocess.run([sys.executable, str(ROOT / t), "--help"], capture_output=True,
                           text=True, env=dict(os.environ, GSVTK_WORK=str(work),
                                               GSVTK_CONFIG=str(profile)))
        if r.returncode != 0:
            raise AssertionError(f"{t} --help exited {r.returncode}: {(r.stderr or r.stdout)[:160]}")
        ran += 1
    if work.exists():
        left = sorted(str(p.relative_to(work)) for p in work.rglob("*"))
        raise AssertionError(f"--help created {work} ({left}) while only printing usage")
    # POSITIVE CONTROL: the same WORK really is writable/creatable when a tool needs it.
    r = subprocess.run([sys.executable, str(ROOT / "kit" / "gsvtk-config"), "work", "runs/probe"],
                       capture_output=True, text=True,
                       env=dict(os.environ, GSVTK_WORK=str(work), GSVTK_CONFIG=str(profile)))
    if r.returncode != 0 or not Path(r.stdout.strip()).is_dir():
        raise AssertionError(f"control: gsvtk-config work did not create the directory ({r.stdout!r})")
    shutil.rmtree(work, ignore_errors=True)
    return f"{ran} tools print usage, 0 directories created; control created one"


def probe_dstore_drift() -> str:
    """batch_rerun_step rebuilds the Dockstore URI by hand; it must equal batch_configs'."""
    if not have("firecloud"):
        raise Skip("firecloud")
    (bc, r) = fresh({"GSVTK_PROJECT": "p", "GSVTK_BRANCH": "v1.1.1"}, "batch_configs",
                    "batch_rerun_step")
    bc.BRANCH = "probe-branch-sha"
    a, b = r.dstore("probe-branch-sha"), bc.dockstore("GenotypeBatch")
    if a != b:
        raise AssertionError(f"dstore() drifted from dockstore(): {a} != {b}")
    if "%2F" not in a["methodUri"] or a["methodUri"].count("%2F") != 3:
        raise AssertionError(f"methodUri is not the %-encoded form Rawls expects: {a['methodUri']}")
    if r.dstore("other-version") == a:
        raise AssertionError("control: the version is not actually part of the constructed URI")
    return f"identical for one version, {a['methodUri'].count('%2F')} %2F encodings, differs on another"



def _fixture_wdl(dirpath: Path, drop, add_required: bool) -> None:
    """A one-workflow WDL tree declaring exactly what the config under test binds (or does not).

    `drop` removes one declared input so the config's binding for it becomes an EXTRA; `add_required`
    adds a File with no default the config cannot bind -- the other half of the check, because a
    checker that only looks one way reports that tree as clean.
    """
    bc = sys.modules["batch_configs"]
    declared = sorted(k.split(".")[-1] for k in bc.CONFIGS["10-GenotypeBatch"]["inputs"]
                      if k.startswith("GenotypeBatch."))
    declared = [d for d in declared if d != drop]
    body = "\n".join(f"    File {d}" if d != "batch" else "    String batch" for d in declared)
    if add_required:
        body += "\n    File probe_required_unbound"
    (dirpath / "wdl").mkdir(parents=True, exist_ok=True)
    (dirpath / "wdl" / "GenotypeBatch.wdl").write_text(
        "version 1.1\n"
        "workflow GenotypeBatch {\n"
        "  input {" + "\n" + body + "\n  }\n"
        "  call probe_task\n"
        '  output { File o = probe_task.out }\n'
        "}\n"
        'task probe_task {\n  command <<< echo x > out >>>\n  output { File out = "out" }\n'
        '  runtime { docker: "x" }\n}\n')


def probe_map_vs_wdl() -> str:
    """batch_configs must refuse to POST keys the target ref's WDL does not declare.

    The defect: CONFIGS is a snapshot of ONE branch's WDL signature while GSVTK_BRANCH only picks the
    Dockstore URL, and nothing compared the two offline. `GenotypeBatch.training_vcf` is declared on
    the branch under test and not on main (26 declared inputs there vs main's 21), so a
    main-pointing config was rejected by Rawls as an extra input AT SUBMISSION -- after the config had
    been created and looked fine. `validate` would have said so, but it asks Terra to fetch the
    Dockstore URI, and the unpublished ref answered 404 first. Same map, two symptoms.
    """
    if not (have("firecloud") and have("WDL")):
        raise Skip("firecloud + WDL (miniwdl)")
    fresh({"GSVTK_PROJECT": "probe", "GSVTK_TERRA_NAMESPACE": "probe-sandbox",
           "GSVTK_TERRA_WORKSPACE": "probe-ws", "GSVTK_BRANCH": "probe-branch"},
          "terra", "batch_configs")
    install_recorder(sys.modules["terra"])
    out = io.StringIO()

    # CONTROL FIRST: a tree declaring everything the config binds must read CLEAN. Without this phase
    # "1 problem" and "the comparison never runs" print the same thing.
    ok = TMP / "wdl-ok"
    _fixture_wdl(ok, None, False)
    with contextlib.redirect_stdout(out):
        rc_ok = sys.modules["batch_configs"].check_maps(str(ok), only="10-GenotypeBatch")
    if rc_ok != 0:
        raise AssertionError(f"the check rejected a WDL declaring every bound key: "
                             f"{out.getvalue()[-400:]}")

    # Now become main: drop training_vcf from the declarations, add a required input nobody binds.
    bad = TMP / "wdl-bad"
    _fixture_wdl(bad, "training_vcf", True)
    out.truncate(0)
    out.seek(0)
    with contextlib.redirect_stdout(out):
        rc_bad = sys.modules["batch_configs"].check_maps(str(bad), only="10-GenotypeBatch")
    txt = out.getvalue()
    if rc_bad == 0:
        raise AssertionError("a config key the WDL does not declare was accepted")
    if "EXTRA  GenotypeBatch.training_vcf" not in txt:
        raise AssertionError(f"the extra input was not named: {txt[-300:]}")
    if "KNOWN branch-only" not in txt:
        raise AssertionError("a known branch-only input was reported as unknown -- the table beside "
                             "CONFIGS is what tells the two apart")
    if "MISSING GenotypeBatch.probe_required_unbound" not in txt:
        raise AssertionError("the required-but-unbound half of the check did not fire")

    # The gate itself: create() must refuse BEFORE any request leaves the machine. The override phase
    # proves the recorder records and that create() would otherwise have POSTed.
    repo = TMP / "fakerepo"
    _fixture_wdl(repo, "training_vcf", False)
    env = dict(os.environ, GIT_AUTHOR_NAME="p", GIT_AUTHOR_EMAIL="p@p", GIT_COMMITTER_NAME="p",
               GIT_COMMITTER_EMAIL="p@p")
    for cmd in (["git", "init", "-q"], ["git", "add", "-A"], ["git", "commit", "-qm", "fixture"]):
        subprocess.run(cmd, cwd=str(repo), env=env, check=True, capture_output=True)
    # Not "master": the default branch name is a git config on the machine, and a probe that
    # hardcodes it fails on boxes configured differently from the author's.
    ref = subprocess.run(["git", "symbolic-ref", "--short", "HEAD"], cwd=str(repo),
                         capture_output=True, text=True, check=True).stdout.strip()
    argv_save, env_save = sys.argv, os.environ.get("GSVTK_GATK_SV_CHECKOUT")
    os.environ["GSVTK_GATK_SV_CHECKOUT"] = str(repo)
    bc = sys.modules["batch_configs"]
    try:
        REQUESTS.clear()
        sys.argv = ["batch_configs.py", "create", "--confirm", "--against", ref]
        refused = None
        with contextlib.redirect_stdout(out):
            try:
                bc.create()
            except SystemExit as e:
                refused = str(e)
        if refused is None or "do not fit the WDL" not in refused:
            raise AssertionError(f"create() posted anyway: {refused!r} / {out.getvalue()[-300:]}")
        if REQUESTS:
            raise AssertionError(f"create() reached Terra before refusing: {REQUESTS}")
        sys.argv = ["batch_configs.py", "create", "--confirm", "--against", ref,
                    "--allow-unknown-inputs"]
        REQUESTS.clear()
        with contextlib.redirect_stdout(out):
            try:
                bc.create()
            except Exception:                     # the recorder answers 201; other noise is beside it
                pass
        sent = len(REQUESTS)
        if not sent:
            raise AssertionError("control: with the override taken create() still sent nothing, so "
                                 "the refusal above proves nothing about the guard")
        said = "override" in out.getvalue().lower()
    finally:
        sys.argv = argv_save
        if env_save is None:
            os.environ.pop("GSVTK_GATK_SV_CHECKOUT", None)
        else:
            os.environ["GSVTK_GATK_SV_CHECKOUT"] = env_save
    return (f"declared-everything -> clean; main-shaped tree -> extra named + labelled branch-only "
            f"+ unbound required caught; create refused with 0 requests (override sent {sent}, "
            f"said so: {said})")


PROBES = [
    ("rerun_guards", probe_rerun_guards),
    ("stage_batch_row", probe_stage_batch_row),
    ("adopt_fallback", probe_adopt_fallback),
    ("wdl_flat_dup", probe_wdl_flat_dup),
    ("publish_guard", probe_publish_guard),
    ("miniwdl_resolver", probe_miniwdl_resolver),
    ("help_writes_nothing", probe_help_writes_nothing),
    ("dstore_drift", probe_dstore_drift),
    ("map_vs_wdl", probe_map_vs_wdl),
]


def main() -> int:
    global VERBOSE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--keep", action="store_true", help="keep the temp tree and print its path")
    a = ap.parse_args()
    VERBOSE = a.verbose
    ok = skip = fail = 0
    print(f"probes: {len(PROBES)} defects, offline, temp tree {TMP}")
    try:
        for name, fn in PROBES:
            REQUESTS.clear()
            try:
                detail = fn()
                ok += 1
                print(f"  ok    {name:20s} {detail}")
            except Skip as e:
                skip += 1
                print(f"  SKIP  {name:20s} needs {e} (make setup / pip install -r requirements.txt)")
            except SystemExit as e:                       # the kit's own sys.exit on a missing dep,
                m = str(e).strip()                        # from an import find_spec() swore was there
                if "missing dependency" in m:
                    skip += 1
                    print(f"  SKIP  {name:20s} {m.splitlines()[0]}")
                else:                                     # ... but a guarded exit is a finding
                    fail += 1
                    print(f"  FAIL  {name:20s} SystemExit: {m}")
            except Exception as e:                        # a probe blowing up is a finding
                fail += 1
                print(f"  FAIL  {name:20s} {type(e).__name__}: {e}")
    finally:
        if a.keep:
            print(f"  (temp tree kept: {TMP})")
        else:
            shutil.rmtree(TMP, ignore_errors=True)
    print(f"probes: {ok} ok, {skip} skipped, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
