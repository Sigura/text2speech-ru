"""`convert`: input files → book folders with `<book>.md` (SPEC §5.2, FR-01–03, FR-21).

One failing book never stops a batch; its result carries the error instead.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from text2audio import __version__, readers
from text2audio.core.config import Config, CoverConfig
from text2audio.core.languages import profile
from text2audio.core.workspace import (
    WORK,
    BookFolder,
    MarkdownStatus,
    folder_name,
    resolve_folder,
    sha256,
    write_json,
    write_markdown,
)
from text2audio.covers.make import make_covers
from text2audio.models.store import ModelStore
from text2audio.readers.detect import detect
from text2audio.readers.document import Document, Heading, Inline
from text2audio.readers.markdown import to_markdown
from text2audio.readers.safety import InputError, read_input
from text2audio.text.segment import segment_markdown, write_manifest

Status = Literal["converted", "unchanged", "kept-edits", "failed"]
_STATUS: dict[MarkdownStatus, Status] = {
    "written": "converted",
    "unchanged": "unchanged",
    "kept-edits": "kept-edits",
}


@dataclass
class ConvertResult:
    input: Path
    status: Status
    folder: Path | None = None
    markdown: Path | None = None
    format: str | None = None
    chapters: int = 0
    segments: int = 0
    resumed: bool = False
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    internal: bool = False  # error is a bug, not a bad input (exit code 5)


def expand_inputs(inputs: list[Path]) -> list[Path]:
    """Directories become the supported files inside them, in name order."""
    out: list[Path] = []
    for path in inputs:
        if path.is_dir():
            out += sorted(
                p
                for p in path.iterdir()
                if p.is_file()
                and not p.name.startswith(".")
                and p.name.lower().endswith(readers.SUFFIXES)
            )
        else:
            out.append(path)
    return out


def _metadata(doc: Document, fmt: str, reader: str, src: Path, data: bytes) -> dict[str, object]:
    meta = asdict(doc.metadata)
    return {
        **meta,
        "chapters": doc.chapters(),
        "heading_confidence": doc.heading_confidence,
        "source": {"name": src.name, "format": fmt, "sha256": sha256(data), "bytes": len(data)},
        "reader": {"name": reader, "version": __version__},
        "cover": None,
        "warnings": doc.warnings,
    }


def convert_one(
    src: Path,
    root: Path,
    *,
    name: str | None = None,
    overwrite: bool = False,
    reconvert: bool = False,
    lang: str | None = None,
    cover_mode: str = "auto",
) -> ConvertResult:
    """Convert one input file, or with `reconvert` the stored source of a book folder.

    `lang` overrides the language of untagged text (TXT, MD); a re-conversion reuses
    the language recorded at the first conversion.
    """
    try:
        if reconvert:
            folder = BookFolder(src)
            stored = folder.source()
            if stored is None:
                raise InputError(f"{src}: not a book folder (no {WORK}/source.*)")
            data, resumed = read_input(stored), True
            original = (folder.metadata() or {}).get("source", {}).get("name") or stored.name
            src_name = Path(str(original))
            lang = lang or (folder.metadata() or {}).get("language")
        else:
            data, src_name = read_input(src), src
        fmt = detect(data, src_name.name)
        reader, doc = readers.read(fmt, data, lang)
        if not reconvert:
            folder, resumed = resolve_folder(
                root, folder_name(src_name, name), fmt, sha256(data), overwrite=overwrite
            )
        if not doc.metadata.title:  # TXT and title-less inputs: the folder name
            doc.metadata.title = folder.path.name
        if not doc.has_title() and doc.markdown is None:
            doc.blocks.insert(0, Heading(1, [Inline("text", doc.metadata.title)]))
        folder.work.mkdir(parents=True, exist_ok=True)
        stale = folder.source()
        target = folder.work / f"source.{fmt}"
        if stale is not None and stale != target:
            stale.unlink()
        if not resumed or not target.exists():
            target.write_bytes(data)
        previous = (folder.metadata() or {}).get("cover") or {} if resumed else {}
        meta = _metadata(doc, fmt, reader, src_name, data)
        if doc.cover is not None and cover_mode in ("auto", "embedded-only"):
            for old in folder.work.glob("cover.*"):
                old.unlink()
            cover = folder.work / f"cover.{doc.cover.extension}"
            cover.write_bytes(doc.cover.data)
            meta["cover"] = {"file": cover.name, "origin": "embedded", "type": doc.cover.media_type}
        elif previous.get("origin") in ("generated", "typographic"):
            meta["cover"] = previous  # make_covers keeps or replaces it
        else:
            for old in folder.work.glob("cover.*"):
                old.unlink()
        if overwrite and not resumed and folder.markdown.exists():
            folder.markdown.unlink()
            folder.converted.unlink(missing_ok=True)
        status = write_markdown(folder, to_markdown(doc))
        write_json(folder.metadata_path, meta)
        # The manifest follows the Markdown the user reads and edits (§6.4.2, FR-08).
        code = doc.metadata.language or "und"
        segments = segment_markdown(
            folder.markdown.read_text(encoding="utf-8"), code, profile(code)
        )
        write_manifest(folder.work / "segments.jsonl", segments)
        markdown = folder.reconverted if status == "kept-edits" else folder.markdown
        return ConvertResult(
            input=src,
            status=_STATUS[status],
            folder=folder.path,
            markdown=markdown if markdown.exists() else folder.markdown,
            format=fmt,
            chapters=doc.chapters(),
            segments=len(segments),
            resumed=resumed,
            warnings=doc.warnings,
        )
    except InputError as exc:
        return ConvertResult(input=src, status="failed", error=str(exc))
    except OSError as exc:
        return ConvertResult(input=src, status="failed", error=f"{exc.strerror}: {exc.filename}")
    except Exception as exc:  # a bug must not stop the rest of a batch (NFR-09)
        return ConvertResult(input=src, status="failed", error=repr(exc), internal=True)


def convert(
    inputs: list[Path],
    root: Path,
    *,
    name: str | None = None,
    overwrite: bool = False,
    reconvert: bool = False,
    lang: str | None = None,
    config: Config | None = None,
    store: ModelStore | None = None,
) -> list[ConvertResult]:
    paths = inputs if reconvert else expand_inputs(inputs)
    if name is not None and len(paths) != 1:
        raise ValueError("--name needs exactly one input file")
    mode = (config.cover if config else CoverConfig()).mode
    results = [
        convert_one(
            p, root, name=name, overwrite=overwrite, reconvert=reconvert, lang=lang, cover_mode=mode
        )
        for p in paths
    ]
    done = [r for r in results if r.folder is not None and r.status != "failed"]
    if config is not None and done:
        # Generated covers are made for the whole batch at once (one service start).
        found = make_covers(
            [BookFolder(r.folder) for r in done if r.folder], config, store or ModelStore()
        )
        for r in done:
            r.warnings += found.get(str(r.folder), [])
    return results
