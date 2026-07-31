"""
TuneThatHue daemon WebUI - a tiny aiohttp browser panel for a headless box.

Serves a single self-contained page plus a small JSON API:
  GET  /                -> the panel (resources/webui.html)
  GET  /api/status      -> live counters + spectrum (browser computes rates)
  GET  /api/config      -> current settings + the option lists for the dropdowns
  POST /api/pair        -> {host} run pairing (user presses the bridge button)
  POST /api/settings    -> {mode,brightness,palette} apply live + persist to toml

No framework, no build step; the page is plain HTML/JS. Runs alongside the VBAN
receiver and render loop in the same event loop.
"""

from __future__ import annotations

import asyncio
import json
import time
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from aiohttp import web

from hue_fx.constants import COLOR_MODES, PALETTE_ALBUM_COLORS
from hue_fx.palettes import palette_names, palettes_with_colors
from ma_schema import load_schema

if TYPE_CHECKING:
    import tth_phase2

RESOURCES = Path(__file__).resolve().parents[1] / "resources"

# The senders this build can hand out, if they were bundled at package time.
DOWNLOADS = {
    "soundrecorder": "TuneThatHue-SoundRecorder-setup.exe",
    "winamp": "TuneThatHue-Winamp-plugin-setup.exe",
    "foobar": "TuneThatHue-foobar2000-setup.exe",
}


def _persist_effects(config_path: Path, changes: dict[str, Any]) -> None:
    """Write changed [effects] keys into hue-box.toml, preserving everything else."""
    _persist_section(config_path, "effects", changes)


def _persist_section(config_path: Path, section: str, changes: dict[str, Any]) -> None:
    """
    Write changed keys into one table of hue-box.toml, preserving everything else.

    Line-based on purpose: the file is hand-edited and commented, and rewriting it from
    a parsed structure would throw all of that away. A table that does not exist yet is
    appended.
    """
    if not config_path.exists():
        return
    keys = {k: v for k, v in changes.items() if v is not None}
    if not keys:
        return
    header = f"[{section}]"
    out: list[str] = []
    in_section = False
    seen_section = False
    written: set[str] = set()
    for line in config_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            if in_section:
                out.extend(_toml_line(k, keys[k]) for k in keys if k not in written)
                written.update(keys)
            # The shipped file annotates its tables ("[pulse]  # drives ..."), so compare
            # the header only. Matching the whole line appended a duplicate table, which
            # makes the file unparseable.
            in_section = stripped.split("#", 1)[0].strip() == header
            seen_section = seen_section or in_section
        elif in_section and "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in keys:
                out.append(_toml_line(key, keys[key]))
                written.add(key)
                continue
        out.append(line)
    if in_section:
        out.extend(_toml_line(k, keys[k]) for k in keys if k not in written)
    elif not seen_section:
        out.extend(["", header, *(_toml_line(k, keys[k]) for k in keys)])
    config_path.write_text("\n".join(out) + "\n", encoding="utf-8")


