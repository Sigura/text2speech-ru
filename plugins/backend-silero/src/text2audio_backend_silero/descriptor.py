"""Entry point `text2audio.backends: silero` — Silero TTS v5.6 (§8, spike `ru-smoke`)."""

from __future__ import annotations

from pathlib import Path

from text2audio.core.contracts import Capabilities, ModelAsset

HERE = Path(__file__).parent


class SileroBackend:
    name = "silero"
    env = "backend-silero"
    env_project = HERE / "env"
    # CPU only: ≈ 12× real time on 4 cores, so GPUs stay free for heavier models and
    # for other jobs on shared machines (spike `ru-smoke`).
    capabilities = Capabilities(
        languages=("ru",),
        voices=("xenia", "baya", "kseniya", "aidar", "eugene"),
        max_chars=800,
        sample_rate=48000,
        devices=("cpu",),
        ram_mb=1500,
        threads=4,
    )

    def assets(self) -> list[ModelAsset]:
        return []  # resolved from models.lock by backend name

    def worker_command(self, python: str) -> list[str]:
        return [python, str(HERE / "worker.py")]
