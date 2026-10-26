"""Entry points `text2audio.backends: chatterbox` and `text2audio.polishers: chatterbox`.

Chatterbox Multilingual (§8, spike `ru-polish`): TTS from stressed text for segments
with foreign words, and voice conversion of another backend's audio on the model's own
S3Gen (the polish stage, §6.6.6). Both run in one worker, so one model per GPU slot.
"""

from __future__ import annotations

from pathlib import Path

from text2audio.core.contracts import Capabilities, ModelAsset

HERE = Path(__file__).parent


class ChatterboxBackend:
    name = "chatterbox"
    env = "backend-chatterbox"
    env_project = HERE / "env"
    capabilities = Capabilities(
        languages=("ru", "en"),
        cloning=True,
        max_chars=300,
        sample_rate=24000,
        devices=("cuda",),
        vram_mb=5000,  # 4.3 GB peak measured (RTX 4090, TTS + VC on a short segment)
        ram_mb=6000,
        threads=2,
    )

    def assets(self) -> list[ModelAsset]:
        return []  # resolved from models.lock by backend name

    def worker_command(self, python: str) -> list[str]:
        return [python, str(HERE / "worker.py")]
