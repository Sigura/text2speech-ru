"""Download the full public-domain novel for the M2 end-to-end renders (not committed).

    uv run python tests/fixtures/fetch_novel.py

Pushkin, «Капитанская дочка» (1836), from lib.ru (koi8-r text with HTML headings),
pinned by SHA-256 and rewritten as a Markdown book (§6.4.1) in
`tests/fixtures/ru/large/kapitanskaya-dochka.md`. Network use: this script only
(test data, like `models pull`). lib.ru typography is cleaned up for reading aloud:
justification spaces collapsed, ` - ` dashes made em dashes, the editors' note markers
`(1)` and the notes themselves (`(сноска 1) …`, variant readings of the draft chapter)
dropped.
"""

from __future__ import annotations

import hashlib
import html
import re
import sys
import urllib.request
from pathlib import Path

URL = "http://lib.ru/LITRA/PUSHKIN/kapitan.txt"
# Of the book text: the page footer below it carries a view counter that changes.
SHA256 = "0cf756a5c1d97933a69535664fc08a21efd1002fcca13ffe5f8eff8005f98952"
FOOTER = "<pre><hr noshade><small>Популярность".encode("koi8-r")
OUT = Path(__file__).parent / "ru" / "large" / "kapitanskaya-dochka.md"
TITLE = "Александр Пушкин. Капитанская дочка"


def chapter_title(text: str) -> str:
    """'ГЛАВА I. СЕРЖАНТ ГВАРДИИ' → 'Глава I. Сержант гвардии'."""
    words = text.lower().split()
    out = [w.upper() if re.fullmatch(r"[ivxl]+\.?", w) else w for w in words]
    text = " ".join(out)
    return re.sub(r"(^|\. )(\w)", lambda m: m[1] + m[2].upper(), text)


def clean(text: str) -> str:
    text = re.sub(r"\(\d+\)", "", text)  # editors' note markers
    text = re.sub(r"(^|\s)-(?=\s)", r"\1—", text)
    return re.sub(r"\s+", " ", text).strip()


def to_markdown(raw: str) -> str:
    body = raw.split("</h2></ul>", 1)[1]  # after the title heading
    body = re.sub(r"(?s)-{20,}.*?-{20,}", "", body, count=1)  # OCR credits
    end = body.find("<!--")
    body = body[:end] if end > 0 else body
    lines = [f"# {TITLE}", ""]
    paragraph: list[str] = []

    def flush() -> None:
        text = clean(" ".join(paragraph))
        if text and not text.startswith("(сноска"):
            lines.extend([text, ""])
        paragraph.clear()

    for line in body.splitlines():
        heading = re.search(r"<h2>(.*?)</h2>", line)
        text = html.unescape(re.sub(r"<[^>]+>", "", line)).strip()
        if heading:
            flush()
            title = html.unescape(re.sub(r"<[^>]+>", "", heading[1])).strip().rstrip(".")
            lines.extend([f"## {chapter_title(clean(title))}", ""])
        elif not text or set(text) <= {".", " ", "-"}:
            flush()
        elif line.startswith("     "):  # an indented line starts a paragraph
            flush()
            paragraph.append(text)
        else:
            paragraph.append(text)
    flush()
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    if OUT.is_file() and "--force" not in sys.argv:
        print(f"{OUT} exists (--force to rebuild)")
        return 0
    # lib.ru recodes by client; this User-Agent gets the original koi8-r bytes.
    request = urllib.request.Request(URL, headers={"User-Agent": "curl/8.5.0"})
    with urllib.request.urlopen(request, timeout=120) as resp:
        data = resp.read()
    data = data.split(FOOTER)[0]
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        print(f"SHA-256 mismatch: {digest}", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(to_markdown(data.decode("koi8-r")), encoding="utf-8")
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
