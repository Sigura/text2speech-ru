"""Spike step 2 (pymupdf env): typeset out/src/*.html into book-like PDFs.

    uv run --project spikes/pdf-readers/pymupdf python spikes/pdf-readers/make_pdfs.py

A5 pages, DejaVu Serif, justified text with hyphenation at soft hyphens, a running
header (book title) and a page number on every page — the things a PDF reader must
drop or repair. Output: out/pdf/<name>.pdf.
"""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
SRC = HERE / "out/src"
OUT = HERE / "out/pdf"
FONTS = Path("/usr/share/fonts/truetype/dejavu")

CSS = """
@font-face { font-family: serif; src: url(DejaVuSerif.ttf); }
@font-face { font-family: serif; src: url(DejaVuSerif-Italic.ttf); font-style: italic; }
@font-face { font-family: serif; src: url(DejaVuSerif-Bold.ttf); font-weight: bold; }
body { font-family: serif; font-size: 10pt; line-height: 1.3; }
p { text-align: justify; text-indent: 1.2em; margin: 0; }
h1 { font-size: 16pt; text-align: center; margin: 2em 0 1em 0; }
h2 { font-size: 13pt; text-align: center; margin: 1.5em 0 0.8em 0; }
h3 { font-size: 11pt; font-style: italic; margin: 1em 0 0.5em 0; }
blockquote { margin: 0.5em 0 0.8em 4em; font-size: 9pt; }
blockquote p { text-indent: 0; text-align: left; }
p.r { text-align: right; font-style: italic; }
p.c { text-align: center; text-indent: 0; }
"""


def typeset(html: str, header: str, out: Path) -> int:
    story = pymupdf.Story(html=html, user_css=CSS, archive=pymupdf.Archive(str(FONTS)))
    tmp = out.with_suffix(".tmp.pdf")
    writer = pymupdf.DocumentWriter(str(tmp))
    page_rect = pymupdf.paper_rect("a5")
    where = page_rect + (48, 56, -48, -52)
    more = True
    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(where)
        story.draw(device)
        writer.end_page()
    writer.close()
    doc = pymupdf.open(str(tmp))
    font = str(FONTS / "DejaVuSerif.ttf")
    for n, page in enumerate(doc, 1):
        w = page.rect.width
        page.insert_text((w / 2 - len(header) * 2.2, 34), header, fontsize=8, fontname="dv", fontfile=font)
        page.draw_line((48, 40), (w - 48, 40), width=0.4)
        page.insert_text((w / 2 - 4, page.rect.height - 28), str(n), fontsize=8, fontname="dv", fontfile=font)
    doc.save(str(out), garbage=3, deflate=True)
    pages = doc.page_count
    doc.close()
    tmp.unlink()
    return pages


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for src in sorted(SRC.glob("*.html")):
        header = json.loads(src.with_suffix(".json").read_text())["header"]
        pages = typeset(src.read_text(encoding="utf-8"), header, OUT / f"{src.stem}.pdf")
        print(f"{src.stem}: {pages} pages")


if __name__ == "__main__":
    main()
