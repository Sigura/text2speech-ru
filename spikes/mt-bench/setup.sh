#!/usr/bin/env bash
# Spike mt-bench, ONLINE step (the only one): the env, FLORES-200 and the candidate weights.
#
#   MT_BENCH_HOME=/mnt/devbox/text2audio/spikes/mt-bench spikes/mt-bench/setup.sh [stage ...]
#
# Stages (default: all): env data weights. Weights go to $MT_BENCH_HOME/hf (HF_HOME) at
# the revisions pinned in models.py (~85 GB), over plain HTTP (Xet downloads hang
# here). Opus-MT comes from the text2audio store.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${MT_BENCH_HOME:-$HERE/models}"
STAGES=("$@")
[[ ${#STAGES[@]} -gt 0 ]] || STAGES=(env data weights)
want() { [[ " ${STAGES[*]} " == *" $1 "* ]]; }

for tool in uv curl tar; do
  command -v "$tool" >/dev/null || { echo "$tool not found" >&2; exit 1; }
done
mkdir -p "$HOME_DIR"

# Managed Python: Triton (pulled in by the LLMs) compiles a helper against Python.h.
if want env; then
  echo "== uv env"
  UV_PROJECT_ENVIRONMENT="$HOME_DIR/venv" uv sync --python 3.12 --managed-python --project "$HERE/env"
fi

if want data; then
  echo "== FLORES-200"
  if [[ ! -d "$HOME_DIR/flores200_dataset" ]]; then
    curl -fsSL --retry 5 -o "$HOME_DIR/flores.tar.gz" \
      https://dl.fbaipublicfiles.com/nllb/flores200_dataset.tar.gz
    tar -xzf "$HOME_DIR/flores.tar.gz" -C "$HOME_DIR"
    rm "$HOME_DIR/flores.tar.gz"
  fi
fi

if want weights; then
  echo "== weights"
  HF_HUB_DISABLE_XET=1 HF_HOME="$HOME_DIR/hf" "$HOME_DIR/venv/bin/python" "$HERE/models.py" download
fi
echo "done"
