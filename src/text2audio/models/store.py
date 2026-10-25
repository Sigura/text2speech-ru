"""Local model store: `<root>/assets/<repo>/<revision>/<file>`, verified by SHA-256."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import platformdirs

from text2audio.core.contracts import ModelAsset


def default_store() -> Path:
    env = os.environ.get("T2A_MODEL_HOME")
    if env:
        return Path(env)
    return Path(platformdirs.user_data_dir("text2audio")) / "models"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pull_command(asset: ModelAsset) -> str:
    cmd = "text2audio models pull"
    if asset.langs:
        cmd += f" --lang {','.join(asset.langs)}"
    return cmd


@dataclass(frozen=True)
class AssetProblem:
    asset: ModelAsset
    file: str
    kind: Literal["missing", "corrupt"]

    def __str__(self) -> str:
        return (
            f"{self.kind} asset {self.file} from {self.asset.source} "
            f"(used by {self.asset.owner}); fix: {pull_command(self.asset)}"
        )


class ModelStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or default_store()

    def asset_dir(self, asset: ModelAsset) -> Path:
        if asset.url is not None:
            # Content-addressed: a changed upstream file never overwrites a pinned one.
            return self.root / "assets" / "url" / asset.sha256[asset.files[0]][:16]
        assert asset.repo is not None and asset.revision is not None
        return self.root / "assets" / asset.repo / asset.revision

    def path(self, asset: ModelAsset, file: str) -> Path:
        return self.asset_dir(asset) / file

    def check_file(self, asset: ModelAsset, file: str) -> AssetProblem | None:
        p = self.path(asset, file)
        if not p.is_file():
            return AssetProblem(asset, file, "missing")
        if sha256_file(p) != asset.sha256[file]:
            return AssetProblem(asset, file, "corrupt")
        return None

    def payload(self, assets: list[ModelAsset]) -> list[dict[str, object]]:
        """Where a worker finds its pinned files: `[{"dir": …, "files": […]}]`."""
        return [{"dir": str(self.asset_dir(a)), "files": list(a.files)} for a in assets]

    def verify(self, assets: list[ModelAsset]) -> list[AssetProblem]:
        return [
            problem
            for asset in assets
            for file in asset.files
            if (problem := self.check_file(asset, file)) is not None
        ]
