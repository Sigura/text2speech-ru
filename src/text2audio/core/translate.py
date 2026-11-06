"""`translate`: a book's Markdown into another language, offline (§6.11).

The text is parsed into the reader's `Document` and written back by the same writer,
so headings, quotes, poems, scene breaks, footnotes and `<!-- t2a: … -->` directives
keep their place. Emphasis is not kept: the translators see and return plain text.

Each run of text (a paragraph, heading, poem line or footnote) is a *piece*. The
source language's `translators` for the target are tried in order; the first that
can run here translates every piece in its unit — a paragraph-unit translator (an
LLM) gets the whole piece, a sentence-unit one each sentence. A piece holding a
footnote reference or an inline directive inside it goes sentence by sentence, so
the marker can go back to the end of its sentence. Every result is checked; a piece
that fails is translated again:

- Latin abbreviations of the source (NASA, USB) must come back as they are: when one
  is missing, the same translator gets the piece again with the abbreviations
  replaced by placeholders (`XQ1`), which are then restored;
- other failures (a word in mixed scripts, a dropped sentence, an odd length, no
  text) go to the next translator in the list, sentence by sentence. Its result
  replaces the first one when it fails fewer checks.

Translations are cached by translator and text (`*.translate.jsonl`), so a run that is
interrupted, or repeated after an edit, translates only what is new.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from text2audio.core import resources as res
from text2audio.core.contracts import Capabilities
from text2audio.core.envs import EnvMissing, plugin_env
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

Unit = Literal["sentence", "paragraph"]
Translate = Callable[[list[str]], list[str]]
Progress = Callable[[str, int, int], None]
BATCH: dict[Unit, int] = {"sentence": 32, "paragraph": 4}  # texts per worker call

# Latin abbreviations kept as they are: 2–6 capitals or digits, starting with a capital.
_ACRONYM = re.compile(r"(?<![\w-])[A-Z][A-Z0-9&]{1,5}(?![\w-])")
_PLACEHOLDER = "XQ{}"
_MIXED_WORD = re.compile(r"[а-яёА-ЯЁ][a-zA-Z]|[a-zA-Z][а-яёА-ЯЁ]")
_LENGTH = (0.45, 2.2)  # translated ÷ source characters, beyond which a piece is suspect
_MIN_CHECKED = 40  # characters; shorter pieces skip the length and sentence checks


class TranslateError(Exception):
    pass


@dataclass
class Engine:
    """One loaded translator: its name, its unit and the call that translates."""

    name: str
    unit: Unit
    run: Translate


EngineFactory = Callable[[], AbstractContextManager[Engine]]


@dataclass
class Stats:
    pieces: int = 0
    translated: int = 0  # texts sent to a translator (cache misses)
    cached: int = 0
    by: dict[str, int] = field(default_factory=dict)  # translator → pieces in the output
    placeholders: int = 0  # pieces redone with abbreviation placeholders
    fallback: int = 0  # pieces taken from the next translator
    warnings: list[str] = field(default_factory=list)


@dataclass
class TranslateResult:
    source: Path
    output: Path
    source_lang: str
    target_lang: str
    translator: str
    device: str
    stats: Stats


# --------------------------------------------------------------------------- pieces


@dataclass
class _Piece:
    """One run of inline text, its sentences and where its markers go."""

    inlines: list[Inline]
    text: str
    sentences: list[str]
    # Per sentence: the markers before and after its text.
    marks: list[tuple[list[Inline], list[Inline]]]
    inner_marks: bool  # a marker sits inside the text, so the piece goes by sentence
    out: list[str] = field(default_factory=list)  # translation per sentence, or one text
    by: str = ""
    problems: list[str] = field(default_factory=list)


def _piece(inlines: list[Inline], abbreviations: tuple[str, ...]) -> _Piece:
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
    inner = False
    for offset, item in marks:
        if not text[:offset].strip():
            placed[0][0].append(item)
            continue
        n = next((i for i, end in enumerate(ends) if offset <= end), len(placed) - 1)
        placed[n][1].append(item)
        inner = inner or bool(text[offset:].strip())
    return _Piece(
        inlines,
        " ".join(text.split()),
        [" ".join(s.split()) for s in sentences],
        placed,
        inner,
    )


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


def _rebuild(p: _Piece) -> None:
    """Put the translation back into the piece's inline list, markers included."""
    new: list[Inline] = []
    whole = len(p.out) == 1 and len(p.sentences) != 1
    for n, (before, after) in enumerate(p.marks):
        if n and new and not whole:
            new.append(Inline("text", " "))
        new += before
        if whole and n == 0:
            new.append(Inline("text", p.out[0]))
        elif not whole and n < len(p.out):
            new.append(Inline("text", p.out[n]))
        if not whole:
            new += after
    if whole:  # paragraph unit: markers that ended sentences go to the end
        new += [m for _, after in p.marks for m in after]
    p.inlines[:] = new


