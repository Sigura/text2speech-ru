"""Pronunciation lexicon at three levels (SPEC §6.5, FR-13), later levels win.

The language pack's list → user-global `~/.config/text2audio/lexicon/<lang>.tsv` →
per-book `_work/lexicon.tsv`. Each line is `word<TAB>replacement`; `#` starts a comment.
Entries match whole words, ignoring case, and are applied **before** normalization so
they can name source spellings (*США*, *Docker*); stress in a replacement is written
`+` before the vowel or with U+0301 and is kept by the stress stage. A native ending
after an apostrophe joins the replacement (*iPhone'а* → «айфо́на», FR-24).
"""

from __future__ import annotations

import re
from pathlib import Path

import platformdirs

from text2audio.core.languages import script_of


def user_lexicon(lang: str) -> Path:
    return Path(platformdirs.user_config_dir("text2audio")) / "lexicon" / f"{lang}.tsv"


def parse_tsv(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in text.splitlines():
        if line.lstrip().startswith("#") or "\t" not in line:
            continue
        word, replacement = (x.strip() for x in line.split("\t", 1))
        if word and replacement:
            entries[word.lower()] = replacement
    return entries


class Lexicon:
    def __init__(self, entries: dict[str, str] | None = None) -> None:
        self.entries = dict(entries or {})
        self._pattern: re.Pattern[str] | None = None

    @classmethod
    def layered(cls, *sources: Path | str | None) -> Lexicon:
        """Merge TSV files or TSV text in order; missing files are skipped."""
        entries: dict[str, str] = {}
        for src in sources:
            if src is None:
                continue
            if isinstance(src, Path):
                if not src.is_file():
                    continue
                src = src.read_text(encoding="utf-8")
            entries.update(parse_tsv(src))
        return cls(entries)

    def apply(self, text: str) -> str:
        if not self.entries:
            return text
        if self._pattern is None:
            words = sorted(self.entries, key=len, reverse=True)
            alts = "|".join(re.escape(w) for w in words)
            self._pattern = re.compile(
                rf"(?<![\w+])({alts})(?![\w+])(?:(['’])(?=([^\W\d_])))?", re.IGNORECASE
            )

        def repl(m: re.Match[str]) -> str:
            out = self.entries[m[1].lower()]
            if m[2] and script_of(m[1][-1]) == script_of(m[3]):
                out += m[2]  # "Docker's": the apostrophe belongs to the word
            return out

        return self._pattern.sub(repl, text)
