"""Side-by-side literary samples for reading: out/<pair>/lit-samples.html.

    MT_BENCH_PAIR=ru-en|en-ru python samples.py [paragraphs]   (default 12)

Each paragraph of the excerpt with every candidate's translation under a letter; the
letter → model key is folded at the end, so the page can be read blind. The letter
order is fixed by a seeded shuffle.
"""

from __future__ import annotations

import html
import random
import sys
from pathlib import Path

from models import MODELS, PAIR, SRC, TGT
from translate import load_set

HERE = Path(__file__).parent
OUT = HERE / "out" / PAIR
BOOKS = {"ru": "Капитанская дочка", "en": "A Scandal in Bohemia"}


def main() -> None:
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    source = load_set("lit")
    found = {
        m.key: (OUT / f"{m.key}.lit.txt").read_text(encoding="utf-8").splitlines()
        for m in MODELS
        if (OUT / f"{m.key}.lit.txt").exists()
    }
    keys = sorted(found)
    random.Random(7).shuffle(keys)
    letters = {k: chr(ord("A") + i) for i, k in enumerate(keys)}
    parts = [
        "<!doctype html><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{SRC}→{TGT} literary samples</title><style>"
        "body{font:16px/1.5 Georgia,serif;max-width:46em;margin:auto;padding:0 16px;"
        "background:#fff;color:#222}"
        ".src{background:#f3f0e8;padding:.6em;border-radius:6px}"
        ".t{margin:.4em 0}.l{font-weight:bold;color:#a33;margin-right:.4em}"
        "h2{font-size:1em;color:#666;margin-top:2em}"
        "@media(prefers-color-scheme:dark){body{background:#181818;color:#ddd}"
        ".src{background:#2a2720}}</style>",
        f"<h1>{BOOKS[SRC]} — {SRC}→{TGT}</h1>"
        f"<p>{count} of the 40 benchmark paragraphs from its opening. Letters are models; "
        "the key is at the end.</p>",
    ]
    for n in range(min(count, len(source))):
        parts.append(f"<h2>¶ {n + 1}</h2><p class='src'>{html.escape(source[n])}</p>")
        for k in keys:
            text = html.escape(found[k][n]) if n < len(found[k]) else "—"
            parts.append(f"<p class='t'><span class='l'>{letters[k]}</span>{text}</p>")
    key = "".join(f"<li>{letters[k]} = {html.escape(k)}</li>" for k in keys)
    parts.append(f"<details><summary>Key</summary><ul>{key}</ul></details>")
    path = OUT / "lit-samples.html"
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
