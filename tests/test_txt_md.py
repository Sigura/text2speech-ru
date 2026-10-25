"""TXT heuristics, `.md` pass-through and the Russian heading patterns (§6.3, §5.2)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from conftest import FIXTURES

from text2audio.core.convert import convert_one
from text2audio.core.languages import guess_language, profile
from text2audio.readers.txt import decode, read_txt


def _meta(folder: Path | None) -> dict[str, object]:
    assert folder is not None
    data = json.loads((folder / "_work/metadata.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_librus_txt_structure_and_confidence(tmp_path: Path) -> None:
    result = convert_one(FIXTURES / "ru/kapitanskaya-dochka.txt", tmp_path)
    assert result.markdown is not None
    text = result.markdown.read_text(encoding="utf-8")
    assert text.startswith("# Александр Пушкин. Капитанская дочка\n")
    assert "## Глава I. СЕРЖАНТ ГВАРДИИ" in text and "## Глава II. ВОЖАТЫЙ" in text
    meta = _meta(result.folder)
    assert meta["language"] == "ru" and meta["heading_confidence"] == 0.9


def test_toc_parts_and_hyphenation(tmp_path: Path) -> None:
    result = convert_one(FIXTURES / "ru/blank-lines-parts.txt", tmp_path)
    assert result.markdown is not None
    text = result.markdown.read_text(encoding="utf-8")
    assert "Содержание" not in text
    assert [x for x in text.splitlines() if x.startswith("#")][1:] == [
        "## Часть первая",
        "### Глава 1. Начало",
        "### Глава 2. Дорога",
        "## Часть вторая",
        "### Глава 3. Конец",
    ]
    assert "переносом слова" in text and "* * *" in text
    assert any("table of contents" in w for w in result.warnings)


def test_untitled_txt_gets_the_file_name(tmp_path: Path) -> None:
    result = convert_one(FIXTURES / "ru/blank-lines-parts.txt", tmp_path)
    assert result.markdown is not None
    assert result.markdown.read_text(encoding="utf-8").startswith("# blank-lines-parts\n")
    assert _meta(result.folder)["title"] == "blank-lines-parts"


def test_txt_without_headings_has_zero_confidence() -> None:
    doc = read_txt("Просто текст.\n\nЕщё абзац.\n".encode())
    assert doc.heading_confidence == 0.0 and doc.chapters() == 0


def test_md_is_kept_verbatim(tmp_path: Path) -> None:
    source = FIXTURES / "ru/kapitanskaya-dochka.md"
    result = convert_one(source, tmp_path)
    assert result.markdown is not None and result.chapters == 2
    assert result.markdown.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert _meta(result.folder)["title"] == "Александр Пушкин. Капитанская дочка"


def test_language_override(tmp_path: Path) -> None:
    result = convert_one(FIXTURES / "ru/blank-lines-parts.txt", tmp_path, lang="uk")
    assert _meta(result.folder)["language"] == "uk"
    assert any("no language pack for 'uk'" in w for w in result.warnings)


@pytest.mark.parametrize("codec", ["utf-8", "cp1251", "koi8-r", "utf-16"])
def test_decode_russian_encodings(codec: str) -> None:
    text = "Глава первая. Ёлка стояла в углу, и все смотрели на неё весь вечер. " * 20
    assert decode(text.encode(codec)).strip() == text.strip()


def test_language_guess_by_script() -> None:
    assert guess_language("Глава 1. Привет") == "ru"
    assert guess_language("Chapter 1. Hello") is None  # no English pack installed yet


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("Глава I", "chapter"),
        ("ГЛАВА 12", "chapter"),
        ("Глава первая", "chapter"),
        ("Глава 3. Отъезд", "chapter"),
        ("Гл. 4 — Буран", "chapter"),
        ("Пролог", "chapter"),
        ("ЭПИЛОГ", "chapter"),
        ("Часть первая", "part"),
        ("КНИГА ВТОРАЯ", "part"),
        ("Том 3", "part"),
        ("Оглавление", "front"),
        ("Глава из жизни моей тётушки была длинной", None),
        ("Глава двадцать третья", "chapter"),
        ("Часть последняя", "part"),
        ("Главная мысль", None),
        ("Частность", None),
    ],
)
def test_russian_heading_patterns(line: str, kind: str | None) -> None:
    ru = profile("ru")
    assert ru is not None
    groups = {"part": ru.part_headings, "chapter": ru.chapter_headings, "front": ru.front_matter}
    found = [k for k, pats in groups.items() if any(re.fullmatch(p, line, re.I) for p in pats)]
    assert found == ([kind] if kind else [])
