#!/usr/bin/env bash
# One-command bootstrap (SPEC §6.10). After it succeeds, text2audio runs offline.
#
#   ./install.sh                       # Russian, all tiers (default)
#   ./install.sh --lang ru,en --tier fast
#   ./install.sh --no-models           # Python environment only
#
# Steps: uv → Python packages from uv.lock → plugin worker environments (one uv env per
# backend/component, ADR-0004) → system tools check → every model file pinned in
# models.lock for the chosen languages/tiers (revision + SHA-256) → doctor.
# This script and `text2audio models pull` are the only parts that use the network.
# The Stable Diffusion WebUI for covers is an external tool and is not installed here
# (ADR-0009); `doctor` reports whether it is available.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

LANGS=ru
TIER=
MODELS=1
while [ $# -gt 0 ]; do
    case $1 in
        --lang) LANGS=$2; shift 2 ;;
        --tier) TIER=$2; shift 2 ;;
        --no-models) MODELS=0; shift ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *) echo "unknown option: $1 (see --help)" >&2; exit 1 ;;
    esac
done
case $TIER in ""|all) TIER= ;; fast|expressive) ;; *) echo "--tier: fast | expressive | all" >&2; exit 1 ;; esac

say() { printf '\n== %s\n' "$*"; }

say "uv"
if command -v uv >/dev/null; then UV=$(command -v uv)
elif [ -x "$HOME/.local/bin/uv" ]; then UV=$HOME/.local/bin/uv
else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    UV=$HOME/.local/bin/uv
fi
"$UV" --version

say "Python packages (uv.lock)"
"$UV" sync --frozen

say "Plugin worker environments"
"$UV" run text2audio envs sync

say "System tools"
missing=()
for tool in ffmpeg espeak-ng; do
    if path=$(command -v "$tool"); then echo "$tool: $path"; else missing+=("$tool"); fi
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "missing: ${missing[*]} — install with: sudo apt install ${missing[*]}" >&2
    echo "(or use the conda environment: conda env create -f environment.yaml)" >&2
fi

if [ "$MODELS" = 1 ]; then
    say "Model weights for lang=$LANGS tier=${TIER:-all}"
    "$UV" run text2audio models pull --lang "$LANGS" ${TIER:+--tier "$TIER"}
    "$UV" run text2audio models verify --lang "$LANGS" ${TIER:+--tier "$TIER"}
fi

pip install -r ./requirements.txt

say "doctor"
"$UV" run text2audio doctor
