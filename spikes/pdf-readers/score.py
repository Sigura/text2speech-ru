"""Spike step 4 (any env, stdlib only): score reader output against the ground truth.

    python3 spikes/pdf-readers/score.py   →  out/report.md

Per reader and book:
- word recall / precision: aligned word sequences (difflib) against truth.md, markup removed;
- header leaks: lines equal to the running header; page numbers: lines that are a number;
- headings: truth `##` headings found as a heading line (any level) in the output;
- broken words: output tokens that are a truth word cut at a line end ("Симб", "ирской");
  wrong joins: hyphenated truth words that lost their hyphen ("премьермайором");
- seconds per page.
"""

from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "out"
READERS = ["pymupdf4llm", "pymupdf4llm-classic", "docling", "marker"]
WORD = re.compile(r"\w+(?:-\w+)*")


def words(md: str) -> list[str]:
    md = re.sub(r"\[\^\d+\]", "", md)
    md = md.replace("­", "")
    return WORD.findall(md)


def heading_texts(md: str) -> list[str]:
    out = []
    for line in md.splitlines():
        if line.lstrip().startswith("#"):
            out.append(" ".join(WORD.findall(line)).lower())
    return out


def pages(name: str) -> int:
    data = (OUT / "pdf" / f"{name}.pdf").read_bytes()
    return max(1, len(re.findall(rb"/Type\s*/Page[^s]", data)))


def score(reader: str, name: str) -> dict[str, float] | None:
    path = OUT / reader / f"{name}.md"
    if not path.exists():
        return None
    truth_md = (OUT / "src" / f"{name}.truth.md").read_text(encoding="utf-8")
    got_md = path.read_text(encoding="utf-8")
    truth, got = words(truth_md), words(got_md)
    matcher = difflib.SequenceMatcher(None, truth, got, autojunk=False)
    matched = sum(b.size for b in matcher.get_matching_blocks())
    header = json.loads((OUT / "src" / f"{name}.json").read_text())["header"]
    lines = [line.strip(" *#_") for line in got_md.splitlines()]
    truth_set = set(truth)
    hyphenated = {w for w in truth if "-" in w}
    broken = sum(1 for w in got if w not in truth_set and any(t.startswith(w) or t.endswith(w) for t in truth_set if len(t) > len(w) + 2) and len(w) >= 2)
    wrong_join = sum(1 for w in got if w.replace("-", "") in {h.replace("-", "") for h in hyphenated} and w not in hyphenated)
    truth_heads = [
        " ".join(WORD.findall(line)).lower() for line in truth_md.splitlines() if line.startswith("## ")
    ]
    got_heads = heading_texts(got_md)
    timings = json.loads((OUT / reader / "timings.json").read_text())
    return {
        "recall": matched / len(truth),
        "precision": matched / max(1, len(got)),
        "header_leaks": sum(1 for line in lines if line == header) - truth_md.count(f"\n{header}\n"),
        "page_numbers": sum(1 for line in lines if re.fullmatch(r"\d{1,4}", line)),
        "headings": sum(1 for h in truth_heads if h in got_heads) / max(1, len(truth_heads)),
        "broken_words": broken,
        "wrong_joins": wrong_join,
        "s_per_page": timings["files"][name] / pages(name),
    }


def main() -> None:
    names = sorted(p.stem for p in (OUT / "pdf").glob("*.pdf"))
    lines = ["# PDF reader benchmark", ""]
    for name in names:
        lines += [
            f"## {name} ({pages(name)} pages)",
            "",
            "| reader | recall | precision | header leaks | page numbers | headings | broken words | wrong joins | s/page |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for reader in READERS:
            s = score(reader, name)
            if s is None:
                lines.append(f"| {reader} | — | — | — | — | — | — | — | — |")
                continue
            lines.append(
                f"| {reader} | {s['recall']:.3f} | {s['precision']:.3f} | {s['header_leaks']:.0f} | "
                f"{s['page_numbers']:.0f} | {s['headings']:.2f} | {s['broken_words']:.0f} | "
                f"{s['wrong_joins']:.0f} | {s['s_per_page']:.2f} |"
            )
        lines.append("")
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
