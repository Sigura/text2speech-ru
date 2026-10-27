"""`config show` and `voices` (FR-22, §6.6.5): the effective view, its sources and the
starting `_work/text2audio.toml`."""

from __future__ import annotations

import tomllib
from pathlib import Path

from conftest import FIXTURES, GOLDEN, assert_golden
from typer.testing import CliRunner

from text2audio import api
from text2audio.cli import app
from text2audio.core.config import load_config
from text2audio.core.effective import effective_config, render, voices


def book(tmp_path: Path, source: Path, lang: str | None = None) -> Path:
    [result] = api.convert([source], tmp_path / "out", lang=lang)
    assert result.folder is not None
    return result.folder


def test_zero_config_snapshot_ru(tmp_path: Path) -> None:
    folder = book(tmp_path, FIXTURES / "ru" / "kapitanskaya-dochka.fb2")
    view = effective_config(folder, user_file=tmp_path / "none.toml")
    assert view.get("synthesis.language").source == "metadata"
    assert_golden(GOLDEN / "config-show" / "ru.toml", render(view))


def test_zero_config_snapshot_without_pack(tmp_path: Path) -> None:
    source = tmp_path / "buch.md"
    source.write_text("# Kapitel 1\n\nEs war einmal ein König.\n", encoding="utf-8")
    folder = book(tmp_path, source, lang="de")
    view = effective_config(folder, user_file=tmp_path / "none.toml")
    assert view.profile is None
    assert not [s for s in view.settings if s.key.startswith("lang.")]
    assert_golden(GOLDEN / "config-show" / "no-pack.toml", render(view))


def test_starting_book_config_is_commented_and_loads_empty(tmp_path: Path) -> None:
    folder = book(tmp_path, FIXTURES / "ru" / "kapitanskaya-dochka.fb2")
    path = folder / "_work" / "text2audio.toml"
    text = path.read_text(encoding="utf-8")
    assert '# fast = "silero"  # profile:ru' in text
    assert tomllib.loads(text) == {}
    assert load_config([path]) == load_config([])


def test_convert_keeps_an_edited_book_config(tmp_path: Path) -> None:
    folder = book(tmp_path, FIXTURES / "ru" / "kapitanskaya-dochka.fb2")
    path = folder / "_work" / "text2audio.toml"
    path.write_text('[qa]\nmode = "off"\n', encoding="utf-8")
    api.convert([folder], reconvert=True)
    assert path.read_text(encoding="utf-8") == '[qa]\nmode = "off"\n'


def test_source_precedence(tmp_path: Path) -> None:
    folder = book(tmp_path, FIXTURES / "ru" / "kapitanskaya-dochka.fb2")
    user = tmp_path / "user.toml"
    user.write_text("[qa]\nbatch = 50\n[output]\nmax_parts = 10\nmin_parts = 3\n")
    (folder / "_work" / "text2audio.toml").write_text(
        '[output]\nmax_parts = 20\n[lang.ru]\nbackend = "sine"\n'
    )
    view = effective_config(folder, {"output": {"max_parts": 30}}, user_file=user)
    got = {s.key: (s.value, s.source) for s in view.settings}
    assert got["qa.batch"] == (50, "user")
    assert got["output.min_parts"] == (3, "user")
    assert got["output.max_parts"] == (30, "cli")
    assert got["output.split"] == ("auto", "core")
    assert got["qa.wer_threshold"] == (0.18, "profile:ru")
    # a per-language string replaces the profile's per-tier table
    assert got["lang.ru.backend"] == ("sine", "book")
    assert "lang.ru.backend.fast" not in got and "lang.ru.backend.expressive" not in got
    assert got["lang.ru.polish.expressive"] == ("chatterbox", "profile:ru")
    tomllib.loads(render(view))


def test_cli_language_override_switches_profile(tmp_path: Path) -> None:
    folder = book(tmp_path, FIXTURES / "ru" / "kapitanskaya-dochka.fb2")
    view = effective_config(folder, {"synthesis": {"language": "de"}})
    assert view.get("synthesis.language").source == "cli"
    assert view.profile is None


def test_voices_lists_roles_and_backend_voices() -> None:
    found = voices("ru")
    assert found.roles["ru"]["narrator"]["silero"] == "xenia"
    assert found.roles["ru"]["dialogue"]["chatterbox"] == "ref-xenia.wav"
    assert found.backends["sine"] == ["narrator", "dialogue"]
    assert found.backends["chatterbox"] == ["(clones a reference clip)"]


def test_cli_config_show_and_voices(tmp_path: Path) -> None:
    folder = book(tmp_path, FIXTURES / "ru" / "kapitanskaya-dochka.fb2")
    runner = CliRunner()
    shown = runner.invoke(app, ["config", "show", str(folder), "--tier", "fast"])
    assert shown.exit_code == 0, shown.output
    assert 'tier = "fast"  # cli' in shown.output
    assert 'fast = "silero"  # profile:ru' in shown.output

    missing = runner.invoke(app, ["config", "show", str(tmp_path / "nope")])
    assert missing.exit_code == 2

    (folder / "_work" / "text2audio.toml").write_text("[qa]\nmode = 3\n")
    bad = runner.invoke(app, ["config", "show", str(folder)])
    assert bad.exit_code == 1

    listed = runner.invoke(app, ["voices", "--lang", "ru"])
    assert listed.exit_code == 0, listed.output
    assert "  narrator: silero xenia, chatterbox ref-xenia.wav" in listed.output
    assert "  sine: narrator, dialogue" in listed.output
