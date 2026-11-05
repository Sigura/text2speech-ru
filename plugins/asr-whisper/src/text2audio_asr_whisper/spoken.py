"""Whisper writes some words the way books print them; QA compares against what was
said. This turns those written forms into spoken ones: titles ("Mr." → "Mister") and
Roman numerals ("Henry VIII" → "Henry the Eighth", "Chapter IV" → "Chapter Four").

Standard library only: imported by the worker (its own environment) and by tests.
"""

from __future__ import annotations

import re

TITLES = {"Mr.": "Mister", "Mrs.": "Missus", "Ms.": "Miz", "Dr.": "Doctor", "St.": "Saint"}
_TITLE = re.compile("|".join(re.escape(t) for t in TITLES))
_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = {2: "twenty", 3: "thirty", 4: "forty"}
_ORDINAL_ONES = {
    "one": "first",
    "two": "second",
    "three": "third",
    "five": "fifth",
    "eight": "eighth",
    "nine": "ninth",
    "twelve": "twelfth",
}
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50}
_VALID = re.compile(r"(XL|L?X{0,3})(IX|IV|V?I{0,3})")
_NUMBERED = re.compile(
    r"\b((?i:chapter|part|book|volume|act|scene|section|stave|world war))\s+([IVXL]+)\b"
)
_RULER = re.compile(r"\b([A-Z][a-z]+)\s+([IVXL]{2,})\b")


def _value(roman: str) -> int | None:
    if not _VALID.fullmatch(roman):
        return None
    total = 0
    for i, ch in enumerate(roman):
        value = _ROMAN[ch]
        nxt = _ROMAN[roman[i + 1]] if i + 1 < len(roman) else 0
        total += -value if value < nxt else value
    return total or None


def _cardinal(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens, ones = divmod(n, 10)
    return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")


def _ordinal(n: int) -> str:
    words = _cardinal(n)
    head, sep, last = words.rpartition("-")
    if last in _ORDINAL_ONES:
        last = _ORDINAL_ONES[last]
    elif last.endswith("y"):
        last = last[:-1] + "ieth"
    else:
        last += "th"
    return head + sep + last


def _numbered(m: re.Match[str]) -> str:
    value = _value(m[2])
    return f"{m[1]} {_cardinal(value).title()}" if value else m[0]


def _ruler(m: re.Match[str]) -> str:
    value = _value(m[2])
    return f"{m[1]} the {_ordinal(value).title()}" if value else m[0]


def spoken(text: str) -> str:
    text = _TITLE.sub(lambda m: TITLES[m[0]], text)
    text = _NUMBERED.sub(_numbered, text)
    return _RULER.sub(_ruler, text)
