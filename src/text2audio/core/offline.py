"""Offline runtime contract (SPEC §6.10, ADR-0002).

`enforce_offline` must run before any ML library is imported: Hugging Face libraries
read these variables at import time. Worker processes inherit them.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from pathlib import Path

OFFLINE_VARS = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE")


def cache_dirs(store: Path) -> dict[str, str]:
    """Caches of libraries that download on first use, redirected into the store."""
    return {
        "HF_HOME": str(store / "hf"),
        "TORCH_HOME": str(store / "torch"),
        "NLTK_DATA": str(store / "nltk"),
    }


def enforce_offline(store: Path, env: MutableMapping[str, str] | None = None) -> None:
    env = os.environ if env is None else env
    for var in OFFLINE_VARS:
        env[var] = "1"
    env["HF_HUB_DISABLE_TELEMETRY"] = "1"
    env.update(cache_dirs(store))


def allow_network(env: MutableMapping[str, str] | None = None) -> None:
    """Lift the offline flags; only `models pull` and `bundle` may call this."""
    env = os.environ if env is None else env
    for var in OFFLINE_VARS:
        env[var] = "0"


def is_offline(env: MutableMapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return all(env.get(var) == "1" for var in OFFLINE_VARS)
