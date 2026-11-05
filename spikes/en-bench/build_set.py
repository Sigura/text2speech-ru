"""Spike, OFFLINE step: pick the ≤ 100 benchmark segments from the English novel.

    uv run --python 3.11 python tests/fixtures/fetch_novel_en.py   # once, online
    uv run --python 3.11 python spikes/en-bench/build_set.py

Paragraphs of "The Adventures of Sherlock Holmes" are sorted into categories (numbers,
abbreviations, foreign words, quote dialogue, plain narration), each spread over the
twelve stories, and taken until the en pack's segmenter (E1, the pipeline's own) counts
`LIMIT` segments. Writes set.tsv (paragraph id, category, story, text) and bench.md,
the book `text2audio run` reads. Both are committed so the run is reproducible without
the novel.
"""

from __future__ import annotations

import re
from pathlib import Path

from text2audio_lang_en import PROFILE

from text2audio.text.segment import segment_markdown

HERE = Path(__file__).parent
NOVEL = HERE.parents[1] / "tests/fixtures/en/large/adventures-of-sherlock-holmes.md"
LIMIT = 100
MAX_SEGMENTS = 3  # per paragraph, so that one long speech does not fill a category
# Segments per category, in the order they are filled; narration takes what is left.
QUOTA = {"number": 25, "abbreviation": 15, "foreign": 10, "dialogue": 30, "narration": 20}
CATEGORY = {
    "number": re.compile(r"\d|£"),
    "abbreviation": re.compile(r"\b(Mr|Mrs|Dr|St|No)\."),
    "foreign": re.compile(r"\*[^*]+\*"),
    "dialogue": re.compile(r"[“”\"]"),
}


def paragraphs(text: str) -> list[tuple[str, str]]:
    """(story, paragraph) for every body paragraph of the novel."""
    story, out = "", []
    for block in text.split("\n\n"):
        block = block.strip()
        if block.startswith("## "):
            story = block[3:]
        elif block and not block.startswith("#"):
            out.append((story, block))
    return out


def category(text: str) -> str:
    return next((name for name, rx in CATEGORY.items() if rx.search(text)), "narration")


def spoken_segments(text: str) -> int:
    segs = segment_markdown(f"# Bench\n\n{text}\n", "en", PROFILE)
    return sum(1 for s in segs if s.heading_level is None)


def spread(items: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Round-robin over the stories, so each category samples the whole book."""
    by_story: dict[str, list[tuple[str, str]]] = {}
    for story, text in items:
        by_story.setdefault(story, []).append((story, text))
    queues = [q[:: max(1, len(q) // 8)] for q in by_story.values()]
    out = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


def main() -> None:
    paras = paragraphs(NOVEL.read_text("utf-8"))
    pool = {name: spread([p for p in paras if category(p[1]) == name]) for name in QUOTA}
    chosen: list[tuple[str, str, str, int]] = []
    total = 0
    for name, quota in QUOTA.items():
        want = quota if name != "narration" else LIMIT - total
        got = 0
        for story, text in pool[name]:
            n = spoken_segments(text)
            if n > MAX_SEGMENTS or got + n > want or total + n > LIMIT:
                continue
            chosen.append((name, story, text, n))
            got += n
            total += n
            if got == want:
                break
    order = {p: i for i, (_, p) in enumerate(paras)}
    chosen.sort(key=lambda c: order[c[2]])
    rows = ["id\tcategory\tstory\ttext"]
    book = ["# Benchmark", ""]
    for i, (name, story, text, _) in enumerate(chosen, 1):
        rows.append(f"p{i:03d}\t{name}\t{story}\t{text}")
        book += [text, ""]
    (HERE / "set.tsv").write_text("\n".join(rows) + "\n", "utf-8")
    (HERE / "bench.md").write_text("\n".join(book).rstrip() + "\n", "utf-8")
    counted = spoken_segments("\n\n".join(c[2] for c in chosen))
    by = {n: sum(c[3] for c in chosen if c[0] == n) for n in QUOTA}
    print(f"{len(chosen)} paragraphs, {counted} segments: {by}")


if __name__ == "__main__":
    main()
