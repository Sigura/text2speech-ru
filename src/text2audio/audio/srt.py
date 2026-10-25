"""SubRip subtitles next to each MP3: the book text, timed per segment (SPEC §6.8).

Timings come from the part assembly itself (lead-in, trimmed speech, generated pauses),
so a cue starts when its segment starts in the MP3. The text is the book's own text,
not the normalized reading. A long segment is cut at word boundaries into cues of at
most two lines, each cue timed in proportion to its share of the characters.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

LINE_CHARS = 42  # common subtitle line length
MAX_LINES = 2


@dataclass(frozen=True)
class Cue:
    start_ms: int
    end_ms: int
    text: str


def _stamp(ms: int) -> str:
    h, rest = divmod(max(0, ms), 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def split_cue(start_ms: int, end_ms: int, text: str) -> list[Cue]:
    """One segment → cues of at most `MAX_LINES` lines of `LINE_CHARS`."""
    lines = textwrap.wrap(" ".join(text.split()), LINE_CHARS, break_long_words=False)
    if not lines or end_ms <= start_ms:
        return []
    chunks = ["\n".join(lines[i : i + MAX_LINES]) for i in range(0, len(lines), MAX_LINES)]
    total = sum(len(c) for c in chunks)
    cues, t, used = [], start_ms, 0
    for chunk in chunks:
        used += len(chunk)
        end = start_ms + (end_ms - start_ms) * used // total
        cues.append(Cue(t, end, chunk))
        t = end
    return cues


def write_srt(path: Path, cues: list[Cue]) -> None:
    blocks = [
        f"{n}\n{_stamp(c.start_ms)} --> {_stamp(c.end_ms)}\n{c.text}\n"
        for n, c in enumerate(cues, 1)
    ]
    tmp = path.with_name(f".{path.name}.partial")
    tmp.write_text("\n".join(blocks), encoding="utf-8")
    tmp.replace(path)
