"""PDF reader (SPEC §6.3): pymupdf4llm with its layout model, then clean-up.

Chosen by the M1 benchmark (`spikes/pdf-readers/FINDINGS.md`): the layout model drops
running headers and page numbers and re-flows paragraphs; its ONNX weights ship inside
the `pymupdf_layout` wheel, so nothing is downloaded. Text-layer PDFs only (v1): a
PDF without extractable text is refused rather than OCR'd.

Clean-up after conversion, language-independent:
- a paragraph that starts with a lowercase letter continues the previous one — a
  paragraph or a hyphenated word split by a page break ("прон-" | "икавшие");
- paragraphs that are only a number (page numbers) and short lines repeated on many
  pages (running headers the layout model missed) are dropped;
- bold is removed from headings, and heading levels are shifted so that chapters are
  `##` under a single `#` book title.
"""

from __future__ import annotations

import re
from collections import Counter

from text2audio.core.languages import guess_language
from text2audio.readers.document import Block, Document, Heading, Inline, Paragraph, plain
from text2audio.readers.markdown import parse_markdown
from text2audio.readers.safety import InputError

MAX_PAGES = 5000
MIN_CHARS_PER_PAGE = 40  # below this the PDF has no usable text layer (a scan)
_PAGE_NUMBER = re.compile(r"[\W_]*\d{1,4}[\W_]*")
_HYPHEN_END = re.compile(r"(\w)[-­]$")


def _continues(block: Block) -> bool:
    if not isinstance(block, Paragraph) or not block.inlines:
        return False
    first = plain(block.inlines).lstrip()
    return bool(first) and first[0].islower()


def _join(prev: Paragraph, nxt: Paragraph) -> Paragraph:
    inlines = list(prev.inlines)
    last = inlines[-1]
    if last.kind != "note" and _HYPHEN_END.search(last.text.rstrip()):
        inlines[-1] = Inline(last.kind, last.text.rstrip()[:-1])
        joined = [*inlines, *nxt.inlines]
        first = joined[len(inlines)]
        joined[len(inlines)] = Inline(first.kind, first.text.lstrip())
        return Paragraph(joined)
    return Paragraph([*inlines, Inline("text", " "), *nxt.inlines])


def clean(doc: Document) -> Document:
    blocks: list[Block] = []
    for block in doc.blocks:
        if isinstance(block, Heading):
            # Bold or italic headings are typesetting, not emphasis.
            block = Heading(
                block.level,
                [
                    Inline("text", i.text) if i.kind in ("strong", "emph") else i
                    for i in block.inlines
                ],
            )
        if blocks and isinstance(blocks[-1], Paragraph) and _continues(block):
            assert isinstance(block, Paragraph)
            blocks[-1] = _join(blocks[-1], block)
            continue
        blocks.append(block)
    texts = Counter(plain(b.inlines).strip() for b in blocks if isinstance(b, Paragraph))
    repeated = {t for t, n in texts.items() if n >= 3 and len(t) <= 80}
    blocks = [
        b
        for b in blocks
        if not (
            isinstance(b, Paragraph)
            and (
                _PAGE_NUMBER.fullmatch(plain(b.inlines).strip())
                or plain(b.inlines).strip() in repeated
            )
        )
    ]
    headings = [b for b in blocks if isinstance(b, Heading)]
    title = (
        headings[0] if headings and blocks[0] is headings[0] and headings[0].level == 1 else None
    )
    others = [h for h in headings if h is not title]
    if others:
        shift = 2 - min(h.level for h in others)
        for h in others:
            h.level = max(2, min(h.level + shift, 6))
    doc.blocks = blocks
    if title is not None and not doc.metadata.title:
        doc.metadata.title = plain(title.inlines).strip() or None
    return doc


def read_pdf(data: bytes, lang: str | None = None) -> Document:
    import pymupdf
    import pymupdf4llm

    try:
        pdf = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # PyMuPDF raises its own error types
        raise InputError(f"cannot open PDF: {exc}") from exc
    with pdf:
        if pdf.needs_pass:
            raise InputError("the PDF is password-protected")
        if pdf.page_count > MAX_PAGES:
            raise InputError(f"the PDF has {pdf.page_count} pages (limit {MAX_PAGES})")
        sample = range(min(pdf.page_count, 30))
        chars = 0
        for n in sample:
            chars += len(str(pdf[n].get_text()).strip())
        if not sample or chars / len(sample) < MIN_CHARS_PER_PAGE:
            raise InputError("the PDF has no text layer (a scan?); OCR is not supported yet")
        pymupdf4llm.use_layout(True)
        md = pymupdf4llm.to_markdown(
            pdf, header=False, footer=False, use_ocr=False, show_progress=False
        )
        info = pdf.metadata or {}
    doc = clean(parse_markdown(str(md)))
    meta = doc.metadata
    meta.title = (info.get("title") or "").strip() or meta.title
    author = (info.get("author") or "").strip()
    if author:
        meta.authors = [a.strip() for a in re.split(r"[;,&]| and ", author) if a.strip()]
    text = " ".join(plain(b.inlines) for b in doc.blocks if isinstance(b, Paragraph | Heading))
    meta.language = lang or guess_language(text)
    doc.heading_confidence = 0.8 if doc.chapters() >= 2 else 0.0
    return doc
