# gatk-sv-testkit — task entry points. `make` alone prints the list.
#
# Everything here is offline. Only `setup` writes anything, and only into ./.venv.
# No target submits a Terra job, creates a method config, copies a bucket, pushes an image or
# boots a VM; those spend money and are run by hand (CONTRIBUTING.md).

SHELL      := /bin/bash
.SHELLFLAGS := -o pipefail -c
.DEFAULT_GOAL := help
.PHONY: help setup test pytest verify-commit syntax helpsweep undefmods flake smoke unit selftest lint audit audit-history clean-work

PYTHON ?= python3
#
# Pick the interpreter once: PYTHON if it can import the gate's runtime dependencies, otherwise
# ./.venv/bin/python if that can (announced on stderr). If neither can, PYTHON stays and the phases
# report the missing module. These are import names from requirements.txt; google-auth imports as
# `google.auth` and is omitted, and optional miniwdl is not probed.
PY_NEEDED := numpy pandas firecloud pysam
ifneq ($(origin PYTHON),command line)
override PYTHON := $(shell \
  _imp() { "$$1" -c "import $$2" >/dev/null 2>&1; }; \
  want='$(PYTHON)'; ok=1; \
  for m in $(PY_NEEDED); do _imp "$$want" "$$m" || ok=0; done; \
  if [ "$$ok" = 0 ] && [ -x .venv/bin/python ]; then \
    vok=1; for m in $(PY_NEEDED); do _imp .venv/bin/python "$$m" || vok=0; done; \
    if [ "$$vok" = 1 ]; then \
      printf 'make: PYTHON=%s cannot import the gate dependencies (%s); using ./.venv/bin/python instead. Pin one explicitly with make ... PYTHON=/path/to/python\n' "$$want" "$(PY_NEEDED)" >&2; \
      printf '.venv/bin/python\n'; exit; \
    fi; \
  fi; \
  printf '%s\n' "$$want")
endif
GSVTK  := ./kit/gsvtk-config

