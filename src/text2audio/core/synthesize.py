"""`synthesize`: book folder → audiobook (SPEC §5.1, §6.8, FR-08, FR-10, FR-17).

    <book>.md → segments → lexicon → normalization → stress → route + hash
              → render missing segments in workers (journaled) → split plan
              → part WAVs → loudness + MP3 + tags → playlist → report.json

Language-specific work is done by the components the language profile names; the core
never imports a model or a language (§6.6.1). Every segment's audio is cached by a
content hash, so editing a paragraph re-renders only its segments, and an interrupted
run resumes where it stopped.
"""

from __future__ import annotations

import hashlib
import json
import math
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from text2audio import __version__
from text2audio.audio import split as splitting
from text2audio.audio.encode import Chapter, encode_mp3, find_ffmpeg, write_m3u
from text2audio.audio.wav import ASSEMBLY_RATE, PartWriter, speech
from text2audio.core import resources as res
from text2audio.core.config import Config
from text2audio.core.contracts import (
    BackendDescriptor,
    LanguageProfile,
    Normalizer,
    Segment,
    Tier,
    TierRoute,
)
from text2audio.core.journal import Journal
from text2audio.core.languages import profile as language_profile
from text2audio.core.plugins import discover
from text2audio.core.workspace import BookFolder, sanitize_name, write_json
from text2audio.models.lock import assets_for, load_lock
from text2audio.models.store import ModelStore
from text2audio.text.lexicon import Lexicon, user_lexicon
from text2audio.text.segment import PAUSE_MS, segment_markdown, write_manifest
from text2audio.text.stress import acute_to_plus, merge_marks, strip_marks
from text2audio.workers.backend import Worker, WorkerBackend
from text2audio.workers.process import WorkerError

HASH_VERSION = 1  # bump when the meaning of a segment hash changes
DEFAULT_MAX_WORKERS = 8  # GPUs and CPUs are shared with other jobs (§9.1)
STRESS_BATCH = 32
SEGMENTS_PER_WORKER = 25  # don't start a worker for fewer pending segments than this

# (stage, done, total): `done == 0` when a stage starts, `done == total` when it ends.
# The callable may also have a `detail(stage, **fields)` method for facts a UI can show
# (route, cached counts, workers, audio rendered, failures); see `_detail` call sites.
Progress = Callable[[str, int, int], None]


def _detail(progress: Progress | None, stage: str, **fields: Any) -> None:
    hook = getattr(progress, "detail", None)
    if hook is not None:
        hook(stage, **fields)


class SynthesisError(Exception):
    """The book cannot be synthesized as configured (missing pack, plugin, env…)."""


@dataclass
class Plan:
    """What `synthesize` will use for a book; also printed by `--dry-run` (later)."""

    lang: str
    profile: LanguageProfile
    tier: Tier
    route: TierRoute
    backend: BackendDescriptor
    normalizer: Normalizer | None
    g2p: Any | None
    notes: list[str] = field(default_factory=list)


@dataclass
class SynthesisReport:
    folder: Path
    files: list[Path]
    report: dict[str, Any]

    @property
    def failed(self) -> int:
        return int(self.report["segments"]["failed"])

    @property
    def flagged(self) -> int:
        return int(self.report.get("qa", {}).get("flagged", 0)) + self.failed


# --------------------------------------------------------------------------- planning


def _book_language(folder: BookFolder, config: Config) -> str:
    meta = folder.metadata() or {}
    lang = config.synthesis.language or meta.get("language")
    if not isinstance(lang, str) or not lang or lang == "und":
        raise SynthesisError(
            f"{folder.path}: unknown book language; set it with --lang or [synthesis] language"
        )
    return lang.split("-")[0].lower()


