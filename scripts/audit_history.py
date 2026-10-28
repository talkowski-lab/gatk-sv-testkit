#!/usr/bin/env python3
"""Scan the git object store, not just the working set, for credential shapes and this machine's
coordinates, and report each hit by how far it can travel.

`make audit` grades `git ls-files`. A value scrubbed from a tracked file is still in history and
still public once pushed, so this tool grades every blob and every commit message:

    HEAD      current content of a tracked path        — `make audit` already grades this
    STAGED    in the index, not yet committed          — one `git commit` from shipping
    HISTORY   reachable from a ref, not at HEAD        — ships on a push
    DANGLING  in the object store, reachable from no ref — does NOT ship; advisory by default

Detectors are imported from `audit.py`. Every blob up to `MAX_BLOB_BYTES` is decoded (with
replacement, so binaries are graded too). Larger blobs are not read but are counted, named and gated
against `MAX_OVERSIZED_BLOBS`, and the run prints `PARTIAL:`.

Read-only (`git cat-file`, `rev-list`, `ls-tree`, `ls-files`). No network. Without the kit's config
it scans shapes only and says so.
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

# Per-blob read budget, and how many blobs may exceed it. Oversized blobs are not scanned but are
# reported, and exceeding the ceiling fails the run. Raising MAX_OVERSIZED_BLOBS means accepting
# that a blob may ship unaudited; do it in the commit that adds the blob.
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
    """(blobs to read, blobs over the read budget) as (oid, size), from one `--batch-check` pass."""
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
    """Announce the blobs this scan could not read; return the count to add to the failure tally.

    `ceiling` defaults to MAX_OVERSIZED_BLOBS read at call time, so the selftest can patch it.
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
    """(findings, oversized blobs that were not read)."""
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
        # Decode with replacement, as audit.py does: coordinates are ASCII, so binaries are graded too.
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
    # Commit messages: same detectors. `-z` gives one record per commit so multi-line bodies stay
    # paired with their sha.
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
    """Build throwaway repos and check the four exposure classes, the message scan, binary blobs and
    the oversized-blob ceiling. Each positive check has a control that must stay silent.
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

        # The PEM header is assembled from pieces so this file does not match the shape itself.
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

        # ---- a blob that is not valid UTF-8 is graded, not skipped ---------------------------------
        # Both blobs contain bytes that are never valid UTF-8. The one with the coordinate must be
        # found; the clean one must not.
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

        # ---- a blob over the read budget is announced, and the ceiling decides ---------------------
        # A separate, otherwise clean repo, so only the ceiling moves the verdict.
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
            """main() over the big-blob repo with MAX_OVERSIZED_BLOBS patched: (rc, output)."""
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
    # Partial reads get their own line and ceiling rather than hiding in the hit count.
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
    # Separate because the remedy differs: the blob was never read.
    if partial_fail:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
