"""Score out/<pair>/<key>.flores.txt against the FLORES-200 devtest of the target
language: BLEU, chrF++, COMET-22.

MT_BENCH_PAIR=ru-en|en-ru HF_HOME=$MT_BENCH_HOME/hf python score.py   → bench-<pair>.md
"""

from __future__ import annotations

import json
import os
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import sacrebleu
from huggingface_hub import snapshot_download
from models import COMET, LANGS, MODELS, PAIR, SRC, TGT

HERE = Path(__file__).parent
HOME = Path(os.environ.get("MT_BENCH_HOME", HERE / "models"))
OUT = HERE / "out" / PAIR


def main() -> None:
    from comet import load_from_checkpoint

    devtest = HOME / "flores200_dataset/devtest"
    src = (devtest / f"{LANGS[SRC]['flores']}.devtest").read_text(encoding="utf-8").splitlines()
    ref = (devtest / f"{LANGS[TGT]['flores']}.devtest").read_text(encoding="utf-8").splitlines()
    comet = load_from_checkpoint(
        str(Path(snapshot_download(COMET[0], revision=COMET[1])) / "checkpoints/model.ckpt")
    )
    rows = []
    for m in MODELS:
        hyp_path = OUT / f"{m.key}.flores.txt"
        if not hyp_path.exists():
            continue
        hyp = hyp_path.read_text(encoding="utf-8").splitlines()
        bleu = sacrebleu.corpus_bleu(hyp, [ref]).score
        chrf = sacrebleu.corpus_chrf(hyp, [ref], word_order=2).score
        data = [{"src": s, "mt": h, "ref": r} for s, h, r in zip(src, hyp, ref, strict=True)]
        cm = comet.predict(data, batch_size=64, gpus=1, progress_bar=False).system_score
        speed = {}
        for name in ("flores", "lit"):
            p = OUT / f"{m.key}.{name}.json"
            if p.exists():
                speed[name] = json.loads(p.read_text())
        rows.append((m, bleu, chrf, cm, speed))
        print(m.key, round(bleu, 1), round(chrf, 1), round(cm, 4), flush=True)
    rows.sort(key=lambda r: -r[3])
    lines = [
        f"# {SRC}→{TGT} MT benchmark (FLORES-200 devtest, 1012 sentences)",
        "",
        "| Model | COMET-22 | chrF++ | BLEU | FLORES chars/s | lit chars/s | VRAM GB | Licence |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m, bleu, chrf, cm, sp in rows:
        f, lit = sp.get("flores", {}), sp.get("lit", {})
        lines.append(
            f"| {m.key} | {cm:.4f} | {chrf:.1f} | {bleu:.1f} | {f.get('chars_per_s', '-')} | "
            f"{lit.get('chars_per_s', '-')} | {max(f.get('vram_gb', 0), lit.get('vram_gb', 0))} | "
            f"{m.license} |"
        )
    (HERE / f"bench-{PAIR}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