def make_plan(folder: BookFolder, config: Config, tier: Tier | None = None) -> Plan:
    lang = _book_language(folder, config)
    prof = language_profile(lang)
    if prof is None:
        raise SynthesisError(f"no language pack for {lang!r}; install text2audio-lang-{lang}")
    if not prof.tiers:
        raise SynthesisError(f"the {lang!r} pack defines no synthesis route")
    found = discover(("backends", "normalizers", "g2p"))
    overrides = config.lang.get(lang, {})
    notes: list[str] = []
    wanted = tier or config.synthesis.tier
    order: list[Tier] = [wanted] if wanted else ["expressive", "fast"]
    chosen: tuple[Tier, TierRoute, Any] | None = None
    for t in order:
        route = prof.tiers.get(t)
        if route is None:
            notes.append(f"tier {t}: not defined by the {lang!r} pack")
            continue
        backends = overrides.get("backend", {})  # [lang.ru] backend.fast = "…"
        name = str(backends.get(t, route.backend) if isinstance(backends, dict) else route.backend)
        backend = found.get("backends", name)
        if backend is None:
            notes.append(f"tier {t}: backend {name!r} is not installed")
            continue
        if t == "expressive" and not wanted:
            plan = res.plan(res.probe(), backend.capabilities)
            if plan.tier != "expressive":
                notes.append("tier expressive: no GPU with enough free VRAM")
                continue
        chosen = (t, route.model_copy(update={"backend": name}), backend)
        break
    if chosen is None:
        raise SynthesisError("no usable synthesis route: " + "; ".join(notes))
    t, route, backend = chosen
    normalizer = found.get("normalizers", prof.normalizer) if prof.normalizer else None
    if prof.normalizer and normalizer is None:
        raise SynthesisError(f"normalizer {prof.normalizer!r} of the {lang!r} pack is missing")
    g2p_name = str(overrides.get("g2p", prof.g2p or ""))
    g2p = None
    if g2p_name and g2p_name != "backend":
        g2p = found.get("g2p", g2p_name)
        if g2p is None:
            raise SynthesisError(f"stress component {g2p_name!r} is not installed")
    return Plan(lang, prof, t, route, backend, normalizer, g2p, notes)


def _model_id(owner: str, lock: list[Any]) -> str:
    """Identity of a plugin's pinned weights: changes when models.lock changes them."""
    digests = sorted(d for a in assets_for(owner, lock) for d in a.sha256.values())
    return hashlib.sha256("".join(digests).encode()).hexdigest()[:16]


def segment_hash(seg: Segment, voice_id: str, model: str) -> str:
    data = {
        "v": HASH_VERSION,
        "text": seg.tts_text,
        "voice": voice_id,
        "route": seg.route,
        "backend": seg.backend,
        "model": model,
        "seed": seg.seed,
    }
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


# --------------------------------------------------------------------------- text


def _pause_map(prof: LanguageProfile) -> dict[int, int]:
    return {PAUSE_MS[k]: v for k, v in prof.pauses_ms.items() if k in PAUSE_MS}


def prepare(
    folder: BookFolder,
    plan: Plan,
    config: Config,
    store: ModelStore,
    progress: Progress | None = None,
) -> tuple[list[Segment], list[dict[str, str]]]:
    """Segments with normalized, stressed text, route and hash; and uncertain spans."""
    prof = plan.profile
    segments = segment_markdown(
        folder.markdown.read_text(encoding="utf-8"), plan.lang, prof, seed=config.synthesis.seed
    )
    lexicon = Lexicon.layered(
        Path(prof.lexicon) if prof.lexicon else None,
        user_lexicon(plan.lang),
        folder.work / "lexicon.tsv",
    )
    pauses = _pause_map(prof)
    uncertain: list[dict[str, str]] = []
    marked: dict[str, str] = {}
    for i, seg in enumerate(segments, 1):
        text = acute_to_plus(lexicon.apply(seg.text))
        if plan.normalizer is not None:
            result = plan.normalizer.normalize(text, seg.role)
            text = result.text
            uncertain += [{"segment": seg.id, "span": s} for s in result.uncertain]
        seg.text_norm = strip_marks(text)
        marked[seg.id] = text
        seg.pause_after_ms = pauses.get(seg.pause_after_ms, seg.pause_after_ms)
        if progress:
            progress("normalize", i, len(segments))
    _detail(progress, "normalize", segments=len(segments), uncertain=len(uncertain))
    stressed = _stress(folder, plan, store, list(marked.values()), progress)
    voices = prof.voices
    model = _model_id(plan.route.backend, load_lock())
    for seg in segments:
        text = marked[seg.id]
        seg.text_stress = (
            merge_marks(text, stressed[strip_marks(text)])
            if plan.g2p
            else (text if "+" in text else None)
        )
        seg.backend = plan.route.backend
        seg.route = plan.route.backend
        voice_id = voices.get(seg.voice, voices.get("narrator", {})).get(seg.backend, "")
        seg.hash = segment_hash(seg, voice_id, model)
    return segments, uncertain


