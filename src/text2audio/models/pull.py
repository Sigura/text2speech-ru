"""`text2audio models pull`: the only runtime code path allowed to use the network."""

from __future__ import annotations

import shutil
import urllib.request
from collections.abc import Callable
from pathlib import Path

from text2audio.core.contracts import ModelAsset
from text2audio.core.offline import allow_network
from text2audio.models.store import ModelStore, sha256_file

# (asset, file, target dir) → downloaded path
Downloader = Callable[[ModelAsset, str, Path], Path]


class PullError(Exception):
    pass


def _download(asset: ModelAsset, file: str, target: Path) -> Path:
    if asset.url is not None:
        target.mkdir(parents=True, exist_ok=True)
        path, partial = target / file, target / f".{file}.partial"
        with urllib.request.urlopen(asset.url, timeout=60) as resp, partial.open("wb") as f:
            shutil.copyfileobj(resp, f, 1 << 20)
        partial.replace(path)
        return path

    from huggingface_hub import hf_hub_download  # imported only when pulling

    assert asset.repo is not None
    return Path(hf_hub_download(asset.repo, file, revision=asset.revision, local_dir=target))


def pull(
    assets: list[ModelAsset], store: ModelStore, downloader: Downloader | None = None
) -> list[Path]:
    """Download missing or corrupt files and verify every hash. Returns new files."""
    if downloader is None:
        allow_network()
        downloader = _download
    fetched: list[Path] = []
    for asset in assets:
        for file in asset.files:
            if store.check_file(asset, file) is None:
                continue
            try:
                path = downloader(asset, file, store.asset_dir(asset))
            except Exception as exc:  # network, 404, gated repo, disk full…
                raise PullError(f"{file} from {asset.source}: download failed: {exc}") from exc
            if sha256_file(path) != asset.sha256[file]:
                path.unlink(missing_ok=True)
                raise PullError(
                    f"{file} from {asset.source}: SHA-256 mismatch; "
                    "models.lock or the upstream file is wrong"
                )
            fetched.append(path)
    return fetched
