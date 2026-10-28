#!/bin/sh
# scripts/selftest.d/declared.sh — the declared/reached pair for selftest phases, counted in call SITES.
#
# Each phase reports how many assertion call sites its source contains and how many distinct sites this run
# reached. Both sides count sites, not executions, so a loop changes neither number, and the two sets must be
# equal whenever nothing skipped. A site that never ran is reported by line number.
#
# A phase declares the block each of its skips can withhold; only sites inside a region whose skip fired may
# go unreached:
#
#   gsvtk_guard_sites <id>      first statement INSIDE the block whose assertions a skip can withhold
#   gsvtk_guard_sites_end <id>  last statement inside that same block
#   gsvtk_guard_to_end <id>     instead of the pair, for a BAIL: this line to the end of the file
#   gsvtk_note_skip <id>        in the branch that prints the SKIP, beside the `skipped` bump
#
# Region extents are read from the source, never typed. The report fails when an unreached site lies outside
# every fired region, when a site inside a fired region ran, when gsvtk_note_skip names an unknown id, when more
# regions fired than the phase counted skips, or when markers do not pair up.
#
# Limits:
#   * Inside a region that really skipped, a hidden site and a withheld site look the same, so a whole-phase
#     bail hides everything behind it. Run the gate where the prerequisites exist.
#   * It does NOT catch a DELETION: a deleted call site drops out of both sets. To catch deletions, compare the
#     declared count against the previous commit rather than pinning a constant.
#   * A verdict recorded outside the recorder functions (a top-level `ok=$((ok + 1))`) is not a site, which is
#     why the report prints the verdict total beside the site total.
#   * Comment lines and here-doc bodies are never sites, and a comment never opens a here-doc.
#   * An empty or wrong callee list, a recorder that never calls gsvtk_note_site, 0 declared sites, or an
#     unwritable ledger is a failure.
#
# The reached set is a per-run file (one `<file>:<line>` per call, `sort -u` as the set operation) because
# bash 3.2 has no associative arrays. Export GSVTK_DECL_KEEP=<prefix> to keep a phase's ledger at
# `<prefix>.<script>` for inspection. GSVTK_DECL_SITES is not read from the environment, so a child phase
# cannot truncate its parent's ledger.
#
# USAGE from a phase:
#   . scripts/selftest.d/declared.sh
#   GSVTK_DECL_FUNCS="$(gsvtk_declared_funset "$SELF" | awk '{print $1}' | tr '\n' ' ')"   # derived
#   gsvtk_declared_init "$TMP"                          # the ledger, inside the phase's own temp dir
#   inside EVERY recorder function, as its first statement:
#       gsvtk_note_site "${BASH_LINENO[0]}"             # ${BASH_LINENO[0]} = the line that CALLED it
#   around a block a skip can withhold:
#       gsvtk_guard_sites "<id>" … gsvtk_guard_sites_end "<id>"      # or gsvtk_guard_to_end before a bail
#       gsvtk_note_skip "<id>"                                       # in the SKIP branch
#   ...  gsvtk_declared_report "rerun" "$SELF" "$((ok + fail))" "$skipped" || fail=$((fail + 1))
#
#
# The callee list comes from gsvtk_declared_funset: functions that record a verdict (directly or through
# another recorder) AND are called from the phase's top level, so a printer called only from inside a recorder
# is excluded. gsvtk_declared_report re-derives the list and fails if the phase's copy disagrees.
#
# An unterminated here-doc makes the rest of the file look like a body; `declared == 0` fails and `bash -n`
# rejects it anyway. Nested here-docs are not tracked.

# Why the site ledger could not be written. Sticky, so the reporter can name the reason.
GSVTK_DECL_NOTE_ERR=""
GSVTK_DECL_NOTE_ERR_SHOWN=""
GSVTK_DECL_SITES=""                        # the ledger file; set by gsvtk_declared_init, never inherited
GSVTK_DECL_OWNED=""                       # 1 when this file made the ledger, so the reporter may remove it
_gsvtk_decl_abs_key=""
_gsvtk_decl_abs_val=""

