"""
Read a WAV stream and hand whole frames to the daemon.

Three of the box's inputs are handed uncompressed audio over some kind of stream - a
Squeezebox server points us at an HTTP URL, a UPnP control point does the same - and all
of them need the same two things: find the format in the RIFF header, then deliver whole
frames. Doing it once means a fix to the header parsing fixes every input at once.
"""

from __future__ import annotations

import struct
from typing import Awaitable, Callable

# 20 ms at a time, which is what the feature extractor is happiest with and what the
# other inputs deliver.
BLOCK_MS = 20


class NotWav(Exception):
    """The stream is not uncompressed WAV, so we cannot read it."""


async def feed_wav(
    read: Callable[[int], Awaitable[bytes]],
    on_chunk: Callable[[bytes, int, int], None],
    *,
    on_format: Callable[[int, int, int], None] | None = None,
) -> None:
    """
    Consume a WAV stream until it ends.

    :param read: ``await read(n)`` returning up to n bytes, b"" at the end. It must
        return exactly n bytes while the header is being parsed, which every stream
        reader does for small reads.
    :param on_chunk: ``(pcm, sample_rate, channels)``.
    :param on_format: called once with ``(sample_rate, channels, bit_depth)``.
    :raises NotWav: the stream is compressed or not RIFF at all.
    """
    head = await _exact(read, 12)
    if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise NotWav("stream is not RIFF/WAVE")

    rate, channels, bits = 44100, 2, 16
    while True:
        chunk_head = await _exact(read, 8)
        cid = chunk_head[:4]
        (size,) = struct.unpack("<I", chunk_head[4:])
        if cid == b"fmt ":
            fmt = await _exact(read, size)
            channels, rate = struct.unpack_from("<HI", fmt, 2)
            bits = struct.unpack_from("<H", fmt, 14)[0]
        elif cid == b"data":
            break
        else:
            # A live stream can carry LIST/INFO before the data; skip whatever it is.
            await _exact(read, size)

    if bits != 16:
        raise NotWav(f"{bits}-bit audio is not supported")
    if on_format is not None:
        on_format(rate, channels, bits)

    frame = channels * 2
    block = max(frame, (rate * BLOCK_MS // 1000) * frame)
    tail = b""
    while True:
        data = await read(block)
        if not data:
            return
        data = tail + data
        usable = len(data) - (len(data) % frame)
        # Never hand out a partial frame: the extractor would read one sample of the
        # left channel as if it were the right and the stereo image would rotate.
        tail = data[usable:]
        if usable:
            on_chunk(data[:usable], rate, channels)


async def _exact(read: Callable[[int], Awaitable[bytes]], count: int) -> bytes:
    """Read exactly ``count`` bytes, or raise if the stream ends first."""
    out = b""
    while len(out) < count:
        piece = await read(count - len(out))
        if not piece:
            raise NotWav("stream ended inside the header")
        out += piece
    return out
