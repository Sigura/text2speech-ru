"""Ensemble by MBR selection on FLORES: per sentence, keep the candidate that agrees most
with the other candidates, then score the selection against the references.

    MT_BENCH_PAIR=ru-en|en-ru HF_HOME=$MT_BENCH_HOME/hf python mbr.py [key ...]

Utility chrF++ (cheap, CPU) and COMET-22 with the other candidates as pseudo-references
(GPU). Prints COMET-22 / chrF++ against the real references for each selection and for
every single candidate.
"""

from __future__ import annotations

import itertools
import os
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import sacrebleu
from huggingface_hub import snapshot_download
from models import COMET, LANGS, PAIR, SRC, TGT

HERE = Path(__file__).parent
HOME = Path(os.environ.get("MT_BENCH_HOME", HERE / "models"))
OUT = HERE / "out" / PAIR
DEFAULT = ["towerplus-9b", "seedx-7b", "nllb-3.3b", "madlad-3b", "qwen3-8b"]


def main() -> None:
    from comet import load_from_checkpoint

    keys = sys.argv[1:] or DEFAULT
    devtest = HOME / "flores200_dataset/devtest"
    src = (devtest / f"{LANGS[SRC]['flores']}.devtest").read_text(encoding="utf-8").splitlines()
    ref = (devtest / f"{LANGS[TGT]['flores']}.devtest").read_text(encoding="utf-8").splitlines()
    hyps = {k: (OUT / f"{k}.flores.txt").read_text(encoding="utf-8").splitlines() for k in keys}
    comet = load_from_checkpoint(
        str(Path(snapshot_download(COMET[0], revision=COMET[1])) / "checkpoints/model.ckpt")
    )

    def comet_scores(pairs: list[tuple[str, str, str]]) -> list[float]:
        data = [{"src": s, "mt": h, "ref": r} for s, h, r in pairs]
        return list(comet.predict(data, batch_size=128, gpus=1, progress_bar=False).scores)

    n = len(src)
    # Utility of candidate a against pseudo-reference b, for every ordered pair.
    order = list(itertools.permutations(keys, 2))
    flat = comet_scores([(src[i], hyps[a][i], hyps[b][i]) for a, b in order for i in range(n)])
    util_comet = {(a, b): flat[j * n : (j + 1) * n] for j, (a, b) in enumerate(order)}
    picks: dict[str, list[str]] = {}
    for name in ("chrf", "comet"):
        chosen = []
        for i in range(n):

            def score(a: str) -> float:
                if name == "comet":
                    return sum(util_comet[(a, b)][i] for b in keys if b != a)
                return sum(
                    sacrebleu.sentence_chrf(hyps[a][i], [hyps[b][i]], word_order=2).score
                    for b in keys
                    if b != a
                )

            chosen.append(hyps[max(keys, key=score)][i])
        picks[f"mbr-{name}"] = chosen
    rows = []
    for label, hyp in [*hyps.items(), *picks.items()]:
        cm = sum(comet_scores(list(zip(src, hyp, ref, strict=True)))) / n
        chrf = sacrebleu.corpus_chrf(hyp, [ref], word_order=2).score
        rows.append((cm, chrf, label))
    for cm, chrf, label in sorted(rows, reverse=True):
        print(f"{label:14s} COMET {cm:.4f}  chrF++ {chrf:.1f}")


if __name__ == "__main__":
    main()
