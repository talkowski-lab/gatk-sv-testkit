# Compatibility with gatk-sv

The kit reads gatk-sv's WDLs, Terra templates and sv-shell scripts, so a change upstream can break it.
`compat.json` records which gatk-sv commits it has been checked against, and the README shows the same
list as a table.

## Roles

A gatk-sv ref is checked in one of two roles:

| role | what it is | what must hold |
|---|---|---|
| `branch` | the commit a branch under test is based on (usually `main`) | the layout below, and every module profile agrees with upstream |
| `baseline` | the release the frozen baseline run used (`v1.1.1`) | the layout below, without the sv-shell paths |

A baseline ref is not checked against the profiles: the baseline run's bindings come from its Terra
workspace, and the profiles describe the branch side.

The layout check needs these at the ref: `wdl/<file>.wdl` and a `.github/.dockstore.yml` entry for every
profile step, `inputs/templates/terra_workspaces`, `inputs/values/dockers.json` and
`scripts/inputs/build_inputs.py`. A branch ref also needs the sv-shell driver
(`single_sample_pipeline.sh`) and the `sample_inputs` directory under gatk-sv's `src/sv_shell/`.

## Profile agreement

For a branch ref, `python scripts/derive_profile.py check` runs for every profile in `profiles/` and must
report zero disagreements. A difference the profile explains is listed as `expected` and does not count.
A profile explains a difference with one of these fields, and each needs a `_why_<field>` beside it
([module-profiles.md](module-profiles.md#explaining-a-difference-from-upstream)):

- `unused_callers` (top level): callers upstream wires that this cohort does not run;
- `branch_only_inputs`, `branch_only_outputs`: keys the branch adds;
- `upstream_only_outputs`: upstream outputs the branch removes;
- `template_overrides`: inputs bound here that upstream's template leaves to the WDL default.

## Commands

```bash
./gsvtk compat --ref HEAD                                   # your gatk-sv clone's checkout
./gsvtk compat --ref v1.1.1 --role baseline
./gsvtk compat --ref main --role branch --record            # write the result to compat.json
./gsvtk compat table                                        # the README table, from compat.json
```

`check` reads the clone named by `--repo` or `GSVTK_GATK_SV_CHECKOUT` with git only, so its working tree is
never touched. It exits 0 when compatible and 1 when not, listing each failed check. After `--record`,
regenerate the README table with `./gsvtk compat table` and paste it between the `compat-table` markers;
`make test` fails while the two differ.

`./gsvtk check` and `./gsvtk entity` print one note when your clone's `HEAD` is not a recorded compatible
branch commit, saying how many commits past it you are. It is a warning only.

## Keeping it current

The `compat` CI job runs weekly. It clones gatk-sv and checks `main` as a branch ref and the latest
release tag as a baseline ref, and fails when either is not compatible. When it fails, fix the kit or
the profile, or record the difference with one of the fields above, then rerun with `--record`.
