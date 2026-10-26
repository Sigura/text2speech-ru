"""Typographic covers (§6.8.2): title and author drawn with the bundled DejaVu Serif.

Deterministic: the background colour comes from a hash of title and author, so a
re-conversion gives the same image and the MP3 tags do not change. `overlay` draws
the same title block on a generated picture.
"""

from __future__ import annotations

import hashlib
import io
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS = Path(__file__).parent / "fonts"
SIZE = 1400  # long side of every cover (§6.8.2)
PALETTE = [(31, 42, 68), (64, 30, 38), (26, 56, 50), (60, 48, 28), (44, 34, 66), (30, 30, 34)]
ACCENT = (222, 196, 132)
INK = (246, 242, 232)

Font = ImageFont.FreeTypeFont


@cache
def font(bold: bool, size: int) -> Font:
    name = "DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf"
    return ImageFont.truetype(str(FONTS / name), size)


def _wrap(text: str, f: Font, width: float) -> list[str]:
    lines: list[str] = []
    line = ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and f.getlength(trial) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    return lines


def _fit(text: str, bold: bool, width: float, start: int, max_lines: int) -> tuple[Font, list[str]]:
    """The largest font from `start` down whose wrapped lines fit `width` and `max_lines`."""
    size = start
    while True:
        f = font(bold, size)
        lines = _wrap(text, f, width)
        if size <= 12 or (len(lines) <= max_lines and all(f.getlength(x) <= width for x in lines)):
            return f, lines[:max_lines]
        size = int(size * 0.92)


def _block(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    f: Font,
    top: float,
    centre: float,
    fill: tuple[int, int, int],
    stroke: tuple[int, int, int] | None = None,
) -> float:
    """Centred lines from `top`, with an outline in `stroke` if given; returns the bottom."""
    step = f.size * 1.18
    width = max(1, f.size // 22) if stroke else 0
    for i, line in enumerate(lines):
        draw.text(
            (centre, top + i * step),
            line,
            font=f,
            fill=fill,
            anchor="ma",
            stroke_width=width,
            stroke_fill=stroke,
        )
    return top + len(lines) * step


def _luminance(rgb: tuple[float, ...]) -> float:
    """WCAG relative luminance."""
    lin = [c / 255 / 12.92 if c <= 10 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in rgb[:3]]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def ink_for(region: Image.Image) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """(text colour, outline colour) with the most contrast against `region`.

    The candidates are compared with the region's mean and with its darkest and
    lightest tenth, so text over a busy picture stays readable; the outline is the
    opposite extreme.
    """
    grey = region.convert("L")
    hist = grey.histogram()
    total = sum(hist)
    stat = region.resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
    mean = tuple(float(c) for c in stat) if isinstance(stat, tuple) else (float(stat or 0),) * 3

    def level(share: float) -> float:
        seen = 0
        for value, n in enumerate(hist):
            seen += n
            if seen >= share * total:
                return float(value)
        return 255.0

    dark, light = (level(0.1),) * 3, (level(0.9),) * 3
    candidates = [INK, (16, 16, 20), ACCENT, (255, 255, 255)]
    best = max(
        candidates, key=lambda c: min(contrast(c, mean), contrast(c, dark), contrast(c, light))
    )
    outline = (0, 0, 0) if _luminance(best) > 0.4 else (255, 255, 255)
    return best, outline


def _colour(title: str, author: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(f"{title}\n{author}".encode()).digest()
    return PALETTE[digest[0] % len(PALETTE)]


def render(title: str, author: str, size: int = SIZE) -> Image.Image:
    """A plain cover: frame, author at the top, the title in the middle."""
    base = _colour(title, author)
    img = Image.new("RGB", (size, size), base)
    draw = ImageDraw.Draw(img)
    for y in range(size):  # darker towards the bottom
        k = 1 - 0.45 * y / size
        draw.line([(0, y), (size, y)], fill=tuple(int(c * k) for c in base))
    margin, stroke = size // 18, max(2, size // 280)
    draw.rectangle([margin, margin, size - margin, size - margin], outline=ACCENT, width=stroke)
    inner = size - 2 * margin - size // 10
    centre = size / 2
    if author:
        f, lines = _fit(author, False, inner, size // 22, 2)
        _block(draw, lines, f, size * 0.16, centre, ACCENT)
    rule = size * 0.3
    draw.line(
        [(centre - size * 0.12, rule), (centre + size * 0.12, rule)], fill=ACCENT, width=stroke
    )
    f, lines = _fit(title, True, inner, size // 11, 6)
    height = len(lines) * f.size * 1.18
    _block(draw, lines, f, max(rule + size * 0.06, size * 0.58 - height / 2), centre, INK)
    return img


def overlay(png: bytes, title: str, author: str, size: int = SIZE) -> Image.Image:
    """Title and author at the bottom of a generated picture (cropped to a square), in the
    colour with the most contrast against what lies behind them."""
    with Image.open(io.BytesIO(png)) as src:
        pic = src.convert("RGB")
    side = min(pic.size)
    left, top = (pic.width - side) // 2, (pic.height - side) // 2
    pic = pic.crop((left, top, left + side, top + side)).resize(
        (size, size), Image.Resampling.LANCZOS
    )
    inner = size * 0.86
    tf, tlines = _fit(title, True, inner, size // 13, 3)
    af, alines = _fit(author, False, inner, size // 26, 1) if author else (tf, [])
    height = len(tlines) * tf.size * 1.18 + len(alines) * af.size * 1.18 + size * 0.07
    start = size - height
    fill, outline = ink_for(pic.crop((int(size * 0.07), int(start), int(size * 0.93), size)))
    draw = ImageDraw.Draw(pic)
    y = _block(draw, tlines, tf, start, size / 2, fill, outline)
    _block(draw, alines, af, y + size * 0.015, size / 2, fill, outline)
    return pic


def save(img: Image.Image, path: Path) -> None:
    img.save(path, "JPEG", quality=90)
