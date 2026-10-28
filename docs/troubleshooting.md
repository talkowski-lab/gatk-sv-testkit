# Troubleshooting

Each table is keyed on the text you see, so you can search this page for your error. Messages
printed by this kit's own tools are quoted as the tools print them. Messages from Terra (Rawls),
Google Cloud, Java and other external tools are quoted as those systems report them.

## Credentials and networks

| You see | What it means | Do this |
|---|---|---|
| `Could not resolve host: api.terra.bio` (from curl or a probe of your own) | The Terra hostname does not resolve on some networks. | Use `https://api.firecloud.org/api/`, which the tools use by default. Test with `curl -s -o /dev/null -w '%{http_code}\n' https://api.firecloud.org/api/version`. |
| `401` from `/api/version` | You reached the API and are not authenticated. The network path is fine. | `gcloud auth application-default login` |
| `ERROR: No matching distribution found for fiss` | The PyPI package is named `firecloud`. | `pip install firecloud`, or `make setup`. |
| `missing dependency: firecloud` | The interpreter running the tool has no `firecloud`, usually because the venv is not active. | `source .venv/bin/activate` or `make setup`. |
| `gsvtk: no interpreter with firecloud: ...` | `gsvtk` looks for an interpreter in `GSVTK_TERRA_PY`/`GSVTK_PY`, then `.venv/bin/python`, then `python3`, and none of them has `firecloud`. | Run `make setup` in the checkout, or export `GSVTK_TERRA_PY=<python with firecloud>`. |
| A build VM reports `Insufficient permissions` pushing to `gs://...artifacts...` | The VM's attached service account cannot push to the registry. Impersonating another account changes who calls the GCE API, not the identity the VM runs as. | Run the `gcloud storage buckets add-iam-policy-binding` command that `docker/gatk-sv-build.sh` prints, or pass `--service-account <SA that can push>`. |

## Terra configs and submissions

