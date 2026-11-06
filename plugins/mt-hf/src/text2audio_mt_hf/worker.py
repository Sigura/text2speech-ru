"""Seed-X / NLLB worker (protocol 1): texts → another language with transformers.

    worker.py seedx|nllb

The model and tokenizer load from the pinned files in the model store by directory, so
nothing is looked up on the Hub; Seed-X ships safetensors, NLLB's `.bin` shards are
read by transformers with `torch.load(weights_only=True)` (§6.12). bf16 on a GPU, fp32
on CPU. Seed-X takes its documented prompt, greedy; NLLB 4 beams.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import t2a_worker  # first: installs the network guard

KIND = sys.argv[1] if len(sys.argv) > 1 else ""
NAMES = {"en": "English", "ru": "Russian"}
NLLB_CODES = {"en": "eng_Latn", "ru": "rus_Cyrl"}
MAX_INPUT = 1024  # tokens; a book paragraph is far shorter
state: dict[str, Any] = {}


def _snapshot(msg: dict[str, Any]) -> Path:
    for asset in msg.get("assets", []):
        if "config.json" in asset["files"]:
            return Path(asset["dir"])
    raise RuntimeError(
        f"{KIND} files not found in the model store; run: "
        "text2audio models pull --feature translate"
    )


def load(msg: dict[str, Any]) -> dict[str, Any]:
    import torch
    from transformers import AutoModelForCausalLM, AutoModelForSeq2SeqLM, AutoTokenizer

    if KIND not in ("seedx", "nllb"):
        raise RuntimeError(f"unknown model kind {KIND!r}")
    torch.set_num_threads(t2a_worker.threads())
    device = msg.get("device") or "cpu"
    dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
    root = _snapshot(msg)
    if KIND == "seedx":
        tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True, padding_side="left")
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(root, local_files_only=True, dtype=dtype)
    else:
        tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True)
        model = AutoModelForSeq2SeqLM.from_pretrained(root, local_files_only=True, dtype=dtype)
    state.update(model=model.eval().to(device), tokenizer=tokenizer, torch=torch, device=device)
    return {"device": device}


def _seedx(texts: list[str], source: str, target: str) -> list[str]:
    tokenizer, model, torch = state["tokenizer"], state["model"], state["torch"]
    prompts = [
        f"Translate the following {NAMES[source]} sentence into {NAMES[target]}:\n{t} <{target}>"
        for t in texts
    ]
    batch = tokenizer(
        prompts, return_tensors="pt", padding=True, truncation=True, max_length=MAX_INPUT
    ).to(state["device"])
    width = batch["input_ids"].shape[1]
    with torch.inference_mode():
        out = model.generate(
            **batch,
            max_new_tokens=min(2048, 2 * width + 32),
            do_sample=False,
            num_beams=1,
            pad_token_id=tokenizer.pad_token_id,
        )
    return [
        " ".join(t.split())
        for t in tokenizer.batch_decode(out[:, width:], skip_special_tokens=True)
    ]


def _nllb(texts: list[str], source: str, target: str) -> list[str]:
    tokenizer, model, torch = state["tokenizer"], state["model"], state["torch"]
    tokenizer.src_lang = NLLB_CODES[source]
    batch = tokenizer(
        texts, return_tensors="pt", padding=True, truncation=True, max_length=MAX_INPUT
    ).to(state["device"])
    with torch.inference_mode():
        out = model.generate(
            **batch,
            forced_bos_token_id=tokenizer.convert_tokens_to_ids(NLLB_CODES[target]),
            num_beams=4,
            max_new_tokens=min(1024, 2 * batch["input_ids"].shape[1] + 16),
        )
    return [" ".join(t.split()) for t in tokenizer.batch_decode(out, skip_special_tokens=True)]


def translate(msg: dict[str, Any]) -> dict[str, Any]:
    if "model" not in state:
        raise RuntimeError("translate before load")
    texts = [str(t) for t in msg["texts"]]
    source, target = str(msg["source"]), str(msg["target"])
    if source not in NAMES or target not in NAMES:
        raise RuntimeError(f"{KIND} is set up for en and ru, not {source} → {target}")
    if not texts:
        return {"texts": []}
    run = _seedx if KIND == "seedx" else _nllb
    return {"texts": run(texts, source, target)}


if __name__ == "__main__":
    t2a_worker.serve(KIND or "mt-hf", {"load": load, "translate": translate})
