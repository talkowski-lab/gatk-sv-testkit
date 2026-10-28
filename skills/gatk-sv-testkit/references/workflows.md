# End-to-end sequences

Every read-only command below runs as `gsvtk --read-only`. Fill `<...>` values from
`gsvtk --read-only doctor --redact`; never invent a project, workspace or registry path.

Steps that `cd` into the checkout and pass `--confirm` are the mutating path: the user runs those, or
approves them explicitly.

## 1. Build a branch's image

```bash
gsvtk --read-only build <branch>                      # --check then --dry-run; creates nothing
```

The output names the project, zone, machine type, disk, clone URL and push target. Before a real build,
confirm with the user that:

- the push target is a path no pipeline reads (that is what `GSVTK_IMAGE_NAMESPACE` is for);
- the branch exists at the clone URL (`--check` fails if it does not).

Then, only once they accept the cost:

```bash
cd "$(gsvtk repo)" && ./gsvtk build <branch> --confirm     # ~1-3 h of e2-standard-8, streams the log
```

The script watches the VM's serial console; keep that one call waiting instead of polling. A successful
build also writes an image-digest capture under `$GSVTK_WORK/image-capture/` for
`terra/batch_rerun_step.py --image-from`. If the watch times out, the VM is still running and billing:
read the log, then delete it.

```bash
gcloud compute instances get-serial-port-output <vm> --project <P> --zone <Z>
gcloud compute instances delete <vm> --project <P> --zone <Z>
```

A denied push means the VM's service account lacks the registry bucket grant;
`--impersonate-service-account` cannot fix it.

## 2. Static checks

```bash
gsvtk --read-only check <base-ref> --wf <Workflow>    # WDL gate + sv_shell contract check + jq plumbing scan
gsvtk --read-only check <base-ref> --semantics        # also the tree-wide WDL semantics checker
```

| Output | Meaning | Next |
|---|---|---|
| `INCOMPLETECALL` / `STALE-BINDINGS` higher than at the base ref | a call stopped binding an input, or passes one the callee dropped | fix the WDL |
| `=> FAIL: ... NEW null/empty` | a renamed key left a reader behind; it becomes `--flag null` later | fix the reader |
| unsupplied-read problems on unmodified gatk-sv | existing upstream findings | compare counts before and after your change |

## 3. Terra head-to-head

Read-only steps:

```bash
gsvtk --read-only terra recon                       # identity, billing, visible workspaces
gsvtk --read-only terra show                        # every binding the configs would create (offline)
gsvtk --read-only terra check --against <ref>       # do those inputs exist in that ref's WDL (needs GSVTK_GATK_SV_CHECKOUT)
gsvtk --read-only terra plan                        # what freezing the baseline would copy
gsvtk --read-only terra verify                      # are the frozen copies still byte-identical (crc32c)
```

Then the mutating steps. Show the user the `show` and `plan` output and get confirmation first.
`--read-only` refuses these, so they run in the checkout:

```bash
cd "$(gsvtk repo)"
python terra/batch_freeze.py copy --write                # server-side copy into the sandbox bucket
python terra/batch_freeze.py verify                      # re-check crc32c of every frozen object
python terra/batch_freeze.py attrs --write               # write the frozen paths as entity attributes
python terra/batch_configs.py create --confirm           # POST the method configs
python terra/batch_check_inputs.py --step 10             # check the step's config before launching
python terra/batch_rerun_step.py --step 10 --image sv_pipeline_docker=<ref> show
python terra/batch_rerun_step.py --step 10 --image sv_pipeline_docker=<ref> submit --confirm
```

Guards to know:

- `attrs --write` requires a recorded `"verified": true` from `verify`. `--allow-unverified` overrides
  it and prints that it did; tell the user before suggesting it.
- `create`, `validate` and the rerun's `submit` check the bindings against the target ref's WDL and
  refuse on `EXTRA`, `MISSING` or `CANNOT CHECK`. Without a gatk-sv checkout they print
  `pre-check SKIPPED`, which is not a pass. The rerun grades the Dockstore version it will launch
  (`GSV_WDL_VERSION`), not `GSVTK_BRANCH`.
- `--allow-unknown-inputs` overrides the binding check and prints that it did.
- `--drop-branch-only-inputs` removes the known branch-only inputs for a ref that lacks them and
  prints each as `DROPPED for <ref>`. Any other unknown key is still `EXTRA`.
- `--against` is accepted only by `check`. `create` and `validate` always grade the branch they will
  POST; to target another ref, set `GSVTK_BRANCH`.
- `--allow-shared-target` is required before any of these tools writes to the shared baseline
  workspace, including a config from `rerun-create`. Never add it silently.
- `batch_configs.py validate` asks Terra to resolve the WDL. It is the last cheap check before
  `submit`, and the user runs it.
