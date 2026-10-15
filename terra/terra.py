"""Thin, safe-by-default wrapper around the FISS python API (PyPI `firecloud`).

Two things this fixes once so every tool gets them right:

  * **API host.** Terra's orchestration API is served on several aliases and
    `api.terra.bio` does not resolve on every network. The `api.firecloud.org`
    alias does, so this module pins fiss to it (override with GSVTK_TERRA_API_ROOT
    or FISS_API_URL). An unauthenticated `GET /api/version` answering 401 is the
    expected *good* sign; NXDOMAIN means you need this override.
  * **Credentials.** Application Default Credentials, i.e. whatever
    `gcloud auth application-default login` established. Nothing is stored here.

A third rule lives here because two tools were wrong without it: the submission and
method-config listings are read from **both** fiss and raw REST, and the disagreement is printed
(`_read_both`). docs/terra-head-to-head.md §8 recorded fiss answering `[]` twice for workspaces that
demonstrably had the data; the code kept trusting it, so the monitoring loop reported "no submission
matching <prefix>" against a workspace holding five.

Read operations are always allowed. Every mutating helper requires an explicit
`confirm=True` argument so nothing is created, updated, or submitted by accident. `submit` refuses a
second thing it was not told: the call-cache choice (`use_call_cache`), because the fiss call it wraps
defaults that field to true and always POSTs it -- see its docstring.

    python -c "import sys; sys.path.insert(0,'terra'); import terra; print(terra.whoami())"
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "kit"))
import config  # noqa: E402

try:
    import firecloud.api as fapi  # the PyPI name is `firecloud`; `fiss` is not a package
except ImportError:      # pragma: no cover - the whole point is the message
    raise SystemExit(
        "missing dependency: firecloud (this IS fiss — `pip install firecloud`).\n"
        "  make setup        # creates .venv with everything the Terra tools need\n"
        "  or: python -m pip install firecloud google-auth        (docs/setup.md)")

TERRA_API = config.get("TERRA_API_ROOT", "https://api.firecloud.org/api/")

# The baseline side of a head-to-head: Terra's public featured GATK-SV workspace,
# the one upstream's own docs send you to. Override with GSVTK_BASELINE_* to
# point at your own frozen run.
BASELINE_NS = config.get("BASELINE_NAMESPACE")
BASELINE_WS = config.get("BASELINE_WORKSPACE")

if os.environ.get("FISS_API_URL"):
    fapi.fcconfig.root_url = os.environ["FISS_API_URL"]
if not fapi.fcconfig.root_url.startswith(TERRA_API):
    print(f"[terra] overriding root_url {fapi.fcconfig.root_url} -> {TERRA_API}", file=sys.stderr)
    fapi.fcconfig.set_root_url(TERRA_API)


class TerraError(RuntimeError):
    pass


_ORIGINAL_EXCEPTHOOK = sys.excepthook


def _friendly_terra_error(exc, value, tb):
    """Print a TerraError as the sentence it already is, instead of a traceback.

    These tools fail for reasons a person can act on -- application-default credentials expired,
    a workspace you are not on, a method config that does not exist yet. TerraError carries that
    sentence, and 20 frames of firecloud/requests above it both hides the sentence and makes the
    toolkit look like the thing that broke. Every other exception still prints normally.
    """
    if isinstance(value, TerraError):
        sys.stderr.write(f"{value}\n")
        raise SystemExit(1)
    try:
        import requests
    except ImportError:                                   # firecloud pulls it in; be defensive
        requests = None
    if requests is not None and isinstance(value, requests.exceptions.ConnectionError):
        sys.stderr.write(
            f"cannot reach Terra at {TERRA_API}\n"
            f"  this network may not resolve api.terra.bio (it has been unreachable here while\n"
            f"  api.firecloud.org, the same API, was fine); check VPN/DNS, or set\n"
            f"  GSVTK_TERRA_API_ROOT. See docs/troubleshooting.md.\n")
        raise SystemExit(1)
    _ORIGINAL_EXCEPTHOOK(exc, value, tb)


sys.excepthook = _friendly_terra_error


def _j(resp: Any, what: str, ok=(200,)) -> Any:
    code = getattr(resp, "status_code", None)
    if code not in ok:
        body = getattr(resp, "text", "")[:400]
        raise TerraError(f"{what}: HTTP {code} {body}")
    try:
        return resp.json()
    except Exception:
        return resp.text


def session():
    """AuthorizedSession for the handful of endpoints fiss does not wrap."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    cred, _ = google.auth.default(scopes=[
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ])
    return AuthorizedSession(cred)


