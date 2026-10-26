"""QA gate: ASR round-trip, sanity checks and retries for rendered segments (§6.9, FR-12).

    the run's sample (≤ `[qa] max_segments`, by hash) → per batch: segments without a
    QA result → transcribe (ASR workers) → WER + checks → failures re-rendered with
    `attempt` 2, 3 → best attempt kept, the rest flagged

The ASR component is the one the language profile names; it runs in its own worker
environment like a backend, on a GPU when one has enough free VRAM, else on CPU slots.
Results go into the render journal, so a killed QA stage resumes where it stopped and a
rerun checks only segments that changed.
"""

from __future__ import annotations

import math
import threading
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from text2audio.core import resources as res
from text2audio.core.config import Config, parse_qa_mode
from text2audio.core.contracts import LanguageProfile, Segment
from text2audio.core.errors import SynthesisError
from text2audio.core.journal import Journal
from text2audio.core.plugins import discover
from text2audio.core.pool import Pool
from text2audio.models.lock import assets_for, load_lock
from text2audio.models.store import ModelStore
from text2audio.qa.checks import check_file
from text2audio.qa.wer import cer, drop_unheard, wer
from text2audio.workers.backend import Worker

MAX_ATTEMPTS = 3
ASR_BATCH = 16
MAX_GPU_WORKERS = 2  # GPUs are shared with other jobs (§9.1)
MAX_CPU_WORKERS = 8
SEGMENTS_PER_WORKER = 25
# FR-24 readings the QA ASR may be unable to write (GigaAM: Cyrillic letters only, §13.1)
UNVERIFIABLE = frozenset({"acronym", "transliterated", "foreign"})

# Re-renders `segments` as attempt `n` into `<hash>.a<n>.wav`; replies by hash.
Rerender = Callable[[list[Segment], int], dict[str, dict[str, Any]]]
Progress = Callable[[str, int, int], None]


def sampled(seg_hash: str, percent: int) -> bool:
    """Deterministic by hash, so reruns check the same segments."""
    return percent >= 100 or int(seg_hash[:8], 16) % 100 < percent


def select(segments: list[Segment], percent: int, limit: int) -> set[str]:
    """Hashes this run may check: sampled ones, then the first segments in hash order.

    Hash order spreads the sample over the book and keeps it stable across reruns.
    `limit` counts segments (a hash shared by several segments counts for each);
    0 = no limit.
    """
    counts = Counter(s.hash for s in segments if s.hash and sampled(s.hash, percent))
    if not limit:
        return set(counts)
    chosen: set[str] = set()
    n = 0
    for h in sorted(counts):
        if n + counts[h] <= limit:
            chosen.add(h)
            n += counts[h]
            if n == limit:
                break
    return chosen


def wav_path(audio_dir: Path, seg_hash: str, attempt: int = 1) -> Path:
    suffix = "" if attempt == 1 else f".a{attempt}"
    return audio_dir / f"{seg_hash[:24]}{suffix}.wav"


@dataclass
class Attempt:
    attempt: int
    wer: float
    cer: float
    heard: str
    flags: list[str]
    duration_ms: int | None = None
    unverified: list[str] = field(default_factory=list)

    @property
    def score(self) -> tuple[int, float, float]:
        checks = sum(1 for f in self.flags if f != "wer")
        return (checks, self.cer, self.wer)

    def passed(self) -> bool:
        return not self.flags


@dataclass
class QaStats:
    mode: str
    asr: str | None = None
    device: str | None = None
    threshold: float = 0.0
    cer_threshold: float = 0.0
    max_segments: int = 0
    sample: int = 0  # segments in this run's sample
    checked_now: int = 0
    retried_now: int = 0
    workers: int = 0
    wall_s: float = 0.0
    notes: list[str] = field(default_factory=list)


@dataclass
class _Context:
    profile: LanguageProfile
    threshold: float
    cer_threshold: float
    audio_dir: Path
    journal: Journal
    lock: threading.Lock = field(default_factory=threading.Lock)


def _detail(progress: Progress | None, stage: str, **fields: Any) -> None:
    hook = getattr(progress, "detail", None)
    if hook is not None:
        hook(stage, **fields)


def threshold_for(config: Config, profile: LanguageProfile) -> float:
    if config.qa.wer_threshold is not None:
        return config.qa.wer_threshold
    return profile.qa.wer_threshold


def cer_threshold_for(config: Config, profile: LanguageProfile) -> float:
    if config.qa.cer_threshold is not None:
        return config.qa.cer_threshold
    return profile.qa.cer_threshold


def asr_component(lang: str, profile: LanguageProfile, config: Config) -> Any:
    name = str(config.lang.get(lang, {}).get("asr", profile.qa.asr or ""))
    if not name:
        raise SynthesisError(f"the {lang!r} pack names no QA ASR; run with --qa off")
    found = discover(("asr",)).get("asr", name)
    if found is None:
        raise SynthesisError(f"QA ASR component {name!r} is not installed (or run with --qa off)")
    return found


