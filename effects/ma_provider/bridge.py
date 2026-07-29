"""
Hue Entertainment bridge — connects as a Sendspin visualizer client.

Instead of being a server-side role in the PushStream (which delivers
audio 30 seconds ahead of playback), we connect as a Sendspin WebSocket
client with the visualizer role. The server computes visualization data
(FFT, loudness, spectrum) and delivers it at the right playback time
through the connection layer's built-in scheduling.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast

import hue_entertainment
from aiosendspin.client import SendspinClient
from aiosendspin.models import Roles
from aiosendspin.models.core import DeviceInfo as SendspinDeviceInfo
from aiosendspin.models.core import ServerStatePayload
from aiosendspin.models.types import UndefinedField
from aiosendspin.models.visualizer import (
    BeatTiming,
    ClientHelloVisualizerSpectrum,
    ClientHelloVisualizerSupport,
    VisualizerFrame,
)
from hue_entertainment import EntertainmentSession
from music_assistant_models.enums import PlayerType

from .analyzer import HueAudioAnalyzer, PulseSettings
from .constants import (
    CONF_BRIGHTNESS,
    CONF_CLIENTKEY,
    CONF_COLOR_BOOST,
    CONF_COLOR_MODE,
    CONF_HUE_LATENCY_MS,
    CONF_PALETTE,
    CONF_PALETTE_ROTATE,
    CONF_PALETTE_ROTATE_BEATS,
    CONF_PALETTE_ROTATE_LIST,
    CONF_PALETTE_ROTATE_SMOOTH,
    CONF_PERLIGHT_BRIGHTNESS_DATA,
    CONF_STROBE_LIGHTS,
    CONF_USERNAME,
    DEFAULT_COLOR_BOOST,
    DEFAULT_HUE_LATENCY_MS,
    DEFAULT_PALETTE_ROTATE_BEATS,
    DEFAULT_PALETTE_ROTATE_SMOOTH,
    SPECTRUM_BINS,
    SPECTRUM_F_MAX,
    SPECTRUM_F_MIN,
    SPECTRUM_SCALE,
)
from .strobe_overlay import StrobeSettings

# Colour-output (Accurate / Vivid) uses the ColorMode enum added in hue-entertainment PR #2.
# Older releases (e.g. the pinned 0.1.2) do not have it, so resolve it optionally: when it is
# absent the feature stays dormant and the stream simply falls back to RGB until the library
# is updated and the pin is bumped. Using getattr keeps this clean for static type checking.
ColorMode = getattr(hue_entertainment, "ColorMode", None)

if TYPE_CHECKING:
    from hue_entertainment import EntertainmentArea
    from hue_entertainment.api import HueEntertainmentAPI

    from music_assistant.providers.sendspin.provider import SendspinProvider

    from .provider import HueEntertainmentProvider

LOGGER = logging.getLogger(__name__)

SENDSPIN_PORT = 8927

# Hue Entertainment streams comfortably accept ~30 Hz updates over DTLS.
_RENDER_RATE_HZ = 30
_RENDER_PERIOD_S = 1.0 / _RENDER_RATE_HZ
# The render loop is a call_later chain on the event loop, so a gap this much larger
# than the period means the loop was blocked (a slow render, a stuck send, or - more
# often - some other coroutine doing blocking I/O). Logged so a freeze leaves a trace.
_RENDER_STALL_WARN_S = 0.5
# Visualizer frame rate requested from the Sendspin server. Bridge filters
# (channel rise/decay, bass baseline) are tuned for ~20 Hz spectrum input; the
# DTLS render loop runs faster and interpolates.
_VISUALIZER_RATE_HZ = 20

# Session start retries when the bridge is slow to complete the DTLS handshake.
_ENTERTAINMENT_START_ATTEMPTS = 6
_ENTERTAINMENT_START_BACKOFF_S = 1.5
_ENTERTAINMENT_STALE_COOLDOWN_S = 1.0
# Grace period before tearing down the DTLS session on a visualizer stream_end.
# Sendspin restarts its push stream at track boundaries (smart Flow-Mode sample-rate
# changes fire stream_end), and the next flow's cold track-fetch can take ~11-13 s to
# re-fire stream_start. A short debounce would drop the session in that gap; this longer
# grace keeps it warm (lib keepalives + idle_timeout=0). on_stream_start cancels the
# pending stop, so back-to-back tracks never actually wait this out.
_STREAM_END_GRACE_S = 20.0


class HueEntertainmentBridge:
    """
    Manages the Hue Entertainment bridge for a single entertainment area.

    Connects to the local Sendspin server as a visualizer client, receives
    pre-computed visualization data at the correct playback time, converts
    it to light colors, and streams to the Hue bridge over DTLS.
    """

    def __init__(
        self,
        provider: HueEntertainmentProvider,
        area: EntertainmentArea,
    ) -> None:
        """Initialize the bridge."""
        self.provider = provider
        self.mass = provider.mass
        self.area = area
        self.logger = LOGGER.getChild(f"bridge.{area.name}")

        self._session: EntertainmentSession | None = None
        self._analyzer: HueAudioAnalyzer | None = None
        self._sendspin_client: SendspinClient | None = None
        self._client_task: asyncio.Task[None] | None = None
        self._is_streaming = False
        self._unsubscribe_viz: Callable[[], None] | None = None
        self._unsubscribe_color: Callable[[], None] | None = None
        self._stop_debounce_task: asyncio.Task[None] | None = None
        self._start_task: asyncio.Task[None] | None = None
        self._render_handle: asyncio.TimerHandle | None = None
        # loop.time() of the previous render tick, for the stall watchdog in _render_tick.
        self._last_tick_time: float = 0.0
        self._entertainment_starting: bool = False
        self._hue_latency_us: int = (
            int(
                float(
                    str(
                        self.provider.config.get_value(CONF_HUE_LATENCY_MS)
                        or DEFAULT_HUE_LATENCY_MS
                    )
                )
            )
            * 1000
        )

    async def start(self) -> None:
        """Start the bridge — connect as a Sendspin visualizer client."""
        cfg = self.provider.config
        self._analyzer = HueAudioAnalyzer(
            channels=self.area.channels,
            color_mode=str(cfg.get_value(CONF_COLOR_MODE) or "smooth"),
            brightness=int(float(str(cfg.get_value(CONF_BRIGHTNESS) or 100))),
            strobe_channel_ids=self._strobe_ids_for_area(cfg.get_value(CONF_STROBE_LIGHTS)),
            strobe=StrobeSettings.from_config(cfg),
            palette=str(cfg.get_value(CONF_PALETTE) or ""),
            per_light=self._per_light_for_area(cfg.get_value(CONF_PERLIGHT_BRIGHTNESS_DATA)),
            pulse=PulseSettings.from_config(cfg),
        )
        rotate_smooth = cfg.get_value(CONF_PALETTE_ROTATE_SMOOTH)
        self._analyzer.set_rotation(
            bool(cfg.get_value(CONF_PALETTE_ROTATE)),
            cast("list[str]", cfg.get_value(CONF_PALETTE_ROTATE_LIST) or []),
            int(
                float(str(cfg.get_value(CONF_PALETTE_ROTATE_BEATS) or DEFAULT_PALETTE_ROTATE_BEATS))
            ),
            DEFAULT_PALETTE_ROTATE_SMOOTH if rotate_smooth is None else bool(rotate_smooth),
        )

        # Create Sendspin client with the visualizer + color roles
        client_id = f"hue-{self.area.id.replace('-', '')[:16]}"

        # Register this client as a LIGHT player type with the Sendspin provider
        # so the resulting player shows up correctly in the UI
        sendspin_prov: SendspinProvider | None = self.mass.get_provider("sendspin")  # type: ignore[assignment]
        if sendspin_prov:
            sendspin_prov.register_bridge_player_type(client_id, PlayerType.LIGHT)

        self._sendspin_client = SendspinClient(
            client_id=client_id,
            client_name=f"Hue: {self.area.name}",
            roles=[Roles.VISUALIZER, Roles.COLOR],
            device_info=SendspinDeviceInfo(
                manufacturer="Signify",
                product_name="Hue Entertainment Area",
            ),
            visualizer_support=ClientHelloVisualizerSupport(
                # Beat + small bundle of periodic features. Each periodic frame
                # is ~20-30 bytes; one second's worth fits comfortably under
                # the buffer cap below.
                buffer_capacity=2048,
                rate_max=_VISUALIZER_RATE_HZ,
                # Peaks requested as a fallback for when beats aren't computed yet.
                types=["beat", "peak", "spectrum"],
                spectrum=ClientHelloVisualizerSpectrum(
                    n_disp_bins=SPECTRUM_BINS,
                    scale=SPECTRUM_SCALE,
                    f_min=SPECTRUM_F_MIN,
                    f_max=SPECTRUM_F_MAX,
                ),
            ),
        )

        self._unsubscribe_viz = self._sendspin_client.add_visualizer_listener(
            self._on_visualizer_frames
        )
        self._unsubscribe_color = self._sendspin_client.add_color_listener(self._on_color)
        self._sendspin_client.add_stream_start_listener(self._on_stream_start)
        self._sendspin_client.add_stream_end_listener(self._on_stream_end)

        # Connect to local Sendspin server
        self._client_task = self.mass.create_task(self._run_client())

        self.logger.info(
            "Hue bridge started for area '%s' (%d channels)",
            self.area.name,
            len(self.area.channels),
        )
        # Make this area + its channels visible in the live browser preview.
        self.provider.preview_register_area(self.area.id, self.area.name, self.area.channels)
        self.logger.debug(
            "Hue bridge channels for area '%s': %s",
            self.area.name,
            [
                f"id={c.channel_id} svc={c.service_id} name={c.name} pos={c.position}"
                for c in self.area.channels
            ],
        )

    async def stop(self) -> None:
        """Stop the bridge."""
        self._cancel_render_loop()
        if self._unsubscribe_viz:
            self._unsubscribe_viz()
            self._unsubscribe_viz = None
        if self._unsubscribe_color:
            self._unsubscribe_color()
            self._unsubscribe_color = None

        if self._client_task and not self._client_task.done():
            self._client_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._client_task
            self._client_task = None

        if self._sendspin_client and self._sendspin_client.connected:
            await self._sendspin_client.disconnect()
        self._sendspin_client = None

        # Cancel an in-flight start so it can't adopt a session after we stop.
        if self._stop_debounce_task and not self._stop_debounce_task.done():
            self._stop_debounce_task.cancel()
        if self._start_task and not self._start_task.done():
            self._start_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._start_task
        self._start_task = None

        await self._stop_entertainment()
        self.logger.debug("Hue bridge stopped for area '%s'", self.area.name)

    def update_settings(
        self,
        color_mode: str | None = None,
        brightness: int | None = None,
        hue_latency_ms: int | None = None,
        strobe_selection: object = None,
        strobe: StrobeSettings | None = None,
        palette: str | None = None,
        per_light_data: object = None,
        pulse: PulseSettings | None = None,
    ) -> None:
        """Update analyzer/bridge settings without restarting the bridge."""
        if self._analyzer:
            strobe_ids = (
                self._strobe_ids_for_area(strobe_selection)
                if strobe_selection is not None
                else None
            )
            per_light = (
                self._per_light_for_area(per_light_data) if per_light_data is not None else None
            )
            self._analyzer.update_settings(
                color_mode=color_mode,
                brightness=brightness,
                strobe_channel_ids=strobe_ids,
                strobe=strobe,
                palette=palette,
                per_light=per_light,
                pulse=pulse,
            )
        if hue_latency_ms is not None:
            self._hue_latency_us = hue_latency_ms * 1000

    def set_rotation(
        self, enabled: bool, names: list[str], beats: int, smooth: bool = False
    ) -> None:
        """Configure bar-aligned palette rotation on this bridge's analyzer."""
        if self._analyzer:
            self._analyzer.set_rotation(enabled, names, beats, smooth)

    def _strobe_ids_for_area(self, selection: object) -> set[int]:
        """Keep only "<this area id>:<channel_id>" entries -> {channel_id}."""
        ids: set[int] = set()
        if not isinstance(selection, (list, tuple, set, frozenset)):
            return ids
        prefix = f"{self.area.id}:"
        for entry in selection:
            text = str(entry)
            if text.startswith(prefix):
                try:
                    ids.add(int(text[len(prefix) :]))
                except ValueError:
                    continue
        return ids

    def _per_light_for_area(self, data: object) -> dict[int, float]:
        """Parse the per-light brightness blob -> {channel_id: scale 0-1} for this area."""
        result: dict[int, float] = {}
        if not data or not isinstance(data, str):
            return result
        try:
            mapping = json.loads(data)
        except ValueError, TypeError:
            return result
        if not isinstance(mapping, dict):
            return result
        prefix = f"{self.area.id}:"
        for key, pct in mapping.items():
            text = str(key)
            if not text.startswith(prefix):
                continue
            try:
                channel_id = int(text[len(prefix) :])
                result[channel_id] = max(0.0, min(100.0, float(pct))) / 100.0
            except ValueError, TypeError:
                continue
        return result

    async def _run_client(self) -> None:
        """Connect to the Sendspin server and stay connected."""
        try:
            assert self._sendspin_client is not None
            bind_ip = self.mass.streams.bind_ip
            ws_url = f"ws://{bind_ip}:{SENDSPIN_PORT}/sendspin"
            await self._sendspin_client.connect(ws_url)
            self.logger.info("Connected to Sendspin server as visualizer client")

            # Keep alive until stopped — entertainment mode starts on first viz frame
            while self._sendspin_client and self._sendspin_client.connected:
                await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            pass
        except Exception as err:
            self.logger.error("Sendspin client error: %s", err)
        finally:
            await self._stop_entertainment()

    async def _start_entertainment(self) -> None:
        """Activate entertainment mode and open the Hue stream, with retry."""
        hue_api = self.provider.hue_api
        if hue_api is None:
            self._entertainment_starting = False
            return

        # idle_timeout=0: teardown is driven by the Sendspin stream start/end
        # events below, not by the session's own inactivity monitor. The session
        # stops any other active area on start (the bridge only allows one).
        # Only pass color_mode when the library supports it (ColorMode present); with an
        # older library the session keeps its default (RGB), which is the legacy path.
        # The "Boost colours" toggle picks vivid (on) or colour-accurate xy (off).
        session_kwargs: dict[str, Any] = {"idle_timeout": 0}
        if ColorMode is not None:
            boost = self.provider.config.get_value(CONF_COLOR_BOOST)
            boost = DEFAULT_COLOR_BOOST if boost is None else bool(boost)
            session_kwargs["color_mode"] = ColorMode("vivid" if boost else "xy")
        session = EntertainmentSession(
            hue_api.host,
            str(self.provider.config.get_value(CONF_USERNAME) or ""),
            str(self.provider.config.get_value(CONF_CLIENTKEY) or ""),
            **session_kwargs,
        )
        # The session is only handed off to self._session once it is streaming;
        # until then it is closed in the finally so a failed - or cancelled -
        # start never leaks the DTLS sender thread or leaves the bridge's
        # entertainment stream active.
        adopted = False
        try:
            await self._clear_stale_entertainment(hue_api)
            for attempt in range(_ENTERTAINMENT_START_ATTEMPTS):
                try:
                    await session.start(self.area.id)
                    self._session = session
                    adopted = True
                    self._is_streaming = True
                    self._start_render_loop()
                    self.logger.info("Entertainment streaming active for area '%s'", self.area.name)
                    return
                except Exception as err:
                    self.logger.warning(
                        "Entertainment start attempt %d failed for '%s': %s",
                        attempt + 1,
                        self.area.name,
                        err,
                    )
                    if attempt + 1 < _ENTERTAINMENT_START_ATTEMPTS:
                        await asyncio.sleep(_ENTERTAINMENT_START_BACKOFF_S)

            self.logger.error(
                "Failed to start entertainment for '%s' after %d attempts",
                self.area.name,
                _ENTERTAINMENT_START_ATTEMPTS,
            )
        finally:
            self._entertainment_starting = False
            if not adopted:
                await session.aclose()

    async def _stop_entertainment(self) -> None:
        """Stop the Hue stream and deactivate entertainment mode."""
        self._is_streaming = False
        self._entertainment_starting = False
        self._cancel_render_loop()
        if self._analyzer is not None:
            self._analyzer.clear_beats()
        if self._session is not None:
            with suppress(Exception):
                await self._session.aclose()
            self._session = None

    def _on_stream_start(self, message: object) -> None:
        """Handle stream start — start entertainment mode + DTLS proactively."""
        # Cancel any pending stop from a previous stream end (track transition)
        if self._stop_debounce_task and not self._stop_debounce_task.done():
            self._stop_debounce_task.cancel()
            self._stop_debounce_task = None
        if not self._is_streaming and not self._entertainment_starting:
            self._entertainment_starting = True
            self.logger.info("Stream starting for area '%s', connecting DTLS...", self.area.name)
            self._start_task = self.mass.create_task(self._start_entertainment())

    def _on_stream_end(self, roles: list[str] | None) -> None:
        """Handle stream end — debounce to survive track transitions."""
        if not (roles and "visualizer" in roles):
            return
        # Also act while a start is still in flight: the stream can end before
        # session.start() completes, and that late start would otherwise adopt a
        # session that streams forever (idle_timeout=0).
        starting = self._start_task is not None and not self._start_task.done()
        if self._is_streaming or starting:
            if self._stop_debounce_task and not self._stop_debounce_task.done():
                self._stop_debounce_task.cancel()
            self._stop_debounce_task = self.mass.create_task(self._debounced_stop())

    async def _debounced_stop(self) -> None:
        """Wait out the stream-end grace before stopping — a new stream may start (track change)."""
        await asyncio.sleep(_STREAM_END_GRACE_S)
        # Cancel a still-running start first (its finally closes the not-yet-adopted
        # session); if the start already completed, tear the live session down.
        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._start_task
        if self._is_streaming:
            self.logger.info("Visualizer stream ended for area '%s'", self.area.name)
            await self._stop_entertainment()

    def _on_visualizer_frames(self, frames: list[VisualizerFrame]) -> None:
        """
        Forward periodic samples, onset peaks, and beat events to the analyzer.

        Frames carrying ``is_downbeat`` are beats. The rest carry spectrum and peak data.
        """
        if not self._is_streaming or self._analyzer is None:
            return
        beats: list[BeatTiming] = []
        for frame in frames:
            if frame.is_downbeat is not None:
                beats.append(
                    BeatTiming(timestamp_us=frame.timestamp_us, is_downbeat=frame.is_downbeat)
                )
                continue
            if frame.spectrum is not None:
                self._analyzer.apply_spectrum(frame.spectrum, frame.timestamp_us)
            if frame.peak_strength is not None:
                self._analyzer.apply_peak(frame.peak_strength, frame.timestamp_us)
        if beats:
            self._analyzer.push_beats(beats)

    def _on_color(self, payload: ServerStatePayload) -> None:
        """Forward color palette updates from the Sendspin server to the analyzer."""
        if self._analyzer is None or payload.color is None:
            return
        update: dict[str, tuple[int, int, int] | None] = {}
        for name in (
            "background_dark",
            "background_light",
            "primary",
            "accent",
            "on_dark",
            "on_light",
        ):
            value = getattr(payload.color, name)
            if isinstance(value, UndefinedField):
                continue
            update[name] = value
        if update:
            self._analyzer.apply_color_palette(update)

    # -- Render loop --

    def _start_render_loop(self) -> None:
        """Begin the fixed-rate render+send loop."""
        if self._render_handle is not None:
            return
        self._render_handle = self.mass.loop.call_later(_RENDER_PERIOD_S, self._render_tick)

    def _cancel_render_loop(self) -> None:
        """Cancel the fixed-rate render+send loop."""
        if self._render_handle is not None:
            self._render_handle.cancel()
            self._render_handle = None
        self._last_tick_time = 0.0  # a fresh start must not report the pause as a stall

    def _render_tick(self) -> None:
        """One render+send iteration, then reschedule while streaming."""
        self._render_handle = None
        now = self.mass.loop.time()
        if self._last_tick_time and (gap := now - self._last_tick_time) > _RENDER_STALL_WARN_S:
            self.logger.warning(
                "Hue render loop stalled %.0f ms for area '%s' (period %.0f ms) - "
                "event loop was blocked",
                gap * 1000,
                self.area.name,
                _RENDER_PERIOD_S * 1000,
            )
        self._last_tick_time = now
        if not self._is_streaming:
            return
        try:
            # Skip this tick when not ready (client/DTLS down) but keep the loop
            # alive so it recovers once the connection is back.
            if (
                self._analyzer is not None
                and self._sendspin_client is not None
                and self._session is not None
                and self._session.is_streaming
            ):
                client_now = int(self.mass.loop.time() * 1_000_000)
                # Render slightly ahead of the playhead to compensate for Hue+DTLS lag.
                server_now = self._sendspin_client.compute_server_time(
                    client_now + self._hue_latency_us
                )
                commands = self._analyzer.render(server_now)
                if commands:
                    self._session.send(commands)
                    # Tee the exact wire frame to the live browser preview.
                    self.provider.preview_publish(self.area.id, commands)
        except Exception:
            # One bad tick must not kill the loop: log and reschedule below.
            self.logger.exception("Hue render tick failed for area '%s'", self.area.name)
        finally:
            if self._is_streaming:
                self._render_handle = self.mass.loop.call_later(_RENDER_PERIOD_S, self._render_tick)

    async def _clear_stale_entertainment(self, hue_api: HueEntertainmentAPI) -> None:
        """
        Stop entertainment left active on the bridge from a prior failed handshake.

        :param hue_api: Authenticated Hue REST client for this bridge.
        """
        try:
            status, _rid = await hue_api.get_entertainment_status(self.area.id)
        except Exception:
            return
        if status != "active":
            return
        self.logger.info(
            "Entertainment area '%s' still active on bridge, clearing before DTLS",
            self.area.name,
        )
        await hue_api.stop_entertainment(self.area.id)
        await asyncio.sleep(_ENTERTAINMENT_STALE_COOLDOWN_S)