| You see | What it means | Do this |
|---|---|---|
| `refusing to <action> in the baseline workspace <ns>/<ws>.` | `GSVTK_TERRA_NAMESPACE`/`GSVTK_TERRA_WORKSPACE` point at the shared baseline workspace. Terra merges entity attributes, so a write there cannot be undone. | Point the profile at your own sandbox (`./kit/gsvtk-config show` names the file each value came from). Pass `--allow-shared-target` only if you really mean to modify that workspace. |
| `<tag>: refusing to continue -- these maps do not fit the WDL at <ref>.` | `create`/`validate` compared the config's input and output keys against the WDL at that ref and found an `EXTRA`, a `MISSING`, or a `CANNOT CHECK`. | Read the lines above it. `CANNOT CHECK` means the workflow file is not in that tree, so nothing was compared. `pre-check SKIPPED` means no `GSVTK_BRANCH` or `GSVTK_GATK_SV_CHECKOUT` was set; that is also not a pass. |
| Submission fails with Rawls reporting `extraInputs: [...]` although `create` succeeded | `GSVTK_BRANCH` selects the Dockstore URL, but the config's input keys come from one branch's WDL. Pointing at a ref that does not declare one of those keys fails at submission, not at creation. | `python terra/batch_configs.py check --against <ref>` names the stray key offline. Fix `GSVTK_BRANCH` or the map. `--allow-unknown-inputs` posts the map anyway and says so; on reruns, `terra/batch_rerun_step.py --drop-branch-only-inputs` removes known branch-only keys and prints what it dropped. |
| HTTP 404 `Cannot get dockstore://... from method repo` | The Dockstore URI is not exact (every `/` in the path is `%2F`-encoded, including those in `github.com`), or that ref was never published to Dockstore. | Let the tools build the URI rather than writing it by hand, and check that the ref is published (`./gsvtk publish-check <branch>`). |
| `refusing to <mode>: the Dockstore pin is not current (STALE).` or `(ABSENT)` | The Dockstore version the rerun names does not serve the branch tip (`STALE`), or no such version is published (`ABSENT`). Terra would run code other than your branch. | Publish the tip as that version, or pass `--allow-stale-dockstore` if running the older WDL is intended. |
| `publish check: ... the check declined, so the pin is UNVERIFIED.` | The publish check could not be run (no capture, network refused). This is not an absence verdict, and `--allow-stale-dockstore` does not apply to it. | Fix the network or capture and run the check again. |
| `gsvtk: REFUSED -- Dockstore could not be consulted for <entry>` | `publish`/`publish-check` could not read one entry (no network, a non-JSON body, or an unreachable `GSVTK_DOCKSTORE_API_ROOT`). It stops instead of grading a partial capture, which would report the missing entry as `ABSENT`. | Fetch the URL it prints yourself and grade the file with `checks/version_resolves.py --from-file <capture>`. |
| `publish`/`publish-check` answers `ABSENT` for a version you can see in the Dockstore UI | The capture came from a route that lists version names without commits. The run prints `capture: <shape>` first; `TRS tools listing` means no commit was available in that file. | Use `./gsvtk publish-check <branch>`, which fetches the route that carries commits, or capture `.../published?include=versions` and pass it with `--from-file`. |
| `image inputs are not fully pinned`, with `unpinned:  <key>` | No ref is stated for that image, so Terra would resolve it from a workspace attribute at run time. | Pass `--image KEY=REF` for each key, or `--allow-unpinned-docker` if you want the workspace attribute to decide. |
| `STATE: PINNED BUT UNTAGGED` with `pinned but unversioned:  <key> = <ref>` | A ref is stated but names no version, so it floats to `latest`. `--allow-unpinned-docker` does not cover this state. | Pass `--image KEY=<repo>:<tag>` or an `@sha256:` digest. If the version is a path segment (for example `ubuntu1804`), declare `{"ref": "...", "versioned_by": "path"}` on that key in `profiles/<module>.json` under `steps[].images`. |
| `nothing states these image refs, so nothing is built.` | `rerun-submit` refuses unpinned images even when `rerun-show` only warned. For steps whose profile marks images as `--image`-only, `--allow-unpinned-docker` is not accepted. | Read the refs from the config that actually ran and pass one `--image KEY=REF` per key. Pass the bare ref; config values are stored quoted, and copying the quotes produces `""ref""`. Then check the `agrees with the body built here` line. |
| `module profile ... is unusable: ... declares versioned_by='path' but '<ref>' names no version` (exit 4) | `versioned_by: path` says where the version lives (the last path segment must end in digits). It cannot make a floating ref count as pinned. | State a release tag or an `@sha256:` digest instead. |
| `gsvtk: unknown command "rerun-show"` | Rerun modes live under the `terra` group. | `./gsvtk terra rerun-show`, and likewise `rerun-create`, `rerun-validate`, `rerun-submit`. `publish` and `doctor` are top-level. |
| `[rerun] step 01 ...` on stderr, then `gsvtk-config: GSVTK_BRANCH is not set` (exit 4) and empty stdout | The step resolved (the `[rerun] step` line names config, root entity and Dockstore entry), and the profile needs `GSVTK_BRANCH` to build the body. | Set `GSVTK_BRANCH` (see [config.md](config.md)). Do not edit the profile. A step that is not in the default module's map is reported as `unknown step`; select the module with `--module single_sample`. |
| A second rerun of one step replaced the first, or its outputs are gone | Without `--tag`, rerun config and output names come from step and workflow only (`10-GenotypeBatch-rerun`, `*_new`), so two reruns of one step share them. | Pass `--tag TAG`: the config becomes `10-GenotypeBatch-TAG` and outputs `*_new_TAG`. `rerun-show` prints both. |
| `note    0 of N expected output(s) under \`_new\`` on a step you know ran | The suffix in force differs from the one that run was written under (a `--tag` rerun, a second chain in the workspace, or a `GSVTK_NEW_SUFFIX` exported for one run only). | The note names the suffix in force and its source. Set `GSVTK_NEW_SUFFIX` to match, or use the `--tag` of the rerun that wrote it. |
| `attribute existence does not claim PASS: N of M reference(s) were withheld` | Some attribute references could not be looked up (row gone, lookup refused, capture silent about the row). They count as `UNKNOWN`, never as `PASS` or `ABSENT`. | Read the per-reference reasons. To answer offline, set `GSVTK_ENTITY_JSON=<capture>`. |
| `FAIL expression-shaped bindings` from `terra/batch_check_inputs.py` (exit 1) | A config binding is an expression rather than a value, so the WDL default won. | Bind a literal path or a plain attribute reference. |
| Batch-level `this.<attr>` bindings resolve to nothing, or a `select_all` comes back empty | `GSVTK_BATCH` does not name a real `sample_set` in that workspace. A wrong entity name typechecks and fails at runtime, after VMs booted. | Set it to the real set name (usually `all_samples`). `terra/batch_configs.py show` prints `rootEntityType` per config. |
| `stage_inputs: no sample_set row named '<x>' in <manifest>` (exit 2) | `GSVTK_BATCH` names a row the frozen manifest does not contain. | Set `GSVTK_BATCH` to one of the listed rows, or re-run `terra/fetch_baseline.py --entity <name>`. |
| `... records no PASSED crc32c verify` / `no frozen-input manifest at ...` | `freeze-attrs` publishes only over a recorded successful verify. Missing, unreadable and failed verifies are treated alike. | Run `python terra/batch_freeze.py verify` (read-only) and resolve any mismatch. `--allow-unverified` exists and prints that it was used. |
| `N local files are named <x> with the object's size, so adoption cannot pick one` | Two `--link-dir` captures contain a file with that name and size. | Narrow `--link-dir` to one capture, or drop it and download. |
| `!! NOT adopting <path>: crc32c:mismatch ... -- downloading the object instead` | A same-name, same-size local file is not the current baseline object. The tool refused a stale input. | Nothing to fix. If you expected a match, your local capture is stale. |
| `cannot flatten: <name> is declared by both A.wdl and B.wdl` | Two files in the import closure declare the same task or struct. `terra/wdl_flat.py` will not choose which survives. | Fix it upstream, or flatten a narrower entry point. Importing one file twice is fine. |
| `400 "The request content was malformed:\nunexpected json type"` (Rawls) | Either `methodVersion` was sent as a string on an Agora ref (it must be an int), or a list/int value in `inputs` was sent unquoted (Rawls wants strings). | Send `methodVersion` unquoted and stringify every value in `inputs`. |
| `Validation errors: Invalid outputs: ... -> Error while parsing the expr...` at submission (Rawls) | Rawls accepts a malformed `outputs` map at creation and only fails at submission. | `python terra/batch_configs.py check --against <ref>` reports undeclared output keys offline. |
| `HTTP 400 Validation errors: Invalid inputs: <key> -> Error while parsing the expression` at submission, after `validate` said the config was valid (Rawls) | Validation typechecks bindings but never resolves `workspace.<attr>` references; submission does. | Treat a clean validate as a type check only. Bind values the target workspace actually holds, or literals. |
| `400 Your method config defines a root entity but you haven't passed one to the submission.` (Rawls) | The entity was nested inside `submissions: [...]`, which is the response shape. | Put `entityType` and `entityName` at the top level of the POST body. |
| `400 Entity type workspace is reserved and cannot be overwritten`, or `500 AttributeEntityReference(workspace,...) not found` (Rawls) | `rootEntityType: workspace` is not usable. | Use the collection's real root entity type (`sample`, `sample_set`, ...). |
| `409 <config> already exists` on a retry (Rawls) | Rawls keeps a config whose method resolution failed, and its `valid` stays `None`. | Create it under a new name. |
| `HTTP Error 405: Method Not Allowed` (Rawls) | A POST-only endpoint (usually `.../methodconfigs/validate`) was reached with GET. | POST it. |
| A method-config listing returns `[]` for a workspace you know has configs | The collection route can return 200 with an empty list. An empty listing is not evidence the workspace is empty. | Read the config by name instead; `create` and `submit` already do. |
| Ad-hoc code reports a workspace has no attributes | `get_workspace()` returns an envelope; attributes are under `["workspace"]["attributes"]`, not at the top level. | Read one level down. The kit's own readers already do. |
| A rerun reproduced the old answer exactly | Images were resolved from workspace attributes, so it ran whatever those pointed at. | Pin images with `--image` and confirm the refs in `rerun-show` output. |

