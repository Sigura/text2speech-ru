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
- [x] **M1 — Conversion:** FB2 (cp1251, `.fb2.zip`), EPUB 2/3, TXT ("Глава 1"), PDF
  (pymupdf4llm + layout, [benchmark](spikes/pdf-readers/FINDINGS.md)), MD → `<book>.md`;
  metadata; embedded cover; segment manifest; `convert --reconvert`; golden tests with a
  Russian fixture per format. Follow-ups:
  - [ ] `--reader` and a reader plugin group once a second PDF reader exists (Docling);
  - [ ] PDF per-file time limit (§6.12); OCR for scanned PDFs (v2);
  - [x] restore compound hyphens lost at PDF line ends (`где-нибудь`) — `ru` normalization, M2;
  - [ ] English TXT headings ("Chapter 1") — with the `en` pack, M4.
- [x] **M2 — Russian speech:** `lang-ru` pack (stress, ё, number agreement, dialogue
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
  - [x] QA limit: at most 100 segments checked per run, polish/QA in batches of ≤ 100
    (SPEC §6.9);
  - [x] step 4 — covers, `preview`, PDF compound hyphens, benchmark, language-pack template:
    - [x] 4a — PDF compound hyphens (`ru` normalization);
    - [x] 4b — QA limit (100 per run) and polish/QA batches;
    - [x] 4c — covers: typographic, SD WebUI plugin, Opus-MT prompt translation;
    - [x] 4c+ — repaint a poor embedded cover (img2img with a WD14-tagger prompt; rule to
      embedded long side < 800 px → img2img at denoise 0.4, agreed 2026-10-26);
    - [x] 4d — `preview`;
    - [x] 4e — language-pack template (`scripts/new_lang.py`);
    - [x] 4f — benchmark ([findings](spikes/ru-bench/FINDINGS.md)): T0 Silero, T1 hybrid;
      Qwen3-TTS → M3 candidate for dialogue/expressive segments;
  - [x] step 5 — M4B of the whole book next to the MP3s, on by default (FR-09);
- [x] **M3 — Expression:** pauses, dialogue (narrator voice `xenia`, owner), author's
  remarks, `<!-- t2a: … -->` directives; E2 deferred (owner, SPEC §13.1).
  - [x] step 0 — carry-overs: `envs sync` names, report route counts and `foreign`
    span label, «pour être»; dialogue voice samples (`spikes/dialogue-voice`);
  - [x] stress listening: RUAccent 2, Silero's own 1, no difference 4 → RUAccent stays;
  - [x] step 1 — pause types instead of milliseconds; voice/role in the segment hash;
  - [x] step 2 — dialogue split (dash paragraphs, author's remarks);
  - [x] step 3 — `<!-- t2a: … -->` directives (E3);
  - [x] step 4 — `voices` and `config show` (FR-22);
  - [x] step 5 — blind A/B listening, E1 vs E0 (≥ 30 pairs) plus T0-vs-T1 narration
    pairs (owner preferred plain Silero on the dialogue samples);
    - [x] `scripts/listen_ab.py`, E0 level, 30 + 20 pairs built;
    - [x] E1 vs E0: 11 of 18 decided pairs (61 %) → E1 stays the default;
    - [x] T1 vs T0: 4 of 15 decided pairs (27 %) → `ru` defaults to the fast tier;
  - [x] step 6 — E2 deferral documented in §6.7;
  - [x] step 7 — Qwen3-TTS dialogue spike: the hybrid won 10 of 10 blind pairs, not
    integrated ([findings](spikes/qwen3-dialogue/FINDINGS.md));
  - [x] step 8 — `ru` text gaps from the benchmark and the listening test:
    - [x] 8a number case and abbreviation dots;
    - [x] 8b pack lexicon (ё in names, «небо», «Зурин»; other homographs are known limits);
    - [x] 8b `test_pack_lexicon_wins_over_ruaccent` passes on real RUAccent;
    - [x] 8b full gate with real models and espeak-ng: 399 passed;
    - [x] 8c stray `"` at paragraph ends in the novel fixture script;
    - [x] 8c local fixture rebuilt; the script follows the charset lib.ru sends.
  - [x] carried over: new lexicon stress («Зурин», «небо», «уже», «кругом», ё in names)
    sounds right to the owner;
  - [ ] carried over: rerun the `slow` full-book renders (`pytest -m slow`) with the new
    lexicon and tier default; `espeak-ng` in the devbox image.
- [x] **M4 — English** (closed 2026-11-06, SPEC 4.0-M5; open items below are carried
  over; the recipe for any new language, [guide](docs/adding-a-language.md)):
  - [x] generate `plugins/lang-en` from the template, no change to `core/` (one approved
    exception for quote dialogue is planned, SPEC §13.1);
  - [x] profile: "Chapter", abbreviations (*Mr., St., e.g.*), fixtures per format;
  - [ ] known limit: a plain-text byline joins the title («… Wonderland. by Lewis
    Carroll», `en/alice.txt` golden); the TXT reader treats the opening lines as title;
  - [x] normalization: years and ordinals, Roman numerals (324 cases, 99.7 %);
  - [x] quote dialogue;
  - [x] components: misaki + espeak-ng G2P in the Kokoro worker, faster-whisper QA;
  - [x] backends: Kokoro (fast), Chatterbox (expressive);
  - [x] benchmark incl. IndexTTS-2 (M4 step 7, `spikes/en-bench/FINDINGS.md`):
    IndexTTS-2 runs offline in the spike and is the T1 challenger until listening;
  - [ ] Kokoro misses NFR-05 on T0 (RTF 1.01 on 4 cores): try Kokoro ONNX / int8 or
    Piper for `en` T0;
  - [x] Chatterbox hallucinates on quote pieces under ~15 characters («“Holmes,”»):
    headings and pieces under 15 letters take the tier's `short` route (`en` → Kokoro,
    `ru` → unpolished Silero);
  - [x] `en` text gaps from the benchmark: *St.* before a name after a capitalized
    word («Avenue, St. John’s Wood» → Saint), pre-decimal money («2s. 6d.» → two
    shillings and six pence), the sentence dot before a closing quote, «I.» / «II.»
    opening a heading;
  - [x] `models.lock` entries with `langs = ["en"]`; golden fixtures per format;
  - [x] female narrator + contrasting dialogue voice (owner: `af_nicole`, `am_michael`);
  - [x] blind T1-vs-T0 test for `en`: T1 preferred in 6/20, fast tier by default;
  - [ ] carried over (owner): `pytest -m "models and slow" tests/e2e/test_novel_en.py`,
    an excerpt of ≤ 100 segments on T0 (no GPU, no core or memory limit) and T1;
    - [x] QA dropped spoken heading numbers («Three. A Case of Identity»): Whisper no
      longer suppresses digits, the transcript goes through the pack's normalizer
      (T1 flags 7 → 1 of 64, T0 3 of 64);
    - [x] explicit `max_workers` lifts the one-per-25-segments floor; T0 tests set 8;
    - [x] CPU workers spread over NUMA nodes (Kokoro RTF per worker 2.62 → 1.02 with
      8 workers); the T1 tests set `max_workers = 8` too;
    - [x] after both changes: `en` T0 `rtf_wall` 0.34 (was 0.507), `ru` T0 0.22, `ru`
      T1 0.31 — pass;
    - [x] `en` T1 `rtf_wall` 0.528 (> 0.5) → 0.4895: the Kokoro `short` stage runs
      alongside Chatterbox instead of after it (thin margin);
    - [x] whole-book M4B normalized in up to 8 pieces in parallel and streamed to one
      AAC encoder: 10 min of audio 36.8 s → 15.8 s (SPEC 4.7-M5);
  - [x] `ru` novel excerpt re-run after the excerpt and short-route changes: T0
    `rtf_wall` 0.30, 4 / 93 flagged; T1 0.31, 3 / 93 flagged;
  - [ ] optional: IndexTTS-2 vs Chatterbox by ear, if the expressive tier matters for
    English (benchmark challenger, not in the blind test);
- [x] **After M4 (owner, 2026-11-06):** review what `ru` can reuse from `en` now that
  there are two packs: misaki G2P / Kokoro for English words in Russian text (FR-24,
  instead of espeak-ng transliteration), the quote splitter for «» dialogue in Russian
  books; refactor only where both packs use it (SPEC 4.8-M5):
  - [x] quoted speech outside dash paragraphs is dialogue in `ru` (`«…», — сказал он`,
    `"Кто там?" спросила`), sharing the quote-span code with `en`;
  - [x] misaki not adopted: same Cyrillic as espeak-ng on 25 of 34 terms, better on 2,
    worse on 2, for a Kokoro env load in every Russian run; English spans in Kokoro's
    voice belong to FR-18;
  - [ ] optional: `ru` lexicon entries for what espeak-ng misreads — *Kubernetes*
    «кубер+нетис», *Sherlock* «ш+ерлок» (espeak: «к+юбернитс», «ш+ёлок»).
- [ ] **After M4 (owner, 2026-10-26):** review the components built so far and expose
  the useful ones as standalone commands — `text2audio translate book.md --to en -o
  book-en.md` (owner's form: `text2audio ru2en text-ru.md -o text-en.md`; paragraph by
  paragraph, Markdown structure kept; benchmark a stronger offline MT than Opus-MT for
  whole books, e.g. NLLB / MADLAD) and `text2audio cover <book> [--mode …]` to remake a
  cover without re-conversion.
  - [x] `text2audio cover <book folder>… [--mode …]` (SPEC 4.9-M5);
  - [x] `text2audio translate` / `ru2en` with Opus-MT, sentence by sentence (Marian
    reads ≤ 512 tokens, so whole paragraphs would be cut), structure kept (SPEC 4.9-M5);
  - [x] benchmark (`spikes/mt-bench/FINDINGS.md`): Tower-Plus-9B COMET 0.876, Seed-X-7B
    0.873, NLLB-3.3B 0.868 … Opus-MT 0.839 on FLORES-200;
  - [x] owner's blind reading → ru→en = Seed-X-PPO-7B, NLLB-3.3B fallback (SPEC 4.13-M5);
  - [x] **last stage (owner):** ru→en with Seed-X by paragraph, NLLB-3.3B fallback,
    Opus-MT when those are not installed (SPEC 4.16-M5);
  - [x] real-model ru→en run with Seed-X (*Капитанская дочка*, 214 k characters):
    13 min 54 s on one RTX 4090, 20 of 731 pieces from NLLB, no warning left;
  - [x] **first:** en→ru — benchmark (Seed-X 0.910 COMET), translator contract with
    pairs and units, `mt-hf` plugin, checks + placeholders + NLLB fallback (ADR-0011);
  - [x] owner's blind reading of the en→ru samples → Tower-Plus-9B leads (SPEC 4.15-M5);
  - [x] whole-book en→ru run (*Adventures of Sherlock Holmes*): 1 h 43 min, 1.9 % of
    pieces from the NLLB fallback, no warning left (§13.1);
  - [x] translation throughput: batches sorted by length — 579 s → 336 s on one story (§13.1);
  - [x] owner's listening to a translated chapter in Russian («Скандал в Богемии»,
    Tower-Plus → Silero xenia, and the re-voiced test clip): "samples look awesome"
    (2026-11-07).
- [ ] **After M4, idea (owner, 2026-11-05):**
  - [x] English → Russian book translation, the strongest offline model by benchmark
    (Tower-Plus-9B, ADR-0011);
  - [x] **re-voice English into Russian** (owner, 2026-11-07), all steps in one command:
    `text2audio revoice` (ADR-0012, SPEC 4.17-M5).
    - Markdown or a book gives the translated Markdown and its audiobook.
    - Audio gives audio in the same format.
    - Video gives the same video with its audio track replaced by the Russian one,
      timed to the original, plus subtitles.
  - [ ] revoice on a real recording from the owner (speech with music, several speakers).
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
