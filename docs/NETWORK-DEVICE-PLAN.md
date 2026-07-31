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

**Open question to settle before building:** whether the server has to be told to use the
`pcm` codec, or whether a client may ask. If it cannot, this stage needs a FLAC decoder and
drops down the list.

## 3. Squeezelite / slimproto player

**Why.** The largest installed base of all of these — every Logitech Media Server and
Music Assistant's own Squeezelite provider. Discovery is a UDP broadcast on **3483**, which
the server answers, so the box finds the server rather than the other way round.

**Shape.** The player announces itself with `HELO`, then obeys `strm` commands: the server
tells it what to fetch, the player pulls the stream over HTTP and plays it. Music Assistant
can be asked for PCM/WAV, so again no decoder — but the format negotiation has to be got
right, and unlike Snapcast the player is expected to report buffer state back.

## 4. DLNA / UPnP renderer

**Why last.** Discovery (SSDP) is trivial and the control protocol is plain SOAP, but the
renderer is handed a URL and is expected to fetch and play it, so the format is whatever
the server offers. That means a decoder unless the source can be pinned to WAV.

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
