"""Command-line interface (SPEC §6.11)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.markup import escape

from text2audio.console import StageProgress, duration, emit, plural
from text2audio.core.offline import allow_network, enforce_offline
from text2audio.models.store import default_store

if TYPE_CHECKING:
    from text2audio.core.convert import ConvertResult


class ExitCode(IntEnum):
    OK = 0
    USAGE = 1
    INPUT = 2
    MISSING_ASSET = 3  # also: offline violation
    QA_WARNINGS = 4
    INTERNAL = 5


app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)
models_app = typer.Typer(no_args_is_help=True, help="Manage the local model store.")
app.add_typer(models_app, name="models")
envs_app = typer.Typer(no_args_is_help=True, help="Manage plugin worker environments.")
app.add_typer(envs_app, name="envs")
bundle_app = typer.Typer(no_args_is_help=True, help="Move an install to an offline machine.")
app.add_typer(bundle_app, name="bundle")
config_app = typer.Typer(no_args_is_help=True, help="Inspect the configuration of a book.")
app.add_typer(config_app, name="config")

LangOpt = Annotated[str | None, typer.Option("--lang", help="Language code(s), e.g. ru or ru,en.")]
TierOpt = Annotated[str | None, typer.Option("--tier", help="fast | expressive")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]
LockOpt = Annotated[Path | None, typer.Option("--lock", help="Path to models.lock.")]
EventsOpt = Annotated[
    bool,
    typer.Option("--events", help="JSON lines on stdout: progress, stages, results (for scripts)."),
]
DeepSeekKeyFlag = Annotated[
    str | None,
    typer.Option(
        "--deepseek-key",
        envvar="T2A_DEEPSEEK_KEY",
        show_default=False,
        help="DeepSeek API key: translation and stress marks go to the API first, the offline "
        "models are the fallback. Texts are sent to api.deepseek.com.",
    ),
]
FeatureOpt = Annotated[
    list[str] | None,
    typer.Option("--feature", help="Opt-in assets too, e.g. translate (repeatable)."),
]


@app.callback()
def _root() -> None:
    # Before anything imports an ML library (§6.10). `models pull` lifts it.
    enforce_offline(default_store())


def _tier(value: str | None) -> Any:
    if value not in (None, "fast", "expressive"):
        raise typer.BadParameter("tier must be 'fast' or 'expressive'")
    return value


def _sets(obj: Any) -> Any:
    if isinstance(obj, frozenset | set):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _sets(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_sets(v) for v in obj]
    return obj


def _compact_cpus(cpus: frozenset[int] | None) -> str:
    if not cpus:
        return "-"
    ordered, runs = sorted(cpus), []
    start = prev = ordered[0]
    for c in [*ordered[1:], None]:
        if c is not None and c == prev + 1:
            prev = c
            continue
        runs.append(f"{start}-{prev}" if prev != start else str(start))
        if c is not None:
            start = prev = c
    return ",".join(runs)


@app.command()
def version() -> None:
    """Print the version."""
    from text2audio import api

    typer.echo(api.version())


@app.command()
def doctor(
    json_out: JsonOpt = False, lock: LockOpt = None, lang: LangOpt = None, tier: TierOpt = None
) -> None:
    """Check tools, hardware, models and plugins; exit 3 if something is missing.
    `--lang` / `--tier` check only the models installed for them (default: all)."""
    from text2audio import api

    report = api.doctor(lock=lock, lang=lang, tier=_tier(tier))
    if json_out:
        typer.echo(json.dumps(_sets([asdict(c) for c in report.checks]), indent=2))
    else:
        marks = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
        for c in report.checks:
            typer.echo(f"[{marks[c.status]}] {c.name}: {c.detail}")
        typer.echo("offline-ready" if report.ok else "NOT ready — fix the FAIL lines above")
    raise typer.Exit(ExitCode.OK if report.ok else ExitCode.MISSING_ASSET)


@app.command()
def resources(json_out: JsonOpt = False) -> None:
    """Show usable CPU/RAM/GPU and the planned worker slots."""
    from text2audio import api

    found, plan = api.resources()
    if json_out:
        data = {"resources": asdict(found), "plan": asdict(plan)}
        typer.echo(json.dumps(_sets(data), indent=2))
        return
    typer.echo(
        f"CPUs: {found.usable_cpus} usable ({_compact_cpus(found.cpus)}); "
        f"RAM: {found.ram_available_mb // 1024}/{found.ram_total_mb // 1024} GiB available"
    )
    for g in found.gpus:
        typer.echo(f"  {g.label}, NUMA CPUs {_compact_cpus(g.cpu_affinity)}")
    for note in found.notes:
        typer.echo(f"  note: {note}")
    typer.echo(f"Tier: {plan.tier}")
    for s in plan.slots:
        typer.echo(
            f"  worker {s.device}: {s.threads} threads on CPUs {_compact_cpus(s.cpu_affinity)}"
        )
    for r in plan.reasons:
        typer.echo(f"  - {r}")


@app.command()
def plugins() -> None:
    """List discovered plugins by entry-point group."""
    from text2audio import api

    found = api.plugins()
    for group, items in found.plugins.items():
        typer.echo(f"{group}: {', '.join(sorted(items)) or '-'}")
    for error in found.errors:
        typer.echo(f"error: {error}", err=True)


@models_app.command("list")
def models_list(
    lang: LangOpt = None, tier: TierOpt = None, lock: LockOpt = None, feature: FeatureOpt = None
) -> None:
    """List pinned assets from models.lock."""
    from text2audio import api

    for a in api.models_list(lang, _tier(tier), lock, tuple(feature or ())):
        typer.echo(f"{a.owner}\t{a.source}\t{len(a.files)} file(s)\t{a.license}")


@models_app.command("verify")
def models_verify(
    lang: LangOpt = None, tier: TierOpt = None, lock: LockOpt = None, feature: FeatureOpt = None
) -> None:
    """Verify SHA-256 of every pinned asset in the store."""
    from text2audio import api

    problems = api.models_verify(lang, _tier(tier), lock=lock, features=tuple(feature or ()))
    for p in problems:
        typer.echo(str(p), err=True)
    if problems:
        raise typer.Exit(ExitCode.MISSING_ASSET)
    typer.echo("all assets verified")


@models_app.command("pull")
def models_pull(
    lang: LangOpt = None, tier: TierOpt = None, lock: LockOpt = None, feature: FeatureOpt = None
) -> None:
    """Download pinned assets (uses the network)."""
    from text2audio import api
    from text2audio.models.pull import PullError

    try:
        fetched = api.models_pull(lang, _tier(tier), lock=lock, features=tuple(feature or ()))
    except PullError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(ExitCode.MISSING_ASSET) from exc
    typer.echo(f"{len(fetched)} file(s) downloaded")


@bundle_app.command("export")
def bundle_export(
    output: Annotated[Path, typer.Option("-o", "--output", help="The .tar to write.")],
    lang: LangOpt = None,
    tier: TierOpt = None,
    feature: FeatureOpt = None,
) -> None:
    """Pack the installed models, the Python packages of every environment, Python and uv
    into one tar for an offline machine (uses the network for the packages, FR-16).
    Default: every language and tier."""
    from text2audio import api
    from text2audio.core.envs import EnvMissing
    from text2audio.models.bundle import BundleError

    allow_network()
    try:
        api.bundle_export(
            output,
            lang,
            _tier(tier),
            tuple(feature or ()),
            progress=lambda step: typer.echo(f"== {step}"),
        )
    except (BundleError, EnvMissing) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ExitCode.MISSING_ASSET) from exc
    typer.echo(f"{output}: {output.stat().st_size / 1e9:.1f} GB")
    typer.echo("on the offline machine: tar xf it, then ./text2audio/install.sh --bundle")


@bundle_app.command("import")
def bundle_import(
    folder: Annotated[Path, typer.Argument(help="The bundle/ folder of an unpacked export.")],
) -> None:
    """Copy the bundle's models into the model store and verify them (offline)."""
    from text2audio import api
    from text2audio.models.bundle import BundleError
    from text2audio.models.pull import PullError

    try:
        manifest, copied = api.bundle_import(folder)
    except BundleError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ExitCode.USAGE) from exc
    except PullError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ExitCode.MISSING_ASSET) from exc
    langs = ",".join(manifest.langs) or "all languages"
    typer.echo(f"{len(copied)} file(s) copied ({langs}, tier {manifest.tier or 'all'})")


