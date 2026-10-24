# 0001. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-10-24
- **Spec references:** §13.1

## Context

The project mixes fast-moving third-party models with long-lived architecture. Without
a record, the reasons behind choices (why a model was rejected, why a license matters)
get lost and decisions are re-argued.

## Decision

Significant decisions are recorded as ADRs in `docs/adr/` and summarized in the
SPEC.md decision log (§13.1). Smaller choices are recorded only in the decision log.

## Consequences

- Each milestone that changes architecture adds an ADR in the same PR.
- ADRs are not edited after acceptance; they are superseded.
