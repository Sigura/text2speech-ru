"""Spike, OFFLINE step: objective quality proxies for every WAV in out/report.json.

    CUDA_VISIBLE_DEVICES=0 uv run --project chatterbox python quality.py

- UTMOS22 strong (SpeechMOS @ v1.2.0, weights v1.0.0): predicted naturalness MOS 1–5.
  Trained on English listening tests, so on Russian it is a proxy, not a verdict.
- Pitch spread: std of F0 in semitones over voiced frames (librosa pYIN). Low = flat,
  monotone reading; the thing voice conversion cannot fix.
Writes out/quality.json and out/quality.md.
"""

from __future__ import annotations

import json
import socket
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).parent
UTMOS_CODE = HERE / "models/utmos/SpeechMOS-ed25eacbfa42b99156c36ebec67a733b5dbb9b79"
UTMOS_WEIGHTS = HERE / "models/torch/hub/checkpoints/utmos22_strong_step7459_v1.pt"

NETWORK_ATTEMPTS: list[str] = []
_connect = socket.socket.connect


def _guarded_connect(self: socket.socket, address: object) -> None:
    host = address[0] if isinstance(address, tuple) else None
    if host is not None and host not in ("127.0.0.1", "::1", "localhost"):
        NETWORK_ATTEMPTS.append(repr(address))
        raise OSError(f"spike offline guard: connection to {address!r} refused")
    _connect(self, address)  # type: ignore[arg-type]


socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
sys.path.insert(0, str(UTMOS_CODE))

import librosa  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402
from speechmos.utmos22.strong.model import UTMOS22Strong  # noqa: E402

RATE = 16000


def pitch_spread(audio: np.ndarray) -> float:
    f0, voiced, _ = librosa.pyin(audio, fmin=70, fmax=500, sr=RATE)
    f0 = f0[voiced & ~np.isnan(f0)]
    if len(f0) < 10:
        return float("nan")
    return float(np.std(12 * np.log2(f0 / np.median(f0))))


def main() -> None:
    report = json.loads((HERE / "out/report.json").read_text())
    model = UTMOS22Strong()
    model.load_state_dict(torch.load(UTMOS_WEIGHTS, map_location="cpu", weights_only=True))
    model = model.to("cuda").eval()

    rows = []
    for r in report["rows"]:
        audio, _ = librosa.load(str(HERE.parent / r["wav"]), sr=RATE)
        with torch.inference_mode():
            mos = float(model(torch.from_numpy(audio)[None].to("cuda"), RATE).item())
        rows.append({**r, "utmos": mos, "pitch_st": pitch_spread(audio)})
    print("scored", len(rows))

    lines = [
        "# ru-polish: objective quality proxies",
        "",
        f"Network attempts: {len(NETWORK_ATTEMPTS)}",
        "",
        "| variant | set | UTMOS (median) | UTMOS (min) | pitch spread, st (median) |",
        "|---|---|---|---|---|",
    ]
    summary = {}
    for v in dict.fromkeys(r["variant"] for r in rows):
        for set_name in ("main", "code"):
            mine = [r for r in rows if r["variant"] == v and r["set"] == set_name]
            if not mine:
                continue
            spreads = [r["pitch_st"] for r in mine if not np.isnan(r["pitch_st"])]
            s = {
                "utmos_median": statistics.median(r["utmos"] for r in mine),
                "utmos_min": min(r["utmos"] for r in mine),
                "pitch_st_median": statistics.median(spreads),
            }
            summary[f"{v}/{set_name}"] = s
            lines.append(
                f"| {v} | {set_name} | {s['utmos_median']:.2f} | {s['utmos_min']:.2f}"
                f" | {s['pitch_st_median']:.2f} |"
            )
    (HERE / "out/quality.json").write_text(
        json.dumps(
            {"network_attempts": NETWORK_ATTEMPTS, "summary": summary, "rows": rows},
            indent=2,
            ensure_ascii=False,
        )
    )
    (HERE / "out/quality.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
