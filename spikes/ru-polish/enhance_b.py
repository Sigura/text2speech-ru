"""Spike, OFFLINE step, variant B: Silero xenia WAVs (from ru-smoke) -> resemble-enhance.

    CUDA_VISIBLE_DEVICES=1 uv run --project enhance python enhance_b.py

Also enhances Piper irina (22 kHz), the one input where raising bandwidth could matter.
deepspeed is imported by resemble-enhance's training helpers only, so it is stubbed;
the checkpoint is loaded here with weights_only=True (SPEC §6.12) instead of through
`load_enhancer()`, which would git-clone unpinned weights. Output: out/b/.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import json
import socket
import sys
import time
import types
from pathlib import Path
from typing import ClassVar

HERE = Path(__file__).parent
SMOKE = HERE.parent / "ru-smoke"
ENHANCE_REVISION = "4e3510ce4a8391159f665903544c5150bee7b2cb"
RUN_DIR = (
    HERE
    / "models/hf/hub/models--ResembleAI--resemble-enhance/snapshots"
    / ENHANCE_REVISION
    / "enhancer_stage2"
)

NETWORK_ATTEMPTS: list[str] = []
_connect = socket.socket.connect


def _guarded_connect(self: socket.socket, address: object) -> None:
    host = address[0] if isinstance(address, tuple) else None
    if host is not None and host not in ("127.0.0.1", "::1", "localhost"):
        NETWORK_ATTEMPTS.append(repr(address))
        raise OSError(f"spike offline guard: connection to {address!r} refused")
    _connect(self, address)  # type: ignore[arg-type]


socket.socket.connect = _guarded_connect  # type: ignore[method-assign]


class _Stub(types.ModuleType):
    """Any deepspeed.* module; any attribute is a placeholder class."""

    __path__: ClassVar[list[str]] = []

    def __getattr__(self, name: str) -> object:
        return type(name, (), {})


class _StubFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name: str, path: object, target: object = None) -> object:
        if name == "deepspeed" or name.startswith("deepspeed."):
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
        return None

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> types.ModuleType:
        return _Stub(spec.name)

    def exec_module(self, module: types.ModuleType) -> None:
        pass


sys.meta_path.insert(0, _StubFinder())

import soundfile as sf  # noqa: E402
import torch  # noqa: E402
import torchaudio  # noqa: E402
from resemble_enhance.enhancer.inference import inference  # noqa: E402
from resemble_enhance.enhancer.train import Enhancer, HParams  # noqa: E402

INPUTS = ("silero-xenia-own", "piper-irina-yo")


def load_enhancer(device: str) -> Enhancer:
    hp = HParams.load(RUN_DIR)
    enhancer = Enhancer(hp)
    path = RUN_DIR / "ds/G/default/mp_rank_00_model_states.pt"
    state = torch.load(path, map_location="cpu", weights_only=True)["module"]
    enhancer.load_state_dict(state)
    return enhancer.eval().to(device)


def main() -> None:
    out_dir = HERE / "out" / "b"
    out_dir.mkdir(parents=True, exist_ok=True)
    device = "cuda"
    t = time.monotonic()
    enhancer = load_enhancer(device)
    enhancer.configurate_(nfe=32, solver="midpoint", lambd=0.5, tau=0.5)  # upstream defaults
    load_s = time.monotonic() - t

    rows = []
    for name in INPUTS:
        for src in sorted((SMOKE / "out/gpu").glob(f"{name}-[0-9][0-9].wav")):
            dwav, sr = torchaudio.load(str(src))
            dwav = dwav.mean(0)
            torch.cuda.synchronize()
            t = time.monotonic()
            with torch.inference_mode():
                hwav, new_sr = inference(model=enhancer, dwav=dwav, sr=sr, device=device)
            torch.cuda.synchronize()
            elapsed = time.monotonic() - t
            out = out_dir / f"{name}-enh-{src.stem[-2:]}.wav"
            sf.write(str(out), hwav.cpu().numpy(), new_sr)
            seconds = len(dwav) / sr
            rows.append(
                {
                    "variant": f"{name}-enh",
                    "source": str(src.relative_to(SMOKE.parent)),
                    "i": int(src.stem[-2:]),
                    "wav": str(out.relative_to(HERE)),
                    "in_sr": sr,
                    "out_sr": new_sr,
                    "seconds": seconds,
                    "synth_s": elapsed,
                    "rtf": elapsed / seconds,
                }
            )
        print(f"{name}: done")
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


if __name__ == "__main__":
    main()
