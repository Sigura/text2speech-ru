"""Sentences, chunks and the segment manifest (§6.4.2, §6.5, FR-08)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from conftest import FIXTURES

from text2audio.core.convert import convert_one
from text2audio.core.languages import profile
from text2audio.readers.document import Heading, Paragraph, Poem, Quote, plain
from text2audio.readers.markdown import parse_markdown
from text2audio.text.segment import read_manifest, segment_markdown
from text2audio.text.sentences import pack, split_sentences

RU = profile("ru")
ABBREVS = RU.abbreviations if RU else ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "— Эх, батюшка Петр Андреич! — отвечал он с глубоким вздохом. — Сержусь-то я.",
            ["— Эх, батюшка Петр Андреич! — отвечал он с глубоким вздохом.", "— Сержусь-то я."],
        ),
        (
            "Я сказал: «Ну, ну, Савельич! полно». Он ушёл.",
            ["Я сказал: «Ну, ну, Савельич! полно».", "Он ушёл."],
        ),
        (
            "А. С. Пушкин родился в г. Москве. Т. е. давно.",
            ["А. С. Пушкин родился в г. Москве.", "Т. е. давно."],
        ),
        ("В 17.. году. Что? «Да!» Ладно…", ["В 17.. году.", "Что?", "«Да!»", "Ладно…"]),
        (
            "Mr. Smith left. He came back!",
            ["Mr.", "Smith left.", "He came back!"],
        ),  # no en pack yet
    ],
)
def test_split_sentences(text: str, expected: list[str]) -> None:
    assert split_sentences(text, ABBREVS) == expected


def test_pack_respects_the_limit_and_keeps_every_word() -> None:
    long_sentence = "Слово, " * 80 + "конец."
    text = "Короткое предложение. " + long_sentence + " Ещё одно."
    chunks = pack(split_sentences(text, ABBREVS), 100)
    assert all(len(c) <= 100 for c in chunks)
    assert " ".join(chunks).split() == text.split()


def _plain_text(markdown: str) -> list[str]:
    doc = parse_markdown(markdown)
    words: list[str] = []

    def walk(blocks: list) -> None:  # type: ignore[type-arg]
        for b in blocks:
            if isinstance(b, Heading | Paragraph):
                words.extend(plain(b.inlines).split())
            elif isinstance(b, Quote):
                walk(b.blocks)
                words.extend(plain(b.author or []).split())
            elif isinstance(b, Poem):
                words.extend(w for st in b.stanzas for line in st for w in plain(line).split())

    walk(doc.blocks)
    for blocks in doc.footnotes.values():
        walk(blocks)
    return words


@pytest.mark.parametrize("case", ["ru/kapitanskaya-dochka.fb2", "ru/blank-lines-parts.txt"])
def test_segments_lose_no_word(case: str, tmp_path: Path) -> None:
    result = convert_one(FIXTURES / case, tmp_path)
    assert result.folder is not None and result.markdown is not None
    segments = read_manifest(result.folder / "_work/segments.jsonl")
    spoken = [w for s in segments for w in s.text.split()]
    assert sorted(spoken) == sorted(_plain_text(result.markdown.read_text(encoding="utf-8")))
    assert all(len(s.text) <= 300 for s in segments)
    assert len({s.id for s in segments}) == len(segments)
    assert all(re.fullmatch(r"ch\d{2}-s\d{4}", s.id) for s in segments)


def test_footnotes_follow_their_chapter() -> None:
    md = (FIXTURES / "ru/kapitanskaya-dochka.md").read_text(encoding="utf-8")
    segments = segment_markdown(md, "ru", RU)
    roles = [(s.chapter, s.role) for s in segments]
    last_ch1 = max(i for i, (ch, _) in enumerate(roles) if ch == 1)
    assert roles[last_ch1 - 1 : last_ch1 + 1] == [(1, "footnote"), (1, "footnote")]
    assert segments[last_ch1].pause_after_ms == 3000  # chapter end
    assert not any(s.role == "footnote" for s in segments if s.chapter == 2)


def test_manifest_follows_the_edited_markdown(tmp_path: Path) -> None:
    folder = convert_one(FIXTURES / "ru/kapitanskaya-dochka.fb2", tmp_path).folder
    assert folder is not None
    book = folder / "kapitanskaya-dochka.md"
    book.write_text(
        book.read_text(encoding="utf-8").replace("Вожатый", "Вожатый и буран"), encoding="utf-8"
    )
    result = convert_one(folder, tmp_path, reconvert=True)
    assert result.status == "kept-edits"
    texts = [s.text for s in read_manifest(folder / "_work/segments.jsonl")]
    assert "Глава II. Вожатый и буран" in texts
