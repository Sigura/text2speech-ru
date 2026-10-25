"""Silero TTS worker (protocol 1): normalized, stress-marked Russian text → 48 kHz WAV.

The model is a `torch.package` file from the model store, loaded with
`PackageImporter` — never `torch.hub`, which would download (§6.6.5). Silero reads
only Cyrillic letters and punctuation; digits and Latin words are dropped silently, so
the `ru` pack's normalization and FR-24 stage run before it. Stress arrives as `+`
before the vowel (RUAccent); Silero fills in only unmarked words.

Silero is deterministic, so a QA retry (`attempt` ≥ 2) cannot just change the seed: it
reads the unmarked text with Silero's own stress and homograph rules instead — the one
other reading it has — and replies `final` (§6.9).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import t2a_worker  # first: installs the network guard

SAMPLE_RATE = 48000
_READABLE = re.compile(r"[А-Яа-яЁё]")
state: dict[str, Any] = {}


def _model_file(msg: dict[str, Any]) -> Path:
    for asset in msg.get("assets", []):
        for name in asset["files"]:
            if name.endswith(".pt"):
                return Path(asset["dir"]) / name
    raise RuntimeError(
        "Silero weights not found in the model store; run: text2audio models pull --lang ru"
    )


def load(msg: dict[str, Any]) -> dict[str, Any]:
    import torch

    torch.set_num_threads(t2a_worker.threads())
    device = torch.device(msg.get("device") or "cpu")
    importer = torch.package.PackageImporter(str(_model_file(msg)))
    model = importer.load_pickle("tts_models", "model")
    model.to(device)
    state.update(model=model, torch=torch, own_stress=bool(msg.get("own_stress", False)))
    return {"sample_rate": SAMPLE_RATE}


def synthesize(msg: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    model, torch = state["model"], state["torch"]
    seg = msg["segment"]
    retry = int(msg.get("attempt", 1)) > 1
    text = seg.get("text_norm") or seg["text"]
    if not retry:
        text = seg.get("text_stress") or text
    out = msg["out"]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    warnings = []
    if not _READABLE.search(text):
        audio = np.zeros(SAMPLE_RATE // 4, dtype=np.float32)  # nothing Silero can read
        warnings.append("no Cyrillic text to read; wrote 250 ms of silence")
    else:
        torch.manual_seed(int(seg.get("seed", 1234)))
        own = state["own_stress"] or retry
        with torch.inference_mode():
            wav = model.apply_tts(
                text=text,
                speaker=msg.get("voice_id") or "xenia",
                sample_rate=SAMPLE_RATE,
                put_accent=True,  # only fills words without a "+" mark
                put_yo=True,
                put_stress_homo=own,
                put_yo_homo=own,
            )
        audio = wav.detach().cpu().numpy().astype(np.float32)
    tmp = out + ".partial.wav"
    sf.write(tmp, audio, SAMPLE_RATE, subtype="PCM_16")
    os.replace(tmp, out)
    reply: dict[str, Any] = {"duration_ms": len(audio) * 1000 // SAMPLE_RATE, "warnings": warnings}
    if retry:
        reply["final"] = True  # a further attempt would read exactly the same way
    return reply


if __name__ == "__main__":
    t2a_worker.serve("silero", {"load": load, "synthesize": synthesize})
