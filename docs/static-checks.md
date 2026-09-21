# Static checks: catching it before a VM boots

Four checkers, all local, all seconds, none needing data, docker, or a Terra account. They
exist because gatk-sv has classes of breakage that no existing CI sees:

| Breakage | Why normal CI misses it | Checker |
|---|---|---|
| A call site that never binds a required input | `miniwdl check` treats `IncompleteCall` as a warning and exits 0 | `wdl_gate.sh` |
| A call site passing an input the callee never declared | typechecking does not compare call sites across files | `wdl_gate.sh` |
| A renamed `sv_shell` JSON key with a reader left behind | `jq -r '.missing'` yields the string `null`, forwarded as `--flag null`, failing stages later | `svshell_contract_check.py`, `svshell_jq_plumbing_scan.py` |
| A shipped image whose jar predates the flags its WDL passes | the image is built from a pinned commit, not your branch | `image-check/` |

## `wdl_gate.sh` — is it launchable, not just valid

```bash
checks/wdl_gate.sh                       # origin/main vs $GSVTK_BRANCH, default workflows
checks/wdl_gate.sh v1.1.1 HEAD           # any two refs
checks/wdl_gate.sh --wf SVShell HEAD     # one workflow
checks/wdl_gate.sh --strict HEAD         # nonzero exit if anything is unlaunchable
```

Needs `miniwdl` (`pip install miniwdl`) and a local gatk-sv clone (`GSVTK_GATK_SV_CHECKOUT`).
Both are checked up front and the fix is printed rather than a stack trace appearing.

Each ref is materialized to **its own directory** by `scripts/fetch_wdl.py` (git-archive of one
ref, with a `.provenance` file recording the exact SHA). That is not tidiness: WDL imports
resolve by filename within the directory, so a mixed tree resolves against the wrong version and
reports a result that is neither the old bug nor the new one.

The output is a count per ref, meant to be **diffed**:

```
WORKFLOW                       REF              EXIT   INCOMPLETECALL   STALE-BINDINGS
SVShell                        main@9a34dc12    0      2                0
SVShell                        main@77c1e0b2    0      3                1
```

A rise in `INCOMPLETECALL` means a call site stopped binding something; a rise in
`STALE-BINDINGS` means one is still passing an input the callee dropped. That delta is the whole
value: against gatk-sv as it stands there are a few `IncompleteCall` warnings that are deliberate,
so a raw nonzero is not a failure — *the change in the column* is. `--strict` makes nonzero the
exit status when you want a hard gate.

## `svshell_contract_check.py` — the rename that becomes `null`

```bash
checks/svshell_contract_check.py                  # $GSVTK_GATK_SV_CHECKOUT, or --repo DIR
checks/svshell_contract_check.py --repo <clone> -v
```

`single_sample_pipeline.sh` chains ~14 module scripts, each fed an `inputs.json` assembled by a
`jq -n` block from the top-level inputs plus the previous module's `outputs.json`. Because `jq`
returns the string `null` for a missing key instead of failing, a renamed key turns into
`--some-flag null` several stages later — long after the cause, inside a VM, after the expensive
part. gatk-sv has no CI coverage of `src/sv_shell` at all.

The checker cross-references statically:

- driver `$inputs[0].KEY` reads **vs** what the top-level supply actually contains — the shipped
  `sample_inputs/single_sample_pipeline*.json` fixtures plus the `--arg`/`--argjson` names in
  `wdl/SVShell.wdl` (its jq is `$ARGS.named`, so argument names *are* the keys);
- module `outputs.json` writes **vs** later reads of those keys.

**Read the findings as a set, not a verdict.** Run against upstream it reports several unsupplied
reads, mostly gcnv hyperparameters that arrive from elsewhere in real use. Their value is the
diff: run before and after your change, and any *new* unsupplied read is a rename you missed.

**Coverage is printed, and partial coverage is a failure, not a clean run.** The header reports
`N of M stage calls compared`; if `N < M` the verdict is `NOT PROVEN` and the exit status is 1,
because "no findings" from a checker that did not look is the most expensive possible output. (This
is not hypothetical: the first version of this checker compared a handful of the ~14 stage calls — it
missed bare object keys, dashed keys, single-quoted module reads and `// default` reads — and still
reported a clean run on the subset it saw. It now compares 14 of 14 against real gatk-sv.) Key
recognition was also widened, so a key written *any* of `KEY:`, `"KEY":`, `'KEY':`, `"KEY":` with
dashes, or read with a `// default`, is no longer invisible.

`--strict` turns the pre-existing upstream findings into a nonzero exit as well; `--selftest` runs
the parser against hostile inline fixtures so a regression in *detection* fails the gate rather than
quietly shrinking coverage (`make selftest` runs it, and pins a minimum number of compared stage
calls).

## `svshell_jq_plumbing_scan.py` — execute the plumbing instead of reading it

