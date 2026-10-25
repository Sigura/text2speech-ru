"""Foreign-script spans inside a sentence (SPEC FR-24, §6.6.5).

The core only finds them: a word is foreign when any of its letters is written in
another script than the book language's (so a Cyrillic word with a Latin look-alike
letter is tagged too), and neighbouring foreign words separated by spaces, hyphens or
apostrophes form one span ("Google Docs", "It's not my problem"). The language pack
decides what each span becomes.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from text2audio.core.languages import script_of

_WORD = re.compile(r"[^\W\d_]+")
_JOINER = re.compile(r"[\s'’-]+")
# An apostrophe between a span and a native ending: "iPhone'а" → reading + "а".
_ENDING_APOSTROPHE = re.compile(r"['’](?=[^\W\d_])")


@dataclass(frozen=True)
class Run:
    start: int
    end: int
    text: str
    script: str


def _foreign_script(word: str, script: str) -> str | None:
    others = Counter(s for ch in word if (s := script_of(ch)) is not None and s != script)
    return others.most_common(1)[0][0] if others else None


def foreign_spans(text: str, script: str) -> list[Run]:
    runs: list[Run] = []
    start = end = -1
    found: Counter[str] = Counter()
    for m in _WORD.finditer(text):
        other = _foreign_script(m[0], script)
        if other is None:
            continue
        if start >= 0 and _JOINER.fullmatch(text[end : m.start()]):
            end = m.end()
            found[other] += len(m[0])
            continue
        if start >= 0:
            runs.append(Run(start, end, text[start:end], found.most_common(1)[0][0]))
        start, end, found = m.start(), m.end(), Counter({other: len(m[0])})
    if start >= 0:
        runs.append(Run(start, end, text[start:end], found.most_common(1)[0][0]))
    return runs


def replace_spans(text: str, runs: list[Run], reading: Callable[[Run], str]) -> str:
    """Replace each run by its reading, joining a native ending without the apostrophe."""
    for run in sorted(runs, key=lambda r: -r.start):
        tail = text[run.end :]
        if _ENDING_APOSTROPHE.match(tail):
            tail = tail[1:]
        text = text[: run.start] + reading(run) + tail
    return text
