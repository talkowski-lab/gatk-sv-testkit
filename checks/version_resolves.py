#!/usr/bin/env python3
"""Does the workflow version this launcher is about to name actually resolve on Dockstore?

Two ways a launch can name something Dockstore does not serve:

1. The Dockstore entry name is not always the WDL's declared name: `/wdl/GATKSVPipelineSingleSample.wdl` is published
   as `SingleSamplePipeline` (`.github/.dockstore.yml`). Terra accepts a config naming a missing entry and the
   failure only shows up later.
2. Dockstore drops a version when its branch is deleted, so an old baseline's version may no longer resolve.

This tool grades a captured listing; it never fetches. An empty listing, or one that does not cover the requested
organization, is REFUSED rather than read as "absent".

TWO DEPTHS

Depth 1 -- DOES THE NAME RESOLVE. `--from-file` alone. Prints `RESOLVES`, `NO SUCH ENTRY`, `NO SUCH VERSION`, or a
refusal.

Depth 2 -- IS IT THE TIP. Add a tip source (`--tip SHA`, or `--tip-from-file FILE` holding the output of
`git ls-remote origin refs/heads/<branch>`) and the verdict is one of `CURRENT`, `STALE`, `ABSENT`, `REFUSED`.
`STALE` names both shas and prints the remedy.

Why a name is not a commit: Dockstore publishes a gatk-sv branch only while it is listed in that entry's
`filters.branches` in `.github/.dockstore.yml`, and `.github/workflows/readonly_check.yaml` fails any PR that edits
that file. If a rebase drops the commit that added the filter, Dockstore stops syncing the branch but the version
name keeps resolving, so depth 1 passes while the published WDL is stale.

CAPTURE (read-only, unauthenticated, no compute) -- two routes that answer different questions:

    # DEPTH 2 (carries the commit behind each version name):
    curl -sS 'https://dockstore.org/api/workflows/path/workflow/github.com%2Fbroadinstitute%2Fgatk-sv%2FGenotypeBatch/published?include=versions' \\
         -o /tmp/dockstore-genotypebatch.json   # keep it OUT of the repo: listings name other people's branches

    # DEPTH 1 only (does the version NAME exist; no commit field):
    curl -sS 'https://dockstore.org/api/ga4gh/trs/v2/tools?organization=broadinstitute&toolname=SingleSamplePipeline' \\
         -o /tmp/trs-single-sample.json

`gsvtk publish --fetch` (or `gsvtk publish-check <branch>`) runs the first GET for each entry the launcher derives,
assembles them into the array shape `parse_capture` reads, and exits 3 rather than grading a partial result.

On the published-entry body, the entry path comes from `full_workflow_path`, not `workflow_path` (the descriptor
file) or `path` (the repo). A capture with no commitID for the requested entry is REFUSED, never `CURRENT`.

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

# `commitID` is Dockstore's spelling; the others appear when a capture is re-serialised.
COMMIT_KEYS = ("commitID", "commit_id", "commitId")

# The entry-path key in either listing shape. An entry path contains `/`; a numeric DB id in `id` does not.
PATH_KEYS = ("id", "tool-id", "toolId", "toolPath", "tool-path", "workflowPath", "workflow_path",
             "entryPath", "entry-path", "fullPath")

SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _launch_identity(step: str) -> tuple:
    """The (entry, version) the launcher will put in methodRepoMethod, from `batch_rerun_step.dstore()` itself."""
    import steps                      # noqa: E402  (terra/steps.py, resolved above)
    import batch_rerun_step as brs    # noqa: E402
    version = _launch_version()
    path = str(brs.dstore(version, str(steps.dockstore_of(step)))["methodPath"])
    return path, version


def _entry_key(raw: str) -> str:
    """`#workflow/github.com/<org>/<repo>/<Entry>` -> `github.com/<org>/<repo>/<Entry>`; anything else untouched.

    TRS tool ids carry the `#workflow/` prefix; Dockstore's `workflowPath` and Terra's `methodPath` do not.
    """
    return raw.split("#workflow/", 1)[-1] if "#workflow/" in raw else raw


def _path_of(obj: dict) -> str:
    """The entry path a capture row names, in either shape -- or "" when it names none.

    The `/` test rejects numeric entry ids, which also live in a field called `id`.
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

    This is the body of `GET /api/workflows/path/workflow/<entry>/published?include=versions`, whose version rows
    carry `commitID`. The entry key comes from `full_workflow_path` (or `trsId`), not `_path_of`: here
    `workflow_path` is the descriptor file and `path` is the repo, and both contain a `/`.
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

    `table` is `entry path -> [version names]`; `commits` is `entry path -> {version name: commitID or ""}`.

    Shapes are detected per row: published entry documents, a TRS tools listing (`{"id": "#workflow/...",
    "versions": [...]}`, no commits), or a list of `WorkflowVersion` rows (`{"workflowPath": ..., "name": ...,
    "commitID": ...}`). `unnamed` counts rows that could not be placed.
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

    # Published entry documents: one, or a list of them (what a multi-entry `publish-check` assembles).
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
    """`entry path -> [version names]` from `parse_capture`."""
    return parse_capture(doc)["table"]


