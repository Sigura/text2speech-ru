#!/usr/bin/env bash
# Spike en-bench, OFFLINE steps: render the 100 benchmark segments with every system,
# transcribe and score them. Run setup.sh once before (the only online step).
#
#   spikes/en-bench/bench.sh [stage ...]
#
# Stages (default: all, in this order):
#   t0         text2audio run of bench.md, fast tier (Kokoro, CPU only), on T0: 4 cores, 8 GB
#   t1         the same on the expressive tier (Chatterbox) on the GPU with most free memory
#   collect    segment WAVs + texts → out/wav/{kokoro,chatterbox}, out/segments.json
#   indextts   IndexTTS-2 on that GPU, cloned from the en pack's reference clips
#   cpu        IndexTTS-2 on 4 cores for $CPU_LIMIT segments (T0 RTF estimate)
#   transcribe faster-whisper large-v3-turbo transcripts (the QA gate's asr-whisper worker)
#   score      out/report.md
#
# Env: EN_BENCH_HOME (as in setup.sh), CPU_LIMIT (default 3), T2A_MODEL_HOME, T2A_ENV_HOME.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$HERE/out"
HOME_DIR="${EN_BENCH_HOME:-$HERE/models}"
CPU_LIMIT="${CPU_LIMIT:-3}"
STAGES=("$@")
[[ ${#STAGES[@]} -gt 0 ]] || STAGES=(t0 t1 collect indextts cpu transcribe score)
export CUDA_DEVICE_ORDER=PCI_BUS_ID

for tool in uv nvidia-smi taskset; do
  command -v "$tool" >/dev/null || { echo "$tool not found" >&2; exit 1; }
done
[[ -f "$HERE/bench.md" ]] || { echo "bench.md missing: run build_set.py" >&2; exit 1; }
mkdir -p "$OUT"
cd "$ROOT"

core() { uv run --python 3.11 "$@"; }

pick_gpu() {
  # "<uuid> <cpu list> <free MB>" of the visible GPU with the most free memory.
  core python - <<'EOF'
from text2audio.core import resources as res
gpus = res.probe().gpus
if not gpus:
    raise SystemExit("no GPU visible")
g = max(gpus, key=lambda g: g.free_mb)
cpus = sorted(g.cpu_affinity or range(8))[:8]
print(g.uuid, ",".join(map(str, cpus)), g.free_mb)
EOF
}

VRAM_PID=""
trap '[[ -z "$VRAM_PID" ]] || kill "$VRAM_PID"' EXIT
sample_vram() {  # sample_vram <uuid> <name>: background sampler, stopped by stop_vram or on exit
  nvidia-smi -i "$1" --query-gpu=memory.used --format=csv,noheader,nounits -lms 500 \
    >"$OUT/vram-$2.txt" &
  VRAM_PID=$!
}
stop_vram() { kill "$VRAM_PID"; VRAM_PID=""; }

memory_scope() {  # SCOPE = systemd user scope with a memory limit, when available
  SCOPE=()
  if command -v systemd-run >/dev/null \
    && systemd-run --user --scope --quiet -p MemoryMax=1G true 2>/dev/null; then
    SCOPE=(systemd-run --user --scope --quiet -p "MemoryMax=$1")
  fi
}

GPU="" CPUS=""
need_gpu() {
  if [[ -z "$GPU" ]]; then
    read -r GPU CPUS FREE < <(pick_gpu)
    echo "== GPU $GPU (${FREE} MB free), CPUs $CPUS"
  fi
}

run_book() {  # run_book <tag> <tier> <prefix...>
  local tag="$1" tier="$2"; shift 2
  rm -rf "$OUT/run-$tag"
  "$@" uv run --python 3.11 text2audio run "$HERE/bench.md" -o "$OUT/run-$tag" \
    --lang en --tier "$tier" --qa off
}

indextts() {  # indextts <device> <selection> <tag> <prefix...>
  local device="$1" selection="$2" tag="$3"; shift 3
  "$@" env EN_BENCH_HOME="$HOME_DIR" UV_PROJECT_ENVIRONMENT="$HOME_DIR/venv-indextts" \
    uv run --python 3.12 --project "$HERE/envs/indextts" python "$HERE/synth.py" \
    --device "$device" $selection --tag "$tag"
}

for stage in "${STAGES[@]}"; do
  echo "== stage $stage"
  case "$stage" in
    t0)
      memory_scope 8G
      CUDA_VISIBLE_DEVICES="" run_book t0 fast "${SCOPE[@]}" taskset -c 0-3 ;;
    t1)
      need_gpu
      sample_vram "$GPU" chatterbox
      memory_scope 16G
      CUDA_VISIBLE_DEVICES="$GPU" run_book t1 expressive "${SCOPE[@]}" taskset -c "$CPUS"
      stop_vram ;;
    collect)
      core python "$HERE/collect.py" ;;
    indextts)
      need_gpu
      sample_vram "$GPU" indextts
      CUDA_VISIBLE_DEVICES="$GPU" indextts cuda "" t1 taskset -c "$CPUS"
      stop_vram ;;
    cpu)
      CUDA_VISIBLE_DEVICES="" indextts cpu "--limit $CPU_LIMIT" t0 taskset -c 0-3 ;;
    transcribe)
      need_gpu
      CUDA_VISIBLE_DEVICES="$GPU" core python "$HERE/transcribe.py" ;;
    score)
      core python "$HERE/score.py" ;;
    *)
      echo "unknown stage: $stage" >&2; exit 1 ;;
  esac
done
echo "== done: $OUT/report.md"
