"""Protocol, offline contract, plugins and config."""

from __future__ import annotations

import socket
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from conftest import RU_TEXT

from text2audio.core import plugins
from text2audio.core.config import load_config
from text2audio.core.offline import allow_network, enforce_offline, is_offline
from text2audio.workers import protocol


def test_protocol_round_trips_cyrillic() -> None:
    line = protocol.encode(protocol.request("synthesize", segment={"text": RU_TEXT}))
    assert RU_TEXT in line  # not \u-escaped
    assert protocol.decode(line)["segment"]["text"] == RU_TEXT


@pytest.mark.parametrize("line", ["not json", "[1, 2]"])
def test_protocol_rejects_bad_messages(line: str) -> None:
    with pytest.raises(protocol.ProtocolError):
        protocol.decode(line)


def test_enforce_offline_sets_flags_and_caches(tmp_path: Path) -> None:
    env: dict[str, str] = {"HF_HUB_OFFLINE": "0"}
    enforce_offline(tmp_path, env)
    assert is_offline(env)
    assert env["HF_HOME"] == str(tmp_path / "hf")
    assert env["TORCH_HOME"] == str(tmp_path / "torch")
    allow_network(env)
    assert not is_offline(env)


@pytest.mark.filterwarnings("ignore:A test tried to use socket")
def test_test_suite_cannot_open_sockets() -> None:
    # The offline CI job relies on pytest-socket being active for every test.
    with pytest.raises(Exception, match=r"(?i)socket"):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_discovery_finds_sine_backend() -> None:
    found = plugins.discover()
    assert set(found.plugins) == set(plugins.GROUPS)
    sine = found.get("backends", "sine")
    assert sine is not None and sine.capabilities.supports("ru")


def test_broken_plugin_is_reported_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = EntryPoint("broken", "no_such_module:Thing", "text2audio.backends")
    monkeypatch.setattr(plugins, "entry_points", lambda group: [bad])
    found = plugins.discover(("backends",))
    assert found.plugins["backends"] == {}
    assert "backends:broken" in found.errors[0]


def test_config_layers_later_wins(tmp_path: Path) -> None:
    user = tmp_path / "user.toml"
    user.write_text('[synthesis]\nlanguage = "ru"\nseed = 7\n[resources]\nmax_workers = 2\n')
    book = tmp_path / "book.toml"
    book.write_text("[synthesis]\nseed = 9\n")
    cfg = load_config([user, book, tmp_path / "missing.toml"], {"output": {"split": "none"}})
    assert cfg.synthesis.language == "ru" and cfg.synthesis.seed == 9
    assert cfg.resources.max_workers == 2
    assert cfg.output.split == "none"


def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[synthesis]\nlangauge = 'ru'\n")
    with pytest.raises(ValueError, match="langauge"):
        load_config([bad])
