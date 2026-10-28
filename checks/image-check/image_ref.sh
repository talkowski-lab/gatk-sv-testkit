# shellcheck shell=bash
# image_ref.sh — the shared `--image` guard, sourced by the three launchers in this directory.
#
#   REF_SH="$SCRIPT_DIR/image_ref.sh"
#   [ -f "$REF_SH" ] || { printf '...' >&2; exit 2; }      # say so; never boot unscreened
#   . "$REF_SH"
#   image_ref_guard "$0" "$IMAGE"                          # exits 2 with the reason, or returns 0
#
# Sourced, not run. The launchers write the reference into a startup script that runs as root on the
# VM, so this runs before any instance name or startup script is derived from it. Callers test for the
# file before sourcing it: they run `set -uo pipefail` without `-e`, so a missing file would not stop them.
#
# An empty reference returns 0 so the caller's own "no --image given" message (naming IMAGE_REPO) prints.

# Character allowlist. In the bracket class, `-` must be last, `/` must be escaped, and `:` must be
# present. A parameter expansion is used rather than `tr`, whose range handling differs between GNU
# and BSD.
image_ref_strip() {   # image_ref_strip REF -- REF with every allowed character removed
    local ref="$1"
    printf '%s' "${ref//[a-zA-Z0-9._\/:@+-]/}"
}

# image_ref_problem REF -- print the first problem with the reference, nothing when it is clean.
# The case arms catch common copy-paste mistakes; the allowlist scan at the end is the security check.
image_ref_problem() {
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
    # Docker repository names are lowercase. [[:upper:]], not [A-Z]: in a UTF-8 locale the range
    # also matches lowercase.
    case "${ref%:*}" in
        *[[:upper:]]*) printf 'has an uppercase letter in the repository name; docker names are lowercase'; return ;;
    esac
    # Shell metacharacters pass the arms above, and the reference is interpolated into a root startup
    # script, so finish with the allowlist and name any offending characters.
    local bad
    bad="$(image_ref_strip "$ref")"
    if [ -n "$bad" ]; then
        printf 'carries %s, which no docker reference can contain — and which would reach a root shell on the probe VM if this reference were interpolated' "\"${bad:0:3}\""
    fi
}

# image_ref_guard WHO REF -- exit 2 with the reason, or return 0. WHO is the caller's "$0".
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
