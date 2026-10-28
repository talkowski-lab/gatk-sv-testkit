#!/usr/bin/env bash
# scripts/selftest.d/abort.sh — assertions for `gsvtk terra abort` and terra/abort_submission.py.
#
# STANDALONE:  bash scripts/selftest.d/abort.sh .venv/bin/python
# prints its own tally and exits nonzero on any failure. bash 3.2 compatible.
#
# Covers four things:
#   1. the CLI gate: `gsvtk terra abort` without --confirm, or under GSVTK_READ_ONLY=1, exits 2 and
#      the child tool never runs (checked with a dispatch-logging interpreter, plus a positive control);
#   2. the tool's own refusals: no --confirm, an empty or missing --submission, and --polls 0;
#   3. with --confirm, abort_submission gets the resolved namespace/workspace and the typed id, and
#      status reads use the same pair;
#   4. the report: the aborted id, the observed terminal state, and an exit code that matches it
#      (--no-wait observes nothing; an exhausted poll is NOT OBSERVED; an empty workflow list is UNKNOWN).
#
# Nothing here touches the network or a credential. Refusals happen before any request, the in-process
# legs replace fiss calls with recorders and raisers, and GSVTK_TERRA_API_ROOT is a closed local port.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../.." && pwd -P)"
cd "$ROOT" || exit 2

PY="${1:-${PYTHON:-python3}}"
if [ ! -x "$PY" ] && [ -x .venv/bin/python ]; then PY=.venv/bin/python; fi
# Prefer ./.venv/bin/python when the given interpreter lacks firecloud (same rule as `make helpsweep`).
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1 && [ -x .venv/bin/python ] \
   && .venv/bin/python -c 'import firecloud' >/dev/null 2>&1; then PY=.venv/bin/python; fi
SELF=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")
. "$(dirname "$SELF")/declared.sh"
# The recorder functions, pinned so drift is reported. Re-derive with:
#   gsvtk_declared_funset scripts/selftest.d/abort.sh | awk '{print $1}'
GSVTK_DECL_FUNCS="run run_absent inproc"
# The bail below skips everything after this line, so it declares its own extent.
gsvtk_guard_to_end "abort-no-firecloud"
if ! "$PY" -c 'import firecloud' >/dev/null 2>&1; then
    printf 'abort: SKIP — %s cannot import firecloud (python -m pip install -r requirements.txt)\n' "$PY"
    printf 'abort: 0 ok, 0 failed, 1 skipped (a SKIP here is not a pass in CI)\n'
    gsvtk_declared_init
    gsvtk_note_skip "abort-no-firecloud"
    gsvtk_declared_report "abort" "$SELF" 0 1 || exit 1
    exit 0
fi

TMP="$(mktemp -d "${TMPDIR:-/tmp}/gsvtk-abort.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
gsvtk_declared_init "$TMP"

CLOSED="http://127.0.0.1:9/api/"
: > "$TMP/empty.env"
# Placeholder sandbox coordinates, and the shipped public baseline pair for the shared-target legs.
cat > "$TMP/sandbox.env" <<'ENVT'
GSVTK_TERRA_NAMESPACE=your-namespace
GSVTK_TERRA_WORKSPACE=your-sandbox-workspace
ENVT
BASE_NS="$("$PY" -c 'import sys; sys.path.insert(0, "kit"); import config; print(config.get("BASELINE_NAMESPACE"))')"
BASE_WS="$("$PY" -c 'import sys; sys.path.insert(0, "kit"); import config; print(config.get("BASELINE_WORKSPACE"))')"
if [ -z "$BASE_NS" ] || [ -z "$BASE_WS" ]; then
    printf 'abort: the shipped baseline defaults are unset, so the shared-target legs have no target to name\n'
    exit 1
fi
cat > "$TMP/baseline.env" <<ENVT
GSVTK_TERRA_NAMESPACE=$BASE_NS
GSVTK_TERRA_WORKSPACE=$BASE_WS
ENVT

# The full submission id every leg uses.
SUB="11111111-2222-3333-4444-555555555555"

# ------------------------------------------------------------------ recorders
ok=0; fail=0; QUIET=

