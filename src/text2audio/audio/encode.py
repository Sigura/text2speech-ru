"""Part WAV → MP3, whole book → M4B, with FFmpeg (SPEC §6.8): loudness, encoding, tags,
cover, chapters.

Loudness is two-pass `loudnorm` in linear mode (−19 LUFS integrated, true peak ≤ −3
dBTP): the first pass measures, the second applies one gain, so speech dynamics are
kept. FFmpeg is an external tool, resolved from the config, then `PATH`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

TARGET_LUFS = -19.0
TRUE_PEAK = -3.0
LRA = 11.0
MP3_BITRATE = "64k"
M4B_BITRATE = "64k"  # AAC-LC, mono (§6.8)
OUTPUT_RATE = 44100


class FfmpegError(Exception):
    pass


def find_ffmpeg(configured: str | None = None) -> str:
    found = configured or shutil.which("ffmpeg")
    if not found or not Path(found).is_file():
        raise FfmpegError(
            "ffmpeg not found; install it (sudo apt install ffmpeg) or set [tools] ffmpeg"
        )
    return found


@dataclass(frozen=True)
class Chapter:
    title: str
    start_ms: int
    end_ms: int


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FfmpegError(f"ffmpeg failed ({proc.returncode}): {proc.stderr[-1500:]}")
    return proc.stderr


def _feed(cmd: list[str], chunks: Iterable[Path]) -> None:
    """Run `cmd` with the chunks' bytes on its standard input, in order, deleting each
    chunk once it is sent; `chunks` may still be producing them."""
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        assert proc.stdin is not None
        try:
            for chunk in chunks:
                with chunk.open("rb") as f:
                    shutil.copyfileobj(f, proc.stdin, 1 << 20)
                chunk.unlink()
        except BrokenPipeError:
            pass  # FFmpeg exited early; its exit status and stderr below say why
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        with suppress(BrokenPipeError):
            proc.stdin.close()
        if proc.wait() != 0:
            err.seek(0)
            tail = err.read().decode("utf-8", "replace")[-1500:]
            raise FfmpegError(f"ffmpeg failed ({proc.returncode}): {tail}")


def measure(ffmpeg: str, wav: Path) -> dict[str, str]:
    """First loudnorm pass: the measured values for the second."""
    err = _run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(wav),
            "-af",
            f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA={LRA}:print_format=json",
            "-f",
            "null",
            "-",
        ]
    )
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.DOTALL)
    if match is None:
        raise FfmpegError(f"loudnorm printed no measurement: {err[-500:]}")
    data: dict[str, str] = json.loads(match[0])
    return data


def _loudnorm(m: dict[str, str]) -> str:
    if m["input_i"] in ("-inf", "inf"):  # pure silence: nothing to normalize
        return "anull"
    return (
        f"loudnorm=I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA={LRA}"
        f":measured_I={m['input_i']}:measured_TP={m['input_tp']}"
        f":measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}"
        f":offset={m['target_offset']}:linear=true"
    )


def _metadata_file(tags: dict[str, str], chapters: list[Chapter]) -> str:
    def esc(value: str) -> str:
        return re.sub(r"([=;#\\\n])", r"\\\1", value)

    lines = [";FFMETADATA1", *(f"{k}={esc(v)}" for k, v in tags.items())]
    for c in chapters:
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={c.start_ms}", f"END={c.end_ms}"]
        lines.append(f"title={esc(c.title)}")
    return "\n".join(lines) + "\n"


def encode_mp3(
    ffmpeg: str,
    wav: Path,
    out: Path,
    *,
    tags: dict[str, str],
    cover: Path | None = None,
    chapters: list[Chapter] | None = None,
    bitrate: str = MP3_BITRATE,
) -> None:
    """Normalize loudness and write `out` atomically."""
    codec = ["-c:a", "libmp3lame", "-b:a", bitrate, "-id3v2_version", "3", "-write_id3v1", "1"]
    _encode(ffmpeg, wav, out, tags, cover, chapters or [], [*codec, "-f", "mp3"])


def normalize(ffmpeg: str, wav: Path, out: Path) -> None:
    """Normalize loudness into raw 16-bit mono PCM at `OUTPUT_RATE`: a piece of the M4B."""
    filt = _loudnorm(measure(ffmpeg, wav))
    cmd = [ffmpeg, "-hide_banner", "-nostats", "-y", "-i", str(wav), "-af", filt]
    _run([*cmd, "-ar", str(OUTPUT_RATE), "-ac", "1", "-f", "s16le", str(out)])


def encode_m4b(
    ffmpeg: str,
    pieces: Iterable[Path],
    out: Path,
    *,
    tags: dict[str, str],
    cover: Path | None = None,
    chapters: list[Chapter] | None = None,
) -> None:
    """AAC-LC audiobook with chapter marks and cover (FR-09), written atomically, from
    the book's pieces in order (`normalize`); encoding starts with the first piece, and
    each piece is deleted once read."""
    # MP4 keeps only its standard tags (`use_metadata_tags` drops the cover in FFmpeg 4.3);
    # audiobook players read the narrator from the composer tag.
    tags = {("composer" if k == "narrator" else k): v for k, v in tags.items()}
    codec = ["-c:a", "aac", "-b:a", M4B_BITRATE, "-movflags", "+faststart", "-f", "ipod"]
    source = ["-f", "s16le", "-ar", str(OUTPUT_RATE), "-ac", "1", "-i", "pipe:0"]
    _mux(ffmpeg, source, [], out, tags, cover, chapters or [], codec, pieces)


def _encode(
    ffmpeg: str,
    wav: Path,
    out: Path,
    tags: dict[str, str],
    cover: Path | None,
    chapters: list[Chapter],
    codec: list[str],
) -> None:
    filt = _loudnorm(measure(ffmpeg, wav))
    _mux(ffmpeg, ["-i", str(wav)], ["-af", filt], out, tags, cover, chapters, codec)


def _mux(
    ffmpeg: str,
    source: list[str],
    filters: list[str],
    out: Path,
    tags: dict[str, str],
    cover: Path | None,
    chapters: list[Chapter],
    codec: list[str],
    feed: Iterable[Path] | None = None,
) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.partial{out.suffix}")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", encoding="utf-8", delete=False) as meta:
        meta.write(_metadata_file(tags, chapters))
    try:
        cmd = [ffmpeg, "-hide_banner", "-nostats", "-y", *source, "-i", meta.name]
        if cover is not None:
            cmd += ["-i", str(cover)]
        cmd += ["-map", "0:a", "-map_metadata", "1", "-map_chapters", "1"]
        if cover is not None:
            cmd += [
                "-map",
                "2:v",
                "-c:v",
                "copy",
                "-disposition:v",
                "attached_pic",
                "-metadata:s:v",
                "title=Album cover",
                "-metadata:s:v",
                "comment=Cover (front)",
            ]
        cmd += [*filters, "-ar", str(OUTPUT_RATE), "-ac", "1", *codec, str(tmp)]
        if feed is None:
            _run(cmd)
        else:
            _feed(cmd, feed)
        tmp.replace(out)
    finally:
        Path(meta.name).unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)


def write_m3u(path: Path, entries: list[tuple[str, int, str]]) -> None:
    """Extended M3U playlist: (file name, duration in ms, title) in reading order."""
    lines = ["#EXTM3U"]
    for name, ms, title in entries:
        lines += [f"#EXTINF:{round(ms / 1000)},{title}", name]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
