# Architecture Decision Records

Each ADR records one significant decision: its context, the decision, and its
consequences. ADRs are immutable once accepted; a later ADR supersedes an earlier one
instead of editing it. Format: [Michael Nygard's format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).
Every ADR also appears in the SPEC.md decision log (§13.1).

| ADR | Title | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-offline-runtime-contract.md) | Offline runtime contract | Accepted |
| [0003](0003-per-language-components.md) | No universal model: per-language components | Accepted |
| [0004](0004-isolated-backend-workers.md) | Isolated TTS backend workers | Accepted |
| [0005](0005-mp3-default-section-split.md) | MP3 default with per-section split and single-file fallback | Accepted |
| [0006](0006-russian-second-language.md) | Russian as the second language | Superseded in part by 0008 |
| [0007](0007-personal-use-offline-quality-priority.md) | Personal use: offline first, quality second, license not a criterion | Accepted |
| [0008](0008-russian-first-language.md) | Russian as the first language | Accepted |
| [0009](0009-covers-and-local-services.md) | Book covers: extract first, generate with a local Stable Diffusion service | Accepted |

New ADR: copy [template.md](template.md), take the next number, open a PR.
