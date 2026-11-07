# 0011. Book translation: an LLM by paragraph, checked, with a sentence-level fallback

- **Status:** Accepted
- **Date:** 2026-11-06
- **Spec references:** §6.11 (`translate`), §6.6.1, §6.10, §9.1, §13.1

## Context

The owner wants whole books translated offline — English → Russian first (to listen to
English books in Russian), Russian → English last — "paragraph by paragraph, Markdown
structure kept", with the best approach "by sentences or by paragraphs, ensembled where
possible", and abbreviations left as they are. Opus-MT, the cover-prompt translator, is
far behind (`spikes/mt-bench`: FLORES-200 COMET-22 en→ru 0.847, ru→en 0.839).

The benchmark found Seed-X-PPO-7B first en→ru (0.910) and a close second ru→en (0.873
against Tower-Plus-9B's 0.876, which the owner's blind reading did not prefer), at 15 GB
of VRAM and ≈ 180 characters/s; NLLB-200 3.3B at 0.892 / 0.868 runs 4× faster, in 13 GB,
also on CPU. The LLMs, given whole paragraphs, keep context (the narrator's gender, names,
tone) but smooth away details that the sentence-level models keep (Pushkin's French
«pour être outchitel»). An MBR ensemble — per sentence, the candidate that agrees most
with the others — gained at most +0.0017 COMET for 2–5× the time. Both models translate
NASA and FBI into НАСА and ФБР.

## Decision

1. A language pack names its translators per target language in order of preference
   (`LanguageProfile.translators`, e.g. `en: {"ru": ("seedx-7b", "nllb-3.3b")}`); a
   translator plugin declares its `pairs` and its `unit` — `paragraph` (an LLM) or
   `sentence`. The core picks the first that can run now (a GPU with room for a
   GPU-only model, §9.1); the next one is the fallback.
2. The leading translator translates every piece of text in its unit. A piece with a
   footnote reference or an inline directive inside it goes sentence by sentence, so the
   marker returns to its sentence.
3. Every piece is checked: Latin abbreviations of 2–6 capitals kept, no word in mixed
   scripts, length within ×0.45–2.2, no sentence dropped or added (paragraph unit).
   Lost abbreviations are fixed by translating the piece again with placeholders (`XQ1`)
   and restoring them; other failures go to the fallback, sentence by sentence, and its
   result replaces the first when it fails fewer checks.
4. No whole-book ensemble: the gain does not pay for the time.
5. Translations are cached by translator and text, so a run resumes and an edited book
   translates only what changed.
6. Translation weights are an opt-in feature (`ModelAsset.feature = "translate"`,
   `models pull --feature translate`), so a language install does not fetch 33 GB.

## Consequences

- One worker environment (`mt-hf`) serves both models; adding a language pair is a
  pack entry when the models cover it.
- A machine without 16 GB of free VRAM translates with NLLB (GPU or CPU) alone, with
  the same checks but no fallback; `translate` says which translator ran.
- Emphasis is still dropped, as before; directives and footnotes keep their place.
- ru→en switches from Opus-MT to Seed-X / NLLB in the `ru` pack as the last stage
  (owner's order); cover prompts follow the pack's first `en` translator.
