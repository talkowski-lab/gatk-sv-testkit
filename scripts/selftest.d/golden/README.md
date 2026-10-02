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
| `rerun-step10-unpinned.txt` | `show` with no `--image` at all (stderr, exit 1) | the same command refusing, because a `*_docker` left as `workspace.*` resolves to whatever the attribute points at today |
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
