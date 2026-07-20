"""
hue-box M0 - standalone "Sendspin null player" that drives Hue Entertainment.

Connects to a Music Assistant server as a Sendspin visualizer client (the
server does all audio analysis and streams tiny feature frames), feeds the
frames into the VERBATIM copy of the MA hue_entertainment effects engine
(effects/hue_fx - synced by tools/sync_effects.py, never edited here), and
streams the rendered colors to the Hue bridge over DTLS.

This file is a faithful port of the provider's bridge.py with Music Assistant
removed: same hello, same frame fan-in, same 30 Hz render loop, same
stream-end debounce. Requires Python 3.14+ (the effects files use PEP 758).

Usage:
    python hue_box_main.py ../config/hue-box.toml
"""

from __future__ import annotations

import asyncio
import logging
import sys
import tomllib
from contextlib import suppress
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "effects"))

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
from hue_entertainment import EntertainmentArea, EntertainmentSession, HueEntertainmentAPI

from hue_fx.analyzer import HueAudioAnalyzer, PulseSettings
from hue_fx.constants import (
    SPECTRUM_BINS,
    SPECTRUM_F_MAX,
    SPECTRUM_F_MIN,
    SPECTRUM_SCALE,
)
from hue_fx.strobe_overlay import StrobeSettings, _hex_to_rgb

LOGGER = logging.getLogger("hue_box")

# Constants mirrored from the provider's bridge.py.
_RENDER_RATE_HZ = 30
_RENDER_PERIOD_S = 1.0 / _RENDER_RATE_HZ
_VISUALIZER_RATE_HZ = 20
_ENTERTAINMENT_START_ATTEMPTS = 6
_ENTERTAINMENT_START_BACKOFF_S = 1.5
_ENTERTAINMENT_STALE_COOLDOWN_S = 1.0
_STREAM_END_GRACE_S = 20.0


