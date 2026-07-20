"""
TuneThatHue Phase 2a - raw-PCM input daemon (Winamp/VBAN -> effects engine).

Receives VBAN/UDP audio (from the dsp_tunethathue Winamp plugin, Voicemeeter,
or any VBAN source), runs Music Assistant's own feature extractor on it
(aiosendspin's VisualizerFeatureExtractor - the exact code the MA server uses),
feeds the features into the VERBATIM hue_fx effects engine, and renders light
frames. No Music Assistant, no Home Assistant anywhere.

Output modes:
  --output none   (default) stats + live spectrum bar in the console - safe,
                  never touches the Hue bridge
  --output hue    stream to the Hue bridge over DTLS (needs config/hue-box.toml
                  with bridge credentials; stops any other entertainment stream)

Pairing (one-time, no secrets read from anywhere - the daemon gets its own key):
  python tth_phase2.py --pair [--host <bridge-ip>]
  -> press the round link button on the Hue bridge; credentials are written to
     config/hue-box.toml. Omit --host to auto-discover the bridge via mDNS.

Usage:
  python tth_phase2.py [--port 6980] [--output none|hue] [--config path.toml]
                       [--webui-port 8080]
  -> a browser panel (live status + pairing button + effect settings) is served
     on --webui-port (default 8080; 0 to disable). Open http://<box-ip>:8080.
"""

from __future__ import annotations

import argparse
import asyncio
import struct
import sys
import time
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "effects"))
# The pystub (LightColorCommand/LightChannel only) is a FALLBACK for the
# stdlib-only M3 build. When the real hue_entertainment lib is installed
# (Phase 2a: bridge pairing + DTLS output) we must prefer it, so only fall
# back to the stub when the real package is genuinely absent.
try:
    import hue_entertainment  # noqa: F401  (real lib from the venv, if installed)
except ModuleNotFoundError:
    sys.path.insert(0, str(BASE / "pystub"))

from aiosendspin.models.visualizer import ClientHelloVisualizerSpectrum, StreamStartVisualizer


