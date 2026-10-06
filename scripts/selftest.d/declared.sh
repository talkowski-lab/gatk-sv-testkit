#!/bin/sh
# scripts/selftest.d/declared.sh — one implementation of the declared/reached pair, counted in SITES.
#
# WHY THIS EXISTS (docs/gap-ledger.md row C10)
#
# Every selftest phase ends with a line like `rerun: 44 ok, 0 failed`, and when a prerequisite is missing the
# same line becomes `rerun: 0 ok, 0 failed, 1 skipped`. That counted skip is honest about the BAIL and silent
# about the ASSERTIONS: the number that vanishes is 1, while the number that stopped running is 44. A reader
# comparing `0 ok` across two runs sees the same thing whether the phase withheld one weak check or its whole
# body, which is the shape where a broken block and a missing dependency look identical. This helper makes a
# phase say both numbers: how many assertion call SITES its own source contains, and how many this run reached.
#
# BOTH HALVES ARE SITES. THE PREVIOUS VERSION COMPARED DIFFERENT UNITS
#
# Declared was a grep over the phase's source; reached was `ok + fail`, which counts EXECUTIONS. Two phases
# whose source is the same length can then disagree, so the units did not match, and the gap was patched with
# a per-phase `GSVTK_DECL_SURPLUS` constant that had to match exactly. That constant was the bug, two ways,
# both measured on this box:
#
#   * It was MODE-DEPENDENT. `jarshape.sh` reached 49 with WOMTOOL_JAR unset and 50 with it set; `womtool.sh`
#     reaches 50 with 2 skipped when there is no jar. One constant cannot describe both runs, so the same
#     commit was red in one environment and green in another — a verdict depending on a file on someone's disk,
#     which is worse than no number at all.
#   * It described a LOOP, not a fact. `rerun.sh` needed GSVTK_DECL_SURPLUS=4 purely because one site sits
#     inside `for s in 06 07 08 09 10`. A number that exists only to describe a loop is not measuring anything,
#     and it silently forbids adding a second loop.
#
# The fix is to count the same unit on both sides. `reached` is now the number of DISTINCT call sites this run
# recorded, collected by `gsvtk_note_site` at each recorder's own call site, so a site executed five times in
# a loop is ONE site on both sides of the comparison, and a mode that runs 50 verdicts instead of 49 differs
# only in WHICH sites it reached — never in what the phase declares. When nothing skipped, the declared set and
# the reached set must be EQUAL, in every mode. GSVTK_DECL_SURPLUS is gone, and a phase that still sets it is
# failed by name: no back-compat path, because a knob that is silently ignored is a knob set wrong.
#
# WHAT THE PAIR ACTUALLY PROVES, AND WHAT IT CANNOT
#
# It catches a site that EXISTS AND NEVER RUNS — an assertion behind a condition that cannot hold, a block
# orphaned by a refactor, a helper renamed out of the counted set — and it names the LINE NUMBERS of those
# sites, which is the whole payoff of counting sites: "a block went missing" becomes an address you can open.
#
# It does NOT catch a DELETION, and this stayed true after the rewrite to sites. A deleted call site is
# neither declared nor recorded, so declared and reached drop together and the pair still prints an all-clear:
# measured by deleting the `run`/`run_absent`/`bytes`/`inproc` calls out of a phase and watching both numbers
# fall. docs/gap-ledger.md C10 words the same limit, and each phase repeats it, because a claim of coverage
# here would be the second source telling you what you wanted to hear. If deletions must ever be caught,
# compare this number against the value in the PREVIOUS commit (which keeps the no-rot property that a pinned
# constant would destroy), do not pin a constant.
#
# WHAT IS NOT ACCOUNTED, SO THAT NOTHING IS SILENT
#
#   * A verdict recorded OUTSIDE the phase's recorder functions — a top-level `if …; then ok=$((ok + 1)); fi`
#     block, which reach.sh and profiles.sh contain — is neither declared nor recorded. That is why the line
#     this file prints states the verdict total beside the site total: if verdicts exceed sites, some verdict
#     lives outside the accounted set and the phase should say where.
#   * A comment line is not a site and cannot open a here-doc body — that is how a whole file once counted
#     zero. A here-doc body is data handed to another program, not a call site.
#   * An empty callee list, a callee list that disagrees with the derived set, a recorder that never calls
#     `gsvtk_note_site`, a declared count of 0, a ledger that cannot be written, or a run that recorded no
#     site while recording verdicts are FAILURES, never passes.
#
# THE TEMP FILE IS THE DATA STRUCTURE
#
# `reached` is a set of call sites, and bash 3.2 — the only bash on a stock macOS, and what these phases must
# run under — has NO associative arrays (`declare -A` is bash 4, and an empty indexed array is an
# unbound-variable error under `set -u` there). So the set lives in a per-run file, one `<file>:<line>` per
# call, and `sort -u` is the set operation. That is also why an unwritable ledger is a loud failure rather
# than a quiet 0: the file IS the mechanism, not a log next to it. A person who wants to look at the set a run
# reached — to see which sites a loop turned into several verdicts, say — can export GSVTK_DECL_KEEP at a
# scratch path before running one phase; the ledger is then written at `<path>.<script>` and truncated on
# entry, so it is a scratch path they own. GSVTK_DECL_SITES itself is NOT honoured from the environment for
# that reason: a child phase inheriting it would truncate its parent's ledger, which this file has measured.
#
# USAGE from a phase:
#   . scripts/selftest.d/declared.sh
#   GSVTK_DECL_FUNCS="$(gsvtk_declared_funset "$SELF" | awk '{print $1}' | tr '\n' ' ')"   # derived
#   gsvtk_declared_init "$TMP"                          # the ledger, inside the phase's own temp dir
#   inside EVERY recorder function, as its first statement:
#       gsvtk_note_site "${BASH_LINENO[0]}"             # ${BASH_LINENO[0]} = the line that CALLED it
#   ...  gsvtk_declared_report "rerun" "$SELF" "$((ok + fail))" "$skipped" || fail=$((fail + 1))
#
# THE CALLEE LIST IS DERIVED, NOT TYPED
#
# `gsvtk_declared_funset` reads the phase's source and prints its verdict-recording entry points: every
# function whose body records a verdict (directly, or by calling another function that does) AND that is
# called from the phase's top-level code. The second clause keeps a printer out of the list: `say_ok` and
# `say_fail` in jarshape.sh record the verdict, but they are only ever called from inside `want`, so a
# `say_fail` line inside `want` would be a "site" that never runs in a green run — a guaranteed false alarm on
# every healthy run. The entry points are the calls the phase itself makes, which is what C10 is about.
# `gsvtk_declared_report` re-derives the set and FAILS if the phase's list disagrees, so the list cannot rot
# when someone adds a recorder; a recorder in the list that never calls `gsvtk_note_site` is failed too,
# because that is the mistake that would otherwise surface as a list of unexplained missing sites.
#
# A here-doc opener whose terminator never appears makes the rest of the file look like a body, so the count
# collapses; the reporter treats `declared == 0` as a failure, and `make syntax` (`bash -n`) rejects an
# unterminated here-doc long before this runs. Nested here-docs are not tracked — none exist in these phases.

