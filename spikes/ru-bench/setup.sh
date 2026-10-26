#!/usr/bin/env bash
# Spike ru-bench, ONLINE step (the only one): create the challenger environments and
# download their weights once into spikes/ru-bench/models/hf.
#
#   spikes/ru-bench/setup.sh [qwen3 espeech xtts]
#
# Downloads: PyPI wheels for three uv envs (torch 2.6.0 is shared through the uv cache)
# and ~8 GB of weights: Qwen3-TTS-12Hz-1.7B-Base (~4.5 GB), ESpeech-TTS-1_SFT-256K +
# vocos-mel-24khz (~1.4 GB), XTTS-v2 (~2.1 GB).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SYSTEMS=("$@")
[[ ${#SYSTEMS[@]} -gt 0 ]] || SYSTEMS=(qwen3 espeech xtts)

command -v uv >/dev/null || { echo "uv not found (expected ~/.local/bin/uv)" >&2; exit 1; }
[[ -f "$HERE/../../plugins/lang-ru/src/text2audio_lang_ru/voices/ref-xenia.wav" ]] \
  || { echo "xenia reference clip missing in the ru pack" >&2; exit 1; }

for name in "${SYSTEMS[@]}"; do
  [[ -f "$HERE/envs/$name/pyproject.toml" ]] || { echo "unknown system: $name" >&2; exit 1; }
  echo "== $name: uv sync"
  uv sync --project "$HERE/envs/$name"
  echo "== $name: fetch weights"
  uv run --project "$HERE/envs/$name" python "$HERE/fetch.py" "$name"
done
echo "== done: $(du -sh "$HERE/models/hf" | cut -f1) in $HERE/models/hf"
