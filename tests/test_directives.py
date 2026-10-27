"""`<!-- t2a: … -->` directives (SPEC §6.4.1, §6.7 E3): parser, reader, segment
effects, and what a synthesis run re-renders and reports (sine backend)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from text2audio import api
from text2audio.core.config import load_config
from text2audio.core.contracts import (
    Capabilities,
    LanguageProfile,
    ModelAsset,
    Segment,
    TierRoute,
)
from text2audio.core.synthesize import Plan, _directive_voices
from text2audio.readers.document import Directive, Inline, Paragraph
from text2audio.readers.markdown import parse_markdown
from text2audio.text import directives
from text2audio.text.segment import segment_markdown

RU = LanguageProfile(
    code="ru",
    name="Русский",
    script="Cyrl",
    voices={"narrator": {"silero": "xenia"}, "dialogue": {"silero": "xenia"}},
    dialogue="dash",
)


def seg(md: str, warnings: list[dict[str, str]] | None = None) -> list[Segment]:
    return segment_markdown(md, "ru", RU, warnings=warnings)


# --------------------------------------------------------------------------- parser


def test_parse_all_keys() -> None:
    d = directives.parse(" voice=baya pause=1500 rate=0.9 emotion=Calm intensity=0.7 skip ")
    assert (d.voice, d.pause_ms, d.rate, d.emotion, d.intensity) == ("baya", 1500, 0.9, "calm", 0.7)
    assert d.skip and not d.reset and not d.warnings
    assert directives.parse('voice="baya"').voice == "baya"


@pytest.mark.parametrize(
    ("body", "warning"),
    [
        ("loud=1", "unknown key 'loud'"),
        ("rate=3", "rate='3' ignored"),
        ("rate=fast", "rate='fast' ignored"),
        ("rate=nan", "rate='nan' ignored"),
        ("pause=-5", "pause='-5' ignored"),
        ("intensity=2", "intensity='2' ignored"),
        ("voice", "voice needs a value"),
        ("skip=yes", "skip takes no value"),
    ],
)
def test_parse_malformed(body: str, warning: str) -> None:
    d = directives.parse(f"{body} pause=100")
    assert len(d.warnings) == 1 and warning in d.warnings[0]
    assert d.pause_ms == 100  # the rest of the directive applies
    assert d.rate is None and not d.skip


def test_settings_reset_and_override() -> None:
    s = directives.Settings().apply(directives.parse("voice=a rate=0.8 emotion=calm"))
    s = s.apply(directives.parse("rate=1.2"))
    assert (s.voice, s.rate, s.emotion) == ("a", 1.2, "calm")
    s = s.apply(directives.parse("reset voice=b"))
    assert s == directives.Settings(voice="b")


# --------------------------------------------------------------------------- reader


def test_reader_keeps_directives_only_when_asked() -> None:
    md = (
        "Раз.\n\n<!-- t2a: voice=a -->\n<!-- other -->\n<!-- t2a: pause=5 -->\n\n"
        "Два <!-- t2a: rate=1.1 -->три.\n"
    )
    doc = parse_markdown(md, directives=True)
    assert doc.blocks[1:3] == [Directive(" voice=a ", 3), Directive(" pause=5 ", 5)]
    last = doc.blocks[3]
    assert isinstance(last, Paragraph)
    assert Inline("directive", " rate=1.1 ") in last.inlines
    plain_doc = parse_markdown(md)
    assert [type(b) for b in plain_doc.blocks] == [Paragraph, Paragraph]
    paragraphs = [b for b in plain_doc.blocks if isinstance(b, Paragraph)]
    assert all(i.kind != "directive" for b in paragraphs for i in b.inlines)


def test_ordinary_comments_change_nothing() -> None:
    md = "## Глава\n\nПервое. Второе.\n"
    commented = "## Глава\n\n<!-- just a note -->\n\nПервое. <!-- x --> Второе.\n"
    assert seg(md) == seg(commented)


# --------------------------------------------------------------------------- segments


def test_block_scope_until_reset() -> None:
    md = (
        "## Глава\n\nДо.\n\n"
        "<!-- t2a: voice=dialogue rate=0.8 emotion=fear -->\n\n"
        "Первый.\n\n> Цитата.\n\n## Глава 2\n\nВторой.\n\n"
        "<!-- t2a: reset -->\n\nПосле.\n"
    )
    out = {s.text: s for s in seg(md)}
    assert (out["До."].voice, out["До."].rate, out["До."].emotion) == ("narrator", 1.0, None)
    for text in ("Первый.", "Цитата.", "Глава 2", "Второй."):
        s = out[text]
        assert (s.voice, s.rate, s.emotion, s.intensity) == ("dialogue", 0.8, "fear", 0.5)
    assert (out["После."].voice, out["После."].rate, out["После."].emotion) == (
        "narrator",
        1.0,
        None,
    )


def test_inline_applies_to_its_sentence_only() -> None:
    md = "## Глава\n\nРаз два. Три <!-- t2a: voice=dialogue -->четыре. Пять. Шесть.\n"
    out = [(s.text, s.voice, s.pause) for s in seg(md)][1:]
    assert out == [
        ("Раз два.", "narrator", "sentence"),
        ("Три четыре.", "dialogue", "sentence"),
        ("Пять. Шесть.", "narrator", "paragraph"),
    ]


def test_inline_between_sentences_belongs_to_the_one_before() -> None:
    md = "## Глава\n\nРаз. <!-- t2a: pause=1500 --> Два.\n\n<!-- t2a: rate=1.5 -->Три. Четыре.\n"
    out = [(s.text, s.pause_after_ms, s.rate) for s in seg(md)][1:]
    assert out == [
        ("Раз.", 1500, 1.0),
        ("Два.", 700, 1.0),
        ("Три.", 300, 1.5),
        ("Четыре.", 700, 1.0),
    ]


def test_pause_replaces_the_structural_pause() -> None:
    md = (
        "## Глава\n\nРаз.\n\n<!-- t2a: pause=2500 -->\n\n* * *\n\n"
        "Два.\n\n<!-- t2a: pause=0 -->\n\n## Глава 2\n\nТри.\n"
    )
    out = {s.text: s for s in seg(md)}
    # the boundary keeps its type (the split plan reads it), the length is the directive's
    assert (out["Раз."].pause, out["Раз."].pause_after_ms) == ("scene", 2500)
    assert (out["Два."].pause, out["Два."].pause_after_ms) == ("chapter", 0)


def test_skip_drops_text_but_keeps_the_boundary() -> None:
    md = (
        "## Глава\n\nРаз.\n\n<!-- t2a: skip -->\n\nНе читать.\n\n* * *\n\n"
        "<!-- t2a: reset -->\n\nДва. Три <!-- t2a: skip -->тоже нет. Четыре.\n"
    )
    out = [(s.text, s.pause) for s in seg(md)][1:]
    assert out == [("Раз.", "scene"), ("Два.", "sentence"), ("Четыре.", "paragraph")]


def test_dialogue_line_with_inline_emotion() -> None:
    md = (
        "## Глава\n\n— Кто там? — спросила она. — Никого<!-- t2a: emotion=fear intensity=0.9 -->.\n"
    )
    out = [(s.role, s.text, s.emotion, s.intensity) for s in seg(md)][1:]
    assert out == [
        ("dialogue", "— Кто там?", None, 0.0),
        ("remark", "— спросила она.", None, 0.0),
        ("dialogue", "— Никого.", "fear", 0.9),
    ]


def test_inline_in_heading_and_poem() -> None:
    md = (
        "## Глава <!-- t2a: rate=0.7 -->\n\n"
        "Строка одна,\\\nстрока <!-- t2a: voice=dialogue -->две.\n\nПроза.\n"
    )
    out = {s.text: s for s in seg(md)}
    assert out["Глава"].rate == 0.7
    assert out["Строка одна,\nстрока две."].voice == "dialogue"
    assert out["Проза."].voice == "narrator" and out["Проза."].rate == 1.0


def test_warnings_name_the_line_or_segment() -> None:
    warnings: list[dict[str, str]] = []
    md = "## Глава\n\n<!-- t2a: volume=3 -->\n\nРаз. Два <!-- t2a: rate=9 -->три.\n"
    seg(md, warnings)
    assert warnings == [
        {"segment": "book.md:3", "warning": "t2a: unknown key 'volume' ignored"},
        {
            "segment": "ch01-s0003",
            "warning": "t2a: rate='9' ignored: expected a number in 0.5–2",
        },
    ]


# --------------------------------------------------------------------------- voices


class _Backend:
    name = "silero"
    capabilities = Capabilities(languages=("ru",), voices=("xenia", "baya"))

    def assets(self) -> list[ModelAsset]:
        return []

    def worker_command(self, python: str) -> list[str]:
        return []


def _plan(polisher: Any = None) -> Plan:
    return Plan(
        lang="ru",
        profile=RU,
        tier="fast",
        route=TierRoute(backend="silero"),
        backend=_Backend(),
        normalizer=None,
        g2p=None,
        polisher=polisher,
    )


def test_backend_voice_id_becomes_a_role_and_unknown_falls_back() -> None:
    segs = [
        Segment(id="a", text="x", lang="ru", voice="baya"),
        Segment(id="b", text="x", lang="ru", voice="anna"),
        Segment(id="c", text="x", lang="ru", voice="dialogue"),
    ]
    notes: list[dict[str, str]] = []
    prof = _directive_voices(_plan(polisher=_Backend()), segs, notes)
    assert prof.voices["baya"] == {"silero": "baya"}
    assert [s.voice for s in segs] == ["baya", "narrator", "dialogue"]
    assert [n["segment"] for n in notes] == ["b", "a"]
    assert "unknown voice 'anna'" in notes[0]["warning"]
    assert "not polished" in notes[1]["warning"]


# --------------------------------------------------------------------------- synthesis

BOOK = """# Директивы

