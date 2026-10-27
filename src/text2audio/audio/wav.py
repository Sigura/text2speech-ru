"""Segment WAVs → part WAVs: edge trimming, resampling, generated pauses (SPEC §6.8).

Parts are streamed to disk segment by segment, so a 90-minute part never sits in
memory (NFR-06).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import TracebackType

import numpy as np
import soundfile as sf
import soxr

from text2audio.audio.encode import FfmpegError

ASSEMBLY_RATE = 44100  # the final encode rate (§6.8)
TRIM_DB = -45.0  # edge trimming threshold relative to full scale
KEEP_MS = 40  # kept around speech when trimming
LEAD_IN_MS = 1500  # silence at the start of every file: players start late and clip the speech


def read_mono(path: Path) -> tuple[np.ndarray, int]:
    data, rate = sf.read(str(path), dtype="float32", always_2d=True)
    return data.mean(axis=1), int(rate)


def trim(samples: np.ndarray, rate: int, db: float = TRIM_DB, keep_ms: int = KEEP_MS) -> np.ndarray:
    """Remove leading and trailing silence (pauses are generated, never trimmed)."""
    if samples.size == 0:
        return samples
    loud = np.flatnonzero(np.abs(samples) > 10 ** (db / 20))
    if loud.size == 0:
        return samples[:0]
    keep = rate * keep_ms // 1000
    return samples[max(0, loud[0] - keep) : min(samples.size, loud[-1] + keep + 1)]


def resample(samples: np.ndarray, rate: int, target: int = ASSEMBLY_RATE) -> np.ndarray:
    if rate == target or samples.size == 0:
        return samples
    return np.asarray(soxr.resample(samples, rate, target, quality="HQ"), dtype=np.float32)


def atempo(samples: np.ndarray, rate: int, factor: float, ffmpeg: str) -> np.ndarray:
    """Speech `factor` times faster (< 1: slower) at the same pitch: FFmpeg `atempo`."""
    if factor == 1 or samples.size == 0:
        return samples
    cmd = [ffmpeg, "-v", "error", "-f", "f32le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0"]
    cmd += ["-af", f"atempo={factor}", "-f", "f32le", "pipe:1"]
    proc = subprocess.run(cmd, input=samples.astype("<f4").tobytes(), capture_output=True)
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace")[-1500:]
        raise FfmpegError(f"ffmpeg atempo failed ({proc.returncode}): {err}")
    return np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32)


def speech(path: Path, target: int = ASSEMBLY_RATE) -> np.ndarray:
    """A segment's audio, trimmed and at the assembly rate."""
    samples, rate = read_mono(path)
    return resample(trim(samples, rate), rate, target)


class PartWriter:
    """Writes one output part as 16-bit mono PCM, tracking its duration."""

    def __init__(self, path: Path, rate: int = ASSEMBLY_RATE, lead_in_ms: int = LEAD_IN_MS):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.rate = rate
        self.frames = 0
        self._file = sf.SoundFile(str(path), "w", samplerate=rate, channels=1, subtype="PCM_16")
        self.silence(lead_in_ms)

    @property
    def duration_ms(self) -> int:
        return self.frames * 1000 // self.rate

    def add(self, samples: np.ndarray) -> None:
        self._file.write(np.clip(samples, -1.0, 1.0))
        self.frames += samples.size

    def silence(self, ms: int) -> None:
        if ms > 0:
            self.add(np.zeros(self.rate * ms // 1000, dtype=np.float32))

    def close(self) -> None:
        self._file.close()

    def __enter__(self) -> PartWriter:
        return self

    def __exit__(
        self, t: type[BaseException] | None, e: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()
