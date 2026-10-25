"""Book folders under the output root (SPEC §5.2, FR-08, FR-21).

One folder per input file, named after it. The editable `<folder>.md` sits at the
top; `_work/` keeps the source copy, the last converter output, metadata and cover.
An edited `<folder>.md` is never overwritten by a conversion: the new text goes to
`<folder>.reconverted.md` instead.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from text2audio.readers.detect import strip_format_extension

WORK = "_work"
MAX_NAME_BYTES = 120
_INVALID = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')

MarkdownStatus = Literal["written", "unchanged", "kept-edits"]


def sanitize_name(name: str) -> str:
    name = _INVALID.sub("_", name).strip(" .")
    encoded = name.encode("utf-8")
    if len(encoded) > MAX_NAME_BYTES:
        name = encoded[:MAX_NAME_BYTES].decode("utf-8", errors="ignore").rstrip(" .")
    return name or "book"


def folder_name(source: Path, override: str | None = None) -> str:
    return sanitize_name(override if override else strip_format_extension(source.name))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class BookFolder:
    path: Path

    @property
    def work(self) -> Path:
        return self.path / WORK

    @property
    def markdown(self) -> Path:
        return self.path / f"{self.path.name}.md"

    @property
    def reconverted(self) -> Path:
        return self.path / f"{self.path.name}.reconverted.md"

    @property
    def converted(self) -> Path:
        return self.work / "book.converted.md"

    @property
    def metadata_path(self) -> Path:
        return self.work / "metadata.json"

    def metadata(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def source(self) -> Path | None:
        found = sorted(self.work.glob("source.*"))
        return found[0] if found else None

    def source_hash(self) -> str | None:
        meta = self.metadata() or {}
        value = meta.get("source", {}).get("sha256")
        return value if isinstance(value, str) else None

    def source_format(self) -> str | None:
        meta = self.metadata() or {}
        value = meta.get("source", {}).get("format")
        return value if isinstance(value, str) else None


def resolve_folder(
    root: Path, name: str, fmt: str, digest: str, *, overwrite: bool = False
) -> tuple[BookFolder, bool]:
    """Pick the book folder; return it and whether it resumes the same source.

    Same source → same folder. A folder holding another source gets a sibling:
    `name (fmt)` when the format differs, else `name (2)`, `name (3)`…; with
    `overwrite` the plain name is reused.
    """
    first = BookFolder(root / name)
    if not first.path.exists():
        return first, False
    if first.source_hash() == digest:
        return first, True
    if overwrite:
        return first, False
    candidates = []
    other_format = first.source_format()
    if other_format is not None and other_format != fmt:
        candidates.append(f"{name} ({fmt.replace('.', '-')})")
    candidates += [f"{name} ({n})" for n in range(2, 1000)]
    for candidate in candidates:
        folder = BookFolder(root / sanitize_name(candidate))
        if not folder.path.exists():
            return folder, False
        if folder.source_hash() == digest:
            return folder, True
    raise RuntimeError(f"no free folder name for {name!r} under {root}")


def write_markdown(folder: BookFolder, text: str) -> MarkdownStatus:
    """Write converter output without ever overwriting the user's edits."""
    folder.work.mkdir(parents=True, exist_ok=True)
    current = folder.markdown.read_text(encoding="utf-8") if folder.markdown.exists() else None
    previous = folder.converted.read_text(encoding="utf-8") if folder.converted.exists() else None
    edited = current is not None and current != previous
    if edited:
        if text != previous:
            folder.reconverted.write_text(text, encoding="utf-8")
            folder.converted.write_text(text, encoding="utf-8")
        return "kept-edits"
    folder.converted.write_text(text, encoding="utf-8")
    if current == text:
        return "unchanged"
    folder.markdown.write_text(text, encoding="utf-8")
    folder.reconverted.unlink(missing_ok=True)
    return "written"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
