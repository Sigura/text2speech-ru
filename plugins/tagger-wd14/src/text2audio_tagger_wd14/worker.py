"""WD14 tagger worker (protocol 1): image files → general tags above a threshold.

wd-swinv2-tagger-v3 as ONNX on CPU (onnxruntime): the image is padded to a white
square, resized to the model's input, fed as BGR float32 NHWC; `selected_tags.csv`
names the outputs (category 0 = general, 4 = character, 9 = rating).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import t2a_worker  # first: installs the network guard

GENERAL = "0"
state: dict[str, Any] = {}


def _snapshot(msg: dict[str, Any]) -> Path:
    for asset in msg.get("assets", []):
        if "selected_tags.csv" in asset["files"]:
            return Path(asset["dir"])
    raise RuntimeError(
        "WD14 tagger files not found in the model store; run: text2audio models pull"
    )


def load(msg: dict[str, Any]) -> dict[str, Any]:
    import onnxruntime as ort

    root = _snapshot(msg)
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = t2a_worker.threads()
    session = ort.InferenceSession(
        str(root / "model.onnx"), opts, providers=["CPUExecutionProvider"]
    )
    with (root / "selected_tags.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    state.update(session=session, rows=rows, size=session.get_inputs()[0].shape[1])
    return {}


def _input(path: str) -> Any:
    import numpy as np
    from PIL import Image

    size = state["size"]
    with Image.open(path) as src:
        img = src.convert("RGBA")
    flat = Image.new("RGBA", img.size, (255, 255, 255, 255))
    flat.alpha_composite(img)
    side = max(flat.size)
    square = Image.new("RGB", (side, side), (255, 255, 255))
    square.paste(flat.convert("RGB"), ((side - img.width) // 2, (side - img.height) // 2))
    square = square.resize((size, size), Image.Resampling.BICUBIC)
    return np.asarray(square, dtype=np.float32)[:, :, ::-1][None]


def tag(msg: dict[str, Any]) -> dict[str, Any]:
    if "session" not in state:
        raise RuntimeError("tag before load")
    threshold = float(msg.get("threshold", 0.35))
    session, rows = state["session"], state["rows"]
    name = session.get_inputs()[0].name
    out = []
    for path in msg["paths"]:
        probs = session.run(None, {name: _input(path)})[0][0]
        found = [
            (float(p), row["name"].replace("_", " "))
            for p, row in zip(probs, rows, strict=True)
            if row["category"] == GENERAL and p >= threshold
        ]
        out.append([t for _, t in sorted(found, reverse=True)])
    return {"tags": out}


if __name__ == "__main__":
    t2a_worker.serve("wd14", {"load": load, "tag": tag})
