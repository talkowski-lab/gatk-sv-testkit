# "I have a branch. Does it work on Terra?"

This page is the order of steps for testing a gatk-sv branch on Terra, one command per step, each
linked to the section of [terra-head-to-head.md](terra-head-to-head.md) that explains it.
[quickstart.md](quickstart.md) walks through the same loop with real output.

Everything before row 9 is free and runs in seconds. Row 9 is the only one that starts compute, and
Terra has no per-workspace budget cap, so the earlier rows exist to catch problems before you pay for
them. What each step costs is in
[Cost and blast radius](terra-head-to-head.md#cost-and-blast-radius).

## Configure once

In `testkit.env` ([config.md](config.md), [setup.md](setup.md)) set:

* your cloud project and your Terra workspace (these have no defaults);
* `GSVTK_BRANCH=your-branch-under-test`;
* `GSVTK_NEW_SUFFIX`, the suffix your sandbox's outputs are written under;
* the image your branch built, for example
  `us.gcr.io/YOUR_PROJECT/YOUR_NS/sv-pipeline:your-branch-under-test-abc123`
  ([docker-builds.md](docker-builds.md)).

`./gsvtk doctor --redact` prints what is set, what is missing, and where each value came from,
without printing the values.

Get the suffix right. The default is `new`; a rerun with `--tag rc1` writes `_new_rc1`, and another
chain in the same workspace may use something else. With the wrong suffix every output of your step
reads as absent, which looks like the step never ran. `batch_check_inputs.py` detects this case and
says so: `0 of N expected output(s) under _new, N of N under _h2h ... this is a suffix mismatch and
not a missing input`, naming the suffix in force and where it came from (`[default]` or `[env]`).
Read the suffix off the workspace rather than guessing it.

## The order

| # | Ask this | Run this | Detail |
|---|---|---|---|
| 0 | Can this machine run the loop, and is my branch set? | `./gsvtk doctor --redact` then `./gsvtk tools` | [quickstart.md](quickstart.md) |
| 1 | Will my change start at all: WDL launchable, `sv_shell` keys intact? | `./gsvtk check your-branch-under-test --wf GenotypeBatch` | [static-checks.md](static-checks.md) |
| 2 | Do the config's input keys fit the WDL at my ref? | `python terra/batch_configs.py check` (grades `GSVTK_BRANCH`; `--against REF` for another ref) | [§3](terra-head-to-head.md#3-method-configs) |
| 3 | Build the image the rerun will pin (free preview first) | `./gsvtk build your-branch-under-test` | [docker-builds.md](docker-builds.md) |
| 4 | Is the Dockstore descriptor this config names still my branch tip? | `./gsvtk publish-check your-branch-under-test` (two GETs), or offline: `./gsvtk publish --all-steps --from-file dockstore-capture.json --tip-from-file branch-tip.txt` | [§4](terra-head-to-head.md#4-gate-before-you-submit), [static-checks.md](static-checks.md) |
| 5 | Look at the baseline I will be compared against | `python terra/recon.py` | [§1](terra-head-to-head.md#1-recon-read-only-run-this-first) |
| 5b | Copy its inputs into my bucket and publish the coordinates (once per baseline) | `python terra/batch_freeze.py plan`, then `copy --write`, `verify`, `attrs --write` | [§2](terra-head-to-head.md#2-freeze-the-baseline) |
| 6 | Does my step's config bind files that exist, and attributes that sit on my row? | `python terra/batch_check_inputs.py --step 08` | [§4](terra-head-to-head.md#4-gate-before-you-submit) |
| 7 | What body would be POSTed, and which images are unpinned? | `python terra/batch_rerun_step.py --step 08 --tag rc1 show` | [§3](terra-head-to-head.md#3-method-configs) |
| 7b | The step I want is not in the batch chain? | `./gsvtk --module single_sample terra rerun-show --step 01 --allow-unpinned-docker` (prints the body, POSTs nothing) | [cli.md](cli.md#choosing-the-module---module-name) |
| 8 | POST that one config | `python terra/batch_rerun_step.py --step 08 --tag rc1 create --confirm` | [§3](terra-head-to-head.md#3-method-configs) |
| 8b | Does Terra accept that config against its published WDL? | `python terra/batch_rerun_step.py --step 08 --tag rc1 validate` | [§3](terra-head-to-head.md#3-method-configs) |
| **9** | **Run the one stage I changed** | `python terra/batch_rerun_step.py --step 08 --tag rc1 submit --confirm` | [§5](terra-head-to-head.md#5-run-wait-price) |
| 10 | Did it finish, and what did it cost? | `python terra/batch_status.py --wait 08`, then `python terra/batch_status.py --costs 08` | [§5](terra-head-to-head.md#5-run-wait-price) |
| 11 | Do the numbers match the baseline? | `terra/batch_fetch_compare.sh all --dry-run` | [§6](terra-head-to-head.md#6-fetch-and-compare), [§7](terra-head-to-head.md#7-if-the-numbers-moved) |

Why some rows come before others:

* **Row 2 before row 8.** The input maps describe one branch's WDL signature, while `GSVTK_BRANCH`
  only picks the Dockstore version. If they disagree, Terra accepts the `create` and rejects the
  submission for extra inputs. `check` finds that offline.
* **Row 6 before row 9.** `batch_check_inputs.py` works without a womtool jar (the womtool checks print
  a counted `SKIP`) and is the tool that reads your workspace to confirm the bound attributes exist on
  the row you will submit against. `show` does not read your workspace.
* **Row 7 before row 8.** `show` prints the config name, workflow, root entity, row source, the
  Dockstore pin, and an `image pins:` line saying where each `*_docker` ref came from. Every mode
  refuses while an image is `UNPINNED`.

## Two ways to get an answer that means nothing

1. **The image or the descriptor is not yours.** An unpinned `*_docker` input, or a Dockstore version
   behind your branch tip, gives a successful run of someone else's code. `--allow-unpinned-docker` and
   `--allow-stale-dockstore` override the two checks, and each prints what it let through before the
   POST. Neither overrides a check that could not run: `REFUSED` means the checker declined, not that
   the descriptor is current ([§4](terra-head-to-head.md#4-gate-before-you-submit), and
   `./gsvtk publish --help`).

   Dockstore publishes a branch only while that workflow's `filters.branches` in
   `.github/.dockstore.yml` lists it, and a PR cannot carry that edit. If a rebase drops the filter
   commit, the version name keeps resolving but stops following your commits, and the check reports
   `STALE`. The fix: push a throwaway `<branch>_terra` branch at your tip plus the filter commit, wait
   for Dockstore to sync, and re-check. Or skip Dockstore: upload the step's flattened WDL at your
   commit to the Terra method repository and rerun with `--method agora://...`
   ([Running a branch that is not on Dockstore](terra-head-to-head.md#running-a-branch-that-is-not-on-dockstore)).

2. **A second rerun overwrote the first.** Config names and output attributes are both names, so an
   untagged second rerun of a step overwrites the first one's config and outputs. `--tag rc1` moves
   the config to `08-FilterBatchSamples-rc1` and the outputs to `this.<attr>_new_rc1`, while inputs
   still read the chain's `_frz` / `_new` attributes
   ([Two arms, one variable](terra-head-to-head.md#two-arms-one-variable)).

## Two things that look like breakage and are not

1. **`GSVTK_READ_ONLY=1` refuses mutating modes on purpose.** The shipped agent skill runs with `--read-only`,
   and the CLI then refuses every mode that POSTs, starts compute or writes an attribute, printing what
   it refused and what would have changed. That refusal is not a broken binary or a missing flag. Run
   the mutating steps of this sequence in the live checkout (`./gsvtk ...` or
   `python terra/batch_rerun_step.py ...`) with `--confirm`, and leave the agent read-only. Read-only
   commands (`check`, `publish`, `publish-check`, `rerun-show`, `batch_check_inputs.py`) work either way.

2. **An old copy of the testkit has old bugs.** A copy frozen at an older commit can show failures
   that have since been fixed, such as Rawls rejecting a config for a missing `methodConfigVersion`.
   Work from the live checkout and note the commit you ran (`./gsvtk` prints `repo ... @ <sha>` on
   every command). Pin an older copy only when reproducing an older run is the point, and record that
   you did.

## Related topics

* **Step 09 and other rows.** `09-MergeBatchSites` runs against a `sample_set_set`, so
  `--step 09 submit` refuses until you pass `--entity your-cohort`
  ([§3](terra-head-to-head.md#3-method-configs)).
* **Steps outside the batch chain.** `--module` selects another module's step map. Row 7b prints the
  single-sample step's body:

  ```
  $ ./gsvtk --module single_sample terra rerun-show --step 01 --allow-unpinned-docker
  [rerun] step 01  workflow GATKSVPipelineSingleSample  config 01-GATKSVPipelineSingleSample-rerun
          root entity sample  (row: UNRESOLVED: `entity_row` is --entity, so pass --entity <name>)
  ```

  Without `--module`, the same command fails with `unknown step '01'`. The launch path has the same
  gates as the chain: `submit --confirm` refuses until `--entity` names a `sample` row and every image
  is pinned. Without a frozen copy, a launch outside the chain runs against whatever the workspace
  already holds; read the body before you create it. To freeze a single-sample baseline row, use
  `batch_freeze.py --module single_sample --entity <row>`
  ([Frozen baseline for other flavors](terra-head-to-head.md#frozen-baseline-for-other-flavors)).
* **A run that failed, or a number that moved.** Search [troubleshooting.md](troubleshooting.md) for
  the exact error text. Terra API behavior is in
  [§8](terra-head-to-head.md#8-terra-api-behavior-to-know-about), and deciding whether a moved number is
  a bug, an intended change or an input difference is
  [§7](terra-head-to-head.md#7-if-the-numbers-moved).
* **Cost.** `terra/batch_cost.py` recomputes VM-minutes from saved metadata and refuses a chain total
  when a step is missing ([§5](terra-head-to-head.md#5-run-wait-price)).
* **One task's logs, or one output by name.**
  [§9](terra-head-to-head.md#9-reading-one-tasks-artifacts-and-fetching-an-output-by-its-name).

`./gsvtk publish --ls-remote` reads the branch tip from the remote; `--tip-from-file` reads it from a
file you already have. With `--from-file` and `--tip-from-file`, row 4 needs no network.
