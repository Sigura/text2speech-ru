"""Hardware probe and resource planning (SPEC §9, §9.1, NFR-07).

The probe reports what *this process* may use, not what the machine has: CPU
affinity (`taskset`) and cgroup quotas cap CPUs, cgroup `memory.max` caps RAM, and
`CUDA_VISIBLE_DEVICES` filters GPUs. GPUs are often shared, so planning uses *free*
VRAM. No torch here: GPUs are read with `nvidia-smi`.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from text2audio.core.contracts import Capabilities, Tier

# T1 floor (§9): a GPU with less free VRAM than this is not used for the expressive tier.
MIN_EXPRESSIVE_VRAM_MB = 8 * 1024
# Headroom left free on each GPU and in RAM for the core, ASR and the OS.
GPU_HEADROOM_MB = 1024
RAM_HEADROOM_MB = 2048

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_CPU_LIST = re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")

NvidiaSmi = Callable[[list[str]], str | None]


@dataclass(frozen=True)
class Gpu:
    index: int  # physical index as reported by nvidia-smi
    uuid: str
    name: str
    total_mb: int
    free_mb: int
    compute_cap: str = ""
    cpu_affinity: frozenset[int] | None = None  # CPUs on the GPU's NUMA node

    @property
    def label(self) -> str:
        return f"GPU{self.index} {self.name} ({self.free_mb}/{self.total_mb} MiB free)"


@dataclass(frozen=True)
class Resources:
    cpus: frozenset[int]  # CPUs this process may run on
    cpu_quota: float | None  # cgroup cpu.max quota in CPUs, if limited
    ram_total_mb: int
    ram_available_mb: int  # min(MemAvailable, cgroup limit - usage)
    gpus: tuple[Gpu, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def usable_cpus(self) -> int:
        n = len(self.cpus)
        if self.cpu_quota is not None:
            n = min(n, max(1, math.floor(self.cpu_quota)))
        return max(1, n)


@dataclass(frozen=True)
class WorkerSlot:
    """Where one worker runs and how it is constrained."""

    device: str  # "cpu" or "cuda:<physical index>"
    threads: int
    cpu_affinity: frozenset[int] | None = None
    gpu_uuid: str | None = None

    def env(self) -> dict[str, str]:
        """Environment for the worker process; the worker sees its GPU as cuda:0."""
        n = str(self.threads)
        return {
            "OMP_NUM_THREADS": n,
            "MKL_NUM_THREADS": n,
            "OPENBLAS_NUM_THREADS": n,
            "TORCH_NUM_THREADS": n,
            "CUDA_VISIBLE_DEVICES": self.gpu_uuid or "",
        }

    @property
    def worker_device(self) -> str:
        return "cuda:0" if self.gpu_uuid else "cpu"


@dataclass
class ResourcePlan:
    tier: Tier
    slots: list[WorkerSlot]
    reasons: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- probe


def parse_cpu_list(text: str) -> frozenset[int]:
    """Parse "0-3,8,10-11" into a set of CPU ids."""
    cpus: set[int] = set()
    for part in text.strip().split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        cpus.update(range(int(lo), int(hi or lo) + 1))
    return frozenset(cpus)


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except OSError:
        return None


def _cgroup_dir(root: Path) -> Path | None:
    """cgroup v2 directory of this process, if any."""
    text = _read(root / "proc/self/cgroup")
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("0::"):
            rel = line[3:].strip().lstrip("/")
            return root / "sys/fs/cgroup" / rel
    return None


def _cgroup_limits(root: Path) -> tuple[float | None, int | None]:
    """Return (cpu quota in CPUs, free memory under the cgroup limit in MiB).

    Walks up the hierarchy, because limits set on a parent slice apply too.
    """
    quota: float | None = None
    mem_free: int | None = None
    base = root / "sys/fs/cgroup"
    d = _cgroup_dir(root)
    while d is not None and d.is_relative_to(base):
        fields = (_read(d / "cpu.max") or "").split()
        if len(fields) == 2 and fields[0] != "max":
            cpus = int(fields[0]) / int(fields[1])
            quota = cpus if quota is None else min(quota, cpus)
        mem_max = _read(d / "memory.max")
        if mem_max and mem_max.strip() != "max":
            used = int((_read(d / "memory.current") or "0").strip() or 0)
            free = (int(mem_max) - used) // (1024 * 1024)
            mem_free = free if mem_free is None else min(mem_free, free)
        if d == base:
            break
        d = d.parent
    return quota, mem_free


def _meminfo(root: Path) -> tuple[int, int]:
    text = _read(root / "proc/meminfo") or ""
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if rest.strip():
            values[key] = int(rest.split()[0]) // 1024  # kB → MiB
    total = values.get("MemTotal", 0)
    return total, values.get("MemAvailable", total)


def _run_nvidia_smi(args: list[str]) -> str | None:
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=15, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout


def _gpu_affinity(topo: str | None) -> dict[int, frozenset[int]]:
    """Parse the "CPU Affinity" column of `nvidia-smi topo -m`."""
    if not topo:
        return {}
    lines = [_ANSI.sub("", ln) for ln in topo.splitlines() if ln.strip()]
    header = next((ln.split("\t") for ln in lines if "CPU Affinity" in ln), None)
    if header is None:
        return {}
    col = [h.strip() for h in header].index("CPU Affinity")
    result: dict[int, frozenset[int]] = {}
    for ln in lines:
        cells = ln.split("\t")
        m = re.fullmatch(r"GPU(\d+)", cells[0].strip())
        if m and len(cells) > col and _CPU_LIST.match(cells[col].strip()):
            result[int(m.group(1))] = parse_cpu_list(cells[col])
    return result


def _visible(gpus: list[Gpu], cuda_visible: str | None) -> tuple[list[Gpu], str | None]:
    if cuda_visible is None:
        return gpus, None
    wanted = [t.strip() for t in cuda_visible.split(",") if t.strip()]
    if not wanted:
        return [], "CUDA_VISIBLE_DEVICES is empty: GPUs hidden"
    picked = [
        g
        for token in wanted
        for g in gpus
        if token == str(g.index) or (len(token) > 4 and g.uuid.startswith(token))
    ]
    return picked, f"CUDA_VISIBLE_DEVICES={cuda_visible}"


def probe_gpus(
    nvidia_smi: NvidiaSmi = _run_nvidia_smi, cuda_visible: str | None = None
) -> tuple[list[Gpu], list[str]]:
    query = "index,uuid,name,memory.total,memory.free,compute_cap"
    out = nvidia_smi([f"--query-gpu={query}", "--format=csv,noheader,nounits"])
    if out is None:
        return [], ["nvidia-smi not available: no CUDA GPUs"]
    affinity = _gpu_affinity(nvidia_smi(["topo", "-m"]))
    gpus = []
    for line in out.strip().splitlines():
        idx, uuid, name, total, free, cc = (c.strip() for c in line.split(","))
        gpus.append(Gpu(int(idx), uuid, name, int(total), int(free), cc, affinity.get(int(idx))))
    visible, note = _visible(gpus, cuda_visible)
    return visible, [note] if note else []


def probe(
    root: Path = Path("/"),
    env: Mapping[str, str] | None = None,
    nvidia_smi: NvidiaSmi = _run_nvidia_smi,
    affinity: frozenset[int] | None = None,
) -> Resources:
    """Probe what this process may use. Arguments exist for tests."""
    env = os.environ if env is None else env
    if affinity is None:
        affinity = frozenset(os.sched_getaffinity(0))
    quota, cg_free = _cgroup_limits(root)
    total, available = _meminfo(root)
    notes: list[str] = []
    if cg_free is not None and cg_free < available:
        available = cg_free
        notes.append("RAM limited by cgroup memory.max")
    if quota is not None:
        notes.append(f"CPU limited by cgroup cpu.max to {quota:g} CPUs")
    gpus, gpu_notes = probe_gpus(nvidia_smi, env.get("CUDA_VISIBLE_DEVICES"))
    notes.extend(gpu_notes)
    return Resources(affinity, quota, total, available, tuple(gpus), tuple(notes))


# --------------------------------------------------------------------------- plan


def first_gpu_with(res: Resources, need_mb: int) -> Gpu | None:
    """First GPU, in nvidia-smi order, with `need_mb` free plus headroom.

    For one-off GPU services (e.g. the cover generator) that need one card, not slots.
    """
    return next(
        (
            g
            for g in sorted(res.gpus, key=lambda g: g.index)
            if g.free_mb - GPU_HEADROOM_MB >= need_mb
        ),
        None,
    )


def plan(
    res: Resources,
    caps: Capabilities | None = None,
    *,
    tier: Tier | None = None,
    devices: list[str] | None = None,
    max_workers: int | None = None,
    threads_per_worker: int | None = None,
) -> ResourcePlan:
    """Decide the tier and the worker slots for one backend.

    `caps` carries the backend's needs (VRAM/RAM per worker, preferred threads);
    without it, the plan reflects the hardware alone (used by `doctor`).
    """
    caps = caps or Capabilities(languages=("*",), devices=("cpu", "cuda"))
    reasons: list[str] = []
    needs_vram = caps.vram_mb or MIN_EXPRESSIVE_VRAM_MB
    gpus = list(res.gpus)
    if devices is not None:
        allowed = {d.removeprefix("cuda:") for d in devices if d.startswith("cuda")}
        gpus = [g for g in gpus if str(g.index) in allowed]
        reasons.append(f"devices restricted by config: {', '.join(devices)}")
    usable = [g for g in gpus if g.free_mb - GPU_HEADROOM_MB >= needs_vram]
    for g in gpus:
        if g not in usable:
            reasons.append(f"GPU{g.index} skipped: {g.free_mb} MiB free < {needs_vram} needed")
    cpu_allowed = devices is None or "cpu" in devices

    if tier is None:
        tier = "expressive" if usable and "cuda" in caps.devices else "fast"
        reasons.append(
            f"tier {tier}: "
            + (f"{len(usable)} GPU(s) with ≥ {needs_vram} MiB free" if usable else "no usable GPU")
        )

    slots: list[WorkerSlot] = []
    remaining = set(res.cpus)
    if "cuda" in caps.devices and usable and tier == "expressive":
        for g in sorted(usable, key=lambda g: -g.free_mb):
            per_gpu = 1
            if caps.vram_mb:
                per_gpu = max(1, (g.free_mb - GPU_HEADROOM_MB) // caps.vram_mb)
            near = (g.cpu_affinity or frozenset(res.cpus)) & res.cpus
            threads = threads_per_worker or min(caps.threads, max(1, len(near) // per_gpu))
            for _ in range(per_gpu):
                pick = frozenset(sorted(near & remaining)[:threads]) or None
                if pick:
                    remaining -= pick
                slots.append(WorkerSlot(f"cuda:{g.index}", threads, pick, g.uuid))
    elif cpu_allowed:
        threads = threads_per_worker or min(caps.threads, res.usable_cpus)
        by_cpu = max(1, res.usable_cpus // threads)
        by_ram = max(1, (res.ram_available_mb - RAM_HEADROOM_MB) // max(1, caps.ram_mb))
        n = min(by_cpu, by_ram)
        reasons.append(
            f"{n} CPU worker(s) × {threads} threads (CPU allows {by_cpu}, RAM allows {by_ram})"
        )
        ordered = sorted(res.cpus)
        for i in range(n):
            pick = frozenset(ordered[i * threads : (i + 1) * threads]) or None
            slots.append(WorkerSlot("cpu", threads, pick))
    else:
        reasons.append("no usable device: CPU excluded by config and no usable GPU")

    if max_workers is not None and len(slots) > max_workers:
        slots = slots[:max_workers]
        reasons.append(f"capped at max_workers={max_workers}")
    return ResourcePlan(tier, slots, reasons)
