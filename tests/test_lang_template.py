"""The language-pack template (§6.6.4): `scripts/new_lang.py` generates a pack whose own
tests pass without installing it, and that is lint-clean."""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from new_lang import generate  # noqa: E402


def test_generated_pack_passes_its_tests(tmp_path: Path) -> None:
    out = tmp_path / "lang-xx"
    files = generate("xx", "Testish", "Latn", out, sample="Plain words.")
    names = {p.relative_to(out).as_posix() for p in files}
    assert "src/text2audio_lang_xx/__init__.py" in names
    assert "tests/test_xx_normalize.py" in names
    assert not any("$" in p.read_text("utf-8") for p in files if p.suffix != ".py")
    project = tomllib.loads((out / "pyproject.toml").read_text("utf-8"))
    assert project["project"]["entry-points"]["text2audio.languages"] == {
        "xx": "text2audio_lang_xx:PROFILE"
    }
    env = {**os.environ, "PYTHONPATH": str(out / "src")}
    run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-c",
            os.devnull,
            "--rootdir",
            str(out),
            str(out / "tests"),
        ],
        env=env,
        capture_output=True,
        text=True,
        cwd=out,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    lint = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--config", str(ROOT / "pyproject.toml"), str(out)],
        capture_output=True,
        text=True,
    )
    assert lint.returncode == 0, lint.stdout + lint.stderr


def test_generator_refuses_bad_input_and_overwrites(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        generate("German", "Deutsch", "Latn", tmp_path / "a")
    with pytest.raises(ValueError):
        generate("de", "Deutsch", "latin", tmp_path / "b")
    (tmp_path / "c").mkdir()
    with pytest.raises(FileExistsError):
        generate("de", "Deutsch", "Latn", tmp_path / "c")