# Which toml table each provider setting lives in, and its key inside that table.
# The provider's CONF_* name is what the UI and the engine speak; the file keeps the
# shorter spelling it always had.
_SETTING_MAP: dict[str, tuple[str, str]] = {
    "hue_latency_ms": ("sendspin", "latency_ms"),
    # Our own settings, not the provider's: the network-device input.
    "sendspin_device": ("sendspin", "device"),
    "sendspin_device_name": ("sendspin", "device_name"),
    "snapcast_enabled": ("snapcast", "enabled"),
    "snapcast_host": ("snapcast", "host"),
    "snapcast_name": ("snapcast", "name"),
    "slimproto_enabled": ("slimproto", "enabled"),
    "slimproto_host": ("slimproto", "host"),
    "slimproto_name": ("slimproto", "name"),
    "dlna_enabled": ("dlna", "enabled"),
    "dlna_name": ("dlna", "name"),
    "wled_enabled": ("wled", "enabled"),
    "wled_host": ("wled", "host"),
    "wled_pixels": ("wled", "pixels"),
    "palette_rotate": ("effects.rotation", "enabled"),
    "palette_rotate_list": ("effects.rotation", "list"),
    "palette_rotate_beats": ("effects.rotation", "beats"),
    "palette_rotate_smooth": ("effects.rotation", "smooth"),
    "color_boost": ("effects", "color_boost"),
    "color_mode": ("effects", "mode"),
    "brightness": ("effects", "brightness"),
    "palette": ("effects", "palette"),
    "strobe_coverage": ("strobe", "coverage"),
    "strobe_sensitivity": ("strobe", "sensitivity"),
    "strobe_auto": ("strobe", "auto"),
    "strobe_blackout": ("strobe", "blackout"),
    "strobe_color": ("strobe", "color"),
    "strobe_brightness": ("strobe", "brightness"),
    "strobe_enabled": ("strobe", "enabled"),
    "strobe_flash_hz": ("strobe", "flash_hz"),
    "strobe_duty": ("strobe", "duty_pct"),
    "strobe_min_hold_ms": ("strobe", "min_hold_ms"),
    "strobe_release_ms": ("strobe", "release_ms"),
    "strobe_beat_sync": ("strobe", "beat_sync"),
    "pulse_select": ("pulse", "select"),
    "pulse_downbeat": ("pulse", "downbeat"),
    # The file spells the two percentage knobs with a _pct suffix.
    "pulse_decay": ("pulse", "decay_pct"),
    "pulse_floor": ("pulse", "floor_pct"),
    "strobe_lights": ("strobe", "lights"),
}



def _schema_with_palettes() -> list[dict[str, Any]]:
    """
    The provider's schema, with the palette list filled in.

    MA builds the palette options from ``palette_names()`` at runtime, which the static
    parse cannot see, so the same call fills them here.
    """
    schema = load_schema()
    for entry in schema:
        if entry["key"] == "palette":
            album = entry["options"][0] if entry["options"] else {
                "value": PALETTE_ALBUM_COLORS, "title": "Music / album colours"
            }
            entry["options"] = [album] + [{"value": n, "title": n} for n in palette_names()]
    return schema


def _toml_line(key: str, value: Any) -> str:
    if isinstance(value, (list, tuple)):
        inner = ", ".join(
            f'"{v}"' if isinstance(v, str) else str(v).lower() if isinstance(v, bool) else str(v)
            for v in value
        )
        return f"{key} = [{inner}]"
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key} = {value}"
    return f'{key} = "{value}"'


async def _apply_sendspin_device(daemon: "tth_phase2.Phase2Daemon") -> None:
    """
    Start, stop or rename the network-device input to match the config.

    Turning it on has to take effect now rather than at the next restart, because the
    whole point is that the box appears on the network while you are looking for it.
    """
    wanted = bool(daemon.cfg.get_value("sendspin_device"))
    name = str(daemon.cfg.get_value("sendspin_device_name") or "TuneThatHue")
    running = daemon.sendspin
    if running is not None and (not wanted or running.name != name):
        await running.stop()
        daemon.sendspin = None
        running = None
    if wanted and running is None:
        from sendspin_device import SendspinDevice  # noqa: PLC0415 - optional input

        device = SendspinDevice(daemon.analyzer, name, claim=daemon.claim)
        try:
            await device.start()
        except Exception as err:  # noqa: BLE001 - report it, do not take the daemon down
            print(f"[sendspin] could not start device mode: {err}")
            return
        daemon.sendspin = device


async def _apply_snapcast(daemon: "tth_phase2.Phase2Daemon") -> None:
    """Start, stop or re-point the Snapcast input to match the config."""
    host = str(daemon.cfg.get_value("snapcast_host") or "").strip()
    name = str(daemon.cfg.get_value("snapcast_name") or "TuneThatHue")
    wanted = bool(daemon.cfg.get_value("snapcast_enabled")) and bool(host)
    running = daemon.snapcast
    if running is not None and (not wanted or running.host != host or running.name != name):
        await running.stop()
        daemon.snapcast = None
        running = None
    if wanted and running is None:
        from snapcast_client import SnapcastClient  # noqa: PLC0415 - optional input

        client = SnapcastClient(host, daemon.audio_input("snapcast"), name=name)
        await client.start()
        daemon.snapcast = client


