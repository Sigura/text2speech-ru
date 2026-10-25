"""Split plan (§6.8.1, FR-20): table-driven on synthetic section durations (§12)."""

from __future__ import annotations

import pytest

from text2audio.audio.split import Item, SplitSettings, plan_split

MIN = 60_000


def book(*sections: tuple[int, str, float], paragraphs: int = 4) -> list[Item]:
    """(heading level, title, minutes) per section → items: a heading + paragraphs."""
    items = [Item(2000, 1, "Книга")]
    for level, title, minutes in sections:
        items.append(Item(1000, level, title))
        per = int(minutes * MIN / paragraphs)
        items += [Item(per) for _ in range(paragraphs)]
    return items


def titles(plan: object) -> list[str]:
    return [p.title for p in plan.parts]  # type: ignore[attr-defined]


def test_clean_chapters_one_file_each() -> None:
    plan = plan_split(book(*[(2, f"Глава {n}", 20) for n in range(1, 6)]), "Книга")
    assert not plan.single
    assert titles(plan) == [f"Глава {n}" for n in range(1, 6)]
    assert plan.level == 2


def test_tiny_front_matter_merges_into_next_section() -> None:
    items = book((2, "Посвящение", 0.5), (2, "Глава 1", 20), (2, "Глава 2", 20))
    plan = plan_split(items, "Книга")
    assert titles(plan) == ["Глава 1", "Глава 2"]
    assert plan.parts[0].start == 0  # book title and dedication are in the first file
    assert plan.merged == 2  # the title block and the dedication


def test_last_short_section_merges_into_previous() -> None:
    plan = plan_split(book((2, "Глава 1", 20), (2, "Глава 2", 20), (2, "Эпилог", 1)), "Книга")
    assert titles(plan) == ["Глава 1", "Глава 2"]
    assert plan.parts[-1].end == len(book((2, "a", 1), (2, "b", 1), (2, "c", 1)))


def test_giant_chapter_split_at_paragraphs() -> None:
    items = book((2, "Глава 1", 20), (2, "Глава 2", 200), paragraphs=40)
    plan = plan_split(items, "Книга", part_title="{title} (часть {n} из {total})")
    assert titles(plan)[0] == "Глава 1"
    assert titles(plan)[1:] == [f"Глава 2 (часть {n} из 5)" for n in range(1, 6)]
    assert all(p.duration_ms <= 90 * MIN for p in plan.parts)


def test_giant_part_split_at_its_chapters() -> None:
    items = book(
        (2, "Часть 1", 0.1),
        *[(3, f"Глава {n}", 30) for n in range(1, 5)],
        (2, "Часть 2", 0.1),
        *[(3, f"Глава {n}", 30) for n in range(5, 9)],
    )
    plan = plan_split(items, "Книга")
    assert not plan.single
    assert titles(plan)[:2] == ["Глава 1", "Глава 2"]  # 30-minute chapters: one file each
    assert len(plan.parts) == 8
    assert all(p.duration_ms <= 90 * MIN for p in plan.parts)


def test_hundreds_of_spurious_headings_fall_back_to_one_file() -> None:
    items = book(*[(2, f"Строка {n}", 0.2) for n in range(500)])
    plan = plan_split(items, "Книга")
    assert plan.single and len(plan.parts) == 1
    assert "merged" in plan.reason or "fewer" in plan.reason


def test_no_headings_gives_one_file() -> None:
    items = [Item(2000, 1, "Книга"), *[Item(MIN) for _ in range(30)]]
    plan = plan_split(items, "Книга")
    assert plan.single and plan.reason == "no usable headings"


def test_low_heading_confidence_gives_one_file_with_chapters() -> None:
    items = book(*[(2, f"Глава {n}", 20) for n in range(1, 4)])
    plan = plan_split(items, "Книга", heading_confidence=0.0)
    assert plan.single and "confidence" in plan.reason
    assert [c.title for c in plan.chapters][1:] == ["Глава 1", "Глава 2", "Глава 3"]


def test_too_many_chapters_tries_parts_level() -> None:
    sections = []
    for part in range(1, 4):
        sections.append((2, f"Часть {part}", 4.0))
        sections += [(3, f"Глава {part}.{n}", 4.0) for n in range(1, 81)]
    plan = plan_split(book(*sections), "Книга", SplitSettings(max_parts=150))
    # 240 chapters at ###: the parts (##) are 5 h long, so each becomes files of several
    # chapters near the 45-minute target instead of 80 files.
    assert not plan.single
    assert 15 <= len(plan.parts) <= 30
    assert plan.parts[0].title.startswith("Часть 1") or plan.parts[0].title.startswith("Глава 1.1")
    assert all(p.duration_ms <= 90 * MIN for p in plan.parts)


@pytest.mark.parametrize("mode", ["none", "section"])
def test_modes(mode: str) -> None:
    items = book(*[(2, f"Строка {n}", 0.2) for n in range(500)])
    plan = plan_split(items, "Книга", SplitSettings(mode=mode))  # type: ignore[arg-type]
    if mode == "none":
        assert plan.single and plan.reason == "split = none"
    else:
        assert not plan.single  # "section" skips the sanity checks


def test_summary_records_decision() -> None:
    plan = plan_split(book((2, "Глава 1", 20), (2, "Глава 2", 20)), "Книга")
    summary = plan.summary()
    assert summary["mode"] == "split" and summary["files"] == 2 and summary["level"] == 2
