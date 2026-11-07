"""Format detection by content (FR-01) and safe input handling (§6.12, NFR-10)."""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

import pytest
from conftest import FIXTURES

from text2audio.readers import safety
from text2audio.readers.detect import detect, strip_format_extension
from text2audio.readers.epub import read_epub
from text2audio.readers.fb2 import read_fb2, read_fb2_zip
from text2audio.readers.safety import InputError, open_zip, parse_xhtml, read_input

LAUGHS = '<!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
XXE = '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
CONTAINER = (
    '<?xml version="1.0"?>{dtd}<container version="1.0" '
    'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
    '<rootfile full-path="book.opf" media-type="application/oebps-package+xml"/>'
    "</rootfiles></container>"
)
OPF = (
    '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
    '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>T</dc:title>'
    "</metadata><manifest>"
    '<item id="c" href="c.xhtml" media-type="application/xhtml+xml"/></manifest>'
    '<spine><itemref idref="c"/></spine></package>'
)
CHAPTER = (
    '<?xml version="1.0"?>{dtd}<html xmlns="http://www.w3.org/1999/xhtml"><body>'
    "<p>{body}</p></body></html>"
)


def _zip(members: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


def _epub(container_dtd: str = "", chapter_dtd: str = "", body: str = "Текст.") -> bytes:
    return _zip(
        {
            "mimetype": "application/epub+zip",
            "META-INF/container.xml": CONTAINER.format(dtd=container_dtd),
            "book.opf": OPF,
            "c.xhtml": CHAPTER.format(dtd=chapter_dtd, body=body),
        }
    )


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


def test_the_epub_without_an_attack_reads() -> None:
    assert "Текст" in str(read_epub(_epub()).blocks)


@pytest.mark.parametrize(
    "epub",
    [
        _epub(container_dtd=LAUGHS),
        _epub(container_dtd=XXE),
        _epub(chapter_dtd=LAUGHS, body="&b;"),
        _epub(chapter_dtd=XXE, body="&e;"),
    ],
    ids=["laughs-container", "xxe-container", "laughs-chapter", "xxe-chapter"],
)
def test_entity_attacks_in_epub_are_refused(epub: bytes) -> None:
    with pytest.raises(InputError, match="unsafe XML"):
        read_epub(epub)


def test_archive_with_too_many_members_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safety, "MAX_MEMBERS", 2)
    with pytest.raises(InputError, match="3 members"):
        open_zip(_zip({"a": "1", "b": "2", "c": "3"}))


def test_archive_unpacking_past_the_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safety, "MAX_UNPACKED_BYTES", 1000)
    with pytest.raises(InputError, match="unpacks to 1200 bytes"):
        open_zip(_zip({"a": bytes(range(256)) * 4 + bytes(range(176))}))


def test_input_over_the_size_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(safety, "MAX_INPUT_BYTES", 10)
    big = tmp_path / "book.txt"
    big.write_text("x" * 11)
    with pytest.raises(InputError, match="larger than the 10 limit"):
        read_input(big)


def test_a_member_cannot_unpack_past_its_header() -> None:
    data = bytearray(_zip({"a.txt": b"x" * 100_000}))
    struct.pack_into("<I", data, data.find(b"PK\x03\x04") + 22, 10)  # local header
    struct.pack_into("<I", data, data.find(b"PK\x01\x02") + 24, 10)  # central directory
    archive = open_zip(bytes(data))
    with pytest.raises(InputError, match="Bad CRC-32"):
        archive.read("a.txt")