@app.command()
def convert(
    inputs: Annotated[
        list[Path], typer.Argument(help="Book files or folders (with --reconvert: book folders).")
    ],
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="Output root (default ./output).")
    ] = None,
    name: Annotated[str | None, typer.Option("--name", help="Book folder name.")] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Reuse a clashing folder name.")
    ] = False,
    reconvert: Annotated[
        bool, typer.Option("--reconvert", help="Convert the stored source of book folders again.")
    ] = False,
    lang: Annotated[
        str | None, typer.Option("--lang", help="Language of TXT/MD input (default: guessed).")
    ] = None,
    events: EventsOpt = False,
) -> None:
    """Convert books to editable Markdown, one folder per book."""
    from text2audio import api

    try:
        results = api.convert(inputs, output, name=name, overwrite=overwrite, reconvert=reconvert)
    except ValueError as exc:
        if events:
            emit("error", message=str(exc))
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ExitCode.USAGE) from exc
    for r in results:
        if r.status == "failed":
            if events:
                emit("error", input=r.input, message=str(r.error))
            typer.echo(f"FAILED {r.input}: {r.error}", err=True)
            continue
        if events:
            emit("converted", **_converted(r))
            continue
        note = {
            "converted": "",
            "unchanged": " (unchanged)",
            "kept-edits": " (your edits kept; new text in this file)",
        }[r.status]
        typer.echo(
            f"{r.markdown}{note} — {r.format}, {r.chapters} chapter(s), {r.segments} segments"
        )
        for w in r.warnings:
            typer.echo(f"  warning: {w}")
    failed = [r for r in results if r.status == "failed"]
    if len(results) > 1 and not events:
        typer.echo(f"{len(results) - len(failed)} of {len(results)} book(s) converted")
    if any(r.internal for r in failed):
        raise typer.Exit(ExitCode.INTERNAL)
    if failed or not results:
        raise typer.Exit(ExitCode.INPUT)