# run DESC WANT_RC NEEDLE -- CMD... — the command exits WANT_RC and its output contains NEEDLE.
run() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # the calling line: one entry per call site
    local desc="$1" want="$2" needle="$3"; shift 3
    [ "${1:-}" = "--" ] && shift
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    if [ "$rc" != "$want" ]; then
        fail=$((fail + 1))
        [ -n "$QUIET" ] || { printf '  FAIL  %s (exit %s, want %s)\n' "$desc" "$rc" "$want"
                             printf '%s\n' "$out" | head -8 | sed 's/^/          /'; }
    elif ! grep -qF -- "$needle" <<< "$out"; then
        fail=$((fail + 1))
        [ -n "$QUIET" ] || { printf '  FAIL  %s (exit %s is right, but the output never says %s)\n' \
                                 "$desc" "$rc" "$needle"
                             printf '%s\n' "$out" | head -8 | sed 's/^/          /'; }
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# run_absent DESC WANT_RC ABSENT -- CMD... — the command exits WANT_RC and its output does not contain ABSENT.
run_absent() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see run() above
    local desc="$1" want="$2" absent="$3"; shift 3
    [ "${1:-}" = "--" ] && shift
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    if [ "$rc" != "$want" ]; then
        fail=$((fail + 1))
        [ -n "$QUIET" ] || { printf '  FAIL  %s (exit %s, want %s)\n' "$desc" "$rc" "$want"
                             printf '%s\n' "$out" | head -8 | sed 's/^/          /'; }
    elif grep -qF -- "$absent" <<< "$out"; then
        fail=$((fail + 1))
        [ -n "$QUIET" ] || { printf '  FAIL  %s (the output says %s, which it must not)\n' "$desc" "$absent"
                             printf '%s\n' "$out" | head -8 | sed 's/^/          /'; }
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# inproc DESC WANT_RC -- PYFILE [ARG...] — an in-process case, which must print PASS itself.
inproc() {
    gsvtk_note_site "${BASH_LINENO[0]}"   # see run() above
    local desc="$1" want="$2"; shift 2
    [ "${1:-}" = "--" ] && shift
    local out rc
    out="$("$@" 2>&1)"; rc=$?
    if [ "$rc" != "$want" ]; then
        fail=$((fail + 1))
        [ -n "$QUIET" ] || { printf '  FAIL  %s (exit %s, want %s)\n' "$desc" "$rc" "$want"
                             printf '%s\n' "$out" | head -12 | sed 's/^/          /'; }
    elif ! grep -qF -- 'PASS' <<< "$out"; then
        fail=$((fail + 1))
        [ -n "$QUIET" ] || { printf '  FAIL  %s (exit %s but it never printed PASS)\n' "$desc" "$rc"
                             printf '%s\n' "$out" | head -12 | sed 's/^/          /'; }
    else
        ok=$((ok + 1)); printf '  ok    %s\n' "$desc"
    fi
}

# ------------------------------------------------------------------ the harness's own harness
# Canaries: prove each recorder reports a wrong exit code and a wrong needle before anything relies on it.
QUIET=1
before=$fail
run "canary: a wrong exit code is noticed" 1 no-such-file -- /bin/cat "$TMP/not-here-at-all"
if [ "$fail" -eq $((before + 1)) ]; then
    fail=$before; ok=$((ok + 1)); printf '  ok    canary: a wrong exit code IS reported (harness works)\n'
else
    fail=$((fail + 1)); printf '  FAIL  canary: a failing command did not increment fail\n'
fi
before=$fail
run "canary: a missing needle is noticed" 0 "not-in-this-output" -- /bin/echo "hello world"
if [ "$fail" -eq $((before + 1)) ]; then
    fail=$before; ok=$((ok + 1)); printf '  ok    canary: an absent needle IS reported (harness works)\n'
else
    fail=$((fail + 1)); printf '  FAIL  canary: a missing needle did not increment fail\n'
fi
before=$fail
run_absent "canary: a forbidden needle that DOES appear is noticed" 0 'hello' -- /bin/echo "hello world"
if [ "$fail" -eq $((before + 1)) ]; then
    fail=$before; ok=$((ok + 1)); printf '  ok    canary: a forbidden needle that appears IS reported (harness works)\n'
else
    fail=$((fail + 1)); printf '  FAIL  canary: a forbidden needle did not increment fail\n'
fi
before=$fail
inproc "canary: an in-process case that never prints PASS is noticed" 0 -- /bin/echo "no verdict here"
if [ "$fail" -eq $((before + 1)) ]; then
    fail=$before; ok=$((ok + 1)); printf '  ok    canary: an in-process case with no PASS IS reported (harness works)\n'
else
    fail=$((fail + 1)); printf '  FAIL  canary: a PASS-less in-process case did not increment fail\n'
fi
before=$fail
inproc "canary: an in-process case with the wrong exit code is noticed" 0 -- /bin/false
if [ "$fail" -eq $((before + 1)) ]; then
    fail=$before; ok=$((ok + 1)); printf '  ok    canary: a wrong in-process exit code IS reported (harness works)\n'
else
    fail=$((fail + 1)); printf '  FAIL  canary: a failing in-process command did not increment fail\n'
fi
QUIET=""

