# Setup

What has to be installed and authorized before each part of the kit can run. `./gsvtk tools` reports
which parts are usable on your machine and where each dependency was found.

## Python

```bash
make setup                                               # ./.venv with requirements.txt
.venv/bin/python -m pip install -r requirements-dev.txt  # miniwdl, jinja2, flake8, pytest
source .venv/bin/activate                                # optional; the tools find ./.venv themselves
```

Python 3.9 or later is required. `checks/` and `kit/` use only the standard library, except that the
WDL checkers need miniwdl. `requirements.txt` holds what the tools import:

| Package | Needed by | Note |
|---|---|---|
| `firecloud` | everything in `terra/` | this is FISS; `pip install fiss` fails, the PyPI name is `firecloud` |
| `google-auth` | `terra/terra.py` | authenticated sessions |
| `numpy`, `pandas`, `pysam` | some of `compare/` | |

Optional tools:

| Tool | Needed by | Install |
|---|---|---|
| `miniwdl` | `checks/wdl_gate.sh`, `checks/wdl_inputs_check.py`, `terra/wdl_flat.py`, `replay/build_inputs.py`, the required-input layer of `checks/terra_entity_check.py` | `requirements-dev.txt`. The CLI tools find it via `$MINIWDL`, `PATH`, the interpreter's bin directory or `./.venv/bin` (`./kit/gsvtk-config miniwdl` prints the one chosen). The Python checkers import it, so their interpreter needs it |
| `jinja2` | `checks/wdl_gate.sh`, to render gatk-sv's input JSONs (the `MISSING-INPUTS` column) | in `requirements-dev.txt`. Without it that column is reported `SKIPPED` |
| `flake8` | `make flake` | `requirements-dev.txt` |
| `pytest` | `make pytest` and the `unit` phase of `make test` | `requirements-dev.txt` |
| `jq` | `checks/svshell_jq_plumbing_scan.py` | `brew install jq` or `apt-get install jq` |
| womtool jar | `terra/batch_check_inputs.py`, the womtool comparison in `checks/wdl_gate.sh` | download womtool and set `WOMTOOL_JAR=/path/to/womtool.jar` (keep it outside the repo) |
| `bcftools` | some of `compare/`, local replay | your package manager |
| [`gatk-sv-profile`](https://github.com/broadinstitute/gatk-sv-profile) | paired profiling in `terra/batch_fetch_compare.sh` | `pip install -e <checkout>`, then `export PROFILE_BIN=...` |
| JDK 17+ and a built GATK jar | local replay | see [local replay](local-replay.md) |

Without the dev requirements, `make test` reports the checks that need them as named `SKIP` lines.
Some selftests also skip until `GSVTK_GATK_SV_CHECKOUT` points at a gatk-sv clone
([config.md](config.md)).

### Matching production's package versions

gatk-sv pins its Python package versions in its dockerfiles. `scripts/prod_pins.py` reads them from a
gatk-sv clone at a ref with `git show`, without touching the clone's working tree:

```bash
./scripts/prod_pins.py --repo "$GSVTK_GATK_SV_CHECKOUT" --ref origin/main
./scripts/prod_pins.py --repo "$GSVTK_GATK_SV_CHECKOUT" --ref origin/main --requirements venv-prod.txt
```

The `pysam` pin matters most: a newer local pysam can produce
`[E::vcf_format] Invalid BCF, the INFO tag id=16 is too large` when a header-added INFO tag goes
through `bcftools sort` on stdin (see [troubleshooting.md](troubleshooting.md)). The tool refuses an
unresolvable ref, a non-git `--repo`, a missing dockerfile, and a package pinned to two different
versions.

## Google Cloud

Needed for `docker/gatk-sv-build.sh` and `checks/image-check/`.

```bash
gcloud auth login                         # credentials for gcloud itself
gcloud auth application-default login     # Application Default Credentials, used by the Python tools
```

`gcloud` and the Python tools use separate credentials. Everything in `terra/` authenticates with ADC.

Your identity in `GSVTK_PROJECT` needs:

- `roles/compute.instanceAdmin.v1` to create, describe and delete the VM;
- `roles/iam.serviceAccountUser` on the VM's service account;
- permission to read serial port output, which is the build's only log channel.

The VM's service account needs push rights to your registry. For GCR that is a grant on the bucket
`us.artifacts.<project>.appspot.com`; the build script prints the exact
`gcloud storage buckets add-iam-policy-binding` command in its preflight. That grant often needs a
project admin.

`--impersonate-service-account` changes who calls the API, not the identity the VM runs as, so it
cannot fix a denied push. Use `--service-account <an account that can push>` or the bucket grant.

Check everything without creating anything:

```bash
docker/gatk-sv-build.sh --check my-branch
```

## Terra

`api.terra.bio` does not resolve on some networks. The same API answers at `api.firecloud.org`, which
is the default `GSVTK_TERRA_API_ROOT`. A 401 from it means you reached it and are not authenticated:

```
$ curl -s -o /dev/null -w '%{http_code}\n' https://api.firecloud.org/api/version
401
```

Check your identity and access:

```bash
python terra/recon.py      # read-only: identity, billing projects, workspaces, a sample of the baseline model
```

`recon.py` writes JSON dumps under `$GSVTK_WORK/recon/` and prints the workspaces you can reach. Your
email is hidden unless you pass `--show-identity`. Review the dumps before attaching any of them to an
issue.

### Which workspace

`GSVTK_TERRA_NAMESPACE` / `GSVTK_TERRA_WORKSPACE` must be a workspace you own. Clone the featured
GATK-SV joint-calling workspace into your own namespace and point the tools at the clone. The tools
create method configs, write entity attributes and submit jobs there.

A cloned workspace has its own Google project, and its pet service account cannot read the original
workspace's bucket. That is why `terra/batch_freeze.py` copies the baseline inputs into your bucket
instead of referencing them.

`GSVTK_BASELINE_NAMESPACE` / `GSVTK_BASELINE_WORKSPACE` point at the reference run and are only read.
They default to the public featured workspace,
`broad-firecloud-dsde-methods/GATK-Structural-Variants-Joint-Calling`, which GATK-SV's own
documentation links to. Override them to use a run you froze yourself.

## Storage

`gsutil` uses your ADC. These public buckets are read in place:

- `gs://gatk-sv-resources-public`: gatk-sv hg38 resources
- `gs://gatk-sv-ref-panel-1kg-v1-1`: the public 1KG reference panel
- `gs://gcp-public-data--broad-references`: hg38 FASTA and dictionary

Buckets named `gs://fc-…` belong to a Terra workspace and are readable only by its members and their
pet service accounts.

## Checking the whole setup

```bash
make test                                 # offline: no credentials needed
./kit/gsvtk-config doctor                 # configuration completeness
python terra/recon.py                     # Terra access (read-only)
docker/gatk-sv-build.sh --check my-branch # GCP access (read-only)
```

## The agent skill

The repo ships an agent skill in the Agent Skills format (a `SKILL.md` with `name` and `description`
frontmatter), so its instructions are versioned with the code:

```
skills/gatk-sv-testkit/
├── SKILL.md                # which loop answers which question, and what is safe unattended
└── references/workflows.md # end-to-end sequences and the checklist for mutating modes
```

The skill runs `gsvtk` from PATH. Link it once; `gsvtk` follows the link back to the checkout, so it
works from any directory, such as a gatk-sv checkout:

```bash
mkdir -p ~/.local/bin && ln -sfn "$PWD/gsvtk" ~/.local/bin/gsvtk
```

Then link the skill into each agent tool you use. Symlinks keep the skill and the code in step when you
`git pull`:

```bash
ln -sfn "$PWD/skills/gatk-sv-testkit" ~/.claude/skills/gatk-sv-testkit      # Claude Code
ln -sfn "$PWD/skills/gatk-sv-testkit" ~/.pi/agent/skills/gatk-sv-testkit    # pi
ln -sfn "$PWD/skills/gatk-sv-testkit" ~/.codex/skills/gatk-sv-testkit       # Codex
```

The skill tells the agent to run every command as `gsvtk --read-only ...`, which refuses every mode that
submits, boots a VM or bulk-downloads, and never to pass `--confirm`. An agent can still ignore its
instructions, or run a tool script directly, so for a hard guarantee deny `--confirm` in your agent
tool's permission settings. In Claude Code, that is a deny rule in `~/.claude/settings.json`:

```json
{ "permissions": { "deny": ["Bash(*--confirm*)"] } }
```

`make test` runs `scripts/check_skill.py` to keep the skill and the CLI consistent.