# Why the site ledger could not be written, if it could not. Sticky, so the reporter can name the reason
# instead of printing "0 sites reached", which would read like a phase that lost every assertion.
GSVTK_DECL_NOTE_ERR=""
GSVTK_DECL_NOTE_ERR_SHOWN=""
GSVTK_DECL_SITES=""                        # the ledger file; gsvtk_declared_init names it. Deliberately reset
                                           # rather than inherited: a phase that spawns another phase must not
                                           # write two phases' sites into one file (measured — it made the child
                                           # truncate the parent's ledger, and the reporter answered with 54
                                           # sites that ran without being declared). GSVTK_DECL_KEEP below is the
                                           # one supported way to point a ledger somewhere durable.
GSVTK_DECL_OWNED=""                       # 1 when this file made the ledger, so the reporter may remove it
_gsvtk_decl_abs_key=""
_gsvtk_decl_abs_val=""

# Absolute path of a source file named in BASH_SOURCE, cached: it is resolved once per assertion, and a
# subshell per assertion would cost more than the assertion does. The `cd` happens in the subshell, never here.
gsvtk_declared_abs() {
    case "$1" in
        /*) printf '%s\n' "$1"; return 0 ;;
    esac
    if [ "$1" = "$_gsvtk_decl_abs_key" ]; then
        printf '%s\n' "$_gsvtk_decl_abs_val"
        return 0
    fi
    _gsvtk_decl_abs_key=$1
    case "$1" in
        */*) _gsvtk_decl_abs_val="$(cd "${1%/*}" 2>/dev/null && pwd -P)/${1##*/}" ;;
        *)   _gsvtk_decl_abs_val="$(pwd -P)/$1" ;;     # a bare name: CWD, which every phase pins to ROOT
    esac
    printf '%s\n' "$_gsvtk_decl_abs_val"
}

