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
- [ ] Spike follow-up: stress listening (Silero's own stress vs RUAccent marks),
  polish stage (enhancement first; voice conversion once a reference clip is chosen).
- [ ] **M1 — Conversion:** FB2 (cp1251, `.fb2.zip`), EPUB, TXT ("Глава 1"), PDF, MD →
  `<book>.md`; metadata; embedded cover extraction; `convert --reconvert`.
- [ ] **M2 — Russian speech:** `lang-ru` pack (stress, ё, number agreement, dialogue
  dashes), backend benchmark, per-backend environments, MP3 + split plan, QA gate,
  resume, covers (SD WebUI + typographic); extract the language-pack template.
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
- [ ] **M5 — Hardening:** air-gapped bundle, security tests, docs, v1.0.

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
| 8 | **Speaking rate and voice per book.** Expose Silero's SSML `prosody rate` and voice choice in `config show`, with a 30-second `preview` per voice. | Narration speed is personal; trying `xenia`, `baya` and `kseniya` on the actual book is quicker than editing config blind. | M2–M3 |
| 9 | **Batched synthesis requests.** Send several segments per worker request (Silero and GigaAM both batch). | With RTF 0.008, process and protocol overhead per segment dominate; batching keeps the GPU busy. | M2 |
| 10 | **Shared-GPU etiquette.** Re-probe free VRAM before each chapter and back off (fewer workers, or CPU) when another job such as the video server grows. | The GPUs are shared; the plan is a snapshot and VRAM use changes while a book renders. | M2 |
