# Archive: session records from the investigation

These are working documents from the work that produced this toolkit, published as-is with
account-specific coordinates replaced. They are **not** documentation of the current state of the
code — [the docs one level up](../) are. Read these for the reasoning, the dead ends, and the
mistakes.

| File | What it is |
|---|---|
| `CHECKPOINT.md` | the main record: verified state at a handoff point, a claims ledger, the gotchas with their verbatim error text, the corrections, and open queue items |
| `PLAN.md` | the original plan for the head-to-head, including the parts that changed once reality was measured |
| `HANDOFF-2026-09-15.md` | a short resume-here note, kept as an example of the format |
| `as-run/` | three driver scripts plus the `_svshell_probe.py` input probe, exactly as they ran in one session — superseded by [`../../examples/`](../../examples/), kept because they show the shape of the real thing |

## What was removed, and why

Real values were replaced by `<angle-bracket>` placeholders:

- GCP project ids, Terra namespaces/workspace names and workspace ids, workspace `gs://` buckets,
  container registry paths, ephemeral VM bucket names;
- personal names, e-mail addresses, colleague and branch names;
- local absolute paths and per-machine interpreter/venv locations;
- **Cromwell submission ids** of my own runs (`<submission-id-1>` …). They are unusable by anyone
  without access to that workspace, so they carry no reproducibility value here — they are only a
  pointer from a private run into a public document.

What is **not** placeholder'd, deliberately: statuses, VM-minutes, job counts, dollar costs, counts of
variants and the timeline of what failed when. Those are the measurements this archive exists to
record, and sandblanking them would leave prose with no evidence. Re-derive any of them with
`terra/batch_cost.py` against your own saved metadata rather than trusting the numbers below.

One real `gs://` path is also left intact, in `replay/single_sample_extra_inputs.json`: the reference
panel's BAF object, `gs://gatk-sv-ref-panel-1kg-v1-1/submissions/…/cacheCopy/all_samples.baf.txt.gz`.
It is a **public** bucket, that object exists only at that Cromwell-derived path (verified with
`gsutil ls`/`gsutil stat`, which is also why the file is a working example rather than a template), and
the run ids inside it belong to the panel's builder, not to a private workspace. If you use a
different panel, replace both `ref_panel_baf*` values — and note that object has a `crc32c`, which is
what `terra/stage_inputs.py` now checks before it adopts a local copy of it.

Placeholder'd sentences are still true — they just aren't about anyone's account. The public `gs://` resource
buckets (`gs://gatk-sv-resources-public`, `gs://gatk-sv-ref-panel-1kg-v1-1`,
`gs://gcp-public-data--broad-references`) were kept: they are anonymously readable and named in
gatk-sv's own documentation. The baseline **workspace** coordinate was placeholder'd like every
other one — the live default lives in `kit/gsvtk-config` as `GSVTK_BASELINE_NAMESPACE`/`_WORKSPACE`
and is described in [config.md](../config.md), not here.

The removal was mechanical (string and regex substitution), so some phrasing now reads awkwardly —
"the `<branch-under-test>`" and so on. Nothing was rewritten: where a sentence said something wrong,
it still says it, and the correction is recorded in the same file.

## Why publish the mistakes

Because four load-bearing claims turned out to be wrong, and the *shape* of those mistakes is the
most transferable thing in the corpus: a sign error that looked symmetric, a mechanism asserted from
a summary statistic, a ratio that was an artefact of interval subsampling, a gating difference
claimed after checking only one of two implementations. See
[methodology.md](../methodology.md) for those in detail.

A cleaned-up narrative would hide exactly the failure modes you are most likely to repeat.

## How to read the numbers in here

Every figure belongs to one cohort at one code state. Before quoting one:

1. check which run produced it — subsampled intervals and full intervals produce same-named
   numbers with different meanings, and for RD cutoffs the difference is large;
2. check the claim's status in the ledger (`CONFIRMED` / `REFUTED` / `SUPERSEDED` / `OPEN`);
3. check whether a later correction supersedes it.

The durable parts are the *methods* — freeze-and-pin, replay-a-stale-run, verify-shipped-bytes,
per-ref materialization, and the rule about never diffing a statistic whose inputs are a superset
on one side. The numbers are not durable. If you use this archive as evidence for a claim about
your own cohort, re-measure; [methodology.md](../methodology.md) lists the few claims that can be
re-checked in minutes offline.