## Глава 1

Первый абзац читает рассказчик.

Второй абзац тоже читает рассказчик.

Третий абзац, последний в главе.
"""


def _config() -> Any:
    return load_config(
        [],
        {
            "lang": {"ru": {"backend": {"fast": "sine"}, "g2p": "backend", "asr": "sine-echo"}},
            "synthesis": {"tier": "fast"},
            "output": {"formats": ["mp3"]},
        },
    )


@pytest.fixture
def book(tmp_path: Path) -> Path:
    src = tmp_path / "directives.md"
    src.write_text(BOOK, "utf-8")
    [result] = api.convert([src], tmp_path / "out", lang="ru")
    assert result.folder is not None
    return result.folder


def _edit(book: Path, old: str, new: str) -> None:
    md = book / f"{book.name}.md"
    text = md.read_text("utf-8")
    assert old in text
    md.write_text(text.replace(old, new, 1), "utf-8")


def _mp3_ms(path: Path) -> int:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return round(float(json.loads(out)["format"]["duration"]) * 1000)


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@needs_ffmpeg
def test_rerender_limited_to_affected_segments(book: Path, tmp_path: Path) -> None:
    store = tmp_path / "store"
    first = api.synthesize(book, config=_config(), store=store)
    assert first.report["warnings"] == []
    base_ms = first.report["duration_ms"]

    # voice: only the paragraph after the directive is rendered again
    _edit(book, "Второй абзац", "<!-- t2a: voice=dialogue -->\n\nВторой абзац")
    _edit(book, "Третий абзац", "<!-- t2a: reset -->\n\nТретий абзац")
    second = api.synthesize(book, config=_config(), store=store)
    assert second.report["segments"]["rendered_now"] == 1
    manifest = [
        json.loads(x) for x in (book / "_work/segments.jsonl").read_text("utf-8").splitlines()
    ]
    assert [s["voice"] for s in manifest][-3:] == ["narrator", "dialogue", "narrator"]

    # pause and rate: nothing to render, the audio is re-assembled
    _edit(book, "<!-- t2a: reset -->", "<!-- t2a: reset pause=3000 -->")
    third = api.synthesize(book, config=_config(), store=store)
    assert third.report["segments"]["rendered_now"] == 0
    assert third.report["duration_ms"] == base_ms - 800 + 3000  # paragraph pause → 3000

    mp3 = third.files[0]
    before = _mp3_ms(mp3)
    _edit(book, "Первый абзац", "<!-- t2a: rate=0.5 -->Первый абзац")
    fourth = api.synthesize(book, config=_config(), store=store)
    assert fourth.report["segments"]["rendered_now"] == 0
    speech = 60 * len("Первый абзац читает рассказчик.")  # sine: 60 ms per character
    assert fourth.report["duration_ms"] == third.report["duration_ms"] + speech
    assert abs(_mp3_ms(mp3) - before - speech) < 150


@needs_ffmpeg
def test_directive_warnings_in_report(book: Path, tmp_path: Path) -> None:
    _edit(book, "Второй абзац", "<!-- t2a: volume=3 voice=anna emotion=joy -->\n\nВторой абзац")
    result = api.synthesize(book, config=_config(), store=tmp_path / "store")
    warnings = [w["warning"] for w in result.report["warnings"]]
    assert any("unknown key 'volume'" in w for w in warnings)
    assert any("unknown voice 'anna'" in w for w in warnings)
    assert any(
        "emotion=joy ignored on 2 segment(s): sine has no emotion control" in w for w in warnings
    )
    md = (book / "report.md").read_text("utf-8")
    assert "unknown key 'volume'" in md
