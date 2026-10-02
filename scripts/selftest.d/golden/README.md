# Goldens for `scripts/selftest.d/rerun.sh`

Captured from `terra/batch_rerun_step.py` on the commit that introduced this directory, with

    GSVTK_CONFIG=scripts/selftest.d/fixtures/rerun.profile.env   # placeholders only
    GSVTK_WORK=$(mktemp -d)                                       # nothing is written by `show`

and no user configuration. `rerun.sh` re-runs the same command and compares bytes, so these are the
control that says "this refactor changed the config body for nobody": every `*_docker` value, the
input map, the Dockstore URI and `useCallCache` must come out byte-identical.

| file | captured from | why |
|---|---|---|
| `rerun-step10-body.json` | `show --image gatk_docker=… --image sv_base_mini_docker=… --image sv_pipeline_docker=…` (stdout, exit 0) | the config body a step-10 rerun would POST, as bytes |
| `rerun-step10-unpinned.txt` | `show` with no `--image` at all (stderr, exit 1) | the same command refusing, because a `*_docker` left as `workspace.*` resolves to whatever the attribute points at today |

`rerun-step10-unpinned.txt` is compared from its own first line (`image inputs are not fully pinned`)
to the end of the captured stderr: `announce()` later added a resolution block to that stream, and the
cut removes exactly that block and nothing of the refusal's own wording. `cmp`, not a grep, so a
reworded refusal still fails. `rerun-step10-body.json` is compared in full: all 2473 bytes of it
came out of the refactor untouched.
