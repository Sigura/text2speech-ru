"""Worker clients: `Worker` for any plugin worker, `WorkerBackend` implements `Backend`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from text2audio.core.contracts import (
    BackendDescriptor,
    Capabilities,
    ModelAsset,
    Segment,
    SynthesisResult,
)
from text2audio.core.envs import python_for
from text2audio.core.resources import WorkerSlot
from text2audio.workers import protocol
from text2audio.workers.process import WorkerCrashed, WorkerProcess

# Put on every worker's PYTHONPATH: the stdlib-only protocol runtime (`t2a_worker`).
RUNTIME_DIR = Path(__file__).parent / "stdlib"


def worker_env(
    store: Path, slot: WorkerSlot, extra: dict[str, str] | None = None
) -> dict[str, str]:
    return {
        **slot.env(),
        "T2A_MODEL_HOME": str(store),
        "T2A_NO_NETWORK": "1",
        "PYTHONPATH": str(RUNTIME_DIR),
        "PYTHONUNBUFFERED": "1",
        **(extra or {}),
    }


class Worker:
    """A plugin worker: started on `load`, restarted once if it crashes mid-request.

    `python` defaults to the plugin's own uv environment (§6.6.3), or the core's
    interpreter for plugins that declare none (the M0 `sine` fixture).
    """

    def __init__(
        self,
        plugin: Any,
        store: Path,
        slot: WorkerSlot | None = None,
        python: str | None = None,
        max_restarts: int = 1,
        env: dict[str, str] | None = None,
        request_timeout: float = 600.0,
    ) -> None:
        self.plugin = plugin
        self.name: str = plugin.name
        self.store = store
        caps = getattr(plugin, "capabilities", None)
        self.slot = slot or WorkerSlot("cpu", caps.threads if caps else 4)
        self.max_restarts = max_restarts
        self.restarts = 0
        self.load_reply: dict[str, Any] = {}
        self._load_fields: dict[str, Any] = {}
        self._device = self.slot.worker_device
        self._proc = WorkerProcess(
            plugin.worker_command(python or python_for(plugin)),
            env=worker_env(store, self.slot, env),
            cpu_affinity=self.slot.cpu_affinity,
            request_timeout=request_timeout,
        )

    @property
    def device(self) -> str:
        return self._device

    def load(self, device: str | None = None, **fields: Any) -> dict[str, Any]:
        self._device = device or self._device
        self._load_fields = fields
        self._proc.start()
        self.load_reply = self._proc.request(
            protocol.request("load", device=self._device, store=str(self.store), **fields)
        )
        return self.load_reply

    def call(self, op: str, **fields: Any) -> dict[str, Any]:
        msg = protocol.request(op, **fields)
        while True:
            try:
                return self._proc.request(msg)
            except WorkerCrashed:
                if self.restarts >= self.max_restarts:
                    raise
                self.restarts += 1
                self.load(**self._load_fields)

    def close(self) -> None:
        self._proc.close()

    def __enter__(self) -> Worker:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class WorkerBackend(Worker):
    """Implements `Backend` for a descriptor whose model runs in a worker."""

    def __init__(
        self,
        descriptor: BackendDescriptor,
        store: Path,
        slot: WorkerSlot | None = None,
        python: str | None = None,
        max_restarts: int = 1,
        env: dict[str, str] | None = None,
    ) -> None:
        super().__init__(descriptor, store, slot, python, max_restarts, env)
        self.descriptor = descriptor
        self.capabilities: Capabilities = descriptor.capabilities
        self.sample_rate = descriptor.capabilities.sample_rate

    def assets(self) -> list[ModelAsset]:
        return self.descriptor.assets()

    def load(self, device: str | None = None, **fields: Any) -> dict[str, Any]:
        reply = super().load(device, **fields)
        self.sample_rate = int(reply.get("sample_rate", self.sample_rate))
        return reply

    def synthesize(self, seg: Segment, out_path: Path) -> SynthesisResult:
        reply = self.call("synthesize", segment=seg.model_dump(mode="json"), out=str(out_path))
        return SynthesisResult(
            duration_ms=int(reply["duration_ms"]),
            elapsed_ms=int(reply.get("elapsed_ms", 0)),
            sample_rate=self.sample_rate,
        )

    def __enter__(self) -> WorkerBackend:
        return self
