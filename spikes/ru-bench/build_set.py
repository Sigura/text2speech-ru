"""Build the M2 benchmark set (SPEC §6.6.5 protocol, step 1) — deterministic.

    uv run --python 3.11 python spikes/ru-bench/build_set.py

Sources: «Капитанская дочка» (tests/fixtures/ru/large, fetched by
tests/fixtures/fetch_novel.py; printed without ё, so its sentences test ё restoration
as found), tests/fixtures/ru/codeswitch.md (FR-24), handwritten.tsv (homographs,
numbers with expected normalization, more FR-24 lines) and one Pushkin poem.
Writes set.tsv (id, category, text, expected) and bench.md (one paragraph per item),
both committed so the benchmark does not need the novel.
"""

from __future__ import annotations

import re
from pathlib import Path

import pymorphy3

HERE = Path(__file__).parent
ROOT = HERE.parents[1]
NOVEL = ROOT / "tests/fixtures/ru/large/kapitanskaya-dochka.md"
CODESWITCH = ROOT / "tests/fixtures/ru/codeswitch.md"
DIALOGUE, YO = 20, 50

POEM = """Буря мглою небо кроет,
Вихри снежные крутя;
То, как зверь, она завоет,
То заплачет, как дитя,
То по кровле обветшалой
Вдруг соломой зашумит,
То, как путник запоздалый,
К нам в окошко застучит.

Наша ветхая лачужка
И печальна и темна.
Что же ты, моя старушка,
Приумолкла у окна?
Или бури завываньем
Ты, мой друг, утомлена,
Или дремлешь под жужжаньем
Своего веретена?

Выпьем, добрая подружка
Бедной юности моей,
Выпьем с горя; где же кружка?
Сердцу будет веселей.
Спой мне песню, как синица
Тихо за морем жила;
Спой мне песню, как девица
За водой поутру шла.

Буря мглою небо кроет,
Вихри снежные крутя;
То, как зверь, она завоет,
То заплачет, как дитя.
Выпьем, добрая подружка
Бедной юности моей,
Выпьем с горя; где же кружка?
Сердцу будет веселей."""

_WORD = re.compile(r"[а-яА-ЯёЁ]+(?:-[а-яА-ЯёЁ]+)*")
_SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[А-ЯЁ«—])")


def spread(items: list[str], n: int) -> list[str]:
    if len(items) <= n:
        return items
    return [items[i * len(items) // n] for i in range(n)]


def yo_words(sentence: str, morph: pymorphy3.MorphAnalyzer) -> list[str]:
    """Words printed with е that every pymorphy3 reading spells with ё."""
    found = []
    for word in _WORD.findall(sentence):
        low = word.lower()
        if "е" not in low or "ё" in low:
            continue
        forms = {p.word for p in morph.parse(low)}
        if forms and all("ё" in f for f in forms):
            found.append(forms.pop())
    return found


def novel_items(morph: pymorphy3.MorphAnalyzer) -> tuple[list[str], list[tuple[str, str]]]:
    paragraphs = [
        " ".join(p.split())
        for p in NOVEL.read_text("utf-8").split("\n\n")
        if p.strip() and not p.startswith("#")
    ]
    clean = [p for p in paragraphs if not re.search(r"[0-9A-Za-z\"*_]", p)]
    dialogue = [
        p
        for p in clean
        if p.startswith("— ") and re.search(r"[,!?.…] — [а-яё]", p) and 30 <= len(p) <= 220
    ]
    dialogue = spread(dialogue, DIALOGUE)
    yo = []
    for p in clean:
        if p in dialogue or p.startswith("— "):
            continue
        for s in _SENTENCE.split(p):
            words = yo_words(s, morph)
            if words and 40 <= len(s) <= 220:
                yo.append((s, ",".join(words)))
    return dialogue, [yo[i * len(yo) // YO] for i in range(min(YO, len(yo)))]


def main() -> None:
    morph = pymorphy3.MorphAnalyzer()
    rows: list[tuple[str, str, str]] = []
    for line in (HERE / "handwritten.tsv").read_text("utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            cat, text, expected = [*line.split("\t"), ""][:3]
            rows.append((cat, text, expected))
    codeswitch = [
        line.strip()
        for line in CODESWITCH.read_text("utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    rows = (
        [r for r in rows if r[0] != "latin"]
        + [("latin", t, "") for t in codeswitch]
        + [r for r in rows if r[0] == "latin"]
    )
    dialogue, yo = novel_items(morph)
    rows += [("yo", s, words) for s, words in yo]
    rows += [("dialogue", p, "") for p in dialogue]
    rows.append(("poem", POEM, ""))

    prefix = {
        "homograph": "h",
        "number": "n",
        "latin": "l",
        "yo": "y",
        "dialogue": "d",
        "poem": "p",
    }
    counters: dict[str, int] = {}
    table = ["id\tcategory\ttext\texpected"]
    md = ["# Тестовый набор", "", "## Набор", ""]
    for cat, text, expected in rows:
        counters[cat] = counters.get(cat, 0) + 1
        item = f"{prefix[cat]}{counters[cat]:03d}"
        table.append("\t".join((item, cat, text.replace("\n", "\\n"), expected)))
        if cat == "poem":
            md.append("\n\n".join(s.replace("\n", "\\\n") for s in text.split("\n\n")))
        else:
            md.append(text)
        md.append("")
    (HERE / "set.tsv").write_text("\n".join(table) + "\n", "utf-8")
    (HERE / "bench.md").write_text("\n".join(md), "utf-8")
    print(f"{sum(counters.values())} items:", counters)


if __name__ == "__main__":
    main()
