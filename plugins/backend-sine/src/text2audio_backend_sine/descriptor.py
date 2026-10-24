"""Entry point `text2audio.backends: sine` — capabilities and worker command."""

from __future__ import annotations

from text2audio.core.contracts import Capabilities, ModelAsset


class SineBackend:
    name = "sine"
    capabilities = Capabilities(
        languages=("*",),
        voices=("narrator", "dialogue"),
        max_chars=400,
        sample_rate=24000,
        devices=("cpu",),
        ram_mb=64,
        threads=1,
    )

    def assets(self) -> list[ModelAsset]:
        return []

    def worker_command(self, python: str) -> list[str]:
        return [python, "-m", "text2audio_backend_sine.worker"]
