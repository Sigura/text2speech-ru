"""Morphology helpers over pymorphy3: the case, gender and number of a word.

The dictionary ships inside the `pymorphy3-dicts-ru` wheel, so nothing is downloaded
(§6.10). Every lookup is cached: books repeat the same few thousand word forms.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache, lru_cache
from typing import Any

# pymorphy3 case grammemes → the six cases (gen2/loc2/acc2 are variants).
CASES = {
    "nomn": "nomn",
    "gent": "gent",
    "gen1": "gent",
    "gen2": "gent",
    "datv": "datv",
    "accs": "accs",
    "acc2": "accs",
    "ablt": "ablt",
    "loct": "loct",
    "loc1": "loct",
    "loc2": "loct",
    "voct": "nomn",
}
GENDERS = {"masc": "m", "femn": "f", "neut": "n", "ms-f": "f"}  # "глава": common gender
# Nouns whose locative after "в/на" is the second locative ("в году", "в часу").
LOC2 = frozenset({"год", "час"})


@cache
def analyzer() -> Any:
    import pymorphy3

    return pymorphy3.MorphAnalyzer(lang="ru")


@lru_cache(maxsize=65536)
def known(word: str) -> bool:
    return bool(analyzer().word_is_known(word.lower()))


@dataclass(frozen=True)
class Form:
    pos: str | None
    case: str | None  # one of the six case grammemes
    gender: str | None  # "m" | "f" | "n" of the lemma
    plural: bool
    animate: bool
    lemma: str
    name: bool  # a first name ("Пётр", "Екатерина")


@lru_cache(maxsize=65536)
def forms(word: str) -> tuple[Form, ...]:
    """All readings of a word form, most probable first."""
    out = []
    for p in analyzer().parse(word.lower()):
        tag = p.tag
        lemma_tag = p.normalized.tag
        out.append(
            Form(
                pos=tag.POS,
                case=CASES.get(str(tag.case)) if tag.case else None,
                gender=GENDERS.get(str(lemma_tag.gender or tag.gender or ""))
                or ("f" if "ms-f" in tag else None),
                plural=tag.number == "plur",
                animate=tag.animacy == "anim",
                lemma=p.normal_form,
                name="Name" in tag,
            )
        )
    return tuple(out)


@lru_cache(maxsize=16384)
def imperative(word: str) -> bool:
    return any(p.tag.mood == "impr" for p in analyzer().parse(word.lower()))


def nominal(word: str) -> tuple[Form, ...]:
    """Readings as a noun, adjective or participle (what a number agrees with)."""
    return tuple(f for f in forms(word) if f.pos in ("NOUN", "ADJF", "PRTF") and f.case)


def lemma_is(word: str, lemmas: frozenset[str]) -> Form | None:
    """The reading of `word` with one of `lemmas`; inanimate first ("глава" = chapter)."""
    hits = [f for f in nominal(word) if f.lemma in lemmas]
    return min(hits, key=lambda f: f.animate) if hits else None


@lru_cache(maxsize=16384)
def inflect(lemma: str, case: str, plural: bool) -> str:
    """`lemma` (a noun) in the given case and number; the lemma itself if unknown."""
    for p in analyzer().parse(lemma):
        if p.tag.POS == "NOUN" and p.normal_form == lemma:
            grammemes = {case, "plur" if plural else "sing"}
            if case == "loct" and lemma in LOC2 and not plural:
                grammemes = {"loc2", "sing"}
            form = p.inflect(grammemes)
            if form is not None:
                return str(form.word)
    return lemma


def plural_for(n: int) -> bool:
    """Whether a noun after the number `n` is plural in an oblique case."""
    return not (n % 10 == 1 and n % 100 != 11)


# Counting forms pymorphy3 returns in the colloquial variant.
_GEN_PLURAL = {"килограмм": "килограммов", "грамм": "граммов", "миллиграмм": "миллиграммов"}


@lru_cache(maxsize=16384)
def agree(lemma: str, n: int, case: str = "nomn") -> str:
    """Noun form after the cardinal `n` in the phrase's case (5 рублей, с 5 рублями)."""
    if case in ("nomn", "accs"):
        for p in analyzer().parse(lemma):
            # Start from the nominative singular reading ("килограмм" is also gen. plural).
            if p.tag.POS == "NOUN" and p.normal_form == lemma and {"nomn", "sing"} in p.tag:
                form = p.make_agree_with_number(n)
                if form is not None:
                    word = str(form.word)
                    return _GEN_PLURAL.get(word, word) if word == lemma and plural_for(n) else word
        return lemma
    return inflect(lemma, case, plural_for(n))


def gender_of(lemma: str) -> str:
    for p in analyzer().parse(lemma):
        if p.tag.POS == "NOUN" and p.tag.gender:
            return GENDERS.get(str(p.tag.gender), "m")
    return "m"
