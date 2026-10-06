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
# WHAT THE PAIR ACTUALLY PROVES, AND WHAT IT CANNOT
#
# It catches a site that EXISTS AND NEVER RUNS — an assertion behind a condition that cannot hold, a block
# orphaned by a refactor, a helper renamed out of the counted set. It does NOT catch a DELETION, because
# deleting a call site lowers `declared` and `reached` together and the pair stays satisfied. Anyone tempted
# to read a green pair as "no assertions were lost" should read this paragraph again; docs/gap-ledger.md C10
# words the same limit, and a claim of coverage here would be the second source telling you what you wanted
# to hear. If deletions must ever be caught, compare this number against the value in the PREVIOUS commit
# (which keeps the no-rot property that a pinned constant would destroy), do not pin a constant.
#
# HOW, AND WHY THE NUMBER CANNOT BE A CONSTANT
#
# The count is derived from the phase's own source, never typed. A hardcoded expectation is the same mistake
# in a different place: when someone deletes six assertions the gate goes quiet instead of complaining.
#
# WHAT `DECLARED` COUNTS, PRECISELY
#   - a call site is a line whose first token is one of the phase's assertion callees, outside a here-doc body
#     and outside a comment. A here-doc body is data handed to another program; a commented-out assertion
#     cannot run, so neither is a site.
#   - `reached` counts EXECUTIONS. One site in a `for` loop over five values is one site and five executions,
#     so `reached` can exceed `declared` legitimately — but ONLY by a surplus the phase DECLARES below. Before
#     an adversarial review this file instead assumed any surplus was fine, and that was a hole worth two
#     CRITICAL findings: a wrong callee list, or assertions arriving from a sourced here-doc file, both looked
#     like a healthy surplus and printed an all-clear that blamed a loop the code cannot see.
#
# USAGE from a phase:
#   . scripts/selftest.d/declared.sh
#   GSVTK_DECL_FUNCS="want want_no"          # required: the callees that record a verdict
#   GSVTK_DECL_SURPLUS=4                     # optional: executions a loop is known to add
#   DECLARED=$(gsvtk_declared_count "$SELF")
#   ...  gsvtk_declared_report "womtool selftest" "$SELF" "$ran" "$skipped" || fail=$((fail + 1))
#
# A here-doc opener whose terminator never appears makes the rest of the file look like a body, so the count
# collapses; the reporter treats `declared == 0` as a failure, and `make syntax` (`bash -n`) rejects an
# unterminated here-doc long before this runs. Nested here-docs are not tracked — none exist in these phases.

# Count assertion call sites in $1. The callee names come in GSVTK_DECL_FUNCS (space-separated).
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
            if (inhere != "") {                                  # inside a here-doc body: data, not sites
                if (line ~ ("^[ \t]*" inhere "[ \t]*$")) inhere = ""
                next
            }
            if (line ~ /^[ \t]*#/) next          # a commented-out assertion cannot run, so it is not a site;
                                                 # and a comment ending in `<<TAG` must not start a body here,
                                                 # which is how a whole file once counted zero sites
            if (line ~ pat) n_sites++
            if (match(line, /<<-?[ \t]*[\x27"]?([A-Za-z_][A-Za-z0-9_]*)[\x27"]?[ \t]*$/)) {
                tag = substr(line, RSTART, RLENGTH)
                sub(/^<<-?[ \t]*/, "", tag); gsub(/[\x27"]/, "", tag)
                inhere = tag
            }
        }
        END { print n_sites + 0 }
    ' "$_file"
}

# Pair the count with what ran, and decide. $1 = the label the phase prints in its own summary, $2 = file to
# count, $3 = ran (ok+fail), $4 = skipped. GSVTK_DECL_SURPLUS declares the executions a loop legitimately
# adds. Returns 1 for anything the reader must not walk past.
gsvtk_declared_report() {
    _label=$1
    _file=$2
    _ran=$3
    _skipped=${4:-0}
    _surplus=${GSVTK_DECL_SURPLUS:-0}
    _decl=$(gsvtk_declared_count "$_file")

    if [ -z "$GSVTK_DECL_FUNCS" ]; then
        printf '  FAIL  %s: declares no assertion callees (GSVTK_DECL_FUNCS is empty), so the declared/reached pair is vacuous\n' "$_label"
        return 1
    fi
    if [ "$_decl" -eq 0 ]; then
        printf '  FAIL  %s: 0 assertion call sites counted for [%s] — the guard has nothing to check, which is not the same as passing\n' \
            "$_label" "$GSVTK_DECL_FUNCS"
        return 1
    fi

    if [ "${_skipped:-0}" -gt 0 ]; then
        printf '%s: declares %s assertion call(s); this run reached %s, so %s did not run behind %s named skip(s)\n' \
            "$_label" "$_decl" "$_ran" "$((_decl - _ran))" "$_skipped"
        return 0
    fi

    if [ "$_ran" -lt "$_decl" ]; then
        printf '  FAIL  %s: %s assertion call site(s) declared, %s ran — %s site(s) became unreachable and nothing skipped\n' \
            "$_label" "$_decl" "$_ran" "$((_decl - _ran))"
        return 1
    fi

    if [ "$_ran" -gt "$_decl" ]; then
        if [ "$_surplus" -gt 0 ] && [ "$((_ran - _decl))" -eq "$_surplus" ]; then
            printf '%s: declares %s assertion call(s); this run reached %s (%s call(s) from a declared loop surplus)\n' \
                "$_label" "$_decl" "$_ran" "$_surplus"
            return 0
        fi
        printf '  FAIL  %s: %s site(s) declared, %s ran — reached exceeds declared by %s, and this phase declares a surplus of %s. Either GSVTK_DECL_FUNCS is missing a callee, or assertions are running from somewhere this counter cannot see (a here-doc-written file that gets sourced, a renamed helper). Not a loop: this file cannot know the cause, so it refuses to guess.\n' \
            "$_label" "$_decl" "$_ran" "$((_ran - _decl))" "$_surplus"
        return 1
    fi

    printf '%s: declares %s assertion call(s); this run reached %s (nothing skipped)\n' \
        "$_label" "$_decl" "$_ran"
}
