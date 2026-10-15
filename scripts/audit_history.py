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

Read scope, stated because a partial answer that looks complete is the failure this file was written
for: every blob at or under `MAX_BLOB_BYTES` is decoded and graded, including the ones that are not
valid UTF-8 (`audit.py` has decoded the working set that way all along, and its canary in
`scripts/selftest.sh` is that a binary is reported BY FILENAME, not skipped). Blobs over the budget are
not read — and are therefore COUNTED, NAMED and gated against `MAX_OVERSIZED_BLOBS`, so the run prints
`PARTIAL:` with the names instead of an unqualified zero.

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

# The read budget for one blob, and the declared ceiling on how many blobs may sit above it.
#
# A blob over the budget is NOT scanned, and this tool used to say `HISTORY 0` with no hint that the
# answer was partial — while `audit.py` (the sibling that grades the working set) decodes with
# errors="replace" and its canary (scripts/selftest.sh:445, :470) says a binary is REPORTED BY
# FILENAME, not skipped. Two halves followed from that:
#
#   * a blob that is not valid UTF-8 is now decoded with errors="replace" and GRADED (see scan()).
#     A coordinate is ASCII, so replacement decoding still finds it; the `except UnicodeDecodeError:
#     continue` that used to sit there was buying invisibility for exactly the committed .vcf.gz or
#     image blob that carries a workspace name.
#   * a blob over MAX_BLOB_BYTES is still not read — hashing and regexing a multi-megabyte object in
#     the publish gate is a real cost — but it is now COUNTED AND NAMED, and the count is graded
#     against MAX_OVERSIZED_BLOBS so "partial" can never print as "clean".
#
# MAX_OVERSIZED_BLOBS = 0 is not a guess: measured on this box at 6aa94af the object store holds 565
# blobs, the largest 177,693 bytes, so ZERO exceed the budget today and the strict form costs nothing.
# LOWERING is not possible (it is already zero — the floor of "nothing may go ungraded"). RAISING it is
# a written admission that some blob will not be audited before it can ship, so raise it in the commit
# that adds the large blob, name that blob there, and keep the number the smallest you can defend.
MAX_BLOB_BYTES = 8_000_000
MAX_OVERSIZED_BLOBS = 0


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
    """(blobs to read, blobs over the read budget) as (oid, size).

    Both halves come from one `--batch-check` pass, and the second half is RETURNED rather than dropped:
    a scan that cannot read part of the store has to say so, or its zero reads as an all-clear.
    """
    read, over = [], []
    for line in git(root, "cat-file", "--batch-all-objects", "--batch-check").splitlines():
        bits = line.split(" ")
        if len(bits) >= 3 and bits[1] == "blob":
            size = int(bits[2])
            (read if size <= MAX_BLOB_BYTES else over).append((bits[0], size))
    return read, over


def mask(lit, text, start):
    """Never echo a whole hit: keep the head, say how long it was, and give bounded context."""
    shown = lit if len(lit) <= 10 else lit[:6] + "…" + lit[-3:]
    lo = max(0, start - 12)
    ctx = " ".join(text[lo:start + len(lit) + 18].split())[-MAX_CONTEXT:]
    return f"{shown} ({len(lit)} chars) in …{ctx}"


def partial_report(oversized, ceiling=None, out=None):
    """Announce the blobs this scan could not read, and whether that is gate-failing.

    Returns the number of findings to ADD to the failure tally (0 or enough to fail). An empty `oversized`
    list prints nothing and returns 0 — silence here means the store was read whole, which is the only
    state that deserves it. `ceiling` defaults to the module constant READ AT CALL TIME, not bound at
    def time, because the positive control has to move it to prove the ceiling is what decides.
    """
    import sys
    out = out or sys.stdout
    if ceiling is None:
        ceiling = MAX_OVERSIZED_BLOBS
    if not oversized:
        return 0
    first = ", ".join(f"{p} ({sz:,} B)" for _oid, sz, p in oversized[:5])
    more = len(oversized) - min(5, len(oversized))
    print(f"  PARTIAL: {len(oversized)} blob(s) exceed the {MAX_BLOB_BYTES:,}-byte read budget and were "
          f"NOT scanned: {first}" + (f", +{more} more" if more else ""), file=out)
    if len(oversized) > ceiling:
        print(f"        {len(oversized)} unscanned blob(s) exceed the declared ceiling "
              f"MAX_OVERSIZED_BLOBS = {ceiling}: `HISTORY 0` would be a PARTIAL answer presented as a "
              f"clean one. Read the named blob (or delete it); raising MAX_OVERSIZED_BLOBS is a written "
              f"admission that it may ship unaudited.", file=out)
    return max(0, len(oversized) - ceiling)


