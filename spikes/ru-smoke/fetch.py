"""Spike, ONLINE step: download every asset the Russian smoke test loads.

Each library is allowed to fetch what it wants once, into a spike-local cache
(models/hf); the script then lists exactly what arrived — repo, commit, files,
SHA-256 — as draft models.lock entries. That inventory is the point: it shows what
each library downloads "on first use" (ADR-0002) and what M2 must pin.

    uv run python fetch.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
MODELS = HERE / "models"
os.environ["HF_HOME"] = str(MODELS / "hf")
os.environ["TORCH_HOME"] = str(MODELS / "torch")

SILERO = "https://models.silero.ai/models/tts/ru/v5_6_ru.pt"
PIPER_REPO = "rhasspy/piper-voices"
PIPER_VOICE = "ru/ru_RU/irina/medium/ru_RU-irina-medium"  # female
GIGAAM_REPO = "ai-sage/GigaAM-v3"
GIGAAM_REVISION = "rnnt"  # plain spoken-form output; e2e_* branches write digits, ₽, %


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_silero() -> dict[str, str]:
    target = MODELS / "silero" / Path(SILERO).name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {SILERO}")
        urllib.request.urlretrieve(SILERO, target)
    return {"url": SILERO, "file": str(target.relative_to(HERE)), "sha256": sha256(target)}


def fetch_piper() -> None:
    from huggingface_hub import hf_hub_download

    for suffix in (".onnx", ".onnx.json"):
        hf_hub_download(PIPER_REPO, PIPER_VOICE + suffix)


RUACCENT_REPO = "ruaccent/accentuator"
RUACCENT_DIR = MODELS / "ruaccent"


def fetch_ruaccent() -> dict[str, object]:
    import ruaccent
    from huggingface_hub import HfApi
    from ruaccent import RUAccent

    # RUAccent downloads unpinned `main` files on first use: models and dictionaries
    # into `workdir`, but the koziev POS data always into its own package directory.
    revision = HfApi().model_info(RUACCENT_REPO).sha
    acc = RUAccent()
    acc.load(omograph_model_size="turbo3.1", use_dictionary=True, workdir=str(RUACCENT_DIR))
    print("ruaccent check:", acc.process_all("Все эти люди знали все о его прошлом."))
    pkg = Path(ruaccent.__file__).parent / "koziev"
    files = {
        str(p.relative_to(root)): sha256(p)
        for root in (RUACCENT_DIR, pkg.parent)
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".cache" not in p.parts and "__pycache__" not in p.parts
        and (root != pkg.parent or "koziev" in p.parts)
    }
    return {"repo": RUACCENT_REPO, "revision_at_fetch": revision, "files": files}


def fetch_gigaam() -> None:
    from transformers import AutoModel

    AutoModel.from_pretrained(GIGAAM_REPO, revision=GIGAAM_REVISION, trust_remote_code=True)


def inventory() -> list[dict[str, object]]:
    from huggingface_hub import scan_cache_dir

    entries = []
    for repo in scan_cache_dir(MODELS / "hf" / "hub").repos:
        for rev in repo.revisions:
            entries.append(
                {
                    "repo": repo.repo_id,
                    "revision": rev.commit_hash,
                    "refs": sorted(rev.refs),
                    "files": {
                        str(f.file_path.relative_to(rev.snapshot_path)): sha256(f.blob_path)
                        for f in rev.files
                    },
                    "size_mb": round(rev.size_on_disk / 2**20, 1),
                }
            )
    return entries


def main() -> None:
    steps = {"silero": fetch_silero, "piper": fetch_piper, "ruaccent": fetch_ruaccent,
             "gigaam": fetch_gigaam}
    only = set(sys.argv[1:]) or set(steps)
    results: dict[str, object] = {}
    for name, step in steps.items():
        if name in only:
            print(f"== {name}")
            results[name] = step()
    results["hf_cache"] = inventory()
    out = MODELS / "inventory.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"inventory written to {out}")
    for e in results["hf_cache"]:  # type: ignore[union-attr]
        print(f"  {e['repo']}@{str(e['revision'])[:12]} {e['refs']} "
              f"{len(e['files'])} files, {e['size_mb']} MB")


if __name__ == "__main__":
    main()
