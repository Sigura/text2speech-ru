"""Sine worker: protocol 1 over stdin/stdout, standard library only.

Each segment becomes a deterministic tone: 60 ms per character (0.5–30 s), pitch
chosen by voice. `T2A_SINE_CRASH_ONCE=<path>` makes the first synthesize request
exit abruptly (and create <path>), so tests can exercise crash recovery.
"""

from __future__ import annotations

import json
import math
import os
import struct
import sys
import time
import wave
from pathlib import Path
from typing import Any

PROTOCOL = 1
MS_PER_CHAR = 60
PITCH_HZ = {"narrator": 220.0, "dialogue": 330.0}


def render(text: str, voice: str, out: Path, sample_rate: int) -> int:
    duration_ms = min(30_000, max(500, MS_PER_CHAR * len(text)))
    n = sample_rate * duration_ms // 1000
    freq = PITCH_HZ.get(voice, 440.0)
    frames = b"".join(
        struct.pack("<h", int(0.3 * 32767 * math.sin(2 * math.pi * freq * i / sample_rate)))
        for i in range(n)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(frames)
    return duration_ms


def handle(msg: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    if msg.get("protocol") != PROTOCOL:
        return {"ok": False, "error": f"protocol mismatch: worker speaks {PROTOCOL}"}
    op = msg.get("op")
    if op == "load":
        state["loaded"] = True
        return {"ok": True, "sample_rate": state["sample_rate"]}
    if op == "synthesize":
        if not state.get("loaded"):
            return {"ok": False, "error": "synthesize before load"}
        marker = os.environ.get("T2A_SINE_CRASH_ONCE")
        if marker and not os.path.exists(marker):
            Path(marker).touch()
            os._exit(3)
        seg = msg["segment"]
        text = seg.get("text_norm") or seg["text"]
        started = time.monotonic()
        duration = render(
            text, seg.get("voice", "narrator"), Path(msg["out"]), state["sample_rate"]
        )
        elapsed = int((time.monotonic() - started) * 1000)
        return {"ok": True, "duration_ms": duration, "elapsed_ms": elapsed}
    if op == "shutdown":
        return {"ok": True}
    return {"ok": False, "error": f"unknown op {op!r}"}


def send(msg: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    sys.stdin.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    state: dict[str, Any] = {"sample_rate": 24000}
    send({"protocol": PROTOCOL, "event": "ready", "backend": "sine"})
    for line in sys.stdin:
        try:
            msg = json.loads(line)
            reply = handle(msg, state)
        except Exception as exc:  # report, keep serving
            msg, reply = {}, {"ok": False, "error": repr(exc)}
        send(reply)
        if msg.get("op") == "shutdown" and reply["ok"]:
            break


if __name__ == "__main__":
    main()
