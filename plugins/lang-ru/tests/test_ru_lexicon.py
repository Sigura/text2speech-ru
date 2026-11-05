"""The pack lexicon (FR-13): stress the G2P model gets wrong, in exact word forms."""

from __future__ import annotations

from pathlib import Path

import pytest
import text2audio_lang_ru
from text2audio_lang_ru.normalize import RussianNormalizer

from text2audio.text.lexicon import Lexicon

LEXICON = Lexicon.layered(Path(text2audio_lang_ru.__file__).parent / "lexicon.tsv")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Пугачев", "Пугач+ёв"),
        ("Пугачёва", "Пугач+ёва"),
        ("с Пугачевым", "с Пугач+ёвым"),
        ("о Пугачеве", "о Пугач+ёве"),
        ("Пугачевские", "Пугач+ёвские"),
        ("пугачевской", "пугач+ёвской"),
        ("Пугачевщина", "Пугач+ёвщина"),
        ("Гринев", "Грин+ёв"),
        ("Гриневу", "Грин+ёву"),
        ("Нижнеозерная", "Нижнеоз+ёрная"),
        ("в Нижнеозёрной", "в Нижнеоз+ёрной"),
        ("Зурин", "З+урин"),
        ("Зуриным", "З+уриным"),
        ("небо", "н+ебо"),
        ("неба", "н+еба"),
        ("уже", "уж+е"),
        ("кругом", "круг+ом"),
    ],
)
def test_stress(source: str, expected: str) -> None:
    assert LEXICON.apply(source) == expected


def test_sentence_start_keeps_its_capital() -> None:
    assert LEXICON.apply("Уже темно. Кругом снег.") == "Уж+е темно. Круг+ом снег."


def test_real_palate_spelled_with_yo_is_left_alone() -> None:
    assert LEXICON.apply("нёбо") == "нёбо"


def test_abbreviation_dot_survives_a_lexicon_word_after_it() -> None:
    text = LEXICON.apply("Книги и т. д. Уже темно.")
    assert RussianNormalizer().normalize(text, "narration").text == (
        "Книги и так далее. Уж+е темно."
    )
