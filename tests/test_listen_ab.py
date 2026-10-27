"""The M3 listening test (`scripts/listen_ab.py`, SPEC §6.7) without rendering: passage
choice, blind sides and the ≥ 60 % rule."""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "listen_ab.py"
spec = importlib.util.spec_from_file_location("listen_ab", SCRIPT)
assert spec is not None and spec.loader is not None
listen_ab = importlib.util.module_from_spec(spec)
sys.modules["listen_ab"] = listen_ab  # dataclasses look their module up there
spec.loader.exec_module(listen_ab)

DIALOGUE = "— Эх, батюшка Петр Андреич! — отвечал он с глубоким вздохом. — Сержусь-то я."
NARRATION = "Я жил недорослем, гоняя голубей. Между тем минуло мне шестнадцать лет."


def test_paragraphs_keep_plain_prose_only() -> None:
    md = (
        f"# Title\n\n## Глава I\n\n{NARRATION}\n\n> epigraph\n\n"
        f"line one  \nline two\n\n{DIALOGUE}\n"
    )
    assert listen_ab.paragraphs(md) == [NARRATION, DIALOGUE]


def test_e1_candidates_differ_between_levels_and_cost_both() -> None:
    [found] = listen_ab._candidates("e1", [NARRATION, DIALOGUE])
    assert found.text == DIALOGUE
    # E1: line, remark, line; E0 adds the unsplit paragraph
    assert found.cost == 3 + 1


def test_t1_candidates_are_long_narration_only() -> None:
    long = f"{NARRATION} {NARRATION} {NARRATION}"
    assert listen_ab._candidates("t1", [NARRATION, DIALOGUE]) == []
    [found] = listen_ab._candidates("t1", [long, DIALOGUE])
    assert (found.text, found.cost) == (long, 1)


def test_pick_stays_within_the_budget() -> None:
    candidates = [listen_ab.Passage(f"p{n}", cost) for n, cost in enumerate([3, 4, 2, 5, 3, 1])]
    chosen = listen_ab.pick(candidates, pairs=10, budget=9)
    assert sum(p.cost for p in chosen) <= 9
    assert listen_ab.pick(candidates, pairs=10, budget=9) == chosen  # seeded
    assert len(listen_ab.pick(candidates, pairs=2, budget=100)) == 2


def test_sides_are_one_of_each_and_vary() -> None:
    key = listen_ab.draw_sides("e1", 30, random.Random(1))
    assert list(key)[:2] == ["e01", "e02"]
    assert all(sorted(v.values()) == ["E0", "E1"] for v in key.values())
    assert len({v["A"] for v in key.values()}) == 2


def test_read_answers() -> None:
    text = "# pair\tchoice\ne01\tA\nE02 b\n\nt01 =\n"
    assert listen_ab.read_answers(text) == {"e01": "A", "e02": "B", "t01": "="}
    with pytest.raises(ValueError, match="line 1"):
        listen_ab.read_answers("e01 maybe\n")


def _key(n: int) -> dict[str, dict[str, str]]:
    return {f"e{i:02d}": {"A": "E1", "B": "E0"} for i in range(1, n + 1)}


def test_sixty_percent_of_decided_pairs() -> None:
    key = _key(30)
    # 15 for E1, 10 for E0, 5 undecided: 15 / 25 = 60 %
    answers = {p: "A" if i < 15 else "B" if i < 25 else "=" for i, p in enumerate(key)}
    [s] = listen_ab.score(key, answers)
    assert (s.decided, s.new_wins, s.share, s.passed) == (25, 15, 0.6, True)
    answers["e01"] = "B"  # 14 / 25
    [s] = listen_ab.score(key, answers)
    assert not s.passed
    assert "E1 not confirmed" in listen_ab.report([s])


def test_not_passed_with_too_few_or_unanswered_pairs() -> None:
    [few] = listen_ab.score(_key(29), dict.fromkeys(_key(29), "A"))
    assert few.share == 1.0 and not few.passed
    key = _key(30)
    [partial] = listen_ab.score(key, dict.fromkeys(list(key)[:29], "A"))
    assert not partial.passed
    with pytest.raises(ValueError, match="e31"):
        listen_ab.score(key, {"e31": "A"})


def test_comparisons_are_scored_separately() -> None:
    key = _key(30) | {"t01": {"A": "T0", "B": "T1"}, "t02": {"A": "T1", "B": "T0"}}
    answers = dict.fromkeys(_key(30), "A") | {"t01": "A", "t02": "A"}
    e1, t1 = listen_ab.score(key, answers)
    assert (e1.kind, e1.passed) == ("e1", True)
    assert (t1.kind, t1.new_wins, t1.share, t1.passed) == ("t1", 1, 0.5, False)
