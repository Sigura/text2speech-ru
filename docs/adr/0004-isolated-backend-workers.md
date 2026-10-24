# 0004. Isolated TTS backend workers

- **Status:** Accepted
- **Date:** 2026-10-24
- **Spec references:** §6.6.3, NFR-02

## Context

TTS models pin different and often incompatible versions of `torch`, `transformers`
and related libraries. Installing several backends into one environment eventually
fails, and per-language best-of-breed (ADR-0003) makes several backends the normal
case.

## Decision

- Each backend runs in its own uv-managed virtual environment as a long-lived worker
  process.
- Core and worker communicate over newline-delimited JSON on stdin/stdout, with a
  versioned protocol (`"protocol": 1`); audio is exchanged as files, not over the pipe.
- Backends whose dependencies are compatible with the core (e.g. ONNX-based) may run
  in-process behind the same interface.

## Consequences

- Backends can be added, upgraded or removed without affecting each other.
- Extra process start-up cost and disk usage per environment; amortized by keeping
  workers alive for a whole book.
- The core needs process supervision: start-up timeouts, crash restart, and clear
  errors when a worker environment is missing.
