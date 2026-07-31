"""
Send the same frames to a WLED strip that go to the Hue bridge.

A Hue area is a handful of lamps with positions in a room. A strip is hundreds of pixels
in a line. So a strip is not "one more lamp": it is divided into as many segments as the
area has lamps, and each segment takes that lamp's colour. Lamp two lights the second
stretch of the strip, and a chase across the room becomes a chase along the strip.

The wire format is WLED's own real-time UDP protocol, on port 21324:

    DNRGB  [2][timeout][start hi][start lo][r g b] x N   - carries an index, so it can
                                                           address more than 490 pixels
    DRGB   [1][timeout][r g b] x N                       - from pixel zero, up to 490

The second byte is how long WLED should keep showing what we sent before it goes back to
its own effects. Two seconds: long enough to ride out a dropped packet, short enough that
the strip does not stay frozen if the daemon dies.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from typing import Any, Callable

WLED_PORT = 21324
PROTOCOL_DRGB = 1
PROTOCOL_DNRGB = 4
# Seconds WLED keeps our frame before returning to its own effects.
HOLD_S = 2
# One datagram per frame; above this many pixels it has to be split.
MAX_PIXELS_PER_PACKET = 480
# The strip does not need every frame the bridge gets, and a NAS on wifi should not
# send 30 datagrams a second if 20 look the same.
SEND_RATE_HZ = 25


class WledOutput:
    """
    A WLED strip driven by the engine's frames.

    :param host: the strip's address.
    :param pixels: how many LEDs it has.
    :param name: what to call it in the panel.
    """

    def __init__(
        self,
        host: str,
        pixels: int,
        *,
        name: str = "WLED",
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.host = host
        self.pixels = max(1, int(pixels))
        self.name = name
        self._on_change = on_change
        self._sock: socket.socket | None = None
        self._last_send = 0.0
        self._period = 1.0 / SEND_RATE_HZ

        self.enabled = False
        self.frames = 0
        self.error = ""

    def start(self) -> None:
        if self._sock is not None:
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self._set(enabled=True, error="")
        print(f"[wled] sending to {self.host}:{WLED_PORT}, {self.pixels} pixels")

    def stop(self) -> None:
        sock, self._sock = self._sock, None
        if sock is not None:
            # Hand the strip back to its own effects rather than leaving it on the last
            # frame we happened to send.
            with contextlib.suppress(Exception):
                sock.sendto(bytes([PROTOCOL_DRGB, 0]), (self.host, WLED_PORT))
            sock.close()
        self._set(enabled=False)

    def send(self, commands: list) -> None:
        """
        Paint the strip from one rendered frame.

        Takes the same list of per-light commands the bridge is sent, so whatever the
        engine decided - a chase, a strobe, a build recruiting lights one at a time -
        happens on the strip too.
        """
        sock = self._sock
        if sock is None or not commands:
            return
        now = time.monotonic()
        if now - self._last_send < self._period:
            return
        self._last_send = now

        # The lights map onto the strip in order, each taking an equal stretch.
        count = len(commands)
        per = max(1, self.pixels // count)
        colours = bytearray()
        for index in range(self.pixels):
            cmd = commands[min(index // per, count - 1)]
            colours += bytes((cmd.red >> 8, cmd.green >> 8, cmd.blue >> 8))

        try:
            for start in range(0, self.pixels, MAX_PIXELS_PER_PACKET):
                chunk = colours[start * 3 : (start + MAX_PIXELS_PER_PACKET) * 3]
                header = bytes([PROTOCOL_DNRGB, HOLD_S, start >> 8, start & 0xFF])
                sock.sendto(header + chunk, (self.host, WLED_PORT))
            self.frames += 1
            if self.error:
                self._set(error="")
        except OSError as err:
            # A strip that is off or has moved should not take the daemon with it.
            self._set(error=str(err))

    def _set(self, **changes: Any) -> None:
        changed = False
        for key, value in changes.items():
            if getattr(self, key, None) != value:
                setattr(self, key, value)
                changed = True
        if changed and self._on_change is not None:
            self._on_change()

    def status(self) -> dict[str, Any]:
        """What the panel shows about this output."""
        return {
            "enabled": self.enabled,
            "host": self.host,
            "pixels": self.pixels,
            "name": self.name,
            "frames": self.frames,
            "error": self.error,
        }


async def discover(timeout: float = 3.0) -> list[dict[str, str]]:
    """
    Find WLED devices on the network, so nobody has to go and read an IP off a router.

    WLED announces itself over mDNS as `_wled._tcp.local.`
    """
    from zeroconf import ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

    found: list[dict[str, str]] = []
    azc = AsyncZeroconf()

    def on_change(zeroconf, service_type, name, state_change) -> None:
        if state_change is not ServiceStateChange.Added:
            return
        asyncio.ensure_future(resolve(zeroconf, service_type, name))

    async def resolve(zc, service_type, name) -> None:
        info = AsyncServiceInfo(service_type, name)
        if await info.async_request(zc, 2000):
            addresses = info.parsed_addresses()
            if addresses:
                found.append({"name": name.split(".")[0], "host": addresses[0]})

    browser = AsyncServiceBrowser(azc.zeroconf, ["_wled._tcp.local."], handlers=[on_change])
    try:
        await asyncio.sleep(timeout)
    finally:
        await browser.async_cancel()
        await azc.async_close()
    return found
