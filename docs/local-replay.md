# Local replay: run a real gatk-sv stage on your machine

Local replay runs GATK-based pipeline stages on your machine, using the exact inputs a real Terra run
used. It is for iterating on `src/gatk` and `src/sv_shell` changes in minutes instead of one
submission at a time. The genotyping trainers (`TrainSVGenotyping`) are the worked example in
`examples/`, but the same approach applies to any stage whose inputs you can stage locally.

Because the inputs are identical to a frozen baseline, a difference in the local output comes from
your code. Local replay does not reproduce the pipeline around the stage (WDL, image, task memory);
the Terra run does that.

`./gsvtk replay --help` lists the modes. `./gsvtk replay preflight` reports what is missing on this
machine (Java, jar, bcftools, disk). Local replay costs disk and time, not money.

Two modes run the same idea on a throwaway cloud VM instead, for work that needs the real image or more
disk than a laptop has: [replay one task](#replay-one-task-on-a-vm) (`./gsvtk replay task`) and
[sv-shell end to end](#sv-shell-end-to-end) (`./gsvtk replay svshell`). Both print a free plan by
default; `--confirm` boots the VM and costs money.

## What you need

### A GATK jar with your change

```bash
cd <gatk checkout>
JAVA_HOME=<a JDK 17+ home> ./gradlew localJar
export GSVTK_GATK_CHECKOUT=<gatk checkout>
```

The drivers use the newest `build/libs/gatk-package-*-local.jar` under `GSVTK_GATK_CHECKOUT`, or
`JAR=/path/to/jar` if you set it. Use the `-local.jar`: it is the shaded jar with the `sv` command
group, and the plain jar does not have it. If `java` is older than 17, the gatk build stops with its
own error ([troubleshooting.md](troubleshooting.md)).

### The inputs

There are two ways to get them.

**Stage the baseline's input files.** `fetch_baseline.py` resolves a baseline run's per-step inputs
into `$GSVTK_WORK/manifests/baseline_run.json`, and `stage_inputs.py` downloads the files it names:

```bash
python terra/fetch_baseline.py --steps 06 07 08 09 10
python terra/stage_inputs.py --keys rd_file pe_file --link-dir /path/to/panel --dry-run
python terra/stage_inputs.py --attrs sr_metric_file --region chr20
```

* `--keys` selects WDL input names; `--attrs` selects attributes of the `sample_set` named by
  `GSVTK_BATCH`. If that row is not in the manifest, the tool exits 2 and lists the rows that exist.
* `--region` tabix-slices bgzipped objects, which is the cheap way to get a chromosome-scale run.
* `--dry-run` prints the plan and changes nothing.
* `--link-dir` adopts a file you already have instead of downloading it (hardlinked, or copied when
  hardlinks are not possible). A candidate must match the object's size and `crc32c`. A `crc32c`
  mismatch prints `!! NOT adopting …` and downloads the real object; more than one same-size candidate
  is an error. When `crc32c` cannot be compared (composite upload, no `gsutil`, or a file over the
  64 MiB hashing budget), the file is adopted and logged as `unverified: …`. `staged.json` records
  the verdict for every file under `identity`.

If you are also running the Terra head-to-head, the frozen copies are already in your workspace
bucket ([terra-head-to-head.md](terra-head-to-head.md#2-freeze-the-baseline)).

**Rebuild the inputs JSON from a captured task script.** A finished Cromwell task leaves its
rendered `script` in the workspace bucket, and that script contains every input value the task
received. `replay/build_inputs.py` (also `./gsvtk replay inputs`) turns it into a launchable inputs
JSON for a WDL you choose:

```bash
python replay/build_inputs.py --script runsvshell_script.sh --wdl SVShell.wdl \
    --images replay/images.example.json --out-dir "$GSVTK_WORK/replay" --arm branch
```

Every emitted key is prefixed with the root workflow name of `--wdl` (`SVShell.` for `SVShell.wdl`).
Each declared input is classified as recovered from the script, a docker image from `--images`,
left to its WDL default, or unresolved; unresolved required inputs go to `unresolved.json` and are
never guessed. Use `--set NAME=JSON` (with `--set-note NAME=TEXT`) to supply a value you recovered
some other way. A captured argument that the WDL does not declare is reported under
`task_args_not_in_wdl` and not renamed; input renames are
[`checks/svshell_contract_check.py`](../checks/svshell_contract_check.py)'s job. It needs miniwdl;
`python replay/build_inputs.py --selftest` checks it offline.

`examples/replay_reference_run.sh inputs` wraps this for `SVShell`: set `CAPTURED` to the local copy
of the task's `script` and `IMAGES` to an image map.

## Run it

`examples/` holds drivers from a real investigation. They are recipes, not supported interfaces:
read one, copy it, and change it ([examples/README.md](../examples/README.md)). Each sources
`kit/config.sh`, finds your jar, and writes under `GSVTK_WORK`.

| Driver | `gsvtk replay` mode | What it runs |
|---|---|---|
| `examples/run_train_chr20.sh` | `train-chr20` | SR/PE/RD trainers on a chr20 slice, for fast iteration |
| `examples/run_train_full.sh` | `train-full` | the same on the full interval set |
| `examples/run_train_definitive.sh` | `train-definitive` | full intervals with the RD trainer's heap sized |
| `examples/run_rd_population_probe.sh` | `rd-population-probe` | RD cutoff behavior across many subsamples |
| `examples/replay_reference_run.sh` | `reference-run` | one captured task command, verbatim |
| `examples/recompute_het_population.py` | `het-population` | population het statistics recomputed outside the pipeline |

Two things to check on every run:

* **Which jar ran.** The log names the jar path and its build time. A stale jar gives the old answer
  with no warning.
* **Where the output went.** Each driver sets its output directory once, with a single
  `OUT=${OUT:-…}` line. If you copy a driver, keep it that way: reassigning `OUT` after honoring an
  override makes a second run overwrite the first one's log.

## Limits

* **Older shell-based stages do not run locally.** v1.1-era tasks call `Rscript` and `python2` from
  inside the image, so you get `Rscript: not found`. Reuse that stage's baseline outputs and replay
  only the Java stages.
* **Wall-clock time is not comparable to Terra,** where the same stage runs on many VMs.
* **BGZF reading is slower on Apple Silicon.** `IntelInflater is not supported, using
  Java.util.zip.Inflater` is informational.
* **Heap sizes need to match the task.** `TrainSVGenotyping` can hit `OutOfMemoryError: Java heap
  space` at `-Xmx14g` on a full-cohort interval set. Give it the memory the Terra task had, or
  subsample intervals.

## Subsampling intervals

Subsampling intervals is fine for SR and PE, whose metrics do not depend on the interval set at the
batch level. It is not fine for RD: RD cutoffs are statistics over the supplied intervals, so a
subsample moves them, sometimes enough to change a conclusion. The pattern the drivers follow:

1. iterate on SR/PE with a subsample;
2. run RD on the full interval set before quoting any cutoff or ratio;
3. label each reported number with the run it came from.

## Replay one task on a VM

`replay/task_replay.py` (`./gsvtk replay task`) reruns one finished Cromwell task's rendered `script`
inside an image, with that run's real inputs staged at the paths the script expects. By default it is
free: it prints the staging plan with sizes and a cost estimate, the probe, and the
`run_in_image.sh --dry-run` it would launch. `--confirm` boots one VM per image.

```bash
python replay/task_replay.py --metadata run.json --task GatherBatchEvidence --shard 0 \
    --image us.gcr.io/my-project/test/gatk-sv/sv-pipeline:my-branch --save-dir work/replay/gbe
python replay/task_replay.py --script work/replay/gbe/script --call-json work/replay/gbe/call.json \
    --image A --image B --pair --confirm
```

* **Which task.** `--script FILE [--call-json FILE]` works offline.
  `--metadata FILE --task T [--shard N] [--attempt N]` reads the script from the call directory, and
  `--ns --ws --submission --workflow --task T` reads it live; both are read-only. `--save-dir DIR`
  saves `script` and `call.json` for later offline runs.
* **Which image.** `--image REF`, given twice for two arms, or `--image-from CAPTURE --image-key KEY`
  (a capture ref must be a digest). With two images, the run compares rc and output sha256s and exits 0
  `SAME` or 1 `DIFFER`. `--pair` is the guard proof from `checks/sigpipe_pair.sh`: arm A must fail and
  arm B must pass, giving `PAIRED` (0), `VACUOUS` (1) or `GUARD DOES NOT WORK` (1).
* **Inputs.** Localized paths (`/cromwell_root/...`, and `/mnt/disks/cromwell_root` on the Batch
  backend) and declared `gs://` inputs are staged on the VM host at the same paths, and that root is
  bind-mounted into the container, so the script is not rewritten. `gs://` strings inside the script
  are left alone unless `--rewrite-gs`. Sizes come from `gsutil ls -l` (read-only) or `--sizes JSON`;
  objects that are not found are listed and not staged. `--max-gb` (default 50) refuses oversized
  staging, `--confirm` with unknown sizes is refused, and `--billing-project` handles requester-pays
  buckets.
* **VM.** `--machine` defaults to a size from the task's runtime attributes; `--disk`, `--timeout`,
  `--hours`, `--project` (default `GSVTK_PROJECT`) and `--zone` are also available. `--show-startup`
  prints the whole startup script.
* **Report.** The task rc, each new file under the root with sha256 and size, and any declared output
  that is missing.

Exit codes: 0 ok, 1 failed, differ or vacuous, 2 refused, 3 missing dependency, 4 missing config, 5 not
scored. Under `GSVTK_READ_ONLY=1`, `--confirm` is refused before anything is read; the plan still runs.

## sv-shell end to end

`replay/svshell_run.py` (`./gsvtk replay svshell`) runs `single_sample_pipeline.sh` in a branch's
`sv-shell` image on a VM and compares the final VCF with a baseline.

```bash
python replay/svshell_run.py --inputs na12878.json --image REF --baseline base.vcf.gz
python replay/svshell_run.py --inputs na12878.json --image REF --baseline base.vcf.gz \
    --out-uri gs://my-bucket/svshell-runs --confirm
python replay/svshell_run.py --fetched work/svshell/<stamp>/final.vcf.gz --baseline base.vcf.gz
```

* **The inputs JSON** uses the driver's own top-level keys, as in
  `src/sv_shell/sample_inputs/single_sample_pipeline.json`, with `gs://` file values. List keys
  (`ref_samples_list`, the `ref_pesr_*_files_list` keys, `gcnv_model_tars_list`) may be JSON arrays, a
  local file or a `gs://` file.
* **Validation before anything runs.** The driver is read from `--checkout` (default
  `GSVTK_GATK_SV_CHECKOUT`; match it to the image's branch) or `--driver FILE`. A key the driver reads
  with no default and that is missing from your JSON is refused (exit 2), naming every missing key;
  `--allow-missing KEY` runs anyway with that key null. A key the driver only passes to a module that
  defaults it counts as optional. Absolute non-`gs://` paths are refused unless `--image-paths`, and a
  `gs://` object that does not exist is refused.
* **Staging.** `gs://` values are staged under `/sv/inputs/` on the VM and the JSON is rewritten to
  those paths, with sibling `.tbi` files where they exist. `--max-gb` defaults to 300.
* **The run.** `--confirm` also needs `--out-uri gs://bucket/prefix`. The VM uploads `final.vcf.gz`,
  `outputs.json` and `driver.log` to `<out-uri>/<stamp>/`, and the plan prints the `gsutil -m rm -r`
  that removes them. Defaults: `--machine e2-standard-16`, `--hours 10`.
* **The compare.** The fetched VCF is compared with `compare/vcf_body_diff.py`, or `vcf_paired_diff.py`
  with `--comparator paired`; `--compare-arg` passes options through. The exit code is the
  comparator's (0 same, 1 differ, 2 nothing compared), or 1 if the pipeline failed or uploaded nothing.
  `--fetched VCF` compares a VCF you already have, at no cost.

The shipped `sample_inputs/single_sample_pipeline*.json` fixtures do not supply
`pesr_breakend_window`, which the driver passes to `cluster_batch.sh` with no default, so this tool
refuses them unless you add the key or pass `--allow-missing pesr_breakend_window`
([roadmap.md](roadmap.md#decisions-for-the-maintainers)).

### What is verified for both

Verified offline only: plan output; staging and path rewriting on synthetic scripts (both backend
roots); the probe's rc logic, run locally with bash; every refusal; that without `--confirm` only
`run_in_image.sh --dry-run` is called; the report and pair verdicts; the comparator exit codes with the
real `vcf_body_diff.py`; and that `run_in_image.sh --dry-run` assembles and validates the startup
scripts.

Unverified until a real cloud run: staging on the VM host with the VM's metadata token (including
requester-pays buckets and token refresh during long staging); that the container sees the bind mount
as expected; that real Cromwell `script` files replay cleanly (`mkfifo` wrappers, the Batch backend
layout and outputs written outside the root are untested); the upload to `--out-uri` (a simple upload,
so each file must be under 5 GB); sv-shell's runtime, disk and machine sizing; and the fetch back with
`gsutil cp`.

## Then verify on Terra

Once the local numbers look right, run [the head-to-head](terra-head-to-head.md). The local run checks
the tool's arithmetic; the Terra run checks the WDL, the image and the task settings around it.