# ----------------------------------------------------------------- file sets
# `gsvtk` has no extension, so it is named explicitly.
SH_FILES := gsvtk $(wildcard docker/*.sh terra/*.sh checks/*.sh checks/image-check/*.sh \
                   examples/*.sh kit/*.sh scripts/*.sh scripts/selftest.d/*.sh)
PY_FILES := $(wildcard kit/*.py terra/*.py checks/*.py compare/*.py replay/*.py \
                   scripts/*.py examples/*.py docs/archive/as-run/*.py)

# Tools that accept --help AND are safe to invoke with no config file, no credentials, no
# data and no network: every one of these only prints usage and exits.
HELP_SAFE    := checks/svshell_contract_check.py checks/svshell_jq_plumbing_scan.py \
                checks/terra_entity_check.py \
                compare/artifact_tally.py compare/vcf_body_diff.py \
                compare/gd_calls_diff.py compare/sample_census.py \
                compare/compare_batch_tables.py \
                compare/table_diff.py compare/matrix_diff.py compare/site_set_diff.py \
                compare/lineset_diff.py compare/json_diff.py compare/tar_manifest.py \
                compare/make_fixtures.py \
                examples/recompute_het_population.py scripts/fetch_wdl.py kit/config.py \
                scripts/audit.py scripts/audit_history.py scripts/prod_pins.py scripts/check_doc_flags.py
HELP_NUMPY   := compare/gq_scale_compare.py \
                compare/profile_summarize.py compare/vcf_paired_diff.py
HELP_PYSAM   := compare/pair_level_concordance.py
# checks/wdl_reach.py's --help works without miniwdl but importing the module does not, so it
# is skipped with the rest of this list when miniwdl is absent.
HELP_MINIWDL := replay/build_inputs.py checks/wdl_semantics.py checks/wdl_reach.py \
                checks/wdl_inputs_check.py
HELP_TERRA   := $(wildcard terra/*.py)
# ./gsvtk runs with an empty GSVTK_CONFIG and no credentials: its --help must not need a profile.
HELP_SH      := gsvtk checks/wdl_gate.sh docker/gatk-sv-build.sh terra/batch_fetch_compare.sh \
                examples/replay_reference_run.sh
# NOT in any sweep, on purpose (they are still syntax-checked):
#   docker/remote-build.sh      runs ON THE BUILDER VM; invoking it starts a real build
#   checks/image-check/*.sh     boot a GCE VM and take no --help (they name the image first)
#   checks/image-check/image_ref.sh
#                               a sourced shim, no main; each of the three launchers above tests for
#                               the file before sourcing it, so a missing guard is a refusal
#   examples/run_*.sh           drivers, not CLIs; --help would launch java/bcftools
#   kit/config.sh               a sourced shim, no main; asserted in `make selftest`

# ---------------------------------------------------------------------- help
help:
	@echo "gatk-sv-testkit make targets"
	@echo
	@echo "  setup          create ./.venv and install requirements.txt"
	@echo "                 (add requirements-dev.txt for the full gate: miniwdl, jinja2, flake8, pytest)"
	@echo "  test           the offline gate: syntax, undefmods, flake, helpsweep, smoke, unit,"
	@echo "                 audit and the selftest phases. No config, credentials or network."
	@echo "                 A missing optional dependency prints a named SKIP."
	@echo "  pytest         every test under tests/, including each tool's built-in --selftest"
	@echo "  verify-commit  run the gate against a commit (REV=HEAD) in a throwaway worktree"
	@echo "  syntax         bash -n every .sh, py_compile every .py (alias: lint)"
	@echo "  flake          pyflakes checks only (undefined names, unused values); SKIPs without flake8"
	@echo "  audit          fail if tracked files hold a credential shape or one of this machine's"
	@echo "                 own config values (V=1 shows what it read)"
	@echo "  audit-history  the same scan over every commit and object; PUBLISH=1 fails on history"
	@echo "  clean-work     show what the work directory holds and what would be deleted"
	@echo
	@echo "  Entry point: ./gsvtk --help.  Interpreter: PYTHON=/path/to/python make test"
	@echo "  Start with docs/setup.md, then ./kit/gsvtk-config doctor."

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
# Every phase runs even when one fails, so one run reports all findings; the aggregate then exits
# nonzero.
test:
	@rc=0; \
	$(MAKE) --no-print-directory syntax     || rc=1; \
	$(MAKE) --no-print-directory undefmods  || rc=1; \
	$(MAKE) --no-print-directory flake      || rc=1; \
	$(MAKE) --no-print-directory helpsweep  || rc=1; \
	$(MAKE) --no-print-directory smoke      || rc=1; \
	$(MAKE) --no-print-directory unit       || rc=1; \
	$(MAKE) --no-print-directory audit      || rc=1; \
	$(MAKE) --no-print-directory selftest   || rc=1; \
	echo; \
	if [ $$rc -eq 0 ]; then echo "make test: PASS (offline gate)"; \
	else echo "make test: FAIL — see the FAIL lines above"; fi; \
	exit $$rc

# Run the gate against a commit (REV, default HEAD) in a throwaway detached worktree, so the result
# describes the commit rather than uncommitted edits in this directory.
#
# It does not prove machine independence: the interpreter, credentials, gatk-sv checkout and womtool
# jar stay reachable, and exported GSVTK_CONFIG / GSVTK_GATK_SV_CHECKOUT are inherited (the first
# output line prints them).
#
#   make verify-commit                                              # REV=HEAD, inner target `test`
#   make verify-commit REV=<sha> GSVTK_VERIFY_MAKE=syntax           # one cheap target, one commit
#
# Not a `test` phase: it costs a whole extra gate. Git calls read /dev/null so none can block waiting
# on stdin. Ctrl-C mid-run can leave the worktree behind; `git worktree prune` plus
# `rm -rf $${TMPDIR:-/tmp}/gsvtk-verify.*` clears it.
verify-commit:
	@rev=$${REV:-HEAD}; what=$${GSVTK_VERIFY_MAKE:-test}; \
	case "$$what" in ''|*[!A-Za-z0-9_-]*) echo "  FAIL  GSVTK_VERIFY_MAKE must be ONE make target name (letters, digits, _ and -); got: [$${what:-empty}]"; exit 1;; esac; \
	py='$(PYTHON)'; case "$$py" in /*) : ;; */*) py="$$PWD/$$py" ;; esac; \
	printf 'verify-commit: grading %s with "%s" in a fresh tree, PYTHON=%s\n' "$$rev" "$$what" "$$py"; \
	printf 'verify-commit: config INHERITED from this shell — GSVTK_CONFIG=%s, GSVTK_GATK_SV_CHECKOUT=%s, GSVTK_ENTITY_REF=%s. A fresh tree does not scrub these, so they retarget what is graded; when one reads <unset> the tree runs its own discovery and the phase SKIP lines are the authority on what it found.\n' "$${GSVTK_CONFIG:-<unset>}" "$${GSVTK_GATK_SV_CHECKOUT:-<unset>}" "$${GSVTK_ENTITY_REF:-<default>}"; \
	wt=$$(mktemp -d "$${TMPDIR:-/tmp}/gsvtk-verify.XXXXXX"); rmdir "$$wt"; \
	git worktree add --detach "$$wt" "$$rev" >/dev/null </dev/null || { echo "  FAIL  cannot make a detached worktree at $$rev (git's reason is above, if any)"; exit 1; }; \
	st=0; start=$$(date +%s); \
	$(MAKE) -C "$$wt" --no-print-directory $$what PYTHON="$$py" || st=1; \
	end=$$(date +%s); \
	git worktree remove -f "$$wt" >/dev/null 2>&1 </dev/null || rm -rf "$$wt"; \
	git worktree prune >/dev/null 2>&1 </dev/null; \
	printf 'verify-commit: rev=%s inner=%s rc=%s wall=%ss  (fresh tree = no uncommitted state, NOT a different machine)\n' "$$rev" "$$what" "$$st" "$$((end - start))"; \
	exit $$st

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

