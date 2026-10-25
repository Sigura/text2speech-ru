# 0010. Plugin worker environments and the worker runtime

- **Status:** Accepted
- **Date:** 2026-10-25
- **Spec references:** §6.6.3, §6.10, NFR-02, ADR-0004

## Context

ADR-0004 puts every TTS backend in its own environment, but left open where the
environments live, how they are created and how a worker speaks the protocol without
the core installed. M2 brings the first real workers (Silero, RUAccent, later GigaAM and
Chatterbox), whose pins already conflict (spike `ru-polish`), and components such as
RUAccent and GigaAM need heavy libraries the core must not import either.

## Decision

- A plugin that needs heavy libraries ships a **uv project next to its descriptor**
  (`src/<package>/env/pyproject.toml` + `uv.lock`, `package = false`). It is excluded from
  the workspace and does not depend on `text2audio`.
- The environment lives **outside the repository** at `<data dir>/text2audio/envs/<name>`
  (`T2A_ENV_HOME` overrides), created by `text2audio envs sync` / `install.sh` with
  `uv sync --frozen` for Python 3.12 — install time, the only time it uses the network.
- The descriptor declares `env` and `env_project`; the core starts the worker as
  `<env>/bin/python <plugin>/worker.py`. A missing environment is an actionable error
  (`text2audio envs sync <name>`), also reported by `doctor`.
- Backends **and components** (stress/G2P, ASR, polishers) use the same `Worker` client
  and protocol 1; new operations (`process`, `transcribe`, `polish`) are additive.
- Workers share a **standard-library runtime**, `t2a_worker.py`, put on their
  `PYTHONPATH` by the core. Imported first, it blocks every non-loopback socket when
  `T2A_NO_NETWORK=1` (always set by the core), so a library that downloads on first use
  fails loudly instead of fetching. It also keeps library output off the protocol stream.
- Workers limit their thread pools to the resource slot: `torch.set_num_threads`, and
  onnxruntime sessions capped explicitly (onnxruntime sizes its pools from the machine's
  cores, not the affinity mask: RUAccent ran 512 threads on 2 CPUs).

## Consequences

- One backend's pins never affect another or the core; the M2 benchmark challengers can
  be added the same way.
- CPU-only backends (Silero) lock CPU torch wheels, keeping their environment small.
- Each environment costs disk (Silero ≈ 0.8 GB, RUAccent ≈ 0.2 GB) and an `envs sync`
  after its lock file changes.
- The air-gapped bundle (M5) must pack the environments' wheels as well.
