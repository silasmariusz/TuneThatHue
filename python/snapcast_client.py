"""
Join a Snapcast group as a client, and light the room with what the group is playing.

Snapcast is the multiroom system most likely to already be running next to a NAS, and it
is not tied to any one music server. A client dials the server, says hello, and from then
on receives the same audio every other speaker in its group receives. Grouping is done on
the server, so once the box is connected it can be dragged into a group in any Snapcast
controller and it follows.

**The one prerequisite.** The transport codec is the server's choice, not the client's.
With ``pcm`` the chunks are raw samples and there is nothing to decode; with the default
``flac`` they are compressed and this client cannot read them. So the server has to be set
to ``pcm`` (in Music Assistant: the Snapcast provider's transport codec setting). Real
snapclients play ``pcm`` perfectly well, so switching costs the rest of the house nothing.
Rather than fail silently on the wrong codec, we say so in the log and in the panel.

Wire format, from Snapcast's own ``binary_protocol.md``: every message is a 26-byte base
header - type u16, id u16, refersTo u16, sent sec/usec i32, received sec/usec i32,
size u32 - then the payload, all little endian.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import struct
import time
import uuid
from typing import Any, Callable

from decoder import DecodeError, decode_stream, find_ffmpeg

# Message types (server -> client unless noted).
MSG_CODEC_HEADER = 1
MSG_WIRE_CHUNK = 2
MSG_SERVER_SETTINGS = 3
MSG_TIME = 4
MSG_HELLO = 5  # client -> server
MSG_ERROR = 8

BASE_HEADER = struct.Struct("<HHHiiiiI")
DEFAULT_PORT = 1704
TIME_SYNC_INTERVAL_S = 5.0
RECONNECT_DELAY_S = 5.0
# A codec we cannot read is a configuration mistake, not a transient fault; say it once
# rather than every time a chunk arrives.
# pcm needs no decoder at all; the rest go through ffmpeg, fed the codec header the
# server sent followed by every chunk, which is exactly the byte stream an encoder
# produced in the first place.
NATIVE_CODECS = ("pcm",)
# Snapcast's opus mode sends RAW Opus packets with no container - flac chunks concatenate
# into a valid flac stream and ogg chunks carry their own pages, but raw opus packets
# cannot be demuxed by anything without being re-framed first. Say so rather than sitting
# there decoding nothing.
UNDECODABLE_CODECS = ("opus",)


def _string(text: str) -> bytes:
    """
    Snapcast's string payloads carry their own length first.

    Sending bare JSON makes the server read the opening `{"Cl` as a u32 length and then
    fail to parse from the fifth byte - which is exactly what it did.
    """
    raw = text.encode()
    return struct.pack("<I", len(raw)) + raw


class SnapcastClient:
    """
    A Snapcast client that listens rather than plays.

    Audio arrives as PCM and goes straight into the same handler the VBAN input uses, so
    the analyzer, the beat tracker and every effect behave identically whichever way the
    music got here.

    :param host: the snapserver address.
    :param on_chunk: ``(pcm, sample_rate, channels)`` - the daemon's audio entry point.
    :param name: how the box appears in Snapcast controllers.
    """

    def __init__(
        self,
        host: str,
        on_chunk: Callable[[bytes, int, int], None],
        *,
        port: int = DEFAULT_PORT,
        name: str = "TuneThatHue",
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.name = name
        self._on_chunk = on_chunk
        self._on_change = on_change
        self._task: asyncio.Task[None] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._msg_id = 0

        self.connected = False
        self.codec = ""
        self.codec_supported = False
        self.sample_rate = 48000
        self.channels = 2
        self.bit_depth = 16
        self.chunks = 0
        self.error = ""
        self._ffmpeg = find_ffmpeg()
        self._queue: asyncio.Queue[bytes] | None = None
        self._decode_task: asyncio.Task[None] | None = None

    # -- lifecycle ----------------------------------------------------------------

    async def start(self) -> None:
        """Connect, and keep reconnecting for as long as this client is enabled."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Disconnect and stop trying."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._stop_decoder()
        self._close_writer()
        self._set(connected=False, codec="", chunks=0)

    async def _run(self) -> None:
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - a server going away is normal
                self._set(error=str(err) or err.__class__.__name__)
            self._close_writer()
            self._set(connected=False)
            # A snapserver restarts, a NAS reboots; keep coming back without a fuss.
            await asyncio.sleep(RECONNECT_DELAY_S)

    # -- one connection -------------------------------------------------------------

    async def _session(self) -> None:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        self._writer = writer
        self._set(connected=True, error="")
        print(f"[snapcast] connected to {self.host}:{self.port}")
        await self._send(MSG_HELLO, _string(json.dumps(self._hello())))
        time_task = asyncio.create_task(self._time_loop())
        try:
            while True:
                header = await reader.readexactly(BASE_HEADER.size)
                msg_type, _mid, _refers, _ss, _su, _rs, _ru, size = BASE_HEADER.unpack(header)
                payload = await reader.readexactly(size) if size else b""
                self._dispatch(msg_type, payload)
        finally:
            time_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await time_task
            self._stop_decoder()
            print("[snapcast] disconnected")

    def _dispatch(self, msg_type: int, payload: bytes) -> None:
        if msg_type == MSG_WIRE_CHUNK:
            self._on_wire_chunk(payload)
        elif msg_type == MSG_CODEC_HEADER:
            self._on_codec_header(payload)
        elif msg_type == MSG_SERVER_SETTINGS:
            # Volume and mute are the server's business; we only light the room, so
            # there is nothing here to obey. Kept for the log.
            with contextlib.suppress(Exception):
                print(f"[snapcast] server settings: {payload.decode()[:120]}")
        elif msg_type == MSG_ERROR:
            self._set(error=payload.decode(errors="replace")[:200])

    def _on_codec_header(self, payload: bytes) -> None:
        """
        Learn the stream format, and refuse politely if we cannot read it.

        The header is a length-prefixed codec name followed by a length-prefixed blob.
        For ``pcm`` that blob is a WAV header, which is where the real sample rate and
        channel count come from - the defaults are only a fallback.
        """
        (name_len,) = struct.unpack_from("<I", payload, 0)
        codec = payload[4 : 4 + name_len].decode(errors="replace")
        (blob_len,) = struct.unpack_from("<I", payload, 4 + name_len)
        blob = payload[8 + name_len : 8 + name_len + blob_len]
        supported = codec not in UNDECODABLE_CODECS and (
            codec in NATIVE_CODECS or self._ffmpeg is not None
        )
        rate, channels, bits = self.sample_rate, self.channels, self.bit_depth
        if supported and len(blob) >= 36 and blob[:4] == b"RIFF":
            channels, rate, bits = struct.unpack_from("<HI", blob, 22) + (
                struct.unpack_from("<H", blob, 34)[0],
            )
        self._set(
            codec=codec,
            codec_supported=supported,
            sample_rate=rate,
            channels=channels,
            bit_depth=bits,
        )
        if codec in NATIVE_CODECS:
            print(f"[snapcast] stream: {codec} {rate} Hz / {channels}ch / {bits}-bit")
        elif supported:
            # Everything the server can send is an encoder's output, so hand ffmpeg the
            # codec header it just gave us and then every chunk after it - that is the
            # byte stream the encoder produced.
            print(f"[snapcast] stream: {codec}, decoding")
            self._start_decoder(blob)
        elif codec in UNDECODABLE_CODECS:
            self._set(error=f"snapcast sends {codec} without a container, which cannot be "
                            f"decoded - use flac, ogg or pcm")
            print(f"[snapcast] '{codec}' has no container on this transport; "
                  f"set the server to flac, ogg or pcm")
        else:
            self._set(error=f"'{codec}' needs ffmpeg, which was not found")
            print(
                f"[snapcast] stream is '{codec}' and no ffmpeg was found - "
                f"set the snapserver transport codec to pcm"
            )

    def _on_wire_chunk(self, payload: bytes) -> None:
        """A chunk is a timestamp and then the audio; with pcm that audio is samples."""
        if not self.codec_supported or len(payload) <= 12:
            return
        audio = payload[12:]
        self.chunks += 1
        if self.codec in NATIVE_CODECS:
            # 16-bit is what the extractor expects, and snapserver's pcm is 16-bit in
            # every configuration we support.
            if self.bit_depth == 16:
                self._on_chunk(audio, self.sample_rate, self.channels)
            return
        queue = self._queue
        if queue is None:
            return
        if queue.full():
            # The decoder has fallen behind. Dropping the oldest keeps us near live
            # rather than building a delay the lights would show.
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(audio)

    def _start_decoder(self, header: bytes) -> None:
        """Run ffmpeg over the encoded chunks for as long as this stream lasts."""
        self._stop_decoder()
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=250)   # ~5 s at 20 ms
        self._queue = queue

        async def read(_n: int) -> bytes:
            return await queue.get()

        async def run() -> None:
            try:
                await decode_stream(
                    read, self._on_chunk, ffmpeg=self._ffmpeg or "", hint=self.codec,
                    prefix=header,
                )
            except asyncio.CancelledError:
                raise
            except DecodeError as err:
                self._set(error=str(err))
                print(f"[snapcast] {err}")

        self._decode_task = asyncio.create_task(run())

    def _stop_decoder(self) -> None:
        task, self._decode_task = self._decode_task, None
        self._queue = None
        if task is not None:
            task.cancel()

    # -- housekeeping ---------------------------------------------------------------

    async def _time_loop(self) -> None:
        """
        Keep exchanging Time messages.

        We do not use the result: nothing here has to be sample-accurate against other
        speakers, because the light is not sound and a few milliseconds do not read. But
        a snapserver expects its clients to keep talking, and one that goes quiet is
        dropped.
        """
        while True:
            await asyncio.sleep(TIME_SYNC_INTERVAL_S)
            now = time.time()
            payload = struct.pack("<ii", int(now), int((now % 1) * 1_000_000))
            with contextlib.suppress(Exception):
                await self._send(MSG_TIME, payload)

    async def _send(self, msg_type: int, payload: bytes) -> None:
        writer = self._writer
        if writer is None:
            return
        self._msg_id += 1
        now = time.time()
        header = BASE_HEADER.pack(
            msg_type, self._msg_id, 0, int(now), int((now % 1) * 1_000_000), 0, 0, len(payload)
        )
        writer.write(header + payload)
        await writer.drain()

    def _hello(self) -> dict[str, Any]:
        mac = ":".join(f"{(uuid.getnode() >> i) & 0xFF:02x}" for i in range(40, -1, -8))
        host = socket.gethostname()
        return {
            "ClientName": self.name,
            "HostName": host,
            "ID": mac,
            "MAC": mac,
            "Version": "0.27.0",
            "OS": "Linux",
            "Arch": "x86_64",
            "Instance": 1,
            "SnapStreamProtocolVersion": 2,
        }

    def _close_writer(self) -> None:
        writer, self._writer = self._writer, None
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()

    def _set(self, **changes: Any) -> None:
        changed = False
        for key, value in changes.items():
            if getattr(self, key, None) != value:
                setattr(self, key, value)
                changed = True
        if changed and self._on_change is not None:
            self._on_change()

    def status(self) -> dict[str, Any]:
        """What the panel shows about this input."""
        return {
            "enabled": self._task is not None,
            "connected": self.connected,
            "host": self.host,
            "name": self.name,
            "codec": self.codec,
            "codec_supported": self.codec_supported,
            "format": f"{self.sample_rate} Hz / {self.channels}ch / {self.bit_depth}-bit",
            "chunks": self.chunks,
            "error": self.error,
        }
