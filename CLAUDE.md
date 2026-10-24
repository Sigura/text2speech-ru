# CLAUDE.md

Guidance for Claude Code sessions working in this repository.

## Source of truth

- [SPEC.md](SPEC.md) is the source of truth. Read the relevant sections before
  planning; reference requirements by ID (`FR-07`, `NFR-02`, `§6.6.1`) in plans,
  commits and PRs.
- Every decision goes into the decision log (SPEC.md §13.1). Significant
  architecture decisions also get an ADR in [docs/adr/](docs/adr/).
- If implementation shows the spec is wrong or incomplete, update SPEC.md in the
  same branch (bump the version in its change log) — do not silently diverge.

## Non-negotiable rules

1. **Offline at runtime** (FR-04, §6.10). Network access happens only in
   `install.sh` / `text2audio models pull` / `bundle`. Every asset a component
   loads — weights, tokenizers, G2P dictionaries, NLP data — must be listed in
   `models.lock` with HF revision + SHA-256 and loaded from the model store. Watch
   for libraries that download on first use (`torch.hub`, RUAccent, GigaAM,
   Docling layout models, NLTK, spaCy).
2. **No universal model** (§6.6.1, ADR-0003). Every language-dependent stage
   (segmentation, normalization, stress/G2P, TTS, QA ASR, emotion) is a plugin
   chosen per language. The core never imports a specific model or language.
3. **Isolated backends** (§6.6.3, ADR-0004). TTS backends run as worker processes
   in their own uv environments; do not add model-specific heavy deps (torch,
   transformers, …) to the core package.
4. **Priorities** (ADR-0007, personal use only). Be free with licenses: offline work
   first, quality second. Record licenses in `models.lock` for information only.
5. **Safe input handling** (§6.12). `defusedxml` for XML, archive size/path
   limits, `safetensors` / `torch.load(weights_only=True)`.

## Workflow

- One milestone (SPEC.md §11) per branch: `m0-scaffold`, `m1-conversion`, …
  Exploratory/throwaway code goes in `spikes/` and is never imported by `src/`.
- Start each milestone in plan mode; the plan must map every exit criterion of
  the milestone to a test.
- Conventional Commits; keep CHANGELOG.md (`Unreleased` section) up to date.
- Before finishing: `uv run ruff check . && uv run ruff format --check . &&
  uv run mypy src && uv run pytest`.

## Local environment

- Use `uv` (installed at `~/.local/bin/uv`) with Python 3.11+. The `python3` on
  PATH is 3.7 (conda env `t110`) — never use it for this project; uv may pick it
  up too, so pass `--python 3.11` (or 3.12, `/usr/bin/python3.12`) explicitly.
- GPUs: 3× RTX 4090 (24 GB) + RTX 3090 Ti (24 GB); 128 CPU cores, 377 GB RAM. For
  T0 (CPU tier) benchmarks, restrict to 4 cores / 8 GB (e.g. `taskset`, cgroup)
  and hide GPUs (`CUDA_VISIBLE_DEVICES=""`).
- `ffmpeg` currently only exists inside a conda env (`t110`); prefer a system
  ffmpeg and resolve it via `doctor`. `espeak-ng` is installed (`/usr/bin`).
- Language order: **Russian first** (M2), English second (M4) — ADR-0008.
