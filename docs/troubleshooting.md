# Troubleshooting: the error text you will actually see

Every entry here was hit for real. The left column holds the message exactly as printed, so
searching for your error should land you on the right row.

## Credentials and networks

| You see | What it means | Do this |
|---|---|---|
| `curl: (6) Could not resolve host: api.terra.bio` | the Terra hostname does not resolve on some networks. Re-verified, still true | use `https://api.firecloud.org/api/`, which the tools pin. Verify with `curl -s -o /dev/null -w '%{http_code}\n' https://api.firecloud.org/api/version` |
| `401` from `/api/version` | **this is the good outcome.** You reached the API and are simply not authenticated | `gcloud auth application-default login` |
| `ERROR: No matching distribution found for fiss` | the PyPI package is named `firecloud`, not `fiss` | `pip install firecloud` |
| `missing dependency: firecloud` | venv not active | `source .venv/bin/activate` or `make setup` |
| A build/probe VM reports `Insufficient permissions on ... gs://us.artifacts.<project>.appspot.com` | the **VM's** service account cannot push. `--impersonate-service-account` changes who calls the API, not the VM's identity, so it cannot fix this | run the `gcloud storage buckets add-iam-policy-binding` the script prints, or pass `--service-account <SA that can push>` |

**The `--impersonate` trap.** A GCE VM acts as its attached service account, and changing who calls
the API does not change that. Every "access denied" inside a build log is a statement about the VM's
identity.

## Terra submissions

