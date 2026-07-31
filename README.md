# TuneThatHue

**A DJ-style VFX daemon for Philips Hue.** Runs on a QNAP NAS, a PC, a Mac or a Pi.

Play music anywhere in the house. The box listens, finds the beat, and drives your Hue
lights like a small lighting desk: strobe on the drop, lights joining one by one through
a build-up, colour moving with the music.

No subscriptions. No cloud. Nothing extra to switch on. You need smart Hue bulbs, a
machine you already own, and fire.

**[Download from forum.qnap.net.pl](https://forum.qnap.net.pl/download/tunethathue.1113/)**

[![Watch TuneThatHue run](docs/img/video.jpg)](https://www.youtube.com/watch?v=w_EbUPbOirw)

![The light wall — what the bridge is actually being sent, live](docs/img/wall.webp)

## What it does

- Reads the tempo and keeps a beat grid, so effects land on the beat, not near it
- Counts bars and follows the phrasing
- Detects a build-up: lights join one at a time, then all fire on the drop
- Beat-locked strobe with its own rate, duty, colour and blackout
- 155 colour palettes, or colours taken from the music itself
- A live light wall showing the exact frames sent to the bridge

## How the music gets in

Five ways. Turn on as many as you like: one drives the lights at a time, and another
takes over a couple of seconds after it goes quiet.

| | How it finds you | What it needs |
| --- | --- | --- |
| **Sender** | You point it at the box | A Winamp, foobar2000 or AIMP plug-in, or Sound Capture |
| **Sendspin** | Music Assistant discovers the box | Nothing. It appears as a player |
| **Snapcast** | You give it the server address | A snapserver on the network |
| **Squeezebox** | The box finds the server by broadcast | Any Logitech Media Server or Music Assistant |
| **DLNA** | Anything looking for a speaker finds it | A phone, a hi-fi app, Music Assistant |

### What each one can play

The decoder ships inside the package, so this is what works out of the box with no
settings to change.

| Codec | Sender | Sendspin | Snapcast | Squeezebox | DLNA |
| --- | :-: | :-: | :-: | :-: | :-: |
| PCM / WAV | yes | — | yes | yes | yes |
| FLAC | — | — | yes | yes | yes |
| MP3 | — | — | — | yes | yes |
| AAC (ADTS) | — | — | — | yes | yes |
| AAC / ALAC in MP4 | — | — | — | no | yes |
| Ogg Vorbis | — | — | yes | yes | yes |
| Opus | — | — | no | yes | yes |
| WMA | — | — | — | yes | yes |
| AC3 | — | — | — | yes | yes |

Sendspin has no column because it sends no audio: Music Assistant analyses the sound and
sends the box the result. A dash means the protocol never carries that format.

### The two limits, and why

**MP4 over Squeezebox: no.** An MP4 keeps its index at the end of the file, so it cannot
be read from a one-way stream by anything. Over DLNA it works, because there the box
fetches the URL itself and can seek.

**Opus over Snapcast: no.** Snapcast sends raw Opus packets with no container. Its FLAC
chunks join into a valid FLAC stream and its Ogg chunks carry their own pages, but raw
Opus would have to be re-framed first. Use flac, ogg or pcm.

Both are reported in the panel in those words, so nobody has to guess.

## Where it runs

| | |
| --- | --- |
| **QNAP NAS** | QuTS hero 6.0 or QTS 6.0, any platform |
| **Windows** | 10 or 11, installs as a service with a tray icon |
| **macOS** | 12 or newer, launchd agent with a menu-bar icon |
| **Linux** | Ubuntu 24.04, Debian, Raspberry Pi OS: systemd user service |
| **Lights** | A Hue bridge with an Entertainment area |

## Install

**On a NAS.** Download the `.qpkg`, install it in App Center through *Install Manually*,
open **TuneThatHue** from the main menu, pair the bridge, pick an area, turn the output
on.

**On a desktop.** Run the installer for your system. It sets up the service, the tray
icon and the decoder in one go.

    Windows   TuneThatHue-daemon-setup.exe
    macOS     ./install/macos/install.sh
    Linux     ./install/linux/install.sh

Then `tunethathue status` from a terminal, or the tray icon, or
`http://127.0.0.1:8080`.

![Settings, generated from the Music Assistant provider's own sources](docs/img/settings.webp)

## The senders

![The Winamp plug-in sending to the NAS](docs/img/winamp.webp)

- **Sound Capture** — sends whatever is playing on the computer. Any player. Can also
  capture one chosen app, a second sound card, or Stereo Mix
- **Winamp** — a DSP plug-in, straight from the player
- **foobar2000** — the same, for foobar 2.x
- **AIMP** — loads the Winamp plug-in: copy `dsp_tunethathue.dll` into AIMP's `Plugins`
  folder. 32-bit AIMP only; on 64-bit use Sound Capture

All of them send the same thing to the box on UDP 6980.

## About the effects

The effects engine is the one from **Music Assistant**'s `hue_entertainment` provider,
copied in byte for byte. TuneThatHue adds what a standalone box needs (beat tracking, a
panel, pairing, packaging) and leaves the effects alone, so an effect tuned here behaves
the same in Music Assistant and the other way round.

That makes it useful in both directions: **it is a workbench for building and debugging
VFX effects for [Music Assistant](https://github.com/music-assistant/server)**. Restart
takes a second, the lights are in the room, and the file copies straight back.

![Advanced settings and latency calibration](docs/img/panel.webp)

## Timing

Light travels faster than sound. Open the panel **on the machine that captures the
audio**, start the metronome, and raise the light delay until the flash lands on the
click.

## Building it yourself

- `.qpkg`: [`qnap/README-BUILD.md`](qnap/README-BUILD.md)
- The bundled decoder: [`qnap/build_ffmpeg.sh`](qnap/build_ffmpeg.sh)
- Windows senders and daemon installer: `build-windows.ps1`,
  `installers/prepare-windows.ps1`

## Credits and licence

Apache-2.0. See [`NOTICE`](NOTICE) and
[`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md). The package carries an LGPL build of
FFmpeg for decoding.

Uses the Philips Hue Entertainment API. Not affiliated with, endorsed by, or sponsored
by Signify / Philips Hue, Native Instruments, Winamp, foobar2000 or AIMP.

---

TuneThatHue © 2025–2026 Silas Mariusz Grzybacz · [devspark.pl](https://devspark.pl)
published: [forum.qnap.net.pl](https://forum.qnap.net.pl/download/tunethathue.1113/) ·
qnap app repo: [myqnap.org](https://www.myqnap.org)