```bash
checks/svshell_jq_plumbing_scan.py --repo <worktree>                  # absolute scan (red upstream by design)
checks/svshell_jq_plumbing_scan.py --repo <worktree> --compare-to main # the gate form: only NEW nulls fail
checks/svshell_jq_plumbing_scan.py --tree <extracted /opt/sv_shell>      # scan shipped bytes
checks/svshell_jq_plumbing_scan.py --list-keys                           # the key inventory
```

Reading the driver cannot prove the jq blocks are right; running them can. The scan extracts
every `jq -n \` … `> "${target}"` block, substitutes shell variables (top-level input file →
fixture, any `*outputs_json*` variable → a synthesized stub carrying every key the driver reads
from any slurped variable), and **executes** each block with real `jq`, then exits nonzero if a
block fails to execute or puts a null/empty into an argument. Note this means a plain scan is red
against unmodified gatk-sv, which has pre-existing nulls: use `--compare-to <ref>` to gate a
change, which fails only on nulls the branch *introduced*.

Needs `jq` on PATH. `--compare-to` exists so a PR gate reports only findings that are new to the
branch, which is the difference between a useful gate and one that gets ignored.

The scan reports its own **coverage** on one line — `blocks: P in file, E extracted, X executed,
Y errored, Z with null/empty` — and refuses to summarise a partial scan: if `X < P` you get a
nonzero exit and a message saying the verdict means nothing. Extraction used to skip blocks that were
written a little differently from the canonical `jq -n \` shape (quoting, line breaks, a redirect on
the same line) while the summary still said *every jq block executed*; blocks that cannot be resolved
are now counted and named instead of dropped. Each producer block gets a stub carrying the keys its
consumers read, so a stale reader against another block's output is detectable rather than silently
null, and `--selftest` pins both behaviours for `make selftest`.

## `image-check/` — proof about shipped bytes, not checkout bytes

A checkout can be checked statically; an image has to be asked. Both scripts boot one throwaway
GCE VM (needs `GSVTK_PROJECT`), pull the image with the instance's own credentials, and stream
results to the serial console:

```bash
# name the image whose bytes you want proven (--image is required: an unnamed image cannot be evidence)
checks/image-check/svshell_image_check.sh --image "$(./kit/gsvtk-config get IMAGE_REPO)/sv-shell:<branch>-<sha6>"
checks/image-check/jar_flag_probe.sh --image <ref>     # which GenotypeSVs flags the shipped jar accepts
```

`--expect-driver-md5` / `--expect-fixture-md5` are what make it a proof rather than a demo, and the
verdict says which of the two you got: **`PROVEN`** only when both expected md5s were supplied and
matched the shipped bytes, **`SCAN_CLEAN`** when the scan ran clean but no expected md5 was given for
something — per file, so a clean scan of a file nobody pinned can never be quoted as byte identity.
The earlier wording printed one blanket "byte-identity check passed" for the whole run, which people
(naturally) quoted as proof about files it had never compared to anything.

`svshell_image_check.sh` extracts `/opt/sv_shell` with `docker create` + `docker cp` (no bind
mounts, no daemon config) and md5s the driver and shipped fixture. **The `--expect-*` values are
the proof**: compute them from the checkout you actually tested and pass them in —

```bash
git -C <gatk-sv> show <ref>:src/sv_shell/single_sample_pipeline.sh | md5sum
```

— because otherwise the byte comparison prints `byte-identity NOT PROVEN` and is skipped: the
image then only proves it contains *a* driver, not the one you audited. It then runs the plumbing
scan `--tree` on the extracted tree, on the VM host.

`jar_flag_probe.sh` answers "does the jar inside the image accept the flags my branch passes".
Only answerable by *executing* the jar: the image's `/opt/gatk.jar` is built from a pinned GATK
commit, which can predate your argument split, in which case the image cannot run your driver
however correct the driver is.

> **Lifecycle note, learned the hard way:** this VM stays running after printing its markers, and
> the driver deletes it only after reading the serial log. GCE makes serial output unreadable once
> an instance is `TERMINATED`, and the self-deleting variants lost their evidence that way.
> If a check dies mid-run, `gcloud compute instances delete <name>` — the scripts only clean up
> instances they created.

## Using them as gates

Pre-submit, in this order (cheapest first, and each one can invalidate the ones after it):

```bash
scripts/fetch_wdl.py --ref HEAD --dest "$GSVTK_WORK/wdl/HEAD"
checks/wdl_gate.sh --strict HEAD                                  # seconds
checks/svshell_contract_check.py                                  # seconds
checks/svshell_jq_plumbing_scan.py --compare-to main              # needs jq
python terra/batch_check_inputs.py --step 10                      # needs womtool + config
```

`--strict` and `--compare-to` are the two flags that turn "here is a list" into "this branch
regressed", and they are the ones worth wiring into automation. Anything that compares against a
ref is only as trustworthy as that ref: `origin/main` today, not `main` from last week.
