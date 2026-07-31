"""
Appear on the network as a UPnP MediaRenderer, so anything that can push to a speaker can
push to the lights.

This is the oldest and most widely implemented of the four inputs: phones, Music
Assistant, Windows, hi-fi apps - if a thing can send audio to a network speaker at all, it
can usually send it to a DLNA renderer. Discovery is SSDP, so nothing has to be told our
address.

A renderer is a device other things on the network will try to talk to, so it answers
properly or not at all:

- M-SEARCH for ssdp:all, upnp:rootdevice, MediaRenderer, AVTransport or RenderingControl,
  plus NOTIFY alive on start and byebye on stop;
- a device description and an SCPD for each service, because a control point fetches them
  before it will call anything;
- the AVTransport actions that matter for pushing a stream (SetAVTransportURI, Play, Stop,
  Pause, GetTransportInfo, GetPositionInfo, GetMediaInfo) and enough of RenderingControl
  that volume queries do not fail;
- GENA SUBSCRIBE, answered with a subscription id, because control points subscribe before
  they play and treat a refusal as a broken device.

**The one prerequisite.** We are handed a URL and play what is there, so it has to be
uncompressed: set the player's output codec to ``wav`` on the server side. Anything else
is reported rather than silently ignored.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import struct
import uuid
from typing import Any, Callable

import aiohttp
from aiohttp import web

from wav_feed import NotWav, feed_wav

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
ALIVE_INTERVAL_S = 300
DEFAULT_PORT = 8930

# What a control point may search for and expect us to answer.
_TARGETS = (
    "ssdp:all",
    "upnp:rootdevice",
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:service:AVTransport:1",
    "urn:schemas-upnp-org:service:RenderingControl:1",
)

_SOAP_NS = "urn:schemas-upnp-org:service:AVTransport:1"
_RENDER_NS = "urn:schemas-upnp-org:service:RenderingControl:1"


class DlnaRenderer:
    """
    A MediaRenderer that lights the room instead of making a sound.

    :param on_chunk: ``(pcm, sample_rate, channels)`` - the daemon's audio entry point.
    :param name: the name shown in whatever is looking for a speaker.
    """

    def __init__(
        self,
        on_chunk: Callable[[bytes, int, int], None],
        *,
        name: str = "TuneThatHue",
        port: int = DEFAULT_PORT,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._on_chunk = on_chunk
        self._on_change = on_change
        self.name = name
        self.port = port
        # Stable across restarts, so a control point sees the same device rather than
        # collecting a new one every time the daemon is restarted.
        self.udn = f"uuid:{uuid.uuid5(uuid.NAMESPACE_DNS, f'tunethathue-{socket.gethostname()}')}"

        self.enabled = False
        self.playing = False
        self.uri = ""
        self.format = ""
        self.bytes_in = 0
        self.error = ""
        self.controller = ""

        self._runner: web.AppRunner | None = None
        self._ssdp: asyncio.DatagramTransport | None = None
        self._alive_task: asyncio.Task[None] | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._ip = _local_ip()

    # -- lifecycle -------------------------------------------------------------------

    async def start(self) -> None:
        if self._runner is not None:
            return
        app = web.Application()
        app.add_routes(
            [
                web.get("/desc.xml", self._desc),
                web.get("/AVTransport.xml", self._scpd_avtransport),
                web.get("/RenderingControl.xml", self._scpd_rendering),
                web.post("/control/AVTransport", self._control_avtransport),
                web.post("/control/RenderingControl", self._control_rendering),
                web.route("SUBSCRIBE", "/event/{service}", self._subscribe),
                web.route("UNSUBSCRIBE", "/event/{service}", self._unsubscribe),
            ]
        )
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()  # noqa: S104
        await self._start_ssdp()
        self._alive_task = asyncio.create_task(self._alive_loop())
        self._set(enabled=True)
        print(f"[dlna] renderer '{self.name}' at http://{self._ip}:{self.port}/desc.xml")

    async def stop(self) -> None:
        await self._stop_stream()
        if self._alive_task is not None:
            self._alive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._alive_task
            self._alive_task = None
        if self._ssdp is not None:
            # Say goodbye, or control points keep a dead device in their list for hours.
            with contextlib.suppress(Exception):
                self._notify("ssdp:byebye")
            self._ssdp.close()
            self._ssdp = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        self._set(enabled=False, playing=False, uri="", format="")

    # -- SSDP -------------------------------------------------------------------------

    async def _start_ssdp(self) -> None:
        loop = asyncio.get_running_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", SSDP_PORT))
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_ADD_MEMBERSHIP,
            struct.pack("4s4s", socket.inet_aton(SSDP_ADDR), socket.inet_aton("0.0.0.0")),
        )
        transport, _ = await loop.create_datagram_endpoint(lambda: _Ssdp(self), sock=sock)
        self._ssdp = transport  # type: ignore[assignment]

    async def _alive_loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                self._notify("ssdp:alive")
            await asyncio.sleep(ALIVE_INTERVAL_S)

    def _notify(self, subtype: str) -> None:
        if self._ssdp is None:
            return
        for target in ("upnp:rootdevice", "urn:schemas-upnp-org:device:MediaRenderer:1"):
            usn = f"{self.udn}::{target}" if target != self.udn else self.udn
            msg = (
                "NOTIFY * HTTP/1.1\r\n"
                f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                f"CACHE-CONTROL: max-age={ALIVE_INTERVAL_S * 2}\r\n"
                f"LOCATION: {self._base()}/desc.xml\r\n"
                f"NT: {target}\r\n"
                f"NTS: {subtype}\r\n"
                f"USN: {usn}\r\n"
                "SERVER: Linux/5 UPnP/1.0 TuneThatHue/1\r\n\r\n"
            )
            self._ssdp.sendto(msg.encode(), (SSDP_ADDR, SSDP_PORT))

    def answer_search(self, target: str, addr: tuple[str, int]) -> None:
        """Reply to an M-SEARCH that asked for something we are."""
        if self._ssdp is None:
            return
        st = "urn:schemas-upnp-org:device:MediaRenderer:1" if target == "ssdp:all" else target
        usn = f"{self.udn}::{st}"
        msg = (
            "HTTP/1.1 200 OK\r\n"
            f"CACHE-CONTROL: max-age={ALIVE_INTERVAL_S * 2}\r\n"
            "EXT:\r\n"
            f"LOCATION: {self._base()}/desc.xml\r\n"
            "SERVER: Linux/5 UPnP/1.0 TuneThatHue/1\r\n"
            f"ST: {st}\r\n"
            f"USN: {usn}\r\n\r\n"
        )
        self._ssdp.sendto(msg.encode(), addr)

    def _base(self) -> str:
        return f"http://{self._ip}:{self.port}"

    # -- the documents a control point fetches first -----------------------------------

    async def _desc(self, _req: web.Request) -> web.Response:
        xml = f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType>
    <friendlyName>{self.name}</friendlyName>
    <manufacturer>devspark.pl</manufacturer>
    <manufacturerURL>https://devspark.pl</manufacturerURL>
    <modelDescription>Hue lighting renderer</modelDescription>
    <modelName>TuneThatHue</modelName>
    <modelNumber>1</modelNumber>
    <UDN>{self.udn}</UDN>
    <serviceList>
      <service>
        <serviceType>{_SOAP_NS}</serviceType>
        <serviceId>urn:upnp-org:serviceId:AVTransport</serviceId>
        <SCPDURL>/AVTransport.xml</SCPDURL>
        <controlURL>/control/AVTransport</controlURL>
        <eventSubURL>/event/AVTransport</eventSubURL>
      </service>
      <service>
        <serviceType>{_RENDER_NS}</serviceType>
        <serviceId>urn:upnp-org:serviceId:RenderingControl</serviceId>
        <SCPDURL>/RenderingControl.xml</SCPDURL>
        <controlURL>/control/RenderingControl</controlURL>
        <eventSubURL>/event/RenderingControl</eventSubURL>
      </service>
    </serviceList>
  </device>
</root>
"""
        return web.Response(text=xml, content_type="text/xml")

    async def _scpd_avtransport(self, _req: web.Request) -> web.Response:
        return web.Response(text=_scpd(_AVTRANSPORT_ACTIONS), content_type="text/xml")

    async def _scpd_rendering(self, _req: web.Request) -> web.Response:
        return web.Response(text=_scpd(_RENDERING_ACTIONS), content_type="text/xml")

    # -- events -------------------------------------------------------------------------

    async def _subscribe(self, req: web.Request) -> web.Response:
        """
        Accept a subscription without ever sending an event.

        We have no state a control point needs; refusing outright makes some of them
        treat the device as broken and never call Play.
        """
        return web.Response(
            headers={"SID": f"uuid:{uuid.uuid4()}", "TIMEOUT": "Second-1800"},
            text="",
        )

    async def _unsubscribe(self, _req: web.Request) -> web.Response:
        return web.Response(text="")

    # -- the actions --------------------------------------------------------------------

    async def _control_avtransport(self, req: web.Request) -> web.Response:
        action, body = await _soap_action(req)
        if action == "SetAVTransportURI":
            uri = _tag(body, "CurrentURI")
            self._set(uri=uri, controller=req.remote or "", error="")
            print(f"[dlna] handed {uri}")
            return _soap_reply(action, _SOAP_NS)
        if action == "Play":
            await self._start_stream()
            return _soap_reply(action, _SOAP_NS)
        if action in ("Stop", "Pause"):
            await self._stop_stream()
            return _soap_reply(action, _SOAP_NS)
        if action == "GetTransportInfo":
            state = "PLAYING" if self.playing else "STOPPED"
            return _soap_reply(
                action,
                _SOAP_NS,
                {
                    "CurrentTransportState": state,
                    "CurrentTransportStatus": "OK",
                    "CurrentSpeed": "1",
                },
            )
        if action == "GetPositionInfo":
            return _soap_reply(
                action,
                _SOAP_NS,
                {
                    "Track": "1",
                    "TrackDuration": "0:00:00",
                    "TrackMetaData": "",
                    "TrackURI": self.uri,
                    "RelTime": "0:00:00",
                    "AbsTime": "0:00:00",
                    "RelCount": "0",
                    "AbsCount": "0",
                },
            )
        if action == "GetMediaInfo":
            return _soap_reply(
                action,
                _SOAP_NS,
                {
                    "NrTracks": "1",
                    "MediaDuration": "0:00:00",
                    "CurrentURI": self.uri,
                    "CurrentURIMetaData": "",
                    "PlayMedium": "NETWORK",
                    "RecordMedium": "NOT_IMPLEMENTED",
                    "WriteStatus": "NOT_IMPLEMENTED",
                },
            )
        return _soap_reply(action, _SOAP_NS)

    async def _control_rendering(self, req: web.Request) -> web.Response:
        action, _body = await _soap_action(req)
        if action == "GetVolume":
            return _soap_reply(action, _RENDER_NS, {"CurrentVolume": "100"})
        if action == "GetMute":
            return _soap_reply(action, _RENDER_NS, {"CurrentMute": "0"})
        # SetVolume and SetMute are accepted and ignored: the lights have their own
        # brightness, and dimming them from a phone's volume slider would be a surprise.
        return _soap_reply(action, _RENDER_NS)

    # -- playing ------------------------------------------------------------------------

    async def _start_stream(self) -> None:
        if not self.uri:
            return
        await self._stop_stream()
        self._stream_task = asyncio.create_task(self._stream(self.uri))

    async def _stream(self, uri: str) -> None:
        def note(rate: int, channels: int, bits: int) -> None:
            self._set(format=f"{rate} Hz / {channels}ch / {bits}-bit", error="")
            print(f"[dlna] stream: wav {rate} Hz / {channels}ch / {bits}-bit")

        def deliver(pcm: bytes, rate: int, channels: int) -> None:
            self.bytes_in += len(pcm)
            self._on_chunk(pcm, rate, channels)

        try:
            async with aiohttp.ClientSession() as session, session.get(uri) as resp:
                resp.raise_for_status()
                self._set(playing=True)
                await feed_wav(resp.content.read, deliver, on_format=note)
        except asyncio.CancelledError:
            raise
        except NotWav as err:
            self._set(error=f"{err} - set the output codec to wav")
            print(f"[dlna] {err}; set the sending player's output codec to wav")
        except Exception as err:  # noqa: BLE001 - the sender closing is normal
            self._set(error=str(err))
        finally:
            self._set(playing=False, format="")

    async def _stop_stream(self) -> None:
        task, self._stream_task = self._stream_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._set(playing=False)

    # -- housekeeping --------------------------------------------------------------------

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
            "enabled": self.enabled,
            "playing": self.playing,
            "name": self.name,
            "url": f"{self._base()}/desc.xml",
            "uri": self.uri,
            "controller": self.controller,
            "format": self.format,
            "bytes": self.bytes_in,
            "error": self.error,
        }


