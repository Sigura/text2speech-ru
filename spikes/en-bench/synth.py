"""Spike, OFFLINE step: IndexTTS-2 reads the benchmark segments, cloned per voice role.

    EN_BENCH_HOME=… UV_PROJECT_ENVIRONMENT=$EN_BENCH_HOME/venv-indextts \
        uv run --python 3.12 --project spikes/en-bench/envs/indextts \
        python spikes/en-bench/synth.py [--device cuda|cpu] [--limit N] [--tag t1|t0]

Texts come from out/segments.json (collect.py): the en pack's `text_norm`, the same text
Kokoro and Chatterbox read, so IndexTTS-2's own normalizer (WeTextProcessing) is replaced
by a pass-through. Narrator segments clone the pack's narrator reference clip and
dialogue segments the dialogue one, as Chatterbox does. Writes
out/wav/indextts[-t0]/<id>.wav and out/timing-indextts-<tag>.json. `--limit N` takes N
segments spread over the set (T0 estimate).
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
OUT = HERE / "out"
HOME = Path(os.environ.get("EN_BENCH_HOME", HERE / "models"))
CHECKPOINTS = HOME / "checkpoints"
VOICES = HERE.parents[1] / "plugins/lang-en/src/text2audio_lang_en/voices"
REFS = {"narrator": VOICES / "ref-af_heart.wav", "dialogue": VOICES / "ref-am_michael.wav"}
SEED = 1234

os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HOME=str(HOME / "hf"))
_connect = socket.socket.connect


def _guarded(self: socket.socket, address: Any) -> None:
    host = address[0] if isinstance(address, tuple) else None
    if host is not None and host not in ("127.0.0.1", "::1", "localhost"):
        raise OSError(f"en-bench offline guard: connection to {address!r} refused")
    _connect(self, address)


socket.socket.connect = _guarded  # type: ignore[method-assign]
sys.path.insert(0, str(HOME / "index-tts"))

import torch  # noqa: E402
from indextts.infer_v2 import IndexTTS2  # noqa: E402
from indextts.utils import front  # noqa: E402


class _Same:
    def normalize(self, text: str) -> str:
        return text


def _load(self: Any) -> None:
    self.zh_normalizer = self.en_normalizer = _Same()


front.TextNormalizer.load = _load  # type: ignore[method-assign]


def build(device: str) -> IndexTTS2:
    hf = CHECKPOINTS / "hf_cache"
    return IndexTTS2(
        cfg_path=str(CHECKPOINTS / "config.yaml"),
        model_dir=str(CHECKPOINTS),
        use_fp16=device == "cuda",
        device="cuda:0" if device == "cuda" else "cpu",
        use_cuda_kernel=False,
        use_qwen_emo=False,
        aux_paths={
            "w2v_bert": str(hf / "w2v-bert-2.0"),
            "semantic_codec": str(hf / "semantic_codec_model.safetensors"),
            "campplus": str(hf / "campplus_cn_common.bin"),
            "bigvgan": str(hf / "bigvgan"),
        },
    )


def duration_s(path: Path) -> float:
    import soundfile as sf

    info = sf.info(str(path))
    return info.frames / info.samplerate


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tag", default="t1")
    args = ap.parse_args()
    if args.device == "cpu":
        torch.set_num_threads(len(os.sched_getaffinity(0)))
    table = json.loads((OUT / "segments.json").read_text("utf-8"))
    if args.limit:
        table = [table[i * len(table) // args.limit] for i in range(args.limit)]
    dest = OUT / "wav" / ("indextts" if args.tag == "t1" else f"indextts-{args.tag}")
    dest.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    tts = build(args.device)
    load_s = time.perf_counter() - started
    rows = []
    for n, seg in enumerate(table, 1):
        ref = REFS["dialogue" if seg["voice"] == "dialogue" else "narrator"]
        out = dest / f"{seg['id']}.wav"
        torch.manual_seed(SEED)
        t = time.perf_counter()
        tts.infer(
            spk_audio_prompt=str(ref), text=" ".join(seg["text_norm"].split()), output_path=str(out)
        )
        if args.device == "cuda":
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - t
        audio_s = duration_s(out)
        rows.append({"id": seg["id"], "seconds": elapsed, "audio_s": audio_s})
        print(f"[{n}/{len(table)}] {seg['id']} {elapsed:.1f}s for {audio_s:.1f}s", flush=True)
    peak = torch.cuda.max_memory_allocated() / 2**20 if args.device == "cuda" else None
    timing = {
        "system": "indextts",
        "device": args.device,
        "load_s": load_s,
        "peak_vram_mb": peak,
        "threads": torch.get_num_threads(),
        "rows": rows,
    }
    (OUT / f"timing-indextts-{args.tag}.json").write_text(json.dumps(timing, indent=1))


if __name__ == "__main__":
    main()
