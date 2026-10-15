#!/usr/bin/env python3
"""Does the workflow version this launcher is about to name actually resolve on Dockstore?

Why this exists, twice in one day, at real cost:

1. The launcher built its method path from the profile's `workflow` field, which is the WDL's DECLARED name. Upstream
   publishes `/wdl/GATKSVPipelineSingleSample.wdl` under the entry `SingleSamplePipeline`
   (`.github/.dockstore.yml:256-257`). Terra creates a config naming an entry that does not exist and says nothing; the
   failure arrives later, downstream, after compute has been requested.
2. The run this launch was meant to compare against names version `mw_fix_single_sample_blocking`, and that version is
   published nowhere any more: the branch is gone, and Dockstore drops the version with it. A baseline whose method no
   longer resolves cannot be relaunched (docs/gap-ledger.md A18).

Both are answerable BEFORE a POST, from Dockstore's own listing, with no credentials and no spend. So this tool takes a
captured listing and says one of four things. It never fetches: the operator captures with the command in CAPTURE
below, and a check that silently reaches the network is a check that fails differently on someone else's machine.

The listing is graded the way every other listing in this repo is graded: an empty one, or one that does not cover the
organization asked for, is REFUSED rather than read as "absent". `dockstore.org` answering `[]` is not evidence that a
workflow was never published -- this repo has already measured a Terra listing answering `[]` for a workspace that held
a config (docs/troubleshooting.md).

CAPTURE (read-only, unauthenticated, no compute):

    curl -sS 'https://dockstore.org/api/ga4gh/trs/v2/tools?organization=broadinstitute&toolname=SingleSamplePipeline' \\
         -o /tmp/trs-single-sample.json     # keep it OUT of the repo: listings name other people's branches

USAGE

    checks/version_resolves.py --step 01 --from-file /tmp/trs-single-sample.json
    checks/version_resolves.py --selftest
"""

import argparse
import json
import os
import sys

KIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kit")
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "terra"))

ORG = "broadinstitute"
REFUSE = 4


def _launch_identity(step: str) -> tuple:
    """The (entry, version) the real launcher will put in methodRepoMethod, read from the launcher's own functions.

    Not a copy of them. `dstore()` in `terra/batch_rerun_step.py` builds `methodPath` AND `methodUri` from
    `steps.dockstore_of(step)` and `WDL_VERSION`, so grading those two is grading what gets POSTed -- a check that
    recomputed the entry from the profile text would pass while the body builder disagreed, which is precisely how the
    first of the two bugs above survived a leg.
    """
    import steps                      # noqa: E402  (terra/steps.py, resolved above)
    import batch_rerun_step as brs    # noqa: E402
    version = str(brs.WDL_VERSION)
    # `dstore()` is the function that fills the body's `methodRepoMethod`, so asking it for the path is the only way to
    # check the string that will be POSTed. Its bare entry name is not what TRS keys on, and rebuilding the prefix here
    # would be a second copy of a rule that already has one home.
    path = str(brs.dstore(version, str(steps.dockstore_of(step)))["methodPath"])
    return path, version


def entries(doc) -> dict:
    """`entry path -> [version names]` from a TRS tools listing, ignoring entries of the wrong class.

    A TRS tool id for a hosted GitHub workflow is `#workflow/github.com/<org>/<repo>/<EntryName>`; Terra's
    `methodPath` is `github.com/<org>/<repo>/<EntryName>`. Stripping the `#workflow/` prefix is the only translation,
    and it is spelled here rather than assumed, because the two spellings both appear in the wild and only one of them
    is what a method config stores.
    """
    out = {}
    tools = doc if isinstance(doc, list) else (doc.get("tools") or doc.get("organizations") or [])
    if isinstance(tools, dict):
        tools = [t for v in tools.values() for t in (v if isinstance(v, list) else [v])]
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        tid = str(tool.get("id") or tool.get("tool-id") or "")
        if not tid:
            continue
        name = tid.split("#workflow/", 1)[-1] if "#workflow/" in tid else tid
        out[name] = [str(v.get("name") or v.get("id") or "") for v in (tool.get("versions") or [])]
    return out


