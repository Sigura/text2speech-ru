"""`<book>.md` → segment manifest `_work/segments.jsonl` (SPEC §6.4.2).

The manifest is always rebuilt from the Markdown the user may have edited. Segments
follow the text order: `#` title, then per chapter (`##`) its headings, narration,
quotes and poem stanzas (one stanza per segment), and — with the default
`chapter-end` footnote policy (§6.3) — the chapter's footnotes after its last
paragraph. Paragraphs are split into sentences and packed into chunks of at most
`max_chars`; a sentence is cut only when it is longer than that.

Each segment records the type of the boundary after it (`pause`, E1 in §6.7) and its
length: the core default for that type unless the language profile overrides it. When
two boundaries meet (a scene break after a paragraph), the longer type wins.
"""

from __future__ import annotations

import json
from pathlib import Path

from text2audio.core.contracts import LanguageProfile, Segment
from text2audio.readers.document import Block, Document, Heading, Paragraph, Poem, Quote, plain
from text2audio.readers.markdown import parse_markdown
from text2audio.text.dialogue import split_dialogue
from text2audio.text.sentences import pack, split_sentences

DEFAULT_MAX_CHARS = 300
PAUSE_MS = {
    "remark": 200,  # between a dialogue line and the author's remark
    "sentence": 300,
    "paragraph": 700,
    "stanza": 900,
    "quote": 1000,
    "heading": 1000,
    "scene": 1500,
    "chapter": 3000,
}


def _notes_in(block: Block) -> list[str]:
    inlines = []
    if isinstance(block, Paragraph | Heading):
        inlines = block.inlines
    elif isinstance(block, Quote):
        return [n for b in block.blocks for n in _notes_in(b)] + [
            i.text for i in (block.author or []) if i.kind == "note"
        ]
    elif isinstance(block, Poem):
        inlines = [i for stanza in block.stanzas for line in stanza for i in line]
    return [i.text for i in inlines if i.kind == "note"]


class _Builder:
    def __init__(self, lang: str, profile: LanguageProfile | None, max_chars: int, seed: int):
        self.lang = lang
        self.abbreviations = profile.abbreviations if profile else ()
        self.dialogue = profile is not None and profile.dialogue == "dash"
        self.remark_words = profile.remark_words if profile else ()
        overrides = profile.pauses_ms if profile else {}
        unknown = sorted(set(overrides) - set(PAUSE_MS))
        if unknown:
            raise ValueError(f"unknown pause type(s) in the {lang!r} profile: {unknown}")
        self.pauses = PAUSE_MS | overrides
        self.max_chars = max_chars
        self.seed = seed
        self.segments: list[Segment] = []
        self.chapter = 0
        self.counter = 0

    def add(self, text: str, role: str, pause: str, level: int | None = None) -> None:
        text = " ".join(text.split()) if role != "poem" else text.strip()
        if not text:
            return
        self.counter += 1
        self.segments.append(
            Segment(
                id=f"ch{self.chapter:02d}-s{self.counter:04d}",
                chapter=self.chapter,
                text=text,
                lang=self.lang,
                role=role,
                voice="dialogue" if role == "dialogue" else "narrator",
                pause=pause,
                pause_after_ms=self.pauses[pause],
                seed=self.seed,
                heading_level=level,
            )
        )

    def pause_last(self, pause: str) -> None:
        if self.segments:
            last = self.segments[-1]
            if PAUSE_MS[pause] > PAUSE_MS[last.pause]:
                last.pause, last.pause_after_ms = pause, self.pauses[pause]

    def prose(self, text: str, role: str, end: str = "paragraph") -> None:
        chunks = pack(split_sentences(text, self.abbreviations), self.max_chars)
        for n, chunk in enumerate(chunks):
            self.add(chunk, role, end if n == len(chunks) - 1 else "sentence")

    def paragraph(self, text: str) -> None:
        parts = split_dialogue(text, self.remark_words) if self.dialogue else [("narration", text)]
        for n, (role, part) in enumerate(parts):
            self.prose(part, role, "paragraph" if n == len(parts) - 1 else "remark")

    def block(self, block: Block, role: str = "narration") -> None:
        if isinstance(block, Heading):
            role = "title" if block.level == 1 else "heading"
            self.add(plain(block.inlines), role, "heading", block.level)
        elif isinstance(block, Paragraph) and role == "narration":
            self.paragraph(plain(block.inlines))
        elif isinstance(block, Paragraph):
            self.prose(plain(block.inlines), role)
        elif isinstance(block, Quote):
            for inner in block.blocks:
                self.block(inner, "quote")
            if block.author:
                self.prose(plain(block.author), "quote")
            self.pause_last("quote")
        elif isinstance(block, Poem):
            for stanza in block.stanzas:
                lines = [plain(line).strip() for line in stanza]
                text = "\n".join(x for x in lines if x)
                if len(text) <= self.max_chars:
                    self.add(text, "poem", "stanza")
                else:
                    chunks = pack(lines, self.max_chars)
                    for n, chunk in enumerate(chunks):
                        self.add(chunk, "poem", "stanza" if n == len(chunks) - 1 else "sentence")
        else:  # SceneBreak
            self.pause_last("scene")

    def notes(self, doc: Document, ids: list[str]) -> None:
        for note_id in dict.fromkeys(ids):
            for block in doc.footnotes.get(note_id, []):
                self.block(block, "footnote")

    def build(self, doc: Document) -> list[Segment]:
        pending: list[str] = []
        for block in doc.blocks:
            if isinstance(block, Heading) and block.level == 2:
                self.notes(doc, pending)
                pending = []
                self.pause_last("chapter")
                self.chapter += 1
                self.counter = 0
            self.block(block)
            pending += _notes_in(block)
        self.notes(doc, pending)
        return self.segments


def segment_markdown(
    markdown: str,
    lang: str,
    profile: LanguageProfile | None = None,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    seed: int = 1234,
) -> list[Segment]:
    return _Builder(lang, profile, max_chars, seed).build(parse_markdown(markdown))


def write_manifest(path: Path, segments: list[Segment]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(s.model_dump(), ensure_ascii=False) for s in segments]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_manifest(path: Path) -> list[Segment]:
    return [
        Segment.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
