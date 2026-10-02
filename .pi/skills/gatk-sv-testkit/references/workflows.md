# End-to-end sequences

Run from anywhere; `scripts/gsvtk repo` gives the checkout path. Substitute `<...>` values from
`scripts/gsvtk doctor --redact` — never invent a project, workspace or registry path.

`scripts/gsvtk` is a shim over the checkout's own `./gsvtk`, run with `GSVTK_READ_ONLY=1`, so every
mode name below is a repo-CLI mode name: `gsvtk terra show`, `gsvtk compare <name>`, `gsvtk replay
train-chr20`. The command-to-tool map and the read-only contract are the checkout's `docs/cli.md`;
`--help` on either entry point prints them from the version you have. Where a step below says to `cd`
into the checkout and run a script, that is the **mutating** path — the shim cannot reach it, which is
the point.

## 1. Build a branch's image (no local Docker)

```bash
S=<skill dir>/scripts/gsvtk
$S build <branch>                      # --check then --dry-run; creates nothing
```

Read the `--check` output before continuing: it names the project, zone, machine, disk, clone URL
and **push target**. Two things to confirm with the user before a real build:

- the push target is a path **no pipeline reads** (that is what `GSVTK_IMAGE_NAMESPACE` is for);
- the branch exists on the clone URL shown (`--check` verifies this and fails if it does not).

Then, only when they confirm the spend:

```bash
cd "$($S repo)" && docker/gatk-sv-build.sh <branch>            # ~1-3 h, streams the build log
#  (or `gsvtk build <branch> --confirm` in the checkout: it prints project, zone, machine, disk, the
#   resolved push target, and the undo, before it boots anything. The shim refuses that mode.)
```

While it runs: the driver polls the serial console, so keep the one call waiting rather than
polling. If it times out, the VM is still up and billing — read the log, then delete:

```bash
gcloud compute instances get-serial-port-output <vm> --project <P> --zone <Z>
gcloud compute instances delete <vm> --project <P> --zone <Z>
```

Gotchas: a denied push is the **VM's** service account lacking the registry bucket grant
(`--impersonate-service-account` cannot help); the gatk jar is copied into the `sv-base` layer to
avoid a ~12-minute `docker cp` per task, which is why an undersized disk fails late.

## 2. Static checks (seconds — do this before anything expensive)

```bash
$S check <base-ref> --wf SVShell           # wdl_gate + contract check + jq plumbing vs <base-ref>
$S check <base-ref> --semantics            # add the tree-wide WDL semantics checker
```

`--wf`/`--strict` belong to `wdl_gate.sh`, `--repo` to the python checkers, `--compare-to` to the jq
plumbing scan: `check` dispatches each flag to the tool that implements it and refuses an unknown one
rather than forwarding it to something that would misread it.

Reading the three outputs:

| Output | Means | Next |
|---|---|---|
| `INCOMPLETECALL` / `STALE-BINDINGS` rose vs the base ref | a call site stopped binding an input, or still passes one the callee dropped | fix the WDL; the typecheck passed, which is why this exists |
| `=> FAIL: ... NEW null/empty` | a rename left a reader behind → `--flag null` several stages later | fix the reader; `--compare-to` already isolated it to this branch |
| `=> 7 unsupplied-read problem(s)` on an unmodified checkout | pre-existing upstream findings | not your bug; compare counts before/after your change |

Same idea as `make test`: the number is only meaningful against the ref you are changing.

## 3. Terra head-to-head

Sequence and the reason for each step:

```bash
$S terra recon            # who am I, what can I charge, what can I see        (free)
$S terra show             # every binding my configs would create               (free, offline)
$S terra check --against <branch>   # do those keys exist in that ref's WDL?     (free, offline,
                                    # needs GSVTK_GATK_SV_CHECKOUT; no Dockstore, no Terra call)
$S terra plan             # what freezing the baseline would copy               (free)
$S terra verify           # are the frozen bytes still the bytes I compared     (free)
```

Then the mutating half — **show the user `show`/`plan` output and get confirmation first**. Through
the shim these are refused by name (`configs-create`, `freeze-copy`, `freeze-attrs`, `rerun-submit`,
`fetch`), so run them in the checkout, where `./gsvtk <mode> --confirm` also prints where the write
lands and what it costs:

```bash
cd "$($S repo)"
python terra/batch_freeze.py copy                       # server-side copy into YOUR bucket
python terra/batch_freeze.py verify                     # re-crc32cs every frozen object
python terra/batch_freeze.py attrs --write              # publish coordinates as attributes
python terra/batch_configs.py create --confirm          # POST the method configs
python terra/batch_check_inputs.py --step 10            # gate before launching
python terra/batch_rerun_step.py --image sv_pipeline_docker=<ref> show     # the exact body first
python terra/batch_rerun_step.py --image sv_pipeline_docker=<ref> submit --confirm
```

