"""Effective configuration of a book and the source of every value (FR-22, §6.6.5).

Layers, later wins: `core` defaults → `profile:<lang>` (the language pack) → `metadata`
(the book's language) → `user` config → `book` `_work/text2audio.toml` → `cli`.
`config show` prints the view; a new book's `_work/text2audio.toml` starts as the same
view commented out, so users see what to change. Values the resource planner picks at
run time (the tier) are shown as `auto`.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from text2audio.core.config import Config, user_config_path
from text2audio.core.contracts import LanguageProfile
from text2audio.core.languages import profile as language_profile
from text2audio.core.workspace import BookFolder
from text2audio.text.segment import PAUSE_MS

# Why a value is left open (None) until run time.
_AUTO = {
    "synthesis.language": "book metadata",
    "synthesis.tier": "resource plan at run time",
    "qa.wer_threshold": "language profile",
    "qa.cer_threshold": "language profile",
    "resources.devices": "resource plan",
    "resources.max_workers": "resource plan",
    "resources.threads_per_worker": "resource plan",
    "tools.ffmpeg": "found on PATH",
    "cover.sd.dir": "cover generation off until set",
    "cover.sd.python": "<dir>/venv/bin/python",
    "cover.sd.checkpoint": "the WebUI's default",
    "cover.sd.size": "by checkpoint family",
}


@dataclass(frozen=True)
class Setting:
    key: str  # dotted: "qa.wer_threshold", "lang.ru.backend.fast"
    value: Any  # None = decided automatically at run time
    source: str


@dataclass
class View:
    lang: str | None
    settings: list[Setting]
    profile: LanguageProfile | None

    def get(self, key: str) -> Setting:
        return next(s for s in self.settings if s.key == key)


def _flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping) and value:
            out |= _flatten(value, name + ".")
        elif not isinstance(value, Mapping):
            out[name] = value
    return out


def _read(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def _profile_layer(prof: LanguageProfile) -> dict[str, Any]:
    c = prof.code
    out: dict[str, Any] = {}
    for tier, route in prof.tiers.items():
        for key in ("backend", "polish", "foreign"):
            value = getattr(route, key)
            if value:
                out[f"lang.{c}.{key}.{tier}"] = value
    if prof.g2p:
        out[f"lang.{c}.g2p"] = prof.g2p
    if prof.qa.asr:
        out[f"lang.{c}.asr"] = prof.qa.asr
    out["qa.wer_threshold"] = prof.qa.wer_threshold
    out["qa.cer_threshold"] = prof.qa.cer_threshold
    return out


def effective_config(
    folder: Path,
    overrides: Mapping[str, Any] | None = None,
    user_file: Path | None = None,
) -> View:
    """The settings a run of the book in `folder` would use, each with its source."""
    values: dict[str, tuple[Any, str]] = {
        k: (v, "core") for k, v in _flatten(Config().model_dump(mode="json")).items()
    }
    files = [
        ("user", _read(user_file or user_config_path())),
        ("book", _read(folder / "_work" / "text2audio.toml")),
        ("cli", dict(overrides or {})),
    ]
    layered = [_flatten(data) for _, data in files]
    lang = next(
        (d["synthesis.language"] for d in reversed(layered) if d.get("synthesis.language")), None
    )
    if lang is None:
        found = (BookFolder(folder).metadata() or {}).get("language")
        if isinstance(found, str) and found and found != "und":
            lang = found.split("-")[0].lower()
            values["synthesis.language"] = (lang, "metadata")
    prof = language_profile(lang)
    if prof is not None:
        values |= {k: (v, f"profile:{prof.code}") for k, v in _profile_layer(prof).items()}
    for (source, _), flat in zip(files, layered, strict=True):
        for key, value in flat.items():
            # A per-language string ("backend = …") replaces the profile's per-tier keys.
            values = {k: v for k, v in values.items() if not k.startswith(key + ".")}
            values[key] = (value, source)
    settings = [Setting(k, v, s) for k, (v, s) in values.items()]
    return View(lang, sorted(settings, key=lambda s: _order(s.key)), prof)


_SECTIONS = ["synthesis", "output", "qa", "resources", "tools", "cover", "lang"]


def _order(key: str) -> tuple[int, str, str]:
    """By section, then table: a table's keys before its subtables, so TOML stays valid."""
    top = key.split(".")[0]
    table, _, name = key.rpartition(".")
    return (_SECTIONS.index(top) if top in _SECTIONS else len(_SECTIONS), table, name)