def rest_get(path: str, what: str, params: dict | None = None) -> Any:
    """GET one endpoint through the ADC session, i.e. raw REST rather than through fiss.

    Exists because of the measurement in docs/terra-head-to-head.md §8 (2026-09-22…25, five real
    submissions): the same `GET …/submissions` answered `[]` through fiss and returned 5 submissions
    through this bearer path. Same URL, same application-default credentials, two answers; the
    mechanism was never pinned down, which is why the listing helpers below read BOTH rather than
    picking a favourite client.
    """
    return _j(session().get(f"{TERRA_API}{path}", params=params, timeout=60), what)


def _as_items(d: Any, via: str) -> list:
    """Unwrap the shapes these list endpoints actually answer with, or name the one it sent.

    `GET …/submissions` answers a bare array; the same endpoint with `?limit`/`?offset` answers
    `{"subset": […]}`; `…/methodconfigs` answers a bare array. The old `submissions()` read only
    `d.get("submissions")` — a shape no version of Rawls has been observed to send — so every
    *unexpected* shape decoded as an empty list, which is this repo's named failure class: an empty
    that reads like nothing was wrong.
    """
    out = d if isinstance(d, list) else None
    if out is None and isinstance(d, dict):
        for key in ("submissions", "subset", "methodconfigs", "items"):
            if isinstance(d.get(key), list):
                out = d[key]
                break
    if out is None:
        raise TerraError(f"{via}: unexpected listing response shape "
                         f"({type(d).__name__}: {str(d)[:200]}) — refusing to read that as "
                         "'nothing there'.")
    if out and not isinstance(out[0], dict):
        raise TerraError(f"{via}: listed {len(out)} entries of type "
                         f"{type(out[0]).__name__}, not objects — refusing to walk that.")
    return out


def _read_both(path: str, subject: str, fiss_call, fiss_name: str) -> list:
    """List one endpoint with BOTH clients, prefer raw REST, and never answer empty quietly.

    `fapi.list_submissions` returned `[]` for a workspace holding 5 submissions and
    `fapi.list_workspace_configs` returned `[]` for one holding 1 config, while raw REST on the same
    URL returned the data (docs/terra-head-to-head.md §8, observed 2026-09-22…25). The prose was
    written; the code was not — `batch_status`, `latest_workflow` and `recon` all read the lying
    client, so `gsvtk terra status` would have printed `no submission matching <prefix>` against a
    sandbox with five. So:

      * ask both, every call (one extra request per listing is the price of detecting the lie);
      * when the counts differ, print both numbers and WHICH endpoint answered which, and use REST;
      * when both answer empty, raise naming the endpoint that claimed it — an empty submission
        list and an invisible one are otherwise indistinguishable to the caller.
    """
    rest_via = f"raw REST GET /api/{path}"
    rest = fiss = None
    rest_err = fiss_err = None
    try:
        rest = _as_items(rest_get(path, f"{subject} via {rest_via}"), rest_via)
    except Exception as exc:                       # a 404 and an expired ADC token both land here
        rest_err = exc
    try:
        fiss = _as_items(_j(fiss_call(), f"{fiss_name} {subject}"), fiss_name)
    except Exception as exc:
        fiss_err = exc

    if rest is None and fiss is None:
        raise TerraError(
            f"{subject}: neither client could list it.\n"
            f"  {rest_via}: {type(rest_err).__name__}: {str(rest_err)[:200]}\n"
            f"  {fiss_name}: {type(fiss_err).__name__}: {str(fiss_err)[:200]}")
    source = rest_via
    if rest is None:
        print(f"[terra] {subject}: {rest_via} raised {type(rest_err).__name__} "
              f"({str(rest_err)[:160]}); falling back to {fiss_name} — whose emptiness this repo "
              "has measured as a lie, so treat the count as unconfirmed "
              "(docs/terra-head-to-head.md §8).", file=sys.stderr)
        chosen, source = fiss, fiss_name
    elif fiss is not None and len(fiss) != len(rest):
        print(f"[terra] {subject}: the two endpoints DISAGREE — {rest_via} answered "
              f"{len(rest)}, {fiss_name} answered {len(fiss)}. Using raw REST "
              "(docs/terra-head-to-head.md §8).", file=sys.stderr)
        chosen = rest
    else:
        if fiss is None:
            print(f"[terra] {subject}: {fiss_name} raised {type(fiss_err).__name__} "
                  f"({str(fiss_err)[:160]}); using {rest_via}.", file=sys.stderr)
        chosen = rest

    if not chosen:
        counts = (f"raw REST {len(rest)}" if rest is not None
                  else f"raw REST unavailable ({type(rest_err).__name__})")
        counts += (f", {fiss_name} {len(fiss)}" if fiss is not None
                   else f", {fiss_name} unavailable ({type(fiss_err).__name__})")
        raise TerraError(
            f"{source} listed 0 {subject} ({counts}).\n"
            "  An empty listing is not evidence the workspace is empty: this repo measured the\n"
            "  fiss client answering [] for a workspace that held 5 submissions and 1 method config\n"
            "  while raw REST returned them (docs/terra-head-to-head.md §8). Check the target\n"
            "  against what you can actually read -- recon prints the accessible workspace list,\n"
            "  and `kit/gsvtk-config show` names the file each --ns/--ws came from.")
    return chosen