## Cost and metadata

| You see | What it means | Do this |
|---|---|---|
| `chain total is PARTIAL: no saved metadata for <step>/<side>` | One step's saved Cromwell metadata is missing and was counted as zero. | Run `terra/batch_save_metadata.py` for that step before quoting a chain total or ratio. See [terra-head-to-head.md](terra-head-to-head.md). |
| `N sub-workflow call(s) have no expanded tree below them -> cost is a FLOOR` / `these are FLOORS, not measurements` | The saved metadata tree was truncated or not expanded, so VM-minutes are a lower bound. | Re-fetch with `terra/batch_save_metadata.py`. |
| `TREE TRUNCATED, NO DOLLAR FIGURE:` ... `unreadable or never expanded` (exit 4) from `checks/cost_estimate.py` | The metadata lists sub-workflows that could not be read, so any dollar figure would be a lower bound. The dollar line is withheld. | Re-fetch with `terra/batch_save_metadata.py --target <step>-<side> --outdir <dir>` while the run's metadata is still readable (Terra prunes sub-workflow metadata later), then price that file. `tree: COMPLETE` means the tree is whole; `tree: NO MARKER` means the file did not come from that writer and is priced without that guarantee. |
| `terra/batch_cost.py` totals look low compared with your bill | It reports VM-minutes and job counts, not prices. | Multiply by your own rates. Disk, network and spot pricing account for the rest. |
| `NOTHING GRADED: status <s> with N scope(s) and 0 failure messages.` from `checks/failure_cause.py` | The metadata was fetched without `expand_sub_workflows=True`, so failures inside sub-workflows are absent. | Re-fetch expanded. On expanded metadata the tool splits causes into `INFRASTRUCTURE` (preemption, lost Batch jobs) and `BODY IMPLICATED` (a task exited nonzero). |
| A failed submission names one scope (for example `MakeCohortVcf`) with the message `Workflow failed` | That scope is where a sub-workflow did not finish; the cause is in the expanded tree. | Run `checks/failure_cause.py --metadata <expanded dump>`. |