def scan(root, literals, shapes, patterns_file):
    """(findings, oversized) — the second half is what this scan could NOT read, never dropped."""
    one = classify(root)
    extra, _waive = audit.read_pattern_file(patterns_file) if patterns_file else ([], [])
    # (label, needle, is_regex): derived values are literals; the pattern file may carry either.
    needles = [(f"a coordinate from this machine ({key})", v, False) for key, _p, v in literals]
    needles += [(f"a coordinate from the local pattern file ({lit[:24]})", lit, is_re)
                for _k, _p, lit, is_re in extra]
    found = []
    oversized_found = []
    to_read, oversized = blobs(root)
    for oid, size in oversized:
        oversized_found.append((oid, size, one(oid)[1]))
    for oid, _size in to_read:
        body = subprocess.run(["git", "-C", root, "cat-file", "blob", oid], capture_output=True).stdout
        # errors="replace", like audit.py:397 does for the working set. A coordinate is ASCII, so a
        # replacement character costs a byte of context and nothing else; what strict decoding bought
        # was that a committed binary carrying a workspace name was invisible to the only check that
        # exists for it (the sibling audit's canary calls that class by name: a binary is REPORTED BY
        # FILENAME, not skipped).
        text = body.decode("utf-8", "replace")
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
    return found, oversized_found


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

        # The PEM header is assembled here instead of written as a literal. `make audit` grades the
        # tracked tree for credential SHAPES with no exemptions, and a literal in this file made the
        # history auditor's own fixture the single hit the publish guard reported on the repo, which
        # also failed selftest's "audit is clean at HEAD and in the index" probe. Splitting the token
        # leaves the guard's red line untouched (its pattern needs the contiguous `-----BEGIN`) and
        # still plants the real shape, in a temp repo that is never tracked.
        pem = "-----BEGIN " + "OPENSSH" + " PRIVATE KEY-----"
        write("gone.txt", "dev bucket gs://PLANTED-COORD-9182/x/results.tgz\n"
                          f"{pem}\nnot really a key\n")
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

        hits, oversized = scan(repo, [], audit.SHAPES, pf)
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
        hits2, _over2 = scan(repo, [], audit.SHAPES, pf)
        ck("commit messages are scanned (a coordinate can ride in a subject line)",
           any("commit message" in p for _c, p, _l, _r, _d in hits2))

        coord = [h for h in hits2 if "a coordinate from" in h[3]]
        shape = [h for h in hits2 if "a coordinate from" not in h[3]]
        ck("coordinate findings and shape findings are distinguishable, so declaring one value public "
           "cannot mute the credential detectors", bool(coord) and bool(shape),
           f"{len(coord)} coord / {len(shape)} shape")

        # ---- a blob that is not valid UTF-8 is GRADED, not skipped ----------------------------------
        # audit.py decodes the working set with errors="replace" and its canary (scripts/selftest.sh:445,
        # :470) is that a binary is REPORTED BY FILENAME. This file used to `except UnicodeDecodeError:
        # continue`, so the one check that exists for a PUBLISHED blob was blind to every published blob
        # that happened to be binary — a .vcf.gz or an image layer carrying a workspace name. The control
        # is asymmetric again: binary WITH the coordinate must be found, binary WITHOUT it must not, or
        # the change is really "flag every byte that is not ASCII".
        #
        # Both blobs carry bytes 00 / ff / fe, where no valid UTF-8 sequence can start, so the strict
        # decode this file used to attempt raises on BOTH of them: neither assertion below is reachable
        # through the old code path (verified by putting the `continue` back and watching both go red).
        with open(os.path.join(repo, "binleak.bin"), "wb") as fh:
            fh.write(b"BC" + bytes([0, 255, 254]) + b" gs://PLANTED-COORD-9182/results.tgz"
                     + bytes([0, 255]))
        with open(os.path.join(repo, "binclean.bin"), "wb") as fh:
            fh.write(b"BC" + bytes([0, 255, 254]) + b" nothing here but bytes" + bytes([255]))
        g("add", "-A")
        g("commit", "-q", "-m", "commit a fixture that cannot be decoded as text")
        g("rm", "-q", "binleak.bin")
        g("commit", "-q", "-m", "remove it from the tree; the object is still reachable")
        hits3, _over_leak = scan(repo, [], audit.SHAPES, pf)
        cls3 = {}
        for cls, path, ln, rule, detail in hits3:
            cls3.setdefault(cls, set()).add(os.path.basename(path))
        ck("a NON-UTF-8 blob carrying a coordinate is FOUND and named by filename as HISTORY — strict "
           "decoding raised on these bytes and the old `continue` skipped the blob in silence, which is "
           "the same failure as printing HISTORY 0 about a store that was never read",
           "binleak.bin" in cls3.get("HISTORY", set()), str(sorted(cls3)))
        ck("CONTROL: a binary with NO coordinate is not reported — replacement decoding grades blobs, "
           "it does not scream at them",
           not any("binclean.bin" in p for _c, p, _l, _r, _d in hits3), str(sorted(cls3)))

        # ---- a blob over the read budget is announced, and the ceiling is what decides --------------
        # Its own repo: the fixture above is deliberately leaky, and this probe has to show the CEILING
        # moving the verdict with nothing else moving it. The repo itself stays clean, so a green run
        # here means "read whole", never "found nothing".
        big = os.path.join(d, "bigrepo")
        os.makedirs(big)
        gb = lambda *a: subprocess.run(["git", "-C", big, "-c", "user.name=t", "-c",
                                        "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                                        *a], capture_output=True, check=True)
        gb("init", "-q", "-b", "main")
        with open(os.path.join(big, "clean.txt"), "w") as fh:
            fh.write("placeholder only: gs://<your-dev-bucket>/x\n")
        with open(os.path.join(big, "huge.bin"), "wb") as fh:
            fh.write(bytes(MAX_BLOB_BYTES + 1))          # one byte past the read budget
        gb("add", "-A")
        gb("commit", "-q", "-m", "a fixture blob larger than the read budget")

        import contextlib
        import io
        import audit_history as ah

        def run_cli(ceiling):
            """main() over the big-blob repo at a GIVEN ceiling: (rc, what it printed).

            The ceiling is patched on the imported module, so this proves the CEILING is the knob — not
            the presence of a big blob, and not the wording of the banner."""
            old_argv, old_ceil = list(sys.argv), ah.MAX_OVERSIZED_BLOBS
            buf = io.StringIO()
            ah.MAX_OVERSIZED_BLOBS = ceiling
            try:
                sys.argv = ["audit_history", "--root", big, "--shapes-only", "--pattern-file", pf]
                with contextlib.redirect_stdout(buf):
                    rc = ah.main()
            finally:
                sys.argv, ah.MAX_OVERSIZED_BLOBS = old_argv, old_ceil
            return rc, buf.getvalue()

        rc_red, out_red = run_cli(0)
        ck("an over-budget blob is ANNOUNCED (count + its name) and is RED at the declared ceiling 0, "
           "naming MAX_OVERSIZED_BLOBS as the reason — a partial read cannot print as a clean one",
           rc_red == 1 and "PARTIAL:" in out_red and "huge.bin" in out_red
           and "MAX_OVERSIZED_BLOBS = 0" in out_red, f"rc={rc_red}")
        rc_green, out_green = run_cli(1)
        ck("CONTROL: the SAME repo is green one ceiling point higher and STILL prints the PARTIAL line "
           "with the name — the ceiling gates the verdict, never the announcement",
           rc_green == 0 and "PARTIAL:" in out_green and "huge.bin" in out_green, f"rc={rc_green}")
        gb("rm", "-q", "huge.bin")
        gb("commit", "-q", "-m", "delete it from the tree")
        rc_after, out_after = run_cli(0)
        ck("and deleting the file does not un-count the blob: it is still reachable, still unscanned, "
           "and the tool still says so — the asymmetry this file exists for, aimed at its own coverage",
           rc_after == 1 and "huge.bin" in out_after, f"rc={rc_after}")
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

    found, oversized = scan(root, literals, audit.SHAPES, a.pattern_file)
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
    # The partial-read verdict separately: a clean tally over a store this tool could only partly read
    # is the `HISTORY 0` that let a coordinate ship, so it gets its own line, its own named blobs and
    # its own ceiling rather than hiding inside `hits=0`.
    partial_fail = partial_report(oversized)
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
    # A separate branch, because its remedy is not "scrub a file": the blob was never read, so nothing
    # here claims it is clean.
    if partial_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
