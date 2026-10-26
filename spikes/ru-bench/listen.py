"""Spike, OFFLINE step: the blind listening page (SPEC §6.6.5 protocol, step 3).

    uv run --python 3.11 python spikes/ru-bench/listen.py plan     # choose clips + synth set
    uv run --python 3.11 python spikes/ru-bench/listen.py          # build out/listen/
    uv run --python 3.11 python spikes/ru-bench/listen.py results.json   # unblind

Part 1 — 40 blind A/B pairs: the current T1 default (hybrid) against each other system
on 10 clips each (homographs, ё, numbers, dialogue, FR-24, poem stanzas), sides
shuffled. Part 2 — stress: 20 homograph sentences (both readings of 10 words) from all
five systems, shuffled; the listener marks each clip's stress right or wrong.
Clips are loudness-normalized MP3s (no level cue). The page keeps answers in the
browser and exports them as JSON; the key stays in out/listen-key.json.
"""

from __future__ import annotations

import html
import json
import random
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from text2audio.audio.encode import find_ffmpeg

HERE = Path(__file__).parent
OUT = HERE / "out"
PAGE = OUT / "listen"
SEED = 1234
BASE = "hybrid"
SYNTHESIZED = ("qwen3", "espeech", "xtts")
CHALLENGERS = (*SYNTHESIZED, "silero")
SYNTH_MAX = 100  # segments per challenger (SPEC §6.6.5, step 1)
PAIR_CATS = (
    "homograph",
    "yo",
    "number",
    "dialogue",
    "latin",
    "homograph",
    "yo",
    "dialogue",
    "poem",
    "latin",
)
STRESS_WORDS = 10


def units(segs: list[dict[str, Any]]) -> dict[str, list[list[str]]]:
    """Clips per category: whole items, except the poem, which is one clip per stanza."""
    by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in segs:
        by_item[s["item"]].append(s)
    out: dict[str, list[list[str]]] = defaultdict(list)
    for parts in by_item.values():
        if parts[0]["category"] == "poem":
            out["poem"] += [[p["id"]] for p in parts]
        else:
            out[parts[0]["category"]].append([p["id"] for p in parts])
    return out


def render(system: str, ids: list[str], dest: Path, ffmpeg: str) -> None:
    chunks, rate = [], 24000
    for seg_id in ids:
        audio, rate = sf.read(str(OUT / "wav" / system / f"{seg_id}.wav"), dtype="float32")
        chunks += [audio if audio.ndim == 1 else audio.mean(axis=1), np.zeros(int(0.35 * rate))]
    with tempfile.NamedTemporaryFile(suffix=".wav") as tmp:
        sf.write(tmp.name, np.concatenate(chunks).astype(np.float32), rate)
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-y",
                "-i",
                tmp.name,
                "-af",
                "loudnorm=I=-20:TP=-2",
                "-ar",
                "44100",
                "-ac",
                "1",
                "-b:a",
                "128k",
                str(dest),
            ],
            check=True,
        )


def expected_readings() -> dict[str, str]:
    expected = {}
    for line in (HERE / "set.tsv").read_text("utf-8").splitlines()[1:]:
        item, _, _, exp = line.split("\t")
        expected[item] = exp
    return expected


