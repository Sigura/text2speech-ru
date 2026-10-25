"""Word error rate between the text a segment should say and what ASR heard (§6.9).

Both sides are folded the same way first: case, punctuation, stress marks and the
language profile's letter folds (`ё` → `е` for Russian, because ASR writes `е`);
hyphens separate words ("ноль-ноль" = "ноль ноль").

WER alone over-flags: ASR spells an invented name its own way (Дизиэт → «дизиет»)
or joins words («дизиэцма»), and in a three-word line one such word is WER 0.33. The
character error rate over the folded text with spaces removed stays low for those and
high for real failures (wrong words, missing text), so the gate needs both (§6.9).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

_WORD = re.compile(r"[^\W_]+")


def fold(text: str, letters: Mapping[str, str] | None = None) -> list[str]:
    text = unicodedata.normalize("NFD", text.replace("+", ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = unicodedata.normalize("NFC", text).casefold()
    for src, dst in (letters or {}).items():
        text = text.replace(src, dst)
    return _WORD.findall(text)


def drop_unheard(
    reference: str,
    hypothesis: str,
    optional: Iterable[str],
    letters: Mapping[str, str] | None = None,
) -> tuple[str, list[str]]:
    """The reference without the `optional` words that ASR did not write at all.

    For words the ASR cannot spell (FR-24 acronym and English readings in Russian):
    when the hypothesis lacks the exact word it is unverified rather than an error, and
    whatever ASR wrote instead still counts as insertions. Returns the folded reference
    and the dropped words.
    """
    ref, heard = fold(reference, letters), Counter(fold(hypothesis, letters))
    allowed = Counter(w for text in optional for w in fold(text, letters))
    kept: list[str] = []
    dropped: list[str] = []
    for w in ref:
        if heard[w]:
            heard[w] -= 1
            kept.append(w)
        elif allowed[w]:
            allowed[w] -= 1
            dropped.append(w)
        else:
            kept.append(w)
    return " ".join(kept), dropped


def _distance(a: list[str] | str, b: list[str] | str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def cer(reference: str, hypothesis: str, letters: Mapping[str, str] | None = None) -> float:
    """Character edit distance ÷ reference length, both folded, spaces ignored."""
    ref = "".join(fold(reference, letters))
    hyp = "".join(fold(hypothesis, letters))
    if not ref:
        return 0.0 if not hyp else 1.0
    return min(1.0, _distance(ref, hyp) / len(ref))


@dataclass(frozen=True)
class WerResult:
    wer: float
    errors: int
    words: int
    substitutions: int
    deletions: int
    insertions: int


def wer(reference: str, hypothesis: str, letters: Mapping[str, str] | None = None) -> WerResult:
    """Word-level Levenshtein distance ÷ reference length (1.0 when ASR heard nothing)."""
    ref, hyp = fold(reference, letters), fold(hypothesis, letters)
    if not ref:
        return WerResult(0.0 if not hyp else 1.0, len(hyp), 0, 0, 0, len(hyp))
    # rows: (cost, substitutions, deletions, insertions)
    prev = [(j, 0, 0, j) for j in range(len(hyp) + 1)]
    for i, r in enumerate(ref, 1):
        cur = [(i, 0, i, 0)]
        for j, h in enumerate(hyp, 1):
            diag = prev[j - 1]
            if r == h:
                best = diag
            else:
                options = [
                    (diag[0] + 1, diag[1] + 1, diag[2], diag[3]),
                    (prev[j][0] + 1, prev[j][1], prev[j][2] + 1, prev[j][3]),
                    (cur[j - 1][0] + 1, cur[j - 1][1], cur[j - 1][2], cur[j - 1][3] + 1),
                ]
                best = min(options)
            cur.append(best)
        prev = cur
    cost, sub, dele, ins = prev[-1]
    return WerResult(cost / len(ref), cost, len(ref), sub, dele, ins)
