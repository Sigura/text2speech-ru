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
_VOICES = Path(__file__).parent / "voices"

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
    default_tier="fast",  # owner's blind test, §13.1
    tiers={
        "fast": TierRoute(backend="kokoro"),
        # Chatterbox garbles headings and short pieces; Kokoro reads its reference voices.
        "expressive": TierRoute(backend="chatterbox", short="kokoro"),
    },
    # Chosen by the owner by ear (M4 step 8, `spikes/en-voices/samples.py`). Chatterbox
    # clones the Kokoro voice from a clip it read (`spikes/en-voices/make_refs.py`), so
    # both tiers keep the same narrator.
    voices={
        "narrator": {"kokoro": "af_nicole", "chatterbox": str(_VOICES / "ref-af_nicole.wav")},
        "dialogue": {"kokoro": "am_michael", "chatterbox": str(_VOICES / "ref-am_michael.wav")},
    },
    pauses_ms={},
    dialogue="quote",
    # af_nicole reads slowly: 8.3–13.6 letters/s on the benchmark sentences (§13.1).
    qa=QaProfile(
        asr="whisper", wer_threshold=0.12, chars_per_sec=(7.0, 22.0), normalize_heard=True
    ),
    # Tower-Plus by paragraph (owner's reading), Seed-X when it has no room, NLLB by
    # sentence for what fails the checks (§13.1, ADR-0011).
    translators={"ru": ("towerplus-9b", "seedx-7b", "nllb-3.3b")},
    lexicon=str(Path(__file__).parent / "lexicon.tsv"),
    part_title="{title} (part {n} of {total})",
)

__all__ = ["PROFILE", "TierRoute"]
