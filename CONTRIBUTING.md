# Contributing

## Setup and tests

```bash
make setup                                               # ./.venv with requirements.txt
.venv/bin/python -m pip install -r requirements-dev.txt  # miniwdl, jinja2, flake8, pytest
make test                                                # the offline test suite
```

`make test` must pass with no credentials, no data and no network. It runs every phase even when one
fails, then prints `make test: PASS (offline gate)` or `FAIL`. Without `requirements-dev.txt`, the
phases that need those tools print a named `SKIP` instead of running; CI installs both files.

| phase | what it checks |
|---|---|
| `syntax` | `bash -n` on every `.sh`, `py_compile` on every `.py` |
| `undefmods` | attribute access on a module the file never imports (`os.path` with no `import os`) |
| `flake` | pyflakes only (`flake8 --select=F`): undefined names, missing imports, unused values |
| `helpsweep` | every tool's `--help` works with zero configuration |
| `smoke` | a few tools run end to end on empty fixtures |
| `unit` | `pytest tests/`, except the wrapper around each tool's `--selftest` |
| `audit` | the tracked files hold no credential shape and none of this machine's own coordinates |
| `selftest` | the phase scripts listed below |

Other targets:

```bash
make pytest                  # everything under tests/, including each tool's built-in --selftest
make verify-commit REV=HEAD  # run the suite against a commit in a throwaway worktree
make audit V=1               # the audit alone, showing what it read
make help                    # all targets
```

Pin an interpreter with `make test PYTHON=/path/to/python`. By default make uses `python3` if it can
import the runtime dependencies, otherwise `./.venv/bin/python`.

## Where tests live

- **`tests/`**: pytest. `tests/conftest.py` provides `clean_env()`, which strips `GSVTK_*` from the
  environment so a test never reads your own configuration.
- **`scripts/selftest.sh`**: the `config` selftest phase. It covers the config layer, the checkers'
  parsing, `scripts/probe_fixes.py`, the agent skill (`scripts/check_skill.py`) and the docs
  (`scripts/check_docs.py`, `scripts/check_doc_flags.py`).
- **`scripts/selftest.d/*.sh`**: the other selftest phases, one script each: `cli` (`./gsvtk`),
  `rerun`, `abort`, `profiles`, `womtool`, `reach`, `jarshape` and `entity`. Each takes the interpreter
  as its first argument, prints its own tally and exits nonzero on failure. Run one with
  `bash scripts/selftest.d/cli.sh .venv/bin/python`. Phases are wired in `SELFTEST_PHASES` in the
  `Makefile`.
- **`scripts/selftest.d/fixtures/`** and **`scripts/selftest.d/golden/`**: fixtures and byte-for-byte
  goldens. Each has a README with its provenance.
- **`scripts/probe_fixes.py`**: one probe per fixed defect. Add a function and a `PROBES` entry, and
  raise the pinned count in `scripts/selftest.sh`.
- Many tools also carry a `--selftest` flag with their own fixtures.

Keep assertions in these scripts, not in `Makefile` recipes: make expands `$(...)` inside a recipe, which
can turn an assertion into one that never runs.

### Writing a check or test

- **A test needs a positive control.** Show that the guarded path was reachable, with a case that would
  fail if the guard were removed. Otherwise a guard that can never fire passes forever.
- **Fakes must have the real object's shape.** A fake for `fiss` returns something that behaves like a
  `requests.Response` (a real `status_code`, a `json()` that raises on an empty body), not a dict.
- **An empty input is not a clean result.** A tool that read nothing must say so with its own exit code.
  Comparators exit 2 when nothing was compared; `fetch_outputs.py` uses 3 for "not declared" and 4 for
  "declared but not produced".
- **A skipped check is reported as skipped**, named and counted, never folded into the pass count.

## Layout

```
gsvtk       command-line entry point
checks/     static checks: WDL launchability and semantics, sv_shell JSON contract, published images
compare/    comparators for tables, matrices, VCFs, sets, JSON and archives
docker/     build and push a branch's images on a cloud VM
terra/      the Terra loop: recon, baseline freezing, method configs, reruns, status, cost, fetch
replay/     rebuild a launchable inputs JSON from a captured run
examples/   worked local-replay drivers, kept as recipes
kit/        the config layer every tool reads (bash 3.2, no dependencies)
profiles/   one JSON per module: which WDL each step runs and what it binds (read only by kit/module_profile.py)
scripts/    the audit, doc and skill checks, the WDL fetcher, and the selftest phases
tests/      pytest
docs/       one page per task
skills/     the agent skill (SKILL.md and references/)
```

