"""Spike: one long WAV per variant for listening (all sentences, 48 kHz, lead-in silence).

    uv run --project chatterbox python listen_concat.py

Short clips that start speaking within 50 ms, at mixed sample rates, made players drop
the first second of audio; one file per variant at a single rate avoids that.
Output: out/listen/full/<label>.wav (+ sentence order in README.md).
"""

from __future__ import annotations

import json
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf

HERE = Path(__file__).parent
RATE = 48000
LEAD_S, GAP_S = 1.0, 1.2
LABELS = {
    "silero-xenia-own": "A-silero",
    "silero-xenia-own-enh": "B-silero-enhanced",
    "silero-vc-builtin": "C-silero-vc-builtin",
    "silero-vc-xenia": "C2-silero-vc-xenia-ref",
    "chatterbox-default": "D1-chatterbox-builtin",
    "chatterbox-xenia": "D2-chatterbox-xenia-ref",
    "chatterbox-xenia-ru": "D3-chatterbox-xenia-ref-stressed",
}


def main() -> None:
    rows = json.loads((HERE / "out/report.json").read_text())["rows"]
    out_dir = HERE / "out/listen/full"
    out_dir.mkdir(parents=True, exist_ok=True)
    order: list[str] = []
    for variant, label in LABELS.items():
        mine = sorted(
            (r for r in rows if r["variant"] == variant and r["set"] == "main"),
            key=lambda r: r["i"],
        )
        parts = [np.zeros(int(LEAD_S * RATE))]
        for r in mine:
            audio, _ = librosa.load(str(HERE.parent / r["wav"]), sr=RATE)
            parts += [audio, np.zeros(int(GAP_S * RATE))]
        sf.write(str(out_dir / f"{label}.wav"), np.concatenate(parts), RATE)
        order = [f"{r['i']:02d}. {r['text']}" for r in mine]
        print(f"{label}: {len(mine)} sentences")
    readme = ["# Full-length listening files (48 kHz, sentences in this order)", "", *order]
    (out_dir / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