def _converted(r: ConvertResult) -> dict[str, Any]:
    """The `converted` event of `--events`."""
    return {
        "input": r.input,
        "folder": r.folder,
        "markdown": r.markdown,
        "format": r.format,
        "chapters": r.chapters,
        "segments": r.segments,
        "status": r.status,
        "warnings": r.warnings,
    }


SourceArg = Annotated[Path, typer.Argument(help="A Markdown file or a book folder.")]
OutputOpt = Annotated[
    Path | None, typer.Option("-o", "--output", help="Output file (default <name>.<lang>.md).")
]


def _translate(
    source: Path,
    output: Path | None,
    source_lang: str | None,
    target: str | None,
    events: bool = False,
    deepseek_key: str | None = None,
) -> Path:
    from text2audio import api
    from text2audio.core.envs import EnvMissing
    from text2audio.core.translate import TranslateError
    from text2audio.workers.process import WorkerError

    with StageProgress(events=events) as ui:
        try:
            r = api.translate(
                source,
                output,
                source_lang=source_lang,
                target_lang=target,
                progress=ui,
                deepseek_key=deepseek_key,
            )
        except TranslateError as exc:
            ui.fail("error:", str(exc), indent="")
            raise typer.Exit(ExitCode.INPUT) from exc
        except (EnvMissing, WorkerError) as exc:
            ui.fail("error:", str(exc), indent="")
            raise typer.Exit(ExitCode.MISSING_ASSET) from exc
        s = r.stats
        ui.event(
            "translated",
            source=r.source,
            output=r.output,
            source_lang=r.source_lang,
            target_lang=r.target_lang,
            translator=r.translator,
            device=r.device,
            pieces=s.pieces,
            translated=s.translated,
            cached=s.cached,
            by=s.by,
            placeholders=s.placeholders,
            warnings=s.warnings,
        )
        extra = [f"{n} by {name}" for name, n in s.by.items() if name != r.translator]
        if s.placeholders:
            extra.append(f"{s.placeholders} redone to keep abbreviations")
        ui.result(
            f"{r.output} — {r.source_lang} → {r.target_lang} by {r.translator} on {r.device}: "
            f"{s.pieces} pieces, {s.translated} translated now, {s.cached} cached"
            + (f" ({'; '.join(extra)})" if extra else "")
        )
        for w in s.warnings:
            ui.warn(w)
    return r.output