def grade(table: dict, entry: str, version: str, org: str = ORG) -> tuple:
    """`(rc, verdict)` -- one refusal per way a version can be unlaunchable, each naming what it checked.

    The order matters. "The listing covers nothing" is reported as a refusal, never as `NO SUCH ENTRY`, because reading
    an unusable listing as evidence of absence is the mistake this whole file exists to stop.
    """
    if not table:
        return REFUSE, ("LISTING UNUSABLE: 0 tool entries parsed. An empty listing is not evidence a workflow was "
                        "never published -- re-capture, and check the query named the right organization")
    covered = {e for e in table if f"/{org}/" in f"/{e}"}
    if not covered:
        return REFUSE, (f"LISTING DOES NOT COVER {org}: {len(table)} entries parsed, none under that organization. "
                        f"This listing cannot answer a question about {org} at all")
    if entry not in table:
        return 1, (f"NO SUCH ENTRY: {entry}. {len(covered)} {org} entr"
                   f"{'y' if len(covered) == 1 else 'ies'} in this listing -- the entry a launch names is the "
                   f"Dockstore name, which for some WDLs is not the file basename (.github/.dockstore.yml)")
    versions = [v for v in table[entry] if v]
    if version not in versions:
        return 1, (f"NO SUCH VERSION: {entry} @ {version}. {len(versions)} published version(s); Dockstore drops a "
                   f"version when its branch goes, so a baseline naming one is perishable (docs/gap-ledger.md A15/A18)")
    return 0, (f"RESOLVES {entry} @ {version} ({len(versions)} versions listed for this entry, "
               f"{len(covered)} {org} entries in the listing)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", default="01", help="the step whose launcher identity to check (default 01)")
    ap.add_argument("--from-file", help="a captured TRS tools listing (see CAPTURE in the module docstring)")
    ap.add_argument("--entry", help="check this entry instead of the launcher's (for fixtures and probes)")
    ap.add_argument("--version", help="check this version instead of the launcher's")
    ap.add_argument("--selftest", action="store_true", help="build synthetic fixtures and grade them")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()

    if not a.from_file:
        print("  checks/version_resolves.py reads a captured listing; pass --from-file (the CAPTURE block in --help "
              "has the curl) or --selftest. This tool never fetches: a check that reaches the network fails "
              "differently on the next machine.")
        return 2
    with open(a.from_file, encoding="utf-8") as fh:
        doc = json.load(fh)
    table = entries(doc)
    entry, version = (a.entry, a.version) if (a.entry and a.version) else _launch_identity(a.step)
    if a.entry and not a.version:
        version = _launch_identity(a.step)[1]
    rc, verdict = grade(table, entry, version)
    print(f"  {verdict}")
    return rc


def _selftest() -> int:
    """Synthetic fixtures only. A real listing is full of other people's branch names, so none is committed here."""
    ok = 0
    bad = 0

    def check(label, want_rc, needle, table, entry, version):
        nonlocal ok, bad
        rc, verdict = grade(table, entry, version)
        good = rc == want_rc and needle in verdict
        print(f"  {'ok  ' if good else 'FAIL'} {label} (exit {rc}, want {want_rc})")
        if not good:
            print(f"        verdict said: {verdict[:120]}")
            bad += 1
        else:
            ok += 1

    published = {"github.com/broadinstitute/gatk-sv/SingleSamplePipeline": ["main", "v1.1.1", "mw_probe"],
                 "github.com/broadinstitute/gatk-sv/GenotypeBatch": ["main"]}
    check("a published entry+version resolves, and says how many it saw", 0, "@ mw_probe",
          published, "github.com/broadinstitute/gatk-sv/SingleSamplePipeline", "mw_probe")
    check("the WDL's declared name is not the entry, and says so", 1, "NO SUCH ENTRY",
          published, "github.com/broadinstitute/gatk-sv/GATKSVPipelineSingleSample", "main")
    check("a version whose branch was deleted is refused, not defaulted", 1, "NO SUCH VERSION",
          published, "github.com/broadinstitute/gatk-sv/SingleSamplePipeline", "mw_fix_single_sample_blocking")
    check("an empty listing is refused rather than read as absence", REFUSE, "LISTING UNUSABLE", {},
          "github.com/broadinstitute/gatk-sv/SingleSamplePipeline", "main")
    check("a listing from another organization cannot answer about ours", REFUSE, "LISTING DOES NOT COVER",
          {"github.com/someother/workflow/Thing": ["main"]},
          "github.com/broadinstitute/gatk-sv/SingleSamplePipeline", "main")
    print(f"version_resolves: {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
