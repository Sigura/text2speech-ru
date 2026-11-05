"""Text structure of the `en` profile: headings and sentence ends (§6.5, §6.6.5)."""

from __future__ import annotations

import re

import pytest
from text2audio_lang_en import PROFILE

from text2audio.text.sentences import split_sentences


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("Chapter I", "chapter"),
        ("CHAPTER 12", "chapter"),
        ("Chapter One", "chapter"),
        ("Chapter Twenty-Three", "chapter"),
        ("Chapter I. Down the Rabbit-Hole", "chapter"),
        ("Chapter 3 — The Pool of Tears", "chapter"),
        ("Ch. 4: The Storm", "chapter"),
        ("Prologue", "chapter"),
        ("EPILOGUE", "chapter"),
        ("Preface", "chapter"),
        ("Part One", "part"),
        ("PART II", "part"),
        ("Book the First", "part"),
        ("Volume 3", "part"),
        ("Contents", "front"),
        ("Table of Contents", "front"),
        ("Chapters of my life were long", None),
        ("Part of the plan", None),
        ("Book of Job", None),
        ("Partly cloudy", None),
    ],
)
def test_heading_patterns(line: str, kind: str | None) -> None:
    groups = {
        "part": PROFILE.part_headings,
        "chapter": PROFILE.chapter_headings,
        "front": PROFILE.front_matter,
    }
    found = [k for k, pats in groups.items() if any(re.fullmatch(p, line, re.I) for p in pats)]
    assert found == ([kind] if kind else [])


@pytest.mark.parametrize(
    "text",
    [
        "Mr. Darcy bowed to Mrs. Bennet and Dr. Watson.",
        "She lives on St. James Street near Mt. Vernon.",
        "Bring fruit, e.g. Apples, or i.e. Pears, vs. Plums.",
        "See No. 5 on p. 12 of Vol. 2 by Prof. Lee.",
        "J. R. R. Tolkien wrote it.",
    ],
)
def test_abbreviations_do_not_end_a_sentence(text: str) -> None:
    assert split_sentences(text, PROFILE.abbreviations) == [text]


def test_ordinary_sentences_are_split() -> None:
    text = "It rained. “Who is there?” she asked. Nobody answered!"
    assert split_sentences(text, PROFILE.abbreviations) == [
        "It rained.",
        "“Who is there?” she asked.",
        "Nobody answered!",
    ]
