"""Signal sanity checks of one segment's audio (SPEC §6.9 step 2).

Cheap numpy checks that catch what ASR alone may miss: speech too fast or too slow for
its text (truncated or stretched audio), clipping, and long silences inside a segment.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from text2audio.audio.wav import read_mono, trim

MIN_RATE_LETTERS = 15  # headings like «Глава I.» are too short for a rate estimate
CLIP_LEVEL = 0.999
CLIP_SHARE = 0.001  # more than 0.1 % of samples at full scale
SILENCE_DB = -50.0
SILENCE_FRAME_MS = 20
MAX_SILENCE_MS = 2000


def letters(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def check_samples(
    samples: np.ndarray, rate: int, text: str, chars_per_sec: tuple[float, float]
) -> list[str]:
    """Flag names for audio that cannot be a good reading of `text`."""
    flags: list[str] = []
    speech = trim(samples, rate)
    if speech.size == 0:
        return ["silent"]
    n = letters(text)
    if n >= MIN_RATE_LETTERS:
        cps = n / (speech.size / rate)
        if cps < chars_per_sec[0]:
            flags.append("rate-low")
        elif cps > chars_per_sec[1]:
            flags.append("rate-high")
    if np.count_nonzero(np.abs(speech) >= CLIP_LEVEL) > CLIP_SHARE * speech.size:
        flags.append("clipping")
    if longest_silence_ms(speech, rate) > MAX_SILENCE_MS:
        flags.append("long-silence")
    return flags


def longest_silence_ms(samples: np.ndarray, rate: int) -> int:
    frame = max(1, rate * SILENCE_FRAME_MS // 1000)
    count = samples.size // frame
    if count == 0:
        return 0
    frames = samples[: count * frame].reshape(count, frame)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    quiet = rms < 10 ** (SILENCE_DB / 20)
    longest = run = 0
    for q in quiet:
        run = run + 1 if q else 0
        longest = max(longest, run)
    return longest * SILENCE_FRAME_MS


def check_file(path: Path, text: str, chars_per_sec: tuple[float, float]) -> list[str]:
    samples, rate = read_mono(path)
    return check_samples(samples, rate, text, chars_per_sec)
