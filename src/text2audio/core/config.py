"""Configuration model and layered resolution (SPEC §6.11, §6.6.5).

Order (later wins): built-in defaults → user config → book `_work/text2audio.toml`
→ CLI flags. Language and content profiles slot in after the defaults in M2.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import platformdirs
from pydantic import BaseModel, ConfigDict, Field, field_validator

from text2audio.core.contracts import Tier


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SynthesisConfig(_Section):
    language: str | None = None  # None = from metadata / detection
    tier: Tier | None = None  # None = from the resource plan
    expression: Literal["E0", "E1", "E2", "E3"] = "E1"
    seed: int = 1234


class OutputConfig(_Section):
    root: Path = Path("output")
    formats: list[Literal["mp3", "m4b", "opus", "wav"]] = ["mp3"]
    loudness: Literal["default", "acx"] = "default"
    split: Literal["auto", "section", "none"] = "auto"
    min_part_minutes: float = 3
    max_part_minutes: float = 90
    target_part_minutes: float = 45
    min_parts: int = 2
    max_parts: int = 150
    max_merge_ratio: float = 0.5
    subtitles: bool = True  # an .srt with the book text next to every MP3


class ToolsConfig(_Section):
    ffmpeg: str | None = None  # None = from PATH


_QA_SAMPLE = re.compile(r"^sample=(\d{1,3})%$")


def parse_qa_mode(value: str) -> int:
    """`on` | `off` | `sample=N%` → the share of segments checked, in percent (§6.9)."""
    value = value.strip().lower()
    if value == "on":
        return 100
    if value == "off":
        return 0
    m = _QA_SAMPLE.match(value)
    if m and 1 <= int(m[1]) <= 100:
        return int(m[1])
    raise ValueError(f"QA mode must be on, off or sample=N% (1–100), not {value!r}")


class QaConfig(_Section):
    mode: str = "on"  # on | off | sample=N%
    wer_threshold: float | None = None  # None = from the language profile
    cer_threshold: float | None = None  # None = from the language profile
    max_segments: int = Field(default=100, ge=0)  # checked per run, 0 = no limit
    batch: int = Field(default=100, ge=1)  # polish and QA work in batches of this size

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        parse_qa_mode(v)
        return v.strip().lower()


class CoverSdConfig(_Section):
    """The `sd-webui` cover generator (§6.8.2, ADR-0009); off until `dir` is set."""

    dir: Path | None = None  # an AUTOMATIC1111 WebUI checkout
    python: Path | None = None  # None = <dir>/venv/bin/python
    checkpoint: str | None = None  # None = the WebUI's configured default
    steps: int = Field(default=30, ge=1)
    size: int | None = Field(default=None, ge=256)  # None = by checkpoint family
    timeout_s: float = Field(default=600, gt=0)  # start-up, including the model load


class CoverConfig(_Section):
    # auto: embedded → generated → typographic; generate: generated even when one is
    # embedded; typographic: always drawn; embedded-only: no cover without one.
    mode: Literal["auto", "embedded-only", "generate", "typographic"] = "auto"
    generator: str = "sd-webui"  # entry point in `text2audio.covers`
    # In auto mode an embedded cover whose long side is below this many pixels is
    # repainted (img2img) from WD14 tags; 0 = never.
    repaint_below: int = Field(default=800, ge=0)
    repaint_denoise: float = Field(default=0.4, gt=0, lt=1)
    tagger: str = "wd14"  # entry point in `text2audio.taggers`
    sd: CoverSdConfig = CoverSdConfig()


class ResourcesConfig(_Section):
    """Overrides for the resource planner (§9.1); `None` = automatic."""

    devices: list[str] | None = None  # e.g. ["cuda:0", "cuda:3"] or ["cpu"]
    max_workers: int | None = Field(default=None, ge=1)
    threads_per_worker: int | None = Field(default=None, ge=1)


class Config(_Section):
    synthesis: SynthesisConfig = SynthesisConfig()
    output: OutputConfig = OutputConfig()
    qa: QaConfig = QaConfig()
    resources: ResourcesConfig = ResourcesConfig()
    tools: ToolsConfig = ToolsConfig()
    cover: CoverConfig = CoverConfig()
    # Per-language overrides, e.g. [lang.ru] g2p = "silero-stress" (§6.6.1).
    lang: dict[str, dict[str, Any]] = {}


def user_config_path() -> Path:
    return Path(platformdirs.user_config_dir("text2audio")) / "config.toml"


def _merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, Mapping) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(
    files: list[Path] | None = None, overrides: Mapping[str, Any] | None = None
) -> Config:
    """Merge TOML files (missing ones are skipped) and CLI overrides."""
    data: dict[str, Any] = {}
    for path in files if files is not None else [user_config_path()]:
        if path.is_file():
            data = _merge(data, tomllib.loads(path.read_text(encoding="utf-8")))
    data = _merge(data, overrides or {})
    return Config.model_validate(data)
