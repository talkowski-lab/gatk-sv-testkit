# How this toolkit's claims were made, and how to disagree

The investigation that produced these tools ran for weeks against a real cohort and a real
baseline run, and it produced wrong answers along the way. The session notes from that work keep
both the claims that survived and the ones that did not, but they are working copies rather than
tracked files, so this page is the part that ships: the short version of why, and what it implies
for anything you read here.

## Corrections stay in the record

Every claim that this work proved wrong is listed, with what replaced it, in the session record's
"Corrections" section, and the ones worth the reading are summarized below, because a summary is what
outlives a transcript. Deleting them would make the record read as if the conclusions had been reached
on the first try, which is not how any of them were reached, and it would hide exactly the failure modes
you are most likely to repeat.

Four of the corrected claims are worth reading for the kind of mistake rather than the content:

- **A sign error in a log transform, believed because it was symmetric.** Both implementations were
  assumed to invert `-log10(1-e^-k)`. They do not: the metric is `-log10(e^-k) = k/ln10`, so the
  conversion is `k = p·ln10` in both. The corrected version reproduces the baseline's `sr_count`
  exactly. The wrong version was the more plausible-looking of the two.
- **A mechanism claimed from a summary statistic, then not reproduced.** A shift in one cutoff was
  attributed to a missing correction factor; emulating the filter with and without the factor moved
  the cutoff but left the quantity being explained unchanged. The omission was real and verified in
  code; its claimed *effect* was never established.
- **A number that was an artefact of subsampling.** A cutoff ratio of 3.06× came from an
  interval-subsampled run; on the full interval set it is 2.40×. The error was in the
  safe-looking direction, which is the easy one to miss.
- **A gating difference asserted after only a partial check.** "v1.1 has no per-sample SR threshold"
  was wrong: it gates per sample in the shell. What survived was narrower: pair-level versus
  row-level gating, and explicitly labelled as not yet measured.

The pattern across all four: a mechanism was asserted before it was reproduced, and a summary
number looked like an explanation. The corrective habit is the same every time: reproduce the
baseline quantity, then perturb one input.

## The ledger, if you want to keep one

`CHECKPOINT.md` keeps a claims ledger. Each claim carries the status its evidence supports.

The vocabulary it actually uses is deliberately small:

| Status | Means |
|---|---|
| `CONFIRMED` | derived from the code, or reproduced numerically, with the file/line or the arithmetic shown |
| `REFUTED` | was believed, and something specific showed it wrong |
| `SUPERSEDED` | not so much wrong as replaced by a better-measured version, which is named |
| `OPEN` | still a hypothesis. Never quoted as a conclusion |

Three habits in that document are worth stealing regardless of how you work:

1. **Record the error you might actually hit.** Every gotcha
   is recorded with its **verbatim** error text, because that is what you will search for.
2. **Evidence lost is recorded as a finding.** An OOM log destroyed by an output-path collision is
   logged as lost evidence, with the mechanism fixed. Losing evidence silently is how a wrong
   conclusion survives.
3. **A run is identified by its inputs.** Subsampled versus full-interval runs
   produce numbers with the same name and different meanings, so each published number carries
   which one it came from.

## What does not transfer from the archive

The archive records one investigation on one cohort. It is not a specification. Specific numbers,
cutoffs and ratios in it belong to that cohort and that code state; the *methods* transfer, the
values do not. Coordinates of real projects, workspaces, buckets, registries, personal identifiers
and branch names were replaced with `<angle-bracket>` placeholders when this repo was published, so
a placeholder is a sign the sentence is still true, just not about your account.

Where an archived statement and a fresh doc disagree, **trust the fresh doc** and consider the
archive's statement superseded: that is the direction the corrections went.

## One history rewrite, and what it cannot undo

This repo was assembled out of a private working directory, and its **first** commit shipped
coordinates that the README and `kit/gsvtk-config` present as placeholders. Later commits added a
workspace-name-derived tag hiding inside directory and script names in `docs/archive/`, and commit
messages that spelled values out while describing their removal. On 2026-09-24 the entire history was
squashed into a single commit carrying the current tree. It is the only mechanism that gets those
blobs out of what `git log -p` shows a stranger, and it is also why the new commit message names no
coordinate, since messages leak the same way files do.

