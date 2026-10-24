"""Worker protocol, version 1: newline-delimited JSON over stdin/stdout, UTF-8.

    worker → core   {"protocol": 1, "event": "ready", "backend": "sine"}
    core → worker   {"protocol": 1, "op": "load", "device": "cpu", "store": "/…/models"}
    worker → core   {"ok": true, "sample_rate": 24000}
    core → worker   {"protocol": 1, "op": "synthesize", "segment": {...}, "out": "/…/x.wav"}
    worker → core   {"ok": true, "duration_ms": 4120, "elapsed_ms": 1630}
    core → worker   {"protocol": 1, "op": "shutdown"}
    worker → core   {"ok": true}

Failures are {"ok": false, "error": "<message>"}. Audio is exchanged as files.
Workers implement this with the standard library only, so the core package never
has to be installed in a backend's environment.
"""

from __future__ import annotations

import json
from typing import Any

PROTOCOL = 1

Message = dict[str, Any]


class ProtocolError(Exception):
    pass


def encode(msg: Message) -> str:
    return json.dumps(msg, ensure_ascii=False) + "\n"


def decode(line: str) -> Message:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"worker sent invalid JSON: {line[:200]!r}") from exc
    if not isinstance(msg, dict):
        raise ProtocolError(f"worker sent a non-object message: {line[:200]!r}")
    return msg


def request(op: str, **fields: Any) -> Message:
    return {"protocol": PROTOCOL, "op": op, **fields}


def check_ready(msg: Message) -> None:
    if msg.get("event") != "ready":
        raise ProtocolError(f"expected a ready message, got {msg!r}")
    if msg.get("protocol") != PROTOCOL:
        raise ProtocolError(
            f"worker speaks protocol {msg.get('protocol')!r}, core speaks {PROTOCOL}; "
            "upgrade the backend or the core"
        )
