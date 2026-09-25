# gatk-sv-testkit — task entry points. `make` alone prints the list.
#
# Everything here is offline. Only `setup` writes anything, and only into ./.venv.
# Note what is deliberately NOT wired up as a target: no target submits a Terra job,
# creates a method config, copies a bucket, pushes an image or boots a VM. Those are the
# paths that spend money, and they stay typed-out-by-a-human operations (CONTRIBUTING.md).

SHELL      := /bin/bash
.SHELLFLAGS := -o pipefail -c
.DEFAULT_GOAL := help
.PHONY: help setup test syntax helpsweep undefmods flake smoke selftest lint audit clean-work

PYTHON ?= python3
GSVTK  := ./kit/gsvtk-config

# ----------------------------------------------------------------- file sets
SH_FILES := $(wildcard docker/*.sh terra/*.sh checks/*.sh checks/image-check/*.sh \
                   examples/*.sh kit/*.sh scripts/*.sh)
PY_FILES := $(wildcard kit/*.py terra/*.py checks/*.py compare/*.py replay/*.py \
                   scripts/*.py examples/*.py docs/archive/as-run/*.py)

# Tools that accept --help AND are safe to invoke with no config file, no credentials, no
# data and no network: every one of these only prints usage and exits.
HELP_SAFE    := checks/svshell_contract_check.py checks/svshell_jq_plumbing_scan.py \
                compare/compare_batch_tables.py compare/diff_rd_states.py \
                examples/recompute_het_population.py scripts/fetch_wdl.py kit/config.py \
                scripts/audit.py
HELP_NUMPY   := compare/gq_scale_compare.py compare/gq_paired_compare.py \
                compare/profile_summarize.py
HELP_PYSAM   := compare/pair_level_concordance.py
HELP_MINIWDL := replay/build_inputs.py
HELP_TERRA   := $(wildcard terra/*.py)
HELP_SH      := checks/wdl_gate.sh docker/gatk-sv-build.sh terra/batch_fetch_compare.sh \
                examples/replay_reference_run.sh
# NOT in any sweep, on purpose (they are still syntax-checked):
#   docker/remote-build.sh      runs ON THE BUILDER VM; invoking it starts a real build
#   checks/image-check/*.sh     boot a GCE VM and take no --help (they name the image first)
#   examples/run_*.sh           drivers, not CLIs; --help would launch java/bcftools
#   kit/config.sh               a sourced shim, no main; asserted in `make selftest`

# ---------------------------------------------------------------------- help
help:
	@echo "gatk-sv-testkit — make targets"
	@echo
	@echo "  setup       create ./.venv and install requirements.txt (offline-safe, idempotent);"
	@echo "              add requirements-dev.txt (miniwdl, flake8) for the FULL gate"
	@echo "  test        the offline gate: syntax + undef-mods + pyflakes + --help sweep + real runs"
	@echo "              + 32 selftests; needs no config file, no credentials, no network. A missing"
	@echo "              optional dependency prints a SKIP naming the file to install, and the pinned"
	@echo "              probe count fails on skipped probes instead of passing on a smaller number"
	@echo "  syntax      bash -n every .sh, py_compile every .py (this is also 'lint')"
	@echo "  lint        alias of syntax: no STYLE checker on purpose (no whitespace opinions)"
	@echo "  flake       pyflakes bug sweep (undefined names, dead values); SKIPs if flake8 absent"
	@echo "  audit       fail if the publishable file set holds a credential shape, or a value that"
	@echo "              is one of THIS machine's own coordinates (make audit V=1 to see what it read)"
	@echo "  clean-work  show what the work directory holds and what WOULD be deleted"
	@echo
	@echo "  Interpreter: PYTHON=/path/to/python make test   (default: python3, then ./.venv)"
	@echo "  Run docs first: docs/config.md, docs/setup.md, then './kit/gsvtk-config doctor'."

# --------------------------------------------------------------------- setup
setup:
	@set -e; \
	if [ ! -x .venv/bin/python ]; then \
	  echo "creating ./.venv with $$(command -v $(PYTHON) 2>/dev/null || echo $(PYTHON))"; \
	  $(PYTHON) -m venv .venv; \
	else \
	  echo "./.venv already exists — reusing it (idempotent)"; \
	fi; \
	.venv/bin/python -m pip install --quiet --upgrade pip; \
	.venv/bin/python -m pip install --quiet -r requirements.txt; \
	echo; \
	echo "installed. Activate it with:"; \
	echo "    source .venv/bin/activate"; \
	echo; \
	echo "make test also wants the dev tools — without them probes SKIP and the pinned count fails:"; \
	echo "    .venv/bin/python -m pip install -r requirements-dev.txt"

# ---------------------------------------------------------------------- test
# Every phase is runnable alone and prints its own tally. All of them run even when one fails,
# then the aggregate exits nonzero -- a gate that stops at the first failure hides the other
# phases' findings, which is exactly the information you need to fix everything in one pass.
test:
	@rc=0; \
	$(MAKE) --no-print-directory syntax     || rc=1; \
	$(MAKE) --no-print-directory undefmods  || rc=1; \
	$(MAKE) --no-print-directory flake      || rc=1; \
	$(MAKE) --no-print-directory helpsweep  || rc=1; \
	$(MAKE) --no-print-directory smoke      || rc=1; \
	$(MAKE) --no-print-directory selftest   || rc=1; \
	echo; \
	if [ $$rc -eq 0 ]; then echo "make test: PASS (offline gate)"; \
	else echo "make test: FAIL — see the FAIL lines above"; fi; \
	exit $$rc

syntax:
	@fail=0; n=0; \
	for f in $(SH_FILES); do n=$$((n+1)); \
	  bash -n "$$f" || { echo "  FAIL  $$f (bash -n)"; fail=$$((fail+1)); }; \
	done; \
	for f in $(PY_FILES); do n=$$((n+1)); \
	  $(PYTHON) -m py_compile "$$f" 2>/tmp/gsvtk_pycompile.err \
	    || { echo "  FAIL  $$f"; sed 's/^/          /' /tmp/gsvtk_pycompile.err; fail=$$((fail+1)); }; \
	done; \
	printf 'syntax: %s files parsed, %s failed\n' "$$n" "$$fail"; \
	[ $$fail -eq 0 ]

# --help on every tool, with an empty profile and a throwaway work directory, so a tool
# that only works when configured is a BUG this catches (that was a real regression: a
# --help path that called require() and died). Missing optional dependencies SKIP.
helpsweep:
	@tmp="$$(mktemp -d)"; : > "$$tmp/empty.env"; \
	py="$(PYTHON)"; \
	if ! $$py -c 'import firecloud' 2>/dev/null && [ -x .venv/bin/python ]; then \
	  py=.venv/bin/python; fi; \
	ok=0; skip=0; fail=0; \
	echo "--help sweep (interpreter: $$py; GSVTK_CONFIG=<empty>, GSVTK_WORK=<tmp>)"; \
	run() { \
	  t="$$1"; mod="$$2"; \
	  if [ -n "$$mod" ] && ! $$py -c "import $$mod" >/dev/null 2>&1; then \
	    printf '  SKIP  %-42s needs module "%s" (make setup / pip install -r requirements.txt)\n' "$$t" "$$mod"; \
	    skip=$$((skip+1)); return 0; \
	  fi; \
	  case "$$t" in \
	    *.sh) out="$$(GSVTK_CONFIG="$$tmp/empty.env" GSVTK_WORK="$$tmp/work" bash "$$t" --help 2>&1)"; rc=$$? ;; \
	    *)    out="$$(GSVTK_CONFIG="$$tmp/empty.env" GSVTK_WORK="$$tmp/work" $$py "$$t" --help 2>&1)"; rc=$$? ;; \
	  esac; \
	  if [ $$rc -eq 0 ]; then ok=$$((ok+1)); printf '  ok    %-42s\n' "$$t"; \
	  else fail=$$((fail+1)); printf '  FAIL  %-42s exit %s\n' "$$t" "$$rc"; \
	    printf '%s\n' "$$out" | head -6 | sed 's/^/          /'; fi; \
	}; \
	for t in $(HELP_SAFE); do        run "$$t" ""; done; \
	for t in $(HELP_NUMPY); do       run "$$t" numpy; done; \
	for t in $(HELP_PYSAM); do       run "$$t" pysam; done; \
	for t in $(HELP_MINIWDL); do     run "$$t" WDL; done; \
	for t in $(HELP_TERRA); do       run "$$t" firecloud; done; \
	for t in $(HELP_SH); do          run "$$t" ""; done; \
	printf 'helpsweep: %s ok, %s skipped, %s failed\n' "$$ok" "$$skip" "$$fail"; \
	[ $$fail -eq 0 ]

# ------------------------------------------------------------------- undefmods
# py_compile resolves no names, and a --help sweep exits inside argparse before main() runs.
# Those two facts used to be enough to ship a comparator that raised
# "NameError: name 'os' is not defined" on EVERY real invocation, with docs and a published
# example capture nobody could reproduce. An undefined name is a compile-clean bug, so it needs
# a compile-time answer: attribute access through a stdlib module name the file never imported.
# Static, dependency-free, and it covers every file -- including the ones with no selftest.
undefmods:
	@$(PYTHON) scripts/undef_module_refs.py

# --------------------------------------------------------------------- flake
# A BUG sweep, not a style check: `--select=F` runs only the Pyflakes checks, so no line
# length, no whitespace, no opinions about quotes. It is the other half of undefmods --
# that one answers "attribute access on a module this file never imported"; this answers
# the bare-name and dead-value half: F821 undefined name (a NameError on the first call),
# F841 a value computed and never used (a check that never checks), F401 an import that is
# not there, F541 an f-string with nothing to interpolate.
#
# What it has already found here: an archived as-run driver whose set comprehension read
# `{p for v in ...}` -- NameError the first time an arm pinned a gs:// image -- and three
# "computed the diagnostic, dropped it on the floor" locals.
#
# It SKIPS with a named install command when flake8 is absent, and says so on the tally
# line, because `make setup` installs only runtime requirements. CI installs
# requirements-dev.txt, so CI is where this is enforced: a SKIP here is not a pass there.
flake:
	@py="$(PYTHON)"; \
	if ! $$py -c 'import flake8' 2>/dev/null; then \
	  if [ -x .venv/bin/python ] && .venv/bin/python -c 'import flake8' 2>/dev/null; then \
	    py=.venv/bin/python; \
	  else \
	    echo "flake: SKIP — flake8 not importable by $$py (python -m pip install -r requirements-dev.txt)"; \
	    echo "       CI runs it; a local SKIP is not a pass there."; \
	    exit 0; \
	  fi; \
	fi; \
	out="$$("$$py" -m flake8 --select=F --max-line-length=200 $(PY_FILES) kit/gsvtk-config 2>&1)"; rc=$$?; \
	if [ -n "$$out" ]; then \
	  printf '%s\n' "$$out" | sed 's/^/  HIT   /' | head -30; \
	  echo "flake: FAIL — pyflakes findings above (each one is a crash or a dead check)"; \
	  exit 1; \
	fi; \
	n=$$(printf '%s\n' $(PY_FILES) kit/gsvtk-config | wc -l | tr -d ' '); \
	printf 'flake: %s files, 0 pyflakes findings (interpreter: %s)\n' "$$n" "$$py"

# ----------------------------------------------------------------------- smoke
# --help proves a CLI parses; it does not prove main() runs. These tools are stdlib-only and
# produce a real verdict on empty fixtures, so invoke them and require the line a real pass ends
# with. Exit status is part of the expectation: compare_batch_tables must exit 1 when every
# column is MISSING, because an empty diff that exits 0 reads like a result.
smoke:
	@tmp="$$(mktemp -d)"; mkdir -p "$$tmp/b" "$$tmp/n"; fail=0; \
	echo "smoke: real invocations (--help cannot reach main())"; \
	out="$$($(PYTHON) compare/compare_batch_tables.py --baseline-dir "$$tmp/b" --new-dir "$$tmp/n" 2>&1)"; rc=$$?; \
	if [ $$rc -eq 1 ] && printf '%s' "$$out" | grep -q '== SUMMARY'; then \
	  echo "  ok    compare_batch_tables runs, finds nothing to compare, exits 1"; \
	else fail=1; echo "  FAIL  compare_batch_tables (exit $$rc; want 1 with a '== SUMMARY' line)"; \
	  printf '%s\n' "$$out" | head -5 | sed 's/^/          /'; fi; \
	out="$$($(PYTHON) scripts/undef_module_refs.py "$$tmp/b" 2>&1)"; rc=$$?; \
	if [ $$rc -eq 0 ]; then echo "  ok    undef_module_refs runs against a real path"; \
	else fail=1; echo "  FAIL  undef_module_refs (exit $$rc)"; \
	  printf '%s\n' "$$out" | head -5 | sed 's/^/          /'; fi; \
	rm -rf "$$tmp"; \
	[ $$fail -eq 0 ]

# ------------------------------------------------------------------- selftest
# Real assertions: config-layer behaviour, the two checkers' own --selftest, and clone-backed
# positive controls. They live in scripts/selftest.sh, NOT in a recipe, because make expands
# `$("$@")` inside `$$($("$@") 2>&1)` to nothing -- bash received `out="$( 2>&1)"`, the command
# never ran, and every assertion reported ok whatever it tested (proved with a two-line Makefile:
# `check "..." false` printed ok). The script also runs a canary that fails the gate if its own
# harness ever goes vacuous again; keep new assertions there for that reason.
selftest:
	@$(SHELL) scripts/selftest.sh "$(PYTHON)"

lint: syntax

# --------------------------------------------------------------------- audit
# LAST LINE OF DEFENCE FOR A PUBLIC REPO.
#
# This repo was assembled out of a private working directory. The tools are generic; the
# coordinates they were built against are not -- project ids, workspace names and their buckets,
# registry paths, people, machine-local paths. Every one of those became a <placeholder>, and this
# target is what stops a leftover from shipping.
#
# It used to do that with a blocklist of ONE PERSON'S identifiers, which was two bugs wearing one
# hat: a new user got no protection at all (the list guarded somebody else's name), and the guard
# shipped the very strings it guarded -- badly enough that the only fix was to exempt the Makefile
# from its own scan, so the one file that could never be checked was the one holding every
# identifier. scripts/audit.py derives the personal set from the machine running it instead: the
# resolved configuration, minus everything that resolves identically with no profile (those are
# shipped defaults, public by construction). Nothing personal is carried, so nothing is exempt --
# the Makefile is scanned like every other file, and with the old blocklist still in place the new
# audit caught it sitting in there. See scripts/audit.py for the two tiers: SHAPES (which CI can
# grade, because they are machine-independent) and PERSONAL (which only the publishing machine can).
#
# Two rules that make it actually work, inherited from the earlier version:
#   * Scan what would GO PUBLIC: the tracked set from `git ls-files`. A .gitignore entry cannot hide
#     a hit -- an ignored file is not pushed, and a force-added one becomes tracked and is therefore
#     scanned. CI runs this on a clean checkout, where the tracked set IS the public repo.
#   * NO per-line exemptions. The scan once dropped any line containing `/Users/you/`, so one
#     placeholder path hid every other identifier sharing that line, and `make audit` printed clean
#     for a file holding four of them. `/Users/you/` keeps its exception INSIDE the home-path
#     pattern and nowhere else, and `make selftest` plants a placeholder and a real leak on the same
#     line to prove that exception cannot blind the rest of the scan.
#
# What it cannot do: grade history. A value already pushed lives in the remote's object store no
# matter how clean this prints.

audit:
	@$(PYTHON) scripts/audit.py $(if $(V),--verbose,)

# ----------------------------------------------------------------- clean-work
# Staged input trees are tens of GB and are often HARDLINKS into someone's reference panel
# (terra/stage_inputs.py --link-dir). `du` counts a hardlinked file once, so it understates
# what a delete would free elsewhere and overstates what is yours. Nothing here deletes.
clean-work:
	@w="$$($(GSVTK) get WORK 2>/dev/null)"; \
	if [ -z "$$w" ]; then echo "no work directory resolved; see ./kit/gsvtk-config doctor"; exit 0; fi; \
	echo "work directory: $$w"; \
	if [ ! -d "$$w" ]; then echo "  (does not exist — nothing to clean)"; exit 0; fi; \
	echo; printf '%-14s %10s  %s\n' SUBDIR SIZE FILES; \
	for d in "$$w"/*/; do \
	  [ -d "$$d" ] || continue; \
	  printf '%-14s %10s  %s\n' "$$(basename "$$d")" \
	    "$$(du -sh "$$d" 2>/dev/null | cut -f1)" \
	    "$$(find "$$d" -type f 2>/dev/null | wc -l | tr -d ' ')"; \
	done; \
	echo; \
	echo "total: $$(du -sh "$$w" 2>/dev/null | cut -f1)  in $$(find "$$w" -type f 2>/dev/null | wc -l | tr -d ' ') files"; \
	echo; \
	hl="$$(find "$$w" -type f -links +1 2>/dev/null | wc -l | tr -d ' ')"; \
	echo "files with more than one hardlink: $$hl"; \
	if [ "$$hl" != "0" ]; then \
	  echo "  These are shared with another tree (a reference-panel copy). Deleting them here"; \
	  echo "  only removes this directory entry — it frees almost nothing — and it is NOT a way"; \
	  echo "  to reclaim space from the panel. Check before trusting any du number:"; \
	  echo "      stat -f '%l links  %N' <file>        # macOS"; \
	  echo "      stat -c '%h links  %n' <file>        # Linux"; \
	fi; \
	echo; \
	echo "NOTHING WAS DELETED. To delete, say it yourself:"; \
	echo "      rm -rf $$w"
