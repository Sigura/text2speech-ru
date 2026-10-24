# 0006. Russian as the second language

- **Status:** Superseded in part by [ADR-0008](0008-russian-first-language.md) (milestone order)
- **Date:** 2026-10-24
- **Spec references:** §6.6.5 (Russian), §8, §11 (M4)

## Context

FB2, one of the three original input formats, is primarily a Russian-language format.
Russian also stresses the architecture differently from English (unwritten stress, ё,
case agreement, dialogue dashes), which makes it a good test of ADR-0003.

## Decision

- Russian (`ru`) is the second language, delivered in milestone M4.
- Proposed front end: RUAccent (stress + ё), rutextnorm (normalization, after a code
  audit), GigaAM-v3 (QA ASR).
- The TTS backend per tier is chosen by the M4 benchmark among Piper `ru` / Silero v5
  (fast) and Chatterbox Multilingual / Qwen3-TTS (expressive).

## Consequences

- The `ru` pack must pre-fetch components that otherwise download on first use.
- Silero v5 and ESpeech licenses must be clarified before either can be a default.
- M4 proves isolation only if Russian uses different components from English in at
  least normalization, stress/G2P and QA ASR.
