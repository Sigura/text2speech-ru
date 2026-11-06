"""`translate` / `ru2en`: Markdown structure kept, sentences translated (§6.11)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from text2audio.cli import app
from text2audio.core.translate import Engine, Translate, Unit, translate_markdown
from text2audio.models.store import ModelStore

SOURCE = """\
# Капитанская дочка

## Глава I. Сержант гвардии

> — Был бы гвардии он завтра ж капитан.\\
> — Того не надобно; пусть в армии послужит.
>
> — Княжнин.[^1]

<!-- t2a: voice=narrator -->

Отец мой служил при графе Минихе. Он вышел в отставку.[^2] Матушка *была*.

<!-- t2a: rate=0.9 -->— Эх, батюшка! — отвечал он. — Сержусь-то я на себя.

* * *

Конец <!-- t2a: pause=500 --> главы. Т. е. всё. Отец мой служил при графе Минихе.

[^1]: Эпиграф из комедии «Хвастун».
[^2]: Примечание.
"""

EXPECTED = """\
# EN(Капитанская дочка)

## EN(Глава I. Сержант гвардии)

> EN(— Был бы гвардии он завтра ж капитан.)\\
> EN(— Того не надобно; пусть в армии послужит.)
>
> — EN(Княжнин.)[^1]

<!-- t2a: voice=narrator -->

EN(Отец мой служил при графе Минихе.) EN(Он вышел в отставку.)[^2] EN(Матушка была.)

<!-- t2a: rate=0.9 -->EN(— Эх, батюшка! — отвечал он.) EN(— Сержусь-то я на себя.)

* * *

EN(Конец главы.)<!-- t2a: pause=500 --> EN(Т. е. всё.) EN(Отец мой служил при графе Минихе.)

[^1]: EN(Эпиграф из комедии «Хвастун».)

[^2]: EN(Примечание.)
"""


def _engine(name: str, unit: Unit, fn: Translate, opened: list[str] | None = None) -> Any:
    @contextmanager
    def factory() -> Iterator[Engine]:
        if opened is not None:
            opened.append(name)
        yield Engine(name, unit, fn)

    return factory


def test_structure_kept_and_each_sentence_translated_once() -> None:
    calls: list[list[str]] = []

    def fake(texts: list[str]) -> list[str]:
        calls.append(texts)
        return [f"EN({t})" for t in texts]

    out, stats = translate_markdown(SOURCE, [_engine("fake", "sentence", fake)], ("т. е.",))
    assert out == EXPECTED
    assert stats.translated == 14 and sum(map(len, calls)) == 14  # the repeat goes once
    assert stats.by == {"fake": stats.pieces} and not stats.warnings


PARAGRAPHS = """# Title

He was a kind man. He lived in Moscow. He had a dog.

She said so.[^1] Then she left. Nobody saw her again.

