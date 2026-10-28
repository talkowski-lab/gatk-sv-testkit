#!/usr/bin/env python3
"""scripts/audit.py — scan the publishable file set for private values.

Two tiers, and the run prints which were live:

  SHAPES     patterns for a class of private value (service-account address, project number,
             private key, machine-local home path). Machine-independent, so CI can grade them.

  PERSONAL   this machine's own coordinates, derived from the resolved configuration
             (`kit/gsvtk-config show` minus what resolves the same with no profile), from config
             values in git history, and from `audit.local.txt`. Nothing personal is stored in the
             repo, so every tracked file, including this one and the Makefile, is scanned.

There are no per-line exemptions; the `/Users/you/` placeholder is allowed only inside the
home-path pattern. Org names are not flagged (upstream is public). Already-published history is
`scripts/audit_history.py`'s job.

Usage
-----
    scripts/audit.py [--root DIR] [--values FILE] [--patterns FILE] [--verbose] [--no-git]

`--values` (KEY=value lines) and `--patterns` (audit.local.txt format) let the offline selftest
plant a fake coordinate. Exit: 0 clean, 1 hit(s), 2 could not run. Python 3.9+, stdlib only.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile

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
    # Project numbers only count in context (projects/<n>, --project <n>): a bare long number
    # also matches placeholder shas and byte counts.
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
    # Machine-local paths. The documented placeholders (/Users/you/, /home/user/, /home/example/)
    # are excepted here and only here, so a placeholder cannot hide another pattern's hit.
    (
        "an absolute home path (only /Users/you/, /home/user/, /home/example/ are allowed)",
        r"(?i)(?:(?<=^)|(?<=[\s\"'`(=:,]))(?:/Users/|/home/)(?!you/|user/|example/)[A-Za-z0-9._-]+/"
        r"|\b[a-z]:[\\/]+Users[\\/]+(?!public)",
        False,
    ),
    ("a macOS per-user temp path (/var/folders/…)", r"/(?:private/)?var/folders/[A-Za-z0-9_./-]{4,}", False),
    ("a throwaway venv under /tmp (/tmp/venv-…)", r"/tmp/venv-[A-Za-z0-9_-]+", False),
]

# Keys whose value names something of yours (project, workspace, registry, bucket, checkout).
# Tunables such as zone or machine type are settings, not coordinates, and are left out.
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

# Public vocabulary: infrastructure words and this repo's own names. Never add a personal name.
STOP_TOKENS = {
    "com", "org", "net", "io", "gcr", "gcr.io", "us.gcr.io", "eu.gcr.io", "us", "eu", "asia",
    "gatk", "gatk-sv", "gsvtk", "testkit", "gatk-sv-testkit", "dev", "tools", "docker",
    "github", "githubusercontent", "googleapis", "google", "storage", "usr", "opt", "srv",
    "tmp", "var", "home", "users", "repo", "repos", "src", "work", "data", "media", "mnt",
    "bin", "lib", "etc", "private", "projects", "datasets", "buckets", "images",
    # Upstream flavor name (profiles/single_sample.json, GATKSVPipelineSingleSample.wdl); without
    # it segment splitting flags every mention of the single-sample flavor.
    "single",
}

# Values that resolve identically with no profile are shipped defaults, so public.
DEFAULT_PROVENANCE = ("[default]", "[unset]", "(unset)")


def run(cmd, cwd=None, env=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, shell=False,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=60)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except Exception:
        return 2, ""


def publishable_files(root, use_git):
    """The set that would go public: `git ls-files`, or a find fallback with no repo. No exemptions."""
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
    """Resolved config as {KEY: (value, provenance)}, asked of the resolver.

    The provenance can contain spaces (`[derived from PROJECT]`), so each row is read with one
    anchored pattern rather than split on whitespace.
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
    # Empty throwaway HOME, so no user profile is read and nothing is cached into the checkout.
    with tempfile.TemporaryDirectory(prefix="gsvtk-audit-home-") as home:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": home,
               "GSVTK_CONFIG": "/dev/null", "PYTHONDONTWRITEBYTECODE": "1"}
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
    """One coordinate value -> the literals to search for: the whole value plus its segments.

    Names often leak as a single segment (e.g. the middle of a workspace name), so non-path values
    are split. Paths are not: their directory names are ordinary words, and the full path is the
    signal (the home-path shape covers the general case).
    """
    out = [(key, prov, val)]
    if val.startswith(("/", "~", ".", "\\")):
        return out
    for seg in re.split(r"[-_./:@\s]+", val):
        seg = seg.strip()
        low = seg.lower()
        if len(seg) < 5 or low in STOP_TOKENS or low in pub:
            continue
        # A substring of a shipped default is public vocabulary, not evidence about you.
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
    # De-dup, longest first so a long value's label wins over its own segment.
    seen, out = set(), []
    for key, prov, lit in sorted(pats, key=lambda t: -len(t[2])):
        if lit.lower() in seen:
            continue
        seen.add(lit.lower())
        out.append((key, prov, lit))
    return out, keys_used, skipped_default


