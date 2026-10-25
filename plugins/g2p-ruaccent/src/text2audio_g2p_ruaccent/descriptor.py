"""Entry point `text2audio.g2p: ruaccent` — Russian stress marks and ё (§6.6.5)."""

from __future__ import annotations

from pathlib import Path

from text2audio.core.contracts import ModelAsset

HERE = Path(__file__).parent


class RuAccent:
    name = "ruaccent"
    component = "g2p:ruaccent"  # owner of its assets in models.lock
    env = "g2p-ruaccent"
    env_project = HERE / "env"
    languages = ("ru",)
    threads = 2

    def assets(self) -> list[ModelAsset]:
        return []  # resolved from models.lock by `component`

    def worker_command(self, python: str) -> list[str]:
        return [python, str(HERE / "worker.py")]