@app.command()
def translate(
    source: SourceArg,
    output: OutputOpt = None,
    to: Annotated[
        str | None,
        typer.Option("--to", help="Target language (default: the first the source has)."),
    ] = None,
    source_lang: Annotated[
        str | None, typer.Option("--from", help="Source language (default: guessed).")
    ] = None,
    deepseek_key: DeepSeekKeyFlag = None,
    events: EventsOpt = False,
) -> None:
    """Translate a book's Markdown, keeping its structure: offline, or with the DeepSeek
    API first when a key is given."""
    _translate(source, output, source_lang, to, events, deepseek_key)


@app.command(hidden=True)
def ru2en(
    source: SourceArg,
    output: OutputOpt = None,
    deepseek_key: DeepSeekKeyFlag = None,
    events: EventsOpt = False,
) -> None:
    """Same as `translate --from ru --to en`."""
    _translate(source, output, "ru", "en", events, deepseek_key)


@app.command(hidden=True)
def en2ru(
    source: SourceArg,
    output: OutputOpt = None,
    deepseek_key: DeepSeekKeyFlag = None,
    events: EventsOpt = False,
) -> None:
    """Same as `translate --from en --to ru`."""
    _translate(source, output, "en", "ru", events, deepseek_key)


_COVER_MODES = ("auto", "embedded-only", "generate", "typographic")


@app.command()
def cover(
    folders: Annotated[list[Path], typer.Argument(help="Book folders made by convert.")],
    mode: Annotated[
        str | None,
        typer.Option("--mode", help="auto | embedded-only | generate | typographic"),
    ] = None,
) -> None:
    """Choose a book's cover again without re-converting it (§6.8.2)."""
    from text2audio import api

    if mode is not None and mode not in _COVER_MODES:
        typer.echo(f"error: --mode must be one of: {', '.join(_COVER_MODES)}", err=True)
        raise typer.Exit(ExitCode.USAGE)
    results = api.cover(folders, mode=mode)
    for r in results:
        if r.error is not None:
            typer.echo(f"FAILED {r.folder}: {r.error}", err=True)
            continue
        note = f" ({r.note})" if r.note else ""
        typer.echo(f"{r.file or r.folder} — {r.origin or 'no cover'}{note}")
        for w in r.warnings:
            typer.echo(f"  warning: {w}")
        if (r.folder / "_work" / "parts.json").is_file():
            typer.echo(f"  run `text2audio synthesize {r.folder}` to put it into the audio files")
    if any(r.error is not None for r in results):
        raise typer.Exit(ExitCode.INPUT)


@envs_app.command("list")
def envs_list() -> None:
    """List plugin environments and whether they are installed."""
    from text2audio import api

    for env in api.plugin_envs():
        state = "installed" if env.installed else f"missing (fix: {env.fix})"
        typer.echo(f"{env.name}\t{state}\t{env.path}")


@envs_app.command("sync")
def envs_sync(
    names: Annotated[list[str] | None, typer.Argument(help="Environments (default: all).")] = None,
) -> None:
    """Create or update plugin environments from their lock files (uses the network)."""
    import subprocess

    from text2audio import api
    from text2audio.core.envs import EnvMissing, UnknownEnv

    try:
        for env in api.envs_sync(names):
            typer.echo(f"{env.name}: {env.path}")
    except UnknownEnv as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ExitCode.USAGE) from exc
    except (EnvMissing, subprocess.CalledProcessError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ExitCode.MISSING_ASSET) from exc


INTERRUPTED = 130  # 128 + SIGINT, as shells report it


