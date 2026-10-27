"""Blind A/B listening test for M3 (SPEC §6.7): E1 vs E0, and T1 vs T0 on narration.

    uv run --python 3.11 python scripts/listen_ab.py build e1 [--out DIR] [--pairs 30]
    uv run --python 3.11 python scripts/listen_ab.py build t1 [--out DIR] [--pairs 20]
    uv run --python 3.11 python scripts/listen_ab.py score [--out DIR]

`build` picks paragraphs of the full novel (fetch it once with
`tests/fixtures/fetch_novel.py`) and renders them through the real pipeline, no QA, at
most 100 segments per call. `e1` takes paragraphs that E0 and E1 segment differently
(dialogue with an author's remark), both on T0; `t1` takes narration paragraphs and
renders them on T0 and T1 (needs a GPU). Each pair is one MP3: the 1.5 s lead-in,
side A, 1.5 s of silence, side B. The sides are drawn at random and written to
DIR/key.json — do not open it before scoring. Answers go in DIR/answers.tsv, one line per pair, e.g.
`e01<TAB>A` (A, B, or = for no preference; spaces work too).

`score` prints, per comparison, how many decided pairs the new side (E1, T1) won; it
becomes the default at ≥ 60 % of decided pairs, with at least 30 pairs for E1
(owner-alone protocol, §6.7). UTMOSv2 is not pinned in models.lock, so no automatic
score is reported.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from text2audio import api
from text2audio.core.config import load_config
from text2audio.core.contracts import Segment, Tier
from text2audio.core.languages import profile
from text2audio.core.synthesize import render_selection, write_clip
from text2audio.text.dialogue import split_dialogue
from text2audio.text.segment import segment_markdown

ROOT = Path(__file__).resolve().parents[1]
NOVEL = ROOT / "tests" / "fixtures" / "ru" / "large" / "kapitanskaya-dochka.md"
OUT = ROOT / "output" / "listen-ab"
BUDGET = 100  # segments rendered per `build` call (owner's test-run limit)
MAX_CHARS = 320  # per passage: short clips, small budget
MIN_NARRATION = 150  # `t1` passages: long enough to judge a voice
SEED = 1234  # passage choice is reproducible; sides are not
THRESHOLD = 0.6
GAP_MS = 1500  # after each side, the same for both: a passage ends at a heading here
MIN_PAIRS = {"e1": 30, "t1": 1}
SIDES = {"e1": ("E0", "E1"), "t1": ("T0", "T1")}  # (old, new)
CHOICE = re.compile(r"^\s*([et]\d+)\s+([AB=])\s*$", re.IGNORECASE)


@dataclass
class Passage:
    text: str  # one paragraph of `book.md`
    cost: int  # segments to render


def paragraphs(markdown: str) -> list[str]:
    """Plain prose paragraphs; headings, quotes, poems, lists and comments are left out."""
    blocks = [b.strip() for b in markdown.split("\n\n")]
    return [b for b in blocks if b and "\n" not in b and not re.match(r"[#>|*<\-+\d]", b)]


def pick(candidates: list[Passage], pairs: int, budget: int, seed: int = SEED) -> list[Passage]:
    """Up to `pairs` passages whose costs fit the budget, cheapest first, ties in a seeded
    random order."""
    order = candidates[:]
    random.Random(seed).shuffle(order)
    order.sort(key=lambda p: p.cost)
    chosen: list[Passage] = []
    spent = 0
    for p in order:
        if len(chosen) == pairs:
            break
        if spent + p.cost <= budget:
            chosen.append(p)
            spent += p.cost
    return chosen


def draw_sides(kind: str, count: int, rng: random.Random) -> dict[str, dict[str, str]]:
    """Pair id → {"A": side, "B": side}."""
    old, new = SIDES[kind]
    key = {}
    for n in range(1, count + 1):
        a, b = (old, new) if rng.random() < 0.5 else (new, old)
        key[f"{kind[0]}{n:02d}"] = {"A": a, "B": b}
    return key


def read_answers(text: str) -> dict[str, str]:
    answers: dict[str, str] = {}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = CHOICE.match(line)
        if m is None:
            raise ValueError(f"answers line {n}: expected '<pair> A|B|=', got {line!r}")
        answers[m.group(1).lower()] = m.group(2).upper()
    return answers


@dataclass
class Score:
    kind: str
    pairs: int
    answered: int
    decided: int
    new_wins: int

    @property
    def share(self) -> float | None:
        return self.new_wins / self.decided if self.decided else None

    @property
    def passed(self) -> bool:
        share = self.share
        return (
            self.answered == self.pairs
            and self.pairs >= MIN_PAIRS[self.kind]
            and share is not None
            and share >= THRESHOLD
        )


def score(key: dict[str, dict[str, str]], answers: dict[str, str]) -> list[Score]:
    unknown = sorted(set(answers) - set(key))
    if unknown:
        raise ValueError(f"answers for unknown pairs: {', '.join(unknown)}")
    out = []
    for kind, (_, new) in SIDES.items():
        ids = [p for p in key if p.startswith(kind[0])]
        if not ids:
            continue
        given = [answers[p] for p in ids if p in answers]
        decided = [(p, answers[p]) for p in ids if answers.get(p) in ("A", "B")]
        wins = sum(key[p][choice] == new for p, choice in decided)
        out.append(Score(kind, len(ids), len(given), len(decided), wins))
    return out


def report(scores: list[Score]) -> str:
    lines = []
    for s in scores:
        old, new = SIDES[s.kind]
        share = "-" if s.share is None else f"{s.share:.0%}"
        verdict = (
            f"{new} becomes the default"
            if s.passed
            else f"{new} not confirmed (needs all {max(s.pairs, MIN_PAIRS[s.kind])} pairs "
            f"answered and ≥ {THRESHOLD:.0%} of decided ones)"
        )
        lines.append(
            f"{new} vs {old}: {s.answered}/{s.pairs} answered, {s.decided} decided, "
            f"{new} preferred in {s.new_wins} ({share}) → {verdict}"
        )
    return "\n".join(lines)


# --------------------------------------------------------------------------- build


def _segments(text: str, expression: str) -> list[Segment]:
    return segment_markdown(text, "ru", profile("ru"), expression=expression)


def _candidates(kind: str, paras: list[str]) -> list[Passage]:
    out = []
    for text in paras:
        if len(text) > MAX_CHARS:
            continue
        e1 = _segments(text, "E1")
        if kind == "e1":
            e0 = _segments(text, "E0")
            if [(s.text, s.pause_after_ms) for s in e0] == [(s.text, s.pause_after_ms) for s in e1]:
                continue
            seen = {s.text for s in e1}
            out.append(Passage(text, len(e1) + sum(s.text not in seen for s in e0)))
        elif len(text) >= MIN_NARRATION and all(r == "narration" for r, _ in split_dialogue(text)):
            out.append(Passage(text, len(e1)))
    return out


def _ending(group: list[Segment]) -> list[Segment]:
    return [*group[:-1], group[-1].model_copy(update={"pause_after_ms": GAP_MS})]


def _text_only(segments: list[Segment]) -> list[Segment]:
    return [s for s in segments if s.heading_level is None]


def build(kind: str, out: Path, pairs: int) -> None:
    if not NOVEL.is_file():
        raise SystemExit(f"{NOVEL} missing: run `uv run python tests/fixtures/fetch_novel.py`")
    print(f"1/4 picking {pairs} {kind} passages")
    chosen = pick(_candidates(kind, paragraphs(NOVEL.read_text(encoding="utf-8"))), pairs, BUDGET)
    if len(chosen) < pairs:
        print(f"  only {len(chosen)} passages fit the {BUDGET}-segment budget")
    print(f"  {len(chosen)} passages, {sum(p.cost for p in chosen)} segments to render")

    work = out / f"book-{kind}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    source = work / "listen.md"
    body = "".join(f"## {n}\n\n{p.text}\n\n" for n, p in enumerate(chosen, 1))
    source.write_text(f"# Проверка на слух\n\n{body}", encoding="utf-8")
    [converted] = api.convert([source], work / "out", lang="ru", overwrite=True)
    if converted.folder is None:
        raise SystemExit(f"convert failed: {converted.error}")
    folder = converted.folder

    old, new = SIDES[kind]
    sides: dict[str, dict[int, list[Segment]]] = {}
    config = load_config([])
    for step, side in enumerate((old, new), 2):
        print(f"{step}/4 rendering {side}")
        expression = side if kind == "e1" else "E1"
        tier: Tier = "fast" if kind == "e1" or side == "T0" else "expressive"
        config = load_config([], {"synthesis": {"expression": expression}, "qa": {"mode": "off"}})
        groups: dict[int, list[Segment]] = {}
        for s in render_selection(folder, config, _text_only, tier=tier):
            groups.setdefault(s.chapter, []).append(s)
        sides[side] = groups

    print("4/4 writing pairs")
    rng = random.SystemRandom()
    key = draw_sides(kind, len(chosen), rng)
    chapters = sorted(sides[old])
    clips = out / "pairs"
    for (pair, ab), chapter, passage in zip(key.items(), chapters, chosen, strict=True):
        clip = [_ending(sides[ab["A"]][chapter]), _ending(sides[ab["B"]][chapter])]
        write_clip(folder, clip, clips / f"{pair}.mp3", config, {"title": pair})
        (clips / f"{pair}.txt").write_text(passage.text + "\n", encoding="utf-8")
    key_file = out / "key.json"
    known = json.loads(key_file.read_text(encoding="utf-8")) if key_file.is_file() else {}
    known = {p: v for p, v in known.items() if not p.startswith(kind[0])} | key
    key_file.write_text(json.dumps(known, indent=2) + "\n", encoding="utf-8")
    print(f"done: {len(key)} pairs in {clips}, key in {key_file} (do not open)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("kind", choices=sorted(SIDES))
    b.add_argument("--out", type=Path, default=OUT)
    b.add_argument("--pairs", type=int)
    s = sub.add_parser("score")
    s.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    if args.cmd == "build":
        build(args.kind, args.out, args.pairs or {"e1": 30, "t1": 20}[args.kind])
        return 0
    key = json.loads((args.out / "key.json").read_text(encoding="utf-8"))
    answers = read_answers((args.out / "answers.tsv").read_text(encoding="utf-8"))
    print(report(score(key, answers)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
