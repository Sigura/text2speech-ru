# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Project specification ([SPEC.md](SPEC.md)), approved for milestone M0.
- README, CONTRIBUTING, CLAUDE.md, architecture decision records 0001–0007 and the
  language-pack guide.
- ADR-0008: Russian is the first language (M2), English second (M4); SPEC.md 1.2-M0.
- M0 scaffold: `text2audio` CLI and Python API with `doctor`, `resources`, `plugins`,
  `models list/verify/pull`, `version` (other commands report their milestone).
- Plugin discovery via entry points for every pipeline stage and covers.
- Model store with `models.lock` (pinned revision + SHA-256) and `models pull`.
- Offline contract: HF offline flags and caches redirected into the model store;
  socket-free test suite and `scripts/test-offline.sh`.
- Backend worker protocol 1 (NDJSON over stdin/stdout) with crash restart, and a
  `sine` dummy backend.
- Resource planning: usable CPU/RAM/GPU within affinity, cgroup and
  `CUDA_VISIBLE_DEVICES` limits; worker slots from free VRAM with NUMA-pinned CPUs.
- ADR-0009 and SPEC.md 1.3-M0: covers (embedded → local Stable Diffusion WebUI →
  typographic) and the loopback-only local-service exception.
- SPEC.md: the editable `<book>.md` lives next to the audio; the original input is
  copied into `_work/` for `convert --reconvert`; `.md` input accepted.
- CI (lint, format, types, tests, strict offline job) and pre-commit hooks.

### Changed

- QA gate ASR model and WER threshold come from the language profile (SPEC.md §6.9).
