"""Spike step 1 (main env): the lines Qwen3-TTS reads and the hybrid's audio of them.

    uv run --python 3.11 python spikes/qwen3-dialogue/collect.py

Picks 20 short dialogue paragraphs (one or two segments) of the full novel (seeded), renders them on
the T1 hybrid through the normal stages (Silero → Chatterbox VC, no QA; needs a GPU),
and writes out/lines.json: the dialogue lines with their RUAccent stress, 3 homograph
pairs of the M2 benchmark with the intended stress, and 5 sentences with Latin-script
words. 21 segments rendered here; synth.py reads 59 more (≤ 100 in all).
"""

from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path

from text2audio import api
from text2audio.core.config import load_config
from text2audio.core.contracts import Segment
from text2audio.core.synthesize import render_selection
from text2audio.qa import gate
from text2audio.text.dialogue import split_dialogue

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
NOVEL = ROOT / "tests/fixtures/ru/large/kapitanskaya-dochka.md"
BENCH = ROOT / "spikes/ru-bench/handwritten.tsv"
DIALOGUE = 20
HOMOGRAPHS = ("замок", "мук", "орган")  # 3 pairs of the benchmark set
LATIN = 5
SEED = 1234

sys.path.insert(0, str(ROOT / "scripts"))
from listen_ab import paragraphs  # noqa: E402


def dialogue_lines() -> list[str]:
    found = [
        p
        for p in paragraphs(NOVEL.read_text(encoding="utf-8"))
        if 40 <= len(p) <= 200 and [r for r, _ in split_dialogue(p)] == ["dialogue"]
    ]
    return random.Random(SEED).sample(found, DIALOGUE)


def bench_rows() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    homographs, latin = [], []
    for line in BENCH.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        kind, text, *rest = line.split("\t")
        if kind == "homograph" and rest[0].replace("+", "").startswith(HOMOGRAPHS):
            word = rest[0].replace("+", "")
            n = len(homographs) + 1
            stressed = text.replace(word, rest[0], 1)
            homographs.append({"id": f"h{n:02d}", "text": text, "tts": stressed})
        elif kind == "latin" and len(latin) < LATIN:
            latin.append({"id": f"l{len(latin) + 1:02d}", "text": text, "tts": text})
    return homographs, latin


def main() -> None:
    if not NOVEL.is_file():
        raise SystemExit(f"{NOVEL} missing: run `uv run python tests/fixtures/fetch_novel.py`")
    work = OUT / "book"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    lines = dialogue_lines()
    source = work / "dialogue.md"
    body = "".join(f"## {n}\n\n{p}\n\n" for n, p in enumerate(lines, 1))
    source.write_text(f"# Диалоги\n\n{body}", encoding="utf-8")
    [converted] = api.convert([source], work / "out", lang="ru", overwrite=True)
    if converted.folder is None:
        raise SystemExit(f"convert failed: {converted.error}")
    folder = converted.folder

    def text_only(segments: list[Segment]) -> list[Segment]:
        return [s for s in segments if s.heading_level is None]

    print("1/2 hybrid (T1) render of the dialogue lines")
    config = load_config([], {"qa": {"mode": "off"}})
    rendered = render_selection(folder, config, text_only, tier="expressive")
    hybrid = OUT / "hybrid"
    hybrid.mkdir(parents=True, exist_ok=True)
    dialogue = []
    for n, s in enumerate(rendered, 1):
        sid = f"d{n:02d}"
        audio = gate.wav_path(folder / "_work" / "audio" / "segments", s.hash or "")
        shutil.copy(audio, hybrid / f"{sid}.wav")
        dialogue.append({"id": sid, "text": s.text, "tts": s.text_stress or s.speech_text})

    print("2/2 benchmark lines")
    homographs, latin = bench_rows()
    data = {"dialogue": dialogue, "homograph": homographs, "latin": latin}
    (OUT / "lines.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {OUT / 'lines.json'}: " + ", ".join(f"{len(v)} {k}" for k, v in data.items()))


if __name__ == "__main__":
    main()