# Absolute path of a BASH_SOURCE entry, cached to avoid a subshell per assertion.
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

# Name and empty the ledger. $1 = a directory for it (the phase's temp dir, cleaned by its EXIT trap); without
# one, mktemp and own it. A ledger that cannot be created is a loud failure.
gsvtk_declared_init() {
    # GSVTK_DECL_KEEP=<prefix>: keep the ledger at <prefix>.<script> for inspection.
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
    elif ! { : > "$GSVTK_DECL_SITES.skips" && : > "$GSVTK_DECL_SITES.guards"; } 2>/dev/null; then
        # Side ledgers: regions this run withheld (.skips) and regions whose block it entered (.guards).
        GSVTK_DECL_NOTE_ERR="cannot write the side ledgers $GSVTK_DECL_SITES.skips / .guards"
    fi
    if [ -n "$GSVTK_DECL_NOTE_ERR" ]; then
        printf '  FAIL  declared/reached: %s — a phase with no site ledger has NO accounting, and no accounting is reported as failure, never as a quiet 0\n' \
            "$GSVTK_DECL_NOTE_ERR"
        return 1
    fi
}

# Record the call site of the calling recorder; pass ${BASH_LINENO[0]}. A loop writes the same line repeatedly
# and `sort -u` collapses it to one site. The file is part of the key, so a site from another file shows up as
# undeclared.
gsvtk_note_site() {
    _f=${BASH_SOURCE[1]:-${BASH_SOURCE[0]:-unknown}}
    if [ -z "$GSVTK_DECL_SITES" ] || \
       ! printf '%s:%s\n' "$(gsvtk_declared_abs "$_f")" "${1:-0}" >> "$GSVTK_DECL_SITES" 2>/dev/null; then
        if [ -z "$GSVTK_DECL_NOTE_ERR" ]; then     # keep the first lost site as the reason
            GSVTK_DECL_NOTE_ERR="no site ledger (first lost call site: line ${1:-0} of $_f)"
        fi
        if [ -z "$GSVTK_DECL_NOTE_ERR_SHOWN" ]; then
            GSVTK_DECL_NOTE_ERR_SHOWN=1
            printf '  FAIL  declared/reached: cannot write the site ledger (%s) — the pair is blind from here, and a blind pair is a failing pair\n' \
                "$GSVTK_DECL_NOTE_ERR"
        fi
        return 1
    fi
}

# Append a region id to a side ledger, read later by gsvtk_declared_report.
_gsvtk_decl_note_id() {                       # $1 = side ledger (skips|guards), $2 = region id
    if [ -z "$GSVTK_DECL_SITES" ] || \
       ! printf '%s\n' "$2" >> "$GSVTK_DECL_SITES.$1" 2>/dev/null; then
        if [ -z "$GSVTK_DECL_NOTE_ERR" ]; then
            GSVTK_DECL_NOTE_ERR="no site ledger (cannot record skip region \"$2\" as $1)"
        fi
        if [ -z "$GSVTK_DECL_NOTE_ERR_SHOWN" ]; then
            GSVTK_DECL_NOTE_ERR_SHOWN=1
            printf '  FAIL  declared/reached: cannot record the skip region "%s" as %s — a guard that cannot tell WHICH block a skip withheld is a failing guard, never a quiet pass\n' "$2" "$1"
        fi
        return 1
    fi
}

# Open a skip region: the first statement inside a block a named skip can withhold. Records that the block ran;
# the extent is read from the source. Name the id after the missing prerequisite; it is printed.
gsvtk_guard_sites() {
    if [ -z "${1:-}" ]; then
        printf '  FAIL  declared/reached: gsvtk_guard_sites needs the id of the region it opens\n'
        return 1
    fi
    _gsvtk_decl_note_id guards "$1"
}

# Close region $1. Records nothing; it only marks the extent in the source.
gsvtk_guard_sites_end() {
    if [ -z "${1:-}" ]; then
        printf '  FAIL  declared/reached: gsvtk_guard_sites_end needs the id of the region it closes\n'
        return 1
    fi
    return 0
}

