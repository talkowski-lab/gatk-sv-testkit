#!/usr/bin/env python
"""Save every method config in one Terra workspace, full payloads, to one JSON file. Read-only.

The workspace listing (what `terra/recon.py` saves) carries `rootEntityType` but not `inputs`, so each
config is fetched in full. The file is the input to
`checks/terra_entity_check.py --against-dump FILE`, which compares Terra's `rootEntityType` with the
type derived from each config's own `${this.<etype>_id}` name key.

    python terra/dump_configs.py                          # the sandbox (GSVTK_TERRA_NAMESPACE/_WORKSPACE)
    python terra/dump_configs.py --ns NS --ws WS          # any workspace you can read, baseline included
    python terra/dump_configs.py --out /path/file.json

Only GETs: no `--confirm`, and it runs under GSVTK_READ_ONLY=1. Default output:
`<GSVTK_WORK>/configs/<ns>__<ws>.json`. The path is printed on the last line.

Exit codes: 0 every listed config saved; 1 the listing failed or any config fetch failed (the file is
still written with an `errors` list when the listing succeeded); 4 no --ns/--ws and none configured.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "kit"))
sys.path.insert(0, _HERE)
import config  # noqa: E402


def collect(terra, ns: str, ws: str) -> dict:
    """{namespace, workspace, listed, configs, errors}: one full payload per listed config."""
    listed = terra.workspace_configs(ns, ws)
    out = {"namespace": ns, "workspace": ws, "listed": len(listed), "configs": [], "errors": []}
    for c in listed:
        cns, cname = c.get("namespace"), c.get("name")
        try:
            out["configs"].append(terra.config_payload(ns, ws, cns, cname))
        except Exception as e:
            out["errors"].append({"namespace": cns, "name": cname,
                                  "error": "%s: %s" % (type(e).__name__, str(e)[:300])})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dump_configs.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ns", default="", help="workspace namespace (default: GSVTK_TERRA_NAMESPACE)")
    ap.add_argument("--ws", default="", help="workspace name (default: GSVTK_TERRA_WORKSPACE)")
    ap.add_argument("--out", default="", help="output file (default: <GSVTK_WORK>/configs/<ns>__<ws>.json)")
    args = ap.parse_args(argv)

    ns = args.ns or config.require("TERRA_NAMESPACE", "which workspace's method configs to dump (or --ns)")
    ws = args.ws or config.require("TERRA_WORKSPACE", "which workspace's method configs to dump (or --ws)")
    out_path = args.out or str(config.work_path("configs", "%s__%s.json" % (ns, ws)))

    import terra                                    # firecloud; imported here so --help needs none
    print("reading method configs in %s/%s (GET only)" % (ns, ws), flush=True)
    try:
        data = collect(terra, ns, ws)
    except Exception as e:
        print("listing failed: %s: %s" % (type(e).__name__, str(e)[:600]))
        return 1
    terra.dump(data, out_path)
    for c in data["configs"]:
        print("  %s/%s  rootEntityType=%s  inputs=%d" % (
            c.get("namespace"), c.get("name"), c.get("rootEntityType") or "-",
            len(c.get("inputs") or {})))
    for e in data["errors"]:
        print("  %s/%s  fetch failed: %s" % (e["namespace"], e["name"], e["error"]))
    print("%d of %d config(s) saved; %d failed" % (len(data["configs"]), data["listed"], len(data["errors"])))
    print(out_path)
    return 1 if data["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
