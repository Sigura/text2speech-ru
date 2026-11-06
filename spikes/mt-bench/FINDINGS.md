# Spike `mt-bench`: offline ru→en machine translation for whole books

Owner's after-M4 item: a stronger offline MT than Opus-MT for `text2audio translate`.
Run 2026-11-06 on one RTX 4090 (shared), bf16, greedy decoding for LLMs, 4 beams for
seq2seq models.

## Sets

- **FLORES-200 devtest** `rus_Cyrl` → `eng_Latn`, 1012 sentences with references,
  scored with sacrebleu (BLEU, chrF++) and COMET-22 (`Unbabel/wmt22-comet-da`).
- **Literary excerpt**: 40 paragraphs of *Капитанская дочка* from chapter I
  (`tests/fixtures/ru/large`, 14.2 k characters, no reference), for reading by the
  owner (`out/ru-en/lit-samples.html`, blind letters). Seq2seq models get it sentence by
  sentence, LLMs paragraph by paragraph.

## Results

| Model | COMET-22 | chrF++ | BLEU | FLORES chars/s | literary chars/s | VRAM GB | Licence |
|---|---|---|---|---|---|---|---|
| Tower-Plus-9B | **0.8755** | **62.8** | **37.6** | 701 | 127 | 19.3 | CC-BY-NC-SA-4.0 |
| Seed-X-PPO-7B | 0.8731 | 62.6 | 36.2 | 1092 | 202 | 15.1 | OpenMDW |
| NLLB-200 3.3B | 0.8680 | 61.3 | 36.9 | 2214 | 1030 | 11.2 | CC-BY-NC-4.0 |
| MADLAD-400 3B | 0.8677 | 61.5 | 36.9 | 486 | 332 | 9.0 | Apache-2.0 |
| Qwen3-8B (no thinking) | 0.8658 | 60.0 | 33.8 | 857 | 167 | 16.3 | Apache-2.0 |
| NLLB-200 distilled 1.3B | 0.8633 | 60.6 | 36.7 | 2235 | 934 | 6.0 | CC-BY-NC-4.0 |
| Opus-MT ru-en (current) | 0.8385 | 55.5 | 29.3 | 8533 | 3968 | 1.0 | CC-BY-4.0 |

A whole novel (*Капитанская дочка*, 214 k characters) at the literary rate: Tower-Plus
≈ 28 min, Seed-X ≈ 18 min, Qwen3 ≈ 21 min, NLLB-3.3B ≈ 3.5 min, Opus-MT ≈ 1 min
(measured by `text2audio ru2en` on the GPU: 1 min 14 s; 71 min 47 s on 4 CPU threads).

## Findings

1. Every candidate beats Opus-MT clearly (+0.025 to +0.037 COMET, +5 to +7 chrF++);
   Opus-MT also garbles literary sentences («Mother was still my belly»).
2. The top four lie within 0.008 COMET; Tower-Plus-9B is first on every metric,
   Seed-X-7B is second at 1.6× its speed and 4 GB less VRAM.
3. NLLB-3.3B is the speed/quality middle: COMET 0.868 at 8× Tower-Plus's literary
   speed, 11 GB — it fits beside other jobs on a shared card.
4. The LLMs need 15–19 GB, i.e. most of a free 24 GB card; on this shared machine
   they wait for one (`bench.sh` waits for 21 GB).

## Owner's blind reading (ru→en, 2026-11-06)

12 paragraphs sent, 7 answered (A = Seed-X, B = Tower-Plus, E = Opus-MT, G = NLLB-3.3B):
¶1 Seed-X slightly ahead of Tower-Plus; ¶2 tie; ¶3 Opus-MT «brilliant», NLLB-3.3B «not
bad»; ¶5 NLLB-3.3B; ¶7 Seed-X; ¶8 and ¶9 Tower-Plus. So Seed-X 2, Tower-Plus 2,
NLLB-3.3B 1 (+1 runner-up), Opus-MT 1. In ¶3 and ¶5 the sentence-level models kept
Pushkin's French «pour être outchitel» and «дворовыми» (yard boys), which the LLMs
smoothed away; Seed-X also wrote one name in mixed scripts («Boпре»).

**Decision (owner asked for one from these answers): Seed-X-PPO-7B for ru→en, NLLB-3.3B
as the fallback.** Seed-X ties Tower-Plus by ear and is 0.002 COMET behind, but 1.6×
faster and needs 15 GB instead of 19 on the shared GPU; NLLB-3.3B runs when no GPU has
room for Seed-X and re-translates sentences that come back in mixed scripts.
Implementation is the last stage (owner: en→ru first).

