# image_ref.sh — the shared `--image` guard, sourced by the three launchers in this directory.
#
#   REF_SH="$SCRIPT_DIR/image_ref.sh"
#   [ -f "$REF_SH" ] || { printf '...' >&2; exit 2; }      # say so; never boot unscreened
#   . "$REF_SH"
#   image_ref_guard "$0" "$IMAGE"                          # exits 2 with the reason, or returns 0
#
# Sourced, not run: no main, no `--help`, and nothing but string work, so it is testable offline and
# it runs BEFORE an instance name is derived from the reference and before a startup script is written.
# Each consumer tests for the file rather than sourcing it bare: these launchers run
# `set -uo pipefail`, NOT `-e`, so a `. missing-file` would print one line and CONTINUE into the
# create call -- which is a guard that vanished, the exact shape this file exists to close.
#
# Why one file and not three copies. `run_in_image.sh` has screened `--image` since `c3196fe`; the
# other two launchers here screened nothing but emptiness, and BOTH write the reference into a startup
# script that runs as ROOT on the VM. Measured on this checkout before the guard was shared, reading
# back the startup file each launcher actually shipped to `--metadata-from-file`:
#
#   svshell_image_check.sh --image 'x/y:tag"; id > /tmp/GSVTK-PWNED; "'
#       -> the guest's line 4 reads:  IMAGE="x/y:tag"; id > /tmp/GSVTK-PWNED; ""
#   jar_flag_probe.sh      --image $'x/y:tag\nid > /tmp/GSVTK-PWNED'
#       -> the guest gets  id > /tmp/GSVTK-PWNED  on a line of its own, after `docker pull`
#
# The first breaks out of the generated `IMAGE="…"` assignment; the second out of the unquoted heredoc
# that launcher writes. Both are the case `run_in_image.sh`'s own comment names — "would reach a root
# shell on the probe VM if this reference were interpolated" — in the two launchers where the
# interpolation is literal. A rule held by one of three siblings guards one of three, which is the
# two-places-one-value shape CONTRIBUTING.md calls this repo's worst history.
#
# The caller's own "no --image given" refusal stays in the caller: it names the config key that
# supplies a reference (`IMAGE_REPO`) and it is a better message than anything generic could be. This
# guard treats an empty reference as "not my question" and returns 0, so those refusals still print.

# The character allowlist. `-` LAST, `/` QUOTED, `:` PRESENT -- all three load-bearing, each one
# measured here: with a bare `/` in the class bash stops matching the set as a whole, so a reference
# comes back as itself instead of as its offending characters; with `-` anywhere but last a range is
# read off `@`; and dropping the `:` refuses every tagged reference, which is the one way this guard
# could fail by accepting nothing.
#
# It is a parameter expansion and not a pipe through `tr`, and that is not a style choice. This line
# used to be `printf '%s' "$ref" | tr -d 'A-Za-z0-9._-/:@+-' | cut -c1-3`, and `_-/` in that set is the
# RANGE `_` to `/` -- backwards. GNU tr prints `range-endpoints of '_-/' are in reverse collating
# sequence order`, deletes NOTHING and exits 1; the value is captured in `$( )`, the caller runs
# `set -uo pipefail` rather than `-e`, and the test asks only whether the string is empty, so a dead
# filter read exactly like "this reference is clean" and every reference passed. macOS's tr takes the
# `-` literally, so the guard worked on the dev box and was dead on `runs-on: ubuntu-latest`, the one
# machine that grades this repo -- and the gate was green because the arm carried no assertion. A
# parameter expansion has no collating sequence to get wrong, no binary that can be missing, and no
# behaviour that differs between the runner and the machine the fix was written on; `1dabbac` carries
# the two-tr measurement. One copy of the pattern lives here, and `run_in_image.sh` sources it.
image_ref_strip() {   # image_ref_strip REF -- REF with every allowed character removed
    local ref="$1"
    printf '%s' "${ref//[a-zA-Z0-9._\/:@+-]/}"
}

# image_ref_problem REF -- print the FIRST problem with the reference, nothing when it is clean.
# Each denylist arm below the allowlist has a measurement behind it (the `rc=125 ... not found` class
# is written up in `docs/troubleshooting.md`), and none of them was ever a security boundary -- that
# is what the character scan at the end of this function is for.
image_ref_problem() {
    # Reports the FIRST problem only: two reasons read as one run-on sentence (measured), and the
    # first one is what the person should fix.
    local ref="$1"
    case "$ref" in
        *[[:space:]]*) printf 'contains whitespace -- a reference is one token'; return ;;
        *://*) printf 'carries a URI scheme; the form is host/repo:tag'; return ;;
        *=*) printf 'contains "=", so this looks like a KEY=value column copied whole'; return ;;
        *,* | *\;*) printf 'contains a list separator; --image takes exactly one reference'; return ;;
        -*) printf 'starts with "-", which docker reads as a flag'; return ;;
        *'//'*) printf 'contains a doubled slash: two names joined, not one'; return ;;
        */ | ':' | *: | /*) printf 'has a dangling separator (a leading/trailing "/" or a trailing ":")'; return ;;
        *'*'* | *'?'*) printf 'contains a glob character the shell left unexpanded (nothing matched it)'; return ;;
    esac
    # Docker repository names are lowercase. An uppercase letter in the name half means this came
    # from prose or a log heading, not from a tag listing. [[:upper:]], not [A-Z]: in a UTF-8 locale
    # the range also matches lowercase, which made every honest reference look mis-assembled.
    case "${ref%:*}" in
        *[[:upper:]]*) printf 'has an uppercase letter in the repository name; docker names are lowercase'; return ;;
    esac
    # Everything above is a denylist of honest mistakes, and it was never a security boundary: `$`,
    # a backtick, `(`, `)`, `|`, `>`, `<`, `\` and `&` pass every single arm, and this reference is
    # interpolated into a startup script that runs as ROOT on the VM — so `--image '$( … )'` executed
    # inside the probe. Docker references are a closed character set, so the last word is an
    # allowlist, and it names the offending characters the way every arm above names its shape.
    local bad
    bad="$(image_ref_strip "$ref")"
    if [ -n "$bad" ]; then
        printf 'carries %s, which no docker reference can contain — and which would reach a root shell on the probe VM if this reference were interpolated' "\"${bad:0:3}\""
    fi
}

# image_ref_guard WHO REF — exit 2 with the reason, or return 0. `who` is the launcher's own name, so
# the refusal says which tool refused and stays attributable in a log; the caller passes "$0".
image_ref_guard() {
    local who="$1" ref="$2" problem
    [ -n "$ref" ] || return 0
    problem="$(image_ref_problem "$ref")"
    [ -n "$problem" ] || return 0
    printf '%b' "$(basename "$who"): refusing \"--image $ref\": it $problem.\n\
  That is the class that reaches a VM as \`rc=125 ... not found\`: the build log printed the\n\
  registry PREFIX and the image NAME as separate columns and the copy kept the space (or lost a\n\
  slash). Join them into one string with nothing between them, or re-read the reference from\n\
  \`gcloud container images list-tags <repo>\`. Nothing was created.\n" >&2
    exit 2
}
