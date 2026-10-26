"""`synthesize`: book folder → audiobook (SPEC §5.1, §6.8, FR-08, FR-10, FR-17).

    <book>.md → segments → lexicon → normalization → foreign spans → stress → route + hash
              → render missing segments in workers (journaled) → QA gate (ASR, retries)
              → split plan → part WAVs → loudness + MP3 + tags → playlist → report.json

Language-specific work is done by the components the language profile names; the core
never imports a model or a language (§6.6.1). Every segment's audio is cached by a
content hash, so editing a paragraph re-renders only its segments, and an interrupted
run resumes where it stopped.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from text2audio import __version__
from text2audio.audio import split as splitting
from text2audio.audio.encode import Chapter, encode_mp3, find_ffmpeg, write_m3u
from text2audio.audio.srt import Cue, split_cue, write_srt
from text2audio.audio.wav import ASSEMBLY_RATE, LEAD_IN_MS, PartWriter, speech
from text2audio.core import report_md
from text2audio.core import resources as res
from text2audio.core.config import Config, parse_qa_mode
from text2audio.core.contracts import (
    BackendDescriptor,
    LanguageProfile,
    Normalizer,
    Segment,
    Span,
    Tier,
    TierRoute,
    Transliterator,
)
from text2audio.core.errors import SynthesisError
from text2audio.core.journal import Journal
from text2audio.core.languages import profile as language_profile
from text2audio.core.plugins import discover
from text2audio.core.pool import Pool, cpu_slots, parallel
from text2audio.core.workspace import BookFolder, sanitize_name, write_json
from text2audio.models.lock import assets_for, load_lock
from text2audio.models.store import ModelStore
from text2audio.qa import gate
from text2audio.text.lexicon import Lexicon, user_lexicon
from text2audio.text.segment import PAUSE_MS, segment_markdown, write_manifest
from text2audio.text.spans import foreign_spans, replace_spans
from text2audio.text.stress import acute_to_plus, merge_marks, strip_marks
from text2audio.workers.backend import Worker, WorkerBackend

__all__ = ["SynthesisError", "synthesize"]

HASH_VERSION = 1  # bump when the meaning of a segment hash changes
DEFAULT_MAX_WORKERS = 8  # GPUs and CPUs are shared with other jobs (§9.1)
STRESS_BATCH = 32
SEGMENTS_PER_WORKER = 25  # don't start a worker for fewer pending segments than this
_FOREIGN = ("transliterated", "unresolved")  # span resolutions that go to the foreign backend

# (stage, done, total): `done == 0` when a stage starts, `done == total` when it ends.
# The callable may also have a `detail(stage, **fields)` method for facts a UI can show
# (route, cached counts, workers, audio rendered, failures); see `_detail` call sites.
Progress = Callable[[str, int, int], None]


def _detail(progress: Progress | None, stage: str, **fields: Any) -> None:
    hook = getattr(progress, "detail", None)
    if hook is not None:
        hook(stage, **fields)


class Timings(dict[str, float]):
    """Wall seconds per stage, for report.json (`timings_s`)."""

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.monotonic()
        try:
            yield
        finally:
            self[name] = round(self.get(name, 0.0) + time.monotonic() - started, 2)


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
    polisher: BackendDescriptor | None = None  # re-voices the backend's audio (§6.6.6)
    foreign: BackendDescriptor | None = None  # reads segments with foreign words (FR-24)


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
    found = discover(("backends", "normalizers", "g2p", "polishers"))
    overrides = config.lang.get(lang, {})
    notes: list[str] = []
    wanted = tier or config.synthesis.tier
    order: list[Tier] = [wanted] if wanted else ["expressive", "fast"]
    chosen: tuple[Tier, TierRoute, Any, Any, Any] | None = None
    for t in order:
        route = prof.tiers.get(t)
        if route is None:
            notes.append(f"tier {t}: not defined by the {lang!r} pack")
            continue
        # [lang.ru] backend.fast = "…", polish.expressive = "…", foreign.expressive = "…"
        names = {
            key: _override(overrides.get(key), t, getattr(route, key))
            for key in ("backend", "polish", "foreign")
        }
        backend = found.get("backends", names["backend"] or "")
        polisher = found.get("polishers", names["polish"]) if names["polish"] else None
        foreign = found.get("backends", names["foreign"]) if names["foreign"] else None
        missing = [
            f"{key} {names[key]!r}"
            for key, d in (("backend", backend), ("polish", polisher), ("foreign", foreign))
            if (names[key] or key == "backend") and d is None
        ]
        if missing:
            notes.append(f"tier {t}: {', '.join(missing)} not installed")
            continue
        if t == "expressive" and not wanted:
            # The GPU stage decides: the polisher / foreign backend, else the backend.
            gpu = next(
                (d for d in (polisher, foreign) if d and "cuda" in d.capabilities.devices),
                backend,
            )
            if res.plan(res.probe(), gpu.capabilities).tier != "expressive":
                notes.append("tier expressive: no GPU with enough free VRAM")
                continue
        chosen = (t, route.model_copy(update=names), backend, polisher, foreign)
        break
    if chosen is None:
        raise SynthesisError("no usable synthesis route: " + "; ".join(notes))
    t, route, backend, polisher, foreign = chosen
    normalizer = found.get("normalizers", prof.normalizer) if prof.normalizer else None
    if prof.normalizer and normalizer is None:
        raise SynthesisError(f"normalizer {prof.normalizer!r} of the {lang!r} pack is missing")
    g2p_name = str(overrides.get("g2p", prof.g2p or ""))
    g2p = None
    if g2p_name and g2p_name != "backend":
        g2p = found.get("g2p", g2p_name)
        if g2p is None:
            raise SynthesisError(f"stress component {g2p_name!r} is not installed")
    return Plan(lang, prof, t, route, backend, normalizer, g2p, notes, polisher, foreign)


def _override(value: Any, tier: Tier, default: str | None) -> str | None:
    if isinstance(value, dict) and tier in value:
        return str(value[tier]) or None
    return default


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


def polish_hash(source_hash: str, polisher: str, voice: str, model: str, seed: int) -> str:
    """Hash of a polished segment: its source audio plus what re-voiced it."""
    data = {"v": HASH_VERSION, "source": source_hash, "polish": polisher}
    data |= {"voice": voice, "model": model, "seed": str(seed)}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _voice_key(voice_id: str) -> str:
    """A reference clip counts by its content, so the hash does not depend on its path."""
    path = Path(voice_id)
    if voice_id and path.is_file():
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return voice_id


def _voice(prof: LanguageProfile, seg: Segment, backend: str) -> str:
    voices = prof.voices
    return voices.get(seg.voice, voices.get("narrator", {})).get(backend, "")


# --------------------------------------------------------------------------- text


def _pause_map(prof: LanguageProfile) -> dict[int, int]:
    return {PAUSE_MS[k]: v for k, v in prof.pauses_ms.items() if k in PAUSE_MS}


def prepare(
    folder: BookFolder,
    plan: Plan,
    config: Config,
    store: ModelStore,
    progress: Progress | None = None,
    timings: Timings | None = None,
) -> tuple[list[Segment], list[dict[str, str]], dict[str, Segment]]:
    """Segments with normalized, stressed text, route and hash; uncertain spans; and the
    backend-rendered source of every segment on a polish route, by the segment's hash.

    Routes (§6.6.6): `<backend>`; `<backend>><polisher>` (the source is the segment as the
    backend alone would render it, same hash, so fast-tier audio is reused); `<foreign>`
    for segments with words the pack could only transliterate (FR-24) — that backend
    reads them in their own script, while `text_norm` keeps the transliteration for QA.
    """
    prof = plan.profile
    timings = Timings() if timings is None else timings
    with timings.stage("segment"):
        segments = segment_markdown(
            folder.markdown.read_text(encoding="utf-8"),
            plan.lang,
            prof,
            seed=config.synthesis.seed,
        )
    lexicon = Lexicon.layered(
        Path(prof.lexicon) if prof.lexicon else None,
        user_lexicon(plan.lang),
        folder.work / "lexicon.tsv",
    )
    pauses = _pause_map(prof)
    uncertain: list[dict[str, str]] = []
    marked: dict[str, str] = {}
    foreign_ids: set[str] = set()
    translit = plan.normalizer if isinstance(plan.normalizer, Transliterator) else None
    with timings.stage("normalize"):
        for i, seg in enumerate(segments, 1):
            found = foreign_spans(seg.text, prof.script)
            text = lexicon.apply(seg.text)
            left = Counter(r.text for r in foreign_spans(text, prof.script))
            spans: list[Span] = []
            for r in found:
                if left[r.text]:
                    left[r.text] -= 1
                else:
                    reading = lexicon.entries.get(r.text.lower())
                    spans.append(
                        Span(text=r.text, script=r.script, resolution="lexicon", reading=reading)
                    )
            text = acute_to_plus(text)
            if plan.normalizer is not None:
                result = plan.normalizer.normalize(text, seg.role)
                text = result.text
                uncertain += [{"segment": seg.id, "span": s} for s in result.uncertain]
                spans += result.resolved
            kept = text
            text = _resolve_foreign(text, prof.script, translit, spans)
            seg.spans = spans
            seg.text_norm = strip_marks(text)
            marked[seg.id] = text
            if plan.foreign is not None and any(s.resolution in _FOREIGN for s in spans):
                foreign_ids.add(seg.id)
                marked[seg.id] = kept
            seg.pause_after_ms = pauses.get(seg.pause_after_ms, seg.pause_after_ms)
            if progress:
                progress("normalize", i, len(segments))
    _detail(progress, "normalize", segments=len(segments), uncertain=len(uncertain))
    with timings.stage("stress"):
        stressed = _stress(folder, plan, store, list(marked.values()), progress)
    lock = load_lock()
    backend = plan.route.backend
    model = _model_id(backend, lock)
    keys: dict[str, str] = {}

    def voice_key(seg: Segment, name: str) -> str:
        voice = _voice(prof, seg, name)
        if voice not in keys:
            keys[voice] = _voice_key(voice)
        return keys[voice]

    sources: dict[str, Segment] = {}
    for seg in segments:
        text = marked[seg.id]
        seg.text_stress = (
            merge_marks(text, stressed[strip_marks(text)])
            if plan.g2p
            else (text if "+" in text else None)
        )
        if plan.foreign is not None and seg.id in foreign_ids:
            seg.text_stress = seg.text_stress or text  # unlike text_norm, keeps the Latin
            seg.backend = seg.route = plan.foreign.name
            seg.hash = segment_hash(seg, voice_key(seg, seg.backend), _model_id(seg.backend, lock))
            continue
        seg.backend = seg.route = backend
        seg.hash = segment_hash(seg, _voice(prof, seg, backend), model)
        if plan.polisher is not None:
            source = seg.model_copy()
            name = plan.polisher.name
            seg.route = f"{backend}>{name}"
            seg.hash = polish_hash(
                source.hash or "", name, voice_key(seg, name), _model_id(name, lock), seg.seed
            )
            sources[seg.hash] = source
    return segments, uncertain, sources


def _resolve_foreign(
    text: str, script: str, translit: Transliterator | None, spans: list[Span]
) -> str:
    """Spans left after the lexicon and the normalizer: the pack's reading, or flagged."""
    runs = foreign_spans(text, script)
    readings = {
        r.text: translit.transliterate(r.text, r.script) if translit is not None else None
        for r in runs
    }
    for r in runs:
        reading = readings[r.text]
        how = "unresolved" if reading is None else "transliterated"
        spans.append(Span(text=r.text, script=r.script, resolution=how, reading=reading))
    readable = [r for r in runs if readings[r.text] is not None]
    return replace_spans(text, readable, lambda r: readings[r.text] or "")


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
        slots = cpu_slots(
            threads, ram_mb=1500, count=max(1, min(DEFAULT_MAX_WORKERS, len(batches) // 8))
        )
        done = [0]
        lock = threading.Lock()
        _detail(progress, "stress", workers=len(slots))
        if progress:
            progress("stress", 0, len(todo))

        def work(worker: Worker, batch: list[str]) -> None:
            out = worker.call("process", texts=batch)["texts"]
            for src, dst in zip(batch, out, strict=True):
                journal.add({"key": key(src), "text": dst})
            with lock:
                done[0] += len(batch)
                if progress:
                    progress("stress", done[0], len(todo))

        parallel(
            [Worker(plan.g2p, store.root, slot) for slot in slots],
            batches,
            work,
            load={"assets": payload},
        )
    return {t: str(journal.entries[key(t)]["text"]) for t in plain if key(t) in journal}


# --------------------------------------------------------------------------- workers

# A segment's request on a worker for one attempt: (op, fields).
Job = Callable[[Segment, int], tuple[str, dict[str, Any]]]


def _job(plan: Plan, backend: str, sources: dict[str, Segment], audio_dir: Path) -> Job:
    """`synthesize` the segment, or `polish` its source audio if it has one."""

    def job(seg: Segment, attempt: int) -> tuple[str, dict[str, Any]]:
        fields: dict[str, Any] = {
            "segment": seg.model_dump(mode="json"),
            "out": str(gate.wav_path(audio_dir, seg.hash or "", attempt)),
            "voice_id": _voice(plan.profile, seg, backend),
            "attempt": attempt,
        }
        source = sources.get(seg.hash or "")
        if source is None:
            return "synthesize", fields
        return "polish", fields | {"source": str(gate.wav_path(audio_dir, source.hash or ""))}

    return job


def _gpu_groups(
    plan: Plan, segments: list[Segment], sources: dict[str, Segment]
) -> dict[str, tuple[BackendDescriptor, list[Segment]]]:
    """Segments of the polish and foreign routes, by the worker that renders them."""
    groups: dict[str, tuple[BackendDescriptor, list[Segment]]] = {}
    for seg in segments:
        if seg.hash in sources:
            d = plan.polisher
        elif plan.foreign is not None and seg.route == plan.foreign.name:
            d = plan.foreign
        else:
            continue
        assert d is not None
        groups.setdefault(d.name, (d, []))[1].append(seg)
    return groups


def _slots(
    backend: BackendDescriptor,
    tier: Tier,
    pending: int,
    config: Config,
    max_workers: int | None,
) -> list[res.WorkerSlot]:
    rc = config.resources
    slots = res.plan(
        res.probe(),
        backend.capabilities,
        tier=tier,
        devices=rc.devices,
        max_workers=rc.max_workers,
        threads_per_worker=rc.threads_per_worker,
    ).slots
    if not slots:
        raise SynthesisError(f"no device to run {backend.name!r} on")
    cap = max_workers or rc.max_workers or DEFAULT_MAX_WORKERS
    n = max(1, min(len(slots), cap, math.ceil(pending / SEGMENTS_PER_WORKER)))
    return slots[:n]


class Pools:
    """Backend worker pools kept loaded across polish and QA batches (§6.9).

    One pool per backend and tier, sized by the first batch that needs it; closed at
    the end of the run.
    """

    def __init__(self, config: Config, store: ModelStore, max_workers: int | None) -> None:
        self.config = config
        self.store = store
        self.max_workers = max_workers
        self.pools: dict[tuple[str, Tier], tuple[Pool, list[res.WorkerSlot]]] = {}

    def get(
        self, backend: BackendDescriptor, tier: Tier, pending: int
    ) -> tuple[Pool, list[res.WorkerSlot]]:
        key = (backend.name, tier)
        if key not in self.pools:
            slots = _slots(backend, tier, pending, self.config, self.max_workers)
            payload = self.store.payload(assets_for(backend.name, load_lock()))
            workers = [WorkerBackend(backend, self.store.root, slot) for slot in slots]
            self.pools[key] = (Pool(workers, {"assets": payload}), slots)
        return self.pools[key]

    def close(self) -> None:
        for pool, _ in self.pools.values():
            pool.close()


def _render_pool(
    backend: BackendDescriptor,
    tier: Tier,
    segments: list[Segment],
    job: Job,
    config: Config,
    store: ModelStore,
    on_done: Callable[[Segment, dict[str, Any]], None],
    on_fail: Callable[[Segment, Exception], None],
    max_workers: int | None = None,
    attempt: int = 1,
    on_start: Callable[[list[res.WorkerSlot]], None] | None = None,
    pools: Pools | None = None,
) -> list[res.WorkerSlot]:
    """Run `job` for `segments` on `backend` workers (from `pools` if given); their slots."""

    def work(worker: WorkerBackend, seg: Segment) -> None:
        op, fields = job(seg, attempt)
        on_done(seg, worker.call(op, **fields))

    if pools is not None:
        pool, slots = pools.get(backend, tier, len(segments))
        if on_start:
            on_start(slots)
        pool.run(segments, work, on_error=on_fail)
        return slots
    slots = _slots(backend, tier, len(segments), config, max_workers)
    payload = store.payload(assets_for(backend.name, load_lock()))
    workers = [WorkerBackend(backend, store.root, slot) for slot in slots]
    if on_start:
        on_start(slots)
    parallel(workers, segments, work, load={"assets": payload}, on_error=on_fail)
    return slots


def render(
    folder: BookFolder,
    backend: BackendDescriptor,
    tier: Tier,
    segments: list[Segment],
    job: Job,
    config: Config,
    store: ModelStore,
    journal: Journal,
    progress: Progress | None = None,
    max_workers: int | None = None,
    stage: str = "synthesize",
    sources: dict[str, Segment] | None = None,
    pools: Pools | None = None,
) -> dict[str, Any]:
    """Render every segment whose hash is not in the journal yet on `backend` workers."""
    audio_dir = folder.work / "audio" / "segments"
    sources = sources or {}
    unique: dict[str, Segment] = {}
    cached = duplicate = 0
    for seg in segments:
        assert seg.hash is not None
        entry = journal.get(seg.hash)
        if entry is not None and gate.wav_path(audio_dir, seg.hash).is_file():
            cached += 1  # rendered by an earlier run
        elif seg.hash in unique:
            duplicate += 1  # same text and settings as a segment rendered now
        else:
            unique[seg.hash] = seg
    pending = list(unique.values())
    stats: dict[str, Any] = {
        "rendered": 0,
        "cached": cached,
        "duplicate": duplicate,
        "elapsed_ms": 0,
        "workers": 0,
        "device": None,
        "wall_s": 0.0,
    }
    _detail(progress, stage, segments=len(segments), pending=len(pending))
    if not pending:
        return stats
    lock = threading.Lock()
    started = time.monotonic()
    processed = [0]  # rendered + failed, so the bar ends even when segments fail
    audio_ms = [0]

    def done(seg: Segment, reply: dict[str, Any]) -> None:
        assert seg.hash is not None
        entry: dict[str, Any] = {
            "hash": seg.hash,
            "segment": seg.id,
            "duration_ms": int(reply["duration_ms"]),
            "elapsed_ms": int(reply.get("elapsed_ms", 0)),
            "backend": backend.name,
            "warnings": reply.get("warnings", []),
        }
        if seg.hash in sources:
            entry["source"] = sources[seg.hash].hash
        journal.add(entry)
        with lock:
            stats["rendered"] += 1
            stats["elapsed_ms"] += int(reply.get("elapsed_ms", 0))
            processed[0] += 1
            audio_ms[0] += int(reply["duration_ms"])
            _detail(progress, stage, audio_ms=audio_ms[0])
            if progress:
                progress(stage, processed[0], len(pending))

    def failed(seg: Segment, exc: Exception) -> None:
        with lock:
            stats.setdefault("failures", []).append({"segment": seg.id, "error": str(exc)})
            processed[0] += 1
            _detail(progress, stage, failed=seg.id, error=str(exc))
            if progress:
                progress(stage, processed[0], len(pending))

    def start(slots: list[res.WorkerSlot]) -> None:
        _detail(progress, stage, workers=len(slots), backend=backend.name)
        if progress:
            progress(stage, 0, len(pending))

    slots = _render_pool(
        backend,
        tier,
        pending,
        job,
        config,
        store,
        done,
        failed,
        max_workers,
        on_start=start,
        pools=pools,
    )
    stats["workers"] = len(slots)
    stats["device"] = slots[0].device
    stats["wall_s"] = round(time.monotonic() - started, 2)
    return stats


def rerenderer(
    folder: BookFolder,
    plan: Plan,
    sources: dict[str, Segment],
    config: Config,
    store: ModelStore,
    journal: Journal,
    pools: Pools,
    warnings: list[dict[str, str]],
) -> gate.Rerender:
    """`gate.Rerender` for this book: attempt `n` of a segment goes to `<hash>.a<n>.wav`.

    A polished segment is polished again with a new seed; its last attempt is the
    unpolished source audio, marked `final` (§6.6.6 fallback).
    """
    audio_dir = folder.work / "audio" / "segments"

    def rerender(segments: list[Segment], attempt: int) -> dict[str, dict[str, Any]]:
        replies: dict[str, dict[str, Any]] = {}
        lock = threading.Lock()

        def done(seg: Segment, reply: dict[str, Any]) -> None:
            with lock:
                replies[seg.hash or ""] = reply

        def failed(seg: Segment, exc: Exception) -> None:
            with lock:
                warnings.append({"segment": seg.id, "warning": f"attempt {attempt}: {exc}"})

        last = attempt >= gate.MAX_ATTEMPTS
        for seg in segments:
            source = sources.get(seg.hash or "")
            entry = journal.get(source.hash or "") if source else None
            if last and source is not None and entry is not None:
                src = gate.wav_path(audio_dir, source.hash or "")
                shutil.copyfile(src, gate.wav_path(audio_dir, seg.hash or "", attempt))
                replies[seg.hash or ""] = {"duration_ms": entry["duration_ms"], "final": True}
        own = [s for s in segments if s.hash not in sources and s.route == plan.route.backend]
        groups = _gpu_groups(plan, [s for s in segments if s.hash not in replies], sources)
        runs: list[tuple[BackendDescriptor, Tier, list[Segment], dict[str, Segment]]] = []
        if own:
            runs.append((plan.backend, plan.tier, own, {}))
        runs += [(d, "expressive", segs, sources) for d, segs in groups.values()]
        for backend, tier, segs, srcs in runs:
            _render_pool(
                backend,
                tier,
                segs,
                _job(plan, backend.name, srcs, audio_dir),
                config,
                store,
                done,
                failed,
                attempt=attempt,
                pools=pools,
            )
        return replies

    return rerender


def _ready(segments: list[Segment], sources: dict[str, Segment], journal: Journal) -> list[Segment]:
    """Segments whose source audio exists: a segment whose source failed stays failed."""
    return [s for s in segments if s.hash not in sources or (sources[s.hash].hash or "") in journal]


def _add_stats(total: dict[str, Any], part: dict[str, Any]) -> None:
    for key in ("rendered", "cached", "duplicate", "elapsed_ms", "wall_s"):
        total[key] = total.get(key, 0) + part[key]
    total["wall_s"] = round(total["wall_s"], 2)
    total["workers"] = max(total.get("workers", 0), part["workers"])
    total["device"] = total.get("device") or part["device"]


class _Batches:
    """Progress for stages that run batch by batch: one bar over all batches.

    `totals` is each stage's expected count for the run; `finish` closes a bar that
    ended short (failed segments, sample hashes that were never rendered).
    """

    def __init__(self, progress: Progress | None, totals: dict[str, int]) -> None:
        self.progress = progress
        self.totals = totals
        self.base = dict.fromkeys(totals, 0)

    def __call__(self, stage: str, done: int, total: int) -> None:
        if self.progress is None:
            return
        if stage not in self.totals:
            self.progress(stage, done, total)
            return
        overall = self.base[stage] + done
        self.totals[stage] = max(self.totals[stage], overall)
        self.progress(stage, overall, self.totals[stage])
        if done >= total:
            self.base[stage] = overall

    def detail(self, stage: str, **fields: Any) -> None:
        _detail(self.progress, stage, **fields)

    def finish(self) -> None:
        for stage, done in self.base.items():
            if self.progress is not None and 0 < done < self.totals[stage]:
                self.progress(stage, done, done)


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
    jobs: list[tuple[int, splitting.Part, Path, dict[str, str], list[Chapter]]] = []
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
            jobs.append((n, part, out, tags, chapters))
        files.append(out)
        playlist.append((name, part.duration_ms, part.title))

    subtitles = config.output.subtitles
    if subtitles:  # parts whose MP3 is current but whose subtitles are missing
        current = {j[2] for j in jobs}
        for n, part in enumerate(split.parts, 1):
            out = folder.path / names[n - 1]
            if out not in current and not out.with_suffix(".srt").is_file():
                write_srt(
                    out.with_suffix(".srt"),
                    _part_audio(segments[part.start : part.end], audio_dir, None),
                )

    def encode(job: tuple[int, splitting.Part, Path, dict[str, str], list[Chapter]]) -> None:
        n, part, out, tags, chapters = job
        wav = parts_dir / f"{n:04d}.wav"
        with PartWriter(wav, ASSEMBLY_RATE) as writer:
            cues = _part_audio(segments[part.start : part.end], audio_dir, writer)
        encode_mp3(ffmpeg, wav, out, tags=tags, cover=cover, chapters=chapters)
        wav.unlink(missing_ok=True)
        if subtitles:
            write_srt(out.with_suffix(".srt"), cues)

    # Parts are independent and FFmpeg's loudnorm is single-threaded: encode in parallel.
    threads = max(1, min(len(jobs), res.probe().usable_cpus, DEFAULT_MAX_WORKERS))
    unchanged = len(split.parts) - len(jobs)
    _detail(progress, "assemble", parts=len(split.parts), reason=split.reason, threads=threads)
    if progress:
        progress("assemble", unchanged, len(split.parts))
    with ThreadPoolExecutor(max_workers=threads) as pool:
        for encoded, _ in enumerate(pool.map(encode, jobs), 1):
            _detail(progress, "assemble", encoded=encoded)
            if progress:
                progress("assemble", unchanged + encoded, len(split.parts))
    for stale in set(previous) - set(state):
        (folder.path / stale).unlink(missing_ok=True)
        (folder.path / stale).with_suffix(".srt").unlink(missing_ok=True)
    if not subtitles:
        for out in files:
            out.with_suffix(".srt").unlink(missing_ok=True)
    write_json(state_path, state)
    if not split.single:
        write_m3u(folder.path / f"{folder.path.name}.m3u", playlist)
    else:
        (folder.path / f"{folder.path.name}.m3u").unlink(missing_ok=True)
    return files, split


def _part_audio(segments: list[Segment], audio_dir: Path, writer: PartWriter | None) -> list[Cue]:
    """Write a part's audio (or only measure it) and return its subtitle cues."""
    frames = ASSEMBLY_RATE * LEAD_IN_MS // 1000 if writer is None else writer.frames
    cues: list[Cue] = []
    for seg in segments:
        path = gate.wav_path(audio_dir, seg.hash or "")
        if path.is_file():
            samples = speech(path)
            start = frames * 1000 // ASSEMBLY_RATE
            if writer is not None:
                writer.add(samples)
            frames += samples.size
            cues += split_cue(start, frames * 1000 // ASSEMBLY_RATE, seg.text)
        if writer is not None:
            writer.silence(seg.pause_after_ms)
        frames += ASSEMBLY_RATE * seg.pause_after_ms // 1000
    return cues


def _offset(items: list[splitting.Item], index: int) -> int:
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
    timings = Timings()
    qa_percent = parse_qa_mode(config.qa.mode)
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
        polish=plan.route.polish,
        foreign=plan.route.foreign,
        voice=narrator,
        notes=plan.notes,
    )
    segments, uncertain, sources = prepare(folder, plan, config, store, progress, timings)
    write_manifest(folder.work / "segments.jsonl", segments)
    journal = Journal(folder.work / "render.jsonl")
    audio_dir = folder.work / "audio" / "segments"
    with timings.stage("synthesize"):
        own = [
            sources.get(s.hash or "", s)
            for s in segments
            if s.hash in sources or s.route == plan.route.backend
        ]
        stats = render(
            folder,
            plan.backend,
            plan.tier,
            own,
            _job(plan, plan.backend.name, {}, audio_dir),
            config,
            store,
            journal,
            progress,
            max_workers,
        )
    retry_warnings: list[dict[str, str]] = []
    pools = Pools(config, store, max_workers)
    qa = gate.Gate(
        segments,
        journal,
        audio_dir,
        plan.profile,
        config,
        store,
        rerenderer(folder, plan, sources, config, store, journal, pools, retry_warnings),
    )
    polishing = {
        s.hash or ""
        for _, segs in _gpu_groups(plan, _ready(segments, sources, journal), sources).values()
        for s in segs
        if not ((s.hash or "") in journal and gate.wav_path(audio_dir, s.hash or "").is_file())
    }
    batched = _Batches(progress, {"polish": len(polishing), "qa": qa.todo})
    qa.progress = batched
    polish_stats: dict[str, Any] = {}
    try:
        # Polish, then QA, a batch at a time (§6.9); the worker pools stay loaded.
        for i in range(0, len(segments), config.qa.batch):
            batch = segments[i : i + config.qa.batch]
            groups = _gpu_groups(plan, _ready(batch, sources, journal), sources)
            if groups:
                with timings.stage("polish"):
                    for backend, segs in groups.values():
                        part = render(
                            folder,
                            backend,
                            "expressive",
                            segs,
                            _job(plan, backend.name, sources, audio_dir),
                            config,
                            store,
                            journal,
                            batched,
                            max_workers,
                            stage="polish",
                            sources=sources,
                            pools=pools,
                        )
                        _add_stats(polish_stats, part)
                        stats.setdefault("failures", []).extend(part.get("failures", []))
            with timings.stage("qa"):
                qa.check(batch)
    finally:
        qa.close()
        pools.close()
    batched.finish()
    for seg in segments:
        entry = journal.get(seg.hash or "")
        seg.status = "done" if entry else "failed"
        seg.duration_ms = int(entry["duration_ms"]) if entry else None
        if entry and "qa" in entry:
            seg.wer = entry["qa"]["wer"]
            seg.attempts = int(entry.get("attempts", 1))
            seg.flags = list(entry["qa"]["flags"])
            seg.status = "flagged" if entry.get("flagged") else "done"
    write_manifest(folder.work / "segments.jsonl", segments)
    with timings.stage("assemble"):
        files, split = assemble(folder, segments, journal, config, meta, narrator, plan, progress)
    report = _report(
        folder, plan, segments, sources, journal, stats, polish_stats, split, files, uncertain, meta
    )
    report["qa"] = gate.summary(segments, journal, qa.stats, qa_percent)
    if sources:
        # Polished segments whose kept attempt is the unpolished source (§6.6.6 fallback).
        report["qa"]["unpolished"] = sum(
            1
            for s in segments
            if s.hash in sources
            and (journal.get(s.hash or "") or {}).get("qa", {}).get("attempt") == gate.MAX_ATTEMPTS
        )
    report["warnings"] += retry_warnings
    report["wall_s"] = round(time.monotonic() - started, 2)
    if report["duration_ms"]:
        report["rtf_wall"] = round(report["wall_s"] * 1000 / report["duration_ms"], 4)
    timings["total"] = report["wall_s"]
    report["timings_s"] = dict(timings)
    write_json(folder.path / "report.json", report)
    (folder.path / "report.md").write_text(report_md.render(report), encoding="utf-8")
    return SynthesisReport(folder.path, files, report)


def _report(
    folder: BookFolder,
    plan: Plan,
    segments: list[Segment],
    sources: dict[str, Segment],
    journal: Journal,
    stats: dict[str, Any],
    polish_stats: dict[str, Any],
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
    backend = plan.route.backend
    own = [
        journal.get(sources[s.hash].hash or "") if s.hash in sources else e
        for s, e in zip(segments, entries, strict=True)
        if s.hash in sources or s.route == backend
    ]
    own_ms = sum(int(e["duration_ms"]) for e in own if e)
    elapsed = sum(int(e.get("elapsed_ms", 0)) for e in own if e)
    gpu = [e for s, e in zip(segments, entries, strict=True) if e and s.route != backend]
    gpu_ms = sum(int(e["duration_ms"]) for e in gpu)
    gpu_elapsed = sum(int(e.get("elapsed_ms", 0)) for e in gpu)
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
            "cached": stats.get("cached", 0),
            "duplicate": stats.get("duplicate", 0),
            "failed": sum(1 for s in segments if s.status == "failed"),
            "failures": stats.get("failures", []),
        },
        "duration_ms": audio_ms,
        "speech_ms": synth_ms,
        "synthesis": {
            "workers": stats.get("workers", 0),
            "elapsed_ms": elapsed,
            # RTF = synthesis time ÷ audio duration, per worker (NFR-05).
            "rtf": round(elapsed / own_ms, 4) if own_ms else None,
            "wall_s_this_run": stats.get("wall_s", 0.0),
        },
        # The GPU stage of the expressive tier: VC of the backend's audio and foreign-word
        # segments (§6.6.6); None on routes without one.
        "polish": {
            "backend": plan.route.polish or plan.route.foreign,
            "workers": polish_stats.get("workers", 0),
            "device": polish_stats.get("device"),
            "segments": len(gpu),
            "elapsed_ms": gpu_elapsed,
            "rtf": round(gpu_elapsed / gpu_ms, 4) if gpu_ms else None,
            "wall_s_this_run": polish_stats.get("wall_s", 0.0),
        }
        if plan.route.polish or plan.route.foreign
        else None,
        # Whole-run wall time ÷ audio duration (stress, QA and encoding included); set by
        # `synthesize` once the run is over.
        "rtf_wall": None,
        "chapters": [
            {"chapter": c, "title": titles.get(c, ""), "duration_ms": ms}
            for c, ms in sorted(chapters.items())
        ],
        "split": split.summary(),
        "files": [f.name for f in files],
        "parts": [
            {"file": f.name, "title": p.title, "duration_ms": p.duration_ms}
            for f, p in zip(files, split.parts, strict=True)
        ],
        "normalization": {"uncertain": uncertain},
        # FR-24: every foreign-script span and how it was read.
        "foreign_spans": [
            {"segment": seg.id, **span.model_dump()} for seg in segments for span in seg.spans
        ],
        "warnings": warnings,
    }
