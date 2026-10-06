#!/bin/sh
# scripts/selftest.d/declared.sh — one implementation of the declared/reached pair.
#
# WHY THIS EXISTS (docs/gap-ledger.md row C10)
#
# Every selftest phase ends with a line like `rerun: 44 ok, 0 failed`, and when a prerequisite is missing the
# same line becomes `rerun: 0 ok, 0 failed, 1 skipped`. That counted skip is honest about the BAIL and silent
# about the ASSERTIONS: the number that vanishes is 1, while the number that stopped running is 44. A reader
# comparing `0 ok` across two runs sees the same thing whether the phase withheld one weak check or its whole
# body, which is the shape where a broken block and a missing dependency look identical. This helper makes a
# phase say both numbers: how many assertion call sites its own source contains, and how many this run
# reached.
#
# HOW, AND WHY IT CANNOT BE A CONSTANT
#
# The count is derived from the phase's own source with grep, never typed. A hardcoded expectation is the
# same mistake in a different place: when someone deletes six assertions the gate goes quiet instead of
# complaining, which is the exact failure this is meant to catch. Deriving it means deleting a block moves the
# number, and an assertion hidden behind a false condition stays visible as a site while never running, which
# is exactly the pair of cases you want separated.
#
# WHAT `DECLARED` MEANS, PRECISELY
#
# It counts call SITES, not executions. A call site inside a loop is counted once and may execute many times,
# so `reached` can legitimately EXCEED `declared`. Therefore:
#   reached < declared, and nothing skipped   -> FAIL. A block went missing and no skip explains it.
#   reached > declared                        -> reported as re-execution, not a failure.
#   skipped > 0                               -> the pair line is informational; the skip's own message
#                                                names how many sites it withholds.
#
# Each phase declares which of its own functions count as assertion call sites, because they are spelled
# differently in every file (`check`/`expect` in cli.sh, `wantf`/`wantline` in reach.sh and entity.sh,
# `want`/`want_no` in womtool.sh and jarshape.sh). The set has to be exactly the functions whose call sites
# record a verdict — include a helper that does not increment ok/fail and the guard fires on a run that is
# fine; omit one and a disappearance goes unreported. So the callee names are an argument here rather than a
# name list baked into this file, and each phase's declaration line says which they are.
#
# Heredoc bodies are excluded: a here-doc is data handed to another program, not a call site in this file, and
# a python body that says `check the output` would otherwise be counted as a bash assertion. A site that
# itself opens a here-doc is counted and its body skipped. Nested here-docs are not tracked; none exist in
# these phases, and adding one would over-count (a visible wrong number) rather than under-count.
#
# USAGE from a phase:
#   . scripts/selftest.d/declared.sh
#   GSVTK_DECL_FUNCS="want want_no"
#   DECLARED=$(gsvtk_declared_count "$SELF")
#   ...  gsvtk_declared_report "womtool selftest" "$SELF" "$ran" "$skipped" || fail=$((fail + 1))

# Count assertion call sites in $1, where the callee names are in GSVTK_DECL_FUNCS (space-separated).
gsvtk_declared_count() {
    _file=$1
    [ -f "$_file" ] || { echo 0; return 1; }
    awk -v funcs="$GSVTK_DECL_FUNCS" '
        BEGIN {
            n = split(funcs, f, /[ \t]+/)
            alt = ""
            for (i = 1; i <= n; i++) if (f[i] != "") alt = (alt == "" ? f[i] : alt "|" f[i])
            if (alt == "") { print 0; exit }
            pat = "^[ \t]*(" alt ")([ \t]|$)"
        }
        {
            line = $0
            if (inhere != "") {                 # inside a here-doc body: data, not call sites
                if (line ~ ("^[ \t]*" inhere "[ \t]*$")) inhere = ""
                next
            }
            if (line ~ pat) n_sites++
            # a here-doc opener at end of line: capture its tag, then skip until the terminator
            if (match(line, /<<-?[ \t]*[\x27"]?([A-Za-z_][A-Za-z0-9_]*)[\x27"]?[ \t]*$/)) {
                tag = substr(line, RSTART, RLENGTH)
                sub(/^<<-?[ \t]*/, "", tag); gsub(/[\x27"]/, "", tag)
                inhere = tag
            }
        }
        END { print n_sites + 0 }
    ' "$_file"
}

# Print the declared/reached pair for a phase, and fail (return 1) when sites vanished with no skip to
# explain it. $1 = label used by the phase's own summary line, $2 = file to count, $3 = ran, $4 = skipped.
gsvtk_declared_report() {
    _label=$1
    _file=$2
    _ran=$3
    _skipped=${4:-0}
    _decl=$(gsvtk_declared_count "$_file")
    if [ "${_skipped:-0}" -gt 0 ]; then
        printf '%s: declares %s assertion call(s); this run reached %s, so %s did not run behind %s named skip(s)\n' \
            "$_label" "$_decl" "$_ran" "$((_decl - _ran))" "$_skipped"
        return 0
    fi
    if [ "$_ran" -lt "$_decl" ]; then
        printf '  FAIL  %s: %s assertion call site(s) declared, %s ran — a block went missing and nothing skipped\n' \
            "$_label" "$_decl" "$_ran"
        return 1
    fi
    if [ "$_ran" -gt "$_decl" ]; then
        printf '%s: declares %s assertion call(s); this run reached %s (%s call(s) came from a site in a loop)\n' \
            "$_label" "$_decl" "$_ran" "$((_ran - _decl))"
        return 0
    fi
    printf '%s: declares %s assertion call(s); this run reached %s (nothing skipped)\n' \
        "$_label" "$_decl" "$_ran"
}
