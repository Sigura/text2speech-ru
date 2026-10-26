"""Spike, OFFLINE step: metrics of the M2 benchmark (SPEC §6.6.5 protocol, step 2).

    uv run --python 3.11 python spikes/ru-bench/score.py

Text metrics (no audio): ru-pack normalization exact match on the number items, ё
restoration and RUAccent homograph stress in the T0 `text_stress`. Audio metrics per
system: GigaAM WER / CER (the QA gate's folding and fail rule), RTF, VRAM. Writes
out/report.json and out/report.md.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from text2audio_lang_ru.normalize import RussianNormalizer

from text2audio.qa.wer import cer, fold, wer

HERE = Path(__file__).parent
OUT = HERE / "out"
FOLD = {"ё": "е"}
WER_FAIL, CER_FAIL = 0.18, 0.10  # ru profile threshold and the gate's CER rule (§6.9)
# Which pipeline text each system read (synth.py): T0 = Latin words transliterated.
REFERENCE = {
    "silero": "t0_norm",
    "espeech": "t0_norm",
    "hybrid": "t1_norm",
    "qwen3": "t1_norm",
    "xtts": "t1_norm",
}
LABEL = {
    "silero": "T0 Silero xenia",
    "hybrid": "T1 hybrid (Silero → Chatterbox VC)",
    "qwen3": "Qwen3-TTS 1.7B Base",
    "espeech": "ESpeech-TTS-1 SFT 256K",
    "xtts": "XTTS-v2",
}


def items() -> list[dict[str, str]]:
    lines = (HERE / "set.tsv").read_text("utf-8").splitlines()
    keys = lines[0].split("\t")
    return [dict(zip(keys, line.split("\t"), strict=True)) for line in lines[1:]]


def stressed_words(text: str) -> list[str]:
    """Words of a `text_stress` string, "+" marks kept, punctuation dropped."""
    return [w.strip('.,!?;:«»"()—–…').casefold() for w in text.split()]


def text_metrics(rows: list[dict[str, str]], segs: list[dict[str, Any]]) -> dict[str, Any]:
    norm = RussianNormalizer()
    numbers = [r for r in rows if r["category"] == "number"]
    misses = []
    for r in numbers:
        got = norm.normalize(r["text"]).text
        if got != r["expected"]:
            misses.append({"id": r["id"], "text": r["text"], "expected": r["expected"], "got": got})
    stress_of: dict[str, str] = {}
    for s in segs:
        stress_of[s["item"]] = (stress_of.get(s["item"], "") + " " + s["t0_stress"]).strip()

    yo_total, yo_missed = 0, []
    for r in (r for r in rows if r["category"] == "yo"):
        said = [w.replace("+", "") for w in stressed_words(stress_of[r["id"]])]
        for word in r["expected"].split(","):
            yo_total += 1
            if word.casefold() not in said:
                yo_missed.append({"id": r["id"], "word": word})

    homograph_wrong = []
    homographs = [r for r in rows if r["category"] == "homograph"]
    for r in homographs:
        target = r["expected"].casefold()
        bare = fold(target, FOLD)
        found = [w for w in stressed_words(stress_of[r["id"]]) if fold(w, FOLD) == bare]
        if target not in found and target.replace("+ё", "ё") not in found:
            homograph_wrong.append({"id": r["id"], "expected": r["expected"], "got": found})
    return {
        "normalization": {
            "items": len(numbers),
            "exact": len(numbers) - len(misses),
            "rate": 1 - len(misses) / len(numbers),
            "misses": misses,
        },
        "yo": {
            "words": yo_total,
            "restored": yo_total - len(yo_missed),
            "rate": 1 - len(yo_missed) / yo_total,
            "missed": yo_missed,
        },
        "homograph_text": {
            "items": len(homographs),
            "right": len(homographs) - len(homograph_wrong),
            "rate": 1 - len(homograph_wrong) / len(homographs),
            "wrong": homograph_wrong,
        },
    }


def corpus_wer(per: list[dict[str, Any]]) -> float:
    return sum(p["errors"] for p in per) / max(1, sum(p["words"] for p in per))


def vram_mb(name: str) -> float | None:
    path = OUT / f"vram-{name}.txt"
    if not path.exists():
        return None
    samples = [int(x) for x in path.read_text().split() if x.strip().isdigit()]
    return float(max(samples) - samples[0]) if samples else None


def vram(name: str, sp: dict[str, Any]) -> dict[str, Any]:
    # Challenger sampler logs were corrupted by leaked samplers; synth.py's torch peak is
    # recorded per run instead (it excludes the CUDA context, ~0.5 GB).
    if name in ("silero", "hybrid"):
        return {"vram_mb": vram_mb(name), "vram_source": "gpu"}
    return {"vram_mb": sp.get("peak_alloc_mb"), "vram_source": "torch peak"}


def speed(name: str, runs: dict[str, Any]) -> dict[str, Any]:
    if name in ("silero", "hybrid"):
        report = runs[name]
        out = {
            "rtf_wall": report["rtf_wall"],
            "synthesis_rtf": report["synthesis"]["rtf"],
            "workers": report["synthesis"]["workers"],
        }
        if report.get("polish"):
            out |= {
                "polish_rtf": report["polish"]["rtf"],
                "polish_workers": report["polish"]["workers"],
            }
            out["rtf"] = (report["synthesis"]["rtf"] or 0) + (report["polish"]["rtf"] or 0)
        else:
            out["rtf"] = report["synthesis"]["rtf"]
        return out
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
    rows = items()
    segs = json.loads((OUT / "segments.json").read_text("utf-8"))
    heard = json.loads((OUT / "transcripts.json").read_text("utf-8"))
    runs = json.loads((OUT / "runs.json").read_text("utf-8"))
    report: dict[str, Any] = {"text": text_metrics(rows, segs), "systems": {}}
    report["runs"] = {"silero": runs["silero"]["timings_s"], "hybrid": runs["hybrid"]["timings_s"]}
    # Systems are compared on the segments every one of them read (`synth.py --planned`).
    common = set.intersection(*(set(heard[n]) for n in LABEL if n in heard))
    for name in LABEL:
        if name not in heard:
            continue
        per: list[dict[str, Any]] = []
        for s in segs:
            if s["id"] not in common:
                continue
            w = wer(s[REFERENCE[name]], heard[name][s["id"]], FOLD)
            c = cer(s[REFERENCE[name]], heard[name][s["id"]], FOLD)
            per.append(
                {
                    "id": s["id"],
                    "category": s["category"],
                    "wer": w.wer,
                    "errors": w.errors,
                    "words": w.words,
                    "cer": c,
                    "failed": w.wer > WER_FAIL and c > CER_FAIL,
                    "heard": heard[name][s["id"]],
                }
            )
        by_cat: dict[str, Any] = {}
        for cat in sorted({p["category"] for p in per}):
            sub = [p for p in per if p["category"] == cat]
            by_cat[cat] = {
                "segments": len(sub),
                "wer": corpus_wer(sub),
                "failed": sum(p["failed"] for p in sub),
            }
        report["systems"][name] = {
            "label": LABEL[name],
            "segments": len(per),
            "wer": corpus_wer(per),
            "wer_no_latin": corpus_wer([p for p in per if p["category"] != "latin"]),
            "mean_cer": statistics.fmean(p["cer"] for p in per) if per else None,
            "failed": sum(p["failed"] for p in per),
            "by_category": by_cat,
            "speed": (sp := speed(name, runs)),
            **vram(name, sp),
            "worst": sorted(per, key=lambda p: -p["cer"])[:8],
        }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    (OUT / "report.md").write_text(markdown(report), "utf-8")
    print(markdown(report))


def markdown(report: dict[str, Any]) -> str:
    t = report["text"]
    lines = [
        "# ru-bench report",
        "",
        f"Normalization exact match: {t['normalization']['exact']}/{t['normalization']['items']}"
        f" ({t['normalization']['rate']:.1%})  ",
        f"ё restored (T0 text_stress): {t['yo']['restored']}/{t['yo']['words']}"
        f" ({t['yo']['rate']:.1%})  ",
        f"Homograph stress in text (RUAccent): {t['homograph_text']['right']}/"
        f"{t['homograph_text']['items']} ({t['homograph_text']['rate']:.1%})",
        "",
        "| System | Segments | WER | WER w/o Latin | mean CER | QA-failed | RTF (1 worker) "
        "| RTF T0 | VRAM MB |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in report["systems"].values():
        sp = s["speed"]
        rtf_t0 = f"{sp['rtf_t0']:.2f}" if sp.get("rtf_t0") else "—"
        vram = "—" if s["vram_mb"] is None else f"{s['vram_mb']:.0f} ({s['vram_source']})"
        lines.append(
            f"| {s['label']} | {s['segments']} | {s['wer']:.3f} | {s['wer_no_latin']:.3f} | "
            f"{s['mean_cer']:.3f} | {s['failed']} | {sp.get('rtf') or 0:.3f} | {rtf_t0} | {vram} |"
        )
    lines += [
        "",
        "WER by category:",
        "",
        "| System | "
        + " | ".join(sorted(next(iter(report["systems"].values()))["by_category"]))
        + " |",
    ]
    cats = sorted(next(iter(report["systems"].values()))["by_category"])
    lines.append("|---" * (len(cats) + 1) + "|")
    for s in report["systems"].values():
        cells = [f"{s['by_category'].get(c, {}).get('wer', 0):.3f}" for c in cats]
        lines.append(f"| {s['label']} | " + " | ".join(cells) + " |")
    lines += ["", "Normalization misses:", ""]
    lines += [
        f"- `{m['text']}` → `{m['got']}` (expected `{m['expected']}`)"
        for m in t["normalization"]["misses"]
    ]
    lines += ["", "Homograph stress misses (text):", ""]
    lines += [
        f"- {m['id']}: expected {m['expected']}, got {m['got']}"
        for m in t["homograph_text"]["wrong"]
    ]
    lines += ["", "ё not restored:", ""]
    lines += [f"- {m['id']}: {m['word']}" for m in t["yo"]["missed"]]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