| You see | What it means | Do this |
|---|---|---|
| Batch-level `this.<attr>` bindings resolve to nothing, or a `select_all` comes back empty | `GSVTK_BATCH` does not name a real `sample_set` in that workspace. A wrong entity name passes typechecking and fails at runtime, after VMs booted | it must be exactly `all_samples` unless you renamed it. Check `batch_configs.py show` |
| Workflow starts then `UnboundDestinationVariableException` on a batch-level expression | submitted against the wrong root entity: `09-MergeBatchSites` needs `sample_set_set`, `06`/`07`/`08`/`10` need `sample_set` | `batch_configs.py show` prints `rootEntityType` per config; that table is the authority |
| HTTP 404 `Cannot get dockstore://... from method repo` | either the URI is not byte-exact (Rawls validates `methodRepoMethod` on overwrite, and every `/` in the path is `%2F`-encoded, including the ones in `github.com`), or that ref was never published to Dockstore. Both mean the config points at a WDL Terra cannot fetch | use `batch_configs.dockstore()` / `batch_rerun_step.dstore()` rather than hand-writing the URI, and check the ref is published. If it is published but the shape is wrong, you are about to see the sibling symptom below: run `python terra/batch_configs.py check --against <ref>` |
| A submission fails with Rawls reporting an **extra / unexpected input** in the method config, although `create` succeeded | `GSVTK_BRANCH` picks the Dockstore URL, but the config's input keys are a snapshot of one branch's WDL signature. Point it at a ref that does not declare one of those keys and you get this at submission. Known case: `GenotypeBatch.training_vcf` (declared on the branch under test; main's `GenotypeBatch` declares 21 inputs and has no `training_vcf`). Rawls' own verdict, captured on a repeat of it: `extraInputs: ["GenotypeBatch.training_vcf"]` with `invalidInputs: {}`, `missingInputs: []`, `invalidOutputs: {}`, one stray key and nothing else wrong, `create` HTTP 200 throughout, `submit` HTTP 400 | `python terra/batch_configs.py check --against <ref>` names it offline, from your own checkout, no Dockstore needed; `create`/`validate`/`submit` run the same comparison first, on the config path *and* the rerun path. Then fix `GSVTK_BRANCH`, fix the map, post it as-is knowingly with `--allow-unknown-inputs` (prints that it took the override), or, to post a map that fits the *other* ref, `--drop-branch-only-inputs`, which prunes known-branch-only keys and prints what that changes about the run |
| `create: refusing to continue -- these maps do not fit the WDL at <ref>` | `create`/`validate` pre-check the maps against the ref and found an `EXTRA`, a `MISSING`, or a `CANNOT CHECK` | read the lines above it. `CANNOT CHECK` means the workflow file is not in that tree: a comparison that did not happen is not a pass. `pre-check SKIPPED` instead means no `GSVTK_BRANCH`/`GSVTK_GATK_SV_CHECKOUT`, which is also not a pass |
| Every shard fails `ImagePullBackupFailed` | an image placeholder reached the config unresolved | every mode but `show` is refused while nothing is pinned; fix `--image` or name `GSVTK_IMAGE_REPO` in the profile |
| A config binding you set had no effect, WDL default won | the binding is an *expression*, not a value `batch_check_inputs.py` prints `FAIL expression-shaped bindings` and exits 1; make it a literal path or a plain attribute reference |
| A rerun reproduced the old answer exactly | images came from workspace attributes, so it ran whatever the attribute pointed at | pin images literally (`batch_rerun_step.py` exists for this) and confirm the ref in `show` output |
| Cost looks low vs the dashboard | `batch_cost.py` reports **VM-minutes and job counts**, never a price: it is not trying to match your bill | multiply by your own per-type rate; the difference should be explained by disk, network and spot pricing. A tree with `_missingSubWorkflows` is a floor, and the tool says so |
| A baseline input vanished between two comparisons | it lived in an ephemeral `fc-*` bucket, or a workspace attribute moved | that is what `batch_freeze.py copy --write` + `verify` are for; compare `crc32c` and byte size before believing any diff |
| `HTTP 405` from `POST /api/workspaces//methodconfigs`; note the empty workspace between the slashes | the target workspace/project resolved to **nothing** and the URL was built from the hole. Nothing was created (Terra rejected it), but a mutating request did leave the machine with no target in it | the tools now resolve and validate the full target identity *before* the first request and exit 1 naming the missing key. Check `./kit/gsvtk-config show`, and remember a `[profile:...]` value can come from a file you are not looking at |
| `refusing to <mutate> the shared baseline workspace ...` | your `GSVTK_TERRA_NAMESPACE`/`GSVTK_TERRA_WORKSPACE` still point at the public featured GATK-SV workspace, i.e. everyone's baseline | set your own sandbox, or pass `--allow-shared-target` if you genuinely mean to modify the shared one. Every mutator (`copy --write`, `attrs --write`, `configs create --confirm`, `rerun create --confirm`, `rerun submit`) enforces this |
| `...records no PASSED crc32c verify` / `no frozen-input manifest at ...` | `attrs` publishes only over a recorded `"verified": true`: absent, unreadable and failed all read as "nobody proved these objects" | run `python terra/batch_freeze.py verify` (read-only) and resolve any mismatch; `--allow-unverified` exists but prints that it was taken |
| `stage_inputs: no sample_set row named '...' in ... (rows present: ...)` (exit 2) | `GSVTK_BATCH` names a row the frozen manifest does not contain, the freeze and the staging were pointed at different batches | set `GSVTK_BATCH` to one of the named rows, or re-run `fetch_baseline.py --entity <name>`. Selecting nothing and exiting 0 is the failure this stops |
| `cannot flatten: <name> is declared by both A.wdl and B.wdl` | two different files in the closure declare one task/struct, so one document would declare it twice | upstream fix or a narrower entry point: choosing which declaration survives changes what the pipeline runs, so `wdl_flat.py` refuses rather than guessing. Importing one file twice is fine |
| `chain total is PARTIAL: no saved metadata for 06/baseline` (exit 1) | one step's Cromwell dump is missing, and a missing step is not zero cost: the chain total and the ratio are withheld on purpose | run `batch_save_metadata.py` for the missing step; see [terra-head-to-head.md](terra-head-to-head.md) |
| `N sub-workflow call(s) have no expanded tree below them -> cost is a FLOOR` | the saved tree was depth-capped, trimmed, or is an older file; `_missingSubWorkflows` was empty, so it *looked* complete | re-fetch with `batch_save_metadata.py`; treat the printed VM-minutes as a lower bound until it says `these are measurements` |
| `N local files are named <x> with the object's size, so adoption cannot pick one` | two `--link-dir` captures both contain an object of that name and size | narrow `--link-dir` to one capture, or drop it and download. Guessing by glob order is how a stale table becomes a "baseline" input |
| `!! NOT adopting <path>: crc32c:mismatch … -- downloading the object instead` | a same-name same-size local file is **not** the current baseline object | nothing to fix: the tool refused a wrong input. If you expected it to match, the local capture is stale |
| `400 "The request content was malformed:\nunexpected json type"` | **two** unrelated causes, and the message names neither: `methodVersion` must be an **Int** on an agora ref, and real JSON list/int values in `inputs` (Rawls wants strings) | send `methodVersion` unquoted, and stringify every value in `inputs` |
| `Validation errors: Invalid outputs: … -> Error while parsing the expr` | an inverted `outputs` map is **accepted at config creation** and only dies at submission; `invalidOutputs` reads 0 even for a name the workflow never declared | diff the `outputs` keys against the workflow's declared outputs before resubmitting, because a `valid` from creation is not evidence |
| `400 Entity type workspace is reserved and cannot be overwritten` | the `rootEntityType: workspace` dead end. Its two predecessor errors are `400 … you haven't passed one to the submission` and `500 AttributeEntityReference(workspace,…) not found` | use the collection's real root entity type (`sample` in a sample-per-row workspace), never `workspace` |
| `500 AttributeEntityReference(workspace,…) not found` | the same dead end, arriving one step earlier | same: `rootEntityType` is not `workspace` |
| `409 <config> already exists` on a retry | Rawls **persists a config whose method resolution failed**, after which `valid` reads `None` forever and `POST …/methodconfigs/validate` answers `405 supported methods: OPTIONS` | create it under a new name. Re-validating the stuck name cannot recover it |
| `HTTP Error 405: Method Not Allowed` | a POST-only Rawls endpoint reached with GET, `…/methodconfigs/validate` is the usual one | POST it, or read `valid` off the submission record instead |

## Docker builds

| You see | What it means | Do this |
|---|---|---|
| `The Linux/chromeos image ... cannot be built on this machine` / no Docker at all | gatk-sv's build assumes a working Docker daemon; Apple Silicon cannot build `linux/amd64` locally without emulation | that is the entire premise of `docker/gatk-sv-build.sh`: build on a throwaway x86_64 VM. If you insist on local, add `--platform linux/amd64` and expect QEMU to make an `sv-pipeline` build effectively endless |
| `nvmm: INFO: Per-minute rates ...` at the top of the log | good sign: the compute API is enabled, credentials work, and you just learned the price of the VM you are about to start | nothing |
| `Operation timed out` polling the serial port, with a booting VM | `--timeout` elapsed, not a failure. The VM stays up, still billing | read the log with `gcloud compute instances get-serial-port-output <vm> --project <P> --zone <Z>`, then delete it with `gcloud compute instances delete <vm> --project <P> --zone <Z>` |
| Build failed, and the VM is gone | an ephemeral VM is deleted only on the **success** path; on failure it is always kept and self-shuts-down, so if it is gone something deleted it, you, or a re-run with the same name | read the serial log BEFORE deleting anything: `--boot-disk-auto-delete` means a delete takes the evidence with it |
| Build succeeded and the VM is still running | `--keep` (ephemeral) or `--keep-running` (persistent) on purpose, or a driver killed before its delete step | `gcloud compute instances delete <name> --zone <zone>`; inventory with `gcloud compute instances list --project <P> --filter 'name~^gsv-'` |
| `apt-get` refused / `Permission denied (publickey)` on first connect | cloud-init is still running | the persistent driver waits up to 30 × 10 s for SSH; if you hit the ceiling, the image or zone is having a day: retry, do not hand-hack the wait |
| Image pushed but the pipeline used something else | you pushed to a path the pipeline reads, or tagged something already in use | always push to a namespace nothing reads (`GSVTK_IMAGE_NAMESPACE`), and tag `<branch>-<sha>` so a tag is never reused |
| `disk-full` / build dies while copying the gatk jar | the jar is copied into the `sv-base` layer on purpose, to avoid a 12-minute `docker cp` on every task launch, so a too-small disk fails late | `--disk-size 200` |
| `Job for docker0 …` / `rc=125` … `not found` | an image reference assembled from the build log's *prefix* and *name*, which the log prints separately, a space where the tag separator belongs | take the ref from one log line, never from two |
| `error: branch '<under-test>' not found on github.com/broadinstitute/gatk-sv`, then `gsvtk: preflight failed; fix that before anything else` | `docker/gatk-sv-build.sh --check` refusing **before** a VM boots, because the branch under test is not pushed | push the branch. A refusal that names the cause while compute is still $0 is the preflight working, not a defect |
| `##[error]Readonly file modified: .github/.dockstore.yml` (job `Verify`, `readonly_check.yaml`) | gatk-sv's own CI, not anything in this kit: only `gatk-sv-bot` is exempt | drop the `.github/` edit from the PR, and know that deleting those commits does **not** unpublish Dockstore versions already published |
| a red `Test Images Build (3.8)` starting one minute after another developer's branch | shared build infra failing for someone else's change | before debugging yours, check whether the same job is red on an unrelated branch |

## Local replay

| You see | What it means | Do this |
|---|---|---|
| `A Java 17 compatible (Java 17 or later) version is required to build GATK, but 11 was found.` | your default `java` is older than the build requirement | `JAVA_HOME=<jdk17 home> ./gradlew localJar`. Do not patch GATK's `build.gradle` to lower the bound: it exists because the toolchain needs the newer compiler |
| `java.lang.OutOfMemoryError: Java heap space` in `DepthEvidenceGenotyper.train` ← `TrainSVGenotyping.trainCopyNumberSites`, after tens of minutes | a real property of the RD trainer at full-cohort interval scale, not a misconfiguration | give it the memory the Terra task got, or subsample intervals; **but see the RD caveat**: subsampling intervals moves RD cutoffs |
| `Rscript: not found`, `python2: not found` | v1.1-era shell genotypers depend on interpreters that live only inside the image | do not try to run that stage locally; reuse its baseline outputs and replay the Java stages |
| `WARN IntelInflaterFactory - IntelInflater is not supported, using Java.util.zip.Inflater` | informational: no Intel Deflater/Inflater on this platform, so BGZF is slower | nothing. Expect longer wall-clock than Terra |
| `du -sh staging` reports tens of GB that you did not download | hardlinks into an existing panel copy (`--link-dir`) | `stat -f '%l' <file>` before concluding what deleting will free |
| Your second run overwrote the first, log included | `OUT` assigned after an `${OUT:-}` default, so the override was silently ignored | fixed in these scripts; if you copy one, keep a single `OUT=${OUT:-...}` line. `ls $OUT` after launching |
| An interval-list step dies with a sort error | `-S` size limits on a big list | sort with an explicit buffer: `sort -k1,1 -k2,2n -S 512M` |
| `invalid choice: 'inputs'` from miniwdl | the subcommand is `input_template`, and the list it prints is required-only, so a **defaulted** input looks unknown to it | `miniwdl input_template <wdl>`; read "not listed" as "has a default", not "does not exist" |
| `[E::vcf_format] Invalid BCF, the INFO tag id=16 is too large` | a pysam version divergence, not a corrupt VCF: the production stack pins `pysam==0.15.4`, and a local 0.24 cannot write a header-`add_line`-added INFO tag through `resolve.py`'s `bcftools sort` stdin pipe | run that step inside the image before concluding the VCF is broken: this one cost a docker rebuild to prove |

## Configuration

| You see | What it means | Do this |
|---|---|---|
| `gsvtk-config: GSVTK_PROJECT is not set ...` (exit 4) | working as designed: this key decides whose money is spent | set it in `testkit.env`; `./kit/gsvtk-config doctor` shows what is missing |
| `... is not a git repository` | `GSVTK_GATK_SV_CHECKOUT` points at a directory that is not a clone | point it at a real clone, or unset it and let a check take `--repo` |
| A comparison came back completely empty | two tools disagree about the attribute suffix: one wrote `*_new`, the other read `*_newest` | keep `GSVTK_NEW_SUFFIX`/`GSVTK_FROZEN_SUFFIX` constant across the profile and every invocation |
| Something used a stale value after you edited the profile | a later file in the chain wins, an exported env var shadows it, or `GSVTK_CONFIG` is set, which **replaces** the whole chain rather than joining it | `./kit/gsvtk-config show` prints each value's source (the **file path**, not just `profile`); `doctor` lists the files read and marks the missing ones |
| `kit/config.sh: the configuration layer produced no exports …` on stderr | sourcing `kit/config.sh` never hard-fails (so `--help` works unconfigured), but here the resolver itself failed and the tool is about to fall back to inline defaults | fix what the printed resolver error says. Do not ignore it: a silent fallback means the run is using someone else's project/workspace, not yours |

## When a check says OK and you do not believe it

You are right to be suspicious. Every false OK below existed here, and every one was found by attack,
not by use. Each now fails loudly instead:

| Symptom | What had been happening | Now |
|---|---|---|
| `=> OK: every jq block executed` | blocks written slightly differently from the canonical `jq -n \` shape were never extracted, so they were never executed | the scan prints `blocks: P in file, E extracted, X executed, …` and refuses a partial scan: `X < P` is exit 1, "a verdict from a partial scan means nothing" |
| contract check with no findings | it compared a subset of stage calls (bare/dashed/single-quoted keys and `// default` reads were invisible) | header prints `N of M stage calls compared`; `N < M` ⇒ `NOT PROVEN` and exit 1 |
| `byte-identity check passed` | one blanket line for the whole run, quoting md5s nobody had supplied as expectations | `PROVEN` only when both `--expect-*-md5` were given and matched; otherwise `SCAN_CLEAN`, per file |
| `wdl_gate.sh --strict` said `no hard errors` | the workflow you named with `--wf` did not exist at that ref, so nothing was checked | named-but-absent is a failure that says nothing was checked |
| `make selftest` printed a wall of `ok` | in a Makefile recipe, `$$($("$@") 2>&1)` is eaten by make: bash got `out="( 2>&1)"`, the command never ran, and `$?` was the substitution's success. Eight config assertions, including env > profile precedence, were permanently vacuous | assertions live in `scripts/selftest.sh` (no make escaping), and a **canary** asserts that a known-failing command is reported as failing; if the harness ever goes vacuous again, the gate fails on the canary |
| a checker that detects nothing still passes the gate | `make test` only ran `--help` and `py_compile`, and selftests asserted "exit code was acceptable", which a blind checker satisfies | `make smoke` invokes the tools end-to-end; selftests require positive-control numbers (blocks executed == blocks present, ≥ 12 stage calls compared) |
| the runner printed `MISSING LOCAL IMAGE` and carried on | both arms then die `rc=1` with `records=0`, which reads **exactly** like the flag under test changed nothing | stop and fix the image ref: an arm that never started is not a result. `checks/image-check/run_in_image.sh` refuses before booting anything: no `--image`, a 0-byte `--probe` script, or an `--image` containing a space (the build log prints a prefix and a name separately, and joining them with a space is how a wrong ref gets born) |

## When a tool here is wrong

Say so, in the issue, with the command and the full error. These tools were built by running
them against real runs, which means they encode assumptions (about attribute names, entity
types, image layout, Cromwell log formats) that a different gatk-sv version can break. A tool
that reports `I could not find X, expected it at Y` is much more useful than one that returns an
empty table, and if you find the second kind, that is a bug worth filing.
