"""Kokoro-82M worker (protocol 1): normalized English text → 24 kHz WAV.

Weights, config and voice tensors come from the model store; `KModel` gets their paths,
so it never calls the Hub. misaki (Kokoro's G2P) tags parts of speech with spaCy to read
heteronyms, and would download `en_core_web_sm` on first use: the pinned copy in the
store is loaded instead. Words misaki does not know go to its espeak-ng fallback.

The voice id names the accent: `a…` American, `b…` British. Kokoro is deterministic, so
a QA retry would read the same way and is answered `final` (§6.9).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import t2a_worker  # first: installs the network guard

SAMPLE_RATE = 24000
WEIGHTS = "kokoro-v1_0.pth"
SPACY_MODEL = "en_core_web_sm"
state: dict[str, Any] = {}


def _store_dir(msg: dict[str, Any], marker: str) -> Path:
    for asset in msg.get("assets", []):
        if marker in asset["files"]:
            return Path(asset["dir"])
    raise RuntimeError(
        f"Kokoro file {marker} not found in the model store; run: text2audio models pull --lang en"
    )


def _pinned_spacy(model_dir: Path) -> None:
    import spacy

    load = spacy.load
    is_package = spacy.util.is_package

    def pinned_load(name: Any, *args: Any, **kwargs: Any) -> Any:
        return load(model_dir if name == SPACY_MODEL else name, *args, **kwargs)

    spacy.load = pinned_load
    spacy.util.is_package = lambda name: name == SPACY_MODEL or is_package(name)


def load(msg: dict[str, Any]) -> dict[str, Any]:
    import torch
    from kokoro import KModel

    torch.set_num_threads(t2a_worker.threads())
    _pinned_spacy(_store_dir(msg, "meta.json"))
    root = _store_dir(msg, WEIGHTS)
    model = KModel(
        repo_id="hexgrad/Kokoro-82M", config=str(root / "config.json"), model=str(root / WEIGHTS)
    )
    model = model.to(torch.device(msg.get("device") or "cpu")).eval()
    state.update(model=model, voices=root / "voices", pipelines={}, torch=torch)
    return {"sample_rate": SAMPLE_RATE}


def _pipeline(accent: str) -> Any:
    from kokoro import KPipeline

    if accent not in state["pipelines"]:
        state["pipelines"][accent] = KPipeline(
            lang_code=accent, repo_id="hexgrad/Kokoro-82M", model=state["model"]
        )
    return state["pipelines"][accent]


def synthesize(msg: dict[str, Any]) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf

    seg = msg["segment"]
    text = (seg.get("text_norm") or seg["text"]).replace("+", "")
    voice = msg.get("voice_id") or "af_heart"
    pieces = []
    phonemes = []
    with state["torch"].inference_mode():
        for result in _pipeline(voice[0])(
            text, voice=str(state["voices"] / f"{voice}.pt"), speed=float(seg.get("rate") or 1.0)
        ):
            phonemes.append(result.phonemes)
            if result.audio is not None:
                pieces.append(result.audio.detach().cpu().numpy().astype(np.float32))
    warnings = []
    if pieces:
        audio = np.concatenate(pieces)
    else:
        audio = np.zeros(SAMPLE_RATE // 4, dtype=np.float32)
        warnings.append("nothing to read; wrote 250 ms of silence")
    out = msg["out"]
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    tmp = out + ".partial.wav"
    sf.write(tmp, audio, SAMPLE_RATE, subtype="PCM_16")
    os.replace(tmp, out)
    reply: dict[str, Any] = {
        "duration_ms": len(audio) * 1000 // SAMPLE_RATE,
        "warnings": warnings,
        "phonemes": " ".join(phonemes),  # what misaki read, for tests and debugging
    }
    if int(msg.get("attempt", 1)) > 1:
        reply["final"] = True  # deterministic: a further attempt would read the same way
    return reply


if __name__ == "__main__":
    t2a_worker.serve("kokoro", {"load": load, "synthesize": synthesize})
