# Contributing

Before anything else:

```bash
make setup            # ./.venv with what the tools import
make test && make audit
```

`make test` is the offline gate and must pass with no credentials, no data and no network. It has seven
parts, and each one was added because the part before it let something through:

| part | what it proves | why `--help` + `py_compile` were not enough |
|---|---|---|
| `syntax` | every `.sh` parses (`bash -n`), every `.py` compiles | nothing, it is the baseline |
| `undefmods` | no attribute access on a module the file never imports | `os.path.isdir` in a file with no `import os` compiles fine and dies on first real call |
| `flake` | the pyflakes sweep (`flake8 --select=F`, nothing else): undefined names, imports that are not there, values computed and then dropped | the half of `NameError` that `undefmods` cannot see (bare names, not module attributes), and the "built the diagnostic, never used it" class: a check that silently stops checking still prints `ok` |
| `helpsweep` | `--help` works on every tool with zero configuration | argument parsers are the only code path `--help` reaches |
| `smoke` | the tools actually **run** end-to-end on hostile/empty fixtures | `--help` never reaches `main()`; the comparator that was dead on every real invocation passed all of the above |
| `audit` | the publishable file set holds no credential shape and no value that is one of this machine's own coordinates | code that passes every check above still ships a doc that names your project, your bucket and your home path: handoff 003 did exactly that (9 hits) while `make test` was green, because the audit was a separate target and prose is not compiled |
| `selftest` | the config layer, both checkers' parsing, the checkers against a real clone, `scripts/probe_fixes.py`, the shipped skill (`scripts/check_skill.py`) and the docs' own structure (`scripts/check_docs.py`), with **positive controls** (blocks executed must equal blocks present; ≥ 12 stage calls compared; the probe tally must account for every probe; each new checker carries a control that must fail) | "exit code was acceptable" is satisfied by a checker that detects nothing |

When you fix a defect that was reproduced, add the reproduction to `scripts/probe_fixes.py`: one
function, one entry in `PROBES`, and a raised count in `scripts/selftest.sh`, which pins the total. A fix
described only in a commit message tends to come back the next time someone edits the file; the pinned
probe count in `selftest` is what makes "the guard still fires" something you can check. Each probe also
needs its control, the phase that proves the guarded path was reachable. Without that, you cannot tell a
guard that never fired apart from one that could never fire, and the two print the same output.

`selftest` also runs a **canary**, which asserts that a known-failing command is reported as failing.
That exists because the assertions used to live in a Makefile recipe where `$$($("$@") 2>&1)` is expanded
by make into nothing: bash received `out="( 2>&1)"`, no command ran, and all eight config assertions
reported `ok` forever. Keep assertions in `scripts/selftest.sh`, not in a recipe.

`make audit` is now one of the seven parts above, and still runs on its own (`make audit V=1` shows what
it read). It fails if the publishable file set holds a credential shape (a service-account address, a
private key, an absolute home path), or a value that resolves to one of this machine's own coordinates:
the project, Terra workspace, registry path or checkout path that your configuration resolves to, minus
whatever resolves identically with no profile (those are shipped defaults, public by construction). No
file is exempt, and nobody's identifiers are in the pattern list. The personal half comes from the
machine doing the publishing, so CI checks the shapes while your machine checks your own names. That is
why you run `make audit` locally before pushing. CI runs both for the same reason.

One limit to know before you trust a green gate: the audit scans the **git-tracked** set. A leak sitting
in an untracked file passes `make test` and fails it one commit later, so `git add` first when the
question is "is this ready to publish".

## The bar for a new tool

