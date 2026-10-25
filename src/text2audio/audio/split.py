"""Split plan (SPEC §6.8.1, FR-20): one file per book section, or one file.

Computed at assembly time from the real duration of every segment. Pure: segments in,
parts out, so it is tested table-driven without audio.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Literal

Mode = Literal["auto", "section", "none"]
LOW_CONFIDENCE = 0.5  # TXT/PDF heading detection below this is not trusted


@dataclass(frozen=True)
class Item:
    """One segment in reading order."""

    duration_ms: int  # including the pause after it
    heading_level: int | None = None  # 1 = book title, 2 = `##`, …
    title: str | None = None  # heading text
    paragraph_end: bool = True  # a paragraph boundary follows (safe place to cut)


@dataclass
class Part:
    title: str
    start: int  # index of the first item
    end: int  # one past the last item
    duration_ms: int = 0


@dataclass(frozen=True)
class SplitSettings:
    mode: Mode = "auto"
    min_parts: int = 2
    max_parts: int = 150
    min_part_minutes: float = 3
    max_part_minutes: float = 90
    target_part_minutes: float = 45
    max_merge_ratio: float = 0.5


@dataclass
class SplitPlan:
    single: bool
    parts: list[Part]
    chapters: list[Part]  # chapter marks for a single file (ID3 CHAP / M4B)
    level: int | None = None
    merged: int = 0
    splits: int = 0
    reason: str = ""
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        return {
            "mode": "single" if self.single else "split",
            "files": len(self.parts),
            "level": self.level,
            "merged_sections": self.merged,
            "split_sections": self.splits,
            "reason": self.reason,
            "notes": self.notes,
        }


def _dur(items: list[Item], start: int, end: int) -> int:
    return sum(i.duration_ms for i in items[start:end])


def sections_at(items: list[Item], level: int, book_title: str) -> list[Part]:
    """Sections starting at every heading of `level` or above (a part heading also
    starts one); what comes before the first is titled with the book title."""
    starts = [
        n
        for n, i in enumerate(items)
        if i.heading_level is not None and 2 <= i.heading_level <= level
    ]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    bounds = [*starts, len(items)]
    out = []
    for a, b in pairwise(bounds):
        if a == b:
            continue
        head = items[a]
        title = head.title if head.heading_level and head.heading_level >= 2 else book_title
        out.append(Part(title or book_title, a, b, _dur(items, a, b)))
    return out


def _merge_short(items: list[Item], parts: list[Part], min_ms: float) -> tuple[list[Part], int]:
    """Merge sections shorter than `min_ms` into the following one (the last into the
    previous); the result takes the title of the first section with real content."""
    out: list[Part] = []
    pending: list[Part] = []
    merged = 0
    for part in parts:
        pending.append(part)
        if part.duration_ms >= min_ms or sum(p.duration_ms for p in pending) >= min_ms:
            out.append(_join(items, pending, min_ms))
            merged += len(pending) - 1
            pending = []
    if pending:
        if out:
            merged += len(pending)
            out[-1] = _join(items, [out[-1], *pending], min_ms)
        else:
            out.append(_join(items, pending, min_ms))
            merged += len(pending) - 1
    return out, merged


def _join(items: list[Item], group: list[Part], min_ms: float) -> Part:
    # The title of the first section long enough to stand alone, else the longest.
    titled = next(
        (p for p in group if p.duration_ms >= min_ms), max(group, key=lambda p: p.duration_ms)
    )
    return Part(
        titled.title, group[0].start, group[-1].end, _dur(items, group[0].start, group[-1].end)
    )


def _split_long(
    items: list[Item], part: Part, level: int, s: SplitSettings, part_title: str
) -> list[Part]:
    max_ms = s.max_part_minutes * 60_000
    if part.duration_ms <= max_ms:
        return [part]
    deeper = sorted(
        {
            i.heading_level
            for i in items[part.start + 1 : part.end]
            if i.heading_level is not None and i.heading_level > level
        }
    )
    pieces: list[Part] = []
    if deeper:
        sub = sections_at(items[part.start : part.end], deeper[0], part.title)
        for p in sub:
            p.start += part.start
            p.end += part.start
        sub, _ = _merge_short(items, sub, s.min_part_minutes * 60_000)
        for group in _group(sub, s.target_part_minutes * 60_000):
            joined = group[0] if len(group) == 1 else _span(items, group)
            pieces += _split_long(items, joined, deeper[0], s, part_title)
        return pieces
    # No lower heading: cut at the paragraph boundaries closest to the target length.
    n = max(2, math.ceil(part.duration_ms / (s.target_part_minutes * 60_000)))
    goal = part.duration_ms / n
    cuts, elapsed, k = [], 0, 1
    for idx in range(part.start, part.end - 1):
        elapsed += items[idx].duration_ms
        if k < n and elapsed >= goal * k and items[idx].paragraph_end:
            cuts.append(idx + 1)
            k += 1
    bounds = [part.start, *cuts, part.end]
    total = len(bounds) - 1
    for m, (a, b) in enumerate(pairwise(bounds), 1):
        title = part_title.format(title=part.title, n=m, total=total) if total > 1 else part.title
        pieces.append(Part(title, a, b, _dur(items, a, b)))
    return pieces


def _group(parts: list[Part], target_ms: float) -> list[list[Part]]:
    """Consecutive sections packed into files of about `target_ms` (a long book part
    becomes a few files of several chapters each, not one file per short chapter)."""
    groups: list[list[Part]] = []
    for p in parts:
        if groups and sum(x.duration_ms for x in groups[-1]) + p.duration_ms <= target_ms:
            groups[-1].append(p)
        else:
            groups.append([p])
    return groups


def _span(items: list[Item], group: list[Part]) -> Part:
    title = f"{group[0].title} — {group[-1].title}"
    return Part(title, group[0].start, group[-1].end, _dur(items, group[0].start, group[-1].end))


def _single(items: list[Item], book_title: str, reason: str, level: int | None) -> SplitPlan:
    chapters = sections_at(items, level, book_title) if level else []
    whole = Part(book_title, 0, len(items), _dur(items, 0, len(items)))
    return SplitPlan(True, [whole], chapters if len(chapters) > 1 else [], level, reason=reason)


def plan_split(
    items: list[Item],
    book_title: str,
    settings: SplitSettings | None = None,
    *,
    heading_confidence: float | None = None,
    part_title: str = "{title} (part {n} of {total})",
) -> SplitPlan:
    s = settings or SplitSettings()
    levels = sorted({i.heading_level for i in items if i.heading_level and i.heading_level >= 2})
    if s.mode == "none":
        return _single(items, book_title, "split = none", levels[0] if levels else None)
    if not levels:
        return _single(items, book_title, "no usable headings", None)

    # 1. Pick a level: chapters (`##`) first, deeper if too few, higher if too many.
    level = 2 if 2 in levels else levels[0]
    sections = sections_at(items, level, book_title)
    if len(sections) < s.min_parts:
        for deeper in (x for x in levels if x > level):
            candidate = sections_at(items, deeper, book_title)
            if len(candidate) >= s.min_parts:
                level, sections = deeper, candidate
                break
    elif len(sections) > s.max_parts:
        for higher in (x for x in reversed(levels) if x < level):
            candidate = sections_at(items, higher, book_title)
            if len(candidate) <= s.max_parts:
                level, sections = higher, candidate
                break
    original = len(sections)

    # 2. Merge short sections; 3. split long ones.
    merged_parts, merged = _merge_short(items, sections, s.min_part_minutes * 60_000)
    parts: list[Part] = []
    for p in merged_parts:
        parts += _split_long(items, p, level, s, part_title)
    splits = len(parts) - len(merged_parts)
    plan = SplitPlan(False, parts, [], level, merged, splits)

    # 4. Sanity checks (skipped with split = "section").
    if s.mode == "auto":
        reason = ""
        if heading_confidence is not None and heading_confidence < LOW_CONFIDENCE:
            reason = f"heading confidence {heading_confidence:.2f} is low"
        elif len(parts) < s.min_parts:
            reason = f"{len(parts)} part(s) after merging, fewer than {s.min_parts}"
        elif len(parts) > s.max_parts:
            reason = f"{len(parts)} parts, more than {s.max_parts}"
        elif original and merged / original > s.max_merge_ratio:
            reason = f"{merged} of {original} sections merged: headings look spurious"
        if reason:
            single = _single(items, book_title, reason, level)
            single.merged, single.splits = merged, splits
            return single
    plan.reason = f"{len(parts)} files at heading level {level}"
    return plan