# --help on every tool, with an empty profile and a throwaway work directory, so a tool that only
# works when configured fails here. Missing optional dependencies SKIP.
#
# run() dispatches by extension; extension-less `gsvtk` is named so it is exec'd directly (shebang +
# exec bit) instead of falling to the Python arm.
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
	    gsvtk) out="$$(GSVTK_CONFIG="$$tmp/empty.env" GSVTK_WORK="$$tmp/work" "./$$t" --help 2>&1)"; rc=$$? ;; \
	    *.sh)  out="$$(GSVTK_CONFIG="$$tmp/empty.env" GSVTK_WORK="$$tmp/work" bash "$$t" --help 2>&1)"; rc=$$? ;; \
	    *)     out="$$(GSVTK_CONFIG="$$tmp/empty.env" GSVTK_WORK="$$tmp/work" $$py "$$t" --help 2>&1)"; rc=$$? ;; \
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
# py_compile resolves no names and --help exits before main() runs, so this catches attribute access
# through a stdlib module name the file never imported. Static and dependency-free.
undefmods:
	@$(PYTHON) scripts/undef_module_refs.py

# --------------------------------------------------------------------- flake
# A bug sweep, not a style check: `--select=F` runs only the Pyflakes checks (F821 undefined name,
# F841 unused value, F401 unused import, F541 empty f-string). Complements undefmods.
#
# SKIPs with an install hint when flake8 is absent (`make setup` installs only runtime requirements).
# CI installs requirements-dev.txt and enforces it.
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

# ----------------------------------------------------------------------- pytest
# `unit` is the pytest phase of `make test`: tests/ minus the wrapper that re-runs every tool's
# --selftest (the selftest phases already run those). `pytest` runs everything.
PYTEST_OK = $(shell $(PYTHON) -c 'import pytest' >/dev/null 2>&1 && echo 1)
unit:
	@if [ "$(PYTEST_OK)" = 1 ]; then echo "unit: pytest tests/"; \
	  $(PYTHON) -m pytest -q tests --ignore=tests/test_embedded_selftests.py; \
	else echo "  SKIP  unit: pytest is not installed (pip install -r requirements-dev.txt)"; fi

pytest:
	$(PYTHON) -m pytest -q tests

# ----------------------------------------------------------------------- smoke
# --help proves a CLI parses, not that main() runs. These stdlib-only tools run on empty fixtures
# and must print their final line. compare_batch_tables must exit 2 (nothing compared) when every column is MISSING.
smoke:
	@tmp="$$(mktemp -d)"; mkdir -p "$$tmp/b" "$$tmp/n"; fail=0; \
	echo "smoke: real invocations (--help cannot reach main())"; \
	out="$$($(PYTHON) compare/compare_batch_tables.py --baseline-dir "$$tmp/b" --new-dir "$$tmp/n" 2>&1)"; rc=$$?; \
	if [ $$rc -eq 2 ] && printf '%s' "$$out" | grep -q '== SUMMARY'; then \
	  echo "  ok    compare_batch_tables runs, finds nothing to compare, exits 2"; \
	else fail=1; echo "  FAIL  compare_batch_tables (exit $$rc; want 2 with a '== SUMMARY' line)"; \
	  printf '%s\n' "$$out" | head -5 | sed 's/^/          /'; fi; \
	out="$$($(PYTHON) scripts/undef_module_refs.py "$$tmp/b" 2>&1)"; rc=$$?; \
	if [ $$rc -eq 0 ]; then echo "  ok    undef_module_refs runs against a real path"; \
	else fail=1; echo "  FAIL  undef_module_refs (exit $$rc)"; \
	  printf '%s\n' "$$out" | head -5 | sed 's/^/          /'; fi; \
	rm -rf "$$tmp"; \
	[ $$fail -eq 0 ]