`attrs --write` **publishes only over a recorded `"verified": true`**: no manifest, an unreadable
one and a failed verify are all refused, because "nobody checked these bytes" is not the same as
"these bytes are the baseline". Run `verify` (free, read-only) and resolve any mismatch. If you are
tempted by `--allow-unverified`, say so out loud — that flag is the deliberate version of publishing
coordinates nobody confirmed, and it prints that it was taken.

`create` and `validate` pre-check the maps against the target ref's WDL in your own checkout and
refuse on an `EXTRA`/`MISSING`/`CANNOT CHECK`; without a checkout or a ref they print `pre-check
SKIPPED`, which is not a pass. The maps are a snapshot of ONE branch's signature while `GSVTK_BRANCH`
only picks the Dockstore URL, so a branch-only key against another ref is rejected as an extra input
**at submission**, after the config was created -- and if that ref was never published you see a 404
first and spend the time chasing the wrong thing. `--allow-unknown-inputs` exists; it prints that it
took the override, and so should you.

With `--drop-branch-only-inputs` the guard grades the *pruned* map rather than the raw table -- the
same `BRANCH_ONLY_INPUTS` rule `body()` applies -- and prints each removed key as `DROPPED for <ref>`.
A key outside that table is still `EXTRA`, so the flag prunes, it does not blind: `EXTRA` without the
flag means the guard really does refuse a branch-only key, and `EXTRA` with it means a key nobody has
ever seen. Which ref the guard may read is not negotiable either: `--against` is `check`'s flag alone,
and `create`/`validate` refuse it, because they POST configs whose Dockstore version *is* the branch
under test -- grading a ref you picked would pass a document nothing runs, and with the drop flag set
it prunes by one ref while posting the other. Want another ref's shape? Point `GSVTK_BRANCH` at it.
Want its findings without posting? `check --against <ref> --drop-branch-only-inputs`.

The rerun step runs the same pre-check in `create`, `validate` and `submit`, and grades
`GSV_WDL_VERSION` (the Dockstore pin its own config carries) rather than `GSVTK_BRANCH` when those
differ -- it imports `body()` from `batch_configs`, which is the builder and not the guard, and that
was once enough to leave the submitting path unguarded. If you genuinely want a config shaped for a
ref that lacks this branch's extra inputs, `--drop-branch-only-inputs` prunes the *known* branch-only
keys against a ref it can read -- the same ref its own config runs, never a third one named by
`--against` -- and prints the semantic consequence (`GenotypeBatch` trains PE/SR from
`vcf` on main, from a separate training VCF on the branch). It is not a fix for pointing at the wrong
ref: if you meant your branch, unset `GSVTK_BRANCH`.

When the question is "did my code change the output", leave exactly one variable: same WDL ref on
both arms, same frozen inputs, one `sv_pipeline_docker` differing. Do not also change the ref, and do
not let both arms write the same `*<GSVTK_NEW_SUFFIX>` attributes -- the second overwrites the first
and you end up comparing a run against itself. `docs/terra-head-to-head.md` §5 has the details.

`batch_configs.py validate` also POSTs -- Terra resolves the Dockstore WDL and reports per-input
bindings -- so it is deliberately outside the wrapper's whitelist. It is the cheapest real gate
before money: run it yourself immediately before `submit`. `--allow-shared-target` is what four of
these tools demand before they will touch the **shared** baseline workspace at all; its absence is
the safety net, so never add it to get past an error without saying out loud that you did. It covers
`rerun create` as well as `rerun submit`: a method config written into the shared workspace is read
by the next person's submission, so "only a config, no compute" is not a safe write. Every Terra
mode also resolves namespace + workspace **before** its first request and exits 4 naming the profile
key if a piece is missing -- an unset target is never a request with a hole in its URL.

Wait with one call (`$S terra status --wait 10`) or hand off to the **terra-monitor** skill.

Collect and compare (all read-only, so all reachable through the shim):

```bash
$S terra status --costs
$S terra cost
$S terra save-metadata --outdir "$(./kit/gsvtk-config work metadata)"
$S terra peek --metadata <dump> --task <Call>                    # one task's rc / stderr tail
```

**Do not `read` those metadata files.** Ask the tool for the summary, or `jq` the specific field.

Then fetch and diff. `fetch` is a bulk download of tens of GB — the user's call, and refused under
read-only; `table` alone only reads what is already on disk:

```bash
cd "$($S repo)" && ./gsvtk terra fetch-compare fetch --confirm && ./gsvtk terra fetch-compare table
#  or the same two modes directly: terra/batch_fetch_compare.sh fetch | table
# then any differ in compare/ by name:
$S compare batch-tables --baseline-dir <old/> --new-dir <new/>     # `compare --list` for the names
```

### Before quoting any number, classify every differing column

1. **a bug in my change** — the interesting case;
2. **an intended behaviour change** — then the baseline value is what must change, and a
   strategy-aware differ marks it `STRATEGY` rather than diffing it as a bare number;
3. **an input difference you did not control** — the comparison is invalid, not the code.

Case 3 is why freezing exists. Case 2 is why `compare/` exists. And the rule that survives most
often: **a column whose inputs are a superset on one side must never be diffed as a mean.**

