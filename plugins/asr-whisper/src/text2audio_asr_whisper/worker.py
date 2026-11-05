"""faster-whisper worker (protocol 1): audio files → spoken-form English transcripts.

The model loads from a pinned directory, never by name, so nothing is fetched. `small`
on CPU and `large-v3-turbo` on a GPU (§9.6); a GPU without the turbo files (`models pull
--tier fast`) uses `small`. QA compares against normalized text, so tokens with digits or
currency signs are suppressed ("nineteen eighty-four", not "1984"), and titles and Roman
numerals are spelled out afterwards (`spoken.py`).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import t2a_worker  # first: installs the network guard
from spoken import spoken  # next to this file: the worker runs as a script

WEIGHTS = "model.bin"
TURBO_MARKER = "preprocessor_config.json"
NUMERIC = re.compile(r"[0-9$£€%°]")
state: dict[str, Any] = {}


def choose_model(assets: list[dict[str, Any]], device: str) -> Path:
    found = [a for a in assets if WEIGHTS in a["files"]]
    if not found:
        raise RuntimeError(
            "faster-whisper files not found in the model store; "
            "run: text2audio models pull --lang en"
        )
    turbo = [a for a in found if TURBO_MARKER in a["files"]]
    small = [a for a in found if TURBO_MARKER not in a["files"]]
    gpu = device.startswith("cuda")
    return Path(((turbo or small) if gpu else (small or turbo))[0]["dir"])


def _preload_cuda() -> None:
    """CTranslate2 opens libcublas and libcudnn by name; the pip wheels put them in
    site-packages/nvidia/*/lib, which is not on the loader path. Loading them first
    with RTLD_GLOBAL lets those lookups find the already loaded libraries."""
    import ctypes
    import site

    for root in site.getsitepackages():
        for pattern in ("cublas/lib/libcublasLt.so.*", "cublas/lib/libcublas.so.*"):
            for lib in sorted(Path(root, "nvidia").glob(pattern)):
                ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
        for lib in sorted(Path(root, "nvidia").glob("cudnn/lib/libcudnn*.so.*")):
            ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)


def load(msg: dict[str, Any]) -> dict[str, Any]:
    device = msg.get("device") or "cpu"
    gpu = device.startswith("cuda")
    if gpu:
        _preload_cuda()
    from faster_whisper import WhisperModel

    model = WhisperModel(
        str(choose_model(msg.get("assets", []), device)),
        device="cuda" if gpu else "cpu",
        device_index=int(device.partition(":")[2] or 0) if gpu else 0,
        compute_type="float16" if gpu else "int8",
        cpu_threads=t2a_worker.threads(),
        local_files_only=True,
    )
    tokenizer = model.hf_tokenizer
    numeric = [
        i for i in range(tokenizer.get_vocab_size()) if NUMERIC.search(tokenizer.decode([i]))
    ]
    state.update(model=model, suppress=[-1, *numeric])
    return {"sample_rate": 16000}


def _transcribe(path: str) -> str:
    segments, _info = state["model"].transcribe(
        path,
        language="en",
        beam_size=5,
        condition_on_previous_text=False,
        without_timestamps=True,
        suppress_tokens=state["suppress"],
    )
    return spoken(" ".join(s.text.strip() for s in segments))


def transcribe(msg: dict[str, Any]) -> dict[str, Any]:
    if "model" not in state:
        raise RuntimeError("transcribe before load")
    return {"texts": [_transcribe(p) for p in msg["paths"]]}


if __name__ == "__main__":
    t2a_worker.serve("whisper", {"load": load, "transcribe": transcribe})
