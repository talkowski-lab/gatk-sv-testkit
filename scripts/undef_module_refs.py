#!/usr/bin/env python3
"""Flag attribute access through a module name this file never imported.

Why a static check instead of more tests
----------------------------------------
`py_compile` resolves no names, and the `--help` sweep exits inside argparse before `main()`
runs. Those two steps are the whole offline gate, so a comparator that raised
`NameError: name 'os' is not defined` on *every real invocation* passed `make test`, shipped to a
public repo, and left `docs/comparators.md` and `examples/table_diff_example.md` describing output
nobody could reproduce. The defect was introduced by adding a guard that used `os.path.isdir` and
`sys.exit` to a file that imported neither.

Tests would only catch the one file they exercise. This closes the class, for every file, without
running anything: an undefined-module-reference is a compile-clean bug, so it needs a compile-time
answer. No dependencies, stdlib `ast` only.

Rule
----
For `Attribute(value=Name(n))` anywhere in a file: FAIL if `n` is a known stdlib module name, and
`n` is neither imported in this file, nor bound at module level, nor a local/parameter/def name,
nor a builtin. The restriction to real module names is what keeps ordinary locals
(`d.items()`, `line.split()`) from becoming noise.

Exit: 0 clean, 1 findings, 2 usage error.
"""
import argparse
import ast
import builtins
import os
import sys

# sys.stdlib_module_names arrived in 3.10 and this toolkit targets 3.9, so carry a list. Only the
# names people actually import-and-forget matters here: an unlisted module produces no finding,
# which is a miss, not a false alarm.
FALLBACK_STDLIB = """
abc argparse array ast asyncio atexit base64 binascii bisect builtins bz2 calendar cmath cmd
collections colorsys concurrent configparser contextlib copy copyreg csv ctypes curses datetime
decimal difflib dis distutils doctest email encodings enum errno faulthandler fcntl filecmp
fileinput fnmatch fractions ftplib functools gc genericpath gettext glob graphlib grp gzip hashlib
hmac html http imaplib importlib inspect io ipaddress itertools json keyword linecache locale
logging lzma mailbox math mimetypes mmap multiprocessing netrc numbers operator os os.path
pathlib pdb pickle pickletools platform plistlib poplib pprint profile pstats pty pwd py_compile
queue random re readline reprlib resource rlcompleter runpy sched secrets select selectors
shelve shlex shutil signal site smtplib socket socketserver sqlite3 ssl stat statistics string
stringio struct subprocess symtable sys syslog tabnanny tarfile tempfile termios textwrap
threading time timeit tkinter token tokenize tomllib trace traceback tracemalloc tty types
typing unicodedata unittest urllib uuid venv warnings wave weakref webbrowser wsgiref xml
xmlrpc zipapp zipfile zlib zoneinfo
""".split()

STDLIB = set(getattr(sys, "stdlib_module_names", ())) | set(FALLBACK_STDLIB)
# 'os.path' is imported as 'os'; keep dotted names out of the lookup set
STDLIB = {m for m in STDLIB if "." not in m or m == "os"}
BUILTIN = set(dir(builtins))


class Seen(ast.NodeVisitor):
    """One pass: collect what the file binds, and every attribute base that is a bare name."""

    def __init__(self):
        self.bound = set()
        self.uses = []            # (lineno, col, name)

    # ---- things that bind a name
    def visit_Import(self, node):
        for a in node.names:
            self.bound.add((a.asname or a.name).split(".")[0])

    def visit_ImportFrom(self, node):
        for a in node.names:
            self.bound.add(a.asname or a.name)
            if a.name == "*":
                self.bound.add("*")

    def visit_FunctionDef(self, node):
        self.bound.add(node.name)
        for a in list(node.args.args) + list(node.args.kwonlyargs) + list(node.args.posonlyargs or []):
            self.bound.add(a.arg)
        if node.args.vararg:
            self.bound.add(node.args.vararg.arg)
        if node.args.kwarg:
            self.bound.add(node.args.kwarg.arg)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        self.bound.add(node.name)
        self.generic_visit(node)

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bound.add(node.id)
        elif isinstance(node.ctx, ast.Load):
            # Only attribute bases: `os.path`, `json.dumps`, `re.sub`. A bare `os()` call is a
            # different bug shape and NameErrors loudly enough to be found by a run.
            self.uses.append((node.lineno, node.col_offset, node.id))
        self.generic_visit(node)

    def visit_Attribute(self, node):
        # Record the base, do not descend into it as a Load of an unrelated name.
        v = node.value
        while isinstance(v, ast.Attribute):
            v = v.value
        if isinstance(v, ast.Name):
            self.uses.append((node.lineno, node.col_offset, v.id))
        self.visit(v)


def scan(path, src):
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:                    # `make syntax` owns parse errors
        return []
    seen = Seen()
    seen.visit(tree)
    if "*" in seen.bound:                       # a star import makes this analysis unsound
        return []
    out = []
    for lineno, col, name in seen.uses:
        if name in STDLIB and name not in seen.bound and name not in BUILTIN:
            out.append((path, lineno, col, name))
    return sorted(set(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths", nargs="*", help="files or directories to scan "
                                             "(default: the repo's own tree)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="path substring to skip (repeatable). docs/archive is excluded by "
                         "default: those scripts are an as-run record, not runnable code.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    args.exclude.append("docs/archive")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = []
    for p in args.paths:
        if os.path.isdir(p):                       # directories are scanned, not opened
            files += [os.path.join(p, n) for n in sorted(os.listdir(p)) if n.endswith(".py")]
        elif os.path.isfile(p):
            files.append(p)
        else:
            print(f"  no such file or directory: {p}", file=sys.stderr)
            return 2
    if not files and not args.paths:               # explicit paths must never fall back to the repo
        for dirpath, dirnames, names in os.walk(root):
            dirnames[:] = [d for d in dirnames
                           if d not in {".git", "__pycache__", ".venv", "node_modules"}]
            if any(x in dirpath + os.sep for x in args.exclude):
                continue
            files += [os.path.join(dirpath, n) for n in names if n.endswith(".py")]
    hits, bad = [], 0
    for f in sorted(files):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                hits += scan(f, fh.read())
        except OSError as e:
            print(f"  unreadable {f}: {e}", file=sys.stderr)
            bad += 1
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep
    for path, lineno, _col, name in hits:
        print(f"  FAIL  {path.replace(here, '')}:{lineno}  '{name}.' used but never imported "
              f"or bound in this file")
    if args.verbose:
        print(f"scanned {len(files)} file(s); {len(hits)} undefined module reference(s)")
    if bad:
        return 2
    if hits:
        print(f"=> {len(hits)} undefined module reference(s): these raise NameError at run time, "
              f"which py_compile cannot see and --help never reaches")
        return 1
    print(f"undefmods: {len(files)} files, no attribute access on an unimported module")
    return 0


if __name__ == "__main__":
    sys.exit(main())
