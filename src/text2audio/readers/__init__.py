"""Readers: input formats → `Document` → `<book>.md` (SPEC §6.3, §6.4.1)."""

from __future__ import annotations

from collections.abc import Callable

from text2audio.readers.detect import Format
from text2audio.readers.document import Document
from text2audio.readers.fb2 import read_fb2, read_fb2_zip
from text2audio.readers.safety import InputError

ReadFn = Callable[[bytes], Document]

READERS: dict[str, tuple[str, ReadFn]] = {
    # format → (reader name recorded in metadata.json, function)
    "fb2": ("fb2", read_fb2),
    "fb2.zip": ("fb2", read_fb2_zip),
}

# File name suffixes picked up when a directory is given as input (FR-21 batch).
SUFFIXES = (".fb2", ".fb2.zip", ".zip", ".epub", ".pdf", ".txt", ".md", ".markdown")


def read(fmt: Format, data: bytes) -> tuple[str, Document]:
    try:
        name, fn = READERS[fmt]
    except KeyError:
        raise InputError(f"no reader for {fmt} input yet") from None
    return name, fn(data)
