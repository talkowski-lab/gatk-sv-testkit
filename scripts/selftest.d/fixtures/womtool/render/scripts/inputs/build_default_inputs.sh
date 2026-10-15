#!/usr/bin/env bash
# build_default_inputs.sh — the fixture twin of gatk-sv's default-inputs renderer.
#
# gatk-sv's own `scripts/inputs/build_default_inputs.sh` is the thing
# `checks/wdl_inputs_check.py::render()` exists to run: it takes `-d <tree-root>`, reads
# `inputs/values/*.json`, and writes `inputs/build/**.json`. That is the whole surface the checker
# depends on — the flag, the two directories, and "it wrote at least one JSON" — and a gatk-sv clone
# is not something a selftest phase may require. So this file stands in for it: same flag, same
# layout, one JSON out of one values file, no templates and no imports.
#
# It is deliberately NOT a reimplementation of upstream's judgement. Which JSONs a real ref renders is
# that ref's business (see the checker's module docstring on why rendering is shelled out and never
# reimplemented); what the fixture is responsible for is the destination contract the checker owns —
# that the render lands where `-d` says, inside the tree the checker stamped, and nowhere else.
#
# Owned by `scripts/selftest.d/womtool.sh` §12, which builds a throwaway git repository around it so
# that `git archive <ref> inputs scripts/inputs` has something to archive.
set -u

tree="."
while [ $# -gt 0 ]; do
    case "$1" in
        # `|| break`, not a bare `shift 2`: with `-d` as the LAST argument the shift fails without
        # shifting anything, and `[ $# -gt 0 ]` would then stay true forever. `checks/wdl_inputs_check.py`
        # always calls `bash <this> -d .`, so the hang is a future-caller hazard rather than a live one —
        # which is exactly the kind of thing a fixture should not be able to grow.
        -d) tree="${2:-.}"; shift 2 || break;;
        *)  shift;;
    esac
done

values="$tree/inputs/values/render.json"
[ -f "$values" ] || { echo "build_default_inputs.sh: no values file at $values" >&2; exit 1; }
value=$(sed -n 's/.*"render_value"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$values" | head -1)
[ -n "$value" ] || { echo "build_default_inputs.sh: no render_value in $values" >&2; exit 1; }

out="$tree/inputs/build/ref_panel_1kg/test/Rendered"
mkdir -p "$out"
printf '{\n  "Rendered.render_value": "%s"\n}\n' "$value" > "$out/Rendered.json"
echo "build_default_inputs.sh: rendered 1 input JSON under $tree/inputs/build (render_value=$value)"
