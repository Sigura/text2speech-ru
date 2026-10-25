"""Detect the input format by content, not by extension (FR-01)."""

from __future__ import annotations

import re
from typing import Literal

from text2audio.readers.safety import InputError, open_zip

Format = Literal["fb2", "fb2.zip", "epub", "pdf", "txt", "md"]

_MD_HEADING = re.compile(rb"^#{1,6} \S", re.MULTILINE)


def detect(data: bytes, name: str = "") -> Format:
    head = data[:4096]
    if head.startswith(b"PK\x03\x04"):
        archive = open_zip(data)
        names = archive.names
        if "mimetype" in names and archive.read("mimetype").strip() == b"application/epub+zip":
            return "epub"
        if "META-INF/container.xml" in names:
            return "epub"
        fb2 = [n for n in names if n.lower().endswith(".fb2")]
        if len(fb2) == 1:
            return "fb2.zip"
        raise InputError(f"{name or 'archive'}: ZIP is neither EPUB nor a single FB2 book")
    if b"%PDF-" in head[:1024]:
        return "pdf"
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped.startswith(b"<") and b"<FictionBook" in head:
        return "fb2"
    lower = name.lower()
    if lower.endswith((".md", ".markdown")) or len(_MD_HEADING.findall(data[:65536])) >= 2:
        return "md"
    if b"\x00" in head:
        raise InputError(f"{name or 'input'}: binary file of an unsupported format")
    return "txt"


def strip_format_extension(name: str) -> str:
    """`Война и мир.fb2.zip` → `Война и мир`; known compound extensions go as one (§5.2)."""
    lower = name.lower()
    for ext in (".fb2.zip", ".fb2", ".epub", ".pdf", ".txt", ".markdown", ".md", ".zip"):
        if lower.endswith(ext) and len(name) > len(ext):
            return name[: -len(ext)]
    return name
