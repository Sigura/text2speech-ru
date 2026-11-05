"""Lexicon levels (FR-13) and stress marks (§6.5)."""

from __future__ import annotations

from pathlib import Path

from text2audio.text.lexicon import Lexicon
from text2audio.text.stress import acute_to_plus, merge_marks, plus_to_acute, strip_marks


def test_later_levels_win(tmp_path: Path) -> None:
    user = tmp_path / "user.tsv"
    book = tmp_path / "book.tsv"
    user.write_text("Docker\tд+окер\n# comment\nСША\tэсшэа\n", "utf-8")
    book.write_text("США\tсэшэ+а\n", "utf-8")
    lex = Lexicon.layered("Python\tп+айтон\n", user, book, tmp_path / "missing.tsv")
    assert lex.apply("США, docker и Python") == "сэшэ+а, д+окер и п+айтон"


def test_whole_words_only() -> None:
    lex = Lexicon({"кот": "к+от"})
    assert lex.apply("кот котёнок скот") == "к+от котёнок скот"


def test_capital_is_kept_for_a_same_script_lowercase_reading() -> None:
    lex = Lexicon({"уже": "уж+е", "сша": "сэшэ+а", "python": "п+айтон", "зурин": "З+урин"})
    assert lex.apply("Уже уже") == "Уж+е уж+е"
    assert lex.apply("США и Python") == "сэшэ+а и п+айтон"
    assert lex.apply("Зурин") == "З+урин"


def test_word_the_author_stressed_is_not_replaced() -> None:
    lex = Lexicon({"уже": "уж+е"})
    assert lex.apply("уже\u0301 и у\u0301же") == "уже\u0301 и у\u0301же"


def test_marks_conversions() -> None:
    assert acute_to_plus("замо́к") == "зам+ок"
    assert plus_to_acute("зам+ок") == "замо́к"
    assert strip_marks("зам+ок замо́к") == "замок замок"


def test_user_marks_win_over_model() -> None:
    marked = "Ржавый зам+ок висел на двери."
    model = "Рж+авый з+амок вис+ел на двер+и."
    assert merge_marks(marked, model) == "Рж+авый зам+ок вис+ел на двер+и."


def test_merge_keeps_model_text_when_words_differ() -> None:
    assert merge_marks("один зам+ок", "совсем другой текст") == "совсем другой текст"
