"""WER of the QA gate (§6.9): folding and word-level edit counts."""

from __future__ import annotations

import pytest

from text2audio.qa.wer import cer, drop_unheard, fold, wer

RU = {"ё": "е"}


def test_fold_case_punctuation_stress_yo_hyphens() -> None:
    assert fold("Вс+ё б́ыло — «хорош+о»!", RU) == ["все", "было", "хорошо"]
    assert fold("ноль-ноль") == ["ноль", "ноль"]


def test_identical_after_fold_is_zero() -> None:
    assert wer("Ёжик пошёл домой.", "ежик пошел домой", RU).wer == 0.0


@pytest.mark.parametrize(
    ("hyp", "sub", "dele", "ins"),
    [
        ("раз два пять четыре", 1, 0, 0),
        ("раз два четыре", 0, 1, 0),
        ("раз два три три четыре", 0, 0, 1),
    ],
)
def test_counts(hyp: str, sub: int, dele: int, ins: int) -> None:
    r = wer("раз два три четыре", hyp)
    assert (r.substitutions, r.deletions, r.insertions) == (sub, dele, ins)
    assert r.wer == pytest.approx(0.25)
    assert r.words == 4


def test_nothing_heard_is_one() -> None:
    assert wer("раз два", "").wer == 1.0


def test_empty_reference() -> None:
    assert wer("", "").wer == 0.0
    assert wer("—", "шум").wer == 1.0


def test_cer_ignores_spacing_and_small_spelling_differences() -> None:
    assert cer("Дизиэт Сма вошла в комнату", "дизиет сма вошла в комнату", RU) < 0.1
    assert cer("Госпожа Дизиэт", "госпожадизиет", RU) < 0.1
    assert cer("Ёжик пошёл", "ежик пошел", RU) == 0.0


def test_unheard_readings_are_dropped_not_scored() -> None:
    ref = "Протокол эйчтитип+и работает поверх тисип+и."
    checked, dropped = drop_unheard(ref, "протокол работает поверх", ["эйчтитип+и", "тисип+и"])
    assert dropped == fold("эйчтитипи тисипи")
    assert wer(checked, "протокол работает поверх").wer == 0.0


def test_heard_or_other_words_still_scored() -> None:
    optional = ["эйчтитипи"]
    checked, dropped = drop_unheard("протокол эйчтитипи", "протокол эйчтитипи", optional)
    assert (checked, dropped) == (" ".join(fold("протокол эйчтитипи")), [])
    checked, dropped = drop_unheard("протокол эйчтитипи", "протокол эйч ти", optional)
    assert dropped == fold("эйчтитипи")
    checked, dropped = drop_unheard("протокол работает", "протокол", optional)
    assert (checked, dropped) == ("протокол работает", [])
    assert wer(checked, "протокол").wer == 0.5


def test_cer_high_for_wrong_or_missing_words() -> None:
    assert cer("Отец мой служил в полку", "шкаф луна дорога") > 0.5
    assert cer("раз два", "") == 1.0
    assert cer("", "") == 0.0
