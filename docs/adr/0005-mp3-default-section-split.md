# 0005. MP3 default with per-section split and single-file fallback

- **Status:** Accepted
- **Date:** 2026-10-24
- **Spec references:** §5.2, §6.8, §6.8.1, FR-09, FR-20, FR-21

## Context

M4B is the standard audiobook format, but MP3 plays everywhere. Per-section files make
navigation easy in any player, but detected structure is not always trustworthy
(TXT/PDF heuristics can find no headings, or a "heading" every paragraph).

## Decision

- Default output is MP3; M4B, Opus and WAV are optional.
- Each input book gets its own folder under the output root, named after the input
  file; work files live in `_work/`.
- In `split = "auto"` mode the book is split per section, after merging short sections
  and splitting long ones; if the sanity checks fail (too few/many parts, too many
  merges, low heading confidence) the book is written as one file.
- The split decision and its reason are recorded in `report.json`.

## Consequences

- Split thresholds need tuning on real books (defaults in §6.8.1).
- Single-file MP3 relies on ID3 chapter frames, which not every player shows.
