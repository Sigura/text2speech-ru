"""English text normalization (§6.5, §6.6.5, FR-11): numbers, dates, abbreviations and
symbols written out as they are read aloud.

Pure-Python rules, not NeMo TN: normalizers run in the core environment (§13.1). Foreign
scripts stay for the core's foreign-span stage. Anything still holding a digit afterwards
("MP3", "1.2.3") is listed as uncertain in report.json.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from text2audio.core.contracts import NormResult
from text2audio_lang_en.numbers import cardinal, decimal, digits, ordinal, plural, roman_value, year

Rule = tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]

_NUM = r"\d{1,3}(?:,\d{3})+|\d+"
_READABLE = rf"(?:{_NUM})(?:\.\d+)?"
_SUFFIX = r"(?:st|nd|rd|th)"

_MONTHS = {
    "Jan.": "January",
    "Feb.": "February",
    "Mar.": "March",
    "Apr.": "April",
    "Jun.": "June",
    "Jul.": "July",
    "Aug.": "August",
    "Sep.": "September",
    "Sept.": "September",
    "Oct.": "October",
    "Nov.": "November",
    "Dec.": "December",
}
_MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]
_MONTH = "(" + "|".join([*_MONTH_NAMES, *(re.escape(k) for k in _MONTHS)]) + ")"

_TITLES = {
    "Prof": "Professor",
    "Rev": "Reverend",
    "Hon": "Honorable",
    "Gen": "General",
    "Col": "Colonel",
    "Capt": "Captain",
    "Lt": "Lieutenant",
    "Sgt": "Sergeant",
    "Gov": "Governor",
    "Sen": "Senator",
    "Mt": "Mount",
}
_NO_DOT_TITLES = {"Mr": "Mister", "Mrs": "Missus", "Ms": "Miz", "Dr": "Doctor"}
# After a capitalized name; the dot stays only when it also ended the sentence.
_AFTER_NAME = {
    "Jr": "Junior",
    "Sr": "Senior",
    "Ave": "Avenue",
    "Rd": "Road",
    "Blvd": "Boulevard",
    "Ltd": "Limited",
    "Inc": "Incorporated",
    "Co": "Company",
}
_BEFORE_NUMBER = {
    r"[Nn]o": "number",
    r"pp": "pages",
    r"p": "page",
    r"[Vv]ol": "volume",
    r"[Ff]ig": "figure",
}
_UNITS = {
    "km": ("kilometer", "kilometers"),
    "kg": ("kilogram", "kilograms"),
    "cm": ("centimeter", "centimeters"),
    "mm": ("millimeter", "millimeters"),
    "mg": ("milligram", "milligrams"),
    "ml": ("milliliter", "milliliters"),
    "mph": ("mile per hour", "miles per hour"),
    "ft": ("foot", "feet"),
    "lb": ("pound", "pounds"),
    "lbs": ("pound", "pounds"),
    "oz": ("ounce", "ounces"),
}
_MONEY = {
    "$": ("dollar", "dollars", "cent", "cents"),
    "£": ("pound", "pounds", "penny", "pence"),
    "€": ("euro", "euros", "cent", "cents"),
}
_ERAS = {
    "BCE": "B C E",
    "B.C.E.": "B C E",
    "BC": "B C",
    "B.C.": "B C",
    "AD": "A D",
    "A.D.": "A D",
    "CE": "C E",
    "C.E.": "C E",
}
_ERA = r"(B\.C\.E\.|BCE|B\.C\.|BC|A\.D\.|AD|C\.E\.|CE)(?!\w)"

_NUMBERED = r"chapter|part|book|volume|act|scene|section|canto|stave|appendix"
# A single I, V or X is a numeral only after a ruler's name ("Charles I", not "Malcolm X").
_RULERS = frozenset(
    [
        "Henry",
        "Charles",
        "James",
        "Edward",
        "George",
        "William",
        "Elizabeth",
        "Mary",
        "Richard",
        "Louis",
        "John",
        "Peter",
        "Catherine",
        "Nicholas",
        "Alexander",
        "Paul",
        "Pius",
        "Philip",
        "Francis",
        "Frederick",
        "Napoleon",
        "Victoria",
    ]
)
# "To John I said nothing": the pronoun, whatever the name before it.
_PRONOUN_VERB = re.compile(
    r"\s+(?:am|have|had|think|know|said|say|will|would|could|can|do|did|saw|see|love|want)\b"
)
_YEAR_PREPOSITION = re.compile(
    r"(?:^|\W)(?:in|by|since|from|to|during|until|till|after|before|around|about|of|year|"
    r"summer|winter|spring|autumn|fall)\s+$",
    re.I,
)
_YEAR_VERB = re.compile(r"\s+(?:was|were|is|saw|had|brought|began|marked)\b")
_COUNTED = re.compile(r"\s+(?:years|men|people|miles|pounds|dollars|times)\b")


def _closing(m: re.Match[str]) -> str:
    """A dot that belonged to an abbreviation also ends the sentence at the end of the
    text or before a capital."""
    rest = m.string[m.end() :]
    return "." if not rest.strip() or re.match(r"\s+[A-Z]", rest) else ""


def _number(text: str) -> str:
    text = text.replace(",", "")
    if "." in text:
        return decimal(text)
    return digits(text) if len(text) > 1 and text[0] == "0" else cardinal(int(text))


def _is_one(text: str) -> bool:
    return text.replace(",", "") == "1"


def _keyword_roman(m: re.Match[str]) -> str:
    value = roman_value(m[2])
    return f"{m[1]} {cardinal(value).title()}" if value else m[0]


def _ruler(m: re.Match[str]) -> str:
    value = roman_value(m[2])
    if not value:
        return m[0]
    if len(m[2]) == 1 and (m[1] not in _RULERS or _PRONOUN_VERB.match(m.string[m.end() :])):
        return m[0]
    return f"{m[1]} the {ordinal(value).title()}"


def _saint_or_street(m: re.Match[str]) -> str:
    before = m.string[: m.start()].rstrip()
    rest = m.string[m.end() :]
    words = before.split()
    after_name = len(words) > 1 and words[-1][0].isupper() and words[-2][-1] not in ".!?"
    if re.match(r"\s+[A-Z]", rest) and not after_name:
        return "Saint"
    return "Street" + _closing(m)


def _money(m: re.Match[str]) -> str:
    one, many, small_one, small_many = _MONEY[m[1]]
    amount, scale = m[2], m[3]
    if scale:
        return f"{_number(amount)} {scale} {many}"
    whole, _, fraction = amount.partition(".")
    units = int(whole.replace(",", ""))
    small = int(fraction.ljust(2, "0")[:2]) if fraction else 0
    parts = []
    if units or not small:
        parts.append(f"{_number(whole)} {one if units == 1 else many}")
    if small:
        parts.append(f"{cardinal(small)} {small_one if small == 1 else small_many}")
    return " and ".join(parts)


def _month(name: str) -> str:
    return _MONTHS.get(name, name)


def _with_year(value: str | None) -> str:
    return f", {year(int(value))}" if value else ""


def _time(hour: str, minute: str | None, meridiem: str | None) -> str | None:
    h, mins = int(hour), int(minute or 0)
    if h > 24 or mins > 59:
        return None
    text = cardinal(h)
    if mins:
        text += f" oh {cardinal(mins)}" if mins < 10 else f" {cardinal(mins)}"
    elif not meridiem:
        text += " o'clock" if h <= 12 else " hundred"
    if meridiem:
        text += " A M" if meridiem[0].lower() == "a" else " P M"
    return text


def _meridiem_time(m: re.Match[str]) -> str:
    text = _time(m[1], m[2], m[3])
    if text is None:
        return m[0]
    return text + (_closing(m) if m[3].endswith(".") else "")


def _clock(m: re.Match[str]) -> str:
    return _time(m[1], m[2], None) or m[0]


def _era_after(m: re.Match[str]) -> str:
    return f"{year(int(m[1]))} {_ERAS[m[2]]}" + (_closing(m) if m[2].endswith(".") else "")


def _decade(m: re.Match[str]) -> str:
    return plural(year(int(m[1])))


def _short_decade(m: re.Match[str]) -> str:
    return plural(cardinal(int(m[1]))) if m[1].endswith("0") and m[1] != "10" else m[0]


def _range(m: re.Match[str]) -> str:
    low, high = m[1], m[2]
    if len(low) == 4 and len(high) == 4:
        return f"{year(int(low))} to {year(int(high))}"
    if len(low) == 4 and len(high) == 2:
        return f"{year(int(low))} to {cardinal(int(high))}"
    if len(high) >= 4 or int(low) >= int(high):
        return m[0]
    return f"{cardinal(int(low))} to {cardinal(int(high))}"


def _unit(m: re.Match[str]) -> str:
    one, many = _UNITS[m[2]]
    return f"{_number(m[1])} {one if _is_one(m[1]) else many}"


def _degrees(m: re.Match[str]) -> str:
    scale = {"C": " Celsius", "F": " Fahrenheit"}.get(m[2] or "", "")
    return f"{_number(m[1])} {'degree' if _is_one(m[1]) else 'degrees'}{scale}"


def _fraction(numerator: int, denominator: int) -> str | None:
    if not 2 <= denominator <= 12:
        return None
    if denominator == 2:
        name, names = "half", "halves"
    elif denominator == 4:
        name, names = "quarter", "quarters"
    else:
        name = ordinal(denominator)
        names = name + "s"
    return f"{cardinal(numerator)} {name if numerator == 1 else names}"


def _simple_fraction(m: re.Match[str]) -> str:
    return _fraction(int(m[1]), int(m[2])) or m[0]


def _mixed_fraction(m: re.Match[str]) -> str:
    part = _fraction(int(m[2]), int(m[3]))
    return f"{cardinal(int(m[1]))} and {part}" if part else m[0]


def _ordinal(m: re.Match[str]) -> str:
    return ordinal(int(m[1].replace(",", "")))


def _integer(m: re.Match[str]) -> str:
    text = m[0]
    if len(text) == 4 and text[0] != "0" and 1000 <= int(text) < 2100:
        rest = m.string[m.end() :]
        counted = _COUNTED.match(rest) is not None
        before = m.string[: m.start()]
        followed_by_word = re.match(r"\s+[a-z]", rest) is not None
        as_year = _YEAR_PREPOSITION.search(before) or _YEAR_VERB.match(rest) or not followed_by_word
        if as_year and not counted:
            return year(int(text))
    return _number(text)


_KEYWORD_ROMAN = re.compile(rf"\b((?i:{_NUMBERED}))\s+([IVXL]+)\b")
_WORLD_WAR = re.compile(r"\b(World War)\s+([IV]+)\b")
_RULER = re.compile(r"\b([A-Z][a-z]+)\s+([IVXL]+)\b")
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")

_TEXT_RULES: list[Rule] = [
    *((re.compile(rf"\b{k}\.?(?=\s+[A-Z])"), v) for k, v in _NO_DOT_TITLES.items()),
    *((re.compile(rf"\b{k}\.(?=\s+[A-Z])"), v) for k, v in _TITLES.items()),
    (re.compile(r"\bSt\."), _saint_or_street),
    *(
        (
            re.compile(rf"(?<=[A-Za-z] ){k}\."),
            lambda m, name=v: name + _closing(m),
        )
        for k, v in _AFTER_NAME.items()
    ),
    (re.compile(r"\betc\."), lambda m: "et cetera" + _closing(m)),
    (re.compile(r"\b([Ee])\.g\."), lambda m: "for example" if m[1] == "e" else "For example"),
    (re.compile(r"\b([Ii])\.e\."), lambda m: "that is" if m[1] == "i" else "That is"),
    (re.compile(r"\bvs\.?(?=\s)"), "versus"),
    (re.compile(r"\bapprox\."), "approximately"),
    *((re.compile(rf"\b{k}\.(?=\s*\d)"), v) for k, v in _BEFORE_NUMBER.items()),
    (re.compile(r"#(?=\d)"), "number "),
    (re.compile(r"§\s*(?=\d)"), "section "),
    (re.compile(r"(?<=\s)&(?=\s)"), "and"),
    (re.compile(r"(?<=\s)@(?=\s)"), "at"),
    (re.compile(r"(?<=\s)\+(?=\s)"), "plus"),
    (re.compile(r"(?<=\s)=(?=\s)"), "equals"),
]

_NUMBER_RULES: list[Rule] = [
    (
        re.compile(rf"([$£€])\s?({_READABLE})(?:\s+(thousand|million|billion|trillion))?(?![\d,])"),
        _money,
    ),
    (
        re.compile(r"(?<![\w.,])(\d+)¢"),
        lambda m: f"{cardinal(int(m[1]))} {'cent' if m[1] == '1' else 'cents'}",
    ),
    (
        re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
        lambda m: (
            f"{_MONTH_NAMES[int(m[2]) - 1]} {ordinal(int(m[3]))}{_with_year(m[1])}"
            if 1 <= int(m[2]) <= 12
            else m[0]
        ),
    ),
    (
        re.compile(rf"\b(?:the\s+)?(\d{{1,2}}){_SUFFIX}\s+of\s+{_MONTH}"),
        lambda m: f"the {ordinal(int(m[1]))} of {_month(m[2])}",
    ),
    (
        re.compile(rf"(?<![\d:.,])(\d{{1,2}}){_SUFFIX}?\s+{_MONTH}(?:,?\s+(\d{{4}}))?(?!\d)"),
        lambda m: f"the {ordinal(int(m[1]))} of {_month(m[2])}{_with_year(m[3])}",
    ),
    (
        re.compile(rf"\b{_MONTH}\s+(\d{{4}})\b"),
        lambda m: f"{_month(m[1])} {year(int(m[2]))}",
    ),
    (
        re.compile(rf"\b{_MONTH}\s+(\d{{1,2}}){_SUFFIX}?(?:,?\s+(\d{{4}}))?(?!\d)"),
        lambda m: f"{_month(m[1])} {ordinal(int(m[2]))}{_with_year(m[3])}",
    ),
    (
        re.compile(r"(?<![\d:.,])(\d{1,2})(?::(\d{2}))?\s*(a\.m\.|p\.m\.|am|pm|AM|PM)(?![A-Za-z])"),
        _meridiem_time,
    ),
    (re.compile(r"(?<![\d:.,])(\d{1,2}):(\d{2})(?![\d:])"), _clock),
    (re.compile(r"\b(A\.D\.|AD)\s+(\d{1,4})\b"), lambda m: f"A D {year(int(m[2]))}"),
    (re.compile(rf"(?<![\w.,])(\d{{1,4}})\s*{_ERA}"), _era_after),
    (re.compile(r"\b(\d{4})['’]?s\b"), _decade),
    (re.compile(r"(?<!\w)['’]?(\d{2})s\b"), _short_decade),
    (re.compile(r"(?<![\w.,])(\d{1,4})\s?[-–—]\s?(\d{1,4})(?![\w.,]*\d)"), _range),
    (
        re.compile(
            rf"(?<![\w.,])({_READABLE})\s?({'|'.join(sorted(_UNITS, key=len, reverse=True))})\b"
        ),
        _unit,
    ),
    (re.compile(rf"(?<![\w.,])({_READABLE})\s?%"), lambda m: f"{_number(m[1])} percent"),
    (re.compile(rf"(?<![\w.,])({_READABLE})\s?°(?:\s?([CF]\b))?"), _degrees),
    (re.compile(rf"(?<![\w.,])({_NUM}){_SUFFIX}\b"), _ordinal),
    (re.compile(r"(?<![\w/.,])(\d+)\s(\d+)/(\d+)(?![\w/])"), _mixed_fraction),
    (re.compile(r"(?<![\w/.,])(\d+)/(\d+)(?![\w/])"), _simple_fraction),
    (re.compile(r"(?<![\w.,])\d+\.\d+(?![\w]|\.\d)"), lambda m: decimal(m[0])),
    (re.compile(r"(?<![\w.,])\.\d+\b"), lambda m: decimal(m[0])),
    (re.compile(r"(?<![\w.,])[-−](?=\d)"), "minus "),
    (re.compile(rf"(?<![\w.,])(?:{_NUM})(?!\w|[.,]\d)"), _integer),
]


def _apply(rules: list[Rule], text: str) -> str:
    for pattern, repl in rules:
        text = pattern.sub(repl, text)
    return text


def _is_shouted(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) > 1 and all(c.isupper() for c in letters)


class Normalizer:
    """Entry point `text2audio.normalizers: en`."""

    def normalize(self, text: str, role: str = "narration") -> NormResult:
        shouted = role in ("heading", "title") and _is_shouted(text)
        text = _KEYWORD_ROMAN.sub(_keyword_roman, text)
        text = _WORLD_WAR.sub(_keyword_roman, text)
        if shouted:
            text = _WORD.sub(lambda m: m[0].capitalize(), text)
        text = _RULER.sub(_ruler, text)
        text = _apply(_TEXT_RULES, text)
        text = _apply(_NUMBER_RULES, text)
        uncertain = re.findall(r"[^\s()\[\]]*\d[^\s()\[\]]*", text)
        return NormResult(text=text, uncertain=[u.rstrip(".,;:!?") for u in uncertain], resolved=[])

    def transliterate(self, span: str, script: str) -> str | None:
        """Foreign-script spans are flagged in report.json for now (FR-24)."""
        return None
