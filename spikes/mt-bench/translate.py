"""Translate one benchmark set with one candidate, offline, on one GPU.

    HF_HOME=$MT_BENCH_HOME/hf python translate.py <model key> flores|lit

flores: FLORES-200 devtest rus_Cyrl (1012 sentences). lit: 40 paragraphs of
*Капитанская дочка* from chapter I on; seq2seq models get them sentence by sentence,
LLMs whole. Writes out/<key>.<set>.txt (one line per item) and out/<key>.<set>.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import torch
from models import BY_KEY, OPUS_DIR, Model
from transformers import (
    AutoModelForCausalLM,
    AutoModelForSeq2SeqLM,
    AutoTokenizer,
)

HERE = Path(__file__).parent
HOME = Path(os.environ.get("MT_BENCH_HOME", HERE / "models"))
OUT = HERE / "out"
LIT = HERE.parents[1] / "tests/fixtures/ru/large/kapitanskaya-dochka.md"


def load_set(name: str) -> list[str]:
    if name == "flores":
        path = HOME / "flores200_dataset/devtest/rus_Cyrl.devtest"
        return path.read_text(encoding="utf-8").splitlines()
    blocks = LIT.read_text(encoding="utf-8").split("\n\n")
    paras = [b.strip() for b in blocks if b.strip() and not b.startswith(("#", ">", "[^", "<!--"))]
    start = next(i for i, p in enumerate(paras) if p.startswith("Отец мой"))
    return [" ".join(p.split()) for p in paras[start : start + 40]]


_SENT = re.compile(r"(?<=[.!?…»])\s+(?=[—«A-ZА-ЯЁ])")


def sentences(text: str) -> list[str]:
    return [s for s in _SENT.split(text) if s]


def prompt(m: Model, src: str) -> tuple[str, bool]:
    """(text, use the chat template)."""
    if m.kind == "towerplus":
        return (
            f"Translate the following Russian source text to English:\nRussian: {src}\nEnglish: ",
            True,
        )
    if m.kind == "seedx":
        return f"Translate the following Russian sentence into English:\n{src} <en>", False
    if m.kind == "qwen3":
        return (
            "Translate the following Russian text into English. Keep the meaning, tone and "
            f"paragraph as they are. Reply with the translation only.\n\n{src}",
            True,
        )
    raise ValueError(m.kind)


class Seq2Seq:
    def __init__(self, m: Model) -> None:
        self.m = m
        path = OPUS_DIR if m.kind == "marian" else m.repo
        kw = {"revision": m.revision} if m.kind != "marian" else {}
        if m.kind == "nllb":
            kw["src_lang"] = "rus_Cyrl"
        self.tok = AutoTokenizer.from_pretrained(path, **kw)
        dtype = torch.float32 if m.kind == "marian" else torch.bfloat16
        self.model = (
            AutoModelForSeq2SeqLM.from_pretrained(
                path, torch_dtype=dtype, **{k: v for k, v in kw.items() if k == "revision"}
            )
            .to("cuda")
            .eval()
        )
        self.batch = 32 if m.kind in ("marian", "nllb") else 16

    def run(self, texts: list[str]) -> list[str]:
        out: list[str] = []
        for i in range(0, len(texts), self.batch):
            chunk = texts[i : i + self.batch]
            if self.m.kind == "madlad":
                chunk = [f"<2en> {t}" for t in chunk]
            enc = self.tok(
                chunk, return_tensors="pt", padding=True, truncation=True, max_length=512
            ).to("cuda")
            kw: dict = {"num_beams": 4, "max_new_tokens": 512}
            if self.m.kind == "nllb":
                kw["forced_bos_token_id"] = self.tok.convert_tokens_to_ids("eng_Latn")
            with torch.inference_mode():
                gen = self.model.generate(**enc, **kw)
            out += self.tok.batch_decode(gen, skip_special_tokens=True)
        return [o.strip() for o in out]

    def translate(self, items: list[str], name: str) -> list[str]:
        if name == "flores":
            return self.run(items)
        flat = [(i, s) for i, p in enumerate(items) for s in sentences(p)]
        done = self.run([s for _, s in flat])
        joined = [[] for _ in items]
        for (i, _), t in zip(flat, done, strict=True):
            joined[i].append(t)
        return [" ".join(j) for j in joined]


class Llm:
    def __init__(self, m: Model) -> None:
        self.m = m
        self.tok = AutoTokenizer.from_pretrained(m.repo, revision=m.revision, padding_side="left")
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        self.model = (
            AutoModelForCausalLM.from_pretrained(
                m.repo, revision=m.revision, torch_dtype=torch.bfloat16
            )
            .to("cuda")
            .eval()
        )

    def _text(self, src: str) -> str:
        text, chat = prompt(self.m, src)
        if not chat:
            return text
        kw = {"enable_thinking": False} if self.m.kind == "qwen3" else {}
        return self.tok.apply_chat_template(
            [{"role": "user", "content": text}], tokenize=False, add_generation_prompt=True, **kw
        )

    def translate(self, items: list[str], name: str) -> list[str]:
        batch, cap = (16, 400) if name == "flores" else (4, 2048)
        order = sorted(range(len(items)), key=lambda i: len(items[i]))
        out: dict[int, str] = {}
        for k in range(0, len(order), batch):
            idx = order[k : k + batch]
            enc = self.tok(
                [self._text(items[i]) for i in idx],
                return_tensors="pt",
                padding=True,
                add_special_tokens=self.m.kind == "seedx",
            ).to("cuda")
            with torch.inference_mode():
                gen = self.model.generate(
                    **enc,
                    max_new_tokens=cap,
                    do_sample=False,
                    num_beams=1,
                    pad_token_id=self.tok.pad_token_id,
                )
            texts = self.tok.batch_decode(
                gen[:, enc["input_ids"].shape[1] :], skip_special_tokens=True
            )
            for i, t in zip(idx, texts, strict=True):
                out[i] = " ".join(t.strip().split())
        return [out[i] for i in range(len(items))]


def main() -> None:
    key, name = sys.argv[1], sys.argv[2]
    m = BY_KEY[key]
    items = load_set(name)
    t0 = time.perf_counter()
    engine = Llm(m) if m.llm else Seq2Seq(m)
    torch.cuda.synchronize()
    t1 = time.perf_counter()
    result = engine.translate(items, name)
    torch.cuda.synchronize()
    t2 = time.perf_counter()
    OUT.mkdir(exist_ok=True)
    (OUT / f"{key}.{name}.txt").write_text("\n".join(result) + "\n", encoding="utf-8")
    stats = {
        "model": key,
        "set": name,
        "items": len(items),
        "src_chars": sum(map(len, items)),
        "load_s": round(t1 - t0, 1),
        "translate_s": round(t2 - t1, 1),
        "chars_per_s": round(sum(map(len, items)) / (t2 - t1), 1),
        "vram_gb": round(torch.cuda.max_memory_allocated() / 2**30, 1),
        "gpu": torch.cuda.get_device_name(0),
    }
    (OUT / f"{key}.{name}.json").write_text(json.dumps(stats, indent=1) + "\n")
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