## Docker builds and image probes

| You see | What it means | Do this |
|---|---|---|
| `error: branch '<b>' not found on ...` then `gsvtk: preflight failed; fix that before anything else` | `docker/gatk-sv-build.sh --check` found that the branch is not pushed, before any VM booted. | Push the branch. |
| `SSH never came up on <vm> ...` or `builder failed to start on <vm>` | The build failed before it started. The script stops the VM it created and prints a postmortem; the VM is kept (stopped) unless deleted. | Read the postmortem, then delete the VM with `gcloud compute instances delete <vm> --project <P> --zone <Z>`. |
| `GSV_BUSY pid=<n>` then `a build is already running on <vm>` | A `--persistent` VM is already building. The script refuses rather than rotating the live build's log. | Watch it with the `gcloud compute ssh ... tail -f /var/log/gsv-build.log` command it prints. |
| `VM '<vm>' exists but is not ours (created-by='<owner>'); refusing to scp/sudo/stop someone else's machine` | `--instance` names a VM the build script did not create. | Choose another `--instance`. |
| Build failed and the VM is gone | On failure the VM is kept and shuts itself down, so if it is gone something else deleted it. The boot disk auto-deletes with the VM. | Read the serial log before deleting anything: `gcloud compute instances get-serial-port-output <vm> --project <P> --zone <Z>`. |
| Build succeeded and the VM is still running | `--keep` (ephemeral) or `--keep-running` (persistent) was passed, or the driver was killed before cleanup. | `gcloud compute instances list --project <P> --filter 'name~^gsv-'`, then delete what you do not need. |
| The build dies while copying the GATK jar, or the disk fills | The jar is copied into the `sv-base` layer, which needs space. | Pass `--disk-size 200`. |
| Image pushed, but the pipeline used something else | You pushed to a path the pipeline reads, or reused a tag. | Push to a namespace nothing reads (`GSVTK_IMAGE_NAMESPACE`) and tag `<branch>-<sha>`. |
| `docker run` exits `125` / `not found` with an image ref containing a space | The ref was assembled from two log lines (prefix and name). | Take the full ref from a single log line. |
| `it carries "$()", which no docker reference can contain` (or another forbidden character) | `checks/image-check/` refuses any `--image` with characters a docker reference cannot contain, because the ref is interpolated into a startup script that runs as root. It also refuses a missing `--image`, an image with a space, and a 0-byte `--probe`. | Fix the ref. |
| `JAR_FLAG_PROBE=IMAGE_ABSENT <ref>` or `JAR_FLAG_PROBE=NO_HELP` (exit 5), with `This is NOT a finding about which flags the shipped jar accepts` | The image could not be pulled, or the jar printed no help, so there was nothing to grade. | Check the ref and the VM's registry access, then probe again. |
| `no answer captured` from `jar_flag_probe.sh` | The poll ended without a marker in the serial log. | Read the serial log for `JAR_FLAG_PROBE=` before re-running. |
| `SVSHELL_IMAGE_CHECK=SCAN_CLEAN` | The scan found nothing, but byte identity was not proven because `--expect-driver-md5` and `--expect-fixture-md5` were not both supplied. | Supply both md5s to get `PROVEN`. |
| `RESULT: TIMEOUT` from `svshell_image_check.sh` | No marker arrived before the timeout. | Check the serial log for `SVSHELL_IMAGE_CHECK=` before recording a timeout: `gcloud compute instances get-serial-port-output <vm> --project=<P> --zone=<Z> --port 1 --start 0 \| grep SVSHELL_IMAGE_CHECK`. |
| Building locally on Apple Silicon is impossibly slow or fails | gatk-sv images are `linux/amd64` and need a Docker daemon; emulation makes `sv-pipeline` builds impractical. | Use `docker/gatk-sv-build.sh`, which builds on a throwaway x86_64 VM. |

