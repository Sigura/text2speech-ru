"""M2 exit criteria (SPEC §11): a full public-domain Russian novel rendered on T0.

«Капитанская дочка» in full is not committed; fetch it once with
`uv run python tests/fixtures/fetch_novel.py`. The run is a real `text2audio run` in a
subprocess, restricted like the T0 reference machine (§9): 4 cores (`taskset`), no GPU
(`CUDA_VISIBLE_DEVICES=""`), 8 GB (a systemd user scope, when available).

    uv run pytest -m "models and slow" tests/e2e/test_novel.py -s
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path

import pytest
from text2audio_asr_gigaam.descriptor import GigaAM
from text2audio_backend_silero.descriptor import SileroBackend
from text2audio_g2p_ruaccent.descriptor import RuAccent

from text2audio.core.envs import plugin_env
from text2audio.models.lock import assets_for
from text2audio.models.store import ModelStore

NOVEL = Path(__file__).parents[1] / "fixtures" / "ru" / "large" / "kapitanskaya-dochka.md"
CLI = Path(sys.executable).parent / "text2audio"
CHAPTERS = 15  # I–XIV and the appendix (the omitted chapter)
pytestmark = [pytest.mark.models, pytest.mark.slow]


def _missing() -> str | None:
    if not NOVEL.is_file():
        return "novel not fetched: uv run python tests/fixtures/fetch_novel.py"
    if shutil.which("ffmpeg") is None or shutil.which("taskset") is None:
        return "needs ffmpeg and taskset"
    store = ModelStore()
    for plugin, owner in ((RuAccent(), "g2p:ruaccent"), (SileroBackend(), "silero")):
        env = plugin_env(plugin)
        if not (env and env.installed) or store.verify(assets_for(owner)):
            return f"{owner}: env or assets missing"
    env = plugin_env(GigaAM())
    if not (env and env.installed) or store.verify(assets_for("asr:gigaam")):
        return "asr:gigaam: env or assets missing"
    return None


def _limits() -> list[str]:
    cmd = ["taskset", "-c", "0-3"]
    scope = ["systemd-run", "--user", "--scope", "--quiet", "-p", "MemoryMax=8G"]
    if shutil.which("systemd-run"):
        probe = subprocess.run([*scope, "true"], capture_output=True, timeout=30)
        if probe.returncode == 0:
            return scope + cmd
    return cmd


@pytest.mark.skipif(_missing() is not None, reason=str(_missing()))
def test_t0(tmp_path: Path) -> None:
    out = tmp_path / "out"
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
    # Stage lines are streamed, so a long QA stage on 4 cores does not look like a hang.
    tail: deque[str] = deque(maxlen=60)
    with subprocess.Popen(
        [*_limits(), str(CLI), "run", str(NOVEL), "-o", str(out)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    ) as proc:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            tail.append(line)
        returncode = proc.wait()
    assert returncode in (0, 4), "".join(tail)
    [book] = [p for p in out.iterdir() if p.is_dir()]
    report = json.loads((book / "report.json").read_text("utf-8"))
    print(json.dumps({k: report[k] for k in ("duration_ms", "wall_s", "rtf_wall")}))
    print(json.dumps(report["timings_s"]), json.dumps(report["synthesis"]))
    print({k: v for k, v in report["qa"].items() if k not in ("segments", "flagged_list")})
    mp3s = sorted(book.glob("*.mp3"))
    assert len(mp3s) >= CHAPTERS  # one MP3 per chapter (long ones may be split)
    assert all(m.with_suffix(".srt").is_file() for m in mp3s)
    assert (book / "report.md").is_file()
    assert report["tier"] == "fast" and report["segments"]["failed"] == 0
    qa = report["qa"]
    assert qa["mode"] == "on" and qa["device"] == "cpu"
    assert qa["checked"] == report["segments"]["total"]
    assert qa["flagged_share"] <= 0.05  # ≤ 5 % flagged (§11)
    assert report["rtf_wall"] <= 0.5  # NFR-05, whole run on 4 cores