def asr_slots(asr: Any, config: Config, pending: int) -> list[res.WorkerSlot]:
    """GPU slots if a GPU has room for the ASR model, else CPU slots (planned now)."""
    rc = config.resources
    plan = res.plan(res.probe(), asr.capabilities, devices=rc.devices)
    cap = MAX_GPU_WORKERS if plan.tier == "expressive" else MAX_CPU_WORKERS
    if rc.max_workers:
        cap = min(cap, rc.max_workers)
    n = max(1, min(len(plan.slots), cap, math.ceil(pending / SEGMENTS_PER_WORKER)))
    return plan.slots[:n]


def _evaluate(ctx: _Context, seg: Segment, path: Path, heard: str, attempt: int) -> Attempt:
    text = seg.speech_text
    letters = ctx.profile.qa.fold
    score = (wer(text, heard, letters).wer, cer(text, heard, letters))
    unverified: list[str] = []
    readings = [s.reading for s in seg.spans if s.resolution in UNVERIFIABLE and s.reading]
    if readings:
        # Dropping a reading ASR spelled its own way («эппл» for «эпл») turns one
        # substitution into an insertion, so the drop is kept only when it scores better.
        checked, dropped = drop_unheard(text, heard, readings, letters)
        reduced = (wer(checked, heard, letters).wer, cer(checked, heard, letters))
        if dropped and reduced < score:
            score, unverified = reduced, dropped
    flags = check_file(path, text, ctx.profile.qa.chars_per_sec)
    if score[0] > ctx.threshold and score[1] > ctx.cer_threshold:
        flags.insert(0, "wer")
    return Attempt(
        attempt, round(score[0], 4), round(score[1], 4), heard, flags, unverified=unverified
    )


def _record(ctx: _Context, seg_hash: str, attempts: list[Attempt]) -> None:
    """Keep the best attempt's audio as the segment's WAV and journal the outcome."""
    best = min(attempts, key=lambda a: a.score)
    for a in attempts:
        if a.attempt == 1:
            continue
        path = wav_path(ctx.audio_dir, seg_hash, a.attempt)
        if a is best and path.is_file():
            path.replace(wav_path(ctx.audio_dir, seg_hash))
        else:
            path.unlink(missing_ok=True)
    entry = dict(ctx.journal.get(seg_hash) or {"hash": seg_hash})
    if best.duration_ms is not None:
        entry["duration_ms"] = best.duration_ms
    entry["attempts"] = len(attempts)
    entry["flagged"] = not best.passed()
    entry["qa"] = {
        "wer": best.wer,
        "cer": best.cer,
        "heard": best.heard,
        "flags": best.flags,
        "unverified": best.unverified,
        "attempt": best.attempt,
        "attempts": [
            {"attempt": a.attempt, "wer": a.wer, "cer": a.cer, "flags": a.flags} for a in attempts
        ],
    }
    with ctx.lock:
        ctx.journal.add(entry)


def _transcribe(
    ctx: _Context,
    pool: Pool,
    jobs: list[tuple[str, Segment, Path, int]],
    on_result: Callable[[str, Attempt], None],
    progress: Progress | None,
    stage: str,
) -> None:
    """Transcribe `(hash, segment, wav, attempt)` jobs in batches on a worker pool."""
    batches = [jobs[i : i + ASR_BATCH] for i in range(0, len(jobs), ASR_BATCH)]
    done = [0]

    def work(worker: Worker, batch: list[tuple[str, Segment, Path, int]]) -> None:
        texts = worker.call("transcribe", paths=[str(p) for _, _, p, _ in batch])["texts"]
        for (h, seg, path, attempt), heard in zip(batch, texts, strict=True):
            on_result(h, _evaluate(ctx, seg, path, str(heard), attempt))
        with ctx.lock:
            done[0] += len(batch)
            if progress:
                progress(stage, done[0], len(jobs))

    if progress:
        progress(stage, 0, len(jobs))
    pool.run(batches, work)


