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

import time
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from aiohttp import web

from hue_fx.constants import COLOR_MODES
from hue_fx.palettes import palette_names

if TYPE_CHECKING:
    import tth_phase2

RESOURCES = Path(__file__).resolve().parents[1] / "resources"


def _persist_effects(config_path: Path, changes: dict[str, Any]) -> None:
    """Write changed [effects] keys into hue-box.toml, preserving everything else."""
    if not config_path.exists():
        return
    keys = {k: v for k, v in changes.items() if v is not None}
    if not keys:
        return
    out: list[str] = []
    in_effects = False
    written: set[str] = set()
    for line in config_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            if in_effects:
                out.extend(_toml_line(k, keys[k]) for k in keys if k not in written)
                written.update(keys)
            in_effects = stripped == "[effects]"
        elif in_effects and "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in keys:
                out.append(_toml_line(key, keys[key]))
                written.add(key)
                continue
        out.append(line)
    if in_effects:
        out.extend(_toml_line(k, keys[k]) for k in keys if k not in written)
    config_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _toml_line(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return f"{key} = {'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key} = {value}"
    return f'{key} = "{value}"'


def _bridge_paired(config_path: Path) -> tuple[bool, str, str]:
    """Return (paired, host, area) from the toml without exposing secrets."""
    try:
        d = tomllib.load(config_path.open("rb"))
    except Exception:  # noqa: BLE001
        return (False, "", "")
    b = d.get("bridge", {})
    paired = bool(b.get("username") and b.get("clientkey"))
    return (paired, str(b.get("host", "")), str(b.get("area", "")))


def create_app(
    daemon: "tth_phase2.Phase2Daemon",
    config_path: Path,
    do_pair: Callable,
) -> web.Application:
    """Build the aiohttp app wired to a running daemon."""
    app = web.Application()

    async def index(_req: web.Request) -> web.StreamResponse:
        html = (RESOURCES / "webui.html").read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")

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
                "renders": s.renders,
                "lit": s.lit,
                "format": s.format,
                "spectrum": s.last_spectrum,
                "output": daemon.output,
                "streaming": daemon.session is not None,
                "area": daemon.area_name,
                "receiving": receiving,
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
            }
        )

    # Pairing runs in the BACKGROUND: do_pair holds for up to 30s waiting for the
    # bridge button, and a request held that long is fragile behind the QNAP
    # app-proxy (it mangles the response). So POST /api/pair returns at once and
    # the browser polls /api/pair-status for the result.
    import asyncio

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
        data = await req.json()
        mode = data.get("mode")
        palette = data.get("palette")
        brightness = data.get("brightness")
        if brightness is not None:
            brightness = int(brightness)
        # apply live to the running engine
        daemon.analyzer.update_settings(
            color_mode=mode if mode in COLOR_MODES else None,
            brightness=brightness,
            palette=palette,
        )
        # persist so a restart keeps them
        _persist_effects(config_path, {"mode": mode, "brightness": brightness, "palette": palette})
        return web.json_response({"ok": True})

    app.add_routes(
        [
            web.get("/", index),
            web.get("/api/status", status),
            web.get("/api/config", config),
            web.post("/api/pair", pair),
            web.get("/api/pair-status", pair_status),
            web.post("/api/output", output),
            web.post("/api/settings", settings),
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
