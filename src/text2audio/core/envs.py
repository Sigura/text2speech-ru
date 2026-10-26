"""Per-plugin uv environments (SPEC §6.6.3, ADR-0004, ADR-0010).

A plugin whose model needs heavy libraries (torch, onnxruntime, transformers) ships a
uv project next to its descriptor (`env/pyproject.toml` + `env/uv.lock`). The
environment itself lives outside the repository, under `<data dir>/text2audio/envs/
<name>`, and is created by `text2audio envs sync` (or `install.sh`) — install time,
the only time it may use the network. At runtime the core only starts the worker
with that environment's interpreter.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import platformdirs

ENV_PYTHON = "3.12"  # the version every plugin env is locked for


class EnvMissing(Exception):
    pass


class UnknownEnv(ValueError):
    pass


def env_home() -> Path:
    env = os.environ.get("T2A_ENV_HOME")
    if env:
        return Path(env)
    return Path(platformdirs.user_data_dir("text2audio")) / "envs"


@dataclass(frozen=True)
class PluginEnv:
    name: str
    project: Path  # directory with pyproject.toml + uv.lock

    @property
    def path(self) -> Path:
        return env_home() / self.name

    @property
    def python(self) -> Path:
        return self.path / "bin" / "python"

    @property
    def installed(self) -> bool:
        return self.python.is_file()

    @property
    def fix(self) -> str:
        return f"text2audio envs sync {self.name}"


def plugin_env(plugin: Any) -> PluginEnv | None:
    """The environment a descriptor declares (`env` + `env_project`), if any."""
    name = getattr(plugin, "env", None)
    project = getattr(plugin, "env_project", None)
    if not name or project is None:
        return None
    return PluginEnv(str(name), Path(project))


def python_for(plugin: Any) -> str:
    """Interpreter for a plugin's worker; the core's own when it declares no env."""
    env = plugin_env(plugin)
    if env is None:
        return sys.executable
    if not env.installed:
        raise EnvMissing(
            f"environment {env.name!r} for plugin {getattr(plugin, 'name', env.name)!r} "
            f"is not installed; fix: {env.fix}"
        )
    return str(env.python)


def find_uv() -> str:
    found = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if not Path(found).is_file():
        raise EnvMissing("uv not found; install it: https://docs.astral.sh/uv/")
    return found


def sync(env: PluginEnv, *, uv: str | None = None, quiet: bool = False) -> None:
    """Create or update the environment from its lock file (install time only)."""
    if not (env.project / "uv.lock").is_file():
        raise EnvMissing(f"{env.project}: no uv.lock; run `uv lock --project {env.project}`")
    cmd = [
        uv or find_uv(),
        "sync",
        "--frozen",
        "--no-install-project",
        "--project",
        str(env.project),
        "--python",
        ENV_PYTHON,
    ]
    if quiet:
        cmd.append("--quiet")
    run_env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(env.path)}
    for var in ("VIRTUAL_ENV", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        run_env.pop(var, None)
    subprocess.run(cmd, check=True, env=run_env)
