"""RUAccent worker: text in, text with `+` stress marks and ё out (protocol 1).

RUAccent downloads unpinned files on first use and writes Koziev's POS tagger into its
own package directory (spike `ru-smoke`). This worker never lets it reach those
branches: the pinned store snapshot is the `workdir` (so its `dictionary/` and `nn/`
checks pass), and the snapshot's `koziev/` is linked into the package before `load()`.
With `T2A_NO_NETWORK=1` any remaining attempt fails instead of fetching.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import t2a_worker  # first: installs the network guard

state: dict[str, Any] = {}


def _snapshot(msg: dict[str, Any]) -> Path:
    for asset in msg.get("assets", []):
        root = Path(asset["dir"])
        if (root / "dictionary").is_dir() and (root / "koziev").is_dir():
            return root
    raise RuntimeError(
        "RUAccent files not found in the model store; run: text2audio models pull --lang ru"
    )


def _link_koziev(package: Path, snapshot: Path) -> None:
    link = package / "koziev"
    target = snapshot / "koziev"
    if link.is_symlink() and link.resolve() == target.resolve():
        return
    if link.is_symlink() or link.is_file():
        link.unlink()
    elif link.exists():
        raise RuntimeError(f"{link} exists and is not ours; remove it to use the pinned copy")
    os.symlink(target, link, target_is_directory=True)


def _limit_onnx_threads(n: int) -> None:
    """onnxruntime sizes its pools from the machine's cores, not the affinity mask:
    with RUAccent's five sessions that is hundreds of spinning threads on 2 CPUs."""
    import onnxruntime as ort

    original = ort.InferenceSession

    class Session(original):  # type: ignore[misc, valid-type]
        def __init__(self, *args: Any, sess_options: Any = None, **kwargs: Any) -> None:
            opts = sess_options or ort.SessionOptions()
            opts.intra_op_num_threads = n
            opts.inter_op_num_threads = 1
            opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
            super().__init__(*args, sess_options=opts, **kwargs)

    ort.InferenceSession = Session


def load(msg: dict[str, Any]) -> dict[str, Any]:
    _limit_onnx_threads(t2a_worker.threads())
    import ruaccent
    from ruaccent import RUAccent

    snapshot = _snapshot(msg)
    _link_koziev(Path(ruaccent.__file__).parent, snapshot)
    model = RUAccent()
    model.load(
        omograph_model_size="turbo3.1",
        use_dictionary=True,
        workdir=str(snapshot),
        device="CUDA" if str(msg.get("device", "cpu")).startswith("cuda") else "CPU",
    )
    state["model"] = model
    return {}


def process(msg: dict[str, Any]) -> dict[str, Any]:
    model = state.get("model")
    if model is None:
        raise RuntimeError("process before load")
    return {"texts": [model.process_all(t) if t.strip() else t for t in msg["texts"]]}


if __name__ == "__main__":
    t2a_worker.serve("ruaccent", {"load": load, "process": process})
