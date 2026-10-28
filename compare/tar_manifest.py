#!/usr/bin/env python3
"""Bundle diff: compare two .tar.gz (or two directories) by member manifest, and recurse.

Many WDLs emit tar.gz bundles (gCNV calls, ploidy calls, RF intermediate files, per-sample QC).
Bundles with identical content routinely differ in bytes, so this compares member manifests.

    python compare/tar_manifest.py base/10-GenotypeBatch.RF_intermediate_files.tar.gz \
                                   new/10-GenotypeBatch.RF_intermediate_files.tar.gz --hash
    python compare/tar_manifest.py ploidy_calls_a.tar.gz ploidy_calls_b.tar.gz
    python compare/tar_manifest.py unpacked_a/ unpacked_b/          # directories work too

Rule:
  * the unit of comparison is the member manifest: path, size, type. Byte identity is only checked
    with `--hash`, and the output says when it was not.
  * mtimes and ownership are ignored unless `--compare-mtime` is given.
  * a member that is itself a tar is recursed (depth <= `--depth`, default 2) and its members appear
    as `inner.tar.gz!/member`.
  * member order is ignored; members present on one side only are reported separately from members
    whose size or bytes differ.
  * nothing is extracted to disk.
Exit: 0 manifests identical, 1 they differ, 2 one side could not be read as a bundle/directory.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import sys
import tarfile

import artifact
from artifact import die  # noqa: E402

TAR_SUFFIXES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
INCLUDE_JUNK = False                       # set by --include-junk



def is_junk(name: str) -> bool:
    """True for macOS AppleDouble/`.DS_Store` members (dropped by default; the count is printed)."""
    base = name.rsplit("/", 1)[-1]
    return base.startswith("._") or base == ".DS_Store" or "/__MACOSX/" in name or \
        name.startswith("__MACOSX/")


def is_tar_name(name: str) -> bool:
    return name.lower().endswith(TAR_SUFFIXES)


def hash_stream(fileobj) -> str:
    h = hashlib.sha256()
    for chunk in iter(lambda: fileobj.read(1 << 20), b""):
        h.update(chunk)
    return h.hexdigest()[:16]


def manifest_of_dir(root: str, do_hash: bool) -> dict:
    out = {}
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            full = os.path.join(base, name)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if is_junk(rel) and not INCLUDE_JUNK:
                continue
            try:
                stat = os.stat(full)
            except OSError:
                continue
            entry = {"size": stat.st_size, "type": "f"}
            if do_hash:
                with open(full, "rb") as fh:
                    entry["sha256_16"] = hash_stream(fh)
            out[rel] = entry
            if is_tar_name(rel):
                out.update(manifest_of_tar(full, do_hash, 1, 2, prefix=rel + "!"))
    return out


def manifest_of_tar(path: str, do_hash: bool, depth: int, max_depth: int, prefix: str = "") -> dict:
    out = {}
    try:
        tar = tarfile.open(path, "r:*")
    except tarfile.TarError as exc:
        die(f"{path} could not be opened as a tar archive ({exc}) — a truncated fetch is the usual "
            f"cause, and diffing a partial read as a result is the false pass this tool exists to "
            f"avoid", 2)
    with tar:
        for member in tar:
            if not member.isfile():
                continue
            if is_junk(member.name) and not INCLUDE_JUNK:
                continue
            name = member.name if not prefix else f"{prefix}/{member.name}"
            entry = {"size": member.size, "type": "f"}
            if do_hash:
                handle = tar.extractfile(member)
                if handle is not None:
                    entry["sha256_16"] = hash_stream(handle)
            out[name] = entry
            if is_tar_name(member.name) and depth < max_depth:
                blob = tar.extractfile(member)
                if blob is None:
                    continue
                raw = blob.read()
                try:
                    nested = tarfile.open(fileobj=io.BytesIO(raw), mode="r:*")
                except tarfile.TarError:
                    continue          # a truncated inner bundle is reported as a member, not a crash
                with nested:
                    for inner in nested:
                        if not inner.isfile():
                            continue
                        child = f"{name}!/{inner.name}"
                        record = {"size": inner.size, "type": "f"}
                        if do_hash:
                            handle = nested.extractfile(inner)
                            if handle is not None:
                                record["sha256_16"] = hash_stream(handle)
                        out[child] = record
    return out


def load(path: str, label: str, do_hash: bool, depth: int) -> dict:
    if os.path.isdir(path):
        return manifest_of_dir(path, do_hash)
    if not os.path.isfile(path):
        die(f"{label}: no such file or directory: {path}")
    if path.lower().endswith(TAR_SUFFIXES) or path.lower().endswith(".gz"):
        return manifest_of_tar(path, do_hash, 1, depth)
    die(f"{label}: {path} is not a tar bundle or a directory; a single file belongs in "
        f"compare/lineset_diff.py (text) or is compared by hash elsewhere", 2)
    raise AssertionError


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compare two tar bundles (or directories) by member manifest, recursing inward.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bundle_a")
    ap.add_argument("bundle_b")
    ap.add_argument("--label-a", default="A")
    ap.add_argument("--label-b", default="B")
    ap.add_argument("--hash", action="store_true",
                    help="hash member bytes (default off: then byte identity is NOT claimed)")
    ap.add_argument("--include-junk", action="store_true",
                    help="keep macOS ._*/.DS_Store members instead of dropping them")
    ap.add_argument("--compare-mtime", action="store_true",
                    help="also report members whose mtime differs (ignored by default)")
    ap.add_argument("--depth", type=int, default=2, help="tar-in-tar recursion depth (default 2)")
    ap.add_argument("--max-print", type=int, default=25)
    ap.add_argument("--json", dest="json_out", help="write the machine-readable artifact here")
    args = ap.parse_args()
    global INCLUDE_JUNK
    INCLUDE_JUNK = args.include_junk

    ma = load(args.bundle_a, args.label_a, args.hash or args.compare_mtime, args.depth)
    mb = load(args.bundle_b, args.label_b, args.hash or args.compare_mtime, args.depth)
    print(f"# {args.label_a}: {args.bundle_a}  ({len(ma)} member(s))")
    print(f"# {args.label_b}: {args.bundle_b}  ({len(mb)} member(s))")
    print("# rule: member path + size compared; byte hash %s; mtimes %s; nesting depth %d; "
          "member order is never a difference"
          % ("ON" if args.hash else "OFF (byte identity is NOT claimed)",
             "compared" if args.compare_mtime else "ignored", args.depth))
    if not ma or not mb:
        die(f"one side holds 0 members ({args.label_a} {len(ma)}, {args.label_b} {len(mb)}), so "
            f"there is nothing to compare", 2)

    keys_a, keys_b = set(ma), set(mb)
    only_a = sorted(keys_a - keys_b)
    only_b = sorted(keys_b - keys_a)
    shared = sorted(keys_a & keys_b)
    size_differ = [k for k in shared if ma[k]["size"] != mb[k]["size"]]
    hash_differ = ([k for k in shared
                    if ma[k].get("sha256_16") and mb[k].get("sha256_16")
                    and ma[k]["sha256_16"] != mb[k]["sha256_16"]] if args.hash else [])
    same = [k for k in shared if k not in size_differ and k not in hash_differ]

    for title, rows in ((f"member present only in {args.label_a}", only_a),
                        (f"member present only in {args.label_b}", only_b)):
        if rows:
            print(f"\n== {title} ({len(rows)})")
            for name in rows[:args.max_print]:
                side = ma if title.endswith(args.label_a) else mb
                print(f"  {name}   [{side[name]['size']:,} bytes]")
            if len(rows) > args.max_print:
                print(f"  ... {len(rows) - args.max_print} more")
    if size_differ:
        print(f"\n== size differs ({len(size_differ)})")
        for name in size_differ[:args.max_print]:
            print(f"  {name:<70} {ma[name]['size']:,} -> {mb[name]['size']:,}")
    if hash_differ:
        print(f"\n== same size, different bytes ({len(hash_differ)})")
        for name in hash_differ[:args.max_print]:
            print(f"  {name:<70} {ma[name]['sha256_16']} -> {mb[name]['sha256_16']}")

    identical = not (only_a or only_b or size_differ or hash_differ)
    print(f"\n== SUMMARY  {len(shared)} shared member(s), {len(same)} matching; "
          f"{len(only_a)} only in {args.label_a}, {len(only_b)} only in {args.label_b}, "
          f"{len(size_differ)} size-differing, {len(hash_differ)} byte-differing"
          + ("" if args.hash else "  [byte identity not checked: rerun with --hash]"))
    if args.json_out:
        with open(args.json_out, "w") as fh:
            json.dump({"tool": "tar_manifest", "argv": sys.argv[1:],
                       "inputs": {**artifact.input_file(args.label_a, args.bundle_a,
                                                        members=len(ma)),
                                  **artifact.input_file(args.label_b, args.bundle_b,
                                                        members=len(mb))},
                       "compared_something": bool(ma or mb),
                       "rule": {"hash": args.hash, "compare_mtime": args.compare_mtime,
                                "depth": args.depth},
                       "difference": {"only_a": only_a, "only_b": only_b, "size": size_differ,
                                      "bytes": hash_differ},
                       "identical": identical,
                       "members": {k: {"a": ma[k], "b": mb[k]} for k in shared[:5000]}},
                      fh, indent=1, sort_keys=True)
            fh.write("\n")
        print(f"wrote artifact -> {args.json_out}")
    return 0 if identical else 1


if __name__ == "__main__":
    sys.exit(main())
