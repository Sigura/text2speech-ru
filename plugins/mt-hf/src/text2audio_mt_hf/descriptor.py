"""Entry points `text2audio.translators: seedx-7b, nllb-3.3b` — book translation (§6.11).

Chosen by `spikes/mt-bench` (§13.1): Seed-X-PPO-7B leads both directions and reads a
whole paragraph; NLLB-200 3.3B translates sentence by sentence and takes over the
paragraphs Seed-X fails the checks on, or the whole book when no GPU has room for it.
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