class _Ssdp(asyncio.DatagramProtocol):
    """Answers M-SEARCH. Everything else on the multicast group is other people's."""

    def __init__(self, renderer: DlnaRenderer) -> None:
        self._renderer = renderer

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            text = data.decode(errors="replace")
        except Exception:  # noqa: BLE001
            return
        if not text.startswith("M-SEARCH"):
            return
        target = ""
        for line in text.splitlines():
            if line.upper().startswith("ST:"):
                target = line.split(":", 1)[1].strip()
                break
        if target in _TARGETS:
            self._renderer.answer_search(target, addr)


# -- SOAP, kept to the two shapes we need ------------------------------------------------


async def _soap_action(req: web.Request) -> tuple[str, str]:
    """Return the action name and the raw body. The action is in the SOAPACTION header."""
    body = await req.text()
    header = req.headers.get("SOAPACTION", "").strip('"')
    action = header.rsplit("#", 1)[-1] if "#" in header else ""
    return action, body


def _tag(xml: str, name: str) -> str:
    """Pull one element's text out. XML from control points is small and predictable."""
    start = xml.find(f"<{name}>")
    if start < 0:
        return ""
    start += len(name) + 2
    end = xml.find(f"</{name}>", start)
    text = xml[start:end] if end > 0 else ""
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").strip()


