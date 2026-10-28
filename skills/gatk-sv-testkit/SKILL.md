---
name: gatk-sv-testkit
description: >-
  Drives the gatk-sv-testkit (github.com/talkowski-lab/gatk-sv-testkit) for GATK-SV development:
  building a branch's docker images on a throwaway GCE VM with no local Docker, running a Terra
  head-to-head against a frozen baseline run, replaying the Java SV trainers locally on a real
  run's exact inputs, and the static checks (WDL launchability, sv_shell JSON contract, jq null
  plumbing; shipped-image byte identity is a separate checks/image-check that `check` does not
  run).
  Use when someone asks to build a gatk-sv branch's image, "did my
  change alter the pipeline output", compare a branch against the v1.1.1 baseline, run a trainer
  locally, check whether a WDL is launchable, or find the SVShell null from a renamed JSON key.
  Prefers read-only modes; never starts compute or POSTs on its own.
compatibility: >-
  Needs a gatk-sv-testkit checkout whose ./gsvtk is on PATH as `gsvtk` (a symlink is fine), bash
  3.2+, and per loop: GCS/Terra credentials (ADC) for Terra modes, a local gatk-sv clone + miniwdl +
  jq for the static checks, gcloud in a project for image builds, a JDK 17 GATK jar for local
  replay. The full documentation is in the checkout's docs/.
metadata:
  author: talkowski-lab
  repo: https://github.com/talkowski-lab/gatk-sv-testkit
---

# gatk-sv-testkit

Four loops for gatk-sv development: **build** a branch's images without local Docker, **compare** a
branch against a frozen baseline run on Terra, **replay** GATK-based stages locally on a real run's
exact inputs, and **check** WDL and `sv_shell` changes statically before submitting anything.

The checkout's `docs/` is the full documentation. This file covers where to start and what is safe to
run unattended.

## First: locate the checkout

Run every command as `gsvtk --read-only ...`. If `gsvtk` is not on PATH but you are in a checkout,
use `./gsvtk --read-only ...`.

```bash
gsvtk --read-only locate        # checkout path, git state, which tools exist
gsvtk --read-only tools         # which of the four loops can run on this machine
```

Don't assume a path, a venv or that a loop is available. If there is no checkout, tell the user to
clone it, run `make setup`, and link `gsvtk` onto PATH (`docs/setup.md`). Subcommands, flags and exit
codes are in `gsvtk --help` and the checkout's `docs/cli.md`.

Every repo-running subcommand prints `repo <path> @ <sha>` on stderr; if that line is missing, the
command did not run. Read files only from the directory `gsvtk --read-only repo` prints.

## Pick the loop

| Question | Run | Cost |
|---|---|---|
| "Build my branch's image" | `gsvtk --read-only build <branch>` (runs `--check`, then `--dry-run`) | free; the real build is 1-3 h of `e2-standard-8` |
| "Is my WDL launchable?" | `gsvtk --read-only check <ref> --wf <Workflow>` | seconds |
| "Did a JSON rename break sv_shell?" | `gsvtk --read-only check <base-ref>` | seconds |
| "What would my method configs bind?" | `gsvtk --read-only terra show` | free, offline |
| "Do those inputs exist in the WDL at this ref?" | `gsvtk --read-only terra check --against <ref>` | free, offline, needs a gatk-sv clone |
| "Which entity row would each launch config run against?" | `gsvtk --read-only entity --repo <gatk-sv clone> --ref <ref>` | seconds, offline |
| "Does the kit support my gatk-sv version?" | `gsvtk --read-only compat --ref HEAD` (checked refs: `gsvtk --read-only compat table`) | seconds, offline |
| "Are the frozen baseline inputs unchanged?" | `gsvtk --read-only terra verify` | free |
| "Did my change alter the output?" | [references/workflows.md](references/workflows.md) section 3, then `gsvtk --read-only compare <name>` (`compare --list` for names) | one stage's VMs |
| "Run this stage locally on the real inputs" | `gsvtk --read-only replay preflight`, then e.g. `gsvtk --read-only replay train-chr20` | free; needs JDK 17 and a GATK jar |
| "Is my submission done, what did it cost?" | `gsvtk --read-only terra status --costs` | free |