# ---------------------------------------------------------------- the dispatch-logged interpreter
# GSVTK_TERRA_PY points ./gsvtk at a wrapper that logs every dispatch to $DISPATCH_LOG. The dependency
# probe (`-c 'import firecloud'`) is not logged.
PYABS="$PY"
case "$PY" in
  /*)  PYABS="$PY" ;;
  */*) PYABS="$(cd "$(dirname "$PY")" && pwd -P)/$(basename "$PY")" ;;
  *)   PYABS="$(command -v "$PY" 2>/dev/null || printf '%s' "$PY")" ;;
esac
[ -x "$PYABS" ] || { printf 'abort: the interpreter handed to ./gsvtk is not executable: %s\n' "$PYABS"; exit 1; }
cat > "$TMP/pylog" <<PYW
#!/bin/sh
# gsvtk's dependency probe is "-c ..."; that is not a dispatch. Everything else is: log it, then exec.
if [ "\${1:-}" != "-c" ]; then
    printf 'DISPATCH %s\n' "\$*" >> "\${DISPATCH_LOG:-/dev/null}"
fi
exec "$PYABS" "\$@"
PYW
chmod +x "$TMP/pylog"

# cli CFG LOG ARGS... — the real ./gsvtk, dispatch-logged, against the closed port. Each leg creates its
# log file first, so a missing log fails loudly.
cli() {
    local cfg="$1" log="$2"; shift 2
    env GSVTK_CONFIG="$cfg" GSVTK_WORK="$TMP/work" GSVTK_REPO_ROOT="$ROOT" \
        GSVTK_TERRA_API_ROOT="$CLOSED" GSVTK_TERRA_PY="$TMP/pylog" DISPATCH_LOG="$log" \
        bash ./gsvtk "$@"
}
# cli_ro — the same, with GSVTK_READ_ONLY=1 (what the agent skill runs under).
cli_ro() {
    local cfg="$1" log="$2"; shift 2
    env GSVTK_READ_ONLY=1 GSVTK_CONFIG="$cfg" GSVTK_WORK="$TMP/work" GSVTK_REPO_ROOT="$ROOT" \
        GSVTK_TERRA_API_ROOT="$CLOSED" GSVTK_TERRA_PY="$TMP/pylog" DISPATCH_LOG="$log" \
        bash ./gsvtk "$@"
}
# tool CFG ARGS... — terra/abort_submission.py directly, at the closed port.
tool() {
    local cfg="$1"; shift
    env GSVTK_CONFIG="$cfg" GSVTK_WORK="$TMP/work" GSVTK_REPO_ROOT="$ROOT" \
        GSVTK_TERRA_API_ROOT="$CLOSED" "$PY" terra/abort_submission.py "$@"
}

printf 'abort selftest (interpreter: %s; Terra root: %s = closed port, offline; submission id %s)\n' \
    "$PY" "$CLOSED" "$SUB"

echo
echo "selftest: abort: the CLI's outer gate — refused before the child tool ever runs"
: > "$TMP/log-noconfirm"
run "terra abort without --confirm exits 2 and names --confirm" 2 'need --confirm' -- \
    cli "$TMP/sandbox.env" "$TMP/log-noconfirm" terra abort --submission "$SUB"
run_absent "and NOTHING was dispatched: the child tool never ran, so nothing was requested" 0 \
    'DISPATCH' -- cat "$TMP/log-noconfirm"
: > "$TMP/log-readonly"
run "GSVTK_READ_ONLY=1 refuses terra abort even with --confirm typed" 2 'REFUSED' -- \
    cli_ro "$TMP/sandbox.env" "$TMP/log-readonly" terra abort --submission "$SUB" --confirm
run_absent "and the read-only refusal dispatches nothing either (policy, not a broken environment)" 0 \
    'DISPATCH' -- cat "$TMP/log-readonly"
run "the read-only refusal prints the exact command to run by hand in the checkout" 2 \
    'terra/abort_submission.py --submission' -- \
    cli_ro "$TMP/sandbox.env" "$TMP/log-readonly2" terra abort --submission "$SUB" --confirm

# Positive control for the empty logs above: an unset target still dispatches the child (exit 4),
# so the log is shown to work.
: > "$TMP/log-dispatched"
run "CONTROL: an unset target still dispatches the child (exit 4), so the log has something to record" 4 \
    'GSVTK_TERRA_NAMESPACE is not set' -- \
    cli "$TMP/empty.env" "$TMP/log-dispatched" terra abort --submission "$SUB" --confirm
run "and the dispatch log DOES record it: an empty log above is not merely a wrapper that never wrote" 0 \
    'DISPATCH terra/abort_submission.py --submission' -- cat "$TMP/log-dispatched"
# The CLI gate is additive: the typed --confirm must be forwarded to the tool, which checks it too.
run "and the forwarded argv still carries the typed --confirm, because the tool gates on it too" 0 \
    ' --confirm' -- cat "$TMP/log-dispatched"
