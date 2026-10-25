"""FictionBook 2 reader (SPEC §6.3).

`<section>` nesting gives the heading level (top level = chapter, `##`), `<subtitle>`
becomes a heading one level below its section, `<epigraph>` / `<cite>` become quotes
with their `<text-author>`, `<poem>` keeps stanzas and lines, and footnotes come from
`<body name="notes">` linked by `l:href`. The XML declaration's encoding (often
windows-1251) is honoured by the parser. Elements outside the schema — library
banners such as `<myheader>` / `<myfooter>` — are dropped and reported, never read.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections import Counter
from xml.etree.ElementTree import Element

from text2audio.readers.document import (
    Block,
    Cover,
    Document,
    Heading,
    Inline,
    InlineKind,
    Metadata,
    Paragraph,
    Poem,
    Quote,
    SceneBreak,
    plain,
)
from text2audio.readers.safety import InputError, local_name, open_zip, parse_xml

MAX_COVER_BYTES = 20 * 2**20
_DINKUS = re.compile(r"[*•·◊⁂~#=_\-–—\s]{1,20}")  # a scene-break line, never empty here
_ENDS_TITLE_LINE = (".", "!", "?", "…", ":", ";", ",", "—", "–", "-")
_INLINE_KINDS: dict[str, InlineKind] = {"emphasis": "emph", "strong": "strong"}
_TRANSPARENT_INLINE = {"style", "strikethrough", "sub", "sup", "code", "a"}
_NOTE_BODIES = {"notes", "comments", "footnotes"}


def _children(el: Element, name: str) -> list[Element]:
    return [c for c in el if local_name(c.tag) == name]


def _child(el: Element | None, name: str) -> Element | None:
    if el is None:
        return None
    found = _children(el, name)
    return found[0] if found else None


def _text(el: Element | None) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def _href(el: Element) -> str | None:
    for key, value in el.attrib.items():
        if local_name(key) == "href":
            return value
    return None


def _person(el: Element) -> str:
    parts = [_text(_child(el, n)) for n in ("first-name", "middle-name", "last-name")]
    name = " ".join(p for p in parts if p)
    return name or _text(_child(el, "nickname"))


class _Reader:
    def __init__(self, root: Element) -> None:
        self.root = root
        self.note_ids: set[str] = set()
        self.dropped: Counter[str] = Counter()
        self.skipped: Counter[str] = Counter()

    # -- inline -----------------------------------------------------------------
    def inlines(self, el: Element, kind: InlineKind = "text") -> list[Inline]:
        out: list[Inline] = []
        if el.text:
            out.append(Inline(kind, el.text))
        for child in el:
            name = local_name(child.tag)
            href = _href(child) if name == "a" else None
            if href and href.startswith("#") and href[1:] in self.note_ids:
                out.append(Inline("note", href[1:]))
            elif name in _INLINE_KINDS:
                inner = "strong" if kind == "strong" else _INLINE_KINDS[name]
                out.extend(self.inlines(child, inner))
            elif name in _TRANSPARENT_INLINE:
                out.extend(self.inlines(child, kind))
            elif name == "image":
                self.skipped["inline image"] += 1
            else:
                out.extend(self.inlines(child, kind))
            if child.tail:
                out.append(Inline(kind, child.tail))
        return out

    def title_inlines(self, title: Element | None) -> list[Inline]:
        """`<title><p>ГЛАВА 1</p><p>Name</p></title>` → "ГЛАВА 1. Name"."""
        if title is None:
            return []
        parts = [self.inlines(p) for p in _children(title, "p")]
        parts = [p for p in parts if plain(p).strip()]
        out: list[Inline] = []
        for i, part in enumerate(parts):
            if i:
                last = plain(out).rstrip()
                out.append(Inline("text", " " if last.endswith(_ENDS_TITLE_LINE) else ". "))
            out.extend(part)
        return out

    # -- blocks -----------------------------------------------------------------
    def poem(self, el: Element) -> Poem:
        stanzas = []
        for stanza in _children(el, "stanza"):
            lines = [self.inlines(v) for v in _children(stanza, "v")]
            stanzas.append([line for line in lines if plain(line).strip()])
        author = _child(el, "text-author")
        title = self.title_inlines(_child(el, "title")) or None
        return Poem(
            stanzas, title=title, author=self.inlines(author) if author is not None else None
        )

    def quote(self, el: Element, kind: str) -> Quote:
        authors = _children(el, "text-author")
        author: list[Inline] | None = None
        for a in authors:
            line = self.inlines(a)
            author = line if author is None else [*author, Inline("text", ", "), *line]
        inner = [c for c in el if local_name(c.tag) != "text-author"]
        blocks = self.blocks(inner, level=6)
        return Quote(blocks, author=author, kind="epigraph" if kind == "epigraph" else "quote")

    def blocks(self, elements: list[Element], level: int) -> list[Block]:
        out: list[Block] = []
        for el in elements:
            name = local_name(el.tag)
            if name in ("p", "subtitle"):
                inl = self.inlines(el)
                text = plain(inl).strip()
                if _DINKUS.fullmatch(text):  # "* * *" and friends, as <p> or <subtitle>
                    out.append(SceneBreak())
                elif name == "subtitle":  # `level` is already one below the section's heading
                    out.append(Heading(min(level, 6), inl))
                elif text or any(i.kind == "note" for i in inl):
                    out.append(Paragraph(inl))
            elif name == "section":
                out.extend(self.section(el, level))
            elif name == "epigraph" or name == "cite":
                out.append(self.quote(el, name))
            elif name == "poem":
                out.append(self.poem(el))
            elif name == "annotation":
                out.extend(self.blocks(list(el), level))
            elif name == "table":
                for row in _children(el, "tr"):
                    cells = [_text(c) for c in row]
                    if any(cells):
                        out.append(Paragraph([Inline("text", ", ".join(c for c in cells if c))]))
            elif name in ("empty-line", "title"):
                continue
            elif name == "image":
                self.skipped["image"] += 1
            else:
                self.dropped[name] += 1
        return out

    def section(self, el: Element, level: int) -> list[Block]:
        out: list[Block] = []
        title = self.title_inlines(_child(el, "title"))
        if title:
            out.append(Heading(min(level, 6), title))
        out.extend(self.blocks(list(el), level + 1 if title else level))
        return out

    # -- document ---------------------------------------------------------------
    def unwrap(self, body: Element, doc: Document) -> list[Element]:
        """A lone top-level section that only wraps the book's sections is dropped.

        Its title usually repeats the author and book title, which `#` already reads.
        """
        elements = list(body)
        sections = [e for e in elements if local_name(e.tag) == "section"]
        if len(sections) != 1 or len(_children(sections[0], "section")) < 2:
            return elements
        wrapper = sections[0]
        title = plain(self.title_inlines(_child(wrapper, "title")))
        if title:
            doc.warnings.append(f"unwrapped the single top-level section «{title}»")
        i = elements.index(wrapper)
        inner = [c for c in wrapper if local_name(c.tag) != "title"]
        return elements[:i] + inner + elements[i + 1 :]

    def metadata(self) -> tuple[Metadata, str | None]:
        desc = _child(self.root, "description")
        info = _child(desc, "title-info")
        meta = Metadata()
        cover_href = None
        if info is not None:
            meta.title = _text(_child(info, "book-title")) or None
            meta.authors = [n for n in (_person(a) for a in _children(info, "author")) if n]
            meta.translators = [n for n in (_person(a) for a in _children(info, "translator")) if n]
            meta.genres = [_text(g) for g in _children(info, "genre") if _text(g)]
            meta.language = (_text(_child(info, "lang")) or "").lower() or None
            annotation = _child(info, "annotation")
            if annotation is not None:
                paras = [_text(p) for p in annotation.iter() if local_name(p.tag) == "p"]
                meta.annotation = "\n".join(p for p in paras if p) or _text(annotation) or None
            date = _child(info, "date")
            if date is not None:
                meta.date = date.get("value") or _text(date) or None
            seq = _child(info, "sequence")
            if seq is not None and seq.get("name"):
                meta.series = seq.get("name")
                number = seq.get("number", "")
                meta.series_index = int(number) if number.isdigit() else None
            image = _child(_child(info, "coverpage"), "image")
            if image is not None:
                cover_href = _href(image)
        publish = _child(desc, "publish-info")
        if publish is not None:
            meta.publisher = _text(_child(publish, "publisher")) or None
            meta.isbn = _text(_child(publish, "isbn")) or None
        return meta, cover_href

    def cover(self, href: str | None) -> Cover | None:
        if not href or not href.startswith("#"):
            return None
        for binary in _children(self.root, "binary"):
            if binary.get("id") == href[1:]:
                try:
                    data = base64.b64decode("".join((binary.text or "").split()), validate=True)
                except (binascii.Error, ValueError):
                    return None
                if not data or len(data) > MAX_COVER_BYTES:
                    return None
                return Cover(data, binary.get("content-type") or "image/jpeg")
        return None

    def read(self) -> Document:
        if local_name(self.root.tag) != "FictionBook":
            raise InputError("not a FictionBook document")
        meta, cover_href = self.metadata()
        bodies = _children(self.root, "body")
        main = [b for b in bodies if (b.get("name") or "").lower() not in _NOTE_BODIES]
        notes = [b for b in bodies if (b.get("name") or "").lower() in _NOTE_BODIES]
        note_sections = [s for b in notes for s in b.iter() if local_name(s.tag) == "section"]
        self.note_ids = {s.get("id", "") for s in note_sections} - {""}
        doc = Document(metadata=meta)
        for s in note_sections:
            content = [c for c in s if local_name(c.tag) not in ("title", "section")]
            doc.footnotes[s.get("id", "")] = self.blocks(content, level=6)
        for body in main:
            title = self.title_inlines(_child(body, "title"))
            if title:
                doc.blocks.append(Heading(1, title))
            elif meta.title and body is main[0]:
                doc.blocks.append(Heading(1, [Inline("text", meta.title)]))
            doc.blocks.extend(self.blocks(self.unwrap(body, doc), level=2))
        doc.cover = self.cover(cover_href)
        if self.dropped:
            names = ", ".join(f"<{n}>×{c}" for n, c in sorted(self.dropped.items()))
            doc.warnings.append(f"dropped elements outside the FB2 schema: {names}")
        if self.skipped:
            names = ", ".join(f"{n}×{c}" for n, c in sorted(self.skipped.items()))
            doc.warnings.append(f"skipped (not narrated): {names}")
        return doc


def read_fb2(data: bytes) -> Document:
    return _Reader(parse_xml(data)).read()


def read_fb2_zip(data: bytes) -> Document:
    archive = open_zip(data)
    books = [n for n in archive.names if n.lower().endswith(".fb2")]
    if len(books) != 1:
        raise InputError(f"expected one .fb2 file in the archive, found {len(books)}")
    return read_fb2(archive.read(books[0]))
