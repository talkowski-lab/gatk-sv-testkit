---
name: gatk-sv-testkit
description: >-
  Drives the gatk-sv-testkit (github.com/talkowski-lab/gatk-sv-testkit) for GATK-SV development:
  building a branch's docker images on a throwaway GCE VM with no local Docker, running a Terra
  head-to-head against a frozen baseline run, replaying the Java SV trainers locally on a real
  run's exact inputs, and the static checks (WDL launchability, sv_shell JSON contract, jq null
  plumbing; shipped-image byte identity is a separate checks/image-check that gate does not run).
  Use when someone asks to build a gatk-sv branch's image, "did my
  change alter the pipeline output", compare a branch against the v1.1.1 baseline, run a trainer
  locally, check whether a WDL is launchable, or find the SVShell null from a renamed JSON key.
  Prefers read-only modes; never starts compute or POSTs on its own.
compatibility: >-
  bash 3.2+ and python3 for the wrapper. Needs a gatk-sv-testkit checkout (GSVTK_HOME or a clone)
  plus, per loop: GCS/Terra credentials (ADC) for Terra modes, a local gatk-sv clone + miniwdl +
  jq for the static checks, gcloud in a project for image builds, a JDK 17 GATK jar for local
  replay. All heavy reference docs live in the checkout's docs/, not here.
metadata:
  author: talkowski-lab     # the org that owns the repo this skill drives; the skill now lives
                            # in that repo (.pi/skills/), so a personal handle would be published
  repo: https://github.com/talkowski-lab/gatk-sv-testkit
  version: 0.4.0            # must equal VERSION in scripts/gsvtk; an unstamped skill cannot be
                            # told apart from a stale one -- scripts/selftest.sh checks it
---

# gatk-sv-testkit

Four loops that shorten gatk-sv development: **build** a branch's images without a local Docker
daemon, **compare** a branch against a real baseline run with every input pinned, **replay** the
Java trainers locally on that run's exact inputs, and **check** statically before submitting.

The repo is the documentation. This skill is the front door plus the things that cost time to
learn twice.

## First move: locate, don't guess

Paths below are relative to this skill's directory (`scripts/`, `references/`):

```bash
scripts/gsvtk locate        # checkout path, git state, which tools exist here
scripts/gsvtk tools         # which of the four loops can actually run on this machine
```

Never assume a path, a venv, or that a loop is available. `locate` fails with the exact
`git clone` + `make setup` to run if there is no checkout. `GSVTK_HOME` overrides discovery.

**The only legal working directory for repo-side commands is the one `repo` prints.** A directory
full of testkit-shaped files (`recon.py`, `compare_batch_tables.py`, a `CHECKPOINT.md` left by
some earlier session) that is not that path is a scratch copy: stale code, no profile, no sha,
and state that reads like current plans but is not. Stop and say so. Do not run from it, and do
not treat its notes as the plan.

A checkout counts as one only if it is a git clone of a remote you listed (`GSVTK_TRUSTED_REPOS`,
default `github.com/talkowski-lab/gatk-sv-testkit`); a vendored copy or unlisted fork is refused,
naming the origin it found. `gate`/`build`/`terra` print the resolved `path @ sha` on stderr — if
that line is absent from your transcript, you did not run them.

## Then pick the loop

| The question | Run | Cost |
|---|---|---|
| "Build my branch's sv-pipeline image" | `scripts/gsvtk build <branch>` → shows `--check` then `--dry-run` | free; the real build is 1-3 h of `e2-standard-8` |
| "Is my WDL actually launchable?" | `scripts/gsvtk gate <ref> --wf SVShell` | seconds |
| "Did that rename break SVShell?" | `scripts/gsvtk gate <base-ref>` | seconds |
| "What would my configs bind?" | `scripts/gsvtk terra show` | free, offline |
| "Do those keys even exist in the WDL I am pointing at?" | `scripts/gsvtk terra check --against <ref>` | free, offline, needs a gatk-sv clone |
| "Are the baseline inputs still the bytes I compared?" | `scripts/gsvtk terra verify` | free, re-crc32cs |
| "Did my change alter the output?" | [references/workflows.md](references/workflows.md) §3 — **no `gsvtk compare` exists**: the differ is repo-side (`compare/`, `terra/batch_fetch_compare.sh table`) | one step's VMs |
| "Run this trainer on the real inputs, locally" | `examples/run_train_chr20.sh` in the checkout | free, needs a JDK 17 jar |
| "Is my submission done / what did it cost?" | the `terra-monitor` skill; then `scripts/gsvtk terra status --costs` | free |

## Read-only by construction — and why that matters here

`scripts/gsvtk` dispatches only modes that cannot POST, cannot start compute, cannot bulk-download.
`create`, `submit`, `copy`, `attrs --write`, `fetch`, `profile` — plus `validate`, which reads
like a check but asks Terra to resolve a config — are refused **before** any environment check, so
a refusal never looks like a broken setup. `--allow-shared-target` is the guard on the four
`--confirm`/`--write` tools that stops a write landing in the shared baseline workspace; the repo
side needs it named, not guessed. It covers `rerun create` as well as `rerun submit` — a config
POSTed into the shared workspace is what the next submission reads.

When the user actually wants one of those, do not work around the wrapper silently. Run it in the
checkout, `show` before `submit --confirm`, and say out loud where the write goes, what it costs,
and how to undo it — the exact commands and the pre-flight wording are in
[references/workflows.md](references/workflows.md) §3 and §"If you must run a mutating mode".

