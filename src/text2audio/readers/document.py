"""In-memory book model that every reader produces (SPEC §5.1 "Parse to Markdown").

Readers map their format onto these blocks; `readers.markdown` writes them as the
§6.4.1 dialect. Inline text is a list of spans so emphasis survives without the
reader having to know Markdown escaping rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

InlineKind = Literal["text", "emph", "strong", "note"]


@dataclass
class Inline:
    """A run of text. `note` is a footnote reference; its `text` is the footnote id."""

    kind: InlineKind
    text: str


@dataclass
class Heading:
    level: int  # 1 = book title, 2 = chapter, 3+ = sections (§6.4.1)
    inlines: list[Inline]


@dataclass
class Paragraph:
    inlines: list[Inline]


@dataclass
class Quote:
    """Block quote, epigraph or citation; `author` is its attribution line."""

    blocks: list[Block]
    author: list[Inline] | None = None
    kind: Literal["quote", "epigraph"] = "quote"


@dataclass
class Poem:
    stanzas: list[list[list[Inline]]]  # stanza → line → inlines
    title: list[Inline] | None = None
    author: list[Inline] | None = None


@dataclass
class SceneBreak:
    pass


Block = Heading | Paragraph | Quote | Poem | SceneBreak


@dataclass
class Cover:
    data: bytes
    media_type: str  # e.g. "image/jpeg"

    @property
    def extension(self) -> str:
        return {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif"}.get(
            self.media_type, "bin"
        )


@dataclass
class Metadata:
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    language: str | None = None
    series: str | None = None
    series_index: int | None = None
    genres: list[str] = field(default_factory=list)
    translators: list[str] = field(default_factory=list)
    annotation: str | None = None
    date: str | None = None
    publisher: str | None = None
    isbn: str | None = None


@dataclass
class Document:
    metadata: Metadata = field(default_factory=Metadata)
    blocks: list[Block] = field(default_factory=list)
    footnotes: dict[str, list[Block]] = field(default_factory=dict)  # id → body
    cover: Cover | None = None
    warnings: list[str] = field(default_factory=list)
    # None = structure comes from markup (FB2, EPUB) and is trusted; TXT/PDF heuristics
    # set 0..1 so the split plan can refuse to split on doubtful headings (§6.8.1).
    heading_confidence: float | None = None
    # Markdown input in the §6.4.1 dialect is kept verbatim instead of being re-written.
    markdown: str | None = None

    def chapters(self) -> int:
        if self.markdown is not None:
            return sum(1 for line in self.markdown.splitlines() if line.startswith("## "))
        return sum(1 for b in self.blocks if isinstance(b, Heading) and b.level == 2)

    def has_title(self) -> bool:
        if self.markdown is not None:
            return self.markdown.lstrip().startswith("# ")
        return (
            bool(self.blocks) and isinstance(self.blocks[0], Heading) and self.blocks[0].level == 1
        )


def plain(inlines: list[Inline]) -> str:
    """Text of inline spans without markup or note references."""
    return "".join(i.text for i in inlines if i.kind != "note")
