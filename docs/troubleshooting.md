# Troubleshooting: the error text you will actually see

Every entry here was hit for real. The left column is the message, not a paraphrase — searching
for your exact error should land you here.

## Credentials and networks

| You see | What it means | Do this |
|---|---|---|
| `curl: (6) Could not resolve host: api.terra.bio` | the Terra hostname does not resolve on some networks. Re-verified, still true | use `https://api.firecloud.org/api/` — the tools pin it. Verify with `curl -s -o /dev/null -w '%{http_code}\n' https://api.firecloud.org/api/version` |
| `401` from `/api/version` | **this is the good outcome.** You reached the API and are simply not authenticated | `gcloud auth application-default login` |
| `ERROR: No matching distribution found for fiss` | the PyPI package is named `firecloud`, not `fiss` | `pip install firecloud` |
| `missing dependency: firecloud` | venv not active | `source .venv/bin/activate` or `make setup` |
| A build/probe VM reports `Insufficient permissions on ... gs://us.artifacts.<project>.appspot.com` | the **VM's** service account cannot push. `--impersonate-service-account` changes who calls the API, not the VM's identity, so it cannot fix this | run the `gcloud storage buckets add-iam-policy-binding` the script prints, or pass `--service-account <SA that can push>` |

**The `--impersonate` trap is worth internalising.** GCE VMs act as their attached service
account. Changing the caller does not change the VM. Every "access denied" inside a build log is
a statement about the VM's identity.

## Terra submissions

| You see | What it means | Do this |
|---|---|---|
| Batch-level `this.<attr>` bindings resolve to nothing, or a `select_all` comes back empty | `GSVTK_BATCH` does not name a real `sample_set` in that workspace. A wrong entity name passes typechecking and fails at runtime, after VMs booted | it must be exactly `all_samples` unless you renamed it. Check `batch_configs.py show` |
| Workflow starts then `UnboundDestinationVariableException` on a batch-level expression | submitted against the wrong root entity: `09-MergeBatchSites` needs `sample_set_set`, `06`/`07`/`08`/`10` need `sample_set` | `batch_configs.py show` prints `rootEntityType` per config; that table is the authority |
| HTTP 404 `Cannot get dockstore://... from method repo` | Rawls validates `methodRepoMethod` on overwrite, and the URI must be byte-exact — every `/` in the path is `%2F`-encoded, including the ones in `github.com` | use `batch_configs.dockstore()` / `batch_rerun_step.dstore()` rather than hand-writing the URI |
| Every shard fails `ImagePullBackupFailed` | an image placeholder reached the config unresolved | every mode but `show` is refused while nothing is pinned; fix `--image` or name `GSVTK_IMAGE_REPO` in the profile |
| A config binding you set had no effect, WDL default won | the binding is an *expression*, not a value `batch_check_inputs.py` prints `FAIL expression-shaped bindings` and exits 1; make it a literal path or a plain attribute reference |
| A rerun reproduced the old answer exactly | images came from workspace attributes, so it ran whatever the attribute pointed at | pin images literally (`batch_rerun_step.py` exists for this) and confirm the ref in `show` output |
| Cost looks low vs the dashboard | `batch_cost.py` reports **VM-minutes and job counts**, never a price — it is not trying to match your bill | multiply by your own per-type rate; the difference should be explained by disk, network and spot pricing. A tree with `_missingSubWorkflows` is a floor, and the tool says so |
| A baseline input vanished between two comparisons | it lived in an ephemeral `fc-*` bucket, or a workspace attribute moved | that is what `batch_freeze.py copy` + `verify` are for; compare `crc32c` and byte size before believing any diff |

## Docker builds