def _synthesize_one(
    folder: Path,
    tier: str | None,
    workers: int | None,
    lang: str | None,
    ui: StageProgress,
    qa: str | None = None,
    qa_max: int | None = None,
    deepseek_key: str | None = None,
) -> int:
    """Synthesize one book folder; returns its exit code."""
    from text2audio import api
    from text2audio.audio.encode import FfmpegError
    from text2audio.core.config import load_config, user_config_path
    from text2audio.core.envs import EnvMissing
    from text2audio.core.synthesize import SynthesisError

    overrides: dict[str, Any] = {"synthesis": {"language": lang}} if lang else {}
    if qa:
        overrides["qa"] = {"mode": qa}
    if qa_max is not None:
        overrides.setdefault("qa", {})["max_segments"] = qa_max
    try:
        config = load_config([user_config_path(), folder / "_work" / "text2audio.toml"], overrides)
    except ValueError as exc:  # pydantic's ValidationError is a ValueError
        ui.fail("✗ bad configuration:", str(exc))
        return ExitCode.USAGE
    try:
        result = api.synthesize(
            folder,
            config=config,
            tier=_tier(tier),
            max_workers=workers,
            progress=ui,
            deepseek_key=deepseek_key,
        )
    except KeyboardInterrupt:
        ui.bars.stop()
        ui.print(
            "\n[yellow]Interrupted.[/] Finished segments are saved; "
            "run the same command again to resume."
        )
        raise typer.Exit(INTERRUPTED) from None
    except (SynthesisError, EnvMissing, FfmpegError) as exc:
        ui.fail("✗ failed:", str(exc))
        return (
            ExitCode.MISSING_ASSET if isinstance(exc, EnvMissing | FfmpegError) else ExitCode.INPUT
        )
    r = result.report
    seg = r["segments"]
    rtf = r["synthesis"]["rtf"]
    ui.event(
        "synthesized",
        folder=result.folder,
        files=result.files,
        m4b=result.m4b,
        duration_ms=r["duration_ms"],
        segments=seg["total"],
        rendered_now=seg["rendered_now"],
        failed=result.failed,
        flagged=result.flagged,
        rtf=rtf,
        wall_s=r["wall_s"],
        report=result.folder / "report.md",
    )
    ui.result(
        f"{result.folder}: {len(result.files)} file(s){' + M4B' if result.m4b else ''}, "
        f"{duration(r['duration_ms'] / 1000)} of audio, {seg['total']} segments "
        f"({seg['rendered_now']} rendered now), "
        + (f"RTF {rtf}, " if rtf is not None else "")
        + f"took {duration(r['wall_s'])}; split: {r['split']['reason']}"
    )
    q = r.get("qa", {})
    if q.get("checked"):
        of = f" of {seg['total']}" if q["checked"] < seg["total"] else ""
        ui.print(
            f"  [dim]QA ({escape(str(q['asr']))}): {q['checked']}{of} checked, "
            f"mean WER {q['mean_wer']}, {q['flagged']} flagged "
            f"({q['flagged_share']:.1%})[/]"
        )
    stages = sorted(
        ((k, v) for k, v in r.get("timings_s", {}).items() if k != "total"), key=lambda kv: -kv[1]
    )
    if stages:
        ui.print("  [dim]time: " + ", ".join(f"{k} {duration(v)}" for k, v in stages[:4]) + "[/]")
    if result.flagged:
        ui.warn(f"{result.flagged} segment(s) flagged — see {result.folder / 'report.json'}")
        return ExitCode.QA_WARNINGS
    return ExitCode.OK


TierFlag = Annotated[str | None, typer.Option("--tier", help="fast | expressive (default: auto).")]
WorkersFlag = Annotated[int | None, typer.Option("--workers", help="Max parallel TTS workers.")]
QaFlag = Annotated[
    str | None,
    typer.Option("--qa", help="QA gate: on | off | sample=N% (default: on, or the config)."),
]
QaMaxFlag = Annotated[
    int | None,
    typer.Option("--qa-max", help="Segments the QA gate checks per run (default 100, 0 = all)."),
]


@app.command()
def synthesize(
    folders: Annotated[list[Path], typer.Argument(help="Book folders made by `convert`.")],
    tier: TierFlag = None,
    workers: WorkersFlag = None,
    lang: Annotated[str | None, typer.Option("--lang", help="Book language override.")] = None,
    qa: QaFlag = None,
    qa_max: QaMaxFlag = None,
    deepseek_key: DeepSeekKeyFlag = None,
    events: EventsOpt = False,
) -> None:
    """Render book folders to audio; only changed segments are synthesized again."""
    codes: list[int] = []
    with StageProgress(events=events) as ui:
        for n, folder in enumerate(folders, 1):
            ui.book(folder.name, folder, n, len(folders))
            codes.append(_synthesize_one(folder, tier, workers, lang, ui, qa, qa_max, deepseek_key))
    raise typer.Exit(max(codes, default=ExitCode.OK))


