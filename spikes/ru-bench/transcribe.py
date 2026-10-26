"""Spike, OFFLINE step: GigaAM-v3 `rnnt` transcripts of every benchmark WAV.

    uv run --project spikes/ru-smoke python spikes/ru-bench/transcribe.py

Runs in the ru-smoke env, whose model cache already holds GigaAM-v3 (the same branch
as the QA gate) and whose run.py installs the offline guard. Reads out/wav/<system>/,
writes out/transcripts.json ({system: {segment id: text}}); already transcribed files
are kept, so a new system only costs its own clips.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent / "ru-smoke"))

import run as smoke  # noqa: E402, F401  (offline guard, HF_HOME)
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from transformers import AutoModel  # noqa: E402

OUT = HERE / "out"
WINDOW_S = 24  # GigaAM's short-form limit is 25 s; longer clips are cut into windows


def hear(asr: Any, wav: Path) -> str:
    audio, rate = sf.read(str(wav), dtype="float32")
    if len(audio) <= WINDOW_S * rate:
        windows = [wav]
    else:
        windows = []
        for n, start in enumerate(range(0, len(audio), WINDOW_S * rate)):
            part = OUT / "tmp" / f"{wav.stem}-{n}.wav"
            part.parent.mkdir(exist_ok=True)
            sf.write(str(part), audio[start : start + WINDOW_S * rate], rate)
            windows.append(part)
    with torch.inference_mode():
        return " ".join(str(asr.transcribe(str(w))) for w in windows)


def main() -> None:
    path = OUT / "transcripts.json"
    done: dict[str, dict[str, str]] = json.loads(path.read_text("utf-8")) if path.exists() else {}
    device = "cuda" if torch.cuda.is_available() else "cpu"
    asr = AutoModel.from_pretrained("ai-sage/GigaAM-v3", revision="rnnt", trust_remote_code=True)
    asr = asr.to(device).eval()
    for system_dir in sorted(p for p in (OUT / "wav").iterdir() if p.is_dir()):
        heard = done.setdefault(system_dir.name, {})
        todo = [w for w in sorted(system_dir.glob("*.wav")) if w.stem not in heard]
        for wav in todo:
            heard[wav.stem] = hear(asr, wav)
        print(f"{system_dir.name}: {len(todo)} new, {len(heard)} total")
        path.write_text(json.dumps(done, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
