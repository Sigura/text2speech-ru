"""Spike, OFFLINE step: faster-whisper transcripts of every benchmark WAV.

    T2A_MODEL_HOME=… T2A_ENV_HOME=… uv run --python 3.11 python spikes/en-bench/transcribe.py

Runs the QA gate's own asr-whisper worker (spoken-form output, digits suppressed) on the
visible GPU with the most free memory, so it uses large-v3-turbo, the model the gate
picks on a GPU. Reads out/wav/<system>/, writes out/transcripts.json
({system: {segment id: text}}); already transcribed files are kept, so a new system only
costs its own clips.
"""

from __future__ import annotations

import json
from pathlib import Path

from text2audio_asr_whisper.descriptor import Whisper

from text2audio.core import resources as res
from text2audio.models.lock import assets_for
from text2audio.models.store import ModelStore
from text2audio.workers.backend import Worker

HERE = Path(__file__).parent
OUT = HERE / "out"
BATCH = 16


def main() -> None:
    path = OUT / "transcripts.json"
    done: dict[str, dict[str, str]] = json.loads(path.read_text("utf-8")) if path.exists() else {}
    store = ModelStore()
    asr = Whisper()
    gpu = max(res.probe().gpus, key=lambda g: g.free_mb)
    slot = res.WorkerSlot("cuda", asr.capabilities.threads, gpu_uuid=gpu.uuid)
    with Worker(asr, store.root, slot) as w:
        w.load(assets=store.payload(assets_for(asr.component)))
        for system_dir in sorted(p for p in (OUT / "wav").iterdir() if p.is_dir()):
            heard = done.setdefault(system_dir.name, {})
            todo = [p for p in sorted(system_dir.glob("*.wav")) if p.stem not in heard]
            for i in range(0, len(todo), BATCH):
                batch = todo[i : i + BATCH]
                texts = w.call("transcribe", paths=[str(p) for p in batch])["texts"]
                heard.update({p.stem: t for p, t in zip(batch, texts, strict=True)})
            print(f"{system_dir.name}: {len(todo)} new, {len(heard)} total")
            path.write_text(json.dumps(done, ensure_ascii=False, indent=1), "utf-8")


if __name__ == "__main__":
    main()
