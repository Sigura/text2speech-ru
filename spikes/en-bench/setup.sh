#!/usr/bin/env bash
# Spike en-bench, ONLINE step (the only one): the IndexTTS-2 env and its weights.
#
#   EN_BENCH_HOME=/mnt/devbox/text2audio/spikes/en-bench spikes/en-bench/setup.sh [stage ...]
#
# Stages (default: all): checkout env weights.
#
# EN_BENCH_HOME (default spikes/en-bench/models) gets the index-tts checkout, the venv
# and ~9 GB of weights at pinned revisions: IndexTTS-2 without its Qwen emotion model
# (~4.7 GB), w2v-bert-2.0 (~2.3 GB), the MaskGCT semantic codec, CAMPPlus and BigVGAN.
# Kokoro, Chatterbox and faster-whisper come from the text2audio model store.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="${EN_BENCH_HOME:-$HERE/models}"
INDEX_TTS_COMMIT=d9e41aac89fd00b3d71497fddb287b7f24613712

STAGES=("$@")
[[ ${#STAGES[@]} -gt 0 ]] || STAGES=(checkout env weights)
want() { [[ " ${STAGES[*]} " == *" $1 "* ]]; }

for tool in uv git curl; do
  command -v "$tool" >/dev/null || { echo "$tool not found" >&2; exit 1; }
done
mkdir -p "$HOME_DIR"

if want checkout; then
  echo "== index-tts checkout @ $INDEX_TTS_COMMIT"
  if [[ ! -d "$HOME_DIR/index-tts/.git" ]]; then
    git clone -q https://github.com/index-tts/index-tts.git "$HOME_DIR/index-tts"
  fi
  git -C "$HOME_DIR/index-tts" fetch -q --depth 1 origin "$INDEX_TTS_COMMIT"
  git -C "$HOME_DIR/index-tts" checkout -q "$INDEX_TTS_COMMIT"
fi

if want env; then
  echo "== uv env"
  UV_PROJECT_ENVIRONMENT="$HOME_DIR/venv-indextts" uv sync --python 3.12 \
    --project "$HERE/envs/indextts"
fi

fetch() {  # fetch <repo> <revision> <file> <dest>
  local dest="$4"
  [[ -s "$dest" ]] && return 0
  echo "   $1/$3"
  mkdir -p "$(dirname "$dest")"
  curl -fsSL --retry 5 -C - -o "$dest.part" "https://huggingface.co/$1/resolve/$2/$3"
  mv "$dest.part" "$dest"
}

want weights || exit 0
echo "== weights"
CK="$HOME_DIR/checkpoints"
for f in config.yaml gpt.pth s2mel.pth bpe.model wav2vec2bert_stats.pt feat1.pt feat2.pt; do
  fetch IndexTeam/IndexTTS-2 740dcaff396282ffb241903d150ac011cd4b1ede "$f" "$CK/$f"
done
for f in config.json preprocessor_config.json model.safetensors; do
  fetch facebook/w2v-bert-2.0 da985ba0987f70aaeb84a80f2851cfac8c697a7b "$f" \
    "$CK/hf_cache/w2v-bert-2.0/$f"
done
fetch amphion/MaskGCT 265c6cef07625665d0c28d2faafb1415562379dc \
  semantic_codec/model.safetensors "$CK/hf_cache/semantic_codec_model.safetensors"
fetch funasr/campplus e4b6ede7ce16997aff4ae69fbca1f0175e2afede \
  campplus_cn_common.bin "$CK/hf_cache/campplus_cn_common.bin"
for f in config.json bigvgan_generator.pt; do
  fetch nvidia/bigvgan_v2_22khz_80band_256x 633ff708ed5b74903e86ff1298cf4a98e921c513 "$f" \
    "$CK/hf_cache/bigvgan/$f"
done
echo "== done: $(du -sh "$CK" | cut -f1) in $CK"
