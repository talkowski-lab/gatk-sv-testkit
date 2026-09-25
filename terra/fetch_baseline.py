#!/usr/bin/env python
"""Freeze the v1.1.1 baseline run in the Terra featured workspace into a local manifest.

The featured workspace drives GATK-SV as 20 numbered per-step method configs whose WDLs come from
Dockstore at tag v1.1.1. Each config's inputs are expressions over `this.sample_set.<attr>` and
`workspace.<attr>`. This script resolves those expressions to concrete gs:// paths using the live
entity + workspace attribute values, so the *exact* inputs the v1.1.1 run consumed can be replayed
against the new genotyper, and the v1.1.1 outputs can be diffed against the new ones.

    python terra/fetch_baseline.py --steps 05 06 07 08 09 10

Writes:
    work/recon/configs_<step>.json         raw method config payloads
    work/manifests/baseline_run.json      resolved inputs/outputs per step, keyed by entity
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import terra  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
# Paths only: import runs for `--help` too, and printing usage must not create scratch dirs.
# Writes here make their own directories (os.makedirs(MAN) in main, terra.dump mkdirs the parent).
RECON = str(config.work_path("recon"))
MAN = str(config.work_path("manifests"))

EXPR = re.compile(r"^(?:this|workspace)\.([A-Za-z0-9_.]+)$")
GS = re.compile(r"gs://\S+")


def p(*a):
    print(*a, flush=True)


def get_config(ns, ws, cnamespace, name):
    r = terra.session().get(f"{terra.TERRA_API}workspaces/{ns}/{ws}/methodconfigs/{cnamespace}/{name}",
                            timeout=60)
    if r.status_code != 200:
        raise terra.TerraError(f"{name}: HTTP {r.status_code} {r.text[:200]}")
    return r.json()


def flatten_entities(rows, etype):
    """entity name -> {attr: value} with Terra's nested list representation flattened to lists."""
    out = {}
    for r in rows:
        attrs = {}
        for k, v in r.get("attributes", {}).items():
            if isinstance(v, dict) and v.get("itemsType") == "AttributeValue":
                attrs[k] = [i if isinstance(i, str) else json.dumps(i) for i in v.get("items", [])]
            else:
                attrs[k] = v
        out[r["name"]] = attrs
    return {"name": etype, "rows": out}