def whoami() -> str:
    r = session().get(TERRA_API.replace("/api/", "/register/v1/user/info"), timeout=40)
    if r.status_code == 200:
        try:
            return r.json()["userEmail"]
        except Exception:
            pass
    # Fallback: decode the ADC token subject.
    import google.auth
    cred, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/userinfo.email"])
    return getattr(cred, "info", {}).get("email") or getattr(cred, "service_account_email", "?")


def workspace(ns: str = BASELINE_NS, name: str = BASELINE_WS) -> dict:
    return _j(fapi.get_workspace(ns, name), f"get_workspace {ns}/{name}")


def entity_types(ns: str, name: str) -> dict:
    return _j(fapi.list_entity_types(ns, name), f"list_entity_types {ns}/{name}")


def entity_sample(ns: str, name: str, etype: str, page_size: int = 50, page: int = 1) -> dict:
    """One page of entities of `etype` (avoids dumping whole tables).

    Note what this is NOT: the dict is truthy even when the page is empty, so it cannot answer whether one
    named row exists. That question is `entity_exists` below -- and the difference is not academic, see
    there.
    """
    d = _j(fapi.get_entities_query(ns, name, etype, page=page, page_size=page_size,
                                   sort_direction="asc", filter_terms=""),
           f"get_entities_query {etype}")
    return d


def entity_exists(ns: str, name: str, etype: str, entity_name: str) -> bool:
    """Does Terra actually have this row? A point lookup, because a page of rows cannot answer that.

    `entity_sample` above returns one page, and its dict is truthy whether or not the page holds anything
    -- measured on this repo's own reasoning today, where calling it with a row NAME in the `etype` slot
    printed "FOUND" for a row that `get_entity` then answered 404 for. A launcher that believes a row
    exists because a list call returned something is that mistake, so this asks the endpoint that answers
    404. Anything other than 200/404 raises: "could not check" is not evidence a row is there, and a
    submission that cannot be described should not be POSTed.
    """
    resp = fapi.get_entity(ns, name, etype, entity_name)
    if resp.status_code == 200:
        return True
    if resp.status_code == 404:
        return False
    raise TerraError(f"get_entity {etype}/{entity_name}: HTTP {resp.status_code} "
                     f"{str(getattr(resp, 'text', ''))[:140]}")


def entity_attributes(ns: str, name: str, etype: str, entity_name: str) -> dict | None:
    """One row's attributes, from the ONE request that can answer it. `{}`, a dict, or `None` for no row.

    Why this exists when `entity_sample` and `entity_exists` already do: the attributes a submission will
    bind live on the row, and asking for them through the listing costs a page walk of an unbounded table
    (`batch_check_inputs.py` did exactly that until this helper landed) while the point lookup costs one
    GET. `entity_exists` reads the same endpoint and throws the body away, which is the right trade for a
    yes/no question and useless for this one.

    The three answers are the API's, not an interpretation of it:
      * HTTP 200 with an `attributes` object -> that object. An EMPTY one means the row exists and binds
        nothing, which is a different report from "no such row" and gets different text downstream.
      * HTTP 404 -> `None`. This is the only answer allowed to mean absence -- the same rule
        `entity_exists` states, for the same reason: a truthy listing page is not evidence.
      * anything else -> raises. "Could not look" is not absence and not a row, and a check that guesses
        here is the one that bills for a rerun whose inputs were never there.

    Both body shapes are read, and neither is assumed: the workspace reader taught this repo
    (`a21cb3b`) that a report of "no attributes" is usually a one-level-shallow read of an envelope, so a
    200 whose `attributes` sits at the top level and one that nests it under `entity` are both honoured.
    A 200 with neither raises rather than returning `{}` -- that is an unknown shape, not an empty row.
    """
    resp = fapi.get_entity(ns, name, etype, entity_name)
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise TerraError(f"get_entity {etype}/{entity_name}: HTTP {resp.status_code} "
                         f"{str(getattr(resp, 'text', ''))[:140]}")
    body = _j(resp, f"get_entity {etype}/{entity_name}")
    nested = body.get("entity") if isinstance(body, dict) else None
    for obj in (body, nested):
        if isinstance(obj, dict) and isinstance(obj.get("attributes"), dict):
            return obj["attributes"]
    raise TerraError(f"get_entity {etype}/{entity_name} answered 200 with no `attributes` object "
                     f"(top-level keys: {sorted(body)[:6] if isinstance(body, dict) else type(body).__name__}). "
                     "Refusing to read an unrecognised body as 'the row binds nothing'.")