run_absent "and that dispatch requested NOTHING: exit 4 named the key before the closed port answered" 4 \
    'cannot reach Terra' -- cli "$TMP/empty.env" "$TMP/log-dispatched2" terra abort \
    --submission "$SUB" --confirm
run "the unset target is exit 4 naming GSVTK_TERRA_NAMESPACE and the file to edit" 4 \
    'GSVTK_TERRA_NAMESPACE is not set' -- cli "$TMP/empty.env" "$TMP/log-dispatched3" terra abort \
    --submission "$SUB" --confirm
# The pre-flight block prints before dispatch, so these legs use the unset profile and never build a request.
run "the pre-flight states the undo BEFORE the child runs, and the undo is the new command" 4 \
    'undo   an aborted submission does not restart' -- \
    cli "$TMP/empty.env" "$TMP/log-preflight" terra abort --submission "$SUB" --confirm
run "the pre-flight says this is a mutating mode before it dispatches" 4 'MUTATING mode' -- \
    cli "$TMP/empty.env" "$TMP/log-preflight2" terra abort --submission "$SUB" --confirm
run "rerun-submit's undo line names the abort command instead of the Terra UI" 1 \
    'gsvtk terra abort --submission <full submission id> --confirm' -- \
    cli "$TMP/sandbox.env" "$TMP/log-undo" terra rerun-submit --confirm
run_absent "and that undo line no longer sends the operator to a browser" 1 'stop the submission in the Terra UI' -- \
    cli "$TMP/sandbox.env" "$TMP/log-undo2" terra rerun-submit --confirm
run "the printed mode lists put abort on the MUTATING side, with the rest of the POSTers" 2 \
    'rerun-submit | abort | fetch' -- cli "$TMP/sandbox.env" "$TMP/log-unknown" terra frobnicate

echo
echo "selftest: abort: the tool's own refusals, at process level (the exit codes a shell sees)"
run "abort_submission without --confirm refuses and names the missing confirmation" 1 \
    're-run with --confirm' -- tool "$TMP/sandbox.env" --submission "$SUB"
run_absent "and it refused before reading anything (no request, no credential read)" 1 \
    'cannot reach Terra' -- tool "$TMP/sandbox.env" --submission "$SUB"
run "an EMPTY --submission is refused rather than read as a wild abort" 1 \
    'empty id aborts nothing' -- tool "$TMP/sandbox.env" --submission "" --confirm
run_absent "and the empty value asked for nothing at the API either" 1 \
    'cannot reach Terra' -- tool "$TMP/sandbox.env" --submission "" --confirm
run "a missing --submission is argparse's usage error (exit 2), not a traceback" 2 \
    'required: --submission' -- tool "$TMP/sandbox.env" --confirm
run "a --polls budget of 0 refuses: such a budget would claim a state it never read" 1 \
    'budget of 0' -- tool "$TMP/sandbox.env" --submission "$SUB" --confirm --polls 0
run "a negative --interval is refused by name, not slept on" 1 \
    'non-negative' -- tool "$TMP/sandbox.env" --submission "$SUB" --confirm --interval -1
run "--help works with an EMPTY profile (helpsweep's claim, graded from this phase too)" 0 \
    'Abort one running Terra submission' -- tool "$TMP/empty.env" --help

echo
echo "selftest: abort: the resolved triple, the observed state and the bounded poll (in process)"
cat > "$TMP/cases.py" <<'PYCASES'
"""Drive terra/abort_submission.py IN PROCESS: every fiss call is a recorder, so "zero requests" and
"the RESOLVED triple reached abort_submission" are counted numbers rather than expectations.

    python cases.py SCENARIO        # GSVTK_CONFIG picks the profile the leg named

WHY THE SEAM IS `fapi.abort_submission` + `fapi.get_submission`, and not the tool's own functions
----------------------------------------------------------------------------------------------
The claim under test is what reaches Terra's API layer and what the tool prints about it. Patching
anything higher up would grade a retelling of the code, so the two calls the tool makes are the only
things replaced, EVERY other `fapi` verb is a raiser, and `terra.session()` — the raw-REST bearer
session — is a raiser too, so this process can neither build a request nor read a credential. The file
is run with runpy under `__main__`, i.e. the real argparse, the real `sys.exit`, the real order of
gates. A fixture that answered nothing would make every scenario look alike, so each one also asserts
the CONTENT it printed, not just the exit code.
"""
import contextlib
import io
import os
import runpy
import sys

repo = os.environ["GSVTK_REPO_ROOT"]
sys.path.insert(0, os.path.join(repo, "kit"))
sys.path.insert(0, os.path.join(repo, "terra"))

import terra                                       # noqa: E402  (the friendly excepthook, TerraError)
from terra import fapi                             # noqa: E402
import batch_configs as tc                         # noqa: E402  (the resolved target globals)

