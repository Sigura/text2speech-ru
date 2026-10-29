# 0009. Book covers: extract first, generate with a local Stable Diffusion service

- **Status:** Accepted
- **Date:** 2026-10-24
- **Spec references:** §6.8.2, §6.10, §9.1, FR-03, FR-23

## Context

MP3/M4B output embeds a cover (FR-09). FB2 and EPUB usually carry one; TXT, many
PDFs and some FB2 files do not. The owner already runs AUTOMATIC1111's Stable
Diffusion WebUI on this machine (conda env `sdui`, 63 local checkpoints, used by
another local service on port 8801). Its
weights are local files from Civitai, not Hugging Face repositories, and it runs in
its own environment with its own torch.

## Decision

- Cover source order: **embedded cover** (FB2 `<coverpage>`, EPUB cover image) →
  **generated cover** (cover plugin) → **typographic cover** (title and author drawn
  with Pillow and a bundled Cyrillic-capable font). The last always works offline.
- Cover generators are plugins (`text2audio.covers`). The first one, `sd-webui`, starts
  **its own** WebUI process: API only (`--nowebui --api`), bound to 127.0.0.1 on a free
  port, with `--skip-prepare-environment --skip-install --skip-version-check`, the HF
  offline flags set, and `CUDA_DEVICE_ORDER=PCI_BUS_ID`. It runs on the **first GPU,
  in nvidia-smi order, with enough free VRAM** (`first_gpu_with`, §9.1). It is
  stopped when the covers are done. An instance someone else runs (such as the bot's)
  is never reused or changed.
- **Local-service exception to the offline contract:** a plugin may talk HTTP to a
  process that text2audio started itself on the loopback interface. No other host is
  allowed.
- External tools with their own weights (SD WebUI, like FFmpeg) are configured by
  path and checked by `doctor`. Their weights are not in `models.lock`; the chosen
  checkpoint's SHA-256 is recorded in `metadata.json` so a cover can be reproduced.
- Prompts come from metadata (genre, annotation, title, author). Text in the image is
  never generated; title and author are drawn on top by the typographic renderer.

## Consequences

- Covers work on every machine: the typographic fallback needs no GPU or model.
- Russian metadata needs an English prompt for SD. FB2 genre codes map to English
  style keywords; translating titles or annotations needs an offline MT model and is
  an open question for M2.
- The WebUI checks are best-effort: it can still try to reach the network on start-up
  if its cache is incomplete. The offline CI job does not cover it; it is tested
  against a fake API.
