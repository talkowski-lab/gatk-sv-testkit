#!/usr/bin/env python3
"""scripts/audit.py — last line of defence for a public repo, without shipping anyone's name.

What it checks
--------------
Two tiers, both graded, and the run always prints which ones were live:

  SHAPES     patterns that identify a *class* of private value -- a service-account address, a
             bare project number, a private key, a machine-local home path -- without naming
             anybody. These are shipped, and they are what CI grades, because CI has no profile
             and therefore no personal values to derive.

  PERSONAL   the values that are private because they are YOURS. There is no list of them in
             this repository: they are read back off the machine that is doing the publishing,
             from the resolved configuration (`kit/gsvtk-config show` minus what resolves the
             same way with no profile at all), plus anything you add in `audit.local.txt`.

Why not a hardcoded blocklist
----------------------------
The first version of this target shipped one person's identifiers -- a project id, workspace
names, initials, machine hostnames, a scratch directory. That is two bugs at once: a new user got
no protection for their own coordinates (the blocklist guarded someone else's name), and the
guard leaked the strings it was guarding, badly enough that the Makefile had to be exempted from
its own scan. So the one file that could never be checked was the one holding every identifier.

Removing the Makefile exemption is the point. With no forbidden string in any tracked file, every
tracked file can be scanned -- including this one, and including the Makefile.

What it deliberately does NOT do
--------------------------------
* No per-line exemptions. The ancestor of this target dropped any line containing `/Users/you/`,
  so one placeholder path hid every other identifier sharing that line; `make audit` then printed
  clean for a file holding four of them. `/Users/you/` keeps its exception *inside the home-path
  pattern only*, and the selftest plants a placeholder and a real leak on the same line to prove
  the exception still cannot blind the rest of the scan.
* No org-name patterns. The upstream project is public and the README links to it; a shape that
  fires on a legitimate citation is a shape that gets waived, then ignored.
* No history. `git ls-files` is the tree that would go public; a value already in a published
  commit stays in the remote's object store no matter what this prints.

Usage
-----
    scripts/audit.py [--root DIR] [--values FILE] [--patterns FILE] [--verbose] [--no-git]

`--values` (KEY=value lines) and `--patterns` (audit.local.txt format) exist so the offline
selftest can hand it a fake coordinate and prove it is detected. Exit: 0 clean, 1 hit(s), 2 could
not run. Python 3.9+, stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

# --------------------------------------------------------------------- shapes
# (label, regex, redact_the_matching_line). Redaction is for credential-shaped hits: echoing a
# real private key into a CI log to tell someone it leaked is its own leak.
SHAPES = [
    (
        "a GCP service-account address (digits@developer.gserviceaccount.com)",
        r"(?i)(?:[A-Za-z0-9._%+-]*[0-9][A-Za-z0-9._%+-]*@(?:developer|iam)\.gserviceaccount\.com"
        r"|[A-Za-z0-9._%+-]+@[0-9]{8,}\.(?:developer|iam)\.gserviceaccount\.com)",
        False,
    ),
    # A project NUMBER is only identifiable in context: GCP numbers are 8-16 digits, and a bare
    # `\b[0-9]{11,}\b` fired on a 40-zero placeholder sha, a JVM heap size in bytes, and the decimal
    # expansions in docs/archive (19 hits on the first run). Requiring the surrounding key keeps the
    # dangerous form (`--project 222…`, `projects/222…`) and stops pretending a long float is a leak.
    (
        "a GCP project number used as one (projects/<n>, --project <n>, project_number: <n>)",
        r"(?i)(?:projects?/[0-9]{8,}\b|(?:project[ _-]?(?:number|id)\D{0,4}|--project[ =:])\s*[0-9]{8,})",
        False,
    ),
    ("a FireCloud/Terra entity or submission handle (fc-<8 hex>)", r"\bfc-[0-9a-f]{8}\b", False),
    ("a private key block", r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", True),
    ("an AWS access key id", r"\bAKIA[0-9A-Z]{16}\b", True),
    ("a Google API key", r"\bAIza[0-9A-Za-z_-]{30,}\b", True),
    ("an OAuth access token (ya29.…)", r"\bya29\.[0-9A-Za-z_-]{25,}", True),
    ("a GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", True),
    ("an HTTP basic/bearer credential in-line", r"(?i)(?:authorization\s*:\s*bearer\s+|https?://[^/\s:]+:[^@\s]+@)[^\s]{12,}", True),
    # Machine-local paths. `/Users/you/`, `/home/user/` and `/home/example/` are the documented
    # placeholders (docs/config.md); the exception lives HERE, in this one pattern, and nowhere
    # else. A placeholder can therefore never suppress a different pattern's hit.
    (
        "an absolute home path (only /Users/you/, /home/user/, /home/example/ are allowed)",
        r"(?i)(?:(?<=^)|(?<=[\s\"'`(=:,]))(?:/Users/|/home/)(?!you/|user/|example/)[A-Za-z0-9._-]+/"
        r"|\b[a-z]:[\\/]+Users[\\/]+(?!public)",
        False,
    ),
    ("a macOS per-user temp path (/var/folders/…)", r"/(?:private/)?var/folders/[A-Za-z0-9_./-]{4,}", False),
    ("a throwaway venv under /tmp (/tmp/venv-…)", r"/tmp/venv-[A-Za-z0-9_-]+", False),
]

# Keys whose resolved value is a NAME OF SOMETHING THAT BELONGS TO YOU: a project, a workspace, a
# registry path, a bucket, a checkout on disk. That is the leak class that shipped by accident
# before. Tunables (zone, machine type, disk size, suffixes, batch name) are deliberately absent:
# they are settings, not coordinates, and matching them would flag ordinary prose.
COORDINATE_KEYS = [
    "PROJECT",
    "TERRA_NAMESPACE",
    "TERRA_WORKSPACE",
    "BASELINE_NAMESPACE",
    "BASELINE_WORKSPACE",
    "IMAGE_NAMESPACE",
    "IMAGE_REPO",
    "GATK_IMAGE_REPO",
    "GATK_SV_CHECKOUT",
    "GATK_CHECKOUT",
    "WORK",
    "GATK_SV_REPO_URL",
    "GATK_REPO_URL",
]

# Public vocabulary: infrastructure words and this repo's own names. A value that splits into one
# of these is not evidence of anybody's identity, and flagging it would train people to waive the
# audit. Anything added here must be public knowledge, never a name.
STOP_TOKENS = {
    "com", "org", "net", "io", "gcr", "gcr.io", "us.gcr.io", "eu.gcr.io", "us", "eu", "asia",
    "gatk", "gatk-sv", "gsvtk", "testkit", "gatk-sv-testkit", "dev", "tools", "docker",
    "github", "githubusercontent", "googleapis", "google", "storage", "usr", "opt", "srv",
    "tmp", "var", "home", "users", "repo", "repos", "src", "work", "data", "media", "mnt",
    "bin", "lib", "etc", "private", "projects", "datasets", "buckets", "images",
}

# Values that resolve identically with no profile at all are shipped defaults: public by
# construction, and never a personal coordinate.
DEFAULT_PROVENANCE = ("[default]", "[unset]", "(unset)")


def run(cmd, cwd=None, env=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, shell=False,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except Exception:
        return 2, ""


def publishable_files(root, use_git):
    """The set that would go public: `git ls-files`, or a find fallback with no repo.

    No exemptions, ever -- not for the Makefile, not for docs/archive/. An ignored file is not
    pushed; a force-added one becomes tracked and is therefore scanned.
    """
    if use_git:
        rc, out = run(["git", "ls-files", "-z"], cwd=root)
        if rc == 0:
            return [f for f in out.split("\0") if f]
    skip_dirs = {".git", ".venv", "work", "__pycache__", ".pytest_cache", ".mypy_cache"}
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs and not d.startswith(".venv")]
        for fn in filenames:
            if fn.endswith(".pyc") or fn in ("testkit.env", "testkit.local.env"):
                continue
            if fn.startswith("testkit.") and fn.endswith(".env"):
                continue
            out.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return sorted(out)


def show_values(root):
    """Resolved config as {KEY: (value, provenance)} by asking the resolver, never by parsing
    files with a regex: one resolver, one precedence chain (CONTRIBUTING.md).

    `show` prints `G SVTK_KEY  value  [provenance]` where the provenance itself contains spaces
    (`[derived from PROJECT]`), so it is read with one anchored pattern. Splitting on whitespace
    instead makes the KEY part of the "value" -- which then matches every doc that legitimately
    names the environment variable. Verified the hard way on the first run of this script.
    """
    cfg = os.path.join(root, "kit", "gsvtk-config")
    if not os.path.exists(cfg):
        return {}
    rc, out = run([cfg, "show"], cwd=root)
    if rc != 0:
        return {}
    row = re.compile(r"^\s*-?\s*GSVTK_(\S+)\s+(.*?)\s+(\[[^\]]*\]|\(unset\))\s*$")
    got = {}
    for line in out.splitlines():
        m = row.match(line)
        if not m:
            continue
        key, val, prov = m.group(1), m.group(2).strip(), m.group(3)
        if val and val != "(unset)":
            got[key] = (val, prov)
    return got


def default_values(root):
    """What `show` resolves with no profile and no environment: the shipped public surface."""
    cfg = os.path.join(root, "kit", "gsvtk-config")
    if not os.path.exists(cfg):
        return set()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": "/nonexistent",
           "GSVTK_CONFIG": "/dev/null"}
    rc, out = run([cfg, "show"], cwd=root, env=env)
    if rc != 0:
        return set()
    vals = set()
    for line in out.splitlines():
        m = re.match(r"^\s*-?\s*GSVTK_(\S+)\s+(.*?)\s+\[default\]\s*$", line)
        if m and m.group(2).strip():
            vals.add(m.group(2).strip().lower())
    return vals


def expand_value(key, prov, val, pub):
    """One coordinate value -> the literals it makes a pattern: the whole value, plus its segments.

    A COORDINATE (`us.gcr.io/project/namespace/repo`, a workspace name built with a convention) leaks
    as the WORD, not the whole name. `docs/archive/` shipped `runs_<seg>/` and `<seg>_status.py` for
    months and BOTH the old hand-tuned audit and the first version of this one passed, because the
    whole workspace name never appeared -- only its middle segment did. A guard that matches whole
    strings misses every derived form of a name; splitting is what closes that.

    NOT paths. A checkout or scratch path under a home directory splits into directory names that are
    ordinary vocabulary -- the first run of this script flagged `$HOME/IdeaProjects`, which the shipped
    skill lists as one of six common places to look for a checkout. The full path is the signal there
    (a doc quoting your exact checkout path IS a leak), and the absolute home-path SHAPE covers the
    general case.
    """
    out = [(key, prov, val)]
    if val.startswith(("/", "~", ".", "\\")):
        return out
    for seg in re.split(r"[-_./:@\s]+", val):
        seg = seg.strip()
        low = seg.lower()
        if len(seg) < 5 or low in STOP_TOKENS or low in pub:
            continue
        # A segment that is a substring of a SHIPPED default is public vocabulary (`broad`, `dsde`,
        # `methods`, `joint`...), not evidence about you. Without this test, splitting a project id
        # would flag every legitimate link to the upstream repository.
        if any(low in d for d in pub):
            continue
        if not re.search(r"[A-Za-z]", seg):
            continue
        out.append((key, prov, seg))
    return out


def personal_patterns(root, keys):
    """Coordinate values from this machine, minus anything that is a shipped default."""
    got = show_values(root)
    pub = default_values(root)
    pats, keys_used, skipped_default = [], [], 0
    for key in keys:
        if key not in got:
            continue
        val, prov = got[key]
        if val.lower() in pub:
            skipped_default += 1
            continue
        keys_used.append(key)
        pats.extend(expand_value(key, prov, val, pub))
    # de-dup, longest first so the label reported for a long value wins over its own segment
    seen, out = set(), []
    for key, prov, lit in sorted(pats, key=lambda t: -len(t[2])):
        if lit.lower() in seen:
            continue
        seen.add(lit.lower())
        out.append((key, prov, lit))
    return out, keys_used, skipped_default


def read_pattern_file(path):
    """audit.local.txt: one literal per line, `re:` for a regex, `waive <token>` to declare a
    derived value public. Returns (patterns, waivers)."""
    pats, waives = [], []
    if not path or not os.path.exists(path):
        return pats, waives
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("waive "):
                waives.append(line[6:].strip())
            elif line.startswith("re:"):
                pats.append(("audit.local.txt", "user pattern", line[3:].strip(), True))
            else:
                pats.append(("audit.local.txt", "user pattern", line, False))
    return pats, waives


def compile_literal(lit):
    # A literal is matched case-insensitively with NO word boundaries: the distinctive part of a
    # personal coordinate often only ever appears embedded in a longer name (a middle segment of a
    # workspace name, say). Splitting non-path values into segments, below, is what makes that
    # segment a pattern in its own right.
    return re.compile(re.escape(lit), re.I)


def main():
    ap = argparse.ArgumentParser(description="scan the publishable file set for private values")
    ap.add_argument("--root", default=".")
    ap.add_argument("--values", help="KEY=value file of personal coordinates (selftest hook)")
    ap.add_argument("--patterns", default="audit.local.txt", help="user-local extra patterns/waivers")
    ap.add_argument("--coordinate-keys", help="comma-separated keys to derive (default: built-in list)")
    ap.add_argument("--no-git", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--selftest", action="store_true", help="run the built-in shape controls")
    args = ap.parse_args()

    root = os.path.abspath(args.root)

    if args.selftest:
        return shape_controls()

    files = publishable_files(root, not args.no_git)
    if not files:
        print("audit: nothing to scan (no files, no git index) -- this is NOT a pass", file=sys.stderr)
        return 2

    extra, waives = read_pattern_file(os.path.join(root, args.patterns) if args.patterns else None)

    if args.values:
        personal, keys_used, skipped = [], [], 0
        with open(args.values, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if v:
                    # Same expansion as the derived path, so the offline selftest grades segment
                    # splitting too rather than only the whole-string case.
                    personal.extend(expand_value(k.strip(), "--values", v, set()))
    else:
        keys = COORDINATE_KEYS
        if args.coordinate_keys:
            keys = [k.strip().upper() for k in args.coordinate_keys.split(",") if k.strip()]
        personal, keys_used, skipped = personal_patterns(root, keys)

    waived = set(w.lower() for w in waives)
    personal = [p for p in personal if p[2].lower() not in waived]
    extra = [p for p in extra if p[2].lower() not in waived]
    personal_pats = [(k, prov, compile_literal(lit), lit, False) for k, prov, lit in personal]
    extra_pats = [(k, prov, (re.compile(lit, re.I | re.M) if regex else compile_literal(lit)), lit, regex)
                  for k, prov, lit, regex in extra]

    where = "git-tracked files" if not args.no_git else "find fallback (no git repo)"
    print("audit: %d files (%s)" % (len(files), where))
    print("audit:   %d shape pattern(s) (shipped, machine-independent)" % len(SHAPES))
    if args.values:
        print("audit:   %d personal value(s) from %s" % (len(personal_pats), args.values))
    else:
        src = "kit/gsvtk-config" + (" + %s" % args.patterns if extra_pats else "")
        print("audit:   %d personal value(s) derived from %s (keys: %s)"
              % (len(personal_pats), src, ", ".join(keys_used) or "none resolved"))
        if not personal_pats:
            print("audit:   NOTE no personal coordinates resolved -- defaults only, so this run")
            print("         graded SHAPES ONLY. Run `make audit` on the machine that will publish")
            print("         (CI has no profile, which is why it cannot grade these for you).")
        if skipped:
            print("audit:   (%d coordinate value(s) matched a shipped default and are public by"
                  " construction -- not counted)" % skipped)
    if waives:
        print("audit:   %d value(s) waived as public by %s: %s"
              % (len(waived), args.patterns, ", ".join(sorted(waived))))
    if args.verbose:
        for key, prov, lit in personal:
            print("         %-22s %-28s (%d chars)" % (key, prov, len(lit)))

    hits, hit_files = 0, set()
    # The user's own pattern file is not scanned: it necessarily contains the values it waives, and
    # it is gitignored, so it can never reach a public repo. Scanning it would turn every `waive` line
    # into a hit.
    skip = set()
    if args.patterns:
        skip.add(os.path.normpath(os.path.join(root, args.patterns)).replace(root + os.sep, ""))
        if os.path.exists(os.path.join(root, args.patterns)):
            print("audit:   (%s is excluded from its own scan: it holds the values it waives, and it"
                  " is gitignored)" % args.patterns)

    for rel in files:
        path = os.path.normpath(os.path.join(root, rel))
        if path in skip or rel in skip:
            continue
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "rb") as fh:
                blob = fh.read()
        except OSError:
            continue
        text = blob.decode("utf-8", "replace")
        binary = b"\0" in blob
        # One HIT per (file, line), not per pattern: a personal coordinate and two of its segments
        # matching one line is ONE thing to fix, and the tally is then a count of lines that must not
        # be published rather than a count of pattern combinations.
        reported = []
        for lineno, line in enumerate(text.splitlines(), 1):
            labels, redact = [], False
            for label, rx, is_red in SHAPES:
                if re.search(rx, line):
                    labels.append(label)
                    redact = redact or is_red
            for key, prov, cre, lit, _regex in personal_pats + extra_pats:
                if cre.search(line):
                    labels.append("personal: %s (%s) = %r" % (key, prov, lit))
            if labels:
                reported.append((labels, lineno, line, redact))
        if not reported and binary:
            labels = [label for label, rx, _r in SHAPES if re.search(rx, text, re.S)]
            for key, prov, cre, lit, _regex in personal_pats + extra_pats:
                if cre.search(text):
                    labels.append("personal: %s (%s) = %r" % (key, prov, lit))
                    break
            if labels:
                reported.append((labels, 0, "binary file matches; its content is not shown", True))
        if reported:
            hits += len(reported)
            hit_files.add(rel)
            for labels, lineno, line, redact in reported[:6]:
                shown = line.strip()
                if redact:
                    shown = shown[:18] + "… (redacted: a credential's own text does not belong in a log)"
                primary = labels[0] if len(labels) == 1 else \
                    "%s  (+%d other match%s on this line)" % (labels[0], len(labels) - 1,
                                                              "" if len(labels) == 2 else "es")
                print("  HIT   %-46s %s" % (rel, primary))
                print("          %s:%d  %s" % (rel, lineno, shown[:150]))
            if len(reported) > 6:
                print("          … %d more hit line(s) in this file" % (len(reported) - 6))

    # An ASCII tally line for scripts/selftest.sh to parse: asserting the EXACT number of hits, not
    # "exit code was nonzero", is what makes this a control rather than a smoke test.
    print("audit: tally: hits=%d files=%d" % (hits, len(hit_files)))
    if hits:
        print()
        print("audit: FAIL — %d hit(s) in %d file(s)." % (hits, len(hit_files)))
        print("       Replace the value with a <placeholder>; if it is something a user should")
        print("       supply, add a row to docs/config.md instead. If a *derived* value is")
        print("       genuinely public, waive it in %s (`waive <value>`) -- never by exempting a"
              % args.patterns)
        print("       file or a line.")
        return 1
    print("audit: clean — no shape match and none of this machine's own coordinates appear"
          " in the publishable set.")
    return 0


def shape_controls():
    """Positive controls for the shipped shapes: every pattern must fire on one synthetic line and
    stay silent on the placeholder form the docs legitimately use. A pattern that cannot fire is a
    pattern that is not being graded.

    Every synthetic secret here is BUILT FROM PIECES. A file that shipped a string its own shapes
    fire on would be flagged by the gate it implements -- and that self-referential trap is exactly
    why the Makefile used to be exempted from its own scan. `make audit` now scans this file like
    every other file, and the last control below asserts it stays clean, so the exemption can never
    quietly come back.
    """
    d = "0123456789"
    n10 = d + "00"                                   # 12 digits, not one literal run in this file
    n13 = d + "0123"
    gsac = "developer.gserv" + "iceaccount.com"
    iamsa = n13[:5] + n13[5:] + ".iam.gserv" + "iceaccount.com"
    keyblk = "-----BEGIN OPENSSH PR" + "IVATE KEY-----"
    akia = "AKIA" + "1234567890" + "ABCDEF"
    aiza = "AIzaSyA" + "1234567890abcdefghijklmnopqrstuvw"
    tok = "ya29." + "a0AfB_byC" + d + "abcdefghijklmnopqrstuvwxyz"
    ghp = "ghp_" + "1234567890abcdefghijklmno" + "ABCDEFGHIJ"
    basic = "https://user:hunter" + "two@example.invalid:8443/x"
    bearer = "Authoriza" + "tion: Bearer abcd" + "efghijklmnop" + d
    alice = "/home/" + "alice/work/gatk-sv/thing"
    alice_jdk = "JAVA_HOME=/home/" + "alice/jdk-17"
    win = "C:" + chr(92) + "Users" + chr(92) + "alice" + chr(92) + "x"
    macswe = "/private/var/fold" + "ers/x1/y2/T/tmpabcd"
    venv = "source /tmp/venv-" + "q7/bin/activate"
    fc = "fc-" + "2b4f9c1a"

    cases = [
        (n10 + "-compute@" + gsac, 0),
        ("svc@" + iamsa, 0),
        ("project number " + n10 + " ran", 1),
        ("--project=" + n10 + " --zone x", 1),
        ("projects/" + n10 + "12/disks/x", 1),
        (fc + " submission", 2),
        (keyblk, 3),
        (akia, 4),
        (aiza, 5),
        (tok, 6),
        (ghp, 7),
        (basic, 8),
        (bearer, 8),
        (alice, 9),
        (alice_jdk, 9),
        ("'" + win + "'", 9),
        (alice.upper(), 9),
        (win, 9),
        (macswe, 10),
        (venv, 11),
    ]
    ok = fail = 0
    for line, want in cases:
        hits = [i for i, (label, rx, _r) in enumerate(SHAPES) if re.search(rx, line)]
        if want in hits:
            ok += 1
        else:
            fail += 1
            print("  FAIL  no shape matched %r (wanted %s)" % (line[:40], SHAPES[want][0]))

    # Things the docs legitimately print: documented placeholders and public examples. Each of these
    # matching is a FALSE POSITIVE that would train people to waive the audit, so it is a failure.
    benign = [
        "<project-number>-compute@developer.gserv" + "iceaccount.com",
        "compute@developer.iam.gserv" + "iceaccount.com",
        "us.gcr.io/YOUR_PROJECT/YOUR_NAMESPACE/gatk-sv",
        "cd /Users/you/repos/gatk-sv && make test",
        "cd /home/user/repos/gatk-sv && make test",
        "150 GB PD-SSD, e2-standard-8, 1-3 hours",
        "22 minutes of VM time",
        'SHA="' + "0" * 40 + '"',
        "`Runtime.totalMemory()=" + d + "36`, heap cap 14 g",
        "`PEQ = 34.743558552260145` is exactly 8 x 10*log10(e)",
        "/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home/bin/java",
        'JAVA_HOME="/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home"',
    ]
    for line in benign:
        hits = [(i, SHAPES[i][0]) for i, (label, rx, _r) in enumerate(SHAPES) if re.search(rx, line)]
        if hits:
            fail += 1
            print("  FAIL  a documented placeholder/public example matched: %r -> %s"
                  % (line[:50], hits[0][1]))
        else:
            ok += 1

    # The control that keeps the exemption dead: this file must not contain a string its own shapes
    # fire on. Before the fixtures were assembled from pieces, the shipped control list matched 20
    # lines of itself -- which is the argument for scanning every file, and against exempting one.
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "audit.py")
    try:
        with open(here, encoding="utf-8", errors="replace") as fh:
            own = fh.read()
    except OSError:
        own = ""
    selfhits = [n for n, line in enumerate(own.splitlines(), 1)
                for _l, rx, _r in SHAPES if re.search(rx, line)]
    if selfhits:
        fail += 1
        print("  FAIL  scripts/audit.py holds %d line(s) its own shapes fire on (lines %s) -- build"
              " fixtures from pieces, do not exempt the file"
              % (len(selfhits), ", ".join(str(n) for n in selfhits[:6])))
    else:
        ok += 1

    print("shapes: %d controls ok, %d failed (%d benign lines must stay silent, and this file must"
          " not match itself)" % (ok, fail, len(benign)))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
