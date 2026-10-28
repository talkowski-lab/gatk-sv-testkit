# Module profiles

A module profile is a JSON file, `profiles/<module>.json`, that describes one chain of gatk-sv Terra
steps: which WDL each step runs, the complete input and output maps its method config posts, which
entity row it runs against, and which images it pins. The Terra tools in this repo (`terra/`) read
every per-module fact from the profile, so supporting a different chain means writing a profile
rather than editing code.

Two profiles ship:

| Module | Steps | Notes |
|---|---|---|
| `genotyping` (default) | `06-GenerateBatchMetrics`, `07-FilterBatchSites`, `08-FilterBatchSamples`, `09-MergeBatchSites`, `10-GenotypeBatch` | The head-to-head chain. Callers `manta`, `wham`, `scramble`. Step 10 carries the `export` list the fetch reads. |
| `single_sample` | `01-GATKSVPipelineSingleSample` | One step rooted on a `sample` entity. No callers, no `export`, resources stated as `literals`, images supplied with `--image`. |

`kit/module_profile.py` is the only reader. It validates the file, expands the `{frz}`/`{new}` suffix
tokens and the `@` caller fan-out, and hands the result to its consumers. No shell script parses
`profiles/` directly.

## Choosing a module

The module is a config-layer key, `MODULE` (default `genotyping`), and the directory profiles are read
from is `MODULE_DIR` (default `<repo>/profiles`). Both resolve through the normal config precedence
(see [config.md](config.md)), so `GSVTK_MODULE=single_sample` in the environment or a profile env file
selects the other module.

For one command, use the wrapper's global flag, which exports `GSVTK_MODULE` for the child tool:

```bash
./gsvtk --module single_sample terra rerun-show --step 01
```

The module decides which step numbers exist. Under the default module, `--step 01` is refused as an
unknown step; under `single_sample` it resolves.

`./kit/gsvtk-config show` prints which module was chosen, where that value came from, and whether the
profile file exists.

## Inspecting a profile

`kit/module_profile.py` is offline and read-only.

```bash
python kit/module_profile.py                              # summary of the default module
python kit/module_profile.py --module single_sample --print   # every binding, expanded, with its kind
python kit/module_profile.py --print-exports              # export names, one per line, for shell callers
```

The summary prints the callers, one line per step (`wdl`, `workflow`, root entity, row source), and a
count of input and output bindings split into paths and literals. `--print` adds each expanded binding
labelled `path` or `literal`, then each image pin labelled `by flag`, `stated`, or `path-versioned`.

`--print-exports` writes the de-duplicated `export` names to stdout and a provenance line to stderr.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Profile loaded (and, for `--print-exports`, at least one name printed). |
| 4 | Profile missing or unusable, or `--print-exports` found no step with an `export` list. The message lists every problem and the full field reference. |
| 2 | Bad command-line arguments (argparse). |

`python terra/steps.py` prints the step map the `terra/` tools derive from the profile: step number,
workflow, root entity, rerun config name, and row source.

## File format

