"""
Appear as a Squeezebox player, so any Logitech Media Server or Music Assistant can send
audio here.

This is the widest-reaching of the box's inputs: slimproto has been around for twenty
years and every LMS, every Music Assistant and a pile of hardware speaks it. The player
finds the server itself with a broadcast, so nothing has to be configured.

How it goes, from `aioslimproto` - the library the servers run:

1. broadcast a datagram to UDP 3483 starting with ``e`` and carrying TLVs (4-byte tag,
   1-byte length, value) asking for ``NAME``, ``IPAD``, ``JSON``, ``VERS``; the server
   replies with ``E`` and the same tags filled in;
2. connect TCP to that address on 3483. Frames are a 4-byte operation, a big-endian u32
   length, then the payload;
3. say ``HELO`` - device id, revision, MAC, capabilities - and from then on answer with
   ``STAT`` messages, which is how the server knows we are alive and where we are;
4. the server sends ``strm`` with an HTTP request in it. We open that request, read the
   stream, and hand the samples to the daemon.

**The one prerequisite.** Set that player's output codec to ``wav`` on the server side.
The samples then arrive uncompressed and there is nothing to decode; on flac or mp3 this
player can do nothing, and says so rather than sitting silent.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import struct
import time
import uuid
from typing import Any, Callable

DISCOVERY_PORT = 3483
SLIM_PORT = 3483
DISCOVERY_TIMEOUT_S = 3.0
RECONNECT_DELAY_S = 5.0
HEARTBEAT_S = 5.0
# 12 = "squeezeplay" in the server's device table: a software player, which is what we
# are. Hardware ids make servers assume hardware volume and display behaviour.
DEVICE_ID = 12
# What we tell the server we can play. Wav first: it is the only one we can actually
# read, and the server picks from this list.
CAPABILITIES = "wav,pcm,Model=squeezeplay,ModelName=TuneThatHue,Firmware=1"

_STAT = struct.Struct("!BBBLLLLHLLLLHLL")
_STRM = struct.Struct("!cc5sBcBcBBBLHL")


class SlimprotoPlayer:
    """
    A slimproto player that listens instead of playing.

    :param on_chunk: ``(pcm, sample_rate, channels)`` - the daemon's audio entry point.
    :param name: the name the server shows for this player.
    :param host: a server address, or empty to find one by broadcast.
    """

    def __init__(
        self,
        on_chunk: Callable[[bytes, int, int], None],
        *,
        name: str = "TuneThatHue",
        host: str = "",
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._on_chunk = on_chunk
        self._on_change = on_change
        self.name = name
        self.configured_host = host

        self.server = ""
        self.server_name = ""
        self.connected = False
        self.playing = False
        self.format = ""
        self.bytes_in = 0
        self.error = ""

        self._task: asyncio.Task[None] | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._mac = _mac_bytes()

    # -- lifecycle ------------------------------------------------------------------

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._stop_stream()
        self._close_writer()
        self._set(connected=False, playing=False, server="", server_name="")

    async def _run(self) -> None:
        while True:
            try:
                host = self.configured_host or await self._discover()
                if not host:
                    self._set(error="no server answered the broadcast")
                else:
                    await self._session(host)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - servers come and go
                self._set(error=str(err) or err.__class__.__name__)
            await self._stop_stream()
            self._close_writer()
            self._set(connected=False, playing=False)
            await asyncio.sleep(RECONNECT_DELAY_S)

    # -- finding a server -------------------------------------------------------------

    async def _discover(self) -> str:
        """
        Broadcast for a server and take the first that answers.

        The reply carries the server's own address in ``IPAD``, which is what we connect
        to - the datagram's source address can be a different interface on a multi-homed
        server.
        """
        loop = asyncio.get_running_loop()
        answer: asyncio.Future[tuple[str, dict[str, str]]] = loop.create_future()

        class _Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                if answer.done() or not data.startswith(b"E"):
                    return
                with contextlib.suppress(Exception):
                    answer.set_result((addr[0], _parse_tlv(data[1:])))

        transport, _ = await loop.create_datagram_endpoint(
            _Proto, local_addr=("0.0.0.0", 0), allow_broadcast=True
        )
        try:
            transport.get_extra_info("socket").setsockopt(
                socket.SOL_SOCKET, socket.SO_BROADCAST, 1
            )
            request = b"e" + _build_tlv(["NAME", "IPAD", "JSON", "VERS"])
            transport.sendto(request, ("255.255.255.255", DISCOVERY_PORT))
            addr, fields = await asyncio.wait_for(answer, DISCOVERY_TIMEOUT_S)
        except (TimeoutError, asyncio.TimeoutError):
            return ""
        finally:
            transport.close()
        self._set(server_name=fields.get("NAME", ""))
        return fields.get("IPAD") or addr

    # -- the connection -----------------------------------------------------------------

    async def _session(self, host: str) -> None:
        reader, writer = await asyncio.open_connection(host, SLIM_PORT)
        self._writer = writer
        self._set(connected=True, server=host, error="")
        print(f"[slimproto] connected to {host} as '{self.name}'")
        await self._send(b"HELO", bytes([DEVICE_ID, 0]) + self._mac + CAPABILITIES.encode())
        await self._stat(b"STMc")
        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            while True:
                # Server -> player: u16 length (counting the 4-byte command), command,
                # payload. The other direction is framed differently - see _send.
                (length,) = struct.unpack("!H", await reader.readexactly(2))
                body = await reader.readexactly(length)
                op = body[:4].strip().decode(errors="replace").lower()
                await self._handle(op, body[4:])
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            print("[slimproto] disconnected")

    async def _handle(self, op: str, payload: bytes) -> None:
        if op == "strm":
            await self._handle_strm(payload)
        elif op == "serv":
            # Being handed to another server: drop and let the loop rediscover.
            raise ConnectionResetError("server handover")
        # aude/audg/setd are volume, name and display; a light has no use for them.

    async def _handle_strm(self, payload: bytes) -> None:
        """Start or stop a stream. The HTTP request to make is in the tail."""
        if len(payload) < _STRM.size:
            return
        fields = _STRM.unpack(payload[: _STRM.size])
        command = fields[0]
        if command != b"s":
            # p pause, q stop, f flush, a skip, t status - all mean "stop reading" here.
            if command in (b"q", b"f"):
                await self._stop_stream()
            if command == b"t":
                await self._stat(b"STMt")
            return
        server_port, server_ip = fields[-2], fields[-1]
        httpreq = payload[_STRM.size :]
        host = self.server if server_ip == 0 else str(_ip_from_int(server_ip))
        port = server_port or 80
        await self._stop_stream()
        self._stream_task = asyncio.create_task(self._stream(host, port, httpreq))

    # -- reading the audio -------------------------------------------------------------

    async def _stream(self, host: str, port: int, httpreq: bytes) -> None:
        """
        Fetch what the server pointed us at and feed the samples through.

        The server sends a complete HTTP request; we do not build one, we replay it, so
        whatever the server wanted - path, range, headers - is what it gets.
        """
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except Exception as err:  # noqa: BLE001
            self._set(error=f"stream connect failed: {err}")
            await self._stat(b"STMn")
            return
        try:
            writer.write(httpreq if httpreq else b"GET / HTTP/1.0\r\n\r\n")
            await writer.drain()
            await self._read_http_headers(reader)
            await self._stat(b"STMs")
            self._set(playing=True)
            await self._read_pcm(reader)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - a track ending closes the socket
            self._set(error=str(err))
        finally:
            with contextlib.suppress(Exception):
                writer.close()
            self._set(playing=False, format="")
            await self._stat(b"STMu")  # buffer underrun = end of playback

    async def _read_http_headers(self, reader: asyncio.StreamReader) -> None:
        while True:
            line = await reader.readline()
            if not line or line in (b"\r\n", b"\n"):
                return

    async def _read_pcm(self, reader: asyncio.StreamReader) -> None:
        """
        Read a WAV stream and hand out whole frames.

        Anything that is not RIFF/WAVE we cannot decode; say which format it was and
        stop, rather than feeding noise into the analyzer.
        """
        head = await reader.readexactly(12)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            self._set(error="server is not sending wav - set this player's output codec to wav")
            print("[slimproto] stream is not wav; set the player's output codec to wav")
            return
        rate, channels, bits = 44100, 2, 16
        while True:
            chunk_head = await reader.readexactly(8)
            cid, size = chunk_head[:4], struct.unpack("<I", chunk_head[4:])[0]
            if cid == b"fmt ":
                fmt = await reader.readexactly(size)
                channels, rate = struct.unpack_from("<HI", fmt, 2)
                bits = struct.unpack_from("<H", fmt, 14)[0]
            elif cid == b"data":
                break
            else:
                await reader.readexactly(size)
        if bits != 16:
            self._set(error=f"{bits}-bit audio is not supported")
            return
        self._set(format=f"{rate} Hz / {channels}ch / {bits}-bit", error="")
        print(f"[slimproto] stream: wav {rate} Hz / {channels}ch / {bits}-bit")
        frame = channels * 2
        # 20 ms at a time, which is what the extractor is happiest with and what the
        # other inputs deliver.
        block = max(frame, (rate // 50) * frame)
        while True:
            data = await reader.read(block)
            if not data:
                return
            self.bytes_in += len(data)
            usable = len(data) - (len(data) % frame)
            if usable:
                self._on_chunk(data[:usable], rate, channels)

    async def _stop_stream(self) -> None:
        task, self._stream_task = self._stream_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._set(playing=False)

    # -- talking back ------------------------------------------------------------------

    async def _heartbeat(self) -> None:
        """A player that stops sending STAT is dropped, so keep ticking."""
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            with contextlib.suppress(Exception):
                await self._stat(b"STMt")

    async def _stat(self, event: bytes) -> None:
        """
        Send a STAT message.

        The numbers are honest but uninteresting: we are not a speaker, so there is no
        output buffer to report. What matters to the server is that they arrive.
        """
        jiffies = int(time.monotonic() * 1000) & 0xFFFFFFFF
        payload = event + _STAT.pack(
            0, 0, 0,          # crlf, mas initialized, mas mode
            0, 0,             # buffer size, fullness
            0, self.bytes_in & 0xFFFFFFFF,  # bytes received, high and low
            0,                # signal strength
            jiffies,
            0, 0,             # output buffer size and readyness
            0,                # elapsed seconds
            0,                # voltage
            0,                # elapsed milliseconds
            0,                # server heartbeat
        )
        await self._send(b"STAT", payload)

    async def _send(self, op: bytes, payload: bytes) -> None:
        """
        Player -> server: the 4-byte operation FIRST, then a u32 length of the payload.

        The other direction puts a u16 length first and the operation after it. Getting
        this backwards makes the server read the operation as a length and quietly never
        register the player - which is exactly what it did.
        """
        writer = self._writer
        if writer is None:
            return
        writer.write(op + struct.pack("!I", len(payload)) + payload)
        await writer.drain()

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
            "playing": self.playing,
            "server": self.server,
            "server_name": self.server_name,
            "name": self.name,
            "format": self.format,
            "bytes": self.bytes_in,
            "error": self.error,
        }


# -- the little bits of the protocol --------------------------------------------------


def _build_tlv(tags: list[str]) -> bytes:
    """Tag, length, value - with the length zero, which is how you ask rather than tell."""
    return b"".join(tag.encode()[:4].ljust(4)[:4] + b"\x00" for tag in tags)


def _parse_tlv(data: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    i = 0
    while i + 5 <= len(data):
        tag = data[i : i + 4].decode(errors="replace")
        length = data[i + 4]
        out[tag] = data[i + 5 : i + 5 + length].decode(errors="replace")
        i += 5 + length
    return out


def _ip_from_int(value: int) -> str:
    return ".".join(str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _mac_bytes() -> bytes:
    """
    A stable MAC-shaped id: the server keys the player on it.

    A random one every start would leave a trail of dead players in the server's list.
    """
    node = uuid.getnode()
    return bytes((node >> shift) & 0xFF for shift in (40, 32, 24, 16, 8, 0))
