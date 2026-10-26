# Spike `ru-bench` — findings (2026-10-26)

Throwaway spike (never imported by `src/`). It runs the SPEC §6.6.5 benchmark for
Russian: the M2 defaults (T0 Silero `xenia`, T1 hybrid Silero → Chatterbox VC) against
three challengers, Qwen3-TTS 1.7B Base, ESpeech-TTS-1 SFT 256K (F5) and XTTS-v2. Every
challenger is cloned from the `xenia` reference clip and runs as a spike environment,
not as a plugin.

**Status:** decided (owner, 2026-10-26) after the blind listening test, see
[Listening](#listening). "Quality" in the component priority (ADR-0007) means TTS
quality: how natural the narration sounds, its intonation and stress. Only listening
measures that. The metrics below come from ASR and timing. They show whether a system
reads the words and whether it fits a tier. They do not rank how good it sounds.

Set: 221 items (`set.tsv`, from `build_set.py`): homographs, ё, numbers, dialogue,
≥ 30 FR-24 sentences with Latin words and a poem, taken from the novel, the
code-switch fixture and `handwritten.tsv`. Text metrics use the whole set. Each
challenger synthesizes the **100 segments** that `listen.py plan` chose (the owner's
cap, benchmark only). Every system is scored on those same 100.

Reproduce:

```bash
spikes/ru-bench/setup.sh             # online, once: 3 uv envs + ~8.8 GB of weights
spikes/ru-bench/bench.sh             # offline: t0 t1 collect qwen3 espeech xtts cpu transcribe score listen
uv run --python 3.11 python spikes/ru-bench/listen.py results.json   # unblind the listening export
```

## Results

WER and CER come from GigaAM-v3 `rnnt` with the QA gate's folding; QA-failed uses the
gate's rule. RTF (1 worker) means synthesis time ÷ audio on one RTX 4090; for Silero it
is the T0 run (4 cores, 8 GB, no GPU), and for the hybrid it is Silero plus VC polish.
RTF T0 for the challengers is measured on 4 cores, no GPU, over 2–6 segments.

| System | WER | WER w/o Latin | mean CER | QA-failed | RTF (1 worker) | RTF T0 | VRAM |
|---|---|---|---|---|---|---|---|
| T0 Silero `xenia` | **0.024** | **0.011** | **0.012** | 1 | **0.083** (CPU) | — | — |
| T1 hybrid (Silero → Chatterbox VC) | 0.032 | 0.015 | 0.014 | 1 | 0.417 | — | 20.5 GB¹ |
| Qwen3-TTS 1.7B Base | 0.037 | **0.011** | 0.023 | 3 | 2.503 | 36 | 4.8 GB² |
| ESpeech-TTS-1 SFT 256K | 0.051 | 0.042 | 0.027 | 3 | 0.384 | 165 | 3.2 GB² |
| XTTS-v2 | 0.043 | 0.021 | 0.040 | 13 | 0.496 | 5.5 | 2.0 GB² |

¹ The whole-GPU increase during the T1 book run: 3 polish workers plus Chatterbox TTS
for Latin spans, sampled by `nvidia-smi`.
² The torch allocator peak from `synth.py`, which leaves out the CUDA context
(~0.5 GB). The challengers' `nvidia-smi` logs are unusable: earlier aborted runs leaked
samplers that kept writing into the same files. `bench.sh` now stops its sampler on
exit.

WER by category:

| System | dialogue | homograph | latin | number | poem | yo |
|---|---|---|---|---|---|---|
| T0 Silero | 0.031 | 0.005 | 0.148 | 0.028 | 0.000 | 0.003 |
| T1 hybrid | 0.047 | 0.005 | 0.193 | 0.019 | 0.000 | 0.010 |
| Qwen3-TTS | 0.000 | 0.000 | 0.284 | 0.019 | 0.000 | 0.023 |
| ESpeech | 0.063 | 0.000 | 0.136 | 0.019 | 0.172 | 0.030 |
| XTTS-v2 | 0.000 | 0.050 | 0.250 | 0.019 | 0.022 | 0.010 |

Latin-category WER overstates the errors: references are Latin, and GigaAM writes
Cyrillic (see `spikes/ru-polish/FINDINGS.md`).

Text stages (`ru` pack, whole set):

- **Normalization exact match: 50/55 (90.9 %).** Misses: «21 страницу» → «двадцать
  одна» (accusative agreement), «заплатил 1000» → «тысяча», the final period lost
  after «до н. э.», «7–8 вёрст» → «от семи до восьми» (acceptable reading),
  «Завтра 31 декабря» → «тридцать первого» (nominative date).
- **ё restored: 50/64 (78.1 %).** The misses are mostly proper names: Пугачёв (10 forms),
  Гринёв, Нижнеозёрная. One false positive showed up in the XTTS log: «буря мглою
  небо» → «нёбо».
- **Homograph stress in text (RUAccent): 56/63 (88.9 %).** Misses: ирис, уже, хлопок,
  косой, кругом, острота, воды.

## Findings for M2

1. **Every system reads the words.** All five pass the intelligibility floor: WER
   without Latin words is ≤ 0.042, and the QA gate fails 1–3 segments of 100 (XTTS-v2
   fails 13, see 4). These numbers are the QA gate's measure and do not pick a winner.
   Qwen3-TTS ties Silero without Latin words (0.011) and reads every dialogue and
   homograph segment right.
2. **All three challengers stay T1 candidates; listening decides.** Speed is the
   third priority (ADR-0007), so it does not rule out a GPU system that sounds better.
   Qwen3-TTS runs at RTF 2.5 on a 4090 (stock fp32, no compile): slower than real time
   and above NFR-05's ≤ 0.5. If it wins on listening, the options are speed work (fp16,
   compile, batching, several GPUs) or a slower best-quality setting, not dropping it.
