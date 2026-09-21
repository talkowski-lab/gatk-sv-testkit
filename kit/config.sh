# kit/config.sh — bash entry point to the shared configuration.
#
#   . "$(dirname "$0")/../kit/config.sh"     # from a tool one directory down
#   gsvtk_require PROJECT                    # dies with an instruction if unset
#   echo "$GSVTK_PROJECT"                    # already exported by the loader
#
# Resolution lives in kit/gsvtk-config (one source of truth for bash and
# Python alike); this file only finds it, evals the result, and adds helpers.
# Bash 3.2 safe (macOS stock /bin/bash), no readlink -f, no arrays-of-associatives.

GSVTK_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd -P)"
export GSVTK_ROOT

if [ -z "${GSVTK_PYTHON:-}" ]; then
    for _gsvtk_py in python3 python; do
        if command -v "$_gsvtk_py" >/dev/null 2>&1 \
           && "$_gsvtk_py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
            GSVTK_PYTHON="$_gsvtk_py"
            break
        fi
    done
fi
if [ -z "${GSVTK_PYTHON:-}" ]; then
    echo "kit/config.sh: need python3 (>=3.9; gsvtk-config uses str.removeprefix) to resolve configuration; set GSVTK_PYTHON" >&2
    return 1 2>/dev/null || exit 1
fi
export GSVTK_PYTHON

# Loading must never be fatal: a script that needs a value calls gsvtk_require
# for it and gets a written instruction. This way `--help` keeps working with no
# configuration at all.
if _gsvtk_env="$("$GSVTK_PYTHON" "$GSVTK_ROOT/kit/gsvtk-config" env 2>/dev/null)"; then
    eval "$_gsvtk_env"
fi
unset _gsvtk_env

# gsvtk_require KEY [extra hint] — exit 4 with the fix, never run with a guess.
gsvtk_require() {
    local key="$1" hint="${2:-}" value
    value="$("$GSVTK_PYTHON" "$GSVTK_ROOT/kit/gsvtk-config" require "$key" 2>&1)" || {
        printf '%s\n' "$value" >&2
        [ -n "$hint" ] && printf '  %s\n' "$hint" >&2
        exit 4
    }
    export "GSVTK_$key=$value"
}

# gsvtk_default KEY FALLBACK — value if configured, else FALLBACK (no failure).
gsvtk_default() {
    local value
    value="$("$GSVTK_PYTHON" "$GSVTK_ROOT/kit/gsvtk-config" get "$1" 2>/dev/null)" || value=""
    printf '%s' "${value:-$2}"
}

# Scratch tree: staging/ runs/ manifests/ recon/ outputs/ metadata/, gitignored.
# gsvtk_work [subdir ...] prints its absolute path, creating any subdirs.
gsvtk_work() {
    "$GSVTK_PYTHON" "$GSVTK_ROOT/kit/gsvtk-config" work "$@"
}
