# 001 — Adversarial hardening of the toolkit: 20 confirmed defects fixed, gate made real

**Session date:** 2026-09-21 (`date -u` on the working box: 19:09:03Z; GitHub Actions stamped run
`35642810452` at 19:07:49Z the same minute — the two agree, recorded rather than assumed).

No prior handoff doc: this is the first in this series. The prior state of record was the published
commit `0d0ddd1`, whose own narrative lives in git log and `docs/`. This session was the
**adversarial half**: three fresh-context attack lanes attacked that commit, and every finding they
produced was reproduced (or source-proven) and then either fixed or explicitly rejected.

---

## 1. What the review panel was, and what it cost

Three `delegate` lanes in one async workflow (`76519b19-57f4-4f43-ac86-ba0b47c19288`, state at
handoff: **complete**, 3/3 children), fresh context each, findings-as-data with a prohibition list
(no bare builds, no Terra POST/submit/copy/attrs-write/push, no `gsutil` writes, hostile fixtures in
scratch dirs only):

| lane | hunting | findings |
|---|---|---|
| `moneyblast` | consent, money, leakage, write paths | 12 (F1–F12) |
| `falsifier` | docs that lie, gates that pass while blind | 12 |
| `wronganswer` | silent wrongness, confident false passes | 12 |

Findings files (parent-session artifacts, not repo files):
``<session-artifacts>`/subagent-artifacts/outputs/76519b19-…/findings-{moneyblast,falsifier,wronganswer}.md`.
**Verified:** workflow state re-read with `subagent(action:"status")` at handoff, not carried forward.

**20 defects were confirmed and fixed; the rest were rejected** as already fixed, documented design
choices, or false positives. Nothing was fixed that had not been reproduced first — that rule paid
for itself twice (see §5, items 3 and 4).

## 2. The finding that mattered most: the offline gate was structurally fake

`make selftest`'s `check()` ran `out="$$($("$@") 2>&1)"`. Make expands the inner `$("$@")` as a
variable reference to **nothing**, so bash received `out="$( 2>&1)"`, no command ever executed, and
`$?` was the substitution's own success. Every assertion reported `ok` forever — including
*env > profile precedence*, the rule that decides which GCP project a mutating tool bills.

Proved, not argued, three ways:

* `make -n selftest` prints `out="$( 2>&1)"`.
* A two-line throwaway Makefile: `check "false, which MUST be caught" false` → `ok    false…`,
  `result: ok=1 fail=0`.
* `make -n` output captured during this session is quoted in `CONTRIBUTING.md` and
  `docs/troubleshooting.md` so the next reader can re-run it.

Fix: assertions moved to `scripts/selftest.sh` (bash, no make escaping), plus a **canary** that
asserts a known-failing command is *reported* as failing — if a harness goes vacuous again the gate
fails on the canary instead of passing everything — plus `make smoke` (real end-to-end invocations)
and `undefmods` (`scripts/undef_module_refs.py`, attribute access on an unimported module, which
`py_compile` and `--help` sweeps cannot see). Selftest assertions now require **positive controls**
(blocks executed must equal blocks present; ≥ 12 stage calls compared), because "exit code was
acceptable" is satisfied by a checker that detects nothing.

`make audit` was failing open the same way: one per-line `/Users/you` exclusion disabled **every**
identifier pattern, and `grep -I` skipped binary tracked files. Both fixed; planted text and binary
leaks now fail the gate (verified by planting both).

## 3. Money / consent / injection fixes (all reproduced first)

* **`terra/stage_inputs.py` — shell injection, RCE grade.** `shell=True` plus `json.dumps(uri)` did
  not prevent `$(…)`/backtick expansion, and `--region` was interpolated raw; those values come from
  Terra workspace attributes and CLI arguments, executing in a shell holding your credentials. Now
  argv lists, control-character rejection, strict `--region`. **Verified:** stub `gsutil`, hostile
  manifest + hostile region, nothing executes.
* **Shared-baseline write guard** — `terra.assert_writable_target()` on `copy --write`,
  `attrs --write`, `configs create --confirm`, `rerun submit`, needing explicit
  `--allow-shared-target`.
* **`configs create` resolves target identity before the first request.** The lane's `POST
  https://api.firecloud.org/api/workspaces//methodconfigs` (empty workspace, Terra answered **405**,
  nothing created) is treated as the finding, not the accident: a mutating request left the machine
  with no target in it.
