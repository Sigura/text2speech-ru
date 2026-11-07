"""Public Python API; every CLI command has an equivalent here (FR-05)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from text2audio import __version__
from text2audio.core import resources as res
from text2audio.core.config import Config, load_config
from text2audio.core.contracts import Capabilities, ModelAsset, Tier
from text2audio.core.convert import ConvertResult, CoverResult
from text2audio.core.convert import convert as _convert
from text2audio.core.convert import remake_covers as _remake_covers
from text2audio.core.doctor import DoctorReport, run_doctor
from text2audio.core.effective import View, Voices, effective_config
from text2audio.core.effective import voices as _voices
from text2audio.core.envs import PluginEnv, UnknownEnv, plugin_env
from text2audio.core.envs import sync as _sync_env
from text2audio.core.plugins import Discovery, discover
from text2audio.core.revoice import RevoiceResult
from text2audio.core.revoice import revoice as _revoice
from text2audio.core.synthesize import Progress, SynthesisReport
from text2audio.core.synthesize import preview as _preview
from text2audio.core.synthesize import synthesize as _synthesize
from text2audio.core.translate import TranslateResult
from text2audio.core.translate import translate_file as _translate
from text2audio.models import bundle as _bundle
from text2audio.models.lock import assets_for, load_lock
from text2audio.models.pull import pull
from text2audio.models.store import AssetProblem, ModelStore


def version() -> str:
    return __version__


def doctor(
    store: Path | None = None,
    lock: Path | None = None,
    lang: str | None = None,
    tier: Tier | None = None,
) -> DoctorReport:
    return run_doctor(ModelStore(store), lock, langs=lang.split(",") if lang else None, tier=tier)


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


def translate(
    source: Path,
    output: Path | None = None,
    *,
    source_lang: str | None = None,
    target_lang: str | None = None,
    store: Path | None = None,
    progress: Progress | None = None,
) -> TranslateResult:
    """A Markdown file or a book folder's `<book>.md` into another language, offline;
    the Markdown structure is kept (default output `<name>.<target>.md`)."""
    return _translate(
        source,
        output,
        source_lang=source_lang,
        target_lang=target_lang,
        store=ModelStore(store),
        progress=progress,
    )


def revoice(
    source: Path,
    output: Path | None = None,
    *,
    source_lang: str = "en",
    target_lang: str | None = None,
    store: Path | None = None,
    progress: Progress | None = None,
) -> RevoiceResult:
    """An audio or video file re-spoken in another language, offline: video keeps its
    picture and gets a track timed to the original (default output `<name>.<lang>.<ext>`)."""
    from text2audio.core.config import user_config_path

    return _revoice(
        source,
        output,
        source_lang=source_lang,
        target_lang=target_lang,
        config=lambda folder: load_config([user_config_path(), folder.work / "text2audio.toml"]),
        store=ModelStore(store),
        progress=progress,
    )


def cover(
    folders: list[Path], *, mode: str | None = None, store: Path | None = None
) -> list[CoverResult]:
    """Choose the cover of converted books again (§6.8.2) under each book's `[cover]`
    settings, `mode` overriding `[cover] mode`; the Markdown is not touched."""
    overrides = {"cover": {"mode": mode}} if mode else None
    books = [(f, load_config(_book_config_files(f), overrides)) for f in folders]
    return _remake_covers(books, ModelStore(store))


def models_list(
    lang: str | None = None,
    tier: Tier | None = None,
    lock: Path | None = None,
    features: tuple[str, ...] = (),
) -> list[ModelAsset]:
    """Assets for the languages and tier; opt-in `features` ("translate") add theirs."""
    langs = lang.split(",") if lang else [None]
    return [a for a in load_lock(lock) if any(a.wanted(x, tier, features) for x in langs)]


def models_verify(
    lang: str | None = None,
    tier: Tier | None = None,
    store: Path | None = None,
    lock: Path | None = None,
    features: tuple[str, ...] = (),
) -> list[AssetProblem]:
    return ModelStore(store).verify(models_list(lang, tier, lock, features))


def models_pull(
    lang: str | None = None,
    tier: Tier | None = None,
    store: Path | None = None,
    lock: Path | None = None,
    features: tuple[str, ...] = (),
) -> list[Path]:
    """Download pinned assets. Uses the network; never call this from runtime code."""
    return pull(models_list(lang, tier, lock, features), ModelStore(store))


def bundle_export(
    output: Path,
    lang: str | None = None,
    tier: Tier | None = None,
    features: tuple[str, ...] = (),
    store: Path | None = None,
    progress: Callable[[str], None] = lambda _: None,
) -> Path:
    """Everything an offline machine needs in one tar (FR-16). Uses the network to fetch
    the Python packages; the models come from the local store."""
    from text2audio.core.envs import find_uv

    assets = models_list(lang, tier, None, features)
    envs = envs_for(assets)
    manifest = _bundle.Manifest(
        langs=lang.split(",") if lang else [],
        tier=tier,
        features=list(features),
        envs=[e.name for e in envs],
        commit=_bundle.commit(_bundle.REPO),
    )
    _bundle.export(
        output,
        assets=assets,
        store=ModelStore(store),
        envs=envs,
        manifest=manifest,
        uv=find_uv(),
        progress=progress,
    )
    return output


def bundle_import(folder: Path, store: Path | None = None) -> tuple[_bundle.Manifest, list[Path]]:
    """The bundle's models into the store, verified (`install.sh --bundle` calls this)."""
    manifest = _bundle.read_manifest(folder)
    assets = models_list(
        ",".join(manifest.langs) or None, manifest.tier, None, tuple(manifest.features)
    )
    return manifest, _bundle.import_models(folder, ModelStore(store), assets)


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
    """Environments declared by installed plugins (backends and components).

    `names` are environment names (`backend-silero`) or plugin names (`silero`).
    """
    found = discover(("backends", "g2p", "asr", "polishers", "translators", "taggers"))
    envs: dict[str, PluginEnv] = {}
    for items in found.plugins.values():
        for plugin in items.values():
            env = plugin_env(plugin)
            if env is not None:
                envs[env.name] = envs[getattr(plugin, "name", env.name)] = env
    if names:
        unknown = [n for n in names if n not in envs]
        if unknown:
            known = ", ".join(sorted({e.name for e in envs.values()}))
            raise UnknownEnv(f"unknown environment(s) {', '.join(unknown)}; known: {known}")
        chosen = {envs[n].name: envs[n] for n in names}
    else:
        chosen = {e.name: e for e in envs.values()}
    return [chosen[k] for k in sorted(chosen)]


def envs_for(assets: list[ModelAsset]) -> list[PluginEnv]:
    """The environments of the plugins that load any of `assets`: what a bundle for
    some languages and a tier needs, instead of every installed plugin's."""
    found = discover(("backends", "g2p", "asr", "polishers", "translators", "taggers"))
    envs: dict[str, PluginEnv] = {}
    for items in found.plugins.values():
        for plugin in items.values():
            env = plugin_env(plugin)
            if env is None:
                continue
            owner = getattr(plugin, "component", None) or plugin.name
            if assets_for(owner, assets) or any(a in assets for a in plugin.assets()):
                envs[env.name] = env
    return [envs[k] for k in sorted(envs)]


def envs_sync(names: list[str] | None = None) -> list[PluginEnv]:
    """Create or update plugin environments from their lock files (uses the network)."""
    envs = plugin_envs(names)
    for env in envs:
        _sync_env(env)
    return envs


def config_show(folder: Path, overrides: Mapping[str, Any] | None = None) -> View:
    """Effective settings of a book with the source of each value (FR-22)."""
    return effective_config(folder, overrides)


def voices(lang: str | None = None) -> Voices:
    """Voice roles per language pack and voices per installed backend."""
    return _voices(lang)
