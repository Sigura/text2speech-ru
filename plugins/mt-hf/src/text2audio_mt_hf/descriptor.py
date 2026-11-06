"""Entry points `text2audio.translators: towerplus-9b, seedx-7b, nllb-3.3b` — book
translation (§6.11, ADR-0011).

Chosen by `spikes/mt-bench` and the owner's blind reading (§13.1): Tower-Plus-9B for
en→ru, Seed-X-PPO-7B for ru→en and wherever Tower-Plus has no room; both read a whole
paragraph. NLLB-200 3.3B translates sentence by sentence and takes over the paragraphs
an LLM fails the checks on, or the whole book when no GPU has room for an LLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from text2audio.core.contracts import Capabilities, ModelAsset

HERE = Path(__file__).parent
PAIRS = (("en", "ru"), ("ru", "en"))


class _HfTranslator:
    name: str
    component: str
    kind: str
    unit: Literal["sentence", "paragraph"]
    env = "mt-hf"
    env_project = HERE / "env"
    pairs = PAIRS
    languages = ("en", "ru")

    def assets(self) -> list[ModelAsset]:
        return []  # resolved from models.lock by `component`

    def worker_command(self, python: str) -> list[str]:
        return [python, str(HERE / "worker.py"), self.kind]


class TowerPlus(_HfTranslator):
    name = "towerplus-9b"
    component = "mt:towerplus-9b"
    kind = "towerplus"
    unit = "paragraph"
    # 19.3 GB peak on paragraphs of up to 2.7 k characters (bf16); GPU only.
    capabilities = Capabilities(languages=("en", "ru"), devices=("cuda",), vram_mb=20000)


class SeedX(_HfTranslator):
    name = "seedx-7b"
    component = "mt:seedx-7b"
    kind = "seedx"
    unit = "paragraph"
    # 15.1 GB peak on paragraphs of up to 2.7 k characters (bf16); a 7B model on CPU
    # would take hours per book, so it runs on a GPU or not at all.
    capabilities = Capabilities(languages=("en", "ru"), devices=("cuda",), vram_mb=16000)


class Nllb(_HfTranslator):
    name = "nllb-3.3b"
    component = "mt:nllb-3.3b"
    kind = "nllb"
    unit = "sentence"
    # 12.8 GB peak in bf16 on a GPU; fp32 on CPU.
    capabilities = Capabilities(
        languages=("en", "ru"), devices=("cpu", "cuda"), vram_mb=13500, ram_mb=16000, threads=8
    )