# Name the ledger and empty it. $1 = a directory to put it in — the phase's own temp dir, so the EXIT trap the
# phase already has removes it. With no directory, mktemp one and own it (the reporter then cleans up). A
# ledger that cannot be created or emptied is a LOUD failure here, not a phase that quietly reaches 0 sites.
gsvtk_declared_init() {
    # GSVTK_DECL_KEEP=<path prefix> is a measurement knob for a HUMAN running one phase: the ledger survives at
    # `<prefix>.<script>` after the phase's temp dir is gone, so the reached set can be inspected. The script
    # name is appended because two phases that run in one process tree must not share one ledger.
    if [ -n "${GSVTK_DECL_KEEP:-}" ]; then
        _keep=${BASH_SOURCE[1]:-${BASH_SOURCE[0]:-phase}}       # the CALLING phase, not declared.sh itself
        GSVTK_DECL_SITES="$GSVTK_DECL_KEEP.${_keep##*/}"
    fi
    if [ -z "$GSVTK_DECL_SITES" ]; then
        if [ -n "${1:-}" ] && [ -d "$1" ]; then
            GSVTK_DECL_SITES="$1/gsvtk-decl.sites"
        else
            GSVTK_DECL_SITES="$(mktemp "${TMPDIR:-/tmp}/gsvtk-decl.XXXXXX" 2>/dev/null)" || GSVTK_DECL_SITES=""
            GSVTK_DECL_OWNED=1
        fi
    fi
    if [ -z "$GSVTK_DECL_SITES" ]; then
        GSVTK_DECL_NOTE_ERR="no site ledger could be created"
    elif ! : >> "$GSVTK_DECL_SITES" 2>/dev/null; then
        GSVTK_DECL_NOTE_ERR="cannot append to $GSVTK_DECL_SITES"
    elif ! : > "$GSVTK_DECL_SITES" 2>/dev/null; then
        GSVTK_DECL_NOTE_ERR="cannot empty $GSVTK_DECL_SITES"
    fi
    if [ -n "$GSVTK_DECL_NOTE_ERR" ]; then
        printf '  FAIL  declared/reached: %s — a phase with no site ledger has NO accounting, and no accounting is reported as failure, never as a quiet 0\n' \
            "$GSVTK_DECL_NOTE_ERR"
        return 1
    fi
}

# Record the call site of the assertion helper that is calling this. Feed it ${BASH_LINENO[0]} from inside the
# recorder: inside a function that is the line number, IN THE CALLER, on which the recorder was called — so a
# `for` loop over five values writes the same line five times and `sort -u` collapses it to ONE site, which is
# what makes the pair mode-invariant where an execution count was not. The file is part of the key, so an
# assertion arriving from a file this count cannot see (a here-doc-written script that gets sourced, say) shows
# up as a site nobody declared instead of quietly inflating a total.
gsvtk_note_site() {
    _f=${BASH_SOURCE[1]:-${BASH_SOURCE[0]:-unknown}}
    if [ -z "$GSVTK_DECL_SITES" ] || \
       ! printf '%s:%s\n' "$(gsvtk_declared_abs "$_f")" "${1:-0}" >> "$GSVTK_DECL_SITES" 2>/dev/null; then
        if [ -z "$GSVTK_DECL_NOTE_ERR" ]; then     # the FIRST lost site is the reason; appending every one of
            GSVTK_DECL_NOTE_ERR="no site ledger (first lost call site: line ${1:-0} of $_f)"   # them would bury it
        fi
        if [ -z "$GSVTK_DECL_NOTE_ERR_SHOWN" ]; then
            GSVTK_DECL_NOTE_ERR_SHOWN=1
            printf '  FAIL  declared/reached: cannot write the site ledger (%s) — the pair is blind from here, and a blind pair is a failing pair\n' \
                "$GSVTK_DECL_NOTE_ERR"
        fi
        return 1
    fi
}

