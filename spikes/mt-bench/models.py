"""Candidates of the ru→en benchmark, pinned; `python models.py download` fetches them (online)."""

from __future__ import annotations

import sys
from dataclasses import dataclass

OPUS_DIR = (
    "/mnt/devbox/text2audio/models/assets/Helsinki-NLP/opus-mt-ru-en/"
    "fbd6dc73284f95536648512cc21d57f19191961a"
)


@dataclass(frozen=True)
class Model:
    key: str
    repo: str
    revision: str
    kind: str  # marian | nllb | madlad | towerplus | seedx | qwen3
    license: str
    llm: bool = False


MODELS = [
    Model("opus-mt", "Helsinki-NLP/opus-mt-ru-en", "local", "marian", "cc-by-4.0"),
    Model(
        "nllb-1.3b",
        "facebook/nllb-200-distilled-1.3B",
        "7be3e24664b38ce1cac29b8aeed6911aa0cf0576",
        "nllb",
        "cc-by-nc-4.0",
    ),
    Model(
        "towerplus-9b",
        "Unbabel/Tower-Plus-9B",
        "c59d17c5bbf5eb6d9f0f8ba67e0ee76b4b106ecc",
        "towerplus",
        "cc-by-nc-sa-4.0",
        llm=True,
    ),
    Model(
        "seedx-7b",
        "ByteDance-Seed/Seed-X-PPO-7B",
        "6ef78fc034ec86c0036d7a7ca2bfc24607f48050",
        "seedx",
        "openmdw",
        llm=True,
    ),
    Model(
        "qwen3-8b",
        "Qwen/Qwen3-8B",
        "b968826d9c46dd6066d109eabc6255188de91218",
        "qwen3",
        "apache-2.0",
        llm=True,
    ),
    Model(
        "nllb-3.3b",
        "facebook/nllb-200-3.3B",
        "1a07f7d195896b2114afcb79b7b57ab512e7b43e",
        "nllb",
        "cc-by-nc-4.0",
    ),
    Model(
        "madlad-3b",
        "google/madlad400-3b-mt",
        "fa184c675da0b5c9e1c8694fccd4e12e2d422094",
        "madlad",
        "apache-2.0",
    ),
]
BY_KEY = {m.key: m for m in MODELS}

COMET = ("Unbabel/wmt22-comet-da", "2760a223ac957f30acfb18c8aa649b01cf1d75f2")
# COMET builds its encoder from this name's config and tokenizer; the weights are in its ckpt.
XLMR = ("xlm-roberta-large", "c23d21b0620b635a76227c604d44e43a9f0ee389")
XLMR_FILES = ["config.json", "sentencepiece.bpe.model", "tokenizer.json", "tokenizer_config.json"]
SKIP = ["*.gguf", "*.msgpack", "*.h5", "onnx/*", "imgs/*", "*.png"]


def download() -> None:
    from huggingface_hub import snapshot_download

    for m in MODELS:
        if m.revision == "local":
            continue
        print(f"   {m.repo}@{m.revision[:8]}", flush=True)
        snapshot_download(m.repo, revision=m.revision, ignore_patterns=SKIP)
    snapshot_download(COMET[0], revision=COMET[1])
    snapshot_download(XLMR[0], revision=XLMR[1], allow_patterns=XLMR_FILES)


if __name__ == "__main__":
    if sys.argv[1:] == ["download"]:
        download()
    else:
        sys.exit("usage: models.py download")
