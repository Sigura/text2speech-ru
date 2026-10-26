"""The full-novel fixture script (`tests/fixtures/fetch_novel.py`) without the download."""

from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).parent / "fixtures" / "fetch_novel.py"
spec = importlib.util.spec_from_file_location("fetch_novel", SCRIPT)
assert spec is not None and spec.loader is not None
fetch_novel = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fetch_novel)


def test_latin1_byte_in_koi8r_original_is_restored() -> None:
    koi8 = "приехал в Россию ".encode("koi8-r") + "pour \xeatre outchitel".encode("latin-1")
    text = fetch_novel.clean(koi8.decode("koi8-r"))
    assert text == "приехал в Россию pour être outchitel"
