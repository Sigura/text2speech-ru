# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Project specification ([SPEC.md](SPEC.md)), approved for milestone M0.
- README, CONTRIBUTING, CLAUDE.md, architecture decision records 0001–0007 and the
  language-pack guide.
- ADR-0008: Russian is the first language (M2), English second (M4); SPEC.md 1.2-M0.
- M0 scaffold: `text2audio` CLI and Python API with `doctor`, `resources`, `plugins`,
  `models list/verify/pull`, `version` (other commands report their milestone).
- Plugin discovery via entry points for every pipeline stage and covers.
- Model store with `models.lock` (pinned revision + SHA-256) and `models pull`.
- Offline contract: HF offline flags and caches redirected into the model store;
  socket-free test suite and `scripts/test-offline.sh`.
- Backend worker protocol 1 (NDJSON over stdin/stdout) with crash restart, and a
  `sine` dummy backend.
- Resource planning: usable CPU/RAM/GPU within affinity, cgroup and
  `CUDA_VISIBLE_DEVICES` limits; worker slots from free VRAM with NUMA-pinned CPUs.
- ADR-0009 and SPEC.md 1.3-M0: covers (embedded → local Stable Diffusion WebUI →
  typographic) and the loopback-only local-service exception.
- SPEC.md: the editable `<book>.md` lives next to the audio; the original input is
  copied into `_work/` for `convert --reconvert`; `.md` input accepted.
- `install.sh`: uv environment, system tool check, every pinned weight for the chosen
  languages/tiers, `doctor`.
- `models.lock` supports `url` assets (content-addressed) and pins the Russian
  assets found by the `ru-smoke` spike (Silero v5.6, Piper irina, RUAccent, GigaAM-v3).
- `spikes/ru-smoke`: offline Russian smoke test and findings.
- `text2audio convert` (M1, FR-01–03, FR-05, FR-08, FR-21): FB2 / `.fb2.zip` → book
  folder with the editable `<book>.md`, `_work/source.*`, `book.converted.md`,
  `metadata.json` and the embedded cover; detection by content; resume, name clashes,
  batch folders, `--reconvert` that never overwrites an edited `<book>.md`. Library
  banners outside the FB2 schema are dropped, `* * *` becomes a scene break, a lone
  wrapper section is unwrapped. Safe input: defusedxml, ZIP limits.
- TXT and `.md` input (M1): encoding detection (UTF-8/16, cp1251, KOI8-R), three
  paragraph layouts (blank lines, lib.ru indents, line per paragraph), headings from
  the language pack, contents lists skipped, heading confidence in `metadata.json`;
  `.md` kept verbatim; `convert --lang`. First language pack `plugins/lang-ru`
  (text structure only; installed with the core via the `langs` dependency group).
- EPUB 2/3 input (M1): chapters from the nav document or NCX (levels by depth, in-text
  duplicates folded), XHTML headings otherwise; footnotes from `epub:type` semantics
  and EPUB 2 numbered links (back-links ignored, notes never read in place);
  epigraphs, poems, OPF metadata (series via `belongs-to-collection` or calibre,
  ISBN, translators) and cover; empty headings dropped.
- PDF input (M1): pymupdf4llm 1.28.2 with its bundled layout model (chosen by the
  `spikes/pdf-readers` benchmark), then clean-up: paragraphs and words split by page
  breaks re-joined, page numbers and repeated running headers dropped, plain headings
  with chapters at `##`; scanned and password-protected PDFs refused. A Markdown →
  Document parser (markdown-it-py) feeds the PDF output through the same writer.
- Segment manifest `_work/segments.jsonl` (M1, §6.4.2) on every conversion, always
  from the current (possibly edited) `<book>.md`: title, headings, prose chunked at
  sentence boundaries (≤ 300 characters), quotes, poem stanzas, footnotes at the end
  of their chapter, default pauses. Rule-based sentence splitter (dialogue remarks,
  initials and the language pack's abbreviations stay inside a sentence) instead of
  pySBD, which split Russian dialogue wrongly.
- SPEC.md 1.7-M1: readers as built (§6.3), defusedxml without lxml (§6.12), PDF
  default reader, rule-based sentence splitting (§6.5), `ru` pack from M1; README
  usage for `convert`; `spikes/pdf-readers` benchmark.
- `spikes/ru-polish`: two-stage synthesis spike (enhancement, Chatterbox VC, Chatterbox
  Multilingual TTS) and Russian text with English terms; SPEC.md 1.5-M0 records the
  hybrid Russian T1 pipeline, 1.6-M0 adds FR-24 (foreign-script spans) and the `xenia`
  narrator voice.
- CI (lint, format, types, tests, strict offline job, requirements freshness) and
  pre-commit hooks.
- `environment.yaml` (conda) and `requirements.txt` / `requirements-dev.txt` (pip),
  exported from `uv.lock` by `scripts/export-requirements.sh`.

### Changed

- README rewritten as a solution description with usage examples, open questions and
  a TODO list; Russian narrator voice defaults to female.
- README keeps the solution description, installation, usage and open questions; the
  roadmap checklist and improvement ideas moved to `TODO.md`.
- QA gate ASR model and WER threshold come from the language profile (SPEC.md §6.9).
