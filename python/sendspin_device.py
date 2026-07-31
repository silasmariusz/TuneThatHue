"""
Be a Sendspin device on the network, so a server finds us instead of us finding it.

The daemon's usual input is VBAN: something has to be told our address and pointed at
us. This is the other direction. We advertise over mDNS as a Sendspin client and a
Sendspin server - Music Assistant - opens the WebSocket towards us, hands us the
visualizer and colour roles, and from then on sends the audio *features* it has already
extracted. Nothing to decode and no beat tracking of our own: the same extractor that
runs inside the provider is doing the work, so an effect looks here exactly as it looks
there.

Two things make this cheap:

- ``aiosendspin`` already ships ``ClientListener``, which does the advertising
  (``_sendspin._tcp.local.``, port 8928, TXT ``path``) and the accept.
- the frames that arrive map one-for-one onto the analyzer calls the VBAN path already
  makes: ``apply_spectrum``, ``apply_peak``, ``push_beats``.

The one subtlety is time. Frame timestamps are in the SERVER's clock, so while a server
is driving us the render loop must ask for server time too - see ``server_time_us``.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import TYPE_CHECKING, Any, Callable

from aiohttp import web
from aiosendspin.client import SendspinClient
from aiosendspin.client.listener import ClientListener
from aiosendspin.models import Roles
from aiosendspin.models.core import DeviceInfo
from aiosendspin.models.types import UndefinedField
from aiosendspin.models.visualizer import (
    BeatTiming,
    ClientHelloVisualizerSpectrum,
    ClientHelloVisualizerSupport,
)

if TYPE_CHECKING:
    from aiosendspin.models.core import ServerStatePayload
    from aiosendspin.models.visualizer import VisualizerFrame

# The spectrum shape the engine expects. These are the provider's own numbers - the
# analyzer indexes bands by position, so a different bin count would silently colour
# the wrong lights.
SPECTRUM_BINS = 17
SPECTRUM_SCALE = "mel"
SPECTRUM_F_MIN = 40
SPECTRUM_F_MAX = 16000
VISUALIZER_RATE_HZ = 20
BUFFER_CAPACITY = 2048

# Colour keys the provider's palette accepts. Anything else in the payload is ignored.
_COLOR_KEYS = (
    "background_dark",
    "background_light",
    "primary",
    "accent",
    "on_dark",
    "on_light",
)


class SendspinDevice:
    """
    Advertise as a Sendspin client and feed whatever a server sends into the engine.

    :param analyzer: the running engine; frames are applied to it directly.
    :param name: how the box should appear on the network.
    :param on_change: called whenever the connection state changes, so the panel can
        show it without polling the library.
    """

    def __init__(
        self,
        analyzer: Any,
        name: str,
        *,
        on_change: Callable[[], None] | None = None,
        port: int = 8928,
    ) -> None:
        self.analyzer = analyzer
        self.name = name
        self.port = port
        self._on_change = on_change
        self._listener: ClientListener | None = None
        self._client: SendspinClient | None = None
        self.server_name: str = ""
        self.frames = 0
        self.beats = 0
        self.streaming = False
        # Last spectrum seen, so the panel's meter works on this input too.
        self.last_spectrum: list[int] = []

    # -- lifecycle ----------------------------------------------------------------

    async def start(self) -> None:
        """Begin advertising. Servers may connect at any point after this returns."""
        if self._listener is not None:
            return
        self._listener = ClientListener(
            client_id=self._client_id(),
            on_connection=self._handle_connection,
            client_name=self.name,
            port=self.port,
        )
        await self._listener.start()
        print(f"[sendspin] advertising as '{self.name}' on port {self.port}")

    async def stop(self) -> None:
        """Stop advertising and drop any server that is connected."""
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.disconnect()
            self._client = None
        if self._listener is not None:
            with contextlib.suppress(Exception):
                await self._listener.stop()
            self._listener = None
        self._set_state(server_name="", streaming=False)

    @property
    def connected(self) -> bool:
        """Whether a Sendspin server is currently attached."""
        return self._client is not None and self._client.connected

    def server_time_us(self, client_time_us: int) -> int | None:
        """
        Translate our clock into the server's, for the render loop.

        Frame timestamps arrive in server time, so rendering against our own clock
        would place every effect at the wrong moment. Returns None when no server is
        attached, which tells the caller to keep using local time.
        """
        client = self._client
        if client is None or not client.connected:
            return None
        try:
            return client.compute_server_time(client_time_us)
        except Exception:  # noqa: BLE001 - clock not synchronised yet
            return None

    # -- the connection ------------------------------------------------------------

    async def _handle_connection(self, ws: web.WebSocketResponse) -> None:
        """Serve one server connection for as long as it lasts."""
        client = SendspinClient(
            client_id=self._client_id(),
            client_name=self.name,
            roles=[Roles.VISUALIZER, Roles.COLOR],
            device_info=DeviceInfo(manufacturer="devspark.pl", product_name="TuneThatHue"),
            visualizer_support=ClientHelloVisualizerSupport(
                buffer_capacity=BUFFER_CAPACITY,
                rate_max=VISUALIZER_RATE_HZ,
                types=["beat", "peak", "spectrum"],
                spectrum=ClientHelloVisualizerSpectrum(
                    n_disp_bins=SPECTRUM_BINS,
                    scale=SPECTRUM_SCALE,
                    f_min=SPECTRUM_F_MIN,
                    f_max=SPECTRUM_F_MAX,
                ),
            ),
        )
        client.add_visualizer_listener(self._on_frames)
        client.add_color_listener(self._on_color)
        client.add_stream_start_listener(self._on_stream_start)
        client.add_stream_end_listener(self._on_stream_end)

        disconnected = asyncio.Event()
        client.add_disconnect_listener(disconnected.set)
        self._client = client
        try:
            await client.attach_websocket(ws)
        except Exception as err:  # noqa: BLE001 - a bad handshake must not kill the daemon
            print(f"[sendspin] handshake failed: {err}")
            self._client = None
            return
        info = client.server_info
        self._set_state(server_name=(info.name if info else "") or "server")
        print(f"[sendspin] connected to '{self.server_name}'")
        try:
            await disconnected.wait()
        finally:
            # A server that goes away must not leave the lights frozen on its last
            # frame, and the listener keeps advertising so it can come straight back.
            self.analyzer.clear_beats()
            self._client = None
            self._set_state(server_name="", streaming=False)
            print("[sendspin] server disconnected")

    # -- what arrives --------------------------------------------------------------

    def _on_frames(self, frames: list[VisualizerFrame]) -> None:
        """Apply a batch of extracted frames - the same calls the VBAN path makes."""
        beats: list[BeatTiming] = []
        for frame in frames:
            # A beat frame carries only its downbeat flag; it is not a spectrum frame.
            if frame.is_downbeat is not None:
                beats.append(
                    BeatTiming(timestamp_us=frame.timestamp_us, is_downbeat=frame.is_downbeat)
                )
                continue
            if frame.spectrum is not None:
                self.analyzer.apply_spectrum(frame.spectrum, frame.timestamp_us)
                self.frames += 1
                self.last_spectrum = [int(v) for v in frame.spectrum]
            if frame.peak_strength is not None:
                self.analyzer.apply_peak(frame.peak_strength, frame.timestamp_us)
        if beats:
            self.analyzer.push_beats(beats)
            self.beats += len(beats)

    def _on_color(self, payload: ServerStatePayload) -> None:
        """Take the colours the server derived from the artwork."""
        if payload.color is None:
            return
        update: dict[str, tuple[int, int, int] | None] = {}
        for key in _COLOR_KEYS:
            value = getattr(payload.color, key, None)
            if isinstance(value, UndefinedField):
                continue
            update[key] = value
        if update:
            self.analyzer.apply_color_palette(update)

    def _on_stream_start(self, *_args: object) -> None:
        self._set_state(streaming=True)

    def _on_stream_end(self, *_args: object) -> None:
        # Beats from the finished track would otherwise keep firing into the next one.
        self.analyzer.clear_beats()
        self._set_state(streaming=False)

    # -- helpers -------------------------------------------------------------------

    def _client_id(self) -> str:
        """
        A stable id, so a server recognises us across restarts.

        Derived from the hostname: reinstalling must not leave a second player behind
        in the server's list.
        """
        host = socket.gethostname().lower()
        safe = "".join(c if c.isalnum() else "-" for c in host).strip("-")
        return f"tunethathue-{safe or 'box'}"

    def _set_state(self, **changes: Any) -> None:
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
            "enabled": self._listener is not None,
            "connected": self.connected,
            "server": self.server_name,
            "streaming": self.streaming,
            "frames": self.frames,
            "beats": self.beats,
            "name": self.name,
            "port": self.port,
            "spectrum": self.last_spectrum,
        }
