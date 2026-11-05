"""Gather the fast (Kokoro) and expressive (Chatterbox) pipeline runs of bench.md into out/.

    uv run --python 3.11 python spikes/en-bench/collect.py

Reads out/run-t0/bench and out/run-t1/bench (written by `text2audio run`), maps every
segment to its set.tsv paragraph, copies the segment WAVs to
out/wav/{kokoro,chatterbox}/<id>.wav and writes out/segments.json — the texts IndexTTS-2
reads — and out/runs.json (the two report.json files).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from text2audio.qa.gate import wav_path
from text2audio.qa.wer import fold

HERE = Path(__file__).parent
OUT = HERE / "out"
RUNS = {"kokoro": OUT / "run-t0" / "bench", "chatterbox": OUT / "run-t1" / "bench"}


def items() -> list[dict[str, str]]:
    lines = (HERE / "set.tsv").read_text("utf-8").splitlines()
    keys = lines[0].split("\t")
    return [dict(zip(keys, line.split("\t"), strict=True)) for line in lines[1:]]


def segments(book: Path) -> list[dict[str, object]]:
    lines = (book / "_work" / "segments.jsonl").read_text("utf-8").splitlines()
    return [s for s in map(json.loads, lines) if s.get("heading_level") is None]


def assign(rows: list[dict[str, str]], segs: list[dict[str, object]]) -> list[str]:
    """Paragraph id per segment: segments follow the paragraphs in order."""
    out: list[str] = []
    k = 0
    for row in rows:
        want, have = fold(row["text"].replace("*", "")), []  # emphasis marks
        while have != want:
            if k >= len(segs) or len(have) > len(want):
                raise SystemExit(f"cannot map paragraph {row['id']}: {row['text'][:60]!r}")
            have += fold(str(segs[k]["text"]))
            out.append(row["id"])
            k += 1
    if k != len(segs):
        raise SystemExit(f"{len(segs) - k} segments left after the last paragraph")
    return out


def main() -> None:
    rows = items()
    by_system = {name: segments(book) for name, book in RUNS.items()}
    fast, expressive = by_system["kokoro"], by_system["chatterbox"]
    if [s["id"] for s in fast] != [s["id"] for s in expressive]:
        raise SystemExit("fast and expressive segmentations differ")
    owner = assign(rows, fast)
    category = {r["id"]: r["category"] for r in rows}
    table = []
    for item, a, b in zip(owner, fast, expressive, strict=True):
        if a["text_norm"] != b["text_norm"]:
            print(f"{a['id']}: the tiers read different text")
        table.append(
            {
                "id": a["id"],
                "item": item,
                "category": category[item],
                "role": a["role"],
                "voice": a["voice"],
                "text": a["text"],
                "text_norm": a["text_norm"],
                "kokoro_route": a["route"],
                "chatterbox_route": b["route"],
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
    print(f"{len(table)} segments of {len(rows)} paragraphs")


if __name__ == "__main__":
    main()
