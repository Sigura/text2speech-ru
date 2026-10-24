"""Spike, OFFLINE step: Russian text → (RUAccent) → TTS → WAV → GigaAM → WER, RTF.

    uv run python run.py                          # GPU (first visible CUDA device)
    CUDA_VISIBLE_DEVICES="" taskset -c 0-3 uv run python run.py --device cpu --tag t0

Network is refused in-process (any non-loopback connect raises and is recorded),
because AppArmor blocks `unshare -rn` on the development machine. Outputs go to
out/<tag>/: one WAV per system and sentence, report.json and report.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import statistics
import time
import wave
from pathlib import Path

HERE = Path(__file__).parent
MODELS = HERE / "models"
os.environ.update(
    HF_HOME=str(MODELS / "hf"),
    TORCH_HOME=str(MODELS / "torch"),
    HF_HUB_OFFLINE="1",
    TRANSFORMERS_OFFLINE="1",
    HF_DATASETS_OFFLINE="1",
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

import jiwer  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402

FEMALE_SILERO = ("xenia", "baya", "kseniya")
SILERO_RATE = 48000


def load_sentences() -> list[tuple[str, str]]:
    group, rows = "misc", []
    for line in (HERE / "sentences.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            group = line.lstrip("# ").strip() or group
        elif line.strip():
            rows.append((group, line.strip()))
    return [r for r in rows if not r[0].startswith("Throwaway")]


def norm(text: str) -> str:
    text = text.lower().replace("ё", "е").replace("+", "")
    text = re.sub(r"[^\w\s%]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def wav_seconds(path: Path) -> float:
    info = sf.info(str(path))
    return info.frames / info.samplerate


class Silero:
    def __init__(self, device: str) -> None:
        pkg = torch.package.PackageImporter(str(MODELS / "silero" / "v5_6_ru.pt"))
        self.model = pkg.load_pickle("tts_models", "model")
        self.model.to(torch.device(device))

    def synth(self, text: str, speaker: str, out: Path, own_stress: bool) -> None:
        audio = self.model.apply_tts(
            text=text,
            speaker=speaker,
            sample_rate=SILERO_RATE,
            put_accent=True,  # only fills words without a "+" mark
            put_stress_homo=own_stress,
            put_yo=own_stress,
            put_yo_homo=own_stress,
        )
        sf.write(str(out), audio.cpu().numpy(), SILERO_RATE)


class Piper:
    def __init__(self, device: str) -> None:
        from piper import PiperVoice

        base = next((MODELS / "hf" / "hub").glob("models--rhasspy--piper-voices/snapshots/*"))
        onnx = base / "ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx"
        self.voice = PiperVoice.load(str(onnx), use_cuda=device.startswith("cuda"))

    def synth(self, text: str, out: Path) -> None:
        with wave.open(str(out), "wb") as w:
            self.voice.synthesize_wav(text, w)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    ap.add_argument("--tag", default="gpu")
    args = ap.parse_args()
    torch.set_num_threads(len(os.sched_getaffinity(0)))
    out_dir = HERE / "out" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)

    from ruaccent import RUAccent
    from transformers import AutoModel

    t = time.monotonic()
    accent = RUAccent()
    accent.load(
        omograph_model_size="turbo3.1",
        use_dictionary=True,
        workdir=str(MODELS / "ruaccent"),
        device="CUDA" if args.device.startswith("cuda") else "CPU",
    )
    silero, piper = Silero(args.device), Piper(args.device)
    asr = AutoModel.from_pretrained("ai-sage/GigaAM-v3", revision="rnnt", trust_remote_code=True)
    asr = asr.to(args.device).eval()
    load_s = time.monotonic() - t

    sentences = load_sentences()
    stressed = {s: accent.process_all(s) for _, s in sentences}
    systems: list[tuple[str, object]] = []
    for spk in FEMALE_SILERO:
        systems.append((f"silero-{spk}-own", lambda s, o, spk=spk: silero.synth(s, spk, o, True)))
        systems.append(
            (f"silero-{spk}-ruaccent",
             lambda s, o, spk=spk: silero.synth(stressed[s], spk, o, False))
        )
    systems.append(("piper-irina", lambda s, o: piper.synth(s, o)))
    systems.append(("piper-irina-yo", lambda s, o: piper.synth(stressed[s].replace("+", ""), o)))

    rows = []
    for name, synth in systems:
        for i, (group, text) in enumerate(sentences):
            wav = out_dir / f"{name}-{i:02d}.wav"
            t = time.monotonic()
            synth(text, wav)  # type: ignore[operator]
            elapsed = time.monotonic() - t
            with torch.inference_mode():
                heard = asr.transcribe(str(wav))
            rows.append({
                "system": name, "group": group, "i": i, "text": text,
                "input": stressed[text] if "ruaccent" in name else text,
                "asr": heard, "wer": jiwer.wer(norm(text), norm(heard)),
                "rtf": elapsed / wav_seconds(wav), "wav": str(wav.relative_to(HERE)),
            })
        print(f"{name}: done")

    summary: dict[str, dict[str, float]] = {}
    for name, _ in systems:
        mine = [r for r in rows if r["system"] == name]
        groups = {r["group"] for r in mine}
        summary[name] = {
            "wer_all": statistics.mean(r["wer"] for r in mine),
            **{f"wer[{g}]": statistics.mean(r["wer"] for r in mine if r["group"] == g)
               for g in sorted(groups)},
            "rtf": statistics.median(r["rtf"] for r in mine),
        }
    report = {"device": args.device, "load_s": load_s, "network_attempts": NETWORK_ATTEMPTS,
              "threads": torch.get_num_threads(), "summary": summary, "rows": rows}
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    groups = sorted({r["group"] for r in rows})
    lines = [f"# ru-smoke ({args.tag}, {args.device})", "",
             f"Network attempts: {len(NETWORK_ATTEMPTS)}; load {load_s:.1f} s", "",
             "| system | WER | " + " | ".join(groups) + " | RTF |",
             "|---|---|" + "---|" * len(groups) + "---|"]
    for name, s in summary.items():
        cells = " | ".join(f"{s[f'wer[{g}]']:.2f}" for g in groups)
        lines.append(f"| {name} | {s['wer_all']:.3f} | {cells} | {s['rtf']:.3f} |")
    lines += ["", "## Stress marks from RUAccent", ""]
    lines += [f"- {stressed[s]}" for _, s in sentences]
    lines += ["", "## Errors (WER > 0)", ""]
    lines += [f"- `{r['system']}` #{r['i']}: «{r['text']}» → «{r['asr']}» ({r['wer']:.2f})"
              for r in rows if r["wer"] > 0]
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:6 + len(summary)]))


if __name__ == "__main__":
    main()
