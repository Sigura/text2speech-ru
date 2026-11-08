"""DeepSeek API first, offline components as fallback (ADR-0013)."""

from __future__ import annotations

import io
import json
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from text2audio.core import deepseek as ds
from text2audio.core import synthesize as syn
from text2audio.core import translate as tr
from text2audio.core.deepseek import DeepSeek, DeepSeekError, stress_kept_text
from text2audio.core.workspace import BookFolder
from text2audio.models.store import ModelStore


def test_stress_answer_may_only_add_marks_and_yo() -> None:
    assert stress_kept_text("Все замки на месте.", "Вс+ё замк+и на м+есте.")
    assert stress_kept_text("Он, John, ушёл", "+Он, John, ушёл")
    assert not stress_kept_text("Все на месте.", "Все на м+есте!")  # punctuation changed
    assert not stress_kept_text("Все на месте.", "Все  на м+есте.")  # a space added
    assert not stress_kept_text("Все на месте.", "Все на ме+сте.")  # mark before a consonant
    assert not stress_kept_text("Всё на месте.", "Все на м+есте.")  # ё turned back into е


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _answer(content: str, finish: str = "stop") -> _Response:
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    return _Response(json.dumps(body).encode())


def test_chat_sends_key_and_text_and_unwraps_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []

    def urlopen(request: Any, timeout: float) -> _Response:
        sent.append(request)
        return _answer("<text>\nHello.\n</text>")

    monkeypatch.setattr(ds.urllib.request, "urlopen", urlopen)
    assert DeepSeek("sk-test").chat("deepseek-flash", "system", "Привет.", 0) == "Hello."
    body = json.loads(sent[0].data)
    assert sent[0].get_header("Authorization") == "Bearer sk-test"
    assert body["model"] == "deepseek-flash" and body["temperature"] == 0
    assert body["thinking"] == {"type": "disabled"}
    assert body["messages"][1]["content"] == "<text>\nПривет.\n</text>"


def test_chat_errors_are_deepseek_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(request: Any, timeout: float) -> _Response:
        raise urllib.error.HTTPError(
            request.full_url, 402, "", {}, io.BytesIO(b"Insufficient Balance")
        )

    monkeypatch.setattr(ds.urllib.request, "urlopen", denied)
    with pytest.raises(DeepSeekError, match="402: Insufficient Balance"):
        DeepSeek("sk").chat("m", "s", "t", 0)
    monkeypatch.setattr(ds.urllib.request, "urlopen", lambda r, timeout: _answer("cut", "length"))
    with pytest.raises(DeepSeekError, match="output limit"):
        DeepSeek("sk").chat("m", "s", "t", 0)


def test_stress_rejects_answers_that_changed_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = {"Вода.": "Вод+а.", "Замок.": "Замочек."}
    monkeypatch.setattr(
        DeepSeek, "chat", lambda self, model, system, text, temperature: answers[text]
    )
    assert DeepSeek("sk").stress(["Вода.", "Замок."]) == ["Вод+а.", None]


# --------------------------------------------------------------------------- stress stage


class FakeRemote:
    stress_name = "deepseek:fake"

    def __init__(self, answers: dict[str, str | None], fail: bool = False) -> None:
        self.answers, self.fail = answers, fail
        self.asked: list[str] = []

    def stress(self, texts: list[str]) -> list[str | None]:
        if self.fail:
            raise DeepSeekError("DeepSeek unreachable at https://api.deepseek.com: down")
        self.asked += texts
        return [self.answers[t] for t in texts]


class Details:
    def __init__(self) -> None:
        self.fields: dict[str, Any] = {}

    def __call__(self, stage: str, done: int, total: int) -> None:
        pass

    def detail(self, stage: str, **fields: Any) -> None:
        self.fields.update(fields)