class Gate:
    """The QA gate of one run: a fixed sample, checked batch by batch on one ASR pool."""

    def __init__(
        self,
        segments: list[Segment],
        journal: Journal,
        audio_dir: Path,
        profile: LanguageProfile,
        config: Config,
        store: ModelStore,
        rerender: Rerender,
        progress: Progress | None = None,
    ) -> None:
        self.percent = parse_qa_mode(config.qa.mode)
        self.config = config
        self.store = store
        self.rerender = rerender
        self.progress = progress
        self.stats = QaStats(
            mode=config.qa.mode,
            threshold=threshold_for(config, profile),
            cer_threshold=cer_threshold_for(config, profile),
            max_segments=config.qa.max_segments,
        )
        self.ctx = _Context(
            profile, self.stats.threshold, self.stats.cer_threshold, audio_dir, journal
        )
        self.sample: set[str] = set()
        self.asr: Any = None
        self.pool: Pool | None = None
        self.todo = 0
        if self.percent == 0:
            return
        self.asr = asr_component(profile.code, profile, config)
        self.stats.asr = self.asr.name
        self.sample = select(segments, self.percent, config.qa.max_segments)
        self.stats.sample = sum(1 for s in segments if s.hash in self.sample)
        self.todo = sum(1 for h in self.sample if "qa" not in (journal.get(h) or {}))

    def _pool(self) -> Pool:
        if self.pool is None:
            slots = asr_slots(self.asr, self.config, self.todo)
            self.stats.workers = len(slots)
            self.stats.device = slots[0].device if slots else None
            name = getattr(self.asr, "component", self.asr.name)
            payload = self.store.payload(assets_for(name, load_lock()))
            workers = [Worker(self.asr, self.store.root, slot) for slot in slots]
            self.pool = Pool(workers, {"assets": payload})
        _detail(self.progress, "qa", workers=self.stats.workers, device=self.stats.device)
        return self.pool

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close()

    def check(self, segments: list[Segment]) -> None:
        """Check the rendered segments of this batch that are in the sample and unchecked."""
        ctx, audio_dir, journal = self.ctx, self.ctx.audio_dir, self.ctx.journal
        unique: dict[str, Segment] = {}
        for seg in segments:
            h = seg.hash or ""
            entry = journal.get(h)
            if entry is None or h in unique or h not in self.sample:
                continue
            if "qa" not in entry and wav_path(audio_dir, h).is_file():
                unique[h] = seg
        if not unique:
            return
        started = time.monotonic()
        pool = self._pool()
        attempts: dict[str, list[Attempt]] = {}
        failing: list[str] = []

        def first(h: str, a: Attempt) -> None:
            attempts[h] = [a]
            if a.passed():
                _record(ctx, h, attempts[h])
            else:
                with ctx.lock:
                    failing.append(h)

        jobs = [(h, seg, wav_path(audio_dir, h), 1) for h, seg in unique.items()]
        _transcribe(ctx, pool, jobs, first, self.progress, "qa")
        self.stats.checked_now += len(unique)
        self.stats.retried_now += len(failing)
        final: set[str] = set()
        for n in range(2, MAX_ATTEMPTS + 1):
            retry = [h for h in failing if h not in final]
            if not retry:
                break
            replies = self.rerender([unique[h] for h in retry], n)
            jobs = []
            for h in retry:
                reply = replies.get(h)
                if reply is None:  # the backend failed this attempt; keep what we have
                    final.add(h)
                    continue
                if reply.get("final"):
                    final.add(h)
                jobs.append((h, unique[h], wav_path(audio_dir, h, n), n))
            still: list[str] = []

            def again(h: str, a: Attempt, replies: Any = replies, still: list[str] = still) -> None:
                a.duration_ms = int(replies[h]["duration_ms"])
                attempts[h].append(a)
                if a.passed():
                    _record(ctx, h, attempts[h])
                else:
                    with ctx.lock:
                        still.append(h)

            if jobs:
                _transcribe(ctx, pool, jobs, again, self.progress, f"qa retry {n}")
            failing = still
        for h in unique:  # attempts left unrecorded: no reply, or every attempt failed
            entry = journal.get(h) or {}
            if "qa" not in entry and h in attempts:
                _record(ctx, h, attempts[h])
        self.stats.wall_s = round(self.stats.wall_s + time.monotonic() - started, 2)


def summary(
    segments: list[Segment], journal: Journal, stats: QaStats, percent: int
) -> dict[str, Any]:
    """The `qa` section of report.json, from the journal (this run and earlier ones)."""
    out: dict[str, Any] = {
        "mode": stats.mode,
        "asr": stats.asr,
        "device": stats.device,
        "workers": stats.workers,
        "threshold": stats.threshold,
        "cer_threshold": stats.cer_threshold,
        "max_segments": stats.max_segments,
        "sample": stats.sample,
        "checked_now": stats.checked_now,
        "retried_now": stats.retried_now,
        "wall_s_this_run": stats.wall_s,
    }
    if percent == 0:
        return {**out, "checked": 0, "flagged": 0}
    rows: list[dict[str, Any]] = []
    flagged: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    for seg in segments:
        entry = journal.get(seg.hash or "")
        qa = entry.get("qa") if entry else None
        if not qa:
            continue
        if qa.get("unverified"):
            unverified.append({"segment": seg.id, "words": qa["unverified"]})
        rows.append(
            {
                "segment": seg.id,
                "wer": qa["wer"],
                "cer": qa.get("cer"),
                "attempts": entry.get("attempts", 1) if entry else 1,
                "flags": qa["flags"],
            }
        )
        if entry and entry.get("flagged"):
            flagged.append(
                {
                    "segment": seg.id,
                    "text_norm": seg.speech_text,
                    "heard": qa["heard"],
                    "wer": qa["wer"],
                    "cer": qa.get("cer"),
                    "flags": qa["flags"],
                    "attempts": entry.get("attempts", 1),
                }
            )
    checked = len(rows)
    return {
        **out,
        "checked": checked,
        "passed": checked - len(flagged),
        "retried": sum(1 for r in rows if r["attempts"] > 1),
        "flagged": len(flagged),
        "flagged_share": round(len(flagged) / checked, 4) if checked else 0.0,
        "mean_wer": round(sum(r["wer"] for r in rows) / checked, 4) if checked else None,
        "flagged_list": flagged,
        "unverified": unverified,
        "segments": rows,
    }
