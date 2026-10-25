"""Pools of plugin workers shared by the synthesis stages (stress, render, QA)."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any

from text2audio.core import resources as res
from text2audio.core.contracts import Capabilities
from text2audio.core.errors import SynthesisError
from text2audio.workers.process import WorkerError


def cpu_slots(threads: int, ram_mb: int, count: int) -> list[res.WorkerSlot]:
    caps = Capabilities(languages=("*",), threads=threads, ram_mb=ram_mb)
    plan = res.plan(res.probe(), caps, tier="fast")
    return plan.slots[: max(1, count)]


def parallel(
    workers: list[Any],
    items: list[Any],
    work: Callable[[Any, Any], None],
    load: dict[str, Any] | None = None,
    on_error: Callable[[Any, Exception], None] | None = None,
) -> None:
    """Run `work(worker, item)` for every item on a pool of loaded workers."""
    todo: queue.Queue[Any] = queue.Queue()
    for item in items:
        todo.put(item)
    errors: list[BaseException] = []

    def run(worker: Any) -> None:
        try:
            worker.load(**(load or {}))
            while True:
                try:
                    item = todo.get_nowait()
                except queue.Empty:
                    return
                try:
                    work(worker, item)
                except WorkerError as exc:
                    if on_error is None:
                        raise
                    on_error(item, exc)
        except BaseException as exc:
            errors.append(exc)
        finally:
            worker.close()

    threads = [threading.Thread(target=run, args=(w,), daemon=True) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors and (todo.qsize() or len(errors) == len(workers)):
        raise SynthesisError(f"workers failed: {errors[0]}") from errors[0]
