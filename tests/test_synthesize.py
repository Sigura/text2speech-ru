"""`synthesize` end to end on the sine backend: MP3s, report, incremental re-render,
resume (FR-08, FR-09, FR-10, FR-17, FR-20). Real Russian models: test_ru_models.py."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from text2audio import api
from text2audio.core.config import load_config
from text2audio.core.contracts import Segment
from text2audio.core.journal import Journal
from text2audio.core.synthesize import PREVIEW_CHARS_PER_SEC, SynthesisError, preview_segments

FIXTURE = Path(__file__).parent / "fixtures" / "ru" / "kapitanskaya-dochka.fb2"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def config(**output: Any) -> Any:
    return load_config(
        [],
        {
            "lang": {"ru": {"backend": {"fast": "sine"}, "g2p": "backend", "asr": "sine-echo"}},
            "synthesis": {"tier": "fast"},
            "output": output,
        },
    )


@pytest.fixture
def book(tmp_path: Path) -> Path:
    [result] = api.convert([FIXTURE], tmp_path / "out")
    assert result.folder is not None
    return result.folder


def run(book: Path, store: Path, **output: Any) -> api.SynthesisReport:
    return api.synthesize(book, config=config(**output), store=store)


def probe(path: Path) -> dict[str, Any]:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data: dict[str, Any] = json.loads(out)
    return data


def test_single_file_with_tags_cover_and_chapters(book: Path, tmp_path: Path) -> None:
    result = run(book, tmp_path / "store", split="none")
    [mp3] = result.files
    assert mp3 == book / f"{book.name}.mp3"
    info = probe(mp3)
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (audio["codec_name"], audio["sample_rate"], audio["channels"]) == ("mp3", "44100", 1)
    cover = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert cover["disposition"]["attached_pic"] == 1
    tags = info["format"]["tags"]
    assert tags["album"] == "Капитанская дочка"
    assert tags["artist"] == "Александр Сергеевич Пушкин"
    assert tags["track"] == "1/1"
    assert [c["tags"]["title"] for c in info["chapters"]][1:] == [
        "Глава I. Сержант гвардии",
        "Глава II. Вожатый",
    ]


def test_one_file_per_chapter_with_playlist(book: Path, tmp_path: Path) -> None:
    result = run(book, tmp_path / "store", min_part_minutes=0.2)
    names = [f.name for f in result.files]
    assert names == ["01 - Глава I. Сержант гвардии.mp3", "02 - Глава II. Вожатый.mp3"]
    playlist = (book / f"{book.name}.m3u").read_text("utf-8").splitlines()
    assert playlist[0] == "#EXTM3U" and playlist[2] == names[0]
    assert probe(result.files[1])["format"]["tags"]["track"] == "2/2"


def test_report(book: Path, tmp_path: Path) -> None:
    result = run(book, tmp_path / "store")
    report = json.loads((book / "report.json").read_text("utf-8"))
    assert report == result.report
    assert report["language"] == "ru" and report["tier"] == "fast"
    assert report["segments"]["total"] == report["segments"]["rendered_now"] > 0
    assert report["synthesis"]["rtf"] is not None
    assert report["split"]["mode"] == "single"  # chapters shorter than 3 minutes
    assert [c["title"] for c in report["chapters"]][1:] == [
        "Глава I. Сержант гвардии",
        "Глава II. Вожатый",
    ]
    assert sum(c["duration_ms"] for c in report["chapters"]) == report["duration_ms"]
    assert report["segments"]["cached"] == 0
    assert report["rtf_wall"] > 0
    timings = report["timings_s"]
    assert set(timings) >= {"segment", "normalize", "stress", "synthesize", "qa", "assemble"}
    assert timings["total"] == report["wall_s"]
    qa = report["qa"]
    assert (qa["mode"], qa["asr"], qa["flagged"], qa["mean_wer"]) == ("on", "sine-echo", 0, 0)
    assert qa["checked"] == len(qa["segments"]) == report["segments"]["total"]
    md = (book / "report.md").read_text("utf-8")
    assert md.startswith("# Капитанская дочка\n")
    assert "## Quality check" in md and "**0 flagged (0.0%)**" in md
    assert "## Time per stage" in md


def test_cached_and_duplicate_counted_apart(book: Path, tmp_path: Path) -> None:
    md = book / f"{book.name}.md"
    md.write_text(md.read_text("utf-8") + "\nКонец.\n\nКонец.\n", "utf-8")
    first = run(book, tmp_path / "store").report["segments"]
    assert first["duplicate"] == 1 and first["cached"] == 0
    second = run(book, tmp_path / "store").report["segments"]
    assert second["cached"] == second["total"] and second["rendered_now"] == 0


def test_subtitles_next_to_each_mp3(book: Path, tmp_path: Path) -> None:
    result = run(book, tmp_path / "store", min_part_minutes=0.2)
    for mp3 in result.files:
        srt = mp3.with_suffix(".srt").read_text("utf-8")
        blocks = srt.strip().split("\n\n")
        first = blocks[0].splitlines()
        assert first[0] == "1" and first[1].startswith("00:00:00,300 --> ")
        last_end = blocks[-1].splitlines()[1].split(" --> ")[1]
        h, m, rest = last_end.split(":")
        end_s = int(h) * 3600 + int(m) * 60 + float(rest.replace(",", "."))
        assert end_s <= float(probe(mp3)["format"]["duration"]) + 0.1
    srt = result.files[0].with_suffix(".srt").read_text("utf-8")
    assert "Глава I. Сержант гвардии" in srt  # the book's text, not «Глава первая»


def test_subtitles_off_removes_them(book: Path, tmp_path: Path) -> None:
    [mp3] = run(book, tmp_path / "store", split="none").files
    assert mp3.with_suffix(".srt").is_file()
    run(book, tmp_path / "store", split="none", subtitles=False)
    assert not mp3.with_suffix(".srt").exists()


def test_missing_subtitles_written_without_reencoding(book: Path, tmp_path: Path) -> None:
    [mp3] = run(book, tmp_path / "store", split="none").files
    srt = mp3.with_suffix(".srt")
    expected = srt.read_text("utf-8")
    srt.unlink()
    mtime = mp3.stat().st_mtime_ns
    run(book, tmp_path / "store", split="none")
    assert srt.read_text("utf-8") == expected
    assert mp3.stat().st_mtime_ns == mtime


def test_normalized_text_in_manifest(book: Path, tmp_path: Path) -> None:
    run(book, tmp_path / "store")
    lines = (book / "_work" / "segments.jsonl").read_text("utf-8").splitlines()
    heading = json.loads(lines[1])
    assert heading["text"] == "Глава I. Сержант гвардии"
    assert heading["text_norm"] == "Глава первая. Сержант гвардии"
    assert heading["status"] == "done" and heading["hash"] and heading["duration_ms"] > 0


def test_rerun_renders_nothing_and_keeps_files(book: Path, tmp_path: Path) -> None:
    first = run(book, tmp_path / "store")
    mtime = first.files[0].stat().st_mtime_ns
    second = run(book, tmp_path / "store")
    assert second.report["segments"]["rendered_now"] == 0
    assert second.files[0].stat().st_mtime_ns == mtime  # not re-encoded


def test_edit_one_paragraph_rerenders_only_it(book: Path, tmp_path: Path) -> None:
    # FR-08: editing one paragraph re-synthesizes only the affected segments.
    run(book, tmp_path / "store")
    md = book / f"{book.name}.md"
    text = md.read_text("utf-8")
    assert "Савельич" in text
    md.write_text(text.replace("Отец мой", "Батюшка мой", 1), "utf-8")
    result = run(book, tmp_path / "store")
    assert result.report["segments"]["rendered_now"] == 1


def test_book_lexicon_changes_reading(book: Path, tmp_path: Path) -> None:
    (book / "_work" / "lexicon.tsv").write_text("Савельич\tСав+ельич\n", "utf-8")
    run(book, tmp_path / "store")
    segs = [json.loads(x) for x in (book / "_work/segments.jsonl").read_text("utf-8").splitlines()]
    assert any("Сав+ельич" in (s["text_stress"] or "") for s in segs)


def test_resume_after_interruption(book: Path, tmp_path: Path) -> None:
    # FR-10: an interrupted job continues where it stopped.
    def stop_after_three(stage: str, done: int, total: int) -> None:
        if stage == "synthesize" and done == 3:
            raise KeyboardInterrupt

    with pytest.raises(SynthesisError):
        api.synthesize(book, config=config(), store=tmp_path / "store", progress=stop_after_three)
    journal = Journal(book / "_work" / "render.jsonl")
    assert len(journal.entries) == 3
    result = run(book, tmp_path / "store")
    total = result.report["segments"]["total"]
    assert result.report["segments"]["rendered_now"] == total - 3


def test_journal_ignores_torn_last_line(tmp_path: Path) -> None:
    path = tmp_path / "render.jsonl"
    path.write_text('{"hash": "a", "duration_ms": 1}\n{"hash": "b", "dur', "utf-8")
    journal = Journal(path)
    assert "a" in journal and "b" not in journal


def test_missing_language_pack(book: Path, tmp_path: Path) -> None:
    cfg = load_config([], {"synthesis": {"language": "xx"}})
    with pytest.raises(SynthesisError, match="no language pack"):
        api.synthesize(book, config=cfg, store=tmp_path / "store")


@pytest.mark.skipif(shutil.which("espeak-ng") is None, reason="needs espeak-ng")
def test_foreign_spans_resolved_and_reported(tmp_path: Path) -> None:
    # FR-24: no Latin word reaches the backend, and report.json lists every span.
    [converted] = api.convert([FIXTURE.parent / "codeswitch.md"], tmp_path / "out")
    assert converted.folder is not None
    report = run(converted.folder, tmp_path / "store").report
    segs = [
        json.loads(x)
        for x in (converted.folder / "_work/segments.jsonl").read_text("utf-8").splitlines()
    ]
    assert not any(ch.isascii() and ch.isalpha() for s in segs for ch in s["text_norm"])
    how = {s["text"]: s["resolution"] for s in report["foreign_spans"]}
    assert how["Python"] == how["iPhone"] == how["Wi-Fi"] == "lexicon"
    assert how["HTTP"] == how["TCP"] == "acronym"
    assert how["сoбака"] == "homoglyph"
    assert how["Docker"] == how["It's not my problem"] == how["party"] == "transliterated"
    assert how["NASA"] == how["Jira"] == "lexicon"
    assert "unresolved" not in how.values()
    assert len(report["foreign_spans"]) == 21
    assert "## Foreign-script spans" in (converted.folder / "report.md").read_text("utf-8")


def test_preview_renders_the_opening_of_a_chapter_and_feeds_the_cache(
    book: Path, tmp_path: Path
) -> None:
    out = api.preview(book, seconds=20, config=config(), store=tmp_path / "store")
    assert out == book / "_work" / "preview" / "chapter-01.mp3"
    assert float(probe(out)["format"]["duration"]) > 5
    entries = Journal(book / "_work" / "render.jsonl").entries
    assert entries and all("qa" not in e for e in entries.values())  # no QA in a preview
    assert all(e["segment"].startswith("ch01-") for e in entries.values())
    assert not list(book.glob("*.mp3"))  # the book itself is not assembled
    report = run(book, tmp_path / "store")
    assert report.report["segments"]["cached"] == len(entries)


def test_preview_of_another_chapter_and_a_missing_one(book: Path, tmp_path: Path) -> None:
    out = api.preview(book, chapter=2, seconds=5, config=config(), store=tmp_path / "store")
    assert out.name == "chapter-02.mp3"
    with pytest.raises(SynthesisError, match="chapter 99"):
        api.preview(book, chapter=99, config=config(), store=tmp_path / "store")


def test_preview_segments_stop_at_the_estimated_length() -> None:
    text = "а" * (PREVIEW_CHARS_PER_SEC * 10)  # ≈ 10 s each
    segs = [Segment(id=f"s{i}", chapter=i // 5, text=text, lang="ru") for i in range(15)]
    assert [s.id for s in preview_segments(segs, None, 25)] == ["s5", "s6", "s7"]
    assert [s.id for s in preview_segments(segs, 0, 15)] == ["s0", "s1"]


def test_preview_cli(book: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from text2audio.cli import app

    (book / "_work" / "text2audio.toml").write_text(
        '[synthesis]\ntier = "fast"\n[lang.ru]\ng2p = "backend"\n'
        '[lang.ru.backend]\nfast = "sine"\n',
        "utf-8",
    )
    monkeypatch.setenv("T2A_MODEL_HOME", str(tmp_path / "store"))
    result = CliRunner().invoke(app, ["preview", str(book), "--seconds", "5", "--chapter", "1"])
    assert result.exit_code == 0, result.output
    assert str(book / "_work" / "preview" / "chapter-01.mp3") in result.output
    bad = CliRunner().invoke(app, ["preview", str(book), "--chapter", "99"])
    assert bad.exit_code == 2, bad.output