* **`batch_freeze.py` frozen-path collision.** Dest was the basename only: two *different* source
  objects sharing a basename (`merged_PE` and `merged_SR` both `merged_pe.out`) mapped to one object,
  last writer won, `attrs --write` published the same path under both names — one of the inputs steps
  06/07 read is then simply the wrong file. Now attribute-qualified on collision (genuinely shared
  source objects still share one copy), `verify` records `verified` + mismatches in the manifest, and
  `attrs` refuses over a failed verify. **Verified** with a 17-attribute fixture, `terra.workspace`
  stubbed: 17 rows, 16 distinct dests, no dest shared by two different sources.
* **Adoption is verified, not size-guessed.** `--link-dir` adoption matched basename + byte size only
  (pipeline object names are stable and those tables are fixed-shape, so a stale capture became the
  "baseline" input). Now: all candidates collected (more than one is an error that lists them), then
  `crc32c` must match; mismatch ⇒ `!! NOT adopting … — downloading the object instead`.
  **Verified:** the pure-Python CRC is Google's (Castagnoli) — `"123456789"` → `4waSgw==` =
  `0xE3069283`; `zlib.crc32` would have been silently wrong. Over-64-MiB / composite / no-`gsutil`
  cases are labelled `unverified: …` rather than passed.
* **`batch_rerun_step.py` fails closed on unpinned/untagged `*_docker`** (the "every image is pinned
  literally" guarantee was unenforced, and it exposed a third input, `sv_base_mini_docker`, that
  nothing pinned). Opt-in escape: `--allow-unpinned-docker`. **Verified:** `show` with an untagged
  repo exits 1 listing `unpinned: GenotypeBatch.sv_base_mini_docker`.
* **`jar_flag_probe.sh` cleanup is now a trap** (EXIT/INT/TERM) registered at create time,
  synchronous, stderr kept, printing the manual delete command on failure; scope narrowed
  `storage-rw` → `storage-ro`; `create` errors no longer swallowed. **Not exercised against GCE** —
  `bash -n` + `--help` only (booting a VM needs your consent, see Open items).
* **`gatk-sv-build.sh` warns before booting** when the resolved push target has no
  `GSVTK_IMAGE_NAMESPACE` segment (docs claimed such a guard; none existed). **Verified** in
  `--dry-run`: `WARNING: push target has no 'my-dev' namespace segment: us.gcr.io/dryrun-proj/prod/sv-shell`.
* **`recon.py` no longer prints your Terra e-mail** in read-only mode (redacted unless
  `--show-identity`), because its stdout is exactly what gets pasted into issues.

## 4. Silent-wrongness fixes

`batch_cost.py`: a step with no saved metadata counted as **zero** — deleting one baseline file
flipped the published conclusion from "4.5× cheaper" to "20× more expensive" at exit 0. Now `chain
total is PARTIAL`, `ratio new/baseline: n/a`, **exit 1**; empty dir exits 1; unexpanded sub-workflow
trees and half-timestamped calls are counted and labelled FLOORS (`_missingSubWorkflows` alone was
not enough — a depth-capped tree looks complete; baseline step 10 read 918 VM-min instead of 1672.8
that way). **Verified** with four metadata fixtures plus a stray `notes.json` (which used to crash
the whole table via `STEP_RE.match(...).groups()` on `None`).

Also: `gsvtk-config work` created `runs/x` but printed the parent (two runs' `OUT` pointed at one
directory and overwrote each other — found by the first *real* selftest assertion, which is the
argument for the whole exercise); `diff_rd_states.py` refused-tautology exits (0 shared keys, 0
comparable obs), `parse_state()` instead of `int()`, `n/a` instead of `max(1,0)`; `compare_batch_tables.py`
was dead on every real invocation (`NameError: os`) and picked files by shortest name; the jq scan,
contract check, `wdl_gate --strict` and image byte-proof now report coverage and refuse partial
verdicts (`15/15 blocks`, `14 of 14 stage calls`, `PROVEN` vs `SCAN_CLEAN`).

## 5. Coordinates of anything touched outside this repo

