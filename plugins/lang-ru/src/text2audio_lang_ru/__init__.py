"""Russian language pack (SPEC §6.6.5, ADR-0008).

M1 provides text structure only: heading patterns for TXT and PDF input. Stress (RUAccent),
normalization, FB2-specific typography, voices and QA bounds arrive in M2.
"""

from __future__ import annotations

from text2audio.core.contracts import LanguageProfile

_ORDINAL = (
    r"(?:(?:двадцать|тридцать|сорок|пятьдесят|шестьдесят)\s+)?"
    r"(?:перв|втор|трет|четв[её]рт|пят|шест|седьм|восьм|девят|десят|одиннадцат|"
    r"двенадцат|тринадцат|четырнадцат|пятнадцат|шестнадцат|семнадцат|восемнадцат|"
    r"девятнадцат|двадцат|тридцат|сороков|пятидесят|последн)[а-яё]*"
)
_NUMBER = rf"(?:[IVXLCDM]+|\d+|{_ORDINAL})"  # Roman, Arabic or an ordinal: «первая»
_TITLE_TAIL = r"(?:\s*[.:—–-]?\s+\S.*)?"  # "Глава 1. Название", "Глава 1 — Название"

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
    segmenter="ru",
)
