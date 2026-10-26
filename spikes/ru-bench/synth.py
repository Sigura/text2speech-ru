"""Spike, OFFLINE step: one challenger reads benchmark segments, cloned from xenia.

    uv run --project spikes/ru-bench/envs/<name> python spikes/ru-bench/synth.py <name> \
        [--device cuda|cpu] [--planned | --limit N] [--tag t1|t0]

<name> is qwen3, espeech or xtts. Texts come from out/segments.json (collect.py), so
every system reads the same ru-pack normalization: Qwen3-TTS and XTTS-v2 are
multilingual and get the T1 text (Latin words kept, ё restored, no stress marks);
ESpeech is Russian-only and gets the T0 text with RUAccent "+" marks (Latin words
transliterated), as a plugin would. Writes out/wav/<name>[-t0]/<id>.wav and
out/timing-<name>-<tag>.json (seconds per segment, audio length, peak VRAM).
`--planned` takes the ≤ 100 segments `listen.py plan` chose (SPEC §6.6.5); `--limit N`
takes N segments spread over the set (T0 estimates); neither reads every segment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
OUT = HERE / "out"
REF = HERE.parents[1] / "plugins/lang-ru/src/text2audio_lang_ru/voices/ref-xenia.wav"
# ref-xenia.wav = these three Silero xenia sentences, 0.4 s of silence between them.
REF_TEXTS = (
    "Все счастливые семьи похожи друг на друга, каждая несчастливая семья несчастлива по-своему.",
    "Мой дядя самых честных правил, когда не в шутку занемог, он уважать себя заставил и"
    " лучше выдумать не мог.",
    "В человеке должно быть всё прекрасно: и лицо, и одежда, и душа, и мысли.",
)
REF_STRESSED = (
    "Вс+е сч+астливые с+емьи пох+ожи друг на др+уга, к+аждая несчастл+ивая семь+я"
    " несчастл+ива по-сво+ему.",
    "Мой д+ядя с+амых ч+естных пр+авил, когд+а не в ш+утку занем+ог, он уваж+ать себ+я"
    " заст+авил и л+учше в+ыдумать не м+ог.",
    "В челов+еке должн+о быть всё прекр+асно: и лиц+о, и од+ежда, и душ+а, и м+ысли.",
)
SEED = 1234

os.environ.update(
    HF_HOME=str(HERE / "models" / "hf"),
    HF_HUB_OFFLINE="1",
    TRANSFORMERS_OFFLINE="1",
    COQUI_TOS_AGREED="1",  # CPML; personal use (ADR-0007)
)
_connect = socket.socket.connect


def _guarded(self: socket.socket, address: Any) -> None:
    host = address[0] if isinstance(address, tuple) else None
    if host is not None and host not in ("127.0.0.1", "::1", "localhost"):
        raise OSError(f"ru-bench offline guard: connection to {address!r} refused")
    _connect(self, address)


socket.socket.connect = _guarded  # type: ignore[method-assign]

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402


def snapshot(repo: str) -> Path:
    """The snapshot directory fetch.py recorded.

    huggingface_hub cannot resolve these offline: fetch.py downloads by commit (no
    refs/main) and only the needed files (it reports the snapshot as incomplete).
    """
    for inv in (HERE / "models").glob("inventory-*.json"):
        for entry in json.loads(inv.read_text()):
            if entry["repo"] == repo:
                return Path(entry["dir"])
    raise SystemExit(f"{repo} not in models/inventory-*.json: run setup.sh")


def plain(text: str) -> str:
    return " ".join(text.replace("+", "").split())


def reference_parts() -> list[np.ndarray]:
    """The reference cut back into its sentences at the inserted runs of exact zeros."""
    audio, rate = sf.read(str(REF), dtype="float32")
    loud = np.flatnonzero(audio != 0)
    cuts = np.flatnonzero(np.diff(loud) > int(0.35 * rate))
    starts = [loud[0], *(loud[c + 1] for c in cuts)]
    ends = [*(loud[c] + 1 for c in cuts), loud[-1] + 1]
    parts = [audio[s:e] for s, e in zip(starts, ends, strict=True)]
    if len(parts) != len(REF_TEXTS):
        raise SystemExit(f"expected {len(REF_TEXTS)} sentences in the reference, got {len(parts)}")
    return parts


class Qwen3:
    text_key = "t1_stress"

    def __init__(self, device: str) -> None:
        from qwen_tts import Qwen3TTSModel

        dtype = torch.bfloat16 if device == "cuda" else torch.float32
        self.model = Qwen3TTSModel.from_pretrained(
            str(snapshot("Qwen/Qwen3-TTS-12Hz-1.7B-Base")),
            device_map=device,
            dtype=dtype,
            attn_implementation="sdpa",
        )
        self.prompt = self.model.create_voice_clone_prompt(
            ref_audio=str(REF), ref_text=" ".join(REF_TEXTS)
        )

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        wavs, rate = self.model.generate_voice_clone(
            text=plain(text), language="Russian", voice_clone_prompt=self.prompt
        )
        return np.asarray(wavs[0], dtype=np.float32), rate


class ESpeech:
    text_key = "t0_stress"
    MAX_REF_S = 12.0  # the model card's limit for the reference clip

    def __init__(self, device: str) -> None:
        from f5_tts.infer.utils_infer import load_model, load_vocoder
        from f5_tts.model import DiT

        root = snapshot("ESpeech/ESpeech-TTS-1_SFT-256K")
        cfg = dict(dim=1024, depth=22, heads=16, ff_mult=2, text_dim=512, conv_layers=4)
        self.model = load_model(
            DiT,
            cfg,
            str(root / "espeech_tts_256k.pt"),
            vocab_file=str(root / "vocab.txt"),
            device=device,
        )
        self.vocoder = load_vocoder(
            is_local=True, local_path=str(snapshot("charactr/vocos-mel-24khz")), device=device
        )
        self.device = device
        rate = sf.info(str(REF)).samplerate
        parts = reference_parts()
        # The longest choice of whole reference sentences that fits the 12 s limit.
        best: tuple[float, list[int]] = (0.0, [])
        for mask in range(1, 1 << len(parts)):
            pick = [i for i in range(len(parts)) if mask >> i & 1]
            length = sum(len(parts[i]) for i in pick) / rate + 0.4 * (len(pick) - 1)
            if best[0] < length <= self.MAX_REF_S:
                best = (length, pick)
        gap = np.zeros(int(0.4 * rate), dtype=np.float32)
        clip = np.concatenate([x for i in best[1] for x in (parts[i], gap)][:-1])
        self.ref = OUT / "espeech-ref.wav"
        sf.write(str(self.ref), clip, rate)
        self.ref_text = " ".join(REF_STRESSED[i] for i in best[1]) + " "
        print(f"espeech reference: sentences {best[1]}, {best[0]:.1f} s")

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        from f5_tts.infer.utils_infer import infer_process

        wav, rate, _ = infer_process(
            str(self.ref),
            self.ref_text,
            " ".join(text.split()),
            self.model,
            self.vocoder,
            nfe_step=32,
            device=self.device,
            show_info=lambda *_: None,
        )
        return np.asarray(wav, dtype=np.float32), rate


class Xtts:
    text_key = "t1_stress"

    def __init__(self, device: str) -> None:
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models import xtts
        from TTS.tts.models.xtts import Xtts as Model

        def load_without_rewrite(_self: Any, model_path: Path) -> Any:
            # coqui-tts converts the original checkpoint and saves it over model.pth;
            # the pinned blob is read-only, so load it the same way and keep it as is.
            with xtts._legacy_safe_globals():
                checkpoint = torch.load(model_path, map_location="cpu", weights_only=True)
            return checkpoint["model"]

        Model._load_checkpoint = load_without_rewrite
        root = snapshot("coqui/XTTS-v2")
        self.config = XttsConfig()
        self.config.load_json(str(root / "config.json"))
        self.model = Model.init_from_config(self.config)
        self.model.load_checkpoint(self.config, checkpoint_dir=str(root), use_deepspeed=False)
        self.model.to(device)
        self.latent, self.embedding = self.model.get_conditioning_latents(audio_path=[str(REF)])

    def __call__(self, text: str) -> tuple[np.ndarray, int]:
        out = self.model.inference(plain(text), "ru", self.latent, self.embedding)
        return np.asarray(out["wav"], dtype=np.float32), self.config.audio.output_sample_rate


SYSTEMS = {"qwen3": Qwen3, "espeech": ESpeech, "xtts": Xtts}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("system", choices=sorted(SYSTEMS))
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--planned", action="store_true", help="the segments listen.py plan chose")
    ap.add_argument("--tag", default="t1")
    args = ap.parse_args()
    if args.device == "cpu":
        torch.set_num_threads(len(os.sched_getaffinity(0)))
    table = json.loads((OUT / "segments.json").read_text("utf-8"))
    if args.planned:
        chosen = set(json.loads((OUT / "listen-key.json").read_text("utf-8"))["synth"])
        table = [s for s in table if s["id"] in chosen]
    elif args.limit:
        table = [table[i * len(table) // args.limit] for i in range(args.limit)]
    dest = OUT / "wav" / (args.system if args.tag == "t1" else f"{args.system}-{args.tag}")
    dest.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    tts = SYSTEMS[args.system](args.device)
    load_s = time.perf_counter() - started
    rows = []
    for n, seg in enumerate(table, 1):
        text = re.sub(r"\s+", " ", seg[tts.text_key])
        torch.manual_seed(SEED)
        t = time.perf_counter()
        audio, rate = tts(text)
        if args.device == "cuda":
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - t
        sf.write(str(dest / f"{seg['id']}.wav"), audio, rate)
        rows.append({"id": seg["id"], "seconds": elapsed, "audio_s": len(audio) / rate})
        print(f"[{n}/{len(table)}] {seg['id']} {elapsed:.1f}s for {len(audio) / rate:.1f}s")
    peak = torch.cuda.max_memory_allocated() / 2**20 if args.device == "cuda" else None
    timing = {
        "system": args.system,
        "device": args.device,
        "load_s": load_s,
        "peak_vram_mb": peak,
        "threads": torch.get_num_threads(),
        "rows": rows,
    }
    (OUT / f"timing-{args.system}-{args.tag}.json").write_text(json.dumps(timing, indent=1))


if __name__ == "__main__":
    main()
