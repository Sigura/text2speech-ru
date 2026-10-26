"""Opus-MT worker (protocol 1): texts → English, with transformers' Marian model on CPU.

The model and tokenizer load from the pinned files in the model store by directory, so
nothing is looked up on the Hub; `pytorch_model.bin` is read by transformers with
`torch.load(weights_only=True)` (§6.12).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import t2a_worker  # first: installs the network guard

MAX_TOKENS = 512
state: dict[str, Any] = {}


def _snapshot(msg: dict[str, Any]) -> Path:
    for asset in msg.get("assets", []):
        if "source.spm" in asset["files"]:
            return Path(asset["dir"])
    raise RuntimeError(
        "Opus-MT files not found in the model store; run: text2audio models pull --lang ru"
    )


def load(msg: dict[str, Any]) -> dict[str, Any]:
    import torch
    from transformers import MarianMTModel, MarianTokenizer

    torch.set_num_threads(t2a_worker.threads())
    root = _snapshot(msg)
    tokenizer = MarianTokenizer.from_pretrained(root, local_files_only=True)
    model = MarianMTModel.from_pretrained(root, local_files_only=True).eval()
    state.update(model=model, tokenizer=tokenizer, torch=torch)
    return {}


def translate(msg: dict[str, Any]) -> dict[str, Any]:
    if "model" not in state:
        raise RuntimeError("translate before load")
    texts = [str(t) for t in msg["texts"]]
    if not texts:
        return {"texts": []}
    tokenizer, model, torch = state["tokenizer"], state["model"], state["torch"]
    batch = tokenizer(
        texts, return_tensors="pt", padding=True, truncation=True, max_length=MAX_TOKENS
    )
    with torch.inference_mode():
        out = model.generate(**batch, num_beams=4, max_new_tokens=MAX_TOKENS)
    return {"texts": tokenizer.batch_decode(out, skip_special_tokens=True)}


if __name__ == "__main__":
    t2a_worker.serve("opus-mt", {"load": load, "translate": translate})
