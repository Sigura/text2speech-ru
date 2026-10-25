"""Installed language packs and the language guess for untagged text (§6.6.1, §6.6.5).

The core knows no language by name: profiles come from the `text2audio.languages`
entry points. Untagged text (TXT, PDF, MD) is matched to an installed pack by the
script its letters are written in.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from functools import cache

from text2audio.core.contracts import LanguageProfile
from text2audio.core.plugins import discover

# Unicode name prefix → ISO 15924 script code, for the scripts we can tell apart cheaply.
_SCRIPTS = {
    "CYRILLIC": "Cyrl",
    "LATIN": "Latn",
    "GREEK": "Grek",
    "ARABIC": "Arab",
    "HEBREW": "Hebr",
    "CJK": "Hani",
}


@cache
def profiles() -> dict[str, LanguageProfile]:
    found = discover(("languages",)).plugins.get("languages", {})
    return {code: p for code, p in found.items() if isinstance(p, LanguageProfile)}


def profile(code: str | None) -> LanguageProfile | None:
    return profiles().get(code) if code else None


def script_of(ch: str) -> str | None:
    """ISO 15924 code of a letter's script, if it is one we tell apart."""
    name = unicodedata.name(ch, "")
    for prefix, script in _SCRIPTS.items():
        if name.startswith(prefix):
            return script
    return None


def dominant_script(text: str, sample: int = 20000) -> str | None:
    counts: Counter[str] = Counter()
    for ch in text[:sample]:
        if ch.isalpha() and (script := script_of(ch)) is not None:
            counts[script] += 1
    return counts.most_common(1)[0][0] if counts else None


def guess_language(text: str) -> str | None:
    """The installed pack whose script dominates the text, if exactly one matches."""
    script = dominant_script(text)
    matches = [p.code for p in profiles().values() if p.script == script]
    return matches[0] if len(matches) == 1 else None
