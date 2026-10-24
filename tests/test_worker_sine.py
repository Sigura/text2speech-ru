"""M0 exit criterion: a dummy backend synthesizes a sine "segment" through a worker."""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import pytest
from text2audio_backend_sine.descriptor import SineBackend

from text2audio.core.contracts import Segment
from text2audio.workers import protocol
from text2audio.workers.backend import WorkerBackend
from text2audio.workers.process import (
    WorkerCrashed,
    WorkerEnvMissing,
    WorkerProcess,
    WorkerRequestError,
)


def test_sine_worker_synthesizes_russian_segment(tmp_path: Path, ru_segment: Segment) -> None:
    out = tmp_path / "audio" / "seg.wav"
    with WorkerBackend(SineBackend(), tmp_path / "store") as backend:
        backend.load()
        result = backend.synthesize(ru_segment, out)
    assert result.sample_rate == 24000
    assert result.duration_ms == 60 * len(ru_segment.text)
    with wave.open(str(out)) as w:
        assert w.getframerate() == 24000 and w.getnchannels() == 1
        assert w.getnframes() == 24000 * result.duration_ms // 1000


def test_sine_output_is_byte_identical_on_rerun(tmp_path: Path, ru_segment: Segment) -> None:
    # NFR-03: same input + seed on CPU ⇒ identical bytes.
    outs = [tmp_path / "a.wav", tmp_path / "b.wav"]
    with WorkerBackend(SineBackend(), tmp_path) as backend:
        backend.load()
        for out in outs:
            backend.synthesize(ru_segment, out)
    assert outs[0].read_bytes() == outs[1].read_bytes()


def test_worker_crash_is_restarted_once(
    tmp_path: Path, ru_segment: Segment, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "crashed"
    monkeypatch.setenv("T2A_SINE_CRASH_ONCE", str(marker))
    with WorkerBackend(SineBackend(), tmp_path) as backend:
        backend.load()
        backend.synthesize(ru_segment, tmp_path / "x.wav")
        assert marker.exists() and backend.restarts == 1


def test_worker_crash_beyond_restart_budget_raises(
    tmp_path: Path, ru_segment: Segment, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("T2A_SINE_CRASH_ONCE", str(tmp_path / "crashed"))
    with WorkerBackend(SineBackend(), tmp_path, max_restarts=0) as backend:
        backend.load()
        with pytest.raises(WorkerCrashed, match="exit code 3"):
            backend.synthesize(ru_segment, tmp_path / "x.wav")


def test_wrong_protocol_version_is_refused(tmp_path: Path) -> None:
    proc = WorkerProcess(SineBackend().worker_command(sys.executable))
    proc.start()
    try:
        with pytest.raises(WorkerRequestError, match="protocol mismatch"):
            proc.request({"protocol": 99, "op": "load"})
        assert proc.request(protocol.request("load", device="cpu"))["ok"]
    finally:
        proc.close()


def test_worker_announcing_other_protocol_is_rejected() -> None:
    script = 'print(\'{"protocol": 2, "event": "ready"}\', flush=True)'
    proc = WorkerProcess([sys.executable, "-c", script])
    with pytest.raises(protocol.ProtocolError, match="protocol 2"):
        proc.start()
    proc.close()


def test_missing_worker_environment_is_explained(tmp_path: Path) -> None:
    proc = WorkerProcess([str(tmp_path / "no-venv/bin/python"), "-m", "x"])
    with pytest.raises(WorkerEnvMissing, match="backend environment"):
        proc.start()