class HueEntertainmentBridgeManager:
    """Manages Hue Entertainment bridges for all entertainment areas."""

    def __init__(self, provider: HueEntertainmentProvider) -> None:
        """Initialize the bridge manager."""
        self.provider = provider
        self.mass = provider.mass
        self.logger = LOGGER.getChild("bridge_manager")
        self._bridges: dict[str, HueEntertainmentBridge] = {}

    @property
    def areas(self) -> list[EntertainmentArea]:
        """Entertainment areas that currently have a live bridge."""
        return [bridge.area for bridge in self._bridges.values()]

    async def setup_bridges(self, areas: list[EntertainmentArea]) -> None:
        """Set up bridges for all entertainment areas."""
        # Remove bridges for areas that no longer exist
        current_ids = {area.id for area in areas}
        for area_id in list(self._bridges.keys()):
            if area_id not in current_ids:
                bridge = self._bridges.pop(area_id)
                with suppress(Exception):
                    await bridge.stop()

        for area in areas:
            if area.id in self._bridges:
                continue
            if not area.channels:
                continue

            bridge = HueEntertainmentBridge(self.provider, area)
            try:
                await bridge.start()
            except Exception:
                self.logger.warning("Failed to start bridge for area '%s'", area.name)
                with suppress(Exception):
                    await bridge.stop()
                continue

            self._bridges[area.id] = bridge
            self.logger.info("Bridge created for Hue area '%s'", area.name)

    def update_settings(
        self,
        color_mode: str | None = None,
        brightness: int | None = None,
        hue_latency_ms: int | None = None,
        strobe_selection: object = None,
        strobe: StrobeSettings | None = None,
        palette: str | None = None,
        per_light_data: object = None,
        pulse: PulseSettings | None = None,
    ) -> None:
        """Update settings on all bridges."""
        for bridge in self._bridges.values():
            bridge.update_settings(
                color_mode=color_mode,
                brightness=brightness,
                hue_latency_ms=hue_latency_ms,
                strobe_selection=strobe_selection,
                strobe=strobe,
                palette=palette,
                per_light_data=per_light_data,
                pulse=pulse,
            )

    def set_rotation(
        self, enabled: bool, names: list[str], beats: int, smooth: bool = False
    ) -> None:
        """Configure bar-aligned palette rotation on all bridges."""
        for bridge in self._bridges.values():
            bridge.set_rotation(enabled, names, beats, smooth)

    async def stop_all(self) -> None:
        """Stop all bridges."""
        for bridge in list(self._bridges.values()):
            with suppress(Exception):
                await bridge.stop()
        self._bridges.clear()
