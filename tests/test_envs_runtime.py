"""Plugin environments (§6.6.3) and the worker runtime's network guard (§6.10)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from text2audio.core.envs import EnvMissing, PluginEnv, plugin_env, python_for
from text2audio.workers.backend import RUNTIME_DIR


class Plugin:
    name = "demo"
    env = "backend-demo"
    env_project = Path("/nonexistent/env")


def test_env_location(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("T2A_ENV_HOME", str(tmp_path))
    env = plugin_env(Plugin())
    assert env == PluginEnv("backend-demo", Path("/nonexistent/env"))
    assert env.python == tmp_path / "backend-demo" / "bin" / "python"


def test_missing_env_names_the_fix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("T2A_ENV_HOME", str(tmp_path))
    with pytest.raises(EnvMissing, match="text2audio envs sync backend-demo"):
        python_for(Plugin())


def test_plugin_without_env_uses_core_interpreter() -> None:
    class Light:
        name = "light"

    assert python_for(Light()) == sys.executable


def test_installed_plugins_declare_locked_envs() -> None:
    from text2audio import api

    envs = api.plugin_envs()
    assert {"backend-silero", "g2p-ruaccent"} <= {e.name for e in envs}
    for env in envs:
        assert (env.project / "pyproject.toml").is_file()
        assert (env.project / "uv.lock").is_file()


GUARDED = """
import t2a_worker, socket
try:
    socket.create_connection(("huggingface.co", 443), timeout=1)
except t2a_worker.NetworkBlocked:
    print("blocked")
"""


def test_worker_runtime_blocks_network() -> None:
    out = subprocess.run(
        [sys.executable, "-c", GUARDED],
        env={"PYTHONPATH": str(RUNTIME_DIR), "T2A_NO_NETWORK": "1"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "blocked"
