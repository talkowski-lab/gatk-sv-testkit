# Contributing

Before anything else:

```bash
make setup            # ./.venv with what the tools import
make test && make audit
```

`make test` is the offline gate and must pass with no credentials, no data and no network. Its six
parts each exist because the previous one proved insufficient:

| part | what it proves | why `--help` + `py_compile` were not enough |
|---|---|---|
| `syntax` | every `.sh` parses (`bash -n`), every `.py` compiles | — |
| `undefmods` | no attribute access on a module the file never imports | `os.path.isdir` in a file with no `import os` compiles fine and dies on first real call |
| `flake` | the pyflakes sweep (`flake8 --select=F`, nothing else): undefined names, imports that are not there, values computed and then dropped | the half of `NameError` that `undefmods` cannot see (bare names, not module attributes), and the "built the diagnostic, never used it" class — a check that silently stops checking still prints `ok` |
| `helpsweep` | `--help` works on every tool with zero configuration | argument parsers are the only code path `--help` reaches |
| `smoke` | the tools actually **run** end-to-end on hostile/empty fixtures | `--help` never reaches `main()`; the comparator that was dead on every real invocation passed all of the above |
| `selftest` | the config layer, both checkers' parsing, the checkers against a real clone, and `scripts/probe_fixes.py` — with **positive controls** (blocks executed must equal blocks present; ≥ 12 stage calls compared; the probe tally must account for every probe) | "exit code was acceptable" is satisfied by a checker that detects nothing |

When you fix a defect that was reproduced, add the reproduction to
`scripts/probe_fixes.py` (one function, one entry in `PROBES`, and raise the pinned count in
`scripts/selftest.sh`). A fix described only in a commit message regresses the next time the file
is edited; `selftest`'s probe count is what makes "the guard still fires" a checked claim. Each
probe also needs its control — the phase proving the guarded path was reachable — because
"the guard never fired" and "the guard could not fire" otherwise print the same thing.

`selftest` also runs a **canary** (it asserts a known-failing command is reported as failing). That
exists because the assertions used to live in a Makefile recipe where `$$($("$@") 2>&1)` is expanded
by make into nothing — bash received `out="( 2>&1)"`, no command ran, and all eight config
assertions reported `ok` forever. Keep assertions in `scripts/selftest.sh`, never in a recipe.

`make audit` fails if
any file that would go public contains an internal identifier from the private working
directory this repo was assembled out of. Both run in CI for exactly that reason.

## The bar for a new tool