# ------------------------------------------------- the history-derived personal tier
#
# The resolver tier only sees today's profile. This tier reads coordinate values from every
# committed version of the config-bearing files below, so a value from a profile you stopped using
# is still caught if it appears in today's files. (audit_history.py is the converse: old blobs
# against today's values.)
#
#   * testkit.env, testkit.local.env — the profile files; gitignored, so normally no history.
#   * testkit.env.example — the tracked template; its placeholders are excluded as shipped at HEAD.
#   * profiles/*.json — module profiles.
#   * scripts/selftest.d/golden/*.json — captured Terra method-config bodies.
#
# A GSVTK_CONFIG= override can point at any file, which this list cannot cover (see
# docs/static-checks.md).
CONFIG_PATHS = [
    "testkit.env",
    "testkit.local.env",
    "testkit.env.example",
    "profiles/*.json",
    "scripts/selftest.d/golden/*.json",
]

# JSON keys that hold a coordinate in this repo's config bodies. `inputs` is not walked: its values
# are bindings (`workspace.<attr>`), not coordinates.
CONFIG_JSON_KEYS = ("workspace", "namespace", "config_namespace")

PROFILE_ROW = re.compile(r"^\s*(?:export\s+)?GSVTK_([A-Z0-9_]+)\s*=\s*(.*?)\s*$")


def config_bodies(root, paths):
    """[(rev, path, text)] for every committed version of a config-bearing path.

    Read with `git show <rev>:<path>`, so the working tree and index are untouched. A deleting
    commit fails to show and is skipped; the content is read from the commit that added it.
    """
    rc, out = run(["git", "log", "--all", "--format=CA%H", "--name-only", "--"] + list(paths),
                  cwd=root)
    if rc != 0:
        return None
    bodies, rev, seen = [], None, set()
    for line in out.splitlines():
        if line.startswith("CA") and len(line) > 8:
            rev = line[2:].strip()
            continue
        if not rev or not line.strip():
            continue
        path = line.strip()
        key = (rev, path)
        if key in seen:
            continue
        seen.add(key)
        rc2, text = run(["git", "show", "%s:%s" % (rev, path)], cwd=root)
        if rc2 == 0:
            bodies.append((rev, path, text))
    return bodies


def config_values(text, path):
    """Coordinate (key, value) pairs in one config body: `KEY=value` profile lines or JSON.

    Keys are filtered as in COORDINATE_KEYS, so settings like ZONE are ignored.
    """
    out = []
    if path.endswith(".json"):
        try:
            import json
            root_obj = json.loads(text)
        except Exception:
            return out

        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if isinstance(v, str) and k.lower().replace("-", "_") in CONFIG_JSON_KEYS:
                        if v.strip():
                            out.append((k.upper(), v.strip()))
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(root_obj)
        return out
    for line in text.splitlines():
        m = PROFILE_ROW.match(line)
        if m and m.group(1) in COORDINATE_KEYS and m.group(2):
            out.append((m.group(1), m.group(2)))
    return out


def shipped_config_values(root, paths):
    """Lowered values the tracked config carries at HEAD, read from the commit.

    These are documented placeholders and public; a value in history but not at HEAD is the one
    that could have leaked from an old profile.
    """
    rc, out = run(["git", "ls-tree", "-r", "--name-only", "HEAD", "--"] + list(paths), cwd=root)
    if rc != 0:
        return set()
    vals = set()
    for path in [p for p in out.splitlines() if p.strip()]:
        rc2, text = run(["git", "show", "HEAD:%s" % path], cwd=root)
        if rc2 == 0:
            for _key, val in config_values(text, path):
                vals.add(val.lower())
    return vals


