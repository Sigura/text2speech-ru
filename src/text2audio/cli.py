"""Command-line interface (SPEC §6.11)."""

from __future__ import annotations

import json
from dataclasses import asdict
from enum import IntEnum
from pathlib import Path
from typing import Annotated, Any

import typer

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
        typer.echo(f"{r.markdown}{note} — {r.format}, {r.chapters} chapter(s)")
        for w in r.warnings:
            typer.echo(f"  warning: {w}")
    failed = [r for r in results if r.status == "failed"]
    if len(results) > 1:
        typer.echo(f"{len(results) - len(failed)} of {len(results)} book(s) converted")
    if any(r.internal for r in failed):
        raise typer.Exit(ExitCode.INTERNAL)
    if failed or not results:
        raise typer.Exit(ExitCode.INPUT)


_PLANNED = {
    "synthesize": "M2",
    "run": "M2",
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
