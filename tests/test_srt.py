"""Subtitle cues (§6.8): timing, splitting of long segments, SubRip format."""

from __future__ import annotations

import itertools
from pathlib import Path

from text2audio.audio.srt import Cue, split_cue, write_srt


def test_short_segment_is_one_cue() -> None:
    assert split_cue(300, 2300, "Глава I.") == [Cue(300, 2300, "Глава I.")]


def test_long_segment_split_in_two_line_cues() -> None:
    text = " ".join(["слово"] * 60)
    cues = split_cue(1000, 31000, text)
    assert len(cues) > 1
    assert cues[0].start_ms == 1000 and cues[-1].end_ms == 31000
    assert all(a.end_ms == b.start_ms for a, b in itertools.pairwise(cues))
    for c in cues:
        lines = c.text.split("\n")
        assert len(lines) <= 2 and all(len(line) <= 42 for line in lines)
    assert " ".join(c.text.replace("\n", " ") for c in cues) == text


def test_empty_or_zero_length() -> None:
    assert split_cue(0, 1000, "  ") == []
    assert split_cue(500, 500, "текст") == []


def test_write_srt(tmp_path: Path) -> None:
    path = tmp_path / "01 - Глава.srt"
    write_srt(path, [Cue(300, 2500, "Раз"), Cue(3_725_004, 3_726_000, "Два\nтри")])
    assert path.read_text("utf-8") == (
        "1\n00:00:00,300 --> 00:00:02,500\nРаз\n\n2\n01:02:05,004 --> 01:02:06,000\nДва\nтри\n"
    )