def load_config(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def strobe_settings_from_toml(cfg: dict[str, Any]) -> StrobeSettings:
    """Build StrobeSettings from the [strobe] table (same fields as the MA config)."""
    s = cfg.get("strobe", {})
    defaults = StrobeSettings()
    return StrobeSettings(
        enabled=bool(s.get("enabled", defaults.enabled)),
        coverage=int(s.get("coverage", defaults.coverage)),
        sensitivity=int(s.get("sensitivity", defaults.sensitivity)),
        blackout=bool(s.get("blackout", defaults.blackout)),
        color=_hex_to_rgb(str(s.get("color", "#FFFFFF"))),
        brightness=int(s.get("brightness", defaults.brightness)),
        flash_hz=float(s.get("flash_hz", defaults.flash_hz)),
        duty=float(s.get("duty_pct", defaults.duty * 100.0)) / 100.0,
        min_hold_ms=int(s.get("min_hold_ms", defaults.min_hold_ms)),
        release_ms=int(s.get("release_ms", defaults.release_ms)),
        beat_sync=bool(s.get("beat_sync", defaults.beat_sync)),
        auto=bool(s.get("auto", defaults.auto)),
    )


def pulse_settings_from_toml(cfg: dict[str, Any]) -> PulseSettings:
    """Build PulseSettings from the [pulse] table (same fields as the MA config)."""
    p = cfg.get("pulse", {})
    defaults = PulseSettings()
    select = str(p.get("select", defaults.select))
    return PulseSettings(
        floor=float(p.get("floor_pct", defaults.floor * 100.0)) / 100.0,
        decay=float(p.get("decay_pct", defaults.decay * 100.0)) / 100.0,
        select=select if select in {"chase", "scatter", "spectrum"} else defaults.select,
        downbeat_all=bool(p.get("downbeat", defaults.downbeat_all)),
    )


class HueBox:
    """One entertainment area: Sendspin client -> effects engine -> Hue DTLS."""

    def __init__(self, cfg: dict[str, Any], area: EntertainmentArea) -> None:
        self.cfg = cfg
        self.area = area
        self.logger = LOGGER.getChild(f"area.{area.name}")
        self.loop = asyncio.get_running_loop()

        effects = cfg.get("effects", {})
        per_light_raw = effects.get("per_light", {})
        per_light = {
            int(cid): max(0.0, min(100.0, float(pct))) / 100.0
            for cid, pct in per_light_raw.items()
        }
        self._analyzer = HueAudioAnalyzer(
            channels=area.channels,
            color_mode=str(effects.get("mode", "smooth")),
            brightness=int(effects.get("brightness", 100)),
            strobe_channel_ids={int(c) for c in cfg.get("strobe", {}).get("lights", [])},
            strobe=strobe_settings_from_toml(cfg),
            palette=str(effects.get("palette", "")),
            per_light=per_light,
            pulse=pulse_settings_from_toml(cfg),
        )
        rotation = effects.get("rotation", {})
        self._analyzer.set_rotation(
            bool(rotation.get("enabled", False)),
            [str(n) for n in rotation.get("list", [])],
            int(rotation.get("beats", 16)),
            bool(rotation.get("smooth", True)),
        )

        self._hue_latency_us = int(cfg.get("sendspin", {}).get("latency_ms", 200)) * 1000
        self._session: EntertainmentSession | None = None
        self._sendspin_client: SendspinClient | None = None
        self._is_streaming = False
        self._entertainment_starting = False
        self._stop_debounce_task: asyncio.Task[None] | None = None
        self._start_task: asyncio.Task[None] | None = None
        self._render_handle: asyncio.TimerHandle | None = None

    async def run(self) -> None:
        """Connect to the Sendspin server and stay connected until cancelled."""
        client_id = f"hue-box-{self.area.id.replace('-', '')[:16]}"
        self._sendspin_client = SendspinClient(
            client_id=client_id,
            client_name=f"hue-box: {self.area.name}",
            roles=[Roles.VISUALIZER, Roles.COLOR],
            device_info=SendspinDeviceInfo(
                manufacturer="Signify",
                product_name="Hue Entertainment Area",
            ),
            visualizer_support=ClientHelloVisualizerSupport(
                buffer_capacity=2048,
                rate_max=_VISUALIZER_RATE_HZ,
                types=["beat", "peak", "spectrum"],
                spectrum=ClientHelloVisualizerSpectrum(
                    n_disp_bins=SPECTRUM_BINS,
                    scale=SPECTRUM_SCALE,
                    f_min=SPECTRUM_F_MIN,
                    f_max=SPECTRUM_F_MAX,
                ),
            ),
        )
        self._sendspin_client.add_visualizer_listener(self._on_visualizer_frames)
        self._sendspin_client.add_color_listener(self._on_color)
        self._sendspin_client.add_stream_start_listener(self._on_stream_start)
        self._sendspin_client.add_stream_end_listener(self._on_stream_end)

        ws_url = str(self.cfg["sendspin"]["url"])
        try:
            await self._sendspin_client.connect(ws_url)
            self.logger.info("Connected to Sendspin server at %s", ws_url)
            while self._sendspin_client and self._sendspin_client.connected:
                await asyncio.sleep(1.0)
            self.logger.warning("Sendspin connection lost")
        except asyncio.CancelledError:
            pass
        finally:
            await self.shutdown()

    async def shutdown(self) -> None:
        """Tear everything down cleanly (idempotent)."""
        self._cancel_render_loop()
        if self._stop_debounce_task and not self._stop_debounce_task.done():
            self._stop_debounce_task.cancel()
        if self._start_task and not self._start_task.done():
            self._start_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._start_task
        self._start_task = None
        await self._stop_entertainment()
        if self._sendspin_client and self._sendspin_client.connected:
            with suppress(Exception):
                await self._sendspin_client.disconnect()
        self._sendspin_client = None

    # -- Entertainment session lifecycle (ported from bridge.py) --

    async def _start_entertainment(self) -> None:
        bridge_cfg = self.cfg["bridge"]
        session = EntertainmentSession(
            str(bridge_cfg["host"]),
            str(bridge_cfg["username"]),
            str(bridge_cfg["clientkey"]),
            idle_timeout=0,
        )
        adopted = False
        try:
            await self._clear_stale_entertainment()
            for attempt in range(_ENTERTAINMENT_START_ATTEMPTS):
                try:
                    await session.start(self.area.id)
                    self._session = session
                    adopted = True
                    self._is_streaming = True
                    self._start_render_loop()
                    self.logger.info("Entertainment streaming active for '%s'", self.area.name)
                    return
                except Exception as err:
                    self.logger.warning(
                        "Entertainment start attempt %d failed: %s", attempt + 1, err
                    )
                    if attempt + 1 < _ENTERTAINMENT_START_ATTEMPTS:
                        await asyncio.sleep(_ENTERTAINMENT_START_BACKOFF_S)
            self.logger.error("Failed to start entertainment for '%s'", self.area.name)
        finally:
            self._entertainment_starting = False
            if not adopted:
                await session.aclose()

    async def _stop_entertainment(self) -> None:
        self._is_streaming = False
        self._entertainment_starting = False
        self._cancel_render_loop()
        self._analyzer.clear_beats()
        if self._session is not None:
            with suppress(Exception):
                await self._session.aclose()
            self._session = None

    async def _clear_stale_entertainment(self) -> None:
        api = HueEntertainmentAPI(
            str(self.cfg["bridge"]["host"]), app_key=str(self.cfg["bridge"]["username"])
        )
        try:
            status, _rid = await api.get_entertainment_status(self.area.id)
            if status == "active":
                self.logger.info("Area still active on bridge, clearing before DTLS")
                await api.stop_entertainment(self.area.id)
                await asyncio.sleep(_ENTERTAINMENT_STALE_COOLDOWN_S)
        except Exception:
            pass
        finally:
            await api.close()

    def _on_stream_start(self, message: object) -> None:
        if self._stop_debounce_task and not self._stop_debounce_task.done():
            self._stop_debounce_task.cancel()
            self._stop_debounce_task = None
        if not self._is_streaming and not self._entertainment_starting:
            self._entertainment_starting = True
            self.logger.info("Stream starting, connecting DTLS...")
            self._start_task = asyncio.get_running_loop().create_task(self._start_entertainment())

    def _on_stream_end(self, roles: list[str] | None) -> None:
        if not (roles and "visualizer" in roles):
            return
        starting = self._start_task is not None and not self._start_task.done()
        if self._is_streaming or starting:
            if self._stop_debounce_task and not self._stop_debounce_task.done():
                self._stop_debounce_task.cancel()
            self._stop_debounce_task = asyncio.get_running_loop().create_task(
                self._debounced_stop()
            )

    async def _debounced_stop(self) -> None:
        await asyncio.sleep(_STREAM_END_GRACE_S)
        if self._start_task is not None and not self._start_task.done():
            self._start_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._start_task
        if self._is_streaming:
            self.logger.info("Visualizer stream ended for '%s'", self.area.name)
            await self._stop_entertainment()

    # -- Feature fan-in (ported from bridge.py) --

    def _on_visualizer_frames(self, frames: list[VisualizerFrame]) -> None:
        if not self._is_streaming:
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
        if payload.color is None:
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

    # -- 30 Hz render loop (ported from bridge.py) --

    def _start_render_loop(self) -> None:
        if self._render_handle is None:
            self._render_handle = self.loop.call_later(_RENDER_PERIOD_S, self._render_tick)

    def _cancel_render_loop(self) -> None:
        if self._render_handle is not None:
            self._render_handle.cancel()
            self._render_handle = None

    def _render_tick(self) -> None:
        self._render_handle = None
        if not self._is_streaming:
            return
        try:
            if (
                self._sendspin_client is not None
                and self._session is not None
                and self._session.is_streaming
            ):
                client_now = int(self.loop.time() * 1_000_000)
                server_now = self._sendspin_client.compute_server_time(
                    client_now + self._hue_latency_us
                )
                commands = self._analyzer.render(server_now)
                if commands:
                    self._session.send(commands)
        except Exception:
            self.logger.exception("Render tick failed")
        finally:
            if self._is_streaming:
                self._render_handle = self.loop.call_later(_RENDER_PERIOD_S, self._render_tick)


async def resolve_area(cfg: dict[str, Any]) -> EntertainmentArea:
    """Look up the configured entertainment area (by id or name) on the Hue bridge."""
    bridge_cfg = cfg["bridge"]
    api = HueEntertainmentAPI(str(bridge_cfg["host"]), app_key=str(bridge_cfg["username"]))
    try:
        areas = await api.get_entertainment_areas()
    finally:
        await api.close()
    if not areas:
        raise SystemExit("No entertainment areas found on the Hue bridge")
    wanted = str(bridge_cfg.get("area", "")).strip().lower()
    if not wanted:
        return areas[0]
    for area in areas:
        if wanted in (area.id.lower(), area.name.lower()):
            return area
    names = ", ".join(f"'{a.name}'" for a in areas)
    raise SystemExit(f"Area '{bridge_cfg['area']}' not found; bridge has: {names}")


async def amain(config_path: Path) -> None:
    cfg = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, str(cfg.get("log", {}).get("level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    area = await resolve_area(cfg)
    LOGGER.info("Using area '%s' (%d channels)", area.name, len(area.channels))
    box = HueBox(cfg, area)

    run_task = asyncio.get_running_loop().create_task(box.run())
    with suppress(NotImplementedError):  # signal handlers are unavailable on Windows
        import signal

        for sig in (signal.SIGINT, signal.SIGTERM):
            asyncio.get_running_loop().add_signal_handler(sig, run_task.cancel)
    with suppress(asyncio.CancelledError):
        await run_task


def main() -> None:
    default_cfg = Path(__file__).resolve().parents[1] / "config" / "hue-box.toml"
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else default_cfg
    if not config_path.is_file():
        raise SystemExit(
            f"Config not found: {config_path}\n"
            "Copy config/hue-box.example.toml to config/hue-box.toml and fill it in."
        )
    try:
        asyncio.run(amain(config_path))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