async def _apply_slimproto(daemon: "tth_phase2.Phase2Daemon") -> None:
    """Start, stop or re-point the Squeezebox input to match the config."""
    host = str(daemon.cfg.get_value("slimproto_host") or "").strip()
    name = str(daemon.cfg.get_value("slimproto_name") or "TuneThatHue")
    wanted = bool(daemon.cfg.get_value("slimproto_enabled"))
    running = daemon.slimproto
    if running is not None and (
        not wanted or running.configured_host != host or running.name != name
    ):
        await running.stop()
        daemon.slimproto = None
        running = None
    if wanted and running is None:
        from slimproto_player import SlimprotoPlayer  # noqa: PLC0415 - optional input

        player = SlimprotoPlayer(daemon.audio_input("slimproto"), name=name, host=host)
        await player.start()
        daemon.slimproto = player


async def _apply_dlna(daemon: "tth_phase2.Phase2Daemon") -> None:
    """Start, stop or rename the DLNA renderer to match the config."""
    name = str(daemon.cfg.get_value("dlna_name") or "TuneThatHue")
    wanted = bool(daemon.cfg.get_value("dlna_enabled"))
    running = daemon.dlna
    if running is not None and (not wanted or running.name != name):
        await running.stop()
        daemon.dlna = None
        running = None
    if wanted and running is None:
        from dlna_renderer import DlnaRenderer  # noqa: PLC0415 - optional input

        port = int(float(str(daemon.cfg.get_value("dlna_port") or 8930)))
        renderer = DlnaRenderer(daemon.audio_input("dlna"), name=name, port=port)
        try:
            await renderer.start()
        except Exception as err:  # noqa: BLE001 - a busy port must not take us down
            print(f"[dlna] could not start: {err}")
            return
        daemon.dlna = renderer


def _apply_wled(daemon: "tth_phase2.Phase2Daemon") -> None:
    """Start, stop or re-point the WLED output to match the config."""
    host = str(daemon.cfg.get_value("wled_host") or "").strip()
    pixels = int(float(str(daemon.cfg.get_value("wled_pixels") or 60)))
    wanted = bool(daemon.cfg.get_value("wled_enabled")) and bool(host)
    running = daemon.wled
    if running is not None and (not wanted or running.host != host or running.pixels != pixels):
        running.stop()
        daemon.wled = None
        running = None
    if wanted and running is None:
        from wled_output import WledOutput  # noqa: PLC0415 - optional output

        device = WledOutput(host, pixels)
        device.start()
        daemon.wled = device


def _bridge_paired(config_path: Path) -> tuple[bool, str, str]:
    """Return (paired, host, area) from the toml without exposing secrets."""
    try:
        d = tomllib.load(config_path.open("rb"))
    except Exception:  # noqa: BLE001
        return (False, "", "")
    b = d.get("bridge", {})
    paired = _is_real_credential(b.get("username")) and _is_real_credential(b.get("clientkey"))
    # An unpaired [bridge] is still the shipped example, so its host is boilerplate too:
    # offering it would send the user pairing against an address that isn't their bridge.
    host = str(b.get("host", "")) if paired else ""
    return (paired, host, str(b.get("area", "")))


def _is_real_credential(value: Any) -> bool:
    """Reject the shipped example placeholders, which a first run copies verbatim."""
    return bool(value) and not str(value).startswith("PASTE-")


