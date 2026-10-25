"""Format detection by content (FR-01) and safe input handling (§6.12, NFR-10)."""

from __future__ import annotations

import pytest
from conftest import FIXTURES

from text2audio.readers.detect import detect, strip_format_extension
from text2audio.readers.fb2 import read_fb2, read_fb2_zip
from text2audio.readers.safety import InputError, parse_xhtml


def test_detects_by_content_not_extension() -> None:
    fb2 = (FIXTURES / "ru/kapitanskaya-dochka.fb2").read_bytes()
    assert detect(fb2, "book.txt") == "fb2"
    assert detect(fb2, "") == "fb2"
    zipped = (FIXTURES / "ru/kapitanskaya-dochka.fb2.zip").read_bytes()
    assert detect(zipped, "book") == "fb2.zip"
    assert detect(b"%PDF-1.7\n...", "book.txt") == "pdf"
    assert detect("Глава 1\n\nТекст.".encode(), "book.md") == "md"
    assert detect("# Книга\n\n## Глава 1\n\nТекст.".encode(), "book.txt") == "md"
    assert detect("Глава 1\n\nТекст.".encode("cp1251"), "book") == "txt"


def test_unsupported_binary_is_an_input_error() -> None:
    with pytest.raises(InputError):
        detect(b"\x00\x01\x02binary", "x.bin")


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Война и мир.fb2.zip", "Война и мир"),
        ("notes.txt", "notes"),
        ("book.FB2", "book"),
        ("archive.tar", "archive.tar"),
    ],
)
def test_strip_format_extension(name: str, expected: str) -> None:
    assert strip_format_extension(name) == expected


@pytest.mark.parametrize("name", ["xxe.fb2", "billion-laughs.fb2"])
def test_entity_attacks_are_refused(name: str) -> None:
    with pytest.raises(InputError, match="unsafe XML"):
        read_fb2((FIXTURES / "evil" / name).read_bytes())


def test_archive_path_traversal_is_refused() -> None:
    with pytest.raises(InputError, match="unsafe archive member"):
        read_fb2_zip((FIXTURES / "evil/traversal.fb2.zip").read_bytes())


def test_zip_bomb_is_refused() -> None:
    with pytest.raises(InputError, match="suspicious ratio"):
        read_fb2_zip((FIXTURES / "evil/zip-bomb.fb2.zip").read_bytes())


def test_xhtml_html_entities_parse_without_a_dtd() -> None:
    root = parse_xhtml(
        b'<?xml version="1.0"?><!DOCTYPE html><html><p>a&nbsp;b &mdash; &amp;</p></html>'
    )
    assert "".join(root.itertext()) == "a b — &"
