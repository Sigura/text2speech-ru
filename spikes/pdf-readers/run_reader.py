"""Spike step 3: run one PDF reader over out/pdf/*.pdf → out/<reader>/<name>.md.

    uv run --project spikes/pdf-readers/pymupdf python spikes/pdf-readers/run_reader.py pymupdf4llm
    uv run --project spikes/pdf-readers/pymupdf python spikes/pdf-readers/run_reader.py pymupdf4llm-classic
    uv run --project spikes/pdf-readers/docling python spikes/pdf-readers/run_reader.py docling
    CUDA_VISIBLE_DEVICES=2 uv run --project spikes/pdf-readers/marker python spikes/pdf-readers/run_reader.py marker

Model downloads (Docling, Marker) go to models/ in this spike; add --offline to run
with networking refused in-process, to prove the cached models suffice.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).parent
MODELS = HERE / "models"
os.environ.update(
    HF_HOME=str(MODELS / "hf"),
    TORCH_HOME=str(MODELS / "torch"),
    MODEL_CACHE_DIR=str(MODELS / "datalab"),  # Marker / Surya
)
ATTEMPTS: list[str] = []
if "--offline" in sys.argv:
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    _connect = socket.socket.connect

    def _guard(self: socket.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) else None
        if host is not None and host not in ("127.0.0.1", "::1", "localhost"):
            ATTEMPTS.append(repr(address))
            raise OSError(f"offline guard: {address!r}")
        _connect(self, address)  # type: ignore[arg-type]

    socket.socket.connect = _guard  # type: ignore[method-assign]


def pymupdf4llm_reader(layout: bool) -> Callable[[Path], str]:
    import pymupdf4llm

    pymupdf4llm.use_layout(layout)

    def read(path: Path) -> str:
        try:
            return str(pymupdf4llm.to_markdown(str(path), header=False, footer=False))
        except TypeError:
            return str(pymupdf4llm.to_markdown(str(path)))

    return read


def docling_reader() -> Callable[[Path], str]:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions(do_ocr=False, do_table_structure=False)
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )
    return lambda path: converter.convert(str(path)).document.export_to_markdown()


def marker_reader() -> Callable[[Path], str]:
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    converter = PdfConverter(artifact_dict=create_model_dict())

    def read(path: Path) -> str:
        text, _, _ = text_from_rendered(converter(str(path)))
        return str(text)

    return read


def main() -> None:
    name = sys.argv[1]
    factories: dict[str, Callable[[], Callable[[Path], str]]] = {
        "pymupdf4llm": lambda: pymupdf4llm_reader(True),
        "pymupdf4llm-classic": lambda: pymupdf4llm_reader(False),
        "docling": docling_reader,
        "marker": marker_reader,
    }
    t = time.monotonic()
    read = factories[name]()
    load_s = time.monotonic() - t
    out = HERE / "out" / name
    out.mkdir(parents=True, exist_ok=True)
    timings = {"load_s": load_s, "files": {}}
    for pdf in sorted((HERE / "out/pdf").glob("*.pdf")):
        t = time.monotonic()
        md = read(pdf)
        elapsed = time.monotonic() - t
        (out / f"{pdf.stem}.md").write_text(md, encoding="utf-8")
        timings["files"][pdf.stem] = elapsed  # type: ignore[index]
        print(f"{name} {pdf.stem}: {elapsed:.1f} s, {len(md)} chars")
    timings["network_attempts"] = ATTEMPTS
    (out / "timings.json").write_text(json.dumps(timings, indent=2))
    print("network attempts:", ATTEMPTS)


if __name__ == "__main__":
    main()
