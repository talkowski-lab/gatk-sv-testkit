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

# GSVTK_PY is the repo-wide interpreter knob, the same one `gsvtk`'s py_for honors. It is consulted
# BEFORE the PATH probe because "the python3 that happens to be on PATH" is not what a caller means when
# it names an interpreter: a checkout whose venv lives somewhere other than $ROOT/.venv, and every
# selftest that hands its own interpreter down, both need the named python to actually be the one that
# runs. Naming it and getting python3 anyway is how a suite ends up grading somebody's machine layout
# while reading like it grades the code.
if [ -z "${GSVTK_PYTHON:-}" ] && [ -n "${GSVTK_PY:-}" ]; then
    if "$GSVTK_PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        GSVTK_PYTHON="$GSVTK_PY"
    else
        echo "kit/config.sh: GSVTK_PY=$GSVTK_PY is not python >= 3.9 (gsvtk-config uses str.removeprefix); refusing to run under it" >&2
        exit 3
    fi
fi
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

# Loading must never be fatal: a script that needs a value calls gsvtk_require for it and gets a
# written instruction, so `--help` keeps working with no configuration at all.
#
# "Not fatal" is not "invisible", though. This call used to carry 2>/dev/null, so a resolver that
# failed for any reason -- bad interpreter, a syntax error introduced in gsvtk-config, a profile it
# could not parse -- left every GSVTK_* UNSET and printed nothing. Each bash tool then fell back to
# its own inline defaults (ZONE us-central1-a, DISK_GB 60, TIMEOUT_H 8), so the failure surfaced
# many steps later as a build in someone's guessed region, or never. One warning here, carrying the
# resolver's own stderr, is the difference between a silent fallback and a diagnosable one.
if _gsvtk_env="$("$GSVTK_PYTHON" "$GSVTK_ROOT/kit/gsvtk-config" env 2>/dev/null)"; then
    eval "$_gsvtk_env"
else
    _gsvtk_err="$("$GSVTK_PYTHON" "$GSVTK_ROOT/kit/gsvtk-config" env 2>&1 1>/dev/null)"
    {
        echo "kit/config.sh: the configuration layer produced no exports; every GSVTK_* stays"
        echo "  unset and tools will fall back to their own inline defaults. Fix it, or pass the"
        echo "  values explicitly -- see docs/config.md."
        [ -n "$_gsvtk_err" ] && printf '%s\n' "$_gsvtk_err" | head -4 | sed 's/^/    /'
    } >&2
fi
unset _gsvtk_env _gsvtk_err

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
