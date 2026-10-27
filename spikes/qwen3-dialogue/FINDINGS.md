# Qwen3-TTS dialogue spike (M3 step 7)

Question: should Russian dialogue lines on T1 be read by Qwen3-TTS 1.7B Base (cloned
from the `xenia` reference) instead of the hybrid (Silero `xenia` → Chatterbox VC)?
The M2 benchmark listening had Qwen3 winning the dialogue lines 2–0 (`spikes/ru-bench`).

```text
uv run --python 3.11 python spikes/qwen3-dialogue/collect.py      # 21 hybrid segments
CUDA_VISIBLE_DEVICES=0 uv run --project spikes/ru-bench/envs/qwen3 \
    python spikes/qwen3-dialogue/synth.py                         # 59 Qwen3 segments
uv run --python 3.11 python spikes/qwen3-dialogue/pairs.py [score]
```

80 segments in all. Lines: 20 short dash-dialogue paragraphs of «Капитанская дочка»
(21 segments), the RUAccent stress given to Qwen3 as U+0301; 3 homograph pairs and 5
Latin-script sentences from the M2 set.

## Results

| | |
|---|---|
| Blind pairs, owner (Qwen3 batched vs hybrid) | **hybrid 10 of 10 decided pairs**; the owner stopped after 10 of 21: "difference too big" |
| RTF, one line per call (bf16, sdpa, RTX 4090) | 2.44 — no faster than fp32 in M2 (2.5); the per-token loop dominates |
| RTF, one batch of 21 lines | 0.24 (10× faster), peak VRAM 18.4 GB |
| Stress marks (U+0301), Latin words | clips sent; not judged — moot after the pairs |

## Decision

Qwen3-TTS is **not** integrated; T1 dialogue stays on the hybrid (owner's listening,
SPEC §13.1). The M2 2–0 came from two lines; on 10 the hybrid won every time. Batching
would make Qwen3 fast enough, so if a later model or the instruct variant is tried for
E2 (§6.7), batch it.

Side finding: the hybrid line q04 read «неба» as «нёба» — the `ru` ё restoration
turns «небо» into «нёбо» (M3 step 8).
