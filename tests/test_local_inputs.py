"""Smoke test on the owner's real books in `inputs/` (never committed; skipped in CI)."""

from __future__ import annotations

from pathlib import Path

import pytest

from text2audio.core.convert import convert_one

INPUTS = Path(__file__).parent.parent / "inputs"
BOOKS = sorted(p for p in INPUTS.glob("*") if p.is_file()) if INPUTS.is_dir() else []


@pytest.mark.skipif(not BOOKS, reason="no local books in inputs/")
@pytest.mark.parametrize("book", BOOKS, ids=[p.name for p in BOOKS])
def test_local_book_converts(book: Path, tmp_path: Path) -> None:
    result = convert_one(book, tmp_path)
    assert result.status == "converted", result.error
    assert result.chapters > 0 and result.markdown is not None
    text = result.markdown.read_text(encoding="utf-8")
    assert "royallib" not in text.lower()
    assert text.startswith("# ")
