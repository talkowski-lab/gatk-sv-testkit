# Prerequisites and credentials

What has to exist before any of these tools can do anything, and what each failure looks like.

## Python

```bash
make setup            # creates .venv and installs requirements.txt
source .venv/bin/activate
```

Python 3.9+ (`kit/gsvtk-config` uses `str.removeprefix`, added in 3.9; `kit/config.sh`'s interpreter
probe enforces the same floor). The pure-python tools — `checks/` and `kit/` — need nothing beyond
the standard library, so they run on a bare interpreter with no venv at all. `compare/` is not one
of them: three of its six tools import `numpy`, one `pandas`, one `pysam`. The Terra tools need two third-party packages:

| Package | Needed by | Note |
|---|---|---|
| `firecloud` | everything in `terra/` | this **is** fiss. `pip install fiss` fails: the PyPI name is `firecloud` |
| `google-auth` | `terra/terra.py` sessions | comes with `firecloud` in practice |

Extras you opt into:

| Tool | Needs | Install |
|---|---|---|
| `checks/wdl_gate.sh`, `terra/wdl_flat.py --check`, `replay/build_inputs.py` | `miniwdl` | `python -m pip install -r requirements-dev.txt`. It is a *package*: `build_inputs.py` needs the interpreter that has it. The two CLI users resolve it themselves (`$MINIWDL` → `PATH` → the bin next to the interpreter → `./.venv/bin`), because a venv your shell never activated is still where `make setup` put it — see `./kit/gsvtk-config miniwdl` |
| `make flake` | `flake8` (used only as a pyflakes runner, `--select=F`) | `python -m pip install -r requirements-dev.txt`. `make test` SKIPs it with this named when it is absent, and CI is where it is enforced |
| `checks/svshell_jq_plumbing_scan.py` | `jq` on PATH | `brew install jq` / `apt-get install jq` |
| `terra/batch_check_inputs.py` | a womtool jar | set `WOMTOOL_JAR=/path/to/womtool.jar` |
| `compare/*` (some) | `bcftools`, `pysam`, `pandas` | `bcftools` via your package manager; the rest in requirements |
| `compare/profile_summarize.py` | `numpy`, `pandas` | reads `gatk-sv-profile`'s bucketed `.tsv.gz` tables |
| paired profiling | [`gatk-sv-profile`](https://github.com/broadinstitute/gatk-sv-profile) | `pip install -e <checkout>`, then `export PROFILE_BIN=...` |
| local replay | JDK 17+, a built GATK jar | see [local replay](local-replay.md) |

## Google Cloud

```bash
gcloud auth login                                  # for gcloud itself
gcloud auth application-default login              # for the python tools (ADC)
gcloud config set project <your-project>           # convenience; tools pass --project explicitly
```

Two separate credentials exist on a workstation and only the second one matters here: `gcloud`
uses its own, the python tools use **Application Default Credentials**. Everything in `terra/`
authenticates as you, via ADC.

For `docker/gatk-sv-build.sh` and `checks/image-check/`, your identity in that project needs:

- `roles/compute.instanceAdmin.v1` (create/start/describe/delete the VM)
- `roles/iam.serviceAccountUser` plus `actAs` on the VM's service account
- the ability to read serial port output (that is the only log channel)

And the **VM's** service account needs push rights to your image registry, which is a bucket-scoped
grant on `us.artifacts.<project>.appspot.com`. The build script prints the exact
`gcloud storage buckets add-iam-policy-binding` command on every preflight, and separately warns
if the bucket is not listable by you, because that grant usually needs a project admin rather than
you.

Run `docker/gatk-sv-build.sh --check <branch>` first: it exercises every read path and the
registry bucket without creating anything.

### A note on `--impersonate`

`gcloud compute instances create --impersonate-service-account` changes who *calls the API*, not
the identity the VM runs as. If a push is denied, adding it is a misleading non-fix: the error
names the missing permission on the **VM's** service account, and the fix is either
`--service-account <an SA that can push>` or the bucket grant.

## Terra

The API host is the first thing that bites:

```
curl: (6) Could not resolve host: api.terra.bio
```

`api.terra.bio` does not resolve on some networks. The same service answers on
`api.firecloud.org`, which is what `terra/terra.py` pins (`GSVTK_TERRA_API_ROOT`). A good sign
that you reached it and are simply not authenticated:

```
$ curl -s -o /dev/null -w '%{http_code}\n' https://api.firecloud.org/api/version
401
```

401 there is correct. NXDOMAIN is the network problem.

Check your identity and access through the tools rather than the console:

```bash
python -c "import sys; sys.path.insert(0,'terra'); import terra; print(terra.whoami())"
python terra/recon.py                            # read-only: identity, billing, workspaces
```

### Which workspace

`GSVTK_TERRA_NAMESPACE`/`_WORKSPACE` must be a workspace **you** own. Clone the featured GATK-SV
joint-calling workspace into your own namespace and point the tools at the clone:

- the harness creates method configs, writes new entity attributes and submits jobs;
- a cloned workspace gets its own Google project, and Cromwell localizes `gs://` with the
  **clone's pet service account**, which cannot read the original workspace's bucket. That is
  why `batch_freeze.py` copies the baseline inputs server-side instead of referencing them.

Baseline (`GSVTK_BASELINE_*`) stays pointed at the reference run and is only ever read.

## Storage

`gsutil` runs under your ADC. Publicly readable and used in place:

- `gs://gatk-sv-resources-public` — gatk-sv hg38 resources
- `gs://gatk-sv-ref-panel-1kg-v1-1` — the public 1KG reference panel
- `gs://gcp-public-data--broad-references` — hg38 fasta/dict

Anything under `gs://fc-…` belongs to a workspace and is only readable by that workspace's
members and pet service accounts.

## Verifying the whole picture

> `recon.py` is read-only against Terra, but it writes eight JSON dumps under
> `$GSVTK_WORK/recon/` and its stdout names your Terra e-mail (redacted unless
> `--show-identity`) and every workspace you can reach. Attach `recon/*.json` to an issue
> only after you have looked at what they list, and prefer describing the failure over
> pasting the inventory.

```bash
make test                                 # offline: syntax, pyflakes, --help, real runs, selftests
./kit/gsvtk-config doctor                 # profile completeness
python terra/recon.py                     # read-only: identity, billing, workspaces, baseline model
docker/gatk-sv-build.sh --check <branch>  # read-only preflight against GCP
```

`recon.py` is the right first call on a new account: it dumps who you are, which billing projects
you can charge, which workspaces you can see, and a bounded sample (a few entities, not whole
tables) of the baseline workspace's model. It creates nothing.

## The agent skill, if you drive this repo from one

The repo ships the skill that drives it, so the instructions version with the code instead of
living in someone's home directory:

```
.pi/skills/gatk-sv-testkit/
├── SKILL.md                # which loop answers which question, and what is safe unattended
├── scripts/gsvtk           # read-only front door: locate / doctor / tools / gate / build / terra
└── references/workflows.md # the four end-to-end sequences + the mutating checklist
```

[pi](https://github.com/badlogic/pi-mono) loads it from `.pi/skills/` once the project is trusted;
other harnesses read `.agents/skills/`, so symlink rather than copy if you need both. To use it from
*other* projects (the usual case — you are usually standing in gatk-sv, not here):

```bash
ln -sfn "$PWD/.pi/skills/gatk-sv-testkit" ~/.pi/agent/skills/gatk-sv-testkit
```

The wrapper finds the checkout on its own (`GSVTK_HOME` overrides) and refuses to treat a
flattened pile of testkit files as a checkout: a directory is accepted only if it is a git clone of
a remote named in `GSVTK_TRUSTED_REPOS`. `make selftest` runs `scripts/check_skill.py` against it —
version stamp, frontmatter, and the refusal list *executed* rather than quoted — because a skill
that quietly drifts from the code is worse than no skill: an agent follows it.
