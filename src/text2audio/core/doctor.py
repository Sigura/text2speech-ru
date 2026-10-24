"""`text2audio doctor`: environment, resources and offline-readiness check (§6.10)."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from text2audio.core import resources as res
from text2audio.core.offline import is_offline
from text2audio.core.plugins import Discovery, discover
from text2audio.models.lock import LockError, load_lock
from text2audio.models.store import ModelStore

Status = Literal["ok", "warn", "fail"]


@dataclass
class Check:
    name: str
    status: Status
    detail: str


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)
    resources: res.Resources | None = None
    plan: res.ResourcePlan | None = None

    @property
    def ok(self) -> bool:
        return all(c.status != "fail" for c in self.checks)

    def add(self, name: str, status: Status, detail: str) -> None:
        self.checks.append(Check(name, status, detail))


def _tool(report: DoctorReport, name: str, path: str | None, required: bool) -> None:
    if path is None:
        need = "required" if required else "needed by some backends"
        report.add(name, "fail" if required else "warn", f"not found on PATH ({need})")
    elif "conda" in path and not path.startswith(sys.prefix):
        # Borrowed from another conda env (it may vanish); our own env's copy is fine.
        report.add(name, "warn", f"{path} (from another conda env; prefer a system install)")
    else:
        report.add(name, "ok", path)


def _resources(report: DoctorReport, r: res.Resources, p: res.ResourcePlan) -> None:
    report.add(
        "cpu",
        "ok",
        f"{r.usable_cpus} usable CPU(s); RAM {r.ram_available_mb // 1024} of "
        f"{r.ram_total_mb // 1024} GiB available",
    )
    if r.gpus:
        report.add("gpu", "ok", "; ".join(g.label for g in r.gpus))
    else:
        report.add("gpu", "ok", "none (CPU-only, fast tier)")
    for note in r.notes:
        report.add("limits", "ok", note)
    slots = ", ".join(sorted({s.device for s in p.slots})) or "none"
    report.add("tier", "ok" if p.slots else "warn", f"{p.tier}; worker devices: {slots}")


def run_doctor(
    store: ModelStore | None = None,
    lock_path: Path | None = None,
    *,
    which: Callable[[str], str | None] | None = None,
    resources: res.Resources | None = None,
    plugins: Discovery | None = None,
    env: Mapping[str, str] | None = None,
) -> DoctorReport:
    env = os.environ if env is None else env
    which = which or shutil.which
    store = store or ModelStore()
    report = DoctorReport()

    v = sys.version_info
    report.add("python", "ok" if v >= (3, 11) else "fail", f"{v.major}.{v.minor}.{v.micro}")
    report.add(
        "offline",
        "ok" if is_offline(dict(env)) else "warn",
        "HF offline flags set"
        if is_offline(dict(env))
        else "offline flags not set "
        "(the CLI sets them; set them yourself when using the Python API)",
    )
    _tool(report, "ffmpeg", which("ffmpeg"), required=True)
    _tool(report, "espeak-ng", which("espeak-ng"), required=False)

    report.resources = resources or res.probe()
    report.plan = res.plan(report.resources)
    _resources(report, report.resources, report.plan)

    if store.root.exists() and not os.access(store.root, os.W_OK):
        report.add("model store", "fail", f"{store.root} is not writable")
    else:
        report.add("model store", "ok", str(store.root))

    try:
        assets = load_lock(lock_path)
    except LockError as exc:
        report.add("models.lock", "fail", str(exc))
        assets = []
    else:
        problems = store.verify(assets)
        for problem in problems:
            report.add("asset", "fail", str(problem))
        n_files = sum(len(a.files) for a in assets)
        report.add(
            "models.lock",
            "fail" if problems else "ok",
            f"{len(assets)} asset(s), {n_files} file(s), {len(problems)} problem(s)",
        )

    plugins = plugins or discover()
    for error in plugins.errors:
        report.add("plugin", "warn", error)
    for name, backend in sorted(plugins.plugins.get("backends", {}).items()):
        caps = backend.capabilities
        licenses = sorted({a.license for a in backend.assets()}) or ["no assets"]
        report.add(
            f"backend {name}",
            "ok",
            f"languages {','.join(caps.languages)}; licenses: {', '.join(licenses)}",
        )
    return report
