"""QA gate (§6.9, FR-12) with injected faults: sine backend + `sine-echo` ASR.

The sine worker writes what an ASR would "hear" next to each WAV; `T2A_SINE_FAULT`
makes it render garbage, silence or truncated audio, so detection, retries, keep-best
and flagging are tested without speech models.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from text2audio import api
from text2audio.cli import app
from text2audio.core.config import load_config, parse_qa_mode
from text2audio.core.journal import Journal
from text2audio.qa.gate import sampled

FIXTURE = Path(__file__).parent / "fixtures" / "ru" / "kapitanskaya-dochka.fb2"
TARGET = "ch01-s0004"  # a long narration segment
pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
LANG = {"ru": {"backend": {"fast": "sine"}, "g2p": "backend", "asr": "sine-echo"}}


def config(qa: str = "on") -> Any:
    return load_config([], {"lang": LANG, "synthesis": {"tier": "fast"}, "qa": {"mode": qa}})


@pytest.fixture
def book(tmp_path: Path) -> Path:
    [result] = api.convert([FIXTURE], tmp_path / "out")
    assert result.folder is not None
    return result.folder


def run(book: Path, tmp_path: Path, qa: str = "on") -> api.SynthesisReport:
    return api.synthesize(book, config=config(qa), store=tmp_path / "store")


def fault(monkeypatch: pytest.MonkeyPatch, spec: str, ids: str = TARGET) -> None:
    monkeypatch.setenv("T2A_SINE_FAULT", spec)
    monkeypatch.setenv("T2A_SINE_FAULT_IDS", ids)


def flagged(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["segment"]: f for f in report["qa"]["flagged_list"]}


@pytest.mark.parametrize(
    ("mode", "flag"),
    [("garbage", "wer"), ("silence", "silent"), ("truncate", "rate-high")],
)
def test_fault_detected_retried_and_flagged(
    book: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, flag: str
) -> None:
    fault(monkeypatch, mode)
    result = run(book, tmp_path)
    qa = result.report["qa"]
    assert list(flagged(result.report)) == [TARGET]
    item = flagged(result.report)[TARGET]
    assert flag in item["flags"] and item["attempts"] == 3
    assert qa["flagged"] == 1 and qa["retried"] == 1
    assert qa["flagged_share"] == pytest.approx(1 / qa["checked"], abs=1e-4)
    assert result.flagged == 1
    seg = next(
        json.loads(x)
        for x in (book / "_work/segments.jsonl").read_text("utf-8").splitlines()
        if json.loads(x)["id"] == TARGET
    )
    assert seg["status"] == "flagged" and seg["attempts"] == 3 and flag in seg["flags"]
    assert TARGET in (book / "report.md").read_text("utf-8")


def test_retry_fixes_segment_and_keeps_best_audio(
    book: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fault(monkeypatch, "truncate:2")
    result = run(book, tmp_path)
    assert result.report["qa"]["flagged"] == 0 and result.report["qa"]["retried"] == 1
    row = next(r for r in result.report["qa"]["segments"] if r["segment"] == TARGET)
    assert row["attempts"] == 2 and row["flags"] == []
    audio = book / "_work" / "audio" / "segments"
    assert not list(audio.glob("*.a2.wav")) and not list(audio.glob("*.a3.wav"))
    journal = Journal(book / "_work" / "render.jsonl")
    entry = next(e for e in journal.entries.values() if e["segment"] == TARGET)
    assert entry["qa"]["attempt"] == 2
    assert entry["duration_ms"] > 3000  # the full-length attempt, not the truncated one


def test_final_reply_stops_retries(
    book: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fault(monkeypatch, "garbage")
    monkeypatch.setenv("T2A_SINE_FINAL", "1")
    result = run(book, tmp_path)
    assert flagged(result.report)[TARGET]["attempts"] == 2


def test_rerun_keeps_flags_without_rechecking(
    book: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fault(monkeypatch, "garbage")
    run(book, tmp_path)
    log = tmp_path / "sine.log"
    monkeypatch.setenv("T2A_SINE_LOG", str(log))
    second = run(book, tmp_path)
    assert second.report["qa"]["checked_now"] == 0 and not log.exists()
    assert list(flagged(second.report)) == [TARGET]


def test_qa_off(book: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fault(monkeypatch, "garbage")
    result = run(book, tmp_path, qa="off")
    assert result.report["qa"]["checked"] == 0 and result.flagged == 0


def test_sample_checks_a_stable_subset(book: Path, tmp_path: Path) -> None:
    result = run(book, tmp_path, qa="sample=50%")
    segs = [json.loads(x) for x in (book / "_work/segments.jsonl").read_text("utf-8").splitlines()]
    expected = {s["id"] for s in segs if sampled(s["hash"], 50)}
    assert {r["segment"] for r in result.report["qa"]["segments"]} == expected
    assert 0 < len(expected) < len(segs)


def test_qa_mode_values() -> None:
    assert [parse_qa_mode(v) for v in ("on", "off", "sample=10%", "SAMPLE=100%")] == [
        100,
        0,
        10,
        100,
    ]
    for bad in ("sample=0%", "sample=101%", "half", "sample=10"):
        with pytest.raises(ValueError):
            parse_qa_mode(bad)


def _book_config(book: Path) -> None:
    (book / "_work" / "text2audio.toml").write_text(
        '[synthesis]\ntier = "fast"\n[lang.ru]\ng2p = "backend"\nasr = "sine-echo"\n'
        '[lang.ru.backend]\nfast = "sine"\n',
        "utf-8",
    )


def test_cli_exit_code_4_when_flags_remain(
    book: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _book_config(book)
    monkeypatch.setenv("T2A_MODEL_HOME", str(tmp_path / "store"))
    fault(monkeypatch, "silence")
    result = CliRunner().invoke(app, ["synthesize", str(book)])
    assert result.exit_code == 4, result.output
    clean = CliRunner().invoke(app, ["synthesize", str(book), "--qa", "off"])
    assert clean.exit_code == 0, clean.output  # flags are not reported with QA off
    bad = CliRunner().invoke(app, ["synthesize", str(book), "--qa", "maybe"])
    assert bad.exit_code == 1
