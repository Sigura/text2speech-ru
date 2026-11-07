# 0012. Re-voicing recordings: timed phrases, translated in context, placed at their time

- **Status:** Accepted
- **Date:** 2026-11-07
- **Spec references:** §6.11 (`revoice`), §6.6.1, §6.8, §6.10, §13.1, ADR-0011

## Context

The owner wants English speech re-voiced in Russian with the female voice, "all steps
in one command". The output matches the input: Markdown gives the translated Markdown
and its audio, audio gives Russian audio, and video gives the same video with its audio
track replaced, timed to the original. The pieces already exist:
- the source pack's QA ASR (faster-whisper for English);
- the book translation pipeline (ADR-0011);
- the target pack's renderer, with normalization, stress and the segment cache;
- FFmpeg.

What is missing is the timing. Russian runs 10–20 % longer than English, and a phrase
has to stay with the picture it belongs to.

## Decision

1. **One command, `revoice`.**
   - Books and Markdown chain `translate` and `run`.
   - Audio and video go through `core/revoice.py`, with a book folder `<name>.<lang>/`
     next to the output as the work area. Its `_work/` caches:
     - the transcript, keyed by the input's SHA-256;
     - the translation (`translate-<lang>.jsonl`);
     - the voiced segments.
2. **Transcription.** A `timed` op on the Whisper worker returns words with times,
   using large-v3-turbo on a GPU.
   - **No VAD:** with Silero VAD, Whisper dropped a whole sentence after a 2 s pause.
   - **A punctuated prompt:** without it, the words come back lowercase with no stops.
   - **`hallucination_silence_threshold=2`** skips hallucinations over long silences.
3. **Phrases.** A phrase ends:
   - at a sentence end (not at the pack's abbreviations);
   - after a 0.8 s pause;
   - at a comma after 8 s;
   - at any word after 20 s.

   A 1.5 s pause starts a new paragraph.
4. **Translation in context, timing kept.**
   - Each paragraph is translated whole, with every ADR-0011 check, and its translated
     sentences go back to its phrases when the counts match.
   - Otherwise that paragraph is translated phrase by phrase.
5. **Placement.**
   - **Video:** a phrase starts at its original time, or right after the previous
     one if that ran late. A clip longer than the time to the next phrase is sped up
     (FFmpeg `atempo`, pitch kept), at most **1.3×**; what is still left runs into
     the pause and is caught up there. A phrase more than 1 s behind is reported.
   - **Audio:** a phrase follows the original pause before it, unhurried.
6. **Output.**
   - The voiced track is loudness-normalized like the audiobook (§6.8).
   - It is written in the input's format: mp3, m4a, ogg, opus, flac or wav.
   - Video keeps every picture stream copied; the new track replaces every audio
     track (the owner's choice: replace, not mix).
   - A Russian `.srt` is written next to the video and muxed into mp4, mov, mkv and
     webm.

## Consequences

- No new model and no new dependency. Whisper's prompt and word times come from the
  pinned faster-whisper env. Nothing is downloaded, so FR-04 holds.
- Background music and effects in the original audio are lost. Mixing in the original
  under the voice ("ducking") would need source separation to drop the English; that
  is left for later.
- Every speaker gets one voice: the target pack's narrator.
- Long recordings have to be transcribed in one request, so the request timeout for
  `timed` is 4 h.