# ------------------------------------------------------------------- selftest
# Phases live in scripts, not in recipes: make expands `$("$@")` inside `$$(...)` to nothing, which
# would make every assertion vacuous. scripts/selftest.sh runs a canary for that.
#
# One loop over a named list, so every phase runs and all red phases are reported together.
# Each entry is `name:script`; the script takes the interpreter as $1, prints its own tally and exits
# nonzero on failure (a counted SKIP exits 0). Phases: config = config layer, checkers, probes,
# audits, skill and docs structure; cli = ./gsvtk; rerun = rerunning a batch step; abort = stopping a
# submission; profiles = module profiles; womtool = CI's womtool half; reach = image buckets;
# jarshape = womtool inputs shape; entity = terra launch configs vs the entity table.
#
# No trailing comments on these lines: in a continued assignment a `#` comments out the rest of the
# whole value, silently dropping later phases. scripts/selftest.sh checks every phase is wired.
SELFTEST_PHASES := config:scripts/selftest.sh \
                   cli:scripts/selftest.d/cli.sh \
                   rerun:scripts/selftest.d/rerun.sh \
                   abort:scripts/selftest.d/abort.sh \
                   profiles:scripts/selftest.d/profiles.sh \
                   womtool:scripts/selftest.d/womtool.sh \
                   reach:scripts/selftest.d/reach.sh \
                   jarshape:scripts/selftest.d/jarshape.sh \
                   entity:scripts/selftest.d/entity.sh

selftest:
	@total=$(words $(SELFTEST_PHASES)); rc=0; red=""; nred=0; n=0; \
	for spec in $(SELFTEST_PHASES); do \
	  n=$$((n+1)); name="$${spec%%:*}"; script="$${spec#*:}"; \
	  printf '\n== selftest phase %s/%s: %s (%s)\n' "$$n" "$$total" "$$name" "$$script"; \
	  $(SHELL) "$$script" "$(PYTHON)"; st=$$?; \
	  if [ $$st -eq 0 ]; then printf '  ok    %s\n' "$$name"; \
	  else rc=1; nred=$$((nred+1)); red="$$red $$name"; \
	       printf '  FAIL  %s (exit %s; its tally is above)\n' "$$name" "$$st"; fi; \
	done; \
	echo; \
	if [ $$rc -eq 0 ]; then printf 'selftest phases: %s ran, 0 red\n' "$$n"; \
	else printf 'selftest phases: %s of %s red:%s\n' "$$nred" "$$n" "$$red"; \
	     printf '             every phase ran; fix them all before re-running the gate\n'; fi; \
	exit $$rc

lint: syntax

# --------------------------------------------------------------------- audit
# Fails if the tracked file set (`git ls-files`) holds a credential shape or one of this machine's
# own configured coordinates (project ids, workspaces, buckets, registry paths, local paths).
# scripts/audit.py derives the personal set from the resolved configuration, so nothing personal is
# stored in the repo and no file is exempt. No per-line exemptions: a placeholder on a line must not
# hide a real value on the same line.
#
# It cannot grade history; see `make audit-history`.

audit:
	@$(PYTHON) scripts/audit.py $(if $(V),--verbose,)

# `audit` grades the working tree. This walks every blob and commit message and classifies hits as
# HEAD/STAGED (fail), HISTORY (reachable from a ref; fails with PUBLISH=1) or DANGLING (advisory).
# Run it with PUBLISH=1 before `git push`.
audit-history:
	@$(PYTHON) scripts/audit_history.py $(if $(PUBLISH),--publish,) $(if $(V),--show 200,)

# ----------------------------------------------------------------- clean-work
# Staged inputs may be hardlinks into a reference panel (terra/stage_inputs.py --link-dir), so `du`
# can mislead. Nothing here deletes.
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
