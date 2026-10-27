"""Spike step 3 (main env): listening clips from collect.py and synth.py.

    uv run --python 3.11 python spikes/qwen3-dialogue/pairs.py          # build
    uv run --python 3.11 python spikes/qwen3-dialogue/pairs.py score    # after answers.tsv

- out/clips/qNN.mp3: blind pairs, Qwen3-TTS (batched) vs the T1 hybrid on the same
  dialogue line; sides drawn at random into out/key.json;
- out/clips/stress-hNN.mp3: a homograph sentence without, then with the stress mark;
- out/clips/latin.mp3: the five Latin-script sentences.
Every clip starts with the 1.5 s lead-in; parts are 1.5 s apart. Answers as for
scripts/listen_ab.py (`q01 A`, `q02 same, note`); Qwen3 is integrated only if it wins
≥ 60 % of decided pairs (§6.7 rule).
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from text2audio.audio.encode import encode_mp3, find_ffmpeg
from text2audio.audio.wav import ASSEMBLY_RATE, PartWriter, speech

HERE = Path(__file__).parent
OUT = HERE / "out"
CLIPS = OUT / "clips"
GAP_MS = 1500

sys.path.insert(0, str(HERE.parents[1] / "scripts"))
from listen_ab import THRESHOLD, read_answers  # noqa: E402


def clip(out: Path, parts: list[Path], title: str) -> None:
    ffmpeg = find_ffmpeg()
    wav = out.with_suffix(".wav")
    with PartWriter(wav, ASSEMBLY_RATE) as writer:
        for p in parts:
            writer.add(speech(p))
            writer.silence(GAP_MS)
    encode_mp3(ffmpeg, wav, out, tags={"title": title})
    wav.unlink()


def build() -> None:
    lines = json.loads((OUT / "lines.json").read_text(encoding="utf-8"))
    CLIPS.mkdir(parents=True, exist_ok=True)
    rng = random.SystemRandom()
    key: dict[str, dict[str, str]] = {}
    for n, item in enumerate(lines["dialogue"], 1):
        pair = f"q{n:02d}"
        sides = {
            "Qwen3": OUT / "qwen-batch" / f"{item['id']}.wav",
            "hybrid": OUT / "hybrid" / f"{item['id']}.wav",
        }
        a, b = ("Qwen3", "hybrid") if rng.random() < 0.5 else ("hybrid", "Qwen3")
        key[pair] = {"A": a, "B": b}
        clip(CLIPS / f"{pair}.mp3", [sides[a], sides[b]], pair)
        (CLIPS / f"{pair}.txt").write_text(item["text"] + "\n", encoding="utf-8")
    for item in lines["homograph"]:
        stress = OUT / "stress"
        parts = [stress / f"{item['id']}-plain.wav", stress / f"{item['id']}-accent.wav"]
        clip(CLIPS / f"stress-{item['id']}.mp3", parts, f"{item['id']}: {item['tts']}")
    latin = [OUT / "latin" / f"{item['id']}.wav" for item in lines["latin"]]
    clip(CLIPS / "latin.mp3", latin, "Latin-script words")
    (OUT / "key.json").write_text(json.dumps(key, indent=2) + "\n")
    print(f"done: {len(key)} pairs, 6 stress clips, 1 Latin clip in {CLIPS}")


def score() -> None:
    key = json.loads((OUT / "key.json").read_text())
    answers = read_answers((OUT / "answers.tsv").read_text(encoding="utf-8"))
    decided = [(p, c) for p, c in answers.items() if c in ("A", "B")]
    wins = sum(key[p][c] == "Qwen3" for p, c in decided)
    share = wins / len(decided) if decided else 0.0
    print(
        f"Qwen3 vs hybrid: {len(answers)}/{len(key)} answered, {len(decided)} decided, "
        f"Qwen3 preferred in {wins} ({share:.0%}) → "
        + ("integrate" if len(answers) == len(key) and share >= THRESHOLD else "keep the hybrid")
    )


if __name__ == "__main__":
    score() if sys.argv[1:] == ["score"] else build()