scenario = sys.argv[1]
SUB = "11111111-2222-3333-4444-555555555555"        # what a leg asks for
REC_ID = SUB                                        # what the fixture's record names (a leg can move it)
CALLS = []            # ("verb", args...) in call order — the thing every assertion below counts
STATES = []           # scripted submission statuses, consumed one READ at a time (empty = "Running")
WF_ROWS = []          # scripted workflow rows: [(workflowId, status)]
ABORT_HTTP = 204      # the measured answer for a submission abort (2026-10-20)
READ_HTTP = 200


class Resp:
    """Stands where `requests.Response` stands: a code, and a json() that only answers with a body.

    A fake that returns a bare dict where fiss returns a Response is the mistake CONTRIBUTING names —
    the client leg then reports itself "unavailable" and the probe passes without testing the client it
    exists to watch. So `status_code` is real, and `json()` raises on an empty body the way a 204 does.
    """

    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("204 No Content carries no body")
        return self._body


def _next_state():
    if not STATES:
        return "Running"
    s = STATES.pop(0)
    if not STATES:
        STATES.append(s)                 # the tail repeats: a read past the script never says ""
    return s


def fake_abort(ns, ws, submission_id):
    CALLS.append(("abort_submission", ns, ws, submission_id))
    return Resp(ABORT_HTTP, None)


def fake_read(ns, ws, submission_id):
    CALLS.append(("get_submission", ns, ws, submission_id))
    return Resp(READ_HTTP, {
        "submissionId": REC_ID,
        "status": _next_state(),
        "methodConfigurationName": "10-GenotypeBatch",
        "entityName": "your_batch_row",
        "submissionDate": "2026-10-20T00:00:00.000Z",
        "workflows": [{"workflowId": w, "status": s, "cost": None} for w, s in WF_ROWS],
    })


def raiser(name):
    def f(*a, **k):
        raise AssertionError("%s: abort_submission.py reached for %s — this phase has no fixture for "
                             "it, so that request was never authorised: %r %r" % (scenario, name, a, k))
    return f


for _verb in ("create_submission", "create_workspace_config", "overwrite_workspace_config",
              "validate_config", "list_submissions", "list_workspace_configs", "get_workspace",
              "get_entity", "get_entities_query", "get_workflow_metadata", "upload_entities_tsv",
              "get_workspace_config"):
    setattr(fapi, _verb, raiser(_verb))
terra.session = raiser("terra.session (the raw-REST bearer session)")
fapi.abort_submission = fake_abort
fapi.get_submission = fake_read


def run_tool(argv):
    """Run the REAL tool with these argv; return (exit code, everything it printed)."""
    global CALLS
    out = io.StringIO()
    saved, sys.argv = sys.argv, ["abort_submission.py"] + argv
    CALLS = []
    code = 0
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            runpy.run_path(os.path.join(repo, "terra", "abort_submission.py"), run_name="__main__")
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        if not isinstance(exc.code, int):
            # The refusal TEXT is the SystemExit's OWN argument: the interpreter prints it when the
            # exception escapes the process, which a caught SystemExit never reaches. A case that
            # discarded it graded request counts alone and could not tell WHICH guard fired — the
            # lesson scripts/probe_fixes.py records for the same trap.
            out.write("\n" + str(exc) + "\n")
    except terra.TerraError as exc:
        # What the top-level excepthook would print and exit 1 for, at process level. Named, not eaten.
        out.write("\n" + str(exc) + "\n")
        code = 1
    finally:
        sys.argv = saved
    return code, out.getvalue()


def want(cond, msg):
    if not cond:
        raise AssertionError("%s: %s | calls=%r" % (scenario, msg, CALLS))


def verbs():
    return [c[0] for c in CALLS]


def reads_after_abort():
    v = verbs()
    if "abort_submission" not in v:
        return v.count("get_submission")
    return v[v.index("abort_submission"):].count("get_submission")


def abort_triple():
    got = [c for c in CALLS if c[0] == "abort_submission"]
    want(len(got) == 1, "abort_submission was reached %d time(s); the tool calls it exactly once"
         % len(got))
    return got[0]


