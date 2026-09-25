# examples/ — worked drivers, not supported entry points

These ran against a real cohort during the investigation that produced this toolkit. They are here
because reading how someone actually drives the trainers is faster than reading the trainers, and
because each one encodes a decision that cost time to reach.

**They are not supported interfaces.** Read one, copy it, change the step. If one breaks for you,
the fix belongs in your copy — what's durable is the pattern, and the parts that are genuinely
reusable live in `kit/`, `terra/` and `compare/`.

All of them source `kit/config.sh`, so they honour your `testkit.env`: scratch under `GSVTK_WORK`,
and the jar under test discovered from `GSVTK_GATK_CHECKOUT` (see
[docs/local-replay.md](../docs/local-replay.md)). None of them touch Terra or cost money.

| Script | What it does | The decision it encodes |
|---|---|---|
| `run_train_chr20.sh` | SR/PE/RD trainers over a chr20 slice | fast iteration loop; **do not publish RD numbers from a slice** |
| `run_train_full.sh` | same, full interval set | the run whose RD cutoffs are quotable |
| `run_train_definitive.sh` | the published run: full intervals, memory sized for the RD trainer | what "sized for the trainer" actually costs in wall-clock and heap |
| `run_rd_population_probe.sh` | cutoff behaviour across many subsample configurations | a distribution, measured; and one arm writes nothing on purpose — an arm whose input is identical to a previous arm is a control, not a variant, and re-running it into a new directory only invites mixing them up |
| `replay_reference_run.sh` | replay one task's command verbatim from a captured Cromwell `script` dump | replaying a *stale* successful run is the only way to exercise old release code when the current default points at newer sources. Needs `CAPTURED` (a local copy of that task's Cromwell `script`, pulled with the `gsutil cp` shown in the script's header) and `IMAGES` (a per-arm image map — see `replay/images.example.json`). Invoke it as `examples/replay_reference_run.sh inputs`; there is no live-workspace mode, so the capture has to be local — which also makes it offline and fail-loudly |
| `recompute_het_population.py` | recomputes population-level het-dispersion statistics from staged SR/PESR inputs | a population quantity, recomputed outside the pipeline, to test whether a mechanism explains an observation — which is exactly the check that refuted one of this project's own claims ([docs/methodology.md](../docs/methodology.md)) |
| `table_diff_example.md` | a real capture from `compare/compare_batch_tables.py` | what `MATCH` / `DELTA` / `MISSING` / `STRATEGY` look like in practice, including a row that must never be diffed as a mean |
| `patches/` | the memory/interval patch that let the RD trainer finish | a patch as a patch, so the run stays reproducible rather than depending on an uncommitted working tree |

## Reading one before running one

Each script prints what it is about to do. The three things worth checking every time:

1. **Which jar.** The log names the jar path and its timestamp. A stale jar reproduces the old
   answer with complete confidence and no error.
2. **Which interval set.** SR and PE metrics are interval-insensitive at batch level; **RD cutoffs
   are not**. A subsampled run answers SR/PE questions and produces RD numbers that belong to no
   real cohort.
3. **Where output goes.** Confirm the directory after launching. A collision that overwrites an
   earlier run's log destroys your only evidence — `run_rd_population_probe.sh` has a comment about
   exactly how that happened once.

## The arms pattern

`run_rd_population_probe.sh` runs several arms and writes a per-arm report. That structure is worth
copying for any "does changing X move Y?" question:

- every arm states its one difference from the arm before it;
- a control arm runs with identical inputs and is labelled as such;
- every arm's output directory is distinct (`gsvtk_work "runs/${name}_${SUFFIX}"`), and the report
  names the interval set used;
- the output path is resolved **once**, from a single `OUT=${OUT:-…}` line. An earlier version of
  these scripts honoured an `OUT` override and then reassigned `OUT`, so a later arm overwrote an
  earlier arm's log — including the OOM trace that was the evidence. Single assignment is what
  prevents that class of loss; `run_train_full.sh` and `run_train_definitive.sh` show the form.

The alternative — one script, one run, memory about which knob was turned — produces numbers you
cannot attribute.
