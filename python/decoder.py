"""
Turn whatever a sender gives us into samples the engine can read.

Every network input the box has accepts a stream from something else's encoder, and what
comes out of those encoders is not our choice: Snapcast serves flac, ogg or opus, a
Squeezebox server sends flac, mp3, aac or wav, and a phone pushing over DLNA sends
whatever it happens to have. Asking the person who installed this to go and change a
codec setting is not a fix, it is a footnote, so we decode.

One decoder for all of it - ffmpeg, fed through a pipe - rather than a Python library per
format. Five libraries means five wheels per architecture and five sets of edge cases in
files written by other people's encoders; ffmpeg is what every media player already trusts
for exactly this.

    encoded bytes ──stdin──► ffmpeg ──stdout──► s16le 48 kHz stereo ──► on_chunk

The fixed output format is deliberate. The feature extractor wants int16, and pinning the
rate stops it being torn down and rebuilt whenever a source changes sample rate.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from pathlib import Path
from typing import Awaitable, Callable

from wav_feed import NotWav, feed_wav

# What we ask ffmpeg for. Matches what the extractor is built around.
OUT_RATE = 48000
OUT_CHANNELS = 2
_FRAME = OUT_CHANNELS * 2
# 20 ms of audio, the size the other inputs deliver.
_BLOCK = (OUT_RATE // 50) * _FRAME
# Feeding ffmpeg faster than it drains is how a pipe deadlocks; this is a ceiling on how
# much we will hold while waiting for it.
_FEED_CHUNK = 64 * 1024

# Where our own build lives inside the package, relative to this file.
_BUNDLED = ("../runtime/ffmpeg-{arch}/ffmpeg", "../../runtime/ffmpeg-{arch}/ffmpeg")
# Last resorts. The NAS has one of these, but it is from 2017 and has no AAC decoder at
# all, so it is a fallback and never the first choice.
_SYSTEM = ("/usr/bin/ffmpeg", "/usr/local/medialibrary/bin/ffmpeg")


class DecodeError(Exception):
    """The stream could not be decoded, with a reason worth showing someone."""


def find_ffmpeg() -> str | None:
    """
    Locate a usable ffmpeg: ours first, the system's only if we have none.

    Returns the path, or None when there is nothing to decode with - in which case the
    caller keeps to the uncompressed path rather than failing.
    """
    arch = os.uname().machine
    here = Path(__file__).resolve().parent
    for pattern in _BUNDLED:
        candidate = (here / pattern.format(arch=arch)).resolve()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    for path in _SYSTEM:
        if Path(path).is_file() and os.access(path, os.X_OK):
            return path
    found = shutil.which("ffmpeg")
    return found


async def decode_stream(
    read: Callable[[int], Awaitable[bytes]],
    on_chunk: Callable[[bytes, int, int], None],
    *,
    ffmpeg: str,
    hint: str = "",
    prefix: bytes = b"",
    on_start: Callable[[], None] | None = None,
) -> None:
    """
    Decode an encoded stream until it ends.

    :param read: ``await read(n)`` returning up to n bytes, b"" at the end.
    :param prefix: bytes already taken off the front - a caller that sniffed the format
        hands them back rather than losing them.
    :param on_chunk: ``(pcm, sample_rate, channels)``, always 48 kHz stereo s16le.
    :param ffmpeg: path from :func:`find_ffmpeg`.
    :param hint: the format name for the log, when the caller knows it.
    :param on_start: called once the first samples arrive, so callers can report
        "playing" only when it is actually true.
    :raises DecodeError: ffmpeg refused the stream, or produced nothing at all.
    """
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-nostdin",
        "-i", "pipe:0",
        "-vn",                              # a stream can carry cover art; ignore it
        # WAV rather than raw s16le: the minimal build carries the wav muxer, and it
        # means the decoded stream goes through the same reader as a native WAV one -
        # one tested path instead of two.
        "-f", "wav",
        "-acodec", "pcm_s16le",
        "-ac", str(OUT_CHANNELS),
        "-ar", str(OUT_RATE),
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None

    async def pump_in() -> None:
        """Feed the encoder's bytes in, and close the pipe when the source ends."""
        try:
            if prefix:
                proc.stdin.write(prefix)
                await proc.stdin.drain()
            while True:
                data = await read(_FEED_CHUNK)
                if not data:
                    break
                proc.stdin.write(data)
                await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass  # ffmpeg gave up on the stream; the reason is on stderr
        except asyncio.CancelledError:
            raise
        finally:
            with contextlib.suppress(Exception):
                proc.stdin.close()

    feeder = asyncio.create_task(pump_in())
    counted = {"bytes": 0}

    def deliver(pcm: bytes, rate: int, channels: int) -> None:
        if not counted["bytes"] and on_start is not None:
            on_start()
        counted["bytes"] += len(pcm)
        on_chunk(pcm, rate, channels)

    try:
        await feed_wav(proc.stdout.read, deliver)
    except NotWav:
        pass  # ffmpeg produced nothing usable; the reason is on stderr, reported below
    finally:
        feeder.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await feeder
        # Kill FIRST, then drain. Reading stderr while ffmpeg is still alive waits for
        # an EOF that will not come until it exits - two seconds of nothing on every
        # stop, which was enough for a control point to give up on the Stop call.
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        err = b""
        with contextlib.suppress(Exception):
            err = await asyncio.wait_for(proc.stderr.read(), timeout=2)
        with contextlib.suppress(Exception):
            await proc.wait()

    if not counted["bytes"]:
        reason = err.decode(errors="replace").strip().splitlines()
        detail = reason[-1] if reason else "no audio came out"
        raise DecodeError(f"{hint or 'stream'}: {detail}"[:200])