[^1]: A note.
"""


def test_paragraph_translator_gets_whole_pieces_unless_a_marker_sits_inside() -> None:
    seen: list[str] = []

    def llm(texts: list[str]) -> list[str]:
        seen.extend(texts)
        return [f"RU({t})" for t in texts]

    out, _ = translate_markdown(PARAGRAPHS, [_engine("llm", "paragraph", llm)])
    assert "He was a kind man. He lived in Moscow. He had a dog." in seen
    assert "She said so." in seen and "Then she left." in seen  # the note splits it
    assert "RU(He was a kind man. He lived in Moscow. He had a dog.)" in out
    assert "RU(She said so.)[^1] RU(Then she left.) RU(Nobody saw her again.)" in out


def test_lost_abbreviations_are_redone_with_placeholders() -> None:
    def llm(texts: list[str]) -> list[str]:
        # Spells abbreviations out the Russian way unless they are placeholders.
        return [t.replace("NASA", "НАСА").replace("FBI", "ФБР") for t in texts]

    text = "He worked at NASA and the FBI for many years, writing about USB drives.\n"
    out, stats = translate_markdown(text, [_engine("llm", "paragraph", llm)])
    assert "NASA" in out and "FBI" in out and "USB" in out and "НАСА" not in out
    assert stats.placeholders == 1 and not stats.warnings


def test_failed_pieces_only_go_to_the_fallback() -> None:
    opened: list[str] = []

    def llm(texts: list[str]) -> list[str]:
        return [t.replace("Bopre", "Boпре") for t in texts]  # a word in mixed scripts

    def nllb(texts: list[str]) -> list[str]:
        return [f"NL({t})" for t in texts]

    text = "Bopre was a barber at home. Then he came here.\n\nAll was quiet that night.\n"
    engines = [_engine("llm", "paragraph", llm, opened), _engine("nllb", "sentence", nllb, opened)]
    out, stats = translate_markdown(text, engines)
    assert "NL(Bopre was a barber at home.) NL(Then he came here.)" in out
    assert "All was quiet that night." in out and "NL(All" not in out
    assert stats.fallback == 1 and stats.by == {"llm": 1, "nllb": 1}
    clean = "All was quiet that night.\n"
    opened.clear()
    translate_markdown(clean, engines)
    assert opened == ["llm"]  # no failure: the fallback is never loaded


def test_cache_resumes_and_skips_what_is_done(tmp_path: Path) -> None:
    cache = tmp_path / "translate.jsonl"
    engines = [_engine("fake", "sentence", lambda ts: [f"EN({t})" for t in ts])]
    first, stats = translate_markdown(SOURCE, engines, ("т. е.",), cache_path=cache)
    assert stats.translated == 14
    again, stats = translate_markdown(SOURCE, engines, ("т. е.",), cache_path=cache)
    assert again == first and stats.translated == 0 and stats.cached == 14


def test_translator_must_answer_every_text() -> None:
    from text2audio.core.translate import TranslateError

    with pytest.raises(TranslateError, match="returned 0 texts for 1"):
        translate_markdown("Одно предложение.\n", [_engine("x", "sentence", lambda ts: [])])


def test_cli_refuses_what_it_cannot_translate(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "book.md"
    src.write_text("# Title\n\nPlain English text.\n", encoding="utf-8")
    out = runner.invoke(app, ["translate", str(src), "--to", "en"])
    assert out.exit_code == 2 and "already in 'en'" in out.output
    out = runner.invoke(app, ["translate", str(src), "--from", "ru", "--to", "en", "-o", str(src)])
    assert out.exit_code == 2 and "overwrite the input" in out.output
    out = runner.invoke(app, ["translate", str(src), "--to", "de", "--from", "ru"])
    assert out.exit_code == 2 and "no 'ru' → 'de' translator" in out.output
    out = runner.invoke(app, ["translate", str(tmp_path / "missing.md")])
    assert out.exit_code == 2 and "no such Markdown file" in out.output


def test_gpu_only_translator_without_room_has_no_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    from text2audio_mt_hf.descriptor import SeedX

    from text2audio.core import resources as res
    from text2audio.core.translate import translator_slot

    gpu = res.Gpu(index=0, uuid="GPU-fake", name="Fake", total_mb=24000, free_mb=8000)
    found = res.Resources(frozenset(range(8)), None, 16000, 16000, (gpu,))
    monkeypatch.setattr(res, "probe", lambda *a, **k: found)
    assert translator_slot(SeedX.capabilities) is None


@pytest.mark.parametrize(("free_mb", "device"), [(20000, "cuda:3"), (1000, "cpu")])
def test_translator_takes_a_gpu_with_room_else_cpu(
    monkeypatch: pytest.MonkeyPatch, free_mb: int, device: str
) -> None:
    from text2audio_mt_opus.descriptor import OpusMtRuEn

    from text2audio.core import resources as res
    from text2audio.core.translate import translator_slot

    gpu = res.Gpu(index=3, uuid="GPU-fake", name="Fake", total_mb=24000, free_mb=free_mb)
    found = res.Resources(frozenset(range(8)), None, 16000, 16000, (gpu,))
    monkeypatch.setattr(res, "probe", lambda *a, **k: found)
    assert translator_slot(OpusMtRuEn.capabilities).device == device


def _translator_ready() -> bool:
    from text2audio_mt_opus.descriptor import OpusMtRuEn

    from text2audio.core.envs import plugin_env
    from text2audio.models.lock import assets_for

    env = plugin_env(OpusMtRuEn())
    assets = assets_for("mt:opus-mt-ru-en")
    return bool(env and env.installed and assets and not ModelStore().verify(assets))


@pytest.mark.models
@pytest.mark.skipif(not _translator_ready(), reason="Opus-MT env or assets missing")
def test_ru2en_with_opus_mt(tmp_path: Path) -> None:
    src = tmp_path / "text-ru.md"
    src.write_text(
        "# Капитанская дочка\n\n## Глава I\n\nОтец мой служил в гвардии.[^1]\n\n"
        "[^1]: Примечание редактора.\n",
        encoding="utf-8",
    )
    out = CliRunner().invoke(app, ["ru2en", str(src)])
    assert out.exit_code == 0, out.output
    text = (tmp_path / "text-ru.en.md").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0].startswith("# ") and "captain" in lines[0].lower()
    assert lines[2].startswith("## ")
    assert "father" in text.lower() and "[^1]" in lines[4] and lines[6].startswith("[^1]: ")
    assert "ru → en by opus-mt-ru-en on " in out.output


def _mt_hf_ready(free_mb: int) -> bool:
    from text2audio_mt_hf.descriptor import SeedX

    from text2audio.core import resources as res
    from text2audio.core.envs import plugin_env
    from text2audio.models.lock import assets_for

    env = plugin_env(SeedX())
    assets = [*assets_for("mt:towerplus-9b"), *assets_for("mt:nllb-3.3b")]
    gpus = res.probe().gpus
    return bool(
        env
        and env.installed
        and len(assets) == 2
        and not ModelStore().verify(assets)
        and any(g.free_mb >= free_mb for g in gpus)
    )


@pytest.mark.models
@pytest.mark.gpu
@pytest.mark.skipif(
    not _mt_hf_ready(17500), reason="mt-hf env, assets or a free 17.5 GB GPU missing"
)
def test_en2ru_with_towerplus_keeps_structure_and_abbreviations(tmp_path: Path) -> None:
    src = tmp_path / "story.md"
    src.write_text(
        "# A Scandal in Bohemia\n\n## Chapter I\n\n"
        "To Sherlock Holmes she is always the woman. I have seldom heard him mention her "
        "under any other name.[^1]\n\n"
        "He had worked for NASA and the FBI before he wrote about USB drives.\n\n"
        "[^1]: A note by the editor.\n",
        encoding="utf-8",
    )
    out = CliRunner().invoke(app, ["en2ru", str(src)])
    assert out.exit_code == 0, out.output
    assert "en → ru by towerplus-9b on cuda" in out.output
    lines = (tmp_path / "story.ru.md").read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# ") and lines[2].startswith("## ")
    assert "Шерлок" in lines[4] and "слышал" in lines[4] and "[^1]" in lines[4]
    assert all(a in lines[6] for a in ("NASA", "FBI", "USB"))  # abbreviations kept
    assert lines[8].startswith("[^1]: ") and any("а" <= c <= "я" for c in lines[8])
