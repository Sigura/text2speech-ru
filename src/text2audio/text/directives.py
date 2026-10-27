"""`<!-- t2a: key=value … -->` directives in `book.md` (SPEC §6.4.1, §6.7 E3).

A directive on a line of its own (a Markdown HTML block) applies to everything after
it until `reset`; one inside a paragraph applies to the sentence that contains it (a
comment between two sentences belongs to the one before it). Keys:

- `voice=<name>` — a voice role of the language profile (`narrator`, `dialogue`) or a
  voice id of the backend (Silero `baya`);
- `pause=<ms>` — the pause after the preceding text (block) or after the sentence
  (inline); it replaces the structural pause there;
- `rate=<factor>` — speaking rate, 0.5–2.0, applied by the core at assembly so that
  every backend honours it;
- `emotion=<label>`, `intensity=<0..1>` — for backends with emotion control;
- `skip` — the text is not read; `reset` — back to the defaults (block scope).

Malformed values and unknown keys become warnings; the rest of the directive applies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

COMMENT = re.compile(r"<!--\s*t2a:(.*?)-->", re.DOTALL)
_ITEM = re.compile(r"([^\s=]+)(?:=(\"[^\"]*\"|\S+))?")
RATE_RANGE = (0.5, 2.0)
_FLAGS = ("skip", "reset")
_VALUES = ("voice", "pause", "rate", "emotion", "intensity")


@dataclass
class Directive:
    """Parsed settings of one comment; `None` = not given."""

    voice: str | None = None
    pause_ms: int | None = None
    rate: float | None = None
    emotion: str | None = None
    intensity: float | None = None
    skip: bool = False
    reset: bool = False
    warnings: list[str] = field(default_factory=list)


def _number(key: str, value: str, low: float, high: float, d: Directive) -> float | None:
    try:
        number = float(value)
    except ValueError:
        number = float("nan")
    if not low <= number <= high:  # also rejects NaN
        d.warnings.append(f"t2a: {key}={value!r} ignored: expected a number in {low:g}–{high:g}")
        return None
    return number


def parse(body: str) -> Directive:
    """The text between `t2a:` and `-->`."""
    d = Directive()
    for m in _ITEM.finditer(body):
        key, value = m.group(1).lower(), m.group(2)
        if value is not None:
            value = value.strip('"')
        if key in _FLAGS:
            if value is not None:
                d.warnings.append(f"t2a: {key} takes no value; {key}={value!r} ignored")
            else:
                setattr(d, key, True)
        elif key not in _VALUES:
            d.warnings.append(f"t2a: unknown key {key!r} ignored")
        elif not value:
            d.warnings.append(f"t2a: {key} needs a value")
        elif key == "voice":
            d.voice = value
        elif key == "emotion":
            d.emotion = value.lower()
        elif key == "pause":
            ms = _number(key, value, 0, 60_000, d)
            d.pause_ms = None if ms is None else int(ms)
        elif key == "rate":
            d.rate = _number(key, value, *RATE_RANGE, d)
        else:
            d.intensity = _number(key, value, 0, 1, d)
    return d


def find(text: str) -> list[Directive]:
    """Every t2a directive in a piece of HTML (a block may hold several comments)."""
    return [parse(m.group(1)) for m in COMMENT.finditer(text)]


@dataclass
class Settings:
    """What a segment is read with; block directives update it until `reset`."""

    voice: str | None = None
    rate: float | None = None
    emotion: str | None = None
    intensity: float | None = None
    skip: bool = False

    def apply(self, d: Directive) -> Settings:
        base = Settings() if d.reset else self
        return Settings(
            voice=d.voice or base.voice,
            rate=d.rate or base.rate,
            emotion=d.emotion or base.emotion,
            intensity=d.intensity if d.intensity is not None else base.intensity,
            skip=d.skip or base.skip,
        )
