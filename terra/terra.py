"""Thin, safe-by-default wrapper around the FISS python API (PyPI `firecloud`).

Two things this fixes once so every tool gets them right:

  * **API host.** Terra's orchestration API is served on several aliases and
    `api.terra.bio` does not resolve on every network. The `api.firecloud.org`
    alias does, so this module pins fiss to it (override with GSVTK_TERRA_API_ROOT
    or FISS_API_URL). An unauthenticated `GET /api/version` answering 401 is the
    expected *good* sign; NXDOMAIN means you need this override.
  * **Credentials.** Application Default Credentials, i.e. whatever
    `gcloud auth application-default login` established. Nothing is stored here.

Read operations are always allowed. Every mutating helper requires an explicit
`confirm=True` argument so nothing is created, updated, or submitted by accident.

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
    """One page of entities of `etype` (avoids dumping whole tables)."""
    d = _j(fapi.get_entities_query(ns, name, etype, page=page, page_size=page_size,
                                   sort_direction="asc", filter_terms=""),
           f"get_entities_query {etype}")
    return d


def workspace_configs(ns: str, name: str) -> list:
    return _j(fapi.list_workspace_configs(ns, name), f"list_workspace_configs {ns}/{name}")


def config_payload(ns: str, name: str, cnamespace: str, config_name: str) -> dict:
    return _j(fapi.get_workspace_config(ns, name, cnamespace, config_name),
              f"get_workspace_config {config_name}")


def submissions(ns: str, name: str, limit: int = 20) -> dict:
    d = _j(fapi.list_submissions(ns, name), f"list_submissions {ns}/{name}")
    items = d.get("submissions", []) if isinstance(d, dict) else d
    items = sorted(items, key=lambda s: s.get("submissionDate") or "", reverse=True)[:limit]
    return {"submissions": items}


def submission(ns: str, name: str, sub_id: str) -> dict:
    return _j(fapi.get_submission(ns, name, sub_id), f"get_submission {sub_id}")


def latest_workflow(ns: str, name: str, config_prefix: str) -> dict:
    """Find the newest submission whose method config starts with `config_prefix`.

    Submissions are discovered from the workspace rather than remembered, so nothing has
    to be carried in source between sessions (and a rerun after a failure is correctly
    the one you wanted to look at). Returns {} when no submission matches.

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
           entity_type: str, expression: str, confirm: bool = False) -> dict:
    if not confirm:
        raise TerraError("refusing to submit a workflow without confirm=True")
    return _j(fapi.create_submission(ns, name, config_namespace, config_name,
                                    entity=entity, etype=entity_type, expression=expression),
              f"create_submission {config_name}", ok=(200, 201))


def dump(obj: Any, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True, default=str)
    return path
