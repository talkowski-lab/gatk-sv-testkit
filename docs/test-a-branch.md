# "I have a branch. Does it work on Terra?"

One screen: the order to ask things in, **one command per step**, and a link to the section of
[terra-head-to-head.md](terra-head-to-head.md) that explains each. This page is deliberately a route
and not a third copy — the *why* for every step is that guide (and it is 40-odd kilobytes of it, paid
for in billed hours), the loop-by-loop walkthrough with real output is
[quickstart.md](quickstart.md) loops 4 and 6. If a sentence here explained something instead of
pointing at it, it should have been a link.

Everything above row 9 is free. Row 9 is the only one that boots a fleet, and Terra has no
per-workspace budget cap, which is the entire reason for the order: rows 0-8 refuse you for free, in
seconds, on a laptop. What each step costs, including the write-once ones, is
[Cost and blast radius](terra-head-to-head.md#cost-and-blast-radius).

Set once before any of it: `testkit.env` with your cloud project and your Terra workspace — the two
keys with no default, yours to name ([config.md](config.md), [setup.md](setup.md)) — plus
`GSVTK_BRANCH=your-branch-under-test`, **`GSVTK_NEW_SUFFIX=<the suffix this sandbox's outputs actually
sit under>`**, and the image that branch built
(`us.gcr.io/YOUR_PROJECT/YOUR_NS/sv-pipeline:your-branch-under-test-abc123`;
[docker-builds.md](docker-builds.md) builds it). `./gsvtk doctor --redact` prints what is set, what is
missing and where each value came from, without printing any value.

That suffix is not a cosmetic knob, and getting it wrong is the quietest way to answer nothing: it
decides whether your outputs are found at all. The default is `new`; a rerun run with `--tag rc1`
writes `_new_rc1` instead, and a second chain in the same workspace may sit under something else
entirely. Set it to the wrong value and every output of your step reads as **ABSENT**, which looks like
"the step never ran" and sends you to fetch files that exist. Since #4 the tools say so instead of
misleading you — `batch_check_inputs.py` prints `0 of N expected output(s) under \`_new\`, N of N under
\`_h2h\` ... this is a suffix mismatch and not a missing input`, naming the suffix in force and where it
came from (`[default]` vs `[env]`, the same provenance `gsvtk doctor --redact` prints). Read the suffix
off the workspace rather than guessing it.

## The order

| # | Ask this | Run this | Detail |
|---|---|---|---|
| 0 | Can this machine run the loop, and is my branch set? | `./gsvtk doctor --redact` then `./gsvtk tools` | [quickstart.md](quickstart.md) §0-1 |
| 1 | Will my change even *start* — WDL launchable, `sv_shell` keys intact? | `./gsvtk check origin/main --wf GenotypeBatch` | [static-checks.md](static-checks.md) |
| 2 | Do the config's input keys still fit the WDL at my ref? | `python terra/batch_configs.py check --against main` | [§3 Method configs](terra-head-to-head.md#3-method-configs) |
| 3 | Build the image the rerun will pin (free preview first) | `./gsvtk build your-branch-under-test` | [docker-builds.md](docker-builds.md) |
| 4 | Is the Dockstore descriptor this config names still my branch tip? | `./gsvtk publish-check your-branch-under-test` (reads Dockstore and the tip, both GETs) — or offline: `./gsvtk publish --all-steps --from-file dockstore-capture.json --tip-from-file branch-tip.txt` | [§4](terra-head-to-head.md#4-gate-before-you-submit), [static-checks.md](static-checks.md) |
| 5 | Freeze the baseline I will be compared against (once per baseline) | `python terra/recon.py` | [§1](terra-head-to-head.md#1-recon-read-only-run-this-first) |
| 5b | …and copy its inputs into *my* bucket, then publish the coordinates | `python terra/batch_freeze.py plan` | [§2](terra-head-to-head.md#2-freeze-the-baseline) |
| 6 | Does my step's config bind files that exist, and attributes that actually sit on my row? | `python terra/batch_check_inputs.py --step 08` | [§4](terra-head-to-head.md#4-gate-before-you-submit) |
| 7 | What body would be POSTed for my step, and which images are unpinned? | `python terra/batch_rerun_step.py --step 08 --tag rc1 show` | [§3](terra-head-to-head.md#3-method-configs) |
| 8 | POST that one config | `python terra/batch_rerun_step.py --step 08 --tag rc1 create --confirm` | [§3](terra-head-to-head.md#3-method-configs) |
| 8b | Does Terra accept that config against its published WDL, before a fleet boots? | `python terra/batch_rerun_step.py --step 08 --tag rc1 validate` | [§3](terra-head-to-head.md#3-method-configs) |
| **9** | **Run the one stage I changed** | `python terra/batch_rerun_step.py --step 08 --tag rc1 submit --confirm` | [§5](terra-head-to-head.md#5-run-wait-price) |
| 10 | Did it finish, and what did it cost? | `python terra/batch_status.py --costs 08` | [§5](terra-head-to-head.md#5-run-wait-price) |
| 11 | Do the numbers match the baseline? | `terra/batch_fetch_compare.sh all --dry-run` | [§6](terra-head-to-head.md#6-fetch-and-compare), [§7](terra-head-to-head.md#7-if-the-numbers-moved) |

Three of those rows are the whole method, so they are worth naming:

* **Row 2 before row 8.** The input maps are a snapshot of one branch's WDL signature while
  `GSVTK_BRANCH` only picks the Dockstore URL; point them at different refs and Rawls rejects the
  whole config as an extra input *at submission* — after `create` returned 200, so the workspace holds
  a config that looks created. `check` is that rejection, offline, in seconds.
* **Row 6 before row 9.** `batch_check_inputs.py --step 08` answers with **no womtool jar present** —
  check 1 and the required-set half of check 3 print a named, counted `SKIP` and everything else still
  runs — and it is the tool that proves the attributes your config binds actually sit on the row it
  submits against. `show` cannot see your workspace and does not claim to; this can.
* **Row 7 before row 8.** `show` prints the config name, the workflow, the root entity and the row
  source, the Dockstore pin it grades, and one `image pins:` line naming where each `*_docker` ref
  came from (`--image`, `profile`, `profile/env`, `UNPINNED`). A rerun refuses every other mode over
  an `UNPINNED` image, because an attribute-resolved image is not a statement about which code ran.

## The two ways this answers nothing anyway

Both are silent, both are cheap to avoid, and both are worked in the guide rather than here:

1. **The image or the descriptor is not yours.** An unpinned `*_docker`, or a Dockstore version that
   is behind your branch tip, produces a green run of somebody else's code — or a rerun that disagrees
   with the baseline for a reason nobody can reproduce, while still billing. The escapes are
   `--allow-unpinned-docker` and `--allow-stale-dockstore`; each prints, before the POST, what it let
   through, and neither silences a check that declined to answer. `CURRENT / STALE / ABSENT / REFUSED`
   are four answers for a reason: a capture that never saw your entry says `ABSENT` about it, and a
   check that could not run is `REFUSED`, not an all-clear
   ([§4](terra-head-to-head.md#4-gate-before-you-submit), and `./gsvtk publish --help` for the four).
   Dockstore publishes a branch only while that entry's `filters.branches` in `.github/.dockstore.yml`
   lists it — and a PR cannot carry that edit — so when a rebase drops the filter commit the version
   name keeps resolving while its commit stops moving. `STALE` prints the cure: push a throwaway
   `<branch>_terra` at the tip plus the filter commit, wait for the sync, re-check.
2. **Your second rerun overwrote your first.** A config POST and an output attribute are both *names*,
   so a second untagged rerun of one step POSTs over the first config and writes over the first run's
   attributes, and the comparison ends up reading one run against itself. `--tag rc1` moves exactly
   two names — the config (`08-FilterBatchSamples-rc1`) and the outputs (`this.<attr>_new_rc1`) — and
   nothing else: the inputs still read the chain's existing `_frz`/`_new` attributes, which is what
   lets two arms answer one question from one input
   ([Two arms, one variable](terra-head-to-head.md#two-arms-one-variable)).

## Two things that look like breakage and are not

Both cost an agent a day on 2026-10-13 by being read as tool failures. Neither is one.

1. **`GSVTK_READ_ONLY=1` refuses mutating modes on purpose.** The shipped skill's shim exports it, and
   with it the CLI refuses every mode that POSTs, boots compute, or writes an attribute — the refusal
   prints what it refused and what would have changed. That is the outer gate doing its job on a
   machine where an accidental submission spends someone else's money: it is not a broken binary, not a
   missing flag, and not a bug to route around. Run the mutating steps of this sequence in the live checkout
   (`./gsvtk ...` / `python terra/batch_rerun_step.py ...`) with `--confirm`, where the refusal does not
   apply, and leave the shim read-only. Read-only commands (`check`, `publish`, `publish-check`,
   `rerun-show`, `batch_check_inputs.py`) work under it either way — a GET and a file-read are nothing to
   refuse.
2. **A frozen copy of the testkit is a different tool than this one.** Clone-or-freeze at an older sha
   and you inherit bugs this repo has already fixed — the recorded case is an agent treating
   `rerun-create`'s Rawls 400 on a missing `methodConfigVersion` as a live blocker when the fix had
   already landed, and losing a day to a bug that no longer existed. Work from the live checkout, and
   note the sha you ran (`./gsvtk` prints it in its `repo ... @ <sha>` stamp on every command). Freeze
   a copy deliberately, for reproducibility, only when matching an *older* run is the point of the
   exercise — and then say so in the record, because a comparison between two shas answers a different
   question than the one that looks the same on one sha.

## Not on this page, on purpose

* **Which row, and why not what you'd guess.** `09-MergeBatchSites` runs against `sample_set_set`, so
  `--step 09 submit` refuses until you pass `--entity your-cohort`; it will not guess a cohort, because
  a guessed row is the accepted submission that binds nothing
  ([§3](terra-head-to-head.md#3-method-configs)).
* **Single-sample / participant-rooted stages are not rerunnable here** — by decision, not omission:
  the freeze loop cannot write a participant entity, so there is no frozen row to rerun against
  ([module-profiles.md](module-profiles.md)).
* **A run that failed, or a number that moved.** Grep [troubleshooting.md](troubleshooting.md) for the
  verbatim error text — it is keyed on exactly that string — and the API edges with the exact text each
  produces are [§8](terra-head-to-head.md#8-api-edges-that-bite-with-the-exact-text-each-produces).
  Deciding whether a moved number is your bug, an intended change, or an input you didn't control is
  [§7](terra-head-to-head.md#7-if-the-numbers-moved).
* **Cost as a number.** `terra/batch_cost.py` recomputes VM-minutes from saved metadata and refuses a
  chain total when a step's metadata is missing; no document here quotes a price, because your billing
  account is not this one ([§5](terra-head-to-head.md#5-run-wait-price)).
* **One task's logs, or one output by name.**
  [§9](terra-head-to-head.md#9-reading-one-tasks-artifacts-and-fetching-an-output-by-its-name).

`./gsvtk publish --ls-remote` and `--tip-from-file` differ in one thing only: the first reads the
remote, the second reads a file you already have. Rows 0-8 are offline from a capture; if you are
asked to prove this loop works without network, that is the flag to leave off.