## The bar for a new tool

A tool should save real waiting time or catch a bug before it costs VM money. It should answer one
question you can state in a sentence ("does this WDL bind its inputs?"). If an existing tool almost
covers it, extend that one instead: two tools computing the same value tend to drift apart.

## Configuration

Any environment-specific value goes in `DEFAULTS` in `kit/gsvtk-config`, in the same change that reads
it. Tools must not read `GSVTK_*` settings from the environment directly: no
`os.environ.get("GSVTK_…")`, and no `$GSVTK_…` in a shell script that has not sourced `kit/config.sh`.
Give the key a default, or a reason it has none. Only `GSVTK_PROJECT` and the Terra workspace pair have
none, because a guessed value would bill or overwrite someone else's resources. Add a row to
[docs/config.md](docs/config.md) in the same change.

## Mutating vs read-only

Read-only by default. Anything that writes outside the machine requires an explicit, typed confirmation.

- Recon, status, cost, and every `checks/` and `compare/` tool never write outside `GSVTK_WORK` and never
  submit.
- Mutators (`create`, `copy`, `attrs`, `submit`, builds, image probes) refuse without `--confirm` or
  `--write`. Writes to Terra refuse the shared baseline workspace unless `--allow-shared-target`. Every
  Terra mode resolves the namespace and workspace before its first request and exits 4 if either is
  missing.
- Before starting compute, a tool prints what it will boot: project, zone, machine type and its timeout.
  `--dry-run` and `--check` print the plan and touch nothing. No tool quotes a price.
- A new mutator needs a guard and a probe in `scripts/probe_fixes.py` showing the guard fires.
- **No `make` target may boot compute or change remote state.** Those paths are typed by hand.

Two more conventions:

- **Name the missing thing.** Fail with the key or file that is missing, the fix, and an exit code. No
  tracebacks and no silent empty results.
- **Checker findings are a diff.** Upstream gatk-sv already has findings, so the checkers take a
  baseline (`--compare-to <ref>` for the jq scan, a base ref for `checks/wdl_gate.sh`) rather than
  asserting zero.

### Exit codes

Use the shared codes: 0 clean, 1 findings or failure, 2 usage error or refused, 3 missing dependency,
4 missing required config. Comparators: 0 same, 1 different, 2 nothing compared.

## No personal identifiers

Nothing committed may contain your project id, registry path, bucket, workspace bucket, home path,
submission or workflow UUID, or a credential. Use a `<placeholder>` and, where the reader needs it, a
short legend saying where the real value comes from (`./kit/gsvtk-config show`, the Terra UI).

`make audit` enforces this on the git-tracked files. It checks credential shapes (service-account
addresses, private keys, home paths) everywhere, and on your machine it also checks for the values your
own configuration resolves to. CI can only check the shapes, so run `make audit` locally before
pushing. It scans tracked files only, so `git add` new files first. If you need the audit to learn an
extra private string, put it in `audit.local.txt` (gitignored), not in `scripts/audit.py`. Only
machine-independent patterns belong in `SHAPES` there.

## Docs

- Every command in `README.md` and `docs/*.md` is checked by `scripts/check_doc_flags.py`: the tool must
  exist, be runnable as written, and accept the flags shown. `scripts/check_docs.py` checks fences and
  relative links. Both run in `make test`.
- Only document a command you have run.
- Put error messages verbatim in [docs/troubleshooting.md](docs/troubleshooting.md) so people can
  search for them.
- Say near the top of a page what costs money and what is read-only.

## Style

`kit/` is loaded by everything: keep it small, dependency-free and bash 3.2 compatible (the macOS
default). Prefer the standard library in `compare/`. External binaries (`jq`, `bcftools`, `gsutil`) are
fine if the tool says what is missing and how to install it; `terra/terra.py`'s guarded `firecloud`
import shows the pattern. Comments explain why, not what.
