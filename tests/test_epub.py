"""EPUB specifics: TOC-driven chapters, notes, metadata, EPUB 2 vs 3 (§6.3, FR-02, FR-03)."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import FIXTURES

from text2audio.core.convert import convert_one
from text2audio.readers.epub import read_epub

EPUB3 = FIXTURES / "ru/kapitanskaya-dochka.epub"
EPUB2 = FIXTURES / "ru/kapitanskaya-dochka-epub2.epub"


def _body(md: str) -> str:
    return md.split("\n", 2)[2]  # without the `#` title line


def test_epub2_and_epub3_give_the_same_text(tmp_path: Path) -> None:
    a = convert_one(EPUB3, tmp_path / "a").markdown
    b = convert_one(EPUB2, tmp_path / "b").markdown
    assert a is not None and b is not None
    assert a.read_text(encoding="utf-8") == b.read_text(encoding="utf-8")


def test_epub_matches_fb2_content(tmp_path: Path) -> None:
    epub = convert_one(EPUB3, tmp_path / "a").markdown
    fb2 = convert_one(FIXTURES / "ru/kapitanskaya-dochka.fb2", tmp_path / "b").markdown
    assert epub is not None and fb2 is not None
    assert _body(epub.read_text(encoding="utf-8")) == _body(fb2.read_text(encoding="utf-8"))


def test_chapter_count_matches_toc() -> None:
    for path in (EPUB3, EPUB2):
        doc = read_epub(path.read_bytes())
        assert doc.chapters() == 2


def test_notes_are_not_read_in_place() -> None:
    doc = read_epub(EPUB2.read_bytes())
    assert len(doc.footnotes) == 2
    assert all("Примечания" not in str(b) for b in doc.blocks)


def test_epub_metadata(tmp_path: Path) -> None:
    for path, isbn in ((EPUB3, None), (EPUB2, "978-5-00-000000-0")):
        result = convert_one(path, tmp_path / path.stem)
        assert result.folder is not None
        meta = json.loads((result.folder / "_work/metadata.json").read_text(encoding="utf-8"))
        assert meta["title"] == "Капитанская дочка"
        assert meta["authors"] == ["Александр Пушкин"]
        assert meta["language"] == "ru"
        assert (meta["series"], meta["series_index"]) == ("Повести и романы", 7)
        assert meta["isbn"] == isbn
        assert meta["cover"]["file"] == "cover.png"


def test_english_epub_without_toc_uses_xhtml_headings() -> None:
    doc = read_epub((FIXTURES / "en/alice.epub").read_bytes())
    assert doc.metadata.language == "en" and doc.chapters() == 1