@app.command()
def run(
    inputs: Annotated[list[Path], typer.Argument(help="Book files or folders of books.")],
    output: Annotated[
        Path | None, typer.Option("-o", "--output", help="Output root (default ./output).")
    ] = None,
    name: Annotated[str | None, typer.Option("--name", help="Book folder name.")] = None,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Reuse a clashing folder name.")
    ] = False,
    lang: Annotated[str | None, typer.Option("--lang", help="Language of TXT/MD input.")] = None,
    tier: TierFlag = None,
    workers: WorkersFlag = None,
    qa: QaFlag = None,
    qa_max: QaMaxFlag = None,
    deepseek_key: DeepSeekKeyFlag = None,
    events: EventsOpt = False,
) -> None:
    """Convert and synthesize in one step, one folder per book."""
    from text2audio import api

    codes: list[int] = []
    with StageProgress(events=events) as ui:
        started = time.monotonic()
        try:
            with ui.console.status(f"Converting {len(inputs)} input(s)…"):
                results = api.convert(inputs, output, name=name, overwrite=overwrite, lang=lang)
        except ValueError as exc:
            ui.fail("error:", str(exc), indent="")
            raise typer.Exit(ExitCode.USAGE) from exc
        # convert works on the whole batch, so a time per book is known only for one book
        took = time.monotonic() - started if len(results) == 1 else None
        for n, r in enumerate(results, 1):
            ui.book(r.input.stem if r.folder is None else r.folder.name, r.input, n, len(results))
            if r.status == "failed" or r.folder is None:
                ui.fail("✗ failed:", str(r.error))
                codes.append(ExitCode.INTERNAL if r.internal else ExitCode.INPUT)
                continue
            ui.event("converted", **_converted(r))
            ui.stage(
                "convert",
                f"{r.format} · {r.chapters} chapter(s) · {r.segments} segments"
                + {
                    "converted": "",
                    "unchanged": " · unchanged",
                    "kept-edits": " · your edits kept, new text in the .reconverted.md",
                }.get(r.status, ""),
                took,
            )
            for w in r.warnings:
                ui.warn(w)
            codes.append(
                _synthesize_one(r.folder, tier, workers, lang, ui, qa, qa_max, deepseek_key)
            )
    if len(results) > 1 and not events:
        ok = sum(c in (ExitCode.OK, ExitCode.QA_WARNINGS) for c in codes)
        typer.echo(f"{ok} of {len(results)} book(s) done")
    raise typer.Exit(max(codes, default=ExitCode.INPUT))


def _revoice_media(source: Path, output: Path | None, source_lang: str, target: str | None) -> None:
    from text2audio import api
    from text2audio.audio.encode import FfmpegError
    from text2audio.core.envs import EnvMissing
    from text2audio.core.revoice import RevoiceError
    from text2audio.core.synthesize import SynthesisError
    from text2audio.core.translate import TranslateError
    from text2audio.workers.process import WorkerError

    with StageProgress() as ui:
        try:
            r = api.revoice(
                source, output, source_lang=source_lang, target_lang=target, progress=ui
            )
        except (RevoiceError, TranslateError, SynthesisError) as exc:
            ui.print(f"[red]error:[/] {escape(str(exc))}")
            raise typer.Exit(ExitCode.INPUT) from exc
        except (EnvMissing, WorkerError, FfmpegError) as exc:
            ui.print(f"[red]error:[/] {escape(str(exc))}")
            raise typer.Exit(ExitCode.MISSING_ASSET) from exc
        ui.result(
            f"{r.output} — {r.source_lang} → {r.target_lang} by {r.translator}: "
            f"{r.phrases} phrases, {duration(r.duration_s)}"
            + (f"; subtitles {r.subtitles.name}" if r.subtitles else "")
        )
        for w in r.stats.warnings:
            ui.warn(w)
        if r.late:
            more = f" (+{len(r.late) - 5} more)" if len(r.late) > 5 else ""
            ui.warn(
                f"{plural(len(r.late), 'phrase')} over 1 s behind the original: "
                + ", ".join(r.late[:5])
                + more
            )


