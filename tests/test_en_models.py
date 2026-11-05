"""English components with real models, offline (§6.10). The workers run with
T2A_NO_NETWORK=1, so any download attempt fails the test."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import soundfile as sf
from text2audio_asr_whisper.descriptor import Whisper
from text2audio_backend_chatterbox.descriptor import ChatterboxBackend
from text2audio_backend_kokoro.descriptor import KokoroBackend
from text2audio_lang_en import PROFILE

from text2audio import api
from text2audio.core import resources as res
from text2audio.core.config import load_config
from text2audio.core.contracts import Segment
from text2audio.core.envs import plugin_env
from text2audio.models.lock import assets_for
from text2audio.models.store import ModelStore
from text2audio.qa.wer import wer
from text2audio.workers.backend import Worker

pytestmark = pytest.mark.models

STORE = ModelStore()


def ready(plugin: object, owner: str) -> bool:
    env = plugin_env(plugin)
    assets = assets_for(owner)
    return bool(env and env.installed and assets and not STORE.verify(assets))


needs_whisper = pytest.mark.skipif(
    not ready(Whisper(), "asr:whisper") or shutil.which("espeak-ng") is None,
    reason="faster-whisper env or assets missing, or no espeak-ng",
)


def spoken_clip(tmp_path: Path, text: str) -> str:
    out = tmp_path / "clip.wav"
    subprocess.run(
        ["espeak-ng", "-v", "en-us", "-s", "130", "-w", str(out), text],
        check=True,
        capture_output=True,
    )
    return str(out)


SENTENCE = "In nineteen eighty four Mister Smith paid five dollars for twenty one books."


def transcribe(tmp_path: Path, gpu: bool) -> str:
    plan = res.plan(res.probe(), Whisper().capabilities, tier="expressive" if gpu else "fast")
    slot = plan.slots[0]
    assert slot.device.startswith("cuda") == gpu, slot
    with Worker(Whisper(), STORE.root, slot) as w:
        w.load(assets=STORE.payload(assets_for("asr:whisper")))
        return str(w.call("transcribe", paths=[spoken_clip(tmp_path, SENTENCE)])["texts"][0])


@needs_whisper
def test_whisper_on_cpu_writes_spoken_forms(tmp_path: Path) -> None:
    text = transcribe(tmp_path, gpu=False)
    assert not any(c.isdigit() for c in text), text
    assert "$" not in text and "Mr." not in text, text
    assert wer(SENTENCE, text).wer < 0.3, text


needs_kokoro = pytest.mark.skipif(
    not ready(KokoroBackend(), "kokoro"), reason="Kokoro env or assets missing"
)


def kokoro_reply(w: Worker, tmp_path: Path, text: str, voice: str = "af_heart") -> dict[str, Any]:
    seg = Segment(id="k", text=text, text_norm=text, lang="en")
    out = tmp_path / f"{abs(hash(text))}.wav"
    reply = w.call("synthesize", segment=seg.model_dump(), out=str(out), voice_id=voice)
    reply["audio"], reply["rate"] = sf.read(str(out))
    return reply


@needs_kokoro
def test_kokoro_offline_with_pinned_spacy(tmp_path: Path) -> None:
    with Worker(KokoroBackend(), STORE.root) as w:
        assert w.load(assets=STORE.payload(assets_for("kokoro")))["sample_rate"] == 24000
        reply = kokoro_reply(w, tmp_path, "Mister Darcy bowed to Missus Bennet.")
    assert reply["rate"] == 24000
    assert 1500 < reply["duration_ms"] < 6000
    assert abs(reply["audio"]).max() > 0.05  # not silent


@needs_kokoro
def test_kokoro_reads_heteronyms_by_part_of_speech(tmp_path: Path) -> None:
    pairs = [
        ("I read it yesterday.", "I will read it tomorrow."),
        ("The lead pipe was heavy.", "They lead the way."),
        ("A live wire sparked.", "They live in London."),
    ]
    with Worker(KokoroBackend(), STORE.root) as w:
        w.load(assets=STORE.payload(assets_for("kokoro")))
        for first, second in pairs:
            a = kokoro_reply(w, tmp_path, first)["phonemes"]
            b = kokoro_reply(w, tmp_path, second)["phonemes"]
            word = first.split()[1]
            assert a != b and word not in a, (word, a, b)


@needs_kokoro
def test_kokoro_british_voice(tmp_path: Path) -> None:
    with Worker(KokoroBackend(), STORE.root) as w:
        w.load(assets=STORE.payload(assets_for("kokoro")))
        reply = kokoro_reply(w, tmp_path, "The tomato was in the garden.", voice="bf_emma")
    assert reply["duration_ms"] > 1000


CHATTERBOX = ChatterboxBackend()
needs_chatterbox_gpu = pytest.mark.skipif(
    not ready(CHATTERBOX, "chatterbox")
    or res.plan(res.probe(), CHATTERBOX.capabilities).tier != "expressive",
    reason="Chatterbox env or assets missing, or no GPU with enough free VRAM",
)


@pytest.mark.gpu
@needs_chatterbox_gpu
@needs_whisper
def test_chatterbox_reads_english_in_the_narrator_voice(tmp_path: Path) -> None:
    slot = res.plan(res.probe(), CHATTERBOX.capabilities, tier="expressive").slots[0]
    ref = PROFILE.voices["narrator"]["chatterbox"]
    assert Path(ref).is_file()
    seg = Segment(id="c", text=SENTENCE, text_norm=SENTENCE, lang="en").model_dump(mode="json")
    out = tmp_path / "chatterbox.wav"
    with Worker(CHATTERBOX, STORE.root, slot) as w:
        w.load(assets=STORE.payload(assets_for("chatterbox")))
        w.call("synthesize", segment=seg, out=str(out), voice_id=ref, attempt=1)
    with Worker(Whisper(), STORE.root) as w:
        w.load(assets=STORE.payload(assets_for("asr:whisper")))
        heard = str(w.call("transcribe", paths=[str(out)])["texts"][0])
    assert wer(SENTENCE, heard).wer <= 0.12, heard


@needs_whisper
@pytest.mark.gpu
@pytest.mark.skipif(not res.probe().gpus, reason="no GPU")
def test_whisper_on_gpu_writes_spoken_forms(tmp_path: Path) -> None:
    text = transcribe(tmp_path, gpu=True)
    assert not any(c.isdigit() for c in text), text
    assert wer(SENTENCE, text).wer < 0.3, text


FIXTURE = Path(__file__).parent / "fixtures" / "en" / "qa-check.md"


def render(tmp_path: Path, tier: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    [converted] = api.convert([FIXTURE], tmp_path / "out")
    assert converted.folder is not None
    cfg = load_config([], {"synthesis": {"tier": tier}})
    report = api.synthesize(converted.folder, config=cfg, store=STORE.root).report
    lines = (converted.folder / "_work/segments.jsonl").read_text("utf-8").splitlines()
    return report, [json.loads(x) for x in lines]


@needs_kokoro
@needs_whisper
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_english_book_passes_the_qa_gate_on_t0(tmp_path: Path) -> None:
    # §11 M4 exit criterion: numbers, titles, dates, dialogue and a ruler's numeral read
    # by Kokoro and heard by faster-whisper; every segment passes.
    report, segs = render(tmp_path, "fast")
    assert report["language"] == "en" and report["tier"] == "fast"
    assert {s["voice"] for s in segs} == {"narrator", "dialogue"}
    flagged = {s["text_norm"]: s["flags"] for s in segs if s["status"] != "done"}
    assert not flagged, flagged
    assert report["qa"]["checked"] == len(segs) and report["qa"]["flagged"] == 0


@pytest.mark.gpu
@needs_chatterbox_gpu
@needs_whisper
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_english_book_passes_the_qa_gate_on_t1(tmp_path: Path) -> None:
    report, segs = render(tmp_path, "expressive")
    assert report["tier"] == "expressive"
    flagged = {s["text_norm"]: s["flags"] for s in segs if s["status"] != "done"}
    assert not flagged, flagged
    assert report["qa"]["flagged"] == 0
