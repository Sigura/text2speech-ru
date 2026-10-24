# Spike `ru-smoke` — findings (2026-10-24)

Throwaway spike (never imported by `src/`). Question: can Russian text become good,
checkable speech **fully offline** on this machine, and what must M2 handle?

Pipeline tried: text → RUAccent (stress + ё) → Silero v5.6 / Piper → WAV → GigaAM-v3
→ WER. 21 sentences in `sentences.txt` (homographs, ё, dialogue dashes, classic prose,
numbers as digits and spelled out). Reproduce:

```bash
uv run python fetch.py                         # online: download + inventory
uv run python run.py                           # offline, GPU
CUDA_VISIBLE_DEVICES="" taskset -c 0-3 uv run python run.py --device cpu --tag t0
uv run python lockgen.py                       # models.lock entries
```

## Results

WER (GigaAM-v3 `rnnt`), RTF = synthesis time ÷ audio duration.

| System | WER all | digits group | other groups | RTF GPU (4090) | RTF T0 (4 cores) |
|---|---|---|---|---|---|
| **Silero `xenia`** (female) | **0.057** | 0.27 | **0.00** | **0.008** | **0.080** |
| Silero `baya` / `kseniya` (female) | 0.063 | 0.27 | 0.00–0.03 | 0.009 | 0.081 |
| Piper `irina` (female) | 0.098 | 0.41 | 0.00–0.03 | 0.17 | 0.10 |
| Piper `irina` + RUAccent ё | 0.067 | 0.32 | 0.00 | 0.16 | 0.10 |

No network connection was attempted at runtime (in-process guard; 0 attempts).

## Findings for M2

1. **Silero `xenia` is the fast-tier default candidate.** Female voice, best WER and
   ≈ 12× real time on 4 CPU cores, ≈ 125× on one GPU. One GPU worker is overkill for
   it: CPU workers can render a book; GPUs are better spent on an expressive model or
   the polish stage.
2. **Normalization is mandatory before Silero: it silently drops digits.** "В 1812
   году" is read as "В году"; "Петр I", "XVIII", "15:30", "15%", "500" vanish. Piper
   reads digits but garbles Roman numerals ("XVIII" → "воанэнвека"). With numbers
   spelled out, every system scores WER 0. The QA gate caught all of it — keep it on.
3. **QA ASR must output spoken form.** GigaAM-v3's default/`e2e_*` branches write
   digits, `₽` and `%` (inverse normalization), so correctly spoken numbers count as
   errors (spelled-out group WER 0.37 → 0.00 after switching). Use branch **`rnnt`**
   (`ctc` is the lighter alternative). Its `load_audio` shells out to **ffmpeg**.
4. **WER cannot measure stress.** ASR writes *замок* the same either way, so the
   homograph group scores 0 for everyone. Stress needs the listening set
   (`out/listen/README.md`) and, later, the human-checked homograph subset (§6.6.5).
5. **RUAccent (turbo3.1) by inspection:** correct on *зáмка/замóк*, *все/всё*,
   *дорогá*, and every ё; **wrong on "настоящей мýкой"** (marked *мук+ой*, flour).
   Silero v5.6 has its own homograph stress and ё (`put_stress_homo`, `put_yo_homo`),
   so whether RUAccent helps Silero at all is a listening question, not a given. It
   clearly helps Piper (ё: WER 0.098 → 0.067).
6. **RUAccent breaks the offline rules unless wrapped.** It downloads unpinned `main`
   files on first use, lists the repo over the network (`HfFileSystem.ls`) and writes
   the `koziev/*` POS data into **its own package directory** (not `workdir`). Offline
   loading works only because it checks for existing folders. The `ru` pack must
   place the pinned store files itself (copy/symlink `koziev/` into the package dir or
   patch `module_path`) and never let `load()` reach the download branch.
7. **Silero is not on Hugging Face** (`models.silero.ai`, `torch.package`):
   `models.lock` now supports `url` assets (content-addressed in the store). Loading
   is `torch.package.PackageImporter(path).load_pickle("tts_models", "model")` — no
   `torch.hub`, so no hidden download.
8. **GigaAM-v3 needs `pyannote.audio`** installed only because its remote code imports
   it (for long-form); transformers refuses to load otherwise. Heavy dependency for
   the QA worker environment; the M2 QA worker can vendor the model code instead.
9. **Silero speaks SSML and has an `intensity` knob** — candidates for E1 pauses and
   E2 expression without a second model.

## Not done here / next

- **Polish stage (SPEC §6.6.6):** voice conversion (Chatterbox VC, Seed-VC, OpenVoice)
  on top of Silero needs a **female target reference clip** whose use is allowed —
  the owner chooses it. Enhancement (resemble-enhance) needs no reference and can be
  tried first.
- Expressive candidates (ESpeech / F5-TTS Russian, Chatterbox Multilingual, Qwen3-TTS,
  XTTS-v2) and the full M2 benchmark protocol.
- Normalization: audit rutextnorm against the digits group.

Pinned assets: see the Russian entries in the repository's `models.lock`
(`install.sh` downloads them; 50 files, ≈ 1.1 GB).