**It removes an hour of waiting, or it catches a bug before it costs VM money.** If it does neither, it
belongs in [gatk-sv](https://github.com/broadinstitute/gatk-sv).

Concretely, a tool earns its place by answering one question in one sentence ("does this WDL actually
bind its inputs?", "did the shipped image get the bytes I tested?"). If you cannot say the sentence, the
tool will end up as a pile of flags. Also, if a tool already here almost covers it, extend that one.
Duplicated steps drift apart, and the worst bugs in this repo's history were two places disagreeing about
one value.

## Two rules about checks

Both come from mistakes that actually happened, and `make test` enforces both.

**Refuse to score an empty input.** A tool that reads nothing must not report a clean answer. Every site
where this repo was fooled by an empty result is on the record: `fapi` answering `[]` for a workspace
holding 5 submissions (`docs/terra-head-to-head.md` §8), `recon` exiting 0 on exactly that answer, a
runner finishing green with `records=0` because the image was missing, an `elif` chain skipping the checks
after it and calling the result a REVIEW, a manifest silently losing an entry. So an empty input is a
named state with its own exit code, not a `0` that looks like success, and "empty" and "absent" are two
different answers (`fetch_outputs.py` gives them exit 4 and 3, and says which it means). The same applies
when a check's target disappears: `artifact_tally.py` exits 2 on zero records counted, and
`wdl_semantics.py` reports `LOAD-FAILURES`, because a count over a partially-parsed tree is a partial
answer that looks complete.

**Every fixed defect ships with a probe that has a POSITIVE CONTROL.** `scripts/probe_fixes.py` pins one
confirmed defect per probe, and each probe first proves the guarded path was *reachable* with a control
that would fail if the guard were removed. Otherwise a guard that can never fire passes forever. Two
probes were nearly vacuous while being written, which is why this is spelled out. One fake returned a bare
dict where `fiss` returns a `requests.Response`, which made the client leg report itself "unavailable",
and the probe passed without testing the client it exists to watch. Another fixture promised to declare
what the config binds while deriving only the inputs side, and scored 10 findings about something else. If
you add a guard, add the phase that proves it could have been hit.

## Configuration

**A new environment-specific value goes in `kit/gsvtk-config`, in the same commit that reads it.** Tools
must not read the environment directly: no `os.environ.get("GSVTK_…")`, no `$GSVTK_…` in a shell script
that did not source `kit/config.sh`. One resolver, one precedence chain (env > profile > derived >
default), and one place that answers "where did this value come from" (`./gsvtk-config show`).

Give it a default, or justify having none in the entry. The only keys that currently refuse a default are
`GSVTK_PROJECT` and `GSVTK_TERRA_NAMESPACE`/`_WORKSPACE`, because they decide whose billing account runs
and whose workspace gets written: a guessed default there spends someone's money or overwrites someone's
configs. Anything that could plausibly default *did* get a default, and the reason for each is in the
file.

Add a row to `docs/config.md` in the same commit. A key that exists but is undocumented is a key nobody
finds, and the next person hardcodes the value instead.

## Mutating vs read-only

**Read-only by default. Mutating requires an explicit, typed-out confirmation.**

- Recon, status, cost, fetch, every `checks/` and `compare/` tool: never write outside `GSVTK_WORK`, never
  submit.
- Mutators (`create`, `copy`, `attrs --write`, `submit`) refuse without confirmation. The Terra helpers
  additionally thread `confirm=True` internally so a caller cannot mutate by accident, `submit` needs
  `--confirm` on the command line as well, every write to Terra refuses the shared baseline workspace
  unless `--allow-shared-target`, and every Terra mode resolves namespace + workspace before the first
  request (an unset target is exit 4, not a request with a hole in its URL).
- A new mutator needs a guard **and a probe**: `scripts/probe_fixes.py` proves the rerun-step and freeze
  guards fire by counting requests, so "it refuses" stays something that gets tested.
- Anything that starts compute names **what it boots** before booting. `docker/gatk-sv-build.sh` prints
  project, zone, machine type and the timeout ceiling in its preflight, and `--dry-run` / `--check` print
  the plan and touch nothing. It deliberately does **not** quote a price. There is no rate lookup anywhere
  in this repo, because your billing account, discounts and spot pricing are not something this repo can
  see. Multiply the machine type by your own rate; the timeout ceiling is the only bound this repo can
  give you, and `checks/image-check/*.sh` name theirs the same way.
- **No `make` target may boot compute or mutate remote state.** The paths that cost money stay things a
  person types by hand. This comes up in review, and it is not a style preference.

Two conventions worth keeping in mind while editing:

- **Name the missing thing.** A tool fails with the key it wanted, the file to edit, and an exit code
  (`require` exits 4). No tracebacks, and no silent empty results either. An empty table that reads like
  "no differences" is worse than a crash.
- **A checker's findings are a diff.** Against upstream gatk-sv the static checkers report real findings
  today, so they print counts and take a baseline: `--compare-to <ref>` for the jq plumbing scan, a base
  ref as an argument for `checks/wdl_gate.sh`. The gate compares against that baseline instead of
  asserting zero.

## Adding or changing a doc

Write it fresh. Do not copy a session log. The transcripts in `docs/handoff/` and `docs/archive/` are
ignored rather than tracked, and not only because of their coordinates: a transcript quotes real paths
where a doc has to state the general case, and the two are different documents.

- One doc per loop, and it must contain a command you actually ran. **Never document a flag you did not
  run.** `make test` catches `--help` drift, not doc drift; several wrong flags were found in these docs
  by running them, and each was one a reader would have trusted. `scripts/check_docs.py` now closes part
  of that gap: unbalanced fences, an info string that lost its fence (one doc rendered "do not attach
  `recon/*.json` to an issue" **as shell code**), and relative links to files that are not there. It
  checks whether the prose is *structurally* intact and cross-referable. Whether a documented flag exists
  is still your job, because the only oracle for that is running it.
- State the failure modes you hit, with the **verbatim** error text, in
  [docs/troubleshooting.md](docs/troubleshooting.md). That file exists so the next person can paste their
  error into a search box. A paraphrased error message is a dead link.
- State where the money goes and what is read-only, in the doc, near the top.
- If you had to redact something to publish, **do not add that string to this repository.** It is your
  coordinate, not the toolkit's. Put it in `audit.local.txt` (gitignored) so the audit learns it on
  *your* machine, where it is derived from your config anyway. The only thing that may be added to
  `SHAPES` in `scripts/audit.py` is a machine-independent **class**, a form that would catch somebody
  else's leak too. The previous design shipped one person's project id, workspace names, initials and
  hostnames, and needed to exempt the Makefile from its own scan to function. That is the failure this
  rule closes. `make selftest` asserts a planted leak in a file named `Makefile` is still reported.

### Coordinates: placeholder plus legend

Working prose publishes measurements, and measurements carry coordinates. The house rule came out of a
handoff doc that shipped nine private coordinates while the publish gate was green: replace the value
with a `<placeholder>` **and ship a legend table next to it**
saying what the placeholder is and where the real value lives (`kit/gsvtk-config show` →
`GSVTK_PROJECT`, the workspace's `bucketName`, the submission list in the Terra UI). A bare `<...>`
withholds nothing useful and destroys the reproducibility that made the doc worth writing; a legend keeps
the doc runnable on the machine that ran it.

The line is drawn at objects, not at names: workspace **names** are kept (the namespace is a shipped
public default, and the name is what you type to find the run again), while project ids, registry paths,
dev buckets, home paths, submission/workflow UUIDs and workspace **bucket** UUIDs are replaced.
`make audit` enforces the machine-specific half: your own coordinates and credential shapes, on the
tracked file set, as part of `make test`. It cannot arbitrate somebody else's workspace or branch name,
which is exactly why the legend is the convention rather than an optional courtesy: three reviews
disagreed about what may be published until the rule was written down.

## Corrections stay in the record

When something this repo claims turns out to be wrong (a number, a mechanism, a "this is how it works"),
fix the text **and** leave a record of what was believed and what replaced it. See
[docs/methodology.md](docs/methodology.md). Four load-bearing claims here were proven wrong and all four
are listed with their replacements. The shape of those mistakes, a mechanism asserted before it was
reproduced and a number that was an artefact of subsampling, is the most useful thing in the corpus.

Same rule for tool behaviour: if you fix a bug that produced a wrong number, the release note says which
published numbers are suspect. An analysis toolkit that quietly corrects itself is not trustworthy. The
point of the frozen manifests, recorded etags and re-checkable `batch_cost.py` arithmetic is that someone
else can redo the arithmetic and get what is written.

## Style, only where it matters

`kit/` is loaded by everything and must stay tiny, dependency-free and bash-3.2 compatible (macOS ships
that; the config shim broke silently on a bash-4 feature before). Prefer the standard library in
`compare/`. External binaries (`jq`, `bcftools`, `gsutil`) are fine when the tool says what is missing and
how to install it; see the guarded `firecloud` import in `terra/terra.py` for the shape of that message.

Comments explain why, not what: `# literal File values must be quoted`, not `# set variable`.
