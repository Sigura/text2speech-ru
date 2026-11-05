"""Spike, OFFLINE step: metrics of the M4 benchmark (SPEC §6.6.5 protocol, step 2).

    uv run --python 3.11 python spikes/en-bench/score.py

Per system, on the segments every system read: faster-whisper WER / CER against the
segment's `speech_text` with the en profile's folding, the QA gate's own verdict (its
WER-and-CER rule and the audio checks of `qa.checks`, e.g. chars per second), RTF and
VRAM. Writes out/report.json and out/report.md.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from text2audio_lang_en import PROFILE

from text2audio.qa.checks import check_file
from text2audio.qa.wer import cer, wer
from text2audio.text.segment import read_manifest

HERE = Path(__file__).parent
OUT = HERE / "out"
LABEL = {
    "kokoro": "Kokoro `af_heart` / `am_michael` (fast)",
    "chatterbox": "Chatterbox, cloned from Kokoro (expressive)",
    "indextts": "IndexTTS-2, cloned from Kokoro (spike)",
}
QA = PROFILE.qa


def corpus_wer(per: list[dict[str, Any]]) -> float:
    return sum(p["errors"] for p in per) / max(1, sum(p["words"] for p in per))


def vram_mb(name: str) -> float | None:
    """Increase of the whole GPU's used memory during the run (nvidia-smi samples)."""
    path = OUT / f"vram-{name}.txt"
    if not path.exists():
        return None
    samples = [int(x) for x in path.read_text().split() if x.strip().isdigit()]
    return float(max(samples) - samples[0]) if samples else None


def speed(name: str, runs: dict[str, Any]) -> dict[str, Any]:
    if name in runs:
        report = runs[name]
        return {
            "rtf": report["synthesis"]["rtf"],
            "rtf_wall": report["rtf_wall"],
            "workers": report["synthesis"]["workers"],
        }
    out: dict[str, Any] = {}
    for tag in ("t1", "t0"):
        path = OUT / f"timing-{name}-{tag}.json"
        if path.exists():
            t = json.loads(path.read_text())
            spent = sum(r["seconds"] for r in t["rows"])
            audio = sum(r["audio_s"] for r in t["rows"])
            out["rtf" if tag == "t1" else "rtf_t0"] = spent / audio
            out[f"segments_{tag}"] = len(t["rows"])
            if tag == "t1":
                out["peak_alloc_mb"] = t["peak_vram_mb"]
                out["load_s"] = t["load_s"]
    return out


def main() -> None:
    segs = json.loads((OUT / "segments.json").read_text("utf-8"))
    manifest = {s.id: s for s in read_manifest(OUT / "run-t0/bench/_work/segments.jsonl")}
    heard = json.loads((OUT / "transcripts.json").read_text("utf-8"))
    runs = json.loads((OUT / "runs.json").read_text("utf-8"))
    report: dict[str, Any] = {"systems": {}}
    systems = [n for n in LABEL if n in heard]
    common = set.intersection(*(set(heard[n]) for n in systems))
    for name in systems:
        per: list[dict[str, Any]] = []
        for s in segs:
            if s["id"] not in common:
                continue
            text = manifest[s["id"]].speech_text
            got = heard[name][s["id"]]
            w = wer(text, got, QA.fold)
            c = cer(text, got, QA.fold)
            flags = check_file(OUT / "wav" / name / f"{s['id']}.wav", text, QA.chars_per_sec)
            if w.wer > QA.wer_threshold and c > QA.cer_threshold:
                flags.insert(0, "wer")
            per.append(
                {
                    "id": s["id"],
                    "category": s["category"],
                    "wer": w.wer,
                    "errors": w.errors,
                    "words": w.words,
                    "cer": c,
                    "flags": flags,
                    "text": text,
                    "heard": got,
                }
            )
        by_cat: dict[str, Any] = {}
        for cat in sorted({p["category"] for p in per}):
            sub = [p for p in per if p["category"] == cat]
            by_cat[cat] = {"segments": len(sub), "wer": corpus_wer(sub)}
        flag_counts: dict[str, int] = {}
        for p in per:
            for f in p["flags"]:
                flag_counts[f] = flag_counts.get(f, 0) + 1
        report["systems"][name] = {
            "label": LABEL[name],
            "segments": len(per),
            "wer": corpus_wer(per),
            "mean_cer": statistics.fmean(p["cer"] for p in per) if per else None,
            "failed": sum(1 for p in per if p["flags"]),
            "flags": flag_counts,
            "by_category": by_cat,
            "speed": speed(name, runs),
            "vram_mb": vram_mb(name),
            "worst": sorted(per, key=lambda p: -p["wer"])[:8],
            "flagged": [p for p in per if p["flags"]],
        }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    (OUT / "report.md").write_text(markdown(report), "utf-8")
    print(markdown(report))


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# en-bench report",
        "",
        f"QA rule: WER > {QA.wer_threshold} and CER > {QA.cer_threshold}, "
        f"chars/s in {QA.chars_per_sec}.",
        "",
        "| System | segments | WER | mean CER | QA-failed | flags | RTF | RTF T0 | VRAM |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in report["systems"].values():
        sp = s["speed"]
        rtf_t0 = f"{sp['rtf_t0']:.2f}" if sp.get("rtf_t0") else "—"
        vram = s["vram_mb"] or sp.get("peak_alloc_mb")
        flags = ", ".join(f"{k} {v}" for k, v in sorted(s["flags"].items())) or "—"
        lines.append(
            f"| {s['label']} | {s['segments']} | {s['wer']:.3f} | {s['mean_cer']:.3f} | "
            f"{s['failed']} | {flags} | {sp.get('rtf') or 0:.3f} | {rtf_t0} | "
            f"{f'{vram:.0f} MB' if vram else '—'} |"
        )
    cats = sorted(next(iter(report["systems"].values()))["by_category"])
    lines += ["", "WER by category:", "", "| System | " + " | ".join(cats) + " |"]
    lines.append("|---" * (len(cats) + 1) + "|")
    for s in report["systems"].values():
        cells = [f"{s['by_category'][c]['wer']:.3f}" for c in cats]
        lines.append(f"| {s['label']} | " + " | ".join(cells) + " |")
    for s in report["systems"].values():
        lines += ["", f"## Worst segments: {s['label']}", ""]
        for p in s["worst"]:
            if p["wer"] == 0:
                break
            lines.append(f"- `{p['id']}` WER {p['wer']:.2f} {p['flags'] or ''}")
            lines.append(f"  - text: {p['text']}")
            lines.append(f"  - heard: {p['heard']}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
