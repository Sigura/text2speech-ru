# Adding a Language

> Verified in M4: the English pack (`plugins/lang-en`) was generated from the template
> and filled in by following this guide. Section 8 records what that took.

A language in Text2Audio is a **language pack**: an independent Python package that
chooses the best components for that language and supplies its text rules. There is
no universal model (ADR-0003); expect your language to need different components from
English.

## 1. Research before code

For each stage, list candidates that run fully offline and record their license:

| Stage | Questions to answer |
|---|---|
| Segmentation | Does pySBD support the language? Special abbreviation or punctuation rules? |
| Normalization | Numbers: does the language inflect them (case, gender)? Date, time, currency formats? Existing TN library? |
| Stress / G2P | Is pronunciation predictable from spelling? Unwritten stress, tones, diacritics, heteronyms? |
| TTS backend | Which models support the language, on which tier (CPU / GPU)? Voices available? |
| QA ASR | Is Whisper good enough, or is there a stronger language-specific ASR? |
| Content conventions | Chapter-heading words, quote characters, dialogue marking, front-matter words. |

Watch for components that **download on first use** — they must be pre-fetched
(ADR-0002).

## 2. Generate the pack

```bash
python scripts/new_lang.py de "Deutsch" --script Latn [--sample "Guten Tag."]
```

This creates `plugins/lang-<code>/` with `pyproject.toml` (entry points), the language
profile skeleton, a normalizer stub with the FR-24 hook, an empty lexicon, the
normalization test set and its runner, and a README checklist that follows this guide.
Then add `text2audio-lang-<code>` to `[tool.uv.sources]` and the `langs` group of the
root `pyproject.toml` and run `uv sync`.

The template (`templates/lang/`, standard-library `string.Template`) was extracted from
the Russian pack in M2 (ADR-0008); the Russian pack itself is the only one written by
hand.

## 3. Fill in the profile

The profile is a `LanguageProfile` object exported by the pack's `text2audio.languages`
entry point (`plugins/lang-<code>/src/text2audio_lang_<code>/__init__.py`), SPEC.md
§6.6.4–6.6.5. Besides the text rules (chapter-heading patterns, abbreviations,
front-matter markers) it names its components by entry point and carries the data the
core reads. Any field you leave out falls back to the generic implementation.

| Field | Russian | English | Meaning |
|---|---|---|---|
| `normalizer` | `"ru"` | `"en"` | `text2audio.normalizers` entry point: `normalize(text, role) → NormResult` |
| `g2p` | `"ruaccent"` | `None` | `text2audio.g2p` component (a worker); `None` when the backend does its own G2P (misaki inside Kokoro) |
| `default_tier` | `"fast"` | `"fast"` | tier when the user names none; set by the blind test |
| `tiers` | `fast`: silero; `expressive`: silero → chatterbox polish, short: silero | `fast`: kokoro; `expressive`: chatterbox, short: kokoro | backend (+ polish, foreign-text backend, short-piece backend) per tier |
| `voices` | narrator `xenia` | narrator `af_nicole`, dialogue `am_michael` | role → backend → voice id or reference clip |
| `dialogue` | `"dash"` | `"quote"` | dialogue convention: `—` lines or quoted speech with attribution |
| `pauses_ms` | `{"paragraph": 800}` | `{}` | overrides of the core's structural pauses |
| `qa` | `asr="gigaam"`, WER 0.18, `fold={"ё": "е"}` | `asr="whisper"`, WER 0.12, 7–22 letters/s | QA gate bounds |
| `lexicon` | `lexicon.tsv` | `lexicon.tsv` | base pronunciation list (FR-13) |
| `part_title` | `"{title} (часть {n} из {total})"` | `"{title} (part {n} of {total})"` | title of a long section split in parts |

The normalizer runs in the core environment, so it must stay light (pure Python, data
inside wheels). Keep a test set like `plugins/lang-ru/tests/normalization.tsv`
(`input<TAB>expected[<TAB>role]`, ≥ 95 % exact match, FR-11).