# ------------------------------------------------------------------------ the scenarios
if scenario == "triple":
    # The leg A25 exists for. With --confirm, the RESOLVED triple reaches abort_submission: namespace
    # and workspace as the profile resolved them, the submission id as the operator typed it. Every
    # read uses that SAME pair — a status reader that resolves the target again is how one run prints
    # two workspaces.
    want(bool(tc.NS) and bool(tc.WS), "the shared target globals did not resolve: (%r, %r)"
         % (tc.NS, tc.WS))
    STATES[:] = ["Running", "Aborted"]
    WF_ROWS[:] = [("wf-aaa", "Aborted")]
    rc, said = run_tool(["--submission", SUB, "--confirm", "--polls", "2", "--interval", "0"])
    want(rc == 0, "exit %s, want 0 (terminal Aborted for both halves). Output: %s" % (rc, said[-400:]))
    t = abort_triple()
    want(t[1] == tc.NS and t[2] == tc.WS and t[3] == SUB,
         "the triple that reached abort_submission is not (resolved NS, resolved WS, typed id): %r"
         % (t,))
    reads = [c for c in CALLS if c[0] == "get_submission"]
    want(reads and all(c[1] == tc.NS and c[2] == tc.WS and c[3] == SUB for c in reads),
         "a status read used a different target than the abort: %r" % (reads,))
    want(verbs()[0] == "get_submission", "the pre-abort read did not come first: %r" % (verbs(),))
    want("about to ABORT submission %s" % SUB in said, "it did not print what it was about to abort")
    want("target    %s/%s" % (tc.NS, tc.WS) in said, "it did not print the coordinates it aborts in")
    want("DELETE accepted (HTTP 204)" in said, "the accepted DELETE is not in the report")
    want("submission %s status=Aborted terminal=yes" % SUB in said,
         "the observed-state report did not print the id it aborted with its terminal status")
    want("terminal ABORTED for the submission and 1/1 workflow(s)" in said,
         "the terminal claim is missing")
    print("PASS %s: abort_submission reached once with (%s, %s, %s); %d read(s), all on that pair"
          % (scenario, tc.NS, tc.WS, SUB[:8], len(reads)))

elif scenario == "noconfirm":
    # The control for the scenario above: the SAME argv minus --confirm reaches NOTHING. Without this
    # arm, "the triple reached" and "nothing was ever reachable" are one and the same observation.
    STATES[:] = ["Running", "Aborted"]
    WF_ROWS[:] = [("wf-aaa", "Aborted")]
    rc, said = run_tool(["--submission", SUB, "--polls", "2", "--interval", "0"])
    want(rc == 1, "exit %s, want 1 (an ordinary refusal)" % rc)
    want(not CALLS, "the no-confirm arm made %d request(s): %r" % (len(CALLS), CALLS))
    want("re-run with --confirm" in said, "the refusal did not name --confirm")
    print("PASS %s: same argv without --confirm, 0 requests, exit 1 naming --confirm" % scenario)

elif scenario == "bounded":
    # Terra never says terminal. The poll must stop at its budget, exit nonzero, and say NOT OBSERVED
    # rather than invent a state or spin forever. Reads: 1 pre-read + 3 polls + 1 row read = 5.
    STATES[:] = ["Aborting"]
    WF_ROWS[:] = [("wf-aaa", "Aborting")]
    rc, said = run_tool(["--submission", SUB, "--confirm", "--polls", "3", "--interval", "0"])
    want(rc == 1, "exit %s, want 1 (never observed terminal is not a clean answer)" % rc)
    want(verbs().count("get_submission") == 5,
         "%d reads, want 5 (1 pre-read + 3 polls + 1 row read)" % verbs().count("get_submission"))
    want("NOT observed terminal after 3 poll(s)" in said, "the budget-exhausted report is missing")
    want("abort: poll 3/3" in said, "the poll never reached its last allowed read")
    want("abort: poll 4/3" not in said, "the poll ran past its budget")
    print("PASS %s: 3 polls, 5 reads total, exit 1 saying NOT OBSERVED" % scenario)

elif scenario == "nowait":
    # --no-wait: the DELETE happened, and the tool must say it observed NOTHING instead of implying it
    # watched the run stop. Zero reads after the abort is the claim, counted.
    STATES[:] = ["Running"]
    WF_ROWS[:] = [("wf-aaa", "Aborted")]
    rc, said = run_tool(["--submission", SUB, "--confirm", "--no-wait"])
    want(rc == 0, "exit %s, want 0 (accepted abort, observation declined by the operator)" % rc)
    abort_triple()
    want(reads_after_abort() == 0, "%d read(s) after the DELETE, want 0 (--no-wait observes nothing)"
         % reads_after_abort())
    want("NO state was observed" in said, "--no-wait did not say out loud that it observed nothing")
    print("PASS %s: 1 pre-read, 1 DELETE, 0 reads after it, exit 0 saying NO state was observed"
          % scenario)

elif scenario == "delete_refused":
    # A refused DELETE is not a submission being stopped. No poll after it: watching a run Terra never
    # agreed to stop answers a question nobody asked.
    STATES[:] = ["Running"]
    WF_ROWS[:] = [("wf-aaa", "Running")]
    ABORT_HTTP = 403
    rc, said = run_tool(["--submission", SUB, "--confirm", "--polls", "3", "--interval", "0"])
    want(rc == 1, "exit %s, want 1" % rc)
    want(verbs().count("get_submission") == 1,
         "%d reads, want the 1 pre-read only: it polled after a refused abort"
         % verbs().count("get_submission"))
    want("Terra refused the abort" in said and "HTTP 403" in said, "the refusal is not in the output")
    print("PASS %s: HTTP 403 named, 0 polls after the refused DELETE, exit 1" % scenario)

