"""Spike, OFFLINE step: WER/RTF for variants B and D against A (Silero xenia), plus a
listening set. Runs in the ru-smoke env (GigaAM-v3 `rnnt`, offline guard):

    CUDA_VISIBLE_DEVICES=0 uv run --project ../ru-smoke python score.py

Writes out/report.json, out/report.md and out/listen/ (A/B/D per sentence).
"""

from __future__ import annotations

import json
import shutil
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).parent
SMOKE = HERE.parent / "ru-smoke"
sys.path.insert(0, str(SMOKE))

import jiwer  # noqa: E402
import numpy as np  # noqa: E402
import run as smoke  # noqa: E402  (offline guard, HF_HOME, norm, wav_seconds)
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from transformers import AutoModel  # noqa: E402

DIGITS = "Numbers as digits (normalization is not in the spike: expect errors here)"


def hf_share(path: Path, cutoff: float = 8000.0) -> float:
    """Share of spectral energy above `cutoff` Hz: a crude bandwidth/brightness gauge."""
    audio, rate = sf.read(str(path))
    spec = np.abs(np.fft.rfft(audio)) ** 2
    freqs = np.fft.rfftfreq(len(audio), 1 / rate)
    return float(spec[freqs >= cutoff].sum() / spec.sum())


def main() -> None:
    smoke_rows = json.loads((SMOKE / "out/gpu/report.json").read_text())["rows"]
    group_of = {r["i"]: r["group"] for r in smoke_rows}
    text_of = {r["i"]: r["text"] for r in smoke_rows}
    code_texts = [
        line.strip()
        for line in (HERE / "codeswitch/sentences.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]

    asr = AutoModel.from_pretrained("ai-sage/GigaAM-v3", revision="rnnt", trust_remote_code=True)
    asr = asr.to("cuda").eval()

    def hear(wav: Path) -> str:
        with torch.inference_mode():
            return str(asr.transcribe(str(wav)))

    rows = []
    for r in smoke_rows:
        if r["system"] in ("silero-xenia-own", "piper-irina-yo"):
            wav = SMOKE / r["wav"]
            rows.append(
                {
                    "variant": r["system"],
                    "set": "main",
                    "i": r["i"],
                    "text": r["text"],
                    "asr": r["asr"],
                    "wer": r["wer"],
                    "rtf": r["rtf"],
                    "hf8k": hf_share(wav),
                    "wav": str(wav.relative_to(HERE.parent)),
                }
            )
    for i, text in enumerate(code_texts):
        wav = HERE / "codeswitch/out" / f"silero-xenia-{i:02d}.wav"
        if wav.exists():
            heard = hear(wav)
            rows.append(
                {
                    "variant": "silero-xenia-own",
                    "set": "code",
                    "i": i,
                    "text": text,
                    "asr": heard,
                    "wer": jiwer.wer(smoke.norm(text), smoke.norm(heard)),
                    "rtf": None,
                    "hf8k": hf_share(wav),
                    "wav": str(wav.relative_to(HERE.parent)),
                }
            )

    base_rtf = {(r["system"], r["i"]): r["rtf"] for r in smoke_rows}
    for sub in ("b", "c", "d"):
        timing = HERE / "out" / sub / "timings.json"
        if not timing.exists():
            continue
        for t in json.loads(timing.read_text())["rows"]:
            wav = HERE / t["wav"]
            text = t.get("text") or text_of[t["i"]]
            heard = hear(wav)
            rtf = t["rtf"]
            if sub in ("b", "c"):  # two-stage cost = TTS + polish
                source = t.get("source_variant") or t["variant"].removesuffix("-enh")
                rtf += base_rtf[(source, t["i"])]
            rows.append(
                {
                    "variant": t["variant"],
                    "set": t.get("set", "main"),
                    "i": t["i"],
                    "text": text,
                    "asr": heard,
                    "wer": jiwer.wer(smoke.norm(text), smoke.norm(heard)),
                    "rtf": rtf,
                    "hf8k": hf_share(wav),
                    "wav": str(wav.relative_to(HERE.parent)),
                }
            )
        print(f"{sub}: scored")

    summary: dict[str, dict[str, float | None]] = {}
    for v in dict.fromkeys(r["variant"] for r in rows):
        main_rows = [r for r in rows if r["variant"] == v and r["set"] == "main"]
        code_rows = [r for r in rows if r["variant"] == v and r["set"] == "code"]
        other = [r for r in main_rows if group_of[r["i"]] != DIGITS]
        rtfs = [r["rtf"] for r in main_rows if r["rtf"] is not None]
        summary[v] = {
            "wer_all": statistics.mean(r["wer"] for r in main_rows),
            "wer_no_digits": statistics.mean(r["wer"] for r in other),
            "wer_digits": statistics.mean(
                r["wer"] for r in main_rows if group_of[r["i"]] == DIGITS
            ),
            "wer_codeswitch": statistics.mean(r["wer"] for r in code_rows) if code_rows else None,
            "rtf_median": statistics.median(rtfs) if rtfs else None,
            "hf8k_median": statistics.median(r["hf8k"] for r in main_rows),
        }
    report = {"network_attempts": smoke.NETWORK_ATTEMPTS, "summary": summary, "rows": rows}
    (HERE / "out/report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))

    def cell(x: float | None, fmt: str) -> str:
        return "—" if x is None else format(x, fmt)

    lines = [
        "# ru-polish: B (enhance) and D (Chatterbox Multilingual) vs A (Silero)",
        "",
        f"Network attempts: {len(smoke.NETWORK_ATTEMPTS)}",
        "",
        "| variant | WER no digits | WER digits | WER code-switch | RTF (GPU) | HF>8k |",
        "|---|---|---|---|---|---|",
    ]
    for v, s in summary.items():
        lines.append(
            f"| {v} | {cell(s['wer_no_digits'], '.3f')} | {cell(s['wer_digits'], '.2f')}"
            f" | {cell(s['wer_codeswitch'], '.2f')} | {cell(s['rtf_median'], '.3f')}"
            f" | {cell(s['hf8k_median'], '.4f')} |"
        )
    lines += ["", "## Errors (WER > 0, digits group excluded)", ""]
    lines += [
        f"- `{r['variant']}` {r['set']} #{r['i']}: «{r['text']}» → «{r['asr']}» ({r['wer']:.2f})"
        for r in rows
        if r["wer"] > 0 and not (r["set"] == "main" and group_of[r["i"]] == DIGITS)
    ]
    (HERE / "out/report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[: 6 + len(summary)]))

    listen = HERE / "out/listen"
    shutil.rmtree(listen, ignore_errors=True)
    listen.mkdir(parents=True)
    labels = {
        "silero-xenia-own": "A-silero",
        "silero-xenia-own-enh": "B-silero-enhanced",
        "silero-vc-builtin": "C-silero-vc-builtin",
        "silero-vc-xenia": "C2-silero-vc-xenia-ref",
        "chatterbox-default": "D1-chatterbox-builtin",
        "chatterbox-xenia": "D2-chatterbox-xenia-ref",
        "chatterbox-xenia-ru": "D3-chatterbox-xenia-ref-stressed",
        "piper-irina-yo": "P-piper",
        "piper-irina-yo-enh": "P-piper-enhanced",
    }
    readme = [
        "# Listening set: A vs B vs D",
        "",
        "Per sentence, compare naturalness, stress (homographs, ё) and artefacts.",
        "A = Silero xenia; B = A + resemble-enhance; C = A converted to Chatterbox's",
        "built-in voice (Chatterbox VC); D1 = Chatterbox TTS, same built-in voice;",
        "D2 = Chatterbox cloned from xenia; D3 = D2 + RUAccent stress marks;",
        "P = Piper irina (22 kHz) with/without enhancement.",
        "",
    ]
    for set_name in ("main", "code"):
        for i in sorted({r["i"] for r in rows if r["set"] == set_name}):
            mine = [r for r in rows if r["set"] == set_name and r["i"] == i]
            readme += [f"## {set_name} {i:02d}. {mine[0]['text']}", ""]
            for r in mine:
                name = f"{set_name}-{i:02d}-{labels[r['variant']]}.wav"
                shutil.copy(HERE.parent / r["wav"], listen / name)
                readme.append(f"- `{name}` — ASR «{r['asr']}»")
            readme.append("")
    (listen / "README.md").write_text("\n".join(readme), encoding="utf-8")


if __name__ == "__main__":
    main()
