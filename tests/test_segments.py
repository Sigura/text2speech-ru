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


PAUSES_MD = """# Книга

## Глава 1

> Береги честь смолоду.
>
> — Пословица

Первое предложение здесь. Второе предложение здесь.

Мороз и солнце,\\
день чудесный!

Ещё ты дремлешь,\\
друг прелестный.

* * *

Последний абзац главы.

## Глава 2

Начало.
"""


def test_each_boundary_records_its_pause_type() -> None:
    segments = segment_markdown(PAUSES_MD, "ru", None, max_chars=40)
    got = [(s.text, s.pause, s.pause_after_ms) for s in segments]
    assert got == [
        ("Книга", "chapter", 3000),  # the chapter break follows the title
        ("Глава 1", "heading", 1000),
        ("Береги честь смолоду.", "paragraph", 700),
        ("Пословица", "quote", 1000),  # end of the epigraph
        ("Первое предложение здесь.", "sentence", 300),
        ("Второе предложение здесь.", "paragraph", 700),
        ("Мороз и солнце,\nдень чудесный!", "stanza", 900),
        ("Ещё ты дремлешь,\nдруг прелестный.", "scene", 1500),
        ("Последний абзац главы.", "chapter", 3000),
        ("Глава 2", "heading", 1000),
        ("Начало.", "paragraph", 700),
    ]


def test_profile_overrides_pause_lengths_by_type() -> None:
    assert RU is not None and RU.pauses_ms["paragraph"] == 800
    segments = segment_markdown(PAUSES_MD, "ru", RU, max_chars=40)
    by_type = {s.pause: s.pause_after_ms for s in segments}
    assert by_type == {
        "heading": 1000,
        "paragraph": 800,
        "quote": 1000,
        "sentence": 300,
        "stanza": 900,
        "scene": 1500,
        "chapter": 3000,
    }


def test_a_long_stanza_pauses_as_a_stanza_only_at_its_end() -> None:
    stanza = "Мороз и солнце; день чудесный!  \nЕщё ты дремлешь, друг прелестный.\n"
    segments = segment_markdown(stanza, "ru", RU, max_chars=40)
    assert [s.pause for s in segments] == ["sentence", "stanza"]


def test_unknown_pause_type_in_a_profile_is_an_error() -> None:
    assert RU is not None
    bad = RU.model_copy(update={"pauses_ms": {"paragrah": 800}})
    with pytest.raises(ValueError, match="paragrah"):
        segment_markdown(PAUSES_MD, "ru", bad)


def test_e0_keeps_dialogue_in_the_narration_and_three_pause_lengths() -> None:
    line = "— Эх, батюшка Петр Андреич! — отвечал он с глубоким вздохом. — Сержусь-то я."
    e1 = segment_markdown(line, "ru", RU)
    e0 = segment_markdown(line, "ru", RU, expression="E0")
    assert {s.role for s in e1} == {"dialogue", "remark"}
    assert {s.role for s in e0} == {"narration"}
    segments = segment_markdown(PAUSES_MD, "ru", RU, max_chars=40, expression="E0")
    by_type = {s.pause: s.pause_after_ms for s in segments}
    assert by_type == {
        "heading": 800,
        "paragraph": 800,
        "quote": 800,
        "sentence": 300,
        "stanza": 800,
        "scene": 800,
        "chapter": 3000,
    }
