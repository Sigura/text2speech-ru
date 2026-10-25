"""Safe handling of untrusted input (SPEC §6.12, NFR-10).

- XML goes through defusedxml: entity declarations and external references are
  refused, so billion-laughs and XXE inputs fail instead of expanding.
- XHTML may use HTML entity names (`&nbsp;`) that only the (never fetched) XHTML DTD
  defines; they are rewritten to numeric references before parsing.
- ZIP archives are read in memory with limits on member count, total size and
  compression ratio, and member names may not escape the archive.
"""

from __future__ import annotations

import html.entities
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

MAX_INPUT_BYTES = 512 * 2**20
MAX_MEMBERS = 5000
MAX_UNPACKED_BYTES = 1024 * 2**20
MAX_RATIO = 200


class InputError(Exception):
    """The input file is unreadable, unsupported or unsafe (CLI exit code 2)."""


def read_input(path: Path) -> bytes:
    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise InputError(f"{path.name}: {size} bytes is larger than the {MAX_INPUT_BYTES} limit")
    return path.read_bytes()


def parse_xml(data: bytes) -> Element:
    try:
        return SafeET.fromstring(data)
    except DefusedXmlException as exc:
        raise InputError(f"unsafe XML refused ({type(exc).__name__})") from exc
    except SafeET.ParseError as exc:
        raise InputError(f"malformed XML: {exc}") from exc


_XML_ENTITIES = {"amp", "lt", "gt", "quot", "apos"}
_ENTITY = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_DOCTYPE = re.compile(r"<!DOCTYPE[^>\[]*>", re.IGNORECASE)


def _numeric_entity(match: re.Match[str]) -> str:
    name = match.group(1)
    if name in _XML_ENTITIES:
        return match.group(0)
    code = html.entities.name2codepoint.get(name)
    return f"&#{code};" if code is not None else match.group(0)


def parse_xhtml(data: bytes) -> Element:
    """Parse an XHTML document that may use HTML named entities."""
    text = data.decode("utf-8-sig", errors="replace")
    text = re.sub(r"^\s*<\?xml[^>]*\?>", "", text)  # encoding already applied
    text = _DOCTYPE.sub("", text, count=1)  # a plain DOCTYPE only; internal subsets stay
    text = _ENTITY.sub(_numeric_entity, text)
    return parse_xml(text.encode("utf-8"))


@dataclass
class SafeZip:
    """A ZIP archive checked against the limits above; members are read on demand."""

    archive: zipfile.ZipFile

    @property
    def names(self) -> list[str]:
        return [i.filename for i in self.archive.infolist() if not i.is_dir()]

    def read(self, name: str) -> bytes:
        try:
            return self.archive.read(name)
        except KeyError as exc:
            raise InputError(f"archive member {name!r} is missing") from exc
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
            raise InputError(f"cannot read archive member {name!r}: {exc}") from exc


def _unsafe_name(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    if not path.parts:
        return True
    return path.is_absolute() or ".." in path.parts or ":" in path.parts[0]  # C:\…


def open_zip(data: bytes) -> SafeZip:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise InputError(f"not a valid ZIP archive: {exc}") from exc
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise InputError(f"archive has {len(infos)} members (limit {MAX_MEMBERS})")
    total = 0
    for info in infos:
        if _unsafe_name(info.filename):
            raise InputError(f"unsafe archive member name {info.filename!r}")
        total += info.file_size
        if info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
            raise InputError(f"archive member {info.filename!r} has a suspicious ratio")
    if total > MAX_UNPACKED_BYTES:
        raise InputError(f"archive unpacks to {total} bytes (limit {MAX_UNPACKED_BYTES})")
    return SafeZip(archive)


def local_name(tag: str) -> str:
    """`{namespace}name` → `name`."""
    return tag.rsplit("}", 1)[-1]