What a rewrite does **not** do:

* anyone who cloned before the rewrite still has the old objects, and so does anyone who forked;
* a host can keep a dereferenced commit reachable by SHA for some time after a force-push (cached
  views, pull-request refs), which needs the host's support desk rather than a `git` command;
* nothing here proves the *current* tree is clean; `make audit` does, and only for the values that
  machine can resolve. The rewrite is a one-time cleanup; the derived audit is what stops it recurring.

So treat quoted git SHAs in these docs (including the handoffs) as **pre-rewrite pointers that no
longer resolve** on `main`. Anything a document claims about a commit should be re-derived from the
tree or from your own `git log`.

## Green in my directory is not green in the commit

`make test` grades the working tree, so its verdict can only ever be as true as what is committed. On one day
of this project that distinction cost two bad pushes: a commit silently reverted a finished lane's shell
change, every gate run afterwards printed `make test: PASS` — true about the directory, false about HEAD — and
two of those runs were pushed before a clean checkout of HEAD failed 15 womtool assertions and lost 48
jarshape ones the dirty tree had been hiding. `make verify-commit` grades a **revision** instead: a detached
worktree from `REV` (default `HEAD`), the caller's interpreter passed by absolute path, the same tallies, then
the tree is removed. `GSVTK_VERIFY_MAKE=syntax` grades one cheap target when you only need one.

