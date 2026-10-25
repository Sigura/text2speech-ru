"""Sentence splitting and chunking for synthesis (SPEC §6.5).

A sentence ends at `.`, `!`, `?` or `…` (and closing quotes or brackets) followed by a
capital letter or digit, optionally after an opening quote or a dash. So a dialogue
line keeps its author's remark ("— Эх! — отвечал он") and a quote keeps its lowercase
continuation ("Савельич! полно"). It does not end after initials ("А. С. Пушкин") or
after an abbreviation from the language pack ("т. е.", "г."). Splitting too little is
harmless — sentences are only packed into chunks — splitting a sentence is not.
"""

from __future__ import annotations

import re

_END = re.compile(r"[.!?…]+[»\"”’')\]]*(?=\s+[«\"“„(\[]?(?:[—–-]\s*)?[A-ZА-ЯЁ0-9])")
_INITIAL = re.compile(r"(?:^|\s)[A-ZА-ЯЁ]\.$")
_CLAUSE = re.compile(r"(?<=[,;:])\s+|\s+(?=[—–]\s)")


def split_sentences(text: str, abbreviations: tuple[str, ...] = ()) -> list[str]:
    abbrevs = tuple(" " + a.lower() for a in abbreviations)
    out: list[str] = []
    start = 0
    for m in _END.finditer(text):
        head = text[start : m.end()]
        before = " " + head.lower()
        if _INITIAL.search(head) or before.endswith(abbrevs):
            continue
        out.append(head.strip())
        start = m.end()
    tail = text[start:].strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


def _hard_split(text: str, max_chars: int) -> list[str]:
    """A sentence longer than `max_chars`: split at clauses, then at spaces."""
    pieces = _CLAUSE.split(text)
    out: list[str] = []
    for piece in pieces:
        while len(piece) > max_chars:
            cut = piece.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            out.append(piece[:cut].strip())
            piece = piece[cut:].strip()
        out.append(piece)
    return pack([p for p in out if p], max_chars, split_long=False)


def pack(sentences: list[str], max_chars: int, *, split_long: bool = True) -> list[str]:
    """Greedily join sentences into chunks of at most `max_chars` characters."""
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        parts = (
            _hard_split(sentence, max_chars)
            if split_long and len(sentence) > max_chars
            else [sentence]
        )
        for part in parts:
            if current and len(current) + 1 + len(part) > max_chars:
                chunks.append(current)
                current = part
            else:
                current = f"{current} {part}" if current else part
    if current:
        chunks.append(current)
    return chunks