## Local replay

| You see | What it means | Do this |
|---|---|---|
| `A Java 17 compatible (Java 17 or later) version is required to build GATK, but 11 was found.` (Gradle) | Your default `java` is too old to build GATK. | `JAVA_HOME=<jdk17 home> ./gradlew localJar`. |
| `java.lang.OutOfMemoryError: Java heap space` in `DepthEvidenceGenotyper.train` | The RD trainer needs a lot of heap at full-cohort interval scale. | Give it the memory the Terra task had. Subsampling intervals avoids the OOM but changes RD cutoffs, so do not publish RD numbers from a subsample. |
| `Rscript: not found`, `python2: not found` | Older shell-based stages depend on interpreters that only exist inside the pipeline image. | Reuse that stage's baseline outputs and replay the Java stages locally, or run the step inside the image. |
| `WARN IntelInflaterFactory - IntelInflater is not supported, using Java.util.zip.Inflater` | Informational. BGZF decompression is slower on this platform. | Nothing. Expect longer wall-clock than on Terra. |
| `[E::vcf_format] Invalid BCF, the INFO tag id=16 is too large` | A pysam version difference: production pins an old pysam, and a newer local one cannot write a header-added INFO tag through a `bcftools sort` pipe. | Run that step inside the image before concluding the VCF is broken. `scripts/prod_pins.py` and `compare/artifact_tally.py --pysam-check` show the pins. |
| `du -sh` on staging reports far more than you downloaded | Files were hardlinked from an existing capture (`--link-dir`). | Check link counts (`stat -f '%l' <file>` on macOS) before deleting. |
| A second run overwrote the first, log included | A copied script assigns `OUT` after an `OUT=${OUT:-...}` default. | Keep a single `OUT=${OUT:-...}` line, as `examples/run_train_full.sh` does. |
| `CAPTURED is not set -- it names the run you are replaying.` (exit 4) from `examples/replay_reference_run.sh` | `inputs` mode needs a local copy of the reference run's Cromwell `script` and an image map. | Set `CAPTURED` and `IMAGES`; the script's header shows the `gsutil cp` to fetch the capture. |
| `invalid choice: 'inputs'` (miniwdl) | The miniwdl subcommand is `input_template`, and it lists required inputs only. | `miniwdl input_template <wdl>`. An input it does not list has a default. |

## Configuration

| You see | What it means | Do this |
|---|---|---|
| `gsvtk-config: GSVTK_PROJECT is not set (...)` (exit 4) | A required key is unset. `GSVTK_PROJECT` decides which project is billed, so it has no default. | Set it in your profile. `./kit/gsvtk-config doctor` lists what is missing. |
| `... is not a git repository` | `GSVTK_GATK_SV_CHECKOUT` (or `--repo`) points at a directory that is not a clone. | Point it at a real gatk-sv clone. |
| `kit/config.sh: the configuration layer produced no exports; ...` on stderr | Sourcing `kit/config.sh` never fails hard, but the resolver itself failed and the tool is falling back to inline defaults. | Fix the resolver error printed with it. Otherwise the run may use a project or workspace you did not intend. |
| A stale value is used after you edited the profile | A later file in the chain, or an exported env var, wins. Precedence is env > `~/.config/gatk-sv-testkit/env` > `<repo>/testkit.local.env` > `./testkit.env`; `GSVTK_X=` and bare `X=` are the same key. `GSVTK_CONFIG` replaces the whole chain. | `./kit/gsvtk-config show` prints each value with the file it came from; `doctor` lists the files read. See [config.md](config.md). |
| A comparison came back empty because one side wrote `*_new` and the other read another suffix | `GSVTK_NEW_SUFFIX`/`GSVTK_FROZEN_SUFFIX` differed between invocations. | Keep them constant across the profile and every command. |

## Static checks