The tools have their own gates (`--confirm`, `confirm=True`, `--write`); this is the outer one.
Terra has no per-workspace budget cap, and a whole-chain rerun is a fleet of VMs — prefer one step.

## Ask the config, never read it

```bash
scripts/gsvtk doctor --redact      # what is set/missing + WHERE each value came from, values hidden
cd "$(scripts/gsvtk repo)" && ./kit/gsvtk-config show      # values — real coordinates, terminal only:
                                           # never into a paste, issue, or transcript
```

- Precedence is env > profile > derived > default. An explicit `GSVTK_CONFIG` **replaces** the
  profile chain rather than joining it — that is what makes a zero-config run possible at all.
- `GSVTK_PROJECT` and the Terra workspace pair have **no default**: they decide whose money is
  spent and whose workspace gets written. Exit 4 naming the missing key is correct behaviour.
- Report config state with `--redact`. Real project / workspace / registry coordinates have no
  business ending up in a transcript or a pasted bug report.

## Cheap-first ordering (token and money burn)

1. `show` / `plan` / `--dry-run` / `--check` before anything that writes. They are offline or
   read-only and print exactly what the mutating call would do.
2. **Never `read` a recon or metadata dump.** `work/recon/*.json` and saved Cromwell metadata are
   megabytes to tens of megabytes — `grep`/`jq` them, or use the tool's printed summary. These are
   the single easiest way to destroy a context.
3. **One wait call, not a poll loop.** `batch_status.py --wait 08` blocks; better, hand off to the
   `terra-monitor` skill, which polls inside one script call. Agent-turn polling pays for the whole
   context every 20 seconds.
4. Static checks before submission: `gate` costs seconds; discovering the same bug on step 08 costs
   an hour of VMs plus the debugging.
5. On any error, **grep the checkout's `docs/troubleshooting.md` for the verbatim string** — it is
   keyed on exact error text, which is what you will have. Do not open it cold.

## Exit codes worth branching on

| exit | means | action |
|---|---|---|
| 0 | clean | proceed |
| 1 | findings, or a real failure | read the lines: `checks/` and `compare/` print findings that are not verdicts |
| 2 | usage error / refused mode | fix the invocation, not the job |
| 3 | missing optional dependency (miniwdl, womtool) | install or skip; the tool printed which. For miniwdl this now means **genuinely** absent: `./kit/gsvtk-config miniwdl` resolves `$MINIWDL` → `PATH` → the bin next to the interpreter → `./.venv/bin`, so a copy `make setup` installed is found without activating anything |
| 4 | missing required config, or a checkout that isn't one | name the key/file to the user; do not invent a value |

## Facts that are not guessable

- **`api.terra.bio` does not resolve on every network**; `api.firecloud.org` is the same API and is
  what the tools pin. A `401` from `/api/version` means you reached it and are unauthenticated.
- **The PyPI package is `firecloud`, not `fiss`.** `pip install fiss` fails; that *is* fiss.
- **Python ≥ 3.9** (`kit/gsvtk-config` uses `str.removeprefix`).
- **`checks/` output is a list to diff, not a pass/fail oracle.** Run against unmodified gatk-sv the
  contract check reports ~7 unsupplied reads and the WDL gate a few `IncompleteCall` warnings. The
  finding is the *delta* against your base ref (`--compare-to`, `--strict`).
- **Root entity differs by step**: `09-MergeBatchSites` is `sample_set_set`, `06`/`07`/`08`/`10` are
  `sample_set`. `batch_configs.py show` prints it per config and is the authority.
- **Freezing pins by `crc32c` + byte size** in `manifests/baseline_frozen_inputs.json` — not etag,
  not generation. Baseline inputs are *copied* server-side because your sandbox's pet service
  account cannot read the baseline's bucket.
- **Cost is VM-minutes and job counts, never currency.** Multiply by your own rate; a tree with
  `_missingSubWorkflows` is a floor, and the tool says so.
- **`GSVTK_BATCH` must name a real `sample_set`, and one tool now stops when it does not.**
  `terra/stage_inputs.py --attrs` resolves the configured row and exits 2 naming the rows the
  manifest holds; the failure mode before was selecting nothing and exiting 0, which is
  indistinguishable from an empty manifest.
- **RD cutoffs move when you subsample intervals; SR/PE metrics do not.** Quote RD numbers only
  from a full-interval run, and label which run each number came from.
- **An image built for `linux/amd64` cannot be built natively on Apple Silicon.** That is the whole
  premise of the remote build, not an obstacle to route around with QEMU.
- **A build VM acts as its own service account.** `--impersonate-service-account` changes the API
  caller, not the VM, so it cannot fix a denied push; the fix is the registry bucket grant.
- **GCE serial output is gone once the instance is `TERMINATED`.** Read the log before deleting.
- **`attrs --write` publishes only over a recorded `"verified": true`.** Absent manifest, unreadable
  manifest and failed verify are three different refusals; `--allow-unverified` is the explicit
  escape and prints that it took it. See [references/workflows.md](references/workflows.md) §3.

## What to hand back

Report, with numbers: which loop ran, the exit code, the ref/sha everything was measured against,
any VM or submission id created (and its deletion status), the cost if compute ran, and which of
the four loops is still blocked by a missing dependency. If a doc contradicted the code, say which
line — that is the kind of defect this repo actively wants reported.

[references/workflows.md](references/workflows.md) has the four end-to-end sequences, the mutating
checklist, and the "the number moved" discipline.
