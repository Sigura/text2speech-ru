"""Sine worker: protocol 1 over stdin/stdout, standard library only.

Each segment becomes a deterministic tone: 60 ms per character (0.5–30 s), pitch
chosen by voice. Next to each WAV it writes `<wav>.heard.txt`, what an ASR "hears"
(read back by the `sine-echo` ASR in `echo.py`), so tests can run the QA gate.

Test hooks (environment):
- `T2A_SINE_CRASH_ONCE=<path>`: the first synthesize request exits abruptly (and
  creates <path>), to exercise crash recovery.
- `T2A_SINE_FAULT=<mode>[:<n>]`: fault injection for the QA gate (§12) — `garbage`
  (right length, wrong words), `silence` (zeros, nothing heard) or `truncate` (a third
  of the audio and words); with `:<n>`, attempts ≥ n are good again.
  `T2A_SINE_FAULT_IDS=<id>,<id>` limits it to those segments.
- `T2A_SINE_DELAY_MS`: sleep per segment; `T2A_SINE_LOG=<path>`: append
  `<hash> <id> <attempt>` per rendered segment (resume tests).
- `T2A_SINE_FINAL=1`: retries reply `final`, like a deterministic backend.
"""

from __future__ import annotations

import hashlib
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
GARBAGE = ["шкаф", "луна", "дорога", "пингвин", "север", "ложка", "трамвай", "облако"]


def fault(seg_id: str, attempt: int) -> str | None:
    spec = os.environ.get("T2A_SINE_FAULT")
    if not spec:
        return None
    mode, _, good_from = spec.partition(":")
    ids = os.environ.get("T2A_SINE_FAULT_IDS")
    if ids and seg_id not in ids.split(","):
        return None
    if good_from and attempt >= int(good_from):
        return None
    return mode


def heard(text: str, mode: str | None) -> str:
    words = text.split()
    if mode == "silence":
        return ""
    if mode == "truncate":
        return " ".join(words[: len(words) // 3])
    if mode == "garbage":
        seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
        return " ".join(GARBAGE[(seed + i) % len(GARBAGE)] for i in range(len(words)))
    return text


def render(text: str, voice: str, out: Path, sample_rate: int, mode: str | None = None) -> int:
    duration_ms = min(30_000, max(500, MS_PER_CHAR * len(text)))
    if mode == "truncate":
        duration_ms //= 3
    n = sample_rate * duration_ms // 1000
    freq = PITCH_HZ.get(voice, 440.0)
    amplitude = 0.0 if mode == "silence" else 0.3
    frames = b"".join(
        struct.pack("<h", int(amplitude * 32767 * math.sin(2 * math.pi * freq * i / sample_rate)))
        for i in range(n)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(frames)
    Path(f"{out}.heard.txt").write_text(heard(text, mode), encoding="utf-8")
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
        attempt = int(msg.get("attempt", 1))
        started = time.monotonic()
        delay = int(os.environ.get("T2A_SINE_DELAY_MS", "0"))
        if delay:
            time.sleep(delay / 1000)
        duration = render(
            text,
            seg.get("voice", "narrator"),
            Path(msg["out"]),
            state["sample_rate"],
            fault(seg.get("id", ""), attempt),
        )
        log = os.environ.get("T2A_SINE_LOG")
        if log:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"{seg.get('hash')} {seg.get('id')} {attempt}\n")
        elapsed = int((time.monotonic() - started) * 1000)
        reply = {"ok": True, "duration_ms": duration, "elapsed_ms": elapsed}
        if attempt > 1 and os.environ.get("T2A_SINE_FINAL") == "1":
            reply["final"] = True  # behave like a deterministic backend (Silero)
        return reply
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
