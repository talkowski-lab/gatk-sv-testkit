#!/usr/bin/env python3
"""Scan the git OBJECT STORE — not the working set — for credential shapes and this machine's
coordinates, and report each hit by how far it can travel.

Why this file exists: `make audit` grades `git ls-files`, which is the right scope for a working-tree
gate and the wrong scope for "what am I about to publish". `docs/handoff/003-...-pr966.md` was committed
at `f8d158a` carrying three absolute home paths, a dev bucket object path, a registry namespace with the
operator's name in it, a project id and a workspace bucket UUID. The repo was (and is) PUBLIC, so those
went out. A later commit scrubbed the file — and from then on `make audit` reported `hits=0` forever,
because the tracked copy is clean while the published blob is not. A scrub does not unpublish: the object
is still reachable from `main`, still served by `git clone`, and GitHub still offers the old version at
its commit URL.

So the unit of review here is the blob, and the unit of severity is its exposure:

    HEAD      current content of a tracked path        — `make audit` already grades this
    STAGED    in the index, not yet committed          — one `git commit` from shipping
    HISTORY   reachable from a ref, not at HEAD        — SHIPS ON A PUSH; invisible to the other audit
    DANGLING  in the object store, reachable from no ref — does NOT ship; advisory by default

Detectors are imported from `audit.py` rather than restated here: a second copy of a credential pattern
list is a second list that stops being true.

Read-only (`git cat-file`, `rev-list`, `ls-tree`, `ls-files`). No network, no credentials, no config file
needed for `--help`; the coordinate half needs the kit's config and degrades to shapes-only with a line
saying so.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit  # noqa: E402  credential SHAPES, COORDINATE_KEYS, personal_patterns, read_pattern_file

CLASSES = ("HEAD", "STAGED", "HISTORY", "DANGLING")
PUSHABLE = ("HEAD", "STAGED", "HISTORY")
MAX_CONTEXT = 44


def git(root, *args, binary=False):
    return subprocess.run(["git", "-C", root, *args], capture_output=True, check=True,
                          text=not binary).stdout


def classify(root):
    """blob id -> (exposure class, a path it is filed under)."""
    at_head, staged, reach = {}, {}, {}
    for line in git(root, "ls-tree", "-r", "HEAD").splitlines():
        meta, path = line.split("\t", 1)
        at_head[meta.split(" ")[2]] = path
    out = git(root, "ls-files", "-s").splitlines()
    for line in out:
        # <mode> <oid> <stage>\t<path>
        meta, _, path = line.partition("\t")
        bits = meta.split(" ")
        if len(bits) >= 3:
            staged[bits[1]] = path
    for line in git(root, "rev-list", "--objects", "--all").splitlines():
        bits = line.split(" ", 1)
        if len(bits) == 2:
            reach.setdefault(bits[0], bits[1])

    def one(oid):
        if oid in at_head:
            return "HEAD", at_head[oid]
        if oid in staged:
            return "STAGED", staged[oid]
        if oid in reach:
            return "HISTORY", reach[oid]
        return "DANGLING", reach.get(oid, "(no path: reachable from no ref)")
    return one


def blobs(root):
    for line in git(root, "cat-file", "--batch-all-objects", "--batch-check").splitlines():
        bits = line.split(" ")
        if len(bits) >= 3 and bits[1] == "blob" and int(bits[2]) <= 8_000_000:
            yield bits[0]


def mask(lit, text, start):
    """Never echo a whole hit: keep the head, say how long it was, and give bounded context."""
    shown = lit if len(lit) <= 10 else lit[:6] + "…" + lit[-3:]
    lo = max(0, start - 12)
    ctx = " ".join(text[lo:start + len(lit) + 18].split())[-MAX_CONTEXT:]
    return f"{shown} ({len(lit)} chars) in …{ctx}"


def scan(root, literals, shapes, patterns_file):
    one = classify(root)
    extra, _waive = audit.read_pattern_file(patterns_file) if patterns_file else ([], [])
    # (label, needle, is_regex): derived values are literals; the pattern file may carry either.
    needles = [(f"a coordinate from this machine ({key})", v, False) for key, _p, v in literals]
    needles += [(f"a coordinate from the local pattern file ({lit[:24]})", lit, is_re)
                for _k, _p, lit, is_re in extra]
    found = []
    for oid in blobs(root):
        body = subprocess.run(["git", "-C", root, "cat-file", "blob", oid], capture_output=True).stdout
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            continue
        cls, path = one(oid)
        for label, rx, *_ in shapes:
            for m in re.compile(rx, re.M).finditer(text):
                found.append((cls, path, text[:m.start()].count("\n") + 1, label,
                              mask(m.group(0), text, m.start())))
        for label, needle, is_re in needles:
            m = (re.search(needle, text, re.I) if is_re
                 else re.search(re.escape(needle), text, re.I))
            if not m:
                continue
            found.append((cls, path, text[:m.start()].count("\n") + 1, label,
                          mask(m.group(0), text, m.start())))
    # commit messages: same detectors, one synthetic "path". `-z` puts one record per commit, so a body
    # full of newlines cannot shift the (sha, body) pairing — splitting on a bare NUL between fields did
    # exactly that, and reported a finding against a commit named "checks:".
    for rec in git(root, "log", "--all", "-z", "--format=%H%n%B").split("\0"):
        if not rec.strip():
            continue
        sha, _, body = rec.partition("\n")
        sha = sha.strip()[:8]
        for label, rx, *_ in shapes:
            m = re.compile(rx, re.M).search(body)
            if m:
                found.append(("HISTORY", f"commit message {sha}", 1, label,
                              mask(m.group(0), body, m.start())))
        for label, needle, is_re in needles:
            m = (re.search(needle, body, re.I) if is_re
                 else re.search(re.escape(needle), body, re.I))
            if not m:
                continue
            found.append(("HISTORY", f"commit message {sha}", 1, label,
                          mask(m.group(0), body, m.start())))
    return found


def selftest() -> int:
    """Build throwaway repos and prove the four exposure classes, the message scan, and — the reason
    this tool exists — that a coordinate in a blob that is no longer at HEAD is still found.

    The positive control is deliberately asymmetric: the same string is planted in a file that is later
    deleted (must be found as HISTORY) and in a file with the blessed placeholder spelling (must not be
    found at all). A checker that flagged both, or neither, is not reading the object store.
    """
    import shutil
    import tempfile

    fails = []

    def ck(name, cond, extra=""):
        print(("  ok    " if cond else "  FAIL  ") + name + (f"  <{extra}>" if extra else ""))
        if not cond:
            fails.append(name)

    print("selftest: scripts/audit_history.py")
    d = tempfile.mkdtemp(prefix="audit-history-selftest-")
    repo = os.path.join(d, "r")
    os.makedirs(repo)
    g = lambda *a: subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c",
                                   "user.email=t@example.invalid", *a], capture_output=True, check=True)
    try:
        g("init", "-q", "-b", "main")
        pf = os.path.join(d, "audit.local.txt")
        with open(pf, "w") as fh:
            fh.write("PLANTED-COORD-9182\nre:PLANTED-[A-Z]{3}-\\d{4}\n")

        def write(name, body):
            with open(os.path.join(repo, name), "w") as fh:
                fh.write(body)

        write("gone.txt", "dev bucket gs://PLANTED-COORD-9182/x/results.tgz\n"
                          "-----BEGIN OPENSSH PRIVATE KEY-----\nnot really a key\n")
        write("at_head.txt", "live path /tmp/PLANTED-COORD-9182/still-tracked\n")
        write("kept.txt", "placeholder form: gs://<your-dev-bucket>/x\n")
        g("add", "-A")
        g("commit", "-q", "-m", "handoff with a coordinate in it")
        g("rm", "-q", "gone.txt")
        g("commit", "-q", "-m", "scrub the file (the object survives this commit)")
        write("staged.txt", "path /tmp/PLANTED-COORD-9182\n")
        g("add", "staged.txt")
        write("dangling.txt", "gs://fc-PLANTED-COORD-9182/\n")
        g("add", "dangling.txt")
        g("reset", "-q", "dangling.txt")

        hits = scan(repo, [], audit.SHAPES, pf)
        cls_of = {}
        for cls, path, ln, rule, detail in hits:
            cls_of.setdefault(cls, set()).add(os.path.basename(path))

        ck("HISTORY: a coordinate in a blob that is NOT at HEAD is found — the class the working-set "
           "audit cannot see (this is the whole reason this file exists)",
           "gone.txt" in cls_of.get("HISTORY", set()), str(sorted(cls_of)))
        ck("HEAD: a tracked file is graded too (no second tool needed for the easy half)",
           "at_head.txt" in cls_of.get("HEAD", set()), str(sorted(cls_of)))
        ck("STAGED: an uncommitted-but-indexed file is caught before it ships",
           "staged.txt" in cls_of.get("STAGED", set()), str(sorted(cls_of)))
        ck("DANGLING: an object reachable from no ref is named but is NOT a push",
           any(c == "DANGLING" and "fc-" in d for c, p, _l, _r, d in hits),
           str(sorted(cls_of)))
        ck("credential shape fires on a PEM header even with no coordinate configured",
           any("key" in r.lower() for _c, _p, _l, r, _d in hits))
        ck("CONTROL: the blessed placeholder spelling is not flagged — the checker is reading the store, "
           "not screaming at any path-looking string",
           not any("kept.txt" in p for _c, p, _l, _r, _d in hits))

        g("commit", "-q", "-m", "a subject line mentioning gs://PLANTED-COORD-9182 too")
        hits2 = scan(repo, [], audit.SHAPES, pf)
        ck("commit messages are scanned (a coordinate can ride in a subject line)",
           any("commit message" in p for _c, p, _l, _r, _d in hits2))

        coord = [h for h in hits2 if "a coordinate from" in h[3]]
        shape = [h for h in hits2 if "a coordinate from" not in h[3]]
        ck("coordinate findings and shape findings are distinguishable, so declaring one value public "
           "cannot mute the credential detectors", bool(coord) and bool(shape),
           f"{len(coord)} coord / {len(shape)} shape")
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print(("  FAIL  " + str(len(fails)) + " assertion(s) failed") if fails
          else "  all selftest assertions passed")
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="audit the object store and every commit message, not just the working set")
    ap.add_argument('--root', default=None, help='repo to audit (default: the repo containing this script)')
    ap.add_argument('--show', type=int, default=25, metavar='N',
                    help='findings to print (default 25; all are counted)')
    ap.add_argument('--dangling-fails', action='store_true',
                    help='treat DANGLING hits as failures too (default: advisory — they do not ship)')
    ap.add_argument('--publish', action='store_true',
                    help='fail on HISTORY as well as HEAD/STAGED: the mode to run before `git push`. '
                         'Without it the default fails only on what a commit has not shipped yet, which '
                         'is what a local gate can enforce without a waiver file for prose it cannot '
                         'un-publish.')
    ap.add_argument('--pattern-file', default=None, metavar='PATH',
                    help='extra literals/regexes (same format as audit.local.txt); also used by tests')
    ap.add_argument('--shapes-only', action='store_true',
                    help='skip this machine\'s coordinates (needs no kit config)')
    ap.add_argument('--selftest', action='store_true',
                    help='build fixture repos and prove the four exposure classes and the message scan')
    a = ap.parse_args()

    if a.selftest:
        return selftest()

    root = a.root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if subprocess.run(["git", "-C", root, "rev-parse", "--git-dir"], capture_output=True).returncode:
        raise SystemExit(f"audit_history: {root!r} is not a git repository")

    literals, keys_used, skipped = ([], [], 0)
    if not a.shapes_only:
        try:
            literals, keys_used, skipped = audit.personal_patterns(root, audit.COORDINATE_KEYS)
        except Exception as exc:  # config may be absent; shapes-only is still useful
            print(f"note: this machine's coordinates not derived ({exc}); scanning shapes only")
        if not literals:
            print("note: no non-default coordinates derived from the kit config; scanning shapes only")

    found = scan(root, literals, audit.SHAPES, a.pattern_file)
    tally = {c: 0 for c in CLASSES}
    for cls, *_ in found:
        tally[cls] = tally.get(cls, 0) + 1

    for cls, path, line, rule, detail in found[:max(0, a.show)]:
        print(f"  {cls:8s} {path}:{line}  {rule}\n           {detail}")
    if len(found) > max(0, a.show):
        print(f"  … {len(found) - a.show} more (raise --show; the counts below are complete)")

    fail = sum(tally.get(c, 0) for c in ("HEAD", "STAGED"))
    if a.publish:
        fail += tally.get("HISTORY", 0)
    if a.dangling_fails:
        fail += tally.get("DANGLING", 0)
    print(f"\naudit_history: {sum(tally.values())} finding(s) — " +
          ", ".join(f"{c} {tally.get(c, 0)}" for c in CLASSES))
    if keys_used:
        print(f"        coordinates tried: {len(keys_used)} setting(s) "
              f"({skipped} matched a shipped default and were skipped)")
    if tally.get("DANGLING"):
        print("        DANGLING objects are reachable from no ref, so a normal push does not send them;"
              "\n        `git gc --prune=now` drops them. Name --dangling-fails to gate on them anyway.")
    if tally.get("HISTORY") and not a.publish:
        print("        HISTORY hits are reported, not failed, in this mode. Run --publish (or"
              " `make audit-history`\n        with PUBLISH=1) before `git push`: a scrubbed file does not "
              "unpublish its blob.")
    if fail:
        print("        HISTORY hits are the ones the working-set audit cannot see: a scrubbed file does "
              "not\n        unpublish its blob. Remedies: rewrite history (filter-repo --replace-text) or "
              "accept\n        that the value is public and rotate anything that was a credential.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