def workspace_configs(ns: str, name: str) -> list:
    """Method configs in a workspace. See `_read_both` for why fiss is not trusted alone."""
    return _read_both(f"workspaces/{ns}/{name}/methodconfigs", f"method configs in {ns}/{name}",
                      lambda: fapi.list_workspace_configs(ns, name), "fapi.list_workspace_configs")


def config_payload(ns: str, name: str, cnamespace: str, config_name: str) -> dict:
    return _j(fapi.get_workspace_config(ns, name, cnamespace, config_name),
              f"get_workspace_config {config_name}")


def submissions(ns: str, name: str, limit: int = 20) -> dict:
    """Recent submissions, newest first, from both clients (see `_read_both`).

    Returns `{"submissions": […]}` because that is the shape `batch_rerun_step status` and
    `batch_check_inputs` already unpack; the fix is in where the items came from, not in the shape.
    Raises rather than returning an empty list.
    """
    items = _read_both(f"workspaces/{ns}/{name}/submissions", f"submissions in {ns}/{name}",
                       lambda: fapi.list_submissions(ns, name), "fapi.list_submissions")
    items = sorted(items, key=lambda s: s.get("submissionDate") or "", reverse=True)[:limit]
    return {"submissions": items}


def submission(ns: str, name: str, sub_id: str) -> dict:
    return _j(fapi.get_submission(ns, name, sub_id), f"get_submission {sub_id}")


def latest_workflow(ns: str, name: str, config_prefix: str) -> dict:
    """Find the newest submission whose method config starts with `config_prefix`.

    Submissions are discovered from the workspace rather than remembered, so nothing has
    to be carried in source between sessions (and a rerun after a failure is correctly
    the one you wanted to look at). Returns {} when submissions exist but none matches;
    an empty *listing* is a raised error, not an empty dict, because that is the case
    where a lying endpoint and an unused workspace look identical.

    A submission can fan out to one workflow per entity; workflowCount is reported so a
    multi-entity run cannot be silently rolled up as if it were one workflow.
    """
    subs = submissions(ns, name, limit=200).get("submissions", [])
    for s in subs:
        cfg = s.get("methodConfigurationName") or ""
        if not cfg.startswith(config_prefix):
            continue
        sub_id = s.get("submissionId")
        full = submission(ns, name, sub_id)
        wfs = full.get("workflows") or full.get("workflowEntities") or []
        if not wfs:
            continue
        wf = wfs[0]
        return {"config": cfg, "submission_id": sub_id, "workflow_id": wf.get("workflowId"),
                "status": wf.get("status"), "workflow_count": len(wfs)}
    return {}


def workspaces(prefix: str | None = None) -> list:
    d = _j(fapi.list_workspaces(), "list_workspaces")
    out = []
    for w in d:
        wd = w.get("workspace", w)
        if prefix and prefix.lower() not in (f"{wd.get('namespace')}/{wd.get('name')}").lower():
            continue
        out.append({"namespace": wd.get("namespace"), "name": wd.get("name"),
                    "id": wd.get("workspaceId"), "created": wd.get("createdDate"),
                    "access": w.get("accessLevel"), "bucket": wd.get("bucketName")})
    return out


def billing_projects() -> list:
    r = fapi.list_billing_projects()
    d = _j(r, "list_billing_projects")
    out = []
    for b in d:
        p = b.get("project", b)
        out.append({"project": p.get("projectName") or p.get("project_id"),
                    "roles": b.get("roles") or p.get("roles"),
                    "status": p.get("status") or (b.get("message"),)})
    return out


