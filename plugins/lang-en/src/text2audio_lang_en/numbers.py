"""English number words."""

from __future__ import annotations

import re

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
_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES = ("thousand", "million", "billion", "trillion")
_IRREGULAR = {
    "one": "first",
    "two": "second",
    "three": "third",
    "five": "fifth",
    "eight": "eighth",
    "nine": "ninth",
    "twelve": "twelfth",
}
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_VALID_ROMAN = re.compile(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})")


def _below_thousand(n: int) -> str:
    words = []
    if n >= 100:
        words.append(f"{_ONES[n // 100]} hundred")
        n %= 100
    if n >= 20:
        words.append(_TENS[n // 10] + (f"-{_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        words.append(_ONES[n])
    return " ".join(words)


def cardinal(n: int) -> str:
    if n == 0:
        return "zero"
    groups = []
    scale = -1
    while n:
        n, group = divmod(n, 1000)
        if group:
            name = f" {_SCALES[scale]}" if scale >= 0 else ""
            groups.append(_below_thousand(group) + name)
        scale += 1
    return " ".join(reversed(groups))


def ordinal(n: int) -> str:
    words = cardinal(n)
    last = re.split(r"[ -]", words)[-1]
    head = words[: -len(last)]
    if last in _IRREGULAR:
        return head + _IRREGULAR[last]
    if last.endswith("y"):
        return head + last[:-1] + "ieth"
    return head + last + "th"


def year(n: int) -> str:
    if n < 1000 or n % 1000 == 0 or 2000 < n < 2010:
        return cardinal(n)
    high, low = divmod(n, 100)
    if low == 0:
        return f"{cardinal(high)} hundred"
    if low < 10:
        return f"{cardinal(high)} oh {cardinal(low)}"
    return f"{cardinal(high)} {cardinal(low)}"


def plural(words: str) -> str:
    return words[:-1] + "ies" if words.endswith("y") else words + "s"


def digits(text: str) -> str:
    return " ".join(_ONES[int(c)] for c in text)


def decimal(text: str) -> str:
    whole, _, fraction = text.partition(".")
    head = cardinal(int(whole)) + " " if whole else ""
    return f"{head}point {digits(fraction)}"


def roman_value(text: str) -> int | None:
    if not text or not _VALID_ROMAN.fullmatch(text):
        return None
    total = 0
    for i, ch in enumerate(text):
        value = _ROMAN[ch]
        following = _ROMAN[text[i + 1]] if i + 1 < len(text) else 0
        total += -value if value < following else value
    return total