| You see | What it means | Do this |
|---|---|---|
| `GSVTK-RENDER status=REFUSED jsons=0 reason=un-stamped` with `RENDER-REFUSED no input JSON was read: <dir> is not empty and carries no gsvtk render stamp ...` | `checks/wdl_inputs_check.py --render-only` deletes its destination before rendering, so it refuses a non-empty directory it did not create. `gsvtk check` shows this as an `INPUTS-JSON ... SKIPPED` row. | Render into a directory of its own with `--dest`, or remove `<dir>` yourself once you know nothing reads it. |
| `reason=other-ref` | The destination was rendered for a different ref. Two refs cannot share one destination. | Give each ref its own `--dest`. |
| `reason=not-a-directory` | `--dest` names a file. | Point `--dest` at a directory. |
| `UNRESOLVED-REQUIRED <Wf>.<key> [unbound] ...` from `checks/terra_entity_check.py` | The launch config never binds a required input of the workflow it names. Nothing else catches this before submission. | Follow the `To clear it:` tail: bind the key, or make the declaration optional or give it a default. Reproduce offline with `checks/terra_entity_check.py --repo "$GSVTK_GATK_SV_CHECKOUT" --ref <ref>`. |
| `UNRESOLVED-REQUIRED <Wf>.<key> [workspace-missing] ...` | The key is bound, but its value is an attribute the workspace template does not declare, so it is also reported in the value layer above. | Fix the `workspace-missing` finding (add the attribute to that flavor's workspace template, or stop binding the name). |
| `capture=NO-MINIWDL` on every config, `required-uncaptured-because=NO-MINIWDL`, exit 0 | The interpreter cannot import miniwdl (`WDL`), so the required-input layer did not run. The entity and value layers still ran, which is why the exit code is 0. `unbound=0` here means "not checked". | Run with an interpreter that has miniwdl: `.venv/bin/python checks/terra_entity_check.py ...` after `pip install -r requirements-dev.txt`. |
| `capture=NO-WDL-DIR` on every `GSVTK-REQUIRED` line | The tree or ref has no `wdl/` subtree, so required inputs were not checked. | Point `--tree`/`--repo` at a whole gatk-sv tree, or name the WDLs with `--wdl-dir`. |
| `cannot-parse`, `values-unreadable` or `UNREADABLE table` from `checks/terra_entity_check.py` | A config, its values, or an entity TSV could not be read (for example, not UTF-8). The rest of the corpus is still checked and the census still prints. | Fix or remove the unreadable file. |
| `blocks: P in file, E extracted, X executed, ...` followed by `=> FAIL: coverage is incomplete` from `checks/svshell_jq_plumbing_scan.py` (exit 1) | Some jq blocks were not extracted or executed, so "no new nulls" cannot be established. | Look at the blocks that were not executed; a block written in an unusual shape may need the extractor extended. |
| `=> NOT PROVEN: N of M stage calls compared.` from `checks/svshell_contract_check.py` (exit 1) | Only part of the stage calls could be compared, so a clean result would not mean anything. | Read the listed calls that were not compared. |
| `wdl_gate.sh --strict` fails naming a `--wf` workflow | The workflow you named does not exist at that ref. A named workflow that is absent is a failure. | Check the name and the ref. |
| `make test` fails in a fresh clone with `FAIL` lines naming `make setup` or a `pip install` line | Nothing is installed. With `requirements.txt` only, the gate passes and the WDL-analysis layers are reported as named skips; with `requirements-dev.txt` they run. | `make setup`, then `pip install -r requirements-dev.txt` if you want the WDL layers checked. |
| `audit_history: ... PARTIAL: N blob(s) exceed the ... read budget and were NOT scanned` | Some history blobs were too large to read, so the audit is incomplete. | Inspect the named blobs. Deleting a file does not remove it from history. |

## Comparators

| You see | What it means | Do this |
|---|---|---|
| Exit 2 and `FATAL: 0 shared IDs` / `0 shared coordinate keys` from `compare/vcf_paired_diff.py` | The two files share no join keys, so nothing was compared. Variant IDs are often rewritten between pipeline versions, and contig names may differ (`chr20` vs `20`). | Try `--join coord`. For contig naming, see [comparators.md](comparators.md). |
| `NOTHING COMPARED` (exit 2) | The tool read both inputs but found no comparable cell or key. This is never agreement. | Check the field name, sample overlap and join rule. |
| Any comparator exits 1 | It compared something and found differences. | Read the report. Exit codes are described in [comparators.md](comparators.md#exit-codes). |

## Reporting a problem with a tool

Open an issue with the full command and its complete output. These tools encode assumptions about
attribute names, entity types, image layout and Cromwell log formats that a different gatk-sv
version can break. A tool that returns an empty result without saying why is a bug worth filing.
Open work is tracked in [roadmap.md](roadmap.md).
