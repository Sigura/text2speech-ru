"""`Document` → Markdown in the §6.4.1 dialect.

One paragraph per line, so edits diff cleanly. Typographic clean-up that every
language needs happens here: NBSP and other odd spaces become plain spaces,
whitespace moves out of emphasis (`<emphasis>как </emphasis>` → `*как* `), adjacent
spans of the same kind merge, and a paragraph that opens with a dash and a space
(`– `, `- `, `― `) starts with `— ` — the dialogue convention of Russian and other
languages, and a list marker to Markdown otherwise.
"""

from __future__ import annotations

import re

from text2audio.readers.document import (
    Block,
    Document,
    Heading,
    Inline,
    Paragraph,
    Poem,
    Quote,
    SceneBreak,
)

_SPACES = re.compile(r"[  -   　\t\r\n]+")
_MULTI_SPACE = re.compile(r" {2,}")
_DIALOGUE = re.compile(r"^[-‒–—―]\s+")
_ESCAPE = re.compile(r"([\\`*_\[\]<])")
_WORDLIKE = re.compile(r"\w")
_LINE_START = re.compile(r"^(#|>|[+*-] |\d+[.)] |=+$|-{3,}$)")


def _escape(text: str) -> str:
    return _ESCAPE.sub(r"\\\1", text)


def normalize_inlines(inlines: list[Inline]) -> list[Inline]:
    """Clean spaces, merge neighbours, keep emphasis free of edge whitespace."""
    merged: list[Inline] = []
    for item in inlines:
        text = item.text if item.kind == "note" else _SPACES.sub(" ", item.text)
        if not text:
            continue
        if item.kind in ("emph", "strong") and not _WORDLIKE.search(text):
            item = Inline("text", text)  # emphasised spaces or a lone dash mean nothing
        else:
            item = Inline(item.kind, text)
        if merged and merged[-1].kind == item.kind and item.kind != "note":
            merged[-1] = Inline(item.kind, merged[-1].text + item.text)
        else:
            merged.append(item)
    # Move edge whitespace of emphasis spans into neighbouring text.
    out: list[Inline] = []
    for item in merged:
        if item.kind in ("emph", "strong"):
            core = item.text.strip()
            lead, trail = (
                item.text[: len(item.text) - len(item.text.lstrip())],
                item.text[len(item.text.rstrip()) :],
            )
            if lead:
                out.append(Inline("text", lead))
            out.append(Inline(item.kind, core))
            if trail:
                out.append(Inline("text", trail))
        else:
            out.append(item)
    # Merge text runs created by the step above and collapse double spaces.
    final: list[Inline] = []
    for item in out:
        if final and final[-1].kind == "text" and item.kind == "text":
            final[-1] = Inline("text", final[-1].text + item.text)
        else:
            final.append(item)
    for i, item in enumerate(final):
        if item.kind != "note":
            final[i] = Inline(item.kind, _MULTI_SPACE.sub(" ", item.text))
    if final and final[0].kind != "note":
        final[0] = Inline(final[0].kind, final[0].text.lstrip())
    if final and final[-1].kind != "note":
        final[-1] = Inline(final[-1].kind, final[-1].text.rstrip())
    return [i for i in final if i.text]


class _Writer:
    def __init__(self, doc: Document) -> None:
        self.doc = doc
        self.note_numbers: dict[str, int] = {}

    def inline(self, inlines: list[Inline], *, line_start: bool = True) -> str:
        parts: list[str] = []
        for item in normalize_inlines(inlines):
            if item.kind == "note":
                number = self.note_numbers.setdefault(item.text, len(self.note_numbers) + 1)
                parts.append(f"[^{number}]")
            elif item.kind == "emph":
                parts.append(f"*{_escape(item.text)}*")
            elif item.kind == "strong":
                parts.append(f"**{_escape(item.text)}**")
            else:
                parts.append(_escape(item.text))
        text = "".join(parts)
        if line_start:
            text = _DIALOGUE.sub("— ", text)
            if _LINE_START.match(text):
                text = "\\" + text
        return text

    def block(self, block: Block) -> str:
        if isinstance(block, Heading):
            return "#" * min(max(block.level, 1), 6) + " " + self.inline(block.inlines)
        if isinstance(block, Paragraph):
            return self.inline(block.inlines)
        if isinstance(block, SceneBreak):
            return "* * *"
        if isinstance(block, Quote):
            inner = [self.block(b) for b in block.blocks]
            if block.author:
                inner.append("— " + self.inline(block.author, line_start=False))
            body = "\n\n".join(x for x in inner if x)
            return "\n".join("> " + line if line else ">" for line in body.split("\n"))
        if isinstance(block, Poem):
            parts = []
            if block.title:
                parts.append(f"**{self.inline(block.title, line_start=False)}**")
            for stanza in block.stanzas:
                lines = [self.inline(line) for line in stanza]
                lines = [x for x in lines if x]
                if lines:
                    parts.append("\\\n".join(lines))
            if block.author:
                parts.append("— " + self.inline(block.author, line_start=False))
            return "\n\n".join(parts)
        raise TypeError(f"unknown block {block!r}")

    def write(self) -> str:
        body = [self.block(b) for b in self.doc.blocks]
        out = [x for x in body if x]
        notes = []
        for note_id, number in sorted(self.note_numbers.items(), key=lambda kv: kv[1]):
            blocks = self.doc.footnotes.get(note_id, [])
            rendered = [self.block(b) for b in blocks] or [""]
            first, *rest = "\n\n".join(x for x in rendered if x).split("\n")
            lines = [f"[^{number}]: {first}".rstrip()]
            lines += ["    " + line if line else "" for line in rest]
            notes.append("\n".join(lines))
        if notes:
            out.append("\n\n".join(notes))
        return "\n\n".join(out) + "\n"


def to_markdown(doc: Document) -> str:
    if doc.markdown is not None:
        return doc.markdown
    return _Writer(doc).write()


def read_md(data: bytes, lang: str | None = None) -> Document:
    """`.md` input is already the editable text (§5.2): keep it, read only the title."""
    from text2audio.core.languages import guess_language
    from text2audio.readers.txt import decode

    text = decode(data).replace("\r\n", "\n").replace("\r", "\n")
    text = text.rstrip("\n") + "\n"
    doc = Document(markdown=text)
    title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), None)
    doc.metadata.title = title
    doc.metadata.language = lang or guess_language(text)
    return doc