**It removes an hour of waiting, or it catches a bug before it costs VM money.** If it does
neither, it belongs in [gatk-sv](https://github.com/broadinstitute/gatk-sv), not here.

Concretely, a tool earns its place by answering one question in one sentence ("does this WDL
actually bind its inputs?", "did the shipped image get the bytes I tested?"). If you cannot
say the sentence, the tool will end up as a pile of flags. Also: if an existing tool here
almost covers it, extend that one — duplication is how bindings drift between steps, and the
worst bugs in this repo's history were two places disagreeing about one value.

## Configuration

**A new environment-specific value goes in `kit/gsvtk-config`, in the same commit that reads
it.** Tools must not read the environment directly: no `os.environ.get("GSVTK_…")`, no
`$GSVTK_…` in a shell script that did not source `kit/config.sh`. One resolver, one
precedence chain (env > profile > derived > default), one place to answer "where did this
value come from" (`./gsvtk-config show`).

Give it a default, or justify having none in the entry. The only keys that currently refuse a
default are `GSVTK_PROJECT` and `GSVTK_TERRA_NAMESPACE`/`_WORKSPACE`, because they decide
whose billing account runs and whose workspace gets written — a guessed default there spends
someone's money or overwrites someone's configs. Anything that could plausibly default *did*
get a default, and the reason for each is in the file.

Add a row to `docs/config.md` in the same commit. A key that exists but is undocumented is a
key nobody will find, and the next person will hardcode the value instead.

## Mutating vs read-only

**Read-only by default. Mutating requires an explicit, typed-out confirmation.**

- Recon, status, cost, fetch, every `checks/` and `compare/` tool: never write outside
  `GSVTK_WORK`, never submit.
- Mutators (`create`, `copy`, `attrs --write`, `submit`) refuse without confirmation — the
  Terra helpers additionally thread `confirm=True` internally so a caller cannot mutate by
  accident, `submit` needs `--confirm` on the command line as well, every write to Terra refuses
  the shared baseline workspace unless `--allow-shared-target`, and every Terra mode resolves
  namespace + workspace before the first request (an unset target is exit 4, not a request with a
  hole in its URL).
- A new mutator needs a guard **and a probe**: `scripts/probe_fixes.py` proves the rerun-step and
  freeze guards fire by counting requests, so "it refuses" stays a checked claim rather than a
  comment.
- Anything that starts compute names **what it boots** before booting:
  `docker/gatk-sv-build.sh` prints project, zone, machine type and the timeout ceiling in its
  preflight, and `--dry-run` / `--check` print the plan and touch nothing. It deliberately does **not**
  quote a price — there is no rate lookup anywhere in this repo, because your billing account, discounts
  and spot pricing are not ours to read. Multiply the machine type by your own rate; the timeout ceiling
  is the only bound this repo can give you, and `checks/image-check/*.sh` name theirs the same way.
- **No `make` target may boot compute or mutate remote state.** The money paths stay
  human-typed. That is a review objection, not a style note.

Two conventions worth keeping in mind while editing:

- **Name the missing thing.** A tool fails with the key it wanted, the file to edit, and an
  exit code (`require` exits 4). Never a traceback, never a silent empty result — an empty
  table that reads like "no differences" is worse than a crash.
- **A checker's findings are a diff, not a verdict.** Against upstream gatk-sv the static
  checkers report real findings today. So they print counts and take a baseline —
  `--compare-to <ref>` for the jq plumbing scan, a base ref as an argument for
  `checks/wdl_gate.sh` — and the gate compares against that baseline instead of asserting
  zero.

## Adding or changing a doc

Write it fresh. Do not copy a session log: `docs/archive/` is for those, and it is published
with coordinates redacted on purpose.

- One doc per loop, and it must contain a command you actually ran. **Never document a flag you
  did not run.** `make test` catches `--help` drift, not doc drift; several wrong flags were
  found in these docs by running them, and each was one a reader would have trusted.
- State the failure modes you hit, with the **verbatim** error text, in
  [docs/troubleshooting.md](docs/troubleshooting.md). That file exists so the next person can
  paste their error into a search box. A paraphrased error message is a dead link.
- State where the money goes and what is read-only, in the doc, near the top.
- If you redact anything while publishing, add the pattern to `AUDIT_PATTERNS` in the
  Makefile. The pattern list is the memory; the audit is the enforcement.

## Corrections get published, not deleted

When something this repo claims turns out to be wrong — a number, a mechanism, a
"this is how it works" — fix the text **and** leave a record of what was believed and what
replaced it. See [docs/methodology.md](docs/methodology.md); four load-bearing claims here were
proven wrong and all four are listed with their replacements, because the shape of those
mistakes (a mechanism asserted before it was reproduced, a number that was an artefact of
subsampling) is the most transferable thing in the corpus.

Same rule for tool behaviour: if you fix a bug that produced a wrong number, the release note
says which published numbers are suspect. An analysis toolkit that quietly corrects itself is
not trustworthy — the point of the frozen manifests, recorded etags and re-checkable
`batch_cost.py` arithmetic is that someone else can redo the arithmetic and get what is written.

## Style, only where it matters

`kit/` is loaded by everything and must stay tiny, dependency-free and bash-3.2 compatible (macOS
ships that; the config shim broke silently on a bash-4 feature before). Prefer the standard
library in `compare/`. External binaries (`jq`, `bcftools`, `gsutil`) are fine when the tool says
what is missing and how to install it — see the guarded `firecloud` import in `terra/terra.py`
for the shape of that message.

Comments explain why, not what: `# literal File values must be quoted`, not `# set variable`.
