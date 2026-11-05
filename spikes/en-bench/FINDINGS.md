# Spike `en-bench` — findings (2026-11-06)

Throwaway spike (never imported by `src/`). It runs the SPEC §6.6.5 benchmark for
English (M4 step 7): the two `en` tiers as built, fast = Kokoro-82M (`af_heart`
narrator, `am_michael` dialogue) and expressive = Chatterbox Multilingual cloned from
clips those voices read, against IndexTTS-2 cloned from the same clips. IndexTTS-2 runs
as a spike environment, not as a plugin.

"Quality" in the component priority (ADR-0007) means TTS quality, judged by listening.
The numbers below show whether a system reads the words and whether it fits a tier.
They do not rank how good it sounds.

Set: **100 segments** (the owner's cap) from 56 paragraphs of *The Adventures of
Sherlock Holmes* (Project Gutenberg #1661, `tests/fixtures/fetch_novel_en.py`), picked
by `build_set.py` across all twelve stories: numbers 25, abbreviations 15, foreign
words 10, quote dialogue 30, narration 20 (`set.tsv`, `bench.md`). Every system reads
the same `en`-pack text (`text_norm`). The quote splitter turns 29 of the 100 into
short pieces of under 40 letters («“Holmes,”», «said Lestrade.»).

Reproduce:

```bash
uv run --python 3.11 python tests/fixtures/fetch_novel_en.py    # online, once
EN_BENCH_HOME=/mnt/devbox/text2audio/spikes/en-bench spikes/en-bench/setup.sh   # online, once
EN_BENCH_HOME=… spikes/en-bench/bench.sh   # offline: t0 t1 collect indextts cpu transcribe score
```

## Results

WER and CER come from the QA gate's own `asr-whisper` worker (faster-whisper
large-v3-turbo on a GPU, digits suppressed, spoken forms), scored against
`speech_text` with the `en` profile's folding. "QA-failed" applies the gate's rules:
WER > 0.12 and CER > 0.10, plus the audio checks (`rate-low` below 10 letters/s,
`long-silence`). RTF = synthesis time ÷ audio. Kokoro: one T0 worker (4 cores,
`taskset`, no GPU). Chatterbox: `text2audio run` on one RTX 4090 with 4 workers
(summed worker time; the wall-clock RTF is 0.42). IndexTTS-2: one process on the same
GPU, fp16, no DeepSpeed and no custom CUDA kernel. RTF T0 for IndexTTS-2 is
measured on 4 cores over 3 segments.

| System | WER | mean CER | QA-failed | flags | RTF | RTF T0 | VRAM¹ |
|---|---|---|---|---|---|---|---|
| Kokoro (fast) | **0.024** | **0.010** | **2** | wer 2, rate-low 1 | 1.01 (T0, wall 1.03) | 1.01 | — |
| Chatterbox (expressive) | 0.033 | 0.036 | 17 | wer 6, rate-low 13, long-silence 2 | 1.29 (wall 0.42) | — | 20.6 GB |
| IndexTTS-2 (spike) | 0.029 | **0.010** | 9 | wer 2, rate-low 8 | 1.52 | 76 | 7.9 GB |

¹ Increase of the whole GPU's used memory during the run (`nvidia-smi` samples):
Chatterbox with 4 workers, IndexTTS-2 alone (torch peak 7.0 GB).

WER by category:

| System | abbreviation | dialogue | foreign | narration | number |
|---|---|---|---|---|---|
| Kokoro | 0.010 | 0.017 | 0.024 | 0.019 | 0.036 |
| Chatterbox | 0.030 | 0.073 | 0.024 | 0.018 | 0.041 |
| IndexTTS-2 | 0.025 | 0.021 | 0.030 | 0.021 | 0.040 |

Speech rate (letters per second of trimmed speech, the gate's measure):

| System | ≥ 40 letters: min / p5 / median / max | < 40 letters: min / median |
|---|---|---|
| Kokoro | 8.1 / 11.7 / 13.5 / 17.7 | 5.7 / 12.6 |
| Chatterbox | 8.4 / 11.7 / 14.8 / 19.1 | 1.4 / 6.2 |
| IndexTTS-2 | 6.1 / 11.3 / 13.7 / 17.0 | 4.0 / 10.2 |

## Findings for M4

1. **Every system reads the words** (WER ≤ 0.033). Kokoro is the most accurate and
   fails 2 segments: pre-decimal money the pack leaves unread, and an ordinal the
   transcript drops (see 6). These numbers are the floor and do
   not pick a winner.
2. **Chatterbox makes things up on short pieces.** «“Holmes,”» became «ohms ohms»,
   «said Lestrade.» got a babbled tail, and «said I, glancing up at my companion,»
   ended with «You have an Edeus talk». 13 of its 17 failures are `rate-low` on pieces
   under 40 letters: audio padded with breaths and tails (median 6.2 letters/s). This
   is a T1 problem for `en` that `ru` does not have: dash dialogue gives longer
   remarks. The QA gate catches these and retries them, which costs time and does not
   fix the cause. Possible fixes, not tried here: join a short remark to its line for
   the cloned backends, or a minimum length for a split.
3. **IndexTTS-2 runs offline within the spike.** It loads with the network guard on
   and nothing is fetched, once its auxiliary models (w2v-bert-2.0, MaskGCT codec,
   CAMPPlus, BigVGAN) are given as local paths and its Qwen emotion model is off. Its
   text normalizer (WeTextProcessing, needs pynini) is replaced by a pass-through,
   because the pack normalizes. The env uses torch 2.6.0 and Python 3.12, not the
   repository's pins (torch 2.8, Python < 3.12), and reads the checkout from
   `sys.path`. It fails 9 segments (8 `rate-low` on short pieces, 1 hallucination:
   «“Holmes,”» → «homes»), against Chatterbox's 17. Dialogue WER is 0.021, Chatterbox's
   0.073. On a 4090 it is 1.2× slower than Chatterbox's per-worker time (RTF 1.52 vs
   1.29). It needs 7.9 GB for one process, so 2–3 workers fit a free 24 GB card. On
   CPU it is out (RTF 76).
4. **Kokoro misses NFR-05 on T0.** RTF 1.01 on 4 cores (≤ 0.5 required); Silero `ru`
   runs at 0.08. The measurement holds on other cores: 0.88–0.92 at 4 threads, 0.41 at
   16 (6 segments, `af_heart`). A 10-hour English book takes about 10 hours on T0.
   Options for later: Kokoro ONNX / int8, or Piper (its voices are already in the
   store for `ru` FR-24 work) as a T0 voice for `en`, if listening allows it.
5. **QA bounds for `en` hold for whole sentences.** On segments of ≥ 40 letters, every
   system stays inside (10, 22) at the 5th percentile (11.3–11.7) and the maximum
   (≤ 19.1). The minimums (6.1–8.4) are single slow readings. WER 0.12 passed every
   clean reading. Short pieces fall below 10 for all three systems; Kokoro's
   are slow too (5.7–15.4), but most of them have fewer than 15 letters, the gate's
   minimum for a rate check. `wer_threshold = 0.12` and `chars_per_sec = (10, 22)`
   stay.
6. **Text-stage gaps in the `en` pack** (not fixed in this step):
   - «Serpentine Avenue, St. John’s Wood» → «Street. John’s Wood»: *St.* after a
     capitalized word is read as Street even when a name follows.
   - Pre-decimal money is not read: «rooms 8s., breakfast 2s. 6d.» stays as is, and
     every system says «eights», «twos, six D».
   - «June nineteenth» lost its last word in the Kokoro and Chatterbox transcripts and
     became «nineteen» for IndexTTS-2. Whisper with digits suppressed may drop an
     ordinal; unverified until heard.

## Proposed (until the owner's listening test, M4 step 8)

- Fast tier stays Kokoro (best WER; NFR-05 on T0 is an open issue, finding 4).
- Expressive tier: Chatterbox stays the built default. IndexTTS-2 is the challenger
  for `en` T1 and dialogue. It reads better by the floor metrics and hallucinates
  less on short pieces. Listening decides whether it becomes a plugin (§6.6.3, its own
  env); until then it stays in the spike.
