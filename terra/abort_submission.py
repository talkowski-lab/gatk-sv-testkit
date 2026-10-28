#!/usr/bin/env python3
"""Abort one running Terra submission from the client.

    python terra/abort_submission.py --submission <full-submission-id> --confirm
    python terra/abort_submission.py --submission <id> --confirm --no-wait
    python terra/abort_submission.py --submission <id> --confirm --polls 20 --interval 15

Steps, in order:
  1. Require `--confirm`. `fapi.abort_submission` is a bare DELETE with no confirmation of its own,
     so nothing (not even a read) runs without it.
  2. Resolve namespace + workspace, or exit 4 naming the missing key. `GSVTK_BRANCH` is not needed,
     so this does not use `batch_configs.require_target()`.
  3. Refuse the shared baseline workspace unless `--allow-shared-target`.
  4. Read the submission record and require it to name the requested id; a 404 or a mismatched id
     aborts nothing.
  5. Print the target, then DELETE once.
  6. Poll the submission (bounded) and read the workflow rows with the same readers as
     `gsvtk terra status` (`terra.submission`, `batch_status.workflow_detail`, `batch_status.TERMINAL`).

Exit codes:

  0   the submission and every workflow row read terminal Aborted
  0   `--no-wait`: the DELETE was accepted and no state was observed
  1   the DELETE was refused; the poll budget ran out before terminal; the submission ended as
      something other than Aborted; or no workflow rows were read

Nothing here starts compute, and there is no `make` target for it.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "kit"))
sys.path.insert(0, HERE)
import config  # noqa: E402
import terra  # noqa: E402
from terra import fapi  # noqa: E402  # via terra: one friendly missing-dependency message
import batch_configs as tc  # noqa: E402  # the resolved target every Terra tool here reads
import batch_status as bs  # noqa: E402  # the monitor's OWN readers and its TERMINAL set

# Rawls answers 204 for an abort; 200/202 are also treated as accepted. Anything else stops before polling.
ACCEPTED = (200, 202, 204)


def require_target() -> tuple:
    """Resolve (NS, WS) into the `batch_configs` globals, or exit 4 naming the missing key.

    The globals are set in place because `batch_status`'s readers key on `tc.NS`/`tc.WS`.
    """
    tc.NS = tc.NS or config.require("TERRA_NAMESPACE", "whose workspace holds the submission to abort")
    tc.WS = tc.WS or config.require("TERRA_WORKSPACE", "which workspace holds the submission to abort")
    return tc.NS, tc.WS


def submission_record(ns: str, ws: str, sub_id: str) -> dict:
    """Return the submission record, or exit 1 if it is missing or does not name `sub_id`."""
    try:
        rec = terra.submission(ns, ws, sub_id)
    except terra.TerraError as exc:
        said = str(exc)
        hint = ""
        if "404" in said:
            hint = ("\n  a submission id is not derivable from a config name or a step number. The full id is\n"
                    "  printed by `gsvtk terra recon` (`id=…`) and by\n"
                    "  `python terra/batch_rerun_step.py status`. `gsvtk terra status` prints only the first\n"
                    "  8 characters, which is a label, not an id — this tool will not guess the rest.")
        raise SystemExit(f"abort: the workspace refused to describe submission {sub_id} in {ns}/{ws}:\n"
                         f"  {said[:400]}{hint}\n  Nothing was aborted.")
    if not isinstance(rec, dict):
        raise SystemExit(f"abort: the submission read answered {type(rec).__name__}, not a record; "
                         f"submission {sub_id} is unconfirmed and nothing was aborted.")
    got = str(rec.get("submissionId") or "")
    if not got:
        raise SystemExit("abort: the submission read carried no `submissionId`, so it confirmed nothing "
                         f"about {sub_id} — and an unconfirmed target is not aborted. Nothing was aborted.")
    if got != sub_id:
        raise SystemExit(f"abort: asked for {sub_id}, the workspace answered {got}.\n"
                         "  Two different submissions, so this read is not evidence about the one named on\n"
                         "  the command line. Nothing was aborted.")
    return rec


def request_abort(ns: str, ws: str, sub_id: str, confirm: bool) -> int:
    """Issue the abort DELETE; refuses without `confirm=True`, like the other mutating helpers."""
    if not confirm:
        raise terra.TerraError("refusing to abort a submission without confirm=True")
    resp = fapi.abort_submission(ns, ws, sub_id)
    code = getattr(resp, "status_code", None)
    if code not in ACCEPTED:
        body = str(getattr(resp, "text", ""))[:300]
        raise SystemExit(f"abort: Terra refused the abort of submission {sub_id} in {ns}/{ws}: "
                         f"HTTP {code} {body}\n  Nothing was polled: watching a submission Terra never "
                         "agreed to stop answers a question nobody asked.")
    return int(code)


def read_state(ns: str, ws: str, sub_id: str, polls: int, interval: float) -> tuple:
    """Poll the submission up to `polls` times, then read the workflow rows once.

    Returns (submission status, workflow rows, polls used). `interval` 0 never sleeps (used by tests).
    """
    status = ""
    used = 0
    for i in range(1, polls + 1):
        used = i
        rec = terra.submission(ns, ws, sub_id)
        status = str(rec.get("status") or "") if isinstance(rec, dict) else ""
        print(f"abort: poll {i}/{polls}: submission {sub_id} in {ns}/{ws} status="
              f"{status or 'UNKNOWN(no status on the record)'}", flush=True)
        if status in bs.TERMINAL:
            break
        if i < polls and interval > 0:
            time.sleep(interval)
    return status, bs.workflow_detail(sub_id), used


def report(sub_id: str, status: str, rows: list, polls: int, interval: float) -> int:
    """Print the observed state; exit 0 only if the submission and every workflow are terminal Aborted."""
    term_wf = [r for r in rows if r[1] in bs.TERMINAL]
    print(f"abort: submission {sub_id} status={status or 'UNKNOWN'} "
          f"terminal={'yes' if status in bs.TERMINAL else 'no'}")
    if not rows:
        print("abort: workflow rows read: 0 — UNKNOWN, and an empty read is not evidence that "
              "everything stopped. Watch it with: gsvtk terra status")
        return 1
    for wid, wst, _cost, _msg in rows:
        print(f"abort:   workflow {wid} status={wst or 'UNKNOWN'}")
    if status not in bs.TERMINAL:
        print(f"abort: NOT observed terminal after {polls} poll(s) "
              f"(interval {interval}s) — the abort was accepted, the outcome is unconfirmed.")
        print("       Watch it with: gsvtk terra status  (or re-run this tool's read by handing it the")
        print("       same --submission; nothing here spins unbounded)")
        return 1
    if len(term_wf) != len(rows):
        print(f"abort: submission is {status} but {len(rows) - len(term_wf)} of {len(rows)} workflow(s) "
              "is NOT terminal — not confirmed.")
        return 1
    if status != "Aborted":
        print(f"abort: terminal, but as {status}, not Aborted — the submission finished (or stopped) "
              "without the abort landing on it. The spend happened.")
        return 1
    print(f"abort: terminal ABORTED for the submission and {len(term_wf)}/{len(rows)} workflow(s) — "
          "the abort landed.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Abort one running Terra submission from the client (mutating: needs --confirm).",
        epilog=(
            "exit codes: 0 terminal Aborted for the submission and every workflow read (or --no-wait, "
            "which observes nothing and says so); 1 the abort was refused, the poll budget ran out "
            "before terminal, it terminalised as something other than Aborted, or the workflow half "
            "read empty; 2 usage; 4 GSVTK_TERRA_NAMESPACE / GSVTK_TERRA_WORKSPACE unset (the key is "
            "named, nothing is requested).\n"
            "\n"
            "the full submission id: `gsvtk terra recon` prints `id=<full id>` per submission, and\n"
            "`python terra/batch_rerun_step.py status` prints it too. `gsvtk terra status` prints an\n"
            "8-character label, which is enough to read a run and not enough to address a DELETE.\n"
            "\n"
            "offline: no leg of this tool's tests contacts Terra. The legs replace the fiss calls with\n"
            "recorders in process and point GSVTK_TERRA_API_ROOT at a closed port; scripts/selftest.d/\n"
            "abort.sh is the phase, and the probe in scripts/probe_fixes.py counts the requests."),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--submission", required=True,
                    help="the FULL submission id to abort (see the epilog for where to read it)")
    ap.add_argument("--confirm", action="store_true",
                    help="required: this is the only confirmation the abort has, so it has to be typed")
    ap.add_argument("--no-wait", action="store_true",
                    help="abort and return: do NOT observe any state, and say out loud that nothing was "
                         "observed (the DELETE itself already happened)")
    ap.add_argument("--polls", type=int, default=8,
                    help="how many times to read the submission before giving up (default 8; bounded — "
                         "nothing here spins forever)")
    ap.add_argument("--interval", type=float, default=15.0,
                    help="seconds between polls (default 15); 0 never sleeps")
    ap.add_argument("--allow-shared-target", action="store_true",
                    help="proceed when the resolved target IS the shared baseline workspace, i.e. when "
                         "the submission being stopped is in the reference run. Refused without it.")
    a = ap.parse_args()

    if not a.submission.strip():
        raise SystemExit('--submission: an empty id aborts nothing, and "nothing" is not what --confirm '
                         'authorised.\n'
                         '   `--submission "$ID"` with ID unset is how this gets typed. The full id is\n'
                         '   printed by `gsvtk terra recon` (`id=…`).')
    if not a.confirm:
        raise SystemExit("abort_submission DELETEs a running submission: it stops a run in flight, and "
                         "fiss's abort_submission has no confirmation semantics of its own.\n"
                         "  re-run with --confirm. Nothing was read and nothing was aborted.")
    if a.polls < 1:
        raise SystemExit(f"--polls wants at least 1 read, not {a.polls}: a budget of 0 claims a state it "
                         "did not observe.")
    if a.interval < 0:
        raise SystemExit(f"--interval wants a non-negative number of seconds, not {a.interval}.")

    ns, ws = require_target()
    terra.assert_writable_target(ns, ws, "abort a submission in", allow=a.allow_shared_target)

    sub_id = a.submission.strip()
    rec = submission_record(ns, ws, sub_id)
    print(f"abort: about to ABORT submission {sub_id}")
    print(f"abort:   target    {ns}/{ws}")
    print(f"abort:   config    {rec.get('methodConfigurationName') or '(not on the record)'}")
    print(f"abort:   row       {rec.get('entityName') or '(not on the record)'}")
    print(f"abort:   submitted {rec.get('submissionDate') or '(not on the record)'}")
    print(f"abort:   status now {rec.get('status') or 'UNKNOWN(no status on the record)'}")
    print("abort:   the undo after this is a NEW submission (gsvtk terra rerun-submit --confirm); an "
          "aborted submission does not restart, and objects it already wrote stay in the bucket")

    code = request_abort(ns, ws, sub_id, confirm=True)
    print(f"abort: DELETE accepted (HTTP {code}) for submission {sub_id} in {ns}/{ws}")
    if a.no_wait:
        print("abort: --no-wait — NO state was observed. This tool is not claiming the submission "
              "stopped; it is reporting that the request was accepted. Watch it with: gsvtk terra status")
        return 0
    status, rows, used = read_state(ns, ws, sub_id, a.polls, a.interval)
    return report(sub_id, status, rows, used, a.interval)


if __name__ == "__main__":
    sys.exit(main())
