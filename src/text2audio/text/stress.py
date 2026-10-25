"""Stress marks in synthesis text (SPEC §6.5, §6.6.5).

The pipeline writes stress as `+` before the stressed vowel (`зам+ок`); a source may
use U+0301 after it (`замо́к`). Marks the user wrote — in `<book>.md` or a lexicon —
always win over the stress model.
"""

from __future__ import annotations

import re

ACUTE = "́"
_ACUTE_AFTER = re.compile(rf"(\w){ACUTE}")
_WORD = re.compile(r"[\w+́]+(?:-[\w+́]+)*")


def acute_to_plus(text: str) -> str:
    """`замо́к` → `зам+ок`."""
    return _ACUTE_AFTER.sub(r"+\1", text)


def plus_to_acute(text: str) -> str:
    """`зам+ок` → `замо́к` (for backends that read U+0301)."""
    return re.sub(r"\+(\w)", rf"\1{ACUTE}", text)


def strip_marks(text: str) -> str:
    return text.replace("+", "").replace(ACUTE, "")


def has_marks(word: str) -> bool:
    return "+" in word or ACUTE in word


def merge_marks(marked: str, stressed: str) -> str:
    """Put the words the user stressed in `marked` back into the model's `stressed`.

    Both texts must have the same words in the same order, which holds because the
    stress model only adds marks and ё; if they disagree, the model's text is kept.
    """
    if "+" not in marked:
        return stressed
    src = _WORD.findall(marked)
    out = list(_WORD.finditer(stressed))
    if len(src) != len(out):
        return stressed
    pieces, last = [], 0
    for word, m in zip(src, out, strict=True):
        if has_marks(word) and strip_marks(word).lower().replace("ё", "е") == strip_marks(
            m[0]
        ).lower().replace("ё", "е"):
            pieces += [stressed[last : m.start()], word]
            last = m.end()
    pieces.append(stressed[last:])
    return "".join(pieces)
