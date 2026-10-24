# 0003. No universal model: per-language components

- **Status:** Accepted
- **Date:** 2026-10-24
- **Spec references:** §6.6.1, §6.6.5, FR-07, FR-18, FR-22

## Context

Open TTS quality varies strongly by language, and the hard problems differ: heteronyms
in English, unwritten stress, ё and case agreement in Russian, tones in Chinese,
diacritics in Arabic and Hebrew. No single model or toolchain is best everywhere, and
the best choice per language changes every few months.

## Decision

- Every language-dependent stage is a plugin discovered via Python entry points:
  segmenter, normalizer, stress/G2P, TTS backend, QA ASR, emotion classifier.
- A language pack's profile ranks candidates per stage and tier; the first installed
  candidate is used. Users can override per language in config (`[lang.ru]`).
- Multilingual models are allowed as one option a language may choose, not as the
  design centre.
- A component replaces a language's default only after winning that language's
  benchmark.

## Consequences

- The core must stay free of language- and model-specific code (enforced in review:
  M4 must not touch `core/`).
- More packages to maintain and test; mitigated by the `_template` cookiecutter and a
  generic fallback for stages a pack does not define.
- Mixed-language books may use different backends within one book; voice matching
  across languages is needed (FR-18).