# Bail form of gsvtk_guard_sites: the region is this line to the end of the file. Place it right before the
# `if` that may print a SKIP and exit. Records nothing at runtime.
gsvtk_guard_to_end() {
    if [ -z "${1:-}" ]; then
        printf '  FAIL  declared/reached: gsvtk_guard_to_end needs the id of the region it opens\n'
        return 1
    fi
    return 0
}

# Name the region this run withheld, in the SKIP branch. The id must be a region this file opens. A skip that
# withholds no call site needs no call.
gsvtk_note_skip() {
    if [ -z "${1:-}" ]; then
        printf '  FAIL  declared/reached: gsvtk_note_skip needs the id of the region it withholds\n'
        return 1
    fi
    _gsvtk_decl_note_id skips "$1"
}

# Declared sites of $1 with their extents: `<abs file>:<start>` TAB `<end>`, sorted. A site is a line whose first
# token is a callee from GSVTK_DECL_FUNCS, outside here-doc bodies, comments and other calls' arguments. The
# extent is needed because bash reports a multi-line call (a quoted argument containing a newline) by an
# unpredictable line inside it, so a recorded line counts for the site whose extent contains it.
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
                if (line ~ /^[ \t]*#/) { i++; continue }             # a comment is never a site and never opens a here-doc
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

# The declared sites as `<abs file>:<line>`, sorted.
gsvtk_declared_sites() {
    gsvtk_declared_spans "$1" | awk -F'\t' '{ print $1 }'
}

# How many sites. Derived from the source, never a pinned constant.
gsvtk_declared_count() {
    _n=$(gsvtk_declared_sites "$1" | wc -l | tr -d ' ')
    printf '%s\n' "${_n:-0}"
}

# Skip regions of $1 as `<id>TAB<start>TAB<end>TAB<kind>` (kind = block | tail), sorted, plus one
# `!ERROR<TAB>message` line per marker that does not form a region (duplicate, unclosed, unopened, or a block
# that also claims the rest of the file). Markers are read like call sites: not in comments or here-docs.
gsvtk_declared_regions() {
    _file=$1
    [ -f "$_file" ] || return 0
    awk '
        function heredoc_tag(l,   tag) {           # the same rule gsvtk_declared_spans uses
            if (match(l, /<<-?[ \t]*[\x27"]?([A-Za-z_][A-Za-z0-9_]*)[\x27"]?[ \t]*$/)) {
                tag = substr(l, RSTART, RLENGTH)
                sub(/^<<-?[ \t]*/, "", tag); gsub(/[\x27"]/, "", tag)
                return tag
            }
            return ""
        }
        function marker_id(l,   parts) {
            split(l, parts, " ")                  # id = first token after the marker call (" " split: leading
            gsub(/[\x27"]/, "", parts[2])          # blanks produce no empty field. NOT a passed-in regex: awk
            return parts[2]                        # reads a bare /re/ argument as $0 ~ /re/ and passes 0.
        }                                          # No apostrophes in this program: it is one single-quoted shell string.
        { L[NR] = $0; n = NR }
        END {
            i = 1
            while (i <= n) {
                line = L[i]
                if (inhere != "") {                                  # here-doc body: data, never a marker
                    if (line ~ ("^[ \t]*" inhere "[ \t]*$")) inhere = ""
                    i++; continue
                }
                if (line ~ /^[ \t]*#/) { i++; continue }              # a commented-out marker marks nothing
                tag = heredoc_tag(line)
                wasm = 0                                            # a marker with a bad or empty id is still a marker, and an error
                if (line ~ /^[ \t]*gsvtk_guard_sites_end([ \t]|$)/) {
                    id = marker_id(line); k = "end"; wasm = 1
                } else if (line ~ /^[ \t]*gsvtk_guard_to_end([ \t]|$)/) {
                    id = marker_id(line); k = "tail"; wasm = 1
                } else if (line ~ /^[ \t]*gsvtk_guard_sites([ \t]|$)/) {
                    id = marker_id(line); k = "begin"; wasm = 1
                } else id = ""
                if (wasm && id !~ /^[A-Za-z0-9][A-Za-z0-9_.:+\/-]*$/) { noarg[++noarg_n] = i; id = "" }
                if (id != "") {
                    if (k == "begin") { if (b[id] != "") twice[id] = twice[id] " gsvtk_guard_sites"; b[id] = i }
                    else if (k == "end") { if (e[id] != "") twice[id] = twice[id] " gsvtk_guard_sites_end"; e[id] = i }
                    else { if (t[id] != "") twice[id] = twice[id] " gsvtk_guard_to_end"; t[id] = i }
                    seen[id] = 1
                }
                if (tag != "") inhere = tag
                i++
            }
            for (_x in noarg) {
                printf "!ERROR\tline %s is a guard marker whose id is missing or is not [A-Za-z0-9][A-Za-z0-9_.:+/-]*, so it names no region\n", noarg[_x]
            }
            for (id in seen) {
                if (twice[id] != "") {
                    printf "!ERROR\t\"%s\":%s appears more than once, so its extent is not one range\n", id, twice[id]
                } else if (b[id] != "" && t[id] != "") {
                    printf "!ERROR\t\"%s\" opens a block with gsvtk_guard_sites AND claims the rest of the file with gsvtk_guard_to_end\n", id
                } else if (t[id] != "") {
                    printf "%s\t%s\t%s\ttail\n", id, t[id], n
                } else if (b[id] == "") {
                    printf "!ERROR\t\"%s\" is closed by gsvtk_guard_sites_end but nothing opens it (gsvtk_guard_sites \"%s\")\n", id, id
                } else if (e[id] == "") {
                    printf "!ERROR\t\"%s\" is opened by gsvtk_guard_sites but never closed (gsvtk_guard_sites_end \"%s\", or gsvtk_guard_to_end for a bail)\n", id, id
                } else if (e[id] < b[id]) {
                    printf "!ERROR\t\"%s\" closes on line %s, before it opens on line %s\n", id, e[id], b[id]
                } else {
                    printf "%s\t%s\t%s\tblock\n", id, b[id], e[id]
                }
            }
        }
    ' "$_file" | LC_ALL=C sort
}

# How many distinct sites this run recorded.
gsvtk_reached_sites() {
    if [ -z "$GSVTK_DECL_SITES" ] || [ ! -r "$GSVTK_DECL_SITES" ]; then
        printf '0\n'
        return 1
    fi
    _n=$(LC_ALL=C sort -u "$GSVTK_DECL_SITES" 2>/dev/null | wc -l | tr -d ' ')
    printf '%s\n' "${_n:-0}"
}

# Derive the verdict-recording entry points of $1, printing `<name> [UNNOTED]` (UNNOTED when the function never
# calls gsvtk_note_site), sorted. A function records a verdict if its body bumps ok/fail or calls another
# recorder (to a fixpoint); it is an entry point if the phase's top level also calls it.
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
                    rest = substr(code, RSTART + RLENGTH)                    # a one-line f() { ...; } body lives here,
                    cur = code; sub(/^[ \t]*/, "", cur); sub(/\(.*/, "", cur)  # so read it before gsub moves RSTART
                    depth = gsub(/\{/, "{", code) - gsub(/\}/, "}", code)   # depth is cumulative from here
                    body[cur, ++nline[cur]] = ";" rest
                    if (depth <= 0) cur = ""
                    next
                }
                topline[++topline_count] = code                       # top-level code, for the entry-point rule below
                tag = heredoc_tag(line); if (tag != "") inhere = tag   # tag from the RAW line: strip() turns a quoted tag into Q
                next
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
                for (i = 1; i <= topline_count; i++) {               # same rule as gsvtk_declared_spans:
                    if (topline[i] ~ ("(^|[;&|])[ \t]*" f1 "([ \t]|$)")) {   # the callee is the first token
                        printf "%s %s\n", f1, (noted[f1] ? "" : "UNNOTED")
                        break
                    }
                }
            }
        }
    ' "$_file" | LC_ALL=C sort
}

# Compare declared and reached sites and decide. $1 = label, $2 = the phase file, $3 = verdicts recorded
# (ok+fail), $4 = named skips. The two SETS must be equal, except for sites inside a skip region this run named
# with gsvtk_note_skip. Returns 1 for anything the reader must not walk past.
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

    # Check the phase's callee list against the one derived from its source.
    _derived=$(gsvtk_declared_funset "$_file" | awk '{print $1}')
    if [ "$(printf '%s\n' "$_derived" | grep -c .)" -eq 0 ]; then
        printf '  FAIL  %s: %s contains no verdict-recording function called from its own top level, so this pair is comparing an empty set — that is a defect in the pair, not a clean phase\n' \
            "$_label" "$_file"
        return 1
    fi
    # Temp files rather than <(...): a shell without process substitution yields an empty result, which would
    # read as "no drift" and silently disable this check.
    _names_typed="${GSVTK_DECL_SITES}.typed.names"
    _names_derived="${GSVTK_DECL_SITES}.derived.names"
    printf '%s\n' $GSVTK_DECL_FUNCS | LC_ALL=C sort -u | sed '/^$/d' > "$_names_typed"
    printf '%s\n' "$_derived" | sed '/^$/d' > "$_names_derived"
    _typed_only=$(LC_ALL=C comm -23 "$_names_typed" "$_names_derived")     # typed, derives nothing
    _derived_only=$(LC_ALL=C comm -13 "$_names_typed" "$_names_derived")   # derived, not typed
    rm -f "$_names_typed" "$_names_derived" 2>/dev/null
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
    # Map each recorded line onto the site whose extent contains it (see gsvtk_declared_spans). Lines outside
    # every site are kept as undeclared.
    awk -F'\t' -v mf="${_reach_list}.mapped" -v uf="$_undecl_list" '
        NR == FNR { n++; sp[n] = $1; pth[n] = $1; sub(/:[0-9]+$/, "", pth[n])          # span file: <file>:<start> TAB <end>
                    st[n] = $1; sub(/^.*:/, "", st[n]); st[n] += 0
                    en[n] = $2 + 0
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

    # --- which unreached sites a fired skip region may withhold -----------------------------------------
    # Regions all come from $_file, so matching a region to a site needs only the line number.
    _regions="${GSVTK_DECL_SITES}.regions"
    _fired="${GSVTK_DECL_SITES}.fired"
    _entered="${GSVTK_DECL_SITES}.entered.ids"
    _allowed="${GSVTK_DECL_SITES}.allowed"
    _raninside="${GSVTK_DECL_SITES}.raninside"
    _never_list="${GSVTK_DECL_SITES}.never"
    _regsummary="${GSVTK_DECL_SITES}.regsummary"
    gsvtk_declared_regions "$_file" > "$_regions" 2>/dev/null
    _badregions=$(awk -F'\t' '$1 == "!ERROR" { sub(/^[^\t]*\t/, ""); print }' "$_regions")
    awk -F'\t' '$1 != "!ERROR" { print $1 }' "$_regions" | LC_ALL=C sort -u > "${_regions}.ids"
    : > "$_fired"; : > "$_entered"; : > "$_allowed"; : > "$_raninside"; : > "$_regsummary"
    if [ -r "${GSVTK_DECL_SITES}.skips" ]; then
        LC_ALL=C sort -u "${GSVTK_DECL_SITES}.skips" | sed '/^$/d' > "$_fired"
    fi
    if [ -r "${GSVTK_DECL_SITES}.guards" ]; then
        LC_ALL=C sort -u "${GSVTK_DECL_SITES}.guards" | sed '/^$/d' > "$_entered"
    fi
    printf '%s\n' "$_never" | sed '/^$/d' > "$_never_list"
    _unknownskip=$(LC_ALL=C comm -23 "$_fired" "${_regions}.ids")
    _ranblock=$(LC_ALL=C comm -12 "$_fired" "$_entered")
    _nfired=$(wc -l < "$_fired" | tr -d ' ')
    awk -v regions="$_regions" -v fired="$_fired" -v declared="$_decl_list" -v reached="$_reach_list" \
        -v allowed="$_allowed" -v raninside="$_raninside" -v summary="$_regsummary" '
        BEGIN {
            while ((getline l < fired) > 0) if (l != "") f[l] = 1
            while ((getline l < regions) > 0) {
                if (substr(l, 1, 7) == "!ERROR\t") continue
                split(l, a, "\t")
                if (a[1] in f) { nr++; rid[nr] = a[1]; rs[nr] = a[2] + 0; re[nr] = a[3] + 0; kind[nr] = a[4] }
            }
            while ((getline l < reached) > 0) if (l != "") has[l] = 1
            while ((getline l < declared) > 0) {
                if (l == "") continue
                ln = l; sub(/^.*:/, "", ln); ln += 0
                for (k = 1; k <= nr; k++) {
                    if (ln >= rs[k] && ln <= re[k]) {
                        cnt[k]++
                        if (l in has) print(l) > raninside     # a site INSIDE a withheld region that ran: the
                        else print(l) > allowed                # markers do not delimit what the skip withheld
                        break
                    }
                }
            }
            for (k = 1; k <= nr; k++) {
                where = (kind[k] == "tail")
                where = (where ? "line " rs[k] " to the end of the file" : "lines " rs[k] "-" re[k])
                printf("%s: %d site(s) withheld, %s\n", rid[k], cnt[k] + 0, where) > summary
            }
        }
    '
    _uncovered=$(LC_ALL=C comm -23 "$_never_list" "$_allowed")
    # Read into variables now; the scratch files are removed below.
    _raninside_list=$(sed '/^$/d' "$_raninside" 2>/dev/null)
    _reglines=$(sed '/^$/d' "$_regsummary" 2>/dev/null)

    if [ -n "$GSVTK_DECL_OWNED" ]; then
        rm -f "$GSVTK_DECL_SITES" 2>/dev/null
        GSVTK_DECL_OWNED=""
    fi
    rm -f "$_decl_list" "$_decl_spans" "$_reach_list" "${_reach_list}.raw" "${_reach_list}.mapped" \
          "$_undecl_list" "$_regions" "${_regions}.ids" "$_fired" "$_entered" "$_allowed" "$_raninside" \
          "$_never_list" "$_regsummary" 2>/dev/null

    # Region-marker problems fail in every mode, whether or not anything skipped.
    if [ -n "$_badregions" ]; then
        printf '  FAIL  %s: the skip-region markers in %s do not describe a region, so what its skips withhold is unknowable:\n' \
            "$_label" "$(basename "$_file")"
        printf '%s\n' "$_badregions" | while read -r _e; do
            [ -n "$_e" ] || continue
            printf '        %s\n' "$_e"
        done
        printf '        Every region needs gsvtk_guard_sites "<id>" as the first statement of the block and gsvtk_guard_sites_end "<id>" as the last, or one gsvtk_guard_to_end "<id>" before a bail.\n'
        return 1
    fi
    if [ -n "$_unknownskip" ]; then
        printf '  FAIL  %s: gsvtk_note_skip named %s, which %s opens no region with gsvtk_guard_sites / gsvtk_guard_to_end. A skip that names no block withholds no sites, so everything behind it is still required to have run.\n' \
            "$_label" "$(printf '%s\n' "$_unknownskip" | tr '\n' ' ' | sed 's/ *$//')" "$(basename "$_file")"
        return 1
    fi
    if [ "$_nfired" -gt "${_skipped:-0}" ]; then
        printf '  FAIL  %s: %s skip region(s) fired but the phase counted %s skipped — its own tally does not add up, so the pair cannot price what ran.\n' \
            "$_label" "$_nfired" "${_skipped:-0}"
        return 1
    fi
    if [ -n "$_ranblock" ]; then
        # The region recorded entry while its skip was named: gsvtk_guard_sites was called before the `if`, so
        # it records entry on both arms. No site inside ran (checked below), so this is a note, not a failure.
        printf '        note: %s recorded entry while its skip was named, though no assertion site inside it\n' \
            "$_label"
        printf '        ran (%s). The markers delimit the block around the `if`, not the arm the skip belongs\n' \
            "$(printf '%s\n' "$_ranblock" | tr '\n' ' ' | sed 's/ *$//')"
        printf '        to. The verdict is unaffected: what a skip withholds is counted by line number, and\n'
        printf '        the check below fails outright if any line inside a withheld region actually ran.\n'
    fi
    if [ -n "$_raninside_list" ]; then
        printf '  FAIL  %s: %s site(s) inside the withheld region(s) of %s DID run, so those markers do not delimit what the skip withholds — the region is explaining sites it has no claim on:\n' \
            "$_label" "$(printf '%s\n' "$_raninside_list" | wc -l | tr -d ' ')" "$(basename "$_file")"
        printf '%s\n' "$_raninside_list" | while read -r _s; do
            [ -n "$_s" ] || continue
            printf '        line %s: %s\n' "${_s##*:}" "$(sed -n "${_s##*:}p" "$_file" | sed 's/^[ \t]*//' | cut -c1-92)"
        done
        return 1
    fi
    if [ "${_skipped:-0}" -gt 0 ] && [ -n "$_uncovered" ]; then
        printf '  FAIL  %s: %s site(s) did not run and NO skip region this run named covers them, so they are not explained by a missing prerequisite — of the %s site(s) that did not run in %s, these are unaccounted:\n' \
            "$_label" "$(printf '%s\n' "$_uncovered" | wc -l | tr -d ' ')" \
            "$(printf '%s\n' "$_never" | grep -c .)" "$(basename "$_file")"
        printf '%s\n' "$_uncovered" | while read -r _s; do
            [ -n "$_s" ] || continue
            printf '        line %s: %s\n' "${_s##*:}" "$(sed -n "${_s##*:}p" "$_file" | sed 's/^[ \t]*//' | cut -c1-92)"
        done
        printf '        Either that block runs, or it is inside a region the skip names: gsvtk_guard_sites / gsvtk_guard_sites_end (or gsvtk_guard_to_end before a bail) and gsvtk_note_skip in the SKIP branch. Naming a skip explains the sites BEHIND THE BLOCK IT NAMES, not the rest of the file.\n'
        return 1
    fi

    if [ "${_skipped:-0}" -gt 0 ]; then
        printf '%s: declares %s assertion call site(s); this run reached %s, so %s did not run behind %s named skip(s) — %s verdict(s) recorded\n' \
            "$_label" "$_decl" "$_reach" "$((_decl - _reach))" "$_skipped" "$_ran"
        if [ -n "$_never" ]; then
            printf '        sites that did not run, by line in %s: %s\n' "$(basename "$_file")" \
                "$(printf '%s\n' "$_never" | awk -F: '{ printf "%s ", $NF }')"
        fi
        if [ "$_nfired" -gt 0 ]; then
            printf '%s\n' "$_reglines" | while read -r _r; do
                [ -n "$_r" ] || continue
                printf '        withheld by the declared skip region %s\n' "$_r"
            done
        else
            printf '        no skip region declared: every skip named in this run withholds 0 assertion call site(s), so every site %s declares still had to run\n' "$_decl"
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

# gsvtk_phase_interpreters — pass the phase's own interpreter to the gsvtk launcher it shells out to.
#
# The launcher falls back to $ROOT/.venv/bin/python, then python3, so in a tree without .venv (fresh clone,
# worktree, CI before `make setup`) it refuses for lack of firecloud even when the phase's $PY has it. When $PY
# imports firecloud, default GSVTK_TERRA_PY to it, and GSVTK_PY too unless the checkout's .venv exists
# (compare/ needs numpy/pandas/pysam, which firecloud alone does not prove). Values the caller exported always
# win: some legs set GSVTK_TERRA_PY=/usr/bin/false on purpose to test a refusal.
gsvtk_phase_interpreters() {
    [ -n "${PY:-}" ] || return 0
    "$PY" -c 'import firecloud' 2>/dev/null || return 0
    : "${GSVTK_TERRA_PY:=$PY}"
    [ -x "$PWD/.venv/bin/python" ] || : "${GSVTK_PY:=$PY}"
    export GSVTK_TERRA_PY GSVTK_PY
}