| thing | identifier | how to verify | how to undo |
|---|---|---|---|
| CI workflow run (fixes commit 1) | `35642367098` | `gh run view 35642367098 --repo talkowski-lab/gatk-sv-testkit` | n/a (read-only job) |
| CI workflow run (fixes commit 2) | `35642810452` | same; `--exit-status` watch returned rc 0 | n/a |
| Review-panel workflow | `76519b19-57f4-4f43-ac86-ba0b47c19288` | `subagent({action:"status", id:"76519b19-…"})` → `State: complete` | n/a |
| Public repo | `talkowski-lab/gatk-sv-testkit` @ `e611324` | `gh api repos/talkowski-lab/gatk-sv-testkit/commits/main --jq .sha` | revert commits; history is public, so rewriting is the only true undo and is not advised |
| Public GCS objects **read** | `gs://gatk-sv-ref-panel-1kg-v1-1/{,mw-gatk-genotype/}` listing + one `gsutil stat` | `gsutil stat <path>` (Content-Length 1806757042, crc32c `0tadfg==`) | nothing to undo — metadata-only reads |

**External mutations actually made:** two commits pushed to `origin/main` (`78ba1a5`, `e611324`).
**Zero** Terra writes, **zero** GCE/GCR resources created, **zero** configs/submissions created.
One lane issued a `POST …/workspaces//methodconfigs` with an empty profile; Terra returned **405
Method Not Allowed** and created nothing — recorded as evidence for the pre-request identity check.
Debug probes (`gsutil ls`/`stat`, fixture dirs under `$TMPDIR`) were read-only or are outside the repo.

## 6. Resume here (paste-able)

```bash
cd ~/IdeaProjects/gatk-sv-testkit

git status -sb && git --no-pager log --oneline -1
git --no-pager log --oneline origin/main -1        # must equal the line above: e611324

make test && make audit                            # the offline gate, no creds, no network
gh run list --repo talkowski-lab/gatk-sv-testkit --limit 2   # CI state at handoff: [ok] x2

  # the three clone-backed self-tests only run when a gatk-sv clone is configured:
./kit/gsvtk-config get GATK_SV_CHECKOUT
python3 scripts/undef_module_refs.py               # the checker that caught the dead comparator

  # re-derive a cost claim from saved metadata instead of trusting docs:
python3 terra/batch_cost.py --outdir "$GSVTK_WORK/metadata"   # exit 1 = chain incomplete, by design

  # the static checks against a real clone (read-only):
python3 checks/svshell_jq_plumbing_scan.py --repo "$(./kit/gsvtk-config get GATK_SV_CHECKOUT)"
python3 checks/svshell_contract_check.py   --repo "$(./kit/gsvtk-config get GATK_SV_CHECKOUT)"
```

## 7. What "good" looks like on the next check

| check | expected (measured at handoff) | what a mismatch means |
|---|---|---|
| `make syntax` | `41 files parsed, 0 failed` | a file was added and not wired into the sweep |
| `make undefmods` | `24 files, no attribute access on an unimported module` | a `NameError`-on-first-call regression of the class that killed `compare_batch_tables.py` |
| `make helpsweep` | `11 ok, 16 skipped, 0 failed` | the 16 skips are tools needing a `firecloud`-capable interpreter; **0 failed** is the number that must not move |
| `make selftest`, no clone (CI) | `14 ok, 3 skipped, 0 failed` | a skip becoming a FAIL means a clone was found but is unusable |
| `make selftest`, clone present (local) | `17 ok, 0 skipped, 0 failed` | `canary … /bin/false returned 0` ⇒ every assertion in the file is meaningless; stop and fix the harness first |
| jq scan on real gatk-sv | `blocks: 15 in file, 15 extracted, 15 executed, 0 errored, 7 with null/empty` | `executed < in file` ⇒ extraction blind spot; the old version skipped blocks and still printed "every jq block executed" |
| contract check on real gatk-sv | `14 of 14 stage calls compared` | fewer ⇒ parser blind spot (bare/dashed/quoted/`// default` keys) |
| `make audit` | `audit: clean — no internal identifier in the publishable set` (72 tracked files) | read the HIT pattern; **never** add a file-wide exclusion (that is how it failed open before) |
| CI on a push | `[ok]`, job `offline-gate` | a fresh clone has no gatk-sv checkout: `3 skipped` is expected, not a failure |

