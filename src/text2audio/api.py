"""Public Python API; every CLI command has an equivalent here (FR-05)."""

from __future__ import annotations

from pathlib import Path

from text2audio import __version__
from text2audio.core import resources as res
from text2audio.core.config import Config, load_config
from text2audio.core.contracts import Capabilities, ModelAsset, Tier
from text2audio.core.convert import ConvertResult
from text2audio.core.convert import convert as _convert
from text2audio.core.doctor import DoctorReport, run_doctor
from text2audio.core.envs import PluginEnv, plugin_env
from text2audio.core.envs import sync as _sync_env
from text2audio.core.plugins import Discovery, discover
from text2audio.core.synthesize import Progress, SynthesisReport
from text2audio.core.synthesize import preview as _preview
from text2audio.core.synthesize import synthesize as _synthesize
from text2audio.models.lock import load_lock
from text2audio.models.pull import pull
from text2audio.models.store import AssetProblem, ModelStore


def version() -> str:
    return __version__


def doctor(store: Path | None = None, lock: Path | None = None) -> DoctorReport:
    return run_doctor(ModelStore(store), lock)


def resources(
    config: Config | None = None, caps: Capabilities | None = None
) -> tuple[res.Resources, res.ResourcePlan]:
    """Probe the hardware and plan workers, honouring `[resources]` and `[synthesis]`."""
    config = config or load_config()
    found = res.probe()
    rc = config.resources
    plan = res.plan(
        found,
        caps,
        tier=config.synthesis.tier,
        devices=rc.devices,
        max_workers=rc.max_workers,
        threads_per_worker=rc.threads_per_worker,
    )
    return found, plan


def plugins() -> Discovery:
    return discover()


def convert(
    inputs: list[Path],
    output_root: Path | None = None,
    *,
    name: str | None = None,
    overwrite: bool = False,
    reconvert: bool = False,
    lang: str | None = None,
    config: Config | None = None,
) -> list[ConvertResult]:
    """Input files or directories → one book folder each (§5.2).

    With `reconvert`, `inputs` are book folders and their stored source is read again;
    an edited `<book>.md` is kept and the new text goes to `<book>.reconverted.md`.
    """
    config = config or load_config()
    root = output_root or config.output.root
    lang = lang or config.synthesis.language
    return _convert(
        inputs, root, name=name, overwrite=overwrite, reconvert=reconvert, lang=lang, config=config
    )


def models_list(
    lang: str | None = None, tier: Tier | None = None, lock: Path | None = None
) -> list[ModelAsset]:
    langs = lang.split(",") if lang else [None]
    return [a for a in load_lock(lock) if any(a.wanted(x, tier) for x in langs)]


def models_verify(
    lang: str | None = None,
    tier: Tier | None = None,
    store: Path | None = None,
    lock: Path | None = None,
) -> list[AssetProblem]:
    return ModelStore(store).verify(models_list(lang, tier, lock))


def models_pull(
    lang: str | None = None,
    tier: Tier | None = None,
    store: Path | None = None,
    lock: Path | None = None,
) -> list[Path]:
    """Download pinned assets. Uses the network; never call this from runtime code."""
    return pull(models_list(lang, tier, lock), ModelStore(store))


def synthesize(
    folder: Path,
    *,
    config: Config | None = None,
    tier: Tier | None = None,
    max_workers: int | None = None,
    store: Path | None = None,
    progress: Progress | None = None,
) -> SynthesisReport:
    """Book folder → audio; only segments whose text or settings changed are rendered."""
    config = config or load_config(_book_config_files(folder))
    return _synthesize(
        folder,
        config,
        store=ModelStore(store),
        tier=tier,
        max_workers=max_workers,
        progress=progress,
    )


def preview(
    folder: Path,
    *,
    chapter: int | None = None,
    seconds: float = 30,
    config: Config | None = None,
    store: Path | None = None,
    tier: Tier | None = None,
    max_workers: int | None = None,
    progress: Progress | None = None,
) -> Path:
    """A short MP3 sample of a chapter in `<book>/_work/preview/` (§6.11)."""
    config = config or load_config(_book_config_files(folder))
    return _preview(
        folder,
        config,
        chapter=chapter,
        seconds=seconds,
        store=ModelStore(store),
        tier=tier,
        max_workers=max_workers,
        progress=progress,
    )


def _book_config_files(folder: Path) -> list[Path]:
    from text2audio.core.config import user_config_path

    return [user_config_path(), folder / "_work" / "text2audio.toml"]


def plugin_envs(names: list[str] | None = None) -> list[PluginEnv]:
    """Environments declared by installed plugins (backends and components)."""
    found = discover(("backends", "g2p", "asr", "polishers", "translators"))
    envs: dict[str, PluginEnv] = {}
    for items in found.plugins.values():
        for plugin in items.values():
            env = plugin_env(plugin)
            if env is not None and (not names or env.name in names):
                envs[env.name] = env
    return [envs[k] for k in sorted(envs)]


def envs_sync(names: list[str] | None = None) -> list[PluginEnv]:
    """Create or update plugin environments from their lock files (uses the network)."""
    envs = plugin_envs(names)
    for env in envs:
        _sync_env(env)
    return envs
