# examples/

Worked drivers that run GATK-based pipeline stages locally on a real run's staged inputs. They were
written against one cohort (the v1.1.1 baseline, with the genotyping trainers as the stage under
test), and they are recipes rather than supported interfaces: read one, copy it, and change the
step. The reusable parts live in `kit/`, `replay/`, `terra/` and `compare/`.

All of them source `kit/config.sh`, so they use your profile: scratch space under `GSVTK_WORK`, and
the GATK jar under test from `GSVTK_GATK_CHECKOUT/build/libs` (override with `JAR=`). See
[docs/local-replay.md](../docs/local-replay.md) for staging inputs and building the jar. None of
them touch Terra or spend money. Each script prints its purpose with `--help`, and most can also be
run through `./gsvtk replay <mode>`.

| File | `gsvtk replay` mode | What it does |
|---|---|---|
| `run_train_chr20.sh` | `train-chr20` | Runs the SR/PE/RD genotyping trainers (`TrainSVGenotyping`) on a chr20 slice, with the command line `GenotypeBatch.wdl` uses. A fast iteration loop. |
| `run_train_full.sh` | `train-full` | The same on the full interval set. |
| `run_train_definitive.sh` | `train-definitive` | The full run with three input differences from v1.1.1 removed: the 56 curated RD training loci, `--num-bins 100000`, and the depth exclusion intervals. RD training then reads few intervals and fits in modest memory. |
| `run_rd_population_probe.sh` | `rd-population-probe` | Runs RD training in several arms that differ only in the training intervals (curated loci versus a same-size random sample) and reports per-arm cutoffs. |
| `replay_reference_run.sh` | `reference-run` | Rebuilds a launchable `SVShell` input JSON from a successful run's captured Cromwell `script` (`inputs` mode), and tallies records and genotypes for two VCFs (`counts` mode). |
| `recompute_het_population.py` | `het-population` | Recomputes the SR training population from staged evidence under the v1.1.1 and Java recipes, to see which difference explains a change in `sd_het`/`median_hom`. |
| `table_diff_example.md` | | Annotated output of `compare/compare_batch_tables.py`. |
| `patches/` | | GATK and WDL patches used by these runs (a streaming RD trainer, and proposed RD training configuration changes), kept as patch files so a run does not depend on an uncommitted working tree. |

`replay_reference_run.sh inputs` needs two variables: `CAPTURED`, a local copy of the reference
run's Cromwell `script` file (the header shows the `gsutil cp` to fetch it), and `IMAGES`, a JSON map
of docker input to image for your arm (see `replay/images.example.json`). It reads WDLs from your
local gatk-sv clone at `GSVTK_BRANCH`, so once the capture is local it runs offline. It writes
`*.inputs.json` under `GSVTK_WORK/replay` for you to inspect; it does not create configs or submit.

## Before running one

Check three things in the log each time:

1. Which jar. The log names the jar path and timestamp. A stale jar gives you the old answer with no
   error.
2. Which interval set. SR and PE metrics are insensitive to interval subsampling at batch level, but
   RD cutoffs are not. Do not publish RD numbers from a slice or a subsample.
3. Where output goes. Each script resolves its output directory once, from a single
   `OUT=${OUT:-...}` line, so `OUT=` overrides it. Make sure two runs do not share a directory, or the
   second overwrites the first's log.

## Copying the arms pattern

`run_rd_population_probe.sh` is a template for "does changing X move Y?" questions:

- each arm differs from the reference in one stated input;
- a control arm changes something irrelevant (here, the number of intervals without the curation)
  so you can tell the effect of X from the effect of any change;
- each arm writes to its own directory (`gsvtk_work "runs/${name}_${SUFFIX}"`), and the report names
  the interval set used.