@app.command()
def revoice(
    source: Annotated[Path, typer.Argument(help="A book, Markdown, audio or video file.")],
    output: Annotated[
        Path | None,
        typer.Option(
            "-o",
            "--output",
            help="Audio/video: the output file (default <name>.<lang>.<ext>). "
            "Books: the output root (default ./output).",
        ),
    ] = None,
    to: Annotated[
        str | None,
        typer.Option("--to", help="Target language (default: the first the source has)."),
    ] = None,
    source_lang: Annotated[str, typer.Option("--from", help="Language spoken or written.")] = "en",
    tier: TierFlag = None,
    workers: WorkersFlag = None,
    qa: QaFlag = None,
) -> None:
    """Re-voice in another language: a book → its translation and audiobook, audio →
    audio, video → the same video with a track timed to the original."""
    from text2audio.core.revoice import media_kind

    if media_kind(source) is not None:
        _revoice_media(source, output, source_lang, to)
        return
    text = source
    if source.is_file() and source.suffix.lower() != ".md":
        from text2audio import api

        converted = api.convert([source], output, lang=source_lang)[0]
        if converted.folder is None:
            typer.echo(f"error: {converted.error}", err=True)
            raise typer.Exit(ExitCode.INTERNAL if converted.internal else ExitCode.INPUT)
        text = converted.folder
    translated = _translate(text, None, source_lang, to)
    run([translated], output, None, False, None, tier, workers, qa, None)


@app.command()
def preview(
    folder: Annotated[Path, typer.Argument(help="Book folder made by `convert`.")],
    chapter: Annotated[
        int | None, typer.Option("--chapter", help="Chapter number (default: the first).")
    ] = None,
    seconds: Annotated[float, typer.Option("--seconds", help="Approximate length.")] = 30,
    tier: TierFlag = None,
    workers: WorkersFlag = None,
    deepseek_key: DeepSeekKeyFlag = None,
    events: EventsOpt = False,
) -> None:
    """Render a short sample of a chapter to audition voice and tier (no QA)."""
    from text2audio import api
    from text2audio.audio.encode import FfmpegError
    from text2audio.core.config import load_config, user_config_path
    from text2audio.core.envs import EnvMissing
    from text2audio.core.synthesize import SynthesisError

    with StageProgress(events=events) as ui:
        ui.book(folder.name, folder)
        try:
            config = load_config([user_config_path(), folder / "_work" / "text2audio.toml"])
        except ValueError as exc:
            ui.fail("✗ bad configuration:", str(exc))
            raise typer.Exit(ExitCode.USAGE) from exc
        try:
            out = api.preview(
                folder,
                chapter=chapter,
                seconds=seconds,
                config=config,
                tier=_tier(tier),
                max_workers=workers,
                progress=ui,
                deepseek_key=deepseek_key,
            )
        except (SynthesisError, EnvMissing, FfmpegError) as exc:
            ui.fail("✗ failed:", str(exc))
            code = (
                ExitCode.MISSING_ASSET
                if isinstance(exc, EnvMissing | FfmpegError)
                else ExitCode.INPUT
            )
            raise typer.Exit(code) from exc
        ui.event("preview", path=out)
        ui.result(str(out))


@config_app.command("show")
def config_show(
    folder: Annotated[Path, typer.Argument(help="Book folder made by `convert`.")],
    lang: Annotated[str | None, typer.Option("--lang", help="Book language override.")] = None,
    tier: TierFlag = None,
) -> None:
    """Print the effective settings of a book with the source of each value."""
    from text2audio import api
    from text2audio.core.config import load_config, user_config_path
    from text2audio.core.effective import render

    if not folder.is_dir():
        typer.echo(f"error: not a book folder: {folder}", err=True)
        raise typer.Exit(ExitCode.INPUT)
    overrides: dict[str, Any] = {"synthesis": {}}
    if lang:
        overrides["synthesis"]["language"] = lang
    if tier:
        overrides["synthesis"]["tier"] = _tier(tier)
    try:
        load_config([user_config_path(), folder / "_work" / "text2audio.toml"], overrides)
    except ValueError as exc:  # pydantic's ValidationError and TOMLDecodeError are ValueErrors
        typer.echo(f"error: bad configuration: {exc}", err=True)
        raise typer.Exit(ExitCode.USAGE) from exc
    typer.echo(render(api.config_show(folder, overrides)), nl=False)


@app.command()
def voices(
    lang: Annotated[str | None, typer.Option("--lang", help="Language code.")] = None,
) -> None:
    """List voice roles per language pack and the voices of installed backends."""
    from text2audio import api

    found = api.voices(lang)
    for code, roles in found.roles.items():
        typer.echo(f"{code}:")
        for role, ids in roles.items():
            typer.echo(f"  {role}: " + ", ".join(f"{b} {v}" for b, v in ids.items()))
    typer.echo("backends:")
    for name, voice_ids in found.backends.items():
        typer.echo(f"  {name}: {', '.join(voice_ids) or '-'}")


def main() -> None:
    app()
