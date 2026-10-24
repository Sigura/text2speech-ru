"""Spike: what Silero v5.6 ru does with Latin-script words (run in the ru-smoke env)."""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
SMOKE = HERE.parent.parent / "ru-smoke"
sys.path.insert(0, str(SMOKE))

import run as smoke  # noqa: E402  (installs the offline guard, sets HF_HOME)
import torch  # noqa: E402
from transformers import AutoModel  # noqa: E402

out = HERE / "out"
out.mkdir(exist_ok=True)
silero = smoke.Silero("cuda")
asr = AutoModel.from_pretrained("ai-sage/GigaAM-v3", revision="rnnt", trust_remote_code=True)
asr = asr.to("cuda").eval()
lines = [
    ln.strip()
    for ln in (HERE / "sentences.txt").read_text().splitlines()
    if ln.strip() and not ln.startswith("#")
]
for i, text in enumerate(lines):
    wav = out / f"silero-xenia-{i:02d}.wav"
    try:
        silero.synth(text, "xenia", wav, True)
    except Exception as e:
        print(f"{i:02d} FAILED {type(e).__name__}: {e}")
        continue
    with torch.inference_mode():
        heard = asr.transcribe(str(wav))
    print(f"{i:02d} {smoke.wav_seconds(wav):5.2f}s «{text}»\n    → «{heard}»")
print("network attempts:", smoke.NETWORK_ATTEMPTS)