def resolve(expr, ent_name, tables, ws_attrs, root_type):
    """Resolve `this.<...>` / `workspace.<...>` expressions to concrete values.

    `tables` maps entity type -> {entity name: {attr: value}}. Cross-entity references
    (`this.sample_sets.merged_bincov`) are followed through entity-reference attributes.
    """
    if not isinstance(expr, str):
        return expr
    s = expr.strip().strip('"')
    m = EXPR.match(s)
    if not m:
        return expr
    path = m.group(1)
    parts = path.split(".")
    if s.startswith("workspace."):
        return {"resolved_from": expr, "value": ws_attrs.get(path)}

    def attr_or_none(row, name):
        return row.get(name) if row else None

    head = parts[0]
    if head == root_type:
        row = tables.get(root_type, {}).get(ent_name)
        attr = ".".join(parts[1:])
        return {"resolved_from": expr, "value": attr_or_none(row, attr)}
    # reference attribute hop, e.g. this.sample_sets.merged_bincov
    row = tables.get(root_type, {}).get(ent_name) or {}
    ref = row.get(head)
    if isinstance(ref, dict) and ref.get("itemsType") == "AttributeValue" or isinstance(ref, list):
        items = ref.get("items") if isinstance(ref, dict) else ref
        if items:
            first = items[0]
            if isinstance(first, dict) and first.get("entityName"):
                target = first["entityType"] if "entityType" in first else first.get("entity_type", "")
                trow = tables.get(target, {}).get(first["entityName"])
                return {"resolved_from": expr, "via": f"{head}->{first['entityName']}",
                        "value": attr_or_none(trow, ".".join(parts[1:]))}
            return {"resolved_from": expr, "value": items}
    # bare attribute on the root entity (this.<attr>)
    return {"resolved_from": expr, "value": row.get(path)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default=terra.BASELINE_NS)
    ap.add_argument("--ws", default=terra.BASELINE_WS)
    ap.add_argument("--steps", nargs="*", default=["05", "06", "07", "08", "09", "10"])
    # The row stage_inputs.py reads back out of this manifest. Defaulting it to a literal batch
    # name while the consumer reads GSVTK_BATCH is how a frozen manifest and a staging run silently
    # disagreed about which sample_set was the batch.
    ap.add_argument("--entity", default=config.get("BATCH", "all_samples"),
                    help="sample_set name to resolve (default: GSVTK_BATCH)")
    args = ap.parse_args()

    os.makedirs(MAN, exist_ok=True)
    ws = terra.workspace(args.ns, args.ws)
    ws_attrs = ws.get("workspace", {}).get("attributes", {})
    p(f"workspace attributes: {len(ws_attrs)}")

    cfgs = terra.session().get(f"{terra.TERRA_API}workspaces/{args.ns}/{args.ws}/methodconfigs?allRepos=true",
                               timeout=90).json()
    by_name = {c["name"]: c for c in cfgs}
    p(f"method configs: {len(by_name)}")

    # entity attribute tables for every entity type the steps root on
    ent_tables = {}
    for etype in ("sample_set", "sample_set_set", "sample"):
        d = terra.entity_sample(args.ns, args.ws, etype, page_size=200)
        ent_tables[etype] = flatten_entities(d.get("results", []), etype)
        p(f"entities {etype}: {len(ent_tables[etype]['rows'])}")
    terra.dump(ent_tables, os.path.join(RECON, "entities_all.json"))

    manifest = {"workspace": f"{args.ns}/{args.ws}", "workspace_id": ws["workspace"]["workspaceId"],
                "bucket": ws["workspace"]["bucketName"], "baseline_tag": "v1.1.1",
                "workspace_attributes": ws_attrs, "steps": {}, "entities": {}}

    for prefix in args.steps:
        matches = [n for n in by_name if n.startswith(prefix + "-")]
        if not matches:
            p(f"!! no config for step {prefix}")
            continue
        name = sorted(matches)[0]
        summ = by_name[name]
        cfg = get_config(args.ns, args.ws, summ.get("namespace") or args.ns, name)
        terra.dump(cfg, os.path.join(RECON, f"configs_{name}.json"))
        root = cfg.get("rootEntityType")
        ent_rows = ent_tables.get(root, {}).get("rows", {})
        chosen = {k: v for k, v in ent_rows.items() if (k == args.entity or args.entity == "*")}
        if not chosen:
            chosen = dict(list(ent_rows.items())[:1])
        ent_name = list(chosen)[0]
        by_type = {t: v["rows"] for t, v in ent_tables.items()}
        resolved_in = {k: resolve(v, ent_name, by_type, ws_attrs, root)
                       for k, v in cfg.get("inputs", {}).items()}
        resolved_out = {k: resolve(v, ent_name, by_type, ws_attrs, root)
                        for k, v in cfg.get("outputs", {}).items()}
        ngs = len(GS.findall(json.dumps({"i": resolved_in, "o": resolved_out}, default=str)))
        manifest["steps"][name] = {"methodUri": cfg.get("methodRepoMethod", {}).get("methodUri"),
                                   "rootEntityType": root, "entity": ent_name,
                                   "inputs": resolved_in, "outputs": resolved_out}
        p(f"step {name:26s} root={root:16s} entity={ent_name:16s} "
          f"in={len(resolved_in):3d} out={len(resolved_out):3d} gs_uris={ngs}")

    manifest["entities"] = {k: v["rows"] for k, v in ent_tables.items()}
    out = os.path.join(MAN, "baseline_run.json")
    with open(out, "w") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True, default=str)
    p(f"-> {out} ({os.path.getsize(out)/1e6:.1f} MB)")


if __name__ == "__main__":
    main()
