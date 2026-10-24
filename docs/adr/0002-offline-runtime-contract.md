# 0002. Offline runtime contract

- **Status:** Accepted
- **Date:** 2026-10-24
- **Spec references:** §6.10, FR-04, FR-16, NFR-01, NFR-04

## Context

Users want private conversion of their books, and some machines are air-gapped. Many
ML libraries download weights or data silently on first use (Hugging Face Hub,
`torch.hub`, NLTK, spaCy, Docling layout models, RUAccent, GigaAM), which breaks
offline use in ways that only show up later.

## Decision

- All network activity happens in `install.sh`, `text2audio models pull` and
  `text2audio bundle`. Runtime code never opens a network connection.
- `models.lock` pins every runtime asset: repository, commit revision, files, SHA-256
  and license. Assets are loaded explicitly from the local model store.
- The CLI sets `HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `HF_DATASETS_OFFLINE` and
  points all caches at the model store before importing any ML library.
- CI runs the full suite with networking disabled (`unshare -rn` + `pytest-socket`).
- `bundle export/import` supports machines that never touch the internet.

## Consequences

- Every new component must declare all of its assets; "it downloads automatically" is
  a bug.
- Install is larger and slower; runtime is predictable and private.
- Missing assets fail fast with an error naming the command that fixes it.
