# Goldens for `scripts/selftest.d/` (rerun and profiles)

Two lanes capture bytes here, for the same reason: a refactor that moves data must be able to prove
it moved nothing. Captured from `terra/batch_rerun_step.py` on the commit that introduced this directory, with

    GSVTK_CONFIG=scripts/selftest.d/fixtures/rerun.profile.env   # placeholders only
    GSVTK_WORK=$(mktemp -d)                                       # nothing is written by `show`

and no user configuration. `rerun.sh` re-runs the same command and compares bytes, so these are the
control that says "this refactor changed the config body for nobody": every `*_docker` value, the
input map, the Dockstore URI and `useCallCache` must come out byte-identical.

| file | captured from | why |
|---|---|---|
| `rerun-step10-body.json` | `show --image gatk_docker=… --image sv_base_mini_docker=… --image sv_pipeline_docker=…` (stdout, exit 0) | the config body a step-10 rerun would POST, as bytes |
| `rerun-step10-unpinned.txt` | `show` with no `--image` at all (stderr, exit 1) | the same command refusing. It names ONE key now (`sv_base_mini_docker`): `GSVTK_IMAGE_REPO`/`GSVTK_GATK_IMAGE_REPO` in the fixture are tagged and count as pins, and no profile key exists for `sv_base_mini_docker` or `linux_docker` at all |
| `configs-show.txt` | `terra/batch_configs.py show` under `fixtures/genotyping.profile.env` | the printed binding table, as bytes: what `show` answered before the bindings became `profiles/genotyping.json` |
| `configs-body.json` | `body()` for all five configs, same fixture profile | the 64 input + 36 output bindings, so a binding dropped in the move is a diff and not a rumour (`scripts/golden_configs.py --control` proves the check fires on a planted drop) |

`rerun-step10-unpinned.txt` is compared from its own first line (`image inputs are not fully pinned`)
to the end of the captured stderr: `announce()` later added a resolution block to that stream, and the
cut removes exactly that block and nothing of the refusal's own wording. `cmp`, not a grep, so a
reworded refusal still fails. `rerun-step10-body.json` is compared in full: all 2473 bytes of it
came out of the refactor untouched.

Both pairs were captured on separate branches from the same base, so after a merge they must be
re-captured together against the merged tree: the rerun pair is what proves the profiles lane's loader
did not move `body()`, and neither pair means anything if one was regenerated against a tree the other
never saw. Re-capture by running the lane runner (`rerun.sh`, `profiles.sh`) and reading its failure,
not by regenerating first: a golden is evidence, and reaching for `golden_configs.py --write` before
you have read the diff is how a real change gets approved by accident.

## Re-verified against the merged tree

Done at `e2fefa4`, both pairs, in the order above (run the runner, read it, then regenerate):

| pair | result |
|---|---|
| `rerun-step10-body.json` | `cmp` clean, 2473 bytes, against the merged tree -- which is the profiles lane's own claim (moving 64 + 36 bindings moved no bytes) checked with the rerun lane's fixture, not its own |
| `rerun-step10-unpinned.txt` | CHANGED on purpose: 450 -> 361 bytes, two `unpinned:` lines gone. `_stated()` in `batch_rerun_step.py` was fixed the same day, so a tag-named profile repo now satisfies the guard and only the key no profile can name is left refusing. The pre-fix bytes recorded a defect |
| `configs-show.txt`, `configs-body.json` | regenerated with `--write` and every digest unchanged, which is the idempotence claim: these files are what the merged tree produces, not what some earlier tree produced |

Re-running the check on any later tree: `bash scripts/selftest.d/rerun.sh`, `bash
scripts/selftest.d/profiles.sh`, `python scripts/golden_configs.py` (compare; `--write` only after the diff
has been read, and `--control` to see the comparison fire on a planted dropped binding).

## What `rerun-step10-body.json` grades, and the one thing it cannot

Read with a parser rather than a regex, at this tree. Grepping is the trap here: `grep '"[a-z_]*_docker": *"[^"]*"'`
finds **nothing**, because a pinned image value is quoted *inside* the JSON string (`"\"us.gcr.io/…:TAG\""`), which
is the requirement and not an artefact — so a regex check on this file reports "no images pinned" on a file whose
images are all pinned.

| field | in the committed golden | what that actually proves |
|---|---|---|
| `useCallCache` | `false` | the cache-off policy is a byte of the body, so drift changes bytes the leg compares. Since `6ae8414` this is also the value the submission POST carries — one read, not a second read of argv |
| `deleteIntermediateOutputFiles` | `false` | staging files survive the run; turning that off is a cost decision, so it is pinned rather than left to a default |
| 3 `*_docker` inputs | `"us.gcr.io/your-dev-project/test/{gatk,sv-base-mini,sv-pipeline}:YOUR_TAG"` | **shape only, and shape is what a golden is for**: quoted, tagged, free of `${workspace.*}`. The registry and tag are placeholders from `fixtures/rerun.profile.env`, so nobody should read these as a real pin. The real-coordinate version is Stage D's golden, which carries its workspace, config name, date and capture command with it |
| `methodRepoMethod.methodVersion` | `"your-branch"` | same distinction: the Dockstore triple's shape is pinned, no published version is |
| `use_reference_disks` | **absent** | it is not a config-body field. It is submission-level, `fiss` defaults it `False`, and nothing in this repo threads it — so looking in a config golden for reference-disk behaviour finds nothing, and that absence is the fact rather than a gap in the golden |

The one capture taken from a live workspace does **not** live in this directory: it is at
`recon-live/live-config-01-SingleSample-mwfix.json`, an ignored path. Committed here it turned the gate red — the
coordinate leg refuses "no credential shape and no coordinate of THIS machine at HEAD or in the index", and a real
method config is nothing but coordinates of somebody's workspace. That is the rule working, not a nuisance: my
`git add` of it was the error. Its provenance block (UTC time, the exact call, workspace, config name, namespace)
travels inside the file. It is a config body, not a cost.

Repeating the provenance here, in case that file is ever lost: captured **2026-10-09T16:27:30Z** by
`terra.config_payload(ns, ws, ns, name)` -- `GET /api/workspaces/{ns}/{ws}/method_configs/{ns}/{name}` --
against workspace `GATK-Structural-Variants-Single-Sample-mw-fix966` in namespace
`broad-firecloud-dsde-methods`, config `01-SingleSample-mwfix` in that same namespace: 112 inputs, 15 outputs,
`rootEntityType` `sample`, 14 `*_docker` inputs of which 12 arrive quoted and tagged. For cost, the same workspace holds six finished `pr966-SingleSample` submissions (newest
2026-10-02), and `terra/batch_status.py --costs` is the tool that asks Terra -- but it reads the **configured**
target, so run it with `GSVTK_TERRA_NAMESPACE`/`GSVTK_TERRA_WORKSPACE` pointed at
`broad-firecloud-dsde-methods/GATK-Structural-Variants-Single-Sample-mw-fix966` or it reports on the sandbox in
`testkit.local.env` instead (measured: `--costs` with no prefix, against the default target, exits 4 and names
nothing). Costs of runs that already happened are the cheapest evidence in this repo: read-only, no new spend.

If a later change makes a real coordinate appear in this file, this table is wrong and must be edited in the same
commit: a golden that pins a real dev registry breaks the moment that dev pushes, and the leg would then be
grading someone's laptop rather than the tree.
