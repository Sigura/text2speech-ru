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
uvx cookiecutter templates/lang          # asks for code (e.g. "de") and name
```

This creates `text2audio-lang-<code>/` with `pyproject.toml`, the language profile,
tests and CI.

The template is extracted from the Russian pack at the end of M2 (ADR-0008); the
Russian pack itself is the only one written by hand.

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

## 4. Declare assets

Every model file and data file the pack's components load goes into its
`models.lock` entries: repository, commit revision, files, SHA-256, license.

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
