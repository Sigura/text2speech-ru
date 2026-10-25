"""Entry points `text2audio.backends: sine` and `text2audio.asr: sine-echo` (test fixtures)."""

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


class SineEcho:
    """Fake QA ASR: returns what the sine worker said it rendered (`echo.py`)."""

    name = "sine-echo"
    component = "asr:sine-echo"
    capabilities = Capabilities(languages=("*",), devices=("cpu",), ram_mb=64, threads=1)

    def assets(self) -> list[ModelAsset]:
        return []

    def worker_command(self, python: str) -> list[str]:
        return [python, "-m", "text2audio_backend_sine.echo"]
