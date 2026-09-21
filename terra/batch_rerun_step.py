"""Re-run Terra step 10 (GenotypeBatch) against the squashed branch + new images.

Why a separate script: the head-to-head method configs are gone from the sandbox
workspace (list_workspace_configs -> 0), and the image attributes should not be
rewritten in place (they are the provenance of the completed Terra chain), so
this creates ONE config with the docker images as literals and everything else
exactly as batch_configs.py specified it.

    python terra/batch_rerun_step.py show      # read-only
    python terra/batch_rerun_step.py create    # POST config
    python terra/batch_rerun_step.py validate  # Terra-side WDL check
    python terra/batch_rerun_step.py submit    # run it (mutation)
    python terra/batch_rerun_step.py status    # submissions + Cromwell id

Images are pinned explicitly so the run cannot silently inherit a pre-change
image from a workspace attribute. Literal File values must be quoted inside the
expression (Terra parses a bare `us.gcr.io/...` as an unquoted identifier and
rejects it: "The value you entered is not in the correct format for this data
type"), hence the embedded double quotes.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kit"))
sys.path.insert(0, HERE)
import config  # noqa: E402
import terra                      # noqa: E402
from terra import fapi      # noqa: E402  # via terra: one friendly missing-dependency message
import batch_configs as tc          # noqa: E402  (NS/WS are read THROUGH this module)
from batch_configs import body, dockstore  # noqa: E402

CONFIG = "10-GenotypeBatch-rerun"
ENTITY, ETYPE = config.get("BATCH", "all_samples"), "sample_set"

# Image refs are PINNED LITERALLY into the config rather than left as workspace
# attribute references -- that is the whole reason this tool exists separately from
# batch_configs.py. If they stay as `workspace.sv_pipeline_docker`, a rerun silently
# inherits whatever image the attribute happened to point at, and you measure the old
# code. So they are never a *derived* default. Either pass them per call --
#     --image gatk_docker=.../gatk:<tag> --image sv_pipeline_docker=.../sv-pipeline:<tag>
# (see docs/docker-builds.md for building them) -- or name them explicitly in the profile as
# GSVTK_IMAGE_REPO / GSVTK_GATK_IMAGE_REPO. `show` prints the exact resolved config either way.
def parse_images(argv: list[str]) -> dict:
    out = {}
    for spec in argv:
        if "=" not in spec:
            raise SystemExit(f"--image wants key=IMAGE_REF, got {spec!r}")
        key, ref = spec.split("=", 1)
        key = key.split(".")[-1]
        if not key.endswith("_docker"):
            key += "_docker"
        out[f"GenotypeBatch.{key}"] = f'"{ref}"'   # literal File values must be quoted
    return out


def images_from_config() -> dict:
    """Fall back to an image the profile names EXPLICITLY.

    Only an explicit `GSVTK_IMAGE_REPO` / `GSVTK_GATK_IMAGE_REPO` counts. A value merely
    *derived* from the project is not a statement about which code ran, and guessing here is
    exactly the failure this tool exists to prevent -- so a derived default still stops.
    """
    res = config.resolve()
    out = {}
    for key, ck in (("gatk_docker", "GATK_IMAGE_REPO"), ("sv_pipeline_docker", "IMAGE_REPO")):
        value, source = res.get(ck, ("", ""))
        # An untagged registry path is not a pin: `.../sv-pipeline` floats to whatever is current at
        # pull time, so the "which code ran" question has no answer afterwards.
        if value and source in ("env", "profile") and ":" in value.rsplit("/", 1)[-1]:
            out[f"GenotypeBatch.{key}"] = f'"{value}"'
    return out


IMAGES: dict[str, str] = {}
CONFIRMED = False        # set only by an explicit --confirm on the command line
ALLOW_UNPINNED = False   # set only by an explicit --allow-unpinned-docker
WDL_VERSION = os.environ.get("GSV_WDL_VERSION") or config.get("BRANCH")


def dstore(version: str) -> dict:
    """Exact same construction as batch_configs.dockstore(), with a version override.

    Rawls validates this URI on overwrite (a malformed one -> HTTP 404 "Cannot get
    dockstore://... from method repo"), so it has to be byte-exact: every slash in the
    path is %-encoded, including the ones in github.com.
    """
    path = "github.com/broadinstitute/gatk-sv/GenotypeBatch"
    return {"sourceRepo": "dockstore", "methodPath": path, "methodVersion": version,
            "methodUri": f"dockstore://{path.replace('/', '%2F')}/{version}"}


def make_body() -> dict:
    # Resolves the target *now*, and note NS/WS are read through `tc` afterwards: they are
    # module attributes there, so a value that was empty at import time is still picked up.
    tc.require_target()
    b = body("10-GenotypeBatch")
    b["name"] = CONFIG
    b["methodRepoMethod"] = dstore(WDL_VERSION)
    b["inputs"].update(IMAGES)
    # docs/terra-head-to-head.md promises every `*_docker` is pinned literally. A `*_docker` still
    # written as an expression (`workspace.sv_pipeline_docker`) resolves to whatever that attribute
    # points at TODAY, so the rerun does not reproduce the code that ran -- and nothing in its output
    # says so. Fail closed rather than publish a config that quietly breaks the one guarantee this
    # tool exists to provide.
    unpinned = sorted(k for k, v in b["inputs"].items()
                      if k.endswith("_docker") and k not in IMAGES)
    untagged = sorted(k for k, v in IMAGES.items()
                      if ":" not in str(v).strip('\"').rsplit("/", 1)[-1])
    if (unpinned or untagged) and not ALLOW_UNPINNED:
        raise SystemExit(
            "image inputs are not fully pinned -- a submit with these runs whatever the workspace\n"
            "  attribute points at today, which is exactly what this tool exists to prevent:\n"
            + "".join(f"    unpinned:  {k}\n" for k in unpinned)
            + ("" if not untagged else
               "  pinned but untagged (a floating reference; today's bytes are not tomorrow's):\n"
               + "".join(f"    {k} = {str(IMAGES[k]).strip(chr(34))}\n" for k in untagged))
            + "  pass --image KEY=REF for each one (see --help), or --allow-unpinned-docker if you\n"
              "  genuinely want the attribute-resolved images for this rerun.")
    return b


def create() -> None:
    if not CONFIRMED:
        raise SystemExit("create overwrites a method config that submissions read.\n"
                         "  check `show` printed the images you meant, then re-run with --confirm.")
    b = make_body()
    r = fapi.create_workspace_config(tc.NS, tc.WS, b)
    if r.status_code == 409:
        r = fapi.overwrite_workspace_config(tc.NS, tc.WS, tc.NS, CONFIG, b)
    print(f"create {CONFIG}: HTTP {r.status_code} {'' if r.status_code in (200, 201) else r.text[:300]}")
    if r.status_code not in (200, 201):
        raise SystemExit(1)


def validate() -> None:
    r = fapi.validate_config(tc.NS, tc.WS, tc.NS, CONFIG)
    d = r.json() if r.status_code == 200 else {"error": r.text[:300]}
    print(json.dumps(d, indent=1))
    terra.dump(d, str(config.work_dir("metadata") / "rerun_config_validation.json"))
    valid = d.get("invalid") or []
    if valid:
        print("\nINVALID:")
        for v in valid:
            print("  ", v)
        raise SystemExit(1)


def submit() -> None:
    # A joint-calling step is a fleet of VMs, not a function call. `confirm=True` below is
    # terra.py's own belt-and-braces; this is the one that stops an accidental invocation.
    if not CONFIRMED:
        raise SystemExit("submit starts real compute and spends real money.\n"
                         "  re-run with --confirm, and check `show` printed the images you meant.")
    terra.assert_writable_target(tc.NS, tc.WS, "submit a workflow",
                                allow="--allow-shared-target" in sys.argv)
    d = terra.submit(tc.NS, tc.WS, tc.NS, CONFIG, ENTITY, ETYPE, None, confirm=True)
    print(json.dumps(d, indent=1)[:600])
    terra.dump(d, str(config.work_dir("metadata") / "rerun_submission.json"))


def status() -> None:
    tc.require_target()
    subs = terra.submissions(tc.NS, tc.WS, limit=8)["submissions"]
    for s in subs:
        print(" ", s.get("submissionId"), "|", s.get("status"), "|", s.get("submissionDate"),
              "| externalIds:", s.get("externalIds"))


def usage(code=0):
    print("""usage: batch_rerun_step.py [--image KEY=REF ...] [show|create|validate|submit|status]

Reruns one step (10-GenotypeBatch) with the images pinned literally into the config,
so the run cannot inherit a stale image from a workspace attribute.

  --image KEY=REF   repeat once per *_docker input, e.g.
                    --image gatk_docker=us.gcr.io/PROJ/NS/gatk:TAG
                    --image sv_pipeline_docker=us.gcr.io/PROJ/NS/sv-pipeline:TAG
  show              print the config body that would be POSTed (no mutation)
  create / validate POST it, then ask Terra to typecheck it
  submit --confirm  start it (real compute; --confirm is mandatory)
  --allow-unpinned-docker   let a *_docker input resolve from a workspace attribute (off by
                            default: it breaks reproducibility, so it has to be asked for)
  status            recent submissions in the configured workspace""",
          file=sys.stderr if code else sys.stdout)
    raise SystemExit(code)


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        usage()
    # Hand-rolled parsing, because an unknown flag must be a usage error and not a KeyError
    # from a dispatch dict -- and `--image` takes a value that is itself `key=value`.
    specs, argv, confirm = [], [], False
    rest = sys.argv[1:]
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--image":
            if i + 1 >= len(rest):
                raise SystemExit("--image wants a KEY=IMAGE_REF argument")
            specs.append(rest[i + 1]); i += 2
        elif a.startswith("--image="):
            specs.append(a.split("=", 1)[1]); i += 1
        elif a == "--confirm":
            confirm = True; i += 1
        elif a == "--allow-unpinned-docker":
            globals()["ALLOW_UNPINNED"] = True; i += 1
        elif a.startswith("--"):
            raise SystemExit(f"unknown option {a!r}   (--help for the options)")
        else:
            argv.append(a); i += 1

    globals()["IMAGES"] = parse_images(specs)
    globals()["CONFIRMED"] = confirm
    if not globals()["IMAGES"]:
        globals()["IMAGES"] = images_from_config()
        if globals()["IMAGES"] and argv and argv[0] != "show":
            print("[rerun] images taken from the profile (set explicitly):")
            for k, v in sorted(globals()["IMAGES"].items()):
                print(f"          {k.split('.')[-1]} = {v.strip(chr(34))}")
    if not globals()["IMAGES"] and argv and argv[0] != "show":
        raise SystemExit(
            "no --image given and no explicit GSVTK_IMAGE_REPO/GSVTK_GATK_IMAGE_REPO in the profile.\n"
            "  A rerun whose images come from workspace attributes cannot prove which code ran.\n"
            "  pass --image KEY=REF for every *_docker input, or name the repo in the profile.\n"
            "  (docs/terra-head-to-head.md)")
    what = argv[0] if argv else "show"
    if len(argv) > 1:
        raise SystemExit(f"one mode at a time; got {' '.join(argv)!r}   (--help)")
    if what == "show":
        print(json.dumps(make_body(), indent=1))
    elif what in ("create", "validate", "submit", "status"):
        {"create": create, "validate": validate, "submit": submit, "status": status}[what]()
    else:
        usage(2)
