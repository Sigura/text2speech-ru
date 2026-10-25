"""FR-24: Latin-script words in Russian text get a Cyrillic reading."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from text2audio_lang_ru.latin import english_ipa, ipa_to_cyrillic, transliterate
from text2audio_lang_ru.normalize import RussianNormalizer

CASES = [
    line.split("\t")
    for line in (Path(__file__).parent / "transliteration.tsv").read_text("utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]
needs_espeak = pytest.mark.skipif(shutil.which("espeak-ng") is None, reason="needs espeak-ng")


@pytest.mark.parametrize(("spelling", "ipa", "expected"), CASES, ids=[c[0] for c in CASES])
def test_ipa_to_cyrillic(spelling: str, ipa: str, expected: str) -> None:
    assert ipa_to_cyrillic(ipa, spelling) == expected


@needs_espeak
@pytest.mark.parametrize(("spelling", "ipa", "expected"), CASES[:5], ids=[c[0] for c in CASES[:5]])
def test_recorded_ipa_matches_espeak(spelling: str, ipa: str, expected: str) -> None:
    assert english_ipa(spelling) == ipa
    assert transliterate(spelling) == expected


def test_normalizer_reports_what_it_resolved() -> None:
    result = RussianNormalizer().normalize("Это сoбака, а протокол HTTP.")
    assert [(s.text, s.resolution, s.reading) for s in result.resolved] == [
        ("сoбака", "homoglyph", "собака"),
        ("HTTP", "acronym", "эйчтитип+и"),
    ]


def test_only_latin_is_transliterated() -> None:
    assert RussianNormalizer().transliterate("λόγος", "Grek") is None
