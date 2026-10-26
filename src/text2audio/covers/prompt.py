"""Prompts for generated covers (§6.8.2): genre keywords, era, translated title and blurb.

Stable Diffusion understands English only. FB2 genre codes and EPUB subjects map to
English style keywords here; the title and the start of the annotation come in
already translated (the language's `translator` component), or are left out.
"""

from __future__ import annotations

import hashlib
import re

# FB2 genre codes (exact) → English style keywords.
GENRES = {
    "sf_space": "space opera, starships, distant planets, nebula",
    "sf_cyberpunk": "cyberpunk city, neon lights, rain",
    "sf_epic": "epic science fiction, vast alien landscape",
    "sf_heroic": "heroic fantasy, warrior, ancient ruins",
    "sf_fantasy": "epic fantasy, magic, ancient castle",
    "sf_horror": "dark horror, eerie fog, ominous shadows",
    "sf_history": "alternate history, period costumes",
    "sf_postapocalyptic": "post-apocalyptic wasteland, ruins",
    "sf_humor": "whimsical science fiction, playful",
    "sf_detective": "science fiction noir, futuristic city at night",
    "det_history": "historical mystery, candlelight, old streets",
    "det_espionage": "spy thriller, cold war, shadows",
    "love_history": "historical romance, period costumes, soft light",
    "prose_rus_classic": "russian classic literature, nineteenth century russia, oil painting",
    "prose_su_classics": "soviet era, realist painting",
    "child_tale": "fairy tale, storybook illustration",
    "poetry": "poetic, dreamy, watercolor",
    "dramaturgy": "theatre stage, curtains, dramatic",
}
# Genre code prefixes and EPUB subject words → keywords (first match wins).
FAMILIES = [
    ("sf", "science fiction, futuristic"),
    ("science fiction", "science fiction, futuristic"),
    ("fantasy", "epic fantasy, magic"),
    ("det", "mystery, noir, dim streetlights"),
    ("mystery", "mystery, noir, dim streetlights"),
    ("crime", "crime, noir, dim streetlights"),
    ("thriller", "thriller, tense, dramatic shadows"),
    ("horror", "dark horror, eerie fog"),
    ("love", "romantic, soft warm light"),
    ("romance", "romantic, soft warm light"),
    ("adv", "adventure, journey, sweeping landscape"),
    ("adventure", "adventure, journey, sweeping landscape"),
    ("child", "children's book illustration, colorful"),
    ("juvenile", "children's book illustration, colorful"),
    ("antique", "ancient world, classical painting"),
    ("prose_history", "historical, period costumes, oil painting"),
    ("histor", "historical, period costumes, oil painting"),
    ("prose", "literary fiction, painterly"),
    ("fiction", "literary fiction, painterly"),
    ("humor", "humorous, lighthearted"),
    ("poetry", "poetic, dreamy, watercolor"),
]
STYLE = "book cover illustration, highly detailed, atmospheric lighting"
NEGATIVE = (
    "text, letters, words, title, caption, typography, watermark, signature, logo, "
    "frame, border, lowres, blurry, deformed, disfigured, nsfw, nude"
)
# WD14 tags never passed on: it guesses explicit tags even for blank pictures.
_EXPLICIT = re.compile(
    r"\b(sex|nude|naked|nsfw|nipples?|breasts?|cleavage|fellatio|penis|pussy|vagina|cum|"
    r"erection|panties|underwear|lingerie|bdsm|bondage|rape|groping|ass|hetero|yuri|yaoi)\b"
)
_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_MARKUP = re.compile(r"[*_`\\]|\[\^?[^\]]*\]")


def keywords(genres: list[str]) -> list[str]:
    out: list[str] = []
    for genre in genres:
        g = genre.strip().lower()
        kw = GENRES.get(g) or next((v for k, v in FAMILIES if g.startswith(k) or k in g), None)
        if kw and kw not in out:
            out.append(kw)
    return out[:2]


def era(date: str | None) -> str | None:
    """'1836' → '19th century'; only for old books, where it says something."""
    m = _YEAR.search(date or "")
    if not m or int(m[1]) >= 1950:
        return None
    century = int(m[1]) // 100 + 1
    return f"{century}th century"


def blurb(text: str | None, limit: int = 240) -> str:
    """The first sentences of an annotation, at most `limit` characters."""
    out = ""
    for sentence in _SENTENCE.split((text or "").strip()):
        if out and len(out) + len(sentence) + 1 > limit:
            break
        out = f"{out} {sentence}".strip()
    return out[:limit]


def opening(markdown: str, words: int = 20) -> str:
    """The first `words` words of the text, past headings and comments."""
    out: list[str] = []
    for line in markdown.splitlines():
        line = line.strip()
        if (
            not line
            or line.startswith(("#", "<!--", ">", "|"))
            or not any(c.isalpha() for c in line)
        ):
            continue
        out += _MARKUP.sub("", line).split()
        if len(out) >= words:
            break
    return " ".join(out[:words])


def build(
    genres: list[str], date: str | None, title_en: str | None, blurb_en: str | None
) -> tuple[str, str]:
    """(prompt, negative prompt)."""
    parts = [STYLE, *keywords(genres)]
    if title_en:
        parts.append(f'"{title_en.rstrip(". ")}"')
    if blurb_en:
        parts.append(blurb_en)
    if (when := era(date)) is not None:
        parts.append(when)
    return ", ".join(parts), NEGATIVE


def repaint(tags: list[str], limit: int = 30) -> tuple[str, str]:
    """(prompt, negative prompt) for repainting a picture from its WD14 tags."""
    kept = [t for t in tags if not _EXPLICIT.search(t.lower())]
    return ", ".join([STYLE, *kept[:limit]]), NEGATIVE


def seed(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) & 0x7FFFFFFF
