"""Plugin contracts shared by the core and plugins (SPEC §6.6.2, §6.4.2).

A backend plugin installed in the core environment is a light *descriptor*: it
declares capabilities and assets and names its worker module. The model itself runs
in the backend's own environment as a worker process (ADR-0004); the core wraps the
descriptor in a `WorkerBackend` that implements `Backend`.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EmotionMode = Literal["none", "scalar", "tags", "prompt", "vector"]
Tier = Literal["fast", "expressive"]

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")


class Capabilities(BaseModel):
    """What a backend can do and what it needs to run."""

    model_config = ConfigDict(frozen=True)

    languages: tuple[str, ...]  # ISO 639-1 codes; "*" means any language
    voices: tuple[str, ...] = ()
    emotion: EmotionMode = "none"
    cloning: bool = False
    max_chars: int = 300
    sample_rate: int = 24000
    devices: tuple[Literal["cpu", "cuda", "mps"], ...] = ("cpu",)
    # Resource needs used by the resource planner (§9.1).
    vram_mb: int = 0  # per worker on GPU; 0 = does not use a GPU
    ram_mb: int = 1024  # per worker on CPU
    threads: int = 4  # preferred CPU threads per worker

    def supports(self, lang: str) -> bool:
        return "*" in self.languages or lang in self.languages


class ModelAsset(BaseModel):
    """One pinned asset entry of `models.lock` (§6.10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Source: a Hugging Face repo pinned to a commit, or a plain URL for a single file
    # (e.g. Silero weights). Either way the SHA-256 pins the content.
    repo: str | None = None
    revision: str | None = None
    url: str | None = None
    files: tuple[str, ...]
    sha256: dict[str, str]
    license: str = "unknown"  # informational only (ADR-0007)
    backend: str | None = None
    component: str | None = None  # e.g. "g2p:ruaccent", "asr:gigaam"
    langs: tuple[str, ...] = ()  # empty = needed for every language
    tiers: tuple[Tier, ...] = ()  # empty = needed for every tier

    @field_validator("revision")
    @classmethod
    def _pinned_revision(cls, v: str | None) -> str | None:
        if v is not None and not _REVISION.match(v):
            raise ValueError("revision must be a 40-character commit sha")
        return v

    @model_validator(mode="after")
    def _source_and_hashes(self) -> ModelAsset:
        if self.url is None and (self.repo is None or self.revision is None):
            raise ValueError("asset needs repo + revision, or url")
        if self.url is not None and (self.repo is not None or len(self.files) != 1):
            raise ValueError(f"{self.url}: a url asset has no repo and exactly one file")
        if set(self.sha256) != set(self.files):
            raise ValueError(f"{self.source}: sha256 keys must match files exactly")
        for name, digest in self.sha256.items():
            if not _SHA256.match(digest):
                raise ValueError(f"{self.source}: invalid sha256 for {name}")
        for name in self.files:
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"{self.source}: unsafe file path {name!r}")
        return self

    @property
    def source(self) -> str:
        return self.url or f"{self.repo}@{(self.revision or '')[:12]}"

    @property
    def owner(self) -> str:
        return self.backend or self.component or self.source

    def wanted(self, lang: str | None, tier: Tier | None) -> bool:
        lang_ok = lang is None or not self.langs or lang in self.langs
        tier_ok = tier is None or not self.tiers or tier in self.tiers
        return lang_ok and tier_ok


class Segment(BaseModel):
    """One synthesis unit; a line of `segments.jsonl` (§6.4.2)."""

    id: str
    chapter: int = 0
    text: str
    text_norm: str | None = None
    lang: str
    voice: str = "narrator"
    role: str = "narration"
    emotion: str | None = None
    intensity: float = 0.0
    pause_after_ms: int = 0
    backend: str | None = None
    seed: int = 1234
    hash: str | None = None
    status: str = "pending"
    wer: float | None = None
    attempts: int = 0

    @property
    def speech_text(self) -> str:
        return self.text_norm if self.text_norm is not None else self.text


class LanguageProfile(BaseModel):
    """What a language pack tells the core about its language (§6.6.5).

    Only text structure for now (M1); stress, normalization, voices and QA bounds are
    added with the first pack that needs them (M2).
    """

    model_config = ConfigDict(frozen=True)

    code: str  # ISO 639-1
    name: str
    script: str  # ISO 15924, e.g. "Cyrl", "Latn"
    # Regexes matched (case-insensitive) against a whole stripped line of plain text.
    part_headings: tuple[str, ...] = ()  # "Часть первая", "Книга 2"
    chapter_headings: tuple[str, ...] = ()  # "Глава I", "Пролог"
    front_matter: tuple[str, ...] = ()  # "Содержание" — skipped in TXT/PDF
    # Abbreviations that end with a full stop but not a sentence ("т. е.", "г."),
    # lowercase; initials (one capital letter and a stop) are handled by the core.
    abbreviations: tuple[str, ...] = ()


class SynthesisResult(BaseModel):
    duration_ms: int
    elapsed_ms: int
    sample_rate: int = Field(gt=0)


@runtime_checkable
class Backend(Protocol):
    """What the core calls to synthesize (§6.6.2)."""

    name: str
    capabilities: Capabilities

    def assets(self) -> list[ModelAsset]: ...
    def load(self, device: str) -> None: ...
    def synthesize(self, seg: Segment, out_path: Path) -> SynthesisResult: ...
    def close(self) -> None: ...


@runtime_checkable
class BackendDescriptor(Protocol):
    """What a backend plugin's entry point resolves to."""

    name: str
    capabilities: Capabilities

    def assets(self) -> list[ModelAsset]: ...

    def worker_command(self, python: str) -> list[str]:
        """Command that starts the worker with the given interpreter."""
        ...
