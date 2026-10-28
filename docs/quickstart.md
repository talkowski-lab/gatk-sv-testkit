# Quickstart

A tour of the kit's main tasks using only commands that cost nothing: no Terra call that writes, no VM,
no bucket write. The two build-preview commands make read-only calls to GCP and GitHub. Outputs are
shortened; `YOUR_*` stands for your own project and workspace values.

## 1. Install and configure

```bash
git clone https://github.com/talkowski-lab/gatk-sv-testkit.git && cd gatk-sv-testkit
make setup                                               # ./.venv (pip needs the network)
.venv/bin/python -m pip install -r requirements-dev.txt  # miniwdl, jinja2, flake8, pytest
cp testkit.env.example testkit.env                       # set your project and Terra workspace
./kit/gsvtk-config show
```

`show` prints every setting and where it came from:

```
  GSVTK_BASELINE_WORKSPACE   GATK-Structural-Variants-Joint-Calling   [default]
  GSVTK_BATCH                all_samples                              [profile:~/src/gatk-sv-testkit/testkit.env]
- GSVTK_BRANCH               (unset)                                  [unset]
  GSVTK_IMAGE_REPO           us.gcr.io/YOUR_PROJECT/test/gatk-sv      [derived from PROJECT]
```

Your GCP project and Terra workspace have no default. A tool that needs one and finds it unset exits 4
and names the key:

```
$ python terra/batch_configs.py validate
gsvtk-config: GSVTK_TERRA_NAMESPACE is not set (Terra workspace namespace of your sandbox).
  set it in the environment, or put `GSVTK_TERRA_NAMESPACE=...` in one of:
    ...
  see docs/config.md
```

Every setting is described in [config.md](config.md).

## 2. See what this machine can run

```
$ ./gsvtk tools
checkout  ~/src/gatk-sv-testkit
gatk-sv   ~/src/gatk-sv @ 8fea3adc
terra     ~/src/gatk-sv-testkit/.venv/bin/python
wdl_gate  ok (~/src/gatk-sv-testkit/.venv/bin/miniwdl)
jq scan   ok
build     gcloud ok
inputs    java ok, WOMTOOL_JAR unset/missing — export WOMTOOL_JAR=/path/womtool.jar ...
```

Each line is one part of the kit and the dependency it found or is missing. The static checks need
`GSVTK_GATK_SV_CHECKOUT` set to a local gatk-sv clone.

## 3. Check a WDL change (seconds, offline)

`miniwdl check` accepts a workflow whose call sites never bind a required input, or that pass an input
the callee no longer declares. The WDL gate counts both, per workflow, at a gatk-sv ref:

```
$ checks/wdl_gate.sh --wf GenotypeBatch origin/main
WORKFLOW         REF            EXIT   INCOMPLETECALL   STALE-BINDINGS  MISSING-INPUTS  EXTRA-KEYS
GenotypeBatch    main@8fea3adc  0      0                0               0               clean
WOMTOOL          main@8fea3adc  SKIPPED — 1 of 1 workflow(s): WOMTOOL_JAR is unset
...
no hard errors. Re-run with --strict to treat IncompleteCall / stale bindings / ... as failure
```

Each ref is extracted into its own directory with `git archive`; your gatk-sv checkout is not modified.
A workflow name that does not exist at the ref is reported `ABSENT` and fails the gate.

Upstream gatk-sv already has some findings, so compare against the ref you are changing rather than
expecting zero. `--strict` turns warnings and skips into failures.

## 4. Check an `sv_shell` change

`src/sv_shell` chains module scripts, each fed an `inputs.json` built with `jq`. `jq -r ".missing_key"`
returns the string `null` instead of failing, so a renamed key surfaces stages later as
`--some-flag null`. Two checkers catch this:

```bash
python checks/svshell_contract_check.py               # stages that read keys nobody supplies
python checks/svshell_jq_plumbing_scan.py              # every null/empty value, including upstream's
python checks/svshell_jq_plumbing_scan.py --compare-to main   # only what your change introduced
```

```
$ python checks/svshell_jq_plumbing_scan.py --compare-to main
vs baseline main: NEW null/empty in 0 key(s) across 0 block(s)
=> OK: full coverage on both refs and no new nulls introduced
```

`./gsvtk check <ref>` runs the WDL gate, the contract check and the plumbing scan together. See
[static-checks.md](static-checks.md).

## 5. Check Terra method configs before any spend

The method configs bind inputs for one branch's WDL. If they name an input the launched WDL does not
declare, Terra rejects the config at submission. Check offline against a ref in your gatk-sv clone:

```
$ python terra/batch_configs.py check --against main
WDL read from ~/src/gatk-sv @ main
  ok  06-GenerateBatchMetrics  20 bound vs 38 declared
  ...
  BAD 10-GenotypeBatch         17 bound vs 21 declared
      EXTRA  GenotypeBatch.training_vcf
check: 1 problem(s).
```

`python terra/batch_configs.py show` prints the full config bodies. The commands that write to Terra
refuse until you confirm:

```
$ python terra/batch_configs.py create
create POSTs (and overwrites) method configs in your workspace: a config with a
  wrong binding is worse than no config, because the next submission will use it.
  review `show` first, then re-run with --confirm.
```

The full sequence (recon, freeze, configs, rerun, fetch, compare) is in
[terra-head-to-head.md](terra-head-to-head.md); [test-a-branch.md](test-a-branch.md) is the same
sequence on one page.

## 6. Compare outputs

The comparators take explicit paths, use no network, and write only to `--out-prefix` or `--json`.
`./gsvtk compare --list` prints them all.

| Tool | Question |
|---|---|
| `compare_batch_tables.py` | per-column diff of two runs' summary tables |
| `table_diff.py` | any two tables with a shared key column |
| `matrix_diff.py` | which cells of a site-by-sample matrix changed |
| `vcf_paired_diff.py` | any `FORMAT`/`INFO` field on the same (site, sample) pairs |
| `site_set_diff.py` | which sites appear in only one run |
| `gq_scale_compare.py` | are two quality fields on the same scale |
| `pair_level_concordance.py` | genotype concordance between two VCFs |
| `artifact_tally.py` | what one file actually contains |

For example, to ask whether GQ moved to a different scale, or whether copy-number states changed:

```bash
python compare/vcf_paired_diff.py old.vcf.gz new.vcf.gz --field FORMAT:GQ --scale 99/999 --cap 99
python compare/vcf_paired_diff.py old.vcf.gz new.vcf.gz --join coord --field FORMAT:RD_CN --by-sample
```

Comparators exit 0 when the inputs match, 1 when they differ, and 2 when there was nothing to compare:

```
$ python compare/compare_batch_tables.py --baseline-dir empty/b --new-dir empty/n
== SUMMARY  MATCH=0  DELTA=0  STRATEGY=0  MISSING=6
  every column is MISSING: this diff compared nothing. ...
```

A worked example is in [table_diff_example.md](../examples/table_diff_example.md); how to read the
results is in [comparators.md](comparators.md).

## 7. Preview an image build

GATK-SV images are `linux/amd64`. The build script builds them on a temporary x86-64 VM in your
project, streams the log, and deletes the VM when done. Preview first:

```bash
docker/gatk-sv-build.sh --check my-branch     # read-only: branch exists, APIs reachable, registry grant
docker/gatk-sv-build.sh --dry-run my-branch   # prints the exact gcloud commands; creates nothing
./gsvtk build my-branch                       # both of the above
```

The real build (`./gsvtk build my-branch --confirm`) uses an `e2-standard-8` with a 150 GB disk for
roughly 1 to 3 hours. See [docker-builds.md](docker-builds.md).

## 8. Run a stage locally

Local replay runs a GATK-based pipeline stage on your machine with a captured run's exact inputs and a
GATK jar you built. It costs time and disk (tens of GB). Start with:

```bash
./gsvtk replay preflight      # Java 17+, the jar, bcftools, free disk
```

See [local-replay.md](local-replay.md).

## 9. Run the test suite

```bash
make test
```

It needs no credentials, data or network, and ends with `make test: PASS (offline gate)`. Missing
optional tools show up as named `SKIP` lines. See [CONTRIBUTING.md](../CONTRIBUTING.md).

## 10. From an agent

The agent skill runs every command with `--read-only`, so modes that write are refused before anything
is checked:

```
$ gsvtk --read-only terra rerun-submit
gsvtk: REFUSED — terra rerun-submit POSTs, boots compute, or bulk-downloads, and this run is read-only.
       ...
       To do it, step out of read-only and run it yourself, in the checkout:
         cd <checkout> && python terra/batch_rerun_step.py submit
```

See [setup.md](setup.md#the-agent-skill) and [cli.md](cli.md).

## Where next

| You want | Read |
|---|---|
| a Terra head-to-head, end to end | [terra-head-to-head.md](terra-head-to-head.md) |
| what the static checks report and why | [static-checks.md](static-checks.md) |
| to read comparator output correctly | [comparators.md](comparators.md) |
| images, registries and the build VM | [docker-builds.md](docker-builds.md) |
| a GATK stage run locally | [local-replay.md](local-replay.md) |
| modules other than genotyping | [module-profiles.md](module-profiles.md) |
| every setting | [config.md](config.md) |
| an error message you are looking at | [troubleshooting.md](troubleshooting.md), searched for the exact text |
