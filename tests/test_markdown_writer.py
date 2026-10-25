"""Markdown writer clean-up rules (§6.4.1)."""

from __future__ import annotations

from text2audio.readers.document import Document, Heading, Inline, Paragraph, Poem, SceneBreak
from text2audio.readers.markdown import to_markdown


def _md(*inlines: Inline) -> str:
    return to_markdown(Document(blocks=[Paragraph(list(inlines))]))


def test_emphasis_edges_and_neighbours() -> None:
    assert (
        _md(
            Inline("text", "Он сказал это "),
            Inline("emph", "как "),
            Inline("emph", "будто "),
            Inline("text", "шутя."),
        )
        == "Он сказал это *как будто* шутя.\n"
    )


def test_dialogue_dashes_and_spaces() -> None:
    assert _md(Inline("text", "–\u00a0Привет,\u00a0– сказал он.")) == "— Привет, – сказал он.\n"
    assert _md(Inline("emph", "–"), Inline("text", " Да.")) == "— Да.\n"
    assert _md(Inline("text", "- Нет.")) == "— Нет.\n"


def test_markdown_syntax_in_text_is_escaped() -> None:
    assert _md(Inline("text", "# not a heading *x* [y] <z>")) == (
        "\\# not a heading \\*x\\* \\[y\\] \\<z>\n"
    )
    assert _md(Inline("text", "1. not a list")) == "\\1. not a list\n"


def test_blocks() -> None:
    doc = Document(
        blocks=[
            Heading(2, [Inline("text", "Глава 1")]),
            Poem([[[Inline("text", "Строка один")], [Inline("text", "Строка два")]]]),
            SceneBreak(),
        ]
    )
    assert to_markdown(doc) == "## Глава 1\n\nСтрока один\\\nСтрока два\n\n* * *\n"
