# Spike `pdf-readers` — findings (2026-10-25)

Throwaway spike (never imported by `src/`). Question (SPEC §6.3, M1): which PDF reader
is the default for text-layer PDFs — pymupdf4llm, Docling or Marker?

Test PDFs are typeset here from known text, so the ground truth is exact: A5 pages,
DejaVu Serif, justified text hyphenated at line ends, a running header (book title)
and a page number on every page, epigraphs, italics. Two books: the Pushkin fixture
(2 pages) and two chapters of a modern Russian novel from the owner's library
(20 pages; local only, not committed).

```bash
uv run python spikes/pdf-readers/make_sources.py                                   # truth + HTML
uv run --project spikes/pdf-readers/pymupdf python spikes/pdf-readers/make_pdfs.py # PDFs
uv run --project spikes/pdf-readers/pymupdf python spikes/pdf-readers/run_reader.py pymupdf4llm --offline
uv run --project spikes/pdf-readers/pymupdf python spikes/pdf-readers/run_reader.py pymupdf4llm-classic --offline
uv run --project spikes/pdf-readers/docling python spikes/pdf-readers/run_reader.py docling   # then --offline
python3 spikes/pdf-readers/score.py                                                # out/report.md
```

## Results (20-page book; RTX 4090 idle, CPU timings)

| Reader | Word recall | Precision | Header leaks | Page numbers | Headings | s/page | Offline |
|---|---|---|---|---|---|---|---|
| **pymupdf4llm 1.28.2, layout model** | 0.997 | 0.996 | **0** | **0** | 1.00 | 0.40 | models inside the wheel |
| pymupdf4llm, classic (no layout) | 0.997 | 0.983 | 20 | 20 | 1.00 | 0.07 | yes |
| Docling 2.136.0 | 0.998 | 0.994 | 0 | 13 | 1.00 | 0.29 | 164 MB layout model from HF on first use |
| Marker 2.0.0 | — | — | — | — | — | — | starts a vLLM Docker server for a VLM |

## Findings

1. **Default: pymupdf4llm 1.28.2 with its layout model.** No running headers or page
   numbers leak, paragraphs are re-flowed, line-end hyphenation is re-joined, headings
   found, 0.4 s/page on CPU. Its ONNX layout models ship **inside** the
   `pymupdf_layout` wheel (≈ 50 MB, onnxruntime on CPU) — nothing is downloaded, so
   `uv.lock` hashes pin them. Pin `pymupdf4llm==1.28.2`: **PyMuPDF 2.0 (2026-10-19)
   absorbed pymupdf4llm** and makes 2.0 an empty placeholder package.
2. Classic mode (no layout model) leaks every running header and page number — the
   layout model is what makes pymupdf4llm good.
3. **Docling** is equally good on text but leaked 13 of 20 page numbers, turned
   dialogue dashes into list items (the dash is lost), and downloads its layout model
   on first use (`docling-project/docling-layout-heron`, 164 MB — would need pinning).
   A possible reader plugin later; not the default.
4. **Marker 2.x is disqualified:** its Surya 2 OCR runs as a vLLM server in Docker
   (`vllm/vllm-openai`, a 23 GB image) serving `datalab-to/surya-ocr-2`; it also
   ignores `MODEL_CACHE_DIR` for its state. Far outside the offline, in-process model.
5. **Every reader** splits words hyphenated across a page break ("прон" | "икавшие")
   and paragraphs that cross a page. Our clean-up repairs both without language
   knowledge: a paragraph that starts with a lowercase letter continues the previous
   one (dropping a trailing line-end hyphen).
6. **Every reader** loses the hyphen of a real compound broken at a line end
   ("по-настоящему" → «понастоящему», "где-нибудь" → «гденибудь»; 2 per 20 pages).
   Restoring them needs language rules (`-нибудь`, `-либо`, `-то`, `кое-`, `по-…ски`):
   a `ru` pack normalization item for M2.
