"""Worker side of protocol 1, standard library only (SPEC §6.6.3).

Backend and component workers run in their own uv environments, where the core is not
installed; the core puts this directory on their `PYTHONPATH`. A worker imports this
module **first**: with `T2A_NO_NETWORK=1` (set by the core) it blocks every non-local
socket before any ML library is imported, so a library that tries to download on first
use fails loudly instead of fetching (ADR-0002).

    import t2a_worker

    def load(msg): ...; return {"sample_rate": 48000}
    def synthesize(msg): ...; return {"duration_ms": 4120}

    t2a_worker.serve("silero", {"load": load, "synthesize": synthesize})
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import traceback
from collections.abc import Callable
from typing import Any

PROTOCOL = 1

Handler = Callable[[dict[str, Any]], dict[str, Any]]


class NetworkBlocked(OSError):
    pass


def _blocked(*args: Any, **kwargs: Any) -> Any:
    raise NetworkBlocked("network access is disabled at runtime (text2audio offline contract)")


def guard_network() -> None:
    """Allow only Unix sockets and loopback; everything else raises `NetworkBlocked`."""
    original_connect = socket.socket.connect

    def connect(self: socket.socket, address: Any) -> Any:
        if self.family == socket.AF_UNIX:
            return original_connect(self, address)
        host = address[0] if isinstance(address, tuple) else str(address)
        if host in ("127.0.0.1", "::1", "localhost"):
            return original_connect(self, address)
        return _blocked()

    socket.socket.connect = connect  # type: ignore[method-assign, assignment]
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked


if os.environ.get("T2A_NO_NETWORK") == "1":
    guard_network()


def serve(name: str, handlers: dict[str, Handler]) -> None:
    """Announce readiness, then answer requests until `shutdown` or end of input.

    Handlers return the reply's fields; `ok` is added. An exception becomes
    `{"ok": false, "error": …}` and the worker keeps serving. Library output to stdout
    is redirected to stderr so it cannot corrupt the protocol stream.
    """
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    out = sys.stdout
    out.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stdout = sys.stderr
    send = _make_sender(out)
    send({"protocol": PROTOCOL, "event": "ready", "backend": name})
    for line in sys.stdin:
        msg: dict[str, Any] = {}
        try:
            msg = json.loads(line)
            if msg.get("protocol") != PROTOCOL:
                reply = {"ok": False, "error": f"protocol mismatch: worker speaks {PROTOCOL}"}
            elif msg.get("op") == "shutdown":
                reply = {"ok": True}
            elif msg.get("op") in handlers:
                started = time.monotonic()
                reply = {"ok": True, **handlers[msg["op"]](msg)}
                reply.setdefault("elapsed_ms", int((time.monotonic() - started) * 1000))
            else:
                reply = {"ok": False, "error": f"unknown op {msg.get('op')!r}"}
        except Exception as exc:  # report, keep serving
            traceback.print_exc(file=sys.stderr)
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        send(reply)
        if msg.get("op") == "shutdown":
            break


def _make_sender(stream: Any) -> Callable[[dict[str, Any]], None]:
    def send(msg: dict[str, Any]) -> None:
        stream.write(json.dumps(msg, ensure_ascii=False) + "\n")
        stream.flush()

    return send


def threads() -> int:
    """CPU threads this worker may use (set by the core from its resource slot)."""
    try:
        return max(1, int(os.environ.get("TORCH_NUM_THREADS", "")))
    except ValueError:
        return max(1, len(os.sched_getaffinity(0)))
