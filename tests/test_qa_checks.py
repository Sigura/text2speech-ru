"""Signal sanity checks of the QA gate (§6.9 step 2)."""

from __future__ import annotations

import numpy as np

from text2audio.qa.checks import check_samples, longest_silence_ms

RATE = 16000
BOUNDS = (6.0, 25.0)
TEXT = "Мой дядя самых честных правил, когда не в шутку занемог"  # 46 letters


def tone(seconds: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def gap(seconds: float) -> np.ndarray:
    return np.zeros(int(RATE * seconds), dtype=np.float32)


def test_good_reading_passes() -> None:
    assert check_samples(tone(3.5), RATE, TEXT, BOUNDS) == []


def test_rate_low_and_high() -> None:
    assert check_samples(tone(12), RATE, TEXT, BOUNDS) == ["rate-low"]
    assert check_samples(tone(1), RATE, TEXT, BOUNDS) == ["rate-high"]


def test_short_text_skips_rate() -> None:
    assert check_samples(tone(5), RATE, "Глава I.", BOUNDS) == []


def test_silent() -> None:
    assert check_samples(gap(2), RATE, TEXT, BOUNDS) == ["silent"]


def test_clipping() -> None:
    assert "clipping" in check_samples(np.clip(tone(3.5, 2.0), -1, 1), RATE, TEXT, BOUNDS)


def test_internal_silence() -> None:
    long_gap = np.concatenate([tone(1.5), gap(2.5), tone(1.5)])
    short_gap = np.concatenate([tone(1.5), gap(1.5), tone(1.5)])
    assert "long-silence" in check_samples(long_gap, RATE, TEXT, (1.0, 25.0))
    assert check_samples(short_gap, RATE, TEXT, (1.0, 25.0)) == []
    assert 2400 <= longest_silence_ms(long_gap, RATE) <= 2500
