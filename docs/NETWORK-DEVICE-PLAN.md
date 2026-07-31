# Making the daemon a network device

Today TuneThatHue only listens for VBAN on UDP 6980. Something has to be pointed at it.
The goal of this branch is the opposite: **the box announces itself, and whatever is
playing music finds it** — so it can be dropped into a speaker group and light the room
without anyone configuring an IP.

Four stages, in the order they should be built. Each one is independent: the daemon can
run any combination of inputs at once, and the panel picks which are enabled.

## Where the audio comes from, and who does the work

Two shapes of input, and the difference decides everything:

    features in     a server has already analysed the audio and sends us spectrum,
                    peaks and beats. We only paint. Effects behave EXACTLY as they do
                    in Music Assistant, because it is the same extractor.

    audio in        we receive PCM and analyse it ourselves with the extractor we
                    already carry plus python/beat_tracker.py. Works with anything,
                    but the beat grid is ours, not the server's.

Stage 1 is "features in". Stages 2-4 are "audio in" and share one internal path — the
same one VBAN already uses — so each new protocol is a transport, not a new engine.

## 1. Sendspin device

**What it is.** The daemon advertises itself over mDNS as a Sendspin client. A Sendspin
server (Music Assistant) discovers it and opens the WebSocket *towards us*.

**Verified before writing any of this** (2026-07-31, probe against a live MA server):

- `aiosendspin.client.listener.ClientListener` already does the advertising:
  `_sendspin._tcp.local.`, port **8928**, TXT `path=/sendspin` and `name`.
- A Music Assistant server on the network found it and connected **within seconds**.
- The handshake with `roles=[VISUALIZER, COLOR]` came back with
  `active_roles=['visualizer@v1','color@v1']`, `connection_reason=DISCOVERY` — **both
  roles granted, no pairing or PIN**, and a colour message arrived immediately.
- Our pinned `aiosendspin` talks to the newer one Music Assistant ships. Roles are
  versioned (`@v1`), which is what makes that safe.
- The runtime already in the .qpkg has aiosendspin, zeroconf and aiohttp. **No new
  dependency.**

**What Music Assistant does with us.** It builds a player of type `VISUALIZER`, hidden by
default, carrying `SET_MEMBERS` — so other Sendspin clients can be grouped with us and we
get the group's audio features.

**Known limit.** That grouping call only accepts Sendspin players. A Sonos or a Chromecast
cannot be put in the same group. Cross-vendor grouping would have to go through Music
Assistant's universal group, which is untested for a visualizer member.

**Work:** run `ClientListener` in `tth_phase2.py`, hand each accepted socket to a
`SendspinClient`, feed the visualizer frames into the existing analyzer at the point where
VBAN frames enter it, and honour the colour role. Panel: a source switch and the connected
server's name.

## 2. Snapcast client

**Why second.** Snapserver can serve the `pcm` codec, so there is nothing to decode — the
bytes that arrive are the bytes the analyzer wants. It is also the multiroom system most
likely to already be running next to a NAS, and it is not tied to Music Assistant.

**Shape.** Snapclient dials the server on TCP 1704 and speaks a small binary protocol
(a length-prefixed header plus JSON `Hello`, then `CodecHeader`, `WireChunk`, `ServerSettings`
and `Time` messages). The server groups clients itself, so joining a group is done on the
server side and needs nothing from us beyond being connected.

**Settled by reading Music Assistant's provider:** the transport codec is a server-wide
setting offering `flac` (default), `ogg`, `opus` and **`pcm`**, and the client does not get
to choose. So this stage works with `pcm` selected, and a FLAC decoder is what it would
take to work on the default. Real snapclients handle `pcm` fine, so switching costs the
rest of the house nothing.

**Wire format** (from Snapcast's `binary_protocol.md`): every message is a 26-byte base
header - `type` u16, `id` u16, `refersTo` u16, `sent` sec/usec i32, `received` sec/usec
i32, `size` u32 - followed by the payload. Types: 1 `CodecHeader`, 2 `WireChunk`,
3 `ServerSettings`, 4 `Time`, 5 `Hello`, 7 `ClientInfo`, 8 `Error`. `Hello` is JSON with
`ClientName`, `HostName`, `ID`, `MAC`, `Version`, `OS`, `Arch`, `Instance` and
`SnapStreamProtocolVersion`. `WireChunk` is a timestamp plus raw payload - with the `pcm`
codec that payload is the samples themselves.

## 3. Squeezelite / slimproto player

**Why.** The largest installed base of all of these — every Logitech Media Server and
Music Assistant's own Squeezelite provider. Discovery is a UDP broadcast on **3483**, which
the server answers, so the box finds the server rather than the other way round.

**Settled by reading `aioslimproto`, the library Music Assistant runs:**

- *Discovery.* The player broadcasts to UDP 3483 a datagram starting with `e` followed by
  TLVs - 4-byte tag, 1-byte length, value - asking for `NAME`, `IPAD`, `JSON`, `VERS`,
  `UUID`. The server answers with `E` and the same tags filled in, which hands us its
  address and JSON-RPC port. (A legacy `d` form exists and gets a fixed `D` reply; not
  worth implementing.)
- *Framing.* Over TCP 3483 every frame is a 4-byte operation name, a big-endian u32
  length, then the payload.
- *Hello.* `HELO` carries a device-id byte, a revision byte, a 6-byte MAC and then the
  capability string. The server answers `vers`, asks for the player name with `setd`, and
  then drives playback with `strm`.
- *Format.* Music Assistant's shared output-codec setting offers `flac`, `mp3`, `aac` and
  **`wav`** - so, as with Snapcast, pick `wav` and there is nothing to decode.

Unlike Snapcast the player is expected to report buffer state back (`STAT`), so this stage
carries the most protocol surface of the four.

## 4. DLNA / UPnP renderer

**Why last.** Discovery (SSDP) is trivial and the control protocol is plain SOAP, but the
renderer is handed a URL and is expected to fetch and play it, so the format is whatever
the server offers. The same `wav` output-codec setting applies here, so it can be decoder
free too - but a renderer is a device other things on the network will try to talk to, and
answering badly is worse than not answering, so it goes last.

**Shape.** Answer M-SEARCH for `urn:schemas-upnp-org:device:MediaRenderer:1`, serve a
device description, implement `AVTransport` (SetAVTransportURI / Play / Stop) and a
`RenderingControl` stub, then fetch the URI.

## Not doing: Chromecast

A Cast receiver is not something a device can simply pretend to be — the receiver
application has to be registered with Google and served from their infrastructure, and the
transport is protobuf over TLS with attestation. It is out of reach and should not be
promised.

## Testing

Every stage is checked on a real NAS running the packaged build, with real lights, not on
a laptop:

1. the box appears in whatever is looking for it, without being configured,
2. audio played to it moves the light wall,
3. the daemon survives the source going away and coming back,
4. the panel reports which source is live.