def _soap_reply(action: str, namespace: str, values: dict[str, str] | None = None) -> web.Response:
    inner = "".join(f"<{k}>{_esc(v)}</{k}>" for k, v in (values or {}).items())
    xml = (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action}Response xmlns:u="{namespace}">{inner}</u:{action}Response>'
        "</s:Body></s:Envelope>"
    )
    return web.Response(text=xml, content_type="text/xml")


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_AVTRANSPORT_ACTIONS = [
    ("SetAVTransportURI", [("InstanceID", "in"), ("CurrentURI", "in"),
                           ("CurrentURIMetaData", "in")]),
    ("Play", [("InstanceID", "in"), ("Speed", "in")]),
    ("Stop", [("InstanceID", "in")]),
    ("Pause", [("InstanceID", "in")]),
    ("GetTransportInfo", [("InstanceID", "in"), ("CurrentTransportState", "out"),
                          ("CurrentTransportStatus", "out"), ("CurrentSpeed", "out")]),
    ("GetPositionInfo", [("InstanceID", "in"), ("Track", "out"), ("TrackDuration", "out"),
                         ("TrackMetaData", "out"), ("TrackURI", "out"), ("RelTime", "out"),
                         ("AbsTime", "out"), ("RelCount", "out"), ("AbsCount", "out")]),
    ("GetMediaInfo", [("InstanceID", "in"), ("NrTracks", "out"), ("MediaDuration", "out"),
                      ("CurrentURI", "out"), ("CurrentURIMetaData", "out"),
                      ("PlayMedium", "out"), ("RecordMedium", "out"), ("WriteStatus", "out")]),
]

