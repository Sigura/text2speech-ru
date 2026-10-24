"""Plugin discovery through Python entry points (SPEC §6.6.1, FR-07).

The core knows stage interfaces only; which implementation a language uses is data
in its profile. A plugin that fails to load is reported, never fatal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import EntryPoint, entry_points
from typing import Any

GROUPS = (
    "languages",
    "backends",
    "segmenters",
    "normalizers",
    "g2p",
    "asr",
    "emotion",
    "polishers",
    "covers",
)


@dataclass
class Discovery:
    plugins: dict[str, dict[str, Any]] = field(default_factory=dict)  # group → name → obj
    errors: list[str] = field(default_factory=list)

    def get(self, group: str, name: str) -> Any:
        return self.plugins.get(group, {}).get(name)


def _load(ep: EntryPoint) -> Any:
    obj = ep.load()
    return obj() if isinstance(obj, type) else obj


def discover(groups: tuple[str, ...] = GROUPS) -> Discovery:
    found = Discovery()
    for group in groups:
        found.plugins[group] = {}
        for ep in entry_points(group=f"text2audio.{group}"):
            try:
                found.plugins[group][ep.name] = _load(ep)
            except Exception as exc:  # a broken plugin must not break the core
                found.errors.append(f"{group}:{ep.name} ({ep.value}): {exc!r}")
    return found
