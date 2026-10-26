#!/usr/bin/env bash
# Spike ru-bench, OFFLINE steps: render the benchmark set with every system, score it
# and build the listening page. Run setup.sh once before (the only online step).
#
#   spikes/ru-bench/bench.sh [stage ...]
#
# Stages (default: all, in this order):
#   t0        text2audio run of bench.md on T0: 4 cores, 8 GB, no GPU (Silero)
#   t1        text2audio run on T1: the free RTX 4090 with most memory, 8 cores near it
#             (hybrid Silero → Chatterbox VC / Chatterbox TTS), GPU memory sampled
#   collect   segment WAVs + texts → out/wav/{silero,hybrid}, out/segments.json; the
#             listening clips and the ≤ 100 challenger segments → out/listen-key.json
#   qwen3 espeech xtts   one challenger on the same GPU, the planned segments
#   cpu       each challenger on 4 cores for $CPU_LIMIT segments (T0 RTF estimate)
#   transcribe GigaAM-v3 transcripts (ru-smoke env)
#   score     out/report.md
#   listen    out/listen/index.html
#
# Env: CPU_LIMIT (default 2; F5 on 4 cores runs at RTF ≈ 150).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$HERE/out"
CPU_LIMIT="${CPU_LIMIT:-2}"
STAGES=("$@")
[[ ${#STAGES[@]} -gt 0 ]] || STAGES=(t0 t1 collect qwen3 espeech xtts cpu transcribe score listen)
export CUDA_DEVICE_ORDER=PCI_BUS_ID

for tool in uv nvidia-smi taskset ffmpeg; do
  command -v "$tool" >/dev/null || { echo "$tool not found" >&2; exit 1; }
done
[[ -f "$HERE/bench.md" ]] || { echo "bench.md missing: run build_set.py" >&2; exit 1; }
mkdir -p "$OUT"
cd "$ROOT"

core() { uv run --python 3.11 "$@"; }

pick_gpu() {
  # "<uuid> <cpu list>" of the RTX 4090 with the most free memory, via the core's probe.
  core python - <<'EOF'
from text2audio.core import resources as res
gpus = [g for g in res.probe().gpus if "4090" in g.name]
if not gpus:
    raise SystemExit("no RTX 4090 visible")
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
    --tier "$tier" --qa off
}

challenger() {  # challenger <name> <device> <selection> <tag> <prefix...>
  local name="$1" device="$2" selection="$3" tag="$4"; shift 4
  "$@" uv run --project "$HERE/envs/$name" python "$HERE/synth.py" "$name" \
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
      sample_vram "$GPU" hybrid
      memory_scope 16G
      CUDA_VISIBLE_DEVICES="$GPU" run_book t1 expressive "${SCOPE[@]}" taskset -c "$CPUS"
      stop_vram ;;
    collect)
      core python "$HERE/collect.py"
      core python "$HERE/listen.py" plan ;;
    qwen3 | espeech | xtts)
      need_gpu
      sample_vram "$GPU" "$stage"
      CUDA_VISIBLE_DEVICES="$GPU" challenger "$stage" cuda --planned t1 taskset -c "$CPUS"
      stop_vram ;;
    cpu)
      for name in qwen3 espeech xtts; do
        CUDA_VISIBLE_DEVICES="" challenger "$name" cpu "--limit $CPU_LIMIT" t0 taskset -c 0-3
      done ;;
    transcribe)
      need_gpu
      CUDA_VISIBLE_DEVICES="$GPU" uv run --project "$HERE/../ru-smoke" python "$HERE/transcribe.py" ;;
    score)
      core python "$HERE/score.py" ;;
    listen)
      core python "$HERE/listen.py" ;;
    *)
      echo "unknown stage: $stage" >&2; exit 1 ;;
  esac
done
echo "== done: $OUT/report.md, $OUT/listen/index.html"