# ----------------------------- mutations (opt-in) -----------------------------

def assert_writable_target(ns: str, ws: str, what: str, allow: bool = False) -> None:
    """Refuse to mutate the shared baseline workspace, which is a read-side default.

    GSVTK_BASELINE_NAMESPACE/_WORKSPACE ship as public defaults because everyone READS that
    workspace -- they also look exactly like coordinates you are allowed to use. Writing there is
    a different act: entity attributes MERGE instead of replace, so a stray `attrs --write`
    un-freezes the reference inputs every later comparison is measured against, and there is no
    undo. Same for method configs: whatever you POST there is what the next submission anyone runs
    reads back.
    """
    if allow:
        return
    if ns and ns == BASELINE_NS and ws and ws == BASELINE_WS:
        raise TerraError(
            f"refusing to {what} in the baseline workspace {ns}/{ws}.\n"
            f"  that is the shared reference run, not your sandbox. Terra MERGES entity\n"
            f"  attributes, so writing here silently un-freezes the inputs every later\n"
            f"  comparison is measured against -- and there is no undo.\n"
            f"  check GSVTK_TERRA_NAMESPACE / GSVTK_TERRA_WORKSPACE (kit/gsvtk-config show\n"
            f"  names the file each came from), or pass --allow-shared-target if you really\n"
            f"  mean this workspace.")


def create_workspace(name: str, namespace: str, attributes: dict | None = None,
                     readers: list | None = None, confirm: bool = False) -> dict:
    if not confirm:
        raise TerraError("refusing to create a workspace without confirm=True")
    return _j(fapi.create_workspace(namespace, name, attributes=attributes or {}, authorizationDomain=""),
              f"create_workspace {namespace}/{name}", ok=(200, 201))


def upload_entities_tsv(ns: str, name: str, tsv: str, confirm: bool = False) -> dict:
    if not confirm:
        raise TerraError("refusing to upload entities without confirm=True")
    return _j(fapi.upload_entities_tsv(ns, name, tsv), f"upload_entities_tsv {ns}/{name}", ok=(200, 201))


def submit(ns: str, name: str, config_namespace: str, config_name: str, entity: str,
           entity_type: str, expression: str, confirm: bool = False,
           use_call_cache: bool | None = None) -> dict:
    """Submit one entity, carrying the caller's OWN call-cache choice into the POST.

    `use_call_cache` has no default in substance, even though it has one in the signature. The call
    this wraps, `fapi.create_submission`, defaults `use_callcache=True` and puts the key in the POST
    body unconditionally -- so a caller that says nothing does not get "whatever the method config
    says", it gets `"useCallCache": true` stated by the submission request itself. Measured before
    this parameter existed (offline, capturing the body at fiss's own request boundary):
    `batch_rerun_step.py submit` on the default printed `useCallCache=false` while the body it POSTed
    to `workspaces/<ns>/<ws>/submissions` said `true`. Which of the two values the server honours is a
    question about Terra that nobody here measured; what is measured is that the tool printed one
    answer and asked Terra for the other, on the one field whose wrong value can still look like a
    clean run (docs/terra-head-to-head.md §5, docs/gap-ledger.md C1). So the caller hands over the
    value its own config body states, and print and POST cannot come from two different reads.

    (`delete_intermediate_output_files` is the same shape unfixed and harmless: fiss defaults it False
    and this repo's builder states False, so the two agree by coincidence, not because anything
    threads it. Nothing prints a claim about it, so there is nothing to disagree with.)
    """
    if not confirm:
        raise TerraError("refusing to submit a workflow without confirm=True")
    if use_call_cache is None:
        raise TerraError(
            "refusing to submit with no call-cache choice, because saying nothing is not neutral.\n"
            "  fiss's create_submission defaults use_callcache=True and always sends `useCallCache`,\n"
            "  so an unstated choice POSTs caching ON -- on the one field whose wrong value still looks\n"
            "  like a clean run: a rerun served from a cache did not run the code it came to measure,\n"
            "  and its output still passes. Hand over the value your own config body states, so the\n"
            "  line printed before the POST and the body that reaches Terra cannot disagree:\n"
            "      terra.submit(..., use_call_cache=body['useCallCache'])\n"
            "  Nothing was submitted.")
    return _j(fapi.create_submission(ns, name, config_namespace, config_name,
                                    entity=entity, etype=entity_type, expression=expression,
                                    use_callcache=use_call_cache),
              f"create_submission {config_name}", ok=(200, 201))


def dump(obj: Any, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True, default=str)
    return path
