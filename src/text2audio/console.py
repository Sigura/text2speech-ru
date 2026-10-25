"""Terminal progress for `run` / `synthesize` (FR-17).

Layout, per book: a header, the plan (language, tier, backend, voice), then one ✓ line
per finished stage with its numbers and time, and a live bar for the running stage
(count, rate, elapsed, ETA). Progress goes to stderr so stdout keeps only results;
without a terminal there are no bars, just the stage lines, so logs stay readable.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    ProgressColumn,
    SpinnerColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
)
from rich.text import Text

LABELS = {
    "convert": "Convert",
    "normalize": "Normalize",
    "stress": "Stress",
    "synthesize": "Synthesize",
    "assemble": "Encode",
}
WIDTH = max(len(v) for v in LABELS.values())
# What a running stage is waiting for before its first item is done.
STARTING = {"stress": "loading stress model", "synthesize": "loading voices"}


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' * (n != 1)}"


def duration(seconds: float) -> str:
    """Short human duration: 0.8s, 41s, 6m 12s, 4h 18m."""
    if seconds < 10:
        return f"{seconds:.1f}s"
    s = round(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    return f"{s // 3600}h {s % 3600 // 60:02d}m"


class _Eta(ProgressColumn):
    """Remaining time once there is a rate to go by; blank while workers load."""

    def render(self, task: Task) -> Text:
        if task.finished or not task.completed or task.time_remaining is None:
            return Text("")
        return Text(f"eta {duration(task.time_remaining)}", style="progress.remaining")


class StageProgress:
    """A `Progress` callable for `api.synthesize` (see `core.synthesize.Progress`)."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(stderr=True, highlight=False)
        self.live = self.console.is_terminal and not self.console.is_dumb_terminal
        self.bars = Progress(
            SpinnerColumn(),
            TextColumn(f"[bold]{{task.description:<{WIDTH}}}[/]"),
            BarColumn(bar_width=32),
            MofNCompleteColumn(),
            TextColumn("[dim]{task.fields[note]}[/]"),
            TimeElapsedColumn(),
            _Eta(),
            console=self.console,
            transient=True,
        )
        self.tasks: dict[str, TaskID] = {}
        self.started: dict[str, float] = {}
        self.facts: dict[str, dict[str, Any]] = {}
        self.last: dict[str, int] = {}
        self.books = 0

    # ----------------------------------------------------------------- lifecycle

    def __enter__(self) -> StageProgress:
        if self.live:
            self.bars.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.bars.stop()

    def print(self, message: str = "") -> None:
        """A line above the bars (rich markup)."""
        self.console.print(message, soft_wrap=True)  # never break paths or errors

    def result(self, line: str) -> None:
        """A result line for stdout; on a shared terminal it goes above the bars."""
        if self.live and sys.stdout.isatty():
            self.console.print(escape(line), soft_wrap=True)
        else:
            print(line, flush=True)

    def book(self, title: str, path: object, index: int = 0, count: int = 0) -> None:
        gap = "\n" if self.books else ""
        self.books += 1
        self.facts.clear()
        self.last.clear()
        prefix = f"[dim][{index}/{count}][/] " if count > 1 else ""
        self.print(f"{gap}{prefix}[bold]{escape(title)}[/]  [dim]{escape(str(path))}[/]")

    def stage(self, stage: str, summary: str, seconds: float | None = None) -> None:
        """A finished stage: ✓ label, its numbers and the time it took."""
        label = LABELS.get(stage, stage)
        took = f"  [dim]{duration(seconds)}[/]" if seconds is not None else ""
        self.print(f"  [green]✓[/] {label:<{WIDTH}}  {summary}{took}")

    def warn(self, message: str) -> None:
        self.print(f"  [yellow]![/] {escape(message)}")

    # ------------------------------------------------------------------ events

    def __call__(self, stage: str, done: int, total: int) -> None:
        self.last[stage] = done
        if stage not in self.tasks:
            self.started[stage] = time.monotonic()
            self.tasks[stage] = self.bars.add_task(
                LABELS.get(stage, stage), total=total, note=self._note(stage, done)
            )
        self.bars.update(
            self.tasks[stage], completed=done, total=total, note=self._note(stage, done)
        )
        if done >= total:
            self._finish(stage)

    def detail(self, stage: str, **fields: Any) -> None:
        facts = self.facts.setdefault(stage, {})
        facts.update(fields)
        if stage == "plan":
            self._plan(facts)
        elif "failed" in fields:
            self.warn(f"segment {fields['failed']} failed: {fields['error']}")
        elif stage == "stress" and "cached" in fields and fields["cached"] == fields["texts"]:
            self.stage(stage, f"{fields['texts']} texts, all cached")
        elif stage == "synthesize" and fields.get("pending") == 0:
            self.stage(stage, f"{fields['segments']} segments, all cached")
        elif stage in self.tasks:
            self.bars.update(self.tasks[stage], note=self._note(stage, self.last.get(stage, 0)))

    def _plan(self, facts: dict[str, Any]) -> None:
        voice = f" · voice {escape(str(facts['voice']))}" if facts.get("voice") else ""
        self.print(
            f"  [dim]{facts['lang']} · {facts['tier']} tier · "
            f"{escape(str(facts['backend']))}{voice}[/]"
        )
        for note in facts.get("notes") or []:
            self.print(f"  [dim]  {escape(str(note))}[/]")

    def _note(self, stage: str, done: int) -> str:
        """The live text next to a running bar."""
        f = self.facts.get(stage, {})
        if not done and stage in STARTING:
            return f"{STARTING[stage]} ({plural(f.get('workers', 1), 'worker')})…"
        if stage == "synthesize" and f.get("audio_ms"):
            parts = [f"{duration(f['audio_ms'] / 1000)} audio"]
            took = time.monotonic() - self.started.get(stage, time.monotonic())
            if took > 1:
                parts.append(f"{f['audio_ms'] / 1000 / took:.1f}× realtime")
            return " · ".join(parts)
        if stage == "assemble" and f.get("current"):
            return escape(str(f["current"]))
        return ""

    def _finish(self, stage: str) -> None:
        task = self.tasks.pop(stage)
        self.bars.remove_task(task)
        self.bars.refresh()
        took = time.monotonic() - self.started.pop(stage)
        self.stage(stage, self._summary(stage), took)

    def _summary(self, stage: str) -> str:
        f = self.facts.get(stage, {})
        done = self.last.get(stage, 0)
        if stage == "normalize":
            unsure = f.get("uncertain")
            extra = f" · {plural(unsure, 'uncertain span')}" if unsure else ""
            return f"{plural(done, 'segment')}{extra}"
        if stage == "stress":
            cached = f" · {f['cached']} cached" if f.get("cached") else ""
            return f"{done} new{cached} · {plural(f.get('workers', 1), 'worker')}"
        if stage == "synthesize":
            cached = f.get("segments", done) - f.get("pending", done)
            parts = [f"{done} rendered"]
            if cached:
                parts.append(f"{cached} cached")
            if f.get("audio_ms"):
                parts.append(f"{duration(f['audio_ms'] / 1000)} audio")
            parts.append(plural(f.get("workers", 1), "worker"))
            return " · ".join(parts)
        if stage == "assemble":
            encoded = f.get("encoded", 0)
            files = plural(done, "file")
            if encoded == done:
                return files
            return f"{files} · unchanged" if not encoded else f"{files} · {encoded} re-encoded"
        return str(done)