elif scenario == "read_404":
    # A submission id is not derivable from a step number or a config name, so a workspace that does
    # not know the id gets a refusal that aborts nothing — and the sentence names where the full id is
    # read, which is the whole reason the old undo line sent people to a browser.
    STATES[:] = ["Running"]
    READ_HTTP = 404
    rc, said = run_tool(["--submission", SUB, "--confirm"])
    want(rc == 1, "exit %s, want 1" % rc)
    want(not any(v == "abort_submission" for v in verbs()), "it aborted a submission it could not read")
    want("Nothing was aborted" in said, "the 404 arm did not say nothing was aborted")
    want("gsvtk terra recon" in said, "it did not name where the full submission id is printed")
    print("PASS %s: 404 refused with 0 DELETEs and the remedy named" % scenario)

elif scenario == "id_mismatch":
    # A read that came back and confirmed NOTHING cannot authorise the DELETE. This is the
    # empty-that-reads-clean class: the workspace answered 200 with a record about another submission.
    STATES[:] = ["Running"]
    REC_ID = "99999999-8888-7777-6666-555555555555"
    rc, said = run_tool(["--submission", SUB, "--confirm"])
    want(rc == 1, "exit %s, want 1" % rc)
    want(not any(v == "abort_submission" for v in verbs()), "it aborted on an unconfirmed read")
    want("Two different submissions" in said, "the mismatch is not named in the refusal")
    print("PASS %s: a record naming a different id aborts nothing" % scenario)

elif scenario == "empty_workflows":
    # Terminal submission, EMPTY workflow list. An empty read is not evidence that everything stopped,
    # so this is UNKNOWN and exit 1 — not the 0 an empty table would have produced.
    STATES[:] = ["Aborted"]
    WF_ROWS[:] = []
    rc, said = run_tool(["--submission", SUB, "--confirm", "--polls", "2", "--interval", "0"])
    want(rc == 1, "exit %s, want 1 (an empty workflow read is not a clean abort)" % rc)
    want("workflow rows read: 0" in said and "UNKNOWN" in said, "the empty read is not named UNKNOWN")
    want("terminal ABORTED for the submission" not in said, "it claimed a terminal workflow it never read")
    print("PASS %s: 0 workflow rows -> UNKNOWN, exit 1" % scenario)

elif scenario == "done_not_aborted":
    # Terminal, but not Aborted: the run finished on its own, or the abort landed too late. The spend
    # happened, and a 0 here would read as "I stopped it".
    STATES[:] = ["Running", "Done"]
    WF_ROWS[:] = [("wf-aaa", "Done")]
    rc, said = run_tool(["--submission", SUB, "--confirm", "--polls", "3", "--interval", "0"])
    want(rc == 1, "exit %s, want 1" % rc)
    want("terminal, but as Done, not Aborted" in said, "the Done arm did not say what it terminalised as")
    print("PASS %s: terminal Done is reported as not-an-abort, exit 1" % scenario)

elif scenario == "lag":
    # Submission Aborted while a workflow still reads Running (Terra lag, or a workflow that will not
    # stop). The measurement that put this tool on the map was terminal for BOTH halves, so a half
    # answer gets a loud "not confirmed" instead of a 0.
    STATES[:] = ["Running", "Aborted"]
    WF_ROWS[:] = [("wf-aaa", "Aborted"), ("wf-bbb", "Running")]
    rc, said = run_tool(["--submission", SUB, "--confirm", "--polls", "3", "--interval", "0"])
    want(rc == 1, "exit %s, want 1" % rc)
    want("is NOT terminal" in said, "the lag arm did not report the not-terminal workflow")
    print("PASS %s: submission Aborted + 1 workflow Running -> not confirmed, exit 1" % scenario)

elif scenario == "baseline_refused":
    # The shared baseline workspace holds the reference run every comparison in this repo is measured
    # against, so aborting inside it is refused before a single request — the same guard every other
    # writer uses, on the one call that can destroy a run in flight.
    STATES[:] = ["Running", "Aborted"]
    WF_ROWS[:] = [("wf-aaa", "Aborted")]
    rc, said = run_tool(["--submission", SUB, "--confirm"])
    want(rc == 1, "exit %s, want 1" % rc)
    want(not CALLS, "the shared-target guard let %d request(s) through: %r" % (len(CALLS), CALLS))
    want("baseline workspace" in said, "the refusal did not name the baseline workspace")
    print("PASS %s: the baseline pair is refused with 0 requests" % scenario)

