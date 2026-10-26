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
- M2 step 1 — first audiobook on CPU: `text2audio run` / `synthesize` turn a Russian
  book into MP3s (`report.json`, playlist); `envs sync` / `envs list`.
- `ru` normalization: numbers with case and gender agreement, years, centuries, Roman
  numerals after names and chapter words, dates, times, percentages, currency, units,
  ranges, abbreviations, initials, acronyms (test set of > 300 cases, FR-11); pack
  lexicon (FR-13 levels: pack → user → book).
- Plugins `g2p-ruaccent` (stress + ё, kept offline) and `backend-silero` (`xenia`,
  48 kHz, CPU), each in its own uv environment; stdlib worker runtime with a network
  guard; `Worker` client for components; ADR-0010.
- Audio assembly: edge trimming, generated pauses, split plan (§6.8.1), two-pass
  loudness (−19 LUFS, −3 dBTP), MP3 64 kbps with ID3v2.3 tags, cover and chapters.
- Incremental re-render by segment hash and resume from the render journal (FR-08,
  FR-10); unchanged output files are not re-encoded.
- `doctor` reports plugin environments; `install.sh` creates them.
- Terminal progress for `run` / `synthesize` (FR-17): a plan header, one line per finished
  stage, a live bar with rate and ETA on a terminal (`rich`, stderr only).
- `run` / `synthesize` progress (FR-17): book header and route, one ✓ line per
  finished stage (convert, normalize, stress, synthesize, encode) with counts, cached
  items and time; a live bar for the running stage with rate, ETA, audio rendered
  and × realtime, and a "loading voices" note while workers start. Plain stage lines
  without a terminal; segment failures shown as they happen; Ctrl-C exits 130 with a
  resume hint.
- M2 step 2 — QA gate (§6.9, FR-12): every rendered segment is transcribed by the
  language's QA ASR (plugin `asr-gigaam`: GigaAM-v3 `rnnt`, own environment, GPU when one
  has free VRAM, else CPU), compared with the normalized text (fails only when both WER
  and CER are above their thresholds, so differently spelled names pass) and checked for speaking rate, clipping, long silences and silence; failures are rendered
  again (Silero: own stress on attempt 2), the best attempt is kept, the rest flagged
  in `report.json`, exit code 4. `--qa on|off|sample=N%`; results journaled, so QA
  resumes too.
- Subtitles: a `.srt` with the book text next to every MP3, timed from the assembly
  (`output.subtitles`).
- `report.md`: readable summary next to `report.json` (files, QA flags with what ASR
  heard, time per stage, warnings).
- `report.json`: time per stage (`timings_s`), whole-run `rtf_wall`, `cached` vs
  `duplicate` segments, per-part titles and durations.
- Test fixtures: the `sine` backend injects faults (`T2A_SINE_FAULT`) and has a
  `sine-echo` ASR; a kill-and-restart resume test; `tests/e2e/test_novel.py::test_t0`
  renders the full «Капитанская дочка» (`tests/fixtures/fetch_novel.py`) on 4 cores.
- M2 step 3a — Latin-script words in Russian text (FR-24): the core tags foreign-script
  spans; the `ru` pack fixes Latin look-alike letters in Cyrillic words, reads brands
  and terms from the lexicon (Russian endings after `'` or `-` attach: `iPhone'а`),
  spells acronyms (`HTTP` → «эйчтитипи́») and transliterates the rest through
  `espeak-ng` English IPA. Every span and how it was read is listed in `report.json`
  (`foreign_spans`) and `report.md`; segments carry `spans` in `segments.jsonl`
  (golden manifests regenerated). The QA gate does not score acronym or transliterated
  readings that GigaAM did not write at all and lists them as `unverified`.
- M2 step 3b — Russian T1 hybrid (§6.6.6): plugin `backend-chatterbox` (Chatterbox
  Multilingual TTS and VC in one worker, own environment, weights in `models.lock`);
  `ru` expressive route Silero → Chatterbox VC into the `xenia` reference, Chatterbox
  TTS for segments with transliterated words (read in Latin script). Fast-tier audio is
  reused as VC source; new `polish` stage on a GPU pool; QA retries polish again, then
  keep the unpolished source (`qa.unpolished`); `report.json` `polish`, `report.md`
  line. `[lang.<code>] polish.<tier>` / `foreign.<tier>` overrides. Test fixture
  `sine-polish`; `tests/e2e/test_novel.py::test_t1`.
- SPEC 1.13-M2: T1 `ru` measured on the full novel (`rtf_wall` 0.21, 0.5 % flagged,
  §9); the owner's QA limit of 100 segments per run and batches of ≤ 100 is recorded
  as proposed (§6.9).
- M2 step 4a — the `ru` pack restores compound hyphens lost at PDF line ends
  («понастоящему» → «по-настоящему», «ктото» → «кто-то») for words pymorphy3 does not
  know (SPEC 1.14-M2).
- M2 step 4b — QA limit (§6.9): at most 100 segments checked per run (`[qa]
  max_segments`, `--qa-max`), the first in hash order; polish and QA run in batches of
  ≤ 100 (`[qa] batch`) on worker pools kept loaded across batches; `report.json`
  `qa.sample` / `qa.max_segments`. The novel tests assert `checked == min(100, total)`
  (SPEC 1.15-M2).
- M2 step 4c — covers (§6.8.2): typographic covers (Pillow, bundled DejaVu Serif);
  plugin `cover-sd-webui` starts its own Stable Diffusion WebUI on 127.0.0.1 with a
  private data dir on the first GPU with enough free VRAM; plugin `mt-opus` (Opus-MT
  ru→en, pinned in `models.lock`) translates title and description for the prompt;
  title colour by contrast; covers cached by key; `[cover]` / `[cover.sd]` config;
  `doctor` reports the cover route; new plugin group `translators` (SPEC 1.16-M2).
- M2 step 4d — `text2audio preview <book> [--chapter N] [--seconds 30]` (API
  `api.preview`): a short MP3 of a chapter's opening in `_work/preview/`, no QA; its
  segments are reused by `run` (SPEC 1.17-M2).
- M2 step 4c+ — small embedded covers (< 800 px) are repainted with img2img from
  WD14-tagger tags (plugin `tagger-wd14`, ONNX, pinned in `models.lock`; group
  `taggers`); embedded placeholders under 64 px are replaced (SPEC 1.18-M2).

### Changed

- README rewritten as a solution description with usage examples, open questions and
  a TODO list; Russian narrator voice defaults to female.
- README keeps the solution description, installation, usage and open questions; the
  roadmap checklist and improvement ideas moved to `TODO.md`.
- QA gate ASR model and WER threshold come from the language profile (SPEC.md §6.9).
- SPEC.md 1.8-M2: rutextnorm rejected, lexicon before normalization, plugin worker
  environments, manifest fields and journals, grouping in the split plan, CPU-only
  Silero and a default cap of 8 workers.
- `segments.jsonl` records each heading's level (golden manifests regenerated).
- Output parts are encoded in parallel (two-pass loudnorm is single-threaded): Banks
  "Выбор оружия" (9 h, 18 files) assembles in 3 m 41 s instead of ~23 min; stress runs
  on up to 8 RUAccent workers.
