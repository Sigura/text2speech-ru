"""Quoted dialogue and attribution with the `en` profile (§6.6.5, §6.7 E1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from text2audio_lang_en import PROFILE

from text2audio.text.dialogue import split_quoted
from text2audio.text.segment import segment_markdown

ROLES = {"D": "dialogue", "R": "remark", "N": "narration"}
CASES = [
    line.split("\t")
    for line in (Path(__file__).parent / "dialogue.tsv").read_text("utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]


@pytest.mark.parametrize(("paragraph", "expected"), CASES, ids=[c[0][:30] for c in CASES])
def test_quote_split(paragraph: str, expected: str) -> None:
    parts = [(ROLES[p[0]], p[2:]) for p in expected.split(" | ")]
    assert split_quoted(paragraph) == parts


def test_attribution_is_read_by_the_narrator() -> None:
    md = "“Stay,” said Alice, “and tell me.”\n\nShe left.\n"
    segments = segment_markdown(md, "en", PROFILE)
    got = [(s.role, s.voice, s.pause, s.text) for s in segments]
    assert got == [
        ("dialogue", "dialogue", "remark", "“Stay,”"),
        ("remark", "narrator", "remark", "said Alice,"),
        ("dialogue", "dialogue", "paragraph", "“and tell me.”"),
        ("narration", "narrator", "paragraph", "She left."),
    ]


def test_e0_reads_everything_in_the_narrator_voice() -> None:
    md = "“Stay,” said Alice.\n"
    segments = segment_markdown(md, "en", PROFILE, expression="E0")
    assert [(s.role, s.voice) for s in segments] == [("narration", "narrator")]
