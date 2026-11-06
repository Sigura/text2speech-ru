"""FR-11: the English normalization test set passes at ≥ 95 % exact match."""

from __future__ import annotations

from pathlib import Path

from text2audio_lang_en import PROFILE
from text2audio_lang_en.normalize import Normalizer as PackNormalizer

from text2audio.core.contracts import LanguageProfile, Normalizer, Transliterator

CASES = [
    line.split("\t")
    for line in (Path(__file__).parent / "normalization.tsv").read_text("utf-8").splitlines()
    if line.strip() and not line.startswith("#")
]


def _run(case: list[str]) -> str:
    role = case[2] if len(case) > 2 else "narration"
    return PackNormalizer().normalize(case[0], role).text


def test_profile_and_hooks() -> None:
    assert isinstance(PROFILE, LanguageProfile) and PROFILE.code == "en"
    assert isinstance(PackNormalizer(), Normalizer)
    assert isinstance(PackNormalizer(), Transliterator)  # FR-24 hook
    assert Path(PROFILE.lexicon or "").is_file()


def test_owner_choices() -> None:
    assert PROFILE.default_tier == "fast"
    assert PROFILE.voices["narrator"]["kokoro"] == "af_nicole"
    assert PROFILE.voices["dialogue"]["kokoro"] == "am_michael"


def test_set_is_large_enough() -> None:
    assert len(CASES) >= 300


def test_flags_what_it_cannot_read() -> None:
    assert PackNormalizer().normalize("He has an MP3.").uncertain == ["MP3"]
    assert PackNormalizer().normalize("I have 5 cats.").uncertain == []


def test_exact_match_rate() -> None:
    misses = [(c[0], c[1], got) for c in CASES if (got := _run(c)) != c[1]]
    rate = 1 - len(misses) / len(CASES)
    report = "\n".join(f"{src!r}: expected {exp!r}, got {got!r}" for src, exp, got in misses)
    assert rate >= 0.95, f"{rate:.1%} exact match ({len(misses)} misses):\n{report}"
