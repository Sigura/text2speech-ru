"""`sine-echo` ASR worker: protocol 1, standard library only (test fixture).

"Transcribes" a WAV by reading what the sine worker recorded for its content,
so the QA gate can be tested without a speech model. `T2A_ECHO_DELAY_MS` sleeps per
file (resume tests); `T2A_LOAD_LOG` as in the sine worker (`echo <pid>`).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from text2audio_backend_sine.worker import heard_path, log_load

PROTOCOL = 1


def transcribe(path: str) -> str:
    delay = int(os.environ.get("T2A_ECHO_DELAY_MS", "0"))
    if delay:
        time.sleep(delay / 1000)
    sidecar = heard_path(Path(path))
    return sidecar.read_text(encoding="utf-8") if sidecar.is_file() else ""


def handle(msg: dict[str, Any]) -> dict[str, Any]:
    if msg.get("protocol") != PROTOCOL:
        return {"ok": False, "error": f"protocol mismatch: worker speaks {PROTOCOL}"}
    op = msg.get("op")
    if op == "load":
        log_load("echo")
        return {"ok": True, "sample_rate": 16000}
    if op == "transcribe":
        return {"ok": True, "texts": [transcribe(p) for p in msg["paths"]]}
    if op == "shutdown":
        return {"ok": True}
    return {"ok": False, "error": f"unknown op {op!r}"}


def main() -> None:
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    print(json.dumps({"protocol": PROTOCOL, "event": "ready", "backend": "sine-echo"}), flush=True)
    for line in sys.stdin:
        try:
            msg = json.loads(line)
            reply = handle(msg)
        except Exception as exc:  # report, keep serving
            msg, reply = {}, {"ok": False, "error": repr(exc)}
        print(json.dumps(reply, ensure_ascii=False), flush=True)
        if msg.get("op") == "shutdown":
            break


if __name__ == "__main__":
    main()
