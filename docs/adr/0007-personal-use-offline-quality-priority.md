# 0007. Personal use: offline first, quality second, license not a criterion

- **Status:** Accepted
- **Date:** 2026-10-24
- **Spec references:** §3.2, §7, §8, §13.1
- **Supersedes:** the license-related consequence in ADR-0006

## Context

Earlier drafts kept the core MIT-compatible and treated copyleft and non-commercial
components (IndexTTS-2, XTTS-v2, ESpeech, Silero v5, MMS, pymupdf4llm, EbookLib) as
second-class. The project owner confirmed the project is **for personal use only**:
neither the code, the bundled models nor the generated audio are redistributed or used
commercially.

## Decision

- License is **not** a selection criterion for any component.
- Every component is chosen by this priority:
  1. **works fully offline** after install (hard requirement, see ADR-0002);
  2. **quality**, measured on the relevant benchmark — for TTS this means how natural
     the speech sounds, judged by listening; ASR WER is only an intelligibility floor
     (owner, 2026-10-26);
  3. everything else: speed, size, maintenance.
- Licenses are still recorded in `models.lock` and shown by `doctor`, for information
  only.
- No LICENSE file is added to the repository.

## Consequences

- Previously sidelined models become full benchmark candidates: IndexTTS-2 (English
  expressive, strongest emotion control), XTTS-v2 and ESpeech (Russian expressive),
  Silero v5 (Russian fast), MMS-TTS (long-tail languages).
- The PDF reader is chosen by the M1 benchmark among Docling, pymupdf4llm and Marker.
- If the intended use ever changes, this ADR must be superseded and a license audit
  added back to M5.
