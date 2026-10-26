# Adding a Language

> **Draft.** Written against SPEC.md 1.2-M0 before implementation; commands and
> interfaces will be finalized in M0, shaped by the Russian pack (M2, the first
> language) and verified when the English pack is generated from the template in M4.

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

`text2audio-lang.toml` (SPEC.md §6.6.4–6.6.5):

- `[language]` — code, name, default backends per tier.
- `[profile]` — ranked components per stage, speaking rate, pause lengths, quote
  characters, dialogue-dash flag, QA settings (ASR model, WER threshold,
  characters-per-second range).
- `[voices]` — narrator and a contrasting dialogue voice per backend.
- Text rules: chapter-heading patterns, abbreviations, front-matter markers, base
  lexicon.

Any stage you leave out falls back to the generic implementation.

**As built in M2 (the Russian pack is the reference):** the profile is a
`LanguageProfile` object exported by the pack's `text2audio.languages` entry point
(`plugins/lang-ru/src/text2audio_lang_ru/__init__.py`). Besides the text rules it names
its components by entry point and carries the data the core reads:

| Field | Russian value | Meaning |
|---|---|---|
| `normalizer` | `"ru"` | `text2audio.normalizers` entry point: `normalize(text, role) → NormResult` |
| `g2p` | `"ruaccent"` | `text2audio.g2p` component that adds `+` stress marks (a worker) |
| `tiers` | `{"fast": TierRoute(backend="silero")}` | backend (+ polish, foreign-text backend) per tier |
| `voices` | `{"narrator": {"silero": "xenia"}}` | role → backend → voice id |
| `pauses_ms` | `{"paragraph": 800}` | overrides of the core's structural pauses |
| `qa` | `QaProfile(asr="gigaam", wer_threshold=0.18, fold={"ё": "е"})` | QA gate bounds |
| `lexicon` | path to `lexicon.tsv` | base pronunciation list (FR-13) |
| `part_title` | `"{title} (часть {n} из {total})"` | title of a long section split in parts |

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

- ≥ 200 sentences from public-domain text covering the language's hard cases
  (e.g. homographs, numbers needing agreement, dialogue, poetry).
- A normalization test set (input → expected spoken form), ≥ 95% exact match required
  (FR-11).
- Golden fixtures: one short book per supported input format.

## 6. Choose defaults by measurement

Run the benchmark for every candidate backend and component: QA-ASR WER,
language-specific accuracy (e.g. stress), RTF on T0/T1, license. Confirm with a short
blind listening test with native speakers. Record the winners in the profile and the
decision in SPEC.md §13.1.

## 7. Done when

- No change was needed in `src/text2audio/core/`.
- The pack's tests, golden fixtures and the offline CI job pass.
- A book in the language renders with an empty `text2audio.toml` and passes the QA
  gate.
- `text2audio plugins` lists the pack and its components; `doctor` shows its licenses.
