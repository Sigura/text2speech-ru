# Spike `ru-polish` — findings (2026-10-24)

Throwaway spike (never imported by `src/`). Question: does two-stage synthesis (SPEC
§6.6.6: fast TTS, then a "polish" model) beat a single expressive model for Russian?
Also: what happens to English terms inside Russian text?

Variants, all on the 19 `ru-smoke` sentences (+ 7 code-switch sentences in
`codeswitch/sentences.txt`), female voices, RTX 4090:

- **A** Silero v5.6 `xenia` (from `ru-smoke`)
- **B** A → resemble-enhance (denoise + enhance, upstream defaults)
- **C** A → Chatterbox VC into Chatterbox's built-in voice (low, median F0 ≈ 130 Hz)
- **C2** A → Chatterbox VC into the 15 s Silero `xenia` clip (female; same target as D2)
- **D1** Chatterbox Multilingual TTS, built-in voice (same timbre as C)
- **D2** Chatterbox Multilingual, cloned from a 15 s Silero `xenia` clip
- **D3** D2 + RUAccent stress marks (converted to U+0301)

Reproduce (each variant has its own uv env — the pins conflict):

```bash
uv run --project chatterbox python fetch.py                          # online
CUDA_VISIBLE_DEVICES=0 uv run --project enhance python enhance_b.py   # B
CUDA_VISIBLE_DEVICES=0 uv run --project chatterbox python vc_c.py     # C
CUDA_VISIBLE_DEVICES=0 uv run --project chatterbox python chatterbox_d.py  # D1–D3
CUDA_VISIBLE_DEVICES=0 uv run --project ../ru-smoke python codeswitch/silero_probe.py
CUDA_VISIBLE_DEVICES=0 uv run --project ../ru-smoke python score.py   # WER + listen set
CUDA_VISIBLE_DEVICES=0 uv run --project chatterbox python quality.py  # UTMOS + pitch
uv run --project chatterbox python listen_concat.py                   # out/listen/full/
```

## Results