# The declared SITES of $1 WITH the extent of the command each one opens: `<abs file>:<start>` then a TAB then
# `<end>`, sorted. The callee names come in GSVTK_DECL_FUNCS. A site is a line whose FIRST TOKEN is a callee,
# outside a here-doc body, outside a comment, and outside the text of another call (a callee named inside
# somebody else`s argument string is data, not a call). The extent is here because bash does not report a
# multi-line call by the line it starts on: when an argument is a double-quoted string carrying a newline — the
# way these phases write long assertion descriptions — ${BASH_LINENO[0]} lands on a line INSIDE that call, and
# which one is not stable (measured: `r "a<nl>b" 0` reports the closing line, `r 'x' "a<nl>b"` reports the
# opening one, a call with two such strings reports the middle line). So a site claims every line its own
# command occupies, and a recorded line counts as reaching the site whose extent contains it. A failure then
# still prints the address a human should open, without pretending the parser is more predictable than it is.
gsvtk_declared_spans() {
    _file=$1
    [ -f "$_file" ] || return 0
    _abs=$(gsvtk_declared_abs "$_file")
    awk -v funcs="$GSVTK_DECL_FUNCS" -v file="$_abs" '
        function heredoc_tag(l,   tag) {          # a here-doc opener at the end of this line, else ""
            if (match(l, /<<-?[ \t]*[\x27"]?([A-Za-z_][A-Za-z0-9_]*)[\x27"]?[ \t]*$/)) {
                tag = substr(l, RSTART, RLENGTH)
                sub(/^<<-?[ \t]*/, "", tag); gsub(/[\x27"]/, "", tag)
                return tag
            }
            return ""
        }
        function cmdend(k,   j, l, q, p, c) {     # the last line of the command that begins on line k
            j = k; q = ""
            while (j <= n && j <= k + 40) {       # the 40 is a fuse, not a limit anyone should reach
                l = L[j]; p = 1
                while (p <= length(l)) {
                    c = substr(l, p, 1)
                    if (q != "") {
                        if (q == "\x22" && c == "\\") { p += 2; continue }     # \" inside a "..." string
                        if (c == q) q = ""
                        p++; continue
                    }
                    if (c == "#") break                                       # the rest of the line is a comment
                    if (c == "\x22" || c == "\x27") { q = c; p++; continue }
                    p++
                }
                if (q != "") { j++; continue }                                # quote still open: it continues
                if (l ~ /\\[ \t]*$/) { j++; continue }                        # a backslash continuation
                return j
            }
            return j - 1
        }
        { L[NR] = $0; n = NR }
        END {
            nf = split(funcs, f, /[ \t]+/); alt = ""
            for (x = 1; x <= nf; x++) if (f[x] != "") alt = (alt == "" ? f[x] : alt "|" f[x])
            if (alt == "") exit
            pat = "^[ \t]*(" alt ")([ \t]|$)"
            i = 1
            while (i <= n) {
                line = L[i]
                if (inhere != "") {                                  # here-doc body: data, never a site
                    if (line ~ ("^[ \t]*" inhere "[ \t]*$")) inhere = ""
                    i++; continue
                }
                if (line ~ /^[ \t]*#/) { i++; continue }             # a commented-out assertion cannot run, so
                                                                     # it is not a site, and a comment ending in
                                                                     # `<<TAG` must not open a body here — that
                                                                     # is how a whole file once counted zero
                tag = heredoc_tag(line)
                if (i <= covered) { i++; continue }                  # inside another call: argument text
                if (line ~ pat) {
                    j = cmdend(i)
                    print file ":" i "\t" j
                    covered = j
                    if (tag != "") inhere = tag
                    i++; continue
                }
                if (tag != "") inhere = tag
                i++
            }
        }
    ' "$_file" | LC_ALL=C sort -u
}

# The declared sites as `<abs file>:<line>`, one per site, sorted for `comm`. The same list as the spans, keyed
# by the line a reader should open.
gsvtk_declared_sites() {
    gsvtk_declared_spans "$1" | awk -F'\t' '{ print $1 }'
}

# How many sites. Never typed by a human, never a constant: when six assertions are deleted this number moves,
# which is exactly the property a pinned expectation would destroy.
gsvtk_declared_count() {
    _n=$(gsvtk_declared_sites "$1" | wc -l | tr -d ' ')
    printf '%s\n' "${_n:-0}"
}

# How many DISTINCT sites this run recorded. The temp file is the set; `sort -u` is the set operation.
gsvtk_reached_sites() {
    if [ -z "$GSVTK_DECL_SITES" ] || [ ! -r "$GSVTK_DECL_SITES" ]; then
        printf '0\n'
        return 1
    fi
    _n=$(LC_ALL=C sort -u "$GSVTK_DECL_SITES" 2>/dev/null | wc -l | tr -d ' ')
    printf '%s\n' "${_n:-0}"
}

# DERIVE the verdict-recording entry points of $1 — see "THE CALLEE LIST IS DERIVED" above. Prints
# `<name> [UNNOTED]` per entry point (UNNOTED when the function never calls gsvtk_note_site), sorted. Two
# rules, both read off the source rather than assumed:
#   records a verdict = its body contains `ok=$((ok + 1))`/`fail=$((fail + 1))`, or calls another function
#                       that records one (run to a fixpoint, so a `wantc -> wantf` alias counts);
#   entry point       = it is ALSO called from code outside every function body, i.e. by the phase itself.
#                       A recorder only ever called from inside another recorder is a printer, and its lines
#                       would be sites that never run in a green run.
gsvtk_declared_funset() {
    _file=$1
    [ -f "$_file" ] || return 0
    awk '
        function strip(l) {                    # drop comments and quoted text: what remains is code
            gsub(/\x27[^\x27]*\x27/, "Q", l)                      # single-quoted (no escapes inside those)
            gsub(/"[^"]*"/, "Q", l)                               # double-quoted (no nested quotes in these files)
            sub(/#.*/, "", l)
            return l
        }
        function heredoc_tag(l,   tag) {       # a here-doc opener at end of line, else ""
            if (match(l, /<<-?[ \t]*[\x27"]?([A-Za-z_][A-Za-z0-9_]*)[\x27"]?[ \t]*$/)) {
                tag = substr(l, RSTART, RLENGTH)
                sub(/^<<-?[ \t]*/, "", tag); gsub(/[\x27"]/, "", tag)
                return tag
            }
            return ""
        }
        {
            line = $0
            if (inhere != "") {                                     # here-doc body: neither code nor a site
                if (line ~ ("^[ \t]*" inhere "[ \t]*$")) inhere = ""
                if (cur != "") body[cur, ++nline[cur]] = ""
                next
            }
            if (line ~ /^[ \t]*#/) {                                 # a comment can be neither code nor opener
                if (cur != "") body[cur, ++nline[cur]] = ""
                next
            }
            code = strip(line)
            if (cur == "") {
                if (match(code, /^[ \t]*[A-Za-z_][A-Za-z0-9_]*[ \t]*\([ \t]*\)[ \t]*\{/)) {
                    rest = substr(code, RSTART + RLENGTH)                    # a `f() { one; }` body lives HERE,
                    cur = code; sub(/^[ \t]*/, "", cur); sub(/\(.*/, "", cur)  # so read it before gsub moves RSTART
                    depth = gsub(/\{/, "{", code) - gsub(/\}/, "}", code)   # depth is CUMULATIVE from here: a
                    body[cur, ++nline[cur]] = ";" rest                      # body line with no braces does not
                    if (depth <= 0) cur = ""                                # close the function
                    next
                }
                topline[++topline_count] = code                       # top level, kept line by line so the
                tag = heredoc_tag(line); if (tag != "") inhere = tag   # entry-point rule below is the SAME rule
                next                                                  # gsvtk_declared_sites uses. The here-doc TAG
                                                                      # is read from the RAW line: strip() turns
                                                                      # a quoted tag into Q, which would then
                                                                      # swallow the rest of the file as a body
            }
            body[cur, ++nline[cur]] = code
            tag = heredoc_tag(line); if (tag != "") { inhere = tag; next }
            depth += gsub(/\{/, "{", code) - gsub(/\}/, "}", code)
            if (depth <= 0) cur = ""
        }
        END {
            for (f1 in nline) fname[f1] = f1
            for (f1 in fname) {                                      # direct verdict recorders first
                for (i = 1; i <= nline[f1]; i++) {
                    if (body[f1, i] ~ /(^|[;&|])[ \t]*(ok|fail)=\$\(\([ \t]*(ok|fail)[ \t]*[+-][ \t]*1[ \t]*\)\)/) {
                        rec[f1] = 1
                    }
                    if (body[f1, i] ~ /gsvtk_note_site/) noted[f1] = 1
                }
            }
            for (pass = 0; pass < 32; pass++) {                      # then the call graph, to a fixpoint
                changed = 0
                for (f1 in fname) {
                    if (rec[f1]) continue
                    for (i = 1; i <= nline[f1] && !rec[f1]; i++) {
                        for (g in fname) {
                            if (!rec[g] || g == f1) continue
                            if (body[f1, i] ~ ("(^|[;&|])[ \t]*" g "([ \t]|$)")) { rec[f1] = 1; changed = 1; break }
                        }
                    }
                }
                if (!changed) break
            }
            for (f1 in fname) {                                      # entry points: rec, and called by the phase
                if (!rec[f1]) continue
                for (i = 1; i <= topline_count; i++) {               # the SAME rule gsvtk_declared_sites uses:
                    if (topline[i] ~ ("(^|[;&|])[ \t]*" f1 "([ \t]|$)")) {   # the callee is the first token here
                        printf "%s %s\n", f1, (noted[f1] ? "" : "UNNOTED")
                        break
                    }
                }
            }
        }
    ' "$_file" | LC_ALL=C sort
}

# Pair the declared sites with the reached sites, and decide. $1 = the label the phase prints in its own
# summary, $2 = the file to count, $3 = verdicts recorded (ok+fail), $4 = named skips. Returns 1 for anything
# the reader must not walk past. When a phase NAMED its skips it is telling the truth about a partial run, so
# the pair prints the addresses of the sites that did not run and passes; when nothing skipped, the declared
# set and the reached set must be EQUAL — the sets, not merely two equal numbers, because two different sites
# can add up to the same count.
gsvtk_declared_report() {
    _label=$1
    _file=$2
    _ran=${3:-0}
    _skipped=${4:-0}

    if [ -n "${GSVTK_DECL_SURPLUS+set}" ]; then
        printf '  FAIL  %s: GSVTK_DECL_SURPLUS is set (to "%s") and is OBSOLETE. Both halves of this pair now count distinct call SITES, so a loop adds nothing to either side and a surplus constant describes nothing. Delete the line: there is no back-compat path, because a knob this file silently ignored would be a knob set wrong.\n' \
            "$_label" "$GSVTK_DECL_SURPLUS"
        return 1
    fi
    if [ -z "$GSVTK_DECL_FUNCS" ]; then
        printf '  FAIL  %s: declares no assertion callees (GSVTK_DECL_FUNCS is empty), so the declared/reached pair is vacuous\n' "$_label"
        return 1
    fi
    if [ -n "$GSVTK_DECL_NOTE_ERR" ]; then
        printf '  FAIL  %s: the site ledger is broken (%s) — reached is unknowable in this run, and unknowable is reported as failure, never as 0-and-quiet\n' \
            "$_label" "$GSVTK_DECL_NOTE_ERR"
        return 1
    fi
    if [ -z "$GSVTK_DECL_SITES" ] || [ ! -r "$GSVTK_DECL_SITES" ]; then
        printf '  FAIL  %s: no site ledger (GSVTK_DECL_SITES="%s") — gsvtk_declared_init never ran, or its file is gone, so nothing was accounted\n' \
            "$_label" "${GSVTK_DECL_SITES:-unset}"
        return 1
    fi

    # The callee list, checked against the source instead of trusted. A phase can still type it (bash 3.2 has
    # no associative arrays to hold a derived set, and the derivation costs an awk pass); what it cannot do is
    # type it WRONG.
    _derived=$(gsvtk_declared_funset "$_file" | awk '{print $1}')
    if [ "$(printf '%s\n' "$_derived" | grep -c .)" -eq 0 ]; then
        printf '  FAIL  %s: %s contains no verdict-recording function called from its own top level, so this pair is comparing an empty set — that is a defect in the pair, not a clean phase\n' \
            "$_label" "$_file"
        return 1
    fi
    _want=$(printf '%s\n' $GSVTK_DECL_FUNCS | LC_ALL=C sort -u)
    _typed_only=$(LC_ALL=C comm -23 <(printf '%s\n' "$_want" | sed '/^$/d') \
                            <(printf '%s\n' "$_derived" | sed '/^$/d'))   # typed, derives nothing
    _derived_only=$(LC_ALL=C comm -13 <(printf '%s\n' "$_want" | sed '/^$/d') \
                            <(printf '%s\n' "$_derived" | sed '/^$/d'))   # derived, not typed
    if [ -n "$_typed_only" ] || [ -n "$_derived_only" ]; then
        printf '  FAIL  %s: GSVTK_DECL_FUNCS=[%s] does not match the recorders derived from %s: derived [%s].%s%s\n' \
            "$_label" "$GSVTK_DECL_FUNCS" "$(basename "$_file")" "$(printf '%s\n' "$_derived" | tr '\n' ' ')" \
            "${_typed_only:+ On the list but deriving no verdict there (a printer, a rename, a typo): $(printf '%s ' "$_typed_only").}" \
            "${_derived_only:+ Derived from the file but MISSING from the list, so its call sites go uncounted: $(printf '%s ' "$_derived_only").}"
        printf '        Derive the list instead of editing it: gsvtk_declared_funset %s\n' "$_file"
        return 1
    fi
    _unnoted=$(gsvtk_declared_funset "$_file" | awk '$2 == "UNNOTED" { printf "%s ", $1 }')
    if [ -n "$_unnoted" ]; then
        printf '  FAIL  %s: %s records verdicts in %s, and that name is in GSVTK_DECL_FUNCS, but the function never calls gsvtk_note_site. Every call site inside it is declared and none can ever be reached, which would read as a phase that lost all its assertions at once. Add `gsvtk_note_site "${BASH_LINENO[0]}"` as the first statement of it.\n' \
            "$_label" "$(basename "$_file")" "$(printf '%s ' "$_unnoted")"
        return 1
    fi

    _decl_spans="${GSVTK_DECL_SITES}.spans"
    _decl_list="${GSVTK_DECL_SITES}.declared"
    _reach_list="${GSVTK_DECL_SITES}.reached"
    _undecl_list="${GSVTK_DECL_SITES}.undeclared"
    if ! gsvtk_declared_spans "$_file" > "$_decl_spans" 2>/dev/null; then
        printf '  FAIL  %s: could not count the declared sites in %s\n' "$_label" "$_file"
        return 1
    fi
    if ! LC_ALL=C sort -u "$GSVTK_DECL_SITES" > "${_reach_list}.raw" 2>/dev/null; then
        printf '  FAIL  %s: could not read the site ledger %s to compare it\n' "$_label" "$GSVTK_DECL_SITES"
        return 1
    fi
    # Project each recorded line onto the site whose command extent contains it (see gsvtk_declared_spans): a
    # call whose argument string carries a newline is reported by bash from inside the call, and the site line
    # stays the address. Entries that fall inside no site are kept, because they are the loud case: a call the
    # callee list does not know about, or an assertion arriving from another file.
    awk -F'\t' -v mf="${_reach_list}.mapped" -v uf="$_undecl_list" '
        NR == FNR { n++; sp[n] = $1; pth[n] = $1; sub(/:[0-9]+$/, "", pth[n])          # the span file is
                    st[n] = $1; sub(/^.*:/, "", st[n]); st[n] += 0                     # `<file>:<start>` then a
                    en[n] = $2 + 0                                                     # TAB then `<end>`
                    next }
        {
            ent = $0
            if (ent == "") next
            p = ent; sub(/:[0-9]+$/, "", p)
            ln = ent; sub(/^.*:/, "", ln); ln += 0
            hit = ""
            for (k = 1; k <= n; k++) {
                if (pth[k] == p && ln >= st[k] && ln <= en[k]) { print sp[k] > mf; hit = 1; break }
            }
            if (!hit) print ent > uf
        }
    ' "$_decl_spans" "${_reach_list}.raw"
    LC_ALL=C sort -u "${_reach_list}.mapped" > "$_reach_list" 2>/dev/null || : > "$_reach_list"
    awk -F'\t' '{ print $1 }' "$_decl_spans" | LC_ALL=C sort -u > "$_decl_list" 2>/dev/null
    _decl=$(wc -l < "$_decl_list" | tr -d ' ')
    _reach=$(wc -l < "$_reach_list" | tr -d ' ')
    if [ "${_decl:-0}" -eq 0 ]; then
        printf '  FAIL  %s: 0 assertion call sites counted for [%s] — the guard has nothing to check, which is not the same as passing\n' \
            "$_label" "$GSVTK_DECL_FUNCS"
        return 1
    fi
    _never=$(LC_ALL=C comm -23 "$_decl_list" "$_reach_list")
    _undeclared=""
    [ -s "$_undecl_list" ] && _undeclared=$(LC_ALL=C sort -u "$_undecl_list")
    if [ -n "$GSVTK_DECL_OWNED" ]; then
        rm -f "$GSVTK_DECL_SITES" 2>/dev/null
        GSVTK_DECL_OWNED=""
    fi
    rm -f "$_decl_list" "$_decl_spans" "$_reach_list" "${_reach_list}.raw" "${_reach_list}.mapped" \
          "$_undecl_list" 2>/dev/null

    if [ "${_skipped:-0}" -gt 0 ]; then
        printf '%s: declares %s assertion call site(s); this run reached %s, so %s did not run behind %s named skip(s) — %s verdict(s) recorded\n' \
            "$_label" "$_decl" "$_reach" "$((_decl - _reach))" "$_skipped" "$_ran"
        if [ -n "$_never" ]; then
            printf '        sites that did not run, by line in %s: %s\n' "$(basename "$_file")" \
                "$(printf '%s\n' "$_never" | awk -F: '{ printf "%s ", $NF }')"
        fi
        return 0
    fi

    if [ -n "$_never" ] || [ -n "$_undeclared" ]; then
        if [ -n "$_never" ]; then
            printf '  FAIL  %s: %s site(s) declared, %s reached — these %s site(s) exist in %s and never ran, and nothing skipped:\n' \
                "$_label" "$_decl" "$_reach" "$(printf '%s\n' "$_never" | wc -l | tr -d ' ')" "$(basename "$_file")"
            printf '%s\n' "$_never" | while read -r _s; do
                [ -n "$_s" ] || continue
                printf '        line %s: %s\n' "${_s##*:}" "$(sed -n "${_s##*:}p" "$_file" | sed 's/^[ \t]*//' | cut -c1-92)"
            done
        fi
        if [ -n "$_undeclared" ]; then
            printf '  FAIL  %s: %s site(s) ran that this count does not declare: %s. Either GSVTK_DECL_FUNCS is wrong, or the call is written so that the line does not start with the callee, or an assertion arrives from outside %s.\n' \
                "$_label" "$(printf '%s\n' "$_undeclared" | wc -l | tr -d ' ')" \
                "$(printf '%s\n' "$_undeclared" | sed "s|$_file:|line |" | tr '\n' ' ')" "$(basename "$_file")"
        fi
        printf '        This pair requires the declared set and the reached set to be EQUAL, in every mode, whenever nothing skipped.\n'
        return 1
    fi

    printf '%s: declares %s assertion call site(s); this run reached %s distinct site(s) — %s verdict(s) recorded, nothing skipped\n' \
        "$_label" "$_decl" "$_reach" "$_ran"
}