**Cost / time / size:** **not measured, deliberately.** No compute was booted and no Terra submission
was made in this session, so there is no dollar figure to hand forward. `make test`+`make audit` are
offline and take well under a minute each (observed, not extrapolated). The only cost figures in this
repo are historical ones in `docs/archive/` (my own past runs) — re-derive anything you plan to act on
with `terra/batch_cost.py` against your own saved metadata.

## 8. Gotchas found this session (each with the text that produced it)

1. `make: *** [selftest] Error 1` + `FAIL … (exit 127: only 0-or-1 is acceptable)` and
   `/bin/bash: selftest: command not found` → my first "fix" produced `$((…))` **arithmetic** by
   dropping a `$`; make expansion is silently shape-sensitive → copy the working pattern byte-for-byte,
   or get out of make entirely (what `scripts/selftest.sh` did).
2. `selftest: … block coverage / -- a verdict from a partial scan means nothing` with **empty**
   counts → the Makefile was eating the command, not the checker failing.
3. `env: -u: No such file or directory` → `env` needs `-u` **before** `VAR=value` assignments.
4. `FileNotFoundError: '-/meta/06-new.json'` → `python3 - $T` puts `-` in `sys.argv[1]`; pass fixtures
   through the environment instead.
5. `missing dependency: firecloud (this IS fiss — pip install firecloud).` → expected on system
   `python3`; use `GSVTK_TERRA_PY` (here: a `.venv-terra` interpreter) for anything importing
   `terra.py`. Not a repo bug — it is the documented skip path in `helpsweep`.
6. `get_workspace ns/ws: HTTP 404 … workspace ns/ws does not exist` → importing `batch_freeze`
   resolves the destination bucket over the network; stub `bf.terra.workspace` in offline tests.
7. Two empty files `cnmops.stderr`, `gather.stderr` appeared in the repo root and `git add -A` staged
   them → stray shell redirects, not artifacts; deleted, and `/*.stderr`, `/*.stdout` are now
   gitignored.

## 9. Corrections to earlier documents (each was load-bearing)

* **Was:** `make test` "is the offline gate … must pass" implied the assertions ran. **Now:** the
  config-layer assertions never executed (make expansion), plus the canary/smoke/undefmods contract in
  `CONTRIBUTING.md`. Evidence: `make -n selftest` → `out="$( 2>&1)"`; `check "…" false` → `ok`. Why
  it mattered: precedence, `require` exit-4 and the documented defaults all had green evidence behind
  them that was worth nothing.
* **Was:** README "Recon, status, cost … **never write**." **Now:** never write **outside
  `$GSVTK_WORK`** — recon dumps eight JSONs there, and they contain a workspace inventory. Evidence:
  `recon.py` dump calls; falsifier F9.
* **Was:** CONTRIBUTING "prints the per-minute price from the compute API before booting". **Now:** it
  names machine type/zone/timeout and quotes no price (`grep -c price docker/gatk-sv-build.sh` →
  **0**). Why it mattered: a safety claim people relied on when deciding to boot.
* **Was:** `--expect-*-md5` optional and "byte-identity check passed" printed once per run. **Now:**
  `PROVEN` only with both expectations supplied and matched, else `SCAN_CLEAN`, per file.
* **Was:** `batch_cost.py` "these are measurements, not floors" whenever `_missingSubWorkflows` was
  empty. **Now:** completeness is checked (missing steps, unexpanded trees, half timestamps) and the
  claim is refused when false.
* **Was:** `docs/archive/README.md` "Real values were replaced by placeholders" while three of my own
  Cromwell submission ids survived. **Now:** scrubbed to `<submission-id-1..3>`, with the one real
  **public** ref-panel `gs://` path kept and justified by `gsutil ls`/`gsutil stat` (that object exists
  only at that Cromwell-derived path).
* **Was:** `kit/gsvtk-config` help implied `work [SUBDIR…]` prints the work dir. **Now:** it prints the
  directory it created, subdirs included — matching every `OUT="$(gsvtk_work runs/x)"` caller.

## 10. Deliverables

