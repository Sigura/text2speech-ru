"""Spike, OFFLINE step, variant D: Chatterbox Multilingual straight from Russian text.

    CUDA_VISIBLE_DEVICES=0 uv run --project chatterbox python chatterbox_d.py

Runs (all on the ru-smoke corpus; D1/D2 also on codeswitch/sentences.txt):
  D1 chatterbox-default  built-in voice (conds.pt), plain text
  D2 chatterbox-xenia    reference = Silero xenia prose clips, plain text
  D3 chatterbox-xenia-ru same reference, RUAccent stress as U+0301 after the vowel
Stock Chatterbox would call `russian_text_stresser` for stress, which is not on PyPI,
so without D3's marks it reads Russian unstressed. WAVs and timings go to out/d/.
"""

from __future__ import annotations

import json
import os
import random
import re
import socket
import time
from pathlib import Path

HERE = Path(__file__).parent
SMOKE = HERE.parent / "ru-smoke"
os.environ.update(HF_HOME=str(HERE / "models" / "hf"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")

NETWORK_ATTEMPTS: list[str] = []
_connect = socket.socket.connect


def _guarded_connect(self: socket.socket, address: object) -> None:
    host = address[0] if isinstance(address, tuple) else None
    if host is not None and host not in ("127.0.0.1", "::1", "localhost"):
        NETWORK_ATTEMPTS.append(repr(address))
        raise OSError(f"spike offline guard: connection to {address!r} refused")
    _connect(self, address)  # type: ignore[arg-type]


socket.socket.connect = _guarded_connect  # type: ignore[method-assign]

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from chatterbox.models.tokenizers import tokenizer as cb_tokenizer  # noqa: E402
from chatterbox.mtl_tts import ChatterboxMultilingualTTS  # noqa: E402

# MTLTokenizer always builds the Chinese converter, whose pkuseg() downloads
# spacy_ontonotes.zip from GitHub on first use, whatever the language.
cb_tokenizer.ChineseCangjieConverter._init_segmenter = (  # type: ignore[method-assign]
    lambda self: setattr(self, "segmenter", None)
)

CHATTERBOX_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
SEED = 1234
REF_CLIPS = (10, 11, 12)  # classic prose, Silero xenia (own stress)


def corpus(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]


def plus_to_acute(text: str) -> str:
    return re.sub(r"\+(\w)", "\\1\u0301", text)


def make_reference(out: Path) -> Path:
    parts = [sf.read(str(SMOKE / "out/gpu" / f"silero-xenia-own-{i:02d}.wav")) for i in REF_CLIPS]
    rate = parts[0][1]
    gap = np.zeros(int(0.4 * rate))
    sf.write(str(out), np.concatenate([x for a, _ in parts for x in (a, gap)]), rate)
    return out


def main() -> None:
    out_dir = HERE / "out" / "d"
    out_dir.mkdir(parents=True, exist_ok=True)
    smoke_rows = json.loads((SMOKE / "out/gpu/report.json").read_text())["rows"]
    main_set = [r["text"] for r in smoke_rows if r["system"] == "silero-xenia-own"]
    marks = {r["text"]: r["input"] for r in smoke_rows if r["system"] == "silero-xenia-ruaccent"}
    code_set = corpus(HERE / "codeswitch" / "sentences.txt")

    t = time.monotonic()
    ckpt = HERE / "models/hf/hub/models--ResembleAI--chatterbox/snapshots" / CHATTERBOX_REVISION
    model = ChatterboxMultilingualTTS.from_local(ckpt, "cuda")
    load_s = time.monotonic() - t
    ref = make_reference(out_dir / "ref-xenia.wav")

    runs = [
        ("chatterbox-default", None, False, [("main", main_set), ("code", code_set)]),
        ("chatterbox-xenia", ref, False, [("main", main_set), ("code", code_set)]),
        ("chatterbox-xenia-ru", ref, True, [("main", main_set)]),
    ]
    rows = []
    for name, prompt, stressed, sets in runs:
        for set_name, texts in sets:
            for i, text in enumerate(texts):
                fed = plus_to_acute(marks[text]) if stressed else text
                random.seed(SEED)
                torch.manual_seed(SEED)
                t = time.monotonic()
                wav = model.generate(
                    fed, language_id="ru", audio_prompt_path=str(prompt) if prompt else None
                )
                elapsed = time.monotonic() - t
                path = out_dir / f"{name}-{set_name}-{i:02d}.wav"
                audio = wav.squeeze(0).cpu().numpy()
                sf.write(str(path), audio, model.sr)
                rows.append(
                    {
                        "variant": name,
                        "set": set_name,
                        "i": i,
                        "text": text,
                        "input": fed,
                        "wav": str(path.relative_to(HERE)),
                        "seconds": len(audio) / model.sr,
                        "synth_s": elapsed,
                        "rtf": elapsed / (len(audio) / model.sr),
                    }
                )
            print(f"{name}/{set_name}: done")
    (out_dir / "timings.json").write_text(
        json.dumps(
            {
                "load_s": load_s,
                "network_attempts": NETWORK_ATTEMPTS,
                "gpu": torch.cuda.get_device_name(0),
                "rows": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    print("network attempts:", NETWORK_ATTEMPTS)


if __name__ == "__main__":
    main()
