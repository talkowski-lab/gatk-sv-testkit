# How this toolkit's claims were made, and how to disagree

The investigation that produced these tools ran for weeks against a real cohort and a real
baseline run, and it produced wrong answers along the way. The published session records in
[archive/](archive/) keep both the claims that survived and the ones that did not. This page is
the short version of why, and what it implies for anything you read here.

## Corrections stay in the record

Every claim that this work proved wrong is listed, with what replaced it, in
[archive/CHECKPOINT.md](archive/CHECKPOINT.md) under "Corrections". Deleting them would make the
record read as if the conclusions had been reached on the first try, which is not how any of them
were reached — and it would hide exactly the failure modes you are most likely to repeat.

Four of the corrected claims are worth reading as examples of the *kind* of error, not just the
content:

- **A sign error in a log transform, believed because it was symmetric.** Both implementations were
  assumed to invert `-log10(1-e^-k)`. They do not: the metric is `-log10(e^-k) = k/ln10`, so the
  conversion is `k = p·ln10` in both. The corrected version reproduces the baseline's `sr_count`
  exactly. The wrong version was more *plausible* — it looked like the kind of thing both sides
  would plausibly do.
- **A mechanism claimed from a summary statistic, then not reproduced.** A shift in one cutoff was
  attributed to a missing correction factor; emulating the filter with and without the factor moved
  the cutoff but left the quantity being explained unchanged. The omission was real and verified in
  code; its claimed *effect* was not established. Both halves of that sentence matter.
- **A number that was an artefact of subsampling.** A cutoff ratio of 3.06× came from an
  interval-subsampled run; on the full interval set it is 2.40×. The direction of the error was
  "the safe-looking one", which is the worst kind.
- **A gating difference asserted after only a partial check.** "v1.1 has no per-sample SR threshold"
  was wrong: it gates per sample in the shell. What survived was narrower — pair-level versus
  row-level gating, and explicitly labelled as not yet measured.

The pattern across all four: a mechanism was asserted before it was reproduced, and a summary
number looked like an explanation. The corrective habit is the same every time — reproduce the
baseline quantity, then perturb one input.

## The ledger, if you want to keep one

`CHECKPOINT.md` keeps a claims ledger: each claim tagged with the status it has *earned*, not the
status anyone wanted.

The vocabulary it actually uses is deliberately small:

| Status | Means |
|---|---|
| `CONFIRMED` | derived from the code, or reproduced numerically — with the file/line or the arithmetic shown |
| `REFUTED` | was believed, and something specific showed it wrong |
| `SUPERSEDED` | not so much wrong as replaced by a better-measured version, which is named |
| `OPEN` | still a hypothesis. Never quoted as a conclusion |

Three habits in that document are worth stealing regardless of how you work:

1. **The error you might actually hit, not the error you might philosophically hit.** Every gotcha
   is recorded with its **verbatim** error text, because that is what you will search for.
2. **Evidence lost is recorded as a finding.** An OOM log destroyed by an output-path collision is
   logged as lost evidence, with the mechanism fixed. Losing evidence silently is how a wrong
   conclusion survives.
3. **A run is identified by its inputs, not its intent.** Subsampled versus full-interval runs
   produce numbers with the same name and different meanings, so each published number carries
   which one it came from.

## What is *not* durable in the archive

The archive is a record of one investigation on one cohort, not a specification. Specific numbers,
cutoffs and ratios in it belong to that cohort and that code state; the *methods* transfer, the
values do not. Coordinates of real projects, workspaces, buckets, registries, personal identifiers
and branch names were replaced with `<angle-bracket>` placeholders when this repo was published —
so a placeholder is a sign the sentence is still true, just not about your account.

Where an archived statement and a fresh doc disagree, **trust the fresh doc** and consider the
archive's statement superseded — that is the direction the corrections went.

## One history rewrite, and what it cannot undo

This repo was assembled out of a private working directory, and its **first** commit shipped
coordinates that the README and `kit/gsvtk-config` present as placeholders — plus, later, a
workspace-name-derived tag hiding inside directory and script names in `docs/archive/`, and commit
messages that spelled values out while describing their removal. On 2026-09-24 the entire history was
squashed into a single commit carrying the current tree. That is the only mechanism that gets those
blobs out of what `git log -p` will show a stranger, and it is why the new commit message names no
coordinate: messages leak the same way files do.

What a rewrite does **not** do, stated instead of hoped:

* anyone who cloned before the rewrite still has the old objects, and so does anyone who forked;
* a host can keep a dereferenced commit reachable by SHA for some time after a force-push (cached
  views, pull-request refs), which needs the host's support desk rather than a `git` command;
* nothing here proves the *current* tree is clean — `make audit` does, and only for the values that
  machine can resolve. The rewrite is a one-time cleanup; the derived audit is what stops it recurring.

So treat quoted git SHAs in these docs (including the handoffs) as **pre-rewrite pointers that no
longer resolve** on `main`. Anything a document claims about a commit should be re-derived from the
tree or from your own `git log`.

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
claims above got — kept, marked wrong, with the replacement named.
