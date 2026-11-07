#!/usr/bin/env bash
# Dependency audit (SPEC §6.12): pip-audit over the main lock and every plugin env's
# lock, each exported to a requirements file with hashes. Uses the network (PyPI's
# vulnerability data); CI runs it, the offline runtime never does.
#
#   scripts/audit.sh
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

command -v uv >/dev/null || { echo "missing: uv" >&2; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

locks=(.)
while IFS= read -r lock; do locks+=("$(dirname "$lock")"); done \
    < <(git ls-files 'plugins/*/env/uv.lock')

failed=()
for project in "${locks[@]}"; do
    name=$( [ "$project" = . ] && echo main || echo "$project" | cut -d/ -f2 )
    echo "== $name ($project)"
    # PyPI knows torch 2.14.1, not the CPU build 2.14.1+cpu; their advisories are the same
    uv export --quiet --frozen --project "$project" --format requirements-txt --no-hashes \
        --no-emit-project --no-emit-workspace --no-editable \
        | sed -E 's/^([A-Za-z0-9_.-]+==[^+ ;]+)\+[a-z0-9.]+/\1/' > "$work/$name.txt"
    if ! uvx pip-audit --progress-spinner off --strict --disable-pip --no-deps \
        -r "$work/$name.txt"; then
        failed+=("$name")
    fi
done

if [ ${#failed[@]} -gt 0 ]; then
    echo "vulnerable or unauditable: ${failed[*]}" >&2
    exit 1
fi
echo "no known vulnerabilities in ${#locks[@]} lock file(s)"
