"""Spike: `en` voice candidates for the owner's listening (M4 step 8), Kokoro on CPU.

    T2A_MODEL_HOME=… T2A_ENV_HOME=… uv run --python 3.11 python spikes/en-voices/samples.py

Narrator: one narration passage from the Sherlock Holmes fixture per female Kokoro voice
pinned in models.lock (out/narrator-<voice>.wav). Dialogue: a quoted exchange with an
attribution, the attribution in the `af_heart` narrator and the quotes in each candidate
dialogue voice, including `af_heart` itself (the `ru` choice: dialogue in the narrator's
voice), out/dialogue-<voice>.wav. Every file starts with the 1.5 s lead-in and gets an
MP3 copy to send (needs `ffmpeg` on PATH). 22 segments.
"""

from __future__ import annotations

import subprocess
import wave
from pathlib import Path

from text2audio_backend_kokoro.descriptor import KokoroBackend

from text2audio.audio.wav import LEAD_IN_MS
from text2audio.core import resources as res
from text2audio.core.contracts import Segment
from text2audio.models.lock import assets_for
from text2audio.models.store import ModelStore
from text2audio.workers.backend import Worker

HERE = Path(__file__).parent
OUT = HERE / "out"
NARRATORS = ("af_heart", "af_bella", "af_nicole", "bf_emma", "bf_isabella")
DIALOGUE = ("am_michael", "am_fenrir", "bm_george", "af_heart")
NARRATOR = "af_heart"
NARRATION = (
    "One night, it was on the twentieth of March, eighteen eighty-eight, I was returning"
    " from a journey to a patient, when my way led me through Baker Street.",
    "As I passed the well-remembered door, I was seized with a keen desire to see Holmes"
    " again, and to know how he was employing his extraordinary powers.",
)
EXCHANGE = (
    ("dialogue", "“You could not possibly have come at a better time, my dear Watson,”"),
    ("narrator", "he said cordially."),
    ("dialogue", "“I was afraid that you were engaged.” “So I am. Very much so.”"),
)


def write_wav(path: Path, parts: list[Path], gap_s: float) -> None:
    frames, rate = [], 0
    for p in parts:
        with wave.open(str(p), "rb") as w:
            rate = w.getframerate()
            frames.append(w.readframes(w.getnframes()))
    silence = b"\0\0" * int(gap_s * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * (rate * LEAD_IN_MS // 1000) + silence.join(frames))
    mp3 = path.with_suffix(".mp3")
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", str(path), "-b:a", "96k", str(mp3)]
    subprocess.run(cmd, check=True)
    print(f"wrote {mp3}")


def main() -> None:
    (OUT / "parts").mkdir(parents=True, exist_ok=True)
    store = ModelStore()
    count = 0
    with Worker(KokoroBackend(), store.root, res.WorkerSlot("cpu", 8)) as w:
        w.load(assets=store.payload(assets_for("kokoro")))

        def say(sid: str, text: str, voice: str) -> Path:
            nonlocal count
            out = OUT / "parts" / f"{sid}.wav"
            seg = Segment(id=sid, text=text, text_norm=text, lang="en")
            w.call("synthesize", segment=seg.model_dump(), out=str(out), voice_id=voice)
            count += 1
            return out

        for voice in NARRATORS:
            parts = [say(f"n-{voice}-{i}", t, voice) for i, t in enumerate(NARRATION)]
            write_wav(OUT / f"narrator-{voice}.wav", parts, 0.6)
        for voice in DIALOGUE:
            parts = [
                say(f"d-{voice}-{i}", text, voice if role == "dialogue" else NARRATOR)
                for i, (role, text) in enumerate(EXCHANGE)
            ]
            write_wav(OUT / f"dialogue-{voice}.wav", parts, 0.3)
    print(f"done: {count} segments")


if __name__ == "__main__":
    main()