elif scenario == "baseline_allowed":
    # POSITIVE CONTROL for the guard above, at the same layer: the documented override clears it, and
    # the very next thing that happens is the pre-read against THAT pair. A guard that could never fire
    # and a guard that fired are otherwise the same output.
    STATES[:] = ["Running", "Aborted"]
    WF_ROWS[:] = [("wf-aaa", "Aborted")]
    rc, said = run_tool(["--submission", SUB, "--confirm", "--allow-shared-target",
                         "--polls", "1", "--interval", "0"])
    want(rc == 0, "exit %s, want 0 (the arm the flag exists to enable, offline). %s" % (rc, said[-400:]))
    t = abort_triple()
    want(t[1] == tc.NS and t[2] == tc.WS and t[3] == SUB,
         "the override reached a different pair than the resolved baseline one: %r" % (t,))
    print("PASS %s: --allow-shared-target cleared the guard and reached (%s, %s, %s)"
          % (scenario, tc.NS, tc.WS, SUB[:8]))

elif scenario == "label_not_expanded":
    # `gsvtk terra status` prints the first 8 characters of an id. That is a label, not an address for a
    # DELETE, and this tool must not expand it into a guess: the value is asked about VERBATIM, the
    # submission LISTING is never consulted to resolve it (list_submissions is a raiser here, so
    # reaching it fails this leg outright), and an unknown string aborts nothing.
    STATES[:] = ["Running"]
    READ_HTTP = 404
    rc, said = run_tool(["--submission", SUB[:8], "--confirm"])
    want(rc == 1, "exit %s, want 1" % rc)
    reads = [c for c in CALLS if c[0] == "get_submission"]
    want(reads and all(c[3] == SUB[:8] for c in reads),
         "the 8-character label was expanded before it was asked about: %r" % (reads,))
    want(not any(v == "abort_submission" for v in verbs()), "it expanded a label into a DELETE")
    want("Nothing was aborted" in said, "the refusal did not say nothing was aborted")
    print("PASS %s: the label is asked about verbatim, the listing is never read, nothing is aborted"
          % scenario)

else:
    raise AssertionError("no such scenario: %s" % scenario)
PYCASES

cases() { env GSVTK_CONFIG="$TMP/sandbox.env" GSVTK_WORK="$TMP/work" GSVTK_REPO_ROOT="$ROOT" \
               GSVTK_TERRA_API_ROOT="$CLOSED" "$PY" "$TMP/cases.py" "$@"; }
cases_baseline() { env GSVTK_CONFIG="$TMP/baseline.env" GSVTK_WORK="$TMP/work" GSVTK_REPO_ROOT="$ROOT" \
                        GSVTK_TERRA_API_ROOT="$CLOSED" "$PY" "$TMP/cases.py" "$@"; }

inproc "with --confirm, abort_submission is reached ONCE with the RESOLVED (ns, ws, typed id) triple" 0 -- \
    cases triple
inproc "CONTROL for it: the same argv WITHOUT --confirm reaches nothing at all (0 requests, exit 1)" 0 -- \
    cases noconfirm
inproc "the poll is BOUNDED: never-terminal stops at 3 polls, 5 reads total, exit 1 NOT OBSERVED" 0 -- \
    cases bounded
inproc "--no-wait aborts and observes NOTHING (0 reads after the DELETE), and says so, exit 0" 0 -- \
    cases nowait
inproc "a refused DELETE (HTTP 403) is named and polls NOTHING afterwards, exit 1" 0 -- \
    cases delete_refused
inproc "a submission the workspace cannot describe (404) is refused with 0 DELETEs, remedy named" 0 -- \
    cases read_404
inproc "a record naming a DIFFERENT submission id authorises nothing: 'Two different submissions'" 0 -- \
    cases id_mismatch
inproc "an EMPTY workflow list is UNKNOWN and exit 1, never the 0 an empty table would have printed" 0 -- \
    cases empty_workflows
inproc "terminal but Done (not Aborted) is reported as not-an-abort, exit 1" 0 -- \
    cases done_not_aborted
inproc "submission Aborted with a workflow still Running is NOT confirmed, exit 1" 0 -- \
    cases lag
inproc "the SHARED baseline workspace is refused before a single request (the guard every writer uses)" 0 -- \
    cases_baseline baseline_refused
inproc "CONTROL for it: --allow-shared-target clears the guard and reaches abort_submission on that pair" 0 -- \
    cases_baseline baseline_allowed
# No backticks in descriptions: the shell would run them inside double quotes.
inproc "the 8-character label 'gsvtk terra status' prints is asked about verbatim, never expanded" 0 -- \
    cases label_not_expanded

printf 'abort selftest: %s ok, %s failed\n' "$ok" "$fail"
# Declared and reached must be equal; a deleted call site lowers both (see declared.sh).
gsvtk_declared_report "abort" "$SELF" "$((ok + fail))" 0 || fail=$((fail + 1))
[ "$fail" -eq 0 ] || exit 1
exit 0
