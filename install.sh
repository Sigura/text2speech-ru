#!/usr/bin/env bash
# One-command bootstrap (SPEC §6.10). After it succeeds, text2audio runs offline.
#
#   ./install.sh                       # Russian, all tiers (default)
#   ./install.sh --lang ru,en --tier fast
#   ./install.sh --no-models           # Python environment only
#   ./install.sh --bundle [DIR]        # offline, from `text2audio bundle export` (FR-16)
#
# Steps: uv → Python packages from uv.lock → plugin worker environments (one uv env per
# backend/component, ADR-0004) → system tools check → every model file pinned in
# models.lock for the chosen languages/tiers (revision + SHA-256) → doctor.
# This script and `text2audio models pull` are the only parts that use the network;
# with --bundle nothing does: uv, Python, packages and models come from DIR (default:
# the bundle/ folder next to this script), and languages/tier from its manifest.
# The Stable Diffusion WebUI for covers is an external tool and is not installed here
# (ADR-0009); `doctor` reports whether it is available.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

LANGS=ru
TIER=
MODELS=1
BUNDLE=
while [ $# -gt 0 ]; do
    case $1 in
        --lang) LANGS=$2; shift 2 ;;
        --tier) TIER=$2; shift 2 ;;
        --no-models) MODELS=0; shift ;;
        --bundle)
            if [ $# -gt 1 ] && [ "${2#--}" = "$2" ]; then BUNDLE=$2; shift 2
            else BUNDLE=bundle; shift; fi ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) echo "unknown option: $1 (see --help)" >&2; exit 1 ;;
    esac
done
case $TIER in ""|all) TIER= ;; fast|expressive) ;; *) echo "--tier: fast | expressive | all" >&2; exit 1 ;; esac

say() { printf '\n== %s\n' "$*"; }

if [ -n "$BUNDLE" ]; then
    BUNDLE=$(readlink -f "$BUNDLE")
    [ -f "$BUNDLE/manifest.json" ] || { echo "$BUNDLE: no manifest.json (not an exported bundle)" >&2; exit 1; }
    LANGS=$(sed -n 's/.*"langs": \[\([^]]*\)\].*/\1/p' "$BUNDLE/manifest.json" | tr -d '" ')
    TIER=$(sed -n 's/.*"tier": "\([a-z]*\)".*/\1/p' "$BUNDLE/manifest.json")
    read -ra ENVS <<< "$(sed -n 's/.*"envs": \[\([^]]*\)\].*/\1/p' "$BUNDLE/manifest.json" | tr -d '",')"
    export UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never UV_CACHE_DIR="$BUNDLE/uv-cache"
fi

say "uv"
if [ -n "$BUNDLE" ]; then
    # The uv that filled the cache: another version may not read its layout.
    UV=$BUNDLE/bin/uv
    if ! command -v uv >/dev/null && [ ! -x "$HOME/.local/bin/uv" ]; then
        mkdir -p "$HOME/.local/bin"
        cp "$UV" "$HOME/.local/bin/uv"
    fi
elif command -v uv >/dev/null; then UV=$(command -v uv)
elif [ -x "$HOME/.local/bin/uv" ]; then UV=$HOME/.local/bin/uv
else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    UV=$HOME/.local/bin/uv
fi
"$UV" --version

if [ -n "$BUNDLE" ]; then
    say "Python (from the bundle)"
    PYDIR=$("$UV" python dir)
    mkdir -p "$PYDIR"
    for build in "$BUNDLE"/python/*; do
        [ -e "$PYDIR/$(basename "$build")" ] || cp -a "$build" "$PYDIR/"
    done
    ls "$PYDIR"
fi

say "Python packages (uv.lock)"
"$UV" sync --frozen

say "Plugin worker environments"
if [ -n "$BUNDLE" ]; then
    # only those the bundle has packages for; an empty list would mean all
    if [ ${#ENVS[@]} -gt 0 ]; then "$UV" run text2audio envs sync "${ENVS[@]}"; fi
else
    "$UV" run text2audio envs sync
fi

say "System tools"
missing=()
for tool in ffmpeg espeak-ng; do
    if path=$(command -v "$tool"); then echo "$tool: $path"; else missing+=("$tool"); fi
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "missing: ${missing[*]} — install with: sudo apt install ${missing[*]}" >&2
    echo "(or use the conda environment: conda env create -f environment.yaml)" >&2
fi

if [ -n "$BUNDLE" ]; then
    say "Model weights (from the bundle: lang=${LANGS:-all} tier=${TIER:-all})"
    "$UV" run text2audio bundle import "$BUNDLE"
elif [ "$MODELS" = 1 ]; then
    say "Model weights for lang=$LANGS tier=${TIER:-all}"
    "$UV" run text2audio models pull --lang "$LANGS" ${TIER:+--tier "$TIER"}
    "$UV" run text2audio models verify --lang "$LANGS" ${TIER:+--tier "$TIER"}
fi

say "doctor"
if [ "$MODELS" = 1 ] || [ -n "$BUNDLE" ]; then
    "$UV" run text2audio doctor ${LANGS:+--lang "$LANGS"} ${TIER:+--tier "$TIER"}
else
    "$UV" run text2audio doctor
fi
