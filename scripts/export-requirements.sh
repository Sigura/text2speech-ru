#!/usr/bin/env bash
# Regenerate requirements*.txt from uv.lock (uv.lock is the source of truth, NFR-04).
# CI runs this with --check and fails if the files are out of date.
set -euo pipefail
cd "$(dirname "$0")/.."
UV=${UV:-uv}
command -v "$UV" >/dev/null || UV=~/.local/bin/uv
header="# Generated from uv.lock by scripts/export-requirements.sh — do not edit."
out_rt=$(mktemp); out_dev=$(mktemp)
trap 'rm -f "$out_rt" "$out_dev"' EXIT
{ echo "$header"; echo "# Runtime: pip install -r requirements.txt"
  "$UV" export --frozen --no-dev --no-hashes --no-header --format requirements-txt; } > "$out_rt"
{ echo "$header"; echo "# Development (tests, lint, sine backend): pip install -r requirements-dev.txt"
  "$UV" export --frozen --no-hashes --no-header --format requirements-txt; } > "$out_dev"
if [ "${1-}" = --check ]; then
    cmp -s "$out_rt" requirements.txt && cmp -s "$out_dev" requirements-dev.txt \
        || { echo "requirements*.txt are stale: run scripts/export-requirements.sh" >&2; exit 1; }
else
    cp "$out_rt" requirements.txt; cp "$out_dev" requirements-dev.txt
fi
