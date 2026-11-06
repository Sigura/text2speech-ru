"""Dialogue lines and the author's remarks between them (SPEC §6.6.5, §6.7 E1).

The dash convention (Russian, and other languages that use it): a paragraph opening
with a dash is dialogue, and a spaced dash inside it switches between the character's
speech and the author's remark, which is read in the narrator voice:

    — Эх, батюшка Петр Андреич! — отвечал он с глубоким вздохом. — Сержусь-то я.
      speech                     remark                            speech

A dash after speech starts a remark when the speech before it ends with punctuation
(`,` `!` `?` `…` `.` `:`), or when the next word starts with one of the profile's
`remark_words` (older texts drop the comma: «— Прикажи читать далее — отвечал
Савельич»); otherwise it is a dash inside the speech. A dash after a remark returns to
speech only when the remark ends with punctuation («— сказал он, — …», «— сказал он.
— …») or a quotation opens after it («— отвечал он — "слава богу…"»), so «сказал он —
и вышел» stays one remark. Every part keeps its dash, so
subtitles show the book's text.

Outside dash paragraphs, the dash convention reads quoted speech, «…» or "…" («"Кто
там?" спросила Палаша.», «Он сказал: «Приду».»). Russian puts the punctuation after
the closing quote and uses the same quotes for titles and terms, so a quote is speech
only when something marks it: it opens the paragraph or follows a colon or a dash, it
is a whole sentence, it ends with punctuation inside, or a dash or one of the
`remark_words` follows it. «Война и мир» and «стариком» are none of these. Speech keeps
the punctuation after its quote, and a remark inside the quotes («Привет, — сказал он,
— как дела?») is split as in a dash paragraph.

The quote convention (English): quoted speech is dialogue and the words around it are
the author's, in straight or curly double quotes.
"""

from __future__ import annotations

import re
from collections.abc import Callable

_DASH = re.compile(r"\s+(?=[—–]\s)")
_OPENS = re.compile(r"^[—–]\s")
_PUNCT_END = re.compile(r"[,.!?…:;][»\"”’)]*$")
_QUOTES = ("«", '"', "„", "“")
_OPENING = re.compile(r'["“]')
_CLOSING = {'"': '"', "“": "”"}
_SPEECH_END = re.compile(r"[,.!?…;:]$")
_DASH_OPENING = re.compile(r'[«"]')
_DASH_CLOSING = {"«": "»", '"': '"'}
_TRAILING = ",.!?…;:"
_BEFORE_SPEECH = re.compile(r"(?:^|[:—–-])\s*$")
_BEFORE_SENTENCE = re.compile(r"[.!?…]\s*$")
_LEADING_DASH = re.compile(r"[—–-]\s*")


def split_dialogue(text: str, remark_words: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """`(role, text)` parts of a paragraph: `dialogue` and `remark`, or one `narration`."""
    if not _OPENS.match(text):
        spans = _dash_quoted_spans(text, remark_words)
        return _parts(text, spans, lambda speech: _alternate(_DASH.split(speech), remark_words))
    return _alternate(_DASH.split(text.strip()), remark_words)


def _alternate(pieces: list[str], remark_words: tuple[str, ...]) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = [("dialogue", pieces[0])]
    for piece in pieces[1:]:
        role, prev = parts[-1]
        word = piece[1:].lstrip().lower()
        ends = bool(_PUNCT_END.search(prev))
        if role == "dialogue":
            switch = ends or word.startswith(remark_words)
        else:
            switch = ends or word.startswith(_QUOTES)
        if switch:
            parts.append(("remark" if role == "dialogue" else "dialogue", piece))
        else:
            parts[-1] = (role, f"{prev} {piece}")
    return parts


def split_quoted(text: str) -> list[tuple[str, str]]:
    """`(role, text)` parts of a paragraph in the quote convention: quoted speech is
    `dialogue`, the words around it `remark`, or one `narration` without speech."""
    return _parts(text, _quoted_spans(text), lambda speech: [("dialogue", speech)])


def _parts(
    text: str,
    spans: list[tuple[int, int]],
    speech: Callable[[str], list[tuple[str, str]]],
) -> list[tuple[str, str]]:
    parts: list[tuple[str, str]] = []
    position = 0
    for start, end in spans:
        before = text[position:start].strip()
        if before:
            parts.append(("remark", before))
        parts.extend(speech(text[start:end].strip()))
        position = end
    after = text[position:].strip()
    if after:
        parts.append(("remark", after))
    if all(role == "remark" for role, _ in parts):
        return [("narration", text)]
    return parts


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    """Spans of speech. An unclosed quote runs to the end: long speech over several
    paragraphs opens each one and closes only the last. Scare quotes are not speech."""
    spans = []
    position = 0
    while (match := _OPENING.search(text, position)) is not None:
        close = text.find(_CLOSING[match[0]], match.end())
        end = close + 1 if close >= 0 else len(text)
        if close < 0 or not _is_scare_quote(text[match.end() : close]):
            spans.append((match.start(), end))
        position = end
    return spans


def _is_scare_quote(inner: str) -> bool:
    return len(inner.split()) <= 2 and not _SPEECH_END.search(inner)


def _dash_quoted_spans(text: str, remark_words: tuple[str, ...]) -> list[tuple[int, int]]:
    """Spans of quoted speech in the dash convention, each with the punctuation after
    its closing quote. An unclosed quote runs to the end, as in `_quoted_spans`."""
    spans = []
    position = 0
    while (match := _DASH_OPENING.search(text, position)) is not None:
        close = text.find(_DASH_CLOSING[match[0]], match.end())
        end = close + 1 if close >= 0 else len(text)
        rest = text[end:]
        tail = end + len(rest) - len(rest.lstrip(_TRAILING))
        before = text[: match.start()]
        after = text[tail:].lstrip()
        dash = _LEADING_DASH.match(after)
        word = (after[dash.end() :] if dash else after).lower()
        if (
            close < 0
            or _BEFORE_SPEECH.search(before)
            or (_BEFORE_SENTENCE.search(before) and any(c in ".!?…" for c in text[end:tail]))
            or _SPEECH_END.search(text[match.end() : close].rstrip())
            or after.startswith(("— ", "– "))
            or word.startswith(remark_words)
        ):
            spans.append((match.start(), tail))
        position = end
    return spans