A profile is one JSON object. Unknown fields are refused at both levels, so a misspelled field name is
reported instead of reading as absent. Any key starting with `_` is ignored by the schema check; see
[Rationale keys](#rationale-keys-_why_) for the one rule that applies to them.

### Top level

| Field | Required | Meaning |
|---|---|---|
| `schema_version` | yes | Must be `1`. Any other value is refused. |
| `name` | yes | The module name. |
| `callers` | yes | List of caller names that `@` expands over. May be empty; a profile with an empty list cannot use `@`. |
| `steps` | yes | Non-empty list of step objects, in chain order. |
| `freeze` | no | What `terra/batch_freeze.py` freezes ([Freeze block](#freeze-block)). A profile without one cannot be frozen; `batch_freeze.py` exits 4. |
| `unused_callers` | no | Callers upstream wires that this cohort does not run. Must not overlap `callers`, and needs `_why_unused_callers` ([Explaining a difference from upstream](#explaining-a-difference-from-upstream)). |

### Freeze block

The optional top-level `freeze` object tells `terra/batch_freeze.py` which baseline attributes to copy
and where to write them back ([terra-head-to-head.md](terra-head-to-head.md#frozen-baseline-for-other-flavors)).

| Field | Meaning |
|---|---|
| `entity_type` | The entity type of the frozen row. Must be some step's `rootEntityType`. |
| `entity_row` | Where the row name comes from: a config key (as for a step's `entity_row`), or `"--entity"`. |
| `attrs` | The attributes to freeze. Each must be present on the baseline row. |
| `optional_attrs` | Attributes copied when the baseline row has them. |
| `attr_suffix` | `"{frz}"` writes `<attr>_frz` beside the baseline attribute. `""` writes the name the step reads, and then some step must read `this.<attr>`. |
| `row_mode` | `merge` onto an existing row, or `create` a new row, which `plan` prints as a TSV. |
| `tag` | Names the manifest, the TSV and the bucket prefix. Defaults to the module name; `""` keeps the unsuffixed cohort names. |

`_why_<field>` siblings are allowed. The shipped `genotyping` profile freezes the 17 batch `sample_set`
attributes with `attr_suffix` `"{frz}"`, `row_mode` `merge` and `tag` `""`. The shipped `single_sample`
profile creates a `sample` row named by `--entity`, with `bam_or_cram_file` (plus its index) and the
optional `case_stripy_file` under their bare names.

### Step fields

Required on every step:

| Field | Meaning |
|---|---|
| `step` | The method config name, `NN-Name`. The two-digit prefix is required: `terra/steps.py` refuses a step without it, because every `terra/` tool matches workspace configs on that number. |
| `wdl` | The WDL file basename, without `.wdl`. |
| `workflow` | The workflow name the WDL declares. Usually the same as `wdl`, but not always (`DepthClustering.wdl` declares `ClusterDepth`), so both are stated. |
| `rootEntityType` | The Terra entity type the step runs on (`sample_set`, `sample_set_set`, `sample`). Used in the posted config and as a cross-check against the derived type (below). |
| `inputs` | The complete input map, `Workflow.input` to value. Must not be empty. |
| `outputs` | The complete output map, `Workflow.output` to value. |

Optional:

| Field | Meaning |
|---|---|
| `entity_row` | Where the entity row name comes from: a config-layer key (`BATCH` or `GSVTK_BATCH`, both read as `BATCH`), or `"--entity"` when the operator must name the row. Absent means `--entity`. |
| `branch_only_inputs` | Input keys the branch declares and older refs do not. `--drop-branch-only-inputs` removes exactly these when building a config for a ref that lacks them. Every key must be bound in `inputs`. |
| `export` | Non-empty list of bare attribute names a downstream tool fetches after the chain runs (`terra/batch_fetch_compare.sh` reads them through `--print-exports`). |
| `images` | Map from a `*_docker` input of this step to a tagged image ref, or `"--image"`. |
| `dockstore` | The Dockstore entry name when it differs from `workflow`. Upstream publishes `GATKSVPipelineSingleSample.wdl` as `SingleSamplePipeline`. Absent means the same as `workflow`. |
| `literals` | Map from an input leaf name to the exact string Terra stores, replacing that binding's value. |
| `branch_only_outputs`, `upstream_only_outputs`, `template_overrides` | Known differences from upstream that `derive_profile.py check` should list as expected ([Explaining a difference from upstream](#explaining-a-difference-from-upstream)). |

`python kit/module_profile.py --module <name>` on a missing module prints this same field list.

### Binding values

A value is a path if it starts with `this.` or `workspace.`. Anything else is a literal.

- `this.<attr>` reads an attribute of the row the step runs on; `this.sample_sets.<attr>` reads that
  attribute from each member of a set. `workspace.<attr>` reads a workspace attribute.
- Literals are posted as strings, because Terra stores the input map as `Map[String, String]`. A
  non-string JSON value is serialized with `json.dumps`. The string `"6"` and the number `6` therefore
  post different bytes; the genotyping profile keeps the strings Terra has always received.
- `inputs` is the complete map. Terra has no inheritance from upstream's template, so an input left out
  of the map takes the WDL default. Inputs deliberately left to a default are explained in
  `_why_unbound`.

### Suffix tokens: `{frz}` and `{new}`

A head-to-head reads frozen baseline attributes and writes its own outputs beside them under suffixed
names. In a path, `{frz}` expands to `_` plus `FROZEN_SUFFIX` (default `_frz`) and `{new}` to `_` plus
`NEW_SUFFIX` (default `_new`):

```json
"GenotypeBatch.median_coverage": "this.median_cov{frz}",
"GenotypeBatch.genotyped_depth_vcf": "this.genotyped_depth_vcf{new}"
```

Tokens are allowed only on paths. Any other token name, or a token on a literal, is refused.
`terra/batch_rerun_step.py --tag TAG` moves the output suffix of one rerun to `_TAG`; inputs do not move.

### Caller fan-out: `@`

An `@` in a key expands into one binding per entry in `callers`, with the same substitution in the
value:

```json
"GenerateBatchMetrics.@_vcf": "this.clustered_@_vcf{frz}"
```

With `callers: ["manta", "wham", "scramble"]` this becomes three bindings
(`GenerateBatchMetrics.manta_vcf` and so on). Refused: `@` in a value whose key has none, `@` on a
literal, and any `@` key when `callers` is empty.

Callers left out of the list are left unbound, which is a pipeline change when the WDL merges them
with `select_all` (as `GenerateBatchMetrics` does). `batch_configs.py check` reports upstream bindings
for excluded callers as optional-bound-upstream-but-omitted.

### Entity row and entity type

The entity type a step runs on is derived, not read from `rootEntityType`: it is the stem of the one
`this.<etype>_id` read among the step's input values (`this.sample_set_id` gives `sample_set`).
Member reads like `this.sample_sets.<attr>` are ignored. A step with zero or several such reads is
refused by `terra/steps.py`. `rootEntityType` must agree with the derived type, and
`terra/batch_rerun_step.py` refuses when it does not.

`entity_row` says where the row name comes from. In the genotyping profile, steps 06, 07, 08 and 10 use
`BATCH` (the `sample_set` named by `GSVTK_BATCH`). Step 09 runs on a `sample_set_set` cohort, and no
config key holds one, so it says `"--entity"` and `batch_rerun_step.py` requires `--entity NAME`. The
single-sample step does the same for its `sample` row. A blank value, a binding path, a token, an `@`,
or a key the config layer does not know is refused.

Participant-rooted steps are not supported. The freeze writes a `participant` only as the reference a
frozen `sample` row carries from its baseline row; nothing freezes or reruns a step whose root entity
is `participant`.

### Images

`images` states which image each `*_docker` input runs in for this step. Each value is one of:

- a tagged or digest-pinned ref, such as `us.gcr.io/proj/ns/sv-pipeline:my-tag`;
- `"--image"`, meaning the operator states the ref on the command line;
- an object `{"ref": "<ref>", "versioned_by": "path"}` for a ref with no tag whose last path segment
  ends in a version number (`marketplace.gcr.io/google/ubuntu1804`).

Refused at load: untagged refs, `this.`/`workspace.` paths, quoted refs, keys that are not `*_docker`
or are not bound in `inputs`, `versioned_by` on a ref that already has a tag or on `"--image"`, any
`versioned_by` value other than `path`, and an empty map (leave the field out instead). Do not copy refs
from upstream's `dockers.json`; a pin should name the image your change ran in.

When `batch_rerun_step.py` builds a config, each `*_docker` key takes the first of: `--image`,
`--image-from <capture.json>`, the profile's `images` entry, then `GSVTK_IMAGE_REPO` /
`GSVTK_GATK_IMAGE_REPO`. `show` labels which route each key took, and any key left unpinned is refused.
The shipped profiles pin almost nothing: the single-sample step marks 13 of its 14 images `"--image"`.

### Literals

`literals` exists for steps whose upstream template reads resources from `workspace.<attr>`. That only
works in a workspace someone has already populated; elsewhere Terra rejects the submission with
`HTTP 400 Invalid inputs`. A literal gives the exact string Terra should store instead:

```json
"literals": { "chr_x": "\"chrX\"", "n_per_split": "\"15000\"" }
```

Keys are leaf names, matched against the step's bound input keys. A leaf shared by several bindings
replaces all of them. A literal whose leaf matches no bound input is refused, as is a `*_docker` leaf
(images belong in `images`).

### Rationale keys (`_why_*`)

JSON has no comments, so rationale lives in sibling keys: `_why_<key>` beside the step field or binding
it explains, for example `_why_GenotypeBatch.training_vcf` or `_why_entity_row`. `_why_unbound`
explains inputs left to WDL defaults. A `_why_` key that names nothing this step binds or carries is
refused, so a rationale cannot outlive its binding. `_why_*` keys inside `inputs`, `outputs` or
`images` are refused too, because everything in those maps is posted.

## Who reads a profile

| Consumer | What it takes from the profile |
|---|---|
| `terra/batch_configs.py` | The full config bodies: `show` prints them, `check` compares every bound key against the WDL at a ref, `create`/`validate` post them. |
| `terra/steps.py` | The step map shared by every `terra/` tool: step number, workflow, Dockstore entry, derived root entity, row source. |
| `terra/batch_rerun_step.py` | One step's config for a pinned-image rerun, its row source and image pins. |
| `terra/batch_fetch_compare.sh` | The required fetch list, via `kit/module_profile.py --print-exports`. Optional attributes (index siblings, extra tables) are still listed in the script. |
| `checks/wdl_reach.py` | Each step's input bindings, keyed by workflow (an unusable profile is reported as a skip). |
| `checks/body_golden_diff.py` | Only the image-version rule (`ref_version_kind`). |
| `terra/batch_freeze.py` | The `freeze` block: which attributes to copy, onto which row, under which names. |
| `scripts/derive_profile.py` | Everything it can compare with upstream ([below](#deriving-a-profile-from-upstream)). |

### Checking a profile against upstream

`batch_configs.py check` grades the profile against a gatk-sv ref offline: it reads WDLs with miniwdl
and upstream's `inputs/templates/**/*.json.tmpl` with `git archive` from `GSVTK_GATK_SV_CHECKOUT`, never
touching that checkout's worktree. For each step it reports:

- keys the WDL does not declare, required inputs left unbound, and output problems;
- a template verdict: `DERIVED`, `JINJA-NEUTRALISED` (Jinja expressions read as `null`), `NO TEMPLATE`,
  `CANNOT PARSE`, or `NOT READ`;
- `optional-bound-upstream-but-omitted`: inputs production binds that the profile leaves to a WDL
  default. This is a finding, not a failure.

```bash
GSVTK_BRANCH=main python terra/batch_configs.py check
```

Templates are parsed as JSON after replacing each `{{ ... }}` with `null`. They are never rendered,
because upstream's renderer depends on a values bundle.

## Deriving a profile from upstream

Most of what a profile states is already written down in gatk-sv: the Terra templates under
`inputs/templates/terra_workspaces/`, the workflow's WDL, and `.github/.dockstore.yml`.
`scripts/derive_profile.py` reads them at a ref with `git rev-parse`, `git ls-tree`, `git grep` and
`git show` only, so your checkout's working tree, index and HEAD are not touched. It prints every
field as `DERIVED <value> [source]` or `NOT DERIVED FROM UPSTREAM (reason)`. It has no `gsvtk` mode;
run it directly.

```bash
python scripts/derive_profile.py show  --module genotyping --ref main
python scripts/derive_profile.py show  --workflow GenotypeBatch --ref v1.1.1
python scripts/derive_profile.py check --module genotyping --ref main
python scripts/derive_profile.py show  --module genotyping --ref main --write-draft /tmp/genotyping.draft.json
```

`--repo` defaults to `GSVTK_GATK_SV_CHECKOUT` and `--ref` to `HEAD`; there is no config key for the
ref. `--profile PATH` checks a profile file instead of a named module.

- **Derived:** the template, `workflow`, `wdl`, `rootEntityType` (the same `this.<etype>_id` rule as
  `checks/terra_entity_check.py`), `dockstore`, every input binding, the WDL's declared input names,
  and the output keys the WDL declares. An output's attribute value is derived only where upstream
  ships an `output_configurations/` template (today, `MergeBatchSites` only).
- **Not derived:** the `{frz}`/`{new}` suffixes, `callers`, `entity_row`, `export`, `images`,
  `literals`, `freeze`, `_why_*`, and any binding upstream renders with Jinja (`{{ ... }}`; the
  expression is printed, never evaluated).

`check` compares the derived fields with the profile, after stripping suffix tokens and expanding `@`
over `callers`. It compares the raw `inputs`, before `literals` are applied. Each disagreement is
printed with both values as `differs`, `upstream-only`, or `profile-only` with a reason (the template
leaves it to the WDL default, the WDL does not declare it, or it is not a declared output). A
difference the profile explains is listed as `expected` instead (next section). The last line is
`GSVTK-DERIVE-CHECK compared= disagree= expected= not-compared=`.

### Explaining a difference from upstream

A profile describes a branch, so some differences from upstream are deliberate. These fields record
them, and `check` then lists each as `expected` rather than as a disagreement. Each field needs a
`_why_<field>` beside it, and the validator refuses one without it.

| Field | Where | `check` treats as expected |
|---|---|---|
| `unused_callers` | top level | any upstream input or output key of these callers (`melt_vcf`, `outlier_filtered_melt_vcf_index`) |
| `branch_only_inputs` | step | these inputs, when upstream does not declare them |
| `branch_only_outputs` | step | these outputs, when upstream does not declare them; each must be in `outputs` |
| `upstream_only_outputs` | step | these upstream outputs, which the branch removes; none may be in `outputs` |
| `template_overrides` | step | these inputs, which upstream's template leaves to the WDL default; each must be in `inputs` |

`--write-draft` keeps them: the draft binds the overrides and branch-only keys, and leaves out the
upstream-only outputs and the unused callers' keys. [compatibility.md](compatibility.md) uses `check` to
decide whether a gatk-sv ref is compatible.

`--write-draft PATH` writes a draft profile and `PATH.lock.json`. The draft takes derived values, keeps
a hand-written suffix only where the bare attribute agrees with upstream, and carries over the fields
that cannot be derived. It is checked with the same validator as a shipped profile. The lock file
records the repo, ref, resolved sha and the sha256 and git blob of every upstream file read. Review the
draft before copying anything from it into `profiles/`.

| Exit | Meaning |
|---|---|
| 0 | `show` derived something, or `check` agrees |
| 1 | `check` found disagreements |
| 2 | bad ref, nothing derived or compared, a profile that does not validate, or a bad flag combination |
| 3 | no git |
| 4 | no checkout, or no profile for `check` |

Verified offline only, against a synthetic gatk-sv repo: derivation, `check` agree / disagree /
nothing-derived, `branch_only_inputs`, the draft and lock contents, the refusals, and that a dirty tree
and a staged change survive a read of an older ref. It has also been run read-only against a real
gatk-sv checkout. Limits: the WDL scan is a standard-library declaration scan, not miniwdl, so an
unusual layout is reported as not derived rather than filled in; and nothing here touches Terra, so
whether a derived binding launches is `checks/terra_entity_check.py`'s and Terra's question.

Against gatk-sv `main`, `check` exits 0 for both shipped profiles, with every remaining difference
listed as expected.

## Adding a module

1. Copy the closest shipped profile to `profiles/<module>.json` and set `name`.
2. For each step, take `inputs` from upstream's `.json.tmpl` for that workflow and keep every key it
   binds, including optional ones (`python scripts/derive_profile.py show --workflow <Workflow>`
   prints them). Templates are the only place that says which attribute feeds which input, and many
   input names differ from their attribute (`median_coverage` reads `median_cov`). Add `{frz}`
   to inputs read from a frozen baseline and `{new}` to inputs produced earlier in your chain and to
   every output.
3. Make sure each step reads exactly one `this.<etype>_id` and that `rootEntityType` matches it.
4. Set `entity_row` to `BATCH` if `GSVTK_BATCH` names the row, otherwise `"--entity"`.
5. Add `images` with `"--image"` for each `*_docker` input you will pass on the command line, and
   `dockstore` if the published entry name differs from the workflow name (check
   `.github/.dockstore.yml` in gatk-sv).
6. Add `export` on the step whose outputs a fetch should bring back, if any.
7. Write a `_why_<key>` for anything a reader would otherwise change: a string-typed literal, a
   branch-only input, an excluded caller, a row source.
8. Validate and compare:

   ```bash
   python kit/module_profile.py --module <module> --print
   GSVTK_MODULE=<module> python terra/steps.py
   GSVTK_MODULE=<module> GSVTK_BRANCH=<ref> python terra/batch_configs.py check
   ```

Limits to check before starting:

- Steps that bind across member rows (`this.samples.<attr>`, as `TrainGCNV` does) cannot be frozen,
  because `batch_freeze.py` writes one row of one entity type.
- The fetch-and-compare stage calls `compare/compare_batch_tables.py`, which knows the genotyping
  tables. A module whose outputs are other artifact types needs its own comparison step (for tarballs,
  `compare/tar_manifest.py`).
- The profile has no field for which comparator to run.

Open decisions on profiles are tracked in [roadmap.md](roadmap.md).

## Tests

`make selftest` includes a `profiles` phase (`scripts/selftest.d/profiles.sh`) that covers the loader's
refusals, the shipped data, and the shell's use of `export`.

`scripts/golden_configs.py` pins the genotyping config bodies byte-for-byte, built from the placeholder
profile `scripts/selftest.d/fixtures/genotyping.profile.env`, so it runs with no user config and no
network:

```bash
python scripts/golden_configs.py              # compare live maps to the goldens
python scripts/golden_configs.py --control    # prove a dropped binding makes the check fail
python scripts/golden_configs.py --write      # regenerate after an intended change; review the diff
```

If you change `profiles/genotyping.json`, regenerate the goldens and review the diff in the same commit.
