"""`translate` / `ru2en`: Markdown structure kept, sentences translated (§6.11)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from text2audio.cli import app
from text2audio.core.translate import translate_markdown
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


def test_structure_kept_and_each_sentence_translated_once() -> None:
    calls: list[list[str]] = []

    def fake(texts: list[str]) -> list[str]:
        calls.append(texts)
        return [f"EN({t})" for t in texts]

    out, count = translate_markdown(SOURCE, fake, ("т. е.",))
    assert out == EXPECTED
    assert count == 14 and sum(map(len, calls)) == 14  # the repeated sentence goes once


def test_translator_must_answer_every_sentence() -> None:
    from text2audio.core.translate import TranslateError

    with pytest.raises(TranslateError, match="returned 0 texts for 1"):
        translate_markdown("Одно предложение.\n", lambda texts: [])


def test_cli_refuses_what_it_cannot_translate(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "book.md"
    src.write_text("# Title\n\nPlain English text.\n", encoding="utf-8")
    out = runner.invoke(app, ["translate", str(src)])
    assert out.exit_code == 2 and "already in 'en'" in out.output
    out = runner.invoke(app, ["translate", str(src), "--from", "ru", "-o", str(src)])
    assert out.exit_code == 2 and "overwrite the input" in out.output
    out = runner.invoke(app, ["translate", str(src), "--to", "de", "--from", "ru"])
    assert out.exit_code == 2 and "no 'ru' → 'de' translator" in out.output
    out = runner.invoke(app, ["translate", str(tmp_path / "missing.md")])
    assert out.exit_code == 2 and "no such Markdown file" in out.output


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
    assert "ru → en by opus-mt-ru-en" in out.output
