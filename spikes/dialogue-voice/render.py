"""Spike: dialogue voice candidates for `ru` (M3), at most 100 segments.

    uv run --python 3.11 python spikes/dialogue-voice/render.py

Renders four dialogue lines from «Капитанская дочка» and one line with an author's
remark (dialogue / narrator xenia / dialogue) for each Silero voice in VOICES, through
the installed plugin workers: Silero on CPU (T0), then Chatterbox VC into a 15 s
reference clip of the same voice (T1, the hybrid route). The reference clip is the
three sentences of `ref-xenia.wav` read by that voice. Writes one file per voice and
tier to out/<voice>-<tier>.wav (lines 0.8 s apart) and the references to out/ref-*.wav.
"""

from __future__ import annotations

import wave
from pathlib import Path

from text2audio.core import resources as res
from text2audio.core.plugins import discover
from text2audio.models.lock import assets_for, load_lock
from text2audio.models.store import ModelStore
from text2audio.workers.backend import Worker

HERE = Path(__file__).parent
OUT = HERE / "out"
XENIA_REF = HERE.parents[1] / "plugins/lang-ru/src/text2audio_lang_ru/voices/ref-xenia.wav"
VOICES = ("baya", "kseniya", "xenia")
NARRATOR = "xenia"
SEED = 1234
VC_NEED_MB = 5 * 1024  # Chatterbox peak 4.3 GB (SPEC §6.6.6)

REF_STRESSED = (
    "Вс+е сч+астливые с+емьи пох+ожи друг на др+уга, к+аждая несчастл+ивая семь+я"
    " несчастл+ива по-сво+ему.",
    "Мой д+ядя с+амых ч+естных пр+авил, когд+а не в ш+утку занем+ог, он уваж+ать себ+я"
    " заст+авил и л+учше в+ыдумать не м+ог.",
    "В челов+еке должн+о быть всё прекр+асно: и лиц+о, и од+ежда, и душ+а, и м+ысли.",
)
# (role, text with stress marks); one inner list per output line.
LINES = (
    [("dialogue", "Ты лжёшь, мерз+авец! Ты лжёшь с+амым бесст+ыдным +образом.")],
    [("dialogue", "Р+ади б+ога! Где М+арья Ив+ановна?")],
    [("dialogue", "Я нич+его не в+ижу, кр+оме б+елой ст+епи да +ясного н+еба.")],
    [("dialogue", "Нет, не узн+ал; а кто же он так+ой?")],
    [
        ("dialogue", "Эх, б+атюшка Пётр Андр+еич!"),
        ("narrator", "+Отвечал он с глуб+оким вздох+ом."),
        ("dialogue", "Серж+усь-то я на сам+ого себ+я; сам я круг+ом винов+ат."),
    ],
)


def read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as w:
        assert w.getnchannels() == 1 and w.getsampwidth() == 2, path
        return w.readframes(w.getnframes()), w.getframerate()


def write_wav(path: Path, parts: list[Path], gap_s: float) -> None:
    frames, rate = [], 0
    for p in parts:
        data, r = read_wav(p)
        if rate and r != rate:
            raise SystemExit(f"{p}: {r} Hz, expected {rate} Hz")
        rate = r
        frames.append(data)
    silence = b"\0\0" * int(gap_s * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(silence.join(frames))
    print(f"wrote {path}")


def segment(sid: str, text: str) -> dict[str, object]:
    return {"id": sid, "text": text.replace("+", ""), "text_stress": text, "seed": SEED}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    store = ModelStore()
    found = discover()
    lock = load_lock()
    silero = found.get("backends", "silero")
    chatterbox = found.get("polishers", "chatterbox")
    if silero is None or chatterbox is None:
        raise SystemExit("the silero and chatterbox plugins must be installed (uv sync)")
    count = 0

    print("1/3 Silero on CPU")
    raw: dict[tuple[str, int, int], Path] = {}
    refs: dict[str, Path] = {NARRATOR: XENIA_REF}
    with Worker(silero, store.root, res.WorkerSlot("cpu", 8)) as w:
        w.load(assets=store.payload(assets_for(silero.name, lock)))

        def say(sid: str, text: str, voice: str) -> Path:
            nonlocal count
            out = OUT / "parts" / f"{sid}.wav"
            w.call("synthesize", segment=segment(sid, text), out=str(out), voice_id=voice)
            count += 1
            return out

        for voice in VOICES:
            if voice not in refs:
                parts = [say(f"ref-{voice}-{i}", t, voice) for i, t in enumerate(REF_STRESSED)]
                refs[voice] = OUT / f"ref-{voice}.wav"
                write_wav(refs[voice], parts, 0.4)
            for n, line in enumerate(LINES):
                for k, (role, text) in enumerate(line):
                    who = voice if role == "dialogue" else NARRATOR
                    raw[voice, n, k] = say(f"{voice}-{n}-{k}", text, who)

    for voice in VOICES:
        lines = [OUT / "parts" / f"{voice}-{n}-t0.wav" for n in range(len(LINES))]
        for n, line in enumerate(LINES):
            write_wav(lines[n], [raw[voice, n, k] for k in range(len(line))], 0.25)
        write_wav(OUT / f"{voice}-t0.wav", lines, 0.8)

    print("2/3 Chatterbox VC on a GPU")
    found_res = res.probe()
    gpu = max(found_res.gpus, key=lambda g: g.free_mb, default=None)
    if gpu is None or gpu.free_mb - res.GPU_HEADROOM_MB < VC_NEED_MB:
        raise SystemExit("no GPU with enough free VRAM for Chatterbox VC")
    print(f"using {gpu.label}")
    slot = res.WorkerSlot(f"cuda:{gpu.index}", 8, gpu.cpu_affinity, gpu.uuid)
    with Worker(chatterbox, store.root, slot) as w:
        w.load(assets=store.payload(assets_for(chatterbox.name, lock)))
        for voice in VOICES:
            lines = []
            for n, line in enumerate(LINES):
                parts = []
                for k, (role, text) in enumerate(line):
                    who = voice if role == "dialogue" else NARRATOR
                    out = OUT / "parts" / f"{voice}-{n}-{k}-vc.wav"
                    w.call(
                        "polish",
                        segment=segment(f"{voice}-{n}-{k}", text),
                        source=str(raw[voice, n, k]),
                        out=str(out),
                        voice_id=str(refs[who]),
                    )
                    count += 1
                    parts.append(out)
                lines.append(OUT / "parts" / f"{voice}-{n}-t1.wav")
                write_wav(lines[-1], parts, 0.25)
            write_wav(OUT / f"{voice}-t1.wav", lines, 0.8)

    print(f"3/3 done: {count} segments rendered")
    if count > 100:
        raise SystemExit("more than 100 segments: over the test-run budget")


if __name__ == "__main__":
    main()
