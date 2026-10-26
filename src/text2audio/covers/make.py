"""Cover choice for converted books (§6.8.2, FR-23, ADR-0009).

    embedded (convert_one) → generated (a `text2audio.covers` plugin, one session for
    the whole batch) → typographic (always works)

A cover is cached in `_work/cover.jpg` under a key of everything that shapes it, so a
re-conversion keeps it. A typographic cover that stood in for a generated one is
replaced once the generator can run.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from text2audio.core.config import Config
from text2audio.core.contracts import CoverImage, CoverRequest
from text2audio.core.envs import EnvMissing
from text2audio.core.errors import CoverError
from text2audio.core.languages import profile
from text2audio.core.plugins import discover
from text2audio.core.pool import cpu_slots
from text2audio.core.workspace import BookFolder, write_json
from text2audio.covers import prompt, typographic
from text2audio.models.lock import assets_for, load_lock
from text2audio.models.store import ModelStore
from text2audio.workers.backend import Worker
from text2audio.workers.process import WorkerError

FILE = "cover.jpg"
FONT = "DejaVu Serif 2.37"


@dataclass
class _Book:
    folder: BookFolder
    meta: dict[str, Any]
    key: str
    warnings: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return str(self.meta.get("title") or self.folder.path.name)

    @property
    def author(self) -> str:
        return ", ".join(self.meta.get("authors") or [])

    @property
    def lang(self) -> str:
        return str(self.meta.get("language") or "und")

    @property
    def description(self) -> str:
        return description(self.folder, self.meta)


def description(folder: BookFolder, meta: dict[str, Any]) -> str:
    """The annotation's first sentences, else the opening words of the book."""
    found = prompt.blurb(meta.get("annotation"))
    if found or not folder.markdown.is_file():
        return found
    return prompt.opening(folder.markdown.read_text(encoding="utf-8"))


def cover_key(meta: dict[str, Any], config: Config, about: str) -> str:
    cover = config.cover
    shape = {
        "about": about,
        "mode": cover.mode,
        "generator": cover.generator,
        "sd": cover.sd.model_dump(mode="json", exclude={"timeout_s"}),
        **{
            k: meta.get(k) for k in ("title", "authors", "genres", "annotation", "date", "language")
        },
    }
    return hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest()[:16]


def make_covers(
    folders: list[BookFolder], config: Config, store: ModelStore
) -> dict[str, list[str]]:
    """Give every book folder a cover; warnings by folder path."""
    mode = config.cover.mode
    generator, unavailable = _generator(config) if mode in ("auto", "generate") else (None, None)
    todo: list[_Book] = []
    warnings: dict[str, list[str]] = {}
    for folder in folders:
        meta = folder.metadata() or {}
        current = meta.get("cover") or {}
        if current.get("origin") == "embedded":
            continue
        if mode == "embedded-only":
            if current:
                (folder.work / FILE).unlink(missing_ok=True)
                meta["cover"] = None
                write_json(folder.metadata_path, meta)
            continue
        book = _Book(folder, meta, cover_key(meta, config, description(folder, meta)))
        cached = current.get("key") == book.key and (folder.work / FILE).is_file()
        if cached and (current.get("origin") == "generated" or generator is None):
            continue
        todo.append(book)
    if generator is not None and todo:
        _generated(generator, todo, config, store)
    for book in todo:
        if (book.meta.get("cover") or {}).get("key") != book.key:
            _typographic(book, unavailable)
        warnings[str(book.folder.path)] = book.warnings
    return warnings


def _generator(config: Config) -> tuple[Any, str | None]:
    """The configured generator when it can run now, else (None, why not)."""
    name = config.cover.generator
    found = discover(("covers",)).get("covers", name)
    if found is None:
        return None, f"cover generator {name!r} is not installed"
    reason = found.unavailable(config.cover)
    return (None, reason) if reason else (found, None)


def _save(book: _Book, entry: dict[str, Any], img: Any) -> None:
    for old in book.folder.work.glob("cover.*"):
        old.unlink()
    typographic.save(img, book.folder.work / FILE)
    book.meta["cover"] = {"file": FILE, "type": "image/jpeg", "key": book.key, **entry}
    write_json(book.folder.metadata_path, book.meta)


def _typographic(book: _Book, note: str | None) -> None:
    entry: dict[str, Any] = {"origin": "typographic", "font": FONT}
    if note:
        entry["note"] = note
    _save(book, entry, typographic.render(book.title, book.author))


def _generated(generator: Any, books: list[_Book], config: Config, store: ModelStore) -> None:
    english = _translate(books, store)
    requests: dict[str, tuple[_Book, CoverRequest, bool]] = {}
    for book in books:
        title, blurb = english.get(id(book), (None, None))
        text, negative = prompt.build(
            book.meta.get("genres") or [], book.meta.get("date"), title, blurb
        )
        req = CoverRequest(
            key=book.key, prompt=text, negative_prompt=negative, seed=prompt.seed(book.key)
        )
        requests[book.key] = (book, req, title is not None)
    try:
        images: list[CoverImage] = generator.generate(
            [r for _, r, _ in requests.values()], config.cover
        )
    except CoverError as exc:
        for book in books:
            book.warnings.append(f"cover not generated: {exc}")
        return
    done = {img.key: img for img in images}
    for key, (book, req, translated) in requests.items():
        img = done.get(key)
        if img is None:
            book.warnings.append("cover not generated: the generator returned no image")
            continue
        entry = {
            "origin": "generated",
            "generator": generator.name,
            "prompt": req.prompt,
            "negative_prompt": req.negative_prompt,
            "seed": req.seed,
            "translated": translated,
            **img.info,
        }
        _save(book, entry, typographic.overlay(img.png, book.title, book.author))


def _translate(books: list[_Book], store: ModelStore) -> dict[int, tuple[str | None, str | None]]:
    """English title and description per book, through each language's translator (offline).

    English books need none; a language without a translator gets genre keywords only.
    """
    out: dict[int, tuple[str | None, str | None]] = {}
    by_lang: dict[str, list[_Book]] = {}
    for book in books:
        if book.lang == "en":
            out[id(book)] = (book.title, book.description or None)
        else:
            by_lang.setdefault(book.lang, []).append(book)
    for lang, group in by_lang.items():
        prof = profile(lang)
        name = prof.translator if prof else None
        found = discover(("translators",)).get("translators", name) if name else None
        if found is None:
            for book in group:
                book.warnings.append(f"no {lang!r} → English translator; cover prompt from genres")
            continue
        texts = [t for b in group for t in (b.title, b.description)]
        try:
            english = _run_translator(found, [t for t in texts if t], store)
        except (WorkerError, EnvMissing) as exc:
            for book in group:
                book.warnings.append(f"cover prompt not translated: {exc}")
            continue
        for book in group:
            title = english.get(book.title)
            out[id(book)] = (title, english.get(book.description))
    return out


def _run_translator(found: Any, texts: list[str], store: ModelStore) -> dict[str, str]:
    caps = found.capabilities
    [slot] = cpu_slots(caps.threads, caps.ram_mb, 1)
    worker = Worker(found, store.root, slot)
    try:
        worker.load(assets=store.payload(assets_for(found.component, load_lock())))
        unique = list(dict.fromkeys(texts))
        reply = worker.call("translate", texts=unique)
        return dict(zip(unique, (str(t) for t in reply["texts"]), strict=True))
    finally:
        worker.close()