async def decode_url(
    url: str,
    on_chunk: Callable[[bytes, int, int], None],
    *,
    ffmpeg: str,
    on_start: Callable[[], None] | None = None,
) -> None:
    """
    Decode straight from a URL, letting ffmpeg do the fetching.

    Two of the inputs are handed an HTTP URL rather than a stream, and giving it to
    ffmpeg instead of piping the bytes ourselves buys something real: it can seek. An
    MP4 keeps its index at the END of the file, so through a one-way pipe it cannot be
    read at all - which is exactly how m4a failed before this existed.

    :raises DecodeError: nothing decodable at that URL.
    """
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-i", url, "-vn", "-f", "wav", "-acodec", "pcm_s16le",
        "-ac", str(OUT_CHANNELS), "-ar", str(OUT_RATE), "pipe:1",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert proc.stdout is not None and proc.stderr is not None
    counted = {"bytes": 0}

    def deliver(pcm: bytes, rate: int, channels: int) -> None:
        if not counted["bytes"] and on_start is not None:
            on_start()
        counted["bytes"] += len(pcm)
        on_chunk(pcm, rate, channels)

    try:
        await feed_wav(proc.stdout.read, deliver)
    except NotWav:
        pass
    finally:
        # Kill FIRST, then drain. Reading stderr while ffmpeg is still alive waits for
        # an EOF that will not come until it exits - two seconds of nothing on every
        # stop, which was enough for a control point to give up on the Stop call.
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        err = b""
        with contextlib.suppress(Exception):
            err = await asyncio.wait_for(proc.stderr.read(), timeout=2)
        with contextlib.suppress(Exception):
            await proc.wait()

    if not counted["bytes"]:
        reason = err.decode(errors="replace").strip().splitlines()
        raise DecodeError((reason[-1] if reason else "no audio came out")[:200])


async def probe_codec(ffmpeg: str, sample: bytes) -> str:
    """
    Name the format of the first bytes of a stream, for the panel.

    Best effort: a name to show someone, never a decision. The decoder does not need it -
    ffmpeg works the format out for itself.
    """
    proc = await asyncio.create_subprocess_exec(
        ffmpeg, "-hide_banner", "-loglevel", "info", "-nostdin", "-i", "pipe:0",
        "-f", "null", "-t", "0", "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _out, err = await asyncio.wait_for(proc.communicate(sample), timeout=5)
    except (TimeoutError, asyncio.TimeoutError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        return ""
    for line in err.decode(errors="replace").splitlines():
        if "Audio:" in line:
            after = line.split("Audio:", 1)[1].strip()
            return after.split(",")[0].split(" ")[0]
    return ""
