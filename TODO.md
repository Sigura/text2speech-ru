# TODO

Roadmap checklist and improvement ideas. Milestones and their exit criteria are
defined in [SPEC.md §11](SPEC.md); open decisions live in the
[README](README.md#open-questions).

## Roadmap

- [x] **M0 — Scaffold:** CLI, plugins, model store, offline contract, worker protocol,
  resource planning, CI.
- [x] **Spike `ru-smoke`:** RUAccent + Silero v5.6 (female) + Piper + GigaAM, fully
  offline, GPU and 4-core CPU ([findings](spikes/ru-smoke/FINDINGS.md)); Russian
  weights pinned in `models.lock`; `install.sh`.
- [x] **Spike `ru-polish`:** two-stage speech and English words in Russian text
  ([findings](spikes/ru-polish/FINDINGS.md)) → GPU hybrid Silero → Chatterbox VC,
  Chatterbox Multilingual for sentences with English words; narrator `xenia`; FR-24.
- [ ] Stress listening: Silero's own stress vs RUAccent marks (`spikes/ru-smoke/out/listen/`).
- [x] **M1 — Conversion:** FB2 (cp1251, `.fb2.zip`), EPUB 2/3, TXT ("Глава 1"), PDF
  (pymupdf4llm + layout, [benchmark](spikes/pdf-readers/FINDINGS.md)), MD → `<book>.md`;
  metadata; embedded cover; segment manifest; `convert --reconvert`; golden tests with a
  Russian fixture per format. Follow-ups:
  - [ ] `--reader` and a reader plugin group once a second PDF reader exists (Docling);
  - [ ] PDF per-file time limit (§6.12); OCR for scanned PDFs (v2);
  - [x] restore compound hyphens lost at PDF line ends (`где-нибудь`) — `ru` normalization, M2;
  - [ ] English TXT headings ("Chapter 1") — with the `en` pack, M4.
- [ ] **M2 — Russian speech:** `lang-ru` pack (stress, ё, number agreement, dialogue
  dashes, English words — FR-24), Silero worker (CPU) and Chatterbox worker (VC + TTS,
  GPU) with the hybrid route, benchmark against Qwen3-TTS / ESpeech / XTTS-v2,
  per-backend environments, MP3 + split plan, QA gate, resume, covers (SD WebUI +
  typographic); extract the language-pack template.
  - [x] step 1 — first audiobook on CPU: normalization, RUAccent, Silero worker, plugin
    environments, assembly + split plan + MP3 tags, `run` / `synthesize`, incremental
    re-render and resume;
  - [x] step 2 — QA gate (GigaAM `rnnt`, WER + signal checks, retries, keep-best,
    `--qa`), kill-and-restart resume test, stage timings and parallel encoding, `.srt`
    subtitles and `report.md`, full-novel T0 run;
  - [x] step 3a — FR-24 Latin-span stage (lexicon, acronyms, homoglyphs, espeak-ng
    transliteration on T0, `foreign_spans` in the report);
  - [x] step 3b — GPU hybrid (Silero → Chatterbox VC, Chatterbox TTS for Latin words);
  - [x] T1 full-novel run (`tests/e2e/test_novel.py::test_t1`);
  - [ ] QA limit: at most 100 segments checked per run, polish/QA in batches of ≤ 100
    (SPEC §6.9);
  - [ ] step 4 — covers, `preview`, PDF compound hyphens, benchmark, language-pack template:
    - [x] 4a — PDF compound hyphens (`ru` normalization);
- [ ] **M3 — Expression:** pauses, dialogue voice, author's remarks in the narrator
  voice, `<!-- t2a: … -->` directives, emotion classifier.
- [ ] **M4 — English** (the recipe for any new language, [guide](docs/adding-a-language.md)):
  - [ ] generate `plugins/lang-en` from the template, **with no change to `core/`**;
  - [ ] profile: "Chapter", curly quotes, abbreviations (*Mr., St., e.g.*), years and
    ordinals, Roman numerals;
  - [ ] components: misaki + espeak-ng G2P, NeMo TN or rules, faster-whisper QA;
  - [ ] backends: Kokoro (fast), Chatterbox (expressive), IndexTTS-2 benchmarked;
  - [ ] `models.lock` entries with `langs = ["en"]`; golden fixtures per format;
  - [ ] female narrator + contrasting dialogue voice; listening test; record in
    SPEC §13.1.
- [ ] **M5 — Hardening:** air-gapped bundle, security tests, docs.

## Housekeeping

- [ ] Add a private git remote for backup: the repository exists only on this disk.
- [ ] Install a system FFmpeg (`sudo apt install ffmpeg`); the uv environment
  currently borrows it from the conda env `t110`.
- [ ] Decide on strict local offline tests (AppArmor sysctl, README open question 5).

## Improvement ideas

Suggestions, not commitments. Each names the milestone where it would fit.

| # | Idea | Why | Fits |
|---|---|---|---|
| 1 | **Name and stress review before synthesis.** After conversion, list proper names, rare words and homographs where the stress model is unsure, and write them into `_work/lexicon.tsv` with the proposed stress for a quick check. | Names are the most common stress error in audiobooks, and fixing one lexicon line beats re-listening to hours of audio. Builds on FR-13. | M2 |
| 2 | **Stress marks in `<book>.md`.** Let the user write `зам+ок` or `за́мок` directly in the Markdown; the pack respects them and strips them from the display text. | The simplest fix for a single wrong word, right where the user reads. SPEC already says source marks are respected; make it a tested feature. | M2 |
| 3 | **CPU for Silero, GPUs for heavier work.** Silero runs 12× real time on 4 cores, so put it on CPU slots and keep the GPUs for the expressive model, polishing and GigaAM. | The planner currently prefers GPUs for any backend; per-backend device preference makes better use of the machine and leaves room for the other jobs on these GPUs. | M2 |
| 4 | **`run --dry-run` estimate.** Before rendering, print segment count, audio hours and wall time from the resource plan and measured RTF. | Know whether a 40-hour book takes 20 minutes or 6 hours before starting it. | M2 |
| 5 | **Inbox folder mode.** `text2audio watch ~/Books/inbox` converts every book dropped there, one at a time, into the output root. | Fits a personal library: copy a book in, find the audiobook later. | M5 |
| 6 | **Completion hook.** Optional `on_complete` command in the config (e.g. a script that messages your Telegram bot). The core runs a local command and never talks to the network itself. | Long books finish hours later; a notification is handy, and the offline contract stays intact. | M2 |
| 7 | **Russian regression corpus in CI.** Keep the spike's sentences (plus the M2 benchmark set) as a nightly GPU job with WER and stress thresholds. | Catches regressions when a model, RUAccent or a normalization rule changes. | M2 |
| 8 | **Speaking rate and voice per book.** Expose Silero's SSML `prosody rate` and voice choice in `config show`, with a 30-second `preview` per voice. | Narration speed is personal; trying `xenia`, `baya` and `kseniya` on the actual book is quicker than editing config blind. | M3 |
| 9 | **Batched synthesis requests.** Send several segments per worker request (Silero and GigaAM both batch). | With RTF 0.008, process and protocol overhead per segment dominate; batching keeps the GPU busy. | M3 |
| 10 | **Shared-GPU etiquette.** Re-probe free VRAM before each chapter and back off (fewer workers, or CPU) when another job such as the video server grows. | The GPUs are shared; the plan is a snapshot and VRAM use changes while a book renders. | M3 |
