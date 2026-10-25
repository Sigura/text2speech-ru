"""Russian text normalization (§6.6.5, FR-11): everything Silero would drop or misread.

Silero reads only Cyrillic letters: digits, Roman numerals and many abbreviations
vanish silently (spike `ru-smoke`). This pass writes them out as words that agree with
their context: the case comes from the governing preposition and the following noun,
the gender from that noun (*с 5 книгами* → «с пятью книгами», *в 1812 году* →
«в тысяча восемьсот двенадцатом году», *Пётр I* → «Пётр первый»).

rutextnorm was audited against the pack's test set first (§13.1) and rejected: it gets
gender agreement wrong («два книги»), turns *Пётр I* into «Пётр и» and drops ranges.

Order: fixed multi-word abbreviations → dates and times (rewritten into words the
token pass understands) → one token pass for numbers, Roman numerals, units and
abbreviations → initials, acronyms and symbols.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from text2audio.core.contracts import NormResult
from text2audio_lang_ru import morph
from text2audio_lang_ru.numbers import cardinal, decimal, digits, ordinal, roman_value

# --------------------------------------------------------------------------- tables

PREPOSITIONS: dict[str, tuple[str, ...]] = {  # governed cases, the usual one first
    **dict.fromkeys(
        [
            "без",
            "для",
            "до",
            "из",
            "из-за",
            "из-под",
            "от",
            "у",
            "около",
            "возле",
            "вокруг",
            "кроме",
            "после",
            "против",
            "среди",
            "мимо",
            "вместо",
            "вдоль",
            "сверх",
            "свыше",
            "позади",
            "внутри",
            "вне",
            "накануне",
            "ради",
            "насчёт",
            "посреди",
            "порядка",
            "более",
            "менее",
            "больше",
            "меньше",
        ],
        ("gent",),
    ),
    **dict.fromkeys(["к", "ко", "благодаря", "согласно", "вопреки", "навстречу"], ("datv",)),
    "по": ("datv", "accs", "loct"),
    "в": ("accs", "loct"),
    "во": ("accs", "loct"),
    "на": ("accs", "loct"),
    **dict.fromkeys(["о", "об", "обо", "при"], ("loct",)),
    "за": ("accs", "ablt"),
    "под": ("accs", "ablt"),
    **dict.fromkeys(["над", "перед", "передо", "между"], ("ablt",)),
    "с": ("gent", "ablt", "accs"),
    "со": ("gent", "ablt", "accs"),
    **dict.fromkeys(["через", "сквозь", "про"], ("accs",)),
}

MONTHS = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]

# Case of "г." / "в." after a preposition: "в 1812 г." is "в … году", "по 2000 г." "по … год".
DATE_CASE = {"в": "loct", "во": "loct", "на": "loct", "по": "nomn", "за": "nomn", "через": "nomn"}

# Nouns after which a number is an ordinal ("1812 год", "XIX век").
ORDINAL_NOUNS = frozenset({"год", "век", "столетие", "тысячелетие"})
# Nouns before which a number is an ordinal agreeing with them ("Глава 4", "в томе II").
ORDINAL_AFTER = frozenset(
    {
        "глава",
        "часть",
        "том",
        "книга",
        "раздел",
        "акт",
        "сцена",
        "действие",
        "явление",
        "урок",
        "лекция",
        "параграф",
        "статья",
    }
)

# Units and currency written after a number: token (lowercase, without the dot) → lemma.
UNITS = {
    "%": "процент",
    "‰": "промилле",
    "₽": "рубль",
    "руб": "рубль",
    "р": "рубль",
    "коп": "копейка",
    "$": "доллар",
    "долл": "доллар",
    "€": "евро",
    "£": "фунт",
    "км": "километр",
    "м": "метр",
    "см": "сантиметр",
    "мм": "миллиметр",
    "кг": "килограмм",
    "т": "тонна",
    "л": "литр",
    "мл": "миллилитр",
    "ч": "час",
    "мин": "минута",
    "сек": "секунда",
    "°": "градус",
}
CURRENCY_PREFIX = {"$": "доллар", "€": "евро", "£": "фунт", "₽": "рубль"}
MULTIPLIERS = {"тыс": "тысяча", "млн": "миллион", "млрд": "миллиард", "трлн": "триллион"}

# Abbreviations read in place, outside number contexts: token → (lemma, when).
# when: "cap" = before a capitalized word, "num" = before a number, "gen" = before a
# capitalized genitive ("им. Пушкина", not the pronoun in "воспользовался им. За"), "any".
ABBREVIATIONS: dict[str, tuple[str, str]] = {
    "г": ("город", "cap"),
    "ул": ("улица", "cap"),
    "пер": ("переулок", "cap"),
    "пр": ("проспект", "cap"),
    "кн": ("князь", "cap"),
    "р": ("река", "cap"),
    "д": ("дом", "num"),
    "кв": ("квартира", "num"),
    "стр": ("страница", "num"),
    "с": ("страница", "num"),
    "ст": ("статья", "num"),
    "рис": ("рисунок", "num"),
    "табл": ("таблица", "num"),
    "гл": ("глава", "any"),
    "им": ("имени", "gen"),
    "проф": ("профессор", "any"),
    "акад": ("академик", "any"),
    "доц": ("доцент", "any"),
    "тов": ("товарищ", "any"),
    "св": ("святой", "any"),
    "гр": ("гражданин", "any"),
    "см": ("смотри", "any"),
    "ср": ("сравни", "any"),
    "напр": ("например", "any"),
}
_INDECLINABLE = frozenset({"имени", "смотри", "сравни", "например", "святой"})

FIXED = [  # multi-word abbreviations, matched on the raw text first
    (r"\bи\s+т\.\s*д\.", "и так далее"),
    (r"\bи\s+т\.\s*п\.", "и тому подобное"),
    (r"\bт\.\s*е\.", "то есть"),
    (r"\bт\.\s*к\.", "так как"),
    (r"\bт\.\s*д\.", "так далее"),
    (r"\bт\.\s*п\.", "тому подобное"),
    (r"\bи\s+др\.", "и другие"),
    (r"\bи\s+пр\.", "и прочее"),
    (r"\bдо\s+н\.\s*э\.", "до нашей эры"),
    (r"\bн\.\s*э\.", "нашей эры"),
    (r"\bт\.\s*н\.", "так называемый"),
    (r"\bт\.\s*ч\.", "том числе"),
]
_FIXED = [(re.compile(p), r) for p, r in FIXED]

LETTERS = {
    "А": "а",
    "Б": "бэ",
    "В": "вэ",
    "Г": "гэ",
    "Д": "дэ",
    "Е": "е",
    "Ё": "ё",
    "Ж": "жэ",
    "З": "зэ",
    "И": "и",
    "Й": "й",
    "К": "ка",
    "Л": "эл",
    "М": "эм",
    "Н": "эн",
    "О": "о",
    "П": "пэ",
    "Р": "эр",
    "С": "эс",
    "Т": "тэ",
    "У": "у",
    "Ф": "эф",
    "Х": "ха",
    "Ц": "цэ",
    "Ч": "чэ",
    "Ш": "ша",
    "Щ": "ща",
    "Ъ": "твёрдый знак",
    "Ы": "ы",
    "Ь": "мягкий знак",
    "Э": "э",
    "Ю": "ю",
    "Я": "я",
}
_VOWELS = set("АЕЁИОУЫЭЮЯ")
SYMBOLS = {"№": "номер", "§": "параграф", "&": "и"}

_SUFFIXES = {  # ordinal endings "5-й", "1-го" → (case, gender, plural) when no noun says
    "й": ("nomn", "m", False),
    "ый": ("nomn", "m", False),
    "ий": ("nomn", "m", False),
    "ой": ("gent", "f", False),
    "я": ("nomn", "f", False),
    "ая": ("nomn", "f", False),
    "е": ("nomn", "n", False),
    "ое": ("nomn", "n", False),
    "ю": ("accs", "f", False),
    "ую": ("accs", "f", False),
    "го": ("gent", "m", False),
    "ого": ("gent", "m", False),
    "его": ("gent", "m", False),
    "му": ("datv", "m", False),
    "ому": ("datv", "m", False),
    "ему": ("datv", "m", False),
    "м": ("loct", "m", False),
    "ом": ("loct", "m", False),
    "ем": ("loct", "m", False),
    "ым": ("ablt", "m", False),
    "им": ("ablt", "m", False),
    "х": ("loct", "m", True),
    "ых": ("loct", "m", True),
    "их": ("loct", "m", True),
    "е годы": ("nomn", "m", True),
    "ми": ("ablt", "m", True),
    "ыми": ("ablt", "m", True),
}


def _dates(n: int, r: morph.Form) -> bool:
    """Whether 'n <noun>' names a year or century rather than counting them.

    "1812 год", "в 41 году", "19 век" name one; "3 года", "5 лет", "2 века" count.
    """
    if r.lemma == "год":
        if r.plural:
            return False
        return n >= 1000 or r.case == "loct"
    if r.lemma in ("век", "столетие", "тысячелетие"):
        if r.plural:
            return False
        return not (2 <= n % 10 <= 4 and r.case == "gent" and n < 10)
    return False


# --------------------------------------------------------------------------- tokens

_SPACES = "    "
_TOKEN = re.compile(
    rf"(?P<num>\d+(?:[{_SPACES} ]\d{{3}}(?!\d))*(?:,\d+)?)"
    r"|(?P<word>(?:[^\W\d_]|[́+])+(?:[-’'](?:[^\W\d_]|[́+])+)*)"
    r"|(?P<space>\s+)"
    r"|(?P<punct>.)",
    re.DOTALL,
)
_DASHES = "-–—"


@dataclass
class Tok:
    kind: str
    text: str
    out: str | None = None  # replacement; "" drops the token

    @property
    def low(self) -> str:
        return self.text.lower().replace("́", "").replace("+", "")

    @property
    def rendered(self) -> str:
        return self.text if self.out is None else self.out


def tokenize(text: str) -> list[Tok]:
    return [Tok(m.lastgroup or "punct", m.group()) for m in _TOKEN.finditer(text)]


class _Pass:
    def __init__(self, toks: list[Tok], role: str) -> None:
        self.toks = toks
        self.role = role
        self.uncertain: list[str] = []

    # -- navigation ------------------------------------------------------------

    def next_idx(self, i: int, spaces: bool = True) -> int | None:
        j = i + 1
        while spaces and j < len(self.toks) and self.toks[j].kind == "space":
            j += 1
        return j if j < len(self.toks) else None

    def prev_idx(self, i: int) -> int | None:
        j = i - 1
        while j >= 0 and self.toks[j].kind == "space":
            j -= 1
        return j if j >= 0 else None

    def tok(self, i: int | None) -> Tok | None:
        return self.toks[i] if i is not None and 0 <= i < len(self.toks) else None

    def is_dot(self, i: int | None) -> bool:
        t = self.tok(i)
        return t is not None and t.kind == "punct" and t.text == "."

    def abbrev_at(self, i: int | None) -> tuple[str, int] | None:
        """A word followed directly by a dot: (word lowercase, index of the dot)."""
        t = self.tok(i)
        if t is None or i is None or t.kind != "word":
            return None
        d = i + 1
        return (t.low, d) if self.is_dot(d) else None

    def drop_dot(self, d: int) -> None:
        """Remove an abbreviation's dot, unless it also ends the text."""
        if self.next_idx(d) is not None:
            self.toks[d].out = ""

    def preposition(self, i: int) -> tuple[str, ...]:
        p = self.tok(self.prev_idx(i))
        if p is not None and p.kind == "word" and p.low in PREPOSITIONS:
            return PREPOSITIONS[p.low]
        return ()

    # -- number context ----------------------------------------------------------

    def unit_after(self, i: int) -> tuple[str, int, int | None] | None:
        """A unit, currency or multiplier right after token i: (lemma, index, dot)."""
        j = self.next_idx(i)
        t = self.tok(j)
        if t is None or j is None:
            return None
        if t.kind == "punct" and t.text in UNITS:
            return UNITS[t.text], j, None
        if t.kind != "word":
            return None
        ab = self.abbrev_at(j)
        word = t.low
        if word in MULTIPLIERS:
            return MULTIPLIERS[word], j, ab[1] if ab else None
        if word in UNITS and t.text == t.text.lower():
            after = self.tok(self.next_idx(ab[1] if ab else j))
            # "5 с половиной", "в 5 м доме" — a unit stands before punctuation or end.
            if word in ("с", "м", "т", "л", "р") and after is not None and after.kind == "word":
                return None
            return UNITS[word], j, ab[1] if ab else None
        return None

    def noun_after(self, i: int) -> tuple[int, tuple[morph.Form, ...]] | None:
        """The word a number agrees with (an adjective may stand in between)."""
        j = self.next_idx(i)
        t = self.tok(j)
        if t is None or j is None or t.kind != "word":
            return None
        readings = morph.nominal(t.low)
        if not readings:
            return None
        if all(r.pos != "NOUN" for r in readings):
            k = self.next_idx(j)
            u = self.tok(k)
            if u is not None and k is not None and u.kind == "word":
                nouns = tuple(r for r in morph.nominal(u.low) if r.pos == "NOUN")
                if nouns:
                    return k, nouns
        return j, readings

    @staticmethod
    def pick_case(readings: tuple[morph.Form, ...], prep: tuple[str, ...]) -> morph.Form:
        for r in readings:
            if r.case in prep:
                return r
        return readings[0]

    # -- rules -------------------------------------------------------------------

    def ordinal_suffix(self, i: int) -> tuple[str, int] | None:
        """'5-й', '90-х': (suffix, index of the suffix word)."""
        d = i + 1
        t = self.tok(d)
        if t is not None and t.kind == "punct" and t.text in _DASHES:
            w = self.tok(d + 1)
            if w is not None and w.kind == "word" and w.low in _SUFFIXES:
                return w.low, d + 1
        return None

    def range_end(self, i: int) -> int | None:
        """'5–7', '1941 — 1945': the index of the second number."""
        j = self.next_idx(i)
        t = self.tok(j)
        if t is None or j is None or t.kind != "punct" or t.text not in _DASHES:
            return None
        k = self.next_idx(j)
        u = self.tok(k)
        if u is not None and u.kind == "num" and "," not in u.text:
            return k
        return None

    def number(self, i: int) -> None:
        t = self.toks[i]
        raw = re.sub(rf"[{_SPACES} ]", "", t.text)
        if "," in raw:
            self.decimal(i, raw)
            return
        if (len(raw) > 1 and raw.startswith("0")) or len(raw) > 15:
            t.out = digits(raw)
            return
        n = int(raw)
        prev = self.tok(self.prev_idx(i))
        if (
            prev is not None
            and prev.kind == "punct"
            and prev.text == "-"
            and i >= 2
            and self.toks[i - 2].kind == "word"
        ):
            prev.out = " "
        suffix = self.ordinal_suffix(i)
        if suffix is not None:
            self.suffixed(i, n, *suffix)
            return
        end = self.range_end(i)
        if end is not None:
            self.range(i, n, end)
            return
        if prev is not None and prev.kind == "word" and morph.lemma_is(prev.low, ORDINAL_AFTER):
            self.after_noun(i, n, prev)
            return
        if self.role in ("heading", "title") and self._alone(i):
            t.out = cardinal(n)
            return
        t.out = self.counted(i, n, self.preposition(i))

    def _alone(self, i: int) -> bool:
        others = [x for k, x in enumerate(self.toks) if k != i and x.kind in ("num", "word")]
        return not others

    def after_noun(self, i: int, n: int, noun: Tok) -> None:
        r = morph.lemma_is(noun.low, ORDINAL_AFTER)
        case, gender = (r.case or "nomn", r.gender or "m") if r else ("nomn", "f")
        self.toks[i].out = ordinal(n, case, gender)

    def suffixed(self, i: int, n: int, suffix: str, w: int) -> None:
        case, gender, plural = _SUFFIXES[suffix]
        found = self.noun_after(w)
        if found is not None:
            r = self.pick_case(found[1], self.preposition(i))
            case, gender, plural = r.case or case, r.gender or gender, r.plural
        elif suffix in ("е", "ые") and n % 10 == 0 and n >= 10:
            plural = True  # "в 90-е" — the nineties
        self.toks[i].out = ordinal(n, case, gender, plural)
        self.toks[i + 1].out = ""  # the dash
        self.toks[w].out = ""

    def day(self, i: int, n: int, prep: tuple[str, ...]) -> str:
        p = self.tok(self.prev_idx(i))
        if p is not None and p.low in ("по", "на", "в"):
            return ordinal(n, "nomn", "n")  # "по десятое мая", "на пятое июня"
        if prep:
            return ordinal(n, prep[0], "n")
        # A date opening the text is named ("12 июня — праздник"); inside a sentence
        # it says when ("родился пятого мая").
        return ordinal(n, "nomn" if self.prev_idx(i) is None else "gent", "n")

    def month_later(self, i: int) -> bool:
        """'с 1 по 10 мая', '1 и 2 июня': a day whose month follows the next number."""
        j = self.next_idx(i)
        t = self.tok(j)
        if t is None or j is None or t.low not in ("по", "до", "и", "или"):
            return False
        k = self.next_idx(j)
        u = self.tok(k)
        if u is None or k is None or u.kind != "num":
            return False
        w = self.tok(self.next_idx(k))
        return w is not None and w.low in MONTHS

    def counted(self, i: int, n: int, prep: tuple[str, ...]) -> str:
        """A number with what it counts or dates: years, units, nouns."""
        unit = self.unit_after(i)
        if unit is not None:
            return self.with_unit(i, n, prep, unit)
        abbrev = self.abbrev_at(self.next_idx(i))
        if abbrev is not None and abbrev[0] in ("г", "гг", "в", "вв"):
            return self.year_or_century(i, n, prep, abbrev)
        if self.month_later(i):
            return self.day(i, n, prep)
        found = self.noun_after(i)
        if found is None:
            case = prep[0] if prep else "nomn"
            return cardinal(n, case)
        j, readings = found
        noun = self.toks[j]
        if noun.low in MONTHS:  # "12 июня" — the day is an ordinal, neuter
            return self.day(i, n, prep)
            # A date opening the text is named ("12 июня — праздник"); inside a sentence
            # it says when ("родился пятого мая").
            return ordinal(n, "nomn" if self.prev_idx(i) is None else "gent", "n")
        r = self.pick_case(readings, prep)
        dated = next((x for x in readings if _dates(n, x)), None)
        if dated is not None:
            r = dated if dated.case in prep or not prep else r
            if _dates(n, r):
                return ordinal(n, r.case or "nomn", r.gender or "m", r.plural)
        case = "nomn"
        hit = next((x for x in readings if x.case in prep and x.case != "accs"), None)
        if hit is not None:
            r, case = hit, hit.case or "nomn"
        elif r.case in ("datv", "ablt", "loct"):
            case = r.case
        return cardinal(n, case, r.gender or "m")

    def with_unit(
        self, i: int, n: int, prep: tuple[str, ...], unit: tuple[str, int, int | None]
    ) -> str:
        lemma, j, dot = unit
        case = prep[0] if prep else "nomn"
        if case == "accs":
            case = "nomn"
        gender = morph.gender_of(lemma)
        self.toks[j].out = (" " if j == i + 1 else "") + morph.agree(lemma, n, case)
        if dot is not None:
            self.drop_dot(dot)
        if lemma in MULTIPLIERS.values():  # "5 тыс. руб." → «пять тысяч рублей»
            after = self.unit_after(dot if dot is not None else j)
            if after is not None and after[0] not in MULTIPLIERS.values():
                lemma2, k, dot2 = after
                self.toks[k].out = morph.inflect(lemma2, "gent" if case == "nomn" else case, True)
                if dot2 is not None:
                    self.drop_dot(dot2)
        return cardinal(n, case, gender)

    def year_or_century(
        self, i: int, n: int, prep: tuple[str, ...], abbrev: tuple[str, int]
    ) -> str:
        word, dot = abbrev
        j = dot - 1
        plural = word in ("гг", "вв")
        lemma = "год" if word.startswith("г") else "век"
        case = self.date_case(i, prep)
        self.toks[j].out = morph.inflect(lemma, case, plural)
        self.drop_dot(dot)
        return ordinal(n, case, "m")

    def date_case(self, i: int, prep: tuple[str, ...]) -> str:
        """Case of a year or century named by an abbreviation ("1812 г.", "XIX в.")."""
        p = self.tok(self.prev_idx(i))
        if p is not None and p.kind == "word":
            if p.low in DATE_CASE:
                return DATE_CASE[p.low]
            if prep:
                return "nomn" if prep[0] == "accs" else prep[0]
            if p.low in MONTHS or any(f.pos == "NOUN" for f in morph.forms(p.low)):
                return "gent"  # "5 мая 1812 г.", "весной 1945 г."
        return "nomn"

    def range(self, i: int, n: int, k: int) -> None:
        """'5–7 лет' → «от пяти до семи лет»; '1941–1945 гг.' → ordinals in one case."""
        second = int(re.sub(rf"[{_SPACES} ]", "", self.toks[k].text))
        prep = self.preposition(i)
        for d in range(i + 1, k):
            self.toks[d].out = "" if self.toks[d].kind == "space" else " — "
        abbrev = self.abbrev_at(self.next_idx(k))
        found = self.noun_after(k)
        is_year = abbrev is not None and abbrev[0] in ("г", "гг", "в", "вв")
        if not is_year and found is not None:
            r = self.pick_case(found[1], prep)
            is_year = (r.lemma == "год" and n >= 1000) or (
                r.lemma in ORDINAL_NOUNS and r.plural and (r.case != "gent")
            )
        if is_year:
            if abbrev is not None and abbrev[0] in ("г", "гг", "в", "вв"):
                case = self.date_case(i, prep)
                word, dot = abbrev
                lemma = "год" if word.startswith("г") else "век"
                self.toks[dot - 1].out = morph.inflect(lemma, case, True)
                self.drop_dot(dot)
                self.toks[k].out = ordinal(second, case, "m")
            else:
                assert found is not None
                case = self.pick_case(found[1], prep).case or "nomn"
                self.toks[k].out = ordinal(second, case, "m")
            self.toks[i].out = ordinal(n, case, "m")
            return
        if prep:
            self.toks[k].out = self.counted(k, second, prep)
            unit = self.unit_after(k)
            case = prep[0] if prep[0] != "accs" else "nomn"
            if unit is None and found is not None:
                hit = next((x for x in found[1] if x.case in prep), None)
                case = hit.case if hit is not None and hit.case else "nomn"
            self.toks[i].out = cardinal(n, case, self._gender_after(k))
            return
        # No preposition: «от A до B» in the genitive, the noun in the genitive plural.
        gender = self._gender_after(k)
        self.toks[i].out = "от " + cardinal(n, "gent", gender)
        for d in range(i + 1, k):
            self.toks[d].out = "" if self.toks[d].kind == "space" else " до "
        self.toks[k].out = cardinal(second, "gent", gender)
        unit = self.unit_after(k)
        if unit is not None:
            lemma, j, dot = unit
            self.toks[j].out = morph.inflect(lemma, "gent", True)
            if dot is not None:
                self.drop_dot(dot)
        elif found is not None:
            j, readings = found
            if not any(r.case == "gent" and r.plural for r in readings):
                self.toks[j].out = morph.inflect(readings[0].lemma, "gent", True)

    def _ordinal_case(self, k: int) -> str:
        """Case of the ordinal just written at k, read back from its noun."""
        out = self.toks[k].out or ""
        last = out.split()[-1] if out else ""
        r = morph.nominal(last)
        return r[0].case or "nomn" if r else "nomn"

    def _gender_after(self, k: int) -> str:
        unit = self.unit_after(k)
        if unit is not None:
            return morph.gender_of(unit[0])
        found = self.noun_after(k)
        return found[1][0].gender or "m" if found else "m"

    def decimal(self, i: int, raw: str) -> None:
        unit = self.unit_after(i)
        if raw in ("1,5", "1.5"):
            lemma = unit[0] if unit else None
            found = self.noun_after(i) if unit is None else None
            gender = morph.gender_of(lemma) if lemma else (found[1][0].gender if found else "m")
            self.toks[i].out = "полторы" if gender == "f" else "полтора"
            if unit is not None:
                _, j, dot = unit
                self.toks[j].out = morph.inflect(unit[0], "gent", False)
                if dot is not None:
                    self.drop_dot(dot)
            return
        self.toks[i].out = decimal(raw)
        if unit is not None:
            lemma, j, dot = unit
            self.toks[j].out = (" " if j == i + 1 else "") + morph.inflect(lemma, "gent", False)
            if dot is not None:
                self.drop_dot(dot)

    def roman(self, i: int) -> None:
        t = self.toks[i]
        if t.text != t.text.upper() or len(t.text) > 9:
            return
        n = roman_value(t.text)
        if n is None:
            return
        prev = self.tok(self.prev_idx(i))
        if prev is not None and prev.kind == "word" and prev.text[:1].isupper():
            name = next((f for f in morph.forms(prev.low) if f.name), None)
            if name is not None:  # "Пётр I", "при Николае II"
                t.out = ordinal(n, name.case or "nomn", name.gender or "m")
                return
        if prev is not None and prev.kind == "word" and morph.lemma_is(prev.low, ORDINAL_AFTER):
            self.after_noun(i, n, prev)
            return
        abbrev = self.abbrev_at(self.next_idx(i))
        found = self.noun_after(i)
        end = self.range_end_roman(i)
        if end is not None:
            # "XVIII–XIX вв." — both centuries in the case of the second.
            second = roman_value(self.toks[end].text)
            assert second is not None
            self.toks[end].out = self._century(end, second, abbrev_at=end, start=i)
            for d in range(i + 1, end):
                self.toks[d].out = "" if self.toks[d].kind == "space" else " — "
            t.out = ordinal(n, self._ordinal_case(end), "m")
            return
        if (abbrev is not None and abbrev[0] in ("в", "вв")) or (
            found is not None and any(r.lemma in ORDINAL_NOUNS for r in found[1])
        ):
            t.out = self._century(i, n, abbrev_at=i)
            return
        if self.role in ("heading", "title") and self._alone(i):
            t.out = cardinal(n)
            return
        if len(t.text) > 1 or t.text in "VXLCDM":
            self.uncertain.append(t.text)

    def range_end_roman(self, i: int) -> int | None:
        j = self.next_idx(i)
        t = self.tok(j)
        if t is None or j is None or t.kind != "punct" or t.text not in _DASHES:
            return None
        k = self.next_idx(j)
        u = self.tok(k)
        if u is not None and k is not None and u.kind == "word" and roman_value(u.text):
            return k
        return None

    def _century(self, i: int, n: int, abbrev_at: int, start: int | None = None) -> str:
        first = i if start is None else start
        prep = self.preposition(first)
        abbrev = self.abbrev_at(self.next_idx(abbrev_at))
        if abbrev is not None and abbrev[0] in ("в", "вв"):
            word, dot = abbrev
            case = self.date_case(first, prep)
            self.toks[dot - 1].out = morph.inflect("век", case, word == "вв")
            self.drop_dot(dot)
            return ordinal(n, case, "m")
        found = self.noun_after(abbrev_at)
        if found is None:
            return ordinal(n)
        r = self.pick_case(found[1], prep)
        return ordinal(n, r.case or "nomn", r.gender or "m", r.plural)

    def abbreviation(self, i: int) -> None:
        ab = self.abbrev_at(i)
        if ab is None or self.toks[i].out is not None:
            return
        word, dot = ab
        if word not in ABBREVIATIONS or (self.toks[i].text[:1].isupper() and len(word) == 1):
            return  # "А." is an initial
        lemma, when = ABBREVIATIONS[word]
        after = self.tok(self.next_idx(dot))
        if when == "cap" and not (after is not None and after.text[:1].isupper()):
            return
        if when == "num" and not (after is not None and after.kind == "num"):
            return
        if when == "gen" and not (
            after is not None
            and after.text[:1].isupper()
            and any(f.case == "gent" for f in morph.nominal(after.low))
        ):
            return
        prep = self.preposition(i)
        if lemma in _INDECLINABLE or not prep:
            form = lemma
        else:
            case = "loct" if "loct" in prep else prep[0]
            form = morph.inflect(lemma, case, False)
        self.toks[i].out = form
        self.drop_dot(dot)

    def run(self) -> None:
        for i, t in enumerate(self.toks):
            if t.out is not None:
                continue
            if t.kind == "num":
                self.number(i)
            elif t.kind == "word":
                self.roman(i)
        for i, t in enumerate(self.toks):
            if t.kind == "word" and t.out is None:
                self.abbreviation(i)
        for i, t in enumerate(self.toks):
            if t.out is None and t.kind == "punct" and t.text in CURRENCY_PREFIX:
                self.currency_prefix(i)
            elif t.out is None and t.kind == "punct" and t.text in SYMBOLS:
                t.out = SYMBOLS[t.text]

    def currency_prefix(self, i: int) -> None:
        """'$20' → «двадцать долларов»."""
        j = i + 1
        t = self.tok(j)
        if t is None or t.kind != "num" or "," in t.text:
            return
        n = int(re.sub(rf"[{_SPACES} ]", "", t.text))
        lemma = CURRENCY_PREFIX[self.toks[i].text]
        case = self.preposition(i)
        c = case[0] if case and case[0] != "accs" else "nomn"
        self.toks[i].out = ""
        t.out = f"{cardinal(n, c, morph.gender_of(lemma))} {morph.agree(lemma, n, c)}"


