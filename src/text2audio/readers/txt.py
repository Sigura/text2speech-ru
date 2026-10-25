"""Plain-text reader (SPEC §6.3): encoding detection and heuristic structure.

Paragraphs are found in one of three layouts: blank lines between paragraphs, an
indented first line (typical of lib.ru texts, hard-wrapped or not), or one paragraph
per line. Headings come from the language pack's patterns ("Глава I", "Часть
первая") plus language-free ones (a Roman or Arabic numeral alone on a line); a
heading followed directly by a short line takes it as its title. A table of
contents after a front-matter heading ("Содержание") is skipped. The result carries
a heading confidence for the split plan (§6.8.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from charset_normalizer import from_bytes

from text2audio.core.contracts import LanguageProfile
from text2audio.core.languages import guess_language, profile
from text2audio.readers.document import (
    DINKUS,
    Block,
    Document,
    Heading,
    Inline,
    Paragraph,
    SceneBreak,
)

MAX_HEADING_CHARS = 80
MAX_SUBTITLE_CHARS = 60
_GENERIC_CHAPTER = (r"[IVXLCDM]{1,7}\.?", r"\d{1,3}\.?")
_HYPHEN_BREAK = re.compile(r"(\w)-$")


def decode(data: bytes) -> str:
    for bom, codec in (
        (b"\xef\xbb\xbf", "utf-8"),
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
    ):
        if data.startswith(bom):
            return data.decode(codec)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    best = from_bytes(data).best()
    return str(best) if best is not None else data.decode("cp1251", errors="replace")


@dataclass
class _Para:
    text: str
    gap_before: bool  # a blank line precedes it
    raw: list[str]  # the stripped source lines

    @property
    def lines(self) -> int:
        return len(self.raw)


def _paragraphs(text: str) -> list[_Para]:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    filled = [line for line in lines if line.strip()]
    if not filled:
        return []
    indented = sum(1 for line in filled if line[:1].isspace()) / len(filled)
    blanks = sum(1 for line in lines if not line.strip()) / len(filled)
    indent_mode = 0.05 < indented < 0.95
    line_mode = not indent_mode and blanks < 0.05
    out: list[_Para] = []
    current: list[str] = []
    current_gap = blank_seen = True

    def emit() -> None:
        joined = current[0].strip()
        for line in current[1:]:
            part = line.strip()
            if _HYPHEN_BREAK.search(joined) and part[:1].islower():
                joined = joined[:-1] + part  # re-join a word hyphenated at the line end
            else:
                joined += " " + part
        out.append(_Para(joined, current_gap, [line.strip() for line in current]))
        current.clear()

    for line in lines:
        if not line.strip():
            if current:
                emit()
            blank_seen = True
            continue
        if current and (line_mode or (indent_mode and line[:1].isspace())):
            emit()
        if not current:
            current_gap = blank_seen
        current.append(line)
        blank_seen = False
    if current:
        emit()
    return out


def _matches(patterns: tuple[str, ...], line: str) -> bool:
    return any(re.fullmatch(p, line, re.IGNORECASE) for p in patterns)


class _Builder:
    def __init__(self, lang: LanguageProfile | None) -> None:
        self.lang = lang
        self.parts = lang.part_headings if lang else ()
        self.chapters = (lang.chapter_headings if lang else ()) + _GENERIC_CHAPTER
        self.front = lang.front_matter if lang else ()

    def kind(self, para: _Para) -> str:
        line = para.text.strip()
        if para.lines > 1 or len(line) > MAX_HEADING_CHARS:
            return "text"
        if DINKUS.fullmatch(line):
            return "break"
        if _matches(self.front, line):
            return "front"
        if _matches(self.parts, line):
            return "part"
        if _matches(self.chapters, line):
            return "chapter"
        return "text"

    def split_short_blocks(self, paras: list[_Para]) -> list[_Para]:
        """A block of short lines with a heading among them is a list, not prose.

        Contents lists, "Глава 1" followed by its title, title pages: one line each.
        """
        out: list[_Para] = []
        for p in paras:
            if p.lines > 1 and all(len(x) <= MAX_HEADING_CHARS for x in p.raw):
                singles = [_Para(x, i == 0 and p.gap_before, [x]) for i, x in enumerate(p.raw)]
                if any(self.kind(s) != "text" for s in singles):
                    out += singles
                    continue
            out.append(p)
        return out

    def build(self, paras: list[_Para]) -> Document:
        doc = Document()
        paras = self.split_short_blocks(paras)
        kinds = [self.kind(p) for p in paras]
        has_parts = "part" in kinds
        level = {"part": 2, "chapter": 3 if has_parts else 2}
        blocks: list[Block] = []
        i, skipped_toc = 0, 0
        first_heading = next(
            (n for n, k in enumerate(kinds) if k in level or k == "front"), len(paras)
        )
        preamble = paras[:first_heading]
        title_lines = [x for p in preamble for x in p.raw]
        if 0 < len(title_lines) <= 3 and all(len(x) <= MAX_HEADING_CHARS for x in title_lines):
            title = ". ".join(x.rstrip(".") for x in title_lines)
            blocks.append(Heading(1, [Inline("text", title)]))
            doc.metadata.title = title
            i = first_heading
        while i < len(paras):
            para, kind = paras[i], kinds[i]
            if kind == "front":  # skip the contents list that follows
                i += 1
                first_entry = paras[i].text if i < len(paras) else None
                while i < len(paras) and len(paras[i].text) <= MAX_HEADING_CHARS + 20:
                    if skipped_toc and paras[i].text == first_entry:
                        break  # the book itself starts: its first heading again
                    if kinds[i] in level and i + 1 < len(paras) and len(paras[i + 1].text) > 200:
                        break  # a heading followed by prose
                    skipped_toc += 1
                    i += 1
                continue
            if kind == "break":
                blocks.append(SceneBreak())
            elif kind in level:
                title = para.text.strip()
                nxt = paras[i + 1] if i + 1 < len(paras) else None
                if (
                    nxt is not None
                    and not nxt.gap_before
                    and kinds[i + 1] == "text"
                    and nxt.lines == 1
                    and len(nxt.text) <= MAX_SUBTITLE_CHARS
                    and not nxt.text.rstrip().endswith((".", "!", "?", "…"))
                ):
                    title = f"{title.rstrip('.')}. {nxt.text.strip()}"
                    i += 1
                blocks.append(Heading(level[kind], [Inline("text", title)]))
            else:
                blocks.append(Paragraph([Inline("text", para.text)]))
            i += 1
        doc.blocks = blocks
        headings = sum(1 for b in blocks if isinstance(b, Heading) and b.level > 1)
        by_pack = sum(
            1
            for p, k in zip(paras, kinds, strict=True)
            if k in level and not _matches(_GENERIC_CHAPTER, p.text.strip())
        )
        doc.heading_confidence = 0.0 if headings < 2 else (0.9 if by_pack >= 2 else 0.6)
        if skipped_toc:
            doc.warnings.append(f"skipped a table of contents ({skipped_toc} lines)")
        return doc


def read_txt(data: bytes, lang: str | None = None) -> Document:
    text = decode(data)
    code = lang or guess_language(text)
    doc = _Builder(profile(code)).build(_paragraphs(text))
    doc.metadata.language = code
    if code is not None and profile(code) is None:
        doc.warnings.append(f"no language pack for {code!r}: generic headings only")
    return doc