- Every Terra mode resolves the namespace and workspace before its first request and exits 4 naming the
  missing key.

To answer "did my code change the output", change one thing: the same WDL ref and frozen inputs on both
sides, one image different. Give each run its own output suffix (`--tag`) so a second run does not
overwrite the first. `docs/terra-head-to-head.md` has the details.

Wait with one call (`gsvtk --read-only terra status --wait 10`), then collect, all read-only:

```bash
gsvtk --read-only terra status --costs
gsvtk --read-only terra cost
gsvtk --read-only terra save-metadata --outdir "$(./kit/gsvtk-config work metadata)"
gsvtk --read-only terra peek --metadata <dump> --task <Call>     # one task's rc, stderr tail and script
```

Don't read the metadata files whole; use the tool's summary or `jq` one field.

Fetching outputs is a bulk download (tens of GB), so the user runs it. Comparing fetched files is local:

```bash
cd "$(gsvtk repo)" && ./gsvtk terra fetch-compare fetch --confirm   # the download
gsvtk --read-only terra fetch-compare table                                     # diff what is on disk
gsvtk --read-only compare batch-tables --baseline-dir <old/> --new-dir <new/>   # or any comparator by name
```

### Before quoting a number, classify each differing column

1. a bug in the change;
2. an intended behavior change, where the baseline value is what should change (a strategy-aware
   comparator reports it as `STRATEGY`);
3. an input difference you did not control, which makes the comparison invalid.

Never compare a mean over a column whose inputs are a superset on one side.

## 4. Local replay

Runs a GATK-based stage locally on a captured run's exact inputs. Free, but needs a JDK 17+ and a GATK
jar you built. Details: `docs/local-replay.md` in the checkout.

```bash
cd <gatk checkout> && JAVA_HOME=<jdk17> ./gradlew localJar
export GSVTK_GATK_CHECKOUT=<gatk checkout>       # the jar is found from here
gsvtk --read-only replay preflight                              # java, jar, bcftools, disk; exits 3 naming what is missing
gsvtk --read-only replay train-chr20                            # a fast driver on one chromosome
```

`replay preflight` reports free disk but does not refuse on it. Check the log's first lines for which jar
ran; a stale jar reproduces the old answer without any error. `train-chr20` is the fast loop; quote RD
numbers only from `train-definitive` or `train-full`, because RD cutoffs change with the interval set.

Local replay checks a stage's computation. It does not check the WDL, the image or task resources; use
loop 3 for those.

## If you must run a mutating mode

Before running it, tell the user:

1. where the write goes (`doctor --redact`, plus the target from `show`);
2. what it costs (VM type and expected hours, or "no compute");
3. how to undo it (method configs can be overwritten; a submission can only be aborted with
   `./gsvtk terra abort --submission <id> --confirm`; never reuse a pushed image tag);
4. then run it in the checkout and report the exit code and any id it created.

Don't report a mutating run as done when it only reached `--dry-run`, and don't treat `--check` success
as proof a build will push.

## 5. Questions answered from upstream or from one file

```bash
# what does a change to this file affect?
./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --reverse --target Structs.wdl
# several targets in one run (the tree is parsed once)
./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --target MakeCohortVcf --target GenotypeBatch

# what package versions does production pin? (read from gatk-sv's dockerfiles with git show)
./scripts/prod_pins.py --repo "$GSVTK_GATK_SV_CHECKOUT" --ref origin/main

# what does this one file contain?
./compare/artifact_tally.py run.vcf --info MOI --header-assert '##INFO=<ID=MOI,'
./compare/artifact_tally.py run.vcf --info SVTYPE --invariant 'info=SVTYPE=DEL:0'   # a violation exits 1

# download a workflow output by its declared name, not a guessed bucket path
./terra/fetch_outputs.py --metadata "$GSVTK_WORK"/metadata/run1.*.json \
    --output GATKSVPipelineSingleSample.svVCF --dry-run
```

`fetch_outputs.py --dry-run` prints the `gsutil` command without running it. It exits 3 when the
workflow declares no output by that name and 4 when the output is declared but the run produced no file.

`terra/batch_peek.py --metadata <dump> --task <Call>` shows one task's return code, `stderr` tail,
rendered script and attempt directories, which tells a preemption apart from a failing task.

## Where the documentation lives

In the checkout:

| Question | File |
|---|---|
| an error message | `docs/troubleshooting.md` (grep the exact text) |
| what a config key does | `docs/config.md` |
| build mechanics and IAM | `docs/docker-builds.md` |
| the head-to-head in depth | `docs/terra-head-to-head.md` |
| reading comparator output | `docs/comparators.md` |
| what the static checks mean | `docs/static-checks.md` |
| which command runs which tool, and what is refused | `docs/cli.md` |
| running a stage locally | `docs/local-replay.md` |

If a doc contradicts the code, the code and `make test` win. Report the discrepancy.
