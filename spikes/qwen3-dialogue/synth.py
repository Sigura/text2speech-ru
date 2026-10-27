"""Spike step 2 (Qwen3-TTS env, GPU): read out/lines.json with Qwen3-TTS 1.7B Base.

    CUDA_VISIBLE_DEVICES=<free gpu> uv run --project spikes/ru-bench/envs/qwen3 \
        python spikes/qwen3-dialogue/synth.py

Voice: cloned from the `ru` pack's `ref-xenia.wav`, like the M2 benchmark. bf16, sdpa.
- dialogue (21 segments): stress as U+0301 after the stressed vowel, one call per line (RTF of
  single calls), then the same lines as one batch (RTF of batching) → out/qwen/, out/qwen-batch/;
- homographs (6): without and with the intended stress mark → out/stress/<id>-{plain,accent}.wav;
- Latin (5): as written → out/latin/.
59 segments. Writes out/timing.json.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "out"
sys.path.insert(0, str(HERE.parent / "ru-bench"))
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from synth import REF, REF_TEXTS, snapshot  # noqa: E402  (offline guard + HF_HOME)

ACCENT = "́"  # combining acute accent


def accented(text: str) -> str:
    """RUAccent's "+" before a vowel → the vowel followed by U+0301 (ё needs none)."""
    return re.sub(r"\+(\w)", lambda m: m.group(1) + ("" if m.group(1) in "ёЁ" else ACCENT), text)


def plain(text: str) -> str:
    return text.replace("+", "")


def main() -> None:
    from qwen_tts import Qwen3TTSModel

    lines = json.loads((OUT / "lines.json").read_text(encoding="utf-8"))
    torch.manual_seed(1234)
    model = Qwen3TTSModel.from_pretrained(
        str(snapshot("Qwen/Qwen3-TTS-12Hz-1.7B-Base")),
        device_map="cuda",
        dtype=torch.bfloat16,
        attn_implementation="sdpa",
    )
    prompt = model.create_voice_clone_prompt(ref_audio=str(REF), ref_text=" ".join(REF_TEXTS))
    count = 0

    def say(texts: list[str]) -> tuple[list[np.ndarray], int, float]:
        nonlocal count
        torch.cuda.synchronize()
        started = time.monotonic()
        wavs, rate = model.generate_voice_clone(
            text=texts if len(texts) > 1 else texts[0],
            language=["Russian"] * len(texts) if len(texts) > 1 else "Russian",
            voice_clone_prompt=prompt * len(texts),
        )
        torch.cuda.synchronize()
        count += len(texts)
        return [np.asarray(w, dtype=np.float32) for w in wavs], rate, time.monotonic() - started

    def write(path: Path, audio: np.ndarray, rate: int) -> float:
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(path), audio, rate)
        return len(audio) / rate

    timing: dict[str, dict[str, float]] = {}
    say(["Проверка."])  # warm-up, not counted in the timing
    dialogue = lines["dialogue"]

    print("1/4 dialogue, one line per call")
    wall = audio = 0.0
    for item in dialogue:
        [wav], rate, took = say([accented(item["tts"])])
        wall += took
        audio += write(OUT / "qwen" / f"{item['id']}.wav", wav, rate)
    timing["single"] = {"wall_s": wall, "audio_s": audio, "rtf": wall / audio}

    print("2/4 dialogue, one batch")
    wavs, rate, took = say([accented(item["tts"]) for item in dialogue])
    audio = sum(
        write(OUT / "qwen-batch" / f"{i['id']}.wav", w, rate)
        for i, w in zip(dialogue, wavs, strict=True)
    )
    timing["batch"] = {"wall_s": took, "audio_s": audio, "rtf": took / audio, "size": len(dialogue)}

    print("3/4 homographs, plain and accented")
    for item in lines["homograph"]:
        for name, text in (("plain", plain(item["tts"])), ("accent", accented(item["tts"]))):
            [wav], rate, _ = say([text])
            write(OUT / "stress" / f"{item['id']}-{name}.wav", wav, rate)

    print("4/4 Latin-script words")
    for item in lines["latin"]:
        [wav], rate, _ = say([item["tts"]])
        write(OUT / "latin" / f"{item['id']}.wav", wav, rate)

    timing["peak_vram_gb"] = {"value": torch.cuda.max_memory_allocated() / 2**30}
    (OUT / "timing.json").write_text(json.dumps(timing, indent=1) + "\n")
    print(json.dumps(timing, indent=1))
    print(f"done: {count - 1} segments (+1 warm-up)")
    if count - 1 > 100:
        raise SystemExit("more than 100 segments: over the test-run budget")


if __name__ == "__main__":
    main()