Cost decided the placement, rather than wishfulness — and cost on this machine turned out to be a story about
the machine. The offline gate is dominated by one phase, `scripts/selftest.d/womtool.sh`: measured **16m48s**
while the box was under memory pressure (roughly 60 MB free, a backup daemon resident, processes being
SIGKILLed), and the same phase inside a **148s** full `make verify-commit` once the machine was idle. An
earlier full run measured **2664s (44m24s)**. All three numbers are real; none of them is "the" cost, and the
tallies were identical in every run — which is the useful part. A fourth, added at `a1a008e` because none of
the three above says whether a womtool jar was present (it changes which legs run, not just how long they
take): with `WOMTOOL_JAR=/tmp/womtool-84.jar` (175,604,615 B) on a machine doing nothing else,
`womtool.sh` ran **27.15s** at 84 verdicts over 83 call sites with **0 skipped**, and `jarshape.sh` **8.18s**
at 50 over 31, 0 skipped; the same two phases without a jar were 24.4s and roughly the same, differing only
in the four `SKIPPED reason=no-jar` sites. So the jar costs about three seconds and buys four legs — which
is worth knowing before concluding from the numbers above that jar work is what makes this phase expensive.
The interpreter matters as much as the jar: `PYTHON=python3` on a box whose `python3` lacks numpy makes five
phases red for a reason that has nothing to do with the branch (see the `Makefile` resolution note). A
control whose runtime moves 18× while its verdicts do not is a control worth having, but size the CI timeout
from the slow number and the developer's patience from the fast one. Either way this target is a second full gate, so it is deliberately not a `test`
phase — and it is not a CI job either, which is a decision rather than an oversight. CI's `actions/checkout`
grades a *commit* in a tree that cannot be dirty, which is the whole defect this target exists to catch; a
`verify-commit` job there would re-grade a revision CI has already graded, at double the cost, and the only case
it adds is somebody passing their own `REV`. What CI cannot do is fire for a side branch at all:
`.github/workflows/ci.yml` triggers on `push` to `main`, on `pull_request`, and on `workflow_dispatch`, so
pushing a feature branch runs nothing. The target therefore stays a local pre-push gate, and the consequence is
stated instead of assumed away: on a branch, the only clean-tree grader is a person remembering to run it. That
memory is not theoretical — a clean-tree red (`aded402`: three assertion sites unrun in a fresh tree where
nothing resolved a gatk-sv checkout, with no skip region claiming them) passed every local `make test` until a
hand-run no-checkout invocation named it, on a box that then failed to finish a full gate twice in one day.
That parenthetical originally blamed `GSVTK_GATK_SV_CHECKOUT=`, meaning an **empty** value, and that mechanism
is wrong: `kit/config.py:45-47` is `return _core.get(key) or fallback`, so an empty value is falsy and falls
through the profile chain to the default, which on a configured box is a real checkout. Measured on this
repo's own phase at `d939f31` (2026-10-08): `env GSVTK_GATK_SV_CHECKOUT= bash scripts/selftest.d/entity.sh
.venv/bin/python` reaches every site (`declares 214 … nothing skipped`) while `env
GSVTK_GATK_SV_CHECKOUT=/nonexistent bash scripts/selftest.d/entity.sh .venv/bin/python` reaches 195 and
withholds 19. What makes sites unreachable is *no checkout resolves*, never `=` empty — the mechanism is
written out at `scripts/selftest.d/entity.sh:183-193`. It prints its
own `wall=<seconds>` so the next reader
measures rather than repeating these.

The teeth were demonstrated on a commit that was genuinely broken, not on a synthetic one. A commit whose
`kit/config.py` cannot compile graded `FAIL kit/config.py … SyntaxError: invalid syntax` in the fresh tree
while, in the same minute on the same machine, `make syntax` in the working tree said
`syntax: 79 files parsed, 0 failed` with rc 0. Opposite verdicts from one box, and only one of them describes
what would ship — which is the entire argument for the target.

What a green run does NOT mean, said plainly because it is the easy overclaim: a fresh tree removes
uncommitted **state**, not the **machine** and not the **environment**. The caller's Python, cloud credentials,
the operator's gatk-sv checkout and the womtool jar all live outside the repo and stay reachable — and they are
*inherited*, not scrubbed, so an exported `GSVTK_CONFIG` or `GSVTK_GATK_SV_CHECKOUT` retargets the graded run
rather than merely being visible to it. A verification run at `33c0358` found the real checkout through normal
config discovery and ran the ref-pinned cross-check rather than printing the named skip it would print on a
machine without it; the target now prints the inherited values on its first line, because a log that does not
say which checkout it graded cannot be audited afterwards. Also note `PYTHON`: a path is made absolute, but a
bare `python3` is forwarded as-is and re-resolved from `PATH` inside the tree — pass a path if the interpreter
matters. The 44-minute run quoted above was the other
outcome, and it is the one worth reading: that tree did not find a checkout, so `entity selftest` reported
`44 passed, 1 skipped` plus `declares 54 … so 10 did not run behind 1 named skip` and `selftest` reported
`81 ok, 4 skipped`. Those three figures are that run's, at `33c0358`, and the phase has grown since — the
same no-checkout shape printed `entity selftest: 195 passed, 0 failed, 1 skipped` with `declares 214
assertion call site(s); this run reached 195, so 19 did not run behind 1 named skip(s)` at `d939f31`
(2026-10-08) (`env
GSVTK_GATK_SV_CHECKOUT=/nonexistent bash scripts/selftest.d/entity.sh .venv/bin/python`), and the full offline
gate printed `selftest: 90 ok, 0 skipped, 0 failed` (`declares 71 assertion call site(s); this run
reached 71 distinct site(s) — 90 verdict(s) recorded, nothing skipped`) on that day with a checkout in hand, ending
`make test: PASS (offline gate)` (`make test < /dev/null` — the redirect is what this page runs, and its
original reason has since narrowed rather than disappeared: the docs checker used to hand every `--help` probe
the gate's own stdin, so a documented tool that reads stdin waited forever on a terminal that never sends any,
and `1268558` gave the probes `stdin=DEVNULL` so they see EOF instead. The redirect stays because it is free,
because the checker's fix covers the checker and not every phase, and because a stall that has already happened
once here is not hypothetical). Same commit, two machines,
different coverage — which is exactly why the skips are named
and counted, and why a green run means "this commit, this interpreter, this machine" and nothing more.

One blind spot fell out of the counts: `make syntax` parses 79 files here and 75 at HEAD. The four are
`docs/archive/as-run/*.py`, present on disk and gitignored, because the syntax file lists are directory globs
while the publish audit already scans the git-tracked set. A green `syntax` can therefore include files no
commit ships — harmless when they pass, but the 79-vs-75 difference is the honest tell that two targets answer
slightly different questions.

## A green gate over a false sentence: the docs-prose ruling

`make test` grades three things a document depends on: that a documented tool exists, that a documented flag
appears in that tool's own `--help`, and that the page's fences and relative links are intact. Not one of the
three is about what a sentence **claims**, and `scripts/check_doc_flags.py` made that vivid by spending 186 s of
`make selftest` executing a documented *gate phase* as a `--help` probe while reporting 0 problems either way.
The decision taken here, recorded rather than assumed: **prose truth stays ungraded, on purpose.** A checker
that asked "is this number still right" would have to know the answer, and the only oracle for a measured number
is the command that prints it; encoding those answers a second time is the two-places-disagreeing-about-one-value
failure this repo's history calls its worst. So the gate grades **runnability**, a person grades **truth**, and
what the convention actually constrains is **when** the re-run happens — the rule below, which is the shipped
home of this ruling. ([CONTRIBUTING.md](../CONTRIBUTING.md)'s "Corrections stay in the record" points here
already; a rule about what a page may assert belongs with the record of what this page got wrong, not beside the
build instructions.)

### A doc's present tense is the part that rots

- **A behaviour change carries a docs pass in the same commit**, scoped to the *present-tense* sentences about
  that behaviour: tallies, exit codes, refusal wording, "today it prints", "does not check", "blobs it cannot
  read are skipped". `git grep -n` on the number, flag, exit code or phrase you just changed, over `docs/` and
  `README.md`, is the whole technique, and it is cheap. A page quoting a tally you just moved is now false
  whether or not you touched it, and the gate is green over that.
- **Dated history keeps its numbers.** "the row used to say 62/52/10", "printed when this bullet was written",
  "at `33c0358` this reported `44 passed, 1 skipped`" are records of what was believed, not claims about this
  commit. They are not defects, they are not quietly updated, and each gains a forward pointer naming the commit
  that undid it — including "and the gate stayed green over it", because a false claim that survived CI is the
  datum the next reader needs. Roughly half of what looks like stale numbers in this corpus is that record, and
  tidying it would leave the current number looking as though it had been known at the time.
- **A prose claim load-bearing enough to enforce belongs in a leg that executes it**, not in a checker that
  reads sentences. That is already the house pattern: `scripts/selftest.d/entity.sh` *runs* the cure that
  `docs/static-checks.md` quotes instead of asserting the quote; the fixture census is pinned as literal needles;
  `scripts/check_skill.py` executes each refused mode and restates the refused list inside the checker on
  purpose, so a prose edit cannot shrink a safety contract by deleting a line. A claim that cannot be restated
  that way is one the gate genuinely cannot hold — say so in the doc, as those two files do.

What the blindness costs, with its interval, because the datum is the argument:

* `docs/static-checks.md:580` and `docs/plan-launch-any-module.md:156` of the tree before `e27f90b` both
  asserted that the entity refusal prints **no** cure. `e27f90b` made every arm print its own cure, and
  `make test` stayed green — twice in that session, on trees containing the false sentence. A reader comparing
  prose to output found it one commit later; no checker did.
* The whole-repo audit lane that followed made more present-tense prose false in the same way, with the same
  green gate: the entity phase's tallies and its skip-region extent, the fixture corpus's census
  (32 configs and one unreadable document → 42 and four), the doc checker's own coverage — for which the
  existing sentence could not be repaired, only replaced — the exit code of a comparator that crashed instead
  of refusing, and the read semantics of the history audit, which had been *skipping* every blob it could not
  decode while its docs described a scan.

## Disagreeing with any of this

The cheapest useful objection is a reproduction. Most claims here are checkable in minutes offline:

```bash
checks/svshell_contract_check.py            # the JSON-contract claims
checks/wdl_gate.sh v1.1.1 origin/main       # the launchability claims
make test                                   # syntax + pyflakes + --help on every tool + smoke runs
                                              # + self-tests/canary + probes for each fixed defect
```

Anything cost-bearing is checkable from saved metadata: `batch_cost.py`'s arithmetic is meant to be
re-run against the JSON rather than believed. If you find a claim here that a few minutes of
running contradicts, that is an issue worth filing, and it will get the same treatment the four
claims above got: kept, marked wrong, with the replacement named.
