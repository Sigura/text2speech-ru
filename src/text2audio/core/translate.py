"""`translate`: a book's Markdown into another language, offline (§6.11).

The text is parsed into the reader's `Document` and written back by the same writer,
so headings, quotes, poems, scene breaks, footnotes and `<!-- t2a: … -->` directives
keep their place. Each sentence is translated on its own; footnote references and
inline directives are taken out of it and put back at its end (at its start when
they opened it). Emphasis is not kept: the translator sees and returns plain text.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from text2audio.core import resources as res
from text2audio.core.contracts import Capabilities
from text2audio.core.languages import guess_language, profile
from text2audio.core.plugins import discover
from text2audio.core.pool import cpu_slots
from text2audio.core.resources import WorkerSlot
from text2audio.core.workspace import BookFolder
from text2audio.models.lock import assets_for, load_lock
from text2audio.models.store import ModelStore
from text2audio.readers.document import Block, Heading, Inline, Paragraph, Poem, Quote
from text2audio.readers.markdown import parse_markdown, to_markdown
from text2audio.text.sentences import split_sentences
from text2audio.workers.backend import Worker

BATCH = 32  # sentences per worker call
Translate = Callable[[list[str]], list[str]]
Progress = Callable[[str, int, int], None]


class TranslateError(Exception):
    pass


@dataclass
class TranslateResult:
    source: Path
    output: Path
    source_lang: str
    target_lang: str
    translator: str
    device: str
    sentences: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class _Unit:
    """One run of inline text and how it was cut into sentences."""

    inlines: list[Inline]
    sentences: list[str]
    # Per sentence: the markers before and after its text.
    marks: list[tuple[list[Inline], list[Inline]]]


def _unit(inlines: list[Inline], abbreviations: tuple[str, ...]) -> _Unit:
    text = ""
    marks: list[tuple[int, Inline]] = []
    for item in inlines:
        if item.kind in ("note", "directive"):
            marks.append((len(text), item))
        else:
            text += item.text
    sentences = split_sentences(text, abbreviations)  # stripped slices of `text`
    ends, at = [], 0
    for s in sentences:
        at = text.index(s, at) + len(s)
        ends.append(at)
    placed: list[tuple[list[Inline], list[Inline]]] = [([], []) for _ in sentences] or [([], [])]
    for offset, item in marks:
        if not text[:offset].strip():
            placed[0][0].append(item)
            continue
        n = next((i for i, end in enumerate(ends) if offset <= end), len(placed) - 1)
        placed[n][1].append(item)
    return _Unit(inlines, [" ".join(s.split()) for s in sentences], placed)


def _inline_lists(blocks: list[Block]) -> Iterator[list[Inline]]:
    for block in blocks:
        if isinstance(block, Heading | Paragraph):
            yield block.inlines
        elif isinstance(block, Quote):
            yield from _inline_lists(block.blocks)
            if block.author:
                yield block.author
        elif isinstance(block, Poem):
            if block.title:
                yield block.title
            for stanza in block.stanzas:
                yield from stanza
            if block.author:
                yield block.author


def translate_markdown(
    text: str,
    translate: Translate,
    abbreviations: tuple[str, ...] = (),
    progress: Progress | None = None,
) -> tuple[str, int]:
    """Markdown in the §6.4.1 dialect → the same structure in the target language;
    also the number of distinct sentences translated."""
    doc = parse_markdown(text, directives=True)
    lists = list(_inline_lists(doc.blocks))
    for blocks in doc.footnotes.values():
        lists += _inline_lists(blocks)
    units = [_unit(inlines, abbreviations) for inlines in lists]
    unique = list(dict.fromkeys(s for u in units for s in u.sentences))
    done: dict[str, str] = {}
    for start in range(0, len(unique), BATCH):
        chunk = unique[start : start + BATCH]
        out = translate(chunk)
        if len(out) != len(chunk):
            raise TranslateError(f"translator returned {len(out)} texts for {len(chunk)}")
        done.update(zip(chunk, (" ".join(t.split()) for t in out), strict=True))
        if progress:
            progress("translate", len(done), len(unique))
    for u in units:
        new: list[Inline] = []
        for n, (before, after) in enumerate(u.marks):
            if n and new:
                new.append(Inline("text", " "))
            new += before
            if n < len(u.sentences):
                new.append(Inline("text", done[u.sentences[n]]))
            new += after
        u.inlines[:] = new
    return to_markdown(doc), len(unique)


def translator_slot(caps: Capabilities) -> WorkerSlot:
    """The first GPU with room for the translator when it can use one, else CPU (§9.1)."""
    if "cuda" in caps.devices:
        plan = res.plan(res.probe(), caps)
        gpu = [s for s in plan.slots if s.gpu_uuid]
        if gpu:
            return gpu[0]
    [slot] = cpu_slots(caps.threads, caps.ram_mb, 1)
    return slot


@contextmanager
def open_translator(found: Any, store: ModelStore) -> Iterator[tuple[Translate, str]]:
    """A worker of a `text2audio.translators` plugin, loaded from the model store, and
    the device it runs on."""
    worker = Worker(found, store.root, translator_slot(found.capabilities))
    try:
        worker.load(assets=store.payload(assets_for(found.component, load_lock())))
        yield (
            lambda texts: [str(t) for t in worker.call("translate", texts=texts)["texts"]],
            worker.slot.device,
        )
    finally:
        worker.close()


def translate_file(
    source: Path,
    output: Path | None,
    *,
    source_lang: str | None,
    target_lang: str,
    store: ModelStore,
    progress: Progress | None = None,
) -> TranslateResult:
    """A Markdown file, or a book folder's `<book>.md`, into `target_lang`.

    The source language comes from the argument, the book's metadata or the text's
    script. Only translation into English exists (the language's `translator`).
    """
    meta: dict[str, Any] = {}
    if source.is_dir():
        folder = BookFolder(source)
        meta = folder.metadata() or {}
        source = folder.markdown
    if not source.is_file():
        raise TranslateError(f"{source}: no such Markdown file")
    text = source.read_text(encoding="utf-8")
    lang = source_lang or meta.get("language") or guess_language(text)
    if not lang:
        raise TranslateError("cannot tell the source language; pass --from")
    if lang == target_lang:
        raise TranslateError(f"the text is already in {lang!r}")
    prof = profile(lang)
    name = prof.translator if prof and target_lang == "en" else None
    found = discover(("translators",)).get("translators", name) if name else None
    if found is None:
        raise TranslateError(f"no {lang!r} → {target_lang!r} translator installed")
    output = output or source.with_name(f"{source.stem}.{target_lang}.md")
    if output.resolve() == source.resolve():
        raise TranslateError("the output would overwrite the input")
    with open_translator(found, store) as (translate, device):
        result, count = translate_markdown(
            text, translate, prof.abbreviations if prof else (), progress
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".partial")
    partial.write_text(result, encoding="utf-8")
    partial.replace(output)
    return TranslateResult(source, output, lang, target_lang, found.name, device, count)
