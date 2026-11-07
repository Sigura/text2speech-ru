"""`text2audio bundle`: everything an offline machine needs, in one tar (FR-16, §6.10).

Everything sits under `text2audio/`:
- the repository's tracked files (install.sh, uv.lock, models.lock, plugins);
- `bundle/manifest.json`: the languages, tier and features the bundle was made for;
- `bundle/models/`: a model store with the pinned assets for those;
- `bundle/uv-cache/`: a uv cache filled by syncing the main env and every worker env,
  so `uv sync --offline` installs from it alone;
- `bundle/python/`: the CPython builds uv creates those envs with;
- `bundle/bin/uv`.

On the target, `./install.sh --bundle bundle` runs the usual install steps with uv
offline and `bundle import` copying the models into the store.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from text2audio.core.contracts import ModelAsset, Tier
from text2audio.core.envs import ENV_PYTHON, PluginEnv, sync
from text2audio.models.pull import pull
from text2audio.models.store import ModelStore

ROOT = "text2audio"
MANIFEST = "manifest.json"
FORMAT = 1
REPO = Path(__file__).resolve().parents[3]


class BundleError(Exception):
    pass


@dataclass(frozen=True)
class Manifest:
    langs: list[str]  # empty = every language
    tier: Tier | None
    features: list[str]
    envs: list[str]  # worker environments for the plugins of those models
    commit: str
    format: int = FORMAT


def _run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    try:
        done = subprocess.run(
            cmd,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise BundleError(f"{' '.join(cmd)} failed:\n{exc.stderr.strip()}") from exc
    return done.stdout.strip()


def source_files(repo: Path) -> list[str]:
    """The repository's tracked files: what a clone would have."""
    if not (repo / ".git").exists():
        raise BundleError(f"{repo} is not a git checkout; export from the repository")
    loose = _run(["git", "ls-files", "--others", "--exclude-standard", "src", "plugins"], cwd=repo)
    if loose:
        raise BundleError(
            "untracked files under src/ or plugins/ would be left out of the bundle; "
            f"commit or remove them first:\n{loose}"
        )
    listed = _run(["git", "ls-files", "-z"], cwd=repo).split("\0")
    return [f for f in listed if f and (repo / f).is_file()]


def commit(repo: Path) -> str:
    return _run(["git", "describe", "--always", "--dirty"], cwd=repo)


def python_installs(uv: str, versions: list[str]) -> list[Path]:
    """The managed CPython directories for `versions`, installed first if missing."""
    _run([uv, "python", "install", *versions])
    found = []
    for v in versions:
        exe = Path(_run([uv, "python", "find", "--managed-python", v]))
        found.append(exe.resolve().parents[1])
    return found


def fill_cache(
    uv: str, cache: Path, repo: Path, envs: list[PluginEnv], scratch: Path, main_python: str
) -> None:
    """Sync the main env and every worker env into throwaway dirs with `cache` as the uv
    cache, so it ends up holding every wheel the lock files name."""
    vars_ = {"UV_CACHE_DIR": str(cache)}
    _run(
        [uv, "sync", "--frozen", "--quiet", "--python", main_python],
        cwd=repo,
        env={**vars_, "UV_PROJECT_ENVIRONMENT": str(scratch / "main")},
    )
    for env in envs:
        try:
            sync(
                env,
                uv=uv,
                quiet=True,
                extra_env={**vars_, "UV_PROJECT_ENVIRONMENT": str(scratch / env.name)},
            )
        except subprocess.CalledProcessError as exc:
            raise BundleError(f"syncing environment {env.name} failed") from exc


def check_store(store: ModelStore, assets: list[ModelAsset]) -> None:
    problems = store.verify(assets)
    if problems:
        raise BundleError(
            "the store lacks files to export:\n" + "\n".join(f"  {p}" for p in problems)
        )


def write(
    out: Path,
    *,
    repo: Path,
    files: list[str],
    store: ModelStore,
    assets: list[ModelAsset],
    cache: Path,
    pythons: list[Path],
    uv: str,
    manifest: Manifest,
) -> None:
    check_store(store, assets)
    partial = out.with_name(f".{out.name}.partial")
    with tarfile.open(partial, "w", format=tarfile.PAX_FORMAT) as tar:
        for f in files:
            tar.add(repo / f, f"{ROOT}/{f}", recursive=False)
        meta = json.dumps(asdict(manifest)).encode()  # one line: install.sh reads it with sed
        info = tarfile.TarInfo(f"{ROOT}/bundle/{MANIFEST}")
        info.size = len(meta)
        info.mode = 0o644
        tar.addfile(info, io.BytesIO(meta))
        for asset in assets:
            for file in asset.files:
                path = store.path(asset, file)
                tar.add(path, f"{ROOT}/bundle/models/{path.relative_to(store.root)}")
        tar.add(cache, f"{ROOT}/bundle/uv-cache")
        for p in pythons:
            tar.add(p, f"{ROOT}/bundle/python/{p.name}")
        tar.add(uv, f"{ROOT}/bundle/bin/uv")
    partial.replace(out)


def export(
    out: Path,
    *,
    assets: list[ModelAsset],
    store: ModelStore,
    envs: list[PluginEnv],
    manifest: Manifest,
    uv: str,
    progress: Callable[[str], None] = lambda _: None,
    repo: Path = REPO,
) -> None:
    """Write the bundle to `out` (uses the network to fill the package cache)."""
    check_store(store, assets)
    files = source_files(repo)
    main_python = (repo / ".python-version").read_text(encoding="utf-8").strip()
    progress(f"Python {main_python} and {ENV_PYTHON}")
    pythons = python_installs(uv, sorted({main_python, ENV_PYTHON}))
    with tempfile.TemporaryDirectory(prefix=".t2a-bundle-", dir=out.parent) as tmp:
        cache = Path(tmp) / "uv-cache"
        progress(f"Python packages for the main env and {len(envs)} worker env(s)")
        fill_cache(uv, cache, repo, envs, Path(tmp) / "envs", main_python)
        progress(f"Writing {out}")
        write(
            out,
            repo=repo,
            files=files,
            store=store,
            assets=assets,
            cache=cache,
            pythons=pythons,
            uv=uv,
            manifest=manifest,
        )


def read_manifest(bundle: Path) -> Manifest:
    path = bundle / MANIFEST
    if not path.is_file():
        raise BundleError(f"{bundle}: no {MANIFEST}; pass the bundle/ folder of an export")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != FORMAT:
        raise BundleError(f"{path}: format {data.get('format')}, this version reads {FORMAT}")
    return Manifest(**data)


def import_models(bundle: Path, store: ModelStore, assets: list[ModelAsset]) -> list[Path]:
    """Copy the bundle's model files into `store`, verifying every hash."""
    source = ModelStore(bundle / "models")

    def copy(asset: ModelAsset, file: str, target: Path) -> Path:
        (target / file).parent.mkdir(parents=True, exist_ok=True)
        return Path(shutil.copy2(source.path(asset, file), target / file))

    return pull(assets, store, downloader=copy)