def tips_from_ls_remote(text: str) -> dict:
    """`git ls-remote` output -> `{branch: sha}`. Only `<sha>\trefs/heads/<branch>` lines are read."""
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
    """`(refusal_or_None, covered_entries)`: refuse an empty listing or one that misses `org`. Shared by both depths."""
    if not table:
        return (REFUSE, ("LISTING UNUSABLE: 0 tool entries parsed. An empty listing is not evidence a workflow was "
                         "never published -- re-capture, and check the query named the right organization")), set()
    covered = {e for e in table if f"/{org}/" in f"/{e}"}
    if not covered:
        return (REFUSE, (f"LISTING DOES NOT COVER {org}: {len(table)} entries parsed, none under that organization. "
                         f"This listing cannot answer a question about {org} at all")), set()
    return None, covered


def grade(table: dict, entry: str, version: str, org: str = ORG) -> tuple:
    """`(rc, verdict)` for depth 1."""
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
                   f"version when its branch goes, so a baseline naming one is perishable")
    return 0, (f"RESOLVES {entry} @ {version} ({len(versions)} versions listed for this entry, "
               f"{len(covered)} {org} entries in the listing)")


def remedy(entry: str, version: str, branch: str) -> str:
    """Steps to fix `STALE`/`ABSENT`: publish a `<branch>_terra` sidecar branch with its own Dockstore filter.

    The filter edit can't go on the real branch because `readonly_check.yaml` fails PRs touching
    `.github/.dockstore.yml`. The version name is the branch name, so the rerun then uses the sidecar name.
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
    """`(rc, verdict)` for depth 2: `CURRENT`, `STALE`, `ABSENT`, or `REFUSED`.

    `REFUSED` covers any capture or tip source that can't answer, including a capture with no commitID. Capture-side
    checks run first; the first failing check decides the verdict.
    """
    table, commits = parsed["table"], parsed["commits"]
    stop, covered = _preclude(table, org)
    if stop:
        # Same refusal text as depth 1, prefixed with the verdict word.
        return stop[0], f"REFUSED: {stop[1]}"
    if entry not in table:
        return 1, (f"{ABSENT} {entry}: no such entry in this capture ({len(covered)} {org} entr"
                   f"{'y' if len(covered) == 1 else 'ies'} parsed, shape: {parsed['shape']}). Nothing is published "
                   f"there to launch, so no version of it is current.\n" + remedy(entry, version, branch))
    versions = [v for v in table[entry] if v]
    if version not in versions:
        return 1, (f"{ABSENT} {entry} @ {version}: {len(versions)} version(s) listed for this entry and that is not "
                   f"one of them. Dockstore publishes a branch only while that entry's filters.branches lists it, and "
                   f"drops the version when the branch goes -- so a name that stopped "
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
    """Every `(entry, version)` a rerun can name, one per step in the module profile (or just `step`)."""
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

    # Used by the fetcher before any capture exists. Deduped in order, since several steps share an entry.
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

    # Any of the three depth-2 inputs selects depth 2; `--branch` alone gets the refusal that prints the tip command.
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
        # Summary line counting each verdict.
        scope = f"{len(targets)} entries named by the launcher, version {targets[0][1]}"
        if deep:
            scope += f", refs/heads/{a.branch or targets[0][1]}"
        print(f"  publish check: {counts[CURRENT]} CURRENT, {counts[STALE]} STALE, {counts[ABSENT]} ABSENT, "
              f"{counts['REFUSED']} REFUSED ({scope})")
    return worst


def _selftest() -> int:
    """Synthetic fixtures. The committed excerpts under `scripts/selftest.d/fixtures/trs/` are graded by
    `scripts/selftest.d/profiles.sh`.
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
        """Depth 2 from a document, so parsing is tested too."""
        nonlocal ok, bad
        rc, verdict = grade_publish(parse_capture(doc), entry, version, tip, branch or version)
        good = rc == want_rc and all(n in verdict for n in needles) and not any(n in verdict for n in absent)
        print(f"  {'ok  ' if good else 'FAIL'} {label} (exit {rc}, want {want_rc})")
        if not good:
            print(f"        verdict said: {verdict[:160]}")
            bad += 1
        else:
            ok += 1

    published = {"github.com/broadinstitute/gatk-sv/SingleSamplePipeline": ["main", "v1.1.1", "dev_probe"],
                 "github.com/broadinstitute/gatk-sv/GenotypeBatch": ["main"]}
    check("a published entry+version resolves, and says how many it saw", 0, "@ dev_probe",
          published, "github.com/broadinstitute/gatk-sv/SingleSamplePipeline", "dev_probe")
    check("the WDL's declared name is not the entry, and says so", 1, "NO SUCH ENTRY",
          published, "github.com/broadinstitute/gatk-sv/GATKSVPipelineSingleSample", "main")
    check("a version whose branch was deleted is refused, not defaulted", 1, "NO SUCH VERSION",
          published, "github.com/broadinstitute/gatk-sv/SingleSamplePipeline", "dev_fix_single_sample_blocking")
    check("an empty listing is refused rather than read as absence", REFUSE, "LISTING UNUSABLE", {},
          "github.com/broadinstitute/gatk-sv/SingleSamplePipeline", "main")
    check("a listing from another organization cannot answer about ours", REFUSE, "LISTING DOES NOT COVER",
          {"github.com/someother/workflow/Thing": ["main"]},
          "github.com/broadinstitute/gatk-sv/SingleSamplePipeline", "main")

    # Depth 2, with synthetic commits.
    tip_a, tip_b = "1" * 40, "2" * 40
    gb = "github.com/broadinstitute/gatk-sv/GenotypeBatch"

    def wl(entry, name, commit=None, **extra):
        row = {"workflowPath": entry, "name": name}
        if commit is not None:
            row["commitID"] = commit
        row.update(extra)
        return row

    listed = [wl(gb, "dev_probe", tip_a), wl(gb, "main", tip_b)]
    check_deep("a version whose commit IS the tip says CURRENT and does not say STALE", 0,
               ["CURRENT", tip_a], listed, gb, "dev_probe", tip_a, absent=[STALE])
    check_deep("a version behind the tip names BOTH shas and the filter it is missing", 1,
               [STALE, tip_a, tip_b, "filters.branches"], listed, gb, "dev_probe", tip_b, absent=[CURRENT])
    check_deep("a version Dockstore does not list is ABSENT, with the remedy, not a shrug", 1,
               [ABSENT, "dev_gone", "REMEDY"], listed, gb, "dev_gone", tip_a)
    check_deep("an entry the capture never heard of is ABSENT, naming how much it did read", 1,
               [ABSENT, "1 broadinstitute"], listed, "github.com/broadinstitute/gatk-sv/Nope", "main", tip_a)
    check_deep("a capture with no commitID anywhere is REFUSED, never CURRENT", REFUSE,
               ["REFUSED", "NO COMMIT TO COMPARE", "0 of 3"],
               [{"id": "#workflow/" + gb, "versions": [{"name": n} for n in ("main", "dev_probe", "v1")]}],
               gb, "main", tip_a, absent=[CURRENT, STALE, ABSENT])
    check_deep("no tip given is a refusal that prints the tip command", REFUSE,
               ["REFUSED", "refs/heads/dev_probe", "ls-remote"], listed, gb, "dev_probe", "")
    check_deep("a tip source that omits the branch refuses rather than reading absence", REFUSE,
               ["REFUSED", "refs/heads/dev_other"], listed, gb, "dev_probe", "", branch="dev_other")
    check_deep("an empty capture is refused in depth 2 too, and never called ABSENT", REFUSE,
               ["LISTING UNUSABLE"], [], gb, "main", tip_a, absent=[ABSENT])
    check_deep("a capture from another organization still cannot answer", REFUSE,
               ["LISTING DOES NOT COVER"], [wl("github.com/someother/x/Thing", "main", tip_a)], gb, "main", tip_a)
    check_deep("a tip that is not a hex sha is refused, not compared", REFUSE,
               ["NOT A COMMIT"], listed, gb, "dev_probe", "af68ae48 (HEAD -> dev_probe)")
    check_deep("a commit spelled `commit_id` on a `reference` row is still read", 1,
               [STALE], [{"workflowPath": gb, "reference": "dev_probe", "commit_id": tip_a}], gb, "dev_probe", tip_b)
    check_deep("a numeric entry id is not mistaken for an entry path", REFUSE,
               ["LISTING UNUSABLE"], [{"id": 4139, "name": "main", "commitID": tip_a}], gb, "main", tip_a,
               absent=[ABSENT, CURRENT])
    print(f"version_resolves: {ok} ok, {bad} failed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
