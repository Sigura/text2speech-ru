"""One long-lived worker process with start-up/request timeouts and crash detection."""

from __future__ import annotations

import os
import queue
import subprocess
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import IO

from text2audio.workers import protocol
from text2audio.workers.protocol import Message


class WorkerError(Exception):
    pass


class WorkerEnvMissing(WorkerError):
    pass


class WorkerCrashed(WorkerError):
    pass


class WorkerTimeout(WorkerError):
    pass


class WorkerRequestError(WorkerError):
    """The worker answered with {"ok": false}."""


class WorkerProcess:
    def __init__(
        self,
        cmd: list[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        cpu_affinity: frozenset[int] | None = None,
        start_timeout: float = 120.0,
        request_timeout: float = 600.0,
    ) -> None:
        self.cmd = cmd
        self.env = dict(env or {})
        self.cwd = cwd
        self.cpu_affinity = cpu_affinity
        self.start_timeout = start_timeout
        self.request_timeout = request_timeout
        self.ready: Message = {}
        self._proc: subprocess.Popen[str] | None = None
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._stderr: IO[str] | None = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> Message:
        self.close()
        self._lines = queue.Queue()
        self._stderr = tempfile.TemporaryFile("w+", encoding="utf-8")  # noqa: SIM115 — lives as long as the worker
        try:
            self._proc = subprocess.Popen(
                self.cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env={**os.environ, **self.env},
                cwd=self.cwd,
            )
        except FileNotFoundError as exc:
            raise WorkerEnvMissing(
                f"cannot start worker {self.cmd[0]!r}: {exc.strerror}; "
                "is the backend environment installed?"
            ) from exc
        if self.cpu_affinity:
            os.sched_setaffinity(self._proc.pid, self.cpu_affinity)
        threading.Thread(target=self._pump, args=(self._proc,), daemon=True).start()
        self.ready = self._read(self.start_timeout, "start-up")
        protocol.check_ready(self.ready)
        return self.ready

    def _pump(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _stderr_tail(self, limit: int = 2000) -> str:
        if self._stderr is None:
            return ""
        self._stderr.seek(0)
        return self._stderr.read()[-limit:].strip()

    def _read(self, timeout: float, what: str) -> Message:
        try:
            line = self._lines.get(timeout=timeout)
        except queue.Empty:
            self.kill()
            raise WorkerTimeout(f"worker {what} timed out after {timeout:g} s") from None
        if line is None:
            code = self._proc.wait() if self._proc else None
            raise WorkerCrashed(
                f"worker exited during {what} (exit code {code}): {self._stderr_tail()}"
            )
        return protocol.decode(line)

    def request(self, msg: Message) -> Message:
        if self._proc is None or self._proc.stdin is None:
            raise WorkerError("worker not started")
        try:
            self._proc.stdin.write(protocol.encode(msg))
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # the reader reports the crash with the exit code and stderr
        reply = self._read(self.request_timeout, str(msg.get("op")))
        if not reply.get("ok"):
            raise WorkerRequestError(str(reply.get("error", reply)))
        return reply

    def kill(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()

    def close(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                self.request(protocol.request("shutdown"))
                proc.wait(timeout=10)
            except (WorkerError, subprocess.TimeoutExpired):
                self.kill()
        if proc is not None:
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    stream.close()
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None
        self._proc = None
