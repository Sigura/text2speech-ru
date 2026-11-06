"""M2 exit criteria (SPEC §11): a public-domain Russian novel rendered on T0 and T1.

«Капитанская дочка» in full is not committed; fetch it once with
`uv run python tests/fixtures/fetch_novel.py`. Tests synthesize at most 100 segments
(owner, SPEC §13.1), so the run takes every chapter heading with its first paragraphs.
Each run is a real `text2audio run` in a subprocess: T0 = no GPU
(`CUDA_VISIBLE_DEVICES=""`), every core and no memory limit (owner, SPEC §13.1); T1 =
the GPU with the most free memory, 8 cores near it (`taskset`), 16 GB. Memory limits use a
systemd user scope, when available.

    uv run pytest -m "models and slow" tests/e2e/test_novel.py -s [-k t1]
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Any

import pytest
from text2audio_asr_gigaam.descriptor import GigaAM
from text2audio_backend_chatterbox.descriptor import ChatterboxBackend
from text2audio_backend_silero.descriptor import SileroBackend
from text2audio_g2p_ruaccent.descriptor import RuAccent

from text2audio.core import resources as res
from text2audio.core.envs import plugin_env
from text2audio.models.lock import assets_for
from text2audio.models.store import ModelStore

NOVEL = Path(__file__).parents[1] / "fixtures" / "ru" / "large" / "kapitanskaya-dochka.md"
CLI = Path(sys.executable).parent / "text2audio"
CHAPTERS = 15  # I–XIV and the appendix (the omitted chapter)
PARAGRAPHS = 4  # per chapter: 93 segments
SEGMENTS = 100
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


def _gpu() -> res.Gpu | None:
    need = ChatterboxBackend().capabilities.vram_mb + res.GPU_HEADROOM_MB
    gpus = [g for g in res.probe().gpus if g.free_mb >= need]
    return max(gpus, key=lambda g: g.free_mb) if gpus else None


def _missing_t1() -> str | None:
    missing = _missing()
    if missing:
        return missing
    env = plugin_env(ChatterboxBackend())
    if not (env and env.installed) or ModelStore().verify(assets_for("chatterbox")):
        return "chatterbox: env or assets missing"
    return None if _gpu() else "no GPU with enough free VRAM for Chatterbox"


def _limits(cpus: str, memory: str) -> list[str]:
    cmd = ["taskset", "-c", cpus]
    scope = ["systemd-run", "--user", "--scope", "--quiet", "-p", f"MemoryMax={memory}"]
    if shutil.which("systemd-run"):
        probe = subprocess.run([*scope, "true"], capture_output=True, timeout=30)
        if probe.returncode == 0:
            return scope + cmd
    return cmd


def _excerpt(novel: Path, folder: Path, paragraphs: int) -> Path:
    kept: list[str] = []
    count = 0
    for block in novel.read_text("utf-8").split("\n\n"):
        if block.startswith("#"):
            kept.append(block)
            if block.startswith(("# ", "## ")):
                count = 0
        elif count < paragraphs:
            kept.append(block)
            count += 1
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / novel.name
    path.write_text("\n\n".join(kept), "utf-8")
    return path


def _run(
    limits: list[str],
    env: dict[str, str],
    out: Path,
    novel: Path = NOVEL,
    chapters: int = CHAPTERS,
    paragraphs: int = PARAGRAPHS,
    tier: str | None = None,
) -> dict[str, Any]:
    novel = _excerpt(novel, out.parent / "excerpt", paragraphs)
    # Excerpt chapters last about a minute; the split plan would merge them into one file.
    config = out.parent / "config" / "text2audio" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[output]\nmin_part_minutes = 0\n", "utf-8")
    env = {**env, "XDG_CONFIG_HOME": str(config.parents[1])}
    # Stage lines are streamed, so a long stage does not look like a hang.
    tail: deque[str] = deque(maxlen=60)
    with subprocess.Popen(
        [*limits, str(CLI), "run", str(novel), "-o", str(out), *(["--tier", tier] if tier else [])],
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
    report: dict[str, Any] = json.loads((book / "report.json").read_text("utf-8"))
    print(json.dumps({k: report[k] for k in ("duration_ms", "wall_s", "rtf_wall")}))
    print(json.dumps(report["timings_s"]), json.dumps(report["synthesis"]))
    print(json.dumps(report.get("polish")))
    print({k: v for k, v in report["qa"].items() if k not in ("segments", "flagged_list")})
    parts = sorted(book.glob("*.mp3"))
    assert len(parts) >= chapters  # one MP3 per chapter (long ones may be split)
    assert all(m.with_suffix(".srt").is_file() for m in parts)
    assert (book / "report.md").is_file()
    assert report["segments"]["total"] <= SEGMENTS
    assert report["segments"]["failed"] == 0
    qa = report["qa"]
    assert qa["mode"] == "on"
    assert qa["checked"] == min(100, report["segments"]["total"])  # QA limit (§6.9)
    assert qa["flagged_share"] <= 0.05  # ≤ 5 % of the checked sample flagged (§11)
    assert report["rtf_wall"] <= 0.5  # NFR-05, whole run
    return report


@pytest.mark.skipif(_missing() is not None, reason=str(_missing()))
def test_t0(tmp_path: Path) -> None:
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
    report = _run([], env, tmp_path / "out")
    assert report["tier"] == "fast" and report["qa"]["device"] == "cpu"


@pytest.mark.gpu
@pytest.mark.skipif(_missing_t1() is not None, reason=str(_missing_t1()))
def test_t1(tmp_path: Path) -> None:
    gpu = _gpu()
    assert gpu is not None
    near = sorted(gpu.cpu_affinity or range(os.cpu_count() or 8))[:8]
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": gpu.uuid}
    limits = _limits(",".join(map(str, near)), "16G")
    report = _run(limits, env, tmp_path / "out", tier="expressive")  # fast is the default
    assert report["tier"] == "expressive"
    assert report["route"] == {"backend": "silero", "polish": "chatterbox", "foreign": "chatterbox"}
    assert report["polish"]["workers"] >= 1
    assert report["polish"]["device"].startswith("cuda")
