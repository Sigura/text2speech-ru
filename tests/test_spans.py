"""Foreign-script spans (FR-24): tagging, replacement, lexicon endings."""

from __future__ import annotations

from text2audio.text.lexicon import Lexicon
from text2audio.text.spans import foreign_spans, replace_spans


def texts(text: str) -> list[tuple[str, str]]:
    return [(r.text, r.script) for r in foreign_spans(text, "Cyrl")]


def test_words_and_phrases_in_another_script() -> None:
    assert texts("Я установил Docker и зашёл в Google Docs.") == [
        ("Docker", "Latn"),
        ("Google Docs", "Latn"),
    ]
    assert texts("Он сказал: «It's not my problem», — и вышел.") == [
        ("It's not my problem", "Latn")
    ]


def test_native_endings_and_compounds_stay_outside() -> None:
    assert texts("Купил iPhone'а и Docker-контейнер.") == [("iPhone", "Latn"), ("Docker", "Latn")]


def test_look_alike_letters_tag_the_whole_word() -> None:
    assert texts("Это сoбака.") == [("сoбака", "Latn")]


def test_native_text_has_no_spans() -> None:
    assert texts("Ёлка стояла в углу, 1812 год.") == []


def test_replace_joins_endings_without_apostrophe() -> None:
    text = "Купил iPhone'а и Docker-контейнер."
    out = replace_spans(text, foreign_spans(text, "Cyrl"), lambda r: r.text.upper())
    assert out == "Купил IPHONEа и DOCKER-контейнер."


def test_lexicon_joins_native_ending() -> None:
    lex = Lexicon({"iphone": "айф+он", "docker": "д+окер"})
    assert (
        lex.apply("Нет iPhone'а, есть Docker-контейнер.") == "Нет айф+она, есть д+окер-контейнер."
    )
    assert lex.apply("Docker's image") == "д+окер's image"
