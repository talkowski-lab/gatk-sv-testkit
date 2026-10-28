# How this kit decides a check passed

Every tool and gate in this repo follows the same few rules about what counts as a pass. They apply to
the comparators, the static checks, the Terra tools and the test suite alike. If you add a tool or a
test, follow them too.

## A check must be able to fail

A check is only trusted after it has been shown to go red on a known-bad input. That known-bad case is
the positive control, and it ships beside the check:

- `python scripts/golden_configs.py --control` removes one input binding and one output binding from a
  copy of the config table and requires the golden comparison to fail both times.
- The selftest phases plant a defect (a renamed key, an edited `export` list, a fixture that should
  refuse) and assert the tool notices, alongside the clean case that must pass.
- `scripts/check_skill.py` runs each mode the skill says it refuses and requires the refusal.

When you add a check, add the case that makes it fail. A check that has never failed may be checking
nothing.

## Nothing compared is not a pass

An empty result is reported as its own outcome. The comparators share one exit-code convention:

| Exit | Meaning |
|---|---|
| 0 | Inputs compared, no differences. |
| 1 | Inputs compared, differences found. |
| 2 | Nothing was compared (no overlapping rows, keys or samples; unreadable input; a filter that matched nothing). |

The same rule runs through the rest of the kit. `kit/module_profile.py` refuses an empty `steps` list,
an empty `inputs` map, an empty `export` list and a blank `entity_row`, and `--print-exports` exits 4
rather than planning a fetch of nothing. `batch_configs.py check` prints `TEMPLATE NOT READ` per step
instead of implying it compared production's bindings. A value the tool would otherwise have to guess
(an entity row, an image ref) is refused by name.

## A skip is not a pass

When a prerequisite is missing (no gatk-sv checkout, no womtool jar, no miniwdl), a check prints a named
SKIP saying what is missing. Skips are counted separately and never count toward a pass:

- The probe runner in `scripts/selftest.sh` requires a minimum number of probes to have run. A probe that
  skipped does not count, and the failure message names the dependency to install.
- Each selftest phase reports how many assertion call sites it declares and how many it reached
  (`scripts/selftest.d/declared.sh`). A site may go unreached only inside a region whose skip actually
  fired; any other unreached site fails the phase.

A green run therefore means: this commit, on this machine, with these prerequisites. Read the SKIP lines
to see what was not graded. To grade a commit rather than your working tree, run `make verify-commit`
(optionally with `REV=<rev>`). It builds a detached worktree, runs the gate there, and removes it. The
fresh tree removes uncommitted files, but the environment is inherited: `GSVTK_CONFIG` and
`GSVTK_GATK_SV_CHECKOUT` still apply, and the target prints them on its first line.

## Derive facts, don't transcribe them

A fact copied from upstream into this repo goes stale silently. Where the kit needs one, it reads it from
the source at a ref:

- WDL inputs, outputs and requiredness come from miniwdl on the WDL at the ref, read with `git archive`
  so your checkout is never modified.
- Which attribute feeds which input comes from upstream's `.json.tmpl` launch templates at the ref.
- A step's entity type is derived from its own `this.<etype>_id` binding; `rootEntityType` is only a
  cross-check.
- The step map exists once, in `terra/steps.py`, derived from the module profile.
- Config values resolve through one resolver (`kit/gsvtk-config`), shared by Python and shell.

When a value has to be written down (a module profile's bindings, an image pin), it is written once,
validated strictly, and the reason sits beside it in a `_why_*` key. See
[module-profiles.md](module-profiles.md).

## Reproduce before you explain

Before claiming why two runs differ, reproduce the baseline quantity from its inputs, then change one
input and show the difference moves. A plausible mechanism read off a summary statistic is a hypothesis
until that is done. Note which inputs produced each number: a run on a subset of intervals and a run on
all of them produce figures with the same name and different values.

## Docs are graded for runnability, not truth

`make test` checks that every command in `README.md` and `docs/*.md` names a tool that exists and flags
its `--help` offers, and that fences and relative links are intact. It does not check that a sentence is
true. So when you change behaviour, update the docs in the same commit: `git grep -n` the flag, exit code,
message or count you changed across `docs/` and `README.md`. A claim important enough to enforce belongs
in a test that executes it, not in prose.

## Disagreeing

Most claims here can be checked offline in minutes:

```bash
python checks/svshell_contract_check.py     # the sv_shell JSON-contract claims
checks/wdl_gate.sh v1.1.1 origin/main       # WDL launchability at two refs
make test                                   # the full offline gate
```

If running something contradicts a doc, file an issue with the command and its output.