## 4. Local replay (free, needs a jar)

The long form -- what local replay cannot prove, and the subsampling trap in it -- is
`docs/local-replay.md` in the checkout.

```bash
cd <gatk checkout> && JAVA_HOME=<jdk17> ./gradlew localJar
cd "$($S repo)"
export GSVTK_GATK_CHECKOUT=<gatk checkout>       # the jar is discovered from here
$S replay preflight                              # java / jar / bcftools / disk, measured, exit 3
$S replay train-chr20                            # the same driver, after that preflight
examples/run_train_chr20.sh --help               # every example honours --help and prints its own notes
```

`replay preflight` exists because the failure used to be a bare `command not found` forty minutes into
a run, or a `no jars found` line after a staging tree had already been built. It exits 3 and names the
fix. It never refuses on disk space: this repo has not measured what one driver needs, so the free
figure is printed with the budget `docs/local-replay.md` states, not enforced behind an invented floor.

Check the log's first lines for **which jar** it used — a stale jar reproduces the old answer with
total confidence and no error. `run_train_chr20.sh` is the fast loop; `run_train_definitive.sh` is
the run whose RD numbers are quotable, because RD cutoffs move with the interval set and SR/PE
metrics do not.

Local replay proves the trainer's arithmetic. It does not prove the WDL, the image, or the task
memory settings — that is loop 3.

## If you must run a mutating mode

Say, in this order, before running it:

1. **where the write goes** — `doctor --redact` output plus the push target from `show`;
2. **what it costs** — VM type × expected hours, or "free, no compute";
3. **how to undo it** — method configs can be overwritten; a submitted job can only be aborted;
   a pushed image tag must never be reused, so tag `<branch>-<sha>`;
4. then run the real command in the checkout and report the exit code and any created id.

Never present a mutating run as finished when it only reached `--dry-run`, and never report
`--check` success as proof the build will work — it verifies read paths, not push rights.

## 5. Facts you should not be typing by hand

Three questions every branch asks, where the answer lives upstream or inside one artifact — not in a
file in this repo:

```bash
# "what breaks if I change this file?" (script edges included; a full-tree parse is ~34 s)
./checks/wdl_reach.py --dir "$GSVTK_GATK_SV_CHECKOUT" --reverse --target Structs.wdl

# "what does production actually pin?" read from gatk-sv's dockerfiles at a ref, git show only
./scripts/prod_pins.py --repo "$GSVTK_GATK_SV_CHECKOUT" --ref origin/main

# "what does THIS artifact contain?" counted independently, no second arm
./compare/artifact_tally.py run.vcf --info MOI --header-assert '##INFO=<ID=MOI,'
./compare/artifact_tally.py run.vcf --info SVTYPE --invariant 'info=SVTYPE=DEL:0'   # VIOLATED exits 1
```

The `--invariant` form exists because a comparator once asserted `CTX == 0` on an arm whose baseline
holds 7 such records, and an `elif` chain skipped the checks after it — a false REVIEW over good data.
Every invariant is measured and printed with both numbers whether or not the previous one failed, so a
violation is a finding rather than a gap in the report.

On the Terra side, two things that were hand-assembled in `curl` in every review: `terra/batch_peek.py`
for a bounded call-level peek (what is running, what retried, what broke first) **and for one task's
artifacts** — `--metadata <local dump> --task <Call>` prints the rc from its file, the tail of its
`stderr`, the rendered `script` block and which `attempt-N` dirs exist, which is how you tell a
preemption (rc=141 under `attempt-1`) from an unstable image without opening a log. Its live listing
path needs credentials and is named as unexercised in the offline gate. And `terra/fetch_outputs.py`,
which takes the **workflow output name** out of Cromwell metadata instead of a bucket path you guessed:

```bash
./terra/fetch_outputs.py --metadata "$GSVTK_WORK"/metadata/run1.*.json \
    --output GATKSVPipelineSingleSample.svVCF --dry-run
```

`--dry-run` prints the exact `gsutil -m cp -n` line without running it, and "the workflow never declared
that name" (exit 3) is a different answer from "declared, but this run produced no file for it"
(exit 4) — the tool says so, because conflating them is how a finished-but-empty output gets re-run.

## Where the real documentation lives

In the checkout, not in this skill:

| Question | File |
|---|---|
| any error text | `docs/troubleshooting.md` — grep for the verbatim string |
| which config key does what | `docs/config.md` |
| build mechanics, IAM, postmortem | `docs/docker-builds.md` |
| the head-to-head in depth | `docs/terra-head-to-head.md` |
| reading comparator output | `docs/comparators.md` |
| what the checks really mean | `docs/static-checks.md` |
| which command runs which tool, and what is refused | `docs/cli.md` |
| replaying a real stage with no cloud account | `docs/local-replay.md` |
| why the archive keeps its mistakes | `docs/methodology.md` |

If one of those contradicts the code, the code and `make test` are authoritative — report the
discrepancy rather than reconciling it silently.
