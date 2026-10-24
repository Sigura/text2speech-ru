# Text2Audio

**Offline, multi-language document-to-audiobook pipeline.**

Text2Audio turns `.txt`, `.fb2` / `.fb2.zip`, `.epub` and `.pdf` files into editable
Markdown and then into an audiobook — MP3 split by book section by default — using
local neural text-to-speech. After installation it runs with no network access.

> **Status:** design approved, implementation starting (milestone M0). Nothing is
> installable yet. See [SPEC.md](SPEC.md) for the full specification.

## Highlights (planned for v1)

- **Fully offline** after install; air-gapped install via an exportable bundle.
- **Best-of-breed per language** — each language picks its own TTS model, text
  normalization, stress/pronunciation and quality-check speech recognition. Russian
  first, English second.
- **Zero-config quality** — language and content profiles choose voices, pauses,
  normalization and models automatically; configuration only overrides.
- **Expressive narration** — structural prosody and a separate dialogue voice by
  default; emotion control on backends that support it.
- **Automatic quality check** — every segment is transcribed back and re-generated
  if the model skipped or invented words.
- **Audiobook-ready output** — one folder per book, one MP3 per section (falls back
  to a single file when the structure is unreliable), optional M4B with chapters.
- **Resumable** — edit the Markdown and only changed parts are re-synthesized.

## Planned usage

```bash
./install.sh --lang ru --tier fast          # downloads everything once
text2audio run "War and Peace.fb2.zip"      # → output/War and Peace/*.mp3
text2audio run books/ --format mp3,m4b      # one folder per book
text2audio doctor                           # offline readiness check
```

## Documentation

| Document | Purpose |
|---|---|
| [SPEC.md](SPEC.md) | Requirements, architecture, roadmap, decision log |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [docs/adding-a-language.md](docs/adding-a-language.md) | How to add a language pack |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup and conventions |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

## License

Be free, offline work first, quality second
