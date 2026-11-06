"""M4 exit criterion (SPEC §11, FR-06): a public-domain English book on T0 and T1.

*The Adventures of Sherlock Holmes* in full is not committed; fetch it once with
`uv run python tests/fixtures/fetch_novel_en.py`. Same limits, excerpt and checks as the
Russian novel (`test_novel.py`).

    uv run pytest -m "models and slow" tests/e2e/test_novel_en.py -s [-k t1]
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from test_novel import WORKERS, _gpu, _limits, _run
from text2audio_asr_whisper.descriptor import Whisper
from text2audio_backend_chatterbox.descriptor import ChatterboxBackend
from text2audio_backend_kokoro.descriptor import KokoroBackend

from text2audio.core.envs import plugin_env
from text2audio.models.lock import assets_for
from text2audio.models.store import ModelStore

NOVEL = Path(__file__).parents[1] / "fixtures" / "en" / "large" / "adventures-of-sherlock-holmes.md"
STORIES = 12
PARAGRAPHS = 1  # per story: 64 segments
pytestmark = [pytest.mark.models, pytest.mark.slow]


def _missing(*plugins: tuple[object, str]) -> str | None:
    if not NOVEL.is_file():
        return "novel not fetched: uv run python tests/fixtures/fetch_novel_en.py"
    if shutil.which("ffmpeg") is None or shutil.which("taskset") is None:
        return "needs ffmpeg and taskset"
    store = ModelStore()
    for plugin, owner in ((KokoroBackend(), "kokoro"), (Whisper(), "asr:whisper"), *plugins):
        env = plugin_env(plugin)
        if not (env and env.installed) or store.verify(assets_for(owner)):
            return f"{owner}: env or assets missing"
    return None


def _missing_t1() -> str | None:
    missing = _missing((ChatterboxBackend(), "chatterbox"))
    if missing:
        return missing
    return None if _gpu() else "no GPU with enough free VRAM for Chatterbox"


@pytest.mark.skipif(_missing() is not None, reason=str(_missing()))
def test_t0(tmp_path: Path) -> None:
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
    report = _run([], env, tmp_path / "out", NOVEL, STORIES, PARAGRAPHS, workers=WORKERS)
    assert report["language"] == "en"
    assert report["tier"] == "fast" and report["qa"]["device"] == "cpu"


@pytest.mark.gpu
@pytest.mark.skipif(_missing_t1() is not None, reason=str(_missing_t1()))
def test_t1(tmp_path: Path) -> None:
    gpu = _gpu()
    assert gpu is not None
    near = sorted(gpu.cpu_affinity or range(os.cpu_count() or 8))[:8]
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": gpu.uuid}
    limits = _limits(",".join(map(str, near)), "16G")
    report = _run(limits, env, tmp_path / "out", NOVEL, STORIES, PARAGRAPHS, "expressive", WORKERS)
    assert report["language"] == "en" and report["tier"] == "expressive"
    assert report["route"]["backend"] == "chatterbox"
    assert report["segments"]["routes"]["kokoro"] > 0  # headings and short pieces
