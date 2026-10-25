"""Latin-script words in Russian text (FR-24, §6.6.5): Silero drops them silently.

The lexicon (applied by the core first) covers brands and terms with their usual
Russian reading. This module does the rest, in the order of §6.6.5:

1. homoglyphs — a Cyrillic word with Latin look-alike letters (OCR, PDF) is a typo;
2. acronyms — a lone all-capitals word that cannot be read as a word is spelled with
   English letter names (*HTTP* → «эйчтитип+и»);
3. anything else — transliterated from its English pronunciation: espeak-ng (en-us,
   a system binary, offline) gives IPA, which is mapped to Cyrillic the way Russian
   borrows English words (*deadline* → «д+едлайн», *Docker* → «д+окер»).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from functools import cache

from text2audio.core.contracts import Span
from text2audio.core.errors import SynthesisError
from text2audio.text.spans import foreign_spans

_WORD = re.compile(r"[^\W\d_]+")
_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
_LATIN = re.compile(r"[a-z]", re.IGNORECASE)
HOMOGLYPHS = str.maketrans("aceopxyACEHKMOPTXYB", "асеорхуАСЕНКМОРТХУВ")
_HOMOGLYPH_LETTERS = frozenset("aceopxyACEHKMOPTXYB")

LETTER_NAMES = {
    "A": "эй",
    "B": "би",
    "C": "си",
    "D": "ди",
    "E": "и",
    "F": "эф",
    "G": "джи",
    "H": "эйч",
    "I": "ай",
    "J": "джей",
    "K": "кей",
    "L": "эл",
    "M": "эм",
    "N": "эн",
    "O": "оу",
    "P": "пи",
    "Q": "кью",
    "R": "ар",
    "S": "эс",
    "T": "ти",
    "U": "ю",
    "V": "ви",
    "W": "даблю",
    "X": "экс",
    "Y": "уай",
    "Z": "зет",
}
_ACRONYM = re.compile(r"[A-Z]{2,5}")
_VOWELS_RU = "аеёиоуыэюя"

# --------------------------------------------------------------------------- text passes


def fix_homoglyphs(text: str) -> tuple[str, list[Span]]:
    resolved: list[Span] = []

    def repl(m: re.Match[str]) -> str:
        word = m[0]
        latin = set(_LATIN.findall(word))
        if not latin or not _CYRILLIC.search(word) or not latin <= _HOMOGLYPH_LETTERS:
            return word
        fixed = word.translate(HOMOGLYPHS)
        resolved.append(Span(text=word, script="Latn", resolution="homoglyph", reading=fixed))
        return fixed

    return _WORD.sub(repl, text), resolved


def spell_acronyms(text: str) -> tuple[str, list[Span]]:
    """Spell lone Latin acronyms; words of an English phrase are left to transliteration."""
    resolved: list[Span] = []
    for run in reversed(foreign_spans(text, "Cyrl")):
        word = run.text
        if not _ACRONYM.fullmatch(word):
            continue
        if len(word) > 3 and any(ch in "AEIOUY" for ch in word):
            continue  # NASA, UNIX: read as words
        names = [LETTER_NAMES[ch] for ch in word]
        last = re.sub(rf"([{_VOWELS_RU}])(?!.*[{_VOWELS_RU}])", r"+\1", names[-1])
        reading = "".join(names[:-1]) + last
        resolved.append(Span(text=word, script="Latn", resolution="acronym", reading=reading))
        text = text[: run.start] + reading + text[run.end :]
    return text, resolved[::-1]


# --------------------------------------------------------------------------- IPA → Cyrillic

_IPA = {
    "tʃ": "ч",
    "dʒ": "дж",
    "aɪ": "ай",
    "aʊ": "ау",
    "eɪ": "эй",
    "oʊ": "оу",
    "əʊ": "оу",
    "ɔɪ": "ой",
    "ɪɹ": "ир",
    "ɪə": "ир",
    "ɛɹ": "эр",
    "eə": "эр",
    "ʊɹ": "ур",
    "ɔːɹ": "ор",
    "ɔɹ": "ор",
    "ɑːɹ": "ар",
    "ɑɹ": "ар",
    "ɜːɹ": "ёр",
    "ɚ": "ер",
    "ɝ": "ёр",
    "iː": "и",
    "i": "и",
    "ɪ": "и",
    "ᵻ": "и",
    "ɛ": "э",
    "e": "э",
    "æ": "э",
    "a": "а",
    "ʌ": "а",
    "ɐ": "а",
    "ɑː": "A",  # «о» or «а», by spelling
    "ɑ": "A",
    "ɒ": "о",
    "ɔː": "о",
    "ɔ": "о",
    "oː": "о",
    "o": "о",
    "uː": "у",
    "u": "у",
    "ʊ": "у",
    "ə": "Ə",  # schwa: dropped before a final «л», else «а»
    "ɜː": "ё",
    "ɜ": "ё",
    "p": "п",
    "b": "б",
    "t": "т",
    "d": "д",
    "k": "к",
    "ɡ": "г",
    "g": "г",
    "f": "ф",
    "v": "в",
    "θ": "т",
    "ð": "з",
    "s": "с",
    "z": "з",
    "ʃ": "ш",
    "ʒ": "ж",
    "h": "х",
    "x": "х",
    "m": "м",
    "n": "н",
    "ŋ": "нг",
    "l": "л",
    "ɫ": "л",
    "ɹ": "р",
    "r": "р",
    "ɾ": "т",
    "ʔ": "",
    "j": "J",  # iotates the next vowel: «ю», «я», «е», «ё»
    "w": "у",
    "ˈ": "ˈ",
}
_IPA_KEYS = sorted(_IPA, key=len, reverse=True)
_IOTATED = {"а": "я", "у": "ю", "э": "е", "о": "ё"}
_CONSONANTS = "бвгджзклмнпрстфхцчшщ"


def _ipa_word(ipa: str, spelling: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(ipa):
        key = next((k for k in _IPA_KEYS if ipa.startswith(k, i)), None)
        if key is None:
            i += 1  # length marks, secondary stress, symbols we do not map
            continue
        out.append(_IPA[key])
        i += len(key)
    word = "".join(out)
    back = "о" if "o" in spelling and "a" not in spelling else "а"
    word = word.replace("A", back)
    word = re.sub(r"Ə(?=л$)", "", word).replace("Ə", "а")
    word = re.sub(rf"ˈ([^{_VOWELS_RU}]*)([{_VOWELS_RU}])", r"\1+\2", word).replace("ˈ", "")
    word = re.sub(r"J(\+?)([аэуо])", lambda m: m[1] + _IOTATED[m[2]], word).replace("J", "й")
    return re.sub(rf"(?<=[{_CONSONANTS}])(\+?)э", r"\1е", word)


def ipa_to_cyrillic(ipa: str, spelling: str) -> str:
    """espeak-ng IPA of `spelling` → a Cyrillic reading with `+` before the stress.

    A one-word span stays one word even when espeak splits it ("iPhone" → "ˈaɪ fˈoʊn"),
    keeping the first stress.
    """
    words = [_ipa_word(w, spelling.lower()) for w in ipa.split()]
    if len(spelling.split()) == 1 and len(words) > 1:
        joined = "".join(words)
        first = joined.find("+")
        words = [joined[: first + 1] + joined[first + 1 :].replace("+", "")]
    return " ".join(w for w in words if w)


@cache
def _espeak() -> str:
    exe = shutil.which("espeak-ng")
    if exe is None:
        raise SynthesisError(
            "espeak-ng is needed to read Latin-script words in Russian text (FR-24): "
            "sudo apt install espeak-ng"
        )
    return exe


@cache
def english_ipa(text: str) -> str:
    out = subprocess.run(
        [_espeak(), "-q", "--ipa", "-v", "en-us", text],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout
    return " ".join(out.split())


def transliterate(span: str) -> str:
    return ipa_to_cyrillic(english_ipa(span), span)
