"""Spike, OFFLINE step, variant C: Silero xenia WAVs (from ru-smoke) -> Chatterbox VC.

    CUDA_VISIBLE_DEVICES=2 uv run --project chatterbox python vc_c.py

Targets: Chatterbox's built-in voice (multilingual conds.pt), the same one D1 speaks
with, so C vs D1 compares Silero's reading against Chatterbox's own with the timbre
held fixed; and the Silero xenia clip D2 is cloned from (female, C-xenia vs D2).
The converter is built from the multilingual s3gen.pt (ChatterboxVC
.from_local wants the English s3gen.safetensors). Output: out/c/.
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

HERE = Path(__file__).parent
SMOKE = HERE.parent / "ru-smoke"
CHATTERBOX_REVISION = "5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18"
CKPT = HERE / "models/hf/hub/models--ResembleAI--chatterbox/snapshots" / CHATTERBOX_REVISION

NETWORK_ATTEMPTS: list[str] = []
_connect = socket.socket.connect


def _guarded_connect(self: socket.socket, address: object) -> None:
    host = address[0] if isinstance(address, tuple) else None
    if host is not None and host not in ("127.0.0.1", "::1", "localhost"):
        NETWORK_ATTEMPTS.append(repr(address))
        raise OSError(f"spike offline guard: connection to {address!r} refused")
    _connect(self, address)  # type: ignore[arg-type]


socket.socket.connect = _guarded_connect  # type: ignore[method-assign]

import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from chatterbox.models.s3gen import S3Gen  # noqa: E402
from chatterbox.vc import ChatterboxVC  # noqa: E402


def load_vc(device: str) -> ChatterboxVC:
    s3gen = S3Gen()
    s3gen.load_state_dict(torch.load(CKPT / "s3gen.pt", map_location="cpu", weights_only=True))
    s3gen.to(device).eval()
    ref_dict = torch.load(CKPT / "conds.pt", map_location="cpu", weights_only=True)["gen"]
    return ChatterboxVC(s3gen, device, ref_dict=ref_dict)


def main() -> None:
    out_dir = HERE / "out" / "c"
    out_dir.mkdir(parents=True, exist_ok=True)
    t = time.monotonic()
    vc = load_vc("cuda")
    load_s = time.monotonic() - t

    # The built-in voice is low (median F0 ~130 Hz); the xenia clip (made by
    # chatterbox_d.py) keeps a female voice and isolates the effect of re-synthesis.
    targets = [("builtin", None), ("xenia", HERE / "out/d/ref-xenia.wav")]
    rows = []
    for target, ref in targets:
        if ref is not None:
            vc.set_target_voice(str(ref))
        for src in sorted((SMOKE / "out/gpu").glob("silero-xenia-own-[0-9][0-9].wav")):
            rows.append(convert(vc, src, out_dir / f"silero-vc-{target}-{src.stem[-2:]}.wav"))
        print(f"{target}: done")
    (out_dir / "timings.json").write_text(
        json.dumps(
            {
                "load_s": load_s,
                "network_attempts": NETWORK_ATTEMPTS,
                "gpu": torch.cuda.get_device_name(0),
                "rows": rows,
            },
            indent=2,
        )
    )
    print("network attempts:", NETWORK_ATTEMPTS)


def convert(vc: ChatterboxVC, src: Path, out: Path) -> dict[str, object]:
    torch.cuda.synchronize()
    t = time.monotonic()
    wav = vc.generate(str(src))
    torch.cuda.synchronize()
    elapsed = time.monotonic() - t
    audio = wav.squeeze(0).numpy()
    sf.write(str(out), audio, vc.sr)
    seconds = sf.info(str(src)).duration
    return {
        "variant": out.stem[:-3],
        "source_variant": "silero-xenia-own",
        "i": int(src.stem[-2:]),
        "wav": str(out.relative_to(HERE)),
        "seconds": seconds,
        "out_seconds": len(audio) / vc.sr,
        "synth_s": elapsed,
        "rtf": elapsed / seconds,
    }


if __name__ == "__main__":
    main()
