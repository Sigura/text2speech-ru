"""Read `models.lock`: every runtime asset pinned by revision and SHA-256."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from text2audio.core.contracts import ModelAsset


class LockError(Exception):
    pass


class _LockFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    asset: list[ModelAsset] = []


def default_lock_path() -> Path:
    return Path(os.environ.get("T2A_MODELS_LOCK", "models.lock"))


def load_lock(path: Path | None = None) -> list[ModelAsset]:
    """Assets in the lock file; a missing file means no assets."""
    path = path or default_lock_path()
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return _LockFile.model_validate(data).asset
    except (tomllib.TOMLDecodeError, ValidationError) as exc:
        raise LockError(f"{path}: {exc}") from exc


def assets_for(owner: str, assets: list[ModelAsset] | None = None) -> list[ModelAsset]:
    """Lock entries a plugin loads: by backend name or component id ("g2p:ruaccent")."""
    found = load_lock() if assets is None else assets
    return [a for a in found if owner in (a.backend, a.component)]