## en→ru (`MT_BENCH_PAIR=en-ru`, `bench-en-ru.md`)

FLORES-200 `eng_Latn` → `rus_Cyrl` and 40 paragraphs of "A Scandal in Bohemia"
(`tests/fixtures/en/large`, 10.2 k characters; `out/en-ru/lit-samples.html`, its own
blind letters):

| Model | COMET-22 | chrF++ | BLEU | literary chars/s | VRAM GB |
|---|---|---|---|---|---|
| Seed-X-PPO-7B | **0.9099** | **59.2** | **35.2** | 180 | 14.5 |
| Tower-Plus-9B | 0.9090 | 57.4 | 32.8 | 156 | 18.4 |
| NLLB-200 3.3B | 0.8917 | 56.2 | 32.2 | 668 | 12.8 |
| MADLAD-400 3B | 0.8884 | 56.9 | 32.1 | 225 | 9.8 |
| NLLB-200 1.3B | 0.8878 | 54.8 | 30.4 | 783 | 6.1 |
| Qwen3-8B | 0.8844 | 52.0 | 26.8 | 148 | 15.9 |
| Opus-MT en-ru | 0.8473 | 50.7 | 25.4 | 2948 | 1.1 |

Seed-X leads in this direction too, and by more over NLLB (+0.018). Opus-MT, reading
sentence by sentence, makes the narrator female in ¶1 («Я редко слышала»); every other
model writes «слышал».

**Owner's blind reading (en→ru, letters A = MADLAD, B = Tower-Plus, C = Opus-MT,
D = NLLB-1.3B, E = Seed-X, F = Qwen3, G = NLLB-3.3B):** ¶1 Tower-Plus and Seed-X, ¶2
Tower-Plus, ¶3 Seed-X, ¶4 Tower-Plus, ¶5 Tower-Plus — «one option significantly
better». **Decision: Tower-Plus-9B leads en→ru** (COMET 0.9090, level with Seed-X's
0.9099), Seed-X when no GPU has the 20 GB Tower-Plus needs, NLLB-3.3B by sentence for
failed pieces.

## Whole book through `text2audio en2ru` (2026-11-07)

*The Adventures of Sherlock Holmes* (`tests/fixtures/en/large`, 575 k characters, 2544
pieces) on one RTX 4090: **1 h 43 min** (6211 s, ≈ 93 characters/s) with Tower-Plus-9B
leading; 48 pieces (1.9 %) failed a check and were taken from NLLB-3.3B by sentence, 6
were redone with abbreviation placeholders, none was left with a warning. Slower than
the benchmark's 156 characters/s: the pipeline batches pieces in book order, so short
ones wait for long ones in a padded batch (sorting by length is the obvious next step).

## Ensemble (MBR selection, `mbr.py`, ru→en FLORES)

Per sentence, the candidate that agrees most with the others (COMET-22 or chrF++ with
the other candidates as pseudo-references), scored against the real references:

| Selection | COMET-22 | chrF++ |
|---|---|---|
| MBR-COMET, 5 models (Tower-Plus, Seed-X, NLLB-3.3B, MADLAD, Qwen3) | **0.8772** | 62.9 |
| MBR-COMET, Seed-X + Tower-Plus + NLLB-3.3B | 0.8767 | 63.1 |
| Tower-Plus-9B alone | 0.8755 | 62.8 |
| MBR-COMET, Seed-X + NLLB-3.3B + MADLAD | 0.8748 | 62.6 |
| MBR-chrF++, 5 models | 0.8747 | 63.1 |
| Seed-X alone | 0.8731 | 62.6 |

MBR gains at most +0.0017 COMET over the best single model — partly metric bias, as
COMET both selects and judges — for 2–5× the translation time (a novel: ≈ 50–90 min
instead of 18). chrF++ selection does not beat Tower-Plus alone. **Not adopted for
whole books**; the ensemble is used only where one model demonstrably fails: an LLM
translates paragraphs, and a paragraph that fails a check (abbreviations lost, mixed
scripts, length ratio, sentence count) is re-translated sentence by sentence by NLLB.

## Reproduce

```bash
MT_BENCH_HOME=/mnt/devbox/text2audio/spikes/mt-bench spikes/mt-bench/setup.sh   # online
MT_BENCH_HOME=/mnt/devbox/text2audio/spikes/mt-bench spikes/mt-bench/bench.sh   # offline
"$MT_BENCH_HOME/venv/bin/python" spikes/mt-bench/samples.py                      # reading page
"$MT_BENCH_HOME/venv/bin/python" spikes/mt-bench/mbr.py [key ...]                # ensemble
```