3. **ESpeech (F5) is fast on GPU (0.38) and unusable on CPU (RTF ≈ 165).** It reads
   the poem worst (WER 0.172) even with the RUAccent `+` marks. Whether that is a
   reading error or ASR missing a stylized delivery is for the listener to judge.
4. **XTTS-v2 truncates long Russian input.** Above 182 characters coqui-tts warns and
   cuts the audio, which explains most of its 13 QA failures. To be judged fairly it
   needs sentence-level splitting below the segment level. On CPU it runs at RTF 5.5.
5. **T0 stays Silero.** T0 is a CPU budget (4 cores), and no challenger comes within
   10× of it.
6. **The T1 hybrid is the heaviest on VRAM** (20.5 GB with 3 polish workers on one
   card). That fits a free 4090 but not an 8 GB T1 GPU, so the planner must size the
   polish pool from free VRAM (TODO idea 10). An earlier T1 run hit OOM when another
   job grew on the same GPU.
7. **Text-stage gaps are `ru`-pack work, not backend work** (not fixed in this step):
   number agreement after accusative nouns and in dates, ё in proper names (a lexicon
   or name list would fix most), and the 7 homograph misses.

## Listening

Blind A/B, the owner alone, female `xenia` voice: 39 of the 40 pairs answered (each
pairs the hybrid with one other system on 10 clips) and 13 of the 100 stress clips.
Results come from `listen.py out/results-3.json`; the answers stay in the gitignored
`out/`.

| Hybrid against | Hybrid preferred | Other preferred | Tie |
|---|---|---|---|
| Silero | 6 | 3 | 1 |
| XTTS-v2 | 6 | 3 | 0 |
| ESpeech | 6 | 4 | 0 |
| Qwen3-TTS | 5 | 5 | 0 |

- Qwen3-TTS won dialogue (2–0), the poem and the number pair. It lost both homograph
  and both Latin-word pairs. It got no stress marks, and its Latin words were not
  routed to Chatterbox.
- Wrong stress flagged in the pairs: hybrid 6/39, XTTS-v2 4/9, Qwen3-TTS 2/10, ESpeech
  2/10, Silero 1/10. Wrong words flagged: Qwen3-TTS 4/10, ESpeech 3/10, hybrid 5/39,
  XTTS-v2 1/9, Silero 0/10.
- The 13 stress clips are too few to use, so stress accuracy stays the text measure
  (RUAccent 56/63).
- One listener with about 10 votes per system: these are clear preferences, not
  statistics.

## Decision (owner, 2026-10-26)

1. **T0 = Silero `xenia`.**
2. **T1 default = the hybrid** (Silero → Chatterbox VC, Chatterbox TTS for Latin
   spans). It beats or ties every challenger and is preferred to plain Silero, so the
   VC polish earns its cost.
3. **ESpeech and XTTS-v2 are dropped.**
4. **Qwen3-TTS is an M3 candidate** for expressive and dialogue segments, where it won.
   It is not a T1 replacement. It would need RUAccent stress marks, Latin spans routed
   to Chatterbox, and speed work (RTF 2.5 on a 4090).
