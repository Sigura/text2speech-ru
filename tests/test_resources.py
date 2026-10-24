from __future__ import annotations

from pathlib import Path

from conftest import gpu, resources

from text2audio.core import resources as res
from text2audio.core.contracts import Capabilities

QUERY = """\
0, GPU-880d557a, NVIDIA GeForce RTX 4090, 24564, 24080, 8.9
1, GPU-31ead051, NVIDIA GeForce RTX 3090 Ti, 24564, 15492, 8.6
2, GPU-119360c5, NVIDIA GeForce RTX 4090, 24564, 5000, 8.9
"""
TOPO = (
    "\x1b[4m\tGPU0\tGPU1\tGPU2\tCPU Affinity\tNUMA Affinity\tGPU NUMA ID\x1b[0m\n"
    "GPU0\t X \tSYS\tSYS\t8-15,72-79\t1\t\tN/A\n"
    "GPU1\tSYS\t X \tSYS\t16-23,80-87\t\tN/A\t\tN/A\n"
    "GPU2\tSYS\tSYS\t X \t24-31,88-95\t3\t\tN/A\n"
)


def fake_smi(args: list[str]) -> str:
    return TOPO if args[0] == "topo" else QUERY


def fake_root(tmp_path: Path, cpu_max: str = "max 100000", mem_max: str = "max") -> Path:
    (tmp_path / "proc/self").mkdir(parents=True)
    (tmp_path / "proc/self/cgroup").write_text("0::/user.slice/t2a.scope\n")
    (tmp_path / "proc/meminfo").write_text(
        "MemTotal:       16384000 kB\nMemAvailable:   12288000 kB\n"
    )
    cg = tmp_path / "sys/fs/cgroup/user.slice/t2a.scope"
    cg.mkdir(parents=True)
    (cg / "cpu.max").write_text(cpu_max + "\n")
    (cg / "memory.max").write_text(mem_max + "\n")
    (cg / "memory.current").write_text(str(1024 * 1024 * 1024) + "\n")
    return tmp_path


def test_parse_cpu_list() -> None:
    assert res.parse_cpu_list("0-2,8,10-11") == {0, 1, 2, 8, 10, 11}


def test_probe_reads_gpus_affinity_and_memory(tmp_path: Path) -> None:
    found = res.probe(
        fake_root(tmp_path), env={}, nvidia_smi=fake_smi, affinity=frozenset(range(8))
    )
    assert [g.index for g in found.gpus] == [0, 1, 2]
    assert found.gpus[0].free_mb == 24080
    assert found.gpus[1].cpu_affinity == res.parse_cpu_list("16-23,80-87")
    assert found.ram_total_mb == 16000
    assert found.ram_available_mb == 12000
    assert found.usable_cpus == 8


def test_probe_honours_cgroup_limits_like_t0(tmp_path: Path) -> None:
    # T0 reference: 4 CPUs and 8 GB through a cgroup, GPUs hidden (CLAUDE.md).
    root = fake_root(tmp_path, cpu_max="400000 100000", mem_max=str(9 * 1024**3))
    found = res.probe(
        root, env={"CUDA_VISIBLE_DEVICES": ""}, nvidia_smi=fake_smi, affinity=frozenset(range(128))
    )
    assert found.usable_cpus == 4
    assert found.ram_available_mb == 8 * 1024  # 9 GiB limit - 1 GiB used
    assert found.gpus == ()
    plan = res.plan(found)
    assert plan.tier == "fast"
    assert [(s.device, s.threads) for s in plan.slots] == [("cpu", 4)]


def test_cuda_visible_devices_by_index_and_uuid() -> None:
    gpus, _ = res.probe_gpus(fake_smi, "2,GPU-880d557a")
    assert [g.index for g in gpus] == [2, 0]


def test_no_nvidia_smi_means_cpu_only() -> None:
    gpus, notes = res.probe_gpus(lambda _: None, None)
    assert gpus == [] and "nvidia-smi" in notes[0]


def test_plan_uses_free_vram_and_numa_affinity() -> None:
    found = resources(gpus=(gpu(0, 24000, "8-15"), gpu(1, 15000, "16-23"), gpu(2, 5000, "24-31")))
    plan = res.plan(found)
    assert plan.tier == "expressive"
    assert [s.device for s in plan.slots] == ["cuda:0", "cuda:1"]  # GPU2 lacks free VRAM
    assert plan.slots[0].cpu_affinity == frozenset(range(8, 12))
    assert any("GPU2 skipped" in r for r in plan.reasons)


def test_plan_packs_workers_by_declared_vram() -> None:
    caps = Capabilities(languages=("ru",), devices=("cpu", "cuda"), vram_mb=5000, threads=2)
    plan = res.plan(resources(gpus=(gpu(0, 24000, "8-15"),)), caps)
    assert len(plan.slots) == 4  # (24000 - 1024 headroom) // 5000
    affinities = [s.cpu_affinity for s in plan.slots]
    assert all(a is not None and len(a) == 2 for a in affinities)
    assert len(set().union(*(a for a in affinities if a))) == 8  # no CPU shared


def test_plan_overrides() -> None:
    found = resources(gpus=(gpu(0, 24000), gpu(1, 24000)))
    assert [s.device for s in res.plan(found, devices=["cuda:1"]).slots] == ["cuda:1"]
    cpu_plan = res.plan(found, tier="fast", max_workers=3, threads_per_worker=8)
    assert [(s.device, s.threads) for s in cpu_plan.slots] == [("cpu", 8)] * 3


def test_cpu_plan_is_limited_by_ram() -> None:
    caps = Capabilities(languages=("ru",), ram_mb=4096, threads=4)
    plan = res.plan(resources(cpus=128, ram_gb=10), caps)
    assert len(plan.slots) == 2  # (10 GiB - 2 GiB headroom) // 4 GiB


def test_first_gpu_with_picks_first_fit_in_index_order() -> None:
    found = resources(gpus=(gpu(1, 15000), gpu(0, 6000), gpu(3, 21000)))
    assert res.first_gpu_with(found, 10000).index == 1  # type: ignore[union-attr]
    assert res.first_gpu_with(found, 30000) is None


def test_worker_slot_env_pins_gpu_and_threads() -> None:
    slot = res.WorkerSlot("cuda:3", 4, frozenset({48, 49}), "GPU-65981e45")
    env = slot.env()
    assert env["CUDA_VISIBLE_DEVICES"] == "GPU-65981e45"
    assert env["OMP_NUM_THREADS"] == "4"
    assert slot.worker_device == "cuda:0"
    assert res.WorkerSlot("cpu", 2).env()["CUDA_VISIBLE_DEVICES"] == ""
