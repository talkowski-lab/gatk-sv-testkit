# Local replay: run a real gatk-sv stage without a cloud account

For `src/sv_shell` and `src/gatk` changes that you want to iterate on in minutes instead of
per-submission, the expensive stages can be run locally on **the exact inputs a real run used**.

The point is not to reproduce the pipeline locally. It is to make the *inputs* identical to a
frozen baseline, so a local difference is attributable to your code and nothing else.

## What you need

1. **A GATK jar containing your change.** Build one from a gatk checkout that carries the
   gatk-sv source patches:

   ```bash
   cd <gatk checkout>
   JAVA_HOME=<a JDK 17+ home> ./gradlew localJar          # ~1-3 min incremental
   export GSVTK_GATK_CHECKOUT=<gatk checkout>             # tools discover the jar from here
   ```

   The tools look for `build/libs/gatk-package-*-local.jar` and pick the newest. `-local.jar`
   is the shaded jar with the `sv` command group; the plain jar will not have it.

   If you don't set `JAVA_HOME` and the default `java` is older than 17, the build stops with
   gatk's own message rather than producing a jar that fails later — see
   [troubleshooting.md](troubleshooting.md).

2. **The frozen inputs.** Either from the Terra path (they are already in your workspace bucket):

   ```bash
   python terra/batch_freeze.py copy --write && python terra/batch_freeze.py attrs --write
   ```

   (`copy` without `--write` is a dry run that prints the object count and GiB; `attrs` will not
   publish over a `verify` that failed. See
   [terra-head-to-head.md](terra-head-to-head.md#2-freeze-the-baseline).)

   or from a run you have metadata for — `replay/` rebuilds a launchable input set from a
   captured successful run:

   ```bash
   python terra/batch_save_metadata.py --outdir "$GSVTK_WORK/metadata"   # full Cromwell metadata
   python replay/build_inputs.py                # rebuild a launchable inputs JSON from it
   ```

   `replay/build_inputs.py` reads the captured task commands and outputs to reconstruct the
   inputs a successful run actually received, so you replay what ran rather than what you think
   ran. Its `images.example.json` and `single_sample_extra_inputs.json` show the shapes it
   expects.

3. **Room on disk.** The staged matrices are tens of GB. If you have a previous copy of the
   same panel, `--link-dir` hardlinks instead of downloading:

   ```bash
   python terra/stage_inputs.py --keys rd_file pe_file --link-dir /path/to/panel --dry-run
   python terra/stage_inputs.py --attrs sr_metric_file --region chr20
   ```

   `--region` tabix-slices bgzf objects, which is the cheap way to get a chromosome-scale run.
   `--dry-run` prints the plan and touches nothing.

   Adoption is **verified, not assumed**. Pipeline object names are stable across runs
   (`<batch>.depth.depth_sepcutoff.txt`, `<batch>.cutoffs`) and those tables are fixed-shape, so
   “same basename, same byte count” used to adopt an older capture as if it were the current baseline
   object — and every later `compare/*` verdict inherited the wrong input. Now:

   * every candidate under every `--link-dir` is collected first; **more than one same-size match is
     an error** that lists them, rather than a race to the first glob hit;
   * the one candidate must match the object's `crc32c`. A mismatch means “this local file is not that
     object” — it prints `!! NOT adopting …` and downloads the real object instead;
   * when `crc32c` cannot be compared (composite upload has none, no `gsutil`, or the file is over
     the 64 MiB hashing budget) the adoption still happens and the log line says
     `unverified: …`. `staged.json` carries the verdict per file in `identity`, so a staged tree
     built on unverified adoptions is visible after the fact.

## Run it

`examples/` holds working drivers from a real investigation. They are recipes, not supported
entry points — read one, copy it, change the step. Each one sources `kit/config.sh`, discovers
your jar, and writes under `GSVTK_WORK`:

```bash
examples/run_train_chr20.sh          # SR/PE/RD trainers over a chr20 slice
examples/run_train_definitive.sh     # full interval set, the run whose numbers you publish
examples/run_rd_population_probe.sh  # cutoff behaviour across many subsamples
examples/replay_reference_run.sh     # replay a captured Cromwell task command verbatim
```

Two things those scripts get right that are easy to get wrong:

- **The jar under test is discovered, never assumed.** Confirm it in the log line: the jar path
  and its build timestamp should match the change you just made. A stale jar reproduces the old
  answer with total confidence.
- **`OUT` is resolved once.** If a script assigns the output directory *after* honouring an
  `${OUT:-}` override, a second run silently overwrites the first — including the log you needed
  to diagnose it. That has happened here. Keep one `OUT=${OUT:-…}` assignment and nothing else —
  `run_train_full.sh` and `run_train_definitive.sh` show the form, and honouring an override before
  reassigning the variable is the bug that lost the evidence.

## What local replay cannot tell you

- **The shell genotyper of an older release will not run locally.** v1.1-era tasks shell out to
  `Rscript` and `python2`, which live inside the image. Locally you get
  `Rscript: not found` / `python2: not found`. Don't chase it — reuse that stage's baseline
  outputs and replay only the Java stages.
- **Wall-clock is not comparable to Terra.** Locally you get one big machine; on Terra the same
  stage is a fleet. Expect slower per-sample times, and don't infer performance from it.
- **BGZF speed differs by platform.** `WARN IntelInflaterFactory - IntelInflater is not
  supported, using Java.util.zip.Inflater` is informational, not an error, and it costs throughput
  on Apple Silicon.
- **Memory defaults are tuned for VMs.** `TrainSVGenotyping.trainCopyNumberSites` will reach
  `OutOfMemoryError: Java heap space` at `-Xmx14g` on a full-cohort interval set. That is a real
  property of the RD trainer, not your machine being wrong — either give it the memory the Terra
  task got, or subsample intervals.

## Subsampling, and the trap in it

Subsampling intervals is the standard way to make a full-size run tractable, and it is valid for
SR and PE, whose metrics are interval-insensitive at the batch level. It is **not** valid for RD:
RD cutoffs are distribution statistics computed over the supplied intervals, so a subsample moves
them. A number measured on subsampled intervals is not the number you should publish, and the
gap between the two is large enough to change a conclusion — the same cohort's hom-del cutoff
moved from 3.06× to 2.40× when the interval set went from subsampled to full.

The honest pattern, which `examples/` follows:

1. run SR/PE on a subsample for fast iteration;
2. run RD on the **full** interval set before quoting any cutoff or ratio;
3. label which run each published number came from, next to the number.

## Then verify on Terra

Local replay shortens the loop; it does not replace the pipeline. Once the local numbers look
right, run [the head-to-head](terra-head-to-head.md) — the local run proves the trainer's
arithmetic, the Terra run proves the WDL, the image and the task memory settings around it.
