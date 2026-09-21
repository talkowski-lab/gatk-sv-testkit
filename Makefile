# gatk-sv-testkit — task entry points. `make` alone prints the list.
#
# Everything here is offline. Only `setup` writes anything, and only into ./.venv.
# Note what is deliberately NOT wired up as a target: no target submits a Terra job,
# creates a method config, copies a bucket, pushes an image or boots a VM. Those are the
# paths that spend money, and they stay typed-out-by-a-human operations (CONTRIBUTING.md).

SHELL      := /bin/bash
.SHELLFLAGS := -o pipefail -c
.DEFAULT_GOAL := help
.PHONY: help setup test syntax helpsweep selftest lint audit clean-work

PYTHON ?= python3
GSVTK  := ./kit/gsvtk-config

# ----------------------------------------------------------------- file sets
SH_FILES := $(wildcard docker/*.sh terra/*.sh checks/*.sh checks/image-check/*.sh \
                   examples/*.sh kit/*.sh)
PY_FILES := $(wildcard kit/*.py terra/*.py checks/*.py compare/*.py replay/*.py \
                   scripts/*.py examples/*.py docs/archive/as-run/*.py)

# Tools that accept --help AND are safe to invoke with no config file, no credentials, no
# data and no network: every one of these only prints usage and exits.
HELP_SAFE    := checks/svshell_contract_check.py checks/svshell_jq_plumbing_scan.py \
                compare/compare_batch_tables.py compare/diff_rd_states.py \
                examples/recompute_het_population.py scripts/fetch_wdl.py kit/config.py
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
	@echo "  setup       create ./.venv and install requirements.txt (offline-safe, idempotent)"
	@echo "  test        the offline gate: syntax + --help on every CLI + config self-tests"
	@echo "              needs no config file, no credentials, no network; SKIPs tools whose"
	@echo "              optional dependency is missing and says which"
	@echo "  syntax      bash -n every .sh, py_compile every .py (this is also 'lint')"
	@echo "  lint        alias of syntax: there is no style checker to install on purpose"
	@echo "  audit       fail if any file that would go public contains an internal identifier"
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
	echo "    source .venv/bin/activate"

# ---------------------------------------------------------------------- test
# Three independent phases, each runnable alone, each printing its own tally. All three
# run even when one fails, then the aggregate exits nonzero.
test:
	@rc=0; \
	$(MAKE) --no-print-directory syntax     || rc=1; \
	$(MAKE) --no-print-directory helpsweep  || rc=1; \
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

# The config layer is what every tool trusts, so it gets real assertions rather than a
# two conventions that make this target honest on a developer machine:
#   * an explicit GSVTK_CONFIG REPLACES the profile chain rather than joining it, so the empty
#     file below really is zero-config even where profiles exist, and the "no default" assertions
#     run here rather than only in CI (each still unsets the exported variables it tests);
#   * the gatk-sv clone location is read from the REAL profile, because asking for it through the
#     empty config would skip these tests on a machine that has the clone right there.
# Nothing here prints resolved config: printing someone's laptop profile is not this gate's job.
#
# CAUTION when editing: a '#' line indented with a TAB inside this backslash-continued recipe
# splits it into separate shells, so $py and check() vanish mid-run. Put commentary above the
# target (a line with no leading TAB ends the recipe) or inside a shell string.
selftest:
	@tmp="$$(mktemp -d)"; : > "$$tmp/empty.env"; \
	py="$(PYTHON)"; \
	if ! $$py -c 'import firecloud' 2>/dev/null && [ -x .venv/bin/python ]; then \
	  py=.venv/bin/python; fi; \
	fail=0; ok=0; skip=0; \
	check() { \
	  desc="$$1"; shift; \
	  out="$$($("$@") 2>&1)"; rc=$$?; \
	  if [ $$rc -eq 0 ]; then ok=$$((ok+1)); printf '  ok    %s\n' "$$desc"; \
	  else fail=$$((fail+1)); printf '  FAIL  %s (exit %s)\n' "$$desc" "$$rc"; \
	    printf '%s\n' "$$out" | head -6 | sed 's/^/          /'; fi; \
	}; \
	echo "selftest: config layer"; \
	check "kit/config.sh loads with no config at all" \
	  env GSVTK_CONFIG="$$tmp/empty.env" bash -c '. kit/config.sh'; \
	check "gsvtk_default returns the fallback for an unconfigured key" \
	  env GSVTK_CONFIG="$$tmp/empty.env" bash -c \
	  '. kit/config.sh; [ "$$(gsvtk_default THIS_KEY_DOES_NOT_EXIST_42 fallback-value)" = "fallback-value" ]'; \
	check "an exported variable beats every profile file (env > profile)" \
	  env GSVTK_CONFIG="$$tmp/empty.env" GSVTK_PROJECT=gate-proj \
	  bash -c '[ "$$($(GSVTK) get PROJECT)" = gate-proj ]'; \
	check "GSVTK_WORK is honoured and gsvtk_work creates subdirs" \
	  env GSVTK_CONFIG="$$tmp/empty.env" GSVTK_WORK="$$tmp/work" bash -c \
	  '. kit/config.sh; p="$$(gsvtk_work runs/x)"; [ "$$p" = "$$tmp/work/runs/x" ] && [ -d "$$p" ]'; \
	if [ ! -f "$$tmp/empty.env" ]; then \
	  echo "  SKIP  the no-default assertions: could not create an empty profile"; \
	  skip=$$((skip+3)); \
	else \
	  out="$$(GSVTK_CONFIG="$$tmp/empty.env" env -u GSVTK_PROJECT -u GSVTK_TERRA_NAMESPACE $(GSVTK) require PROJECT 2>&1)"; rc=$$?; \
	  if [ $$rc -eq 4 ] && printf '%s' "$$out" | grep -q GSVTK_PROJECT; then \
	    ok=$$((ok+1)); printf '  ok    %s\n' "require PROJECT exits 4 and names GSVTK_PROJECT"; \
	  else fail=$$((fail+1)); printf '  FAIL  require PROJECT (exit %s)\n%s\n' "$$rc" "$$out"; fi; \
	  out="$$(env -u GSVTK_PROJECT -u GSVTK_TERRA_NAMESPACE GSVTK_CONFIG="$$tmp/empty.env" $(GSVTK) doctor 2>&1)"; rc=$$?; \
	  if [ $$rc -eq 4 ] && printf '%s' "$$out" | grep -q 'MISS *PROJECT'; then \
	    ok=$$((ok+1)); printf '  ok    %s\n' "doctor exits 4 and reports MISS for each unset required key"; \
	  else fail=$$((fail+1)); printf '  FAIL  doctor (exit %s)\n%s\n' "$$rc" "$$out"; fi; \
	  out="$$(env GSVTK_CONFIG="$$tmp/empty.env" GSVTK_WORK="$$tmp/work" $(GSVTK) require ZONE 2>&1)"; rc=$$?; \
	  if [ $$rc -eq 0 ] && [ "$$out" = "us-central1-a" ]; then \
	    ok=$$((ok+1)); printf '  ok    %s\n' "a documented default still resolves with no profile (ZONE)"; \
	  else fail=$$((fail+1)); printf '  FAIL  default resolution (exit %s): %s\n' "$$rc" "$$out"; fi; \
	fi; \
	echo; echo "selftest: checkers and fetchers, against a local gatk-sv clone if present"; \
	ck="$$($(GSVTK) get GATK_SV_CHECKOUT 2>/dev/null)"; \
	if [ ! -d "$$ck" ]; then \
	  echo "  SKIP  the three clone-backed self-tests: GSVTK_GATK_SV_CHECKOUT is unset or not a"; \
	  echo "        directory. Set it in testkit.env to run them (docs/config.md)."; \
	  skip=$$((skip+3)); \
	else \
	  printf '  (clone: %s)\n' "$$ck"; \
	  if command -v jq >/dev/null 2>&1; then \
	    out="$$($$py checks/svshell_jq_plumbing_scan.py --repo "$$ck" --list-keys 2>&1)"; rc=$$?; \
	    if [ $$rc -eq 0 ]; then ok=$$((ok+1)); printf '  ok    %s\n' "svshell_jq_plumbing_scan ran every jq block clean"; \
	    else ok=$$((ok+1)); printf '  note  svshell_jq_plumbing_scan reported findings (exit %s) — by design\n' "$$rc"; \
	      printf '%s\n' "$$out" | tail -3 | sed 's/^/          /'; \
	      printf '        upstream gatk-sv has pre-existing nulls, so gate a change with\n'; \
	      printf '        --compare-to <ref>; see docs/static-checks.md. Reported, not failed.\n'; fi; \
	  else \
	    echo "  SKIP  jq not on PATH: svshell_jq_plumbing_scan cannot execute the jq blocks"; \
	    skip=$$((skip+1)); \
	  fi; \
	  out="$$($$py checks/svshell_contract_check.py --repo "$$ck" 2>&1)"; rc=$$?; \
	  if [ $$rc -eq 0 ]; then ok=$$((ok+1)); printf '  ok    %s\n' "svshell_contract_check exits clean"; \
	  else ok=$$((ok+1)); printf '  note  svshell_contract_check reported findings (exit %s) — by design\n' "$$rc"; \
	    printf '%s\n' "$$out" | tail -3 | sed 's/^/          /'; \
	    printf '        its output is a list to DIFF against a base ref, not a verdict; see\n'; \
	    printf '        docs/static-checks.md. Reported here, not failed.\n'; fi; \
	  check "fetch_wdl --list materializes a ref's file list offline (git archive)" \
	    $$py scripts/fetch_wdl.py --repo "$$ck" --ref HEAD --list; \
	fi; \
	echo; \
	printf 'selftest: %s ok, %s skipped, %s failed\n' "$$ok" "$$skip" "$$fail"; \
	[ $$fail -eq 0 ]

lint: syntax

# --------------------------------------------------------------------- audit
# LAST LINE OF DEFENCE FOR A PUBLIC REPO.
#
# This repo was assembled out of a private working directory. The tools are generic; the
# coordinates they were built against are not (project ids, workspace names and their
# buckets, registry paths, people, machine-local paths). Every one of those was replaced
# with a <placeholder>, and this target is what stops a leftover from shipping.
#
# Two rules that make it actually work:
#   * Scan the files that would go PUBLIC, i.e. the tracked set from `git ls-files`. A
#     .gitignore entry cannot hide a hit, because an ignored file is not pushed and a
#     force-added one becomes tracked and is therefore scanned. CI runs this from a clean
#     checkout, where the tracked set is the entire public repo by construction.
#   * The only file exempted is this Makefile, because the blocklist below IS those
#     strings. Everything else is fair game, including docs/archive/.
#
# Add a pattern here whenever you redact something by hand. The pattern is the memory.
AUDIT_PATTERNS := \
	'broad-dsde-methods|broad-firecloud-dsde-methods/[^ ]*(TrackB|trackb|-mw|_mw)' \
	'fc-[0-9a-f]{8}' \
	'@broadinstitute\.' \
	'[0-9]{8,}-compute@developer\.gserviceaccount\.com|[0-9]{11,}@developer' \
	'222581509023' \
	'genotypebatch_debug|Work/genotypebatch' \
	'markw|mwalker|vjalili|Vahid|Jalili' \
	'_mw[-_a-z]|[-_]mw[._-]|mw_[a-z]' \
	'help-gatk|terra-dab62c9d' \
	'ryzen9|killer-b' \
	'/tmp/venv-[a-z]*'

audit:
	@if git rev-parse --git-dir >/dev/null 2>&1; then \
	  list="$$(git ls-files)"; mode="git-tracked files ($$(printf '%s\n' $$list | grep -c .))"; \
	else \
	  list="$$(find . -type f -not -path './.git/*' -not -path './work/*' -not -path './.venv*' \
	                 -not -path '*/__pycache__/*' -not -name '*.pyc' \
	                 -not -name 'testkit.env' -not -name 'testkit.local.env' | sed 's|^\./||')"; \
	  mode="find fallback, no git repo ($$(printf '%s\n' $$list | grep -c .) files)"; \
	fi; \
	echo "audit: scanning $$mode for internal identifiers"; \
	hits=0; \
	printf '%s\n' $$list | grep -vx 'Makefile' > "$$.auditlist"; \
	while IFS= read -r f; do \
	  [ -f "$$f" ] || continue; \
	  for pat in $(AUDIT_PATTERNS); do \
	    m="$$(grep -InE "$$pat" "$$f" 2>/dev/null | grep -v '/Users/you[/"]' | head -4)"; \
	    if [ -n "$$m" ]; then \
	      hits=$$((hits+1)); \
	      printf '  HIT   %-46s pattern: %s\n' "$$f" "$$pat"; \
	      printf '%s\n' "$$m" | sed 's/^/          /' | cut -c1-160; \
	    fi; \
	  done; \
	  m="$$(grep -In '/Users/' "$$f" 2>/dev/null | grep -v '/Users/you' | head -4)"; \
	  if [ -n "$$m" ]; then hits=$$((hits+1)); \
	    printf '  HIT   %-46s pattern: an absolute /Users/ path (only /Users/you/ is allowed)\n' "$$f"; \
	    printf '%s\n' "$$m" | sed 's/^/          /' | cut -c1-160; fi; \
	done < "$$.auditlist"; \
	rm -f "$$.auditlist"; \
	if [ $$hits -gt 0 ]; then \
	  echo; \
	  echo "audit: FAIL — $$hits file(s) contain something that must not be published."; \
	  echo "       Replace the value with a <placeholder> and add a row to docs/config.md if"; \
	  echo "       it is a value a user should supply themselves."; \
	  exit 1; \
	fi; \
	echo "audit: clean — no internal identifier in the publishable set."

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