# --------------------------------------------------------------------------- text passes

_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b")
_TIME = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")
_INITIALS = re.compile(r"\b([А-ЯЁ])\.\s*(?=(?:[А-ЯЁ]\.\s*)*[А-ЯЁ][а-яё])")
_ACRONYM = re.compile(r"\b[А-ЯЁ]{2,5}\b")
_MIXED_SPACES = re.compile(r"[ \t]{2,}")


def _date(m: re.Match[str]) -> str:
    day, month, year = int(m[1]), int(m[2]), m[3]
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return m[0]
    return f"{day} {MONTHS[month - 1]} {year} года"


def _minutes(mm: str) -> str:
    if mm == "00":
        return "ноль-ноль"
    return f"ноль {cardinal(int(mm))}" if mm.startswith("0") else cardinal(int(mm))


def _time(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        before = text[: m.start()].split()
        prep = PREPOSITIONS.get(before[-1].lower(), ()) if before else ()
        case = prep[0] if prep and prep[0] not in ("accs", "loct") else "nomn"
        if case == "nomn":
            return f"{cardinal(int(m[1]))} {_minutes(m[2])}"
        if m[2] == "00":
            return cardinal(int(m[1]), case)  # "с девяти до восемнадцати"
        return f"{cardinal(int(m[1]), case)} {cardinal(int(m[2]), case)}"

    return _TIME.sub(repl, text)


def _acronyms(text: str) -> str:
    words = re.findall(r"[^\W\d_]+", text)
    caps = sum(w.isupper() and len(w) > 1 for w in words)
    if caps >= 2 and caps > len(words) / 2:
        return text  # a line in capitals ("ГЛАВА ПЕРВАЯ") is not a row of acronyms

    def repl(m: re.Match[str]) -> str:
        word = m[0]
        vowels = [ch in _VOWELS for ch in word]
        spell = not any(vowels) or (len(word) <= 3 and not any(vowels[:-1]))
        if not spell:
            return word
        letters = [LETTERS[ch] for ch in word]
        last = letters[-1]
        # Stress falls on the last letter name: «эсэсэс+эр», «ка-гэ-б+э».
        stressed = re.sub(r"([аеёиоуыэюя])(?!.*[аеёиоуыэюя])", r"+\1", last)
        return "".join(letters[:-1]) + stressed

    return _ACRONYM.sub(repl, text)


def _initials(text: str) -> str:
    return _INITIALS.sub(lambda m: LETTERS[m[1]].capitalize() + " ", text)


class RussianNormalizer:
    """Entry point `text2audio.normalizers: ru`."""

    def normalize(self, text: str, role: str = "narration") -> NormResult:
        for pattern, repl in _FIXED:
            text = pattern.sub(repl, text)
        text = _DATE.sub(_date, text)
        text = _time(text)
        p = _Pass(tokenize(text), role)
        p.run()
        text = "".join(t.rendered for t in p.toks)
        text = _initials(text)
        text = _acronyms(text)
        text = _MIXED_SPACES.sub(" ", text).strip()
        return NormResult(text=text, uncertain=p.uncertain)
