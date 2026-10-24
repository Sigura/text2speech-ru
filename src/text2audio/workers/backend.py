"""`WorkerBackend`: implements `Backend` for a descriptor whose model runs in a worker."""

from __future__ import annotations

import sys
from pathlib import Path

from text2audio.core.contracts import (
    BackendDescriptor,
    Capabilities,
    ModelAsset,
    Segment,
    SynthesisResult,
)
from text2audio.core.resources import WorkerSlot
from text2audio.workers import protocol
from text2audio.workers.process import WorkerCrashed, WorkerProcess


class WorkerBackend:
    """Starts the worker on `load`, restarts it once if it crashes mid-request.

    `python` is the backend environment's interpreter. Until per-backend
    environments are installed (M2), it defaults to the core's interpreter.
    """

    def __init__(
        self,
        descriptor: BackendDescriptor,
        store: Path,
        slot: WorkerSlot | None = None,
        python: str | None = None,
        max_restarts: int = 1,
    ) -> None:
        self.descriptor = descriptor
        self.name = descriptor.name
        self.capabilities: Capabilities = descriptor.capabilities
        self.store = store
        self.slot = slot or WorkerSlot("cpu", descriptor.capabilities.threads)
        self.max_restarts = max_restarts
        self.restarts = 0
        self.sample_rate = descriptor.capabilities.sample_rate
        self._device = self.slot.worker_device
        self._proc = WorkerProcess(
            descriptor.worker_command(python or sys.executable),
            env={**self.slot.env(), "T2A_MODEL_HOME": str(store)},
            cpu_affinity=self.slot.cpu_affinity,
        )

    def assets(self) -> list[ModelAsset]:
        return self.descriptor.assets()

    def load(self, device: str | None = None) -> None:
        self._device = device or self._device
        self._proc.start()
        reply = self._proc.request(
            protocol.request("load", device=self._device, store=str(self.store))
        )
        self.sample_rate = int(reply.get("sample_rate", self.sample_rate))

    def synthesize(self, seg: Segment, out_path: Path) -> SynthesisResult:
        msg = protocol.request("synthesize", segment=seg.model_dump(mode="json"), out=str(out_path))
        while True:
            try:
                reply = self._proc.request(msg)
                break
            except WorkerCrashed:
                if self.restarts >= self.max_restarts:
                    raise
                self.restarts += 1
                self.load()
        return SynthesisResult(
            duration_ms=int(reply["duration_ms"]),
            elapsed_ms=int(reply.get("elapsed_ms", 0)),
            sample_rate=self.sample_rate,
        )

    def close(self) -> None:
        self._proc.close()

    def __enter__(self) -> WorkerBackend:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
