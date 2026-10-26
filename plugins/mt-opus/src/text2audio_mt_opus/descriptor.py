"""Entry point `text2audio.translators: opus-mt-ru-en` — Helsinki-NLP Opus-MT (§6.8.2)."""

from __future__ import annotations

from pathlib import Path

from text2audio.core.contracts import Capabilities, ModelAsset

HERE = Path(__file__).parent


class OpusMtRuEn:
    name = "opus-mt-ru-en"
    component = "mt:opus-mt-ru-en"  # owner of its assets in models.lock
    env = "mt-opus"
    env_project = HERE / "env"
    languages = ("ru",)
    # A few titles and blurbs per batch: one CPU worker is enough.
    capabilities = Capabilities(languages=("ru",), devices=("cpu",), ram_mb=1500, threads=4)

    def assets(self) -> list[ModelAsset]:
        return []  # resolved from models.lock by `component`

    def worker_command(self, python: str) -> list[str]:
        return [python, str(HERE / "worker.py")]
