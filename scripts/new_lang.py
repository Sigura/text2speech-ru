#!/usr/bin/env python3
"""Generate a language pack from templates/lang (SPEC §6.6.4, docs/adding-a-language.md).

    python scripts/new_lang.py de "Deutsch" --script Latn [--sample "Guten Tag."]

Standard library only (`string.Template`): `${code}`, `${name}`, `${script}` and
`${sample}` are filled in file contents and paths, and the `.tmpl` suffix is dropped.
The pack is written to `plugins/lang-<code>` unless `--out` says otherwise; an existing
directory is never overwritten.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "lang"


def generate(code: str, name: str, script: str, out: Path, sample: str = "Text.") -> list[Path]:
    if not re.fullmatch(r"[a-z]{2,3}", code):
        raise ValueError(f"language code must be ISO 639 letters, not {code!r}")
    if not re.fullmatch(r"[A-Z][a-z]{3}", script):
        raise ValueError(f"script must be an ISO 15924 code such as Latn, not {script!r}")
    if out.exists():
        raise FileExistsError(f"{out} exists; not overwriting")
    values = {"code": code, "name": name, "script": script, "sample": sample}
    written = []
    for src in sorted(TEMPLATE.rglob("*.tmpl")):
        rel = Template(str(src.relative_to(TEMPLATE))).substitute(values).removesuffix(".tmpl")
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(Template(src.read_text("utf-8")).substitute(values), encoding="utf-8")
        written.append(dst)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("code", help="ISO 639-1 code, e.g. de")
    parser.add_argument("name", help="language name, e.g. Deutsch")
    parser.add_argument("--script", default="Latn", help="ISO 15924 script (default Latn)")
    parser.add_argument("--sample", default="Text.", help="first normalization test line")
    parser.add_argument("--out", type=Path, help="target directory (default plugins/lang-<code>)")
    args = parser.parse_args()
    out = args.out or ROOT / "plugins" / f"lang-{args.code}"
    try:
        files = generate(args.code, args.name, args.script, out, args.sample)
    except (ValueError, FileExistsError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in files:
        print(path)
    print(f"next: add text2audio-lang-{args.code} to the root pyproject.toml, then uv sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