_RENDERING_ACTIONS = [
    ("GetVolume", [("InstanceID", "in"), ("Channel", "in"), ("CurrentVolume", "out")]),
    ("SetVolume", [("InstanceID", "in"), ("Channel", "in"), ("DesiredVolume", "in")]),
    ("GetMute", [("InstanceID", "in"), ("Channel", "in"), ("CurrentMute", "out")]),
    ("SetMute", [("InstanceID", "in"), ("Channel", "in"), ("DesiredMute", "in")]),
]

# Every argument needs a state variable, so give each its own rather than modelling the
# real UPnP table: a control point only checks that the names line up.
_VAR_TYPES = {
    "InstanceID": "ui4",
    "CurrentMute": "boolean",
    "DesiredMute": "boolean",
    "CurrentVolume": "ui2",
    "DesiredVolume": "ui2",
    "Track": "ui4",
    "NrTracks": "ui4",
    "RelCount": "i4",
    "AbsCount": "i4",
}


def _scpd(actions: list[tuple[str, list[tuple[str, str]]]]) -> str:
    names: dict[str, str] = {}
    action_xml = []
    for action, args in actions:
        arg_xml = []
        for arg, direction in args:
            names[arg] = _VAR_TYPES.get(arg, "string")
            arg_xml.append(
                f"<argument><name>{arg}</name><direction>{direction}</direction>"
                f"<relatedStateVariable>A_ARG_{arg}</relatedStateVariable></argument>"
            )
        action_xml.append(
            f"<action><name>{action}</name><argumentList>{''.join(arg_xml)}"
            "</argumentList></action>"
        )
    var_xml = "".join(
        f'<stateVariable sendEvents="no"><name>A_ARG_{name}</name>'
        f"<dataType>{dtype}</dataType></stateVariable>"
        for name, dtype in names.items()
    )
    return (
        '<?xml version="1.0"?>'
        '<scpd xmlns="urn:schemas-upnp-org:service-1-0">'
        "<specVersion><major>1</major><minor>0</minor></specVersion>"
        f"<actionList>{''.join(action_xml)}</actionList>"
        f"<serviceStateTable>{var_xml}</serviceStateTable></scpd>"
    )


def _local_ip() -> str:
    """The address a control point can reach us on, not 127.0.0.1."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return str(sock.getsockname()[0])
    except Exception:  # noqa: BLE001
        return "127.0.0.1"
    finally:
        sock.close()
