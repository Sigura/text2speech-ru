"""Entry point `text2audio.backends: kokoro` — Kokoro-82M v1.0, English (§8)."""

from __future__ import annotations

from pathlib import Path

from text2audio.core.contracts import Capabilities, ModelAsset

HERE = Path(__file__).parent


class KokoroBackend:
    name = "kokoro"
    env = "backend-kokoro"
    env_project = HERE / "env"
    # CPU only, like Silero: an 82M model, so GPUs stay free for the expressive tier and
    # for other jobs on shared machines.
    capabilities = Capabilities(
        languages=("en",),
        voices=(
            "af_heart",
            "af_bella",
            "af_nicole",
            "bf_emma",
            "bf_isabella",
            "am_michael",
            "am_fenrir",
            "bm_george",
        ),
        max_chars=400,
        sample_rate=24000,
        devices=("cpu",),
        ram_mb=1500,
        threads=4,
    )

    def assets(self) -> list[ModelAsset]:
        return []  # resolved from models.lock by backend name

    def worker_command(self, python: str) -> list[str]:
        return [python, str(HERE / "worker.py")]
