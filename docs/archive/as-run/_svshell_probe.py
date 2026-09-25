#!/usr/bin/env python3
"""_svshell_probe.py -- print SVShell's required input names as JSON for svshell_arms.py.

Prints {"wf_required": [...], "task_required": [...]} for workflow SVShell and task RunSVShell.
Load it on a tree materialised from a single ref (`git archive <ref> wdl | tar -x`), never a mixed
directory: a mixed tree silently resolves imports from the wrong ref and produces a false verdict.
"""
import json
import sys

import WDL


def calls_in(body, out, depth=0):
    for node in body:
        if isinstance(node, WDL.Call):
            out.append(node)
        elif hasattr(node, 'body') and depth < 4:
            calls_in(node.body, out, depth + 1)


path = sys.argv[1]
doc = WDL.load(path)
workflows = [doc.workflow] + list(getattr(doc, 'secondaryWorkflows', []) or [])
wf = next(w for w in workflows if w.name == 'SVShell')
task = next(t for t in doc.tasks if t.name == 'RunSVShell')

wf_required = sorted(d.name for d in wf.inputs
                     if d.expr is None and not getattr(d.type, 'optional', False))
task_required = sorted(d.name for d in task.inputs
                       if d.expr is None and not getattr(d.type, 'optional', False))
bound = set()
cs = []
calls_in(wf.body, cs)
for c in cs:
    for k, v in c.inputs.items():
        if v is not None:
            bound.add(k)

print(json.dumps({'wf_required': wf_required, 'task_required': task_required,
                  'bound_at_call': sorted(bound)}))
