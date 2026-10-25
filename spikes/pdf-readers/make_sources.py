"""Spike step 1 (core env): ground truth + typesetting HTML for the test PDFs.

    uv run python spikes/pdf-readers/make_sources.py

For each source book: out/src/<name>.truth.md (what a perfect reader would give, in
our Markdown dialect) and <name>.html (the same text, with soft hyphens in long
words so the typesetter hyphenates at line ends). The owner's Gibson book is used
only if present in inputs/ (its PDFs stay in this spike's out/, never committed).
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from text2audio.readers.document import Document, Heading, Paragraph, Poem, Quote, SceneBreak
from text2audio.readers.fb2 import read_fb2
from text2audio.readers.markdown import normalize_inlines, to_markdown

HERE = Path(__file__).parent
REPO = HERE.parent.parent
OUT = HERE / "out/src"


def hyphenate(word: str) -> str:
    """Soft hyphens every 4 letters inside long words (not real syllables: the test is
    whether readers re-join a word broken at a line end)."""
    if len(word) < 9:
        return word
    parts = [word[i : i + 4] for i in range(0, len(word), 4)]
    if len(parts[-1]) < 3:
        parts[-2:] = [parts[-2] + parts[-1]]
    return "­".join(parts)


def inline_html(inlines: list) -> str:  # type: ignore[type-arg]
    out = []
    for i in normalize_inlines(inlines):
        text = re.sub(r"\w+", lambda m: hyphenate(m.group(0)), html.escape(i.text))
        out.append(f"<em>{text}</em>" if i.kind == "emph" else text if i.kind != "note" else "")
    return "".join(out)


def to_html(doc: Document) -> str:
    parts = []
    for b in doc.blocks:
        if isinstance(b, Heading):
            n = min(b.level, 3)
            parts.append(f"<h{n}>{inline_html(b.inlines)}</h{n}>")
        elif isinstance(b, Paragraph):
            parts.append(f"<p>{inline_html(b.inlines)}</p>")
        elif isinstance(b, SceneBreak):
            parts.append('<p class="c">* * *</p>')
        elif isinstance(b, Quote):
            inner = "".join(
                f"<p>{inline_html(x.inlines)}</p>"
                if isinstance(x, Paragraph)
                else "".join(
                    "<p>" + "<br/>".join(inline_html(line) for line in st) + "</p>"
                    for st in (x.stanzas if isinstance(x, Poem) else [])
                )
                for x in b.blocks
            )
            author = f'<p class="r">{inline_html(b.author)}</p>' if b.author else ""
            parts.append(f"<blockquote>{inner}{author}</blockquote>")
        elif isinstance(b, Poem):
            parts += ["<p>" + "<br/>".join(inline_html(x) for x in st) + "</p>" for st in b.stanzas]
    return "<html><body>" + "\n".join(parts) + "</body></html>"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    books = {"kapitanskaya": REPO / "tests/fixtures/ru/kapitanskaya-dochka.fb2"}
    gibson = REPO / "inputs/Гибсон Гэри. Станции Ангелов.fb2"
    if gibson.exists():
        books["gibson-2ch"] = gibson
    for name, path in books.items():
        doc = read_fb2(path.read_bytes())
        doc.footnotes = {}  # notes are not typeset in this test
        if name == "gibson-2ch":  # title, prologue and chapter 1
            chapter_starts = [i for i, b in enumerate(doc.blocks) if isinstance(b, Heading) and b.level == 2]
            doc.blocks = doc.blocks[: chapter_starts[2]]
        for b in doc.blocks:  # drop note references from the truth too
            for attr in ("inlines", "author"):
                if isinstance(getattr(b, attr, None), list):
                    setattr(b, attr, [i for i in getattr(b, attr) if i.kind != "note"])
        (OUT / f"{name}.truth.md").write_text(to_markdown(doc), encoding="utf-8")
        (OUT / f"{name}.html").write_text(to_html(doc), encoding="utf-8")
        title = doc.metadata.title or name
        (OUT / f"{name}.json").write_text(json.dumps({"header": title}, ensure_ascii=False))
        print(name, len(doc.blocks), "blocks")


if __name__ == "__main__":
    main()
