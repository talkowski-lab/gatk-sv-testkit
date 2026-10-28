"""Thin, safe-by-default wrapper around the FISS python API (PyPI `firecloud`).

Two things this fixes once so every tool gets them right:

  * **API host.** Terra's orchestration API is served on several aliases and
    `api.terra.bio` does not resolve on every network. The `api.firecloud.org`
    alias does, so this module pins fiss to it (override with GSVTK_TERRA_API_ROOT
    or FISS_API_URL). An unauthenticated `GET /api/version` answering 401 is the
    expected *good* sign; NXDOMAIN means you need this override.
  * **Credentials.** Application Default Credentials, i.e. whatever
    `gcloud auth application-default login` established. Nothing is stored here.

Submission and method-config listings are read from both fiss and raw REST (`_read_both`),
because fiss has been seen to return `[]` for workspaces that hold data.

Read operations are always allowed. Every mutating helper requires an explicit
`confirm=True`. `submit` also requires an explicit `use_call_cache`.

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

# Baseline workspace for head-to-head comparisons; override with GSVTK_BASELINE_*.
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
    """Print TerraError and connection errors as one message instead of a traceback."""
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
    """GET one endpoint through the ADC session (raw REST, bypassing fiss)."""
    return _j(session().get(f"{TERRA_API}{path}", params=params, timeout=60), what)


def _as_items(d: Any, via: str) -> list:
    """Unwrap a listing response (bare array or `{"subset": […]}` etc.); raise on any other shape."""
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


def _read_both(path: str, subject: str, fiss_call, fiss_name: str,
               params: dict | None = None) -> list:
    """List one endpoint with both raw REST and fiss, preferring REST.

    Prints a warning when the counts differ, and raises if the chosen listing is empty, since fiss
    has been seen to return `[]` for workspaces that hold data.
    """
    rest_via = f"raw REST GET /api/{path}"
    rest = fiss = None
    rest_err = fiss_err = None
    try:
        rest = _as_items(rest_get(path, f"{subject} via {rest_via}", params), rest_via)
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
              "(docs/terra-head-to-head.md).", file=sys.stderr)
        chosen, source = fiss, fiss_name
    elif fiss is not None and len(fiss) != len(rest):
        print(f"[terra] {subject}: the two endpoints DISAGREE — {rest_via} answered "
              f"{len(rest)}, {fiss_name} answered {len(fiss)}. Using raw REST "
              "(docs/terra-head-to-head.md).", file=sys.stderr)
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
            "  Either the workspace really has none, or the listing is wrong: the fiss client can\n"
            "  answer [] for a populated workspace (docs/terra-head-to-head.md). `recon` prints the\n"
            "  workspaces you can read, and `kit/gsvtk-config show` names the file each --ns/--ws\n"
            "  came from.")
    return chosen


def whoami() -> str:
    r = session().get(TERRA_API.replace("/api/", "/register/v1/user/info"), timeout=40)
    if r.status_code == 200:
        try:
            return r.json()["userEmail"]
        except Exception:
            pass
    # Fallback: read the email from the ADC credentials.
    import google.auth
    cred, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/userinfo.email"])
    return getattr(cred, "info", {}).get("email") or getattr(cred, "service_account_email", "?")


def workspace(ns: str = BASELINE_NS, name: str = BASELINE_WS) -> dict:
    return _j(fapi.get_workspace(ns, name), f"get_workspace {ns}/{name}")


def entity_types(ns: str, name: str) -> dict:
    return _j(fapi.list_entity_types(ns, name), f"list_entity_types {ns}/{name}")


def entity_sample(ns: str, name: str, etype: str, page_size: int = 50, page: int = 1) -> dict:
    """One page of entities of `etype`. Use `entity_exists` to test for a single row."""
    d = _j(fapi.get_entities_query(ns, name, etype, page=page, page_size=page_size,
                                   sort_direction="asc", filter_terms=""),
           f"get_entities_query {etype}")
    return d


def entity_exists(ns: str, name: str, etype: str, entity_name: str) -> bool:
    """True on 200, False on 404; any other status raises."""
    resp = fapi.get_entity(ns, name, etype, entity_name)
    if resp.status_code == 200:
        return True
    if resp.status_code == 404:
        return False
    raise TerraError(f"get_entity {etype}/{entity_name}: HTTP {resp.status_code} "
                     f"{str(getattr(resp, 'text', ''))[:140]}")


def entity_attributes(ns: str, name: str, etype: str, entity_name: str) -> dict | None:
    """One row's attributes via a point lookup: a dict (possibly empty), or `None` on 404.

    Accepts `attributes` at the top level or nested under `entity`; any other status or body shape raises.
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
    """Method configs in a workspace. See `_read_both` for why fiss is not trusted alone.

    `allRepos=true` is required: without it Terra lists only method-repository (Agora) configs and
    leaves out every Dockstore config, which is all of GATK-SV's.
    """
    return _read_both(f"workspaces/{ns}/{name}/methodconfigs", f"method configs in {ns}/{name}",
                      lambda: fapi.list_workspace_configs(ns, name, allRepos=True),
                      "fapi.list_workspace_configs", params={"allRepos": "true"})


def config_payload(ns: str, name: str, cnamespace: str, config_name: str) -> dict:
    return _j(fapi.get_workspace_config(ns, name, cnamespace, config_name),
              f"get_workspace_config {config_name}")


def submissions(ns: str, name: str, limit: int = 20) -> dict:
    """Recent submissions, newest first, as `{"submissions": […]}`. Raises on an empty listing."""
    items = _read_both(f"workspaces/{ns}/{name}/submissions", f"submissions in {ns}/{name}",
                       lambda: fapi.list_submissions(ns, name), "fapi.list_submissions")
    items = sorted(items, key=lambda s: s.get("submissionDate") or "", reverse=True)[:limit]
    return {"submissions": items}


def submission(ns: str, name: str, sub_id: str) -> dict:
    return _j(fapi.get_submission(ns, name, sub_id), f"get_submission {sub_id}")


def latest_workflow(ns: str, name: str, config_prefix: str) -> dict:
    """Find the newest submission whose method config starts with `config_prefix`.

    Returns {} when none matches (an empty listing raises). `workflow_count` is reported because a
    submission can fan out to one workflow per entity.
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
    """Refuse to mutate the shared baseline workspace unless `allow` is set.

    Terra merges entity attributes rather than replacing them, so a write there can't be undone.
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
    """Submit one entity. `use_call_cache` is required.

    `fapi.create_submission` defaults `use_callcache=True` and always sends it, so the caller must
    pass the value from its own config body.
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
