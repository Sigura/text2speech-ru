"""Numbers as Russian words, with case, gender and number (§6.6.5).

A thin layer over num2words: it uses pymorphy3 case names, reads "тысяча", not
"одна тысяча", and treats the accusative as inanimate unless told otherwise.
"""

from __future__ import annotations

import re
from decimal import Decimal

from num2words import num2words

_N2W_CASE = {"nomn": "n", "gent": "g", "datv": "d", "accs": "a", "ablt": "i", "loct": "p"}
_ONE_THOUSAND = re.compile(r"^(?:одна|одной|одну|одною)\s+(тысяч[а-я]*)")
_ONE_MILLION = re.compile(r"^(?:один|одного|одному|одним|одном)\s+(миллион[а-я]*|миллиард[а-я]*)")

ROMAN = re.compile(r"^M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})$")
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
# Cyrillic look-alikes typed into Roman numerals ("ХIХ век" with a Cyrillic Х).
_ROMAN_HOMOGLYPHS = str.maketrans({"Х": "X", "І": "I", "С": "C", "М": "M", "Ӏ": "I"})


def _tidy(words: str) -> str:
    words = _ONE_THOUSAND.sub(r"\1", words)
    return _ONE_MILLION.sub(r"\1", words)


def cardinal(
    n: int, case: str = "nomn", gender: str = "m", animate: bool = False, plural: bool = False
) -> str:
    return _tidy(
        num2words(n, lang="ru", case=_N2W_CASE[case], gender=gender, animate=animate, plural=plural)
    )


def ordinal(
    n: int, case: str = "nomn", gender: str = "m", plural: bool = False, animate: bool = False
) -> str:
    # Only the last word declines ("в тысяча восемьсот двенадцатом году").
    return str(
        num2words(
            n,
            lang="ru",
            to="ordinal",
            case=_N2W_CASE[case],
            gender=gender,
            plural=plural,
            animate=animate,
        )
    )


def decimal(text: str) -> str:
    """'3,5' → 'три целых пять десятых'."""
    return str(num2words(Decimal(text.replace(",", ".")), lang="ru"))


def roman_value(word: str) -> int | None:
    """Value of a Roman numeral (Cyrillic look-alikes allowed), else None."""
    latin = word.translate(_ROMAN_HOMOGLYPHS)
    if not latin or not ROMAN.match(latin):
        return None
    total, prev = 0, 0
    for ch in reversed(latin):
        value = _ROMAN_VALUES[ch]
        total += -value if value < prev else value
        prev = max(prev, value)
    return total


def digits(text: str) -> str:
    """Digit by digit, for codes and leading zeros ('05' → 'ноль пять')."""
    return " ".join(cardinal(int(d)) for d in text if d.isdigit())
