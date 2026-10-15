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

TWO DEPTHS, ONE TOOL, ONE CAPTURE

Depth 1 -- DOES THE NAME RESOLVE. `--from-file` alone. Prints `RESOLVES`, `NO SUCH ENTRY`, `NO SUCH VERSION`, or a
refusal.

Depth 2 -- IS IT THE TIP. Hand the SAME capture a tip source (`--tip SHA`, or `--tip-from-file FILE` holding the output
of `git ls-remote origin refs/heads/<branch>`) and the verdict becomes one word: `CURRENT`, `STALE`, `ABSENT`,
`REFUSED`. `STALE` names both shas as literals and prints the remedy. Depth 2 is a MODE of this file rather than a
second file because both depths read one document, and two tools answering one question with two vocabularies is a
reader reconciling two verdicts about one listing. It is also the whole point of the mode: depth 1 passing is not
evidence of launchability, it is the precondition depth 2 asks a question about.

WHY A NAME IS NOT A COMMIT -- the structural cause, not a one-off (issue #3; docs/static-checks.md)

Dockstore publishes a gatk-sv branch only while that branch is listed in THAT ENTRY's `filters.branches` in
`.github/.dockstore.yml`, and a PR branch cannot carry that edit: `.github/workflows/readonly_check.yaml` fails any PR
touching that file. Both read at `origin/main` on 2026-10-14: the `GenotypeBatch` entry's filter is `branches:
[main]`, and the check's list is `readonly_files=(".github/.dockstore.yml" "inputs/values/dockers.json")`. So when a
rebase drops the commit that added the filter, Dockstore stops syncing the branch while the version NAME it already
published keeps resolving. Depth 1 then says `RESOLVES` about a WDL that is no longer the branch you pushed, and the
launch runs the stale descriptor with a name that sounds current. There is no CI that can see this, because the filter
can never be edited on the branch that needs it.

Measured on the field case 2026-10-14, unauthenticated, one capture, no POST: `GenotypeBatch` still lists the version
`mw_genotype_scale` (Dockstore `meta_version` 2026-10-01 16:07:38) while `git ls-remote origin
refs/heads/mw_genotype_scale` answers af68ae488725dddf7d43b66547c9bfa0324db1e1 -- 12 commits past the 62d7f80d that
Dockstore was serving, whose committer timestamp IS that `meta_version` to the second. The name resolved; the WDL was
a week stale.

CAPTURE (read-only, unauthenticated, no compute) -- TWO routes, and they do not answer the same question:

    # DEPTH 2 (the commit behind the version name) -- the route issue #3 named, answered 200 with NO token:
    curl -sS 'https://dockstore.org/api/workflows/path/workflow/github.com%2Fbroadinstitute%2Fgatk-sv%2FGenotypeBatch/published?include=versions' \\
         -o /tmp/dockstore-genotypebatch.json   # keep it OUT of the repo: listings name other people's branches

    # DEPTH 1 only (does the version NAME exist) -- the TRS tools listing:
    curl -sS 'https://dockstore.org/api/ga4gh/trs/v2/tools?organization=broadinstitute&toolname=SingleSamplePipeline' \\
         -o /tmp/trs-single-sample.json

`gsvtk publish --fetch` (or `gsvtk publish-check <branch>`) runs the first GET for you -- one per entry the launcher
derives, assembled into the array shape `parse_capture` reads -- and stops at exit 3 rather than grading a partial
result, because an entry it never read is REFUSED, not ABSENT.

**Correction, and how the mistake happened (measured 2026-10-15).** An earlier revision of this file asserted that the
anonymous routes for `WorkflowVersion` rows "measured closed (404 / 401), so a depth-2 capture is a token'd GET", and
the docs repeated it. That was WRONG: what answers 401 is the same path WITHOUT `/published`, and
`.../published?include=versions` answers 200 unauthenticated with 28 version rows, EVERY one carrying `commitID`
(including `mw_genotype_scale` -> `62d7f80d0b32…`, the very stale case issue #3 was written about, and `main` ->
`743dd9d41eed…`). The TRS-tools half of the old claim still stands -- measured 2026-10-14 on the `broadinstitute`
listing: 341 version rows, 0 carrying a commit field of any spelling, because Dockstore's openapi gives `ToolVersion`
no such property. The two measurements were conflated into one sentence, and the sentence then made the depth-2 check
look impossible, so the capture recipe that got documented could never answer depth 2. Same failure class as reading a
workspace one level too shallow and reporting "no attributes": the request that was made is not the request that was
asked about.

The rows that carry a commit are `WorkflowVersion`s, whose field is `commitID`. On the published-entry body the entry
path comes from `full_workflow_path` and NOT from `workflow_path` (which is the descriptor FILE, `/Dockstore.cwl`) or
`path` (the repo) -- see `_published_entry`. This tool never fetches: it grades the file you give it, and REFUSES --
rather than guessing `CURRENT` -- when that file carries no commitID for the entry asked about.

USAGE

    checks/version_resolves.py --step 01 --from-file /tmp/trs-single-sample.json
    checks/version_resolves.py --from-file capture.json --all-steps \\
        --tip-from-file /tmp/ls-remote.txt        # depth 2, every entry the launcher names
    checks/version_resolves.py --print-entries --all-steps   # the entries a fetch would read, one per line
    checks/version_resolves.py --selftest         # synthetic fixtures; the gate's legs grade the committed ones
"""

import argparse
import json
import os
import re
import sys

KIT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "kit")
sys.path.insert(0, KIT)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "terra"))

ORG = "broadinstitute"
REFUSE = 4
CURRENT, STALE, ABSENT = "CURRENT", "STALE", "ABSENT"

# `commitID` is Dockstore's own spelling (`WorkflowVersion`/`Tag`/`Version` in its openapi, read 2026-10-14); the other
# two are what a snake/camel writer produces when a capture is re-serialised. Spelled here, not guessed: a version row
# with no commit is a REFUSAL, so a missed spelling would read as "this capture cannot answer".
COMMIT_KEYS = ("commitID", "commit_id", "commitId")

# The entry-path key, in the two document shapes Dockstore publishes for one listing. An entry path always contains a
# `/`, which is what separates it from the numeric DB ids that also live in a field called `id`.
PATH_KEYS = ("id", "tool-id", "toolId", "toolPath", "tool-path", "workflowPath", "workflow_path",
             "entryPath", "entry-path", "fullPath")

SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _launch_identity(step: str) -> tuple:
    """The (entry, version) the real launcher will put in methodRepoMethod, read from the launcher's own functions.

    Not a copy of them. `dstore()` in `terra/batch_rerun_step.py` builds `methodPath` AND `methodUri` from
    `steps.dockstore_of(step)` and `WDL_VERSION`, so grading those two is grading what gets POSTed -- a check that
    recomputed the entry from the profile text would pass while the body builder disagreed, which is precisely how the
    first of the two bugs above survived a leg.
    """
    import steps                      # noqa: E402  (terra/steps.py, resolved above)
    import batch_rerun_step as brs    # noqa: E402
    version = _launch_version()
    # `dstore()` is the function that fills the body's `methodRepoMethod`, so asking it for the path is the only way to
    # check the string that will be POSTed. Its bare entry name is not what TRS keys on, and rebuilding the prefix here
    # would be a second copy of a rule that already has one home.
    path = str(brs.dstore(version, str(steps.dockstore_of(step)))["methodPath"])
    return path, version


def _entry_key(raw: str) -> str:
    """`#workflow/github.com/<org>/<repo>/<Entry>` -> `github.com/<org>/<repo>/<Entry>`; anything else untouched.

    A TRS tool id for a hosted GitHub workflow carries the `#workflow/` prefix; a Dockstore `workflowPath` and Terra's
    `methodPath` do not. Stripping the prefix is the ONLY translation between the two spellings, and it is spelled here
    rather than assumed, because both appear in the wild and only one of them is what a method config stores.
    """
    return raw.split("#workflow/", 1)[-1] if "#workflow/" in raw else raw


def _path_of(obj: dict) -> str:
    """The entry path a capture row names, in either shape -- or "" when it names none.

    The `/` test is what separates an entry path from the numeric entry id that also lives in a field called `id`: a
    row whose "path" is `4139` would otherwise be filed as an entry named `4139`, and a listing that cannot answer a
    question about the entry asked for is exactly the thing this file must refuse rather than invent.
    """
    for key in PATH_KEYS:
        val = obj.get(key)
        if isinstance(val, str) and "/" in val:
            return _entry_key(val.strip())
    return ""


def _version_name(obj: dict) -> str:
    """The version a row is about: `name`, Dockstore's `reference`, or the tail of a TRS `path:version` id."""
    for key in ("name", "reference"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    val = obj.get("id")
    if isinstance(val, str) and ":" in val:
        return _entry_key(val.rsplit(":", 1)[-1])
    return ""


def commit_of(obj: dict) -> str:
    """The commit this version row says Dockstore serves, or "" when it says none."""
    for key in COMMIT_KEYS:
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _published_entry(obj):
    """`(entry path, version rows)` when `obj` is a published ENTRY document, else `("", None)`.

    `GET /api/workflows/path/workflow/<entry>/published?include=versions` answers 200 UNAUTHENTICATED (measured
    2026-10-15: 72 KB, 28 versions, EVERY one carrying `commitID` -- including `mw_genotype_scale` ->
    `62d7f80d0b32…`, the exact stale case issue #3 was written about). It is the route the issue named and the one
    this file previously declared closed; see the module docstring's correction.

    The entry key is read from `full_workflow_path` (falling back to `trsId`) and deliberately NOT from `_path_of`:
    on this body `workflow_path` is the DESCRIPTOR FILE inside the repo (`/Dockstore.cwl`) and `path` is the repo
    (`github.com/broadinstitute/gatk-sv`). Both contain a `/`, so both satisfy the generic walk's entry-path test --
    filing all 28 versions under a `.cwl` filename and then answering `ABSENT` for the entry actually asked about. A
    wrong answer that looks like an answer is what this file exists to refuse.
    """
    if not isinstance(obj, dict):
        return "", None
    rows = obj.get("workflowVersions")
    if not isinstance(rows, list):
        return "", None
    for key in ("full_workflow_path", "trsId"):
        val = obj.get(key)
        if isinstance(val, str) and "/" in val:
            return _entry_key(val.strip()), rows
    return "", None


def parse_capture(doc) -> dict:
    """One parse of one capture -> `{shape, table, commits, rows, with_commit, unnamed, tools, versions}`.

    `table` is `entry path -> [version names]`; `commits` is `entry path -> {version name: commitID or ""}`. Both come
    out of the same walk, because the two depths must not disagree about what the document said.

    Two shapes, chosen per row rather than by guessing a document format, because Dockstore publishes the same listing
    both ways: a TRS tools listing (`{"id": "#workflow/...", "versions": [...]}`), and a per-entry list of
    `WorkflowVersion` rows (`{"workflowPath": "...", "name": ..., "commitID": ...}`) -- the second being the only one
    of the two that carries a commit at all (measured 2026-10-14; see the module docstring). `shape` is printed on
    every run so a reader knows which one was parsed, and `unnamed` counts rows this walk could not place: a refusal
    quotes it rather than pretending the capture was read in full.
    """
    items = doc if isinstance(doc, list) else (doc.get("tools") or doc.get("organizations") or [])
    if isinstance(items, dict):
        items = [t for v in items.values() for t in (v if isinstance(v, list) else [v])]
    table: dict = {}
    commits: dict = {}
    counts = {"rows": 0, "with_commit": 0, "unnamed": 0, "tools": 0, "versions": 0, "published": 0}

    def add(entry, name, commit):
        table.setdefault(entry, []).append(name)
        commits.setdefault(entry, {})[name] = commit
        counts["rows"] += 1
        if commit:
            counts["with_commit"] += 1

    # Published ENTRY documents (see `_published_entry`): one of them, or a list of them -- the list is what a
    # multi-entry `publish-check` assembles after fetching one URL per entry.
    published = []
    for cand in (doc if isinstance(doc, list) else [doc]):
        pub_entry, pub_rows = _published_entry(cand)
        if pub_entry:
            published.append((pub_entry, pub_rows))
    for pub_entry, pub_rows in published:
        for ver in pub_rows:
            if not isinstance(ver, dict):
                continue
            name = _version_name(ver)
            if not name:
                counts["unnamed"] += 1
                continue
            counts["published"] += 1
            add(pub_entry, name, commit_of(ver))

    for item in items:
        if not isinstance(item, dict):
            continue
        if _published_entry(item)[0]:
            continue    # already counted above; must not also be filed as an unnamed row
        if isinstance(item.get("versions"), list):
            counts["tools"] += 1
            entry = _path_of(item)
            if not entry:
                counts["unnamed"] += 1
                continue
            for ver in item["versions"]:
                if not isinstance(ver, dict):
                    continue
                name = _version_name(ver)
                if not name:
                    counts["unnamed"] += 1
                    continue
                add(entry, name, commit_of(ver))
        else:
            entry, name = _path_of(item), _version_name(item)
            if not entry or not name:
                counts["unnamed"] += 1
                continue
            counts["versions"] += 1
            add(entry, name, commit_of(item))
    shape = ("Dockstore published entry (workflowVersions)" if counts["published"] else
             ("TRS tools listing" if counts["tools"] else
              ("Dockstore workflowVersions list" if counts["versions"] else "unrecognised shape")))
    return dict(table=table, commits=commits, shape=shape, **counts)


def entries(doc) -> dict:
    """`entry path -> [version names]` -- one projection of `parse_capture`, which owns the shapes and the one
    translation between the TRS and Terra spellings."""
    return parse_capture(doc)["table"]


def tips_from_ls_remote(text: str) -> dict:
    """`git ls-remote` output -> `{branch: sha}`, reading the format git actually prints.

    `<sha>\trefs/heads/<branch>` per line, and nothing else: tags, HEAD and a `warning:` line are not branches, and a
    tip source that silently read every ref as a branch would compare a version against some other ref and call the
    answer a verdict.
    """
    out = {}
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        sha, ref = parts[0].strip(), parts[1].strip()
        if ref.startswith("refs/heads/") and SHA_RE.match(sha):
            out[ref[len("refs/heads/"):]] = sha.lower()
    return out


def _preclude(table: dict, org: str = ORG) -> tuple:
    """`(refusal_or_None, covered_entries)` -- the two ways a capture cannot answer at all, shared by both depths.

    The order matters. "The listing covers nothing" is reported as a refusal, never as `NO SUCH ENTRY` or `ABSENT`,
    because reading an unusable listing as evidence of absence is the mistake this whole file exists to stop.
    """
    if not table:
        return (REFUSE, ("LISTING UNUSABLE: 0 tool entries parsed. An empty listing is not evidence a workflow was "
                         "never published -- re-capture, and check the query named the right organization")), set()
    covered = {e for e in table if f"/{org}/" in f"/{e}"}
    if not covered:
        return (REFUSE, (f"LISTING DOES NOT COVER {org}: {len(table)} entries parsed, none under that organization. "
                         f"This listing cannot answer a question about {org} at all")), set()
    return None, covered


def grade(table: dict, entry: str, version: str, org: str = ORG) -> tuple:
    """`(rc, verdict)` for depth 1 -- one answer per way a version can be unlaunchable, each naming what it checked.

    Refusals come first, and come from `_preclude`, the same function depth 2 uses: "the capture is unusable" means the
    same thing in both depths and has one wording.
    """
    stop, covered = _preclude(table, org)
    if stop:
        return stop
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


def remedy(entry: str, version: str, branch: str) -> str:
    """The way out of `STALE`/`ABSENT`, printed with both verdicts, in the order it has to be done.

    A throwaway branch rather than a filter edit on the real branch, because that edit cannot live on the branch that
    needs it: `readonly_check.yaml` fails any PR touching `.github/.dockstore.yml`. Naming `<branch>_terra` (the
    sidecar's OWN name) in `filters.branches` is what Dockstore publishes -- the version name is the branch name, so
    the rerun's `GSV_WDL_VERSION` moves to the sidecar name at step 3, and that move is the point of the check: it is
    the difference between launching the tip and launching whatever Dockstore last saw.
    """
    side = branch if branch.endswith("_terra") else f"{branch}_terra"
    return ("  REMEDY (this tool never pushes, never POSTs, never touches Dockstore -- these are the commands, typed\n"
            f"  by you, against {entry} @ {version}):\n"
            f"    1. git branch {side} <tip-sha> && git push origin {side}\n"
            f"       a throwaway branch at the tip, nothing else on it{' (already a sidecar name; add the filter commit to it)' if side == branch else ''}\n"
            f"    2. one commit on {side} adding {side} to THIS ENTRY's filters.branches in\n"
            f"       .github/.dockstore.yml. Not on {branch}: .github/workflows/readonly_check.yaml fails any PR\n"
            "       touching that file, which is exactly how a version goes stale while its name keeps resolving.\n"
            "    3. wait for Dockstore's sync -- under a minute in practice -- then re-run this check naming\n"
            f"       {side} as the version, and set GSV_WDL_VERSION={side} on the rerun that reads it.")


def grade_publish(parsed: dict, entry: str, version: str, tip: str, branch: str, org: str = ORG) -> tuple:
    """`(rc, verdict)` for depth 2 -- one of `CURRENT`, `STALE`, `ABSENT`, `REFUSED`, in that ladder.

    Four verdicts, and the fourth is what keeps the other three honest. `REFUSED` covers every way the capture or the
    tip source cannot answer the question -- including a capture that carries no commitID, which is what the
    unauthenticated TRS listing IS. Answering `CURRENT` to a question the file cannot answer is the failure class this
    repo keeps meeting, and it is the one that costs money: a rerun of a stale descriptor launches, bills, and
    disagrees with the baseline for reasons nobody can reproduce.

    The ladder runs capture-side first, because "the name is not in the listing" is a fact independent of the tip, and
    stops at the first refusal: a verdict is one answer, not a summary of everything that was tried.
    """
    table, commits = parsed["table"], parsed["commits"]
    stop, covered = _preclude(table, org)
    if stop:
        # Same two refusals as depth 1, same words, with the verdict word in front of them: depth 1's messages are quoted
        # verbatim in docs/gap-ledger.md and graded by legs that match on them, and depth 2 promised four words. One
        # sentence, two vocabularies, both kept honest -- the prefix is the whole difference.
        return stop[0], f"REFUSED: {stop[1]}"
    if entry not in table:
        return 1, (f"{ABSENT} {entry}: no such entry in this capture ({len(covered)} {org} entr"
                   f"{'y' if len(covered) == 1 else 'ies'} parsed, shape: {parsed['shape']}). Nothing is published "
                   f"there to launch, so no version of it is current.\n" + remedy(entry, version, branch))
    versions = [v for v in table[entry] if v]
    if version not in versions:
        return 1, (f"{ABSENT} {entry} @ {version}: {len(versions)} version(s) listed for this entry and that is not "
                   f"one of them. Dockstore publishes a branch only while that entry's filters.branches lists it, and "
                   f"drops the version when the branch goes (docs/gap-ledger.md A15/A18) -- so a name that stopped "
                   f"being published and a branch that was deleted look identical from here, and neither is "
                   f"launchable.\n" + remedy(entry, version, branch))
    served = commits.get(entry, {}).get(version, "")
    if not served:
        withc = sum(1 for e in commits for v in commits[e].values() if v)
        return REFUSE, (f"REFUSED: NO COMMIT TO COMPARE for {entry} @ {version}: this capture carries commitID on "
                        f"{withc} of {parsed['rows']} version rows ({parsed['shape']}), and not on this one. The "
                        f"listing that answers 'does the name resolve' is not the listing that carries a commit "
                        f"(measured 2026-10-14: 341 rows of the unauthenticated broadinstitute TRS listing, 0 with a "
                        f"commit field). Depth 1 already answered your question -- RESOLVES -- and a stale descriptor "
                        f"passes it. Capture the per-entry workflowVersions list (CAPTURE in --help), or pass --tip "
                        f"and read the two shas side by side.")
    if not tip:
        return REFUSE, (f"REFUSED: NO TIP TO COMPARE AGAINST for refs/heads/{branch}. Pass one, and this answers:\n"
                        f"    git -C <gatk-sv checkout> ls-remote origin refs/heads/{branch}\n"
                        f"  -> --tip <sha>, or --tip-from-file <file holding that output>. This tool never fetches: a "
                        f"check that silently reaches the network is a check that fails differently on someone "
                        f"else's machine.")
    if not SHA_RE.match(tip):
        return REFUSE, (f"REFUSED: NOT A COMMIT: --tip {tip!r} is not a hex sha (7-40). Paste the first field of the "
                        f"`git ls-remote origin refs/heads/{branch}` line, not the line itself")
    tip = tip.lower()
    if served.lower() == tip:
        return 0, (f"{CURRENT} {entry} @ {version}: Dockstore serves {served} and refs/heads/{branch} is {tip} -- the "
                   f"same commit, so the descriptor a launch reads IS the tip")
    return 1, (f"{STALE} {entry} @ {version}: Dockstore serves commit {served} while refs/heads/{branch} is "
               f"{tip}. The name resolves -- depth 1 says RESOLVES, `validate` passes, and the launch runs the WDL "
               f"Dockstore last synced, not the branch you pushed.\n" + remedy(entry, version, branch))


def launch_entries(step: str = None, version: str = None) -> list:
    """Every `(entry, version)` a rerun can name -- asked of the launcher's own functions, never a copy of them.

    One entry per step the module profile carries. `--all-steps` exists because "the entry a rerun names" is a set and
    not a singleton: a check that graded one step's entry while the operator launched another answers a question nobody
    asked and prints it as an all-clear, which is the same failure the `--images` note in `gsvtk check --help` records
    for a forwarded ref.
    """
    import steps                          # noqa: E402  (terra/steps.py, resolved above)
    version = str(version or _launch_version())
    out = []
    for num in steps.known_steps() if step is None else [step]:
        out.append((f"github.com/broadinstitute/gatk-sv/{steps.dockstore_of(num)}", version))
    return out


def _launch_version() -> str:
    """The version string the launcher will put in `methodRepoMethod`, read from the launcher itself."""
    import batch_rerun_step as brs        # noqa: E402
    return str(brs.WDL_VERSION)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--step", default="01", help="the step whose launcher identity to check (default 01)")
    ap.add_argument("--from-file", help="a captured listing (CAPTURE in the module docstring has the curl); this "
                                        "tool never fetches, so the capture is the only live thing in the run")
    ap.add_argument("--entry", help="check this entry instead of the launcher's (for fixtures and probes)")
    ap.add_argument("--version", help="check this version instead of the launcher's")
    ap.add_argument("--all-steps", action="store_true", help="answer for EVERY entry a rerun can name, one verdict "
                                                             "per step of the module profile, not just --step's")
    ap.add_argument("--branch", help="depth 2: the branch the version names (default: the version name itself, a "
                                     "Dockstore version name for these entries IS a branch name)")
    ap.add_argument("--tip", help="depth 2: the commit at refs/heads/<branch>, the first field of `git ls-remote "
                                  "origin refs/heads/<branch>`")
    ap.add_argument("--tip-from-file", help="depth 2: a file holding that `git ls-remote` output (what the offline "
                                            "legs grade); several refs in one file are read as one branch-per-line "
                                            "listing")
    ap.add_argument("--selftest", action="store_true", help="build synthetic fixtures and grade them")
    ap.add_argument("--print-entries", action="store_true", help="print the entry path(s) this invocation would grade, "
                                                                 "one per line, and stop. This tool never fetches, so "
                                                                 "the caller that DOES fetch (`gsvtk publish --fetch`) "
                                                                 "asks here which URLs to read rather than keeping a "
                                                                 "second copy of the entry list that could disagree "
                                                                 "with this one")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()

    # Entries first, capture second: the fetcher needs to know WHAT to read before any capture exists, and the entry
    # list is derived from the launcher identity (the module profile), so printing it from here is the only way to
    # keep one derivation. Order-preserving dedupe: several steps share one entry, and fetching it twice would put the
    # same entry in the assembled capture twice.
    if a.print_entries:
        if a.all_steps:
            pairs = launch_entries(None, a.version)
        elif a.entry and a.version:
            pairs = [(a.entry, a.version)]
        else:
            ident_ent, ident_ver = _launch_identity(a.step)
            pairs = [(a.entry or ident_ent, a.version or ident_ver)]
        seen = set()
        for ent, _ver in pairs:
            if ent not in seen:
                seen.add(ent)
                print(ent)
        return 0

    if not a.from_file:
        print("  checks/version_resolves.py reads a captured listing; pass --from-file (the CAPTURE block in --help "
              "has the curl) or --selftest. This tool never fetches: a check that reaches the network fails "
              "differently on the next machine.")
        return 2
    with open(a.from_file, encoding="utf-8") as fh:
        doc = json.load(fh)
    parsed = parse_capture(doc)

    # Depth 2 is asked for by ANY of its three inputs, not by a tip alone: `--branch` on its own gets the refusal that
    # prints the tip command, because answering depth 1 to someone who asked for depth 2 is how a stale WDL earns a
    # pass. `--tip-from-file` is the seam the offline legs drive: a tip source is data, never a fetch.
    deep = bool(a.tip or a.tip_from_file or a.branch)
    tips = {}
    if a.tip_from_file:
        with open(a.tip_from_file, encoding="utf-8") as fh:
            tips = tips_from_ls_remote(fh.read())

    if a.all_steps:
        targets = launch_entries(None, a.version)
        if a.step != "01":
            print(f"  (--all-steps answers every step the profile names; --step {a.step} is not a filter here)")
    else:
        entry, version = (a.entry, a.version) if (a.entry and a.version) else _launch_identity(a.step)
        if a.entry and not a.version:
            version = _launch_version()
        targets = [(entry, version)]

    n_ent = len(parsed["table"])
    print(f"  capture: {parsed['shape']}, {n_ent} entr{'y' if n_ent == 1 else 'ies'}, "
          f"{parsed['rows']} version rows, "
          f"{parsed['with_commit']} carrying a commitID"
          + (f", {parsed['unnamed']} row(s) this tool could not place" if parsed["unnamed"] else "")
          + ("; tip source: none given" if deep and not (a.tip or a.tip_from_file) else ""))
    counts = {CURRENT: 0, STALE: 0, ABSENT: 0, "REFUSED": 0}
    worst = 0
    for entry, version in targets:
        branch = a.branch or version
        if deep:
            rc, verdict = grade_publish(parsed, entry, version, a.tip or tips.get(branch, ""), branch)
        else:
            rc, verdict = grade(parsed["table"], entry, version)
        print(f"  {verdict}")
        worst = max(worst, rc)
        for word in (CURRENT, STALE, ABSENT, "REFUSED"):
            if verdict.startswith(word):
                counts[word] += 1
                break
    if len(targets) > 1:
        # One line counting the four verdicts, because "exit 1 over five entries" does not say whether one entry was
        # stale or four were never published -- different remedies, and a different question actually answered.
        scope = f"{len(targets)} entries named by the launcher, version {targets[0][1]}"
        if deep:
            scope += f", refs/heads/{a.branch or targets[0][1]}"
        print(f"  publish check: {counts[CURRENT]} CURRENT, {counts[STALE]} STALE, {counts[ABSENT]} ABSENT, "
              f"{counts['REFUSED']} REFUSED ({scope})")
    return worst


def _selftest() -> int:
    """Synthetic fixtures in this function; the GATE's legs grade the committed excerpt under
    `scripts/selftest.d/fixtures/trs/` (see the legs in `scripts/selftest.d/profiles.sh`). A full listing is a list of
    other people's branch names, so only an excerpt of one is committed -- and this suite stays shape-true to both
    capture shapes without carrying either.
    """
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

    def check_deep(label, want_rc, needles, doc, entry, version, tip, branch="", absent=()):
        """Depth 2, from a DOCUMENT rather than a hand-built table: the parse is part of what is graded, so a shape
        this walk cannot read has to fail here rather than pass downstream as an empty listing.
        """
        nonlocal ok, bad
        rc, verdict = grade_publish(parse_capture(doc), entry, version, tip, branch or version)
        good = rc == want_rc and all(n in verdict for n in needles) and not any(n in verdict for n in absent)
        print(f"  {'ok  ' if good else 'FAIL'} {label} (exit {rc}, want {want_rc})")
        if not good:
            print(f"        verdict said: {verdict[:160]}")
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

    # Depth 2. `1`*40 and `2`*40 are synthetic commits; the gate's legs carry the real pair from the field case.
    tip_a, tip_b = "1" * 40, "2" * 40
    gb = "github.com/broadinstitute/gatk-sv/GenotypeBatch"

    def wl(entry, name, commit=None, **extra):
        row = {"workflowPath": entry, "name": name}
        if commit is not None:
            row["commitID"] = commit
        row.update(extra)
        return row

    listed = [wl(gb, "mw_probe", tip_a), wl(gb, "main", tip_b)]
    check_deep("a version whose commit IS the tip says CURRENT and does not say STALE", 0,
               ["CURRENT", tip_a], listed, gb, "mw_probe", tip_a, absent=[STALE])
    check_deep("a version behind the tip names BOTH shas and the filter it is missing", 1,
               [STALE, tip_a, tip_b, "filters.branches"], listed, gb, "mw_probe", tip_b, absent=[CURRENT])
    check_deep("a version Dockstore does not list is ABSENT, with the remedy, not a shrug", 1,
               [ABSENT, "mw_gone", "REMEDY"], listed, gb, "mw_gone", tip_a)
    check_deep("an entry the capture never heard of is ABSENT, naming how much it did read", 1,
               [ABSENT, "1 broadinstitute"], listed, "github.com/broadinstitute/gatk-sv/Nope", "main", tip_a)
    check_deep("a capture with no commitID anywhere is REFUSED, never CURRENT", REFUSE,
               ["REFUSED", "NO COMMIT TO COMPARE", "0 of 3"],
               [{"id": "#workflow/" + gb, "versions": [{"name": n} for n in ("main", "mw_probe", "v1")]}],
               gb, "main", tip_a, absent=[CURRENT, STALE, ABSENT])
    check_deep("no tip given is a refusal that prints the tip command", REFUSE,
               ["REFUSED", "refs/heads/mw_probe", "ls-remote"], listed, gb, "mw_probe", "")
    check_deep("a tip source that omits the branch refuses rather than reading absence", REFUSE,
               ["REFUSED", "refs/heads/mw_other"], listed, gb, "mw_probe", "", branch="mw_other")
    check_deep("an empty capture is refused in depth 2 too, and never called ABSENT", REFUSE,
               ["LISTING UNUSABLE"], [], gb, "main", tip_a, absent=[ABSENT])
    check_deep("a capture from another organization still cannot answer", REFUSE,
               ["LISTING DOES NOT COVER"], [wl("github.com/someother/x/Thing", "main", tip_a)], gb, "main", tip_a)
    check_deep("a tip that is not a hex sha is refused, not compared", REFUSE,
               ["NOT A COMMIT"], listed, gb, "mw_probe", "af68ae48 (HEAD -> mw_probe)")
    check_deep("a commit spelled `commit_id` on a `reference` row is still read", 1,
               [STALE], [{"workflowPath": gb, "reference": "mw_probe", "commit_id": tip_a}], gb, "mw_probe", tip_b)
    check_deep("a numeric entry id is not mistaken for an entry path", REFUSE,
               ["LISTING UNUSABLE"], [{"id": 4139, "name": "main", "commitID": tip_a}], gb, "main", tip_a,
               absent=[ABSENT, CURRENT])
    print(f"version_resolves: {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
