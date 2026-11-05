"""Download the full public-domain English novel for M4 renders (not committed).

    uv run python tests/fixtures/fetch_novel_en.py

Arthur Conan Doyle, "The Adventures of Sherlock Holmes" (1892), Project Gutenberg
eBook #1661, pinned by the SHA-256 of the plain-text file and rewritten as a Markdown
book (§6.4.1) in `tests/fixtures/en/large/adventures-of-sherlock-holmes.md`: the twelve
stories are `##` headings, their numbered parts `###`. Network use: this script only
(test data, like `models pull`). The Gutenberg header, contents and license are dropped,
`_italics_` become Markdown emphasis and "£ 4" loses its typesetting space.
"""

from __future__ import annotations

import hashlib
import re
import sys
import urllib.request
from pathlib import Path

URL = "https://www.gutenberg.org/cache/epub/1661/pg1661.txt"
SHA256 = "922e2a12ccb43a4c9544c260b2166c6ad2097aeb5957faeee113f173bb857cd0"
OUT = Path(__file__).parent / "en" / "large" / "adventures-of-sherlock-holmes.md"
TITLE = "Arthur Conan Doyle. The Adventures of Sherlock Holmes"
START = "*** START OF THE PROJECT GUTENBERG EBOOK"
END = "*** END OF THE PROJECT GUTENBERG EBOOK"
STORY = re.compile(r"[IVX]+\. [A-Z][A-Z’' -]+")
PART = re.compile(r"[IVX]+\.")
SMALL = {"a", "an", "and", "in", "of", "the", "with"}


def story_title(line: str) -> str:
    """'VI. THE MAN WITH THE TWISTED LIP' → 'VI. The Man with the Twisted Lip'."""
    number, words = line.split(" ", 1)
    out = []
    for i, w in enumerate(words.lower().split()):
        out.append(w if i and w in SMALL else w[0].upper() + w[1:])
    return f"{number} {' '.join(out)}"


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"_([^_]+)_", r"*\1*", text)
    return re.sub(r"£ (?=\d)", "£", text)


def to_markdown(raw: str) -> str:
    body = raw.split(START, 1)[1].split("\n", 1)[1].split(END, 1)[0]
    lines = body.splitlines()
    first = next(i for i, line in enumerate(lines) if STORY.fullmatch(line.strip()))
    lines = lines[first:]  # the first story heading after the contents
    out = [f"# {TITLE}", ""]
    paragraph: list[str] = []

    def flush() -> None:
        text = clean(" ".join(paragraph))
        if text:
            out.extend([text, ""])
        paragraph.clear()

    for line in lines:
        text = line.strip()
        if STORY.fullmatch(text):
            flush()
            out.extend([f"## {story_title(text)}", ""])
        elif PART.fullmatch(text):
            flush()
            out.extend([f"### {text}", ""])
        elif not text:
            flush()
        else:
            paragraph.append(text)
    flush()
    return "\n".join(out).rstrip() + "\n"


def main() -> int:
    if OUT.is_file() and "--force" not in sys.argv:
        print(f"{OUT} exists (--force to rebuild)")
        return 0
    request = urllib.request.Request(URL, headers={"User-Agent": "curl/8.5.0"})
    with urllib.request.urlopen(request, timeout=120) as resp:
        data = resp.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256:
        print(f"SHA-256 mismatch: {digest}", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(to_markdown(data.decode("utf-8-sig").replace("\r\n", "\n")), encoding="utf-8")
    print(OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
