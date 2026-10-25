"""Entry point `text2audio.asr: gigaam` — GigaAM-v3 `rnnt`, spoken-form output (§6.9)."""

from __future__ import annotations

from pathlib import Path

from text2audio.core.contracts import Capabilities, ModelAsset

HERE = Path(__file__).parent


class GigaAM:
    name = "gigaam"
    component = "asr:gigaam"  # owner of its assets in models.lock
    env = "asr-gigaam"
    env_project = HERE / "env"
    languages = ("ru",)
    # Planned like a backend: one worker per GPU slot, else CPU.
    capabilities = Capabilities(
        languages=("ru",),
        devices=("cpu", "cuda"),
        vram_mb=2500,
        ram_mb=3000,
        threads=4,
        sample_rate=16000,
    )

    def assets(self) -> list[ModelAsset]:
        return []  # resolved from models.lock by `component`

    def worker_command(self, python: str) -> list[str]:
        return [python, str(HERE / "worker.py")]
