"""§14: with no configuration at all, every installed language pack renders a short book
that passes its own QA gate, with the tier and voices its profile picks for this
machine. The user config is an empty directory (conftest), and no book config exists."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from text2audio import api
from text2audio.core.languages import profiles
from text2audio.models.store import ModelStore

SAMPLES = {
    "ru": "# Проверка\n\nВ тысяча девятьсот двенадцатом году она прочла 25 книг.\n\n"
    "— Кто там? — спросила она.\n",
    "en": "# A Check\n\nIn 1912 she read 25 books.\n\n“Who is there?” she asked.\n",
}


def _missing(code: str) -> str | None:
    assets = api.models_list(code)
    if ModelStore().verify(assets):
        return f"models for {code} are not installed"
    absent = [e.name for e in api.envs_for(assets) if not e.installed]
    return f"environments {', '.join(absent)} are not installed" if absent else None


def test_every_language_pack_has_a_sample() -> None:
    assert set(profiles()) <= set(SAMPLES)


@pytest.mark.models
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
@pytest.mark.parametrize("code", sorted(SAMPLES))
def test_zero_config_book_passes_qa(code: str, tmp_path: Path) -> None:
    reason = _missing(code)
    if reason:
        pytest.skip(reason)
    book = tmp_path / f"check-{code}.md"
    book.write_text(SAMPLES[code], "utf-8")
    [converted] = api.convert([book], tmp_path / "out")
    assert converted.folder is not None
    report = api.synthesize(converted.folder).report
    assert report["language"] == code
    lines = (converted.folder / "_work/segments.jsonl").read_text("utf-8").splitlines()
    segs = [json.loads(x) for x in lines]
    flagged = {s["text_norm"]: s["flags"] for s in segs if s["status"] != "done"}
    assert not flagged, flagged
    assert report["qa"]["mode"] == "on" and report["qa"]["flagged"] == 0
    assert list(converted.folder.glob("*.mp3"))
