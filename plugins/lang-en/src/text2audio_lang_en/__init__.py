"""English language pack (SPEC §6.6.5), generated from templates/lang.

Components are named by entry point; the pack itself stays free of heavy dependencies.
"""

from __future__ import annotations

from pathlib import Path

from text2audio.core.contracts import LanguageProfile, QaProfile, TierRoute

_UNITS = (
    "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    "fifteen|sixteen|seventeen|eighteen|nineteen"
)
_TENS = "twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety"
_ORDINALS = "first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last"
_NUMBER = (
    rf"(?:[ivxlcdm]+|\d+|(?:{_TENS})(?:-(?:{_UNITS}))?|{_UNITS}|hundred|"
    rf"(?:the\s+)?(?:{_ORDINALS}))"
)
_TITLE_TAIL = r"(?:\s*[.:—–-]?\s+\S.*)?"  # "Chapter 1. A title", "Chapter 1 — A title"

PROFILE = LanguageProfile(
    code="en",
    name="English",
    script="Latn",
    part_headings=(rf"(?:part|book|volume)\s+{_NUMBER}\.?{_TITLE_TAIL}",),
    chapter_headings=(
        rf"(?:chapter|ch\.)\s*{_NUMBER}\.?{_TITLE_TAIL}",
        rf"(?:prologue|epilogue|preface|foreword|introduction|afterword){_TITLE_TAIL}",
    ),
    front_matter=(r"(?:table\s+of\s+)?contents\.?",),
    abbreviations=(
        "mr.",
        "mrs.",
        "ms.",
        "dr.",
        "prof.",
        "rev.",
        "hon.",
        "sr.",
        "jr.",
        "st.",
        "mt.",
        "gen.",
        "col.",
        "capt.",
        "lt.",
        "sgt.",
        "gov.",
        "sen.",
        "etc.",
        "e.g.",
        "i.e.",
        "vs.",
        "no.",
        "p.",
        "pp.",
        "vol.",
        "fig.",
        "ed.",
    ),
    normalizer="en",
    g2p=None,  # misaki runs inside the Kokoro worker; Chatterbox reads plain text (§13.1)
    tiers={},
    voices={},
    pauses_ms={},
    dialogue="quote",
    qa=QaProfile(asr=None, wer_threshold=0.12, chars_per_sec=(10.0, 22.0)),
    translator=None,  # English needs no translation for cover prompts
    lexicon=str(Path(__file__).parent / "lexicon.tsv"),
    part_title="{title} (part {n} of {total})",
)

__all__ = ["PROFILE", "TierRoute"]
