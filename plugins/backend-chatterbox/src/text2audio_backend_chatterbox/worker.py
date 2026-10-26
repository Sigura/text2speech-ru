"""Chatterbox worker (protocol 1): Multilingual TTS and voice conversion, 24 kHz WAV.

`load` builds `ChatterboxMultilingualTTS` from the model store files (`weights_only`
loads); the VC converter shares the TTS model's S3Gen, so one model serves both ops.
Two parts of the stock tokenizer reach the network for Chinese only: the Cangjie table
(`hf_hub_download`) is read from the store instead, and the pkuseg segmenter (downloads
on first use) is turned off.

- `synthesize`: stressed text (`+` before the vowel → U+0301 after it, which Chatterbox
  reads as stress), `language_id` "en" when most letters are Latin, else "ru"; the
  voice is cloned from the reference clip `voice_id`. Sampling is seeded from the
  segment seed and the attempt, so a QA retry reads differently.
- `polish`: re-voices `source` (another backend's WAV) into the reference voice.
"""

from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path
from typing import Any

import t2a_worker  # first: installs the network guard

FILES = (
    "ve.pt",
    "t3_mtl23ls_v2.safetensors",
    "s3gen.pt",
    "grapheme_mtl_merged_expanded_v1.json",
    "conds.pt",
    "Cangjie5_TC.json",
)
_LATIN = re.compile(r"[A-Za-z]")
_LETTER = re.compile(r"[^\W\d_]")
state: dict[str, Any] = {}


def _model_dir(msg: dict[str, Any]) -> Path:
    for asset in msg.get("assets", []):
        if set(FILES) <= set(asset["files"]):
            return Path(asset["dir"])
    raise RuntimeError(
        "Chatterbox weights not found in the model store; "
        "run: text2audio models pull --lang ru --tier expressive"
    )


def _patch_tokenizer(model_dir: Path) -> None:
    from chatterbox.models.tokenizers import tokenizer

    def load_cangjie(self: Any, _model_dir: Any = None) -> None:
        data = json.loads((model_dir / "Cangjie5_TC.json").read_text(encoding="utf-8"))
        for entry in data:
            word, code = entry.split("\t")[:2]
            self.word2cj[word] = code
            self.cj2word.setdefault(code, []).append(word)

    tokenizer.ChineseCangjieConverter._load_cangjie_mapping = load_cangjie
    tokenizer.ChineseCangjieConverter._init_segmenter = lambda self: setattr(
        self, "segmenter", None
    )


def load(msg: dict[str, Any]) -> dict[str, Any]:
    import torch

    torch.set_num_threads(t2a_worker.threads())
    model_dir = _model_dir(msg)
    _patch_tokenizer(model_dir)
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS
    from chatterbox.vc import ChatterboxVC

    device = msg.get("device") or "cuda"
    tts = ChatterboxMultilingualTTS.from_local(model_dir, device)
    vc = ChatterboxVC(tts.s3gen, device)
    state.update(torch=torch, tts=tts, vc=vc, tts_ref=None, vc_ref=None)
    return {"sample_rate": tts.sr}


def _seed(seg: dict[str, Any], attempt: int) -> None:
    seed = int(seg.get("seed", 1234)) + 1000 * (attempt - 1)
    random.seed(seed)
    state["torch"].manual_seed(seed)


def _write(audio: Any, out: str) -> int:
    import soundfile as sf

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    rate = state["tts"].sr
    tmp = out + ".partial.wav"
    sf.write(tmp, audio, rate, subtype="PCM_16")
    os.replace(tmp, out)
    return len(audio) * 1000 // rate


def _reference(msg: dict[str, Any]) -> str:
    ref = msg.get("voice_id") or ""
    if not os.path.isfile(ref):
        raise RuntimeError(f"Chatterbox needs a reference clip as voice_id, got {ref!r}")
    return ref


def language_id(text: str) -> str:
    letters = len(_LETTER.findall(text))
    return "en" if letters and len(_LATIN.findall(text)) * 2 >= letters else "ru"


def plus_to_acute(text: str) -> str:
    return re.sub(r"\+(\w)", "\\1\u0301", text)


def synthesize(msg: dict[str, Any]) -> dict[str, Any]:
    tts, torch = state["tts"], state["torch"]
    seg = msg["segment"]
    text = seg.get("text_stress") or seg.get("text_norm") or seg["text"]
    ref = _reference(msg)
    if state["tts_ref"] != ref:
        tts.prepare_conditionals(ref)
        state["tts_ref"] = ref
    _seed(seg, int(msg.get("attempt", 1)))
    with torch.inference_mode():
        wav = tts.generate(plus_to_acute(text), language_id=language_id(text))
    audio = wav.squeeze(0).detach().cpu().numpy()
    return {"duration_ms": _write(audio, msg["out"]), "warnings": []}


def polish(msg: dict[str, Any]) -> dict[str, Any]:
    vc, torch = state["vc"], state["torch"]
    ref = _reference(msg)
    if state["vc_ref"] != ref:
        vc.set_target_voice(ref)
        state["vc_ref"] = ref
    _seed(msg.get("segment") or {}, int(msg.get("attempt", 1)))
    with torch.inference_mode():
        wav = vc.generate(msg["source"])
    audio = wav.squeeze(0).detach().cpu().numpy()
    return {"duration_ms": _write(audio, msg["out"]), "warnings": []}


if __name__ == "__main__":
    t2a_worker.serve("chatterbox", {"load": load, "synthesize": synthesize, "polish": polish})
