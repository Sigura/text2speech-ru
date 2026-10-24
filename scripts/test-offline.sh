#!/usr/bin/env bash
# Run the test suite with no network (SPEC §12, FR-04).
#
# Preferred: a network namespace with no interfaces (`unshare -rn`). pytest-socket
# (enabled for every run in pyproject.toml) blocks sockets on top of that.
#
# Ubuntu 24.04 blocks unprivileged user namespaces through AppArmor
# (kernel.apparmor_restrict_unprivileged_userns=1). There the script falls back to
# pytest-socket alone and says so; set T2A_OFFLINE_STRICT=1 (CI does) to fail instead.
# To enable namespaces locally: sudo sysctl kernel.apparmor_restrict_unprivileged_userns=0
set -euo pipefail
cd "$(dirname "$0")/.."

UV=${UV:-uv}
command -v "$UV" >/dev/null || UV=~/.local/bin/uv

# Resolve and install everything while the network is still available.
"$UV" sync --frozen --quiet

if unshare -rn true 2>/dev/null; then
    echo "== offline: running inside a network namespace with no interfaces"
    exec unshare -rn "$UV" run --frozen --offline pytest "$@"
fi

if [ "${T2A_OFFLINE_STRICT:-0}" = 1 ]; then
    echo "== offline: cannot create a network namespace (unshare -rn failed)" >&2
    exit 1
fi
echo "== offline: WARNING — no network namespace available; relying on pytest-socket only" >&2
exec "$UV" run --frozen --offline pytest "$@"