| file | change |
|---|---|
| `scripts/selftest.sh` | new — real assertions, canary, positive controls, clone-backed checks |
| `scripts/undef_module_refs.py` | new — attribute access on an unimported module |
| `Makefile` | `selftest` one-liner; `smoke`; `undefmods`; `scripts/*.sh` in `syntax`; audit fail-open fixes |
| `terra/stage_inputs.py` | injection fix; `crc32c` adoption verification + ambiguity refusal |
| `terra/batch_freeze.py` | collision-safe frozen names; `verified` in manifest; `attrs` gated on verify |
| `terra/batch_cost.py`, `terra/batch_save_metadata.py` | completeness refusal, `quality()`, exit codes |
| `terra/batch_rerun_step.py`, `terra/batch_configs.py`, `terra/terra.py` | pinning fail-closed; identity-before-POST; `assert_writable_target` |
| `terra/recon.py`, `checks/image-check/*`, `docker/gatk-sv-build.sh` | identity redaction; cleanup trap + `storage-ro`; namespace warning |
| `checks/*`, `compare/*`, `kit/*` | coverage honesty, join/parse guards, `work` semantics, provenance, stderr |
| `README.md`, `CONTRIBUTING.md`, `docs/*.md`, `docs/archive/*` | claims made true; new gate/verdict semantics; "when a check says OK and you do not believe it" |

**Commits / pushes:** `gatk-sv-testkit` → `78ba1a5` then `e611324`; remote head re-read
(`git log --oneline origin/main -1` = `e611324`, `git status -sb` clean, `behind=0 ahead=0`). CI green
on both (`35642367098`, `35642810452`).

## Open items / next steps

- [ ] **First real end-to-end use with your own account.** Nothing in the Terra or GCE paths has ever
      run as a real user from this repo — `recon` → `freeze copy --write` → `verify` → `attrs --write`
      → one `rerun submit --confirm` on a small step. Worked so far: read-only discovery, static
      checks, the offline gate. Know it worked when: a submission you can see in the Terra UI whose
      cost `batch_cost.py` reproduces from saved metadata without a `PARTIAL` line.
- [ ] Publish the `pi` skill — `~/.pi/agent/skills/gatk-sv-testkit/` (`SKILL.md`,
      `scripts/gsvtk`, `references/workflows.md`) is a real work product and is in **no git repo**; it
      exists only on this box. Decide: into this repo (e.g. `pi-skill/`) or a dotfiles repo. Its
      wrapper dispatches only read-only/safe modes and refuses `create/submit/copy/attrs/fetch/profile`.
- [ ] Add a UUID/identifier audit pattern with a **documented allowlist mechanism** (deliberately not
      done: it needs a per-file exemption, and per-file exemptions are exactly the failure mode that
      made `audit` fail open). Verify by planting a UUID in a tracked file and seeing a HIT.
- [ ] Exercise `jar_flag_probe.sh` and `svshell_image_check.sh` once against a real image (cleanup trap,
      `--boot-disk-auto-delete`, serial-console capture, `PROVEN` vs `SCAN_CLEAN` wording) — code-only
      verified so far, and they boot VMs, so they need a human typing the command.
- [ ] Run one of `examples/*.sh` end-to-end (never done: they stage tens of GB and start compute).
- [ ] Repo polish: GitHub description/topics, and whether to enable Issues.
- [ ] Decide the fate of the **pre-move scratch tree** this repo was built from (a machine-local
      directory outside any repo — so unversioned, and its name is a blocklisted internal identifier,
      which is why it is not written here — `make audit` refused it on its first two drafts, correctly,
      including once when the refusal was the *subject* of the sentence). Superseded by this
      repo, but it is the only place some as-run scratch logs exist. Find it with
      `ls ~/*/genotypebatch* 2>/dev/null` on the original box, or let it go.
- [ ] The upstream gatk-sv clone the checks run against (`~/IdeaProjects/gatk-sv`) is on a **personal
      working branch** at `857419a0` — re-read the branch with `git -C ~/IdeaProjects/gatk-sv branch
      --show-current` rather than trusting this line, and note its name is a blocklisted personal
      identifier (`make audit` refuses it) with only untracked tool dirs (`.claude/`, `.serena/`, `.tokensave/`, `wt/`) — no code
      changes from this session; leave as-is unless the rename work resumes.
- [ ] Skill-tooling gap (outside this repo): `session-handoff/SKILL.md` tells you to prove a handoff
      with `scripts/handoff_scan.sh`, which does not exist in that skill dir — the headings in this doc
      were verified by hand, so re-verify mechanically once the script exists.
