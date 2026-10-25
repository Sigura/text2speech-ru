"""PDF reader: refusals and the language-free clean-up (§6.3, §6.12, spike pdf-readers)."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FIXTURES

from text2audio.readers.document import Document, Heading, Inline, Paragraph, plain
from text2audio.readers.pdf import clean, read_pdf
from text2audio.readers.safety import InputError


def _p(text: str) -> Paragraph:
    return Paragraph([Inline("text", text)])


def test_typeset_pdf_loses_no_structure() -> None:
    doc = read_pdf((FIXTURES / "ru/kapitanskaya-dochka.pdf").read_bytes())
    texts = [plain(b.inlines) for b in doc.blocks if isinstance(b, Paragraph)]
    assert doc.chapters() == 2 and doc.metadata.language == "ru"
    assert doc.metadata.title == "Капитанская дочка"
    assert doc.metadata.authors == ["Александр Пушкин"]
    assert not any(t == "Капитанская дочка" or t.isdigit() for t in texts)  # header, page numbers
    assert any("Симбирской" in t for t in texts)  # hyphenated at a line end in the PDF


@pytest.mark.parametrize(
    ("name", "message"),
    [("scanned.pdf", "no text layer"), ("encrypted.pdf", "password-protected")],
)
def test_unreadable_pdfs_are_refused(name: str, message: str) -> None:
    with pytest.raises(InputError, match=message):
        read_pdf((FIXTURES / "evil" / name).read_bytes())


def test_page_break_splits_are_joined() -> None:
    doc = clean(
        Document(
            blocks=[
                _p("Попадая в лучи вечернего света, прон-"),
                _p("икавшие через дыры потолка фигуры"),
                _p("превращались в людей."),  # starts lowercase: same paragraph
                _p("— Новый абзац."),
            ]
        )
    )
    assert [plain(b.inlines) for b in doc.blocks if isinstance(b, Paragraph)] == [
        "Попадая в лучи вечернего света, проникавшие через дыры потолка фигуры "
        "превращались в людей.",
        "— Новый абзац.",
    ]


def test_page_numbers_and_running_headers_are_dropped() -> None:
    blocks = [Heading(1, [Inline("text", "Книга")])]
    for n in range(1, 5):
        blocks += [_p("Станции Ангелов"), _p(f"Текст страницы {n}."), _p(str(n))]
    doc = clean(Document(blocks=list(blocks)))
    texts = [plain(b.inlines) for b in doc.blocks if isinstance(b, Paragraph)]
    assert texts == [f"Текст страницы {n}." for n in range(1, 5)]


def test_heading_levels_put_chapters_at_level_two() -> None:
    doc = clean(
        Document(
            blocks=[
                Heading(1, [Inline("strong", "Книга")]),
                Heading(1, [Inline("emph", "Глава 1")]),
                _p("Текст."),
                Heading(2, [Inline("text", "Сцена")]),
                _p("Ещё."),
            ]
        )
    )
    heads = [(b.level, b.inlines[0].kind) for b in doc.blocks if isinstance(b, Heading)]
    assert heads == [(1, "text"), (2, "text"), (3, "text")]


def test_pdf_detected_by_content(tmp_path: Path) -> None:
    from text2audio.core.convert import convert_one

    renamed = tmp_path / "book.txt"
    renamed.write_bytes((FIXTURES / "en/alice.pdf").read_bytes())
    result = convert_one(renamed, tmp_path / "out")
    assert result.format == "pdf" and result.chapters == 2