def history_personal_patterns(root, today_lits, pub, paths=CONFIG_PATHS):
    """(patterns, counted_values, public_values, sources, err) for the history tier.

    `err` is reported with the tally so a skipped tier is never mistaken for a clean one.
    """
    bodies = config_bodies(root, paths)
    if bodies is None:
        return [], [], [], [], "git log over %s failed" % ", ".join(paths)
    shipped = shipped_config_values(root, paths)
    cands, public, sources = {}, 0, []
    for rev, path, text in bodies:
        if path not in sources:
            sources.append(path)
        for key, val in config_values(text, path):
            low = val.lower()
            if low in shipped or low in pub:
                public += 1          # documented placeholder or shipped default: public by construction
                continue
            # First sighting wins; the rev is only used for reporting.
            cands.setdefault(low, (rev, key, val))
    pats = []
    for low, (rev, key, val) in cands.items():
        prov = "history %s" % rev[:8]
        for _k, _p, lit in expand_value(key, prov, val, pub):
            low_lit = lit.lower()
            if low_lit in pub or low_lit in shipped or low_lit in today_lits:
                continue
            pats.append((key, prov, lit))
    seen, out = set(), []
    for key, prov, lit in sorted(pats, key=lambda t: -len(t[2])):
        if lit.lower() in seen:
            continue
        seen.add(lit.lower())
        out.append((key, prov, lit))
    counted = sorted({v for (_r, _k, v) in cands.values()})
    return out, counted, public, sources, ""


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
    # Case-insensitive, no word boundaries: a coordinate often appears inside a longer name.
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
        return 1 if (shape_controls() or history_controls()) else 0

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
                    # Same expansion as the derived path, so the selftest covers segment splitting.
                    personal.extend(expand_value(k.strip(), "--values", v, set()))
    else:
        keys = COORDINATE_KEYS
        if args.coordinate_keys:
            keys = [k.strip().upper() for k in args.coordinate_keys.split(",") if k.strip()]
        personal, keys_used, skipped = personal_patterns(root, keys)

    # The history tier. Off with --values (which replaces the derived set) and --no-git; both are
    # announced rather than reported as a pass.
    hist, hist_values, hist_public, hist_sources, hist_err = [], [], 0, [], ""
    if not args.values and not args.no_git:
        today = set(l.lower() for _k, _p, l in personal)
        hist, hist_values, hist_public, hist_sources, hist_err = \
            history_personal_patterns(root, today, default_values(root))

    waived = set(w.lower() for w in waives)
    personal = [p for p in personal if p[2].lower() not in waived]
    hist = [p for p in hist if p[2].lower() not in waived]
    extra = [p for p in extra if p[2].lower() not in waived]
    personal_pats = [(k, prov, compile_literal(lit), lit, False) for k, prov, lit in personal]
    personal_pats += [(k, prov, compile_literal(lit), lit, False) for k, prov, lit in hist]
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
              % (len(personal), src, ", ".join(keys_used) or "none resolved"))
        if not personal:
            print("audit:   NOTE no personal coordinates resolved -- defaults only, so this run")
            print("         graded SHAPES ONLY. Run `make audit` on the machine that will publish")
            print("         (CI has no profile, which is why it cannot grade these for you).")
        if skipped:
            print("audit:   (%d coordinate value(s) matched a shipped default and are public by"
                  " construction -- not counted)" % skipped)
        # The history tier is reported on its own line.
        if args.no_git:
            print("audit:   HISTORY tier SKIPPED (--no-git): the personal tier is today's profile")
            print("         only, so a coordinate this machine no longer resolves was NOT graded.")
        elif hist_err:
            print("audit:   HISTORY tier COULD NOT RUN (%s) -- the personal tier is today's profile"
                  " only," % hist_err)
            print("         so a coordinate this machine no longer resolves was NOT graded.")
        else:
            print("audit:   %d personal value(s) derived from git HISTORY -- %d historical config"
                  " value(s) HEAD does not resolve (not in the profile today), read from %s"
                  % (len(hist), len(hist_values),
                     ", ".join(hist_sources) or "no config path with history"))
            if not hist_values:
                print("audit:   NOTE the history derivation yielded NOTHING on this repo, so this run")
                print("         graded no historical coordinate. That is the vacuous case, named here")
                print("         rather than counted as a pass: the tier is graded on a planted fixture")
                print("         (`scripts/audit.py --selftest`), not by this tree.")
            if hist_public:
                print("audit:   (%d historical value(s) are shipped defaults or still carried by the"
                      " tracked" % hist_public)
                print("         config at HEAD: public by construction, not counted as someone's")
                print("         coordinate)")
        if args.verbose:
            for key, prov, lit in hist:
                print("         %-22s %-28s %r (history)" % (key, prov, lit))
    if waives:
        print("audit:   %d value(s) waived as public by %s: %s"
              % (len(waived), args.patterns, ", ".join(sorted(waived))))
    if args.verbose:
        for key, prov, lit in personal:
            print("         %-22s %-28s (%d chars)" % (key, prov, len(lit)))

    hits, hit_files = 0, set()
    # Skip the user's own pattern file: it contains the values it waives, and it is gitignored.
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
        # One hit per (file, line), however many patterns match it.
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

    # Tally line parsed by scripts/selftest.sh, which asserts the exact hit count.
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
    """Positive controls for SHAPES: each pattern must fire on a synthetic line and stay silent on
    the placeholder forms the docs use.

    Synthetic secrets are built from pieces so this file never matches its own shapes; the last
    control asserts that, since this file is scanned like any other.
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

    # Documented placeholders and public examples: a match here is a false positive.
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

    # This file must not contain a string its own shapes fire on.
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


def history_controls():
    """Positive controls for the history-derived tier, on a throwaway git repo.

    An earlier commit carries a config value HEAD no longer has, and a file at HEAD names it: the
    audit must fail. Also checked: a segment-only leak fires, a placeholder still carried at HEAD
    does not, the cured file passes, and both the no-history and --no-git cases are announced.
    """
    import contextlib
    import io
    import shutil
    import tempfile

    fails = []
    ok = [0]

    def ck(name, cond, extra=""):
        print(("  ok    " if cond else "  FAIL  ") + name + ("  <%s>" % extra if extra else ""))
        if cond:
            ok[0] += 1
        else:
            fails.append(name)

    print("selftest: scripts/audit.py history-derived personal tier")
    # Synthetic workspace name, built from pieces; only its coined segment survives stop tokens.
    SEG = "zq91kf" + "x"
    LEAK = "GATK-SV-sv-" + SEG
    PLACEHOLDER = "your-" + "dev-project"          # the documented placeholder spelling

    d = tempfile.mkdtemp(prefix="audit-history-tier-selftest-")
    repo = os.path.join(d, "r")
    os.makedirs(os.path.join(repo, "docs"))
    g = lambda *a: subprocess.run(["git", "-C", repo, "-c", "user.name=t",
                                   "-c", "user.email=t@example.invalid",
                                   "-c", "core.hooksPath=/dev/null", *a],
                                  capture_output=True, check=True)

    def write(name, body):
        path = os.path.join(repo, name)
        if os.path.dirname(name) != ".":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)

    def run_cli(extra_args):
        """main() as the CLI runs it, over the fixture repo: (rc, what it printed)."""
        old_argv, buf = list(sys.argv), io.StringIO()
        try:
            sys.argv = ["audit.py", "--root", repo] + extra_args
            with contextlib.redirect_stdout(buf):
                rc = main()
        finally:
            sys.argv = old_argv
        return rc, buf.getvalue()

    try:
        g("init", "-q", "-b", "main")
        # 1. A commit whose profile names a workspace.
        write("testkit.env.example",
              "GSVTK_PROJECT=%s\nGSVTK_TERRA_WORKSPACE=%s\n" % (PLACEHOLDER, LEAK))
        g("add", "-A")
        g("commit", "-q", "-m", "the profile in use, with a real workspace name in it")
        # 2. The profile moves on; HEAD no longer has that name.
        write("testkit.env.example",
              "GSVTK_PROJECT=%s\nGSVTK_TERRA_WORKSPACE=GATK-SV-head-to-head\n" % PLACEHOLDER)
        g("add", "-A")
        g("commit", "-q", "-m", "the profile moved on; HEAD no longer resolves the old name")
        # 3. Files at HEAD naming the whole value, only its segment, and only a placeholder.
        write("docs/plan.md", "the arm ran in %s and nowhere else\n" % LEAK)
        write("docs/notes.md", "the tag fragment %s appears on its own here\n" % SEG)
        write("docs/placeholders.md", "copy %s from the template\n" % PLACEHOLDER)
        g("add", "-A")
        g("commit", "-q", "-m", "a doc names the workspace the profile stopped carrying")

        rc, out = run_cli([])
        tally = [l for l in out.splitlines() if l.startswith("audit: tally:")]
        ck("a historical config value that today's profile cannot resolve is DERIVED, and the file "
           "naming it at HEAD goes RED (gap A28: the resolver-only tier reported clean on exactly "
           "this shape on this repo)",
           rc == 1 and tally == ["audit: tally: hits=2 files=2"], "rc=%s %s" % (rc, tally))
        ck("the hit is labelled with the key and the commit it was read out of, so a reader can "
           "check the provenance instead of trusting the tool",
           "personal: TERRA_WORKSPACE (history " in out)
        ck("the tier is its own counted line, distinct from the resolver-derived one",
           "derived from git HISTORY" in out and "derived from kit/gsvtk-config" in out)
        ck("a leak carrying ONLY a segment of the historical value fires (the whole-string case is "
           "the one that shipped for months in docs/archive)",
           "docs/notes.md" in out)
        ck("CONTROL: a value the tracked config still carries at HEAD is NOT flagged -- a documented "
           "placeholder is public by construction, and flagging it is how an audit gets waived",
           "docs/placeholders.md" not in out, "placeholder flagged")
        ck("the two states are distinguishable in the header: counted-as-somebody's, and excluded-as"
           "-public",
           "public by construction" in out and "not in the profile today" in out)

        # The cure: same repo and history, values replaced with placeholders.
        write("docs/plan.md", "the arm ran in <your-old-workspace> and nowhere else\n")
        write("docs/notes.md", "the tag fragment <suffix> appears on its own here\n")
        rc2, out2 = run_cli([])
        ck("CONTROL: the SAME repo, SAME history, with the two files placeholder-ised, is GREEN -- "
           "the verdict is carried by the file, and the tier still derives the value (so a cure "
           "cannot be mistaken for a dead check)",
           rc2 == 0 and "tally: hits=0 files=0" in out2
           and "1 historical config value(s) HEAD does not resolve" in out2,
           "rc=%s" % rc2)

        # The two ways this tier could silently stop working.
        empty = os.path.join(d, "empty")
        os.makedirs(empty)
        ge = lambda *a: subprocess.run(["git", "-C", empty, "-c", "user.name=t",
                                        "-c", "user.email=t@example.invalid", *a],
                                       capture_output=True, check=True)
        ge("init", "-q", "-b", "main")
        with open(os.path.join(empty, "README.md"), "w") as fh:
            fh.write("a repo whose config was never committed\n")
        ge("add", "-A")
        ge("commit", "-q", "-m", "no config history at all")
        old_argv, buf = list(sys.argv), io.StringIO()
        try:
            sys.argv = ["audit.py", "--root", empty]
            with contextlib.redirect_stdout(buf):
                rc3 = main()
        finally:
            sys.argv = old_argv
        out3 = buf.getvalue()
        ck("a repo with no config history says the tier yielded NOTHING rather than passing quietly "
           "-- the vacuous-guard failure this repo refuses, named at the point it would be read as "
           "an all-clear", rc3 == 0 and "history derivation yielded NOTHING" in out3, "rc=%s" % rc3)
        rc4, out4 = run_cli(["--no-git"])
        ck("--no-git announces that the history tier was SKIPPED: a tier that did not run is not a "
           "tier that passed", rc4 == 0 and "HISTORY tier SKIPPED" in out4, "rc=%s" % rc4)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print("history tier: %d controls ok, %d failed (a historical coordinate must make the publishable"
          " tree red, a documented placeholder must not, and a tier that did not run must say so)"
          % (ok[0], len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
