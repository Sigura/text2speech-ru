"""Entry point `text2audio.taggers: wd14` — booru-style tags of an image (§6.8.2)."""

from __future__ import annotations

from pathlib import Path

from text2audio.core.contracts import Capabilities, ModelAsset

HERE = Path(__file__).parent


class Wd14Tagger:
    name = "wd14"
    component = "tagger:wd14"  # owner of its assets in models.lock
    env = "tagger-wd14"
    env_project = HERE / "env"
    capabilities = Capabilities(languages=("*",), devices=("cpu",), ram_mb=1500, threads=4)

    def assets(self) -> list[ModelAsset]:
        return []  # resolved from models.lock by `component`

    def worker_command(self, python: str) -> list[str]:
        return [python, str(HERE / "worker.py")]
