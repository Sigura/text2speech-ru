"""Terminal progress for `run` / `synthesize` (FR-17): stage lines, live notes,
interrupt hint."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from text2audio.cli import INTERRUPTED, app
from text2audio.console import StageProgress, duration

runner = CliRunner()


def ui() -> tuple[StageProgress, io.StringIO]:
    out = io.StringIO()
    return StageProgress(Console(file=out, width=200)), out


def test_duration() -> None:
    assert [duration(s) for s in (0.84, 41, 372, 15480)] == ["0.8s", "41s", "6m 12s", "4h 18m"]


def test_stage_lines_without_a_terminal() -> None:
    progress, out = ui()
    assert not progress.live
    with progress:
        progress.book("Капитанская дочка", Path("out/kd"))
        progress.detail("plan", lang="ru", tier="fast", backend="silero", voice="xenia")
        progress.detail("synthesize", segments=10, pending=4)
        progress.detail("synthesize", workers=2)
        progress("synthesize", 0, 4)
        for n in range(1, 5):
            progress.detail("synthesize", audio_ms=n * 60_000)
            progress("synthesize", n, 4)
        progress.detail("assemble", parts=1)
        progress("assemble", 0, 1)
        progress("assemble", 1, 1)
    text = out.getvalue()
    assert "Капитанская дочка" in text
    assert "ru · fast tier · silero · voice xenia" in text
    assert "✓ Synthesize  4 rendered · 6 cached · 4m 00s audio · 2 workers" in text
    assert "✓ Encode      1 file · unchanged" in text
    assert "━" not in text  # no bars in logs


def test_cached_stage_and_failures_are_reported() -> None:
    progress, out = ui()
    progress.detail("stress", texts=7, cached=7, component="ruaccent")
    progress.detail("synthesize", failed="c1.p2.s1", error="worker died")
    text = out.getvalue()
    assert "✓ Stress      7 texts, all cached" in text
    assert "! segment c1.p2.s1 failed: worker died" in text


def test_live_note_while_workers_load() -> None:
    progress, _ = ui()
    progress.detail("synthesize", workers=8)
    progress("synthesize", 0, 100)
    task = progress.bars.tasks[0]
    assert task.fields["note"] == "loading voices (8 workers)…"


def test_ctrl_c_explains_how_to_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from text2audio import api

    def interrupted(*args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(api, "synthesize", interrupted)
    result = runner.invoke(app, ["synthesize", str(tmp_path)])
    assert result.exit_code == INTERRUPTED
    assert "run the same command again to resume" in result.output
