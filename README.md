# Text2Audio

**Offline books → audiobooks, in Russian and English, with local neural voices.**

Text2Audio turns `.fb2` / `.fb2.zip`, `.epub`, `.txt`, `.pdf` and `.md` files into an
editable Markdown text and then into an audiobook: one MP3 per chapter with subtitles,
an M4B of the whole book, tags and a cover. It can also translate a book between
English and Russian first. Everything runs on your machine; after installation nothing
touches the network. Personal use only ([ADR-0007](docs/adr/0007-personal-use-offline-quality-priority.md)).

**Status:** milestones M0–M4 are done (conversion, Russian and English speech,
expression); M5 (hardening, air-gapped bundle) is in progress — see [TODO.md](TODO.md).

## Install

```bash
./install.sh                              # Russian: Python env, worker envs, weights, doctor
./install.sh --lang ru,en                 # Russian and English
./install.sh --lang ru --tier fast        # CPU voices only (smaller)
text2audio models pull --feature translate   # optional: book translation models (~51 GB)
text2audio doctor                         # everything ready? exit 0 = offline-ready
```

**A machine without internet:** make a bundle where text2audio is installed, carry it
over, and install from it. Nothing is downloaded on the target.

```bash
text2audio bundle export -o t2a.tar --lang ru --tier fast   # default: every language and tier
# on the offline machine (FFmpeg and espeak-ng from its own packages):
tar xf t2a.tar && text2audio/install.sh --bundle             # ends with doctor
```

Needs Linux, FFmpeg and espeak-ng; a GPU is optional (CPU voices are fast). Every
downloaded file is pinned by revision and SHA-256 in `models.lock`. Other setups (uv,
conda, pip): [CONTRIBUTING.md](CONTRIBUTING.md).

## Use

**A book → an audiobook**

```bash
text2audio run "Мастер и Маргарита.epub"     # → output/Мастер и Маргарита/
text2audio run books/ -o ~/Audiobooks        # every book in a folder
```

The language is read from the book (or `--lang`). Without a GPU it uses the fast CPU
voices; with one, the expressive tier where it is the language's default
(`--tier fast|expressive`). Every sentence is transcribed back and re-generated if
words were lost (`--qa off` to skip). An interrupted run resumes where it stopped.

**Check the text first, then listen**

```bash
text2audio convert "Мастер и Маргарита.epub"                         # editable Markdown only
$EDITOR "output/Мастер и Маргарита/Мастер и Маргарита.md"             # fix typos, drop front matter
text2audio preview "output/Мастер и Маргарита" --chapter 3 --seconds 30   # a short sample
text2audio synthesize "output/Мастер и Маргарита"                    # re-renders only what changed
```

**Fix how a word is read** — add `word<TAB>replacement` (`+` before the stressed vowel)
to the book's `_work/lexicon.tsv`, or to `~/.config/text2audio/lexicon/ru.tsv` for all
books, and run `synthesize` again:

```text
Воланд	Вол+анд
Kubernetes	кубер+нетис
```

English words in Russian text are read without entries (acronyms spelled out, words
transliterated or voiced by the GPU model); `report.md` lists how each one was read.

**Direct the reading** with comments in the `.md` — a voice, a pause, a rate, an emotion,
or text to skip; one on its own line holds until `reset` (SPEC §6.4.1):

```markdown
<!-- t2a: voice=dialogue emotion=fearful -->
— Кто там? — прошептала она.
<!-- t2a: reset pause=2000 -->
```

**Translate a book, then listen to it**

```bash
text2audio en2ru "Adventures of Sherlock Holmes.md"   # → Adventures of Sherlock Holmes.ru.md
text2audio run "Adventures of Sherlock Holmes.ru.md"  # a Russian audiobook of it
text2audio translate "output/Книга" --to en           # a book folder, any direction (ru2en)
```

Translation keeps headings, quotes, poems, footnotes and directives; English → Russian
uses Tower-Plus-9B paragraph by paragraph on a GPU with 20 GB free (Seed-X or NLLB with
less), Russian → English Seed-X (Opus-MT without the translation models); every
paragraph is checked and abbreviations are kept as they are ([ADR-0011](docs/adr/0011-book-translation-pipeline.md)).
A run resumes from its cache.

**Re-voice English speech in Russian**: one command for a book, a recording or a video.

```bash
text2audio revoice lecture.mp4     # → lecture.ru.mp4 (+ lecture.ru.srt), timed to the original
text2audio revoice podcast.mp3     # → podcast.ru.mp3
text2audio revoice "Book.epub"     # → the Russian .md and its audiobook
```

Video keeps its picture, and the original audio track is replaced. Each phrase starts
at its original time and is sped up by at most 1.3× to fit; phrases that still run more
than 1 s late are listed ([ADR-0012](docs/adr/0012-revoicing-recordings.md)).

**A new cover** without converting again:

```bash
text2audio cover "output/Мастер и Маргарита" --mode generate   # auto | embedded-only | generate | typographic
```

Covers come from the book, from a local Stable Diffusion WebUI (`[cover.sd] dir`), or are
drawn from the title. `synthesize` then puts the new cover into the audio files.

## What you get

```text
output/Война и мир/
├── 01 - Том первый. Часть первая.mp3   # one MP3 per section (or one file with chapters)
├── 01 - Том первый. Часть первая.srt   # subtitles with the book's text
├── …
├── Война и мир.m4b                     # the whole book with chapter marks
├── Война и мир.m3u
├── Война и мир.md                      # the editable text
├── report.md / report.json             # quality check, timings, flagged sentences
└── _work/                              # source copy, metadata, cover, cache
```

## Settings

Nothing needs configuring. To change a default, put it in
`~/.config/text2audio/config.toml` (all books) or the book's `_work/text2audio.toml`:

```toml
[synthesis]
tier = "expressive"                  # fast (CPU voices) | expressive (GPU)

[output]
formats = ["mp3", "m4b"]
split   = "auto"                     # auto | section | none

[resources]
devices     = ["cuda:0"]             # leave other GPUs to other jobs
max_workers = 4
```

`text2audio config show <book>` prints every effective setting and where it came from;
`text2audio voices` lists the voices; `text2audio resources` shows the CPUs, RAM and
GPUs it may use and the workers it would start.

## Documentation

| Document | Purpose |
|---|---|
| [SPEC.md](SPEC.md) | Requirements, architecture, CLI (§6.11), decision log (§13.1) |
| [TODO.md](TODO.md) | Roadmap and open items |
| [docs/adr/](docs/adr/) | Architecture decisions |
| [docs/adding-a-language.md](docs/adding-a-language.md) | Adding a language pack |
| `spikes/*/FINDINGS.md` | Benchmarks behind the model choices (voices, PDF readers, translation) |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Development setup, conventions, checks |
| [CHANGELOG.md](CHANGELOG.md) | Change history |

## License

Personal use only, non-commercial (see [LICENSE](LICENSE) and ADR-0007).
