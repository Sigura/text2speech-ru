"""The full-novel fixture script (`tests/fixtures/fetch_novel.py`) without the download."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "fixtures" / "fetch_novel.py"
spec = importlib.util.spec_from_file_location("fetch_novel", SCRIPT)
assert spec is not None and spec.loader is not None
fetch_novel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_novel)


def test_latin1_byte_in_koi8r_original_is_restored() -> None:
    koi8 = "приехал в Россию ".encode("koi8-r") + "pour \xeatre outchitel".encode("latin-1")
    text = fetch_novel.clean(koi8.decode("koi8-r"))
    assert text == "приехал в Россию pour être outchitel"


def test_detached_quote_at_paragraph_end_is_dropped_when_unpaired() -> None:
    assert fetch_novel.clean('— отвечал я с твердостию. "') == "— отвечал я с твердостию."
    assert fetch_novel.clean('Секретарь продолжал: "') == "Секретарь продолжал:"


def test_detached_closing_quote_is_attached_when_paired() -> None:
    assert fetch_novel.clean('"Шуба лисья, 40 рублей. "') == '"Шуба лисья, 40 рублей."'


def test_attached_quotes_are_kept() -> None:
    assert fetch_novel.clean('"Мы поспорили".') == '"Мы поспорили".'
    assert fetch_novel.clean('Отец твой А. Г."') == 'Отец твой А. Г."'


@pytest.mark.parametrize("charset", ["koi8-r", "windows-1251"])
def test_digest_does_not_depend_on_the_charset_the_server_sends(charset: str) -> None:
    page = "<h2>Капитанская дочка</h2>\n"
    served = (page + fetch_novel.FOOTER + ": 60").encode(charset)
    assert fetch_novel.book_text(served, charset) == page
    assert fetch_novel.book_digest(page) == hashlib.sha256(page.encode("koi8-r")).hexdigest()
