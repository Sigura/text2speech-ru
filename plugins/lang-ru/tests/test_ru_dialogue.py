"""Dialogue dashes and author's remarks with the `ru` profile (§6.6.5, §6.7 E1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from text2audio_lang_ru import PROFILE

from text2audio.text.dialogue import split_dialogue
from text2audio.text.segment import segment_markdown

ROLES = {"D": "dialogue", "R": "remark", "N": "narration"}
CASES = [
    line.split("\t")
    for line in (Path(__file__).parent / "dialogue.tsv").read_text("utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]


@pytest.mark.parametrize(("paragraph", "expected"), CASES, ids=[c[0][:30] for c in CASES])
def test_dialogue_split(paragraph: str, expected: str) -> None:
    parts = [(ROLES[p[0]], p[2:]) for p in expected.split(" | ")]
    assert split_dialogue(paragraph, PROFILE.remark_words) == parts


def test_remarks_are_read_by_the_narrator() -> None:
    md = "— Добро, — сказала комендантша, — так и быть.\n\nОна ушла.\n"
    segments = segment_markdown(md, "ru", PROFILE)
    got = [(s.role, s.voice, s.pause, s.text) for s in segments]
    assert got == [
        ("dialogue", "dialogue", "remark", "— Добро,"),
        ("remark", "narrator", "remark", "— сказала комендантша,"),
        ("dialogue", "dialogue", "paragraph", "— так и быть."),
        ("narration", "narrator", "paragraph", "Она ушла."),
    ]
    assert segments[0].pause_after_ms == 200
