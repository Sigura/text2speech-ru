"""`text2audio models pull`: the only runtime code path allowed to use the network."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from text2audio.core.contracts import ModelAsset
from text2audio.core.offline import allow_network
from text2audio.models.store import ModelStore, sha256_file

# (repo, file, revision, target dir) → downloaded path
Downloader = Callable[[str, str, str, Path], Path]


class PullError(Exception):
    pass


def _hf_download(repo: str, file: str, revision: str, target: Path) -> Path:
    from huggingface_hub import hf_hub_download  # imported only when pulling

    return Path(hf_hub_download(repo, file, revision=revision, local_dir=target))


def pull(
    assets: list[ModelAsset], store: ModelStore, downloader: Downloader | None = None
) -> list[Path]:
    """Download missing or corrupt files and verify every hash. Returns new files."""
    if downloader is None:
        allow_network()
        downloader = _hf_download
    fetched: list[Path] = []
    for asset in assets:
        for file in asset.files:
            if store.check_file(asset, file) is None:
                continue
            path = downloader(asset.repo, file, asset.revision, store.asset_dir(asset))
            if sha256_file(path) != asset.sha256[file]:
                path.unlink(missing_ok=True)
                raise PullError(
                    f"{asset.repo}/{file}@{asset.revision[:12]}: SHA-256 mismatch; "
                    "models.lock or the upstream file is wrong"
                )
            fetched.append(path)
    return fetched
