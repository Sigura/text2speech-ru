"""FR-11: the Russian normalization test set passes at ≥ 95 % exact match."""

from __future__ import annotations

from pathlib import Path

import pytest
from text2audio_lang_ru.normalize import RussianNormalizer

CASES = [
    line.split("\t")
    for line in (Path(__file__).parent / "normalization.tsv").read_text("utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]


def _run(case: list[str]) -> str:
    role = case[2] if len(case) > 2 else "narration"
    return RussianNormalizer().normalize(case[0], role).text


def test_set_is_large_enough() -> None:
    assert len(CASES) >= 300


def test_exact_match_rate() -> None:
    misses = [(c[0], c[1], got) for c in CASES if (got := _run(c)) != c[1]]
    rate = 1 - len(misses) / len(CASES)
    report = "\n".join(f"{src!r}: expected {exp!r}, got {got!r}" for src, exp, got in misses)
    assert rate >= 0.95, f"{rate:.1%} exact match ({len(misses)} misses):\n{report}"


@pytest.mark.parametrize(
    "text",
    ["В 1812 году", "Пётр I", "XIX век", "15:30", "15%", "500 ₽", "12.06.2025", "5–7 лет"],
)
def test_no_digits_or_roman_numerals_reach_silero(text: str) -> None:
    out = RussianNormalizer().normalize(text).text
    assert not any(ch.isdigit() for ch in out), out
    assert not any(ch in "IVX%₽" for ch in out), out
