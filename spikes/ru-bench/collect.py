"""Gather the T0 (Silero) and T1 (hybrid) pipeline runs of bench.md into out/.

    uv run --python 3.11 python spikes/ru-bench/collect.py

Reads out/run-t0/bench and out/run-t1/bench (written by `text2audio run`), maps every
segment to its set.tsv item, copies the segment WAVs to out/wav/{silero,hybrid}/<id>.wav
and writes out/segments.json — the texts the challengers read — and out/runs.json
(the two report.json files).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from text2audio.qa.gate import wav_path
from text2audio.qa.wer import fold

HERE = Path(__file__).parent
OUT = HERE / "out"
RUNS = {"silero": OUT / "run-t0" / "bench", "hybrid": OUT / "run-t1" / "bench"}


def items() -> list[dict[str, str]]:
    lines = (HERE / "set.tsv").read_text("utf-8").splitlines()
    keys = lines[0].split("\t")
    rows = [dict(zip(keys, line.split("\t"), strict=True)) for line in lines[1:]]
    for row in rows:
        row["text"] = row["text"].replace("\\n", "\n")
    return rows


def segments(book: Path) -> list[dict[str, object]]:
    lines = (book / "_work" / "segments.jsonl").read_text("utf-8").splitlines()
    return [s for s in map(json.loads, lines) if s.get("heading_level") is None]


def assign(rows: list[dict[str, str]], segs: list[dict[str, object]]) -> list[str]:
    """Item id per segment: segments follow the items in order, an item may span several."""
    out: list[str] = []
    k = 0
    for row in rows:
        want, have = fold(row["text"]), []
        while have != want:
            if k >= len(segs) or len(have) > len(want):
                raise SystemExit(f"cannot map item {row['id']}: {row['text'][:60]!r}")
            have += fold(str(segs[k]["text"]))
            out.append(row["id"])
            k += 1
    if k != len(segs):
        raise SystemExit(f"{len(segs) - k} segments left after the last item")
    return out


def main() -> None:
    rows = items()
    by_system = {name: segments(book) for name, book in RUNS.items()}
    t0, t1 = by_system["silero"], by_system["hybrid"]
    if [s["id"] for s in t0] != [s["id"] for s in t1]:
        raise SystemExit("T0 and T1 segmentations differ")
    owner = assign(rows, t0)
    category = {r["id"]: r["category"] for r in rows}
    table = []
    for item, a, b in zip(owner, t0, t1, strict=True):
        table.append(
            {
                "id": a["id"],
                "item": item,
                "category": category[item],
                "text": a["text"],
                "t0_norm": a["text_norm"],
                "t0_stress": a["text_stress"],
                "t1_norm": b["text_norm"],
                "t1_stress": b["text_stress"],
                "t1_route": b["route"],
            }
        )
    for name, book in RUNS.items():
        dest = OUT / "wav" / name
        dest.mkdir(parents=True, exist_ok=True)
        audio = book / "_work" / "audio" / "segments"
        for seg in by_system[name]:
            shutil.copyfile(wav_path(audio, str(seg["hash"])), dest / f"{seg['id']}.wav")
    (OUT / "segments.json").write_text(json.dumps(table, ensure_ascii=False, indent=1), "utf-8")
    reports = {n: json.loads((b / "report.json").read_text("utf-8")) for n, b in RUNS.items()}
    (OUT / "runs.json").write_text(json.dumps(reports, ensure_ascii=False, indent=1), "utf-8")
    print(f"{len(table)} segments of {len(rows)} items")


if __name__ == "__main__":
    main()
