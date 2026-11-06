#!/usr/bin/env bash
# Spike mt-bench, OFFLINE: translate both sets with every candidate, then score.
#   MT_BENCH_PAIR=ru-en|en-ru MT_BENCH_HOME=/mnt/devbox/text2audio/spikes/mt-bench \
#     spikes/mt-bench/bench.sh [key ...]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
export MT_BENCH_HOME="${MT_BENCH_HOME:-$HERE/models}"
export HF_HOME="$MT_BENCH_HOME/hf"
export MT_BENCH_PAIR="${MT_BENCH_PAIR:-ru-en}"
PY="$MT_BENCH_HOME/venv/bin/python"
[[ -x "$PY" ]] || { echo "run setup.sh first" >&2; exit 1; }
KEYS=("$@")
[[ ${#KEYS[@]} -gt 0 ]] || mapfile -t KEYS < <(cd "$HERE" && "$PY" -c 'from models import MODELS; print("\n".join(m.key for m in MODELS))')
cd "$HERE"
# The GPU is shared: wait until it has room (MT_BENCH_FREE_MB, default 21000 MiB, enough
# for the largest candidate) instead of running out of memory; finished outputs are kept.
FREE_MB="${MT_BENCH_FREE_MB:-21000}"
wait_for_gpu() {
  local free
  while free=$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits -i 0) \
      && (( free < FREE_MB )); do
    echo "   waiting for GPU memory: ${free} MiB free, need ${FREE_MB}"
    sleep 60
  done
}
for key in "${KEYS[@]}"; do
  for set in flores lit; do
    [[ -s "out/$MT_BENCH_PAIR/$key.$set.json" ]] && { echo "== $key $set (done)"; continue; }
    echo "== $key $set"
    wait_for_gpu
    "$PY" translate.py "$key" "$set"
  done
done
echo "== score"
"$PY" score.py
