"""Russian components with real models, offline (§6.6.5, spike `ru-smoke`).

Marked `models`: skipped unless the pinned assets (`text2audio models pull --lang ru`)
and the plugin environments (`text2audio envs sync`) are installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import soundfile as sf
from text2audio_backend_silero.descriptor import SileroBackend
from text2audio_g2p_ruaccent.descriptor import RuAccent

from text2audio.core.contracts import Segment
from text2audio.core.envs import plugin_env
from text2audio.models.lock import assets_for
from text2audio.models.store import ModelStore
from text2audio.workers.backend import Worker, WorkerBackend

pytestmark = pytest.mark.models
STORE = ModelStore()


def ready(plugin: object, owner: str) -> bool:
    env = plugin_env(plugin)
    assets = assets_for(owner)
    return bool(env and env.installed and assets and not STORE.verify(assets))


needs_ruaccent = pytest.mark.skipif(
    not ready(RuAccent(), "g2p:ruaccent"), reason="RUAccent env or assets missing"
)
needs_silero = pytest.mark.skipif(
    not ready(SileroBackend(), "silero"), reason="Silero env or assets missing"
)


@needs_ruaccent
def test_ruaccent_stress_and_yo_offline() -> None:
    # The worker runs with T2A_NO_NETWORK=1: any download attempt would fail it.
    with Worker(RuAccent(), STORE.root) as w:
        w.load(assets=STORE.payload(assets_for("g2p:ruaccent")))
        out = w.call("process", texts=["Все было хорошо.", "Ежик пошел домой.", ""])["texts"]
    assert out[0] == "Вс+ё б+ыло хорош+о."
    assert "ё" in out[1].lower() and "+" in out[1]
    assert out[2] == ""


@needs_silero
def test_silero_xenia_48k_offline(tmp_path: Path) -> None:
    seg = Segment(
        id="s",
        text="В 1812 году.",
        text_norm="В тысяча восемьсот двенадцатом году.",
        text_stress="В т+ысяча восемьс+от двен+адцатом год+у.",
        lang="ru",
    )
    out = tmp_path / "s.wav"
    with WorkerBackend(SileroBackend(), STORE.root) as b:
        b.load(assets=STORE.payload(assets_for("silero")))
        result = b.synthesize(seg, out)
    audio, rate = sf.read(str(out))
    assert rate == 48000 and result.sample_rate == 48000
    assert 1500 < result.duration_ms < 6000
    assert abs(audio).max() > 0.05  # not silent
