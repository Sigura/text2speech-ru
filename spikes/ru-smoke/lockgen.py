"""Spike: turn models/inventory.json into models.lock entries for the Russian assets.

    uv run python lockgen.py >> ../../models.lock
"""

from __future__ import annotations

import json
from pathlib import Path

inv = json.loads((Path(__file__).parent / "models" / "inventory.json").read_text())


def entry(comment: str, fields: dict[str, object], files: dict[str, str]) -> str:
    lines = [f"\n# {comment}", "[[asset]]"]
    for key, value in fields.items():
        lines.append(f"{key} = {json.dumps(value, ensure_ascii=False)}")
    lines.append("files = [" + ", ".join(json.dumps(f) for f in files) + "]")
    lines.append("[asset.sha256]")
    lines += [f"{json.dumps(f)} = {json.dumps(h)}" for f, h in files.items()]
    return "\n".join(lines)


hf = {(e["repo"], tuple(e["refs"])): e for e in inv["hf_cache"]}
piper = hf[("rhasspy/piper-voices", ("main",))]
gigaam = hf[("ai-sage/GigaAM-v3", ("rnnt",))]
silero = inv["silero"]
ruaccent = inv["ruaccent"]

out = [
    entry(
        "Silero TTS v5.6 Russian (female voices: xenia, baya, kseniya). torch.package, not on HF.",
        {"url": silero["url"], "license": "CC-BY-NC-4.0 (Silero, informational)",
         "backend": "silero", "langs": ["ru"], "tiers": ["fast"]},
        {Path(silero["url"]).name: silero["sha256"]},
    ),
    entry(
        "Piper ru_RU-irina-medium (female) — fast-tier baseline.",
        {"repo": piper["repo"], "revision": piper["revision"], "license": "MIT (voice card)",
         "backend": "piper", "langs": ["ru"], "tiers": ["fast"]},
        {f: h for f, h in piper["files"].items() if "irina" in f},
    ),
    entry(
        "RUAccent turbo3.1: stress + ё. koziev/* must sit in the ruaccent package dir (see FINDINGS).",
        {"repo": ruaccent["repo"], "revision": ruaccent["revision_at_fetch"], "license": "MIT",
         "component": "g2p:ruaccent", "langs": ["ru"]},
        ruaccent["files"],
    ),
    entry(
        "GigaAM-v3 RNN-T, plain spoken-form output (QA ASR). Remote code pinned by revision.",
        {"repo": gigaam["repo"], "revision": gigaam["revision"], "license": "MIT",
         "component": "asr:gigaam", "langs": ["ru"]},
        gigaam["files"],
    ),
]
print("\n".join(out))
