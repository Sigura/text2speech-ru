"""Speech in one language → the same recording spoken in another (after-M4, ADR-0012).

Audio or video: timed transcription (the source pack's QA ASR) → phrases → the book
translation pipeline → the target pack's voice → each phrase placed at its original
time → the input's container with the new track. Video keeps its picture and is timed
to the original; audio keeps the original pauses. The work folder next to the output is
a book folder, so the voiced phrases, the transcript and the translation are cached.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

from text2audio.audio.encode import TRACK_FORMATS, find_ffmpeg, replace_track
from text2audio.audio.srt import Cue, split_cue, write_srt
from text2audio.audio.wav import ASSEMBLY_RATE, PartWriter, atempo, speech
from text2audio.core import synthesize as synth
from text2audio.core.config import Config
from text2audio.core.contracts import Segment
from text2audio.core.languages import profile
from text2audio.core.translate import Stats, engine_chain, translate_markdown
from text2audio.core.workspace import BookFolder, write_json
from text2audio.models.lock import assets_for, load_lock
from text2audio.models.store import ModelStore
from text2audio.qa import gate
from text2audio.readers.document import Document, Inline, Paragraph
from text2audio.readers.markdown import parse_markdown, to_markdown
from text2audio.text.sentences import split_sentences
from text2audio.workers.backend import Worker

Progress = Callable[[str, int, int], None]
Kind = Literal["audio", "video"]

VIDEO = frozenset({".mp4", ".mkv", ".mov", ".webm", ".avi"})
AUDIO = TRACK_FORMATS - VIDEO
MAX_SPEEDUP = 1.3  # a phrase longer than its slot is sped up at most this much
PHRASE_GAP_S = 0.8  # silence that ends a phrase without punctuation
PARAGRAPH_GAP_S = 1.5  # silence that starts a new paragraph (a translation unit)
CLAUSE_CUT_S = 8.0  # a phrase this long ends at the next comma
MAX_PHRASE_S = 20.0  # and at the next word beyond this
LATE_MS = 1000  # a phrase starting later than this behind the original is reported
CLIP_GAP_MS = 120  # between the segments of one phrase
TRANSCRIBE_TIMEOUT_S = 4 * 3600.0
_SENTENCE_END = re.compile(r"[.!?…][\"'”’»)\]]*$")
_CLAUSE_END = re.compile(r"[,;:—–][\"'”’»)\]]*$")
_LETTERS = re.compile(r"\W+")
_ISO639_2 = {"ru": "rus", "en": "eng"}  # container language tags


class RevoiceError(Exception):
    pass


@dataclass
class Phrase:
    start: float  # seconds
    end: float
    text: str
    paragraph: int
    out: str = ""


@dataclass
class RevoiceResult:
    source: Path
    output: Path
    subtitles: Path | None
    folder: Path
    kind: Kind
    source_lang: str
    target_lang: str
    translator: str
    phrases: int
    duration_s: float
    late: list[str] = field(default_factory=list)
    stats: Stats = field(default_factory=Stats)


def media_kind(path: Path) -> Kind | None:
    ext = path.suffix.lower()
    return "video" if ext in VIDEO else "audio" if ext in AUDIO else None


def phrases(words: list[tuple[float, float, str]], abbreviations: tuple[str, ...]) -> list[Phrase]:
    """Timed words → phrases: a sentence, or a run of speech ended by a pause; a long
    pause also starts a new paragraph, the unit the translator sees."""
    abbrevs = {a.lower() for a in abbreviations}
    out: list[Phrase] = []
    current: list[tuple[float, float, str]] = []
    paragraph = 0

    def flush() -> None:
        if current:
            text = " ".join(w for _, _, w in current)
            out.append(Phrase(current[0][0], current[-1][1], text, paragraph))
            current.clear()

    last_end: float | None = None
    for start, end, word in words:
        gap = start - last_end if last_end is not None else 0.0
        if current and (gap >= PHRASE_GAP_S or end - current[0][0] > MAX_PHRASE_S):
            flush()
        if gap >= PARAGRAPH_GAP_S:
            flush()
            paragraph += 1
        current.append((start, end, word))
        last_end = end
        sentence = _SENTENCE_END.search(word) and word.lower().strip("\"'”’»)]") not in abbrevs
        if sentence or (_CLAUSE_END.search(word) and end - current[0][0] >= CLAUSE_CUT_S):
            flush()
    flush()
    return out


def _doc(texts: list[str]) -> str:
    return to_markdown(Document(blocks=[Paragraph([Inline("text", t)]) for t in texts]))


def _paragraphs(markdown: str) -> list[str]:
    texts = []
    for block in parse_markdown(markdown).blocks:
        if not isinstance(block, Paragraph):
            raise RevoiceError(f"translation came back with a {type(block).__name__} block")
        texts.append(" ".join("".join(i.text for i in block.inlines if i.kind == "text").split()))
    return texts


def _add(total: Stats, part: Stats) -> None:
    total.pieces += part.pieces
    total.translated += part.translated
    total.cached += part.cached
    total.placeholders += part.placeholders
    total.fallback += part.fallback
    for name, n in part.by.items():
        total.by[name] = total.by.get(name, 0) + n
    total.warnings += part.warnings


def translate_phrases(
    found: list[Phrase],
    run: Callable[[str], tuple[str, Stats]],
    target_abbrevs: tuple[str, ...],
) -> Stats:
    """Each paragraph is translated whole and its sentences are given back to its
    phrases when the counts match; the other paragraphs go phrase by phrase, so every
    phrase keeps its own time slot."""
    groups: dict[int, list[Phrase]] = {}
    for p in found:
        groups.setdefault(p.paragraph, []).append(p)
    whole = list(groups.values())
    translated, stats = run(_doc([" ".join(p.text for p in g) for g in whole]))
    texts = _paragraphs(translated)
    if len(texts) != len(whole):
        raise RevoiceError(f"{len(whole)} paragraphs translated into {len(texts)}")
    single: list[Phrase] = []
    for group, text in zip(whole, texts, strict=True):
        sentences = split_sentences(text, target_abbrevs) if len(group) > 1 else [text]
        if len(sentences) == len(group):
            for p, s in zip(group, sentences, strict=True):
                p.out = s
        else:
            single += group
    if single:
        translated, more = run(_doc([p.text for p in single]))
        _add(stats, more)
        texts = _paragraphs(translated)
        if len(texts) != len(single):
            raise RevoiceError(f"{len(single)} phrases translated into {len(texts)}")
        for p, text in zip(single, texts, strict=True):
            p.out = text
    return stats


def clips_by_phrase(found: list[Phrase], segments: list[Segment]) -> list[list[Segment]]:
    """The rendered segments of each phrase: a phrase is one paragraph of the work
    Markdown, which the segmenter may split into several segments, never join."""
    keys = [_LETTERS.sub("", p.out).lower() for p in found]
    out: list[list[Segment]] = [[] for _ in found]
    i, have = 0, ""
    for seg in segments:
        key = _LETTERS.sub("", seg.text).lower()
        if not key:
            continue
        while i < len(keys) and (keys[i] == have or not keys[i]):
            i, have = i + 1, ""
        if i == len(keys) or not keys[i].startswith(have + key):
            raise RevoiceError(f"segment {seg.id} ({seg.text[:40]!r}) matches no phrase")
        out[i].append(seg)
        have += key
    return out


def place(
    found: list[Phrase],
    clips: list[list[Path]],
    writer: PartWriter,
    kind: Kind,
    duration_ms: int,
    ffmpeg: str,
) -> tuple[list[Cue], list[str]]:
    """Write each phrase's clips at its time: video at the original start (sped up to
    fit its slot, at most `MAX_SPEEDUP`, then running into the next pause); audio after
    the original pause before it, unhurried."""
    cues: list[Cue] = []
    late: list[str] = []
    previous_end = 0.0
    gap = np.zeros(ASSEMBLY_RATE * CLIP_GAP_MS // 1000, dtype=np.float32)
    for n, (p, paths) in enumerate(zip(found, clips, strict=True)):
        parts = [speech(path) for path in paths]
        if not parts:
            continue
        audio = np.concatenate([x for part in parts for x in (part, gap)][:-1])
        if kind == "video":
            start = max(round(p.start * 1000), writer.duration_ms)
            nxt = round(found[n + 1].start * 1000) if n + 1 < len(found) else duration_ms
            length = audio.size * 1000 / ASSEMBLY_RATE
            room = nxt - start
            if length > room:
                factor = MAX_SPEEDUP if room <= 0 else min(MAX_SPEEDUP, length / room)
                audio = atempo(audio, ASSEMBLY_RATE, round(factor, 3), ffmpeg)
            if start - p.start * 1000 > LATE_MS:
                late.append(f"{_clock(p.start)} +{(start - p.start * 1000) / 1000:.1f} s")
        else:
            start = writer.duration_ms + round(max(0.0, p.start - previous_end) * 1000)
        previous_end = p.end
        writer.silence(start - writer.duration_ms)
        writer.add(audio)
        cues += split_cue(start, writer.duration_ms, p.out)
    if kind == "video":
        writer.silence(duration_ms - writer.duration_ms)
    return cues, late


def _clock(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _sha256(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def transcribe(
    source: Path, lang: str, work: Path, config: Config, store: ModelStore
) -> tuple[list[tuple[float, float, str]], float]:
    """Timed words of the recording, cached in `_work/transcript.json` by its hash."""
    cache = work / "transcript.json"
    digest = _sha256(source)
    try:
        saved = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if saved.get("sha256") != digest:
        prof = profile(lang)
        if prof is None:
            raise RevoiceError(f"no language pack for {lang!r}")
        asr = gate.asr_component(lang, prof, config)
        slot = gate.asr_slots(asr, config, 1)[0]
        payload = store.payload(assets_for(getattr(asr, "component", asr.name), load_lock()))
        with Worker(asr, store.root, slot, request_timeout=TRANSCRIBE_TIMEOUT_S) as worker:
            worker.load(assets=payload)
            reply = worker.call("timed", path=str(source.resolve()), language=lang)
        saved = {"sha256": digest, "duration": reply["duration"], "words": reply["words"]}
        write_json(cache, saved)
    words = [(float(s), float(e), str(w)) for s, e, w in saved["words"]]
    return words, float(saved["duration"])


def revoice(
    source: Path,
    output: Path | None,
    *,
    source_lang: str,
    target_lang: str | None,
    config: Callable[[BookFolder], Config],
    store: ModelStore,
    progress: Progress | None = None,
) -> RevoiceResult:
    """An audio or video file re-spoken in `target_lang` (default: the first language
    the source pack translates into), written as `<name>.<lang>.<ext>`."""
    kind = media_kind(source)
    if kind is None or not source.is_file():
        raise RevoiceError(f"{source}: not an audio or video file")
    src_prof = profile(source_lang)
    if src_prof is None:
        raise RevoiceError(f"no language pack for {source_lang!r}")
    target = target_lang or next(iter(src_prof.translators), None)
    if not target or target == source_lang:
        raise RevoiceError(f"no target language for {source_lang!r}; pass --to")
    tgt_prof = profile(target)
    if tgt_prof is None:
        raise RevoiceError(f"no language pack for {target!r}")
    out = output or source.with_name(f"{source.stem}.{target}{source.suffix}")
    if out.suffix.lower() not in TRACK_FORMATS:
        raise RevoiceError(f"{out}: cannot write {out.suffix or 'extension-less'} files")
    if out.resolve() == source.resolve():
        raise RevoiceError("the output would overwrite the input")
    folder = BookFolder(out.parent / out.stem)
    folder.work.mkdir(parents=True, exist_ok=True)
    cfg = config(folder)
    ffmpeg = find_ffmpeg(cfg.tools.ffmpeg)

    if progress:
        progress("transcribe", 0, 1)
    words, duration = transcribe(source, source_lang, folder.work, cfg, store)
    if progress:
        progress("transcribe", 1, 1)
    found = phrases(words, src_prof.abbreviations)
    if not found:
        raise RevoiceError(f"{source}: no speech found")

    factories, lead, _slot, skipped = engine_chain(source_lang, target, store)
    cache = folder.work / f"translate-{target}.jsonl"

    def run(markdown: str) -> tuple[str, Stats]:
        return translate_markdown(
            markdown, factories, src_prof.abbreviations, tgt_prof.abbreviations, cache, progress
        )

    stats = translate_phrases(found, run, tgt_prof.abbreviations)
    stats.warnings[:0] = [f"skipped {why}" for why in skipped]

    folder.markdown.write_text(_doc([p.out for p in found]), encoding="utf-8")
    meta: dict[str, Any] = {"language": target, "title": out.stem, "source": {"name": source.name}}
    write_json(folder.metadata_path, meta)
    segments = synth.render_selection(folder.path, cfg, lambda s: s, store=store, progress=progress)
    audio_dir = folder.work / "audio" / "segments"
    clips = [
        [gate.wav_path(audio_dir, s.hash or "") for s in group]
        for group in clips_by_phrase(found, segments)
    ]

    wav = folder.work / "revoiced.wav"
    with PartWriter(wav, ASSEMBLY_RATE, lead_in_ms=0) as writer:
        cues, late = place(found, clips, writer, kind, round(duration * 1000), ffmpeg)
    srt = out.with_suffix(".srt") if kind == "video" else None
    if srt is not None:
        write_srt(srt, cues)
    if progress:
        progress("encode", 0, 1)
    replace_track(
        ffmpeg,
        wav,
        out,
        video=source if kind == "video" else None,
        subtitles=srt,
        language=_ISO639_2.get(target, "und"),
    )
    wav.unlink()
    if progress:
        progress("encode", 1, 1)
    return RevoiceResult(
        source,
        out,
        srt,
        folder.path,
        kind,
        source_lang,
        target,
        lead.name,
        len(found),
        duration,
        late,
        stats,
    )
