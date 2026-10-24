# 0008. Russian as the first language

- **Status:** Accepted
- **Date:** 2026-10-24
- **Spec references:** §3.1, §6.6.5 (Russian), §8, §11 (M2, M4), §14, FR-06, FR-07
- **Supersedes:** the milestone order in ADR-0006 (Russian second, in M4)

## Context

ADR-0006 made Russian the second language, delivered in M4 after English (M2). The
project owner's own library is mostly Russian, and FB2 — a primarily Russian format —
is a core input. Russian is also the harder language for the text front end
(unwritten stress, ё, case agreement, dialogue dashes), so building it first tests
the per-language plugin design (ADR-0003) where it matters most.

## Decision

- Russian (`ru`) is the **first** language, delivered in **M2** (Russian TTS).
- English (`en`) becomes the second language, delivered in **M4**.
- M0 (scaffold) and M1 (conversion) keep their scope; their fixtures and examples
  favour Russian (cp1251 FB2, `.fb2.zip`, Russian TXT headings).
- The Russian front end and the backend benchmark (former "M4 benchmark protocol")
  move to M2 unchanged.
- The language-pack template is extracted from the Russian pack at the end of M2.
  The isolation proof moves to English: M4 must add the English pack from the
  template with **zero diff under `src/text2audio/core/`**.

## Consequences

- FR-07's acceptance test now applies to English in M4.
- The pack interfaces are designed against Russian's needs first; English must fit
  them without core changes, which keeps the proof of ADR-0003 intact.
- T0/T1 reference backends in §9 are Russian candidates until M4.
