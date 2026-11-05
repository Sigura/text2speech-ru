"""Entry point `text2audio.asr: whisper` — faster-whisper, spoken-form output (§6.9)."""

from __future__ import annotations

from pathlib import Path

from text2audio.core.contracts import Capabilities, ModelAsset

HERE = Path(__file__).parent


class Whisper:
    name = "whisper"
    component = "asr:whisper"  # owner of its assets in models.lock
    env = "asr-whisper"
    env_project = HERE / "env"
    languages = ("en",)
    # Planned like a backend: one worker per GPU slot, else CPU.
    capabilities = Capabilities(
        languages=("en",),
        devices=("cpu", "cuda"),
        vram_mb=3000,
        ram_mb=3000,
        threads=4,
        sample_rate=16000,
    )

    def assets(self) -> list[ModelAsset]:
        return []  # resolved from models.lock by `component`

    def worker_command(self, python: str) -> list[str]:
        return [python, str(HERE / "worker.py")]
