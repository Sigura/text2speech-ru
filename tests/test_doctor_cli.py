"""M0 exit criterion: `doctor` passes; CLI commands and exit codes (§6.11)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import gpu, resources, write_lock
from typer.testing import CliRunner

from text2audio.cli import ExitCode, app
from text2audio.core.contracts import ModelAsset
from text2audio.core.doctor import run_doctor
from text2audio.models.store import ModelStore

runner = CliRunner()
ON_PATH = {"ffmpeg": "/usr/bin/ffmpeg", "espeak-ng": "/usr/bin/espeak-ng"}
OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"}


def _doctor(store: ModelStore, lock: Path, which: dict[str, str]):  # type: ignore[no-untyped-def]
    return run_doctor(
        store,
        lock,
        which=which.get,
        resources=resources(gpus=(gpu(0, 24000),)),
        env=OFFLINE_ENV,
    )


def test_doctor_passes_with_verified_assets(
    tmp_path: Path, store: ModelStore, make_asset: Callable[..., ModelAsset]
) -> None:
    report = _doctor(store, write_lock(tmp_path / "models.lock", [make_asset()]), ON_PATH)
    assert report.ok, [c for c in report.checks if c.status == "fail"]
    names = {c.name for c in report.checks}
    assert {"python", "offline", "ffmpeg", "gpu", "tier", "models.lock", "backend sine"} <= names
    assert report.plan is not None and report.plan.tier == "expressive"


def test_doctor_fails_on_missing_asset_and_names_the_fix(
    tmp_path: Path, store: ModelStore, make_asset: Callable[..., ModelAsset]
) -> None:
    lock = write_lock(tmp_path / "models.lock", [make_asset(install=False)])
    report = _doctor(store, lock, ON_PATH)
    assert not report.ok
    assert any("text2audio models pull --lang ru" in c.detail for c in report.checks)


def test_doctor_warns_on_conda_ffmpeg_and_fails_without_it(
    tmp_path: Path, store: ModelStore
) -> None:
    lock = tmp_path / "none.lock"
    conda = _doctor(store, lock, {"ffmpeg": "/home/u/miniconda3/envs/t110/bin/ffmpeg"})
    assert {c.name: c.status for c in conda.checks}["ffmpeg"] == "warn"
    assert {c.name: c.status for c in conda.checks}["espeak-ng"] == "warn"
    assert not _doctor(store, lock, {}).ok


def test_cli_doctor_exit_code_3_on_missing_asset(
    tmp_path: Path, make_asset: Callable[..., ModelAsset], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("T2A_MODEL_HOME", str(tmp_path / "store"))
    lock = write_lock(tmp_path / "models.lock", [make_asset(install=False)])
    result = runner.invoke(app, ["doctor", "--lock", str(lock)])
    assert result.exit_code == ExitCode.MISSING_ASSET
    assert "NOT ready" in result.output


def test_cli_sets_offline_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in OFFLINE_ENV:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("T2A_MODEL_HOME", str(tmp_path))
    assert runner.invoke(app, ["version"]).exit_code == 0
    import os

    assert all(os.environ[v] == "1" for v in OFFLINE_ENV)
    assert os.environ["HF_HOME"] == str(tmp_path / "hf")


def test_cli_plugins_and_models(
    tmp_path: Path, make_asset: Callable[..., ModelAsset], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("T2A_MODEL_HOME", str(tmp_path / "store"))
    backends = next(
        x for x in runner.invoke(app, ["plugins"]).output.splitlines() if x.startswith("backends:")
    )
    assert {"sine", "silero"} <= set(backends.removeprefix("backends: ").split(", "))
    lock = str(write_lock(tmp_path / "models.lock", [make_asset(install=False)]))
    listed = runner.invoke(app, ["models", "list", "--lang", "ru", "--lock", lock])
    assert "org/model@aaaaaaaaaaaa" in listed.output
    assert runner.invoke(app, ["models", "list", "--lang", "en", "--lock", lock]).output == ""
    verify = runner.invoke(app, ["models", "verify", "--lock", lock])
    assert verify.exit_code == ExitCode.MISSING_ASSET


def test_cli_resources_json() -> None:
    result = runner.invoke(app, ["resources", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["plan"]["tier"] in ("fast", "expressive")
    assert data["plan"]["slots"]


def test_planned_commands_explain_their_milestone() -> None:
    result = runner.invoke(app, ["preview", "output/Война и мир"])
    assert result.exit_code == ExitCode.USAGE
    assert "M2" in result.output


def test_synthesize_rejects_a_folder_that_is_not_a_book(tmp_path: Path) -> None:
    result = runner.invoke(app, ["synthesize", str(tmp_path)])
    assert result.exit_code == ExitCode.INPUT
    assert "not a book folder" in result.output
