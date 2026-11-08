# 0013. DeepSeek API for translation and stress, opt-in by key

- **Status:** Accepted
- **Date:** 2026-11-07
- **Spec references:** §6.10, §6.11, §6.6.5, FR-04

## Context

The owner wants better translations and stress marks than the local models give, and
accepts sending book text to a hosted LLM for that. The offline contract (ADR-0002) allows
no network at runtime, and it must stay the default: books are often processed with no
key or no connection.

## Decision

- `--deepseek-key` (or `T2A_DEEPSEEK_KEY`) on `translate`, `ru2en`, `en2ru`, `run`,
  `synthesize` and `preview` turns on the DeepSeek API (`deepseek-v4-pro` for translation, `deepseek-flash` for stress, at
  api.deepseek.com). Without a key nothing changes and nothing touches the network.
- **Translation:** DeepSeek is the lead translator, by paragraph; pieces that fail the
  checks of ADR-0011 go to the first local translator by sentence. If the API fails
  (no connection, bad key, no balance), the whole text is translated offline and a
  warning says why. With no local translator able to run, the API alone is used.
- **Stress (Russian):** DeepSeek marks stress (`+` before the vowel) and ё first. An
  answer that changed anything but marks and е→ё is rejected; rejected texts and the
  rest after an API failure go to the pack's stress component. Answers, rejections
  included, are cached in `_work/stress.jsonl` under the API model's name.
- The key is never written to config files, reports or caches; it is passed by option
  or environment only.

## Consequences

- FR-04 holds for runs without a key; with one, book text leaves the machine.
- Results with a key depend on a remote model that may change; the caches keep the
  answers already received, so a rerun does not ask again.
- The core gains a small stdlib HTTP client (`core/deepseek.py`) with a Russian stress
  prompt — a language-specific piece in the core, limited by `STRESS_LANGS`.
