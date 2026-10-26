"""Russian components with real models, offline (§6.6.5, spike `ru-smoke`).

Marked `models`: skipped unless the pinned assets (`text2audio models pull --lang ru`)
and the plugin environments (`text2audio envs sync`) are installed.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import soundfile as sf
from text2audio_asr_gigaam.descriptor import GigaAM
from text2audio_backend_chatterbox.descriptor import ChatterboxBackend
from text2audio_backend_silero.descriptor import SileroBackend
from text2audio_g2p_ruaccent.descriptor import RuAccent
from text2audio_lang_ru import PROFILE

from text2audio import api
from text2audio.core import resources as res
from text2audio.core.config import load_config
from text2audio.core.contracts import Segment
from text2audio.core.envs import plugin_env
from text2audio.models.lock import assets_for
from text2audio.models.store import ModelStore
from text2audio.qa.wer import wer
from text2audio.workers.backend import Worker, WorkerBackend

pytestmark = pytest.mark.models
STORE = ModelStore()
FIXTURES = Path(__file__).parent / "fixtures"


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
needs_gigaam = pytest.mark.skipif(
    not ready(GigaAM(), "asr:gigaam"), reason="GigaAM env or assets missing"
)
CHATTERBOX = ChatterboxBackend()
needs_chatterbox = pytest.mark.skipif(
    not ready(CHATTERBOX, "chatterbox"), reason="Chatterbox env or assets missing"
)
needs_gpu = pytest.mark.skipif(
    res.plan(res.probe(), CHATTERBOX.capabilities).tier != "expressive",
    reason="no GPU with enough free VRAM for Chatterbox",
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


NUMBERS = Segment(
    id="n",
    text="В 1812 году было 25 градусов мороза.",
    text_norm="В тысяча восемьсот двенадцатом году было двадцать пять градусов мороза.",
    text_stress="В т+ысяча восемьс+от двен+адцатом год+у б+ыло дв+адцать п+ять гр+адусов мор+оза.",
    lang="ru",
)


def _silero(seg: Segment, out: Path, attempt: int = 1) -> dict[str, object]:
    with WorkerBackend(SileroBackend(), STORE.root) as b:
        b.load(assets=STORE.payload(assets_for("silero")))
        return b.call(
            "synthesize",
            segment=seg.model_dump(mode="json"),
            out=str(out),
            voice_id="xenia",
            attempt=attempt,
        )


@needs_silero
def test_silero_retry_reads_with_own_stress(tmp_path: Path) -> None:
    first = _silero(NUMBERS, tmp_path / "a1.wav")
    retry = _silero(NUMBERS, tmp_path / "a2.wav", attempt=2)
    assert "final" not in first and retry["final"] is True
    audio, _ = sf.read(str(tmp_path / "a2.wav"))
    assert abs(audio).max() > 0.05


@needs_silero
@needs_gigaam
def test_gigaam_hears_silero_numbers_in_spoken_form(tmp_path: Path) -> None:
    # QA ASR (§6.9): GigaAM `rnnt` writes numbers as words, so WER against the
    # normalized text is 0; runs on CPU with the network guard on.
    out = tmp_path / "n.wav"
    _silero(NUMBERS, out)
    with Worker(GigaAM(), STORE.root) as w:
        w.load(assets=STORE.payload(assets_for("asr:gigaam")))
        [heard] = w.call("transcribe", paths=[str(out)])["texts"]
    assert wer(NUMBERS.speech_text, heard, {"ё": "е"}).wer == 0.0, heard


@needs_ruaccent
@needs_silero
@needs_gigaam
@pytest.mark.skipif(
    shutil.which("espeak-ng") is None or shutil.which("ffmpeg") is None,
    reason="needs espeak-ng and ffmpeg",
)
def test_codeswitch_loses_no_word_on_t0(tmp_path: Path) -> None:
    # FR-24 acceptance on T0: Latin words are read (lexicon, acronyms, look-alike
    # letters, transliteration) and every segment passes the QA gate against the
    # resolved text. Not WER 0: in a sentence GigaAM leaves English-sounding words
    # blank even when Silero says them (its vocabulary is 33 Cyrillic letters, §13.1).
    [converted] = api.convert([FIXTURES / "ru" / "codeswitch.md"], tmp_path / "out")
    assert converted.folder is not None
    cfg = load_config([], {"synthesis": {"tier": "fast"}})
    report = api.synthesize(converted.folder, config=cfg, store=STORE.root).report
    segs = [
        json.loads(x)
        for x in (converted.folder / "_work/segments.jsonl").read_text("utf-8").splitlines()
    ]
    assert report["foreign_spans"] and all(
        s["resolution"] != "unresolved" for s in report["foreign_spans"]
    )
    flagged = {s["text_norm"]: s["flags"] for s in segs if s["status"] != "done"}
    assert not flagged, flagged
    assert report["qa"]["flagged"] == 0


def _hear(paths: list[Path]) -> list[str]:
    with Worker(GigaAM(), STORE.root) as w:
        w.load(assets=STORE.payload(assets_for("asr:gigaam")))
        return [str(t) for t in w.call("transcribe", paths=[str(p) for p in paths])["texts"]]


@pytest.mark.gpu
@needs_chatterbox
@needs_silero
@needs_gigaam
@needs_gpu
def test_chatterbox_tts_and_vc_offline_pass_qa(tmp_path: Path) -> None:
    # One worker, the network guard on: TTS from stressed text and VC of Silero audio
    # into the narrator reference both read every word (§6.6.6).
    source, tts, vc = (tmp_path / f"{name}.wav" for name in ("source", "tts", "vc"))
    _silero(NUMBERS, source)
    slot = res.plan(res.probe(), CHATTERBOX.capabilities, tier="expressive").slots[0]
    ref = PROFILE.voices["narrator"]["chatterbox"]
    seg = NUMBERS.model_dump(mode="json")
    with WorkerBackend(CHATTERBOX, STORE.root, slot) as w:
        assert w.load(assets=STORE.payload(assets_for("chatterbox")))["sample_rate"] == 24000
        w.call("synthesize", segment=seg, out=str(tts), voice_id=ref, attempt=1)
        reply = w.call(
            "polish", segment=seg, source=str(source), out=str(vc), voice_id=ref, attempt=1
        )
    assert reply["elapsed_ms"] < reply["duration_ms"]  # VC is faster than real time on a GPU
    for heard in _hear([tts, vc]):
        assert wer(NUMBERS.speech_text, heard, {"ё": "е"}).wer <= 0.18, heard


@pytest.mark.gpu
@needs_ruaccent
@needs_chatterbox
@needs_silero
@needs_gigaam
@needs_gpu
@pytest.mark.skipif(
    shutil.which("espeak-ng") is None or shutil.which("ffmpeg") is None,
    reason="needs espeak-ng and ffmpeg",
)
def test_codeswitch_loses_no_word_on_t1(tmp_path: Path) -> None:
    # FR-24 acceptance on T1: segments with transliterated words are read by Chatterbox
    # in their own script, the rest is Silero → Chatterbox VC; nothing flagged.
    [converted] = api.convert([FIXTURES / "ru" / "codeswitch.md"], tmp_path / "out")
    assert converted.folder is not None
    cfg = load_config([], {"synthesis": {"tier": "expressive"}})
    report = api.synthesize(converted.folder, config=cfg, store=STORE.root).report
    segs = [
        json.loads(x)
        for x in (converted.folder / "_work/segments.jsonl").read_text("utf-8").splitlines()
    ]
    assert report["tier"] == "expressive"
    assert {s["route"] for s in segs} == {"silero>chatterbox", "chatterbox"}
    assert report["foreign_spans"] and all(
        s["resolution"] != "unresolved" for s in report["foreign_spans"]
    )
    flagged = {s["text_norm"]: s["flags"] for s in segs if s["status"] != "done"}
    assert not flagged, flagged
    assert report["qa"]["flagged"] == 0
