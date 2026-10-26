"""Russian language pack (SPEC §6.6.5, ADR-0008).

Text structure for TXT/PDF headings (M1); normalization (`normalize.py`), the stress
component (RUAccent), voices, tier routes and QA bounds (M2). Components are named by
entry point; the pack itself stays free of heavy dependencies.
"""

from __future__ import annotations

from pathlib import Path

from text2audio.core.contracts import LanguageProfile, QaProfile, TierRoute

_ORDINAL = (
    r"(?:(?:двадцать|тридцать|сорок|пятьдесят|шестьдесят)\s+)?"
    r"(?:перв|втор|трет|четв[её]рт|пят|шест|седьм|восьм|девят|десят|одиннадцат|"
    r"двенадцат|тринадцат|четырнадцат|пятнадцат|шестнадцат|семнадцат|восемнадцат|"
    r"девятнадцат|двадцат|тридцат|сороков|пятидесят|последн)[а-яё]*"
)
_NUMBER = rf"(?:[IVXLCDM]+|\d+|{_ORDINAL})"  # Roman, Arabic or an ordinal: «первая»
_TITLE_TAIL = r"(?:\s*[.:—–-]?\s+\S.*)?"  # "Глава 1. Название", "Глава 1 — Название"
# Chatterbox clones the narrator from a 15 s Silero `xenia` clip (owner-approved, §13.1).
_VOICES = Path(__file__).parent / "voices"

PROFILE = LanguageProfile(
    code="ru",
    name="Русский",
    script="Cyrl",
    part_headings=(rf"(?:часть|книга|том)\s+{_NUMBER}\.?{_TITLE_TAIL}",),
    chapter_headings=(
        rf"(?:глава|гл\.)\s*{_NUMBER}\.?{_TITLE_TAIL}",
        rf"(?:пролог|эпилог|предисловие|послесловие|вступление|введение|заключение)\.?{_TITLE_TAIL}",
    ),
    front_matter=(r"(?:содержание|оглавление)\.?",),
    normalizer="ru",
    g2p="ruaccent",
    tiers={
        "fast": TierRoute(backend="silero"),
        # §6.6.6: Silero → Chatterbox VC; segments with Latin words read by Chatterbox.
        "expressive": TierRoute(backend="silero", polish="chatterbox", foreign="chatterbox"),
    },
    voices={
        "narrator": {"silero": "xenia", "chatterbox": str(_VOICES / "ref-xenia.wav")},
        "dialogue": {"silero": "xenia", "chatterbox": str(_VOICES / "ref-xenia.wav")},
    },
    pauses_ms={"paragraph": 800},
    dialogue="dash",
    # Verbs of speech that open an author's remark even without a comma before the dash
    # («— Прикажи читать далее — отвечал Савельич»); prefixes of their forms.
    remark_words=(
        "сказа",
        "говори",
        "ответи",
        "отвеча",
        "спроси",
        "спрашива",
        "вскрича",
        "закрича",
        "крикну",
        "замети",
        "прерва",
        "продолжа",
        "произнё",
        "произне",
        "прошепта",
        "шепну",
        "воскликну",
        "подхвати",
        "возрази",
        "добави",
        "пробормота",
        "молви",
        "повтори",
        "отрезал",
        "согласи",
    ),
    qa=QaProfile(asr="gigaam", wer_threshold=0.18, chars_per_sec=(6.0, 25.0), fold={"ё": "е"}),
    translator="opus-mt-ru-en",  # English cover prompts (§6.8.2)
    lexicon=str(Path(__file__).parent / "lexicon.tsv"),
    part_title="{title} (часть {n} из {total})",
    abbreviations=(
        "т. е.",
        "т. к.",
        "т. д.",
        "т. п.",
        "и др.",
        "и пр.",
        "см.",
        "ср.",
        "напр.",
        "г.",
        "гг.",
        "в.",
        "вв.",
        "н. э.",
        "до н. э.",
        "р.",
        "руб.",
        "коп.",
        "тыс.",
        "млн.",
        "млрд.",
        "ул.",
        "пр.",
        "пер.",
        "д.",
        "кв.",
        "им.",
        "тов.",
        "гр.",
        "ст.",
        "с.",
        "стр.",
        "гл.",
        "рис.",
        "табл.",
        "проф.",
        "акад.",
        "доц.",
        "кн.",
        "св.",
    ),
)