def _toml(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list | tuple):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def render(view: View, *, commented: bool = False) -> str:
    """The view as TOML, `# source` after each value; `commented` comments out every line
    (the starting `_work/text2audio.toml`)."""
    lines: list[str] = []
    section: str | None = None
    for s in view.settings:
        head, _, name = s.key.rpartition(".")
        if head != section:
            if lines:
                lines.append("")
            lines.append(f"[{head}]")
            section = head
        if s.value is None:
            why = _AUTO.get(s.key, "automatic")
            lines.append(f"# {name} = (auto)  # {s.source}: {why}")
        else:
            lines.append(f"{name} = {_toml(s.value)}  # {s.source}")
    if view.profile is not None:
        lines += ["", *_profile_notes(view.profile)]
    elif view.lang:
        lines += ["", f"# No language pack for {view.lang}: install text2audio-lang-{view.lang}."]
    if commented:
        lines = [line if line.startswith("#") or not line else "# " + line for line in lines]
    return "\n".join(lines) + "\n"


def _profile_notes(prof: LanguageProfile) -> list[str]:
    """What the language pack decides that is not a setting (§6.6.5), for reference."""
    out = [f"# Language profile {prof.code} ({prof.name}), from the pack:"]
    for role, voices in prof.voices.items():
        ids = ", ".join(f"{b}: {Path(v).name}" for b, v in voices.items())
        out.append(f"#   voice {role}: {ids}")
    pauses = ", ".join(
        f"{t} {ms}" + (" (pack)" if t in prof.pauses_ms else "")
        for t, ms in (PAUSE_MS | prof.pauses_ms).items()
    )
    out.append(f"#   pauses, ms: {pauses}")
    out.append(f"#   dialogue: {prof.dialogue}; normalizer: {prof.normalizer or 'none'}")
    return out


def starting_book_config(folder: Path) -> str:
    """A new book's `_work/text2audio.toml`: the effective view, commented out."""
    head = [
        "# Settings for this book. Every line is commented out: it shows the value a run",
        "# uses now and where it comes from. Uncomment and edit a line to override it;",
        "# `text2audio config show <book>` prints the current view (SPEC §6.6.5).",
        "",
    ]
    return "\n".join(head) + render(effective_config(folder), commented=True)


@dataclass
class Voices:
    # language → voice role → backend → voice id (a reference clip's file name)
    roles: dict[str, dict[str, dict[str, str]]]
    # backend → voice ids it declares; "(clones a reference clip)" for cloning backends
    backends: dict[str, list[str]]


def voices(lang: str | None = None) -> Voices:
    """Voice roles of the installed language packs and the voices of installed backends.
    A `<!-- t2a: voice=… -->` directive accepts either (§6.4.1)."""
    from text2audio.core.languages import profiles
    from text2audio.core.plugins import discover

    packs = {c: p for c, p in profiles().items() if lang is None or c == lang}
    roles = {
        c: {r: {b: Path(v).name for b, v in m.items()} for r, m in p.voices.items()}
        for c, p in sorted(packs.items())
    }
    backends: dict[str, list[str]] = {}
    for name, backend in sorted(discover(("backends",)).plugins.get("backends", {}).items()):
        caps = backend.capabilities
        if lang is not None and not caps.supports(lang):
            continue
        backends[name] = list(caps.voices) + (["(clones a reference clip)"] if caps.cloning else [])
    return Voices(roles, backends)
