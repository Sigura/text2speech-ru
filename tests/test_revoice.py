"""`revoice`: timed phrases, translation per phrase, slot fitting, track replacement."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import soundfile as sf
from text2audio_asr_gigaam.descriptor import GigaAM
from text2audio_asr_whisper.descriptor import Whisper
from text2audio_backend_silero.descriptor import SileroBackend
from typer.testing import CliRunner

from text2audio.audio.encode import replace_track
from text2audio.audio.wav import ASSEMBLY_RATE, PartWriter
from text2audio.cli import app
from text2audio.core.contracts import Segment
from text2audio.core.envs import plugin_env
from text2audio.core.revoice import (
    LATE_MS,
    MAX_SPEEDUP,
    Phrase,
    RevoiceError,
    clips_by_phrase,
    phrases,
    place,
    translate_phrases,
)
from text2audio.core.translate import (
    Engine,
    Stats,
    Translate,
    runnable_translators,
    translate_markdown,
)
from text2audio.models.lock import assets_for
from text2audio.models.store import ModelStore
from text2audio.workers.backend import Worker

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg")


def test_phrases_end_at_sentences_and_pauses_but_not_abbreviations() -> None:
    found = phrases(
        [
            (0.0, 0.4, "Good"),
            (0.4, 0.8, "evening."),
            (1.0, 1.3, "Mr."),
            (1.3, 1.8, "Holmes"),
            (1.8, 2.2, "smiled"),
            (3.1, 3.5, "and"),  # 0.9 s pause: the phrase ends without punctuation
            (3.5, 3.9, "left."),
            (6.0, 6.4, "Look!"),  # 2.1 s pause: a new paragraph
        ],
        ("mr.",),
    )
    assert [(p.text, p.paragraph) for p in found] == [
        ("Good evening.", 0),
        ("Mr. Holmes smiled", 0),
        ("and left.", 0),
        ("Look!", 1),
    ]
    assert (found[1].start, found[1].end) == (1.0, 2.2)


def test_long_phrases_end_at_a_comma() -> None:
    words = [(i * 0.5, i * 0.5 + 0.4, "word") for i in range(20)]
    words[17] = (8.5, 8.9, "word,")
    assert [len(p.text.split()) for p in phrases(words, ())] == [18, 2]


TABLE = {
    "Good evening. Look up.": "Добрый вечер. Посмотрите вверх.",
    "Good evening.": "Добрый вечер.",
    "Look up.": "Посмотрите вверх.",
    "Now!": "Сейчас!",
}


def _run(table: dict[str, str], seen: list[str]) -> Any:
    def translate(texts: list[str]) -> list[str]:
        seen.extend(texts)
        return [table[t] for t in texts]

    fn: Translate = translate

    @contextmanager
    def factory() -> Iterator[Engine]:
        yield Engine("fake", "paragraph", fn)

    def run(markdown: str) -> tuple[str, Stats]:
        return translate_markdown(markdown, [factory], (), (), None, None)

    return run


def _phrases() -> list[Phrase]:
    return [
        Phrase(0.0, 1.0, "Good evening.", 0),
        Phrase(1.2, 2.0, "Look up.", 0),
        Phrase(4.0, 4.5, "Now!", 1),
    ]


def test_a_paragraph_is_translated_whole_and_its_sentences_go_back_to_its_phrases() -> None:
    found, seen = _phrases(), []
    translate_phrases(found, _run(TABLE, seen), ())
    assert [p.out for p in found] == ["Добрый вечер.", "Посмотрите вверх.", "Сейчас!"]
    assert sorted(seen) == ["Good evening. Look up.", "Now!"]  # batches go by length


def test_a_paragraph_whose_sentences_do_not_match_goes_phrase_by_phrase() -> None:
    merged = {**TABLE, "Good evening. Look up.": "Добрый вечер, посмотрите вверх."}
    found, seen = _phrases(), []
    translate_phrases(found, _run(merged, seen), ())
    assert [p.out for p in found] == ["Добрый вечер.", "Посмотрите вверх.", "Сейчас!"]
    assert sorted(seen[:2]) == ["Good evening. Look up.", "Now!"]
    assert sorted(seen[2:]) == ["Good evening.", "Look up."]  # only the mismatched paragraph


def _seg(n: int, text: str) -> Segment:
    return Segment(id=f"ch00-s{n:04d}", text=text, lang="ru", hash=f"h{n}")


def test_segments_are_given_to_their_phrases() -> None:
    found = [
        Phrase(0, 1, "", 0, out="Сказал он. И ушёл."),
        Phrase(1, 2, "", 0, out="♪"),
        Phrase(2, 3, "", 0, out="«Да!»"),
    ]
    segs = [_seg(1, "Сказал он."), _seg(2, "И ушёл."), _seg(3, "«Да!»")]
    assert [[s.id for s in g] for g in clips_by_phrase(found, segs)] == [
        ["ch00-s0001", "ch00-s0002"],
        [],
        ["ch00-s0003"],
    ]
    with pytest.raises(RevoiceError, match="matches no phrase"):
        clips_by_phrase(found, [_seg(1, "Другое.")])


def _tone(path: Path, seconds: float) -> Path:
    t = np.arange(int(ASSEMBLY_RATE * seconds)) / ASSEMBLY_RATE
    sf.write(str(path), 0.3 * np.sin(2 * np.pi * 220 * t), ASSEMBLY_RATE)
    return path


@needs_ffmpeg
def test_video_phrases_start_on_time_long_ones_speed_up_and_run_late(tmp_path: Path) -> None:
    assert FFMPEG is not None
    found = [
        Phrase(1.0, 2.0, "a", 0, out="Раз."),
        Phrase(3.0, 4.0, "b", 0, out="Два."),  # 2 s slot, a 4 s clip
        Phrase(5.0, 6.0, "c", 0, out="Три."),
    ]
    clips = [
        [_tone(tmp_path / "1.wav", 1.0)],
        [_tone(tmp_path / "2.wav", 4.0)],
        [_tone(tmp_path / "3.wav", 0.5)],
    ]
    with PartWriter(tmp_path / "out.wav", ASSEMBLY_RATE, lead_in_ms=0) as writer:
        cues, late = place(found, clips, writer, "video", 10_000, FFMPEG)
    assert writer.duration_ms == 10_000
    starts = [c.start_ms for c in cues]
    assert starts[:2] == [1000, 3000]
    second_end = 3000 + round(4000 / MAX_SPEEDUP)
    assert abs(cues[1].end_ms - second_end) < 40
    assert abs(starts[2] - second_end) < 40  # waited for the late phrase, did not overlap
    assert len(late) == (1 if second_end - 5000 > LATE_MS else 0)


@needs_ffmpeg
def test_audio_keeps_the_original_pauses_unhurried(tmp_path: Path) -> None:
    assert FFMPEG is not None
    found = [Phrase(0.5, 1.0, "a", 0, out="Раз."), Phrase(3.0, 4.0, "b", 0, out="Два.")]
    clips = [[_tone(tmp_path / "1.wav", 2.0)], [_tone(tmp_path / "2.wav", 1.0)]]
    with PartWriter(tmp_path / "out.wav", ASSEMBLY_RATE, lead_in_ms=0) as writer:
        cues, late = place(found, clips, writer, "audio", 4000, FFMPEG)
    assert [c.start_ms for c in cues] == [500, 4500]  # the 2 s pause after a 2 s clip
    assert writer.duration_ms == 5500 and late == []


def _streams(path: Path) -> list[str]:
    assert FFMPEG is not None
    proc = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path)], capture_output=True, text=True)
    return [line.strip() for line in proc.stderr.splitlines() if line.strip().startswith("Stream")]


@needs_ffmpeg
def test_video_keeps_its_picture_and_gets_the_new_track_and_subtitles(tmp_path: Path) -> None:
    assert FFMPEG is not None
    video = tmp_path / "in.mp4"
    lavfi = ["-f", "lavfi", "-i"]
    cmd = [FFMPEG, "-v", "error", *lavfi, "testsrc=size=64x64:rate=10:duration=3", *lavfi]
    cmd += ["sine=frequency=440:duration=3", "-c:v", "mpeg4", "-c:a", "aac", str(video)]
    subprocess.run(cmd, check=True)
    srt = tmp_path / "in.srt"
    srt.write_text("1\n00:00:00,500 --> 00:00:01,500\nПривет.\n", encoding="utf-8")
    out = tmp_path / "in.ru.mp4"
    replace_track(
        FFMPEG, _tone(tmp_path / "ru.wav", 3.0), out, video=video, subtitles=srt, language="rus"
    )
    streams = _streams(out)
    assert len(streams) == 3, streams
    assert "Video: mpeg4" in streams[0]
    assert "(rus): Audio: aac" in streams[1] and "mono" in streams[1]
    assert "(rus): Subtitle: mov_text" in streams[2]
    assert not list(tmp_path.glob(".*partial*"))


def test_revoice_refuses_what_is_not_a_recording(tmp_path: Path) -> None:
    out = CliRunner().invoke(app, ["revoice", str(tmp_path / "missing.mp4")])
    assert out.exit_code != 0
    assert "not an audio or video file" in out.output


STORE = ModelStore()


def _ready(plugin: object, owner: str) -> bool:
    env = plugin_env(plugin)
    assets = assets_for(owner)
    return bool(env and env.installed and assets and not STORE.verify(assets))


def _all_ready() -> bool:
    return (
        FFMPEG is not None
        and shutil.which("espeak-ng") is not None
        and _ready(Whisper(), "asr:whisper")
        and _ready(SileroBackend(), "silero")
        and _ready(GigaAM(), "asr:gigaam")
        and bool(runnable_translators("en", "ru", STORE)[0])
    )


@pytest.mark.models
@pytest.mark.skipif(not _all_ready(), reason="ffmpeg, espeak-ng, Whisper, Silero, GigaAM or MT")
def test_english_video_is_revoiced_in_russian_on_time(tmp_path: Path) -> None:
    assert FFMPEG is not None
    lines = ["The telescope was built in eighteen ninety eight.", "Look at the moon tonight."]
    parts = []
    for n, line in enumerate(lines):
        wav = tmp_path / f"en{n}.wav"
        subprocess.run(["espeak-ng", "-v", "en-us", "-w", str(wav), line], check=True)
        parts.append(wav)
    speech = tmp_path / "en.wav"
    # 1 s of silence, the first line, 3 s of silence, the second line, 1 s of silence
    pad = "aevalsrc=0:d={}:s=22050"
    graph = (
        f"{pad.format(1)}[a];{pad.format(3)}[b];{pad.format(1)}[c];"
        "[a][0:a][b][1:a][c]concat=n=5:v=0:a=1"
    )
    cmd = [FFMPEG, "-v", "error", "-i", str(parts[0]), "-i", str(parts[1])]
    subprocess.run([*cmd, "-filter_complex", graph, str(speech)], check=True)
    seconds = sf.info(str(speech)).duration
    video = tmp_path / "talk.mp4"
    cmd = [FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=64x64:rate=10:d={seconds}"]
    cmd += ["-i", str(speech), "-c:v", "mpeg4", "-c:a", "aac", "-shortest", str(video)]
    subprocess.run(cmd, check=True)

    result = CliRunner().invoke(app, ["revoice", str(video)])
    assert result.exit_code == 0, result.output
    out = tmp_path / "talk.ru.mp4"
    streams = _streams(out)
    assert "Video: mpeg4" in streams[0] and "(rus): Audio: aac" in streams[1]
    cues = (tmp_path / "talk.ru.srt").read_text(encoding="utf-8")
    assert "телескоп" in cues.lower() and "лун" in cues.lower()
    second = cues.split("\n\n")[1].splitlines()[1]
    assert second.startswith("00:00:0")  # the second line keeps its slot after the pause

    heard = tmp_path / "ru16.wav"
    subprocess.run(
        [FFMPEG, "-v", "error", "-i", str(out), "-vn", "-ac", "1", "-ar", "16000", str(heard)],
        check=True,
    )
    assert abs(sf.info(str(heard)).duration - seconds) < 0.5
    gigaam = GigaAM()
    with Worker(gigaam, STORE.root) as worker:
        worker.load(assets=STORE.payload(assets_for("asr:gigaam")))
        text = worker.call("transcribe", paths=[str(heard)])["texts"][0].lower()
    assert "телескоп" in text and "лун" in text, text
