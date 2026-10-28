# Goldens for `scripts/selftest.d/` (rerun and profiles phases)

Byte-for-byte captures of what the Terra tools would POST or print, so a refactor that moves data can
show it changed nothing. All values are placeholders from the fixture profiles in
`scripts/selftest.d/fixtures/`; nothing here is a real registry, workspace or image pin.

| file | captured from | compared by |
|---|---|---|
| `rerun-step10-body.json` | `terra/batch_rerun_step.py show --image gatk_docker=… --image sv_base_mini_docker=… --image sv_pipeline_docker=…` (stdout, exit 0) | `rerun.sh`, in full |
| `rerun-step10-unpinned.txt` | the same `show` with no `--image` (stderr, exit 1): the refusal naming `sv_base_mini_docker`, the one key no profile setting pins | `rerun.sh`, from the line `image inputs are not fully pinned` to the end |
| `configs-show.txt` | `terra/batch_configs.py show` | `scripts/golden_configs.py` |
| `configs-body.json` | `body()` for all five genotyping configs (64 input and 39 output bindings) | `scripts/golden_configs.py` |

The rerun pair is captured with `GSVTK_CONFIG=scripts/selftest.d/fixtures/rerun.profile.env` and a
throwaway `GSVTK_WORK`; the configs pair with `fixtures/genotyping.profile.env`. `show` makes no
requests and writes nothing.

## Reading `rerun-step10-body.json`

Use a JSON parser, not grep. A pinned image value is a quoted string inside the JSON string
(`"\"us.gcr.io/…:TAG\""`), so a regex for `"…_docker": "…"` finds nothing even when every image is
pinned.

| field | value | meaning |
|---|---|---|
| `useCallCache` | `false` | call caching is off in the body, and the submission uses the same value |
| `deleteIntermediateOutputFiles` | `false` | intermediate files survive the run |
| three `*_docker` inputs | `us.gcr.io/your-dev-project/test/…:YOUR_TAG` | shape only: quoted, tagged, no `${workspace.*}` |
| `methodRepoMethod.methodVersion` | `your-branch` | shape of the Dockstore triple, not a real version |
| `use_reference_disks` | absent | a submission-level option, not part of a config body |

## Updating

When a leg fails, run the phase (`rerun.sh`, `profiles.sh`) and read the diff first. Regenerate only
when the change is intended: `python scripts/golden_configs.py --write` for the configs pair, or
re-capture the rerun pair with the commands above. `python scripts/golden_configs.py --control` shows
the check failing on a planted dropped binding. If the two pairs are touched on different branches,
re-capture both against the merged tree.

Never commit a config captured from a live workspace here. It is all workspace coordinates, and
`make audit` will fail on it. Keep such captures in an ignored directory (`recon-live/` is
conventional) with their provenance inside the file.
