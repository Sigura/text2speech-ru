#!/usr/bin/env bash
# FR-16 / SPEC §14: `bundle export` → `bundle import` on a machine with networking
# disabled, then doctor passes and a short text becomes an MP3.
#
#   scripts/test-bundle.sh [WORK_DIR] [--lang ru] [--tier fast]
#
# Export runs here, with the network (packages) and the local model store (models).
# The install then runs in a clean environment: an empty HOME, no inherited variables,
# uv offline, and every proxy variable pointing at a closed port, so any download
# attempt by uv, pip or a Python library fails. WORK_DIR needs room for the bundle
# twice (ru fast: about 15 GB).
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

WORK=${1:-${TMPDIR:-/tmp}/t2a-bundle-test}
[ $# -gt 0 ] && shift
ARGS=("$@")
[ ${#ARGS[@]} -gt 0 ] || ARGS=(--lang ru --tier fast)
say() { printf '\n== %s\n' "$*"; }

say "Prerequisites"
for tool in uv git tar ffmpeg; do
    command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }
done
uv --version
rm -rf "$WORK"
mkdir -p "$WORK/target" "$WORK/home"

say "Export ${ARGS[*]} (network)"
uv run --python 3.11 text2audio bundle export -o "$WORK/t2a.tar" "${ARGS[@]}"
ls -la "$WORK/t2a.tar"

say "Unpack"
tar -xf "$WORK/t2a.tar" -C "$WORK/target"
rm "$WORK/t2a.tar"

DEAD=http://127.0.0.1:9
offline() {
    env -i HOME="$WORK/home" PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
        LANG=C.UTF-8 HTTP_PROXY=$DEAD HTTPS_PROXY=$DEAD ALL_PROXY=$DEAD \
        http_proxy=$DEAD https_proxy=$DEAD all_proxy=$DEAD NO_PROXY= no_proxy= \
        UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never "$@"
}

say "Install from the bundle, offline"
offline "$WORK/target/text2audio/install.sh" --bundle

say "A short book, offline"
mkdir -p "$WORK/book"
printf '# Проверка\n\nЭта книга прочитана на машине без сети.\n' > "$WORK/book/check.md"
(cd "$WORK/target/text2audio" && offline bundle/bin/uv run text2audio run \
    "$WORK/book/check.md" -o "$WORK/out")
find "$WORK/out" -name '*.mp3' | grep -q . || { echo "no MP3 written" >&2; exit 1; }
find "$WORK/out" -name '*.mp3'

say "OK: bundle installed and used without network"
