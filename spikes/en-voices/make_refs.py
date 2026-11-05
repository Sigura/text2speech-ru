"""Reference clips for Chatterbox `en`, read by the Kokoro voices of the `en` profile.

    T2A_MODEL_HOME=… T2A_ENV_HOME=… uv run --python 3.11 python spikes/en-voices/make_refs.py

Chatterbox clones a voice from a ~15 s clip; reading it with the fast tier's voice keeps
both tiers sounding like the same narrator (the `ru` pack does the same with `xenia`).
"""

from __future__ import annotations

import sys
from pathlib import Path

from text2audio_backend_kokoro.descriptor import KokoroBackend
from text2audio_lang_en import PROFILE

from text2audio.core.contracts import Segment
from text2audio.models.lock import assets_for
from text2audio.models.store import ModelStore
from text2audio.workers.backend import Worker

TEXT = (
    "Alice was beginning to get very tired of sitting by her sister on the bank, and of "
    "having nothing to do. Once or twice she had peeped into the book her sister was "
    "reading, but it had no pictures or conversations in it."
)
OUT = Path(PROFILE.lexicon or "").parent / "voices"


def main() -> int:
    store = ModelStore()
    OUT.mkdir(exist_ok=True)
    seg = Segment(id="ref", text=TEXT, text_norm=TEXT, lang="en")
    with Worker(KokoroBackend(), store.root) as w:
        w.load(assets=store.payload(assets_for("kokoro")))
        for role in ("narrator", "dialogue"):
            voice = PROFILE.voices[role]["kokoro"]
            out = OUT / f"ref-{voice}.wav"
            reply = w.call("synthesize", segment=seg.model_dump(), out=str(out), voice_id=voice)
            print(f"{out}: {reply['duration_ms'] / 1000:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
