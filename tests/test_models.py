from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import REV, write_lock

from text2audio.core.contracts import ModelAsset
from text2audio.models.lock import LockError, load_lock
from text2audio.models.pull import PullError, pull
from text2audio.models.store import ModelStore


def test_lock_round_trip(tmp_path: Path, make_asset: Callable[..., ModelAsset]) -> None:
    asset = make_asset(install=False)
    assert load_lock(write_lock(tmp_path / "models.lock", [asset])) == [asset]


def test_missing_lock_means_no_assets(tmp_path: Path) -> None:
    assert load_lock(tmp_path / "nope.lock") == []


@pytest.mark.parametrize(
    "body",
    [
        'repo = "o/m"\nrevision = "main"\nfiles = ["a"]\nsha256 = { "a" = "' + "0" * 64 + '" }',
        f'repo = "o/m"\nrevision = "{REV}"\nfiles = ["a", "b"]\nsha256 = {{ "a" = "'
        + "0" * 64
        + '" }',
        f'repo = "o/m"\nrevision = "{REV}"\nfiles = ["../x"]\nsha256 = {{ "../x" = "'
        + "0" * 64
        + '" }',
    ],
    ids=["unpinned-revision", "missing-hash", "path-traversal"],
)
def test_invalid_lock_entries_are_rejected(tmp_path: Path, body: str) -> None:
    lock = tmp_path / "models.lock"
    lock.write_text(f"version = 1\n[[asset]]\n{body}\n")
    with pytest.raises(LockError):
        load_lock(lock)


def test_wanted_filters_by_language(make_asset: Callable[..., ModelAsset]) -> None:
    asset = make_asset(install=False, langs=("ru",))
    assert asset.wanted("ru", None) and asset.wanted(None, "fast")
    assert not asset.wanted("en", None)


def test_verify_reports_missing_and_corrupt(
    store: ModelStore, make_asset: Callable[..., ModelAsset]
) -> None:
    asset = make_asset()
    assert store.verify([asset]) == []
    store.path(asset, "model.onnx").write_bytes(b"tampered")
    (problem,) = store.verify([asset])
    assert problem.kind == "corrupt"
    assert "text2audio models pull --lang ru" in str(problem)
    store.path(asset, "model.onnx").unlink()
    assert store.verify([asset])[0].kind == "missing"


def test_pull_downloads_and_verifies(
    store: ModelStore, make_asset: Callable[..., ModelAsset]
) -> None:
    asset = make_asset(content=b"weights", install=False)

    def download(a: ModelAsset, file: str, target: Path) -> Path:
        assert (a.repo, a.revision) == ("org/model", REV)
        target.mkdir(parents=True, exist_ok=True)
        (target / file).write_bytes(b"weights")
        return target / file

    assert len(pull([asset], store, download)) == 1
    assert store.verify([asset]) == []
    assert pull([asset], store, download) == []  # already present: nothing fetched


def test_pull_rejects_hash_mismatch(
    store: ModelStore, make_asset: Callable[..., ModelAsset]
) -> None:
    asset = make_asset(install=False)

    def download(a: ModelAsset, file: str, target: Path) -> Path:
        target.mkdir(parents=True, exist_ok=True)
        (target / file).write_bytes(b"something else")
        return target / file

    with pytest.raises(PullError, match="SHA-256 mismatch"):
        pull([asset], store, download)
    assert not store.path(asset, "model.onnx").exists()


def test_url_asset_is_content_addressed_and_pulled(store: ModelStore) -> None:
    import hashlib

    data = b"silero package"
    asset = ModelAsset(
        url="https://models.example/tts/ru/v5_6_ru.pt",
        files=("v5_6_ru.pt",),
        sha256={"v5_6_ru.pt": hashlib.sha256(data).hexdigest()},
        backend="silero",
        langs=("ru",),
    )
    assert asset.source == "https://models.example/tts/ru/v5_6_ru.pt"
    assert store.asset_dir(asset).parts[-2:] == ("url", asset.sha256["v5_6_ru.pt"][:16])

    def download(a: ModelAsset, file: str, target: Path) -> Path:
        assert a.url is not None and file == "v5_6_ru.pt"
        target.mkdir(parents=True, exist_ok=True)
        (target / file).write_bytes(data)
        return target / file

    assert len(pull([asset], store, download)) == 1
    assert store.verify([asset]) == []


@pytest.mark.parametrize(
    "fields",
    [
        {"files": ("a",)},  # no source at all
        {"url": "https://x/a", "repo": "o/m", "files": ("a",)},  # both sources
        {"url": "https://x/a", "files": ("a", "b")},  # url asset with two files
    ],
)
def test_asset_source_rules(fields: dict[str, object]) -> None:
    sha = {f: "0" * 64 for f in fields["files"]}  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        ModelAsset(sha256=sha, **fields)  # type: ignore[arg-type]


def test_download_failure_becomes_pull_error(
    store: ModelStore, make_asset: Callable[..., ModelAsset]
) -> None:
    def download(a: ModelAsset, file: str, target: Path) -> Path:
        raise OSError("404 Entry Not Found")

    with pytest.raises(PullError, match=r"model\.onnx from org/model@a{12}: download failed"):
        pull([make_asset(install=False)], store, download)
