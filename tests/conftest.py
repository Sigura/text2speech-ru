from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest

from text2audio.core.contracts import ModelAsset, Segment
from text2audio.core.resources import Gpu, Resources
from text2audio.models.store import ModelStore

REV = "a" * 40
RU_TEXT = "«Привет, мир», — сказал он. Ёлка стояла в углу."


@pytest.fixture
def store(tmp_path: Path) -> ModelStore:
    return ModelStore(tmp_path / "store")


@pytest.fixture
def make_asset(store: ModelStore) -> Callable[..., ModelAsset]:
    """Create an asset; with install=True its file is also placed in the store."""

    def make(
        content: bytes = b"weights", install: bool = True, langs: tuple[str, ...] = ("ru",)
    ) -> ModelAsset:
        asset = ModelAsset(
            repo="org/model",
            revision=REV,
            files=("model.onnx",),
            sha256={"model.onnx": hashlib.sha256(content).hexdigest()},
            license="MIT",
            backend="dummy",
            langs=langs,
        )
        if install:
            p = store.path(asset, "model.onnx")
            p.parent.mkdir(parents=True)
            p.write_bytes(content)
        return asset

    return make


def write_lock(path: Path, assets: list[ModelAsset]) -> Path:
    lines = ["version = 1"]
    for a in assets:
        sha = ", ".join(f'"{k}" = "{v}"' for k, v in a.sha256.items())
        files = ", ".join(f'"{f}"' for f in a.files)
        langs = ", ".join(f'"{x}"' for x in a.langs)
        lines += [
            "[[asset]]",
            f'repo = "{a.repo}"',
            f'revision = "{a.revision}"',
            f"files = [{files}]",
            f"sha256 = {{ {sha} }}",
            f'license = "{a.license}"',
            f'backend = "{a.backend}"',
            f"langs = [{langs}]",
        ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def ru_segment() -> Segment:
    return Segment(id="ch01-s0001", chapter=1, text=RU_TEXT, lang="ru")


def gpu(index: int, free_mb: int, affinity: str | None = None) -> Gpu:
    from text2audio.core.resources import parse_cpu_list

    return Gpu(
        index,
        f"GPU-{index:08d}-0000",
        "RTX 4090",
        24564,
        free_mb,
        "8.9",
        parse_cpu_list(affinity) if affinity else None,
    )


def resources(cpus: int = 128, ram_gb: int = 300, gpus: tuple[Gpu, ...] = ()) -> Resources:
    return Resources(frozenset(range(cpus)), None, ram_gb * 1024, ram_gb * 1024, gpus)
