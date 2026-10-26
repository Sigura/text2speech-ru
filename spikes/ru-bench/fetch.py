"""Spike, ONLINE step: download one challenger's weights, pinned to the current commit.

    uv run --project spikes/ru-bench/envs/<name> python spikes/ru-bench/fetch.py <name>

Weights go to models/hf (HF_HOME of the offline steps); repo@commit, files and SHA-256
go to models/inventory-<name>.json (draft models.lock entries if a challenger wins).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
MODELS = HERE / "models"
os.environ["HF_HOME"] = str(MODELS / "hf")

from huggingface_hub import HfApi, snapshot_download  # noqa: E402

REPOS: dict[str, dict[str, list[str] | None]] = {
    "qwen3": {"Qwen/Qwen3-TTS-12Hz-1.7B-Base": None},
    "espeech": {
        "ESpeech/ESpeech-TTS-1_SFT-256K": ["espeech_tts_256k.pt", "vocab.txt"],
        "charactr/vocos-mel-24khz": ["config.yaml", "pytorch_model.bin"],
    },
    "xtts": {
        "coqui/XTTS-v2": [
            "config.json",
            "model.pth",
            "vocab.json",
            "speakers_xtts.pth",
            "mel_stats.pth",
            "dvae.pth",
        ]
    },
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    name = sys.argv[1]
    entries = []
    for repo, patterns in REPOS[name].items():
        revision = HfApi().model_info(repo).sha
        root = Path(snapshot_download(repo, revision=revision, allow_patterns=patterns))
        files = {
            str(p.relative_to(root)): sha256(p) for p in sorted(root.rglob("*")) if p.is_file()
        }
        size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        entries.append({"repo": repo, "revision": revision, "dir": str(root), "files": files})
        print(f"{repo}@{revision[:12]}: {len(files)} files, {size / 2**30:.2f} GiB")
    (MODELS / f"inventory-{name}.json").write_text(json.dumps(entries, indent=2))


if __name__ == "__main__":
    main()