WER by GigaAM-v3 `rnnt`; RTF = total synthesis time ÷ audio duration (B and C include
Silero's own time). HF>8k = share of spectral energy above 8 kHz (bandwidth gauge).
No network connection was attempted at runtime in any step.

| Variant | WER (no digits) | UTMOS median (min) | Pitch spread, st | RTF 4090 | RTF 4 cores | Rate |
|---|---|---|---|---|---|---|
| A Silero `xenia` | 0.000 | 3.29 (2.85) | 1.9 | **0.013** | 0.08 | 48 kHz |
| B A + resemble-enhance | 0.000 | 3.48 (3.17) | 1.9 | 0.205 | — | 44.1 kHz |
| C A + VC → built-in voice | 0.012¹ | **4.11 (3.51)** | 4.5 | 0.209 | — | 24 kHz |
| **C2 A + VC → `xenia` clip** | **0.000** | 3.80 (3.40) | 2.3 | 0.202 | ≈ 8–11 | 24 kHz |
| D1 Chatterbox, built-in voice | 0.070 | 3.77 (2.85) | 5.5² | 1.127 | — | 24 kHz |
| D2 Chatterbox, `xenia` clone | 0.036 | 3.70 (3.01) | 2.1 | 1.233 | — | 24 kHz |
| D3 D2 + RUAccent stress | 0.000 | 3.81 (3.27) | 2.0 | 1.315 | — | 24 kHz |

UTMOS22 strong (SpeechMOS) predicts listener MOS 1–5; it is trained on English tests,
so on Russian it is a proxy. Pitch spread = std of F0 in semitones (pYIN); low = flat.
¹ The only "error" is *всё* → «вс», an ASR/normalization artefact also seen in D.
² Inflated by creak: 16 % of D1's voiced frames are > 9 st off the median (C: 6 %).

Code-switch set (Russian sentences with Docker, Python, Google Docs, iPhone, HTTP …):

| System | Behaviour |
|---|---|
| Silero | **Silently drops every Latin-script word** («Я установил Docker» → «я установил»; whole English quote gone). |
| Chatterbox (D1/D2) | Reads them in Russian-accented English: «докер», «пайтон», «гугл докс», «дедлайн», «айфон», «итс нот май проблем». **Acronyms fail**: HTTP → «хтдпо», TCP → «тт». WER (≈ 0.3) overstates this — references are Latin, ASR writes Cyrillic. |

## Findings for M2

1. **Enhancement on Silero is a dead end.** B costs ~15× Silero's synthesis time,
   barely moves UTMOS, and *lowers* the sample rate (48 → 44.1 kHz); Silero's output is
   clean, which is not what resemble-enhance is trained to fix. Enhancement may still
   matter **after** 24 kHz models (C/D keep ~1/5 of Silero's high-band energy) —
   untested.
2. **Voice conversion is the best quality-per-cost option: Silero → Chatterbox VC.**
   C2 matches the best Chatterbox TTS run (D3) on UTMOS (3.80 vs 3.81) and WER (0) at
   **1/6 of the GPU time**, never invents or skips words (Silero picks them), and needs
   only S3Gen, not Chatterbox's autoregressive T3. Conversion does **not** just copy
   Silero's flat intonation: into the livelier built-in voice, pitch spread rises from
   1.9 to 4.5 st and UTMOS to 4.11. **Expressiveness follows the target voice**, so the
   narrator reference clip is the main quality lever (choice is the owner's, §6.12).
   It is a GPU-only stage: on 4 CPU cores VC runs ~8–11× slower than real time, so T0
   stays Silero alone.
3. **Chatterbox Multilingual is slower than real time** on a 4090 (RTF 1.1–1.3, stock
   fp32, no compile). NFR-05 asks ≤ 0.5 on T1 (8 GB GPU). It needs speed work (fp16,
   compile, batching) or several GPUs to qualify for the expressive tier.
4. **Chatterbox needs external Russian stress.** It calls `russian_text_stresser`
   (not on PyPI) and silently reads unstressed text without it. Feeding RUAccent marks
   as U+0301 (the vocab has it) took WER from 0.036 to 0.000 — the `ru` pack's stress
   stage is useful for Chatterbox too.
5. **Code-switching is unspecified and breaks the fast tier.** SPEC only has
   per-paragraph language detection (FR-18). The `ru` pack needs a Latin-span stage
   before TTS: acronyms spelled out (HTTP → «эйч-ти-ти-пи»), a loanword/brand lexicon
   (Docker → «до́кер»), transliteration fallback, and optionally routing longer English
   phrases to the `en` backend in a matching voice. The QA gate catches the drops
   (WER) but cannot fix them.
6. **More first-use downloads (ADR-0002):**
   - resemble-enhance `download()` git-clones unpinned `main` into its package
     directory; load the pinned HF snapshot yourself. Its checkpoint loads fine with
     `torch.load(weights_only=True)` (§6.12). Inference imports deepspeed only through
     training helpers — stubbed here; a real worker should vendor the inference code.
   - Chatterbox `MTLTokenizer` always builds the Chinese converter: `spacy_pkuseg.pkuseg()`
     downloads `spacy_ontonotes.zip` from GitHub on every load, whatever the language
     (patched off here), and the Cangjie mapping tries the Hub (warning only).
   - `resemble-perth` (watermark) imports `pkg_resources` → pin `setuptools<81`.
7. **Dependency pins conflict** (resemble-enhance: torch 2.1.1 + deepspeed; Chatterbox:
   torch 2.6 + transformers 5.2) — confirms one uv env per worker (ADR-0004).

8. **Playback artefact, not synthesis.** The owner heard words 2–3 missing; ASR hears
   them in every variant and the waveforms have no gaps there (only comma pauses). Clips
   start speaking within 50 ms at four different sample rates, which made the player
   stall at the start. `listen_concat.py` writes one 48 kHz file per variant with 1 s
   lead-in (`out/listen/full/`); the product's assembled MP3s do not have this issue.

9. **Owner listening (2026-10-25):** both halves sound like one narrator; D2
   (Chatterbox cloned from `xenia`) is as good as C2 on plain Russian and reads English
   terms well (`code-01`, `code-02`). Decision: **hybrid** — C2 for Cyrillic-only
   segments (≈ 6× cheaper, no word errors), Chatterbox TTS with RUAccent stress for
   segments with Latin-script words.

## Not done here / next

- **Listening** (`out/listen/full/`): A vs C2 (does conversion improve the voice?),
  C vs D1 (Silero's reading vs Chatterbox's, same timbre), C2 vs D3, D2 vs D3 (stress marks).
  Homograph stress must be judged by ear — WER cannot see it.
- VC with an expressive female narrator reference (owner's choice); Seed-VC and
  OpenVoice as alternative converters; enhancement after C/D (24 → 44.1 kHz).
- Chatterbox speed-up (fp16 / `torch.compile`) before ruling it out of T1.
