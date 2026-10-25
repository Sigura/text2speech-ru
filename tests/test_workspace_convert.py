"""Book folders (§5.2, FR-08, FR-21) and the `convert` CLI / API (FR-05)."""

from __future__ import annotations

import shutil
from pathlib import Path

from conftest import FIXTURES
from typer.testing import CliRunner

from text2audio import api
from text2audio.cli import ExitCode, app
from text2audio.core.convert import convert_one
from text2audio.core.workspace import folder_name, sanitize_name

FB2 = FIXTURES / "ru/kapitanskaya-dochka.fb2"
QUIRKS = FIXTURES / "quirks/royallib-style.fb2"


def _copy(src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    return dest


def test_folder_names_are_sanitised_not_transliterated() -> None:
    assert folder_name(Path("Война и мир.fb2.zip")) == "Война и мир"
    assert sanitize_name('a/b:c*d?"e<f>g|h') == "a_b_c_d__e_f_g_h"
    assert sanitize_name("  .hidden. ") == "hidden"
    long = sanitize_name("я" * 100)  # 200 bytes in UTF-8
    assert len(long.encode()) <= 120 and set(long) == {"я"}
    assert folder_name(Path("x.fb2"), "Мой выбор") == "Мой выбор"


def test_layout_and_resume(tmp_path: Path) -> None:
    out = tmp_path / "out"
    first = convert_one(FB2, out)
    folder = out / "kapitanskaya-dochka"
    assert first.folder == folder and not first.resumed
    assert (folder / "kapitanskaya-dochka.md").exists()
    assert (folder / "_work/source.fb2").read_bytes() == FB2.read_bytes()
    assert (folder / "_work/book.converted.md").exists()
    again = convert_one(FB2, out)
    assert again.folder == folder and again.resumed and again.status == "unchanged"


def test_name_clashes(tmp_path: Path) -> None:
    out = tmp_path / "out"
    a = convert_one(_copy(FB2, tmp_path / "in1/book.fb2"), out)
    b = convert_one(_copy(QUIRKS, tmp_path / "in2/book.fb2"), out)  # same name and format
    c = convert_one(_copy(FIXTURES / "ru/kapitanskaya-dochka.fb2.zip", tmp_path / "book.zip"), out)
    assert a.folder is not None and a.folder.name == "book"
    assert b.folder is not None and b.folder.name == "book (2)"
    assert c.folder is not None and c.folder.name == "book (fb2-zip)"  # other format
    d = convert_one(tmp_path / "in2/book.fb2", out, overwrite=False)
    assert d.folder == b.folder and d.resumed  # same source finds its folder again
    e = convert_one(tmp_path / "in2/book.fb2", out / "other", overwrite=True)
    assert e.folder is not None and e.folder.name == "book"


def test_overwrite_replaces_a_different_source(tmp_path: Path) -> None:
    out = tmp_path / "out"
    convert_one(_copy(FB2, tmp_path / "a/book.fb2"), out)
    result = convert_one(_copy(QUIRKS, tmp_path / "b/book.fb2"), out, overwrite=True)
    assert result.folder == out / "book" and result.status == "converted"
    assert "Станция" in (out / "book/book.md").read_text(encoding="utf-8")


def test_reconvert_keeps_edits(tmp_path: Path) -> None:
    out = tmp_path / "out"
    folder = convert_one(FB2, out).folder
    assert folder is not None
    book = folder / "kapitanskaya-dochka.md"
    # Untouched → reconversion simply rewrites (here: unchanged).
    assert convert_one(folder, out, reconvert=True).status == "unchanged"
    # Edited → never overwritten; a changed converter output goes next to it.
    edited = book.read_text(encoding="utf-8").replace("Вожатый", "Вожатый (правка)")
    book.write_text(edited, encoding="utf-8")
    (folder / "_work/book.converted.md").write_text("older converter output\n", encoding="utf-8")
    result = convert_one(folder, out, reconvert=True)
    assert result.status == "kept-edits"
    assert book.read_text(encoding="utf-8") == edited
    reconverted = folder / "kapitanskaya-dochka.reconverted.md"
    assert result.markdown == reconverted and "## Глава II. Вожатый" in reconverted.read_text(
        encoding="utf-8"
    )


def test_reconvert_needs_a_book_folder(tmp_path: Path) -> None:
    result = convert_one(tmp_path, tmp_path / "out", reconvert=True)
    assert result.status == "failed" and "not a book folder" in (result.error or "")


def test_batch_continues_after_a_failure(tmp_path: Path) -> None:
    books = tmp_path / "books"
    _copy(FB2, books / "a.fb2")
    _copy(FIXTURES / "evil/xxe.fb2", books / "b.fb2")
    _copy(QUIRKS, books / "c.fb2")
    (books / "notes.jpg").write_bytes(b"ignored: not a book suffix")
    results = api.convert([books], tmp_path / "out")
    assert [(r.input.name, r.status) for r in results] == [
        ("a.fb2", "converted"),
        ("b.fb2", "failed"),
        ("c.fb2", "converted"),
    ]


def test_cli_matches_api_and_reports_failures(tmp_path: Path) -> None:
    runner = CliRunner()
    ok = runner.invoke(app, ["convert", str(FB2), "-o", str(tmp_path / "cli")])
    assert ok.exit_code == ExitCode.OK, ok.output
    assert "kapitanskaya-dochka.md — fb2, 2 chapter(s)" in ok.output
    via_api = api.convert([FB2], tmp_path / "api")
    assert via_api[0].markdown is not None
    assert (tmp_path / "cli/kapitanskaya-dochka/kapitanskaya-dochka.md").read_text(
        encoding="utf-8"
    ) == via_api[0].markdown.read_text(encoding="utf-8")
    bad = runner.invoke(app, ["convert", str(FIXTURES / "evil/xxe.fb2"), "-o", str(tmp_path)])
    assert bad.exit_code == ExitCode.INPUT
    two = runner.invoke(app, ["convert", str(FB2), str(QUIRKS), "--name", "x"])
    assert two.exit_code == ExitCode.USAGE