@pytest.fixture
def local_stress(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    def fake(folder: Any, plan: Any, store: Any, plain: list[str], progress: Any) -> dict[str, str]:
        asked.extend(plain)
        return {t: f"LOCAL({t})" for t in plain}

    monkeypatch.setattr(syn, "_stress_local", fake)
    return asked


PLAN = SimpleNamespace(g2p=SimpleNamespace(name="ruaccent"), lang="ru")


def test_deepseek_marks_stress_and_the_component_does_the_rest(
    tmp_path: Path, local_stress: list[str]
) -> None:
    folder = BookFolder(tmp_path)
    remote = FakeRemote({"Вода.": "Вод+а.", "Замок.": None})
    progress = Details()
    out = syn._stress(folder, PLAN, ModelStore(tmp_path), ["Вода.", "Замок."], progress, remote)
    assert out == {"Вода.": "Вод+а.", "Замок.": "LOCAL(Замок.)"}
    assert local_stress == ["Замок."]
    assert progress.fields["deepseek_marked"] == 1 and progress.fields["fallback"] == 1
    # both answers are journaled, a rerun does not ask DeepSeek again
    remote.asked.clear()
    syn._stress(folder, PLAN, ModelStore(tmp_path), ["Вода.", "Замок."], None, remote)
    assert remote.asked == []


def test_api_failure_leaves_every_text_to_the_component(
    tmp_path: Path, local_stress: list[str]
) -> None:
    progress = Details()
    remote = FakeRemote({}, fail=True)
    out = syn._stress(BookFolder(tmp_path), PLAN, ModelStore(tmp_path), ["Вода."], progress, remote)
    assert out == {"Вода.": "LOCAL(Вода.)"}
    assert "unreachable" in progress.fields["deepseek_error"]


def test_without_a_key_or_for_other_languages_only_the_component_runs(
    tmp_path: Path, local_stress: list[str]
) -> None:
    remote = FakeRemote({})
    en = SimpleNamespace(g2p=SimpleNamespace(name="g2p-en"), lang="en")
    syn._stress(BookFolder(tmp_path), en, ModelStore(tmp_path), ["Water."], None, remote)
    syn._stress(BookFolder(tmp_path), PLAN, ModelStore(tmp_path), ["Вода."], None, None)
    assert remote.asked == [] and local_stress == ["Water.", "Вода."]


# --------------------------------------------------------------------------- translation


@pytest.fixture
def local_translator(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """One sentence-unit offline translator that can run on the CPU."""
    opened: list[str] = []
    opus = SimpleNamespace(
        name="opus-mt-ru-en", capabilities=SimpleNamespace(vram_mb=0), unit="sentence"
    )

    @contextmanager
    def open_translator(
        found: Any, store: Any, s: str, t: str, slot: Any = None
    ) -> Iterator[tr.Engine]:
        opened.append(found.name)
        yield tr.Engine(found.name, "sentence", lambda texts: [f"OPUS({x})" for x in texts])

    monkeypatch.setattr(tr, "_chain", lambda s, t: [opus])
    monkeypatch.setattr(tr, "_unusable", lambda t, store: None)
    monkeypatch.setattr(tr, "translator_slot", lambda caps: SimpleNamespace(device="cpu"))
    monkeypatch.setattr(tr, "open_translator", open_translator)
    return opened


def _remote(fn: Any) -> Any:
    return SimpleNamespace(translate_name="deepseek:fake", translate=fn)


def test_deepseek_translates_first(tmp_path: Path, local_translator: list[str]) -> None:
    src = tmp_path / "message.md"
    src.write_text("Привет. Как дела?\n", encoding="utf-8")
    remote = _remote(lambda texts, s, t: ["Hello. How are you?" for _ in texts])
    r = tr.translate_file(
        src, None, source_lang="ru", target_lang="en", store=ModelStore(tmp_path), deepseek=remote
    )
    assert r.output.read_text(encoding="utf-8").strip() == "Hello. How are you?"
    assert (r.translator, r.device) == ("deepseek:fake", "api")
    assert local_translator == []  # nothing failed the checks, the offline model never loads


def test_api_failure_translates_everything_offline(
    tmp_path: Path, local_translator: list[str]
) -> None:
    src = tmp_path / "message.md"
    src.write_text("Привет.\n", encoding="utf-8")

    def down(texts: list[str], s: str, t: str) -> list[str]:
        raise DeepSeekError("DeepSeek returned 401: invalid key")

    r = tr.translate_file(
        src,
        None,
        source_lang="ru",
        target_lang="en",
        store=ModelStore(tmp_path),
        deepseek=_remote(down),
    )
    assert r.output.read_text(encoding="utf-8").strip() == "OPUS(Привет.)"
    assert r.translator == "opus-mt-ru-en"
    assert r.stats.warnings[0].startswith("DeepSeek returned 401: invalid key; translated offline")


def test_api_failure_without_a_runnable_translator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, local_translator: list[str]
) -> None:
    monkeypatch.setattr(tr, "translator_slot", lambda caps: None)
    src = tmp_path / "message.md"
    src.write_text("Привет.\n", encoding="utf-8")

    def down(texts: list[str], s: str, t: str) -> list[str]:
        raise DeepSeekError("DeepSeek unreachable")

    with pytest.raises(tr.TranslateError, match="DeepSeek unreachable; no translator can run now"):
        tr.translate_file(
            src,
            None,
            source_lang="ru",
            target_lang="en",
            store=ModelStore(tmp_path),
            deepseek=_remote(down),
        )
