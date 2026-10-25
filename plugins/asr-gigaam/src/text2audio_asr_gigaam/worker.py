"""GigaAM-v3 `rnnt` worker (protocol 1): WAV files → spoken-form Russian transcripts.

The pinned `modeling_gigaam.py` is loaded from the model store as a module and the
model is built from the pinned `config.json` with hydra — no `trust_remote_code`, no
Hub lookups, no `pyannote.audio` (only its long-form helper needs it) and no ffmpeg
shell-out: audio is read with soundfile and resampled with torchaudio. Weights load with
`torch.load(weights_only=True)` (§6.12).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import t2a_worker  # first: installs the network guard

RATE = 16000
MAX_SECONDS = 25  # the model's short-form limit; longer audio is cut into windows
state: dict[str, Any] = {}


def _snapshot(msg: dict[str, Any]) -> Path:
    for asset in msg.get("assets", []):
        if "modeling_gigaam.py" in asset["files"]:
            return Path(asset["dir"])
    raise RuntimeError(
        "GigaAM files not found in the model store; run: text2audio models pull --lang ru"
    )


def load(msg: dict[str, Any]) -> dict[str, Any]:
    import hydra
    import omegaconf
    import torch

    torch.set_num_threads(t2a_worker.threads())
    root = _snapshot(msg)
    spec = importlib.util.spec_from_file_location("modeling_gigaam", root / "modeling_gigaam.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["modeling_gigaam"] = module  # hydra `_target_`s name this module
    spec.loader.exec_module(module)
    config = json.loads((root / "config.json").read_text(encoding="utf-8"))
    cfg = omegaconf.OmegaConf.create(config["cfg"]["model"])
    model = hydra.utils.instantiate(cfg, _recursive_=False)
    weights = torch.load(root / "pytorch_model.bin", map_location="cpu", weights_only=True)
    weights = {k.removeprefix("model."): v for k, v in weights.items()}
    model.load_state_dict(weights)
    device = torch.device(msg.get("device") or "cpu")
    model = model.to(device).eval()
    state.update(model=model, torch=torch, device=device)
    return {"sample_rate": RATE}


def _audio(path: str) -> Any:
    import soundfile as sf
    import torch
    import torchaudio

    data, rate = sf.read(path, dtype="float32", always_2d=True)
    wav = torch.from_numpy(data.mean(axis=1))
    if rate != RATE:
        wav = torchaudio.functional.resample(wav, rate, RATE)
    return wav


def _transcribe(wav: Any) -> str:
    model, torch, device = state["model"], state["torch"], state["device"]
    window = MAX_SECONDS * RATE
    texts = []
    with torch.inference_mode():
        for start in range(0, max(1, wav.shape[-1]), window):
            piece = wav[start : start + window]
            if piece.shape[-1] < RATE // 10:
                continue
            x = piece.to(device).to(model._dtype).unsqueeze(0)
            length = torch.full([1], x.shape[-1], device=device)
            encoded, encoded_len = model.forward(x, length)
            texts.append(model.decoding.decode(model.head, encoded, encoded_len)[0])
    return " ".join(t for t in texts if t)


def transcribe(msg: dict[str, Any]) -> dict[str, Any]:
    if "model" not in state:
        raise RuntimeError("transcribe before load")
    return {"texts": [_transcribe(_audio(p)) for p in msg["paths"]]}


if __name__ == "__main__":
    t2a_worker.serve("gigaam", {"load": load, "transcribe": transcribe})