def _stress(
    folder: BookFolder,
    plan: Plan,
    store: ModelStore,
    texts: list[str],
    progress: Progress | None,
) -> dict[str, str]:
    """Stress-marked text per unmarked text, cached in `_work/stress.jsonl`."""
    if plan.g2p is None:
        return {}
    name = plan.g2p.name
    journal = Journal(folder.work / "stress.jsonl", key="key")

    def key(text: str) -> str:
        return hashlib.sha256(f"{name}\0{text}".encode()).hexdigest()

    plain = list(dict.fromkeys(strip_marks(t) for t in texts))
    todo = [t for t in plain if key(t) not in journal]
    _detail(progress, "stress", texts=len(plain), cached=len(plain) - len(todo), component=name)
    if todo:
        owner = getattr(plan.g2p, "component", name)
        payload = store.payload(assets_for(owner, load_lock()))
        batches = [todo[i : i + STRESS_BATCH] for i in range(0, len(todo), STRESS_BATCH)]
        threads = getattr(plan.g2p, "threads", 2)
        slots = _cpu_slots(threads, ram_mb=1500, count=max(1, min(4, len(batches) // 8)))
        done = [0]
        _detail(progress, "stress", workers=len(slots))
        if progress:
            progress("stress", 0, len(todo))

        def work(worker: Worker, batch: list[str]) -> None:
            out = worker.call("process", texts=batch)["texts"]
            for src, dst in zip(batch, out, strict=True):
                journal.add({"key": key(src), "text": dst})
            done[0] += len(batch)
            if progress:
                progress("stress", done[0], len(todo))

        _parallel(
            [Worker(plan.g2p, store.root, slot) for slot in slots],
            batches,
            work,
            load={"assets": payload},
        )
    return {t: str(journal.entries[key(t)]["text"]) for t in plain if key(t) in journal}


# --------------------------------------------------------------------------- workers


def _cpu_slots(threads: int, ram_mb: int, count: int) -> list[res.WorkerSlot]:
    from text2audio.core.contracts import Capabilities

    caps = Capabilities(languages=("*",), threads=threads, ram_mb=ram_mb)
    plan = res.plan(res.probe(), caps, tier="fast")
    return plan.slots[: max(1, count)]


def _parallel(
    workers: list[Any],
    items: list[Any],
    work: Callable[[Any, Any], None],
    load: dict[str, Any] | None = None,
    on_error: Callable[[Any, Exception], None] | None = None,
) -> None:
    """Run `work(worker, item)` for every item on a pool of loaded workers."""
    todo: queue.Queue[Any] = queue.Queue()
    for item in items:
        todo.put(item)
    errors: list[BaseException] = []

    def run(worker: Any) -> None:
        try:
            worker.load(**(load or {}))
            while True:
                try:
                    item = todo.get_nowait()
                except queue.Empty:
                    return
                try:
                    work(worker, item)
                except WorkerError as exc:
                    if on_error is None:
                        raise
                    on_error(item, exc)
        except BaseException as exc:
            errors.append(exc)
        finally:
            worker.close()

    threads = [threading.Thread(target=run, args=(w,), daemon=True) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors and (todo.qsize() or len(errors) == len(workers)):
        raise SynthesisError(f"workers failed: {errors[0]}") from errors[0]


def render(
    folder: BookFolder,
    plan: Plan,
    segments: list[Segment],
    config: Config,
    store: ModelStore,
    journal: Journal,
    progress: Progress | None = None,
    max_workers: int | None = None,
) -> dict[str, Any]:
    """Synthesize every segment whose hash is not in the journal yet."""
    audio_dir = folder.work / "audio" / "segments"
    unique: dict[str, Segment] = {}
    for seg in segments:
        assert seg.hash is not None
        entry = journal.get(seg.hash)
        done = entry is not None and (audio_dir / f"{seg.hash[:24]}.wav").is_file()
        if not done and seg.hash not in unique:
            unique[seg.hash] = seg
    pending = list(unique.values())
    stats: dict[str, Any] = {"rendered": 0, "elapsed_ms": 0, "workers": 0, "wall_s": 0.0}
    _detail(progress, "synthesize", segments=len(segments), pending=len(pending))
    if not pending:
        return stats
    caps = plan.backend.capabilities
    rc = config.resources
    slots = res.plan(
        res.probe(),
        caps,
        tier=plan.tier,
        devices=rc.devices,
        max_workers=rc.max_workers,
        threads_per_worker=rc.threads_per_worker,
    ).slots
    cap = max_workers or rc.max_workers or DEFAULT_MAX_WORKERS
    n = max(1, min(len(slots), cap, math.ceil(len(pending) / SEGMENTS_PER_WORKER)))
    voices = plan.profile.voices
    payload = store.payload(assets_for(plan.route.backend, load_lock()))
    lock = threading.Lock()
    started = time.monotonic()
    processed = [0]  # rendered + failed, so the bar ends even when segments fail
    audio_ms = [0]

    def work(worker: WorkerBackend, seg: Segment) -> None:
        assert seg.hash is not None
        out = audio_dir / f"{seg.hash[:24]}.wav"
        voice_id = voices.get(seg.voice, voices.get("narrator", {})).get(worker.name, "")
        reply = worker.call(
            "synthesize", segment=seg.model_dump(mode="json"), out=str(out), voice_id=voice_id
        )
        journal.add(
            {
                "hash": seg.hash,
                "segment": seg.id,
                "duration_ms": int(reply["duration_ms"]),
                "elapsed_ms": int(reply.get("elapsed_ms", 0)),
                "backend": worker.name,
                "warnings": reply.get("warnings", []),
            }
        )
        with lock:
            stats["rendered"] += 1
            stats["elapsed_ms"] += int(reply.get("elapsed_ms", 0))
            processed[0] += 1
            audio_ms[0] += int(reply["duration_ms"])
            _detail(progress, "synthesize", audio_ms=audio_ms[0])
            if progress:
                progress("synthesize", processed[0], len(pending))

    def failed(seg: Segment, exc: Exception) -> None:
        with lock:
            stats.setdefault("failures", []).append({"segment": seg.id, "error": str(exc)})
            processed[0] += 1
            _detail(progress, "synthesize", failed=seg.id, error=str(exc))
            if progress:
                progress("synthesize", processed[0], len(pending))

    workers = [WorkerBackend(plan.backend, store.root, slot) for slot in slots[:n]]
    stats["workers"] = len(workers)
    _detail(progress, "synthesize", workers=len(workers), backend=plan.route.backend)
    if progress:
        progress("synthesize", 0, len(pending))
    _parallel(workers, pending, work, load={"assets": payload}, on_error=failed)
    stats["wall_s"] = round(time.monotonic() - started, 2)
    return stats


# --------------------------------------------------------------------------- assembly


def _items(segments: list[Segment], journal: Journal) -> list[splitting.Item]:
    items = []
    for seg in segments:
        entry = journal.get(seg.hash or "")
        ms = int(entry["duration_ms"]) if entry else 0
        items.append(
            splitting.Item(
                duration_ms=ms + seg.pause_after_ms,
                heading_level=seg.heading_level,
                title=seg.text if seg.heading_level else None,
                paragraph_end=seg.pause_after_ms >= PAUSE_MS["paragraph"],
            )
        )
    return items


def _settings(config: Config) -> splitting.SplitSettings:
    o = config.output
    return splitting.SplitSettings(
        mode=o.split,
        min_parts=o.min_parts,
        max_parts=o.max_parts,
        min_part_minutes=o.min_part_minutes,
        max_part_minutes=o.max_part_minutes,
        target_part_minutes=o.target_part_minutes,
        max_merge_ratio=o.max_merge_ratio,
    )


def _cover(folder: BookFolder) -> Path | None:
    found = sorted(folder.work.glob("cover.*"))
    return found[0] if found else None


def _file_names(folder: BookFolder, parts: list[splitting.Part], single: bool) -> list[str]:
    if single:
        return [f"{folder.path.name}.mp3"]
    width = max(2, len(str(len(parts))))
    return [f"{n:0{width}d} - {sanitize_name(p.title)}.mp3" for n, p in enumerate(parts, 1)]


def assemble(
    folder: BookFolder,
    segments: list[Segment],
    journal: Journal,
    config: Config,
    meta: dict[str, Any],
    narrator: str,
    plan: Plan,
    progress: Progress | None = None,
) -> tuple[list[Path], splitting.SplitPlan]:
    title = str(meta.get("title") or folder.path.name)
    items = _items(segments, journal)
    split = splitting.plan_split(
        items,
        title,
        _settings(config),
        heading_confidence=meta.get("heading_confidence"),
        part_title=plan.profile.part_title,
    )
    ffmpeg = find_ffmpeg(config.tools.ffmpeg)
    cover = _cover(folder)
    names = _file_names(folder, split.parts, split.single)
    audio_dir = folder.work / "audio" / "segments"
    parts_dir = folder.work / "audio" / "parts"
    state_path = folder.work / "parts.json"
    previous = json.loads(state_path.read_text("utf-8")) if state_path.is_file() else {}
    authors = ", ".join(meta.get("authors") or []) if isinstance(meta.get("authors"), list) else ""
    year = str(meta.get("year") or meta.get("date") or "")[:4]
    cover_digest = hashlib.sha256(cover.read_bytes()).hexdigest() if cover else ""
    state: dict[str, Any] = {}
    files: list[Path] = []
    playlist: list[tuple[str, int, str]] = []
    encoded = 0
    _detail(progress, "assemble", parts=len(split.parts), reason=split.reason)
    if progress:
        progress("assemble", 0, len(split.parts))
    for n, (part, name) in enumerate(zip(split.parts, names, strict=True), 1):
        tags = {
            "title": part.title,
            "album": title,
            "artist": authors or title,
            "album_artist": authors or title,
            "track": f"{n}/{len(split.parts)}",
            "genre": "Audiobook",
            "narrator": narrator,
            "encoded_by": f"text2audio {__version__}",
        }
        if year:
            tags["date"] = year
        chapters = [
            Chapter(c.title, _offset(items, c.start), _offset(items, c.end))
            for c in (split.chapters if split.single else [])
        ]
        fingerprint = hashlib.sha256(
            json.dumps(
                [
                    [(s.hash, s.pause_after_ms) for s in segments[part.start : part.end]],
                    tags,
                    [(c.title, c.start_ms, c.end_ms) for c in chapters],
                    cover_digest,
                    config.output.loudness,
                ],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        out = folder.path / name
        state[name] = fingerprint
        if previous.get(name) != fingerprint or not out.is_file():
            _detail(progress, "assemble", current=name)
            wav = parts_dir / f"{n:04d}.wav"
            with PartWriter(wav, ASSEMBLY_RATE) as writer:
                for seg in segments[part.start : part.end]:
                    path = audio_dir / f"{(seg.hash or '')[:24]}.wav"
                    if path.is_file():
                        writer.add(speech(path))
                    writer.silence(seg.pause_after_ms)
            encode_mp3(ffmpeg, wav, out, tags=tags, cover=cover, chapters=chapters)
            wav.unlink(missing_ok=True)
            encoded += 1
            _detail(progress, "assemble", encoded=encoded)
        files.append(out)
        playlist.append((name, part.duration_ms, part.title))
        if progress:
            progress("assemble", n, len(split.parts))
    for stale in set(previous) - set(state):
        (folder.path / stale).unlink(missing_ok=True)
    write_json(state_path, state)
    if not split.single:
        write_m3u(folder.path / f"{folder.path.name}.m3u", playlist)
    else:
        (folder.path / f"{folder.path.name}.m3u").unlink(missing_ok=True)
    return files, split


def _offset(items: list[splitting.Item], index: int) -> int:
    from text2audio.audio.wav import LEAD_IN_MS

    return LEAD_IN_MS + sum(i.duration_ms for i in items[:index])


# --------------------------------------------------------------------------- entry


def synthesize(
    path: Path,
    config: Config,
    *,
    store: ModelStore | None = None,
    tier: Tier | None = None,
    max_workers: int | None = None,
    progress: Progress | None = None,
) -> SynthesisReport:
    folder = BookFolder(path)
    if not folder.markdown.is_file():
        raise SynthesisError(f"{path}: not a book folder (no {folder.markdown.name})")
    store = store or ModelStore()
    started = time.monotonic()
    plan = make_plan(folder, config, tier)
    meta = folder.metadata() or {}
    voices = plan.profile.voices
    narrator = voices.get("narrator", {}).get(plan.route.backend, plan.route.backend)
    _detail(
        progress,
        "plan",
        lang=plan.lang,
        tier=plan.tier,
        backend=plan.route.backend,
        voice=narrator,
        notes=plan.notes,
    )
    segments, uncertain = prepare(folder, plan, config, store, progress)
    write_manifest(folder.work / "segments.jsonl", segments)
    journal = Journal(folder.work / "render.jsonl")
    stats = render(folder, plan, segments, config, store, journal, progress, max_workers)
    for seg in segments:
        entry = journal.get(seg.hash or "")
        seg.status = "done" if entry else "failed"
        seg.duration_ms = int(entry["duration_ms"]) if entry else None
    write_manifest(folder.work / "segments.jsonl", segments)
    files, split = assemble(folder, segments, journal, config, meta, narrator, plan, progress)
    report = _report(folder, plan, segments, journal, stats, split, files, uncertain, meta)
    report["wall_s"] = round(time.monotonic() - started, 2)
    write_json(folder.path / "report.json", report)
    return SynthesisReport(folder.path, files, report)


def _report(
    folder: BookFolder,
    plan: Plan,
    segments: list[Segment],
    journal: Journal,
    stats: dict[str, Any],
    split: splitting.SplitPlan,
    files: list[Path],
    uncertain: list[dict[str, str]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    entries = [journal.get(s.hash or "") for s in segments]
    audio_ms = sum(int(e["duration_ms"]) for e in entries if e) + sum(
        s.pause_after_ms for s in segments
    )
    synth_ms = sum(int(e["duration_ms"]) for e in entries if e)
    elapsed = sum(int(e.get("elapsed_ms", 0)) for e in entries if e)
    chapters: dict[int, int] = {}
    for seg, e in zip(segments, entries, strict=True):
        chapters[seg.chapter] = (
            chapters.get(seg.chapter, 0) + (int(e["duration_ms"]) if e else 0) + seg.pause_after_ms
        )
    titles = {s.chapter: s.text for s in segments if s.heading_level and s.heading_level <= 2}
    warnings = [
        {"segment": seg.id, "warning": w}
        for seg, e in zip(segments, entries, strict=True)
        if e
        for w in e.get("warnings", [])
    ]
    return {
        "text2audio": __version__,
        "book": meta.get("title") or folder.path.name,
        "language": plan.lang,
        "tier": plan.tier,
        "route": plan.route.model_dump(),
        "notes": plan.notes,
        "segments": {
            "total": len(segments),
            "rendered_now": stats.get("rendered", 0),
            "cached": len(segments) - stats.get("rendered", 0),
            "failed": sum(1 for s in segments if s.status == "failed"),
            "failures": stats.get("failures", []),
        },
        "duration_ms": audio_ms,
        "speech_ms": synth_ms,
        "synthesis": {
            "workers": stats.get("workers", 0),
            "elapsed_ms": elapsed,
            # RTF = synthesis time ÷ audio duration, per worker (NFR-05).
            "rtf": round(elapsed / synth_ms, 4) if synth_ms else None,
            "wall_s_this_run": stats.get("wall_s", 0.0),
        },
        "chapters": [
            {"chapter": c, "title": titles.get(c, ""), "duration_ms": ms}
            for c, ms in sorted(chapters.items())
        ],
        "split": split.summary(),
        "files": [f.name for f in files],
        "normalization": {"uncertain": uncertain},
        "warnings": warnings,
        "qa": {"mode": "off"},
    }
