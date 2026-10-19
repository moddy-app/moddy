"""Audio / small-video conversion for /convert, with the ffmpeg binary that
``imageio-ffmpeg`` ships inside its wheel (no system package, 0 RAM when idle).

Safety:

* input and output live in a private temp directory, never on the command line
  as user text; the input demuxer is **forced** from the sniffed format
  (``-f mov``…) and ``-protocol_whitelist file`` is set, so a crafted playlist
  cannot make ffmpeg open other files or the network;
* the duration is probed first and refused above the caps of ``formats``;
  ``-t`` is also passed as a safety net for streams with no duration;
* one conversion at a time in the whole process, a hard timeout, a capped
  thread count and a capped output size.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from ..media_errors import MediaError
from . import formats as F
from .local import ConvertOutput

logger = logging.getLogger("moddy.convert.ffmpeg")

MAX_OUTPUT_BYTES = 64 * 1024 * 1024

#: format key -> ffmpeg demuxer name used with ``-f`` before ``-i``.
INPUT_DEMUXER = {
    "mp3": "mp3", "ogg": "ogg", "opus": "ogg", "wav": "wav", "flac": "flac", "aac": "aac",
    "m4a": "mov", "mp4": "mov", "mov": "mov", "webm": "matroska", "mkv": "matroska", "gif": "gif",
}

#: Encoders per target (``-vn`` / stream maps are added by :func:`build_command`).
_AUDIO_ARGS = {
    "mp3": ["-c:a", "libmp3lame", "-q:a", "2"],
    "ogg": ["-c:a", "libvorbis", "-q:a", "5"],
    "opus": ["-c:a", "libopus", "-b:a", "96k"],
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
    "m4a": ["-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart"],
    "aac": ["-c:a", "aac", "-b:a", "160k", "-f", "adts"],
}
_SCALE = "scale=w='min(1280,iw)':h=-2"
_GIF_FILTER = ("[0:v:0]fps=12,scale=w='min(480,iw)':h=-1:flags=lanczos,split[a][b];"
               "[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4[out]")


def ffmpeg_path() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001
        raise MediaError("ffmpeg_unavailable") from exc


def available() -> bool:
    try:
        ffmpeg_path()
        return True
    except MediaError:
        return False


def max_seconds(src: str, dst: str) -> int:
    """Duration cap of a conversion."""
    if dst == "gif":
        return F.MAX_GIF_SECONDS
    if src in F.VIDEO_KEYS or src == "gif":
        return F.MAX_VIDEO_SECONDS if dst in F.VIDEO_KEYS or dst == "gif" else F.MAX_AUDIO_SECONDS
    return F.MAX_AUDIO_SECONDS


def build_command(exe: str, src: str, dst: str, in_path: Path, out_path: Path,
                  seconds_cap: int) -> List[str]:
    """The ffmpeg argv for ``src -> dst`` (pure: unit-tested without running it)."""
    cmd = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
           "-protocol_whitelist", "file", "-threads", str(F.FFMPEG_THREADS),
           "-f", INPUT_DEMUXER[src], "-i", str(in_path), "-t", str(seconds_cap)]
    to_audio = dst in F.AUDIO_KEYS
    if to_audio:
        cmd += ["-vn", "-map", "0:a:0"] + _AUDIO_ARGS[dst]
    elif dst == "mp4":
        cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-vf", _SCALE + ",format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "27",
                "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart"]
    elif dst == "webm":
        cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-vf", _SCALE,
                "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "34", "-deadline", "good",
                "-cpu-used", "5", "-row-mt", "1", "-c:a", "libopus", "-b:a", "96k"]
    elif dst == "gif":
        cmd += ["-filter_complex", _GIF_FILTER, "-map", "[out]", "-an", "-loop", "0"]
    else:
        raise MediaError("unsupported")
    cmd += ["-fs", str(MAX_OUTPUT_BYTES), str(out_path)]
    return cmd


_DURATION = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def parse_duration(stderr: str) -> Optional[float]:
    match = _DURATION.search(stderr or "")
    if not match:
        return None
    h, m, s = match.groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


async def _run(argv: List[str], timeout: float):
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise MediaError("timeout") from None
    return proc.returncode, (err or b"").decode("utf-8", "replace")


# One conversion at a time: a video encode saturates a Railway vCPU.
_ONE_AT_A_TIME = asyncio.Semaphore(1)


async def convert_media(src: str, dst: str, raw: bytes, filename: str) -> ConvertOutput:
    """Convert ``raw`` (format ``src``) to ``dst``. Raises :class:`MediaError`."""
    if src not in INPUT_DEMUXER:
        raise MediaError("unsupported")
    exe = ffmpeg_path()
    cap = max_seconds(src, dst)
    out_name = F.output_name(filename, dst)
    async with _ONE_AT_A_TIME:
        tmp = Path(tempfile.mkdtemp(prefix="moddy-ffmpeg-"))
        try:
            in_path = tmp / f"input.{F.FORMATS[src].ext}"
            out_path = tmp / f"output.{F.FORMATS[dst].ext}"
            await asyncio.to_thread(in_path.write_bytes, raw)

            probe = [exe, "-nostdin", "-hide_banner", "-protocol_whitelist", "file",
                     "-f", INPUT_DEMUXER[src], "-i", str(in_path)]
            _, info = await _run(probe, 20)
            duration = parse_duration(info)
            if duration is None and "Invalid data" in info:
                raise MediaError("unreadable")
            if duration is not None and duration > cap + 0.5:
                raise MediaError("too_long", max=cap if cap < 120 else f"{cap // 60} min")

            code, err = await _run(build_command(exe, src, dst, in_path, out_path, cap),
                                   F.FFMPEG_TIMEOUT)
            if code != 0 or not out_path.exists() or out_path.stat().st_size == 0:
                logger.info("ffmpeg %s->%s failed: %s", src, dst, err[-300:])
                lowered = err.lower()
                if "does not contain any stream" in lowered or "matches no streams" in lowered:
                    raise MediaError("no_stream")
                raise MediaError("convert_failed")
            data = await asyncio.to_thread(out_path.read_bytes)
            notes = []
            if out_path.stat().st_size >= MAX_OUTPUT_BYTES:
                raise MediaError("convert_failed")
            return ConvertOutput(out_name, data, dst, notes)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