def plan() -> None:
    """Choose the clips before the challengers run, so their segments are synthesized.

    out/listen-key.json gets the pairs, the stress clips and `synth`: the clips'
    segments, padded with segments spread over the set up to SYNTH_MAX.
    """
    segs = json.loads((OUT / "segments.json").read_text("utf-8"))
    expected = expected_readings()
    rng = random.Random(SEED)
    pool = units(segs)
    for clips in pool.values():
        rng.shuffle(clips)
    pairs: list[dict[str, Any]] = []
    for other in CHALLENGERS:
        for cat in PAIR_CATS:
            sides = [BASE, other]
            rng.shuffle(sides)
            pairs.append(
                {
                    "n": len(pairs) + 1,
                    "a": sides[0],
                    "b": sides[1],
                    "segments": pool[cat].pop(),
                    "category": cat,
                }
            )

    by_word: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in segs:
        if s["category"] == "homograph":
            by_word[expected[s["item"]].replace("+", "").casefold()].append(s)
    two_way = sorted(w for w, ss in by_word.items() if len({expected[s["item"]] for s in ss}) == 2)
    stress = [
        {"seg": s["id"], "system": system}
        for word in rng.sample(two_way, STRESS_WORDS)
        for s in by_word[word][:2]
        for system in (BASE, *CHALLENGERS)
    ]
    rng.shuffle(stress)
    stress = [{"n": n, **c} for n, c in enumerate(stress, 1)]

    needed = {i for p in pairs if {p["a"], p["b"]} & set(SYNTHESIZED) for i in p["segments"]}
    needed |= {c["seg"] for c in stress}
    room = SYNTH_MAX - len(needed)
    if room < 0:
        raise SystemExit(f"the clips need {len(needed)} segments, more than {SYNTH_MAX}")
    rest = [s["id"] for s in segs if s["id"] not in needed]
    needed |= {rest[i * len(rest) // room] for i in range(min(room, len(rest)))}
    synth = [s["id"] for s in segs if s["id"] in needed]
    key = {"pairs": pairs, "stress": stress, "synth": synth}
    (OUT / "listen-key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1), "utf-8")
    print(f"{len(pairs)} pairs, {len(stress)} stress clips, {len(synth)} segments to synthesize")


def build() -> None:
    segs = json.loads((OUT / "segments.json").read_text("utf-8"))
    text = {s["id"]: s["text"] for s in segs}
    item_of = {s["id"]: s["item"] for s in segs}
    expected = expected_readings()
    key = json.loads((OUT / "listen-key.json").read_text("utf-8"))
    ffmpeg = find_ffmpeg()
    (PAGE / "clips").mkdir(parents=True, exist_ok=True)
    pairs = []
    for p in key["pairs"]:
        for side in "ab":
            render(p[side], p["segments"], PAGE / "clips" / f"p{p['n']:02d}{side}.mp3", ffmpeg)
        pairs.append({"n": p["n"], "text": " / ".join(text[i] for i in p["segments"])})
    random.Random(SEED).shuffle(pairs)
    clips = []
    for c in key["stress"]:
        render(c["system"], [c["seg"]], PAGE / "clips" / f"s{c['n']:03d}.mp3", ffmpeg)
        clips.append({"n": c["n"], "text": text[c["seg"]], "target": expected[item_of[c["seg"]]]})
    (PAGE / "index.html").write_text(page(pairs, clips), "utf-8")
    print(f"{len(pairs)} pairs, {len(clips)} stress clips → {PAGE / 'index.html'}")


def page(pairs: list[dict[str, Any]], clips: list[dict[str, Any]]) -> str:
    rows = []
    for i, p in enumerate(pairs, 1):
        n = p["n"]
        rows.append(f"""
<section class="pair" data-n="{n}">
  <h3>{i}. {html.escape(p["text"])}</h3>
  <div class="players">
    <label>A <audio controls preload="none" src="clips/p{n:02d}a.mp3"></audio></label>
    <label>B <audio controls preload="none" src="clips/p{n:02d}b.mp3"></audio></label>
  </div>
  <div class="choice">
    {
            "".join(
                f'<label><input type="radio" name="p{n}" value="{v}"> {t}</label>'
                for v, t in (("A", "A лучше"), ("=", "одинаково"), ("B", "B лучше"))
            )
        }
  </div>
  <div class="flags">
    <label><input type="checkbox" name="p{n}-a-stress"> A: ударение</label>
    <label><input type="checkbox" name="p{n}-a-words"> A: слова/артефакты</label>
    <label><input type="checkbox" name="p{n}-b-stress"> B: ударение</label>
    <label><input type="checkbox" name="p{n}-b-words"> B: слова/артефакты</label>
  </div>
</section>""")
    stress_rows = []
    for c in clips:
        n, target = c["n"], html.escape(c["target"])
        stress_rows.append(f"""
<section class="clip" data-n="{n}">
  <p><b>{n}.</b> {html.escape(c["text"])} <span class="target">→ {target}</span></p>
  <audio controls preload="none" src="clips/s{n:03d}.mp3"></audio>
  <label><input type="radio" name="s{n}" value="ok"> верно</label>
  <label><input type="radio" name="s{n}" value="wrong"> неверно</label>
</section>""")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ru-bench listening</title>
<style>
:root {{ --bg: #fbfaf7; --fg: #1d1c1a; --muted: #6b665e; --line: #e2ddd3; --accent: #2f5d8a; }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg: #1b1a18; --fg: #ece8e1; --muted: #a39d93; --line: #34312c; --accent: #8fb5dc; }}
}}
body {{ background: var(--bg); color: var(--fg); font: 16px/1.5 system-ui, sans-serif;
       margin: 0 auto; max-width: 860px; padding: 16px; }}
h1 {{ font-size: 1.4rem; }} h2 {{ margin-top: 2.5rem; }}
h3 {{ font-size: 1rem; font-weight: 500; }}
section {{ border-top: 1px solid var(--line); padding: 12px 0; }}
.players {{ display: flex; flex-wrap: wrap; gap: 12px; }}
.players label {{ display: flex; align-items: center; gap: 6px; font-weight: 600; }}
audio {{ max-width: 100%; }}
.choice, .flags {{ display: flex; flex-wrap: wrap; gap: 14px; margin-top: 6px; }}
.flags {{ color: var(--muted); font-size: .9rem; }}
.target {{ color: var(--accent); white-space: nowrap; }}
.clip label {{ margin-right: 14px; }}
button {{ font: inherit; padding: 8px 14px; margin: 4px 8px 4px 0; }}
#bar {{ position: sticky; bottom: 0; background: var(--bg); border-top: 1px solid var(--line);
        padding: 8px 0; }}
</style></head><body>
<h1>ru-bench: слепое прослушивание</h1>
<p>Часть 1 — пары A/B: какой вариант лучше звучит как аудиокнига (естественность,
ударения, ошибки). Флажки — если заметны ошибки ударения или слова/артефакты.
Часть 2 — ударение: верно ли произнесено слово, указанное синим.
Ответы сохраняются в браузере; в конце нажмите «Скачать».</p>
<h2>Часть 1 — пары ({len(pairs)})</h2>
{"".join(rows)}
<h2>Часть 2 — ударение ({len(clips)})</h2>
{"".join(stress_rows)}
<div id="bar"><span id="count"></span>
<button id="save">Скачать результаты</button><button id="copy">Скопировать</button></div>
<script>
const KEY = "ru-bench-listening";
const inputs = () => [...document.querySelectorAll("input")];
function collect() {{
  const out = {{}};
  for (const el of inputs()) {{
    if (el.type === "radio" && el.checked) out[el.name] = el.value;
    if (el.type === "checkbox" && el.checked) out[el.name] = true;
  }}
  return out;
}}
function update() {{
  const answers = collect();
  try {{ localStorage.setItem(KEY, JSON.stringify(answers)); }} catch (e) {{}}
  const total = document.querySelectorAll("section").length;
  const done = Object.keys(answers).filter(k => !k.includes("-")).length;
  document.getElementById("count").textContent = `отвечено ${{done}} из ${{total}} `;
}}
let saved = {{}};
try {{ saved = JSON.parse(localStorage.getItem(KEY) || "{{}}"); }} catch (e) {{}}
for (const el of inputs()) {{
  if (el.type === "radio" && saved[el.name] === el.value) el.checked = true;
  if (el.type === "checkbox" && saved[el.name]) el.checked = true;
  el.addEventListener("change", update);
}}
document.getElementById("save").onclick = () => {{
  const blob = new Blob([JSON.stringify(collect(), null, 1)], {{type: "application/json"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "ru-bench-results.json"; a.click();
}};
document.getElementById("copy").onclick = () =>
  navigator.clipboard.writeText(JSON.stringify(collect()));
const players = document.querySelectorAll("audio");
for (const a of players)
  a.addEventListener("play", () => players.forEach(o => o !== a && o.pause()));
update();
</script></body></html>
"""


def unblind(results_path: Path) -> None:
    answers = json.loads(results_path.read_text("utf-8"))
    key = json.loads((OUT / "listen-key.json").read_text("utf-8"))
    prefs: dict[str, dict[str, int]] = defaultdict(lambda: {"base": 0, "same": 0, "other": 0})
    flags: dict[str, dict[str, int]] = defaultdict(lambda: {"stress": 0, "words": 0, "clips": 0})
    for p in key["pairs"]:
        other = p["b"] if p["a"] == BASE else p["a"]
        vote = answers.get(f"p{p['n']}")
        if not vote:
            continue
        winner = {"A": p["a"], "B": p["b"], "=": None}[vote]
        prefs[other]["same" if winner is None else "base" if winner == BASE else "other"] += 1
        for side in "ab":
            system = p[side]
            flags[system]["clips"] += 1
            for kind in ("stress", "words"):
                flags[system][kind] += bool(answers.get(f"p{p['n']}-{side}-{kind}"))
    stress: dict[str, dict[str, int]] = defaultdict(lambda: {"ok": 0, "wrong": 0})
    for c in key["stress"]:
        vote = answers.get(f"s{c['n']}")
        if vote:
            stress[c["system"]][vote] += 1
    result = {"preference_vs_hybrid": prefs, "flags": flags, "stress": stress}
    (OUT / "listening.json").write_text(json.dumps(result, indent=1), "utf-8")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    if sys.argv[1:] == ["plan"]:
        plan()
    elif len(sys.argv) > 1:
        unblind(Path(sys.argv[1]))
    else:
        build()
