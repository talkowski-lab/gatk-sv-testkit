#!/usr/bin/env python
"""Read-only Terra recon for the GATK-SV head-to-head test framework.

Dump everything needed to design a head-to-head replay against a completed baseline run:
identity, billing, accessible workspaces, the featured baseline workspace's data model,
its method configurations, and its submission history (which carries the v1.1.1 run's
real inputs/outputs and gs:// paths).

    python terra/recon.py            # everything, writes work/recon/*.json
    python terra/recon.py --entities batch,sample
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

OUT = str(config.work_dir("recon"))
GS = re.compile(r"gs://[^\s\"'\\]+")


FAILS = []          # every probe below catches its own exceptions; count them


def p(*a):
    line = " ".join(str(x) for x in a)
    # Every failure path in this file prints a line containing "failed:". Counting here keeps
    # one honest exit code without wrapping all eleven sections in bookkeeping -- and without
    # it, a recon where nothing worked (no ADC, wrong workspace) exited 0 like a clean one.
    if " failed:" in line:
        FAILS.append(line.strip()[:60])
    print(line, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ns", default=terra.BASELINE_NS)
    ap.add_argument("--ws", default=terra.BASELINE_WS)
    ap.add_argument("--entities", default="", help="comma separated entity types to page")
    ap.add_argument("--subs", type=int, default=25)
    ap.add_argument("--page-size", dest="page_size", type=int, default=3)
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)

    p("== identity")
    try:
        p("   user:", terra.whoami())
    except Exception as e:
        p("   whoami failed:", type(e).__name__, e)

    p("== billing projects")
    try:
        bp = terra.billing_projects()
        terra.dump(bp, os.path.join(OUT, "billing.json"))
        for b in bp[:40]:
            p(f"   {b['project']}  roles={b['roles']}  status={b['status']}")
        p(f"   total {len(bp)} -> recon/billing.json")
    except Exception as e:
        p("   billing failed:", type(e).__name__, str(e)[:200])

    p("== accessible workspaces")
    try:
        ws = terra.workspaces()
        terra.dump(ws, os.path.join(OUT, "workspaces.json"))
        p(f"   {len(ws)} workspaces -> recon/workspaces.json")
        for w in ws[:25]:
            p(f"   {w['access']:<8} {w['namespace']}/{w['name']}  bucket={w['bucket']}")
    except Exception as e:
        p("   workspaces failed:", type(e).__name__, str(e)[:200])

    p(f"== baseline workspace {args.ns}/{args.ws}")
    try:
        w = terra.workspace(args.ns, args.ws)
        terra.dump(w, os.path.join(OUT, "baseline_workspace.json"))
        wd = w.get("workspace", {})
        p("   id:", wd.get("workspaceId"), "bucket:", wd.get("bucketName"))
        p("   created:", wd.get("createdDate"), "last modified:", wd.get("lastModified"))
        attrs = wd.get("attributes", {})
        p("   workspace attributes:", json.dumps(attrs, default=str)[:600])
        p("   can compute:", w.get("canCompute"), "access:", w.get("accessLevel"))
        p("   submission stats:", json.dumps(w.get("workspaceSubmissionStats", {}), default=str))
    except Exception as e:
        p("   workspace failed:", type(e).__name__, str(e)[:300])

    p("== entity types + counts")
    try:
        et = terra.entity_types(args.ns, args.ws)
        terra.dump(et, os.path.join(OUT, "baseline_entity_types.json"))
        for tname, tv in sorted(et.items()):
            p(f"   {tname}  count={tv.get('count')}  attrs={len(tv.get('attributeNames', []))}")
    except Exception as e:
        p("   entity types failed:", type(e).__name__, str(e)[:300])
        et = {}

    want = [x for x in args.entities.split(",") if x]
    if not want:
        want = [t for t in ("sample_set", "sample_set_set", "batch", "sample") if t in et][:4]
    p(f"== sampling entity types: {want}")
    ent_dump = {}
    for t in want:
        try:
            d = terra.entity_sample(args.ns, args.ws, t, page_size=args.page_size)
            res = d.get("results", [])
            total = ((d.get("params", {}).get("params") or {}).get("totalLength")) or len(res)
            ent_dump[t] = {"total": total, "sample": res}
            p(f"   {t}: total={total}")
            for r in res[:8]:
                p(f"      {r.get('name')}: {len(r.get('attributes', {}))} attrs")
        except Exception as e:
            p(f"   {t}: failed {type(e).__name__} {str(e)[:120]}")
    terra.dump(ent_dump, os.path.join(OUT, "baseline_entities.json"))
    allgs = set()
    for t, d in ent_dump.items():
        for r in d.get("sample", []):
            allgs.update(GS.findall(json.dumps(r.get("attributes", {}), default=str)))
    p(f"   gs:// paths seen in sampled entities: {len(allgs)}")
    for g in sorted(allgs)[:15]:
        p("     ", g)

    p("== workspace method configs")
    try:
        cfgs = terra.session().get(f"{terra.TERRA_API}workspaces/{args.ns}/{args.ws}/methodconfigs",
                                   timeout=60).json()
        terra.dump(cfgs, os.path.join(OUT, "baseline_configs.json"))
        for c in cfgs:
            p(f"   {c.get('namespace')}/{c.get('name')}  snapshot={c.get('snapshotId')}"
              f"  entity={c.get('rootEntityType')}  wdl={c.get('methodUri')}")
    except Exception as e:
        p("   configs failed:", type(e).__name__, str(e)[:300])

    p("== submissions")
    try:
        subs = {"submissions": terra.submissions(args.ns, args.ws, limit=args.subs)}
        terra.dump(subs, os.path.join(OUT, "baseline_submissions.json"))
        p(f"   {len(subs['submissions'])} recent -> recon/baseline_submissions.json")
        for s in subs["submissions"]:
            p(f"   {s.get('submissionDate')}  {s.get('workflowStatus')}  "
              f"config={s.get('methodConfigurationNamespace')}/{s.get('methodConfigurationName')}  "
              f"entity={s.get('entityType')}/{s.get('entityName')}  id={s.get('submissionId')}")
    except Exception as e:
        p("   submissions failed:", type(e).__name__, str(e)[:300])
        subs = {}

    # One level deeper on the most recent SUCCEEDED-ish submission: real input/output paths.
    for s in subs.get("submissions", [])[:3]:
        sid = s.get("submissionId")
        try:
            full = terra.submission(args.ns, args.ws, sid)
            terra.dump(full, os.path.join(OUT, f"submission_{sid}.json"))
            for wf in full.get("workflows", [])[:1]:
                ins = [ir.get("value") for ir in wf.get("inputResolutions", []) if ir.get("value")]
                outs = [o.get("value") for o in wf.get("outputResolutions", []) if o.get("value")]
                p(f"   sub {sid[:8]} wf={wf.get('taskRoots') or wf.get('workflowName')}")
                p(f"      inputs {len(ins)}, outputs {len(outs)}")
                for v in outs[:12]:
                    p("        out:", str(v)[:120])
        except Exception as e:
            p(f"   sub {sid}: failed {type(e).__name__} {str(e)[:120]}")


if __name__ == "__main__":
    main()
    if FAILS:
        print(f"\n{len(FAILS)} section(s) failed above - the output is partial, not clean. "
              "Most commonly: no application-default credentials "
              "(gcloud auth application-default login), or --ns/--ws you cannot read.")
        sys.exit(1)
