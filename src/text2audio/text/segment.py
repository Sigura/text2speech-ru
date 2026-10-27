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

`<!-- t2a: … -->` directives (E3, `text/directives.py`) set a segment's voice, rate,
emotion and pause, or skip its text. Inline ones travel through dialogue and sentence
splitting as noncharacter markers (U+FDD0 + index) placed right after the word before
them, so the splitters still see the punctuation; a sentence holding a marker becomes
a chunk of its own.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from text2audio.core.contracts import LanguageProfile, Segment
from text2audio.readers.document import (
    Block,
    Directive,
    Document,
    Heading,
    Inline,
    Paragraph,
    Poem,
    Quote,
)
from text2audio.readers.markdown import parse_markdown
from text2audio.text import directives
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
_MARK0 = 0xFDD0
_MAX_MARKS = 32  # U+FDD0–U+FDEF: noncharacters, never in real text
_MARK = re.compile("[﷐-﷯]")
_DEFAULT_INTENSITY = 0.5  # `emotion=` without `intensity=`


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
        self.settings = directives.Settings()
        self.marks: list[directives.Directive] = []
        self.fixed: set[str] = set()  # ids of segments whose pause a directive set
        self.warnings: list[dict[str, str]] = []

    def add(
        self,
        text: str,
        role: str,
        pause: str,
        level: int | None = None,
        settings: directives.Settings | None = None,
    ) -> None:
        text = " ".join(text.split()) if role != "poem" else text.strip()
        s = settings or self.settings
        if not text:
            return
        if s.skip:
            self.pause_last(pause)
            return
        self.counter += 1
        self.segments.append(
            Segment(
                id=f"ch{self.chapter:02d}-s{self.counter:04d}",
                chapter=self.chapter,
                text=text,
                lang=self.lang,
                role=role,
                voice=s.voice or ("dialogue" if role == "dialogue" else "narrator"),
                emotion=s.emotion,
                intensity=(
                    (s.intensity if s.intensity is not None else _DEFAULT_INTENSITY)
                    if s.emotion
                    else 0.0
                ),
                rate=s.rate or 1.0,
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
                last.pause = pause
                if last.id not in self.fixed:
                    last.pause_after_ms = self.pauses[pause]

    def set_pause(self, ms: int) -> None:
        if self.segments:
            self.segments[-1].pause_after_ms = ms
            self.fixed.add(self.segments[-1].id)

    def warn(self, where: str, d: directives.Directive) -> None:
        self.warnings += [{"segment": where, "warning": w} for w in d.warnings]

    def next_id(self) -> str:
        return f"ch{self.chapter:02d}-s{self.counter + 1:04d}"

    def directive(self, block: Directive) -> None:
        d = directives.parse(block.body)
        self.warn(f"book.md:{block.line}", d)
        if d.pause_ms is not None:
            self.set_pause(d.pause_ms)
        self.settings = self.settings.apply(d)

    def marked(self, inlines: list[Inline], *, reset: bool = True) -> str:
        """Plain text with a marker for every inline directive (module docstring)."""
        if reset:
            self.marks = []
        text = ""
        leading = ""
        for item in inlines:
            if item.kind == "note":
                continue
            if item.kind != "directive":
                text += item.text
                continue
            d = directives.parse(item.text)
            if len(self.marks) == _MAX_MARKS:
                d.warnings = [f"t2a: more than {_MAX_MARKS} directives in one block; ignored"]
                self.warn(self.next_id(), d)
                continue
            self.marks.append(d)
            mark = chr(_MARK0 + len(self.marks) - 1)
            cut = len(text)
            while cut and not (text[cut - 1].isalnum() or _MARK.match(text[cut - 1])):
                cut -= 1
            if cut:
                text = text[:cut] + mark + text[cut:]
            else:
                leading += mark
        if leading:  # nothing before it: the directive belongs to the first word
            first = re.search(r"\w+", text)
            at = first.end() if first else len(text)
            text = text[:at] + leading + text[at:]
        return text

    def unmark(self, text: str) -> tuple[str, list[directives.Directive]]:
        """Text without markers, and the directives they stood for."""
        found = [self.marks[ord(m) - _MARK0] for m in _MARK.findall(text)]
        return (_MARK.sub("", text), found) if found else (text, [])

    def effect(
        self, found: list[directives.Directive]
    ) -> tuple[directives.Settings | None, int | None]:
        """Settings and pause of the next segment's inline directives (None: none)."""
        if not found:
            return None, None
        settings, pause = self.settings, None
        for d in found:
            self.warn(self.next_id(), d)
            settings = settings.apply(d)
            pause = d.pause_ms if d.pause_ms is not None else pause
        return settings, pause

    def prose(self, text: str, role: str, end: str = "paragraph") -> None:
        # A sentence with directives is packed alone: its settings differ.
        runs: list[tuple[list[str], list[directives.Directive]]] = []
        for sentence in split_sentences(text, self.abbreviations):
            clean, found = self.unmark(sentence)
            if not found and runs and not runs[-1][1]:
                runs[-1][0].append(clean)
            else:
                runs.append(([clean], found))
        for r, (sentences, found) in enumerate(runs):
            settings, pause = self.effect(found)
            chunks = pack(sentences, self.max_chars)
            for n, chunk in enumerate(chunks):
                last = r == len(runs) - 1 and n == len(chunks) - 1
                self.add(chunk, role, end if last else "sentence", settings=settings)
            if pause is not None:
                self.set_pause(pause)

    def paragraph(self, text: str) -> None:
        parts = split_dialogue(text, self.remark_words) if self.dialogue else [("narration", text)]
        for n, (role, part) in enumerate(parts):
            self.prose(part, role, "paragraph" if n == len(parts) - 1 else "remark")

    def block(self, block: Block, role: str = "narration") -> None:
        if isinstance(block, Heading):
            role = "title" if block.level == 1 else "heading"
            text, found = self.unmark(self.marked(block.inlines))
            settings, pause = self.effect(found)
            self.add(text, role, "heading", block.level, settings)
            if pause is not None:
                self.set_pause(pause)
        elif isinstance(block, Paragraph) and role == "narration":
            self.paragraph(self.marked(block.inlines))
        elif isinstance(block, Paragraph):
            self.prose(self.marked(block.inlines), role)
        elif isinstance(block, Quote):
            for inner in block.blocks:
                self.block(inner, "quote")
            if block.author:
                self.prose(self.marked(block.author), "quote")
            self.pause_last("quote")
        elif isinstance(block, Poem):
            self.marks = []
            for stanza in block.stanzas:
                marked = [self.marked(line, reset=False).strip() for line in stanza]
                settings, pause = self.effect(self.unmark("".join(marked))[1])
                lines = [x for x in (_MARK.sub("", m).strip() for m in marked) if x]
                text = "\n".join(lines)
                if len(text) <= self.max_chars:
                    self.add(text, "poem", "stanza", settings=settings)
                else:
                    chunks = pack(lines, self.max_chars)
                    for n, chunk in enumerate(chunks):
                        last = n == len(chunks) - 1
                        self.add(chunk, "poem", "stanza" if last else "sentence", None, settings)
                if pause is not None:
                    self.set_pause(pause)
        elif isinstance(block, Directive):
            self.directive(block)
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
    warnings: list[dict[str, str]] | None = None,
) -> list[Segment]:
    """Segments of `book.md`; directive problems are appended to `warnings`."""
    builder = _Builder(lang, profile, max_chars, seed)
    segments = builder.build(parse_markdown(markdown, directives=True))
    if warnings is not None:
        warnings += builder.warnings
    return segments


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
