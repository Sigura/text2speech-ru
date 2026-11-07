"""M5 / FR-16: `bundle export` packs what an offline install needs; `bundle import` puts
the models into the store, verified. The whole round trip with real uv and networking
blocked is `scripts/test-bundle.sh`."""

from __future__ import annotations

import json
import subprocess
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from text2audio.cli import ExitCode, app
from text2audio.core.contracts import ModelAsset
from text2audio.models import bundle
from text2audio.models.pull import PullError
from text2audio.models.store import ModelStore

MANIFEST = bundle.Manifest(
    langs=["ru"], tier="fast", features=[], envs=["backend-silero"], commit="abc123"
)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "plugins").mkdir(parents=True)
    (repo / "install.sh").write_text("#!/bin/sh\n")
    (repo / "plugins" / "uv.lock").write_text("lock\n")
    (repo / "untracked.txt").write_text("not in git\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "install.sh", "plugins"], check=True)
    return repo


def _export(tmp_path: Path, store: ModelStore, asset: ModelAsset) -> Path:
    repo = _repo(tmp_path)
    cache = tmp_path / "cache"
    (cache / "archive-v0").mkdir(parents=True)
    (cache / "archive-v0" / "wheel").write_text("wheel\n")
    python = tmp_path / "cpython-3.12.15-linux-x86_64-gnu"
    (python / "bin").mkdir(parents=True)
    (python / "bin" / "python3.12").write_text("")
    uv = tmp_path / "uv"
    uv.write_text("#!/bin/sh\n")
    uv.chmod(0o755)
    out = tmp_path / "t2a.tar"
    bundle.write(
        out,
        repo=repo,
        files=bundle.source_files(repo),
        store=store,
        assets=[asset],
        cache=cache,
        pythons=[python],
        uv=str(uv),
        manifest=MANIFEST,
    )
    return out


def _unpack(tar_path: Path, into: Path) -> Path:
    with tarfile.open(tar_path) as tar:
        tar.extractall(into, filter="data")
    return into / bundle.ROOT / "bundle"


def test_export_packs_source_models_packages_python_and_uv(
    tmp_path: Path, store: ModelStore, make_asset: Callable[..., ModelAsset]
) -> None:
    asset = make_asset()
    out = _export(tmp_path, store, asset)
    with tarfile.open(out) as tar:
        names = set(tar.getnames())
        meta = tar.extractfile("text2audio/bundle/manifest.json")
        assert meta is not None
        manifest = json.loads(meta.read())
        uv_mode = tar.getmember("text2audio/bundle/bin/uv").mode
    model = f"text2audio/bundle/models/assets/org/model/{asset.revision}/model.onnx"
    assert {
        "text2audio/install.sh",
        "text2audio/plugins/uv.lock",
        model,
        "text2audio/bundle/uv-cache/archive-v0/wheel",
        "text2audio/bundle/python/cpython-3.12.15-linux-x86_64-gnu/bin/python3.12",
    } <= names
    assert "text2audio/untracked.txt" not in names
    assert manifest == {
        "langs": ["ru"],
        "tier": "fast",
        "features": [],
        "envs": ["backend-silero"],
        "commit": "abc123",
        "format": 1,
    }
    assert uv_mode & 0o111
    assert not list(tmp_path.glob(".*.partial"))


def test_export_refuses_a_store_without_the_models_before_downloading(
    tmp_path: Path, store: ModelStore, make_asset: Callable[..., ModelAsset]
) -> None:
    missing = make_asset(install=False)
    with pytest.raises(bundle.BundleError, match=r"missing asset model\.onnx"):
        bundle.export(
            tmp_path / "t2a.tar",
            assets=[missing],
            store=store,
            envs=[],
            manifest=MANIFEST,
            uv=str(tmp_path / "no-uv"),
            repo=_repo(tmp_path),
        )
    assert not (tmp_path / "t2a.tar").exists()


def test_export_refuses_untracked_code_it_would_leave_out(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    (repo / "plugins" / "new.py").write_text("x = 1\n")
    with pytest.raises(bundle.BundleError, match=r"plugins/new\.py"):
        bundle.source_files(repo)


def test_source_files_need_a_git_checkout(tmp_path: Path) -> None:
    with pytest.raises(bundle.BundleError, match="not a git checkout"):
        bundle.source_files(tmp_path)


def test_import_copies_and_verifies_the_models(
    tmp_path: Path, store: ModelStore, make_asset: Callable[..., ModelAsset]
) -> None:
    asset = make_asset()
    unpacked = _unpack(_export(tmp_path, store, asset), tmp_path / "target")
    assert bundle.read_manifest(unpacked) == MANIFEST
    target = ModelStore(tmp_path / "new-store")
    copied = bundle.import_models(unpacked, target, [asset])
    assert copied == [target.path(asset, "model.onnx")]
    assert target.verify([asset]) == []
    assert bundle.import_models(unpacked, target, [asset]) == []


def test_import_refuses_a_damaged_model(
    tmp_path: Path, store: ModelStore, make_asset: Callable[..., ModelAsset]
) -> None:
    asset = make_asset()
    unpacked = _unpack(_export(tmp_path, store, asset), tmp_path / "target")
    damaged = next((unpacked / "models").rglob("model.onnx"))
    damaged.write_bytes(b"not the weights")
    target = ModelStore(tmp_path / "new-store")
    with pytest.raises(PullError, match="SHA-256 mismatch"):
        bundle.import_models(unpacked, target, [asset])
    assert not target.path(asset, "model.onnx").exists()


def test_cli_import_needs_an_exported_bundle(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["bundle", "import", str(tmp_path)])
    assert result.exit_code == ExitCode.USAGE
    assert "manifest.json" in result.output


def test_a_bundle_holds_only_the_environments_its_models_need() -> None:
    from text2audio import api

    ru_fast = {e.name for e in api.envs_for(api.models_list("ru", "fast"))}
    assert {"backend-silero", "g2p-ruaccent", "asr-gigaam"} <= ru_fast
    assert not {"backend-chatterbox", "asr-whisper", "backend-kokoro", "mt-hf"} & ru_fast
    translate = api.envs_for(api.models_list("ru", "fast", features=("translate",)))
    assert "mt-hf" in {e.name for e in translate}
