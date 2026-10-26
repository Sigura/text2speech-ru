"""Expressive hybrid route (§6.6.6, FR-24) on fixtures: `sine` renders the source audio,
`sine-polish` re-voices it and reads segments with foreign words; `sine-echo` hears
both. Chatterbox itself: test_ru_models.py."""

from __future__ import annotations

import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from text2audio import api
from text2audio.core.config import load_config
from text2audio.core.journal import Journal

FIXTURES = Path(__file__).parent / "fixtures" / "ru"
TARGET = "ch01-s0004"  # a long narration segment
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def config(tier: str, **qa: int) -> Any:
    lang = {
        "backend": {"fast": "sine", "expressive": "sine"},
        "polish": {"expressive": "sine-polish"},
        "foreign": {"expressive": "sine-polish"},
        "g2p": "backend",
        "asr": "sine-echo",
    }
    return load_config([], {"lang": {"ru": lang}, "synthesis": {"tier": tier}, "qa": qa})


def convert(tmp_path: Path, name: str) -> Path:
    [result] = api.convert([FIXTURES / name], tmp_path / "out")
    assert result.folder is not None
    return result.folder


def run(book: Path, tmp_path: Path, tier: str = "expressive", **qa: int) -> dict[str, Any]:
    return api.synthesize(book, config=config(tier, **qa), store=tmp_path / "store").report


def manifest(book: Path) -> list[dict[str, Any]]:
    lines = (book / "_work/segments.jsonl").read_text("utf-8").splitlines()
    return [json.loads(x) for x in lines]


@pytest.fixture
def book(tmp_path: Path) -> Path:
    return convert(tmp_path, "kapitanskaya-dochka.fb2")


def test_polish_route_reuses_fast_tier_audio_as_source(book: Path, tmp_path: Path) -> None:
    run(book, tmp_path, "fast")
    fast = {s["id"]: s["hash"] for s in manifest(book)}
    report = run(book, tmp_path)
    every = manifest(book)
    segs = [s for s in every if s["route"] == "sine>sine-polish"]
    assert report["tier"] == "expressive"
    assert len(segs) >= len(every) - 1  # the fixture has one segment in Latin script
    assert report["segments"]["cached"] == len(segs)  # no source rendered again
    assert report["polish"]["segments"] == len(every)
    assert report["polish"]["polished"] == len(segs)
    assert report["polish"]["foreign"] == len(every) - len(segs)
    assert report["segments"]["routes"] == dict(Counter(s["route"] for s in every))
    assert report["polish"]["backend"] == "sine-polish"
    journal = Journal(book / "_work/render.jsonl")
    for s in segs:
        entry = journal.get(s["hash"])
        assert entry is not None and entry["source"] == fast[s["id"]] != s["hash"]
    assert report["qa"]["flagged"] == report["qa"]["unpolished"] == 0
    assert "then `sine-polish`" in (book / "report.md").read_text("utf-8")


def test_edit_repolishes_only_the_edited_segment(book: Path, tmp_path: Path) -> None:
    run(book, tmp_path)
    lines = len((book / "_work/render.jsonl").read_text("utf-8").splitlines())
    md = book / f"{book.name}.md"
    md.write_text(md.read_text("utf-8").replace("Отец мой", "Батюшка мой", 1), "utf-8")
    report = run(book, tmp_path)
    assert report["segments"]["rendered_now"] == 1
    added = (book / "_work/render.jsonl").read_text("utf-8").splitlines()[lines:]
    # the new source, its polish and the polish's QA result
    assert [("source" in json.loads(x), "qa" in json.loads(x)) for x in added] == [
        (False, False),
        (True, False),
        (True, True),
    ]


@pytest.mark.skipif(shutil.which("espeak-ng") is None, reason="needs espeak-ng")
def test_foreign_words_go_to_the_foreign_backend(tmp_path: Path) -> None:
    book = convert(tmp_path, "codeswitch.md")
    report = run(book, tmp_path)
    segs = {s["text"]: s for s in manifest(book)}
    docker = segs["Я установил Docker и запустил контейнер на сервере."]
    assert docker["route"] == "sine-polish"
    assert "Docker" in docker["text_stress"]  # read in its own script
    assert "Docker" not in docker["text_norm"]  # QA compares with the transliteration
    lexicon_only = segs["Этот скрипт написан на Python, а фронтенд на React."]
    assert lexicon_only["route"] == "sine>sine-polish"
    assert report["qa"]["flagged"] == 0
    how = {s["text"]: s["resolution"] for s in report["foreign_spans"]}
    assert how["Docker"] == "foreign"  # not "transliterated": the foreign backend read it
    assert how["Python"] == "lexicon"
    foreign = report["segments"]["routes"]["sine-polish"]
    assert report["polish"]["foreign"] == foreign > 0
    assert f"{foreign} read with foreign words" in (book / "report.md").read_text("utf-8")


def test_unpolished_source_kept_when_polish_keeps_failing(
    book: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("T2A_SINE_POLISH_FAULT", "garbage")
    monkeypatch.setenv("T2A_SINE_FAULT_IDS", TARGET)
    report = run(book, tmp_path)
    assert report["qa"]["flagged"] == 0
    assert report["qa"]["unpolished"] == 1
    target = next(s for s in manifest(book) if s["id"] == TARGET)
    assert target["status"] == "done" and target["attempts"] == 3
    assert "kept unpolished" in (book / "report.md").read_text("utf-8")


def test_polish_retry_with_new_seed_fixes_segment(
    book: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("T2A_SINE_POLISH_FAULT", "garbage:2")
    monkeypatch.setenv("T2A_SINE_FAULT_IDS", TARGET)
    report = run(book, tmp_path)
    assert report["qa"]["flagged"] == report["qa"]["unpolished"] == 0
    target = next(s for s in manifest(book) if s["id"] == TARGET)
    assert target["attempts"] == 2


def test_polish_batches_reuse_one_worker_pool(
    book: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = tmp_path / "loads.log"
    monkeypatch.setenv("T2A_LOAD_LOG", str(loads))
    monkeypatch.setenv("T2A_SINE_POLISH_FAULT", "garbage:2")
    monkeypatch.setenv("T2A_SINE_FAULT_IDS", TARGET)
    report = run(book, tmp_path, batch=4, max_segments=0)
    segments = report["segments"]["total"]
    assert segments > 3 * 4  # several batches
    assert report["polish"]["segments"] == segments
    assert report["qa"]["checked"] == segments and report["qa"]["flagged"] == 0
    assert report["qa"]["retried"] == 1  # the retry is polished on the same pool
    # One sine pool for the source audio and one sine-polish pool for every batch.
    sine = [x for x in loads.read_text("utf-8").splitlines() if x.startswith("sine")]
    assert len(sine) == report["synthesis"]["workers"] + report["polish"]["workers"]
