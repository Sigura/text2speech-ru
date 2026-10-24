# Contributing

## Development setup

Requirements: Linux or macOS, [uv](https://docs.astral.sh/uv/), FFmpeg, espeak-ng.

```bash
uv sync                      # creates .venv with Python 3.11+ and dev tools
uv run pre-commit install
uv run pytest
```

Model weights are not needed for most unit tests. Tests that need models are marked
`@pytest.mark.models` (CPU) or `@pytest.mark.gpu` and are skipped when assets are
missing.

## Ground rules

Read [SPEC.md](SPEC.md) first. In particular:

- **Offline at runtime** — no code path outside install/pull/bundle may touch the
  network. The offline CI job enforces this.
- **No universal model** — language-specific logic and models belong in plugins,
  never in `src/text2audio/core/`.
- **Licenses** — Be free, offline work first, quality second.

## Workflow

- Work directly on `main` (personal project: no feature branches, PRs or releases);
  small commits, each passing the checks below.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat(readers): parse FB2 footnotes`).
- Reference requirement IDs from SPEC.md in commit messages (`Implements FR-02`).
- Add an entry under `Unreleased` in [CHANGELOG.md](CHANGELOG.md) for user-visible
  changes.
- Decisions that change the architecture need an ADR in [docs/adr/](docs/adr/) and a
  line in the SPEC.md decision log (§13.1).
- If your change makes the spec inaccurate, update SPEC.md in the same commit.

## Checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

## Adding a language or model

See [docs/adding-a-language.md](docs/adding-a-language.md).
