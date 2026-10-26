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
"""

from __future__ import annotations

import re

_DASH = re.compile(r"\s+(?=[—–]\s)")
_OPENS = re.compile(r"^[—–]\s")
_PUNCT_END = re.compile(r"[,.!?…:;][»\"”’)]*$")
_QUOTES = ("«", '"', "„", "“")


def split_dialogue(text: str, remark_words: tuple[str, ...] = ()) -> list[tuple[str, str]]:
    """`(role, text)` parts of a paragraph: `dialogue` and `remark`, or one `narration`."""
    if not _OPENS.match(text):
        return [("narration", text)]
    pieces = _DASH.split(text.strip())
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
