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


class Pool:
    """Workers loaded once and reused by several `run` calls (polish and QA batches)."""

    def __init__(self, workers: list[Any], load: dict[str, Any] | None = None) -> None:
        self.workers = workers
        self.load = load or {}
        self.loaded = False

    def run(
        self,
        items: list[Any],
        work: Callable[[Any, Any], None],
        on_error: Callable[[Any, Exception], None] | None = None,
    ) -> None:
        """Run `work(worker, item)` for every item; a worker that fails is dropped."""
        if items and self.loaded and not self.workers:
            raise SynthesisError("every worker of this pool has failed")
        todo: queue.Queue[Any] = queue.Queue()
        for item in items:
            todo.put(item)
        failed: list[tuple[Any, BaseException]] = []
        lock = threading.Lock()

        def run(worker: Any) -> None:
            try:
                if not self.loaded:
                    worker.load(**self.load)
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
                with lock:
                    failed.append((worker, exc))

        threads = [threading.Thread(target=run, args=(w,), daemon=True) for w in self.workers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.loaded = True
        for worker, _ in failed:
            worker.close()
        dead = {id(w) for w, _ in failed}
        self.workers = [w for w in self.workers if id(w) not in dead]
        if failed and (todo.qsize() or not self.workers):
            raise SynthesisError(f"workers failed: {failed[0][1]}") from failed[0][1]

    def close(self) -> None:
        for worker in self.workers:
            worker.close()
        self.workers = []


def parallel(
    workers: list[Any],
    items: list[Any],
    work: Callable[[Any, Any], None],
    load: dict[str, Any] | None = None,
    on_error: Callable[[Any, Exception], None] | None = None,
) -> None:
    """Run `work(worker, item)` for every item on a pool of loaded workers."""
    pool = Pool(workers, load)
    try:
        pool.run(items, work, on_error)
    finally:
        pool.close()