| You see | What it means | Do this |
|---|---|---|
| `The Linux/chromeos image ... cannot be built on this machine` / no Docker at all | gatk-sv's build assumes a working Docker daemon; Apple Silicon cannot build `linux/amd64` locally without emulation | that is the entire premise of `docker/gatk-sv-build.sh`: build on a throwaway x86_64 VM. If you insist on local, add `--platform linux/amd64` and expect QEMU to make an `sv-pipeline` build effectively endless |
| `nvmm: INFO: Per-minute rates ...` at the top of the log | good sign — the compute API is enabled, credentials work, and you just learned the price of the VM you are about to start | nothing |
| `Operation timed out` polling the serial port, with a booting VM | `--timeout` elapsed, not a failure. The VM stays up, still billing | read the log with `gcloud compute instances get-serial-port-output <vm> --project <P> --zone <Z>`, then delete it with `gcloud compute instances delete <vm> --project <P> --zone <Z>` |
| Build failed, and the VM is gone | an ephemeral VM is deleted only on the **success** path; on failure it is always kept and self-shuts-down, so if it is gone something deleted it — you, or a re-run with the same name | read the serial log BEFORE deleting anything: `--boot-disk-auto-delete` means a delete takes the evidence with it |
| Build succeeded and the VM is still running | `--keep` (ephemeral) or `--keep-running` (persistent) on purpose — or a driver killed before its delete step | `gcloud compute instances delete <name> --zone <zone>`; inventory with `gcloud compute instances list --project <P> --filter 'name~^gsv-'` |
| `apt-get` refused / `Permission denied (publickey)` on first connect | cloud-init is still running | the persistent driver waits up to 30 × 10 s for SSH; if you hit the ceiling, the image or zone is having a day — retry, do not hand-hack the wait |
| Image pushed but the pipeline used something else | you pushed to a path the pipeline reads, or tagged something already in use | always push to a namespace nothing reads (`GSVTK_IMAGE_NAMESPACE`), and tag `<branch>-<sha>` so a tag is never reused |
| `disk-full` / build dies while copying the gatk jar | the jar is copied into the `sv-base` layer on purpose, to avoid a 12-minute `docker cp` on every task launch — so a too-small disk fails late | `--disk-size 200` |

## Local replay

| You see | What it means | Do this |
|---|---|---|
| `A Java 17 compatible (Java 17 or later) version is required to build GATK, but 11 was found.` | your default `java` is older than the build requirement | `JAVA_HOME=<jdk17 home> ./gradlew localJar`. Do not patch GATK's `build.gradle` to lower the bound — it exists because the toolchain needs the newer compiler |
| `java.lang.OutOfMemoryError: Java heap space` in `DepthEvidenceGenotyper.train` ← `TrainSVGenotyping.trainCopyNumberSites`, after tens of minutes | a real property of the RD trainer at full-cohort interval scale, not a misconfiguration | give it the memory the Terra task got, or subsample intervals — **but see the RD caveat**: subsampling intervals moves RD cutoffs |
| `Rscript: not found`, `python2: not found` | v1.1-era shell genotypers depend on interpreters that live only inside the image | do not try to run that stage locally; reuse its baseline outputs and replay the Java stages |
| `WARN IntelInflaterFactory - IntelInflater is not supported, using Java.util.zip.Inflater` | informational: no Intel Deflater/Inflater on this platform, so BGZF is slower | nothing. Expect longer wall-clock than Terra |
| `du -sh staging` reports tens of GB that you did not download | hardlinks into an existing panel copy (`--link-dir`) | `stat -f '%l' <file>` before concluding what deleting will free |
| Your second run overwrote the first, log included | `OUT` assigned after an `${OUT:-}` default, so the override was silently ignored | fixed in these scripts; if you copy one, keep a single `OUT=${OUT:-...}` line. `ls $OUT` after launching |
| An interval-list step dies with a sort error | `-S` size limits on a big list | sort with an explicit buffer: `sort -k1,1 -k2,2n -S 512M` |

## Configuration

| You see | What it means | Do this |
|---|---|---|
| `gsvtk-config: GSVTK_PROJECT is not set ...` (exit 4) | working as designed — this key decides whose money is spent | set it in `testkit.env`; `./kit/gsvtk-config doctor` shows what is missing |
| `... is not a git repository` | `GSVTK_GATK_SV_CHECKOUT` points at a directory that is not a clone | point it at a real clone, or unset it and let a check take `--repo` |
| A comparison came back completely empty | two tools disagree about the attribute suffix — one wrote `*_new`, the other read `*_newest` | keep `GSVTK_NEW_SUFFIX`/`GSVTK_FROZEN_SUFFIX` constant across the profile and every invocation |
| Something used a stale value after you edited the profile | a later file in the chain wins, an exported env var shadows it, or `GSVTK_CONFIG` is set — which **replaces** the whole chain rather than joining it | `./kit/gsvtk-config show` prints each value's source; `doctor` lists the files read and marks the missing ones |

## When a tool here is wrong

Say so, in the issue, with the command and the full error. These tools were built by running
them against real runs, which means they encode assumptions — about attribute names, entity
types, image layout, Cromwell log formats — that a different gatk-sv version can break. A tool
that reports `I could not find X, expected it at Y` is much more useful than one that returns an
empty table, and if you find the second kind, that is a bug worth filing.
