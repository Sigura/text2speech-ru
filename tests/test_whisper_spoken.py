"""Written forms in Whisper transcripts turned into what was said (§6.9)."""

from __future__ import annotations

import pytest
from text2audio_asr_whisper.spoken import spoken


@pytest.mark.parametrize(
    ("heard", "said"),
    [
        ("Mr. Smith met Mrs. Bennet.", "Mister Smith met Missus Bennet."),
        ("Dr. Watson and St. Paul.", "Doctor Watson and Saint Paul."),
        ("Henry VIII had six wives.", "Henry the Eighth had six wives."),
        ("Louis XIV built it.", "Louis the Fourteenth built it."),
        ("Pope John Paul II spoke.", "Pope John Paul the Second spoke."),
        ("George III went mad.", "George the Third went mad."),
        ("Chapter IV. The Storm", "Chapter Four. The Storm"),
        ("CHAPTER XXI", "CHAPTER Twenty-One"),
        ("World War II ended.", "World War Two ended."),
        ("Then I left.", "Then I left."),
        ("Malcolm X spoke.", "Malcolm X spoke."),
        ("He has a CD and a DVD.", "He has a CD and a DVD."),
        ("Henry I ruled.", "Henry I ruled."),
    ],
)
def test_spoken(heard: str, said: str) -> None:
    assert spoken(heard) == said