Anything heavy — stress/G2P models, ASR, TTS — is a separate plugin that runs as a
worker in its own uv environment (ADR-0010): a descriptor with `name`, `env`,
`env_project` and `worker_command()`, a `worker.py` that imports `t2a_worker` first,
and `env/pyproject.toml` + `env/uv.lock`. Copy `plugins/g2p-ruaccent` (component) or
`plugins/backend-silero` (backend) as a starting point; `text2audio envs sync` builds the
environment.

## 4. Declare assets

Every model file and data file the pack's components load goes into its
`models.lock` entries: repository, commit revision, files, SHA-256, license.
Entries are matched to plugins by `backend = "<name>"` or `component = "<kind>:<name>"`
(`"g2p:ruaccent"`, `"asr:gigaam"`); the core passes their store directories to the worker
in its `load` request. Workers run with `T2A_NO_NETWORK=1`: a library that tries to
download on first use fails instead — wrap it so it loads only pinned files (see the
RUAccent worker).

## 5. Build the benchmark set

- A full public-domain novel, fetched by a script pinned by SHA-256 and not committed
  (`tests/fixtures/fetch_novel.py`, `fetch_novel_en.py`).
- At most 100 benchmark segments per system, picked across the novel by category
  (numbers, abbreviations, dialogue, foreign words, narration; `spikes/en-bench/build_set.py`).
- A normalization test set (`input<TAB>expected[<TAB>role]`), ≥ 95 % exact match
  (FR-11).
- Golden fixtures: one short book per supported input format.

## 6. Choose defaults by measurement

Run the benchmark for every candidate backend (`spikes/ru-bench/`, `spikes/en-bench/`
are the templates): QA-ASR WER with the gate's own worker, QA flags, RTF on T0/T1,
VRAM. The numbers are a floor; quality is decided by ear (ADR-0007):

1. Voice samples, one passage per candidate narrator and one quoted exchange per
   candidate dialogue voice (`spikes/en-voices/samples.py`); the owner picks.
2. Reference clips for cloning backends, read by the chosen fast-tier voice
   (`spikes/en-voices/make_refs.py`), so both tiers keep the same narrator.
3. The blind tier test: `scripts/listen_ab.py build t1 --lang <code>`, then
   `score`; the expressive tier becomes the default only at ≥ 60 % of decided pairs.

Record the winners in the profile and the decision in SPEC.md §13.1.

## 7. Done when

- No change was needed in `src/text2audio/core/` (English needed one word, see §8).
- The pack's tests, golden fixtures and the offline CI job pass.
- A book in the language renders with an empty `text2audio.toml` and passes the QA
  gate.
- `text2audio plugins` lists the pack and its components; `doctor` shows its licenses.
- `tests/e2e/` renders the full novel on T0 and T1 (`slow`, run by the owner).

## 8. What English needed (M4)

Steps 0–6 (pack, text rules, normalization, dialogue, QA ASR, two backends) took one
evening of commits (2026-11-05 20:43 → 2026-11-06 00:10), within the ≤ 3
developer-day target; benchmark and listening came after.

- **One core change:** `"quote"` in the `dialogue` Literal of `core/contracts.py`, with
  the splitter in `text/dialogue.py`. A language with a new dialogue convention needs
  the same kind of change.
- **Offline traps:** misaki loads a spaCy model on first use; it is pinned in
  `models.lock` and loaded from the store. IndexTTS-2 fetches four auxiliary models
  unless they are given as local paths.
- **QA ASR writes digits** unless told otherwise: the Whisper worker suppresses digit
  tokens and spells titles and Roman numerals, so transcripts match normalized text.
- **The rate bounds depend on the voice:** the slow `af_nicole` needed a floor of 7
  letters/s, not 10. Re-measure after the owner picks voices.
- **Short dialogue pieces** from the quote splitter (< 40 letters) make cloning
  backends hallucinate; check them in the benchmark.
- **T0 speed is per backend:** Kokoro runs at RTF ≈ 1.0 on 4 cores and misses NFR-05
  (open in TODO).