def _load_feature_extractor():
    """
    Load aiosendspin's features.py directly, bypassing aiosendspin.server's
    package __init__ (which drags in Pillow/av for roles we never use - that
    matters on the QNAP target where we want minimal deps).
    """
    import importlib.util

    import aiosendspin

    path = (
        Path(aiosendspin.__file__).parent / "server" / "roles" / "visualizer" / "features.py"
    )
    spec = importlib.util.spec_from_file_location("tth_features", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tth_features"] = mod  # dataclasses resolve annotations via sys.modules
    spec.loader.exec_module(mod)
    return mod.VisualizerFeatureExtractor


VisualizerFeatureExtractor = _load_feature_extractor()

from hue_fx.analyzer import HueAudioAnalyzer, PulseSettings
from hue_fx.constants import SPECTRUM_BINS, SPECTRUM_F_MAX, SPECTRUM_F_MIN, SPECTRUM_SCALE
from hue_fx.strobe_overlay import StrobeSettings

VBAN_HDR = struct.Struct("<4sBBBB16sI")
VBAN_SR_TABLE = [
    6000, 12000, 24000, 48000, 96000, 192000, 384000,
    8000, 16000, 32000, 64000, 128000, 256000, 512000,
    11025, 22050, 44100, 88200, 176400, 352800, 705600,
]
FEATURE_RATE_HZ = 20
RENDER_RATE_HZ = 30
RENDER_AHEAD_US = 200_000  # render latency lead, mirrors hue latency default
RESYNC_GAP_S = 2.0


class Stats:
    def __init__(self) -> None:
        self.packets = 0
        self.bytes = 0
        self.frames = 0
        self.peaks = 0
        self.renders = 0
        self.lit = 0
        self.format = "-"
        self.last_spectrum: list[int] = []


class VbanAudio(asyncio.DatagramProtocol):
    """Parse VBAN audio packets into (pcm-bytes, sample_rate, channels) chunks."""

    def __init__(self, on_chunk, stats: Stats) -> None:
        self.on_chunk = on_chunk
        self.stats = stats
        self.transport = None

    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        # Control datagrams for the plugin's "Test connection" button.
        if data[:4] == b"TTHP":
            self.transport.sendto(b"TTHO", addr)
            return
        if len(data) <= VBAN_HDR.size or data[:4] != b"VBAN":
            return
        magic, fmt_sr, fmt_nbs, fmt_nbc, fmt_bit, _name, _nu = VBAN_HDR.unpack_from(data)
        if fmt_sr >> 5 != 0:  # not the audio sub-protocol
            return
        if fmt_bit & 0x07 != 0x01:  # int16 PCM only (what our plugin sends)
            return
        sr_index = fmt_sr & 0x1F
        if sr_index >= len(VBAN_SR_TABLE):
            return
        sample_rate = VBAN_SR_TABLE[sr_index]
        channels = fmt_nbc + 1
        frames = fmt_nbs + 1
        payload = data[VBAN_HDR.size : VBAN_HDR.size + frames * channels * 2]
        if len(payload) < frames * channels * 2:
            return
        self.stats.packets += 1
        self.stats.bytes += len(payload)
        self.stats.format = f"{sample_rate} Hz / {channels}ch / int16"
        self.on_chunk(payload, sample_rate, channels)


class Phase2Daemon:
    def __init__(self, output: str) -> None:
        self.output = output
        self.stats = Stats()
        self.loop = asyncio.get_running_loop()

        channels = [
            SimpleNamespace(
                channel_id=i, name=f"tth-{i}", position=(i * 0.3, 0.0, 0.0), service_id=""
            )
            for i in range(4)
        ]
        self.analyzer = HueAudioAnalyzer(
            channels=channels,
            color_mode="pulse",
            brightness=100,
            strobe_channel_ids=set(),
            strobe=StrobeSettings(),
            palette="Disco",
            per_light={},
            pulse=PulseSettings(),
        )

        self.extractor: VisualizerFeatureExtractor | None = None
        self.ex_rate = 0
        self.ex_channels = 0
        # Sample clock: chunk timestamps derive from a running sample count so
        # network jitter doesn't wobble the analysis timeline.
        self.anchor_us: int | None = None
        self.samples_seen = 0
        self.last_packet_mono = 0.0

        # Runtime-controllable Hue output (togglable from the WebUI, no restart).
        self.session = None
        self.area_name = ""
        self.config_path: Path | None = None

    def now_us(self) -> int:
        return int(self.loop.time() * 1_000_000)

    async def apply_output(self, mode: str, area_name: str | None = None) -> dict:
        """Start/stop the Hue stream at runtime. Returns {ok, output, area, error}."""
        try:
            await self._close_session()
            if mode == "hue":
                if self.config_path is None:
                    return {"ok": False, "error": "no config path"}
                self.session, self.area_name = await open_entertainment(
                    self.config_path, area_name
                )
            self.output = mode
            return {"ok": True, "output": self.output, "area": self.area_name}
        except Exception as err:  # noqa: BLE001 - surface bridge/pairing errors to the UI
            self.output = "none"
            return {"ok": False, "error": str(err)}

    async def _close_session(self) -> None:
        if self.session is not None:
            from contextlib import suppress

            with suppress(Exception):
                await self.session.aclose()
            self.session = None

    def _ensure_extractor(self, sample_rate: int, channels: int) -> None:
        if self.extractor is not None and sample_rate == self.ex_rate and channels == self.ex_channels:
            return
        config = StreamStartVisualizer(
            types=("peak", "spectrum"),
            rate_max=FEATURE_RATE_HZ,
            spectrum=ClientHelloVisualizerSpectrum(
                n_disp_bins=SPECTRUM_BINS,
                scale=SPECTRUM_SCALE,
                f_min=SPECTRUM_F_MIN,
                f_max=SPECTRUM_F_MAX,
            ),
        )
        self.extractor = VisualizerFeatureExtractor(
            sample_rate=sample_rate, channels=channels, config=config
        )
        self.ex_rate, self.ex_channels = sample_rate, channels
        self.anchor_us = None
        self.samples_seen = 0
        print(f"[extractor] ready for {sample_rate} Hz / {channels}ch (MA's own DSP, 1:1)")

    def on_chunk(self, pcm: bytes, sample_rate: int, channels: int) -> None:
        mono_now = time.monotonic()
        self._ensure_extractor(sample_rate, channels)
        assert self.extractor is not None

        # Re-anchor after silence (player stopped) so timestamps stay near "now".
        if self.anchor_us is None or (mono_now - self.last_packet_mono) > RESYNC_GAP_S:
            self.anchor_us = self.now_us()
            self.samples_seen = 0
            self.extractor.reset()
            self.analyzer.clear_beats()
        self.last_packet_mono = mono_now

        frames = len(pcm) // (channels * 2)
        ts = self.anchor_us + self.samples_seen * 1_000_000 // sample_rate
        self.samples_seen += frames

        for frame in self.extractor.process_chunk(pcm, ts):
            if frame.spectrum is not None:
                self.analyzer.apply_spectrum(
                    [int(v) for v in frame.spectrum], frame.timestamp_us
                )
                self.stats.frames += 1
                self.stats.last_spectrum = [int(v) for v in frame.spectrum]
            if frame.peak is not None:
                self.analyzer.apply_peak(
                    max(0, min(255, int(frame.peak))), frame.timestamp_us
                )
                self.stats.peaks += 1

    async def render_loop(self) -> None:
        period = 1.0 / RENDER_RATE_HZ
        while True:
            await asyncio.sleep(period)
            cmds = self.analyzer.render(self.now_us() + RENDER_AHEAD_US)
            self.stats.renders += 1
            if any(c.red or c.green or c.blue for c in cmds):
                self.stats.lit += 1
            if self.session is not None:  # live: set/cleared by apply_output()
                self.session.send(cmds)

    async def stats_loop(self) -> None:
        blocks = " .:-=+*#%@"
        while True:
            await asyncio.sleep(2.0)
            s = self.stats
            bar = "".join(
                blocks[min(len(blocks) - 1, v * len(blocks) // 65536)] for v in s.last_spectrum
            )
            print(
                f"[stats] pkts={s.packets} ({s.bytes // 1024} KB) fmt={s.format} | "
                f"spectra={s.frames} peaks={s.peaks} | renders={s.renders} lit={s.lit} | [{bar}]"
            )


def _bridge_cfg(config_path: Path) -> dict:
    import tomllib

    return tomllib.loads(config_path.read_text(encoding="utf-8")).get("bridge", {})


async def list_areas(config_path: Path) -> list[str]:
    """Names of the bridge's entertainment areas (for the WebUI dropdown)."""
    from hue_entertainment import HueEntertainmentAPI

    bridge = _bridge_cfg(config_path)
    if not bridge.get("username"):
        return []
    api = HueEntertainmentAPI(str(bridge["host"]), app_key=str(bridge["username"]))
    try:
        return [a.name for a in await api.get_entertainment_areas()]
    except Exception:  # noqa: BLE001
        return []
    finally:
        await api.close()


async def open_entertainment(config_path: Path, area_name: str | None = None):
    """
    Open a Hue Entertainment DTLS stream to the chosen area (or the configured /
    first one). Returns (session, area_name_used). Raises on missing bridge/area.
    """
    from hue_entertainment import EntertainmentSession, HueEntertainmentAPI

    bridge = _bridge_cfg(config_path)
    if not bridge.get("username"):
        raise RuntimeError("bridge not paired - pair first (--pair or the WebUI)")
    api = HueEntertainmentAPI(str(bridge["host"]), app_key=str(bridge["username"]))
    try:
        areas = await api.get_entertainment_areas()
    finally:
        await api.close()
    wanted = (area_name or str(bridge.get("area", ""))).strip().lower()
    area = next(
        (a for a in areas if wanted in (a.id.lower(), a.name.lower())),
        areas[0] if areas else None,
    )
    if area is None:
        raise RuntimeError("no entertainment area found on the bridge")
    session = EntertainmentSession(
        str(bridge["host"]), str(bridge["username"]), str(bridge["clientkey"]), idle_timeout=0
    )
    await session.start(area.id)
    print(f"[hue] streaming to area '{area.name}'")
    return session, area.name


def _save_bridge_creds(
    config_path: Path, host: str, username: str, clientkey: str, area: str
) -> None:
    """
    Persist paired bridge credentials into the [bridge] table of hue-box.toml,
    preserving every other setting. Creates the file if it does not exist.
    """
    keys = {"host": host, "username": username, "clientkey": clientkey, "area": area}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        body = "".join(f'{k} = "{v}"\n' for k, v in keys.items())
        config_path.write_text("[bridge]\n" + body, encoding="utf-8")
        return
    out: list[str] = []
    in_bridge = False
    written: set[str] = set()
    for line in config_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            if in_bridge:  # leaving [bridge]: flush any keys we didn't overwrite
                out.extend(f'{k} = "{keys[k]}"' for k in keys if k not in written)
                written.update(keys)
            in_bridge = stripped == "[bridge]"
        elif in_bridge and "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in keys:
                out.append(f'{key} = "{keys[key]}"')
                written.add(key)
                continue
        out.append(line)
    if in_bridge:  # file ended inside [bridge]
        out.extend(f'{k} = "{keys[k]}"' for k in keys if k not in written)
    config_path.write_text("\n".join(out) + "\n", encoding="utf-8")


async def _discover_hue_host(timeout: float = 6.0) -> str | None:
    """
    Find a Hue bridge's IP via mDNS. Uses hue_entertainment's proven discovery
    when the lib is present (it is, for Phase 2a); returns None otherwise so the
    caller falls back to asking for the IP.
    """
    try:
        from hue_entertainment import discover_bridges
    except ModuleNotFoundError:
        return None
    try:
        bridges = await discover_bridges(timeout=timeout)
    except Exception:  # noqa: BLE001 - discovery is best-effort; --host always works
        return None
    return bridges[0].host if bridges else None


async def do_pair(host: str | None, config_path: Path, wait_s: float = 30.0) -> dict:
    """
    Pair with a Hue bridge and store credentials in hue-box.toml. The user must
    press the bridge's round link button. No secrets are read from anywhere else
    - the daemon obtains its own key. Self-contained (aiohttp + zeroconf only).

    Returns a result dict: {"ok", "host", "areas", "area", "error"}. Never raises
    for the normal "button not pressed" case - callers (CLI and WebUI) report it.
    """
    import aiohttp

    if not host:
        host = await _discover_hue_host()
        if not host:
            return {"ok": False, "error": "no bridge found on the LAN - enter the IP manually"}

    body = {"devicetype": "tunethathue#daemon", "generateclientkey": True}
    username = clientkey = None
    error = None
    async with aiohttp.ClientSession() as session:
        deadline = asyncio.get_running_loop().time() + wait_s
        while asyncio.get_running_loop().time() < deadline:
            try:
                async with session.post(f"https://{host}/api", json=body, ssl=False) as resp:
                    result = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                error = f"cannot reach bridge at {host}: {err}"
                await asyncio.sleep(2.0)
                continue
            if isinstance(result, list) and result:
                entry = result[0]
                if "success" in entry:
                    username = entry["success"]["username"]
                    clientkey = entry["success"]["clientkey"]
                    break
                if entry.get("error", {}).get("type") == 101:  # button not pressed yet
                    error = "link button not pressed yet"
                    await asyncio.sleep(2.0)
                    continue
                return {"ok": False, "host": host, "error": str(entry.get("error"))}
            await asyncio.sleep(2.0)
        if not username:
            return {"ok": False, "host": host, "error": error or "timed out waiting for the button"}

        areas: list[str] = []
        try:
            async with session.get(
                f"https://{host}/clip/v2/resource/entertainment_configuration",
                headers={"hue-application-key": username},
                ssl=False,
            ) as resp:
                data = (await resp.json(content_type=None)).get("data", [])
                areas = [d.get("metadata", {}).get("name", "Area") for d in data]
        except Exception:  # noqa: BLE001 - area listing is a best-effort convenience
            pass

    area = areas[0] if areas else ""
    _save_bridge_creds(config_path, host, username, clientkey, area)
    return {"ok": True, "host": host, "areas": areas, "area": area}


async def pair_bridge(host: str | None, config_path: Path) -> None:
    """CLI wrapper around do_pair: print progress + the result for `--pair`."""
    if not host:
        print("[pair] discovering Hue bridges on the LAN (mDNS, 5s) ...", flush=True)
    print("\n[pair] >>> PRESS THE ROUND LINK BUTTON on the Hue bridge NOW <<<", flush=True)
    print("[pair] waiting up to 30s for the button ...", flush=True)
    result = await do_pair(host, config_path)
    if not result["ok"]:
        raise SystemExit(f"pairing failed: {result['error']}")
    print(f"\n[pair] SUCCESS - credentials saved to {config_path}", flush=True)
    areas = result.get("areas") or []
    print(f"[pair] entertainment areas: {', '.join(areas) or '(none - create one in the Hue app)'}")
    print(f"[pair] default area = '{result['area']}'.  Next:  python tth_phase2.py --output hue")


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=6980)
    ap.add_argument("--output", choices=("none", "hue"), default="none")
    ap.add_argument("--config", type=Path, default=BASE / "config" / "hue-box.toml")
    ap.add_argument("--pair", action="store_true", help="pair with a Hue bridge and exit")
    ap.add_argument("--host", help="Hue bridge IP for --pair (else mDNS auto-discovery)")
    ap.add_argument("--webui-port", type=int, default=8080, help="browser panel port (0 = off)")
    args = ap.parse_args()

    if args.pair:
        await pair_bridge(args.host, args.config)
        return

    daemon = Phase2Daemon(args.output)
    daemon.config_path = args.config

    if args.output == "hue":
        result = await daemon.apply_output("hue")
        if not result["ok"]:
            print(f"[hue] could not start output: {result['error']} (continuing; toggle it in the WebUI)")

    webui_runner = None
    if args.webui_port:
        from webui import start_webui  # noqa: PLC0415 - optional, keeps deps lazy

        webui_runner = await start_webui(daemon, args.config, do_pair, args.webui_port)

    transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: VbanAudio(daemon.on_chunk, daemon.stats),
        local_addr=("0.0.0.0", args.port),
    )
    print(f"[vban] listening on UDP {args.port} - start Winamp with the TuneThatHue DSP plugin")

    try:
        await asyncio.gather(daemon.render_loop(), daemon.stats_loop())
    finally:
        transport.close()
        if webui_runner is not None:
            await webui_runner.cleanup()
        await daemon._close_session()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