## Read-only by construction

Under `--read-only` the CLI runs only modes that cannot POST, start compute or bulk-download.
`create`, `submit`, `copy`, `attrs --write`, `fetch` and `profile` are refused, and so is `validate`, because it asks Terra to resolve a config. Each is refused before any dependency or config check, with exit 2, the mode named, and the exact command for a person to run in the checkout.

Never drop `--read-only` and never pass `--confirm` yourself. A person in the checkout runs those modes
with `./gsvtk ... --confirm`, which first prints what gets created, where, what it costs and how to
undo it. When the user wants one, say where the write goes, what it costs and how to undo it, and let
them run it after reviewing `show` or `plan`. The steps are in
[references/workflows.md](references/workflows.md) section 3 and "If you must run a mutating mode".

The tools keep their own gates too (`--confirm`, `--write`); `--confirm` never replaces a `--write`.
`--allow-shared-target` is required before any of the Terra writers will touch the shared baseline
workspace, including `rerun-create`. Never add it to get past an error without telling the user.
Terra has no per-workspace budget cap, so prefer rerunning one step over the whole chain.

## Configuration

```bash
gsvtk --read-only doctor --redact      # what is set or missing, and where each value came from, values hidden
```

- Precedence: environment > profile files > derived > default. `GSVTK_CONFIG` replaces the profile
  files with one file.
- `GSVTK_PROJECT` and the Terra workspace pair have no default. Exit 4 naming the key is correct; ask
  the user, never invent a value.
- Report config with `--redact`. Real project, workspace and registry names don't belong in a
  transcript or bug report.

## Keep it cheap

1. Run `show`, `plan`, `--check` or `--dry-run` before anything that writes.
2. Don't read recon dumps (`work/recon/*.json`) or saved Cromwell metadata whole; they are large. Use
   the tool's summary, `grep` or `jq`.
3. Wait with one blocking call (`gsvtk --read-only terra status --wait 08`) rather than polling from the
   conversation.
4. Run the static checks before any submission.
5. On an error, grep the checkout's `docs/troubleshooting.md` for the exact message.

## Exit codes

| exit | meaning | action |
|---|---|---|
| 0 | clean | proceed |
| 1 | findings, or a real failure | read the output; findings are not verdicts on their own |
| 2 | usage error or refused mode | fix the invocation |
| 3 | missing dependency | the tool names it; install it or skip that loop |
| 4 | missing required config | name the key to the user |

Comparators: 0 same, 1 different, 2 nothing compared.

## Facts worth knowing

- `api.terra.bio` does not resolve on every network; the tools use `api.firecloud.org`, the same API.
  A 401 from `/api/version` means you reached it unauthenticated.
- The PyPI package for FISS is `firecloud`, not `fiss`.
- Python 3.9 or later.
- `checks/` output is a diff against a base ref, not pass/fail. Unmodified gatk-sv already has
  findings; use `--compare-to` and compare counts.
- Root entity type differs by step (`09-MergeBatchSites` is `sample_set_set`; 06, 07, 08 and 10 are
  `sample_set`). `gsvtk --read-only terra show` prints it per config.
- Frozen baseline inputs are pinned by crc32c and size. They are copied into the sandbox because its
  pet service account cannot read the baseline bucket.
- Cost is reported as VM-minutes and job counts, never money.
- `GSVTK_BATCH` must name a real `sample_set`.
- RD cutoffs change when intervals are subsampled; SR/PE metrics do not. Quote RD numbers only from a
  full-interval run.
- `linux/amd64` images cannot be built natively on Apple Silicon, which is why builds run on a VM.
- A build VM pushes as its own service account; `--impersonate-service-account` cannot fix a denied push.
- GCE serial output is lost once an instance is deleted. Read the log first.
- `attrs --write` requires a recorded `"verified": true` from `verify`. `--allow-unverified` overrides
  that and says so; tell the user if you suggest it.

## What to report back

Which loop ran, the exit code, the ref and sha it ran against, any VM or submission id created and
whether it was deleted, the cost if compute ran, and which loops are blocked by a missing dependency.
If a doc contradicts the code, say which line.

[references/workflows.md](references/workflows.md) has the end-to-end sequences and the checklist for
mutating modes.
