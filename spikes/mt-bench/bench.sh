#!/usr/bin/env bash
# Spike mt-bench, OFFLINE: translate both sets with every candidate, then score.
#   MT_BENCH_HOME=/mnt/devbox/text2audio/spikes/mt-bench spikes/mt-bench/bench.sh [key ...]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export MT_BENCH_HOME="${MT_BENCH_HOME:-$HERE/models}"
export HF_HOME="$MT_BENCH_HOME/hf"
PY="$MT_BENCH_HOME/venv/bin/python"
[[ -x "$PY" ]] || { echo "run setup.sh first" >&2; exit 1; }
KEYS=("$@")
[[ ${#KEYS[@]} -gt 0 ]] || mapfile -t KEYS < <(cd "$HERE" && "$PY" -c 'from models import MODELS; print("\n".join(m.key for m in MODELS))')
cd "$HERE"
for key in "${KEYS[@]}"; do
  for set in flores lit; do
    echo "== $key $set"
    "$PY" translate.py "$key" "$set"
  done
done
echo "== score"
"$PY" score.py
