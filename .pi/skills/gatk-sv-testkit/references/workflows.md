# End-to-end sequences

Run from anywhere; `scripts/gsvtk repo` gives the checkout path. Substitute `<...>` values from
`scripts/gsvtk doctor --redact` — never invent a project, workspace or registry path.

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

## 2. Static gate (seconds — do this before anything expensive)

```bash
$S gate <base-ref> --wf SVShell            # wdl_gate + contract check + jq plumbing vs <base-ref>
```

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

Then the mutating half — **show the user `show`/`plan` output and get confirmation first**:

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

The rerun step runs the same pre-check in `create`, `validate` and `submit`, and grades
`GSV_WDL_VERSION` (the Dockstore pin its own config carries) rather than `GSVTK_BRANCH` when those
differ -- it imports `body()` from `batch_configs`, which is the builder and not the guard, and that
was once enough to leave the submitting path unguarded. If you genuinely want a config shaped for a
ref that lacks this branch's extra inputs, `--drop-branch-only-inputs` prunes the *known* branch-only
keys against a ref it can read and prints the semantic consequence (`GenotypeBatch` trains PE/SR from
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

Wait with one call (`terra/batch_status.py --wait 10`) or hand off to the **terra-monitor** skill.

Collect and compare:

```bash
$S terra status --costs
$S terra cost
cd "$($S repo)" && python terra/batch_save_metadata.py --outdir "$(./kit/gsvtk-config work metadata)"
```

**Do not `read` those metadata files.** Ask the tool for the summary, or `jq` the specific field.

Then fetch and diff (bulk download — tens of GB, user's call):

```bash
cd "$($S repo)" && terra/batch_fetch_compare.sh fetch && terra/batch_fetch_compare.sh table
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
examples/run_train_chr20.sh --help               # every example honours --help and prints its own notes
examples/run_train_chr20.sh
```

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
| replaying a real stage with no cloud account | `docs/local-replay.md` |
| why the archive keeps its mistakes | `docs/methodology.md` |

If one of those contradicts the code, the code and `make test` are authoritative — report the
discrepancy rather than reconciling it silently.