# --------------------------------------------------------------------------- checks


def _acronyms(text: str) -> list[str]:
    letters = [c for c in text if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) > 0.5 * len(letters):
        return []  # an all-caps passage: its words are not abbreviations
    return list(dict.fromkeys(_ACRONYM.findall(text)))


def _problems(source: str, out: str, unit: Unit, target_abbrevs: tuple[str, ...]) -> list[str]:
    if not out.strip():
        return ["empty"] if source.strip() else []
    found: list[str] = []
    missing = [a for a in _acronyms(source) if not re.search(rf"(?<![\w-]){re.escape(a)}", out)]
    if missing:
        found.append("abbreviations " + ", ".join(missing))
    if _MIXED_WORD.search(out) and not _MIXED_WORD.search(source):
        found.append("mixed scripts")
    if len(source) >= _MIN_CHECKED:
        ratio = len(out) / len(source)
        if not _LENGTH[0] <= ratio <= _LENGTH[1]:
            found.append(f"length ×{ratio:.2f}")
        if unit == "paragraph":
            n_src = len(split_sentences(source))
            n_out = len(split_sentences(out, target_abbrevs))
            if n_src >= 3 and abs(n_out - n_src) > max(1, n_src // 3):
                found.append(f"{n_out} sentences for {n_src}")
    return found


def _protect(text: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}
    for n, acronym in enumerate(_acronyms(text), 1):
        mapping[_PLACEHOLDER.format(n)] = acronym
    for key, acronym in mapping.items():
        text = re.sub(rf"(?<![\w-]){re.escape(acronym)}(?![\w-])", key, text)
    return text, mapping


def _restore(text: str, mapping: dict[str, str]) -> str:
    for key in sorted(mapping, key=len, reverse=True):
        text = text.replace(key, mapping[key])
    return text


# --------------------------------------------------------------------------- cache


class _Cache:
    """Translations by translator and text, appended to a JSONL file as they arrive."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.done: dict[str, str] = {}
        if path is not None and path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue  # a line cut off by an interrupted run
                self.done[row["key"]] = row["out"]

    @staticmethod
    def key(engine: str, text: str) -> str:
        return hashlib.sha256(f"{engine}\0{text}".encode()).hexdigest()[:32]

    def run(self, engine: Engine, texts: list[str], stats: Stats) -> list[str]:
        todo = list(dict.fromkeys(t for t in texts if self.key(engine.name, t) not in self.done))
        stats.cached += len(set(texts)) - len(todo)
        if todo:
            out = engine.run(todo)
            if len(out) != len(todo):
                raise TranslateError(f"{engine.name} returned {len(out)} texts for {len(todo)}")
            stats.translated += len(todo)
            rows = {
                self.key(engine.name, t): " ".join(o.split())
                for t, o in zip(todo, out, strict=True)
            }
            self.done.update(rows)
            if self.path is not None:
                with self.path.open("a", encoding="utf-8") as f:
                    for k, o in rows.items():
                        f.write(json.dumps({"key": k, "out": o}, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
        return [self.done[self.key(engine.name, t)] for t in texts]


# --------------------------------------------------------------------------- core


def _translate_pieces(
    engine: Engine,
    pieces: list[_Piece],
    cache: _Cache,
    stats: Stats,
    progress: Progress | None,
    *,
    by_sentence: bool = False,
) -> None:
    """Fill `out` of every piece with this engine (whole pieces or sentences)."""
    jobs: list[tuple[_Piece, list[str]]] = []
    for p in pieces:
        whole = engine.unit == "paragraph" and not p.inner_marks and not by_sentence
        jobs.append((p, [p.text] if whole else p.sentences))
    texts = [t for _, ts in jobs for t in ts]
    batch = BATCH[engine.unit]
    done: list[str] = []
    for start in range(0, len(texts), batch):
        done += cache.run(engine, texts[start : start + batch], stats)
        if progress:
            progress("translate", len(done), len(texts))
    at = 0
    for p, ts in jobs:
        p.out, p.by = done[at : at + len(ts)], engine.name
        at += len(ts)


def _check(p: _Piece, unit: Unit, target_abbrevs: tuple[str, ...]) -> list[str]:
    source = p.text if len(p.out) == 1 else " ".join(p.sentences)
    return _problems(source, " ".join(p.out), unit, target_abbrevs)


def _with_placeholders(
    engine: Engine, p: _Piece, cache: _Cache, stats: Stats, *, by_sentence: bool = False
) -> list[str]:
    texts = (
        [p.text]
        if engine.unit == "paragraph" and len(p.out) == 1 and not by_sentence
        else p.sentences
    )
    protected = [_protect(t) for t in texts]
    out = cache.run(engine, [t for t, _ in protected], stats)
    return [_restore(o, m) for o, (_, m) in zip(out, protected, strict=True)]


def translate_markdown(
    text: str,
    engines: Sequence[EngineFactory],
    source_abbrevs: tuple[str, ...] = (),
    target_abbrevs: tuple[str, ...] = (),
    cache_path: Path | None = None,
    progress: Progress | None = None,
) -> tuple[str, Stats]:
    """Markdown in the §6.4.1 dialect → the same structure in the target language.

    `engines` open the translators in order of preference; the first translates
    everything, the second (opened only if needed) re-translates failed pieces.
    """
    if not engines:
        raise TranslateError("no translator")
    doc = parse_markdown(text, directives=True)
    lists = list(_inline_lists(doc.blocks))
    for blocks in doc.footnotes.values():
        lists += _inline_lists(blocks)
    pieces = [p for p in (_piece(i, source_abbrevs) for i in lists) if p.text]
    stats = Stats(pieces=len(pieces))
    cache = _Cache(cache_path)
    failed: list[_Piece] = []
    with engines[0]() as first:
        _translate_pieces(first, pieces, cache, stats, progress)
        for p in pieces:
            p.problems = _check(p, first.unit, target_abbrevs)
            if p.problems and all(x.startswith("abbreviations") for x in p.problems):
                p.out = _with_placeholders(first, p, cache, stats)
                p.problems = _check(p, first.unit, target_abbrevs)
                stats.placeholders += 1
            if p.problems:
                failed.append(p)
    if failed and len(engines) > 1:
        with engines[1]() as second:
            for p in failed:
                kept = (list(p.out), p.by, p.problems)
                _translate_pieces(second, [p], cache, stats, None, by_sentence=True)
                p.problems = _check(p, "sentence", target_abbrevs)
                if any(x.startswith("abbreviations") for x in p.problems):
                    p.out = _with_placeholders(second, p, cache, stats, by_sentence=True)
                    p.problems = _check(p, "sentence", target_abbrevs)
                if len(p.problems) < len(kept[2]):
                    stats.fallback += 1
                else:
                    p.out, p.by, p.problems = kept
    for p in pieces:
        stats.by[p.by] = stats.by.get(p.by, 0) + 1
        if p.problems:
            stats.warnings.append(f"{'; '.join(p.problems)}: {p.text[:60]}")
        _rebuild(p)
    return to_markdown(doc), stats


# --------------------------------------------------------------------------- workers


def translator_slot(caps: Capabilities) -> WorkerSlot | None:
    """The first GPU with room for the translator, else a CPU slot if it may use the CPU;
    None when it needs a GPU and none has room (§9.1)."""
    if "cuda" in caps.devices:
        gpu = [s for s in res.plan(res.probe(), caps).slots if s.gpu_uuid]
        if gpu:
            return gpu[0]
    if "cpu" not in caps.devices:
        return None
    [slot] = cpu_slots(caps.threads, caps.ram_mb, 1)
    return slot


@contextmanager
def open_translator(
    found: Any, store: ModelStore, source: str, target: str, slot: WorkerSlot | None = None
) -> Iterator[Engine]:
    """A loaded worker of a `text2audio.translators` plugin as an `Engine`."""
    slot = slot or translator_slot(found.capabilities)
    if slot is None:
        raise TranslateError(f"{found.name} needs a GPU with {found.capabilities.vram_mb} MiB free")
    worker = Worker(found, store.root, slot)
    try:
        worker.load(assets=store.payload(assets_for(found.component, load_lock())))

        def run(texts: list[str]) -> list[str]:
            reply = worker.call("translate", texts=texts, source=source, target=target)
            return [str(t) for t in reply["texts"]]

        yield Engine(found.name, getattr(found, "unit", "sentence"), run)
    finally:
        worker.close()


def _chain(source: str, target: str) -> list[Any]:
    """Discovered translators for the pair, in the source language's order of preference."""
    prof = profile(source)
    names = prof.translators.get(target, ()) if prof else ()
    found = discover(("translators",))
    chain = [found.get("translators", n) for n in names]
    return [t for t in chain if t is not None and (source, target) in getattr(t, "pairs", ())]


def _unusable(translator: Any, store: ModelStore) -> str | None:
    """Why a translator cannot load here (files or environment missing), or None."""
    assets = assets_for(translator.component, load_lock())
    if not assets or any(not store.path(a, f).is_file() for a in assets for f in a.files):
        feature = next((a.feature for a in assets if a.feature), None)
        how = f" --feature {feature}" if feature else ""
        return f"{translator.name}: model files missing (text2audio models pull{how})"
    env = plugin_env(translator)
    if env is not None and not env.installed:
        return f"{translator.name}: environment missing ({env.fix})"
    return None


def runnable_translators(
    source: str, target: str, store: ModelStore
) -> tuple[list[tuple[Any, WorkerSlot]], list[str]]:
    """Translators for the pair that are installed and have a device now, in order of
    preference, with a slot each; and why the others were passed over."""
    ready: list[tuple[Any, WorkerSlot]] = []
    skipped: list[str] = []
    for t in _chain(source, target):
        why = _unusable(t, store)
        slot = None if why else translator_slot(t.capabilities)
        if why is None and slot is None:
            why = f"{t.name}: needs a GPU with {t.capabilities.vram_mb} MiB free"
        if why:
            skipped.append(why)
        elif slot is not None:
            ready.append((t, slot))
    return ready, skipped


def translate_file(
    source: Path,
    output: Path | None,
    *,
    source_lang: str | None,
    target_lang: str | None,
    store: ModelStore,
    progress: Progress | None = None,
) -> TranslateResult:
    """A Markdown file, or a book folder's `<book>.md`, into `target_lang`.

    The source language comes from the argument, the book's metadata or the text's
    script; the target defaults to the first one the source language translates into.
    The cache lives in the book's `_work/`, else next to the output.
    """
    meta: dict[str, Any] = {}
    work: Path | None = None
    if source.is_dir():
        folder = BookFolder(source)
        meta, work = folder.metadata() or {}, folder.work
        source = folder.markdown
    if not source.is_file():
        raise TranslateError(f"{source}: no such Markdown file")
    text = source.read_text(encoding="utf-8")
    lang = source_lang or meta.get("language") or guess_language(text)
    if not lang:
        raise TranslateError("cannot tell the source language; pass --from")
    src_prof = profile(lang)
    target_lang = target_lang or next(iter(src_prof.translators if src_prof else ()), None)
    if not target_lang:
        raise TranslateError(f"no target language: {lang!r} translates into none; pass --to")
    if lang == target_lang:
        raise TranslateError(f"the text is already in {lang!r}")
    if not _chain(lang, target_lang):
        raise TranslateError(f"no {lang!r} → {target_lang!r} translator installed")
    output = output or source.with_name(f"{source.stem}.{target_lang}.md")
    if output.resolve() == source.resolve():
        raise TranslateError("the output would overwrite the input")
    # The first translator that can run here leads; a sentence-unit one is the fallback.
    runnable, skipped = runnable_translators(lang, target_lang, store)
    if not runnable:
        raise TranslateError("no translator can run now: " + "; ".join(skipped))
    lead, lead_slot = runnable[0]
    factories: list[EngineFactory] = [
        lambda: open_translator(lead, store, lang, target_lang, lead_slot)
    ]
    # The fallback re-translates by sentence: a sentence-unit model when one can run.
    rest = [t for t, _ in runnable[1:]]
    backup = next((t for t in rest if getattr(t, "unit", "sentence") == "sentence"), None)
    backup = backup or next(iter(rest), None)
    if backup is not None:
        factories.append(lambda: open_translator(backup, store, lang, target_lang))
    tgt_prof = profile(target_lang)
    cache_dir = work if work is not None else output.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        result, stats = translate_markdown(
            text,
            factories,
            src_prof.abbreviations if src_prof else (),
            tgt_prof.abbreviations if tgt_prof else (),
            cache_dir / f".{output.stem}.translate.jsonl"
            if work is None
            else cache_dir / f"translate-{target_lang}.jsonl",
            progress,
        )
    except EnvMissing as exc:
        raise TranslateError(str(exc)) from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".partial")
    partial.write_text(result, encoding="utf-8")
    partial.replace(output)
    stats.warnings[:0] = [f"skipped {why}" for why in skipped]
    return TranslateResult(source, output, lang, target_lang, lead.name, lead_slot.device, stats)
