"""EPUB 2/3 reader (SPEC §6.3).

container.xml → OPF (metadata, manifest, spine, cover) → table of contents (EPUB 3 nav
document or EPUB 2 NCX) → XHTML of each spine item. Chapters come from the table of
contents: an entry starts a heading whose level follows its depth (top level = `##`),
and an in-text `<h1>`–`<h6>` with the same title is not repeated. Without a usable
table of contents the XHTML headings are used, the largest one becoming `##`.

Footnotes: EPUB 3 `epub:type="noteref"` / `footnote|endnote|rearnote`, and the
common EPUB 2 pattern of a short link ("1", "[1]", "*") to an element elsewhere.
Notes are moved to the footnote list and never read in place.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import unquote
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
    drop_empty_headings,
    plain,
)
from text2audio.readers.safety import (
    InputError,
    SafeZip,
    local_name,
    open_zip,
    parse_xhtml,
    parse_xml,
)

EPUB_NS = "http://www.idpf.org/2007/ops"
_HEADINGS = {f"h{n}": n for n in range(1, 7)}
_NOTE_TYPES = {"footnote", "endnote", "rearnote", "note"}
_NOTE_LABEL = re.compile(r"\[?\(?\s*(\d{1,4}|[*†‡§]+|[a-z])\s*\)?\]?")
_POEM_CLASS = re.compile(r"\b(poem|poetry|stanza|verse)\b", re.I)
_EPIGRAPH_CLASS = re.compile(r"\bepigraph\b", re.I)
_AUTHOR_CLASS = re.compile(r"\b(text-?author|attribution|signature|epigraph-?author|cite)\b", re.I)
_DINKUS = re.compile(r"[*•·◊⁂~#=_\-–—\s]{1,20}")
_SKIP = {"script", "style", "head", "img", "image", "svg", "audio", "video", "object", "nav"}
_INLINE: dict[str, InlineKind] = {"em": "emph", "i": "emph", "cite": "emph", "dfn": "emph"}
_STRONG = {"strong", "b"}
_BLOCKS = {
    "p", "div", "section", "article", "blockquote", "ul", "ol", "li", "table", "tr",
    "hr", "h1", "h2", "h3", "h4", "h5", "h6", "header", "footer", "aside", "main",
    "figure", "figcaption", "dl", "dt", "dd", "pre", "address", "body",
}  # fmt: skip


def _attr(el: Element, name: str) -> str | None:
    for key, value in el.attrib.items():
        if local_name(key) == name:
            return value
    return None


def _types(el: Element) -> set[str]:
    return (
        set((_attr(el, "type") or "").split())
        if any(k.startswith(f"{{{EPUB_NS}}}") or k == "epub:type" for k in el.attrib)
        else set()
    )


def _text(el: Element | None) -> str:
    """Plain text of an element; `<br>` separates words."""
    if el is None:
        return ""
    parts: list[str] = []

    def walk(e: Element) -> None:
        parts.append(e.text or "")
        for c in e:
            if local_name(c.tag) == "br":
                parts.append(" ")
            else:
                walk(c)
            parts.append(c.tail or "")

    walk(el)
    return " ".join("".join(parts).split())


def _norm_title(text: str) -> str:
    return re.sub(r"[\W_]+", " ", text).strip().lower()


@dataclass
class TocEntry:
    path: str
    fragment: str | None
    title: str
    depth: int


@dataclass
class _Package:
    zip: SafeZip
    opf_dir: str
    manifest: dict[str, tuple[str, str, set[str]]]  # id → (path, media type, properties)
    spine: list[str]  # paths
    metadata: Metadata
    cover_path: str | None
    toc: list[TocEntry] = field(default_factory=list)
    nav_path: str | None = None


def _resolve(base_dir: str, href: str) -> tuple[str, str | None]:
    href = unquote(href)
    path, _, fragment = href.partition("#")
    full = posixpath.normpath(posixpath.join(base_dir, path)) if path else ""
    return full, fragment or None


def _child(el: Element, name: str) -> Element | None:
    return next((c for c in el if local_name(c.tag) == name), None)


def _package(data: bytes) -> _Package:
    archive = open_zip(data)
    container = parse_xml(archive.read("META-INF/container.xml"))
    rootfile = next((e for e in container.iter() if local_name(e.tag) == "rootfile"), None)
    opf_path = rootfile.get("full-path") if rootfile is not None else None
    if not opf_path:
        raise InputError("EPUB has no package document in META-INF/container.xml")
    opf = parse_xml(archive.read(opf_path))
    opf_dir = posixpath.dirname(opf_path)
    manifest: dict[str, tuple[str, str, set[str]]] = {}
    for item in opf.iter():
        if local_name(item.tag) == "item" and item.get("id") and item.get("href"):
            path, _ = _resolve(opf_dir, item.get("href", ""))
            props = set((item.get("properties") or "").split())
            manifest[item.get("id", "")] = (path, item.get("media-type", ""), props)
    spine_el = next((e for e in opf.iter() if local_name(e.tag) == "spine"), None)
    spine = []
    if spine_el is not None:
        for ref in spine_el:
            idref = ref.get("idref")
            if idref in manifest and ref.get("linear", "yes") != "no":
                spine.append(manifest[idref][0])
    meta, cover_id = _opf_metadata(opf)
    cover_path = next((p for p, _, props in manifest.values() if "cover-image" in props), None)
    if cover_path is None and cover_id in manifest:
        cover_path = manifest[cover_id][0]
    pkg = _Package(archive, opf_dir, manifest, spine, meta, cover_path)
    nav = next((p for p, _, props in manifest.values() if "nav" in props), None)
    if nav is not None:
        pkg.nav_path = nav
        pkg.toc = _nav_toc(archive, nav)
    if not pkg.toc and spine_el is not None and spine_el.get("toc") in manifest:
        pkg.toc = _ncx_toc(archive, manifest[spine_el.get("toc", "")][0])
    return pkg


def _opf_metadata(opf: Element) -> tuple[Metadata, str | None]:
    meta = Metadata()
    md = next((e for e in opf.iter() if local_name(e.tag) == "metadata"), None)
    cover_id = None
    if md is None:
        return meta, None
    refines: dict[str, dict[str, str]] = {}
    for el in md:
        if local_name(el.tag) == "meta" and el.get("refines"):
            refines.setdefault(el.get("refines", "").lstrip("#"), {})[el.get("property", "")] = (
                el.text or ""
            ).strip()
    for el in md:
        name, text = local_name(el.tag), (el.text or "").strip()
        if name == "title" and not meta.title:
            meta.title = text or None
        elif name == "creator" and text:
            role = _attr(el, "role") or refines.get(el.get("id", ""), {}).get("role", "aut")
            (meta.translators if role == "trl" else meta.authors).append(text)
        elif name == "contributor" and text and _attr(el, "role") == "trl":
            meta.translators.append(text)
        elif name == "language" and not meta.language:
            meta.language = text.split("-")[0].lower() or None
        elif name == "date" and not meta.date:
            meta.date = text or None
        elif name == "publisher":
            meta.publisher = text or None
        elif name == "description":
            meta.annotation = re.sub(r"<[^>]+>", " ", text).strip() or None
        elif name == "subject" and text:
            meta.genres.append(text)
        elif name == "identifier" and "isbn" in (text + (_attr(el, "scheme") or "")).lower():
            meta.isbn = text.split(":")[-1].strip() or None
        elif name == "meta":
            if el.get("name") == "cover":
                cover_id = el.get("content")
            elif el.get("name") == "calibre:series":
                meta.series = el.get("content")
            elif el.get("name") == "calibre:series_index":
                with_index = (el.get("content") or "").split(".")[0]
                meta.series_index = int(with_index) if with_index.isdigit() else None
            elif el.get("property") == "belongs-to-collection" and text:
                meta.series = text
                index = refines.get(el.get("id", ""), {}).get("group-position", "")
                meta.series_index = int(index) if index.isdigit() else None
    return meta, cover_id


def _nav_toc(archive: SafeZip, nav_path: str) -> list[TocEntry]:
    root = parse_xhtml(archive.read(nav_path))
    navs = [e for e in root.iter() if local_name(e.tag) == "nav"]
    toc_nav = next((n for n in navs if "toc" in _types(n)), navs[0] if navs else None)
    if toc_nav is None:
        return []
    base = posixpath.dirname(nav_path)
    out: list[TocEntry] = []

    def walk(ol: Element, depth: int) -> None:
        for li in ol:
            if local_name(li.tag) != "li":
                continue
            link = next((c for c in li.iter() if local_name(c.tag) in ("a", "span")), None)
            if link is not None and link.get("href"):
                path, frag = _resolve(base, link.get("href", ""))
                out.append(TocEntry(path, frag, _text(link), depth))
            for sub in li:
                if local_name(sub.tag) == "ol":
                    walk(sub, depth + 1)

    top = next((c for c in toc_nav.iter() if local_name(c.tag) == "ol"), None)
    if top is not None:
        walk(top, 0)
    return out


def _ncx_toc(archive: SafeZip, ncx_path: str) -> list[TocEntry]:
    root = parse_xml(archive.read(ncx_path))
    base = posixpath.dirname(ncx_path)
    out: list[TocEntry] = []
    nav_map = next((e for e in root.iter() if local_name(e.tag) == "navMap"), None)

    def walk(parent: Element, depth: int) -> None:
        for point in parent:
            if local_name(point.tag) != "navPoint":
                continue
            label = next((e for e in point.iter() if local_name(e.tag) == "text"), None)
            content = _child(point, "content")
            if content is not None and content.get("src"):
                path, frag = _resolve(base, content.get("src", ""))
                out.append(TocEntry(path, frag, _text(label), depth))
            walk(point, depth + 1)

    if nav_map is not None:
        walk(nav_map, 0)
    return out


class _Reader:
    def __init__(self, pkg: _Package) -> None:
        self.pkg = pkg
        self.docs: dict[str, Element] = {}
        self.ids: dict[tuple[str, str], Element] = {}
        self.skipped: Counter[str] = Counter()
        self.path = ""
        self.pending_toc: dict[str, list[TocEntry]] = {}
        self.toc_titles: set[str] = set()
        self.base_level = 2
        self.h_shift = 1  # XHTML hN → level N + shift when there is no TOC
        self.current_level = 1

    def load(self) -> None:
        for path in self.pkg.spine:
            try:
                self.docs[path] = parse_xhtml(self.pkg.zip.read(path))
            except InputError as exc:
                raise InputError(f"{path}: {exc}") from exc
            for el in self.docs[path].iter():
                if el.get("id"):
                    self.ids[(path, el.get("id", ""))] = el

    # -- notes ------------------------------------------------------------------
    def find_notes(self, doc: Document) -> None:
        refs: list[tuple[Element, str, str]] = []
        for path, root in self.docs.items():
            base = posixpath.dirname(path)
            for a in root.iter():
                if local_name(a.tag) != "a" or not a.get("href"):
                    continue
                target_path, frag = _resolve(base, a.get("href", ""))
                target_path = target_path or path
                if frag is None or (target_path, frag) not in self.ids:
                    continue
                target = self.ids[(target_path, frag)]
                if local_name(target.tag) == "a" and target.get("href"):
                    a.set("data-t2a-backlink", "1")
                    continue  # a back-link from a note to its reference
                semantic = "noteref" in _types(a) or bool(_types(target) & _NOTE_TYPES)
                looks_like = bool(_NOTE_LABEL.fullmatch(_text(a))) and target_path in self.docs
                if semantic or (looks_like and target is not a):
                    refs.append((a, target_path, frag))
        for a, target_path, frag in refs:
            target = self.ids[(target_path, frag)]
            if local_name(target.tag) == "a":  # an anchor inside the note: use its block
                target = self._block_parent(target_path, target)
            note_id = f"{target_path}#{frag}"
            a.set("data-t2a-note", note_id)
            if note_id not in doc.footnotes:
                target.set("data-t2a-note-body", note_id)
                if any(local_name(c.tag) in _BLOCKS for c in target):
                    body = self.blocks(list(target), in_note=True)
                else:
                    inl = self.inlines(target, in_note=True)
                    body = [Paragraph(inl)] if plain(inl).strip() else []
                doc.footnotes[note_id] = body

    def _block_parent(self, path: str, el: Element) -> Element:
        parents = {c: p for p in self.docs[path].iter() for c in p}
        node = el
        while node in parents and local_name(node.tag) not in ("p", "li", "div", "aside", "dd"):
            node = parents[node]
        return node

    # -- inline -----------------------------------------------------------------
    def inlines(
        self, el: Element, kind: InlineKind = "text", *, in_note: bool = False, br: str = " "
    ) -> list[Inline]:
        """Inline spans of `el`; `br` is what a `<br>` becomes (". " in headings)."""
        out: list[Inline] = []
        if el.text:
            out.append(Inline(kind, el.text))
        for child in el:
            name = local_name(child.tag)
            note = child.get("data-t2a-note")
            if note and not in_note:
                out.append(Inline("note", note))
            elif (note and in_note) or child.get("data-t2a-backlink"):
                pass  # a back-link from a note to its reference: not read
            elif name in _SKIP:
                if name not in ("script", "style"):
                    self.skipped[name] += 1
            elif name == "br":
                ends_sentence = plain(out).rstrip().endswith((".", "!", "?", "…", ":"))
                out.append(Inline(kind, " " if ends_sentence else br))
            elif name in _INLINE:
                inner: InlineKind = "strong" if kind == "strong" else "emph"
                out.extend(self.inlines(child, inner, in_note=in_note, br=br))
            elif name in _STRONG:
                out.extend(self.inlines(child, "strong", in_note=in_note, br=br))
            else:
                out.extend(self.inlines(child, kind, in_note=in_note, br=br))
            if child.tail:
                out.append(Inline(kind, child.tail))
        return out

    # -- blocks -----------------------------------------------------------------
    def poem(self, el: Element) -> Poem:
        """Stanzas are block children that have block children of their own (or a
        poem/stanza class); otherwise the block children are lines of one stanza, and
        without block children the lines are separated by `<br>`."""

        def has_blocks(e: Element) -> bool:
            return any(local_name(c.tag) in _BLOCKS for c in e)

        children = [c for c in el if local_name(c.tag) in _BLOCKS]
        if any(has_blocks(c) or _POEM_CLASS.search(c.get("class", "")) for c in children):
            groups = children
        else:
            groups = [el]
        stanzas: list[list[list[Inline]]] = []
        for group in groups:
            lines = [c for c in group if local_name(c.tag) in _BLOCKS]
            stanza = [self.inlines(line) for line in lines] if lines else self._br_lines(group)
            stanza = [line for line in stanza if plain(line).strip()]
            if stanza:
                stanzas.append(stanza)
        return Poem(stanzas)

    def _br_lines(self, el: Element) -> list[list[Inline]]:
        lines: list[list[Inline]] = [[]]
        if el.text:
            lines[-1].append(Inline("text", el.text))
        for child in el:
            if local_name(child.tag) == "br":
                lines.append([])
            else:
                lines[-1].extend(self.inlines(child))
            if child.tail:
                lines[-1].append(Inline("text", child.tail))
        return lines

    def toc_headings(self, el: Element) -> list[Block]:
        out: list[Block] = []
        entries = self.pending_toc.get(self.path, [])
        for entry in list(entries):
            if entry.fragment == el.get("id"):
                entries.remove(entry)
                level = min(self.base_level + entry.depth, 6)
                out.append(Heading(level, [Inline("text", entry.title)]))
                self.current_level = level
        return out

    def blocks(self, elements: list[Element], *, in_note: bool = False) -> list[Block]:
        out: list[Block] = []
        for el in elements:
            if el.get("data-t2a-note-body") and not in_note:
                continue  # read as a footnote instead
            if not in_note and el.get("id"):
                out.extend(self.toc_headings(el))
            name = local_name(el.tag)
            cls = el.get("class", "")
            types = _types(el)
            if name in _SKIP or "pagebreak" in types:
                continue
            if name in _HEADINGS:
                text = _text(el)
                if not text:
                    continue
                if _norm_title(text) in self.toc_titles:
                    continue  # the TOC entry already gave this heading
                level = (
                    min(_HEADINGS[name] + self.h_shift, 6)
                    if not self.pkg.toc
                    else min(self.current_level + 1, 6)
                )
                out.append(Heading(level, self.inlines(el, br=". ")))
            elif name == "hr":
                out.append(SceneBreak())
            elif name == "blockquote" or _EPIGRAPH_CLASS.search(cls) or "epigraph" in types:
                out.append(self.quote(el, cls, types))
            elif _POEM_CLASS.search(cls):
                out.append(self.poem(el))
            elif name in ("ul", "ol", "table", "tr", "dl"):
                out.extend(self.blocks(list(el), in_note=in_note))
            elif name in ("p", "li", "dt", "dd", "figcaption", "pre", "address", "td", "th") or (
                name in _BLOCKS and not any(local_name(c.tag) in _BLOCKS for c in el)
            ):
                inl = self.inlines(el, in_note=in_note)
                text = plain(inl).strip()
                if _DINKUS.fullmatch(text):
                    out.append(SceneBreak())
                elif text or any(i.kind == "note" for i in inl):
                    out.append(Paragraph(inl))
            else:  # section, div, article, aside … with block children
                if el.text and el.text.strip():
                    out.append(Paragraph([Inline("text", el.text)]))
                out.extend(self.blocks(list(el), in_note=in_note))
        return out

    def quote(self, el: Element, cls: str, types: set[str]) -> Quote:
        children = list(el)
        author: list[Inline] | None = None
        for c in children:
            if _AUTHOR_CLASS.search(c.get("class", "")) or local_name(c.tag) == "cite":
                author = self.inlines(c)
                children.remove(c)
                break
        if author and author[0].kind == "text":  # "— Княжнин." → "Княжнин." (the writer adds —)
            author[0] = Inline("text", author[0].text.lstrip().lstrip("—–-").lstrip())
        blocks: list[Block] = self.blocks(children) if children else [Paragraph(self.inlines(el))]
        epigraph = bool(_EPIGRAPH_CLASS.search(cls)) or "epigraph" in types
        return Quote(blocks, author=author, kind="epigraph" if epigraph else "quote")

    # -- document ---------------------------------------------------------------
    def read(self) -> Document:
        doc = Document(metadata=self.pkg.metadata)
        self.load()
        self.find_notes(doc)
        toc = [e for e in self.pkg.toc if e.path in self.docs and e.title]
        if not toc:
            levels = [
                _HEADINGS[local_name(e.tag)]
                for root in self.docs.values()
                for e in root.iter()
                if local_name(e.tag) in _HEADINGS and _text(e)
            ]
            self.h_shift = 2 - min(levels) if levels else 1
        for entry in toc:
            self.pending_toc.setdefault(entry.path, []).append(entry)
            self.toc_titles.add(_norm_title(entry.title))
        if doc.metadata.title:
            doc.blocks.append(Heading(1, [Inline("text", doc.metadata.title)]))
            self.toc_titles.add(_norm_title(doc.metadata.title))
        for path in self.pkg.spine:
            if path == self.pkg.nav_path or path not in self.docs:
                continue
            self.path = path
            root = self.docs[path]
            body = next((e for e in root.iter() if local_name(e.tag) == "body"), root)
            # Entries for the file itself (no fragment) open before its content.
            for entry in [e for e in self.pending_toc.get(path, []) if e.fragment is None]:
                self.pending_toc[path].remove(entry)
                level = min(self.base_level + entry.depth, 6)
                doc.blocks.append(Heading(level, [Inline("text", entry.title)]))
                self.current_level = level
            doc.blocks.extend(self.blocks(list(body)))
        doc.blocks = drop_empty_headings(doc.blocks)
        doc.cover = self.cover()
        if self.skipped:
            names = ", ".join(f"{n}×{c}" for n, c in sorted(self.skipped.items()) if c)
            if names:
                doc.warnings.append(f"skipped (not narrated): {names}")
        return doc

    def cover(self) -> Cover | None:
        path = self.pkg.cover_path
        if not path:
            return None
        media = next((m for p, m, _ in self.pkg.manifest.values() if p == path), "")
        if not media.startswith("image/"):
            return None
        return Cover(self.pkg.zip.read(path), media)


def read_epub(data: bytes) -> Document:
    return _Reader(_package(data)).read()
