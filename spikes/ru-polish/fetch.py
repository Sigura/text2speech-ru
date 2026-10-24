"""Spike, ONLINE step: download the variant B/D weights pinned to a commit.

    uv run --project chatterbox python fetch.py

Prints repo@commit, files and SHA-256 (draft models.lock entries) into
models/inventory.json. resemble-enhance is fetched from Hugging Face here instead of
its own `download()`, which git-clones unpinned `main` into the package directory.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

HERE = Path(__file__).parent
MODELS = HERE / "models"
os.environ["HF_HOME"] = str(MODELS / "hf")

from huggingface_hub import HfApi, snapshot_download  # noqa: E402

REPOS = {
    "ResembleAI/chatterbox": [
        "ve.pt",
        "t3_mtl23ls_v2.safetensors",
        "s3gen.pt",
        "grapheme_mtl_merged_expanded_v1.json",
        "conds.pt",
        "Cangjie5_TC.json",
    ],
    "ResembleAI/resemble-enhance": [
        "enhancer_stage2/hparams.yaml",
        "enhancer_stage2/ds/G/default/mp_rank_00_model_states.pt",
    ],
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    entries = []
    for repo, patterns in REPOS.items():
        revision = HfApi().model_info(repo).sha
        root = Path(snapshot_download(repo, revision=revision, allow_patterns=patterns))
        files = {
            str(p.relative_to(root)): sha256(p) for p in sorted(root.rglob("*")) if p.is_file()
        }
        entries.append({"repo": repo, "revision": revision, "files": files})
        print(f"{repo}@{revision[:12]}: {len(files)} files")
    (MODELS / "inventory.json").write_text(json.dumps(entries, indent=2))


if __name__ == "__main__":
    main()
