"""Golden files for every reader fixture (M1 exit criterion, FR-02, FR-03)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import FIXTURES, GOLDEN, assert_golden

from text2audio.core.convert import convert_one

CASES = [
    "ru/kapitanskaya-dochka.fb2",
    "ru/kapitanskaya-dochka.fb2.zip",
    "quirks/royallib-style.fb2",
    "quirks/fb2-saved-as.txt",
    "quirks/wrapped-parts.fb2",
    "ru/kapitanskaya-dochka.txt",
    "ru/blank-lines-parts.txt",
    "ru/kapitanskaya-dochka.md",
    "ru/kapitanskaya-dochka.epub",
    "ru/kapitanskaya-dochka-epub2.epub",
    "en/alice.epub",
    "ru/kapitanskaya-dochka.pdf",
    "en/alice.pdf",
]


def _golden_dir(case: str) -> Path:
    return GOLDEN / case.replace("/", "__")


@pytest.mark.parametrize("case", CASES)
def test_reader_matches_golden(case: str, tmp_path: Path) -> None:
    result = convert_one(FIXTURES / case, tmp_path)
    assert result.status == "converted", result.error
    assert result.markdown is not None and result.folder is not None
    meta = json.loads((result.folder / "_work/metadata.json").read_text(encoding="utf-8"))
    meta["reader"].pop("version")  # changes every release, not with the reader's output
    assert_golden(_golden_dir(case) / "book.md", result.markdown.read_text(encoding="utf-8"))
    assert_golden(
        _golden_dir(case) / "metadata.json",
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
    )
    assert_golden(
        _golden_dir(case) / "segments.jsonl",
        (result.folder / "_work/segments.jsonl").read_text(encoding="utf-8"),
    )


def test_chapter_count_matches_fb2_sections(tmp_path: Path) -> None:
    result = convert_one(FIXTURES / "ru/kapitanskaya-dochka.fb2", tmp_path)
    assert result.chapters == 2
    assert result.markdown is not None
    text = result.markdown.read_text(encoding="utf-8")
    assert [line for line in text.splitlines() if line.startswith("## ")] == [
        "## Глава I. Сержант гвардии",
        "## Глава II. Вожатый",
    ]


def test_fb2_metadata_and_cover(tmp_path: Path) -> None:
    result = convert_one(FIXTURES / "ru/kapitanskaya-dochka.fb2", tmp_path)
    assert result.folder is not None
    meta = json.loads((result.folder / "_work/metadata.json").read_text(encoding="utf-8"))
    assert meta["title"] == "Капитанская дочка"
    assert meta["authors"] == ["Александр Сергеевич Пушкин"]
    assert meta["language"] == "ru"
    assert (meta["series"], meta["series_index"]) == ("Повести и романы", 7)
    assert meta["cover"] == {"file": "cover.png", "origin": "embedded", "type": "image/png"}
    assert (result.folder / "_work/cover.png").read_bytes().startswith(b"\x89PNG")


def test_lone_wrapper_section_is_unwrapped(tmp_path: Path) -> None:
    result = convert_one(FIXTURES / "quirks/wrapped-parts.fb2", tmp_path)
    assert result.markdown is not None
    text = result.markdown.read_text(encoding="utf-8")
    headings = [line for line in text.splitlines() if line.startswith("#")]
    assert headings == [
        "# Книга в обёртке",
        "## Часть первая",
        "### Глава 1",
        "### Глава 2",
        "## Часть вторая",
        "### Глава 3",
    ]
    assert text.count("\n* * *\n") == 2
    assert any("КНИГА В ОБЁРТКЕ, ИЛИ РОМАН" in w for w in result.warnings)


def test_library_banners_are_not_narrated(tmp_path: Path) -> None:
    result = convert_one(FIXTURES / "quirks/royallib-style.fb2", tmp_path)
    assert result.markdown is not None
    text = result.markdown.read_text(encoding="utf-8")
    assert "royallib" not in text.lower()
    assert any("myheader" in w and "myfooter" in w for w in result.warnings)
    assert "*как будто* шутя" in text
    assert "\n— Скажи мне" in text and "\n— Убить меня" in text
