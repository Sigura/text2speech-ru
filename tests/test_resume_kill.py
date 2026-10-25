"""FR-10 with a real kill: `text2audio synthesize` is SIGKILLed mid-chapter (workers
included), restarted, and no journaled segment is rendered or checked again."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from text2audio import api
from text2audio.core.journal import Journal

FIXTURE = Path(__file__).parent / "fixtures" / "ru" / "kapitanskaya-dochka.fb2"
CLI = Path(sys.executable).parent / "text2audio"
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


@pytest.fixture
def book(tmp_path: Path) -> Path:
    [result] = api.convert([FIXTURE], tmp_path / "out")
    assert result.folder is not None
    (result.folder / "_work" / "text2audio.toml").write_text(
        '[synthesis]\ntier = "fast"\n[resources]\nmax_workers = 1\n'
        '[lang.ru]\ng2p = "backend"\nasr = "sine-echo"\n[lang.ru.backend]\nfast = "sine"\n',
        "utf-8",
    )
    return result.folder


def start(book: Path, tmp_path: Path, **env: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [str(CLI), "synthesize", str(book)],
        env={**os.environ, "T2A_MODEL_HOME": str(tmp_path / "store"), **env},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # its own process group: the kill takes the workers too
    )


def kill_when(proc: subprocess.Popen[bytes], ready: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 60
    while not ready():
        assert proc.poll() is None, "finished before it could be killed"
        assert time.monotonic() < deadline, "timed out waiting"
        time.sleep(0.05)
    os.killpg(proc.pid, signal.SIGKILL)
    proc.wait()


def journal(book: Path) -> Journal:
    return Journal(book / "_work" / "render.jsonl")


def logged(path: Path) -> list[str]:
    return (
        [line.split()[0] for line in path.read_text("utf-8").splitlines()] if path.exists() else []
    )


def test_kill_during_synthesis(book: Path, tmp_path: Path) -> None:
    first_log, second_log = tmp_path / "first.log", tmp_path / "second.log"
    proc = start(book, tmp_path, T2A_SINE_DELAY_MS="300", T2A_SINE_LOG=str(first_log))
    kill_when(proc, lambda: len(journal(book).entries) >= 5)
    done = set(journal(book).entries)
    assert not list(book.glob("*.mp3"))
    proc = start(book, tmp_path, T2A_SINE_LOG=str(second_log))
    assert proc.wait(timeout=120) == 0
    again = set(logged(second_log))
    assert again and not (again & done)  # no journaled segment rendered again
    report = json.loads((book / "report.json").read_text("utf-8"))
    seg = report["segments"]
    assert seg["rendered_now"] == seg["total"] - seg["duplicate"] - len(done)
    assert list(book.glob("*.mp3"))


def test_kill_during_qa(book: Path, tmp_path: Path) -> None:
    proc = start(book, tmp_path, T2A_ECHO_DELAY_MS="300")
    kill_when(proc, lambda: sum("qa" in e for e in journal(book).entries.values()) >= 3)
    checked = sum("qa" in e for e in journal(book).entries.values())
    log = tmp_path / "second.log"
    proc = start(book, tmp_path, T2A_SINE_LOG=str(log))
    assert proc.wait(timeout=120) == 0
    report = json.loads((book / "report.json").read_text("utf-8"))
    assert report["segments"]["rendered_now"] == 0 and not log.exists()
    assert report["qa"]["checked_now"] == report["qa"]["checked"] - checked