def create_app(
    daemon: "tth_phase2.Phase2Daemon",
    config_path: Path,
    do_pair: Callable,
) -> web.Application:
    """Build the aiohttp app wired to a running daemon."""
    app = web.Application()

    async def index(_req: web.Request) -> web.StreamResponse:
        html = (RESOURCES / "webui.html").read_text(encoding="utf-8")
        # The panel changes with every deploy, and a cached copy talking to a newer API
        # looks exactly like a broken page. Never let the browser hold on to it.
        return web.Response(
            text=html,
            content_type="text/html",
            headers={"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"},
        )

    async def status(_req: web.Request) -> web.Response:
        s = daemon.stats
        receiving = (time.monotonic() - daemon.last_packet_mono) < 2.0 if daemon.last_packet_mono else False
        return web.json_response(
            {
                "t": time.monotonic(),
                "packets": s.packets,
                "bytes": s.bytes,
                "spectra": s.frames,
                "peaks": s.peaks,
                "beats": s.beats,
                "bpm": round(s.bpm, 1),
                "renders": s.renders,
                "lit": s.lit,
                "format": s.format,
                "spectrum": s.last_spectrum,
                "output": daemon.output,
                "streaming": daemon.session is not None,
                "area": daemon.area_name,
                "receiving": receiving,
                # Stereo peak, for the panel's meters.
                "level_l": round(s.level_l, 4),
                "level_r": round(s.level_r, 4),
                # Which input is currently driving, so the panel can show it.
                "driving": daemon._source,
                # The second input, when it is running: who is connected and whether
                # anything is playing to us.
                "sendspin": daemon.sendspin.status() if daemon.sendspin is not None else None,
                "snapcast": daemon.snapcast.status() if daemon.snapcast is not None else None,
                "slimproto": daemon.slimproto.status() if daemon.slimproto is not None else None,
                "dlna": daemon.dlna.status() if daemon.dlna is not None else None,
                "wled": daemon.wled.status() if daemon.wled is not None else None,
            }
        )

    async def config(_req: web.Request) -> web.Response:
        paired, host, cfg_area = _bridge_paired(config_path)
        try:
            d = tomllib.load(config_path.open("rb"))
        except Exception:  # noqa: BLE001
            d = {}
        eff = d.get("effects", {})
        areas = []
        if paired:
            import tth_phase2  # noqa: PLC0415 - already loaded (webui is imported by it)

            areas = await tth_phase2.list_areas(config_path)
        return web.json_response(
            {
                "paired": paired,
                "host": host,
                "area": daemon.area_name or cfg_area,
                "areas": areas,
                "output": daemon.output,
                "mode": eff.get("mode", "pulse"),
                "brightness": eff.get("brightness", 100),
                "palette": eff.get("palette", "Disco"),
                "modes": list(COLOR_MODES),
                "palettes": palette_names(),
                # Name -> swatch colours, so the palette board can show what each one
                # actually looks like instead of a dropdown of names.
                "palette_colors": palettes_with_colors(),
                # Every provider setting the engine understands, under its CONF_* name,
                # so the panel can render the full set instead of a hand-picked three.
                "settings": daemon.cfg.as_dict(),
                # The settings screen is generated from the Music Assistant provider
                # sources we carry, so the two screens name and order everything alike.
                "schema": _schema_with_palettes(),
                # The area's real lights, so the VFX picker offers them by name instead
                # of asking for a list of channel numbers.
                "lights": daemon.lights,
            }
        )

    # Pairing runs in the BACKGROUND: do_pair holds for up to 30s waiting for the
    # bridge button, and a request held that long is fragile behind the QNAP
    # app-proxy (it mangles the response). So POST /api/pair returns at once and
    # the browser polls /api/pair-status for the result.
    pair_state: dict = {"running": False, "result": None}

    async def pair(req: web.Request) -> web.Response:
        if pair_state["running"]:
            return web.json_response({"state": "running"})
        data = await req.json()
        host = (data.get("host") or "").strip() or None
        pair_state["running"] = True
        pair_state["result"] = None

        async def _run() -> None:
            try:
                pair_state["result"] = await do_pair(host, config_path)
            except Exception as err:  # noqa: BLE001
                pair_state["result"] = {"ok": False, "error": str(err)}
            finally:
                pair_state["running"] = False

        asyncio.ensure_future(_run())
        return web.json_response({"state": "started"})

    async def pair_status(_req: web.Request) -> web.Response:
        return web.json_response(
            {"running": pair_state["running"], "result": pair_state["result"]}
        )

    async def output(req: web.Request) -> web.Response:
        data = await req.json()
        mode = "hue" if data.get("mode") == "hue" else "none"
        area = (data.get("area") or "").strip() or None
        result = await daemon.apply_output(mode, area)
        return web.json_response(result)

    async def settings(req: web.Request) -> web.Response:
        """
        Apply any subset of the provider's settings, live, and persist them.

        The body is keyed by the provider's own CONF_* names. Each is written to its
        table in hue-box.toml and the whole file is then re-applied through the engine's
        own from_config path, so one code path handles every setting and the result is
        exactly what Music Assistant would do with the same values.
        """
        data = await req.json()
        # "mode" is what the old UI sent; accept it as color_mode.
        if "mode" in data and "color_mode" not in data:
            data["color_mode"] = data.pop("mode")
        if data.get("color_mode") not in COLOR_MODES:
            data.pop("color_mode", None)

        by_section: dict[str, dict[str, Any]] = {}
        unknown = []
        for key, value in data.items():
            target = _SETTING_MAP.get(key)
            if target is None:
                unknown.append(key)
                continue
            section, name = target
            by_section.setdefault(section, {})[name] = value
        for section, changes in by_section.items():
            _persist_section(config_path, section, changes)

        daemon.apply_config()
        if "sendspin_device" in data or "sendspin_device_name" in data:
            await _apply_sendspin_device(daemon)
        if any(k.startswith("snapcast_") for k in data):
            await _apply_snapcast(daemon)
        if any(k.startswith("slimproto_") for k in data):
            await _apply_slimproto(daemon)
        if any(k.startswith("dlna_") for k in data):
            await _apply_dlna(daemon)
        if any(k.startswith("wled_") for k in data):
            _apply_wled(daemon)
        return web.json_response(
            {"ok": True, "applied": sorted(k for k in data if k not in unknown), "ignored": unknown}
        )

    async def preview_stream(request: web.Request) -> web.StreamResponse:
        """
        Stream the frames the bridge is being sent, as server-sent events.

        The first event carries the light list so the page can lay out its tiles before
        any frame arrives; after that it is one event per rendered frame.
        """
        resp = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-store",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
        await resp.prepare(request)
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=4)
        daemon._preview_subs.add(queue)
        try:
            meta = json.dumps({"lights": daemon.lights, "area": daemon.area_name})
            await resp.write(f"event: meta\ndata: {meta}\n\n".encode())
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    # Keeps the panel proxy from closing an idle stream.
                    await resp.write(b": keep-alive\n\n")
                    continue
                await resp.write(f"data: {payload}\n\n".encode())
        except (asyncio.CancelledError, ConnectionResetError, RuntimeError):
            pass
        finally:
            daemon._preview_subs.discard(queue)
        return resp

    async def download(req: web.Request) -> web.StreamResponse:
        """Serve one of the bundled sender installers."""
        name = req.match_info.get("name", "")
        target = DOWNLOADS.get(name)
        path = (RESOURCES / "downloads" / target) if target else None
        if path is None or not path.is_file():
            raise web.HTTPNotFound(text="not bundled in this build")
        return web.FileResponse(
            path, headers={"Content-Disposition": f'attachment; filename="{target}"'}
        )

    async def downloads_index(_req: web.Request) -> web.Response:
        """Which senders this build actually ships, so the page only offers real files."""
        here = RESOURCES / "downloads"
        return web.json_response(
            [
                {"id": key, "file": fname, "size": (here / fname).stat().st_size}
                for key, fname in DOWNLOADS.items()
                if (here / fname).is_file()
            ]
        )

    app.add_routes(
        [
            web.get("/", index),
            # The panel's logos. Served from the package so the page needs nothing from
            # the internet - a NAS in a rack may have none.
            web.static("/logos", str(RESOURCES / "logos")),
            web.get("/api/status", status),
            web.get("/api/config", config),
            web.post("/api/pair", pair),
            web.get("/api/pair-status", pair_status),
            web.post("/api/output", output),
            web.post("/api/settings", settings),
            web.get("/api/preview", preview_stream),
            web.get("/api/downloads", downloads_index),
            web.get("/download/{name}", download),
        ]
    )
    return app


async def start_webui(
    daemon: "tth_phase2.Phase2Daemon",
    config_path: Path,
    do_pair: Callable,
    port: int,
) -> web.AppRunner:
    """Start the WebUI server; returns the runner so main() can clean it up."""
    app = create_app(daemon, config_path, do_pair)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=port)
    await site.start()
    print(f"[webui] http://0.0.0.0:{port}  (open in a browser to configure)")
    return runner
