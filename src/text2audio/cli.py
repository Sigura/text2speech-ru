"""Command-line interface (SPEC §6.11)."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from enum import IntEnum
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.markup import escape

from text2audio.console import StageProgress, duration
from text2audio.core.offline import enforce_offline
from text2audio.models.store import default_store


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

LangOpt = Annotated[str | None, typer.Option("--lang", help="Language code(s), e.g. ru or ru,en.")]
TierOpt = Annotated[str | None, typer.Option("--tier", help="fast | expressive")]
JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]
LockOpt = Annotated[Path | None, typer.Option("--lock", help="Path to models.lock.")]


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
def doctor(json_out: JsonOpt = False, lock: LockOpt = None) -> None:
    """Check tools, hardware, models and plugins; exit 3 if something is missing."""
    from text2audio import api

    report = api.doctor(lock=lock)
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
def models_list(lang: LangOpt = None, tier: TierOpt = None, lock: LockOpt = None) -> None:
    """List pinned assets from models.lock."""
    from text2audio import api

    for a in api.models_list(lang, _tier(tier), lock):
        typer.echo(f"{a.owner}\t{a.source}\t{len(a.files)} file(s)\t{a.license}")


@models_app.command("verify")
def models_verify(lang: LangOpt = None, tier: TierOpt = None, lock: LockOpt = None) -> None:
    """Verify SHA-256 of every pinned asset in the store."""
    from text2audio import api

    problems = api.models_verify(lang, _tier(tier), lock=lock)
    for p in problems:
        typer.echo(str(p), err=True)
    if problems:
        raise typer.Exit(ExitCode.MISSING_ASSET)
    typer.echo("all assets verified")


@models_app.command("pull")
def models_pull(lang: LangOpt = None, tier: TierOpt = None, lock: LockOpt = None) -> None:
    """Download pinned assets (uses the network)."""
    from text2audio import api
    from text2audio.models.pull import PullError

    try:
        fetched = api.models_pull(lang, _tier(tier), lock=lock)
    except PullError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(ExitCode.MISSING_ASSET) from exc
    typer.echo(f"{len(fetched)} file(s) downloaded")


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
) -> None:
    """Convert books to editable Markdown, one folder per book."""
    from text2audio import api

    try:
        results = api.convert(inputs, output, name=name, overwrite=overwrite, reconvert=reconvert)
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(ExitCode.USAGE) from exc
    for r in results:
        if r.status == "failed":
            typer.echo(f"FAILED {r.input}: {r.error}", err=True)
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
    if len(results) > 1:
        typer.echo(f"{len(results) - len(failed)} of {len(results)} book(s) converted")
    if any(r.internal for r in failed):
        raise typer.Exit(ExitCode.INTERNAL)
    if failed or not results:
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
    from text2audio.core.envs import EnvMissing

    try:
        for env in api.envs_sync(names):
            typer.echo(f"{env.name}: {env.path}")
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
) -> int:
    """Synthesize one book folder; returns its exit code."""
    from text2audio import api
    from text2audio.audio.encode import FfmpegError
    from text2audio.core.config import load_config, user_config_path
    from text2audio.core.envs import EnvMissing
    from text2audio.core.synthesize import SynthesisError

    overrides = {"synthesis": {"language": lang}} if lang else {}
    config = load_config([user_config_path(), folder / "_work" / "text2audio.toml"], overrides)
    try:
        result = api.synthesize(
            folder, config=config, tier=_tier(tier), max_workers=workers, progress=ui
        )
    except KeyboardInterrupt:
        ui.bars.stop()
        ui.print(
            "\n[yellow]Interrupted.[/] Finished segments are saved; "
            "run the same command again to resume."
        )
        raise typer.Exit(INTERRUPTED) from None
    except (SynthesisError, EnvMissing, FfmpegError) as exc:
        ui.print(f"  [red]✗ failed:[/] {escape(str(exc))}")
        return (
            ExitCode.MISSING_ASSET if isinstance(exc, EnvMissing | FfmpegError) else ExitCode.INPUT
        )
    r = result.report
    seg = r["segments"]
    rtf = r["synthesis"]["rtf"]
    ui.result(
        f"{result.folder}: {len(result.files)} file(s), "
        f"{duration(r['duration_ms'] / 1000)} of audio, {seg['total']} segments "
        f"({seg['rendered_now']} rendered now), "
        + (f"RTF {rtf}, " if rtf is not None else "")
        + f"took {duration(r['wall_s'])}; split: {r['split']['reason']}"
    )
    if result.flagged:
        ui.warn(f"{result.flagged} segment(s) flagged — see {result.folder / 'report.json'}")
        return ExitCode.QA_WARNINGS
    return ExitCode.OK


TierFlag = Annotated[str | None, typer.Option("--tier", help="fast | expressive (default: auto).")]
WorkersFlag = Annotated[int | None, typer.Option("--workers", help="Max parallel TTS workers.")]


@app.command()
def synthesize(
    folders: Annotated[list[Path], typer.Argument(help="Book folders made by `convert`.")],
    tier: TierFlag = None,
    workers: WorkersFlag = None,
    lang: Annotated[str | None, typer.Option("--lang", help="Book language override.")] = None,
) -> None:
    """Render book folders to audio; only changed segments are synthesized again."""
    codes: list[int] = []
    with StageProgress() as ui:
        for n, folder in enumerate(folders, 1):
            ui.book(folder.name, folder, n, len(folders))
            codes.append(_synthesize_one(folder, tier, workers, lang, ui))
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
) -> None:
    """Convert and synthesize in one step, one folder per book."""
    from text2audio import api

    codes: list[int] = []
    with StageProgress() as ui:
        started = time.monotonic()
        try:
            with ui.console.status(f"Converting {len(inputs)} input(s)…"):
                results = api.convert(inputs, output, name=name, overwrite=overwrite, lang=lang)
        except ValueError as exc:
            ui.print(f"[red]error:[/] {escape(str(exc))}")
            raise typer.Exit(ExitCode.USAGE) from exc
        # convert works on the whole batch, so a time per book is known only for one book
        took = time.monotonic() - started if len(results) == 1 else None
        for n, r in enumerate(results, 1):
            ui.book(r.input.stem if r.folder is None else r.folder.name, r.input, n, len(results))
            if r.status == "failed" or r.folder is None:
                ui.print(f"  [red]✗ failed:[/] {escape(str(r.error))}")
                codes.append(ExitCode.INTERNAL if r.internal else ExitCode.INPUT)
                continue
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
            codes.append(_synthesize_one(r.folder, tier, workers, lang, ui))
    if len(results) > 1:
        ok = sum(c in (ExitCode.OK, ExitCode.QA_WARNINGS) for c in codes)
        typer.echo(f"{ok} of {len(results)} book(s) done")
    raise typer.Exit(max(codes, default=ExitCode.INPUT))


_PLANNED = {
    "preview": "M2",
    "voices": "M2",
    "bundle": "M5",
}


def _planned(name: str, milestone: str) -> None:
    def command() -> None:
        typer.echo(f"`{name}` is not implemented yet (planned for {milestone}).", err=True)
        raise typer.Exit(ExitCode.USAGE)

    command.__doc__ = f"Not implemented yet ({milestone})."
    settings = {"ignore_unknown_options": True, "allow_extra_args": True}
    app.command(name, context_settings=settings)(command)


for _name, _milestone in _PLANNED.items():
    _planned(_name, _milestone)


def main() -> None:
    app()
